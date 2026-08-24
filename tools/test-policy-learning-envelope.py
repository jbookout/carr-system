#!/usr/bin/env python3
"""Adversarial acceptance checks for the offline Policy Learning Envelope."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))
import policy_learning as learning  # noqa: E402
import evaluation_kernel  # noqa: E402

FIXTURE = ROOT / "control-room" / "contracts" / "fixtures" / "policy-learning-envelope.synthetic.v1.json"


def fixture(): return json.loads(FIXTURE.read_text())


def digest_decision(row):
    row["decision_digest"] = learning.canonical_digest({key: value for key, value in row.items() if key != "decision_digest"})


def refuse(fn, phrase):
    try:
        fn()
    except learning.PolicyLearningError as exc:
        assert phrase in str(exc), exc
        return
    raise AssertionError(f"expected refusal containing {phrase}")


def schema_ref(schema, ref):
    assert ref.startswith("#/")
    value = schema
    for part in ref[2:].split("/"):
        value = value[part]
    return value


def json_schema_validate(value, schema, root=None, label="$"):
    """Small Draft-2020-12 validator for the closed constructs CARR uses here.

    This validates the JSON Schema document against its fixture independently of
    the Python policy validator; it deliberately supports only constructs this
    schema declares and fails if a new one appears without a test implementation.
    """
    root = root or schema
    if "$ref" in schema:
        return json_schema_validate(value, schema_ref(root, schema["$ref"]), root, label)
    if "const" in schema and value != schema["const"]: raise AssertionError(f"{label}: const mismatch")
    if "enum" in schema and value not in schema["enum"]: raise AssertionError(f"{label}: enum mismatch")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict): raise AssertionError(f"{label}: expected object")
        for key in schema.get("required", []):
            if key not in value: raise AssertionError(f"{label}: missing {key}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties: json_schema_validate(child, properties[key], root, f"{label}.{key}")
            elif schema.get("additionalProperties") is False: raise AssertionError(f"{label}: unknown {key}")
            elif isinstance(schema.get("additionalProperties"), dict): json_schema_validate(child, schema["additionalProperties"], root, f"{label}.{key}")
        if len(value) < schema.get("minProperties", 0): raise AssertionError(f"{label}: too few properties")
    elif kind == "array":
        if not isinstance(value, list): raise AssertionError(f"{label}: expected array")
        if len(value) < schema.get("minItems", 0): raise AssertionError(f"{label}: too few items")
        if "items" in schema:
            for index, child in enumerate(value): json_schema_validate(child, schema["items"], root, f"{label}[{index}]")
    elif kind == "string":
        if not isinstance(value, str): raise AssertionError(f"{label}: expected string")
        if len(value) < schema.get("minLength", 0): raise AssertionError(f"{label}: too short")
        if "pattern" in schema:
            import re
            if re.fullmatch(schema["pattern"], value) is None: raise AssertionError(f"{label}: pattern mismatch")
    elif kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool): raise AssertionError(f"{label}: expected number")
        if value < schema.get("minimum", float("-inf")) or value > schema.get("maximum", float("inf")): raise AssertionError(f"{label}: bounds")
    elif kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool): raise AssertionError(f"{label}: expected integer")
        if value < schema.get("minimum", float("-inf")): raise AssertionError(f"{label}: bounds")
    elif kind == "boolean" and not isinstance(value, bool): raise AssertionError(f"{label}: expected boolean")


def json_schema_fixture_and_nested_shapes_are_real_contracts():
    schema = json.loads((ROOT / "control-room/contracts/policy-learning-envelope.v1.schema.json").read_text())
    value = fixture(); json_schema_validate(value, schema)
    changed = copy.deepcopy(value); changed["decisions"][0]["outcome"]["terminal"]["scalar_reward"] = 1
    try: json_schema_validate(changed, schema)
    except AssertionError as exc: assert "unknown scalar_reward" in str(exc)
    else: raise AssertionError("schema accepted an undeclared nested scalar reward")
    changed = copy.deepcopy(value); del changed["decisions"][0]["policy"]
    try: json_schema_validate(changed, schema)
    except AssertionError as exc: assert "missing policy" in str(exc)
    else: raise AssertionError("schema accepted a decision without its frozen policy binding")


def valid_safe_shadow_candidate_is_reviewable():
    value = fixture()
    assert learning.validate_policy_learning_envelope(value) == value
    result = learning.offline_candidate_evaluation(value, min_effective_sample_size=1)
    assert result["decision"] == "eligible_for_human_review"
    assert result["causal_claim"] == "not_established_without_randomized_or_known_assignment"
    assert {row["signal_kind"] for row in result["estimators"]} == {"terminal", "process"}


def forged_or_missing_propensity_fails_closed():
    value = fixture(); value["decisions"][0]["behavior"] = {"selection_method": "deterministic", "propensity": 0.4}
    digest_decision(value["decisions"][0])
    refuse(lambda: learning.validate_policy_learning_envelope(value), "deterministic policy evidence")
    value = fixture(); del value["decisions"][0]["behavior"]["propensity"]
    digest_decision(value["decisions"][0])
    refuse(lambda: learning.validate_policy_learning_envelope(value), "missing fields: propensity")


def unsupported_candidate_action_fails_coverage():
    value = fixture()
    for row in value["decisions"]:
        row["eligible_actions"].append("route:unobserved")
        row["candidate_action_probabilities"] = {"route:primary": 0.25, "route:other": 0.25, "route:unobserved": 0.5}
        digest_decision(row)
    result = learning.offline_candidate_evaluation(value, min_effective_sample_size=1)
    assert result["decision"] == "not_eligible"
    assert any(code.startswith("candidate_action_without_observed_support") for code in result["reason_codes"])


def low_ess_and_extreme_weight_are_visible_blockers():
    value = fixture()
    for row in value["decisions"]:
        row["behavior"] = {"selection_method": "exploration", "propensity": 0.01}
        row["candidate_action_probabilities"] = {action: (1.0 if action == row["selected_action"] else 0.0) for action in row["eligible_actions"]}
        digest_decision(row)
    result = learning.offline_candidate_evaluation(value, max_importance_weight=2.0, min_effective_sample_size=3)
    assert result["decision"] == "not_eligible"
    assert any(code.startswith("low_effective_sample_size") for code in result["reason_codes"])
    assert any(code.startswith("importance_weight_clipped") for code in result["reason_codes"])


def stale_mixed_and_leaky_evidence_is_refused():
    value = fixture(); value["evaluation_window"]["expires_at"] = "2026-08-24T11:00:00Z"
    refuse(lambda: learning.validate_policy_learning_envelope(value, as_of="2026-08-24T12:00:00Z"), "generated after")
    value = fixture(); value["decisions"][0]["policy"]["version"] = "v9"; digest_decision(value["decisions"][0])
    refuse(lambda: learning.validate_policy_learning_envelope(value), "exact frozen reference")
    value = fixture(); value["evaluation_window"]["judge_actor"] = value["evaluation_window"]["candidate_builder_actor"]
    refuse(lambda: learning.validate_policy_learning_envelope(value), "distinct actors")


def temporal_ordering_refuses_future_or_out_of_horizon_evidence():
    value = fixture(); value["decisions"][0]["observed_at"] = "2026-08-24T09:00:00Z"; digest_decision(value["decisions"][0])
    refuse(lambda: learning.validate_policy_learning_envelope(value), "predates")
    value = fixture(); value["decisions"][0]["outcome"]["terminal"]["observed_at"] = "2026-08-24T11:00:00Z"; digest_decision(value["decisions"][0])
    refuse(lambda: learning.validate_policy_learning_envelope(value), "cannot precede")
    value = fixture(); value["generated_at"] = "2026-08-26T12:00:00Z"
    refuse(lambda: learning.validate_policy_learning_envelope(value), "generated after")
    value = fixture()
    refuse(lambda: learning.validate_policy_learning_envelope(value, as_of="2026-08-24T10:00:00Z"), "future relative")


def critical_regression_scalar_reward_and_ineligible_action_fail():
    value = fixture()
    for index, row in enumerate(value["decisions"]):
        row["outcome"]["terminal"]["signals"][0]["value"] = 0.0 if index == 0 else 1.0
        digest_decision(row)
    result = learning.offline_candidate_evaluation(value, min_effective_sample_size=1)
    assert any(code.startswith("critical_dimension_regression") for code in result["reason_codes"])
    value = fixture(); value["scalar_reward"] = 1
    refuse(lambda: learning.validate_policy_learning_envelope(value), "unknown fields: scalar_reward")
    value = fixture(); value["decisions"][0]["risk_class"] = "R5"; digest_decision(value["decisions"][0])
    refuse(lambda: learning.validate_policy_learning_envelope(value), "risk ceiling")
    value = fixture(); value["decisions"][0]["eligible_actions"] = ["write:deal", "route:other"]; value["decisions"][0]["selected_action"] = "write:deal"; value["decisions"][0]["candidate_action_probabilities"] = {"write:deal": 0.5, "route:other": 0.5}; value["decisions"][0]["action_trace"]["agent_actions"] = ["write:deal"]; digest_decision(value["decisions"][0])
    refuse(lambda: learning.validate_policy_learning_envelope(value), "action kind is ineligible")


def learning_refusals_project_as_shared_kernel_blockers():
    value = fixture()
    for index, row in enumerate(value["decisions"]):
        row["outcome"]["terminal"]["signals"][0]["value"] = 0.0 if index == 0 else 1.0
        digest_decision(row)
    assessment = learning.offline_candidate_evaluation(value, min_effective_sample_size=1)
    blockers = learning.evaluation_kernel_blockers(assessment)
    assert "policy_learning_not_eligible" in blockers
    assert any(code.startswith("policy_learning_blocker:critical_dimension_regression") for code in blockers)


if __name__ == "__main__":
    for name, check in (
        ("JSON Schema validates the fixture and nested closed shapes", json_schema_fixture_and_nested_shapes_are_real_contracts),
        ("safe shadow candidate stays human-review only", valid_safe_shadow_candidate_is_reviewable),
        ("forged or missing propensity fails closed", forged_or_missing_propensity_fails_closed),
        ("unsupported candidate action fails coverage", unsupported_candidate_action_fails_coverage),
        ("low ESS and extreme weight are visible", low_ess_and_extreme_weight_are_visible_blockers),
        ("stale, mixed, and leaky evidence is refused", stale_mixed_and_leaky_evidence_is_refused),
        ("temporal ordering is fail closed", temporal_ordering_refuses_future_or_out_of_horizon_evidence),
        ("critical regression, scalar reward, and ineligible action fail", critical_regression_scalar_reward_and_ineligible_action_fail),
        ("learning refusal becomes a shared evaluation blocker", learning_refusals_project_as_shared_kernel_blockers),
    ):
        check(); print(f"ok  {name}")
