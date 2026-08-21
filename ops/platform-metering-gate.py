#!/usr/bin/env python3
"""CLI boundary for shell-owned metered execution paths."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.platform_metering import MeteringRefusal, authorize_metered_execution  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--gate", required=True)
    result.add_argument("--requested-lifetime-minutes", type=int)
    result.add_argument("--active-nondefault-branches", type=int)
    result.add_argument("--cleanup-registered", action="store_true")
    result.add_argument("--local-checks-green", action="store_true")
    result.add_argument("--release-preflight-green", action="store_true")
    result.add_argument("--performance-budget-ref")
    result.add_argument("--release-candidate-count", type=int)
    result.add_argument("--candidate-sha")
    return result


def main() -> int:
    args = parser().parse_args()
    request = {
        key: value for key, value in {
            "requested_lifetime_minutes": args.requested_lifetime_minutes,
            "active_nondefault_branches": args.active_nondefault_branches,
            "cleanup_registered": args.cleanup_registered,
            "local_checks_green": args.local_checks_green,
            "release_preflight_green": args.release_preflight_green,
            "performance_budget_ref": args.performance_budget_ref,
            "release_candidate_count": args.release_candidate_count,
            "candidate_sha": args.candidate_sha,
        }.items() if value is not None
    }
    policy = json.loads(
        (REPO / "ops/config/platform-metering.v1.json").read_text(encoding="utf-8"))
    try:
        decision = authorize_metered_execution(policy, args.gate, request)
    except (MeteringRefusal, ValueError, TypeError) as exc:
        print(f"metered execution refused: {exc}", file=sys.stderr)
        return 77
    print(json.dumps(decision, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
