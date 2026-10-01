#!/usr/bin/env python3
"""Synthetic desk contracts: no provider, credentials, or live sessions."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_desktop_wire as wire
import desk_cli
import desks
import dispatch


INSTRUCTION = (
    "Model Room desk instruction: If you need approvals, permissions, or decisions, "
    "send them to the orchestrator session via send_message in one message, then "
    "end the turn. Never ask Joe."
)


class DeskPermissionTests(unittest.TestCase):
    def test_registration_refuses_prompting_and_unbounded_modes(self):
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            for kind in ("claude-desktop", "claude-session"):
                for mode in ("default", "manual", "plan", "bypassPermissions", "", "typo"):
                    with self.subTest(kind=kind, mode=mode):
                        with self.assertRaisesRegex(desks.DeskError, "orchestrator"):
                            reg.register("fixture-desk", kind, socket="/tmp/fixture-desk.sock",
                                         model="fixture-model", effort="high" if kind == "claude-desktop" else None,
                                         permission_mode=mode)
                        self.assertEqual(reg.entries(), {})

    def test_default_and_explicit_noninteractive_modes(self):
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            default = reg.register("fixture-desk", "claude-desktop", model="fixture-model", effort="high")
            self.assertEqual(default["permission_mode"], "dontAsk")
            for mode in ("dontAsk", "auto", "acceptEdits"):
                entry = reg.register("fixture-desk", "claude-desktop", model="fixture-model",
                                     effort="high", permission_mode=mode)
                self.assertEqual(entry["permission_mode"], mode)

    def test_edited_registry_refused_on_resolve(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "registry.json"
            reg = desks.Registry(path)
            for mode in ("default", "manual", "plan", "bypassPermissions"):
                path.write_text(json.dumps({"desks": {"fixture-desk": {
                    "kind": "claude-desktop", "permission_mode": mode}}}))
                with self.subTest(mode=mode), self.assertRaisesRegex(desks.DeskError, "orchestrator"):
                    reg.resolve("fixture-desk")

    def test_direct_background_launch_refuses_before_subprocess(self):
        for mode in ("default", "manual", "plan", "bypassPermissions", "", "typo"):
            with self.subTest(mode=mode):
                calls = []
                def run(argv, **_kwargs):
                    calls.append(argv)
                    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
                with self.assertRaisesRegex(wire.ClaudeDesktopError, "orchestrator"):
                    wire.launch_background({"model": "fixture-model", "effort": "high",
                                            "cwd": "/tmp", "permission_mode": mode}, "Synthetic task", run=run)
                self.assertEqual(calls, [])

    def test_cli_refuses_manual_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as root:
            from contextlib import redirect_stderr
            from io import StringIO
            stderr = StringIO()
            with redirect_stderr(stderr):
                result = desk_cli.main(["--registry", str(Path(root) / "registry.json"),
                    "register", "fixture-desk", "--kind", "claude-desktop",
                    "--model", "fixture-model", "--effort", "high", "--permission-mode", "manual"])
            self.assertEqual(result, 2)
            self.assertIn("orchestrator", stderr.getvalue())


class DeskFirstTurnTests(unittest.TestCase):
    def test_codex_live_resume_overrides_inherited_approval_policy(self):
        for thread in (None, "fixture-thread"):
            messages = []
            fake = SimpleNamespace(upgrade=lambda: None, send_json=messages.append,
                receive_json=lambda: {"method": "item/completed", "params": {
                    "item": {"type": "agentMessage", "text": "Synthetic result"}}})
            with self.subTest(thread=thread), patch.object(dispatch.codex_wire, "Wire", return_value=fake), \
                 patch.object(dispatch.codex_wire, "wait_response", return_value={"thread": {"id": "fixture-thread"}}):
                dispatch.codex_wire.run_turn("/tmp/fixture.sock", "Synthetic task", thread_id=thread)
                opened = next(m for m in messages if m.get("id") == "thread-open")
                turn = next(m for m in messages if m.get("method") == "turn/start")
                self.assertEqual(opened["params"]["approvalPolicy"], "never")
                self.assertEqual(turn["params"]["approvalPolicy"], "never")
                self.assertEqual(turn["params"]["input"][0]["text"], INSTRUCTION + "\n\nSynthetic task")

    def test_codex_desktop_turn_is_never_and_includes_instruction(self):
        from unittest.mock import Mock
        client = Mock()
        client.owner.return_value = "fixture-owner"
        client.request.return_value = {"resultType": "success"}
        with patch.object(dispatch.codex_ipc, "_open", return_value=client), \
             patch.object(dispatch.codex_ipc, "thread_owner", return_value="fixture-owner"):
            dispatch._to_codex({"thread_id": "fixture-thread"}, "Synthetic task", env={}, live_desktop=True)
        request = client.request.call_args.args[1]["turnStart"]["request"]
        self.assertEqual(request["approvalPolicy"], "never")
        self.assertEqual(request["input"][0]["text"], INSTRUCTION + "\n\nSynthetic task")

    def test_non_desk_desktop_messages_keep_their_original_posture(self):
        from unittest.mock import Mock
        client = Mock()
        client.owner.return_value = "fixture-owner"
        client.request.return_value = {"resultType": "success"}
        with patch.object(dispatch.codex_ipc, "_open", return_value=client):
            dispatch.codex_ipc.start_turn("fixture-thread", "Synthetic ordinary session message")
        request = client.request.call_args.args[1]["turnStart"]["request"]
        self.assertNotIn("approvalPolicy", request)
        self.assertEqual(request["input"][0]["text"], "Synthetic ordinary session message")

    def test_desk_never_steers_into_a_turn_with_unverified_permissions(self):
        from unittest.mock import Mock
        client = Mock()
        client.owner.return_value = "fixture-owner"
        client.request.side_effect = [{"resultType": "error", "error": "turn already in progress"},
                                      {"resultType": "success"}]
        with patch.object(dispatch.codex_ipc, "_open", return_value=client), \
             patch.object(dispatch.codex_ipc, "thread_owner", return_value="fixture-owner"):
            result = dispatch._to_codex({"thread_id": "fixture-thread"}, "Synthetic task", env={}, live_desktop=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("orchestrator", result["detail"])
        self.assertEqual(client.request.call_count, 1)

    def test_background_default_and_explicit_modes_include_instruction(self):
        sid = "12345678-1234-4123-8123-123456789abc"
        for mode in (None, "dontAsk", "auto", "acceptEdits"):
            calls = []
            def run(argv, **_kwargs):
                calls.append(argv)
                output = (json.dumps([{"id": sid[:8], "sessionId": sid}])
                          if argv[1] == "agents" else "backgrounded · 12345678")
                return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
            entry = {"model": "fixture-model", "effort": "high", "cwd": "/tmp"}
            if mode is not None:
                entry["permission_mode"] = mode
            with self.subTest(mode=mode):
                wire.launch_background(entry, "Synthetic task", request_id=sid, run=run)
                self.assertEqual(calls[0][calls[0].index("--permission-mode") + 1], mode or "dontAsk")
                self.assertEqual(calls[0][-1], INSTRUCTION + "\n\nSynthetic task")

    def test_every_desk_dispatch_includes_instruction_on_start_and_resume(self):
        # Observe the prompt at each provider boundary; no provider is called.
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            cases = {
                "claude-session": ("_to_claude", dispatch),
                "claude-desktop": ("_to_claude_desktop", dispatch),
                "codex-session": ("_to_codex", dispatch),
                "codex-live": ("run_turn", dispatch.codex_wire),
                "grok-cli": ("run_task", dispatch.grok_wire),
                "flash-local": ("run_task", dispatch.flash_wire),
            }
            for kind, (method, module) in cases.items():
                entry = {"kind": kind, "name": "fixture-desk", "model": "fixture-model",
                         "effort": "high", "socket": "/tmp/fixture.sock"}
                for thread in (None, "fixture-thread"):
                    with self.subTest(kind=kind, thread=thread):
                        entry["thread_id"] = thread
                        with patch.object(reg, "resolve", return_value=entry), patch.object(
                                module, method, return_value={"status": "delivered"}) as send:
                            dispatch.dispatch("fixture-desk", "Synthetic task", registry=reg,
                                              results_path=Path(root) / "results.jsonl")
                        prompt = send.call_args.args[0 if kind == "flash-local" else 1]
                        self.assertEqual(prompt, INSTRUCTION + "\n\nSynthetic task")

    def test_persistent_claude_start_is_dontask_and_seeds_instruction(self):
        with tempfile.TemporaryDirectory() as root:
            reg = desks.Registry(Path(root) / "registry.json")
            for seed in (None, "Synthetic seed"):
                state = Path(root) / ("unseeded" if seed is None else "seeded")
                with patch.object(dispatch.credential_env, "claude_child_env", return_value=({}, None)), \
                     patch.object(dispatch.os, "mkfifo", side_effect=lambda p, _m: Path(p).touch()), \
                     patch.object(desks, "is_live", return_value=True), \
                     patch.object(dispatch.subprocess, "Popen", return_value=SimpleNamespace(pid=4242)) as launch:
                    dispatch.desk_start("fixture-desk", registry=reg, state_dir=state,
                                        sock_dir=Path(root) / "socks", seed=seed)
                self.assertIn("--permission-mode dontAsk", launch.call_args.args[0][2])
                first = json.loads((state / "fixture-desk.stdin").read_text())
                self.assertEqual(first["message"]["content"],
                                 INSTRUCTION + ("\n\nSynthetic seed" if seed else ""))

    def test_codex_cli_start_and_resume_pin_never_and_include_instruction(self):
        entry = {"model": "fixture-model", "effort": "high", "cwd": "/tmp"}
        for thread in (None, "fixture-thread"):
            with self.subTest(thread=thread), patch.object(dispatch.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="")) as run:
                dispatch._to_codex({**entry, "thread_id": thread}, "Synthetic task", env={"PATH": "/tmp"})
                argv = run.call_args.args[0]
                self.assertIn('approval_policy="never"', argv)
                self.assertEqual(argv[-1], INSTRUCTION + "\n\nSynthetic task")


if __name__ == "__main__":
    unittest.main()
