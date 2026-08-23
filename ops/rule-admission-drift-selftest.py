#!/usr/bin/env python3
"""Contract tests for the nightly rule-admission drift watch.

The watch reads Production under a routine role, so what is worth pinning is
what it refuses to connect as, and that the numbers it prints are judged by the
same predicate the audit itself uses. Both are checked without a database.
"""
from __future__ import annotations

import importlib.util
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


def main() -> int:
    failures: list[str] = []
    ran: list[str] = []

    def check(name, condition):
        ran.append(name)
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    drift = load("rule_admission_drift", "rule-admission-drift.py")
    audit = load("rule_admission_audit", "rule-admission-audit.py")

    os.environ.pop("CARR_DB_JOBS_URL", None)
    check("no routine credential is a SKIP, not a failed night",
          drift.main() == drift.EX_CONFIG)

    os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_owner:x@example/db"
    try:
        drift.routine_dsn()
        refused = False
    except SystemExit as exc:
        refused = exc.code == 1
    check("an owner login is refused before any connection is opened", refused)

    os.environ["CARR_DB_JOBS_URL"] = "postgresql://carr_jobs:x@example/db"
    check("a jobs login is accepted", drift.routine_dsn() is not None)
    os.environ.pop("CARR_DB_JOBS_URL", None)

    clean = {"total": 218, "admitted": 218, "needs_revision": 0, "missing": 0, "incomplete": 0}
    check("a fully admitted store is not a finding", audit.failing(clean) is False)
    check("a rule with no contract is a finding",
          audit.failing({**clean, "admitted": 217, "missing": 1}) is True)
    check("a contract admitted against an uninstalled control is a finding",
          audit.failing({**clean, "admitted": 217, "needs_revision": 1}) is True)
    check("an admitted contract missing its four dimensions is a finding",
          audit.failing({**clean, "incomplete": 1}) is True)
    # The empty store is the shape a sanitized rehearsal database has, and it
    # must stay a finding by default: the rollback gate opts into it explicitly.
    empty = {"total": 0, "admitted": 0, "needs_revision": 0, "missing": 0, "incomplete": 0}
    check("an empty store is a finding unless explicitly allowed",
          audit.failing(empty) is True
          and audit.failing(empty, allow_empty_store=True) is False)
    check("the rendered line names all five numbers",
          all(k in audit.render(clean) for k in
              ("total=", "admitted=", "needs_revision=", "missing=", "incomplete=")))

    print(f"\nrule-admission-drift-selftest: {len(ran)-len(failures)}/{len(ran)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
