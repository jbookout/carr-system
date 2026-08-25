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

# The one scrubber (ops/git_env.py), not a local copy. GIT_DIR is exported by
# every git hook and overrides cwd for any child git process — the 2026-08-14
# incident where fixture commits landed on live main is exactly what an
# unscrubbed ENV here would reproduce, since ENV is handed to every git call
# and to fleet-sync.sh itself (which shells out to git internally).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "bin", "fleet-sync.sh")
SAFETY = os.path.join(REPO, "tools", "fleet_sync_safety.py")
HEALTH_SUBMODULE = os.path.join(REPO, "tools", "health_submodule.py")
ENV = dict(fixture_env(), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
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
    # THE FIXTURE MUST DECLARE ITS DEFAULT BRANCH, NOT INHERIT THE MACHINE'S.
    # `git init --bare` points the new repository's HEAD at whatever
    # init.defaultBranch says, which is "main" on this Mac and "master" on a
    # GitHub runner. Clone B below takes its branch from origin's HEAD, so on
    # the runner it landed on an unborn "master" while the only real branch was
    # "main" — leaving B detached. fleet-sync.sh then refused for the RIGHT
    # reason ("checkout is on 'HEAD', not main") and the dirty-tree assertion
    # failed against a message about something else entirely. Four cases passed
    # locally and the same four failed in CI, which is the shape of a test that
    # is measuring the host rather than the code. Setting HEAD explicitly here
    # is version-safe where `init -b` is not.
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")

    b = os.path.join(tmp, "B")
    git(tmp, "clone", "-q", origin, "B")
    os.makedirs(os.path.join(b, "bin"), exist_ok=True)
    os.makedirs(os.path.join(b, "ops"), exist_ok=True)
    os.makedirs(os.path.join(b, "tools"), exist_ok=True)
    shutil.copy(SCRIPT, os.path.join(b, "bin", "fleet-sync.sh"))
    shutil.copy(SAFETY, os.path.join(b, "tools", "fleet_sync_safety.py"))
    shutil.copy(HEALTH_SUBMODULE, os.path.join(b, "tools", "health_submodule.py"))
    open(os.path.join(b, "ops", "config-as-code.py"), "w").write(STUB)

    # origin moves ahead; B is now stale exactly as Dell's Mac was.
    open(os.path.join(a, "f.txt"), "a").write("newer\n")
    git(a, "add", "f.txt")
    git(a, "commit", "-qm", "newer")
    git(a, "push", "-q", "origin", "main")
    return b


def add_expected_quill_patch(tmp, clone):
    """Add the exact permanent Quill-style patch state to ``clone``.

    The submodule is locally seeded so the test exercises the same classifier
    path as production rather than merely faking a porcelain row.
    """
    quill_remote = os.path.join(tmp, "quill-origin")
    quill_source = os.path.join(tmp, "quill-source")
    os.makedirs(quill_remote)
    git(quill_remote, "init", "-q", "--bare")
    git(tmp, "clone", "-q", quill_remote, "quill-source")
    open(os.path.join(quill_source, "quill.txt"), "w").write("base\n")
    git(quill_source, "add", "quill.txt")
    git(quill_source, "commit", "-qm", "quill base")
    git(quill_source, "branch", "-M", "main")
    git(quill_source, "push", "-q", "origin", "main")
    git(quill_remote, "symbolic-ref", "HEAD", "refs/heads/main")

    # Rebuild the fixture's remote from a parent containing the submodule, then
    # clone it exactly as the machine under test would.
    parent = os.path.join(tmp, "parent")
    git(tmp, "clone", "-q", os.path.join(tmp, "origin"), "parent")
    os.makedirs(os.path.join(parent, "tools", "dictation-rig", "patches"), exist_ok=True)
    git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q",
        quill_remote, "tools/dictation-rig/vendor/quill")
    patch = os.path.join(parent, "tools", "dictation-rig", "patches", "0001.patch")
    open(patch, "w").write("--- a/quill.txt\n+++ b/quill.txt\n@@ -1 +1 @@\n-base\n+patched\n")
    git(parent, "add", ".gitmodules", "tools/dictation-rig/vendor/quill", patch)
    git(parent, "commit", "-qm", "add quill patch")
    git(parent, "push", "-q", "origin", "main")

    # Advance B with the parent contents and initialise its submodule.  The
    # expected dirty state is then a real diff equal to the tracked patch.
    git(clone, "pull", "-q", "--ff-only", "origin", "main")
    git(clone, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    quill = os.path.join(clone, "tools", "dictation-rig", "vendor", "quill")
    open(os.path.join(quill, "quill.txt"), "w").write("patched\n")
    return quill, parent


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


def test_expected_quill_patch_allows_unrelated_fast_forward():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        _, parent = add_expected_quill_patch(tmp, b)
        # Parent moves only an unrelated tracked path beyond B's current remote.
        open(os.path.join(parent, "f.txt"), "a").write("unrelated remote change\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "unrelated remote change")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "fast-forwarded" in r.stdout, r.stdout
        assert open(os.path.join(b, "tools", "dictation-rig", "vendor", "quill", "quill.txt")).read() == "patched\n"
    print("PASS  exact expected Quill patch permits unrelated fast-forward")


def test_extra_quill_edit_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, parent = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "a").write("hand edit\n")
        open(os.path.join(parent, "f.txt"), "a").write("remote advance\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "remote advance")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "actionable" in r.stdout, r.stdout
    print("PASS  extra Quill edit refuses")


def test_changed_patch_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        _, parent = add_expected_quill_patch(tmp, b)
        patch = os.path.join(parent, "tools", "dictation-rig", "patches", "0001.patch")
        open(patch, "a").write("# changed remotely\n")
        git(parent, "add", "tools/dictation-rig/patches/0001.patch")
        git(parent, "commit", "-qm", "change tracked Quill patch")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "incoming update changes Quill gitlink or tracked patches" in r.stdout, repr(r.stdout + r.stderr)
    print("PASS  incoming changed tracked patch refuses")


def test_moved_quill_gitlink_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        quill, parent = add_expected_quill_patch(tmp, b)
        open(os.path.join(quill, "quill.txt"), "w").write("locally committed move\n")
        git(quill, "add", "quill.txt")
        git(quill, "commit", "-qm", "local quill move")
        open(os.path.join(parent, "f.txt"), "a").write("remote advance\n")
        git(parent, "add", "f.txt")
        git(parent, "commit", "-qm", "remote advance")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "actionable" in r.stdout, r.stdout
    print("PASS  moved Quill gitlink refuses")


def test_incoming_quill_gitlink_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        b = build(tmp)
        _, parent = add_expected_quill_patch(tmp, b)
        remote_quill = os.path.join(parent, "tools", "dictation-rig", "vendor", "quill")
        open(os.path.join(remote_quill, "quill.txt"), "w").write("upstream moved\n")
        git(remote_quill, "add", "quill.txt")
        git(remote_quill, "commit", "-qm", "move incoming quill gitlink")
        git(remote_quill, "push", "-q", "origin", "HEAD:main")
        git(parent, "add", "tools/dictation-rig/vendor/quill")
        git(parent, "commit", "-qm", "advance incoming quill gitlink")
        git(parent, "push", "-q", "origin", "main")
        r = run_sync(b)
        assert r.returncode == 78, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "incoming update changes Quill gitlink or tracked patches" in r.stdout, repr(r.stdout + r.stderr)
    print("PASS  incoming moved Quill gitlink refuses")


def main():
    if not os.path.exists(SCRIPT):
        print(f"fleet-sync-selftest: {SCRIPT} missing", file=sys.stderr)
        return 1
    test_dirty_tree_refuses()
    test_clean_tree_fast_forwards()
    test_already_current_is_a_noop()
    test_non_main_branch_refuses()
    test_expected_quill_patch_allows_unrelated_fast_forward()
    test_extra_quill_edit_refuses()
    test_changed_patch_refuses()
    test_moved_quill_gitlink_refuses()
    test_incoming_quill_gitlink_refuses()
    print("9/9 fleet-sync cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
