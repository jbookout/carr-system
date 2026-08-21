#!/usr/bin/env python3
# ci: db-gate
"""Disposable PostgreSQL proof for the safe renewal decision reader.

The fixture deliberately puts email, phone, address, opaque source JSON, and a
candidate UUID in the base row.  The reader view must make the T1 decision
state usable without making any of those fields reachable.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


FORBIDDEN_COLUMNS = {"id", "pool_id", "source_key", "source_row", "email", "phone", "address"}


def one(cur: psycopg.Cursor[Any], sql: str, args: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    cur.execute(sql, args)
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"expected one row: {sql}")
    return tuple(row)


def fail(message: str) -> None:
    raise RuntimeError(f"renewal decision delivery gate: {message}")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        fail("DATABASE_URL is required")
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        for relation in ("public.v_renewal_decision_queue", "public.v_renewal_decision_queue_status"):
            if one(cur, "select to_regclass(%s)", (relation,))[0] is None:
                fail(f"missing {relation}")
        columns = {
            row[0] for row in cur.execute(
                """select column_name from information_schema.columns
                     where table_schema='public' and table_name='v_renewal_decision_queue'"""
            ).fetchall()
        }
        leaked = sorted(columns & FORBIDDEN_COLUMNS)
        if leaked:
            fail(f"safe queue exposes forbidden columns: {', '.join(leaked)}")
        required = {"display_name", "est_lease_event", "tier_status", "flag_status", "has_channel",
                    "decision_count", "source_observed_at", "freshness_state"}
        if not required.issubset(columns):
            fail(f"safe queue is missing required decision/freshness fields: {sorted(required - columns)}")
        if not one(cur, "select has_table_privilege('carr_reader', 'public.v_renewal_decision_queue', 'select')")[0]:
            fail("carr_reader cannot read the safe renewal queue")
        if not one(cur, "select has_table_privilege('carr_reader', 'public.v_renewal_decision_queue_status', 'select')")[0]:
            fail("carr_reader cannot read the safe renewal status")
        for role, relation in (("carr_reader", "public.candidate_pool"),
                               ("carr_reader", "public.v_export_pool"),
                               ("carr_exporter", "public.v_renewal_decision_queue"),
                               ("carr_jobs", "public.v_renewal_decision_queue")):
            if one(cur, "select has_table_privilege(%s, %s, 'select')", (role, relation))[0]:
                fail(f"{role} unexpectedly reads {relation}")

        actor = one(cur, "select id from actor where active order by slug limit 1")[0]
        suffix = uuid.uuid4().hex
        fixture_names = [f"Renewal Safe {suffix}", f"Renewal Building {suffix}", f"Renewal T2 {suffix}"]
        for name, tier, flag in ((fixture_names[0], "T1", ""),
                                 (fixture_names[1], "T1", "not yet tenant-identified"),
                                 (fixture_names[2], "T2", "")):
            cur.execute(
                """insert into candidate_pool
                     (source,source_key,source_row,name,address,city,state,email,phone,status,created_by,updated_by)
                   values ('renewal-radar',%s,%s,%s,'123 Sensitive Way','Mobile','AL',
                           'private@example.test','555-0100','pool',%s,%s)""",
                (uuid.uuid4().hex, Jsonb({"tier": tier, "flag": flag, "opaque": "do-not-leak"}), name,
                 actor, actor),
            )

        grant_settable_runtime_roles(cur, "carr_reader")
        set_local_role(cur, "carr_reader")
        rows = cur.execute(
            """select display_name,tier_status,flag_status,has_channel,decision_count,freshness_state
                 from v_renewal_decision_queue
                where display_name = any(%s::text[])
                order by display_name""",
            (fixture_names,),
        ).fetchall()
        if len(rows) != 2:
            fail(f"T1 queue must show exactly its two fixture rows, not T2: {rows!r}")
        if {row[1] for row in rows} != {"t1"}:
            fail(f"queue did not normalize T1 state: {rows!r}")
        if {row[2] for row in rows} != {"clear", "building_signal"}:
            fail(f"queue leaked or lost flag status: {rows!r}")
        if not all(row[3] is True and row[4] >= 2 and row[5] == "fresh" for row in rows):
            fail(f"queue lost safe channel/count/freshness facts: {rows!r}")
        status = one(cur, "select t1_candidate_count,source_observed_at,freshness_state from v_renewal_decision_queue_status")
        if status[0] < 2 or status[1] is None or status[2] != "fresh":
            fail(f"status does not distinguish a fresh nonempty source: {status!r}")
        cur.execute("reset role")

        serialized = repr(rows) + repr(status)
        for forbidden in ("private@example.test", "555-0100", "Sensitive Way", "do-not-leak"):
            if forbidden in serialized:
                fail(f"reader response leaked fixture secret {forbidden!r}")
    print("renewal decision delivery gate: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"renewal decision delivery gate: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
