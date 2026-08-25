#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "eligibility", REPO / "ops" / "rule-delivery-shadow-eligibility.py")
assert spec and spec.loader
eligibility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eligibility)
NOW = datetime(2026,8,25,22,0,tzinfo=timezone.utc)


def row(hours: int, **changes):
    value = {"ts": (NOW-timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "mode":"shadow","loaded":["engineering-git"],"would_omit_count":150,
             "missed_rules":[]}
    value.update(changes)
    return value


def ok(rows): return eligibility.evaluate(rows,NOW)["eligible"]

cases = [
    ("empty", [], False),
    ("presence is not scoped", [row(170,loaded=[],would_omit_count=0)], False),
    ("thirty hours is short", [row(30),row(0)], False),
    ("seven days continuous", [row(h) for h in range(0,169,24)], True),
    ("stale newest", [row(h) for h in range(49,218,24)], False),
    ("gap", [row(200),row(120),row(48),row(0)], False),
    ("miss", [row(h,missed_rules=["deadbeef"] if h==72 else []) for h in range(0,169,24)], False),
    ("error", [row(h,error="boom") if h==72 else row(h) for h in range(0,169,24)], False),
    ("mode null ignored", [row(170,mode=None),*[row(h) for h in range(0,169,24)]], True),
]
failures=[]
for label,rows,want in cases:
    got=ok(rows)
    if got != want: failures.append(f"{label}: {got} != {want}")
if failures:
    print("rule-delivery-shadow-eligibility-selftest: FAIL")
    for failure in failures: print("  "+failure)
    raise SystemExit(1)
print(f"rule-delivery-shadow-eligibility-selftest: {len(cases)} cases passed")
