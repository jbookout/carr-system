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
            "4. Close with ONE next action for Joe, in his words (rule "
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
        "refs, decisions already settled, and the exact next action.\n\n"
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

        dlog(f"BLOCK band={band} {pct:.1f}% ({used:,})")
        print(json.dumps({"decision": "block", "reason": reason_for(band, pct, used)}))
        sys.exit(0)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
