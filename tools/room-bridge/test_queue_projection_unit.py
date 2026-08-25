#!/usr/bin/env python3
"""Slice 2 projection contracts: Hermes state becomes safe, idempotent wire receipts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import queue_projection  # noqa: E402


def task(task_id="t_one", status="todo", title="Queue <title>"):
    return {"id": task_id, "title": title, "status": status, "assignee": "desk:codex-desk",
            "priority": 2, "created_at": 100, "started_at": None, "completed_at": None,
            "model_override": "gpt-5.6-sol"}


def main():
    event = {"id": 41, "task_id": "t_one", "kind": "started", "created_at": 200}
    receipt = queue_projection.receipt_for(event, task(), target_catalog={"sol": {
        "assignee": "desk:codex-desk", "effective_model": "gpt-5.6-sol"}}, board="carr-build")
    payload = receipt["queue_event"]
    assert payload["event_id"] == 41
    assert payload["event"] == "started"
    assert payload["card"]["status"] == "todo"
    assert payload["card"]["title"] == "Queue <title>", "the wire carries data; the UI escapes it"
    record_task = {**task(), "body": '[CARR_QUEUE_META {"v":1,"target":"claude","cap":"record-write","source_seq":1,"source_msg_id":"m","finish":"review"}]\nWrite one record.'}
    record_card = queue_projection.card_for(record_task, target_catalog={"claude": {
        "assignee": "desk:joe-desk", "effective_model": "claude"}}, updated_at=200)
    assert record_card["cap"] == "record-write", "yellow work must not project as misleading read-only work"
    assert queue_projection.event_msg_id("carr-build", 41) == queue_projection.event_msg_id("carr-build", 41)
    assert queue_projection.event_msg_id("carr-build", 41) != queue_projection.event_msg_id("carr-build", 42)

    cards = queue_projection.current_cards([task("t_live", "running"), task("t_old", "archived")],
        target_catalog={"sol": {"assignee": "desk:codex-desk", "effective_model": "gpt-5.6-sol"}})
    assert [card["task_id"] for card in cards] == ["t_live"], "archived cards never project"

    class Rows:
        def __init__(self, values):
            self.values = values

        def fetchall(self):
            return self.values

        def fetchone(self):
            return self.values[0] if self.values else None

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, _args=()):
            if "from task_events" in sql:
                return Rows([{"id": 42, "task_id": "t_one", "kind": "started", "created_at": 200}])
            return Rows([task()])

    def open_fake_reader(_path: Path) -> Reader:
        return Reader()

    original_reader = queue_projection.open_reader
    queue_projection.open_reader = open_fake_reader
    state = {"queue_event_cursor": 0, "queue_projection_digest": None}
    seen_ids = []
    try:
        try:
            queue_projection.project_once(
                state=state,
                add_room_turn=lambda **kwargs: seen_ids.append(kwargs["msg_id"]) or (_ for _ in ()).throw(RuntimeError("crash after receipt")),
                target_catalog={"sol": {"assignee": "desk:codex-desk", "effective_model": "gpt-5.6-sol"}},
            )
        except RuntimeError:
            pass
        assert state["queue_event_cursor"] == 0, "receipt-before-cursor crash must replay"
        queue_projection.project_once(
            state=state,
            add_room_turn=lambda **kwargs: seen_ids.append(kwargs["msg_id"]) or {"seq": 1},
            target_catalog={"sol": {"assignee": "desk:codex-desk", "effective_model": "gpt-5.6-sol"}},
        )
    finally:
        queue_projection.open_reader = original_reader
    assert seen_ids == [queue_projection.event_msg_id("carr-build", 42)] * 2
    assert state["queue_event_cursor"] == 42
    print("all queue projection unit tests passed")


if __name__ == "__main__":
    main()
