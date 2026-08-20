#!/usr/bin/env python3
"""Fail-closed Phase 4 gate: reduce only database-resolved durable evidence."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.partner_continuity import ContinuityRefusal, evaluate_window, load_contract
from lib.partner_continuity_db import resolver_from_environment


def validate_database_evidence(resolver: object) -> dict[str, object]:
    contract = load_contract(REPO / "ops/config/partner-continuity-contract.v1.json")
    rows = resolver.evidence_rows()  # type: ignore[attr-defined]
    result = evaluate_window(contract, rows)
    result["drive_status"] = resolver.drive_status()  # type: ignore[attr-defined]
    return result


def main() -> int:
    if len(sys.argv) != 1:
        print("NOT READY: this gate accepts no caller evidence JSON; it reads fixed database projections")
        return 2
    resolver = None
    try:
        resolver = resolver_from_environment()
        result = validate_database_evidence(resolver)
    except ContinuityRefusal as exc:
        print(f"NOT READY: {exc}")
        return 2
    finally:
        if resolver is not None:
            resolver.close()
    print("partner continuity evidence: all ten durable streams cover one common 48-hour window; "
          f"Drive={result['drive_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
