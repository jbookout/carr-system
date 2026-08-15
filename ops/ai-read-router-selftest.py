#!/usr/bin/env python3
"""Focused, offline tests for the synthetic read-only function router."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import ai_read_router


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "evals" / "ai" / "function-router.v1.json"
CANARY = "CARR-SECRET-CANARY-7F4A"


class ReadRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = ai_read_router.load_router_policy(FIXTURE_PATH, ROOT)
        cls.envelope_evidence = cls.policy["response_envelope_evidence"]["accepted_evidence"]

    def route(self, proposal, policy=None, envelope_evidence=None):
        return ai_read_router.route_read_only(
            proposal, self.envelope_evidence if envelope_evidence is None else envelope_evidence,
            policy or self.policy, ROOT,
        )

    def assert_refused(self, proposal, code, policy=None, envelope_evidence=None):
        result = self.route(proposal, policy, envelope_evidence)
        self.assertEqual(result, {"state": "refused", "violation_codes": [code]})
        self.assertNotIn(CANARY, json.dumps(result, sort_keys=True))

    def test_policy_binds_two_safe_server_derived_read_routes(self):
        self.assertEqual(
            [row["tool_name"] for row in self.policy["routes"]],
            ["find", "who-do-we-know"],
        )
        self.assertEqual(
            self.policy["tool_registry"]["path"], "mcp-server/src/tools.js"
        )
        self.assertEqual(
            self.policy["action_risk_registry"]["path"],
            "control-room/contracts/action-risk-registry.v1.json",
        )
        self.assertTrue(self.policy["server_context_digest"])
        for route in self.policy["routes"]:
            self.assertFalse(route["write"])
            self.assertFalse(route["full_only"])

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

    def test_accepts_normalized_find_with_server_owned_attribution(self):
        result = self.route({
            "schema_version": 1,
            "tool_name": "find",
            "arguments": {"query": "synthetic practice"},
        })
        self.assertEqual(result, {
            "state": "accepted",
            "route": {"tool_name": "find", "arguments": {"query": "synthetic practice"}},
            "attribution": self.policy["server_context"],
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
        self.assert_refused({
            "schema_version": 1, "tool_name": "not-a-real-tool", "arguments": {},
        }, "router_unknown_tool")
        self.assert_refused({
            "schema_version": 1, "tool_name": "add-loop", "arguments": {},
        }, "router_write_target_forbidden")
        self.assert_refused({
            "schema_version": 1, "tool_name": "read-work-shape", "arguments": {},
        }, "router_sensitive_target_forbidden")

    def test_refuses_extra_missing_and_wrong_typed_arguments_without_coercion(self):
        self.assert_refused({
            "schema_version": 1, "tool_name": "find", "arguments": {},
        }, "router_arguments_missing")
        self.assert_refused({
            "schema_version": 1, "tool_name": "find",
            "arguments": {"query": "synthetic", "limit": 1},
        }, "router_arguments_unknown")
        self.assert_refused({
            "schema_version": 1, "tool_name": "who-do-we-know",
            "arguments": {"target": "C-001", "max_depth": "2"},
        }, "router_arguments_type_invalid")

    def test_refuses_direct_target_and_authority_widening_fields_without_leaking_input(self):
        forbidden = [
            "target", "organization_tenant_id", "tenant_id", "identity", "actor",
            "runtime_principal", "sponsoring_human_id", "profile", "capability",
            "capabilities", "write", "action", "actions", "allowed_actions",
        ]
        for field in forbidden:
            with self.subTest(field=field):
                proposal = {
                    "schema_version": 1,
                    "tool_name": "find",
                    "arguments": {"query": "synthetic"},
                    field: CANARY,
                }
                self.assert_refused(proposal, "router_authority_field_forbidden")

    def test_refuses_unknown_outer_fields_and_bad_proposal_shape(self):
        self.assert_refused({
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"},
            "unexpected": CANARY,
        }, "router_proposal_unknown_fields")
        self.assert_refused([], "router_proposal_not_object")
        self.assert_refused({"schema_version": 1, "tool_name": "find"}, "router_proposal_missing_fields")

    def test_stale_policy_digest_refuses_before_any_route_can_be_selected(self):
        for binding in ("tool_registry", "action_risk_registry"):
            with self.subTest(binding=binding):
                stale = copy.deepcopy(self.policy)
                stale[binding]["sha256"] = "0" * 64
                self.assert_refused({
                    "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"},
                }, "router_policy_invalid", stale)

    def test_mutated_route_schema_cannot_weaken_required_arguments(self):
        forged = copy.deepcopy(self.policy)
        forged["routes"][0]["input_schema"]["required"] = []
        self.assert_refused({
            "schema_version": 1, "tool_name": "find", "arguments": {},
        }, "router_policy_invalid", forged)

    def test_mutated_server_context_cannot_forge_accepted_attribution(self):
        for field in ("runtime_principal", "sponsoring_human_id"):
            with self.subTest(field=field):
                forged = copy.deepcopy(self.policy)
                forged["server_context"][field] = CANARY
                self.assert_refused({
                    "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"},
                }, "router_policy_invalid", forged)

    def test_requires_separate_accepted_response_envelope_evidence(self):
        proposal = {
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"},
        }
        self.assertEqual(
            ai_read_router.route_read_only(proposal, None, self.policy, ROOT),
            {"state": "refused", "violation_codes": ["router_envelope_evidence_invalid"]},
        )
        for forged in (
            {**self.envelope_evidence, "attempts": 2},
            {**self.envelope_evidence, "state": "refused"},
            {**self.envelope_evidence, "validator_digest": "0" * 64},
        ):
            self.assert_refused(
                proposal, "router_envelope_evidence_invalid", envelope_evidence=forged
            )
        proposal_with_evidence = {**proposal, "envelope_evidence": self.envelope_evidence}
        self.assert_refused(proposal_with_evidence, "router_proposal_unknown_fields")

    def test_adapter_has_no_execution_or_mcp_import_path(self):
        source = (ROOT / "ops" / "ai_read_router.py").read_text()
        for forbidden in ("mcp-server", "subprocess", "requests", "sqlite3", "callback"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
