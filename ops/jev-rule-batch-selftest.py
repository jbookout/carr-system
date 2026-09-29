#!/usr/bin/env python3
"""Offline contract for one shared-state rule-binding request."""
import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).with_name("rule_trigger_delivery.py")
spec = importlib.util.spec_from_file_location("rule_trigger_delivery_batch_test", path)
assert spec and spec.loader
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


class Client:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions,
                "criteria": {"true": true, "false": false}}


class BatchContract(unittest.TestCase):
    def test_shortlist_uses_one_request_with_scoped_questions(self):
        rules = [
            {"id": "rule0001", "statement": "Before sending, verify recipient."},
            {"id": "rule0002", "statement": "When reviewing code, check tests."},
        ]
        calls = []

        def ask(state, questions, **kwargs):
            calls.append((state, questions, kwargs))
            return {"model": "jev-1.13.0", "answers": {
                "bind_rule0001": {"noul": 0.91},
                "bind_rule0002": {"noul": 0.14},
            }}

        selected, report = delivery.judge_budgeted(
            "send the email", rules, [], ask=ask, client=Client,
            titles={"rule0001": ("send rule", "outbound"),
                    "rule0002": ("review rule", "code")},
            deadline=10**12)
        self.assertEqual(report["calls"], 1)
        self.assertEqual(set(selected), {"rule0001"})
        self.assertEqual(len(calls), 1)
        state, questions, _ = calls[0]
        self.assertEqual(state["situation"], "send the email")
        self.assertEqual(set(questions), {"bind_rule0001", "bind_rule0002"})
        for rule_id in ("rule0001", "rule0002"):
            self.assertIn(f"rules.{rule_id}", questions[f"bind_{rule_id}"]["instructions"])
        self.assertIn("rule0001", state["rules"])

    def test_serial_fallback_is_explicit(self):
        calls = []

        def ask(state, questions, **kwargs):
            calls.append((state, questions))
            return {"model": "jev-1.13.0", "answers": {"binds": {"noul": 0.9}}}

        selected, report = delivery.judge_budgeted(
            "send", [{"id": "rule0001", "statement": "send"}], [],
            ask=ask, client=Client, serial_fallback=True, deadline=10**12)
        self.assertEqual(report["calls"], 1)
        self.assertEqual(set(selected), {"rule0001"})
        self.assertEqual(set(calls[0][1]), {"binds"})

    def test_batch_floor_is_calibrated_separately_from_serial(self):
        rule = [{"id": "rule0001", "statement": "Before sending, verify recipient."}]

        def ask(state, questions, **kwargs):
            return {"model": "jev-1.13.0", "answers": {
                next(iter(questions)): {"noul": 0.35}}}

        batched, _ = delivery.judge_budgeted(
            "send", rule, [], ask=ask, client=Client, deadline=10**12)
        serial, _ = delivery.judge_budgeted(
            "send", rule, [], ask=ask, client=Client,
            serial_fallback=True, deadline=10**12)
        self.assertEqual(set(batched), {"rule0001"})
        self.assertEqual(serial, {})


if __name__ == "__main__":
    unittest.main()
