"""Read-only canonical collectors for idea and social cognition inputs.

The collector deliberately has no database connection, filesystem mutation, or
network method.  Its three adapters make the production integration explicit:
canonical queries, a versioned policy file, and immutable receipt reads.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol


class CollectorUnavailable(RuntimeError):
    pass


class CanonicalQuery(Protocol):
    def rows(self, name: str) -> Iterable[Mapping[str, Any]]: ...


class PolicyFile(Protocol):
    def read_object(self, name: str) -> Mapping[str, Any]: ...


class ImmutableReceiptReader(Protocol):
    def latest(self, workflow_key: str) -> Mapping[str, Any] | None: ...


BUILDERS = frozenset({
    "idea-bank.oldest-unsurfaced", "social.next-week-brief", "social.metrics-exports",
})


def _receipt_state(reader: ImmutableReceiptReader, workflow_key: str) -> str:
    receipt = reader.latest(workflow_key)
    if receipt is None:
        return "absent"
    if (not isinstance(receipt, Mapping) or not isinstance(receipt.get("receipt_ref"), str)
            or not receipt["receipt_ref"].strip() or not isinstance(receipt.get("created_at"), str)):
        raise CollectorUnavailable(f"{workflow_key}: immutable receipt is malformed")
    return "present"


def _rows(query: CanonicalQuery, name: str) -> list[Mapping[str, Any]]:
    values = list(query.rows(name))
    if not values or not all(isinstance(row, Mapping) for row in values):
        raise CollectorUnavailable(f"{name}: no canonical rows")
    return values


class SocialCanonicalCollector:
    """EvidenceCollector-compatible collector for three registered builders."""

    def __init__(self, query: CanonicalQuery, policy: PolicyFile,
                 receipts: ImmutableReceiptReader):
        self.query, self.policy, self.receipts = query, policy, receipts

    def collect(self, *, builder_key: str, workflow_key: str):
        if builder_key not in BUILDERS:
            raise CollectorUnavailable(f"unregistered social collector: {builder_key}")
        if builder_key == "idea-bank.oldest-unsurfaced":
            rows = _rows(self.query, "idea_bank_oldest_unsurfaced")
            ideas: list[str] = []
            surfaced: dict[str, str | None] = {}
            for row in rows:
                identifier, title = row.get("id"), row.get("title")
                last = row.get("last_surfaced")
                if not isinstance(identifier, str) or not identifier or not isinstance(title, str) or not title:
                    raise CollectorUnavailable("idea rows require id and title")
                if last is not None and not isinstance(last, str):
                    raise CollectorUnavailable("idea last_surfaced must be ISO text or null")
                ref = f"idea:{identifier}"
                if ref in surfaced:
                    raise CollectorUnavailable("idea rows contain duplicate ids")
                ideas.append(ref)
                surfaced[ref] = last
            return [
                {"source_kind": "canonical_db", "source_ref": "db:idea_bank:oldest_unsurfaced",
                 "values": {"ideas": ideas, "last_surfaced": surfaced}},
                {"source_kind": "canonical_db", "source_ref": f"db:ops.job_receipt:{workflow_key}",
                 "values": {"previous_receipt_state": _receipt_state(self.receipts, workflow_key)}},
            ]
        if builder_key == "social.next-week-brief":
            policy = self.policy.read_object("social_cadence_policy")
            platforms, voice = policy.get("platforms"), policy.get("voice_version")
            if (not isinstance(platforms, list) or not platforms or not all(isinstance(x, str) and x for x in platforms)
                    or not isinstance(voice, int) or isinstance(voice, bool)):
                raise CollectorUnavailable("social policy requires platforms and integer voice_version")
            coverage = _rows(self.query, "social_next_week_coverage")
            if len(coverage) != 1 or coverage[0].get("coverage_state") not in {"covered", "uncovered"}:
                raise CollectorUnavailable("social coverage query must return one typed state")
            refs = []
            for row in _rows(self.query, "social_next_week_sources"):
                source_ref = row.get("source_ref")
                if not isinstance(source_ref, str) or not source_ref:
                    raise CollectorUnavailable("social source rows require source_ref")
                refs.append(source_ref)
            return [
                {"source_kind": "canonical_db", "source_ref": "db:content_piece:next_week_brief",
                 "values": {"platforms": platforms, "source_refs": refs, "voice_version": voice,
                            "coverage_state": coverage[0]["coverage_state"], "cadence": policy.get("cadence"),
                            "topic_rotation": policy.get("topic_rotation"), "reply_mode": policy.get("reply_mode")}},
                {"source_kind": "canonical_db", "source_ref": f"db:ops.job_receipt:{workflow_key}",
                 "values": {"previous_receipt_state": _receipt_state(self.receipts, workflow_key)}},
            ]
        exports = _rows(self.query, "social_metric_exports")
        required = {"placement_id", "external_id", "platform", "source_observed_at",
                    "metric_kind", "metric_value", "window_current", "owned_account",
                    "placement_in_window"}
        if any(not required.issubset(row) or not isinstance(row["placement_id"], str) or not isinstance(row["external_id"], str)
               or not isinstance(row["platform"], str) or not isinstance(row["source_observed_at"], str)
               or not isinstance(row["metric_kind"], str)
               or not isinstance(row["metric_value"], (int, float))
               or isinstance(row["metric_value"], bool)
               or any(type(row[field]) is not bool for field in ("window_current", "owned_account", "placement_in_window"))
               for row in exports):
            raise CollectorUnavailable("metric exports lack typed canonical placement fields")
        return [{"source_kind": "canonical_db", "source_ref": "db:placement:metric_exports",
                 "values": {"platform_exports": [dict(row) for row in exports]}}]
