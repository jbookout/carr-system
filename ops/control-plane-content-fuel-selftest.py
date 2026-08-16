#!/usr/bin/env python3
"""Hermetic fail-closed checks for post-provider content-fuel validation."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.control_plane_content_fuel import ContentFuelContractError, validate_content_fuel_proposal

ROTATION = {"lanes": [{"lane": "local-healthcare", "temperature": "local"},
                      {"lane": "next-cold", "temperature": "cold"}]}
VALID = {"lane_health": [], "candidates": [
    {"source_ref": "source:cms:100", "source_class": "primary", "citation_refs": ["https://agency.example/item/100"],
     "current": True, "decision": "retain", "action": "propose"},
    {"source_ref": "source:cms:101", "source_class": "secondary", "citation_refs": ["https://agency.example/item/101"],
     "decision": "cut", "reason": "secondary summary; original unavailable", "action": "cut"},
]}
FAILED: list[str] = []


def check(label: str, value: bool) -> None:
    print(("  ok    " if value else "  FAIL  ") + label)
    if not value:
        FAILED.append(label)


def refuses(label: str, rotation=ROTATION, proposal=VALID) -> None:
    try:
        validate_content_fuel_proposal(rotation, proposal)
    except ContentFuelContractError:
        check(label, True)
    else:
        check(label, False)


def changed(path: list[Any], value: Any) -> dict[str, Any]:
    proposal = copy.deepcopy(VALID)
    target: Any = proposal
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return proposal


def main() -> int:
    normalized = validate_content_fuel_proposal(ROTATION, VALID)
    check("valid primary retain plus explicit secondary cut is accepted", normalized[0]["action"] == "propose" and normalized[1]["action"] == "cut")
    refuses("empty proposal fails closed", proposal={"lane_health": [], "candidates": []})
    refuses("malformed candidate fails closed", proposal={"lane_health": [], "candidates": ["not-an-object"]})
    refuses("rotation must contain local plus cold", rotation={"lanes": [{"lane": "a", "temperature": "local"}, {"lane": "b", "temperature": "local"}]})
    refuses("retained candidate must be current", proposal=changed(["candidates", 0, "current"], False))
    refuses("retained candidate must be primary", proposal=changed(["candidates", 0, "source_class"], "secondary"))
    refuses("unknown source class fails closed", proposal=changed(["candidates", 0, "source_class"], "unknown"))
    refuses("missing citation fails closed", proposal=changed(["candidates", 0, "citation_refs"], []))
    refuses("blank citation fails closed", proposal=changed(["candidates", 0, "citation_refs"], [""]))
    refuses("mutable URL source reference fails closed", proposal=changed(["candidates", 0, "source_ref"], "https://example.invalid/story"))
    duplicate = changed(["candidates", 1, "source_ref"], "source:cms:100")
    refuses("duplicate immutable source reference fails closed", proposal=duplicate)
    refuses("retained publication action fails closed", proposal=changed(["candidates", 0, "action"], "publish"))
    refuses("cut requires a reason", proposal=changed(["candidates", 1, "reason"], ""))
    refuses("cut publication action fails closed", proposal=changed(["candidates", 1, "action"], "publish"))
    refuses("unknown decision fails closed", proposal=changed(["candidates", 0, "decision"], "maybe"))
    print(f"content-fuel contract selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
