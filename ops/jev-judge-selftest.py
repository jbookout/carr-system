#!/usr/bin/env python3
"""Offline tests for the judging layer.

NOTHING HERE REACHES THE NETWORK. Every judgment is served by a fake client, so
this runs on a hosted runner with no credential and no spend.

The load-bearing cases are the two that protect callers rather than this module:
an outage must never fail the caller, and a logging failure must never fail the
caller either. Both are ways a monitoring layer turns into an outage of its own,
and both are cheap to get wrong.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("jev_judge.py")
SPEC = importlib.util.spec_from_file_location("jev_judge", MODULE_PATH)
assert SPEC and SPEC.loader
judge_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(judge_mod)


class FakeClient:
    """Stands in for ops/typesafe_client.py."""

    def __init__(self, answers=None, raises=None):
        self.answers = answers or {"q": {"type": "noul", "noul": 0.9}}
        self.raises = raises
        self.calls = []

    def ask(self, state, questions, **kwargs):
        self.calls.append((state, questions, kwargs))
        if self.raises:
            raise self.raises
        return {"model": "jev-1.13.0", "answers": self.answers,
                "usage": {"input_tokens": 10, "output_tokens": 2}}


class LibraryShapeTests(unittest.TestCase):
    MAIN_GUARD = re.compile(r"""if\s+__name__\s*==\s*["']__main__["']\s*:""")

    def test_module_is_a_library_and_must_stay_one(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"),
                         "a shebang makes this a sealed script entrypoint and owes a registry successor")
        self.assertIsNone(self.MAIN_GUARD.search(source),
                          "a main guard has the same consequence as a shebang here")

    def test_the_detector_used_here_catches_a_real_entrypoint(self):
        self.assertIsNotNone(self.MAIN_GUARD.search('if __name__ == "__main__":'))
        self.assertIsNone(self.MAIN_GUARD.search("prose mentioning __main__"))


class JudgeTests(unittest.TestCase):
    def test_every_question_about_one_subject_travels_in_one_request(self):
        client = FakeClient({"a": {"type": "noul", "noul": 0.9},
                             "b": {"type": "noul", "noul": 0.1}})
        judge_mod.judge({"code": "x"}, {"a": {}, "b": {}}, client=client)
        self.assertEqual(len(client.calls), 1,
                         "one subject, one request: batching per subject is the measured shape")

    def test_elapsed_is_reported_so_a_slow_judgment_is_visible(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        self.assertIsInstance(answer["elapsed_ms"], int)

    def test_any_failure_surfaces_as_one_catchable_type(self):
        for boom in (RuntimeError("service down"), ValueError("bad json"), OSError("no key file")):
            with self.assertRaises(judge_mod.JudgeUnavailable):
                judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient(raises=boom))

    def test_the_failure_message_names_the_underlying_cause(self):
        with self.assertRaises(judge_mod.JudgeUnavailable) as caught:
            judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient(raises=RuntimeError("service down")))
        self.assertIn("service down", str(caught.exception))
        self.assertIn("RuntimeError", str(caught.exception))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.log = str(Path(self.dir) / "sub" / "jev-judge.jsonl")

    def _rows(self):
        return [json.loads(line) for line in Path(self.log).read_text().splitlines() if line.strip()]

    def test_a_shadow_row_carries_both_verdicts_side_by_side(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("gate_probe", "ops/thing.py", answer,
                         existing_decision="allowed", note="disagreed", log_path=self.log)
        row = self._rows()[0]
        self.assertEqual(row["existing_decision"], "allowed")
        self.assertEqual(row["answers"]["q"]["noul"], 0.9)
        self.assertEqual(row["kind"], "gate_probe")

    def test_an_outage_is_recorded_rather_than_reducing_coverage_silently(self):
        judge_mod.record("gate_probe", "ops/thing.py", None,
                         existing_decision="allowed", error="JudgeUnavailable: service down",
                         log_path=self.log)
        row = self._rows()[0]
        self.assertIn("service down", row["error"])
        self.assertNotIn("answers", row)

    def test_a_logging_failure_never_reaches_the_caller(self):
        # The monitoring layer must not become the outage. A directory where a
        # file is expected is the cheapest way to make the write fail for real.
        bad = str(Path(self.dir))
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("gate_probe", "ref", answer, log_path=bad)  # must not raise

    def test_agreement_counts_only_comparable_rows(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("k", "a", answer, existing_decision="allowed", note="agreed", log_path=self.log)
        judge_mod.record("k", "b", answer, existing_decision="allowed", note="disagreed", log_path=self.log)
        judge_mod.record("k", "c", answer, existing_decision=None, log_path=self.log)
        judge_mod.record("k", "d", None, existing_decision="allowed", error="down", log_path=self.log)
        stats = judge_mod.agreement(self.log, kind="k")
        self.assertEqual((stats["rows"], stats["errors"]), (4, 1))
        self.assertEqual((stats["comparable"], stats["agreed"], stats["disagreed"]), (2, 1, 1))

    def test_agreement_on_a_missing_log_is_empty_rather_than_an_error(self):
        self.assertEqual(judge_mod.agreement(str(Path(self.dir) / "nope.jsonl"))["rows"], 0)

    def test_agreement_ignores_a_corrupt_line_instead_of_dying_on_it(self):
        answer = judge_mod.judge({"code": "x"}, {"q": {}}, client=FakeClient())
        judge_mod.record("k", "a", answer, existing_decision="allowed", note="agreed", log_path=self.log)
        with open(self.log, "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        self.assertEqual(judge_mod.agreement(self.log, kind="k")["rows"], 1)


class DefaultsTests(unittest.TestCase):
    def test_the_thresholds_are_stated_and_pessimistic(self):
        # Pinned so a later edit that quietly loosens them is a visible diff in
        # a test, not a one-character change nobody reviews.
        self.assertEqual(judge_mod.YES_AT, 0.80)
        self.assertEqual(judge_mod.NO_AT, 0.20)
        self.assertEqual(judge_mod.MIN_CONFIDENCE, 0.60)

    def test_the_shadow_log_lives_under_the_repository(self):
        self.assertTrue(judge_mod.SHADOW_LOG.startswith(judge_mod.REPO))


if __name__ == "__main__":
    unittest.main()
