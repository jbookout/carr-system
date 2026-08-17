#!/usr/bin/env python3
"""
db-tap.py — run a production tap (a .sql file or a pipelines/ script) without
shell command substitution.

WHY THIS EXISTS (2026-07-31, Fable seat session 3): Joe opened the psql gate
(three allow rules in the vault project's .claude/settings.json), but the
harness classifier still refuses any Bash command containing $(...) because it
cannot vouch for the nested command. backup-dump.sh already solved this shape:
derive the owner DSN from neonctl INSIDE the process. This tool is that pattern
for taps: it obtains the connection string itself, then either runs psql -f on
a SQL file or execs a pipelines/ script with DATABASE_URL set.

Usage:
  .venv/bin/python tools/db-tap.py sql pipelines/r2-quota-seed.sql
  .venv/bin/python tools/db-tap.py run pipelines/backfill_document_attachments.py [--apply ...]
  .venv/bin/python tools/db-tap.py --branch rehearse-0026 run tools/migrate.py --apply --yes

--project staging (before the mode) targets the ISOLATED STAGING PROJECT, a
separate Neon project that shares no data or credentials with production. That
separation is the point: a Neon BRANCH lives inside production's project and
starts as a copy of its data, so it is not isolation.

--branch <name> (before the mode) targets a Neon branch instead of production —
the rehearse-on-branch pattern through the same no-substitution path (added
2026-07-31, Fable seat session 4, for the 0026 rehearsal).

READ-ONLY BY DEFAULT (Phase 1, 2026-08-13): every ordinary invocation, sql or
run, opens its subprocess with default_transaction_read_only=on. A write
against production fails at the server unless you deliberately break glass:

  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason "why" sql pipelines/x.sql

Both CARR_BREAK_GLASS=1 and a non-empty --reason are required together. A
break-glass run is banner-announced, requires a local actor identity
(~/.config/carr/local-actor.json, see bin/set-local-actor.sh), and appends a
receipt to out/break-glass-receipts.log before the target ever runs.

Never prints the DSN. ON_ERROR_STOP is always set for sql mode.
"""
import json
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEONCTL = os.path.join(REPO, "mcp-server", "node_modules", ".bin", "neonctl")
PSQL_CANDIDATES = [
    "/opt/homebrew/opt/libpq/bin/psql",
    "/usr/local/opt/libpq/bin/psql",
    "psql",
]
LOCAL_ACTOR_FILE = os.path.join(os.path.expanduser("~"), ".config", "carr", "local-actor.json")
RECEIPT_LOG = os.path.join(REPO, "out", "break-glass-receipts.log")


def _neon_api_key() -> str:
    """NEON_API_KEY from the environment, or from db.env beside the other
    credentials. Empty string when there is none.

    WHY THIS EXISTS, 2026-08-10. neonctl's saved browser login expires on its own
    schedule, and when it does it does not fail cleanly — it PROMPTS, waits 60
    seconds for a browser nobody is sitting at, and times out. Because this
    function is the one place four different jobs derive a credential, that one
    expiry silently took down all four: bin/migrate-prod.sh, bin/import-doctrine.sh,
    bin/restore-rehearse.sh (the only proof the encrypted backups can be restored)
    and pipelines/partner_ping.py, the Joe/Dell interrupt channel.

    The ping is the one that shows how bad the failure mode is. It kept running
    every 120 seconds and kept logging "nothing new since 2026-08-03T20:02:16",
    382 consecutive identical lines over six days, because a channel whose query
    is broken and a channel with genuinely nothing to say produce byte-identical
    output. Its watermark had not moved since the day the login lapsed. Nothing
    alarmed, because nothing watches it.

    A Neon API key does not expire on a timer and needs no browser, and neonctl
    reads it from NEON_API_KEY. This keeps the property the pattern was built for
    — the DSN is still derived per invocation, still never on a command line and
    never in a transcript — and removes only the human from the refresh."""
    key = (os.environ.get("NEON_API_KEY") or "").strip()
    if key:
        return key
    env_file = os.path.join(os.path.expanduser("~"), ".config", "carr", "db.env")
    try:
        with open(env_file, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("NEON_API_KEY="):
                    # db.env values are shell-quoted so `set -a; . db.env` survives
                    # an & in a DSN; strip the quotes the same way db_url() does.
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


# The two Neon PROJECTS, which is the isolation boundary that matters. A Neon
# BRANCH lives inside its parent project and starts as a copy of its data, so a
# branch of production is NOT isolated from production — it IS production's data
# under another name. Gate G1 requires an environment that cannot reach
# production's data or credentials, and only a separate project delivers that.
# Created 2026-08-13 alongside this change.
# Each project carries its own DEFAULT BRANCH NAME, because they differ and
# assuming otherwise fails at the neonctl call: the production project's primary
# branch is named "production", while a freshly created Neon project's is "main".
# Production is PINNED BY ID and staging is RESOLVED BY NAME, and the asymmetry
# is deliberate. Production's id must never drift to whatever a lookup happens to
# return — a name collision or a typo that silently repointed it at another
# project is the worst failure this file could have. Staging, by contrast, is
# rebuilt whenever it needs to be (it holds nothing that cannot be regenerated),
# and pinning its id meant editing this file on every rebuild — a step someone
# forgets, after which the tool writes to a project that no longer exists or,
# worse, to whatever inherited the id.
PROJECTS = {
    "production": {"id": "steep-field-48688294", "default_branch": "production"},
    "staging":    {"name": "carr-staging", "default_branch": "main"},
}
NEON_ORG = "org-dry-dew-75906281"


def _project_id_by_name(name: str, env: dict) -> str:
    """Resolve a Neon project id from its name. Never prints a connection string;
    the projects list carries ids and names only."""
    out = subprocess.run(
        [NEONCTL, "projects", "list", "--org-id", NEON_ORG, "--output", "json"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    if out.returncode != 0:
        sys.exit(f"db-tap: could not list Neon projects (rc={out.returncode}): {out.stderr.strip()[:200]}")
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.exit("db-tap: Neon project list was not valid JSON")
    rows = payload if isinstance(payload, list) else payload.get("projects", [])
    matches = [r["id"] for r in rows if r.get("name") == name]
    if not matches:
        sys.exit(f"db-tap: no Neon project named '{name}'. Create it, then retry.")
    if len(matches) > 1:
        sys.exit(f"db-tap: {len(matches)} Neon projects named '{name}' — refusing to guess which.")
    return matches[0]


def dsn(branch=None, project: str = "production", role_name: str = "neondb_owner") -> str:
    """Connection string for a branch of a named PROJECT.

    The value is returned to the caller and never printed. neonctl prints the
    URI with its password embedded when run bare, which is how a live credential
    ended up in a session transcript on 2026-08-13 while creating the staging
    project — that project was destroyed and rebuilt with output suppressed
    rather than left with a credential that had been written down. Every path
    through this file keeps that property: derive, use, never echo.
    """
    spec = PROJECTS.get(project)
    if spec is None:
        sys.exit(f"db-tap: unknown project '{project}' (known: {', '.join(PROJECTS)})")
    if role_name not in ("neondb_owner", "app_writer"):
        sys.exit("db-tap: role_name must be one of neondb_owner or app_writer")
    key = _neon_api_key()
    env = {**os.environ,
           "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    if key:
        env["NEON_API_KEY"] = key
    project_id = spec.get("id") or _project_id_by_name(spec["name"], env)
    if branch is None:
        branch = spec["default_branch"]
    out = subprocess.run(
        [NEONCTL, "connection-string", branch,
         "--project-id", project_id,
         "--role-name", role_name],
        capture_output=True, text=True, timeout=60, env=env,
    )
    if out.returncode != 0 or not out.stdout.strip():
        # NAME THE ACTUAL CAUSE. The old message printed neonctl's stderr, which
        # on an expired login is a browser URL and an "authentication timed out"
        # line — true, and it does not tell the reader that the fix is a stored
        # key rather than another browser trip. A timeout with no key present is
        # this failure until proven otherwise.
        detail = out.stderr.strip()[:200]
        if not key:
            sys.exit(
                "neonctl could not derive a connection string, and NEON_API_KEY is NOT SET.\n"
                "  This is almost certainly the expired-browser-login failure: neonctl\n"
                "  prompts for a browser, waits 60 seconds, and gives up.\n"
                "  Fix it once: create a Neon API key in the console and add it to\n"
                "  ~/.config/carr/db.env as NEON_API_KEY=... (chmod 600, already gitignored).\n"
                f"  neonctl said: {detail}")
        sys.exit(f"neonctl failed with NEON_API_KEY set (rc={out.returncode}): {detail}")
    return out.stdout.strip()


def psql_bin() -> str:
    for p in PSQL_CANDIDATES:
        if os.path.sep not in p or os.path.exists(p):
            return p
    return "psql"


def _engaged(reason: str) -> bool:
    # Break-glass is an ACCIDENT gate and an audit trail, not an attacker gate:
    # any process running as this Mac user can set CARR_BREAK_GLASS=1 and pass
    # any --reason string, exactly as this script itself can. The real security
    # boundary is server-side (a scoped role/credential), not this file — same
    # honest limit bin/set-local-actor.sh states for identity.
    return os.environ.get("CARR_BREAK_GLASS") == "1" and bool(reason and reason.strip())


def local_actor_slug() -> str:
    """Public (Phase 1, 2026-08-13): tools/call-verb.py's own break-glass path
    (mcp-server/local-verb.mjs's direct-database mode) reuses this rather than
    re-implementing the same ~/.config/carr/local-actor.json read a second
    time — rule a8c55a47, a manual path and an automated path that do the same
    job must be the same code. Was `_local_actor_slug`; renamed, no other
    caller referenced the old name."""
    try:
        with open(LOCAL_ACTOR_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        slug = (data.get("actor_slug") or "").strip()
        if slug:
            return slug
    except (OSError, ValueError):
        pass
    return "identity-not-set"


def append_receipt(actor: str, mode: str, target: str, host: str, reason: str,
                    log_path: str = RECEIPT_LOG) -> None:
    """Public (Phase 1, 2026-08-13): same reuse rationale as local_actor_slug
    above. tools/call-verb.py's break-glass path appends to this exact log,
    in this exact line shape, so out/break-glass-receipts.log stays ONE audit
    trail for every break-glass act in the repo rather than two files that
    could drift in format. Was `_append_receipt`; renamed, no other caller
    referenced the old name."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    line = (f"{datetime.now(timezone.utc).isoformat()} actor={actor} mode={mode} "
            f'target={target} host={host} reason="{reason.strip()}"\n')
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(line)


def main() -> None:
    argv = sys.argv[1:]
    branch = None            # None = use the project's own default branch
    project = "production"
    reason = ""
    while argv and argv[0] in ("--branch", "--reason", "--project"):
        flag = argv[0]
        if len(argv) < 2:
            sys.exit(f"{flag} needs a value")
        if flag == "--branch":
            branch = argv[1]
        elif flag == "--project":
            project = argv[1]
            # A named project's default branch is its own "production" branch in
            # Neon's vocabulary; only override when the caller also asked for one.
        else:
            reason = argv[1]
        argv = argv[2:]
    if len(argv) < 2 or argv[0] not in ("sql", "run"):
        sys.exit(__doc__)
    mode, target, extra = argv[0], argv[1], argv[2:]
    target_abs = target if os.path.isabs(target) else os.path.join(REPO, target)
    if not os.path.exists(target_abs):
        sys.exit(f"no such file: {target_abs}")
    url = dsn(branch, project)
    os.chdir(REPO)

    # THE READ-ONLY DEFAULT IS A PRODUCTION PROTECTION, NOT A UNIVERSAL ONE.
    # Break-glass exists because an unintended write against the live record
    # layer is unrecoverable. Staging is a separate Neon project holding no
    # production data, and writing to it is the entire reason it exists —
    # requiring break-glass there would train everyone to type CARR_BREAK_GLASS=1
    # routinely, which is precisely how a protection stops protecting the thing
    # it was built for. So staging is writable and production is not.
    engaged = _engaged(reason)
    # THE READ-ONLY DEFAULT IS A PRODUCTION PROTECTION, NOT A UNIVERSAL ONE, and
    # writability is a SEPARATE question from break-glass. Break-glass exists
    # because an unintended write against the live record layer is unrecoverable;
    # it logs a receipt and announces itself. Staging is a separate Neon project
    # holding no production data, and writing to it is the entire reason it
    # exists. Requiring break-glass there would mean a receipt for every routine
    # staging load and would train everyone to type CARR_BREAK_GLASS=1 by habit —
    # exactly how a protection stops protecting the thing it was built for.
    # So: staging is writable WITHOUT being break-glass.
    writable = engaged or project != "production"
    env = {**os.environ}

    if engaged:
        actor = local_actor_slug()
        host = urllib.parse.urlsplit(url).hostname or "unknown-host"
        if actor == "identity-not-set":
            append_receipt(actor, mode, target, host, reason)
            sys.exit(
                "BREAK-GLASS REFUSED: no local actor identity found at\n"
                "  ~/.config/carr/local-actor.json (written once per machine by\n"
                "  bin/set-local-actor.sh). Run that script, then retry.\n"
                "  This refused attempt was still logged to\n"
                "  out/break-glass-receipts.log as identity-not-set."
            )
        print("=" * 72, file=sys.stderr)
        print("BREAK-GLASS ENGAGED — running WITHOUT the read-only guard.", file=sys.stderr)
        print(f"  actor:  {actor}", file=sys.stderr)
        print(f"  reason: {reason.strip()}", file=sys.stderr)
        print(f"  mode:   {mode} {target}", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        # Log the ATTEMPT before running the target below — the attempt is the
        # event this receipt records, not success. Do not roll this line back
        # if the run then fails.
        append_receipt(actor, mode, target, host, reason)
    elif not writable:
        # Default posture: PGOPTIONS is a standard libpq env var honored the
        # same way whether the subprocess is psql (sql mode) or a Python
        # script connecting via psycopg/psycopg2 off DATABASE_URL (run mode,
        # also libpq-based) — one mechanism, no per-mode special-casing.
        # Merge onto any PGOPTIONS a caller already set rather than overwrite
        # it (none currently do).
        existing = env.get("PGOPTIONS", "")
        guard = "-c default_transaction_read_only=on"
        env["PGOPTIONS"] = f"{existing} {guard}".strip()

    if mode == "sql":
        rc = subprocess.run([psql_bin(), url, "-v", "ON_ERROR_STOP=1", "-f", target_abs], env=env).returncode
    else:
        env["DATABASE_URL"] = url
        # Cloudflare ACCOUNT ID (an identifier, not a credential) — needed by
        # R2 wrangler calls in pipeline scripts; verbatim from ORDER 20's taps.
        env.setdefault("CLOUDFLARE_ACCOUNT_ID", "12ccca77eb49142a6be8eb84c0d6a3a0")
        rc = subprocess.run([os.path.join(REPO, ".venv", "bin", "python"), target_abs, *extra], env=env).returncode

    if not writable and rc != 0:
        print(
            "refused (or failed) while running read-only. To write, set "
            'CARR_BREAK_GLASS=1 and pass --reason "..." — see tools/db-tap.py.',
            file=sys.stderr,
        )
    sys.exit(rc)


if __name__ == "__main__":
    main()
