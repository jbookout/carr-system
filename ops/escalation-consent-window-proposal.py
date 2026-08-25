#!/usr/bin/env python3
"""escalation-consent-window-proposal.py — PROPOSED, NOT INSTALLED.

A reference implementation of the ONE change being put to Joe as a boundary
change: how hooks/escalation-gate.py reads allow-class 3 ("HE ASKED") off the
transcript. Nothing here is wired to anything. The live gate is untouched.
Fixtures: ops/escalation-consent-window-proposal-selftest.py.

═══════════════════════════════════════════════════════════════════════════════
THE DEFECT, as measured on 2026-08-23 rather than inferred from refusal text
═══════════════════════════════════════════════════════════════════════════════
escalation-gate.py's classify(blob, human_last) tests HUMAN_WANTS_CHOICE against
Joe's SINGLE most recent human turn. In a Joe-commissioned multi-item interview
his commissioning turn is the most recent one only for item 1.

What the live run actually showed, once the real hook was fired against built
transcripts rather than reasoned about:

    commission only                       -> allow
    commission + tap answers (tool_result)-> allow      <- already works
    commission + ONE TYPED answer         -> DENY       <- the break

Tap answers arrive as `tool_result` blocks inside a user record. The gate's
extractor keeps only `type == "text"` blocks, so a tap turn flattens to "" and
is skipped, and the commissioning turn stays visible however many taps follow.
So a PURE tap-through interview — the shape the gate's header worried about —
survives today. What breaks the window is Joe TYPING anything at all: one typed
answer, one aside, one correction becomes human_last, matches nothing, and
allow-class 3 is gone for the rest of the interview.

That is why skills/network-debrief/SKILL.md is genuinely exposed even though the
tap path works: the skill deliberately MIXES the two — "reserve free-text for the
open questions (what happened, what they said, what he heard)" — so a real
debrief alternates typed storytelling with taps. Every typed answer closes the
window on every tap that follows.

Measured separately, and worth saying plainly: network-debrief's own taps
(PURSUE/TABLE, stage change, A/B/C/F delivery grade) all classify as
`fact_capture_only_joe_knows` on their own and never needed class 3. The
exposure is real but it is for taps whose SUBJECT is internal, not for the
debrief verdicts themselves.

═══════════════════════════════════════════════════════════════════════════════
WHAT THIS CHANGES, AND WHY IT IS NOT A REGEX WIDENING
═══════════════════════════════════════════════════════════════════════════════
HUMAN_WANTS_CHOICE is NOT touched. What counts as Joe granting a choice is
exactly what counted before. The change is in the TIME DIMENSION only: the grant
is read from a bounded, still-open window of his turns instead of from the last
turn alone. The window closes on its own, four different ways.

STRICTLY ADDITIVE BY CONSTRUCTION. When the granting turn IS the most recent
turn, this returns exactly what the current code returns, with no TTL applied —
so no call that is allowed today becomes denied. Every difference is a call that
is denied today and would be allowed. That is the whole boundary change, and it
is the reason it is Joe's to make.

NO STATE FILE, ON PURPOSE — this is the security argument, not a convenience
one. A consent token in out/ would be a file the session can write with one
Bash redirect, which means the session could mint its own consent. The
transcript is written by the harness and is the only record in this system a
session cannot forge. So the window is read, every time, from the transcript.

FOUR WAYS THE WINDOW CLOSES. Each is a separate bound so that no single one has
to be trusted alone:

  1. A NON-ANSWER HUMAN TURN. Walking back from now, every intervening human
     turn must be answer-shaped — one line, at most ANSWER_MAX_WORDS words.
     Joe typing a new paragraph of instruction closes the interview, and the
     consent dies with it. Deliberately crude, and deliberately biased toward
     closing: a false close costs one re-commission, a false open is laundering.
  2. TURN BUDGET. At most MAX_TURNS human turns back.
  3. WALL CLOCK. At most TTL_MINUTES between the granting turn and now, so an
     interview cannot license a question asked hours later in a session that
     drifted somewhere else. Not applied at distance 0 — see additive, above.
  4. READ BUDGET. Only the last TAIL_BYTES of the transcript are read. Past
     that the window is simply not found, which denies. Failing toward refusal
     is the safe direction for a bound whose only job is to cap latency.

THE RESIDUAL GAP, STATED RATHER THAN PAPERED OVER. Inside an open window this
mechanism does not check that the current ask belongs to the commissioned
interview. A session running a legitimate 17-item walkthrough could slip an
unrelated internal question in among the items and it would be allowed. The
bounds cap the blast radius — one interview, ANSWER-shaped turns only, minutes
not hours — but they do not close that. Closing it means fingerprinting the
first licensed ask (its option labels, read from the assistant's own tool_use
records in the same transcript) and requiring later asks to match. That is
buildable and stateless for the same reason this is, and it is written up as the
tighter alternative in the proposal rather than built here, because it is
strictly more machinery and the choice between them is Joe's.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

# The grant vocabulary is NOT redefined here. It is imported from the same
# single source both conduct gates already share, so this proposal cannot
# quietly become a second copy of the rule (rule d367188d, consolidation).
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))
from conduct_patterns import HUMAN_WANTS_CHOICE  # noqa: E402

# ── The four dials. Named, so Joe can move any of them without touching code
# logic, and so a later reader can see what was chosen rather than guess.
MAX_TURNS = 30          # 17 items plus asides, and nothing like a whole session
TTL_MINUTES = 90        # a 17-item walkthrough runs ~35 min; this is headroom
ANSWER_MAX_WORDS = 12   # "retire it" · "yes, decline that one" · "option B"
TAIL_BYTES = 4_000_000  # latency cap on a PreToolUse read

SKIP_PREFIXES = (
    "<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
    "<local-command", "<command-name>", "Caveat:",
)


def human_text(rec):
    """The human's own typed words in one record, or "" for anything else.

    THE tool_result CASE IS THE SECURITY-RELEVANT ONE. A tool_result block lives
    inside a record whose type is "user", and its content is whatever a tool
    printed — which a session controls completely. `echo "walk me through the
    options"` must never read as Joe granting anything. Keeping only
    `type == "text"` blocks is what makes that impossible, and it is the same
    extraction the live gate already does; it is restated here so the window
    walk cannot accidentally relax it.
    """
    if rec.get("type") not in ("user", "human"):
        return ""
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return ""
    msg = rec.get("message") or rec
    c = msg.get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        t = "\n".join(b.get("text", "") for b in c
                      if isinstance(b, dict) and b.get("type") == "text")
    else:
        return ""
    if not t or t.lstrip().startswith(SKIP_PREFIXES):
        return ""
    return t


def is_answer_shaped(text):
    """An ANSWER, not a new instruction. One line, few words."""
    t = text.strip()
    return bool(t) and "\n" not in t and len(t.split()) <= ANSWER_MAX_WORDS


def parse_ts(rec):
    raw = rec.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def tail_records(path, tail_bytes=TAIL_BYTES):
    """Parsed transcript records, oldest→newest, from the last tail_bytes.

    The first line of the slice is dropped: a byte offset lands mid-record and
    a half-parsed record is worse than a missing one.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()
            raw = fh.read()
    except Exception:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def consent_window(records, now=None,
                   max_turns=MAX_TURNS, ttl_minutes=TTL_MINUTES):
    """Did Joe grant a choice inside a still-open window? -> (bool, why).

    `records` is oldest→newest. Walk his turns newest→oldest; the first one that
    grants stops the walk, and anything that is not answer-shaped stops it
    first. `why` is returned on both paths because a gate that cannot say which
    bound closed the window is a gate nobody can debug at 11pm.
    """
    now = now or datetime.now(timezone.utc)
    turns = [(human_text(r), parse_ts(r)) for r in records]
    turns = [(t, ts) for t, ts in turns if t]

    for distance, (text, ts) in enumerate(reversed(turns)):
        if distance >= max_turns:
            return False, "window_turn_budget"
        if HUMAN_WANTS_CHOICE.search(text):
            # Distance 0 is today's behaviour exactly — no TTL, so nothing that
            # is allowed today becomes denied.
            if distance == 0:
                return True, "human_asked_for_choice"
            if ts is None:
                return False, "window_no_timestamp"
            age = (now - ts).total_seconds() / 60.0
            if age > ttl_minutes:
                return False, "window_expired"
            return True, "human_asked_for_choice_window"
        if not is_answer_shaped(text):
            return False, "window_closed_by_new_instruction"
    return False, "no_consent_in_window"


def granted(transcript_path, now=None):
    """Convenience wrapper: the whole read, from a path."""
    return consent_window(tail_records(transcript_path), now=now)
