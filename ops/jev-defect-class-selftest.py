"""Offline suite for ops/jev_defect_class.py. No credential, no network, no spend.

Every model call arrives through an injected fake. The cases that earn their
place are the ones that decide whether this is safe to put in front of a
recorder: it must never pick a class, it must survive a store it cannot reach
and requests that fail, and it must ask ONE request per class rather than one
request carrying the whole corpus — the batched shape does not reproduce the
published method and is the easiest thing to regress into.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_defect_class.py"
SPEC = importlib.util.spec_from_file_location("jev_defect_class", MODULE_PATH)
assert SPEC and SPEC.loader
sel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sel)


CORPUS = [
    {"name": "dated-artifact-read-as-present-state", "occurrences": 29,
     "claimed": "Reported the August plan as the current one.",
     "actual": "It was superseded in September."},
    {"name": "silent_failure", "occurrences": 2,
     "claimed": "Said the job completed.", "actual": "It exited non-zero and nobody looked."},
    {"name": "verb-refuses-its-own-required-path", "occurrences": 2,
     "claimed": "Said the verb accepted the field.", "actual": "It refused and named no field."},
]

PROPOSED = {"claimed": "Told the partner the dashboard was live.",
            "actual": "The dashboard had never been opened by a human."}


class _FakeJudge:
    """Stands in for ops/jev_judge. Records every state it is asked about.

    Reproduces the REAL response shape, with answers nested under an "answers"
    key. An earlier module read one level shallower and every test passed
    against a flattened fake until mutation testing exposed it; a fake that
    lies about the shape is worse than no fake.
    """

    def __init__(self, scores, fail=()):
        self.scores = scores
        self.fail = set(fail)
        self.seen = []

    def judge(self, state, questions, **kwargs):
        self.seen.append(state)
        name = state["existing_class"]["name"]
        if name in self.fail:
            raise RuntimeError("service did not answer")
        return {"answers": {"belongs": {"noul": self.scores.get(name, 0.0)}}}


def scores(**by_readable_name):
    return {name.replace("_", " "): value for name, value in by_readable_name.items()}


class OneRequestPerClassTests(unittest.TestCase):
    """The published method is one request per candidate. A batched request
    lets each judgment see its competitors and does not reproduce its results."""

    def test_each_class_gets_its_own_request(self):
        judge = _FakeJudge({})
        sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(), workers=1)
        self.assertEqual(len(judge.seen), len(CORPUS))

    def test_no_request_sees_another_candidate(self):
        judge = _FakeJudge({})
        sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(), workers=1)
        for state in judge.seen:
            self.assertIsInstance(state["existing_class"], dict,
                                  "a state holding a list of classes is the batched shape")
            names = [state["existing_class"]["name"]]
            self.assertEqual(len(names), 1)

    def test_the_proposed_defect_reaches_every_request(self):
        judge = _FakeJudge({})
        sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(), workers=1)
        for state in judge.seen:
            self.assertEqual(state["proposed_defect"]["actual"], PROPOSED["actual"])


class RankingTests(unittest.TestCase):
    def test_highest_first(self):
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.7,
                                     "silent failure": 0.95,
                                     "verb refuses its own required path": 0.8}))
        got = sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(), workers=1)
        self.assertEqual([name for name, _, _ in got],
                         ["silent_failure", "verb-refuses-its-own-required-path",
                          "dated-artifact-read-as-present-state"])

    def test_the_floor_trims_the_tail(self):
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.9,
                                     "silent failure": 0.1,
                                     "verb refuses its own required path": 0.2}))
        got = sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(),
                            workers=1, floor=0.6)
        self.assertEqual([name for name, _, _ in got],
                         ["dated-artifact-read-as-present-state"])

    def test_the_limit_caps_what_a_reader_is_shown(self):
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.9,
                                     "silent failure": 0.95,
                                     "verb refuses its own required path": 0.8}))
        got = sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(),
                            workers=1, limit=2)
        self.assertEqual(len(got), 2)

    def test_the_occurrence_count_survives_the_ranking(self):
        """A class seen 29 times and one seen twice are different invitations,
        and the count is the only thing that says which is which."""
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.9}))
        got = sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(), workers=1)
        self.assertEqual(got[0][2], 29)

    def test_concurrency_does_not_change_the_answer(self):
        """Serial scoring of three hundred classes takes minutes and would not
        be used, so this runs concurrently — which must not reorder anything."""
        values = scores(**{"dated artifact read as present state": 0.9,
                           "silent failure": 0.95,
                           "verb refuses its own required path": 0.8})
        serial = sel.shortlist(PROPOSED, CORPUS, judge=_FakeJudge(values),
                               client=_FakeClient(), workers=1)
        parallel = sel.shortlist(PROPOSED, CORPUS, judge=_FakeJudge(values),
                                 client=_FakeClient(), workers=8)
        self.assertEqual(serial, parallel)

    def test_ties_break_deterministically(self):
        values = scores(**{"dated artifact read as present state": 0.8,
                           "silent failure": 0.8,
                           "verb refuses its own required path": 0.8})
        first = sel.shortlist(PROPOSED, CORPUS, judge=_FakeJudge(values),
                              client=_FakeClient(), workers=4)
        second = sel.shortlist(PROPOSED, CORPUS, judge=_FakeJudge(values),
                               client=_FakeClient(), workers=4)
        self.assertEqual(first, second, "a shortlist that reshuffles is not readable")


class SurvivesFailureTests(unittest.TestCase):
    def test_a_failed_request_drops_only_that_class(self):
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.9,
                                     "silent failure": 0.95}),
                           fail=["silent failure"])
        got = sel.shortlist(PROPOSED, CORPUS, judge=judge, client=_FakeClient(), workers=1)
        self.assertEqual([name for name, _, _ in got],
                         ["dated-artifact-read-as-present-state"])

    def test_an_empty_corpus_yields_an_empty_shortlist(self):
        self.assertEqual(sel.shortlist(PROPOSED, [], judge=_FakeJudge({}),
                                       client=_FakeClient()), [])

    def test_a_store_that_cannot_be_reached_yields_no_corpus(self):
        def boom(sql):
            raise OSError("no route to host")
        self.assertEqual(sel.load_classes(runner=boom), [],
                         "a caller must carry on, not stop")

    def test_junk_lines_in_the_corpus_are_skipped(self):
        def runner(sql):
            return ('psql: NOTICE something\n'
                    '{"name": "a", "occurrences": 1, "claimed": "c", "actual": "x"}\n'
                    'not json at all\n'
                    '{broken\n')
        got = sel.load_classes(runner=runner)
        self.assertEqual([c["name"] for c in got], ["a"])

    def test_a_class_row_missing_its_text_does_not_raise(self):
        judge = _FakeJudge({})
        got = sel.shortlist(PROPOSED, [{"name": "bare"}], judge=judge,
                            client=_FakeClient(), workers=1)
        self.assertEqual(got, [])


class NeverPicksTests(unittest.TestCase):
    """The measured top of this ranking is flat — the same held-out defect came
    back 1st and then 4th on identical inputs. Anything that reads as a
    decision is a misreading of the measurement."""

    def test_the_advice_says_it_is_not_a_decision(self):
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.9}))
        note = sel.advise(PROPOSED, classes=CORPUS, judge=judge,
                          client=_FakeClient(), workers=1)
        self.assertIn("None of these is a decision", note)
        self.assertIn("choose", note)

    def test_the_advice_names_the_count_because_the_count_is_the_point(self):
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.9,
                                     "silent failure": 0.9}))
        note = sel.advise(PROPOSED, classes=CORPUS, judge=judge,
                          client=_FakeClient(), workers=1)
        self.assertIn("seen 29 times", note)
        self.assertIn("seen twice" if "seen twice" in note else "seen 2 times", note)

    def test_nothing_above_the_floor_says_nothing_at_all(self):
        judge = _FakeJudge(scores(**{"dated artifact read as present state": 0.1}))
        self.assertIsNone(sel.advise(PROPOSED, classes=CORPUS, judge=judge,
                                     client=_FakeClient(), workers=1))

    def test_the_library_files_nothing(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("record-defect", "record_defect", "call-verb", "run.sh"):
            self.assertNotIn(forbidden, source.split('"""', 2)[2],
                             f"{forbidden} outside the docstring would make this act")


class QuestionContractTests(unittest.TestCase):
    def test_the_false_criterion_forbids_scoring_on_topic(self):
        """Without this the selector scores subject-matter overlap and returns
        the same handful of classes for every defect, which is exactly the
        degeneracy an earlier rule selector shipped with."""
        question = sel.belongs_question(_FakeClient())
        false = question["criteria"]["false"].lower()
        self.assertIn("shared subject matter is not the same class", false)
        self.assertIn("mechanism", false)

    def test_the_true_criterion_expects_different_surface_details(self):
        question = sel.belongs_question(_FakeClient())
        self.assertIn("different files", question["criteria"]["true"])

    def test_the_class_name_is_presented_as_the_category(self):
        question = sel.belongs_question(_FakeClient())
        self.assertIn("illustration", question["instructions"])


class ShapeTests(unittest.TestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        """A shebang or a main guard makes this a NEW sealed ingress, and
        admitting one costs a registry successor with a production migration.
        Uses the inventory's own regex, not a substring search."""
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        self.assertIsNone(
            _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:").search(source),
            "no main guard")

    def test_the_corpus_query_takes_exactly_one_anchor_per_class(self):
        """One anchor, not three. Measured: three illustrations moved the true
        class from 1st to 11th, because their specifics outweigh the name."""
        self.assertIn("limit 1", sel.CORPUS_SQL)

    def test_the_corpus_query_is_read_only(self):
        for forbidden in ("insert", "update ", "delete", "drop", "alter"):
            self.assertNotIn(forbidden, sel.CORPUS_SQL.lower())


class _FakeClient:
    """Stands in for ops/typesafe_client, for the question builders only."""

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


if __name__ == "__main__":
    unittest.main(verbosity=1)
