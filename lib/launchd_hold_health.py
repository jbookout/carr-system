from __future__ import annotations

import fcntl
import json
import platform
import time
import uuid
from pathlib import Path

from lib.launchd_hold import read_holds

THRESHOLD_SECONDS = 7 * 86400
ACTION = ("on breach: open/update one deduplicated repair loop per label · owner claude (Platform Engineer) · "
          "repair the hold's named cause, verify the job's expected output, then remove its hold "
          "and run config-as-code.py install --apply · verify check no longer reports HELD · "
          "auto-clear when the hold is removed or its dated review renews it to at most 7d")


def check(home, verb, now=None):
    now = time.time() if now is None else now
    directory = Path(home) / ".config/carr"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        with (directory / "launchd-hold-health.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            holds = read_holds(home)
            stale = {label: hold for label, hold in holds.items() if now - hold.since > THRESHOLD_SECONDS}
            path = directory / "launchd-hold-health.json"
            try:
                state = json.loads(path.read_text())
                if not isinstance(state, dict) or not all(isinstance(row, dict) for row in state.values()):
                    raise ValueError("hold health state must be an object")
            except FileNotFoundError:
                state = {}

            def save():
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
                temporary.replace(path)

            for label, hold in stale.items():
                row = state.setdefault(label, {"open_key": str(uuid.uuid4())})
                observed = None
                if row.get("loop_id"):
                    observed = verb("read-loop", {"loop_id": row["loop_id"]})
                    if not observed.get("version") or observed.get("status") not in {"open", "done", "dropped", "closed"}:
                        raise RuntimeError("repair loop read failed")
                    if observed["status"] != "open":
                        row = {"open_key": str(uuid.uuid4())}
                        state[label] = row
                body = (f"Launchd hold older than 7 days on {platform.node()}: {label}, {hold.reason}. "
                        f"Hold start epoch {hold.since}; measured from {hold.age_source}. "
                        "Repair the named cause and verify the job's expected output before "
                        "removing the hold and running config-as-code.py install --apply. "
                        "Auto-clear when absent or explicitly reviewed and renewed to at most 7 days.")
                if row.get("body") == body and row.get("loop_id"):
                    continue
                if row.get("loop_id"):
                    version = observed.get("version")
                    if not version:
                        raise RuntimeError("repair loop read failed")
                    response = verb("update-loop", {"idempotency_key": str(uuid.uuid4()),
                                    "loop_id": row["loop_id"], "base_version": int(version), "body": body})
                else:
                    row.setdefault("open_payload", {"idempotency_key": row["open_key"], "kind": "open_loop",
                                    "owner": "claude", "domain": "system", "body": body,
                                    "blocker": "other_lane", "blocker_detail":
                                    f"Platform Engineer must repair the held {label} job on {platform.node()}: {hold.reason}"})
                    save()
                    response = verb("add-loop", row["open_payload"])
                if not response.get("ok") or not (row.get("loop_id") or response.get("loop_id")):
                    raise RuntimeError("repair loop response failed")
                row.update(loop_id=row.get("loop_id") or response["loop_id"],
                           body=body if row.get("loop_id") else row["open_payload"]["body"])
                save()
            for label in list(state):
                if label in stale:
                    continue
                row = state[label]
                if not row.get("loop_id") and row.get("open_payload"):
                    response = verb("add-loop", row["open_payload"])
                    if not response.get("ok") or not response.get("loop_id"):
                        raise RuntimeError("repair loop pending response failed")
                    row["loop_id"] = response["loop_id"]
                    save()
                if row.get("loop_id"):
                    observed = verb("read-loop", {"loop_id": row["loop_id"]})
                    if not observed.get("version"):
                        raise RuntimeError("repair loop recovery read failed")
                    if observed.get("status") == "open":
                        row.setdefault("close_key", str(uuid.uuid4()))
                        save()
                        response = verb("close-loop", {"idempotency_key": row["close_key"],
                                        "loop_id": row["loop_id"], "base_version": int(observed["version"]),
                                        "resolution": "done", "outcome":
                                        f"Verified {label}'s hold is absent or reviewed and renewed to at most 7 days; age warning cleared."})
                        if not response.get("ok"):
                            raise RuntimeError("repair loop auto-clear response failed")
                del state[label]
                save()
            if stale:
                summary = "; ".join(hold.describe(now) for hold in stale.values())
                return f"WARN launchd holds {summary} · repair loops recorded · {ACTION}", 1
            return f"OK launchd holds {len(holds)} held; none older than 7d · {ACTION}", 0
    except (OSError, ValueError, RuntimeError) as exc:
        return f"WARN launchd holds response failed ({type(exc).__name__}: {exc}) · {ACTION}", 1
