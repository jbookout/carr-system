#!/usr/bin/env python3
"""Static seeded checks for the typed Guidance Registry migration."""
from __future__ import annotations

import os
import re
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(REPO, "migrations", "0168_guidance_registry.sql")


def main() -> int:
    if not os.path.exists(MIGRATION):
        print("guidance-registry-migration-selftest: FAIL — migration 0168 is missing", file=sys.stderr)
        return 1
    sql = open(MIGRATION, encoding="utf-8").read().lower()
    compact = re.sub(r"\s+", " ", sql)
    checks = {
        "canonical item spine": "create table ops.guidance_item" in compact,
        "append-only revisions": "create table ops.guidance_revision" in compact
            and "guidance_revision_append_only" in compact,
        "stable rule provenance": "source_rule_id" in compact
            and "references rule(id) on delete restrict" in compact,
        "exact receipt/revision binding": "create table ops.guidance_authority_binding" in compact
            and "guidance_revision_id" in compact and "authority_receipt_id" in compact
            and "contract_hash" in compact,
        "split identity": "source_clause" in compact and "is_primary" in compact
            and "split_group_id" in compact,
        "seven-type vocabulary": all(
            f"'{kind}'" in compact for kind in (
                "constraint", "procedure", "doctrine", "rubric",
                "preference", "precedent", "example")),
        "typed revision validator": "validate_guidance_revision" in compact,
        "constraint projection": "v_guidance_constraint" in compact,
        "procedure projection": "v_guidance_procedure" in compact,
        "doctrine retrieval bridge": "v_guidance_doctrine_retrieval" in compact
            and "doctrine_concept_mapping" in compact,
        "rubric projection": "v_guidance_rubric" in compact,
        "preference projection": "v_guidance_preference" in compact,
        "precedent projection": "v_guidance_precedent" in compact,
        "example projection": "v_guidance_example" in compact,
        "standing-context projection": "standing_guidance" in compact,
        "coverage gate": "assert_guidance_registry_coverage" in compact,
        "guarded activation": "activate_guidance_registry" in compact
            and "between 5 and 10" in compact,
        "no bulk rule mutation": "update rule " not in compact
            and "delete from rule" not in compact,
        "no implicit rule backfill": "insert into ops.guidance_item" not in compact
            or " from rule" not in compact,
    }
    failed = [name for name, passed in checks.items() if not passed]
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    if failed:
        print("guidance-registry-migration-selftest: failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    print(f"guidance-registry-migration-selftest: {len(checks)} contract checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
