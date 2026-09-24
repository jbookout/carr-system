#!/usr/bin/env python3
"""Offline suite for ops/jev_scorecard.py. No credential, no network, no spend.

The HTTP call to the local flash server is mocked through run_task's own
`chat_opener` hook (the same convention ops/typesafe_client.py's `ask(...,
opener=...)` uses), so this never dials out. Covers: extracting code from a
model reply, grading an implementation task and a write-tests (mutation) task
in a real temp directory, the batched-noul fuzzy grader against a fake Jev
client, and the deterministic summary arithmetic.
"""

import importlib.util
import io
import json
import unittest
import urllib.error
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_scorecard.py"
SPEC = importlib.util.spec_from_file_location("jev_scorecard", MODULE_PATH)
assert SPEC and SPEC.loader
sc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sc)


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _chat_opener_returning(content, usage=None):
    def opener(request, timeout=None):
        return _FakeResponse({"choices": [{"message": {"content": content}}],
                               "usage": usage or {"total_tokens": 42}})
    return opener


def _chat_opener_raising_http():
    def opener(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 500, "boom", {}, io.BytesIO(b"server exploded"))
    return opener


def _chat_opener_raising_connection():
    def opener(request, timeout=None):
        raise urllib.error.URLError("connection refused")
    return opener


IMPL_TASK = {
    "id": "t_add", "category": "demo", "lang": "py", "kind": "impl",
    "prompt": "write add(a, b)",
    "test": 'check("basic", lambda: add(1, 2) == 3)\n'
            'check("neg", lambda: add(-1, -1) == -2)\n',
}

MUTATION_TASK = {
    "id": "t_wrap", "category": "demo", "lang": "py", "kind": "mutation",
    "prompt": "write tests for f",
    "impl": "def f(x):\n    return x + 1\n",
    "mutants": ["def f(x):\n    return x + 2\n", "def f(x):\n    return x + 1\n"],
}


class ExtractCodeTests(unittest.TestCase):
    def test_pulls_the_fenced_block(self):
        text = "here you go\n```python\ndef f():\n    return 1\n```\nthanks"
        self.assertIn("def f():", sc.extract_code(text, lang="py"))

    def test_strips_a_think_block_first(self):
        text = "<think>plan the approach</think>```python\ndef g():\n    pass\n```"
        code = sc.extract_code(text, lang="py")
        self.assertNotIn("plan the approach", code)
        self.assertIn("def g():", code)

    def test_prefers_the_larger_block_when_untagged(self):
        text = "```\nshort\n```\n```\nmuch longer block of code here\n```"
        self.assertEqual(sc.extract_code(text).strip(), "much longer block of code here")

    def test_no_fence_returns_the_stripped_text(self):
        self.assertEqual(sc.extract_code("  bare code  \n"), "bare code")

    def test_a_short_lang_code_matches_a_full_fence_tag(self):
        text = "```javascript\nconsole.log(1)\n```"
        self.assertIn("console.log", sc.extract_code(text, lang="js"))


class LoadSuiteTests(unittest.TestCase):
    def test_the_real_suite_loads_and_is_not_empty(self):
        tasks = sc.load_suite()
        self.assertGreater(len(tasks), 5)
        self.assertTrue(all(t.get("id") and t.get("prompt") for t in tasks))

    def test_every_task_has_a_grading_path(self):
        for task in sc.load_suite():
            if task.get("kind") == "mutation":
                self.assertIn("impl", task)
                self.assertIn("mutants", task)
            else:
                self.assertIn("test", task)


class GradeImplTests(unittest.TestCase):
    def test_a_correct_candidate_passes(self):
        result = sc.grade_candidate(IMPL_TASK, "def add(a, b):\n    return a + b\n")
        self.assertTrue(result["pass"])
        self.assertIn("2/2", result["subtests"])

    def test_a_wrong_candidate_fails(self):
        result = sc.grade_candidate(IMPL_TASK, "def add(a, b):\n    return a - b\n")
        self.assertFalse(result["pass"])

    def test_a_crashing_candidate_fails_without_raising(self):
        result = sc.grade_candidate(IMPL_TASK, "raise SyntaxError this is not python")
        self.assertFalse(result["pass"])


class GradeMutationTests(unittest.TestCase):
    def test_a_thorough_suite_kills_every_mutant_and_passes(self):
        suite = (
            "import unittest\nfrom solution import f\n"
            "class T(unittest.TestCase):\n"
            "    def test_f(self):\n        self.assertEqual(f(1), 2)\n"
        )
        result = sc.grade_candidate(MUTATION_TASK, suite)
        self.assertTrue(result["correct_passes"])
        self.assertEqual(result["killed"], "1/2")
        self.assertFalse(result["pass"], "one mutant (identical to impl) cannot be killed")

    def test_a_suite_that_fails_the_correct_impl_does_not_pass(self):
        suite = (
            "import unittest\nfrom solution import f\n"
            "class T(unittest.TestCase):\n"
            "    def test_f(self):\n        self.assertEqual(f(1), 999)\n"
        )
        result = sc.grade_candidate(MUTATION_TASK, suite)
        self.assertFalse(result["correct_passes"])
        self.assertFalse(result["pass"])


class RunTaskTests(unittest.TestCase):
    def test_a_passing_reply_is_graded_and_reported(self):
        opener = _chat_opener_returning("```python\ndef add(a, b):\n    return a + b\n```")
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=opener)
        self.assertTrue(out["any_pass"])
        self.assertTrue(out["first_pass"])
        self.assertEqual(out["id"], "t_add")
        self.assertEqual(len(out["attempts"]), 1)

    def test_multiple_attempts_are_each_graded(self):
        opener = _chat_opener_returning("```python\ndef add(a, b):\n    return a + b\n```")
        out = sc.run_task(IMPL_TASK, attempts=3, chat_opener=opener)
        self.assertEqual(len(out["attempts"]), 3)
        self.assertTrue(out["any_pass"])

    def test_an_http_failure_is_recorded_on_the_attempt_not_raised(self):
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=_chat_opener_raising_http())
        self.assertFalse(out["any_pass"])
        self.assertIn("HTTP 500", out["attempts"][0]["chat_error"])

    def test_a_connection_failure_is_recorded_on_the_attempt_not_raised(self):
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=_chat_opener_raising_connection())
        self.assertFalse(out["any_pass"])
        self.assertIn("chat_error", out["attempts"][0])

    def test_a_reply_with_no_usable_code_fails_gracefully(self):
        opener = _chat_opener_returning("sorry, I cannot help with that")
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=opener)
        self.assertFalse(out["any_pass"])


class GradeFuzzyTests(unittest.TestCase):
    class _FakeJudge:
        JudgeUnavailable = RuntimeError
        SHADOW_LOG = "unused-in-tests"

        def __init__(self, probs=None, fail=False):
            self.probs = probs or {}
            self.fail = fail
            self.calls = []
            self.records = []

        def judge(self, subject, questions, **kwargs):
            self.calls.append((subject, questions))
            if self.fail:
                raise self.JudgeUnavailable("synthetic outage")
            answers = {qid: {"type": "noul", "noul": self.probs.get(qid, 0.5)}
                       for qid in questions}
            return {"model": "fake", "answers": answers}

        def record(self, *a, **kw):
            self.records.append((a, kw))

    class _FakeClient:
        @staticmethod
        def noul(instructions, true=None, false=None):
            return {"type": "noul", "instructions": instructions,
                    "criteria": {"true": true, "false": false}}

    def test_all_subchecks_satisfied_is_true(self):
        judge = self._FakeJudge({"c0": 0.95, "c1": 0.9})
        out = sc.grade_fuzzy("some output", ["mentions X", "is polite"],
                              client=self._FakeClient, judge=judge)
        self.assertTrue(out["verdict"])
        self.assertFalse(out["escalate"])
        self.assertEqual(len(judge.calls), 1, "every sub-check batched into one request")

    def test_one_failing_subcheck_makes_the_verdict_false(self):
        judge = self._FakeJudge({"c0": 0.95, "c1": 0.1})
        out = sc.grade_fuzzy("some output", ["mentions X", "is polite"],
                              client=self._FakeClient, judge=judge)
        self.assertFalse(out["verdict"])

    def test_an_ambiguous_subcheck_escalates(self):
        judge = self._FakeJudge({"c0": 0.5})
        out = sc.grade_fuzzy("some output", ["mentions X"],
                              client=self._FakeClient, judge=judge)
        self.assertTrue(out["escalate"])

    def test_no_subchecks_is_vacuously_true_and_does_not_ask_jev(self):
        judge = self._FakeJudge(fail=True)
        out = sc.grade_fuzzy("some output", [], client=self._FakeClient, judge=judge)
        self.assertTrue(out["verdict"])
        self.assertEqual(len(judge.calls), 0)

    def test_an_outage_reports_unavailable_not_an_exception(self):
        judge = self._FakeJudge(fail=True)
        out = sc.grade_fuzzy("some output", ["mentions X"],
                              client=self._FakeClient, judge=judge)
        self.assertEqual(out["verdict"], "unavailable")
        self.assertTrue(out["escalate"])


class SummarizeTests(unittest.TestCase):
    def test_counts_by_category_and_overall(self):
        results = [
            {"category": "parser", "any_pass": True, "first_pass": True},
            {"category": "parser", "any_pass": False, "first_pass": False},
            {"category": "js", "any_pass": True, "first_pass": False},
        ]
        out = sc.summarize(results)
        self.assertEqual(out["by_category"]["parser"], {"n": 2, "any_pass": 1, "first_pass": 1})
        self.assertEqual(out["by_category"]["js"], {"n": 1, "any_pass": 1, "first_pass": 0})
        self.assertEqual(out["overall"], {"n": 3, "any_pass": 2, "first_pass": 1})

    def test_empty_results_summarize_to_zero(self):
        out = sc.summarize([])
        self.assertEqual(out["overall"], {"n": 0, "any_pass": 0, "first_pass": 0})


class EntrypointTests(unittest.TestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
