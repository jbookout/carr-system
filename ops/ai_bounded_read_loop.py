#!/usr/bin/env python3
"""Bounded, offline plan-to-route-to-verification workflow.

The workflow exercises the fixed read router against synthetic observations.
It never calls a model, MCP tool, database, network client, or injected function.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import ai_read_router


REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "evals" / "ai" / "bounded-read-loop.v1.json"
OBSERVATIONS_PATH = REPO_ROOT / "evals" / "ai" / "bounded-read-loop-observations.v1.json"
POLICY_SHA256 = "18a07f9c531a1df69c1a1c9f05de1b5ce9777567691406d8ed9ae74d7d28643d"
OBSERVATIONS_SHA256 = "96d1cb3bdef8716d27b09b19eed9c650ec93cb8bcf1d5a38d2b648d0b39aaf7d"
POLICY_FIELDS = {
    "schema_version", "artifact_type", "data_class", "execution", "calls_models",
    "invokes_tools", "writes_records", "allowed_actions", "task_id", "task_type",
    "caps", "plan", "verification",
}
CAP_FIELDS = {
    "max_steps", "max_attempts_per_step", "max_failures", "max_elapsed_ms",
    "max_cost_usd",
}
PLAN_FIELDS = {"step", "tool_name", "arguments", "evidence_kind"}
VERIFICATION_FIELDS = {
    "completion_requires", "minimum_evidence_refs_per_step", "allowed_failure_codes",
}
OBSERVATION_FIELDS = {
    "step", "state", "route_digest", "observed_elapsed_ms", "observed_cost_usd",
    "evidence_kind", "evidence_refs", "failure_code",
}
OBSERVATION_ARTIFACT_FIELDS = {
    "schema_version", "artifact_type", "data_class", "execution", "calls_models",
    "invokes_tools", "writes_records", "allowed_actions", "policy_digest", "observations",
}
LOOP_VIOLATION_CODES = {
    "loop_policy_invalid", "loop_observation_invalid", "loop_cap_exceeded",
    "loop_route_refused", "loop_step_failed", "loop_verification_missing",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$")
PROHIBITED_RE = re.compile(r"canary|secret|password|api[_-]?key|token", re.IGNORECASE)


class LoopError(ValueError):
    """A fixed loop policy or observation is invalid."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exact_object(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise LoopError("loop_policy_invalid")
    return value


def _exact_positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _safe_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and SAFE_ID_RE.fullmatch(value) is not None
        and PROHIBITED_RE.search(value) is None
    )


def _validate_policy(value: Any) -> dict[str, Any]:
    policy = _exact_object(value, POLICY_FIELDS)
    if (
        type(policy["schema_version"]) is not int or policy["schema_version"] != 1
        or policy["artifact_type"] != "synthetic_bounded_read_loop_policy"
        or policy["data_class"] != "synthetic_only"
        or policy["execution"] != "offline_deterministic"
        or policy["calls_models"] is not False
        or policy["invokes_tools"] is not False
        or policy["writes_records"] is not False
        or policy["allowed_actions"] != []
        or not _safe_id(policy["task_id"])
        or policy["task_type"] != "record_catch_up"
    ):
        raise LoopError("loop_policy_invalid")
    caps = _exact_object(policy["caps"], CAP_FIELDS)
    if (
        not all(_exact_positive_int(caps[field]) for field in (
            "max_steps", "max_attempts_per_step", "max_failures", "max_elapsed_ms"
        ))
        or caps["max_steps"] != 2
        or caps["max_attempts_per_step"] != 1
        or caps["max_failures"] != 1
        or not isinstance(caps["max_cost_usd"], (int, float))
        or isinstance(caps["max_cost_usd"], bool)
        or not math.isfinite(caps["max_cost_usd"])
        or caps["max_cost_usd"] != 0
    ):
        raise LoopError("loop_policy_invalid")
    if not isinstance(policy["plan"], list) or len(policy["plan"]) != caps["max_steps"]:
        raise LoopError("loop_policy_invalid")
    expected_tools = ["find", "catch-me-up"]
    expected_evidence = ["record_match", "timeline_source"]
    for index, row in enumerate(policy["plan"], start=1):
        plan = _exact_object(row, PLAN_FIELDS)
        if (
            type(plan["step"]) is not int or plan["step"] != index
            or plan["tool_name"] != expected_tools[index - 1]
            or plan["evidence_kind"] != expected_evidence[index - 1]
            or not isinstance(plan["arguments"], dict)
        ):
            raise LoopError("loop_policy_invalid")
    verification = _exact_object(policy["verification"], VERIFICATION_FIELDS)
    failures = verification["allowed_failure_codes"]
    if (
        verification["completion_requires"] != "external_observation_evidence"
        or type(verification["minimum_evidence_refs_per_step"]) is not int
        or verification["minimum_evidence_refs_per_step"] != 1
        or not isinstance(failures, list) or not failures
        or not all(_safe_id(code) for code in failures)
        or len(failures) != len(set(failures))
    ):
        raise LoopError("loop_policy_invalid")
    return policy


def _load_fixed_policy() -> dict[str, Any]:
    if _sha256(POLICY_PATH) != POLICY_SHA256:
        raise LoopError("loop_policy_invalid")
    try:
        return _validate_policy(json.loads(POLICY_PATH.read_text()))
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, LoopError):
            raise
        raise LoopError("loop_policy_invalid") from exc


def _load_fixed_observations() -> list[dict[str, Any]]:
    if _sha256(OBSERVATIONS_PATH) != OBSERVATIONS_SHA256:
        raise LoopError("loop_observation_invalid")
    try:
        artifact = json.loads(OBSERVATIONS_PATH.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise LoopError("loop_observation_invalid") from exc
    if not isinstance(artifact, dict) or set(artifact) != OBSERVATION_ARTIFACT_FIELDS:
        raise LoopError("loop_observation_invalid")
    if (
        type(artifact["schema_version"]) is not int or artifact["schema_version"] != 1
        or artifact["artifact_type"] != "synthetic_bounded_read_loop_observations"
        or artifact["data_class"] != "synthetic_only"
        or artifact["execution"] != "offline_deterministic"
        or artifact["calls_models"] is not False
        or artifact["invokes_tools"] is not False
        or artifact["writes_records"] is not False
        or artifact["allowed_actions"] != []
        or artifact["policy_digest"] != POLICY_SHA256
        or not isinstance(artifact["observations"], list)
    ):
        raise LoopError("loop_observation_invalid")
    return artifact["observations"]


def _base_result(state: str, code: str | None, steps: int, elapsed: int, cost: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "state": state,
        "steps_completed": steps,
        "observed_elapsed_ms": elapsed,
        "observed_cost_usd": float(cost),
        "calls_models": False,
        "invokes_tools": False,
        "writes_records": False,
        "allowed_actions": [],
    }
    if code is not None:
        if code not in LOOP_VIOLATION_CODES:
            raise LoopError("loop_policy_invalid")
        result["violation_codes"] = [code]
    return result


def _refuse(code: str, steps: int = 0, elapsed: int = 0, cost: float = 0.0) -> dict[str, Any]:
    return _base_result("refused", code, steps, elapsed, cost)


def _validate_observation(
    value: Any,
    step: int,
    route_digest: str,
    evidence_kind: str,
    allowed_failures: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != OBSERVATION_FIELDS:
        raise LoopError("loop_observation_invalid")
    if (
        type(value["step"]) is not int or value["step"] != step
        or value["state"] not in {"succeeded", "failed"}
        or not isinstance(value["route_digest"], str)
        or SHA256_RE.fullmatch(value["route_digest"]) is None
        or value["route_digest"] != route_digest
        or type(value["observed_elapsed_ms"]) is not int
        or value["observed_elapsed_ms"] < 0
        or not isinstance(value["observed_cost_usd"], (int, float))
        or isinstance(value["observed_cost_usd"], bool)
        or not math.isfinite(value["observed_cost_usd"])
        or value["observed_cost_usd"] < 0
        or value["evidence_kind"] != evidence_kind
        or not isinstance(value["evidence_refs"], list)
        or not all(_safe_id(ref) for ref in value["evidence_refs"])
        or len(value["evidence_refs"]) != len(set(value["evidence_refs"]))
    ):
        raise LoopError("loop_observation_invalid")
    if value["state"] == "succeeded":
        if value["failure_code"] is not None:
            raise LoopError("loop_observation_invalid")
        if not value["evidence_refs"]:
            raise LoopError("loop_verification_missing")
    elif value["failure_code"] not in allowed_failures or value["evidence_refs"]:
        raise LoopError("loop_observation_invalid")
    return value


def _evaluate_observations(observations: Any, response_envelope: Any) -> dict[str, Any]:
    """Route a fixed two-step synthetic read plan and verify external observations."""
    try:
        policy = _load_fixed_policy()
    except LoopError:
        return _refuse("loop_policy_invalid")
    if not isinstance(observations, list) or len(observations) != len(policy["plan"]):
        return _refuse("loop_observation_invalid")
    elapsed = 0
    cost = 0.0
    route_digests: list[str] = []
    evidence_digests: list[str] = []
    allowed_failures = set(policy["verification"]["allowed_failure_codes"])
    for index, plan in enumerate(policy["plan"], start=1):
        proposal = {
            "schema_version": 1,
            "tool_name": plan["tool_name"],
            "arguments": plan["arguments"],
        }
        routed = ai_read_router.route_read_only(proposal, response_envelope)
        if routed.get("state") != "accepted":
            return _refuse("loop_route_refused", index - 1, elapsed, cost)
        route_digest = canonical_digest(proposal)
        try:
            observation = _validate_observation(
                observations[index - 1], index, route_digest,
                plan["evidence_kind"], allowed_failures,
            )
        except LoopError as exc:
            return _refuse(exc.code, index - 1, elapsed, cost)
        elapsed += observation["observed_elapsed_ms"]
        cost += float(observation["observed_cost_usd"])
        if elapsed > policy["caps"]["max_elapsed_ms"] or cost > policy["caps"]["max_cost_usd"]:
            return _refuse("loop_cap_exceeded", index - 1, elapsed, cost)
        if observation["state"] == "failed":
            return _refuse("loop_step_failed", index - 1, elapsed, cost)
        route_digests.append(route_digest)
        evidence_digests.append(canonical_digest({
            "evidence_kind": observation["evidence_kind"],
            "evidence_refs": observation["evidence_refs"],
        }))
    result = _base_result("completed", None, len(policy["plan"]), elapsed, cost)
    result.update({
        "task_id": policy["task_id"],
        "policy_digest": POLICY_SHA256,
        "plan_digest": canonical_digest(policy["plan"]),
        "route_digests": route_digests,
        "evidence_digests": evidence_digests,
        "verification_state": "pinned_synthetic_evidence",
    })
    return result


def run_bounded_read_loop(response_envelope: Any) -> dict[str, Any]:
    """Run the fixed D1 workflow against its independently pinned observations."""
    try:
        observations = _load_fixed_observations()
    except LoopError as exc:
        return _refuse(exc.code)
    return _evaluate_observations(observations, response_envelope)
