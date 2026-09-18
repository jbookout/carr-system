"""Offline suite for ops/jev_rule_select.py. No credential, no network, no spend.

Every judgment arrives through an injected fake, so this runs on a hosted runner
with nothing configured. The cases that earn their place are the ones that would
have caught a defect this module actually had: the response envelope is nested
one level deeper than a caller expects, and neither sibling module is importable
as a package.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_rule_select.py"
SPEC = importlib.util.spec_from_file_location("jev_rule_select", MODULE_PATH)
sel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sel)


class FakeJudge:
    """Stands in for ops/jev_judge.py, with its real response envelope."""

    JudgeUnavailable = RuntimeError

    def __init__(self, by_gist=None, default=0.1, fail_on=None):
        self.by_gist = by_gist or {}
        self.default = default
        self.fail_on = fail_on or set()
        self.subjects = []

    def judge(self, subject, questions, **kwargs):
        self.subjects.append(subject)
        gist = subject["rule"]
        if gist in self.fail_on:
            raise self.JudgeUnavailable("synthetic outage")
        value = self.by_gist.get(gist, self.default)
        # The real judge returns the decoded response, so the answer sits under
        # "answers". A caller reading one level too shallow is the defect this
        # envelope exists to catch.
        return {"answers": {"binds": {"type": "noul", "noul": value}},
                "usage": {"input_tokens": 1}, "model": "fake", "elapsed_ms": 1}


class FakeClient:
    """Stands in for ops/typesafe_client.py's question builders."""

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


RULES = [
    {"id": "aaaaaaaa", "gist": "binds here", "context": ""},
    {"id": "bbbbbbbb", "gist": "also binds", "context": ""},
    {"id": "cccccccc", "gist": "does not bind", "context": ""},
]


class SelectionTests(unittest.TestCase):
    def test_only_rules_over_the_floor_are_surfaced(self):
        judge = FakeJudge({"binds here": 0.95, "also binds": 0.88,
                           "does not bind": 0.40})
        out = sel.select("a moment", RULES, floor=0.85,
                         client=FakeClient, judge=judge)
        self.assertEqual([r["id"] for r in out], ["aaaaaaaa", "bbbbbbbb"])

    def test_the_floor_is_inclusive_and_actually_moves(self):
        judge = FakeJudge({"binds here": 0.85, "also binds": 0.84,
                           "does not bind": 0.10})
        out = sel.select("a moment", RULES, floor=0.85,
                         client=FakeClient, judge=judge)
        self.assertEqual([r["id"] for r in out], ["aaaaaaaa"],
                         "0.84 must fall below a floor of 0.85")

    def test_results_are_ordered_by_probability(self):
        judge = FakeJudge({"binds here": 0.90, "also binds": 0.99,
                           "does not bind": 0.95})
        out = sel.select("a moment", RULES, floor=0.5,
                         client=FakeClient, judge=judge)
        self.assertEqual([r["id"] for r in out],
                         ["bbbbbbbb", "cccccccc", "aaaaaaaa"])

    def test_the_cap_is_enforced(self):
        rules = [{"id": f"{i:08d}", "gist": f"rule {i}", "context": ""}
                 for i in range(20)]
        judge = FakeJudge(default=0.99)
        out = sel.select("a moment", rules, floor=0.5, limit=5,
                         client=FakeClient, judge=judge)
        self.assertEqual(len([r for r in out if r["probability"] is not None]), 5)

    def test_one_request_per_rule_and_no_rule_sees_another(self):
        judge = FakeJudge(default=0.9)
        sel.select("a moment", RULES, floor=0.5, client=FakeClient, judge=judge)
        self.assertEqual(len(judge.subjects), len(RULES))
        for subject in judge.subjects:
            serialized = json.dumps(subject)
            others = [r["gist"] for r in RULES if r["gist"] != subject["rule"]]
            for other in others:
                self.assertNotIn(other, serialized,
                                 "a rule's request must not carry a competitor")

    def test_a_failed_judgment_is_reported_not_dropped(self):
        judge = FakeJudge(default=0.99, fail_on={"also binds"})
        out = sel.select("a moment", RULES, floor=0.5,
                         client=FakeClient, judge=judge)
        failed = [r for r in out if r["probability"] is None]
        self.assertEqual([r["id"] for r in failed], ["bbbbbbbb"],
                         "a rule that could not be judged must stay visible")

    def test_concurrency_does_not_change_the_answer(self):
        """The whole corpus asked serially took over a minute on the first live
        run, so select() asks in parallel. The risk that introduces is order:
        a pool that returns out of sequence would silently reshuffle ties."""
        rules = [{"id": f"{i:08d}", "gist": f"rule {i}", "context": ""}
                 for i in range(30)]
        scores = {f"rule {i}": 0.5 + i / 100 for i in range(30)}
        serial = sel.select("a moment", rules, floor=0.5, limit=30,
                            client=FakeClient, judge=FakeJudge(scores), workers=1)
        parallel = sel.select("a moment", rules, floor=0.5, limit=30,
                              client=FakeClient, judge=FakeJudge(scores), workers=16)
        self.assertEqual([r["id"] for r in serial], [r["id"] for r in parallel])
        self.assertEqual([r["probability"] for r in serial],
                         [r["probability"] for r in parallel])

    def test_an_outage_does_not_raise_at_the_caller(self):
        judge = FakeJudge(default=0.99, fail_on={r["gist"] for r in RULES})
        out = sel.select("a moment", RULES, client=FakeClient, judge=judge)
        self.assertTrue(all(r["probability"] is None for r in out))


class QuestionShapeTests(unittest.TestCase):
    def test_the_false_criterion_excuses_a_sound_but_irrelevant_rule(self):
        """Without this the answer drifts to 'is this a good rule', which every
        active rule passes, which selects everything, which selects nothing."""
        question = sel.binding_question(client=FakeClient)
        false = question["criteria"]["false"].casefold()
        self.assertIn("excellent rule", false)
        self.assertIn("not bind", false)

    def test_the_question_asks_about_binding_not_about_quality(self):
        question = sel.binding_question(client=FakeClient)
        self.assertIn("binds", question["instructions"].casefold())


class CorpusTests(unittest.TestCase):
    def test_the_real_rule_corpus_loads_and_is_not_empty(self):
        rules = sel.load_rules()
        self.assertGreater(len(rules), 50)
        self.assertTrue(all(r["id"] and isinstance(r["gist"], str) for r in rules))

    def test_unreachable_rules_are_a_real_and_live_measurement(self):
        """The count must come from the files, never from a number in prose."""
        unreachable = sel.unreachable_rules()
        rules = sel.load_rules()
        reachable = sel.reachable_rule_ids()
        self.assertEqual(len(unreachable), len(rules) - len(
            {r["id"] for r in rules} & reachable))
        self.assertGreater(len(unreachable), 0,
                           "if every rule became reachable, this module's "
                           "premise changed and its docstring must be re-read")

    def test_the_docstring_quotes_no_count(self):
        """A number in prose becomes a dated artifact read as present state —
        the most-logged failure class in this system."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        docstring = source.split('"""')[1]
        import re as _re
        # A provenance date is not a count and is required, so strip ISO dates
        # first. Anything numeric left in the prose is a measurement that moved.
        prose = _re.sub(r"\d{4}-\d{2}-\d{2}", "", docstring)
        # A probability recorded against a date is an observation, not a count:
        # it describes what happened once and does not go stale the way "there
        # are N unreachable rules" does. Counts are the thing being banned.
        prose = _re.sub(r"\d*\.\d+", "", prose)
        found = _re.findall(r"\b(\d{2,})\b", prose)
        self.assertEqual(found, [], f"the docstring quotes {found}; these are "
                                    "counts that move — call the function instead")

    def test_regex_delivery_reproduces_the_git_push_trigger(self):
        delivered = sel.regex_delivery("git push origin HEAD")
        self.assertIn("173119a8", delivered)
        self.assertNotIn("173119a8", sel.regex_delivery("ls -la"))


class ShadowTests(unittest.TestCase):
    def test_shadow_records_both_sets_and_both_differences(self):
        judge = FakeJudge(default=0.99)
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "shadow.jsonl")
            record = sel.shadow_selection(
                "about to push a branch", "git push origin HEAD",
                log_path=log, rules=RULES, floor=0.5,
                client=FakeClient, judge=judge)
            written = json.loads(Path(log).read_text(encoding="utf-8").strip())
        self.assertEqual(record["judged_would_surface"], written["judged_would_surface"])
        for field in ("judged_would_surface", "regex_did_surface",
                      "judged_only", "regex_only"):
            self.assertIn(field, record)
        self.assertIn("173119a8", record["regex_did_surface"])

    def test_shadow_computes_no_accuracy_number(self):
        """Scoring against the existing bundle measures agreement with a
        known-crude mechanism and would be read as a score. See the docstring."""
        judge = FakeJudge(default=0.99)
        with tempfile.TemporaryDirectory() as tmp:
            record = sel.shadow_selection(
                "a moment", "git push", log_path=os.path.join(tmp, "s.jsonl"),
                rules=RULES, floor=0.5, client=FakeClient, judge=judge)
        for banned in ("accuracy", "precision", "recall", "agreement", "score"):
            self.assertNotIn(banned, record,
                             f"{banned} would be read as a verdict on the selector")

    def test_a_broken_log_path_never_reaches_the_caller(self):
        judge = FakeJudge(default=0.99)
        record = sel.shadow_selection(
            "a moment", "git push", log_path="/proc/nonexistent/s.jsonl",
            rules=RULES, floor=0.5, client=FakeClient, judge=judge)
        self.assertIn("judged_would_surface", record)

    def test_shadow_decides_nothing(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in ("sys.exit", "raise SystemExit", "os._exit"):
            self.assertNotIn(forbidden, source,
                             "shadow mode must never stop a caller")


class EntrypointTests(unittest.TestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        """Uses the sealed inventory's own detector, not a substring search. A
        shebang or a main guard here would move the frontier and owe a registry
        successor — and the detector is a regex over the WHOLE file, so even an
        example inside this docstring would seal it."""
        source = MODULE_PATH.read_text(encoding="utf-8")
        import re as _re
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
