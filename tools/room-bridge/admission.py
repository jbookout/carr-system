"""Fail-closed server admission seam for CARR ExecutionEnvelope v1.

This module does not query or mutate a store.  Its caller must load the
existing Work Request, Plan Revision, Agent Session, identity, authority, and
adapter records under the owning server transaction, then pass only those
server-derived records here.  It creates no task, route, or authority source.
"""
from __future__ import annotations

from typing import Any

import execution_contract as contract


ADMISSION_FIELDS = {"envelope_id", "issued_at", "expires_at", "work_request", "plan_revision", "agent_session", "request", "server_binding", "handoff", "phase_binding", "evaluation_context"}
WORK_FIELDS = {"work_request_id", "state_version", "canonical_record_digest", "accepted_resource_revisions"}


def admit_execution_envelope(record: Any) -> dict:
    """Build an envelope solely from server-derived canonical records.

    Caller/model supplied identity, environment, risk, capability, adapter,
    and state fields are not accepted because the input is closed and every
    source carries ``derived_by`` proof.  A stale plan/session binding fails
    before an envelope can reach a harness.
    """
    value = contract._expect_exact(record, ADMISSION_FIELDS, "execution admission")
    work = contract._expect_exact(value["work_request"], WORK_FIELDS, "admission work request")
    for key in ("work_request_id",): contract._string(work[key], f"admission {key}", identifier=True)
    if not isinstance(work["state_version"], int) or work["state_version"] < 1: raise contract.ContractError("admission state_version must be positive")
    contract._digest(work["canonical_record_digest"], "admission canonical_record_digest")
    if not isinstance(work["accepted_resource_revisions"], list): raise contract.ContractError("admission accepted_resource_revisions must be list")
    for row in work["accepted_resource_revisions"]:
        contract._expect_exact(row, {"resource_ref", "revision_ref", "digest"}, "admission resource revision")
    for name in ("plan_revision", "agent_session", "server_binding"):
        if not isinstance(value[name], dict): raise contract.ContractError(f"admission {name} must be server record")
    binding = value["server_binding"]
    identity = binding.get("identity") if isinstance(binding, dict) else None
    authority = binding.get("authority") if isinstance(binding, dict) else None
    if not isinstance(identity, dict) or identity.get("derived_by") != "server_identity_resolution" or identity.get("client_mutable") is not False:
        raise contract.ContractError("admission identity must be server-derived")
    if not isinstance(authority, dict) or authority.get("derived_by") != "server_capability_resolution" or authority.get("client_mutable") is not False:
        raise contract.ContractError("admission authority must be server-derived")
    envelope = {"schema_version": "execution-envelope.v1", "envelope_id": value["envelope_id"], "work_request_id": work["work_request_id"], "plan_revision": value["plan_revision"], "agent_session": value["agent_session"], "issued_at": value["issued_at"], "expires_at": value["expires_at"], "request": value["request"], "server_binding": binding, "handoff": value["handoff"], "state_binding": {"state_version": work["state_version"], "canonical_record_digest": work["canonical_record_digest"], "accepted_resource_revisions": work["accepted_resource_revisions"], "compare_and_swap_required": True}, "phase_binding": value["phase_binding"], "evaluation_context": value["evaluation_context"]}
    return contract.validate_execution_envelope(envelope)
