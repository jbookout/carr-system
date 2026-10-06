#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loop_feed", ROOT / "tools/routines/loop_feed.py")
assert spec and spec.loader
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)


class FeedTests(unittest.TestCase):
    def fixture(self, body="THE FIX: replace tools/example.py query with v_pool."):
        row = {"number": "21", "kind": "open_loop", "owner": "claude", "status": "open", "version": 3,
               "title": "Repair example collector", "label": "Repair example collector"}
        return {"board": {"count": 1, "loops": [row]}, "details": {"21": {"loop": dict(row, body=body)}}}

    def reader(self, fixture):
        calls = []
        def read(verb, args):
            calls.append((verb, args))
            if verb == "loop-board": return fixture["board"]
            if verb == "read-loop": return fixture["details"][args["number"]]
            raise AssertionError("only reads permitted")
        return read, calls

    def test_reads_body_from_read_loop_not_board(self):
        read, calls = self.reader(self.fixture())
        result = feed.collect(read)
        self.assertEqual(result["count"], 1)
        self.assertIn("tools/example.py", result["loops"][0]["fix"])
        self.assertEqual([verb for verb,_ in calls], ["loop-board", "read-loop"])
        self.assertFalse(result["possibly_truncated"])

    def test_plain_concrete_fix_is_included(self):
        read, _ = self.reader(self.fixture("Replace tools/example.py's retired output path with out/radar."))
        self.assertEqual(feed.collect(read)["count"], 1)

    def test_vague_or_human_asks_are_not_builder_fixes(self):
        for body in ["Ask Joe what should happen.", "There is a bug.", "THE FIX: investigate.", "Fix the bug.", "Need approval."]:
            with self.subTest(body=body):
                read, _ = self.reader(self.fixture(body))
                self.assertEqual(feed.collect(read)["count"], 0)

    def test_owner_and_status_are_verified_after_hydration(self):
        for field,value in [("owner", "joe"), ("status", "done"), ("kind", "team_loop")]:
            fixture = self.fixture(); fixture["details"]["21"]["loop"][field] = value
            read, _ = self.reader(fixture)
            self.assertEqual(feed.collect(read)["count"], 0)

    def test_unavailable_body_or_wrong_number_fails_closed(self):
        for value in [{"error": "not_found"}, {"loop": {"number": "22"}}, {"loop": {"number": "21", "kind": "open_loop"}}]:
            fixture = self.fixture(); fixture["details"]["21"] = value
            read, _ = self.reader(fixture)
            with self.assertRaises(ValueError): feed.collect(read)

    def test_malformed_board_fails_closed(self):
        for board in [{}, {"count": 1, "loops": []}, {"count": 1, "loops": [None]}, {"count": True, "loops": []}]:
            fixture = self.fixture(); fixture["board"] = board
            read, _ = self.reader(fixture)
            with self.assertRaises(ValueError): feed.collect(read)


if __name__ == "__main__":
    unittest.main()
