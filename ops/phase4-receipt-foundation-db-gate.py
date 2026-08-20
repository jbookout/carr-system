#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only managed-owner acceptance for the Phase 4 receipt foundation."""
from __future__ import annotations

import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))
from gate_runtime_role import (  # noqa: E402
    grant_settable_runtime_roles,
    rollback_only_connection,
    set_local_role,
)


def one(cur: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"expected one row from {query}")
    return row[0]


def refusal(cur: Any, query: str, params: tuple[Any, ...], label: str) -> None:
    cur.execute("savepoint phase4_refusal")
    try:
        cur.execute(query, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint phase4_refusal")
        return
    cur.execute("rollback to savepoint phase4_refusal")
    raise RuntimeError(f"{label} was accepted")


def job_receipt(cur: Any, key: str, actor: str, evidence: dict[str, Any]) -> uuid.UUID:
    contract = Jsonb({"entrypoint": f"fixture:{key}"})
    cur.execute(
        """insert into ops.job_definition
          (key,version,risk,owner_actor,execution_kind,execution_contract,recurrence,
           retry_policy,deduplication,completion_contract)
        values (%s,1,'green',%s,'deterministic',%s,'{}'::jsonb,
                '{"max_attempts":1,"timeout_seconds":60,"base_seconds":1,"cap_seconds":1,"backoff":"constant"}'::jsonb,
                '{}'::jsonb,'{}'::jsonb)""",
        (key, actor, contract),
    )
    job_id = one(
        cur,
        """insert into ops.job
          (definition_key,definition_version,idempotency_key,scheduled_for,state,max_attempts,
           timeout_seconds,ended_at)
        values (%s,1,%s,now(),'succeeded',1,60,now()) returning id""",
        (key, f"phase4-db-gate-job:{uuid.uuid4()}"),
    )
    return one(
        cur,
        """insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
        values (%s,1,'completion',%s,%s) returning id""",
        (job_id, f"phase4-db-gate-receipt:{uuid.uuid4()}", Jsonb(evidence)),
    )


def writer_one(cur: Any, query: str, params: tuple[Any, ...]) -> Any:
    set_local_role(cur, "carr_writer")
    try:
        return one(cur, query, params)
    finally:
        cur.execute("reset role")


def _concurrent_source_call(
    dsn: str, source_id: uuid.UUID, key: str, barrier: threading.Barrier
) -> tuple[str, str]:
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            barrier.wait(timeout=10)
            row = cur.execute(
                "select ops.record_phase4_standing_context(%s,%s)", (source_id, key)
            ).fetchone()
            if row is None:
                return ("error", "missing receipt row")
            return ("ok", str(row[0]))
    except Exception as exc:
        return ("error", str(exc))


def disposable_concurrency_proof(dsn: str) -> None:
    """Commit concurrency fixtures only in the explicitly disposable CI DB.

    The managed-owner behavioural gate above always rolls back.  Real races
    need independent committed transactions, so this extension is enabled only
    when CI itself supplied this exact database as CARR_CI_DATABASE_URL.  The
    disposable cluster/service is destroyed after the migration class.
    """
    if os.environ.get("CARR_CI_DATABASE_URL", "") != dsn:
        return
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if one(cur, "select pg_has_role(session_user,'carr_writer','member')") is not True:
            raise RuntimeError("disposable concurrency owner cannot exercise carr_writer receipt functions")
        joe_id = one(cur, "select id from actor where slug='joe'")
        sources = [
            one(
                cur,
                """insert into tool_read_call
                  (verb,actor_slug,actor_id,ok,via,sponsoring_human_slug,personal_scope)
                values ('standing-context','joe',%s,true,'phase4-concurrency','joe','joe-personal')
                returning id""",
                (joe_id,),
            )
            for _ in range(2)
        ]

    exact_key = f"phase4-concurrent-exact:{uuid.uuid4()}"
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        exact = list(
            pool.map(
                lambda source: _concurrent_source_call(dsn, source, exact_key, barrier),
                (sources[0], sources[0]),
            )
        )
    if [state for state, _ in exact] != ["ok", "ok"] or len({value for _, value in exact}) != 1:
        raise RuntimeError(f"exact concurrent replay did not converge on one receipt: {exact}")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if one(cur, "select count(*) from ops.phase4_source_receipt where idempotency_key=%s", (exact_key,)) != 1:
            raise RuntimeError("exact concurrent replay persisted other than one receipt")

    mismatch_key = f"phase4-concurrent-mismatch:{uuid.uuid4()}"
    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        mismatched = list(
            pool.map(
                lambda source: _concurrent_source_call(dsn, source, mismatch_key, barrier), sources
            )
        )
    states = sorted(state for state, _ in mismatched)
    errors = [message.lower() for state, message in mismatched if state == "error"]
    if states != ["error", "ok"] or len(errors) != 1 or "idempotency mismatch" not in errors[0]:
        raise RuntimeError(f"mutated concurrent same-key call did not fail as a semantic mismatch: {mismatched}")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if one(cur, "select count(*) from ops.phase4_source_receipt where idempotency_key=%s", (mismatch_key,)) != 1:
            raise RuntimeError("mutated concurrent same-key race persisted other than one receipt")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("phase4-receipt-foundation-db-gate: DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(
                cur, "carr_writer", "carr_reader", "carr_jobs", "carr_device_evidence", "carr_authority"
            )
            database_actor = one(cur, "select session_user")
            joe_id = one(cur, "select id from actor where slug='joe'")

            # The managed owner is a rollback-only device fixture.  This keeps
            # the gate portable to hosted PostgreSQL, where SET SESSION
            # AUTHORIZATION is unavailable to an owner.
            cur.execute(
                """insert into ops.device_evidence_principal(login_role,device_id)
                values (%s,%s)""",
                (database_actor, "phase4-db-gate-dell-device"),
            )
            cur.execute(
                """insert into ops.phase4_device_partner_binding
                  (login_role,device_id,actor_slug,tenant_id)
                values (%s,%s,'dell','carr-internal')""",
                (database_actor, "phase4-db-gate-dell-device"),
            )

            standing_call = one(
                cur,
                """insert into tool_read_call
                  (verb,actor_slug,actor_id,ok,via,sponsoring_human_slug,personal_scope)
                values ('standing-context','joe',%s,true,'phase4-db-gate','joe','joe-personal')
                returning id""",
                (joe_id,),
            )
            retrieval_call = one(
                cur,
                """insert into tool_read_call
                  (verb,actor_slug,actor_id,ok,via,sponsoring_human_slug,personal_scope)
                values ('search-doctrine','joe',%s,true,'phase4-db-gate','joe','joe-personal')
                returning id""",
                (joe_id,),
            )
            policy = f"phase4-db-gate:{uuid.uuid4()}"
            cur.execute(
                """insert into retrieval_ranking_policy
                  (policy_id,formula,config,golden_suite_digest,status,is_default)
                values (%s,'weighted_sum','{}'::jsonb,%s,'candidate',false)""",
                (policy, "a" * 64),
            )
            query_log = one(
                cur,
                """insert into retrieval_query_log
                  (normalized_hash,result_count,score_bands,selected_row_ids,policy_id,
                   policy_version,explicit_hit,created_at,phase4_tool_read_call_id,
                   phase4_actor_slug,phase4_tenant_id)
                values (%s,1,'{"top":1}'::jsonb,array[%s::uuid],%s,1,true,now(),%s,'joe','carr-internal')
                returning id""",
                ("b" * 64, uuid.uuid4(), policy, retrieval_call),
            )

            tool_key = f"phase4-db-gate-tool:{uuid.uuid4()}"
            cur.execute(
                """insert into tool_call(idempotency_key,verb,actor_id,request_hash,response)
                values (%s,'add-loop',%s,%s,'{"status":"proposed","readback":"loop:fixture"}'::jsonb)""",
                (tool_key, joe_id, "c" * 64),
            )
            readback_event = one(
                cur,
                """insert into event
                  (occurred_at,actor_id,verb,subject_type,subject_id,cause,idempotency_key)
                values (now(),%s,'add-loop','loop',%s,'human_stated',%s) returning id""",
                (joe_id, uuid.uuid4(), tool_key),
            )

            proposals: list[uuid.UUID] = []
            events: list[uuid.UUID] = []
            for index, status in enumerate(("rejected", "approved"), start=1):
                proposal_id = one(
                    cur,
                    """insert into retrieval_proposal
                      (proposal_type,payload,reason,proposer_id,status,reviewer_id,idempotency_key,reviewed_at)
                    values ('concept','{}'::jsonb,%s,%s,%s,%s,%s,now()) returning id""",
                    (f"phase4 conflict proposal {index}", joe_id, status, joe_id, uuid.uuid4()),
                )
                proposals.append(proposal_id)
                events.append(
                    one(
                        cur,
                        """insert into event
                          (occurred_at,actor_id,verb,subject_type,subject_id,cause,idempotency_key)
                        values (now(),%s,%s,'retrieval_proposal',%s,'human_correction',%s) returning id""",
                        (joe_id, f"phase4-conflict-{index}", proposal_id, f"phase4-conflict-event:{uuid.uuid4()}"),
                    )
                )

            privacy_job_receipt = job_receipt(
                cur,
                "phase4-personal-canary-scan",
                "joe",
                {"privacy_scan": True, "model_output_scan": True, "telemetry_scan": True},
            )
            attachment_id = one(
                cur,
                """insert into attachment
                  (subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by)
                values ('deal',%s,%s,'fixture.pdf','application/pdf',%s,64,%s) returning id""",
                (uuid.uuid4(), f"phase4-db-gate/{uuid.uuid4()}", "d" * 64, joe_id),
            )
            document_job_receipt = job_receipt(
                cur,
                "phase4-document-download",
                "joe",
                {
                    "attachment_id": str(attachment_id),
                    "fetched_bytes_sha256": "d" * 64,
                    "download_audit": True,
                },
            )

            receipt_ids = [
                writer_one(
                    cur,
                    "select ops.record_phase4_standing_context(%s,%s)",
                    (standing_call, f"phase4-source:{uuid.uuid4()}"),
                ),
                writer_one(
                    cur,
                    "select ops.record_phase4_governed_retrieval(%s,%s,%s)",
                    (retrieval_call, query_log, f"phase4-source:{uuid.uuid4()}"),
                ),
                writer_one(
                    cur,
                    "select ops.record_phase4_tentative_write_readback(%s,%s,%s)",
                    (tool_key, readback_event, f"phase4-source:{uuid.uuid4()}"),
                ),
                writer_one(
                    cur,
                    "select ops.record_phase4_conflict_undo(%s,%s,%s,%s,%s)",
                    (*proposals, *events, f"phase4-source:{uuid.uuid4()}"),
                ),
                writer_one(
                    cur,
                    "select ops.record_phase4_privacy_scan(%s,%s)",
                    (privacy_job_receipt, f"phase4-source:{uuid.uuid4()}"),
                ),
                writer_one(
                    cur,
                    "select ops.record_phase4_document_download(%s,%s,%s)",
                    (document_job_receipt, attachment_id, f"phase4-source:{uuid.uuid4()}"),
                ),
            ]
            streams = one(cur, "select count(distinct stream) from ops.phase4_source_receipt")
            if streams != 6 or len(set(receipt_ids)) != 6:
                raise RuntimeError("the six typed source streams were not minted independently")

            receiver_key = f"phase4-receiver:{uuid.uuid4()}"
            set_local_role(cur, "carr_device_evidence")
            receiver_id = one(
                cur,
                "select ops.receive_phase4_source_receipt(%s,%s)",
                (receipt_ids[0], receiver_key),
            )
            replay_id = one(
                cur,
                "select ops.receive_phase4_source_receipt(%s,%s)",
                (receipt_ids[0], receiver_key),
            )
            if replay_id != receiver_id:
                raise RuntimeError("exact device replay did not return the same immutable receipt")
            refusal(
                cur,
                "select ops.receive_phase4_source_receipt(%s,%s)",
                (receipt_ids[1], receiver_key),
                "receiver idempotency mismatch",
            )
            refusal(
                cur,
                "select ops.record_phase4_standing_context(%s,%s)",
                (standing_call, f"phase4-device-forgery:{uuid.uuid4()}"),
                "device source-evidence mint",
            )
            cur.execute("reset role")
            received = cur.execute(
                """select r.source_actor_slug,r.receiver_actor_slug,r.tenant_id,r.device_id,
                          r.received_at<=clock_timestamp(),s.source_session_id<>r.receiver_session_id
                     from ops.phase4_receiver_receipt r
                     join ops.phase4_source_receipt s on s.id=r.source_receipt_id
                    where r.id=%s""",
                (receiver_id,),
            ).fetchone()
            if received != (
                "joe",
                "dell",
                "carr-internal",
                "phase4-db-gate-dell-device",
                True,
                True,
            ):
                raise RuntimeError(f"receiver lost derived cross-partner/session/tenant bindings: {received}")

            drive_specs = (
                ("inventory", "phase4-drive-inventory", {"inventory_complete": True, "unclassified": 0}),
                (
                    "repoint",
                    "phase4-drive-repoint",
                    {"readers_repointed": True, "writers_repointed": True},
                ),
                ("recovery", "phase4-drive-recovery", {"recovery_verified": True}),
                ("cutover", "phase4-drive-cutover", {"legacy_drive_disabled": True}),
            )
            drive_ids: list[uuid.UUID] = []
            for kind, definition, evidence in drive_specs:
                source = job_receipt(cur, definition, "system", evidence)
                drive_ids.append(
                    writer_one(
                        cur,
                        f"select ops.record_phase4_drive_{kind}(%s,%s)",
                        (source, f"phase4-drive:{uuid.uuid4()}"),
                    )
                )
            if one(cur, "select count(*) from ops.phase4_drive_evidence_receipt") != 4:
                raise RuntimeError("the four typed Drive evidence receipts were not preserved")
            known_retirement_key = f"phase4-known-retirement:{uuid.uuid4()}"
            cur.execute(
                """insert into ops.phase4_drive_retirement_authority_receipt
                  (receipt_ref,tenant_id,approved_by,inventory_receipt_id,repoint_receipt_id,
                   recovery_receipt_id,cutover_receipt_id,idempotency_key)
                values (%s,'carr-internal','joe',%s,%s,%s,%s,%s)""",
                (f"phase4-known-retirement:{uuid.uuid4()}", *drive_ids, known_retirement_key),
            )
            set_local_role(cur, "carr_authority")
            refusal(
                cur,
                "select ops.approve_phase4_drive_retirement(%s,%s,%s,%s,%s)",
                (*drive_ids, f"phase4-retirement:{uuid.uuid4()}"),
                "unmapped managed-owner Drive retirement authority",
            )
            refusal(
                cur,
                "select ops.approve_phase4_drive_retirement(%s,%s,%s,%s,%s)",
                (*drive_ids, known_retirement_key),
                "non-Joe known-key retirement replay",
            )
            cur.execute("reset role")

            # Fixed read bundles have only the tenant-filtered functions, not
            # direct receipt-table access.  The managed-owner session is not
            # impersonated as a reader; positive login identity is a deployed
            # credential probe, not disposable-owner evidence.
            privileges = cur.execute(
                """select
                  has_table_privilege('carr_reader','ops.phase4_source_receipt','select'),
                  has_table_privilege('carr_jobs','ops.phase4_source_receipt','select'),
                  has_function_privilege('carr_reader','ops.phase4_receipt_rows()'::regprocedure,'execute'),
                  has_function_privilege('carr_jobs','ops.phase4_receipt_rows()'::regprocedure,'execute'),
                  has_function_privilege('carr_device_evidence','ops.record_phase4_privacy_scan(uuid,text)'::regprocedure,'execute')"""
            ).fetchone()
            if privileges != (False, False, True, True, False):
                raise RuntimeError(f"least-privilege read/device ACL drift: {privileges}")
            authority_contract = cur.execute(
                """select sole_required_system_authority,dell_participation,
                          continuity_may_gate_system_rollout,continuity_may_gate_system_activation
                     from ops.phase4_system_authority_contract
                    where contract_key='phase4_optional_continuity_v1'"""
            ).fetchone()
            if authority_contract != ("joe", "optional_nonblocking", False, False):
                raise RuntimeError(f"Phase 4 authority guard drifted: {authority_contract}")
            external_dependencies = one(
                cur,
                """select count(*)
                     from pg_constraint c
                     join pg_class child on child.oid=c.conrelid
                     join pg_namespace child_ns on child_ns.oid=child.relnamespace
                     join pg_class parent on parent.oid=c.confrelid
                     join pg_namespace parent_ns on parent_ns.oid=parent.relnamespace
                    where c.contype='f' and parent_ns.nspname='ops'
                      and parent.relname like 'phase4_%%'
                      and not (child_ns.nspname='public' and child.relname='retrieval_query_log')
                      and not (child_ns.nspname='ops' and child.relname like 'phase4_%%')""",
            )
            if external_dependencies != 0:
                raise RuntimeError("a non-Phase4 system table depends on optional Phase4 evidence")
            retirement_definition = str(
                one(
                    cur,
                    "select pg_get_functiondef('ops.approve_phase4_drive_retirement(uuid,uuid,uuid,uuid,text)'::regprocedure)",
                )
            ).lower()
            if "authority_actor_slug()" not in retirement_definition or "<>'joe'" not in retirement_definition:
                raise RuntimeError("whole-Drive authority is not independently Joe-only")
            if "receiver_receipt" in retirement_definition or "dell" in retirement_definition:
                raise RuntimeError("Joe Drive authority incorrectly depends on Dell/continuity evidence")
            read_definition = str(
                one(cur, "select pg_get_functiondef('ops.phase4_receipt_rows()'::regprocedure)")
            ).lower().replace(" ", "")
            if "login_role=session_user" not in read_definition or "s.tenant_id=tenant" not in read_definition:
                raise RuntimeError("receipt projection does not filter by the session-bound tenant in SQL")
            refusal(
                cur,
                "update ops.phase4_source_receipt set evidence_sha256=%s where id=%s",
                ("e" * 64, receipt_ids[0]),
                "source receipt rewrite",
            )
        disposable_concurrency_proof(dsn)
    except Exception as exc:
        raise SystemExit(f"phase4-receipt-foundation-db-gate: FAIL — {exc}") from exc
    print(
        "phase4-receipt-foundation-db-gate: PASS — six source types, server-bound cross-partner receipt, "
        "four typed Drive predecessors, concurrent exact/mutated replay, refusal/ACL/append-only and "
        "Joe-before-replay/nonblocking-Dell guard exercised; "
        "no reducer or authority success claimed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
