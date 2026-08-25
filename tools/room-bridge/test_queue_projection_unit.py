#!/usr/bin/env python3
"""Slice 2 projection contracts: Hermes state becomes safe, idempotent wire receipts."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import queue_projection  # noqa: E402


def task(task_id="t_one", status="todo", title="Queue <title>"):
    return {"id": task_id, "title": title, "status": status, "assignee": "desk:codex-desk",
            "priority": 2, "created_at": 100, "started_at": None, "completed_at": None,
            "model_override": "gpt-5.6-sol"}


def test_missing_task_with_temp_sqlite():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "kanban.db"
        conn = sqlite3.connect(path)
        conn.executescript("""
            create table tasks (id text primary key, title text, status text,
                assignee text, priority integer, created_at integer,
                started_at integer, completed_at integer, model_override text, body text);
            create table task_events (id integer primary key, task_id text,
                kind text, created_at integer);
            insert into tasks values
                ('t_live', 'Live task', 'running', 'desk:codex-desk', 2, 100, null, null, 'sol', null);
            insert into task_events values (1, 't_gone', 'deleted', 200);
            insert into task_events values (2, 't_live', 'started', 201);
        """)
        conn.commit()
        conn.close()
        state = {"queue_event_cursor": 0, "queue_projection_digest": None}
        posted = []
        queue_projection.project_once(
            state=state, db_path=path, target_catalog={"sol": {
                "assignee": "desk:codex-desk", "effective_model": "gpt-5.6-sol"}},
            add_room_turn=lambda **row: posted.append(row))
        assert [row["msg_id"] for row in posted] == [
            queue_projection.event_msg_id("carr-build", 1),
            queue_projection.event_msg_id("carr-build", 2)]
        first = json.loads(posted[0]["body"])["queue_event"]
        assert first["card"]["status"] == "missing"
        assert state["queue_event_cursor"] == 2



def main():
    test_missing_task_with_temp_sqlite()
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

    missing = queue_projection.missing_task_receipt(
        {"id": 43, "task_id": "t_deleted", "kind": "completed", "created_at": 300},
        board="carr-build")
    assert missing == {"queue_event": {
        "v": 1, "board": "carr-build", "event_id": 43,
        "event": "completed", "task_id": "t_deleted",
        "card": {"title": "Task unavailable", "target": "unassigned",
                  "effective_model": None, "status": "missing", "priority": "P2",
                  "cap": "read", "updated_at": "1970-01-01T00:05:00Z",
                  "source_seq": None},
        "summary": "Task record unavailable.",
        "projected_at": "1970-01-01T00:05:00Z",
    }}, "missing-task receipts contain only fixed safe fields"

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
                return Rows([
                    {"id": 42, "task_id": "t_deleted", "kind": "deleted", "created_at": 200},
                    {"id": 43, "task_id": "t_one", "kind": "started", "created_at": 201},
                ])
            if "where id" in sql.lower():
                return Rows([] if _args[0] == "t_deleted" else [task()])
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
    assert seen_ids == [queue_projection.event_msg_id("carr-build", 42),
                        queue_projection.event_msg_id("carr-build", 42),
                        queue_projection.event_msg_id("carr-build", 43)]
    assert state["queue_event_cursor"] == 43
    assert state["queue_projection_digest"] == queue_projection.event_msg_id("carr-build", 43)
    print("all queue projection unit tests passed")


if __name__ == "__main__":
    main()
