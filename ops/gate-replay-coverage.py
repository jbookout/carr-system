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
  b. contains a reference to a fixture path under ops/fixtures/real-replay/,
  c. references the changed gate's module (by stem name, e.g. `bash_write_gate`
     or `bash-write-gate`) somewhere in its own source -- a proxy for "runs
     the changed gate's logic over the fixture" that a static check can prove
     without executing the selftest.
A gate with no selftest meeting (a)-(c) fails the check by name.

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
import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join("ops", "config", "gate-replay-map.json")
FIXTURE_DIR_MARKER = "ops/fixtures/real-replay"

IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)


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


def find_watched_gates(changed_files, file_contents=None):
    """Returns {gate_hook_path: reason} for every watched gate touched by
    this diff, whether directly (the hook file itself changed) or
    transitively (a lib/ file it imports changed)."""
    watched = {}
    changed_set = set(changed_files)

    hook_changes = [f for f in changed_files if f.startswith("hooks/") and f.endswith(".py")]
    for hook in hook_changes:
        watched[hook] = "hooks/*.py changed directly"

    gate_map = load_map()
    mapped_gates = set(gate_map.get("gates", {}).keys())
    for f in changed_files:
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
    if FIXTURE_DIR_MARKER not in text:
        return False
    gate_stem = stem_of(gate_path)
    names = module_names(gate_stem)
    if any(n in text for n in names):
        return True
    # Also accept a reference to the gate's own filename (covers selftests
    # that shell out to the gate rather than importing it).
    return os.path.basename(gate_path) in text


def check(changed_files, file_contents=None):
    watched = find_watched_gates(changed_files, file_contents)
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

    if args.files is not None:
        changed = set(args.files)
    else:
        changed = git_changed_files(args.base)
        if changed is None:
            print("gate-replay-coverage: not configured here (no git available)", file=sys.stderr)
            return 78

    ok, failures = check(changed)
    if ok:
        print(f"gate-replay-coverage: OK ({len(changed)} changed files checked)")
        return 0

    print("gate-replay-coverage: FAILED — the following gate(s) changed with no real-data replay test:", file=sys.stderr)
    for f in failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
