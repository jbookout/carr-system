#!/usr/bin/env python3
"""Synthetic timestamp-only tests; no real credential or provider call."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("grok_session", ROOT / "ops/grok_session.py")
assert spec is not None and spec.loader is not None
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)
NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


class GrokSessionTests(unittest.TestCase):
    def test_public_health_section_is_narrow_and_propagates_status(self):
        # Supply synthetic results through the helper seam. This entrypoint
        # must exit before DB/other-provider health readers can run.
        harness = '''import runpy, sys, types
sys.path.insert(0, sys.argv.pop(1))
status = sys.argv.pop(1)
fake = types.ModuleType("grok_session")
fake.inspect_session = lambda: {"status": status}
fake.health_row = lambda row: status + " Grok session — synthetic timestamps · run grok login"
sys.modules["grok_session"] = fake
path = sys.argv.pop(1)
runpy.run_path(path, run_name="__main__")
'''
        for status, code in [("OK", 0), ("WARN", 0), ("FAIL", 1)]:
            result = subprocess.run([sys.executable, "-c", harness, str(ROOT / "tools"), status,
                                     str(ROOT / "tools/health-check.py"), "--section", "grok-session"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertIn(status + " Grok session", result.stdout)

    def test_nested_timestamp_storage_is_supported_without_retaining_scope_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, 72)
            pair = json.loads(path.read_text())
            path.write_text(json.dumps({"sessions": {"synthetic-scope": pair}}))
            self.assertEqual(session.read_timestamps(path), pair)

    def test_nightly_runs_narrow_health_and_preflights_reader(self):
        source = (ROOT / "bin/nightly.sh").read_text()
        self.assertTrue('step "Grok session expiry alarm"' in source)
        self.assertTrue('tools/health-check.py --section grok-session' in source)
        self.assertTrue('tools/health-check.py ops/jev_spend_health.py ops/grok_session.py' in source)

    def test_transports_are_independent_and_never_echo_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def record(name, payload):
                if name == "add-loop":
                    return {"ok": True, "loop_id": "synthetic-loop"}
                self.fail(name)
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0, 'synthetic private output', 'synthetic private output'))
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record), \
                    mock.patch.object(session.subprocess, "run", provider):
                session._deliver("Grok needs sign-in; run grok login.")
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(provider.call_args_list[0].args[0][0], "/usr/bin/osascript")
            self.assertIn("joe", provider.call_args_list[1].args[0])
            for call in provider.call_args_list:
                self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
                self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)

    def test_partial_delivery_does_not_repeat_successful_or_uncertain_send(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = mock.Mock(side_effect=[subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)])
            with mock.patch.object(session, "ROOT", Path(directory)), \
                    mock.patch.object(session, "_run_verb", return_value={"ok": True, "loop_id": "synthetic-loop"}), \
                    mock.patch.object(session.subprocess, "run", provider):
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        session._deliver("Grok needs sign-in; run grok login.")
            self.assertEqual(provider.call_count, 2)

    def test_loop_failure_still_reaches_local_and_mail_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
            with mock.patch.object(session, "ROOT", Path(directory)), \
                    mock.patch.object(session, "_run_verb", side_effect=RuntimeError("record unavailable")), \
                    mock.patch.object(session.subprocess, "run", provider):
                with self.assertRaises(RuntimeError):
                    session._deliver("Grok needs sign-in; run grok login.")
            self.assertEqual(provider.call_count, 2)

    def test_healthy_readback_closes_existing_response_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            (root / "out/grok-session-loop.json").write_text('{"loop_id":"synthetic-loop"}')
            record = mock.Mock(side_effect=[{"loop_id":"synthetic-loop", "version":1}, {"ok":True}])
            with mock.patch.object(session, "ROOT", root), mock.patch.object(session, "_run_verb", record):
                session._clear_loop()
            self.assertEqual(record.call_args.args[0], "close-loop")
            self.assertEqual(json.loads((root / "out/grok-session-loop.json").read_text()), {})

    def fixture(self, directory, hours, age=24):
        path = Path(directory) / "auth.json"
        path.write_text(json.dumps({"create_time": (NOW - timedelta(hours=age)).isoformat(),
                                    "expires_at": (NOW + timedelta(hours=hours)).isoformat()}))
        return path

    def test_days_left_and_age_use_timestamps_not_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            row = session.inspect_session(self.fixture(directory, 72, age=48), now=NOW)
            self.assertEqual((row["age_days"], row["days_left"], row["status"]), (2, 3, "OK"))

    def test_warn_fail_thresholds_inclusive_and_expired(self):
        with tempfile.TemporaryDirectory() as directory:
            for hours, status in [(48.001, "OK"), (48, "WARN"), (24.001, "WARN"),
                                  (24, "FAIL"), (0, "FAIL"), (-12, "FAIL")]:
                with self.subTest(hours=hours):
                    row = session.inspect_session(self.fixture(directory, hours), now=NOW)
                    self.assertEqual(row["status"], status)
                    self.assertAlmostEqual(row["days_left"], hours / 24)

    def test_missing_malformed_and_future_timestamps_fail_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            for raw in [None, "{", '{}', '{"create_time":"bad","expires_at":null}',
                        json.dumps({"create_time": (NOW + timedelta(days=1)).isoformat(),
                                    "expires_at": (NOW + timedelta(days=2)).isoformat()})]:
                if raw is not None:
                    path.write_text(raw)
                row = session.inspect_session(path, now=NOW)
                self.assertEqual(row["status"], "FAIL")
                self.assertNotIn("bad", row["detail"])

    def test_one_alert_per_utc_day_shared_by_health_and_sign_in(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            calls = []
            row = session.inspect_session(self.fixture(directory, 48), now=NOW)
            send = lambda message: calls.append(message)
            session.health_row(row, state_path=state, now=NOW, send=send)
            session.sign_in_alert(state_path=state, now=NOW, send=send)
            session.health_row(row, state_path=state, now=NOW, send=send)
            self.assertEqual(len(calls), 1)
            session.health_row(row, state_path=state, now=NOW+timedelta(days=1), send=send)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all("run grok login" in msg for msg in calls))

    def test_concurrent_breaches_share_the_daily_lock(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            calls = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: session.sign_in_alert(state_path=state, now=NOW, send=calls.append), range(8)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(results.count("alert sent"), 1)

    def test_failed_alert_does_not_claim_delivery_or_suppress_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            def fail(message):
                raise RuntimeError("synthetic private diagnostic")
            row = session.inspect_session(self.fixture(directory, 1), now=NOW)
            line = session.health_row(row, state_path=state, now=NOW, send=fail)
            self.assertIn("alert FAILED", line)
            self.assertNotIn("private diagnostic", line)
            calls = []
            session.health_row(row, state_path=state, now=NOW, send=calls.append)
            self.assertEqual(len(calls), 1)

    def test_health_row_binds_owner_remediation_verification_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            row = session.inspect_session(self.fixture(directory, 72), now=NOW)
            line = session.health_row(row, state_path=Path(directory)/"state.json", now=NOW,
                                      send=lambda _: self.fail("healthy must not alert"))
            for text in ["age 1.00d", "3.00d left", "owner Joe", "run grok login", "verify", "auto-clear"]:
                self.assertIn(text, line)

    def test_reader_returns_only_allowed_fields_and_rejects_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory, 72)
            self.assertEqual(set(session.read_timestamps(path)), {"create_time", "expires_at"})
            path.write_text('{"create_time":"2026-10-01T12:00:00Z", "create_time":"2026-10-01T12:00:00Z", "expires_at":"2026-10-05T12:00:00Z"}')
            self.assertEqual(session.inspect_session(path, now=NOW)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
