#!/usr/bin/env python3
"""Focused, offline tests for the bounded synthetic read workflow."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
import ai_bounded_read_loop as loop


POLICY_PATH = ROOT / "evals" / "ai" / "bounded-read-loop.v1.json"
OBSERVATIONS_PATH = ROOT / "evals" / "ai" / "bounded-read-loop-observations.v1.json"
ENVELOPE_PATH = ROOT / "evals" / "ai" / "response-envelope.v1.json"
GATE = ROOT / "ops" / "ai_router_integration_review_gate.py"
CANARY = "CARR-SECRET-CANARY-7F4A"


class BoundedReadLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(POLICY_PATH.read_text())
        cls.observation_artifact = json.loads(OBSERVATIONS_PATH.read_text())
        cls.envelope = json.loads(ENVELOPE_PATH.read_text())["reference_envelope"]
        cls.routes = [
            {"schema_version": 1, "tool_name": row["tool_name"], "arguments": row["arguments"]}
            for row in cls.policy["plan"]
        ]
        cls.observations = cls.observation_artifact["observations"]

    def run_loop(self, observations=None, envelope=None):
        selected_envelope = copy.deepcopy(self.envelope if envelope is None else envelope)
        if observations is None:
            return loop.run_bounded_read_loop(selected_envelope)
        return loop._evaluate_observations(copy.deepcopy(observations), selected_envelope)

    def assert_refused(self, result, code):
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["violation_codes"], [code])
        self.assertFalse(result["calls_models"])
        self.assertFalse(result["invokes_tools"])
        self.assertFalse(result["writes_records"])
        self.assertEqual(result["allowed_actions"], [])
        self.assertNotIn(CANARY, json.dumps(result, sort_keys=True))

    def test_fixed_policy_and_two_step_record_catch_up_complete_deterministically(self):
        self.assertEqual(hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest(), loop.POLICY_SHA256)
        self.assertEqual(hashlib.sha256(OBSERVATIONS_PATH.read_bytes()).hexdigest(), loop.OBSERVATIONS_SHA256)
        self.assertEqual(self.observation_artifact["policy_digest"], loop.POLICY_SHA256)
        self.assertEqual([row["tool_name"] for row in self.policy["plan"]], ["find", "catch-me-up"])
        first = self.run_loop()
        second = self.run_loop()
        self.assertEqual(first, second)
        self.assertEqual(first["state"], "completed")
        self.assertEqual(first["steps_completed"], 2)
        self.assertEqual(first["observed_elapsed_ms"], 20)
        self.assertEqual(first["observed_cost_usd"], 0.0)
        self.assertEqual(first["verification_state"], "pinned_synthetic_evidence")
        self.assertEqual(len(first["route_digests"]), 2)
        self.assertEqual(len(first["evidence_digests"]), 2)
        serialized = json.dumps(first, sort_keys=True)
        for prohibited in ("synthetic practice", "C-001", "event:SYN-001", "answer", "response"):
            self.assertNotIn(prohibited, serialized)

    def test_caps_are_fixed_exact_integers_and_enforced(self):
        self.assertEqual(self.policy["caps"], {
            "max_steps": 2,
            "max_attempts_per_step": 1,
            "max_failures": 1,
            "max_elapsed_ms": 100,
            "max_cost_usd": 0.0,
        })
        slow = copy.deepcopy(self.observations)
        slow[1]["observed_elapsed_ms"] = 91
        self.assert_refused(self.run_loop(slow), "loop_cap_exceeded")
        costly = copy.deepcopy(self.observations)
        costly[0]["observed_cost_usd"] = 0.01
        self.assert_refused(self.run_loop(costly), "loop_cap_exceeded")
        for bad in (True, 1.0, -1):
            malformed = copy.deepcopy(self.observations)
            malformed[0]["observed_elapsed_ms"] = bad
            self.assert_refused(self.run_loop(malformed), "loop_observation_invalid")

    def test_unknown_or_first_failure_stops_without_retry_or_payload_echo(self):
        failed = copy.deepcopy(self.observations)
        failed[0].update(state="failed", failure_code="source_unavailable", evidence_refs=[])
        failed[1]["evidence_refs"] = [CANARY]
        result = self.run_loop(failed)
        self.assert_refused(result, "loop_step_failed")
        self.assertEqual(result["steps_completed"], 0)
        unknown = copy.deepcopy(self.observations)
        unknown[0].update(state="failed", failure_code="unknown_failure", evidence_refs=[])
        self.assert_refused(self.run_loop(unknown), "loop_observation_invalid")

    def test_completion_requires_bound_external_evidence_for_every_step(self):
        missing = copy.deepcopy(self.observations)
        missing[1]["evidence_refs"] = []
        self.assert_refused(self.run_loop(missing), "loop_verification_missing")
        stale = copy.deepcopy(self.observations)
        stale[0]["route_digest"] = "0" * 64
        self.assert_refused(self.run_loop(stale), "loop_observation_invalid")
        duplicate = copy.deepcopy(self.observations)
        duplicate[1]["evidence_refs"] = ["event:SYN-001", "event:SYN-001"]
        self.assert_refused(self.run_loop(duplicate), "loop_observation_invalid")

    def test_route_or_response_envelope_refusal_closes_the_loop(self):
        bad_envelope = copy.deepcopy(self.envelope)
        bad_envelope["response"]["answer"] = CANARY
        self.assert_refused(self.run_loop(envelope=bad_envelope), "loop_route_refused")
        signature = inspect.signature(loop.run_bounded_read_loop)
        self.assertEqual(list(signature.parameters), ["response_envelope"])
        with self.assertRaises(TypeError):
            loop.run_bounded_read_loop(self.envelope, observations=self.observations)

    def test_observation_contract_rejects_shape_type_nonfinite_and_canary_attacks(self):
        attacks = []
        extra = copy.deepcopy(self.observations)
        extra[0]["raw_result"] = CANARY
        attacks.append(extra)
        wrong_type = copy.deepcopy(self.observations)
        wrong_type[0]["evidence_refs"] = CANARY
        attacks.append(wrong_type)
        nonfinite = copy.deepcopy(self.observations)
        nonfinite[0]["observed_cost_usd"] = float("nan")
        attacks.append(nonfinite)
        for attack in attacks:
            self.assert_refused(self.run_loop(attack), "loop_observation_invalid")

    def test_runtime_is_pure_and_the_real_tree_review_gate_passes(self):
        source = (ROOT / "ops" / "ai_bounded_read_loop.py").read_text()
        for forbidden in ("subprocess", "requests", "socket", "mcp-server", "callTool", "callback"):
            self.assertNotIn(forbidden, source)
        gate = subprocess.run(
            [sys.executable, str(GATE), "--repo", str(ROOT)],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
        self.assertIn("1 reviewed consumer", gate.stdout)


if __name__ == "__main__":
    unittest.main()
