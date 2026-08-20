#!/usr/bin/env python3
"""Hermetic contract checks for the Phase 4 receipt predecessor."""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "0197_phase4_receipt_foundation.sql"
STREAMS = {
    "standing_context",
    "governed_retrieval",
    "tentative_write_readback",
    "conflict_undo",
    "personal_canary_privacy_model_telemetry",
    "document_download",
}
SOURCE_FUNCTIONS = (
    "record_phase4_standing_context",
    "record_phase4_governed_retrieval",
    "record_phase4_tentative_write_readback",
    "record_phase4_conflict_undo",
    "record_phase4_privacy_scan",
    "record_phase4_document_download",
)


def function_signature(sql: str, name: str) -> str:
    marker = f"create or replace function ops.{name}("
    start = sql.find(marker)
    if start < 0:
        return ""
    end = sql.find(") returns", start)
    return sql[start:end] if end >= 0 else ""


def findings(sql: str) -> list[str]:
    compact = " ".join(sql.lower().split())
    errors: list[str] = []
    for stream in STREAMS:
        if f"'{stream}'" not in sql:
            errors.append(f"missing typed stream {stream}")
    for name in SOURCE_FUNCTIONS:
        signature = function_signature(sql, name)
        if not signature:
            errors.append(f"missing source function {name}")
            continue
        if any(term in signature for term in ("status", "timestamp", "sha256", "json", "actor", "tenant", "session")):
            errors.append(f"{name} accepts caller substantive evidence")
    if sql.count("perform ops.phase4_lock_idempotency('source',p_idempotency_key);") != 6:
        errors.append("all six source writers must serialize one shared idempotency namespace")
    for scope in ("receiver", "drive-evidence", "drive-retirement"):
        if f"perform ops.phase4_lock_idempotency('{scope}',p_idempotency_key);" not in sql:
            errors.append(f"missing atomic idempotency lock for {scope}")
    receiver = function_signature(sql, "receive_phase4_source_receipt")
    if "p_source_receipt_id uuid,p_idempotency_key text" not in receiver:
        errors.append("receiver accepts more than a pre-existing receipt and idempotency key")
    required = (
        "references tool_read_call(id)",
        "references retrieval_query_log(id)",
        "phase4_tool_read_call_id uuid references tool_read_call(id)",
        "query_row.phase4_tool_read_call_id is distinct from call_row.id",
        "query_row.phase4_actor_slug is distinct from actor_slug",
        "query_row.phase4_tenant_id is distinct from 'carr-internal'",
        "references tool_call(idempotency_key)",
        "references event(id)",
        "references retrieval_proposal(id)",
        "references ops.job_receipt(id)",
        "references attachment(id)",
        "where login_role=session_user and active",
        "source.actor_slug=binding.actor_slug",
        "received_at timestamptz not null default now()",
        "where login_role=session_user",
        "where s.tenant_id=tenant and r.tenant_id=tenant",
        "authority_actor_slug()<>'joe'",
        "references ops.phase4_drive_evidence_receipt(id,evidence_kind)",
        "job_row.definition_key like '%scheduler%'",
        "sole_required_system_authority text not null check (sole_required_system_authority='joe')",
        "dell_participation text not null check (dell_participation='optional_nonblocking')",
        "continuity_may_gate_system_rollout boolean not null check (not continuity_may_gate_system_rollout)",
        "continuity_may_gate_system_activation boolean not null check (not continuity_may_gate_system_activation)",
    )
    for phrase in required:
        if phrase not in compact:
            errors.append(f"missing fixed binding: {phrase}")
    if "grant execute on function ops.receive_phase4_source_receipt(uuid,text) to carr_device_evidence" not in compact:
        errors.append("device receiver grant is not narrow")
    if "record_phase4_standing_context(uuid,text)'::regprocedure" not in compact:
        errors.append("device source-mint refusal is not asserted")
    if "phase4_receipt_rows()" not in compact or "to carr_reader,carr_jobs" not in compact:
        errors.append("fixed jobs/reader projection is missing")
    if any(term in compact for term in ("continuity_proven", "phase4_accepted", "minimum_overlap")):
        errors.append("foundation claims reducer/acceptance state")
    retirement_start = sql.find("create or replace function ops.approve_phase4_drive_retirement(")
    retirement_end = sql.find("create or replace function ops.phase4_receipt_rows()", retirement_start)
    retirement = sql[retirement_start:retirement_end]
    authority_at = retirement.find("authority_actor_slug()<>'joe'")
    replay_at = retirement.find("from ops.phase4_drive_retirement_authority_receipt where idempotency_key")
    if authority_at < 0 or replay_at < 0 or authority_at > replay_at:
        errors.append("Joe authority must be established before retirement replay lookup")
    return errors


def check(label: str, ok: bool) -> None:
    global passed
    if not ok:
        failures.append(label)
    else:
        passed += 1


sql = MIGRATION.read_text(encoding="utf-8").lower()
passed = 0
failures: list[str] = []
check("real migration satisfies receipt contract", findings(sql) == [])

mutations = {
    "caller timestamp is refused": sql.replace(
        "p_tool_read_call_id uuid,p_idempotency_key text",
        "p_tool_read_call_id uuid,p_observed_at timestamptz,p_idempotency_key text",
        1,
    ),
    "device source mint is refused": sql.replace(
        "record_phase4_standing_context(uuid,text)'::regprocedure", "unrelated(uuid,text)'::regprocedure", 1
    ),
    "tenant filter loss is refused": sql.replace(
        "where s.tenant_id=tenant and r.tenant_id=tenant", "where true", 1
    ),
    "scheduler exclusion loss is refused": sql.replace(
        "or job_row.definition_key like '%scheduler%'", "", 1
    ),
    "Joe authority loss is refused": sql.replace(
        "if ops.authority_actor_slug()<>'joe' then", "if false then", 1
    ),
    "reducer claim is refused": sql + "\n-- CONTINUITY_PROVEN\n",
    "Dell-required authority is refused": sql.replace(
        "dell_participation='optional_nonblocking'", "dell_participation='required'", 1
    ),
    "continuity rollout gate is refused": sql.replace(
        "check (not continuity_may_gate_system_rollout)",
        "check (continuity_may_gate_system_rollout)",
        1,
    ),
    "unbound retrieval result is refused": sql.replace(
        "query_row.phase4_tool_read_call_id is distinct from call_row.id",
        "false",
        1,
    ),
    "unserialized source replay is refused": sql.replace(
        "perform ops.phase4_lock_idempotency('source',p_idempotency_key);", "", 1
    ),
    "authority-after-replay is refused": sql.replace(
        "if ops.authority_actor_slug()<>'joe' then raise exception 'phase 4 whole-drive retirement requires joe authority'; end if;\n  perform ops.phase4_lock_idempotency('drive-retirement',p_idempotency_key);",
        "perform ops.phase4_lock_idempotency('drive-retirement',p_idempotency_key);",
        1,
    ),
}
for label, mutation in mutations.items():
    check(label, bool(findings(mutation)))

print(f"phase4 receipt foundation selftest — {passed}/{passed + len(failures)} passed")
if failures:
    print("FAILED: " + "; ".join(failures))
    raise SystemExit(1)
