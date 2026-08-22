#!/usr/bin/env python3
# ci: db-gate
"""Disposable adversarial proof for immutable renewal morning freshness."""
from __future__ import annotations

import os
import hashlib
import json
import uuid
from datetime import timedelta
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


def source_job(cur: psycopg.Cursor[Any], lease: uuid.UUID, offset: str = "0 seconds") -> uuid.UUID:
    job_id = uuid.uuid4()
    cur.execute(
        """insert into ops.job
             (id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds)
           values (%s,'renewal-radar-source-daily',1,%s,now()+(%s)::interval,'live','running',1,1,now(),'renewal-delivery-gate',%s,now()+interval '5 minutes',60)""",
        (job_id, str(job_id), offset, lease),
    )
    return job_id


def source_row(name: str, tier: str, flag: str, suffix: str) -> dict[str, object]:
    return {
        "source_key": f"renewal-gate|{suffix}|{name}", "name": name,
        "org_name": name, "vertical": "Physician practice", "address": "123 Sensitive Way",
        "city": "Mobile", "county": "Mobile", "state": "AL", "email": "private@example.test",
        "phone": "555-0100", "segment": "LEASE EVENT — decision window",
        "source_row": {"tier": tier, "flag": flag, "opaque": "do-not-leak"},
        "est_lease_event": "2027-04-01", "est_basis": "signed fixture",
    }


def ingest(cur: psycopg.Cursor[Any], job_id: uuid.UUID, lease: uuid.UUID, rows: list[dict[str, object]], snapshot: uuid.UUID | None = None) -> uuid.UUID:
    snapshot = snapshot or uuid.uuid4()
    payload_digest = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return one(cur, """select source_run_id from ops.ingest_renewal_signed_snapshot
                      (%s,%s,%s,'fixture-provider',%s,now(),%s,%s,%s::jsonb)""",
               (job_id, lease, snapshot, "a" * 64, payload_digest, "c" * 64, Jsonb(rows)))[0]


def set_attestor(cur: psycopg.Cursor[Any]) -> None:
    cur.execute("set session authorization carr_renewal_source_attestor")
    if one(cur, "select session_user,current_user") != ("carr_renewal_source_attestor", "carr_renewal_source_attestor"):
        fail("renewal source gate could not assume its dedicated attestor identity")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        fail("DATABASE_URL is required")
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        cur.execute("""do $$ begin
          if not exists (select 1 from pg_roles where rolname='carr_renewal_source_attestor') then
            create role carr_renewal_source_attestor login;
          end if;
          grant carr_renewal_source_attestors to carr_renewal_source_attestor;
        end $$""")
        for relation in ("public.v_renewal_decision_queue", "public.v_renewal_decision_queue_status"):
            if one(cur, "select to_regclass(%s)", (relation,))[0] is None:
                fail(f"missing {relation}")
        columns = {row[0] for row in cur.execute("select column_name from information_schema.columns where table_schema='public' and table_name='v_renewal_decision_queue'").fetchall()}
        if columns & FORBIDDEN_COLUMNS:
            fail(f"safe queue exposes forbidden columns: {sorted(columns & FORBIDDEN_COLUMNS)}")
        required = {"display_name", "est_lease_event", "tier_status", "flag_status", "has_channel", "decision_count", "source_observed_at", "freshness_state"}
        if not required.issubset(columns):
            fail(f"safe queue misses required fields: {sorted(required - columns)}")
        for role, relation in (("carr_reader", "public.v_renewal_decision_queue"),
                               ("carr_reader", "public.v_renewal_decision_queue_status"),
                               ("carr_reader", "public.candidate_pool"), ("carr_reader", "public.v_export_pool"),
                               ("carr_reader", "ops.renewal_decision_source_run"), ("carr_jobs", "ops.renewal_decision_source_run")):
            actual = one(cur, "select has_table_privilege(%s,%s,'select')", (role, relation))[0]
            if actual != relation.startswith("public.v_renewal_decision"):
                fail(f"unexpected {role} select boundary on {relation}")

        suffix = uuid.uuid4().hex
        fixtures = [
            source_row(f"Renewal T1 {suffix}", "T1", "", suffix),
            source_row(f"Renewal Empty T1 {suffix}", "T1", "not yet tenant-identified", suffix),
            source_row(f"Renewal T2 {suffix}", "T2", "", suffix),
        ]

        grant_settable_runtime_roles(cur, "carr_jobs", "carr_reader")
        set_local_role(cur, "carr_reader")
        if one(cur, "select freshness_state from v_renewal_decision_queue_status")[0] != "unavailable":
            fail("missing signed source run looked fresh")
        cur.execute("reset role")

        # The generic job identity has neither the function grant nor a
        # substitute session identity; only the dedicated attestor may ingest.
        lease = uuid.uuid4(); job_id = source_job(cur, lease)
        set_local_role(cur, "carr_jobs")
        cur.execute("savepoint jobs_ingress_refusal")
        try:
            ingest(cur, job_id, lease, fixtures)
        except psycopg.Error as exc:
            if exc.sqlstate != "42501":
                raise
            cur.execute("rollback to savepoint jobs_ingress_refusal")
        else:
            fail("general carr_jobs identity could ingest a signed renewal source")
        cur.execute("reset role")
        set_attestor(cur)
        snapshot = uuid.uuid4()
        run_id = ingest(cur, job_id, lease, fixtures, snapshot)
        replay_id = ingest(cur, job_id, lease, fixtures, snapshot)
        if replay_id != run_id:
            fail("exact source-run replay did not converge on its immutable receipt")
        cur.execute("reset session authorization")
        # The source-run transaction fence remains held until this disposable
        # gate rolls back; a competing source seal cannot interleave membership.
        with psycopg.connect(dsn) as peer, peer.cursor() as peer_cur:
            if one(peer_cur, "select pg_try_advisory_xact_lock(hashtextextended('renewal-decision-source-run',0))")[0] is not False:
                fail("renewal source-run advisory race fence is not held")
            peer.rollback()

        set_local_role(cur, "carr_reader")
        names = [str(row["name"]) for row in fixtures]
        rows = cur.execute("select display_name,tier_status,flag_status,has_channel,freshness_state from v_renewal_decision_queue where display_name=any(%s::text[]) order by display_name", (names,)).fetchall()
        if len(rows) != 2 or {row[1] for row in rows} != {"t1"} or {row[2] for row in rows} != {"clear", "building_signal"} or not all(row[3] and row[4] == "ready" for row in rows):
            fail(f"sealed queue lost its safe T1 decision facts: {rows!r}")
        if one(cur, "select freshness_state from v_renewal_decision_queue_status")[0] != "ready":
            fail("sealed nonempty source did not become ready")
        cur.execute("reset role")

        # A post-seal mutable cache addition is not source evidence and cannot
        # make the signed run unavailable or leak its own row into the queue.
        actor = one(cur, "select id from actor where active order by slug limit 1")[0]
        cur.execute(
            """insert into candidate_pool(source,source_key,source_row,name,city,state,status,created_by,updated_by)
               values ('renewal-radar',%s,%s,%s,'Mobile','AL','pool',%s,%s)""",
            (uuid.uuid4().hex, Jsonb({"tier": "T1", "flag": ""}), f"Renewal Added After Seal {suffix}", actor, actor),
        )
        set_local_role(cur, "carr_reader")
        if one(cur, "select freshness_state from v_renewal_decision_queue_status")[0] != "ready" or cur.execute("select count(*) from v_renewal_decision_queue where display_name=%s", (f"Renewal Added After Seal {suffix}",)).fetchone()[0] != 0:
            fail("unsigned cache addition altered or entered the signed renewal queue")
        cur.execute("reset role")

        # A signed member edit invalidates the snapshot rather than relabeling
        # a changed projection as current.
        cur.execute("update candidate_pool set city='Changed After Seal',updated_by=%s where name=%s", (actor, names[0]))
        set_local_role(cur, "carr_reader")
        if one(cur, "select freshness_state from v_renewal_decision_queue_status")[0] != "unavailable" or cur.execute("select * from v_renewal_decision_queue").fetchall():
            fail("post-seal row edit looked fresh or left safe rows visible")
        cur.execute("reset role")

        stale_lease = uuid.uuid4(); stale_job = source_job(cur, stale_lease, "-37 hours")
        set_attestor(cur)
        cur.execute("savepoint stale_source_job")
        try:
            ingest(cur, stale_job, stale_lease, [])
        except psycopg.Error as exc:
            if exc.sqlstate != "22023":
                raise
            cur.execute("rollback to savepoint stale_source_job")
        else:
            fail("stale source job was accepted")
        cur.execute("reset session authorization")

        # A separate, sealed source with no T1 rows is an explicit fresh empty;
        # it is not inferred from a missing or edited queue.
        cur.execute("update candidate_pool set source='renewal-radar-gate-shadow',updated_by=%s where source='renewal-radar'", (actor,))
        fresh_lease = uuid.uuid4(); fresh_job = source_job(cur, fresh_lease, "1 second")
        set_attestor(cur)
        fresh_run = ingest(cur, fresh_job, fresh_lease, [])
        if fresh_run == run_id:
            fail("new source job reused an old immutable receipt")
        cur.execute("reset session authorization")
        set_local_role(cur, "carr_reader")
        # Both seals deliberately occur in this one rollback-only transaction,
        # where PostgreSQL's now() is identical. The current source must follow
        # its DB-owned scheduled snapshot time, never UUID tie-breaking.
        if one(cur, "select freshness_state from v_renewal_decision_queue_status")[0] != "empty" or cur.execute("select * from v_renewal_decision_queue").fetchall():
            fail("sealed true-zero source was not explicit empty")
        cur.execute("reset role")

        serialized = repr(rows)
        if any(secret in serialized for secret in ("private@example.test", "555-0100", "Sensitive Way", "do-not-leak")):
            fail("reader response leaked a fixture secret")
    print("renewal decision delivery gate: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"renewal decision delivery gate: FAIL — {exc}", file=__import__("sys").stderr)
        raise SystemExit(1) from exc
