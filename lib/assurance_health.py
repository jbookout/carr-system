"""Deterministic assurance health and scoped degradation (V5-A01, pure domain phase).

WHAT THIS IS. A pure function from one exactly bound scope plus six DISTINCT
evidence inputs to one closed display ``state``, one closed ``capability_stage``,
and the owner/evidence/impact/recovery a reader needs to act.  It reads nothing,
writes nothing, holds no authority, performs no effect, and stores no state.
Every input is supplied by a caller that read an existing authoritative surface.

THE SIX EVIDENCE SLOTS ARE SIX SEPARATE FACTS, and the whole point of this module
is that none of them is ever derived from another (Q043):

    artifact_assessment       independently reviewed artifact/evidence digest bound
                              to an exact repository commit and tree
    execution_assessment      an exact attempt/engineering receipt plus deterministic
                              execution evidence (envelope digest, plan hash)
    controller_assessment     current server/controller readback; an ENABLED
                              CONFIGURATION IS NEVER THIS FACT (Q053)
    candidate_outcome_oracle  a preactivation oracle receipt bound to governed data,
                              environment, expected result, equivalence comparator,
                              component versions and its own expiry
    activation_readback       a separate current controller/provider readback taken
                              AFTER activation
    actual_business_outcome   a separate accepted sourced outcome-feedback receipt
                              (ref + hash + acceptance receipt) joined through the
                              SAME exact Work Request identity

THREE INDEPENDENT GUARDS KEEP THE LAST SLOT HONEST, because filling it by
inference is the exact false-green this slice exists to remove:

 1. every evidence record declares its own ``layer``, and a record declaring one
    layer cannot be handed to another slot (refusal, not a guess);
 2. every record declares its ``basis``, and only one basis is admissible per
    slot -- ``enforcement_closure``, ``completion_lifecycle``, ``merge_or_ci_result``,
    ``enabled_configuration``, ``telemetry``, ``self_attestation`` and
    ``candidate_outcome_pass`` are named in the vocabulary precisely so that
    offering one is reported as ``refused_substitute`` rather than silently read
    as an outcome;
 3. the six slots must hold six DISTINCT exact identities; reusing one receipt's
    ref or digest in a second slot is ``indistinct``, never two facts.

WHAT IT DELIBERATELY IS NOT.

* It is not a workflow, status, receipt, completion or incident registry.  The
  only workflow authority is ``lib/control_plane_workflow_truth`` (V5-F09); this
  module CONSUMES one of its projected rows and refuses any other shape, so a
  second census cannot grow here.  Incident, impact and recovery references are
  echoed from the caller's existing records; none is invented.
* It is not an activation, a deployment, a page, or any other effect.  A
  ``recovery`` block names what evidence the bound owner must obtain; it never
  claims something was done.
* It invents no freshness.  Every evidence record must arrive with the expiry its
  own source defines; a record without one is refused rather than treated as
  eternally current.
* It never guesses a degradation stage.  The act/draft/read/unavailable ladder is
  a fixed table of capability requirements evaluated against supplied evidence.

FAIL-CLOSED, AND THE TWO KINDS OF ABSENCE.  ``UNREADABLE`` (reused from F09)
means "the read did not happen or failed"; ``None`` means "the read happened and
there is genuinely no evidence".  They are different facts and are reported
differently, and neither is ever green.  A malformed input raises
``AssuranceHealthContractError`` rather than being guessed at: the caller owns
mapping an absent row onto ``None``, and this module owns identities,
distinctness, currentness, precedence, scope and state.

SCOPE IS EXACT, AND SCOPE IS A FENCE.  A scope is bound by
(workflow_key, workflow_version, work_request_id) -- identities only.  No human
label, description or free-text field participates in any join, and evidence
carrying a different identity is ``mismatched``, never "close enough".  Each row
is computed from its own bound scope's inputs alone, so a failure in one scope
cannot move an unrelated bound scope's evidence-derived state (Q006).

DISPLAY PRECEDENCE, most conservative first, evaluated in this exact order:

    1. unknown              authoritative workflow truth is indeterminate
    2. disabled             F09 says the definition exists and is not enabled
    3. failed               a determinate non-pass withdrew even read capability,
                            and it did so BY ITSELF -- capability that is missing
                            only because a layer could not be read is unproven,
                            not withdrawn, and never reported as failed
    4. degraded             a determinate non-pass withdrew act and/or draft
    5. not-yet-operational  nothing failed, and the scope has not yet earned live
                            admission or has no accepted outcome evidence yet
    6. healthy              full act capability on six current, distinct,
                            independent, exactly bound passing facts
    7. unknown              anything else: evidence is incomplete, so nothing is claimed
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lib.control_plane_workflow_truth import (  # the ONE workflow authority (V5-F09)
    SCHEMA_VERSION as WORKFLOW_TRUTH_SCHEMA_VERSION,
    STATES as WORKFLOW_TRUTH_STATES,
    UNREADABLE,
    completion_subject_key,
)

SCHEMA_VERSION = "assurance-health.v1"

# The six display states, exactly as the settled contract names them.  There is no
# seventh, and only one of them is green.
DISPLAY_STATES = (
    "healthy",
    "degraded",
    "failed",
    "unknown",
    "disabled",
    "not-yet-operational",
)
GREEN_STATE = "healthy"

# The scoped degradation ladder.  A failure walks DOWN this ladder and stops; it
# never jumps, and it never leaves the bound scope.
CAPABILITY_STAGES = ("act", "draft", "read", "unavailable")

PREACTIVATION_SLOTS = (
    "artifact_assessment",
    "execution_assessment",
    "controller_assessment",
    "candidate_outcome_oracle",
)
POSTACTIVATION_SLOTS = ("activation_readback", "actual_business_outcome")
EVIDENCE_SLOTS = PREACTIVATION_SLOTS + POSTACTIVATION_SLOTS

# The exact identity a scope is bound by.  Identities only: adding a label-shaped
# field here is what "no name/title heuristic" forbids.
#
# THE WORK REQUEST IDENTITY IS OPTIONAL ON THE SCOPE AND REQUIRED FOR THE LAYER
# THAT JOINS THROUGH IT.  The authoritative workflow census this projection is
# wired to carries workflow identities and no Work Request identity, so making a
# Work Request part of every scope key would have left every real scope
# unbindable -- and a surface that can bind no scope is silent, not honest.  A
# scope may therefore be bound by workflow identity alone; the only consequence,
# and it is the correct one, is that ``actual_business_outcome`` then has no
# admissible join, so act capability and green are unreachable for it.
SCOPE_IDENTITY_FIELDS = ("workflow_key", "workflow_version", "work_request_id")
REQUIRED_SCOPE_IDENTITY_FIELDS = ("workflow_key", "workflow_version")
OPTIONAL_SCOPE_IDENTITY_FIELDS = ("work_request_id",)

# The exact scope identity each layer must be able to join through.  A layer whose
# required identity the scope does not carry is ``unbindable``: it is not a
# finding against the scope, and it is never a pass.
SLOT_REQUIRED_SCOPE_IDENTITY = {"actual_business_outcome": "work_request_id"}

# What a source can say about its own evidence.  Anything else is refused.
EVIDENCE_STATUSES = ("pass", "fail", "skipped", "untested", "error", "conflicting")

# Ordered most-conservative first: the first condition that holds wins, so a
# contradiction, a refused substitute or an expiry can never be outranked by the
# record's own claim to pass.
EVIDENCE_STATES = (
    "unreadable",          # the caller could not read the source
    "missing",             # read, and there is genuinely no evidence
    "mismatched",          # evidence exists but binds a different exact scope
    "unbindable",          # this scope carries no identity this layer can join through
    "conflicting",         # the record contradicts itself or its neighbours
    "refused_substitute",  # a basis that is never sufficient for THIS slot
    "failed",              # a real, current, exactly bound non-pass
    "error",               # the assessment could not complete
    "skipped",
    "untested",
    "self_attested",       # the evaluator and the subject are one identity
    "indistinct",          # reuses another slot's exact evidence identity
    "stale",               # past the expiry its own source defined
    "passing",             # the ONLY state that can contribute to healthy
)

# "missing" and "unbindable" belong to neither class: they are the two absences
# that "not-yet-operational" is allowed to describe, and neither is a finding on
# its own.  Neither is ever green either -- both block the act stage.
INDETERMINATE_EVIDENCE_STATES = ("unreadable", "mismatched", "conflicting", "indistinct")
UNEARNED_EVIDENCE_STATES = ("missing", "unbindable")
DETERMINATE_NONPASS_EVIDENCE_STATES = (
    "refused_substitute", "failed", "error", "skipped", "untested", "self_attested", "stale",
)

# Every basis a caller may declare.  The forbidden ones are IN this vocabulary on
# purpose: an unnamed basis is a malformed input, while a named-but-inadmissible
# basis must be reported as the exact refusal it is.
EVIDENCE_BASES = (
    "independent_artifact_review",
    "attempt_receipt_execution_evidence",
    "controller_readback",
    "candidate_outcome_oracle_receipt",
    "activation_readback",
    "accepted_sourced_outcome_feedback_receipt",
    # never sufficient for the slot that offers them
    "enforcement_closure",
    "completion_lifecycle",
    "merge_or_ci_result",
    "enabled_configuration",
    "telemetry",
    "self_attestation",
    "candidate_outcome_pass",
)

SLOT_ADMISSIBLE_BASES = {
    "artifact_assessment": ("independent_artifact_review",),
    "execution_assessment": ("attempt_receipt_execution_evidence",),
    "controller_assessment": ("controller_readback",),
    "candidate_outcome_oracle": ("candidate_outcome_oracle_receipt",),
    "activation_readback": ("activation_readback",),
    "actual_business_outcome": ("accepted_sourced_outcome_feedback_receipt",),
}

# The exact binding each layer must carry, drawn from the records that already
# prove these edges (0451 assurance extensions, 0303 attempt/activation, the
# activation-reliability evaluation plan, 0179 sourced outcome feedback).
SLOT_REQUIRED_FIELDS = {
    "artifact_assessment": ("repository_commit_sha", "repository_tree_sha", "reviewer_fact_id"),
    "execution_assessment": ("attempt_id", "envelope_digest", "plan_hash"),
    "controller_assessment": ("controller_state", "readback_source", "readback_at"),
    "candidate_outcome_oracle": ("governed_data_ref", "environment", "expected_result_ref",
                                 "equivalence_comparator", "component_versions"),
    "activation_readback": ("activation_id", "readback_source", "readback_at"),
    "actual_business_outcome": ("outcome_feedback_ref", "outcome_feedback_hash",
                                "acceptance_receipt_id"),
}
_INSTANT_FIELDS = ("readback_at",)
_VERSION_MAP_FIELDS = ("component_versions",)

# What the bound owner must obtain to clear a non-passing slot.  These are
# requirements, not effects: nothing here is performed.
SLOT_REQUIREMENT = {
    "artifact_assessment":
        "a current independently reviewed artifact assessment bound to the exact "
        "repository commit and tree of this scope",
    "execution_assessment":
        "a current exact attempt receipt with deterministic execution evidence "
        "(envelope digest and plan hash) for this scope",
    "controller_assessment":
        "a current controller/provider readback for this scope; enabled configuration "
        "is never this fact",
    "candidate_outcome_oracle":
        "a current passing candidate-outcome oracle receipt bound to governed data, "
        "environment, expected result, equivalence comparator, versions and its own TTL",
    "activation_readback":
        "a current controller/provider readback taken after activation of this scope",
    "actual_business_outcome":
        "a current accepted sourced outcome-feedback receipt (ref, hash and acceptance "
        "receipt) joined through this scope's exact Work Request identity",
}

# The ladder as a table, evaluated top down.  The first stage whose every
# requirement is proven is the stage the scope actually holds.
_STAGE_REQUIREMENTS = (
    ("act", ("workflow_readable", "workflow_coherent", "workflow_live_admissible",
             "artifact_assessment", "execution_assessment", "controller_assessment",
             "candidate_outcome_oracle", "activation_readback", "actual_business_outcome")),
    ("draft", ("workflow_readable", "workflow_coherent", "workflow_enabled",
               "artifact_assessment", "execution_assessment", "controller_assessment")),
    ("read", ("workflow_readable", "workflow_coherent", "artifact_assessment")),
)

# What a failure took away, given the highest stage the failure alone still allows.
_STAGE_DOWN_TO = {stage: list(CAPABILITY_STAGES[:index])
                  for index, stage in enumerate(CAPABILITY_STAGES)}

# F09 states this projection reads as authoritative dispositions of the workflow
# itself.  Nothing else in this module decides these three facts.
_TRUTH_INDETERMINATE = ("unknown", "conflict", "undeclared")
_TRUTH_NOT_ENABLED = ("declared_disabled",)

_WORKFLOW_TRUTH_PUBLIC_FIELDS = (
    "state", "disposition", "enabled", "admissible_modes", "operational",
    "false_operational", "duplicate_open", "completion_lifecycle_state", "reasons",
)


class AssuranceHealthContractError(ValueError):
    """A caller supplied an input this projection refuses to guess at."""


def _utc(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise AssuranceHealthContractError(f"{field} must be an ISO-8601 instant")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise AssuranceHealthContractError(f"{field} is not an ISO-8601 instant: {exc}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _require_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssuranceHealthContractError(f"{field} must be an object")
    return value


def _require_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssuranceHealthContractError(f"{field} must be a non-empty string")
    return value


def _require_refs(value: Any, *, field: str) -> list[str]:
    """Existing incident/recovery references, echoed exactly or not at all."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AssuranceHealthContractError(f"{field} must be a list of existing evidence refs")
    return [_require_text(item, field=f"{field}[]") for item in value]


def scope_identity(scope: Any, *, field: str = "scope") -> dict[str, Any]:
    """The exact identity a scope or an evidence record binds.

    Identities only.  There is deliberately no route by which a human label could
    stand in for one of these values.
    """
    mapping = _require_mapping(scope, field=field)
    missing = [name for name in REQUIRED_SCOPE_IDENTITY_FIELDS if name not in mapping]
    if missing:
        raise AssuranceHealthContractError(
            f"{field} is missing the exact scope-binding identities {sorted(missing)}; "
            "an unbound record is refused, never joined by any other field")
    version = mapping["workflow_version"]
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise AssuranceHealthContractError(f"{field}.workflow_version must be a positive integer")
    # Absent and explicitly null are the same fact: this scope is not bound to a
    # Work Request.  Anything else present must be an exact identity -- an empty
    # or non-string value is refused rather than quietly read as unbound, because
    # "no binding" and "a broken binding" are different facts.
    work_request_id = mapping.get("work_request_id")
    if work_request_id is not None:
        work_request_id = _require_text(work_request_id, field=f"{field}.work_request_id")
    return {
        "workflow_key": _require_text(mapping["workflow_key"], field=f"{field}.workflow_key"),
        "workflow_version": version,
        "work_request_id": work_request_id,
    }


def _component_versions(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, dict) or not value:
        raise AssuranceHealthContractError(
            f"{field} must be a non-empty object of component name to version")
    return sorted(f"{_require_text(name, field=field)}="
                  f"{_require_text(version, field=f'{field}.{name}')}"
                  for name, version in value.items())


def _evidence_record(slot: str, value: Any) -> dict[str, Any]:
    """Validate one supplied evidence mapping into its exact, public identity.

    Only the fields named here are ever carried into the projection, so a caller
    that hands over a row with a secret alongside its identity cannot leak it
    through this surface.
    """
    supplied = _require_mapping(value, field=f"evidence[{slot}]")

    layer = _require_text(supplied.get("layer"), field=f"evidence[{slot}].layer")
    if layer != slot:
        raise AssuranceHealthContractError(
            f"evidence declaring layer {layer!r} cannot fill the {slot!r} slot: "
            "no layer may be inferred from another")

    basis = _require_text(supplied.get("basis"), field=f"evidence[{slot}].basis")
    if basis not in EVIDENCE_BASES:
        raise AssuranceHealthContractError(
            f"evidence[{slot}].basis {basis!r} is outside the closed vocabulary")

    status = supplied.get("status")
    if status not in EVIDENCE_STATUSES:
        raise AssuranceHealthContractError(
            f"evidence[{slot}].status must be one of {EVIDENCE_STATUSES}")

    record: dict[str, Any] = {
        "slot": slot,
        "layer": layer,
        "basis": basis,
        "status": status,
        "evidence_ref": _require_text(supplied.get("evidence_ref"),
                                      field=f"evidence[{slot}].evidence_ref"),
        "evidence_digest": _require_text(supplied.get("evidence_digest"),
                                         field=f"evidence[{slot}].evidence_digest"),
        "evaluator_identity": _require_text(supplied.get("evaluator_identity"),
                                            field=f"evidence[{slot}].evaluator_identity"),
        "subject_identity": _require_text(supplied.get("subject_identity"),
                                          field=f"evidence[{slot}].subject_identity"),
        "observed_at": _utc(supplied.get("observed_at"), field=f"evidence[{slot}].observed_at"),
        # REQUIRED. This module defines no window of its own; evidence that arrives
        # without the expiry its own source defines is refused rather than treated
        # as current forever.
        "expires_at": _utc(supplied.get("expires_at"), field=f"evidence[{slot}].expires_at"),
        "scope": scope_identity(supplied.get("scope"), field=f"evidence[{slot}].scope"),
    }
    for name in SLOT_REQUIRED_FIELDS[slot]:
        field = f"evidence[{slot}].{name}"
        if name in _INSTANT_FIELDS:
            record[name] = _utc(supplied.get(name), field=field)
        elif name in _VERSION_MAP_FIELDS:
            record[name] = _component_versions(supplied.get(name), field=field)
        else:
            record[name] = _require_text(supplied.get(name), field=field)
    return record


def _classify(record: dict[str, Any], *, bound: dict[str, Any], now: datetime,
              shared: dict[str, list[str]]) -> tuple[str, list[str]]:
    """Classify one validated record.  Every condition that holds is reported."""
    slot = record["slot"]
    flags: set[str] = set()
    reasons: list[str] = []

    if record["scope"] != bound:
        flags.add("mismatched")
        reasons.append(
            f"{slot}: evidence binds workflow {record['scope']['workflow_key']}"
            f":v{record['scope']['workflow_version']} / work request "
            f"{record['scope']['work_request_id']}, which is not this bound scope")

    required_identity = SLOT_REQUIRED_SCOPE_IDENTITY.get(slot)
    if required_identity is not None and bound.get(required_identity) is None:
        flags.add("unbindable")
        reasons.append(
            f"{slot}: this scope carries no {required_identity}, so there is no identity "
            "this layer can be joined through; no receipt can fill it until the binding exists")

    for name in ("observed_at",) + _INSTANT_FIELDS:
        instant = record.get(name)
        if isinstance(instant, datetime) and instant > now:
            flags.add("conflicting")
            reasons.append(f"{slot}: {name} is after the projection instant")

    if record["status"] == "conflicting":
        flags.add("conflicting")
        reasons.append(f"{slot}: the source reports contradictory evidence")

    if record["basis"] not in SLOT_ADMISSIBLE_BASES[slot]:
        flags.add("refused_substitute")
        reasons.append(
            f"{slot}: {record['basis']} is refused as a substitute for this layer; "
            f"only {list(SLOT_ADMISSIBLE_BASES[slot])} can fill it")

    if record["evaluator_identity"] == record["subject_identity"]:
        flags.add("self_attested")
        reasons.append(
            f"{slot}: evaluator {record['evaluator_identity']} is the subject it assessed")

    others = sorted(set(shared.get(slot, [])))
    if others:
        flags.add("indistinct")
        reasons.append(
            f"{slot}: reuses the exact evidence identity of {others}; one receipt is "
            "one fact and can never satisfy two layers")

    if now > record["expires_at"]:
        flags.add("stale")
        reasons.append(
            f"{slot}: evidence expired at {record['expires_at'].isoformat()} "
            f"and is no longer current")

    status_state = {"fail": "failed", "error": "error", "skipped": "skipped",
                    "untested": "untested"}.get(record["status"])
    if status_state:
        flags.add(status_state)
        reasons.append(f"{slot}: the source reports {record['status']}")

    for state in EVIDENCE_STATES:
        if state in flags:
            return state, reasons
    return "passing", reasons


def _shared_identities(records: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Slots that collide on an exact evidence ref or digest.

    Two layers holding one identity is not two facts; it is one fact counted
    twice, which is exactly the cross-layer inference this slice forbids.
    """
    shared: dict[str, list[str]] = {slot: [] for slot in records}
    for field in ("evidence_ref", "evidence_digest"):
        index: dict[str, list[str]] = {}
        for slot, record in records.items():
            index.setdefault(record[field], []).append(slot)
        for slots in index.values():
            if len(slots) > 1:
                for slot in slots:
                    shared[slot].extend(other for other in slots if other != slot)
    return shared


def _public_evidence(slot: str, state: str, record: dict[str, Any] | None,
                     reasons: list[str]) -> dict[str, Any]:
    layer_class = "preactivation" if slot in PREACTIVATION_SLOTS else "postactivation"
    public: dict[str, Any] = {
        "slot": slot,
        "layer_class": layer_class,
        "state": state,
        "present": record is not None,
        "requirement": SLOT_REQUIREMENT[slot],
        "reasons": sorted(set(reasons)),
    }
    if record is None:
        return public
    public.update({
        "basis": record["basis"],
        "status": record["status"],
        "evidence_ref": record["evidence_ref"],
        "evidence_digest": record["evidence_digest"],
        "evaluator_identity": record["evaluator_identity"],
        "subject_identity": record["subject_identity"],
        "observed_at": record["observed_at"].isoformat(),
        "expires_at": record["expires_at"].isoformat(),
        "bound_scope": dict(record["scope"]),
    })
    for name in SLOT_REQUIRED_FIELDS[slot]:
        value = record[name]
        public[name] = value.isoformat() if isinstance(value, datetime) else value
    return public


def _workflow_truth_facts(workflow_truth: Any, bound: dict[str, Any]) -> tuple[
        dict[str, Any] | None, dict[str, Any]]:
    """Read F09's projected row.  This module derives no workflow truth of its own."""
    if workflow_truth is UNREADABLE:
        return None, {"state": None, "indeterminate": True, "not_enabled": False,
                      "readable": False, "coherent": False, "enabled": False,
                      "live_admissible": False}
    row = _require_mapping(workflow_truth, field="workflow_truth")
    if row.get("schema_version") != WORKFLOW_TRUTH_SCHEMA_VERSION:
        raise AssuranceHealthContractError(
            "workflow_truth must be a row projected by lib/control_plane_workflow_truth "
            f"({WORKFLOW_TRUTH_SCHEMA_VERSION}); this projection builds no second "
            "workflow, status or receipt registry")
    state = row.get("state")
    if state not in WORKFLOW_TRUTH_STATES:
        raise AssuranceHealthContractError(
            f"workflow_truth.state {state!r} is outside the F09 vocabulary")
    key = _require_text(row.get("workflow_key"), field="workflow_truth.workflow_key")
    version = row.get("workflow_version")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise AssuranceHealthContractError(
            "workflow_truth.workflow_version must be a positive integer")
    if key != bound["workflow_key"] or version != bound["workflow_version"]:
        raise AssuranceHealthContractError(
            "workflow_truth row does not bind the exact scope being projected")
    modes = row.get("admissible_modes")
    if not isinstance(modes, list):
        raise AssuranceHealthContractError("workflow_truth.admissible_modes must be a list")
    facts = {
        "state": state,
        "indeterminate": state in _TRUTH_INDETERMINATE,
        "not_enabled": state in _TRUTH_NOT_ENABLED,
        "readable": state != "unknown",
        "coherent": state not in ("conflict", "undeclared"),
        "enabled": bool(row.get("enabled")),
        # F09 owns the evidence ladder: "live" appears only when accepted acceptance
        # evidence earned it, so an enabled row alone can never reach act here.
        "live_admissible": "live" in modes,
    }
    return row, facts


def assurance_health_row(*, scope: Any, workflow_truth: Any, evidence: Any,
                         now: Any) -> dict[str, Any]:
    """Project one exactly bound scope onto one display state and one capability stage.

    ``workflow_truth`` is one row from ``lib.control_plane_workflow_truth`` (or
    ``UNREADABLE``).  ``evidence`` must name every one of the six slots
    explicitly: a caller that did not read a layer says so with ``UNREADABLE``
    rather than inheriting a permissive default.
    """
    bound = scope_identity(scope)
    scope_mapping = _require_mapping(scope, field="scope")
    owner = _require_text(scope_mapping.get("owner"), field="scope.owner")
    incident_refs = _require_refs(scope_mapping.get("incident_refs"), field="scope.incident_refs")
    recovery_refs = _require_refs(scope_mapping.get("recovery_refs"), field="scope.recovery_refs")
    instant = _utc(now, field="now")

    supplied = _require_mapping(evidence, field="evidence")
    unnamed = sorted(set(supplied) - set(EVIDENCE_SLOTS))
    if unnamed:
        raise AssuranceHealthContractError(
            f"evidence carries slots outside the closed set: {unnamed}")
    absent = [slot for slot in EVIDENCE_SLOTS if slot not in supplied]
    if absent:
        raise AssuranceHealthContractError(
            f"evidence must name every layer explicitly; {absent} were not supplied. "
            "Pass UNREADABLE for a layer that could not be read and None for a layer "
            "that was read and has no evidence")

    truth_row, truth = _workflow_truth_facts(workflow_truth, bound)

    records: dict[str, dict[str, Any]] = {}
    states: dict[str, str] = {}
    slot_reasons: dict[str, list[str]] = {slot: [] for slot in EVIDENCE_SLOTS}
    for slot in EVIDENCE_SLOTS:
        value = supplied[slot]
        if value is UNREADABLE:
            states[slot] = "unreadable"
            slot_reasons[slot].append(f"{slot}: the caller could not read this layer")
        elif value is None:
            states[slot] = "missing"
            slot_reasons[slot].append(f"{slot}: read, and there is no evidence")
        else:
            # A record for a layer this scope cannot join is still VALIDATED, so a
            # malformed one is refused rather than excused; _classify then reports
            # the join it cannot make rather than the claim it makes.
            records[slot] = _evidence_record(slot, value)

    for slot, required_identity in SLOT_REQUIRED_SCOPE_IDENTITY.items():
        if slot in records or bound.get(required_identity) is not None:
            continue
        states[slot] = "unbindable"
        slot_reasons[slot].append(
            f"{slot}: this scope carries no {required_identity}, so there is no identity "
            "this layer can be joined through; no receipt can fill it until the binding exists")

    shared = _shared_identities(records)
    for slot, record in records.items():
        state, record_reasons = _classify(record, bound=bound, now=instant, shared=shared)
        states[slot] = state
        slot_reasons[slot].extend(record_reasons)

    # ORDERING COHERENCE. An accepted business outcome for a scope with no current
    # passing activation readback cannot have happened the way it claims; reporting
    # it as passing would let the last slot be earned out of order.
    if states["actual_business_outcome"] == "passing" \
            and states["activation_readback"] != "passing":
        states["actual_business_outcome"] = "conflicting"
        slot_reasons["actual_business_outcome"].append(
            "actual_business_outcome: an accepted business outcome is claimed for a scope "
            "with no current passing activation readback")

    capability_inputs = {
        "workflow_readable": truth["readable"],
        "workflow_coherent": truth["coherent"],
        "workflow_enabled": truth["enabled"],
        "workflow_live_admissible": truth["live_admissible"],
    }
    capability_inputs.update({slot: states[slot] == "passing" for slot in EVIDENCE_SLOTS})

    def _stage(inputs: dict[str, bool]) -> str:
        for name, requirements in _STAGE_REQUIREMENTS:
            if all(inputs[requirement] for requirement in requirements):
                return name
        return "unavailable"

    stage = _stage(capability_inputs)
    stage_index = CAPABILITY_STAGES.index(stage)
    retained = list(CAPABILITY_STAGES[stage_index:-1])
    withdrawn = list(CAPABILITY_STAGES[:stage_index])

    requirements_of = dict(_STAGE_REQUIREMENTS)
    blocking_next = ([] if stage == "act" else
                     sorted(requirement
                            for requirement in requirements_of[CAPABILITY_STAGES[stage_index - 1]]
                            if not capability_inputs[requirement]))
    blocking_act = sorted(requirement for requirement in requirements_of["act"]
                          if not capability_inputs[requirement])

    indeterminate = sorted(slot for slot in EVIDENCE_SLOTS
                           if states[slot] in INDETERMINATE_EVIDENCE_STATES)

    # CAPABILITY LOST TO AN UNREAD LAYER IS NOT CAPABILITY A FAILURE TOOK AWAY.
    # A false red is the same defect as a false green wearing the other colour:
    # both report something the evidence does not say.  So the stage is computed
    # a second time with every INDETERMINATE layer granted, and the difference
    # between the two answers is exactly the capability that is unproven rather
    # than withdrawn.  Only what survives that counterfactual is attributed to
    # the failure; the reported capability_stage stays the conservative one.
    attributable_stage = _stage({**capability_inputs,
                                 **{slot: True for slot in indeterminate}})
    determinate_nonpass = sorted(slot for slot in EVIDENCE_SLOTS
                                 if states[slot] in DETERMINATE_NONPASS_EVIDENCE_STATES)
    missing = sorted(slot for slot in EVIDENCE_SLOTS if states[slot] == "missing")
    unbindable = sorted(slot for slot in EVIDENCE_SLOTS if states[slot] == "unbindable")
    preactivation_passing = all(states[slot] == "passing" for slot in PREACTIVATION_SLOTS)

    reasons: list[str] = []
    for slot in EVIDENCE_SLOTS:
        reasons.extend(slot_reasons[slot])

    # ---- display state, most conservative predicate first --------------------
    if truth["indeterminate"]:
        state = "unknown"
        state_reason = (
            "authoritative workflow truth is indeterminate "
            f"(F09 state {truth['state']!r}); nothing is claimed about this scope")
    elif truth["not_enabled"]:
        # Only F09 emits disabled, and a deliberately chosen state is not a failure.
        # Any evidence finding stays visible in evidence/reasons below.
        state = "disabled"
        state_reason = "F09 authoritative workflow truth reports the definition is not enabled"
    elif determinate_nonpass and attributable_stage == "unavailable":
        state = "failed"
        state_reason = (
            f"a current exactly bound non-pass in {determinate_nonpass} withdrew every "
            "capability of this scope")
    elif determinate_nonpass:
        state = "degraded"
        state_reason = (
            f"a current exactly bound non-pass in {determinate_nonpass} withdrew "
            f"{_STAGE_DOWN_TO[attributable_stage]} capability; {attributable_stage} is what "
            "the failure itself leaves standing")
        if stage != attributable_stage:
            state_reason += (
                f". Capability below {attributable_stage} is unproven rather than withdrawn: "
                f"{indeterminate} could not be read, so {retained} is what this scope can "
                "currently be shown to hold")
    elif not determinate_nonpass and not indeterminate and (
            not truth["live_admissible"]
            or any(states[slot] in UNEARNED_EVIDENCE_STATES for slot in POSTACTIVATION_SLOTS)):
        state = "not-yet-operational"
        state_reason = (
            "nothing failed and nothing is operational yet: "
            + ("the F09 evidence ladder has not admitted live execution"
               if not truth["live_admissible"] else
               f"{sorted(slot for slot in POSTACTIVATION_SLOTS if states[slot] in UNEARNED_EVIDENCE_STATES)} "
               "has no evidence the bound scope can yet carry"))
    elif stage == "act":
        state = "healthy"
        state_reason = (
            "six distinct, current, independently evaluated, exactly bound passing facts "
            "and F09 live admission")
    else:
        state = "unknown"
        state_reason = (
            f"evidence is incomplete or indeterminate (indeterminate={indeterminate}, "
            f"missing={missing}); no state is claimed")
    reasons.append(state_reason)

    if state != GREEN_STATE and stage == "act":  # pragma: no cover - guards a future edit
        raise AssuranceHealthContractError(
            "full act capability must be reported as healthy or not at all")
    if state == GREEN_STATE and not all(states[slot] == "passing" for slot in EVIDENCE_SLOTS):
        raise AssuranceHealthContractError(  # pragma: no cover - guards a future edit
            "healthy requires every layer to be passing")

    impact = {
        "capability_stage": stage,
        "capabilities_retained": retained,
        "capabilities_withdrawn": withdrawn,
        "blocking_next_stage": blocking_next,
        "blocking_act": blocking_act,
        # A failure is fenced to exactly this identity; no other scope is touched.
        "scope_limited_to": dict(bound),
    }
    recovery = {
        "owner": owner,
        "required_evidence": [
            {"slot": slot, "evidence_state": states[slot], "requirement": SLOT_REQUIREMENT[slot]}
            for slot in EVIDENCE_SLOTS if states[slot] != "passing"],
        "incident_refs": incident_refs,
        "recovery_refs": recovery_refs,
        "authority_path": (
            "the bound owner obtains the named evidence through its existing authority; "
            "this projection performs no activation, effect or notification"),
    }

    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": instant.isoformat(),
        "scope": {
            **bound,
            "completion_subject_key": completion_subject_key(
                bound["workflow_key"], bound["workflow_version"]),
            "owner": owner,
        },
        "state": state,
        "green": state == GREEN_STATE,
        "state_reason": state_reason,
        "capability_stage": stage,
        "capabilities_retained": retained,
        "capabilities_withdrawn": withdrawn,
        "workflow_truth": (
            {"source": "lib/control_plane_workflow_truth.py", "available": False}
            if truth_row is None else
            {"source": "lib/control_plane_workflow_truth.py", "available": True,
             **{name: truth_row.get(name) for name in _WORKFLOW_TRUTH_PUBLIC_FIELDS}}),
        "evidence": {slot: _public_evidence(slot, states[slot], records.get(slot),
                                            slot_reasons[slot])
                     for slot in EVIDENCE_SLOTS},
        "preactivation_receipts_passing": preactivation_passing,
        "indeterminate_layers": indeterminate,
        "failing_layers": determinate_nonpass,
        "missing_layers": missing,
        "unbindable_layers": unbindable,
        "capability_stage_attributable_to_findings": attributable_stage,
        "impact": impact,
        "recovery": recovery,
        "reasons": sorted(set(reasons)),
    }

    if row["state"] not in DISPLAY_STATES:  # pragma: no cover - guards a future edit
        raise AssuranceHealthContractError(
            f"state {row['state']!r} is outside the closed vocabulary")
    if row["capability_stage"] not in CAPABILITY_STAGES:  # pragma: no cover
        raise AssuranceHealthContractError(
            f"capability stage {row['capability_stage']!r} is outside the closed vocabulary")
    for slot, public in row["evidence"].items():  # pragma: no cover
        if public["state"] not in EVIDENCE_STATES:
            raise AssuranceHealthContractError(
                f"{slot} evidence state {public['state']!r} is outside the vocabulary")
    return row


def assurance_health(*, scopes: Any, now: Any) -> dict[str, Any]:
    """Project every explicitly bound scope, in a stable order.

    Each entry is ``{"scope": ..., "workflow_truth": ..., "evidence": ...}``.  Rows
    are computed independently of one another: a failure in one scope has no route
    by which it could alter another scope's evidence-derived state.
    """
    instant = _utc(now, field="now")
    if not isinstance(scopes, list):
        raise AssuranceHealthContractError("scopes must be a list of bound scope bundles")

    rows = []
    seen: set[tuple[str, int, str]] = set()
    for entry in scopes:
        bundle = _require_mapping(entry, field="scope bundle")
        for name in ("scope", "workflow_truth", "evidence"):
            if name not in bundle:
                raise AssuranceHealthContractError(f"scope bundle is missing {name!r}")
        identity = scope_identity(bundle["scope"])
        key = (identity["workflow_key"], identity["workflow_version"],
               identity["work_request_id"])
        if key in seen:
            raise AssuranceHealthContractError(
                f"scope {key} is bound twice; one exact scope has one projected state")
        seen.add(key)
        rows.append(assurance_health_row(
            scope=bundle["scope"], workflow_truth=bundle["workflow_truth"],
            evidence=bundle["evidence"], now=instant))

    # An unbound scope sorts before its Work Request-bound siblings; None is not
    # orderable against a string, so the absence is spelled out rather than
    # crashing the whole census on one unbound row.
    rows.sort(key=lambda row: (row["scope"]["workflow_key"], row["scope"]["workflow_version"],
                               row["scope"]["work_request_id"] is not None,
                               row["scope"]["work_request_id"] or ""))
    summary = {
        "scopes": len(rows),
        "states": {state: sum(1 for row in rows if row["state"] == state)
                   for state in DISPLAY_STATES},
        "capability_stages": {stage: sum(1 for row in rows if row["capability_stage"] == stage)
                              for stage in CAPABILITY_STAGES},
        "green": sum(1 for row in rows if row["green"]),
        "degraded_scopes": sorted(row["scope"]["work_request_id"] for row in rows
                                  if row["state"] in ("degraded", "failed")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": instant.isoformat(),
        "states": list(DISPLAY_STATES),
        "capability_stages": list(CAPABILITY_STAGES),
        "evidence_slots": list(EVIDENCE_SLOTS),
        "workflow_truth_source": "lib/control_plane_workflow_truth.py",
        "rows": rows,
        "summary": summary,
    }
