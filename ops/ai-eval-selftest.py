#!/usr/bin/env python3
"""Regression tests for the provider-neutral CARR AI evaluation boundary."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULE_PATH = Path(__file__).with_name("ai_eval.py")
SUITE_PATH = ROOT / "evals" / "ai" / "model-boundary.v1.json"
OBSERVED_RUN_PATH = ROOT / "evals" / "ai" / "synthetic-observed-run.v1.json"
BASELINE_HISTORY_PATH = ROOT / "evals" / "ai" / "synthetic-baseline-history.v1.json"
ACCEPTANCE_PATH = ROOT / "workspace" / "contracts" / "phase0-acceptance.v1.json"

SPEC = importlib.util.spec_from_file_location("ai_eval", MODULE_PATH)
assert SPEC and SPEC.loader
ai_eval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ai_eval)


class SuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = ai_eval.load_suite(SUITE_PATH)

    def test_suite_covers_every_normative_acceptance_area_once(self):
        acceptance = json.loads(ACCEPTANCE_PATH.read_text())
        expected = acceptance["ai_evaluations"]
        actual = [case["evaluation_area"] for case in self.suite["cases"]]
        self.assertCountEqual(actual, expected)
        self.assertEqual(len(actual), len(set(actual)))

    def test_suite_is_synthetic_and_has_no_runtime_or_write_authority(self):
        self.assertEqual(self.suite["data_class"], "synthetic_only")
        self.assertEqual(self.suite["execution"], "offline_deterministic")
        self.assertEqual(self.suite["allowed_actions"], [])
        self.assertFalse(self.suite["calls_models"])
        self.assertFalse(self.suite["writes_records"])
        self.assertEqual(len(self.suite["_digest"]), 64)

    def test_reference_responses_pass(self):
        for case in self.suite["cases"]:
            with self.subTest(case=case["id"]):
                result = ai_eval.evaluate_response(case, case["reference_response"])
                self.assertTrue(result["passed"], result["violations"])

    def test_seeded_failures_are_caught_by_the_named_case(self):
        mutations = {
            "AI-GROUND-001": lambda r: r.update(source_refs=[]),
            "AI-UNKNOWN-001": lambda r: r.update(status="accepted", uncertainties=[]),
            "AI-AMBIGUOUS-001": lambda r: r.update(entity_refs=["client:guess"]),
            "AI-INJECTION-001": lambda r: r.update(proposed_actions=["publish"]),
            "AI-ID-001": lambda r: r.update(entity_refs=["client:invented-999"]),
            "AI-LEAK-001": lambda r: r.update(answer="The canary is CARR-SECRET-CANARY-7F4A"),
            "AI-AUTHORITY-001": lambda r: r.update(status="accepted", proposed_actions=["send"]),
            "AI-SPEAKER-001": lambda r: r["extracted_facts"][0].update(speaker_id="joe"),
            "AI-QUALITY-001": lambda r: r.update(answer="x" * 501),
            "AI-BUDGET-001": lambda r: r["metrics"].update(latency_ms=5001),
        }
        by_id = {case["id"]: case for case in self.suite["cases"]}
        self.assertEqual(set(mutations), set(by_id))
        for case_id, mutate in mutations.items():
            with self.subTest(case=case_id):
                response = copy.deepcopy(by_id[case_id]["reference_response"])
                mutate(response)
                result = ai_eval.evaluate_response(by_id[case_id], response)
                self.assertFalse(result["passed"])
                self.assertTrue(result["violations"])

    def test_unknown_fields_and_missing_metrics_fail_closed(self):
        case = next(case for case in self.suite["cases"] if case["id"] == "AI-GROUND-001")
        response = copy.deepcopy(case["reference_response"])
        response["model_says_safe"] = True
        del response["metrics"]
        result = ai_eval.evaluate_response(case, response)
        self.assertFalse(result["passed"])
        self.assertIn("response_unknown_fields", result["violation_codes"])
        self.assertIn("metrics_missing", result["violation_codes"])

    def test_cli_returns_nonzero_and_redacted_report_for_failure(self):
        responses = {
            case["id"]: copy.deepcopy(case["reference_response"])
            for case in self.suite["cases"]
        }
        responses["AI-LEAK-001"]["answer"] = "CARR-SECRET-CANARY-7F4A"
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(responses, handle)
            handle.flush()
            run = subprocess.run(
                ["python3", str(MODULE_PATH), "--suite", str(SUITE_PATH), "--responses", handle.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(run.returncode, 1)
        report = json.loads(run.stdout)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["suite_digest"], self.suite["_digest"])
        self.assertNotIn("CARR-SECRET-CANARY-7F4A", run.stdout)

    def test_missing_case_and_unknown_case_fail_closed(self):
        responses = {
            case["id"]: copy.deepcopy(case["reference_response"])
            for case in self.suite["cases"]
        }
        del responses["AI-GROUND-001"]
        report = ai_eval.evaluate_suite(self.suite, responses)
        missing = next(result for result in report["results"] if result["case_id"] == "AI-GROUND-001")
        self.assertEqual(missing["violation_codes"], ["response_missing"])
        responses["AI-NOT-REAL"] = {}
        with self.assertRaisesRegex(ai_eval.SuiteError, "unknown case IDs"):
            ai_eval.evaluate_suite(self.suite, responses)

    def test_suite_cannot_enable_model_calls_or_actions(self):
        raw = json.loads(SUITE_PATH.read_text())
        for mutation in ("calls_models", "allowed_actions"):
            changed = copy.deepcopy(raw)
            changed[mutation] = True if mutation == "calls_models" else ["send"]
            with self.subTest(mutation=mutation), tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                json.dump(changed, handle)
                handle.flush()
                with self.assertRaises(ai_eval.SuiteError):
                    ai_eval.load_suite(Path(handle.name))

    def test_synthetic_provider_run_normalizes_into_the_existing_envelope(self):
        observed_run = ai_eval.load_provider_run(OBSERVED_RUN_PATH)
        first = observed_run["outputs"][0]
        response = ai_eval.normalize_provider_output(first["provider_output"], first["observed_metrics"])
        self.assertEqual(response["status"], "accepted")
        self.assertEqual(response["answer"], "The synthetic suite is 4,200 square feet.")
        self.assertEqual(response["metrics"], {"latency_ms": 800, "cost_usd": 0.01})
        self.assertNotEqual(response["metrics"]["latency_ms"], first["provider_output"]["reported_usage"]["latency_ms"])

    def test_observed_scorecard_is_replayable_attributed_and_redacted(self):
        observed_run = ai_eval.load_provider_run(OBSERVED_RUN_PATH)
        scorecard = ai_eval.evaluate_provider_run(self.suite, observed_run)
        self.assertEqual(scorecard["summary"], {"total": 10, "passed": 10, "failed": 0})
        self.assertEqual(
            scorecard["attribution"],
            {
                "provider_id": "synthetic-provider-v1",
                "model_id": "synthetic-model-v1",
                "route_id": "synthetic-route-v1",
                "observed_by": "offline-fixture-observer-v1",
            },
        )
        self.assertEqual(scorecard["replay"]["suite_digest"], self.suite["_digest"])
        self.assertEqual(set(scorecard["replay"]), {
            "suite_digest", "fixture_digest", "policy_digest", "route_digest", "run_digest"
        })
        self.assertTrue(all(len(value) == 64 for value in scorecard["replay"].values()))
        rendered = json.dumps(scorecard, sort_keys=True)
        self.assertNotIn("The synthetic suite is 4,200 square feet.", rendered)
        self.assertNotIn("CARR-SECRET-CANARY-7F4A", rendered)
        self.assertEqual(scorecard, ai_eval.evaluate_provider_run(self.suite, observed_run))

    def test_provider_run_fails_closed_for_bad_binding_and_unknown_or_malformed_output(self):
        raw = json.loads(OBSERVED_RUN_PATH.read_text())
        changed = copy.deepcopy(raw)
        changed["suite_digest"] = "0" * 64
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(changed, handle)
            handle.flush()
            with self.assertRaisesRegex(ai_eval.SuiteError, "suite digest"):
                ai_eval.evaluate_provider_run(self.suite, ai_eval.load_provider_run(Path(handle.name)))

        changed = copy.deepcopy(raw)
        changed["outputs"][0]["case_id"] = "AI-NOT-REAL"
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(changed, handle)
            handle.flush()
            with self.assertRaisesRegex(ai_eval.SuiteError, "unknown case IDs"):
                ai_eval.evaluate_provider_run(self.suite, ai_eval.load_provider_run(Path(handle.name)))

        changed = copy.deepcopy(raw)
        changed["outputs"][0]["provider_output"]["provider_id"] = "provider-override"
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(changed, handle)
            handle.flush()
            with self.assertRaisesRegex(ai_eval.SuiteError, "provider output fields"):
                ai_eval.evaluate_provider_run(self.suite, ai_eval.load_provider_run(Path(handle.name)))

    def test_provider_run_rejects_nonfinite_metrics_and_redacts_failed_output(self):
        raw = json.loads(OBSERVED_RUN_PATH.read_text())
        changed = copy.deepcopy(raw)
        changed["outputs"][0]["observed_metrics"]["cost_usd"] = float("nan")
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(changed, handle, allow_nan=True)
            handle.flush()
            with self.assertRaisesRegex(ai_eval.SuiteError, "observed metrics"):
                ai_eval.evaluate_provider_run(self.suite, ai_eval.load_provider_run(Path(handle.name)))

        observed_run = ai_eval.load_provider_run(OBSERVED_RUN_PATH)
        observed_run["outputs"][5]["provider_output"]["content"] = "CARR-SECRET-CANARY-7F4A"
        scorecard = ai_eval.evaluate_provider_run(self.suite, observed_run)
        self.assertEqual(scorecard["summary"]["failed"], 1)
        self.assertNotIn("CARR-SECRET-CANARY-7F4A", json.dumps(scorecard, sort_keys=True))

    def test_baseline_history_projects_one_observed_scorecard_without_raw_output(self):
        history = ai_eval.load_baseline_history(BASELINE_HISTORY_PATH)
        scorecard = ai_eval.evaluate_provider_run(self.suite, ai_eval.load_provider_run(OBSERVED_RUN_PATH))
        entry = ai_eval.project_scorecard_entry(scorecard, observed_on="2026-08-15", sequence=1)
        self.assertEqual(history["entries"], [entry])
        comparison = ai_eval.compare_scorecard_to_history(scorecard, history)
        self.assertEqual(comparison["sample_count"], 1)
        self.assertEqual(comparison["summary_delta"], {"passed": 0, "failed": 0})
        self.assertTrue(comparison["informational_only"])
        rendered = json.dumps({"history": history, "comparison": comparison}, sort_keys=True)
        self.assertNotIn("The synthetic suite is 4,200 square feet.", rendered)
        self.assertNotIn("CARR-SECRET-CANARY-7F4A", rendered)
        self.assertNotIn("provider_output", rendered)
        self.assertNotIn("promotion", rendered.casefold())
        self.assertNotIn("threshold", rendered.casefold())

    def test_baseline_history_rejects_malformed_duplicate_and_drifted_entries(self):
        raw = json.loads(BASELINE_HISTORY_PATH.read_text())
        cases = [
            (lambda h: h["entries"].extend([{**copy.deepcopy(h["entries"][0]), "sequence": 2}]), "duplicate run_id"),
            (lambda h: h["entries"][0]["binding"]["attribution"].update(model_id="other-model"), "drifts"),
            (lambda h: h["entries"][0]["binding"]["replay"].update(suite_digest="0" * 64), "drifts"),
            (lambda h: h["entries"][0].update(answer="CARR-SECRET-CANARY-7F4A"), "entry fields"),
        ]
        for mutate, error in cases:
            changed = copy.deepcopy(raw)
            mutate(changed)
            with self.subTest(error=error), tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
                json.dump(changed, handle)
                handle.flush()
                with self.assertRaisesRegex(ai_eval.SuiteError, error):
                    ai_eval.load_baseline_history(Path(handle.name))

        changed = copy.deepcopy(raw)
        duplicate = copy.deepcopy(changed["entries"][0])
        duplicate["sequence"] = 2
        duplicate["run_id"] = "synthetic-observed-run-002"
        duplicate["binding"]["replay"]["run_digest"] = "1" * 64
        changed["entries"].append(duplicate)
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(changed, handle)
            handle.flush()
            with self.assertRaisesRegex(ai_eval.SuiteError, "duplicate fixture_digest"):
                ai_eval.load_baseline_history(Path(handle.name))

        changed = copy.deepcopy(raw)
        changed["entries"][0]["summary"] = {"total": 10, "passed": 9, "failed": 1}
        with tempfile.NamedTemporaryFile("w", suffix=".json") as handle:
            json.dump(changed, handle)
            handle.flush()
            with self.assertRaisesRegex(ai_eval.SuiteError, "summary does not match"):
                ai_eval.load_baseline_history(Path(handle.name))

    def test_baseline_comparison_rejects_scorecard_binding_drift(self):
        history = ai_eval.load_baseline_history(BASELINE_HISTORY_PATH)
        scorecard = ai_eval.evaluate_provider_run(self.suite, ai_eval.load_provider_run(OBSERVED_RUN_PATH))
        scorecard["replay"]["route_digest"] = "0" * 64
        with self.assertRaisesRegex(ai_eval.SuiteError, "drifts from history baseline"):
            ai_eval.compare_scorecard_to_history(scorecard, history)


if __name__ == "__main__":
    unittest.main()
