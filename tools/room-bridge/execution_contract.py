"""Strict, offline v1 contracts for CARR's portable Job Passport.

This is intentionally an admission/receipt seam, not a work-request store or
an authority engine.  A real server issues ``server_binding`` after identity
and capability resolution.  Callers may submit only ``validate_client_job_request``;
they cannot select identity, environment, authority, provider, model, or a
personal-brain scope.  The compatibility adapter uses these helpers without
changing room-bridge's existing plain-task dispatch path.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


class ContractError(ValueError):
    """A portable execution artifact failed its closed v1 contract."""


SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

ENVELOPE_FIELDS = {
    "schema_version", "envelope_id", "work_request_id", "plan_revision", "agent_session",
    "issued_at", "expires_at", "state_binding", "phase_binding", "evaluation_context", "request",
    "server_binding", "handoff",
}
REQUEST_FIELDS = {"job_ref", "input_digest", "data_class", "allowed_actions", "declared_expectations"}
CLIENT_REQUEST_FIELDS = {"job_ref", "input_digest", "data_class"}
DECLARED_EXPECTATION_FIELDS = {"plan_step_refs", "component_refs", "component_dependencies", "resource_refs"}
COMPONENT_DEPENDENCY_FIELDS = {"component_ref", "depends_on_component_ref"}
PLAN_FIELDS = {"id", "revision", "digest"}
SESSION_FIELDS = {"id", "lease_expires_at"}
SERVER_BINDING_FIELDS = {"identity", "authority", "adapter"}
IDENTITY_FIELDS = {
    "organization_tenant_id", "sponsoring_human_id", "agent_principal_id", "runtime_principal",
    "personal_brain_scope", "personal_brain_version", "personal_rule_count", "derived_by",
    "client_mutable",
}
AUTHORITY_FIELDS = {
    "environment", "risk_class", "capability_profile", "capability_grant_ref", "read_only",
    "derived_by", "client_mutable",
}
ADAPTER_FIELDS = {
    "surface", "adapter_id", "adapter_version", "harness_id", "harness_version", "provider_id",
    "model_id", "native_session_ref", "configuration_fingerprint",
}
HANDOFF_FIELDS = {"mode", "replaces_agent_session_id", "capability_inherited"}
STATE_BINDING_FIELDS = {"state_version", "canonical_record_digest", "accepted_resource_revisions", "compare_and_swap_required"}
RESOURCE_REVISION_FIELDS = {"resource_ref", "revision_ref", "digest"}
PHASE_BINDING_FIELDS = {"phase_id", "session_affinity", "switch_conditions", "native_session_transfer"}
EVALUATION_CONTEXT_FIELDS = {"experiment_arm", "auditor_mode"}

RECEIPT_FIELDS = {
    "schema_version", "attempt_id", "envelope_digest", "attempt_ordinal", "adapter", "lifecycle",
    "result", "attestation", "negative_knowledge", "telemetry", "tool_event_summaries", "observation",
    "interventions", "handoff_proposal",
}
LIFECYCLE_FIELDS = {"started_at", "ended_at", "state", "retry_count", "recovery_count", "failure_class"}
RESULT_FIELDS = {"job_ref", "outcome", "verification_state", "artifact_refs", "evidence_refs", "validation_results"}
TELEMETRY_FIELDS = {"latency_ms", "cost_usd", "usage", "reset_tax"}
USAGE_FIELDS = {"input_tokens", "output_tokens", "cached_input_tokens"}
RESET_TAX_FIELDS = {"context_reconstruction_ms", "duplicated_tool_calls", "repeated_failed_approach_count", "human_correction_count", "switch_overhead_ms"}
TOOL_SUMMARY_FIELDS = {"tool_name", "result_class", "duration_ms"}
INTERVENTION_FIELDS = {"kind", "occurred_at", "summary"}
HANDOFF_PROPOSAL_FIELDS = {"proposed", "reason", "replacement_session_ref", "checkpoint_ref", "requires_independent_verification"}
VALIDATION_RESULT_FIELDS = {"check_id", "state", "evidence_refs"}
ATTESTATION_FIELDS = {"claim_state", "canonical_promotion_state", "independent_evidence_required"}
NEGATIVE_KNOWLEDGE_FIELDS = {"approach_ref", "evidence_refs", "applicability", "revalidate_after", "expires_at"}
OBSERVATION_FIELDS = {
    "progress_state", "coverage_state", "activity_fidelity", "declared_refs_observed",
    "unmapped_activity_refs", "deviation_candidates", "uncertainty",
}
DEVIATION_CANDIDATE_FIELDS = {"candidate_id", "basis", "confidence", "requires_review"}
PROGRESS_EVENT_FIELDS = {
    "schema_version", "attempt_id", "sequence", "occurred_at", "event_type", "declared_step_ref",
    "observed_resource_ref", "observed_component_ref", "tool_class", "state", "correlation_id",
    "causation_id", "redaction_class", "evidence_refs", "retention",
}
PROFILE_FIELDS = {"profile_id", "display_label"}
OBSERVATORY_PROJECTION_FIELDS = {
    "schema_version", "work_request_id", "declared_intent", "component_map", "attempt_lane",
    "state", "observed_movement", "timeline", "evidence_refs",
}

TERMINAL_STATES = {"succeeded", "failed", "timed_out", "cancelled", "partial", "unknown"}
OUTCOMES = {"success", "failure", "timeout", "cancellation", "partial", "unknown"}
VERIFICATION_STATES = {"verified_success", "verified_failure", "partial", "unknown", "not_attempted"}
SURFACES = {"claude_desktop", "codex_desktop", "hermes_desktop", "grok_x_native"}

# A request arrives without identity or routing selectors. These values are rejected
# even if a future caller adds them to the request schema by mistake.
FORBIDDEN_CLIENT_SELECTORS = {
    "organization_tenant_id", "tenant_id", "sponsoring_human_id", "agent_principal_id",
    "runtime_principal", "environment", "risk_class", "capability_profile", "capability_grant_ref",
    "personal_brain_scope", "personal_brain_version", "provider_id", "model_id", "adapter_id",
    "harness_id", "surface", "configuration_fingerprint", "phase_id", "session_affinity",
}


def _expect_exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContractError(f"{label} is missing fields: {', '.join(missing)}")
    return value


def _string(value: Any, label: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    if identifier and not ID.fullmatch(value):
        raise ContractError(f"{label} must be an opaque identifier")
    return value


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ContractError(f"{label} must be an ISO UTC timestamp")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ContractError(f"{label} must be a sha256 digest")
    return value


def _list_of_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ContractError(f"{label} must be a list of non-empty strings")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContractError(f"{label} must be a non-negative integer")
    return value


def _finite_nonnegative(value: Any, label: str) -> float | int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ContractError(f"{label} must be a finite non-negative number")
    return value


def canonical_digest(value: Any) -> str:
    """Return a stable digest independent of JSON key ordering or whitespace."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("canonical digest input must be JSON serializable") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_client_job_request(request: Any) -> dict[str, Any]:
    """Validate the only request-provided portion of a future Job Passport."""
    if not isinstance(request, dict):
        raise ContractError("client job request must be an object")
    forbidden = sorted(set(request) & FORBIDDEN_CLIENT_SELECTORS)
    if forbidden:
        raise ContractError(f"client job request refuses client-selected {', '.join(forbidden)}")
    unknown = sorted(set(request) - CLIENT_REQUEST_FIELDS)
    if unknown:
        raise ContractError(f"client job request has unknown fields: {', '.join(unknown)}")
    required = {"job_ref", "input_digest"}
    missing = sorted(required - set(request))
    if missing:
        raise ContractError(f"client job request is missing fields: {', '.join(missing)}")
    result = dict(request)
    _string(result["job_ref"], "client job request job_ref", identifier=True)
    _digest(result["input_digest"], "client job request input_digest")
    if "data_class" in result and result["data_class"] != "synthetic_only":
        raise ContractError("client job request data_class must be synthetic_only in v1")
    result.setdefault("data_class", "synthetic_only")
    return result


def _validate_request(value: Any) -> dict[str, Any]:
    request = _expect_exact(value, REQUEST_FIELDS, "envelope request")
    _string(request["job_ref"], "envelope request job_ref", identifier=True)
    _digest(request["input_digest"], "envelope request input_digest")
    if request["data_class"] not in {"synthetic_only", "metadata_only"}:
        raise ContractError("envelope request data_class is not supported")
    if request["allowed_actions"] != []:
        raise ContractError("execution envelope v1 is read-only and cannot authorize actions")
    declared = _expect_exact(request["declared_expectations"], DECLARED_EXPECTATION_FIELDS,
                              "envelope declared_expectations")
    for field in DECLARED_EXPECTATION_FIELDS - {"component_dependencies"}:
        _list_of_strings(declared[field], f"envelope declared_expectations {field}")
    if not isinstance(declared["component_dependencies"], list):
        raise ContractError("envelope declared_expectations component_dependencies must be a list")
    for edge in declared["component_dependencies"]:
        pair = _expect_exact(edge, COMPONENT_DEPENDENCY_FIELDS, "envelope component dependency")
        for field in COMPONENT_DEPENDENCY_FIELDS:
            _string(pair[field], f"envelope component dependency {field}")
    return request


def _validate_adapter(value: Any) -> dict[str, Any]:
    adapter = _expect_exact(value, ADAPTER_FIELDS, "adapter")
    if adapter["surface"] not in SURFACES:
        raise ContractError("adapter surface is not a supported v1 surface")
    for field in ADAPTER_FIELDS - {"surface", "configuration_fingerprint"}:
        _string(adapter[field], f"adapter {field}", identifier=(field == "native_session_ref"))
    _digest(adapter["configuration_fingerprint"], "adapter configuration_fingerprint")
    return adapter


def _validate_server_binding(value: Any) -> dict[str, Any]:
    binding = _expect_exact(value, SERVER_BINDING_FIELDS, "server_binding")
    identity = _expect_exact(binding["identity"], IDENTITY_FIELDS, "server_binding identity")
    for field in IDENTITY_FIELDS - {"personal_rule_count", "derived_by", "client_mutable"}:
        _string(identity[field], f"server_binding identity {field}", identifier=True)
    _nonnegative_int(identity["personal_rule_count"], "server_binding identity personal_rule_count")
    if identity["derived_by"] != "server_identity_resolution" or identity["client_mutable"] is not False:
        raise ContractError("identity must be server-derived and client immutable")
    authority = _expect_exact(binding["authority"], AUTHORITY_FIELDS, "server_binding authority")
    if authority["environment"] not in {"local", "rehearsal", "staging", "production"}:
        raise ContractError("server_binding authority environment is invalid")
    if authority["risk_class"] not in {f"R{i}" for i in range(7)}:
        raise ContractError("server_binding authority risk_class is invalid")
    for field in ("capability_profile", "capability_grant_ref"):
        _string(authority[field], f"server_binding authority {field}", identifier=True)
    if authority["read_only"] is not True:
        raise ContractError("execution envelope v1 only accepts a server-derived read-only authority")
    if authority["derived_by"] != "server_capability_resolution" or authority["client_mutable"] is not False:
        raise ContractError("authority must be server-derived and client immutable")
    _validate_adapter(binding["adapter"])
    return binding


def _validate_state_binding(value: Any) -> dict[str, Any]:
    binding = _expect_exact(value, STATE_BINDING_FIELDS, "state_binding")
    if not isinstance(binding["state_version"], int) or isinstance(binding["state_version"], bool) or binding["state_version"] < 1:
        raise ContractError("state_binding state_version must be a positive integer")
    _digest(binding["canonical_record_digest"], "state_binding canonical_record_digest")
    if binding["compare_and_swap_required"] is not True:
        raise ContractError("state_binding requires compare_and_swap_required")
    if not isinstance(binding["accepted_resource_revisions"], list):
        raise ContractError("state_binding accepted_resource_revisions must be a list")
    for row in binding["accepted_resource_revisions"]:
        resource = _expect_exact(row, RESOURCE_REVISION_FIELDS, "accepted resource revision")
        _string(resource["resource_ref"], "accepted resource revision resource_ref", identifier=True)
        _string(resource["revision_ref"], "accepted resource revision revision_ref", identifier=True)
        _digest(resource["digest"], "accepted resource revision digest")
    return binding


def _validate_phase_binding(value: Any) -> dict[str, Any]:
    phase = _expect_exact(value, PHASE_BINDING_FIELDS, "phase_binding")
    _string(phase["phase_id"], "phase_binding phase_id", identifier=True)
    if phase["session_affinity"] not in {"same_native_session_required", "same_native_session_preferred", "fresh_native_session_required"}:
        raise ContractError("phase_binding session_affinity is invalid")
    if not isinstance(phase["switch_conditions"], list) or not phase["switch_conditions"]:
        raise ContractError("phase_binding switch_conditions must be a non-empty list")
    allowed = {"verified_checkpoint", "native_session_unavailable", "phase_boundary", "capability_expired"}
    if not set(phase["switch_conditions"]).issubset(allowed) or "verified_checkpoint" not in phase["switch_conditions"]:
        raise ContractError("phase_binding only permits a switch at a verified_checkpoint")
    if phase["native_session_transfer"] != "semantic_state_only":
        raise ContractError("phase_binding cannot promise portable native session internals")
    return phase


def _validate_evaluation_context(value: Any) -> dict[str, Any]:
    context = _expect_exact(value, EVALUATION_CONTEXT_FIELDS, "evaluation_context")
    if context["experiment_arm"] not in {
        "fixed_native_pair", "same_pair_audited_state", "audited_state_routed_executors", "diverse_auditor",
    }:
        raise ContractError("evaluation_context experiment_arm is invalid")
    if context["auditor_mode"] not in {"none", "same_pair_auditor", "diverse_read_only_auditor"}:
        raise ContractError("evaluation_context auditor_mode is invalid")
    return context


def validate_execution_envelope(envelope: Any) -> dict[str, Any]:
    value = _expect_exact(envelope, ENVELOPE_FIELDS, "execution envelope")
    if value["schema_version"] != "execution-envelope.v1":
        raise ContractError("unsupported execution envelope schema_version")
    for field in ("envelope_id", "work_request_id"):
        _string(value[field], f"execution envelope {field}", identifier=True)
    plan = _expect_exact(value["plan_revision"], PLAN_FIELDS, "plan_revision")
    _string(plan["id"], "plan_revision id", identifier=True)
    if not isinstance(plan["revision"], int) or isinstance(plan["revision"], bool) or plan["revision"] < 1:
        raise ContractError("plan_revision revision must be a positive integer")
    _digest(plan["digest"], "plan_revision digest")
    session = _expect_exact(value["agent_session"], SESSION_FIELDS, "agent_session")
    _string(session["id"], "agent_session id", identifier=True)
    _timestamp(session["lease_expires_at"], "agent_session lease_expires_at")
    _timestamp(value["issued_at"], "execution envelope issued_at")
    _timestamp(value["expires_at"], "execution envelope expires_at")
    _validate_state_binding(value["state_binding"])
    _validate_phase_binding(value["phase_binding"])
    _validate_evaluation_context(value["evaluation_context"])
    _validate_request(value["request"])
    _validate_server_binding(value["server_binding"])
    handoff = _expect_exact(value["handoff"], HANDOFF_FIELDS | {"checkpoint_ref", "native_session_transfer"}, "handoff")
    if handoff["mode"] not in {"original", "replacement"}:
        raise ContractError("handoff mode is invalid")
    if handoff["capability_inherited"] is not False:
        raise ContractError("handoff capability_inherited must be false")
    replacement = handoff["replaces_agent_session_id"]
    if handoff["native_session_transfer"] != "semantic_state_only":
        raise ContractError("handoff cannot promise portable native session internals")
    if handoff["mode"] == "original" and (replacement is not None or handoff["checkpoint_ref"] is not None):
        raise ContractError("original envelope cannot name a replacement session or checkpoint")
    if handoff["mode"] == "replacement":
        _string(replacement, "handoff replaces_agent_session_id", identifier=True)
        _string(handoff["checkpoint_ref"], "handoff checkpoint_ref", identifier=True)
        if replacement == session["id"]:
            raise ContractError("replacement envelope must use a new agent session")
    return value


def execution_envelope_digest(envelope: Any) -> str:
    return canonical_digest(validate_execution_envelope(envelope))


def validate_replacement_envelope(previous: Any, replacement: Any) -> dict[str, Any]:
    prior = validate_execution_envelope(previous)
    value = validate_execution_envelope(replacement)
    if value["handoff"]["mode"] != "replacement":
        raise ContractError("replacement validation requires handoff mode replacement")
    if value["handoff"]["replaces_agent_session_id"] != prior["agent_session"]["id"]:
        raise ContractError("replacement must link the preceding agent session")
    if value["server_binding"]["authority"]["capability_grant_ref"] == prior["server_binding"]["authority"]["capability_grant_ref"]:
        raise ContractError("replacement session cannot inherit capability grant")
    return value


def _validate_receipt_lifecycle(value: Any) -> dict[str, Any]:
    lifecycle = _expect_exact(value, LIFECYCLE_FIELDS, "receipt lifecycle")
    _timestamp(lifecycle["started_at"], "receipt lifecycle started_at")
    _timestamp(lifecycle["ended_at"], "receipt lifecycle ended_at")
    if lifecycle["state"] not in TERMINAL_STATES:
        raise ContractError("receipt lifecycle state is invalid")
    for field in ("retry_count", "recovery_count"):
        _nonnegative_int(lifecycle[field], f"receipt lifecycle {field}")
    if lifecycle["failure_class"] is not None:
        _string(lifecycle["failure_class"], "receipt lifecycle failure_class", identifier=True)
    return lifecycle


def _validate_receipt_result(value: Any) -> dict[str, Any]:
    result = _expect_exact(value, RESULT_FIELDS, "receipt result")
    _string(result["job_ref"], "receipt result job_ref", identifier=True)
    if result["outcome"] not in OUTCOMES:
        raise ContractError("receipt result outcome is invalid")
    if result["verification_state"] not in VERIFICATION_STATES:
        raise ContractError("receipt result verification_state is invalid")
    _list_of_strings(result["artifact_refs"], "receipt result artifact_refs")
    _list_of_strings(result["evidence_refs"], "receipt result evidence_refs")
    if not isinstance(result["validation_results"], list):
        raise ContractError("receipt result validation_results must be a list")
    for row in result["validation_results"]:
        entry = _expect_exact(row, VALIDATION_RESULT_FIELDS, "receipt validation result")
        _string(entry["check_id"], "receipt validation result check_id", identifier=True)
        if entry["state"] not in {"passed", "failed", "partial", "unknown", "not_run"}:
            raise ContractError("receipt validation result state is invalid")
        _list_of_strings(entry["evidence_refs"], "receipt validation result evidence_refs")
    return result


def _validate_attestation(value: Any) -> dict[str, Any]:
    attestation = _expect_exact(value, ATTESTATION_FIELDS, "receipt attestation")
    if attestation["claim_state"] != "executor_claim" or attestation["canonical_promotion_state"] != "not_promoted":
        raise ContractError("attempt receipt is executor evidence, not canonical verified state")
    if attestation["independent_evidence_required"] is not True:
        raise ContractError("attempt receipt requires independent evidence before canonical promotion")
    return attestation


def _validate_negative_knowledge(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractError("receipt negative_knowledge must be a list")
    for row in value:
        item = _expect_exact(row, NEGATIVE_KNOWLEDGE_FIELDS, "receipt negative knowledge")
        _string(item["approach_ref"], "receipt negative knowledge approach_ref", identifier=True)
        _list_of_strings(item["evidence_refs"], "receipt negative knowledge evidence_refs")
        _string(item["applicability"], "receipt negative knowledge applicability", identifier=True)
        _timestamp(item["revalidate_after"], "receipt negative knowledge revalidate_after")
        _timestamp(item["expires_at"], "receipt negative knowledge expires_at")
    return value


def _validate_observation(value: Any) -> dict[str, Any]:
    """Validate redacted declared-versus-observed evidence without overclaiming."""
    observation = _expect_exact(value, OBSERVATION_FIELDS, "receipt observation")
    if observation["progress_state"] not in {
        "active", "quiet", "stale", "blocked", "failed", "unknown", "verified_complete",
    }:
        raise ContractError("receipt observation progress_state is invalid")
    if observation["coverage_state"] not in {"covered", "partial", "none_observed", "unknown", "not_applicable"}:
        raise ContractError("receipt observation coverage_state is invalid")
    if observation["activity_fidelity"] not in {"native_hook", "filesystem", "mixed", "none"}:
        raise ContractError("receipt observation activity_fidelity is invalid")
    _list_of_strings(observation["declared_refs_observed"], "receipt observation declared_refs_observed")
    _list_of_strings(observation["unmapped_activity_refs"], "receipt observation unmapped_activity_refs")
    if not isinstance(observation["deviation_candidates"], list):
        raise ContractError("receipt observation deviation_candidates must be a list")
    for row in observation["deviation_candidates"]:
        candidate = _expect_exact(row, DEVIATION_CANDIDATE_FIELDS, "receipt deviation candidate")
        _string(candidate["candidate_id"], "receipt deviation candidate candidate_id", identifier=True)
        if not isinstance(candidate["basis"], list) or not candidate["basis"]:
            raise ContractError("receipt deviation candidate basis must be a non-empty list")
        allowed_basis = {"declared_binding_mismatch", "native_tool_event", "filesystem_change", "human_report"}
        if not set(candidate["basis"]).issubset(allowed_basis):
            raise ContractError("receipt deviation candidate basis is invalid")
        if set(candidate["basis"]) == {"filesystem_change"}:
            raise ContractError("filesystem movement alone cannot establish a deviation candidate")
        if candidate["confidence"] not in {"low", "medium", "high"} or candidate["requires_review"] is not True:
            raise ContractError("receipt deviation candidates remain uncertain and require review")
    if observation["uncertainty"] not in {"none", "native_event_gap", "filesystem_only", "stale_signal", "scope_ambiguous", "unknown"}:
        raise ContractError("receipt observation uncertainty is invalid")
    return observation


def validate_attempt_receipt(receipt: Any, envelope: Any | None = None) -> dict[str, Any]:
    value = _expect_exact(receipt, RECEIPT_FIELDS, "attempt receipt")
    if value["schema_version"] != "attempt-receipt.v1":
        raise ContractError("unsupported attempt receipt schema_version")
    _string(value["attempt_id"], "attempt receipt attempt_id", identifier=True)
    _digest(value["envelope_digest"], "attempt receipt envelope_digest")
    if not isinstance(value["attempt_ordinal"], int) or isinstance(value["attempt_ordinal"], bool) or value["attempt_ordinal"] < 1:
        raise ContractError("attempt receipt attempt_ordinal must be a positive integer")
    _validate_adapter(value["adapter"])
    lifecycle = _validate_receipt_lifecycle(value["lifecycle"])
    result = _validate_receipt_result(value["result"])
    _validate_attestation(value["attestation"])
    _validate_negative_knowledge(value["negative_knowledge"])
    telemetry = _expect_exact(value["telemetry"], TELEMETRY_FIELDS, "receipt telemetry")
    _nonnegative_int(telemetry["latency_ms"], "receipt telemetry latency_ms")
    _finite_nonnegative(telemetry["cost_usd"], "receipt telemetry cost_usd")
    usage = _expect_exact(telemetry["usage"], USAGE_FIELDS, "receipt telemetry usage")
    for field in USAGE_FIELDS:
        _nonnegative_int(usage[field], f"receipt telemetry usage {field}")
    reset_tax = _expect_exact(telemetry["reset_tax"], RESET_TAX_FIELDS, "receipt telemetry reset_tax")
    for field in RESET_TAX_FIELDS:
        _nonnegative_int(reset_tax[field], f"receipt telemetry reset_tax {field}")
    if not isinstance(value["tool_event_summaries"], list):
        raise ContractError("receipt tool_event_summaries must be a list")
    for row in value["tool_event_summaries"]:
        event = _expect_exact(row, TOOL_SUMMARY_FIELDS, "receipt tool event summary")
        _string(event["tool_name"], "receipt tool event summary tool_name", identifier=True)
        if event["result_class"] not in {"ok", "refused", "failed", "timed_out", "unknown"}:
            raise ContractError("receipt tool event summary result_class is invalid")
        _nonnegative_int(event["duration_ms"], "receipt tool event summary duration_ms")
    _validate_observation(value["observation"])
    if not isinstance(value["interventions"], list):
        raise ContractError("receipt interventions must be a list")
    for row in value["interventions"]:
        intervention = _expect_exact(row, INTERVENTION_FIELDS, "receipt intervention")
        if intervention["kind"] not in {"human", "automatic_recovery", "policy_refusal"}:
            raise ContractError("receipt intervention kind is invalid")
        _timestamp(intervention["occurred_at"], "receipt intervention occurred_at")
        _string(intervention["summary"], "receipt intervention summary")
    proposal = _expect_exact(value["handoff_proposal"], HANDOFF_PROPOSAL_FIELDS, "receipt handoff_proposal")
    if not isinstance(proposal["proposed"], bool):
        raise ContractError("receipt handoff_proposal proposed must be boolean")
    if proposal["proposed"]:
        _string(proposal["reason"], "receipt handoff_proposal reason")
        _string(proposal["replacement_session_ref"], "receipt handoff_proposal replacement_session_ref", identifier=True)
        _string(proposal["checkpoint_ref"], "receipt handoff_proposal checkpoint_ref", identifier=True)
        if proposal["requires_independent_verification"] is not True:
            raise ContractError("handoff proposal requires independent checkpoint verification")
    elif any(proposal[field] is not None for field in ("reason", "replacement_session_ref", "checkpoint_ref")) or proposal["requires_independent_verification"] is not False:
        raise ContractError("non-proposed handoff cannot name a reason, checkpoint, or replacement")
    if envelope is not None:
        bound = validate_execution_envelope(envelope)
        if value["envelope_digest"] != execution_envelope_digest(bound):
            raise ContractError("attempt receipt does not bind the exact execution envelope")
        if value["adapter"] != bound["server_binding"]["adapter"]:
            raise ContractError("attempt receipt adapter does not match the envelope adapter")
        if result["job_ref"] != bound["request"]["job_ref"]:
            raise ContractError("attempt receipt job_ref does not match the envelope request")
    return value


def validate_progress_event(event: Any) -> dict[str, Any]:
    """Validate an optional live-attempt event; events are observational, never control."""
    value = _expect_exact(event, PROGRESS_EVENT_FIELDS, "progress event")
    if value["schema_version"] != "progress-event.v1":
        raise ContractError("unsupported progress event schema_version")
    _string(value["attempt_id"], "progress event attempt_id", identifier=True)
    if not isinstance(value["sequence"], int) or isinstance(value["sequence"], bool) or value["sequence"] < 1:
        raise ContractError("progress event sequence must be a positive integer")
    _timestamp(value["occurred_at"], "progress event occurred_at")
    if value["event_type"] not in {"declared", "observed_tool", "observed_filesystem", "coverage_updated", "blocked", "terminal"}:
        raise ContractError("progress event event_type is invalid")
    for field in ("declared_step_ref", "observed_resource_ref", "observed_component_ref", "tool_class", "causation_id"):
        if value[field] is not None:
            _string(value[field], f"progress event {field}", identifier=True)
    if value["state"] not in {"active", "quiet", "stale", "blocked", "failed", "unknown", "verified_complete"}:
        raise ContractError("progress event state is invalid")
    _string(value["correlation_id"], "progress event correlation_id", identifier=True)
    if value["redaction_class"] not in {"metadata_only", "redacted_evidence"}:
        raise ContractError("progress event redaction_class is invalid")
    _list_of_strings(value["evidence_refs"], "progress event evidence_refs")
    if value["retention"] not in {"ephemeral", "material_redacted"}:
        raise ContractError("progress event retention is invalid")
    return value


def project_observatory_attempt(envelope: Any, receipt: Any, events: list[Any], profile: Any) -> dict[str, Any]:
    """Build a deterministic, transcript-free Model Room projection.

    The Work Request groups the view. Repository/worktree references stay in
    declared or observed resources, and the persistent profile is deliberately
    adjacent to—not merged with—the actual model/harness staffing.
    """
    bound = validate_execution_envelope(envelope)
    completed = validate_attempt_receipt(receipt, bound)
    named_profile = _expect_exact(profile, PROFILE_FIELDS, "observatory profile")
    _string(named_profile["profile_id"], "observatory profile profile_id", identifier=True)
    _string(named_profile["display_label"], "observatory profile display_label")
    if not isinstance(events, list):
        raise ContractError("observatory events must be a list")
    timeline = []
    for raw in events:
        event = validate_progress_event(raw)
        if event["attempt_id"] != completed["attempt_id"]:
            raise ContractError("observatory event belongs to another attempt")
        timeline.append({field: event[field] for field in (
            "sequence", "occurred_at", "event_type", "declared_step_ref", "observed_resource_ref",
            "observed_component_ref", "tool_class", "state", "evidence_refs", "retention",
        )})
    if [row["sequence"] for row in timeline] != sorted(row["sequence"] for row in timeline):
        raise ContractError("observatory events must be sequence ordered")
    declared = bound["request"]["declared_expectations"]
    current_component = next(
        (row["observed_component_ref"] for row in reversed(timeline) if row["observed_component_ref"]), None
    )
    dependencies = {ref: [] for ref in declared["component_refs"]}
    for edge in declared["component_dependencies"]:
        dependencies.setdefault(edge["component_ref"], []).append(edge["depends_on_component_ref"])
    evidence_refs = sorted(set(completed["result"]["evidence_refs"] + [
        ref for row in timeline for ref in row["evidence_refs"]
    ]))
    return {
        "schema_version": "observatory-attempt-projection.v1",
        "work_request_id": bound["work_request_id"],
        "declared_intent": declared,
        "component_map": [{
            "component_ref": ref, "depends_on_component_refs": sorted(dependencies.get(ref, [])),
            "current": ref == current_component,
        } for ref in declared["component_refs"]],
        "attempt_lane": {
            "attempt_id": completed["attempt_id"],
            "persistent_profile": named_profile,
            "actual_staffing": completed["adapter"],
        },
        "state": {
            "lifecycle": completed["lifecycle"]["state"],
            "progress": completed["observation"]["progress_state"],
            "verification": completed["result"]["verification_state"],
        },
        "observed_movement": completed["observation"],
        "timeline": timeline,
        "evidence_refs": evidence_refs,
    }


def receipt_from_dispatch_row(envelope: Any, row: dict[str, Any], *, attempt_id: str | None = None) -> dict[str, Any]:
    """Create a redacted receipt from today's bridge row; never copy task/result text."""
    bound = validate_execution_envelope(envelope)
    if not isinstance(row, dict):
        raise ContractError("dispatch row must be an object")
    status = row.get("status")
    mapped = {
        "completed": ("succeeded", "success", "not_attempted", None),
        "failed": ("failed", "failure", "unknown", "dispatch_failed"),
        "timed_out": ("timed_out", "timeout", "unknown", "dispatch_timeout"),
        "cancelled": ("cancelled", "cancellation", "not_attempted", "dispatch_cancelled"),
        "partial": ("partial", "partial", "partial", "dispatch_partial"),
    }.get(status, ("unknown", "unknown", "unknown", "dispatch_unknown"))
    stamp = row.get("dispatched_at")
    _timestamp(stamp, "dispatch row dispatched_at")
    native_ref = row.get("thread_id") or row.get("msg_id") or "native-dispatch-unknown"
    # The envelope itself is the server-selected adapter identity. A transport-returned
    # thread ID is evidence only; it cannot replace that selection in the receipt.
    result_class = "ok" if mapped[0] == "succeeded" else mapped[0]
    if result_class == "succeeded":
        result_class = "ok"
    elif result_class not in {"failed", "timed_out", "unknown"}:
        result_class = "unknown"
    return {
        "schema_version": "attempt-receipt.v1",
        "attempt_id": attempt_id or str(row.get("msg_id") or "attempt-unknown"),
        "envelope_digest": execution_envelope_digest(bound),
        "attempt_ordinal": 1,
        "adapter": bound["server_binding"]["adapter"],
        "lifecycle": {
            "started_at": stamp, "ended_at": stamp, "state": mapped[0], "retry_count": 0,
            "recovery_count": 0, "failure_class": mapped[3],
        },
        "result": {
            "job_ref": bound["request"]["job_ref"], "outcome": mapped[1],
            "verification_state": mapped[2], "artifact_refs": [], "evidence_refs": [],
            "validation_results": [],
        },
        "attestation": {"claim_state": "executor_claim", "canonical_promotion_state": "not_promoted", "independent_evidence_required": True},
        "negative_knowledge": [],
        "telemetry": {"latency_ms": 0, "cost_usd": 0, "usage": {
            "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0,
        }, "reset_tax": {"context_reconstruction_ms": 0, "duplicated_tool_calls": 0,
                           "repeated_failed_approach_count": 0, "human_correction_count": 0,
                           "switch_overhead_ms": 0}},
        "tool_event_summaries": [{"tool_name": "room_bridge_dispatch", "result_class": result_class, "duration_ms": 0}],
        "observation": {
            "progress_state": "unknown", "coverage_state": "unknown", "activity_fidelity": "none",
            "declared_refs_observed": [], "unmapped_activity_refs": [], "deviation_candidates": [],
            "uncertainty": "unknown",
        },
        "interventions": [],
        "handoff_proposal": {"proposed": False, "reason": None, "replacement_session_ref": None,
                              "checkpoint_ref": None, "requires_independent_verification": False},
    }
