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
     and touches no database: `_canonical_finding`, `_red`, and `_write_
     findings_json`. This file extracts just those function definitions out
     of tools/health-check.py's AST and execs them into an isolated
     namespace (with `_FINDINGS = []`, `json`, `os`, `datetime`, `timezone`
     provided), then calls them directly. This is the actual shipped code,
     not a reimplementation, run hermetically.

  2. THE INVARIANT "every rc=1 code path records a finding, and every
     structural (whole-section-unreadable) rc=1 path records one with
     hard_error=True" is checked STATICALLY against the real source.

     Round 8 of an independent review of PR #1237 changed HOW this is
     proven. Through round 7, `_canonical_health` still contained bare
     `rc = 1` statements accompanied (in the same branch or loop) by a
     `_canonical_finding(...)` call, and this file's job was to prove, via a
     fairly elaborate per-branch/per-loop control-flow walk (`_check_block`
     and its helpers), that every one of those `rc = 1` statements really
     was reachable only after its accompanying finding call had run. Round 7
     ALSO added a runtime backstop (`_section_runtime_guard`) because real
     runs showed that reasoning had structural blind spots no static branch
     walk could close by itself: a section could satisfy "a finding was
     recorded somewhere in this section" using a finding an unrelated
     EARLIER statement already logged rather than the one that actually
     flipped `rc`, and — more fundamentally — a LATER section entered with
     `rc` already 1 from an earlier one (which, in a real run, is most
     sections after the first that reports anything) could never trigger the
     "did rc flip 0->1 during this section" precondition the runtime guard
     itself needed to fire.

     Round 8's fix removes the entire problem instead of chasing its
     symptoms further: `_canonical_health` now has a single, mandatory `_red
     (key, detail, ...)` helper that records the finding and returns 1 in
     one call, and EVERY `rc = 1` path in the function was rewritten to go
     through it — `rc = _red(...)`. That makes the invariant this file needs
     to prove trivial and purely mechanical: walk `_canonical_health`'s AST
     and assert `rc` is never assigned a bare literal `1` (or `|=`'d with
     one) directly, full stop. No branch/loop/for-else/break/sibling-name
     reasoning is needed any more, because there is no longer any bare
     `rc = 1` for that reasoning to be reasoning ABOUT — a code path that
     tried to flip `rc` red without a finding to explain it can no longer
     even be WRITTEN without also being a literal `rc = 1`, which the check
     below rejects outright. `_section_runtime_guard` and every one of its
     call sites are removed from tools/health-check.py along with this.

     The elaborate per-branch/loop machinery this file used to carry
     (`_check_block`, `_branch_always_finds`, `_if_always_finds`,
     `_propagates_finding_call`, `_loop_always_finds`, the for/else and
     break handling, the same-accumulator-name credit rule, and their
     dedicated mutation tests) existed ONLY to answer "does this rc=1 path
     have a preceding finding call" for `_canonical_health`'s old bare-
     `rc = 1` shape. It is removed as dead weight now that `_canonical_
     health` no longer has any such shape to reason about. The one other
     caller that machinery served, `_canonical_contradiction_alarm`
     (`_canonical_health` calls it and folds its return value into `rc` with
     `or`, rather than a bare `rc = 1`, precisely so this file's simple
     literal-only check does not need to special-case it), is a flat,
     loop-free if/elif/else chain where its own `return 1` sits on the very
     next line after the `_canonical_finding(...)` call that explains it —
     provable with a small, direct, non-recursive check
     (`_returns_one_have_preceding_finding_call`) instead of the general
     machinery, and is checked separately below.

     A business-count finding (e.g. rule_enforcement's "98 active rule
     gaps") is deliberately NOT hard_error=True — see the regression-diff
     test in ops/release-pipeline-selftest.py's HealthGate class, which
     pins that a count DECREASE must not fail a release. hard_error is
     reserved for a section that could not be read at all, or a finding
     (jev_call_receipt_integrity) that is a tamper-DETECTION result rather
     than an improvable business metric — see ALWAYS_HARD_ERROR_KEYS below.
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

# The two names a finding-recording call inside tools/health-check.py may
# appear under: the low-level `_canonical_finding` itself (still called
# directly by `_canonical_contradiction_alarm` and by `_canonical_health`'s
# own whole-run backstop, neither of which needs to set `rc`), and `_red`
# (the round-8 helper that records a finding AND returns 1 — the only way
# `_canonical_health` may set `rc` to anything other than its initial 0).
FINDING_CALL_NAMES = {"_canonical_finding", "_red"}

# Keys that must always carry hard_error=True regardless of any baseline
# comparison: either because they mean "this whole section of run.sh health
# could not be read" (a structural failure, not a business count that could
# legitimately improve), or — jev_call_receipt_integrity — because it is a
# tamper-DETECTION result on the Jev call receipt store (round 8, point 4:
# a mismatched receipt or a disabled append-only trigger is never something
# a baseline should be allowed to excuse). Both need hard_error=True
# somewhere and must be excluded from the business-count-never-hard_error
# check below.
STRUCTURAL_KEYS = {
    "canonical_health_refused", "source_unreadable", "export_unreadable",
    "job_ledger", "control_state", "repo_status", "registry_integrity",
    "credential_health", "unrecorded_failure",
}
ALWAYS_HARD_ERROR_KEYS = STRUCTURAL_KEYS | {"jev_call_receipt_integrity"}


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


def _load_finding_and_red_functions() -> dict:
    """Exec `_canonical_finding` and `_red` together (round 8's helper) —
    `_red` calls `_canonical_finding` by name, so both must land in the SAME
    namespace, hermetically, with no other top-level code in health-check.py
    running. Same pattern `_load_finding_functions` above uses for
    `_canonical_finding`/`_write_findings_json`: the actual shipped code,
    run directly, not a reimplementation."""
    mod = ast.Module(body=[_find_function("_canonical_finding"),
                            _find_function("_red")],
                     type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {"_FINDINGS": [], "json": json, "datetime": datetime, "timezone": timezone}
    import os as _os
    ns["os"] = _os
    exec(compile(mod, str(HEALTH_CHECK_PATH), "exec"), ns)  # noqa: S102 — trusted repo source
    return ns


def _finding_call_keys_with_kw(node: ast.AST, kw: str, value: bool) -> set[str]:
    """Every string literal key of a `_canonical_finding("key", ..., kw=value)`
    or `_red("key", ..., kw=value)` call anywhere in `node`, restricted to
    calls whose `kw` keyword argument is the literal `value`."""
    hits = set()
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in FINDING_CALL_NAMES):
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
                and n.func.id in FINDING_CALL_NAMES and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            keys.add(n.args[0].value)
    return keys


def _rc_assignment_violations(func: ast.AST) -> list[str]:
    """Every assignment to `rc` inside `func` that sets it via a bare literal
    1 (or `|=`s one in) — found anywhere in the function, with no block-
    scoping or control-flow reasoning needed (round 8 of an independent
    review of PR #1237 made this a purely mechanical, unconditional rule):
    `rc` may be initialized to 0, folded with a call's boolean result
    (`rc = _canonical_contradiction_alarm() or rc`), or set via `rc =
    _red(...)` (which itself always records the finding before returning 1)
    — but never handed the literal integer 1 directly. This check does not
    inspect the callee of an assigned Call — it only forbids the literal —
    so it is a deliberately narrow, mechanical net; the other tests in this
    file (and code review) still confirm any call used this way is itself
    trustworthy, the same as any other code review question."""
    violations = []
    where = getattr(func, "name", None) or "<mutated>"
    for node in ast.walk(func):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "rc"
                and isinstance(node.value, ast.Constant) and node.value.value == 1):
            violations.append(f"{where}:{node.lineno}: rc = 1 (bare literal — "
                              f"must go through rc = _red(...))")
        if (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "rc" and isinstance(node.op, ast.BitOr)
                and isinstance(node.value, ast.Constant) and node.value.value == 1):
            violations.append(f"{where}:{node.lineno}: rc |= 1 (bare literal — "
                              f"must go through rc = _red(...))")
    return violations


def _returns_one_have_preceding_finding_call(func: ast.FunctionDef) -> list[str]:
    """A small, direct (non-recursive-reasoning) check fit for a flat,
    loop-free function like `_canonical_contradiction_alarm`: within each
    straight-line statement list, a `return 1` must be immediately preceded,
    somewhere earlier in that SAME block, by a call to `_canonical_finding`
    or `_red`. Unlike the general per-branch machinery this file used to
    carry for `_canonical_health`'s old bare-`rc = 1` shape, this does not
    need to reason about loops, for/else, or sibling-branch coverage —
    `_canonical_contradiction_alarm` is a flat if/elif/else chain with no
    loops at all, so a straight-line same-block scan is already exact."""
    errors: list[str] = []

    def walk_block(body: list) -> None:
        seen = False
        for stmt in body:
            if (isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value == 1 and not seen):
                errors.append(f"{func.name}:{stmt.lineno}: return 1 with no preceding "
                              f"_canonical_finding(...)/_red(...) call in the same block")
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name)
                    and stmt.value.func.id in FINDING_CALL_NAMES):
                seen = True
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list) and nested:
                    walk_block(nested)
            for handler in getattr(stmt, "handlers", []) or []:
                walk_block(handler.body)

    walk_block(func.body)
    return errors


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

    def test_hard_error_ors_and_time_rolling_ands_across_merges(self):
        # Point 4 of the THIRD round of review of PR #1237, correcting the
        # second round's overcorrection: hard_error merges with OR (one
        # hard_error=True contributor makes the merged row hard_error, and
        # a later calmer call for the same pair must not launder that
        # away), while time_rolling still merges with AND (it only means
        # "every contributor agrees this pair is clock-driven"; one
        # non-rolling contributor is real news and must not be excused).
        self.finding("x", "first", subject="s", hard_error=True, time_rolling=True)
        self.finding("x", "second", subject="s", hard_error=True, time_rolling=True)
        row = self.ns["_FINDINGS"][0]
        self.assertTrue(row["hard_error"])
        self.assertTrue(row["time_rolling"])
        self.assertEqual(row["count"], 2)

        # A dissenting hard_error=False call does NOT clear an earlier
        # hard_error=True for the same pair.
        self.finding("y", "first", subject="s", hard_error=True, time_rolling=False)
        self.finding("y", "second", subject="s", hard_error=False, time_rolling=True)
        row_y = next(r for r in self.ns["_FINDINGS"] if r["key"] == "y")
        self.assertTrue(row_y["hard_error"])
        # But a dissenting time_rolling=False call DOES clear a merged
        # time_rolling — not every contributor agreed it was clock noise.
        self.assertFalse(row_y["time_rolling"])
        self.assertEqual(row_y["count"], 2)

    def test_merged_row_detail_reflects_the_latest_call_not_the_first(self):
        # Point 3 of round 4 of an independent review of PR #1237: a merged
        # row's `detail` must track the LATEST call's text, not stay frozen
        # at the first call's — "98 active rule gaps" must not linger once a
        # later merged call for the same (key, subject) reports 99, 100, ...
        self.finding("rule_enforcement", "98 active rule gaps", count=98)
        self.finding("rule_enforcement", "99 active rule gaps", count=1)
        row = self.ns["_FINDINGS"][0]
        self.assertEqual(row["count"], 99)
        self.assertEqual(row["detail"], "99 active rule gaps")

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


class RedHelper(unittest.TestCase):
    """The round-8 `_red()` helper, execed in isolation alongside the real
    `_canonical_finding` it delegates to (same hermetic pattern as
    `FindingFunctions` above) — proves it records the finding with the exact
    arguments given AND returns 1, in one call, which is what lets
    `_canonical_health` fold recording and the rc=1 assignment into a single
    `rc = _red(...)` statement."""

    def setUp(self):
        self.ns = _load_finding_and_red_functions()
        self.red = self.ns["_red"]

    def test_red_records_a_finding_and_returns_1(self):
        result = self.red("export_unreadable", "export receipts UNREADABLE", hard_error=True)
        self.assertEqual(result, 1)
        self.assertEqual(len(self.ns["_FINDINGS"]), 1)
        row = self.ns["_FINDINGS"][0]
        self.assertEqual(row["key"], "export_unreadable")
        self.assertEqual(row["detail"], "export receipts UNREADABLE")
        self.assertTrue(row["hard_error"])

    def test_red_passes_through_all_keyword_arguments(self):
        result = self.red("job_terminal_failure", "nightly-export failed",
                          subject="nightly-export", count=3, time_rolling=True)
        self.assertEqual(result, 1)
        row = self.ns["_FINDINGS"][0]
        self.assertEqual(row["subject"], "nightly-export")
        self.assertEqual(row["count"], 3)
        self.assertTrue(row["time_rolling"])
        self.assertFalse(row["hard_error"])

    def test_red_merges_into_an_existing_row_like_a_direct_finding_call_would(self):
        self.red("job_stuck", "first", subject="j")
        self.red("job_stuck", "second", subject="j")
        self.assertEqual(len(self.ns["_FINDINGS"]), 1)
        self.assertEqual(self.ns["_FINDINGS"][0]["count"], 2)


class RcAssignedOnlyViaRed(unittest.TestCase):
    """Round 8 of an independent review of PR #1237: `_canonical_health` may
    never assign `rc` a bare literal 1 (or `|=` one in) — the only way to set
    it besides its initial `rc = 0` is a call, in practice always `rc =
    _red(...)`, which records the finding before returning 1. This is the
    mechanical replacement for the old per-branch/per-loop "does a preceding
    finding call exist" reasoning (see the module docstring) — a bare
    `rc = 1` is simply forbidden now, so there is nothing left for that
    reasoning to reason about."""

    def test_no_bare_rc_literal_assignment_in_canonical_health(self):
        fn = _find_function("_canonical_health")
        violations = _rc_assignment_violations(fn)
        self.assertEqual(violations, [], "\n".join(violations))

    def test_mutation_a_bare_rc1_is_caught(self):
        mutated = copy.deepcopy(_find_function("_canonical_health"))
        mutated.body.append(ast.parse("if True:\n    rc = 1\n").body[0])
        ast.fix_missing_locations(mutated)
        violations = _rc_assignment_violations(mutated)
        self.assertNotEqual(violations, [],
                            "a bare rc = 1 planted into _canonical_health was not caught")

    def test_mutation_a_bare_rc_or_equals_1_is_caught(self):
        mutated = copy.deepcopy(_find_function("_canonical_health"))
        mutated.body.append(ast.parse("if True:\n    rc |= 1\n").body[0])
        ast.fix_missing_locations(mutated)
        violations = _rc_assignment_violations(mutated)
        self.assertNotEqual(violations, [],
                            "a bare rc |= 1 planted into _canonical_health was not caught")

    def test_rc_set_via_a_call_is_not_flagged(self):
        src = (
            "def f():\n"
            "    rc = 0\n"
            "    if True:\n"
            "        rc = _red('x', 'x')\n"
            "    rc = _canonical_contradiction_alarm() or rc\n"
        )
        fn = ast.parse(src).body[0]
        violations = _rc_assignment_violations(fn)
        self.assertEqual(violations, [], "\n".join(violations))

    def test_every_structural_or_tamper_detection_key_is_hard_error(self):
        fn = _find_function("_canonical_health")
        alarm = _find_function("_canonical_contradiction_alarm")
        hard_error_keys = (_finding_call_keys_with_kw(fn, "hard_error", True)
                           | _finding_call_keys_with_kw(alarm, "hard_error", True))
        missing = ALWAYS_HARD_ERROR_KEYS - hard_error_keys
        self.assertEqual(missing, set(),
                         f"key(s) not recorded with hard_error=True anywhere: {missing}")

    def test_business_count_keys_are_not_hard_error(self):
        # The other half of the same contract: a count that can legitimately
        # improve (98 -> 97 rule gaps) must never be hard_error, or the
        # release gate in ops/release-pipeline.py would fail every release
        # with any standing count, defeating the whole point of the gate.
        fn = _find_function("_canonical_health")
        alarm = _find_function("_canonical_contradiction_alarm")
        all_keys = _all_finding_call_keys(fn) | _all_finding_call_keys(alarm)
        hard_error_keys = (_finding_call_keys_with_kw(fn, "hard_error", True)
                           | _finding_call_keys_with_kw(alarm, "hard_error", True))
        business_keys = all_keys - ALWAYS_HARD_ERROR_KEYS
        overlap = business_keys & hard_error_keys
        self.assertEqual(overlap, set(),
                         f"business-count key(s) wrongly marked hard_error=True: {overlap}")


class ContradictionAlarmSelfRecords(unittest.TestCase):
    """`_canonical_contradiction_alarm` backs one of `_canonical_health`'s rc
    transitions (`rc = _canonical_contradiction_alarm() or rc`), so its own
    `return 1` must be preceded, in the same block, by a finding call — proven
    directly (see `_returns_one_have_preceding_finding_call`'s docstring for
    why the general per-branch machinery this file used to carry is
    unnecessary for this flat, loop-free function)."""

    def test_return_one_has_a_preceding_finding_call(self):
        fn = _find_function("_canonical_contradiction_alarm")
        errors = _returns_one_have_preceding_finding_call(fn)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_mutation_an_unrecorded_return_one_is_caught(self):
        src = (
            "def f():\n"
            "    if x:\n"
            "        return 1\n"
        )
        fn = ast.parse(src).body[0]
        errors = _returns_one_have_preceding_finding_call(fn)
        self.assertNotEqual(errors, [],
                            "an unrecorded return 1 was not caught by the direct same-block check")

    def test_mutation_a_recorded_return_one_is_not_flagged(self):
        src = (
            "def f():\n"
            "    if x:\n"
            "        _canonical_finding('a', 'a')\n"
            "        return 1\n"
        )
        fn = ast.parse(src).body[0]
        errors = _returns_one_have_preceding_finding_call(fn)
        self.assertEqual(errors, [], "\n".join(errors))


class CompletionMarkerAlwaysPrints(unittest.TestCase):
    """Point 3 of the third round of an independent review of PR #1237: an
    EARLY return out of `_canonical_health` (the `except Exception:` branch
    at its top, when `_canonical_snapshot()` itself raises) used to skip the
    completion-marker print entirely, even though it had already recorded a
    real, fully-explained `canonical_health_refused` hard_error finding. A
    read that catches and names its own failure is COMPLETE, not
    unavailable — without the marker, ops/release-pipeline.py's
    read_health_findings() would read it as an incomplete baseline and hold
    forever, never reaching the hard_error verdict it already has. This
    proves, statically, that every `return` out of `_canonical_health`
    OTHER than its final statement is preceded, in the same enclosing
    block, by a `print(_HEALTH_COMPLETION_MARKER)` call."""

    @staticmethod
    def _is_marker_print(stmt: ast.stmt) -> bool:
        return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "print"
                and len(stmt.value.args) == 1 and isinstance(stmt.value.args[0], ast.Name)
                and stmt.value.args[0].id == "_HEALTH_COMPLETION_MARKER")

    def _check_marker_before_early_returns(self, body: list, errors: list, where: str,
                                           seen_marker: bool, is_final_block: bool) -> None:
        n = len(body)
        for i, stmt in enumerate(body):
            is_last_top_level = is_final_block and i == n - 1
            if isinstance(stmt, ast.Return) and not is_last_top_level:
                if not seen_marker:
                    errors.append(f"{where}:{stmt.lineno}: early return with no preceding "
                                  f"print(_HEALTH_COMPLETION_MARKER) call in the same or an "
                                  f"enclosing block")
            if self._is_marker_print(stmt):
                seen_marker = True
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list) and nested and all(isinstance(x, ast.stmt) for x in nested):
                    self._check_marker_before_early_returns(nested, errors, where, seen_marker, False)
            for handler in getattr(stmt, "handlers", []) or []:
                self._check_marker_before_early_returns(handler.body, errors, where, seen_marker, False)

    def test_every_early_return_prints_the_completion_marker_first(self):
        fn = _find_function("_canonical_health")
        errors: list = []
        self._check_marker_before_early_returns(fn.body, errors, "_canonical_health",
                                                seen_marker=False, is_final_block=True)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_mutation_an_early_return_with_no_marker_print_is_caught(self):
        bad_src = (
            "def f():\n"
            "    try:\n"
            "        snap = risky()\n"
            "    except Exception:\n"
            "        _canonical_finding('x', 'x', hard_error=True)\n"
            "        return 1\n"
            "    print(_HEALTH_COMPLETION_MARKER)\n"
            "    return 0\n"
        )
        fn_bad = ast.parse(bad_src).body[0]
        errors_bad: list = []
        self._check_marker_before_early_returns(fn_bad.body, errors_bad, "f",
                                                seen_marker=False, is_final_block=True)
        self.assertNotEqual(errors_bad, [])

    def test_completion_marker_prints_exactly_once_on_the_normal_path(self):
        # Round 8, point 5: a merge with main's Jev-receipts block once left
        # the SAME literal marker text printed twice in a row at the bottom
        # of the normal (non-early-return) path — once via `print(_HEALTH_
        # COMPLETION_MARKER)`, once more as a duplicate hard-coded string
        # right after it. This proves the function's own top-level body
        # contains exactly one statement that prints the marker (by name or
        # by the identical literal text), not two.
        fn = _find_function("_canonical_health")
        marker_text = (
            "Projection freshness/tamper checks are recovery evidence; "
            "use --recovery --reason <why>."
        )

        def is_marker_print_by_name_or_text(stmt: ast.stmt) -> bool:
            if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == "print"
                    and len(stmt.value.args) == 1):
                return False
            arg = stmt.value.args[0]
            if isinstance(arg, ast.Name) and arg.id == "_HEALTH_COMPLETION_MARKER":
                return True
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value == marker_text:
                return True
            return False

        top_level_marker_prints = [s for s in fn.body if is_marker_print_by_name_or_text(s)]
        self.assertEqual(len(top_level_marker_prints), 1,
                         "the normal (non-early-return) path at the bottom of _canonical_health "
                         "must print the completion marker exactly once")


if __name__ == "__main__":
    unittest.main(verbosity=2)
