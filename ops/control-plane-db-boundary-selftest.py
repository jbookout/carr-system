#!/usr/bin/env python3
"""Hermetic credential and read-only boundary checks for the ledger CLI."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable, Literal

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.loadpy import load_module_from_path

cp = load_module_from_path("control_plane_db_boundary_under_test",
                           str(REPO / "tools" / "control-plane.py"))
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def raises_system_exit(callable_) -> bool:
    try:
        callable_()
    except SystemExit:
        return True
    return False


class Cursor:
    def __init__(self, conn: "Connection") -> None:
        self.conn = conn

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_args: object) -> Literal[False]:
        return False

    def execute(self, sql: str) -> None:
        self.conn.sql.append(sql)

    def fetchone(self) -> tuple[str, str]:
        return self.conn.identity


class Connection:
    def __init__(self, identity: tuple[str, str]) -> None:
        self.identity: tuple[str, str] = identity
        self.sql: list[str] = []
        self.closed = False

    def cursor(self) -> Cursor:
        return Cursor(self)

    def close(self) -> None:
        self.closed = True


def install_fake_psycopg(connect: Callable[[str], Connection]) -> None:
    module = ModuleType("psycopg")
    setattr(module, "connect", connect)
    sys.modules["psycopg"] = module


def main() -> int:
    original = dict(os.environ)
    original_psycopg = sys.modules.get("psycopg")
    try:
        os.environ.clear()
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs:secret@example.invalid/carr"  # ci-secret-scan: allow — selftest fixture
        check("routine DB URL comes only from CARR_DB_JOBS_URL",
              cp.database_url() == os.environ["CARR_DB_JOBS_URL"])
        os.environ["DATABASE_URL"] = "postgresql://carr_writer:secret@example.invalid/carr"  # ci-secret-scan: allow — selftest fixture
        check("ambient DATABASE_URL cannot replace routine jobs URL",
              cp.database_url() == os.environ["CARR_DB_JOBS_URL"])
        os.environ.pop("CARR_DB_JOBS_URL")
        check("routine command refuses without CARR_DB_JOBS_URL",
              raises_system_exit(cp.database_url))
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_writer:secret@example.invalid/carr"  # ci-secret-scan: allow — selftest fixture
        check("writer DSN is refused even when misfiled as jobs URL",
              raises_system_exit(cp.database_url))
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://owner:secret@example.invalid/carr"  # ci-secret-scan: allow — selftest fixture
        check("owner DSN is refused even when misfiled as jobs URL",
              raises_system_exit(cp.database_url))
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs:secret@example.invalid/carr"  # ci-secret-scan: allow — selftest fixture
        os.environ["DATABASE_URL"] = "postgresql://carr_writer:secret@example.invalid/carr"  # ci-secret-scan: allow — selftest fixture
        check("authority bootstrap retains its dedicated DATABASE_URL path",
              cp.database_url(routine=False) == os.environ["DATABASE_URL"])

        made: list[Connection] = []
        def connect_good(_dsn: str) -> Connection:
            conn = Connection(("carr_jobs", "carr_jobs"))
            made.append(conn)
            return conn
        install_fake_psycopg(connect_good)
        cp.connect(read_only=True)
        check("read-only transaction starts before identity or collector SQL",
              bool(made) and made[-1].sql[:2] == ["begin transaction read only", "select session_user, current_user"])

        wrong_connections: list[Connection] = []
        def connect_wrong(_dsn: str) -> Connection:
            conn = Connection(("carr_writer", "carr_jobs"))
            wrong_connections.append(conn)
            return conn
        install_fake_psycopg(connect_wrong)
        try:
            cp.connect(read_only=True)
            wrong_identity = False
        except RuntimeError:
            wrong_identity = True
        check("routine connection refuses a writer session even after role switch", wrong_identity)
        check("identity refusal closes the untrusted connection",
              bool(wrong_connections) and wrong_connections[-1].closed)

        os.environ["CARR_DB_JOBS_ROLE"] = "carr_writer"
        def connect_env_override(_dsn: str) -> Connection:
            return Connection(("carr_writer", "carr_writer"))
        install_fake_psycopg(connect_env_override)
        try:
            cp.connect()
            env_override = False
        except RuntimeError:
            env_override = True
        check("caller-controlled role override cannot admit a writer identity", env_override)
    finally:
        os.environ.clear()
        os.environ.update(original)
        if original_psycopg is None:
            sys.modules.pop("psycopg", None)
        else:
            sys.modules["psycopg"] = original_psycopg
    print(f"control-plane DB boundary selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
