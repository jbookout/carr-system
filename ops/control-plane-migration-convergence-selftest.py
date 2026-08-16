#!/usr/bin/env python3
"""Keep renumbered control-plane migrations safe on an old equivalent schema.

The first control-plane attempt used migration numbers 0134--0145.  A staging
ledger can contain those old numbers while its schema already contains the
same append-only triggers.  PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS``;
each recreated trigger must therefore have a preceding, narrowly-targeted
``DROP TRIGGER IF EXISTS`` in its renumbered migration.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATIONS = tuple(REPO / "migrations" / name for name in (
    "0148_control_plane_admission.sql",
    "0149_control_plane_jobs.sql",
    "0150_control_plane_job_fixes.sql",
    "0151_control_plane_admission_grants.sql",
    "0152_rule_writer_grants.sql",
    "0153_control_plane_resilience.sql",
    "0154_control_plane_cost_release.sql",
    "0155_rule_applicability_wildcard.sql",
    "0156_control_plane_input_grants.sql",
    "0157_control_plane_runtime_guards.sql",
    "0158_job_timeout_receipts.sql",
    "0159_control_plane_evidence_grants.sql",
))
CREATE_TRIGGER = re.compile(r"(?im)^\s*create\s+trigger\s+([a-z_][a-z0-9_]*)\b")
DROP_TRIGGER = re.compile(r"(?im)^\s*drop\s+trigger\s+if\s+exists\s+([a-z_][a-z0-9_]*)\b")
EXPECTED_LEGACY_TRIGGER_NAMES = {
    "authority_receipt_append_only",
    "rule_activation_requires_admission",
    "job_attempt_append_only",
    "job_receipt_append_only",
    "provider_observation_append_only",
    "workflow_acceptance_append_only",
    "job_definition_cutover_requires_evidence",
    "cost_reservation_no_delete",
}


def main() -> int:
    failures: list[str] = []
    checked = 0
    created_names: set[str] = set()
    for path in MIGRATIONS:
        source = path.read_text()
        for created in CREATE_TRIGGER.finditer(source):
            checked += 1
            name = created.group(1)
            created_names.add(name)
            drop_positions = [drop.start() for drop in DROP_TRIGGER.finditer(source)
                              if drop.group(1) == name]
            if not any(position < created.start() for position in drop_positions):
                failures.append(f"{path.name}: {name} is created without a prior DROP TRIGGER IF EXISTS")
    missing = sorted(EXPECTED_LEGACY_TRIGGER_NAMES - created_names)
    if missing:
        failures.append("missing expected legacy trigger creation(s): " + ", ".join(missing))
    if failures:
        print("control-plane migration convergence selftest: FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"control-plane migration convergence selftest: {checked}/{checked} triggers have drop-before-create guards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
