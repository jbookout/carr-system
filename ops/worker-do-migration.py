#!/usr/bin/env python3
"""worker-do-migration.py — is a Durable Object migration pending on the Worker?

WHY THIS EXISTS. Production ships through `wrangler versions upload` followed by
an exact `wrangler versions deploy <id>@100` (bin/deploy-worker.sh). Wrangler
4.137 refuses `versions upload` outright while the Worker has a Durable Object
migration it has not applied, and Cloudflare's own documentation says a Durable
Object lifecycle change "can only be applied via `wrangler deploy`". So the first
release whose wrangler.toml adds a `[[migrations]]` tag would stop the
unattended release pipeline at its upload step, with nothing a retry could fix.

bin/deploy-worker.sh --upload-version asks this file ONE question before it
uploads: which migration tag has the deployed Worker applied, and is the newest
tag declared in wrangler.toml beyond it. When it is, the wrapper applies it the
documented way and then continues with the ordinary upload.

THE APPLIED TAG IS READ, NEVER INFERRED. It comes from the same Cloudflare
metadata wrangler itself reads to decide what to send
(`GET /accounts/<account>/workers/services/<script>`, field
`default_environment.script.migration_tag`; see getMigrationsToUpload in
wrangler-dist/cli.js). This file does not fetch it — the wrapper does, with the
token `wrangler auth token` resolves — it only judges the response. The pending
decision then mirrors wrangler's own: no applied tag means every declared
migration is pending; an applied tag that is the newest declared one means none
is; an applied tag earlier in the list means the ones after it are.

FAIL CLOSED, exit 3, whenever the applied tag cannot be determined: a response
that is not JSON, not `success: true`, has no script object, names a different
script, or carries a migration_tag that is not a non-empty string. An applied tag
that wrangler.toml does NOT declare is also exit 3: wrangler would warn and
re-apply the whole list, and a release pipeline must not guess which of those
two histories is true. `migration_tag` absent or null on an otherwise exact
script object is the documented "none applied" state (the Cloudflare API marks
the field optional), which is also exactly how wrangler reads it.

Subcommands (all output is JSON on stdout; nothing here reads a credential):
  target  --config <wrangler.toml> --env <production|name>
          account id, script name and declared tags for that environment
  plan    --config <wrangler.toml> --env <...> --services-json <file>
          the pending decision against a fetched services response
  receipt --file <receipt.json> --sha <40-hex>
          validate a migration receipt this wrapper wrote and print it
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

UNKNOWN = 3
USAGE = 2
RECEIPT_SCHEMA = "carr-worker-do-migration-receipt.v1"
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Undetermined(Exception):
    """The applied tag cannot be established; the caller must fail closed."""


class ConfigError(Exception):
    """wrangler.toml does not describe a usable target."""


def load_target(config: Path, env: str) -> dict:
    try:
        doc = tomllib.loads(config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {config}: {exc}") from exc
    account = doc.get("account_id")
    if env == "production":
        section = doc
    else:
        section = (doc.get("env") or {}).get(env)
        if not isinstance(section, dict):
            raise ConfigError(f"{config} has no [env.{env}] section")
        account = section.get("account_id", account)
    script = section.get("name")
    # migrations are inheritable in wrangler: an env without its own list uses
    # the top-level one, which is what PR #1244's staging comment relies on.
    migrations = section.get("migrations", doc.get("migrations", []))
    if not isinstance(account, str) or not re.fullmatch(r"[0-9a-f]{32}", account):
        raise ConfigError("account_id is not an exact 32-hex account id")
    if not isinstance(script, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", script):
        raise ConfigError("the target script name is not an exact Worker name")
    if not isinstance(migrations, list):
        raise ConfigError("[[migrations]] is not a list")
    tags: list[str] = []
    for entry in migrations:
        tag = entry.get("tag") if isinstance(entry, dict) else None
        if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
            raise ConfigError(f"a [[migrations]] entry has no usable tag: {entry!r}")
        if tag in tags:
            raise ConfigError(f"migration tag {tag!r} is declared twice")
        tags.append(tag)
    exports = section.get("exports", doc.get("exports"))
    if exports:
        # wrangler sends declarative exports INSTEAD of migrations when present;
        # this file only understands the migrations list, so it refuses rather
        # than answering a question wrangler is not asking.
        raise ConfigError("declarative `exports` is configured; this check covers [[migrations]] only")
    return {"account_id": account, "script": script, "declared_tags": tags}


def applied_tag(services: object, script: str) -> str | None:
    if not isinstance(services, dict) or services.get("success") is not True:
        raise Undetermined("the services response is not a successful Cloudflare API envelope")
    result = services.get("result")
    if not isinstance(result, dict):
        raise Undetermined("the services response has no result object")
    default_env = result.get("default_environment")
    if not isinstance(default_env, dict):
        raise Undetermined("the services response has no default_environment")
    script_obj = default_env.get("script")
    if not isinstance(script_obj, dict):
        raise Undetermined("the services response has no default_environment.script object")
    if script_obj.get("id") != script:
        raise Undetermined(
            f"the services response describes script {script_obj.get('id')!r}, not {script!r}")
    if "migration_tag" not in script_obj or script_obj["migration_tag"] is None:
        return None
    tag = script_obj["migration_tag"]
    if not isinstance(tag, str) or not tag:
        raise Undetermined(f"migration_tag is present but not a non-empty string: {tag!r}")
    return tag


def plan(target: dict, services: object) -> dict:
    declared = target["declared_tags"]
    if not declared:
        return {"script": target["script"], "declared_tags": [], "latest_tag": None,
                "applied_tag": None, "applied_known": False, "pending": False,
                "pending_tags": []}
    applied = applied_tag(services, target["script"])
    if applied is None:
        pending_tags = list(declared)
    elif applied in declared:
        pending_tags = declared[declared.index(applied) + 1:]
    else:
        raise Undetermined(
            f"the Worker reports applied tag {applied!r}, which wrangler.toml does not declare; "
            "wrangler would re-apply every migration, and which history is true is not guessable")
    return {"script": target["script"], "declared_tags": declared, "latest_tag": declared[-1],
            "applied_tag": applied, "applied_known": True, "pending": bool(pending_tags),
            "pending_tags": pending_tags}


def read_receipt(path: Path, sha: str) -> dict:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read migration receipt {path}: {exc}") from exc
    required = {"schema", "git_sha", "script", "old_tag", "new_tag", "migration_version_id",
                "state", "applied_at"}
    if not isinstance(receipt, dict) or not required <= set(receipt):
        raise ConfigError("migration receipt is missing required fields")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["git_sha"] != sha:
        raise ConfigError("migration receipt is for another schema or SHA")
    if not isinstance(receipt["new_tag"], str) or not TAG_RE.fullmatch(receipt["new_tag"]):
        raise ConfigError("migration receipt has no exact new tag")
    version = receipt["migration_version_id"]
    if version is not None and not (isinstance(version, str) and UUID_RE.fullmatch(version)):
        raise ConfigError("migration receipt has a malformed provider version id")
    if receipt["state"] not in ("applied_verified", "applied_unverified", "not_applied", "unknown"):
        raise ConfigError("migration receipt has an unknown state")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("target", "plan"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True, type=Path)
        p.add_argument("--env", required=True)
        if name == "plan":
            p.add_argument("--services-json", required=True, type=Path)
    r = sub.add_parser("receipt")
    r.add_argument("--file", required=True, type=Path)
    r.add_argument("--sha", required=True)
    args = parser.parse_args(argv)

    try:
        if args.cmd == "receipt":
            print(json.dumps(read_receipt(args.file, args.sha), sort_keys=True))
            return 0
        target = load_target(args.config, args.env)
        if args.cmd == "target":
            print(json.dumps(target, sort_keys=True))
            return 0
        services: object = None
        if target["declared_tags"]:
            try:
                services = json.loads(args.services_json.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Undetermined(f"the services response is not readable JSON: {exc}") from exc
        print(json.dumps(plan(target, services), sort_keys=True))
        return 0
    except Undetermined as exc:
        print(f"worker-do-migration: APPLIED TAG UNKNOWN: {exc}", file=sys.stderr)
        return UNKNOWN
    except ConfigError as exc:
        print(f"worker-do-migration: {exc}", file=sys.stderr)
        return USAGE


if __name__ == "__main__":
    raise SystemExit(main())
