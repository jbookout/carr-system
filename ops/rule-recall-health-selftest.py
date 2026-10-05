#!/usr/bin/env python3
# ci: selftest
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.rule_recall_health import check_recall


class Health(unittest.TestCase):
    def test_breach_dedup_update_clear_and_missing_evidence(self):
        calls = []
        def verb(name, args):
            calls.append((name, args))
            return {"ok": True, "loop_id": "test-loop", "version": 1}
        now = "2026-10-05T12:00:00Z"
        receipt = {"schema": "rule-recall-delivery-observation/v1", "receipt_id": "1",
                   "observed_at": now, "delivered": ["abcdef12"]}
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            line = check_recall([receipt], {"abcdef12": "First", "abcdef13": "Second"}, state, verb, now=now)
            self.assertIn("abcdef13", line)
            self.assertIn("owner orchestrator", line)
            self.assertEqual(calls[0][0], "add-loop")
            check_recall([receipt], {"abcdef12": "First", "abcdef13": "Second"}, state, verb, now=now)
            self.assertEqual(len(calls), 1)
            self.assertIn("UNAVAILABLE", check_recall([], {"abcdef12": "First"}, state, verb, now=now))
            self.assertEqual(len(calls), 1)
            line = check_recall([dict(receipt, delivered=["abcdef12", "abcdef13"])],
                                {"abcdef12": "First", "abcdef13": "Second"}, state, verb, now=now)
            self.assertIn("OK", line)
            self.assertEqual(calls[-1][0], "close-loop")

    def test_failed_write_is_visible_and_cannot_clear(self):
        def verb(*args):
            return {"ok": False}
        row = {"schema": "rule-recall-delivery-observation/v1", "receipt_id": "1",
               "observed_at": "2026-10-05T12:00:00Z", "delivered": ["abcdef12"]}
        with tempfile.TemporaryDirectory() as folder:
            line = check_recall([row], {"abcdef12": "First", "abcdef13": "Second"},
                                Path(folder) / "state.json", verb, now=row["observed_at"])
            self.assertIn("loop action FAILED", line)


if __name__ == "__main__":
    unittest.main()
