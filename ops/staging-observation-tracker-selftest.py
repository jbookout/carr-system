#!/usr/bin/env python3
"""staging-observation-tracker-selftest.py — fixtures for
hooks/staging-observation-tracker.py.

Exercises the REAL Pre/Post `git status` diff mechanics against a disposable
fixture git repo (never the real ~/carr-system tree, which several sessions
use concurrently) by calling `handle()` directly with synthetic PreToolUse
and PostToolUse payloads, same technique as the delegation-gate selftest's
in-process harness. Each case builds its own temp repo and temp
out/staging-observed/ directory so cases never interfere with each other or
with a real session's state.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "staging-observation-tracker.py")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

spec = importlib.util.spec_from_file_location("staging_observation_tracker", HOOK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def git(repo, *args, check=True):
    """Every git call this file makes, confined to `repo`.

    THE ENV ARGUMENT IS THE WHOLE POINT. `cwd=repo` alone is not isolation:
    git reads GIT_DIR before the working directory, and every git hook exports
    GIT_DIR. ops/githooks/pre-push runs ops/ci.sh which runs this file, so
    before 2026-08-13 a plain `git push` made the three calls below rewrite the
    REAL repo's user.email to selftest@example.com and commit to it. Verified by
    running this file against a clone with GIT_DIR exported: the clone's
    identity changed and its HEAD moved. See ops/git_env.py. Loop #371.
    """
    return subprocess.run(["git", *args], cwd=repo, check=check,
                          env=fixture_env(), capture_output=True, text=True)


def make_fixture_repo():
    """A disposable git repo with one committed, tracked file."""
    repo = tempfile.mkdtemp(prefix="sot-fixture-repo-")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "selftest@example.com")
    git(repo, "config", "user.name", "selftest")
    tracked = os.path.join(repo, "tracked.txt")
    with open(tracked, "w") as fh:
        fh.write("original content\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


def pre_payload(repo, session, call_id):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": repo,
            "session_id": session, "tool_use_id": call_id, "tool_input": {"command": "true"}}


def post_payload(repo, session, call_id):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": repo,
            "session_id": session, "tool_use_id": call_id, "tool_input": {"command": "true"},
            "tool_response": {"success": True}}


def observed_for(session):
    with open(mod.state_path(session)) as fh:
        return set(json.load(fh).get("observed", []))


def case_script_modifies_tracked_file_is_observed():
    """THE REAL SHAPE, case 1: a script modifies an EXISTING tracked file
    between the Pre and Post snapshot of one Bash call -- must be observed."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-a-")
    orig_state_dir = mod.STATE_DIR
    mod.STATE_DIR = observed_dir
    repo = None
    try:
        repo = make_fixture_repo()
        session, call_id = "sot-case-1", "call-1"
        mod.handle(pre_payload(repo, session, call_id), repo=repo)
        # The script: a plain shell write to the already-tracked file,
        # exactly the shape staging-attribution-gate.py's transcript scan
        # cannot see (no Write/Edit/MultiEdit tool_use record involved).
        with open(os.path.join(repo, "tracked.txt"), "a") as fh:
            fh.write("appended by a script\n")
        mod.handle(post_payload(repo, session, call_id), repo=repo)
        observed = observed_for(session)
        ok = observed == {"tracked.txt"}
        if not ok:
            print(f"       observed={observed!r}, want {{'tracked.txt'}}")
        return ok
    finally:
        mod.STATE_DIR = orig_state_dir
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_already_dirty_before_pre_is_not_credited():
    """THE REAL SHAPE, case 2: a file was modified by another process BEFORE
    this session's Bash call ever ran (so it is already dirty at the Pre
    snapshot) -- the Post snapshot sees the same dirty state, not a NEW
    change, so it must NOT be credited to this session. This is the
    differential that keeps another session's in-flight edit from being
    silently adopted just because this session later ran an unrelated
    command in the same tree."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-b-")
    orig_state_dir = mod.STATE_DIR
    mod.STATE_DIR = observed_dir
    repo = None
    try:
        repo = make_fixture_repo()
        # Another process dirties the file BEFORE this session's Bash call.
        with open(os.path.join(repo, "tracked.txt"), "a") as fh:
            fh.write("dirtied by someone else, before this call\n")
        session, call_id = "sot-case-2", "call-1"
        mod.handle(pre_payload(repo, session, call_id), repo=repo)  # already dirty here
        # This session's own command touches something unrelated, not the
        # already-dirty file.
        with open(os.path.join(repo, "untouched.txt"), "w") as fh:
            fh.write("new file, not the concern of this case\n")
        mod.handle(post_payload(repo, session, call_id), repo=repo)
        observed = observed_for(session)
        ok = "tracked.txt" not in observed
        if not ok:
            print(f"       observed={observed!r} wrongly credited tracked.txt")
        return ok
    finally:
        mod.STATE_DIR = orig_state_dir
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_no_matching_pre_snapshot_fails_safe():
    """A Post call whose tool_use_id has no matching Pre snapshot (harness
    restart, or this call's Pre never fired) must credit nothing -- fail
    SAFE, never guess."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-c-")
    orig_state_dir = mod.STATE_DIR
    mod.STATE_DIR = observed_dir
    repo = None
    try:
        repo = make_fixture_repo()
        with open(os.path.join(repo, "tracked.txt"), "a") as fh:
            fh.write("modified with no prior Pre snapshot\n")
        session = "sot-case-3"
        mod.handle(post_payload(repo, session, "orphan-call-id"), repo=repo)
        observed = observed_for(session) if os.path.exists(mod.state_path(session)) else set()
        ok = observed == set()
        if not ok:
            print(f"       observed={observed!r}, want empty")
        return ok
    finally:
        mod.STATE_DIR = orig_state_dir
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_untracked_new_file_never_credited():
    """A brand-new untracked file ("??") is never credited here --
    staging-attribution-gate.py already allows those unconditionally, so
    crediting them here would be redundant, not additive."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-d-")
    orig_state_dir = mod.STATE_DIR
    mod.STATE_DIR = observed_dir
    repo = None
    try:
        repo = make_fixture_repo()
        session, call_id = "sot-case-4", "call-1"
        mod.handle(pre_payload(repo, session, call_id), repo=repo)
        with open(os.path.join(repo, "brand-new.txt"), "w") as fh:
            fh.write("never seen before\n")
        mod.handle(post_payload(repo, session, call_id), repo=repo)
        observed = observed_for(session) if os.path.exists(mod.state_path(session)) else set()
        ok = "brand-new.txt" not in observed
        if not ok:
            print(f"       observed={observed!r} wrongly credited a new untracked file")
        return ok
    finally:
        mod.STATE_DIR = orig_state_dir
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_accumulates_across_multiple_calls():
    """Two separate Bash calls in the same session, each touching a
    different tracked file -- both must accumulate into the one running
    observed set, not overwrite each other."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-e-")
    orig_state_dir = mod.STATE_DIR
    mod.STATE_DIR = observed_dir
    repo = None
    try:
        repo = make_fixture_repo()
        second = os.path.join(repo, "second.txt")
        with open(second, "w") as fh:
            fh.write("second tracked file\n")
        git(repo, "add", "second.txt")
        git(repo, "commit", "-q", "-m", "second file")

        session = "sot-case-5"
        mod.handle(pre_payload(repo, session, "call-1"), repo=repo)
        with open(os.path.join(repo, "tracked.txt"), "a") as fh:
            fh.write("first call touches tracked.txt\n")
        mod.handle(post_payload(repo, session, "call-1"), repo=repo)

        mod.handle(pre_payload(repo, session, "call-2"), repo=repo)
        with open(second, "a") as fh:
            fh.write("second call touches second.txt\n")
        mod.handle(post_payload(repo, session, "call-2"), repo=repo)

        observed = observed_for(session)
        ok = observed == {"tracked.txt", "second.txt"}
        if not ok:
            print(f"       observed={observed!r}, want both files")
        return ok
    finally:
        mod.STATE_DIR = orig_state_dir
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_non_bash_tool_ignored():
    """A non-Bash tool (e.g. Read) must never engage this tracker."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-f-")
    orig_state_dir = mod.STATE_DIR
    mod.STATE_DIR = observed_dir
    repo = None
    try:
        repo = make_fixture_repo()
        session = "sot-case-6"
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": repo,
                   "session_id": session, "tool_use_id": "call-1", "tool_input": {"file_path": "x"}}
        mod.handle(payload, repo=repo)
        ok = not os.path.exists(mod.state_path(session))
        if not ok:
            print("       a Read call wrongly created tracker state")
        return ok
    finally:
        mod.STATE_DIR = orig_state_dir
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_outside_repo_cwd_ignored():
    """A Bash call whose cwd is outside the tracked repo must be ignored,
    even for a real git repo at that other location."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-g-")
    orig_state_dir = mod.STATE_DIR
    mod.STATE_DIR = observed_dir
    other_repo = None
    try:
        other_repo = make_fixture_repo()
        session, call_id = "sot-case-7", "call-1"
        # repo= parameter simulates the tracker's own REPO constant staying
        # fixed while cwd points elsewhere -- in_repo() must say no.
        pre = pre_payload(other_repo, session, call_id)
        mod.handle(pre, repo="/some/other/repo/entirely")
        ok = not os.path.exists(mod.state_path(session))
        if not ok:
            print("       an out-of-scope cwd wrongly created tracker state")
        return ok
    finally:
        mod.STATE_DIR = orig_state_dir
        if other_repo:
            shutil.rmtree(other_repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_main_subprocess_smoke():
    """The real subprocess entry point (main(), reading stdin, always exit
    0) works end to end for one Pre call -- proves the wiring shape, not
    just the in-process handle() path."""
    observed_dir = tempfile.mkdtemp(prefix="sot-state-h-")
    repo = None
    try:
        repo = make_fixture_repo()
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": repo,
                   "session_id": "sot-subprocess", "tool_use_id": "call-1",
                   "tool_input": {"command": "true"}}
        env = dict(os.environ)
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                           text=True, capture_output=True, env=env, timeout=30)
        return p.returncode == 0
    finally:
        if repo:
            shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(observed_dir, ignore_errors=True)


def case_malformed_json_never_raises():
    p = subprocess.run([sys.executable, HOOK], input="not json at all",
                       capture_output=True, text=True, timeout=30)
    return p.returncode == 0


CASES = [
    ("script-modifies-tracked-file-is-observed", case_script_modifies_tracked_file_is_observed),
    ("already-dirty-before-pre-is-not-credited", case_already_dirty_before_pre_is_not_credited),
    ("no-matching-pre-snapshot-fails-safe", case_no_matching_pre_snapshot_fails_safe),
    ("untracked-new-file-never-credited", case_untracked_new_file_never_credited),
    ("accumulates-across-multiple-calls", case_accumulates_across_multiple_calls),
    ("non-bash-tool-ignored", case_non_bash_tool_ignored),
    ("outside-repo-cwd-ignored", case_outside_repo_cwd_ignored),
    ("main-subprocess-smoke", case_main_subprocess_smoke),
    ("malformed-json-never-raises", case_malformed_json_never_raises),
]


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1
    passed = failed = 0
    bad = []
    for name, fn in CASES:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"       ({name} raised: {type(exc).__name__}: {exc})")
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:48} verdict={'as expected' if ok else 'WRONG'}")
    print()
    print(f"staging-observation-tracker-selftest: {passed}/{passed + failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
