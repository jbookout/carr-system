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
        {"slice_ref": "slice:a", "ordinal": 1, "objective": "Implement typed contracts", "definition_of_done": "Plan and receipt schemas validate", "dependency_refs": [], "declared_resource_refs": ["resource:worktree-a"], "declared_component_refs": ["component:execution-fabric"], "declared_plan_step_refs": ["step:synthetic-read"], "baseline_evidence_refs": [evidence("evidence:baseline")], "planned_checks": [{"check_ref": "check:contracts", "failure_condition": "unknown fields are accepted", "evidence_requirement": "redacted_evidence_required"}], "scope_boundary": "contract files and validators", "forbidden_change_refs": ["forbidden:authority"], "concurrency_posture": "parallel_safe", "manual_qa_required": False, "risk_class": "R1", "release_requirement": "required"},
        {"slice_ref": "slice:b", "ordinal": 2, "objective": "Implement deterministic runtime", "definition_of_done": "Eligibility and projection are deterministic", "dependency_refs": [], "declared_resource_refs": ["resource:worktree-a"], "declared_component_refs": ["component:execution-fabric"], "declared_plan_step_refs": ["step:synthetic-read"], "baseline_evidence_refs": [evidence("evidence:baseline")], "planned_checks": [{"check_ref": "check:runtime", "failure_condition": "cycles or inherited authority pass", "evidence_requirement": "redacted_evidence_required"}], "scope_boundary": "offline runtime module", "forbidden_change_refs": ["forbidden:database"], "concurrency_posture": "parallel_safe", "manual_qa_required": False, "risk_class": "R1", "release_requirement": "required"},
        {"slice_ref": "slice:c", "ordinal": 3, "objective": "Integrate the passport projection", "definition_of_done": "Model Room can render a typed passport", "dependency_refs": ["slice:a", "slice:b"], "declared_resource_refs": ["resource:worktree-a"], "declared_component_refs": ["component:execution-fabric"], "declared_plan_step_refs": ["step:synthetic-read"], "baseline_evidence_refs": [evidence("evidence:baseline")], "planned_checks": [{"check_ref": "check:room", "failure_condition": "existing card behavior changes without typed fact", "evidence_requirement": "redacted_evidence_required"}], "scope_boundary": "wire and additive reader section", "forbidden_change_refs": ["forbidden:task-ui"], "concurrency_posture": "serial_after_dependencies", "manual_qa_required": True, "risk_class": "R2", "release_requirement": "required"},
    ]
    value = {"schema_version": "engineering-slice-plan.v1", "work_request": {"id": "wr-synthetic-read-only", "state_version": 1, "canonical_record_digest": digest("c")}, "accepted_plan_revision": {"id": "plan-synthetic-read", "revision": 1, "digest": digest("a")}, "slices": slices}
    value["plan_digest"] = contract.canonical_digest({key: item for key, item in value.items() if key != "plan_digest"})
    return value


def receipt(plan: dict, slice_ref: str, *, outcome: str = "claimed_complete", deviation: bool = False) -> dict:
    slice_row = next(s for s in plan["slices"] if s["slice_ref"] == slice_ref)
    check_ref = next(row["check_ref"] for row in slice_row["planned_checks"])
    value = {"schema_version": "engineering-slice-receipt.v1", "envelope_digest": contract.execution_envelope_digest(ENVELOPE), "attempt_id": f"attempt:{slice_ref.split(':')[1]}", "slice_ref": slice_ref, "plan_digest": plan["plan_digest"], "attribution": {"actor_ref": "actor:codex", "session_ref": "session:fresh", "adapter_ref": "adapter:codex"}, "planned_resource_refs": slice_row["declared_resource_refs"], "actual_resource_refs": slice_row["declared_resource_refs"], "planned_component_refs": slice_row["declared_component_refs"], "actual_component_refs": slice_row["declared_component_refs"], "checks": [{"check_ref": check_ref, "state": "passed", "evidence_refs": [evidence(f"evidence:{slice_ref.split(':')[1]}-check")]}], "outcome": outcome, "artifact_refs": [f"artifact:{slice_ref.split(':')[1]}"], "evidence_refs": [evidence(f"evidence:{slice_ref.split(':')[1]}")], "deviations": [], "source_evidence": {"worktree_ref": "worktree:isolated", "branch_ref": "branch:engineering-passport", "source_sha": "0e7279b4", "evidence_refs": [evidence("evidence:source")]}, "reset_reconstruction": {"fresh_session": True, "inherited_transcript_used": False, "reconstruction_free": True, "remediation_action": None}, "executor_claim": {"claim_state": "executor_claim", "claimed_by": "actor:codex", "claimed_at": "2026-08-24T12:15:00Z"}, "independent_verification_required": True}
    if deviation:
        value["deviations"] = [{"deviation_ref": "deviation:scope", "category": "discovered-constraint", "reason": "The existing reader required a typed wire kind", "impact": "Additive validator registration was needed", "plan_revision_required": False, "evidence_refs": [evidence("evidence:deviation")], "out_of_scope_resource_refs": [], "out_of_scope_component_refs": [], "review_state": "resolved" }]
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
    plan = plan_fixture(); assert ep.eligible_slices(plan, execution_envelopes=[]) == ["slice:a", "slice:b"]
    assert ep.eligible_slices(plan, [receipt(plan, "slice:a"), receipt(plan, "slice:b")], execution_envelopes=[ENVELOPE]) == []
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
        ep.project_engineering_passport(plan, [row], execution_envelopes=[ENVELOPE], reviewer_facts=[{"slice_ref": "slice:a", "attempt_id": "attempt:a", "reviewer_ref": "actor:codex", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence("evidence:review")], "is_independent": True, "reviewed_deviation_refs": ["deviation:scope"], "resolved_deviation_refs": ["deviation:scope"]}]); assert False
    except ep.EngineeringContractError: pass
    passport = ep.project_engineering_passport(plan, [row], execution_envelopes=[ENVELOPE], reviewer_facts=[{"slice_ref": "slice:a", "attempt_id": "attempt:a", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence("evidence:review")], "is_independent": True, "reviewed_deviation_refs": ["deviation:scope"], "resolved_deviation_refs": ["deviation:scope"]}])
    assert "deviation:scope" in passport["operator_receipt"]["deviations"]
    assert passport["closure_state"] == "blocked"


def test_verified_closure_requires_qa_release_learning_and_rejects_self_verifier():
    plan = plan_fixture(); receipts = [receipt(plan, "slice:a"), receipt(plan, "slice:b"), receipt(plan, "slice:c")]
    reviewers = [{"slice_ref": f"slice:{x}", "attempt_id": f"attempt:{x}", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence(f"evidence:review-{x}")], "is_independent": True, "reviewed_deviation_refs": [], "resolved_deviation_refs": []} for x in "abc"]
    qa = [{"slice_ref": "slice:c", "state": "passed", "evidence_refs": [evidence("evidence:qa")], "note": "fresh mobile and keyboard path passed"}]
    explanation = {field: {"state": "complete", "evidence_refs": [evidence(f"evidence:{field}")], "note": f"{field} complete"} for field in ("work", "proof", "explanation")}
    passport = ep.project_engineering_passport(plan, receipts, execution_envelopes=[ENVELOPE], reviewer_facts=reviewers, qa_facts=qa, explanation=explanation, release={"state": "released", "evidence_refs": [evidence("evidence:release")], "note": "exact SHA released"}, learning={"state": "proposed", "route": "regression_test", "evidence_refs": [evidence("evidence:learning")], "note": "promote regression test"})
    assert passport["closure_state"] == "complete"
    assert ep.validate_engineering_passport(passport)["closure_state"] == "complete"
    bad = copy.deepcopy(passport); bad["closure"]["release"] = {"state": "released", "evidence_refs": [], "note": "missing proof"}; bad["projection_digest"] = contract.canonical_digest({k: v for k, v in bad.items() if k != "projection_digest"})
    try: ep.validate_engineering_passport(bad); assert False
    except ep.EngineeringContractError: pass


def test_wire_accepts_additive_engineering_passport_kind():
    plan = plan_fixture(); passport = ep.project_engineering_passport(plan, [], execution_envelopes=[])
    assert passport["execution_envelopes"] == []
    assert passport["closure"]["learning"]["route"] is None
    wrapped = ep.engineering_passport_wire(passport)
    assert contract.job_passport_wire_receipt("engineering_passport", passport) == wrapped


def test_forged_complete_passport_from_zero_receipts_refuses():
    plan = plan_fixture()
    forged = ep.project_engineering_passport(plan, [], execution_envelopes=[])
    forged["closure_state"] = "complete"
    forged["projection_digest"] = contract.canonical_digest({k: v for k, v in forged.items() if k != "projection_digest"})
    try:
        ep.validate_engineering_passport(forged)
        assert False, "zero-receipt forged completion must refuse"
    except ep.EngineeringContractError:
        pass


def test_packet_full_source_scope_replacement_refuses_after_recomputed_digest():
    plan = plan_fixture(); source = copy.deepcopy(ENVELOPE)
    source["request"]["declared_expectations"]["plan_step_refs"].append("step:other")
    source["request"]["declared_expectations"]["component_refs"].append("component:other")
    source["request"]["declared_expectations"]["resource_refs"].append("resource:other")
    packet = ep.build_engineering_slice_packet(source, plan, "slice:a")
    packet["envelope"]["request"]["declared_expectations"] = copy.deepcopy(source["request"]["declared_expectations"])
    packet["packet_digest"] = contract.canonical_digest({k: v for k, v in packet.items() if k != "packet_digest"})
    try:
        ep.validate_engineering_slice_packet(packet, plan, ENVELOPE)
        assert False, "full-source scope replacement must refuse"
    except ep.EngineeringContractError:
        pass


def test_receipt_forged_planned_or_actual_scope_refuses():
    plan = plan_fixture(); row = receipt(plan, "slice:a")
    bad_planned = copy.deepcopy(row); bad_planned["planned_resource_refs"] = ["resource:outside"]
    try: ep.validate_engineering_slice_receipt(bad_planned, plan, ENVELOPE); assert False
    except ep.EngineeringContractError: pass
    bad_actual = copy.deepcopy(row); bad_actual["actual_component_refs"] = ["component:outside"]
    try: ep.validate_engineering_slice_receipt(bad_actual, plan, ENVELOPE); assert False
    except ep.EngineeringContractError: pass


def test_eligibility_requires_bound_successful_independent_reviews():
    plan = plan_fixture(); a = receipt(plan, "slice:a"); b = receipt(plan, "slice:b")
    try: ep.eligible_slices(plan, [a, b], ["slice:a", "slice:b"], execution_envelopes=[ENVELOPE]); assert False
    except ep.EngineeringContractError: pass
    reviewers = [
        {"slice_ref":"slice:a","attempt_id":"attempt:a","reviewer_ref":"reviewer:a","session_ref":"session:review-a","state":"passed","evidence_refs":[evidence("evidence:review-a")],"is_independent":True,"reviewed_deviation_refs":[],"resolved_deviation_refs":[]},
        {"slice_ref":"slice:b","attempt_id":"attempt:b","reviewer_ref":"reviewer:b","session_ref":"session:review-b","state":"passed","evidence_refs":[evidence("evidence:review-b")],"is_independent":True,"reviewed_deviation_refs":[],"resolved_deviation_refs":[]},
    ]
    assert ep.eligible_slices(plan, [a, b], reviewers, execution_envelopes=[ENVELOPE]) == ["slice:c"]


def test_unresolved_deviation_and_arbitrary_learning_route_block_completion():
    plan = plan_fixture(); rows = [receipt(plan, "slice:a"), receipt(plan, "slice:b", deviation=True), receipt(plan, "slice:c")]
    reviewers = [{"slice_ref": f"slice:{x}", "attempt_id": f"attempt:{x}", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": ("failed" if x == "b" else "passed"), "evidence_refs": [evidence(f"evidence:review-{x}")], "is_independent": True, "reviewed_deviation_refs": (["deviation:scope"] if x == "b" else []), "resolved_deviation_refs": []} for x in "abc"]
    qa = [{"slice_ref":"slice:c","state":"passed","evidence_refs":[evidence("evidence:qa")],"note":"passed"}]
    explanation = {field: {"state":"complete","evidence_refs":[evidence(f"evidence:{field}")],"note":"complete"} for field in ("work","proof","explanation")}
    passport = ep.project_engineering_passport(plan, rows, execution_envelopes=[ENVELOPE], reviewer_facts=reviewers, qa_facts=qa, explanation=explanation, release={"state":"released","evidence_refs":[evidence("evidence:release")],"note":"released"}, learning={"state":"proposed","route":"regression_test","evidence_refs":[evidence("evidence:learning")],"note":"bad"})
    assert passport["closure_state"] == "blocked"
    passport["closure"]["learning"]["route"] = "made_up"
    passport["projection_digest"] = contract.canonical_digest({k: v for k, v in passport.items() if k != "projection_digest"})
    try: ep.validate_engineering_passport(passport); assert False
    except ep.EngineeringContractError: pass


def test_forged_complete_duplicate_slice_coverage_and_operator_fields_refuse():
    plan = plan_fixture(); rows = [receipt(plan, "slice:a"), receipt(plan, "slice:b"), receipt(plan, "slice:c")]
    reviewers = [{"slice_ref": f"slice:{x}", "attempt_id": f"attempt:{x}", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence(f"evidence:review-{x}")], "is_independent": True, "reviewed_deviation_refs": [], "resolved_deviation_refs": []} for x in "abc"]
    qa = [{"slice_ref":"slice:c","state":"passed","evidence_refs":[evidence("evidence:qa")],"note":"passed"}]
    explanation = {field: {"state":"complete","evidence_refs":[evidence(f"evidence:{field}")],"note":"complete"} for field in ("work","proof","explanation")}
    p = ep.project_engineering_passport(plan, rows, execution_envelopes=[ENVELOPE], reviewer_facts=reviewers, qa_facts=qa, explanation=explanation, release={"state":"released","evidence_refs":[evidence("evidence:release")],"note":"released"}, learning={"state":"proposed","route":"regression_test","evidence_refs":[evidence("evidence:learning")],"note":"test"})
    p["slices"][1] = copy.deepcopy(p["slices"][0]); p["projection_digest"] = contract.canonical_digest({k:v for k,v in p.items() if k != "projection_digest"})
    try: ep.validate_engineering_passport(p); assert False
    except ep.EngineeringContractError: pass
    p = ep.project_engineering_passport(plan, rows, execution_envelopes=[ENVELOPE], reviewer_facts=reviewers, qa_facts=qa, explanation=explanation, release={"state":"released","evidence_refs":[evidence("evidence:release")],"note":"released"}, learning={"state":"proposed","route":"regression_test","evidence_refs":[evidence("evidence:learning")],"note":"test"})
    p["operator_receipt"]["remaining_risk"] = ["slice:a"]; p["projection_digest"] = contract.canonical_digest({k:v for k,v in p.items() if k != "projection_digest"})
    try: ep.validate_engineering_passport(p); assert False
    except ep.EngineeringContractError: pass


def test_plan_envelope_cross_authority_binding_refuses_build_packet_and_receipt():
    plan = plan_fixture(); wrong = copy.deepcopy(plan); wrong["work_request"]["id"] = "wr:other"; wrong["plan_digest"] = contract.canonical_digest({k:v for k,v in wrong.items() if k != "plan_digest"})
    try: ep.build_engineering_slice_packet(ENVELOPE, wrong, "slice:a"); assert False
    except ep.EngineeringContractError: pass
    row = receipt(plan, "slice:a")
    try: ep.validate_engineering_slice_receipt(row, wrong, ENVELOPE); assert False
    except ep.EngineeringContractError: pass


def test_evidence_requirements_and_duplicate_review_verdicts_refuse():
    plan = plan_fixture(); row = receipt(plan, "slice:a"); row["checks"][0]["evidence_refs"] = []
    try: ep.validate_engineering_slice_receipt(row, plan, ENVELOPE); assert False
    except ep.EngineeringContractError: pass
    metadata_plan = copy.deepcopy(plan); metadata_plan["slices"][0]["planned_checks"][0]["evidence_requirement"] = "metadata_only_sufficient"; metadata_plan["plan_digest"] = contract.canonical_digest({k:v for k,v in metadata_plan.items() if k != "plan_digest"})
    metadata_row = receipt(metadata_plan, "slice:a")
    try: ep.validate_engineering_slice_receipt(metadata_row, metadata_plan, ENVELOPE); assert False
    except ep.EngineeringContractError: pass
    metadata_row["checks"][0]["evidence_refs"][0]["redaction_class"] = "metadata_only"
    ep.validate_engineering_slice_receipt(metadata_row, metadata_plan, ENVELOPE)
    reviewers = [{"slice_ref":"slice:a","attempt_id":"attempt:a","reviewer_ref":"reviewer:one","session_ref":"session:one","state":"passed","evidence_refs":[evidence("evidence:one")],"is_independent":True,"reviewed_deviation_refs":[],"resolved_deviation_refs":[]}, {"slice_ref":"slice:a","attempt_id":"attempt:a","reviewer_ref":"reviewer:two","session_ref":"session:two","state":"passed","evidence_refs":[evidence("evidence:two")],"is_independent":True,"reviewed_deviation_refs":[],"resolved_deviation_refs":[]}]
    try: ep.eligible_slices(plan, [receipt(plan, "slice:a")], reviewers, execution_envelopes=[ENVELOPE]); assert False
    except ep.EngineeringContractError: pass


def test_projection_requires_authoritative_envelope_resolution():
    plan = plan_fixture(); row = receipt(plan, "slice:a"); row_b = receipt(plan, "slice:b")
    forged = copy.deepcopy(row); forged["envelope_digest"] = digest("e")
    for order in ([forged, row_b], [row_b, forged]):
        try: ep.validate_engineering_slice_receipt(forged, plan, ENVELOPE); assert False
        except ep.EngineeringContractError: pass
        try: ep.eligible_slices(plan, order, execution_envelopes=[ENVELOPE]); assert False
        except ep.EngineeringContractError: pass
    try: ep.project_engineering_passport(plan, [forged], execution_envelopes=[ENVELOPE]); assert False
    except ep.EngineeringContractError: pass
    wrong_envelope = copy.deepcopy(ENVELOPE); wrong_envelope["work_request_id"] = "wr-other"
    try: ep.project_engineering_passport(plan, [row], execution_envelopes=[wrong_envelope]); assert False
    except ep.EngineeringContractError: pass
    complete_rows = [receipt(plan, f"slice:{x}") for x in "abc"]
    reviewers = [{"slice_ref": f"slice:{x}", "attempt_id": f"attempt:{x}", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence(f"evidence:review-{x}")], "is_independent": True, "reviewed_deviation_refs": [], "resolved_deviation_refs": []} for x in "abc"]
    qa = [{"slice_ref": "slice:c", "state": "passed", "evidence_refs": [evidence("evidence:qa")], "note": "passed"}]
    explanation = {field: {"state": "complete", "evidence_refs": [evidence(f"evidence:{field}")], "note": "complete"} for field in ("work", "proof", "explanation")}
    passport = ep.project_engineering_passport(plan, complete_rows, execution_envelopes=[ENVELOPE], reviewer_facts=reviewers, qa_facts=qa, explanation=explanation, release={"state": "released", "evidence_refs": [evidence("evidence:release")], "note": "released"}, learning={"state": "proposed", "route": "regression_test", "evidence_refs": [evidence("evidence:learning")], "note": "test"})
    passport["receipts"][0]["envelope_digest"] = digest("e")
    passport["projection_digest"] = contract.canonical_digest({key: item for key, item in passport.items() if key != "projection_digest"})
    try: ep.validate_engineering_passport(passport); assert False
    except ep.EngineeringContractError: pass


def test_passport_envelope_retention_is_exactly_receipt_digest_set():
    plan = plan_fixture(); passport = ep.project_engineering_passport(plan, [], execution_envelopes=[])
    assert passport["execution_envelopes"] == []
    ep.validate_engineering_passport(passport)
    rows = [receipt(plan, "slice:a"), receipt(plan, "slice:b"), receipt(plan, "slice:c")]
    reviewers = [{"slice_ref": f"slice:{x}", "attempt_id": f"attempt:{x}", "reviewer_ref": "reviewer:independent", "session_ref": "session:review", "state": "passed", "evidence_refs": [evidence(f"evidence:review-{x}")], "is_independent": True, "reviewed_deviation_refs": [], "resolved_deviation_refs": []} for x in "abc"]
    qa = [{"slice_ref": "slice:c", "state": "passed", "evidence_refs": [evidence("evidence:qa")], "note": "passed"}]
    explanation = {field: {"state": "complete", "evidence_refs": [evidence(f"evidence:{field}")], "note": "complete"} for field in ("work", "proof", "explanation")}
    complete = ep.project_engineering_passport(plan, rows, execution_envelopes=[ENVELOPE], reviewer_facts=reviewers, qa_facts=qa, explanation=explanation, release={"state": "released", "evidence_refs": [evidence("evidence:release")], "note": "released"}, learning={"state": "proposed", "route": "regression_test", "evidence_refs": [evidence("evidence:learning")], "note": "test"})
    extra = copy.deepcopy(ENVELOPE); extra["envelope_id"] = "env-synthetic-extra"
    complete["execution_envelopes"].append(extra)
    complete["projection_digest"] = contract.canonical_digest({key: item for key, item in complete.items() if key != "projection_digest"})
    try: ep.validate_engineering_passport(complete); assert False
    except ep.EngineeringContractError: pass


def test_duplicate_planned_check_refs_refuse_every_producer_path():
    plan = plan_fixture(); plan["slices"][0]["planned_checks"].append(copy.deepcopy(plan["slices"][0]["planned_checks"][0])); plan["plan_digest"] = contract.canonical_digest({key: item for key, item in plan.items() if key != "plan_digest"})
    try: ep.validate_engineering_slice_plan(plan); assert False
    except ep.EngineeringContractError: pass
    try: ep.validate_engineering_slice_receipt(receipt(plan, "slice:a"), plan, ENVELOPE); assert False
    except ep.EngineeringContractError: pass


# --- V5-F03 deep-module execution contract ------------------------------------
#
# These fixtures are deliberately the same shape as the ones in
# mcp-server/test/engineering-runtime.test.mjs.  The two validators must accept
# and refuse exactly the same closed contract, so the case tables below are kept
# identical on purpose.


def reseal(plan: dict) -> dict:
    plan["plan_digest"] = contract.canonical_digest({k: v for k, v in plan.items() if k != "plan_digest"})
    return plan


def model_step(**overrides) -> dict:
    step = {
        "step_ref": "step:synthetic-read", "responsibility_class": "classification",
        "input_contract_ref": "contract:step-input", "output_contract_ref": "contract:step-output",
        "rationale": "the candidate label is genuinely uncertain and code would reduce quality",
        "selection_basis": ["typed_uncertainty", "quality_gain"],
    }
    step.update(overrides)
    return step


def design_contract(row: dict) -> dict:
    depth = ep.classify_design_depth(row)
    redaction = "redacted_evidence" if any(
        check["evidence_requirement"] == "redacted_evidence_required" for check in row["planned_checks"]
    ) else "metadata_only"
    return {
        "contract_version": ep.DESIGN_CONTRACT_VERSION,
        "rationale": "the closed validator owns this behavior end to end",
        "dependency_rationale": "no accepted predecessor slice is required",
        "code_model_decision": {
            "rationale": "stable enforceable behavior stays deterministic code",
            "selection_basis": ["capability_gain", "quality_gain"],
            "model_judgment_steps": [],
        },
        "routing": {"executor_class": "deterministic_code", "adapter_ref": "adapter:codex-desktop", "fresh_session_required": True},
        "authority": {"capability_profile": "capability:engineering-repository-write", "read_only": False, "environment": "rehearsal"},
        "isolation": {"worktree_required": True, "branch_required": True, "shared_resource_refs": []},
        "tests": {
            "planned_check_refs": [check["check_ref"] for check in row["planned_checks"]],
            "verification_lanes": ["contract"] + (["manual_qa"] if row["manual_qa_required"] else []),
        },
        "review": {"independent_review_required": True, "reviewer_class": "independent_agent"},
        "failure": {"failure_modes": [{
            "failure_ref": "failure:contract-drift", "detection": "the closed validator refuses the plan",
            "compensation": "revise the accepted plan revision before admission",
        }]},
        "evidence": {
            "redaction_class": redaction, "retention": "material_redacted",
            "evidence_refs": [{"ref": "evidence:design", "redaction_class": redaction, "content_digest": digest("a")}],
        },
        "deployment": {
            "release_requirement": row["release_requirement"],
            "rollback_ref": "release:rollback-plan" if row["release_requirement"] == "required" else None,
            "confirmation_required": row["risk_class"] not in {"R0", "R1"},
        },
        "completion": {
            "completion_predicate": "every accepted planned check passes under independent review",
            "verified_by": "independent_review_and_manual_qa" if row["manual_qa_required"] else "independent_review",
        },
        "seam_decision": {
            "mode": "extend", "target_seam_ref": "seam:engineering-runtime",
            "measurement": {"basis": "complexity_reduction", "note": "extending the proven validator is smaller than a new module"},
            "new_module_justification": None, "replaced_seam_refs": [], "residual_authority_refs": [],
        },
        "full_design_refs": None if depth == "short" else {
            "design_interview_ref": "interview:v5-f03", "authority_envelope_ref": "envelope:v5-f03",
            "failure_model_ref": "failure-model:v5-f03", "fixture_refs": ["fixture:v5-f03-boundary"],
            "oracle_ref": "oracle:doctorcre-v5:Q035.D1",
        },
        "short_template": None if depth == "full" else {
            "template_ref": "template:short-governed-v1",
            "objective_summary": "one bounded parallel-safe change with no dependencies",
            "verification_ref": "verification:short-governed-v1",
        },
    }


def v2_slice(slice_ref: str = "slice:short", ordinal: int = 1, *, design: dict | None = None, **overrides) -> dict:
    row = {
        "slice_ref": slice_ref, "ordinal": ordinal, "objective": "Deepen the accepted slice contract",
        "definition_of_done": "Both validators agree on one closed contract",
        "dependency_refs": [], "declared_resource_refs": ["resource:worktree-a"],
        "declared_component_refs": ["component:execution-fabric"], "declared_plan_step_refs": ["step:synthetic-read"],
        "baseline_evidence_refs": [evidence("evidence:baseline")],
        "planned_checks": [{"check_ref": "check:contract", "failure_condition": "an unknown field is accepted", "evidence_requirement": "redacted_evidence_required"}],
        "scope_boundary": "the two existing slice-plan validators", "forbidden_change_refs": ["forbidden:new-authority"],
        "concurrency_posture": "parallel_safe", "manual_qa_required": False,
        "risk_class": "R1", "release_requirement": "not_required",
    }
    row.update(overrides)
    row["design_contract"] = copy.deepcopy(design) if design is not None else design_contract(row)
    return row


def v2_plan(slices: list[dict]) -> dict:
    value = {
        "schema_version": "engineering-slice-plan.v2",
        "work_request": {"id": "wr-synthetic-read-only", "state_version": 1, "canonical_record_digest": digest("c")},
        "accepted_plan_revision": {"id": "plan-synthetic-read", "revision": 1, "digest": digest("a")},
        "slices": slices,
    }
    return reseal(value)


def refuses(plan: dict, note: str = "") -> None:
    try:
        ep.validate_engineering_slice_plan(plan)
    except ep.EngineeringContractError:
        return
    raise AssertionError(f"plan should have refused: {note}")


def test_design_depth_classifier_matches_the_approved_short_predicate():
    base = v2_slice()
    assert ep.classify_design_depth(base) == "short"
    for field, value in (
        ("concurrency_posture", "serial_after_dependencies"),
        ("concurrency_posture", "exclusive_resource"),
        ("manual_qa_required", True),
        ("release_requirement", "required"),
        ("dependency_refs", ["slice:other"]),
        ("declared_resource_refs", ["resource:a", "resource:b"]),
        ("declared_component_refs", ["component:a", "component:b"]),
        ("declared_plan_step_refs", ["step:a", "step:b"]),
    ):
        row = copy.deepcopy(base); row[field] = value
        assert ep.classify_design_depth(row) == "full", f"{field}={value}"
    for risk in ("R0", "R1", "R2", "R3"):
        row = copy.deepcopy(base); row["risk_class"] = risk
        assert ep.classify_design_depth(row) == "short", risk
    for risk in ("R4", "R5", "R6"):
        row = copy.deepcopy(base); row["risk_class"] = risk
        assert ep.classify_design_depth(row) == "full", risk
    # At most one declared resource, component and plan step still qualifies.
    row = copy.deepcopy(base); row["declared_resource_refs"] = []; row["declared_component_refs"] = []
    row["declared_plan_step_refs"] = []
    assert ep.classify_design_depth(row) == "short"


def test_planned_check_count_is_never_a_classifier_input():
    base = v2_slice()
    inputs = ep.design_depth_inputs(base)
    assert "planned_check" not in json.dumps(inputs)
    many = copy.deepcopy(base)
    many["planned_checks"] = [
        {"check_ref": f"check:extra-{index}", "failure_condition": "verification is missing",
         "evidence_requirement": "redacted_evidence_required"} for index in range(4)
    ]
    assert ep.design_depth_inputs(many) == inputs
    assert ep.classify_design_depth(many) == "short"
    # Every declared check stays mandatory even on the SHORT path.
    many["design_contract"] = design_contract(many)
    ep.validate_engineering_slice_plan(v2_plan([copy.deepcopy(many)]))
    dropped = copy.deepcopy(many); dropped["design_contract"]["tests"]["planned_check_refs"] = ["check:extra-0"]
    refuses(v2_plan([dropped]), "tests may not drop a planned check")
    emptied = copy.deepcopy(many); emptied["planned_checks"] = []
    refuses(v2_plan([emptied]), "a slice must plan at least one check")


def test_agent_cannot_self_label_design_depth_or_a_bypass():
    for label in ("design_depth", "simple", "complexity", "classifier_override", "bypass"):
        row = v2_slice(); row[label] = "short"
        try:
            ep.classify_design_depth(row)
            raise AssertionError(f"classifier consumed a self-label: {label}")
        except ep.EngineeringContractError:
            pass
        refuses(v2_plan([row]), f"slice self-label {label}")
        contracted = v2_slice(); contracted["design_contract"][label] = "short"
        refuses(v2_plan([contracted]), f"contract self-label {label}")


def test_every_design_contract_field_is_bound_and_changes_the_canonical_digest():
    plan = v2_plan([v2_slice()])
    assert ep.validate_engineering_slice_plan(plan)["plan_digest"] == plan["plan_digest"]
    for field in sorted(ep.DESIGN_CONTRACT_FIELDS):
        missing = copy.deepcopy(plan); del missing["slices"][0]["design_contract"][field]
        refuses(reseal(missing), f"missing {field}")
    extra = copy.deepcopy(plan); extra["slices"][0]["design_contract"]["extra_field"] = "x"
    refuses(reseal(extra), "unknown design contract field")
    stale = copy.deepcopy(plan); stale["slices"][0]["design_contract"]["rationale"] = "a different rationale"
    assert contract.canonical_digest({k: v for k, v in stale.items() if k != "plan_digest"}) != plan["plan_digest"]
    refuses(stale, "stale digest after a design contract change")
    for path, value in (
        (("routing", "fresh_session_required"), False),
        (("authority", "capability_profile"), "capability:read-only"),
        (("isolation", "worktree_required"), False),
        (("review", "independent_review_required"), False),
        (("evidence", "redaction_class"), "metadata_only"),
        (("deployment", "release_requirement"), "required"),
        (("completion", "verified_by"), "independent_review_and_manual_qa"),
    ):
        row = copy.deepcopy(plan); row["slices"][0]["design_contract"][path[0]][path[1]] = value
        refuses(reseal(row), f"{path[0]}.{path[1]}={value}")
    empty_failure = copy.deepcopy(plan); empty_failure["slices"][0]["design_contract"]["failure"]["failure_modes"] = []
    refuses(reseal(empty_failure), "a slice must model at least one failure mode")


def test_reserved_deterministic_responsibilities_refuse_model_judgment():
    for reserved in sorted(ep.RESERVED_CODE_RESPONSIBILITIES):
        row = v2_slice()
        row["design_contract"]["routing"]["executor_class"] = "model_assisted"
        row["design_contract"]["code_model_decision"]["model_judgment_steps"] = [model_step(responsibility_class=reserved)]
        refuses(v2_plan([row]), f"model judgment claimed {reserved}")


def test_model_step_requires_typed_contracts_rationale_and_more_than_cost():
    accepted = v2_slice()
    accepted["design_contract"]["routing"]["executor_class"] = "model_assisted"
    accepted["design_contract"]["code_model_decision"]["model_judgment_steps"] = [model_step()]
    ep.validate_engineering_slice_plan(v2_plan([copy.deepcopy(accepted)]))
    for override in (
        {"input_contract_ref": ""}, {"output_contract_ref": ""}, {"rationale": "  "},
        {"selection_basis": ["cost"]}, {"selection_basis": []}, {"selection_basis": ["invented"]},
        {"step_ref": "step:never-declared"}, {"responsibility_class": "vibes"},
    ):
        row = v2_slice()
        row["design_contract"]["routing"]["executor_class"] = "model_assisted"
        row["design_contract"]["code_model_decision"]["model_judgment_steps"] = [model_step(**override)]
        refuses(v2_plan([row]), f"model step {override}")
    # Routing and the declared seams must agree in both directions.
    deterministic = v2_slice()
    deterministic["design_contract"]["code_model_decision"]["model_judgment_steps"] = [model_step()]
    refuses(v2_plan([deterministic]), "a deterministic_code route cannot carry model judgment")
    unstaffed = v2_slice(); unstaffed["design_contract"]["routing"]["executor_class"] = "model_assisted"
    refuses(v2_plan([unstaffed]), "a model_assisted route needs a typed model step")
    # Q029.D1: price alone never selects model work; a capability reason may.
    cost_only = v2_slice(); cost_only["design_contract"]["code_model_decision"]["selection_basis"] = ["cost"]
    refuses(v2_plan([cost_only]), "cost alone selected the code/model choice")
    measured = v2_slice(); measured["design_contract"]["code_model_decision"]["selection_basis"] = ["cost", "capability_gain"]
    ep.validate_engineering_slice_plan(v2_plan([measured]))


def test_full_depth_requires_the_full_envelope_and_short_stays_governed():
    short = v2_slice()
    ep.validate_engineering_slice_plan(v2_plan([copy.deepcopy(short)]))
    complex_row = v2_slice(risk_class="R4")
    assert ep.classify_design_depth(complex_row) == "full"
    ep.validate_engineering_slice_plan(v2_plan([copy.deepcopy(complex_row)]))
    smuggled = copy.deepcopy(complex_row)
    smuggled["design_contract"]["full_design_refs"] = None
    smuggled["design_contract"]["short_template"] = copy.deepcopy(short["design_contract"]["short_template"])
    refuses(v2_plan([smuggled]), "high-risk work took the shorter template")
    for field in ("design_interview_ref", "authority_envelope_ref", "failure_model_ref", "oracle_ref"):
        row = copy.deepcopy(complex_row); row["design_contract"]["full_design_refs"][field] = ""
        refuses(v2_plan([row]), f"full_design_refs {field}")
    no_fixtures = copy.deepcopy(complex_row); no_fixtures["design_contract"]["full_design_refs"]["fixture_refs"] = []
    refuses(v2_plan([no_fixtures]), "full depth requires fixtures")
    overreaching = copy.deepcopy(short)
    overreaching["design_contract"]["full_design_refs"] = copy.deepcopy(complex_row["design_contract"]["full_design_refs"])
    refuses(v2_plan([overreaching]), "the SHORT shape is exact")
    # SHORT is a shorter template, not a bypass: every other facet stays required.
    for field in ("review", "failure", "evidence", "deployment", "completion", "seam_decision", "tests"):
        row = copy.deepcopy(short); del row["design_contract"][field]
        refuses(v2_plan([row]), f"SHORT dropped {field}")
    ungoverned = copy.deepcopy(short); ungoverned["design_contract"]["review"]["independent_review_required"] = False
    refuses(v2_plan([ungoverned]), "SHORT waived independent review")
    # R2/R3 keep their canonical confirmation gate even when SHORT applies.
    material = v2_slice(risk_class="R3")
    assert ep.classify_design_depth(material) == "short"
    unconfirmed = copy.deepcopy(material); unconfirmed["design_contract"]["deployment"]["confirmation_required"] = False
    refuses(v2_plan([unconfirmed]), "R3 dropped its explicit confirmation gate")


def test_seam_decisions_refuse_duplicate_authority_and_half_replacement():
    ep.validate_engineering_slice_plan(v2_plan([v2_slice()]))
    unjustified = v2_slice()
    unjustified["design_contract"]["seam_decision"].update({"mode": "new_module", "new_module_justification": None})
    refuses(v2_plan([unjustified]), "a new module needs a real seam")
    invented = v2_slice()
    invented["design_contract"]["seam_decision"].update({"mode": "new_module", "new_module_justification": "convenience"})
    refuses(v2_plan([invented]), "convenience is not an accepted module justification")
    justified = v2_slice()
    justified["design_contract"]["seam_decision"].update({"mode": "new_module", "new_module_justification": "lifecycle"})
    ep.validate_engineering_slice_plan(v2_plan([justified]))
    unmeasured = v2_slice()
    unmeasured["design_contract"]["seam_decision"]["measurement"] = {"basis": "gut_feel", "note": "it felt simpler"}
    refuses(v2_plan([unmeasured]), "reuse/extend/replace must be measured")
    half = v2_slice()
    half["design_contract"]["seam_decision"].update({
        "mode": "replace", "replaced_seam_refs": ["seam:legacy"], "residual_authority_refs": ["seam:legacy-residual"]})
    refuses(v2_plan([half]), "a replacement that leaves residual authority is a half-fix")
    itself = v2_slice()
    itself["design_contract"]["seam_decision"].update({
        "mode": "replace", "replaced_seam_refs": ["seam:engineering-runtime"]})
    refuses(v2_plan([itself]), "a seam cannot replace itself")
    first = v2_slice("slice:one", 1)
    first["design_contract"]["seam_decision"].update({
        "mode": "new_module", "target_seam_ref": "seam:new-authority", "new_module_justification": "authority"})
    second = v2_slice("slice:two", 2)
    second["design_contract"]["seam_decision"].update({
        "mode": "replace", "target_seam_ref": "seam:new-authority", "replaced_seam_refs": ["seam:old-authority"]})
    refuses(v2_plan([first, second]), "two slices claimed one seam")
    retiring = v2_slice("slice:one", 1)
    retiring["design_contract"]["seam_decision"].update({
        "mode": "replace", "target_seam_ref": "seam:successor", "replaced_seam_refs": ["seam:engineering-runtime"]})
    extending = v2_slice("slice:two", 2)
    refuses(v2_plan([retiring, extending]), "one slice extended a seam another retires")


def test_v1_stays_exactly_compatible_and_unknown_versions_fail_explicitly():
    v1 = plan_fixture()
    assert ep.validate_engineering_slice_plan(v1)["schema_version"] == "engineering-slice-plan.v1"
    smuggled = copy.deepcopy(v1)
    smuggled["slices"][0]["design_contract"] = design_contract(smuggled["slices"][0])
    refuses(reseal(smuggled), "v1 remains closed against the successor field")
    v2 = v2_plan([v2_slice()])
    downgraded = copy.deepcopy(v2); downgraded["schema_version"] = "engineering-slice-plan.v1"
    refuses(reseal(downgraded), "a v2 slice is not silently reinterpreted as v1")
    for unknown in ("engineering-slice-plan.v3", "engineering-slice-plan", "", None):
        row = copy.deepcopy(v2); row["schema_version"] = unknown
        refuses(reseal(row), f"unknown schema_version {unknown!r}")
    assert ep.ENGINEERING_SLICE_PLAN_VERSIONS == ("engineering-slice-plan.v1", "engineering-slice-plan.v2")


def test_v2_plan_projects_through_the_existing_packet_and_passport_seams():
    plan = v2_plan([v2_slice()])
    packet = ep.build_engineering_slice_packet(ENVELOPE, plan, "slice:short")
    assert "design_contract" not in packet
    assert ep.validate_engineering_slice_packet(packet, plan, ENVELOPE)["packet_digest"] == packet["packet_digest"]
    row = receipt(plan, "slice:short")
    ep.validate_engineering_slice_receipt(row, plan, ENVELOPE)
    passport = ep.project_engineering_passport(plan, [row], execution_envelopes=[ENVELOPE])
    assert ep.validate_engineering_passport(passport)["closure_state"] == "blocked"


if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for fn in tests: fn(); print("ok", fn.__name__)
