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

# Script-relative, NOT expanduser("~/carr-system"). CI checks the repo out to
# /home/runner/work/carr-system/carr-system, which is not under $HOME, so the old
# form did not fail an assertion — it failed to FIND the hook and took the whole
# file down before a single case ran.
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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


def loop_tick():
    """The third false-positive shape, found live 2026-08-06 minutes after the
    first fix shipped: the autonomous-loop tick prompt is injected AS a user
    message wearing a human origin stamp, so shape-based detection alone
    trusted it. Its text deliberately contains override/challenge-shaped
    language pulled from the real prompt ('you should scale back', 'don't
    describe what could be done')."""
    content = ("# Autonomous loop check\n\nYou're being invoked on a timer "
               "while the user is away. Don't describe what could be done — "
               "you should scale back to a quick CI check and stop, not "
               "narrate. Is that really the right approach?")
    return {"type": "user", "origin": {"kind": "human"},
            "message": {"role": "user", "content": content}}


def cross_session_message(summary_text):
    """The FOURTH false-positive shape, found live 2026-08-14. A message relayed
    from ANOTHER CLAUDE SESSION arrives as a user record wearing a human origin
    stamp, exactly like the loop tick. Its text is a peer agent talking, never
    the partner, so a ruling inside it is another machine's words.

    Note the shape carefully: the harness prefixes the tag with a plain-English
    lead-in line, so the record does NOT start with "<cross-session-message" and
    a startswith() check on the tag alone does not see it."""
    content = ("Another Claude session sent a message:\n"
               "<cross-session-message from=\"uds:/tmp/cc-socks/52188.sock\" "
               "from-name=\"peer\" from-mode=\"prompting\">\n"
               f"{summary_text}\n</cross-session-message>")
    return {"type": "user", "origin": {"kind": "human"},
            "message": {"role": "user", "content": content}}


def peer_relay_real_shape(summary_text):
    """The RELAY AS THE TRANSCRIPT ACTUALLY RECORDS IT, counted from a live
    session file on 2026-08-14: origin kind "peer" AND isMeta true. The earlier
    fixture in this file guessed kind "human" from the loop tick's behaviour and
    was wrong about the bytes — both fixtures are kept, because the guessed one
    still pins the content-prefix fallback for any record that lacks origin."""
    content = ("Another Claude session sent a message:\n"
               "<cross-session-message from=\"uds:/tmp/cc-socks/52188.sock\">\n"
               f"{summary_text}\n</cross-session-message>")
    return {"type": "user", "origin": {"kind": "peer"}, "isMeta": True,
            "message": {"role": "user", "content": content}}


def stop_hook_feedback(text):
    """Harness commentary injected when a Stop gate reopens the turn. 12 such
    records in the same live transcript, every one isMeta true with NO origin
    field. It is the SESSION's own gate talking, never the partner, and no
    content prefix in this hook ever matched it."""
    return {"type": "user", "isMeta": True,
            "message": {"role": "user", "content": f"Stop hook feedback: {text}"}}


def unknown_future_origin(text):
    """A kind nobody has invented yet. This is the whole point of the positive
    test: the blocklist trusted every unrecognised kind, so each new harness
    shape defeated it once, in production, before anyone knew it existed. Four
    times for this one hook. A kind that is not "human" is not the partner."""
    return {"type": "user", "origin": {"kind": "webhook-relay-2027"},
            "message": {"role": "user", "content": text}}


CASES = [
    # ---- THE POSITIVE TEST: anything not positively the partner stays quiet --
    ("class-5 \u00b7 peer relay in its REAL transcript shape (kind=peer, isMeta)",
     QUIET, [
        human("go"),
        assistant("Working."),
        peer_relay_real_shape("You should always commit these files from now on."),
    ]),
    ("class-5 \u00b7 stop-hook feedback is the session's own gate, not the partner",
     QUIET, [
        human("go"),
        assistant("Working."),
        stop_hook_feedback("CONDUCT GATE — you must always do X from now on."),
    ]),
    ("class-6 \u00b7 an UNKNOWN future origin kind is not trusted", QUIET, [
        human("go"),
        assistant("Working."),
        unknown_future_origin("no, that goes in the database instead. always."),
    ]),
    ("class-6 control \u00b7 isMeta on a HUMAN-stamped record still stays quiet",
     QUIET, [
        human("go"),
        assistant("Working."),
        {"type": "user", "origin": {"kind": "human"}, "isMeta": True,
         "message": {"role": "user",
                      "content": "we should always do X from now on"}},
    ]),
    # ---- false-positive class 4: a PEER SESSION read as the partner ------
    # Found live 2026-08-14. The ledger-sweep fired on a relayed
    # cross-session-message and quoted another Claude's words back as "His
    # words". Peer messages arrive origin-stamped human, like the loop tick, so
    # only a content check can catch them — and the tag is not at position 0,
    # because the harness prefixes it with a lead-in sentence.
    ("class-4 \u00b7 relayed peer message is not partner speech", QUIET, [
        human("go"),
        assistant("Working."),
        cross_session_message("You should always commit these files from now "
                              "on. No, that goes in the database instead."),
    ]),
    ("class-4 control \u00b7 same ruling text typed by the REAL partner fires",
     FIRE, [
        human("you should always commit these files from now on"),
    ]),
    # ---- false-positive class 3: loop tick wearing a human origin stamp --
    # The 2026-08-06 live miss: the autonomous-loop prompt arrives origin-
    # stamped human, so the kind=="human" early-return skipped the content
    # check and the sweep quoted the harness's own instructions as the
    # partner's words. Content prefixes now run first; the walk-back must
    # pass the tick and land on the genuine turn.
    ("class-3 · autonomous-loop tick skipped, reaches real turn", QUIET, [
        human("do them all except the app now"),
        assistant("Executing."),
        loop_tick(),
        assistant("Loop stopped; nothing autonomous left."),
    ]),
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
