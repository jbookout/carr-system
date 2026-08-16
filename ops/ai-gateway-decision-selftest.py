#!/usr/bin/env python3
"""Focused offline tests for the D1 AI-gateway evidence decision."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
import ai_gateway_decision


FIXTURE_PATH = ROOT / "evals" / "ai" / "gateway-decision.v1.json"
CANARY = "CARR-SECRET-CANARY-7F4A"


class GatewayDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text())

    def valid(self):
        return copy.deepcopy(self.fixture)

    def assert_rejected(self, artifact):
        with self.assertRaises(ai_gateway_decision.GatewayDecisionError) as error:
            ai_gateway_decision.validate_gateway_decision(artifact)
        self.assertNotIn(CANARY, str(error.exception))

    def test_fixed_d1_evidence_decision_is_deterministic_redacted_and_declined(self):
        self.assertEqual(
            hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
            ai_gateway_decision.DECISION_FIXTURE_SHA256,
        )
        artifact = ai_gateway_decision.load_gateway_decision()
        first = ai_gateway_decision.decide_gateway()
        self.assertEqual(first, ai_gateway_decision.decide_gateway())
        self.assertEqual(first["state"], "declined")
        self.assertEqual(first["decision"], "declined")
        self.assertEqual(
            first["comparison"],
            {
                "status": "unavailable",
                "reason": "insufficient_pinned_provider_evidence",
                "provider_evidence_count": 1,
            },
        )
        self.assertEqual(
            first["runtime_absence"],
            {"gateway_runtime": False, "provider_transport": False, "fallback_runtime": False},
        )
        self.assertEqual(
            first["future_gate"],
            "new_pinned_provider_evidence_and_new_gateway_shape_decision_required",
        )
        self.assertEqual(first["calls_models"], False)
        self.assertEqual(first["writes_records"], False)
        self.assertEqual(first["allowed_actions"], [])
        self.assertEqual(first["provider_inventory"], [
            {
                "provider_id": "synthetic-provider-v1",
                "model_id": "synthetic-model-v1",
                "route_id": "synthetic-route-v1",
                "run_id": "synthetic-observed-run-001",
            }
        ])
        rendered = json.dumps({"artifact": artifact, "decision": first}, sort_keys=True)
        for forbidden in ("provider_output", "content", "answer", "reported_usage", CANARY):
            self.assertNotIn(forbidden, rendered)

    def test_exact_current_bindings_inventory_the_actual_single_provider_evidence(self):
        artifact = ai_gateway_decision.load_gateway_decision()
        self.assertEqual(
            [row["path"] for row in artifact["evidence_bindings"]],
            [
                "evals/ai/synthetic-observed-run.v1.json",
                "evals/ai/model-boundary.v1.json",
            ],
        )
        for binding in artifact["evidence_bindings"]:
            self.assertEqual(
                binding["sha256"],
                hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest(),
            )
        self.assertEqual(len(artifact["provider_evidence"]), 1)

    def test_gateway_decision_does_not_transitively_pin_router_or_evaluator_modules(self):
        artifact = ai_gateway_decision.load_gateway_decision()
        bound_paths = {row["path"] for row in artifact["evidence_bindings"]}
        self.assertTrue(bound_paths.issubset({
            "evals/ai/synthetic-observed-run.v1.json",
            "evals/ai/model-boundary.v1.json",
        }))
        self.assertNotIn("evals/ai/function-router.v1.json", bound_paths)
        self.assertNotIn("ops/ai_read_router.py", bound_paths)
        self.assertNotIn("ops/ai_eval.py", bound_paths)

    def test_refuses_unknown_missing_duplicate_and_fake_multi_provider_evidence(self):
        unknown = self.valid()
        unknown["unexpected"] = CANARY
        self.assert_rejected(unknown)

        missing = self.valid()
        del missing["comparison"]
        self.assert_rejected(missing)

        duplicate = self.valid()
        duplicate["provider_evidence"].append(copy.deepcopy(duplicate["provider_evidence"][0]))
        self.assert_rejected(duplicate)

        fake_multi = self.valid()
        fake_multi["provider_evidence"].append({
            "source_path": "evals/ai/synthetic-observed-run.v1.json",
            "source_digest": fake_multi["provider_evidence"][0]["source_digest"],
            "run_id": "other-run",
            "provider_id": "other-provider",
            "model_id": "other-model",
            "route_id": "other-route",
        })
        self.assert_rejected(fake_multi)

    def test_refuses_forged_comparison_need_adoption_metrics_and_stale_bindings(self):
        forged_comparison = self.valid()
        forged_comparison["comparison"]["status"] = "available"
        self.assert_rejected(forged_comparison)

        forged_need = self.valid()
        forged_need["comparison"]["quality"] = CANARY
        self.assert_rejected(forged_need)

        forged_outage = self.valid()
        forged_outage["comparison"]["outage"] = CANARY
        self.assert_rejected(forged_outage)

        adoption = self.valid()
        adoption["decision"] = "adopt"
        self.assert_rejected(adoption)

        nonfinite_metric = self.valid()
        nonfinite_metric["comparison"]["cost_usd"] = float("nan")
        self.assert_rejected(nonfinite_metric)

        stale = self.valid()
        stale["evidence_bindings"][0]["sha256"] = "0" * 64
        self.assert_rejected(stale)

    def test_refuses_boolean_and_float_integer_lookalikes(self):
        for field, mutate in (
            ("schema_version", lambda artifact, value: artifact.update(schema_version=value)),
            (
                "comparison.provider_evidence_count",
                lambda artifact, value: artifact["comparison"].update(provider_evidence_count=value),
            ),
        ):
            for value in (True, 1.0):
                with self.subTest(field=field, value=repr(value)):
                    malformed = self.valid()
                    mutate(malformed, value)
                    self.assert_rejected(malformed)

    def test_refuses_provider_claims_that_do_not_exactly_project_the_observed_run(self):
        for field, value in (
            ("provider_id", CANARY),
            ("model_id", "forged-model"),
            ("route_id", "forged-route"),
            ("run_id", "forged-run"),
            ("source_digest", "f" * 64),
        ):
            with self.subTest(field=field):
                forged = self.valid()
                forged["provider_evidence"][0][field] = value
                self.assert_rejected(forged)

    def test_runtime_and_fallback_are_evidence_only_and_module_has_no_execution_imports(self):
        source = (ROOT / "ops" / "ai_gateway_decision.py").read_text()
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertTrue(imports.issubset({"__future__", "hashlib", "json", "pathlib", "typing"}))
        for forbidden in ("requests", "urllib", "http", "socket", "subprocess", "sqlite", "mcp"):
            self.assertNotIn(forbidden, imports)


if __name__ == "__main__":
    unittest.main()
