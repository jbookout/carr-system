"""Pure post-provider contract for the content-fuel cognition boundary.

Rotation selection is deterministic: exactly one local and one cold lane.  A
provider may then propose candidates, but only this contract decides whether a
candidate is retained in the proposal.  In particular, source classification
belongs to the selected candidate, not to a pre-provider lane label.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


class ContentFuelContractError(ValueError):
    """The provider proposal is not safe to retain as content-fuel evidence."""


_IMMUTABLE_SOURCE_REF = re.compile(r"^(?:content|document|record|source):[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SOURCE_CLASSES = frozenset({"primary", "secondary"})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentFuelContractError(f"{field} must be a non-empty string")
    return value.strip()


def _immutable_ref(value: Any, field: str) -> str:
    ref = _text(value, field)
    if not _IMMUTABLE_SOURCE_REF.fullmatch(ref):
        raise ContentFuelContractError(f"{field} must be an immutable canonical source_ref")
    return ref


def _citations(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContentFuelContractError("citation_refs must be a non-empty list")
    refs = [_text(item, "citation_refs item") for item in value]
    if len(set(refs)) != len(refs):
        raise ContentFuelContractError("citation_refs must not contain duplicates")
    return refs


def validate_rotation_policy(rotation: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Accept only the deterministic selected local+cold rotation."""
    lanes = rotation.get("lanes") if isinstance(rotation, Mapping) else None
    if not isinstance(lanes, Sequence) or isinstance(lanes, (str, bytes)) or len(lanes) != 2:
        raise ContentFuelContractError("rotation policy must select exactly two lanes")
    normalized: list[dict[str, str]] = []
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ContentFuelContractError("rotation lane must be an object")
        normalized.append({"lane": _text(lane.get("lane"), "lane"),
                           "temperature": _text(lane.get("temperature"), "temperature")})
    if {lane["temperature"] for lane in normalized} != {"local", "cold"}:
        raise ContentFuelContractError("rotation policy must select one local and one cold lane")
    if len({lane["lane"] for lane in normalized}) != 2:
        raise ContentFuelContractError("rotation policy lanes must be distinct")
    return normalized[0], normalized[1]


def validate_content_fuel_proposal(rotation: Mapping[str, Any], proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate and return a normalized candidate list; otherwise fail closed.

    Retained candidates must be current, primary-source, cited, proposal-only,
    and deduplicated by their immutable canonical source reference.  A provider
    may explicitly cut an observed candidate, but a cut needs a reason and may
    not use a publication action.
    """
    validate_rotation_policy(rotation)
    if not isinstance(proposal, Mapping):
        raise ContentFuelContractError("provider proposal must be an object")
    candidates = proposal.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ContentFuelContractError("provider proposal must contain a non-empty candidates list")
    if not isinstance(proposal.get("lane_health"), list):
        raise ContentFuelContractError("provider proposal must contain lane_health list")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ContentFuelContractError("candidate must be an object")
        source_ref = _immutable_ref(candidate.get("source_ref"), "source_ref")
        if source_ref in seen:
            raise ContentFuelContractError("candidates must be deduplicated by source_ref")
        seen.add(source_ref)
        source_class = _text(candidate.get("source_class"), "source_class")
        if source_class not in _SOURCE_CLASSES:
            raise ContentFuelContractError("source_class is unknown")
        citations = _citations(candidate.get("citation_refs"))
        decision = _text(candidate.get("decision"), "decision")
        action = candidate.get("action")
        if decision == "retain":
            if candidate.get("current") is not True:
                raise ContentFuelContractError("retained candidate must be current")
            if source_class != "primary":
                raise ContentFuelContractError("retained candidate must have source_class primary")
            if action != "propose":
                raise ContentFuelContractError("retained candidate must be proposal-only")
            normalized.append({"source_ref": source_ref, "source_class": source_class,
                               "citation_refs": citations, "current": True,
                               "decision": "retain", "action": "propose"})
        elif decision == "cut":
            reason = _text(candidate.get("reason"), "cut reason")
            if action not in (None, "cut"):
                raise ContentFuelContractError("cut candidate may not carry a publication action")
            normalized.append({"source_ref": source_ref, "source_class": source_class,
                               "citation_refs": citations, "decision": "cut", "reason": reason,
                               "action": "cut"})
        else:
            raise ContentFuelContractError("decision must be retain or cut")
    return normalized
