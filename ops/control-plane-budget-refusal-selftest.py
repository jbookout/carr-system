#!/usr/bin/env python3
"""Hermetic runtime contract check for durable cost-admission refusal."""
from __future__ import annotations

import sys
from decimal import Decimal
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
    params = conn.params if isinstance(conn.params, tuple) else ()
    numeric_bound = (len(params) == 4 and isinstance(params[3], Decimal)
                     and params[3] == Decimal("1.0"))
    settle_conn = Connection()
    control_plane.connect = lambda: settle_conn
    try:
        control_plane._settle(
            "reservation", "job", "lease",
            {"usage": {"input_tokens": 2, "output_tokens": 5,
                       "total_tokens": 7, "cost_usd": 0.01}})
    finally:
        control_plane.connect = original
    settle_params = settle_conn.params if isinstance(settle_conn.params, tuple) else ()
    settle_numeric_bound = (
        len(settle_params) == 6 and isinstance(settle_params[5], Decimal)
        and settle_params[5] == Decimal("0.01") and settle_conn.committed)
    if (not refused or not conn.committed or "ops.admit_job_cost" not in conn.statement
            or not numeric_bound or not settle_numeric_bound):
        print("FAIL runtime did not convert durable non-admission into pre-dispatch BudgetExceeded")
        return 1
    print("ok: runtime commits typed cost refusal then raises BudgetExceeded before dispatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
