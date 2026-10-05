#!/usr/bin/env python3
"""The reconstructed CI database must exercise the grant boundary, never skip it."""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('capture_probe', Path(__file__).with_name('capture-verb-reachability.py'))
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
seen: list[str] = []

class Cursor:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, query):
        assert 'column_privileges' in query and "grantee = 'carr_jobs'" in query
        return [('state',), ('monitoring_at',)]

class Connection:
    def cursor(self): return Cursor()

@contextlib.contextmanager
def rollback_only(dsn):
    seen.append(dsn)
    yield Connection()

with tempfile.TemporaryDirectory() as root:
    token = Path(root) / 'synthetic-token.env'
    token.touch()
    dsn = 'postgres://carr_ci@127.0.0.1:55432/carr_ci'
    env = {'CARR_MCP_ENV': str(token), 'CARR_CI_DATABASE_URL': dsn}
    with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {
        'gate_runtime_role': types.SimpleNamespace(rollback_only_connection=rollback_only)
    }), patch.object(probe, 'probe_capture_path', return_value=(True, 'seeded schema refusal')):
        output = io.StringIO()
        with contextlib.redirect_stdout(output): result = probe.main()
        assert result == 0, f'reconstructed CI grant boundary skipped: {output.getvalue()}'
        assert seen == [dsn]
        assert 'close stays a human' in output.getvalue()
        assert 'PASS' in output.getvalue()
print('capture probe: reconstructed CI grant boundary read and verified')
