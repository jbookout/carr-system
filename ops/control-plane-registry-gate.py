#!/usr/bin/env python3
"""Read back the canonical ledger registry and compare every tracked contract."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "ops" / "config" / "control-plane-workflows.v1.json"


def normalized(value):
    return value if isinstance(value, (dict, list)) else json.loads(value)


def fetchone_required(row: tuple[Any, ...] | None, context: str) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError(f"registry gate expected one row for {context}")
    return row


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("control-plane-registry-gate: DATABASE_URL is required", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures: list[str] = []
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""select key,version,enabled,risk,execution_kind,execution_contract,
                              inventory_contract,recurrence,routing_contract,filtering_contract,
                              validation_contract,retry_policy,deduplication,completion_contract,
                              legacy_schedule from ops.job_definition""")
        live = {(r[0],r[1]):r for r in cur.fetchall()}
        expected = {(w["key"],w["version"]):w for w in manifest["workflows"]}
        if set(live) != set(expected):
            failures.append(f"workflow keys differ missing={sorted(set(expected)-set(live))} extra={sorted(set(live)-set(expected))}")
        for identity, workflow in expected.items():
            row = live.get(identity)
            if row is None:
                continue
            comparisons = {
                "enabled": (row[2],workflow["enabled"]), "risk": (row[3],workflow["risk"]),
                "execution_kind": (row[4],workflow["execution"]["kind"]),
                "execution": (normalized(row[5]),{k:v for k,v in workflow["execution"].items() if k!="kind"}),
                "inventory": (normalized(row[6]),workflow["inventory"]),
                "recurrence": (normalized(row[7]),workflow["recurrence"]),
                "routing": (normalized(row[8]),workflow["routing"]),
                "filtering": (normalized(row[9]),workflow["filtering"]),
                "validation": (normalized(row[10]),workflow["validation"]),
                "retry": (normalized(row[11]),workflow["retry"]),
                "deduplication": (normalized(row[12]),workflow["deduplication"]),
                "completion": (normalized(row[13]),workflow["completion"]),
                "legacy_schedule": (normalized(row[14]),workflow["legacy_schedule"]),
            }
            failures.extend(f"{identity[0]} {name} differs" for name,(got,want) in comparisons.items() if got!=want)
        cur.execute("select key,version,canonical_write_authority from ops.cognition_job")
        live_cognition = {(r[0],r[1]):r[2] for r in cur.fetchall()}
        expected_cognition = {(c["key"],c["version"]) for c in manifest["cognition_jobs"]}
        if set(live_cognition) != expected_cognition:
            failures.append("cognition job keys differ")
        if any(live_cognition.values()):
            failures.append("a cognition job has canonical-write authority")
        cur.execute("select count(*) from ops.job_definition where legacy_disabled_at is not null")
        if fetchone_required(cur.fetchone(), "legacy schedule cutover count")[0]:
            failures.append("a legacy schedule was disabled before acceptance")
    if failures:
        print("control-plane-registry-gate FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    print(f"control-plane-registry-gate passed: {len(expected)} workflows and {len(expected_cognition)} cognition contracts exact; zero legacy cutovers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
