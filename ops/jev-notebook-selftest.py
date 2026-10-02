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


class RecallTests(unittest.TestCase):
    def _seeded_notebook(self, path):
        nb.record_mistake("off-by-one", "reverse words in a list",
                           "used wrong index and dropped the last word",
                           "iterate with reversed() instead", source="unit-test",
                           notebook_path=str(path))
        nb.record_mistake("null-check", "parse a json pointer path",
                           "forgot to handle a missing key", "added a null check",
                           source="unit-test", notebook_path=str(path))

    def test_top_pick_is_confirmed_and_returned_as_a_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            self._seeded_notebook(path)
            responses = [
                {"answers": {"rank": {"choice": "entry_0", "confidence": 0.8}}},
                {"answers": {"entry_0": {"noul": 0.9}}},
            ]
            judge = FakeJudge(responses)
            out = nb.recall_mistakes("write a function that reverses words in a list",
                                      notebook_path=str(path), client=FakeClient, judge=judge)
            self.assertEqual(len(judge.calls), 2, "one Choice request, one Noul confirm request")
            self.assertEqual(out["verdict"], [0])
            self.assertEqual(len(out["detail"]["lines"]), 1)
            self.assertIn("off-by-one", out["detail"]["lines"][0])
            self.assertFalse(out["escalate"])

    def test_a_confirmation_noul_below_half_excludes_the_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            self._seeded_notebook(path)
            responses = [
                {"answers": {"rank": {"choice": "entry_0", "confidence": 0.8}}},
                {"answers": {"entry_0": {"noul": 0.1}}},
            ]
            judge = FakeJudge(responses)
            out = nb.recall_mistakes("write a function that reverses words in a list",
                                      notebook_path=str(path), client=FakeClient, judge=judge)
            # Nothing cleared confirmation; the top pick is still surfaced rather
            # than silently returning nothing for a Choice that did narrow.
            self.assertEqual(out["verdict"], [0])

    def test_none_relevant_returns_no_lines_after_one_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            self._seeded_notebook(path)
            responses = [{"answers": {"rank": {"choice": nb.NONE_RELEVANT, "confidence": 0.6}}}]
            judge = FakeJudge(responses)
            out = nb.recall_mistakes("write a function that reverses words in a list",
                                      notebook_path=str(path), client=FakeClient, judge=judge)
            self.assertEqual(len(judge.calls), 1)
            self.assertEqual(out["verdict"], [])
            self.assertEqual(out["detail"]["lines"], [])

    def test_jev_unavailable_falls_back_to_the_overlap_shortlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            self._seeded_notebook(path)
            judge = FakeJudge([], fail_at={0})
            out = nb.recall_mistakes("write a function that reverses words in a list",
                                      notebook_path=str(path), client=FakeClient, judge=judge)
            self.assertTrue(out["escalate"])
            self.assertGreater(len(out["detail"]["lines"]), 0,
                                "a weak signal must still produce something, not nothing")

    def test_the_choice_offers_an_explicit_none_relevant_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            self._seeded_notebook(path)
            responses = [{"answers": {"rank": {"choice": "entry_0", "confidence": 0.8}}},
                         {"answers": {"entry_0": {"noul": 0.9}}}]
            judge = FakeJudge(responses)
            nb.recall_mistakes("write a function that reverses words in a list",
                                notebook_path=str(path), client=FakeClient, judge=judge)
            rank_options = judge.calls[0][1]["rank"]["criteria"]
            self.assertIn(nb.NONE_RELEVANT, rank_options)


class ClassifyKindTests(unittest.TestCase):
    def test_no_existing_kinds_is_new_kind_without_asking_jev(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            judge = FakeJudge([], fail_at={0})
            out = nb.classify_kind("forgot to close a file handle", "write a parser",
                                    client=FakeClient, judge=judge, notebook_path=str(path))
            self.assertEqual(out["verdict"], "new_kind")
            self.assertEqual(len(judge.calls), 0)

    def test_matches_an_existing_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            nb.record_mistake("off-by-one", "t", "w", "f", source="s", notebook_path=str(path))
            nb.record_mistake("null-check", "t", "w", "f", source="s", notebook_path=str(path))
            judge = FakeJudge([{"answers": {"kind": {"choice": "off-by-one", "confidence": 0.85}}}])
            out = nb.classify_kind("dropped the last element", "reverse a list",
                                    client=FakeClient, judge=judge, notebook_path=str(path))
            self.assertEqual(out["verdict"], "off-by-one")
            self.assertFalse(out["escalate"])

    def test_new_kind_option_is_offered_and_can_be_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            nb.record_mistake("off-by-one", "t", "w", "f", source="s", notebook_path=str(path))
            judge = FakeJudge([{"answers": {"kind": {"choice": nb.NEW_KIND, "confidence": 0.9}}}])
            out = nb.classify_kind("used the wrong encoding", "read a file",
                                    client=FakeClient, judge=judge, notebook_path=str(path))
            self.assertEqual(out["verdict"], "new_kind")
            options = judge.calls[0][1]["kind"]["criteria"]
            self.assertIn(nb.NEW_KIND, options)

    def test_an_outage_reports_unavailable_not_an_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            nb.record_mistake("off-by-one", "t", "w", "f", source="s", notebook_path=str(path))
            judge = FakeJudge([], fail_at={0})
            out = nb.classify_kind("x", "y", client=FakeClient, judge=judge,
                                    notebook_path=str(path))
            self.assertEqual(out["verdict"], "unavailable")
            self.assertTrue(out["escalate"])


class ShapeTests(unittest.TestCase):
    def test_recall_result_has_the_required_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            out = nb.recall_mistakes("anything", notebook_path=str(path),
                                      client=FakeClient, judge=FakeJudge([]))
            for key in ("check", "verdict", "confidence", "escalate", "detail"):
                self.assertIn(key, out)

    def test_classify_result_has_the_required_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notebook.jsonl"
            out = nb.classify_kind("x", "y", client=FakeClient, judge=FakeJudge([]),
                                    notebook_path=str(path))
            for key in ("check", "verdict", "confidence", "escalate", "detail"):
                self.assertIn(key, out)


class EntrypointTests(unittest.TestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
