#!/usr/bin/env python3
"""Offline acceptance tests for governed execution-environment providers."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import execution_environment as environment  # noqa: E402


def manifest() -> dict:
    value = {
        "schema_version": "execution-environment-provider.v1",
        "provider_key": "hermes-local",
        "provider_version": 1,
        "display_name": "Hermes Local Terminal",
        "source_class": "built_in",
        "backend_kind": "local",
        "implementation_ref": "hermes:tools.environments.local.LocalEnvironment",
        "implementation_digest": "sha256:" + "1" * 64,
        "capability_refs": ["environment:exec", "environment:filesystem", "environment:process"],
        "operation_refs": ["operation:create", "operation:exec", "operation:cancel", "operation:destroy", "operation:health"],
        "isolation_class": "host_process",
        "egress_policy_ref": "egress:host-governed",
        "secret_policy_ref": "secrets:never-in-manifest",
        "persistence_mode": "session_scoped",
        "resource_policy_ref": "resources:bounded-local-v1",
        "cleanup_policy_ref": "cleanup:process-tree-v1",
        "threat_model_ref": "threat-model:local-trusted-input-v1",
        "conformance_contract_ref": "conformance:execution-environment-v1",
        "conformance_contract_digest": "sha256:" + "2" * 64,
        "configuration_schema_digest": "sha256:" + "3" * 64,
        "package_provenance": {
            "package_ref": "package:nous-hermes-agent",
            "package_digest": "sha256:" + "4" * 64,
            "signature_ref": "signature:upstream-git-commit",
            "sbom_ref": "sbom:hermes-installed-tree",
        },
        "collision_policy": "protected_builtin",
        "contains_secrets": False,
    }
    value["manifest_digest"] = environment.provider_manifest_digest(value)
    return value


def binding() -> dict:
    value = {
        "provider_ref": "environment-provider:hermes-local:v1",
        "provider_version": 1,
        "provider_digest": manifest()["manifest_digest"],
        "requirement_digest": "sha256:" + "5" * 64,
        "configuration_digest": "sha256:" + "6" * 64,
        "backend_kind": "local",
        "source_class": "built_in",
        "isolation_class": "host_process",
        "capability_refs": ["environment:exec", "environment:filesystem", "environment:process"],
        "conformance_ref": "conformance-run:hermes-local-v1",
        "conformance_digest": "sha256:" + "7" * 64,
    }
    value["binding_digest"] = environment.environment_binding_digest(value)
    return value


def evidence() -> dict:
    return {
        "binding_digest": binding()["binding_digest"],
        "session_ref": "environment-session:synthetic",
        "lease_state": "released",
        "operation_count": 2,
        "policy_refusal_refs": [],
        "security_event_refs": [],
        "cleanup_state": "verified",
        "cleanup_evidence_refs": ["evidence:cleanup"],
        "side_effect_state": "none",
        "resource_usage": {
            "cpu_ms": 12,
            "memory_peak_mb": 32,
            "disk_peak_mb": 1,
            "network_egress_bytes": 0,
        },
        "evidence_refs": ["evidence:environment-session"],
    }


def refuse(fn, fragment: str) -> None:
    try:
        fn()
    except environment.ExecutionEnvironmentError as exc:
        assert fragment in str(exc), exc
        return
    raise AssertionError("expected execution-environment refusal")


def test_manifest_is_closed_digest_bound_and_secret_free() -> None:
    value = manifest()
    assert environment.validate_provider_manifest(value) == value
    changed = copy.deepcopy(value)
    changed["backend_kind"] = "cloud"
    refuse(lambda: environment.validate_provider_manifest(changed), "digest")
    changed = copy.deepcopy(value)
    changed["api_token"] = "never"
    refuse(lambda: environment.validate_provider_manifest(changed), "unknown fields")
    changed = copy.deepcopy(value)
    changed["contains_secrets"] = True
    changed["manifest_digest"] = environment.provider_manifest_digest(changed)
    refuse(lambda: environment.validate_provider_manifest(changed), "secrets")


def test_plugin_cannot_shadow_a_protected_builtin() -> None:
    value = manifest()
    value["source_class"] = "plugin"
    value["collision_policy"] = "digest_pinned"
    value["manifest_digest"] = environment.provider_manifest_digest(value)
    refuse(lambda: environment.validate_provider_manifest(value), "protected built-in")


def test_binding_is_exact_and_cannot_demote_conformance() -> None:
    value = binding()
    assert environment.validate_environment_binding(value) == value
    changed = copy.deepcopy(value)
    changed["conformance_digest"] = "sha256:" + "8" * 64
    refuse(lambda: environment.validate_environment_binding(changed), "binding digest")
    changed = copy.deepcopy(value)
    changed["capability_refs"].append("environment:network-unrestricted")
    changed["binding_digest"] = environment.environment_binding_digest(changed)
    refuse(lambda: environment.validate_environment_binding(changed), "capabilities")


def test_receipt_evidence_binds_session_and_cleanup_without_raw_content() -> None:
    value = evidence()
    assert environment.validate_environment_evidence(value, binding()) == value
    changed = copy.deepcopy(value)
    changed["binding_digest"] = "sha256:" + "9" * 64
    refuse(lambda: environment.validate_environment_evidence(changed, binding()), "does not bind")
    changed = copy.deepcopy(value)
    changed["raw_transcript"] = "client content"
    refuse(lambda: environment.validate_environment_evidence(changed, binding()), "unknown fields")
    changed = copy.deepcopy(value)
    changed["cleanup_state"] = "failed"
    changed["cleanup_evidence_refs"] = []
    refuse(lambda: environment.validate_environment_evidence(changed, binding()), "cleanup evidence")


def test_contract_schema_is_closed_and_matches_validator() -> None:
    schema = json.loads((ROOT / "control-room" / "contracts" / "execution-environment-provider.v1.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "execution-environment-provider.v1"
    assert set(schema["required"]) == environment.PROVIDER_FIELDS


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"execution-environment tests: {len(tests)} passed")
