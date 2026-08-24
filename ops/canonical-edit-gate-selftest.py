#!/usr/bin/env python3
"""canonical-edit-gate-selftest.py — fixtures for hooks/canonical-edit-gate.py.

Spawns the REAL hook with a REAL PreToolUse payload and reads its exit code.
Exit 2 = denied, exit 0 = allowed. Same shape as git-writer-gate-selftest.py
and escalation-gate-selftest.py: no mocking of the hook's own logic, because a
test that reimplements the gate to check the gate proves nothing about drift
between the two.

WHY REPO IS SCRIPT-RELATIVE, and what that means for where this actually runs.
Like every gate in this family, REPO here is "wherever this test file itself
lives" — inside a worktree during development, inside ~/carr-system once
merged. The hook under test resolves ITS OWN REPO the identical way, so the
two always agree on what "canonical" means for a given run: a tracked file
relative to REPO is a tracked-canonical-file case, a path under
REPO/.claude/worktrees/<x>/ is a worktree case, regardless of which physical
checkout REPO happens to be this time.

THE DECISION THIS FILE ENCODES: untracked/new files are ALLOWED everywhere in
the canonical tree, not gated. See the hook's own docstring for the reasoning
(git-writer-gate.py draws the identical tracked/untracked line when judging a
worktree, for the same underlying reason — only EXISTING shared content is the
hazard). This suite proves that reading, not just asserts it.
"""
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "canonical-edit-gate.py")
HOOKS_JSON = os.path.join(REPO, "ops", "config", "hooks.json")


def fire(tool, path, env_extra=None, session="selftest"):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    else:
        env.pop("CARR_ALLOW_CANONICAL_EDIT", None)
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"tool_name": tool,
                          "tool_input": {"file_path": path},
                          "session_id": session}),
        capture_output=True, text=True, timeout=30, env=env,
    )
    return p


def first_tracked_file():
    """A real tracked file relative to REPO, so the DENY case is genuine."""
    out = subprocess.run(["git", "-C", REPO, "ls-files", "README.md"],
                         capture_output=True, text=True, timeout=15).stdout.strip()
    if out:
        return "README.md"
    # Fallback: first tracked file at all, in case README.md is ever renamed.
    out = subprocess.run(["git", "-C", REPO, "ls-files"],
                         capture_output=True, text=True, timeout=15).stdout.splitlines()
    return out[0] if out else None


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1

    tracked_rel = first_tracked_file()
    if not tracked_rel:
        print("FAIL: repo has no tracked files to build a fixture from")
        return 1
    tracked_abs = os.path.join(REPO, tracked_rel)

    wt_root = os.path.join(REPO, ".claude", "worktrees")
    fake_wt_name = "_canonical_edit_gate_selftest_fixture"
    fake_wt = os.path.join(wt_root, fake_wt_name)
    os.makedirs(fake_wt, exist_ok=True)
    fake_wt_created = True

    outside_path = "/tmp/canonical-edit-gate-selftest-outside.txt"
    untracked_root = os.path.join(REPO, "_selftest_untracked_probe_canonical_edit_gate.txt")
    untracked_out = os.path.join(REPO, "out", "_selftest_probe_canonical_edit_gate.log")

    passed = failed = 0
    bad = []

    def check(name, tool, path, want_deny, env_extra=None):
        nonlocal passed, failed
        res = fire(tool, path, env_extra=env_extra)
        got_deny = res.returncode == 2
        ok = got_deny == want_deny
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:24} "
              f"want={'DENY ' if want_deny else 'allow'} "
              f"got={'DENY' if got_deny else 'allow'}")
        return res

    try:
        # 1. tracked canonical file -> DENY
        check("tracked-canonical-deny", "Edit", tracked_abs, True)

        # 2. the SAME file, addressed inside a (fixture) worktree -> ALLOW
        check("worktree-file-allow", "Edit", os.path.join(fake_wt, tracked_rel), False)

        # 3. untracked new file at the canonical root -> ALLOW
        check("untracked-root-allow", "Write", untracked_root, False)

        # 4. untracked path under out/ -> ALLOW
        check("untracked-out-allow", "Write", untracked_out, False)

        # 5. anything outside the repo entirely -> ALLOW
        check("outside-repo-allow", "Edit", outside_path, False)

        # 6. non-edit tools are not this gate's business -> ALLOW (no-op)
        check("read-tool-ignored", "Read", tracked_abs, False)

        # 7. escape hatch: same tracked file, but the env var is set -> ALLOW,
        #    and it must say so loudly (systemMessage in stdout), not silently.
        res = check("escape-hatch-allow", "Edit", tracked_abs, False,
                    env_extra={"CARR_ALLOW_CANONICAL_EDIT": "1"})
        loud = "escape hatch" in (res.stdout or "").lower()
        ok = loud
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append("escape-hatch-is-loud")
        print(f"  {'ok  ' if ok else 'FAIL'} {'escape-hatch-is-loud':24} "
              f"want=systemMessage mentions the bypass got="
              f"{'present' if loud else 'MISSING'}")

        # 8. MultiEdit and NotebookEdit are covered too (defensive superset,
        #    matching gate-edit-gate.py's / lint-gate.py's own tool lists).
        check("multiedit-tracked-deny", "MultiEdit", tracked_abs, True)
        check("notebookedit-tracked-deny", "NotebookEdit", tracked_abs, True)

    finally:
        # NO TEMP FILES LEFT IN THE REPO ROOT (2026-08-23 load-flake sweep).
        # The untracked-root probe file was created here and never removed —
        # every ci.sh run dropped _selftest_untracked_probe_*.txt into the
        # checkout and left it there.
        for leftover in (untracked_root, untracked_out):
            try:
                os.unlink(leftover)
            except OSError:
                pass
        if fake_wt_created:
            shutil.rmtree(fake_wt, ignore_errors=True)

    # 9. hooks.json actually wires this gate — a gate nobody wires is not a
    # gate. Read the SOURCE config (not live ~/.claude/settings.json, which a
    # worktree checkout has no independent copy of) and confirm a PreToolUse
    # group covering Write and Edit names canonical-edit-gate.py.
    wired = False
    try:
        cfg = json.load(open(HOOKS_JSON))
        for group in cfg.get("PreToolUse", []):
            matcher_tools = (group.get("matcher") or "").split("|")
            if "Write" not in matcher_tools or "Edit" not in matcher_tools:
                continue
            for h in group.get("hooks", []) or []:
                if "canonical-edit-gate.py" in (h.get("command") or ""):
                    wired = True
    except Exception as exc:
        print(f"  FAIL registration-wired      could not read {HOOKS_JSON}: {exc}")
    passed, failed = (passed + 1, failed) if wired else (passed, failed + 1)
    if not wired:
        bad.append("registration-wired")
    print(f"  {'ok  ' if wired else 'FAIL'} {'registration-wired':24} "
          f"want=PreToolUse Write|Edit group names canonical-edit-gate.py "
          f"got={'present' if wired else 'MISSING'}")

    print()
    print(f"canonical-edit-gate-selftest: {passed}/{passed + failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
