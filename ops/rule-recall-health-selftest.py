#!/usr/bin/env python3
# ci: selftest
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.rule_recall_health import check_recall

NOW = "2026-10-05T12:00:00Z"
RULES = {"abcdef12": "First", "abcdef13": "Second"}


def receipt(*ids, at=NOW):
    return {"schema": "rule-recall-delivery-observation/v1", "receipt_id": ",".join(ids),
            "observed_at": at, "delivered": list(ids)}


class Store:
    """The record layer's loop and idempotency contract: a reused key with a
    different request is key_reuse; the same request replays its response."""

    def __init__(self):
        self.loops, self.keys, self.writes, self.lose_next = {}, {}, [], set()

    def __call__(self, name, args):
        if name == "read-loop":
            return dict(self.loops[args["loop_id"]])
        key = args["idempotency_key"]
        request = json.dumps({**args, "idempotency_key": None}, sort_keys=True)
        if key in self.keys:
            if self.keys[key][0] != request:
                raise RuntimeError(f"{name} failed (key_reuse)")
            return {"replayed": True, **self.keys[key][1]}
        answer = self.write(name, args)
        self.keys[key] = (request, answer)
        self.writes.append(name)
        if name in self.lose_next:
            self.lose_next.discard(name)
            raise subprocess.TimeoutExpired(name, 35)
        return answer

    def write(self, name, args):
        if name == "add-loop":
            loop_id = f"loop-{len(self.loops) + 1}"
            self.loops[loop_id] = {"loop_id": loop_id, "status": "open", "version": 1, "body": args["body"]}
            return {"ok": True, "loop_id": loop_id, "version": 1}
        loop = self.loops[args["loop_id"]]
        if args["base_version"] != loop["version"]:
            raise RuntimeError(f"{name} failed (version_conflict)")
        if loop["status"] != "open":
            raise RuntimeError(f"{name} failed (loop_closed)")
        loop["version"] += 1
        if name == "close-loop":
            loop["status"] = "done"
        else:
            loop["body"] = args["body"]
        return {"ok": True, "loop_id": loop["loop_id"], "version": loop["version"]}

    def open_loops(self):
        return sorted(k for k, v in self.loops.items() if v["status"] == "open")


class Health(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name) / "state.json"
        self.store = Store()

    def check(self, rows, *, now=NOW):
        return check_recall(rows, RULES, self.state, self.store, now=now)

    def test_breach_dedup_update_clear(self):
        line = self.check([receipt("abcdef12")])
        self.assertIn("abcdef13", line)
        self.assertIn("owner orchestrator", line)
        self.check([receipt("abcdef12")])
        self.assertEqual(self.store.writes, ["add-loop"])
        self.check([receipt("abcdef13")])
        self.assertEqual(self.store.writes, ["add-loop", "update-loop"])
        line = self.check([receipt("abcdef12", "abcdef13")])
        self.assertIn("OK", line)
        self.assertEqual(self.store.writes[-1], "close-loop")
        self.assertEqual(self.store.open_loops(), [])

    def test_first_missing_receipts_open_the_unavailable_warning(self):
        for rows in ([], [receipt("abcdef12", at="2026-09-01T00:00:00Z")], [{"delivered": "bad"}]):
            with self.subTest(rows=rows):
                self.setUp()
                line = self.check(rows)
                self.assertIn("UNAVAILABLE", line)
                self.assertEqual(self.store.writes, ["add-loop"])
                self.assertIn("no dated full-text receipts", self.store.loops["loop-1"]["body"])
                self.check(rows)
                self.assertEqual(self.store.writes, ["add-loop"])

    def test_unavailable_warning_keeps_silence_warning_and_clears_on_evidence(self):
        self.check([receipt("abcdef12")])
        self.check([])
        self.assertEqual(self.store.open_loops(), ["loop-1", "loop-2"])
        self.check([receipt("abcdef12")])
        self.assertEqual(self.store.open_loops(), ["loop-1"])
        self.check([receipt("abcdef12", "abcdef13")])
        self.assertEqual(self.store.open_loops(), [])

    def test_lost_close_response_reconciles_on_the_same_day_and_later(self):
        for later in (NOW, "2026-10-07T12:00:00Z"):
            with self.subTest(later=later):
                self.setUp()
                self.check([receipt("abcdef12")])
                self.store.lose_next.add("close-loop")
                line = self.check([receipt("abcdef12", "abcdef13")])
                self.assertIn("loop action FAILED", line)
                self.assertEqual(self.store.open_loops(), [])
                line = self.check([receipt("abcdef12", "abcdef13", at=later)], now=later)
                self.assertNotIn("FAILED", line)
                self.assertEqual(json.loads(self.state.read_text()), {})
                self.assertEqual(self.store.writes.count("close-loop"), 1)

    def test_lost_update_response_reconciles(self):
        self.check([receipt("abcdef12")])
        self.store.lose_next.add("update-loop")
        self.check([receipt("abcdef13")])
        line = self.check([receipt("abcdef13")])
        self.assertNotIn("FAILED", line)
        self.assertEqual(self.store.writes, ["add-loop", "update-loop"])

    def test_state_write_failure_after_add_does_not_duplicate_the_loop(self):
        real = Path.write_text
        calls = []
        def failing(path, *args, **kwargs):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(path, *args, **kwargs)
        with patch.object(Path, "write_text", failing):
            line = self.check([receipt("abcdef12")])
        self.assertIn("loop action FAILED", line)
        self.check([receipt("abcdef12")])
        self.assertEqual(self.store.writes, ["add-loop"])
        self.assertEqual(json.loads(self.state.read_text())["silence"]["loop_id"], "loop-1")

    def test_unsaved_intent_sends_nothing(self):
        with patch.object(Path, "write_text", side_effect=OSError("read-only")):
            line = self.check([receipt("abcdef12")])
        self.assertIn("loop action FAILED", line)
        self.assertEqual(self.store.writes, [])

    def test_failed_write_is_visible_and_cannot_clear(self):
        line = check_recall([receipt("abcdef12")], RULES, self.state, lambda *a: {"ok": False}, now=NOW)
        self.assertIn("loop action FAILED", line)


if __name__ == "__main__":
    unittest.main()
