"""Local Jev spend estimate and its single deduplicated response loop.

This is a library for the canonical health surface. It reads the canonical
usage receipt log, never vendor credentials or prompt text.
"""

import fcntl
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager


def _root():
    source_root = Path(__file__).resolve().parent.parent
    try:
        git_dir = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=source_root, capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        return Path(git_dir).parent if git_dir else source_root
    except (OSError, subprocess.SubprocessError):
        return source_root


ROOT = _root()
USAGE_LOG = ROOT / "out" / "jev-calls.jsonl"
LOOP_STATE = ROOT / "out" / "jev-spend-loop.json"
CONFIG = Path(__file__).resolve().parent / "config" / "jev-cost-guard.v1.json"
ACTION = ("on breach: open/update one dedup loop · owner orchestrator · "
          "remediation find caller in jev usage log · verify next UTC-day estimate "
          "below threshold · auto-clear when below threshold")


def _run_verb(name, payload):
    result = subprocess.run(["./run.sh", "call", name, json.dumps(payload)],
                            cwd=ROOT, capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError(f"{name} returned {result.returncode}")
    start = result.stdout.find("{")
    if start < 0:
        raise RuntimeError(f"{name} returned no JSON")
    answer = json.loads(result.stdout[start:])
    if not isinstance(answer, dict) or answer.get("error") or (name != "read-loop" and answer.get("ok") is not True):
        raise RuntimeError(f"{name} did not confirm the write")
    return answer


def _loop_version(run_verb, loop_id):
    current = run_verb("read-loop", {"loop_id": loop_id})
    if current.get("loop_id") != loop_id or type(current.get("version")) is not int:
        raise RuntimeError("read-loop returned no matching version")
    return current["version"]


def _state(path):
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _loop_lock(path):
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _id(day, action, amount):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr-jev-spend:{day}:{action}:{amount:.2f}"))


def _today_usage(log_path, day):
    calls = tokens = unknown = 0
    with Path(log_path).open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                if row.get("ok") is not True or row.get("cache_hit") is True:
                    continue
                stamp = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
                if stamp.astimezone(timezone.utc).date().isoformat() != day:
                    continue
                usage = row.get("usage") or {}
                value = usage.get("input_tokens")
                if type(value) is not int or value < 0:
                    unknown += 1
                    continue
                calls += 1
                tokens += value
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return calls, tokens, unknown


def check_spend(log_path=USAGE_LOG, config_path=CONFIG, state_path=LOOP_STATE,
                run_verb=_run_verb, *, now=None):
    """Return one health row, with the bound action in the row itself."""
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    threshold = float(config["daily_warning_usd"])
    price = float(config["price_usd_per_million_input_tokens"])
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    if not Path(log_path).is_file():
        return f"UNAVAILABLE jev spend — local usage log absent · {ACTION}"
    calls, tokens, unknown = _today_usage(log_path, day)
    amount = tokens * price / 1_000_000
    if unknown and amount <= threshold:
        return (f"UNKNOWN jev spend — {unknown} successful calls missing usage "
                f"({calls} measured calls; warning retained until usage is measurable) "
                f"· {ACTION}")
    status = "WARN" if amount > threshold else "OK"
    line = (f"{status} jev spend — ${amount:.3f} estimated local / UTC day "
            f"({calls} calls, {tokens} input tokens; threshold ${threshold:.2f}) · {ACTION}")
    if unknown:
        line += f" · at least this amount; {unknown} successful calls missing usage"
    body = (f"Jev estimated local spend is ${amount:.3f} on {day} UTC, above "
            f"${threshold:.2f}/day. Find caller in jev usage log at "
            "out/jev-calls.jsonl, inspect prompt hashes for duplicate judge "
            "calls, and verify the next UTC-day estimate below threshold.")
    try:
        with _loop_lock(state_path):
            state = _state(state_path)
            if amount > threshold:
                if not state.get("loop_id"):
                    answer = run_verb("add-loop", {
                        "idempotency_key": _id(day, "add", amount),
                        # CARR's orchestrator queue is named "claude" in the
                        # loop owner contract; the health row names the role.
                        "kind": "open_loop", "domain": "system", "owner": "claude",
                        "body": body, "marker": "none", "blocker": "capability",
                        "blocker_detail": "TypeSafe callers outside this machine's local usage ledger",
                    })
                    if not answer.get("loop_id"):
                        raise RuntimeError("add-loop returned no loop_id")
                    state = {"loop_id": answer["loop_id"], "reported": amount, "day": day}
                    _save_state(state_path, state)
                elif day != state.get("day") or amount - float(state.get("reported", 0)) >= 0.10:
                    run_verb("update-loop", {
                        "idempotency_key": _id(day, "update", amount),
                        "loop_id": state["loop_id"],
                        "base_version": _loop_version(run_verb, state["loop_id"]),
                        "body": body,
                    })
                    state.update(reported=amount, day=day)
                    _save_state(state_path, state)
            elif state.get("loop_id"):
                run_verb("close-loop", {
                    "idempotency_key": _id(day, "clear", amount),
                    "loop_id": state["loop_id"], "resolution": "done",
                    "base_version": _loop_version(run_verb, state["loop_id"]),
                    "outcome": (f"Auto-cleared: estimated local Jev spend on {day} UTC "
                                f"is ${amount:.3f}, below ${threshold:.2f}/day."),
                })
                _save_state(state_path, {})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        line += f" · loop action FAILED ({type(exc).__name__})"
    return line
