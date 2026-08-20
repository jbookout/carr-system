#!/usr/bin/env python3
"""Hermetic adversarial coverage for the schema snapshot local DSN parser."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "ops" / "schema-snapshot-dsn.py"
SPEC = importlib.util.spec_from_file_location("schema_snapshot_dsn", VALIDATOR)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)

accepted = (
    "postgres://127.0.0.1:55434/carr_ci",
    "postgresql://localhost:5432/carr_ci",
)
rejected = (
    "https://127.0.0.1:55434/carr_ci",
    "postgres://evil.example/@localhost:55434/carr_ci",
    "postgres://evil.example/carr_ci?x=@localhost:55434",
    "postgres://user:password@127.0.0.1:55434/carr_ci",
    "postgres://user@localhost:55434/carr_ci?sslmode=disable",
    "postgres://[::1]:55434/carr_ci",
    "postgres://user@[::1]:55434/carr_ci",
    "postgres://127.0.0.1:55434/carr_ci?host=/tmp",
    "postgres://127.0.0.1:55434/carr_ci#@localhost",
    "postgres://127.0.0.1:55434/%63arr_ci",
    "postgres://%31%32%37.0.0.1:55434/carr_ci",
    "postgres://127.0.0.1:055434/carr_ci",
    "POSTGRES://127.0.0.1:55434/carr_ci",
    "postgres://LOCALHOST:55434/carr_ci",
    "postgres://127.0.0.1:55434//carr_ci",
    "postgres://127.0.0.1/carr_ci",
    "postgres://127.0.0.1:0/carr_ci",
    "postgres://127.0.0.1:65536/carr_ci",
    "postgres://127.0.0.1:55434/carr-ci",
    "postgres://127.0.0.1:55434/carr_ci/extra",
    "postgres://127.0.0.1:55434/carr_ci\\@evil.example",
)

assert all(validator.is_allowed_local_dsn(value) for value in accepted)
assert not any(validator.is_allowed_local_dsn(value) for value in rejected)

for value, expected in ((*[(item, 0) for item in accepted],
                         *[(item, 1) for item in rejected])):
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        env={**os.environ, "CARR_SCHEMA_SNAPSHOT_DSN": value},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected, (value, result.returncode)
    assert result.stdout == "" and result.stderr == "", value

print(f"schema snapshot DSN selftest: {len(accepted)} accepted / {len(rejected)} rejected")
