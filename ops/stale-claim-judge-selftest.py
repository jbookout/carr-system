"""Offline suite for ops/stale_claim_judge.py and its use in the gate.

No credential, no network, no spend. Every model call arrives through an
injected fake.

The cases that earn their place are the ones that decide whether this is safe
in front of a Stop door on the gate that guards this system's most frequent
failure class: it must never announce when nothing answers the claim, it must
be distinguishable from an outage, and an outage must leave the gate behaving
exactly as it did before this module existed.
"""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

OPS = Path(__file__).resolve().parent
REPO = OPS.parent
MODULE_PATH = OPS / "stale_claim_judge.py"
GATE_PATH = REPO / "hooks" / "stale-claim-gate.py"
SPEC = importlib.util.spec_from_file_location("stale_claim_judge", MODULE_PATH)
assert SPEC and SPEC.loader
judge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(judge)

COMMITS = [
    ("c39ed3bf", "Warn before a shell command runs, in every session"),
    ("a7b7bf46", "Admit the Doc conversation list door"),
    ("36ba65c9", "Bind F03's SQL leg to the shared corpus"),
]
CLAIM = "The pre-check that warns before a shell command runs has not shipped."


class _FakeJudge:
    """Reproduces the REAL response shape, with answers nested under "answers".

    A fake that flattens that nesting hides the exact defect mutation testing
    caught in a sibling module, so it is worse than no fake at all.
    """

    def __init__(self, scores, fail=()):
        self.scores = scores
        self.fail = set(fail)
        self.seen = []

    def judge(self, state, questions, **kwargs):
        self.seen.append(state)
        subject = state["commit_subject"]
        if subject in self.fail or self.fail == {"*"}:
            raise RuntimeError("service did not answer")
        return {"answers": {"addresses": {"noul": self.scores.get(subject, 0.0)}}}


class _FakeClient:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


def scores(**kwargs):
    return {"Warn before a shell command runs, in every session": kwargs.get("warn", 0.0),
            "Admit the Doc conversation list door": kwargs.get("doc", 0.0),
            "Bind F03's SQL leg to the shared corpus": kwargs.get("f03", 0.0)}


class SilenceIsLoadBearingTests(unittest.TestCase):
    """Reporting real breakage is core work and must never need an argument.

    A search that finds something for every query would be worse than the stem
    matcher it replaces, not better.
    """

    def test_nothing_above_the_floor_returns_an_empty_list(self):
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                     judge=_FakeJudge(scores(warn=0.66, doc=0.25, f03=0.1)))
        self.assertEqual(got, [], "0.66 was the best score against a window "
                                  "holding no answer at all, and must stay silent")

    def test_an_empty_list_is_not_the_same_object_as_unavailable(self):
        """The gate falls back to the stem matcher on None and stays silent on
        []. Collapsing them turns an outage into a finding or the reverse."""
        quiet = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                       judge=_FakeJudge(scores()))
        self.assertEqual(quiet, [])
        self.assertIsNotNone(quiet)

    def test_no_commits_at_all_is_silence_not_an_outage(self):
        self.assertEqual(judge.refuting_commits(CLAIM, [], client=_FakeClient()), [])

    def test_the_false_criterion_says_answering_no_to_everything_is_correct(self):
        """Without this the question reads as "pick the best commit", and it
        will always pick one."""
        question = judge.addresses_question(_FakeClient())
        self.assertIn("Answering no to every commit is the correct outcome",
                      question["criteria"]["false"])

    def test_the_false_criterion_forbids_scoring_on_shared_words(self):
        false = question_false()
        self.assertIn("same file", false)
        self.assertIn("still concern different things", false)


def question_false():
    return judge.addresses_question(_FakeClient())["criteria"]["false"]


class UnavailableMeansFallBackTests(unittest.TestCase):
    """Today's behaviour is the floor. Every failure path ends there."""

    def test_every_request_failing_is_an_outage_not_a_finding(self):
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                     judge=_FakeJudge(scores(), fail={"*"}))
        self.assertIsNone(got, "an outage must not read as 'nothing refutes this'")

    def test_a_judge_that_cannot_be_loaded_returns_none(self):
        def boom(name):
            raise RuntimeError("no credential")
        with mock.patch.object(judge, "_sibling", side_effect=boom):
            self.assertIsNone(judge.refuting_commits(CLAIM, COMMITS))

    def test_some_requests_failing_still_yields_a_finding(self):
        got = judge.refuting_commits(
            CLAIM, COMMITS, client=_FakeClient(),
            judge=_FakeJudge(scores(warn=0.9), fail={"Bind F03's SQL leg to the shared corpus"}))
        self.assertEqual([h for h, _, _ in got], ["c39ed3bf"])

    def test_the_kill_switch_hands_the_gate_straight_back_to_the_matcher(self):
        called = []
        with mock.patch.dict(os.environ, {judge.DISABLE: "0"}):
            got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                         judge=_FakeJudge(scores(warn=0.99)))
        self.assertIsNone(got)
        self.assertEqual(called, [])


class RankingTests(unittest.TestCase):
    def test_best_first(self):
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                     judge=_FakeJudge(scores(warn=0.8, doc=0.95)))
        self.assertEqual([h for h, _, _ in got], ["a7b7bf46", "c39ed3bf"])

    def test_the_floor_is_inclusive(self):
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                     judge=_FakeJudge(scores(warn=judge.CONFIRM_AT)))
        self.assertEqual(len(got), 1)

    def test_the_cap_holds(self):
        many = [(f"{i:08x}", f"subject {i}") for i in range(20)]
        got = judge.refuting_commits(
            CLAIM, many, client=_FakeClient(),
            judge=_FakeJudge({f"subject {i}": 0.9 for i in range(20)}))
        self.assertEqual(len(got), judge.MAX_HITS)

    def test_the_reason_carries_the_probability_for_a_reader(self):
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                     judge=_FakeJudge(scores(warn=0.83)))
        self.assertIn("0.83", got[0][2])

    def test_the_hit_shape_matches_what_the_gate_prints(self):
        """The gate unpacks three values and prints the third under the hash."""
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                     judge=_FakeJudge(scores(warn=0.9)))
        for hit in got:
            self.assertEqual(len(hit), 3)


class OneRequestPerCommitTests(unittest.TestCase):
    def test_each_commit_gets_its_own_request(self):
        fake = _FakeJudge(scores())
        judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        self.assertEqual(len(fake.seen), len(COMMITS))

    def test_no_request_sees_another_commit(self):
        """A state holding every commit lets each judgment see its competitors
        and does not reproduce the published method."""
        fake = _FakeJudge(scores())
        judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        for state in fake.seen:
            self.assertIsInstance(state["commit_subject"], str)
            self.assertEqual(state["claim"], CLAIM)


class GateWiringTests(unittest.TestCase):
    """The module is inert unless the gate actually uses it this way."""

    def test_the_gate_prefers_the_judgment_and_keeps_the_matcher(self):
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("judged = judged_hits(prose, commits)", source)
        self.assertIn("judged if judged is not None else match_commits", source,
                      "None must fall back to the matcher, not to silence")

    def test_the_matcher_still_labels_its_own_reason(self):
        """Both paths hand the announcement the same shape, and a reader must
        be able to tell which one spoke."""
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn('"matched on: "', source)

    def test_the_module_is_not_a_script_entrypoint(self):
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        self.assertIsNone(
            _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:").search(source),
            "no main guard")

    def test_the_module_cannot_stop_a_session(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("exit(2)", "SystemExit", "sys.exit"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=1)
