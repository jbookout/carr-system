#!/usr/bin/env python3
"""Focused, offline tests for the synthetic read-only function router."""

import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import ai_read_router


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "evals" / "ai" / "function-router.v1.json"
ENVELOPE_FIXTURE_PATH = ROOT / "evals" / "ai" / "response-envelope.v1.json"
CANARY = "CARR-SECRET-CANARY-7F4A"
DEFAULT_ENVELOPE = object()


class ReadRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(FIXTURE_PATH.read_text())
        cls.valid_envelope = json.loads(ENVELOPE_FIXTURE_PATH.read_text())["reference_envelope"]
        cls.suite = ai_read_router._load_bound_suite()

    def route(self, proposal, envelope=DEFAULT_ENVELOPE):
        return ai_read_router.route_read_only(
            proposal, self.valid_envelope if envelope is DEFAULT_ENVELOPE else envelope
        )

    def assert_refused(self, proposal, code, envelope=DEFAULT_ENVELOPE):
        result = self.route(proposal, envelope)
        self.assertEqual(result, {"state": "refused", "violation_codes": [code]})
        self.assertNotIn(CANARY, json.dumps(result, sort_keys=True))

    def test_policy_is_fixed_and_binds_two_safe_server_derived_read_routes(self):
        self.assertEqual(hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(), ai_read_router.POLICY_SHA256)
        self.assertEqual([row["tool_name"] for row in self.policy["routes"]], ["find", "who-do-we-know"])
        self.assertEqual(self.policy["tool_registry"]["path"], "mcp-server/src/tools.js")
        self.assertEqual(
            self.policy["action_risk_registry"]["path"],
            "control-room/contracts/action-risk-registry.v1.json",
        )
        self.assertNotIn("server_context", self.policy)

    def test_selected_tool_evidence_exactly_matches_current_registry_in_test_only_node_check(self):
        probe = (
            "import { TOOLS } from './mcp-server/src/tools.js';"
            "const names = ['find', 'who-do-we-know'];"
            "process.stdout.write(JSON.stringify(names.map((tool_name) => ({"
            "tool_name,write:Boolean(TOOLS[tool_name].write),"
            "full_only:Boolean(TOOLS[tool_name].fullOnly),input_schema:TOOLS[tool_name].inputSchema}))));"
        )
        run = subprocess.run(
            ["node", "--input-type=module", "-e", probe], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )
        current = json.loads(run.stdout)
        embedded = [{
            key: row[key] for key in ("tool_name", "write", "full_only", "input_schema")
        } for row in self.policy["selected_tool_evidence"]]
        self.assertEqual(embedded, current)

    def test_accepts_normalized_find_with_code_owned_attribution_and_envelope_binding(self):
        proposal = {
            "schema_version": 1,
            "tool_name": "find",
            "arguments": {"query": "synthetic practice"},
        }
        result = self.route(proposal)
        self.assertEqual(result, {
            "state": "accepted",
            "route": {"tool_name": "find", "arguments": {"query": "synthetic practice"}},
            "attribution": ai_read_router.SERVER_CONTEXT,
            "envelope_binding": {
                "suite_digest": self.suite["_digest"],
                "case_id": "AI-GROUND-001",
                "envelope_digest": ai_read_router._canonical_digest(self.valid_envelope),
            },
            "calls_models": False,
            "writes_records": False,
            "allowed_actions": [],
        })

    def test_accepts_second_safe_read_route_with_typed_optional_arguments(self):
        result = self.route({
            "schema_version": 1,
            "tool_name": "who-do-we-know",
            "arguments": {"target": "C-001", "max_depth": 2, "limit": 3},
        })
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(result["route"]["tool_name"], "who-do-we-know")
        self.assertEqual(result["route"]["arguments"], {
            "target": "C-001", "max_depth": 2, "limit": 3,
        })

    def test_refuses_unknown_write_and_sensitive_targets_without_execution(self):
        self.assert_refused({"schema_version": 1, "tool_name": "not-a-real-tool", "arguments": {}}, "router_unknown_tool")
        self.assert_refused({"schema_version": 1, "tool_name": "add-loop", "arguments": {}}, "router_write_target_forbidden")
        self.assert_refused({"schema_version": 1, "tool_name": "read-work-shape", "arguments": {}}, "router_sensitive_target_forbidden")

    def test_refuses_extra_missing_and_wrong_typed_arguments_without_coercion(self):
        self.assert_refused({"schema_version": 1, "tool_name": "find", "arguments": {}}, "router_arguments_missing")
        self.assert_refused({
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic", "limit": 1},
        }, "router_arguments_unknown")
        self.assert_refused({
            "schema_version": 1, "tool_name": "who-do-we-know", "arguments": {"target": "C-001", "max_depth": "2"},
        }, "router_arguments_type_invalid")

    def test_refuses_direct_target_and_authority_widening_fields_without_leaking_input(self):
        forbidden = [
            "target", "organization_tenant_id", "tenant_id", "identity", "actor",
            "runtime_principal", "sponsoring_human_id", "profile", "capability",
            "capabilities", "write", "action", "actions", "allowed_actions",
        ]
        for field in forbidden:
            with self.subTest(field=field):
                self.assert_refused({
                    "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"}, field: CANARY,
                }, "router_authority_field_forbidden")

    def test_refuses_unknown_outer_fields_and_bad_proposal_shape(self):
        self.assert_refused({
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"}, "unexpected": CANARY,
        }, "router_proposal_unknown_fields")
        self.assert_refused([], "router_proposal_not_object")
        self.assert_refused({"schema_version": 1, "tool_name": "find"}, "router_proposal_missing_fields")

    def test_fresh_self_consistent_forged_policy_cannot_reach_public_api(self):
        signature = inspect.signature(ai_read_router.route_read_only)
        self.assertEqual(list(signature.parameters), ["proposal", "response_envelope"])
        forged = json.loads(FIXTURE_PATH.read_text())
        forged["routes"][0]["input_schema"]["required"] = []
        forged["selected_tool_evidence"][0]["input_schema"]["required"] = []
        forged["selected_tool_evidence"][0]["input_schema_digest"] = ai_read_router._canonical_digest(
            forged["selected_tool_evidence"][0]["input_schema"]
        )
        with self.assertRaises(TypeError):
            ai_read_router.route_read_only(
                {"schema_version": 1, "tool_name": "find", "arguments": {}}, self.valid_envelope,
                policy=forged,
            )
        self.assert_refused(
            {"schema_version": 1, "tool_name": "find", "arguments": {}}, "router_arguments_missing"
        )

    def test_response_envelope_must_be_actual_valid_and_semantically_passing(self):
        proposal = {"schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"}}
        self.assert_refused(proposal, "router_envelope_evidence_invalid", None)
        missing_field = json.loads(json.dumps(self.valid_envelope))
        del missing_field["response"]["metrics"]
        self.assert_refused(proposal, "router_envelope_evidence_invalid", missing_field)
        semantic_failure = json.loads(json.dumps(self.valid_envelope))
        semantic_failure["response"]["answer"] = CANARY
        self.assert_refused(proposal, "router_envelope_evidence_invalid", semantic_failure)
        refused = json.loads(json.dumps(self.valid_envelope))
        refused["response"]["status"] = "refused"
        self.assert_refused(proposal, "router_envelope_evidence_invalid", refused)

    def test_adapter_has_no_execution_or_mcp_import_path(self):
        source = (ROOT / "ops" / "ai_read_router.py").read_text()
        for forbidden in ("mcp-server", "subprocess", "requests", "sqlite3", "callback"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
