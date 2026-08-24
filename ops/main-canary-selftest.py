#!/usr/bin/env python3
"""main-canary-selftest.py — the paired suite for layers 2 and 3 of the
2026-08-23 CI-failures council: the debounced main canary and the merge freeze.

TWO THINGS ARE UNDER TEST AND THEY FAIL DIFFERENTLY.

The CANARY (.github/workflows/main-canary.yml) is a cost object. Its correctness
is almost entirely in properties a reader cannot see by looking at it running:
that the debounce sleep comes BEFORE the checkout, so a cancelled run in a merge
burst costs one billed minute instead of a checkout plus two setup actions plus
a pip install; that it runs the four measured classes and not the ten; that it
has no schedule, because the council forbade a new always-on job. Those are
asserted against the file, because there is nowhere else they exist.

The FREEZE (ops/main-canary-state.py) is a verdict object, and it is tested
behaviourally with the network mocked out. The bar it has to clear is Codex's
kill criterion — "kill any implementation under which a skipped or neutral check
accidentally satisfies branch protection" — so the cases that matter most here
are the ones where the answer is NOT a clean red or green: a burst where every
recent run was cancelled, an API that would not answer, a workflow that has
never run. Every one of them must refuse.

Exit 0 all cases pass · 1 a case failed.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CANARY = REPO / ".github" / "workflows" / "main-canary.yml"
PILOT = REPO / ".github" / "workflows" / "automerge-pilot.yml"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"

# The council's measured four: across the 30 most recent failed runs the failing
# classes were gates 24, migration 8, types 5, freshness 4. Six classes never
# failed at all. This tuple is the thing the canary is not allowed to drift from
# quietly — widening it is a cost decision and belongs in a change that says so.
COUNCIL_CLASSES = ("gates", "migration", "types", "freshness")
NEVER_FAILED = ("unit", "contract", "secret", "dependency", "binding", "artifact")

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def load_state_module():
    spec = importlib.util.spec_from_file_location(
        "main_canary_state", REPO / "ops" / "main-canary-state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the canary
def test_the_canary_is_event_driven_and_never_always_on():
    y = CANARY.read_text(encoding="utf-8")
    check("it triggers on push to main", "push:" in y and "branches: [main]" in y)
    check("it has NO schedule — the council forbade a new always-on job",
          "schedule:" not in y and "cron:" not in y,
          "ci.yml already carries the 06:20 UTC quiet-period backstop")
    check("it can still be run by hand when main's state must be re-established",
          "workflow_dispatch:" in y)


def test_the_debounce_is_real_and_comes_first():
    y = CANARY.read_text(encoding="utf-8")
    check("one canary per merge burst", "group: main-canary" in y
          and "cancel-in-progress: true" in y)
    sleep_at = y.find("sleep 90")
    checkout_at = y.find("actions/checkout")
    check("the debounce sleep exists", sleep_at != -1)
    check("the sleep is BEFORE the checkout, which is what makes a cancelled "
          "run cost one billed minute instead of a full setup",
          -1 < sleep_at < checkout_at, f"sleep at {sleep_at}, checkout at {checkout_at}")
    check("the job is bounded", "timeout-minutes:" in y)


def test_it_runs_the_measured_four_and_not_the_ten():
    y = CANARY.read_text(encoding="utf-8")
    marker = "for class in "
    check("the canary has a class loop", marker in y)
    if marker not in y:
        return
    listed = tuple(y.split(marker, 1)[1].split(";", 1)[0].split())
    check("the class loop is EXACTLY the council's measured four",
          listed == COUNCIL_CLASSES,
          f"found {listed}; widening this is a cost decision and must be "
          f"argued in the change that widens it")
    for c in NEVER_FAILED:
        check(f"it does not run {c}, which never failed on main in the window",
              c not in listed)
    check("no check logic lives in the workflow — every class is ops/ci.sh's",
          "ops/ci.sh --strict --only" in y)


def test_a_red_canary_stays_red_and_names_main():
    y = CANARY.read_text(encoding="utf-8")
    check("a failing class fails the run", "exit 1" in y)
    for weakener in ("continue-on-error", "|| true", "exit 0"):
        check(f"the canary cannot be weakened with {weakener!r}", weakener not in y)
    check("its failure NAMES MAIN rather than reading as somebody's PR",
          "MAIN IS RED" in y and "not any pull request's" in y)
    check("its failure names the move", "THE MOVE:" in y)
    check("it tells victims what their own red means",
          "INHERITED FROM MAIN" in y)


def test_the_canary_setup_has_not_drifted_from_ci_yml():
    """Two files install the same toolchain; a8c55a47 keeps the CHECKS in one
    place, and ops/ci.sh already does that. The SETUP is duplicated by
    necessity, so the drift is caught here instead."""
    y, ci = CANARY.read_text(encoding="utf-8"), CI_YML.read_text(encoding="utf-8")
    for pin in ("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
                "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"):
        check(f"pinned to the same commit as ci.yml: {pin.split('@')[0]}",
              pin in y and pin in ci, "actions are pinned to SHAs, never tags")
    for shared in ("fetch-depth: 0", "requirements.lock", "npm --prefix mcp-server ci",
                   "postgres:17", "neondb_owner", 'CARR_CI_PORTABLE_ONLY: "1"'):
        check(f"same environment as ci.yml: {shared}", shared in y and shared in ci)


# ------------------------------------------------------------------- the freeze
def test_freeze_reads_a_verdict_not_a_cancellation():
    mod = load_state_module()
    calls = []

    def fake(path):
        calls.append(path)
        return {"workflow_runs": [
            {"conclusion": "cancelled", "run_number": 9, "head_sha": "c" * 40},
            {"conclusion": None, "run_number": 8, "head_sha": "b" * 40},
            {"conclusion": "success", "run_number": 7, "head_sha": "a" * 40,
             "html_url": "https://example.invalid/7"},
        ]}
    mod.api = fake
    os.environ.pop("CARR_MAIN_FREEZE", None)
    os.environ["GITHUB_REPOSITORY"] = "carr/system"
    s = mod.state()
    check("cancelled and in-progress runs are skipped for the last real verdict",
          s["state"] == "green" and "run 7" in s["source"], s)
    check("it asked only the canary's own runs on main",
          calls and "main-canary.yml" in calls[0] and "branch=main" in calls[0], calls)


def test_every_not_knowing_refuses():
    mod = load_state_module()
    os.environ.pop("CARR_MAIN_FREEZE", None)
    os.environ["GITHUB_REPOSITORY"] = "carr/system"

    for label, payload in (("the API would not answer", None),
                           ("the workflow has never run", {"workflow_runs": []}),
                           ("a burst is in flight, all cancelled",
                            {"workflow_runs": [{"conclusion": "cancelled", "run_number": 3}]})):
        mod.api = lambda _p, _v=payload: _v
        s = mod.state()
        check(f"unknown when {label}", s["state"] == "unknown", s)
        code = require_green_exit(mod)
        check(f"--require-green REFUSES when {label} (Codex's kill criterion)",
              code == 1, f"exit {code}")


def require_green_exit(mod) -> int:
    argv, out = sys.argv, io.StringIO()
    sys.argv = ["main-canary-state.py", "--require-green"]
    try:
        with contextlib.redirect_stdout(out):
            return mod.main()
    finally:
        sys.argv = argv


def test_red_freezes_and_green_releases():
    mod = load_state_module()
    os.environ.pop("CARR_MAIN_FREEZE", None)
    os.environ["GITHUB_REPOSITORY"] = "carr/system"

    mod.api = lambda _p: {"workflow_runs": [
        {"conclusion": "failure", "run_number": 11, "head_sha": "f" * 40,
         "html_url": "https://example.invalid/11"}]}
    s = mod.state()
    check("a failed canary is red", s["state"] == "red", s)
    check("the red names the commit it was found on", s.get("sha") == "f" * 12, s)
    check("--require-green refuses on red", require_green_exit(mod) == 1)

    mod.api = lambda _p: {"workflow_runs": [
        {"conclusion": "success", "run_number": 12, "head_sha": "e" * 40}]}
    check("the next green canary IS the unfreeze — nothing to reset",
          mod.state()["state"] == "green" and require_green_exit(mod) == 0)


def test_the_manual_lever_can_freeze_but_never_force_green():
    mod = load_state_module()
    os.environ["GITHUB_REPOSITORY"] = "carr/system"
    called = []
    mod.api = lambda _p: called.append(_p) or {"workflow_runs": [
        {"conclusion": "success", "run_number": 12, "head_sha": "e" * 40}]}

    os.environ["CARR_MAIN_FREEZE"] = "on"
    s = mod.state()
    check("CARR_MAIN_FREEZE=on freezes even when the canary is green",
          s["state"] == "red" and not called, s)
    check("the manual freeze says how to clear itself", "unfreeze" in s["detail"], s)
    check("--require-green refuses under the manual freeze",
          require_green_exit(mod) == 1)

    # THE ASYMMETRY IS THE POINT. A lever that can declare a broken main healthy
    # is a verdict weakener wearing a convenience hat, and the council's first
    # constraint was that no verdict is weakened to improve the number.
    for forcing in ("off", "false", "green", "no"):
        os.environ["CARR_MAIN_FREEZE"] = forcing
        called.clear()
        mod.api = lambda _p: {"workflow_runs": [
            {"conclusion": "failure", "run_number": 13, "head_sha": "d" * 40}]}
        check(f"CARR_MAIN_FREEZE={forcing!r} cannot force a red main green",
              mod.state()["state"] == "red")
    os.environ.pop("CARR_MAIN_FREEZE", None)

    src = (REPO / "ops" / "main-canary-state.py").read_text(encoding="utf-8")
    check("there is no force-green anywhere in the door",
          'return {"state": "green"' not in src.replace(
              '"state": "green" if green else "red"', ""),
          "green is only ever derived from a successful canary run")


def test_the_pilot_asks_before_it_plans():
    y = PILOT.read_text(encoding="utf-8")
    check("the automerge pilot consults the freeze",
          "ops/main-canary-state.py --require-green" in y)
    check("it asks BEFORE planning, not after a 20-minute verify job",
          y.find("main-canary-state.py") < y.find("id: plan"),
          "the cheap refusal has to come first or it saves nothing")
    check("the plan job can read the canary's runs",
          "actions: read" in y)
    check("the manual lever reaches the pilot",
          "CARR_MAIN_FREEZE: ${{ vars.CARR_MAIN_FREEZE }}" in y)


def main():
    for fn in (test_the_canary_is_event_driven_and_never_always_on,
               test_the_debounce_is_real_and_comes_first,
               test_it_runs_the_measured_four_and_not_the_ten,
               test_a_red_canary_stays_red_and_names_main,
               test_the_canary_setup_has_not_drifted_from_ci_yml,
               test_freeze_reads_a_verdict_not_a_cancellation,
               test_every_not_knowing_refuses,
               test_red_freezes_and_green_releases,
               test_the_manual_lever_can_freeze_but_never_force_green,
               test_the_pilot_asks_before_it_plans):
        print(f"\n{fn.__name__}")
        try:
            fn()
        except Exception as exc:  # a crashing case is a failing case, never a skip
            check(f"{fn.__name__} raised", False, repr(exc))
    print(f"\nmain-canary-selftest: {len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
