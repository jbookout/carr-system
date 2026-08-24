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
    assert queue_projection.event_msg_id("carr-build", 41) == queue_projection.event_msg_id("carr-build", 41)
    assert queue_projection.event_msg_id("carr-build", 41) != queue_projection.event_msg_id("carr-build", 42)

    cards = queue_projection.current_cards([task("t_live", "running"), task("t_old", "archived")],
        target_catalog={"sol": {"assignee": "desk:codex-desk", "effective_model": "gpt-5.6-sol"}})
    assert [card["task_id"] for card in cards] == ["t_live"], "archived cards never project"
    print("all queue projection unit tests passed")


if __name__ == "__main__":
    main()
