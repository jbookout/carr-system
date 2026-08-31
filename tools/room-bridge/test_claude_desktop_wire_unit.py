#!/usr/bin/env python3
"""Hermetic proof of Model Room -> Claude background -> Desktop delivery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import claude_desktop_wire as wire  # noqa: E402
import desks  # noqa: E402
import dispatch  # noqa: E402
import state as state_mod  # noqa: E402


SID = "12345678-1234-4123-8123-123456789abc"
FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{label}: {exc!r}")
        print(f"  FAIL  {label}\n          {exc!r}")
    else:
        print(f"  ok    {label}")


def test_launch_is_named_durable_and_backgrounded() -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1:4] == ["agents", "--json", "--all"]:
            payload = [{"id": "12345678", "sessionId": SID,
                        "kind": "background", "state": "running"}]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="backgrounded · 12345678", stderr="")

    out = wire.launch_background(
        {"model": "opus", "effort": "high", "cwd": "/tmp", "permission_mode": "dontAsk"},
        "Read Model Room seq 6606", request_id=SID, run=run,
    )
    argv, kwargs = calls[0]
    assert argv[:2] == ["claude", "--bg"]
    assert "--session-id" not in argv
    assert argv[argv.index("--name") + 1] == "model-room-12345678"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert kwargs["cwd"] == "/tmp" and kwargs["stdin"] is subprocess.DEVNULL
    assert out == {"status": "delivered", "session_id": SID,
                   "session_short_id": "12345678",
                   "session_name": "model-room-12345678", "transport": "claude-desktop"}


def test_supervisor_status_is_uuid_bound() -> None:
    payload = [{"sessionId": SID, "id": "12345678", "kind": "background", "state": "completed"}]

    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    assert wire.inspect_session(SID, run=run) == {
        "session_id": SID, "state": "completed", "found": True, "kind": "background"
    }


def test_final_typed_result_comes_from_persisted_transcript() -> None:
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "project" / f"{SID}.jsonl"
        path.parent.mkdir()
        rows = [
            {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [
                {"type": "text", "text": "working"}]}},
            {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
                {"type": "text", "text": "Done\nCARR_QUEUE_RESULT {\"v\":1}"}]}},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        assert wire.read_final_text(SID, transcript_root=Path(root)) == (
            'Done\nCARR_QUEUE_RESULT {"v":1}'
        )


def test_desktop_handoff_uses_attached_pty_slash_command() -> None:
    with tempfile.TemporaryDirectory() as root:
        fake = Path(root) / "claude"
        capture = Path(root) / "input.txt"
        fake.write_text(
            "#!/bin/sh\n"
            "printf 'ready\\n'\n"
            "IFS= read -r line\n"
            f"printf '%s' \"$line\" > {capture}\n"
            "[ \"$line\" = '/desktop' ]\n"
        )
        fake.chmod(0o755)
        assert wire.handoff_to_desktop(SID, claude_bin=str(fake), timeout_s=5) == {
            "status": "opened", "session_id": SID
        }
        assert capture.read_text() == "/desktop"


def test_registry_dispatch_and_queue_only_fanout() -> None:
    with tempfile.TemporaryDirectory() as root:
        reg = desks.Registry(Path(root) / "desks.json")
        entry = reg.register("claude-desktop", "claude-desktop", model="opus",
                             effort="high", cwd=root)
        assert entry["permission_mode"] == "dontAsk"
        raw = reg._load()  # local fixture readback
        raw["desks"]["claude-desktop"]["room_seat"] = "claude"
        raw["desks"]["ordinary"] = {
            "kind": "claude-session", "room_seat": "reviewer", "socket": "/tmp/named.sock"
        }
        reg._save(raw)
        seats = bridge.conversational_desk_seats(reg.entries())
        assert seats == {"ordinary": "reviewer"}

        original = dispatch.claude_desktop_wire.launch_background
        setattr(dispatch.claude_desktop_wire, "launch_background", lambda _entry, _task: {
            "status": "delivered", "session_id": SID, "transport": "claude-desktop"
        })
        try:
            row = dispatch.dispatch("claude-desktop", "do review", registry=reg,
                                    results_path=Path(root) / "results.jsonl")
        finally:
            setattr(dispatch.claude_desktop_wire, "launch_background", original)
        assert row["session_id"] == SID and row["status"] == "delivered"


def test_completed_pending_hands_off_then_finishes_queue() -> None:
    state = state_mod.default_state()
    state_mod.set_pending(
        state, "claude-desktop", dispatch_msg_id="dispatch-1", log_offset=0,
        injected_at="2026-08-31T12:00:00+00:00", source_msg_id="queue:t_queue0001",
        source_seq=1, origin_kind="queue", kanban_task_id="t_queue0001",
        target="claude-desktop", finish="review", cap="read",
        transport="claude-desktop", session_id=SID,
    )
    handed: list[str] = []
    finished: list[tuple[dict, str]] = []
    posted: list[dict] = []

    class Executor:
        def finish_pending(self, pending: dict, raw_result: str) -> dict:
            finished.append((pending, raw_result))
            return {"outcome": "review", "task_id": "t_queue0001"}

    def post(**row) -> None:
        posted.append(row)

    def handoff(session_id: str) -> dict:
        handed.append(session_id)
        return {"status": "opened"}

    executor = Executor()
    result = 'Reviewed\nCARR_QUEUE_RESULT {"v":1}'
    outcome = bridge.handle_pending(
        "claude-desktop", "claude", state,
        add_room_turn=post, log_path=Path("unused"),
        pending_timeout_s=1800, queue_executor=cast(Any, executor),
        inspect_background=lambda _sid: {"state": "completed", "found": True},
        read_background_result=lambda _sid: result,
        handoff_background=handoff,
    )
    assert outcome is not None
    assert outcome["outcome"] == "review"
    assert handed == [SID] and finished[0][1] == result
    assert json.loads(posted[0]["body"])["claude_desktop_handoff"]["status"] == "opened"
    assert state_mod.get_pending(state, "claude-desktop") is None


def main() -> int:
    check("launch is named, durable, and backgrounded", test_launch_is_named_durable_and_backgrounded)
    check("supervisor status is UUID-bound", test_supervisor_status_is_uuid_bound)
    check("typed result is read from persisted transcript", test_final_typed_result_comes_from_persisted_transcript)
    check("Desktop handoff uses attached PTY slash command", test_desktop_handoff_uses_attached_pty_slash_command)
    check("desktop desk is queue-only and dispatchable", test_registry_dispatch_and_queue_only_fanout)
    check("completed background hands off before queue completion", test_completed_pending_hands_off_then_finishes_queue)
    print(f"claude-desktop-wire unit: {6 - len(FAILURES)}/6 passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
