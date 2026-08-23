#!/usr/bin/env python3
"""Contract tests for the nightly control-plane registry drift watch.

The watch reads Production under a routine role, so the two things worth
pinning are what it refuses to connect as, and that it never reports an
unreadable input as a pass. Both are checked here without a database.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "ops" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubCursor:
    """Replays the fixed query sequence compare() issues, in order."""

    def __init__(self, definitions, cognition, cutovers):
        self.definitions = definitions
        self.cognition = cognition
        self.cutovers = cutovers
        self.result: list = []
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        if "from ops.job_definition" in sql and "count(*)" not in sql:
            self.result = list(self.definitions)
        elif "from ops.cognition_job" in sql:
            self.result = list(self.cognition)
        elif "count(*)" in sql:
            self.result = [(self.cutovers,)]
        else:  # any calendar-prebrief read is a contract violation for this caller
            raise AssertionError(f"unexpected statement: {sql}")

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


def main() -> int:
    failures: list[str] = []
    ran: list[str] = []

    def check(name, condition):
        ran.append(name)
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    drift = load("control_plane_registry_drift", "control-plane-registry-drift.py")
    gate = load("control_plane_registry_gate", "control-plane-registry-gate.py")

    os.environ.pop("CARR_DB_JOBS_URL", None)
    check("no routine credential is a SKIP, not a failed night",
          drift.main() == drift.EX_CONFIG)

    os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_writer:x@example/db"
    try:
        drift.routine_dsn()
        refused = False
    except SystemExit as exc:
        refused = exc.code == 1
    check("a writer login is refused before any connection is opened", refused)

    os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs:x@example/db"
    check("a jobs login is accepted", drift.routine_dsn() is not None)
    os.environ.pop("CARR_DB_JOBS_URL", None)

    key, version = gate.MANAGED_ENABLED_IDENTITY
    workflow = {
        "key": key, "version": version, "enabled": False, "risk": "yellow",
        "execution": {"kind": "deterministic", "command": "x"},
        "inventory": {"owner": "system"}, "recurrence": {"cron": "* * * * *"},
        "state": {}, "routing": {}, "filtering": {}, "validation": {},
        "retry": {}, "deduplication": {}, "completion": {}, "legacy_schedule": {},
    }
    manifest = {"workflows": [workflow], "cognition_jobs": []}
    # The live row is ENABLED where the manifest says disabled — the exact
    # difference only the activation receipt can excuse.
    row = (key, version, True, "yellow", "deterministic",
           json.dumps({"command": "x"}), json.dumps({"owner": "system"}),
           json.dumps({"cron": "* * * * *"}), json.dumps({}), json.dumps({}),
           json.dumps({}), json.dumps({}), json.dumps({}), json.dumps({}),
           json.dumps({}))

    cur = StubCursor([row], [], 0)
    result = gate.compare(cur, manifest, resolve_managed_enabled=False)
    reported, _, _, not_checked = result
    check("a role that cannot read the receipt does not compare the managed bit",
          not any("enabled differs" in f for f in reported))
    check("and says so rather than reporting the unread input as a pass",
          len(not_checked) == 1 and key in not_checked[0])
    check("it never touches a calendar-prebrief table under the routine role",
          not any("calendar_prebrief" in s for s in cur.statements))
    check("every other contract column is still compared",
          any("risk" in f or "recurrence" in f for f in reported) is False
          and len(cur.statements) == 3)

    print(f"\ncontrol-plane-registry-drift-selftest: {len(ran)-len(failures)}/{len(ran)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
