#!/usr/bin/env python3
"""Offline acceptance tests. Only loopback and recorded vendor responses run."""
import io
import hashlib
import json
import os
from contextlib import closing
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import typesafe_client as ts
import jevlint_review as review

RECORDING = json.loads((Path(__file__).parent / "fixtures/jevlint/systemone-recording.json").read_text())
PAYLOAD = RECORDING["payload"]
RESPONSE = RECORDING["response"]


class VendorResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.log = self.root / "calls.jsonl"
        registry = self.root / "sites.json"
        entry = {"caller": "jevlint_review", "trigger": "fixture", "runs_in": "fixture",
                 "attribution": "session", "unattended": "allowed", "hourly_budget": 1,
                 "daily_budget": 1, "owner": "orchestrator", "value": "fixture",
                 "sources": ["ops/jevlint_review.py"]}
        registry.write_text(json.dumps({"schema": "carr-jev-call-sites/v1",
                                       "hourly_paid_call_cap": 10, "sites": [entry]}))
        self.enterContext(patch.object(ts, "JEV_DAILY_CAP_LOG", str(self.log)))
        self.enterContext(patch.object(ts, "JEV_CALL_SITES_PATH", str(registry)))
        self.enterContext(patch.dict(ts.ask.__kwdefaults__, calls_log=str(self.log)))
        self.enterContext(patch.dict(ts.JEV_COST_CONFIG, daily_paid_call_cap=10))
        self.enterContext(patch.object(ts, "read_api_key", return_value="offline-recording"))
        self.enterContext(patch.object(ts, "server_ask", return_value=(None, "offline")))
        self.enterContext(patch.object(ts, "_launch_spend_alert_worker", return_value=None))
        env = {k: v for k, v in os.environ.items() if k not in ts.SESSION_ID_ENV_KEYS and
               k not in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE", "CARR_JEV_WORKER", "CARR_JEV_JOB")}
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.transport = self.enterContext(patch.object(ts.urllib.request, "urlopen",
            side_effect=lambda *a, **kw: VendorResponse(json.dumps(RESPONSE).encode())))

    def rows(self):
        return [json.loads(row) for row in self.log.read_text().splitlines()]

    def test_refusal_reaches_http_contract_without_second_vendor_call(self):
        shim = review.Shim("fixture-session", "pr:1537:head")
        self.assertEqual(shim.evaluate(PAYLOAD), (200, RESPONSE))
        other = {**PAYLOAD, "state": {**PAYLOAD["state"], "name": "other"}}
        status, body = shim.evaluate(other)
        self.assertEqual(status, 403)
        self.assertIn(body["error"], ("site_hourly_budget", "site_daily_budget"))
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(self.rows()[-1]["caller"], "jevlint_review")
        self.assertEqual(self.rows()[-1]["error"], body["error"])

    def test_call_has_session_pr_attribution_and_shared_daily_cap_receipt(self):
        status, body = review.Shim("fixture-session", "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual(status, 200)
        self.assertEqual(body, RESPONSE)
        row = self.rows()[-1]
        self.assertEqual(row["session"], "fixture-session")
        self.assertEqual(row["caller"], "jevlint_review")
        self.assertIn("pr:1537:head", row["facets"])
        self.assertTrue(row["ok"])
        with closing(ts.sqlite3.connect(ts._cap_db_path())) as db:
            self.assertEqual(db.execute("SELECT attempts FROM daily_cap").fetchone()[0], 1)

    def test_missing_attribution_makes_zero_vendor_calls(self):
        status, body = review.Shim(None, "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "missing_attribution")
        self.transport.assert_not_called()

    def test_retries_reuse_answer_instead_of_buying_again(self):
        shim = review.Shim("fixture-session", "pr:1537:head")
        self.assertEqual(shim.evaluate(PAYLOAD), shim.evaluate(PAYLOAD))
        self.assertEqual(self.transport.call_count, 1)

    def test_payload_cannot_choose_another_provider(self):
        status, _ = review.Shim("fixture-session", "pr:1537:head").evaluate(
            {**PAYLOAD, "model": "another-model", "endpoint": "https://api.typesafe.ai"})
        self.assertEqual(status, 400)
        self.transport.assert_not_called()

    def test_wrong_response_model_is_unavailable(self):
        with patch.object(ts, "ask", return_value={**RESPONSE, "model": "other-model"}):
            status, body = review.Shim("fixture-session", "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual((status, body), (424, {"error": "jev_unavailable"}))

    def test_daily_cap_refuses_before_transport(self):
        with patch.dict(ts.JEV_COST_CONFIG, daily_paid_call_cap=0):
            status, body = review.Shim("fixture-session", "pr:1537:head").evaluate(PAYLOAD)
        self.assertEqual((status, body), (403, {"error": "daily_paid_call_cap"}))
        self.transport.assert_not_called()

    def test_http_and_review_runner_refusal_exit_two(self):
        binary = self.root / "jevlint"
        binary.write_text('''#!/usr/bin/env python3
import json, os, sys, urllib.request, urllib.error
assert sys.argv[1] == 'check' and '--changed' in sys.argv
assert os.environ['JEVLINT_PROVIDER'] == 'typesafe'
assert os.environ['TYPESAFE_API_KEY'] == 'carr-jevlint-loopback'
payload = json.loads(os.environ['FIXTURE_PAYLOAD'])
for name in ('_exact', 'other'):
    payload['state']['name'] = name
    request = urllib.request.Request(os.environ['TYPESAFE_ENDPOINT'], data=json.dumps(payload).encode(),
        headers={'Authorization':'Bearer carr-jevlint-loopback', 'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request) as response: json.load(response)
    except urllib.error.HTTPError as error:
        assert error.code == 403
        sys.exit(2)
print(json.dumps({'findings':[]}))
''')
        binary.chmod(0o700)
        with patch.dict(os.environ, FIXTURE_PAYLOAD=json.dumps(PAYLOAD),
                        JEVLINT_PROVIDER="openrouter", TYPESAFE_ENDPOINT="https://invalid.example"):
            shim = review.Shim("fixture-session", "pr:1537:head")
            code, report = review.run_jevlint(binary, self.root, shim, port=0)
        self.assertEqual(code, 2)
        self.assertEqual(report["refused"], 1)
        self.assertEqual(self.transport.call_count, 1)
        self.assertEqual(shim.paid_attempts, 1)

    def test_real_pinned_cli_with_recorded_response_when_installed(self):
        binary = Path.home() / "go/bin/jevlint"
        if not binary.exists():
            self.skipTest("optional upstream binary; HTTP runner contract is always tested")
        from git_env import fixture_env
        env = fixture_env()
        subprocess.run(["git", "init", "-q", str(self.root)], env=env, check=True)
        (self.root / "sample.py").write_text(PAYLOAD["state"]["source"])
        (self.root / "jevlint.json").write_text(json.dumps({"languages":{"python":{}},
            "rules":[{"id":"wrapper-without-value", "description":"Do not merely forward arguments.",
                      "kinds":["function"], "severity":"warning", "include":["**/*"]}]}))
        with patch.dict(os.environ, env, clear=True):
            code, report = review.run_jevlint(binary, self.root,
                review.Shim("fixture-session", "pr:fixture:head"), port=0)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["evaluations"], 1)
        self.assertEqual(report["findings"][0]["ruleId"], "wrapper-without-value")
        self.assertEqual(self.transport.call_count, 1)

    def test_daily_ledger_rollover_cannot_report_a_false_paid_count(self):
        binary = self.root / "jevlint"
        binary.write_text('#!/usr/bin/env python3\nprint(\'{"findings":[]}\')\n')
        binary.chmod(0o700)
        with patch.object(review, "datetime") as clock:
            clock.now.side_effect = [datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc),
                                     datetime(2026, 10, 5, tzinfo=timezone.utc)]
            with self.assertRaises(ValueError):
                review.run_jevlint(binary, self.root, review.Shim("fixture", "pr:fixture:head"), port=0)


class DiffTests(unittest.TestCase):
    def test_clean_committed_pr_becomes_only_changed_source_in_scratch(self):
        from git_env import fixture_env
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); repo = root / "repo"; repo.mkdir()
            env = fixture_env()
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()
            git("init", "-q")
            (repo / "unchanged.py").write_text("def untouched(): return 1\n")
            git("add", "unchanged.py")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (repo / "changed.py").write_text("def added(): return 2\n")
            (repo / "deleted.py").write_text("def dirty_untracked(): return 3\n")
            git("add", "changed.py")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "head")
            config = root / "config.json"; config.write_text('{}')
            scratch = root / "scratch"; scratch.mkdir()
            with patch.dict(os.environ, {**env, "GIT_DIR": str(repo / ".git"),
                                        "GIT_INDEX_FILE": str(repo / ".git/index")}, clear=True):
                selected = review.materialize(repo, base, "HEAD", scratch, config)
            self.assertEqual(selected, ["changed.py"])
            self.assertEqual((scratch / "changed.py").read_text(), "def added(): return 2\n")
            self.assertFalse((scratch / "unchanged.py").exists())
            self.assertFalse((scratch / "deleted.py").exists())
            self.assertTrue((scratch / ".git").is_dir())
            self.assertEqual(git("status", "--porcelain"), "?? deleted.py")


class EvidenceTests(unittest.TestCase):
    def test_shared_budget_obeys_brief(self):
        cap = json.loads((review.ROOT / "ops/config/jev-cost-guard.v1.json").read_text())["daily_paid_call_cap"]
        self.assertEqual(cap, 500)
        self.assertLessEqual(cap, 1000)

    def test_real_fixture_sources_are_unchanged(self):
        folder = review.ROOT / "ops/fixtures/jevlint"
        provenance = json.loads((folder / "provenance.json").read_text())
        for source in provenance["sources"]:
            with self.subTest(file=source["file"]):
                self.assertEqual(hashlib.sha256((folder / source["file"]).read_bytes()).hexdigest(), source["sha256"])

    def test_calibration_and_measurements_bind_active_configuration(self):
        folder = review.ROOT / "ops/fixtures/jevlint"
        digest = hashlib.sha256((review.ROOT / "jevlint.json").read_bytes()).hexdigest()
        calibration = json.loads((folder / "calibration.json").read_text())
        measurements = json.loads((folder / "pr-measurements.json").read_text())
        self.assertEqual(calibration["config_sha256"], digest)
        self.assertEqual(measurements["config_sha256"], digest)
        self.assertEqual(calibration["upstream"], review.PIN)
        final = next(r["report"] for r in calibration["rounds"] if r["round"] == "calibrated")
        cases = json.loads((review.ROOT / "jevlint-evals.json").read_text())["cases"]
        self.assertEqual(final["total"], len(cases))
        self.assertEqual(final["matched"], final["total"])
        self.assertEqual(final["inconclusive"], 0)
        self.assertTrue(all(c["matched"] for c in final["cases"]))
        self.assertEqual(len(measurements["samples"]), 3)
        for sample in measurements["samples"]:
            self.assertEqual(sample["shim"]["paid_attempts"], sample["shim"]["answered"])
            self.assertEqual(sample["shim"]["refused"], 0)
            self.assertEqual(sample["shim"]["errors"], 0)


if __name__ == "__main__":
    unittest.main()
