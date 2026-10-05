#!/usr/bin/env python3
"""gate_ledger.py — one line per gate decision, so a gate's precision is countable.

WHY (2026-10-05, gap #4). One orchestrator session that day hit five false
alarms: the unattended guard refused a Grok prompt for naming a deploy command
and a builder brief for naming a key file; the completion-evidence and conduct
Stop gates reopened replies that had no defect; the escalation gate refused a
question about lifting a merge freeze. None of that was countable. The meter
(hook-meter-run.py) saw every firing, but nothing said which refusals were
WRONG, so no gate could be graded and the noisiest one could not be named.

WHAT IT WRITES. hook-meter-run.py calls observe() after every gate it runs.

  * Every block, hold (ask) or reopen appends a `decision` line: gate, rule,
    input digest, session, time. The rule is the gate's own refusal headline cut
    to its label (text after " — ", parenthesised and quoted detail removed), so
    no command, prompt or reply text is stored. The digest is a sha256 of the
    tool input or reply.
  * A `verdict` line labels a decision right or wrong. tools/gate_verdict.py
    writes human labels. This module writes one automatic label: WRONG, when the
    same session, as its very next move, completes the same substance anyway —
    a PostToolUse of a matching call, or the refusing gate itself accepting the
    matching call or reply. If the session did anything else in between (ran a
    check, changed course), the block shaped the work and stays unlabelled.

HOW "SAME SUBSTANCE" IS DECIDED WITHOUT KEEPING TEXT. Word 3-grams of the call's
content, each HMAC-keyed with the session id and kept as a hash in a per-session
pending file for WINDOW_S seconds. Two calls match when nearly all the 3-grams
of the shorter one appear in the longer one and their sizes are within 2x — so
a heredoc wrapper around a brief matches a Write of that brief. Session-keyed
hashes are comparable within the session and meaningless outside it.

IT NEVER CHANGES A VERDICT. Everything here runs after the gate has decided,
inside hook-meter-run's recording block, and every entry point swallows its own
errors. Fixtures: ops/gate_ledger-selftest.py
"""
import os

WINDOW_S = 600
MATCH_CONTAINMENT = 0.8
MATCH_SIZE_RATIO = 0.5
MAX_SHINGLES = 512
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
            msg = row.get("message") if isinstance(row, dict) else None
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            texts = [b.get("text", "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    except Exception:
        pass
    return ""


def substance(payload, event):
    """The text a decision was about: the call's content, or the reply."""
    import json
    if event in STOP_EVENTS:
        return (payload.get("last_assistant_message")
                or _last_reply(payload.get("transcript_path") or ""))
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


def shingles(text, session):
    import hashlib
    import hmac
    import re
    words = re.findall(r"[a-z0-9_]+", (text or "").lower())
    grams = {" ".join(words[i:i + 3]) for i in range(max(1, len(words) - 2))} if words else set()
    key = (session or "").encode()
    hashed = sorted(int(hmac.new(key, g.encode(), hashlib.sha256).hexdigest()[:12], 16) for g in grams)
    return hashed[:MAX_SHINGLES]


def same_substance(a, b):
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    if len(small) < 3 or len(small) < MATCH_SIZE_RATIO * len(large):
        return False
    return len(set(small) & set(large)) / len(small) >= MATCH_CONTAINMENT


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


def _observe(repo, record, raw, captured_out, captured_err):
    import time
    session = record.get("session")
    event = record.get("event") or ""
    outcome = record.get("outcome")
    if not session:
        return
    src = record.get("source")
    if not src:
        try:
            import hook_meter
            src = hook_meter.source()
        except Exception:
            src = "unclassified"
    ledger = ledger_path(repo, src)
    pending_path = _pending_path(ledger, session)
    deciding = outcome in KIND
    if not deciding and not os.path.exists(pending_path):
        return                                   # the hot path: nothing to do

    now = time.time()
    call = record.get("tool_use_id") or record.get("prompt_id") or ""
    payload = _payload(raw)
    sig = shingles(substance(payload, event), session)
    pending = [p for p in _load_pending(pending_path) if now - p.get("ts", 0) <= _window()]

    if deciding:
        import hashlib
        kind = "reopen" if event in STOP_EVENTS else KIND[outcome]
        decision_id = hashlib.sha256(
            f"{session}|{call}|{record.get('hook')}|{now}".encode()).hexdigest()[:16]
        headline = record.get("deny_class") or _headline(captured_err, captured_out)
        _append(ledger, {
            "type": "decision", "id": decision_id,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "gate": record.get("hook"), "event": event or None, "tool": record.get("tool"),
            "kind": kind, "rule": rule_of(headline), "input_digest": digest(payload, event),
            "session": session, "tool_use_id": record.get("tool_use_id"),
            "prompt_id": record.get("prompt_id"), "source": src,
        })
        pending = [p for p in pending if p.get("call") != call or p.get("hook") != record.get("hook")]
        pending.append({"id": decision_id, "hook": record.get("hook"), "event": event,
                        "call": call, "sig": sig, "ts": now})
        _save_pending(pending_path, pending)
        return

    keep = []
    for p in pending:
        # Another hook on the refused tool call itself. A reopened Stop turn
        # keeps its prompt_id, so for Stop a matching id is the NEXT attempt.
        if call and p.get("call") == call and event not in STOP_EVENTS:
            keep.append(p)
            continue
        if not same_substance(sig, p.get("sig") or []):
            if event == "PreToolUse":
                continue                         # the session moved on: not immediate
            keep.append(p)
            continue
        completed = (event == "PostToolUse"
                     or (outcome == "allow" and record.get("hook") == p.get("hook")
                         and (event in STOP_EVENTS) == (p.get("event") in STOP_EVENTS)))
        if not completed:
            keep.append(p)
            continue
        how = ("completed through " + (record.get("tool") or "another call")
               if event == "PostToolUse" else "accepted by the same gate")
        _append(ledger, {
            "type": "verdict", "decision_id": p["id"], "label": "wrong", "by": "auto",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "reason": f"same session's next move {how} with the same substance "
                      f"{int(now - p.get('ts', now))}s later",
        })
    _save_pending(pending_path, keep)
