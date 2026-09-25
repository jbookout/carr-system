#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-Postgres proof for the DoctorCRE V5-R02 SQL guards
added or fixed during PR #1245's review (2026-09-24, migrations 0593 and
0599). Each guard below is exercised BOTH ways -- the fix passes, and the
exact defect the reviewer found (or a deliberate mutant of the same shape)
is proven refused -- against a real PostgreSQL, not a fake DB client. This is
the "3 SQL mutants that survived" gap: node --test's fake query() client
(mcp-server/test/workflow-cutover.v5.test.mjs) can only prove the JS layer
calls the SQL doors with the right arguments in the right order; it cannot
prove the SQL itself enforces anything, because the fake just re-implements
the guard in JavaScript rather than exercising the real function body.

Guards covered, one function per PR #1245 review item:

  item 2  ops.advance_workflow_cutover_stage: 'retired' is a terminal stage
          (array_position returns NULL for it, which plpgsql's IF treats as
          falsy -- an explicit v_from_idx IS NULL guard is what actually
          catches this, not the sequential-index comparison alone).
  item 3  ops.retire_workflow_cutover_plan: workflow_version match (not just
          workflow_key), approved_at must be at or after the cutover
          transition, one-use per receipt, a receipt for every registered
          legacy surface, authority-derived actor.
  item 4  ops.mark_slice_completion / ops.register_slice_checkable_done: an
          unregistered slice_id is refused, the submitted criteria set must
          equal the registered set exactly, pass is recomputed from a
          resolved evidence row (never the caller's claim), and
          status=complete is authority-only.
  item 5  ops.enqueue_job: at single_write_authority or later, live mode
          requires every legacy surface to carry a disable receipt; at any
          active stage, canary/live requires a canary acceptance recorded
          since the plan's most recent stage transition.
  item 6  ops.retire_workflow_cutover_plan's p_census_available gate (checked
          last, after every stage/receipt check); ops.record_workflow_caller
          refuses status=done for an unregistered job_definition and appends
          to ops.workflow_caller_history on every call, which itself refuses
          a direct UPDATE/DELETE.
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"workflow-cutover-r02-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def expect_refusal(cur, sql: str, params: tuple, label: str, *, match: str | None = None) -> None:
    cur.execute("savepoint r02_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint r02_refusal")
        if match is not None and match not in str(exc):
            raise RuntimeError(f"{label} was refused for the wrong reason: {exc}") from exc
        return
    cur.execute("rollback to savepoint r02_refusal")
    raise RuntimeError(f"{label} was accepted")


def insert_job_definition(cur, key: str, *, canary_enabled: bool | None = True) -> None:
    contract = {"entrypoint": "fixture"}
    if canary_enabled is not None:
        contract["canary"] = {"enabled": canary_enabled}
    cur.execute(
        """insert into ops.job_definition
             (key,version,enabled,risk,execution_kind,execution_contract,
              recurrence,retry_policy,deduplication,completion_contract,legacy_schedule)
           values (%s,1,true,'green','deterministic',%s,
                   '{"cron":"* * * * *","timezone":"UTC"}'::jsonb,
                   '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}'::jsonb,
                   '{"key_template":"r02-gate-fixture"}'::jsonb,
                   '{"predicate":"fixture","receipt_kind":"fixture"}'::jsonb,
                   '{"status":"enabled"}'::jsonb)""",
        (key, Jsonb(contract)),
    )


def insert_acceptance(cur, key: str, mode: str, ref: str, *, backdate: bool = False) -> str:
    # `now()` is fixed for the whole life of this gate's one transaction
    # (rollback_only_connection never commits), so a row inserted "before" a
    # later advance_workflow_cutover_stage call still gets the SAME
    # created_at as that transition's occurred_at -- there is no natural
    # ordering signal to test staleness against inside one transaction.
    # `backdate=True` sets created_at an hour in the past explicitly, which
    # IS honored on INSERT (only the append-only tables' UPDATE/DELETE
    # triggers block a later rewrite), so a genuinely-earlier row exists to
    # prove ops.enqueue_job's per-stage freshness check against.
    if backdate:
        return cur.execute(
            """insert into ops.workflow_acceptance (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by,created_at)
               values (%s,1,%s,'accepted',%s,'r02-gate', now() - interval '1 hour') returning id::text""",
            (key, mode, ref),
        ).fetchone()[0]
    return cur.execute(
        """insert into ops.workflow_acceptance (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
           values (%s,1,%s,'accepted',%s,'r02-gate') returning id::text""",
        (key, mode, ref),
    ).fetchone()[0]


def open_plan(cur, key: str) -> str:
    return cur.execute(
        "select id::text from ops.open_workflow_cutover_plan(%s,1,'fixture recovery plan',%s,'joe')",
        (key, uuid.uuid4()),
    ).fetchone()[0]


def advance(cur, plan_id: str, to_stage: str, evidence_ref: str | None, reason: str) -> str:
    return cur.execute(
        "select stage from ops.advance_workflow_cutover_stage(%s,%s,%s,%s,%s,'joe')",
        (plan_id, to_stage, evidence_ref, reason, uuid.uuid4()),
    ).fetchone()[0]


def walk_to_single_write_authority(cur, key: str, *, backdate_canary: bool = False) -> str:
    """Open a plan and advance it read_legacy -> single_write_authority with
    real accepted evidence at each gated transition. Returns the plan id."""
    plan_id = open_plan(cur, key)
    advance(cur, plan_id, "build_projection", "ev", "r1")
    shadow_id = insert_acceptance(cur, key, "shadow", f"{key}-shadow-1")
    advance(cur, plan_id, "shadow_compare", shadow_id, "r2")
    canary_id = insert_acceptance(cur, key, "canary", f"{key}-canary-1", backdate=backdate_canary)
    advance(cur, plan_id, "single_write_authority", canary_id, "r3")
    return plan_id


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            cur.execute("""do $$ begin
              if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
            end $$""")
            cur.execute("grant carr_authority to carr_authority_joe")
            # The authority login reaches mark_slice_completion (EXECUTE to
            # carr_writer) through SET ROLE; rolled back with the transaction.
            cur.execute("grant carr_writer to carr_authority_joe with set true")
            grant_settable_runtime_roles(cur, "carr_authority_joe", "carr_writer", "carr_reader", "carr_jobs")

            token = uuid.uuid4().hex[:8]

            # ================================================================
            # ITEM 2: 'retired' is a terminal stage for advance_*.
            # ================================================================
            key2 = f"r02gate-{token}-item2"
            insert_job_definition(cur, key2)
            plan2 = walk_to_single_write_authority(cur, key2)
            canary2b = insert_acceptance(cur, key2, "canary", f"{key2}-canary-2")
            advance(cur, plan2, "cutover", canary2b, "r4")
            advance(cur, plan2, "monitor", None, "r5")
            advance(cur, plan2, "recovery_ready", None, "r6")
            cur.execute(
                """insert into ops.legacy_schedule_disable_receipt
                     (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by)
                   values (%s,%s,%s,1,'no-surface','no-locator','no legacy surface registered','joe')
                   returning id::text""",
                (f"{key2}-disable", f"{key2}-disable-idem", key2),
            )
            receipt2 = cur.fetchone()[0]
            set_local_role(cur, "carr_authority")
            cur.execute("set session authorization carr_authority_joe")
            retired2 = cur.execute(
                "select stage,status from ops.retire_workflow_cutover_plan(%s,%s,'legacy disabled',%s,'joe',true)",
                (plan2, receipt2, uuid.uuid4()),
            ).fetchone()
            if retired2 != ("retired", "retired"):
                return fail(f"item 2 fixture plan did not reach retired/retired: {retired2}")
            # A retired plan is refused twice over: status left 'active' is
            # checked first (workflow_cutover_plan_not_active, since item 2
            # also moves status to 'retired'), and even if that check were
            # ever removed, array_position(v_stages, 'retired') is NULL for a
            # plan at stage='retired' -- the mutant this guards against is
            # deleting the explicit `if v_from_idx is null` check and relying
            # on `v_to_idx <> v_from_idx + 1` alone, which plpgsql evaluates
            # as NULL (falsy) rather than true, silently allowing the
            # "advance". Both layers are real; this proves the outer one.
            # The advance door is EXECUTE-granted to carr_writer only (0593),
            # so the refusal is asked as carr_writer: asked as carr_authority
            # it would stop at "permission denied" and prove nothing.
            cur.execute("reset session authorization")
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur, "select ops.advance_workflow_cutover_stage(%s,'monitor','ev','trying to un-retire',%s,'joe')",
                (plan2, uuid.uuid4()),
                "advancing a retired plan",
                match="workflow_cutover_plan_not_active",
            )
            cur.execute("reset role")

            # Directly exercise the array_position-NULL guard itself (the
            # actual mutant named in the review), independent of the
            # status<>'active' check above: force stage='retired' while
            # status stays 'active' (a state ops.retire_workflow_cutover_plan
            # itself never produces since item 2's fix, but exactly the
            # "some future write path" case v_from_idx IS NULL guards
            # against) and confirm the sequential-index comparison alone
            # would NOT have caught it.
            cur.execute(
                "update ops.workflow_cutover_plan set status='active' where id=%s", (plan2,)
            )
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur, "select ops.advance_workflow_cutover_stage(%s,'monitor','ev','trying to un-retire again',%s,'joe')",
                (plan2, uuid.uuid4()),
                "advancing a plan forced to stage=retired,status=active (the array_position(NULL) mutant)",
                match="workflow_cutover_plan_stage_not_advanceable",
            )
            cur.execute("reset role")

            # ================================================================
            # ITEM 3: retire-receipt binding.
            # ================================================================
            key3 = f"r02gate-{token}-item3"
            insert_job_definition(cur, key3)
            plan3 = walk_to_single_write_authority(cur, key3)
            canary3b = insert_acceptance(cur, key3, "canary", f"{key3}-canary-2")
            advance(cur, plan3, "cutover", canary3b, "r4")
            advance(cur, plan3, "monitor", None, "r5")
            advance(cur, plan3, "recovery_ready", None, "r6")

            # 3a: a receipt for a DIFFERENT workflow_version is refused even
            # though workflow_key matches.
            cur.execute(
                """insert into ops.job_definition
                     (key,version,enabled,risk,execution_kind,execution_contract,
                      recurrence,retry_policy,deduplication,completion_contract,legacy_schedule)
                   values (%s,2,false,'green','deterministic','{"entrypoint":"fixture"}'::jsonb,
                           '{"cron":"* * * * *","timezone":"UTC"}'::jsonb,
                           '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}'::jsonb,
                           '{"key_template":"r02-gate-fixture-v2"}'::jsonb,
                           '{"predicate":"fixture","receipt_kind":"fixture"}'::jsonb,
                           '{"status":"enabled"}'::jsonb)""",
                (key3,),
            )
            wrong_version_receipt = cur.execute(
                """insert into ops.legacy_schedule_disable_receipt
                     (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by)
                   values (%s,%s,%s,2,'s','l','wrong version','joe') returning id::text""",
                (f"{key3}-wrongver", f"{key3}-wrongver-idem", key3),
            ).fetchone()[0]
            set_local_role(cur, "carr_authority")
            cur.execute("set session authorization carr_authority_joe")
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,%s,'r',%s,'joe',true)",
                (plan3, wrong_version_receipt, uuid.uuid4()),
                "receipt for a different workflow_version",
                match="legacy_schedule_disable_receipt_workflow_mismatch",
            )
            cur.execute("reset session authorization")

            # 3b: a receipt approved BEFORE the cutover transition is refused.
            cur.execute(
                """insert into ops.legacy_schedule_disable_receipt
                     (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by,approved_at)
                   values (%s,%s,%s,1,'s2','l2','predates cutover','joe', now() - interval '1 year')
                   returning id::text""",
                (f"{key3}-stale", f"{key3}-stale-idem", key3),
            )
            stale_receipt = cur.fetchone()[0]
            cur.execute("set session authorization carr_authority_joe")
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,%s,'r',%s,'joe',true)",
                (plan3, stale_receipt, uuid.uuid4()),
                "receipt approved before the cutover transition",
                match="legacy_schedule_disable_receipt_predates_cutover_transition",
            )
            cur.execute("reset session authorization")

            # 3c: a receipt for a legacy surface that IS registered, but with
            # no disable receipt yet, is refused.
            cur.execute(
                """insert into ops.legacy_schedule_surface_registry (workflow_key,workflow_version,surface_id,locator,scheduler_kind)
                   values (%s,1,'launchd-1','com.carr.r02gate.plist','launchd')""",
                (key3,),
            )
            good_receipt = cur.execute(
                """insert into ops.legacy_schedule_disable_receipt
                     (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by)
                   values (%s,%s,%s,1,'other-surface','other-locator','wrong surface','joe') returning id::text""",
                (f"{key3}-wrongsurface", f"{key3}-wrongsurface-idem", key3),
            ).fetchone()[0]
            cur.execute("set session authorization carr_authority_joe")
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,%s,'r',%s,'joe',true)",
                (plan3, good_receipt, uuid.uuid4()),
                "a receipt that does not cover the registered legacy surface",
                match="legacy_schedule_disable_receipt_missing_for_",
            )
            cur.execute("reset session authorization")

            # 3d positive: the real, matching receipt for the registered
            # surface, approved_at set explicitly one day in the future at
            # INSERT time (the append-only trigger blocks a later UPDATE, but
            # not choosing the value up front) -- far enough ahead that it
            # postdates plan3's cutover AND the second plan's cutover built
            # below, which is what isolates 3e's one-use refusal from the
            # already-proven predates-cutover-transition check (3b): reusing
            # a receipt that genuinely does NOT predate either plan's cutover
            # must still be refused, specifically for having been consumed.
            real_receipt = cur.execute(
                """insert into ops.legacy_schedule_disable_receipt
                     (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by,approved_at)
                   values (%s,%s,%s,1,'launchd-1','com.carr.r02gate.plist','disabled for real','joe',now() + interval '1 day')
                   returning id::text""",
                (f"{key3}-real", f"{key3}-real-idem", key3),
            ).fetchone()[0]
            cur.execute("set session authorization carr_authority_joe")
            retired3 = cur.execute(
                "select stage,status from ops.retire_workflow_cutover_plan(%s,%s,'legacy disabled for real',%s,'joe',true)",
                (plan3, real_receipt, uuid.uuid4()),
            ).fetchone()
            if retired3 != ("retired", "retired"):
                return fail(f"item 3 positive retire did not succeed: {retired3}")
            cur.execute("reset session authorization")

            # 3e: a fresh SECOND plan for the same identity (a normal open,
            # not a supersession, since plan3's status left 'active' at
            # retirement) walked to recovery_ready, then an attempt to reuse
            # the SAME already-consumed receipt -- still future-dated ahead
            # of THIS plan's cutover too -- must be refused specifically for
            # one-use, not for predates-cutover or any other check.
            plan3_second = open_plan(cur, key3)
            advance(cur, plan3_second, "build_projection", "ev", "r1b")
            shadow3b = insert_acceptance(cur, key3, "shadow", f"{key3}-shadow-2")
            advance(cur, plan3_second, "shadow_compare", shadow3b, "r2b")
            canary3c = insert_acceptance(cur, key3, "canary", f"{key3}-canary-3")
            advance(cur, plan3_second, "single_write_authority", canary3c, "r3b")
            canary3d = insert_acceptance(cur, key3, "canary", f"{key3}-canary-4")
            advance(cur, plan3_second, "cutover", canary3d, "r4b")
            advance(cur, plan3_second, "monitor", None, "r5b")
            advance(cur, plan3_second, "recovery_ready", None, "r6b")
            cur.execute("set session authorization carr_authority_joe")
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,%s,'reuse attempt',%s,'joe',true)",
                (plan3_second, real_receipt, uuid.uuid4()),
                "reusing an already-consumed disable receipt against a second, otherwise-valid plan",
                match="legacy_schedule_disable_receipt_already_used",
            )
            cur.execute("reset session authorization")

            # 3f: even a session granted the carr_authority ROLE (so the GRANT
            # EXECUTE check alone would let it through) is refused, because
            # ops.authority_actor_slug() checks session_user (the real login
            # identity), not current_user (what SET ROLE changes) -- the
            # defense-in-depth re-check the function's own comment names.
            key3b = f"r02gate-{token}-item3b"
            insert_job_definition(cur, key3b)
            plan3b = open_plan(cur, key3b)
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,gen_random_uuid(),'r',%s,'joe',true)",
                (plan3b, uuid.uuid4()),
                "a plain carr_writer session (no execute grant at all)",
                match="permission denied",
            )
            cur.execute("reset role")
            set_local_role(cur, "carr_authority")
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,gen_random_uuid(),'r',%s,'joe',true)",
                (plan3b, uuid.uuid4()),
                "a session with the carr_authority ROLE but a non-authority session_user",
                match="authority session user",
            )
            cur.execute("reset role")

            # ================================================================
            # ITEM 4: slice-completion criteria registry and evidence recompute.
            # ================================================================
            slice_id = f"r02gate-slice-{token}"
            # mark_slice_completion is EXECUTE-granted to carr_writer (0593)
            # and additionally requires an authority session_user for
            # status=complete, so it is asked as the authority login acting
            # through SET ROLE carr_writer; register_slice_checkable_done is
            # granted to carr_authority and is asked through that role.
            cur.execute("set session authorization carr_authority_joe")
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur, "select ops.mark_slice_completion(%s,'complete','[{\"criterion\":\"x\",\"evidence_kind\":\"acceptance\",\"evidence_ref\":\"00000000-0000-0000-0000-000000000000\"}]'::jsonb,'r',%s,'joe')",
                (f"{slice_id}-unknown", uuid.uuid4()),
                "marking an unregistered slice_id",
                match="slice_completion_unknown_slice_id",
            )
            set_local_role(cur, "carr_authority")
            cur.execute(
                "select ops.register_slice_checkable_done(%s,array['criterion A','criterion B'],%s,'joe')",
                (slice_id, uuid.uuid4()),
            )
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur,
                "select ops.mark_slice_completion(%s,'complete','[{\"criterion\":\"criterion A\",\"evidence_kind\":\"acceptance\",\"evidence_ref\":\"00000000-0000-0000-0000-000000000000\"}]'::jsonb,'r',%s,'joe')",
                (slice_id, uuid.uuid4()),
                "submitting fewer criteria than registered",
                match="slice_completion_criteria_set_mismatch",
            )
            cur.execute("reset session authorization")
            insert_job_definition(cur, f"{slice_id}-evwf")
            acc_id = insert_acceptance(cur, f"{slice_id}-evwf", "canary", f"{slice_id}-acc")
            cur.execute("set session authorization carr_authority_joe")
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur,
                "select ops.mark_slice_completion(%s,'complete',jsonb_build_array("
                "jsonb_build_object('criterion','criterion A','evidence_kind','acceptance','evidence_ref','00000000-0000-0000-0000-000000000000','pass',true),"
                "jsonb_build_object('criterion','criterion B','evidence_kind','acceptance','evidence_ref',%s::text,'pass',true)"
                "),'r',%s,'joe')",
                (slice_id, "00000000-0000-0000-0000-000000000000", uuid.uuid4()),
                "an unresolvable evidence_ref, even with the caller claiming pass=true",
                match="slice_completion_complete_requires_every_criterion_proven",
            )
            completed = cur.execute(
                "select status,criteria_receipt from ops.mark_slice_completion(%s,'complete',jsonb_build_array("
                "jsonb_build_object('criterion','criterion A','evidence_kind','acceptance','evidence_ref',%s::text),"
                "jsonb_build_object('criterion','criterion B','evidence_kind','acceptance','evidence_ref',%s::text)"
                "),'r',%s,'joe')",
                (slice_id, acc_id, acc_id, uuid.uuid4()),
            ).fetchone()
            if completed[0] != "complete" or not all(el["pass"] is True for el in completed[1]):
                return fail(f"item 4 positive completion did not recompute pass=true server-side: {completed}")
            cur.execute("reset session authorization")

            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur, "select ops.register_slice_checkable_done(%s,array['c1'],%s,'joe')",
                (f"{slice_id}-writer-attempt", uuid.uuid4()),
                "a plain carr_writer registering a checkable_done set",
                match="permission denied",
            )
            cur.execute("reset role")

            # ================================================================
            # ITEM 5: dual-write gating on ops.enqueue_job.
            # ================================================================
            key5 = f"r02gate-{token}-item5"
            insert_job_definition(cur, key5)
            plan5 = walk_to_single_write_authority(cur, key5, backdate_canary=True)
            set_local_role(cur, "carr_jobs")
            expect_refusal(
                cur, "select ops.enqueue_job(%s,1,now()+interval '1 hour','{}'::jsonb,%s,'live')",
                (key5, f"{key5}-idem-stale"),
                "live enqueue with only a stale (pre-transition) canary acceptance",
                match="no canary acceptance evidence recorded since entering cutover stage",
            )
            cur.execute("reset role")
            fresh_canary = insert_acceptance(cur, key5, "canary", f"{key5}-fresh")
            cur.execute(
                """insert into ops.legacy_schedule_surface_registry (workflow_key,workflow_version,surface_id,locator,scheduler_kind)
                   values (%s,1,'launchd-1-item5','com.carr.r02gate5.plist','launchd')""",
                (key5,),
            )
            set_local_role(cur, "carr_jobs")
            expect_refusal(
                cur, "select ops.enqueue_job(%s,1,now()+interval '1 hour','{}'::jsonb,%s,'live')",
                (key5, f"{key5}-idem-undisabled"),
                "live enqueue at single_write_authority with an undisabled legacy surface",
                match="legacy surface(s) still undisabled",
            )
            cur.execute("reset role")
            cur.execute(
                """insert into ops.legacy_schedule_disable_receipt
                     (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by)
                   values (%s,%s,%s,1,'launchd-1-item5','com.carr.r02gate5.plist','disabled','joe')""",
                (f"{key5}-disable", f"{key5}-disable-idem", key5),
            )
            set_local_role(cur, "carr_jobs")
            enqueued = cur.execute(
                "select mode from ops.enqueue_job(%s,1,now()+interval '1 hour','{}'::jsonb,%s,'live')",
                (key5, f"{key5}-idem-ok"),
            ).fetchone()
            cur.execute("reset role")
            if enqueued != ("live",):
                return fail(f"item 5 positive live enqueue did not succeed once gated: {enqueued}")

            # A workflow with no active cutover plan at all must be totally
            # unaffected by any of the above.
            key5b = f"r02gate-{token}-item5b"
            insert_job_definition(cur, key5b, canary_enabled=False)
            insert_acceptance(cur, key5b, "shadow", f"{key5b}-shadow")
            set_local_role(cur, "carr_jobs")
            unaffected = cur.execute(
                "select mode from ops.enqueue_job(%s,1,now()+interval '1 hour','{}'::jsonb,%s,'live')",
                (key5b, f"{key5b}-idem"),
            ).fetchone()
            cur.execute("reset role")
            if unaffected != ("live",):
                return fail(f"item 5: a workflow with no active cutover plan was unexpectedly gated: {unaffected}")

            # ================================================================
            # ITEM 6: census gate ordering, record_workflow_caller, history.
            # ================================================================
            key6 = f"r02gate-{token}-item6"
            insert_job_definition(cur, key6)
            plan6 = walk_to_single_write_authority(cur, key6)
            canary6b = insert_acceptance(cur, key6, "canary", f"{key6}-canary-2")
            advance(cur, plan6, "cutover", canary6b, "r4")
            advance(cur, plan6, "monitor", None, "r5")
            advance(cur, plan6, "recovery_ready", None, "r6")
            receipt6 = cur.execute(
                """insert into ops.legacy_schedule_disable_receipt
                     (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by)
                   values (%s,%s,%s,1,'no-surface','no-locator','no surfaces','joe') returning id::text""",
                (f"{key6}-disable", f"{key6}-disable-idem", key6),
            ).fetchone()[0]
            set_local_role(cur, "carr_authority")
            cur.execute("set session authorization carr_authority_joe")
            # The stage/receipt checks still win over a false census when both
            # would refuse -- proven by an intentionally-wrong receipt id
            # (not found) combined with census_available=false: the specific
            # not-found error must be what's raised, not the generic census one.
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,'00000000-0000-0000-0000-000000000000','r',%s,'joe',false)",
                (plan6, uuid.uuid4()),
                "a not-found receipt combined with an unavailable census",
                match="legacy_schedule_disable_receipt_not_found",
            )
            expect_refusal(
                cur, "select ops.retire_workflow_cutover_plan(%s,%s,'r',%s,'joe',false)",
                (plan6, receipt6, uuid.uuid4()),
                "a fully valid plan+receipt with an unavailable census",
                match="workflow_cutover_retire_refused_census_unknown",
            )
            retired6 = cur.execute(
                "select stage from ops.retire_workflow_cutover_plan(%s,%s,'r',%s,'joe',true)",
                (plan6, receipt6, uuid.uuid4()),
            ).fetchone()
            if retired6 != ("retired",):
                return fail(f"item 6 positive retire with census_available=true did not succeed: {retired6}")
            cur.execute("reset session authorization")

            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur, "select ops.record_workflow_caller(%s,1,%s,'script','done',null,'commit-abc',null)",
                (f"{key6}-unregistered", "caller.py"),
                "status=done for a (workflow_key, workflow_version) with no job_definition row",
                match="caller_done_requires_registered_job_definition",
            )
            cur.execute(
                "select id from ops.record_workflow_caller(%s,1,'caller-hist.py','script','remaining',null,null,'writer')",
                (key6,),
            )
            cur.execute(
                "select id from ops.record_workflow_caller(%s,1,'caller-hist.py','script','blocked','waiting',null,'writer')",
                (key6,),
            )
            cur.execute(
                "select id from ops.record_workflow_caller(%s,1,'caller-hist.py','script','done',null,'commit-xyz','writer')",
                (key6,),
            )
            cur.execute("reset role")
            set_local_role(cur, "carr_reader")
            history_statuses = [
                row[0] for row in cur.execute(
                    """select status from ops.workflow_caller_history
                       where workflow_key=%s and caller_locator='caller-hist.py' order by recorded_at""",
                    (key6,),
                ).fetchall()
            ]
            current_status = cur.execute(
                "select status from ops.workflow_caller where workflow_key=%s and caller_locator='caller-hist.py'",
                (key6,),
            ).fetchone()[0]
            cur.execute("reset role")
            if history_statuses != ["remaining", "blocked", "done"]:
                return fail(f"workflow_caller_history did not preserve all three status changes: {history_statuses}")
            if current_status != "done":
                return fail(f"workflow_caller did not hold the latest status: {current_status}")

            # No runtime role is even granted UPDATE on this table (only
            # carr_reader gets SELECT; every write is meant to happen only
            # through ops.record_workflow_caller) -- that GRANT-level
            # refusal is real, but it isn't the guard this proves. Run the
            # UPDATE as the table owner instead, bypassing the GRANT layer
            # entirely, to reach and prove the append-only TRIGGER itself.
            expect_refusal(
                cur, "update ops.workflow_caller_history set status='remaining' where workflow_key=%s and caller_locator='caller-hist.py' and status='done'",
                (key6,),
                "a direct UPDATE against the append-only workflow_caller_history table (as table owner, past any GRANT)",
                match="append-only",
            )

        print("PASS: V5-R02 PR #1245 review-fix SQL guards (items 2-6) hold against a real PostgreSQL, "
              "both the positive path and every named refusal")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
