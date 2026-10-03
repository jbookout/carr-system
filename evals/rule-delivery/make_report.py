#!/usr/bin/env python3
"""Authenticate the preserved historical rule-delivery receipt and source blobs.

Report generation is retired; fresh final receipts use ops.eval_split.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

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
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    if not isinstance(receipt.get("source_manifest"), dict) or set(receipt["source_manifest"]) != set(SOURCE_PATHS):
        return False
    # Historical evidence authenticates its original source, never the evolving
    # runtime. This verifier makes no claim about current shipping eligibility.
    # The archive contains the original commit, path trees and pinned blobs.
    # Resolve only those objects in an isolated database: neither local history
    # nor a network fetch supplies missing evidence in a clean checkout.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        with tempfile.TemporaryDirectory() as objects:
            def git(*args, **kwargs):
                return subprocess.run(["git", "--git-dir=" + objects, *args],
                                      env=env, capture_output=True, timeout=10, **kwargs)
            if git("init", "--bare").returncode:
                return False
            with open(os.path.join(HERE, "historical-source.pack"), "rb") as archive:
                if git("unpack-objects", stdin=archive).returncode:
                    return False
            for path, expected in receipt["source_manifest"].items():
                blob = git("show", f"{commit}:{path}")
                if blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != expected:
                    return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True
