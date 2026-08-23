"""Compile the reviewed enforcement-map row into the Phase 1 admission shape."""
from __future__ import annotations
from typing import Any

BUILT = {
    "deny_gate":"deny_gate", "stop_gate":"stop_gate", "surfacing":"surfacing",
    "schema":"schema",
}

# The reason stamped on every row this compiler produces. tools/sync-rule-admission.py
# reads it back to tell its OWN rows apart from a contract someone wrote by hand
# through admit-rule, which it must never overwrite.
BACKFILL_REASON = "Backfilled from the reviewed active rule enforcement map"


def backfill_owns_row(existing_reason: str | None, approved: bool) -> bool:
    """May the map-driven backfill write this rule's contract?

    No for an APPROVED rule: migration 0228 freezes the contract an approval
    receipt covers, and the enforcement-point trigger raises on any touch.
    No for a contract admitted through admit-rule: its reason is someone's own
    words, and its enforcement points name controls the map's catalog does not
    carry. Yes for a rule with no contract yet, and yes for a row the backfill
    itself wrote, so re-running stays idempotent and a map correction still
    propagates to the rows the map is actually the authority for.
    """
    if approved:
        return False
    return existing_reason is None or existing_reason == BACKFILL_REASON


def admission_contract(rule_id: str, scope: str, entry: dict[str,Any],
                       catalog: dict[str,Any]) -> dict[str,Any]:
    cls=entry["enforcement_class"]
    admission=entry.get("admission", {})
    if not isinstance(admission, dict):
        raise ValueError(f"{rule_id} admission metadata must be an object")
    for field in ("applicability", "projection", "reachability", "input_contract"):
        if field in admission and not isinstance(admission[field], dict):
            raise ValueError(f"{rule_id} admission.{field} must be an object")
    applicability={"scopes":[scope],"workflows":["*"],"surfaces":["*"],"tiers":["*"]}
    projection={"targets":["standing-context","applicable-rules","rule-enforcement-map"],
                "category":entry.get("category")}
    reachability={"paths":["record-layer","session-boot","CI"],"exceptions":entry.get("exceptions")}
    applicability=admission.get("applicability", applicability)
    projection=admission.get("projection", projection)
    reachability=admission.get("reachability", reachability)
    input_contract=admission.get("input_contract", {
        "type":"object","required":["workflow","surface","tier"],
        "properties":{"workflow":{"type":"string"}, "surface":{"type":"string"},
                      "tier":{"type":"string"}}})
    base={"rule_id":rule_id,"applicability":applicability,"projection":projection,
          "reachability":reachability,
          "input_contract":input_contract,
          "reason":BACKFILL_REASON}
    if cls=="judgment_ambient":
        return {**base,"state":"admitted","enforcement_class":"judgment_advisory",
                "binding_moment":"when the named contextual judgment is required",
                "fixture_refs":[],"enforcement_points":[],
                "reachability":{**reachability,"why_unenforceable":entry["why_unenforceable"]}}
    if cls=="unbuilt":
        return {**base,"state":"needs_revision","enforcement_class":"judgment_advisory",
                "binding_moment":"planned control is not installed",
                "fixture_refs":[],"enforcement_points":[],
                "projection":{**projection,"planned_control":entry["planned_control"]}}
    control_names=[entry.get("control"),entry.get("second_control"),entry.get("third_control")]
    points=[]; fixtures=[]
    for name in [x for x in control_names if x]:
        detail=catalog.get(name)
        if not isinstance(detail,dict) or not detail.get("implementation") or not detail.get("test"):
            raise ValueError(f"{rule_id} control {name} lacks implementation/test evidence")
        implementations=detail["implementation"]
        tests=detail["test"]
        fixtures.extend(tests)
        points.append({"control_key":name,"implementation_ref":"; ".join(implementations),
                       "test_ref":"; ".join(tests),"enforcement_class":BUILT[cls],
                       "installed":True})
    if not points:
        raise ValueError(f"{rule_id} built class names no control")
    return {**base,"state":"admitted","enforcement_class":"machine_enforceable",
            "binding_moment":entry["binding_moment"],
            "fixture_refs":sorted(set(fixtures)),"enforcement_points":points}
