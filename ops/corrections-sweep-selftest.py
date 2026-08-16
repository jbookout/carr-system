#!/usr/bin/env python3
"""Corrections sweep uses only purpose-limited jobs projections and identity."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.loadpy import load_module_from_path

sweep = load_module_from_path("corrections_sweep_under_test", str(REPO / "ops" / "corrections-sweep.py"))
FAILED: list[str] = []


def check(label: str, value: bool) -> None:
    print(("  ok    " if value else "  FAIL  ") + label)
    if not value:
        FAILED.append(label)


class Cursor:
    def __init__(self, conn: "Conn") -> None:
        self.conn = conn
    def __enter__(self) -> "Cursor": return self
    def __exit__(self, *_args: object) -> Literal[False]: return False
    def execute(self, sql: str) -> None: self.conn.sql.append(sql)
    def fetchone(self) -> tuple[str, str]: return self.conn.identity


class Conn:
    def __init__(self, identity: tuple[str, str]) -> None:
        self.identity: tuple[str, str] = identity
        self.sql: list[str] = []
        self.closed = False
    def cursor(self) -> Cursor: return Cursor(self)
    def close(self) -> None: self.closed = True


def main() -> int:
    original = dict(os.environ)
    try:
        os.environ.clear()
        try:
            sweep.jobs_dsn()
            missing = False
        except RuntimeError:
            missing = True
        check("missing jobs credential refuses", missing)
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_writer:x@example.invalid/carr"
        try:
            sweep.jobs_dsn()
            writer = False
        except RuntimeError:
            writer = True
        check("writer DSN is refused even in jobs variable", writer)
        os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs:x@example.invalid/carr"
        good = Conn(("carr_jobs", "carr_jobs"))
        returned = sweep.jobs_connection(type("Psycopg", (), {"connect": staticmethod(lambda _dsn: good)}))
        check("jobs connection verifies both identity values and begins read-only transaction",
              returned is good and good.sql == ["select session_user, current_user", "begin transaction read only"])
        wrong = Conn(("carr_writer", "carr_jobs"))
        try:
            sweep.jobs_connection(type("Psycopg", (), {"connect": staticmethod(lambda _dsn: wrong)}))
            wrong_identity = False
        except RuntimeError:
            wrong_identity = wrong.closed
        check("writer session is refused even after role switch", wrong_identity)
        sql = (REPO / "migrations" / "0166_correction_sweep_jobs_projection.sql").read_text(encoding="utf-8")
        check("migration grants only purpose-limited correction projections to jobs",
              "grant select on public.v_correction_sweep_defects," in sql
              and "public.v_correction_sweep_decisions to carr_jobs" in sql
              and "grant select on v_defect_class to carr_jobs" not in sql
              and "grant select on v_decision_entry to carr_jobs" not in sql)
        class QueryCursor:
            def __init__(self) -> None: self.sql: list[str] = []
            def execute(self, statement: str) -> None: self.sql.append(statement)
            def fetchall(self): return []
        query = QueryCursor()
        sweep.defect_repeats(query); sweep.quoted_corrections(query)
        check("report queries only the jobs projections",
              "v_correction_sweep_defects" in query.sql[0]
              and "v_correction_sweep_decisions" in query.sql[1]
              and "v_defect_class" not in query.sql[0]
              and "v_decision_entry" not in query.sql[1])
    finally:
        os.environ.clear(); os.environ.update(original)
    print(f"corrections sweep selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
