"""Provider metadata, refusal, desk/bridge/auth integration without model spend."""

import copy
import json
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

ENTRY = {"kind": "grok-cli", "model": "grok-4.7", "effort": "high",
         "sandbox": "read-only", "cwd": "/tmp"}
END = {"type": "end", "stopReason": "end_turn", "requestId": "provider-request",
       "sessionId": "provider-session", "total_cost_usd": 0.01,
       "modelUsage": {"grok-4.7-build": {"modelCalls": 1}}}


def output(end=None, text="Safe Methods", before=None):
    return "\n".join(json.dumps(e) for e in [*(before or []),
                    {"type": "text", "data": text}, END if end is None else end])


class GrokTests(unittest.TestCase):
    def test_actual_model_comes_from_provider_and_text_is_joined(self):
        result = grok_wire.parse_result(output(before=[{"type": "text", "data": "RFC "}]), 0)
        self.assertEqual(result["result"], "RFC Safe Methods")
        self.assertEqual(result["provider_metadata"]["actual_model"], "grok-4.7-build")
        self.assertEqual(result["provider_metadata"]["request_id"], "provider-request")

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
