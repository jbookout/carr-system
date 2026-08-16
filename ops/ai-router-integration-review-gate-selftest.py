#!/usr/bin/env python3
"""Focused fixtures for the read-router integration review gate."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
import ai_router_integration_review_gate as gate


GATE = ROOT / "ops" / "ai_router_integration_review_gate.py"
CANARY = "CARR-SECRET-CANARY-7F4A"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IntegrationReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ai-router-review-gate-")
        self.repo = Path(self.temp.name)
        self.write("ops/ai_read_router.py", "POLICY_SHA256 = 'fixed'\n")
        self.write("evals/ai/function-router.v1.json", json.dumps({
            "routes": [{"tool_name": "find"}, {"tool_name": "who-do-we-know"}],
        }))
        self.write("evals/ai/model-boundary.v1.json", json.dumps({"suite_id": "synthetic"}))
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, text: str):
        target = self.repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def track(self):
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)

    def run_gate(self):
        self.track()
        return subprocess.run(
            [sys.executable, str(GATE), "--repo", str(self.repo)],
            capture_output=True, text=True,
        )

    def consumer(self, relative="mcp-server/src/router-consumer.mjs"):
        self.write(relative, "import { route_read_only } from 'ai_read_router';\nroute_read_only({}, {});\n")
        self.track()
        return next(row for row in gate.detect_consumers(self.repo) if row["path"] == relative)

    def valid_artifact(self, consumers):
        return {
            "schema_version": 1,
            "artifact_type": "read_router_integration_review",
            "data_class": "D2_internal_metadata_only",
            "execution": "offline_deterministic",
            "calls_models": False,
            "writes_records": False,
            "allowed_actions": [],
            "review_status": "reviewed_not_runtime_authorization",
            "reviewer_owner_id": "platform_reviewer",
            "reviewed_at": "2026-08-15T12:34:56Z",
            "review_reference": "WR-AI-003-review-001",
            "router_module": {"path": "ops/ai_read_router.py", "sha256": digest(self.repo / "ops/ai_read_router.py")},
            "policy": {"path": "evals/ai/function-router.v1.json", "sha256": digest(self.repo / "evals/ai/function-router.v1.json")},
            "envelope_suite": {"path": "evals/ai/model-boundary.v1.json", "sha256": digest(self.repo / "evals/ai/model-boundary.v1.json")},
            "reviews": [
                {
                    "consumer": {
                        "path": row["path"], "sha256": row["sha256"], "language": row["language"],
                        "trigger_anchors": row["trigger_anchors"],
                    },
                    "requested_routes": ["find"],
                    "execution_assurance": {
                        "execution_mode": "descriptor_only",
                        "calls_models": False,
                        "invokes_tools": False,
                        "writes_records": False,
                        "allowed_actions": [],
                        "server_owned_identity_tenant_sponsor_capability": True,
                        "caller_override_denied": True,
                        "route_action_risk_revalidated": True,
                        "passing_envelope_required": True,
                        "redacted_audit_observability": True,
                        "audit_event": "router_descriptor_audit",
                        "observability_signal": "router_descriptor_metrics",
                        "rollback_owner": "platform_owner",
                        "disable_path": "feature_flag_disable",
                        "safe_behavior": "refuse",
                    },
                }
                for row in consumers
            ],
        }

    def write_artifact(self, artifact):
        self.write(gate.REVIEW_ARTIFACT_REL, json.dumps(artifact, sort_keys=True))

    def assert_refused(self, artifact=None):
        if artifact is not None:
            self.write_artifact(artifact)
        result = self.run_gate()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(gate.REVIEW_ARTIFACT_REL, result.stderr)
        self.assertNotIn(CANARY, result.stdout + result.stderr)

    def test_real_tree_first_descriptor_consumer_is_reviewed_and_passes(self):
        result = subprocess.run([sys.executable, str(GATE), "--repo", str(ROOT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 reviewed consumer", result.stdout)
        self.assertTrue((ROOT / gate.REVIEW_ARTIFACT_REL).is_file())

    def test_orphan_review_artifact_is_refused_when_no_production_consumer_exists(self):
        self.write_artifact({})
        self.assert_refused()

    def test_test_eval_docs_fixtures_and_unrelated_code_are_not_consumers(self):
        anchor = "ai_read_router route_read_only function-router.v1.json POLICY_SHA256"
        self.write("README.md", anchor)
        self.write("fixtures/router_fixture.py", anchor)
        self.write("ops/example-selftest.py", anchor)
        self.write("mcp-server/test/router.test.mjs", anchor)
        self.write("evals/ai/router_eval.py", anchor)
        self.write("mcp-server/src/unrelated.mjs", "export const harmless = true;\n")
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_direct_import_call_and_distinctive_policy_anchors_require_the_future_artifact(self):
        anchors = {
            "tools/python_import.py": "from ai_read_router import route_read_only\n",
            "mcp-server/src/router_call.mjs": "route_read_only({}, {})\n",
            "bin/policy_path.sh": "policy=evals/ai/function-router.v1.json\n",
            "tools/module_path.py": "ROUTER_PATH = 'ops/ai_read_router.py'\n",
            "mcp-server/src/distinctive_copy.mjs": "const copy = 'synthetic_read_only_router_policy POLICY_SHA256';\n",
        }
        for relative, text in anchors.items():
            with self.subTest(relative=relative):
                self.write(relative, text)
                self.assert_refused()
                (self.repo / relative).unlink()

    def test_valid_exact_artifact_passes_and_deletion_is_refused(self):
        consumers = [self.consumer()]
        artifact = self.valid_artifact(consumers)
        self.write_artifact(artifact)
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.repo / gate.REVIEW_ARTIFACT_REL).unlink()
        self.assert_refused()

    def test_attestation_entrypoint_and_execution_assurance_are_exact(self):
        consumers = [self.consumer()]
        valid = self.valid_artifact(consumers)
        mutations = (
            ("owner_missing", lambda a: a.pop("reviewer_owner_id")),
            ("owner_malformed", lambda a: a.update(reviewer_owner_id="x")),
            ("reviewed_at_missing", lambda a: a.pop("reviewed_at")),
            ("reviewed_at_malformed", lambda a: a.update(reviewed_at="2026-08-15T12:34:56+00:00")),
            ("reference_missing", lambda a: a.pop("review_reference")),
            ("reference_malformed", lambda a: a.update(review_reference="x")),
            ("root_extra", lambda a: a.update(extra=CANARY)),
            ("anchor_line", lambda a: a["reviews"][0]["consumer"]["trigger_anchors"][0].update(line=99)),
            ("anchor_kind", lambda a: a["reviews"][0]["consumer"]["trigger_anchors"][0].update(kind="forged")),
            ("entrypoint_free_string", lambda a: a["reviews"][0]["consumer"].update(entrypoint="forged")),
            ("read_only_execution", lambda a: a["reviews"][0]["execution_assurance"].update(execution_mode="read_only_tool_execution")),
            ("writes", lambda a: a["reviews"][0]["execution_assurance"].update(writes_records=True)),
            ("actions", lambda a: a["reviews"][0]["execution_assurance"].update(allowed_actions=["find"])),
            ("authority", lambda a: a["reviews"][0]["execution_assurance"].update(caller_override_denied=False)),
            ("audit", lambda a: a["reviews"][0]["execution_assurance"].update(audit_event="x")),
            ("rollback", lambda a: a["reviews"][0]["execution_assurance"].update(disable_path="x")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                artifact = copy.deepcopy(valid)
                mutate(artifact)
                self.assert_refused(artifact)

    def test_stale_malformed_raw_payload_and_route_drift_are_refused(self):
        consumers = [self.consumer()]
        valid = self.valid_artifact(consumers)
        mutations = (
            ("stale_consumer", lambda a: a["reviews"][0]["consumer"].update(sha256="0" * 64)),
            ("stale_policy", lambda a: a["policy"].update(sha256="0" * 64)),
            ("stale_suite", lambda a: a["envelope_suite"].update(sha256="0" * 64)),
            ("unknown", lambda a: a.update(unexpected=CANARY)),
            ("raw_payload", lambda a: a["reviews"][0]["execution_assurance"].update(raw_args=CANARY)),
            ("canary", lambda a: a.update(review_reference=CANARY)),
            ("audit_bool", lambda a: a["reviews"][0]["execution_assurance"].update(redacted_audit_observability=False)),
            ("rollback_behavior", lambda a: a["reviews"][0]["execution_assurance"].update(safe_behavior="continue")),
            ("route_unknown", lambda a: a["reviews"][0].update(requested_routes=["write-tool"])),
            ("route_duplicate", lambda a: a["reviews"][0].update(requested_routes=["find", "find"])),
            ("route_missing", lambda a: a["reviews"][0].update(requested_routes=[])),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                artifact = copy.deepcopy(valid)
                mutate(artifact)
                self.assert_refused(artifact)

    def test_two_consumers_require_exact_one_review_per_current_consumer(self):
        first = self.consumer("mcp-server/src/one.mjs")
        second = self.consumer("tools/two.py")
        artifact = self.valid_artifact([first, second])
        self.write_artifact(artifact)
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        missing = copy.deepcopy(artifact)
        missing["reviews"].pop()
        self.assert_refused(missing)
        duplicate = copy.deepcopy(artifact)
        duplicate["reviews"].append(copy.deepcopy(duplicate["reviews"][0]))
        self.assert_refused(duplicate)


if __name__ == "__main__":
    unittest.main()
