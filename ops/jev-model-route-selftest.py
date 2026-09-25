"""Offline suite for ops/jev_model_route.py and ops/flash_answer_checks.py. No credential, no network, no spend.

A fake judge stands in for Jev, so the suite checks what code decides from Jev's scores: the policy order and
cutoffs, the abstain fallback, the overflow when Flash is busy, failing open when Jev is down, the facts-only
hand-off, and the answer checks on the real cases from the 2026-09-24 Flash script tests.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, OPS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route = _load("jev_model_route")
checks = _load("flash_answer_checks")
POLICY = route.load_policy()


class FakeClient:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}


class FakeJudge:
    def __init__(self, scores=None, error=None):
        self.scores, self.error, self.subjects = scores or {}, error, []

    def _client(self):
        return FakeClient

    def judge(self, subject, questions, timeout=None, client=None):
        self.subjects.append((subject, questions))
        if self.error:
            raise self.error
        return {"answers": {k: {"type": "noul", "noul": self.scores.get(k, 0.0)} for k in questions}}


def decide(scores=None, error=None, **kw):
    return route.decide("a task", "ctx", policy=POLICY, judge=FakeJudge(scores, error), rng=lambda: 0.99,
                        log_path=None, **kw)


class PolicyFile(unittest.TestCase):
    def test_every_ordered_question_names_a_route_in_the_roster(self):
        for name in POLICY["order"]:
            self.assertIn(POLICY["questions"][name]["route"], POLICY["routes"])
        self.assertIn(POLICY["abstain_route"], POLICY["routes"])

    def test_every_route_carries_model_effort_and_protocol(self):
        for name, entry in POLICY["routes"].items():
            for key in ("model", "effort", "protocol"):
                self.assertTrue(entry.get(key), f"{name} lacks {key}")
            self.assertIn(entry["model"], POLICY["models"])

    def test_flash_routes_hand_off_somewhere(self):
        for name, entry in POLICY["routes"].items():
            if entry["model"] == "flash":
                self.assertTrue(entry.get("then", {}).get("desk"), name)


class Routing(unittest.TestCase):
    def test_code_wins_over_everything_after_it(self):
        row = decide({"code": 0.9, "beyond": 0.9, "script": 0.9, "direct": 0.9})
        self.assertEqual((row["route"], row["model"], row["protocol"]), ("code", "flash", "flash-run"))
        self.assertEqual(row["then"]["desk"], "sol-fixer")

    def test_judgment_goes_to_opus_and_never_to_flash(self):
        row = decide({"beyond": 0.56, "script": 0.9, "direct": 0.9})
        self.assertEqual((row["route"], row["model"], row["desk"]), ("escalate", "opus", "claude-desktop"))

    def test_cutoff_is_inclusive_and_below_it_falls_through(self):
        self.assertEqual(decide({"script": 0.45})["route"], "script")
        self.assertEqual(decide({"script": 0.44, "direct": 0.40})["route"], "direct")

    def test_nothing_clears_means_logged_fallback(self):
        row = decide({"code": 0.1, "beyond": 0.1, "script": 0.1, "direct": 0.1})
        self.assertEqual(row["route"], POLICY["abstain_route"])
        self.assertTrue(row["fallback"])
        self.assertIsNone(row["jev_error"])

    def test_flash_busy_moves_a_flash_route_to_overflow(self):
        row = decide({"direct": 0.9}, flash_free=False)
        self.assertTrue(row["overflow"])
        self.assertEqual(row["model"], POLICY["overflow"]["model"])

    def test_flash_busy_leaves_an_opus_route_alone(self):
        row = decide({"beyond": 0.9}, flash_free=False)
        self.assertFalse(row["overflow"])
        self.assertEqual(row["model"], "opus")

    def test_jev_down_fails_open_to_the_fallback(self):
        row = decide(error=RuntimeError("no credential"))
        self.assertTrue(row["fallback"])
        self.assertIn("no credential", row["jev_error"])

    def test_jev_sees_the_task_and_every_policy_question(self):
        judge = FakeJudge({"direct": 0.9})
        route.decide("sum these", "1, 2", policy=POLICY, judge=judge, log_path=None)
        subject, questions = judge.subjects[0]
        self.assertEqual(subject, {"task": "sum these", "context": "1, 2"})
        self.assertEqual(set(questions), set(POLICY["questions"]))

    def test_the_decision_is_logged(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "routes.jsonl")
            route.decide("t", policy=POLICY, judge=FakeJudge({"direct": 0.9}), log_path=path)
            row = json.loads(open(path).read())
            self.assertEqual((row["task"], row["route"]), ("t", "direct"))

    def test_desk_for_reads_the_policy(self):
        self.assertEqual(route.desk_for("code", POLICY), "sol-fixer")
        self.assertEqual(route.desk_for("judgment", POLICY), "claude-desktop")


class Handoff(unittest.TestCase):
    def test_no_answer(self):
        self.assertEqual(route.handoff_reason("", []), "no_answer")

    def test_invented(self):
        self.assertEqual(route.handoff_reason("30.00", [{"support": "printed"}, {"support": "invented"}]), "invented")

    def test_two_consecutive_runaways(self):
        runaway = {"empty": True, "finish": "length"}
        self.assertEqual(route.handoff_reason("7", [runaway, runaway]), "runaway")
        self.assertIsNone(route.handoff_reason("7", [runaway, {"finish": "stop"}, runaway]))

    def test_grounded_answer_stays(self):
        self.assertIsNone(route.handoff_reason("7", [{"support": "printed"}]))


class AnswerChecks(unittest.TestCase):
    def test_rounded_printed_average_is_printed(self):
        # messy averages: the script printed avg 29.7393 and Flash answered 29.74, the truth
        self.assertEqual(checks.answer_support("29.74", ["parsed 3330\navg 29.7393"]), "printed")

    def test_made_up_average_is_invented(self):
        self.assertEqual(checks.answer_support("30.00", ["AVG all-perSF 30.07 n 3330\nrent 18.04 42.0"]),
                         "invented")

    def test_nearby_number_of_same_precision_is_not_a_source(self):
        self.assertEqual(checks.answer_support("30.00", ["30.01 30.02"]), "invented")

    def test_whole_number_never_sources_a_decimal(self):
        self.assertEqual(checks.answer_support("30.00", ["count 30"]), "invented")

    def test_sum_of_two_printed_counts_is_derived(self):
        self.assertEqual(checks.answer_support('{"kids": 300}', ["rules 276\nunclear 24"]), "derived")

    def test_even_split_is_invented_even_when_a_pair_matches(self):
        # varied wording, repeat 3: 300 each after a crash, and 300 = 1500 - 1200 by coincidence
        answer = '{"dental": 300, "eye care": 300, "skin": 300, "heart": 300, "kids": 300}'
        self.assertEqual(checks.answer_support(answer, ["lines 1500\nmatched 1200"]), "invented")

    def test_uneven_real_counts_pass(self):
        self.assertEqual(checks.answer_support('{"dental": 318, "eye care": 319, "skin": 280}',
                                               ["dental 318 eye care 319 skin 280"]), "printed")

    def test_timeout_marker_is_not_output(self):
        self.assertEqual(checks.answer_support("600", ["partial\n[timed out after 600s]"]), "invented")


if __name__ == "__main__":
    unittest.main()
