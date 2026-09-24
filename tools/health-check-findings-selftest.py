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
    "credential_health", "unrecorded_failure",
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


def _loop_always_finds(stmt: ast.stmt) -> bool:
    """Whether a `for`/`while` loop is GUARANTEED to make a finding call
    every time this statement is reached, regardless of how many times (if
    any) its body actually runs — used only by `_branch_always_finds`,
    which decides whether an entire branch may be trusted to cover a LATER,
    UNRELATED sibling statement outside that branch (point 1 of round 4 of
    an independent review of PR #1237).

    A bare loop body is NOT sufficient, even when it contains a finding
    call, because the loop's iterable can be empty at runtime — the body
    then never runs and nothing is ever recorded. Only a `for`/`else` (or
    `while`/`else`) clause qualifies: Python runs that `else` whenever the
    loop finishes without hitting a `break`, which includes finishing after
    zero iterations, so a finding call placed there is unconditional --
    UNLESS the loop body can itself `break`, which skips the `else` clause
    entirely (see `_loop_body_has_own_break`): a `break` anywhere in the
    body disqualifies the loop, since Python no longer guarantees the
    `else` runs.

    This is deliberately STRICTER than `_propagates_finding_call`'s own
    For/While case, which stays permissive for the different, legitimate
    job of covering a sibling `if` in the SAME block that shares the loop's
    own accumulator (e.g. `for job in bad: _canonical_finding(...)` then
    `if bad: rc = 1` right after it — if the loop ran zero times, `bad` is
    empty and that `rc = 1` can't fire either, so the loop and its sibling
    rise and fall together). That in-block relationship does not exist for
    a branch being judged fit to excuse code OUTSIDE itself, which is what
    `_branch_always_finds` is for — so here the loop must prove it always
    fires on its own, via for/else."""
    if not isinstance(stmt, (ast.For, ast.While, ast.AsyncFor)):
        return False
    if not stmt.orelse or not any(_contains_finding_call(s) for s in stmt.orelse):
        return False
    return not _loop_body_has_own_break(stmt.body)


def _loop_body_has_own_break(body: list) -> bool:
    """Whether an `ast.Break` appears anywhere in `body` that belongs to
    THIS loop rather than to a loop nested inside it — a `break` inside a
    nested `for`/`while`/`async for` targets that inner loop, not the outer
    one being checked, so descent stops at the boundary of any nested loop
    (its own body/orelse are skipped for this purpose; `break` cannot
    legally appear in an `orelse` at all, so there is nothing to miss by
    skipping it). Descends into `if`/`try`/`with` bodies, since a `break`
    inside one of those still belongs to the enclosing loop."""
    for stmt in body:
        if isinstance(stmt, ast.Break):
            return True
        if isinstance(stmt, (ast.For, ast.While, ast.AsyncFor)):
            continue
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(stmt, field, None)
            if isinstance(nested, list) and nested and _loop_body_has_own_break(nested):
                return True
        for handler in getattr(stmt, "handlers", []) or []:
            if _loop_body_has_own_break(handler.body):
                return True
    return False


def _branch_always_finds(stmts: list) -> bool:
    """Whether this straight-line statement list is GUARANTEED to make a
    finding call every time it runs — used to decide whether an `if`
    propagates to a later, UNRELATED sibling statement (see
    `_if_always_finds`). A bare `for`/`while` loop is deliberately NOT
    trusted here (see `_loop_always_finds`) even though the more permissive
    `_propagates_finding_call` trusts one for the different, narrower job of
    covering its own immediate in-block sibling."""
    for s in stmts:
        if isinstance(s, (ast.For, ast.While, ast.AsyncFor)):
            if _loop_always_finds(s):
                return True
            continue
        if _propagates_finding_call(s):
            return True
        if isinstance(s, ast.If) and _if_always_finds(s):
            return True
    return False


def _if_always_finds(node: ast.If) -> bool:
    """Whether EVERY path through this `if`/`elif`/`else` chain makes a
    finding call — i.e. it is safe to treat as "found something" for a
    LATER sibling statement, same as a for-loop is. Two ways an `if` earns
    this:

    1. Its own `test` expression itself contains a finding/delegate call
       (e.g. `if _canonical_contradiction_alarm(): rc = 1`) — the test is
       evaluated every time this statement is reached, whichever way it
       comes out, so a call inside it always happens.
    2. It has a real `else` (or an `elif` chain that ends in one — a bare
       `if` with no `else` can never qualify, since the false path
       guarantees nothing), and EVERY leaf branch (recursively, through any
       `elif`) independently guarantees a finding call.

    A lone `if X: _canonical_finding(...)` with no `else` does NOT
    propagate: reaching it with X false calls nothing. This is why a
    'four independent un-elsed `if`s covering one OR'd guard' pattern
    still needs a code-level for-loop rewrite, not a smarter checker —
    proving that kind of disjunctive coverage statically is out of scope
    here, and out of scope for real code review too."""
    test_hit = _contains_finding_call(node.test)
    if test_hit:
        return True
    if not node.orelse:
        return False
    return _branch_always_finds(node.body) and _branch_always_finds(node.orelse)


def _propagates_finding_call(stmt: ast.stmt) -> bool:
    """Whether a finding call found in/around `stmt` should count toward a
    LATER SIBLING statement at the SAME block level — point 3 of the third
    round of an independent review of PR #1237: the check must be per
    BRANCH, not per section.

    A `for`/`while`/`with` body runs unconditionally once its own header is
    reached, so a finding call anywhere inside one (even nested one level
    deeper, e.g. `for x in bad: if cond(x): _canonical_finding(...)`) is
    propagated — this is the documented, legitimate "a for-loop reports one
    finding per bad item, followed by a sibling `if bad: rc = 1`" pattern,
    where the loop's own condition and the sibling `if`'s condition are
    drawn from the same accumulator.

    An `if` propagates ONLY when `_if_always_finds` proves every path
    through it makes a finding call (see that function) — a lone `if` whose
    single branch happens to call a finding does NOT propagate, because
    that branch's own condition is no guarantee about a later, unrelated
    statement's condition. This is exactly the bug a planted `rc = 1` after
    `_canonical_workflow_truth()` exposed: an EARLIER, unrelated `if`
    branch's finding call had satisfied the old check for the rest of the
    whole top-level section, when in reality that branch might not have
    executed at all.

    A bare `try` (no matching structural guarantee) is likewise NOT
    propagated — its body may not finish if an exception fires partway
    through, so nothing inside it is guaranteed either."""
    if isinstance(stmt, (ast.For, ast.While, ast.With, ast.AsyncFor, ast.AsyncWith)):
        return _contains_finding_call(stmt)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _contains_finding_call(stmt)
    if isinstance(stmt, ast.If):
        return _if_always_finds(stmt)
    return False


def _loop_accumulator_names(stmt: ast.stmt) -> set:
    """The `Name` ids referenced in a `for`/`while` statement's own iterated
    expression or test (e.g. `for job in bad:` -> {'bad'}, `while queue:` ->
    {'queue'}, `for _c, _k in (gate_failures, ...):` -> the names embedded
    in that tuple literal, including ones buried in an f-string). Used only
    to match a LATER sibling `if`'s condition against the SAME accumulator
    (see `_check_block`'s point-4-of-round-5 same-name credit rule)."""
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        src: ast.expr = stmt.iter
    elif isinstance(stmt, ast.While):
        src = stmt.test
    else:
        return set()
    return {n.id for n in ast.walk(src) if isinstance(n, ast.Name)}


def _test_names(test: ast.expr) -> set:
    return {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}


def _check_block(body: list, errors: list, where: str, seen_finding: bool = False) -> bool:
    """Every `rc = 1` / `return 1` reachable from THIS statement list must be
    preceded by at least one `_canonical_finding(...)` call reachable from an
    earlier point on the SAME control-flow path — including a finding call
    made by an earlier SIBLING `for`/`while`/`with` statement's own nested
    block (see `_propagates_finding_call`), but NEVER a finding call made
    inside an earlier sibling `if`/`try` branch, since that branch's own
    condition is no guarantee about a later, different statement's
    condition (point 3 of the third round of review — "per branch, not per
    section"). `seen_finding` is the state carried in from the enclosing
    block, and recursion into a nested body passes the CURRENT running
    state rather than resetting it, so an inherited for/while case is not a
    false positive; an inherited if/try case never contributes at all (see
    `_propagates_finding_call`). Returns the updated state (not used by
    callers today, but keeps the function honest about what it tracks).

    A `for`/`while` loop containing a finding call does NOT set `seen_finding`
    for the REST of the block the way an `if`/`try` propagation does (round 5
    of an independent review of PR #1237, point 4/point-4-caveat: a bare
    `rc = 1` mutation planted after several unrelated for-loops in the same
    block used to be silently excused by any one of them). Instead a loop's
    credit is scoped to exactly two shapes, both seen in the real source:
      1. the loop's OWN DIRECT NEXT sibling statement (no other statement in
         between), when that next statement is itself an rc=1/return 1 with
         no guarding `if` — e.g. `for _c, _k in (...): _canonical_finding(...)`
         immediately followed by a bare `rc = 1` (doctrine/credential-health
         sections);
      2. a LATER `if` sibling (anywhere in the block, not just the next
         statement) whose test expression references the SAME name(s) the
         loop iterated over or tested — e.g. `for job in bad: _canonical_
         finding(...)` ... `if bad: rc = 1` (exports section), or several
         such loops feeding one `if a or b or c: rc = 1` (jobs section)."""
    loop_names: set = set()
    prev_loop_credit = False
    for stmt in body:
        name_credit = (isinstance(stmt, ast.If) and bool(loop_names & _test_names(stmt.test)))
        credited = seen_finding or prev_loop_credit or name_credit
        if _is_rc_one_assign(stmt) or _is_return_one(stmt):
            if not credited:
                errors.append(f"{where}:{stmt.lineno}: rc=1/return 1 with no preceding "
                              f"_canonical_finding(...) call in the same or an enclosing block")
        this_loop_credit = False
        if isinstance(stmt, (ast.For, ast.While, ast.AsyncFor)):
            if _contains_finding_call(stmt):
                loop_names |= _loop_accumulator_names(stmt)
                this_loop_credit = True
        elif _propagates_finding_call(stmt):
            seen_finding = True
        # Recurse into this statement's own nested blocks so a violation
        # buried inside an `if`/`for`/`try` is still caught. Unlike the
        # `credited` value used for THIS statement's own rc=1/return-1 check
        # above (computed from state as it stood BEFORE this statement),
        # recursion into stmt's OWN body/orelse/handlers uses the state as
        # it stands AFTER this statement updated it — e.g. `if _canonical_
        # contradiction_alarm(): rc = 1`'s own test contains the delegate
        # call, so `_propagates_finding_call` above already set `seen_
        # finding = True` for this exact statement, and its `rc = 1` is
        # reached only once that call has run, so its body must see the
        # updated state, not the pre-statement one. Likewise a for-loop
        # that itself contains a finding call credits ITS OWN nested body.
        # This recursion is independent of whether `stmt` propagates to a
        # LATER, OUTER-block sibling — that is governed by `seen_finding`/
        # `prev_loop_credit`/`loop_names` at the outer level only.
        recurse_credited = seen_finding or this_loop_credit or name_credit
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(stmt, field, None)
            if isinstance(nested, list) and nested and all(isinstance(x, ast.stmt) for x in nested):
                _check_block(nested, errors, where, recurse_credited)
        for handler in getattr(stmt, "handlers", []) or []:
            _check_block(handler.body, errors, where, recurse_credited)
        prev_loop_credit = this_loop_credit
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

    def test_mutation_an_unrelated_if_branch_does_not_cover_a_later_sibling(self):
        # Point 3 of the THIRD round of review: the check must be per
        # BRANCH, not per section. The round-2 mutation test proved
        # cross-TOP-LEVEL-STATEMENT leaks were fixed; this proves the same
        # kind of leak WITHIN one top-level statement, across two sibling
        # `if`s that test unrelated conditions, is also caught — this is
        # exactly the shape of "a planted rc=1 inside the jobs section
        # after _canonical_workflow_truth() still passes" from the
        # reviewer's mut3 probe: an earlier, unrelated conditional finding
        # call must not excuse a later, different conditional's rc=1.
        src_bad = (
            "def f():\n"
            "    if section:\n"
            "        if some_earlier_condition():\n"
            "            _canonical_finding('a', 'a')\n"
            "        _canonical_workflow_truth()\n"
            "        if some_unrelated_condition():\n"
            "            rc = 1\n"
        )
        fn_bad = ast.parse(src_bad).body[0]
        errors_bad: list = []
        _check_function_top_level_isolated(fn_bad, errors_bad, "f")
        self.assertNotEqual(errors_bad, [],
                            "an earlier, unrelated if branch's finding call must not cover a "
                            "later sibling if's rc=1")

        # The two legitimate propagating shapes must still pass: a for-loop
        # feeding a sibling if (the original documented pattern), and an
        # if/else where EVERY leaf branch calls a finding (so the branch
        # is provably unavoidable, not merely possible).
        # Both legitimate patterns are tested nested inside one common
        # enclosing statement — same as the real code (both live inside one
        # `if CANONICAL_SECTION in (...):` block) — since round 2's
        # per-top-level isolation deliberately does NOT thread seen_finding
        # between statements that are themselves top-level siblings of the
        # function; only nesting inside a shared enclosing block does.
        src_good_for = (
            "def f():\n"
            "    if section:\n"
            "        for item in bad:\n"
            "            _canonical_finding('a', 'a')\n"
            "        if bad:\n"
            "            rc = 1\n"
        )
        errors_good_for: list = []
        _check_function_top_level_isolated(ast.parse(src_good_for).body[0], errors_good_for, "f")
        self.assertEqual(errors_good_for, [])

        src_good_ifelse = (
            "def f():\n"
            "    if section:\n"
            "        if cond:\n"
            "            _canonical_finding('a', 'a')\n"
            "        else:\n"
            "            _canonical_finding('b', 'b')\n"
            "        rc = 1\n"
        )
        errors_good_ifelse: list = []
        _check_function_top_level_isolated(ast.parse(src_good_ifelse).body[0], errors_good_ifelse, "f")
        self.assertEqual(errors_good_ifelse, [])

    def test_mutation_rc1_planted_after_canonical_workflow_truth_is_caught(self):
        # Point 1 of round 4 of an independent review of PR #1237, planting
        # the reviewer's EXACT probe against the REAL, unmutated source: an
        # `rc = 1` inserted immediately after the real `_canonical_workflow_
        # truth()` call in the jobs section of `_canonical_health`, with no
        # finding call of its own. Before the round-4 fix (tightening
        # `_branch_always_finds`/`_loop_always_finds` so a bare `for`/`while`
        # loop is not trusted to cover an UNRELATED later sibling outside its
        # own branch), this slipped past the checker: the jobs section's
        # `if not isinstance(...): ... else: <bare for-loops>...` compound
        # statement was wrongly judged to "always find something" purely
        # because ITS orelse branch happened to contain a for-loop with a
        # finding call buried somewhere inside it — even though that loop's
        # iterable (`bad`, `unreceipted`, ...) could be empty at runtime, so
        # the branch is not actually guaranteed to record anything. That
        # false "always finds" status then wrongly propagated past the whole
        # if/else to excuse this later, wholly unrelated `rc = 1`.
        real = _find_function("_canonical_health")
        mutated = copy.deepcopy(real)

        target = None
        for node in ast.walk(mutated):
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id == "_canonical_workflow_truth"):
                target = node
                break
        self.assertIsNotNone(target, "_canonical_workflow_truth() call not found in "
                              "_canonical_health — has the jobs section moved?")

        planted = ast.parse("rc = 1\n").body[0]
        ast.fix_missing_locations(planted)

        planted_into = None

        def _plant(body):
            nonlocal planted_into
            if planted_into is not None:
                return
            if target in body:
                body.insert(body.index(target) + 1, planted)
                planted_into = body

        for node in ast.walk(mutated):
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(node, field, None)
                if isinstance(nested, list) and all(isinstance(x, ast.stmt) for x in nested):
                    _plant(nested)
        self.assertIsNotNone(planted_into, "could not locate the enclosing block of the "
                              "_canonical_workflow_truth() call to plant the mutation into")
        ast.fix_missing_locations(mutated)

        errors: list = []
        _check_function_top_level_isolated(mutated, errors, "_canonical_health(mutated)")
        self.assertNotEqual(errors, [],
                            "a bare for-loop inside an unrelated earlier branch wrongly excused "
                            "an rc=1 planted right after _canonical_workflow_truth() — the "
                            "for/while-loop-propagation check is vacuous again")

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


class ForElseBreakHandling(unittest.TestCase):
    """Round 5 of an independent review of PR #1237: the mypy error at the
    old `_loop_always_finds` (passing `stmt.orelse`, a list, straight into
    `_contains_finding_call`, which expects a single `ast.AST` and calls
    `ast.walk` on it — also a latent runtime bug, `ast.walk` on a list
    raises) is fixed by walking each statement of `orelse` separately. This
    class also proves the accompanying correctness fix: a `break` in the
    loop's own body skips its `else` clause entirely, so such a loop must
    NOT be trusted to guarantee the else's finding call runs."""

    def test_for_else_without_break_is_trusted(self):
        stmt = ast.parse(
            "for x in items:\n"
            "    pass\n"
            "else:\n"
            "    _canonical_finding('a', 'a')\n"
        ).body[0]
        self.assertTrue(_loop_always_finds(stmt))

    def test_for_else_with_break_is_not_trusted(self):
        stmt = ast.parse(
            "for x in items:\n"
            "    if x.bad:\n"
            "        break\n"
            "else:\n"
            "    _canonical_finding('a', 'a')\n"
        ).body[0]
        self.assertFalse(_loop_always_finds(stmt))

    def test_break_in_a_nested_loop_does_not_disqualify_the_outer_loop(self):
        # A `break` inside a NESTED for/while belongs to that inner loop,
        # not the outer one being judged -- it must not disqualify the
        # outer for/else.
        stmt = ast.parse(
            "for x in items:\n"
            "    for y in x.sub:\n"
            "        if y.bad:\n"
            "            break\n"
            "else:\n"
            "    _canonical_finding('a', 'a')\n"
        ).body[0]
        self.assertTrue(_loop_always_finds(stmt))

    def test_end_to_end_for_else_without_break_excuses_a_later_sibling(self):
        # Mirrors the real shape `_branch_always_finds`/`_if_always_finds`
        # need this for: an if/else where one branch is a for/else that
        # always finds (no break) and the other branch always finds
        # unconditionally -- the whole if/else then legitimately excuses a
        # LATER, unrelated sibling's rc=1.
        src = (
            "def f():\n"
            "    if section:\n"
            "        if cond:\n"
            "            for x in items:\n"
            "                pass\n"
            "            else:\n"
            "                _canonical_finding('a', 'a')\n"
            "        else:\n"
            "            _canonical_finding('b', 'b')\n"
            "        rc = 1\n"
        )
        errors: list = []
        _check_function_top_level_isolated(ast.parse(src).body[0], errors, "f")
        self.assertEqual(errors, [], "\n".join(errors))

    def test_end_to_end_for_else_with_break_does_not_excuse_a_later_sibling(self):
        # Same shape, but the for-loop's body can break -- the else is no
        # longer guaranteed, so this if/else must NOT be trusted, and the
        # checker must flag the later rc=1 as a violation.
        src = (
            "def f():\n"
            "    if section:\n"
            "        if cond:\n"
            "            for x in items:\n"
            "                if x.bad:\n"
            "                    break\n"
            "            else:\n"
            "                _canonical_finding('a', 'a')\n"
            "        else:\n"
            "            _canonical_finding('b', 'b')\n"
            "        rc = 1\n"
        )
        errors: list = []
        _check_function_top_level_isolated(ast.parse(src).body[0], errors, "f")
        self.assertNotEqual(errors, [],
                            "a for/else whose loop body can break was wrongly trusted to excuse "
                            "a later, unrelated sibling's rc=1 -- the break-detection fix is not "
                            "working")


class LoopSiblingCreditIsScoped(unittest.TestCase):
    """Round 5, point 4 of an independent review of PR #1237: a bare `rc = 1`
    planted in the SAME block as several earlier, unrelated for-loops (each
    of which contains a finding call) used to be silently excused by any one
    of them, no matter how unrelated -- `seen_finding` never reset across
    same-block siblings once any for-loop had set it. `_check_block` now
    scopes a loop's credit to (1) its own direct next sibling statement when
    that is a bare rc=1/return 1, or (2) a later `if` sibling whose test
    references the SAME name the loop iterated over -- this proves both the
    mutation is now caught and the real legitimate shapes still pass."""

    def test_mutation_unrelated_rc1_after_several_for_loops_is_caught(self):
        # The reviewer's probe, reproduced structurally: the real jobs
        # section's shape (several for-loops over unrelated accumulators,
        # an unrelated `if legacy: print(...)`, THEN the real closing `if
        # bad or unreceipted or missing or stuck: rc = 1`) with an EXTRA
        # bare `rc = 1` mutation planted right after the unrelated `if
        # legacy:` line and before the real closing `if` -- not immediately
        # adjacent to any for-loop, and matching no loop's accumulator name.
        src = (
            "def f():\n"
            "    if section:\n"
            "        for job in bad:\n"
            "            _canonical_finding('job_terminal_failure', 'x', subject='j')\n"
            "        for job in unreceipted:\n"
            "            _canonical_finding('job_completion_receipt', 'x', subject='j')\n"
            "        if legacy:\n"
            "            print('carried')\n"
            "        rc = 1\n"  # the planted mutation -- not covered by anything above
            "        if bad or unreceipted:\n"
            "            rc = 1\n"
        )
        errors: list = []
        _check_function_top_level_isolated(ast.parse(src).body[0], errors, "f")
        self.assertNotEqual(errors, [],
                            "an unrelated bare rc=1 planted after several unrelated for-loops "
                            "was wrongly excused -- the same-block loop-credit fix is not working")

    def test_real_shape_direct_bare_rc1_after_a_single_loop_is_trusted(self):
        # Doctrine/credential-health real shape: a for-loop whose iterated
        # expression is a literal tuple (not a bare Name), immediately
        # followed, with nothing in between, by a bare `rc = 1` -- trusted
        # via direct adjacency, not name-matching.
        src = (
            "def f():\n"
            "    if section:\n"
            "        for _count, _key in ((gate_failures, 'a'), (stale, 'b')):\n"
            "            if _count:\n"
            "                _canonical_finding(_key, 'x', count=_count)\n"
            "        rc = 1\n"
        )
        errors: list = []
        _check_function_top_level_isolated(ast.parse(src).body[0], errors, "f")
        self.assertEqual(errors, [], "\n".join(errors))

    def test_real_shape_same_name_if_after_a_gap_is_trusted(self):
        # Exports real shape: `for target in bad: _canonical_finding(...)`,
        # an unrelated assignment in between, THEN `if bad: rc = 1` -- not
        # directly adjacent, but the `if`'s test references the same name
        # the loop iterated over, so it is trusted.
        src = (
            "def f():\n"
            "    if section:\n"
            "        for target in bad:\n"
            "            _canonical_finding('export_receipt', 'x', subject=target)\n"
            "        _carried = 'note'\n"
            "        if bad:\n"
            "            rc = 1\n"
        )
        errors: list = []
        _check_function_top_level_isolated(ast.parse(src).body[0], errors, "f")
        self.assertEqual(errors, [], "\n".join(errors))

    def test_real_shape_multiple_loops_feeding_one_ord_if_is_trusted(self):
        # Jobs section's real closing shape: several for-loops over
        # different accumulators, an unrelated `if legacy: print(...)` in
        # between, then one `if bad or unreceipted or missing or stuck: rc
        # = 1` whose test references every accumulator -- trusted.
        src = (
            "def f():\n"
            "    if section:\n"
            "        for job in bad:\n"
            "            _canonical_finding('job_terminal_failure', 'x', subject='j')\n"
            "        for job in unreceipted:\n"
            "            _canonical_finding('job_completion_receipt', 'x', subject='j')\n"
            "        if legacy:\n"
            "            print('carried')\n"
            "        if bad or unreceipted:\n"
            "            rc = 1\n"
        )
        errors: list = []
        _check_function_top_level_isolated(ast.parse(src).body[0], errors, "f")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
