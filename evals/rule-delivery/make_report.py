#!/usr/bin/env python3
"""Authenticate the preserved historical rule-delivery receipt and source blobs.

Report generation is retired; fresh final receipts use ops.eval_split.
"""
import hashlib
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

SOURCE_PATHS = (
    "lib/rule_routes.py", "ops/config/rule-routes.v1.json", "ops/rule-jit-compile.py",
    "ops/config/rule-jit-triggers.v1.json", "ops/fixtures/rule-delivery-eval/cases.v2.json",
    "ops/rule-pack-preuse-reselection-selftest.py", "ops/rule-trigger-compile-selftest.py",
    "evals/rule-delivery/README.md", "evals/rule-delivery/explain.py",
    "evals/rule-delivery/freeze_split.py", "evals/rule-delivery/hard_cases.v1.json",
    "evals/rule-delivery/noise.py", "evals/rule-delivery/split.json",
    "evals/rule-delivery/run_eval.py", "evals/rule-delivery/round.sh",
    "evals/rule-delivery/make_report.py", "evals/rule-delivery/selftest.py",
)


def receipt_source_matches(receipt=None):
    if receipt is None:
        with open(os.path.join(HERE, "historical-receipt.json"), encoding="utf-8") as handle:
            receipt = json.load(handle)
    commit = receipt.get("code_sha", "")
    if not isinstance(commit, str) or len(commit) != 40:
        return False
    if not isinstance(receipt.get("source_manifest"), dict) or set(receipt["source_manifest"]) != set(SOURCE_PATHS):
        return False
    # Historical evidence authenticates its original source, never the evolving
    # runtime. This verifier makes no claim about current shipping eligibility.
    # Every pinned blob below must still resolve and match that original SHA.
    for path, expected in receipt["source_manifest"].items():
        blob = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=REPO,
                              capture_output=True)
        if blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != expected:
            return False
    return True
