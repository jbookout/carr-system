#!/usr/bin/env python3
"""Hermetic checks for the replacement-staging app_reader pre-claim gate."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("program5_ops_record", ROOT / "tools" / "ops-record.py")
if spec is None or spec.loader is None:
    raise SystemExit("could not load ops-record controller")
record = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record)

ROWS = [
    ("0001_bootstrap.sql", "1" * 64),
    ("0315_program5_forward_fix_rehearsal.sql", "5" * 64),
    ("0315a_program5_bounded_forward_fix_rehearsal.sql", "a" * 64),
]
COUNT, HIGHEST, LEDGER = record._ledger_identity(ROWS)
CONTRACT = {
    "target_prefix": {
        "applied_count": COUNT,
        "highest_migration": HIGHEST,
        "ledger_sha256": LEDGER,
    },
    "held_back_migrations": [
        {"ordinal": COUNT + 1, "filename": "0316_rule_delivery_audit_counts.sql", "sha256": "6" * 64},
        {"ordinal": COUNT + 2, "filename": "0317_atomic_rule_delivery_cutover.sql", "sha256": "7" * 64},
    ],
}


class Cursor:
    def __init__(self, rows, identity):
        self.rows = rows
        self.identity = identity
        self.statement = ""
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement):
        self.statement = statement
        self.statements.append(statement)

    def fetchone(self):
        return self.identity

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, rows, identity):
        self.cursor_value = Cursor(rows, identity)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.cursor_value


def fake_connect(rows=ROWS, identity=("app_reader", "app_reader", "neondb", 5432)):
    observed = []
    opened = []

    def connect(dsn):
        observed.append(dsn)
        connection = Connection(rows, identity)
        opened.append(connection)
        return connection

    return observed, opened, connect


def refuses(rows=ROWS, identity=("app_reader", "app_reader", "neondb", 5432)):
    observed, _opened, connect = fake_connect(rows, identity)
    try:
        record.replacement_staging_bounded_prefix_readback(
            CONTRACT, dsn_factory=lambda: "postgresql://app_reader:not-printed@staging/neondb?sslmode=require",  # ci-secret-scan: allow - synthetic fixture
            connect_factory=connect)
    except ValueError:
        assert len(observed) == 1
        return
    raise AssertionError("expected app_reader staging prefix refusal")


seen, opened, connect = fake_connect()
result = record.replacement_staging_bounded_prefix_readback(
    CONTRACT, dsn_factory=lambda: "postgresql://app_reader:not-printed@staging/neondb?sslmode=require",  # ci-secret-scan: allow - synthetic fixture
    connect_factory=connect)
assert result == {
    "session_user": "app_reader", "database": "neondb", "schema_applied_count": COUNT,
    "schema_highest_migration": HIGHEST, "schema_ledger_sha256": LEDGER, "held_back_absent": True,
}
assert seen == ["postgresql://app_reader:not-printed@staging/neondb?sslmode=require"]  # ci-secret-scan: allow - synthetic fixture
queries = opened[0].cursor_value.statements
assert any("public.v_schema_ledger" in query for query in queries)
assert not any("public.schema_migrations" in query for query in queries)

# A valid source-like row after the prefix and a held-back row both fail closed.
refuses(ROWS + [("0315b_unexpected.sql", "b" * 64)])
refuses(ROWS + [("0316_rule_delivery_audit_counts.sql", "6" * 64)])
refuses(ROWS + [("0315_program5_forward_fix_rehearsal.sql", "5" * 64)])
refuses(ROWS, ("carr_jobs", "carr_jobs", "neondb", 5432))

# Driver messages may contain DSNs. The command's generic catch must report
# only a static refusal plus the normalized exception class.
previous_contract = record.bounded_forward_fix_contract
previous_readback = record.replacement_staging_bounded_prefix_readback
record.bounded_forward_fix_contract = lambda _path, _sha: CONTRACT
record.replacement_staging_bounded_prefix_readback = lambda _contract: (_ for _ in ()).throw(
    RuntimeError("postgresql://user:secret@host.invalid/db"))  # ci-secret-scan: allow - synthetic fixture
captured = io.StringIO()
try:
    with contextlib.redirect_stderr(captured):
        rc = record.cmd_staging_forward_fix_prefix_read(types.SimpleNamespace(
            bounded_contract="unused", git_sha="a" * 40, field=None))
finally:
    record.bounded_forward_fix_contract = previous_contract
    record.replacement_staging_bounded_prefix_readback = previous_readback
assert rc == 1
assert "RuntimeError" in captured.getvalue()
assert "secret" not in captured.getvalue() and "postgresql://" not in captured.getvalue()

# The wrapper must call this physical-target gate before prepare/claim/Wrangler.
wrapper = (ROOT / "bin" / "deploy-worker.sh").read_text(encoding="utf-8")
gate = wrapper.index("staging-forward-fix-prefix-read")
prepare = wrapper.index('DEPLOY_TAG="$(staging_attempt prepare')
claim = wrapper.index('DEPLOY_ALLOWED="$(staging_attempt claim')
wrangler = wrapper.index('"$WRANGLER" deploy --env "$TARGET_ENV"')
assert gate < prepare < claim < wrangler
assert 'if [ "$RECOVERY_STEP" = "forward_fix" ] && [ -n "$BOUNDED_FORWARD_FIX_CONTRACT" ]; then' in wrapper
assert '--manifest "$RELEASE_MANIFEST"' in wrapper

controller_source = (ROOT / "tools" / "ops-record.py").read_text(encoding="utf-8")
assert "ops.record_staging_bounded_forward_fix_rehearsal" in controller_source
assert "ops.record_staging_forward_fix_rehearsal" in controller_source

migration = (ROOT / "migrations" / "0315a_program5_bounded_forward_fix_rehearsal.sql").read_text(encoding="utf-8")
assert "create or replace function ops.record_staging_bounded_forward_fix_rehearsal(" in migration
assert "create or replace function ops.record_staging_forward_fix_rehearsal(" not in migration
assert "b.bounded_forward_fix_contract_id is null" in migration
assert "exists(select 1 from ops.staging_forward_fix_rehearsal_result x where x.id=b.forward_fix_result_id)" in migration

print("program5 bounded forward-fix controller selftest: replacement staging app_reader proof precedes claim")
