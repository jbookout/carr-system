#!/usr/bin/env python3
"""rule-shape-gate.py — catch an aspirational rule at the moment it is written.

WHY THIS EXISTS. On 2026-08-02 the same defect was found three times in one
session, in three different costumes:

  · Joe's development kit (rules 21-23) had produced ZERO entries since it was
    enacted. It said "when Joe makes a major call, log it" — a judgment about
    his speech, made mid-task, with nothing to notice it.
  · The CEO rule (75c2e4c9) failed within HOURS of being activated. It said
    "the main session verifies and decides." Findings still died on the desk.
  · The writing-lint gate depended on a session remembering to run it.

Each was fixed the same way: name the detectable events that fire it, and give
it a count the human can see. Each fix was itself written as prose in a rule,
which is the joke — the correction shares the shape of the defect.

WHAT IT CHECKS, AND THE DISTINCTION THAT MATTERS. Not every rule needs a
trigger. CONSTRAINT rules ("never store 205-643-6555", "LOIs go out in Word")
bind at the moment of the action they govern, so the action itself is the
trigger and they fire on their own. PROACTIVE rules ask a session to DO
something no request prompted — log, sweep, surface, propose, report, recite.
Nothing notices the moment for those, so without an explicit trigger they are
aspirations wearing rule syntax. This gate flags the second class only.

Two properties it looks for in a proactive rule:
  1. A TRIGGER — a detectable moment. "when the partner corrects you", "before
     any commit", "when a subagent returns". Not "when it matters".
  2. An AUDIT SIGNAL — something that makes NOT doing it visible. A count
     stated back, a tally in the brief, a render that shows zero. Taught rules
     survive because the session recites them; the ledgers died because nobody
     could see the silence.

IT WARNS, IT NEVER BLOCKS. A rule that trips this may still be correct — some
proactive rules are genuinely hard to trigger, and the honest move is to write
it anyway and know it is fragile. Blocking would also make `teach` refuse the
partner's own words, which is the one thing the verb must never do. The warning
goes back as context so the session can fix the shape before activating, which
is the cheap moment.

FAILS OPEN on any error, like every other hook here. Logged to out/hook-guard.log.
"""

import json
import os
import re
import sys

LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")

# Asking a session to act with nothing prompting it.
# The (?:s|es|ed|ing)? suffix is not cosmetic. The first draft used \bcapture\b
# and therefore did NOT match "captures" — so it passed rule 21, the ledger rule
# that produced zero entries and motivated this entire gate. Tested against the
# real failures rather than invented examples, which is the only reason it showed.
PROACTIVE = re.compile(
    r"\b(logs?|logged|logging|captures?|captured|capturing|records?|recorded|"
    r"recording|surfaces?|surfaced|surfacing|sweeps?|swept|proposes?|proposed|"
    r"reports?|reported|recites?|recited|states? back|watch(?:es)? for|notices?|"
    r"checks? for|reminds?|raises?|flags?|scores?|reviews? periodically)\b", re.I)

# A moment a session can actually detect.
TRIGGER = re.compile(
    r"(\bwhen (the|a|an|joe|dell|he|she|they|any)\b|\bbefore any\b|\bbefore a\b|"
    r"\bbefore writing\b|\beach time\b|\bevery time\b|\bwhenever\b|\bon any\b|"
    r"\bat the event\b|\bthe moment\b|\btriggers?\b|\bfires? on\b|\bboundary sweep)", re.I)

# Something that makes the omission visible.
AUDIT = re.compile(
    r"(\bcount\b|\btally\b|\bstate the\b|\brecit|\bvisible\b|\bsurfaces? in\b|"
    r"\bmonday brief\b|\bzero is\b|\baudit signal\b|\brenders? (in|to)\b)", re.I)

# Constraints bind at the action; they do not need a trigger.
CONSTRAINT = re.compile(r"\b(never|always|must not|may not|is banned|hard ban|"
                        r"refuses?|forbidden|absolute)\b", re.I)


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(msg.rstrip() + "\n")
    except Exception:
        pass


def assess(stmt):
    """Return a warning string, or None when the shape is fine."""
    proactive = bool(PROACTIVE.search(stmt))
    if not proactive:
        return None
    has_trigger = bool(TRIGGER.search(stmt))
    has_audit = bool(AUDIT.search(stmt))
    constraint = bool(CONSTRAINT.search(stmt))
    if has_trigger and has_audit:
        return None
    # A pure constraint that also mentions logging is usually fine.
    if constraint and has_trigger:
        return None

    missing = []
    if not has_trigger:
        missing.append(
            "NO DETECTABLE TRIGGER. It asks a session to act, but names no moment that "
            "fires it. Rules 21-23 read 'when Joe makes a major call' and produced zero "
            "entries in the weeks they were active, because that is a judgment made "
            "mid-task, not an event. Name the moments: he corrects you, he picks between "
            "options, a subagent returns, before any commit, before a handoff, he changes "
            "subject.")
    if not has_audit:
        missing.append(
            "NO AUDIT SIGNAL. Nothing makes NOT doing it visible. Taught rules survive "
            "because the session recites their count at start and the partner can see a "
            "wrong number; the personal ledgers died silently because zero entries and "
            "zero events look identical. Give it a count, a tally, or a render where a "
            "gap shows.")

    return ("RULE SHAPE WARNING — this reads PROACTIVE (it asks a session to do something "
            "nothing requested), and proactive rules without both halves have failed every "
            "time in this system: the CEO rule failed within hours of activation, and the "
            "development-kit ledgers produced nothing at all.\n\n"
            + "\n\n".join(missing)
            + "\n\nCONSTRAINT rules are exempt and need no trigger: 'never store that number' "
              "binds at the moment of the action. This is only about rules that ask for "
              "unprompted work. Fix the shape before activate-rule, or activate it knowing "
              "it is fragile and say so.")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) rule-shape {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if not tool.endswith("__teach"):
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        stmt = (ti or {}).get("statement", "") if isinstance(ti, dict) else ""
        if not stmt or len(stmt) < 40:
            sys.exit(0)

        warning = assess(stmt)
        if warning:
            log(f"SHAPE-WARN :: {stmt[:160]}")
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "additionalContext": warning,
                }
            }))
        sys.exit(0)
    except Exception as exc:
        log(f"ALLOW(internal-error) rule-shape {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
