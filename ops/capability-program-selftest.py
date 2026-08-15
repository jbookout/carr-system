#!/usr/bin/env python3
"""Acceptance checks for the one ordered AI capability portfolio.

Tier 1 runs in every CI job without credentials. Tier 2 activates only when
DATABASE_URL is already supplied (normally through db-tap against isolated
staging) and rolls every mutation back.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = REPO / "migrations" / "0125_ai_capability_program.sql"
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def tier1() -> None:
    print("TIER 1 — static program and authority contract")
    sql = MIGRATION.read_text(encoding="utf-8")
    rows = re.findall(
        r"\(\s*(\d+)\s*,\s*'carr-ai-engineering-suite-v1'\s*,\s*'(WR-AI-\d+)'\s*,\s*'([^']+)'\s*,\s*'(build|extend|adopt|decline)'\s*,",
        sql,
    )
    check("exactly 51 canonical Work Requests are seeded", len(rows) == 51, str(len(rows)))
    check("ordinals are contiguous 1..51", [int(r[0]) for r in rows] == list(range(1, 52)))
    check("refs are contiguous and stable", [r[1] for r in rows] == [f"WR-AI-{n:03d}" for n in range(1, 52)])
    check("the current projection cannot skip an unfinished predecessor",
          "p.program_ordinal < w.program_ordinal" in sql and "p.state <> 'confirmed_closed'" in sql)
    check("scheduled jobs receive read but not mutation grants",
          "grant select on ops.v_capability_program_next to carr_reader, carr_writer, carr_jobs" in sql
          and "grant update" not in sql.lower().split("carr_jobs")[-1])
    required_context = ["scope", "non_goals", "prerequisites", "first_deliverable",
                        "rollback_exit", "data_risk", "effort", "completion_definition"]
    check("every row includes a session-complete context shape",
          all(sql.count(f'"{key}"') >= 51 for key in required_context),
          ", ".join(f'{k}={sql.count(chr(34) + k + chr(34))}' for k in required_context))


def tier2() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("TIER 2 — SKIP (DATABASE_URL not set; run through staging db-tap)")
        return
    print("TIER 2 — live isolated schema, all writes rolled back")
    try:
        import psycopg
    except ImportError as exc:
        check("psycopg is installed", False, str(exc))
        return

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*), min(program_ordinal), max(program_ordinal) from ops.work_request where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            summary = cur.fetchone()
            assert summary is not None
            count, first, last = summary
            check("store contains the complete ordered program", (count, first, last) == (51, 1, 51), str((count, first, last)))

            cur.execute("select program_ordinal, ref from ops.v_capability_program_next where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            check("project 1 is the sole initial head", cur.fetchall() == [(1, "WR-AI-001")])

            cur.execute("select has_table_privilege('carr_jobs','ops.v_capability_program_next','SELECT'), has_table_privilege('carr_jobs','ops.work_request','UPDATE')")
            privileges = cur.fetchone()
            check("scheduled role can read the projection but cannot update Work Requests",
                  privileges == (True, False), str(privileges))

            cur.execute("select count(*) from ops.work_request where program_key=%s and not (project_context ?& array['scope','non_goals','prerequisites','first_deliverable','rollback_exit','data_risk','effort','completion_definition'])",
                        ("carr-ai-engineering-suite-v1",))
            incomplete = cur.fetchone()
            assert incomplete is not None
            check("all stored project contexts are build-complete", incomplete[0] == 0)

            # Prove the derived handoff with a rolled-back close. The server verb
            # adds stronger transition/evidence checks; this proves the database
            # view itself advances exactly one position and nothing persists.
            cur.execute("savepoint queue_handoff")
            cur.execute("""
                update ops.work_request
                   set state='confirmed_closed', verification_accepted_at=now(),
                       verification_evidence_ref='selftest:verifier', closed_at=now(),
                       completion_kind='extended', completion_evidence='{"selftest":true}'::jsonb
                 where program_key=%s and program_ordinal=1
            """, ("carr-ai-engineering-suite-v1",))
            cur.execute("select program_ordinal from ops.v_capability_program_next where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            check("a verified close exposes exactly project 2", cur.fetchall() == [(2,)])
            cur.execute("rollback to savepoint queue_handoff")
            cur.execute("select program_ordinal from ops.v_capability_program_next where program_key=%s",
                        ("carr-ai-engineering-suite-v1",))
            check("rollback restores project 1 as head", cur.fetchall() == [(1,)])
        conn.rollback()


if __name__ == "__main__":
    tier1()
    tier2()
    if FAILED:
        print(f"\nFAILED: {len(FAILED)}")
        sys.exit(1)
    print("\nPASS: capability program contract")
