#!/usr/bin/env python3
"""Static acceptance for immutable native launchd scheduler observations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import scheduler_launchd_rows

MIGRATION = (REPO / "migrations/0182_launchd_scheduler_observation_receipt.sql").read_text().lower()
CLI = (REPO / "ops/control-plane-scheduler-cutover.py").read_text()
NATIVE = (REPO / "tools/launchd-scheduler-observe.py").read_text()
RESOLVER = (REPO / "lib/control_plane_scheduler_cutover_db.py").read_text().lower()
REGISTRY = json.loads((REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text())
MANIFEST = json.loads((REPO / "ops/config/control-plane-workflows.v1.json").read_text())
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def main() -> int:
    rows = scheduler_launchd_rows(REGISTRY, manifest=MANIFEST, repo=REPO)
    declared = sum(item["scheduler_kind"] == "launchd" for item in REGISTRY["surfaces"])
    check("every launchd surface derives an exact tracked plist and recurrence contract",
          len(rows) == declared and len({row[2] for row in rows}) == declared
          and all(len(row[7]) == 64 and len(row[8]) == 64 for row in rows))
    check("migration does not preseed FK-bound launchd contracts",
          "insert into ops.legacy_schedule_launchd_contract" not in MIGRATION)
    check("launchd observation is append-only and device-session bound",
          "where login_role=session_user and active" in MIGRATION
          and "legacy_schedule_observation_receipt" in MIGRATION
          and "to carr_device_evidence" in MIGRATION)
    check("database binds exact label timezone plist and schedule hashes",
          all(token in MIGRATION for token in (
              "p_label <> contract.locator", "p_timezone <> contract.timezone",
              "p_plist_sha256 <> contract.plist_sha256",
              "p_schedule_sha256 <> contract.schedule_sha256", "or p_enabled is null")))
    check("direct replay is NULL-safe and bound to device plus native revision",
          all(token in MIGRATION for token in (
              "existing.scheduler_kind is distinct from 'launchd'",
              "existing.provider_revision is distinct from p_launchctl_revision",
              "existing.device_id is distinct from principal.device_id")))
    check("native adapter reads launchctl and plists before the fixed stored function",
          "read_native_launchd" in NATIVE and "submission_for_surface" in NATIVE
          and "validated_native_read" in NATIVE)
    check("generic device JSON path still refuses scheduler assertions",
          "scheduler observations must come from the native read adapter" in
          (REPO / "tools/device-evidence-submit.py").read_text())
    check("resolver joins launchd history to every exact current native contract field",
          all(token in RESOLVER for token in (
              "legacy_schedule_launchd_contract l", "l.surface_id=r.surface_id",
              "l.workflow_version=r.workflow_version", "l.locator=r.locator",
              "l.schedule_sha256=r.cron_expression", "l.timezone=r.timezone",
              "l.plist_sha256=r.definition_sha256")))
    check("prepare and verify consume immutable refs for every scheduler kind",
          "scheduler prepare requires one immutable observation ref, never caller JSON" in CLI
          and "scheduler verification requires immutable pre/post observation refs" in CLI)
    check("Joe authority consumes fresh exact launchd enabled-to-disabled evidence",
          all(token in MIGRATION for token in (
              "kind='launchd'", "pre.scheduler_state<>'enabled'",
              "post.scheduler_state<>'disabled'", "pre.observed_at<now()-interval '15 minutes'",
              "post.observed_at<now()-interval '15 minutes'")))
    check("Notes duplicate remains fail-closed pending two-surface evidence",
          "notes duplicate retirement requires two-surface native evidence" in MIGRATION)
    print(f"control-plane launchd scheduler observation selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
