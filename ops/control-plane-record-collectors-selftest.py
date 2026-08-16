#!/usr/bin/env python3
"""Contract checks for canonical record cognition collectors (no database)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.control_plane_collectors_records import CanonicalRecordCollector, QUERIES
from lib.control_plane_inputs import InputUnavailable


class FakeReadOnlyAdapter:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def fetch_all(self, *, query_key, sql, params=()):
        self.calls.append((query_key, sql, tuple(params)))
        return self.rows.get(query_key, [])


def check(label, condition):
    if not condition: raise AssertionError(label)
    print(f"ok: {label}")


def main() -> int:
    instant = datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    policies = {
        "entity_research_policy": {"mode":"direct-primary-sources", "proposal_only":True},
        "deal_history_research_policy": {"mode":"direct-identity-sources", "proposal_only":True},
    }
    enrichment = [{"subject_type":"party", "subject_id":str(i),
                   "reverification_due":"expired", "current_verification_status":"not_current",
                   "priority":i, "expired_at":f"2026-01-{i:02d}"} for i in range(1, 41)]
    rows = {
        "entity-enrichment.next-40": enrichment,
        "deal-history.next-slice": [{"subject_type":"candidate", "subject_id":str(i),
          "verification":"unverified", "priority":i, "source_class":"canonical_counterparty",
          "slice_limit":15, "enrichment_subject_count":30,
          "enrichment_scheduled_for":"2026-08-13T14:00:00+00:00",
          "enrichment_mode":"shadow"} for i in range(15)],
        "content-fuel.next-rotation": [
          {"lane":"local", "temperature":"local", "source_class":"primary", "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"},
          {"lane":"cold", "temperature":"cold", "source_class":"primary", "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"}],
        "npi.weekly-delta": [{"lane":"npi", "territory_match":True, "entity_type":"healthcare_provider", "freshness_cutoff":"2026-08-01T00:00:00Z", "delta_state":"unprocessed"}],
        "radar.weekly-candidates": [
          {"lane":"corp", "score":4, "fresh":True, "overdue":True, "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"},
          {"lane":"renewal", "score":2.5, "fresh":True, "overdue":False, "freshness_cutoff":"2026-08-01T00:00:00Z", "previous_receipt_state":"absent"}],
    }
    adapter = FakeReadOnlyAdapter(rows)
    collector = CanonicalRecordCollector(adapter, scheduled_for=instant, mode="shadow", policies=policies)
    for key in QUERIES:
        evidence = collector.collect(builder_key=key, workflow_key="fixture")
        check(f"{key} emits canonical provenance", evidence[0]["source_kind"] == "canonical_db" and evidence[0]["source_ref"] == f"db:{key}")
        check(f"{key} has structured predicate input",
              isinstance(evidence[0].get("values"), dict))
    check("all collector queries use SELECT-only read surfaces",
          len(adapter.calls) == len(QUERIES) and all(call[1].lstrip().lower().startswith("select")
                                                   for call in adapter.calls))
    bad = dict(rows)
    bad["entity-enrichment.next-40"] = enrichment[:-1]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(bad), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="entity-enrichment.next-40", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("partial enrichment queue fails closed", refused)
    bad = dict(rows)
    bad["npi.weekly-delta"] = [{"lane":"npi", "territory_match":False,
                                 "entity_type":"healthcare_provider",
                                 "freshness_cutoff":"2026-08-01T00:00:00Z",
                                 "delta_state":"unprocessed"}]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(bad), scheduled_for=instant, mode="shadow",
                                 policies=policies).collect(builder_key="npi.weekly-delta", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("semantic predicate violation fails closed", refused)
    tail = dict(rows)
    tail["deal-history.next-slice"] = [{"subject_type":"party", "subject_id":str(i),
        "verification":"unverified", "priority":i, "source_class":"canonical_counterparty",
        "slice_limit":25, "enrichment_subject_count":12,
        "enrichment_scheduled_for":"2026-08-13T14:00:00+00:00",
        "enrichment_mode":"shadow"} for i in range(3)]
    evidence = CanonicalRecordCollector(FakeReadOnlyAdapter(tail), scheduled_for=instant,
                                        mode="shadow", policies=policies).collect(
        builder_key="deal-history.next-slice", workflow_key="fixture")
    check("final partial deal-history slice stays within its canonical cap",
          evidence[0]["values"]["slice_limit"] == 25 and len(evidence[0]["values"]["subjects"]) == 3)
    fabricated = dict(rows)
    fabricated["entity-enrichment.next-40"] = [
        {**row, "current_verification_status":"current"} for row in enrichment
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(fabricated), scheduled_for=instant,
                                 mode="shadow", policies=policies).collect(
            builder_key="entity-enrichment.next-40", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("expired evidence cannot be relabeled as currently verified", refused)
    wrong_mode = dict(rows)
    deal_rows = rows["deal-history.next-slice"]
    assert isinstance(deal_rows, list)
    wrong_mode["deal-history.next-slice"] = [
        {**row, "enrichment_mode":"canary"} for row in deal_rows
        if isinstance(row, dict)
    ]
    try:
        CanonicalRecordCollector(FakeReadOnlyAdapter(wrong_mode), scheduled_for=instant,
                                 mode="shadow", policies=policies).collect(
            builder_key="deal-history.next-slice", workflow_key="fixture")
    except InputUnavailable: refused = True
    else: refused = False
    check("another mode's Thursday receipt cannot size this job", refused)
    return 0


if __name__ == "__main__": raise SystemExit(main())
