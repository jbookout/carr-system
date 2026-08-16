"""Least-privilege read-only receipt resolver for scheduler cutover evidence."""
from __future__ import annotations

import os
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from lib.control_plane_scheduler_cutover import CutoverRefusal

IDENTITIES = {"carr_jobs", "carr_reader"}
IDENTITY_SQL = "select session_user, current_user"
AUTHORITY_SQL = (
    "select has_function_privilege(current_user, "
    "'ops.record_workflow_acceptance(text,text,text,text)'::regprocedure, 'execute'), "
    "has_function_privilege(current_user, 'ops.disable_legacy_schedule(text,text,text,text,text)'::regprocedure, 'execute')"
)
ACCEPTANCE_SQL = """
select wa.workflow_key,wa.workflow_version,wa.mode,wa.status,wa.receipt_ref,wa.accepted_by
  from ops.workflow_acceptance wa
 where wa.receipt_ref=%s
   and exists (
     select 1 from ops.job j join ops.job_receipt r on r.job_id=j.id
      where j.definition_key=wa.workflow_key and j.definition_version=wa.workflow_version
        and j.mode=wa.mode and r.kind='completion' and r.receipt_ref=wa.receipt_ref
   )
"""
DISABLE_RECEIPT_SQL = """
select receipt_ref,workflow_key,workflow_version,surface_id,locator,reason,approved_by
  from ops.legacy_schedule_disable_receipt
 where receipt_ref=%s
"""


def jobs_dsn(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    value = source.get("CARR_DB_JOBS_URL", "").strip()
    if not value:
        raise CutoverRefusal("CARR_DB_JOBS_URL is required for cutover receipt reads")
    try:
        login = unquote(urlsplit(value).username or "").lower()
    except ValueError as exc:
        raise CutoverRefusal("CARR_DB_JOBS_URL is malformed") from exc
    if login in {"carr_writer", "carr_owner", "owner", "writer", "postgres"}:
        raise CutoverRefusal("cutover receipt reader must not use an owner or writer URL")
    return value


class ReceiptResolver:
    """Fixed-query, read-only adapter; it never accepts caller-provided SQL."""

    def __init__(self, connect: Callable[[str], Any], dsn: str) -> None:
        self._conn = connect(dsn)
        try:
            with self._conn.cursor() as cur:
                cur.execute("begin transaction read only")
                cur.execute(IDENTITY_SQL)
                identity = cur.fetchone()
                if (not isinstance(identity, (tuple, list)) or len(identity) != 2
                        or str(identity[0]) not in IDENTITIES or str(identity[1]) not in IDENTITIES):
                    raise CutoverRefusal("cutover receipt reader is not a jobs/reader identity")
                cur.execute(AUTHORITY_SQL)
                authority = cur.fetchone()
                if not isinstance(authority, (tuple, list)) or len(authority) != 2 or any(bool(value) for value in authority):
                    raise CutoverRefusal("cutover receipt reader has writer authority")
        except Exception:
            self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    def _acceptance_row(self, receipt_ref: str) -> tuple[Any, ...]:
        if not isinstance(receipt_ref, str) or not receipt_ref:
            raise CutoverRefusal("receipt reference is required")
        with self._conn.cursor() as cur:
            cur.execute(ACCEPTANCE_SQL, (receipt_ref,))
            rows = cur.fetchall()
        if len(rows) != 1:
            raise CutoverRefusal("receipt reference does not resolve to exactly one immutable completion acceptance")
        return tuple(rows[0])

    def acceptance_receipt(self, receipt_ref: str) -> dict[str, Any]:
        workflow_key, version, mode, status, ref, accepted_by = self._acceptance_row(receipt_ref)
        return {"kind": "workflow_acceptance_receipt", "receipt_ref": str(ref),
                "workflow_key": str(workflow_key), "workflow_version": int(version),
                "mode": str(mode), "status": str(status), "accepted_by": accepted_by,
                "immutable": True}

    def disable_authority_receipt(self, receipt_ref: str) -> dict[str, Any]:
        if not isinstance(receipt_ref, str) or not receipt_ref:
            raise CutoverRefusal("disable authority receipt reference is required")
        with self._conn.cursor() as cur:
            cur.execute(DISABLE_RECEIPT_SQL, (receipt_ref,))
            rows = cur.fetchall()
        if len(rows) != 1:
            raise CutoverRefusal("disable authority receipt does not resolve exactly once")
        ref, workflow_key, version, surface_id, locator, reason, approved_by = rows[0]
        if str(approved_by) != "joe" or not str(reason).strip():
            raise CutoverRefusal("disable authority receipt is not Joe-bound")
        return {"kind": "human_authority_receipt", "receipt_ref": str(ref), "immutable": True,
                "authority_subject": "joe", "action": "disable-legacy-schedule",
                "subject": {"workflow_key": str(workflow_key), "workflow_version": int(version),
                            "surface_id": str(surface_id), "locator": str(locator)}}


def resolver_from_environment(connect: Callable[[str], Any] | None = None) -> ReceiptResolver:
    if connect is None:
        try:
            import psycopg
        except ImportError as exc:
            raise CutoverRefusal("psycopg is required for cutover receipt reads") from exc
        connect = psycopg.connect
    return ReceiptResolver(connect, jobs_dsn())
