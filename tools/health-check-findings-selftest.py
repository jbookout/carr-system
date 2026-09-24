#!/usr/bin/env python3
"""health-check-findings-selftest.py — offline proof of tools/health-check.py's
--findings-json schema, added for PR #1237's independent-review fixes (points
C, D on the finding schema; the "every rc=1 path emits a hard_error finding"
requirement of point A/D).

health-check.py cannot be imported as a plain module: every top-level
statement below its `_reader_args` call runs unconditionally at import time
(the normal path ends in `sys.exit(_canonical_health())`, the --recovery path
falls through into ~1800 lines of legacy Drive-recovery code ending in its own
`sys.exit(rc)`), and `_canonical_health()` itself dials the live production
database. Neither is safe or hermetic to run from a selftest. So this file
proves two different things two different ways, BOTH WITHOUT EXECUTING THE
LIVE HEALTH CHECK:

  1. THE FINDING-RECORDING/JSON-WRITING CODE ITSELF is small, self-contained,
     and touches no database: `_canonical_finding` and `_write_findings_json`.
     This file extracts just those two function definitions out of
     tools/health-check.py's AST and execs them into an isolated namespace
     (with `_FINDINGS = []`, `json`, `os`, `datetime`, `timezone` provided),
     then calls them directly. This is the actual shipped code, not a
     reimplementation, run hermetically.

  2. THE INVARIANT "every rc=1 code path records a finding, and every
     structural (whole-section-unreadable) rc=1 path records one with
     hard_error=True" is checked STATICALLY against the real source of
     `_canonical_health` and `_canonical_contradiction_alarm` (which backs
     one of `_canonical_health`'s rc=1 branches) via `ast`, walking each
     block of statements in source order and requiring a `_canonical_finding`
     call somewhere before (or inside an earlier sibling of) any `rc = 1` /
     `return 1` in that same block. A business-count finding (e.g.
     rule_enforcement's "98 active rule gaps") is deliberately NOT
     hard_error=True — see the regression-diff test in
     ops/release-pipeline-selftest.py's HealthGate class, which pins that a
     count DECREASE must not fail a release. hard_error is reserved for a
     section that could not be read at all, so this file separately checks by
     name that every known structural key (source_unreadable,
     export_unreadable, job_ledger, control_state, repo_status,
     registry_integrity, credential_health, canonical_health_refused) is
     always recorded with hard_error=True.
"""
from __future__ import annotations

import ast
import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HEALTH_CHECK_PATH = Path(__file__).resolve().parent / "health-check.py"
SOURCE = HEALTH_CHECK_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(HEALTH_CHECK_PATH))

# Keys that mean "this whole section of run.sh health could not be read",
# which must always carry hard_error=True (see the module docstring).
STRUCTURAL_KEYS = {
    "canonical_health_refused", "source_unreadable", "export_unreadable",
    "job_ledger", "control_state", "repo_status", "registry_integrity",
    "credential_health",
}


def _find_function(name: str) -> ast.FunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {HEALTH_CHECK_PATH}")


def _load_finding_functions() -> dict:
    """Exec just `_canonical_finding` and `_write_findings_json` — no other
    top-level code in health-check.py runs."""
    mod = ast.Module(body=[_find_function("_canonical_finding"),
                            _find_function("_write_findings_json")],
                     type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {"_FINDINGS": [], "json": json, "datetime": datetime, "timezone": timezone}
    import os as _os
    ns["os"] = _os
    exec(compile(mod, str(HEALTH_CHECK_PATH), "exec"), ns)  # noqa: S102 — trusted repo source
    return ns


# _canonical_contradiction_alarm is a verified delegate, not a stand-in: its
# own rc=1-equivalent path (`return 1` in its RED branch) is separately
# proven to have a preceding _canonical_finding(...) call by the same check
# below (see test_every_rc1_path_has_a_preceding_finding_call), so a call to
# it from _canonical_health counts as "a finding was recorded" wherever its
# truthy return is what sets rc = 1.
DELEGATE_FUNCS = {"_canonical_finding", "_canonical_contradiction_alarm"}


def _contains_finding_call(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in DELEGATE_FUNCS):
            return True
    return False


def _finding_call_keys_with_kw(node: ast.AST, kw: str, value: bool) -> set[str]:
    """Every string literal key of a `_canonical_finding("key", ..., kw=value)`
    call anywhere in `node`, restricted to calls whose `kw` keyword argument is
    the literal `value`."""
    hits = set()
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_canonical_finding"):
            continue
        if not n.args or not isinstance(n.args[0], ast.Constant) or not isinstance(n.args[0].value, str):
            continue
        for keyword in n.keywords:
            if keyword.arg == kw and isinstance(keyword.value, ast.Constant) and keyword.value.value is value:
                hits.add(n.args[0].value)
    return hits


def _all_finding_call_keys(node: ast.AST) -> set[str]:
    keys = set()
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_canonical_finding" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            keys.add(n.args[0].value)
    return keys


def _is_rc_one_assign(stmt: ast.stmt) -> bool:
    return (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id == "rc"
            and isinstance(stmt.value, ast.Constant) and stmt.value.value == 1)


def _is_return_one(stmt: ast.stmt) -> bool:
    return (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant)
            and stmt.value.value == 1)


def _check_block(body: list, errors: list, where: str, seen_finding: bool = False) -> bool:
    """Every `rc = 1` / `return 1` reachable from THIS statement list must be
    preceded by at least one `_canonical_finding(...)` call reachable from an
    earlier point in the same enclosing scope — including a finding call made
    by an earlier SIBLING statement's own nested block (e.g. a for-loop that
    reports one finding per bad item, followed by a sibling `if bad: rc = 1`
    that itself calls no finding function). `seen_finding` is the state
    carried in from the enclosing block, and recursion into a nested body
    passes the CURRENT running state rather than resetting it, so that
    inherited case is not a false positive. Returns the updated state (not
    used by callers today, but keeps the function honest about what it
    tracks)."""
    for stmt in body:
        if _is_rc_one_assign(stmt) or _is_return_one(stmt):
            if not seen_finding:
                errors.append(f"{where}:{stmt.lineno}: rc=1/return 1 with no preceding "
                              f"_canonical_finding(...) call in the same or an enclosing block")
        if _contains_finding_call(stmt):
            seen_finding = True
        # Recurse into this statement's own nested blocks so a violation
        # buried inside an `if`/`for`/`try` is still caught, carrying the
        # running seen_finding state in (see docstring).
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(stmt, field, None)
            if isinstance(nested, list) and nested and all(isinstance(x, ast.stmt) for x in nested):
                _check_block(nested, errors, where, seen_finding)
        for handler in getattr(stmt, "handlers", []) or []:
            _check_block(handler.body, errors, where, seen_finding)
    return seen_finding


def _check_function_top_level_isolated(func: ast.FunctionDef, errors: list, where: str) -> None:
    """Drives `_check_block` once per TOP-LEVEL statement of `func`, each
    with a FRESH `seen_finding=False`, instead of one single pass across the
    whole function body sharing one running `seen_finding`.

    The single-pass form was vacuous (point 3 of the second round of an
    independent review of PR #1237, proven with a mutation test below): a
    finding call recorded ANYWHERE in one top-level section (e.g. the
    exports section's `for` loop) set `seen_finding = True` for the rest of
    the SAME `for stmt in body` walk over the function's top-level
    statements — so it silently covered every later, wholly unrelated
    top-level section (e.g. a later `if CANONICAL_SECTION in ("all",
    "jobs"):` block) too, even one that recorded no finding at all before
    its own `rc = 1`.

    `_check_block`'s OWN within-block threading (an accumulating for-loop
    finding covering a later sibling `if: rc = 1` inside the SAME compound
    statement) is still exactly what a legitimate pattern in this file
    needs and is preserved here — those siblings live inside one top-level
    statement's own nested body, so a single `_check_block` call over that
    one top-level statement still sees them in order. Only the CROSS-
    top-level-statement leak is cut, by giving each top-level statement its
    own isolated `_check_block` call."""
    for stmt in func.body:
        _check_block([stmt], errors, where, seen_finding=False)


class FindingFunctions(unittest.TestCase):
    """The real _canonical_finding/_write_findings_json, execed in isolation."""

    def setUp(self):
        self.ns = _load_finding_functions()
        self.finding = self.ns["_canonical_finding"]
        self.write = self.ns["_write_findings_json"]

    def test_default_shape(self):
        self.finding("export_receipt", "LATEST FAILED vendors.xlsx", subject="vendors.xlsx")
        row = self.ns["_FINDINGS"][0]
        self.assertEqual(row["key"], "export_receipt")
        self.assertEqual(row["subject"], "vendors.xlsx")
        self.assertEqual(row["count"], 1)
        self.assertFalse(row["hard_error"])
        self.assertFalse(row["time_rolling"])

    def test_duplicate_key_and_subject_accumulates_count_not_a_second_line(self):
        # The probe's scenario C: the SAME job fails twice in one run and must
        # not collapse into "no change" against a baseline of one failure.
        self.finding("job_terminal_failure", "nightly-export failed", subject="nightly-export")
        self.finding("job_terminal_failure", "nightly-export failed", subject="nightly-export")
        self.assertEqual(len(self.ns["_FINDINGS"]), 1)
        self.assertEqual(self.ns["_FINDINGS"][0]["count"], 2)

    def test_different_subject_is_a_separate_finding(self):
        self.finding("export_receipt", "LATEST FAILED a.xlsx", subject="a.xlsx")
        self.finding("export_receipt", "LATEST FAILED b.xlsx", subject="b.xlsx")
        self.assertEqual(len(self.ns["_FINDINGS"]), 2)
        subjects = {row["subject"] for row in self.ns["_FINDINGS"]}
        self.assertEqual(subjects, {"a.xlsx", "b.xlsx"})

    def test_hard_error_and_time_rolling_require_consensus_across_merges(self):
        # AND, not OR (point 4 of the second round of review of PR #1237): a
        # merged (key, subject) only keeps a flag when EVERY contributing
        # call agreed on it. Two calls that both agree stay flagged; one
        # dissenting call clears it for the whole merged row, rather than
        # one hard_error=True call permanently painting every later, calmer
        # call for the same pair within the same run.
        self.finding("x", "first", subject="s", hard_error=True, time_rolling=True)
        self.finding("x", "second", subject="s", hard_error=True, time_rolling=True)
        row = self.ns["_FINDINGS"][0]
        self.assertTrue(row["hard_error"])
        self.assertTrue(row["time_rolling"])
        self.assertEqual(row["count"], 2)

        self.finding("y", "first", subject="s", hard_error=False, time_rolling=True)
        self.finding("y", "second", subject="s", hard_error=True, time_rolling=False)
        row_y = next(r for r in self.ns["_FINDINGS"] if r["key"] == "y")
        self.assertFalse(row_y["hard_error"])
        self.assertFalse(row_y["time_rolling"])
        self.assertEqual(row_y["count"], 2)

    def test_findings_json_round_trips_the_schema(self):
        self.finding("rule_enforcement", "98 active rule gaps", count=98)
        self.finding("export_unreadable", "export receipts UNREADABLE", hard_error=True)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "findings.json"
            self.write(str(path))
            payload = json.loads(path.read_text())
        self.assertIn("generated_at", payload)
        rows = {row["key"]: row for row in payload["findings"]}
        self.assertEqual(rows["rule_enforcement"]["count"], 98)
        self.assertFalse(rows["rule_enforcement"]["hard_error"])
        self.assertTrue(rows["export_unreadable"]["hard_error"])
        for row in payload["findings"]:
            for field in ("key", "subject", "detail", "count", "hard_error", "time_rolling"):
                self.assertIn(field, row)


class Rc1AlwaysFindsSomething(unittest.TestCase):
    """Static proof against the real source: no rc=1/return 1 path in
    _canonical_health (or _canonical_contradiction_alarm, which backs one of
    its branches) is reachable without having recorded a finding first —
    this is exactly PR #1237's point A ("export receipts UNREADABLE exits 1
    with no finding line")."""

    def test_every_rc1_path_has_a_preceding_finding_call(self):
        errors: list = []
        _check_function_top_level_isolated(_find_function("_canonical_health"), errors,
                                           "_canonical_health")
        _check_function_top_level_isolated(_find_function("_canonical_contradiction_alarm"), errors,
                                           "_canonical_contradiction_alarm")
        self.assertEqual(errors, [], "\n".join(errors))

    def test_every_structural_key_is_hard_error(self):
        fn = _find_function("_canonical_health")
        alarm = _find_function("_canonical_contradiction_alarm")
        hard_error_keys = (_finding_call_keys_with_kw(fn, "hard_error", True)
                           | _finding_call_keys_with_kw(alarm, "hard_error", True))
        missing = STRUCTURAL_KEYS - hard_error_keys
        self.assertEqual(missing, set(),
                         f"structural key(s) not recorded with hard_error=True: {missing}")

    def test_mutation_an_unrecorded_new_top_level_red_is_caught(self):
        # Non-vacuousness proof for the check above, using the SAME mutation
        # an independent reviewer used to prove the old single-pass check
        # was vacuous (scratchpad/mut/tools/health-check.py, point 3 of the
        # second round of review): a brand-new top-level section, appended
        # AFTER real sections that already call _canonical_finding, that
        # sets rc=1 with no finding call of its own. The old check passed
        # this because an EARLIER section's finding call had already set
        # `seen_finding = True` and it never reset across top-level
        # siblings; the isolated-per-top-level-statement check must catch
        # it, and must NOT flag the same mutation once it DOES call a
        # finding function.
        real = _find_function("_canonical_health")

        bad_src = (
            "if True:\n"
            "    if os.environ.get('NEW_UNRECORDED_RED'):\n"
            "        print('  \\u26a0\\ufe0e some new section UNREADABLE')\n"
            "        rc = 1\n"
        )
        bad_stmt = ast.parse(bad_src).body[0]
        mutated_bad = copy.deepcopy(real)
        mutated_bad.body.append(bad_stmt)
        ast.fix_missing_locations(mutated_bad)
        errors_bad: list = []
        _check_function_top_level_isolated(mutated_bad, errors_bad, "_canonical_health(mutated)")
        self.assertNotEqual(errors_bad, [],
                            "the isolated top-level check did not catch an unrecorded new red — "
                            "it is vacuous again")

        good_src = (
            "if True:\n"
            "    if os.environ.get('NEW_UNRECORDED_RED'):\n"
            "        _canonical_finding('new_unrecorded_red', 'some new section UNREADABLE', "
            "hard_error=True)\n"
            "        print('  \\u26a0\\ufe0e some new section UNREADABLE')\n"
            "        rc = 1\n"
        )
        good_stmt = ast.parse(good_src).body[0]
        mutated_good = copy.deepcopy(real)
        mutated_good.body.append(good_stmt)
        ast.fix_missing_locations(mutated_good)
        errors_good: list = []
        _check_function_top_level_isolated(mutated_good, errors_good, "_canonical_health(mutated)")
        self.assertEqual(errors_good, [],
                         "\n".join(errors_good) or
                         "a new top-level section that DOES call _canonical_finding before its "
                         "rc=1 was wrongly flagged")

    def test_business_count_keys_are_not_hard_error(self):
        # The other half of the same contract: a count that can legitimately
        # improve (98 -> 97 rule gaps) must never be hard_error, or the
        # release gate in ops/release-pipeline.py would fail every release
        # with any standing count, defeating the whole point of the gate.
        fn = _find_function("_canonical_health")
        all_keys = _all_finding_call_keys(fn) | _all_finding_call_keys(
            _find_function("_canonical_contradiction_alarm"))
        hard_error_keys = (_finding_call_keys_with_kw(fn, "hard_error", True)
                           | _finding_call_keys_with_kw(
                               _find_function("_canonical_contradiction_alarm"), "hard_error", True))
        business_keys = all_keys - STRUCTURAL_KEYS
        overlap = business_keys & hard_error_keys
        self.assertEqual(overlap, set(),
                         f"business-count key(s) wrongly marked hard_error=True: {overlap}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
