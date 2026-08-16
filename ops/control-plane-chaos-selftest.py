#!/usr/bin/env python3
"""Black-box acceptance suite for the rollback-only control-plane chaos drill."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRILL = REPO / "ops" / "control-plane-chaos.py"


def main() -> int:
    proc = subprocess.run([sys.executable, str(DRILL), "--json"], text=True,
                          capture_output=True, timeout=30)
    failures: list[str] = []
    try:
        evidence = json.loads(proc.stdout)
    except ValueError as exc:
        print(f"FAIL invalid JSON evidence: {exc}: {proc.stdout[:200]!r}")
        return 1

    def check(name: str, value: bool) -> None:
        print(f"  {'ok' if value else 'FAIL'} {name}")
        if not value:
            failures.append(name)

    scenarios = {item["name"]: item for item in evidence.get("scenarios", [])}
    metrics = evidence.get("metrics", {})
    check("process succeeds", proc.returncode == 0)
    check("exercise declares rollback-only/no external mutation",
          evidence.get("mode") == "rollback_only" and evidence.get("external_mutations") is False)
    check("primary outage trace proves secondary failover",
          scenarios.get("provider_outage", {}).get("selected_route") == "secondary"
          and [a["outcome"] for a in scenarios.get("provider_outage", {}).get("attempt_trace", [])]
          == ["failed", "succeeded"])
    check("stale cache is unavailable after dependency invalidation",
          scenarios.get("stale_cache", {}).get("before") == "hit"
          and scenarios.get("stale_cache", {}).get("after") == "invalidated"
          and scenarios.get("stale_cache", {}).get("invalidated_entries") == 1)
    check("worker death rejects stale completion and creates recovery receipts",
          scenarios.get("worker_death", {}).get("stale_completion_refused") is True
          and scenarios.get("worker_death", {}).get("recovery_attempt") == 2
          and [r["kind"] for r in scenarios.get("worker_death", {}).get("receipts", [])]
          == ["lease_expired", "completion"])
    check("cost ceiling refusal remains measured",
          scenarios.get("cost_ceiling", {}).get("refusal") == "monthly_budget_exceeded"
          and scenarios.get("cost_ceiling", {}).get("metrics", {}).get("refused_count") == 1)
    check("aggregate recovery evidence has all required measurements",
          scenarios.get("recovery_evidence", {}).get("passed") is True
          and metrics == {"scenario_count": 4, "passed_count": 4,
                          "provider_failovers": 1, "cache_invalidations": 1,
                          "lease_recoveries": 1, "recovery_receipt_count": 2,
                          "cost_refusals": 1})
    print(f"control-plane-chaos-selftest: {7 - len(failures)}/7 passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
