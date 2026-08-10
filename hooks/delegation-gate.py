#!/usr/bin/env python3
"""Block the second inline mechanical tool call until an executor is named.

The delegation rules used to be advisory prose.  That failed on 2026-08-10:
Joe had explicitly asked for lower-model subagents, the work changed phase when
Salesforce became available, and the main seat silently reclaimed a repetitive
browser sweep.  Nothing in the runtime represented Joe's instruction as a
task-level latch and no hook watched the rule's own "second tool call" trigger.

This gate covers local Claude Code, the surface with PreToolUse hooks.  It is
deliberately narrow:

* only CARR sessions (the vault or carr-system repo);
* only the main transcript (subagent transcripts live under /subagents/);
* only mechanical tools; and
* only the second call after the partner's latest genuine turn.

The main seat can satisfy the tripwire by spawning an Agent on the cheapest
model that is still qualified to complete the job correctly.  "Cheapest" is
constrained by competence, data access, risk and the model-fit map; it does not
mean "lower than the parent."  A Terra parent may need a Terra peer.  For work
that legitimately belongs inline, the main seat must put a visible
`executor: ... because ...` line in the transcript before continuing.  That
escape is disabled when the partner explicitly requested delegation: the
instruction is sticky for the whole active task and only an Agent spawn or the
partner's explicit revocation releases it.

This cannot enforce Codex/Cowork surfaces that do not run Claude hooks.  Their
equivalent rail lives in the always-read bootstrap text.  Calling this hook a
universal hard boundary would be hardness cosplay.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import sys


REPO = os.path.expanduser("~/carr-system")
VAULT_MARKERS = ("/CARR AI", "/My Drive/CARR AI")
LOG = os.path.join(REPO, "out", "delegation-gate.jsonl")

MECHANICAL = {"Bash", "Read", "Grep", "Glob", "WebFetch", "WebSearch"}
AGENT_TOOLS = {"Agent", "Task"}

DELEGATE = re.compile(
    r"\b(delegat(?:e|ed|ing|ion)|sub[- ]?agents?|lower[- ]?(?:cost|tier)?\s*models?|"
    r"cheaper\s+models?|cheapest\s+(?:qualified\s+)?model|"
    r"use\s+(?:a\s+)?(?:sonnet|haiku|terra|codex|grok))\b",
    re.I,
)
REVOKE = re.compile(
    r"\b(do not|don'?t|stop|no)\s+(?:use\s+)?(?:delegat\w*|sub[- ]?agents?|lower[- ]?models?)\b"
    r"|\bkeep (?:this|it) inline\b",
    re.I,
)
EXECUTOR = re.compile(
    r"(?im)^\s*executor:\s*(?:T3|top(?:\s+seat)?|Fable|Opus|inline)\b[^\n]{0,180}"
    r"(?:because|\u2014|--|:)\s*\S+"
)
COMPLETE = re.compile(r"(?im)^\s*delegation complete:\s*\S+")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit(record: dict) -> None:
    if record.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def in_carr_scope(cwd: str) -> bool:
    try:
        path = os.path.realpath(os.path.expanduser(cwd or ""))
    except Exception:
        return False
    if path == REPO or path.startswith(REPO + os.sep):
        return True
    return any(marker in path for marker in VAULT_MARKERS)


def records(path: str, limit: int = 500) -> list[dict]:
    out = []
    with open(path, "r", errors="replace") as fh:
        for line in fh.readlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def text_blocks(rec: dict, roles: tuple[str, ...]) -> str | None:
    if rec.get("type") not in roles:
        return None
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    msg = rec.get("message") or rec
    content = msg.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        return None
    if not text or text.lstrip().startswith((
        "<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
        "<local-command", "<command-name>", "Caveat:",
    )):
        return None
    return text


def tool_names(rec: dict) -> list[str]:
    msg = rec.get("message") or rec
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [
        b.get("name", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


def deny(payload: dict, reason: str, latch: bool, count: int) -> int:
    audit({
        "ts": now(),
        "hook": "delegation-gate",
        "session": payload.get("session_id"),
        "class": "sticky_latch" if latch else "second_mechanical_call",
        "mechanical_calls_before_denial": count,
        "tool": payload.get("tool_name") or payload.get("toolName"),
    })
    print(reason, file=sys.stderr)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in MECHANICAL:
            return 0
        if not in_carr_scope(payload.get("cwd") or ""):
            return 0

        path = payload.get("transcript_path") or payload.get("transcriptPath") or ""
        if not path or not os.path.exists(path):
            return 0
        if f"{os.sep}subagents{os.sep}" in os.path.realpath(path):
            return 0

        recs = records(path)
        last_human_idx = None
        last_human = ""
        for i in range(len(recs) - 1, -1, -1):
            text = text_blocks(recs[i], ("user", "human"))
            if text is not None:
                last_human_idx, last_human = i, text
                break
        if last_human_idx is None:
            return 0
        # The partner's explicit no-delegation instruction controls this turn;
        # it both releases any earlier latch and exempts the ordinary tripwire.
        if REVOKE.search(last_human):
            return 0
        window = recs[last_human_idx + 1:]
        used = [name for rec in window for name in tool_names(rec)]
        if any(name in AGENT_TOOLS for name in used):
            return 0

        mechanical_count = sum(name in MECHANICAL for name in used)
        if mechanical_count == 0:  # the one inline lookup the rule allows
            return 0

        assistant_text = "\n".join(
            text for rec in window
            if (text := text_blocks(rec, ("assistant",)))
        )
        # TASK-STICKY MEANS ACROSS TURNS.  The first version looked only at the
        # latest human message, which would have repeated the production bug:
        # "delegate this" followed by "I'm in" erased the signal exactly when a
        # newly available login opened the next mechanical phase.  Walk the
        # transcript instead.  A later explicit revocation or a visible
        # `delegation complete: <task>` line ends the latch; ordinary follow-up
        # messages and compaction summaries do not.
        last_delegate = -1
        last_revoke = -1
        last_complete = -1
        for i, rec in enumerate(recs):
            human = text_blocks(rec, ("user", "human"))
            if human:
                if DELEGATE.search(human):
                    last_delegate = i
                if REVOKE.search(human):
                    last_revoke = i
            assistant = text_blocks(rec, ("assistant",))
            if assistant and COMPLETE.search(assistant):
                last_complete = i
        sticky = last_delegate > max(last_revoke, last_complete)
        if not sticky and EXECUTOR.search(assistant_text):
            return 0

        if sticky:
            return deny(
                payload,
                "DELEGATION GATE — the partner explicitly requested delegation for this "
                "active task. That instruction survives phase changes, new logins/data "
                "sources, retries, continuation and compaction. One briefing lookup was "
                "allowed; this is the second mechanical call. Spawn the cheapest Agent "
                "that is qualified to complete the subtask correctly, including a peer-tier "
                "Agent when the work needs it, and pass along the evidence already gathered. "
                "The main seat may then verify "
                "load-bearing findings and execute authorized writes, but it may not "
                "reclaim the sweep. Only the partner can revoke the latch.",
                True,
                mechanical_count,
            )

        return deny(
            payload,
            "DELEGATION TRIPWIRE — this is the second mechanical tool call on the same "
            "turn and no executor has been declared. Before continuing, either spawn a "
            "qualified Agent on the cheapest model that can do the subtask correctly "
            "(peer tier is valid when required) or state a transcript-visible "
            "line `executor: T3-inline — because <specific allowed reason>` for work that "
            "truly belongs to orchestration, judgment, verification, or is too small to "
            "brief. If this session's harness blocks proactive delegation, use its exact "
            "user-approval path; do not silently absorb the sweep.",
            False,
            mechanical_count,
        )
    except Exception:
        return 0  # conduct/cost gate fails open; it must never wedge the session


if __name__ == "__main__":
    sys.exit(main())
