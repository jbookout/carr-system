#!/usr/bin/env python3
"""Canonical controller for Hermes queue tasks addressed to named desks.

Hermes remains the only task-state authority.  This module reads ready cards,
claims one atomically, delivers it through the existing named-desk wire, and
applies only the task's admitted terminal transition.  It never calls a model
directly, never falls back to another target, and never republishes raw model
output into the partner room.

The claim uses Hermes' canonical 900-second lease.  Hermes
``release_stale_claims`` restores an expired, workerless run to its retry
phase on a dispatcher tick.  This is deliberately the recovery authority: a
second local retry ledger would create a competing task state machine.

Queue metadata is an execution-routing boundary, not a Job Passport
ExecutionEnvelope.  The controller therefore does not fabricate Work Request,
authority, state, or AttemptReceipt records from the small queue header.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Callable


META_PREFIX = "[CARR_QUEUE_META "
RESULT_PREFIX = "CARR_QUEUE_RESULT "
META_FIELDS = {"v", "target", "cap", "source_seq", "source_msg_id", "finish"}
RESULT_FIELDS = {"v", "task_id", "outcome", "summary"}
RECORD_WRITE_EVIDENCE_FIELDS = {"mcp_verb", "record_id", "readback_verb", "readback_record_id"}
TERMINAL_STATES = {"done", "review", "blocked", "archived"}
MCP_VERB = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
QUEUE_TRANSIENT_PREFIX = "queue_transient:"
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 300
RETRYABLE_DISPATCH_STATUSES = {
    "quota_exhausted": "provider_quota",
    "timed_out": "provider_unavailable",
    "failed": "provider_unavailable",
}
DESK_UNAVAILABLE_WAIT_S = 60.0


class QueueDispatchError(ValueError):
    pass


class RecordWriteEvidenceMissing(QueueDispatchError):
    pass


def _now(now: str | None = None) -> datetime:
    value = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _retry_at(*, attempt: int, now: str | None) -> str:
    delay = min(RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)), RETRY_MAX_SECONDS)
    return (_now(now) + timedelta(seconds=delay)).isoformat(timespec="seconds")


def _dispatch_failure_code(value: object) -> str:
    if isinstance(value, str) and value in RETRYABLE_DISPATCH_STATUSES:
        return RETRYABLE_DISPATCH_STATUSES[value]
    return "provider_unavailable"


def validate_execution_catalog(catalog: dict) -> dict:
    targets = catalog.get("targets") if isinstance(catalog, dict) else None
    if catalog.get("v") != 1 or not isinstance(targets, dict):
        raise QueueDispatchError("execution catalog must be version 1")
    for alias, target in targets.items():
        if not isinstance(alias, str) or not isinstance(target, dict):
            raise QueueDispatchError("execution catalog target is invalid")
        adapter = target.get("adapter")
        assignee = target.get("assignee")
        if adapter not in {"desk", "hermes", "manual"} or not isinstance(assignee, str):
            raise QueueDispatchError(f"target {alias!r} has no supported adapter")
        if adapter == "desk":
            if not assignee.startswith("desk:") or not isinstance(target.get("desk"), str):
                raise QueueDispatchError(f"desk target {alias!r} is not named explicitly")
        elif assignee.startswith("desk:"):
            raise QueueDispatchError(f"non-desk target {alias!r} cannot use a desk assignee")
    return catalog


def _decode_exact(raw: str, fields: set[str], label: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise QueueDispatchError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict) or set(value) != fields:
        raise QueueDispatchError(f"{label} fields are invalid")
    return value


def parse_queue_task(task: dict, target_alias: str, target: dict) -> dict:
    task_id = task.get("id")
    body = task.get("body")
    if not isinstance(task_id, str) or not task_id.startswith("t_") or not isinstance(body, str):
        raise QueueDispatchError("queue task identity or body is invalid")
    first, separator, instructions = body.partition("\n")
    if not separator or not first.startswith(META_PREFIX) or not first.endswith("]"):
        raise QueueDispatchError("queue task metadata is absent")
    meta = _decode_exact(first[len(META_PREFIX):-1], META_FIELDS, "queue task metadata")
    if (meta["v"] != 1 or meta["target"] != target_alias or
            meta["cap"] not in target.get("capabilities", [])):
        raise QueueDispatchError("queue task metadata does not match its target")
    if meta["finish"] not in {"done", "review"}:
        raise QueueDispatchError("queue task finish state is invalid")
    if task.get("assignee") != target.get("assignee") or target.get("adapter") != "desk":
        raise QueueDispatchError("queue task assignee does not match its named desk")
    if not isinstance(meta["source_msg_id"], str) or not meta["source_msg_id"]:
        raise QueueDispatchError("queue task source identity is invalid")
    if (not isinstance(meta["source_seq"], int) or isinstance(meta["source_seq"], bool)
            or meta["source_seq"] < 0):
        raise QueueDispatchError("queue task source sequence is invalid")
    if not isinstance(instructions, str) or not instructions.strip():
        raise QueueDispatchError("queue task instructions are empty")
    title = task.get("title")
    if not isinstance(title, str) or not title.strip():
        raise QueueDispatchError("queue task title is invalid")
    return {"task_id": task_id, "title": title.strip(), "instructions": instructions.strip(), "meta": meta}


def parse_terminal_result(raw: str, task_id: str, cap: str = "read") -> dict:
    if not isinstance(raw, str):
        raise QueueDispatchError("terminal result is absent")
    lines = [line.strip() for line in raw.rstrip().splitlines() if line.strip()]
    if not lines or not lines[-1].startswith(RESULT_PREFIX):
        raise QueueDispatchError("terminal result line is absent")
    try:
        value = json.loads(lines[-1][len(RESULT_PREFIX):])
    except json.JSONDecodeError as exc:
        raise QueueDispatchError("terminal result is not valid JSON") from exc
    if not isinstance(value, dict):
        raise QueueDispatchError("terminal result fields are invalid")
    allowed = [RESULT_FIELDS]
    if value.get("outcome") == "blocked":
        allowed.append(RESULT_FIELDS | {"code"})
    if set(value) not in allowed:
        if cap == "record-write" and value.get("outcome") == "success" and set(value) == RESULT_FIELDS:
            raise RecordWriteEvidenceMissing("record-write evidence is absent")
        expected = RESULT_FIELDS | RECORD_WRITE_EVIDENCE_FIELDS
        if not (cap == "record-write" and value.get("outcome") == "success" and set(value) == expected):
            raise QueueDispatchError("terminal result fields are invalid")
    if value["v"] != 1 or value["task_id"] != task_id:
        raise QueueDispatchError("terminal result belongs to another task")
    if value["outcome"] not in {"success", "blocked"}:
        raise QueueDispatchError("terminal result outcome is invalid")
    summary = value["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 500 or "\n" in summary:
        raise QueueDispatchError("terminal result summary is invalid")
    value["summary"] = summary.strip()
    if value["outcome"] == "blocked" and "code" in value:
        if value["code"] != "capability_escalation_required":
            raise QueueDispatchError("terminal result block code is invalid")
    if cap == "record-write" and value["outcome"] == "success":
        for field in RECORD_WRITE_EVIDENCE_FIELDS:
            evidence = value.get(field)
            if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 200 or "\n" in evidence:
                raise RecordWriteEvidenceMissing("record-write evidence is invalid")
        if not MCP_VERB.fullmatch(value["mcp_verb"]) or not MCP_VERB.fullmatch(value["readback_verb"]):
            raise RecordWriteEvidenceMissing("record-write MCP verb evidence is invalid")
        if value["record_id"] != value["readback_record_id"]:
            raise RecordWriteEvidenceMissing("record-write read-back identifies another record")
    return value


def _task_status(payload: object) -> str | None:
    if isinstance(payload, dict):
        task = payload.get("task")
        if isinstance(task, dict) and isinstance(task.get("status"), str):
            return task["status"]
        if isinstance(payload.get("status"), str):
            return payload["status"]
    return None


class QueueDeskExecutor:
    def __init__(self, *, catalog: dict, adapter):
        self.catalog = validate_execution_catalog(catalog)
        self.adapter = adapter
        self.last_ready_task_ids: set[str] = set()
        self.last_ready_scan_complete = False

    def _target(self, alias: str) -> dict | None:
        target = self.catalog["targets"].get(alias)
        return target if isinstance(target, dict) else None

    @staticmethod
    def _prompt(parsed: dict) -> str:
        task_id = parsed["task_id"]
        evidence = ""
        if parsed["meta"]["cap"] == "record-write":
            evidence = (
                " For record-write success, also include bounded mcp_verb, record_id, readback_verb, "
                "and readback_record_id fields in that JSON; no evidence means Review or Blocked, never Done."
            )
        return (
            f"[Hermes queue {task_id}] {parsed['title']}\n\n{parsed['instructions']}\n\n"
            "Your final non-empty line must be exactly one JSON object prefixed with "
            f"CARR_QUEUE_RESULT and must bind task_id={task_id}. Allowed outcomes: success, blocked. "
            "Keep summary to one redacted sentence of at most 500 characters. If broader authority is needed, "
            "return outcome=blocked with code=capability_escalation_required." + evidence
        )

    def _retry_or_block(self, task_id: str, code: str, *, now: str | None) -> dict:
        """Use only Hermes evidence for the finite recovery bound."""
        try:
            attempts, limit = self.adapter.retry_attempt(task_id, QUEUE_TRANSIENT_PREFIX)
            if (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0
                    or not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
                raise QueueDispatchError("canonical retry evidence is invalid")
        except Exception:
            self.adapter.block(task_id, "queue_unavailable", kind="transient")
            return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
        if attempts + 1 >= limit:
            self.adapter.block(task_id, code, kind="transient")
            return {"outcome": "blocked", "task_id": task_id, "code": code}
        try:
            self.adapter.reclaim(task_id, f"{QUEUE_TRANSIENT_PREFIX}{code}")
        except Exception:
            self.adapter.block(task_id, "queue_unavailable", kind="transient")
            return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
        return {"outcome": "retry_scheduled", "task_id": task_id, "code": code,
                "retry_at": _retry_at(attempt=attempts + 1, now=now)}

    def start(self, target_alias: str, *, dispatch_call: Callable[[str], dict],
              desk_busy: bool = False, retry_at: dict[str, str] | None = None,
              now: str | None = None, desk_live: bool = True,
              unavailable_since: dict[str, str] | str | None = None,
              unavailable_wait_s: float = DESK_UNAVAILABLE_WAIT_S) -> dict:
        target = self._target(target_alias)
        self.last_ready_task_ids = set()
        self.last_ready_scan_complete = False
        if target is None or target.get("adapter") != "desk" or not target.get("enabled"):
            return {"outcome": "not_desk_target", "target": target_alias}
        if desk_busy:
            return {"outcome": "desk_busy", "target": target_alias}

        candidates = []
        for row in self.adapter.ready_for(target["assignee"]):
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].startswith("t_"):
                self.last_ready_task_ids.add(row["id"])
            try:
                parsed = parse_queue_task(row, target_alias, target)
            except QueueDispatchError:
                continue
            candidates.append((int(row.get("created_at") or 0), parsed["task_id"], parsed))
        self.last_ready_scan_complete = True
        if not candidates:
            return {"outcome": "idle", "target": target_alias}
        current_time = _now(now)
        ready_candidates = []
        delayed_candidates = []
        for candidate in candidates:
            scheduled_at = (retry_at or {}).get(candidate[1])
            if scheduled_at is None:
                ready_candidates.append(candidate)
                continue
            if not isinstance(scheduled_at, str):
                self.adapter.block(candidate[1], "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": candidate[1], "code": "queue_unavailable"}
            try:
                due = _now(scheduled_at)
            except (TypeError, ValueError):
                self.adapter.block(candidate[1], "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": candidate[1], "code": "queue_unavailable"}
            if due > current_time:
                delayed_candidates.append((due, candidate, scheduled_at))
            else:
                ready_candidates.append(candidate)
        if not ready_candidates:
            due, candidate, scheduled_at = min(delayed_candidates)
            return {"outcome": "retry_wait", "task_id": candidate[1], "target": target_alias,
                    "retry_at": scheduled_at}
        _created, task_id, parsed = min(ready_candidates)

        # A socket-backed desk that is known dead gets a timing-only grace
        # window.  Crucially this happens before claim, so no Hermes attempt,
        # dispatch, retry, or reassignment is manufactured while waiting.
        if desk_live is False:
            task_unavailable_since = (
                unavailable_since.get(task_id) if isinstance(unavailable_since, dict)
                else unavailable_since
            )
            if not isinstance(unavailable_wait_s, (int, float)) or isinstance(unavailable_wait_s, bool) \
                    or unavailable_wait_s <= 0 or unavailable_wait_s > 3600:
                self.adapter.block(task_id, "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
            if task_unavailable_since is None:
                return {"outcome": "desk_unavailable_wait", "task_id": task_id,
                        "target": target_alias, "unavailable_since": _now(now).isoformat()}
            try:
                first_dead = _now(task_unavailable_since)
            except (TypeError, ValueError, OverflowError):
                self.adapter.block(task_id, "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
            elapsed = (_now(now) - first_dead).total_seconds()
            if elapsed < 0:
                self.adapter.block(task_id, "queue_unavailable", kind="transient")
                return {"outcome": "blocked", "task_id": task_id, "code": "queue_unavailable"}
            if elapsed < unavailable_wait_s:
                return {"outcome": "desk_unavailable_wait", "task_id": task_id,
                        "target": target_alias, "unavailable_since": task_unavailable_since}
            self.adapter.block(task_id, "desk_unavailable", kind="transient")
            return {"outcome": "blocked", "task_id": task_id, "code": "desk_unavailable"}

        # The canonical claim is the race boundary. No dispatch happens first.
        try:
            self.adapter.claim(task_id)
        except Exception:
            # Another controller may have won the atomic claim, or Hermes may
            # have become unavailable. Either way, dispatching would be wrong.
            return {"outcome": "claim_not_acquired", "task_id": task_id, "target": target_alias}
        try:
            row = dispatch_call(self._prompt(parsed))
        except Exception as exc:  # dispatch details may contain provider output; never persist them here
            error_code = getattr(exc, "code", None)
            if error_code == "queue_unavailable":
                code = "queue_unavailable"
            elif isinstance(error_code, str):
                code = "desk_unavailable"
            else:
                code = "provider_unavailable"
            return self._retry_or_block(task_id, code, now=now)

        status = row.get("status") if isinstance(row, dict) else None
        if status == "delivered":
            return {
                "outcome": "pending", "task_id": task_id, "target": target_alias,
                "pending": {
                    "origin_kind": "queue", "kanban_task_id": task_id,
                    "target": target_alias, "finish": parsed["meta"]["finish"],
                    "cap": parsed["meta"]["cap"],
                    "dispatch_msg_id": row.get("msg_id"),
                    "injected_at": row.get("dispatched_at"),
                },
            }
        if status != "completed":
            return self._retry_or_block(task_id, _dispatch_failure_code(status), now=now)
        raw_result = row.get("result")
        return self.finish_pending(
            {"kanban_task_id": task_id, "target": target_alias, "finish": parsed["meta"]["finish"],
             "cap": parsed["meta"]["cap"]},
            raw_result if isinstance(raw_result, str) else "",
        )

    def finish_pending(self, pending: dict, raw_result: str) -> dict:
        task_id = pending.get("kanban_task_id")
        if not isinstance(task_id, str) or not task_id.startswith("t_"):
            raise QueueDispatchError("pending queue task identity is invalid")
        current = _task_status(self.adapter.show(task_id))
        if current in TERMINAL_STATES:
            return {"outcome": "already_terminal", "task_id": task_id}
        try:
            terminal = parse_terminal_result(raw_result, task_id, str(pending.get("cap") or "read"))
        except RecordWriteEvidenceMissing:
            metadata = {"queue_protocol": "carr-queue-result.v1", "target": pending.get("target"),
                        "outcome": "unverified", "verification": "record_write_evidence_missing"}
            if pending.get("finish") == "review":
                self.adapter.request_review(task_id, "record_write_evidence_missing", metadata)
                return {"outcome": "review", "task_id": task_id}
            self.adapter.block(task_id, "record_write_evidence_missing", kind="needs_input")
            return {"outcome": "record_write_evidence_missing", "task_id": task_id}
        except QueueDispatchError:
            self.adapter.block(task_id, "result_protocol_error")
            return {"outcome": "result_protocol_error", "task_id": task_id}

        summary = terminal["summary"]
        metadata = {
            "queue_protocol": "carr-queue-result.v1",
            "target": pending.get("target"),
            "outcome": terminal["outcome"],
        }
        if pending.get("cap") == "record-write":
            metadata["record_write"] = {key: terminal[key] for key in RECORD_WRITE_EVIDENCE_FIELDS}
        if terminal["outcome"] == "blocked":
            self.adapter.block(task_id, terminal.get("code") or summary, kind="needs_input")
            return {"outcome": "blocked", "task_id": task_id}
        if pending.get("finish") == "review":
            self.adapter.request_review(task_id, summary, metadata)
            return {"outcome": "review", "task_id": task_id}
        if pending.get("finish") != "done":
            self.adapter.block(task_id, "result_protocol_error")
            return {"outcome": "result_protocol_error", "task_id": task_id}
        self.adapter.complete(task_id, summary, metadata)
        return {"outcome": "done", "task_id": task_id}

    def fail_pending(self, pending: dict, reason: str, *, now: str | None = None) -> dict:
        task_id = pending.get("kanban_task_id")
        if not isinstance(task_id, str) or not task_id.startswith("t_"):
            raise QueueDispatchError("pending queue task identity is invalid")
        current = _task_status(self.adapter.show(task_id))
        if current in TERMINAL_STATES:
            return {"outcome": "already_terminal", "task_id": task_id}
        safe_reason = "desk_result_timeout" if reason == "desk_result_timeout" else "provider_unavailable"
        return self._retry_or_block(task_id, safe_reason, now=now)
