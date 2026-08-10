#!/usr/bin/env python3
"""Enforce task-sticky delegation for CARR Claude Code sessions.

The 2026-08-10 failure was not a lack of written policy: an explicit request to
delegate was forgotten when a new Salesforce login changed the work phase.  A
transcript-only check also made the instruction vulnerable to transcript
truncation and continuation.  This hook therefore keeps a small, locked state
ledger at ``out/delegation-gate-state.json``.  It gives each explicit delegation
an immutable task id, binds that task to one main session, and permits a new
session to claim it only with a visible exact ``delegation resume: <task-id>``.

This is intentionally narrow.  It intercepts the known read-sweep tools only;
the main seat retains authorised writes and final verification.  The cheapest
executor must still be qualified by competence, data access, and risk.  That
may be a Terra peer, not a forced downgrade.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile


REPO = os.path.expanduser("~/carr-system")
VAULT_MARKERS = ("/CARR AI", "/My Drive/CARR AI")
LOG = os.path.join(REPO, "out", "delegation-gate.jsonl")
STATE = os.environ.get(
    "DELEGATION_GATE_STATE", os.path.join(REPO, "out", "delegation-gate-state.json")
)

# Claude Code records these names directly. Codex uses functions.<name> in
# PreToolUse payloads and a custom_tool_call named <name> in its transcript.
MECHANICAL = {
    "Bash", "Read", "Grep", "Glob", "WebFetch", "WebSearch",
    "apply_patch", "functions.exec", "functions.apply_patch",
}
AGENT_TOOLS = {"Agent", "Task", "functions.Agent", "functions.spawn_agent"}

DELEGATE = re.compile(
    r"\b(delegat(?:e|ed|ing)|sub[- ]?agents?|lower[- ]?(?:cost|tier)?\s*models?|"
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
    r"(?im)^\s*executor:\s*(?:T3(?:-inline)?|top(?:\s+seat)?|Fable|Opus|inline|"
    r"Terra(?:\s+(?:peer|agent|specialist))?|peer(?:\s+Terra)?)\b[^\n]{0,180}"
    r"(?:because|\u2014|--|:)\s*\S+"
)
RESUME_LINE = re.compile(
    r"(?im)^\s*delegation resume:\s*(dg-[0-9a-f]{16})\s*$"
)


def complete_line(task_id: str) -> re.Pattern[str]:
    """Exact completion marker for one immutable task, not a prose approximation."""
    return re.compile(
        rf"(?im)^\s*delegation complete:\s*{re.escape(task_id)}\s*$"
    )


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


def records(path: str) -> list[dict]:
    """Read the whole transcript: a 500-record tail loses active task authority."""
    out = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


SYNTHETIC_CODEX_USER_PREFIXES = (
    "<recommended_plugins>", "# AGENTS.md instructions", "<environment_context>",
    "<app-context>", "<skills_instructions>", "<permissions instructions>",
    "<apps_instructions>", "<plugins_instructions>",
    "The following is the Codex agent history added since your last approval",
)


def message_for(rec: dict) -> dict:
    """Return a Claude message or Codex response-item payload."""
    payload = rec.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload
    msg = rec.get("message")
    return msg if isinstance(msg, dict) else rec


def text_blocks(rec: dict, roles: tuple[str, ...]) -> str | None:
    """Read genuine partner/assistant text from Claude or Codex transcripts.

    Codex represents generated environment context and approval-review deltas
    as user messages. Those may quote an old "delegate" instruction, so they
    must not create a delegation latch.
    """
    msg = message_for(rec)
    role = msg.get("role") or rec.get("type")
    if role not in roles:
        return None
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # A Codex approval/history wrapper is one synthetic user record even
        # when the quoted delta arrives in a later content block.  Skipping only
        # its prefix block would let an old quoted "delegate" recreate a live
        # task latch.
        eligible = [block.get("text", "") for block in content
                    if isinstance(block, dict)
                    and block.get("type") in {"text", "input_text", "output_text"}
                    and isinstance(block.get("text"), str)]
        if role in {"user", "human"} and eligible and eligible[0].lstrip().startswith(
            SYNTHETIC_CODEX_USER_PREFIXES
        ):
            return None
        chunks = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in {"text", "input_text", "output_text"}:
                continue
            value = block.get("text", "")
            if not isinstance(value, str):
                continue
            if role in {"user", "human"} and value.lstrip().startswith(
                SYNTHETIC_CODEX_USER_PREFIXES
            ):
                continue
            chunks.append(value)
        text = "\n".join(chunks)
    else:
        return None
    if not text or text.lstrip().startswith((
        "<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
        "<local-command", "<command-name>", "Caveat:",
    )):
        return None
    return text


def tool_names(rec: dict) -> list[str]:
    payload = rec.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "custom_tool_call":
            name = payload.get("name", "")
            return [f"functions.{name}"] if isinstance(name, str) else []
        if payload.get("type") == "function_call":
            name, namespace = payload.get("name", ""), payload.get("namespace")
            if isinstance(name, str):
                return [f"{namespace}.{name}" if namespace else name]
            return []
    msg = rec.get("message") or rec
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [
        b.get("name", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


def is_mechanical(tool: str) -> bool:
    """Classify repeatable CARR sweep work across both runtime spellings."""
    return tool in MECHANICAL or tool.startswith(("mcp__carr__", "mcp__carr_records__"))


def is_agent_tool(tool: str) -> bool:
    return tool in AGENT_TOOLS or tool.endswith(".spawn_agent")


def is_subagent_payload(payload: dict) -> bool:
    """Codex supplies agent_type for child tool calls; main calls omit it."""
    kind = str(payload.get("agent_type") or payload.get("agentType") or "").lower()
    return kind in {"subagent", "sub_agent", "child"}


def is_carr_mcp_tool(tool: str) -> bool:
    """CARR record calls remain in scope even when a desktop task has no cwd."""
    return tool.startswith(("mcp__carr__", "mcp__carr_records__"))


def task_id_for(session_id: str, record_index: int, instruction: str) -> str:
    """Stable opaque id.  Neither the model nor later transcript edits choose it."""
    material = f"{session_id}\0{record_index}\0{instruction}".encode("utf-8")
    return "dg-" + hashlib.sha256(material).hexdigest()[:16]


def read_state_unlocked() -> dict:
    try:
        with open(STATE) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("tasks", {})
            data.setdefault("sessions", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "tasks": {}, "sessions": {}}


def write_state_unlocked(data: dict) -> None:
    """Atomic replacement while the adjacent advisory lock is held."""
    directory = os.path.dirname(STATE)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".delegation-gate-", dir=directory)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, STATE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked_state():
    """Serialize state transitions across parallel hooks and main sessions."""
    directory = os.path.dirname(STATE)
    os.makedirs(directory, exist_ok=True)
    lock_path = STATE + ".lock"
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = read_state_unlocked()
        try:
            yield data
        finally:
            write_state_unlocked(data)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def latest_human(recs: list[dict]) -> tuple[int | None, str]:
    for i in range(len(recs) - 1, -1, -1):
        text = text_blocks(recs[i], ("user", "human"))
        if text is not None:
            return i, text
    return None, ""


def latest_delegate(recs: list[dict]) -> tuple[int | None, str]:
    """Return a delegation not superseded by a later (or same-message) revoke.

    This matters when the durable state file is missing or recreated.  A later
    ordinary user turn must not resurrect an old delegation merely because the
    binding that would otherwise remember its revocation no longer exists.
    """
    found: tuple[int | None, str] = (None, "")
    last_revoke = -1
    for i, rec in enumerate(recs):
        human = text_blocks(rec, ("user", "human"))
        # Revocation wins even when the same message also contains "delegate".
        if human and REVOKE.search(human):
            last_revoke = i
        elif human and DELEGATE.search(human):
            found = (i, human)
    return found if found[0] is not None and found[0] > last_revoke else (None, "")


def has_exact_completion(recs: list[dict], task_id: str) -> bool:
    marker = complete_line(task_id)
    return any(
        (text := text_blocks(rec, ("assistant",))) and marker.search(text)
        for rec in recs
    )


def bind_task(data: dict, task_id: str, session_id: str) -> None:
    """A task has exactly one bound main session at a time."""
    task = data["tasks"][task_id]
    old_session = task.get("bound_session")
    if old_session:
        data["sessions"].pop(old_session, None)
    for known_session, known_task in list(data["sessions"].items()):
        if known_task == task_id:
            data["sessions"].pop(known_session, None)
    task["bound_session"] = session_id
    data["sessions"][session_id] = task_id


def release_task(data: dict, task_id: str, status: str) -> None:
    task = data["tasks"].get(task_id)
    if not task:
        return
    bound = task.get("bound_session")
    if bound:
        data["sessions"].pop(bound, None)
    task["bound_session"] = None
    task["status"] = status
    task["ended_at"] = now()


def sticky_task(session_id: str, recs: list[dict]) -> str | None:
    """Return this session's active task, creating/binding it only by valid rails."""
    if not session_id:
        return None
    _, latest = latest_human(recs)
    with locked_state() as data:
        bound_id = data["sessions"].get(session_id)
        bound = data["tasks"].get(bound_id) if bound_id else None
        if bound and bound.get("bound_session") != session_id:
            data["sessions"].pop(session_id, None)
            bound_id, bound = None, None

        if bound and bound.get("status") == "active":
            if REVOKE.search(latest):
                # A revocation releases this session's task only, never another
                # session's task in the same ledger.
                release_task(data, bound_id, "revoked")
                return None
            if has_exact_completion(recs, bound_id):
                release_task(data, bound_id, "completed")
                return None
            return bound_id

        # A new session cannot inherit a task from a broad "delegate" phrase.
        # It may bind only by the exact visible resume marker and only to an
        # active task already known to this state ledger.
        resume = RESUME_LINE.search(latest)
        if resume:
            candidate = resume.group(1)
            task = data["tasks"].get(candidate)
            if task and task.get("status") == "active":
                bind_task(data, candidate, session_id)
                return candidate
            return None

        # Same-message revoke beats delegation and creates no replacement task.
        if REVOKE.search(latest):
            return None
        index, instruction = latest_delegate(recs)
        if index is None:
            return None
        task_id = task_id_for(session_id, index, instruction)
        task = data["tasks"].get(task_id)
        if not task:
            data["tasks"][task_id] = {
                "created_at": now(),
                "origin_session": session_id,
                "status": "active",
                "bound_session": None,
            }
        bind_task(data, task_id, session_id)
        return task_id


def deny(payload: dict, reason: str, task_id: str | None, count: int) -> int:
    audit({
        "ts": now(),
        "hook": "delegation-gate",
        "session": payload.get("session_id") or payload.get("sessionId"),
        "class": "sticky_latch" if task_id else "second_mechanical_call",
        "task_id": task_id,
        "mechanical_calls_before_denial": count,
        "tool": payload.get("tool_name") or payload.get("toolName"),
    })
    # Codex requires structured JSON to block a PreToolUse invocation. Claude
    # Code blocks command hooks on exit 2 and stderr. Both paths are hard.
    if payload.get("hook_event_name") == "PreToolUse":
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    print(reason, file=sys.stderr)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if not is_mechanical(tool):
            return 0
        if not in_carr_scope(payload.get("cwd") or "") and not is_carr_mcp_tool(tool):
            return 0

        # Child agents do their assigned mechanical work. The main session is
        # the seat constrained by the no-reclaim rule; blocking the worker is
        # both redundant and would defeat the delegated task itself.
        if is_subagent_payload(payload):
            return 0

        path = payload.get("transcript_path") or payload.get("transcriptPath") or ""
        if not path or not os.path.exists(path):
            return 0
        if f"{os.sep}subagents{os.sep}" in os.path.realpath(path):
            return 0
        recs = records(path)
        last_human_idx, last_human = latest_human(recs)
        if last_human_idx is None:
            return 0

        window = recs[last_human_idx + 1:]
        used = [name for rec in window for name in tool_names(rec)]
        # Persist/reconcile task authority before applying the one-lookup
        # allowance.  Otherwise a first allowed lookup could be followed by a
        # continuation before any durable task id existed.
        task_id = sticky_task(
            str(payload.get("session_id") or payload.get("sessionId") or ""), recs
        )
        if any(is_agent_tool(name) for name in used):
            return 0
        mechanical_count = sum(is_mechanical(name) for name in used)
        if mechanical_count == 0:  # the one inline briefing lookup the rule allows
            return 0

        if task_id:
            return deny(
                payload,
                "DELEGATION GATE — active delegated task " + task_id + ". The partner's "
                "instruction survives phase changes, new logins/data sources, retries, "
                "continuation and compaction. One briefing lookup was allowed; this is the "
                "second mechanical call. Spawn the cheapest Agent qualified to complete the "
                "subtask correctly, including a Terra peer when the work needs it. The main "
                "seat may verify load-bearing findings and execute authorised writes, but may "
                "not reclaim the sweep. Release only with a visible exact `delegation "
                "complete: " + task_id + "`, or the partner's explicit revocation.",
                task_id,
                mechanical_count,
            )

        assistant_text = "\n".join(
            text for rec in window
            if (text := text_blocks(rec, ("assistant",)))
        )
        if EXECUTOR.search(assistant_text):
            return 0
        return deny(
            payload,
            "DELEGATION TRIPWIRE — this is the second mechanical tool call on the same "
            "turn and no executor has been declared. Before continuing, either spawn a "
            "qualified Agent on the cheapest model that can do the subtask correctly "
            "(a Terra peer is valid when required) or state a transcript-visible line "
            "`executor: Terra peer — because <specific allowed reason>` for work that "
            "truly belongs to orchestration, judgment, verification, or is too small to "
            "brief. Do not silently absorb the sweep.",
            None,
            mechanical_count,
        )
    except Exception:
        return 0  # conduct/cost gate fails open; it must never wedge the session


if __name__ == "__main__":
    sys.exit(main())
