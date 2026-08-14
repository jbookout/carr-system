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

# The two exemptions added 2026-08-14, each pinned in BOTH directions — the
# allow AND the case that must still be refused. An exemption tested only on the
# side that permits is how a gate quietly stops gating.
#
# WORKTREE: the deny text tells the reader "to change branch, use a WORKTREE so
# no other session's tree moves", and the gate then refused exactly that, because
# it read the SHARED tree's dirtiness no matter where the command ran. The remedy
# it recommends was unusable precisely when it was needed.
#
# PATH-SCOPED CHECKOUT: `git checkout <ref> -- <paths>` does not move HEAD and
# rewrites only what it names, so it cannot cause the 2026-08-09 loss (a commit
# sweeping unnamed files, then a branch move underneath everyone). It is exempt
# only while none of the named paths is dirty — restoring a path that has no
# uncommitted changes destroys nothing; restoring one that does is still refused.
DANGEROUS += [
    # A worktree path that does not exist, and a path outside the repo, must
    # both fall back to judging the shared tree — otherwise `cd` anywhere
    # convenient would be a way to talk the gate out of its own job.
    ("wt-missing",    f"cd {os.path.join(REPO, '.claude', 'worktrees', 'nope')} && git checkout -b x"),
    ("wt-outside",    "cd /tmp && git checkout main"),
    # Prose is ignored, but only prose. A real command sitting after a heredoc,
    # or an unterminated heredoc (where the body cannot be identified at all),
    # must still be caught — the strip fails CLOSED by design.
    ("heredoc-then-real", "git commit -F- <<'MSG'\nharmless prose\nMSG\ngit checkout main"),
    ("heredoc-unclosed",  "git commit -F- <<'MSG'\ngit checkout main"),
]

SAFE = [
    ("scoped-clean",  "git checkout HEAD -- README.md"),
    # Writing ABOUT a dangerous command is not running one. Hit three times on
    # 2026-08-14, twice on commit messages for the gate itself — whose whole
    # subject is which git commands are safe.
    ("msg-heredoc",   "git commit -q -F- <<'MSG'\nnever run git checkout main while dirty\nMSG"),
    ("msg-dash-m",    "git commit -m 'explain why git add -A is banned'"),
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

    # The other half of each 2026-08-14 exemption needs LIVE state to be a real
    # test, so both are derived rather than hardcoded, and SKIPPED out loud when
    # the state is absent. A case that silently disappears is how a suite starts
    # reporting green about something it stopped checking.
    tracked_dirty = subprocess.run(
        ["git", "-C", REPO, "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True).stdout.splitlines()
    if tracked_dirty:
        victim = tracked_dirty[0][3:].strip().strip('"')
        DANGEROUS.append(("scoped-dirty", f"git checkout HEAD -- {victim}"))
    else:
        print("  SKIP scoped-dirty      (no modified tracked file to name)")

    wt_root = os.path.join(REPO, ".claude", "worktrees")
    live_wt = next((os.path.join(wt_root, d) for d in sorted(os.listdir(wt_root))
                    if os.path.isdir(os.path.join(wt_root, d))), None) \
        if os.path.isdir(wt_root) else None
    if live_wt:
        SAFE.append(("wt-branch-op", f"cd {live_wt} && git checkout -b probe"))
    else:
        print("  SKIP wt-branch-op      (no worktree exists to run one in)")
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
