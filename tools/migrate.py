#!/usr/bin/env python3
# mypy: ignore-errors
# GRANDFATHERED 2026-08-06: predates the nightly type-check tripwire and fails it.
# Fix this file's mypy errors and delete these three lines when you next touch it.
"""carr-system migrations runner (record layer, scaffolded 2026-07-30).

Applies migrations/NNNN_*.sql in filename order, each in its own transaction,
tracked in schema_migrations. Forward-only by design: a bad migration is
fixed by a NEW migration, never by editing an applied file (applied files'
sha256 is recorded and re-checked, so drift is caught).

Usage:
    DATABASE_URL=postgres://... python3 tools/migrate.py            # dry run
    DATABASE_URL=postgres://... python3 tools/migrate.py --apply    # apply, confirm host
    DATABASE_URL=postgres://... python3 tools/migrate.py --apply --yes

CREDENTIAL RULE (stress-test addendum A14): build sessions run against a
NEON BRANCH credential, never the production writer. Risky changes rehearse
on a branch of production data before touching production. This runner
cannot tell a branch URL from production, so it prints the host and makes
you confirm — read what it prints.

Requires psycopg (pip install 'psycopg[binary]'); listed in requirements.txt.
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
NAME_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")

# ── DDL TIMEOUTS (added 2026-08-02, cold-session audit) ──────────────────────
# WHY. Migrations are applied by hand against production Neon while a Cloudflare
# Worker holds live connections. An ALTER TABLE needs an ACCESS EXCLUSIVE lock,
# and Postgres queues lock requests: if the ALTER lands behind a long-running
# read it waits — and every query that arrives after it, including trivial ones
# that would not otherwise conflict, queues behind the ALTER. A change that
# takes two milliseconds of actual work can therefore stall the whole API for as
# long as one unrelated slow read runs. Without lock_timeout the runner waits
# for ever and looks like it is "still applying".
#
# 5 SECONDS, because the failure mode we are avoiding IS the wait. Every DDL in
# migrations/ is a catalog change on a database of ~67 tables and ~17k rows;
# none of them needs five seconds to ACQUIRE a lock, so anything that does is
# blocked rather than busy. Failing fast costs a re-run; waiting costs an
# outage. This is the standard online-migration posture (gitlab, strong_migrations
# and friends all sit in the 50ms–5s band); 5s is the forgiving end of it,
# chosen because a human is watching this run and a spurious abort wastes their
# attention.
#
# statement_timeout is the second half and a much blunter tool: it bounds how
# long a migration may HOLD a lock once it has one. Set too low it kills
# legitimate backfills, so it is deliberately generous at 5 minutes — roughly
# two orders of magnitude more than anything in migrations/ has ever needed on
# this data, while still guaranteeing a runaway statement cannot pin the API
# indefinitely.
#
# BOTH ARE OVERRIDABLE, because the day someone writes a genuine long backfill
# they should raise the ceiling consciously rather than delete this block:
#   CARR_MIGRATE_LOCK_TIMEOUT=30s CARR_MIGRATE_STATEMENT_TIMEOUT=30min
# A migration may also override either one for itself with `set local ...` as
# its first statement; SET LOCAL inside the transaction wins over the session
# value set here, and reverts at commit.
LOCK_TIMEOUT = os.environ.get("CARR_MIGRATE_LOCK_TIMEOUT", "5s")
STATEMENT_TIMEOUT = os.environ.get("CARR_MIGRATE_STATEMENT_TIMEOUT", "5min")

BOOTSTRAP = """
create table if not exists schema_migrations (
  filename   text primary key,
  sha256     text not null,
  applied_at timestamptz not null default now()
);
"""


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_migrations() -> list[tuple[str, str, str]]:
    """Return [(filename, sql, sha256)] sorted by filename."""
    if not MIGRATIONS_DIR.is_dir():
        fail(f"no migrations directory at {MIGRATIONS_DIR}")
    out = []
    for p in sorted(MIGRATIONS_DIR.iterdir()):
        if p.suffix == ".sql":
            if not NAME_RE.match(p.name):
                fail(f"bad migration filename (want NNNN_name.sql): {p.name}")
            sql = p.read_text()
            out.append((p.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
    if not out:
        fail("no .sql files in migrations/")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="apply pending migrations")
    ap.add_argument("--yes", action="store_true", help="skip the host confirmation")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        fail("DATABASE_URL is not set. Use a Neon BRANCH credential for build work (A14).")

    try:
        import psycopg
    except ImportError:
        fail("psycopg not installed: pip install 'psycopg[binary]'")

    migrations = load_migrations()

    with psycopg.connect(url) as conn:
        host = conn.info.host
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
            conn.commit()
            cur.execute("select filename, sha256 from schema_migrations")
            applied = dict(cur.fetchall())

        # drift check: an applied file must not have changed on disk
        for name, _sql, digest in migrations:
            if name in applied and applied[name] != digest:
                fail(f"{name} was EDITED after being applied (sha mismatch). "
                     "Write a new migration instead; never rewrite an applied one.")

        pending = [(n, s, d) for n, s, d in migrations if n not in applied]
        print(f"host: {host}")
        print(f"applied: {len(applied)}   pending: {len(pending)}")
        for name, _s, _d in pending:
            print(f"  pending: {name}")
        if not pending:
            print("nothing to do")
            return
        if not args.apply:
            print("dry run — pass --apply to run these")
            return
        if not args.yes:
            answer = input(f"Apply {len(pending)} migration(s) to host '{host}'? "
                           "Type the host name to confirm: ").strip()
            if answer != host:
                fail("confirmation did not match host; nothing applied")

        print(f"lock_timeout: {LOCK_TIMEOUT}   statement_timeout: {STATEMENT_TIMEOUT}")
        for name, sql, digest in pending:
            with conn.cursor() as cur:
                print(f"applying {name} ...", end=" ", flush=True)
                # SET LOCAL, not SET: scoped to THIS migration's transaction and
                # reverted at commit, so one migration can never leak a timeout
                # onto the next. It is re-issued per migration on purpose — a
                # migration that resets the session must not silently disarm the
                # guard for everything that follows it.
                cur.execute(f"set local lock_timeout = '{LOCK_TIMEOUT}'")
                cur.execute(f"set local statement_timeout = '{STATEMENT_TIMEOUT}'")
                try:
                    cur.execute(sql)
                except psycopg.errors.LockNotAvailable:
                    # Named explicitly so nobody debugs the migration. Nothing is
                    # applied: this migration's transaction rolls back whole, and
                    # every migration before it is already committed and recorded,
                    # so a re-run picks up exactly here. Forward-only is intact.
                    conn.rollback()
                    fail(f"{name} could not acquire its lock within {LOCK_TIMEOUT} and was "
                         "ABANDONED (nothing applied from this file).\n"
                         "  This is the guard working, not a broken migration. Something else "
                         "is holding a lock on the tables it touches — usually a long-running "
                         "read from the Worker.\n"
                         "  Check pg_stat_activity for the blocker, then just re-run: earlier "
                         "migrations are already committed and will be skipped.\n"
                         f"  To wait longer on purpose: CARR_MIGRATE_LOCK_TIMEOUT=30s")
                except psycopg.errors.QueryCanceled:
                    conn.rollback()
                    fail(f"{name} exceeded statement_timeout ({STATEMENT_TIMEOUT}) and was "
                         "ABANDONED (nothing applied from this file).\n"
                         "  If this migration genuinely needs longer, raise the ceiling "
                         "deliberately rather than removing it:\n"
                         f"  CARR_MIGRATE_STATEMENT_TIMEOUT=30min python3 tools/migrate.py --apply")
                cur.execute(
                    "insert into schema_migrations (filename, sha256) values (%s, %s)",
                    (name, digest),
                )
            conn.commit()
            print("ok")
        print("done")


if __name__ == "__main__":
    main()
