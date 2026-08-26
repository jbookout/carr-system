#!/usr/bin/env python3
"""
release-manifest.py — build and verify the P0-1 release manifest.

THE ACCEPTANCE CLAUSE THIS FILE OWNS: "identical artifact rebuild from recorded
SHA". The other two clauses live in the database (migration 0131) and in
ops/ci.sh; this is the half that answers "is what shipped what we think
shipped", months later, from nothing but the recorded SHA.

WHY A CONTENT DIGEST AND NOT A BUILD OUTPUT. The Worker is not compiled into a
tarball anybody keeps — wrangler ships from source. So the artifact IS the
deployed source tree, and the honest digest of it is content-addressed: every
file under the deployed paths, at that SHA, as (path, blob-id) pairs, hashed in
sorted order. That has three properties a build-output hash would not have
here. It is reproducible by anyone with the repo and the SHA, on any machine,
years later. It cannot be affected by build-time nondeterminism, because there
is no build. And it is computed from git's own object store rather than the
working tree, so a dirty checkout cannot forge it — the digest of a SHA is the
same whether or not someone has unsaved edits open.

WHAT IT DELIBERATELY DOES NOT DO. It does not write to the database; that is
tools/ops-record.py's job (rule a8c55a47 keeps one writer). It does not decide
whether a release may ship. It computes evidence and compares evidence.

  build     Compute the manifest for a SHA and print it as JSON.
  verify    Recompute from a manifest's own recorded SHA and diff. Exit 1 on
            any mismatch. THIS IS THE ACCEPTANCE TEST for the rebuild clause.
  bind-provider  Record the immutable provider version returned after upload,
            recompute the approval-plan hash, and print the updated manifest.
  staging-forward-fix-prefix  Build the staging-only, bounded source/prefix
            contract for a migration-bearing forward-fix rehearsal.
  verify-staging-forward-fix-prefix  Rebuild that contract from immutable git.
  program6-posture  Print the exact reviewed Program 6 posture in a manifest.
  plan-hash Hash the fields an approver actually reads, so a changed plan is
            detectable by the database trigger that voids stale approvals.

USAGE
  tools/release-manifest.py build --sha HEAD
  tools/release-manifest.py build --sha HEAD > out/release.json
  tools/release-manifest.py bind-provider --manifest out/release.json \
      --provider cloudflare-workers --provider-version-id <version-id> > out/bound.json
  tools/release-manifest.py verify --manifest out/release.json
  tools/release-manifest.py plan-hash --manifest out/release.json
"""

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tomllib
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The deployed surface, from bin/deploy-worker.sh, which is the ONE sanctioned
# way this ships. If that script's scope ever changes, this list changes with it
# in the same commit — a manifest that digests the wrong paths is worse than
# none, because it is confidently wrong.
DEPLOYED_PATHS = ("mcp-server", "dealroom")
EXCLUDED_PATH_PARTS = ("node_modules", ".last-deployed-verb-count")

# What the build recipe pins. A rebuild is only "identical" if the inputs were
# pinned too, so the lock digest travels beside the artifact digest.
LOCK_PATHS = ("requirements.txt", "mcp-server/package-lock.json")

# Configuration that changes what runs without changing a line of code. The
# maturity baseline requires the worker/configuration fingerprint on the
# manifest for exactly this reason.
CONFIG_PATHS = ("mcp-server/wrangler.toml",)
CONFIG_GLOBS = ("ops/config/*.json",)

# The fields an approver reads. Changing any of them is a material plan
# revision, and migration 0131's trigger destroys the approval when the hash
# moves. Deploy-time facts (read-backs, verification) are NOT here: they happen
# after the approval and cannot retroactively invalidate it. A provider version
# is different: it is returned by upload before approval, identifies exactly
# what later promotion deploys, and therefore belongs in the approval preimage.
PLAN_FIELDS = (
    "git_sha",
    "artifact_digest",
    "dependency_lock_digest",
    "config_fingerprint",
    "schema_highest_migration",
    "schema_applied_count",
    "schema_ledger_sha256",
    "migration_set",
    "environment",
    "service",
    "provider",
    "provider_version_id",
)
ASSURANCE_PLAN_FIELDS = (
    "performance_budget_ref",
    "performance_budget_ms",
    "recovery_strategy",
    "rollback_ready",
    "rollback_plan_ref",
)

RECOVERY_STRATEGIES = (
    "rollback",
    "forward_fix",
)
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{2,200}$")
MIGRATION_FILENAME_RE = re.compile(r"^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$")
MIGRATION_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SCHEMA_LEDGER_COPY = (
    "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;"
)


def git(*args: str) -> str:
    out = subprocess.run(
        ("git", "-C", str(REPO)) + args,
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        sys.exit(f"release-manifest: git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def git_bytes(*args: str) -> bytes:
    out = subprocess.run(
        ("git", "-C", str(REPO)) + args,
        capture_output=True, check=False)
    if out.returncode != 0:
        detail = out.stderr.decode(errors="replace").strip()
        sys.exit(f"release-manifest: git {' '.join(args)} failed: {detail}")
    return out.stdout


def resolve_sha(ref: str) -> str:
    sha = git("rev-parse", ref).strip()
    if len(sha) != 40:
        sys.exit(f"release-manifest: {ref!r} did not resolve to a full SHA")
    return sha


def tree_entries(sha: str, paths: tuple[str, ...]) -> list[tuple[str, str]]:
    """(path, blob id) for every file under paths at this SHA, sorted.

    Read from the object store with `git ls-tree`, never from the working tree:
    the whole point is that the digest belongs to the commit, not to whatever
    happens to be checked out while another session is mid-edit.
    """
    entries: list[tuple[str, str]] = []
    for path in paths:
        raw = git("ls-tree", "-r", "-z", "--format=%(objectname) %(path)", sha, "--", path)
        for line in raw.split("\0"):
            line = line.strip()
            if not line:
                continue
            blob, _, filepath = line.partition(" ")
            if any(part in filepath for part in EXCLUDED_PATH_PARTS):
                continue
            entries.append((filepath, blob))
    return sorted(entries)


def digest_entries(entries: list[tuple[str, str]]) -> str:
    h = hashlib.sha256()
    for filepath, blob in entries:
        h.update(filepath.encode())
        h.update(b"\0")
        h.update(blob.encode())
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


def digest_files(sha: str, paths: tuple[str, ...]) -> str:
    """Digest of named files' CONTENT at this SHA, missing files named as absent
    rather than skipped — a lock that disappeared must change the digest."""
    h = hashlib.sha256()
    for path in sorted(paths):
        blob = git("rev-parse", f"{sha}:{path}").strip() if path_exists(sha, path) else "ABSENT"
        h.update(path.encode())
        h.update(b"\0")
        h.update(blob.encode())
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


def path_exists(sha: str, path: str) -> bool:
    out = subprocess.run(
        ("git", "-C", str(REPO), "cat-file", "-e", f"{sha}:{path}"),
        capture_output=True, text=True, check=False)
    return out.returncode == 0


def expand_config_paths(sha: str) -> tuple[str, ...]:
    paths = list(CONFIG_PATHS)
    for glob in CONFIG_GLOBS:
        prefix = glob.rsplit("/", 1)[0]
        suffix = glob.rsplit("/", 1)[1].lstrip("*")
        raw = git("ls-tree", "-r", "--name-only", sha, "--", prefix)
        paths.extend(p for p in raw.split("\n") if p.endswith(suffix))
    return tuple(sorted(set(paths)))


def program6_actions_at(sha: str, environment: str) -> dict[str, object]:
    """Read the reviewed Program 6 posture from this manifest's git object."""
    try:
        config = tomllib.loads(git("show", f"{sha}:mcp-server/wrangler.toml"))
    except (tomllib.TOMLDecodeError, SystemExit) as exc:
        raise ValueError("Program 6 Wrangler configuration cannot be parsed") from exc
    if environment == "production":
        variables = config.get("vars")
    else:
        variables = (config.get("env") or {}).get(environment, {}).get("vars")
    if not isinstance(variables, dict):
        raise ValueError(f"Program 6 {environment} configuration has no vars table")
    value = variables.get("DEALROOM_PROGRAM6_ACTIONS_ENABLED")
    if value == "true":
        return {"enabled": True, "posture": "enabled"}
    if value == "false":
        return {"enabled": False, "posture": "disabled"}
    raise ValueError("DEALROOM_PROGRAM6_ACTIONS_ENABLED must be exactly true or false")


def manifest_program6_posture(manifest: dict) -> str:
    """Return the one reviewed posture a serving release must report."""
    value = manifest.get("program6_actions")
    if not isinstance(value, dict):
        raise ValueError("manifest has no Program 6 posture")
    enabled, posture = value.get("enabled"), value.get("posture")
    if enabled is True and posture == "enabled":
        return "enabled"
    if enabled is False and posture == "disabled":
        return "disabled"
    raise ValueError("manifest Program 6 posture is invalid")


def migration_set(sha: str, since: str | None = None) -> tuple[list[str], str]:
    """The migrations this release carries, and the basis that word is using.

    WITH --since, it is the honest answer: the migrations present at this SHA
    and absent at the previous release's SHA, which is what "migration set"
    means on a release manifest. WITHOUT it, there is no previous release to
    diff against, so it is the full set at this SHA and the basis says so.
    A count whose meaning is ambiguous is the thing rule b01edd26 refuses.
    """
    def at(ref: str) -> list[str]:
        raw = git("ls-tree", "-r", "--name-only", ref, "--", "migrations")
        return sorted(
            Path(p).name.split("_", 1)[0]
            for p in raw.split("\n")
            if p.endswith(".sql")
        )

    here = at(sha)
    if not since:
        return here, "full-set-at-sha (no previous release given)"
    before = set(at(resolve_sha(since)))
    return [m for m in here if m not in before], f"added-since {resolve_sha(since)[:12]}"


def applied_schema_ledger(sha: str) -> tuple[int, str, str]:
    """Read the candidate's immutable expected post-rollout ledger.

    ``db/schema.sql`` is generated from the canonical ``schema_migrations``
    ledger.  Its rows must be the exact filename/content prefix of the migration
    tree: the snapshot says what was applied when it was generated, while a
    pending suffix is the normal commit -> CI -> migrate -> refresh workflow.
    The release itself binds the complete tree because that is the exact ledger
    it must observe after rollout.  Applied timestamps are excluded from the
    digest because they are not schema identity.
    """
    snapshot = git("show", f"{sha}:db/schema.sql")
    if snapshot.count(SCHEMA_LEDGER_COPY) != 1:
        sys.exit("release-manifest: db/schema.sql must contain exactly one "
                 "schema_migrations COPY block")
    _, ledger_and_tail = snapshot.split(SCHEMA_LEDGER_COPY, 1)
    rows_text, separator, _ = ledger_and_tail.lstrip("\r\n").partition("\n\\.\n")
    if not separator:
        sys.exit("release-manifest: schema_migrations COPY block is unterminated")

    rows: list[tuple[str, str]] = []
    for line in rows_text.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            sys.exit("release-manifest: malformed schema_migrations COPY row")
        filename, file_sha256, applied_at = fields
        if (not MIGRATION_FILENAME_RE.fullmatch(filename)
                or not MIGRATION_SHA256_RE.fullmatch(file_sha256)
                or not applied_at.strip()):
            sys.exit("release-manifest: invalid schema_migrations COPY row")
        rows.append((filename, file_sha256))
    if not rows:
        sys.exit("release-manifest: applied schema ledger is empty")
    if len({filename for filename, _ in rows}) != len(rows):
        sys.exit("release-manifest: applied schema ledger filenames must be unique")
    rows.sort()

    # The generated snapshot is a release input, not a second migration
    # catalog.  Fail closed unless it is an exact filename/content PREFIX of
    # the complete tree.  A suffix may be pending; a hole, rename, edit, or
    # insertion before an applied row may not.  Compare full filenames
    # (including optional letter suffixes), never numeric prefixes.
    tree_rows = migration_tree_ledger(sha)
    ensure_exact_schema_prefix(rows, tree_rows)
    return schema_ledger_identity(tree_rows)


def migration_tree_ledger(sha: str) -> list[tuple[str, str]]:
    """Return the exact migration filename/content ledger at a SHA."""
    # One archive transfer preserves exact bytes without one subprocess per
    # migration file.
    archive = git_bytes("archive", "--format=tar", sha, "--", "migrations")
    rows = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            prefix = "migrations/"
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            filename = member.name[len(prefix):]
            if not filename.endswith(".sql"):
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                sys.exit("release-manifest: could not read migration from git archive")
            rows.append((filename, hashlib.sha256(extracted.read()).hexdigest()))
    rows.sort()
    if not rows:
        sys.exit("release-manifest: target SHA has no migrations/*.sql files")
    return rows


def ensure_exact_schema_prefix(
        snapshot_rows: list[tuple[str, str]],
        migration_rows: list[tuple[str, str]]) -> None:
    """Require snapshot rows to be the exact applied prefix of the tree."""
    if len(snapshot_rows) <= len(migration_rows) \
            and snapshot_rows == migration_rows[:len(snapshot_rows)]:
        return
    only_snapshot = sorted(set(snapshot_rows) - set(migration_rows))
    only_tree = sorted(set(migration_rows) - set(snapshot_rows))
    detail = []
    if only_snapshot:
        detail.append(f"snapshot-only={only_snapshot[:3]}")
    if only_tree:
        detail.append(f"migrations-only={only_tree[:3]}")
    raise SystemExit(
        "release-manifest: db/schema.sql exact migration ledger is not an "
        "applied filename/content prefix of migrations/*.sql at the target SHA"
        + (" (" + "; ".join(detail) + ")" if detail else ""))


def schema_ledger_identity(
        rows: list[tuple[str, str]]) -> tuple[int, str, str]:
    """Count/highest/digest for the exact expected post-rollout ledger."""
    if not rows:
        raise SystemExit("release-manifest: expected schema ledger is empty")
    material = "".join(f"{filename}\0{file_sha256}\n"
                       for filename, file_sha256 in rows)
    digest = "sha256:" + hashlib.sha256(material.encode()).hexdigest()
    return len(rows), rows[-1][0], digest


def bounded_forward_fix_contract(manifest: dict, through: str,
                                 held_back: list[str],
                                 candidate_provider_version_id: str) -> dict:
    """Build the staging-only source/prefix binding for Program 5 forward-fix.

    A normal release manifest remains the exact *Production* full-tree identity.
    This separate object names a contiguous staging prefix without changing that
    truth.  It is deliberately unusable as a Production deployment manifest.
    """
    if manifest.get("environment") != "production":
        raise ValueError("bounded forward-fix source manifest must be production-shaped")
    source_sha = manifest.get("git_sha")
    artifact_digest = manifest.get("artifact_digest")
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha)
            or not isinstance(artifact_digest, str) or not SHA256_REF_RE.fullmatch(artifact_digest)):
        raise ValueError("bounded forward-fix source manifest identity is invalid")
    try:
        provider_uuid = str(uuid.UUID(candidate_provider_version_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("bounded forward-fix candidate provider version is invalid") from exc
    if provider_uuid != candidate_provider_version_id:
        raise ValueError("bounded forward-fix candidate provider version is not canonical")
    if (manifest.get("provider") != "cloudflare-workers"
            or manifest.get("provider_version_id") != provider_uuid):
        raise ValueError("bounded forward-fix provider version must match the immutable source manifest")

    rows = migration_tree_ledger(source_sha)
    names = [filename for filename, _ in rows]
    if through not in names:
        raise ValueError("bounded forward-fix boundary must be an exact source migration filename")
    boundary = names.index(through)
    prefix_rows = rows[:boundary + 1]
    if [filename for filename, _ in prefix_rows[-2:]] != [
            "0315_program5_forward_fix_rehearsal.sql",
            "0315a_program5_bounded_forward_fix_rehearsal.sql"]:
        raise ValueError("bounded forward-fix boundary must be the exact adjacent 0315/0315a pair")
    selected_rows = prefix_rows[-2:]
    actual_held_back = names[boundary + 1:]
    if held_back != actual_held_back:
        raise ValueError("bounded forward-fix held-back migrations must be the exact ordered source suffix")
    if not selected_rows or not actual_held_back:
        raise ValueError("bounded forward-fix contract requires both selected prefix and held-back suffix")
    prefix_count, prefix_highest, prefix_digest = schema_ledger_identity(prefix_rows)
    full_count, full_highest, full_digest = schema_ledger_identity(rows)
    for key, value in (("schema_applied_count", full_count),
                       ("schema_highest_migration", full_highest),
                       ("schema_ledger_sha256", full_digest)):
        if manifest.get(key) != value:
            raise ValueError("bounded forward-fix source manifest does not bind the exact full migration tree")

    def rows_with_ordinals(items: list[tuple[str, str]], start: int) -> list[dict]:
        return [
            {"ordinal": start + offset, "filename": filename, "sha256": digest}
            for offset, (filename, digest) in enumerate(items)
        ]

    contract = {
        "contract_version": 1,
        "purpose": "program5-forward-fix-staging-prefix",
        "environment": "staging",
        "production_deploy_authorized": False,
        "plan_ref": "runbooks/program5-forward-fix-staging-rebuild.md#bounded-prefix-contract",
        "source": {
            "git_sha": source_sha,
            "artifact_digest": artifact_digest,
            "artifact_file_count": manifest.get("artifact_file_count"),
            "candidate_provider": "cloudflare-workers",
            "candidate_provider_version_id": provider_uuid,
            "full_schema_highest_migration": full_highest,
            "full_schema_applied_count": full_count,
            "full_schema_ledger_sha256": full_digest,
        },
        "target_prefix": {
            "through": through,
            "highest_migration": prefix_highest,
            "applied_count": prefix_count,
            "ledger_sha256": prefix_digest,
        },
        "selected_migrations": rows_with_ordinals(selected_rows, boundary),
        "selected_migrations_sha256": schema_ledger_identity(selected_rows)[2],
        "held_back_migrations": rows_with_ordinals(rows[boundary + 1:], boundary + 2),
        "held_back_migrations_sha256": schema_ledger_identity(rows[boundary + 1:])[2],
    }
    # This is deliberately not a JSON serializer hash: PostgreSQL jsonb's
    # presentation order is not Python's. The DB routine derives these same
    # fixed fields and joins them with the same LF-only preimage.
    preimage = "\n".join((
        contract["purpose"], contract["environment"], "false", source_sha,
        artifact_digest, provider_uuid, full_digest,
        prefix_highest, str(prefix_count), prefix_digest,
        ",".join(item["filename"] for item in contract["selected_migrations"]),
        ",".join(str(item["ordinal"]) for item in contract["selected_migrations"]),
        contract["selected_migrations_sha256"],
        ",".join(item["filename"] for item in contract["held_back_migrations"]),
        ",".join(str(item["ordinal"]) for item in contract["held_back_migrations"]),
        contract["held_back_migrations_sha256"],
    )).encode()
    contract["contract_sha256"] = "sha256:" + hashlib.sha256(preimage).hexdigest()
    return contract


def verify_bounded_forward_fix_contract(contract: dict) -> None:
    """Rebuild and compare a typed staging-prefix contract from immutable git."""
    if not isinstance(contract, dict):
        raise ValueError("bounded forward-fix contract is not an object")
    source = contract.get("source")
    target = contract.get("target_prefix")
    held = contract.get("held_back_migrations")
    if (contract.get("contract_version") != 1
            or contract.get("purpose") != "program5-forward-fix-staging-prefix"
            or contract.get("environment") != "staging"
            or contract.get("production_deploy_authorized") is not False
            or not isinstance(source, dict) or not isinstance(target, dict)
            or not isinstance(held, list)):
        raise ValueError("bounded forward-fix contract shape is invalid")
    source_sha = source.get("git_sha")
    through = target.get("through")
    provider_version = source.get("candidate_provider_version_id")
    if not isinstance(source_sha, str) or not isinstance(through, str) or not isinstance(provider_version, str):
        raise ValueError("bounded forward-fix contract identity is invalid")
    # Rebuild from the object store rather than trust serialised member rows.
    rebuilt_manifest = bind_provider(
        build(source_sha, "carr-mcp", "production"), "cloudflare-workers", provider_version)
    rebuilt = bounded_forward_fix_contract(rebuilt_manifest, through,
                                           [item.get("filename") for item in held if isinstance(item, dict)],
                                           provider_version)
    if contract != rebuilt:
        raise ValueError("bounded forward-fix contract digest or immutable source contents differ")


def performance_contract(budget_ref: object, budget_ms: object,
                         recovery_strategy: object,
                         rollback_plan_ref: object = None
                         ) -> tuple[str | None, int | None, str | None, str | None]:
    """Validate the performance/recovery plan that an approver reads."""
    if (budget_ref is None and budget_ms is None and recovery_strategy is None
            and rollback_plan_ref is None):
        return None, None, None, None
    if (budget_ref is None or budget_ms is None or recovery_strategy is None
            or rollback_plan_ref is None):
        raise ValueError("performance assurance fields must be supplied together")
    if not isinstance(budget_ref, str) or not REFERENCE_RE.fullmatch(budget_ref):
        raise ValueError("performance_budget_ref must be a non-empty stable reference")
    if isinstance(budget_ms, bool) or not isinstance(budget_ms, int) or budget_ms <= 0:
        raise ValueError("performance_budget_ms must be a positive integer")
    if recovery_strategy not in RECOVERY_STRATEGIES:
        raise ValueError("recovery_strategy must be one of: " + ", ".join(RECOVERY_STRATEGIES))
    if (not isinstance(rollback_plan_ref, str)
            or not REFERENCE_RE.fullmatch(rollback_plan_ref)):
        raise ValueError("rollback_plan_ref must be a non-empty stable reference")
    return budget_ref, budget_ms, recovery_strategy, rollback_plan_ref


def manifest_performance_contract(
        manifest: dict) -> tuple[str | None, int | None, str | None, str | None]:
    return performance_contract(manifest.get("performance_budget_ref"),
                                manifest.get("performance_budget_ms"),
                                manifest.get("recovery_strategy"),
                                manifest.get("rollback_plan_ref"))


def build(sha_ref: str, service: str, environment: str,
          since: str | None = None, performance_budget_ref: str | None = None,
          performance_budget_ms: int | None = None,
          recovery_strategy: str | None = None,
          rollback_plan_ref: str | None = None) -> dict:
    try:
        budget_ref, budget_ms, strategy, recovery_plan = performance_contract(
            performance_budget_ref, performance_budget_ms, recovery_strategy,
            rollback_plan_ref)
    except ValueError as exc:
        sys.exit(f"release-manifest: {exc}")
    sha = resolve_sha(sha_ref)
    entries = tree_entries(sha, DEPLOYED_PATHS)
    if not entries:
        sys.exit(f"release-manifest: no files under {DEPLOYED_PATHS} at {sha} — "
                 "refusing to digest an empty artifact")
    migrations, basis = migration_set(sha, since)
    schema_applied_count, schema_highest_migration, schema_ledger_sha256 = (
        applied_schema_ledger(sha)
    )
    config_paths = expand_config_paths(sha)
    try:
        program6_actions = program6_actions_at(sha, environment)
    except ValueError as exc:
        sys.exit(f"release-manifest: {exc}")

    manifest = {
        "manifest_version": 1,
        "service": service,
        "environment": environment,
        "performance_budget_ref": budget_ref,
        "performance_budget_ms": budget_ms,
        "recovery_strategy": strategy,
        "rollback_ready": recovery_plan is not None,
        "rollback_plan_ref": recovery_plan,

        "git_sha": sha,
        "artifact_digest": digest_entries(entries),
        "artifact_file_count": len(entries),
        "artifact_paths": list(DEPLOYED_PATHS),

        "dependency_lock_digest": digest_files(sha, LOCK_PATHS),
        "dependency_lock_paths": list(LOCK_PATHS),

        "config_fingerprint": digest_files(sha, config_paths),
        "config_paths": list(config_paths),
        "program6_actions": program6_actions,

        "migration_set": migrations,
        "migration_set_basis": basis,
        # Recorded so verify() rebuilds against the SAME basis. Without it a
        # manifest built with --since would fail its own rebuild, which would be
        # the check reporting a defect it created itself.
        "migration_set_since": resolve_sha(since) if since else None,
        "schema_highest_migration": schema_highest_migration,
        "schema_applied_count": schema_applied_count,
        "schema_ledger_sha256": schema_ledger_sha256,

        "commit_subject": git("log", "-1", "--format=%s", sha).strip(),
        "commit_authored_at": git("log", "-1", "--format=%aI", sha).strip(),
    }
    manifest["plan_hash"] = plan_hash(manifest)
    return manifest


def plan_hash(manifest: dict) -> str:
    material = {k: manifest.get(k) for k in PLAN_FIELDS}
    # Old manifests predate assurance fields. Omitting an all-null assurance
    # group preserves their recorded hash; once any assurance input exists, the
    # whole recovery/performance plan enters the approval preimage.
    if any(manifest.get(k) is not None for k in ASSURANCE_PLAN_FIELDS
           if k != "rollback_ready"):
        material.update({k: manifest.get(k) for k in ASSURANCE_PLAN_FIELDS})
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return "plan:" + hashlib.sha256(blob.encode()).hexdigest()[:32]


def provider_binding(manifest: dict) -> tuple[str | None, str | None]:
    """Validate an optional immutable provider-version binding.

    A source manifest deliberately has neither field: a source rebuild cannot
    honestly know a provider-generated version ID. Once upload returns it, the
    pair is all-or-nothing. Accept absent/null for backwards-compatible source
    manifests; reject a partial or blank pair rather than hashing ambiguity.
    """
    provider = manifest.get("provider")
    version_id = manifest.get("provider_version_id")
    if provider is None and version_id is None:
        return None, None
    if (not isinstance(provider, str) or not provider.strip()
            or not isinstance(version_id, str) or not version_id.strip()):
        raise ValueError("provider and provider_version_id must be non-empty strings together")
    return provider, version_id


def bind_provider(manifest: dict, provider: str, version_id: str) -> dict:
    """Return a post-upload manifest bound to one immutable provider version.

    This does not rebuild or alter source evidence. It only records the version
    identity the provider already assigned and recalculates the approval plan
    hash over that now-complete preimage. A different later version must start
    from a fresh source manifest and approval plan; it cannot overwrite history.
    """
    if not provider.strip() or not version_id.strip():
        raise ValueError("--provider and --provider-version-id must be non-empty")
    manifest_performance_contract(manifest)
    recorded_provider, recorded_version = provider_binding(manifest)
    if recorded_provider is not None:
        if (recorded_provider, recorded_version) != (provider, version_id):
            raise ValueError("manifest is already bound to a different provider/version")
        # An exact retry is safe and deterministic (for example, after a
        # transport failure while persisting stdout); do not create a new ID.
        bound = dict(manifest)
    else:
        bound = dict(manifest)
        bound["provider"] = provider
        bound["provider_version_id"] = version_id
    bound["plan_hash"] = plan_hash(bound)
    return bound


def verify(manifest: dict) -> int:
    """Rebuild from the manifest's OWN recorded SHA and diff. This is the
    acceptance clause, executable."""
    sha = manifest.get("git_sha")
    if not sha:
        print("verify: the manifest records no git_sha — nothing to rebuild from")
        return 1

    try:
        provider_binding(manifest)
        manifest_performance_contract(manifest)
    except ValueError as exc:
        print(f"verify: invalid provider binding: {exc}")
        return 1

    rebuilt = build(sha, manifest.get("service", ""), manifest.get("environment", ""),
                    manifest.get("migration_set_since"),
                    manifest.get("performance_budget_ref"),
                    manifest.get("performance_budget_ms"),
                    manifest.get("recovery_strategy"),
                    manifest.get("rollback_plan_ref"))
    compared = ("artifact_digest", "dependency_lock_digest", "config_fingerprint", "program6_actions",
                "schema_highest_migration", "schema_applied_count",
                "schema_ledger_sha256", "migration_set", "artifact_file_count")

    failures = []
    for field in compared:
        want, got = manifest.get(field), rebuilt.get(field)
        if want != got:
            failures.append(f"  {field}\n    recorded: {want}\n    rebuilt:  {got}")
        else:
            print(f"  ok    {field}")

    # Rebuild knows source inputs only, so it intentionally does not generate
    # provider fields. Validate the recorded hash against this manifest's own
    # approval preimage instead of comparing it to the unbound rebuilt hash.
    recorded_hash = manifest.get("plan_hash")
    expected_hash = plan_hash(manifest)
    if recorded_hash != expected_hash:
        failures.append("  plan_hash\n"
                        f"    recorded: {recorded_hash}\n"
                        f"    expected: {expected_hash}")
    else:
        print("  ok    plan_hash")

    if failures:
        print(f"\nverify: {len(failures)} mismatch(es) rebuilding {sha[:12]}")
        for f in failures:
            print(f)
        print("\nThe artifact recorded against this SHA is NOT what that SHA produces.")
        return 1

    print(f"\nverify: identical rebuild from {sha[:12]} — every digest matches.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="compute the manifest for a SHA")
    b.add_argument("--sha", default="HEAD")
    b.add_argument("--service", default="carr-mcp")
    b.add_argument("--environment", default="production")
    b.add_argument("--since", default=None,
                   help="the previous release's SHA, so migration_set means "
                        "'added since that release' rather than 'every migration'")
    b.add_argument("--performance-budget-ref",
                   help="approved performance budget reference")
    b.add_argument("--performance-budget-ms", type=int,
                   help="approved maximum elapsed milliseconds")
    b.add_argument("--recovery-strategy", choices=RECOVERY_STRATEGIES,
                   help="approved recovery strategy")
    b.add_argument("--rollback-plan-ref",
                   help="immutable rollback or forward-fix plan reference")

    v = sub.add_parser("verify", help="rebuild from the manifest's own SHA and diff")
    v.add_argument("--manifest", required=True)

    ph = sub.add_parser("plan-hash", help="hash the fields an approver reads")
    ph.add_argument("--manifest", required=True)

    bp = sub.add_parser("bind-provider", help="bind a post-upload provider version to a manifest")
    bp.add_argument("--manifest", required=True)
    bp.add_argument("--provider", required=True)
    bp.add_argument("--provider-version-id", required=True)

    pp = sub.add_parser("program6-posture", help="print the manifest-bound Program 6 posture")
    pp.add_argument("--manifest", required=True)

    sfp = sub.add_parser("staging-forward-fix-prefix",
                         help="build a staging-only Program 5 bounded prefix contract")
    sfp.add_argument("--manifest", required=True)
    sfp.add_argument("--through", required=True,
                     help="exact selected migration boundary")
    sfp.add_argument("--held-back", action="append", required=True,
                     help="every later source migration, in exact source order")
    sfp.add_argument("--candidate-provider-version-id", required=True)

    vsfp = sub.add_parser("verify-staging-forward-fix-prefix",
                          help="rebuild a staging-only bounded prefix contract")
    vsfp.add_argument("--contract", required=True)

    args = p.parse_args()

    if args.cmd == "build":
        print(json.dumps(
            build(args.sha, args.service, args.environment, args.since,
                  args.performance_budget_ref, args.performance_budget_ms,
                  args.recovery_strategy, args.rollback_plan_ref), indent=2))
        return 0

    if args.cmd == "verify-staging-forward-fix-prefix":
        try:
            contract = json.loads(Path(args.contract).read_text())
            verify_bounded_forward_fix_contract(contract)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"release-manifest: {exc}", file=sys.stderr)
            return 1
        print("verify: exact staging-only bounded forward-fix contract.")
        return 0

    manifest = json.loads(Path(args.manifest).read_text())
    if args.cmd == "verify":
        return verify(manifest)
    if args.cmd == "bind-provider":
        try:
            print(json.dumps(bind_provider(manifest, args.provider,
                                           args.provider_version_id), indent=2))
        except ValueError as exc:
            print(f"release-manifest: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "program6-posture":
        try:
            print(manifest_program6_posture(manifest))
        except ValueError as exc:
            print(f"release-manifest: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "staging-forward-fix-prefix":
        try:
            print(json.dumps(bounded_forward_fix_contract(
                manifest, args.through, args.held_back,
                args.candidate_provider_version_id), indent=2))
        except ValueError as exc:
            print(f"release-manifest: {exc}", file=sys.stderr)
            return 1
        return 0
    print(plan_hash(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
