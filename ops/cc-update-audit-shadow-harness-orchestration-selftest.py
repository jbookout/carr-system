#!/usr/bin/env python3
"""Hermetic failure-path tests for the disposable cognition-shadow harness."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

PATH = Path(__file__).with_name("cc-update-audit-shadow-harness.py")
SPEC = importlib.util.spec_from_file_location("cc_shadow_harness", PATH)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def refuses(fn, text: str) -> None:
    try:
        fn()
    except mod.HarnessRefusal as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError(f"expected refusal containing {text!r}")


class FakeDbError(Exception): pass


class RefusingCursor:
    def __init__(self) -> None: self.calls: list[str] = []
    def execute(self, sql: str, _params=None):
        self.calls.append(sql)
        if sql.startswith("update ops.job_receipt"): raise FakeDbError("append-only")
    def fetchone(self): return (1,)


class PermissiveCursor(RefusingCursor):
    def execute(self, sql: str, _params=None): self.calls.append(sql)


def main() -> int:
    exact = "cc-shadow-exact"
    deleted: list[str] = []
    def delete_success(branch_id: str) -> bool:
        deleted.append(branch_id)
        return True
    # A successful provider create response may be malformed; exact-name
    # resolution, not its payload, determines the sole teardown target.
    assert mod.created_branch_id("not-json") == ""
    mod.cleanup_ephemeral_branch(
        created_ok=True, branch_id="", branch_name=exact,
        list_branches=lambda: [{"name": exact, "id": "branch-1", "default": False}],
        delete_branch=delete_success,
    )
    assert deleted == ["branch-1"]
    refused_deletes: list[str] = []
    def record_delete(branch_id: str) -> bool:
        refused_deletes.append(branch_id)
        return True
    # A parsed create ID is never a deletion target.  If it does not equal the
    # fresh exact-name resolution, the harness refuses before touching either.
    refuses(lambda: mod.cleanup_ephemeral_branch(
        created_ok=True, branch_id="malicious-create-id", branch_name=exact,
        list_branches=lambda: [{"name": exact, "id": "branch-1", "default": False}],
        delete_branch=record_delete), "differs from exact")
    assert refused_deletes == []
    refuses(lambda: mod.cleanup_ephemeral_branch(
        created_ok=True, branch_id="", branch_name=exact,
        list_branches=lambda: [{"name": exact + "-other", "id": "wrong", "default": False}],
        delete_branch=record_delete), "exact name")
    assert refused_deletes == []
    refuses(lambda: mod.cleanup_ephemeral_branch(
        created_ok=True, branch_id="", branch_name=exact,
        list_branches=lambda: [{"name": exact, "id": "one", "default": False},
                                {"name": exact, "id": "two", "default": False}],
        delete_branch=record_delete), "exact name")
    assert refused_deletes == []
    refuses(lambda: mod.cleanup_ephemeral_branch(
        created_ok=True, branch_id="", branch_name=exact,
        list_branches=lambda: {"name": exact, "id": "branch-1"},
        delete_branch=record_delete), "not an array")
    assert refused_deletes == []
    refuses(lambda: mod.cleanup_ephemeral_branch(
        created_ok=True, branch_id="", branch_name=exact,
        list_branches=lambda: [{"name": exact, "id": "default", "default": True}],
        delete_branch=record_delete), "default branch")
    assert refused_deletes == []
    refuses(lambda: mod.cleanup_ephemeral_branch(
        created_ok=True, branch_id="branch-1", branch_name=exact,
        list_branches=lambda: [{"name": exact, "id": "branch-1", "default": False}],
        delete_branch=lambda _id: False), "teardown failed")

    original = '[{"release_key":"a","unrelated_column":"before"}]'
    changed = '[{"release_key":"a","unrelated_column":"after"}]'
    assert mod.fingerprint_release_snapshot(original) != mod.fingerprint_release_snapshot(changed)

    refusing = RefusingCursor()
    mod.assert_receipt_append_only_cursor(refusing, "job-1", FakeDbError)
    assert any(sql.startswith("rollback to savepoint") for sql in refusing.calls)
    refuses(lambda: mod.assert_receipt_append_only_cursor(PermissiveCursor(), "job-1", FakeDbError),
            "update was accepted")
    secret_url = "postgresql://carr_jobs:top-secret@example.invalid/carr"
    try:
        mod.must(subprocess.CompletedProcess(
            args=["fixture"], returncode=1,
            stdout="", stderr=f"connection failed at {secret_url}\n",
        ), "redaction fixture")
    except mod.HarnessRefusal as exc:
        detail = str(exc)
        assert secret_url not in detail
        assert "top-secret" not in detail
        assert "[url]" in detail
    else:
        raise AssertionError("expected failed subprocess refusal")
    source = PATH.read_text(encoding="utf-8")
    assert "'wrapper','fixture:release:'||k" in source
    assert "'wrapper','fixture:deployment'" in source
    assert "'fixture','fixture:release:'||k" not in source
    print("cc-update-audit-shadow-harness-orchestration-selftest: 12 cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
