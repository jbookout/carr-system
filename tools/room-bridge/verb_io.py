#!/usr/bin/env python3
"""verb_io.py — the room bridge's one path to the record layer: shell to
tools/call-verb.py, exactly the documented `./run.sh call <verb> '<json>'`
route (HTTPS to the deployed Worker, server-derived identity). Ordinary room
traffic uses LOCAL_TOKENS; only shape-checked Queue projection uses the locked
Hermes token selector below. Never a direct database connection — same stance
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
import os
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CALL_VERB = REPO / "tools" / "call-verb.py"
DEFAULT_ROOM = "partner-line"


def _run_verb(verb: str, args: dict, *, call_verb_path: Path = CALL_VERB,
              timeout: float = 30.0, client_profile: str | None = None) -> dict:
    env = dict(os.environ)
    # An inherited selector must never silently turn normal bridge traffic into
    # Hermes traffic.  Only the projector call below opts into that credential.
    env.pop("CARR_MCP_CLIENT_PROFILE", None)
    if client_profile is not None:
        env["CARR_MCP_CLIENT_PROFILE"] = client_profile
    proc = subprocess.run(
        [sys.executable, str(call_verb_path), verb, json.dumps(args)],
        capture_output=True, text=True, timeout=timeout, env=env,
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


def read_profiles(*, call_verb_path: Path = CALL_VERB) -> list:
    """The named-agent roster (loop 520), in the exact compact shape the
    heartbeat republishes: key, name, model, desk, status per profile. Raises
    on any failure — the caller (bridge.run_once) degrades to an absent
    roster key rather than a dead heartbeat."""
    result = _run_verb("read-profiles", {}, call_verb_path=call_verb_path)
    profiles = result.get("profiles")
    if not isinstance(profiles, list):
        raise RuntimeError(f"read-profiles returned no profile list: {str(result)[:300]!r}")
    return [
        {
            "key": p.get("profile_key"),
            "name": p.get("display_name"),
            "model": p.get("current_model"),
            "desk": p.get("current_desk"),
            "status": p.get("status"),
        }
        for p in profiles
    ]


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


def project_room_queue(body: str, seat: str = "hermes", *, kind: str = "receipt",
                       room: str = DEFAULT_ROOM, msg_id: str | None = None,
                       call_verb_path: Path = CALL_VERB,
                       client_profile: str = "hermes-projector") -> dict:
    """Append one Queue projection with the dedicated Hermes bearer.

    No secret enters argv: local-verb selects CARR_HERMES_MCP_TOKEN from the
    existing 600-mode token file, and the Worker derives hermes-pilot from it.
    The append response is checked against the reader's exact provenance before
    the projector may record local success.
    """
    if seat != "hermes" or kind != "receipt" or room != DEFAULT_ROOM or msg_id is None:
        raise RuntimeError("project-room-queue requires fixed partner-line/hermes/receipt provenance and msg_id")
    args = {"idempotency_key": str(uuid.uuid4()), "body": body, "msg_id": msg_id}
    result = _run_verb("project-room-queue", args, call_verb_path=call_verb_path,
                       client_profile=client_profile)
    expected = {"room": DEFAULT_ROOM, "sponsor": "joe", "seat": "hermes", "kind": "receipt",
                "origin_channel": "mcp", "origin_actor": "hermes-pilot", "msg_id": msg_id.lower()}
    if any(result.get(key) != value for key, value in expected.items()):
        raise RuntimeError("project-room-queue rejected provenance: append receipt is not reader-compatible")
    return result
