#!/usr/bin/env python3
"""Hermetic tests for the exact, fail-closed launchd observation seam."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.launchd_observation import LaunchdObservationError, label_state, loaded_labels

FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def main() -> int:
    calls: list[list[str]] = []
    domain = """gui/501 = {
    services = {
           0      0  com.carr.loaded
           0      0  com.carr.second
           0      0  com.apple.other
    }
    disabled services = {
        \"com.carr.absent\" => disabled
    }
}
"""

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command == ["/bin/launchctl", "print", "gui/501"]:
            return subprocess.CompletedProcess(command, 0, domain, "")
        if command[-1] in {"gui/501/com.carr.loaded", "gui/501/com.carr.second"}:
            return subprocess.CompletedProcess(command, 0, "job", "")
        return subprocess.CompletedProcess(
            command, 113, "", 'Could not find service "com.carr.absent" in domain for user gui: 501'
        )

    observed = loaded_labels(runner=runner, uid=501)
    check("domain inventory is filtered to services and exact reads confirm loaded labels",
          observed == {"com.carr.loaded", "com.carr.second"})
    check("loaded inventory uses print, never launchctl list",
          all(command[1] == "print" for command in calls))
    check("exact not-found is classified as absence",
          label_state("com.carr.absent", runner=runner, uid=501) == "not_found")

    def error_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "Operation not permitted")

    try:
        label_state("com.carr.error", runner=error_runner, uid=501)
        error_raised = False
    except LaunchdObservationError:
        error_raised = True
    check("non-not-found errors fail closed rather than becoming absence", error_raised)

    def domain_error_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "Operation not permitted")

    try:
        loaded_labels(runner=domain_error_runner, uid=501)
        domain_error_raised = False
    except LaunchdObservationError:
        domain_error_raised = True
    check("domain read errors fail closed", domain_error_raised)
    print(f"launchd observation selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
