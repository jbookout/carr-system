#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-PostgreSQL acceptance for the V5-F09 workflow census store
(migration 0595: ops.workflow_census_record, its write door
ops.record_workflow_census and its read door ops.read_workflow_census).

WHY THIS EXISTS. The node suite (mcp-server/test/workflow-census.test.mjs)
drives the verbs against a JS fake, and the Python suite
(ops/workflow-census-attestation-selftest.py) drives the verifier against chains
it builds itself. Neither can prove the one property the whole design rests on:
that the hashes the DATABASE computes are the hashes the READER recomputes. This
gate records real rows through the real door and hands the real read-door answer
to lib/workflow_census_attestation.verify_census_chain. It also proves:

  1. The principal comes from carr.acting_actor_slug, never a parameter, and a
     session that never set it (the break-glass shape) is refused by name.
  2. No app role can touch the table directly.
  3. The table is append-only for everyone, the owner included, and the chain
     guard refuses an owner-level INSERT that splices, back-dates or forges a
     digest -- the negative paths at the storage layer.
  4. Fractional numbers and malformed census shapes are refused at the door;
     an idempotent replay returns the same row, and key reuse is refused.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role  # noqa: E402
from lib.workflow_census_attestation import load_config, verify_census_chain  # noqa: E402

WRITER = "workflow-census-gate"
# Escapes, a BMP character above U+E000 and an astral one, keys whose code-point
# order differs from their UTF-16 order: every place the two canonical
# renderings could disagree, so the cross-language check below means something.
CENSUS: dict[str, Any] = {
    "schema_version": "control-plane-workflow-truth.v1",
    "rows": [{"workflow_key": "gate-flow", "workflow_version": 1, "state": "enabled_shadow_only",
              "note": "é ☃ 日本 \n\t\"q\" \\ \u0001   /"}],
    "summary": {"workflows": 1, "ｚ": 1, "𝄞": 2, "z": None, "big": 12345678901234},
}


def fail(message: str) -> int:
    print(f"workflow-census-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def record(cur: psycopg.Cursor[Any], payload: Any = None, key: str | None = None) -> tuple[Any, ...]:
    row = cur.execute(
        "select seq, recorded_at, principal, row_hash, prev_hash, payload_sha256, replayed "
        "from ops.record_workflow_census(%s, %s)",
        (Jsonb(CENSUS if payload is None else payload), key or str(uuid.uuid4())),
    ).fetchone()
    if row is None:
        raise RuntimeError("record_workflow_census returned no row")
    return row


def read(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    row = cur.execute("select ops.read_workflow_census(%s)", (20000,)).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise RuntimeError("read_workflow_census returned no object")
    return row[0]


def expect_refusal(cur: psycopg.Cursor[Any], fn: Callable[[], object],
                   errors: tuple[type[BaseException], ...], expected: str, label: str) -> None:
    cur.execute(f"savepoint {label}")
    try:
        fn()
    except errors as exc:
        cur.execute(f"rollback to savepoint {label}")
        if expected not in str(exc):
            raise RuntimeError(f"{label} refused with the wrong reason: {exc}") from exc
        return
    cur.execute(f"rollback to savepoint {label}")
    raise RuntimeError(f"{label} was accepted; expected a refusal")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, "carr_writer", "carr_reader")
            session_role = cur.execute("select session_user::text").fetchone()[0]
            prior = cur.execute("select count(*) from ops.workflow_census_record").fetchone()[0]
            if prior:
                return fail(f"the disposable database already holds {prior} census rows")

            # 1. No actor setting: the break-glass shape is refused by name.
            set_local_role(cur, "carr_writer")
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_principal_unavailable", "no_principal")

            # 1b. The Worker shape: the actor setting, then the door.
            cur.execute("select set_config('carr.acting_actor_slug', %s, true)", (WRITER,))
            first = record(cur)
            key = str(uuid.uuid4())
            second = record(cur, key=key)
            replay = record(cur, key=key)
            if first[0] != 1 or second[0] != 2 or first[4] is not None or second[4] != first[3]:
                raise RuntimeError(f"chain fields are not seq 1..2 linked by prev_hash: {first} {second}")
            if first[2] != WRITER or replay[6] is not True or replay[3] != second[3]:
                raise RuntimeError(f"principal or idempotent replay misbehaved: {first} {replay}")
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "rows": []}, key=key),
                           (psycopg.errors.RaiseException,), "workflow_census_key_reuse", "key_reuse")
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "summary": {"ratio": 0.5}}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_fraction_refused",
                           "fraction")
            expect_refusal(cur, lambda: record(cur, payload={"schema_version": "other.v1"}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_shape_refused",
                           "shape")
            cur.execute("reset role")

            # THE CROSS-LANGUAGE PROOF: the database's hashes verify in Python.
            set_local_role(cur, "carr_reader")
            answer = read(cur)
            cur.execute("reset role")
            answer["ok"] = True
            config = load_config({
                "schema_version": "workflow-census-attestation.v1",
                "writer_principals": [WRITER],
                "writer_db_session_principals": [session_role],
                "writer_cadence_seconds": 86400, "freshness_window_seconds": 93600,
                "max_chain_rows": 20000})
            verdict = verify_census_chain(answer, config)
            if verdict.get("available") is not True:
                raise RuntimeError(f"the reader could not recompute the database's chain: {verdict}")
            if verdict["attestation"]["seq"] != 2 or verdict["census"] != CENSUS:
                raise RuntimeError(f"the reader attested the wrong row: {verdict['attestation']}")
            if any(row["db_session_principal"] != session_role for row in answer["chain"]):
                raise RuntimeError("db_session_principal is not the session login role")
            stranger = load_config({**{
                "schema_version": "workflow-census-attestation.v1",
                "writer_principals": ["someone-else"],
                "writer_db_session_principals": [session_role],
                "writer_cadence_seconds": 86400, "freshness_window_seconds": 93600,
                "max_chain_rows": 20000}})
            if verify_census_chain(answer, stranger).get("reason") != "unknown_writer":
                raise RuntimeError("a real row from an unlisted writer was not refused as unknown_writer")

            # 2. No direct table privilege for any app role.
            for role in ("carr_writer", "carr_reader"):
                set_local_role(cur, role)
                for label, statement in (
                    ("select", "select count(*) from ops.workflow_census_record"),
                    ("update", "update ops.workflow_census_record set principal = 'x'"),
                    ("delete", "delete from ops.workflow_census_record"),
                ):
                    def direct(statement: str = statement) -> object:
                        return cur.execute(statement)
                    expect_refusal(cur, direct, (psycopg.errors.InsufficientPrivilege,),
                                   "permission denied", f"{role}_direct_{label}")
                cur.execute("reset role")
            set_local_role(cur, "carr_reader")
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.InsufficientPrivilege,),
                           "permission denied", "reader_cannot_write")
            cur.execute("reset role")

            # 3. Append-only and chain-guarded for the owner too.
            expect_refusal(cur, lambda: cur.execute(
                "update ops.workflow_census_record set payload = '{}'::jsonb where seq = 1"),
                (psycopg.errors.RaiseException,), "append-only", "owner_edit_refused")
            expect_refusal(cur, lambda: cur.execute(
                "delete from ops.workflow_census_record where seq = 2"),
                (psycopg.errors.RaiseException,), "append-only", "owner_delete_refused")
            head = cur.execute(
                "select row_hash, recorded_at from ops.workflow_census_record order by seq desc limit 1"
            ).fetchone()

            def owner_insert(seq: int, prev: str | None, at: str, payload_sha: str | None = None,
                             row_hash: str | None = None) -> object:
                return cur.execute(
                    """insert into ops.workflow_census_record
                         (seq, recorded_at, principal, db_session_principal, prev_hash,
                          payload_sha256, row_hash, payload, idempotency_key)
                       select %(seq)s, %(at)s::timestamptz, %(p)s, session_user, %(prev)s,
                              coalesce(%(ps)s, ops.workflow_census_payload_sha256(%(payload)s)),
                              coalesce(%(rh)s, ops.workflow_census_row_hash(%(seq)s, %(at)s::timestamptz,
                                %(p)s, session_user::text, %(prev)s,
                                ops.workflow_census_payload_sha256(%(payload)s))),
                              %(payload)s, %(key)s""",
                    {"seq": seq, "prev": prev, "at": at, "p": WRITER, "payload": Jsonb(CENSUS),
                     "ps": payload_sha, "rh": row_hash, "key": str(uuid.uuid4())})

            now_text = head[1].isoformat()
            expect_refusal(cur, lambda: owner_insert(5, head[0], now_text),
                           (psycopg.errors.RaiseException,), "workflow_census_chain_splice_refused",
                           "owner_seq_gap")
            expect_refusal(cur, lambda: owner_insert(3, "0" * 64, now_text),
                           (psycopg.errors.RaiseException,), "workflow_census_chain_splice_refused",
                           "owner_wrong_prev")
            expect_refusal(cur, lambda: owner_insert(3, head[0], "2020-01-01T00:00:00Z"),
                           (psycopg.errors.RaiseException,), "workflow_census_time_regression_refused",
                           "owner_backdated")
            expect_refusal(cur, lambda: owner_insert(3, head[0], now_text, payload_sha="a" * 64),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_digest_mismatch",
                           "owner_forged_payload_digest")
            expect_refusal(cur, lambda: owner_insert(3, head[0], now_text, row_hash="b" * 64),
                           (psycopg.errors.RaiseException,), "workflow_census_row_hash_mismatch",
                           "owner_forged_row_hash")

        print("PASS: workflow-census real-PostgreSQL chain recomputed by the Python reader, "
              "principal from the actor setting only, no direct grants, append-only, and the "
              "chain guard refuses splice, back-date and forged digests")
        return 0
    except Exception as exc:  # noqa: BLE001 - gate contract is a printed failure, not a traceback
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
