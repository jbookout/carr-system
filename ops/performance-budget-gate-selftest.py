#!/usr/bin/env python3
"""Hermetic contract tests for the release performance-budget gate."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "ops" / "performance-budget-gate.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(GATE), *args], capture_output=True, text=True)


def check(name: str, result: subprocess.CompletedProcess[str], code: int) -> bool:
    ok = result.returncode == code
    print(f"  {'ok' if ok else 'FAIL'}  {name}" + ("" if ok else f" (rc={result.returncode})"))
    return ok


def main() -> int:
    shared = ("--budget-ms", "1000", "--budget-ref", "runbook:worker-performance-v1",
              "--evidence-ref", "ops.run:performance-123")
    results = [
        check("under budget passes", run("--elapsed-ms", "999", *shared), 0),
        check("exact budget passes", run("--elapsed-ms", "1000", *shared), 0),
        check("over budget refuses", run("--elapsed-ms", "1001", *shared), 1),
        check("zero elapsed refuses", run("--elapsed-ms", "0", *shared), 2),
        check("negative elapsed refuses", run("--elapsed-ms", "-1", *shared), 2),
        check("empty evidence refuses", run("--elapsed-ms", "1", "--budget-ms", "1000",
                                              "--budget-ref", "runbook:worker-performance-v1",
                                              "--evidence-ref", ""), 2),
    ]
    if all(results):
        print("performance-budget-gate-selftest: budget evidence is bounded")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
