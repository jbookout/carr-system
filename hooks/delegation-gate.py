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
import time


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    r"(?im)^\s*(?:#|//)?\s*executor:\s*(?:T3(?:-inline)?|top(?:\s+seat)?|main(?:\s+seat)?|"
    r"orchestrator|Fable|Opus|inline|"
    r"Terra(?:\s+(?:peer|agent|specialist))?|peer(?:\s+Terra)?)\b[^\n]{0,180}"
    r"(?:because|\u2014|--|:)\s*\S+"
)
# The deny message must name every label the regex accepts.  A session that
# substitutes its own word gets silently rejected, which is how a valid-looking
# declaration failed three times on 2026-08-11.  The selftest asserts this
# string appears in the message so the two can never drift apart again.
EXECUTOR_LABELS = (
    "main seat, top seat, inline, orchestrator, T3, Fable, Opus, Terra peer"
)

# A denial re-reads the transcript this many times before it is final.  The
# harness writes the assistant text block and the tool_use it accompanies as
# separate records, so a single synchronous read can land in the gap and miss a
# declaration that was already made.  Paid only on the about-to-deny path.
RETRY_DELAYS = (0.12, 0.25, 0.4)
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


_LAST_DROPPED_LINES = 0


def records(path: str) -> list[dict]:
    """Read the whole transcript: a 500-record tail loses active task authority.

    A partially written trailing line is skipped rather than fatal, but the
    count is kept: silently discarding it is what made the 2026-08-11 race
    undiagnosable from the audit log.
    """
    global _LAST_DROPPED_LINES
    out = []
    dropped = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except Exception:
                dropped += 1
                continue
    _LAST_DROPPED_LINES = dropped
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
    return [name for name, _ in tool_calls(rec)]


def tool_calls(rec: dict) -> list[tuple[str, dict]]:
    """(name, input) pairs for one record -- tool_names' data plus the call
    input, which the sweep-vs-noise counting refinement needs to tell a real
    read sweep apart from a trivial command or a self-referential re-read."""
    payload = rec.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "custom_tool_call":
            name = payload.get("name", "")
            inp = payload.get("input") or payload.get("arguments") or {}
            if not isinstance(inp, dict):
                inp = {}
            return [(f"functions.{name}", inp)] if isinstance(name, str) else []
        if payload.get("type") == "function_call":
            name, namespace = payload.get("name", ""), payload.get("namespace")
            inp = payload.get("arguments") or payload.get("input") or {}
            if not isinstance(inp, dict):
                inp = {}
            if isinstance(name, str):
                return [(f"{namespace}.{name}" if namespace else name, inp)]
            return []
    msg = rec.get("message") or rec
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            inp = b.get("input")
            out.append((b.get("name", ""), inp if isinstance(inp, dict) else {}))
    return out


def is_mechanical(tool: str) -> bool:
    """Classify repeatable CARR sweep work across both runtime spellings."""
    return tool in MECHANICAL or tool.startswith(("mcp__carr__", "mcp__carr_records__"))


# A no-op-ish confirmation command has no read surface and cannot enumerate or
# exfiltrate anything, so counting it toward the mechanical-sweep threshold
# only produces false positives.  Live case, 2026-08-13: a leaf worker's plain
# `echo` -- issued only to signal a step boundary -- tripped the tripwire
# meant for repeated grep/find/read sweeps.  Deliberately narrow: any shell
# metacharacter (pipe, subshell, redirect, substitution, chaining) drops the
# command out of this allowance and back into ordinary mechanical counting,
# so `echo $(cat secrets)` or `echo done && rm -rf x` is never exempted.
TRIVIAL_BASH = re.compile(
    r"^\s*(?:echo|true|pwd|date|whoami|hostname)\b[^|;&`$<>]*$"
)
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "apply_patch", "functions.apply_patch"}


def is_trivial_bash(tool: str, tool_input: dict) -> bool:
    """A bare echo/true/pwd/date/whoami/hostname, no metacharacters."""
    if tool != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return bool(TRIVIAL_BASH.match(command.strip()))


def written_path(tool: str, tool_input: dict) -> str | None:
    """The realpath a Write/Edit/apply_patch call targets, or None."""
    if tool not in WRITE_TOOLS:
        return None
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        return None
    try:
        return os.path.realpath(path)
    except Exception:
        return path


def reads_own_recent_write(tool: str, tool_input: dict, written: set[str]) -> bool:
    """A Read of a path this same turn already wrote is a self-check, not a
    sweep across files the session doesn't yet know the contents of -- the
    other 2026-08-13 case: a worker denied on re-reading its own output."""
    if tool != "Read":
        return False
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        return False
    try:
        real = os.path.realpath(path)
    except Exception:
        real = path
    return real in written


def is_agent_tool(tool: str) -> bool:
    return tool in AGENT_TOOLS or tool.endswith(".spawn_agent")


def is_subagent_payload(payload: dict) -> bool:
    """Codex supplies agent_type for child tool calls; main calls omit it."""
    kind = str(payload.get("agent_type") or payload.get("agentType") or "").lower()
    return kind in {"subagent", "sub_agent", "child"}


# Claude Code sets this in a spawned session's own OS environment whenever it
# starts that session as the child of another running Claude Code process --
# exactly the shape of an Agent/Task-tool leaf worker, confirmed 2026-08-13 by
# instrumenting this hook and triggering it live from inside a real leaf
# worker: the hook subprocess inherited CLAUDE_CODE_CHILD_SESSION=1 from its
# own session's process environment. Unlike the Codex agent_type field or the
# legacy nested-"/subagents/"-directory transcript shape (neither of which
# matched: this deployment gives every leaf worker its own top-level
# transcript file, not one nested under a parent's session directory), this
# signal is present on the exact payload Claude Code delivers for the exact
# failure observed four times on 2026-08-13. A leaf worker cannot delegate
# further -- it IS the delegate -- so the gate must never fire here; doing so
# blocks the very worker the main seat delegated the sweep to, which is the
# defect this exemption exists to close.
def is_subagent_env() -> bool:
    return os.environ.get("CLAUDE_CODE_CHILD_SESSION") == "1"


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


def declared_on_the_call(payload: dict) -> bool:
    """A declaration carried by the tool call itself cannot race the transcript.

    The transcript channel depends on the harness having flushed the assistant
    text block before the hook runs, which is exactly what failed on
    2026-08-11.  A Bash `description`, or a leading `# executor: ...` comment on
    the command, arrives inside this hook's own stdin payload, so it is visible
    at the only moment that matters.  It is also visible to the partner in the
    UI, attached to the call it justifies.
    """
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return False
    for key in ("description", "command"):
        value = tool_input.get(key)
        if isinstance(value, str) and EXECUTOR.search(value):
            return True
    return False


def executor_scan(path: str) -> dict | None:
    """Re-read the transcript from disk and look for a declaration.

    Deliberately re-reads rather than reusing the caller's records: the point is
    to see writes that landed after the first read.
    """
    recs = records(path)
    dropped = _LAST_DROPPED_LINES
    index, _ = latest_human(recs)
    if index is None:
        return None
    window = recs[index + 1:]
    text = "\n".join(
        chunk for rec in window
        if (chunk := text_blocks(rec, ("assistant",)))
    )
    return {
        "found": bool(EXECUTOR.search(text)),
        "assistant_chars": len(text),
        "record_count": len(recs),
        "window_size": len(window),
        "dropped_lines": dropped,
    }


def deny(
    payload: dict,
    reason: str,
    task_id: str | None,
    count: int,
    scan: dict | None = None,
    attempts: int = 1,
) -> int:
    record = {
        "ts": now(),
        "hook": "delegation-gate",
        "session": payload.get("session_id") or payload.get("sessionId"),
        "class": "sticky_latch" if task_id else "second_mechanical_call",
        "task_id": task_id,
        "mechanical_calls_before_denial": count,
        "tool": payload.get("tool_name") or payload.get("toolName"),
    }
    if scan is not None:
        # What the hook actually saw, so the next failure is readable from the
        # log instead of reconstructed from the transcript by hand.
        record.update({
            "executor_seen": scan["found"],
            "assistant_chars": scan["assistant_chars"],
            "record_count": scan["record_count"],
            "window_size": scan["window_size"],
            "dropped_lines": scan["dropped_lines"],
            "retry_attempts": attempts,
        })
    audit(record)
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
        # A leaf worker cannot delegate further -- it IS the delegate. This
        # must be the first check: it must exempt every mechanical tool this
        # gate would otherwise classify, before scope or transcript checks
        # that a leaf worker's own payload (cwd, transcript shape) might not
        # satisfy the same way a main-seat session's would.
        if is_subagent_env():
            return 0

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
        dropped_lines = _LAST_DROPPED_LINES
        last_human_idx, last_human = latest_human(recs)
        if last_human_idx is None:
            return 0

        window = recs[last_human_idx + 1:]
        calls = [call for rec in window for call in tool_calls(rec)]
        used = [name for name, _ in calls]
        # Persist/reconcile task authority before applying the one-lookup
        # allowance.  Otherwise a first allowed lookup could be followed by a
        # continuation before any durable task id existed.
        task_id = sticky_task(
            str(payload.get("session_id") or payload.get("sessionId") or ""), recs
        )
        if any(is_agent_tool(name) for name in used):
            return 0

        # Count only calls that are actually sweep work: a trivial echo/true
        # confirmation, or a Read of a path this same turn already wrote,
        # carries none of the repeated-grep/find/read cost the gate exists to
        # push downhill, so neither should consume the one free lookup or
        # trip the tripwire.  Order matters -- a write earlier in this window
        # exempts a later read of that same path, not the reverse.
        written: set[str] = set()
        mechanical_count = 0
        for name, inp in calls:
            if is_mechanical(name) and not is_trivial_bash(name, inp) \
                    and not reads_own_recent_write(name, inp, written):
                mechanical_count += 1
            target = written_path(name, inp)
            if target:
                written.add(target)
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
        scan = {
            "found": bool(EXECUTOR.search(assistant_text)),
            "assistant_chars": len(assistant_text),
            "record_count": len(recs),
            "window_size": len(window),
            "dropped_lines": dropped_lines,
        }
        if scan["found"] or declared_on_the_call(payload):
            return 0

        # The first read can race the harness still writing the assistant text
        # record that carries the declaration.  On 2026-08-11 that cost three
        # denials of a call the gate's own logic would have allowed, so a denial
        # is never decided on a single read.
        attempts = 1
        for delay in RETRY_DELAYS:
            time.sleep(delay)
            attempts += 1
            rescan = executor_scan(path)
            if rescan is None:
                continue
            scan = rescan
            if scan["found"]:
                audit({
                    "ts": now(),
                    "hook": "delegation-gate",
                    "session": payload.get("session_id") or payload.get("sessionId"),
                    "class": "executor_allowed_after_retry",
                    "task_id": None,
                    "attempt": attempts,
                    "assistant_chars": scan["assistant_chars"],
                    "dropped_lines": scan["dropped_lines"],
                    "tool": payload.get("tool_name") or payload.get("toolName"),
                })
                return 0

        return deny(
            payload,
            "DELEGATION TRIPWIRE — second mechanical tool call this turn and no executor "
            "declared. Either spawn the cheapest Agent qualified to do the subtask "
            "correctly (a Terra peer counts), or START A LINE with `executor: <label> — "
            "because <specific reason>`. The label must be one of: " + EXECUTOR_LABELS +
            ". Any other word is rejected. Most reliable channel: put that same line in the "
            "Bash `description`, or as a leading `# executor: ...` comment on the command, "
            "which this hook reads directly and cannot miss. "
            "Declare it only for work that truly belongs to "
            "orchestration, judgment, verification, or is too small to brief. Do not "
            "silently absorb the sweep.",
            None,
            mechanical_count,
            scan,
            attempts,
        )
    except Exception:
        return 0  # conduct/cost gate fails open; it must never wedge the session


if __name__ == "__main__":
    sys.exit(main())
