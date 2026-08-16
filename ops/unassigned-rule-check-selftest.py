#!/usr/bin/env python3
"""
unassigned-rule-check-selftest.py — fixtures for the half of
ops/rule-enforcement-map-check.py that refuses a rule left unclassified
(rule ab814a26: a rule ships with its enforcement, decided at creation —
recitation is not enforcement).

WHY A GRACE WINDOW RATHER THAN A FLAT REFUSAL, which is the whole design.
bin/sync-enforcement-map.py adds a placeholder entry for every newly ACTIVATED
rule, on purpose and correctly: classifying a rule is a judgment call a
mechanical hourly job must not make on a human's behalf. If the checker
refused every placeholder outright, the next taught rule would put every
session on the machine into a gate-integrity failure within the hour, and the
fix would be to delete the check. A guard that punishes the honest interim
state gets removed, and then nothing is checked at all.

So the placeholder is allowed to EXIST and not allowed to PERSIST. It carries
the date it was minted, and the checker refuses it once it is older than the
window. That makes "unassigned" a temporary state with a deadline instead of
the permanent resting place 132 rules had been sitting in — every one of them
carrying the same placeholder, none of them ever refused by anything.

WHAT MUST STAY TRUE:
  1. A fresh placeholder passes, so the hourly job never breaks the build.
  2. A stale placeholder FAILS, naming the rule and its age.
  3. A placeholder with no date at all fails, since an undated one can never
     age out and would be permanent by construction.
  4. A real planned_control passes at any age.
  5. The other enforcement classes are untouched by this check.

RUNNING IT. No database, no network, no vault:

    .venv/bin/python ops/unassigned-rule-check-selftest.py
"""

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "ops" / "rule-enforcement-map-check.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("map_check", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load()

if not hasattr(mod, "stale_placeholder"):
    print("  FAIL  the checker has no stale_placeholder() yet")
    print("\n1 check(s) failed: not implemented")
    sys.exit(1)

stale = mod.stale_placeholder
today = date.today()
fresh = (today - timedelta(days=2)).isoformat()
old = (today - timedelta(days=60)).isoformat()

print("\nops/rule-enforcement-map-check.py — an unassigned rule may not persist")

check("a freshly minted placeholder passes",
      not stale(f"pending classification — see rule-enforceability audit {fresh}"))
check("a stale placeholder is refused",
      stale(f"pending classification — see rule-enforceability audit {old}"))
check("an undated placeholder is refused",
      stale("pending classification — see the enforceability audit"),
      "an undated placeholder can never age out, so it is permanent by construction")
check("a real planned_control passes however old the text",
      not stale("pre-commit check: refuse the commit unless the baseline moves with it"))
check("a real planned_control mentioning a date still passes",
      not stale("extend the sync job's dropped-id path, per the 2026-01-01 design note"))
check("empty text is not treated as a stale placeholder",
      not stale(""), "absence is caught by the existing lacks-planned_control error")

# The live map must have no stale placeholders left — this is the regression
# lock on the 132 that were sitting there.
import json
live = json.loads((REPO / "ops" / "config" / "rule-enforcement-map.json").read_text())
lingering = [rid for rid, d in live["rule_controls"].items()
             if d.get("enforcement_class") == "unbuilt"
             and stale(str(d.get("planned_control", "")))]
check("the live map carries no stale placeholder", not lingering,
      f"{len(lingering)} left: {lingering[:5]}")

unassigned = [rid for rid, d in live["rule_controls"].items()
              if d.get("enforcement_class") == "unbuilt"
              and "pending classification" in str(d.get("planned_control", ""))]
check("no rule is still awaiting classification at all", not unassigned,
      f"{len(unassigned)} left: {unassigned[:5]}")

print(f"\n{passed} passed, {len(failures)} failed")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("UNASSIGNED RULE CHECK SELFTEST PASSED: a placeholder may exist briefly "
      "and may not persist, and the live map holds none.")
