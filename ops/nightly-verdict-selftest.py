#!/usr/bin/env python3
"""Hermetic tests for the nightly chain completion-banner classifier.

The Healthchecks ping emits ``whole chain OK`` before the chain's own
completion banner.  A loose ``chain OK`` search therefore closes a run early
and drops its failures/blocked steps.  These cases pin the complete accepted
banner and the near-matches that must remain ordinary log lines.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from health_submodule import nightly_completion  # noqa: E402


CASES = {
    "exact success banner":
        ("===== nightly chain OK =====", True),
    "surrounding whitespace is ignored":
        ("  \t===== nightly chain OK =====  \n", True),
    "timestamped success banner is not a verdict":
        ("2026-08-25T07:00:00Z  ===== nightly chain OK =====", None),
    "prefixed success banner is not a verdict":
        ("prefix ===== nightly chain OK =====", None),
    "suffixed success banner is not a verdict":
        ("===== nightly chain OK ===== suffix", None),
    "embedded success banner is not a verdict":
        ("before ===== nightly chain OK ===== after", None),
    "healthchecks whole-chain ping is not a verdict":
        ("hc-ping: whole chain OK -> pinged", None),
    "short success substring is not a verdict":
        ("chain OK", None),
    "near-match missing one equals sign is not a verdict":
        ("===== nightly chain OK ====", None),
    "near-match with altered wording is not a verdict":
        ("===== nightly chain SUCCESS =====", None),
    "failed-chain banner is a failed verdict":
        ("2026-08-25T07:01:00Z  ===== nightly chain FINISHED WITH FAILURES =====", False),
    "failed-chain text containing chain OK is still failed":
        ("FINISHED WITH FAILURES after chain OK text", False),
    "ordinary step text is not a verdict":
        ("  FAIL  export receipts (exit 1)", None),
}


passed = 0
for name, (line, expected) in CASES.items():
    actual = nightly_completion(line)
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r} for {line!r}")
    passed += 1
    print(f"ok {passed:02d} - {name}")

source = (ROOT / "tools" / "health-check.py").read_text(encoding="utf-8")
if "_health_sub.nightly_completion(_ln)" not in source:
    raise AssertionError("health-check parser is not wired to the tested classifier")
passed += 1
print(f"ok {passed:02d} - health-check uses the tested classifier")
if 'elif "chain OK" in _ln' in source:
    raise AssertionError("health-check parser regressed to loose chain OK matching")
passed += 1
print(f"ok {passed:02d} - health-check has no loose chain OK match")

print(f"nightly-verdict-selftest: {passed}/{len(CASES) + 2} passed")
