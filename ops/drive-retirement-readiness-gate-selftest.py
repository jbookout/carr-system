#!/usr/bin/env python3
"""Hermetic refusal coverage for the Drive retirement preflight."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "ops" / "drive-retirement-readiness-gate.py"
PYTHON = sys.executable

passed = 0
with tempfile.TemporaryDirectory(dir=ROOT) as temp:
    root = Path(temp)
    (root / "ops/config").mkdir(parents=True)
    (root / "ops/config/drive-dependencies.schema.v1.json").write_text(
        (ROOT / "ops/config/drive-dependencies.schema.v1.json").read_text())
    (root / "bin").mkdir()
    (root / "bin/run.sh").write_text('x="$CARR_VAULT/a"\n')
    registry = {
        "schema_version": 1, "contract": "drive-dependencies", "binary_exclusions": [], "entries": [{
            "id": "fixture", "class": "normal_runtime", "sources": ["bin/run.sh"],
            "path_pattern": "{{VAULT}}/a", "producer": "fixture", "consumers": ["fixture"],
            "canonicality": "canonical", "replacement": {"status": "unrepointed"}}]}
    path = root / "ops/config/drive-dependencies.v1.json"
    path.write_text(json.dumps(registry))
    normal = subprocess.run([PYTHON, str(GATE), "--root", str(root)], capture_output=True, text=True)
    assert normal.returncode == 0 and "not accepted for retirement" in normal.stdout, (normal.returncode, normal.stdout, normal.stderr)
    passed += 1
    exit_check = subprocess.run([PYTHON, str(GATE), "--root", str(root), "--phase4-exit"], capture_output=True, text=True)
    assert exit_check.returncode == 2 and "immutable repoint receipts" in exit_check.stderr
    passed += 1
    (root / "bin/unregistered.sh").write_text('x="$CARR_VAULT/b"\n')
    incomplete = subprocess.run([PYTHON, str(GATE), "--root", str(root)], capture_output=True, text=True)
    assert incomplete.returncode == 2 and "inventory incomplete" in incomplete.stderr
    passed += 1

print(f"drive retirement readiness gate selftest: {passed}/3 passed")
