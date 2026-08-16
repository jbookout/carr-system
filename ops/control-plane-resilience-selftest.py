#!/usr/bin/env python3
"""Acceptance tests for the portable control-plane resilience harness.

These scenarios deliberately use deterministic clocks and injected provider
callbacks.  They are recovery exercises, not availability claims: each test
reads the evidence the harness emitted (attempt trace, cache state, lease
receipt, and budget decision) before it passes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ops"))

failures: list[str] = []
total = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global total
    total += 1
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}{': ' + detail if detail else ''}")
        failures.append(name)


def main() -> int:
    try:
        from control_plane_resilience import (
            BudgetLedger,
            LeaseLedger,
            ProposalCache,
            ProviderRoute,
            run_with_failover,
        )
    except Exception as exc:
        print(f"control-plane-resilience-selftest: implementation unavailable: {exc}")
        return 1

    # A primary provider failure must be visible, bounded, and followed only by
    # an eligible secondary route.  The callback is a local fixture: no model
    # invocation or credential is involved.
    routes = [
        ProviderRoute("primary", priority=1, enabled=True, health="healthy"),
        ProviderRoute("secondary", priority=2, enabled=True, health="healthy"),
    ]

    def provider(route: str) -> str:
        if route == "primary":
            raise RuntimeError("synthetic provider outage")
        return "typed-proposal"

    failover = run_with_failover(routes, provider)
    check("provider failure is recorded before failover",
          [(a.route_key, a.outcome) for a in failover.attempts]
          == [("primary", "failed"), ("secondary", "succeeded")],
          repr(failover.attempts))
    check("failover selects the secondary provider, not a hidden retry",
          failover.selected_route == "secondary" and failover.output == "typed-proposal")
    no_route = run_with_failover(
        [ProviderRoute("down", priority=1, enabled=True, health="unavailable")],
        lambda _: "must-not-run",
    )
    check("no healthy route refuses dispatch with evidence",
          no_route.selected_route is None and no_route.refusal == "no_eligible_provider"
          and no_route.attempts == ())

    # Cache invalidation must make a previously valid result unavailable before
    # its TTL.  A cache key never includes provider identity.
    cache = ProposalCache()
    key = cache.put("research.entity", 1, {"party": "P-1"}, {"proposal": []},
                    now=100, ttl_seconds=300, dependencies=("party:P-1",))
    hit = cache.get(key, now=101)
    invalidated = cache.invalidate("party:P-1", now=102)
    after = cache.get(key, now=103)
    check("cache key is provider-neutral and initially returns a measured hit",
          key == cache.key("research.entity", 1, {"party": "P-1"}, provider="secondary")
          and hit.state == "hit", repr(hit))
    check("dependency invalidation is recorded and blocks stale reuse",
          invalidated == 1 and after.state == "invalidated" and after.value is None,
          f"invalidated={invalidated} after={after}")

    # A worker that loses its lease may not finish the old attempt.  Reclaim is
    # explicit and is the only route by which a later attempt can complete.
    leases = LeaseLedger()
    leases.enqueue("job-1")
    first = leases.claim("worker-a", now=0, lease_seconds=5)
    stale_completion = leases.complete("job-1", first.token, now=6, receipt_ref="old")
    reclaimed = leases.claim("worker-b", now=6, lease_seconds=5)
    completed = leases.complete("job-1", reclaimed.token, now=7, receipt_ref="new")
    job = leases.job("job-1")
    check("expired lease cannot complete after lease loss", not stale_completion)
    check("expired job is reclaimed as a new attempt with a new token",
          first.attempt == 1 and reclaimed.attempt == 2 and first.token != reclaimed.token,
          f"first={first} reclaimed={reclaimed}")
    check("reclaimed completion produces one durable receipt",
          completed and job.state == "succeeded"
          and [(r.kind, r.attempt, r.receipt_ref) for r in job.receipts]
          == [("lease_expired", 1, "lease-expired:job-1:1"), ("completion", 2, "new")],
          repr(job.receipts))

    # Cost is admitted before dispatch and the report retains refusal evidence;
    # it is not an after-the-fact advisory.  No currency target is asserted:
    # the exercise checks the actual reserved/spent/refused values.
    budget = BudgetLedger(monthly_limit_usd=10.0)
    allowed = budget.authorize("primary", estimated_cost_usd=4.0, request_ref="job-a")
    budget.record(allowed, actual_cost_usd=3.5)
    refused = budget.authorize("secondary", estimated_cost_usd=7.0, request_ref="job-b")
    report = budget.report()
    check("within-budget work reserves before dispatch and records actual cost",
          allowed.allowed and report.spent_usd == 3.5 and report.reserved_usd == 0.0
          and report.spent_by_route == (("primary", 3.5),),
          repr(report))
    check("over-budget work is refused before dispatch and remains visible",
          not refused.allowed and refused.reason == "monthly_budget_exceeded"
          and report.refused_count == 1 and report.refused_cost_usd == 7.0,
          f"refusal={refused} report={report}")
    concurrent = BudgetLedger(monthly_limit_usd=10.0)
    first_reservation = concurrent.authorize("primary", estimated_cost_usd=6.0,
                                             request_ref="job-c")
    overlapping = concurrent.authorize("secondary", estimated_cost_usd=5.0,
                                       request_ref="job-d")
    check("an unspent reservation still consumes budget capacity",
          first_reservation.allowed and not overlapping.allowed
          and concurrent.report().reserved_usd == 6.0,
          f"first={first_reservation} overlapping={overlapping} report={concurrent.report()}")

    print(f"\ncontrol-plane-resilience-selftest: {total - len(failures)}/{total} passed")
    if failures:
        print("FAILURES: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
