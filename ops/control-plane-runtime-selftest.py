#!/usr/bin/env python3
"""Hermetic checks for the runtime adapter around the database ledger."""
from __future__ import annotations

import importlib.util
import subprocess
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("control_plane_cli", REPO / "tools" / "control-plane.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def main() -> int:
    failures: list[str] = []
    total = 0

    def check(name: str, condition: bool) -> None:
        nonlocal total
        total += 1
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    heartbeats: list[tuple] = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, _sql, args): heartbeats.append(args)
        def fetchone(self): return (True,)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return Cursor()
        def commit(self): pass

    original_connect = getattr(module, "connect")
    setattr(module, "connect", lambda: Connection())
    try:
        with module.LeaseKeeper("job", "lease", seconds=9, interval=0.01):
            time.sleep(0.035)
    finally:
        setattr(module, "connect", original_connect)
    check("long work renews the committed lease more than once",len(heartbeats) >= 2)
    check("heartbeat uses the exact job, token and registered lease duration",
          all(x == ("job","lease",9) for x in heartbeats))

    claim_calls: list[tuple[str, tuple]] = []

    class ClaimCursor:
        description: list[object] = []
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, args): claim_calls.append((sql, args))
        def fetchone(self): return None

    class ClaimConnection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return ClaimCursor()
        def commit(self): pass

    original_connect = getattr(module, "connect")
    setattr(module, "connect", lambda: ClaimConnection())
    try:
        shadow_empty = module.run_once({}, "shadow-worker", mode="shadow")
        generic_empty = module.run_once({}, "manual-worker")
    finally:
        setattr(module, "connect", original_connect)
    check("shadow worker uses the mode-filtered claim function",
          shadow_empty == {"claimed": 0} and claim_calls[0] ==
          ("select * from ops.claim_job_mode(%s,%s,1,300)", ("shadow-worker", "shadow")))
    check("generic run-once stays mode-agnostic for deliberate operator recovery",
          generic_empty == {"claimed": 0} and claim_calls[1] ==
          ("select * from ops.claim_job(%s,1,300)", ("manual-worker",)))

    cognition_order: list[str] = []
    cognition_input = {"facts": {"release": {"from": "r1", "to": "r2"}}}
    original_build_input = getattr(module, "build_input")
    original_evidence_collector = getattr(module, "RuntimeEvidenceCollector")
    original_fact_collector = getattr(module, "_workflow_fact_collector")
    original_evaluate_stage = getattr(module, "evaluate_stage")
    def fake_build_input(*_args, **_kwargs):
        cognition_order.append("build")
        return cognition_input
    def fake_cognition_facts(_workflow, _payload, **kwargs):
        cognition_order.append("facts")
        return kwargs.get("input_payload")
    def fake_cognition_stage(_workflow, stage, facts):
        cognition_order.append(stage)
        return facts is cognition_input
    setattr(module, "RuntimeEvidenceCollector", lambda *_args, **_kwargs: object())
    setattr(module, "build_input", fake_build_input)
    setattr(module, "_workflow_fact_collector", fake_cognition_facts)
    setattr(module, "evaluate_stage", fake_cognition_stage)
    try:
        admitted_input = module._build_and_admit_cognition_input(
            {}, {"key": "cc-update-audit", "execution": {"input_builder": "cc-release-diff"}},
            {"payload": {}, "mode": "shadow"})
    finally:
        setattr(module, "build_input", original_build_input)
        setattr(module, "RuntimeEvidenceCollector", original_evidence_collector)
        setattr(module, "_workflow_fact_collector", original_fact_collector)
        setattr(module, "evaluate_stage", original_evaluate_stage)
    check("cognition routing and filtering run only after canonical typed input is built",
          admitted_input is cognition_input
          and cognition_order == ["build", "facts", "routing", "filtering"])

    post_kwargs: dict[str, object] = {}
    original_fact_collector = getattr(module, "_workflow_fact_collector")
    def fake_post_facts(_workflow, _payload, **kwargs):
        post_kwargs.update(kwargs)
        return "facts"
    setattr(module, "_workflow_fact_collector", fake_post_facts)
    try:
        post_facts = module._post_execution_facts(
            {"key": "cc-update-audit"}, {"payload": {}, "mode": "shadow"},
            {"proposal": {}}, cognition_input, receipt_ref="receipt:1",
            receipt_evidence={"proposal": {}})
    finally:
        setattr(module, "_workflow_fact_collector", original_fact_collector)
    check("cognition validation and completion retain the exact typed input",
          post_facts == "facts" and post_kwargs.get("input_payload") is cognition_input
          and post_kwargs.get("receipt_ref") == "receipt:1")

    calls: list[list[str]] = []
    call_envs: list[dict[str, str]] = []
    original_run = module.subprocess.run

    def fake_run(argv, **kwargs):
        calls.append(argv)
        call_envs.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "shadow evidence", "")

    module.subprocess.run = fake_run
    try:
        workflow = {"execution":{"entrypoint":"bin/nightly.sh","args":[],
                                  "shadow_args":["--preflight"],"canary_args":[]}}
        evidence = module._execute_deterministic(workflow,{},30,"shadow")
    finally:
        module.subprocess.run = original_run
    check("shadow execution selects only the registered shadow arguments",
          bool(calls) and calls[0][1:] == ["--preflight"])
    check("execution evidence records mode and exact arguments",
          evidence["mode"] == "shadow" and evidence["args"] == ["--preflight"])
    check("every deterministic subprocess receives its exact control-plane mode",
          bool(call_envs) and call_envs[0].get("CARR_CONTROL_PLANE_MODE") == "shadow")
    check("deterministic subprocesses never inherit ledger, provider, owner, or live-ingest secrets",
          bool(call_envs) and not any(key in call_envs[0] for key in ("CARR_DB_JOBS_URL", "DATABASE_URL", "CARR_DB_OWNER_URL", "CARR_DB_WRITER_URL", "CARR_AI_ROUTE_PRIMARY_TOKEN", "CARR_INGEST_TOKEN_CALENDAR")))

    canary_calls: list[list[str]] = []

    def canary_run(argv, **_kwargs):
        canary_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "nightly result: chain_ok\n", "")

    module.subprocess.run = canary_run
    try:
        disabled_canary = {
            "execution": {
                "entrypoint": "bin/nightly.sh",
                "args": [],
                "shadow_args": ["--preflight"],
                "canary": {
                    "enabled": False,
                    "reason": "the registered canary is live-equivalent",
                },
            }
        }
        try:
            module._execute_deterministic(disabled_canary, {}, 30, "canary")
            canary_refused = False
        except RuntimeError as exc:
            canary_refused = "canary isolation is disabled" in str(exc)
    finally:
        module.subprocess.run = original_run
    check("disabled deterministic canary refuses before launching a subprocess",
          canary_refused and not canary_calls)

    replay_calls: list[list[str]] = []

    def replay_run(argv, **_kwargs):
        replay_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "nightly result: chain_ok\n", "")

    module.subprocess.run = replay_run
    try:
        try:
            module._execute_deterministic(disabled_canary, {}, 30, "replay")
            replay_refused = False
        except RuntimeError as exc:
            replay_refused = "deterministic replay execution is disabled" in str(exc)
    finally:
        module.subprocess.run = original_run
    check("deterministic replay cannot alias the live command",
          replay_refused and not replay_calls)

    connect_calls = 0

    def should_not_connect():
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("unsafe canary scheduling reached the database")

    original_connect = getattr(module, "connect")
    setattr(module, "connect", should_not_connect)
    try:
        unsafe_manifest = module.load_manifest()
        next(workflow for workflow in unsafe_manifest["workflows"]
             if workflow["key"] == "calendar-fetch-daily")["execution"]["canary"]["isolation_guard"] = "forged-canary-guard"
        try:
            module.enqueue_due(
                unsafe_manifest,
                datetime.fromisoformat("2026-08-17T12:09:00+00:00"),
                "canary",
            )
            schedule_refused = False
        except RuntimeError as exc:
            schedule_refused = "canary isolation guard is not registered" in str(exc)
    finally:
        setattr(module, "connect", original_connect)
    check("unsafe deterministic canary is refused before a ledger write",
          schedule_refused and connect_calls == 0)

    sync_sql: list[str] = []

    class SyncCursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, statement, _args=()): sync_sql.append(str(statement))
        def fetchall(self): return [(1,)]
        def fetchone(self): return (1,)

    class SyncConnection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return SyncCursor()
        def commit(self): pass

    manifest = module.load_manifest()
    nightly = next(w for w in manifest["workflows"] if w["key"] == "nightly-record-layer")
    original_connect = getattr(module, "connect")
    setattr(module, "connect", lambda **_kwargs: SyncConnection())
    try:
        try:
            module.sync_registry({"cognition_jobs": [], "workflows": [nightly]})
            running_version_refused = False
        except RuntimeError as exc:
            running_version_refused = "drain running jobs" in str(exc)
    finally:
        setattr(module, "connect", original_connect)
    check("definition sync refuses to supersede a running old version",
          running_version_refused
          and not any("update ops.job_definition" in statement.lower() for statement in sync_sql))

    observations: list[tuple] = []

    class ObservationCursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, _sql, args): observations.append(args)
        def fetchone(self): return ("observation-id",)

    class ObservationConnection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return ObservationCursor()
        def commit(self): pass

    original_connect = getattr(module, "connect")
    setattr(module, "connect", lambda: ObservationConnection())
    try:
        module._observe_provider("secondary", status="healthy", latency_ms=12,
                                 error=None, source_ref="fixture:attempt:1")
        unavailable = module._provider_failure_status(ConnectionError("offline"))
        rate_limited = module._provider_failure_status(module.urllib.error.HTTPError(
            "https://fixture", 429, "rate", {}, None))
    finally:
        setattr(module, "connect", original_connect)
    check("provider observations are ledger writes with bounded TTL and source",
          observations == [("secondary", "healthy", 12, None, 300, "fixture:attempt:1")])
    check("provider failure classes map to route-health states",
          unavailable == "unavailable" and rate_limited == "rate_limited"
          and module._provider_failure_status(ValueError("bad proposal")) == "degraded")

    original_observe = getattr(module, "_observe_provider")
    setattr(module, "_observe_provider", lambda *_args, **_kwargs:
            (_ for _ in ()).throw(ConnectionError("telemetry unavailable")))
    try:
        observation_recorded = module._try_observe_provider(
            "secondary", status="healthy", latency_ms=12, error=None,
            source_ref="fixture:attempt:1")
    finally:
        setattr(module, "_observe_provider", original_observe)
    check("route-health telemetry outage cannot trigger a duplicate provider call",
          observation_recorded is False)

    class MetricsCursor:
        def __init__(self): self.query = 0
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, _sql): self.query += 1
        def fetchone(self):
            return {
                2: (1.25, 3),
                5: (2, 1, 4),
                6: (5, 2),
            }[self.query]
        def fetchall(self):
            return {
                1: [("succeeded", 2)],
                3: [("secondary", 3, 1.25)],
                4: [("healthy", 1)],
                7: [("primary", "monthly_budget_exceeded", 2, 0.50,
                     "2026-08-16T12:00:00+00:00")],
            }[self.query]

    class MetricsConnection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return MetricsCursor()

    original_connect = getattr(module, "connect")
    setattr(module, "connect", lambda: MetricsConnection())
    try:
        metric_result = module.metrics()
    finally:
        setattr(module, "connect", original_connect)
    check("metrics reports durable budget refusals from the ledger view",
          metric_result["budget_refusals"] == [{
              "route": "primary", "reason": "monthly_budget_exceeded", "count": 2,
              "refused_estimated_cost_usd": 0.5,
              "last_refused_at": "2026-08-16T12:00:00+00:00",
          }])

    print(f"\ncontrol-plane-runtime-selftest: {total-len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
