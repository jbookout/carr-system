#!/usr/bin/env python3
# ci: db-gate
"""Disposable-DB regression for rollback-only runtime-role gate helpers."""

from __future__ import annotations

import os
import secrets
import uuid

import psycopg
from psycopg import sql

from gate_runtime_role import grant_settable_runtime_roles, set_local_role


def one(cur, query: str, params: tuple = ()):
    row = cur.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError(f"expected one row: {query}")
    return row[0]


def set_option(cur, role: str, member: str) -> bool | None:
    return one(cur, """select m.set_option
                         from pg_auth_members m
                         join pg_roles granted on granted.oid=m.roleid
                         join pg_roles recipient on recipient.oid=m.member
                        where granted.rolname=%s and recipient.rolname=%s""", (role, member))


def refused_set_role(cur, role: str) -> None:
    cur.execute("savepoint rejected_role_switch")
    try:
        cur.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
    except psycopg.Error:
        cur.execute("rollback to savepoint rejected_role_switch")
        return
    cur.execute("rollback to savepoint rejected_role_switch")
    raise RuntimeError("SET FALSE membership unexpectedly permitted SET ROLE")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")
    suffix = uuid.uuid4().hex
    denied_role = f"gate_switch_denied_{suffix}"
    owner_role = f"gate_switch_owner_{suffix}"
    member = f"gate_switch_member_{suffix}"
    password = secrets.token_urlsafe(24)
    try:
        # The non-admin actor proves the PostgreSQL premise: SET FALSE blocks a
        # bare SET ROLE.  It intentionally cannot alter its own membership.
        with psycopg.connect(dsn, autocommit=True) as owner, owner.cursor() as cur:
            cur.execute(sql.SQL("create role {} nologin").format(sql.Identifier(denied_role)))
            cur.execute(
                sql.SQL("create role {} login password {}").format(
                    sql.Identifier(member), sql.Literal(password)
                )
            )
            cur.execute(
                sql.SQL("grant {} to {} with set false").format(
                    sql.Identifier(denied_role), sql.Identifier(member)
                )
            )

        with psycopg.connect(dsn, user=member, password=password, autocommit=False) as conn, conn.cursor() as cur:
            if set_option(cur, denied_role, member) is not False:
                raise RuntimeError("fixture did not create a SET FALSE membership")
            refused_set_role(cur, denied_role)

        # The real helper must run as a fixture owner, just like the acceptance
        # gates.  This proves its explicit option update, identity assertion,
        # and savepoint/outer rollback behaviour without leaving any role.
        with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
            owner_actor = one(cur, "select current_user")
            cur.execute(sql.SQL("create role {} nologin").format(sql.Identifier(owner_role)))
            cur.execute(
                sql.SQL("grant {} to {} with set false").format(
                    sql.Identifier(owner_role), sql.Identifier(owner_actor)
                )
            )
            if set_option(cur, owner_role, owner_actor) is not False:
                raise RuntimeError("owner fixture did not create a SET FALSE membership")

            # Omitting SET from a repeated GRANT must retain the existing false flag.
            cur.execute(sql.SQL("grant {} to {}").format(sql.Identifier(owner_role), sql.Identifier(owner_actor)))
            if set_option(cur, owner_role, owner_actor) is not False:
                raise RuntimeError("bare repeated GRANT changed the SET FALSE membership")

            cur.execute("savepoint temporary_role_membership")
            grant_settable_runtime_roles(cur, owner_role)
            if set_option(cur, owner_role, owner_actor) is not True:
                raise RuntimeError("explicit gate grant did not set SET TRUE")
            set_local_role(cur, owner_role)
            cur.execute("reset role")
            cur.execute("rollback to savepoint temporary_role_membership")
            if set_option(cur, owner_role, owner_actor) is not False:
                raise RuntimeError("savepoint rollback did not restore SET FALSE")

            conn.rollback()
            if cur.execute("select to_regrole(%s)", (owner_role,)).fetchone() != (None,):
                raise RuntimeError("outer rollback did not remove disposable owner fixture")
    except Exception as exc:
        raise SystemExit(f"gate-runtime-role-gate: FAIL — {exc}") from exc
    finally:
        # Setup must commit so the non-owner connection can exist.  Clean it
        # explicitly afterwards; the membership mutation under test still
        # rolls back inside the member's transaction above.
        with psycopg.connect(dsn, autocommit=True) as owner, owner.cursor() as cur:
            cur.execute(sql.SQL("revoke {} from {}").format(sql.Identifier(denied_role), sql.Identifier(member)))
            cur.execute(sql.SQL("drop role {}").format(sql.Identifier(member)))
            cur.execute(sql.SQL("drop role {}").format(sql.Identifier(denied_role)))
            if cur.execute("select to_regrole(%s),to_regrole(%s)", (member, denied_role)).fetchone() != (None, None):
                raise SystemExit("gate-runtime-role-gate: FAIL — disposable role cleanup was incomplete")
    print("gate-runtime-role-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
