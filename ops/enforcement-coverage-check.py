#!/usr/bin/env python3
"""ops/enforcement-coverage-check.py — the coverage map must be able to SEE
every gate that is actually live.

WHY THIS EXISTS. Joe, 2026-08-15: "we need to stop the bleeding." The bleeding
was 42 active rules that ops/config/rule-enforcement-map.json called `unbuilt` —
rules a session is told to obey with nothing able to refuse it.

A session started working that list and reached rule 4a53ff82,
worktree-per-session, whose entry read `unbuilt` and carried a `planned_control`
describing the hook that should exist. THAT HOOK ALREADY EXISTED.
hooks/canonical-edit-gate.py had been built the day before, denying exactly what
the planned_control described, blessed in the gate baseline, and wired into the
live PreToolUse set. The map did not know about it, so the session came one step
from building a second gate for a rule that was already enforced.

Measured the same hour: 21 of 40 blessed gate files were named by NO control in
the catalog. A coverage inventory blind to half the enforcement it inventories
produces a number nobody should act on, and 42 was that number.

WHAT IT CHECKS, deliberately narrow: every file in the blessed gate baseline is
either named by some control in the map's catalog, or recorded in the backlog
with a reason. This is a structural question with one right answer, so it is code
(rule 5e89c211) rather than a periodic read-through by a model.

WHY A BACKLOG FILE AND NOT A CLEAN FAILURE. Twenty-one orphans existed the day
this was written. A check that fails on day one gets muted on day one, and a
muted check is worse than none. The backlog is the recorded debt; a NEW orphan
fails immediately, which is where the bleeding actually stops.

THE DEBT MAY ONLY SHRINK, and that is enforced in both directions: a backlog
entry that has since been mapped FAILS, and so does one naming a gate that no
longer exists. Without those, the backlog becomes the place things go to be
forgotten — the failure this whole check exists to end.

SHARED HELPER MODULES ARE NOT GATES. conduct_patterns.py, cmd_text.py and their
kin are imported BY gates and are blessed so tampering is detectable, but they
deny nothing and no control should claim them. They live in the backlog with
that as their stated reason rather than being silently filtered, because a silent
filter is how a real gate gets excluded by accident.

RUN IT:
    python3 ops/enforcement-coverage-check.py
    python3 ops/enforcement-coverage-check.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASELINE = os.path.join(REPO, "ops", "config", "gate-baseline.json")
DEFAULT_MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
DEFAULT_BACKLOG = os.path.join(REPO, "ops", "config", "enforcement-coverage-backlog.json")


def named_files(mapping: dict) -> set[str]:
    """Every gate basename any control in the catalog claims.

    Basenames, not paths: the catalog writes 'hooks/x.py' while the baseline
    keys on 'x.py', and a few entries carry a 'path:' or 'external:' prefix.
    Comparing basenames is what makes those agree without rewriting either file.
    """
    out: set[str] = set()
    for control in (mapping.get("control_catalog") or {}).values():
        for impl in control.get("implementation", []):
            out.add(impl.split(":")[-1].split("/")[-1])
    return out


def audit(baseline: dict, mapping: dict, backlog: dict) -> dict:
    gates = set((baseline.get("hashes") or {}).keys())
    named = named_files(mapping)
    recorded = set(backlog.get("unmapped_gates") or [])

    orphans = sorted(g for g in gates if g not in named)
    new_orphans = sorted(g for g in orphans if g not in recorded)
    now_mapped = sorted(g for g in recorded if g in named)
    gone = sorted(g for g in recorded if g not in gates)

    return {"gates": len(gates), "named": len(gates) - len(orphans),
            "orphans": orphans, "new_orphans": new_orphans,
            "now_mapped": now_mapped, "vanished": gone}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--map", dest="mapping", default=DEFAULT_MAP)
    ap.add_argument("--backlog", default=DEFAULT_BACKLOG)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        baseline = json.load(open(args.baseline))
        mapping = json.load(open(args.mapping))
    except Exception as e:
        print(f"enforcement-coverage: cannot read inputs ({type(e).__name__}: {e})")
        return 1
    try:
        backlog = json.load(open(args.backlog))
    except Exception:
        backlog = {"unmapped_gates": []}   # absent backlog means no debt recorded

    result = audit(baseline, mapping, backlog)

    if args.json:
        print(json.dumps(result, indent=2))
        return 1 if (result["new_orphans"] or result["now_mapped"]
                     or result["vanished"]) else 0

    bad = False
    if result["new_orphans"]:
        bad = True
        print("enforcement-coverage: FAIL — a live blessed gate that NO control "
              "in the map names, and that is not in the recorded backlog:")
        for g in result["new_orphans"]:
            print(f"    {g}")
        print("  This is how rule 4a53ff82 read 'unbuilt' on 2026-08-15 while "
              "hooks/canonical-edit-gate.py was already enforcing it.")
        print("  FIX: add a control naming it in rule-enforcement-map.json's "
              "control_catalog and point the rules it enforces at that control. "
              "If it is a shared helper rather than a gate, record it in "
              f"{os.path.relpath(args.backlog, REPO)} with that reason.")
    if result["now_mapped"]:
        bad = True
        print("enforcement-coverage: FAIL — these are in the backlog but a control "
              "now names them; remove them from the backlog so the debt stays true:")
        for g in result["now_mapped"]:
            print(f"    {g}")
    if result["vanished"]:
        bad = True
        print("enforcement-coverage: FAIL — the backlog names gates that no longer "
              "exist in the baseline; remove them:")
        for g in result["vanished"]:
            print(f"    {g}")

    if not bad:
        held = len(result["orphans"])
        print(f"enforcement-coverage: OK — {result['named']} of {result['gates']} "
              f"blessed gates are named by a control"
              + (f"; {held} held in the recorded backlog" if held else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
