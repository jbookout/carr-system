#!/usr/bin/env python3
"""Offline suite for ops/jev_done_checks.py. No credential, no network, no spend.

A fake jev_judge (and a fake typesafe client) stand in for the vendor, so the
suite checks the contract every caller of ops/jev_done_checks.py relies on:
each public function returns a plain dict, never raises, fires only on its
deterministic trigger, and falls back to verdict "unavailable" when Jev cannot
be reached. build_handoff is checked separately, against its own
{"pack", "kept", "dropped"} contract.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(OPS))
from git_env import fixture_env  # noqa: E402

SPEC = importlib.util.spec_from_file_location("jev_done_checks", OPS / "jev_done_checks.py")
assert SPEC and SPEC.loader
jdc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jdc)


# --------------------------------------------------------------- fakes

class FakeClient:
    """Stands in for ops/typesafe_client.py's question builders."""

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}

    @staticmethod
    def choice(instructions, options):
        return {"type": "choice", "instructions": instructions, "options": dict(options)}

    @staticmethod
    def score(instructions, levels):
        return {"type": "score", "instructions": instructions, "levels": list(levels)}


class FakeGitEnv:
    @staticmethod
    def scrubbed_env():
        return dict(fixture_env())


class FakeJudge:
    """Stands in for ops/jev_judge.py. `answers` maps question-id -> answer body."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.rows = []
        self.calls = 0
        self.last = None

    def _client(self):
        return FakeClient

    def judge(self, subject, questions, *, timeout=None, client=None, api_key=None):
        self.calls += 1
        self.last = (subject, questions)
        if self.error:
            raise self.error
        out = {}
        for qid, q in questions.items():
            body = self.answers.get(qid)
            if body is None:
                if q["type"] == "noul":
                    body = {"type": "noul", "noul": 0.5}
                elif q["type"] == "choice":
                    body = {"type": "choice", "choice": "__none__", "confidence": 0.5}
                else:
                    body = {"type": "score", "score": 0.0, "confidence": 0.5}
            out[qid] = body
        return {"answers": out, "model": "fake", "usage": {}, "elapsed_ms": 1}

    def record(self, kind, subject_ref, answer, existing_decision=None, *, note=None, error=None):
        self.rows.append({"kind": kind, "subject_ref": subject_ref, "note": note,
                          "error": error is not None})


# --------------------------------------------------------- #13 test quality

GOOD_TEST = """
def test_withdraw_rejects_over_balance():
    account = Account(balance=10)
    with pytest.raises(InsufficientFunds):
        account.withdraw(50)
    assert account.balance == 10
"""

WEAK_TEST = """
def test_it_runs():
    result = do_the_thing()
    assert result
"""

CODE_UNDER_TEST = "def withdraw(self, amount):\n    if amount > self.balance:\n        raise InsufficientFunds()\n"


class TestQualityTests(unittest.TestCase):
    def test_no_test_source_is_not_triggered_and_costs_no_call(self):
        fake = FakeJudge()
        result = jdc.check_test_quality("", CODE_UNDER_TEST, "task", judge_module=fake)
        self.assertEqual(result["verdict"], "not_triggered")
        self.assertEqual(fake.calls, 0)

    def test_non_test_source_is_not_triggered(self):
        fake = FakeJudge()
        result = jdc.check_test_quality("x = 1\ny = 2\n", CODE_UNDER_TEST, "task", judge_module=fake)
        self.assertEqual(result["verdict"], "not_triggered")
        self.assertEqual(fake.calls, 0)

    def test_solid_tests_verdict(self):
        fake = FakeJudge(answers={
            "asserts_behavior": {"type": "noul", "noul": 0.95},
            "covers_stated_edge_cases": {"type": "noul", "noul": 0.9},
            "could_pass_with_wrong_impl": {"type": "noul", "noul": 0.05},
            "happy_path_only": {"type": "noul", "noul": 0.05},
        })
        result = jdc.check_test_quality(GOOD_TEST, CODE_UNDER_TEST,
                                        "Reject withdrawals over the balance.",
                                        judge_module=fake)
        self.assertEqual(result["check"], "test_quality")
        self.assertEqual(result["verdict"], "solid")
        self.assertFalse(result["escalate"])
        self.assertNotIn("advice", result["detail"])
        self.assertEqual(fake.calls, 1)
        self.assertEqual(fake.rows[0]["kind"], "supervise.test_quality")

    def test_weak_tests_verdict_carries_advice(self):
        fake = FakeJudge(answers={
            "asserts_behavior": {"type": "noul", "noul": 0.1},
            "covers_stated_edge_cases": {"type": "noul", "noul": 0.1},
            "could_pass_with_wrong_impl": {"type": "noul", "noul": 0.9},
            "happy_path_only": {"type": "noul", "noul": 0.9},
        })
        result = jdc.check_test_quality(WEAK_TEST, CODE_UNDER_TEST,
                                        "Reject withdrawals over the balance.",
                                        judge_module=fake)
        self.assertEqual(result["verdict"], "weak")
        self.assertIn("tautological_or_mocked", result["detail"]["red_flags"])
        self.assertIn("advice", result["detail"])

    def test_empty_code_under_test_does_not_crash(self):
        fake = FakeJudge(answers={
            "asserts_behavior": {"type": "noul", "noul": 0.9},
            "covers_stated_edge_cases": {"type": "noul", "noul": 0.9},
            "could_pass_with_wrong_impl": {"type": "noul", "noul": 0.1},
            "happy_path_only": {"type": "noul", "noul": 0.1},
        })
        result = jdc.check_test_quality(GOOD_TEST, "", "task", judge_module=fake)
        self.assertEqual(result["verdict"], "solid")

    def test_unavailable_path(self):
        fake = FakeJudge(error=RuntimeError("timeout"))
        result = jdc.check_test_quality(GOOD_TEST, CODE_UNDER_TEST, "task", judge_module=fake)
        self.assertEqual(result["verdict"], "unavailable")
        self.assertIn("error", result["detail"])


# --------------------------------------------------------- #14 done claim

class DoneClaimTests(unittest.TestCase):
    def test_no_completion_word_is_no_claim_and_costs_no_call(self):
        fake = FakeJudge()
        result = jdc.check_done_claim("Here is a summary of the changes.", {}, judge_module=fake)
        self.assertEqual(result["verdict"], "no_claim")
        self.assertEqual(fake.calls, 0)

    def test_supported_claim(self):
        fake = FakeJudge(answers={
            "claims_supported": {"type": "noul", "noul": 0.95},
            "evidence_shows_omitted_failure": {"type": "noul", "noul": 0.05},
        })
        evidence = {"test_command": "pytest", "test_output": "3 passed", "test_exit_code": 0}
        result = jdc.check_done_claim("All done, tests pass.", evidence, judge_module=fake)
        self.assertEqual(result["check"], "done_claim")
        self.assertEqual(result["verdict"], "supported")
        self.assertNotIn("advice", result["detail"])

    def test_evidence_shows_omitted_failure(self):
        fake = FakeJudge(answers={
            "claims_supported": {"type": "noul", "noul": 0.3},
            "evidence_shows_omitted_failure": {"type": "noul", "noul": 0.9},
        })
        evidence = {"test_output": "1 failed, 2 passed", "test_exit_code": 1}
        result = jdc.check_done_claim("Fixed it, all done.", evidence, judge_module=fake)
        self.assertEqual(result["verdict"], "unsupported")
        self.assertIn("advice", result["detail"])
        self.assertIn("claims_supported", result["detail"])

    def test_unavailable_path(self):
        fake = FakeJudge(error=RuntimeError("boom"))
        result = jdc.check_done_claim("All done.", {}, judge_module=fake)
        self.assertEqual(result["verdict"], "unavailable")


# --------------------------------------------------------- #17 review triage

DIFF_TWO_FILES = """diff --git a/src/widgets.py b/src/widgets.py
index 111..222 100644
--- a/src/widgets.py
+++ b/src/widgets.py
@@ -1,3 +1,4 @@
+# a comment
 def widget():
     return 1
diff --git a/src/auth.py b/src/auth.py
index 333..444 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,3 +1,4 @@
+def check_password(pw):
+    return pw == stored_hash
"""


class TriageReviewTests(unittest.TestCase):
    def test_empty_diff_is_not_triggered(self):
        fake = FakeJudge()
        result = jdc.triage_review("", "task", judge_module=fake)
        self.assertEqual(result["verdict"], "not_triggered")
        self.assertEqual(fake.calls, 0)

    def test_deterministic_floor_flags_auth_path_without_a_call(self):
        fake = FakeJudge(answers={})
        result = jdc.triage_review("diff --git a/src/auth.py b/src/auth.py\n@@ -1 +1 @@\n+x\n",
                                   "task", judge_module=fake)
        self.assertEqual(result["verdict"], "needs_review")
        self.assertEqual(result["detail"]["files"]["src/auth.py"]["source"], "deterministic_floor")
        self.assertIn("advice", result["detail"])

    def test_mixed_diff_one_floor_one_judged_high(self):
        files = jdc.split_diff_by_file(DIFF_TWO_FILES)
        widgets_key = jdc._safe_id("src/widgets.py")
        fake = FakeJudge(answers={widgets_key: {"type": "score", "score": 1.8, "confidence": 0.7}})
        result = jdc.triage_review(DIFF_TWO_FILES, "task", judge_module=fake)
        self.assertEqual(result["verdict"], "needs_review")
        self.assertEqual(result["detail"]["files"]["src/auth.py"]["source"], "deterministic_floor")
        self.assertEqual(result["detail"]["files"]["src/widgets.py"]["risk"], "high")
        self.assertEqual(fake.calls, 1)  # one request even with two files total
        self.assertEqual(len(files), 2)

    def test_low_risk_diff_is_ok(self):
        fake = FakeJudge(answers={
            jdc._safe_id("src/widgets.py"): {"type": "score", "score": 0.1, "confidence": 0.8},
        })
        diff = "diff --git a/src/widgets.py b/src/widgets.py\n@@ -1 +1 @@\n+# comment\n"
        result = jdc.triage_review(diff, "task", judge_module=fake)
        self.assertEqual(result["verdict"], "ok")
        self.assertNotIn("advice", result["detail"])

    def test_unavailable_path(self):
        fake = FakeJudge(error=RuntimeError("down"))
        result = jdc.triage_review(DIFF_TWO_FILES, "task", judge_module=fake)
        self.assertEqual(result["verdict"], "unavailable")


class TriageReviewCacheTests(unittest.TestCase):
    """The Stop hook re-triages an unchanged diff at every Stop; an identical
    diff and task is asked once per window, and the cache only ever costs an
    extra ask."""

    DIFF = "diff --git a/src/widgets.py b/src/widgets.py\n@@ -1 +1 @@\n+x = 1\n"
    HIGH = {jdc._safe_id("src/widgets.py"): {"type": "score", "score": 1.8, "confidence": 0.7}}

    def test_repeated_identical_diff_asks_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            fake = FakeJudge(answers=self.HIGH)
            first = jdc.triage_review(self.DIFF, "task", judge_module=fake,
                                      cache_path=cache, now=1000.0)
            second = jdc.triage_review(self.DIFF, "task", judge_module=fake,
                                       cache_path=cache, now=1100.0)
            self.assertEqual(fake.calls, 1)
            self.assertEqual(len(fake.rows), 1)  # a hit writes no call row
            self.assertEqual(first, second)
            self.assertEqual(second["verdict"], "needs_review")

    def test_a_changed_diff_or_task_asks_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            fake = FakeJudge(answers=self.HIGH)
            jdc.triage_review(self.DIFF, "task", judge_module=fake, cache_path=cache, now=1000.0)
            jdc.triage_review(self.DIFF + "+y = 2\n", "task", judge_module=fake,
                              cache_path=cache, now=1001.0)
            jdc.triage_review(self.DIFF, "another task", judge_module=fake,
                              cache_path=cache, now=1002.0)
            self.assertEqual(fake.calls, 3)

    def test_cache_expiry_asks_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            fake = FakeJudge(answers=self.HIGH)
            jdc.triage_review(self.DIFF, "task", judge_module=fake, cache_path=cache, now=1000.0)
            jdc.triage_review(self.DIFF, "task", judge_module=fake, cache_path=cache,
                              now=1000.0 + 31 * 60)
            self.assertEqual(fake.calls, 2)

    def test_an_unwritable_cache_still_triages(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file")
            Path(blocker).write_text("not a directory", encoding="utf-8")
            fake = FakeJudge(answers=self.HIGH)
            for step in range(2):
                result = jdc.triage_review(self.DIFF, "task", judge_module=fake,
                                           cache_path=os.path.join(blocker, "c.json"),
                                           now=1000.0 + step)
                self.assertEqual(result["verdict"], "needs_review")
            self.assertEqual(fake.calls, 2)

    def test_an_outage_is_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "c.json")
            down = FakeJudge(error=RuntimeError("down"))
            self.assertEqual(jdc.triage_review(self.DIFF, "task", judge_module=down,
                                               cache_path=cache, now=1000.0)["verdict"],
                             "unavailable")
            up = FakeJudge(answers=self.HIGH)
            jdc.triage_review(self.DIFF, "task", judge_module=up, cache_path=cache, now=1001.0)
            self.assertEqual(up.calls, 1)


# --------------------------------------------------------- #24 fact check

class FactCheckTests(unittest.TestCase):
    def test_no_passages_is_unsupported_without_a_call(self):
        fake = FakeJudge()
        result = jdc.fact_check("Dr. CRE is the app persona.", [], judge_module=fake)
        self.assertEqual(result["verdict"], "unsupported")
        self.assertEqual(fake.calls, 0)
        self.assertIn("advice", result["detail"])

    def test_supported(self):
        fake = FakeJudge(answers={
            "which_supports": {"type": "choice", "choice": "dr-cre-concept", "confidence": 0.9},
            "contra_" + jdc._safe_id("dr-cre-concept"): {"type": "noul", "noul": 0.05},
        })
        passages = [{"ref": "dr-cre-concept", "text": "The app persona is Dr. CRE."}]
        result = jdc.fact_check("The app persona is Dr. CRE.", passages, judge_module=fake)
        self.assertEqual(result["check"], "fact_check")
        self.assertEqual(result["verdict"], "supported")
        self.assertEqual(result["detail"]["supporting_passage"], "dr-cre-concept")

    def test_contradicted(self):
        ref = "loop-250"
        fake = FakeJudge(answers={
            "which_supports": {"type": "choice", "choice": "__none__", "confidence": 0.6},
            "contra_" + jdc._safe_id(ref): {"type": "noul", "noul": 0.9},
        })
        passages = [{"ref": ref, "text": "The origination conversation predates capture and was never recovered."}]
        result = jdc.fact_check("Loop #250's origination conversation was recovered.", passages,
                                judge_module=fake)
        self.assertEqual(result["verdict"], "contradicted")
        self.assertTrue(result["escalate"])
        self.assertEqual(result["detail"]["contradicted_by"][0][0], ref)

    def test_none_addresses_it(self):
        fake = FakeJudge(answers={
            "which_supports": {"type": "choice", "choice": "__none__", "confidence": 0.8},
            "contra_" + jdc._safe_id("unrelated-doc"): {"type": "noul", "noul": 0.05},
        })
        passages = [{"ref": "unrelated-doc", "text": "Something else entirely."}]
        result = jdc.fact_check("Claim nothing here addresses.", passages, judge_module=fake)
        self.assertEqual(result["verdict"], "unsupported")

    def test_unavailable_path(self):
        fake = FakeJudge(error=RuntimeError("down"))
        passages = [{"ref": "r1", "text": "text"}]
        result = jdc.fact_check("claim", passages, judge_module=fake)
        self.assertEqual(result["verdict"], "unavailable")


# --------------------------------------------------------- #10 handoff pack

def make_repo():
    tmp = tempfile.mkdtemp()
    env = {**fixture_env(), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=tmp, env=env, check=True)
    path = os.path.join(tmp, "widget.py")
    with open(path, "w") as fh:
        fh.write("def widget():\n    return 1\n")
    subprocess.run(["git", "add", "widget.py"], cwd=tmp, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp, env=env, check=True)
    with open(path, "a") as fh:
        fh.write("\ndef widget2():\n    return 2\n")
    return tmp


def write_transcript(path, notes):
    with open(path, "w") as fh:
        for i, text in enumerate(notes):
            rec = {"type": "assistant", "message": {"role": "assistant",
                   "content": [{"type": "text", "text": text}]}}
            fh.write(json.dumps(rec) + "\n")


class HandoffTests(unittest.TestCase):
    def test_empty_inputs_yield_empty_pack(self):
        result = jdc.build_handoff("", None, [], None, judge_module=FakeJudge())
        self.assertEqual(result, {"pack": "", "kept": [], "dropped": []})

    def test_under_budget_keeps_everything_with_no_call(self):
        repo = make_repo()
        transcript = os.path.join(repo, "transcript.jsonl")
        write_transcript(transcript, ["Working on the widget change."])
        fake = FakeJudge()
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = jdc.build_handoff("Add widget2().", transcript, ["widget.py"],
                                       None, judge_module=fake)
        finally:
            os.chdir(cwd)
        self.assertEqual(fake.calls, 0)
        self.assertIn("task", result["kept"])
        self.assertIn("diff:widget.py", result["kept"])
        self.assertEqual(result["dropped"], [])
        self.assertIn("Add widget2", result["pack"])

    def test_over_budget_drops_low_relevance_items(self):
        repo = make_repo()
        transcript = os.path.join(repo, "transcript.jsonl")
        write_transcript(transcript, ["An old, unrelated aside about lunch plans."])
        task_key = jdc._safe_id("task")
        diff_key = jdc._safe_id("diff:widget.py")
        note_key = jdc._safe_id("assistant_note_0")
        fake = FakeJudge(answers={
            task_key: {"type": "noul", "noul": 0.95},
            diff_key: {"type": "noul", "noul": 0.9},
            note_key: {"type": "noul", "noul": 0.05},
        })
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = jdc.build_handoff("Add widget2().", transcript, ["widget.py"], None,
                                       max_chars=250, judge_module=fake)
        finally:
            os.chdir(cwd)
        self.assertEqual(fake.calls, 1)
        self.assertIn("assistant_note_0", result["dropped"])
        self.assertIn("task", result["kept"])
        self.assertIn("diff:widget.py", result["kept"])
        self.assertLessEqual(len(result["pack"]), 250)

    def test_jev_unavailable_falls_back_to_deterministic_priority(self):
        repo = make_repo()
        transcript = os.path.join(repo, "transcript.jsonl")
        write_transcript(transcript, ["note one", "note two", "note three"])
        fake = FakeJudge(error=RuntimeError("down"))
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = jdc.build_handoff("Add widget2().", transcript, ["widget.py"],
                                       "Traceback: boom", max_chars=80, judge_module=fake)
        finally:
            os.chdir(cwd)
        self.assertIn("task", result["kept"])
        self.assertTrue(len(result["pack"]) <= 80)

    def test_failure_output_is_collected(self):
        result = jdc.build_handoff("Fix the bug.", None, [], "Traceback: KeyError",
                                   judge_module=FakeJudge())
        self.assertIn("last_failure", result["kept"])
        self.assertIn("KeyError", result["pack"])


if __name__ == "__main__":
    unittest.main()
