#!/usr/bin/env python3
"""staging-attribution-gate-selftest.py — fixtures for
hooks/staging-attribution-gate.py.

TWO HALVES, TWO TECHNIQUES, BOTH DELIBERATE.

Half one (SPAWN) fires the REAL hook as a subprocess and reads its exit code,
same convention as ops/git-writer-gate-selftest.py: exit 2 = denied, 0 =
allowed. This covers everything whose verdict does NOT depend on the real
repo's live git status — the wholesale-form refusal is unconditional by
design (see the hook's own docstring), and the pass-through cases (non-git,
unparseable, malformed JSON) are state-independent by construction.

Half two (IMPORT) loads the hook module directly and monkeypatches
`porcelain_status()` to a controlled fixture dict instead of shelling out to
the real `git status`. This is the ONLY way to test the per-path attribution
logic deterministically: ~/carr-system is a shared tree with several sessions
committing concurrently (the very hazard this hook exists to fix), so a test
that depends on which real paths happen to be dirty right now would be
flaky by construction, or worse, would have to manufacture real dirty state
in a tree other sessions are actively using. `own_written_paths()` is left
UNMOCKED and run against a real synthetic transcript file, so the transcript-
scan logic — the actual attribution mechanism — is exercised for real.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "staging-attribution-gate.py")

spec = importlib.util.spec_from_file_location(
    "staging_attribution_gate", HOOK
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# ---------------------------------------------------------------- SPAWN half
def fire(cmd, transcript=None, session="selftest"):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd},
               "session_id": session}
    if transcript:
        payload["transcript_path"] = transcript
    p = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
    )
    return p.returncode, p.stdout, p.stderr


WHOLESALE_DENY = [
    ("add-A", "git add -A"),
    ("add-a", "git add -a"),
    ("add-dot", "git add ."),
    ("add-all-long", "git add --all"),
    ("add-u", "git add -u"),
    ("add-combined-short", "git add -Av"),
    ("commit-a", "git commit -a -m 'wip'"),
    ("commit-am", "git commit -am 'wip'"),
    ("commit-all-long", "git commit --all -m 'wip'"),
]

PASS_THROUGH_ALLOW = [
    ("status", "git status --short"),
    ("log", "git log --oneline -5"),
    ("not-git", "python3 ops/conduct-gate-selftest.py"),
    ("add-unbalanced-quote", 'git add "unterminated'),
]


# --------------------------------------------------------------- IMPORT half
def tool_write(file_path):
    return {"message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": file_path}}
    ]}}


def make_transcript(*records):
    fd, path = tempfile.mkstemp(prefix="sag-selftest-", suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


def run_main(command, transcript_path, session="selftest"):
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "session_id": session, "transcript_path": transcript_path,
               "cwd": REPO}
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                mod.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.stdin = old_stdin
    return code, out.getvalue(), err.getvalue()


class FakeDbTap:
    """Spy standing in for tools/db-tap.py so the test never touches the
    real ~/.config/carr/local-actor.json or out/break-glass-receipts.log."""
    def __init__(self):
        self.receipts = []

    def local_actor_slug(self):
        return "selftest-actor"

    def append_receipt(self, actor, mode, target, host, reason):
        self.receipts.append((actor, mode, target, host, reason))


def import_case_own_path_allowed():
    transcript = make_transcript(tool_write(os.path.join(REPO, "fake/mine.py")))
    orig = mod.porcelain_status
    mod.porcelain_status = lambda: {"fake/mine.py": "M "}
    try:
        code, out, err = run_main("git add fake/mine.py", transcript)
    finally:
        mod.porcelain_status = orig
        os.unlink(transcript)
    return code == 0


def import_case_foreign_path_denied():
    transcript = make_transcript(tool_write(os.path.join(REPO, "fake/mine.py")))
    orig = mod.porcelain_status
    mod.porcelain_status = lambda: {"fake/foreign.py": "M "}
    try:
        code, out, err = run_main("git add fake/foreign.py", transcript)
        mentions = "fake/foreign.py" in err
    finally:
        mod.porcelain_status = orig
        os.unlink(transcript)
    return code == 2 and mentions


def import_case_untracked_new_file_allowed():
    transcript = make_transcript()
    orig = mod.porcelain_status
    mod.porcelain_status = lambda: {"fake/brand-new.py": "??"}
    try:
        code, out, err = run_main("git add fake/brand-new.py", transcript)
    finally:
        mod.porcelain_status = orig
        os.unlink(transcript)
    return code == 0


def import_case_override_allowed_and_receipted():
    transcript = make_transcript()
    orig_status = mod.porcelain_status
    orig_dbtap = mod._db_tap
    mod.porcelain_status = lambda: {"fake/foreign.py": "M "}
    spy = FakeDbTap()
    mod._db_tap = lambda: spy
    try:
        cmd = ('CARR_ADOPT_ORPHAN=1 CARR_ADOPT_REASON="writer vanished, '
               'adopting the file" git add fake/foreign.py')
        code, out, err = run_main(cmd, transcript)
        receipted = len(spy.receipts) == 1 and spy.receipts[0][1] == "staging-adopt-orphan"
        confirmed_visibly = "receipt" in out.lower() or "adopt" in out.lower()
    finally:
        mod.porcelain_status = orig_status
        mod._db_tap = orig_dbtap
        os.unlink(transcript)
    return code == 0 and receipted and confirmed_visibly


def import_case_override_flag_without_reason_still_denied():
    transcript = make_transcript()
    orig = mod.porcelain_status
    mod.porcelain_status = lambda: {"fake/foreign.py": "M "}
    try:
        code, out, err = run_main(
            "CARR_ADOPT_ORPHAN=1 git add fake/foreign.py", transcript
        )
    finally:
        mod.porcelain_status = orig
        os.unlink(transcript)
    return code == 2


def import_case_malformed_json_allows():
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("{ this is not json")
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                mod.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 0
    finally:
        sys.stdin = old_stdin
    return code == 0


IMPORT_CASES = [
    ("own-path-allowed", import_case_own_path_allowed, True),
    ("foreign-path-denied", import_case_foreign_path_denied, False),
    ("untracked-new-file-allowed", import_case_untracked_new_file_allowed, True),
    ("override-allowed-and-receipted", import_case_override_allowed_and_receipted, True),
    ("override-no-reason-denied", import_case_override_flag_without_reason_still_denied, False),
    ("malformed-json-allows", import_case_malformed_json_allows, True),
]


def main():
    if not os.path.exists(HOOK):
        print(f"FAIL: hook not found at {HOOK}")
        return 1

    passed = failed = 0
    bad = []

    print("-- spawn half: wholesale forms (must DENY, unconditionally) --")
    for name, cmd in WHOLESALE_DENY:
        code, out, err = fire(cmd)
        ok = code == 2
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:22} want=DENY  got={'DENY' if code == 2 else 'allow'}")

    print("-- spawn half: pass-through (must ALLOW) --")
    for name, cmd in PASS_THROUGH_ALLOW:
        code, out, err = fire(cmd)
        ok = code == 0
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:22} want=allow got={'DENY' if code == 2 else 'allow'}")

    # malformed top-level JSON on stdin, via the real subprocess path too
    p = subprocess.run([sys.executable, HOOK], input="not json at all",
                        capture_output=True, text=True, timeout=30)
    ok = p.returncode == 0
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    if not ok:
        bad.append("malformed-json-spawn")
    print(f"  {'ok  ' if ok else 'FAIL'} {'malformed-json-spawn':22} want=allow got={'DENY' if p.returncode == 2 else 'allow'}")

    print("-- import half: per-path attribution (fixture git status) --")
    for name, fn, want_allow in IMPORT_CASES:
        try:
            ok = fn()
        except Exception as exc:
            ok = False
            print(f"       ({name} raised: {exc})")
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        if not ok:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:32} verdict={'as expected' if ok else 'WRONG'}")

    print()
    print(f"staging-attribution-gate-selftest: {passed}/{passed + failed} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
