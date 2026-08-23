#!/usr/bin/env python3
"""Fixtures for the scheduler cutover coverage ratchet.

Every case builds its own tiny tree, so this measures the GATE and says nothing
about the real inventory — which is why the gate itself also runs against this
repository in ops/ci.sh, beside the other inventory checks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "ops" / "scheduler-cutover-coverage-gate.py"
passed, failures = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(jobs, bound, baseline, args=()):
    """jobs: launchd labels present. bound: labels the registry binds."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "ops" / "launchd").mkdir(parents=True)
        (root / "ops" / "config").mkdir(parents=True)
        for label in jobs:
            (root / "ops" / "launchd" / f"{label}.plist").write_text("<plist/>")
        (root / "ops" / "config" / "control-plane-scheduler-cutover.v1.json").write_text(
            json.dumps({"surfaces": [
                {"locator": b, "workflow_key": f"{b}-wf", "scheduler_kind": "launchd"}
                for b in bound]}))
        if baseline is not None:
            (root / "ops" / "config" / "scheduler-cutover-coverage-baseline.json").write_text(
                json.dumps({"covered": sorted(baseline), "covered_count": len(baseline)}))
        gate = root / "ops" / "scheduler-cutover-coverage-gate.py"
        gate.write_text(GATE.read_text())
        p = subprocess.run([sys.executable, str(gate), *args],
                           capture_output=True, text=True)
        return p.returncode, (p.stdout or "") + (p.stderr or "")


print("\nops/scheduler-cutover-coverage-gate.py — coverage is a ratchet, not a threshold")

# ── the ordinary case: nothing moved, nothing lost ──────────────────────────
rc, out = run(["a", "b", "c"], ["a"], ["a"])
check("unchanged coverage passes", rc == 0, out[:120])
check("and it reports the real shortfall rather than only the good news",
      "2 still scheduled only on this Mac" in out, out[:160])

# ── the whole point: a binding disappears ───────────────────────────────────
rc, out = run(["a", "b", "c"], [], ["a"])
check("a job that LOSES its workflow binding fails", rc == 1)
check("and the failure names the job", "a" in out and "lost their workflow binding" in out,
      out[:160])

# ── coverage cannot be bought by deleting the job ───────────────────────────
# Without the intersection against jobs that exist, removing a bound plist would
# shrink both sides and look like a clean run.
rc, out = run(["b", "c"], ["a"], ["a"])
check("deleting a bound job is a regression, not an improvement", rc == 1,
      "the bound set is intersected with the jobs that actually exist")

# ── progress is reported, never demanded ────────────────────────────────────
rc, out = run(["a", "b", "c"], ["a", "b"], ["a"])
check("gaining a binding passes", rc == 0)
check("and it says the baseline should be raised in the same change",
      "--record" in out, out[:200])

# ── a new unbound job does not fail the push ────────────────────────────────
rc, out = run(["a", "b", "c", "d"], ["a"], ["a"])
check("adding an unbound job does not fail", rc == 0,
      "three of twenty-three would fail on day one; a check that always fails is scrolled past")
check("but the new job is counted in the shortfall",
      "3 still scheduled only on this Mac" in out, out[:160])

# ── the inputs must be readable, and a missing one is not a pass ────────────
rc, out = run(["a"], ["a"], None)
check("a missing baseline exits 2, not 0", rc == 2, out[:160])

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("SCHEDULER CUTOVER COVERAGE SELFTEST PASSED: coverage can rise freely and "
      "cannot fall silently.")
