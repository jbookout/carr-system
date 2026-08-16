#!/usr/bin/env python3
"""Operate the CARR job ledger; provider schedulers are adapters, never owners.

Commands:
  validate                 validate the tracked workflow/cognition registry
  sync                     register provider routes, cognition jobs and workflows
  schedule [--at ISO]      idempotently enqueue workflows due at an exact minute
  run-once [--worker NAME] claim and execute one ledger job
  metrics                  print aggregate job state/cost JSON

Routine ledger commands require CARR_DB_JOBS_URL and verify their database
identity as carr_jobs (or an explicitly provisioned jobs identity).  ``sync``
is the separate authority/bootstrap path and continues to take DATABASE_URL.
No command accepts a model name or canonical-write instruction.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import unquote, urlsplit
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lib.control_plane import deterministic_args, validate_manifest  # noqa: E402
from lib.control_plane_content_fuel import (ContentFuelContractError,
                                            validate_content_fuel_proposal,
                                            validate_rotation_policy)  # noqa: E402
from lib.control_plane_facts import CompositeFactCollector, evaluate_stage, fact_envelope  # noqa: E402
from lib.control_plane_inputs import build_input  # noqa: E402
from lib.control_plane_proposal_contracts import (ProposalContractError,
                                                  validate_proposal_contract)  # noqa: E402
from lib.control_plane_runner import BudgetExceeded, CognitionDispatcher, due_workflows  # noqa: E402
from lib.control_plane_runtime_collectors import RuntimeCanonicalEvidenceCollector  # noqa: E402
from lib.control_plane_scheduler_cutover import CutoverRefusal, scheduler_surface_rows  # noqa: E402

MANIFEST_PATH = REPO / "ops" / "config" / "control-plane-workflows.v1.json"
SCHEDULER_CUTOVER_REGISTRY_PATH = REPO / "ops" / "config" / "control-plane-scheduler-cutover.v1.json"


def load_manifest() -> dict[str, Any]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    errors = validate_manifest(data, repo=REPO)
    if errors:
        raise SystemExit("invalid control-plane manifest:\n  " + "\n  ".join(errors))
    return data


def load_scheduler_cutover_registry() -> dict[str, Any]:
    data = json.loads(SCHEDULER_CUTOVER_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("scheduler cutover registry must be an object")
    return data


def sync_scheduler_surface_registry(cur: Any, *, manifest: dict[str, Any], registry: dict[str, Any]) -> int:
    """Reconcile the canonical scheduler surface projection after definition sync.

    This runs inside the same authority transaction as ``ops.job_definition``
    synchronization.  Validation completes before the first registry write;
    hence an incomplete evolution cannot prune a previously usable surface.
    """
    try:
        rows = scheduler_surface_rows(registry, manifest=manifest)
    except CutoverRefusal as exc:
        raise RuntimeError(str(exc)) from exc
    for workflow_key, workflow_version, surface_id, locator, scheduler_kind in rows:
        cur.execute(
            """insert into ops.legacy_schedule_surface_registry
                 (workflow_key,workflow_version,surface_id,locator,scheduler_kind)
               values (%s,%s,%s,%s,%s)
               on conflict (surface_id) do update set
                 workflow_key=excluded.workflow_key,workflow_version=excluded.workflow_version,
                 locator=excluded.locator,scheduler_kind=excluded.scheduler_kind""",
            (workflow_key, workflow_version, surface_id, locator, scheduler_kind),
        )
    cur.execute(
        "delete from ops.legacy_schedule_surface_registry where not (surface_id = any(%s))",
        ([row[2] for row in rows],),
    )
    return len(rows)


def _dsn_login(value: str) -> str:
    """Return the decoded login portion of a PostgreSQL URI, if present."""
    try:
        return unquote(urlsplit(value).username or "").strip().lower()
    except ValueError:
        return ""


def _reject_non_jobs_dsn(value: str) -> None:
    # This is an early, legible guard; the session identity assertion below is
    # authoritative.  Do not let a plainly named owner/writer URL reach the
    # ledger just because it was assigned to the jobs environment variable.
    login = _dsn_login(value)
    if login in {"carr_writer", "carr_owner", "owner", "writer", "postgres"}:
        raise SystemExit("CARR_DB_JOBS_URL must not name an owner or writer login")


def database_url(*, routine: bool = True) -> str:
    name = "CARR_DB_JOBS_URL" if routine else "DATABASE_URL"
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    if routine:
        _reject_non_jobs_dsn(value)
    return value


def _assert_jobs_identity(conn: Any) -> None:
    # The ledger migration creates this login explicitly.  Never accept an
    # environment-supplied role name here: routine environments are exactly
    # what this boundary constrains.
    expected = {"carr_jobs"}
    with conn.cursor() as cur:
        cur.execute("select session_user, current_user")
        row = cur.fetchone()
    if not isinstance(row, (tuple, list)) or len(row) != 2:
        raise RuntimeError("could not verify jobs database identity")
    session_user, current_user = (str(value) for value in row)
    if session_user not in expected or current_user not in expected:
        raise RuntimeError("routine control-plane connection is not a provisioned jobs identity")


def connect(*, routine: bool = True, read_only: bool = False):
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("psycopg is required") from exc
    conn = psycopg.connect(database_url(routine=routine))
    if routine:
        _assert_jobs_identity(conn)
        if read_only:
            with conn.cursor() as cur:
                cur.execute("begin transaction read only")
    return conn


def sync_registry(manifest: dict[str, Any]) -> dict[str, int]:
    routes = sorted({route for c in manifest["cognition_jobs"]
                     for route in c["provider_routes"]})
    with connect(routine=False) as conn, conn.cursor() as cur:
        for priority, route in enumerate(routes, start=1):
            env_name = "CARR_AI_ROUTE_" + route.upper().replace("-", "_") + "_URL"
            cur.execute(
                """insert into ops.provider_route(route_key,priority,endpoint_ref)
                   values (%s,%s,%s) on conflict (route_key) do update set
                     priority=excluded.priority,endpoint_ref=excluded.endpoint_ref,updated_at=now()""",
                (route, priority, f"env:{env_name}"))
        for job in manifest["cognition_jobs"]:
            budget = job["budget"]
            cur.execute(
                """insert into ops.cognition_job
                     (key,version,input_schema_version,output_schema_version,input_schema,
                      output_schema,max_tokens,max_cost_usd,timeout_seconds,provider_routes,
                      cache_ttl_seconds,canonical_write_authority,active)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,true)
                   on conflict (key,version) do update set
                     input_schema_version=excluded.input_schema_version,
                     output_schema_version=excluded.output_schema_version,
                     input_schema=excluded.input_schema,output_schema=excluded.output_schema,
                     max_tokens=excluded.max_tokens,max_cost_usd=excluded.max_cost_usd,
                     timeout_seconds=excluded.timeout_seconds,
                     provider_routes=excluded.provider_routes,
                     cache_ttl_seconds=excluded.cache_ttl_seconds,active=true""",
                (job["key"],job["version"],job["input_schema_version"],
                 job["output_schema_version"],json.dumps(job["input_schema"]),
                 json.dumps(job["output_schema"]),budget["max_tokens"],
                 budget["max_cost_usd"],budget["timeout_seconds"],
                 job["provider_routes"],job.get("cache_ttl_seconds",0)))
        for workflow in manifest["workflows"]:
            execution = workflow["execution"]
            # The partial unique index permits one enabled version per key.
            # Lock superseded definitions before checking for running work.
            # Claim functions lock the same row, so either a claim wins and
            # sync refuses until that worker drains, or sync wins and no old
            # worker can obtain a new lease.  A database cannot undo an
            # external command after launch, so silently disabling a running
            # version would be false fencing.
            cur.execute(
                "select version from ops.job_definition "
                "where key=%s and version<>%s and enabled for update",
                (workflow["key"], workflow["version"]),
            )
            superseded = [int(row[0]) for row in cur.fetchall()]
            if superseded:
                cur.execute(
                    "select count(*) from ops.job where definition_key=%s "
                    "and definition_version=any(%s) and state='running'",
                    (workflow["key"], superseded),
                )
                running = int(cur.fetchone()[0])
                if running:
                    raise RuntimeError(
                        f"drain running jobs before superseding {workflow['key']}: {running} active")
            # Disable superseded versions in this same transaction before
            # installing the declared version; a failed insert rolls the
            # disable and its queued-job fencing receipts back with it.
            cur.execute(
                "update ops.job_definition set enabled=false,updated_at=now() "
                "where key=%s and version<>%s and enabled",
                (workflow["key"],workflow["version"]),
            )
            cur.execute(
                """insert into ops.job_definition
                     (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
                      inventory_contract,recurrence,state_contract,routing_contract,
                      filtering_contract,validation_contract,retry_policy,deduplication,
                      completion_contract,legacy_schedule)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict (key,version) do update set
                     enabled=excluded.enabled,risk=excluded.risk,owner_actor=excluded.owner_actor,
                     execution_kind=excluded.execution_kind,
                     execution_contract=excluded.execution_contract,
                     inventory_contract=excluded.inventory_contract,recurrence=excluded.recurrence,
                     state_contract=excluded.state_contract,routing_contract=excluded.routing_contract,
                     filtering_contract=excluded.filtering_contract,
                     validation_contract=excluded.validation_contract,
                     retry_policy=excluded.retry_policy,deduplication=excluded.deduplication,
                     completion_contract=excluded.completion_contract,
                     legacy_schedule=excluded.legacy_schedule,updated_at=now()""",
                (workflow["key"],workflow["version"],workflow["enabled"],workflow["risk"],
                 workflow.get("inventory",{}).get("owner","system"),execution["kind"],
                 json.dumps({k:v for k,v in execution.items() if k != "kind"}),
                 json.dumps(workflow.get("inventory",{})),json.dumps(workflow["recurrence"]),
                 json.dumps(workflow["state"]),json.dumps(workflow["routing"]),
                 json.dumps(workflow["filtering"]),json.dumps(workflow["validation"]),
                 json.dumps(workflow["retry"]),json.dumps(workflow["deduplication"]),
                 json.dumps(workflow["completion"]),json.dumps(workflow["legacy_schedule"])))
        # Migration 0176 creates an empty, FK-bound registry.  Populate it
        # only after every manifest job definition exists, in this transaction.
        scheduler_surfaces = sync_scheduler_surface_registry(
            cur, manifest=manifest, registry=load_scheduler_cutover_registry())
        conn.commit()
    return {"provider_routes": len(routes), "cognition_jobs": len(manifest["cognition_jobs"]),
            "workflows": len(manifest["workflows"]), "scheduler_surfaces": scheduler_surfaces}


def parse_instant(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc).replace(second=0, microsecond=0)
    value = raw.replace("Z", "+00:00")
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None:
        raise SystemExit("--at must carry a timezone")
    return instant.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _parse_utc_timestamp(value: Any) -> datetime | None:
    """Accept only explicit instants and normalize their comparison in UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.tzinfo is None:
        return None
    return instant.astimezone(timezone.utc)


def enqueue_due(manifest: dict[str, Any], instant: datetime,
                mode: str = "live") -> list[dict[str, str]]:
    if mode not in {"shadow", "canary", "live", "replay"}:
        raise ValueError(f"invalid job mode: {mode}")
    due = due_workflows(manifest, instant)
    # Resolve every deterministic canary contract before opening a database
    # transaction.  A live-equivalent command must not even produce a queued
    # canary job that could later be mistaken for acceptance evidence.
    if mode in {"canary", "replay"}:
        for workflow in due:
            if workflow.get("execution", {}).get("kind") == "deterministic":
                deterministic_args(workflow["execution"], mode)
    rows: list[dict[str, str]] = []
    with connect() as conn, conn.cursor() as cur:
        for workflow in due:
            key, version = workflow["key"], workflow["version"]
            idem = f"schedule:{mode}:{key}:v{version}:{instant.isoformat()}"
            payload = {"workflow_key": key, "scheduled_for": instant.isoformat()}
            cur.execute("select (ops.enqueue_job(%s,%s,%s,%s,%s,%s)).id",
                        (key,version,instant,json.dumps(payload),idem,mode))
            rows.append({"workflow": key, "job_id": str(cur.fetchone()[0])})
        conn.commit()
    return rows


class DatabaseCache:
    def __init__(self, cognition_key: str, cognition_version: int,
                 output_schema_version: int, dependency_refs: list[str]):
        self.cognition_key = cognition_key
        self.cognition_version, self.output_schema_version = cognition_version, output_schema_version
        self.dependency_refs = dependency_refs

    def get(self, key: str):
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select ops.get_cognition_cache(%s)", (key,))
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None

    def put(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select ops.put_cognition_cache(%s,%s,%s,%s,%s,%s,%s)",
                (key,self.cognition_key,self.cognition_version,self.output_schema_version,
                 json.dumps(value),self.dependency_refs,ttl_seconds))
            conn.commit()


class HttpProposalProvider:
    def call(self, route: str, contract: dict[str, Any], payload: Any) -> dict[str, Any]:
        stem = route.upper().replace("-", "_")
        endpoint = os.environ.get(f"CARR_AI_ROUTE_{stem}_URL", "").strip()
        if not endpoint:
            raise RuntimeError("provider endpoint is not configured")
        body = json.dumps({
            "job_type": contract["key"], "job_version": contract["version"],
            "input_schema_version": contract["input_schema_version"],
            "output_schema_version": contract["output_schema_version"],
            "input": payload, "budget": contract["budget"],
        }).encode()
        headers = {"content-type":"application/json"}
        token = os.environ.get(f"CARR_AI_ROUTE_{stem}_TOKEN", "").strip()
        if token:
            headers["authorization"] = f"Bearer {token}"
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=contract["budget"]["timeout_seconds"]) as response:
            return json.loads(response.read())


class RuntimeEvidenceCollector(RuntimeCanonicalEvidenceCollector):
    """Bind reviewed collectors to the current jobs-role connection."""
    def __init__(self, payload: dict[str, Any], *, mode: str):
        super().__init__(payload, mode=mode,
                         connect_factory=lambda: connect(read_only=True),
                         policy_path=REPO / "ops" / "config" / "control-plane-collector-policy.v1.json")


def _contract(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    for contract in manifest["cognition_jobs"]:
        if contract["key"] == key:
            return contract
    raise KeyError(key)


def _validate_workflow_proposal(workflow: dict[str, Any], input_payload: dict[str, Any],
                                proposal: dict[str, Any]) -> None:
    if workflow["key"] == "content-fuel-harvest-weekly":
        validate_content_fuel_proposal(input_payload, proposal)
        return
    validate_proposal_contract(workflow["key"], input_payload, proposal)


def _dependency_refs(payload: Any) -> list[str]:
    if isinstance(payload, dict) and isinstance(payload.get("dependency_refs"), list):
        return sorted({str(x) for x in payload["dependency_refs"] if str(x).strip()})
    return []


def _reserve(job_id, lease, route: str, estimate: float):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select admitted,reservation_id,refusal_id,reason "
                    "from ops.admit_job_cost(%s,%s,%s,%s)",
                    (job_id,lease,route,Decimal(str(estimate))))
        admitted, reservation, refusal, reason = cur.fetchone()
        conn.commit()
        if admitted is not True or reservation is None:
            if refusal is None or not isinstance(reason, str) or not reason:
                raise RuntimeError("cost admission returned an invalid refusal")
            raise BudgetExceeded(reason)
        return reservation


def _settle(reservation, job_id, lease, result: dict[str, Any]) -> None:
    usage = result["usage"]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select ops.settle_job_cost(%s,%s,%s,%s,%s,%s)",
                    (reservation,job_id,lease,usage.get("input_tokens",0),
                     usage.get("output_tokens",usage["total_tokens"]),
                     Decimal(str(usage["cost_usd"]))))
        conn.commit()


def _release(reservation, job_id, lease) -> None:
    if reservation is None:
        return
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select ops.release_job_cost(%s,%s,%s)", (reservation,job_id,lease))
        conn.commit()


def _observe_provider(route: str, *, status: str, latency_ms: int | None,
                      error: Exception | None, source_ref: str) -> None:
    """Persist bounded route health for the next ledger dispatch decision."""
    if status not in {"healthy", "degraded", "unavailable", "rate_limited"}:
        raise ValueError("unregistered provider observation status")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select ops.record_provider_observation(%s,%s,%s,%s,%s,%s)",
                    (route, status, latency_ms,
                     type(error).__name__ if error is not None else None,
                     300, source_ref))
        cur.fetchone()
        conn.commit()


def _try_observe_provider(route: str, *, status: str, latency_ms: int | None,
                          error: Exception | None, source_ref: str) -> bool:
    """Record route health without turning an observability outage into a second provider call.

    Provider output and cost settlement are business state. Route-health
    telemetry is still persisted when available, but a telemetry write failure
    must not discard a valid paid proposal or suppress failover from the
    original provider error. The false result is carried in completion evidence.
    """
    try:
        _observe_provider(route, status=status, latency_ms=latency_ms,
                          error=error, source_ref=source_ref)
    except Exception:
        return False
    return True


def _provider_failure_status(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return "rate_limited"
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return "unavailable"
    return "degraded"


def _workflow(manifest: dict[str, Any], key: str, version: int) -> dict[str, Any]:
    for workflow in manifest["workflows"]:
        if workflow["key"] == key and workflow["version"] == version:
            return workflow
    raise KeyError(f"{key} v{version}")


def _scheduled_for(payload: Any) -> datetime:
    if not isinstance(payload, dict) or not isinstance(payload.get("scheduled_for"), str):
        raise ValueError("job payload must contain scheduled_for ISO timestamp")
    return datetime.fromisoformat(payload["scheduled_for"].replace("Z", "+00:00"))


class RuntimeWorkflowFactCollector:
    """Derive normal workflow facts from the scheduler, source, and result.

    This is intentionally not a generic payload->true adapter.  Pre-dispatch
    facts come from the exact scheduled instant/registered execution or the
    canonical input evidence; post-dispatch facts come from the subprocess
    receipt or typed proposal envelope.  Device observations can supplement a
    fact for a signed-in collector, never stand in for these normal paths.
    """
    def __init__(self, workflow: dict[str, Any], payload: dict[str, Any], *,
                 input_payload: dict[str, Any] | None = None,
                 execution: dict[str, Any] | None = None, mode: str = 'live',
                 receipt_ref: str | None = None,
                 receipt_evidence: dict[str, Any] | None = None):
        self.workflow, self.payload = workflow, payload
        self.instant = _scheduled_for(payload)
        self.input_payload = input_payload or {}
        self.execution, self.mode, self.receipt_ref = execution, mode, receipt_ref
        self.receipt_evidence = receipt_evidence or {}

    def _registered_args(self) -> list[str] | None:
        execution = self.workflow.get('execution', {})
        try:
            return deterministic_args(execution, self.mode)
        except RuntimeError:
            return None

    def _command_identity_valid(self, evidence: Any) -> bool:
        if not isinstance(evidence, dict):
            return False
        args = self._registered_args()
        if args is None or evidence.get('entrypoint') != self.workflow['execution'].get('entrypoint'):
            return False
        return evidence.get('mode') == self.mode and evidence.get('args') == args

    def _command_evidence_valid(self, evidence: Any) -> bool:
        """Validate finite command evidence against the registered workflow."""
        if not self._command_identity_valid(evidence) or not isinstance(evidence, dict):
            return False
        if evidence.get('exit_code') != 0:
            return False
        text = evidence.get('stdout_tail')
        if not isinstance(text, str):
            return False
        markers: dict[tuple[str, str], tuple[str, ...]] = {
            ('calendar-fetch-daily', 'shadow'): (r'calendar-pull: source=calendar .* posted=\d+ duplicate=\d+ failed=0 unparseable=\d+',),
            ('calendar-fetch-daily', 'canary'): (r'calendar-pull: source=calendar mode=canary destination=[^\s]+ .* posted=\d+ duplicate=\d+ failed=0 unparseable=\d+',),
            ('calendar-fetch-daily', 'live'): (r'calendar-pull: source=calendar .* posted=\d+ duplicate=\d+ failed=0 unparseable=\d+',),
            ('nightly-record-layer', 'shadow'): (r'nightly preflight: \d+ chain surfaces present; writes=0',),
            ('nightly-record-layer', 'canary'): (r'nightly result: chain_ok',),
            ('nightly-record-layer', 'live'): (r'nightly result: chain_ok',),
            ('notes-sweep-hourly', 'shadow'): (r'notes-sweep shadow: scanned=\d+ unposted=\d+ writes=0 posts=0',),
            ('notes-sweep-hourly', 'canary'): (r'notes-sweep: source=.+ mode=canary destination=[^\s]+ posted=\d+ duplicate=\d+ failed=0 still_queued=0',),
            ('notes-sweep-hourly', 'live'): (r'notes-sweep: source=.+ posted=\d+ duplicate=\d+ failed=0 still_queued=0',),
            ('restore-rehearse-weekly', 'shadow'): (r'PREFLIGHT OK — every check that runs before anything is created has passed\.', r'Nothing was created, decrypted or charged for\.'),
            ('restore-rehearse-weekly', 'canary'): (r'RESTORE REHEARSAL: PASS',),
            ('restore-rehearse-weekly', 'live'): (r'RESTORE REHEARSAL: PASS',),
        }
        required = markers.get((self.workflow['key'], self.mode))
        return required is not None and all(re.search(marker, text) is not None for marker in required)

    def _audit_value(self, fact: str) -> bool:
        facts = self.input_payload.get('facts')
        if not isinstance(facts, dict):
            return False
        if fact == 'capability.candidate_admitted':
            candidate = facts.get('candidate')
            return isinstance(candidate, dict) and candidate.get('admission_state') == 'admitted'
        if fact == 'capability.single_bounded_candidate':
            candidate = facts.get('candidate')
            return (isinstance(candidate, dict) and isinstance(candidate.get('id'), str)
                    and bool(candidate['id']) and candidate.get('scope') in {'single', 'bounded'})
        if fact == 'mutation.production_absent':
            return facts.get('requested_mutation') == 'none'
        if fact == 'runner.identity_bound':
            return isinstance(facts.get('runner_identity'), str) and facts['runner_identity'].startswith('worker:')
        if fact == 'release.version_change_recorded':
            release = facts.get('release')
            return isinstance(release, dict) and all(isinstance(release.get(k), str) and release[k] for k in ('from', 'to')) and release['from'] != release['to']
        if fact == 'release.newer_than_last_accepted_audit':
            release = facts.get('release')
            if not isinstance(release, dict):
                return False
            released_at = _parse_utc_timestamp(release.get('released_at'))
            last_accepted_at = _parse_utc_timestamp(release.get('last_accepted_at'))
            return released_at is not None and last_accepted_at is not None and released_at > last_accepted_at
        if fact in {'health.monthly_receipt_absent', 'health.one_run_in_monthly_window'}:
            # Both predicates are the same tested ledger condition at different
            # stages: no immutable completion receipt exists in this month's
            # window. The job idempotency key then admits exactly one run.
            return facts.get('monthly_receipt_state') == 'absent'
        if fact in {'health.live_evidence', 'health.registry_evidence', 'health.artifact_evidence'}:
            evidence = facts.get('evidence')
            needed = fact.split('.', 1)[1].replace('_evidence', '')
            return isinstance(evidence, dict) and isinstance(evidence.get(needed), list) and bool(evidence[needed])
        if fact == 'loops.system_owned_actionable_exist':
            actions = facts.get('actions')
            return isinstance(actions, list) and any(isinstance(x, dict) and x.get('owner') == 'system' and x.get('state') == 'actionable' for x in actions)
        if fact == 'loops.no_human_counterparty_or_event_blocker':
            actions = facts.get('actions')
            return isinstance(actions, list) and bool(actions) and all(isinstance(x, dict) and x.get('counterparty') is None and x.get('event_blocker') is None for x in actions)
        if fact == 'playbook.monthly_receipt_absent':
            return facts.get('monthly_receipt_state') == 'absent'
        if fact == 'playbook.sweep_receipt_present':
            return facts.get('sweep_receipt_state') == 'present'
        if fact == 'playbook.due_policy_sections':
            return isinstance(facts.get('due_sections'), list) and bool(facts['due_sections'])
        if fact == 'playbook.measured_failure_evidence':
            return isinstance(facts.get('failure_evidence_refs'), list) and bool(facts['failure_evidence_refs'])
        if fact == 'system_sweep.monthly_receipt_absent': return facts.get('monthly_receipt_state') == 'absent'
        if fact == 'system_sweep.measured_stale_duplicate_or_oversized':
            candidates = facts.get('candidates')
            return isinstance(candidates, list) and bool(candidates) and all(isinstance(x, dict) and x.get('measurement') in {'stale','duplicate','oversized'} for x in candidates)
        return False

    def _pre_value(self, fact: str) -> bool:
        local = self.instant.astimezone(__import__('zoneinfo').ZoneInfo(
            self.workflow['recurrence']['timezone']))
        if fact.endswith('.weekday') or fact.endswith('.weekday_slot'):
            return local.weekday() < 5
        if fact == 'notes.business_hour_weekday':
            return local.weekday() < 5 and 8 <= local.hour < 18
        if fact == 'calendar.non_interactive_credential':
            # Calendar dry runs use local ICS files; live invokes a registered
            # noninteractive endpoint.  No credential value is inspected.
            return self.mode == 'shadow' or bool(os.environ.get('CARR_CALENDAR_INGEST_URL'))
        if fact == 'restore.non_interactive_credential':
            return bool(os.environ.get('NEON_API_KEY') or os.environ.get('CARR_AGE_IDENTITY'))
        if fact == 'restore.encrypted_dump_exists':
            return any((REPO / 'backups').glob('*.age'))
        if fact == 'notes.canonical_schedule_owner':
            return self.workflow['state'].get('owner') == 'ops.job'
        if fact == 'nightly.one_instance_per_local_date':
            # Idempotent schedule identity is established by the ledger before
            # this runner claims it; the payload is read back from that row.
            return bool(self.payload.get('scheduled_for'))
        if self.workflow['execution']['kind'] == 'cognition':
            audit = self._audit_value(fact)
            if audit: return True
            subjects = self.input_payload.get('subjects')
            lanes = self.input_payload.get('lanes')
            posts = self.input_payload.get('source_posts')
            if fact == 'enrichment.reverification_due_nonempty': return isinstance(subjects, list) and bool(subjects) and all(isinstance(x, dict) and x.get('current_verification_status') == 'not_current' and x.get('reverification_due') in {'expired','unstamped_volatile'} for x in subjects)
            if fact == 'enrichment.exactly_40_prioritized': return isinstance(subjects, list) and len(subjects) == 40 and all(isinstance(x, dict) and isinstance(x.get('priority'), int) for x in subjects)
            if fact == 'enrichment.reverification_priority_ordered': return isinstance(subjects, list) and bool(subjects) and all(isinstance(x, dict) for x in subjects) and [x.get('priority') for x in subjects] == list(range(1, len(subjects) + 1))
            if fact == 'deal_history.unverified_counterparties_exist': return isinstance(subjects, list) and bool(subjects) and all(isinstance(x, dict) and x.get('verification') == 'unverified' for x in subjects)
            if fact == 'deal_history.slice_size_within_policy':
                cap = self.input_payload.get('slice_limit')
                return isinstance(subjects, list) and isinstance(cap, int) and cap in (15, 25) and 1 <= len(subjects) <= cap
            if fact == 'content_fuel.weekly_receipt_absent': return self.input_payload.get('previous_receipt_state') == 'absent'
            if fact == 'content_fuel.local_and_next_cold_lane':
                try:
                    validate_rotation_policy(self.input_payload)
                except ContentFuelContractError:
                    return False
                return True
            if fact == 'npi.weekly_delta_unprocessed': return self.input_payload.get('delta_state') == 'unprocessed'
            if fact == 'npi.territory_predicate': return isinstance(lanes, list) and bool(lanes) and all(isinstance(x, dict) and x.get('territory_match') is True for x in lanes)
            if fact == 'npi.healthcare_provider_predicate': return isinstance(lanes, list) and bool(lanes) and all(isinstance(x, dict) and x.get('entity_type') == 'healthcare_provider' for x in lanes)
            if fact == 'radar.weekly_receipt_absent': return self.input_payload.get('previous_receipt_state') == 'absent'
            if fact == 'radar.lanes_code_scored': return isinstance(lanes, list) and bool(lanes) and all(isinstance(x, dict) and isinstance(x.get('score'), (int,float)) for x in lanes)
            if fact == 'radar.freshness_guard': return isinstance(lanes, list) and bool(lanes) and all(isinstance(x, dict) and x.get('fresh') is True for x in lanes)
            if fact == 'radar.overdue_pool': return isinstance(lanes, list) and any(isinstance(x, dict) and x.get('overdue') is True for x in lanes)
            if fact == 'idea.monthly_receipt_absent': return self.input_payload.get('previous_receipt_state') == 'absent'
            if fact == 'idea.oldest_or_least_recently_surfaced':
                ideas, surfaced = self.input_payload.get('ideas'), self.input_payload.get('last_surfaced')
                return isinstance(ideas, list) and bool(ideas) and isinstance(surfaced, dict) and all(str(x) in surfaced for x in ideas)
            if fact in {'linkedin.collector_available','x.collector_available'}: return self.input_payload.get('collector_state') == 'available'
            if fact == 'linkedin.post_count_in_range': return isinstance(posts, list) and 3 <= len(posts) <= 5 and all(isinstance(x, dict) and isinstance(x.get('url'), str) and x['url'] for x in posts)
            if fact == 'x.fresh_in_lane_posts': return isinstance(posts, list) and 1 <= len(posts) <= 20 and all(isinstance(x, dict) and isinstance(x.get('url'), str) and x['url'] for x in posts)
            if fact == 'linkedin.network_priority': return isinstance(posts, list) and bool(posts) and all(isinstance(x, dict) and x.get('network_priority') is True for x in posts)
            if fact == 'x.actual_post_read': return isinstance(posts, list) and bool(posts) and all(isinstance(x, dict) and x.get('read_at') for x in posts)
            if fact == 'x.no_duplicate_source': return isinstance(posts, list) and len({x.get('url') for x in posts if isinstance(x, dict)}) == len(posts)
            if fact == 'social.next_week_uncovered': return self.input_payload.get('coverage_state') == 'uncovered'
            if fact in {'social.cadence','social.topic_rotation','social.no_replies'}:
                brief = self.input_payload
                required = {'social.cadence':'cadence','social.topic_rotation':'topic_rotation','social.no_replies':'reply_mode'}[fact]
                expected = 'no_replies' if fact == 'social.no_replies' else 'valid'
                return brief.get(required) == expected
            if fact == 'metrics.current_platform_windows': return isinstance(self.input_payload.get('platform_exports'), list) and all(isinstance(x, dict) and x.get('window_current') is True for x in self.input_payload['platform_exports'])
            if fact == 'metrics.owned_accounts': return isinstance(self.input_payload.get('platform_exports'), list) and bool(self.input_payload['platform_exports']) and all(isinstance(x, dict) and x.get('owned_account') is True for x in self.input_payload['platform_exports'])
            if fact == 'metrics.placements_in_window': return isinstance(self.input_payload.get('platform_exports'), list) and bool(self.input_payload['platform_exports']) and all(isinstance(x, dict) and x.get('placement_in_window') is True for x in self.input_payload['platform_exports'])
        # Every other non-temporal fact needs a dedicated canonical/device
        # collector.  Failing closed is intentional; do not translate a queue
        # read, successful command, or scheduled timestamp into semantic truth.
        return False

    def _post_value(self, fact: str) -> bool:
        if self.receipt_ref:
            # Completion is proven from the immutable ledger readback, not the
            # runner's in-memory result.  Cognition may prove only that its
            # typed proposal was persisted without write authority; acceptance
            # remains a separate human-gated cutover condition.
            receipt = self.receipt_evidence
            cognition = receipt.get('cognition') if isinstance(receipt, dict) else None
            proposal = receipt.get('proposal') if isinstance(receipt, dict) else None
            typed = (isinstance(cognition, dict) and isinstance(cognition.get('key'), str)
                     and isinstance(cognition.get('version'), int)
                     and isinstance(cognition.get('output_schema_version'), int)
                     and isinstance(proposal, dict))
            if fact == 'proposal.receipt_persisted':
                return typed
            if fact == 'proposal.no_canonical_write_authority':
                if not isinstance(cognition, dict) or not isinstance(proposal, dict):
                    return False
                return (typed and cognition.get('canonical_write_authority') is False
                        and 'canonical_write' not in proposal and 'mutation' not in proposal)
            if fact == 'command.receipt_persisted':
                return self._command_evidence_valid(receipt)
            if fact == 'command.execution_evidence_reconciles':
                return (self._command_evidence_valid(receipt) and isinstance(self.execution, dict)
                        and all(receipt.get(field) == self.execution.get(field)
                                for field in ('entrypoint', 'mode', 'args', 'exit_code', 'stdout_tail')))
            # Other deterministic completion facts are never implied by a receipt.
            return False
        if not self.execution:
            return False
        if 'exit_code' in self.execution:
            if fact == 'command.registered_args_selected':
                return self._command_identity_valid(self.execution)
            if fact == 'command.exit_zero':
                return self.execution.get('exit_code') == 0
            if fact == 'command.workflow_marker_valid':
                return self._command_evidence_valid(self.execution)
            text = self.execution.get('stdout_tail', '')
            if fact == 'calendar.summary_failed_zero': return 'failed=0' in text
            if fact == 'restore.pass_line': return 'REHEARSAL PASS' in text
            if fact == 'restore.throwaway_branch_deleted': return 'branch deleted' in text.lower()
            if fact == 'notes.summary_and_durable_receipt_reconcile':
                return 'failed=0' in text and 'still_queued=0' in text
            if fact == 'notes.unposted_call_recordings_only':
                return 'notes-sweep shadow:' in text and 'writes=0 posts=0' in text
            if fact == 'nightly.versioned_chain_steps_only':
                return 'nightly preflight:' in text and 'writes=0' in text
            return False
        proposal = self.execution.get('proposal')
        if not isinstance(proposal, dict): return False
        # Shape itself is not proof of a semantic claim.  These are the only
        # proposal facts that have an exact, model-neutral structural meaning.
        if fact in ('proposal.worktree','proposal.tests','proposal.risks','proposal.next_human_action'):
            return isinstance(proposal.get(fact.split('.',1)[1]), (str,list,dict)) and bool(proposal.get(fact.split('.',1)[1]))
        findings = proposal.get('findings')
        drafts = proposal.get('drafts')
        candidates = proposal.get('candidates')
        if fact == 'release.every_action_has_source': return isinstance(findings, list) and bool(findings) and all(isinstance(x, dict) and isinstance(x.get('source_refs'), list) and bool(x['source_refs']) for x in findings)
        if fact == 'enrichment.every_finding_has_source_observed_at_status': return isinstance(findings, list) and all(isinstance(x, dict) and isinstance(x.get('source_ref'), str) and x.get('observed_at') and x.get('status') in {'verified','unverified','cannot_verify'} for x in findings)
        if fact == 'deal_history.identity_sources_direct': return isinstance(findings, list) and all(isinstance(x, dict) and x.get('source_class') == 'direct_identity' for x in findings)
        if fact == 'deal_history.discrepancies_proposal_only': return isinstance(findings, list) and all(isinstance(x, dict) and x.get('action') == 'propose' for x in findings)
        if fact in {'health.live_evidence','health.registry_evidence','health.artifact_evidence'}: return isinstance(findings, list) and any(isinstance(x, dict) and x.get('evidence_class') == fact.split('.',1)[1].replace('_evidence','') for x in findings)
        if fact == 'loops.proposal_has_evidence': return isinstance(proposal.get('proposed_actions'), list) and bool(proposal['proposed_actions']) and all(isinstance(x, dict) and isinstance(x.get('evidence_refs'), list) and bool(x['evidence_refs']) for x in proposal['proposed_actions'])
        if fact == 'loops.inside_data_class_grant': return isinstance(proposal.get('proposed_actions'), list) and all(isinstance(x, dict) and x.get('data_class_grant') == 'granted' for x in proposal['proposed_actions'])
        if fact == 'idea.shortlist_references_canonical_rows': return isinstance(proposal.get('shortlist'), list) and all(isinstance(x, dict) and isinstance(x.get('canonical_row_ref'), str) for x in proposal['shortlist'])
        if fact in {'linkedin.draft_only','x.draft_only'}: return isinstance(drafts, list) and all(isinstance(x, dict) and x.get('action') == 'draft' for x in drafts)
        if fact in {'linkedin.voice_valid','x.voice_valid'}: return isinstance(drafts, list) and all(isinstance(x, dict) and isinstance(x.get('voice_version'), int) for x in drafts)
        if fact == 'linkedin.link_and_relationship': return isinstance(drafts, list) and all(isinstance(x, dict) and isinstance(x.get('source_url'), str) and isinstance(x.get('relationship'), str) for x in drafts)
        if fact == 'content_fuel.post_provider_contract':
            try:
                validate_content_fuel_proposal(self.input_payload, proposal)
            except ContentFuelContractError:
                return False
            return True
        if fact == 'proposal.input_reconciled_contract':
            try:
                _validate_workflow_proposal(self.workflow, self.input_payload, proposal)
            except (ContentFuelContractError, ProposalContractError):
                return False
            return True
        if fact in {'npi.source_rows', 'npi.input_reconciliation', 'npi.proposal_dedup'}:
            inputs = self.input_payload.get('npi_candidates')
            if not isinstance(candidates, list) or not candidates or not isinstance(inputs, list) or not inputs:
                return False
            input_pairs = {(item.get('npi'), item.get('source_ref')) for item in inputs if isinstance(item, dict)}
            if len(input_pairs) != len(inputs) or any(not isinstance(npi, str) or not isinstance(ref, str)
                                                      or not npi or not ref for npi, ref in input_pairs):
                return False
            proposal_pairs = {(item.get('npi'), item.get('source_row_ref')) for item in candidates if isinstance(item, dict)}
            if fact == 'npi.source_rows':
                return len(proposal_pairs) == len(candidates) and all(pair[1] for pair in proposal_pairs)
            if fact == 'npi.input_reconciliation':
                return (len(proposal_pairs) == len(candidates) and proposal_pairs <= input_pairs
                        and all(isinstance(item, dict) and item.get('action') == 'propose' for item in candidates))
            return len(proposal_pairs) == len(candidates) and len({pair[0] for pair in proposal_pairs}) == len(proposal_pairs)
        if fact == 'playbook.changes_proposal_only_until_gated': return isinstance(proposal.get('proposed_actions'), list) and all(isinstance(x, dict) and x.get('action') == 'propose' and x.get('approval_state') == 'required' for x in proposal['proposed_actions'])
        if fact == 'radar.lane_health_explicit': return isinstance(proposal.get('lane_health'), list) and all(isinstance(x, dict) and x.get('state') in {'healthy','warning','blocked'} for x in proposal['lane_health'])
        if fact == 'radar.candidates_proposal_only': return isinstance(candidates, list) and all(isinstance(x, dict) and x.get('action') == 'propose' for x in candidates)
        if fact == 'social.schema': return isinstance(drafts, list) and all(isinstance(x, dict) and isinstance(x.get('platform'), str) and isinstance(x.get('body'), str) for x in drafts)
        if fact == 'social.writing_lint': return isinstance(drafts, list) and all(isinstance(x, dict) and x.get('lint_state') == 'passed' for x in drafts)
        if fact == 'social.source_verification': return isinstance(drafts, list) and all(isinstance(x, dict) and isinstance(x.get('source_refs'), list) and bool(x['source_refs']) for x in drafts)
        if fact == 'social.format': return isinstance(drafts, list) and all(isinstance(x, dict) and x.get('format_valid') is True for x in drafts)
        if fact == 'social.publication_firewall': return isinstance(drafts, list) and all(isinstance(x, dict) and x.get('action') == 'draft' for x in drafts)
        if fact == 'metrics.numeric_types': return isinstance(proposal.get('measurements'), list) and bool(proposal['measurements']) and all(isinstance(x, dict) and isinstance(x.get('value'), (int,float)) and not isinstance(x.get('value'), bool) for x in proposal['measurements'])
        if fact == 'metrics.placement_ids': return isinstance(proposal.get('measurements'), list) and bool(proposal['measurements']) and all(isinstance(x, dict) and isinstance(x.get('placement_id'), str) and bool(x['placement_id']) for x in proposal['measurements'])
        if fact == 'metrics.source_timestamps': return isinstance(proposal.get('measurements'), list) and bool(proposal['measurements']) and all(isinstance(x, dict) and _parse_utc_timestamp(x.get('source_observed_at')) is not None for x in proposal['measurements'])
        if fact == 'system_sweep.every_destructive_proposal_recoverable': return isinstance(proposal.get('proposed_actions'), list) and all(isinstance(x, dict) and (not x.get('destructive') or x.get('recoverable') is True) for x in proposal['proposed_actions'])
        if fact == 'system_sweep.every_destructive_proposal_human_gated': return isinstance(proposal.get('proposed_actions'), list) and all(isinstance(x, dict) and (not x.get('destructive') or x.get('approval_state') == 'required') for x in proposal['proposed_actions'])
        if fact == 'x.draft_count_in_range': return isinstance(drafts, list) and 5 <= len(drafts) <= 10
        if fact == 'x.no_likes_follows_or_posts': return isinstance(drafts, list) and all(isinstance(x, dict) and x.get('action') == 'draft' for x in drafts)
        return False

    def collect(self, *, fact: str, workflow_key: str, stage: str):
        source = 'canonical_receipt' if self.receipt_ref else (
            'canonical_db' if self.workflow['execution']['kind'] == 'cognition' else 'read_only_command')
        ref = self.receipt_ref or (
            f"job:{workflow_key}:{self.payload['scheduled_for']}:{stage}")
        value = (self._post_value(fact) if self.execution is not None and stage in ('filtering', 'validation', 'completion')
                 else self._pre_value(fact))
        return (fact_envelope(fact, value, source_kind=source, source_ref=ref),)


def _workflow_fact_collector(workflow: dict[str, Any], payload: Any, **kwargs: Any) -> CompositeFactCollector:
    if not isinstance(payload, dict):
        raise ValueError("job payload must be an object")
    return CompositeFactCollector(RuntimeWorkflowFactCollector(workflow, payload, **kwargs))


def _execute_deterministic(workflow: dict[str, Any], payload: dict[str, Any],
                           timeout: int, mode: str) -> dict[str, Any]:
    execution = workflow["execution"]
    path = (REPO / execution["entrypoint"]).resolve()
    if REPO not in path.parents or not path.is_file():
        raise RuntimeError("deterministic entrypoint is outside the registered repository")
    # Deterministic children never inherit ledger, provider, owner or live-ingest
    # credentials.  Their explicit canary config is the sole credential seam.
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "CARR_VAULT",
                                             "CARR_CALENDAR_CANARY_ENV", "CARR_NOTES_CANARY_ENV",
                                             "CARR_CALENDAR_CANARY_ROOT", "CARR_NOTES_CANARY_ROOT")
           if os.environ.get(key)}
    env.update({"CARR_JOB_PAYLOAD": json.dumps(payload, sort_keys=True), "CARR_CONTROL_PLANE_MODE": mode})
    args = deterministic_args(execution, mode)
    proc = subprocess.run([str(path), *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(f"entrypoint exited {proc.returncode}")
    return {"entrypoint": execution["entrypoint"], "mode": mode,
            "args": args, "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-2000:]}


class LeaseKeeper:
    """Renew a committed job lease while external work is in flight."""
    def __init__(self, job_id: Any, lease_token: Any, *, seconds: int = 300,
                 interval: int = 60):
        self.job_id, self.lease_token = job_id, lease_token
        self.seconds, self.interval = seconds, interval
        self.stop = threading.Event()
        self.failure: Exception | None = None
        self.thread = threading.Thread(target=self._run, name=f"lease:{job_id}", daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                with connect() as conn, conn.cursor() as cur:
                    cur.execute("select ops.heartbeat_job(%s,%s,%s)",
                                (self.job_id,self.lease_token,self.seconds))
                    if not cur.fetchone()[0]:
                        raise RuntimeError("job lease heartbeat was refused")
                    conn.commit()
            except Exception as exc:  # surfaced synchronously at context exit
                self.failure = exc
                self.stop.set()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, _exc, _tb):
        self.stop.set()
        self.thread.join(timeout=5)
        if exc_type is None and self.failure is not None:
            raise RuntimeError(f"lease heartbeat failed: {self.failure}")
        return False


def _build_and_admit_cognition_input(
    manifest: dict[str, Any], workflow: dict[str, Any], claim: dict[str, Any]
) -> dict[str, Any]:
    """Build canonical input before evaluating any cognition routing predicate."""
    input_payload = build_input(
        manifest, workflow["execution"]["input_builder"],
        RuntimeEvidenceCollector(claim["payload"], mode=claim["mode"]),
        workflow_key=workflow["key"])
    facts = _workflow_fact_collector(
        workflow, claim["payload"], input_payload=input_payload, mode=claim["mode"])
    for stage in ("routing", "filtering"):
        if not evaluate_stage(workflow, stage, facts):
            raise RuntimeError(f"{workflow['key']}.{stage} predicate was not satisfied")
    return input_payload


def _post_execution_facts(
    workflow: dict[str, Any], claim: dict[str, Any], evidence: dict[str, Any],
    input_payload: dict[str, Any] | None, **receipt: Any
) -> CompositeFactCollector:
    """Carry the admitted typed input through validation and receipt completion."""
    return _workflow_fact_collector(
        workflow, claim["payload"], execution=evidence,
        input_payload=input_payload, mode=claim["mode"], **receipt)


def run_once(manifest: dict[str, Any], worker: str, mode: str | None = None) -> dict[str, Any]:
    with connect() as conn, conn.cursor() as cur:
        if mode is None:
            cur.execute("select * from ops.claim_job(%s,1,300)", (worker,))
        else:
            cur.execute("select * from ops.claim_job_mode(%s,%s,1,300)", (worker, mode))
        row = cur.fetchone()
        if not row:
            conn.commit()
            return {"claimed": 0}
        cols = [d.name for d in cur.description]
        claim = dict(zip(cols,row))
        job_id, lease = claim["job_id"], claim["lease_token"]
        # A lease must be visible to other workers and the reaper while the
        # payload runs. Never hold the claim transaction open across provider
        # I/O or a potentially hours-long deterministic entrypoint.
        conn.commit()
        try:
            workflow = _workflow(manifest,claim["definition_key"],claim["definition_version"])
            if workflow["execution"]["kind"] == "deterministic":
                facts = _workflow_fact_collector(workflow, claim["payload"], mode=claim["mode"])
                if not evaluate_stage(workflow, "routing", facts):
                    raise RuntimeError(f"{workflow['key']}.routing predicate was not satisfied")
            input_payload: dict[str, Any] | None = None
            with LeaseKeeper(job_id,lease):
                if claim["execution_kind"] == "deterministic":
                    evidence = _execute_deterministic(
                        workflow,claim["payload"],claim["timeout_seconds"],claim["mode"])
                else:
                    cognition = _contract(manifest,workflow["execution"]["cognition_job"])
                    input_payload = _build_and_admit_cognition_input(manifest, workflow, claim)
                # Health selection is a database predicate and is committed
                # before any network call; no transaction stays open over I/O.
                    with conn.cursor() as route_cur:
                        route_cur.execute("select route_key from ops.select_provider_routes(%s)",
                                          (cognition["provider_routes"],))
                        eligible = [r[0] for r in route_cur.fetchall()]
                    conn.commit()
                    if not eligible:
                        raise RuntimeError("no eligible provider route")
                    cognition = {**cognition,"provider_routes":eligible}
                    cache = DatabaseCache(cognition["key"],cognition["version"],
                                          cognition["output_schema_version"],
                                          [x["source_ref"] for x in input_payload.get("source_evidence",[])])
                    route_started: dict[str, float] = {}
                    observation_ref = f"control-plane:{job_id}:attempt:{claim['attempt']}"

                    def before_route(route: str):
                        route_started[route] = time.monotonic()
                        return _reserve(job_id, lease, route, float(cognition["budget"]["max_cost_usd"]))

                    def after_accept(route: str, reservation: Any, result: dict[str, Any]) -> None:
                        _settle(reservation, job_id, lease, result)
                        result["provider_observation_recorded"] = _try_observe_provider(
                            route, status="healthy",
                            latency_ms=int((time.monotonic() - route_started[route]) * 1000),
                            error=None, source_ref=observation_ref)

                    def after_failure(route: str, reservation: Any, exc: Exception) -> None:
                        _release(reservation, job_id, lease)
                        if isinstance(exc, BudgetExceeded):
                            # A local budget admission decision says nothing
                            # about provider health and must not degrade it.
                            return
                        _try_observe_provider(
                            route, status=_provider_failure_status(exc),
                            latency_ms=int((time.monotonic() - route_started.get(route, time.monotonic())) * 1000),
                            error=exc, source_ref=observation_ref)

                    evidence = CognitionDispatcher(
                        HttpProposalProvider(),cache,
                        before_route=before_route,
                        after_accept=after_accept,
                        after_failure=after_failure,
                        proposal_validator=lambda proposal: _validate_workflow_proposal(
                            workflow, input_payload, proposal),
                    ).execute(cognition,input_payload)
                    # Store the full typed canonical input and the exact
                    # cognition contract with the proposal.  Completion is
                    # later evaluated only from an immutable receipt readback.
                    evidence = {**evidence, "input": input_payload,
                                "cognition": {"key": cognition["key"], "version": cognition["version"],
                                              "output_schema_version": cognition["output_schema_version"],
                                              "canonical_write_authority": False}}
            facts = _post_execution_facts(workflow, claim, evidence, input_payload)
            if workflow["execution"]["kind"] == "deterministic" and not evaluate_stage(workflow, "filtering", facts):
                raise RuntimeError(f"{workflow['key']}.filtering predicate was not satisfied")
            if not evaluate_stage(workflow, "validation", facts):
                raise RuntimeError(f"{workflow['key']}.validation predicate was not satisfied")
            receipt = f"job:{job_id}:attempt:{claim['attempt']}"
            cur.execute("select ops.complete_job(%s,%s,%s,%s)",
                        (job_id,lease,json.dumps(evidence),receipt))
            # The completion receipt is written by the ledger function above
            # and read back in the SAME uncommitted transaction.  A rollback
            # erases it if the immutable row does not actually exist; no runner
            # is allowed to predict its own receipt before the write.
            cur.execute("""select receipt_ref,evidence from ops.job_receipt
                           where job_id=%s and attempt=%s and kind='completion'
                           order by created_at desc limit 1""", (job_id,claim["attempt"]))
            row = cur.fetchone()
            if row is None or row[0] != receipt or not isinstance(row[1], dict):
                raise RuntimeError("ledger completion receipt did not read back")
            completion_facts = _post_execution_facts(
                workflow, claim, evidence, input_payload, receipt_ref=receipt,
                receipt_evidence=row[1])
            if not evaluate_stage(workflow, "completion", completion_facts):
                raise RuntimeError(f"{workflow['key']}.completion predicate was not satisfied")
            conn.commit()
            return {"claimed":1,"job_id":str(job_id),"state":"succeeded"}
        except Exception as exc:
            conn.rollback()
            # Start a fresh transaction and fail only if the committed lease
            # still owns the job; an expired/reaped token is correctly refused.
            with conn.cursor() as fail_cur:
                if isinstance(exc,(subprocess.TimeoutExpired,TimeoutError)):
                    fail_cur.execute("select ops.timeout_job(%s,%s,%s)",
                                     (job_id,lease,str(exc)[:1000]))
                else:
                    fail_cur.execute("select ops.fail_job(%s,%s,%s,%s)",
                                     (job_id,lease,type(exc).__name__,str(exc)[:1000]))
                state = fail_cur.fetchone()[0]
            conn.commit()
            return {"claimed":1,"job_id":str(job_id),"state":state,
                    "failure_class":type(exc).__name__}


def metrics() -> dict[str, Any]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select state,count(*) from ops.v_job_control group by state order by state")
        states = {state: count for state,count in cur.fetchall()}
        cur.execute("""select coalesce(sum(cost_usd),0),coalesce(sum(attempts),0)
                       from ops.v_job_cost where month=date_trunc('month',now())""")
        cost, attempts = cur.fetchone()
        cur.execute("""select coalesce(provider_route,'unrouted'),sum(attempts),sum(cost_usd)
                         from ops.v_job_cost where month=date_trunc('month',now())
                        group by provider_route order by provider_route nulls first""")
        route_costs = [{"route": route, "attempts": int(route_attempts), "cost_usd": float(route_cost)}
                       for route, route_attempts, route_cost in cur.fetchall()]
        cur.execute("""select health,count(*) from ops.v_provider_route
                       group by health order by health""")
        provider_health = {health: count for health, count in cur.fetchall()}
        cur.execute("""select count(*) filter(where invalidated_at is null and expires_at>now()),
                              count(*) filter(where invalidated_at is not null),
                              count(*) filter(where invalidated_at is null and expires_at<=now())
                         from ops.cognition_result_cache""")
        cache_active, cache_invalidated, cache_expired = cur.fetchone()
        cur.execute("""select count(*) filter(where state='failed'),
                              count(*) filter(where state='timed_out')
                         from ops.job_attempt
                        where started_at>=date_trunc('month',now())""")
        failed_attempts, timed_out_attempts = cur.fetchone()
        cur.execute("""select route_key,reason,refusal_count,refused_estimated_cost_usd,
                              last_refused_at
                         from ops.v_cost_refusal_metric
                        where month=date_trunc('month',now())
                        order by route_key,reason""")
        budget_refusals = [
            {"route": route, "reason": reason, "count": int(count),
             "refused_estimated_cost_usd": float(estimate),
             "last_refused_at": last_refused_at}
            for route, reason, count, estimate, last_refused_at in cur.fetchall()
        ]
    return {"states":states,"month_cost_usd":float(cost),"month_attempts":attempts,
            "route_costs":route_costs,"provider_health":provider_health,
            "cache":{"active":cache_active,"invalidated":cache_invalidated,"expired":cache_expired},
            "attempt_failures":{"failed":failed_attempts,"timed_out":timed_out_attempts},
            "budget_refusals":budget_refusals}


def inspect_job(job_id: str) -> dict[str, Any]:
    """Read back state, attempts and immutable receipts for one ledger job."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""select definition_key,definition_version,state,mode,attempt,
                              last_failure_class,started_at,ended_at
                         from ops.job where id=%s""",(job_id,))
        row=cur.fetchone()
        if row is None:
            raise SystemExit(f"unknown job: {job_id}")
        job_cols=[d.name for d in cur.description]
        job=dict(zip(job_cols,row))
        cur.execute("""select attempt,state,provider_route,input_tokens,output_tokens,
                              cost_usd,failure_class,started_at,ended_at
                         from ops.job_attempt where job_id=%s order by attempt""",(job_id,))
        attempt_cols=[d.name for d in cur.description]
        attempts=[dict(zip(attempt_cols,r)) for r in cur.fetchall()]
        cur.execute("""select attempt,kind,receipt_ref,evidence,created_at
                         from ops.job_receipt where job_id=%s order by created_at,id""",(job_id,))
        receipt_cols=[d.name for d in cur.description]
        receipts=[dict(zip(receipt_cols,r)) for r in cur.fetchall()]
    return {"job_id":job_id,"job":job,"attempts":attempts,"receipts":receipts}


def tick(manifest: dict[str,Any],max_jobs: int=4,mode: str="shadow") -> dict[str,Any]:
    scheduled=enqueue_due(manifest,parse_instant(None),mode)
    runs=[]
    for _ in range(max_jobs):
        result=run_once(manifest,f"tick:{os.getpid()}",mode=mode)
        if result.get("claimed")==0:
            break
        runs.append(result)
    return {"scheduled":scheduled,"runs":runs,"run_count":len(runs)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("sync")
    schedule = sub.add_parser("schedule")
    schedule.add_argument("--at")
    schedule.add_argument("--mode",choices=("shadow","canary","live","replay"),default="live")
    run = sub.add_parser("run-once")
    run.add_argument("--worker", default=f"local:{os.getpid()}")
    sub.add_parser("metrics")
    inspect_parser=sub.add_parser("inspect")
    inspect_parser.add_argument("--job-id",required=True)
    tick_parser=sub.add_parser("tick")
    tick_parser.add_argument("--max-jobs",type=int,default=4)
    tick_parser.add_argument("--mode",choices=("shadow","canary","live"),default="shadow")
    args = parser.parse_args()
    manifest = load_manifest()
    if args.command == "validate": result: Any = {"ok":True,"workflows":len(manifest["workflows"])}
    elif args.command == "sync": result = sync_registry(manifest)
    elif args.command == "schedule": result = enqueue_due(manifest,parse_instant(args.at),args.mode)
    elif args.command == "run-once": result = run_once(manifest,args.worker)
    elif args.command=="metrics": result = metrics()
    elif args.command=="inspect": result = inspect_job(args.job_id)
    else: result=tick(manifest,args.max_jobs,args.mode)
    print(json.dumps(result,sort_keys=True,default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
