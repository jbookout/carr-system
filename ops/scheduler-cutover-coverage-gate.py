#!/usr/bin/env python3
# doctrine: scheduler-cutover-coverage
"""How much of this Mac's scheduling has actually moved to the cloud, and did it slip.

WHY THIS EXISTS. "Migrate the recurring jobs off the laptop so the cloud runs
them and the Mac is a client" was a program item for weeks with no number
attached, and the number turned out to be the whole story: of the 23 launchd
jobs declared in this repository, THREE are bound to a control-plane workflow
through ops/config/control-plane-scheduler-cutover.v1.json. Twenty are outside
it. Meanwhile that same registry carries seventeen surfaces for the
ask-an-AI-client-later kind, so one half of the migration is well-trodden and
the other has barely started. Nobody could see that, because nothing asked.

WHAT WAS ALREADY CHECKED, and why it could not answer this. The control-plane
database gate compares the registry's DECLARED launchd surfaces against the
contract rows in the database, and the observation selftests check that a
declared surface reports itself correctly. Both are consistency checks over the
declared set. A job that appears in neither the registry nor the database is
consistent with itself and invisible to all of them. Coverage is the question
none of them asks.

WHY A RATCHET AND NOT A THRESHOLD. Three of twenty-three would fail on day one
and every day after, and a check that fails constantly is one people learn to
scroll past — the same trap as a warning printed on every push, which this
repository has already paid for once. So this fails only on a REGRESSION against
a recorded baseline: a new launchd job added without a registry surface, or a
surface removed from one that had it. Progress is reported, never demanded.

RAISING THE BASELINE IS THE POINT. When a job gains a surface, re-record the
baseline in the same change; the gate prints the command. The baseline can only
be raised by this gate's own writer, so it cannot be quietly lowered to make a
red run green.

WHAT IT DELIBERATELY DOES NOT CLAIM. It says nothing about whether a job COULD
move. Many cannot: they need a logged-in macOS session, the calendar store,
Apple Notes, local hardware or a local socket. Deciding that is a per-job
reading, and a screen over the files gets it wrong — an earlier pass put the
room bridge in the movable column when its own header says it exists to reach
local desks. This gate counts what IS bound, never what ought to be.

Exit 0 coverage held or improved · 1 regression · 2 the inputs cannot be read.

  ops/scheduler-cutover-coverage-gate.py
  ops/scheduler-cutover-coverage-gate.py --record   # raise the baseline
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(REPO, "ops", "config", "control-plane-scheduler-cutover.v1.json")
LAUNCHD_DIR = os.path.join(REPO, "ops", "launchd")
BASELINE = os.path.join(REPO, "ops", "config", "scheduler-cutover-coverage-baseline.json")


def fail(msg, code=2):
    print(f"scheduler-cutover-coverage: FAIL — {msg}", file=sys.stderr)
    return code


def read_state():
    """(all launchd labels in the repo, the subset bound to a workflow)."""
    with open(REGISTRY, encoding="utf-8") as fh:
        surfaces = json.load(fh)["surfaces"]
    bound = {s["locator"] for s in surfaces
             if s.get("scheduler_kind") == "launchd" and s.get("locator")}
    declared = {name[:-len(".plist")] for name in os.listdir(LAUNCHD_DIR)
                if name.endswith(".plist")}
    # A surface naming a plist that does not exist is a broken mapping, not
    # coverage. Counting it would let coverage rise by deleting a plist.
    return declared, bound & declared


def main(argv):
    try:
        declared, covered = read_state()
    except (OSError, ValueError, KeyError) as exc:
        return fail(f"cannot read the registry or the launchd declarations: {exc}")

    if "--record" in argv:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump({
                "_note": ("Coverage of this Mac's launchd jobs by control-plane workflow "
                          "surfaces. Raised by ops/scheduler-cutover-coverage-gate.py "
                          "--record in the same change that binds a job. Never lower it "
                          "to make a red run green — a regression is the signal."),
                "covered": sorted(covered),
                "covered_count": len(covered),
            }, fh, indent=1)
            fh.write("\n")
        print(f"scheduler-cutover-coverage: baseline recorded at {len(covered)} "
              f"of {len(declared)} launchd job(s)")
        return 0

    try:
        with open(BASELINE, encoding="utf-8") as fh:
            base = set(json.load(fh)["covered"])
    except (OSError, ValueError, KeyError) as exc:
        return fail(f"cannot read the baseline: {exc}")

    lost = sorted(base - covered)
    if lost:
        print("scheduler-cutover-coverage: FAIL — job(s) lost their workflow binding:",
              file=sys.stderr)
        for label in lost:
            print(f"    {label}", file=sys.stderr)
        print("  Each of these was bound to a control-plane workflow and is not now.\n"
              "  Either restore its surface in ops/config/control-plane-scheduler-cutover.v1.json,\n"
              "  or if the job was retired deliberately, re-record with --record in the same change.",
              file=sys.stderr)
        return 1

    gained = sorted(covered - base)
    uncovered = len(declared) - len(covered)
    if gained:
        print(f"ok  scheduler-cutover-coverage: {len(covered)} of {len(declared)} launchd "
              f"job(s) bound to a workflow — UP by {len(gained)}: {', '.join(gained)}")
        print(f"    raise the baseline in this change:  "
              f"ops/scheduler-cutover-coverage-gate.py --record")
        return 0

    print(f"ok  scheduler-cutover-coverage: {len(covered)} of {len(declared)} launchd "
          f"job(s) bound to a control-plane workflow; {uncovered} still scheduled only "
          f"on this Mac")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
