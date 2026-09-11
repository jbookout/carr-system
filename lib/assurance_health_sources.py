"""Bind the A01 assurance-health projection to the F09 census this seam READS.

WHAT THIS IS.  One public function, ``assurance_health_census()``, which takes no
arguments, reads the V5-F09 workflow census from the control plane that owns it
through ``lib/control_plane_workflow_truth_reader`` -- the same reader
``tools/health-check.py --canonical`` renders its own F09 section from -- and
projects the bound scopes ``lib/assurance_health`` turns into health states.

THE CORRECTION THIS MODULE MOST RECENTLY TOOK, and it is the reason the shape
changed.  This module used to export ``assurance_health_scopes(workflows)`` and
``assurance_health_from_snapshot(workflows, now=...)``: both ACCEPTED A CENSUS
FROM THEIR CALLER, validated its schema version and each row's key and version,
and then returned the caller's own row back as ``workflow_truth``.  A review
handed those functions a census it had written by hand and reproduced

    {"state": "healthy", "green": true, "capability_stage": "act"}

through the exported adapter.  Nothing in that chain was a reading.  The domain
module's later refusals did not close the route, because the route was already
public and already answered.  A caller-supplied census is the caller's assertion
about the control plane, exactly as a caller-supplied receipt is its assertion
about a receipt store, and SHAPE IS NEVER AUTHORITY.

SO THE ONLY CALLER INPUT IS THE REQUEST TO READ.  ``assurance_health_census()``
has no parameter through which a census, a row, a clock or a path could arrive:
it performs the read itself and takes its own instant.  The classification logic
that turns a reading into scopes is module-private
(``_assurance_health_scopes``, ``_project``), and the acceptance suite reaches it
for fixture censuses ONLY through ``_would_be_assurance_health_if_authoritative``
at the foot of this module, which is unexported and returns its answer under a
hypothetical name that no consumer can mistake for a state this surface read.
``ops/assurance-health-selftest.py :: sources_public_surface_guard_checks``
parses this file with ``ast`` and fails if any of that becomes public again.

THE ONE RULE IT ENFORCES ON THE READING.  A LAYER THIS READING DID NOT CONTAIN
IS DECLARED UNREAD, NEVER DEFAULTED.  The F09 census reads the control-plane
workflow rows, the scheduler observation receipts and the Completion Register.
It does NOT read independent artifact reviews, attempt receipts, candidate
outcome oracles, activation readbacks or accepted outcome feedback.  Each of
those is therefore handed to the projection as ``UNREADABLE`` together with the
name of the surface that would have to supply it -- so the resulting row can
reach ``unknown``, ``disabled`` or ``not-yet-operational`` and can never reach
green.  That is the point: the gap becomes a named gap instead of a silence.

AND THE CONTROLLER READBACK IS ONE OF THEM.  Reading the observation receipts
out of the control plane is not the same act as ADMITTING one as current
controller evidence: the admitting surface -- whoever vouches that this receipt
is the live state of that scheduler -- does not exist in this repository.  So
the decision procedure is the same one every other layer gets, and it still
distinguishes the cases a reader needs to see:

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

# EVERY IMPORT IS UNDERSCORED, AND THAT IS THE POINT.  An imported name is a
# PUBLIC name of the importing module: re-exporting ``assurance_health`` here
# would put a second callable that accepts caller input on this module's surface,
# and re-exporting the reader would invite a consumer to perform the read and
# hand the result back in.  Aliasing them out makes the claim in ``__all__``
# literally true -- this module's public surface is one callable that accepts
# nothing -- and lets the acceptance suite assert exactly that.  A caller that
# wants the domain or the reader imports it from the module that owns it.
from datetime import datetime as _datetime, timezone as _timezone
from typing import Any as _Any

from lib.assurance_health import (
    EVIDENCE_SLOTS as _EVIDENCE_SLOTS,
    OWED_EVIDENCE_OWNER_SEAMS as _OWED_EVIDENCE_OWNER_SEAMS,
    AssuranceHealthContractError as _AssuranceHealthContractError,
    assurance_health as _assurance_health,
)
from lib.control_plane_workflow_truth import (
    SCHEMA_VERSION as _WORKFLOW_TRUTH_SCHEMA_VERSION,
    UNREADABLE as _UNREADABLE,
)
from lib.control_plane_workflow_truth_reader import (
    read_workflow_truth_snapshot as _read_workflow_truth_snapshot,
)

SCHEMA_VERSION = "assurance-health-sources.v1"

# THE PUBLIC SURFACE, EXHAUSTIVELY.  One callable, and it accepts nothing.
# Everything that turns a reading into a state is absent from this list.
__all__ = [
    "SCHEMA_VERSION",
    "UNREAD_LAYER_SOURCE",
    "assurance_health_census",
]

# The exact source each layer this reading does NOT contain would have to come
# from.  Naming it is the whole difference between an unread layer and a silence.
UNREAD_LAYER_SOURCE = {
    "artifact_assessment":
        "an independent artifact review bound to this scope's exact repository commit "
        "and tree; the F09 census reads no reviewed-artifact surface",
    "execution_assessment":
        "an attempt receipt with its envelope digest and plan hash; the F09 census "
        "reads job rows, which are not that receipt",
    "candidate_outcome_oracle":
        "a preactivation candidate-outcome oracle receipt; the F09 census reads no "
        "oracle surface",
    "activation_readback":
        "a controller readback taken after activation, carrying its activation id; the "
        "F09 census reads no activation surface",
    "actual_business_outcome":
        "an accepted sourced outcome-feedback receipt joined through a Work Request "
        "identity; the F09 census reads no outcome-feedback surface",
    "controller_assessment":
        "a surface that ADMITS a reading of ops.legacy_schedule_observation_receipt as "
        "the live state of that scheduler; reading the receipt rows, which this seam "
        "does, is not the same act as vouching that one of them is current",
}


def _workflow_identity(key: _Any, version: _Any) -> str:
    return f"{key}@v{version}"


def _observation_records(surfaces: _Any, key: _Any, version: _Any) -> tuple[
        list[tuple[dict[str, _Any], dict[str, _Any]]], list[str]]:
    """Every readback this reading holds for one exact workflow identity."""
    if not isinstance(surfaces, list):
        return [], []
    found: list[tuple[dict[str, _Any], dict[str, _Any]]] = []
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


def _controller_evidence(surfaces: _Any, key: _Any, version: _Any,
                         notes: list[str]) -> _Any:
    """Always UNREAD, and specific about what this reading actually held.

    There is no evidence owner for this layer in this repository, so no count of
    receipts -- read here or supplied by anybody -- can make it current.  What
    the count still earns is an accurate note: a reader learns whether the
    reading held no receipt, one, or two that no admission could have chosen
    between.
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
                 f"{_OWED_EVIDENCE_OWNER_SEAMS['controller_assessment']}")
    return _UNREADABLE


def _owner(owners: _Any, key: str, version: int) -> str | None:
    if not isinstance(owners, dict):
        return None
    owner = owners.get(_workflow_identity(key, version))
    return owner if isinstance(owner, str) and owner.strip() else None


def _assurance_health_scopes(workflows: _Any) -> dict[str, _Any]:
    """Turn one workflow-truth reading into bound scopes.  MODULE-PRIVATE.

    Private because its ``workflow_truth`` output is its input row travelling
    onward: a caller that could reach this could choose what the projection then
    classifies.  ``assurance_health_census`` supplies it a reading it performed
    itself, and nothing else supplies it at all.

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
            _WORKFLOW_TRUTH_SCHEMA_VERSION:
        return {"available": False,
                "reason": "the workflow census is not a row set projected by "
                          f"lib/control_plane_workflow_truth ({_WORKFLOW_TRUTH_SCHEMA_VERSION})"}
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
        evidence: dict[str, _Any] = {slot: _UNREADABLE for slot in _EVIDENCE_SLOTS}
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


def _project(workflows: _Any, *, now: _Any) -> dict[str, _Any]:
    """Project the assurance-health census for one reading.  MODULE-PRIVATE."""
    bound = _assurance_health_scopes(workflows)
    if not bound["available"]:
        return {"schema_version": SCHEMA_VERSION, "available": False,
                "reason": bound["reason"]}
    try:
        projection = _assurance_health(scopes=bound["scopes"], now=now)
    except _AssuranceHealthContractError as exc:
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


def assurance_health_census() -> dict[str, _Any]:
    """Read the F09 census from the control plane and project assurance health.

    THE ONLY PUBLIC ENTRY, AND IT ACCEPTS NOTHING.  No census, no rows, no
    surfaces, no owners, no clock, no path: the one caller input is the request
    to read.  The reading is performed by
    ``lib/control_plane_workflow_truth_reader.read_workflow_truth_snapshot`` --
    the same one ``tools/health-check.py --canonical`` renders its F09 section
    from -- and the instant is this function's own.

    Returns ``{"available": False, "reason": ...}`` whenever the reading refused,
    which is what a machine with no database tap gets.  An absent reading is
    reported absent; it is never projected as an empty or a healthy census.
    """
    return _project(_read_workflow_truth_snapshot(), now=_datetime.now(_timezone.utc))


def _would_be_assurance_health_if_authoritative(workflows: _Any, *, now: _Any) -> dict[str, _Any]:
    """TEST-ONLY, MODULE-PRIVATE, and named for what its answer is worth.

    The acceptance suite has to drive the classification over fixture censuses --
    an ownerless workflow, a disabled one, two receipts for one identity, a
    receipt outside the observation window -- and it cannot read a control plane
    to get them.  So it reaches the private projection through this hook, whose
    result is returned under ``would_be_census_if_authoritative``: a name no
    consumer can read as a state this surface actually holds, because no surface
    holds it.  The hook is absent from ``__all__``, starts with an underscore,
    and is called from nowhere but ``ops/assurance-health-selftest.py``.

    What it does NOT do is widen the public surface.  A caller reaching a private
    name is not an exported route, and the guard in the acceptance suite fails
    the moment this name loses its underscore or enters ``__all__``.
    """
    return {"schema_version": SCHEMA_VERSION,
            "authoritative": False,
            "would_be_census_if_authoritative": _project(workflows, now=now)}
