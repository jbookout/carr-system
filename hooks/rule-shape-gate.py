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
from datetime import datetime, timezone
import os
import re
import sys

LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")

# Asking a session to act with nothing prompting it.
# The (?:s|es|ed|ing)? suffix is not cosmetic. The first draft used \bcapture\b
# and therefore did NOT match "captures" — so it passed rule 21, the ledger rule
# that produced zero entries and motivated this entire gate. Tested against the
# real failures rather than invented examples, which is the only reason it showed.
# DETERMINER GUARD ADDED 2026-08-03. Half this list is noun/verb ambiguous —
# record, report, flag, check, sweep, score, notice, log — and the nouns are
# everywhere in a system whose central object is literally called the RECORD
# LAYER. Unguarded, `\brecords?\b` matched "a row in the record layer" and "the
# records", so the gate fired PROACTIVE on almost every rule written about the
# system it guards, including pure prohibitions like "never store an SSN in the
# record layer".
#
# A noun in English is nearly always preceded by a determiner and a verb is not:
# "the records" is a thing, "the session records it" is an act. Refusing to
# match right after a determiner separates them cheaply and without a parser.
# Deliberately conservative — it can still miss a proactive rule phrased oddly,
# and that is the correct direction to fail, because a gate that warns on
# everything gets clicked past and then catches nothing at all. Same
# alarm-fatigue argument the façade check (rule 28) makes about health checks
# that report every finding at one severity.
PROACTIVE = re.compile(
    r"(?<!\bthe )(?<!\ba )(?<!\ban )(?<!\bits )(?<!\bthis )(?<!\bthat )"
    r"(?<!\bevery )(?<!\ball )(?<!\bany )(?<!\bthose )(?<!\bthese )"
    r"\b(logs?|logged|logging|captures?|captured|capturing|records?|recorded|"
    r"recording|surfaces?|surfaced|surfacing|sweeps?|swept|proposes?|proposed|"
    r"reports?|reported|recites?|recited|states? back|watch(?:es)? for|notices?|"
    r"checks? for|reminds?|raises?|flags?|scores?|reviews? periodically)\b", re.I)

# A moment a session can actually detect.
#
# SECOND PERSON ADDED 2026-08-03. The alternation covered "when the / a / joe /
# dell / he / they" and not "when you", so a rule addressed directly to the
# session — which is the NATURAL voice for a rule a session obeys — read as
# having no trigger at all. It false-positived twice in a row on the escalation
# contract (c20dc3d5), whose three moments are "when you hand a finding upward",
# "when you receive a subagent's finding", "when you are about to act but are
# missing something". Those are as detectable as any moment in this file; the
# regex simply could not see them. A gate that cries wolf on well-shaped rules
# trains the author to click past it, which costs more than the rule it was
# guarding — the same alarm-fatigue argument the façade check (rule 28) already
# makes about health checks that report everything at one severity.
TRIGGER = re.compile(
    r"(\bwhen (the|a|an|joe|dell|he|she|they|any|you|we|your|this|it)\b|"
    r"\bbefore any\b|\bbefore a\b|\bbefore you\b|\bbefore writing\b|"
    r"\beach time\b|\bevery time\b|\bwhenever\b|\bon any\b|\bat the event\b|"
    r"\bthe moment\b|\btriggers?\b|\bfires? on\b|\bboundary sweep)", re.I)

# Something that makes the omission visible.
AUDIT = re.compile(
    r"(\bcount\b|\btally\b|\bstate the\b|\brecit|\bvisible\b|\bsurfaces? in\b|"
    r"\bmonday brief\b|\bzero is\b|\baudit signal\b|\brenders? (in|to)\b)", re.I)

# Constraints bind at the action, so a PURE constraint needs no trigger — and it
# already gets that for free: with no proactive verb in it, assess() returns
# early and never reaches this test.
#
# What this flag actually does is narrower, and the old comment oversold it: a
# rule that BOTH prohibits something AND asks for unprompted work still needs a
# trigger for the second half. "PII may not leave the system; flag it to the
# partner" prohibits cleanly but never says when the flagging fires.
#
# NOT exempting on the constraint word alone, and this was measured rather than
# assumed on 2026-08-03: rule bbffc139 — the canonical proactive rule, the one
# whose failure to fire is why this gate exists at all — contains "never" in a
# mid-sentence clause. A blanket constraint exemption would have waved through
# precisely the rule the gate was built to catch. One stray prohibition word in
# a long statement cannot be allowed to disarm the check.
CONSTRAINT = re.compile(r"\b(never|always|must not|may not|is banned|hard ban|"
                        r"refuses?|forbidden|absolute)\b", re.I)


def log(msg):
    """Timestamped and self-identifying. Before 2026-08-03 no hook stamped its
    lines, so out/hook-guard.log could not answer "when did this fire" or even
    "which gate wrote this" — 51 lines with test fixtures indistinguishable from
    production denials. A log you cannot read chronologically is an artifact,
    not a check."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} rule-shape-gate {msg.rstrip()}\n")
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
