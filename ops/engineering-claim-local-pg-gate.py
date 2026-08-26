#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
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
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


RUNTIME_ROLE = "carr_jobs"


def fail(message: str) -> int:
    print(f"engineering-claim-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, query: str, params: tuple = ()):  # pragma: no cover - DB-only gate
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError("required fixture row was not returned")
    return row


def sha(char: str) -> str:
    return "sha256:" + char * 64


def fixture(cur, *, expired: bool = False, read_only: bool = False,
            session_state: str = "claimed", expires_in_seconds: float | None = None,
            packet_expiry_mode: str = "exact", executor_slug: str = "codex",
            executor_kind: str = "automation"):
    token = uuid.uuid4().hex
    work_ref = f"WR-ENGINEERING-CLAIM-{token}"
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    codex_id = one(cur, "select id from actor where slug=%s and active and kind=%s",
                   (executor_slug, executor_kind))[0]
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
        """insert into ops.work_request
             (ref,state,title,requester_actor,owner_actor,shape_disposition,
              shape_fixed_surface_ref,shape_rationale,shape_decided_by_actor_id,shape_decided_at)
             values (%s,'ready','Engineering claim fixture','joe','joe','not_required',
                     'fixture:engineering-claim','fixed Engineering Passport fixture',%s,now())
             returning id""",
        (work_ref, joe_id),
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
    one(
        cur,
        """insert into ops.sourced_work_request_plan_acceptance_receipt
             (work_request_id,plan_id,idempotency_key,base_version,plan_hash,
              accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
             values (%s,%s,%s,1,%s,%s,1,'fixture:engineering-claim','fixture acceptance')
             returning id""",
        (work_request_id, plan_id, uuid.uuid4(), sha("c"), joe_id),
    )
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
             (work_request_id,executor_actor_id,created_by_actor_id,
              source_commit_sha,worktree_ref,scope_ref)
             values (%s,%s,%s,%s,'fixture-worktree','slice:claim-fixture')
             returning id""",
        (work_request_id, codex_id, joe_id, "e" * 40),
    )[0]
    if session_state == "cancelled":
        cur.execute(
            "update ops.capability_agent_session set state='cancelled',cancelled_at=now(),version=version+1 where id=%s",
            (session_id,),
        )
    elif session_state == "completed":
        cur.execute(
            "update ops.capability_agent_session set state='in_progress',started_at=now(),version=version+1 where id=%s",
            (session_id,),
        )
        cur.execute(
            """update ops.capability_agent_session set state='verification',candidate_kind='built',
                      candidate_evidence=%s,prepared_at=now(),version=version+1 where id=%s""",
            (Jsonb({"artifact_ref": "fixture", "candidate_commit_sha": "f" * 40,
                    "acceptance_test_refs": ["fixture"]}), session_id),
        )
        cur.execute(
            "update ops.capability_agent_session set state='completed',completed_at=now(),version=version+1 where id=%s",
            (session_id,),
        )
    scheduled_at = (datetime.now(timezone.utc) - timedelta(
        seconds=1, microseconds=int(token[:6], 16) % 999999)).isoformat()
    job_id = one(
        cur,
        """insert into ops.job
             (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode,payload)
             values ('engineering-slice',1,%s,%s::timestamptz,2,300,'shadow',%s)
             returning id""",
        (f"engineering-claim:{token}", scheduled_at,
         Jsonb({"work_request": work_ref, "slice_ref": slice_ref,
                "plan_digest": plan_digest, "generation": 1})),
    )[0]
    envelope_digest = "sha256:" + token * 2
    source = one(cur, "select ops.engineering_admission_source(%s)", (work_ref,))[0]
    record_digest = source["work_request"]["canonical_record_digest"]
    clock = datetime.now(timezone.utc)
    issued_at = (clock - timedelta(hours=2 if expired else 0, minutes=1)).isoformat()
    expires_at = (clock - timedelta(hours=1) if expired else clock + timedelta(
        seconds=expires_in_seconds if expires_in_seconds is not None else 3600)).isoformat()
    envelope = {
        "work_request_id": f"wr:{work_request_id}",
        "expires_at": expires_at,
        "state_binding": {"state_version": 1, "canonical_record_digest": record_digest},
        "request": {"allowed_actions": [
            "repository:create-worktree", "repository:create-branch", "repository:write-declared-scope",
            "repository:run-checks", "repository:commit", "repository:push-branch", "repository:open-pr",
        ]},
        "server_binding": {
            "authority": {"read_only": read_only, "capability_profile": "capability:engineering-repository-write"},
            "adapter": {"surface": "codex_desktop"},
        },
    }
    if packet_expiry_mode == "missing":
        del envelope["expires_at"]
    elif packet_expiry_mode == "invalid":
        envelope["expires_at"] = "not-a-timestamp"
    elif packet_expiry_mode == "mismatch":
        envelope["expires_at"] = (clock + timedelta(hours=2)).isoformat()
    elif packet_expiry_mode != "exact":
        raise RuntimeError(f"unknown packet expiry fixture mode: {packet_expiry_mode}")
    envelope_id = one(
        cur,
        """insert into ops.engineering_execution_envelope
             (job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
              state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
             values (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s::timestamptz,%s::timestamptz)
             returning id""",
        (job_id, work_request_id, plan_id, slice_plan_id, slice_ref, session_id, record_digest, envelope_digest,
         Jsonb(envelope), issued_at, expires_at),
    )[0]
    return job_id, envelope_id, session_id, codex_id, plan_digest, slice_ref, envelope_digest


def supersede_fixture(cur, prior_job_id, prior_envelope_id):
    """Create the one permitted successor for an already expired fixture."""
    row = one(
        cur,
        """select e.work_request_id,e.accepted_plan_id,e.slice_plan_id,e.slice_ref,e.agent_session_id,
                  e.state_version,e.canonical_record_digest,e.envelope,j.payload,
                  s.executor_actor_id,s.created_by_actor_id
             from ops.engineering_execution_envelope e join ops.job j on j.id=e.job_id
             join ops.capability_agent_session s on s.id=e.agent_session_id
            where e.id=%s and e.job_id=%s""",
        (prior_envelope_id, prior_job_id),
    )
    payload = dict(row[8])
    payload["generation"] = int(payload["generation"]) + 1
    successor_job_id = one(
        cur,
        """insert into ops.job
             (definition_key,definition_version,idempotency_key,scheduled_for,max_attempts,timeout_seconds,mode,payload)
             values ('engineering-slice',1,%s,now()-interval '1 second',2,300,'shadow',%s)
             returning id""",
        (f"engineering-claim-successor:{uuid.uuid4().hex}", Jsonb(payload)),
    )[0]
    # The capability ledger permits one open session per Work Request. An
    # expired Passport is retired before its successor is issued, exactly as
    # the server-side replacement path does; the predecessor remains an
    # immutable, explicitly superseded envelope for the refusals below.
    cur.execute(
        "update ops.capability_agent_session set state='cancelled',cancelled_at=now() where id=%s",
        (row[4],),
    )
    successor_session_id = one(
        cur,
        """insert into ops.capability_agent_session
             (work_request_id,executor_actor_id,created_by_actor_id,state,source_commit_sha,worktree_ref,scope_ref)
             values (%s,%s,%s,'claimed',%s,'fixture-successor-worktree',%s) returning id""",
        (row[0], row[9], row[10], "e" * 40, row[3]),
    )[0]
    successor_envelope = dict(row[7])
    successor_envelope["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    successor_envelope_id = one(
        cur,
        """insert into ops.engineering_execution_envelope
             (job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
              state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at,
              supersedes_envelope_id,supersession_reason)
             values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s::timestamptz,%s,'fixture replacement')
             returning id""",
        (successor_job_id, row[0], row[1], row[2], row[3], successor_session_id, *row[5:7], "sha256:" + uuid.uuid4().hex * 2,
         Jsonb(successor_envelope), successor_envelope["expires_at"], prior_envelope_id),
    )[0]
    return successor_job_id, successor_envelope_id


def expect_receipt_refusal(cur, envelope_id, lease_token, actor_id, label: str) -> str:
    cur.execute("savepoint engineering_receipt_refusal")
    try:
        cur.execute(
            "select ops.engineering_record_slice_receipt(%s,%s,%s,%s,%s)",
            (envelope_id, lease_token, Jsonb({}), sha("7"), actor_id),
        )
    except psycopg.Error as exc:
        detail = str(exc)
        cur.execute("rollback to savepoint engineering_receipt_refusal")
        cur.execute("release savepoint engineering_receipt_refusal")
        if "engineering envelope is no longer executable" not in detail:
            raise RuntimeError(f"{label} returned the wrong refusal: {detail}") from exc
        return detail
    cur.execute("release savepoint engineering_receipt_refusal")
    raise RuntimeError(f"{label} persisted a receipt")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, RUNTIME_ROLE)
            job_id, envelope_id, session_id, codex_id, plan_digest, slice_ref, envelope_digest = fixture(
                cur, expires_in_seconds=2160)
            near_expiry_job_id, _, _, _, _, _, _ = fixture(cur, expires_in_seconds=90)
            superseded_job_id, superseded_envelope_id, _, _, _, _, _ = fixture(cur, expired=True)
            successor_job_id, successor_envelope_id = supersede_fixture(
                cur, superseded_job_id, superseded_envelope_id)
            invalid_jobs = [
                fixture(cur, read_only=True)[0],
                fixture(cur, session_state="cancelled")[0],
                fixture(cur, session_state="completed")[0],
                fixture(cur, packet_expiry_mode="missing")[0],
                fixture(cur, packet_expiry_mode="invalid")[0],
                fixture(cur, packet_expiry_mode="mismatch")[0],
                fixture(cur, executor_slug="joe", executor_kind="human")[0],
                superseded_job_id, near_expiry_job_id,
            ]
            post_claim_expired_job_id, post_claim_expired_envelope_id, _, _, _, _, _ = fixture(
                cur, expired=True)
            post_claim_expired_lease = uuid.uuid4()
            cur.execute(
                """update ops.job set state='running',attempt=1,lease_owner='expired-after-claim',
                          lease_token=%s,leased_until=now()+interval '5 minutes',started_at=now(),updated_at=now()
                     where id=%s""",
                (post_claim_expired_lease, post_claim_expired_job_id),
            )
            cur.execute(
                """insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
                     values (%s,1,'expired-after-claim',%s,'running')""",
                (post_claim_expired_job_id, post_claim_expired_lease),
            )
            terminal_job_id, terminal_envelope_id, terminal_session_id, _, _, _, _ = fixture(cur)
            if not one(cur, "select ops.engineering_envelope_is_executable(%s,%s)",
                       (successor_envelope_id, successor_job_id))[0]:
                return fail("fresh successor did not satisfy the shared executable-envelope predicate")
            # Every fixture is created in one transaction, so PostgreSQL gives
            # their default now() schedule the same timestamp. Make the claim
            # order explicit instead of turning a test into an unordered tie.
            cur.execute("update ops.job set scheduled_for=now()+interval '1 hour' where id=%s", (successor_job_id,))
            cur.execute("update ops.job set scheduled_for=now()+interval '2 hours' where id=%s", (terminal_job_id,))
            set_local_role(cur, RUNTIME_ROLE)
            if one(cur, "select has_table_privilege(current_user,'ops.job','UPDATE')")[0]:
                return fail("carr_jobs gained direct UPDATE on ops.job")
            if one(cur, "select has_table_privilege(current_user,'ops.job_attempt','INSERT')")[0]:
                return fail("carr_jobs gained direct INSERT on ops.job_attempt")
            if not one(cur, "select has_function_privilege(current_user,'ops.engineering_claim_slice(text,integer,integer)'::regprocedure,'EXECUTE')")[0]:
                return fail("carr_jobs cannot execute the scoped Engineering claim")
            if not one(cur, "select has_function_privilege(current_user,'ops.engineering_controller_binding(uuid,uuid)'::regprocedure,'EXECUTE')")[0]:
                return fail("carr_jobs cannot read the scoped controller binding")
            cur.execute("savepoint multi_claim_refusal")
            try:
                cur.execute("select * from ops.engineering_claim_slice(%s,2,1800)",
                            ("engineering-multi-claim-refusal",))
            except psycopg.Error as exc:
                detail = str(exc)
                cur.execute("rollback to savepoint multi_claim_refusal")
                cur.execute("release savepoint multi_claim_refusal")
                if "exactly one claim" not in detail:
                    raise RuntimeError(f"multi-claim refusal was not deterministic: {detail}") from exc
            else:
                raise RuntimeError("multi-candidate Engineering claim was accepted")

            claimed = one(
                cur,
                "select job_id,lease_token,definition_key,attempt from ops.engineering_claim_slice(%s,1,1800)",
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
            if one(cur, "select count(*) from ops.job where id=any(%s) and state='queued' and attempt=0", (invalid_jobs,))[0] != len(invalid_jobs):
                return fail("an expired, read-only, superseded or terminal-session envelope left the unclaimed queue state")
            if one(cur, "select count(*) from ops.job_attempt where job_id=any(%s)", (invalid_jobs,))[0] != 0:
                return fail("an ineligible Engineering envelope created an attempt")
            binding = one(cur, "select ops.engineering_controller_binding(%s,%s)", (envelope_id, job_id))[0]
            if binding != {"envelope_id": str(envelope_id), "envelope_digest": envelope_digest,
                           "slice_ref": slice_ref, "plan_digest": plan_digest,
                           "slice_plan": {"plan_digest": plan_digest, "slices": [{"slice_ref": slice_ref}]},
                           "executor_actor": {"id": str(codex_id), "slug": "codex"},
                           "agent_session_id": str(session_id)}:
                return fail("controller binding did not remain exact after claim")
            if one(cur, "select ops.engineering_controller_binding(%s,%s)",
                   (superseded_envelope_id, superseded_job_id))[0] is not None:
                return fail("a superseded envelope retained its controller binding")
            successor_claimed = one(
                cur,
                "select job_id,lease_token from ops.engineering_claim_slice(%s,1,300)",
                ("engineering-claim-local-successor",),
            )
            if successor_claimed[0] != successor_job_id or not isinstance(successor_claimed[1], uuid.UUID):
                return fail(f"the current successor was not claimed after its stale predecessor was refused: got {successor_claimed[0]}")
            cur.execute("reset role")
            expired_lease_job_id, expired_lease_envelope_id, _, expired_lease_actor_id, _, _, _ = fixture(cur)
            expired_lease_token = uuid.uuid4()
            cur.execute(
                """update ops.job set state='running',attempt=1,lease_owner='expired-lease',
                          lease_token=%s,leased_until=now()-interval '1 second',started_at=now(),updated_at=now()
                     where id=%s""",
                (expired_lease_token, expired_lease_job_id),
            )
            cur.execute(
                """insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
                     values (%s,1,'expired-lease',%s,'running')""",
                (expired_lease_job_id, expired_lease_token),
            )
            set_local_role(cur, RUNTIME_ROLE)
            expect_receipt_refusal(cur, superseded_envelope_id, uuid.uuid4(), codex_id,
                                   "superseded-envelope receipt")
            # Model a lease whose immutable envelope has since expired: both
            # binding and receipt persistence must repeat the shared predicate.
            if one(cur, "select ops.engineering_controller_binding(%s,%s)",
                   (post_claim_expired_envelope_id, post_claim_expired_job_id))[0] is not None:
                return fail("an envelope that expired after claim retained its controller binding")
            expect_receipt_refusal(cur, post_claim_expired_envelope_id, post_claim_expired_lease, codex_id,
                                   "expiry-after-claim receipt")
            if one(cur, "select state,lease_token is not null,leased_until<statement_timestamp() from ops.job where id=%s",
                   (expired_lease_job_id,)) != ("running", True, True):
                return fail("expired-lease fixture did not persist an expired running lease")
            if one(cur, "select ops.engineering_controller_binding(%s,%s)",
                   (expired_lease_envelope_id, expired_lease_job_id))[0] is not None:
                return fail("an expired lease retained its controller binding")
            expect_receipt_refusal(cur, expired_lease_envelope_id, expired_lease_token,
                                   expired_lease_actor_id, "expired-lease receipt")
            terminal_claimed = one(
                cur,
                "select job_id,lease_token from ops.engineering_claim_slice(%s,1,300)",
                ("engineering-claim-local-terminal",),
            )
            if terminal_claimed[0] != terminal_job_id or not isinstance(terminal_claimed[1], uuid.UUID):
                return fail("fresh terminal-race fixture was not claimable")
            cur.execute("reset role")
            cur.execute("savepoint terminalization_live_lease")
            try:
                cur.execute("update ops.capability_agent_session set state='cancelled',cancelled_at=now() where id=%s", (terminal_session_id,))
            except psycopg.Error as exc:
                detail = str(exc)
                cur.execute("rollback to savepoint terminalization_live_lease")
                cur.execute("release savepoint terminalization_live_lease")
                if "engineering session terminalization deferred while its dispatch lease is live" not in detail:
                    raise RuntimeError(f"live-lease terminalization returned the wrong refusal: {detail}") from exc
            else:
                raise RuntimeError("live Engineering lease permitted session terminalization")
            cur.execute("update ops.job set state='failed',ended_at=now(),lease_token=null,leased_until=null where id=%s", (terminal_job_id,))
            cur.execute("update ops.capability_agent_session set state='cancelled',cancelled_at=now() where id=%s", (terminal_session_id,))
            set_local_role(cur, RUNTIME_ROLE)
            if one(cur, "select ops.engineering_controller_binding(%s,%s)", (terminal_envelope_id, terminal_job_id))[0] is not None:
                return fail("a cancelled session retained its controller binding")
            expect_receipt_refusal(cur, terminal_envelope_id, terminal_claimed[1], codex_id,
                                   "terminal-session receipt")
    except Exception as exc:  # noqa: BLE001 - DB gates report exact refusal details
        return fail(str(exc))
    print("engineering claim local acceptance passed: scoped lease, attempt, and binding are exact and rollback-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
