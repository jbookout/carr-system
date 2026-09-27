#!/usr/bin/env python3
"""Bridge-level contract for session presence and the Codex Desktop delivery fix.

Two things are proven here, end to end through bridge.run_once with fakes:

1. THE ROOT CAUSE OF THE 2026-09-27 NON-DELIVERY. A codex-session desk bound to
   a thread that Codex Desktop holds open cannot be reached by `codex exec
   resume` (the thread already has an active writer). dispatch now asks the
   Desktop IPC router first and, when the thread has an owner, starts the turn
   inside that owner. The bridge treats that as a live delivery (the session
   answers in its own window), never as a failure receipt.

2. PRESENCE THROUGH THE WIRE. A session_presence receipt read from the room
   lands in the session directory, an @-addressed turn reaches that session,
   the heartbeat carries the session roster, and the existing desks keep
   routing exactly as before.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import desks  # noqa: E402
import dispatch  # noqa: E402
import session_directory as sd  # noqa: E402

THREAD = "01a0e2da-dabe-7642-a06a-1a686394537b"


class Room:
    def __init__(self):
        self.calls = []

    def add_room_turn(self, *, body, seat, kind="turn", room="model-room", msg_id=None,
                      idempotency_key=None):
        self.calls.append({"body": body, "seat": seat, "kind": kind})
        return {"ok": True}


class ReadRoom:
    def __init__(self, batches):
        self.batches = list(batches)

    def __call__(self, after_seq, *, room="model-room", limit=50):
        return {"turns": self.batches.pop(0) if self.batches else []}


class NoQueue:
    catalog: dict = {"targets": {}}

    @staticmethod
    def handle(turn, *, room):
        return {"handled": False}

    @staticmethod
    def reconcile_disabled_targets():
        return {"scanned": 0, "blocked": [], "diagnostics": []}


def presence_turn(seq=1):
    rec = {"schema": "session-presence/v1", "event": "announce", "runtime": "codex",
           "session_id": THREAD, "handle": "codex-6394537b", "name": "sol",
           "title": "Orchestrator", "cwd": "/w", "host": "studio", "pid": 1,
           "surface": "codex-desktop", "address": {"kind": "codex-thread", "value": THREAD},
           "announced_at": "2026-09-27T13:00:00+00:00", "beat_at": "2026-09-27T13:00:00+00:00"}
    return {"seq": seq, "seat": "codex", "kind": "receipt", "msg_id": f"p-{seq}",
            "body": json.dumps({"session_presence": rec})}


def run(tdp: Path, batches, *, dispatch_fn, deliver, desks_json):
    (tdp / "hermes-desks.json").write_text(json.dumps({"desks": desks_json}))
    reg = desks.Registry(tdp / "hermes-desks.json")
    room = Room()
    with mock.patch.object(bridge, "probe_live", return_value=True):
        summary = bridge.run_once(
            registry=reg, state_path=tdp / "state.json", room="model-room",
            read_room=ReadRoom(batches), add_room_turn=room.add_room_turn,
            dispatch_fn=dispatch_fn, desk_state_dir=tdp, probe_auth=lambda e: None,
            queue_service=cast(Any, NoQueue()), queue_projector=lambda **k: [], read_profiles=lambda: [],
            session_probe=lambda e: True, session_deliver=deliver, host="studio",
            log=lambda *a: None)
    return summary, room, reg


def test_presence_then_addressed_turn_is_delivered_and_heartbeat_lists_it():
    delivered = []

    def deliver(entry, text, msg_id):
        delivered.append((entry["handle"], text))
        return {"status": "delivered"}

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        human = {"seq": 2, "seat": "claude", "kind": "turn", "msg_id": "t-2", "body": "@sol confirm receipt"}
        summary, room, _ = run(tdp, [[presence_turn(1), human]],
                               dispatch_fn=lambda *a, **k: {"status": "completed", "result": "ok"},
                               deliver=deliver, desks_json={})
        assert delivered == [("codex-6394537b", "[model-room · claude] @sol confirm receipt")]
        assert summary["sessions"]["delivered"][0]["status"] == "delivered"
        directory = json.loads((tdp / "session-directory.json").read_text())
        assert directory["sessions"]["codex-6394537b"]["live"] is True
        beats = [json.loads(c["body"])["heartbeat"] for c in room.calls
                 if c["kind"] == "receipt" and "heartbeat" in c["body"]]
        assert beats and beats[0]["sessions"][0]["handle"] == "codex-6394537b"


def test_existing_desk_routing_is_unchanged_by_presence():
    dispatched = []

    def fake_dispatch(name, task, *, registry, results_path, fresh=False):
        dispatched.append(name)
        return {"status": "completed", "result": "desk reply"}

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        human = {"seq": 2, "seat": "human", "kind": "turn", "msg_id": "t-2", "body": "status please"}
        run(tdp, [[presence_turn(1), human]], dispatch_fn=fake_dispatch,
            deliver=lambda *a: {"status": "delivered"},
            desks_json={"codex-desk": {"kind": "codex-session", "model": "m", "cwd": "/tmp",
                                       "thread_id": None, "room_seat": "codex"}})
        # the human turn reaches the desk; the presence receipt never does
        assert dispatched == ["codex-desk"]


def test_session_alias_cannot_shadow_a_desk_seat():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        turn = presence_turn(1)
        body = json.loads(turn["body"])
        body["session_presence"]["name"] = "codex"
        turn["body"] = json.dumps(body)
        run(tdp, [[turn]], dispatch_fn=lambda *a, **k: {}, deliver=lambda *a: {},
            desks_json={"codex-desk": {"kind": "codex-session", "model": "m", "cwd": "/tmp",
                                       "thread_id": None, "room_seat": "codex"}})
        directory = json.loads((tdp / "session-directory.json").read_text())
        assert directory["sessions"]["codex-6394537b"]["name"] is None


def test_codex_desk_on_a_desktop_held_thread_goes_through_ipc():
    entry = {"name": "sol-orchestrator", "kind": "codex-session", "model": "gpt-5.6-sol",
             "effort": None, "cwd": "/w", "thread_id": THREAD}
    with mock.patch.object(dispatch.codex_ipc, "thread_owner", return_value="owner-1"), \
         mock.patch.object(dispatch.codex_ipc, "start_turn",
                           return_value={"status": "delivered", "thread_id": THREAD, "mode": "start"}) as st, \
         mock.patch.object(dispatch.subprocess, "run",
                           side_effect=AssertionError("codex exec must not run")):
        out = dispatch._to_codex(entry, "hello", None)
    assert out["status"] == "delivered" and out["thread_id"] == THREAD
    st.assert_called_once()


def test_codex_desk_without_desktop_owner_still_uses_exec_resume():
    entry = {"name": "codex-desk", "kind": "codex-session", "model": "m", "effort": None,
             "cwd": "/w", "thread_id": THREAD}
    ran = []

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    with mock.patch.object(dispatch.codex_ipc, "thread_owner", return_value=None), \
         mock.patch.object(dispatch.subprocess, "run", side_effect=lambda argv, **k: ran.append(argv) or Proc()):
        dispatch._to_codex(entry, "hello", None)
    assert ran and ran[0][:3] == ["codex", "exec", "resume"]


def test_bridge_treats_live_codex_delivery_as_delivered_not_failed():
    room = Room()

    def fake_dispatch(name, text, *, registry, results_path):
        return {"status": "delivered", "thread_id": THREAD, "detail": "turn started in Codex Desktop"}

    with tempfile.TemporaryDirectory() as td:
        out = bridge.deliver("sol-orchestrator", {"kind": "codex-session"}, "sol",
                             {"msg_id": "t-1", "seat": "claude", "body": "hi", "seq": 1},
                             state={}, registry=None, results_path=Path(td) / "r.jsonl",
                             add_room_turn=room.add_room_turn, dispatch_fn=fake_dispatch)
    assert out["outcome"] == "delivered_live"
    assert room.calls == []  # no failure receipt, no fake reply


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc!r}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: unexpected {exc!r}", file=sys.stderr)
    if failures:
        print(f"{failures} session bridge test(s) failed", file=sys.stderr)
        return 1
    print("all session bridge unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
