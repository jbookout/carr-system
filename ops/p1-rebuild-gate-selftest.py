#!/usr/bin/env python3
"""Hermetic regression tests for the rebuilt carr_jobs login probe."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Literal

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.loadpy import load_module_from_path


gate = load_module_from_path("p1_rebuild_gate_under_test", str(REPO / "ops" / "p1-rebuild-gate.py"))
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


class Result:
    def __init__(self, row: tuple[str, str]) -> None:
        self.row = row

    def fetchone(self) -> tuple[str, str]:
        return self.row


class ProviderResult:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


class Connection:
    def __init__(self, row: tuple[str, str] | None = None) -> None:
        self.row = row
        self.calls: list[tuple[object, tuple[object, ...]]] = []
        self.committed = False

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, *_args: object) -> Literal[False]:
        return False

    def execute(self, sql: object, params: tuple[object, ...] = ()) -> Result:
        self.calls.append((sql, params))
        if self.row is None:
            return Result(("", ""))
        return Result(self.row)

    def commit(self) -> None:
        self.committed = True


class FakeSql:
    class Identifier:
        def __init__(self, value: str) -> None:
            self.value = value

    class Literal:
        def __init__(self, value: str) -> None:
            self.value = value

    class SQL:
        def __init__(self, value: str) -> None:
            self.value = value

        def format(self, identifier: "FakeSql.Identifier", literal: "FakeSql.Literal") -> tuple[str, str, str]:
            return (self.value, identifier.value, literal.value)


def main() -> int:
    owner = Connection()
    jobs = Connection(("carr_jobs", "carr_jobs"))
    seen: list[str] = []

    def connect(dsn: str) -> Connection:
        seen.append(dsn)
        return owner if len(seen) == 1 else jobs

    good = gate.verify_rebuilt_jobs_login(
        "postgresql://neondb_owner:owner@branch.example:5432/rebuild_check?sslmode=require",
        connect=connect, password_factory=lambda _n: "a/password?never-printed",
        sql_module=FakeSql,
    )
    check("generated password is composed as role identifier/literal without LOGIN conversion",
          owner.calls == [(("alter role {} password {}", "carr_jobs", "a/password?never-printed"), ())]
          and owner.committed)
    check("probe connects as carr_jobs and preserves the database/query target",
          good and len(seen) == 2 and seen[1].startswith("postgresql://carr_jobs:a%2Fpassword%3Fnever-printed@branch.example:5432/rebuild_check")  # ci-secret-scan: allow — encoded selftest fixture
          and seen[1].endswith("?sslmode=require"))
    check("probe reads both authenticated identity fields",
          jobs.calls == [("select session_user, current_user", ())])

    mismatch_owner = Connection()
    mismatch_jobs = Connection(("carr_jobs", "neondb_owner"))
    calls = 0

    def mismatch_connect(_dsn: str) -> Connection:
        nonlocal calls
        calls += 1
        return mismatch_owner if calls == 1 else mismatch_jobs

    check("role switching cannot impersonate a rebuilt carr_jobs login",
          not gate.verify_rebuilt_jobs_login("postgresql://owner@branch.example/rebuild_check",
                                             connect=mismatch_connect,
                                             password_factory=lambda _n: "fixture",
                                             sql_module=FakeSql))

    provider_calls: list[tuple[object, ...]] = []
    sleeps: list[float] = []
    provider_results = iter([
        ProviderResult(1),
        ProviderResult(0, ""),
        ProviderResult(0, "postgresql://fixture@branch.example/neondb?sslmode=require\n"),  # ci-secret-scan: allow — hermetic non-routable fixture
    ])

    def provider(_env: dict[str, str], *args: str) -> ProviderResult:
        provider_calls.append(args)
        return next(provider_results)

    dsn = gate.wait_for_branch_connection_string(
        {}, "branch-id", "staging-project", attempts=3, delay_seconds=0.25,
        runner=provider, sleeper=sleeps.append,
    )
    check("new branch connection lookup retries bounded provider-not-ready results",
          dsn.endswith("sslmode=require") and sleeps == [0.25, 0.25]
          and len(provider_calls) == 3)
    check("connection lookup pins branch, project, owner, database, and read-write endpoint",
          all(call == (
              "connection-string", "branch-id", "--project-id", "staging-project",
              "--role-name", "neondb_owner", "--database-name", "neondb",
              "--endpoint-type", "read_write",
          ) for call in provider_calls))

    exhausted_sleeps: list[float] = []
    exhausted = gate.wait_for_branch_connection_string(
        {}, "branch-id", "staging-project", attempts=2, delay_seconds=0.5,
        runner=lambda _env, *_args: ProviderResult(1), sleeper=exhausted_sleeps.append,
    )
    check("connection lookup fails closed after its bounded retry window",
          exhausted == "" and exhausted_sleeps == [0.5])

    print(f"p1-rebuild-gate-selftest: {7 - len(FAILED)}/7 passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
