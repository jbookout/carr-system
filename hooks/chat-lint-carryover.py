#!/usr/bin/env python3
"""
chat-lint-carryover.py — deliver the chat lint's findings BEFORE the next reply
is written, instead of blocking the last one after Joe has already read it.

WHY THIS EXISTS. Joe, 2026-08-16: "why does every claude session now repeat its
responses twice? bc all its doing is chewing up my tokens." He was right, and
his follow-up named the fix: "what if we change the gate so that it doesn't
trigger a restate in the first place."

THE MECHANISM, and its hard ceiling. There is no PreAssistantMessage deny in
Claude Code — hooks/conduct-stop-gate.py documents this and it was live-probed
on this machine 2026-08-09. A reply cannot be caught before Joe sees it. The
only lever is a Stop hook returning {"decision":"block"}, and a block by
definition forces ANOTHER assistant message. So for a WORDING violation the
trade is bad: the banned word already reached him, it cannot be unsent, and
blocking buys nothing except a second copy of a message he owns.

THE SPLIT THIS ENFORCES, which is the real rule:
  - A gate catching UNDONE WORK (a command handed over instead of run, a
    verification never performed) SHOULD block. The next message is the result
    of work, not a restatement, and blocking is the only thing that gets the
    work done. That is hooks/conduct-stop-gate.py and it still blocks.
  - A gate catching WORDING only should NOT block. It records the finding and
    this hook injects it as context on the next UserPromptSubmit, so the very
    next reply is written correctly. Cost: a few lines of context, once, and
    only when there was a violation. No extra message, ever.

FAIL-OPEN, always. A missing note, an unreadable file, a bad path — all exit 0
silently. A writing-style reminder is never worth breaking a turn over.
"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARRY_DIR = os.path.join(REPO, "out", "chat-lint-carry")


def carry_path(session):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "unknown"))[:64]
    return os.path.join(CARRY_DIR, f"{safe}.txt")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        path = carry_path(payload.get("session_id"))
        if not os.path.exists(path):
            return 0
        with open(path) as fh:
            note = fh.read().strip()
        # Consume it either way: a note that survives its delivery would be
        # re-injected on every following turn.
        try:
            os.remove(path)
        except Exception:
            pass
        if not note:
            return 0
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": note,
            }
        }))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
