#!/usr/bin/env python3
"""context-handoff-gate.py — hand the work off BEFORE the context runs out.

WHAT JOE ASKED FOR, AND WHAT IS ACTUALLY BUILDABLE. Joe (2026-08-09): "can you
implement a hook that makes you create a new session and pass your task to the
new session when you hit 70% context limit?" Two halves with very different
ceilings on this surface:

  DETECTING 70% — fully automatic, and this file does it.
  OPENING THE NEW SESSION — a hook CANNOT open an interactive session. There is
  no hook event, no CLI flag, and no product API that forks a live conversation
  into a second live one. What exists is the desktop app's background-task chip
  (`spawn_task`): the session mints a chip carrying the full continuation
  prompt, and ONE CLICK opens a fresh session already holding the task. So the
  honest guarantee is:

      NOT "a new session appears on its own"
        — but "the packet is written, the chip is minted, and the new session
          is one click away, every time, without Joe having to notice".

  Anything stronger would be the hardness cosplay conduct-stop-gate.py warns
  about. The click is the whole manual step, stated out loud here so no later
  session mistakes this for full automation.

IT ANNOUNCES, IT NO LONGER REOPENS (2026-08-23, Joe's Stop-gate rationing off
the gates-audit council). It emitted {"decision": "block"}, which forces a whole
extra assistant message. Eleven Stop hooks held that power; one measured shipped
session paid nine such reopens for findings that changed nothing, and reopens
are direct token spend against a standing no-ceremony constraint. Three keep it:
core conduct, completion-evidence, drift-assertion.

THIS IS THE DEMOTION WITH THE THINNEST CASE, and saying so is the point of
writing it down rather than letting a later reader assume it was obvious. The
council's own test asks whether the next message would be the RESULT OF WORK or
a restatement, and here it is genuinely work: the packet, the chip and the push
all get written in the reopened turn. Two things carry it anyway. The band latch
already caps this at one intervention per band per session, so it contributes
almost nothing to the reopen count the rationing is aimed at — and the
announcement arrives at the same moment carrying the same instructions, so the
work is ordered either way; what changes is that a session mid-thought is asked
rather than compelled. REOPEN THIS ONE FIRST if a week of hook telemetry shows
bands crossed with no packet written after them: restoring it is one line, the
`announce(...)` call below going back to
`print(json.dumps({"decision": "block", "reason": ...}))`.

THE LAST MILE, added 2026-08-10 (decision aa6c00fa). The chip renders ONLY in
the desktop app. Joe checked his phone: "Yes it was not visible on my phone."
So the handoff completed exactly when he was already at the machine — the case
where he needs it least — and went silent when he was in the field, which for a
CRE broker is most of the working day. Twenty selftests passed and he found this
in one look, which is the whole argument for testing on the surface the human
actually carries.

  TWO DELIVERY MECHANISMS NOW, because they do DIFFERENT jobs and neither one
  closes the gap alone. Both were checked against the live tool contracts on
  2026-08-10 rather than assumed:

    PushNotification REACHES THE PHONE. It sends a desktop notification and,
    when Remote Control is connected, also pushes to mobile. A live call from
    this machine returned "Mobile push requested", so the mobile route is wired
    on Joe's setup. It REMINDS: it carries no work and it still needs him to
    come back. Note the tool deliberately SKIPS sending when he is actively at
    the terminal, so a "not sent" result is correct behaviour, not a failure.

    A ONE-TIME SCHEDULED TASK REMOVES THE CLICK. create_scheduled_task with a
    `fireAt` timestamp runs once and auto-disables, and a scheduled run starts
    with no human action at all. It BINDS where the chip only offers.

  THE CORRECTION THAT MATTERS, and the reason this file does not claim more
  than it does: a scheduled task does NOT run on a closed machine. Per its own
  tool contract, "scheduled tasks run while this app is open. If the app is
  closed when a task is due, it runs on next launch." So the continuation is
  guaranteed to run WITHOUT A CLICK, but it is not guaranteed to run AT A TIME.
  Calling it "unattended" would be the same overclaim as calling the chip a new
  session. The honest combined guarantee is:

      Joe LEARNS of the handoff wherever he is (push), and the continuation
      RUNS WITHOUT HIM CLICKING ANYTHING (scheduled task) the next time the
      app is open. The chip stays as the instant path when he IS at the desk.

  Three routes, degrading gracefully: chip if he is at the desk, push so he
  knows either way, scheduled task so the work resumes even if he does nothing.

WHY A Stop HOOK AND NOT A CONTEXT EVENT. There is no context-threshold hook
event in this product (verified against the hook-events reference 2026-08-09:
SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PostToolBatch, Stop,
PreCompact, PostCompact, SessionEnd and the rest — none fire on a percentage).
PreCompact is the only context-pressure event and it fires far too late: at
auto-compact the window is already effectively full, which is the exact
situation a handoff is meant to prevent. Stop is the right seam — clean turn
boundary, it can BLOCK, and a blocked Stop's `reason` is obeyed by the model
(live probe documented in conduct-stop-gate.py, re-proven for this file by
ops/context-handoff-gate-selftest.py).

HOW THE PERCENTAGE IS COMPUTED — measured, not estimated. Every assistant row
in the transcript JSONL carries a real `usage` object, and live context size at
that turn is:

    input_tokens + cache_creation_input_tokens + cache_read_input_tokens

Verified live on this machine 2026-08-09 against a real transcript row
(2 + 790 + 149,325 = 150,117). That is the billed prefix, so it is true
occupancy — not a token estimate, not a character heuristic.

THE WINDOW. Opus 5's context window is 1,000,000 tokens (Claude API model
catalog), and that matches this machine: the largest real session under
~/.claude/projects/-Users-booko-My-Drive-CARR-AI is 848,521 tokens, which no
200K-window session could have reached. Both halves matter — the documented
number alone would be a claim about a surface with no test from that surface
(rule 97326357), and the observed peak alone would only prove a lower bound.
Override with CARR_CONTEXT_WINDOW if the model or harness changes. Getting it
WRONG HIGH is the dangerous direction: the gate then fires too late to help.

BANDS, not a single trip. 70% is the prepare-the-handoff line. A session that
sails past it and keeps working needs a second, harder line, or the feature
degrades into a one-time nudge ignored on exactly the long sessions it was
built for. Default bands: 70 (write the packet, mint the chip) and 88 (stop
taking new work; hand over now). Each band fires AT MOST ONCE per session —
state in out/context-handoff-state.json — so no loop is possible.

FAIL-OPEN, ALWAYS. Every failure path exits 0 and lets the turn end. A gate
whose bug can strand Joe mid-session is worse than no gate.
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stop_latch import announce  # noqa: E402
LOG = os.path.join(REPO, "out", "context-handoff.jsonl")
STATE = os.path.join(REPO, "out", "context-handoff-state.json")

# Opus 5 = 1M. Documented in the model catalog AND consistent with the largest
# real session on this machine (848,521 tokens). See module docstring.
WINDOW = int(os.environ.get("CARR_CONTEXT_WINDOW", "1000000"))
BANDS = sorted(
    int(b) for b in os.environ.get("CARR_CONTEXT_BANDS", "70,88").split(",") if b.strip()
)


def dlog(msg):
    if os.environ.get("CARR_GATE_DEBUG"):
        sys.stderr.write(f"[context-handoff-gate] {msg}\n")


def audit(record):
    if record.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def state_path():
    return os.environ.get("CARR_CONTEXT_STATE", STATE)


def load_state():
    try:
        with open(state_path()) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    try:
        path = state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Keep the file from growing forever: only the 50 most recent sessions.
        if len(state) > 50:
            state = dict(list(state.items())[-50:])
        with open(path, "w") as fh:
            json.dump(state, fh, indent=2)
    except Exception:
        pass


def context_tokens(transcript_path):
    """The live context size, from the LAST assistant row that carries usage.

    Reads the whole file rather than tailing it: transcripts are line-delimited
    JSON with no index, the last line is often a user or system row carrying no
    usage at all, and a tail heuristic that guesses wrong reports a context of
    zero — which fails SILENTLY OPEN, the one failure mode that makes this gate
    useless exactly when it matters. A multi-MB read at a turn boundary is
    cheap; a wrong number is not.
    """
    last = None
    try:
        with open(transcript_path, errors="ignore") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                usage = (rec.get("message") or {}).get("usage")
                if not isinstance(usage, dict):
                    continue
                total = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                )
                if total:
                    last = total
    except Exception as exc:
        dlog(f"transcript unreadable: {exc}")
        return None
    return last


def reason_for(band, pct, used):
    head = (
        f"CONTEXT HANDOFF GATE — this session is at {pct:.0f}% of its context "
        f"window ({used:,} of {WINDOW:,} tokens). Crossing {band}% is the "
        f"trigger; it fires once per band per session.\n\n"
    )
    if band >= 88:
        return head + (
            "THIS IS THE HARD LINE. Do not start any new substantive work in "
            "this session. In this same turn:\n\n"
            "1. Invoke the `handoff` skill and produce the packet for EVERYTHING "
            "still open, not just the thread you were last on.\n"
            "2. Land the packet through the record layer (`add-loop` / "
            "`log-decision` as the content warrants) — WRITE LAW 14181e60, "
            "verbs not .md files. If a scratch file is genuinely required by a "
            "machine, say where it went and why.\n"
            "3. Call `spawn_task` with the continuation prompt so Joe gets a "
            "one-click chip that opens the new session already holding the "
            "work. The prompt must stand alone: file paths, record refs, the "
            "decisions already settled, and the single next action.\n"
            "4. Call `PushNotification`. The chip is DESKTOP ONLY and Joe is "
            "usually not at the desk; without this he never learns the handoff "
            "happened. One line, under 200 characters, naming the work in "
            "plain words — not 'handoff ready' but what it is a handoff OF. A "
            "'not sent' result is fine and means he is already at the "
            "terminal reading you.\n"
            "   DISPOSITION FIRST, SUBJECT SECOND — and the ORDER is the rule, "
            "not a preference. Tapping the push opens THIS session, the one "
            "that just ran out of room, not the continuation. So a message "
            "naming only the work lands Joe in a dead end with no way to tell "
            "whether anything happened. Joe, 2026-08-10, on the first live "
            "push: \"i dont know if it did anything.\" A lock screen shows "
            "roughly the first 100 characters and truncates the TAIL, so a "
            "disposition written at the end is the part the phone eats. Open "
            "with one of exactly two stems:\n"
            "     'Nothing needed from you — <what happened>'\n"
            "     'Need your call on <the one thing> — <what happened>'\n"
            "   Then the subject in plain words. Under 100 characters if it "
            "can be done honestly, 200 absolute. If the whole line cannot "
            "survive being cut at 100 characters and still tell Joe whether he "
            "is needed, it is the wrong line.\n"
            "5. Call `create_scheduled_task` with a `fireAt` about ten minutes "
            "out and the SAME self-contained prompt from step 3. This is the "
            "half that removes the click: a scheduled run needs no human "
            "action. It runs only while the app is open, so it is a guarantee "
            "the work RESUMES, never a promise about when. Give it a taskId of "
            "`handoff-continuation-<yyyymmdd-hhmm>` so it cannot collide with "
            "the 18 standing tasks, and say in one line that you created it.\n"
            "6. Close with ONE next action for Joe, in his words (rule "
            "0156e9fa), naming the deal or build in the message itself (rule "
            "c315befa).\n\n"
            "Then stop. Do not re-explain this gate to Joe beyond one line."
        )
    return head + (
        "Prepare the handoff NOW, while there is still room to do it properly. "
        "In this same turn:\n\n"
        "1. Finish or cleanly park the thread you are on — do not abandon it "
        "mid-edit.\n"
        "2. Invoke the `handoff` skill and produce the continuation packet.\n"
        "3. Land it through the record layer (verbs, never a hand-written .md — "
        "WRITE LAW 14181e60).\n"
        "4. Call `spawn_task` with a self-contained continuation prompt so the "
        "next session is one click away for Joe. Include file paths, record "
        "refs, decisions already settled, and the exact next action.\n"
        "5. Call `PushNotification` with one plain-language line naming the "
        "work. The chip renders on the desktop app only, so this is the only "
        "part of the handoff that reaches Joe when he is in the field. A 'not "
        "sent' result means he is at the terminal already and is correct. "
        "LEAD WITH THE DISPOSITION, then the subject — tapping the push opens "
        "THIS session, so a line naming only the subject leaves him unable to "
        "tell whether anything happened, and a lock screen truncates the TAIL "
        "at roughly 100 characters, so a disposition written last is the part "
        "the phone eats. At this band the honest opener is usually 'Nothing "
        "needed from you — packet written, still working' rather than anything "
        "claiming a handoff completed.\n\n"
        "At 70% the scheduled-continuation step is deliberately NOT required — "
        "the packet is insurance and the session usually keeps going, so "
        "queueing a run that duplicates it would be worse than no run. That "
        "step belongs to the 88% band, where the session is actually stopping.\n\n"
        "You may keep working after that if the remaining work is small. The "
        "packet is insurance, not a stop order — but it must exist before the "
        "window closes, because a session that runs out with no packet loses "
        "the thread entirely. Mention the handoff to Joe in one line; do not "
        "lecture him about the gate."
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(no-payload) {exc}")
        sys.exit(0)

    try:
        transcript = payload.get("transcript_path")
        session = payload.get("session_id") or "unknown"
        if not transcript or not os.path.exists(transcript):
            dlog("ALLOW(no transcript)")
            sys.exit(0)

        used = context_tokens(transcript)
        if not used or WINDOW <= 0:
            dlog("ALLOW(no usage rows)")
            sys.exit(0)

        pct = 100.0 * used / WINDOW
        crossed = [b for b in BANDS if pct >= b]
        if not crossed:
            dlog(f"ALLOW {pct:.1f}% ({used:,})")
            sys.exit(0)

        band = max(crossed)
        state = load_state()
        already = int(state.get(session, 0))
        if already >= band:
            dlog(f"ALLOW(band {band} already fired) {pct:.1f}%")
            sys.exit(0)

        state[session] = band
        save_state(state)

        audit({
            "hook": "context-handoff-gate",
            "session": session,
            "band": band,
            "pct": round(pct, 1),
            "used_tokens": used,
            "window": WINDOW,
        })

        dlog(f"ANNOUNCE band={band} {pct:.1f}% ({used:,})")
        announce(reason_for(band, pct, used))
        sys.exit(0)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
