#!/usr/bin/env python3
"""Hermetic acceptance checks for immediate failure-to-incident wiring."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
RECORD = REPO / "tools" / "ops-record.py"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


class FakeCursor:
    """Only the incident statements used by the focused helper."""

    def __init__(self) -> None:
        self.incidents: list[dict] = []
        self.links: set[tuple[str, str, str]] = set()
        self.facts: list[tuple[str, str, str]] = []
        self.events: list[str] = []
        self._one: tuple[Any, ...] | None = None

    def execute(self, sql: str, params=()) -> None:
        normalized = " ".join(sql.split())
        if "pg_advisory_xact_lock" in normalized:
            if "ops.incident.ref-allocation" in normalized:
                self.events.append("ref_lock")
            else:
                self.events.append("correlation_lock")
            self._one = (None,)
        elif ("from ops.incident i" in normalized
              and "i.correlation_id = %s" in normalized
              and normalized.startswith("select i.id")):
            self.events.append("correlation_lookup")
            corr = params[0]
            correlation_ref = f"correlation:{corr}"
            row = next((
                x for x in self.incidents
                if x["correlation"] == corr
                or any(incident == x["id"] and source_ref == correlation_ref
                       for incident, _, source_ref in self.facts)
            ), None)
            self._one = (row["id"],) if row else None
        elif "from ops.incident" in normalized and "signature = %s" in normalized:
            signature = params[0]
            row = next((x for x in self.incidents if x["signature"] == signature), None)
            self._one = (row["id"],) if row else None
        elif "coalesce(max(substring(ref" in normalized:
            self.events.append("ref_allocate")
            self._one = (len(self.incidents) + 1,)
        elif normalized.startswith("insert into ops.incident "):
            incident_id = f"incident-{len(self.incidents) + 1}"
            self.incidents.append({
                "id": incident_id,
                "correlation": params[1],
                "signature": params[-2],
            })
            self._one = (incident_id,)
        elif normalized.startswith("insert into ops.incident_link "):
            link = (params[0], params[1], params[2])
            inserted = link not in self.links
            self.links.add(link)
            self._one = (params[0],) if inserted else None
        elif (normalized.startswith("insert into ops.incident_fact ")
              and "select %s, %s, %s" in normalized):
            incident_id, fact, source_ref = params[:3]
            original = next(x for x in self.incidents if x["id"] == incident_id)
            inserted = (original["correlation"] != params[4]
                        and not any(i == incident_id and ref == source_ref
                                    for i, _, ref in self.facts))
            if inserted:
                self.facts.append((incident_id, fact, source_ref))
            self._one = ("fact-correlation",) if inserted else None
        elif normalized.startswith("insert into ops.incident_fact "):
            self.facts.append((params[0], params[1], params[2]))
            self._one = None
        else:
            raise AssertionError(f"unexpected SQL: {normalized[:180]}")

    def fetchone(self):
        return self._one


def main() -> int:
    print("immediate-incident-selftest: failed writes raise one linked incident")
    spec = importlib.util.spec_from_file_location("ops_record_incident_test", RECORD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    helper = getattr(module, "_record_failure_incident", None)
    check("1. one shared immediate-incident helper exists", callable(helper))
    if not callable(helper):
        return 1

    cur = FakeCursor()
    common = dict(
        cur=cur,
        service_id="service-1",
        service_key="carr-mcp",
        criticality="critical",
        environment="production",
    )
    helper(**common, correlation_id="11111111-1111-4111-8111-111111111111",
           source_kind="run", source_id="run-1", source_label="golden.performance",
           state="timed_out", failure_class="performance_gate_failed",
           detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="11111111-1111-4111-8111-111111111111",
           source_kind="deployment", source_id="deployment-1",
           source_label="worker promotion", state="failed",
           failure_class="production_readback_mismatch",
           detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="22222222-2222-4222-8222-222222222222",
           source_kind="run", source_id="run-2", source_label="backup.verify",
           state="failed", failure_class="backup_verification_failed",
           detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="11111111-1111-4111-8111-111111111111",
           source_kind="run", source_id="run-1", source_label="golden.performance",
           state="timed_out", failure_class="performance_gate_failed",
           detail="CANARY-CLIENT-SECRET")

    check("2. same correlation deduplicates to one open incident",
          len(cur.incidents) == 2, f"incidents={cur.incidents}")
    check("2a. each helper call locks correlation before incident lookup",
          cur.events.count("correlation_lock") == 4
          and cur.events[0:2] == ["correlation_lock", "correlation_lookup"],
          f"events={cur.events}")
    ref_locks = [i for i, event in enumerate(cur.events) if event == "ref_lock"]
    ref_allocations = [i for i, event in enumerate(cur.events)
                       if event == "ref_allocate"]
    check("2aa. new refs serialize globally before max+1 allocation",
          len(ref_locks) == len(ref_allocations) == 2
          and all(lock_at < alloc_at
                  for lock_at, alloc_at in zip(ref_locks, ref_allocations)),
          f"events={cur.events}")
    first_id = cur.incidents[0]["id"] if cur.incidents else "missing"
    first_links = {(kind, ref) for incident, kind, ref in cur.links
                   if incident == first_id}
    check("2b. failed run and failed deployment both link to that incident",
          first_links == {("run", "run-1"), ("deployment", "deployment-1")},
          f"links={first_links}")
    check("2c. each new link adds one sourced fact", len(cur.facts) == 3)
    check("2cc. replaying the same source link adds no duplicate fact",
          len(cur.links) == 3 and len(cur.facts) == 3)
    check("2d. facts are redacted rather than copying caller detail",
          all("CANARY-CLIENT-SECRET" not in text for _, text, _ in cur.facts))

    recurring = FakeCursor()
    recurring_common = dict(common, cur=recurring)
    old_corr = "55555555-5555-4555-8555-555555555555"
    new_corr = "66666666-6666-4666-8666-666666666666"
    helper(**recurring_common, correlation_id=old_corr,
           source_kind="run", source_id="run-old",
           source_label="performance.release", state="failed",
           failure_class="performance_budget_exceeded", detail=None)
    helper(**recurring_common, correlation_id=new_corr,
           source_kind="run", source_id="run-new",
           source_label="performance.release", state="failed",
           failure_class="performance_budget_exceeded", detail=None)
    helper(**recurring_common, correlation_id=new_corr,
           source_kind="deployment", source_id="deployment-new",
           source_label="deployment", state="failed",
           failure_class="production_readback_mismatch", detail=None)
    recurring_links = {(kind, ref) for _, kind, ref in recurring.links}
    correlation_facts = [ref for _, _, ref in recurring.facts
                         if ref.startswith("correlation:")]
    check("2e. a recurring run and its new-correlation deployment stay one journey",
          len(recurring.incidents) == 1
          and recurring_links == {
              ("run", "run-old"), ("run", "run-new"),
              ("deployment", "deployment-new")},
          f"incidents={recurring.incidents} links={recurring_links}")
    check("2f. recurrence correlation is traceable and idempotent",
          correlation_facts == [f"correlation:{new_corr}"],
          f"correlation facts={correlation_facts}")

    before = (len(cur.incidents), len(cur.links), len(cur.facts))
    helper(**common, correlation_id="33333333-3333-4333-8333-333333333333",
           source_kind="run", source_id="run-skip", source_label="optional.step",
           state="skipped", failure_class=None, detail="CANARY-CLIENT-SECRET")
    helper(**common, correlation_id="44444444-4444-4444-8444-444444444444",
           source_kind="run", source_id="run-cancel", source_label="cancelled.step",
           state="cancelled", failure_class=None, detail="CANARY-CLIENT-SECRET")
    check("3. skipped and cancelled observations open nothing",
          before == (len(cur.incidents), len(cur.links), len(cur.facts)))

    source = RECORD.read_text(encoding="utf-8")
    deployment_body = source[source.index("def cmd_deployment"):source.index("# ── release")]
    check("4. run and deployment call the helper in their insert transaction",
          source.count("_record_failure_incident(") >= 4
          and source.count("conn.transaction()") >= 2
          and "_record_failure_incident(" in deployment_body)
    check("4a. helper takes a correlation advisory transaction lock",
          "pg_advisory_xact_lock" in source)
    check("4aa. helper takes the global ref-allocation lock after correlation",
          "ops.incident.ref-allocation" in source)
    check("4b. routine/write authority modes remain distinct",
          'connect("routine")' in source and 'connect("write")' in source)

    print()
    if FAILURES:
        print(f"immediate-incident-selftest: {len(FAILURES)} FAILED")
        return 1
    print("immediate-incident-selftest: immediate incidents are linked and deduplicated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
