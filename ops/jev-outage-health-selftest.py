#!/usr/bin/env python3
"""Hermetic behavior checks for the Jev outage health row and bound loop."""
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import jev_outage_health as health  # noqa:E402


class OutageTests(unittest.TestCase):
    def test_stale_success_with_recent_402_attempt_warns_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            judge = root / "judge.jsonl"
            calls.write_text(json.dumps({"ts": "2026-09-28T01:00:00Z", "ok": True}) + "\n")
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402: SECRET"}) + "\n")
            judge.write_text(judge.read_text() + json.dumps({
                "at": "2026-09-28T11:30:00Z", "model": "fake-jev",
                "answers": {"test": {"noul": 0.9}}}) + "\n")
            result = health.evaluate(judge, calls, now=health.parse_time("2026-09-28T12:00:00Z"),
                                     threshold_hours=2)
            self.assertEqual((result["status"], result["reason"]),
                             ("warn", "billing_exhausted"))
            self.assertNotIn("SECRET", json.dumps(result))
            calls.write_text(calls.read_text() + json.dumps(
                {"ts": "2026-09-28T12:01:00Z", "ok": True}) + "\n")
            self.assertEqual(health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:02:00Z"))["status"], "ok")

    def test_no_attempt_is_not_an_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            calls.write_text(json.dumps({"ts": "2026-09-28T01:00:00Z", "ok": True}) + "\n")
            result = health.evaluate(root / "missing.jsonl", calls,
                                     now=health.parse_time("2026-09-28T12:00:00Z"))
            self.assertEqual(result["status"], "skip")

    def test_failed_402_stays_warn_until_a_later_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            judge = root / "judge.jsonl"
            calls.write_text(json.dumps({"ts": "2026-09-28T01:00:00Z", "ok": True}) + "\n")
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402"}) + "\n")
            for when in ("2026-09-28T12:00:00Z", "2026-09-28T14:00:00Z"):
                with self.subTest(when=when):
                    result = health.evaluate(judge, calls, now=health.parse_time(when))
                    self.assertEqual((result["status"], result["reason"]),
                                     ("warn", "billing_exhausted"))

    def test_one_loop_then_auto_close_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            calls = []
            def verb(name, payload):
                calls.append((name, payload))
                return {"ok": True, "loop_id": "loop-123", "number": "777"}
            warning = {"status": "warn", "reason": "billing_exhausted", "age_hours": 10}
            self.assertEqual(health.reconcile(warning, state, verb), "opened")
            self.assertEqual(health.reconcile(warning, state, verb), "open")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["owner"], "Joe")
            self.assertIn("add TypeSafe credits", calls[0][1]["body"])
            self.assertEqual(health.reconcile({"status": "ok"}, state, verb), "cleared")
            self.assertEqual(calls[-1][0], "close-loop")
            self.assertEqual(calls[-1][1]["loop_id"], "loop-123")


if __name__ == "__main__":
    unittest.main()
