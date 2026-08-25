#!/usr/bin/env python3
"""The room bridge's supported, canonical seam into Hermes kanban.

Hermes owns task state.  This adapter uses its CLI for mutations and JSON reads
for discovery; it never reaches into kanban.db and never creates a fallback
queue when Hermes is unavailable.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List

import queue_grammar


BOARD = "carr-build"
PROJECT = "carr"
CATALOG_PATH = Path(__file__).with_name("queue-targets.json")


class QueueError(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError("queue_unavailable", f"queue target catalog could not be read: {exc}") from exc
    targets = data.get("targets") if isinstance(data, dict) else None
    if data.get("v") != 1 or not isinstance(targets, dict):
        raise QueueError("queue_unavailable", "queue target catalog is not version 1")
    for alias, entry in targets.items():
        if not queue_grammar.SLUG.fullmatch(alias) or not isinstance(entry, dict):
            raise QueueError("queue_unavailable", "queue target catalog has an invalid target")
        if entry.get("adapter") not in {"desk", "hermes", "manual"} or not isinstance(entry.get("assignee"), str):
            raise QueueError("queue_unavailable", f"queue target {alias!r} has no valid adapter")
        if not isinstance(entry.get("capabilities"), list):
            raise QueueError("queue_unavailable", f"queue target {alias!r} has no capabilities")
    return data


def _subprocess_runner(argv: list[str]) -> dict:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueueError("queue_unavailable", f"Hermes queue is unavailable: {exc}") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[0:1]
        raise QueueError("queue_unavailable", f"Hermes queue is unavailable: {' '.join(detail)[:400]}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise QueueError("queue_unavailable", "Hermes queue returned invalid JSON") from exc


def _command_runner(argv: list[str]) -> str:
    """Run one supported Hermes mutation without interpreting human output."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QueueError("queue_unavailable", f"Hermes queue is unavailable: {exc}") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()[0:1]
        raise QueueError("queue_unavailable", f"Hermes queue mutation failed: {' '.join(detail)[:400]}")
    return completed.stdout


def _find_task_id(value) -> str | None:
    if isinstance(value, dict):
        task_id = value.get("task_id") or value.get("id")
        if isinstance(task_id, str) and task_id.startswith("t_"):
            return task_id
        for nested in value.values():
            found = _find_task_id(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_task_id(nested)
            if found:
                return found
    return None


class KanbanAdapter:
    def __init__(self, *, runner=_subprocess_runner, command_runner=_command_runner):
        self.runner = runner
        self.command_runner = command_runner

    def create(self, command: dict, turn: dict, target: dict) -> dict:
        meta = {
            "v": 1, "target": command["target"], "cap": command["cap"],
            "source_seq": turn.get("seq"), "source_msg_id": turn.get("msg_id"),
            "finish": command["finish"],
        }
        task_body = f"[CARR_QUEUE_META {json.dumps(meta, separators=(',', ':'))}]\n{command['body']}".rstrip()
        argv = [
            "hermes", "kanban", "--board", BOARD, "create", "--project", PROJECT,
            "--assignee", target["assignee"], "--priority", str(command["priority"]),
            "--max-runtime", command["runtime"], "--idempotency-key", command["idempotency_key"],
            "--created-by", str(turn.get("seat") or "room"), "--body", task_body,
        ]
        if command.get("after"):
            argv.extend(["--parent", command["after"]])
        if target.get("model"):
            argv.extend(["--model", target["model"]])
        if target.get("provider"):
            argv.extend(["--provider", target["provider"]])
        if command.get("manual"):
            # Hermes creates this manual card blocked atomically.  It can never
            # appear in a ready list between a create and a later transition.
            argv.extend(["--initial-status", "blocked"])
        argv.extend(["--json", command["title"]])
        raw = self.runner(argv)
        task_id = _find_task_id(raw)
        if not task_id:
            raise QueueError("queue_unavailable", "Hermes create returned no task id")
        return {"task_id": task_id, "created": bool(raw.get("created", True)), "raw": raw}

    def show(self, task_id: str) -> dict:
        return self.runner(["hermes", "kanban", "--board", BOARD, "show", task_id, "--json"])

    def list(self, state: str, target: str | None, catalog: dict) -> dict:
        argv = ["hermes", "kanban", "--board", BOARD, "list", "--status", state, "--json"]
        if target:
            argv.extend(["--assignee", catalog["targets"][target]["assignee"]])
        return self.runner(argv)

    def ready_for(self, assignee: str) -> List[dict]:
        payload = self.runner([
            "hermes", "kanban", "--board", BOARD, "list", "--status", "ready",
            "--assignee", assignee, "--sort", "created", "--json",
        ])
        if not isinstance(payload, list):
            raise QueueError("queue_unavailable", "Hermes ready list returned an invalid shape")
        return [row for row in payload if isinstance(row, dict)]

    def claim(self, task_id: str) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "claim", task_id, "--ttl", "900",
        ])

    def comment(self, task_id: str, summary: str) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "comment", task_id, summary,
            "--author", "queue-dispatch",
        ])

    def complete(self, task_id: str, summary: str, metadata: dict) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "complete", task_id,
            "--result", summary, "--summary", summary,
            "--metadata", json.dumps(metadata, separators=(",", ":")),
        ])

    def request_review(self, task_id: str, summary: str, metadata: dict) -> None:
        self.command_runner([
            "hermes", "kanban", "--board", BOARD, "request-review", task_id,
            "--summary", summary,
            "--metadata", json.dumps(metadata, separators=(",", ":")),
        ])

    def block(self, task_id: str, reason: str, *, kind: str | None = None) -> None:
        argv = ["hermes", "kanban", "--board", BOARD, "block", task_id]
        if kind:
            argv.extend(["--kind", kind])
        argv.append(reason)
        self.command_runner(argv)


def public_targets(catalog: dict) -> list[dict]:
    return [
        {"alias": alias, "enabled": bool(entry.get("enabled")),
         "capabilities": entry.get("capabilities", []),
         "effective_model": entry.get("effective_model"),
         "unavailable_reason": entry.get("unavailable_reason") if not entry.get("enabled") else None}
        for alias, entry in sorted(catalog["targets"].items())
    ]


def bounded_status(payload: object, *, limit: int = 50) -> dict:
    """Return only display-safe card fields from a Hermes read.

    Status is a discovery receipt, not a second copy of task bodies, comments,
    worker output, or attachments.  Recursive collection tolerates the small
    shape differences between Hermes ``show`` and ``list`` JSON responses.
    """
    rows: list[dict] = []

    def visit(value: object) -> None:
        if len(rows) >= limit:
            return
        if isinstance(value, dict):
            task_id = value.get("task_id") or value.get("id")
            if isinstance(task_id, str) and task_id.startswith("t_"):
                row = {"task_id": task_id}
                for key in ("title", "status", "assignee", "priority", "updated_at"):
                    item = value.get(key)
                    if isinstance(item, (str, int)):
                        row[key] = str(item)[:200]
                rows.append(row)
                return
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return {"tasks": rows, "truncated": len(rows) >= limit}


class QueueService:
    def __init__(self, *, catalog: dict | None = None, adapter: KanbanAdapter | None = None):
        self.catalog = catalog or load_catalog()
        self.adapter = adapter or KanbanAdapter()

    def handle(self, turn: dict, *, room: str) -> dict:
        parsed = queue_grammar.parse({**turn, "room": room}, self.catalog)
        if parsed.kind == "not_command":
            return {"handled": False}
        source = {"source_seq": turn.get("seq"), "source_msg_id": turn.get("msg_id")}
        if parsed.kind == "rejected":
            return {"handled": True, "kind": "rejected", "receipt": {"queue_rejected": {
                **source, "code": parsed.code, "reason": parsed.reason, "hint": parsed.hint,
            }}}
        if parsed.kind == "targets":
            return {"handled": True, "kind": "targets", "receipt": {"queue_targets": {
                **source, "targets": public_targets(self.catalog),
            }}}
        try:
            if parsed.kind == "status":
                value = parsed.value or {}
                payload = self.adapter.show(value["id"]) if "id" in value else self.adapter.list(
                    value["state"], value.get("target"), self.catalog)
                return {"handled": True, "kind": "status", "receipt": {"queue_status": {
                    **source, "query": value, "result": bounded_status(payload),
                }}}
            assert parsed.kind == "enqueue" and parsed.value is not None
            command = parsed.value
            created = self.adapter.create(command, turn, self.catalog["targets"][command["target"]])
            return {"handled": True, "kind": "accepted", "receipt": {"queue_accepted": {
                **source, "task_id": created["task_id"], "target": command["target"],
                "cap": command["cap"], "idempotency_key": command["idempotency_key"],
                "status": "blocked" if command.get("manual") and created["created"] else
                          ("created" if created["created"] else "duplicate"),
            }}}
        except QueueError as exc:
            return {"handled": True, "kind": "rejected", "receipt": {"queue_rejected": {
                **source, "code": exc.code, "reason": exc.reason, "hint": "Try again after Hermes is available",
            }}}
