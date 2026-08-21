#!/usr/bin/env python3
"""carr-system migrations runner (record layer, scaffolded 2026-07-30).

Applies migrations/NNNN_*.sql in filename order, each in its own transaction,
tracked in schema_migrations. Forward-only by design: a bad migration is
fixed by a NEW migration, never by editing an applied file (applied files'
sha256 is recorded and re-checked, so drift is caught).

Usage:
    DATABASE_URL=postgres://... python3 tools/migrate.py            # dry run
    DATABASE_URL=postgres://... python3 tools/migrate.py --through 0170_guidance_import_lifecycle.sql
    DATABASE_URL=postgres://... python3 tools/migrate.py --apply    # apply, confirm host
    DATABASE_URL=postgres://... python3 tools/migrate.py --apply --yes --through 0170_guidance_import_lifecycle.sql

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
from typing import NoReturn

from migration_number_contract import (
    LEGACY_APPLIED_ALIASES,
    MigrationNumberError,
    validate_migration_names,
)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# NNNN_name.sql, plus an OPTIONAL single lowercase letter after the number:
# 0013a_name.sql. Widened 2026-08-13 for a defect that could not be fixed inside
# the old contract.
#
# THE NEED. Applying every migration to an empty database died at 0014, which
# asserts fourteen client_status rows are flagged and found three: eleven slugs
# entered production out of band and no migration creates them. The fix has to
# run BEFORE 0014 on a fresh database, and 0013 and 0014 are consecutive
# integers, so there is no number between them. Appending at the end would not
# help — on an empty database it would still run after the migration it needs to
# precede. This is the ordinary reason migration tools use timestamps or decimal
# numbering; a single letter is the smallest change that buys the same thing.
#
# WHY IT IS SAFE. The regex only WIDENS what is accepted, so every existing
# filename still matches and nothing about already-applied migrations changes.
# Ordering is unaffected: Python sorts 0013_ < 0013a_ < 0014_ ('_' is 0x5F and
# 'a' is 0x61, then '3' < '4'), which is exactly the order the fix needs. And
# migrate.py is the ONLY parser of the filename shape — v_schema_ledger (0113)
# and mcp-server/src/release.js store and display the string without extracting
# a number from it, so widening here cannot desync a second reader.
NAME_RE = re.compile(r"^\d{4}[a-z]?_[a-z0-9_]+\.sql$")

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


def fail(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_migrations() -> list[tuple[str, str, str]]:
    """Return [(filename, sql, sha256)] sorted by filename."""
    if not MIGRATIONS_DIR.is_dir():
        fail(f"no migrations directory at {MIGRATIONS_DIR}")
    out: list[tuple[str, str, str]] = []
    for p in sorted(MIGRATIONS_DIR.iterdir()):
        if p.suffix == ".sql":
            if not NAME_RE.match(p.name):
                fail(f"bad migration filename (want NNNN_name.sql): {p.name}")
            sql = p.read_text()
            out.append((p.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
    if not out:
        fail("no .sql files in migrations/")
    try:
        validate_migration_names(
            (name for name, _sql, _digest in out), require_frozen=True
        )
    except MigrationNumberError as exc:
        fail(str(exc))
    return out


def pending_migrations(
    migrations: list[tuple[str, str, str]], applied: dict[str, str]
) -> list[tuple[str, str, str]]:
    """Return files absent from the filename-keyed ledger, preserving order."""
    return [(name, sql, digest) for name, sql, digest in migrations if name not in applied]


def migrations_through(
    migrations: list[tuple[str, str, str]],
    pending: list[tuple[str, str, str]],
    through: str | None,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Split pending migrations into the authorized prefix and held-back tail.

    A production activation may need an already-reviewed migration without also
    taking every newer, unrelated file that merged while the activation was
    waiting.  The boundary is an exact checked-in filename, and selection is
    still the ordinary forward-only filename prefix: no gap or hand-picked
    dependency can be skipped.
    """
    if through is None:
        return pending, []
    names = [name for name, _sql, _digest in migrations]
    if through not in names:
        raise ValueError(
            f"--through target is not an exact checked-in migration filename: {through}"
        )
    selected = [item for item in pending if item[0] <= through]
    held_back = [item for item in pending if item[0] > through]
    return selected, held_back


class AppliedMigrationLedgerError(ValueError):
    """The database ledger cannot be reconciled to immutable files in the tree."""


def validate_applied_ledger(
    migrations: list[tuple[str, str, str]], applied: dict[str, str]
) -> None:
    """Refuse missing, edited, or reordered ledger state.

    The effective applied set accounts for the exact historical rename aliases,
    then must be an uninterrupted prefix of the current migration tree.  A
    later applied row after any earlier hole is not a harmless partial deploy:
    applying the hole now would reorder history and may violate dependencies.
    """
    current = {name: digest for name, _sql, digest in migrations}
    missing = sorted(set(applied) - set(current))
    unknown_missing = [name for name in missing if name not in LEGACY_APPLIED_ALIASES]
    if unknown_missing:
        raise AppliedMigrationLedgerError(
            "applied migration(s) missing from the tree: " + ", ".join(unknown_missing)
            + ". Restore the exact files; do not rename or delete applied migrations."
        )
    absent_targets = sorted({
        LEGACY_APPLIED_ALIASES[name]
        for name in missing
        if LEGACY_APPLIED_ALIASES[name] not in current
    })
    if absent_targets:
        raise AppliedMigrationLedgerError(
            "legacy applied migration alias target(s) missing from the tree: "
            + ", ".join(absent_targets)
        )
    for name, digest in current.items():
        if name in applied and applied[name] != digest:
            raise AppliedMigrationLedgerError(
                f"{name} was EDITED after being applied (sha mismatch). Write a new "
                "migration instead; never rewrite an applied one."
            )
    effective_applied = set(applied) & set(current)
    effective_applied.update(
        LEGACY_APPLIED_ALIASES[name]
        for name in applied
        if name in LEGACY_APPLIED_ALIASES
    )
    first_hole: str | None = None
    later_applied: list[str] = []
    for name, _sql, _digest in migrations:
        if name not in effective_applied:
            first_hole = first_hole or name
        elif first_hole is not None:
            later_applied.append(name)
    if later_applied:
        raise AppliedMigrationLedgerError(
            "migration ledger is reordered: earlier migration is pending "
            f"({first_hole}) while later migration(s) are already applied: "
            + ", ".join(later_applied)
            + ". Stop; reconcile dependency history before applying anything."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="apply pending migrations")
    ap.add_argument("--yes", action="store_true", help="skip the host confirmation")
    ap.add_argument(
        "--through",
        metavar="FILENAME",
        help="consider only the forward migration prefix through this exact checked-in filename",
    )
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
            applied: dict[str, str] = dict(cur.fetchall())

        try:
            validate_applied_ledger(migrations, applied)
        except AppliedMigrationLedgerError as exc:
            fail(str(exc))

        all_pending = pending_migrations(migrations, applied)
        try:
            pending, held_back = migrations_through(
                migrations, all_pending, args.through
            )
        except ValueError as exc:
            fail(str(exc))
        print(f"host: {host}")
        print(f"applied: {len(applied)}   pending: {len(all_pending)}")
        if args.through:
            print(
                f"authorized prefix: through {args.through}   "
                f"selected: {len(pending)}   held back: {len(held_back)}"
            )
        for name, _s, _d in pending:
            print(f"  pending: {name}")
        for name, _s, _d in held_back:
            print(f"  held back: {name}")
        if not pending:
            print("nothing to do in authorized prefix")
            return
        if not args.apply:
            print("dry run — pass --apply to run these")
            return
        if not args.yes:
            # END THE READ TRANSACTION BEFORE ASKING A HUMAN ANYTHING.
            # Reading schema_migrations above opened a transaction (psycopg is
            # not autocommit), and the prompt below asks the operator to type a
            # ~58-character hostname. That leaves the session idle IN a
            # transaction for however long they take, and Neon enforces
            # idle_in_transaction_session_timeout. On 2026-08-07 the first
            # production run of 0074 died with IdleInTransactionSessionTimeout
            # on the very first `set local` after the confirmation, having
            # applied nothing. An idle session is fine; idle-in-transaction is
            # not. A rehearsal cannot catch this, because rehearsals pass --yes
            # and --yes is precisely the path that never waits.
            conn.rollback()
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
