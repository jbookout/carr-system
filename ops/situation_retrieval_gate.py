#!/usr/bin/env python3
"""Release-blocking scorecard for deterministic situation retrieval."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


class ContractError(ValueError):
    pass


def load(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"{path}: root must be an object")
    return value


def complete_provenance(hit: dict[str, Any]) -> bool:
    provenance = hit.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("complete") is not True:
        return False
    required = ("policy_id", "lexical_score", "concept_score", "final_score")
    if any(key not in provenance for key in required):
        return False
    if float(provenance.get("concept_score", 0)) > 0:
        return all(isinstance(provenance.get(key), list) and provenance[key]
                   for key in ("phrase_ids", "concept_ids", "mapping_ids"))
    return True


def grade(suite: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    if suite.get("schema_version") != "carr-situation-retrieval-suite-v1":
        raise ContractError("unsupported situation retrieval suite")
    if observation.get("schema_version") != "carr-situation-retrieval-observation-v1":
        raise ContractError("unsupported situation retrieval observation")
    for key in ("suite_id", "scope_ref"):
        if observation.get(key) != suite.get(key):
            raise ContractError(f"{key} mismatch")
    if observation.get("status") not in ("measured", "candidate_synthetic"):
        raise ContractError("observation status must be measured or candidate_synthetic")
    scope_ok = observation.get("scope_applied_before_rank") is True
    replay_ok = observation.get("deterministic_replay_mismatches") == 0
    policy_id = observation.get("policy_id")
    if policy_id not in ("lexical-dominant-v1", "coequal-normalized-v1"):
        raise ContractError("observation policy_id must name a shipped ranking policy")
    observed = observation.get("cases")
    if not isinstance(observed, dict):
        raise ContractError("observation cases must be an object")
    graded = []
    for case in suite.get("cases", []):
        hits = observed.get(case["id"], [])
        if not isinstance(hits, list):
            raise ContractError(f"{case['id']}: hits must be an array")
        ordered = sorted(hits, key=lambda hit: int(hit.get("rank", 10**9)))
        top = ordered[:int(case.get("top_k", 3))]
        top_targets = [hit.get("target") for hit in top]
        required = case.get("required_targets", [])
        target_ok = (all(target in top_targets for target in required)
                     if case.get("require_all_targets") else
                     (not required or any(target in top_targets for target in required)))
        forbidden = set(case.get("expect_no_targets", []))
        negative_ok = not any(hit.get("target") in forbidden for hit in ordered)
        if case.get("expect_no_hits") is True:
            target_ok = not ordered
        current_ok = all(hit.get("current") is True for hit in ordered)
        provenance_ok = all(
            complete_provenance(hit) and hit["provenance"].get("policy_id") == policy_id
            for hit in ordered
        )
        passed = scope_ok and replay_ok and target_ok and negative_ok and current_ok and provenance_ok
        graded.append({
            "case_id": case["id"], "status": "pass" if passed else "fail",
            "required_in_top_k": target_ok, "negative_clean": negative_ok,
            "stale_leakage": not current_ok, "provenance_complete": provenance_ok,
            "observed_targets": top_targets,
        })
    return {
        "schema_version": "carr-situation-retrieval-scorecard-v1",
        "suite_id": suite["suite_id"], "policy_id": policy_id,
        "observation_status": observation["status"],
        "scope_applied_before_rank": scope_ok,
        "deterministic_replay_mismatches": observation.get("deterministic_replay_mismatches"),
        "cases": graded,
        "diagnostics": observation.get("diagnostics", {}),
        "summary": {
            "passed": sum(row["status"] == "pass" for row in graded),
            "failed": sum(row["status"] == "fail" for row in graded),
            "overall": "pass" if graded and all(row["status"] == "pass" for row in graded) else "fail",
        },
    }


def choose_winner(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose from recorded scorecards only; ties are policy-id deterministic."""
    if len(reports) != 2:
        raise ContractError("exactly two policy observations are required")
    policy_ids = [report.get("policy_id") for report in reports]
    if set(policy_ids) != {"lexical-dominant-v1", "coequal-normalized-v1"}:
        raise ContractError("observations must cover both shipped ranking policies exactly once")
    eligible = [report for report in reports if report["summary"]["overall"] == "pass"]
    if not eligible:
        return {"schema_version": "carr-situation-retrieval-policy-selection-v1",
                "status": "fail", "reason": "no_policy_passes_release_gate", "scorecards": reports}
    # Diagnostics never override release gates.  They only provide a stable
    # comparator after the release contract has admitted a candidate.
    def key(report: dict[str, Any]) -> tuple[float, str]:
        diagnostics = report.get("diagnostics", {})
        return (float(diagnostics.get("mean_reciprocal_rank", 0.0)), str(report["policy_id"]))
    # max with an inverted lexical policy-id tie breaker would be opaque; sort
    # makes the dated artifact's deterministic tie rule inspectable.
    winner = sorted(eligible, key=lambda report: (-key(report)[0], key(report)[1]))[0]
    return {
        "schema_version": "carr-situation-retrieval-policy-selection-v1",
        "status": ("candidate" if any(report["observation_status"] == "candidate_synthetic" for report in reports)
                   else "pass") if len(eligible) == len(reports) else "fail",
        "default_policy_id": winner["policy_id"],
        "tie_breaker": "highest diagnostic mean_reciprocal_rank, then policy_id ascending",
        "scorecards": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=pathlib.Path, required=True)
    parser.add_argument("--observation", type=pathlib.Path, action="append")
    parser.add_argument("--observations", type=pathlib.Path, nargs=2)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        if bool(args.observation) == bool(args.observations):
            raise ContractError("provide --observation once, or --observations with both policy files")
        suite = load(args.suite)
        observations = args.observations or args.observation
        reports = [grade(suite, load(path)) for path in observations]
        report = reports[0] if len(reports) == 1 else choose_winner(reports)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary = report.get("summary") or {"overall": report.get("status")}
        print(json.dumps(summary, sort_keys=True))
        return 0 if (summary.get("overall") == "pass" or report.get("status") == "candidate") else 1
    except (OSError, json.JSONDecodeError, ContractError, ValueError) as exc:
        print(f"situation-retrieval-gate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
