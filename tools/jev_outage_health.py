"""Read Jev call evidence and keep one outage loop tied to its recovery."""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

THRESHOLD_HOURS = 2
ACTION = ("on breach: open/update one deduplicated loop (owner Joe; add TypeSafe "
          "credits); verify a successful Jev call; auto-clear on the next success")


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _rows(path):
    try:
        with open(path, encoding="utf-8") as source:
            for line in source:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except FileNotFoundError:
        return


def _safe_reason(error):
    advisory = re.fullmatch(r"build_advisory:([a-z_]+)", error or "")
    if advisory and advisory.group(1) in (
            "billing_exhausted", "auth_failed", "rate_limited", "timeout",
            "network", "server_5xx", "unknown"):
        return advisory.group(1)
    match = re.search(r"\bTypeSafe returned HTTP (\d{3})\b", error or "")
    status = int(match.group(1)) if match else None
    if status == 402:
        return "billing_exhausted"
    if status in (401, 403):
        return "auth_failed"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status <= 599:
        return "server_5xx"
    return "unknown"


def evaluate(judge_path, calls_path, *, now=None, threshold_hours=THRESHOLD_HOURS):
    """Keep an overdue failed attempt in WARN until a later success."""
    now = now or datetime.now(timezone.utc)
    success = attempt = None
    reason = "unknown"
    for row in _rows(calls_path):
        at = parse_time(row.get("ts"))
        if at and row.get("ok") is True and (success is None or at > success):
            success = at
    for row in _rows(judge_path):
        at = parse_time(row.get("at"))
        if not at:
            continue
        # Judge logs can contain offline fake-model selftest rows. Only the
        # TypeSafe client's real-call receipt ledger proves provider success.
        if isinstance(row.get("error"), str) and (attempt is None or at > attempt):
            attempt = at
            reason = _safe_reason(row["error"])
    age = (now - success).total_seconds() / 3600 if success else None
    pending = bool(attempt and attempt <= now and (success is None or attempt > success))
    if success and (attempt is None or success > attempt):
        status = "ok" if age <= threshold_hours or attempt else "skip"
    elif pending:
        status = "warn" if age is None or age > threshold_hours else "skip"
    else:
        status = "skip"
    return {"status": status, "reason": reason if status == "warn" else None,
            "pending": pending,
            "age_hours": round(max(age, 0), 1) if age is not None else None}


def _save(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _body(reason):
    cause = ("TypeSafe account has no API credits" if reason == "billing_exhausted"
             else f"TypeSafe call failed ({reason})")
    return (f"Jev is offline: {cause}. Joe owns remediation: add TypeSafe credits "
            "when billing is exhausted; otherwise repair the named provider failure. "
            "Verify with a successful Jev call. This loop auto-closes on the next success.")


def reconcile(result, state_path, verb):
    """Idempotently open/update the one outage loop, then close on success."""
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if result["status"] == "warn":
        reason = result["reason"]
        if state.get("loop_id"):
            if state.get("reason") == reason:
                return "open"
            payload = {"idempotency_key": str(uuid.uuid4()), "loop_id": state["loop_id"],
                       "body": _body(reason)}
            response = verb("update-loop", payload)
            if not response.get("ok"):
                return "error"
            state["reason"] = reason
            _save(state_path, state)
            return "updated"
        key = state.get("open_key") or str(uuid.uuid4())
        state["open_key"] = key
        _save(state_path, state)
        payload = {"idempotency_key": key, "kind": "open_loop", "owner": "Joe",
                   "domain": "system",
                   "blocker": "human_only" if reason == "billing_exhausted" else "capability",
                   "blocker_detail": ("Joe must add TypeSafe account credits"
                                      if reason == "billing_exhausted"
                                      else f"TypeSafe provider failure: {reason}"),
                   "body": _body(reason)}
        response = verb("add-loop", payload)
        loop_id = response.get("loop_id")
        if not response.get("ok") or not isinstance(loop_id, str):
            return "error"
        _save(state_path, {"loop_id": loop_id, "reason": reason})
        return "opened"
    if result["status"] == "ok" and state.get("loop_id"):
        key = state.get("close_key") or str(uuid.uuid4())
        state["close_key"] = key
        _save(state_path, state)
        response = verb("close-loop", {
            "idempotency_key": key, "loop_id": state["loop_id"], "resolution": "done",
            "outcome": "A new successful Jev call verified that the TypeSafe outage cleared."})
        if not response.get("ok"):
            return "error"
        _save(state_path, {})
        return "cleared"
    return "none"


def call_verb(name, payload, *, repo):
    process = subprocess.run(["./run.sh", "call", name, json.dumps(payload)],
                             cwd=repo, capture_output=True, text=True, timeout=35,
                             stdin=subprocess.DEVNULL)
    if process.returncode:
        return {"ok": False}
    output = process.stdout
    start = output.find("{")
    try:
        response = json.loads(output[start:]) if start >= 0 else {}
    except json.JSONDecodeError:
        response = {}
    return response if isinstance(response, dict) else {"ok": False}
