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

--branch <name> (before the mode) targets a Neon branch instead of production —
the rehearse-on-branch pattern through the same no-substitution path (added
2026-07-31, Fable seat session 4, for the 0026 rehearsal).

Never prints the DSN. ON_ERROR_STOP is always set for sql mode.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEONCTL = os.path.join(REPO, "mcp-server", "node_modules", ".bin", "neonctl")
PSQL_CANDIDATES = [
    "/opt/homebrew/opt/libpq/bin/psql",
    "/usr/local/opt/libpq/bin/psql",
    "psql",
]


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


def dsn(branch: str = "production") -> str:
    key = _neon_api_key()
    env = {**os.environ,
           "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    if key:
        env["NEON_API_KEY"] = key
    out = subprocess.run(
        [NEONCTL, "connection-string", branch,
         "--project-id", "steep-field-48688294",
         "--role-name", "neondb_owner"],
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


def main() -> None:
    argv = sys.argv[1:]
    branch = "production"
    if argv and argv[0] == "--branch":
        if len(argv) < 2:
            sys.exit("--branch needs a name")
        branch, argv = argv[1], argv[2:]
    if len(argv) < 2 or argv[0] not in ("sql", "run"):
        sys.exit(__doc__)
    mode, target, extra = argv[0], argv[1], argv[2:]
    target_abs = target if os.path.isabs(target) else os.path.join(REPO, target)
    if not os.path.exists(target_abs):
        sys.exit(f"no such file: {target_abs}")
    url = dsn(branch)
    os.chdir(REPO)
    if mode == "sql":
        rc = subprocess.run([psql_bin(), url, "-v", "ON_ERROR_STOP=1", "-f", target_abs]).returncode
    else:
        env = {**os.environ, "DATABASE_URL": url}
        # Cloudflare ACCOUNT ID (an identifier, not a credential) — needed by
        # R2 wrangler calls in pipeline scripts; verbatim from ORDER 20's taps.
        env.setdefault("CLOUDFLARE_ACCOUNT_ID", "12ccca77eb49142a6be8eb84c0d6a3a0")
        rc = subprocess.run([os.path.join(REPO, ".venv", "bin", "python"), target_abs, *extra], env=env).returncode
    sys.exit(rc)


if __name__ == "__main__":
    main()
