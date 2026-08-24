#!/usr/bin/env python3
"""Read-only Hermes-to-room Queue projection.

Hermes remains authoritative.  This module opens its live SQLite database with
``mode=ro`` and ``PRAGMA query_only=ON``, turns durable task events into compact
state-complete room receipts, and advances its cursor only after the room
accepts the deterministic receipt id.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BOARD = "carr-build"
DB_PATH = Path(os.environ.get("CARR_HERMES_KANBAN_DB",
    Path.home() / ".hermes" / "kanban" / "boards" / BOARD / "kanban.db"))
EVENT_NAMESPACE = uuid.UUID("42f7b149-34a7-512d-bad1-b9ad6f35d4c4")
EVENT_LIMIT = 200


def event_msg_id(board: str, event_id: int) -> str:
    return str(uuid.uuid5(EVENT_NAMESPACE, f"{board}:{event_id}"))


def _iso(epoch: object) -> str:
    try:
        return datetime.fromtimestamp(int(epoch), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return datetime.fromtimestamp(0, timezone.utc).isoformat().replace("+00:00", "Z")


def _target_for(task: dict, target_catalog: dict) -> tuple[str, str | None]:
    for alias, entry in target_catalog.items():
        if isinstance(entry, dict) and entry.get("assignee") == task.get("assignee"):
            return alias, entry.get("effective_model")
    return "unassigned", None


def _source_seq(task: dict) -> int | None:
    body = task.get("body")
    if not isinstance(body, str) or not body.startswith("[CARR_QUEUE_META "):
        return None
    try:
        payload = json.loads(body.split("]", 1)[0][len("[CARR_QUEUE_META "):])
        value = payload.get("source_seq")
        return value if isinstance(value, int) and value >= 0 else None
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


def _priority(value: object) -> str:
    try:
        return f"P{max(0, min(4, 4 - int(value)))}"
    except (TypeError, ValueError):
        return "P2"


def card_for(task: dict, *, target_catalog: dict, updated_at: object) -> dict:
    target, model = _target_for(task, target_catalog)
    return {
        "title": str(task.get("title") or "Untitled")[:200],
        "target": target,
        "effective_model": model,
        "status": str(task.get("status") or "triage")[:32],
        "priority": _priority(task.get("priority")),
        "cap": "read",  # Slice 1 is intentionally read-only.
        "updated_at": _iso(updated_at),
        "source_seq": _source_seq(task),
    }


def _summary(event: dict, task: dict) -> str:
    title = str(task.get("title") or "this task")[:200]
    status = str(task.get("status") or "updated")
    kind = str(event.get("kind") or "updated")
    if status == "running": return f"{title} started."
    if status == "review": return f"{title} is ready for review."
    if status == "blocked": return f"{title} is blocked."
    if status == "done": return f"{title} finished."
    return f"{title} {kind.replace('_', ' ')}."


def receipt_for(event: dict, task: dict, *, target_catalog: dict, board: str = BOARD) -> dict:
    event_id = int(event["id"])
    card = card_for(task, target_catalog=target_catalog,
                    updated_at=event.get("created_at") or task.get("created_at"))
    return {"queue_event": {
        "v": 1, "board": board, "event_id": event_id,
        "event": str(event.get("kind") or "updated")[:48],
        "task_id": str(task["id"]), "card": card,
        "summary": _summary(event, task)[:300],
        "projected_at": _iso(event.get("created_at") or task.get("created_at")),
    }}


def current_cards(tasks: list[dict], *, target_catalog: dict) -> list[dict]:
    return [
        {"task_id": str(task["id"]), **card_for(task, target_catalog=target_catalog,
          updated_at=task.get("completed_at") or task.get("started_at") or task.get("created_at"))}
        for task in tasks if str(task.get("status")) != "archived"
    ]


def open_reader(path: Path = DB_PATH) -> sqlite3.Connection:
    # immutable=1 is deliberately prohibited: Hermes writes through WAL and an
    # immutable reader can miss its live changes.
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def project_once(*, state: dict, add_room_turn, target_catalog: dict,
                 db_path: Path = DB_PATH, board: str = BOARD, limit: int = EVENT_LIMIT) -> list[dict]:
    cursor = int(state.get("queue_event_cursor", 0) or 0)
    projected: list[dict] = []
    with open_reader(db_path) as conn:
        events = conn.execute(
            "select id, task_id, kind, created_at from task_events where id > ? order by id asc limit ?",
            (cursor, limit)).fetchall()
        for event_row in events:
            event = dict(event_row)
            task_row = conn.execute("select * from tasks where id=?", (event["task_id"],)).fetchone()
            # A deleted row cannot be projected. It is nonetheless observed so
            # the cursor does not retry an impossible history forever.
            if task_row is None:
                state["queue_event_cursor"] = event["id"]
                continue
            task = dict(task_row)
            receipt = receipt_for(event, task, target_catalog=target_catalog, board=board)
            add_room_turn(body=json.dumps(receipt, separators=(",", ":")), seat="hermes",
                          kind="receipt", msg_id=event_msg_id(board, event["id"]))
            # Only a successful, idempotent room append makes this event safe to
            # advance; an exception intentionally leaves it for the next cycle.
            state["queue_event_cursor"] = event["id"]
            state["queue_projection_digest"] = event_msg_id(board, event["id"])
            projected.append(receipt)
    return projected
