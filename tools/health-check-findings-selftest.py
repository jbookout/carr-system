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


def _allowed_rc_assignment_value(value: ast.AST) -> bool:
    """True iff `value` is the RHS of one of the exactly three forms an
    assignment to `rc` inside `_canonical_health` is allowed to take (round 9
    of an independent review of PR #1237, converting the round-8 DENYLIST —
    which only forbade a literal `1` and so let anything else through
    undetected, e.g. `rc = p.returncode` — into an ALLOWLIST that names every
    legitimate shape and rejects everything else):

      1. `rc = 0`                                   — the initial assignment
      2. `rc = _red(...)`                            — any arguments
      3. `rc = _canonical_contradiction_alarm() or rc` — the one self-
         referential pattern `_canonical_health` actually uses, folding the
         contradiction alarm's own self-recording return value into `rc`
         without re-recording its finding a second time.
    """
    if isinstance(value, ast.Constant) and value.value == 0:
        return True
    if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
            and value.func.id == "_red"):
        return True
    if (isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or)
            and len(value.values) == 2
            and isinstance(value.values[0], ast.Call)
            and isinstance(value.values[0].func, ast.Name)
            and value.values[0].func.id == "_canonical_contradiction_alarm"
            and isinstance(value.values[1], ast.Name) and value.values[1].id == "rc"):
        return True
    return False


def _rc_assignment_violations(func: ast.AST) -> list[str]:
    """Every assignment to (or binding of) `rc` inside `func`, checked
    against an ALLOWLIST rather than a denylist (round 9 of an independent
    review of PR #1237). The round-8 check here only forbade a bare literal
    `1` (or `|= 1`) — a denylist by construction, which meant any OTHER
    unrecorded write to `rc` (`rc = p.returncode`, `rc = max(rc, 1)`,
    `rc = 1 if cond else rc`, `rc = rc or 1`, `rc = int(True)`, a tuple
    target `rc, _z = 1, 0`, or a walrus `(rc := 1)`) got through undetected,
    both here (the static check never looked at it) and at runtime (none of
    those paths call `_red()`, so none would record a finding). This walks
    the whole function and flags any binding of the name `rc` that is not
    exactly one of the three forms `_allowed_rc_assignment_value` names:
    a single-target `Assign` whose value passes that check. Everything else
    — `AugAssign` (`rc += 1`, `rc |= 1`, any operator), a `NamedExpr` walrus
    binding `rc`, a multi/tuple assignment target that includes `rc`, or a
    single-target `Assign` whose value is any other shape — is rejected."""
    violations = []
    where = getattr(func, "name", None) or "<mutated>"
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            single_rc_target = (len(node.targets) == 1
                                and isinstance(node.targets[0], ast.Name)
                                and node.targets[0].id == "rc")
            touches_rc = single_rc_target or any(
                isinstance(t, (ast.Tuple, ast.List))
                and any(isinstance(elt, ast.Name) and elt.id == "rc" for elt in t.elts)
                for t in node.targets
            )
            if not touches_rc:
                continue
            if not single_rc_target:
                violations.append(f"{where}:{node.lineno}: rc assigned via a tuple/multiple "
                                  f"assignment target — not one of the three allowed forms")
            elif not _allowed_rc_assignment_value(node.value):
                violations.append(f"{where}:{node.lineno}: rc assignment does not match one "
                                  f"of the three allowed forms (rc = 0 / rc = _red(...) / "
                                  f"rc = _canonical_contradiction_alarm() or rc)")
        elif (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "rc"):
            violations.append(f"{where}:{node.lineno}: augmented assignment to rc "
                              f"({type(node.op).__name__}) is not one of the three allowed forms")
        elif (isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name)
                and node.target.id == "rc"):
            violations.append(f"{where}:{node.lineno}: walrus assignment to rc "
                              f"is not one of the three allowed forms")
        # Round 10 of an independent review of PR #1237: the round-9 allowlist
        # only inspected plain Assign/AugAssign/NamedExpr — every OTHER Python
        # construct that can bind a name (an annotated assignment, a `for`
        # loop target, a `with ... as` context-manager target, a `match`
        # case capture, an `except ... as` handler name, or an `import ... as`
        # alias) binds `rc` completely unseen by that check, and none of them
        # go through `_red()`, so a bug hiding behind one of these shapes
        # would set `rc` red and record nothing. Every one of these forms is
        # rejected outright if it binds the name `rc` — none of the three
        # allowed forms can be expressed through them.
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "rc":
            violations.append(f"{where}:{node.lineno}: annotated assignment to rc "
                              f"(`rc: ... = ...`) is not one of the three allowed forms")
        elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name) and node.target.id == "rc":
            violations.append(f"{where}:{node.lineno}: `for rc in ...:` binds rc as a loop "
                              f"target — not one of the three allowed forms")
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == "rc":
                    violations.append(f"{where}:{node.lineno}: `with ... as rc:` binds rc via "
                                      f"a context manager — not one of the three allowed forms")
        elif isinstance(node, ast.ExceptHandler) and node.name == "rc":
            violations.append(f"{where}:{node.lineno}: `except ... as rc:` binds rc to the "
                              f"caught exception — not one of the three allowed forms")
        elif isinstance(node, ast.MatchAs) and node.name == "rc":
            violations.append(f"{where}:{node.lineno}: a `match`/`case` pattern captures rc "
                              f"(`case rc:` or `case ... as rc:`) — not one of the three "
                              f"allowed forms")
        elif isinstance(node, ast.alias) and node.asname == "rc":
            violations.append(f"{where}: `import ... as rc` binds rc via an import alias — "
                              f"not one of the three allowed forms")
    return violations


def _shadow_violations(module: ast.Module | None = None) -> list[str]:
    """Round 10, item 3: banning every unrecorded way to WRITE `rc` is
    pointless if `_red` or `_canonical_contradiction_alarm` themselves can be
    silently redefined to no-ops elsewhere in the module — a `_red = lambda
    *a, **k: 1` (or any other rebinding of either name) would make every
    `rc = _red(...)` in `_canonical_health` pass this file's allowlist while
    recording nothing at runtime. The only binding of either name permitted
    anywhere in the module is its own `def` statement; every other binding —
    a plain assignment, an import alias, a `for`/`with`/`except`/`match`
    target, a walrus, a nested `def`/class redefinition, anything — is
    rejected."""
    tree = module if module is not None else TREE
    guarded = {"_red", "_canonical_contradiction_alarm"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in guarded:
            continue  # the one legitimate binding: the function's own definition
        name = None
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in guarded:
            name = node.id
        elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name) and node.target.id in guarded:
            name = node.target.id
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id in guarded:
                    name = item.optional_vars.id
        elif isinstance(node, ast.ExceptHandler) and node.name in guarded:
            name = node.name
        elif isinstance(node, ast.MatchAs) and node.name in guarded:
            name = node.name
        elif isinstance(node, ast.alias) and (node.asname in guarded or (node.asname is None and node.name in guarded)):
            name = node.asname or node.name
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name) and node.target.id in guarded:
            name = node.target.id
        if name is not None:
            violations.append(f"{getattr(node, 'lineno', '?')}: {name} is rebound outside its "
                              f"defining `def` — this would silently defeat every rc = "
                              f"{name}(...) call that trusts it")
    return violations


def _canonical_health_return_violations(func: ast.FunctionDef) -> list[str]:
    """Every `return` inside `_canonical_health` must be either `return rc`,
    or a literal `return 1` that is preceded — somewhere earlier in the SAME
    enclosing statement block — by a call to `_red(...)` (directly, or via
    `rc = _red(...)`). This matches the function's one actual early return:
    the `except Exception:` branch at the top (when `_canonical_snapshot()`
    itself raises) calls `_red("canonical_health_refused", ...)` to record
    the refusal, prints the completion marker, and only then `return 1`s —
    with the finding call and the return in the same block but not on
    adjacent lines (a completion-marker print and explanatory comments sit
    between them), so this checks "earlier in the block", not "the
    immediately preceding statement". Every other return shape (a bare
    `return 0`, a bare `return 2`, or a literal `return 1` with no `_red()`
    call anywhere earlier in its own block) is rejected."""
    errors: list[str] = []

    def walk_block(body: list) -> None:
        seen_red = False
        for stmt in body:
            if isinstance(stmt, ast.Return):
                is_return_rc = isinstance(stmt.value, ast.Name) and stmt.value.id == "rc"
                is_recorded_return_one = (isinstance(stmt.value, ast.Constant)
                                          and stmt.value.value == 1 and seen_red)
                if not (is_return_rc or is_recorded_return_one):
                    errors.append(f"{func.name}:{stmt.lineno}: return does not match the "
                                  f"allowed shape (`return rc`, or `return 1` preceded by a "
                                  f"`_red(...)` call in the same block)")
            is_red_call = (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                           and isinstance(stmt.value.func, ast.Name)
                           and stmt.value.func.id == "_red")
            is_red_assign = (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                             and isinstance(stmt.targets[0], ast.Name)
                             and stmt.targets[0].id == "rc"
                             and isinstance(stmt.value, ast.Call)
                             and isinstance(stmt.value.func, ast.Name)
                             and stmt.value.func.id == "_red")
            if is_red_call or is_red_assign:
                seen_red = True
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list) and nested and all(isinstance(x, ast.stmt) for x in nested):
                    walk_block(nested)
            for handler in getattr(stmt, "handlers", []) or []:
                walk_block(handler.body)

    walk_block(func.body)
    return errors


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
    """Round 9 of an independent review of PR #1237 converts this from a
    DENYLIST to an ALLOWLIST. Round 8 only forbade a bare literal `rc = 1`
    (or `rc |= 1`) — which meant any OTHER unrecorded write to `rc` (e.g.
    `rc = p.returncode`) got through both this static check and, at runtime,
    without ever calling `_red()` to record a finding. `_canonical_health`
    may now assign `rc` ONLY via one of three exact forms — `rc = 0`,
    `rc = _red(...)`, or `rc = _canonical_contradiction_alarm() or rc` — and
    every other binding of the name `rc` (any other literal, any other call,
    a conditional expression, a boolean-or that isn't the one allowed
    contradiction-alarm pattern, an augmented assignment, a tuple/multiple
    assignment target, or a walrus binding) is rejected. See
    `_allowed_rc_assignment_value`'s docstring for the exact three forms and
    `_rc_assignment_violations`'s for what it rejects and why."""

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

    # ── round 9: the nine allowlist-only mutations ──────────────────────
    # Each plants one statement that a DENYLIST checking only for a bare
    # literal `1` would have missed, into a deep copy of the real
    # `_canonical_health` AST, and asserts the new allowlist check now
    # rejects it. Each needs its own AST node shape — a plain `Call`, a
    # `Constant`, an `AugAssign`, an `IfExp`, a `BoolOp`, a tuple-target
    # `Assign`, and a `NamedExpr` walrus — built either by `ast.parse`-ing
    # the real Python source (simplest and least error-prone) or, for the
    # walrus, wrapped in a statement context that accepts an expression.

    def _assert_mutation_caught(self, stmt_src: str, label: str) -> None:
        mutated = copy.deepcopy(_find_function("_canonical_health"))
        mutated.body.append(ast.parse(stmt_src).body[0])
        ast.fix_missing_locations(mutated)
        violations = _rc_assignment_violations(mutated)
        self.assertNotEqual(violations, [], f"{label} planted into _canonical_health was not caught")

    def test_mutation_rc_equals_p_returncode_is_caught(self):
        # The motivating gap: a denylist checking only for a literal `1`
        # never even looks at a `Call`/`Attribute` RHS like this one.
        self._assert_mutation_caught("if True:\n    rc = p.returncode\n",
                                     "rc = p.returncode")

    def test_mutation_rc_equals_2_is_caught(self):
        # A literal RHS that isn't 0 and isn't reached via `_red(...)`.
        self._assert_mutation_caught("if True:\n    rc = 2\n", "rc = 2")

    def test_mutation_rc_plus_equals_1_is_caught(self):
        # AugAssign — the round-8 check only forbade `|= 1` specifically;
        # this allowlist rejects AugAssign to rc unconditionally.
        self._assert_mutation_caught("if True:\n    rc += 1\n", "rc += 1")

    def test_mutation_rc_equals_max_rc_1_is_caught(self):
        # A Call whose callee is not `_red` — the allowlist only accepts
        # `_red(...)` by name, so any other call is rejected.
        self._assert_mutation_caught("if True:\n    rc = max(rc, 1)\n", "rc = max(rc, 1)")

    def test_mutation_rc_equals_conditional_expression_is_caught(self):
        # ast.IfExp — a conditional expression RHS, not one of the three
        # allowed shapes.
        self._assert_mutation_caught("if True:\n    rc = 1 if cond else rc\n",
                                     "rc = 1 if cond else rc")

    def test_mutation_rc_equals_rc_or_1_is_caught(self):
        # ast.BoolOp(Or) — but NOT the one allowed contradiction-alarm
        # pattern (Call(...) or Name('rc')); here it's Name('rc') or
        # Constant(1), which must still be rejected.
        self._assert_mutation_caught("if True:\n    rc = rc or 1\n", "rc = rc or 1")

    def test_mutation_rc_equals_int_true_is_caught(self):
        # A Call whose callee is the builtin `int`, not `_red`.
        self._assert_mutation_caught("if True:\n    rc = int(True)\n", "rc = int(True)")

    def test_mutation_rc_tuple_target_assignment_is_caught(self):
        # ast.Tuple assignment target that includes `rc` — a multi/tuple
        # target is rejected outright regardless of the values assigned.
        self._assert_mutation_caught("if True:\n    rc, _z = 1, 0\n", "rc, _z = 1, 0")

    def test_mutation_rc_walrus_assignment_is_caught(self):
        # ast.NamedExpr — the walrus operator binding `rc` inside an
        # expression statement.
        self._assert_mutation_caught("if True:\n    (rc := 1)\n", "(rc := 1)")

    # ── round 10: binding forms the round-9 allowlist never looked at ──────
    # Round 9 only inspected plain Assign/AugAssign/NamedExpr. Every OTHER
    # Python construct that can bind a name — an annotated assignment, a
    # `for` target, a `with ... as` target, a `match` case capture, an
    # `except ... as` handler name, or an `import ... as` alias — binds `rc`
    # completely unseen by that check, and none of them go through `_red()`.
    # The reviewer confirmed the first of these (`rc: int = 1`) is a real,
    # currently-unrecorded red that the round-9 gate passed.

    def test_mutation_rc_annotated_assignment_is_caught(self):
        self._assert_mutation_caught("if True:\n    rc: int = 1\n", "rc: int = 1")

    def test_mutation_rc_for_loop_target_is_caught(self):
        self._assert_mutation_caught("for rc in (1,):\n    pass\n", "for rc in (1,):")

    def test_mutation_rc_with_statement_target_is_caught(self):
        self._assert_mutation_caught("with ctx as rc:\n    pass\n", "with ctx as rc:")

    def test_mutation_rc_match_case_capture_is_caught(self):
        self._assert_mutation_caught("match 1:\n    case rc:\n        pass\n", "case rc:")

    def test_mutation_rc_except_as_is_caught(self):
        self._assert_mutation_caught(
            "try:\n    pass\nexcept Exception as rc:\n    pass\n", "except ... as rc:")

    def test_mutation_rc_import_as_is_caught(self):
        self._assert_mutation_caught("import os as rc\n", "import os as rc")

    def test_shadowing_red_or_alarm_outside_their_def_is_caught(self):
        # Round 10, item 3: banning every unrecorded WRITE to rc is pointless
        # if `_red`/`_canonical_contradiction_alarm` can be silently
        # redefined to a no-op elsewhere in the module.
        mutated = copy.deepcopy(TREE)
        mutated.body.append(ast.parse("_red = lambda *a, **k: 1\n").body[0])
        ast.fix_missing_locations(mutated)
        violations = _shadow_violations(mutated)
        self.assertNotEqual(violations, [], "a shadowing `_red = ...` was not caught")

    def test_shadowing_contradiction_alarm_outside_its_def_is_caught(self):
        mutated = copy.deepcopy(TREE)
        mutated.body.append(ast.parse(
            "def _wrap():\n    _canonical_contradiction_alarm = lambda: False\n").body[0])
        ast.fix_missing_locations(mutated)
        violations = _shadow_violations(mutated)
        self.assertNotEqual(violations, [],
                            "a shadowing `_canonical_contradiction_alarm = ...` was not caught")

    def test_no_shadow_violations_in_the_real_module(self):
        self.assertEqual(_shadow_violations(), [])

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


class CanonicalHealthReturnsAreAllowlisted(unittest.TestCase):
    """Round 9 companion to `RcAssignedOnlyViaRed`: the allowlist on `rc`
    assignments alone doesn't close the loop if a `return` could still hand
    back an unrecorded value directly. Every `return` inside
    `_canonical_health` must be `return rc`, or a literal `return 1`
    preceded — earlier in the same block — by a call to `_red(...)`. See
    `_canonical_health_return_violations`'s docstring for the exact real
    shape this matches."""

    def test_canonical_health_returns_match_the_allowed_shape(self):
        fn = _find_function("_canonical_health")
        errors = _canonical_health_return_violations(fn)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_mutation_unrecorded_return_one_is_caught(self):
        src = (
            "def f():\n"
            "    rc = 0\n"
            "    if bad:\n"
            "        return 1\n"
            "    return rc\n"
        )
        fn = ast.parse(src).body[0]
        errors = _canonical_health_return_violations(fn)
        self.assertNotEqual(errors, [],
                            "an unrecorded literal return 1 was not caught")

    def test_mutation_bare_return_zero_is_caught(self):
        src = (
            "def f():\n"
            "    rc = 0\n"
            "    if early:\n"
            "        return 0\n"
            "    return rc\n"
        )
        fn = ast.parse(src).body[0]
        errors = _canonical_health_return_violations(fn)
        self.assertNotEqual(errors, [], "a bare return 0 (not return rc) was not caught")

    def test_recorded_return_one_after_red_in_same_block_is_not_flagged(self):
        src = (
            "def f():\n"
            "    rc = 0\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:\n"
            "        _red('x', 'x', hard_error=True)\n"
            "        print(MARKER)\n"
            "        return 1\n"
            "    return rc\n"
        )
        fn = ast.parse(src).body[0]
        errors = _canonical_health_return_violations(fn)
        self.assertEqual(errors, [], "\n".join(errors))


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
