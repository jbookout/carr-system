#!/usr/bin/env python3
"""chat-lint-gate-selftest.py — fixtures for hooks/chat-lint-gate.py, written
before the hook (rule e65efc68, enforcing rules 5be2f462 and 3a9dbafd).

TWO RULES, ONE MOMENT. Both bind the assistant's own outgoing chat text, and
the only place that text exists before Joe reads it is the Stop hook:

  5be2f462  the contrast-reframe and banned-vocab HARD regexes that already
            guard every prospect-visible surface (tools/writing-lint.py,
            enforced by lint-gate.py on file writes) finally reach the surface
            they were actually taught about: internal dialogue to Joe.
  3a9dbafd  never make a partner decode an ID. A bare 8-hex rule id or a bare
            'loop #250' with no plain words around it forces Joe to go look up
            what the thing is; the id must ride WITH its gloss.

SCOPE IS DELIBERATELY NARROW. Only the HARD ids the audit row names (vocab,
contrast-reframe, contrast-reframe-split) point at chat. contrast-compressed
stays off this surface: 'X, not Y' is REVIEW severity precisely because a
genuine correction of fact takes that shape, and honest technical chat is
full of genuine corrections. The em-dash ban stays prospect-only — chat and
this repo's own house style use em dashes on purpose. A lint that flags
half of every honest report gets muted in a day, and then it catches nothing.

FALSE-POSITIVE GUARDS THE CASES BELOW PIN DOWN: fenced code is exempt (git
output is full of hex), all-digit tokens are exempt (20260814 is a date, not
an id), and an id WITH plain words in its sentence is a gloss, not a bare id.

Spawns the REAL hook with REAL Stop payloads + transcript fixtures.

THE CONTRACT CHANGED 2026-08-16 and these fixtures pin the new one: a wording
fault no longer BLOCKS the turn, because the offending text has already reached
Joe and a block only buys a restatement he pays for twice (rule 1d50a3bb). The
gate now parks a note that hooks/chat-lint-carryover.py injects before the next
reply. CAUGHT = a note was parked; exit 2 is now itself a regression.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "hooks", "chat-lint-gate.py")

# Both are set by main() to paths inside THIS run's private root. See
# build_sandbox() for why they cannot be fixed paths under the real out/.
HOOK = None
CARRY_DIR = None

# (name, assistant_text, expect_caught)
CASES = [
    ("vocab-banned", "This will seamlessly unlock a transformative workflow "
                     "for the whole pipeline.", True),
    ("contrast-reframe", "It's not about speed, it's about correctness of the "
                         "render path.", True),
    ("contrast-split", "This isn't a bug. It's a feature of the exporter that "
                       "was chosen on purpose.", True),
    # bare-id moved to the conduct stop gate 2026-08-22 (loop 504, item three):
    # both gates flagged it under two class names, so one bare identifier blocked
    # the turn twice. This gate must now stay SILENT on it, and the conduct
    # selftest carries the case. Verified before the move: conduct flags this
    # exact string as hex_alone on both ids.
    ("bare-id-list", "Done today:\n- c0b38d80\n- 24e10ee8\nBoth landed.", False),
    # Same move as bare-id-list above. Verified before flipping: the conduct
    # detector flags this exact string as loop_num on both references.
    ("bare-loop-ref", "Close loop #250 and loop #251.", False),
    ("glossed-id", "Rule c0b38d80 (re-bless the baseline in the same commit "
                   "as any gate change) is now enforced by the pre-commit "
                   "hook, so the next unblessed gate change cannot commit.",
     False),
    ("glossed-loop", "Loop #250 (the living-orb panel visual spec) still "
                     "waits on the origination conversation nobody recovered.",
     False),
    ("hex-in-fence", "All green now:\n```\nc0b38d80 gates: re-bless landed\n"
                     "24e10ee8 commit-msg check\n```\nEvery selftest passes.",
     False),
    ("date-not-id", "The audit file 20260814 rows were classified today and "
                    "the queue is ranked by leverage.", False),
    ("plain-chat", "The rebase finished cleanly and every selftest passes. "
                   "The branch is ready to push once the checkout reconciles.",
     False),
    ("unnamed-deal-question", "Can you review the draft lease before lunch?", True),
    ("named-deal-question", "Can you review the Riverwalk draft lease before lunch?", False),
    ("unnamed-loi-question", "Did the LOI arrive from the landlord?", True),
    ("street-named-deal-question", "Did the 123 Main LOI arrive from the landlord?", False),
    ("multi-clause-task", "Please review the lease and approve the redlines today.", True),
    ("numbered-but-unmarked", "Please do these:\n1. Review the lease\n2. Approve the redlines", True),
    ("numbered-all-required", "All required:\n1. Review the lease\n2. Approve the redlines", False),
    ("self-narrated-progress", "I reviewed the lease and approved the redlines today.", False),
    ("confidentiality-access-boundary", "Hermes may not read CARR doctrine because it is confidential.", True),
    ("private-share-boundary", "The provider must not share CARR material because it is private.", True),
    ("confidentiality-correction", "Confidentiality is not the boundary; Hermes cannot hold a live credential.", False),
    ("real-credential-boundary", "Hermes cannot hold a live CARR credential or execute autonomously.", False),
    ("quoted-historical-boundary", "Historical rule text: “The model cannot read CARR records because they are confidential.”", False),
    ("blockquote-historical-boundary", "> The provider may not share CARR material because it is sensitive.", False),
]

passed = 0
bad: list[str] = []


def build_sandbox():
    """A private repo root for THIS run, because out/ is shared machine-wide.

    The gate derives every path it writes from its own file location — the
    parked note at REPO/out/chat-lint-carry/<session>.txt, plus its audit and
    debug logs — and ./run.sh worktree plumbs out/ in every worktree back to
    the one canonical directory. So the fixture this suite used to read,
    out/chat-lint-carry/selftest.txt, was literally the same file for every
    session on this Mac. Two concurrent ops/ci.sh runs deleted and read each
    other's notes, and this suite printed things like

        FAIL stop-active-never-loops  want=clean got=CAUGHT
        FAIL unnamed-deal-question    want=CAUGHT got=clean

    which is indistinguishable from a real regression and costs a push.
    Measured 2026-08-23: red inside a full suite with about eleven other
    ci.sh processes live, then green 4 times out of 4 run alone. Same class
    of bug as CARR_RUN_SPOOL_DB (tools/ops-spool.py) and
    CARR_RUN_SCHEDULED_STATE_DIR (bin/run-scheduled.sh), both of which exist
    because a test that does not override a shared out/ path writes over
    production state.

    THE GATE IS SYMLINKED, NEVER COPIED, so the bytes under test stay the
    installed gate's own and cannot drift from it. os.path.abspath, which is
    what the gate uses to find its repo, does not follow a symlink the way
    realpath does, so the gate resolves REPO to this temp root and keeps
    every file it writes inside it. The session id stays the literal
    "selftest" that the audit skip in this gate and its siblings keys on.

    tools/ is linked in because the gate imports tools/writing-lint.py by
    repo-relative path. A gate that grows some OTHER repo-relative
    dependency turns this suite loudly red rather than quietly wrong: the
    writing half is skipped when that import fails, so every case that
    expects CAUGHT reports clean. The fix then is one more link here.
    """
    root = tempfile.mkdtemp(prefix="chat-lint-gate-selftest-")
    os.mkdir(os.path.join(root, "hooks"))
    os.symlink(GATE, os.path.join(root, "hooks", "chat-lint-gate.py"))
    os.symlink(os.path.join(REPO, "tools"), os.path.join(root, "tools"))
    return root


def run_stop(assistant_text, event="Stop", stop_active=False):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"type": "user", "origin": {"kind": "user"},
                "message": {"content": [{"type": "text", "text": "status?"}]}}) + "\n")
            fh.write(json.dumps({"type": "assistant",
                "message": {"content": [{"type": "text", "text": assistant_text}]}}) + "\n")
        payload = {"hook_event_name": event, "transcript_path": path,
                   "session_id": "selftest", "stop_hook_active": stop_active}
        # The gate no longer BLOCKS on a wording fault (rule 1d50a3bb: the bad
        # text has already reached Joe, so a block only buys a restatement he
        # pays for twice). It parks a note that hooks/chat-lint-carryover.py
        # injects before the next reply. So "caught" now means a note was
        # written, and exit 2 would itself be a regression.
        # CARRY_DIR is this run's own root, never the shared out/ — see
        # build_sandbox().
        carry = os.path.join(CARRY_DIR, "selftest.txt")
        try:
            os.unlink(carry)
        except Exception:
            pass
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=30)
        if p.returncode == 2:
            return False, "REGRESSION: gate blocked instead of carrying"
        note = ""
        if os.path.exists(carry):
            with open(carry) as fh:
                note = fh.read()
            os.unlink(carry)
        return bool(note.strip()), note
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def main():
    global passed, HOOK, CARRY_DIR
    if not os.path.exists(GATE):
        print(f"FAIL: hook not found at {GATE}")
        return 1
    root = build_sandbox()
    HOOK = os.path.join(root, "hooks", "chat-lint-gate.py")
    CARRY_DIR = os.path.join(root, "out", "chat-lint-carry")
    try:
        return run_cases()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_cases():
    global passed
    for name, text, expect in CASES:
        got, out = run_stop(text)
        ok = got == expect
        if ok:
            passed += 1
        else:
            bad.append(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:20} "
              f"want={'CAUGHT' if expect else 'clean'} got={'CAUGHT' if got else 'clean'}")

    got, _ = run_stop(CASES[0][1], stop_active=True)
    if not got:
        passed += 1
    else:
        bad.append("stop-active-loops")
    print(f"  {'ok  ' if not got else 'FAIL'} {'stop-active-never-loops':20} "
          f"want=clean got={'CAUGHT' if got else 'clean'}")

    got, out = run_stop(CASES[0][1])
    if "writing" in out.lower() or "vocab" in out.lower():
        passed += 1
        print("  ok   the carried note names what it caught")
    else:
        bad.append("carry-text")
        print(f"  FAIL the carried note names what it caught — {out[:100]}")

    print(f"\nchat-lint-gate-selftest: {passed}/{passed + len(bad)} passed")
    if bad:
        print("FAILURES: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
