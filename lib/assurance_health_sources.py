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
those is therefore handed to the projection as ``UNREADABLE`` together with the
name of the surface that would have to supply it -- so the resulting row can
reach ``unknown``, ``disabled`` or ``not-yet-operational`` and can never reach
green.  That is the point: the gap becomes a named gap instead of a silence.

AND THE CONTROLLER READBACK IS ONE OF THEM, which is the correction this module
most recently took.  An earlier cut treated the scheduler observation receipts in
the snapshot as the one layer the reading "genuinely carried", and handed them to
an admission function in ``lib/assurance_health`` that minted passing controller
evidence from them.  A review then reproduced ``controller_assessment: passing``
from a snapshot written by hand: nothing in that chain was a read, because THIS
MODULE READS NOTHING AND ITS CALLER SUPPLIES THE SNAPSHOT.  The shape of an F09
row was standing in for the authority of the store behind it.

So the decision procedure is now the same one every other layer gets, and it
still distinguishes the cases a reader needs to see:

  1. Collect the registered scheduler surfaces of the exact workflow identity
     that carry BOTH a ``scheduler_state`` and an ``observed_at``.
  2. Whatever the count, hand the layer over as ``UNREADABLE``: no evidence owner
     for it exists in this repository, so nothing here can make it current.
  3. Say in the notes WHAT this reading held -- no receipt, exactly one, or two
     that would have been ambiguous anyway -- and name the seam that is owed
     before any of them could become evidence.  The count is diagnostic, and it
     is never the difference between unread and passing.

WHAT IT REFUSES TO INVENT.  An owner is read from the checked-in workflow
manifest (``inventory.owner``); a workflow that declares none is reported as
unprojectable, naming the exact missing field, rather than being given a
placeholder owner so that a row can be printed.  A Work Request identity is
never synthesised: the census carries none, so every scope it binds is a
workflow-only scope whose business-outcome layer is ``unbindable`` and whose
green state is therefore unreachable by construction.
"""
from __future__ import annotations

from typing import Any

from lib.assurance_health import (
    EVIDENCE_SLOTS,
    OWED_EVIDENCE_OWNER_SEAMS,
    AssuranceHealthContractError,
    assurance_health,
)
from lib.control_plane_workflow_truth import (
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
    "controller_assessment":
        "a reading of ops.legacy_schedule_observation_receipt performed by whoever "
        "admits it; the snapshot's observation entries are supplied to this module, "
        "and a supplied receipt is its caller's assertion rather than a reading",
}


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


def _controller_evidence(surfaces: Any, key: Any, version: Any,
                         notes: list[str]) -> Any:
    """Always UNREAD, and specific about what this reading actually held.

    There is no evidence owner for this layer in this repository, so no count of
    receipts in a caller-supplied snapshot can make it current.  What the count
    still earns is an accurate note: a reader learns whether the snapshot held no
    receipt, one, or two that no admission could have chosen between.
    """
    _, ids = _observation_records(surfaces, key, version)
    if not ids:
        held = "this reading holds no scheduler observation receipt for this workflow"
    elif len(ids) == 1:
        held = f"this reading holds one scheduler observation receipt ({ids[0]})"
    else:
        held = (f"this reading holds {len(ids)} scheduler observation receipts "
                f"({', '.join(ids)}), which one controller assessment could not have "
                "chosen between in any case")
    notes.append(f"controller_assessment: {held}; it is UNREAD, not current — "
                 f"{OWED_EVIDENCE_OWNER_SEAMS['controller_assessment']}")
    return UNREADABLE


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
        # EVERY layer is unread on this surface, so every slot is named and none
        # is defaulted.  The controller layer gets its own note because this
        # reading may actually be holding receipts that still are not evidence.
        evidence: dict[str, Any] = {slot: UNREADABLE for slot in EVIDENCE_SLOTS}
        for slot, source in UNREAD_LAYER_SOURCE.items():
            if slot == "controller_assessment":
                continue
            row_notes.append(f"{slot}: not read by this surface — it would come from {source}")
        evidence["controller_assessment"] = _controller_evidence(
            surfaces, key, version, row_notes)
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
