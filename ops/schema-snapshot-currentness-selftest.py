#!/usr/bin/env python3
"""Exact boundary tests for schema-snapshot production-currentness comparison."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = ROOT / "ops" / "schema-snapshot-currentness.py"
GENERATOR = ROOT / "bin" / "schema-snapshot.sh"
EXACT_77 = "select pg_catalog.setval('ops.work_request_ref_seq', 77, true);"
EXACT_78 = "select pg_catalog.setval('ops.work_request_ref_seq', 78, true);"


def compare(left: str, right: str) -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        expected = root / "expected.sql"
        observed = root / "observed.sql"
        expected.write_text(left, encoding="utf-8")
        observed.write_text(right, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(COMPARATOR), str(expected), str(observed)],
            check=False,
            capture_output=True,
            text=True,
        ).returncode


def snapshot(line: str = EXACT_77, suffix: str = "") -> str:
    return f"CREATE SCHEMA ops;\n{line}\nCOMMIT;\n{suffix}"


assert compare(snapshot(), snapshot(EXACT_78)) == 0, (
    "only the exact work-request sequence's numeric current value may drift"
)
assert compare(snapshot(), snapshot()) == 0

refusals = (
    (snapshot(), snapshot(EXACT_78, "-- unrelated drift\n")),
    (snapshot(), snapshot("select pg_catalog.setval('ops.other_seq', 78, true);")),
    (snapshot(), snapshot("select pg_catalog.setval('ops.work_request_ref_seq', 78, false);")),
    (snapshot(), snapshot("SELECT pg_catalog.setval('ops.work_request_ref_seq', 78, true);")),
    (snapshot(), snapshot("select pg_catalog.setval('ops.work_request_ref_seq',78,true);")),
    (snapshot(), "CREATE SCHEMA ops;\nCOMMIT;\n"),
    ("CREATE SCHEMA ops;\nCOMMIT;\n", "CREATE SCHEMA ops;\nCOMMIT;\n"),
    (snapshot() + EXACT_77 + "\n", snapshot() + EXACT_78 + "\n"),
    (
        snapshot() + "SELECT pg_catalog.setval('ops.work_request_ref_seq', 90, true);\n",
        snapshot(EXACT_78) + "SELECT pg_catalog.setval('ops.work_request_ref_seq', 90, true);\n",
    ),
    (
        snapshot() + "select  pg_catalog.setval( 'ops.work_request_ref_seq' , 90 , true );\n",
        snapshot(EXACT_78) + "select  pg_catalog.setval( 'ops.work_request_ref_seq' , 90 , true );\n",
    ),
)
for expected, observed in refusals:
    assert compare(expected, observed) == 1, (expected, observed)

generator = GENERATOR.read_text(encoding="utf-8")
assert 'CURRENTNESS_PY="$REPO/ops/schema-snapshot-currentness.py"' in generator
assert '"$CATALOG_PY" "$CURRENTNESS_PY" "$OUT" "$TMP"' in generator

print("schema snapshot currentness selftest: exact volatile-value boundary pinned")
