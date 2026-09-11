"""Bind the A01 assurance-health projection to readings a health surface already has.

WHAT THIS IS.  A pure function from ONE ALREADY-READ workflow-truth snapshot --
the exact section ``tools/health-check.py`` builds for its workflow census -- to
the bound scopes ``lib/assurance_health`` projects.  It opens no file, runs no
query, starts no process and takes no clock: every fact it uses was read by the
caller before this module was entered, and the caller passes its own instant in.

WHY IT EXISTS AS ITS OWN SEAM.  ``lib/assurance_health`` is a closed domain that
refuses to guess, and the health surface is a reader that already refuses to
print a chosen state as a failure.  Between them sits exactly one question --
*which of the six evidence layers did this reading actually contain?* -- and
answering it inside either one would put a second opinion in a place that must
have none.  So it lives here, it is the only place that answers it, and it
answers it the same way for every caller.

THE ONE RULE IT ENFORCES.  A LAYER THIS READING DID NOT CONTAIN IS DECLARED
UNREAD, NEVER DEFAULTED.  The canonical health snapshot reads the control-plane
workflow rows, the scheduler observation receipts and the Completion Register.
It does NOT read independent artifact reviews, attempt receipts, candidate
outcome oracles, activation readbacks or accepted outcome feedback.  Each of
those five is therefore handed to the projection as ``UNREADABLE`` together with
the name of the surface that would have to supply it -- so the resulting row can
reach ``unknown``, ``disabled`` or ``not-yet-operational`` and can never reach
green.  That is the point: the gap becomes a named gap instead of a silence.

THE ONE LAYER THIS READING GENUINELY CARRIES is the controller readback, and it
is admitted under a decision procedure rather than a judgement:

  1. Collect the registered scheduler surfaces of the exact workflow identity
     that carry BOTH a ``scheduler_state`` and an ``observed_at``.
  2. Zero -- the read happened and there is no receipt: hand over ``None``
     (genuinely absent), which is not the same fact as an unread layer.
  3. More than one -- two receipts are two identities, and one controller
     assessment needs one: hand over ``UNREADABLE`` naming both surface ids.
     Picking either would be the cross-layer guessing this slice removes.
  4. Exactly one -- build the evidence record from it, with the expiry taken
     from the registry's OWN ``observation_max_age_seconds``.  This module
     defines no freshness window of its own.

WHAT IT REFUSES TO INVENT.  An owner is read from the checked-in workflow
manifest (``inventory.owner``); a workflow that declares none is reported as
unprojectable, naming the exact missing field, rather than being given a
placeholder owner so that a row can be printed.  A Work Request identity is
never synthesised: the census carries none, so every scope it binds is a
workflow-only scope whose business-outcome layer is ``unbindable`` and whose
green state is therefore unreachable by construction.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from lib.assurance_health import (
    EVIDENCE_SLOTS,
    AssuranceHealthContractError,
    assurance_health,
)
from lib.control_plane_workflow_truth import (
    NATIVE_SCHEDULER_STATES,
    SCHEMA_VERSION as WORKFLOW_TRUTH_SCHEMA_VERSION,
    UNREADABLE,
)

SCHEMA_VERSION = "assurance-health-sources.v1"

# The exact source each layer this reading does NOT contain would have to come
# from.  Naming it is the whole difference between an unread layer and a silence.
UNREAD_LAYER_SOURCE = {
    "artifact_assessment":
        "an independent artifact review bound to this scope's exact repository commit "
        "and tree; the canonical health snapshot reads no reviewed-artifact surface",
    "execution_assessment":
        "an attempt receipt with its envelope digest and plan hash; the canonical health "
        "snapshot reads job rows, which are not that receipt",
    "candidate_outcome_oracle":
        "a preactivation candidate-outcome oracle receipt; the canonical health snapshot "
        "reads no oracle surface",
    "activation_readback":
        "a controller readback taken after activation, carrying its activation id; the "
        "canonical health snapshot reads no activation surface",
    "actual_business_outcome":
        "an accepted sourced outcome-feedback receipt joined through a Work Request "
        "identity; the canonical health snapshot reads no outcome-feedback surface",
}

# The identity that writes a scheduler observation receipt.  It is named as a
# constant so the evaluator of this evidence can never silently become the
# subject it assessed.
_OBSERVATION_EVALUATOR = "receipt-producer:ops.legacy_schedule_observation_receipt"


def _workflow_identity(key: Any, version: Any) -> str:
    return f"{key}@v{version}"


def _observation_records(surfaces: Any, key: Any, version: Any) -> tuple[
        list[tuple[dict[str, Any], dict[str, Any]]], list[str]]:
    """Every readback this reading holds for one exact workflow identity."""
    if not isinstance(surfaces, list):
        return [], []
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    ids: list[str] = []
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        if surface.get("workflow_key") != key or surface.get("workflow_version") != version:
            continue
        observation = surface.get("observation")
        if not isinstance(observation, dict):
            continue
        if not observation.get("scheduler_state") or not observation.get("observed_at"):
            continue
        found.append((surface, observation))
        ids.append(str(surface.get("surface_id")))
    return found, sorted(ids)


def _controller_evidence(surfaces: Any, key: Any, version: Any, max_age_seconds: int,
                         notes: list[str]) -> Any:
    found, ids = _observation_records(surfaces, key, version)
    if not found:
        notes.append(
            "controller_assessment: this reading holds no scheduler observation receipt for "
            "this workflow; the layer is absent, not unread")
        return None
    if len(found) > 1:
        notes.append(
            f"controller_assessment: {len(found)} scheduler observation receipts "
            f"({', '.join(ids)}) each bind this workflow; one controller assessment needs one "
            "exact identity, and choosing between them here would be a guess")
        return UNREADABLE

    surface, observation = found[0]
    surface_id = str(surface.get("surface_id"))
    state = str(observation["scheduler_state"])
    observed_at = str(observation["observed_at"])
    try:
        from lib.assurance_health import _utc  # noqa: PLC0415 - one shared parser, not a second
        expires_at = (_utc(observed_at, field="observation.observed_at")
                      + timedelta(seconds=int(max_age_seconds))).isoformat()
    except (AssuranceHealthContractError, TypeError, ValueError) as exc:
        notes.append(
            f"controller_assessment: {surface_id} carries an observation this reading cannot "
            f"place in time ({exc}); an undatable readback is unread, never current")
        return UNREADABLE
    return {
        "layer": "controller_assessment",
        "basis": "controller_readback",
        # A readback that came back with a state the provider vocabulary declares is a
        # completed reading. Anything else did not complete, and says so.
        "status": "pass" if state in NATIVE_SCHEDULER_STATES else "error",
        "evidence_ref": f"observation-receipt:{surface_id}",
        "evidence_digest": f"observation-receipt:{surface_id}@{observed_at}",
        "evaluator_identity": _OBSERVATION_EVALUATOR,
        "subject_identity": f"scheduler-surface:{surface_id}",
        "observed_at": observed_at,
        # The registry's own window, never one invented here.
        "expires_at": expires_at,
        "scope": {"workflow_key": key, "workflow_version": version},
        "controller_state": state,
        "readback_source": f"ops.legacy_schedule_observation_receipt:{surface_id}",
        "readback_at": observed_at,
    }


def _owner(owners: Any, key: str, version: int) -> str | None:
    if not isinstance(owners, dict):
        return None
    owner = owners.get(_workflow_identity(key, version))
    return owner if isinstance(owner, str) and owner.strip() else None


def assurance_health_scopes(workflows: Any) -> dict[str, Any]:
    """Turn one already-read workflow-truth snapshot section into bound scopes.

    Returns ``{"available": False, "reason": ...}`` when the reading itself is
    absent -- an unavailable reading is reported as unavailable and never as an
    empty census.
    """
    if not isinstance(workflows, dict):
        return {"available": False, "reason": "no workflow-truth reading was supplied"}
    if not workflows.get("available"):
        return {"available": False,
                "reason": str(workflows.get("reason") or "the workflow census is unavailable")}
    census = workflows.get("census")
    if not isinstance(census, dict) or census.get("schema_version") != \
            WORKFLOW_TRUTH_SCHEMA_VERSION:
        return {"available": False,
                "reason": "the workflow census is not a row set projected by "
                          f"lib/control_plane_workflow_truth ({WORKFLOW_TRUTH_SCHEMA_VERSION})"}
    rows = census.get("rows")
    if not isinstance(rows, list):
        return {"available": False, "reason": "the workflow census carries no rows"}

    # The census carries the registry's own window. Reading it from there rather
    # than from the caller keeps one declared freshness window in the system.
    max_age = census.get("observation_max_age_seconds")
    if not isinstance(max_age, int) or isinstance(max_age, bool) or max_age <= 0:
        return {"available": False,
                "reason": "the census carries no observation_max_age_seconds; this module "
                          "defines no freshness window of its own"}

    surfaces = workflows.get("surfaces")
    owners = workflows.get("owners")

    scopes, unprojectable, notes = [], [], {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key, version = row.get("workflow_key"), row.get("workflow_version")
        if not isinstance(key, str) or not isinstance(version, int) or isinstance(version, bool):
            continue
        identity = _workflow_identity(key, version)
        owner = _owner(owners, key, version)
        if owner is None:
            unprojectable.append({
                "workflow": identity,
                "reason": "ops/config/control-plane-workflows.v1.json declares no "
                          "inventory.owner for this workflow, and an owner is never invented"})
            continue
        row_notes: list[str] = []
        evidence: dict[str, Any] = {
            slot: UNREADABLE for slot in EVIDENCE_SLOTS if slot in UNREAD_LAYER_SOURCE}
        for slot, source in UNREAD_LAYER_SOURCE.items():
            row_notes.append(f"{slot}: not read by this surface — it would come from {source}")
        evidence["controller_assessment"] = _controller_evidence(
            surfaces, key, version, max_age, row_notes)
        scopes.append({
            # No work_request_id: the census carries none, so this is a workflow-only
            # scope and its business-outcome layer is unbindable by construction.
            "scope": {"workflow_key": key, "workflow_version": version, "owner": owner},
            "workflow_truth": row,
            "evidence": evidence,
        })
        notes[identity] = row_notes

    return {"available": True, "scopes": scopes, "unprojectable": unprojectable,
            "input_notes": notes}


def assurance_health_from_snapshot(workflows: Any, *, now: Any) -> dict[str, Any]:
    """Project the assurance-health census for one already-read snapshot section."""
    bound = assurance_health_scopes(workflows)
    if not bound["available"]:
        return {"schema_version": SCHEMA_VERSION, "available": False,
                "reason": bound["reason"]}
    try:
        projection = assurance_health(scopes=bound["scopes"], now=now)
    except AssuranceHealthContractError as exc:
        # The domain refused an input this adapter built. That is this adapter's
        # defect, and it is reported as one rather than printed as a health state.
        return {"schema_version": SCHEMA_VERSION, "available": False,
                "reason": f"the assurance-health projection refused these inputs ({exc})"}
    return {
        "schema_version": SCHEMA_VERSION,
        "available": True,
        "projection": projection,
        "unprojectable": bound["unprojectable"],
        "input_notes": bound["input_notes"],
    }
