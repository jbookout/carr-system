#!/usr/bin/env python3
"""Behavioral checks for the runtime collector's read-only adapter boundary."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.control_plane_inputs import InputUnavailable
from lib.control_plane_runtime_collectors import (SOCIAL_SQL, PsycopgReadOnlyAdapter,
                                                   RuntimeCanonicalEvidenceCollector)


class Description:
    def __init__(self, name): self.name = name


class Cursor:
    def __init__(self):
        self.calls = []
        self.description = [Description("value")]

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=()): self.calls.append((sql, tuple(params)))
    def fetchall(self): return [("canonical",)]


class Connection:
    def __init__(self, cursor): self.value = cursor
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return self.value


def check(label, value):
    if not value: raise AssertionError(label)
    print("ok:", label)


def main() -> int:
    cursor = Cursor()
    adapter = PsycopgReadOnlyAdapter(lambda: Connection(cursor))
    rows = adapter.fetch_all("social_next_week_coverage", SOCIAL_SQL["social_next_week_coverage"],
                             ("2026-08-17", "2026-08-17"))
    check("collector enters a read-only transaction before its SELECT",
          cursor.calls == [("set transaction read only", ()),
                           (SOCIAL_SQL["social_next_week_coverage"].strip(),
                            ("2026-08-17", "2026-08-17"))])
    check("collector maps named columns without leaking a connection object", rows == [{"value": "canonical"}])
    try: adapter.fetch_all("social_next_week_coverage", "select unreviewed")
    except InputUnavailable: refused = True
    else: refused = False
    check("unregistered collector SQL refuses before execution", refused)
    device = [{"source_kind":"device", "source_ref":"device:platform-export",
               "values":{"platform_exports":[{"placement_id":"p1"}]}}]
    collector = RuntimeCanonicalEvidenceCollector(
        {"scheduled_for":"2026-08-16T14:00:00+00:00", "device_source_evidence": device},
        mode="shadow", connect_factory=lambda: Connection(Cursor()),
        policy_path=ROOT / "ops/config/control-plane-collector-policy.v1.json")
    try: list(collector.collect(builder_key="linkedin.source-posts", workflow_key="linkedin-engagement-daily"))
    except InputUnavailable: refused = True
    else: refused = False
    check("scheduler device payload cannot satisfy a device builder", refused)
    class BoundReader:
        def fetch_all(self, query_key, sql, params=()):
            if query_key != "device_evidence_receipt": return []
            check("device receipt lookup binds workflow, builder, mode, and scheduled instant",
                  params == ("linkedin-engagement-daily", "linkedin.source-posts", "shadow",
                             __import__('datetime').datetime.fromisoformat("2026-08-16T14:00:00+00:00")))
            return [{"id":"receipt-1", "device_id":"joe-mac",
                     "observed_at":"2026-08-16T13:55:00+00:00",
                     "evidence":{"platform":"linkedin", "collector_state":"available",
                                 "voice_version":1,
                                 "source_posts":[
                                   {"url":"https://linkedin.test/1","network_priority":True},
                                   {"url":"https://linkedin.test/2","network_priority":True},
                                   {"url":"https://linkedin.test/3","network_priority":False}]}}]
    setattr(collector, "reader", BoundReader())
    evidence = list(collector.collect(
        builder_key="linkedin.source-posts", workflow_key="linkedin-engagement-daily"))
    check("immutable bound device receipt becomes typed device evidence",
          evidence[0]["source_kind"] == "device"
          and evidence[0]["source_ref"].startswith("device-receipt:receipt-1:joe-mac:"))
    class BoundNpiReader:
        def fetch_all(self, query_key, sql, params=()):
            if query_key != "npi_device_evidence_receipt": return []
            check("NPI receipt lookup binds workflow, mode, and scheduled instant",
                  params == ("npi-sweep-weekly", "shadow",
                             __import__('datetime').datetime.fromisoformat("2026-08-16T14:00:00+00:00")))
            return [{"id":"npi-receipt-1", "device_id":"joe-mac", "observed_at":"2026-08-16T13:55:00+00:00",
                     "source_release":"2026w33", "source_checksum":"a" * 64,
                     "results":[{"source_ref":"nppes:weekly:1", "npi":"1234567890", "enumeration_type":"NPI-2",
                                  "last_updated":"2026-08-10T00:00:00Z", "addresses":[{"postal_code":"32501"}],
                                  "taxonomies":["207Q00000X"]}]}]
    setattr(collector, "reader", BoundNpiReader())
    try: list(collector.collect(builder_key="npi.weekly-delta", workflow_key="npi-sweep-weekly"))
    except InputUnavailable as exc: refused = "taxonomy allowlist" in str(exc)
    else: refused = False
    check("NPI receipt stays fail-closed until a human-reviewed taxonomy allowlist exists", refused)
    try: list(collector.collect(builder_key="unregistered", workflow_key="x"))
    except InputUnavailable: refused = True
    else: refused = False
    check("unregistered runtime builder refuses", refused)
    return 0


if __name__ == "__main__": raise SystemExit(main())
