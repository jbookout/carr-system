#!/usr/bin/env python3
# ci: db-gate
# doctrine: doctorcre-v5-design-basis
"""Rollback-only proof that morning-brief's assurance_cadence section is
actually readable on the carr_reader connection.

WHY THIS EXISTS. Opus adversarial review of PR #1236 round 1: morning-brief
(mcp-server/src/tools.js) read ops.notification/ops.notification_read
directly, on the reader connection, with no carr_reader grant at all. Every
existing unit test for morning-brief runs against a fake client whose stub
returns `{rows: []}` for any matching query regardless of role -- it cannot
catch a missing grant, because it never talks to a real Postgres or a real
role. This gate does: it connects as an unprivileged carr_reader-scoped
role and calls ops.v5_a05_assurance_cadence_batch (0592) exactly as
morning-brief's assurance_cadence section does, then asserts the SAME role
is refused direct SELECT on the two underlying tables.
"""
from __future__ import annotations

import os
import uuid

import psycopg

from gate_runtime_role import rollback_only_connection


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        raise RuntimeError("v5-a05 assurance-cadence reader gate requires DATABASE_URL")
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        cur.execute("""do $$ begin
          if not exists(select 1 from pg_roles where rolname='carr_reader_a05_probe') then
            create role carr_reader_a05_probe login;
          end if;
          grant carr_reader to carr_reader_a05_probe;
        end $$""")

        tenant = cur.execute("select ops.completion_runtime_tenant()").fetchone()[0]
        actor_row = cur.execute(
            "select id from actor where slug='joe' and active limit 1"
        ).fetchone()
        if actor_row is None:
            raise RuntimeError("v5-a05 assurance-cadence reader gate expected a seeded 'joe' actor")
        actor_id = actor_row[0]

        # Seed one signal_event + notification the batch function should return,
        # as carr_writer/carr_authority (the real minting path), before probing
        # as the reader.
        event_id = cur.execute(
            """insert into signal_event(subject_type, subject_ref, signal_kind, producer, payload)
               values ('engineering_program','doctorcre-v5','cadence_miss_replan_required',
                       'v5-a05-delivery-cadence','{}'::jsonb)
               returning id"""
        ).fetchone()[0]
        notification_id = cur.execute(
            """insert into ops.notification
                 (subject_type, subject_ref, event_ref, event_source, recipient_actor,
                  reason, severity, deep_link, dedupe_key, correlation_id)
               values ('engineering_program','doctorcre-v5', %s, 'signal_event', %s,
                       'cadence_miss_replan_required', 'urgent', null,
                       %s, null)
               returning id""",
            (event_id, actor_id, str(uuid.uuid4())),
        ).fetchone()[0]

        cur.execute("set session authorization carr_reader_a05_probe")
        try:
            batch = cur.execute(
                "select ops.v5_a05_assurance_cadence_batch('joe')"
            ).fetchone()[0]
            if not isinstance(batch, list) or len(batch) != 1:
                raise RuntimeError(
                    f"v5-a05 assurance-cadence reader gate: expected one seeded row, got {batch!r}"
                )
            if str(batch[0].get("notification_id")) != str(notification_id):
                raise RuntimeError(
                    "v5-a05 assurance-cadence reader gate: batch did not return the seeded notification"
                )

            # The same reader-scoped role must still be refused DIRECT table
            # access -- the function is the door, not a new blanket grant.
            cur.execute("savepoint expect_refusal")
            try:
                cur.execute("select 1 from ops.notification limit 1")
            except psycopg.errors.InsufficientPrivilege:
                cur.execute("rollback to savepoint expect_refusal")
            else:
                raise RuntimeError(
                    "v5-a05 assurance-cadence reader gate: carr_reader can read "
                    "ops.notification directly -- the door is a blanket grant, not the function"
                )

            cur.execute("savepoint expect_refusal2")
            try:
                cur.execute("select 1 from ops.notification_read limit 1")
            except psycopg.errors.InsufficientPrivilege:
                cur.execute("rollback to savepoint expect_refusal2")
            else:
                raise RuntimeError(
                    "v5-a05 assurance-cadence reader gate: carr_reader can read "
                    "ops.notification_read directly -- the door is a blanket grant, not the function"
                )
        finally:
            cur.execute("reset session authorization")

    print("delivery-cadence-a05-assurance-reader-local-pg-gate: PASS -- morning-brief's "
          "reader connection sees the seeded escalation through "
          "ops.v5_a05_assurance_cadence_batch and is refused direct table access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
