#!/usr/bin/env python3
"""Self-test the retrieval benchmark with deterministic synthetic metadata."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parent.parent
RUNNER = REPO / "ops" / "retrieval_eval.py"
HYBRID_RUNNER = REPO / "ops" / "retrieval_hybrid_eval.py"
GOLDEN = REPO / "evals" / "retrieval" / "golden-queries.v1.json"
BASELINE = REPO / "evals" / "retrieval" / "baselines" / "lexical.v1.json"
BASELINE_INDEX = REPO / "evals" / "retrieval" / "fixtures" / "section-index.baseline.v1.tsv"
DOCTRINE_BASELINE = REPO / "evals" / "retrieval" / "baselines" / "doctrine-fts.2026-08-15.v1.json"


def check(label: str, condition: bool) -> None:
    print(f"  {'ok' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    index = root / "section-index.tsv"
    index.write_text(
        "# synthetic\n"
        "doctrine:current-source\t1\t1\t1\tProtected trunk integration\tCARR Software\tExact current source\tstore\n"
        "Archive/dead.md\t1\t3\t1\tProtected trunk integration\tOld\tSuperseded copy\tfile\n"
        "doctrine:other\t1\t1\t1\tUnrelated retrieval\tCARR Software\tNothing relevant\tstore\n",
        encoding="utf-8",
    )
    suite = root / "suite.json"
    suite.write_text(json.dumps({
        "schema_version": "carr-retrieval-golden-v1",
        "suite_id": "synthetic-retrieval-selftest",
        "data_class": "D1_synthetic",
        "scope_ref": "carr-internal",
        "cases": [{
            "id": "RET-SELF-001",
            "query": "protected trunk integration",
            "engines": {
                "section_index_lexical": {
                    "required_targets": ["doctrine:current-source"],
                    "top_k": 1,
                },
                "doctrine_postgres_fts": {
                    "required_targets": ["current-source#protected-trunk"],
                    "top_k": 1,
                },
            },
            "forbidden_target_patterns": ["Archive/", "superseded"],
        }],
    }), encoding="utf-8")
    observed = root / "doctrine-results.json"
    observed.write_text(json.dumps({
        "schema_version": "carr-doctrine-fts-observation-v1",
        "suite_id": "synthetic-retrieval-selftest",
        "scope_ref": "carr-internal",
        "scope_applied_before_rank": True,
        "status": "measured",
        "cases": {"RET-SELF-001": [{
            "target": "current-source#protected-trunk",
            "rank": 1,
            "current": True,
            "provenance_complete": True,
        }]},
    }), encoding="utf-8")
    report = root / "report.json"
    proc = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--output", str(report),
    ], text=True, capture_output=True)
    check("a current, sourced result passes both lexical engines", proc.returncode == 0)
    payload = json.loads(report.read_text(encoding="utf-8"))
    check("report binds the suite digest", len(payload["suite_digest"]) == 64)
    check("report keeps engine results separate", set(payload["engines"]) == {"section_index_lexical", "doctrine_postgres_fts"})
    check("all measured current-source hits pass", payload["summary"]["passed_cases"] == 2)
    check("scope-before-rank evidence is explicit", payload["engines"]["doctrine_postgres_fts"]["scope_applied_before_rank"] is True)

    synthetic_baseline = root / "baseline.json"
    synthetic_baseline.write_text(json.dumps({
        "schema_version": "carr-retrieval-baseline-v1",
        "suite_digest": payload["suite_digest"],
        "engine_summary": {
            engine: {
                "passed": len(value["cases"]), "failed": 0, "unknown": 0,
                "mean_reciprocal_rank": 1.0,
                "case_status": {case["case_id"]: "pass" for case in value["cases"]},
            } for engine, value in payload["engines"].items()
        },
    }), encoding="utf-8")
    gated = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--baseline", str(synthetic_baseline),
        "--output", str(report),
    ], text=True, capture_output=True)
    check("an unchanged measured baseline passes the regression gate", gated.returncode == 0)

    bad = json.loads(observed.read_text(encoding="utf-8"))
    bad["cases"]["RET-SELF-001"][0]["current"] = False
    observed.write_text(json.dumps(bad), encoding="utf-8")
    bad_proc = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--output", str(report),
    ], text=True, capture_output=True)
    check("a stale expected result fails the scorecard", bad_proc.returncode == 1)

    unknown = dict(bad)
    unknown["status"] = "unavailable"
    unknown["cases"] = {}
    observed.write_text(json.dumps(unknown), encoding="utf-8")
    unknown_proc = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(suite), "--section-index", str(index),
        "--doctrine-results", str(observed), "--output", str(report),
    ], text=True, capture_output=True)
    unknown_payload = json.loads(report.read_text(encoding="utf-8"))
    check("an unavailable store is unknown, never a passing zero", unknown_proc.returncode == 1 and unknown_payload["summary"]["unknown_cases"] == 1)

golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
canonical = json.dumps(golden, sort_keys=True, separators=(",", ":")).encode()
import hashlib
check("the committed baseline is bound to the exact golden suite",
      baseline["suite_digest"] == hashlib.sha256(canonical).hexdigest())
check("the committed baseline records every engine-case outcome",
      baseline["summary"]["total_engine_cases"] == sum(len(case["engines"]) for case in golden["cases"]))
check("the baseline names measured misses instead of interpreting them as success",
      baseline["summary"]["failed"] == len(baseline["measured_misses"]) and baseline["summary"]["failed"] > 0)

with tempfile.TemporaryDirectory() as tmp:
    committed_report = pathlib.Path(tmp) / "committed-report.json"
    committed = subprocess.run([
        sys.executable, str(RUNNER), "--suite", str(GOLDEN),
        "--section-index", str(BASELINE_INDEX), "--doctrine-results", str(DOCTRINE_BASELINE),
        "--baseline", str(BASELINE), "--output", str(committed_report),
    ], text=True, capture_output=True)
    committed_payload = json.loads(committed_report.read_text(encoding="utf-8"))
    check("CI runs the production scorer against the committed golden floor", committed.returncode == 0)
    check("the reproducible floor remains 16 passes and 6 named misses",
          committed_payload["summary"]["passed_cases"] == 16
          and committed_payload["summary"]["failed_cases"] == 6
          and committed_payload["summary"]["unknown_cases"] == 0)

    hybrid_report = pathlib.Path(tmp) / "hybrid-report.json"
    hybrid = subprocess.run([
        sys.executable, str(HYBRID_RUNNER), "--suite", str(GOLDEN),
        "--section-index", str(BASELINE_INDEX), "--doctrine-results", str(DOCTRINE_BASELINE),
        "--section-index-scope-proven", "--output", str(hybrid_report),
    ], text=True, capture_output=True)
    hybrid_payload = json.loads(hybrid_report.read_text(encoding="utf-8"))
    check("the feature-gated hybrid keeps exact evidence for every golden case",
          hybrid.returncode == 0
          and hybrid_payload["summary"]["passed_cases"] == len(golden["cases"])
          and hybrid_payload["summary"]["failed_cases"] == 0
          and hybrid_payload["summary"]["unknown_cases"] == 0)

print("PASS: retrieval evaluation self-test")
