#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only local-PostgreSQL acceptance for the trusted continuity boundary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import uuid

import psycopg

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role
from lib.partner_continuity import evaluate_window, load_contract


def refusal(cur, query: str, params: tuple, label: str) -> None:
    cur.execute("savepoint continuity_refusal")
    try:
        cur.execute(query, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint continuity_refusal")
        return
    cur.execute("rollback to savepoint continuity_refusal")
    raise RuntimeError(f"{label} was accepted")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("partner-continuity-db-gate: DATABASE_URL is required")
    contract = load_contract(REPO / "ops/config/partner-continuity-contract.v1.json")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, "carr_reader", "carr_writer", "carr_device_evidence")
            base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=48)
            receiver_rows: list[tuple[uuid.UUID, str, str, uuid.UUID, datetime, uuid.UUID, datetime]] = []
            # Device rows must exist before receiver evidence and are bound to
            # the same canonical tenant/actor as every subsequent receipt.
            for actor in contract["partners"]:
                cur.execute("insert into ops.partner_continuity_device_principal(login_role,device_id,actor_slug,tenant_id) values (%s,%s,%s,'carr-internal')", (f"fixture_{actor}", f"fixture-{actor}-device", actor))
            for actor in contract["partners"]:
                for stream in contract["streams"]:
                    for index in range(3):
                        origin_id, origin_session, receiver_session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                        observed = base + timedelta(hours=24 * index)
                        proposal = f"proposal:{actor}:{index}" if stream == "conflict_undo" else None
                        event = f"event:{actor}:{index}" if stream == "conflict_undo" else None
                        readback = f"readback:{actor}:{index}" if stream == "tentative_write_readback" else None
                        privacy = f"scan:{actor}:{index}" if stream == "personal_canary_privacy_telemetry" else None
                        digest = "a" * 64 if stream == "document_download" else None
                        cur.execute("""insert into ops.partner_continuity_origin
                          (id,tenant_id,actor_slug,stream,session_id,observed_at,governed_origin_ref,proposal_ref,event_ref,readback_ref,privacy_telemetry_ref,fetched_bytes_sha256,idempotency_key)
                          values (%s,'carr-internal',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                          (origin_id, actor, stream, origin_session, observed, f"governed:{actor}:{stream}:{index}", proposal, event, readback, privacy, digest, f"origin:{origin_id}"))
                        receiver_rows.append((origin_id, actor, stream, origin_session, observed, receiver_session, observed + timedelta(minutes=1)))
            for origin_id, actor, _stream, _origin_session, _observed, receiver_session, receiver_at in receiver_rows:
                cur.execute("""insert into ops.partner_continuity_receiver_evidence
                  (origin_id,tenant_id,actor_slug,device_id,session_id,observed_at,idempotency_key)
                  values (%s,'carr-internal',%s,%s,%s,%s,%s)""",
                  (origin_id, actor, f"fixture-{actor}-device", receiver_session, receiver_at, f"receiver:{origin_id}"))
            before = cur.execute("select count(*) from ops.partner_continuity_origin").fetchone()[0]
            set_local_role(cur, "carr_reader")
            cur.execute("select set_config('carr.continuity_tenant','carr-internal',true)")
            rows = cur.execute("select * from ops.partner_continuity_evidence_window()").fetchall()
            result = evaluate_window(contract, rows)
            if result["status"] != "CONTINUITY_PROVEN":
                raise RuntimeError("reader acceptance did not reduce only pre-existing evidence")
            if cur.execute("select ops.partner_continuity_drive_retirement_status()").fetchone() != ("READY_FOR_JOE_APPROVAL",):
                raise RuntimeError("missing Joe Drive retirement approval did not fail closed")
            if cur.execute("select has_table_privilege(current_user,'ops.partner_continuity_origin','select')").fetchone() != (False,):
                raise RuntimeError("reader holds direct origin-table read authority")
            cur.execute("select set_config('carr.continuity_tenant','other',true)")
            refusal(cur, "select * from ops.partner_continuity_evidence_window()", (), "noncanonical tenant read")
            cur.execute("reset role")
            after = cur.execute("select count(*) from ops.partner_continuity_origin").fetchone()[0]
            if before != after:
                raise RuntimeError("reader acceptance minted or rewrote substantive evidence")
    except Exception as exc:
        raise SystemExit(f"partner-continuity-db-gate: FAIL — {exc}") from exc
    print("partner-continuity-db-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
