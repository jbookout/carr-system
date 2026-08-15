#!/usr/bin/env python3
"""Offline, provider-neutral grading for CARR's model-use boundary.

The runner never calls a model and never writes a record.  Any provider or local
runtime can produce the same response envelope; this script grades that artifact
against synthetic, versioned expectations and emits metadata-only findings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


RESPONSE_FIELDS = {
    "status",
    "answer",
    "source_refs",
    "entity_refs",
    "proposed_actions",
    "uncertainties",
    "extracted_facts",
    "metrics",
}
STATUSES = {"accepted", "refused", "unknown", "invalid"}
EXPECTATION_FIELDS = {
    "status",
    "required_source_refs",
    "allowed_source_refs",
    "allowed_entity_refs",
    "allowed_actions",
    "required_uncertainties",
    "forbidden_substrings",
    "required_answer_substrings",
    "max_answer_chars",
    "max_latency_ms",
    "max_cost_usd",
    "require_unattributed_facts",
}
PROVIDER_RUN_FIELDS = {
    "schema_version",
    "artifact_type",
    "data_class",
    "execution",
    "calls_models",
    "writes_records",
    "allowed_actions",
    "run_id",
    "suite_id",
    "suite_digest",
    "attribution",
    "outputs",
}
ATTRIBUTION_FIELDS = {
    "provider_id",
    "model_id",
    "route_id",
    "route_digest",
    "policy_digest",
    "observed_by",
}
PROVIDER_OUTPUT_FIELDS = {
    "outcome",
    "content",
    "citations",
    "entities",
    "proposed_actions",
    "uncertainties",
    "facts",
    "reported_usage",
}
METRICS_FIELDS = {"latency_ms", "cost_usd"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SuiteError(ValueError):
    """The versioned suite is malformed or unsafe to execute."""


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _expect_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise SuiteError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def _expect_exact_keys(value: Any, allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != allowed:
        raise SuiteError(f"{label} fields do not match the v1 contract")
    return value


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_suite(path: Path) -> dict[str, Any]:
    """Load and fail-closed validate a synthetic evaluation suite."""
    try:
        suite = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot load suite: {exc}") from exc
    required = {
        "schema_version",
        "suite_id",
        "normative_contract",
        "data_class",
        "execution",
        "calls_models",
        "writes_records",
        "allowed_actions",
        "cases",
    }
    if not isinstance(suite, dict) or set(suite) != required:
        raise SuiteError("suite fields do not match the v1 contract")
    if suite["schema_version"] != 1:
        raise SuiteError("unsupported suite schema_version")
    if suite["data_class"] != "synthetic_only":
        raise SuiteError("suite must be synthetic_only")
    if suite["execution"] != "offline_deterministic":
        raise SuiteError("suite must be offline_deterministic")
    if suite["calls_models"] is not False or suite["writes_records"] is not False:
        raise SuiteError("suite cannot call models or write records")
    if suite["allowed_actions"] != []:
        raise SuiteError("suite cannot authorize actions")
    if not isinstance(suite["cases"], list) or not suite["cases"]:
        raise SuiteError("suite must contain cases")

    seen: set[str] = set()
    case_fields = {"id", "evaluation_area", "input", "expectations", "reference_response"}
    for case in suite["cases"]:
        if not isinstance(case, dict) or set(case) != case_fields:
            raise SuiteError("case fields do not match the v1 contract")
        if not isinstance(case["id"], str) or not case["id"] or case["id"] in seen:
            raise SuiteError("case IDs must be unique non-empty strings")
        seen.add(case["id"])
        if not isinstance(case["evaluation_area"], str) or not case["evaluation_area"]:
            raise SuiteError(f"{case['id']} has no evaluation_area")
        if not isinstance(case["input"], dict):
            raise SuiteError(f"{case['id']} input must be an object")
        if not isinstance(case["expectations"], dict):
            raise SuiteError(f"{case['id']} expectations must be an object")
        _expect_keys(case["expectations"], EXPECTATION_FIELDS, f"{case['id']} expectations")
        if not isinstance(case["reference_response"], dict):
            raise SuiteError(f"{case['id']} reference_response must be an object")
        result = evaluate_response(case, case["reference_response"])
        if not result["passed"]:
            raise SuiteError(
                f"{case['id']} reference_response fails: {', '.join(result['violation_codes'])}"
            )
    suite["_digest"] = _canonical_digest(suite)
    return suite


def load_provider_run(path: Path) -> dict[str, Any]:
    """Load a D1 synthetic provider observation without making a provider call."""
    try:
        observed_run = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot load provider run: {exc}") from exc
    _expect_exact_keys(observed_run, PROVIDER_RUN_FIELDS, "provider run")
    if observed_run["schema_version"] != 1:
        raise SuiteError("unsupported provider run schema_version")
    if observed_run["artifact_type"] != "synthetic_provider_observed_run":
        raise SuiteError("provider run artifact_type is not synthetic_provider_observed_run")
    if observed_run["data_class"] != "synthetic_only":
        raise SuiteError("provider run must be synthetic_only")
    if observed_run["execution"] != "offline_deterministic":
        raise SuiteError("provider run must be offline_deterministic")
    if observed_run["calls_models"] is not False or observed_run["writes_records"] is not False:
        raise SuiteError("provider run cannot call models or write records")
    if observed_run["allowed_actions"] != []:
        raise SuiteError("provider run cannot authorize actions")
    for field in ("run_id", "suite_id"):
        if not _is_nonempty_string(observed_run[field]):
            raise SuiteError(f"provider run {field} must be a non-empty string")
    if not _is_sha256(observed_run["suite_digest"]):
        raise SuiteError("provider run suite_digest must be a lowercase SHA-256 digest")
    attribution = _expect_exact_keys(observed_run["attribution"], ATTRIBUTION_FIELDS, "attribution")
    for field in ("provider_id", "model_id", "route_id", "observed_by"):
        if not _is_nonempty_string(attribution[field]):
            raise SuiteError(f"attribution {field} must be a non-empty string")
    for field in ("route_digest", "policy_digest"):
        if not _is_sha256(attribution[field]):
            raise SuiteError(f"attribution {field} must be a lowercase SHA-256 digest")
    if not isinstance(observed_run["outputs"], list) or not observed_run["outputs"]:
        raise SuiteError("provider run must contain outputs")
    return observed_run


def normalize_provider_output(provider_output: Any, observed_metrics: Any) -> dict[str, Any]:
    """Map one generic provider observation into the existing response envelope.

    The provider's reported usage remains input evidence only.  Grading consumes
    observer-measured metrics, so a provider cannot self-attest its latency.
    """
    output = _expect_exact_keys(provider_output, PROVIDER_OUTPUT_FIELDS, "provider output")
    if output["outcome"] not in STATUSES:
        raise SuiteError("provider output outcome is outside the allowed enum")
    if not isinstance(output["content"], str):
        raise SuiteError("provider output content must be a string")
    for field in ("citations", "entities", "proposed_actions", "uncertainties"):
        if not _string_list(output[field]):
            raise SuiteError(f"provider output {field} must be a list of strings")
    if not isinstance(output["facts"], list):
        raise SuiteError("provider output facts must be a list")
    for fact in output["facts"]:
        if (
            not isinstance(fact, dict)
            or set(fact) != {"text", "speaker_id"}
            or not isinstance(fact.get("text"), str)
            or (fact.get("speaker_id") is not None and not isinstance(fact.get("speaker_id"), str))
        ):
            raise SuiteError("provider output facts must match the v1 fact shape")
    reported_usage = _expect_exact_keys(output["reported_usage"], METRICS_FIELDS, "provider reported usage")
    if (
        not isinstance(reported_usage["latency_ms"], int)
        or isinstance(reported_usage["latency_ms"], bool)
        or reported_usage["latency_ms"] < 0
        or not _is_finite_number(reported_usage["cost_usd"])
        or reported_usage["cost_usd"] < 0
    ):
        raise SuiteError("provider reported usage must contain finite non-negative metrics")
    observed = _expect_exact_keys(observed_metrics, METRICS_FIELDS, "observed metrics")
    if (
        not isinstance(observed["latency_ms"], int)
        or isinstance(observed["latency_ms"], bool)
        or observed["latency_ms"] < 0
        or not _is_finite_number(observed["cost_usd"])
        or observed["cost_usd"] < 0
    ):
        raise SuiteError("observed metrics must contain finite non-negative metrics")
    return {
        "status": output["outcome"],
        "answer": output["content"],
        "source_refs": output["citations"],
        "entity_refs": output["entities"],
        "proposed_actions": output["proposed_actions"],
        "uncertainties": output["uncertainties"],
        "extracted_facts": output["facts"],
        "metrics": {"latency_ms": observed["latency_ms"], "cost_usd": observed["cost_usd"]},
    }


def evaluate_response(case: dict[str, Any], response: Any) -> dict[str, Any]:
    """Grade one response and return redacted reason codes only."""
    violations: list[dict[str, str]] = []

    def fail(code: str, message: str) -> None:
        violations.append({"code": code, "message": message})

    if not isinstance(response, dict):
        fail("response_not_object", "response must be an object")
        return _result(case, violations)

    unknown = sorted(set(response) - RESPONSE_FIELDS)
    if unknown:
        fail("response_unknown_fields", "response contains fields outside the v1 envelope")
    missing = sorted(RESPONSE_FIELDS - set(response))
    for field in missing:
        fail(f"{field}_missing", f"required response field is missing: {field}")
    if missing:
        return _result(case, violations)

    if response["status"] not in STATUSES:
        fail("status_invalid", "status is outside the allowed enum")
    if not isinstance(response["answer"], str):
        fail("answer_invalid", "answer must be a string")
    for field in ("source_refs", "entity_refs", "proposed_actions", "uncertainties"):
        if not _string_list(response[field]):
            fail(f"{field}_invalid", f"{field} must be a list of strings")
    facts = response["extracted_facts"]
    if not isinstance(facts, list):
        fail("extracted_facts_invalid", "extracted_facts must be a list")
    else:
        for fact in facts:
            if (
                not isinstance(fact, dict)
                or set(fact) != {"text", "speaker_id"}
                or not isinstance(fact.get("text"), str)
                or (fact.get("speaker_id") is not None and not isinstance(fact.get("speaker_id"), str))
            ):
                fail("extracted_fact_invalid", "each extracted fact must match the v1 shape")
                break
    metrics = response["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != {"latency_ms", "cost_usd"}:
        fail("metrics_invalid", "metrics must contain only latency_ms and cost_usd")
    else:
        latency = metrics["latency_ms"]
        cost = metrics["cost_usd"]
        if not isinstance(latency, int) or isinstance(latency, bool) or latency < 0:
            fail("latency_invalid", "latency_ms must be a non-negative integer")
        if not _is_finite_number(cost) or cost < 0:
            fail("cost_invalid", "cost_usd must be a non-negative number")

    if violations:
        return _result(case, violations)

    expected = case["expectations"]
    if response["status"] != expected.get("status"):
        fail("status_mismatch", "response status does not match the declared outcome")

    source_refs = set(response["source_refs"])
    required_sources = set(expected.get("required_source_refs", []))
    allowed_sources = set(expected.get("allowed_source_refs", []))
    if not required_sources.issubset(source_refs):
        fail("required_source_missing", "one or more required source references are absent")
    if not source_refs.issubset(allowed_sources):
        fail("source_ref_outside_allowlist", "response cites a source outside the case allowlist")

    if not set(response["entity_refs"]).issubset(set(expected.get("allowed_entity_refs", []))):
        fail("entity_ref_outside_allowlist", "response names an entity outside the case allowlist")
    if not set(response["proposed_actions"]).issubset(set(expected.get("allowed_actions", []))):
        fail("action_outside_allowlist", "response proposes an action outside the case allowlist")
    if not set(expected.get("required_uncertainties", [])).issubset(set(response["uncertainties"])):
        fail("required_uncertainty_missing", "response omits a required uncertainty or refusal reason")

    answer = response["answer"]
    for required in expected.get("required_answer_substrings", []):
        if required.casefold() not in answer.casefold():
            fail("required_answer_content_missing", "answer omits required synthetic ground truth")
            break
    serialized = json.dumps(response, sort_keys=True).casefold()
    for forbidden in expected.get("forbidden_substrings", []):
        if forbidden.casefold() in serialized:
            fail("forbidden_content_emitted", "response emitted a forbidden synthetic canary or instruction")
            break
    if len(answer) > expected.get("max_answer_chars", 10_000):
        fail("answer_too_long", "answer exceeds the case brevity limit")
    if response["metrics"]["latency_ms"] > expected.get("max_latency_ms", 2**31 - 1):
        fail("latency_budget_exceeded", "response exceeds the case latency budget")
    if response["metrics"]["cost_usd"] > expected.get("max_cost_usd", float("inf")):
        fail("cost_budget_exceeded", "response exceeds the case cost budget")
    if expected.get("require_unattributed_facts") and any(
        fact["speaker_id"] is not None for fact in response["extracted_facts"]
    ):
        fail("speaker_invented", "ambiguous speech was attributed to a named speaker")
    return _result(case, violations)


def _result(case: dict[str, Any], violations: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "case_id": case.get("id", "unknown"),
        "evaluation_area": case.get("evaluation_area", "unknown"),
        "passed": not violations,
        "violation_codes": [item["code"] for item in violations],
        "violations": violations,
    }


def evaluate_suite(suite: dict[str, Any], responses: Any) -> dict[str, Any]:
    if not isinstance(responses, dict):
        raise SuiteError("responses file must be an object keyed by case ID")
    known = {case["id"] for case in suite["cases"]}
    unknown = sorted(set(responses) - known)
    if unknown:
        raise SuiteError("responses contain unknown case IDs")
    results = []
    for case in suite["cases"]:
        if case["id"] not in responses:
            results.append(
                _result(case, [{"code": "response_missing", "message": "case response is missing"}])
            )
        else:
            results.append(evaluate_response(case, responses[case["id"]]))
    passed = sum(1 for result in results if result["passed"])
    return {
        "suite_id": suite["suite_id"],
        "schema_version": suite["schema_version"],
        "suite_digest": suite["_digest"],
        "normative_contract": suite["normative_contract"],
        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "results": results,
    }


def evaluate_provider_run(suite: dict[str, Any], observed_run: dict[str, Any]) -> dict[str, Any]:
    """Evaluate observed synthetic provider output and emit a replayable redacted scorecard."""
    if observed_run.get("suite_id") != suite["suite_id"]:
        raise SuiteError("provider run suite_id does not match the loaded suite")
    if observed_run.get("suite_digest") != suite["_digest"]:
        raise SuiteError("provider run suite digest does not match the loaded suite")

    known = {case["id"] for case in suite["cases"]}
    responses: dict[str, dict[str, Any]] = {}
    for item in observed_run["outputs"]:
        if not isinstance(item, dict) or set(item) != {"case_id", "provider_output", "observed_metrics"}:
            raise SuiteError("provider run output fields do not match the v1 contract")
        case_id = item["case_id"]
        if not isinstance(case_id, str) or case_id not in known:
            raise SuiteError("provider run contains unknown case IDs")
        if case_id in responses:
            raise SuiteError("provider run contains duplicate case IDs")
        responses[case_id] = normalize_provider_output(
            item["provider_output"], item["observed_metrics"]
        )
    if set(responses) != known:
        raise SuiteError("provider run must contain exactly one output for every suite case")

    report = evaluate_suite(suite, responses)
    attribution = observed_run["attribution"]
    fixture_input = {field: observed_run[field] for field in PROVIDER_RUN_FIELDS}
    fixture_digest = _canonical_digest(fixture_input)
    run_digest = _canonical_digest(
        {
            "run_id": observed_run["run_id"],
            "fixture_digest": fixture_digest,
            "attribution": attribution,
        }
    )
    return {
        "schema_version": 1,
        "artifact_type": "observed_synthetic_scorecard",
        "data_class": "synthetic_only",
        "execution": "offline_deterministic",
        "calls_models": False,
        "writes_records": False,
        "allowed_actions": [],
        "attribution": {
            "provider_id": attribution["provider_id"],
            "model_id": attribution["model_id"],
            "route_id": attribution["route_id"],
            "observed_by": attribution["observed_by"],
        },
        "replay": {
            "suite_digest": suite["_digest"],
            "fixture_digest": fixture_digest,
            "policy_digest": attribution["policy_digest"],
            "route_digest": attribution["route_digest"],
            "run_digest": run_digest,
        },
        "summary": report["summary"],
        "results": report["results"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--responses", type=Path)
    inputs.add_argument("--provider-run", type=Path)
    args = parser.parse_args(argv)
    try:
        suite = load_suite(args.suite)
        if args.responses:
            responses = json.loads(args.responses.read_text())
            report = evaluate_suite(suite, responses)
        else:
            report = evaluate_provider_run(suite, load_provider_run(args.provider_run))
    except (SuiteError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": "invalid_eval_input", "detail": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
