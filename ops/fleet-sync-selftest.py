#!/usr/bin/env python3
"""
fleet-sync-selftest.py — acceptance test for bin/fleet-sync.sh, written the same
day as the script (rule e65efc68: write the test before the thing, especially
before a gate).

THE INCIDENT IT COMES FROM, 2026-08-18. Dell's Mac was migrated clean on
2026-08-11. Over the following week the repo grew new gates; nothing on his
machine ever fetched them, so his sessions announced the whole hook tuple as
missing and — having no escalation gate installed — sent every blocker to Joe.
He was pulled between two machines twice in one day. An audit of all nineteen
scheduled jobs that day found not one that touched a git remote, so no machine
could ever catch itself up.

WHAT THE JOB MUST DO, one assertion per line of that:

  1. FAST-FORWARD a clean checkout that is behind, then re-render the installed
     wiring from what it just pulled.
  2. REFUSE, loudly and without touching anything, when the tree has local
     changes. Dell's machine deliberately carries two uncommitted edits from his
     migration; a blind fast-forward would have destroyed them. This is the most
     important case in the file.
  3. REFUSE when the checkout is a worktree or is not on main. Worktree-per-
     session means most sessions run elsewhere, and syncing from there would
     move a branch somebody is mid-edit on.
  4. BE A CLEAN NO-OP when already current, so running it four times a day
     costs nothing and never destabilises a machine.

Every case runs against throwaway repositories under a temp dir with a stubbed
installer. Nothing here touches the real machine's config, which is the point:
a test that re-renders live settings is not a test.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "bin", "fleet-sync.sh")
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

STUB = "import sys\nprint('stub installer')\nsys.exit(0)\n"


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {r.stderr}")
    return r.stdout.strip()


def run_sync(clone):
    return subprocess.run(["zsh", os.path.join(clone, "bin", "fleet-sync.sh")],
                          cwd=clone, capture_output=True, text=True, env=ENV)


def build(tmp):
    """origin + clone A (pushes) + clone B (the machine under test, falls behind)."""
    origin = os.path.join(tmp, "origin")
    os.makedirs(origin)
    git(origin, "init", "-q", "--bare")
    a = os.path.join(tmp, "A")
    git(tmp, "clone", "-q", origin, "A")
    open(os.path.join(a, "f.txt"), "w").write("base\n")
    git(a, "add", "f.txt")
    git(a, "commit", "-qm", "base")
    git(a, "branch", "-M", "main")
    git(a, "push", "-q", "origin", "main")

    b = os.path.join(tmp, "B")
    git(tmp, "clone", "-q", origin, "B")
    os.makedirs(os.path.join(b, "bin"), exist_ok=True)
    os.makedirs(os.path.join(b, "ops"), exist_ok=True)
    shutil.copy(SCRIPT, os.path.join(b, "bin", "fleet-sync.sh"))
    open(os.path.join(b, "ops", "config-as-code.py"), "w").write(STUB)

    # origin moves ahead; B is now stale exactly as Dell's Mac was.
    open(os.path.join(a, "f.txt"), "a").write("newer\n")
    git(a, "add", "f.txt")
    git(a, "commit", "-qm", "newer")
    git(a, "push", "-q", "origin", "main")
    return b


def behind(clone):
    git(clone, "fetch", "-q", "origin", "main")
    return int(git(clone, "rev-list", "--count", "HEAD..origin/main"))


def test_dirty_tree_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        open(os.path.join(b, "f.txt"), "a").write("uncommitted local work\n")
        r = run_sync(b)
        assert r.returncode == 78, f"expected skip 78, got {r.returncode}: {r.stdout}{r.stderr}"
        assert "refusing to fast-forward" in r.stdout, r.stdout
        assert "f.txt" in r.stdout, "the skip must NAME the dirty path"
        assert behind(b) == 1, "it must not have advanced the checkout"
        assert "uncommitted local work" in open(os.path.join(b, "f.txt")).read(), \
            "LOCAL WORK WAS DESTROYED — the one thing this job must never do"
    print("PASS  dirty tree refuses, names the file, destroys nothing")


def test_clean_tree_fast_forwards():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        assert behind(b) == 1
        r = run_sync(b)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "fast-forwarded" in r.stdout, r.stdout
        assert "stub installer" in r.stdout, "must re-render wiring after pulling"
        assert behind(b) == 0
    print("PASS  clean tree fast-forwards, then re-renders the wiring")


def test_already_current_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        run_sync(b)
        r = run_sync(b)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "already current" in r.stdout, r.stdout
        assert "fast-forwarded" not in r.stdout
    print("PASS  second run is a clean no-op")


def test_non_main_branch_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        git(b, "checkout", "-q", "-b", "some-session-branch")
        r = run_sync(b)
        assert r.returncode == 78, f"expected skip 78, got {r.returncode}: {r.stdout}"
        assert "not main" in r.stdout, r.stdout
        assert behind(b) == 1, "it must not have moved a session's branch"
    print("PASS  refuses on a non-main branch without moving it")


def main():
    if not os.path.exists(SCRIPT):
        print(f"fleet-sync-selftest: {SCRIPT} missing", file=sys.stderr)
        return 1
    test_dirty_tree_refuses()
    test_clean_tree_fast_forwards()
    test_already_current_is_a_noop()
    test_non_main_branch_refuses()
    print("4/4 fleet-sync cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
