#!/usr/bin/env python3
"""Hermetic coverage for schema snapshot's non-argv libpq connection path."""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ops" / "schema-snapshot-connection.py"
SNAPSHOT = ROOT / "bin" / "schema-snapshot.sh"
SPEC = importlib.util.spec_from_file_location("schema_snapshot_connection", HELPER)
assert SPEC and SPEC.loader
connection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connection)

DSN = "postgresql://snapshot_user:fixture-password@db.example.test:5432/carr?sslmode=require&channel_binding=require"  # ci-secret-scan: allow — synthetic parser fixture

lines = connection.service_lines(DSN)
assert lines[0] == "[schema_snapshot]"
assert "host='db.example.test'" in lines
assert "sslmode='require'" in lines
assert "channel_binding='require'" in lines
assert all("fixture-password" not in line for line in lines if line.startswith(("host=", "port=", "dbname=", "user=")))

for malformed in ("", "https://db.example.test/carr", "postgresql://db.example.test/a/b", "postgresql://u:p@db.example.test/carr?host=elsewhere", "postgresql://u:p@db.example.test/carr?service=other"):
    try:
        connection.service_lines(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError(malformed)

with tempfile.TemporaryDirectory() as directory:
    service_file = Path(directory) / "service.conf"
    service_file.touch()
    result = subprocess.run(
        [sys.executable, str(HELPER), "--write-service", str(service_file)],
        input=DSN,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ},
    )
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    assert stat.S_IMODE(service_file.stat().st_mode) == 0o600
    assert "fixture-password" in service_file.read_text(encoding="utf-8")

source = SNAPSHOT.read_text(encoding="utf-8")
assert "unset URL" in source
assert "PGSERVICE=schema_snapshot" in source
assert "$URL" not in source.split("unset URL", 1)[1]
assert '"$PG_DUMP" --schema-only --no-owner --no-acl "$URL"' not in source
assert '"$PSQL" "$URL"' not in source

print("schema snapshot connection selftest: private service transport verified")
