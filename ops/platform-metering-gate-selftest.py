#!/usr/bin/env python3
"""Executable contract for the permanent platform-cost admission gate."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
import re
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.platform_metering import MeteringRefusal, authorize_metered_execution  # noqa: E402


POLICY = json.loads((REPO / "ops/config/platform-metering.v1.json").read_text(encoding="utf-8"))
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def refuses(call: Callable[[], Any]) -> bool:
    try:
        call()
    except MeteringRefusal:
        return True
    return False


def main() -> int:
    print("platform-metering-gate-selftest — paid dispatch is admitted before execution\n")

    check("unknown metered dispatch fails closed", refuses(lambda: authorize_metered_execution(
        POLICY, "unregistered-dispatch", {}, today=date(2026, 8, 17))))

    check("GitHub Actions remains blocked until an allowance reset is verified", refuses(
        lambda: authorize_metered_execution(POLICY, "github-actions-remote-ci", {
            "candidate_sha": "a" * 40, "local_checks_green": True,
        }, today=date(2026, 9, 1))))

    allowed_neon = authorize_metered_execution(POLICY, "neon-disposable-branch", {
        "requested_lifetime_minutes": 120,
        "cleanup_registered": True,
        "active_nondefault_branches": 1,
    }, today=date(2026, 8, 17))
    check("one bounded disposable Neon branch is admitted", allowed_neon["admitted"] is True)
    check("Neon branch without same-run cleanup is refused", refuses(
        lambda: authorize_metered_execution(POLICY, "neon-disposable-branch", {
            "requested_lifetime_minutes": 120,
            "cleanup_registered": False,
            "active_nondefault_branches": 1,
        }, today=date(2026, 8, 17))))
    check("Neon branch over the lifetime cap is refused", refuses(
        lambda: authorize_metered_execution(POLICY, "neon-disposable-branch", {
            "requested_lifetime_minutes": 121,
            "cleanup_registered": True,
            "active_nondefault_branches": 1,
        }, today=date(2026, 8, 17))))
    check("Neon fanout over the branch cap is refused", refuses(
        lambda: authorize_metered_execution(POLICY, "neon-disposable-branch", {
            "requested_lifetime_minutes": 60,
            "cleanup_registered": True,
            "active_nondefault_branches": 3,
        }, today=date(2026, 8, 17))))

    check("Cloudflare deploy without local verification is refused", refuses(
        lambda: authorize_metered_execution(POLICY, "cloudflare-worker-release", {
            "release_preflight_green": False,
            "performance_budget_ref": "release:R-1",
            "release_candidate_count": 1,
        }, today=date(2026, 8, 17))))
    allowed_deploy = authorize_metered_execution(POLICY, "cloudflare-worker-release", {
        "release_preflight_green": True,
        "performance_budget_ref": "release:R-1",
        "release_candidate_count": 1,
    }, today=date(2026, 8, 17))
    check("one verified Cloudflare release candidate is admitted", allowed_deploy["admitted"] is True)

    gates = POLICY.get("execution_gates", {})
    check("paid cognition delegates to the transactional database cost gate",
          gates.get("cognition-provider", {}).get("installed_enforcement_ref") ==
          "tools/control-plane.py:_reserve->ops.admit_job_cost")

    branch_sources = {
        "ops/p1-integration-gate.py",
        "ops/p1-rebuild-gate.py",
        "ops/cc-update-audit-shadow-harness.py",
        "bin/restore-rehearse.sh",
    }
    branch_patterns = (
        re.compile(r'["\x27]branches["\x27]\s*,\s*["\x27]create["\x27]'),
        re.compile(r'\$NEONCTL[^\n]*\bbranches\s+create\b'),
    )
    discovered_branch_sources = {
        str(path.relative_to(REPO))
        for path in REPO.rglob("*")
        if path.suffix in {".py", ".sh"}
        and path.name != Path(__file__).name
        and any(pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
                for pattern in branch_patterns)
    }
    check("every Neon branch-create implementation is inventoried",
          discovered_branch_sources == branch_sources)
    for relative in sorted(branch_sources):
        source = (REPO / relative).read_text(encoding="utf-8")
        create_at = source.find("branches create")
        if create_at < 0:
            create_at = source.find('"branches","create"')
        if create_at < 0:
            create_at = source.find('"branches", "create"')
        authorize_at = source.find("neon-disposable-branch")
        check(f"{relative} admits metered branch work before create",
              authorize_at >= 0 and create_at >= 0 and authorize_at < create_at)

    deploy = (REPO / "bin/deploy-worker.sh").read_text(encoding="utf-8")
    check("Worker release passes the metering gate before Wrangler",
          deploy.find("cloudflare-worker-release") < deploy.find('"$WRANGLER" versions upload'))

    control_plane = (REPO / "tools/control-plane.py").read_text(encoding="utf-8")
    check("provider call remains behind transactional cost admission",
          control_plane.find("ops.admit_job_cost") < control_plane.find("CognitionDispatcher(")
          and "budget admission refusal stops before every provider call" in
          (REPO / "ops/control-plane-runner-selftest.py").read_text(encoding="utf-8"))

    pilot = (REPO / "ops/automerge_pilot.py").read_text(encoding="utf-8")
    execute_start = pilot.find("def command_execute")
    check("automerge admits remote CI before it merges or dispatches",
          execute_start >= 0
          and execute_start < pilot.find('"github-actions-remote-ci"', execute_start)
          < pilot.find("execute_conditional_merge(", execute_start)
          < pilot.find("api.dispatch_workflow(", execute_start))

    print(f"\n{len(FAILED)} failure(s)" if FAILED else "\nall platform metering gate checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
