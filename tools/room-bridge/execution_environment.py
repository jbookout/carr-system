"""Fail-closed contracts for replaceable execution-environment providers.

Hermes owns backend mechanics. CARR owns admission, exact route binding,
evidence, and promotion. Nothing in this module grants a tool or capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import execution_contract as base


class ExecutionEnvironmentError(base.ContractError):
    """An environment provider, binding, or evidence row is not admissible."""


PROVIDER_FIELDS = {
    "schema_version", "provider_key", "provider_version", "display_name",
    "source_class", "backend_kind", "implementation_ref", "implementation_digest",
    "capability_refs", "operation_refs", "isolation_class", "egress_policy_ref",
    "secret_policy_ref", "persistence_mode", "resource_policy_ref", "cleanup_policy_ref",
    "threat_model_ref", "conformance_contract_ref", "conformance_contract_digest",
    "configuration_schema_digest", "package_provenance", "collision_policy",
    "contains_secrets", "manifest_digest",
}
PROVENANCE_FIELDS = {"package_ref", "package_digest", "signature_ref", "sbom_ref"}
BINDING_FIELDS = {
    "provider_ref", "provider_version", "provider_digest", "requirement_digest",
    "configuration_digest", "backend_kind", "source_class", "isolation_class",
    "capability_refs", "conformance_ref", "conformance_digest", "binding_digest",
}
EVIDENCE_FIELDS = {
    "binding_digest", "session_ref", "lease_state", "operation_count",
    "policy_refusal_refs", "security_event_refs", "cleanup_state",
    "cleanup_evidence_refs", "side_effect_state", "resource_usage", "evidence_refs",
}
RESOURCE_USAGE_FIELDS = {"cpu_ms", "memory_peak_mb", "disk_peak_mb", "network_egress_bytes"}

PROTECTED_BUILTIN_KEYS = {
    "hermes-local", "hermes-docker", "hermes-ssh", "hermes-singularity",
    "hermes-modal", "hermes-daytona", "hermes-vercel-sandbox",
}
CAPABILITIES = {
    "environment:none", "environment:exec", "environment:filesystem",
    "environment:process", "environment:network-governed", "environment:snapshot",
    "environment:transfer", "environment:persistent-workspace",
}
REQUIRED_OPERATIONS = {
    "operation:create", "operation:exec", "operation:cancel", "operation:destroy",
    "operation:health",
}
BACKEND_KINDS = {"none", "local", "container", "remote", "cloud"}
ISOLATION_CLASSES = {"none", "host_process", "container", "microvm", "remote_host"}
PERSISTENCE_MODES = {"none", "command_scoped", "session_scoped", "durable_workspace"}


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    try:
        return base._expect_exact(value, fields, label)
    except base.ContractError as exc:
        raise ExecutionEnvironmentError(str(exc)) from exc


def _identifier(value: Any, label: str) -> str:
    try:
        return base._string(value, label, identifier=True)
    except base.ContractError as exc:
        raise ExecutionEnvironmentError(str(exc)) from exc


def _text(value: Any, label: str) -> str:
    try:
        return base._string(value, label)
    except base.ContractError as exc:
        raise ExecutionEnvironmentError(str(exc)) from exc


def _digest(value: Any, label: str) -> str:
    try:
        return base._digest(value, label)
    except base.ContractError as exc:
        raise ExecutionEnvironmentError(str(exc)) from exc


def _refs(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    try:
        refs = base._list_of_strings(value, label)
    except base.ContractError as exc:
        raise ExecutionEnvironmentError(str(exc)) from exc
    if nonempty and not refs:
        raise ExecutionEnvironmentError(f"{label} must not be empty")
    if len(refs) != len(set(refs)):
        raise ExecutionEnvironmentError(f"{label} must be unique")
    for index, ref in enumerate(refs):
        _identifier(ref, f"{label}[{index}]")
    return refs


def _canonical_digest(value: dict[str, Any], omitted: str) -> str:
    preimage = {key: item for key, item in value.items() if key != omitted}
    try:
        encoded = json.dumps(preimage, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExecutionEnvironmentError("environment contract must be canonical JSON") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def provider_manifest_digest(value: dict[str, Any]) -> str:
    return _canonical_digest(value, "manifest_digest")


def environment_binding_digest(value: dict[str, Any]) -> str:
    return _canonical_digest(value, "binding_digest")


def validate_provider_manifest(value: Any) -> dict[str, Any]:
    manifest = _exact(value, PROVIDER_FIELDS, "execution environment provider")
    if manifest["schema_version"] != "execution-environment-provider.v1":
        raise ExecutionEnvironmentError("unsupported execution environment provider schema")
    key = _text(manifest["provider_key"], "provider_key")
    if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", key) is None:
        raise ExecutionEnvironmentError("provider_key is invalid")
    if not isinstance(manifest["provider_version"], int) or isinstance(manifest["provider_version"], bool) or manifest["provider_version"] < 1:
        raise ExecutionEnvironmentError("provider_version must be positive")
    display_name = _text(manifest["display_name"], "display_name")
    if len(display_name) > 80:
        raise ExecutionEnvironmentError("display_name is too long")
    if manifest["source_class"] not in {"built_in", "plugin"}:
        raise ExecutionEnvironmentError("source_class is invalid")
    if manifest["backend_kind"] not in BACKEND_KINDS:
        raise ExecutionEnvironmentError("backend_kind is invalid")
    for field in ("implementation_ref", "egress_policy_ref", "secret_policy_ref",
                  "resource_policy_ref", "cleanup_policy_ref", "threat_model_ref",
                  "conformance_contract_ref"):
        _identifier(manifest[field], field)
    for field in ("implementation_digest", "conformance_contract_digest",
                  "configuration_schema_digest", "manifest_digest"):
        _digest(manifest[field], field)
    capabilities = _refs(manifest["capability_refs"], "capability_refs", nonempty=True)
    if any(ref not in CAPABILITIES for ref in capabilities):
        raise ExecutionEnvironmentError("provider declares unsupported capabilities")
    operations = set(_refs(manifest["operation_refs"], "operation_refs", nonempty=True))
    if not REQUIRED_OPERATIONS.issubset(operations):
        raise ExecutionEnvironmentError("provider lacks required lifecycle operations")
    if manifest["isolation_class"] not in ISOLATION_CLASSES:
        raise ExecutionEnvironmentError("isolation_class is invalid")
    if manifest["persistence_mode"] not in PERSISTENCE_MODES:
        raise ExecutionEnvironmentError("persistence_mode is invalid")
    provenance = _exact(manifest["package_provenance"], PROVENANCE_FIELDS, "package provenance")
    for field in ("package_ref", "signature_ref", "sbom_ref"):
        _identifier(provenance[field], f"package provenance {field}")
    _digest(provenance["package_digest"], "package provenance package_digest")
    if manifest["contains_secrets"] is not False:
        raise ExecutionEnvironmentError("provider manifest cannot contain secrets")
    if manifest["source_class"] == "built_in":
        if manifest["collision_policy"] != "protected_builtin":
            raise ExecutionEnvironmentError("built-in provider must be protected")
    else:
        if key in PROTECTED_BUILTIN_KEYS:
            raise ExecutionEnvironmentError("plugin cannot shadow a protected built-in provider")
        if manifest["collision_policy"] != "digest_pinned":
            raise ExecutionEnvironmentError("plugin provider must be digest pinned")
    if manifest["manifest_digest"] != provider_manifest_digest(manifest):
        raise ExecutionEnvironmentError("provider manifest digest does not bind canonical manifest")
    return manifest


def validate_environment_binding(value: Any) -> dict[str, Any]:
    binding = _exact(value, BINDING_FIELDS, "execution environment binding")
    _identifier(binding["provider_ref"], "environment provider_ref")
    if re.fullmatch(r"environment-provider:[a-z][a-z0-9]*(?:-[a-z0-9]+)*:v[1-9][0-9]*", binding["provider_ref"]) is None:
        raise ExecutionEnvironmentError("environment provider_ref is invalid")
    if not isinstance(binding["provider_version"], int) or isinstance(binding["provider_version"], bool) or binding["provider_version"] < 1:
        raise ExecutionEnvironmentError("environment provider_version must be positive")
    for field in ("provider_digest", "requirement_digest", "configuration_digest",
                  "conformance_digest", "binding_digest"):
        _digest(binding[field], f"environment {field}")
    if binding["backend_kind"] not in BACKEND_KINDS or binding["source_class"] not in {"built_in", "plugin"}:
        raise ExecutionEnvironmentError("environment provider kind/source is invalid")
    if binding["isolation_class"] not in ISOLATION_CLASSES:
        raise ExecutionEnvironmentError("environment isolation class is invalid")
    capabilities = _refs(binding["capability_refs"], "environment capability_refs", nonempty=True)
    if any(ref not in CAPABILITIES for ref in capabilities):
        raise ExecutionEnvironmentError("environment binding declares unsupported capabilities")
    _identifier(binding["conformance_ref"], "environment conformance_ref")
    if binding["binding_digest"] != environment_binding_digest(binding):
        raise ExecutionEnvironmentError("environment binding digest does not bind exact provider")
    return binding


def validate_environment_evidence(value: Any, binding: Any) -> dict[str, Any]:
    row = _exact(value, EVIDENCE_FIELDS, "execution environment evidence")
    exact_binding = validate_environment_binding(binding)
    _digest(row["binding_digest"], "environment evidence binding_digest")
    if row["binding_digest"] != exact_binding["binding_digest"]:
        raise ExecutionEnvironmentError("environment evidence does not bind issued environment")
    _identifier(row["session_ref"], "environment evidence session_ref")
    if row["lease_state"] not in {"active", "released", "expired", "failed", "unknown"}:
        raise ExecutionEnvironmentError("environment evidence lease_state is invalid")
    if not isinstance(row["operation_count"], int) or isinstance(row["operation_count"], bool) or row["operation_count"] < 0:
        raise ExecutionEnvironmentError("environment evidence operation_count is invalid")
    for field in ("policy_refusal_refs", "security_event_refs", "cleanup_evidence_refs", "evidence_refs"):
        _refs(row[field], f"environment evidence {field}")
    if row["cleanup_state"] not in {"not_required", "pending", "verified", "failed", "unknown"}:
        raise ExecutionEnvironmentError("environment evidence cleanup_state is invalid")
    if row["cleanup_state"] in {"verified", "failed"} and not row["cleanup_evidence_refs"]:
        raise ExecutionEnvironmentError("environment cleanup evidence is required")
    if row["side_effect_state"] not in {"none", "attempted", "refused", "observed", "unknown"}:
        raise ExecutionEnvironmentError("environment evidence side_effect_state is invalid")
    usage = _exact(row["resource_usage"], RESOURCE_USAGE_FIELDS, "environment resource usage")
    for field, amount in usage.items():
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ExecutionEnvironmentError(f"environment resource usage {field} must be non-negative")
    if not row["evidence_refs"]:
        raise ExecutionEnvironmentError("environment evidence requires evidence_refs")
    return row
