#!/usr/bin/env python3
"""Fixture for worktree-self-plumb.py's orphan reaper.

The reaper deletes directories, so every rule that KEEPS a worktree is pinned
here against a real git repository — a mock would test the parsing, not the
behaviour, and the behaviour is the part that removes 4.4GB when it is right
and somebody's session when it is wrong. Rules under test are the 2026-08-18
manual sweep's (see the hook's docstring): skip canonical / locked / dirty /
fresh-index (<6h), reap a clean idle NAMED branch, reap a detached HEAD only
when it is an ancestor of origin/main.

The live removal path shells through bin/worktree.sh --remove (rule a8c55a47);
that half runs only where zsh exists (macOS always; some CI runners not), and
classification — the part that decides — is covered everywhere.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(os.path.dirname(HERE), "hooks", "worktree-self-plumb.py")

spec = importlib.util.spec_from_file_location("worktree_self_plumb", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures: list[str] = []


def git(*args: str, cwd: str) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, check=True,
                       capture_output=True, text=True)
    return p.stdout.strip()


def age_index(canon: str, wt: str, hours: float) -> None:
    """Backdate the worktree's private index so the 6h rule sees it idle."""
    gitdir = mod.index_gitdir(wt)
    assert gitdir, f"no gitdir resolved for {wt}"
    idx = os.path.join(gitdir, "index")
    target = idx if os.path.exists(idx) else gitdir
    past = time.time() - hours * 3600
    os.utime(target, (past, past))


with tempfile.TemporaryDirectory() as tmp:
    canon = os.path.join(tmp, "canonical")
    os.makedirs(os.path.join(canon, "hooks"))
    with open(os.path.join(canon, "hooks", "marker.py"), "w") as fh:
        fh.write("# marker\n")
    git("init", "-q", "-b", "main", cwd=canon)
    git("config", "user.email", "t@example.com", cwd=canon)
    git("config", "user.name", "t", cwd=canon)
    git("add", "-A", cwd=canon)
    git("commit", "-qm", "one", cwd=canon)
    first_sha = git("rev-parse", "HEAD", cwd=canon)
    with open(os.path.join(canon, "second.txt"), "w") as fh:
        fh.write("two\n")
    git("add", "-A", cwd=canon)
    git("commit", "-qm", "two", cwd=canon)
    # a local stand-in for origin/main at today's tip
    git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=canon)
    # a commit that is NOT on origin/main
    git("checkout", "-qb", "side", cwd=canon)
    with open(os.path.join(canon, "side.txt"), "w") as fh:
        fh.write("side\n")
    git("add", "-A", cwd=canon)
    git("commit", "-qm", "side", cwd=canon)
    side_sha = git("rev-parse", "HEAD", cwd=canon)
    git("checkout", "-q", "main", cwd=canon)

    def add_wt(name: str, *args: str, at: str = "") -> str:
        wt = os.path.join(tmp, name)
        git("worktree", "add", "-q", *args, wt, *((at,) if at else ()), cwd=canon)
        return wt

    wt_orphan = add_wt("orphan-branch", "-b", "orphan-branch")
    wt_dirty = add_wt("dirty", "-b", "dirty-branch")
    with open(os.path.join(wt_dirty, "loose.txt"), "w") as fh:
        fh.write("uncommitted\n")
    wt_fresh = add_wt("fresh", "-b", "fresh-branch")
    wt_locked = add_wt("locked", "-b", "locked-branch")
    git("worktree", "lock", wt_locked, cwd=canon)
    wt_det_anc = add_wt("det-ancestor", "--detach", at=first_sha)
    wt_det_no = add_wt("det-unpublished", "--detach", at=side_sha)
    wt_mine = add_wt("mine", "-b", "mine-branch")
    # an old checkout whose .gitignore predates the plumbing names: the
    # untracked .venv SYMLINK shows as `??` but is this system's own
    # handiwork, which --remove drops before its dirty test — so it must
    # read clean here too (found live 2026-08-18: ~20 worktrees kept for it)
    wt_plumbed = add_wt("plumbed-orphan", "-b", "plumbed-orphan")
    os.symlink(os.path.join(canon, ".venv"), os.path.join(wt_plumbed, ".venv"))

    # everything except wt_fresh has sat idle 7h; wt_fresh stays warm
    ALL_IDLE = (wt_orphan, wt_dirty, wt_locked, wt_det_anc, wt_det_no,
                wt_mine, wt_plumbed)
    for wt in ALL_IDLE:
        age_index(canon, wt, 7)

    # ── classification, entry by entry ────────────────────────────────────
    skip = {os.path.realpath(canon), os.path.realpath(wt_mine)}
    verdicts = {}
    for entry in mod.worktree_entries(canon):
        v = mod.classify(os.path.realpath(canon), entry, skip)
        verdicts[os.path.basename(entry["path"])] = v and v[0]

    expect = {
        "canonical": None,          # skip set — never judged
        "mine": None,               # the invoking session's own tree
        "orphan-branch": "reap",    # clean + idle + named branch
        "dirty": "keep",            # uncommitted work
        "fresh": "keep",            # index <6h — possibly live
        "locked": "keep",
        "det-ancestor": "reap",     # detached, already on origin/main
        "det-unpublished": "keep",  # detached, commit not upstream
        "plumbed-orphan": "reap",   # untracked plumbing symlink is not work
    }
    for name, want in expect.items():
        got = verdicts.get(name, "MISSING")
        if got != want:
            failures.append(f"classify({name}): want {want}, got {got}")

    # mark_alive must pull a stale index back inside the live window
    mod.mark_alive(wt_orphan)
    fresh_age = mod.index_age_s(wt_orphan)
    if fresh_age is None or fresh_age > 60:
        failures.append(f"mark_alive did not refresh the index (age {fresh_age})")
    age_index(canon, wt_orphan, 7)

    # ── dry run touches nothing ───────────────────────────────────────────
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mod.reap_main(["--reap", "--dry-run", "--repo", canon, "--skip", wt_mine])
    out = buf.getvalue()
    if "would remove orphan-branch" not in out:
        failures.append(f"dry run should name orphan-branch:\n{out}")
    if not os.path.isdir(wt_orphan):
        failures.append("dry run REMOVED a worktree")

    # ── live run, where zsh can drive bin/worktree.sh ─────────────────────
    # `git status` during the dry run refreshed every candidate's index, which
    # correctly re-marks them "possibly live" for 6h — so back-date them again
    # before the live pass, as a night of real idleness would.
    for wt in ALL_IDLE:
        age_index(canon, wt, 7)
    if shutil.which("zsh"):
        os.makedirs(os.path.join(canon, "bin"))
        shutil.copy2(os.path.join(os.path.dirname(HERE), "bin", "worktree.sh"),
                     os.path.join(canon, "bin", "worktree.sh"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mod.reap_main(["--reap", "--repo", canon, "--skip", wt_mine])
        out = buf.getvalue()
        gone = [("orphan-branch", wt_orphan), ("det-ancestor", wt_det_anc),
                ("plumbed-orphan", wt_plumbed)]
        kept = [("dirty", wt_dirty), ("fresh", wt_fresh), ("locked", wt_locked),
                ("det-unpublished", wt_det_no), ("mine", wt_mine)]
        for name, wt in gone:
            if os.path.isdir(wt):
                failures.append(f"live run should have reaped {name}:\n{out}")
        for name, wt in kept:
            if not os.path.isdir(wt):
                failures.append(f"live run REAPED {name}, which must be kept:\n{out}")
        # branch ref survives its worktree — the whole reason reaping is safe
        try:
            git("show-ref", "--verify", "refs/heads/orphan-branch", cwd=canon)
        except subprocess.CalledProcessError:
            failures.append("reaping orphan-branch deleted its branch ref")
        if os.path.exists(os.path.join(canon, "out", "worktree-reap.lock")):
            failures.append("reap_main left its lock behind")
    else:
        print("  (zsh unavailable — live-removal half skipped, classification covered)")

if failures:
    print("worktree-self-plumb selftest FAILED")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("worktree-self-plumb selftest passed")
