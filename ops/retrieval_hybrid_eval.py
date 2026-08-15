#!/usr/bin/env python3
"""Read-only scorecard for the feature-gated hybrid retrieval candidate."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ops"))
sys.path.insert(0, str(REPO / "tools"))
from retrieval_eval import (  # noqa: E402
    ContractError, OBSERVATION_SCHEMA, canonical_digest, load_json, validate_suite,
)
from retrieval_hybrid import fuse_candidates, normalize_document_identity  # noqa: E402
from retrieval_lexical import rank_index_sections, target_for_row  # noqa: E402


SCORECARD_SCHEMA = "carr-retrieval-hybrid-scorecard-v1"


def _expected(case: dict[str, Any]) -> tuple[set[str], set[str], int]:
    expectations = case["engines"]
    documents = {
        normalize_document_identity(target)
        for expectation in expectations.values()
        for target in expectation["required_targets"]
    }
    # The doctrine expectation is the evidence contract.  A section-index hit
    # may satisfy it now that its store rows retain section_key.
    evidence = set(expectations["doctrine_postgres_fts"]["required_targets"])
    top_k = max(expectation["top_k"] for expectation in expectations.values())
    return documents, evidence, top_k


def _fts_candidates(observation: dict[str, Any], case_id: str, scope_ref: str) -> list[dict[str, Any]] | None:
    if observation["status"] == "unavailable" or case_id not in observation["cases"]:
        return None
    return [{**hit, "scope_ref": scope_ref} for hit in observation["cases"][case_id]]


def run(
    suite: dict[str, Any], *, section_index: pathlib.Path, doctrine_observation: dict[str, Any],
    section_scope_applied_before_rank: bool,
) -> dict[str, Any]:
    validate_suite(suite)
    if doctrine_observation.get("schema_version") != OBSERVATION_SCHEMA:
        raise ContractError("unsupported doctrine observation schema")
    if doctrine_observation.get("suite_id") != suite["suite_id"]:
        raise ContractError("doctrine observation suite_id mismatch")
    if doctrine_observation.get("scope_ref") != suite["scope_ref"]:
        raise ContractError("doctrine observation scope mismatch")
    if doctrine_observation.get("status") not in ("measured", "unavailable"):
        raise ContractError("doctrine observation status must be measured or unavailable")
    if not isinstance(doctrine_observation.get("cases"), dict):
        raise ContractError("doctrine observation cases must be an object")

    cases: list[dict[str, Any]] = []
    fts_scope_proven = doctrine_observation.get("scope_applied_before_rank") is True
    for case in suite["cases"]:
        expected_documents, expected_evidence, top_k = _expected(case)
        ranked_sections = rank_index_sections(section_index, case["query"], top=top_k)
        section_hits = [{
            "target": target_for_row(item.row),
            "rank": rank,
            "scope_ref": suite["scope_ref"],
            "current": True,
            "provenance_complete": item.row.source in ("file", "store") and bool(item.row.path),
        } for rank, item in enumerate(ranked_sections, 1)]
        fts_hits = _fts_candidates(doctrine_observation, case["id"], suite["scope_ref"])
        fused = fuse_candidates(
            section_hits=section_hits,
            fts_hits=fts_hits,
            scope_ref=suite["scope_ref"],
            section_scope_applied_before_rank=section_scope_applied_before_rank,
            fts_scope_applied_before_rank=fts_scope_proven,
            forbidden_target_patterns=case.get("forbidden_target_patterns", []),
            top_k=top_k,
        )
        selected = fused["hits"][:top_k]
        exact = [hit for hit in selected if hit["target"] in expected_evidence]
        document_ok = any(hit["document"] in expected_documents for hit in selected)
        if fused["status"] == "unknown":
            status = "unknown"
        elif fused["status"] == "no_answer":
            status = "no_answer"
        elif exact and document_ok:
            status = "pass"
        else:
            status = "fail"
        cases.append({
            "case_id": case["id"],
            "status": status,
            "required_documents": sorted(expected_documents),
            "required_evidence_targets": sorted(expected_evidence),
            "observed_targets": [hit["target"] for hit in selected],
            "best_required_rank": min((hit["rank"] for hit in exact), default=None),
            "fallback_used": fused["fallback_used"],
            "scope_applied_before_rank": fused["scope_applied_before_rank"],
            "generators": {
                "section_index": {
                    "available": section_scope_applied_before_rank,
                    "scope_applied_before_rank": section_scope_applied_before_rank,
                },
                "doctrine_fts": {
                    "available": fts_hits is not None and fts_scope_proven,
                    "scope_applied_before_rank": fts_scope_proven,
                },
            },
        })
    statuses = [case["status"] for case in cases]
    return {
        "schema_version": SCORECARD_SCHEMA,
        "suite_id": suite["suite_id"],
        "suite_digest": canonical_digest(suite),
        "scope_ref": suite["scope_ref"],
        "feature_gate": "hybrid_retrieval_candidate",
        "cases": cases,
        "summary": {
            "total_cases": len(cases),
            "passed_cases": statuses.count("pass"),
            "failed_cases": statuses.count("fail"),
            "unknown_cases": statuses.count("unknown"),
            "no_answer_cases": statuses.count("no_answer"),
            "overall": "pass" if statuses and all(status == "pass" for status in statuses) else "not_pass",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, type=pathlib.Path)
    parser.add_argument("--section-index", required=True, type=pathlib.Path)
    parser.add_argument("--doctrine-results", required=True, type=pathlib.Path)
    parser.add_argument("--section-index-scope-proven", action="store_true")
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        report = run(
            load_json(args.suite), section_index=args.section_index,
            doctrine_observation=load_json(args.doctrine_results),
            section_scope_applied_before_rank=args.section_index_scope_proven,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], sort_keys=True))
        return 0 if report["summary"]["overall"] == "pass" else 1
    except (OSError, ValueError, ContractError, json.JSONDecodeError) as exc:
        print(f"retrieval-hybrid-eval: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
