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
import subprocess
import sys
import tempfile
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


NOW = "2026-09-09T12:00:00+00:00"
MAX_AGE = 900


def _declaration(key, version=1, *, enabled=True, provider="claude-code", status="enabled"):
    return {"key": key, "version": version, "enabled": enabled,
            "legacy_schedule": {"provider": provider, "status": status}}


def _definition(key, version=1, *, enabled=True, canary=None, legacy_disabled_at=None):
    contract = {} if canary is None else {"canary": {"enabled": canary}}
    return {"key": key, "version": version, "enabled": enabled,
            "execution_contract": contract, "legacy_disabled_at": legacy_disabled_at}


def _acceptance(key, mode, status, version=1):
    return {"workflow_key": key, "workflow_version": version, "mode": mode, "status": status}


def _surface(key, surface_id, kind, *, version=1, group=None, receipt=None, observation=None):
    return {"workflow_key": key, "workflow_version": version, "surface_id": surface_id,
            "locator": surface_id, "scheduler_kind": kind, "duplicate_group": group,
            "disable_receipt_ref": receipt, "observation": observation}


def _project(truth, **kwargs):
    payload = {"declarations": [], "definitions": [], "acceptances": [], "surfaces": [],
               "completion": {}, "observation_max_age_seconds": MAX_AGE, "now": NOW}
    payload.update(kwargs)
    return {row["workflow_key"]: row for row in truth(**payload)["rows"]}


def workflow_truth_checks(manifest) -> None:
    """The V5-F09 clean-start census: closed output, and a fence it cannot weaken."""
    try:
        from lib.control_plane_workflow_truth import (
            DISPOSITIONS, EVIDENCE_STATES, STATES, UNREADABLE,
            WorkflowTruthContractError, completion_subject_key, workflow_truth,
        )
    except Exception as exc:
        check("workflow truth adapter imports", False, str(exc))
        return

    # ---- the closed matrix over one enabled workflow's evidence ladder -------
    # ops.enqueue_job (0334) is the only admission path. These cases assert the
    # projection agrees with that function rung for rung, and NEVER claims an
    # admission it would refuse.
    rows = _project(
        workflow_truth,
        declarations=[_declaration("bare"), _declaration("shadowed"),
                      _declaration("canaried"), _declaration("nocanary"),
                      _declaration("off", enabled=False),
                      _declaration("absent")],
        definitions=[_definition("bare"), _definition("shadowed"), _definition("canaried"),
                     _definition("nocanary", canary=False),
                     _definition("off", enabled=False)],
        acceptances=[_acceptance("shadowed", "shadow", "accepted"),
                     _acceptance("canaried", "shadow", "accepted"),
                     _acceptance("canaried", "canary", "accepted"),
                     _acceptance("nocanary", "shadow", "accepted")],
    )
    check("an enabled definition with no acceptance can only run a shadow evidence run",
          rows["bare"]["state"] == "enabled_shadow_only"
          and rows["bare"]["admissible_modes"] == ["shadow", "replay"]
          and rows["bare"]["operational"] is False,
          json.dumps(rows["bare"]["admissible_modes"]))
    check("an accepted shadow unlocks canary and nothing further",
          rows["shadowed"]["state"] == "enabled_canary_eligible"
          and "canary" in rows["shadowed"]["admissible_modes"]
          and "live" not in rows["shadowed"]["admissible_modes"])
    check("an accepted canary unlocks live but does not make the workflow operational",
          rows["canaried"]["state"] == "enabled_live_eligible"
          and "live" in rows["canaried"]["admissible_modes"]
          and rows["canaried"]["operational"] is False)
    check("a contractually canary-disabled workflow reaches live on accepted shadow "
          "and still cannot enqueue canary",
          rows["nocanary"]["state"] == "enabled_live_eligible"
          and "live" in rows["nocanary"]["admissible_modes"]
          and "canary" not in rows["nocanary"]["admissible_modes"]
          and rows["nocanary"]["canary_contractually_disabled"] is True)
    check("a disabled definition admits no mode at all",
          rows["off"]["state"] == "declared_disabled" and rows["off"]["admissible_modes"] == [])
    check("a declared workflow with no definition row is admitted through the lifecycle, "
          "never assumed present",
          rows["absent"]["state"] == "unregistered"
          and rows["absent"]["disposition"] == "admit_via_governed_lifecycle")

    # ENABLED IS NOT OPERATIONAL. Every enabled-but-unproven row above carries
    # the false_operational label, and no row without completion evidence is
    # ever promoted -- the exact untruth Q062 asks the clean start to remove.
    check("enabled without the acceptance its live tier requires is labelled false_operational",
          rows["bare"]["false_operational"] is True
          and rows["shadowed"]["false_operational"] is True
          and rows["canaried"]["false_operational"] is False)
    check("no workflow is operational without completion evidence saying so",
          not any(row["operational"] for row in rows.values()))

    operational = _project(
        workflow_truth,
        declarations=[_declaration("proven")], definitions=[_definition("proven")],
        acceptances=[_acceptance("proven", "shadow", "accepted"),
                     _acceptance("proven", "canary", "accepted")],
        completion={completion_subject_key("proven", 1):
                    {"lifecycle_state": "operational", "first_path": True}})
    check("live admission plus operational completion evidence is the only route to operational",
          operational["proven"]["state"] == "operational"
          and operational["proven"]["disposition"] == "retain_operational")

    # ---- conflicting reuse, and the fence it must not weaken ----------------
    conflicting = _project(
        workflow_truth,
        declarations=[_declaration("split")], definitions=[_definition("split")],
        acceptances=[_acceptance("split", "shadow", "accepted"),
                     _acceptance("split", "shadow", "rejected"),
                     _acceptance("split", "canary", "accepted")])
    check("accepted and rejected evidence for one mode is a conflict that holds",
          conflicting["split"]["state"] == "conflict"
          and conflicting["split"]["disposition"] == "hold_for_disposition"
          and conflicting["split"]["evidence"]["shadow_acceptance"] == "conflicting")
    check("a conflict label cannot weaken the database's own canary/live fence",
          "live" in conflicting["split"]["admissible_modes"],
          "the projection must still report what ops.enqueue_job would admit")

    # ---- freshness comes only from where it already exists ------------------
    stale = _project(
        workflow_truth,
        declarations=[_declaration("aged")], definitions=[_definition("aged")],
        acceptances=[_acceptance("aged", "shadow", "accepted")],
        surfaces=[_surface("aged", "aged.claude-code.v1", "claude-code",
                           observation={"scheduler_state": "enabled",
                                        "observed_at": "2026-09-09T11:00:00+00:00"})])
    fresh = _project(
        workflow_truth,
        declarations=[_declaration("recent")], definitions=[_definition("recent")],
        acceptances=[_acceptance("recent", "shadow", "accepted")],
        surfaces=[_surface("recent", "recent.claude-code.v1", "claude-code",
                           observation={"scheduler_state": "enabled",
                                        "observed_at": "2026-09-09T11:55:00+00:00"})])
    check("a native scheduler observation outside the registry's own window reads stale",
          stale["aged"]["evidence"]["native_schedule"] == "stale"
          and fresh["recent"]["evidence"]["native_schedule"] == "observed")
    check("acceptance evidence is never stale: ops.workflow_acceptance has no expiry to read",
          all(row["evidence"]["shadow_acceptance"] != "stale"
              and row["evidence"]["canary_acceptance"] != "stale"
              for row in list(rows.values()) + list(stale.values()) + list(fresh.values())))
    check("a surface with no observation reads missing, never absent-as-fine",
          _project(workflow_truth, declarations=[_declaration("unseen")],
                   definitions=[_definition("unseen")],
                   surfaces=[_surface("unseen", "unseen.claude-code.v1", "claude-code")]
                   )["unseen"]["evidence"]["native_schedule"] == "missing")

    # ---- THE DUPLICATE BOUNDARY (the mandatory correction) ------------------
    # Same-workflow same-slot idempotency is retry deduplication. It says nothing
    # about two DISTINCT registered identities sharing one duplicate_group, and
    # the projection must model the two cases separately.
    one_identity = _project(
        workflow_truth,
        declarations=[_declaration("notes")], definitions=[_definition("notes")],
        surfaces=[_surface("notes", "notes.launchd.v1", "launchd", group="notes.legacy"),
                  _surface("notes", "notes.claude-code.v1", "claude-code", group="notes.legacy")])
    check("a duplicate group of two surfaces on ONE identity is covered by same-slot dedup",
          one_identity["notes"]["duplicate"] is True
          and one_identity["notes"]["duplicate_exclusion"]["distinct_identity"]
          == "not_applicable_single_identity"
          and one_identity["notes"]["disposition"] == "retire_duplicate")

    two_identities = workflow_truth(
        declarations=[_declaration("twin-a"), _declaration("twin-b")],
        definitions=[_definition("twin-a"), _definition("twin-b")],
        acceptances=[], completion={},
        surfaces=[_surface("twin-a", "twin-a.launchd.v1", "launchd", group="twins.legacy"),
                  _surface("twin-b", "twin-b.claude-code.v1", "claude-code", group="twins.legacy")],
        observation_max_age_seconds=MAX_AGE, now=NOW)
    twins = {row["workflow_key"]: row for row in two_identities["rows"]}
    check("two DISTINCT registered identities in one duplicate_group are reported as an "
          "exclusion the queue does not enforce today",
          all(twins[key]["duplicate_exclusion"]["distinct_identity"] == "unenforced_pending_phase_b"
              for key in ("twin-a", "twin-b"))
          and two_identities["summary"]["unenforced_distinct_identity_groups"] == ["twins.legacy"])
    check("the distinct-identity case is never described as covered by same-slot idempotency",
          all(twins[key]["duplicate_exclusion"]["same_slot_idempotency"]
              == "enforced_by_ops_enqueue_job"
              and twins[key]["duplicate_exclusion"]["distinct_identity"]
              != twins[key]["duplicate_exclusion"]["same_slot_idempotency"]
              for key in ("twin-a", "twin-b")))
    closed = _project(
        workflow_truth,
        declarations=[_declaration("retired", status="disabled")],
        definitions=[_definition("retired", legacy_disabled_at="2026-09-01T00:00:00+00:00")],
        acceptances=[_acceptance("retired", "shadow", "accepted"),
                     _acceptance("retired", "canary", "accepted")],
        surfaces=[_surface("retired", "retired.launchd.v1", "launchd", group="retired.legacy",
                           receipt="legacy-disable:one"),
                  _surface("retired", "retired.claude-code.v1", "claude-code",
                           group="retired.legacy", receipt="legacy-disable:two")])
    check("a duplicate group stays duplicate until the immutable disable evidence closes it",
          closed["retired"]["duplicate"] is True
          and closed["retired"]["duplicate_open"] is False
          and closed["retired"]["disposition"] != "retire_duplicate")

    # ---- requested dispositions are requests, never effects ------------------
    requested = [row for row in (list(one_identity.values()) + list(rows.values()))
                 if row["requested_effect"]]
    check("every requested scheduler disposition names its authority path and is unapplied",
          bool(requested) and all(
              effect["applied"] is False and "disable_legacy_schedule" in effect["authority_path"]
              for effect in (row["requested_effect"] for row in requested)))
    native_off = _project(
        workflow_truth,
        declarations=[_declaration("nonative", provider="none", status="disabled")],
        definitions=[_definition("nonative")])
    check("a false-operational workflow with no native scheduler asks for no disablement",
          native_off["nonative"]["false_operational"] is True
          and native_off["nonative"]["disposition"] == "hold_for_disposition"
          and native_off["nonative"]["evidence"]["native_schedule"] == "not_required")

    # ---- repair_first_path only from a bound relation -----------------------
    bound = _project(
        workflow_truth,
        declarations=[_declaration("first", provider="none", status="disabled")],
        definitions=[_definition("first")],
        acceptances=[_acceptance("first", "shadow", "accepted"),
                     _acceptance("first", "canary", "accepted")],
        completion={completion_subject_key("first", 1):
                    {"lifecycle_state": "active_unproven", "first_path": True}})
    unbound = _project(
        workflow_truth,
        declarations=[_declaration("later", provider="none", status="disabled")],
        definitions=[_definition("later")],
        acceptances=[_acceptance("later", "shadow", "accepted"),
                     _acceptance("later", "canary", "accepted")],
        completion={completion_subject_key("later", 1):
                    {"lifecycle_state": "active_unproven", "first_path": False}})
    check("repair_first_path is reported only from a bound first-path relation",
          bound["first"]["disposition"] == "repair_first_path"
          and unbound["later"]["disposition"] == "hold_for_disposition")

    # ---- fail closed on what could not be read ------------------------------
    unreadable_ladder = _project(
        workflow_truth, declarations=[_declaration("dark")], definitions=UNREADABLE)
    check("an unreadable definition makes the state unknown and holds the disposition",
          unreadable_ladder["dark"]["state"] == "unknown"
          and unreadable_ladder["dark"]["disposition"] == "hold_for_disposition")
    unreadable_register = _project(
        workflow_truth, declarations=[_declaration("held")], definitions=[_definition("held")],
        acceptances=[_acceptance("held", "shadow", "accepted"),
                     _acceptance("held", "canary", "accepted")],
        completion=UNREADABLE)
    check("an unreadable Completion Register caps promotion and holds, but keeps the ladder",
          unreadable_register["held"]["state"] == "enabled_live_eligible"
          and unreadable_register["held"]["operational"] is False
          and unreadable_register["held"]["disposition"] == "hold_for_disposition"
          and unreadable_register["held"]["evidence"]["completion"] == "missing")

    refusals = []
    for label, payload in (
        ("unknown acceptance status",
         {"acceptances": [_acceptance("x", "shadow", "believed")]}),
        ("unknown scheduler kind",
         {"surfaces": [_surface("x", "x.cron.v1", "cron")]}),
        ("completion without an explicit bound first_path",
         {"completion": {completion_subject_key("x", 1): {"lifecycle_state": "operational"}}}),
    ):
        try:
            _project(workflow_truth, declarations=[_declaration("x")],
                     definitions=[_definition("x")], **payload)
        except WorkflowTruthContractError:
            continue
        refusals.append(label)
    check("a malformed authoritative input is refused rather than guessed at",
          not refusals, f"accepted={refusals}")

    # ---- the real registry, projected end to end ----------------------------
    registry = json.loads(
        (REPO / "ops" / "config" / "control-plane-scheduler-cutover.v1.json")
        .read_text(encoding="utf-8"))
    live_surfaces = [
        _surface(str(s["workflow_key"]), str(s["surface_id"]), str(s["scheduler_kind"]),
                 version=int(s["workflow_version"]), group=s.get("duplicate_group"))
        for s in registry["surfaces"]]
    live = workflow_truth(
        declarations=manifest["workflows"],
        definitions=[{"key": w["key"], "version": w["version"], "enabled": w["enabled"],
                      "execution_contract": {k: v for k, v in w["execution"].items()
                                             if k != "kind"},
                      "legacy_disabled_at": None} for w in manifest["workflows"]],
        acceptances=[], surfaces=live_surfaces, completion={},
        observation_max_age_seconds=int(registry["observation_max_age_seconds"]), now=NOW)
    check("every declared workflow gets exactly one closed state and one closed disposition",
          len(live["rows"]) == len(manifest["workflows"])
          and all(row["state"] in STATES and row["disposition"] in DISPOSITIONS
                  and all(value in EVIDENCE_STATES for value in row["evidence"].values())
                  for row in live["rows"])
          and sum(live["summary"]["states"].values()) == len(live["rows"])
          and sum(live["summary"]["dispositions"].values()) == len(live["rows"]))
    check("the checked-in registry's freshness window is the one the census uses, "
          "never a window this module invented",
          live["observation_max_age_seconds"] == registry["observation_max_age_seconds"])
    check("the live census reports today's clean start truthfully: no workflow is operational "
          "and the enabled-without-evidence rows are named",
          live["summary"]["states"]["operational"] == 0
          and live["summary"]["false_operational"] > 0,
          json.dumps(live["summary"]["states"]))

    health_truth_checks(live)


def health_truth_checks(live) -> None:
    """Operations health renders the SAME projection, and never calls enabled operational."""
    health = REPO / "tools" / "health-check.py"
    with tempfile.TemporaryDirectory(prefix="workflow-truth-health-") as td:
        carried = Path(td) / "carried.json"
        carried.write_text(json.dumps({
            "observed_at": NOW, "exports": None, "errors": [],
            "job_definitions": [], "jobs": [],
            "workflows": {"available": True, "census": live},
        }), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(health), "--section", "jobs",
                               "--fixture", str(carried)],
                              cwd=REPO, text=True, capture_output=True, timeout=90)
        check("health renders the clean-start census without calling a chosen state a failure",
              proc.returncode == 0 and "Workflow truth" in proc.stdout
              and "evidence-backed operational" in proc.stdout
              and "false-operational" in proc.stdout,
              proc.stdout + proc.stderr)
        check("health separates evidence-run eligibility from operational",
              "evidence-run eligible only (shadow is not operation)" in proc.stdout,
              proc.stdout)

        conflicted = json.loads(json.dumps(live))
        conflicted["rows"][0]["state"] = "conflict"
        conflicted["rows"][0]["reasons"] = ["shadow_acceptance evidence is conflicting"]
        conflicted["summary"]["states"]["conflict"] = 1
        conflict_fixture = Path(td) / "conflict.json"
        conflict_fixture.write_text(json.dumps({
            "observed_at": NOW, "exports": None, "errors": [],
            "job_definitions": [], "jobs": [],
            "workflows": {"available": True, "census": conflicted},
        }), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(health), "--section", "jobs",
                               "--fixture", str(conflict_fixture)],
                              cwd=REPO, text=True, capture_output=True, timeout=90)
        check("contradictory workflow evidence is the one condition that turns health red",
              proc.returncode == 1
              and "CANONICAL_FINDING workflow_truth_conflict" in proc.stdout,
              proc.stdout + proc.stderr)

        missing = Path(td) / "missing.json"
        missing.write_text(json.dumps({
            "observed_at": NOW, "exports": None, "errors": [],
            "job_definitions": [], "jobs": [],
            "workflows": {"available": False, "reason": "no database tap on this machine"},
        }), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(health), "--section", "jobs",
                               "--fixture", str(missing)],
                              cwd=REPO, text=True, capture_output=True, timeout=90)
        check("an unavailable census is printed as unavailable, never as an empty census",
              proc.returncode == 0 and "UNAVAILABLE" in proc.stdout
              and "no database tap on this machine" in proc.stdout,
              proc.stdout + proc.stderr)


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

    engineering = next(w for w in manifest["workflows"] if w["key"] == "engineering-slice")
    check("an enabled on-demand workflow is explicit and has no scheduler",
          engineering["recurrence"].get("kind") == "on_demand"
          and engineering["recurrence"].get("schedule") is None
          and engineering["recurrence"].get("cron") is None)
    forged_on_demand = deepcopy(manifest)
    forged_engineering = next(w for w in forged_on_demand["workflows"]
                              if w["key"] == "engineering-slice")
    forged_engineering["recurrence"].pop("kind")
    forged_errors = validate_manifest(forged_on_demand, repo=REPO)
    check("an enabled scheduleless workflow without on-demand posture is refused",
          any("requires recurrence.cron or an explicit on_demand contract" in error
              for error in forged_errors))

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

    workflow_truth_checks(manifest)

    print(f"\ncontrol-plane-selftest: {total-len(failures)}/{total} passed")
    if failures:
        print("FAILURES: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
