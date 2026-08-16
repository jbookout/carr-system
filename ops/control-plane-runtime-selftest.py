#!/usr/bin/env python3
"""Hermetic checks for the runtime adapter around the database ledger."""
from __future__ import annotations

import importlib.util
import subprocess
import time
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

    calls: list[list[str]] = []
    original_run = module.subprocess.run

    def fake_run(argv, **_kwargs):
        calls.append(argv)
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
