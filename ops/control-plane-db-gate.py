#!/usr/bin/env python3
# ci: db-gate
"""Database acceptance gate for control-plane admission and job execution.

Runs inside one transaction and rolls back every fixture.  It exercises the
public functions as a caller would, including refusal paths; catalog presence
alone is not evidence that a state machine works.
"""
from __future__ import annotations

import os
import sys
import uuid
import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from gate_runtime_role import grant_settable_runtime_roles, set_local_role

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.control_plane_scheduler_cutover import scheduler_launchd_rows, scheduler_provider_rows  # noqa: E402

SCHEDULER_REGISTRY_PATH = REPO / "ops" / "config" / "control-plane-scheduler-cutover.v1.json"
WORKFLOW_MANIFEST_PATH = REPO / "ops" / "config" / "control-plane-workflows.v1.json"


REQUIRED_TABLES = [
    "ops.guidance_intake", "ops.rule_admission", "ops.rule_enforcement_point",
    "ops.authority_receipt", "ops.legacy_schedule_disable_receipt", "ops.legacy_schedule_surface_registry",
    "ops.legacy_schedule_provider_contract", "ops.legacy_schedule_launchd_contract",
    "ops.legacy_schedule_observation_receipt", "ops.job_definition", "ops.job",
    "ops.job_attempt", "ops.job_receipt", "ops.cognition_job",
    "ops.cognition_result_cache", "ops.workflow_acceptance",
    "ops.provider_route", "ops.provider_observation",
    "ops.cost_reservation", "ops.cost_refusal", "ops.npi_device_evidence_receipt",
]

REQUIRED_FUNCTIONS = [
    "ops.enqueue_job(text,integer,timestamp with time zone,jsonb,text,text)",
    "ops.claim_job(text,integer,integer)",
    "ops.claim_job_mode(text,text,integer,integer)",
    "ops.heartbeat_job(uuid,uuid,integer)",
    "ops.complete_job(uuid,uuid,jsonb,text)",
    "ops.fail_job(uuid,uuid,text,text)",
    "ops.timeout_job(uuid,uuid,text)",
    "ops.reap_expired_jobs()",
    "ops.record_workflow_acceptance(text,text,text,text)",
    "ops.disable_legacy_schedule(text,text,text,text,text,text,text,text,text,text,text)",
    "ops.authority_actor_slug()",
    "ops.select_provider_routes(text[])",
    "ops.get_cognition_cache(text)",
    "ops.put_cognition_cache(text,text,integer,integer,jsonb,text[],integer)",
    "ops.invalidate_cognition_cache(text)",
    "ops.record_provider_observation(text,text,integer,text,integer,text)",
    "ops.reserve_job_cost(uuid,uuid,text,numeric)",
    "ops.admit_job_cost(uuid,uuid,text,numeric)",
    "ops.record_npi_device_evidence(uuid,timestamp with time zone,text,text,jsonb,text)",
    "ops.record_claude_scheduler_observation(text,text,text,text,boolean,text,text,text,timestamp with time zone,text)",
    "ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamp with time zone,text)",
    "ops.settle_job_cost(uuid,uuid,uuid,integer,integer,numeric)",
    "ops.release_job_cost(uuid,uuid,uuid)",
]


def fail(message: str) -> None:
    print(f"control-plane-db-gate FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetchone_required(row: tuple[Any, ...] | None, context: str) -> tuple[Any, ...]:
    """Turn an unexpected empty SELECT into the gate's normal failure path."""
    if row is None:
        fail(f"expected one row for {context}")
        raise AssertionError("fail exits")
    return row


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        fail("DATABASE_URL is required")
        return 2
    try:
        registry = json.loads(SCHEDULER_REGISTRY_PATH.read_text(encoding="utf-8"))
        manifest = json.loads(WORKFLOW_MANIFEST_PATH.read_text(encoding="utf-8"))
        expected_surfaces = sorted(
            (str(surface["workflow_key"]), int(surface["workflow_version"]), str(surface["surface_id"]),
             str(surface["locator"]), str(surface["scheduler_kind"]), surface.get("duplicate_group"))
            for surface in registry["surfaces"]
        )
        expected_provider = sorted(
            (row[2], row[0], row[1], row[3], row[4], row[5], row[6], row[7])
            for row in scheduler_provider_rows(registry, manifest=manifest, repo=REPO)
        )
        expected_launchd = sorted(
            (row[2], row[0], row[1], row[3], row[4], row[5], json.loads(row[6]),
             row[7], row[8], row[9])
            for row in scheduler_launchd_rows(registry, manifest=manifest, repo=REPO)
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        fail(f"could not load checked-in scheduler surface registry: {exc}")
        return 2

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for table in REQUIRED_TABLES:
                cur.execute("select to_regclass(%s)", (table,))
                if fetchone_required(cur.fetchone(), f"table {table}")[0] is None:
                    fail(f"missing table {table}")
            for function in REQUIRED_FUNCTIONS:
                cur.execute("select to_regprocedure(%s)", (function,))
                if fetchone_required(cur.fetchone(), f"function {function}")[0] is None:
                    fail(f"missing function {function}")
            cur.execute("""select workflow_key,workflow_version,surface_id,locator,scheduler_kind,duplicate_group
                             from ops.legacy_schedule_surface_registry
                             order by workflow_key,workflow_version,surface_id""")
            actual_surfaces = [tuple(row) for row in cur.fetchall()]
            if actual_surfaces != expected_surfaces:
                fail("scheduler surface registry is empty, stale, or does not exactly match checked-in sync inventory")
            cur.execute("""select surface_id,workflow_key,workflow_version,locator,cron_expression,
                                  timezone,definition_relpath,definition_sha256
                             from ops.legacy_schedule_provider_contract
                             order by surface_id""")
            actual_provider = [tuple(row) for row in cur.fetchall()]
            if actual_provider != expected_provider:
                fail("Claude scheduler provider contract is empty, stale, or not derived from checked-in definitions")
            cur.execute("""select surface_id,workflow_key,workflow_version,locator,repo_plist_relpath,
                                  installed_plist_name,program_arguments,plist_sha256,schedule_sha256,timezone
                             from ops.legacy_schedule_launchd_contract
                             order by surface_id""")
            actual_launchd = [tuple(row) for row in cur.fetchall()]
            if actual_launchd != expected_launchd:
                fail("launchd scheduler contract is empty, stale, or not derived from checked-in plists")
            cur.execute("select has_function_privilege('carr_jobs', "
                        "'ops.record_claude_scheduler_observation(text,text,text,text,boolean,text,text,text,timestamptz,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "jobs scheduler observation privilege")[0]:
                fail("carr_jobs can mint native scheduler observations")
            cur.execute("select has_function_privilege('carr_jobs', "
                        "'ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "jobs launchd observation privilege")[0]:
                fail("carr_jobs can mint native launchd observations")

            # The reaper must lock a finite expired-job set before it changes
            # attempt evidence.  Otherwise a heartbeat can renew the lease
            # between a broad attempt update and the later job transition.
            cur.execute("select pg_get_functiondef('ops.reap_expired_jobs()'::regprocedure)")
            reaper = fetchone_required(cur.fetchone(), "expired-job reaper definition")[0]
            reaper_text = str(reaper).lower()
            lock_at = reaper_text.find("with expired as materialized")
            attempt_at = reaper_text.find("update ops.job_attempt")
            if lock_at < 0 or "for update skip locked" not in reaper_text or attempt_at < lock_at:
                fail("expired-job reaper can mark an attempt before locking its job")
            cur.execute("select has_function_privilege('carr_writer', "
                        "'ops.record_workflow_acceptance(text,text,text,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "writer workflow acceptance privilege")[0]:
                fail("carr_writer can forge workflow acceptance")
            cur.execute("select has_function_privilege('carr_writer', "
                        "'ops.disable_legacy_schedule(text,text,text,text,text,text,text,text,text,text,text)'::regprocedure, 'execute')")
            if fetchone_required(cur.fetchone(), "writer legacy-disable privilege")[0]:
                fail("carr_writer can retire a legacy schedule")
            cur.execute("select pg_get_functiondef('ops.record_workflow_acceptance(text,text,text,text)'::regprocedure)")
            acceptance = str(fetchone_required(cur.fetchone(), "authority acceptance definition")[0]).lower()
            if "authority_actor_slug()" not in acceptance or "p_actor" in acceptance:
                fail("workflow acceptance does not derive its actor from the authority session")
            if ("p_mode='canary'" not in acceptance.replace(" ", "")
                    or "authority_actor<>'joe'" not in acceptance.replace(" ", "")):
                fail("accepted canary workflow evidence is not Joe-only")

            cur.execute("""
                select 1 from pg_trigger
                 where tgrelid='public.rule'::regclass
                   and tgname='rule_activation_requires_admission'
                   and not tgisinternal
            """)
            if cur.fetchone() is None:
                fail("rule activation trigger is not installed")

            # The runtime role operates through functions and must not own or
            # erase the ledger tables.
            cur.execute("select pg_get_userbyid(relowner) from pg_class where oid='ops.job'::regclass")
            if fetchone_required(cur.fetchone(), "ops.job owner")[0] == "carr_jobs":
                fail("carr_jobs owns ops.job")
            cur.execute("select has_table_privilege('carr_jobs','ops.job','delete')")
            if fetchone_required(cur.fetchone(), "ops.job delete privilege")[0]:
                fail("carr_jobs can delete jobs")
            cur.execute("select has_table_privilege('carr_jobs','ops.job_receipt','update')")
            if fetchone_required(cur.fetchone(), "ops.job_receipt update privilege")[0]:
                fail("carr_jobs can rewrite receipts")
            cur.execute("""
                select has_table_privilege('carr_jobs','ops.device_evidence_receipt','select'),
                       has_table_privilege('carr_jobs','ops.device_evidence_receipt','insert'),
                       has_table_privilege('carr_jobs','ops.device_evidence_receipt','update'),
                       has_table_privilege('carr_jobs','ops.device_evidence_receipt','delete')
            """)
            device_acl = fetchone_required(cur.fetchone(), "device evidence jobs ACL")
            if tuple(device_acl) != (True, False, False, False):
                fail("carr_jobs device evidence access is not exactly read-only")
            cur.execute("""
                select has_function_privilege(
                         'carr_jobs',
                         'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,
                         'execute'),
                       has_function_privilege(
                         'carr_writer',
                         'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,
                         'execute'),
                       has_function_privilege(
                         'carr_device_evidence',
                         'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,
                         'execute')
            """)
            device_exec = fetchone_required(cur.fetchone(), "device evidence execution ACL")
            if tuple(device_exec) != (False, False, True):
                fail("device evidence append authority leaks to a routine role")
            cur.execute("""
                select pg_get_functiondef(
                  'ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure)
            """)
            device_function = str(fetchone_required(
                cur.fetchone(), "device evidence function definition")[0]).lower()
            if "login_role=session_user" not in device_function.replace(" ", ""):
                fail("device evidence function does not derive its principal from session_user")
            cur.execute("""
                select 1 from pg_trigger
                 where tgrelid='ops.device_evidence_receipt'::regclass
                   and tgname='device_evidence_receipt_append_only'
                   and not tgisinternal
            """)
            if cur.fetchone() is None:
                fail("device evidence receipts are not append-only")
            for table in ("ops.guidance_intake", "ops.rule_admission",
                          "ops.rule_enforcement_point"):
                cur.execute("select has_table_privilege('carr_writer',%s,'update')", (table,))
                if not fetchone_required(cur.fetchone(), f"carr_writer update {table}")[0]:
                    fail(f"carr_writer cannot update {table} during admission")
            collector_views = (
                "public.v_control_plane_enrichment_queue",
                "public.v_control_plane_deal_history_queue",
                "public.v_control_plane_content_fuel_rotation",
                "public.v_control_plane_npi_delta",
                "public.v_control_plane_radar_candidates",
                "public.v_control_plane_idea_candidates",
                "public.v_control_plane_social_sources",
                "public.v_control_plane_social_coverage",
                "public.v_control_plane_social_metric_exports",
                "ops.v_control_plane_health_evidence",
                "ops.v_control_plane_capability_candidate",
                "ops.v_control_plane_actionable_loops",
                "ops.v_control_plane_doctrine_due",
                "ops.v_control_plane_doctrine_failures",
                "ops.v_control_plane_system_prune_candidates",
            )
            for relation in collector_views:
                cur.execute("select has_table_privilege('carr_jobs',%s,'select')",(relation,))
                if not fetchone_required(cur.fetchone(), f"carr_jobs select {relation}")[0]:
                    fail(f"carr_jobs cannot read typed workflow evidence from {relation}")
                cur.execute(sql.SQL("select 1 from {} limit 1").format(sql.Identifier(*relation.split("."))))
                cur.fetchall()
            for relation in ("public.v_expired_verification", "public.candidate_pool",
                             "public.content_piece", "public.placement", "public.v_loops"):
                cur.execute("select has_table_privilege('carr_jobs',%s,'select')", (relation,))
                if fetchone_required(cur.fetchone(), f"carr_jobs broad read {relation}")[0]:
                    fail(f"carr_jobs retains broad source-table access to {relation}")

            cur.execute("select has_table_privilege('carr_jobs','ops.npi_device_evidence_receipt','select'), "
                        "has_table_privilege('carr_jobs','ops.npi_device_evidence_receipt','insert'), "
                        "has_function_privilege('carr_jobs',"
                        "'ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text)'::regprocedure,'execute')")
            if fetchone_required(cur.fetchone(), "NPI device evidence ACL") != (True, False, False):
                fail("jobs role NPI evidence boundary is not read-only")
            cur.execute("select has_function_privilege('carr_writer',"
                        "'ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text)'::regprocedure,'execute')")
            if fetchone_required(cur.fetchone(), "writer NPI evidence mint privilege")[0]:
                fail("writer can mint NPI device evidence")

            # Collector projections are definer views with PII-minimized
            # columns.  Explicit grants to carr_jobs must not be widened by a
            # deployment's default ACLs.
            for relation in collector_views:
                cur.execute("""
                    select exists (
                      select 1 from pg_class c
                       cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl
                      where c.oid=%s::regclass and acl.grantee=0
                        and acl.privilege_type='SELECT'
                    )
                """, (relation,))
                if fetchone_required(cur.fetchone(), f"PUBLIC collector read {relation}")[0]:
                    fail(f"PUBLIC can read collector projection {relation}")

            # An expired or unstamped re-verification queue is not a current
            # verified queue.  A model input must therefore refuse it instead
            # of relabeling its provenance as verification.
            cur.execute("""
                select count(*) from public.v_control_plane_enrichment_queue
                 where current_verification_status='verified'
                    or reverification_due not in ('expired','unstamped_volatile')
            """)
            if fetchone_required(cur.fetchone(), "enrichment truthfulness")[0] != 0:
                fail("expired verification evidence is represented as current verified evidence")

            # Candidate-pool state and vertical are source fields, not a
            # territory policy or provider taxonomy.  Until a reviewed policy
            # projection is installed the NPI path must expose unknowns.
            cur.execute("""
                select count(*) from public.v_control_plane_npi_delta
                 where territory_match is not null or entity_type is not null
            """)
            if fetchone_required(cur.fetchone(), "NPI unknown predicates")[0] != 0:
                fail("NPI projection asserts territory or provider facts without policy evidence")

            # The two rotation lanes are policy configuration only.  No source
            # record currently proves that either has a primary market source.
            cur.execute("""
                select count(*) from public.v_control_plane_content_fuel_rotation
                 where source_class is not null
            """)
            if fetchone_required(cur.fetchone(), "content-fuel source policy")[0] != 0:
                fail("content-fuel projection fabricates a primary-source class")

            # Deal-history sizing is allowed only after a Thursday enrichment
            # completion receipt has a typed exact subject count.  The view may
            # not substitute unrelated record_flag volume for that receipt.
            cur.execute("""
                select count(*)
                  from public.v_control_plane_deal_history_queue q
                 where not exists (
                   select 1 from ops.job_receipt r join ops.job j on j.id=r.job_id
                    where j.definition_key='contact-enrichment-weekly'
                      and r.kind='completion'
                      and extract(isodow from j.scheduled_for at time zone 'America/Chicago')=4
                      and jsonb_typeof(r.evidence->'subjects_processed')='number'
                      and (r.evidence->>'subjects_processed')::integer=q.enrichment_subject_count
                      and j.scheduled_for=q.enrichment_scheduled_for
                      and j.mode=q.enrichment_mode
                 )
            """)
            if fetchone_required(cur.fetchone(), "deal-history enrichment receipt binding")[0] != 0:
                fail("deal-history slice exists without its Thursday enrichment receipt/count")

            # A new rule cannot activate on prose alone.
            cur.execute("select id from actor where slug='joe'")
            actor = fetchone_required(cur.fetchone(), "Joe actor")[0]
            cur.execute("""
                insert into rule(statement,human_quote,taught_by,status)
                values ('control-plane fixture','fixture',%s,'proposed') returning id
            """, (actor,))
            rule_id = fetchone_required(cur.fetchone(), "fixture rule")[0]
            cur.execute("savepoint admission_refusal")
            try:
                cur.execute("update rule set status='active',activated_by=%s,activated_at=now() where id=%s",
                            (actor, rule_id))
                fail("rule activated without an admitted contract")
            except psycopg.Error:
                cur.execute("rollback to savepoint admission_refusal")
            cur.execute("savepoint admission_insert_refusal")
            try:
                cur.execute("""
                    insert into rule(statement,human_quote,taught_by,status,activated_by,activated_at)
                    values ('direct active fixture','fixture',%s,'active',%s,now())
                """, (actor,actor))
                fail("rule inserted active without an admitted contract")
            except psycopg.Error:
                cur.execute("rollback to savepoint admission_insert_refusal")

            cur.execute("""
                insert into ops.guidance_intake
                  (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
                values ('rule','human','db-gate','control-plane fixture','normalized',
                        '{"enforcement_class":"machine_enforceable"}',%s)
                returning id
            """, (actor,))
            intake_id = fetchone_required(cur.fetchone(), "fixture guidance intake")[0]
            cur.execute("""
                insert into ops.rule_admission
                  (rule_id,guidance_intake_id,enforcement_class,binding_moment,
                   applicability,projection,reachability,input_contract,fixture_refs,
                   state,admitted_by,admitted_at)
                values (%s,%s,'machine_enforceable','before fixture action',
                        '{"workflows":["db-gate"]}',
                        '{"targets":["db-gate"]}', '{"paths":["database"]}',
                        '{"type":"object"}',
                        array['ops/control-plane-db-gate.py'],'admitted',%s,now())
            """, (rule_id, intake_id, actor))
            cur.execute("""
                insert into ops.rule_enforcement_point
                  (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed)
                values (%s,'db-gate-fixture','migration:0148',
                        'ops/control-plane-db-gate.py','transactional_schema',true)
            """, (rule_id,))
            # Exercise the trigger as its real firing role, not as owner. A
            # grant that exists only for the migration actor is not a control.
            # The Neon owner credential is intentionally not standing SET-role
            # enabled for runtime bundles. Enable it inside this transaction
            # only, switch to the firing role, then the final rollback erases
            # the temporary membership option along with every fixture.
            grant_settable_runtime_roles(cur, "carr_writer", "carr_jobs")
            set_local_role(cur, "carr_writer")
            cur.execute("update rule set status='active',activated_by=%s,activated_at=now() where id=%s",
                        (actor, rule_id))
            cur.execute("reset role")
            cur.execute("select status from rule where id=%s", (rule_id,))
            if fetchone_required(cur.fetchone(), "activated fixture rule")[0] != "active":
                fail("admitted rule did not activate")

            # Disabling a definition is a database-level dispatch fence.  It
            # cancels queued/retry work with immutable evidence, and both claim
            # functions must ignore the old version even when called by an old
            # worker checkout.
            fenced_definition = f"db-gate-fenced-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,
                   legacy_schedule)
                values (%s,1,true,'green','deterministic','{"entrypoint":"fixture"}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"fixture-fenced"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (fenced_definition,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'canary')).id",
                        (fenced_definition, "2026-08-15T11:59:00Z",
                         '{"fixture":"must-not-run"}', f"fixture-fenced-{uuid.uuid4()}"))
            fenced_job = fetchone_required(cur.fetchone(), "fenced fixture enqueue")[0]
            cur.execute("reset role")
            cur.execute("update ops.job_definition set enabled=false where key=%s and version=1",
                        (fenced_definition,))
            cur.execute("select state,attempt,last_failure_class from ops.job where id=%s",
                        (fenced_job,))
            if fetchone_required(cur.fetchone(), "disabled definition job state") != (
                    "cancelled", 0, "definition_disabled"):
                fail("definition disable did not cancel queued canary work before dispatch")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and attempt=0 "
                        "and kind='override' and evidence->>'failure_class'='definition_disabled'",
                        (fenced_job,))
            if fetchone_required(cur.fetchone(), "definition fence receipt")[0] != 1:
                fail("definition disable did not persist one immutable fencing receipt")
            set_local_role(cur, "carr_jobs")
            cur.execute("select * from ops.claim_job_mode('db-gate-old-worker','canary',1,30)")
            if cur.fetchone() is not None:
                fail("old worker claimed a job for a disabled definition")
            cur.execute("reset role")

            # One enqueue identity, one row, even when two schedulers fire it.
            definition = f"db-gate-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,
                   legacy_schedule)
                values (%s,1,true,'green','deterministic','{"entrypoint":"fixture"}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"fixture"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (definition,))
            scheduled = "2026-08-15T12:00:00Z"
            args = (definition, 1, scheduled, '{"fixture":true}', "fixture-idem", "shadow")
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,%s,%s,%s,%s,%s)).id", args)
            first = fetchone_required(cur.fetchone(), "first enqueue")[0]
            cur.execute("select (ops.enqueue_job(%s,%s,%s,%s,%s,%s)).id", args)
            second = fetchone_required(cur.fetchone(), "idempotent enqueue")[0]
            if first != second:
                fail("idempotent enqueue produced two jobs")
            duplicate_delivery = (definition, 1, scheduled, '{"fixture":true}',
                                  "fixture-idem-from-second-scheduler", "shadow")
            cur.execute("select (ops.enqueue_job(%s,%s,%s,%s,%s,%s)).id", duplicate_delivery)
            if fetchone_required(cur.fetchone(), "duplicate scheduler enqueue")[0] != first:
                fail("independent scheduler delivery produced a second scheduled job")

            cur.execute("select (ops.claim_job('db-gate-worker',1,30)).*")
            claim = fetchone_required(cur.fetchone(), "fixture job claim")
            if claim[0] != first:
                fail("dispatcher did not claim the queued fixture")
            lease_token = claim[1]
            cur.execute("reset role")

            # Provider health is a finite routing predicate owned by code. An
            # unavailable primary is skipped and the eligible secondary remains.
            cur.execute("""insert into ops.provider_route
                         (route_key,priority,endpoint_ref,monthly_budget_usd)
                       values ('gate-primary',9001,'env:GATE_PRIMARY',0.05),
                              ('gate-secondary',9002,'env:GATE_SECONDARY',1.00),
                              ('gate-third',9003,'env:GATE_THIRD',1.00)""")
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.record_provider_observation('gate-primary','unavailable',null,'synthetic',300,'db-gate')")
            cur.execute("select ops.record_provider_observation('gate-secondary','healthy',10,null,300,'db-gate')")
            cur.execute("select route_key from ops.select_provider_routes(array['gate-primary','gate-secondary'])")
            if [r[0] for r in cur.fetchall()] != ["gate-secondary"]:
                fail("provider health did not route around unavailable primary")
            cur.execute("reset role")

            # Cache data is proposal-only, provider-neutral, and invalidatable
            # by exact canonical dependency before its TTL expires.
            cur.execute("""insert into ops.cognition_job
                         (key,version,input_schema_version,output_schema_version,input_schema,
                          output_schema,max_tokens,max_cost_usd,timeout_seconds,provider_routes)
                       values ('db-gate-cognition',1,1,1,'{"type":"object"}',
                               '{"type":"object"}',100,1.0,30,array['gate-secondary'])""")
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.put_cognition_cache('gate-cache','db-gate-cognition',1,1,%s,array['party:P-1'],300)",
                        ('{"route":"gate-secondary","proposal":{}}',))
            cur.execute("select ops.get_cognition_cache('gate-cache')")
            if fetchone_required(cur.fetchone(), "fresh cognition cache")[0] is None:
                fail("fresh cognition cache entry was not readable")
            cur.execute("select ops.invalidate_cognition_cache('party:P-1')")
            if fetchone_required(cur.fetchone(), "cache invalidation")[0] != 1:
                fail("cache dependency invalidation did not name one entry")
            cur.execute("select ops.get_cognition_cache('gate-cache')")
            if fetchone_required(cur.fetchone(), "invalidated cognition cache")[0] is not None:
                fail("invalidated cache entry remained readable")

            # Cost is reserved before provider dispatch and settled against the
            # live lease. A configured monthly ceiling is a durable pre-
            # dispatch refusal, not a rollback-erased exception.
            cur.execute("select ops.reserve_job_cost(%s,%s,'gate-secondary',0.10)",
                        (first, lease_token))
            reservation = fetchone_required(cur.fetchone(), "cost reservation")[0]
            cur.execute("select ops.settle_job_cost(%s,%s,%s,10,5,0.08)",
                        (reservation, first, lease_token))
            if not fetchone_required(cur.fetchone(), "cost settlement")[0]:
                fail("admitted cost reservation did not settle")
            cur.execute("select ops.reserve_job_cost(%s,%s,'gate-third',0.10)",
                        (first, lease_token))
            released_reservation = fetchone_required(cur.fetchone(), "failover cost reservation")[0]
            cur.execute("select ops.release_job_cost(%s,%s,%s)",
                        (released_reservation, first, lease_token))
            if not fetchone_required(cur.fetchone(), "cost reservation release")[0]:
                fail("failed provider reservation was not released for failover")
            cur.execute("select admitted,reservation_id,refusal_id,reason "
                        "from ops.admit_job_cost(%s,%s,'gate-primary',0.10)",
                        (first, lease_token))
            admitted, refused_reservation, refusal_id, refusal_reason = fetchone_required(
                cur.fetchone(), "durable budget refusal")
            if admitted is not False or refused_reservation is not None or refusal_id is None \
                    or refusal_reason != "monthly_budget_exceeded":
                fail("monthly provider budget did not return a typed refusal")
            cur.execute("select count(*) from ops.cost_refusal where id=%s and job_id=%s and attempt=1 "
                        "and route_key='gate-primary' and reason='monthly_budget_exceeded'",
                        (refusal_id, first))
            if fetchone_required(cur.fetchone(), "budget refusal evidence")[0] != 1:
                fail("budget refusal did not persist immutable evidence")
            cur.execute("select refusal_count,refused_estimated_cost_usd from ops.v_cost_refusal_metric "
                        "where month=date_trunc('month',now()) and route_key='gate-primary' "
                        "and reason='monthly_budget_exceeded'")
            metric = fetchone_required(cur.fetchone(), "budget refusal metric")
            if metric[0] != 1 or float(metric[1]) != 0.10:
                fail("budget refusal metric did not report the durable event")

            cur.execute("select ops.heartbeat_job(%s,%s,30)", (first, lease_token))
            if not fetchone_required(cur.fetchone(), "lease heartbeat")[0]:
                fail("lease heartbeat was refused")
            cur.execute("savepoint wrong_lease")
            try:
                cur.execute("select ops.complete_job(%s,%s,'{}','fixture')", (first, uuid.uuid4()))
                fail("wrong lease token completed a job")
            except psycopg.Error:
                cur.execute("rollback to savepoint wrong_lease")
            cur.execute("select ops.complete_job(%s,%s,%s::jsonb,'fixture')",
                        (first, lease_token, '{"ok":true}'))
            cur.execute("select state from ops.job where id=%s", (first,))
            if fetchone_required(cur.fetchone(), "completed job state")[0] != "succeeded":
                fail("job did not reach succeeded")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s", (first,))
            if fetchone_required(cur.fetchone(), "successful job receipt count")[0] != 1:
                fail("successful job did not produce exactly one receipt")
            cur.execute("reset role")

            # Shadow, canary, and live replacements at one scheduled instant
            # are distinct ledger identities.  Per-mode scheduler idempotency
            # must not collapse the evidence required for cutover.
            set_local_role(cur, "carr_jobs")
            mode_jobs = []
            for mode in ("shadow", "canary", "live"):
                cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,%s)).id",
                            (definition, "2026-08-15T12:00:15Z", '{"fixture":"mode-identity"}',
                             f"fixture-mode-{mode}-{uuid.uuid4()}", mode))
                mode_jobs.append(fetchone_required(cur.fetchone(), f"{mode} mode enqueue")[0])
            if len(set(mode_jobs)) != 3:
                fail("mode-specific schedules collapsed to one ledger job")
            cur.execute("reset role")
            cur.execute("update ops.job set next_attempt_at='2099-01-01T00:00:00Z' where id=any(%s)",
                        (mode_jobs,))

            # A normal failure reaches retry_wait with a failure receipt, then
            # the same ledger job is reclaimed under a fresh lease and can
            # complete.  It must never become a second scheduler delivery.
            retry_key = f"fixture-retry-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition, "2026-08-15T12:00:20Z", '{"fixture":"retry"}', retry_key))
            retry_job = fetchone_required(cur.fetchone(), "retry fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-retry-a','shadow',1,30)")
            retry_claim = fetchone_required(cur.fetchone(), "retry first claim")
            if retry_claim[0] != retry_job:
                fail("dispatcher did not claim retry fixture")
            cur.execute("select ops.fail_job(%s,%s,'fixture_failure','first attempt')",
                        (retry_job, retry_claim[1]))
            if fetchone_required(cur.fetchone(), "retry failure state")[0] != "retry_wait":
                fail("ordinary failure did not enter retry_wait")
            cur.execute("reset role")
            cur.execute("select state,attempt,lease_token from ops.job where id=%s", (retry_job,))
            if fetchone_required(cur.fetchone(), "retry release state") != ("retry_wait", 1, None):
                fail("retry_wait retained a live lease or wrong attempt number")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and attempt=1 and kind='failure'",
                        (retry_job,))
            if fetchone_required(cur.fetchone(), "retry failure receipt")[0] != 1:
                fail("ordinary failure did not create one immutable failure receipt")
            cur.execute("update ops.job set next_attempt_at=now()-interval '1 second' where id=%s", (retry_job,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select * from ops.claim_job_mode('db-gate-retry-b','shadow',1,30)")
            retry_second = fetchone_required(cur.fetchone(), "retry reclaim")
            if retry_second[0] != retry_job or retry_second[1] == retry_claim[1]:
                fail("retry did not reclaim the same job with a new lease token")
            cur.execute("select ops.complete_job(%s,%s,%s::jsonb,'fixture:retry-complete')",
                        (retry_job, retry_second[1], '{"ok":true}'))
            cur.execute("reset role")
            cur.execute("select state,attempt from ops.job where id=%s", (retry_job,))
            if fetchone_required(cur.fetchone(), "retry completed state") != ("succeeded", 2):
                fail("retried job did not complete as attempt two")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and kind in ('failure','completion')",
                        (retry_job,))
            if fetchone_required(cur.fetchone(), "retry receipt chain")[0] != 2:
                fail("retry completion did not retain both failure and completion receipts")

            # Produce a real canary completion receipt.  Human acceptance may
            # name this evidence, but arbitrary receipt strings must not open
            # a legacy cutover.
            canary_key = f"fixture-canary-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'canary')).id",
                        (definition, "2026-08-15T12:00:30Z", '{"fixture":"canary"}', canary_key))
            canary_job = fetchone_required(cur.fetchone(), "canary fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-canary','canary',1,30)")
            canary_claim = fetchone_required(cur.fetchone(), "canary fixture claim")
            if canary_claim[0] != canary_job:
                fail("dispatcher did not claim canary fixture")
            cur.execute("select ops.complete_job(%s,%s,%s::jsonb,'fixture:canary')",
                        (canary_job, canary_claim[1], '{"ok":true}'))

            # A recurring shadow adapter must not steal queued live or canary
            # work merely because it happens to wake first.
            isolated_jobs = []
            for mode, instant in (("live", "2026-08-15T12:00:31Z"),
                                  ("canary", "2026-08-15T12:00:32Z")):
                key = f"fixture-{mode}-isolation-{uuid.uuid4()}"
                cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,%s)).id",
                            (definition, instant, '{"fixture":"mode-isolation"}', key, mode))
                isolated_jobs.append(fetchone_required(cur.fetchone(), f"{mode} isolation enqueue")[0])
            cur.execute("select * from ops.claim_job_mode('db-gate-shadow','shadow',1,30)")
            if cur.fetchone() is not None:
                fail("shadow worker claimed queued live or canary work")
            cur.execute("reset role")
            cur.execute("select count(*) from ops.job where id=any(%s) and state='queued'", (isolated_jobs,))
            if fetchone_required(cur.fetchone(), "mode-isolated queued jobs")[0] != 2:
                fail("shadow mode claim changed queued live/canary work")

            # An abandoned final lease is terminal evidence, not a silent state
            # flip. Reaping it must produce an immutable dead-letter receipt.
            expired_key = f"fixture-expired-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition, "2026-08-15T12:01:00Z", '{"fixture":"expired"}', expired_key))
            expired_job = fetchone_required(cur.fetchone(), "expired fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-expiry','shadow',1,30)")
            expired_claim = fetchone_required(cur.fetchone(), "expired fixture claim")
            if expired_claim[0] != expired_job:
                fail("dispatcher did not claim expiry fixture")
            cur.execute("reset role")
            cur.execute("update ops.job set attempt=max_attempts,leased_until=now()-interval '1 second' where id=%s",
                        (expired_job,))
            set_local_role(cur, "carr_jobs")
            cur.execute("savepoint expired_worker_refusal")
            try:
                cur.execute("select ops.fail_job(%s,%s,'late','late worker')",
                            (expired_job,expired_claim[1]))
                fail("expired worker lease was allowed to mutate job state")
            except psycopg.Error:
                cur.execute("rollback to savepoint expired_worker_refusal")
            cur.execute("select ops.reap_expired_jobs()")
            if fetchone_required(cur.fetchone(), "expired job reap")[0] != 1:
                fail("expired lease was not reaped")
            cur.execute("reset role")
            cur.execute("select state from ops.job where id=%s", (expired_job,))
            if fetchone_required(cur.fetchone(), "expired job state")[0] != "dead_lettered":
                fail("exhausted expired lease did not dead-letter")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and kind='dead_letter'",
                        (expired_job,))
            if fetchone_required(cur.fetchone(), "dead letter receipt count")[0] != 1:
                fail("expired final lease did not produce one dead-letter receipt")

            # A retryable expired lease is also durable evidence.  It must
            # retain a timeout receipt before it returns to retry_wait.
            reaper_retry_key = f"fixture-reaper-retry-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition, "2026-08-15T12:01:30Z", '{"fixture":"reaper-retry"}', reaper_retry_key))
            reaper_retry_job = fetchone_required(cur.fetchone(), "reaper retry enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-reaper-retry','shadow',1,30)")
            reaper_retry_claim = fetchone_required(cur.fetchone(), "reaper retry claim")
            if reaper_retry_claim[0] != reaper_retry_job:
                fail("dispatcher did not claim retryable-expiry fixture")
            cur.execute("reset role")
            cur.execute("update ops.job set leased_until=now()-interval '1 second' where id=%s", (reaper_retry_job,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.reap_expired_jobs()")
            if fetchone_required(cur.fetchone(), "retryable lease reap")[0] != 1:
                fail("retryable expired lease was not reaped")
            cur.execute("reset role")
            cur.execute("select state from ops.job where id=%s", (reaper_retry_job,))
            if fetchone_required(cur.fetchone(), "retryable lease state")[0] != "retry_wait":
                fail("retryable expired lease did not return to retry_wait")
            cur.execute("select count(*) from ops.job_receipt where job_id=%s and attempt=1 and kind='timeout'",
                        (reaper_retry_job,))
            if fetchone_required(cur.fetchone(), "retryable lease timeout receipt")[0] != 1:
                fail("retryable expired lease lacks immutable timeout receipt")

            # A subprocess/provider deadline is distinct from a generic
            # failure and remains visible on the immutable attempt.
            timeout_key = f"fixture-timeout-{uuid.uuid4()}"
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (definition,"2026-08-15T12:02:00Z",'{"fixture":"timeout"}',timeout_key))
            timeout_job = fetchone_required(cur.fetchone(), "timeout fixture enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-timeout','shadow',1,30)")
            timeout_claim = fetchone_required(cur.fetchone(), "timeout fixture claim")
            if timeout_claim[0] != timeout_job:
                fail("dispatcher did not claim timeout fixture")
            cur.execute("select ops.timeout_job(%s,%s,'fixture deadline')",
                        (timeout_job,timeout_claim[1]))
            if fetchone_required(cur.fetchone(), "timed-out job state")[0] != "retry_wait":
                fail("timed-out attempt did not enter retry policy")
            cur.execute("reset role")
            cur.execute("select state,failure_class from ops.job_attempt where job_id=%s",
                        (timeout_job,))
            if cur.fetchone() != ("timed_out","execution_timeout"):
                fail("timeout attempt evidence was not preserved")

            # Retirement remains shut until accepted shadow AND canary receipts.
            cur.execute("savepoint early_cutover")
            try:
                set_local_role(cur, "carr_writer")
                cur.execute("select ops.disable_legacy_schedule(%s,'surface','locator','too early',"
                            "'native:enabled','native:disabled',null,null,null,null,'db-gate')", (definition,))
                fail("routine writer disabled a legacy schedule")
            except psycopg.Error:
                cur.execute("rollback to savepoint early_cutover")
            set_local_role(cur, "carr_writer")
            cur.execute("savepoint machine_acceptance_refusal")
            try:
                cur.execute("select ops.record_workflow_acceptance(%s,'shadow','accepted','fixture:bad')",
                            (definition,))
                fail("machine actor accepted workflow evidence")
            except psycopg.Error:
                cur.execute("rollback to savepoint machine_acceptance_refusal")
            cur.execute("savepoint fabricated_evidence_refusal")
            try:
                cur.execute("select ops.record_workflow_acceptance(%s,'shadow','accepted','fixture:made-up')",
                            (definition,))
                fail("fabricated receipt reference accepted for cutover")
            except psycopg.Error:
                cur.execute("rollback to savepoint fabricated_evidence_refusal")
            # A real accepted-cutover success requires an externally provisioned
            # carr_authority_joe/dell login DSN.  This owner-session fixture
            # proves only the negative: an unmapped DB session is refused.
            cur.execute("savepoint authority_actor_mismatch")
            try:
                cur.execute("select ops.record_workflow_acceptance(%s,'shadow','accepted','fixture')", (definition,))
                fail("unmapped database session was accepted as human authority")
            except psycopg.Error:
                cur.execute("rollback to savepoint authority_actor_mismatch")
            cur.execute("reset role")

            # carr_authority_joe/dell are externally provisioned LOGIN roles.
            # SET SESSION AUTHORIZATION is superuser-only on managed Postgres,
            # so an owner-driven disposable rebuild must not pretend to be
            # either partner.  The owner mismatch above is this gate's only
            # live authority result.  Positive Joe/Dell identity acceptance
            # requires an externally provisioned real authority-DSN probe;
            # this disposable owner gate does not perform one.

        conn.rollback()
    print("control-plane-db-gate passed: admission, leases, idempotency, receipts and owner cutover refusal exercised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
