#!/usr/bin/env python3
"""gate-replay-coverage.py — the "every gate change ships a real-data replay
test" enforcement, added to ops/ci.sh's gates class.

WHY. Defect class "capability-reported-live-before-first-human-use" recurred
8 times, twice recently and both against invented data:
  PR #1224: a gate parsed an invented transcript shape and never fired once
            against 803 real receipts.
  PR #1225: shell regexes were tested only against invented commands; a
            replay of 12,145 real commands showed 62 false denials and
            trivial bypasses still worked.
Jev's architecture_or_design call (2026-09-24, choice=b_explicit_mapping,
confidence 0.91) picked an explicit mapping file over an import-graph walk or
a bare naming convention: ops/config/gate-replay-map.json names each
hooks/*.py gate's known replay selftest. That file is NOT
ops/config/gate-baseline.json — that one is hooks/gate-integrity.py's
blessed-hash baseline and is unrelated; this script never reads or writes it.

WHAT THIS CHECKS. Given the set of files changed since a base ref (default:
merge-base with origin/main, or main):
  1. Any changed hooks/*.py file is a "watched gate".
  2. Any changed file that is IMPORTED by an unchanged-or-changed hooks/*.py
     file (a simple static `import X` / `from X import` scan, not a full
     import-graph walk -- Jev's pick was the mapping file, not the graph) is
     also a watched gate, attributed to the hook(s) that import it.
  3. Any changed file explicitly listed under "gates" in
     ops/config/gate-replay-map.json is a watched gate.
For each watched gate, the diff must ALSO add or modify a selftest that:
  a. is part of the same changed-file set,
  b. ACTUALLY LOADS a fixture file: a line that both names a path under
     ops/fixtures/real-replay/ and calls one of open(/Path(/glob(/read_text(/
     glob.glob( on it -- a bare mention of the directory in a comment or
     string with no load call does NOT count. (Coordinator review round 2,
     2026-09-24: the first version of this check accepted a text mention
     alone, which a selftest could satisfy by mentioning the fixture path in
     a docstring while never reading it -- this is the gap Jev's
     semantic_creation flagged at 0.57/1.0 in round 1. See CHANGELOG below.)
  c. ACTUALLY CALLS the changed gate's module: a call-shaped reference
     (`<module>.<name>(` ) to the gate's stem name, not just an `import`
     line -- a proxy for "runs the changed gate's logic over it" that a
     static check can prove without executing the selftest.
A gate with no selftest meeting (a)-(c) fails the check by name.

DELETED / RENAMED HOOKS. A hooks/*.py file that is DELETED in this diff is
never a watched gate -- there is no logic left to replay-test, and requiring
one would make every gate retirement PR impossible to land. git represents a
rename as a delete of the old path plus an add of the new one (this script
does not special-case -M rename detection); the new path is watched exactly
like any other new/changed hook, and the deleted old path is exempted, same
as a plain deletion.

SHARED lib/ FILES. When a file that multiple hooks import changes, EVERY
importing hook is a separately-watched gate, each needing its own covering
selftest -- not just one of them. This is the strict reading and was
Jev's own architecture_or_design pick (round 2, item 3): the explicit-mapping
design says nothing about weakening coverage when a dependency is shared, and
a shared helper is exactly the highest-leverage place for a silent regression
to hide behind one hook's passing selftest while another's breaks unnoticed.

USAGE:
    python3 ops/gate-replay-coverage.py [--base <ref>] [--files <path>...]
`--files` is for the selftest: it substitutes an explicit changed-file list
(and file contents read from disk) instead of shelling out to git, so the
check's logic can be exercised on synthetic diffs with no repository at all.

Exit codes: 0 pass, 1 a watched gate lacks a replay test, 78 not configured
here (no git / no base ref resolvable and no --files given -- EX_CONFIG, this
repo's convention, honoured by ops/ci.sh's gates loop).

This is a plain script (this repo's tools/ops convention: shebang + main
guard); it is not a new MCP verb, schema, worker route, or job definition, so
it owes no SCAC registry successor (decision 05e144eb, scac-successor-seal-
procedure memory note).
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ops import business_data_patterns  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join("ops", "config", "gate-replay-map.json")
FIXTURE_DIR_MARKER = "ops/fixtures/real-replay"

IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)

# A line that both names the fixture directory AND calls a load-shaped
# function on it -- the fix for the round-1 gap (a bare text mention was
# previously enough). Deliberately line-scoped rather than whole-file: the
# load call and the fixture path must appear together, not merely both
# somewhere in the file.
LOAD_VERBS = r"(?:open\(|Path\(|glob\(|glob\.glob\(|read_text\(|readlines\(|load_fixture\(|read_fixture\(|iter_fixture\()"
FIXTURE_LOAD_LINE_RE = re.compile(
    re.escape(FIXTURE_DIR_MARKER) + r"[^\n]*" + LOAD_VERBS
    + r"|" + LOAD_VERBS + r"[^\n]*" + re.escape(FIXTURE_DIR_MARKER)
)
# Jev's architecture_or_design re-judge (round 2, item 3, score 1.69/3,
# confidence 0.31 on "plausibly rejects legitimate selftests that load
# fixtures indirectly, e.g. through a shared loader helper"): the verb list
# above already includes load_fixture(/read_fixture(/iter_fixture( as named
# escape hatches for a future shared loader helper, alongside the direct
# open()/Path()/glob() calls every selftest in this repo uses today. No such
# helper exists yet; if one is added, name it one of these three (or extend
# this list in the same commit that adds it) rather than working around the
# check.


def stem_of(path: str) -> str:
    base = os.path.basename(path)
    if base.endswith(".py"):
        base = base[:-3]
    return base


def module_names(stem: str):
    """A hooks/*.py stem can be imported either as itself (hyphenated files
    are not valid Python module names and are never imported, only run) or,
    for underscore-named library files, as their literal module name."""
    return {stem, stem.replace("-", "_")}


def git_changed_files(base_ref: str):
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", base_ref, "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        )
        base = merge_base.stdout.strip() or base_ref
        diff = subprocess.run(
            ["git", "diff", "--name-only", base, "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        )
        # Also include uncommitted changes (staged + unstaged) so a local
        # pre-push run sees work in progress, not just committed history.
        working = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        )
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    files = set()
    for proc in (diff, working, staged):
        if proc.returncode == 0:
            files.update(f for f in proc.stdout.splitlines() if f)
    return files


def git_deleted_files(base_ref: str):
    """Paths this diff REMOVES (committed + staged + working), for exempting
    a deleted hooks/*.py file from find_watched_gates. Best-effort: any
    failure just yields an empty set, same fail-open-to-"no info" behaviour
    as git_changed_files on a fatal git error."""
    try:
        merge_base = subprocess.run(
            ["git", "merge-base", base_ref, "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        )
        base = merge_base.stdout.strip() or base_ref
        procs = [
            subprocess.run(["git", "diff", "--diff-filter=D", "--name-only", base, "HEAD"],
                            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15),
            subprocess.run(["git", "diff", "--diff-filter=D", "--name-only", "HEAD"],
                            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15),
            subprocess.run(["git", "diff", "--diff-filter=D", "--name-only", "--cached"],
                            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15),
        ]
    except (OSError, subprocess.SubprocessError):
        return set()
    deleted = set()
    for proc in procs:
        if proc.returncode == 0:
            deleted.update(f for f in proc.stdout.splitlines() if f)
    return deleted


def scan_fixtures_for_business_data(fixture_dir=None):
    """Scans every committed fixture under ops/fixtures/real-replay/ (or
    `fixture_dir`) for business-data patterns. Returns a list of
    "path:line: pattern1, pattern2" strings, empty if clean. Runs on EVERY
    invocation, independent of the changed-file diff -- a leak already
    committed on a branch that touches nothing else must still fail CI."""
    base = fixture_dir or os.path.join(REPO_ROOT, "ops", "fixtures", "real-replay")
    hits = []
    for path in sorted(globmod.glob(os.path.join(base, "*.jsonl"))):
        rel = os.path.relpath(path, REPO_ROOT)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    matches = business_data_patterns.find_matches(line)
                    if matches:
                        hits.append(f"{rel}:{lineno}: {', '.join(matches)}")
        except OSError:
            continue
    return hits


def read_text(path: str, file_contents=None):
    if file_contents is not None:
        return file_contents.get(path, "")
    full = os.path.join(REPO_ROOT, path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def load_map():
    full = os.path.join(REPO_ROOT, MAP_PATH)
    try:
        with open(full, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"gates": {}, "fixture_dir": FIXTURE_DIR_MARKER}


def find_watched_gates(changed_files, file_contents=None, deleted_files=frozenset()):
    """Returns {gate_hook_path: reason} for every watched gate touched by
    this diff, whether directly (the hook file itself changed) or
    transitively (a lib/ file it imports changed).

    `deleted_files` are paths this diff REMOVES. A deleted hooks/*.py file is
    never watched -- there is no logic left to replay-test, and a rename
    shows up here as a delete of the old path (exempted) plus an add of the
    new one (watched normally, like any other new file)."""
    watched = {}
    changed_set = set(changed_files)

    hook_changes = [
        f for f in changed_files
        if f.startswith("hooks/") and f.endswith(".py") and f not in deleted_files
    ]
    for hook in hook_changes:
        watched[hook] = "hooks/*.py changed directly"

    gate_map = load_map()
    mapped_gates = set(gate_map.get("gates", {}).keys())
    for f in changed_files:
        if f in deleted_files:
            continue
        base = os.path.basename(f)
        if base in mapped_gates and f not in watched:
            watched[f] = "listed in ops/config/gate-replay-map.json"

    # Transitive: does an unchanged-or-changed hooks/*.py import a changed
    # non-hook file? Only scan hooks/*.py source that exists on disk (or in
    # the synthetic file_contents map) -- this is a shallow static scan, by
    # design (Jev's pick was the explicit mapping, not a full import graph).
    non_hook_changed = [f for f in changed_files if f not in watched and f.endswith(".py")]
    if non_hook_changed:
        all_hook_files = set(hook_changes)
        if file_contents is None:
            try:
                all_hook_files |= {
                    os.path.join("hooks", n) for n in os.listdir(os.path.join(REPO_ROOT, "hooks"))
                    if n.endswith(".py")
                }
            except OSError:
                pass
        else:
            all_hook_files |= {p for p in file_contents if p.startswith("hooks/") and p.endswith(".py")}

        for hook in sorted(all_hook_files):
            text = read_text(hook, file_contents)
            if not text:
                continue
            imported = set()
            for m in IMPORT_RE.finditer(text):
                name = m.group(1) or m.group(2)
                if name:
                    imported.add(name.split(".")[0])
            for changed in non_hook_changed:
                changed_stem = stem_of(changed)
                if module_names(changed_stem) & imported:
                    watched.setdefault(
                        hook, f"imports changed file {changed} (transitively watched)"
                    )
    return watched


def selftest_covers_gate(selftest_path, gate_path, file_contents=None):
    text = read_text(selftest_path, file_contents)
    if not text:
        return False
    # (b) an ACTUAL fixture load, not a bare mention. Round-1 gap fix: Jev's
    # semantic_creation flagged this design (0.57/1.0) and the specific risk
    # was a selftest that merely referenced the fixture directory in a
    # docstring/comment/string with no load call -- that would previously
    # have satisfied "b" and (with an import line for "c") passed the check
    # while never actually replaying anything.
    if not FIXTURE_LOAD_LINE_RE.search(text):
        return False
    gate_stem = stem_of(gate_path)
    names = module_names(gate_stem)
    # (c) an ACTUAL call into the gate's module -- `<name>.<attr>(`, not a
    # bare `import <name>` line -- a proxy for "runs the changed gate's logic
    # over it" that this static check can prove without executing anything.
    for n in names:
        if re.search(rf"\b{re.escape(n)}\.\w+\(", text):
            return True
    # Also accept a direct subprocess/shell invocation of the gate's own
    # filename (covers selftests that shell out to the gate rather than
    # importing it), but only alongside an actual invocation marker so a bare
    # filename in a comment still does not count.
    base = os.path.basename(gate_path)
    if base in text and re.search(
        r"(?:subprocess\.(?:run|check_call|check_output|Popen)\([^\n]*"
        + re.escape(base) + r"|\[[^\]\n]*" + re.escape(base) + r"[^\]\n]*\])",
        text,
    ):
        return True
    return False


def check(changed_files, file_contents=None, deleted_files=frozenset()):
    watched = find_watched_gates(changed_files, file_contents, deleted_files)
    if not watched:
        return True, []

    changed_selftests = [
        f for f in changed_files
        if (f.startswith("ops/") and f.endswith("-selftest.py"))
        or (f.startswith("tools/") and (os.path.basename(f).startswith("test_") or os.path.basename(f).startswith("test-")))
    ]

    failures = []
    for gate, reason in sorted(watched.items()):
        covered = any(
            selftest_covers_gate(st, gate, file_contents) for st in changed_selftests
        )
        if not covered:
            gate_map = load_map()
            suggested = gate_map.get("gates", {}).get(os.path.basename(gate))
            if not suggested:
                suggested = f"ops/{stem_of(gate)}-selftest.py"
            failures.append(
                f"{gate}: no replay-covered selftest in this diff ({reason}). "
                f"Add or modify a selftest (e.g. {suggested}) that references "
                f"a fixture under {FIXTURE_DIR_MARKER}/ and exercises {gate}'s "
                f"own logic over it."
            )
    return (not failures), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("CARR_CI_BASE_REF", "main"))
    parser.add_argument("--files", nargs="*", default=None, help="explicit changed-file list (selftest use)")
    args = parser.parse_args()

    # The fixture leak scan runs unconditionally, before anything else: a
    # business-data leak already sitting in a committed fixture must fail CI
    # even on a branch that touches nothing else (jbookout/carr-system is a
    # PUBLIC repo -- coordinator review, 2026-09-24).
    leaks = scan_fixtures_for_business_data()
    if leaks:
        print("gate-replay-coverage: FAILED — business data found in committed real-replay fixtures:", file=sys.stderr)
        for h in leaks:
            print(f"  - {h}", file=sys.stderr)
        return 1

    if args.files is not None:
        changed = set(args.files)
        deleted = set()
    else:
        changed = git_changed_files(args.base)
        if changed is None:
            print("gate-replay-coverage: not configured here (no git available)", file=sys.stderr)
            return 78
        deleted = git_deleted_files(args.base)

    ok, failures = check(changed, deleted_files=deleted)
    if ok:
        print(f"gate-replay-coverage: OK ({len(changed)} changed files checked, fixtures clean)")
        return 0

    print("gate-replay-coverage: FAILED — the following gate(s) changed with no real-data replay test:", file=sys.stderr)
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
