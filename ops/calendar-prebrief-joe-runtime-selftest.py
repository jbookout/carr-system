#!/usr/bin/env python3
"""Hermetic checks for the Joe prebrief runtime's leased failure boundary."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calendar_prebrief_joe_runtime_under_test", ROOT / "tools/calendar-prebrief-joe-runtime.py")
assert spec and spec.loader
runtime: Any = cast(Any, importlib.util.module_from_spec(spec))
spec.loader.exec_module(runtime)

bad: list[str] = []


def check(name: str, value: bool) -> None:
    print(("  ok " if value else "  FAIL ") + name)
    if not value:
        bad.append(name)


claim = {"job_id": "00000000-0000-4000-8000-000000000001", "lease": "00000000-0000-4000-8000-000000000002", "scheduled_for": "2026-07-13T11:30:00Z"}
profile = {
    "CARR_CALENDAR_PREBRIEF_ENABLED": "true", "CARR_DB_JOBS_URL": "postgresql://carr_jobs:fixture@db.example/carr",  # ci-secret-scan: allow — inert fixture
    "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": "/fixture/child.env", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": "/fixture/public.pem",
    "CARR_CALENDAR_PREBRIEF_ALLOWLIST": "/fixture/allowlist.json", "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY": "/fixture/private.pem",
    "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION": "fixture-1", "CARR_CALENDAR_PREBRIEF_EVENTKIT_APP": "/fixture/CARR Calendar Access.app",
}

# Profile/app filesystem safety is separately adversarially tested by the
# provisioner suite.  This fixture starts at the runtime's DB lease boundary.
runtime._secure_file = lambda *_args, **_kwargs: None
runtime.verify_app = lambda _path: None
runtime.schedule = lambda _dsn: None


class RefusingCoordinator:
    class Refusal(RuntimeError):
        pass

    def parent_execute(self, **kwargs):
        kwargs["after_claim"](claim)
        raise self.Refusal("fixture DB-issued contract refusal")


calls: list[tuple[str, tuple[object, ...]]] = []


def refusing_jobs(_dsn: str, query: str, args=()):
    calls.append((query, tuple(args)))
    if "heartbeat_job" in query:
        return True
    if "fail_job" in query:
        return "retry_wait"
    raise AssertionError(f"unexpected jobs query: {query}")


runtime._coordinator = lambda: RefusingCoordinator()
runtime._jobs_call = refusing_jobs
try:
    runtime.run_tick(profile, Path("/fixture/runtime.env"))
except runtime.RecordedJobFailure as exc:
    check("a post-claim deterministic child refusal returns the recorded retry state", exc.state == "retry_wait")
else:
    check("a post-claim deterministic child refusal returns the recorded retry state", False)
check("runtime renews the validated lease before the child starts", len(calls) >= 1 and "heartbeat_job" in calls[0][0] and calls[0][1] == (claim["job_id"], claim["lease"], runtime.LEASE_SECONDS))
check("runtime records a typed child refusal instead of leaving a lease to expire", len(calls) == 2 and "fail_job" in calls[1][0] and calls[1][1][2:] == (runtime.CHILD_REFUSAL_CLASS, "sponsor child refused its DB-issued calendar capture contract"))


class CompletingCoordinator:
    def parent_execute(self, **kwargs):
        kwargs["after_claim"](claim)
        return {"claim": dict(claim), "result": {"sponsor": "joe", "mode": "live", "attestation_id": "00000000-0000-4000-8000-000000000003", "receipt_id": "00000000-0000-4000-8000-000000000004"}}


success_calls: list[tuple[str, tuple[object, ...]]] = []


def completing_jobs(_dsn: str, query: str, args=()):
    success_calls.append((query, tuple(args)))
    if "heartbeat_job" in query:
        return True
    if "complete_calendar_prebrief_joe_live_job" in query:
        return {"job_id": claim["job_id"], "attempt": 1, "state": "succeeded", "attestation_id": "00000000-0000-4000-8000-000000000003", "receipt_id": "00000000-0000-4000-8000-000000000004", "allowlist_revision_id": "00000000-0000-4000-8000-000000000005", "allowlist_digest": "a" * 64, "scheduled_for": claim["scheduled_for"]}
    raise AssertionError(f"unexpected jobs query: {query}")


runtime._coordinator = lambda: CompletingCoordinator()
runtime._jobs_call = completing_jobs
result = runtime.run_tick(profile, Path("/fixture/runtime.env"))
check("runtime renews a live lease again immediately before completion", ["heartbeat_job" in query for query, _ in success_calls] == [True, True, False])
check("successful completion stays in the dedicated Joe live boundary", result["claimed"] == 1 and result["completion"]["state"] == "succeeded")

print("PASS" if not bad else "FAIL " + ", ".join(bad))
raise SystemExit(bool(bad))
