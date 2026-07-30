#!/usr/bin/env python3
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

        for name, sql, digest in pending:
            with conn.cursor() as cur:
                print(f"applying {name} ...", end=" ", flush=True)
                cur.execute(sql)
                cur.execute(
                    "insert into schema_migrations (filename, sha256) values (%s, %s)",
                    (name, digest),
                )
            conn.commit()
            print("ok")
        print("done")


if __name__ == "__main__":
    main()
