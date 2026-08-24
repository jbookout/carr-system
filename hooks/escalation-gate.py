#!/usr/bin/env python3
"""escalation-gate.py — PreToolUse deny on AskUserQuestion for INTERNAL calls.

THE TOOL-CHANNEL HALF of the conduct gates. conduct-stop-gate.py catches an
offload phrased as free prose, which can only be caught at Stop — after the
words exist. This one catches an offload phrased as a structured question, at
PreToolUse — BEFORE Joe ever sees it. That difference matters: this is the only
one of the two that is genuinely preventive rather than corrective.

RULE aa411351 (taught 2026-08-09) draws the gate BY AUDIENCE, NOT BY DIFFICULTY:

    Joe decides   client-facing · public-facing · money · irreversible
    System decides everything internal — schema, records, renders, jobs, config,
                  rules, refactors, agent/skill design, its own procedure

So a question whose subject is internal is not a question. It is a decision the
session was supposed to make. This hook refuses it and says so.

═══════════════════════════════════════════════════════════════════════════════
THE THING THAT NEARLY BROKE A WORKING SKILL, AND WHY THIS IS NOT A BLANKET DENY
═══════════════════════════════════════════════════════════════════════════════
The review council (Grok + Codex, 2026-08-09) both proposed denying
AskUserQuestion for internal-class calls. Neither knew that AskUserQuestion is
ALREADY LOAD-BEARING for legitimate work: skills/network-debrief/SKILL.md uses
it deliberately as a tap-through for verdicts during a debrief — the vendor
PURSUE/TABLE call, a deal-stage change, an intro Delivery grade — because
tapping an option beats typing, and it "cuts the friction of a debrief roughly
in half on a multi-meeting day."

That is NOT a decision being offloaded. It is FACT CAPTURE: the session is
asking Joe for information that exists only in his head, because he is the one
who was in the room. No amount of research substitutes for it — which is
precisely the test rule aa411351 sets ("research until confident" presumes the
answer is findable). A gate that blocked those would break a working skill and
be switched off within a week, which is the same outcome as never building it.

    ASKING JOE TO DECIDE SOMETHING INTERNAL   -> refuse; he delegated that
    ASKING JOE WHAT HE ALONE KNOWS            -> allow; research cannot reach it

FOUR ALLOW CLASSES, all narrow, none self-granted by the session:
  1. FACT CAPTURE — what happened, what they said, who was there, how it went,
     a grade or verdict on a real-world event. Detected by past-tense/event
     vocabulary about the world rather than about the system.
  2. PROTECTED CLASS — client-facing, public-facing, money, irreversible. His by
     rule; must still reach him.
  3. HE ASKED — one of his own turns, inside a still-open window, requested a
     choice or options (/options, "what are my options", "which would you
     recommend", "ask me", "walk him through"). Read off his keystrokes, never
     assertable by the session. See THE CONSENT WINDOW below.
  4. BOUNDARY CHANGE — anything that weakens a gate, widens permissions, edits
     hooks, or expands what the system may do unattended. See below; this is
     the one place the council overruled Joe's own framing, on purpose.

THE CONSTITUTIONAL CARVE-OUT. Joe's instruction was "internal is yours". Both
council chairs independently, without being asked, refused that at exactly one
point: a change to the boundary ITSELF is internal by subject and
constitutional by effect. Grok: "If the system can 'internally decide' to loosen
its own collar, you do not have a boundary." Codex: "the agent may be the
operator, but it cannot also be the root authority that decides whether its own
actions were allowed." So NARROWING the system's own authority is internal and
free; WIDENING it is Joe's, permanently, and this hook lets that question
through rather than refusing it.

FAILS OPEN on any error, same reasoning as conduct-stop-gate.py: a wedged
session is worse than an unnecessary question.

THE ASYNC SPELLING OF THE SAME OFFLOAD (rule e065aa82, extended 2026-08-14).
AskUserQuestion is the synchronous way to park a decision on Joe; an add-loop
call with marker='decision' (or blocker='ruling') is the asynchronous one — the
❓ surfaces in the Monday brief and waits for a ruling. Same audience test, same
four allow classes, applied to the loop's own text. This deliberately does NOT
touch record-defect or any other loop kind: drift-claim-gate's rule that a
defect must always be filable stands, and a backlog/bell/dated loop is work,
not a parked question. Only the two spellings that literally ask Joe to decide
are classified.

═══════════════════════════════════════════════════════════════════════════════
THE CONSENT WINDOW (allow-class 3), widened 2026-08-23 on Joe's approval
═══════════════════════════════════════════════════════════════════════════════
THE DEFECT IT FIXES, measured by firing this hook against built transcripts
rather than inferred from refusal text. Class 3 used to test HUMAN_WANTS_CHOICE
against Joe's SINGLE most recent turn. In a multi-item interview HE
COMMISSIONED, his commissioning turn is the most recent one only for item 1:

    commission only (item 1)                -> allow
    commission + TAP answers (tool_result)  -> allow, any number of them
    commission + ONE TYPED answer           -> DENY        <- the break

Taps survive because a tap arrives as a `tool_result` block inside a record
whose type is "user", and human_text() keeps only `type == "text"` blocks, so a
tap flattens to "" and is skipped. Joe TYPING is what killed it: one answer, one
aside, one correction became the last turn, matched nothing, and class 3 was
gone for the rest of the run.

That is why network-debrief was genuinely exposed even though its taps worked —
the skill deliberately MIXES the two ("reserve free-text for the open
questions"), so every typed story closed the window on every tap after it.
Measured separately and worth saying plainly: that skill's own verdicts
(PURSUE/TABLE, stage change, A/B/C/F delivery) all classify as FACT CAPTURE on
their own and never needed class 3. The exposure was for taps whose SUBJECT is
internal.

WHAT CHANGED, AND WHAT DID NOT. HUMAN_WANTS_CHOICE gained third-person phrasing
(a separate widening, approved separately — see conduct_patterns.py). The window
changes only the TIME DIMENSION: the grant is read from a bounded window of his
turns instead of the last turn alone.

STRICTLY ADDITIVE BY CONSTRUCTION. At distance 0 the window returns exactly what
the old code returned, with no TTL applied, so no call that was allowed before
is denied now. Every difference is a call that was denied and is now allowed.
That direction is the whole reason this needed Joe rather than a session.

NO STATE FILE, ON PURPOSE, and this is a security argument rather than a
convenience one. A consent token under out/ is one Bash redirect away from the
session minting its own consent. The transcript is written by the harness and is
the only record here a session cannot forge, so the window is re-read from it
every time.

FOUR INDEPENDENT CLOSES, so no single bound has to be trusted alone:
  1. A NON-ANSWER HUMAN TURN. Every intervening turn must be answer-shaped —
     one line, at most ANSWER_MAX_WORDS words. Joe typing a new paragraph of
     instruction ends the interview and the consent with it. Deliberately crude
     and deliberately biased toward closing: a false close costs one
     re-commission, a false open is laundering.
  2. TURN BUDGET — MAX_TURNS of his turns back, not a whole session.
  3. WALL CLOCK — TTL_MINUTES between the grant and now.
  4. READ BUDGET — only the last TAIL_BYTES of transcript is read. Past that the
     window is simply not found, which denies. Failing toward refusal is the
     safe direction for a bound whose only job is to cap PreToolUse latency.

THE RESIDUAL GAP, RECORDED RATHER THAN PAPERED OVER. Inside an open window this
does NOT check that the current ask belongs to the commissioned interview, so a
session running a legitimate 17-item walkthrough could slip one unrelated
internal question in among the items and have it allowed. The four bounds cap
the blast radius; they do not close that. Closing it means fingerprinting the
first licensed ask (its option labels, read from the assistant's own tool_use
records in the same transcript) and requiring later asks to match. Joe was shown
that alternative on 2026-08-23 and chose the window without it; it is buildable
later, statelessly, for the same reason this is.

AUDIT SIGNAL: every fire appends to out/conduct-gate.jsonl, the same ledger the
Stop gate writes, so one count covers both moments.

Fixtures: ops/escalation-gate-selftest.py.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conduct_patterns import PROTECTED, HUMAN_WANTS_CHOICE  # noqa: E402

# ── (1) FACT CAPTURE — only Joe was in the room. Research cannot reach it. ────
FACT_CAPTURE = re.compile(
    r"\b(what happened|how did it go|how'?d it go|what did (he|she|they|it) say"
    r"|did (he|she|they) (say|mention|agree|commit|respond|show|come|call|reply)"
    r"|who (was|were|did) (there|you|attend)|were you|did you (meet|call|visit|tour|talk|speak|see)"
    r"|how many people|when did (he|she|they|you)"
    r"|pursue or table|worth a follow[- ]?up|any good|what'?s your read|your read on"
    r"|grade|rating|rate (him|her|them|the vendor)|deliver(y|ed)"
    r"|stage (change|now|for)|still (active|live|warm|interested)|is (he|she|they) still"
    r"|did (it|that|the deal) (close|sign|die|stall)"
    r"|which of these did you|have you (met|spoken|talked|heard))\b", re.I)

# ── (4) BOUNDARY CHANGE — constitutional; must reach Joe. ────────────────────
BOUNDARY = re.compile(
    r"\b(disable|weaken|loosen|relax|bypass|turn off|switch off|remove|widen|expand"
    r"|opt out of|make .{0,20}optional)\b[^.?]{0,60}"
    r"\b(gate|guard|hook|rule|check|constraint|permission|allowlist|deny list|denylist"
    r"|boundary|approval|escalation|firewall|limit|cap|restriction)\b"
    r"|\b(hook|gate|guard|allowlist|permission|settings\.json|denylist|deny list)\b"
    r"[^.?]{0,50}\b(edit|change|modify|update|add to|grant|escalate|elevate|root|sudo)\b"
    r"|\bgrant (myself|itself|the system|sessions?)\b"
    r"|\bunattended\b[^.?]{0,40}\b(authority|permission|allow|expand)\b", re.I)

# ── INTERNAL SUBJECT — the vocabulary of the system talking about itself. ────
#
# TRAILING `s?` ON EVERY COUNTABLE NOUN, and it is not cosmetic. The first
# version wrote `|loop|` and the selftest caught "Should loops sort by created
# date or by severity?" sailing straight through: `\bloop\b` cannot match
# "loops", because \b after "loop" needs a non-word character and "s" is a word
# character. Every plural in this list was therefore invisible — one silent leak
# per noun. Prefer `s?` (or `\w*`) over a bare singular anywhere in this file.
INTERNAL = re.compile(
    r"\b(schemas?|migrations?|tables?|columns?|indexe?s?|constraints?|triggers?"
    r"|views?|queries|query|sql"
    r"|verbs?|endpoints?|workers?|connectors?|mcp|apis?"
    r"|hooks?|scripts?|modules?|functions?|refactor\w*|renam\w+|repos?|branch\w*"
    r"|commits?|merges?"
    r"|renders?|exporters?|exports?|pipelines?|jobs?|crons?|launchd"
    r"|scheduled tasks?|nightly"
    r"|folders?|director(y|ies)|file (names?|structure|layout)|naming|structure"
    r"|architecture"
    r"|rule stores?|doctrine|loops?|record layer|detectors?|selftests?|fixtures?"
    r"|tests?|migrat\w+"
    r"|configs?|settings?|flags?|env|variables?|caches?|logs?|formats?|layouts?"
    r"|sort order|sort by|ordering|sorting)\b",
    re.I)


# ── THE CONSENT WINDOW'S FOUR DIALS. Named so they can be moved without
# touching logic, and so a later reader sees what was chosen instead of guessing.
MAX_TURNS = 30          # 17 items plus asides — nothing like a whole session
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
    options"` must never read as Joe granting anything, and neither must a file
    the session wrote. Keeping ONLY `type == "text"` blocks is what makes that
    impossible.

    Do not "helpfully" flatten nested content here. A tool_result's content is
    frequently itself a list of text blocks; reaching into it is exactly the
    refactor that would turn tool output into consent. There is a fixture
    pinning this (abuse-grant-via-nested-tool-output).
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
    """An ANSWER to the interview, not a new instruction. One line, few words."""
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

    The first line of the slice is dropped: a byte offset lands mid-record, and
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


def consent_window(records, at=None,
                   max_turns=MAX_TURNS, ttl_minutes=TTL_MINUTES):
    """Did Joe grant a choice inside a still-open window? -> (granted, why).

    `records` is oldest→newest. Walk HIS turns newest→oldest: the first turn
    that grants stops the walk, and anything not answer-shaped stops it first.
    `why` comes back on both paths because a gate that cannot say which bound
    closed the window is a gate nobody can debug at 11pm.
    """
    at = at or datetime.now(timezone.utc)
    turns = [(human_text(r), parse_ts(r)) for r in records]
    turns = [(t, ts) for t, ts in turns if t]

    for distance, (text, ts) in enumerate(reversed(turns)):
        if distance >= max_turns:
            return False, "window_turn_budget"
        if HUMAN_WANTS_CHOICE.search(text):
            # Distance 0 is the OLD behaviour exactly — no TTL — which is what
            # makes this change strictly additive.
            if distance == 0:
                return True, "human_asked_for_choice"
            if ts is None:
                return False, "window_no_timestamp"
            if (at - ts).total_seconds() / 60.0 > ttl_minutes:
                return False, "window_expired"
            return True, "human_asked_for_choice_window"
        if not is_answer_shaped(text):
            return False, "window_closed_by_new_instruction"
    return False, "no_consent_in_window"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  {msg}\n")
    except Exception:
        pass


def audit(record):
    """Shares out/conduct-gate.jsonl with conduct-stop-gate.py, so one count
    covers both moments. FIXTURES DO NOT COUNT — see that file's audit() for
    why: a metric its own test suite inflates is not a metric."""
    if record.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def question_text(tool_input):
    """Flatten every question, header, option label and description into one
    blob. Classification reads the WHOLE call — a session cannot hide an
    internal subject in the option descriptions while keeping the question
    stem neutral."""
    if not isinstance(tool_input, dict):
        return ""
    parts = []
    for q in (tool_input.get("questions") or []):
        if not isinstance(q, dict):
            continue
        parts.append(str(q.get("question", "")))
        parts.append(str(q.get("header", "")))
        for o in (q.get("options") or []):
            if isinstance(o, dict):
                parts.append(str(o.get("label", "")))
                parts.append(str(o.get("description", "")))
    return "\n".join(p for p in parts if p)


def loop_text(tool_input):
    """Flatten the fields of an add-loop call that carry the question. Same
    whole-call principle as question_text: the subject cannot hide in
    blocker_detail while the title stays neutral."""
    if not isinstance(tool_input, dict):
        return ""
    fields = ("title", "body", "unblocks", "source_note", "blocker_detail")
    return "\n".join(str(tool_input.get(f) or "") for f in fields
                     if tool_input.get(f))


def parks_a_decision(tool_input):
    """Only the two spellings that literally await Joe's ruling."""
    if not isinstance(tool_input, dict):
        return False
    return (tool_input.get("marker") == "decision"
            or tool_input.get("blocker") == "ruling")


def classify(blob, consent):
    """Return (allow: bool, why: str).

    `consent` is the (granted, why) pair from consent_window(). A plain STRING
    is still accepted and tested exactly as the old signature did — that is not
    laziness about the migration, it is the diagnostic path: this defect was
    found on 2026-08-23 by importing the module and calling classify() directly
    with a candidate turn, rather than reading refusal text and inferring. Keep
    that call cheap and nobody has to guess again.
    """
    if not blob.strip():
        return True, "empty"
    if isinstance(consent, str):
        consent = (bool(HUMAN_WANTS_CHOICE.search(consent)),
                   "human_asked_for_choice")
    granted, why_granted = consent
    if granted:
        return True, why_granted
    if BOUNDARY.search(blob):
        return True, "boundary_change_is_constitutional"
    if FACT_CAPTURE.search(blob):
        return True, "fact_capture_only_joe_knows"
    if PROTECTED.search(blob):
        return True, "protected_class_is_joes"
    if INTERNAL.search(blob):
        return False, "internal_decision"
    # Neither internal nor protected. Do NOT refuse on an unclassified call —
    # a gate that denies what it does not understand trains sessions to phrase
    # around it, which is the laundering failure the council ranked #2.
    return True, "unclassified_allowed"


REASON = (
    "ESCALATION GATE — refused. This question is an INTERNAL decision, and "
    "internal decisions are yours (rules aa411351 + 14e0408b).\n\n"
    "The gate is drawn by AUDIENCE, not by difficulty. Joe decides "
    "client-facing, public-facing, money and irreversible. Everything internal "
    "— schema, records, renders, jobs, config, rules, refactors, procedure — "
    "you decide and report.\n\n"
    "IF YOU ARE NOT CONFIDENT, THE MOVE IS RESEARCH, NOT ASKING. Read the "
    "actual code. Run the query. Test the surface live. Read the full verb "
    "list. Convene the council. His words: \"if you truly aren't confident in "
    "what decision to make you need to research the problem harder until you "
    "are confident about what to do... you will be more effective at "
    "researching the solutions yourself than relying on me for every single "
    "thing.\"\n\n"
    "DO THIS INSTEAD: pick the smallest reversible option that advances the "
    "objective, execute it, log it with log-decision, and tell Joe in ONE LINE "
    "what you did and why. If it turns out wrong, that is recoverable; his "
    "attention is not.\n\n"
    "This gate does NOT block: asking him what only he knows (what happened in "
    "a meeting, what someone said, a vendor grade), anything client-facing, "
    "public-facing, money or irreversible, or anything that would widen the "
    "system's own authority. Those still reach him.\n\n"
    "NOR does it block an interview HE COMMISSIONED. His own words open a "
    "bounded window and it stays open across his answers, so a multi-item "
    "walkthrough runs to the end instead of dying at item 2. If you are seeing "
    "this during one, the window has closed — he gave a new instruction, or it "
    "ran past its turn or time budget. Ask him to re-commission it; do not "
    "rephrase this question to get around the gate."
)

LOOP_REASON = (
    "ESCALATION GATE — refused. This loop parks an INTERNAL decision on Joe "
    "(rule e065aa82), and internal decisions are yours to make and record.\n\n"
    "marker='decision' and blocker='ruling' both mean the ❓ waits in the "
    "Monday brief for Joe to rule. The gate is drawn by AUDIENCE, not by "
    "difficulty: he decides client-facing, public-facing, money and "
    "irreversible. Everything internal — schema, records, renders, jobs, "
    "config, rules, refactors, procedure — you decide and report.\n\n"
    "DO THIS INSTEAD: research until confident, pick the smallest reversible "
    "option, execute it, record it with log-decision, and tell Joe in ONE "
    "LINE what you did and why. If the work itself must wait on something "
    "real, file the loop with the blocker that names that thing — not "
    "'ruling' for a question you can answer.\n\n"
    "This gate does NOT block: decision loops that are genuinely his "
    "(client-facing, public, money, irreversible, or a boundary widening), "
    "defect filings, or any bell/dated/backlog loop. Those still land."
)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        is_ask = tool == "AskUserQuestion"
        is_loop = tool.startswith("mcp__") and tool.endswith("__add-loop")
        if not (is_ask or is_loop):
            sys.exit(0)

        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        if is_ask:
            blob = question_text(ti)
        else:
            if not parks_a_decision(ti):
                sys.exit(0)
            blob = loop_text(ti)

        # The consent window, for the "he asked" exemption. Best-effort: if the
        # transcript is unreadable we lose one exemption and the other three
        # still apply. The old 200-LINE tail is gone on purpose — 200 lines does
        # not reach back past a couple of tool calls, let alone to the start of
        # a 17-item interview, so the window would have been bounded by an
        # accident of transcript density rather than by any of its four stated
        # bounds. tail_records() reads by bytes instead.
        consent = (False, "no_transcript")
        path = payload.get("transcript_path")
        if path and os.path.exists(path):
            try:
                consent = consent_window(tail_records(path))
            except Exception:
                pass

        allow, why = classify(blob, consent)
        if allow:
            dlog(f"ALLOW({why}) :: {' '.join(blob.split())[:160]}")
            sys.exit(0)

        audit({
            "ts": now(),
            "hook": "escalation-gate",
            "classes": ["internal_ask" if is_ask else "internal_loop_parked"],
            "patterns": [f"escalation:{why}"],
            "session": payload.get("session_id"),
            "excerpt": " ".join(blob.split())[:400],
        })
        dlog(f"DENY({why}) :: {' '.join(blob.split())[:200]}")
        # Exit 2, not JSON. Same reasoning as guard-unattended.py: on any build
        # that does not parse the structured contract, exit 0 reads as ALLOW and
        # the gate fails open silently. Exit 2 blocks everywhere and hands
        # stderr back as the reason.
        print(REASON if is_ask else LOOP_REASON, file=sys.stderr)
        sys.exit(2)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
