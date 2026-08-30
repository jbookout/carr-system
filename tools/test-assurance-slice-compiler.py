"""Focused invariants for the pure Assurance Slice Compiler v1."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "tools/room-bridge"
sys.path.insert(0, str(BRIDGE))
import assurance_slice_compiler as compiler  # noqa: E402
import execution_contract  # noqa: E402

FIXTURE = ROOT / "control-room/contracts/fixtures/execution-fabric/assurance-compiler.valid.v1.json"


def schema_valid(schema: dict, entry: str, value: object) -> bool:
    source = """
import fs from "node:fs";
import {compileSchema} from "./workspace/contracts/schema-validator.mjs";
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(compileSchema(payload.schema, payload.schema.$defs[payload.entry])(payload.value)));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source], cwd=ROOT,
        input=json.dumps({"schema": schema, "entry": entry, "value": value}),
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)["valid"] is True


def _digest_without(value: dict, field: str) -> str:
    return execution_contract.canonical_digest({key: item for key, item in value.items() if key != field})


def seal_plan(value: dict) -> None:
    value["engineering_slice_plan"]["plan_digest"] = _digest_without(value["engineering_slice_plan"], "plan_digest")


def seal_rules(value: dict, *, update_binding: bool = True) -> None:
    rules = value["applicable_rules"]
    rules["rules"] = compiler._sort_set(rules["rules"])
    rules["snapshot_digest"] = execution_contract.canonical_digest({"snapshot_ref": rules["snapshot_ref"], "rules": rules["rules"]})
    if update_binding:
        value["assurance_slice"]["rule_snapshot_binding"] = {"snapshot_ref": rules["snapshot_ref"], "snapshot_digest": rules["snapshot_digest"]}


def seal_coordination(value: dict) -> None:
    coord = value["coordination_snapshot"]
    coord["leases"] = compiler._sort_set(coord["leases"])
    for lease in coord["leases"]:
        lease["claims"] = compiler._sort_set(lease["claims"])
    coord["dependencies"] = compiler._sort_set(coord["dependencies"])
    coord["snapshot_digest"] = _digest_without(coord, "snapshot_digest")


def seal_contract(value: dict) -> None:
    contract = compiler._normalized_contract(value["assurance_slice"])
    contract["contract_digest"] = _digest_without(contract, "contract_digest")
    value["assurance_slice"] = contract


def seal(value: dict) -> dict:
    seal_plan(value)
    value["assurance_slice"]["engineering_slice_plan_digest"] = value["engineering_slice_plan"]["plan_digest"]
    seal_rules(value)
    seal_coordination(value)
    seal_contract(value)
    return value


def valid_input() -> dict:
    return json.loads(FIXTURE.read_text())


def refusal(value: dict, code: str, causal_object: str) -> dict:
    result = compiler.compile_assurance_slice(value)
    assert result["ok"] is False, result
    assert result["refusal"]["code"] == code, result
    assert result["refusal"]["causal_object"] == causal_object, result
    assert "expected" in result["refusal"] and "actual" in result["refusal"]
    return result["refusal"]


def test_valid_compilation_is_deterministic_and_manifest_is_immutable_posture():
    value = valid_input()
    first = compiler.compile_assurance_slice(value)
    second = compiler.compile_assurance_slice(copy.deepcopy(value))
    assert first == second and first["ok"] is True
    manifest = first["manifest"]
    assert manifest["authority_state"] == "compiled_not_authorized"
    assert manifest["verification_state"] == "unverified"
    assert manifest["self_certification"] is False
    assert manifest["currentness"]["authorizes_action"] is False
    assert manifest["currentness"]["currentness_state"] == "declared_window_consistent_not_live_verified"
    assert manifest["currentness"]["live_currentness_verified"] is False
    assert manifest["currentness"]["declared_evaluation_time"] == value["declared_evaluation_time"]
    assert manifest["currentness"]["snapshot_as_of"] == value["coordination_snapshot"]["as_of"]
    assert manifest["currentness"]["snapshot_valid_until"] == value["coordination_snapshot"]["valid_until"]
    assert manifest["currentness"]["lease_expires_at"] == value["coordination_snapshot"]["leases"][0]["expires_at"]
    assert manifest["currentness"]["recompile_against_resulting_commit_tree_before"] == ["commit", "push", "pr_update", "review", "merge", "runtime_action"]
    assert manifest["currentness"]["usable_only_as_preflight_for"] == []
    assert manifest["currentness"]["requires_live_currentness_check_before"][:2] == ["write", "test"]
    assert manifest["slice"]["required_tests"][0]["check_profile_ref"] == "check-profile:assurance-compiler-v1"
    assert manifest["slice"]["required_tests"][0]["runner"] == "python_pytest"
    assert manifest["slice"]["required_tests"][0]["argv"] == ["python3", "-m", "pytest", "-q", "tools/test-assurance-slice-compiler.py"]
    assert manifest["slice"]["required_tests"][0]["cwd"] == "."
    required = manifest["slice"]["required_tests"][0]
    assert required["environment_gate"] == compiler._CHECK_PROFILES["check:compiler"]["environment_gate"]
    assert required["environment_gate"]["must_pass_before_test"] is True
    assert required["environment_gate"]["argv"][0] == required["environment"]["runtime"] == required["argv"][0]
    for binding in (required["test_artifact"], required["environment"]["version_source"], required["environment"]["dependency_lock"]):
        actual = "sha256:" + hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest()
        assert binding["digest"] == actual
    assert manifest["manifest_hash"] == execution_contract.canonical_digest({k: v for k, v in manifest.items() if k != "manifest_hash"})


def test_declared_set_reordering_preserves_manifest_hash():
    left = valid_input(); right = copy.deepcopy(left)
    right["assurance_slice"]["path_claims"].reverse()
    right["assurance_slice"]["required_tests"][0]["evidence_fields"].reverse()
    right["applicable_rules"]["rules"].reverse()
    right["coordination_snapshot"]["leases"][0]["claims"].reverse()
    assert compiler.compile_assurance_slice(left)["manifest"]["manifest_hash"] == compiler.compile_assurance_slice(right)["manifest"]["manifest_hash"]


def test_unknown_missing_and_absent_extension_refuse_exactly():
    value = valid_input(); value["surprise"] = True
    refusal(value, "INPUT_UNKNOWN_FIELD", "compiler_input")
    value = valid_input(); del value["repository"]
    refusal(value, "INPUT_MISSING_FIELD", "compiler_input")
    value = valid_input(); value["assurance_slice"] = None
    refusal(value, "ASSURANCE_SLICE_ABSENT", "assurance_slice")


def test_work_request_plan_and_slice_binding_mismatches_are_distinct():
    value = valid_input(); value["work_request"]["state_version"] += 1
    refusal(value, "WORK_REQUEST_BINDING_MISMATCH", "work_request")
    value = valid_input(); value["accepted_plan_revision"]["revision"] += 1
    refusal(value, "ACCEPTED_PLAN_BINDING_MISMATCH", "accepted_plan_revision")
    value = valid_input(); value["assurance_slice"]["engineering_slice_plan_digest"] = "sha256:" + "9" * 64; seal_contract(value)
    refusal(value, "ENGINEERING_SLICE_PLAN_BINDING_MISMATCH", "assurance_slice.engineering_slice_plan_digest")
    value = valid_input(); value["assurance_slice"]["slice_ref"] = "slice:missing"; seal_contract(value)
    refusal(value, "SLICE_BINDING_MISMATCH", "assurance_slice.slice_ref")


@pytest.mark.parametrize("bad", ["/absolute.py", "./dot.py", "a/../b.py", "a\\b.py", "a/*.py", "unicodé.py"])
def test_invalid_path_forms_refuse_with_the_exact_path_object(bad):
    value = valid_input(); value["assurance_slice"]["path_claims"][0]["path"] = bad
    refusal(value, "PATH_INVALID", "assurance_slice.path_claims[0].path")


def test_case_alias_and_allowed_forbidden_component_ancestry_refuse():
    value = valid_input(); value["assurance_slice"]["path_claims"][0]["path"] = "tools/space path.py"
    refusal(value, "PATH_INVALID", "assurance_slice.path_claims[0].path")
    value = valid_input(); value["assurance_slice"]["forbidden_paths"].append({"path":"Tools/room-bridge/other.py","mode":"file"})
    refusal(value, "PATH_CASE_ALIAS", "assurance_slice.path_claims")
    value = valid_input(); value["assurance_slice"]["forbidden_paths"] = [{"path":"tools","mode":"tree"}]
    refusal(value, "PATH_SCOPE_COLLISION", "tools/room-bridge/assurance_slice_compiler.py")


def test_dependency_missing_and_unsatisfied_have_causal_slice_ref():
    value = valid_input(); value["coordination_snapshot"]["dependencies"] = []; seal_coordination(value)
    refusal(value, "DEPENDENCY_MISSING", "slice:contracts")
    value = valid_input(); dep = value["coordination_snapshot"]["dependencies"][0]; dep["state"] = "pending"; dep["evidence_digest"] = None; seal_coordination(value)
    refusal(value, "DEPENDENCY_UNSATISFIED", "slice:contracts")


@pytest.mark.parametrize("operation", ["write", "rename_source", "rename_destination"])
def test_active_foreign_file_or_tree_lease_collision_includes_rename_claims(operation):
    value = valid_input()
    value["coordination_snapshot"]["leases"].append({"lease_id":"lease:foreign","state":"active","holder_session_id":"session:foreign","holder_host_id":"host:other","expires_at":"2026-08-30T15:30:00Z","fencing_generation":2,"claims":[{"path":"tools/room-bridge","mode":"tree","operation":operation}]})
    seal_coordination(value)
    refusal(value, "FOREIGN_LEASE_COLLISION", "tools/room-bridge/assurance_slice_compiler.py")
    value = valid_input()
    value["coordination_snapshot"]["leases"].append({"lease_id":"lease:case-alias","state":"active","holder_session_id":"session:foreign","holder_host_id":"host:other","expires_at":"2026-08-30T15:30:00Z","fencing_generation":2,"claims":[{"path":"Tools/room-bridge/assurance_slice_compiler.py","mode":"file","operation":operation}]})
    seal_coordination(value)
    refusal(value, "FOREIGN_LEASE_COLLISION", "tools/room-bridge/assurance_slice_compiler.py")


def test_stale_snapshot_expired_lease_and_released_lease_are_not_equivalent():
    value = valid_input(); value["coordination_snapshot"]["valid_until"] = value["coordination_snapshot"]["as_of"]
    refusal(value, "COORDINATION_SNAPSHOT_STALE", "coordination_snapshot.valid_until")
    value = valid_input(); value["declared_evaluation_time"] = "2026-08-30T21:37:24Z"
    refusal(value, "COORDINATION_SNAPSHOT_STALE", "coordination_snapshot.valid_until")
    for field, bad in (("as_of", "2026-13-01T15:00:00Z"), ("valid_until", "2026-02-30T16:00:00Z")):
        value = valid_input(); value["coordination_snapshot"][field] = bad
        refusal(value, "FIELD_INVALID", f"coordination_snapshot.{field}")
    value = valid_input(); value["coordination_snapshot"]["leases"][0]["expires_at"] = "2026-02-30T16:00:00Z"; seal_coordination(value)
    refusal(value, "FIELD_INVALID", "coordination_snapshot.leases[0].expires_at")
    value = valid_input()
    value["declared_evaluation_time"] = "2000-01-01T00:00:01Z"
    value["coordination_snapshot"]["as_of"] = "2000-01-01T00:00:00Z"
    value["coordination_snapshot"]["valid_until"] = "2000-01-01T01:00:00Z"
    value["coordination_snapshot"]["leases"][0]["expires_at"] = "2000-01-01T01:00:00Z"
    seal_coordination(value)
    result = compiler.compile_assurance_slice(value)
    assert result["ok"] is True
    assert result["manifest"]["currentness"]["currentness_state"] == "declared_window_consistent_not_live_verified"
    assert result["manifest"]["currentness"]["usable_only_as_preflight_for"] == []
    value = valid_input(); value["coordination_snapshot"]["leases"][0]["expires_at"] = "2026-08-30T14:59:59Z"; seal_coordination(value)
    refusal(value, "LEASE_EXPIRED", "lease:lease:a1a")
    value = valid_input(); value["coordination_snapshot"]["leases"][0]["state"] = "released"; seal_coordination(value)
    refusal(value, "LEASE_RELEASED", "lease:lease:a1a")


def test_stale_rule_digest_and_repository_identity_mismatch_refuse():
    value = valid_input(); value["applicable_rules"]["rules"][0]["revision"] += 1; seal_rules(value, update_binding=False)
    refusal(value, "RULE_SNAPSHOT_STALE", "applicable_rules")
    value = valid_input(); value["repository"]["tree_sha"] = "9" * 40
    refusal(value, "REPOSITORY_IDENTITY_MISMATCH", "repository")


def test_owner_acceptance_cannot_substitute_for_independent_review():
    value = valid_input(); value["assurance_slice"]["reviewer_policy"]["owner_acceptance_is_review"] = True
    refusal(value, "REVIEWER_POLICY_INVALID", "assurance_slice.reviewer_policy")


def test_required_commands_refine_exact_planned_checks_and_reviewer_names_executor():
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["check_ref"] = "check:unplanned"; seal_contract(value)
    refusal(value, "SLICE_BINDING_MISMATCH", "assurance_slice.required_tests")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["planned_check_digest"] = "sha256:" + "9" * 64; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["argv"] = ["true", "tools/test-assurance-slice-compiler.py"]; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); required = value["assurance_slice"]["required_tests"][0]; required["test_artifact"] = {"path":"tools/room-bridge/assurance_slice_compiler.py","digest":"sha256:" + "8" * 64}; required["argv"][-1] = required["test_artifact"]["path"]; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); required = value["assurance_slice"]["required_tests"][0]; required["environment"]["runtime"] = "true"; required["argv"][0] = "true"; required["environment_gate"]["argv"][0] = "true"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); required = value["assurance_slice"]["required_tests"][0]; required["test_artifact"] = {"path":"tools/room-bridge/test_engineering_passport_unit.py","digest":"sha256:" + "7" * 64}; required["argv"][-1] = required["test_artifact"]["path"]; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["check_profile_ref"] = "check-profile:foreign"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["environment_gate"]["must_pass_before_test"] = False
    refusal(value, "FIELD_INVALID", "assurance_slice.required_tests[0].environment_gate.must_pass_before_test")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["causal_failure"]["expected"] = "anything exits nonzero"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["causal_failure"]["code"] = "generic_nonzero"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); value["assurance_slice"]["required_tests"][0]["causal_failure"]["object"] = "anything"; seal_contract(value)
    refusal(value, "REQUIRED_TEST_BINDING_MISMATCH", "check:compiler")
    value = valid_input(); artifact = value["assurance_slice"]["required_tests"][0]["test_artifact"]["path"]
    value["assurance_slice"]["path_claims"] = [claim for claim in value["assurance_slice"]["path_claims"] if claim["path"] != artifact]
    value["coordination_snapshot"]["leases"][0]["claims"] = [claim for claim in value["coordination_snapshot"]["leases"][0]["claims"] if claim["path"] != artifact]
    seal_coordination(value); seal_contract(value)
    assert compiler.compile_assurance_slice(value)["ok"] is True
    value = valid_input(); value["assurance_slice"]["reviewer_policy"]["executor_actor_ref"] = "actor:someone-else"; seal_contract(value)
    refusal(value, "REVIEWER_POLICY_INVALID", "assurance_slice.reviewer_policy")


def test_lease_holder_is_the_bound_executor_session_and_host():
    value = valid_input(); value["assurance_slice"]["executor_identity"]["host_ref"] = "host:other"; seal_contract(value)
    refusal(value, "LEASE_BINDING_MISMATCH", "assurance_slice.lease_binding")
    value = valid_input(); value["coordination_snapshot"]["requesting_session_id"] = "session:foreign"; seal_coordination(value)
    fact = refusal(value, "REQUESTER_IDENTITY_MISMATCH", "coordination_snapshot.requesting_session_id")
    assert fact["expected"] == "session:a1a" and fact["actual"] == "session:foreign"
    value = valid_input(); value["coordination_snapshot"]["requesting_host_id"] = "host:other"; seal_coordination(value)
    fact = refusal(value, "REQUESTER_IDENTITY_MISMATCH", "coordination_snapshot.requesting_host_id")
    assert fact["expected"] == "host:codex" and fact["actual"] == "host:other"


def test_output_cannot_claim_authorization_verification_or_self_certification():
    manifest = compiler.compile_assurance_slice(valid_input())["manifest"]
    schema = json.loads((ROOT / "control-room/contracts/assurance-execution-manifest.v1.schema.json").read_text())
    assert schema["properties"]["authority_state"]["const"] == manifest["authority_state"]
    assert schema["properties"]["verification_state"]["const"] == manifest["verification_state"]
    assert schema["properties"]["self_certification"]["const"] is manifest["self_certification"] is False
    for name in ("Risk", "DependencyGate", "EvidenceRequirement", "ReviewerPolicy", "ObservableOutput", "Rollback", "LeaseBinding", "ExecutorIdentity"):
        assert schema["$defs"][name]["additionalProperties"] is False
    slice_schema = json.loads((ROOT / "control-room/contracts/assurance-slice-contract.v1.schema.json").read_text())
    input_schema = json.loads((ROOT / "control-room/contracts/assurance-compiler-input.v1.schema.json").read_text())
    required_schema_value = valid_input()["assurance_slice"]["required_tests"][0]
    assert schema_valid(slice_schema, "RequiredTest", required_schema_value)
    altered = copy.deepcopy(required_schema_value); altered["environment"]["runtime"] = "true"; altered["argv"][0] = "true"
    assert not schema_valid(slice_schema, "RequiredTest", altered)
    revised = valid_input()
    planned = revised["engineering_slice_plan"]["slices"][1]["planned_checks"][0]
    planned["failure_condition"] = "a revised accepted compiler failure condition"
    seal_plan(revised)
    revised["assurance_slice"]["engineering_slice_plan_digest"] = revised["engineering_slice_plan"]["plan_digest"]
    required = revised["assurance_slice"]["required_tests"][0]
    required["planned_check_digest"] = execution_contract.canonical_digest(planned)
    required["causal_failure"]["expected"] = planned["failure_condition"]
    seal_contract(revised)
    revised_result = compiler.compile_assurance_slice(revised)
    assert revised_result["ok"] is True, revised_result
    assert schema_valid(slice_schema, "RequiredTest", required)
    assert revised_result["manifest"]["slice"]["required_tests"][0] == required
    assert schema_valid(slice_schema, "Risk", {"risk_class":"R1","summary":"bounded"})
    assert not schema_valid(slice_schema, "Risk", {"risk_class":1,"summary":"bounded"})
    for bad in ("tools//test.py", "tools/", "tools/space path.py"):
        assert not schema_valid(slice_schema, "RepoPath", bad)
    assert schema_valid(slice_schema, "RepoPath", "tools/test.py")
    assert schema_valid(input_schema, "Timestamp", "2026-08-30T15:00:00Z")
    for bad in ("2026-13-01T15:00:00Z", "2026-02-30T16:00:00Z"):
        assert not schema_valid(input_schema, "Timestamp", bad)
        assert not schema_valid(schema, "Timestamp", bad)
    value = valid_input(); fields = value["assurance_slice"]["required_tests"][0]["evidence_fields"]; fields.append(fields[0]); seal_contract(value)
    refusal(value, "FIELD_INVALID", "assurance_slice.required_tests[0].evidence_fields")
    value = valid_input(); value["assurance_slice"]["evidence_requirements"][0]["required_fields"] = [1]; seal_contract(value)
    refusal(value, "FIELD_INVALID", "assurance_slice.evidence_requirements[0].required_fields")


def test_every_bound_category_and_compiler_version_changes_manifest_hash():
    baseline = valid_input()
    baseline_hash = compiler.compile_assurance_slice(baseline)["manifest"]["manifest_hash"]
    variants = []
    contract_change = copy.deepcopy(baseline); contract_change["assurance_slice"]["unfinished_work"].append("A serial consumer changed"); seal_contract(contract_change); variants.append(contract_change)
    rule_change = copy.deepcopy(baseline); rule_change["applicable_rules"]["rules"][0]["revision"] += 1; seal_rules(rule_change); seal_contract(rule_change); variants.append(rule_change)
    coord_change = copy.deepcopy(baseline); coord_change["coordination_snapshot"]["valid_until"] = "2026-08-30T16:30:00Z"; coord_change["coordination_snapshot"]["leases"][0]["expires_at"] = "2026-08-30T16:30:00Z"; seal_coordination(coord_change); variants.append(coord_change)
    repo_change = copy.deepcopy(baseline); repo_change["repository"]["commit_sha"] = "9" * 40; repo_change["assurance_slice"]["repository_binding"]["commit_sha"] = "9" * 40; seal_contract(repo_change); variants.append(repo_change)
    for value in variants:
        result = compiler.compile_assurance_slice(value)
        assert result["ok"] is True, result
        assert result["manifest"]["manifest_hash"] != baseline_hash
    versioned = compiler.compile_assurance_slice(baseline, compiler_version="1.0.1")
    assert versioned["ok"] is True and versioned["manifest"]["manifest_hash"] != baseline_hash


def test_module_has_no_provider_network_git_database_model_or_write_imports():
    source = (BRIDGE / "assurance_slice_compiler.py").read_text()
    for forbidden in ("requests", "urllib", "subprocess", "psycopg", "openai", "anthropic", ".write_text(", "open("):
        assert forbidden not in source


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-q", __file__]))
