#!/usr/bin/env python3
"""Focused deterministic tests for the D2 action-risk disposition contract."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "ops" / "action-risk-guardrails.py"
SPEC = importlib.util.spec_from_file_location("action_risk_guardrails", MODULE_PATH)
assert SPEC and SPEC.loader
action_risk_guardrails = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action_risk_guardrails)

ARTIFACT_PATH = ROOT / "control-room" / "contracts" / "action-risk-dispositions.v1.json"
REGISTRY_PATH = ROOT / "control-room" / "contracts" / "action-risk-registry.v1.json"
CANARY = "CARR-SECRET-CANARY-7F4A"


class ActionRiskDispositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifact = json.loads(ARTIFACT_PATH.read_text())
        cls.registry = json.loads(REGISTRY_PATH.read_text())

    def validate(self, artifact=None, registry=None, registry_bytes=None):
        return action_risk_guardrails.validate_dispositions(
            self.artifact if artifact is None else artifact,
            self.registry if registry is None else registry,
            REGISTRY_PATH.read_bytes() if registry_bytes is None else registry_bytes,
        )

    def assert_invalid(self, artifact, expected):
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, expected):
            self.validate(artifact)

    def test_baseline_is_exactly_covered_and_set_lead_is_the_only_selected_remediation(self):
        result = self.validate()
        self.assertEqual(result, {"status": "valid", "selected_slice": "set-lead", "rows": 13})
        rows = self.artifact["dispositions"]
        self.assertEqual({row["tool_name"] for row in rows}, action_risk_guardrails.BASELINE_NONE_ROWS)
        self.assertEqual([row["tool_name"] for row in rows if row["selected_slice"]], ["set-lead"])
        self.assertNotIn(CANARY, json.dumps(self.artifact, sort_keys=True))

    def test_registry_digest_is_bound_and_stale_bytes_refuse(self):
        stale_binding = copy.deepcopy(self.artifact)
        stale_binding["action_risk_registry"]["sha256"] = "0" * 64
        self.assert_invalid(stale_binding, "registry digest")
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, "registry digest"):
            self.validate(registry_bytes=b"stale")

    def test_missing_unknown_duplicate_and_unowned_baseline_rows_refuse(self):
        missing = copy.deepcopy(self.artifact)
        missing["dispositions"].pop()
        self.assert_invalid(missing, "baseline coverage")

        unknown = copy.deepcopy(self.artifact)
        unknown["dispositions"][-1]["tool_name"] = "unknown-tool"
        self.assert_invalid(unknown, "baseline coverage")

        duplicate = copy.deepcopy(self.artifact)
        duplicate["dispositions"][-1]["tool_name"] = duplicate["dispositions"][0]["tool_name"]
        self.assert_invalid(duplicate, "duplicates")

        unowned = copy.deepcopy(self.artifact)
        unowned["dispositions"][0]["owner"] = ""
        self.assert_invalid(unowned, "owner")

    def test_selected_row_can_leave_none_only_when_runtime_protection_and_evidence_match(self):
        current_none = copy.deepcopy(self.registry)
        current_none["verbs"]["set-lead"]["protection"] = "NONE"
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, "selected runtime protection"):
            self.validate(registry=current_none)

        missing_base_version = copy.deepcopy(self.registry)
        missing_base_version["verbs"]["set-lead"]["base_version_required"] = False
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, "selected base_version"):
            self.validate(registry=missing_base_version)

        bad_evidence = copy.deepcopy(self.artifact)
        selected = next(row for row in bad_evidence["dispositions"] if row["tool_name"] == "set-lead")
        selected["evidence"] = "unverified"
        self.assert_invalid(bad_evidence, "selected evidence")


if __name__ == "__main__":
    unittest.main()
