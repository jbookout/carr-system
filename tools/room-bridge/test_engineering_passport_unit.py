"""Acceptance tests for the offline Engineering Passport v1 vertical slice."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engineering_passport as ep  # noqa: E402
import execution_contract as contract  # noqa: E402


ROOT = Path(__file__).parents[2]
ENVELOPE = json.loads((ROOT / "control-room/contracts/fixtures/execution-fabric/codex_desktop.execution-envelope.v1.json").read_text())


def digest(seed: str) -> str:
    return "sha256:" + seed * 64


def evidence(ref: str) -> dict[str, str]:
    return {"ref": ref, "redaction_class": "redacted_evidence", "content_digest": digest("a")}


def plan_fixture() -> dict:
    slices = [
        {"slice_ref": "slice:a", "ordinal": 1, "objective": "Implement typed contracts", "definition_of_done": "Plan and receipt schemas validate", "dependency_refs": [], "declared_resource_refs": ["resource:repo"], "declared_component_refs": ["component:contracts"], "declared_plan_step_refs": ["step:contracts"], "baseline_evidence_refs": [evidence("evidence:baseline")], "planned_checks": [{"check_ref": "check:contracts", "failure_condition": "unknown fields are accepted", "evidence_requirement": "redacted_evidence_required"}], "scope_boundary": "contract files and validators", "forbidden_change_refs": ["forbidden:authority"], "concurrency_posture": "parallel_safe", "manual_qa_required": False, "risk_class": "R1", "release_requirement": "required"},
        {"slice_ref": "slice:b", "ordinal": 2, "objective": "Implement deterministic runtime", "definition_of_done": "Eligibility and projection are deterministic", "dependency_refs": [], "declared_resource_refs": ["resource:repo"], "declared_component_refs": ["component:runtime"], "declared_plan_step_refs": ["step:runtime"], "baseline_evidence_refs": [evidence("evidence:baseline")], "planned_checks": [{"check_ref": "check:runtime", "failure_condition": "cycles or inherited authority pass", "evidence_requirement": "redacted_evidence_required"}], "scope_boundary": "offline runtime module", "forbidden_change_refs": ["forbidden:database"], "concurrency_posture": "parallel_safe", "manual_qa_required": False, "risk_class": "R1", "release_requirement": "required"},
        {"slice_ref": "slice:c", "ordinal": 3, "objective": "Integrate the passport projection", "definition_of_done": "Model Room can render a typed passport", "dependency_refs": ["slice:a", "slice:b"], "declared_resource_refs": ["resource:repo"], "declared_component_refs": ["component:room"], "declared_plan_step_refs": ["step:room"], "baseline_evidence_refs": [evidence("evidence:baseline")], "planned_checks": [{"check_ref": "check:room", "failure_condition": "existing card behavior changes without typed fact", "evidence_requirement": "redacted_evidence_required"}], "scope_boundary": "wire and additive reader section", "forbidden_change_refs": ["forbidden:task-ui"], "concurrency_posture": "serial_after_dependencies", "manual_qa_required": True, "risk_class": "R2", "release_requirement": "required"},
    ]
    value = {"schema_version": "engineering-slice-plan.v1", "work_request": {"id": "wr:engineering-passport", "state_version": 1, "canonical_record_digest": digest("b")}, "accepted_plan_revision": {"id": "plan:engineering-passport", "revision": 1, "digest": digest("c")}, "slices": slices}
    value["plan_digest"] = contract.canonical_digest({key: item for key, item in value.items() if key != "plan_digest"})
    return value


def receipt(plan: dict, slice_ref: str, *, outcome: str = "claimed_complete", deviation: bool = False) -> dict:
    check_ref = next(row["check_ref"] for row in next(s for s in plan["slices"] if s["slice_ref"] == slice_ref)["planned_checks"])
    value = {"schema_version": "engineering-slice-receipt.v1", "envelope_digest": contract.execution_envelope_digest(ENVELOPE), "attempt_id": f"attempt:{slice_ref.split(':')[1]}", "slice_ref": slice_ref, "plan_digest": plan["plan_digest"], "attribution": {"actor_ref": "actor:codex", "session_ref": "session:fresh", "adapter_ref": "adapter:codex"}, "planned_resource_refs": ["resource:repo"], "actual_resource_refs": ["resource:repo"], "planned_component_refs": [next(s for s in plan["slices"] if s["slice_ref"] == slice_ref)["declared_component_refs"][0]], "actual_component_refs": [next(s for s in plan["slices"] if s["slice_ref"] == slice_ref)["declared_component_refs"][0]], "checks": [{"check_ref": check_ref, "state": "passed", "evidence_refs": [evidence(f"evidence:{slice_ref.split(':')[1]}-check")]}], "outcome": outcome, "artifact_refs": [f"artifact:{slice_ref.split(':')[1]}"], "evidence_refs": [evidence(f"evidence:{slice_ref.split(':')[1]}")], "deviations": [], "source_evidence": {"worktree_ref": "worktree:isolated", "branch_ref": "branch:engineering-passport", "source_sha": "0e7279b4", "evidence_refs": [evidence("evidence:source")]}, "reset_reconstruction": {"fresh_session": True, "inherited_transcript_used": False, "reconstruction_free": True, "remediation_action": None}, "executor_claim": {"claim_state": "executor_claim", "claimed_by": "actor:codex", "claimed_at": "2026-08-24T12:15:00Z"}, "independent_verification_required": True}
    if deviation:
        value["deviations"] = [{"deviation_ref": "deviation:scope", "category": "discovered-constraint", "reason": "The existing reader required a typed wire kind", "impact": "Additive validator registration was needed", "plan_revision_required": False, "evidence_refs": [evidence("evidence:deviation")] }]
    return value


def test_plan_rejects_duplicates_missing_dependencies_and_cycles():
    plan = plan_fixture()
    duplicate = copy.deepcopy(plan); duplicate["slices"].append(copy.deepcopy(plan["slices"][0])); duplicate["plan_digest"] = contract.canonical_digest({k: v for k, v in duplicate.items() if k != "plan_digest"})
    try: ep.validate_engineering_slice_plan(duplicate); assert False
    except ep.EngineeringContractError: pass
    missing = copy.deepcopy(plan); missing["slices"][2]["dependency_refs"] = ["slice:nope"]; missing["plan_digest"] = contract.canonical_digest({k: v for k, v in missing.items() if k != "plan_digest"})
    try: ep.validate_engineering_slice_plan(missing); assert False
    except ep.EngineeringContractError: pass
    cycle = copy.deepcopy(plan); cycle["slices"][0]["dependency_refs"] = ["slice:b"]; cycle["slices"][1]["dependency_refs"] = ["slice:a"]; cycle["plan_digest"] = contract.canonical_digest({k: v for k, v in cycle.items() if k != "plan_digest"})
    try: ep.validate_engineering_slice_plan(cycle); assert False
    except ep.EngineeringContractError: pass


def test_parallel_eligibility_and_fresh_packet_preserve_authority():
    plan = plan_fixture(); assert ep.eligible_slices(plan) == ["slice:a", "slice:b"]
    assert ep.eligible_slices(plan, [receipt(plan, "slice:a"), receipt(plan, "slice:b")]) == []
    packet = ep.build_engineering_slice_packet(ENVELOPE, plan, "slice:a")
    assert ep.validate_engineering_slice_packet(packet, plan, ENVELOPE)["packet_digest"] == packet["packet_digest"]
    assert packet["fresh_native_session_required"] is True
    assert packet["envelope"]["phase_binding"]["session_affinity"] == "fresh_native_session_required"
    assert packet["envelope"]["server_binding"] == ENVELOPE["server_binding"]
    assert packet["envelope"]["handoff"]["capability_inherited"] is False


def test_receipt_cannot_disappear_deviation_or_self_verify():
    plan = plan_fixture(); row = receipt(plan, "slice:a", deviation=True)
    ep.validate_engineering_slice_receipt(row, plan, ENVELOPE)
    bad = copy.deepcopy(row); bad["executor_claim"]["claimed_by"] = "reviewer:self"
    ep.validate_engineering_slice_receipt(bad, plan, ENVELOPE)
    try:
        ep.project_engineering_passport(plan, [row], reviewer_facts=[{"slice_ref": "slice:a", "reviewer_ref": "actor:codex", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence("evidence:review")], "is_independent": True}]); assert False
    except ep.EngineeringContractError: pass
    passport = ep.project_engineering_passport(plan, [row], reviewer_facts=[{"slice_ref": "slice:a", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence("evidence:review")], "is_independent": True}])
    assert "deviation:scope" in passport["operator_receipt"]["deviations"]
    assert passport["closure_state"] == "blocked"


def test_verified_closure_requires_qa_release_learning_and_rejects_self_verifier():
    plan = plan_fixture(); receipts = [receipt(plan, "slice:a"), receipt(plan, "slice:b"), receipt(plan, "slice:c")]
    reviewers = [{"slice_ref": f"slice:{x}", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence(f"evidence:review-{x}")], "is_independent": True} for x in "abc"]
    qa = [{"slice_ref": "slice:c", "state": "passed", "evidence_refs": [evidence("evidence:qa")], "note": "fresh mobile and keyboard path passed"}]
    explanation = {field: {"state": "complete", "evidence_refs": [evidence(f"evidence:{field}")], "note": f"{field} complete"} for field in ("work", "proof", "explanation")}
    passport = ep.project_engineering_passport(plan, receipts, reviewer_facts=reviewers, qa_facts=qa, explanation=explanation, release={"state": "released", "evidence_refs": [evidence("evidence:release")], "note": "exact SHA released"}, learning={"state": "proposed", "evidence_refs": [evidence("evidence:learning")], "note": "promote regression test"})
    assert passport["closure_state"] == "complete"
    assert ep.validate_engineering_passport(passport)["closure_state"] == "complete"
    bad = copy.deepcopy(passport); bad["closure"]["release"] = {"state": "released", "evidence_refs": [], "note": "missing proof"}; bad["projection_digest"] = contract.canonical_digest({k: v for k, v in bad.items() if k != "projection_digest"})
    try: ep.validate_engineering_passport(bad); assert False
    except ep.EngineeringContractError: pass


def test_wire_accepts_additive_engineering_passport_kind():
    plan = plan_fixture(); passport = ep.project_engineering_passport(plan, [])
    wrapped = ep.engineering_passport_wire(passport)
    assert contract.job_passport_wire_receipt("engineering_passport", passport) == wrapped


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for fn in tests: fn(); print("ok", fn.__name__)
