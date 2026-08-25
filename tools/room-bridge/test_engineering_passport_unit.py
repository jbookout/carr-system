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



if __name__ == "__main__":
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for fn in tests: fn(); print("ok", fn.__name__)
