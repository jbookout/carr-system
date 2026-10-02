"""Grok credential timestamps and one daily response. Never decode other values."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jev_spend_health import ROOT, _run_verb, _loop_version, _state, _save_state

AUTH = Path.home() / ".grok" / "auth.json"
STATE = ROOT / "out" / "grok-session-alert.json"
ACTION = ("on breach: one dedup loop and alert/day · owner Joe · remediation run grok login · "
          "verify grok models succeeds and timestamp health is OK · auto-clear when health is OK")


def read_timestamps(path=AUTH):
    """Project one timestamp pair, skipping other values without decoding.

    Unlike json.load, this never constructs a token/id value in memory. Unknown
    strings are scanned a character at a time solely to find their end. Errors
    are fixed messages and cannot carry source bytes.
    """
    with Path(path).open(encoding="utf-8") as stream:
        char = stream.read(1)

        def advance():
            nonlocal char
            char = stream.read(1)

        def whitespace():
            while char and char in " \r\n\t":
                advance()

        def expect(value):
            if char != value:
                raise ValueError("invalid credential structure")
            advance()
            whitespace()

        def string(retain=False, key=False):
            if char != '"':
                raise ValueError("invalid credential string")
            advance()
            kept = '"' if retain else None
            candidates = ["create_time", "expires_at"] if key else []
            index = 0
            escaped = False
            while char:
                if retain:
                    if len(kept) >= 128:
                        raise ValueError("invalid timestamp field")
                    kept += char
                if char == '"' and not escaped:
                    advance()
                    whitespace()
                    if key:
                        return next((name for name in candidates if len(name) == index), None)
                    return json.loads(kept) if retain else None
                if ord(char) < 32:
                    raise ValueError("invalid credential string")
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                if key:
                    candidates = [name for name in candidates if index < len(name) and name[index] == char]
                    index += 1
                advance()
            raise ValueError("unterminated credential string")

        def skip(depth=0):
            if depth > 32:
                raise ValueError("invalid credential structure")
            if char == '"':
                string()
            elif char in ("{", "["):
                opener = char
                closer = "}" if opener == "{" else "]"
                expect(opener)
                if char != closer:
                    while True:
                        field = None
                        if opener == "{":
                            field = string(key=True)
                            expect(":")
                        if field:
                            if field in result:
                                raise ValueError("duplicate timestamp field")
                            result[field] = string(retain=True)
                        else:
                            skip(depth+1)
                        if char == closer:
                            break
                        expect(",")
                expect(closer)
            else:
                if not char or char in ",}]":
                    raise ValueError("invalid credential value")
                # No conversion or retention of unknown scalar values.
                while char and char not in ",}] \r\n\t":
                    advance()
                whitespace()

        whitespace()
        result = {}
        if char != "{":
            raise ValueError("invalid credential structure")
        skip()
        if char or set(result) != {"create_time", "expires_at"}:
            raise ValueError("missing or invalid credential timestamps")
        return result


def inspect_session(path=AUTH, *, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        times = read_timestamps(path)
        created, expires = (datetime.fromisoformat(times[key].replace("Z", "+00:00"))
                            for key in ("create_time", "expires_at"))
        if not created.tzinfo or not expires.tzinfo or created > now or expires <= created:
            raise ValueError("invalid credential timestamps")
        age = (now-created).total_seconds()/86400
        left = (expires-now).total_seconds()/86400
        status = "FAIL" if left <= 1 else "WARN" if left <= 2 else "OK"
        return {"status": status, "age_days": age, "days_left": left,
                "detail": f"access-token age {age:.2f}d; {left:.2f}d left (refresh-session expiry undisclosed)"}
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"status": "FAIL", "detail": "credential timestamps unavailable or invalid"}


def _deliver(message):
    """Reuse the spend alarm's record path and existing local/mail transports."""
    day = datetime.now(timezone.utc).date().isoformat()
    loop_path = ROOT / "out" / "grok-session-loop.json"
    state = _state(loop_path)
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr-grok-session:{day}"))
    failures = []
    try:
        if state.get("day") == day:
            if state.get("record") != "sent":
                failures.append("record")
        elif state.get("loop_id"):
            _run_verb("update-loop", {"loop_id": state["loop_id"],
                  "base_version": _loop_version(_run_verb, state["loop_id"]),
                  "idempotency_key": key, "body": message})
            state.update(day=day, record="sent")
        else:
            answer = _run_verb("add-loop", {"kind": "open_loop", "domain": "system", "owner": "joe",
                  "marker": "none", "blocker": "human_only", "blocker_detail": "Grok browser session needs grok login",
                  "idempotency_key": key, "body": message})
            if not answer.get("loop_id"):
                raise RuntimeError("loop write unconfirmed")
            state.update(loop_id=answer["loop_id"], day=day, record="sent")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        state.update(day=day, record="failed")
        failures.append("record")
    _save_state(loop_path, state)
    delivery_path = ROOT / "out" / "grok-session-delivery.json"
    delivery = _state(delivery_path)
    if delivery.get("day") != day:
        delivery = {"day": day}
    commands = [["/usr/bin/osascript", "-e", 'on run argv\ndisplay notification (item 1 of argv) with title "CARR Grok login"\nend run', message],
                [sys.executable, str(ROOT / "bin/gmail-handover.py"), "--to", "joe",
                 "--subject", "CARR Grok login needs attention", "--body", message]]
    for channel, command in zip(("local", "mail"), commands):
        if channel in delivery:
            if delivery[channel] != "sent":
                failures.append(channel)
            continue
        # Save BEFORE sending: a timeout/crash leaves an uncertain outcome,
        # never permission to duplicate a message to Joe that day.
        delivery[channel] = "attempted"
        _save_state(delivery_path, delivery)
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=35)
            if result.returncode:
                delivery[channel] = "failed"
                failures.append(channel)
            else:
                delivery[channel] = "sent"
        except (OSError, subprocess.SubprocessError):
            delivery[channel] = "failed"
            failures.append(channel)
        _save_state(delivery_path, delivery)
    if failures:
        raise RuntimeError("alert transport unavailable")


def _clear_loop():
    path = ROOT / "out" / "grok-session-loop.json"
    state = _state(path)
    if state.get("loop_id"):
        _run_verb("close-loop", {"loop_id": state["loop_id"],
                  "base_version": _loop_version(_run_verb, state["loop_id"]),
                  "idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr-grok-session:clear:{state['loop_id']}")),
                  "resolution": "done", "outcome": "Auto-cleared: Grok timestamp health is OK."})
        _save_state(path, {})


def sign_in_alert(*, state_path=STATE, now=None, send=_deliver, message=None):
    """One alert/day, shared by health and the runner; errors never echo bytes."""
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(timezone.utc).date().isoformat()
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path)+".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _state(path)
        if state.get("day") == day:
            return "alert already sent today"
        send(message or "Grok needs sign-in; run grok login. Verify grok models succeeds.")
        _save_state(path, {"day": day})
        return "alert sent"


def health_row(row, *, state_path=STATE, now=None, send=_deliver):
    line = f"{row['status']} Grok session — {row['detail']} · {ACTION}"
    if row["status"] != "OK":
        try:
            result = sign_in_alert(state_path=state_path, now=now, send=send,
                                   message=f"Grok session: {row['detail']}; run grok login. Verify grok models succeeds.")
            line += f" · {result}"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            line += " · alert FAILED"
    elif send is _deliver:
        try:
            Path(state_path).parent.mkdir(parents=True, exist_ok=True)
            with Path(str(state_path)+".lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _clear_loop()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            line += " · loop auto-clear FAILED"
    return line
