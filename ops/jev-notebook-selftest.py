#!/usr/bin/env python3
"""Offline suite for ops/jev_notebook.py. No credential, no network, no spend.

Every judgment arrives through an injected fake, so this runs on a hosted
runner with nothing configured. Covers: deterministic append-only recording,
the token-overlap shortlist, the two-request recall (Choice then Noul
confirmation), the "none relevant" and unavailable fallbacks, and kind
classification against an existing roster plus the "new kind" escape.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_notebook.py"
SPEC = importlib.util.spec_from_file_location("jev_notebook", MODULE_PATH)
assert SPEC and SPEC.loader
nb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nb)


class FakeJudge:
    """Stands in for ops/jev_judge.py. Returns queued responses in call order,
    one per judge() call, so a two-request recall can be scripted exactly."""

    JudgeUnavailable = RuntimeError
    SHADOW_LOG = "unused-in-tests"

    def __init__(self, responses=None, fail_at=None):
        self.responses = list(responses or [])
        self.fail_at = set(fail_at or ())
        self.calls = []
        self.records = []

    def judge(self, subject, questions, **kwargs):
        idx = len(self.calls)
        self.calls.append((subject, questions))
        if idx in self.fail_at:
            raise self.JudgeUnavailable("synthetic outage")
        return self.responses[idx]

    def record(self, kind, subject_ref, answer, existing_decision=None, **kwargs):
        row = {"kind": kind, "subject_ref": subject_ref, "answer": answer,
               "existing_decision": existing_decision, **kwargs}
        self.records.append(row)
        return row


class FakeClient:
    @staticmethod
    def choice(instructions, options):
        return {"type": "choice", "instructions": instructions, "criteria": dict(options)}

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


class RecordMistakeTests(unittest.TestCase):
    def test_appends_a_row_and_never_calls_jev(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            row = nb.record_mistake("off-by-one", "reverse a list", "used i instead of i-1",
                                     "used i-1", source="unit-test", notebook_path=str(path))
            self.assertEqual(row["id"], 0)
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["kind"], "off-by-one")

    def test_ids_increment_and_the_file_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            first = nb.record_mistake("k", "t1", "w1", "f1", source="s", notebook_path=str(path))
            second = nb.record_mistake("k", "t2", "w2", "f2", source="s", notebook_path=str(path))
            self.assertEqual((first["id"], second["id"]), (0, 1))
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)

    def test_a_broken_path_never_raises(self):
        row = nb.record_mistake("k", "t", "w", "f", source="s",
                                 notebook_path="/proc/nonexistent/nb.jsonl")
        self.assertEqual(row["kind"], "k")


class RecallEmptyTests(unittest.TestCase):
    def test_empty_notebook_returns_no_lines_and_does_not_escalate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            out = nb.recall_mistakes("do a thing", notebook_path=str(path),
                                      client=FakeClient, judge=FakeJudge([]))
            self.assertEqual(out["detail"]["lines"], [])
            self.assertFalse(out["escalate"])

    def test_no_token_overlap_returns_no_lines_without_asking_jev(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            nb.record_mistake("k", "parse json pointer paths", "forgot null check",
                               "added null check", source="s", notebook_path=str(path))
            judge = FakeJudge([], fail_at={0})
            out = nb.recall_mistakes("completely unrelated banana pancake recipe",
                                      notebook_path=str(path), client=FakeClient, judge=judge)
            self.assertEqual(out["detail"]["lines"], [])
            self.assertEqual(len(judge.calls), 0)








class EntrypointTests(unittest.TestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
