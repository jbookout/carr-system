"""Offline suite for ops/stale_claim_judge.py and its use in the gate.

No credential, no network, no spend. Every model call arrives through an
injected fake.

THIS SUITE USED TO ASSERT THE BUG. Its first version had a class called
OneRequestPerCommitTests that checked the module sent one request per commit
and that no request saw another candidate. That was the reranking rule applied
to a roster it does not govern, and the tests locked it in — a suite can make a
mistake permanent as easily as it can catch one. The shape is now a single
Choice over the whole window, measured at one request and 0.6 seconds against
214 requests and 4.2 seconds for the same four answers, and the cases below
guard the new shape and the reasons for it.
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
    """Reproduces the REAL response shape: answers nested under "answers", and
    a Choice answer carrying a probabilities mapping over its options.

    A fake that flattens either nesting hides a defect rather than catching it,
    which is how an earlier module shipped reading one level too shallow.
    """

    def __init__(self, probabilities, fail=False):
        self.probabilities = probabilities
        self.fail = fail
        self.calls = []

    def judge(self, state, questions, **kwargs):
        self.calls.append((state, questions))
        if self.fail:
            raise RuntimeError("service did not answer")
        return {"answers": {"pick": {"type": "choice",
                                     "probabilities": dict(self.probabilities)}}}


class _FakeClient:
    @staticmethod
    def choice(instructions, options):
        if not isinstance(options, dict) or len(options) < 2:
            raise ValueError("a choice needs at least two options")
        return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


class OneRequestTests(unittest.TestCase):
    """The candidates compete for one slot, so they belong in one Choice.

    One request per candidate is the RERANKING rule and it governs a shortlist
    a keyword search produced first, not a whole roster.
    """

    def test_the_whole_window_goes_out_in_a_single_request(self):
        fake = _FakeJudge({"c39ed3bf": 0.9, judge.NONE_OF_THESE: 0.05})
        judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        self.assertEqual(len(fake.calls), 1,
                         "214 requests became one; do not go back")

    def test_every_commit_becomes_an_option(self):
        fake = _FakeJudge({"c39ed3bf": 0.9, judge.NONE_OF_THESE: 0.05})
        judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        options = fake.calls[0][1]["pick"]["criteria"]
        for commit, _ in COMMITS:
            self.assertIn(commit, options)

    def test_the_claim_is_the_state_not_an_option(self):
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.9})
        judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        self.assertEqual(fake.calls[0][0]["claim"], CLAIM)

    def test_option_rubrics_are_truncated_for_the_ranking_pass(self):
        """255 full-length options returns HTTP 400 max_tokens_exceeded, which
        is how truncation stopped being optional."""
        long_subject = "x" * 900
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.9})
        judge.refuting_commits(CLAIM, [("deadbeef", long_subject)],
                               client=_FakeClient(), judge=fake)
        rubric = fake.calls[0][1]["pick"]["criteria"]["deadbeef"]
        self.assertLessEqual(len(rubric), 150)

    def test_a_window_over_the_cap_is_trimmed_not_split(self):
        """Probabilities sum to one WITHIN a request, so numbers from two
        Choices cannot be compared and pooling them is a measurement error."""
        many = [(f"{i:08x}", f"subject {i}") for i in range(400)]
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.9})
        judge.refuting_commits(CLAIM, many, client=_FakeClient(), judge=fake)
        self.assertEqual(len(fake.calls), 1, "never split into two Choices")
        options = fake.calls[0][1]["pick"]["criteria"]
        self.assertLessEqual(len(options), judge.MAX_OPTIONS + 1)

    def test_the_trim_keeps_the_most_recent(self):
        many = [(f"{i:08x}", f"subject {i}") for i in range(400)]
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.9})
        judge.refuting_commits(CLAIM, many, client=_FakeClient(), judge=fake)
        options = fake.calls[0][1]["pick"]["criteria"]
        self.assertIn(f"{0:08x}", options, "recent_commits returns newest first")
        self.assertNotIn(f"{399:08x}", options)


class SilenceIsLoadBearingTests(unittest.TestCase):
    """Reporting real breakage is core work and must never need an argument."""

    def test_the_none_option_is_always_offered(self):
        """Choice probabilities sum to one, so without an explicit way to
        decline the model must hand back a commit for every claim."""
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.9})
        judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        self.assertIn(judge.NONE_OF_THESE, fake.calls[0][1]["pick"]["criteria"])

    def test_the_none_rubric_says_declining_is_the_common_case(self):
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.9})
        judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        rubric = fake.calls[0][1]["pick"]["criteria"][judge.NONE_OF_THESE]
        self.assertIn("COMMON CASE", rubric.upper())
        self.assertIn("share words", rubric)

    def test_the_none_option_winning_ends_it(self):
        """Measured: the two claims nothing answered put none at 0.96 and 0.77."""
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.77, "c39ed3bf": 0.15})
        self.assertEqual(
            judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake), [])

    def test_the_none_option_wins_ties(self):
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.5, "c39ed3bf": 0.5})
        self.assertEqual(
            judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake), [])

    def test_nothing_above_the_floor_is_silence(self):
        fake = _FakeJudge({judge.NONE_OF_THESE: 0.2, "c39ed3bf": 0.3, "a7b7bf46": 0.25})
        self.assertEqual(
            judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake,
                                   floor=0.5), [])

    def test_no_commits_at_all_is_silence_not_an_outage(self):
        self.assertEqual(judge.refuting_commits(CLAIM, [], client=_FakeClient()), [])


class UnavailableMeansFallBackTests(unittest.TestCase):
    """Today's behaviour is the floor. Every failure path ends there."""

    def test_a_failed_request_is_an_outage_not_a_finding(self):
        fake = _FakeJudge({}, fail=True)
        self.assertIsNone(
            judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake),
            "an outage must not read as 'nothing refutes this'")

    def test_an_answer_with_no_probabilities_is_an_outage(self):
        fake = _FakeJudge({})
        self.assertIsNone(
            judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake))

    def test_a_judge_that_cannot_be_loaded_returns_none(self):
        def boom(name):
            raise RuntimeError("no credential")
        with mock.patch.object(judge, "_sibling", side_effect=boom):
            self.assertIsNone(judge.refuting_commits(CLAIM, COMMITS))

    def test_the_kill_switch_hands_the_gate_back_to_the_matcher(self):
        with mock.patch.dict(os.environ, {judge.DISABLE: "0"}):
            self.assertIsNone(
                judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(),
                                       judge=_FakeJudge({"c39ed3bf": 0.99})))

    def test_an_option_the_window_does_not_hold_is_ignored(self):
        """A returned key that is not a known commit cannot be printed as one."""
        fake = _FakeJudge({"not-a-commit": 0.99, judge.NONE_OF_THESE: 0.01})
        self.assertEqual(
            judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake), [])


class RankingTests(unittest.TestCase):
    def test_best_first(self):
        fake = _FakeJudge({"c39ed3bf": 0.8, "a7b7bf46": 0.95, judge.NONE_OF_THESE: 0.01})
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        self.assertEqual([h for h, _, _ in got], ["a7b7bf46", "c39ed3bf"])

    def test_the_cap_holds(self):
        many = [(f"{i:08x}", f"subject {i}") for i in range(20)]
        probabilities = {f"{i:08x}": 0.9 for i in range(20)}
        probabilities[judge.NONE_OF_THESE] = 0.01
        got = judge.refuting_commits(CLAIM, many, client=_FakeClient(),
                                     judge=_FakeJudge(probabilities))
        self.assertEqual(len(got), judge.MAX_HITS)

    def test_the_reason_carries_both_numbers(self):
        """A reader needs the margin, not just the score: 0.55 against 0.40 and
        0.55 against 0.02 are different invitations."""
        fake = _FakeJudge({"c39ed3bf": 0.83, judge.NONE_OF_THESE: 0.04})
        got = judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake)
        self.assertIn("0.83", got[0][2])
        self.assertIn("0.04", got[0][2])

    def test_the_hit_shape_matches_what_the_gate_prints(self):
        fake = _FakeJudge({"c39ed3bf": 0.9, judge.NONE_OF_THESE: 0.01})
        for hit in judge.refuting_commits(CLAIM, COMMITS, client=_FakeClient(), judge=fake):
            self.assertEqual(len(hit), 3)
            self.assertEqual(hit[1], dict(COMMITS)[hit[0]], "subject must survive")


class GateWiringTests(unittest.TestCase):
    """The module is inert unless the gate actually uses it this way."""

    def test_the_gate_prefers_the_judgment_and_keeps_the_matcher(self):
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("judged = judged_hits(prose, commits)", source)
        self.assertIn("judged if judged is not None else match_commits", source,
                      "None must fall back to the matcher, not to silence")

    def test_the_matcher_still_labels_its_own_reason(self):
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
