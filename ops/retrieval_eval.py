#!/usr/bin/env python3
"""Grade CARR's existing lexical retrieval against versioned golden queries.

The evaluator is read-only. It scores the production section-index algorithm
directly and consumes a metadata-only observation from doctrine Postgres FTS.
The two engines remain separate because their matching semantics differ.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from retrieval_lexical import rank_index  # noqa: E402


SUITE_SCHEMA = "carr-retrieval-golden-v1"
OBSERVATION_SCHEMA = "carr-doctrine-fts-observation-v1"
ENGINES = ("section_index_lexical", "doctrine_postgres_fts")


class ContractError(ValueError):
    pass


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"{path}: root must be an object")
    return value


def validate_suite(suite: dict[str, Any]) -> None:
    if suite.get("schema_version") != SUITE_SCHEMA:
        raise ContractError("unsupported retrieval suite schema")
    for field in ("suite_id", "data_class", "scope_ref"):
        if not isinstance(suite.get(field), str) or not suite[field].strip():
            raise ContractError(f"suite {field} must be non-empty")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ContractError("suite cases must be a non-empty array")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ContractError("every retrieval case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ContractError("retrieval case ids must be unique non-empty strings")
        seen.add(case_id)
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ContractError(f"{case_id}: query must be non-empty")
        engines = case.get("engines")
        if not isinstance(engines, dict) or not engines:
            raise ContractError(f"{case_id}: engines must be non-empty")
        unknown = set(engines) - set(ENGINES)
        if unknown:
            raise ContractError(f"{case_id}: unknown engines {sorted(unknown)}")
        for engine, expectation in engines.items():
            if not isinstance(expectation, dict):
                raise ContractError(f"{case_id}/{engine}: expectation must be an object")
            targets = expectation.get("required_targets")
            top_k = expectation.get("top_k")
            if not isinstance(targets, list) or not targets or not all(isinstance(x, str) and x for x in targets):
                raise ContractError(f"{case_id}/{engine}: required_targets must be non-empty strings")
            if not isinstance(top_k, int) or top_k < 1 or top_k > 20:
                raise ContractError(f"{case_id}/{engine}: top_k must be 1..20")
        forbidden = case.get("forbidden_target_patterns", [])
        if not isinstance(forbidden, list) or not all(isinstance(x, str) and x for x in forbidden):
            raise ContractError(f"{case_id}: forbidden_target_patterns must be strings")


def evaluate_hits(
    *, case: dict[str, Any], engine: str, hits: list[dict[str, Any]],
    scope_proven: bool, unavailable: bool = False,
) -> dict[str, Any]:
    expectation = case["engines"][engine]
    if unavailable:
        return {"case_id": case["id"], "status": "unknown", "reason": "engine_unavailable"}
    top_k = expectation["top_k"]
    visible = sorted(hits, key=lambda hit: int(hit.get("rank", 10**9)))[:top_k]
    targets = set(expectation["required_targets"])
    target_hits = [hit for hit in visible if hit.get("target") in targets]
    forbidden = [re.compile(pattern, re.IGNORECASE) for pattern in case.get("forbidden_target_patterns", [])]
    forbidden_hits = [
        hit.get("target", "") for hit in visible
        if any(pattern.search(str(hit.get("target", ""))) for pattern in forbidden)
    ]
    current = bool(target_hits) and all(hit.get("current") is True for hit in target_hits)
    provenance = bool(target_hits) and all(hit.get("provenance_complete") is True for hit in target_hits)
    passed = bool(target_hits) and current and provenance and not forbidden_hits and scope_proven
    rank = min((int(hit["rank"]) for hit in target_hits), default=None)
    reciprocal_rank = 0.0 if rank is None else 1.0 / rank
    return {
        "case_id": case["id"],
        "status": "pass" if passed else "fail",
        "required_targets": sorted(targets),
        "observed_targets": [hit.get("target") for hit in visible],
        "best_required_rank": rank,
        "reciprocal_rank": reciprocal_rank,
        "current_source": current,
        "provenance_complete": provenance,
        "scope_applied_before_rank": scope_proven,
        "forbidden_hits": forbidden_hits,
    }


def run(
    suite: dict[str, Any], *, section_index: pathlib.Path,
    doctrine_observation: dict[str, Any],
) -> dict[str, Any]:
    validate_suite(suite)
    if doctrine_observation.get("schema_version") != OBSERVATION_SCHEMA:
        raise ContractError("unsupported doctrine observation schema")
    if doctrine_observation.get("suite_id") != suite["suite_id"]:
        raise ContractError("doctrine observation suite_id mismatch")
    if doctrine_observation.get("scope_ref") != suite["scope_ref"]:
        raise ContractError("doctrine observation scope mismatch")
    observation_status = doctrine_observation.get("status")
    if observation_status not in ("measured", "unavailable"):
        raise ContractError("doctrine observation status must be measured or unavailable")
    observed_cases = doctrine_observation.get("cases", {})
    if not isinstance(observed_cases, dict):
        raise ContractError("doctrine observation cases must be an object")

    engines: dict[str, Any] = {
        "section_index_lexical": {
            "status": "measured",
            # One index artifact is built from one declared CARR vault scope;
            # no foreign rows are introduced after ranking begins.
            "scope_ref": suite["scope_ref"],
            "scope_applied_before_rank": True,
            "cases": [],
        },
        "doctrine_postgres_fts": {
            "status": observation_status,
            "scope_ref": suite["scope_ref"],
            "scope_applied_before_rank": doctrine_observation.get("scope_applied_before_rank") is True,
            "cases": [],
        },
    }
    for case in suite["cases"]:
        if "section_index_lexical" in case["engines"]:
            expectation = case["engines"]["section_index_lexical"]
            ranked = rank_index(section_index, case["query"], top=expectation["top_k"])
            hits = [{
                "target": item.row.path,
                "rank": rank,
                "current": True,
                "provenance_complete": item.row.source in ("file", "store") and bool(item.row.path),
            } for rank, item in enumerate(ranked, 1)]
            engines["section_index_lexical"]["cases"].append(evaluate_hits(
                case=case, engine="section_index_lexical", hits=hits, scope_proven=True,
            ))
        if "doctrine_postgres_fts" in case["engines"]:
            engines["doctrine_postgres_fts"]["cases"].append(evaluate_hits(
                case=case,
                engine="doctrine_postgres_fts",
                hits=observed_cases.get(case["id"], []),
                scope_proven=engines["doctrine_postgres_fts"]["scope_applied_before_rank"],
                unavailable=observation_status == "unavailable" or case["id"] not in observed_cases,
            ))

    all_cases = [case for engine in engines.values() for case in engine["cases"]]
    statuses = [case["status"] for case in all_cases]
    measured = [case for case in all_cases if case["status"] != "unknown"]
    mean_rr = sum(case.get("reciprocal_rank", 0.0) for case in measured) / len(measured) if measured else 0.0
    return {
        "schema_version": "carr-retrieval-scorecard-v1",
        "suite_id": suite["suite_id"],
        "suite_digest": canonical_digest(suite),
        "scope_ref": suite["scope_ref"],
        "engines": engines,
        "summary": {
            "total_engine_cases": len(all_cases),
            "passed_cases": statuses.count("pass"),
            "failed_cases": statuses.count("fail"),
            "unknown_cases": statuses.count("unknown"),
            "mean_reciprocal_rank": round(mean_rr, 6),
            "overall": "pass" if statuses and all(status == "pass" for status in statuses) else "not_pass",
        },
    }


def apply_regression_gate(report: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    if baseline.get("schema_version") != "carr-retrieval-baseline-v1":
        raise ContractError("unsupported retrieval baseline schema")
    if baseline.get("suite_digest") != report["suite_digest"]:
        raise ContractError("retrieval baseline suite digest mismatch")
    checks = []
    for engine, floor in baseline.get("engine_summary", {}).items():
        if engine not in report["engines"]:
            checks.append({"engine": engine, "status": "fail", "reason": "engine_missing"})
            continue
        cases = report["engines"][engine]["cases"]
        observed = {
            "passed": sum(case["status"] == "pass" for case in cases),
            "failed": sum(case["status"] == "fail" for case in cases),
            "unknown": sum(case["status"] == "unknown" for case in cases),
            "mean_reciprocal_rank": (
                sum(case.get("reciprocal_rank", 0.0) for case in cases) / len(cases)
                if cases else 0.0
            ),
        }
        by_case = {case["case_id"]: case["status"] for case in cases}
        case_floor = floor.get("case_status", {})
        case_safe = all(
            by_case.get(case_id) == "pass" if baseline_status == "pass"
            else by_case.get(case_id) in ("fail", "pass")
            for case_id, baseline_status in case_floor.items()
        ) and set(by_case) == set(case_floor)
        passed = (
            observed["passed"] >= int(floor["passed"])
            and observed["failed"] <= int(floor["failed"])
            and observed["unknown"] <= int(floor["unknown"])
            and observed["mean_reciprocal_rank"] + 1e-9 >= float(floor["mean_reciprocal_rank"])
            and case_safe
        )
        checks.append({"engine": engine, "status": "pass" if passed else "fail", "floor": floor, "observed": observed, "case_safe": case_safe})
    return {"status": "pass" if checks and all(check["status"] == "pass" for check in checks) else "fail", "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, type=pathlib.Path)
    parser.add_argument("--section-index", required=True, type=pathlib.Path)
    parser.add_argument("--doctrine-results", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--baseline", type=pathlib.Path)
    args = parser.parse_args()
    try:
        suite = load_json(args.suite)
        observation = load_json(args.doctrine_results)
        report = run(suite, section_index=args.section_index, doctrine_observation=observation)
        if args.baseline:
            report["regression_gate"] = apply_regression_gate(report, load_json(args.baseline))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], sort_keys=True))
        if args.baseline:
            return 0 if report["regression_gate"]["status"] == "pass" else 1
        return 0 if report["summary"]["overall"] == "pass" else 1
    except (OSError, json.JSONDecodeError, ContractError, ValueError) as exc:
        print(f"retrieval-eval: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
