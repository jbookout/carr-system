#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate: the capability queue can actually be closed.

THE DEFECT THIS EXISTS FOR. On 2026-08-21 the CARR AI Engineering Suite read
0 complete of 51 while six of its projects were demonstrably finished. Nothing
was wrong with the work. `complete-capability-project` looked up the independent
attestation with `select ... for update` against ops.capability_verification —
a table that migration 0127 made immutable on purpose and that carr_writer holds
only `insert, select` on. PostgreSQL requires UPDATE privilege to take a row
lock, so every completion attempt raised permission denied and surfaced to the
caller as a bare `internal error`. The close verb had never once run to the end.

WHY NOTHING CAUGHT IT. Migration 0127 ships its own proof block, and that proof
passes — because it runs as the migration role, which owns the table. The bug
lives entirely in the gap between the role that builds the schema and the role
that serves traffic. So this gate asserts as carr_writer, the role the Worker
actually connects as (rule a9ecd5b4: a success signal must be derived from the
thing being checked, not from a friendlier neighbour of it).

WHAT IT PINS, and why each half matters:
  1. carr_writer can run the completion lookup. If someone reintroduces a row
     lock on this table, the whole queue silently stops closing again.
  2. carr_writer still CANNOT update this table. That is the append-only
     evidence contract. Granting UPDATE would make the lock work and would be
     the wrong repair: it widens the write surface of an attestation table to
     buy a lock that requireCurrent() and loadSession() already provide on rows
     this role may lock (rule 5409731b — grant-check every table a change
     touches). If a later migration grants it, this gate fails and forces the
     conversation rather than letting the guarantee erode quietly.

Writes nothing: every statement runs inside one transaction that is rolled back.
"""

from __future__ import annotations

import os
import pathlib
import sys
from typing import Any

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gate_runtime_role import (  # noqa: E402
    grant_settable_runtime_roles,
    set_local_role,
)

RUNTIME_ROLE = "carr_writer"
EVIDENCE_TABLE = "ops.capability_verification"

# The exact shape complete-capability-project issues. Kept literal rather than
# imported so that a change to the handler does not silently change the gate too.
COMPLETION_LOOKUP = (
    "select * from ops.capability_verification "
    "where build_session_id=%s and work_request_id=%s and outcome='pass' "
    "and candidate_fingerprint=%s and verifier_actor_id <> %s "
    "order by attested_at desc limit 1"
)
NEVER_MATCHES = (
    "00000000-0000-0000-0000-000000000000",
    "00000000-0000-0000-0000-000000000000",
    "0" * 32,
    "00000000-0000-0000-0000-000000000000",
)


def fail(message: str) -> int:
    print(f"capability-completion-gate: FAIL — {message}", file=sys.stderr)
    return 1


def scalar(cur: Any, query: str, label: str, params: tuple[Any, ...] = ()) -> Any:
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"{label} returned no row")
    return row[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")

    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            # The immutability contract this gate defends must actually be in
            # place; asserting privileges against a table with no trigger would
            # be checking the wrong thing.
            trigger_present = scalar(
                cur,
                "select count(*) from pg_trigger t join pg_class c on c.oid=t.tgrelid "
                "join pg_namespace n on n.oid=c.relnamespace "
                "where n.nspname='ops' and c.relname='capability_verification' "
                "and not t.tgisinternal",
                "attestation immutability trigger",
            )
            if int(trigger_present) < 1:
                return fail(
                    f"{EVIDENCE_TABLE} carries no immutability trigger; migration 0127's "
                    "append-only guarantee is missing, so this gate is checking a "
                    "contract that no longer exists"
                )

            grant_settable_runtime_roles(cur, RUNTIME_ROLE)
            set_local_role(cur, RUNTIME_ROLE)

            # 1. The lookup the close path depends on must be runnable by the
            #    serving role. Zero rows is the expected and correct result for
            #    these never-matching identifiers; we are asserting that it
            #    RETURNS rather than raising.
            try:
                cur.execute(COMPLETION_LOOKUP, NEVER_MATCHES).fetchall()
            except psycopg.errors.InsufficientPrivilege as exc:
                return fail(
                    f"{RUNTIME_ROLE} cannot run the completion attestation lookup: {exc}. "
                    "This is the 0-of-51 defect: every complete-capability-project call "
                    "raises permission denied and reports 'internal error'."
                )

            # 2. And the same role must still be unable to lock or mutate the
            #    attestation row. A passing lock here would mean UPDATE was
            #    granted on an append-only evidence table.
            may_update = scalar(
                cur,
                "select has_table_privilege(current_user, %s, 'UPDATE')",
                "writer UPDATE privilege on the attestation table",
                (EVIDENCE_TABLE,),
            )
            if may_update:
                return fail(
                    f"{RUNTIME_ROLE} now holds UPDATE on {EVIDENCE_TABLE}. Attestations are "
                    "append-only by design (migration 0127). If a row lock was wanted on "
                    "the completion path, take it on the work_request and session rows, "
                    "which requireCurrent() and loadSession() already lock — do not widen "
                    "this table."
                )

            try:
                cur.execute(COMPLETION_LOOKUP + " for update", NEVER_MATCHES).fetchall()
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                return fail(
                    f"{RUNTIME_ROLE} was able to take a row lock on {EVIDENCE_TABLE}; the "
                    "append-only contract has been weakened somewhere other than the "
                    "UPDATE grant"
                )
    except Exception as exc:  # noqa: BLE001 - the gate reports, never raises
        return fail(str(exc))
    finally:
        conn.rollback()
        conn.close()

    print(
        "capability-completion-gate passed: the serving role can read the completion "
        "attestation and still cannot lock or mutate it"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
