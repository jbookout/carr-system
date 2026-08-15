#!/usr/bin/env python3
"""Offline selection of a typed, synthetic read-only route.

This module verifies fixed file evidence and returns a descriptor only.  It has
no transport, database, or tool invocation path.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any


POLICY_FIELDS = {
    "schema_version", "artifact_type", "data_class", "execution", "calls_models",
    "writes_records", "allowed_actions", "tool_registry", "action_risk_registry",
    "server_context", "server_context_digest", "selected_tool_evidence", "routes",
    "response_envelope_evidence", "denied_targets",
}
FILE_BINDING_FIELDS = {"path", "sha256"}
SERVER_CONTEXT_FIELDS = {
    "organization_tenant_id", "runtime_principal", "sponsoring_human_id", "capability_profile",
}
ROUTE_FIELDS = {"tool_name", "write", "full_only", "input_schema"}
SELECTED_TOOL_EVIDENCE_FIELDS = ROUTE_FIELDS | {"input_schema_digest"}
DENIED_TARGET_FIELDS = {"tool_name", "write", "full_only"}
RESPONSE_ENVELOPE_EVIDENCE_FIELDS = {
    "fixture", "validator", "accepted_evidence", "accepted_evidence_digest",
}
ACCEPTED_ENVELOPE_EVIDENCE_FIELDS = {
    "state", "attempts", "violation_codes", "case_id", "fixture_digest", "validator_digest",
}
PROPOSAL_FIELDS = {"schema_version", "tool_name", "arguments"}
FORBIDDEN_AUTHORITY_FIELDS = {
    "target", "organization_tenant_id", "tenant_id", "identity", "actor",
    "runtime_principal", "sponsoring_human_id", "profile", "capability", "capabilities",
    "write", "action", "actions", "allowed_actions",
}
ROUTER_VIOLATION_CODES = {
    "router_policy_invalid",
    "router_proposal_not_object",
    "router_proposal_missing_fields",
    "router_proposal_unknown_fields",
    "router_authority_field_forbidden",
    "router_schema_version_invalid",
    "router_unknown_tool",
    "router_write_target_forbidden",
    "router_sensitive_target_forbidden",
    "router_envelope_evidence_invalid",
    "router_arguments_missing",
    "router_arguments_unknown",
    "router_arguments_type_invalid",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCHEMA_FIELDS = {
    "type", "properties", "required", "items", "enum", "description", "default",
    "additionalProperties",
}
SUPPORTED_SCHEMA_TYPES = {"string", "integer", "number", "boolean", "object", "array"}


class RouterError(ValueError):
    """A policy or server-evidence artifact is invalid."""


@dataclass(frozen=True)
class RouterPolicy:
    """A loaded policy paired with the digest that authenticates its in-memory shape."""

    data: dict[str, Any]
    trusted_digest: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]


def _exact_object(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise RouterError(f"{label} must contain its exact v1 fields")
    return value


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _validate_file_binding(value: Any, repo_root: Path, label: str) -> None:
    binding = _exact_object(value, FILE_BINDING_FIELDS, label)
    relative_path = binding["path"]
    if not _nonempty_string(relative_path) or not SHA256_RE.fullmatch(binding["sha256"]):
        raise RouterError(f"{label} has an invalid file binding")
    root = repo_root.resolve()
    target = (root / relative_path).resolve()
    if root not in target.parents or not target.is_file() or _sha256(target) != binding["sha256"]:
        raise RouterError(f"{label} does not bind the current file")


def _validate_schema(schema: Any) -> None:
    if not isinstance(schema, dict) or not set(schema).issubset(SCHEMA_FIELDS):
        raise RouterError("route input_schema is unsupported")
    schema_type = schema.get("type")
    if schema_type not in SUPPORTED_SCHEMA_TYPES:
        raise RouterError("route input_schema type is unsupported")
    if "enum" in schema and (
        not isinstance(schema["enum"], list) or not schema["enum"]
        or len(schema["enum"]) != len(set(json.dumps(item, sort_keys=True) for item in schema["enum"]))
    ):
        raise RouterError("route input_schema enum is invalid")
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise RouterError("route object schema is invalid")
        if not all(isinstance(key, str) and key for key in properties):
            raise RouterError("route object schema has invalid property names")
        if not all(isinstance(key, str) and key in properties for key in required):
            raise RouterError("route object schema has invalid required fields")
        if len(required) != len(set(required)):
            raise RouterError("route object schema repeats required fields")
        if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
            raise RouterError("route object schema has invalid additionalProperties")
        for child in properties.values():
            _validate_schema(child)
    elif schema_type == "array":
        if "items" not in schema:
            raise RouterError("route array schema requires items")
        _validate_schema(schema["items"])
    elif any(field in schema for field in ("properties", "required", "items", "additionalProperties")):
        raise RouterError("route scalar schema has object or array fields")


def _validate_policy(policy: Any, repo_root: Path) -> dict[str, Any]:
    artifact = _exact_object(policy, POLICY_FIELDS, "router policy")
    if artifact["schema_version"] != 1 or artifact["artifact_type"] != "synthetic_read_only_router_policy":
        raise RouterError("router policy schema is unsupported")
    if artifact["data_class"] != "synthetic_only" or artifact["execution"] != "offline_deterministic":
        raise RouterError("router policy is not D1 offline data")
    if artifact["calls_models"] is not False or artifact["writes_records"] is not False:
        raise RouterError("router policy cannot call models or write records")
    if artifact["allowed_actions"] != []:
        raise RouterError("router policy cannot authorize actions")
    _validate_file_binding(artifact["tool_registry"], repo_root, "tool registry")
    _validate_file_binding(artifact["action_risk_registry"], repo_root, "action risk registry")
    context = _exact_object(artifact["server_context"], SERVER_CONTEXT_FIELDS, "server context")
    if not all(_nonempty_string(context[field]) for field in SERVER_CONTEXT_FIELDS):
        raise RouterError("server context fields must be non-empty strings")
    if context["organization_tenant_id"] != "carr-internal" or context["capability_profile"] != "read_only":
        raise RouterError("server context cannot widen tenant or capability")
    if artifact["server_context_digest"] != _canonical_digest(context):
        raise RouterError("server context digest does not match its evidence")
    risk_path = repo_root / artifact["action_risk_registry"]["path"]
    try:
        risk_rows = json.loads(risk_path.read_text())["verbs"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RouterError("action risk registry cannot be read") from exc
    if not isinstance(artifact["routes"], list) or len(artifact["routes"]) < 2:
        raise RouterError("router policy needs two safe routes")
    if not isinstance(artifact["selected_tool_evidence"], list):
        raise RouterError("selected tool evidence is invalid")
    evidence_by_name: dict[str, dict[str, Any]] = {}
    for row in artifact["selected_tool_evidence"]:
        evidence = _exact_object(row, SELECTED_TOOL_EVIDENCE_FIELDS, "selected tool evidence")
        name = evidence["tool_name"]
        if not _nonempty_string(name) or name in evidence_by_name:
            raise RouterError("selected tool evidence must have unique names")
        if evidence["write"] is not False or evidence["full_only"] is not False:
            raise RouterError("selected tool evidence is not read-only")
        _validate_schema(evidence["input_schema"])
        if evidence["input_schema_digest"] != _canonical_digest(evidence["input_schema"]):
            raise RouterError("selected tool schema digest does not match")
        evidence_by_name[name] = evidence
    if len(evidence_by_name) != len(artifact["routes"]):
        raise RouterError("selected tool evidence does not match routes")
    names: set[str] = set()
    for row in artifact["routes"]:
        route = _exact_object(row, ROUTE_FIELDS, "router route")
        name = route["tool_name"]
        if not _nonempty_string(name) or name in names:
            raise RouterError("router routes must have unique names")
        names.add(name)
        if route["write"] is not False or route["full_only"] is not False:
            raise RouterError("router route is not safe for read-only selection")
        _validate_schema(route["input_schema"])
        expected = evidence_by_name.get(name)
        if expected is None or route != {key: expected[key] for key in ROUTE_FIELDS}:
            raise RouterError("router route drifts from selected tool evidence")
        risk = risk_rows.get(name)
        if not isinstance(risk, dict) or risk.get("write") is not False or risk.get("protection") != "read_only":
            raise RouterError("router route does not match action risk evidence")
    if not isinstance(artifact["denied_targets"], list):
        raise RouterError("router denied targets are invalid")
    denied_names: set[str] = set()
    for row in artifact["denied_targets"]:
        denied = _exact_object(row, DENIED_TARGET_FIELDS, "router denied target")
        if (
            not _nonempty_string(denied["tool_name"])
            or denied["tool_name"] in names
            or denied["tool_name"] in denied_names
        ):
            raise RouterError("router denied target is invalid")
        denied_names.add(denied["tool_name"])
        if not isinstance(denied["write"], bool) or not isinstance(denied["full_only"], bool):
            raise RouterError("router denied target flags are invalid")
    envelope = _exact_object(
        artifact["response_envelope_evidence"], RESPONSE_ENVELOPE_EVIDENCE_FIELDS,
        "response envelope evidence",
    )
    _validate_file_binding(envelope["fixture"], repo_root, "response envelope fixture")
    _validate_file_binding(envelope["validator"], repo_root, "response envelope validator")
    accepted = _exact_object(
        envelope["accepted_evidence"], ACCEPTED_ENVELOPE_EVIDENCE_FIELDS,
        "accepted response envelope evidence",
    )
    if (
        accepted["state"] != "accepted"
        or accepted["attempts"] != 1
        or accepted["violation_codes"] != []
        or not _nonempty_string(accepted["case_id"])
        or accepted["fixture_digest"] != envelope["fixture"]["sha256"]
        or accepted["validator_digest"] != envelope["validator"]["sha256"]
    ):
        raise RouterError("accepted response envelope evidence is invalid")
    if envelope["accepted_evidence_digest"] != _canonical_digest(accepted):
        raise RouterError("accepted response envelope evidence digest does not match")
    return artifact


def load_router_policy(path: Path, repo_root: Path | None = None) -> RouterPolicy:
    """Load and verify the fixed D1 read-route evidence artifact."""
    root = repo_root or path.resolve().parents[2]
    try:
        policy = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RouterError("router policy cannot be loaded") from exc
    artifact = _validate_policy(policy, root)
    return RouterPolicy(artifact, _canonical_digest(artifact))


def _schema_matches(value: Any, schema: dict[str, Any]) -> bool:
    schema_type = schema["type"]
    if schema_type == "string":
        matches = isinstance(value, str)
    elif schema_type == "boolean":
        matches = isinstance(value, bool)
    elif schema_type == "integer":
        matches = isinstance(value, int) and not isinstance(value, bool)
    elif schema_type == "number":
        matches = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    elif schema_type == "array":
        matches = isinstance(value, list) and all(_schema_matches(item, schema["items"]) for item in value)
    else:
        if not isinstance(value, dict):
            return False
        properties = schema["properties"]
        if set(value) - set(properties):
            return False
        if any(key not in value for key in schema.get("required", [])):
            return False
        matches = all(_schema_matches(item, properties[key]) for key, item in value.items())
    return matches and ("enum" not in schema or value in schema["enum"])


def _refuse(code: str) -> dict[str, Any]:
    if code not in ROUTER_VIOLATION_CODES:
        raise RouterError("router emitted an undeclared violation code")
    return {"state": "refused", "violation_codes": [code]}


def route_read_only(
    proposal: Any, envelope_evidence: Any, policy: Any, repo_root: Path
) -> dict[str, Any]:
    """Return a normalized descriptor only when the proposal is safely routable."""
    if not isinstance(policy, RouterPolicy) or policy.trusted_digest != _canonical_digest(policy.data):
        return _refuse("router_policy_invalid")
    try:
        artifact = _validate_policy(policy.data, repo_root)
    except RouterError:
        return _refuse("router_policy_invalid")
    envelope = artifact["response_envelope_evidence"]
    if not isinstance(envelope_evidence, dict):
        return _refuse("router_envelope_evidence_invalid")
    if (
        envelope_evidence != envelope["accepted_evidence"]
        or _canonical_digest(envelope_evidence) != envelope["accepted_evidence_digest"]
    ):
        return _refuse("router_envelope_evidence_invalid")
    if not isinstance(proposal, dict):
        return _refuse("router_proposal_not_object")
    if set(proposal) & FORBIDDEN_AUTHORITY_FIELDS:
        return _refuse("router_authority_field_forbidden")
    if PROPOSAL_FIELDS - set(proposal):
        return _refuse("router_proposal_missing_fields")
    if set(proposal) - PROPOSAL_FIELDS:
        return _refuse("router_proposal_unknown_fields")
    if proposal["schema_version"] != 1:
        return _refuse("router_schema_version_invalid")
    name = proposal["tool_name"]
    if not isinstance(name, str):
        return _refuse("router_unknown_tool")
    allowed = {row["tool_name"]: row for row in artifact["routes"]}
    denied = {row["tool_name"]: row for row in artifact["denied_targets"]}
    if name not in allowed:
        target = denied.get(name)
        if target and target["write"]:
            return _refuse("router_write_target_forbidden")
        if target and target["full_only"]:
            return _refuse("router_sensitive_target_forbidden")
        return _refuse("router_unknown_tool")
    arguments = proposal["arguments"]
    if not isinstance(arguments, dict):
        return _refuse("router_arguments_type_invalid")
    schema = allowed[name]["input_schema"]
    properties = schema["properties"]
    if set(arguments) - set(properties):
        return _refuse("router_arguments_unknown")
    if any(field not in arguments for field in schema.get("required", [])):
        return _refuse("router_arguments_missing")
    if not _schema_matches(arguments, schema):
        return _refuse("router_arguments_type_invalid")
    return {
        "state": "accepted",
        "route": {"tool_name": name, "arguments": arguments},
        "attribution": artifact["server_context"],
        "calls_models": False,
        "writes_records": False,
        "allowed_actions": [],
    }
