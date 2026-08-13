#!/usr/bin/env python3
"""Phase 0 displacement baselines 1, 2, 3 and 5, measured from local session records.

Doctrine (carr-workspace-bduf s01a) requires these be INSTRUMENTED OR INTERVIEWED
and never invented, and that each carries its evidence method, observation window,
result, bound product action and post-launch comparison. Measure 4 was captured
separately by timed tasks because it measures human comprehension time. These four
are mechanical and need nothing from the partner.

WHAT THIS DOES AND DOES NOT CLAIM
---------------------------------
Everything here is counted, not judged. Where a measure's doctrine wording needs a
judgment call the script reports the mechanical substrate and says so plainly
rather than guessing a number. Doctrine objective 8 explicitly permits heavy
sessions for exploring, investigating genuinely complex problems, and constructing
something new, so total session time is NOT the baseline — only routine-operation
time is, and separating those two is the judgment this script deliberately leaves
open rather than faking.

ACTIVE TIME, NOT WALL CLOCK
---------------------------
A session's first-to-last timestamp span is worthless: a window left open
overnight would read as sixteen hours. Active time is the sum of gaps between
consecutive records within one session, with any gap longer than IDLE_CAP treated
as the partner having walked away and contributing nothing. This undercounts
thinking time spent staring at a screen and overcounts nothing, so it is the
conservative direction.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
JOE_PROJECT = "-Users-booko-My-Drive-CARR-AI"  # the CARR business project
WINDOW_DAYS = 28
IDLE_CAP = timedelta(minutes=10)

NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(days=WINDOW_DAYS)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def text_of(record):
    """Best-effort plain text of a record, for counting shapes only.

    Never printed. Used to classify a turn's SHAPE (is it a question, does it name
    a verb) and then discarded, so no client content leaves this process.
    """
    msg = record.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
            return "\n".join(parts)
    content = record.get("content")
    return content if isinstance(content, str) else ""


def tool_names(record):
    msg = record.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [
        b.get("name")
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")
    ]


def load_sessions():
    """Return {session_id: [records]} for Joe's CARR project inside the window."""
    root = PROJECTS / JOE_PROJECT
    if not root.exists():
        sys.exit(f"project directory not found: {root}")

    sessions = defaultdict(list)
    files = sorted(root.glob("*.jsonl"))
    for path in files:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = parse_ts(rec.get("timestamp"))
                    if ts is None or ts < CUTOFF:
                        continue
                    sid = rec.get("sessionId") or path.stem
                    rec["_ts"] = ts
                    sessions[sid].append(rec)
        except OSError:
            continue
    for recs in sessions.values():
        recs.sort(key=lambda r: r["_ts"])
    return sessions, len(files)


def active_intervals(records):
    """Active stretches within one session, as (start, end) pairs.

    Returned as intervals rather than a scalar because summing per-session totals
    DOUBLE COUNTS: sessions run concurrently — subagents, background tasks, more
    than one window — and the first version of this script summed them, producing
    87 active hours per week for one person, which is over twelve hours a day
    every day. Wall clock cannot be added up; it has to be unioned.
    """
    spans = []
    for earlier, later in zip(records, records[1:]):
        gap = later["_ts"] - earlier["_ts"]
        if timedelta(0) <= gap <= IDLE_CAP:
            spans.append((earlier["_ts"], later["_ts"]))
    return spans


def union_seconds(spans):
    """Total wall-clock covered by a set of possibly overlapping intervals."""
    if not spans:
        return 0.0
    spans = sorted(spans)
    total = 0.0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start > cur_end:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    total += (cur_end - cur_start).total_seconds()
    return total


def is_typed_prompt(record):
    """True when this looks like the partner actually typing, not machinery.

    Excludes tool results, SDK-driven prompts (subagent and scripted traffic), and
    the hook/system feedback that arrives wearing a user role. This is a filter on
    SHAPE, never on content, and it is imperfect — it is reported as a floor
    rather than an exact count.
    """
    if record.get("type") != "user":
        return False
    if record.get("isSidechain"):
        return False
    if record.get("promptSource") == "sdk":
        return False
    msg = record.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), list):
        if any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in msg["content"]
        ):
            return False
    return bool(partner_text(record))


# Hook feedback, system reminders and harness notifications are PREPENDED to the
# partner's own message rather than arriving as separate turns. An earlier version
# discarded any turn beginning with one, which threw away 90 real turns in a
# 60-file sample and drove the partner-turn count down to an impossible 46. Strip
# the injected block; keep whatever the human actually typed after it.
INJECTED = re.compile(
    r"^(?:\s*(?:<[^>]+>[\s\S]*?</[^>]+>"
    r"|Stop hook[^\n]*\n?"
    r"|PreToolUse[^\n]*\n?"
    r"|PostToolUse[^\n]*\n?"
    r"|\[SYSTEM NOTIFICATION[\s\S]*?\n\n"
    r"|Caveat: The messages below[^\n]*\n?"
    r"))+"
)

MIN_TYPED = 12  # shorter than this is an acknowledgement fragment, not a turn


def partner_text(record):
    """What the human actually typed in this record, or empty string."""
    body = text_of(record)
    if not body.strip():
        return ""
    tail = INJECTED.sub("", body).strip()
    return tail if len(tail) >= MIN_TYPED else ""


# A question shape, not a topic. Deliberately crude and stated as such: it counts
# turns that ASK rather than instruct, which is the substrate for measure 2.
QUESTION = re.compile(
    r"(\?|^\s*(what|where|when|why|how|which|who|is|are|do|does|did|can|could|should|would|will)\b)",
    re.IGNORECASE | re.MULTILINE,
)

# Phrases that mark a turn as asking the SYSTEM to explain its own state or
# behaviour, which is what measure 2 is actually about.
EXPLAIN = re.compile(
    r"\b(explain|what does|what is|why did|why is|how do(es)? (it|this|the)|"
    r"what happened|show me|status|where (is|are|did)|did (it|that|you)|"
    r"is (it|this|that) (working|running|done|correct)|what changed)\b",
    re.IGNORECASE,
)

# Marks of context being carried by hand between sessions rather than by record.
HANDOFF = re.compile(
    r"\b(handoff|hand off|catch ?me ?up|continue (from|where)|as (i|we) (said|mentioned)|"
    r"last (session|time)|previous session|earlier (you|we|i)|"
    r"picking (this |it )?back up|resume|carry over|remind you)\b",
    re.IGNORECASE,
)


def main():
    sessions, file_count = load_sessions()
    if not sessions:
        sys.exit("no session records inside the window")

    all_ts = [r["_ts"] for recs in sessions.values() for r in recs]
    span_start, span_end = min(all_ts), max(all_ts)

    # ---- measure 1 substrate: active time -------------------------------
    per_session_spans = {sid: active_intervals(recs) for sid, recs in sessions.items()}
    per_session_secs = {sid: union_seconds(sp) for sid, sp in per_session_spans.items()}
    summed_secs = sum(per_session_secs.values())
    all_spans = [sp for spans in per_session_spans.values() for sp in spans]
    total_secs = union_seconds(all_spans)
    partner_sessions = {
        sid for sid, recs in sessions.items() if any(is_typed_prompt(r) for r in recs)
    }
    partner_spans = [sp for sid in partner_sessions for sp in per_session_spans[sid]]
    partner_secs = union_seconds(partner_spans)
    observed_days = max((span_end - span_start).total_seconds() / 86400, 1)
    weeks = observed_days / 7

    # ---- measure 2 substrate: questions asked of the system --------------
    user_turns = 0
    question_turns = 0
    explain_turns = 0
    for recs in sessions.values():
        for r in recs:
            if r.get("type") != "user":
                continue
            body = partner_text(r)
            if not body:
                continue
            # Tool results arrive as user-role records; they are not the partner
            # asking anything, so they must not inflate this count.
            msg = r.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                if any(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in msg["content"]
                ):
                    continue
            if not is_typed_prompt(r):
                continue
            user_turns += 1
            if QUESTION.search(body):
                question_turns += 1
                if EXPLAIN.search(body):
                    explain_turns += 1

    # ---- measure 3 substrate: actions that needed a session ---------------
    verbs = Counter()
    tools = Counter()
    for recs in sessions.values():
        for r in recs:
            for name in tool_names(r):
                tools[name] += 1
                if "__" in name:  # record-layer verbs arrive as mcp__<server>__<verb>
                    verbs[name.rsplit("__", 1)[-1]] += 1

    # ---- measure 5 substrate: hand-carried context ------------------------
    handoff_turns = 0
    for recs in sessions.values():
        for r in recs:
            if not is_typed_prompt(r):
                continue
            if HANDOFF.search(partner_text(r)):
                handoff_turns += 1

    out = {
        "window": {
            "requested_days": WINDOW_DAYS,
            "actual_span_days": round(observed_days, 2),
            "first_record": span_start.isoformat(),
            "last_record": span_end.isoformat(),
            "note": (
                "The transcript corpus begins 2026-07-16, so a longer window is not "
                "measurable. Rates below are per-week over the ACTUAL span, not "
                "extrapolated."
            ),
        },
        "corpus": {
            "transcript_files_in_project": file_count,
            "sessions_in_window": len(sessions),
            "records_in_window": sum(len(r) for r in sessions.values()),
        },
        "measure_1_weekly_heavy_session_time": {
            "method": "active time as a UNION of inter-record gaps <= 10 minutes, "
            "restricted to sessions the partner actually typed in",
            "partner_active_hours": round(partner_secs / 3600, 1),
            "weekly_partner_active_hours": round((partner_secs / 3600) / weeks, 1),
            "all_session_union_hours": round(total_secs / 3600, 1),
            "naive_summed_hours_DO_NOT_USE": round(summed_secs / 3600, 1),
            "double_counting_factor": (
                round(summed_secs / total_secs, 2) if total_secs else None
            ),
            "sessions_total": len(sessions),
            "sessions_with_a_typed_prompt": len(partner_sessions),
            "median_session_minutes": round(
                sorted(per_session_secs.values())[len(per_session_secs) // 2] / 60, 1
            ),
            "JUDGMENT_STILL_REQUIRED": (
                "This is TOTAL active time. The baseline doctrine asks for ROUTINE "
                "OPERATION time only, because objective 8 permits heavy sessions for "
                "exploration, investigation and construction. Splitting the two is a "
                "judgment call this script will not fake."
            ),
        },
        "measure_2_questions_needing_agent_explanation": {
            "method": "user turns matching a question shape, excluding tool results",
            "partner_turns": user_turns,
            "question_turns": question_turns,
            "questions_asking_the_system_to_explain_itself": explain_turns,
            "weekly_explain_questions": round(explain_turns / weeks, 1),
            "share_of_turns_that_are_questions": (
                round(question_turns / user_turns, 3) if user_turns else None
            ),
        },
        "measure_3_actions_not_completable_visually": {
            "method": "record-layer verb invocations; every one is an action that "
            "required an AI session because no visual control exists for it",
            "distinct_verbs_used": len(verbs),
            "total_verb_calls": sum(verbs.values()),
            "weekly_verb_calls": round(sum(verbs.values()) / weeks, 1),
            "top_20_verbs": verbs.most_common(20),
            "CONFIRMATION_REQUIRED": (
                "Joe confirms which of these genuinely had no visual route. A verb "
                "called by a session on its own initiative is not the same as an "
                "action he could not otherwise perform."
            ),
        },
        "measure_5_hand_carried_context_transfers": {
            "method": "sessions in window, plus partner turns carrying context by hand",
            "sessions_total": len(sessions),
            "sessions_with_a_typed_prompt": len(partner_sessions),
            "weekly_partner_sessions": round(len(partner_sessions) / weeks, 1),
            "turns_restating_prior_context": handoff_turns,
            "weekly_hand_carried_turns": round(handoff_turns / weeks, 1),
            "note": (
                "Each session boundary is a potential transfer; a turn matching the "
                "handoff shape is one that actually carried context by hand."
            ),
        },
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
