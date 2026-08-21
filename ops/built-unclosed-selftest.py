#!/usr/bin/env python3
"""built-unclosed-selftest.py — acceptance test for ops/built_unclosed.py.

WHY THIS EXISTS. capability-program said completed=0/51 and a session read that
as "nothing is built" and started a new build. The artifacts already exist on
main — completed counts confirmed_closed attestations, not code on disk — so
the number was attestation truth, not build truth. A session that treats
"not confirmed_closed" as "not built" rebuilds work that already landed.

The latch this tests is the missing second number: built_unclosed, the count of
Work Requests whose evidence paths all exist on disk AND whose state is not
confirmed_closed. The Worker cannot stat the repo; this detector can, because
it runs in local hooks that already see the tree.

Run: python3 ops/built-unclosed-selftest.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MOD = os.path.join(REPO, "ops", "built_unclosed.py")

_spec = importlib.util.spec_from_file_location("built_unclosed", MOD)
assert _spec and _spec.loader, f"cannot load {MOD}"
bu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bu)

failures: list[str] = []
CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def _rows(*items):
    """Build fixture rows from (ref, state, [evidence_paths]) tuples."""
    return [{"ref": r, "state": s, "evidence": e} for r, s, e in items]


# ── detect_built_unclosed: the core detector ──────────────────────────────


@case("confirmed_closed is not built-unclosed even when evidence exists")
def _(assert_):
    root = tempfile.mkdtemp()
    # create the evidence files so they exist
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(("WR-AI-001", "confirmed_closed", ["ops/ai_eval.py",
                                                       "evals/ai/model-boundary.v1.json"]))
    result = bu.detect_built_unclosed(rows, root)
    assert_(result == [],
            f"confirmed_closed with all-evidence-present must NOT be built_unclosed: {result!r}")


@case("not-confirmed_closed with all evidence present IS built-unclosed")
def _(assert_):
    root = tempfile.mkdtemp()
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(("WR-AI-006", "ready", ["ops/ai_eval.py",
                                          "evals/ai/model-boundary.v1.json"]))
    result = bu.detect_built_unclosed(rows, root)
    assert_([r["ref"] for r in result] == ["WR-AI-006"],
            f"not-confirmed_closed + all evidence present => built_unclosed: {result!r}")


@case("empty evidence is NOT landed")
def _(assert_):
    root = tempfile.mkdtemp()
    rows = _rows(("WR-AI-005", "ready", []),
                 ("WR-AI-006", "ready", []))
    result = bu.detect_built_unclosed(rows, root)
    assert_(result == [],
            f"empty evidence must not be built_unclosed (do not guess): {result!r}")


@case("one missing path => NOT landed")
def _(assert_):
    root = tempfile.mkdtemp()
    exists = os.path.join(root, "ops/ai_eval.py")
    os.makedirs(os.path.dirname(exists), exist_ok=True)
    open(exists, "w").write("x")
    # second path does NOT exist
    rows = _rows(("WR-AI-006", "ready", ["ops/ai_eval.py",
                                          "evals/ai/does-not-exist.v1.json"]))
    result = bu.detect_built_unclosed(rows, root)
    assert_(result == [],
            f"one missing evidence path => NOT built_unclosed: {result!r}")


@case("mixed: some built-unclosed, some not")
def _(assert_):
    root = tempfile.mkdtemp()
    for p in ["ops/ai_eval.py"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(
        ("WR-AI-001", "confirmed_closed", ["ops/ai_eval.py"]),
        ("WR-AI-002", "ready", ["ops/ai_eval.py"]),         # built-unclosed
        ("WR-AI-003", "ready", []),                          # empty evidence — not landed
        ("WR-AI-004", "ready", ["ops/nonexistent.py"]),      # missing path — not landed
    )
    result = bu.detect_built_unclosed(rows, root)
    refs = [r["ref"] for r in result]
    assert_(refs == ["WR-AI-002"],
            f"only WR-AI-002 should be built_unclosed: {refs!r}")


# ── landed_in_repo ─────────────────────────────────────────────────────────


@case("landed_in_repo counts all-evidence-present regardless of state")
def _(assert_):
    root = tempfile.mkdtemp()
    for p in ["ops/ai_eval.py", "evals/ai/model-boundary.v1.json"]:
        full = os.path.join(root, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").write("x")
    rows = _rows(
        ("WR-AI-001", "confirmed_closed", ["ops/ai_eval.py"]),  # landed but closed
        ("WR-AI-002", "ready", ["ops/ai_eval.py"]),               # landed and unclosed
        ("WR-AI-003", "ready", []),                               # not landed
    )
    count = bu.landed_in_repo(rows, root)
    assert_(count == 2,
            f"landed_in_repo should be 2 (closed+unclosed with evidence present): {count}")


@case("landed_in_repo is 0 when no evidence paths exist")
def _(assert_):
    root = tempfile.mkdtemp()
    rows = _rows(("WR-AI-001", "ready", []),
                 ("WR-AI-002", "confirmed_closed", []))
    count = bu.landed_in_repo(rows, root)
    assert_(count == 0, f"no evidence => landed_in_repo=0: {count}")


# ── state variations: blocked, failed, needs-Joe, verification ─────────────


@case("blocked/failed/verification states are built-unclosed when evidence exists")
def _(assert_):
    root = tempfile.mkdtemp()
    p = os.path.join(root, "ops/ai_eval.py")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("x")
    rows = _rows(
        ("WR-AI-001", "blocked", ["ops/ai_eval.py"]),
        ("WR-AI-002", "failed", ["ops/ai_eval.py"]),
        ("WR-AI-003", "verification", ["ops/ai_eval.py"]),
        ("WR-AI-004", "needs_joe", ["ops/ai_eval.py"]),
    )
    result = bu.detect_built_unclosed(rows, root)
    refs = sorted(r["ref"] for r in result)
    assert_(refs == ["WR-AI-001", "WR-AI-002", "WR-AI-003", "WR-AI-004"],
            f"all non-confirmed_closed states with evidence are built_unclosed: {refs!r}")


# ── load_live_rows: DB helper must not be required for unit tests ──────────


@case("load_live_rows returns None when CARR_DB_EXPORTER_URL is absent")
def _(assert_):
    # Save and clear the env var so the test is deterministic
    saved = os.environ.pop("CARR_DB_EXPORTER_URL", None)
    try:
        rows = bu.load_live_rows()
        assert_(rows is None,
                f"load_live_rows without DB should return None, got {rows!r}")
    finally:
        if saved is not None:
            os.environ["CARR_DB_EXPORTER_URL"] = saved


def main():
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
