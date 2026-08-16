#!/usr/bin/env python3
"""Run the control-plane's rollback-only chaos and recovery exercise.

This is deliberately an adapter-level drill: it invokes no provider, scheduler,
database, or canonical writer.  The state machines are isolated in memory and
the resulting evidence is emitted as one JSON document for a staging runner to
store beside its run receipt.  A non-zero exit means a recovery boundary failed
to produce the expected trace, refusal, or receipt.

Scenarios:
  provider_outage       primary failure, eligible secondary failover
  stale_cache           canonical dependency invalidates an unexpired proposal
  worker_death          expired lease is reclaimed; stale token cannot complete
  cost_ceiling          a reservation consumes capacity and excess dispatch refuses
  recovery_evidence     aggregate receipt and measurement assertions
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ops"))

from control_plane_resilience import (  # noqa: E402
    BudgetLedger,
    LeaseLedger,
    ProposalCache,
    ProviderRoute,
    run_with_failover,
)


def scenario_provider_outage() -> tuple[dict[str, Any], int]:
    routes = (
        ProviderRoute("primary", 1, True, "healthy"),
        ProviderRoute("secondary", 2, True, "healthy"),
    )

    def execute(route_key: str) -> dict[str, str]:
        if route_key == "primary":
            raise ConnectionError("synthetic primary outage")
        return {"kind": "proposal", "route": route_key}

    result = run_with_failover(routes, execute)
    trace = [{"route": a.route_key, "outcome": a.outcome,
              "error_class": a.error_class} for a in result.attempts]
    passed = (result.selected_route == "secondary"
              and [item["outcome"] for item in trace] == ["failed", "succeeded"])
    return ({"name": "provider_outage", "passed": passed,
             "selected_route": result.selected_route, "attempt_trace": trace,
             "refusal": result.refusal}, 1 if passed else 0)


def scenario_stale_cache() -> tuple[dict[str, Any], int]:
    cache = ProposalCache()
    key = cache.put("research.entity", 1, {"party": "P-CHAOS"},
                    {"proposal": "before-change"}, now=100, ttl_seconds=600,
                    dependencies=("party:P-CHAOS",))
    before = cache.get(key, now=101)
    invalidated = cache.invalidate("party:P-CHAOS", now=102)
    after = cache.get(key, now=103)
    passed = (before.state == "hit" and invalidated == 1
              and after.state == "invalidated" and after.value is None)
    return ({"name": "stale_cache", "passed": passed, "cache_key": key,
             "before": before.state, "invalidated_entries": invalidated,
             "after": after.state}, 1 if passed else 0)


def scenario_worker_death() -> tuple[dict[str, Any], int]:
    ledger = LeaseLedger()
    ledger.enqueue("chaos-worker-death")
    first = ledger.claim("worker-primary", now=0, lease_seconds=5)
    stale_completion = ledger.complete(first.job_id, first.token, now=6,
                                      receipt_ref="must-not-land")
    recovered = ledger.claim("worker-recovery", now=6, lease_seconds=5)
    completed = ledger.complete(recovered.job_id, recovered.token, now=7,
                                receipt_ref="recovery-completion")
    snapshot = ledger.job(first.job_id)
    receipts = [{"kind": r.kind, "attempt": r.attempt, "ref": r.receipt_ref}
                for r in snapshot.receipts]
    passed = (not stale_completion and completed and snapshot.state == "succeeded"
              and first.token != recovered.token and first.attempt == 1
              and recovered.attempt == 2 and receipts == [
                  {"kind": "lease_expired", "attempt": 1,
                   "ref": "lease-expired:chaos-worker-death:1"},
                  {"kind": "completion", "attempt": 2,
                   "ref": "recovery-completion"},
              ])
    return ({"name": "worker_death", "passed": passed,
             "stale_completion_refused": not stale_completion,
             "recovery_attempt": recovered.attempt, "state": snapshot.state,
             "receipts": receipts}, len(receipts) if passed else 0)


def scenario_cost_ceiling() -> tuple[dict[str, Any], int]:
    budget = BudgetLedger(monthly_limit_usd=10.0)
    admitted = budget.authorize("primary", estimated_cost_usd=6.0,
                               request_ref="chaos-reserved")
    refused = budget.authorize("secondary", estimated_cost_usd=5.0,
                              request_ref="chaos-over-limit")
    report = budget.report()
    passed = (admitted.allowed and not refused.allowed
              and refused.reason == "monthly_budget_exceeded"
              and report.reserved_usd == 6.0 and report.refused_count == 1
              and report.remaining_usd == 4.0)
    return ({"name": "cost_ceiling", "passed": passed,
             "admitted_reservation": admitted.reservation_id,
             "refusal": refused.reason,
             "metrics": {"limit_usd": report.monthly_limit_usd,
                         "reserved_usd": report.reserved_usd,
                         "remaining_usd": report.remaining_usd,
                         "refused_count": report.refused_count,
                         "refused_cost_usd": report.refused_cost_usd}},
            1 if passed else 0)


def run_exercise() -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = []
    failovers = 0
    receipts = 0
    for builder in (scenario_provider_outage, scenario_stale_cache,
                    scenario_worker_death, scenario_cost_ceiling):
        scenario, measure = builder()
        scenarios.append(scenario)
        if scenario["name"] == "provider_outage":
            failovers += measure
        if scenario["name"] == "worker_death":
            receipts += measure

    all_passed = all(s["passed"] for s in scenarios)
    metrics = {
        "scenario_count": len(scenarios),
        "passed_count": sum(1 for s in scenarios if s["passed"]),
        "provider_failovers": failovers,
        "cache_invalidations": scenarios[1]["invalidated_entries"],
        "lease_recoveries": 1 if scenarios[2]["passed"] else 0,
        "recovery_receipt_count": receipts,
        "cost_refusals": scenarios[3]["metrics"]["refused_count"],
    }
    recovery = {"name": "recovery_evidence", "passed": all_passed
                and metrics["recovery_receipt_count"] == 2
                and metrics["provider_failovers"] == 1
                and metrics["cost_refusals"] == 1,
                "metrics": metrics}
    scenarios.append(recovery)
    return {"exercise": "control-plane-chaos-v1", "mode": "rollback_only",
            "external_mutations": False, "passed": all(s["passed"] for s in scenarios),
            "scenarios": scenarios, "metrics": metrics}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run a rollback-only control-plane chaos drill.")
    parser.add_argument("--json", action="store_true", help="emit JSON evidence (default)")
    args = parser.parse_args(argv)
    del args
    evidence = run_exercise()
    print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
