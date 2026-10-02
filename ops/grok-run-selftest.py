#!/usr/bin/env python3
"""Exercise the sanctioned runner's public text, receipt and exit contracts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
import sys
import importlib.util
import io
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/room-bridge"))
import grok_wire

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "bin/grok-run.sh"
FIXTURES = ROOT / "ops/fixtures/grok-run"
spec = importlib.util.spec_from_file_location("grok_runner", ROOT / "bin/grok_run.py")
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class GrokRunTests(unittest.TestCase):
    def test_hook_turns_preserve_substantive_answer_for_runner_and_desk(self):
        # Minimized from the private 2026-10-02 Grok hook-turn capture. Retain
        # response/usage boundaries; replace source prose and omit rule text.
        raw = (FIXTURES / "hook-turns.ndjson").read_text()
        text, _, code = runner.parse_output(raw.splitlines(), "fixture")
        self.assertEqual(code, 0)
        self.assertEqual(text, "Fetched post: the announced skill is example-pro.\nSource: https://example.com/post")
        desk = grok_wire.parse_result(raw, 0)
        self.assertEqual(desk["status"], "completed")
        self.assertEqual(desk["result"], text)

    def test_acknowledgements_alone_are_non_answers(self):
        end = json.loads((FIXTURES / "hook-turns.ndjson").read_text().splitlines()[-1])
        for ack in ("Noted. Standing by.", "Noted. No tools were called.", "No action."):
            with self.subTest(ack=ack):
                raw = [json.dumps({"type": "text", "data": ack}), json.dumps(end)]
                text, _, code = runner.parse_output(raw, "fixture")
                self.assertEqual(text, "")
                self.assertEqual(code, 4)

    def test_latest_short_substantive_answer_wins(self):
        events = [json.loads(line) for line in (FIXTURES / "hook-turns.ndjson").read_text().splitlines()]
        events[-1:-1] = [{"type": "text", "data": "The answer is 42."}, {"type": "usage"}]
        text, _, code = runner.parse_output(map(json.dumps, events), "fixture")
        self.assertEqual((text, code), ("The answer is 42.", 0))

    def test_timeout_option_reaches_provider_and_preserves_default(self):
        for value in (None, "1", "600", "1800"):
            with self.subTest(timeout=value):
                argv = ["grok-run", "--prompt", "test"]
                if value is not None:
                    argv += ["--timeout-seconds", value]
                provider = mock.Mock(return_value=subprocess.CompletedProcess(
                    [], 0, (FIXTURES / "good.ndjson").read_text(), ""))
                with mock.patch.object(sys, "argv", argv), \
                        mock.patch.dict(os.environ, {}, clear=True), \
                        mock.patch.object(runner, "preflight", return_value="1.0.10"), \
                        mock.patch.object(runner, "invoke_cli", side_effect=
                            lambda *a, **kw: grok_wire.invoke_cli(*a, **kw, run=provider)), \
                        mock.patch.object(sys, "stdout", io.StringIO()), \
                        mock.patch.object(sys, "stderr", io.StringIO()):
                    self.assertEqual(runner.main(), 0)
                self.assertEqual(provider.call_args.kwargs["timeout"],
                                 180 if value is None else int(value))

    def run_fixture(self, fixture, *args, receipt_file=False):
        with tempfile.TemporaryDirectory(prefix="grok-run-test-") as directory:
            env = dict(os.environ)
            env.pop("GROK_RUN_RECEIPT", None)
            env["GROK_RUN_FAKE_NDJSON"] = str(FIXTURES / fixture)
            if receipt_file:
                env["GROK_RUN_RECEIPT"] = str(Path(directory) / "receipt.json")
            run = subprocess.run(["bash", str(RUNNER), "--prompt", "test", *args],
                                 env=env, capture_output=True, text=True, timeout=10)
            if receipt_file:
                receipt = json.loads(Path(env["GROK_RUN_RECEIPT"]).read_text())
                self.assertEqual(run.stderr, "")
            else:
                receipt = json.loads(run.stderr)
            return run, receipt

    def test_good_run_joins_text_and_reads_final_model_usage(self):
        run, receipt = self.run_fixture("good.ndjson")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "Hello world\n")
        self.assertEqual(receipt, {
            "requested_model": "grok-4.7", "actual_models": ["grok-4.7-build"],
            "stopReason": "end_turn", "num_turns": 2, "cost_usd": 0.012,
            "cli_version": "fixture",
        })

    def test_cancelled_run_refuses_completion(self):
        run, receipt = self.run_fixture("cancelled.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "cancelled")

    def test_accounting_after_completed_response_preserves_runner_and_desk_answer(self):
        run, receipt = self.run_fixture("accounting-after-response.ndjson")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "Final answer\n")
        self.assertEqual(receipt["stopReason"], "end_turn")
        self.assertEqual(receipt["actual_models"], ["grok-4.7-build"])
        desk = grok_wire.run_task(
            {"model": "grok-4.7", "effort": "high", "sandbox": "read-only"}, "test",
            run=lambda argv, **kw: subprocess.CompletedProcess(
                argv, 0, (FIXTURES / "accounting-after-response.ndjson").read_text(), ""))
        self.assertEqual(desk["status"], "completed")
        self.assertEqual(desk["result"], "Final answer")

    def test_wrong_model_refuses_substitution(self):
        run, receipt = self.run_fixture("wrong-model.ndjson")
        self.assertEqual(run.returncode, 5)
        self.assertEqual(receipt["actual_models"], ["grok-4.6"])

    def test_mixed_model_usage_refuses_substitution(self):
        run, receipt = self.run_fixture("mixed-model.ndjson")
        self.assertEqual(run.returncode, 5)
        self.assertEqual(receipt["actual_models"], ["grok-4.6", "grok-4.7-build"])

    def test_lookalike_model_prefix_refuses_substitution(self):
        run, receipt = self.run_fixture("lookalike-model.ndjson")
        self.assertEqual(run.returncode, 5)
        self.assertEqual(receipt["actual_models"], ["grok-4.70"])

    def test_empty_text_has_no_narration_and_can_write_receipt_file(self):
        run, _ = self.run_fixture("empty-text.ndjson", receipt_file=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "")

    def test_recorded_live_cli_cost_and_text(self):
        run, receipt = self.run_fixture("live-ok.ndjson")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "OK\n")
        self.assertEqual(receipt["actual_models"], ["grok-4.7-build"])
        self.assertEqual(receipt["num_turns"], 9)
        self.assertEqual(receipt["cost_usd"], 0.04628488)

    def test_truncated_stream_is_incomplete_with_receipt(self):
        run, receipt = self.run_fixture("truncated.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertIsNone(receipt["stopReason"])
        self.assertEqual(receipt["actual_models"], [])

    def test_malformed_stream_cannot_claim_success(self):
        run, receipt = self.run_fixture("malformed.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")

    def test_text_after_end_cannot_certify_completion(self):
        run, receipt = self.run_fixture("end-then-text.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")
        self.assertEqual(run.stdout, "answer\n")

    def test_error_after_end_cannot_certify_completion(self):
        run, receipt = self.run_fixture("end-then-error.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")
        self.assertNotIn("private diagnostic", run.stderr)

    def test_duplicate_ends_cannot_certify_completion(self):
        run, receipt = self.run_fixture("duplicate-end.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")

    def test_runner_and_desk_reject_the_same_review_fixtures(self):
        for fixture in ("mixed-model.ndjson", "lookalike-model.ndjson", "end-then-text.ndjson",
                        "end-then-error.ndjson", "duplicate-end.ndjson"):
            with self.subTest(fixture=fixture):
                shell, _ = self.run_fixture(fixture)
                raw = (FIXTURES / fixture).read_text()
                desk = grok_wire.run_task(
                    {"model": "grok-4.7", "effort": "high", "sandbox": "read-only"}, "test",
                    run=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, raw, ""))
                self.assertNotEqual(shell.returncode, 0)
                self.assertEqual(desk["status"], "failed")

    def run_cli(self, *args, installed="1.0.9", latest="1.0.10", auth=True,
                registry=True, upgrade=True, cli_exit=0):
        with tempfile.TemporaryDirectory(prefix="grok-cli-test-") as directory:
            scratch = Path(directory)
            grok = scratch / "grok"
            grok.write_text(textwrap.dedent('''\
                #!/usr/bin/env python3
                import json, os, pathlib, sys
                root = pathlib.Path(__file__).parent
                args = sys.argv[1:]
                with (root / "calls.jsonl").open("a") as log:
                    log.write(json.dumps(["grok", *args]) + "\\n")
                if args == ["--version"]:
                    print("grok " + (os.environ["LATEST"] if (root / "upgraded").exists() else os.environ["INSTALLED"]))
                elif args == ["models"]:
                    print("grok-4.7" if os.environ["AUTH"] == "yes" else "Not authenticated. Run grok login")
                    sys.exit(0 if os.environ["AUTH"] == "yes" else 1)
                else:
                    print(pathlib.Path(os.environ["FIXTURE"]).read_text(), end="")
                    sys.exit(int(os.environ["CLI_EXIT"]))
                '''))
            npm = scratch / "npm"
            npm.write_text(textwrap.dedent('''\
                #!/usr/bin/env python3
                import json, os, pathlib, sys
                root = pathlib.Path(__file__).parent
                args = sys.argv[1:]
                with (root / "calls.jsonl").open("a") as log:
                    log.write(json.dumps(["npm", *args]) + "\\n")
                if args[0] == "view":
                    print(os.environ["LATEST"])
                    sys.exit(0 if os.environ["REGISTRY"] == "yes" else 1)
                if os.environ["UPGRADE"] == "yes":
                    (root / "upgraded").touch()
                sys.exit(0 if os.environ["UPGRADE"] == "yes" else 1)
                '''))
            grok.chmod(0o755)
            npm.chmod(0o755)
            prompt_file = scratch / "prompt.txt"
            prompt_file.write_text("file prompt\nwith two lines")
            env = dict(os.environ, PATH=f"{scratch}:{os.environ['PATH']}",
                       INSTALLED=installed, LATEST=latest, AUTH="yes" if auth else "no",
                       REGISTRY="yes" if registry else "no", UPGRADE="yes" if upgrade else "no",
                       FIXTURE=str(FIXTURES / "good.ndjson"), CLI_EXIT=str(cli_exit))
            for name in ("GROK_RUN_FAKE_NDJSON", "GROK_RUN_RECEIPT"):
                env.pop(name, None)
            selected = [str(prompt_file) if arg == "PROMPT_FILE" else arg for arg in args]
            run = subprocess.run(["bash", str(RUNNER), *selected], env=env,
                                 capture_output=True, text=True, timeout=10)
            calls_file = scratch / "calls.jsonl"
            calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
            return run, calls

    def test_live_path_upgrades_and_passes_safe_defaults(self):
        run, calls = self.run_cli("--prompt", "literal $HOME `x`")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "Hello world\n")
        self.assertIn(["npm", "install", "-g", "@xai-official/grok@1.0.10"], calls)
        self.assertEqual(calls.count(["grok", "--version"]), 2)
        self.assertEqual(calls[-1], ["grok", "--model", "grok-4.7", "--reasoning-effort", "high",
                                   "--max-turns", "60", "--always-approve", "--sandbox", "read-only",
                                   "--output-format", "streaming-json", "--print",
                                   "Do not call any CARR or record-layer tool; do not write anything unless asked.\n\nliteral $HOME `x`"])
        self.assertEqual(json.loads(run.stderr)["cli_version"], "1.0.10")

    def test_runner_and_desk_use_the_same_default_invocation(self):
        shell, calls = self.run_cli("--prompt", "test", installed="1.0.10")
        self.assertEqual(shell.returncode, 0, shell.stderr)
        desk_calls = []

        def provider(argv, **kwargs):
            desk_calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, (FIXTURES / "live-ok.ndjson").read_text(), "")

        desk = grok_wire.run_task(
            {"model": "grok-4.7", "effort": "high", "sandbox": "read-only"}, "test", run=provider)
        self.assertEqual(desk["status"], "completed")
        self.assertEqual(len(desk_calls), 1)
        self.assertEqual(calls[-1][:-1], desk_calls[0][0][:-1])
        self.assertTrue(calls[-1][-1].endswith("\n\ntest"))
        self.assertTrue(desk_calls[0][0][-1].endswith("\n\ntest"))

    def test_writable_prompt_file_and_options(self):
        run, calls = self.run_cli("--writable", "--effort", "low", "--max-turns", "3",
                                   "--prompt-file", "PROMPT_FILE", installed="1.0.10")
        self.assertEqual(run.returncode, 0, run.stderr)
        invocation = calls[-1]
        self.assertEqual(invocation[invocation.index("--sandbox") + 1], "workspace")
        self.assertEqual(invocation[invocation.index("--reasoning-effort") + 1], "low")
        self.assertEqual(invocation[invocation.index("--max-turns") + 1], "3")
        self.assertTrue(invocation[-1].endswith("file prompt\nwith two lines"))
        self.assertFalse(any(call[:2] == ["npm", "install"] for call in calls))

    def test_authentication_failure_is_exit_three_and_never_logs_in(self):
        run, calls = self.run_cli("--prompt", "test", installed="1.0.10", auth=False)
        self.assertEqual(run.returncode, 3)
        self.assertEqual(run.stdout, "")
        self.assertEqual(run.stderr, "Grok needs sign-in: a human runs grok login\n")
        self.assertEqual(calls[-1], ["grok", "models"])
        self.assertNotIn(["grok", "login"], calls)

    def test_registry_unreachable_warns_and_continues(self):
        run, calls = self.run_cli("--prompt", "test", registry=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("registry unreachable", run.stderr)
        self.assertEqual(json.loads(run.stderr.splitlines()[-1])["cli_version"], "1.0.9")
        self.assertFalse(any(call[:2] == ["npm", "install"] for call in calls))

    def test_failed_upgrade_refuses_to_run(self):
        run, calls = self.run_cli("--prompt", "test", upgrade=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(any("--print" in call for call in calls))

    def test_cli_failure_cannot_be_masked_by_success_end(self):
        run, _ = self.run_cli("--prompt", "test", installed="1.0.10", cli_exit=7)
        self.assertNotEqual(run.returncode, 0)

    def test_bad_options_fail_before_preflight(self):
        for args in (("--effort", "extreme", "--prompt", "x"),
                     ("--max-turns", "0", "--prompt", "x"),
                     ("--prompt", "x", "--prompt-file", "PROMPT_FILE")):
            run, calls = self.run_cli(*args)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(calls, [])

    def test_invalid_timeouts_fail_before_preflight(self):
        for value in ("0", "-1", "1801", "nan", "inf", "1.5"):
            with self.subTest(timeout=value):
                run, calls = self.run_cli("--prompt", "test", "--timeout-seconds", value)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
