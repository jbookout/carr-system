"""Offline Engineering Passport v1 contracts and deterministic projection.

The passport is a typed projection over an already accepted Work Request,
Plan Revision, ExecutionEnvelope and redacted receipts.  It is deliberately
not a task store, scheduler, transcript archive, or authority source.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable

import execution_contract as base


class EngineeringContractError(base.ContractError):
    """An engineering passport artifact failed its fail-closed contract."""


ID = base.ID
DIGEST = base.SHA256


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EngineeringContractError(f"{label} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise EngineeringContractError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise EngineeringContractError(f"{label} is missing fields: {', '.join(missing)}")
    return value


def _str(value: Any, label: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or (identifier and not ID.fullmatch(value)):
        raise EngineeringContractError(f"{label} must be a non-empty {'opaque identifier' if identifier else 'string'}")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise EngineeringContractError(f"{label} must be a sha256 digest")
    return value


def _posint(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise EngineeringContractError(f"{label} must be a positive integer")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise EngineeringContractError(f"{label} must be boolean")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EngineeringContractError(f"{label} must be a list")
    return value


def _ids(value: Any, label: str) -> list[str]:
    rows = _list(value, label)
    for item in rows:
        _str(item, label, identifier=True)
    return rows


def _evidence(value: Any, label: str) -> list[dict[str, str]]:
    rows = _list(value, label)
    for raw in rows:
        row = _exact(raw, {"ref", "redaction_class", "content_digest"}, label + " item")
        _str(row["ref"], label + " ref", identifier=True)
        if row["redaction_class"] not in {"metadata_only", "redacted_evidence"}:
            raise EngineeringContractError(f"{label} redaction_class is invalid")
        _digest(row["content_digest"], label + " content_digest")
    return rows


def _binding(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"id", "state_version", "canonical_record_digest"}, label)
    _str(row["id"], label + " id", identifier=True)
    _posint(row["state_version"], label + " state_version")
    _digest(row["canonical_record_digest"], label + " canonical_record_digest")
    return row


def _plan_ref(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"id", "revision", "digest"}, label)
    _str(row["id"], label + " id", identifier=True)
    _posint(row["revision"], label + " revision")
    _digest(row["digest"], label + " digest")
    return row


SLICE_FIELDS = {
    "slice_ref", "ordinal", "objective", "definition_of_done", "dependency_refs",
    "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs",
    "baseline_evidence_refs", "planned_checks", "scope_boundary", "forbidden_change_refs",
    "concurrency_posture", "manual_qa_required", "risk_class", "release_requirement",
}
CHECK_FIELDS = {"check_ref", "failure_condition", "evidence_requirement"}


def _validate_check(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, CHECK_FIELDS, label)
    _str(row["check_ref"], label + " check_ref", identifier=True)
    _str(row["failure_condition"], label + " failure_condition")
    if row["evidence_requirement"] not in {"redacted_evidence_required", "metadata_only_sufficient"}:
        raise EngineeringContractError(f"{label} evidence_requirement is invalid")
    return row


def _validate_slice(value: Any, label: str = "engineering slice") -> dict[str, Any]:
    row = _exact(value, SLICE_FIELDS, label)
    _str(row["slice_ref"], label + " slice_ref", identifier=True)
    _posint(row["ordinal"], label + " ordinal")
    for field in ("objective", "definition_of_done", "scope_boundary"):
        _str(row[field], label + " " + field)
    for field in ("dependency_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "forbidden_change_refs"):
        _ids(row[field], label + " " + field)
    _evidence(row["baseline_evidence_refs"], label + " baseline_evidence_refs")
    checks = _list(row["planned_checks"], label + " planned_checks")
    if not checks:
        raise EngineeringContractError(f"{label} must plan at least one check before execution")
    for index, check in enumerate(checks):
        _validate_check(check, f"{label} planned_checks[{index}]")
    if row["concurrency_posture"] not in {"parallel_safe", "serial_after_dependencies", "exclusive_resource"}:
        raise EngineeringContractError(f"{label} concurrency_posture is invalid")
    _bool(row["manual_qa_required"], label + " manual_qa_required")
    if row["risk_class"] not in {f"R{i}" for i in range(7)}:
        raise EngineeringContractError(f"{label} risk_class is invalid")
    if row["release_requirement"] not in {"required", "not_required"}:
        raise EngineeringContractError(f"{label} release_requirement is invalid")
    return row


def validate_engineering_slice_plan(plan: Any) -> dict[str, Any]:
    fields = {"schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices"}
    value = _exact(plan, fields, "engineering slice plan")
    if value["schema_version"] != "engineering-slice-plan.v1":
        raise EngineeringContractError("unsupported engineering slice plan schema_version")
    _binding(value["work_request"], "engineering slice plan work_request")
    _plan_ref(value["accepted_plan_revision"], "engineering slice plan accepted_plan_revision")
    _digest(value["plan_digest"], "engineering slice plan plan_digest")
    slices = _list(value["slices"], "engineering slice plan slices")
    if not slices:
        raise EngineeringContractError("engineering slice plan slices cannot be empty")
    refs: set[str] = set()
    ordinals: set[int] = set()
    for index, raw in enumerate(slices):
        row = _validate_slice(raw, f"engineering slice plan slices[{index}]")
        if row["slice_ref"] in refs or row["ordinal"] in ordinals:
            raise EngineeringContractError("engineering slice plan has duplicate slice refs or ordinals")
        refs.add(row["slice_ref"]); ordinals.add(row["ordinal"])
    for row in slices:
        missing = set(row["dependency_refs"]) - refs
        if missing:
            raise EngineeringContractError(f"slice {row['slice_ref']} has missing dependencies: {', '.join(sorted(missing))}")
    _assert_acyclic(slices)
    without_digest = {key: item for key, item in value.items() if key != "plan_digest"}
    if value["plan_digest"] != base.canonical_digest(without_digest):
        raise EngineeringContractError("engineering slice plan digest does not bind exact content")
    return value


def _assert_acyclic(slices: Iterable[dict[str, Any]]) -> None:
    graph = {row["slice_ref"]: set(row["dependency_refs"]) for row in slices}
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(ref: str) -> None:
        if ref in visiting:
            raise EngineeringContractError("engineering slice dependencies contain a cycle")
        if ref in visited:
            return
        visiting.add(ref)
        for dep in graph[ref]: visit(dep)
        visiting.remove(ref); visited.add(ref)
    for ref in graph: visit(ref)


RECEIPT_FIELDS = {
    "schema_version", "envelope_digest", "attempt_id", "slice_ref", "plan_digest", "attribution",
    "planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs",
    "checks", "outcome", "artifact_refs", "evidence_refs", "deviations", "source_evidence",
    "reset_reconstruction", "executor_claim", "independent_verification_required",
}


def _validate_receipt(value: Any, plan: dict[str, Any], envelope: dict[str, Any] | None) -> dict[str, Any]:
    row = _exact(value, RECEIPT_FIELDS, "engineering slice receipt")
    if row["schema_version"] != "engineering-slice-receipt.v1":
        raise EngineeringContractError("unsupported engineering slice receipt schema_version")
    _digest(row["envelope_digest"], "engineering receipt envelope_digest")
    _str(row["attempt_id"], "engineering receipt attempt_id", identifier=True)
    _str(row["slice_ref"], "engineering receipt slice_ref", identifier=True)
    if row["plan_digest"] != plan["plan_digest"]:
        raise EngineeringContractError("engineering receipt plan_digest does not match accepted plan")
    slices = {item["slice_ref"]: item for item in plan["slices"]}
    if row["slice_ref"] not in slices:
        raise EngineeringContractError("engineering receipt names an unknown slice")
    attribution = _exact(row["attribution"], {"actor_ref", "session_ref", "adapter_ref"}, "engineering receipt attribution")
    for field in attribution: _str(attribution[field], "engineering receipt attribution " + field, identifier=True)
    for field in ("planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "artifact_refs"):
        _ids(row[field], "engineering receipt " + field)
    checks = _list(row["checks"], "engineering receipt checks")
    planned = {check["check_ref"] for check in slices[row["slice_ref"]]["planned_checks"]}
    seen: set[str] = set()
    for index, raw in enumerate(checks):
        item = _exact(raw, {"check_ref", "state", "evidence_refs"}, f"engineering receipt checks[{index}]")
        _str(item["check_ref"], "engineering receipt check_ref", identifier=True)
        if item["check_ref"] not in planned: raise EngineeringContractError("receipt ran an unplanned check")
        if item["check_ref"] in seen: raise EngineeringContractError("receipt duplicates a check")
        seen.add(item["check_ref"])
        if item["state"] not in {"passed", "failed", "blocked", "not_run"}:
            raise EngineeringContractError("engineering receipt check state is invalid")
        _evidence(item["evidence_refs"], "engineering receipt check evidence_refs")
    if planned - seen:
        raise EngineeringContractError("receipt omits a planned check")
    if row["outcome"] not in {"claimed_complete", "failed", "blocked", "reopened"}:
        raise EngineeringContractError("engineering receipt outcome is invalid")
    if row["outcome"] == "claimed_complete" and (any(item["state"] != "passed" for item in checks) or not row["artifact_refs"] or not row["evidence_refs"]):
        raise EngineeringContractError("claimed completion requires passing checks, artifacts, and evidence")
    _evidence(row["evidence_refs"], "engineering receipt evidence_refs")
    deviations = _list(row["deviations"], "engineering receipt deviations")
    deviation_refs: set[str] = set()
    for index, raw in enumerate(deviations):
        item = _exact(raw, {"deviation_ref", "category", "reason", "impact", "plan_revision_required", "evidence_refs"}, f"engineering receipt deviations[{index}]")
        _str(item["deviation_ref"], "deviation_ref", identifier=True)
        if item["deviation_ref"] in deviation_refs: raise EngineeringContractError("receipt duplicates a deviation")
        deviation_refs.add(item["deviation_ref"])
        for field in ("category", "reason", "impact"): _str(item[field], "deviation " + field)
        _bool(item["plan_revision_required"], "deviation plan_revision_required")
        _evidence(item["evidence_refs"], "deviation evidence_refs")
    source = _exact(row["source_evidence"], {"worktree_ref", "branch_ref", "source_sha", "evidence_refs"}, "engineering receipt source_evidence")
    for field in ("worktree_ref", "branch_ref", "source_sha"): _str(source[field], "source_evidence " + field, identifier=(field != "source_sha"))
    _evidence(source["evidence_refs"], "source_evidence evidence_refs")
    reset = _exact(row["reset_reconstruction"], {"fresh_session", "inherited_transcript_used", "reconstruction_free", "remediation_action"}, "engineering receipt reset_reconstruction")
    for field in ("fresh_session", "inherited_transcript_used", "reconstruction_free"): _bool(reset[field], "reset_reconstruction " + field)
    if reset["inherited_transcript_used"] or not reset["fresh_session"]: raise EngineeringContractError("engineering slice execution requires a fresh native session")
    if not reset["reconstruction_free"] and not reset["remediation_action"]: raise EngineeringContractError("failed reconstruction-free metric requires remediation")
    _str(reset["remediation_action"], "reset_reconstruction remediation_action") if not reset["reconstruction_free"] else (None if reset["remediation_action"] is None else _str(reset["remediation_action"], "reset_reconstruction remediation_action"))
    claim = _exact(row["executor_claim"], {"claim_state", "claimed_by", "claimed_at"}, "engineering receipt executor_claim")
    if claim["claim_state"] != "executor_claim": raise EngineeringContractError("executor claim state is invalid")
    _str(claim["claimed_by"], "executor claim claimed_by", identifier=True); _str(claim["claimed_at"], "executor claim claimed_at")
    if row["independent_verification_required"] is not True: raise EngineeringContractError("engineering receipt requires independent verification")
    if envelope is not None:
        bound = base.validate_execution_envelope(envelope)
        if row["envelope_digest"] != base.execution_envelope_digest(bound): raise EngineeringContractError("receipt does not bind exact execution envelope")
    return row


def validate_engineering_slice_receipt(receipt: Any, plan: Any, envelope: Any | None = None) -> dict[str, Any]:
    return _validate_receipt(receipt, validate_engineering_slice_plan(plan), envelope)


def build_engineering_slice_packet(envelope: Any, plan: Any, slice_ref: str) -> dict[str, Any]:
    bound = base.validate_execution_envelope(envelope)
    accepted = validate_engineering_slice_plan(plan)
    row = next((item for item in accepted["slices"] if item["slice_ref"] == slice_ref), None)
    if row is None: raise EngineeringContractError("cannot build packet for unknown slice")
    packet_envelope = copy.deepcopy(bound)
    expected = packet_envelope["request"]["declared_expectations"]
    packet_envelope["request"]["declared_expectations"] = {
        "plan_step_refs": sorted(set(expected["plan_step_refs"]) & set(row["declared_plan_step_refs"])),
        "component_refs": sorted(set(expected["component_refs"]) & set(row["declared_component_refs"])),
        "component_dependencies": [edge for edge in expected["component_dependencies"] if edge["component_ref"] in row["declared_component_refs"]],
        "resource_refs": sorted(set(expected["resource_refs"]) & set(row["declared_resource_refs"])),
    }
    packet_envelope["phase_binding"]["session_affinity"] = "fresh_native_session_required"
    packet = {
        "schema_version": "engineering-slice-packet.v1", "slice_ref": slice_ref,
        "plan_digest": accepted["plan_digest"], "envelope_digest": base.execution_envelope_digest(bound),
        "fresh_native_session_required": True, "objective": row["objective"],
        "definition_of_done": row["definition_of_done"], "planned_checks": row["planned_checks"],
        "scope_boundary": row["scope_boundary"], "forbidden_change_refs": row["forbidden_change_refs"],
        "envelope": packet_envelope,
    }
    packet["packet_digest"] = base.canonical_digest({key: item for key, item in packet.items() if key != "packet_digest"})
    return packet


def validate_engineering_slice_packet(packet: Any, plan: Any, source_envelope: Any) -> dict[str, Any]:
    row = _exact(packet, {"schema_version", "slice_ref", "plan_digest", "envelope_digest", "fresh_native_session_required", "objective", "definition_of_done", "planned_checks", "scope_boundary", "forbidden_change_refs", "envelope", "packet_digest"}, "engineering slice packet")
    if row["schema_version"] != "engineering-slice-packet.v1" or row["fresh_native_session_required"] is not True:
        raise EngineeringContractError("engineering slice packet must require a fresh native session")
    accepted = validate_engineering_slice_plan(plan); source = base.validate_execution_envelope(source_envelope)
    _str(row["slice_ref"], "engineering packet slice_ref", identifier=True); _digest(row["plan_digest"], "engineering packet plan_digest"); _digest(row["envelope_digest"], "engineering packet envelope_digest"); _digest(row["packet_digest"], "engineering packet packet_digest")
    if row["plan_digest"] != accepted["plan_digest"] or row["envelope_digest"] != base.execution_envelope_digest(source): raise EngineeringContractError("engineering packet source binding does not match")
    expected = next((item for item in accepted["slices"] if item["slice_ref"] == row["slice_ref"]), None)
    if expected is None: raise EngineeringContractError("engineering packet names an unknown slice")
    for field in ("objective", "definition_of_done", "scope_boundary", "forbidden_change_refs"):
        if row[field] != expected[field]: raise EngineeringContractError("engineering packet content does not match accepted slice")
    _list(row["planned_checks"], "engineering packet planned_checks")
    envelope = base.validate_execution_envelope(row["envelope"])
    if envelope["server_binding"] != source["server_binding"] or envelope["state_binding"] != source["state_binding"] or envelope["plan_revision"] != source["plan_revision"]:
        raise EngineeringContractError("engineering packet changed server authority, state, or plan binding")
    if envelope["phase_binding"]["session_affinity"] != "fresh_native_session_required": raise EngineeringContractError("engineering packet lost fresh-session requirement")
    without = {key: item for key, item in row.items() if key != "packet_digest"}
    if row["packet_digest"] != base.canonical_digest(without): raise EngineeringContractError("engineering packet digest does not bind exact content")
    return row


def eligible_slices(plan: Any, receipts: Iterable[Any] = (), independent_verified_refs: Iterable[str] = ()) -> list[str]:
    accepted = validate_engineering_slice_plan(plan)
    receipts_by_slice = {row["slice_ref"]: row for row in (_validate_receipt(item, accepted, None) for item in receipts)}
    # An executor receipt is never verification.  The caller must supply the
    # fresh verifier's typed refs explicitly; this keeps dependency eligibility
    # from silently granting a maker promotion.
    verified = set(independent_verified_refs)
    result = []
    for row in sorted(accepted["slices"], key=lambda item: item["ordinal"]):
        if row["slice_ref"] not in receipts_by_slice and set(row["dependency_refs"]).issubset(verified): result.append(row["slice_ref"])
    return result


def _disposition(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    row = _exact(value, {"state", "evidence_refs", "note"}, field)
    if row["state"] not in allowed: raise EngineeringContractError(f"{field} state is invalid")
    _evidence(row["evidence_refs"], field + " evidence_refs")
    _str(row["note"], field + " note")
    if row["state"] in {"resolved", "complete", "passed", "released", "proposed", "rejected", "nothing_durable"} and not row["evidence_refs"] and row["state"] not in {"nothing_durable", "rejected"}:
        raise EngineeringContractError(f"{field} resolved state requires evidence")
    return row


def route_learning_disposition(kind: str) -> str:
    allowed = {"regression_test", "gate_or_validator", "decision_record", "skill_or_workflow", "memory_or_rule_candidate", "incident_finding", "speculative_finding", "nothing_durable"}
    if kind not in allowed: raise EngineeringContractError("learning disposition route is invalid")
    return kind


def project_engineering_passport(plan: Any, receipts: Iterable[Any], *, reviewer_facts: list[dict[str, Any]] | None = None, qa_facts: list[dict[str, Any]] | None = None, explanation: dict[str, Any] | None = None, release: dict[str, Any] | None = None, learning: dict[str, Any] | None = None, stale_conflict: dict[str, Any] | None = None) -> dict[str, Any]:
    accepted = validate_engineering_slice_plan(plan)
    receipt_rows = [_validate_receipt(item, accepted, None) for item in receipts]
    if len({row["slice_ref"] for row in receipt_rows}) != len(receipt_rows): raise EngineeringContractError("passport cannot select duplicate receipt for a slice")
    reviewer_facts = reviewer_facts or []; qa_facts = qa_facts or []
    for fact in reviewer_facts:
        _exact(fact, {"slice_ref", "reviewer_ref", "session_ref", "state", "evidence_refs", "is_independent"}, "reviewer fact")
        if fact["state"] not in {"passed", "failed", "blocked"} or not fact["is_independent"]: raise EngineeringContractError("review must be fresh and independent")
        matching_receipt = next((item for item in receipt_rows if item["slice_ref"] == fact["slice_ref"]), None)
        if matching_receipt and fact["reviewer_ref"] == matching_receipt["attribution"]["actor_ref"]:
            raise EngineeringContractError("executor cannot self-verify")
        _evidence(fact["evidence_refs"], "reviewer fact evidence_refs")
    for fact in qa_facts:
        _exact(fact, {"slice_ref", "state", "evidence_refs", "note"}, "manual QA fact")
        if fact["state"] not in {"passed", "failed", "blocked"}: raise EngineeringContractError("manual QA state is invalid")
        _evidence(fact["evidence_refs"], "manual QA evidence_refs")
    reviewer_by = {fact["slice_ref"]: fact for fact in reviewer_facts}; qa_by = {fact["slice_ref"]: fact for fact in qa_facts}
    slices = []
    for row in sorted(accepted["slices"], key=lambda item: item["ordinal"]):
        receipt = next((item for item in receipt_rows if item["slice_ref"] == row["slice_ref"]), None)
        review = reviewer_by.get(row["slice_ref"]); qa = qa_by.get(row["slice_ref"])
        if receipt is None: state = "eligible" if row["slice_ref"] in eligible_slices(accepted, receipt_rows) else "blocked"
        elif review and review["state"] == "passed" and (not row["manual_qa_required"] or (qa and qa["state"] == "passed")): state = "verified_complete"
        elif receipt["outcome"] in {"failed", "reopened"} or (qa and qa["state"] == "failed"): state = "reopened"
        else: state = "claimed"
        slices.append({"slice_ref": row["slice_ref"], "ordinal": row["ordinal"], "dependency_refs": row["dependency_refs"], "state": state, "planned_check_refs": [check["check_ref"] for check in row["planned_checks"]], "deviation_refs": [item["deviation_ref"] for item in (receipt or {}).get("deviations", [])], "manual_qa_required": row["manual_qa_required"], "release_requirement": row["release_requirement"]})
    unresolved_deviations = any(item["deviations"] and any(deviation["plan_revision_required"] for deviation in item["deviations"]) for item in receipt_rows)
    closure = {
        "work": _disposition((explanation or {}).get("work", {"state": "unresolved", "evidence_refs": [], "note": "work disposition pending"}), "work disposition", {"unresolved", "complete"}),
        "proof": _disposition((explanation or {}).get("proof", {"state": "unresolved", "evidence_refs": [], "note": "proof disposition pending"}), "proof disposition", {"unresolved", "complete"}),
        "explanation": _disposition((explanation or {}).get("explanation", {"state": "unresolved", "evidence_refs": [], "note": "operator explanation pending"}), "explanation disposition", {"unresolved", "complete"}),
        "release": _disposition(release or {"state": "unresolved", "evidence_refs": [], "note": "release disposition pending"}, "release disposition", {"unresolved", "released", "not_required"}),
        "learning": _disposition(learning or {"state": "unresolved", "evidence_refs": [], "note": "learning disposition pending"}, "learning disposition", {"unresolved", "proposed", "rejected", "nothing_durable"}),
    }
    all_verified = all(item["state"] == "verified_complete" for item in slices) and bool(slices)
    release_ok = closure["release"]["state"] == "released" or (closure["release"]["state"] == "not_required" and all(item["release_requirement"] == "not_required" for item in slices))
    closure_complete = all_verified and not unresolved_deviations and all(item["state"] != "unresolved" for item in closure.values()) and closure["proof"]["state"] == "complete" and release_ok
    operator_evidence = {ev["ref"]: ev for rec in receipt_rows for ev in rec["evidence_refs"]}
    projection = {"schema_version": "engineering-passport.v1", "work_request": accepted["work_request"], "accepted_plan_revision": accepted["accepted_plan_revision"], "plan_digest": accepted["plan_digest"], "slices": slices, "operator_receipt": {"what_changed": [item["slice_ref"] for item in slices if item["state"] == "verified_complete"], "why": "derived from accepted plan and typed receipts", "evidence_refs": [operator_evidence[key] for key in sorted(operator_evidence)], "deviations": sorted({dev["deviation_ref"] for rec in receipt_rows for dev in rec["deviations"]}), "remaining_risk": [item["slice_ref"] for item in slices if item["state"] != "verified_complete"], "manual_qa_items": [item["slice_ref"] for item in slices if item["manual_qa_required"] and item["slice_ref"] not in qa_by]}, "closure": closure, "closure_state": "complete" if closure_complete else "blocked", "stale_conflict": stale_conflict or {"state": "none", "reason": None}}
    projection["projection_digest"] = base.canonical_digest(projection)
    return projection


def validate_engineering_passport(value: Any) -> dict[str, Any]:
    fields = {"schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices", "operator_receipt", "closure", "closure_state", "stale_conflict", "projection_digest"}
    row = _exact(value, fields, "engineering passport")
    if row["schema_version"] != "engineering-passport.v1": raise EngineeringContractError("unsupported engineering passport schema_version")
    _binding(row["work_request"], "engineering passport work_request"); _plan_ref(row["accepted_plan_revision"], "engineering passport accepted_plan_revision"); _digest(row["plan_digest"], "engineering passport plan_digest"); _digest(row["projection_digest"], "engineering passport projection_digest")
    if row["closure_state"] not in {"blocked", "complete"}: raise EngineeringContractError("engineering passport closure_state is invalid")
    _list(row["slices"], "engineering passport slices")
    operator = _exact(row["operator_receipt"], {"what_changed", "why", "evidence_refs", "deviations", "remaining_risk", "manual_qa_items"}, "engineering passport operator_receipt")
    _ids(operator["what_changed"], "operator what_changed"); _str(operator["why"], "operator why"); _ids(operator["deviations"], "operator deviations"); _ids(operator["remaining_risk"], "operator remaining_risk"); _ids(operator["manual_qa_items"], "operator manual_qa_items"); _evidence(operator["evidence_refs"], "operator evidence_refs")
    closure = _exact(row["closure"], {"work", "proof", "explanation", "release", "learning"}, "engineering passport closure")
    for field, allowed in (("work", {"unresolved", "complete"}), ("proof", {"unresolved", "complete"}), ("explanation", {"unresolved", "complete"}), ("release", {"unresolved", "released", "not_required"}), ("learning", {"unresolved", "proposed", "rejected", "nothing_durable"})):
        _disposition(closure[field], field, allowed)
    conflict = _exact(row["stale_conflict"], {"state", "reason"}, "stale_conflict")
    if conflict["state"] not in {"none", "stale", "conflict", "uncertain"}: raise EngineeringContractError("stale_conflict state is invalid")
    if conflict["state"] == "none" and conflict["reason"] is not None: raise EngineeringContractError("none stale_conflict cannot carry a reason")
    if conflict["state"] != "none": _str(conflict["reason"], "stale_conflict reason")
    without = {key: item for key, item in row.items() if key != "projection_digest"}
    if row["projection_digest"] != base.canonical_digest(without): raise EngineeringContractError("engineering passport digest does not bind exact content")
    return row


def engineering_passport_wire(payload: Any) -> dict[str, Any]:
    return {"job_passport": {"schema_version": "job-passport-wire.v1", "kind": "engineering_passport", "payload": validate_engineering_passport(payload)}}
