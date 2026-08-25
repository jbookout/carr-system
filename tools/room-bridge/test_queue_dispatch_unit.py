#!/usr/bin/env python3
"""Hermetic contract tests for Observatory queue Slice 3.

No Hermes task is claimed and no model or named desk is contacted.  Fakes pin
the controller boundary: canonical claim first, one desk at a time, exact
terminal protocol, one terminal transition, and no conversational echo.
"""

from __future__ import annotations

import json
import inspect
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import queue_dispatch  # noqa: E402
import bridge  # noqa: E402
import kanban_adapter  # noqa: E402
import state as state_mod  # noqa: E402


CATALOG: dict = {
    "v": 1,
    "targets": {
        "sol": {
            "enabled": True,
            "adapter": "desk",
            "assignee": "desk:codex-desk",
            "desk": "codex-desk",
            "effective_model": "gpt-5.6-sol",
            "capabilities": ["read"],
        },
        "claude": {
            "enabled": True,
            "adapter": "desk",
            "assignee": "desk:joe-desk",
            "desk": "joe-desk",
            "effective_model": "claude",
            "capabilities": ["read", "repo-write", "record-write"],
        },
        "grok": {
            "enabled": True,
            "adapter": "hermes",
            "assignee": "default",
            "effective_model": "Grok 4.6",
            "capabilities": ["read"],
        },
        "joe": {
            "enabled": True,
            "adapter": "manual",
            "assignee": "human:joe",
            "effective_model": "Joe manual lane",
            "capabilities": ["merge-approve", "production", "external-send", "destructive", "credential"],
        },
    },
}


def task(task_id: str = "t_queue0001", *, target: str = "sol", created_at: int = 2,
         finish: str = "done", cap: str = "read", body: str = "Inspect locally.") -> dict:
    meta = {
        "v": 1,
        "target": target,
        "cap": cap,
        "source_seq": 81,
        "source_msg_id": "11111111-1111-4111-8111-111111111111",
        "finish": finish,
    }
    entry = CATALOG["targets"][target]
    return {
        "id": task_id,
        "title": "Bounded queue task",
        "body": f"[CARR_QUEUE_META {json.dumps(meta, separators=(',', ':'))}]\n{body}",
        "assignee": entry["assignee"],
        "status": "ready",
        "created_at": created_at,
    }


def result(task_id: str = "t_queue0001", *, outcome: str = "success",
           summary: str = "Local attestation complete.", code: str | None = None,
           record_evidence: dict | None = None) -> str:
    payload = {"v": 1, "task_id": task_id, "outcome": outcome, "summary": summary}
    if code is not None:
        payload["code"] = code
    if record_evidence is not None:
        payload.update(record_evidence)
    return "Human-readable text stays at the desk.\nCARR_QUEUE_RESULT " + json.dumps(payload, separators=(",", ":"))


class FakeAdapter:
    def __init__(self, tasks: list[dict]):
        self.tasks = tasks
        self.calls: list[tuple] = []
        self.status = {row["id"]: row["status"] for row in tasks}

    def ready_for(self, assignee: str) -> list[dict]:
        self.calls.append(("ready_for", assignee))
        return list(self.tasks)

    def claim(self, task_id: str) -> None:
        self.calls.append(("claim", task_id))
        self.status[task_id] = "running"

    def show(self, task_id: str) -> dict:
        self.calls.append(("show", task_id))
        return {"task": {"id": task_id, "status": self.status[task_id]}}

    def comment(self, task_id: str, summary: str) -> None:
        self.calls.append(("comment", task_id, summary))

    def complete(self, task_id: str, summary: str, metadata: dict) -> None:
        self.calls.append(("complete", task_id, summary, metadata))
        self.status[task_id] = "done"

    def request_review(self, task_id: str, summary: str, metadata: dict) -> None:
        self.calls.append(("request_review", task_id, summary, metadata))
        self.status[task_id] = "review"

    def block(self, task_id: str, reason: str, *, kind: str | None = None) -> None:
        self.calls.append(("block", task_id, reason, kind))
        self.status[task_id] = "blocked"


class QueueDispatchTests(unittest.TestCase):
    def test_repository_catalog_keeps_profiles_mapped_and_ox_budget_gated(self):
        catalog = kanban_adapter.load_catalog()
        targets = catalog["targets"]
        self.assertEqual(targets["grok"]["assignee"], "default")
        self.assertEqual(targets["kimi"]["assignee"], "designer")
        self.assertEqual(targets["deepseek"]["assignee"], "reviewer")
        self.assertEqual(targets["ox-alpha"]["assignee"], "builder")
        self.assertEqual(targets["ox-alpha"]["model"], "stealth/ox-alpha")
        self.assertEqual(targets["ox-alpha"]["provider"], "openrouter")
        self.assertIs(targets["ox-alpha"]["enabled"], False)
        self.assertIn("operator confirms provider capacity", targets["ox-alpha"]["unavailable_reason"])

    def test_kanban_mutations_use_supported_cli_only(self):
        json_calls: list[list[str]] = []
        mutation_calls: list[list[str]] = []

        def read_runner(argv: list[str]):
            json_calls.append(argv)
            if "list" in argv:
                return []
            return {"task": {"id": "t_queue0001", "status": "running"}}

        adapter = kanban_adapter.KanbanAdapter(
            runner=read_runner,
            command_runner=lambda argv: mutation_calls.append(argv) or "",
        )
        adapter.ready_for("desk:codex-desk")
        adapter.claim("t_queue0001")
        adapter.comment("t_queue0001", "Safe summary.")
        adapter.request_review("t_queue0001", "Safe summary.", {"target": "sol"})
        adapter.complete("t_queue0001", "Safe summary.", {"target": "sol"})
        adapter.block("t_queue0001", "dispatch_failed", kind="transient")
        self.assertIn("--status", json_calls[0])
        claim = mutation_calls[0]
        self.assertEqual(claim[claim.index("--ttl") + 1], "900")
        self.assertEqual([call[4] for call in mutation_calls], [
            "claim", "comment", "request-review", "complete", "block",
        ])
        self.assertTrue(all(call[:4] == ["hermes", "kanban", "--board", "carr-build"]
                            for call in mutation_calls))

    def test_catalog_maps_native_profiles_without_dispatching_them_as_desks(self):
        queue_dispatch.validate_execution_catalog(CATALOG)
        adapter = FakeAdapter([])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        self.assertEqual(controller.start("grok", dispatch_call=lambda _prompt: None),
                         {"outcome": "not_desk_target", "target": "grok"})
        self.assertEqual(adapter.calls, [])

    def test_manual_human_lane_can_never_be_auto_dispatched(self):
        adapter = FakeAdapter([task(target="joe", cap="merge-approve")])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "joe", dispatch_call=lambda _prompt: self.fail("manual work must never dispatch"))
        self.assertEqual(outcome, {"outcome": "not_desk_target", "target": "joe"})
        self.assertEqual(adapter.calls, [])

    def test_busy_desk_is_not_claimed_or_interrupted(self):
        adapter = FakeAdapter([task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        self.assertEqual(controller.start("sol", dispatch_call=lambda _prompt: None,
                                          desk_busy=True)["outcome"], "desk_busy")
        self.assertEqual(adapter.calls, [])

    def test_oldest_valid_card_is_claimed_before_dispatch(self):
        adapter = FakeAdapter([
            task("t_queue0002", created_at=9), task("t_queue0001", created_at=1),
        ])
        seen: list[str] = []

        def dispatch_call(prompt: str) -> dict:
            seen.append(prompt)
            self.assertEqual(adapter.calls[1], ("claim", "t_queue0001"))
            return {"status": "completed", "result": result("t_queue0001")}

        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=dispatch_call)
        self.assertEqual(outcome["task_id"], "t_queue0001")
        self.assertIn("CARR_QUEUE_RESULT", seen[0])
        self.assertNotIn("Human-readable text stays at the desk", json.dumps(adapter.calls))

    def test_lost_claim_never_dispatches(self):
        adapter = FakeAdapter([task()])
        adapter.claim = lambda _task_id: (_ for _ in ()).throw(RuntimeError("already claimed"))
        dispatched: list[str] = []
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda prompt: dispatched.append(prompt) or {})
        self.assertEqual(outcome["outcome"], "claim_not_acquired")
        self.assertEqual(dispatched, [])

    def test_async_named_claude_desk_returns_pending_without_transition(self):
        adapter = FakeAdapter([task(target="claude")])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "claude", dispatch_call=lambda _prompt: {
                "status": "delivered", "msg_id": "dispatch-1", "dispatched_at": "2026-08-24T12:00:00+00:00",
            })
        self.assertEqual(outcome["outcome"], "pending")
        self.assertEqual(outcome["pending"]["kanban_task_id"], "t_queue0001")
        self.assertFalse(any(call[0] in {"complete", "request_review", "block"} for call in adapter.calls))

    def test_success_transitions_exactly_once_and_replay_is_noop(self):
        adapter = FakeAdapter([task()])
        controller = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter)
        pending = {"kanban_task_id": "t_queue0001", "target": "sol", "finish": "done"}
        first = controller.finish_pending(pending, result())
        second = controller.finish_pending(pending, result())
        self.assertEqual(first["outcome"], "done")
        self.assertEqual(second["outcome"], "already_terminal")
        self.assertEqual(sum(call[0] == "complete" for call in adapter.calls), 1)
        self.assertEqual(sum(call[0] == "comment" for call in adapter.calls), 0)

    def test_review_finish_uses_review_transition(self):
        adapter = FakeAdapter([task(finish="review")])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda _prompt: {"status": "completed", "result": result()})
        self.assertEqual(outcome["outcome"], "review")
        self.assertEqual(sum(call[0] == "request_review" for call in adapter.calls), 1)
        self.assertEqual(sum(call[0] == "complete" for call in adapter.calls), 0)

    def test_malformed_or_mismatched_terminal_result_blocks_safely(self):
        for raw in ("no protocol", result("t_wrong0001"), result(outcome="unknown")):
            adapter = FakeAdapter([task()])
            outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
                "sol", dispatch_call=lambda _prompt, value=raw: {"status": "completed", "result": value})
            self.assertEqual(outcome["outcome"], "result_protocol_error")
            block = next(call for call in adapter.calls if call[0] == "block")
            self.assertEqual(block[2], "result_protocol_error")
            self.assertNotIn(raw, json.dumps(adapter.calls))

    def test_declared_block_never_falls_back_to_another_target(self):
        adapter = FakeAdapter([task()])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda _prompt: {
                "status": "completed", "result": result(outcome="blocked", summary="Needs owner input."),
            })
        self.assertEqual(outcome["outcome"], "blocked")
        self.assertEqual(sum(call[0] == "block" for call in adapter.calls), 1)
        self.assertFalse(any("claude" in json.dumps(call) or "grok" in json.dumps(call)
                             for call in adapter.calls))

    def test_capability_escalation_blocks_canonically_without_retargeting(self):
        adapter = FakeAdapter([task()])
        outcome = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=adapter).start(
            "sol", dispatch_call=lambda _prompt: {
                "status": "completed", "result": result(outcome="blocked", code="capability_escalation_required",
                                                         summary="This needs record-write."),
            })
        self.assertEqual(outcome["outcome"], "blocked")
        self.assertIn(("block", "t_queue0001", "capability_escalation_required", "needs_input"), adapter.calls)
        self.assertFalse(any(call[0] in {"complete", "request_review", "ready_for"} and "joe" in json.dumps(call)
                             for call in adapter.calls))

    def test_record_write_success_needs_bounded_verb_and_readback_evidence(self):
        evidence = {"mcp_verb": "update-lead", "record_id": "lead:123",
                    "readback_verb": "read-lead", "readback_record_id": "lead:123"}
        missing = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        refused = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=missing).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result()})
        self.assertEqual(refused["outcome"], "record_write_evidence_missing")
        self.assertIn(("block", "t_queue0001", "record_write_evidence_missing", "needs_input"), missing.calls)
        review = FakeAdapter([task(target="claude", cap="record-write", finish="review")])
        reviewed = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=review).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result()})
        self.assertEqual(reviewed["outcome"], "review")
        review_call = next(call for call in review.calls if call[0] == "request_review")
        self.assertEqual(review_call[3]["outcome"], "unverified")
        verified = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        completed = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=verified).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result(record_evidence=evidence)})
        self.assertEqual(completed["outcome"], "done")
        complete = next(call for call in verified.calls if call[0] == "complete")
        self.assertEqual(complete[3]["record_write"], evidence)
        mismatch = FakeAdapter([task(target="claude", cap="record-write", finish="done")])
        mismatched = {**evidence, "readback_record_id": "lead:other"}
        out = queue_dispatch.QueueDeskExecutor(catalog=CATALOG, adapter=mismatch).start(
            "claude", dispatch_call=lambda _prompt: {"status": "completed", "result": result(record_evidence=mismatched)})
        self.assertEqual(out["outcome"], "record_write_evidence_missing")

    def test_queue_pending_result_never_reenters_room_as_a_turn(self):
        state = state_mod.default_state()
        state_mod.set_pending(
            state, "joe-desk", dispatch_msg_id="dispatch-1", log_offset=0,
            injected_at="2026-08-24T12:00:00+00:00", source_msg_id="queue:t_queue0001",
            source_seq=81, origin_kind="queue", kanban_task_id="t_queue0001",
            target="claude", finish="done",
        )
        finished: list[tuple] = []
        executor = type("Executor", (), {
            "finish_pending": lambda self, pending, raw: finished.append((pending, raw)) or {
                "outcome": "done", "task_id": "t_queue0001"
            },
        })()
        posted: list[dict] = []
        with tempfile.TemporaryDirectory() as root:
            outcome = bridge.handle_pending(
                "joe-desk", "claude", state, add_room_turn=lambda **kwargs: posted.append(kwargs),
                log_path=Path(root) / "desk.log", pending_timeout_s=60,
                scan=lambda _path, _offset: result(), queue_executor=executor,
            )
        self.assertEqual(outcome["outcome"], "done")
        self.assertEqual(len(finished), 1)
        self.assertEqual(posted, [])
        self.assertIsNone(state_mod.get_pending(state, "joe-desk"))

    def test_queue_timeout_blocks_canonically_without_echo(self):
        state = state_mod.default_state()
        state_mod.set_pending(
            state, "joe-desk", dispatch_msg_id="dispatch-1", log_offset=0,
            injected_at="2026-08-24T10:00:00+00:00", source_msg_id="queue:t_queue0001",
            source_seq=81, origin_kind="queue", kanban_task_id="t_queue0001",
            target="claude", finish="done",
        )
        failed: list[tuple] = []
        executor = type("Executor", (), {
            "fail_pending": lambda self, pending, reason: failed.append((pending, reason)) or {
                "outcome": reason, "task_id": "t_queue0001"
            },
        })()
        posted: list[dict] = []
        outcome = bridge.handle_pending(
            "joe-desk", "claude", state, add_room_turn=lambda **kwargs: posted.append(kwargs),
            log_path=Path("unused"), pending_timeout_s=10, scan=lambda _path, _offset: None,
            now="2026-08-24T12:00:00+00:00", queue_executor=executor,
        )
        self.assertEqual(outcome["outcome"], "desk_result_timeout")
        self.assertEqual(failed[0][1], "desk_result_timeout")
        self.assertEqual(posted, [])

    def test_bridge_has_a_real_queue_execution_seam_after_room_fifo(self):
        source = inspect.getsource(bridge.run_once)
        self.assertIn("queue_executor.start", source)
        self.assertLess(source.index("state_mod.pop_next_queued"), source.index("queue_executor.start"))


def main() -> int:
    result = unittest.main(module=__name__, exit=False)
    return 0 if result.result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
