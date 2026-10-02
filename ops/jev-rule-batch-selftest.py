#!/usr/bin/env python3
"""Offline contract for one shared-state rule-binding request."""
import importlib.util
import hashlib
import json
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
    def test_heldout_serial_labels_match_control_receipts(self):
        fixture = json.loads(path.with_name("fixtures").joinpath("rule-batch-parity.v1.json").read_text())
        client = delivery._sibling("typesafe_client")
        receipts = {receipt["state_sha256"]: receipt for receipt in fixture["serial_control_receipts"]}
        cases = {case["id"]: case for case in fixture["cases"]}
        for row in fixture["fresh_replays"][-1]["heldout"]:
            for rule_id, probability in row["serial"].items():
                rule = fixture["serial_control_rules"][rule_id]
                state = {"situation": cases[row["case"]]["prompt"], "rule_title": rule["gist"],
                         "rule": rule["statement"] or rule["gist"], "rule_context": rule["context"]}
                receipt = receipts[client.state_sha256(state)]
                self.assertEqual(receipt["model"], fixture["model"])
                self.assertEqual(receipt["answers"]["binds"]["noul"], probability)

    def test_variant_tables_recompute_from_frozen_scores(self):
        fixture = json.loads(path.with_name("fixtures").joinpath("rule-batch-parity.v1.json").read_text())
        def measure(rows, floor, serial=False):
            tp = fp = fn = 0
            losses = []
            for row in rows:
                for rule_id, score in row["batch"].items():
                    gold = row["serial"][rule_id] >= .75 if serial else rule_id == row["target"]
                    predicted = score >= floor
                    tp += int(gold and predicted)
                    fp += int(not gold and predicted)
                    fn += int(gold and not predicted)
                    losses.append((score - int(gold)) ** 2)
            return {"tp": tp, "fp": fp, "fn": fn, "recall": tp / (tp + fn),
                    "precision": tp / (tp + fp) if tp + fp else 0,
                    "brier": sum(losses) / len(losses)}
        for replay in fixture["fresh_replays"]:
            with self.subTest(variant=replay["variant"]):
                self.assertEqual(measure(replay["training"], replay["floor"]), replay["train_metrics"])
                self.assertEqual(measure(replay["heldout"], replay["floor"], True), replay["test_metrics"])
                for grid in replay["threshold_scores"]:
                    actual = measure(replay["training"], grid["floor"])
                    self.assertEqual({key: actual[key] for key in grid if key != "floor"},
                                     {key: value for key, value in grid.items() if key != "floor"})

    def test_frozen_parity_replays_through_both_public_paths(self):
        fixture = json.loads(path.with_name("fixtures").joinpath("rule-batch-parity.v1.json").read_text())
        selection = delivery._sibling("jev_rule_select")
        client = selection._sibling("typesafe_client")
        replay = fixture["fresh_replays"][-1]
        rows = replay["training"] + replay["heldout"]
        cases = {case["id"]: case for case in fixture["cases"]}
        totals = {"train": [0, 0, 0], "test": [0, 0, 0]}
        positives, negatives = [], []
        for row, receipt in zip(rows, replay["batch_receipts"], strict=True):
            case = cases[row["case"]]
            rules = [fixture["rules"][rule_id] for rule_id in case["candidates"]]
            calls = []

            def ask(state, questions, **kwargs):
                self.assertEqual(client.state_sha256(state), receipt["state_sha256"])
                questions_digest = hashlib.sha256(json.dumps(
                    questions, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                self.assertEqual(questions_digest, receipt["questions_sha256"])
                self.assertEqual(receipt["model"], fixture["model"])
                self.assertEqual({key.removeprefix("bind_"): value["noul"]
                                  for key, value in receipt["answers"].items()}, row["batch"])
                calls.append(questions)
                return receipt

            class Judge:
                JudgeUnavailable = RuntimeError
                judge = staticmethod(ask)

            with self.subTest(case=case["id"]):
                selected, report = delivery.judge_budgeted(
                    case["prompt"], rules, [], ask=ask, client=client,
                    titles={rule["id"]: (rule.get("gist", ""), rule.get("context", ""))
                            for rule in rules}, deadline=10**12)
                standalone = selection.select(case["prompt"], rules=rules,
                                              judge=Judge, client=client, limit=len(rules))
                expected = {key for key, value in row["batch"].items()
                            if value >= fixture["threshold_selection"]["floor"]}
                self.assertEqual(set(selected), expected)
                self.assertEqual({rule["id"] for rule in standalone}, expected)
                self.assertEqual(report["calls"], 1)
                self.assertEqual(len(calls), 2)
                for rule_id, score in row["batch"].items():
                    gold = rule_id == row["target"] if case["split"] == "train" else row["serial"][rule_id] >= .75
                    if gold:
                        totals[case["split"]][0 if rule_id in expected else 2] += 1
                    elif rule_id in expected:
                        totals[case["split"]][1] += 1
                    if case["split"] == "train":
                        (positives if gold else negatives).append(score)
        self.assertEqual(totals, {"train": [14, 2, 0], "test": [11, 0, 0]})
        interval = fixture["threshold_selection"]["training_interval"]
        self.assertEqual(min(positives), interval["inclusive_high"])
        self.assertEqual(sorted(negatives, reverse=True)[2], interval["exclusive_low"])
        self.assertAlmostEqual((min(positives) + sorted(negatives, reverse=True)[2]) / 2,
                               selection.BATCH_BIND_AT)
        for split, key in (("train", "train_metrics"), ("test", "test_metrics")):
            metrics = replay[key]
            self.assertEqual(totals[split], [metrics["tp"], metrics["fp"], metrics["fn"]])
        self.assertEqual(len(fixture["historical_variants"]), 6)

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

    def test_no_serial_option_on_either_interface(self):
        import inspect
        selection = delivery._sibling("jev_rule_select")
        for interface in (delivery.judge_budgeted, delivery.advise, selection.select):
            with self.subTest(interface=interface.__name__):
                self.assertNotIn("serial_fallback", inspect.signature(interface).parameters)

    def test_batch_question_preserves_serial_binding_criteria(self):
        selection = delivery._sibling("jev_rule_select")
        baseline = selection.binding_question(Client)
        scoped = selection.batch_binding_question("rule0001", Client)
        self.assertEqual(scoped["criteria"], baseline["criteria"])
        self.assertIn(baseline["instructions"], scoped["instructions"])
        self.assertIn("state.rules.rule0001", scoped["instructions"])

    def test_measured_parity_floor_is_shared_and_inclusive(self):
        import json
        fixture = json.loads(path.with_name("fixtures").joinpath("rule-batch-parity.v1.json").read_text())
        selection = delivery._sibling("jev_rule_select")
        floor = fixture["threshold_selection"]["floor"]
        self.assertEqual(selection.BATCH_BIND_AT, floor)
        rules = [{"id": "rule0001", "statement": "send"},
                 {"id": "rule0002", "statement": "review"}]
        def ask(state, questions, **kwargs):
            return {"model": fixture["model"], "answers": {
                "bind_rule0001": {"noul": floor},
                "bind_rule0002": {"noul": floor - .001}}}
        result, _ = delivery.judge_budgeted("send", rules, [], ask=ask,
                                           client=Client, deadline=10**12)
        self.assertEqual(set(result), {"rule0001"})

    def test_batch_failure_never_retries_serially(self):
        calls = []
        def ask(state, questions, **kwargs):
            calls.append(questions)
            raise RuntimeError("offline")
        selected, report = delivery.judge_budgeted(
            "send", [{"id": "rule0001", "statement": "send"}], [],
            ask=ask, client=Client, deadline=10**12)
        self.assertEqual(selected, {})
        self.assertEqual(report["bind_status"], "unavailable")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
