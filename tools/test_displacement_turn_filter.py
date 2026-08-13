#!/usr/bin/env python3
"""Guards the partner-turn filter behind displacement baselines 1, 2 and 5.

The filter has now been wrong three times, each time in a way a clean run could
not reveal, so it gets assertions rather than trust. Two directions are tested,
because the third error was wrong in BOTH at once: machine-authored text counted
as the partner typing, and the partner's genuinely short turns discarded.

Run: python3 tools/test_displacement_turn_filter.py
"""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "displacement_baselines", HERE / "displacement-baselines.py"
)
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


def rec(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


# Machine-authored. Every one of these was counted as the partner typing before
# 2026-08-13; the first is the exact shape that produced the 94% contamination.
MACHINE = [
    "Stop hook feedback:\nCONDUCT GATE — this turn is not finished. The session "
    "is being held open, not punished; fix the turn and it will close.",
    "COMPLETION EVIDENCE GATE — terminal completion claim has no fresh verification.",
    "DELEGATION TRIPWIRE — second mechanical tool call this turn.",
    "CONTEXT HANDOFF GATE — this session is at 72% of its context window.",
    "The previous response failed to produce a valid tool call. Please retry the "
    "tool call now.",
    "[Request interrupted by user]",
    "[Request interrupted by user for tool use]",
    "[Image: original 1920x2004, displayed at 1916x2000.]",
    "Base directory for this skill: /private/tmp/bundled-skills/dataviz",
    "# Update Config Skill Modify Claude Code configuration by updating settings.",
    "Approach this as the design lead at a small studio known for their versatility.",
    "Continue from where you left off.",
]

# The partner actually typing. The short ones are the regression guard: a
# twelve-character floor silently deleted every one of them, including the exact
# turn that authorised this fix.
HUMAN = [
    "lets do it",
    "go ahead",
    "yes",
    "start the phase 1 roadmap",
    "those loose changes are from another session, leave them",
    "well, phase 0 is still being working on in the roadmap continued session",
    "why is the deal room showing stale data?",
    # Real turns eaten by an IGNORECASE draft of the shouted-gate pattern: any
    # turn starting with a word that ENDS in "gate". All five occurred in the
    # corpus and all five are the partner's own words.
    "investigate the lead board",
    "navigate to the deal room",
    "propagate the change to Dell's copy",
    "yea loosen the gate",
    "commite the gate",
]

# The partner typing AFTER an injected block — the case the second fix existed to
# protect. It must keep working: strip the injected block, keep the human's words.
#
# The injected block that actually gets PREPENDED to a human message is a
# system-reminder or SYSTEM NOTIFICATION, not a gate. Measured against the corpus
# on 2026-08-13: of 156 conduct/completion gate turns, ZERO carried any text after
# the gate body, so a gate turn is always wholly machine-authored and vetoing it
# outright cannot eat a human's words. An earlier draft of this fix asserted a
# gate-plus-human shape that does not occur, and this test caught it.
MIXED = [
    (
        "<system-reminder>Recalled memory: prefer Word format.</system-reminder>\n\n"
        "actually just ship it",
        "actually just ship it",
    ),
]

failures = []

for text in MACHINE:
    got = db.partner_text(rec(text))
    if got:
        failures.append("MACHINE TEXT COUNTED AS PARTNER: %r -> %r" % (text[:60], got[:60]))

for text in HUMAN:
    if not db.partner_text(rec(text)):
        failures.append("PARTNER TURN DISCARDED: %r" % text[:60])

for text, expected_tail in MIXED:
    got = db.partner_text(rec(text))
    if expected_tail not in got:
        failures.append(
            "INJECTED-PREFIX TURN LOST ITS HUMAN TAIL: %r -> %r" % (text[:60], got[:60])
        )

total = len(MACHINE) + len(HUMAN) + len(MIXED)
if failures:
    print("FAIL  %d of %d" % (len(failures), total))
    for f in failures:
        print("  " + f)
    sys.exit(1)

print("ok  %d/%d  partner-turn filter rejects machine text and keeps short human turns" % (total, total))
