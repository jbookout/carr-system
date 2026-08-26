#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only proof that carr_jobs can claim one exact Engineering slice.

The fixture uses the real SECURITY DEFINER claim and controller-binding
functions but rolls back every row, temporary role membership, lease, and
attempt. It protects against a PL/pgSQL output-variable ambiguity that is only
visible when PostgreSQL executes the claim CTE.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


RUNTIME_ROLE = "carr_jobs"


def fail(message: str) -> int:
    print(f"engineering-claim-local-pg-acceptance: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):  # pragma: no cover - DB-only gate
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError("required fixture row was not returned")
    return row


def sha(char: str) -> str:
    return "sha256:" + char * 64


def fixture(cur):
    token = uuid.uuid4().hex
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    codex_id = one(cur, "select id from actor where slug='codex' and active and kind='automation'")[0]
    document_id = one(
        cur,
        """insert into doctrine_document(slug,title,content_class,visibility,created_by)
             values (%s,'Engineering claim fixture','reference','shared',%s) returning id""",
        (f"engineering-claim-{token}", joe_id),
    )[0]
    section_id = one(
        cur,
        """insert into doctrine_section(document_id,section_key,title,ordinal,status,current_version)
             values (%s,'fixture','Engineering claim fixture',1,'active',1) returning id""",
        (document_id,),
    )[0]
    revision_id = one(
        cur,
        """insert into doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash,commit_message)
             values (%s,1,%s,%s,'Engineering claim fixture',%s,'fixture') returning id""",
        (section_id, joe_id, Jsonb({"text": "Engineering claim fixture"}), "a" * 64),
    )[0]
    work_request_id = one(
        cur,
        """insert into ops.work_request(ref,state,title,requester_actor,owner_actor)
             values (%s,'captured','Engineering claim fixture','joe','joe') returning id""",
        (f"WR-ENGINEERING-CLAIM-{token}",),
    )[0]
    plan_id = one(
        cur,
        """insert into ops.sourced_work_request_plan
             (work_request_id,plan_version,idempotency_key,work_request_version,preimage,
              scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,
              dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
             values (%s,1,%s,1,%s,'fixture scope','doctrine:runbook#fixture',%s,%s,%s,
                     %s,'safe:recovery:fixture','safe:observability:fixture',%s,%s,%s)
             returning id""",
        (work_request_id, uuid.uuid4(), Jsonb({}), section_id, revision_id, "b" * 64,
         Jsonb([]), Jsonb({}), sha("c"), f"PLAN-{token[:12]}-v1"),
    )[0]
    plan_digest = sha("d")
    slice_ref = "slice:claim-fixture"
    slice_plan_id = one(
        cur,
        """insert into ops.engineering_slice_plan
             (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
             values (%s,%s,%s,1,%s,%s,%s) returning id""",
        (work_request_id, plan_id, sha("c"), plan_digest,
         Jsonb({"plan_digest": plan_digest, "slices": [{"slice_ref": slice_ref}]}), uuid.uuid4()),
    )[0]
    session_id = one(
        cur,
        """insert into ops.capability_agent_session
             (work_request_id,executor_actor_id,created_by_actor_id,source_commit_sha,worktree_ref,scope_ref)
             values (%s,%s,%s,%s,'fixture-worktree','slice:claim-fixture') returning id""",
        (work_request_id, codex_id, joe_id, "e" * 40),
    )[0]
    job_id = one(
        cur,
        """insert into ops.job
             (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode,payload)
             values ('engineering-slice',1,%s,now()-interval '1 second',2,300,'shadow',%s)
             returning id""",
        (f"engineering-claim:{token}", Jsonb({"work_request": f"WR-ENGINEERING-CLAIM-{token}",
                                                   "slice_ref": slice_ref, "plan_digest": plan_digest})),
    )[0]
    envelope_digest = sha("f")
    envelope_id = one(
        cur,
        """insert into ops.engineering_execution_envelope
             (job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
              state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
             values (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,now()-interval '1 minute',now()+interval '1 hour')
             returning id""",
        (job_id, work_request_id, plan_id, slice_plan_id, slice_ref, session_id, sha("9"), envelope_digest,
         Jsonb({"work_request_id": f"wr:{work_request_id}",
                "state_binding": {"state_version": 1, "canonical_record_digest": sha("9")}})),
    )[0]
    return job_id, envelope_id, session_id, codex_id, plan_digest, slice_ref, envelope_digest


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, RUNTIME_ROLE)
            job_id, envelope_id, session_id, codex_id, plan_digest, slice_ref, envelope_digest = fixture(cur)
            set_local_role(cur, RUNTIME_ROLE)
            if one(cur, "select has_table_privilege(current_user,'ops.job','UPDATE')")[0]:
                return fail("carr_jobs gained direct UPDATE on ops.job")
            if one(cur, "select has_table_privilege(current_user,'ops.job_attempt','INSERT')")[0]:
                return fail("carr_jobs gained direct INSERT on ops.job_attempt")
            if not one(cur, "select has_function_privilege(current_user,'ops.engineering_claim_slice(text,integer,integer)'::regprocedure,'EXECUTE')")[0]:
                return fail("carr_jobs cannot execute the scoped Engineering claim")
            if not one(cur, "select has_function_privilege(current_user,'ops.engineering_controller_binding(uuid,uuid)'::regprocedure,'EXECUTE')")[0]:
                return fail("carr_jobs cannot read the scoped controller binding")

            claimed = one(
                cur,
                "select job_id,lease_token,definition_key,attempt from ops.engineering_claim_slice(%s,1,300)",
                ("engineering-claim-local-acceptance",),
            )
            if claimed[0] != job_id or not isinstance(claimed[1], uuid.UUID) or claimed[2:] != ("engineering-slice", 1):
                return fail("claim did not return the exact eligible Engineering job and lease")
            if one(cur, "select state,attempt,lease_owner,lease_token=%s,leased_until>now() from ops.job where id=%s", (claimed[1], job_id)) != (
                    "running", 1, "engineering-claim-local-acceptance", True, True):
                return fail("claim did not persist one exact running job lease")
            if one(cur, "select count(*),min(state),min(lease_owner),bool_and(lease_token=%s) from ops.job_attempt where job_id=%s", (claimed[1], job_id)) != (
                    1, "running", "engineering-claim-local-acceptance", True):
                return fail("claim did not persist one exact running attempt")
            binding = one(cur, "select ops.engineering_controller_binding(%s,%s)", (envelope_id, job_id))[0]
            if binding != {"envelope_id": str(envelope_id), "envelope_digest": envelope_digest,
                           "slice_ref": slice_ref, "plan_digest": plan_digest,
                           "slice_plan": {"plan_digest": plan_digest, "slices": [{"slice_ref": slice_ref}]},
                           "executor_actor": {"id": str(codex_id), "slug": "codex"},
                           "agent_session_id": str(session_id)}:
                return fail("controller binding did not remain exact after claim")
    except Exception as exc:  # noqa: BLE001 - DB gates report exact refusal details
        return fail(str(exc))
    print("engineering claim local acceptance passed: scoped lease, attempt, and binding are exact and rollback-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
