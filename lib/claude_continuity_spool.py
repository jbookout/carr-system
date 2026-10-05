"""Local spool for Claude continuity receipts the store did not confirm.

The spool holds only receipts whose delivery is unknown (the store was
unreachable or did not answer in time).  A receipt the store refused can never
apply, so it goes straight to the archive with the store's reason.  The drain
re-sends spooled lifecycle receipts in order under their original idempotency
keys; it never sends a checkpoint or anything carrying a pending external
effect.  Nothing here deletes a receipt: every one that leaves the spool is
appended to the archive with its outcome.
"""
from __future__ import annotations

from typing import cast

import fcntl
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import shlex
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[1]
CALL_TIMEOUT_SECONDS = 7.0
MAX_SPOOL_BYTES = 1_000_000
MAX_SPOOL_FILES = 100
MAX_DRAIN_ATTEMPTS = 5
WARN_COUNT = 10
WARN_AGE_HOURS = 36
DRAINABLE_VERB = "claude-record-event"
RECORD_EVENT_FIELDS = frozenset({
    "runtime", "session_id", "transcript_path_digest", "project_affinity", "cwd",
    "parent_session_id", "native_agent_id", "model_id", "idempotency_key", "event_type",
    "cursor", "transcript_digest", "observed_at", "telemetry", "checkpoint_version",
})
ACTION = ("on breach: open/update one dedup loop · owner orchestrator · "
          "remediation read the failure kinds in the spool and the refusal reasons in the archive, "
          "then fix the refused or unreachable write · verify the next drain leaves fewer than "
          f"{WARN_COUNT} unsent and none older than {WARN_AGE_HOURS}h · auto-clear when under threshold")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def spool_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get(
        "CARR_CLAUDE_CONTINUITY_SPOOL_DIR",
        str(pathlib.Path.home() / ".config/carr/claude-continuity-spool"))).expanduser()


def archive_path() -> pathlib.Path:
    default = spool_dir().with_name(spool_dir().name + "-archive.jsonl")
    return pathlib.Path(os.environ.get("CARR_CLAUDE_CONTINUITY_SPOOL_ARCHIVE", str(default))).expanduser()


def _attempts_path() -> pathlib.Path:
    return spool_dir().with_suffix(".drain.json")


def spool_key() -> bytes:
    path = spool_dir().with_suffix(".key")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        data = path.read_bytes()
        if len(data) != 32 or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("invalid spool key")
        return data
    except FileNotFoundError:
        data = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return data


def _signature(body: dict) -> str:
    unsigned = {key: value for key, value in body.items() if key != "hmac_sha256"}
    return hmac.new(spool_key(), canonical(unsigned), hashlib.sha256).hexdigest()


def invoke(verb: str, args: dict) -> tuple[dict | None, dict | None]:
    """Call one verb; return (response, None) or (None, failure).

    failure.kind is "refused" (the store answered with an error code — the
    write can never apply as sent), "timeout" or "transport" (delivery unknown).
    """
    raw = os.environ.get("CARR_CLAUDE_CONTINUITY_CALL")
    try:
        argv = shlex.split(raw) if raw else [str(REPO / "run.sh"), "call"]
    except ValueError:
        return None, {"kind": "transport", "exit": None}
    argv.extend((verb, json.dumps(args, separators=(",", ":"), ensure_ascii=False)))
    env = {**os.environ, "CARR_MCP_CLIENT_PROFILE": "claude-continuity"}
    try:
        proc = subprocess.run(argv, cwd=REPO, env=env, capture_output=True,
                              text=True, timeout=CALL_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return None, {"kind": "timeout"}
    except OSError:
        return None, {"kind": "transport", "exit": None}
    if proc.returncode:
        marker = proc.stderr.find("TOOL ERROR ")
        if marker >= 0:
            try:
                payload, _ = json.JSONDecoder().raw_decode(proc.stderr[marker + len("TOOL ERROR "):])
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, str) and error:
                    return None, {"kind": "refused", "error": error[:200]}
            except ValueError:
                pass
        return None, {"kind": "transport", "exit": proc.returncode}
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, {"kind": "transport", "exit": 0}
    if isinstance(response, dict) and response.get("ok") is True:
        return response, None
    return None, {"kind": "transport", "exit": 0}


def archive(receipt: dict, outcome: str, reason: str) -> None:
    path = archive_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    row = {"schema_version": 1, "outcome": outcome, "reason": reason,
           "archived_at": datetime.now(timezone.utc).isoformat(), "receipt": receipt}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, canonical(row) + b"\n")
    finally:
        os.close(fd)


def _read(path: pathlib.Path) -> dict | None:
    try:
        body = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _move_to_archive(path: pathlib.Path, outcome: str, reason: str) -> None:
    receipt = _read(path)
    archive(receipt if receipt is not None else {"unreadable_file": path.name}, outcome, reason)
    path.unlink(missing_ok=True)


def spooled() -> list[pathlib.Path]:
    """Spool files oldest first; the name starts with the spool time in ns."""
    try:
        return sorted(spool_dir().glob("*.json"))
    except OSError:
        return []


def spool_receipt(verb: str, args: dict, failure: dict, *,
                  max_files: int = MAX_SPOOL_FILES, max_bytes: int = MAX_SPOOL_BYTES) -> None:
    directory = spool_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    body = {"schema_version": 1, "verb": verb, "args": args, "failure": failure,
            "spooled_at": datetime.now(timezone.utc).isoformat()}
    body["hmac_sha256"] = _signature(body)
    encoded = canonical(body) + b"\n"
    files = spooled()
    total = sum(path.stat().st_size for path in files)
    while files and (len(files) >= max_files or total + len(encoded) > max_bytes):
        oldest = files.pop(0)
        total -= oldest.stat().st_size
        _move_to_archive(oldest, "overflow", "spool_cap_reached")
    fd, temp_name = tempfile.mkstemp(prefix=".receipt-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, directory / f"{time.time_ns()}-{secrets.token_hex(4)}.json")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        pathlib.Path(temp_name).unlink(missing_ok=True)


def _not_replayable(body: dict | None) -> str | None:
    if body is None:
        return "unreadable"
    signature = body.get("hmac_sha256")
    if not isinstance(signature, str) or not hmac.compare_digest(signature, _signature(body)):
        return "signature_invalid"
    if body.get("verb") != DRAINABLE_VERB:
        return str(body.get("verb"))[:200]
    args = body.get("args")
    if not isinstance(args, dict) or not args.get("idempotency_key") or set(args) - RECORD_EVENT_FIELDS:
        return "not_a_lifecycle_receipt"
    return None


@contextmanager
def _drain_lock():
    lock = spool_dir().with_suffix(".drain.lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _attempts() -> dict:
    try:
        value = json.loads(_attempts_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_attempts(attempts: dict) -> None:
    path = _attempts_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(attempts, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def drain(call=None) -> dict:
    """Re-send spooled lifecycle receipts in order; stop at the first unknown outcome."""
    call = call or invoke
    result = {"delivered": 0, "already_recorded": 0, "archived": 0, "transient": 0, "locked": False}
    with _drain_lock() as held:
        if not held:
            result["locked"] = True
            return result
        attempts = _attempts()
        try:
            for path in spooled():
                body = _read(path)
                reason = _not_replayable(body)
                if reason:
                    _move_to_archive(path, "not_replayable", reason)
                    result["archived"] += 1
                    continue
                body = cast(dict, body)
                response, failure = call(body["verb"], body["args"])
                if response is not None:
                    outcome = "already_recorded" if response.get("replayed") is True else "delivered"
                    _move_to_archive(path, outcome, "store_confirmed")
                    attempts.pop(path.name, None)
                    result[outcome] += 1
                elif failure["kind"] == "refused":
                    _move_to_archive(path, "refused", failure["error"])
                    attempts.pop(path.name, None)
                    result["archived"] += 1
                else:
                    tried = attempts.get(path.name, 0) + 1
                    if tried >= MAX_DRAIN_ATTEMPTS:
                        _move_to_archive(path, "retry_budget_exhausted", failure["kind"])
                        attempts.pop(path.name, None)
                        result["archived"] += 1
                    else:
                        attempts[path.name] = tried
                    result["transient"] += 1
                    break
        finally:
            live = {path.name for path in spooled()}
            _save_attempts({name: count for name, count in attempts.items() if name in live})
    return result


def measure(now: datetime) -> tuple[int, float | None]:
    """Unsent count and the oldest receipt's age in hours, from the spool itself."""
    files = spooled()
    if not files:
        return 0, None
    oldest_ns = int(files[0].name.split("-", 1)[0])
    return len(files), (now.timestamp() - oldest_ns / 1e9) / 3600


def loop_state_path() -> pathlib.Path:
    return spool_dir().with_suffix(".loop.json")


def run_verb(name: str, payload: dict) -> dict:
    """Loop verbs run as the ordinary local actor, not the continuity token."""
    env = {key: value for key, value in os.environ.items() if key != "CARR_MCP_CLIENT_PROFILE"}
    result = subprocess.run([str(REPO / "run.sh"), "call", name, json.dumps(payload)], cwd=REPO,
                            env=env, capture_output=True, text=True, timeout=35, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} returned {result.returncode}")
    start = result.stdout.find("{")
    if start < 0:
        raise RuntimeError(f"{name} returned no JSON")
    answer = json.loads(result.stdout[start:])
    if not isinstance(answer, dict) or answer.get("error") or (name != "read-loop" and answer.get("ok") is not True):
        raise RuntimeError(f"{name} did not confirm the write")
    return answer


def _loop_key(action: str, day: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr-claude-continuity-spool:{action}:{day}"))


def _state(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: pathlib.Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _loop_version(run_verb, loop_id: str) -> int:
    current = run_verb("read-loop", {"loop_id": loop_id})
    current = current.get("loop", current)
    if current.get("loop_id") != loop_id or type(current.get("version")) is not int:
        raise RuntimeError("read-loop returned no matching version")
    return current["version"]


def health_row(*, run_verb, state_path: pathlib.Path, now: datetime, drained: dict | None = None) -> str:
    """One row: unsent count and oldest age, with the bound response inline."""
    count, age = measure(now)
    breach = count >= WARN_COUNT or (age is not None and age > WARN_AGE_HOURS)
    oldest = "none" if age is None else f"oldest {age:.1f}h"
    ran = ""
    if drained is not None:
        ran = (f" · this run delivered {drained['delivered'] + drained['already_recorded']}, "
               f"archived {drained['archived']}")
    line = (f"{'WARN' if breach else 'OK'} claude continuity spool — {count} unsent, {oldest} "
            f"(warn at {WARN_COUNT} or {WARN_AGE_HOURS}h){ran} · {ACTION}")
    day = now.astimezone(timezone.utc).date().isoformat()
    try:
        state = _state(state_path)
        if breach and not state.get("loop_id"):
            answer = run_verb("add-loop", {
                "idempotency_key": _loop_key("add", day),
                "kind": "open_loop", "domain": "system", "owner": "claude", "marker": "none",
                "body": (f"Claude continuity receipts are not reaching the record layer: {count} unsent "
                         f"in ~/.config/carr/claude-continuity-spool, {oldest}. Read each spooled "
                         "receipt's failure kind and the archive's refusal reasons "
                         "(claude-continuity-spool-archive.jsonl), fix the refused or unreachable "
                         "write, then run `tools/health-check.py --section claude-continuity-spool` "
                         f"and confirm fewer than {WARN_COUNT} unsent, none older than {WARN_AGE_HOURS}h."),
                "blocker": "capability",
                "blocker_detail": "the Worker refusing or not answering claude-record-event "
                                  "from the claude-continuity token",
            })
            if not answer.get("loop_id"):
                raise RuntimeError("add-loop returned no loop_id")
            _save_state(state_path, {"loop_id": answer["loop_id"], "day": day})
        elif not breach and state.get("loop_id"):
            run_verb("close-loop", {
                "idempotency_key": _loop_key("clear", day), "loop_id": state["loop_id"],
                "resolution": "done", "base_version": _loop_version(run_verb, state["loop_id"]),
                "outcome": f"Auto-cleared: continuity spool holds {count} unsent, {oldest}.",
            })
            _save_state(state_path, {})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        line += f" · loop action FAILED ({type(exc).__name__})"
    return line
