#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only gate for one-time, authority-bound Program 6 browser approval challenges."""

from __future__ import annotations

import os
import sys
import uuid

import psycopg
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"program6-browser-action-challenge-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refusal(cur, sql: str, params: tuple = (), label: str = "unsafe operation") -> None:
    cur.execute("savepoint program6_browser_challenge_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint program6_browser_challenge_refusal")
        return
    cur.execute("rollback to savepoint program6_browser_challenge_refusal")
    raise RuntimeError(f"{label} was accepted")


def as_authority(cur, actor: str, sql: str, params: tuple):
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        return cur.execute(sql, params).fetchone()
    finally:
        cur.execute("reset session authorization")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            cur.execute("""do $$ begin
              if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
                create role carr_authority_joe login;
              end if;
              if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then
                create role carr_authority_dell login;
              end if;
            end $$""")
            cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
            grant_settable_runtime_roles(
                cur, "carr_authority_joe", "carr_authority_dell", "carr_writer", "carr_reader", "carr_jobs"
            )

            function = "ops.redeem_program6_browser_action_challenge(%s,%s,%s,%s,%s)"
            token_a = "a" * 64
            session_a = "b" * 64
            material_a = "c" * 64
            key_a = uuid.uuid4()

            set_local_role(cur, "carr_writer")
            refusal(cur, f"select {function}", (token_a, session_a, "accept-ready-plan", material_a, key_a),
                    "routine writer challenge redemption")
            refusal(cur, """insert into ops.program6_browser_action_challenge_redemption
                            (token_digest,session_digest,action,material_digest,idempotency_key,redeemed_by_actor_id)
                          values (%s,%s,'accept-ready-plan',%s,%s,(select id from actor where slug='joe'))""",
                    ("d" * 64, session_a, material_a, uuid.uuid4()), "routine writer direct redemption insert")
            cur.execute("reset role")

            first = as_authority(cur, "dell", f"select {function}",
                (token_a, session_a, "accept-ready-plan", material_a, key_a))
            replay = as_authority(cur, "dell", f"select {function}",
                (token_a, session_a, "accept-ready-plan", material_a, key_a))
            if first != (True,) or replay != (False,):
                raise RuntimeError(f"first redemption and atomic replay were not true then false: {first}, {replay}")

            row = cur.execute("""select token_digest,session_digest,action,material_digest,idempotency_key,a.slug
                                 from ops.program6_browser_action_challenge_redemption r
                                 join actor a on a.id=r.redeemed_by_actor_id
                                where r.token_digest=%s""", (token_a,)).fetchone()
            if row != (token_a, session_a, "accept-ready-plan", material_a, key_a, "dell"):
                raise RuntimeError(f"authority-derived redemption evidence was not exact: {row}")

            other = as_authority(cur, "joe", f"select {function}",
                ("e" * 64, "f" * 64, "accept-outcome-feedback", "0" * 64, uuid.uuid4()))
            if other != (True,):
                raise RuntimeError(f"second allowed approval action was not redeemable: {other}")

            for bad_token, bad_session, bad_action, bad_material, label in (
                ("A" * 64, session_a, "accept-ready-plan", material_a, "uppercase digest"),
                (token_a, "short", "accept-ready-plan", material_a, "short session digest"),
                (token_a, session_a, "execute", material_a, "open action"),
                (token_a, session_a, "accept-outcome-feedback", "sha256:" + material_a, "prefixed material digest"),
            ):
                refusal(cur, f"select {function}",
                        (bad_token, bad_session, bad_action, bad_material, uuid.uuid4()), label)

            refusal(cur, "update ops.program6_browser_action_challenge_redemption set action='accept-outcome-feedback' where token_digest=%s",
                    (token_a,), "append-only redemption update")
            refusal(cur, "delete from ops.program6_browser_action_challenge_redemption where token_digest=%s",
                    (token_a,), "append-only redemption delete")

            privileges = cur.execute("""select
              has_table_privilege('carr_reader','ops.program6_browser_action_challenge_redemption','INSERT'),
              has_table_privilege('carr_writer','ops.program6_browser_action_challenge_redemption','INSERT'),
              has_table_privilege('carr_jobs','ops.program6_browser_action_challenge_redemption','INSERT'),
              has_table_privilege('carr_authority','ops.program6_browser_action_challenge_redemption','INSERT'),
              has_function_privilege('carr_writer',
                'ops.redeem_program6_browser_action_challenge(text,text,text,text,uuid)'::regprocedure,'EXECUTE')
            """).fetchone()
            if privileges != (False, False, False, False, False):
                raise RuntimeError(f"browser challenge ledger grant leaked: {privileges}")
    except Exception as exc:
        return fail(str(exc))
    print("program6-browser-action-challenge-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
