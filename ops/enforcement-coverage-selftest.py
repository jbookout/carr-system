#!/usr/bin/env python3
"""ops/enforcement-coverage-selftest.py — acceptance test for
ops/enforcement-coverage-check.py.

WHAT THE CHECK IS FOR. On 2026-08-15 Joe said "we need to stop the bleeding",
where the bleeding was 42 active rules the coverage map called `unbuilt` — told
to sessions, enforceable, with nothing able to refuse them. A session started
working that list and reached rule 4a53ff82 (worktree-per-session), whose map
entry read `unbuilt` with a `planned_control` describing the hook to build.

That hook already existed. hooks/canonical-edit-gate.py had been built the day
before, denying exactly what the planned_control described, blessed in
ops/config/gate-baseline.json, and wired into the live PreToolUse set. The map
simply did not know about it. The session came within one step of building a
second gate for a rule that was already enforced.

The map was wrong in a specific, mechanical way: 21 of 40 blessed gate files
were named by NO control in the catalog. A coverage inventory that cannot see
half the enforcement it is inventorying produces a number nobody should act on,
and the 42 was that number.

WHAT THIS CHECKS, and it is deliberately narrow: every file in the blessed gate
baseline is either named by some control in the map's catalog, or listed in the
recorded backlog with a reason. New orphans fail immediately. The existing ones
are a tracked debt that may only shrink — the same shape as a lint baseline, and
for the same reason: a check that fails on day one gets muted on day one.

RUN IT:
    python3 ops/enforcement-coverage-selftest.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(REPO, "ops", "enforcement-coverage-check.py")

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)


def run(tmp: str, baseline: dict, mapping: dict, backlog: list[str] | None = None,
        extra: list[str] | None = None):
    """Run the check against a fixture triple, never the real repo."""
    bp = os.path.join(tmp, "gate-baseline.json")
    mp = os.path.join(tmp, "rule-enforcement-map.json")
    kp = os.path.join(tmp, "backlog.json")
    with open(bp, "w") as fh:
        json.dump(baseline, fh)
    with open(mp, "w") as fh:
        json.dump(mapping, fh)
    with open(kp, "w") as fh:
        json.dump({"unmapped_gates": backlog or []}, fh)
    p = subprocess.run([sys.executable, CHECK, "--baseline", bp, "--map", mp,
                        "--backlog", kp, *(extra or [])],
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


BASE = {"hashes": {"alpha-gate.py": "x", "beta-gate.py": "y"}, "contracts": {}}
MAP_BOTH = {"control_catalog": {
    "a": {"implementation": ["hooks/alpha-gate.py"]},
    "b": {"implementation": ["hooks/beta-gate.py"]}}, "rule_controls": {}}
MAP_ONE = {"control_catalog": {
    "a": {"implementation": ["hooks/alpha-gate.py"]}}, "rule_controls": {}}

print("enforcement-coverage-selftest — the coverage map must be able to see "
      "every gate that is actually live")

check("the check exists", os.path.exists(CHECK), CHECK)
if not os.path.exists(CHECK):
    sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    rc, out = run(tmp, BASE, MAP_BOTH)
    check("a fully mapped baseline passes", rc == 0, out[:300])

with tempfile.TemporaryDirectory() as tmp:
    rc, out = run(tmp, BASE, MAP_ONE)
    check("A GATE NO CONTROL NAMES IS A FAILURE — the 2026-08-15 case", rc != 0,
          out[:300])
    check("...and the failure names the orphaned gate", "beta-gate.py" in out,
          out[:300])

with tempfile.TemporaryDirectory() as tmp:
    rc, out = run(tmp, BASE, MAP_ONE, backlog=["beta-gate.py"])
    check("a recorded backlog entry holds it without failing", rc == 0, out[:300])

with tempfile.TemporaryDirectory() as tmp:
    # The debt may only shrink. A gate mapped since the backlog was written must
    # be removed from it, or the backlog becomes a place things go to be
    # forgotten — which is the failure this whole check exists to end.
    rc, out = run(tmp, BASE, MAP_BOTH, backlog=["beta-gate.py"])
    check("a backlog entry that is now MAPPED fails, so the debt cannot go stale",
          rc != 0, out[:300])
    check("...and says the entry can be removed",
          "remove" in out.lower() or "no longer" in out.lower(), out[:300])

with tempfile.TemporaryDirectory() as tmp:
    # A backlog naming a gate that no longer exists is also stale bookkeeping.
    rc, out = run(tmp, BASE, MAP_BOTH, backlog=["deleted-gate.py"])
    check("a backlog entry for a gate that no longer exists fails", rc != 0,
          out[:300])

with tempfile.TemporaryDirectory() as tmp:
    rc, out = run(tmp, BASE, MAP_ONE, extra=["--json"])
    try:
        payload = json.loads(out)
    except Exception:
        payload = {}
    check("--json returns the orphan list as data",
          payload.get("orphans") == ["beta-gate.py"], out[:300])

# The real repository: the check must run against it and say something true.
with tempfile.TemporaryDirectory() as tmp:
    p = subprocess.run([sys.executable, CHECK], capture_output=True, text=True,
                       cwd=REPO, timeout=120)
    check("it runs against the real repository without crashing",
          p.returncode in (0, 1), (p.stdout + p.stderr)[:300])

print()
if FAILED:
    print(f"FAILED {len(FAILED)} check(s):")
    for _label in FAILED:
        print(f"  - {_label}")
    sys.exit(1)
print("all checks passed")
sys.exit(0)
