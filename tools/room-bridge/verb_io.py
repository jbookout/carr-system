#!/usr/bin/env python3
"""verb_io.py — the room bridge's one path to the record layer: shell to
tools/call-verb.py, exactly the documented `./run.sh call <verb> '<json>'`
route (HTTPS to the deployed Worker, LOCAL_TOKENS bearer, server-derived
identity). Never a direct database connection — same stance
tools/partner-line/watch.py's fetch_turns() already took, for the same
reason: add-room-turn's sponsor attribution is server-derived
(personalScopeForActor), and a script that wrote the table directly could
never earn that the honest way.

Both functions are thin and raise RuntimeError on any failure — bridge.py
decides what a failure means (skip this cycle, fail the run, ...); this file
only speaks to the Worker.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CALL_VERB = REPO / "tools" / "call-verb.py"
DEFAULT_ROOM = "partner-line"


def _run_verb(verb: str, args: dict, *, call_verb_path: Path = CALL_VERB,
              timeout: float = 30.0) -> dict:
    proc = subprocess.run(
        [sys.executable, str(call_verb_path), verb, json.dumps(args)],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{verb} failed (rc={proc.returncode}): {proc.stderr.strip()[:2000]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{verb} returned non-JSON stdout: {proc.stdout[:500]!r}") from e


def read_room(after_seq: int, *, room: str = DEFAULT_ROOM, limit: int = 50,
              call_verb_path: Path = CALL_VERB) -> dict:
    return _run_verb(
        "read-room", {"room": room, "after_seq": after_seq, "limit": limit},
        call_verb_path=call_verb_path,
    )


def add_room_turn(body: str, seat: str, *, kind: str = "turn", room: str = DEFAULT_ROOM,
                   msg_id: str | None = None, call_verb_path: Path = CALL_VERB) -> dict:
    args = {
        "idempotency_key": str(uuid.uuid4()),
        "body": body,
        "seat": seat,
        "room": room,
        "kind": kind,
        "msg_id": msg_id or str(uuid.uuid4()),
    }
    return _run_verb("add-room-turn", args, call_verb_path=call_verb_path)
