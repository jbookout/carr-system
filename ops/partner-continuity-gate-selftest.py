#!/usr/bin/env python3
"""Hermetic refusal coverage for the Phase 4 fixed-query continuity gate."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.partner_continuity import ContinuityRefusal, evaluate_window, load_contract
from lib.partner_continuity_db import (AUTHORITY_SQL, DRIVE_SQL, EVIDENCE_SQL, IDENTITY_SQL,
                                       TENANT_SQL, ContinuityResolver, continuity_dsn)
from lib.loadpy import load_module_from_path

gate = load_module_from_path("partner_continuity_gate", str(REPO / "ops/partner-continuity-gate.py"))
CONTRACT = load_contract(REPO / "ops/config/partner-continuity-contract.v1.json")
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def refuses(fn: Any) -> bool:
    try:
        fn()
    except ContinuityRefusal:
        return True
    return False


def rows() -> list[tuple[object, ...]]:
    base = datetime(2026, 8, 17, 12, tzinfo=timezone.utc)
    result: list[tuple[object, ...]] = []
    for actor in CONTRACT["partners"]:
        for stream in CONTRACT["streams"]:
            for index in range(3):
                instant = base + timedelta(hours=24 * index)
                result.append((actor, stream, f"{actor}-{stream}-origin-{index}", instant,
                               f"{actor}-{stream}-receiver-{index}", instant + timedelta(minutes=1),
                               1, CONTRACT["contract_digest"]))
    return result


class Cursor:
    def __init__(self, conn: "Connection") -> None:
        self.conn = conn
        self.sql = ""
    def __enter__(self) -> "Cursor": return self
    def __exit__(self, *_args: object) -> bool: return False
    def execute(self, sql: str, _params: object = None) -> None:
        self.sql = sql
        self.conn.calls.append(sql)
    def fetchone(self) -> Any:
        if self.sql == IDENTITY_SQL: return self.conn.identity
        if self.sql == AUTHORITY_SQL: return self.conn.authority
        raise AssertionError(f"unexpected scalar query {self.sql!r}")
    def fetchall(self) -> list[tuple[object, ...]]:
        if self.sql == EVIDENCE_SQL: return self.conn.evidence
        if self.sql == DRIVE_SQL: return [(self.conn.drive,)]
        raise AssertionError(f"unexpected rows query {self.sql!r}")


class Connection:
    def __init__(self, *, identity: tuple[str, str] = ("carr_reader", "carr_reader"),
                 authority: tuple[bool, bool, bool] = (False, False, False),
                 evidence: list[tuple[object, ...]] | None = None, drive: str = "READY_FOR_JOE_APPROVAL") -> None:
        self.identity, self.authority = identity, authority
        self.evidence, self.drive = rows() if evidence is None else evidence, drive
        self.calls: list[str] = []
        self.closed = False
    def cursor(self) -> Cursor: return Cursor(self)
    def close(self) -> None: self.closed = True


def main() -> int:
    good = rows()
    result = evaluate_window(CONTRACT, good)
    check("immutable config digest validates exact canonical contract", CONTRACT["contract_version"] == 1)
    check("all ten streams prove one real 48-hour common window", result["status"] == "CONTINUITY_PROVEN"
          and result["common_window"]["overlap_seconds"] == 172800 and result["stream_count"] == 10)
    check("missing stream refuses", refuses(lambda: evaluate_window(CONTRACT, good[:-3])))
    stale_gap = list(good); stale_gap[2] = (*stale_gap[2][:3], datetime(2026, 8, 22, tzinfo=timezone.utc), *stale_gap[2][4:])
    check("cadence gap refuses", refuses(lambda: evaluate_window(CONTRACT, stale_gap)))
    same_session = list(good); same_session[0] = (same_session[0][0], same_session[0][1], "same", same_session[0][3], "same", same_session[0][5], 1, CONTRACT["contract_digest"])
    check("origin and receiver cannot share a session", refuses(lambda: evaluate_window(CONTRACT, same_session)))
    forged = list(good); forged[0] = (*forged[0][:-1], "0" * 64)
    check("contract digest mismatch refuses", refuses(lambda: evaluate_window(CONTRACT, forged)))
    check("caller evidence JSON route is removed", "validate_evidence(" not in (REPO / "ops/partner-continuity-gate.py").read_text())
    check("no positional evidence input is accepted", subprocess.run(
        [sys.executable, str(REPO / "ops/partner-continuity-gate.py"), "forged.json"], capture_output=True, text=True
    ).returncode == 2)
    check("continuity reader requires its dedicated reader URL", continuity_dsn({"CARR_DB_CONTINUITY_URL": "postgresql://carr_reader:x@example.invalid/carr"}).startswith("postgresql://carr_reader"))  # ci-secret-scan: allow
    check("writer URL is refused", refuses(lambda: continuity_dsn({"CARR_DB_CONTINUITY_URL": "postgresql://carr_writer:x@example.invalid/carr"})))  # ci-secret-scan: allow
    conn = Connection()
    resolver = ContinuityResolver(lambda _dsn: conn, continuity_dsn({"CARR_DB_CONTINUITY_URL": "postgresql://carr_reader:x@example.invalid/carr"}))  # ci-secret-scan: allow
    check("resolver begins read-only and sets canonical tenant before fixed reads", conn.calls[:4] == ["begin transaction read only", IDENTITY_SQL, AUTHORITY_SQL, TENANT_SQL])
    check("resolver executes only fixed evidence and Drive status projections", resolver.evidence_rows() == good and resolver.drive_status() == "READY_FOR_JOE_APPROVAL" and conn.calls[-2:] == [EVIDENCE_SQL, DRIVE_SQL])
    check("writer-capable resolver refuses", refuses(lambda: ContinuityResolver(lambda _dsn: Connection(authority=(True, False, False)), "fixture")))
    check("non-reader identity refuses", refuses(lambda: ContinuityResolver(lambda _dsn: Connection(identity=("carr_jobs", "carr_reader")), "fixture")))
    resolver.close()
    source = (REPO / "migrations/0196_partner_continuity_trusted_boundary.sql").read_text(encoding="utf-8").lower()
    check("migration stores canonical tenant/domain and partner bindings", all(token in source for token in ("canonical_domain='carr.us'", "('joe','carr-internal','carr.us')", "('dell','carr-internal','carr.us')")))
    check("receiver only references pre-existing same-tenant origin and cannot mint it", "select * into origin from ops.partner_continuity_origin where id=p_origin_id" in source and "insert into ops.partner_continuity_origin" not in source[source.find("record_partner_continuity_receiver_evidence"):source.find("record_partner_continuity_drive_retirement")])
    check("conflict/undo and document byte hash have schema-enforced exact evidence", "stream='conflict_undo'" in source and "fetched_bytes_sha256 ~ '^[0-9a-f]{64}$'" in source)
    check("Drive retirement remains Joe-only and forbids scheduler receipt reuse", "ops.authority_actor_slug() <> 'joe'" in source and "lower(approval_ref) not like '%scheduler%'" in source)
    print(f"partner continuity gate selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
