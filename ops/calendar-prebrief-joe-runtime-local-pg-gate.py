#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance for the authority-managed Joe prebrief runtime."""
from __future__ import annotations

import os
import uuid

import psycopg

from gate_runtime_role import rollback_only_connection


def one(cur: psycopg.Cursor, sql: str, args: tuple[object, ...] = ()) -> tuple[object, ...]:
    cur.execute(sql, args)
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"calendar Joe runtime gate expected one row: {sql}")
    return tuple(row)


def refused(cur: psycopg.Cursor, sql: str, args: tuple[object, ...], text: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(sql, args)
    except psycopg.Error as exc:
        if text not in str(exc):
            raise
        cur.execute("rollback to savepoint expected_refusal")
        return
    raise RuntimeError(f"calendar Joe runtime gate accepted forbidden call: {text}")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("calendar Joe runtime gate requires DATABASE_URL")
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        cur.execute("""do $$ begin
          if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then
            create role carr_authority_joe login;
          end if;
          grant carr_authority to carr_authority_joe;
        end $$""")
        calendar_key = "9" * 64
        cur.execute("set session authorization carr_authority_joe")
        revision = one(
            cur,
            "select (ops.replace_calendar_prebrief_allowlist(%s)).id",
            ([calendar_key],),
        )[0]
        evidence = "8" * 64
        activation = one(cur, "select ops.activate_calendar_prebrief_joe_live(%s)", (evidence,))[0]
        receipt = one(
            cur,
            "select sponsor,app_evidence_digest,allowlist_revision_id from ops.read_calendar_prebrief_joe_activation(%s)",
            (activation,),
        )
        if receipt != ("joe", evidence, revision):
            raise RuntimeError("calendar Joe activation receipt did not read back exactly")
        cur.execute("reset session authorization")
        if one(cur, "select enabled from ops.job_definition where key='calendar-prebrief-projection-joe-daily' and version=1") != (True,):
            raise RuntimeError("calendar Joe activation did not enable its exact definition")

        queued = uuid.uuid4()
        cur.execute(
            """insert into ops.job
                 (id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,
                  attempt,max_attempts,next_attempt_at,timeout_seconds)
               values (%s,'calendar-prebrief-projection-joe-daily',1,%s,now()+interval '1 second','live','queued',0,2,now(),300)""",
            (queued, str(queued)),
        )
        cur.execute("set session authorization carr_jobs")
        generic = cur.execute(
            "select job_id from ops.claim_job('generic-runtime-gate',100,300)"
        ).fetchall()
        generic_mode = cur.execute(
            "select job_id from ops.claim_job_mode('generic-mode-runtime-gate','live',100,300)"
        ).fetchall()
        if queued in {row[0] for row in (*generic, *generic_mode)}:
            raise RuntimeError("generic worker stole the dedicated Joe prebrief job")
        if one(cur, "select state from ops.job where id=%s", (queued,)) != ("queued",):
            raise RuntimeError("generic worker changed the dedicated Joe prebrief job")
        claimed = one(cur, "select job_id,lease from ops.claim_calendar_prebrief_joe_live_job('runtime-gate',300)")
        if claimed[0] != queued or not isinstance(claimed[1], uuid.UUID):
            raise RuntimeError("calendar Joe narrow claim did not return its exact job")
        refused(cur, "select ops.activate_calendar_prebrief_joe_live(%s)", (evidence,), "permission denied")
        cur.execute("reset session authorization")

        # A new allowlist revision immediately fences even already queued work.
        second = uuid.uuid4()
        cur.execute(
            """insert into ops.job
                 (id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,
                  attempt,max_attempts,next_attempt_at,timeout_seconds)
               values (%s,'calendar-prebrief-projection-joe-daily',1,%s,now(),'live','queued',0,2,now(),300)""",
            (second, str(second)),
        )
        cur.execute("set session authorization carr_authority_joe")
        one(cur, "select (ops.replace_calendar_prebrief_allowlist(%s)).id", ([calendar_key, "7" * 64],))
        cur.execute("reset session authorization")
        cur.execute("set session authorization carr_jobs")
        refused(
            cur,
            "select * from ops.claim_calendar_prebrief_joe_live_job('runtime-gate',300)",
            (),
            "activation gate refused",
        )
        cur.execute("reset session authorization")
        refused(
            cur,
            "update ops.calendar_prebrief_runtime_activation_receipt set app_evidence_digest=%s where id=%s",
            ("6" * 64, activation),
            "append-only",
        )
    print("calendar prebrief Joe runtime local acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
