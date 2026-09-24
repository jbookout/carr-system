#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-PostgreSQL acceptance for the V5-F09 workflow census store
(migration 0595: ops.workflow_census_record, its write door
ops.record_workflow_census and its read door ops.read_workflow_census).

WHY THIS EXISTS. The node suite (mcp-server/test/workflow-census.test.mjs)
drives the verbs against a JS fake, and the Python suite
(ops/workflow-census-attestation-selftest.py) drives the verifier against chains
it builds itself. Neither can prove what the database does. This gate records
real rows through the real door and hands the real read-door answer to
lib/workflow_census_attestation.verify_census_chain.

WHO IT IS WHILE IT CHECKS. The writer and the reader here are NOT the
superuser the gate connects as. It creates two throwaway LOGIN roles inside its
own transaction -- one a member of carr_writer, one a member of carr_reader --
and becomes each with SET SESSION AUTHORIZATION, which changes session_user
exactly as logging in as that role would (and so what the door records as
db_session_principal, and what the chain guard compares against). A second
connection is not used because a role created in this rolled-back transaction
does not exist for any other connection. The superuser identity is used only
for the database-owner attacks, which are the point of those checks.

WHAT IT PROVES, each by a refusal or a verdict on real rows:
  1. The database's row hash equals the reader's on two pinned vectors and on
     real rows with unicode, astral characters and escapes.
  2. The door takes its principal from carr.acting_actor_slug only: none, or a
     value that is not an actor slug, is refused by name.
  3. The door refuses key reuse (another payload, or the same payload under
     another principal), a fractional number, a wrong shape and a payload over
     the 4 MiB cap; an idempotent replay returns the same row.
  4. The non-owner writer login cannot touch the table directly, and cannot
     forge identity through it.
  5. The chain guard refuses an OWNER insert that forges principal or login
     role, splices, back-dates, stamps the future, or forges a digest -- with
     every trigger on.
  6. All three triggers are ENABLE ALWAYS, the read door reports that, and a
     disable followed by a plain ENABLE (state 'O') reads as tampered.
  7. The reviewer's wholesale rewrite (disable the triggers, delete, re-insert
     a clean chain, re-enable ALWAYS) verifies on its own and reads as tampered
     against the anchor the Worker would have recorded.
  8. Freshness is measured on the DATABASE clock: a genesis row 27 hours old by
     that clock is stale, and read with max_rows smaller than the chain the
     answer says truncated.
"""

from __future__ import annotations

import copy
import functools
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gate_runtime_role import rollback_only_connection  # noqa: E402
from lib.workflow_census_attestation import load_config, row_hash, verify_census_chain  # noqa: E402

WRITER = "workflow-census-gate"
WRITER_LOGIN = "workflow_census_gate_writer"
READER_LOGIN = "workflow_census_gate_reader"
# Escapes, a BMP character above U+E000 and an astral one, keys whose code-point
# order differs from their UTF-16 order: every place the two canonical
# renderings could disagree, so the cross-language check below means something.
CENSUS: dict[str, Any] = {
    "schema_version": "control-plane-workflow-truth.v1",
    "rows": [{"workflow_key": "gate-flow", "workflow_version": 1, "state": "enabled_shadow_only",
              "note": "é ☃ 日本 \n\t\"q\" \\ \u0001   /"}],
    "summary": {"workflows": 1, "ｚ": 1, "𝄞": 2, "z": None, "big": 12345678901234},
}
FORGED = {"schema_version": "control-plane-workflow-truth.v1",
          "rows": [{"workflow_key": "forged-all-clear"}], "summary": {"workflows": 99}}
# The same two vectors ops/workflow-census-attestation-selftest.py pins.
VECTOR_ROW = {"seq": 7, "recorded_at": "2026-09-24T04:10:00.123456Z", "principal": "joe-local",
              "db_session_principal": "carr_writer", "prev_hash": "a" * 64,
              "payload_sha256": "b" * 64}
VECTORS = ((VECTOR_ROW, "d06373a7bf4840bfbc2fab8bae4caca4e2cd42cf289c116174460af02864186f"),
           ({**VECTOR_ROW, "seq": 1, "prev_hash": None},
            "c557fc0fa00e3c53b091055baa3eb469fc015e72f38e1b36a5e36ffae3f5be9d"))
GUARDS = ("workflow_census_record_append_only", "workflow_census_record_chain_guard",
          "workflow_census_record_no_truncate")


def fail(message: str) -> int:
    print(f"workflow-census-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def config(*principals: str, sessions: tuple[str, ...] = (WRITER_LOGIN,)) -> dict[str, Any]:
    return load_config({
        "schema_version": "workflow-census-attestation.v1",
        "writer_principals": list(principals or (WRITER,)),
        "writer_db_session_principals": list(sessions),
        "writer_cadence_seconds": 86400, "freshness_window_seconds": 93600,
        "max_chain_rows": 20000})


def become(cur: psycopg.Cursor[Any], login: str | None) -> None:
    """Take a login role's session identity (None: back to the superuser)."""
    cur.execute("reset session authorization" if login is None
                else f"set session authorization {login}")
    who = cur.execute("select session_user::text, current_user::text").fetchone()
    if who is None:
        raise RuntimeError("session identity was not returned")
    expected = login or who[0]
    if who[0] != expected or who[1] != expected:
        raise RuntimeError(f"session identity is {who}, expected {expected}")


def actor(cur: psycopg.Cursor[Any], slug: str | None) -> None:
    cur.execute("select set_config('carr.acting_actor_slug', %s, true)", (slug or "",))


def record(cur: psycopg.Cursor[Any], payload: Any = None, key: str | None = None) -> tuple[Any, ...]:
    row = cur.execute(
        "select seq, recorded_at, principal, row_hash, prev_hash, payload_sha256, replayed "
        "from ops.record_workflow_census(%s, %s)",
        (Jsonb(CENSUS if payload is None else payload), key or str(uuid.uuid4())),
    ).fetchone()
    if row is None:
        raise RuntimeError("record_workflow_census returned no row")
    return row


def read(cur: psycopg.Cursor[Any], max_rows: int = 20000) -> dict[str, Any]:
    row = cur.execute("select ops.read_workflow_census(%s)", (max_rows,)).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise RuntimeError("read_workflow_census returned no object")
    return row[0]


def served(answer: dict[str, Any], anchor: dict[str, Any] | None = None) -> dict[str, Any]:
    """The verb's envelope around the read door's answer. The anchor is what the
    Worker's Durable Object would hold: by default the head it committed."""
    out = copy.deepcopy(answer)
    out["ok"] = True
    head = out["chain"][-1] if out["chain"] else None
    out["anchor"] = anchor if anchor is not None else (
        {"state": "present", "seq": head["seq"], "row_hash": head["row_hash"],
         "anchored_at": head["recorded_at"]} if head else {"state": "absent"})
    return out


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


def owner_insert(cur: psycopg.Cursor[Any], seq: int, prev: str | None, at_sql: str, *,
                 principal: str | None = None, session: str | None = None,
                 payload: Any = None, payload_sha: str | None = None,
                 forged_row_hash: str | None = None) -> object:
    """An owner-level INSERT that skips the door, hashed genuinely unless told not to.
    principal/session default to the session's own (what the guard demands)."""
    return cur.execute(
        f"""with v as (
              select %(seq)s::bigint as seq, ({at_sql})::timestamptz as at,
                     coalesce(%(p)s, current_setting('carr.acting_actor_slug', true)) as p,
                     coalesce(%(s)s, session_user::text) as s, %(prev)s::text as prev,
                     %(payload)s::jsonb as payload)
            insert into ops.workflow_census_record
              (seq, recorded_at, principal, db_session_principal, prev_hash,
               payload_sha256, row_hash, payload, idempotency_key)
            select v.seq, v.at, v.p, v.s, v.prev,
                   coalesce(%(ps)s, ops.workflow_census_payload_sha256(v.payload)),
                   coalesce(%(rh)s, ops.workflow_census_row_hash(v.seq, v.at, v.p, v.s, v.prev,
                     coalesce(%(ps)s, ops.workflow_census_payload_sha256(v.payload)))),
                   v.payload, %(key)s
              from v""",
        {"seq": seq, "prev": prev, "p": principal, "s": session,
         "payload": Jsonb(FORGED if payload is None else payload),
         "ps": payload_sha, "rh": forged_row_hash, "key": str(uuid.uuid4())})


def head(cur: psycopg.Cursor[Any]) -> tuple[int, str]:
    row = cur.execute("select seq, row_hash from ops.workflow_census_record "
                      "order by seq desc limit 1").fetchone()
    if row is None:
        raise RuntimeError("no head row")
    return row[0], row[1]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            if not cur.execute("select rolsuper from pg_roles where rolname = session_user"
                               ).fetchone()[0]:
                return fail("the gate needs a superuser session to take login identities")
            prior = cur.execute("select count(*) from ops.workflow_census_record").fetchone()[0]
            if prior:
                return fail(f"the disposable database already holds {prior} census rows")
            cur.execute(f"create role {WRITER_LOGIN} login in role carr_writer")
            cur.execute(f"create role {READER_LOGIN} login in role carr_reader")

            # 1. Pinned vectors: the database's rule is the reader's rule.
            for vector, digest in VECTORS:
                got = cur.execute(
                    "select ops.workflow_census_row_hash(%s, %s::timestamptz, %s, %s, %s, %s)",
                    (vector["seq"], vector["recorded_at"], vector["principal"],
                     vector["db_session_principal"], vector["prev_hash"],
                     vector["payload_sha256"])).fetchone()[0]
                if got != digest or row_hash(vector) != digest:
                    raise RuntimeError(f"row-hash vector disagrees: db {got}, pinned {digest}")

            # 6a. All three guards ENABLE ALWAYS.
            states = dict(cur.execute(
                "select tgname::text, tgenabled::text from pg_trigger "
                "where tgrelid = 'ops.workflow_census_record'::regclass and not tgisinternal"
            ).fetchall())
            if states != {name: "A" for name in GUARDS}:
                raise RuntimeError(f"guards are not exactly the three ENABLE ALWAYS triggers: {states}")

            # 5a. An empty store admits only a true genesis, even from the owner.
            actor(cur, WRITER)
            expect_refusal(cur, lambda: owner_insert(cur, 5, "0" * 64, "clock_timestamp()"),
                           (psycopg.errors.RaiseException,), "workflow_census_chain_splice_refused",
                           "owner_orphan_genesis")

            # 8a. Freshness on the DATABASE clock: a genesis 27h old by that clock.
            cur.execute("savepoint stale_genesis")
            owner_insert(cur, 1, None, "clock_timestamp() - interval '27 hours'", payload=CENSUS)
            owner = cur.execute("select session_user::text").fetchone()[0]
            verdict = verify_census_chain(served(read(cur)), config(sessions=(owner,)))
            if verdict.get("reason") != "stale" or not 27 * 3600 <= verdict.get("age_seconds", 0) < 27 * 3600 + 600:
                raise RuntimeError(f"a genesis 27h old by the database clock was not stale: {verdict}")
            cur.execute("rollback to savepoint stale_genesis")

            # 2. As the writer LOGIN: no actor setting, or a non-slug, is refused.
            become(cur, WRITER_LOGIN)
            actor(cur, None)
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_principal_unavailable", "no_principal")
            actor(cur, "Not A Slug!")
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.RaiseException,),
                           "workflow_census_principal_unavailable", "bad_principal")

            # 3. The Worker shape: the actor setting, then the door.
            actor(cur, WRITER)
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
            actor(cur, "another-writer")
            expect_refusal(cur, lambda: record(cur, key=key), (psycopg.errors.RaiseException,),
                           "workflow_census_key_reuse", "key_reuse_other_principal")
            actor(cur, WRITER)
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "summary": {"ratio": 0.5}}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_fraction_refused",
                           "fraction")
            expect_refusal(cur, lambda: record(cur, payload={"schema_version": "other.v1"}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_shape_refused",
                           "shape")
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "schema_version": "other.v1"}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_shape_refused",
                           "schema_only")
            expect_refusal(cur, lambda: record(cur, payload={**CENSUS, "rows": [{"blob": "x" * 4_200_000}]}),
                           (psycopg.errors.RaiseException,), "workflow_census_payload_too_large",
                           "too_large")

            # 4. The writer login has no table privilege, so it cannot forge through it.
            for label, statement in (
                ("select", "select count(*) from ops.workflow_census_record"),
                ("update", "update ops.workflow_census_record set principal = 'x'"),
                ("delete", "delete from ops.workflow_census_record"),
            ):
                expect_refusal(cur, functools.partial(cur.execute, statement),
                               (psycopg.errors.InsufficientPrivilege,), "permission denied",
                               f"writer_direct_{label}")
            expect_refusal(cur, lambda: owner_insert(cur, 3, second[3], "clock_timestamp()",
                                                     principal="joe-local", session="carr_writer"),
                           (psycopg.errors.InsufficientPrivilege,), "permission denied",
                           "writer_forged_insert")

            # 1b + reading as the reader LOGIN: the database's chain verifies here.
            become(cur, READER_LOGIN)
            expect_refusal(cur, lambda: record(cur), (psycopg.errors.InsufficientPrivilege,),
                           "permission denied", "reader_cannot_write")
            expect_refusal(cur, lambda: cur.execute("select count(*) from ops.workflow_census_record"),
                           (psycopg.errors.InsufficientPrivilege,), "permission denied",
                           "reader_direct_select")
            answer = read(cur)
            truncated = read(cur, 1)
            become(cur, None)
            if answer.get("guards") != {name: "A" for name in GUARDS}:
                raise RuntimeError(f"the read door did not report the guards: {answer.get('guards')}")
            if truncated.get("truncated") is not True or len(truncated.get("chain", [])) != 1:
                raise RuntimeError(f"max_rows 1 over a 2-row chain did not say truncated: {truncated}")
            if verify_census_chain(served(truncated), config()).get("detail") != "chain_truncated":
                raise RuntimeError("the reader accepted a truncated chain")
            verdict = verify_census_chain(served(answer), config())
            if verdict.get("available") is not True:
                raise RuntimeError(f"the reader could not recompute the database's chain: {verdict}")
            if verdict["attestation"]["seq"] != 2 or verdict["census"] != CENSUS:
                raise RuntimeError(f"the reader attested the wrong row: {verdict['attestation']}")
            if any(row["db_session_principal"] != WRITER_LOGIN for row in answer["chain"]):
                raise RuntimeError("db_session_principal is not the writer's login role")
            if verify_census_chain(served(answer), config("someone-else")).get("reason") != "unknown_writer":
                raise RuntimeError("a real row from an unlisted writer was not refused as unknown_writer")

            # 5. OWNER attacks with every trigger on. The owner may set the actor
            # setting; it still cannot write a row under any identity but its own.
            actor(cur, "joe-local")
            seq, prev = head(cur)
            for label, kwargs, expected in (
                ("owner_forged_principal", {"principal": "someone-else"}, "workflow_census_principal_forged"),
                ("owner_forged_session", {"session": "carr_writer"}, "workflow_census_session_principal_forged"),
                ("owner_forged_both", {"principal": "someone-else", "session": WRITER_LOGIN},
                 "workflow_census_session_principal_forged"),
            ):
                expect_refusal(cur, functools.partial(owner_insert, cur, seq + 1, prev,
                                                      "clock_timestamp()",
                                                      principal=kwargs.get("principal"),
                                                      session=kwargs.get("session")),
                               (psycopg.errors.RaiseException,), expected, label)
            owner_cases: list[tuple[str, tuple[int, str | None, str], dict[str, Any], str]] = [
                ("owner_seq_gap", (seq + 3, prev, "clock_timestamp()"), {}, "workflow_census_chain_splice_refused"),
                ("owner_wrong_prev", (seq + 1, "0" * 64, "clock_timestamp()"), {}, "workflow_census_chain_splice_refused"),
                ("owner_backdated", (seq + 1, prev, "'2020-01-01T00:00:00Z'"), {}, "workflow_census_time_regression_refused"),
                ("owner_future", (seq + 1, prev, "clock_timestamp() + interval '1 hour'"), {}, "workflow_census_future_time_refused"),
                ("owner_forged_payload_digest", (seq + 1, prev, "clock_timestamp()"), {"payload_sha": "a" * 64},
                 "workflow_census_payload_digest_mismatch"),
                ("owner_forged_row_hash", (seq + 1, prev, "clock_timestamp()"), {"forged_row_hash": "b" * 64},
                 "workflow_census_row_hash_mismatch"),
            ]
            for label, args, extra, expected in owner_cases:
                expect_refusal(cur, functools.partial(owner_insert, cur, *args, **extra),
                               (psycopg.errors.RaiseException,), expected, label)
            # The one owner row the guard admits carries the owner's own login role,
            # which no writer list names -- anywhere in the chain it fails the read.
            cur.execute("savepoint owner_append")
            owner_insert(cur, seq + 1, prev, "clock_timestamp()")
            appended = read(cur)
            owner_verdict = verify_census_chain(served(appended), config(WRITER, "joe-local"))
            if (owner_verdict.get("detail") != "db_session_principal_not_listed"
                    or owner_verdict.get("at_seq") != seq + 1):
                raise RuntimeError("an owner-appended row was not refused as an unlisted login role")
            cur.execute("rollback to savepoint owner_append")
            expect_refusal(cur, lambda: cur.execute(
                "update ops.workflow_census_record set payload = '{}'::jsonb where seq = 1"),
                (psycopg.errors.RaiseException,), "append-only", "owner_edit_refused")
            expect_refusal(cur, lambda: cur.execute(
                "delete from ops.workflow_census_record where seq = 2"),
                (psycopg.errors.RaiseException,), "append-only", "owner_delete_refused")
            cur.execute("set local session_replication_role = replica")
            expect_refusal(cur, lambda: cur.execute(
                "delete from ops.workflow_census_record where seq = 2"),
                (psycopg.errors.RaiseException,), "append-only", "owner_replica_delete_refused")
            cur.execute("set local session_replication_role = origin")

            # 6b. Disable, then a plain ENABLE: state 'O', which the reader refuses.
            genuine = served(answer)
            cur.execute("savepoint guard_flip")
            cur.execute("alter table ops.workflow_census_record disable trigger workflow_census_record_chain_guard")
            cur.execute("alter table ops.workflow_census_record enable trigger workflow_census_record_chain_guard")
            flipped = verify_census_chain(served(read(cur)), config())
            if flipped.get("reason") != "tampered" or flipped.get("detail") != "guard_not_enforced":
                raise RuntimeError(f"a guard re-enabled as an ordinary trigger was not refused: {flipped}")
            cur.execute("rollback to savepoint guard_flip")

            # 7. The wholesale rewrite: triggers off, history deleted, a clean forged
            # chain written under the listed identities, triggers back ENABLE ALWAYS.
            cur.execute("savepoint rewrite")
            cur.execute("alter table ops.workflow_census_record disable trigger user")
            cur.execute("delete from ops.workflow_census_record")
            owner_insert(cur, 1, None, "clock_timestamp() - interval '1 hour'",
                         principal=WRITER, session=WRITER_LOGIN)
            for name in GUARDS:
                cur.execute(f"alter table ops.workflow_census_record enable always trigger {name}")
            rewritten = read(cur)
            alone = verify_census_chain(served(rewritten), config())
            if alone.get("available") is not True:
                raise RuntimeError(f"control failed: the rewritten chain should verify on its own: {alone}")
            against_anchor = verify_census_chain(served(rewritten, genuine["anchor"]), config())
            if against_anchor.get("reason") != "tampered":
                raise RuntimeError(f"a wholesale rewrite was not refused against the anchor: {against_anchor}")
            cur.execute("rollback to savepoint rewrite")

        print("PASS: workflow-census real-PostgreSQL: pinned and real hashes agree with the reader; "
              "writer and reader run as non-owner logins; the door refuses a missing or malformed "
              "principal, key reuse, fractions, wrong shape and oversize payloads; owner inserts that "
              "forge identity, splice, back-date, future-date or forge a digest are refused with every "
              "trigger on; guards are ENABLE ALWAYS and a flipped guard or a wholesale rewrite reads "
              "tampered; freshness and truncation follow the database")
        return 0
    except Exception as exc:  # noqa: BLE001 - gate contract is a printed failure, not a traceback
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
