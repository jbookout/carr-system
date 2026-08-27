#!/usr/bin/env python3
"""One-time repair for the isolated staging project's genuine 0382 hole.

Staging records 0383 while 0382 is absent, and its ops.standing_guidance
function still has the exact pre-0382 invoker shape.  The ordinary forward-only
migrator correctly refuses that reordered history.  This tool repairs only that
marker-proven state: it verifies the immutable 0382 bytes against Production's
recorded digest, applies those bytes, verifies the narrow reader boundary, and
then records the truthful ledger row.

If execution is interrupted after the migration's own COMMIT but before the
ledger insert, an exact repaired boundary is the only accepted recovery state;
the next invocation records the row after re-verifying every marker.

Usage through the isolated staging door:
  .venv/bin/python tools/db-tap.py --project staging run \
      tools/staging-ledger-repair-0382.py
  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --project staging \
      --reason "repair genuine staging 0382 hole" run \
      tools/staging-ledger-repair-0382.py --apply
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

import psycopg


REPO = Path(__file__).resolve().parent.parent
MIGRATION_NAME = "0382_standing_guidance_reader_boundary.sql"
MIGRATION = REPO / "migrations" / MIGRATION_NAME
EXPECTED_SHA256 = "a6ffe5f29e9224f263b0c6a90c414b4828915a5ed3265e52e8fadbe31ef8c2bc"
LATER_NAME = "0383_control_plane_not_configured_state.sql"
LATER_SHA256 = "f0cb86f97fcd87db8412be1f4c36544fe40f1ba9e524182bb3cb3b9ad3148bfa"


def fail(message: str) -> NoReturn:
    sys.exit(f"staging-ledger-repair-0382: {message}")


def classify_state(applied: dict[str, str], boundary: str) -> str:
    """Return the sole safe action for marker-proven ledger/function state."""
    if MIGRATION_NAME in applied:
        if applied[MIGRATION_NAME] != EXPECTED_SHA256:
            raise ValueError("0382 ledger digest does not match immutable migration")
        return "already_recorded"
    if applied.get(LATER_NAME) != LATER_SHA256:
        raise ValueError("exact later 0383 ledger marker is absent or mismatched")
    if boundary == "legacy":
        return "apply_and_record"
    if boundary == "repaired":
        return "record_verified_recovery"
    raise ValueError("standing_guidance is neither the exact legacy nor repaired boundary")


def boundary_state(cur: Any) -> str:
    cur.execute(
        """select p.prosecdef,
                  coalesce(array_to_string(p.proconfig, ','), ''),
                  pg_catalog.pg_get_functiondef(p.oid),
                  has_function_privilege('carr_reader', p.oid, 'EXECUTE'),
                  has_function_privilege('carr_writer', p.oid, 'EXECUTE'),
                  has_table_privilege('carr_reader', 'public.rule', 'SELECT')
             from pg_catalog.pg_proc p
             join pg_catalog.pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'ops'
              and p.proname = 'standing_guidance'
              and pg_catalog.pg_get_function_identity_arguments(p.oid) =
                  'p_actor text, p_workflow text, p_surface text, p_tier text'"""
    )
    rows = cur.fetchall()
    if len(rows) != 1:
        return "unknown"
    security_definer, config, definition, reader_execute, writer_execute, reader_rule = rows[0]
    common = reader_execute and writer_execute and not reader_rule
    if (
        not security_definer
        and config == ""
        and common
        and "join rule r" in definition
        and "join actor teacher" in definition
    ):
        return "legacy"
    if (
        security_definer
        and "search_path=pg_catalog, ops, public, pg_temp" in config
        and common
        and "join public.rule r" in definition
        and "join public.actor teacher" in definition
        and "left join public.actor owner" in definition
    ):
        return "repaired"
    return "unknown"


def main() -> None:
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL")
    if not url:
        fail("DATABASE_URL is not set")

    sql_text = MIGRATION.read_text()
    digest = hashlib.sha256(sql_text.encode()).hexdigest()
    if digest != EXPECTED_SHA256:
        fail(f"immutable 0382 digest mismatch: got {digest}, expected {EXPECTED_SHA256}")

    with psycopg.connect(url) as conn:
        cur = conn.cursor()
        print("host:", conn.info.host)
        cur.execute(
            "select filename, sha256 from public.schema_migrations "
            "where filename in (%s, %s)",
            (MIGRATION_NAME, LATER_NAME),
        )
        applied: dict[str, str] = dict(cur.fetchall())
        boundary = boundary_state(cur)
        try:
            action = classify_state(applied, boundary)
        except ValueError as exc:
            fail(f"marker refusal: {exc}")

        if action == "already_recorded":
            print("0382 is already recorded with the immutable digest — nothing to do, ever.")
            return
        print(f"exact staging skew confirmed: action={action}")
        if not apply:
            print("dry run — pass --apply")
            return

        if action == "apply_and_record":
            conn.rollback()
            cur.execute(sql_text)
            if boundary_state(cur) != "repaired":
                fail("post-apply boundary verification failed; refusing ledger record")
            print("applied exact 0382 bytes; repaired boundary verified")

        if boundary_state(cur) != "repaired":
            fail("repaired boundary changed before ledger record; refusing")
        cur.execute(
            "insert into public.schema_migrations (filename, sha256) values (%s, %s)",
            (MIGRATION_NAME, EXPECTED_SHA256),
        )
        conn.commit()

        cur.execute(
            "select sha256 from public.schema_migrations where filename=%s",
            (MIGRATION_NAME,),
        )
        row = cur.fetchone()
        if row != (EXPECTED_SHA256,) or boundary_state(cur) != "repaired":
            fail("post-commit ledger/boundary verification failed")
        print(f"committed and verified — {MIGRATION_NAME} ({EXPECTED_SHA256[:12]}…)")


if __name__ == "__main__":
    main()
