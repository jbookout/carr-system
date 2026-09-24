"""Offline suite for ops/jev_handoff.py. No credential, no network, no spend.

A fake jev_judge stands in for the vendor, so the suite checks the contract the
two gates rely on: a confident yes blocks, anything else (a no, an unsure
middle, an outage, an unreadable answer) falls back to the keyword patterns,
and every judgment is recorded beside the keyword decision.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("jev_handoff", OPS / "jev_handoff.py")
assert SPEC and SPEC.loader
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


class FakeClient:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}


class FakeJudge:
    def __init__(self, noul=None, error=None):
        self.noul, self.error, self.rows, self.calls = noul, error, [], 0

    def _client(self):
        return FakeClient

    def judge(self, subject, questions, timeout=None):
        self.calls += 1
        if self.error:
            raise self.error
        return {"answers": {"hands_off": {"type": "noul", "noul": self.noul}},
                "model": "fake", "usage": {}, "elapsed_ms": 1}

    def record(self, kind, subject_ref, answer, existing_decision=None, error=None):
        self.rows.append((kind, subject_ref, existing_decision, error is not None))


HANDOFF = "Trust the Homebrew tap, then run the install when you get a chance."


class HandoffJudgeTests(unittest.TestCase):
    def test_confident_yes_hands_off_and_is_recorded(self):
        fake = FakeJudge(noul=0.93)
        self.assertTrue(handoff.hands_off(HANDOFF, surface="stop",
                                          existing_decision=False, judge_module=fake))
        self.assertEqual(fake.rows, [("command_handoff", "stop", False, False)])

    def test_unsure_middle_does_not_block(self):
        fake = FakeJudge(noul=0.6)
        self.assertFalse(handoff.hands_off(HANDOFF, surface="ask", judge_module=fake))

    def test_outage_falls_back_and_is_recorded(self):
        fake = FakeJudge(error=RuntimeError("timeout"))
        self.assertIsNone(handoff.judge(HANDOFF, surface="stop", judge_module=fake))
        self.assertEqual(fake.rows, [("command_handoff", "stop", None, True)])

    def test_unreadable_answer_is_no_judgment(self):
        fake = FakeJudge(noul="not a number")
        self.assertIsNone(handoff.judge(HANDOFF, surface="stop", judge_module=fake))

    def test_text_with_no_computer_cue_costs_no_call(self):
        fake = FakeJudge(noul=0.99)
        self.assertIsNone(handoff.judge("The meeting went well; Dr. Patel agreed.",
                                        surface="stop", judge_module=fake))
        self.assertEqual(fake.calls, 0)

    def test_long_message_is_trimmed_to_its_tail(self):
        captured = {}
        fake = FakeJudge(noul=0.1)
        original = fake.judge

        def spy(subject, questions, timeout=None):
            captured["len"] = len(subject["message"])
            return original(subject, questions, timeout=timeout)

        fake.judge = spy
        handoff.judge("x" * 20000 + " run it", surface="stop", judge_module=fake)
        self.assertEqual(captured["len"], handoff.MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
