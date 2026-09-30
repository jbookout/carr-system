"""Provider metadata, refusal, desk/bridge/auth integration without model spend."""

import copy
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import auth_control
import bridge
import desks
import dispatch
import grok_desk
import grok_wire
import kanban_adapter
import queue_dispatch
from test_queue_dispatch_unit import FakeAdapter

ENTRY = {"kind": "grok-cli", "model": "grok-4.7", "effort": "high",
         "sandbox": "read-only", "cwd": "/tmp"}
END = {"type": "end", "stopReason": "end_turn", "requestId": "provider-request",
       "sessionId": "provider-session", "total_cost_usd": 0.01,
       "modelUsage": {"grok-4.7-build": {"modelCalls": 1}}}


def output(end=None, text="Safe Methods", before=None):
    return "\n".join(json.dumps(e) for e in [*(before or []),
                    {"type": "text", "data": text}, END if end is None else end])


class GrokTests(unittest.TestCase):
    def _run_queued_grok(self, *, fail_post=False, recover_transition=False):
        answer = "Safe methods: https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.1"
        protocol = 'CARR_QUEUE_RESULT {"v":1,"task_id":"t_grok0001","outcome":"success","summary":"Retrieved RFC."}'
        meta = {"v": 1, "target": "grok", "cap": "read", "finish": "done",
                "source_seq": 42, "source_msg_id": "source-message"}
        card = {"id": "t_grok0001", "status": "ready", "assignee": "desk:grok-desk",
                "created_at": 1, "title": "Retrieve RFC",
                "body": f"[CARR_QUEUE_META {json.dumps(meta)}]\nRetrieve safe methods."}
        catalog = kanban_adapter.load_catalog()
        adapter = FakeAdapter([card])
        adapter.reconcile_disabled_targets = lambda _catalog: {
            "scanned": 0, "blocked": [], "diagnostics": []}
        executor = queue_dispatch.QueueDeskExecutor(catalog=catalog, adapter=adapter)
        service = kanban_adapter.QueueService(catalog=catalog, adapter=adapter)
        posted = []
        completion_bodies = {}

        if recover_transition:
            complete = adapter.complete

            def crash_once(*args):
                adapter.complete = complete
                raise OSError("terminal transition unavailable")
            adapter.complete = crash_once

        def post(**kw):
            if "queue_completion" in json.loads(kw["body"]):
                self.assertEqual(adapter.status[card["id"]], "running")
                if fail_post:
                    raise RuntimeError("room publication unavailable")
                key = kw["idempotency_key"]
                if key in completion_bodies:
                    if completion_bodies[key] != kw["body"]:
                        raise RuntimeError("key_reuse")
                    return {"ok": True}
                completion_bodies[key] = kw["body"]
            posted.append(kw)
            return {"ok": True}

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            reg = desks.Registry(root / "desks.json")
            grok_desk.install(reg, str(root))
            # Exercise the real parser, dispatch, executor and bridge. Only the
            # provider process and external room/Hermes boundaries are fake.
            provider = mock.Mock(return_value=subprocess.CompletedProcess(
                [], 0, output(text=answer + "\n" + protocol), ""))
            run_task = grok_wire.run_task
            with mock.patch.object(grok_wire, "run_task", side_effect=
                    lambda entry, task: run_task(entry, task, run=provider)), \
                    mock.patch.object(subprocess, "Popen", side_effect=AssertionError("live process denied")), \
                    mock.patch.object(socket.socket, "connect", side_effect=AssertionError("live network denied")), \
                    mock.patch.dict(os.environ, {"CARR_ENGINEERING_DISPATCH_ENABLED": "false"}):
                def cycle(executor):
                    return bridge.run_once(
                        registry=reg, state_path=root / "state.json", results_path=root / "results.jsonl",
                        desk_state_dir=root / "desk-state", read_room=lambda *_a, **_k: {"turns": []},
                        add_room_turn=post, queue_service=service, queue_executor=executor,
                        queue_projector=lambda **_k: [], probe_auth=lambda _e: True,
                        session_probe=lambda *_a, **_k: False, host="test-host",
                        read_profiles=lambda: [], log=lambda _m: None)
                if recover_transition:
                    with self.assertRaisesRegex(OSError, "terminal transition unavailable"):
                        cycle(executor)
                    self.assertEqual(adapter.status[card["id"]], "running")
                    # Hermes expires the workerless claim. A new process must
                    # finish the published execution without another model call.
                    adapter.status[card["id"]] = "ready"
                    executor = queue_dispatch.QueueDeskExecutor(catalog=catalog, adapter=adapter)
                summary = cycle(executor)
            row = json.loads((root / "results.jsonl").read_text().splitlines()[0])
        self.assertEqual(provider.call_count, 1)
        return summary, row, adapter, posted, answer

    def test_queued_result_is_published_with_provider_binding_before_done(self):
        summary, row, adapter, posted, answer = self._run_queued_grok()
        self.assertEqual(summary["errors"], [])
        completions = [p for p in posted if "queue_completion" in json.loads(p["body"])]
        self.assertEqual(len(completions), 1)
        callback = json.loads(completions[0]["body"])["queue_completion"]
        self.assertEqual(callback["reply"], answer)
        self.assertEqual(callback["provider_metadata"], {
            "requested_model": "grok-4.7", "actual_model": "grok-4.7-build",
            "effort": "high", "request_id": "provider-request",
            "session_id": "provider-session", "model_calls": 1,
            "cost_usd": 0.01, "stop_reason": "end_turn"})
        self.assertEqual(callback["dispatch_msg_id"], row["msg_id"])
        self.assertEqual((callback["source_msg_id"], callback["source_seq"]), ("source-message", 42))
        self.assertEqual(completions[0]["idempotency_key"], "queue-completion:t_grok0001")
        self.assertEqual(adapter.status["t_grok0001"], "done")

    def test_queued_publication_failure_keeps_the_claim_nonterminal(self):
        summary, _row, adapter, posted, _answer = self._run_queued_grok(fail_post=True)
        self.assertEqual(summary["errors"], [{
            "desk": "grok-desk", "error": "queue_completion_post_failed",
            "detail": "room publication unavailable"}])
        self.assertEqual(adapter.status["t_grok0001"], "running")
        self.assertFalse(any(c[0] in {"complete", "block", "request_review"} for c in adapter.calls))
        self.assertFalse(any("queue_completion" in json.loads(p["body"]) for p in posted))
        self.assertTrue(any("heartbeat" in json.loads(p["body"]) for p in posted))

    def test_published_completion_survives_terminal_failure_and_bridge_restart(self):
        summary, row, adapter, posted, answer = self._run_queued_grok(recover_transition=True)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(adapter.status["t_grok0001"], "done")
        completions = [json.loads(p["body"])["queue_completion"] for p in posted
                       if "queue_completion" in json.loads(p["body"])]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]["reply"], answer)
        self.assertEqual(completions[0]["dispatch_msg_id"], row["msg_id"])
        self.assertEqual(completions[0]["provider_metadata"], row["provider_metadata"])

    def test_actual_model_comes_from_provider_and_text_is_joined(self):
        result = grok_wire.parse_result(output(before=[{"type": "text", "data": "RFC "}]), 0)
        self.assertEqual(result["result"], "RFC Safe Methods")
        self.assertEqual(result["provider_metadata"]["actual_model"], "grok-4.7-build")
        self.assertEqual(result["provider_metadata"]["request_id"], "provider-request")

    def test_recorded_turns_return_only_the_final_assistant_message(self):
        fixture = Path(__file__).resolve().parents[2] / "ops/fixtures/grok-run/live-ok.ndjson"
        result = grok_wire.parse_result(fixture.read_text(), 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], "OK")

    def test_final_message_keeps_chunks_and_intentional_repeated_text(self):
        raw = output(text="Final Final", before=[
            {"type": "text", "data": "Earlier answer"},
            {"type": "usage", "usage": {"output_tokens": 2}},
            {"type": "available_commands", "tools": []},
            {"type": "thought", "data": "Private reasoning"},
            {"type": "text", "data": "Final chunk: "},
        ])
        result = grok_wire.parse_result(raw, 0)
        self.assertEqual(result["result"], "Final chunk: Final Final")

    def test_usage_after_final_text_does_not_erase_the_answer(self):
        raw = "\n".join(json.dumps(e) for e in [
            {"type": "text", "data": "Final answer"},
            {"type": "usage", "usage": {"output_tokens": 2}}, END])
        self.assertEqual(grok_wire.parse_result(raw, 0)["result"], "Final answer")

    def test_refuses_absent_mismatched_mixed_and_incomplete_metadata(self):
        for models in (None, {}, {"grok-4.6": {"modelCalls": 1}},
                       {**END["modelUsage"], "grok-4.7-build-fast": {"modelCalls": 1}}):
            with self.subTest(models=models):
                self.assertEqual(grok_wire.parse_result(output({**END, "modelUsage": models}), 0)["status"], "failed")
        for changes in ({"stopReason": "max_turns"}, {"requestId": ""},
                        {"modelUsage": {"grok-4.7-build": {}}}):
            self.assertEqual(grok_wire.parse_result(output({**END, **changes}), 0)["status"], "failed")
        for raw, code in ((output(text=""), 0), (output(), 1), ('{"type":"text","data":"I am Grok 4.7"}', 0),
                          (output(before=[{"type": "error"}]), 0), (output()+ '\n{"type":"text","data":"late"}', 0)):
            self.assertEqual(grok_wire.parse_result(raw, code)["status"], "failed")

    def test_cli_posture_and_failure_have_no_raw_diagnostics(self):
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, output(), "ignored private diagnostic")
        result = grok_wire.run_task(ENTRY, "read public source", run=run)
        argv, options = calls[0]
        self.assertEqual(argv[argv.index("--model")+1], "grok-4.7")
        self.assertIn("--always-approve", argv)
        self.assertEqual(argv[argv.index("--sandbox")+1], "read-only")
        self.assertEqual(argv[argv.index("--output-format")+1], "streaming-json")
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["timeout"], 180)
        self.assertNotIn("diagnostic", json.dumps(result))
        for error, status in ((FileNotFoundError(), "failed"), (subprocess.TimeoutExpired("grok", 180), "timed_out")):
            def fail(*a, **k):
                raise error
            self.assertEqual(grok_wire.run_task(ENTRY, "test", run=fail)["status"], status)

    def test_installer_registry_dispatch_and_bridge_keep_provider_binding(self):
        with tempfile.TemporaryDirectory() as td:
            reg = desks.Registry(Path(td)/"desks.json")
            reg.register("other-desk", "flash-local")
            other = copy.deepcopy(reg.entries()["other-desk"])
            entry = grok_desk.install(reg, "/tmp")
            self.assertEqual(reg.entries()["other-desk"], other)
            self.assertEqual(entry["room_listen"], "mention")
            result = grok_wire.parse_result(output(), 0)
            turns = []
            def post(**kw):
                turns.append(kw)
                return {"ok": True}
            with mock.patch.object(grok_wire, "run_task", return_value=result) as run:
                delivered = bridge.deliver("grok-desk", entry, "grok", {"body":"@grok retrieve", "seat":"claude",
                    "msg_id":"source-message", "seq":42}, state={}, registry=reg,
                    results_path=Path(td)/"results.jsonl", add_room_turn=post)
            self.assertEqual(delivered["outcome"], "replied_sync")
            self.assertEqual(run.call_count, 1)
            receipt = json.loads(turns[0]["body"])["grok_execution"]
            self.assertEqual(receipt["source_msg_id"], "source-message")
            self.assertEqual(receipt["source_seq"], 42)
            self.assertEqual(receipt["actual_model"], "grok-4.7-build")
            self.assertEqual(turns[1]["body"], "Safe Methods")
            row = json.loads((Path(td)/"results.jsonl").read_text())
            self.assertEqual(row["msg_id"], receipt["dispatch_msg_id"])
            heartbeat = json.loads(bridge.heartbeat_body(reg.entries(), 42, "now"))["heartbeat"]
            grok = next(e for e in heartbeat["desks"] if e["name"] == "grok-desk")
            self.assertEqual((grok["model"],grok["effort"]), ("grok-4.7","high"))

    def test_registry_refuses_widened_or_stale_grok_even_if_hand_edited(self):
        with tempfile.TemporaryDirectory() as td:
            reg = desks.Registry(Path(td)/"desks.json")
            for changes in ({"model":"grok-4.6"}, {"effort":"low"}, {"sandbox":"workspace"}):
                e = {**ENTRY, **changes}
                with self.assertRaises(ValueError):
                    grok_wire.run_task(e, "test", run=lambda *a,**k: self.fail("must not execute"))
                reg.path.write_text(json.dumps({"desks":{"grok-desk":e}}))
                with self.assertRaises(desks.DeskError):
                    reg.resolve("grok-desk")

    def test_auth_probe_uses_existing_login_and_no_login_launcher(self):
        def run(argv, **kw):
            self.assertEqual(argv, ["grok","models"])
            return subprocess.CompletedProcess(argv, 0, "You are logged in with grok.com.", "")
        self.assertTrue(auth_control.probe_auth(ENTRY, run=run))
        self.assertNotIn("grok-cli", auth_control.AUTH_LOGIN_COMMANDS)


if __name__ == "__main__":
    unittest.main()
