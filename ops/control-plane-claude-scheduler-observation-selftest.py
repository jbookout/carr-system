#!/usr/bin/env python3
"""Static acceptance for the native Claude scheduler observation boundary."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import scheduler_provider_rows
from lib.loadpy import load_module_from_path

import json

MIGRATION = (REPO / "migrations/0180_claude_scheduler_observation_receipt.sql").read_text(encoding="utf-8").lower()
DB_GATE = (REPO / "ops/control-plane-db-gate.py").read_text(encoding="utf-8").lower()
CLI = (REPO / "ops/control-plane-scheduler-cutover.py").read_text(encoding="utf-8")
NATIVE_CLI = (REPO / "tools/claude-scheduler-observe.py").read_text(encoding="utf-8")
RESOLVER = (REPO / "lib/control_plane_scheduler_cutover_db.py").read_text(encoding="utf-8").lower()
REGISTRY = json.loads((REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((REPO / "ops/config/control-plane-workflows.v1.json").read_text(encoding="utf-8"))
CONTROL_PLANE = load_module_from_path("control_plane_provider_contract_test", str(REPO / "tools/control-plane.py"))
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def main() -> int:
    rows = scheduler_provider_rows(REGISTRY, manifest=MANIFEST, repo=REPO)
    check("all 18 Claude surfaces derive exact recurrence and tracked definition hashes",
          len(rows) == 18 and len({row[2] for row in rows}) == 18
          and all(len(row[7]) == 64 and row[6].startswith("ops/scheduled-tasks/") for row in rows))
    check("provider contract is populated only by post-definition authority sync",
          "insert into ops.legacy_schedule_provider_contract" not in MIGRATION
          and "sync_scheduler_surface_registry" in CONTROL_PLANE.sync_registry.__code__.co_names)
    check("observation receipt is append-only and bound to a provisioned session device",
          "legacy_schedule_observation_receipt_append_only" in MIGRATION
          and "before update or delete" in MIGRATION
          and "where login_role=session_user and active" in MIGRATION)
    check("immutable history does not block current surface pruning or inherit mutable contract semantics",
          "foreign key (surface_id) references ops.legacy_schedule_surface_registry" not in MIGRATION
          and "join ops.legacy_schedule_provider_contract c" in MIGRATION)
    check("database validates exact surface/task/cron/timezone/definition contract",
          all(token in MIGRATION for token in (
              "p_provider_task_id <> contract.locator", "p_cron_expression <> contract.cron_expression",
              "p_timezone <> contract.timezone", "p_definition_sha256 <> contract.definition_sha256",
              "or p_enabled is null", "coalesce(p_definition_sha256,'') !~", "coalesce(p_source_fingerprint,'') !~")))
    check("idempotent replay compares immutable evidence and device identity",
          all(token in MIGRATION for token in (
              "existing.surface_id<>p_surface_id", "existing.scheduler_state<>state",
              "existing.source_fingerprint<>p_source_fingerprint", "existing.device_id<>principal.device_id",
              "idempotency key was reused with different evidence")))
    check("routine and authority roles cannot mint scheduler observations",
          "from public,carr_jobs,carr_reader,carr_writer,carr_authority" in MIGRATION
          and "to carr_device_evidence" in MIGRATION
          and "grant select on ops.legacy_schedule_provider_contract, ops.legacy_schedule_observation_receipt" in MIGRATION)
    check("provider prepare and verification require DB-resolved receipt refs, never caller JSON",
          "scheduler prepare requires one immutable observation ref, never caller JSON" in CLI
          and "scheduler verification requires immutable pre/post observation refs" in CLI)
    check("native adapter derives provider state before the fixed receipt submission",
          "read_native_task" in NATIVE_CLI and "submission_for_surface" in NATIVE_CLI
          and "validated_native_read" in NATIVE_CLI)
    check("historic observations resolve only while every current provider-contract field still matches",
          all(token in RESOLVER for token in (
              "c.surface_id=r.surface_id", "c.workflow_key=r.workflow_key",
              "c.workflow_version=r.workflow_version", "c.locator=r.locator",
              "c.cron_expression=r.cron_expression", "c.timezone=r.timezone",
              "c.definition_sha256=r.definition_sha256")))
    check("Joe retirement consumes exact immutable enabled-to-disabled native observations",
          all(token in MIGRATION for token in (
              "p_pre_observation_ref", "p_post_observation_ref",
              "pre.scheduler_state<>'enabled'", "post.scheduler_state<>'disabled'",
              "pre.source_fingerprint=post.source_fingerprint",
              "legacy_disable_pre_observation_fk", "legacy_disable_post_observation_fk"))
          and "disable_legacy_schedule(text,text,text,text,text,text,text,text)" in MIGRATION
          and "drop function ops.disable_legacy_schedule(text,text,text,text,text)" in MIGRATION)
    check("direct authority path requires both observations current and rejects legacy NULL replay",
          all(token in MIGRATION for token in (
              "pre.observed_at<now()-interval '15 minutes'",
              "post.observed_at<now()-interval '15 minutes'",
              "pre.observed_at>now()+interval '5 minutes'",
              "post.observed_at>now()+interval '5 minutes'",
              "existing.pre_observation_ref is distinct from p_pre_observation_ref",
              "existing.post_observation_ref is distinct from p_post_observation_ref")))
    check("Claude path remains exact while Notes duplicate stays separately fail-closed",
          "legacy schedule lacks a current native claude provider contract" in MIGRATION
          and "notes duplicate retirement requires two-surface native evidence" in MIGRATION)
    check("DB gate asserts exact provider-contract parity and jobs mint refusal",
          "Claude scheduler provider contract is empty, stale".lower() in DB_GATE
          and "carr_jobs can mint native scheduler observations" in DB_GATE)
    print(f"control-plane Claude scheduler observation selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
