#!/usr/bin/env python3
"""Hermetic diagnostics checks for the fresh Program 5 prefix gate."""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
gate_text = (ROOT / "ops" / "program5-clean-prefix-compatibility-gate.py").read_text()
ci_text = (ROOT / "ops" / "ci.sh").read_text()
tree = ast.parse(gate_text)
wanted = {"URI_RE", "ERROR_LINE_RE", "ROUTINE_STDOUT_PREFIXES", "migration_failure_detail"}
nodes = [node for node in tree.body if (
    isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in wanted
                                         for target in node.targets)
) or (isinstance(node, ast.FunctionDef) and node.name in wanted)]
namespace = {"re": re}
exec(compile(ast.Module(body=nodes, type_ignores=[]), "diagnostic-fragment", "exec"), namespace)

detail = namespace["migration_failure_detail"](
    "Traceback (most recent call last):\n  File '/tmp/x', line 1\n"
    "RuntimeError: migration 0014 failed against postgresql://user:secret@host/db\n",  # ci-secret-scan: allow - synthetic fixture
    "host: 127.0.0.1\npending: 0307_calendar_prebrief_failure_receipt.sql\n")
assert detail.startswith("RuntimeError: migration 0014 failed")
assert "Traceback" not in detail and "secret" not in detail and "postgresql://[redacted]" in detail

detail = namespace["migration_failure_detail"]("", "migration runner returned nonzero\n")
assert detail == "migration runner returned nonzero"
detail = namespace["migration_failure_detail"]("", "pending: 0307_failure.sql\napplying: 0308_x.sql\n")
assert detail == "migration runner returned nonzero"

assert gate_text.startswith("# ci: db-prefix-gate\n")
assert 'if db_name != "carr_ci"' in gate_text
assert 'if os.environ.get("CARR_CI_DATABASE_URL") != source_dsn' in gate_text
assert "reset_required = True" in gate_text
assert "finally:\n        if reset_required:\n            try:\n                reset_disposable_target(admin_dsn, db_name)" in gate_text
assert '"PGDATABASE": info["dbname"]' in gate_text
assert 'psql_env[env_key] = info[source_key]' in gate_text
assert "process.env.DATABASE_URL" not in gate_text
assert '"-f", str(REPO / "db" / "schema.sql")' in gate_text
assert "canonical snapshot is not the exact Production 0312/248 boundary" in gate_text

prefix_discovery = ci_text.index("if grep -q '^# ci: db-prefix-gate' \"$g\"; then")
snapshot_load = ci_text.index('"$psql_bin" -v ON_ERROR_STOP=1 -q -d "$dsn" -f db/schema.sql')
assert prefix_discovery < snapshot_load
post_gate_skip = ci_text.index("elif grep -q '^# ci: db-prefix-gate' \"$g\"; then", snapshot_load)
assert post_gate_skip > snapshot_load
assert 'CARR_CI_DATABASE_URL="$dsn" DATABASE_URL="$dsn"' in ci_text[prefix_discovery:snapshot_load]
print("program5 clean-prefix compatibility selftest: diagnostics redacted; empty-target CI ordering enforced")
