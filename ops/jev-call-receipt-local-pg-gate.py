#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only real-PostgreSQL acceptance for the server-side Jev call log
(migration 0587: ops.jev_call_receipt, its write door
ops.record_jev_call_receipt and its read door ops.read_jev_call_receipts).

WHY THIS EXISTS. mcp-server/test/jev-call-receipt.test.mjs exercises the
ask-jev and read-jev-call-receipts handlers against a JS fake of the two
doors. A fake cannot catch what only a real catalog exposes (migration 0580's
unqualified digest() under a pinned search_path shipped exactly that way), and
it cannot prove the properties the Jev gates will lean on:

  1. The write door works for carr_writer and stamps recorded_at from the
     server clock; the read door works for carr_reader and returns the
     session's receipts oldest-first with answers only for build_advisory.
  2. No app role can touch the table directly: carr_writer and carr_reader
     are refused SELECT/INSERT/UPDATE/DELETE on ops.jev_call_receipt.
  3. The table is append-only even for its owner: UPDATE and DELETE are
     refused by trigger.
  4. prompt_sha256 is required exactly when purpose = 'build_advisory', both
     at the door and by the table's own CHECK.
  5. The read door honours since and limit (the most recent `limit` rows at
     or after `since`, returned oldest first).
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role

H_STATE = "a" * 64
H_QUESTIONS = "b" * 64
H_ANSWERS = "c" * 64
H_PROMPT = "d" * 64
SESSION = f"jev-gate-{uuid.uuid4()}"
ANSWERS = {"q1": {"noul": 0.8}}


def fail(message: str) -> int:
    print(f"jev-call-receipt-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def record(cur: psycopg.Cursor[Any], **overrides: Any) -> tuple[Any, ...]:
    args: dict[str, Any] = {
        "session_id": SESSION,
        "purpose": "call",
        "question_ids": ["q1"],
        "facets": ["diagnosis"],
        "model_requested": "jev-latest",
        "model_answered": "jev-1.14.0",
        "state_sha256": H_STATE,
        "questions_sha256": H_QUESTIONS,
        "answers_sha256": H_ANSWERS,
        "prompt_sha256": None,
        "answers": Jsonb(ANSWERS),
        "usage": Jsonb({"input_tokens": 3}),
        "actor_slug": "jev-call-receipt-local-pg-gate",
        "idempotency_key": str(uuid.uuid4()),
    }
    args.update(overrides)
    row = cur.execute(
        """select receipt_id, recorded_at, replayed from ops.record_jev_call_receipt(
             %(session_id)s, %(purpose)s, %(question_ids)s, %(facets)s,
             %(model_requested)s, %(model_answered)s, %(state_sha256)s,
             %(questions_sha256)s, %(answers_sha256)s, %(prompt_sha256)s,
             %(answers)s, %(usage)s, %(actor_slug)s, %(idempotency_key)s)""",
        args,
    ).fetchone()
    if row is None:
        raise RuntimeError("record_jev_call_receipt returned no row")
    return row


def read(cur: psycopg.Cursor[Any], since: str | None = None, limit: int = 200) -> dict[str, Any]:
    row = cur.execute(
        "select ops.read_jev_call_receipts(%s, %s::timestamptz, %s)", (SESSION, since, limit)
    ).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise RuntimeError("read_jev_call_receipts returned no object")
    return row[0]


def expect_refusal(
    cur: psycopg.Cursor[Any],
    fn: Callable[[], object],
    errors: tuple[type[BaseException], ...],
    expected_substring: str,
    label: str,
) -> None:
    """Run `fn` expecting one of `errors` mentioning `expected_substring`.

    Each expected refusal gets its own savepoint: a failed statement poisons
    the rest of a real PostgreSQL transaction until it is rolled back.
    """
    cur.execute(f"savepoint {label}")
    try:
        fn()
    except errors as exc:
        cur.execute(f"rollback to savepoint {label}")
        if expected_substring not in str(exc):
            raise RuntimeError(f"{label} refused with the wrong reason: {exc}") from exc
        return
    cur.execute(f"rollback to savepoint {label}")
    raise RuntimeError(f"{label} was accepted; expected a refusal")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            grant_settable_runtime_roles(cur, "carr_writer", "carr_reader")

            # 1. The write door, as carr_writer, with the server clock.
            set_local_role(cur, "carr_writer")
            before = cur.execute("select clock_timestamp()").fetchone()
            first = record(cur)
            advisory_key = str(uuid.uuid4())
            second = record(
                cur,
                purpose="build_advisory",
                prompt_sha256=H_PROMPT,
                facets=["architecture_or_design"],
                idempotency_key=advisory_key,
            )
            replay = record(
                cur,
                purpose="build_advisory",
                prompt_sha256=H_PROMPT,
                facets=["architecture_or_design"],
                idempotency_key=advisory_key,
            )
            cur.execute("reset role")
            if before is None or not (first[1] >= before[0] and second[1] >= first[1]):
                raise RuntimeError(f"recorded_at is not the server clock in order: {before} {first} {second}")
            if first[2] is not False or second[2] is not False or replay[2] is not True or replay[0] != second[0]:
                raise RuntimeError(f"idempotent replay misbehaved: {first} {second} {replay}")

            # 1b/5. The read door, as carr_reader.
            set_local_role(cur, "carr_reader")
            payload = read(cur)
            limited = read(cur, since=None, limit=1)
            after_first = read(cur, since=first[1].isoformat(), limit=200)
            cur.execute("reset role")
            receipts = payload["receipts"]
            if not payload.get("server_now") or len(receipts) != 2:
                raise RuntimeError(f"read door returned {len(receipts)} receipts: {payload}")
            call, advisory = receipts
            expected_keys = {
                "receipt_id", "recorded_at", "purpose", "question_ids", "facets", "model",
                "state_sha256", "prompt_sha256", "answers",
            }
            if set(call) != expected_keys:
                raise RuntimeError(f"receipt shape drifted: {sorted(call)}")
            if call["purpose"] != "call" or call["answers"] is not None or call["prompt_sha256"] is not None:
                raise RuntimeError(f"a call receipt leaked answers or a prompt digest: {call}")
            if advisory["purpose"] != "build_advisory" or advisory["answers"] != ANSWERS:
                raise RuntimeError(f"a build_advisory receipt did not carry its answers: {advisory}")
            if advisory["prompt_sha256"] != H_PROMPT or advisory["model"] != "jev-1.14.0":
                raise RuntimeError(f"build_advisory receipt fields drifted: {advisory}")
            if call["question_ids"] != ["q1"] or call["facets"] != ["diagnosis"]:
                raise RuntimeError(f"array fields drifted: {call}")
            if len(limited["receipts"]) != 1 or limited["receipts"][0]["purpose"] != "build_advisory":
                raise RuntimeError(f"limit did not keep the most recent receipt: {limited}")
            if len(after_first["receipts"]) != 2:
                raise RuntimeError(f"since is not inclusive: {after_first}")

            # 2. No direct table privilege for any app role.
            for role in ("carr_writer", "carr_reader"):
                set_local_role(cur, role)
                for label, statement in (
                    ("select", "select count(*) from ops.jev_call_receipt"),
                    ("update", "update ops.jev_call_receipt set model_answered = 'x'"),
                    ("delete", "delete from ops.jev_call_receipt"),
                ):

                    def direct(statement: str = statement) -> object:
                        return cur.execute(statement)

                    expect_refusal(
                        cur,
                        direct,
                        (psycopg.errors.InsufficientPrivilege,),
                        "permission denied",
                        f"{role}_direct_{label}",
                    )
                cur.execute("reset role")
            set_local_role(cur, "carr_reader")
            expect_refusal(
                cur,
                lambda: record(cur),
                (psycopg.errors.InsufficientPrivilege,),
                "permission denied",
                "reader_cannot_write",
            )
            cur.execute("reset role")

            # 3. Append-only for everyone, the owner included.
            expect_refusal(
                cur,
                lambda: cur.execute(
                    "update ops.jev_call_receipt set model_answered = 'forged' where session_id = %s",
                    (SESSION,),
                ),
                (psycopg.errors.RaiseException,),
                "append-only",
                "owner_update_refused",
            )
            expect_refusal(
                cur,
                lambda: cur.execute("delete from ops.jev_call_receipt where session_id = %s", (SESSION,)),
                (psycopg.errors.RaiseException,),
                "append-only",
                "owner_delete_refused",
            )

            # 4. prompt_sha256 iff build_advisory, at the door and in the table.
            set_local_role(cur, "carr_writer")
            expect_refusal(
                cur,
                lambda: record(cur, purpose="build_advisory", prompt_sha256=None),
                (psycopg.errors.RaiseException,),
                "jev_call_receipt_prompt_sha256_iff_build_advisory",
                "advisory_without_prompt",
            )
            expect_refusal(
                cur,
                lambda: record(cur, purpose="call", prompt_sha256=H_PROMPT),
                (psycopg.errors.RaiseException,),
                "jev_call_receipt_prompt_sha256_iff_build_advisory",
                "call_with_prompt",
            )
            expect_refusal(
                cur,
                lambda: record(cur, state_sha256="NOT-HEX"),
                (psycopg.errors.CheckViolation,),
                "state_sha256",
                "bad_digest",
            )
            cur.execute("reset role")
            expect_refusal(
                cur,
                lambda: cur.execute(
                    """insert into ops.jev_call_receipt (session_id, purpose, question_ids,
                         model_requested, model_answered, state_sha256, questions_sha256,
                         answers_sha256, prompt_sha256, answers, actor_slug, idempotency_key)
                       values (%s, 'build_advisory', array['q1'], 'm', 'm', %s, %s, %s, null,
                         '{}'::jsonb, 'gate', %s)""",
                    (SESSION, H_STATE, H_QUESTIONS, H_ANSWERS, str(uuid.uuid4())),
                ),
                (psycopg.errors.CheckViolation,),
                "jev_call_receipt_prompt_iff_build_advisory",
                "table_check_prompt_iff",
            )

        print(
            "PASS: jev-call-receipt real-PostgreSQL write/read doors, no direct grants, "
            "append-only trigger, and prompt_sha256-iff-build_advisory proof"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - gate contract is a printed failure, not a traceback
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
