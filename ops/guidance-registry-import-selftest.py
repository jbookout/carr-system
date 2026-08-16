#!/usr/bin/env python3
"""Hermetic tests for the dry-run-first typed Guidance Registry importer."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("guidance_registry_import", REPO / "ops" / "guidance-registry-import.py")
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def refuses(fn, text: str) -> None:
    try:
        fn()
    except mod.ImportRefusal as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError(f"expected refusal containing {text!r}")


def main() -> int:
    expected = {"aaaaaaaa", "bbbbbbbb"}
    rows = [
        {"source_id": "aaaaaaaa", "id": "aaaaaaaa-0000-4000-8000-000000000001"},
        {"source_id": "bbbbbbbb", "id": "bbbbbbbb-0000-4000-8000-000000000002"},
    ]
    assert mod.resolve_active_rules(rows, expected) == {
        "aaaaaaaa": "aaaaaaaa-0000-4000-8000-000000000001",
        "bbbbbbbb": "bbbbbbbb-0000-4000-8000-000000000002",
    }
    refuses(lambda: mod.resolve_active_rules(rows[:1], expected), "missing=bbbbbbbb")
    refuses(lambda: mod.resolve_active_rules(rows + [{"source_id": "aaaaaaaa", "id": "aaaaaaaa-0000-4000-8000-000000000009"}], expected), "ambiguous")
    refuses(lambda: mod.resolve_active_rules(rows + [{"source_id": "cccccccc", "id": "cccccccc-0000-4000-8000-000000000003"}], expected), "extra=cccccccc")
    assert mod.resolve_classifier_actor([{"id": "actor-codex"}], "codex") == "actor-codex"
    refuses(lambda: mod.resolve_classifier_actor([], "codex"), "exactly one")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mapping.json"
        path.write_text(json.dumps({"schema": mod.MAPPING_PLAN_SCHEMA, "doctrine_mappings": {}}), encoding="utf-8")
        assert mod.load_mapping_plan(path) == {}
        path.write_text("{}", encoding="utf-8")
        refuses(lambda: mod.load_mapping_plan(path), "must declare schema")
    class Args:
        apply = True
        stage_idempotency_key = "same"
        stage_reason = "stage"
        apply_idempotency_key = "same"
        apply_reason = "apply"
    refuses(lambda: mod.require_apply_args(Args()), "distinct idempotency")
    refuses(lambda: mod.require_writer_dsn({}), "CARR_DB_WRITER_URL")
    writer_url = "postgresql://carr_writer@example.invalid/carr"
    assert mod.require_writer_dsn({"CARR_DB_WRITER_URL": writer_url}) == writer_url
    class IdentityCursor:
        def __init__(self, identity): self.identity = identity; self.calls = []
        def execute(self, sql): self.calls.append(sql); return self
        def fetchone(self): return self.identity
    writer_identity = IdentityCursor({"session_user": "carr_writer", "current_user": "carr_writer"})
    mod.assert_writer_identity(writer_identity)
    assert writer_identity.calls == ["select session_user::text as session_user, current_user::text as current_user"]
    refuses(lambda: mod.assert_writer_identity(IdentityCursor({"session_user": "carr_owner", "current_user": "carr_owner"})),
            "must both be carr_writer")
    refuses(lambda: mod.assert_writer_identity(IdentityCursor({"session_user": "unrecognized", "current_user": "unrecognized"})),
            "must both be carr_writer")
    refuses(lambda: mod.assert_writer_identity(IdentityCursor({"session_user": "carr_writer", "current_user": "carr_owner"})),
            "must both be carr_writer")
    read_only_cursor = IdentityCursor({})
    mod.begin_read_only(read_only_cursor)
    assert read_only_cursor.calls == ["set transaction read only"]
    class WriterCursor(IdentityCursor):
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    class WriterConnection:
        def __init__(self, cursor): self.cursor_value = cursor; self.committed = False
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self, **_kwargs): return self.cursor_value
        def commit(self): self.committed = True
    denied_cursor = WriterCursor({"session_user": "carr_owner", "current_user": "carr_owner"})
    denied_connection = WriterConnection(denied_cursor)
    writer_dsns = []
    original_psycopg = getattr(mod, "psycopg")
    def denied_connect(dsn):
        writer_dsns.append(dsn)
        return denied_connection
    setattr(mod, "psycopg", types.SimpleNamespace(
        connect=denied_connect,
        rows=types.SimpleNamespace(dict_row=object()),
    ))
    try:
        refuses(lambda: mod.apply_reviewed_batch(
            "writer-dsn", digest="d", canonical_manifest_text="{}\\n", classifier_actor_slug="codex",
            stage_idempotency_key="stage", stage_reason="stage", apply_idempotency_key="apply",
            apply_reason="apply"), "must both be carr_writer")
    finally:
        setattr(mod, "psycopg", original_psycopg)
    assert writer_dsns == ["writer-dsn"]
    assert denied_cursor.calls == ["select session_user::text as session_user, current_user::text as current_user"]
    assert not denied_connection.committed
    class FakeCursor:
        def __init__(self): self.calls = []; self.rows = iter([{"id": "batch"}, {"id": "applied"}])
        def execute(self, sql, params): self.calls.append((sql, params)); return self
        def fetchone(self): return next(self.rows)
    fake = FakeCursor()
    assert mod.stage_and_apply(fake, digest="d", canonical_manifest_text="{}\\n", classifier_actor_id="actor-codex",
                               stage_idempotency_key="stage", stage_reason="stage", apply_idempotency_key="apply",
                               apply_reason="apply") == ("batch", "applied")
    assert "stage_guidance_import_batch" in fake.calls[0][0]
    assert fake.calls[0][1] == ("d", "{}\\n", "actor-codex", "stage", "stage")
    assert "apply_guidance_import_batch" in fake.calls[1][0]
    source = (REPO / "ops" / "guidance-registry-import.py").read_text(encoding="utf-8")
    assert "where status='active' order by id" in source
    assert "left(id::text,8) = any" not in source
    assert 'env.get("CARR_DB_WRITER_URL"' in source
    assert 'os.environ.get("DATABASE_URL")' in source
    assert "select session_user::text as session_user, current_user::text as current_user" in source
    assert 'cur.execute("set transaction read only")' in source
    assert "apply_reviewed_batch(" in source
    assert '.open("xb")' in source
    print("guidance-registry-import-selftest: 18 cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
