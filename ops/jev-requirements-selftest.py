"""Offline suite for ops/jev_requirements.py. No credential, no network, no spend.

A fake jev_judge stands in for the vendor and a throwaway git repository stands
in for the turn's working tree, so the suite checks the contract the
completion-evidence gate relies on: requirements are split deterministically,
every requirement rides in ONE request, a low score yields one advisory line, an
outage fails open with an error row, and a turn with no diff costs no call.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()
SPEC = importlib.util.spec_from_file_location("jev_requirements", OPS / "jev_requirements.py")
assert SPEC and SPEC.loader
req = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(req)


class FakeClient:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}


class FakeJudge:
    def __init__(self, probs=None, error=None):
        self.probs, self.error = probs or {}, error
        self.rows, self.calls, self.last = [], 0, None

    def _client(self):
        return FakeClient

    def judge(self, subject, questions, timeout=None):
        self.calls += 1
        self.last = (subject, questions, timeout)
        if self.error:
            raise self.error
        return {"answers": {key: {"type": "noul", "noul": self.probs.get(key, 0.9)}
                            for key in questions},
                "model": "fake", "usage": {}, "elapsed_ms": 1}

    def record(self, kind, subject_ref, answer, existing_decision=None, *, note=None, error=None):
        self.rows.append({"kind": kind, "subject_ref": subject_ref, "note": note,
                          "error": error is not None})


PROMPT = """Hi Claude, thanks for the help earlier.

Please do the following:
- Add a --dry-run flag to the exporter.
- Write a selftest for it.

Also update the README. Why did the last run fail?
Sounds good."""


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def edit(path, tool_id="t1"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": "Edit", "input": {"file_path": path}}]}}


def no_llm(prompt):
    return None


class Repo:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        env = {**ENV, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.path = os.path.join(root, "exporter.py")
        with open(self.path, "w") as fh:
            fh.write("print('export')\n")
        for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git", "-C", root, *args], check=True, env=env, capture_output=True)
        return self

    def change(self):
        with open(self.path, "a") as fh:
            fh.write("DRY_RUN = '--dry-run'\n")

    def __exit__(self, *exc):
        self.tmp.cleanup()


class SplitTests(unittest.TestCase):
    def test_split_keeps_orders_and_drops_greetings_filler_questions(self):
        got = req.split_requirements(PROMPT)
        self.assertEqual(got, ["Add a --dry-run flag to the exporter.",
                               "Write a selftest for it.",
                               "Also update the README."])

    def test_request_phrased_as_a_question_is_kept(self):
        self.assertEqual(req.split_requirements("Can you rename the flag to --plan?"),
                         ["Can you rename the flag to --plan?"])

    def test_code_fences_and_system_tags_are_not_requirements(self):
        text = ("Fix the parser.\n```\nrm -rf build and rebuild everything now\n```\n"
                "<system-reminder>Do the whole other thing please.</system-reminder>")
        self.assertEqual(req.split_requirements(text), ["Fix the parser."])

    def test_local_llm_outage_returns_none(self):
        def refuse(request, timeout=None):
            raise OSError("connection refused")
        self.assertIsNone(req.local_llm_requirements("Add a flag.", opener=refuse))


class CheckTests(unittest.TestCase):
    def test_all_requirements_batched_into_one_request(self):
        with Repo() as repo:
            repo.change()
            fake = FakeJudge()
            out = req.check({"session_id": "s1"}, [user(PROMPT), edit(repo.path)],
                            judge_module=fake, llm=no_llm)
            self.assertIsNone(out)
            self.assertEqual(fake.calls, 1)
            subject, questions, _ = fake.last
            self.assertEqual(sorted(questions), ["req_1", "req_2", "req_3"])
            self.assertIn("Requirement 2: \"Write a selftest for it.\"",
                          questions["req_2"]["instructions"])
            self.assertIn("--dry-run", subject["diff"])
            self.assertEqual(subject["task"], PROMPT)
            self.assertEqual(fake.rows[0]["kind"], "requirement_checklist")
            self.assertEqual(fake.rows[0]["note"]["source"], "split")

    def test_low_score_yields_one_advisory_line(self):
        with Repo() as repo:
            repo.change()
            fake = FakeJudge(probs={"req_3": 0.05, "req_2": 0.2})
            out = req.check({"session_id": "s1"}, [user(PROMPT), edit(repo.path)],
                            judge_module=fake, llm=no_llm)
            self.assertEqual(out.count("\n"), 0)
            self.assertIn("requirement 3 may be unmet (p=0.05)", out)
            self.assertIn("README", out)
            self.assertIn("+1 more", out)

    def test_outage_fails_open_and_records_an_error_row(self):
        with Repo() as repo:
            repo.change()
            fake = FakeJudge(error=RuntimeError("JudgeUnavailable: timeout"))
            out = req.check({"session_id": "s1"}, [user(PROMPT), edit(repo.path)],
                            judge_module=fake, llm=no_llm)
            self.assertIsNone(out)
            self.assertEqual(len(fake.rows), 1)
            self.assertTrue(fake.rows[0]["error"])

    def test_no_diff_means_no_call(self):
        with Repo() as repo:
            fake = FakeJudge()
            # Edited then reverted: a path, but an empty diff.
            self.assertIsNone(req.check({"session_id": "s1"}, [user(PROMPT), edit(repo.path)],
                                        judge_module=fake, llm=no_llm))
            # No mutation tool at all.
            self.assertIsNone(req.check({"session_id": "s1"}, [user(PROMPT)],
                                        judge_module=fake, llm=no_llm))
            self.assertEqual(fake.calls, 0)
            self.assertEqual(fake.rows, [])

    def test_selftest_session_is_skipped(self):
        with Repo() as repo:
            repo.change()
            fake = FakeJudge()
            self.assertIsNone(req.check({"session_id": "selftest"}, [user(PROMPT), edit(repo.path)],
                                        judge_module=fake, llm=no_llm))
            self.assertEqual(fake.calls, 0)

    def test_local_llm_list_is_used_when_it_answers(self):
        with Repo() as repo:
            repo.change()
            fake = FakeJudge()
            req.check({"session_id": "s1"}, [user(PROMPT), edit(repo.path)], judge_module=fake,
                      llm=lambda prompt: ["Add a --dry-run flag", "Update the README"])
            self.assertEqual(sorted(fake.last[1]), ["req_1", "req_2"])
            self.assertEqual(fake.rows[0]["note"]["source"], "local_llm")

    def test_only_the_last_human_prompt_counts(self):
        with Repo() as repo:
            repo.change()
            fake = FakeJudge()
            tool_result = {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
            recs = [user("Delete the whole database right now."), edit(repo.path, "t0"),
                    user("Add a dry run flag to the exporter."), edit(repo.path), tool_result]
            req.check({"session_id": "s1"}, recs, judge_module=fake, llm=no_llm)
            self.assertEqual(fake.last[0]["task"], "Add a dry run flag to the exporter.")

    def test_broken_input_never_raises(self):
        self.assertIsNone(req.check(None, [{"type": "user", "message": 7}, "junk"]))


if __name__ == "__main__":
    unittest.main()
