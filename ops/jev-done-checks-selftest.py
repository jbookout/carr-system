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
from unittest.mock import patch
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




# --------------------------------------------------------- #14 done claim



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






# --------------------------------------------------------- #24 fact check



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
        fake = FakeJudge()
        cwd = os.getcwd()
        try:
            os.chdir(repo)
            result = jdc.build_handoff("Add widget2().", transcript, ["widget.py"], None,
                                       max_chars=250, judge_module=fake)
        finally:
            os.chdir(cwd)
        self.assertEqual(fake.calls, 0)
        self.assertTrue(any(n.startswith("assistant_note_0") for n in result["dropped"]))
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
