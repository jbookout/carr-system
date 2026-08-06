#!/usr/bin/env python3
"""test-ledger-sweep.py — proves the ledger-sweep Stop hook fires on real
rulings/overrides and stays quiet on the two false-positive classes found
live on 2026-08-05/06 (loop #191):

  1. HARNESS TURNS READ AS PARTNER SPEECH — the walk-back to "the last human
     turn" landed on a <task-notification> block (an agent-completion event)
     instead of Joe's actual last message.
  2. QUESTIONS READ AS RULINGS — trigger 4 fired on a pure interrogative
     ("do we need to do a confirmation run ... or are you good?").

No test harness for this hook existed before this file (verified: no
tools/test-*ledger* anywhere in the repo). This file was written fresh to
prove the loop #191 fix, and it can be pointed at any copy of the hook via
LEDGER_SWEEP_HOOK to re-check a prior version — that is how the "before"
counts in the fix's commit/changelog were produced: run once against
`git show HEAD:hooks/ledger-sweep.py` (the pre-fix state) and once against
the hook on disk.

    .venv/bin/python tools/test-ledger-sweep.py                    # current
    LEDGER_SWEEP_HOOK=/path/to/old/copy.py \\
        .venv/bin/python tools/test-ledger-sweep.py                # any other copy
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.expanduser("~/carr-system")
HOOK = os.environ.get("LEDGER_SWEEP_HOOK", f"{REPO}/hooks/ledger-sweep.py")

FIRE, QUIET = True, False


# --------------------------------------------------------------------------
# Transcript record builders. Shapes mirror real ~/.claude/projects/*.jsonl
# records (verified against live transcripts): a genuine typed turn carries
# `origin: {"kind": "human"}`; a harness-injected task-notification carries
# `origin: {"kind": "task-notification"}`; a tool result is a `user` record
# with no `origin` field and no text content block at all.
# --------------------------------------------------------------------------

def human(text):
    return {"type": "user", "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


def human_no_origin(text):
    """Older-format / bare fixture: a genuine human turn with no `origin`
    field at all. The content-prefix fallback must not misfire on this."""
    return {"type": "user",
            "message": {"role": "user", "content": text}}


def task_notification(summary_text):
    """A harness-injected agent-completion event. `content` deliberately
    contains ruling-shaped language, so a test that still fires proves the
    hook is reading the notification as if it were the partner talking."""
    content = ("<task-notification>\n<task-id>abc123</task-id>\n"
               "<tool-use-id>toolu_01X</tool-use-id>\n<status>completed</status>\n"
               f"<summary>{summary_text}</summary>\n</task-notification>")
    return {"type": "user", "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": content}}


def system_reminder(text):
    content = f"<system-reminder>\n{text}\n</system-reminder>"
    return {"type": "user",
            "message": {"role": "user", "content": content}}


def tool_result():
    return {"type": "user",
            "message": {"role": "user",
                         "content": [{"type": "tool_result",
                                      "tool_use_id": "toolu_01Y",
                                      "content": "ok"}]}}


def assistant(text):
    return {"type": "assistant",
            "message": {"role": "assistant",
                         "content": [{"type": "text", "text": text}]}}


CASES = [
    # ---- false-positive class 1: harness turn read as partner speech -----
    # The real 2026-08-05/06 shape: Joe says "go", the assistant kicks off a
    # background agent, the agent finishes and posts a task-notification
    # whose summary is stuffed with ruling-shaped language, and the response
    # ends there. The hook must walk PAST the notification to "go" (which
    # trips no trigger) and stay quiet — not quote the notification as his
    # words.
    ("class-1 · task-notification after real 'go' stays quiet", QUIET, [
        human("go"),
        assistant("Starting the background agent now."),
        task_notification("Agent finished. We should always log every "
                           "deal from now on, no exceptions."),
        assistant("Done — the agent's summary is above."),
    ]),
    # Same shape but the notification is buried behind a tool_result too,
    # proving the walk-back skips more than one harness record in a row.
    ("class-1 · notification + tool_result both skipped, reaches real turn",
     QUIET, [
        human("looks good, ship it"),
        assistant("On it."),
        tool_result(),
        task_notification("we need to always split fees this way from now on"),
    ]),
    # Control: strip the origin/task-notification wrapper and the SAME
    # ruling-shaped text, now genuinely typed by the partner, must still
    # fire. Proves the fix keys on shape, not on making the trigger patterns
    # weaker.
    ("class-1 control · same ruling text as a REAL human turn still fires",
     FIRE, [
        human("we should always log every deal from now on, no exceptions"),
    ]),
    # Bare fixture with no `origin` field at all: the content-prefix fallback
    # must still catch a <task-notification> block when there is no shape
    # signal to key on.
    ("class-1 · no-origin task-notification-shaped text still skipped",
     QUIET, [
        human_no_origin("go"),
        {"type": "user",
         "message": {"role": "user",
                      "content": "<task-notification>\nwe should always do "
                                 "X from now on\n</task-notification>"}},
    ]),
    # system-reminder fallback (pre-existing behavior, must not regress).
    ("class-1 · system-reminder wrapper still skipped (pre-existing)",
     QUIET, [
        human_no_origin("go"),
        system_reminder("we should always do X from now on"),
    ]),

    # ---- false-positive class 2: questions read as rulings ----------------
    # The real 2026-08-05/06 line. Pure interrogative, trigger 4's "or are
    # you good" / lead-in shape used to fire; must now stay quiet.
    ("class-2 · pure question ('do we ... or are you good?') stays quiet",
     QUIET, [
        human("do we need to do a confirmation run of that last command "
              "or are you good?"),
    ]),
    ("class-2 · pure question, 'should we' lead + embedded trigger-1 words",
     QUIET, [
        human("should we go with the smaller unit rather than the larger "
              "one?"),
    ]),
    # Embedded ruling inside a question — a trailing tag-question, NOT a
    # leading interrogative. This must keep firing: the turn opens as a
    # declarative ruling and only tacks a "right?" on the end.
    ("class-2 control · embedded ruling with trailing '?' still fires",
     FIRE, [
        human("we're doing X from now on, right?"),
    ]),
    # A question that hits ONLY a trigger outside the suppression set
    # (trigger 6, which exists specifically to catch challenges posed as
    # questions) must still fire — the fix is narrow to triggers 1 and 4.
    ("class-2 control · question hitting trigger 6 only still fires",
     FIRE, [
        human("did you actually verify that?"),
    ]),

    # ---- plain regressions: ordinary true positives must still fire -------
    ("regression · 'from now on always do X' (trigger 4)", FIRE, [
        human("from now on always do X"),
    ]),
    ("regression · override 'no, that goes in the database instead' "
     "(trigger 1)", FIRE, [
        human("no, that goes in the database instead of the spreadsheet"),
    ]),
    ("regression · hedged-confident correction (trigger 7)", FIRE, [
        human("i'm fairly certain we already switched to the new export "
              "path"),
    ]),
    ("regression · already logged since that turn stays quiet", QUIET, [
        human("from now on always do X"),
        assistant("Logging that with log-decision now."),
    ]),
    ("regression · no ledger-worthy content at all stays quiet", QUIET, [
        human("what time is the meeting tomorrow"),
    ]),
]


def run_hook(hook_path, records):
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False) as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
        transcript_path = fh.name
    try:
        payload = {"transcript_path": transcript_path,
                   "stop_hook_active": False}
        p = subprocess.run([sys.executable, hook_path],
                           input=json.dumps(payload),
                           capture_output=True, text=True, timeout=20)
        fired = "LEDGER SWEEP" in (p.stdout or "")
        return fired, p.stdout, p.stderr
    finally:
        os.unlink(transcript_path)


def main():
    print(f"testing: {HOOK}\n")
    failed = 0
    for name, expected, records in CASES:
        got, out, err = run_hook(HOOK, records)
        ok = got == expected
        failed += 0 if ok else 1
        exp_s = "FIRE " if expected else "quiet"
        got_s = "FIRE " if got else "quiet"
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}  "
              f"(expected {exp_s}, got {got_s})")
        if not ok and err:
            print(f"          stderr: {err[:200]}")
    print(f"\npassed {len(CASES) - failed} · failed {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
