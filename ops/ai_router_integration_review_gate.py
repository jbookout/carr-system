#!/usr/bin/env python3
"""Fail closed when the synthetic read router gains its first direct consumer.

The router is currently an offline evaluation surface.  A production integration
must carry a reviewed, metadata-only record before it can consume the router or
its policy.  This checker never invokes a router, model, tool, database, or
network service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


REVIEW_ARTIFACT_REL = "evals/ai/read-router-integration-review.v1.json"
ROUTER_MODULE_REL = "ops/ai_read_router.py"
POLICY_REL = "evals/ai/function-router.v1.json"
ENVELOPE_SUITE_REL = "evals/ai/model-boundary.v1.json"
EXECUTABLE_ROOTS = {"ops", "mcp-server", "tools", "bin", "workspace", "control-room"}
SOURCE_SUFFIXES = {".py": "python", ".js": "node", ".mjs": "node", ".cjs": "node", ".ts": "node", ".tsx": "node", ".sh": "shell"}
EXCLUDED_EXACT = {ROUTER_MODULE_REL, "ops/ai_router_integration_review_gate.py", "ops/ai-router-integration-review-gate-selftest.py"}
ARTIFACT_FIELDS = {
    "schema_version", "artifact_type", "data_class", "execution", "calls_models", "writes_records",
    "allowed_actions", "review_status", "router_module", "policy", "envelope_suite", "reviews",
}
BINDING_FIELDS = {"path", "sha256"}
REVIEW_FIELDS = {"consumer", "requested_routes", "execution_mode", "security"}
CONSUMER_FIELDS = {"path", "sha256", "language", "entrypoint", "trigger_kinds"}
SECURITY_FIELDS = {
    "server_owned_identity_tenant_sponsor_capability", "caller_override_denied",
    "route_action_risk_revalidated", "passing_envelope_required", "redacted_audit_observability",
    "rollback_owner", "disable_path", "safe_behavior",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
PROHIBITED_VALUE_RE = re.compile(r"(?:api[_-]?key|bearer|password|secret|token|sk-)", re.IGNORECASE)
ANCHORS = (
    ("router_module_anchor", re.compile(r"\bai_read_router\b|ops/ai_read_router\.py")),
    ("router_call_anchor", re.compile(r"\broute_read_only\s*\(")),
    ("router_policy_anchor", re.compile(r"function-router\.v1\.json")),
    ("router_distinctive_anchor", re.compile(r"synthetic_read_only_router_policy|\bPOLICY_SHA256\b")),
)


class ReviewGateError(ValueError):
    """A direct router consumer lacks an exact integration review."""


def _exact_object(value: Any, fields: set[str], message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ReviewGateError(message)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_file(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        raise ReviewGateError("integration review binding path is invalid")
    return target


def _tracked_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files"], check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReviewGateError("integration review gate requires a tracked repository") from exc
    return [line for line in result.stdout.splitlines() if line]


def _excluded(relative: str) -> bool:
    parts = relative.split("/")
    name = parts[-1]
    if relative in EXCLUDED_EXACT or parts[0] == "evals":
        return True
    if any(part in {"test", "tests", "fixtures", "fixture"} for part in parts[:-1]):
        return True
    return "selftest" in name or ".test." in name or name.startswith("test_")


def _language(relative: str) -> str | None:
    path = Path(relative)
    if (path.parts and path.parts[0] not in EXECUTABLE_ROOTS and len(path.parts) > 1) or path.suffix not in SOURCE_SUFFIXES:
        return None
    return SOURCE_SUFFIXES[path.suffix]


def detect_consumers(root: Path) -> list[dict[str, Any]]:
    """Return direct, tracked source consumers; test/eval and artifact files are inert."""
    consumers: list[dict[str, Any]] = []
    for relative in _tracked_files(root):
        if _excluded(relative):
            continue
        language = _language(relative)
        if language is None:
            continue
        try:
            source = _repo_file(root, relative).read_text()
        except (OSError, UnicodeDecodeError) as exc:
            raise ReviewGateError("integration review consumer source cannot be read") from exc
        kinds = sorted(kind for kind, pattern in ANCHORS if pattern.search(source))
        if kinds:
            consumers.append({
                "path": relative,
                "sha256": _sha256(_repo_file(root, relative)),
                "language": language,
                "trigger_kinds": kinds,
            })
    return sorted(consumers, key=lambda row: row["path"])


def _contains_prohibited_content(value: Any) -> bool:
    if isinstance(value, str):
        return "CARR-SECRET-CANARY-7F4A" in value or bool(PROHIBITED_VALUE_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_prohibited_content(key) or _contains_prohibited_content(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_prohibited_content(item) for item in value)
    return False


def _validate_binding(root: Path, value: Any, expected_path: str, label: str) -> None:
    binding = _exact_object(value, BINDING_FIELDS, f"{label} binding fields are invalid")
    if binding["path"] != expected_path or not isinstance(binding["sha256"], str) or not SHA256_RE.fullmatch(binding["sha256"]):
        raise ReviewGateError(f"{label} binding is invalid")
    if _sha256(_repo_file(root, expected_path)) != binding["sha256"]:
        raise ReviewGateError(f"{label} binding is stale")


def _policy_routes(root: Path) -> set[str]:
    try:
        routes = json.loads(_repo_file(root, POLICY_REL).read_text())["routes"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReviewGateError("router policy cannot be loaded") from exc
    names = {row.get("tool_name") for row in routes if isinstance(row, dict)}
    if not names or not all(isinstance(name, str) and name for name in names):
        raise ReviewGateError("router policy routes are invalid")
    return names


def _validate_review(root: Path, row: Any, expected: dict[str, Any], routes: set[str]) -> None:
    review = _exact_object(row, REVIEW_FIELDS, "integration review row fields are invalid")
    consumer = _exact_object(review["consumer"], CONSUMER_FIELDS, "integration review consumer fields are invalid")
    if not isinstance(consumer["entrypoint"], str) or not SAFE_NAME_RE.fullmatch(consumer["entrypoint"]):
        raise ReviewGateError("integration review entrypoint is invalid")
    if not isinstance(consumer["trigger_kinds"], list) or consumer["trigger_kinds"] != expected["trigger_kinds"]:
        raise ReviewGateError("integration review trigger kinds are stale")
    for field in ("path", "sha256", "language"):
        if consumer[field] != expected[field]:
            raise ReviewGateError("integration review consumer binding is stale")
    requested_routes = review["requested_routes"]
    if (
        not isinstance(requested_routes, list) or not requested_routes
        or not all(isinstance(route, str) and route in routes for route in requested_routes)
        or len(requested_routes) != len(set(requested_routes))
    ):
        raise ReviewGateError("integration review requested routes are invalid")
    if review["execution_mode"] not in {"descriptor_only", "read_only_tool_execution"}:
        raise ReviewGateError("integration review execution mode is invalid")
    security = _exact_object(review["security"], SECURITY_FIELDS, "integration review security fields are invalid")
    required_true = {
        "server_owned_identity_tenant_sponsor_capability", "caller_override_denied",
        "route_action_risk_revalidated", "passing_envelope_required", "redacted_audit_observability",
    }
    if any(security[field] is not True for field in required_true):
        raise ReviewGateError("integration review security decision is incomplete")
    if (
        not isinstance(security["rollback_owner"], str) or not SAFE_NAME_RE.fullmatch(security["rollback_owner"])
        or not isinstance(security["disable_path"], str) or not SAFE_NAME_RE.fullmatch(security["disable_path"])
        or security["safe_behavior"] != "refuse"
    ):
        raise ReviewGateError("integration review rollback decision is invalid")


def validate_artifact(root: Path, consumers: list[dict[str, Any]], artifact: Any) -> None:
    if _contains_prohibited_content(artifact):
        raise ReviewGateError("integration review contains prohibited payload content")
    doc = _exact_object(artifact, ARTIFACT_FIELDS, "integration review artifact fields are invalid")
    if (
        type(doc["schema_version"]) is not int or doc["schema_version"] != 1
        or doc["artifact_type"] != "read_router_integration_review"
        or doc["data_class"] != "D2_internal_metadata_only"
        or doc["execution"] != "offline_deterministic"
        or doc["calls_models"] is not False or doc["writes_records"] is not False
        or doc["allowed_actions"] != [] or doc["review_status"] != "reviewed_not_runtime_authorization"
    ):
        raise ReviewGateError("integration review artifact is not metadata-only")
    _validate_binding(root, doc["router_module"], ROUTER_MODULE_REL, "router module")
    _validate_binding(root, doc["policy"], POLICY_REL, "router policy")
    _validate_binding(root, doc["envelope_suite"], ENVELOPE_SUITE_REL, "envelope suite")
    if not isinstance(doc["reviews"], list) or len(doc["reviews"]) != len(consumers):
        raise ReviewGateError("integration review coverage is incomplete")
    routes = _policy_routes(root)
    expected_by_path = {row["path"]: row for row in consumers}
    seen: set[str] = set()
    for row in doc["reviews"]:
        if not isinstance(row, dict) or not isinstance(row.get("consumer"), dict):
            raise ReviewGateError("integration review row is invalid")
        path = row["consumer"].get("path")
        if not isinstance(path, str) or path in seen or path not in expected_by_path:
            raise ReviewGateError("integration review coverage contains an orphan or duplicate")
        seen.add(path)
        _validate_review(root, row, expected_by_path[path], routes)
    if seen != set(expected_by_path):
        raise ReviewGateError("integration review coverage is incomplete")


def check(root: Path) -> int:
    consumers = detect_consumers(root)
    if not consumers:
        print("ai-router-integration-review: no production consumer; review artifact not required")
        return 0
    artifact_path = root / REVIEW_ARTIFACT_REL
    if not artifact_path.is_file():
        raise ReviewGateError("integration review artifact is required for a router consumer")
    try:
        artifact = json.loads(artifact_path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise ReviewGateError("integration review artifact cannot be loaded") from exc
    validate_artifact(root, consumers, artifact)
    print(f"ai-router-integration-review: {len(consumers)} reviewed consumer(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    try:
        return check(args.repo.resolve())
    except ReviewGateError as exc:
        print(f"ai-router-integration-review: {REVIEW_ARTIFACT_REL}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
