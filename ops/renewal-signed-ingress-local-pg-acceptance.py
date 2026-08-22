#!/usr/bin/env python3
"""Disposable-PostgreSQL proof of the renewal signed-ingress capability."""
from __future__ import annotations

import json
import os
import uuid
from typing import Any
from urllib.parse import urlparse

import psycopg


def row(cur: psycopg.Cursor[Any], label: str) -> tuple[Any, ...]:
    value = cur.fetchone()
    if value is None:
        raise RuntimeError(f"{label} returned no row")
    return tuple(value)


def refused(cur: psycopg.Cursor[Any], statement: str, args: tuple[Any, ...], state: str, text: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(statement, args)
    except psycopg.Error as exc:
        if exc.sqlstate != state or text not in str(exc):
            raise
        cur.execute("rollback to savepoint expected_refusal")
        return
    raise RuntimeError("expected refusal was accepted")


dsn = os.environ.get("CARR_LOCAL_PG_DSN", "")
parsed = urlparse(dsn)
if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("renewal signed ingress acceptance requires loopback CARR_LOCAL_PG_DSN")

source_row = {
    "source_key": "renewal-acceptance|100-main-st",
    "name": "Renewal Acceptance Practice",
    "org_name": "Renewal Acceptance Practice",
    "vertical": "Physician practice",
    "address": "100 Main St",
    "city": "Pensacola",
    "county": "Escambia",
    "state": "FL",
    "email": "renewal.acceptance@example.test",
    "phone": "555-0100",
    "segment": "LEASE EVENT — decision window",
    "source_row": {"tier": "T1 (window <12mo)", "flag": ""},
    "est_lease_event": "2027-04-01",
    "est_basis": "local acceptance",
}

with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    job, lease, snapshot = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cur.execute("""do $$ begin
      if not exists (select 1 from pg_roles where rolname='carr_renewal_source_attestor') then
        create role carr_renewal_source_attestor login;
      end if;
    end $$""")
    cur.execute("grant carr_renewal_source_attestors to carr_renewal_source_attestor")
    cur.execute("select id from actor where slug='system'")
    system_id = row(cur, "system actor")[0]
    # A historical cache extra must not become a member of this current signed run.
    cur.execute("""insert into candidate_pool(source,source_key,source_seq,source_row,name,status,created_by,updated_by)
                   values('renewal-radar','historical-cache-extra',1,'{}','Historical cache extra','pool',%s,%s)""",
                (system_id, system_id))
    cur.execute("""insert into ops.job(id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,
                                        attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds)
                   values(%s,'renewal-radar-source-daily',1,%s,now(),'live','running',1,2,now(),'local',%s,
                          now()+interval '5 minutes',300)""", (job, str(job), lease))
    cur.execute("select now()")
    observed_at = row(cur, "DB observed timestamp")[0]
    args = (job, lease, snapshot, "fixture-provider", "a" * 64, observed_at, "b" * 64, "c" * 64, json.dumps([source_row]))
    cur.execute("set session authorization carr_jobs")
    refused(cur, "select * from ops.ingest_renewal_signed_snapshot(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            args, "42501", "permission denied for function")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_renewal_source_attestor")
    cur.execute("""select source_run_id,row_count from ops.ingest_renewal_signed_snapshot
                   (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""", args)
    source_run_id, count = row(cur, "first signed ingress")
    if count != 1:
        raise RuntimeError("signed ingress did not persist exactly its source row")
    cur.execute("""select source_run_id,row_count from ops.ingest_renewal_signed_snapshot
                   (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""", args)
    if row(cur, "exact signed ingress replay") != (source_run_id, 1):
        raise RuntimeError("exact signed ingress replay did not return its immutable source run")
    refused(cur, "select * from candidate_pool", (), "42501", "permission denied")
    refused(cur, "insert into ops.renewal_source_snapshot(id,job_id,attempt,provider,key_fingerprint,source_observed_at,payload_sha256,signature_sha256,row_count) values(%s,%s,1,'x',%s,now(),%s,%s,0)",
            (uuid.uuid4(), job, "a" * 64, "b" * 64, "c" * 64), "42501", "permission denied")
    refused(cur, "select * from ops.ingest_renewal_signed_snapshot(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (job, uuid.uuid4(), snapshot, "fixture-provider", "a" * 64, observed_at, "b" * 64, "c" * 64, json.dumps([source_row])),
            "55000", "current live job lease")
    oversized = dict(source_row)
    oversized["name"] = "x" * 513
    refused(cur, "select * from ops.ingest_renewal_signed_snapshot(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (job, lease, uuid.uuid4(), "fixture-provider", "a" * 64, observed_at, "b" * 64, "c" * 64, json.dumps([oversized])),
            "22023", "source row shape is malformed")
    cur.execute("reset session authorization")
    cur.execute("select member_count,source_snapshot_id from ops.renewal_decision_source_run where id=%s", (source_run_id,))
    if row(cur, "sealed source run") != (1, snapshot):
        raise RuntimeError("signed source run incorporated a historical cache row")
    cur.execute("select count(*) from ops.renewal_decision_source_run_member where source_run_id=%s", (source_run_id,))
    if row(cur, "sealed member count") != (1,):
        raise RuntimeError("signed source run has an unexpected member count")
    cur.execute("insert into ops.renewal_decision_source_run(job_id,attempt,snapshot_at,member_count,source_snapshot_id) values(%s,2,now(),0,null)", (job,))
    status_columns = {value[0] for value in cur.execute(
        "select column_name from information_schema.columns "
        "where table_schema='public' and table_name='v_renewal_decision_queue_status'"
    ).fetchall()}
    if "owner_slug" in status_columns:
        # 0279 keeps signed market-source ingress as disabled historical
        # machinery, but the live renewal reader is CARR's executed-lease
        # ledger. A signed candidate snapshot must not enter that reader.
        cur.execute("select count(*) from v_renewal_decision_queue where display_name=%s", (source_row["name"],))
        if row(cur, "signed market row excluded from lease ledger") != (0,):
            raise RuntimeError("signed market row entered the CARR lease renewal reader")
        cur.execute("select count(*) from v_renewal_decision_queue_status where freshness_state='empty'")
        if row(cur, "empty lease-ledger partner statuses") != (2,):
            raise RuntimeError("signed market ingress changed lease-ledger readiness")
    else:
        cur.execute("select freshness_state from v_renewal_decision_queue_status")
        if row(cur, "legacy unsigned run is unavailable")[0] != "ready":
            raise RuntimeError("signed queue status was displaced by a legacy unsigned run")
    conn.rollback()

print("renewal signed ingress local acceptance passed")
