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
    "has_function_privilege(current_user, 'ops.disable_legacy_schedule(text,text,text,text,text,text,text,text,text,text,text)'::regprocedure, 'execute'), "
    "has_function_privilege(current_user, "
    "'ops.record_claude_scheduler_observation(text,text,text,text,boolean,text,text,text,timestamptz,text)'::regprocedure, 'execute'), "
    "has_function_privilege(current_user, "
    "'ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text)'::regprocedure, 'execute')"
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
select receipt_ref,workflow_key,workflow_version,surface_id,locator,reason,approved_by,
       pre_observation_ref,post_observation_ref,sibling_observation_ref,
       sibling_surface_id,sibling_locator,sibling_pre_observation_ref,sibling_post_observation_ref
  from ops.legacy_schedule_disable_receipt
 where receipt_ref=%s
"""
PROVIDER_OBSERVATION_SQL = """
select r.receipt_ref,r.surface_id,r.workflow_key,r.workflow_version,r.locator,r.scheduler_kind,r.scheduler_state,
       r.cron_expression,r.timezone,r.definition_sha256,r.provider_revision,r.source_fingerprint,
       r.observed_at,r.device_id
  from ops.legacy_schedule_observation_receipt r
  left join ops.legacy_schedule_provider_contract c
    on r.scheduler_kind='claude-code' and c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
   and c.workflow_version=r.workflow_version and c.locator=r.locator
   and c.cron_expression=r.cron_expression and c.timezone=r.timezone
   and c.definition_sha256=r.definition_sha256
  left join ops.legacy_schedule_launchd_contract l
    on r.scheduler_kind='launchd' and l.surface_id=r.surface_id and l.workflow_key=r.workflow_key
   and l.workflow_version=r.workflow_version and l.locator=r.locator
   and l.schedule_sha256=r.cron_expression and l.timezone=r.timezone
   and l.plist_sha256=r.definition_sha256
 where r.receipt_ref=%s
   and ((r.scheduler_kind='claude-code' and c.surface_id is not null)
     or (r.scheduler_kind='launchd' and l.surface_id is not null))
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
                if not isinstance(authority, (tuple, list)) or len(authority) != 4 or any(bool(value) for value in authority):
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
        (ref, workflow_key, version, surface_id, locator, reason, approved_by,
         pre_observation_ref, post_observation_ref, sibling_observation_ref,
         sibling_surface_id, sibling_locator, sibling_pre_observation_ref,
         sibling_post_observation_ref) = rows[0]
        if str(approved_by) != "joe" or not str(reason).strip():
            raise CutoverRefusal("disable authority receipt is not Joe-bound")
        subject = {"workflow_key": str(workflow_key), "workflow_version": int(version),
                   "surface_id": str(surface_id), "locator": str(locator)}
        if sibling_surface_id is not None:
            subject.update({"sibling_surface_id": str(sibling_surface_id),
                            "sibling_locator": str(sibling_locator)})
            observation_refs: dict[str, Any] = {
                "pre": str(pre_observation_ref), "post": str(post_observation_ref),
                "sibling_pre": str(sibling_pre_observation_ref),
                "sibling_post": str(sibling_post_observation_ref),
            }
        else:
            observation_refs = {"pre": str(pre_observation_ref), "post": str(post_observation_ref),
                                "sibling": None if sibling_observation_ref is None else str(sibling_observation_ref)}
        return {"kind": "human_authority_receipt", "receipt_ref": str(ref), "immutable": True,
                "authority_subject": "joe", "action": "disable-legacy-schedule",
                "subject": subject, "observation_refs": observation_refs}

    def scheduler_observation_receipt(self, receipt_ref: str) -> dict[str, Any]:
        if not isinstance(receipt_ref, str) or not receipt_ref:
            raise CutoverRefusal("provider observation receipt reference is required")
        with self._conn.cursor() as cur:
            cur.execute(PROVIDER_OBSERVATION_SQL, (receipt_ref,))
            rows = cur.fetchall()
        if len(rows) != 1:
            raise CutoverRefusal("provider observation receipt does not resolve exactly once")
        (ref, surface_id, workflow_key, version, locator, scheduler_kind, state,
         cron_expression, timezone_name,
         definition_sha256, provider_revision, source_fingerprint, observed_at, device_id) = rows[0]
        return {
            "kind": "scheduler_observation_receipt", "receipt_ref": str(ref),
            "surface_id": str(surface_id), "workflow_key": str(workflow_key),
            "workflow_version": int(version), "locator": str(locator),
            "scheduler_kind": str(scheduler_kind),
            "scheduler_state": str(state), "cron_expression": str(cron_expression),
            "timezone": str(timezone_name), "definition_sha256": str(definition_sha256),
            "provider_revision": str(provider_revision), "source_fingerprint": str(source_fingerprint),
            "observed_at": observed_at.isoformat().replace("+00:00", "Z") if hasattr(observed_at, "isoformat") else str(observed_at),
            "device_id": str(device_id), "device_principal_bound": True, "immutable": True,
        }

    def provider_observation_receipt(self, receipt_ref: str) -> dict[str, Any]:
        """Compatibility wrapper for the Claude-only caller name."""
        receipt = self.scheduler_observation_receipt(receipt_ref)
        if receipt["scheduler_kind"] != "claude-code":
            raise CutoverRefusal("receipt is not a Claude scheduler observation")
        return {**receipt, "kind": "provider_scheduler_observation_receipt"}


def resolver_from_environment(connect: Callable[[str], Any] | None = None) -> ReceiptResolver:
    if connect is None:
        try:
            import psycopg
        except ImportError as exc:
            raise CutoverRefusal("psycopg is required for cutover receipt reads") from exc
        connect = psycopg.connect
    return ReceiptResolver(connect, jobs_dsn())
