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

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role

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
    "ops.cognition_result_cache", "ops.cognition_cache_observation", "ops.workflow_acceptance",
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
    "ops.get_cognition_cache_for_job(uuid,uuid,text)",
    "ops.put_cognition_cache_for_job(uuid,uuid,text,text,integer,integer,jsonb,text[],integer)",
    "ops.invalidate_cognition_cache_for_job(uuid,uuid,text)",
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

    with rollback_only_connection(dsn) as conn:
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

            # Renewal source ingress is a separate capability: an externally
            # provisioned LOGIN attestor, paired with one NOLOGIN bundle. The
            # generic jobs identity must not be able to turn a mutable cache
            # into a signed source receipt.
            cur.execute("""
                select
                  (select not rolcanlogin from pg_roles where rolname='carr_renewal_source_attestors'),
                  has_function_privilege('carr_renewal_source_attestors',
                    'ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb)'::regprocedure,
                    'execute'),
                  has_function_privilege('carr_jobs',
                    'ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb)'::regprocedure,
                    'execute'),
                  has_function_privilege('carr_jobs',
                    'ops.seal_renewal_decision_source_run(uuid,uuid)'::regprocedure,'execute')
            """)
            renewal_acl = fetchone_required(cur.fetchone(), "renewal source attestor ACL")
            if tuple(renewal_acl) != (True, True, False, False):
                fail("renewal signed ingress is not confined to its exact attestor capability")
            cur.execute("""
                select pg_get_functiondef(
                  'ops.ingest_renewal_signed_snapshot(uuid,uuid,uuid,text,text,timestamptz,text,text,jsonb)'::regprocedure)
            """)
            renewal_function = str(fetchone_required(
                cur.fetchone(), "renewal source ingress definition")[0]).replace(" ", "").lower()
            if ("session_user<>'carr_renewal_source_attestor'" not in renewal_function
                    or "pg_has_role(session_user,'carr_renewal_source_attestors','member')" not in renewal_function):
                fail("renewal signed ingress does not require exact attestor session and bundle")

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
            # A snapshot carries 0194 in its migration ledger but historically
            # omitted its mutable catalog seeds. 0228 must restore exactly the
            # two reviewed global controls before attempting semantic binding.
            cur.execute("""
                select count(*) from ops.enforcement_control_catalog
                 where (control_key='human_authority_runtime'
                        and implementation_ref='migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js'
                        and test_ref='mcp-server/test/control-plane-authority-boundary.test.mjs; ops/control-plane-authority-runtime-preflight-selftest.py'
                        and enforcement_class='transactional_schema'
                        and installed and verified_at is not null)
                    or (control_key='platform_metering_pre_dispatch'
                        and implementation_ref='lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py'
                        and test_ref='ops/platform-metering-gate-selftest.py; ops/platform-metering-policy-selftest.py; ops/guard-selftest.py'
                        and enforcement_class='deny_gate'
                        and installed and verified_at is not null)
            """)
            if fetchone_required(cur.fetchone(), "forward control catalog restoration")[0] != 2:
                fail("0228 did not restore the two exact reviewed control catalog rows")
            # The exact spending rule and decision were captured before the
            # atomic approval architecture existed. Deployment must bind their
            # pinned preimages to the cost gate; a familiar UUID with different
            # words or governance decision must never be blessed.
            cur.execute("""
                insert into rule(id,statement,human_quote,taught_by,status,scope)
                values ('a57d981a-8f6d-4c18-95ee-0e63a5a90b89',
                        'Every metered CARR execution must pass a machine-enforced pre-dispatch budget gate; prose or registry-only guidance does not count as enforcement, and Joe alone may approve exceeding a cap, buying usage credits, or enabling paid overage.',
                        'fixture',%s,'proposed',
                        '{"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}'::jsonb)
            """, (actor,))
            cur.execute("""
                insert into event
                  (id,occurred_at,actor_id,verb,subject_type,subject_id,new_value,
                   cause,human_quote,agent_rationale,idempotency_key)
                values
                  ('f7ea060c-268b-47f1-8a17-7168841b77e0',now(),%s,
                   'log-decision','decision','8b31938a-e2f2-4b8f-9c29-187efa5c1650',
                   jsonb_build_object(
                     'title','Make cost discipline permanent; expire only the temporary emergency restriction',
                     'quote_absent',false,'provenance','rollback DB gate fixture'),
                   'human_stated',
                   'But also, we want a budget rule in affect going forward not just expiring in September. We need to operate the system with cost in mind. Not to the point where it limits the system but just to the point where excessive spending is avoided',
                   'exact pinned decision fixture','db-gate-cost-decision')
            """, (actor,))
            cur.execute("""
                insert into record_source(entity_type,entity_id,source_system,external_key)
                values ('event','f7ea060c-268b-47f1-8a17-7168841b77e0',
                        'decision-history','fixture#db-gate-cost-binding')
            """)
            cur.execute("select ops.sync_system_rule_control_bindings()")
            if fetchone_required(cur.fetchone(), "system-rule binding sync")[0] != 1:
                fail("existing spending rule did not receive its exact installed-control binding")
            cur.execute("""
                select count(*)
                  from ops.rule_control_binding b
                  join rule r on r.id=b.rule_id
                 where b.rule_id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'
                   and b.control_key='platform_metering_pre_dispatch'
                   and b.statement_hash=encode(digest(r.statement,'sha256'),'hex')
                   and b.binding_contract->>'durable_decision_ref'=
                       '8b31938a-e2f2-4b8f-9c29-187efa5c1650'
                   and b.binding_contract->>'decision_event_ref'=
                       'f7ea060c-268b-47f1-8a17-7168841b77e0'
            """)
            if fetchone_required(cur.fetchone(), "system-rule binding readback")[0] != 1:
                fail("spending rule binding does not match the exact statement and decision")
            for savepoint, mutation, params, message in (
                ("narrowed_system_rule_scope",
                 "update rule set scope='{\"workflows\":[\"one-workflow\"]}'::jsonb "
                 "where id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'",
                 (), "system-rule sync accepted narrowed applicability"),
                ("personal_system_rule_audience",
                 "update rule set personal_to=%s "
                 "where id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'",
                 (actor,), "system-rule sync accepted a personal audience"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    cur.execute(mutation, params)
                    cur.execute("select ops.sync_system_rule_control_bindings()")
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
            cur.execute("savepoint wrong_system_rule_preimage")
            try:
                cur.execute("""
                    insert into rule(id,statement,human_quote,taught_by,status)
                    values ('ae44e0c0-e773-456c-a85b-2dc4cf4dd49e',
                            'wrong governance statement','fixture',%s,'proposed')
                """, (actor,))
                cur.execute("select ops.sync_system_rule_control_bindings()")
                fail("system-rule sync accepted a known UUID with the wrong statement")
            except psycopg.Error:
                cur.execute("rollback to savepoint wrong_system_rule_preimage")
            grant_settable_runtime_roles(cur, "carr_writer")
            set_local_role(cur, "carr_writer")
            cur.execute("select has_function_privilege(current_user,%s,'execute')",
                        ("ops.sync_system_rule_control_bindings()",))
            if fetchone_required(cur.fetchone(), "system-rule binding ACL")[0] is not False:
                fail("routine writer can install semantic rule bindings")
            cur.execute("reset role")
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
                  (rule_id,guidance_intake_id,enforcement_class,enforcement_status,binding_moment,
                   applicability,projection,reachability,input_contract,fixture_refs,
                   state,admitted_by,admitted_at)
                values (%s,%s,'machine_enforceable','hard_enforced','before fixture action',
                        '{"workflows":["db-gate"]}',
                        '{"targets":["db-gate"]}', '{"paths":["database"]}',
                        '{"type":"object"}',
                        array['ops/control-plane-db-gate.py'],'admitted',%s,now())
            """, (rule_id, intake_id, actor))
            cur.execute("""
                insert into ops.enforcement_control_catalog
                  (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                values ('db-gate-fixture','migration:0194',
                        'ops/control-plane-db-gate.py','transactional_schema',true,now())
                on conflict (control_key) do update set installed=true,verified_at=now()
            """)
            cur.execute("""
                insert into ops.rule_control_binding
                  (rule_id,control_key,statement_hash,binding_contract)
                select id,'db-gate-fixture',encode(digest(statement,'sha256'),'hex'),
                       '{"fixture":"control-plane-db-gate"}'::jsonb
                  from rule where id=%s
            """, (rule_id,))
            cur.execute("""
                insert into ops.rule_enforcement_point
                  (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                values (%s,'db-gate-fixture','migration:0148',
                        'ops/control-plane-db-gate.py','transactional_schema',true,now())
            """, (rule_id,))
            cur.execute("""
                with approved as (
                  select r.id,r.version,encode(digest(r.statement,'sha256'),'hex') statement_hash,
                         jsonb_build_object(
                           'fixture','control-plane-db-gate',
                           'binding_moment','before fixture action',
                           'applicability','{"workflows":["db-gate"]}'::jsonb,
                           'projection','{"targets":["db-gate"]}'::jsonb,
                           'reachability','{"paths":["database"]}'::jsonb,
                           'input_contract','{"type":"object"}'::jsonb) contract
                    from rule r where r.id=%s
                )
                insert into ops.rule_approval_receipt
                  (idempotency_key,rule_id,rule_version,statement_hash,actor_id,policy_kind,
                   enforcement_status,requested_control_keys,installed_control_keys,reason,
                   normalized_contract,contract_hash,evidence_refs)
                select 'db-gate-approval:'||id::text,id,version+1,statement_hash,%s,
                       'machine_enforceable','hard_enforced',array['db-gate-fixture'],
                       array['db-gate-fixture'],'rollback-only enforced activation fixture',
                       contract,encode(digest(contract::text,'sha256'),'hex'),
                       array['ops/control-plane-db-gate.py']
                  from approved
            """, (rule_id, actor))
            cur.execute("""
                insert into ops.authority_receipt
                  (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
                   contract_hash,evidence_refs)
                select 'approval:'||ar.idempotency_key,'activation','rule',ar.rule_id,
                       ar.actor_id,'rollback-only exact approval fixture',
                       ar.contract_hash,ar.evidence_refs
                  from ops.rule_approval_receipt ar where ar.rule_id=%s
            """, (rule_id,))
            # Exercise the trigger as its real firing role, not as owner. A
            # grant that exists only for the migration actor is not a control.
            # The Neon owner credential is intentionally not standing SET-role
            # enabled for runtime bundles. Enable it inside this transaction
            # only, switch to the firing role, then the final rollback erases
            # the temporary membership option along with every fixture.
            grant_settable_runtime_roles(cur, "carr_writer", "carr_jobs")
            set_local_role(cur, "carr_writer")
            cur.execute("update rule set status='active',activated_by=%s,activated_at=now(),enforcement='gate' where id=%s",
                        (actor, rule_id))
            cur.execute("reset role")
            cur.execute("select status from rule where id=%s", (rule_id,))
            if fetchone_required(cur.fetchone(), "activated fixture rule")[0] != "active":
                fail("admitted rule did not activate")
            cur.execute("savepoint active_rule_drift_refusal")
            try:
                set_local_role(cur, "carr_writer")
                cur.execute("update rule set statement=statement||' drift' where id=%s", (rule_id,))
                fail("active rule statement changed under an old approval receipt")
            except psycopg.Error:
                cur.execute("rollback to savepoint active_rule_drift_refusal")
            finally:
                cur.execute("reset role")
            cur.execute("""
                select r.version=ar.rule_version
                  and encode(digest(r.statement,'sha256'),'hex')=ar.statement_hash
                  from rule r join ops.rule_approval_receipt ar on ar.rule_id=r.id
                 where r.id=%s
            """, (rule_id,))
            if fetchone_required(cur.fetchone(), "active rule immutable preimage")[0] is not True:
                fail("active rule version/hash no longer matches its approval receipt")
            cur.execute("select count(*) from ops.applicable_rules('db-gate',null,null) where rule_id=%s",
                        (rule_id,))
            if fetchone_required(cur.fetchone(), "receipt-bound applicable rule")[0] != 1:
                fail("exact active enforced rule is absent from the policy compiler")
            # 0228 may preserve an OLD 0194 pre-activation receipt only through
            # its exact migration-time anchor. A fresh post-0228 approval is
            # already post-version and must never be made to look legacy; nor
            # may a caller claim a matching receipt with a substituted hash.
            for savepoint, sql_text, message in (
                ("fresh_receipt_anchor_refusal", """
                    insert into ops.rule_approval_lifecycle_anchor
                      (approval_receipt_id,rule_id,rule_version_after,statement_hash)
                    select ar.id,ar.rule_id,ar.rule_version,ar.statement_hash
                      from ops.rule_approval_receipt ar where ar.rule_id=%s
                """, "fresh post-version approval was accepted as a legacy anchor"),
                ("mismatched_anchor_refusal", """
                    insert into ops.rule_approval_lifecycle_anchor
                      (approval_receipt_id,rule_id,rule_version_after,statement_hash)
                    select ar.id,ar.rule_id,ar.rule_version+1,repeat('0',64)
                      from ops.rule_approval_receipt ar where ar.rule_id=%s
                """, "mismatched legacy anchor was accepted"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    cur.execute(sql_text, (rule_id,))
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
            for savepoint, sql_text, message in (
                ("active_admission_drift_refusal",
                 "update ops.rule_admission set applicability='{}'::jsonb where rule_id=%s",
                 "active rule admission changed under an old approval receipt"),
                ("active_control_removal_refusal",
                 "update ops.rule_enforcement_point set installed=false where rule_id=%s",
                 "active rule enforcement point was removed under an old approval receipt"),
                ("retirement_preimage_drift_refusal",
                 "update rule set status='retired',statement=statement||' drift' where id=%s",
                 "approved rule substance changed during retirement"),
                ("unreceipted_retirement_refusal",
                 "update rule set status='retired' where id=%s",
                 "routine writer retired an approved rule without Joe authority"),
                ("unreceipted_deactivation_refusal",
                 "update rule set status='proposed' where id=%s",
                 "routine writer deactivated an approved rule without Joe authority"),
                ("approved_rule_noop_update_refusal",
                 "update rule set statement=statement where id=%s",
                 "routine writer invalidated an approved rule through a no-op version bump"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    set_local_role(cur, "carr_writer")
                    cur.execute(sql_text, (rule_id,))
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
                finally:
                    cur.execute("reset role")

            # A proposed rule has no approval receipt, but its retirement is
            # still a permanent tombstone. Build one through the same receipt
            # precondition then prove a routine writer cannot alter any
            # tombstone field or revive it.
            tombstone_rule = fetchone_required(cur.execute("""
                insert into rule(statement,human_quote,taught_by,status)
                values ('retired rule fixture','fixture',%s,'proposed') returning id
            """, (actor,)).fetchone(), "retired proposed fixture")[0]
            tombstone_at = fetchone_required(cur.execute("""
                insert into ops.rule_retirement_receipt
                  (idempotency_key,rule_id,rule_version_before,rule_version_after,
                   statement_hash,previous_status,actor_id,reason,contract_hash,retired_at)
                select 'db-gate-retirement:'||id::text,id,version,version+1,
                       encode(digest(statement,'sha256'),'hex'),'proposed',%s,
                       'rollback-only retired tombstone fixture',
                       encode(digest('{}'::text,'sha256'),'hex'),now()
                  from rule where id=%s returning retired_at
            """, (actor, tombstone_rule)).fetchone(), "retired proposed receipt")[0]
            cur.execute("""update rule set status='retired',retired_by=%s,retired_at=%s
                           where id=%s""", (actor, tombstone_at, tombstone_rule))
            for savepoint, sql_text, message in (
                ("retired_rule_mutation_refusal",
                 "update rule set statement=statement||' drift' where id=%s",
                 "routine writer changed retired rule statement"),
                ("retired_rule_scope_mutation_refusal",
                 "update rule set scope='{\"workflows\":[\"drift\"]}'::jsonb where id=%s",
                 "routine writer changed retired rule scope"),
                ("retired_rule_actor_mutation_refusal",
                 "update rule set retired_by=null where id=%s",
                 "routine writer changed retired rule actor"),
                ("retired_rule_timestamp_mutation_refusal",
                 "update rule set retired_at=now() where id=%s",
                 "routine writer changed retired rule timestamp"),
                ("retired_rule_revival_refusal",
                 "update rule set status='proposed' where id=%s",
                 "routine writer revived a retired rule"),
            ):
                cur.execute(f"savepoint {savepoint}")
                try:
                    set_local_role(cur, "carr_writer")
                    cur.execute(sql_text, (tombstone_rule,))
                    fail(message)
                except psycopg.Error:
                    cur.execute(f"rollback to savepoint {savepoint}")
                finally:
                    cur.execute("reset role")

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

            # Cache reads and writes from a running cognition job are durable,
            # immutable evidence.  A deterministic job or an arbitrary table
            # insert cannot forge the workflow/job/attempt/mode binding.
            cur.execute("reset role")
            cache_definition = f"db-gate-cache-{uuid.uuid4()}"
            cur.execute("""
                insert into ops.job_definition
                  (key,version,enabled,risk,execution_kind,execution_contract,
                   recurrence,retry_policy,deduplication,completion_contract,legacy_schedule)
                values (%s,1,true,'green','cognition','{"cognition_job":"db-gate-cognition"}',
                        '{"cron":"* * * * *","timezone":"UTC"}',
                        '{"max_attempts":2,"base_seconds":1,"cap_seconds":2,"timeout_seconds":30,"backoff":"exponential"}',
                        '{"key_template":"cache-observation-fixture"}',
                        '{"predicate":"fixture","receipt_kind":"fixture"}',
                        '{"status":"enabled"}')
            """, (cache_definition,))
            set_local_role(cur, "carr_jobs")
            cur.execute("select (ops.enqueue_job(%s,1,%s,%s,%s,'shadow')).id",
                        (cache_definition, "2026-08-15T12:01:00Z", '{}',
                         f"cache-observation-{uuid.uuid4()}"))
            cache_job = fetchone_required(cur.fetchone(), "cache observation job enqueue")[0]
            cur.execute("select * from ops.claim_job_mode('db-gate-cache-worker','shadow',1,30)")
            cache_claim = fetchone_required(cur.fetchone(), "cache observation job claim")
            if cache_claim[0] != cache_job:
                fail("cache observation worker claimed the wrong job")
            cache_lease = cache_claim[1]
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "cache miss observation") != ("miss", None):
                fail("cache miss was not measured before provider dispatch")
            cur.execute("select ops.put_cognition_cache_for_job(%s,%s,'gate-cache-observed',"
                        "'db-gate-cognition',1,1,%s,array['party:P-2'],300)",
                        (cache_job, cache_lease, '{"route":"gate-secondary","proposal":{}}'))
            if not fetchone_required(cur.fetchone(), "cache store observation")[0]:
                fail("cache store was refused for a live cognition lease")
            for bad_key, bad_version, bad_schema in (("db-gate-cognition", 2, 1),
                                                     ("db-gate-cognition", 1, 2),
                                                     ("wrong-cognition", 1, 1)):
                cur.execute("savepoint bad_cache_contract")
                try:
                    cur.execute("select ops.put_cognition_cache_for_job(%s,%s,'bad-cache',%s,%s,%s,'{}',array[]::text[],300)",
                                (cache_job, cache_lease, bad_key, bad_version, bad_schema))
                    fail("wrong cognition contract wrote a cache entry")
                except psycopg.Error:
                    cur.execute("rollback to savepoint bad_cache_contract")
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            cache_hit = fetchone_required(cur.fetchone(), "cache hit observation")
            if cache_hit[0] != "hit" or cache_hit[1] is None:
                fail("cache hit did not return measured proposal evidence")
            cur.execute("reset role")
            cur.execute("update ops.cognition_result_cache set output_schema_version=2 where cache_key='gate-cache-observed'")
            set_local_role(cur, "carr_jobs")
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "mismatched cache contract")[0] != "miss":
                fail("mismatched cache entry produced a hit")
            cur.execute("reset role")
            cur.execute("update ops.cognition_result_cache set output_schema_version=1 where cache_key='gate-cache-observed'")
            set_local_role(cur, "carr_jobs")
            cur.execute("select ops.invalidate_cognition_cache_for_job(%s,%s,'party:P-2')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "cache invalidation observation")[0] != 1:
                fail("cache invalidation did not persist one bound observation")
            cur.execute("select cache_state,proposal from ops.get_cognition_cache_for_job(%s,%s,'gate-cache-observed')",
                        (cache_job, cache_lease))
            if fetchone_required(cur.fetchone(), "cache invalidated observation")[0] != "invalidated":
                fail("invalidated cache state was not measured")
            cur.execute("""select observation_kind,workflow_key,workflow_version,mode
                             from ops.cognition_cache_observation
                            where job_id=%s and attempt=1 and cache_key='gate-cache-observed'
                            order by observed_at,observation_kind""", (cache_job,))
            cache_evidence = [tuple(row) for row in cur.fetchall()]
            if {row[0] for row in cache_evidence} != {"miss", "store", "hit", "invalidate", "invalidated"} \
                    or any(row[1:] != (cache_definition, 1, "shadow") for row in cache_evidence):
                fail("cache observations did not bind exact workflow and mode")
            cur.execute("reset role")
            cur.execute("select has_table_privilege('carr_jobs','ops.cognition_cache_observation','insert'),"
                        "has_table_privilege('carr_jobs','ops.cognition_cache_observation','update'),"
                        "has_table_privilege('carr_jobs','ops.cognition_cache_observation','delete')")
            if fetchone_required(cur.fetchone(), "cache observation jobs ACL") != (False, False, False):
                fail("jobs role can directly rewrite cache observations")
            set_local_role(cur, "carr_jobs")
            cur.execute("savepoint cache_wrong_job")
            try:
                cur.execute("select cache_state from ops.get_cognition_cache_for_job(%s,%s,'forbidden')",
                            (first, lease_token))
                fail("deterministic job could create a cache observation")
            except psycopg.Error:
                cur.execute("rollback to savepoint cache_wrong_job")

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

    print("control-plane-db-gate passed: admission, leases, idempotency, receipts and owner cutover refusal exercised")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
