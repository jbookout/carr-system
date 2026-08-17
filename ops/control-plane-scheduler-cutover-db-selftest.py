#!/usr/bin/env python3
"""Hermetic tests for the fixed-query least-privilege cutover receipt reader."""
from __future__ import annotations

import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.control_plane_scheduler_cutover import CutoverRefusal, scheduler_surface_rows
from lib.loadpy import load_module_from_path
from lib.control_plane_scheduler_cutover_db import (ACCEPTANCE_SQL, AUTHORITY_SQL, DISABLE_RECEIPT_SQL, IDENTITY_SQL,
                                                     PROVIDER_OBSERVATION_SQL,
                                                     ReceiptResolver, jobs_dsn)

MIGRATION = (REPO / "migrations" / "0176_legacy_schedule_disable_receipt.sql").read_text(encoding="utf-8").lower()
OBSERVATION_MIGRATION = (REPO / "migrations" / "0180_claude_scheduler_observation_receipt.sql").read_text(encoding="utf-8").lower()
LAUNCHD_MIGRATION = (REPO / "migrations" / "0182_launchd_scheduler_observation_receipt.sql").read_text(encoding="utf-8").lower()
REGISTRY = json.loads((REPO / "ops" / "config" / "control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((REPO / "ops" / "config" / "control-plane-workflows.v1.json").read_text(encoding="utf-8"))
CONTROL_PLANE = load_module_from_path(
    "control_plane_scheduler_db_test", str(REPO / "tools" / "control-plane.py"))
DB_GATE = (REPO / "ops" / "control-plane-db-gate.py").read_text(encoding="utf-8").lower()

FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def refuses(fn: Any) -> bool:
    try:
        fn()
    except CutoverRefusal:
        return True
    return False


class Cursor:
    def __init__(self, conn: "Connection") -> None:
        self.conn = conn
        self.sql = ""

    def __enter__(self) -> "Cursor": return self
    def __exit__(self, *_args: object) -> Literal[False]: return False
    def execute(self, sql: str, params: tuple[str, ...] | None = None) -> None:
        self.sql = sql
        self.conn.calls.append((sql, params))
    def fetchone(self) -> Any:
        if self.sql == IDENTITY_SQL: return self.conn.identity
        if self.sql == AUTHORITY_SQL: return self.conn.authority
        raise AssertionError("unexpected fetchone query")
    def fetchall(self) -> list[tuple[Any, ...]]:
        if self.sql == ACCEPTANCE_SQL: return self.conn.rows
        if self.sql == DISABLE_RECEIPT_SQL: return self.conn.disable_rows
        if self.sql == PROVIDER_OBSERVATION_SQL: return self.conn.observation_rows
        raise AssertionError("unexpected fetchall query")


class Connection:
    def __init__(self, identity: tuple[str, str] = ("carr_jobs", "carr_jobs"),
                 authority: tuple[bool, bool, bool, bool] = (False, False, False, False),
                 rows: list[tuple[Any, ...]] | None = None,
                 disable_rows: list[tuple[Any, ...]] | None = None,
                 observation_rows: list[tuple[Any, ...]] | None = None) -> None:
        self.identity, self.authority = identity, authority
        self.rows = rows or [("nightly-record-layer", 2, "canary", "accepted", "receipt:canary", "joe")]
        self.disable_rows = disable_rows if disable_rows is not None else [("disable:cc", "cc-update-audit", 1,
                                              "cc-update-audit.claude-code.v1", "cc-update-audit",
                                              "accepted after native readback", "joe",
                                              "scheduler-observation:enabled", "scheduler-observation:disabled", None)]
        self.observation_rows = observation_rows if observation_rows is not None else [(
            "scheduler-observation:fixture", "cc-update-audit.claude-code.v1", "cc-update-audit", 1,
            "cc-update-audit", "claude-code", "enabled", "45 9 * * 1", "America/Chicago", "a" * 64,
            "provider-revision-1", "b" * 64, datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc), "joe-mac",
        )]
        self.calls: list[tuple[str, tuple[str, ...] | None]] = []
        self.closed = False
    def cursor(self) -> Cursor: return Cursor(self)
    def close(self) -> None: self.closed = True


def main() -> int:
    check("native disable receipt is append-only and Joe-bound in the migration",
          "legacy_schedule_disable_receipt_append_only" in MIGRATION and "approved_by = 'joe'" in MIGRATION
          and "before update or delete" in MIGRATION)
    check("native receipt binds workflow/version/surface/locator and jobs has SELECT only",
          all(token in MIGRATION for token in ("workflow_version", "surface_id", "locator", "idempotency_key",
                                               "grant select on ops.legacy_schedule_disable_receipt to carr_jobs"))
          and "grant insert on ops.legacy_schedule_disable_receipt to carr_jobs" not in MIGRATION)
    check("verified disable receipts reference exact immutable provider observations",
          all(token in OBSERVATION_MIGRATION for token in (
              "legacy_disable_pre_observation_fk", "legacy_disable_post_observation_fk",
              "pre_observation_ref", "post_observation_ref",
              "pre.scheduler_state<>'enabled'", "post.scheduler_state<>'disabled'")))
    check("authority SQL rejects stale enabled evidence and NULL-unsafe legacy replay",
          "pre.observed_at<now()-interval '15 minutes'" in OBSERVATION_MIGRATION
          and "pre.observed_at>now()+interval '5 minutes'" in OBSERVATION_MIGRATION
          and "existing.pre_observation_ref is distinct from p_pre_observation_ref" in OBSERVATION_MIGRATION
          and "existing.post_observation_ref is distinct from p_post_observation_ref" in OBSERVATION_MIGRATION)
    check("launchd retirement uses the same fresh immutable observation and Joe authority boundary",
          "record_launchd_scheduler_observation" in LAUNCHD_MIGRATION
          and "kind='launchd'" in LAUNCHD_MIGRATION
          and "pre.scheduler_state<>'enabled'" in LAUNCHD_MIGRATION
          and "post.scheduler_state<>'disabled'" in LAUNCHD_MIGRATION)
    rows = scheduler_surface_rows(REGISTRY, manifest=MANIFEST)
    check("database surface registry is populated only by complete exact sync parity",
          "insert into ops.legacy_schedule_surface_registry" not in MIGRATION
          and len(rows) == 20 and len({row[2] for row in rows}) == 20
          and "sync_scheduler_surface_registry" in CONTROL_PLANE.sync_registry.__code__.co_names)
    check("unbound two- and three-argument disable actions are retired and asserted absent",
          "drop function if exists ops.disable_legacy_schedule(text,text)" in MIGRATION
          and "drop function if exists ops.disable_legacy_schedule(text,text,text)" in MIGRATION
          and "to_regprocedure('ops.disable_legacy_schedule(text,text)') is not null" in MIGRATION
          and "to_regprocedure('ops.disable_legacy_schedule(text,text,text)') is not null" in MIGRATION)
    replay_lookup = "select * into existing from ops.legacy_schedule_disable_receipt\n   where idempotency_key=p_idempotency_key;"
    registry_lookup = "select 1 from ops.legacy_schedule_surface_registry"
    check("idempotent replay compares every caller-bound field before current registry lookup", all(token in MIGRATION for token in
          ("existing.workflow_key <> p_workflow_key", "existing.surface_id <> p_surface_id",
           "existing.locator <> p_locator", "existing.reason <> p_reason"))
          and replay_lookup in MIGRATION and registry_lookup in MIGRATION
          and MIGRATION.find(replay_lookup) < MIGRATION.find(registry_lookup))
    check("pruned/retired surface cannot break exact replay but mismatched inputs still refuse",
          "return existing.receipt_ref;\n  end if;\n  select version into v" in MIGRATION
          and "existing.workflow_version <> v" not in MIGRATION
          and MIGRATION.count("idempotency key is bound to different legacy disable evidence") == 2)
    check("resolver authority query checks only the current observation-bound disable function",
          "disable_legacy_schedule(text,text,text,text,text,text,text,text)" in AUTHORITY_SQL
          and "disable_legacy_schedule(text,text)'" not in AUTHORITY_SQL)
    check("owner rebuild gate never creates or impersonates Joe or Dell authority sessions",
          'cur.execute("set session authorization' not in DB_GATE
          and "create role carr_authority_joe" not in DB_GATE
          and "create role carr_authority_dell" not in DB_GATE
          and "grant carr_authority to carr_authority_joe" not in DB_GATE
          and "owner cutover refusal" in DB_GATE)
    good = {"CARR_DB_JOBS_URL": "postgresql://carr_jobs:fixture@example.invalid/carr"}  # ci-secret-scan: allow
    check("jobs resolver requires the jobs URL", refuses(lambda: jobs_dsn({})))
    check("jobs resolver refuses writer URL", refuses(lambda: jobs_dsn({"CARR_DB_JOBS_URL": "postgresql://carr_writer:x@db/carr"})))
    check("jobs resolver preserves only jobs DSN", jobs_dsn(good) == good["CARR_DB_JOBS_URL"])

    conn = Connection()
    resolver = ReceiptResolver(lambda _dsn: conn, jobs_dsn(good))
    receipt = resolver.acceptance_receipt("receipt:canary")
    check("resolver begins read-only before every identity/authority SELECT",
          [call[0] for call in conn.calls[:3]] == ["begin transaction read only", IDENTITY_SQL, AUTHORITY_SQL])
    check("resolver executes only exact allowlisted acceptance query with bound ref",
          conn.calls[-1] == (ACCEPTANCE_SQL, ("receipt:canary",)) and receipt["immutable"] is True)
    authority = resolver.disable_authority_receipt("disable:cc")
    check("distinct native Joe disable receipt is the only typed disable authority receipt",
          authority["authority_subject"] == "joe" and authority["action"] == "disable-legacy-schedule"
          and authority["observation_refs"] == {"pre": "scheduler-observation:enabled",
                                                   "post": "scheduler-observation:disabled", "sibling": None})
    check("disable authority uses a separate exact allowlisted receipt query",
          conn.calls[-1] == (DISABLE_RECEIPT_SQL, ("disable:cc",)))
    observation = resolver.provider_observation_receipt("scheduler-observation:fixture")
    check("provider observation resolves only an immutable device-bound native receipt",
          observation["kind"] == "provider_scheduler_observation_receipt"
          and observation["device_principal_bound"] is True and observation["immutable"] is True)
    check("provider observation uses a separate exact allowlisted receipt query",
          conn.calls[-1] == (PROVIDER_OBSERVATION_SQL, ("scheduler-observation:fixture",)))
    launchd_conn = Connection(observation_rows=[(
        "scheduler-observation:launchd", "nightly-record-layer.launchd.v1", "nightly-record-layer", 2,
        "com.carr.nightly-record-layer", "launchd", "disabled", "c" * 64, "America/Chicago",
        "d" * 64, "e" * 64, "f" * 64, datetime(2026, 8, 16, 18, 1, tzinfo=timezone.utc), "joe-mac",
    )])
    launchd_resolver = ReceiptResolver(lambda _dsn: launchd_conn, jobs_dsn(good))
    launchd_observation = launchd_resolver.scheduler_observation_receipt("scheduler-observation:launchd")
    check("launchd observation resolves through the exact current contract join",
          launchd_observation["scheduler_kind"] == "launchd"
          and launchd_observation["scheduler_state"] == "disabled")
    launchd_resolver.close()
    resolver.close()

    check("writer-capable reader connection is refused", refuses(lambda: ReceiptResolver(
        lambda _dsn: Connection(authority=(True, False, False, False)), jobs_dsn(good))))
    check("writer identity is refused", refuses(lambda: ReceiptResolver(
        lambda _dsn: Connection(identity=("carr_writer", "carr_jobs")), jobs_dsn(good))))
    check("ambiguous receipt reference is refused", refuses(lambda: ReceiptResolver(
        lambda _dsn: Connection(rows=[("a", 1, "shadow", "accepted", "x", "joe"), ("b", 1, "shadow", "accepted", "x", "joe")]), jobs_dsn(good)
    ).acceptance_receipt("x")))
    check("canary acceptance cannot be synthesized into disable authority", refuses(lambda: ReceiptResolver(
        lambda _dsn: Connection(disable_rows=[]), jobs_dsn(good)
    ).disable_authority_receipt("receipt:canary")))
    check("Dell native disable receipt is refused", refuses(lambda: ReceiptResolver(
        lambda _dsn: Connection(disable_rows=[("disable:dell", "cc-update-audit", 1,
            "cc-update-audit.claude-code.v1", "cc-update-audit", "reason", "dell", "pre", "post", None)]), jobs_dsn(good)
    ).disable_authority_receipt("disable:dell")))
    check("duplicate native disable receipt is refused", refuses(lambda: ReceiptResolver(
        lambda _dsn: Connection(disable_rows=[("disable:a", "cc-update-audit", 1, "surface", "loc", "reason", "joe", "pre", "post", None),
            ("disable:b", "cc-update-audit", 1, "surface", "loc", "reason", "joe", "pre", "post", None)]), jobs_dsn(good)
    ).disable_authority_receipt("disable:a")))
    print(f"control-plane scheduler cutover DB selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
