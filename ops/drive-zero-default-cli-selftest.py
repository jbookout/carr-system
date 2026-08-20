#!/usr/bin/env python3
# ci: selftest
"""Focused contract tests for normal canonical mode and explicit Drive recovery."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.drive_recovery import RecoveryArgumentError, parse_recovery_controls


def refused(argv: list[str], needle: str) -> None:
    try:
        parse_recovery_controls(argv, "test seam")
    except RecoveryArgumentError as exc:
        assert needle in str(exc), (argv, exc)
    else:
        raise AssertionError(f"accepted malformed recovery controls: {argv}")


os.environ["CARR_VAULT"] = "/poison/ambient-drive"
normal = parse_recovery_controls(["--county", "Escambia", "--count", "3"], "test seam")
assert normal.args == ("--county", "Escambia", "--count", "3")
assert normal.vault is None and not normal.recovery
assert "CARR_VAULT" not in os.environ
assert os.environ["CARR_SOURCE_MODE"] == "records"

recovery = parse_recovery_controls(
    ["--count", "3", "--recovery", "--reason", "database outage", "--vault", "/tmp/vault", "--county", "Bay"],
    "test seam",
)
assert recovery.recovery and recovery.reason == "database outage"
assert recovery.vault == Path("/tmp/vault")
assert recovery.args == ("--count", "3", "--county", "Bay")
assert os.environ["CARR_SOURCE_MODE"] == "files"

for argv, needle in (
    (["--files"], "not caller-selectable"),
    (["--records"], "not caller-selectable"),
    (["--recovery"], "requires"),
    (["--reason", "why"], "recovery-only"),
    (["--vault", "/tmp/v"], "recovery-only"),
    (["--recovery", "--reason=why"], "does not accept ="),
    (["--recovery", "--reason", "-bad"], "non-option-looking"),
    (["--recovery", "--recovery", "--reason", "why"], "duplicate"),
):
    refused(argv, needle)

run_text = (REPO / "run.sh").read_text()
assert "VAULT=" not in run_text and "$VAULT" not in run_text
for command in ("deal-room", "lead-board", "lead-promote", "renewal-feed", "corroborate", "graph", "graph-system", "graph-health", "section-index"):
    assert command in run_text

env = os.environ.copy()
env["CARR_VAULT"] = "/poison/ambient-drive"
for script, name in (
    ("generators/build-renewal-feed.py", "canonical external MLS ingress"),
    ("pipelines/radar/corroborate.py", "canonical upstream signal ingress"),
):
    proc = subprocess.run([sys.executable, str(REPO / script)], text=True, capture_output=True, env=env)
    assert proc.returncode != 0 and name in (proc.stdout + proc.stderr), (script, proc.stdout, proc.stderr)
    assert "/poison/ambient-drive" not in (proc.stdout + proc.stderr)

print("drive-zero-default CLI selftest passed")
