#!/usr/bin/env python3
"""Hermetic behavior checks for the Jev outage health row and bound loop."""
import json
import os
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

    def test_open_outage_stays_warn_when_judge_log_is_missing_or_damaged(self):
        for damaged in (None, '{"at": "2026-09-28T11:00:00Z", "error":',
                        "this is not json\n", "valid_prefix"):
            with self.subTest(damaged=damaged), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                calls = root / "calls.jsonl"
                judge = root / "judge.jsonl"
                state = root / "outage-state.json"
                calls.write_text(json.dumps({"ts": "2026-09-28T01:00:00Z", "ok": True}) + "\n")
                prefix = json.dumps({"at": "2026-09-28T10:00:00Z",
                                     "error": "TypeSafe returned HTTP 402"}) + "\n"
                judge.write_text(prefix + json.dumps({"at": "2026-09-28T11:00:00Z",
                                                      "error": "TypeSafe returned HTTP 402"}) + "\n")
                warning = health.evaluate(judge, calls,
                    now=health.parse_time("2026-09-28T12:00:00Z"), state_path=state)
                self.assertEqual(health.reconcile(warning, state,
                    lambda name, payload: {"ok": True, "loop_id": "loop-123"}), "opened")
                if damaged is None:
                    judge.unlink()
                else:
                    judge.write_text(prefix if damaged == "valid_prefix" else damaged)
                result = health.evaluate(judge, calls,
                    now=health.parse_time("2026-09-28T12:10:00Z"), state_path=state)
                self.assertEqual((result["status"], result["reason"]),
                                 ("warn", "log_unreadable"))
                updates = []
                def update(name, payload):
                    updates.append((name, payload))
                    return {"ok": True, "loop_id": "loop-123"}
                self.assertEqual(health.reconcile(result, state, update), "updated")
                self.assertIn("judgment log", updates[0][1]["body"])
                calls.write_text(calls.read_text() + json.dumps(
                    {"ts": "2026-09-28T12:11:00Z", "ok": True}) + "\n")
                recovered = health.evaluate(judge, calls,
                    now=health.parse_time("2026-09-28T12:12:00Z"), state_path=state)
                self.assertEqual(recovered["status"], "ok")
                self.assertEqual(health.reconcile(recovered, state,
                    lambda name, payload: {"ok": True, "loop_id": "loop-123"}), "cleared")

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

    def test_existing_loop_without_attempt_time_requires_new_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            judge = root / "missing-judge.jsonl"
            calls = root / "calls.jsonl"
            state.write_text(json.dumps({"loop_id": "loop-123",
                                         "reason": "billing_exhausted"}))
            opened = health.parse_time("2026-09-28T11:00:00Z").timestamp()
            os.utime(state, (opened, opened))
            calls.write_text(json.dumps({"ts": "2026-09-28T09:00:00Z", "ok": True}) + "\n")
            pending = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:00:00Z"), state_path=state)
            self.assertEqual((pending["status"], pending["reason"]),
                             ("warn", "log_unreadable"))
            calls.write_text(calls.read_text() + json.dumps(
                {"ts": "2026-09-28T11:01:00Z", "ok": True}) + "\n")
            recovered = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:00:00Z"), state_path=state)
            self.assertEqual(recovered["status"], "ok")

    def test_later_failed_attempt_moves_recovery_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            judge = root / "judge.jsonl"
            calls = root / "calls.jsonl"
            calls.write_text(json.dumps({"ts": "2026-09-28T09:00:00Z", "ok": True}) + "\n")
            judge.write_text(json.dumps({"at": "2026-09-28T11:00:00Z",
                                         "error": "TypeSafe returned HTTP 402"}) + "\n")
            verb = lambda name, payload: {"ok": True, "loop_id": "loop-123"}
            first = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T12:00:00Z"), state_path=state)
            self.assertEqual(health.reconcile(first, state, verb), "opened")
            judge.write_text(judge.read_text() + json.dumps({
                "at": "2026-09-28T13:00:00Z", "error": "TypeSafe returned HTTP 402"}) + "\n")
            later = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T14:00:00Z"), state_path=state)
            self.assertEqual(health.reconcile(later, state, verb), "open")
            judge.unlink()
            calls.write_text(calls.read_text() + json.dumps(
                {"ts": "2026-09-28T12:00:00Z", "ok": True}) + "\n")
            still_open = health.evaluate(judge, calls,
                now=health.parse_time("2026-09-28T14:00:00Z"), state_path=state)
            self.assertEqual((still_open["status"], still_open["reason"]),
                             ("warn", "log_unreadable"))


if __name__ == "__main__":
    unittest.main()
