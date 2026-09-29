#!/usr/bin/env python3
"""Exercise the sanctioned runner's public text, receipt and exit contracts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "bin/grok-run.sh"
FIXTURES = ROOT / "ops/fixtures/grok-run"


class GrokRunTests(unittest.TestCase):
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
        self.assertEqual(run.stdout, "OKOKOKOKOKOKOKOKOK\n")
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


if __name__ == "__main__":
    unittest.main()
