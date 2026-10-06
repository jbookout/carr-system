#!/usr/bin/env python3
"""Record refusals and label only matching successful next operations.

Evidence contains digests and labels; command, reply and prompt text stays local
in the native transcript. Session transitions and ledger identity are locked.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from contextlib import contextmanager

WINDOW_S = 600
STOP_EVENTS = ("Stop", "SubagentStop")
KIND = {"deny": "block", "ask": "hold"}


def ledger_path(repo, src):
    """One ledger per machine, in the CANONICAL checkout's out/. The meter keeps
    per-worktree evidence on purpose; a gate's precision is not per-worktree, and
    on 2026-10-05 the orchestrator's refusals sat in a .claude/worktrees/ out/
    that a canonical reader never saw."""
    override = os.environ.get("CARR_GATE_LEDGER")
    if override:
        return override
    out = os.path.join(repo, "out")
    try:
        import hook_meter
        checkout = hook_meter._checkout(repo)
        if checkout:
            out = os.path.join(checkout[1], "out")
    except Exception:
        pass
    if src == "live":
        return os.path.join(out, "gate-decisions.jsonl")
    if src == "fixture":
        return os.path.join(out, "fixtures", "gate-decisions-fixture.jsonl")
    return os.path.join(out, "gate-decisions-unclassified.jsonl")


def _window():
    try:
        return float(os.environ.get("CARR_GATE_LEDGER_WINDOW_S", WINDOW_S))
    except ValueError:
        return WINDOW_S


def _pending_path(ledger, session):
    # Checked on every firing, so no imports: the session id, made path-safe.
    name = "".join(c if c.isalnum() or c in "-_" else "_" for c in session)[:80] + ".json"
    return os.path.join(ledger + ".pending", name)


def rule_of(headline):
    """The refusal's label, with every piece of call-specific detail removed."""
    import re
    text = (headline or "").strip()
    if not text:
        return "unlabelled refusal"
    text = re.split(r"\s+[—–]\s+|:\s", text, maxsplit=1)[0]
    text = re.sub(r"\([^)]*\)", "(…)", text)
    text = re.sub(r"(['\"`]).*?\1", "…", text)
    return re.sub(r"\s+", " ", text).strip()[:80] or "unlabelled refusal"


def _payload(raw):
    import json
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _last_reply(transcript_path):
    """The final assistant text in a transcript, read from its tail only."""
    import json
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 256 * 1024))
            lines = fh.read().decode("utf-8", "replace").splitlines()
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            from lib.transcript_read import text
            reply = text(row, {"assistant"})
            if reply:
                return reply
    except Exception:
        pass
    return ""


def substance(payload, event):
    """The text a decision was about: the call's content, or the reply."""
    import json
    if event in STOP_EVENTS:
        return (payload.get("last_assistant_message")
                or _last_reply(payload.get("transcript_path") or payload.get("transcriptPath") or ""))
    if event == "UserPromptSubmit":
        return payload.get("prompt") or ""
    ti = payload.get("tool_input")
    if isinstance(ti, dict):
        for key in ("command", "cmd", "content", "new_string", "prompt"):
            if isinstance(ti.get(key), str):
                return ti[key]
        if isinstance(ti.get("edits"), list):
            return "\n".join(str(e.get("new_string", "")) for e in ti["edits"] if isinstance(e, dict))
    if isinstance(ti, str):
        return ti
    return json.dumps(ti, sort_keys=True, default=str) if ti is not None else ""


def digest(payload, event):
    import hashlib
    import json
    if event in STOP_EVENTS or event == "UserPromptSubmit":
        blob = substance(payload, event)
    else:
        blob = json.dumps(payload.get("tool_input"), sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256((blob or "").encode("utf-8", "replace")).hexdigest()


def _append(path, row):
    import json
    line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
    try:
        fh = open(path, "a", encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, "a", encoding="utf-8")
    with fh:
        fh.write(line)


def _load_pending(path):
    import json
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _save_pending(path, rows):
    import json
    if not rows:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    os.replace(tmp, path)


def _headline(captured_err, captured_out):
    import json
    for line in (captured_err or "").splitlines():
        if line.strip():
            return line.strip()
    try:
        data = json.loads((captured_out or "").strip())
        specific = data.get("hookSpecificOutput") or {}
        reason = data.get("reason") or specific.get("permissionDecisionReason") or ""
        if isinstance(reason, str) and reason.lstrip().startswith("{"):
            reason = json.loads(reason).get("reason") or ""
        return str(reason).strip().splitlines()[0] if str(reason).strip() else ""
    except Exception:
        return ""


def observe(repo, record, raw, captured_out="", captured_err=""):
    """Record a decision, or settle a pending one. Never raises."""
    try:
        _observe(repo, record, raw, captured_out, captured_err)
    except Exception:
        pass


@contextmanager
def locked(path):
    import fcntl
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def decision_id(record):
    import hashlib
    call = record.get("tool_use_id")
    if not call:
        call = f"{record.get('prompt_id')}|{record.get('ts')}"
    identity = f"{record.get('session')}|{call}|{record.get('event')}|{record.get('hook')}"
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def write_decision(ledger, record, input_digest=None, headline=None, backfilled=False):
    import json
    did = decision_id(record)
    with locked(ledger):
        if os.path.exists(ledger):
            with open(ledger, encoding="utf-8") as handle:
                for line in handle:
                    row = json.loads(line)
                    if row.get("type") == "decision" and row.get("id") == did:
                        return did, False
        event = record.get("event")
        row = {"type": "decision", "id": did, "ts": record.get("ts"),
               "gate": record.get("hook"), "event": event, "tool": record.get("tool"),
               "kind": "reopen" if event in STOP_EVENTS else KIND[record["outcome"]],
               "rule": rule_of(headline or record.get("deny_class") or record.get("deny_headline")),
               "input_digest": input_digest, "session": record.get("session"),
               "tool_use_id": record.get("tool_use_id"), "prompt_id": record.get("prompt_id"),
               "source": record.get("source")}
        if backfilled:
            row["backfilled"] = True
        _append(ledger, row)
    return did, True


def _operation(payload, tool, session):
    import hashlib
    import hmac
    import json
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return None
    if tool in {"Write", "Edit", "MultiEdit"} and not (ti.get("file_path") or ti.get("path")):
        return None
    operation = {"tool": tool, "input": {k: v for k, v in ti.items() if k != "fixture_verdict"},
                 "cwd": payload.get("cwd")}
    return hmac.new(session.encode(), json.dumps(operation, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def _successful(payload, tool):
    response = payload.get("tool_response")
    if not isinstance(response, dict) or response.get("error") or response.get("isError"):
        return False
    if "exit_code" in response:
        return response["exit_code"] == 0
    return tool in {"Write", "Edit", "MultiEdit"} and response.get("success") is True


def _observe(repo, record, raw, captured_out, captured_err):
    import time
    session = record.get("session")
    if not session:
        return
    src = record.get("source")
    if not src:
        import hook_meter
        src = hook_meter.source()
    ledger = ledger_path(repo, src)
    pending_path = _pending_path(ledger, session)
    with locked(pending_path):
        _transition(ledger, pending_path, record, raw, captured_out, captured_err, src)


def _transition(ledger, pending_path, record, raw, captured_out, captured_err, src):
    import time
    event = record.get("event") or ""
    outcome = record.get("outcome")
    now = time.time()
    call = record.get("tool_use_id") or record.get("prompt_id") or ""
    payload = _payload(raw)
    reply_digest = digest(payload, event) if event in STOP_EVENTS else None
    operation = _operation(payload, record.get("tool"), record["session"])
    pending = [p for p in _load_pending(pending_path) if now - p.get("ts", 0) <= _window()]
    keep = []
    for p in pending:
        same_call = bool(call and call == p.get("call"))
        matches = (reply_digest == p.get("reply_digest") if event in STOP_EVENTS else
                   operation is not None and operation == p.get("operation"))
        if same_call and event not in STOP_EVENTS and event != "PostToolUse":
            keep.append(p)
            continue
        completed = (event == "PostToolUse" and _successful(payload, record.get("tool")))
        if matches and completed:
            _append(ledger, {"type": "verdict", "decision_id": p["id"], "label": "wrong", "by": "auto",
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                            "reason": "same session's next operation completed successfully with the same target and substance"})
        elif matches and event != "PostToolUse":
            # Admission is not completion. Remember the admitted retry's identity.
            if call:
                p["call"] = call
            keep.append(p)
        elif event not in {"PreToolUse", "PostToolUse", "UserPromptSubmit", *STOP_EVENTS}:
            keep.append(p)
    if outcome in KIND:
        record = {**record, "source": src, "ts": record.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
        did, added = write_decision(ledger, record, digest(payload, event),
                                    record.get("deny_class") or _headline(captured_err, captured_out))
        if added:
            keep.append({"id": did, "hook": record.get("hook"), "event": event,
                         "call": call, "reply_digest": reply_digest, "operation": operation, "ts": now})
    _save_pending(pending_path, keep)
