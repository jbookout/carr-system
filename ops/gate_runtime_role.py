"""Runtime-role helpers for rollback-only database acceptance gates.

Gates connect with the owner credential so they can build disposable fixtures,
but their behavioural assertions must fire as the constrained runtime bundle.
Membership changes made here are intentionally inside the caller's transaction;
the gate's final rollback removes them along with its fixtures.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg import sql


def _one_text(cur: Any, query: str, label: str) -> str:
    row = cur.execute(query).fetchone()
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise RuntimeError(f"{label} was not returned")
    return row[0]


def grant_settable_runtime_roles(cur: Any, *roles: str) -> None:
    """Temporarily allow the session's current user to SET each named bundle.

    PostgreSQL preserves an existing membership's options when a repeated GRANT
    omits them.  Spell ``WITH SET TRUE`` so a Neon owner whose standing
    membership is deliberately ``SET FALSE`` can exercise the real firing
    role, solely until the surrounding transaction rolls back.
    """
    if not roles or any(not isinstance(role, str) or not role for role in roles):
        raise RuntimeError("at least one concrete runtime role is required")
    database_actor = _one_text(cur, "select current_user", "database actor")
    for role in roles:
        cur.execute(
            sql.SQL("grant {} to {} with set true").format(
                sql.Identifier(role), sql.Identifier(database_actor)
            )
        )


def set_local_role(cur: Any, role: str) -> None:
    """Become a runtime role and fail closed unless the database confirms it."""
    if not isinstance(role, str) or not role:
        raise RuntimeError("a concrete runtime role is required")
    cur.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
    actual = _one_text(cur, "select current_user", "runtime role identity")
    if actual != role:
        raise RuntimeError(f"expected current_user {role!r} after SET LOCAL ROLE, got {actual!r}")


@contextmanager
def rollback_only_connection(dsn: str) -> Iterator[Any]:
    """Yield one acceptance-gate transaction and roll it back on every exit.

    A gate's ``return fail(...)`` is a normal context-manager exit, which would
    otherwise commit fixture rows, temporary role options, or DDL.  The finally
    block therefore rolls back both assertion failures and normal early returns.
    """
    with psycopg.connect(dsn, autocommit=False) as conn:
        try:
            yield conn
        finally:
            conn.rollback()
