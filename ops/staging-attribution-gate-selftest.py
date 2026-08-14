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
import shutil
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
    # The other direction, so the exemption above cannot widen unnoticed: a REAL
    # wholesale command sitting after a heredoc is still refused, and an
    # unterminated heredoc strips nothing at all (the helper fails closed).
    ("heredoc-then-real-add-all",
     "git commit -F- <<'MSG'\nharmless prose\nMSG\ngit add -A"),
    ("heredoc-unclosed-hiding-add-all",
     "git commit -F- <<'MSG'\ngit add -A"),
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
    # Writing ABOUT whole-tree staging is not doing it. This gate refused a
    # commit whose own staging was correctly path-named, because the MESSAGE
    # explained why the wholesale forms are banned — 2026-08-14, on the commit
    # for the sibling gate's fix for exactly the same defect.
    ("msg-heredoc-mentions-add-all",
     "git add hooks/x.py && git commit -q -F- <<'MSG'\nexplain why git add -A is banned\nMSG"),
    ("msg-dash-m-mentions-commit-a",
     "git add hooks/x.py && git commit -m 'why git commit -am is refused'"),
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


# --- 2026-08-14 fix: attribute by OBSERVATION as well as by transcript -----
# staging-observation-tracker.py accumulates a per-session
# out/staging-observed/<session>.json of tracked paths this session made
# dirty by running a script through Bash. These cases exercise the union
# staging-attribution-gate.py now does: own_written_paths() UNION
# observed_dirty_paths(). mod.OBSERVED_DIR is monkeypatched to an isolated
# temp dir for every case so this never reads or writes the real out/
# directory (which real concurrent sessions are using).

def import_case_observed_script_write_allowed():
    """THE REAL SHAPE, case 1: a script (not Write/Edit/MultiEdit) modified
    an existing tracked file, the tracker observed it, and THIS session then
    stages it -- must ALLOW even though the transcript carries zero
    Write/Edit/MultiEdit records for that path."""
    transcript = make_transcript()  # no Write/Edit/MultiEdit records at all
    orig_status, orig_dir = mod.porcelain_status, mod.OBSERVED_DIR
    tmp_observed = tempfile.mkdtemp(prefix="sag-observed-")
    session = "observed-fixture-session"
    mod.porcelain_status = lambda: {"fake/script-written.py": "M "}
    mod.OBSERVED_DIR = tmp_observed
    try:
        with open(os.path.join(tmp_observed, f"{session}.json"), "w") as fh:
            json.dump({"observed": ["fake/script-written.py"], "pending": {}}, fh)
        code, out, err = run_main("git add fake/script-written.py", transcript, session=session)
    finally:
        mod.porcelain_status, mod.OBSERVED_DIR = orig_status, orig_dir
        os.unlink(transcript)
        shutil.rmtree(tmp_observed, ignore_errors=True)
    return code == 0


def import_case_not_observed_and_not_written_denied():
    """THE REAL SHAPE, case 2: a file modified by another process, with NO
    Write/Edit record AND no observation by this session (empty observed
    state) -- must still DENY. Proves the union does not quietly become
    allow-everything."""
    transcript = make_transcript()  # no Write/Edit/MultiEdit records
    orig_status, orig_dir = mod.porcelain_status, mod.OBSERVED_DIR
    tmp_observed = tempfile.mkdtemp(prefix="sag-observed-empty-")
    session = "no-observation-session"
    mod.porcelain_status = lambda: {"fake/untouched.py": "M "}
    mod.OBSERVED_DIR = tmp_observed  # empty: no state file for this session
    try:
        code, out, err = run_main("git add fake/untouched.py", transcript, session=session)
        mentions = "fake/untouched.py" in err
    finally:
        mod.porcelain_status, mod.OBSERVED_DIR = orig_status, orig_dir
        os.unlink(transcript)
        shutil.rmtree(tmp_observed, ignore_errors=True)
    return code == 2 and mentions


def import_case_observed_set_does_not_bypass_wholesale():
    """THE REAL SHAPE, case 3: wholesale forms (`git add -A`) must still DENY
    unconditionally even when the observed set would have covered every
    dirty path individually. Observation is an attribution signal for named
    paths, never a reason to waive the wholesale-form refusal -- decision
    9e1f83c2 has no exception for it."""
    transcript = make_transcript()
    orig_status, orig_dir = mod.porcelain_status, mod.OBSERVED_DIR
    tmp_observed = tempfile.mkdtemp(prefix="sag-observed-wholesale-")
    session = "observed-wholesale-session"
    mod.porcelain_status = lambda: {"fake/a.py": "M ", "fake/b.py": "M "}
    mod.OBSERVED_DIR = tmp_observed
    try:
        with open(os.path.join(tmp_observed, f"{session}.json"), "w") as fh:
            json.dump({"observed": ["fake/a.py", "fake/b.py"], "pending": {}}, fh)
        code, out, err = run_main("git add -A", transcript, session=session)
    finally:
        mod.porcelain_status, mod.OBSERVED_DIR = orig_status, orig_dir
        os.unlink(transcript)
        shutil.rmtree(tmp_observed, ignore_errors=True)
    return code == 2


IMPORT_CASES = [
    ("own-path-allowed", import_case_own_path_allowed, True),
    ("foreign-path-denied", import_case_foreign_path_denied, False),
    ("untracked-new-file-allowed", import_case_untracked_new_file_allowed, True),
    ("override-allowed-and-receipted", import_case_override_allowed_and_receipted, True),
    ("override-no-reason-denied", import_case_override_flag_without_reason_still_denied, False),
    ("malformed-json-allows", import_case_malformed_json_allows, True),
    ("observed-script-write-allowed", import_case_observed_script_write_allowed, True),
    ("not-observed-and-not-written-denied", import_case_not_observed_and_not_written_denied, False),
    ("observed-set-does-not-bypass-wholesale", import_case_observed_set_does_not_bypass_wholesale, False),
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
