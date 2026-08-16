#!/usr/bin/env python3
"""Hermetic runtime contract check for durable cost-admission refusal."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.loadpy import load_module_from_path


class Cursor:
    def __init__(self, conn: "Connection") -> None: self.conn = conn
    def __enter__(self) -> "Cursor": return self
    def __exit__(self, *_args: object) -> Literal[False]: return False
    def execute(self, statement: str, params: object = None) -> None:
        self.conn.statement, self.conn.params = statement, params
    def fetchone(self) -> tuple[bool, None, str, str]:
        return (False, None, "refusal-id", "monthly_budget_exceeded")


class Connection:
    def __init__(self) -> None:
        self.statement = ""
        self.params: object = None
        self.committed = False
    def __enter__(self) -> "Connection": return self
    def __exit__(self, *_args: object) -> Literal[False]: return False
    def cursor(self) -> Cursor: return Cursor(self)
    def commit(self) -> None: self.committed = True


def main() -> int:
    control_plane: Any = load_module_from_path(
        "control_plane_budget_refusal_under_test", str(ROOT / "tools" / "control-plane.py"))
    conn = Connection()
    original = control_plane.connect
    control_plane.connect = lambda: conn
    try:
        try:
            control_plane._reserve("job", "lease", "primary", 1.0)
        except control_plane.BudgetExceeded as exc:
            refused = str(exc) == "monthly_budget_exceeded"
        else:
            refused = False
    finally:
        control_plane.connect = original
    if not refused or not conn.committed or "ops.admit_job_cost" not in conn.statement:
        print("FAIL runtime did not convert durable non-admission into pre-dispatch BudgetExceeded")
        return 1
    print("ok: runtime commits typed cost refusal then raises BudgetExceeded before dispatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
