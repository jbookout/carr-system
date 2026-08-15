#!/usr/bin/env python3
"""Validate the closed D2 disposition coverage for derived action-risk gaps.

This is intentionally a contract validator, not a dispatcher or policy engine.
It has no record-layer, network, or client-payload path: it only proves that the
known baseline NONE rows are owned and that a selected remediation actually
changed the derived runtime evidence it claims to change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = ROOT / "control-room" / "contracts" / "action-risk-dispositions.v1.json"
REGISTRY_PATH = ROOT / "control-room" / "contracts" / "action-risk-registry.v1.json"

BASELINE_NONE_ROWS = frozenset({
    "complete-action", "detach-decision", "end-deal-review", "link-parties",
    "measure-placement", "register-template", "resolve-conflict", "set-lead",
    "set-next-action", "set-next-step", "triage-item", "update-decision",
    "update-document-status",
})
ARTIFACT_FIELDS = {
    "schema_version", "artifact_type", "data_class", "action_risk_registry",
    "selected_slice", "dispositions",
}
BINDING_FIELDS = {"path", "sha256"}
ROW_FIELDS = {
    "tool_name", "baseline_protection", "owner", "disposition", "evidence",
    "priority", "selected_slice",
}
OWNERS = {"mcp-server-runtime"}
PRIORITIES = {"P0", "P1", "P2"}
PENDING_DISPOSITION = "pending_shape_analysis"
SELECTED_DISPOSITION = "human_boundary_and_optimistic_concurrency"
SELECTED_TOOL = "set-lead"
SELECTED_EVIDENCE = "mcp-server/test/action-risk-guardrails.test.mjs"


class DispositionError(ValueError):
    """Closed, non-payload-bearing disposition contract error."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact_object(value: Any, fields: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise DispositionError(message)
    return value


def _validate_row(row: Any) -> dict[str, Any]:
    item = _exact_object(row, ROW_FIELDS, "disposition row fields are invalid")
    if not isinstance(item["tool_name"], str) or not item["tool_name"]:
        raise DispositionError("disposition tool name is invalid")
    if item["baseline_protection"] != "NONE":
        raise DispositionError("baseline protection is invalid")
    if item["owner"] not in OWNERS:
        raise DispositionError("disposition owner is invalid")
    if item["priority"] not in PRIORITIES:
        raise DispositionError("disposition priority is invalid")
    if not isinstance(item["selected_slice"], bool):
        raise DispositionError("selected slice marker is invalid")
    if not isinstance(item["evidence"], str) or not item["evidence"]:
        raise DispositionError("disposition evidence is invalid")
    if item["selected_slice"]:
        if item["tool_name"] != SELECTED_TOOL or item["disposition"] != SELECTED_DISPOSITION:
            raise DispositionError("selected disposition is invalid")
        if item["evidence"] != SELECTED_EVIDENCE:
            raise DispositionError("selected evidence is invalid")
    elif item["disposition"] != PENDING_DISPOSITION or item["evidence"] != "pending_shape_analysis":
        raise DispositionError("pending disposition is invalid")
    return item


def validate_dispositions(artifact: Any, registry: Any, registry_bytes: bytes) -> dict[str, Any]:
    """Validate exact baseline coverage against the current generated registry."""
    doc = _exact_object(artifact, ARTIFACT_FIELDS, "disposition artifact fields are invalid")
    if doc["schema_version"] != 1 or doc["artifact_type"] != "carr_action_risk_dispositions":
        raise DispositionError("disposition artifact version is invalid")
    if doc["data_class"] != "D2_internal_metadata_only":
        raise DispositionError("disposition artifact data class is invalid")
    if doc["selected_slice"] != SELECTED_TOOL:
        raise DispositionError("selected slice is invalid")
    binding = _exact_object(doc["action_risk_registry"], BINDING_FIELDS, "registry binding is invalid")
    if binding["path"] != "control-room/contracts/action-risk-registry.v1.json":
        raise DispositionError("registry path is invalid")
    if not isinstance(binding["sha256"], str) or binding["sha256"] != _digest(registry_bytes):
        raise DispositionError("registry digest is invalid")
    if not isinstance(registry, dict) or not isinstance(registry.get("verbs"), dict):
        raise DispositionError("generated registry is invalid")

    rows = doc["dispositions"]
    if not isinstance(rows, list):
        raise DispositionError("disposition rows are invalid")
    validated = [_validate_row(row) for row in rows]
    names = [row["tool_name"] for row in validated]
    if len(set(names)) != len(names):
        raise DispositionError("disposition rows contain duplicates")
    if set(names) != BASELINE_NONE_ROWS:
        raise DispositionError("baseline coverage is invalid")
    if [row["tool_name"] for row in validated if row["selected_slice"]] != [SELECTED_TOOL]:
        raise DispositionError("selected slice coverage is invalid")

    for row in validated:
        runtime = registry["verbs"].get(row["tool_name"])
        if not isinstance(runtime, dict) or runtime.get("write") is not True:
            raise DispositionError("baseline runtime row is invalid")
        if row["selected_slice"]:
            if runtime.get("protection") != "human_boundary" or runtime.get("binds_both_partners") is not True:
                raise DispositionError("selected runtime protection is invalid")
            if runtime.get("base_version_required") is not True:
                raise DispositionError("selected base_version protection is invalid")
        elif runtime.get("protection") != "NONE":
            raise DispositionError("unselected runtime protection drifted")
    return {"status": "valid", "selected_slice": SELECTED_TOOL, "rows": len(validated)}


def load_and_validate() -> dict[str, Any]:
    """Read only the fixed repository contract files and return redacted status."""
    try:
        artifact = json.loads(ARTIFACT_PATH.read_text())
        raw_registry = REGISTRY_PATH.read_bytes()
        registry = json.loads(raw_registry)
    except (OSError, ValueError, TypeError) as exc:
        raise DispositionError("action-risk contract files cannot be loaded") from exc
    return validate_dispositions(artifact, registry, raw_registry)


if __name__ == "__main__":
    print(json.dumps(load_and_validate(), sort_keys=True))
