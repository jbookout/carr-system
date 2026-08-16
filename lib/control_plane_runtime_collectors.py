"""Least-privilege runtime adapters for cognition input collectors.

Every database read runs in an explicit read-only transaction.  The scheduler
payload is never an evidence source. Signed-in collectors append immutable,
ledger-bound receipts through their separately provisioned device identities;
the routine jobs role can only read those receipts.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import UUID
from zoneinfo import ZoneInfo

from lib.control_plane_collectors_audit import AUDIT_BUILDERS, SQL as AUDIT_SQL, audit_evidence_envelope
from lib.control_plane_collectors_records import CanonicalRecordCollector, QUERIES
from lib.control_plane_collectors_social import BUILDERS as SOCIAL_BUILDERS, SocialCanonicalCollector
from lib.control_plane_inputs import InputUnavailable
from lib.npi_sweep import NpiInputError, filter_candidates, load_policy

REPO = Path(__file__).resolve().parent.parent

DEVICE_BUILDERS = frozenset({"linkedin.source-posts", "x.source-posts"})

SOCIAL_SQL: dict[str, str] = {
    "idea_bank_oldest_unsurfaced": """select id, title, last_surfaced
      from public.v_control_plane_idea_candidates
      order by last_surfaced asc nulls first, id limit 10""",
    "social_next_week_sources": """select source_ref
      from public.v_control_plane_social_sources limit 40""",
    "social_next_week_coverage": """select case when count(*)=0 then 'uncovered' else 'covered' end as coverage_state
      from public.v_control_plane_social_coverage
     where scheduled_at >= %s::date
       and scheduled_at < (%s::date + interval '7 days')""",
    "social_metric_exports": """select placement_id::text, external_id, platform,
             source_observed_at, metric_kind, metric_value,
             (source_observed_at >= %s::timestamptz - interval '7 days'
              and source_observed_at <= %s::timestamptz) as window_current,
             owned_account,
             (coalesce(live_at, scheduled_at) >= %s::timestamptz - interval '7 days'
              and coalesce(live_at, scheduled_at) <= %s::timestamptz) as placement_in_window
      from public.v_control_plane_social_metric_exports
      order by source_observed_at desc, placement_id""",
    "latest_receipt": """select r.receipt_ref, r.created_at
          from ops.job_receipt r join ops.job j on j.id=r.job_id
         where j.definition_key=%s and j.mode=%s and r.kind='completion'
           and j.scheduled_for >= %s::timestamptz
           and j.scheduled_for < %s::timestamptz
         order by r.created_at desc limit 1""",
    "device_evidence_receipt": """select id::text, device_id, observed_at, evidence
      from ops.device_evidence_receipt
     where workflow_key=%s and builder_key=%s and mode=%s and scheduled_for=%s
     order by created_at desc""",
    "npi_device_evidence_receipt": """select id::text, device_id, observed_at, source_release,
             source_checksum, results
      from ops.npi_device_evidence_receipt
     where workflow_key=%s and builder_key='npi.weekly-delta' and mode=%s and scheduled_for=%s
     order by created_at desc""",
}

# The adapter is an allowlisted transport, not a generic SELECT executor.  A
# collector must use one reviewed query text keyed by this registry.
REGISTERED_SQL = {**AUDIT_SQL, **QUERIES, **SOCIAL_SQL}


def _plain(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


class PsycopgReadOnlyAdapter:
    """Execute one reviewed SELECT and return mapping rows."""

    def __init__(self, connect_factory: Callable[[], Any]):
        self.connect_factory = connect_factory

    def fetch_all(self, query_key: str, sql: str,
                  params: Sequence[object] = ()) -> list[Mapping[str, Any]]:
        expected = REGISTERED_SQL.get(query_key)
        if expected is None or sql.strip() != expected.strip():
            raise InputUnavailable(query_key, "collector query is not the exact registered SQL")
        statement = expected.strip()
        with self.connect_factory() as conn, conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(statement, tuple(params))
            names = [column.name for column in (cur.description or ())]
            return [dict(zip(names, (_plain(value) for value in row), strict=True))
                    for row in cur.fetchall()]


class CanonicalSocialQuery:
    def __init__(self, reader: PsycopgReadOnlyAdapter, scheduled_for: datetime):
        self.reader, self.scheduled_for = reader, scheduled_for

    def rows(self, name: str):
        sql = SOCIAL_SQL.get(name)
        if sql is None:
            raise InputUnavailable(name, "social evidence requires a registered device collector")
        if name == "social_next_week_coverage":
            local_day = self.scheduled_for.astimezone(
                ZoneInfo("America/Chicago")).date()
            next_monday = local_day + timedelta(days=(7 - local_day.weekday()) % 7 or 7)
            return self.reader.fetch_all(
                name, sql, (next_monday.isoformat(), next_monday.isoformat()))
        if name == "social_metric_exports":
            return self.reader.fetch_all(
                name, sql, (self.scheduled_for, self.scheduled_for,
                            self.scheduled_for, self.scheduled_for))
        return self.reader.fetch_all(name, sql)


class VersionedPolicyReader:
    def __init__(self, path: Path): self.path = path

    def read_object(self, name: str) -> Mapping[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InputUnavailable(name, "versioned collector policy is unavailable") from exc
        if data.get("schema_version") != 1 or not isinstance(data.get(name), Mapping):
            raise InputUnavailable(name, "versioned collector policy lacks the registered object")
        return data[name]


class JobReceiptReader:
    def __init__(self, reader: PsycopgReadOnlyAdapter, *, mode: str,
                 scheduled_for: datetime):
        self.reader, self.mode, self.scheduled_for = reader, mode, scheduled_for

    def latest(self, workflow_key: str) -> Mapping[str, Any] | None:
        local = self.scheduled_for.astimezone(ZoneInfo("America/Chicago"))
        if workflow_key == "idea-resurface-monthly":
            start_local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start_local.month == 12:
                end_local = start_local.replace(year=start_local.year + 1, month=1)
            else:
                end_local = start_local.replace(month=start_local.month + 1)
        else:
            start_local = (local - timedelta(days=local.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0)
            end_local = start_local + timedelta(days=7)
        rows = self.reader.fetch_all(
            "latest_receipt", SOCIAL_SQL["latest_receipt"],
            (workflow_key, self.mode, start_local, min(end_local, self.scheduled_for)))
        return rows[0] if rows else None


class RuntimeCanonicalEvidenceCollector:
    """EvidenceCollector implementation covering every registered builder."""

    def __init__(self, payload: dict[str, Any], *, mode: str,
                 connect_factory: Callable[[], Any], policy_path: Path):
        self.payload = payload
        self.mode = mode
        self.reader = PsycopgReadOnlyAdapter(connect_factory)
        self.policy = VersionedPolicyReader(policy_path)

    def _scheduled_for(self) -> datetime:
        value = self.payload.get("scheduled_for")
        if not isinstance(value, str):
            raise InputUnavailable("runtime", "collector needs ledger scheduled_for")
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InputUnavailable("runtime", "collector scheduled_for is invalid") from exc

    def collect(self, *, builder_key: str, workflow_key: str):
        if builder_key in DEVICE_BUILDERS:
            scheduled_for = self._scheduled_for()
            rows = self.reader.fetch_all(
                "device_evidence_receipt", SOCIAL_SQL["device_evidence_receipt"],
                (workflow_key, builder_key, self.mode, scheduled_for))
            if len(rows) != 1:
                raise InputUnavailable(
                    builder_key, "requires exactly one immutable bound device receipt")
            row = rows[0]
            receipt_id, device_id, observed_at, values = (
                row.get("id"), row.get("device_id"), row.get("observed_at"), row.get("evidence"))
            if (not isinstance(receipt_id, str) or not receipt_id
                    or not isinstance(device_id, str) or not device_id
                    or not isinstance(observed_at, str) or not observed_at
                    or not isinstance(values, Mapping)):
                raise InputUnavailable(builder_key, "bound device receipt is malformed")
            platform = "linkedin" if builder_key == "linkedin.source-posts" else "x"
            posts = values.get("source_posts")
            if (values.get("platform") != platform
                    or values.get("collector_state") != "available"
                    or not isinstance(values.get("voice_version"), int)
                    or isinstance(values.get("voice_version"), bool)
                    or not isinstance(posts, list) or not posts
                    or not all(isinstance(post, Mapping)
                               and isinstance(post.get("url"), str) and post["url"]
                               for post in posts)):
                raise InputUnavailable(builder_key, "bound device receipt values are invalid")
            if (builder_key == "linkedin.source-posts"
                    and (not 3 <= len(posts) <= 5
                         or not all(type(post.get("network_priority")) is bool
                                    for post in posts))):
                raise InputUnavailable(builder_key, "LinkedIn device receipt violates collector policy")
            if (builder_key == "x.source-posts"
                    and (not 1 <= len(posts) <= 20
                         or not all(isinstance(post.get("read_at"), str) and post["read_at"]
                                    for post in posts))):
                raise InputUnavailable(builder_key, "X device receipt violates collector policy")
            return [{"source_kind": "device",
                     "source_ref": f"device-receipt:{receipt_id}:{device_id}:{observed_at}",
                     "values": dict(values)}]
        if builder_key == "npi.weekly-delta":
            scheduled_for = self._scheduled_for()
            rows = self.reader.fetch_all("npi_device_evidence_receipt",
                SOCIAL_SQL["npi_device_evidence_receipt"],
                (workflow_key, self.mode, scheduled_for))
            if len(rows) != 1:
                raise InputUnavailable(builder_key, "requires exactly one immutable bound NPI receipt")
            row = rows[0]
            receipt_id, device_id, observed_at = row.get("id"), row.get("device_id"), row.get("observed_at")
            release, checksum, results = row.get("source_release"), row.get("source_checksum"), row.get("results")
            if not all(isinstance(value, str) and value for value in
                       (receipt_id, device_id, observed_at, release, checksum)) or not isinstance(results, list):
                raise InputUnavailable(builder_key, "bound NPI receipt is malformed")
            try:
                raw = json.loads((REPO / "ops/config/npi-sweep-policy.v1.json").read_text(encoding="utf-8"))
                policy = load_policy(raw)
                taxonomy = policy.get("taxonomy")
                approved = taxonomy.get("approved_codes") if isinstance(taxonomy, Mapping) else None
                if not isinstance(approved, list) or not approved or not all(isinstance(code, str) and code for code in approved):
                    raise InputUnavailable(builder_key, "missing human-reviewed exact healthcare taxonomy allowlist")
                candidates = filter_candidates(results, policy=policy, approved_taxonomy_codes=approved,
                                               as_of=scheduled_for)
            except (OSError, ValueError, NpiInputError) as exc:
                raise InputUnavailable(builder_key, f"NPI policy/evidence refused: {exc}") from exc
            if not candidates:
                raise InputUnavailable(builder_key, "NPI receipt has no current in-territory approved candidates")
            lanes = [{"lane": candidate["source_ref"], "territory_match": True,
                      "entity_type": "healthcare_provider"} for candidate in candidates]
            return [{"source_kind": "device",
                     "source_ref": f"npi-device-receipt:{receipt_id}:{device_id}:{release}:{checksum}",
                     "values": {"lanes": lanes, "freshness_cutoff": scheduled_for.isoformat(),
                                "delta_state": "unprocessed", "npi_candidates": candidates,
                                "source_release": release, "source_checksum": checksum}}]
        if builder_key in AUDIT_BUILDERS:
            return [audit_evidence_envelope(
                builder_key, workflow_key, self.reader,
                mode=self.mode, scheduled_for=self._scheduled_for())]
        if builder_key in QUERIES:
            policies = {
                name: self.policy.read_object(name)
                for name in ("entity_research_policy", "deal_history_research_policy")
            }
            return CanonicalRecordCollector(
                self.reader, scheduled_for=self._scheduled_for(), mode=self.mode,
                policies=policies).collect(
                builder_key=builder_key, workflow_key=workflow_key)
        if builder_key in SOCIAL_BUILDERS:
            scheduled_for = self._scheduled_for()
            return SocialCanonicalCollector(
                CanonicalSocialQuery(self.reader, scheduled_for), self.policy,
                JobReceiptReader(self.reader, mode=self.mode, scheduled_for=scheduled_for)
            ).collect(builder_key=builder_key, workflow_key=workflow_key)
        raise InputUnavailable(builder_key, "no registered runtime collector")
