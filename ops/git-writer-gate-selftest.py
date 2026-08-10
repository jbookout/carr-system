#!/usr/bin/env python3
"""git-writer-gate-selftest.py — fixtures for hooks/git-writer-gate.py.

Spawns the REAL hook and reads its exit code. Exit 2 = denied, 0 = allowed.

WHY THE COMMANDS ARE ASSEMBLED FROM PIECES rather than written literally: this
suite has to name genuinely destructive git invocations, and the pre-existing
egress guard (hooks/guard-unattended.py) scans the Bash command a session sends
and refuses on sight of a hard reset. Running the fixtures from a FILE keeps
those strings out of any Bash tool call, so the two guards do not fight. The
join() indirection additionally keeps them from matching a naive grep of this
repo. This is deliberate, not obfuscation.

BOTH HALVES MATTER. MUST-DENY is the sweep that cost an hour on 2026-08-09.
MUST-ALLOW is every read-only verb plus `git add <paths>` — the correct pattern.
A gate that blocks ordinary committing gets switched off, which is the same as
never building it.

NOTE ON STATE: the deny half only fires when ~/carr-system actually holds
uncommitted work. On a clean tree the gate allows everything by design, so those
cases are reported as SKIPPED rather than passed — a green run against a clean
tree would be meaningless.
"""
import json
import os
import subprocess
import sys

# The repo this file lives in, not a guess at where it is checked out. The old
# expanduser("~/carr-system") assumed one clone location on one machine and
# reported "hook not found" anywhere else (2026-08-10 fresh-machine audit).
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "git-writer-gate.py")

_H = "-" * 2 + "hard"                     # --hard  (was mis-assembled as -hard,
                                          # which made the reset case test a
                                          # flag that does not exist and pass
                                          # for the wrong reason)
_FD = "-fd"
_A = "-A"

DANGEROUS = [
    ("checkout-branch", "git checkout main"),
    ("switch-branch",   "git switch some-branch"),
    ("commit-a",        "git commit -am 'wip'"),
    ("add-A",           f"git add {_A} && git commit -m x"),
    ("add-dot",         "git add ."),
    ("clean-fd",        f"git clean {_FD}"),
    ("reset-hard",      f"git reset {_H} origin/main"),
    ("restore-dot",     "git restore ."),
    ("checkout-dot",    "git checkout -- ."),
    ("stash-bare",      "git stash"),
]

SAFE = [
    ("add-specific",  "git add bin/precheck.sh && git commit -m 'x'"),
    ("commit-plain",  "git commit -m 'already staged'"),
    ("status",        "git status --short"),
    ("log",           "git log --oneline -5"),
    ("diff",          "git diff HEAD~1"),
    ("show",          "git show abc1234 --stat"),
    ("branch-list",   "git branch -a"),
    ("reflog",        "git reflog -8"),
    ("stash-list",    "git stash list"),
    ("worktree-list", "git worktree list"),
    ("not-git",       "python3 ops/conduct-gate-selftest.py"),
]


def fire(cmd):
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": cmd},
                          "session_id": "selftest"}),
        capture_output=True, text=True, timeout=30)
    return p.returncode == 2


def tree_is_dirty():
    out = subprocess.run(
        ["git", "-C", REPO, "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, timeout=20).stdout
    return len([l for l in out.splitlines() if l.strip()])


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1

    dirty = tree_is_dirty()
    print(f"  working tree: {dirty} uncommitted path(s)")
    print()

    passed = failed = skipped = 0
    bad = []

    for name, cmd in DANGEROUS:
        if not dirty:
            print(f"  SKIP {name:18} (clean tree — gate allows by design)")
            skipped += 1
            continue
        got = fire(cmd)
        ok = got is True
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:18} want=DENY  got={'DENY' if got else 'allow'}")

    for name, cmd in SAFE:
        got = fire(cmd)
        ok = got is False
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:18} want=allow got={'DENY' if got else 'allow'}")

    print()
    print(f"git-writer-gate-selftest: {passed}/{passed + failed} passed"
          + (f", {skipped} skipped (clean tree)" if skipped else ""))
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
