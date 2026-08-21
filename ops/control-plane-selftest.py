#!/usr/bin/env python3
"""Executable acceptance tests for the CARR control-plane kernel.

These tests are deliberately provider-free and database-free.  They pin the
decisions code must make before a dispatcher, queue adapter, or model provider
is allowed into the path: registry completeness, retry timing, proposal shape,
cache identity, and the evidence required to disable a legacy schedule.
"""
from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

failures: list[str] = []
total = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global total
    total += 1
    ok = bool(condition)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok or not detail else ' — ' + detail}")
    if not ok:
        failures.append(name)


def main() -> int:
    try:
        from lib.control_plane import (
            cache_key,
            can_disable_legacy,
            evaluate_predicate,
            predicate_seed_context,
            retry_delay_seconds,
            validate_manifest,
            validate_proposal,
        )
    except Exception as exc:  # red until the implementation exists
        print(f"control-plane-selftest: implementation unavailable: {exc}")
        return 1

    manifest_path = REPO / "ops" / "config" / "control-plane-workflows.v1.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"control-plane-selftest: manifest unavailable: {exc}")
        return 1

    errors = validate_manifest(manifest, repo=REPO)
    check("manifest validates", not errors, "; ".join(errors[:5]))

    forged_canary = deepcopy(manifest)
    deterministic = next(w for w in forged_canary["workflows"]
                         if w["execution"]["kind"] == "deterministic")
    deterministic["execution"]["canary"] = {
        "enabled": True,
        "args": [],
        "isolation_guard": "manifest-only-claim",
    }
    forged_errors = validate_manifest(forged_canary, repo=REPO)
    check("a manifest edit alone cannot enable deterministic canary execution",
          any("canary isolation guard is not registered" in error
              for error in forged_errors))

    notes_canary = next(workflow for workflow in manifest["workflows"]
                        if workflow["key"] == "notes-sweep-hourly")
    check("Notes canary manifest names the lease-bound isolated aggregate as its completion evidence",
          notes_canary["execution"]["canary"] == {
              "enabled": True, "isolation_guard": "notes-sweep-hourly.canary.v1", "args": ["--canary"]}
          and "lease-bound" in notes_canary["inventory"]["current_completion_signal"]
          and "source snapshot" in notes_canary["completion"]["description"])

    tracked = {p.stem.replace(".SKILL", "") for p in
               (REPO / "ops" / "scheduled-tasks").glob("*.SKILL.md")}
    registered = {w["key"] for w in manifest.get("workflows", [])}
    check("every tracked scheduled-task definition is registered",
          tracked == registered,
          f"missing={sorted(tracked-registered)} extra={sorted(registered-tracked)}")
    check("the live register is not frozen at the council's original 17",
          len(registered) >= 20, f"registered={len(registered)}")

    inventory_fields = {
        "trigger", "owner", "inputs", "canonical_reads", "canonical_writes",
        "external_dependencies", "authority", "current_completion_signal",
        "replacement_program", "acceptance", "retirement_approval",
    }
    incomplete_inventory = [w["key"] for w in manifest["workflows"]
                            if not inventory_fields.issubset(w.get("inventory", {}))]
    check("every scheduled workflow has the complete migration inventory",
          not incomplete_inventory, f"incomplete={incomplete_inventory}")

    decision_fields = ("routing", "filtering", "validation", "completion")
    prose_decisions = [f"{w['key']}.{field}" for w in manifest["workflows"]
                       for field in decision_fields
                       if isinstance(w.get(field), dict) and "predicate" in w[field]]
    check("routing, filtering, validation, and completion use executable specs",
          not prose_decisions, f"prose-only={prose_decisions}")

    unresolved: list[str] = []
    rejected_violations: list[str] = []
    for workflow in manifest["workflows"]:
        for field in decision_fields:
            decision = workflow[field]
            try:
                seed = predicate_seed_context(decision)
                if not evaluate_predicate(decision, seed):
                    unresolved.append(f"{workflow['key']}.{field}: pass")
                violating = dict(seed)
                for fact in decision.get("spec", {}).get("all_of", []):
                    violating[fact] = False
                    break
                if evaluate_predicate(decision, violating):
                    rejected_violations.append(f"{workflow['key']}.{field}")
            except (KeyError, TypeError, ValueError) as exc:
                unresolved.append(f"{workflow['key']}.{field}: {exc}")
    check("every registered decision predicate resolves with a seeded passing case",
          not unresolved, f"unresolved={unresolved}")
    check("every registered decision predicate rejects a seeded violation",
          not rejected_violations, f"accepted-violation={rejected_violations}")

    forbidden = [w["key"] for w in manifest["workflows"]
                 if w.get("execution", {}).get("kind") == "model_session"]
    check("no provider session owns a workflow", not forbidden,
          f"model-owned={forbidden}")

    cognition = {c["key"]: c for c in manifest.get("cognition_jobs", [])}
    unsafe = [k for k, c in cognition.items()
              if c.get("canonical_write_authority") is not False
              or not c.get("input_schema_version")
              or not c.get("output_schema_version")
              or not c.get("budget", {}).get("max_tokens")]
    check("every cognition boundary is typed, versioned, budgeted, proposal-only",
          not unsafe, f"unsafe={unsafe}")

    check("retry backoff is deterministic and capped",
          [retry_delay_seconds(i, 5, 60) for i in range(1, 7)]
          == [5, 10, 20, 40, 60, 60])
    check("cache identity is model-neutral",
          cache_key("draft.comments", 2, {"b": 2, "a": 1}, provider="one")
          == cache_key("draft.comments", 2, {"a": 1, "b": 2}, provider="two"))

    schema = {"type": "object", "required": ["items"],
              "properties": {"items": {"type": "array"}}}
    good = {"job_type": "draft.comments", "schema_version": 2,
            "proposal": {"items": []}}
    check("a typed proposal passes its deterministic envelope",
          validate_proposal(good, "draft.comments", 2, schema) == [])
    bad = {"job_type": "draft.comments", "schema_version": 1,
           "canonical_write": {"table": "lead"}, "proposal": {}}
    check("wrong-version or write-bearing AI output is rejected",
          len(validate_proposal(bad, "draft.comments", 2, schema)) >= 3)

    evidence = [
        {"mode": "shadow", "status": "accepted", "receipt_ref": "run:1"},
        {"mode": "canary", "status": "accepted", "receipt_ref": "run:2"},
    ]
    check("accepted shadow and canary evidence can open retirement",
          can_disable_legacy(evidence, minimum_accepted=2))
    check("an unaccepted run can never disable a legacy schedule",
          not can_disable_legacy(evidence[:1] + [
              {"mode": "canary", "status": "observed", "receipt_ref": "run:3"}],
              minimum_accepted=2))

    print(f"\ncontrol-plane-selftest: {total-len(failures)}/{total} passed")
    if failures:
        print("FAILURES: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
