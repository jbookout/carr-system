"""Fixed-query least-privilege resolver for trusted continuity evidence."""
from __future__ import annotations

import os
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from lib.partner_continuity import ContinuityRefusal

IDENTITY_SQL = "select session_user,current_user"
AUTHORITY_SQL = (
    "select has_function_privilege(current_user, "
    "'ops.record_partner_continuity_origin(text,text,uuid,timestamptz,text,text,text,text,text,text,text)'::regprocedure,'execute'),"
    "has_function_privilege(current_user, "
    "'ops.record_partner_continuity_receiver_evidence(uuid,uuid,timestamptz,text)'::regprocedure,'execute'),"
    "has_function_privilege(current_user, "
    "'ops.record_partner_continuity_drive_retirement(text,text,text,text,timestamptz,text)'::regprocedure,'execute')"
)
TENANT_SQL = "select set_config('carr.continuity_tenant','carr-internal',true)"
EVIDENCE_SQL = "select * from ops.partner_continuity_evidence_window()"
DRIVE_SQL = "select ops.partner_continuity_drive_retirement_status()"


def continuity_dsn(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    value = source.get("CARR_DB_CONTINUITY_URL", "").strip()
    if not value:
        raise ContinuityRefusal("CARR_DB_CONTINUITY_URL is required for continuity evidence reads")
    try:
        login = unquote(urlsplit(value).username or "").lower()
    except ValueError as exc:
        raise ContinuityRefusal("CARR_DB_CONTINUITY_URL is malformed") from exc
    if login != "carr_reader":
        raise ContinuityRefusal("continuity evidence reader must use the carr_reader URL")
    return value


class ContinuityResolver:
    """Reads the two allowlisted projections, and never takes caller SQL or evidence."""
    def __init__(self, connect: Callable[[str], Any], dsn: str) -> None:
        self._conn = connect(dsn)
        try:
            with self._conn.cursor() as cur:
                cur.execute("begin transaction read only")
                cur.execute(IDENTITY_SQL)
                if cur.fetchone() != ("carr_reader", "carr_reader"):
                    raise ContinuityRefusal("continuity resolver is not the carr_reader identity")
                cur.execute(AUTHORITY_SQL)
                authority = cur.fetchone()
                if not isinstance(authority, (tuple, list)) or len(authority) != 3 or any(value is not False for value in authority):
                    raise ContinuityRefusal("continuity resolver has evidence-writing authority")
                cur.execute(TENANT_SQL)
        except Exception:
            self._conn.close()
            raise

    def evidence_rows(self) -> list[tuple[Any, ...]]:
        with self._conn.cursor() as cur:
            cur.execute(EVIDENCE_SQL)
            return [tuple(row) for row in cur.fetchall()]

    def drive_status(self) -> str:
        with self._conn.cursor() as cur:
            cur.execute(DRIVE_SQL)
            rows = cur.fetchall()
        if len(rows) != 1 or not isinstance(rows[0], (tuple, list)) or rows[0][0] not in {"RETIRED", "READY_FOR_JOE_APPROVAL"}:
            raise ContinuityRefusal("Drive retirement status does not resolve exactly once")
        return str(rows[0][0])

    def close(self) -> None:
        self._conn.close()


def resolver_from_environment(connect: Callable[[str], Any] | None = None) -> ContinuityResolver:
    if connect is None:
        try:
            import psycopg
        except ImportError as exc:
            raise ContinuityRefusal("psycopg is required for continuity evidence reads") from exc
        connect = psycopg.connect
    return ContinuityResolver(connect, continuity_dsn())
