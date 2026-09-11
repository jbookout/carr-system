#!/usr/bin/env python3
"""Executable acceptance tests for the A01 assurance health projection.

These tests are deliberately provider-free, network-free and database-free: the
projection under test is a pure function, so every fact it needs is supplied
here as data.  They pin the decisions this module must make before any loader,
CLI or persistence phase is allowed near it:

  * the six display states, each reached only by its own evidence, and a
    deterministic precedence between them;
  * a matrix in which EVERY layer, degraded EVERY way the settled contract
    names, never renders green;
  * four preactivation receipts with four distinct exact identities, each of
    which independently blocks activation when it is absent, failed, stale,
    mismatched, self-attested or substituted;
  * an actual business outcome that is its own evidence slot and cannot be
    filled by completion enforcement_closure, merge/CI, enabled configuration,
    telemetry, activation, or a candidate-outcome pass;
  * one workflow degrading act -> draft -> read -> unavailable while an
    unrelated explicitly bound scope keeps its own evidence-derived state,
    byte for byte;
  * F09 workflow truth governing disabled, conflicting and not-operational, with
    no second workflow/status/receipt/incident registry anywhere in the module.

The workflow truth rows below are built by calling the real V5-F09 adapter, not
by hand: if the two modules ever disagree about a workflow's state, these tests
fail rather than paper over it.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from typing import Any

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
OBSERVED = "2026-09-09T11:00:00+00:00"
CURRENT_UNTIL = "2026-09-09T18:00:00+00:00"
ALREADY_EXPIRED = "2026-09-09T06:00:00+00:00"
AFTER_NOW = "2026-09-09T23:00:00+00:00"
MAX_AGE = 900

SCOPE = {
    "workflow_key": "assurance-fabric-child",
    "workflow_version": 1,
    "work_request_id": "wr-a01-0001",
    "owner": "assurance_fabric_child_owner",
    "incident_refs": ["incident:2026-09-08:outcome-feedback-drift"],
    "recovery_refs": ["recovery:2026-09-08:reissue-independent-artifact-review"],
}
UNRELATED = {
    "workflow_key": "notes-sweep-hourly",
    "workflow_version": 1,
    "work_request_id": "wr-a01-0002",
    "owner": "notes_owner",
}

# Every projected row this suite produces, so vocabulary closure and the
# six-state coverage can be asserted over the whole matrix rather than a sample.
PROJECTED: list[dict] = []


def _identity(scope):
    return {name: scope[name] for name in
            ("workflow_key", "workflow_version", "work_request_id") if name in scope}


def _truth(scope, *, enabled=True, shadow="accepted", canary="accepted",
           declared=True, registered=True, extra_acceptances=()):
    """One row from the real V5-F09 adapter; this suite invents no workflow truth."""
    from lib.control_plane_workflow_truth import workflow_truth_row
    key, version = scope["workflow_key"], scope["workflow_version"]
    acceptances = []
    for mode, status in (("shadow", shadow), ("canary", canary)):
        if status:
            acceptances.append({"workflow_key": key, "workflow_version": version,
                                "mode": mode, "status": status})
    acceptances.extend(extra_acceptances)
    return workflow_truth_row(
        workflow_key=key, workflow_version=version,
        declaration=({"key": key, "version": version, "enabled": enabled,
                      "legacy_schedule": {"provider": "none", "status": "disabled"}}
                     if declared else None),
        definition=({"key": key, "version": version, "enabled": enabled,
                     "execution_contract": {}, "legacy_disabled_at": None}
                    if registered else None),
        acceptances=acceptances, surfaces=[], completion=None,
        duplicate_group_identities={}, observation_max_age_seconds=MAX_AGE, now=NOW)


SLOT_BINDING = {
    "artifact_assessment": {
        "repository_commit_sha": "968541ce072af2aee9a0706b32d81fb46879c4c3",
        "repository_tree_sha": "d6f695df31341b5613503f55331a8bfa1be6967e",
        "reviewer_fact_id": "reviewer-fact:a01:0001"},
    "execution_assessment": {
        "attempt_id": "attempt:a01:0001", "envelope_digest": "envelope:a01:0001",
        "plan_hash": "plan-hash:a01:0001"},
    "controller_assessment": {
        "controller_state": "running", "readback_source": "controller:carr-jobs",
        "readback_at": OBSERVED},
    "candidate_outcome_oracle": {
        "governed_data_ref": "governed:a01:fixture:0001", "environment": "isolated-disposable",
        "expected_result_ref": "expected:a01:0001", "equivalence_comparator": "exact-json.v1",
        "component_versions": {"comparator": "1.4.0", "runtime": "3.12.4"}},
    "activation_readback": {
        "activation_id": "activation:a01:0001", "readback_source": "controller:carr-jobs",
        "readback_at": OBSERVED},
    "actual_business_outcome": {
        "outcome_feedback_ref": "sourced-feedback:a01:0001",
        "outcome_feedback_hash": "feedback-hash:a01:0001",
        "acceptance_receipt_id": "acceptance-receipt:a01:0001"},
}


def _ev(slot, *, scope=SCOPE, status="pass", expires=CURRENT_UNTIL, observed=OBSERVED,
        **overrides):
    from lib.assurance_health import SLOT_ADMISSIBLE_BASES
    record = {
        "layer": slot,
        "basis": SLOT_ADMISSIBLE_BASES[slot][0],
        "status": status,
        "evidence_ref": f"ref:{slot}:0001",
        "evidence_digest": f"digest:{slot}:0001",
        "evaluator_identity": f"evaluator:{slot}",
        "subject_identity": f"subject:{slot}",
        "observed_at": observed,
        "expires_at": expires,
        "scope": _identity(scope),
    }
    record.update(SLOT_BINDING[slot])
    record.update(overrides)
    return record


def _evidence(scope=SCOPE, **overrides):
    from lib.assurance_health import EVIDENCE_SLOTS
    bundle = {slot: _ev(slot, scope=scope) for slot in EVIDENCE_SLOTS}
    bundle.update(overrides)
    return bundle


# A sentinel, because ``None`` is itself a meaningful input to every one of these
# arguments and must be passable through to the projection unchanged.
_DEFAULT = object()


def _row(scope=SCOPE, truth=_DEFAULT, evidence=_DEFAULT, now=NOW):
    """One verdict from the MODULE-PRIVATE label logic, through the test-only hook.

    THE ONLY DOOR TO THE CLASSIFIER.  The predicate is not exported and is not in
    ``lib.assurance_health.__all__``; this suite reaches it through
    ``_test_only_hypothetical_row``, whose answer comes back under
    ``would_be_healthy_if_authoritative`` because that is what it is -- what the
    ladder WOULD say if these fixture shapes were evidence an owner had admitted.
    No public callable in that module will produce this answer from these shapes,
    and ``public_surface_guard_checks`` parses the file to keep it that way.

    Fixture-shaped evidence is exactly what the ladder must be tested against --
    including the six-passing shape, which is the only way to prove it really
    requires all six layers.
    """
    from lib.assurance_health import _test_only_hypothetical_row
    row = _test_only_hypothetical_row(
        scope=scope,
        workflow_truth=_truth(scope) if truth is _DEFAULT else truth,
        evidence=_evidence(scope) if evidence is _DEFAULT else evidence,
        now=now)["would_be_healthy_if_authoritative"]
    PROJECTED.append(row)
    return row



def display_state_checks(health) -> None:
    """Each of the six states, reached only by its own evidence."""
    healthy = _row()
    check("six current, distinct, independently evaluated, exactly bound passing facts "
          "with F09 live admission are the only route to healthy",
          healthy["state"] == "healthy" and healthy["green"] is True
          and healthy["capability_stage"] == "act"
          and healthy["capabilities_withdrawn"] == []
          and healthy["recovery"]["required_evidence"] == [],
          json.dumps(healthy["state_reason"]))

    degraded = _row(evidence=_evidence(actual_business_outcome=_ev(
        "actual_business_outcome", status="fail")))
    check("a failed layer degrades the scope and withdraws exactly the capability it earned",
          degraded["state"] == "degraded" and degraded["green"] is False
          and degraded["capability_stage"] == "draft"
          and degraded["capabilities_withdrawn"] == ["act"]
          and degraded["capabilities_retained"] == ["draft", "read"],
          json.dumps(degraded["capability_stage"]))

    failed = _row(evidence=_evidence(artifact_assessment=_ev(
        "artifact_assessment", status="fail")))
    check("a failure that withdraws even read capability is failed, not degraded",
          failed["state"] == "failed" and failed["capability_stage"] == "unavailable"
          and failed["capabilities_retained"] == [])

    from lib.control_plane_workflow_truth import UNREADABLE
    unknown = _row(truth=UNREADABLE)
    check("unreadable authoritative workflow truth is unknown, never a guessed state",
          unknown["state"] == "unknown" and unknown["green"] is False
          and unknown["workflow_truth"]["available"] is False)

    disabled = _row(truth=_truth(SCOPE, enabled=False))
    check("disabled is emitted only from authoritative F09 workflow truth",
          disabled["state"] == "disabled" and disabled["green"] is False
          and disabled["workflow_truth"]["state"] == "declared_disabled")

    not_yet = _row(evidence=_evidence(activation_readback=None,
                                      actual_business_outcome=None))
    check("four passing preactivation receipts with no activation or outcome evidence "
          "are not-yet-operational, never healthy",
          not_yet["state"] == "not-yet-operational"
          and not_yet["preactivation_receipts_passing"] is True
          and not_yet["green"] is False)

    observed_states = {row["state"] for row in PROJECTED}
    check("all six display states are reachable and no seventh exists",
          observed_states == set(health.DISPLAY_STATES),
          f"missing={sorted(set(health.DISPLAY_STATES) - observed_states)} "
          f"extra={sorted(observed_states - set(health.DISPLAY_STATES))}")

    # DETERMINISM. Same inputs, same bytes -- and the order a caller happened to
    # build its evidence mapping in is not an input.
    repeated = json.dumps(_row(), sort_keys=True)
    reordered = _evidence()
    shuffled = {slot: reordered[slot] for slot in reversed(list(reordered))}
    check("the projection is deterministic and independent of caller key order",
          repeated == json.dumps(_row(), sort_keys=True)
          and repeated == json.dumps(_row(evidence=shuffled), sort_keys=True))


def never_green_matrix_checks(health) -> None:
    """EVERY layer, degraded EVERY way the contract names, never renders green."""
    from lib.control_plane_workflow_truth import UNREADABLE

    def degradations(slot):
        borrowed = ("execution_assessment" if slot == "artifact_assessment"
                    else "artifact_assessment")
        return {
            "absent": (None, "missing"),
            "unreadable": (UNREADABLE, "unreadable"),
            "mismatched": (_ev(slot, scope=UNRELATED), "mismatched"),
            "stale": (_ev(slot, expires=ALREADY_EXPIRED), "stale"),
            "self_attested": (_ev(slot, subject_identity=f"evaluator:{slot}"), "self_attested"),
            "skipped": (_ev(slot, status="skipped"), "skipped"),
            "untested": (_ev(slot, status="untested"), "untested"),
            "failed": (_ev(slot, status="fail"), "failed"),
            "errored": (_ev(slot, status="error"), "error"),
            "conflicting": (_ev(slot, status="conflicting"), "conflicting"),
            "future_dated": (_ev(slot, observed=AFTER_NOW), "conflicting"),
            "refused_substitute": (_ev(slot, basis="enforcement_closure"), "refused_substitute"),
            "indistinct": (_ev(slot, evidence_ref=f"ref:{borrowed}:0001",
                               evidence_digest=f"digest:{borrowed}:0001"), "indistinct"),
        }

    greens: list[str] = []
    misclassified: list[str] = []
    cells = 0
    for slot in health.EVIDENCE_SLOTS:
        for label, (value, expected) in degradations(slot).items():
            cells += 1
            row = _row(evidence=_evidence(**{slot: value}))
            if row["green"] or row["state"] == "healthy":
                greens.append(f"{slot}/{label}")
            if row["evidence"][slot]["state"] != expected:
                misclassified.append(
                    f"{slot}/{label}={row['evidence'][slot]['state']} (want {expected})")
    check(f"no degraded layer ever renders green ({cells} matrix cells)",
          not greens, f"green={greens}")
    check("every degradation is classified as the exact evidence state it is",
          not misclassified, f"misclassified={misclassified}")

    # The same fact stated the way the acceptance predicate states it.
    unproven = _row(evidence=_evidence(
        artifact_assessment=None, execution_assessment=UNREADABLE,
        controller_assessment=_ev("controller_assessment", status="skipped"),
        candidate_outcome_oracle=_ev("candidate_outcome_oracle", expires=ALREADY_EXPIRED)))
    check("missing, unreadable, skipped and stale evidence together are never green and "
          "each is named separately",
          unproven["green"] is False
          and unproven["evidence"]["artifact_assessment"]["state"] == "missing"
          and unproven["evidence"]["execution_assessment"]["state"] == "unreadable"
          and unproven["evidence"]["controller_assessment"]["state"] == "skipped"
          and unproven["evidence"]["candidate_outcome_oracle"]["state"] == "stale")

    check("an absent layer is reported as absent, never as an empty passing receipt",
          unproven["evidence"]["artifact_assessment"]["present"] is False
          and "evidence_ref" not in unproven["evidence"]["artifact_assessment"]
          and unproven["missing_layers"] == ["artifact_assessment"])


def preactivation_chain_checks(health) -> None:
    """Four layers, four distinct exact identities, four independent blocks."""
    healthy = _row()
    identities = [(healthy["evidence"][slot]["evidence_ref"],
                   healthy["evidence"][slot]["evidence_digest"])
                  for slot in health.EVIDENCE_SLOTS]
    check("the six layers carry six distinct exact evidence identities",
          len(set(identities)) == len(health.EVIDENCE_SLOTS))

    non_blocking: list[str] = []
    for slot in health.PREACTIVATION_SLOTS:
        for label, value in (("absent", None),
                             ("failed", _ev(slot, status="fail")),
                             ("mismatched", _ev(slot, scope=UNRELATED))):
            row = _row(evidence=_evidence(**{slot: value}))
            if row["green"] or slot not in row["impact"]["blocking_act"]:
                non_blocking.append(f"{slot}/{label}")
    check("removing, failing or mismatching each preactivation receipt independently "
          "blocks activation and names itself as the blocker",
          not non_blocking, f"did-not-block={non_blocking}")

    reused = _row(evidence=_evidence(
        candidate_outcome_oracle=_ev("candidate_outcome_oracle",
                                     evidence_digest="digest:execution_assessment:0001")))
    check("one receipt can never satisfy two layers: a reused digest is indistinct, "
          "not two independent facts",
          reused["green"] is False
          and reused["evidence"]["candidate_outcome_oracle"]["state"] == "indistinct"
          and reused["evidence"]["execution_assessment"]["state"] == "indistinct"
          and reused["state"] == "unknown")

    self_signed = _row(evidence=_evidence(artifact_assessment=_ev(
        "artifact_assessment", evaluator_identity="session:a01-author",
        subject_identity="session:a01-author")))
    check("an assessment whose evaluator is its own subject is self-attested, not review",
          self_signed["green"] is False
          and self_signed["evidence"]["artifact_assessment"]["state"] == "self_attested")

    controller_by_config = _row(evidence=_evidence(controller_assessment=_ev(
        "controller_assessment", basis="enabled_configuration")))
    check("enabled configuration is refused as controller truth: it is never a readback",
          controller_by_config["green"] is False
          and controller_by_config["evidence"]["controller_assessment"]["state"]
          == "refused_substitute")

    enabled_only = _row(truth=_truth(SCOPE, canary=None))
    check("an enabled definition without the acceptance its live tier requires is "
          "not-yet-operational even when all six evidence layers pass",
          enabled_only["state"] == "not-yet-operational" and enabled_only["green"] is False
          and enabled_only["workflow_truth"]["state"] == "enabled_canary_eligible"
          and "workflow_live_admissible" in enabled_only["impact"]["blocking_act"])


def actual_outcome_separation_checks(health) -> None:
    """The last slot is its own fact, and nothing else may fill it."""
    activated = _row(evidence=_evidence(actual_business_outcome=None))
    check("four preactivation passes plus activation readback, with no accepted outcome "
          "receipt, remains not-yet-operational",
          activated["state"] == "not-yet-operational" and activated["green"] is False
          and activated["evidence"]["activation_readback"]["state"] == "passing"
          and activated["evidence"]["actual_business_outcome"]["state"] == "missing")

    accepted = _row()
    check("only an exact current accepted sourced outcome-feedback receipt earns healthy",
          accepted["state"] == "healthy"
          and accepted["evidence"]["actual_business_outcome"]["basis"]
          == "accepted_sourced_outcome_feedback_receipt"
          and accepted["evidence"]["actual_business_outcome"]["outcome_feedback_hash"]
          == "feedback-hash:a01:0001"
          and accepted["evidence"]["actual_business_outcome"]["acceptance_receipt_id"]
          == "acceptance-receipt:a01:0001")

    accepted_elsewhere = _row(evidence=_evidence(actual_business_outcome=_ev(
        "actual_business_outcome",
        scope={**UNRELATED, "work_request_id": "wr-a01-0099"})))
    check("an accepted outcome receipt for a different Work Request identity never fills "
          "this scope's outcome slot",
          accepted_elsewhere["green"] is False
          and accepted_elsewhere["evidence"]["actual_business_outcome"]["state"]
          == "mismatched")

    admitted: list[str] = []
    for basis in ("enforcement_closure", "completion_lifecycle", "merge_or_ci_result",
                  "enabled_configuration", "telemetry", "self_attestation",
                  "candidate_outcome_pass"):
        row = _row(evidence=_evidence(actual_business_outcome=_ev(
            "actual_business_outcome", basis=basis)))
        if row["green"] or row["evidence"]["actual_business_outcome"]["state"] \
                != "refused_substitute":
            admitted.append(basis)
        if basis == "enforcement_closure":
            check("completion enforcement_closure offered as the business outcome is "
                  "refused by name, and the refusal is printed",
                  row["state"] == "degraded"
                  and any("enforcement_closure is refused as a substitute" in reason
                          for reason in row["reasons"]),
                  json.dumps(row["reasons"]))
    check("no lifecycle, merge/CI, configuration, telemetry, self-attested or "
          "candidate-outcome substitute can fill the actual-business-outcome slot",
          not admitted, f"admitted={admitted}")

    try:
        _row(evidence=_evidence(actual_business_outcome=_ev("candidate_outcome_oracle")))
        cross_layer_refused = False
    except health.AssuranceHealthContractError:
        cross_layer_refused = True
    check("a candidate-outcome receipt handed to the outcome slot is refused outright: "
          "no layer is ever inferred from another",
          cross_layer_refused)

    out_of_order = _row(evidence=_evidence(activation_readback=None))
    check("an accepted outcome claimed for a scope with no current activation readback "
          "is contradictory, never a pass",
          out_of_order["green"] is False
          and out_of_order["evidence"]["actual_business_outcome"]["state"] == "conflicting"
          and out_of_order["state"] == "unknown")


def scoped_degradation_checks(health) -> None:
    """One workflow walks the ladder; an unrelated bound scope does not move."""
    from lib.assurance_health import _test_only_hypothetical_census
    def assurance_health(**kwargs):
        return _test_only_hypothetical_census(**kwargs)["would_be_healthy_if_authoritative"]

    def project(**failing):
        bundles = [
            {"scope": SCOPE, "workflow_truth": _truth(SCOPE),
             "evidence": _evidence(**{slot: _ev(slot, status="fail")
                                      for slot in failing})},
            {"scope": UNRELATED, "workflow_truth": _truth(UNRELATED),
             "evidence": _evidence(UNRELATED)},
        ]
        projection = assurance_health(scopes=bundles, now=NOW)
        PROJECTED.extend(projection["rows"])
        rows = {row["scope"]["work_request_id"]: row for row in projection["rows"]}
        return projection, rows

    steps = [
        ({}, "healthy", "act"),
        ({"actual_business_outcome": True}, "degraded", "draft"),
        ({"actual_business_outcome": True, "controller_assessment": True},
         "degraded", "read"),
        ({"actual_business_outcome": True, "controller_assessment": True,
          "artifact_assessment": True}, "failed", "unavailable"),
    ]
    walked: list[str] = []
    unrelated_rows: list[str] = []
    exposure_gaps: list[str] = []
    for failing, expected_state, expected_stage in steps:
        projection, rows = project(**failing)
        affected = rows["wr-a01-0001"]
        walked.append(f"{affected['state']}/{affected['capability_stage']}")
        if affected["state"] != expected_state or affected["capability_stage"] != expected_stage:
            walked[-1] += f" (want {expected_state}/{expected_stage})"
        unrelated_rows.append(json.dumps(rows["wr-a01-0002"], sort_keys=True))
        if affected["state"] in ("degraded", "failed"):
            recovery = affected["recovery"]
            named = {item["slot"] for item in recovery["required_evidence"]}
            if not (recovery["owner"] == SCOPE["owner"]
                    and set(failing).issubset(named)
                    and recovery["incident_refs"] == SCOPE["incident_refs"]
                    and recovery["recovery_refs"] == SCOPE["recovery_refs"]
                    and affected["impact"]["capabilities_withdrawn"]
                    and affected["impact"]["scope_limited_to"] == _identity(SCOPE)
                    and all(affected["evidence"][slot]["evidence_ref"] for slot in failing)):
                exposure_gaps.append(affected["state"])

    check("one workflow degrades act -> draft -> read -> unavailable, one stage per "
          "injected failure and never by a jump",
          walked == ["healthy/act", "degraded/draft", "degraded/read", "failed/unavailable"],
          " | ".join(walked))
    check("every degradation exposes owner, the exact failing evidence, the impact it "
          "bounds and the recovery it requires",
          not exposure_gaps, f"incomplete={exposure_gaps}")
    check("an unrelated explicitly bound scope keeps its evidence-derived state, byte "
          "for byte, through every injected failure",
          len(set(unrelated_rows)) == 1
          and json.loads(unrelated_rows[0])["state"] == "healthy")

    projection, rows = project(actual_business_outcome=True)
    check("the projection summary counts what it projected and scopes the degradation "
          "to the exact bound identity",
          projection["summary"]["scopes"] == 2
          and projection["summary"]["states"]["degraded"] == 1
          and projection["summary"]["states"]["healthy"] == 1
          and projection["summary"]["green"] == 1
          # Named by workflow AND Work Request: a bare Work Request id does not say
          # which workflow degraded, and a workflow-only scope has no such id at all.
          and projection["summary"]["degraded_scopes"]
          == ["assurance-fabric-child@v1/wr-a01-0001"])


def workflow_truth_governance_checks(health) -> None:
    """F09 owns workflow truth; this projection consumes it and builds no second one."""
    from lib.control_plane_workflow_truth import (
        SCHEMA_VERSION as F09_SCHEMA_VERSION, UNREADABLE)

    check("the workflow authority is the delivered F09 adapter, not a local copy",
          health.WORKFLOW_TRUTH_SCHEMA_VERSION == F09_SCHEMA_VERSION)

    conflicting = _row(truth=_truth(SCOPE, extra_acceptances=[
        {"workflow_key": SCOPE["workflow_key"], "workflow_version": 1,
         "mode": "shadow", "status": "rejected"}]))
    check("F09 conflicting workflow truth governs the projection: contradictory truth "
          "is unknown even when all six evidence layers pass",
          conflicting["workflow_truth"]["state"] == "conflict"
          and conflicting["state"] == "unknown" and conflicting["green"] is False)

    unregistered = _row(truth=_truth(SCOPE, registered=False))
    check("a declared workflow with no registered definition is not-yet-operational",
          unregistered["workflow_truth"]["state"] == "unregistered"
          and unregistered["state"] == "not-yet-operational"
          and unregistered["green"] is False)

    disabled = _row(truth=_truth(SCOPE, enabled=False),
                    evidence=_evidence(artifact_assessment=_ev(
                        "artifact_assessment", status="fail")))
    check("a deliberately disabled workflow reads disabled and still prints its failing "
          "evidence rather than being called a live failure",
          disabled["state"] == "disabled"
          and disabled["evidence"]["artifact_assessment"]["state"] == "failed"
          and disabled["failing_layers"] == ["artifact_assessment"]
          and disabled["green"] is False)

    check("an unreadable census input holds the whole scope at unknown",
          _row(truth=UNREADABLE)["state"] == "unknown")

    forged = dict(_truth(SCOPE))
    forged["schema_version"] = "assurance-health-local-workflow-registry.v1"
    try:
        _row(truth=forged)
        second_registry_refused = False
    except health.AssuranceHealthContractError:
        second_registry_refused = True
    check("a workflow row from anywhere but the F09 adapter is refused, so no second "
          "workflow/status registry can grow here",
          second_registry_refused)

    try:
        _row(truth=_truth(UNRELATED))
        wrong_row_refused = False
    except health.AssuranceHealthContractError:
        wrong_row_refused = True
    check("a workflow truth row for a different workflow is refused, never joined",
          wrong_row_refused)


    # Mutate the real F09 projection after constructing valid inputs.  Coercible
    # values must not borrow the healthy result of the integer version they mimic.
    bad_versions: tuple[Any, ...] = (True, False, 1.9, 1.0, "1", "01", None, [], {}, 0, -1, 2)
    for bad_version in bad_versions:
        truth = {**_truth(SCOPE), "workflow_version": bad_version}
        try:
            _row(truth=truth)
            refused = False
        except health.AssuranceHealthContractError:
            refused = True
        check(f"workflow truth version {bad_version!r} ({type(bad_version).__name__}) "
              "cannot join integer scope version 1", refused)

    bad_keys: tuple[Any, ...] = (None, 123, True, [], {}, "", " " + str(SCOPE["workflow_key"]))
    for bad_key in bad_keys:
        try:
            _row(truth={**_truth(SCOPE), "workflow_key": bad_key})
            refused = False
        except health.AssuranceHealthContractError:
            refused = True
        check(f"workflow truth key {bad_key!r} cannot join a different scope", refused)

    check("the exact F09 string key and positive integer version remain healthy",
          _row(truth=_truth(SCOPE))["green"] is True)


def scope_identity_checks(health) -> None:
    """Exact identities only: no name, label or title ever participates in a join."""
    check("a scope is bound by exact identities and nothing else",
          health.SCOPE_IDENTITY_FIELDS
          == ("workflow_key", "workflow_version", "work_request_id"))

    labelled = _row(
        scope={**SCOPE, "human_label": "Assurance Fabric — child A",
               "title": "child outcome"},
        evidence=_evidence(artifact_assessment=_ev(
            "artifact_assessment",
            scope={**_identity(SCOPE), "human_label": "a completely different label"})))
    check("human labels are carried by neither the scope nor the evidence join: the same "
          "identities project identically however they are labelled",
          json.dumps(labelled, sort_keys=True) == json.dumps(_row(), sort_keys=True))

    same_label = _row(evidence=_evidence(execution_assessment=_ev(
        "execution_assessment",
        scope={"workflow_key": SCOPE["workflow_key"], "workflow_version": 2,
               "work_request_id": SCOPE["work_request_id"]})))
    check("an identical label with an unequal exact identity is mismatched, never matched",
          same_label["evidence"]["execution_assessment"]["state"] == "mismatched"
          and same_label["green"] is False and same_label["state"] == "unknown")

    wrong_request = _row(evidence=_evidence(actual_business_outcome=_ev(
        "actual_business_outcome",
        scope={**_identity(SCOPE), "work_request_id": "wr-a01-0777"})))
    check("evidence bound to another Work Request identity is refused for this scope",
          wrong_request["evidence"]["actual_business_outcome"]["state"] == "mismatched"
          and wrong_request["green"] is False)

    from lib.assurance_health import _test_only_hypothetical_census
    def assurance_health(**kwargs):
        return _test_only_hypothetical_census(**kwargs)["would_be_healthy_if_authoritative"]
    try:
        assurance_health(scopes=[
            {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence()},
            {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence()},
        ], now=NOW)
        duplicate_refused = False
    except health.AssuranceHealthContractError:
        duplicate_refused = True
    check("one exact scope has exactly one projected state; a doubly bound scope is refused",
          duplicate_refused)


def refusal_checks(health) -> None:
    """A malformed input is refused, never guessed at."""
    from lib.control_plane_workflow_truth import UNREADABLE

    incomplete_scope_evidence = _ev("execution_assessment")
    del incomplete_scope_evidence["scope"]["workflow_version"]

    # The malformed input is the ONLY malformed thing in each case: a bad scope is
    # paired with sound truth and evidence so the refusal cannot come from elsewhere.
    accepted: list[str] = []
    for label, kwargs in (
        ("evidence that does not name every layer explicitly",
         {"evidence": {slot: _ev(slot) for slot in health.PREACTIVATION_SLOTS}}),
        ("an evidence slot outside the closed set",
         {"evidence": {**_evidence(), "vibes_assessment": _ev("artifact_assessment")}}),
        ("an evidence status outside the closed set",
         {"evidence": _evidence(artifact_assessment=_ev(
             "artifact_assessment", status="looks-fine"))}),
        ("a basis outside the closed set",
         {"evidence": _evidence(controller_assessment=_ev(
             "controller_assessment", basis="someone-said-so"))}),
        ("evidence with no expiry from its own source",
         {"evidence": _evidence(candidate_outcome_oracle={
             key: value for key, value in _ev("candidate_outcome_oracle").items()
             if key != "expires_at"})}),
        ("evidence with no exact scope identity",
         {"evidence": _evidence(
             execution_assessment=incomplete_scope_evidence)}),
        ("evidence missing its layer-specific exact binding",
         {"evidence": _evidence(artifact_assessment={
             key: value for key, value in _ev("artifact_assessment").items()
             if key != "repository_tree_sha"})}),
        ("an oracle receipt with no component versions",
         {"evidence": _evidence(candidate_outcome_oracle=_ev(
             "candidate_outcome_oracle", component_versions={}))}),
        ("a scope with no owner",
         {"scope": {key: value for key, value in SCOPE.items() if key != "owner"},
          "truth": _truth(SCOPE), "evidence": _evidence()}),
        # A scope with no Work Request identity is NOT refused any more: it is a
        # legitimate workflow-only binding whose outcome layer is unbindable, and
        # workflow_only_scope_checks proves it can never be green.  What is still
        # refused is a work_request_id that is present and malformed.
        ("a scope whose work request identity is present and empty",
         {"scope": {**SCOPE, "work_request_id": "  "},
          "truth": _truth(SCOPE), "evidence": _evidence()}),
        ("a scope version that is not a positive integer",
         {"scope": {**SCOPE, "workflow_version": 0},
          "truth": _truth(SCOPE), "evidence": _evidence()}),
        ("an incident reference that is not an existing ref",
         {"scope": {**SCOPE, "incident_refs": [{"note": "invented"}]}}),
        ("a workflow truth input that was neither read nor declared unreadable",
         {"truth": None}),
        ("an instant that is not ISO-8601", {"now": "yesterday"}),
    ):
        try:
            _row(**kwargs)
        except health.AssuranceHealthContractError:
            continue
        except Exception as exc:  # a refusal must be the declared contract error
            accepted.append(f"{label} raised {type(exc).__name__}")
            continue
        accepted.append(label)
    check("every malformed or unbound input is refused with the declared contract error",
          not accepted, f"accepted={accepted}")

    check("UNREADABLE and absent are different facts and are reported differently",
          _row(evidence=_evidence(controller_assessment=UNREADABLE))
          ["evidence"]["controller_assessment"]["state"] == "unreadable"
          and _row(evidence=_evidence(controller_assessment=None))
          ["evidence"]["controller_assessment"]["state"] == "missing")


def output_discipline_checks(health) -> None:
    """What the canonical output must always carry, and must never carry."""
    row = _row(evidence=_evidence(controller_assessment=_ev(
        "controller_assessment",
        api_token="SUPERSECRET-DO-NOT-PRINT",
        connection_string="SYNTHETIC-CONNECTION-DO-NOT-PRINT")))
    rendered = json.dumps(row)
    check("evidence identity is echoed and adjacent secrets never are",
          "SUPERSECRET-DO-NOT-PRINT" not in rendered and "SYNTHETIC-CONNECTION-DO-NOT-PRINT" not in rendered
          and row["evidence"]["controller_assessment"]["evidence_ref"]
          == "ref:controller_assessment:0001")

    healthy = _row()
    check("the canonical row always exposes state, scope, owner and every layer's "
          "identity and currentness",
          set(healthy["evidence"]) == set(health.EVIDENCE_SLOTS)
          and healthy["scope"]["owner"] == SCOPE["owner"]
          and healthy["scope"]["completion_subject_key"]
          == "workflow:assurance-fabric-child:v1"
          and all(healthy["evidence"][slot]["expires_at"] == CURRENT_UNTIL
                  and healthy["evidence"][slot]["observed_at"] == OBSERVED
                  for slot in health.EVIDENCE_SLOTS))
    check("no state is authored: healthy is a derived label with its derivation printed",
          bool(healthy["state_reason"]) and bool(healthy["reasons"])
          and healthy["workflow_truth"]["source"]
          == "lib/control_plane_workflow_truth.py")

    outside: list[str] = []
    for projected in PROJECTED:
        if projected["state"] not in health.DISPLAY_STATES:
            outside.append(f"state={projected['state']}")
        if projected["capability_stage"] not in health.CAPABILITY_STAGES:
            outside.append(f"stage={projected['capability_stage']}")
        for slot, public in projected["evidence"].items():
            if public["state"] not in health.EVIDENCE_STATES:
                outside.append(f"{slot}={public['state']}")
        if projected["green"] != (projected["state"] == "healthy"):
            outside.append("green disagrees with state")
        if projected["state"] != "healthy" and not projected["recovery"]["required_evidence"] \
                and projected["state"] not in ("disabled", "unknown", "not-yet-operational"):
            outside.append(f"{projected['state']} named no required evidence")
    check(f"every projected row in this suite stays inside the closed vocabularies "
          f"({len(PROJECTED)} rows)", not outside, f"outside={sorted(set(outside))[:5]}")

    source = (REPO / "lib" / "assurance_health.py").read_text(encoding="utf-8")
    modules: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            modules.update(part.strip().split(" as ")[0].split(".")[0]
                           for part in stripped[len("import "):].split(","))
        elif stripped.startswith("from ") and " import " in stripped:
            modules.add(stripped[len("from "):].split(" import ")[0].strip().split(".")[0])
    forbidden = sorted(modules & {
        "os", "subprocess", "socket", "urllib", "http", "requests", "psycopg", "psycopg2",
        "sqlite3", "pathlib", "shutil", "random", "time", "secrets", "logging"})
    check("the projection is pure: it imports no clock, filesystem, process, network "
          "or database route",
          not forbidden, f"forbidden={forbidden}")
    check("the projection reuses the F09 workflow adapter rather than restating it",
          "from lib.control_plane_workflow_truth import" in source
          and "def workflow_truth" not in source)


WORKFLOW_ONLY = {
    "workflow_key": "assurance-fabric-child",
    "workflow_version": 1,
    "owner": "assurance_fabric_child_owner",
}


def workflow_only_scope_checks(health) -> None:
    """A scope may be bound by workflow identity alone, and then it cannot be green.

    THE DEFECT THIS FIXES. The first cut of this module made ``work_request_id``
    part of every scope's identity, so a truthful-health surface that reads the
    workflow census -- which carries no Work Request identity at all -- could bind
    no scope and therefore could not be wired to this projection.  Requiring an
    identity the authoritative reading does not carry does not make a surface
    honest; it makes it silent.

    The rule instead: the Work Request identity is OPTIONAL on the scope and
    REQUIRED for exactly the layer that joins through it.  A workflow-only scope
    projects every other layer normally and reports ``actual_business_outcome`` as
    ``unbindable`` -- so act capability, and therefore green, is unreachable until
    the binding exists.  That is the settled position (Q043: no layer may be
    inferred from another) expressed as a join rather than as silence.
    """
    row = _row(scope=WORKFLOW_ONLY, evidence=_evidence(WORKFLOW_ONLY))
    check("a scope bound by workflow identity alone is projected, not refused",
          row["scope"]["workflow_key"] == "assurance-fabric-child"
          and row["scope"]["work_request_id"] is None,
          json.dumps(row["scope"]))
    outcome = row["evidence"]["actual_business_outcome"]
    check("a workflow-only scope reports the business outcome layer as unbindable",
          outcome["state"] == "unbindable", outcome["state"])
    check("unbindable names the exact missing identity, not a vague absence",
          any("work_request_id" in reason for reason in outcome["reasons"]),
          json.dumps(outcome["reasons"]))
    check("a perfect outcome receipt cannot fill a layer the scope cannot join",
          outcome["state"] == "unbindable" and outcome.get("status") == "pass",
          json.dumps(outcome))
    check("a workflow-only scope is never green however good its other evidence is",
          row["state"] != health.GREEN_STATE and not row["green"], row["state"])
    check("a workflow-only scope with five passing layers is not-yet-operational",
          row["state"] == "not-yet-operational", row["state_reason"])
    check("act capability is unreachable without the Work Request join",
          row["capability_stage"] == "draft"
          and "actual_business_outcome" in row["impact"]["blocking_act"],
          f"{row['capability_stage']} {row['impact']['blocking_act']}")
    check("the recovery block names the outcome evidence the owner still owes",
          any(item["slot"] == "actual_business_outcome"
              and item["evidence_state"] == "unbindable"
              for item in row["recovery"]["required_evidence"]),
          json.dumps(row["recovery"]["required_evidence"]))

    explicit = _row(scope={**WORKFLOW_ONLY, "work_request_id": None},
                    evidence=_evidence(WORKFLOW_ONLY))
    check("an explicit null Work Request identity binds exactly as its absence does",
          explicit["state"] == row["state"]
          and explicit["evidence"]["actual_business_outcome"]["state"] == "unbindable",
          explicit["state"])

    bound_evidence = _evidence(WORKFLOW_ONLY)
    bound_evidence["artifact_assessment"] = _ev("artifact_assessment", scope=SCOPE)
    joined = _row(scope=WORKFLOW_ONLY, evidence=bound_evidence)
    check("evidence carrying a Work Request identity cannot join a workflow-only scope",
          joined["evidence"]["artifact_assessment"]["state"] == "mismatched",
          joined["evidence"]["artifact_assessment"]["state"])

    both = health._test_only_hypothetical_census(scopes=[
        {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
         "evidence": _evidence(WORKFLOW_ONLY)},
        {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence(SCOPE)},
    ], now=NOW)["would_be_healthy_if_authoritative"]
    PROJECTED.extend(both["rows"])
    check("the same workflow bound with and without a Work Request is two scopes",
          both["summary"]["scopes"] == 2, json.dumps(both["summary"]))
    check("a workflow-only row and a Work Request row sort deterministically",
          [r["scope"]["work_request_id"] for r in both["rows"]] == [None, "wr-a01-0001"],
          json.dumps([r["scope"]["work_request_id"] for r in both["rows"]]))

    bad_identities: tuple[Any, ...] = ("", "   ", 7, True, [], {})
    for bad in bad_identities:
        try:
            health._test_only_hypothetical_row(
                scope={**WORKFLOW_ONLY, "work_request_id": bad},
                workflow_truth=_truth(WORKFLOW_ONLY), evidence=_evidence(WORKFLOW_ONLY),
                now=NOW)
            refused = False
        except health.AssuranceHealthContractError:
            refused = True
        check(f"a malformed Work Request identity {bad!r} is refused, never coerced to unbound",
              refused)

    # TWO DEGRADED WORKFLOW-ONLY SCOPES, THROUGH THE LABEL LOGIC ITSELF.
    #
    # THE DEFECT THIS PINS.  The summary listed its degraded scopes by
    # ``work_request_id``, which every scope the live census binds leaves absent.
    # One degraded scope never compared anything, so every fixture stayed green
    # while the first census carrying two of them raised TypeError on ``None <
    # None`` inside the projection -- which the adapter does not catch.  The
    # assertion has to carry TWO of them, and it lives here rather than on the
    # health surface because no reading that surface can perform is determinate
    # enough to degrade a scope at all: it has no admitted evidence to fail.
    other_workflow_only = {"workflow_key": "also-degrading", "workflow_version": 1,
                           "owner": "assurance_fabric_child_owner"}
    degraded_pair = health._test_only_hypothetical_census(scopes=[
        {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
         "evidence": _evidence(WORKFLOW_ONLY, controller_assessment=_ev(
             "controller_assessment", scope=WORKFLOW_ONLY, status="fail"))},
        {"scope": other_workflow_only, "workflow_truth": _truth(other_workflow_only),
         "evidence": _evidence(other_workflow_only, controller_assessment=_ev(
             "controller_assessment", scope=other_workflow_only, status="fail"))},
    ], now=NOW)["would_be_healthy_if_authoritative"]
    PROJECTED.extend(degraded_pair["rows"])
    check("two degraded scopes with no Work Request identity are both projected",
          degraded_pair["summary"]["scopes"] == 2
          and degraded_pair["summary"]["states"]["degraded"] == 2,
          json.dumps(degraded_pair["summary"]["states"]))
    check("the degraded list orders and names both by the identity they actually have",
          degraded_pair["summary"]["degraded_scopes"]
          == ["also-degrading@v1", "assurance-fabric-child@v1"],
          json.dumps(degraded_pair["summary"]["degraded_scopes"]))
    check("a Work Request-bound scope keeps its id in the same degraded list",
          health._test_only_hypothetical_census(scopes=[
              {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
               "evidence": _evidence(WORKFLOW_ONLY, controller_assessment=_ev(
                   "controller_assessment", scope=WORKFLOW_ONLY, status="fail"))},
              {"scope": SCOPE, "workflow_truth": _truth(SCOPE),
               "evidence": _evidence(SCOPE, controller_assessment=_ev(
                   "controller_assessment", status="fail"))},
          ], now=NOW)["would_be_healthy_if_authoritative"]["summary"]["degraded_scopes"]
          == ["assurance-fabric-child@v1", "assurance-fabric-child@v1/wr-a01-0001"])

    check("unbindable is a declared evidence state that is not a passing one",
          "unbindable" in health.EVIDENCE_STATES
          and "unbindable" not in health.DETERMINATE_NONPASS_EVIDENCE_STATES
          and "unbindable" not in health.INDETERMINATE_EVIDENCE_STATES)


def caller_evidence_is_never_authority_checks(health) -> None:
    """THE EXHAUSTIVE UNREACHABILITY MATRIX for the PUBLIC surface.

    A caller-supplied label, class string, flag, receipt object or injected
    holder is never authority.  ``lib/assurance_health`` owns the label logic and
    owns no evidence: NOT ONE of its six layers has an evidence owner anywhere in
    this repository, so there is no admission point through which a caller could
    make any of them passing, whatever it passes in.  This iterates every
    caller-controlled input shape the public entry points accept and asserts the
    privileged outcomes -- ``passing``, ``healthy``/green and ``act`` -- are
    unreachable rather than merely undocumented.
    """
    check("no layer has an evidence owner in this repository",
          health.EVIDENCE_OWNERS == {}, json.dumps(health.EVIDENCE_OWNERS))
    check("every layer names the seam that is owed before it could ever pass",
          all(slot in health.OWED_EVIDENCE_OWNER_SEAMS for slot in health.EVIDENCE_SLOTS)
          and all(health.OWED_EVIDENCE_OWNER_SEAMS[slot].strip()
                  for slot in health.EVIDENCE_SLOTS),
          json.dumps(sorted(health.OWED_EVIDENCE_OWNER_SEAMS)))
    check("the controller layer's owed seam names a READ, not a shape to trust",
          "ops.legacy_schedule_observation_receipt"
          in health.OWED_EVIDENCE_OWNER_SEAMS["controller_assessment"]
          and "tools/health-check.py"
          in health.OWED_EVIDENCE_OWNER_SEAMS["controller_assessment"],
          health.OWED_EVIDENCE_OWNER_SEAMS["controller_assessment"])

    # ---- every caller-controlled shape, for every slot ----------------------
    far_future = "2099-01-01T00:00:00+00:00"

    class _LooksAdmitted:
        """An injected holder wearing the admitted shape.  Shape is not authority."""
        def __init__(self, slot, record):
            self.slot = slot
            self.owner = "receipt-producer:ops.legacy_schedule_observation_receipt"
            self.record = record

    class _EvidenceDict(dict):
        """A dict subclass, in case isinstance(..., dict) were the whole test."""

    def caller_shapes(slot):
        """Every shape a caller can hand this slot, most plausible first."""
        perfect = _ev(slot)
        yield f"{slot}: a perfectly shaped passing record", perfect
        yield f"{slot}: a record whose expiry is decades away", _ev(slot, expires=far_future)
        yield (f"{slot}: a record naming this repository's own admission function",
               _ev(slot, evaluator_identity="lib/assurance_health.admit_evidence"))
        yield (f"{slot}: a record claiming the retired admission path's evaluator",
               _ev(slot, evaluator_identity=
                   "receipt-producer:ops.legacy_schedule_observation_receipt"))
        yield (f"{slot}: a record carrying an extra admitted/verified flag",
               dict(perfect, admitted=True, verified=True, authority="authoritative"))
        yield f"{slot}: the same record as a dict subclass", _EvidenceDict(perfect)
        yield f"{slot}: an injected holder wearing the admitted shape", _LooksAdmitted(slot, perfect)
        yield (f"{slot}: an object whose attributes spell an admitted record",
               type("Forged", (), {"slot": slot, "owner": "authoritative", "record": perfect})())

    forged_green: list[str] = []
    forged_passing: list[str] = []
    for slot in health.EVIDENCE_SLOTS:
        for label, shape in caller_shapes(slot):
            bundle = _evidence()
            bundle[slot] = shape
            try:
                row = health.assurance_health_row(
                    scope=SCOPE, workflow_truth=_truth(SCOPE), evidence=bundle, now=NOW)
            except health.AssuranceHealthContractError:
                continue  # a refusal is an acceptable non-green outcome
            PROJECTED.append(row)
            if row["green"] or row["state"] == health.GREEN_STATE \
                    or row["capability_stage"] == "act":
                forged_green.append(label)
            if row["evidence"][slot]["state"] == "passing":
                forged_passing.append(label)
    check("no caller-controlled evidence shape reaches passing on any layer",
          not forged_passing, json.dumps(forged_passing[:4]))
    check("no caller-controlled evidence shape reaches green or act capability",
          not forged_green, json.dumps(forged_green[:4]))

    # ---- the whole bundle at once, through both public entry points ---------
    whole = health.assurance_health_row(
        scope=SCOPE, workflow_truth=_truth(SCOPE), evidence=_evidence(), now=NOW)
    PROJECTED.append(whole)
    check("six perfectly shaped caller-supplied receipts are unreadable, never passing",
          all(whole["evidence"][slot]["state"] == "unreadable"
              for slot in health.EVIDENCE_SLOTS)
          and not whole["green"] and whole["capability_stage"] == "unavailable",
          json.dumps({slot: whole["evidence"][slot]["state"]
                      for slot in health.EVIDENCE_SLOTS}))
    check("each unreadable reason names the owed seam rather than a generic absence",
          all(any("no evidence owner" in reason
                  for reason in whole["evidence"][slot]["reasons"])
              for slot in health.EVIDENCE_SLOTS),
          json.dumps(whole["evidence"]["controller_assessment"]["reasons"]))
    census = health.assurance_health(scopes=[
        {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence()},
        {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
         "evidence": _evidence(WORKFLOW_ONLY)}], now=NOW)
    PROJECTED.extend(census["rows"])
    check("a whole census of caller-supplied receipts renders no green row",
          census["summary"]["green"] == 0
          and census["summary"]["states"]["healthy"] == 0,
          json.dumps(census["summary"]["states"]))

    # ---- the retired admission path is gone, not renamed --------------------
    # THE DEFECT THIS PINS.  This module used to export an admission function
    # that minted a PASSING controller record out of an F09-SHAPED ROW its caller
    # supplied, and a review reproduced ``controller_assessment: passing`` from a
    # hand-written census.  Nothing in that chain was a read.  A rename would have
    # left the route open, so the function and its holder were removed.
    retired = [name for name in
               ("admit_scheduler_observation_receipt", "AdmittedEvidence",
                "admit_evidence", "admit")
               if hasattr(health, name)]
    check("no admission function survives anywhere on the module, under any name",
          not retired, json.dumps(retired))
    minting = [name for name in dir(health)
               if not name.startswith("_") and "admit" in name.lower()]
    check("nothing named like an admission point is reachable on the module",
          not minting, json.dumps(minting))


def public_surface_guard_checks(health) -> None:
    """PARSER-BACKED. The public names of the module, read out of its own syntax.

    WHY A PARSER AND NOT A REGEX.  The claim being made is about the module's
    PUBLIC SURFACE, and that is a syntactic fact: which top-level names it binds,
    and which of them ``__all__`` exports.  A regex over the text can be fooled by
    a name inside a docstring, a comment or a string, in either direction -- and a
    guard that can be fooled is worse than none, because it reports green.  So
    this parses ``lib/assurance_health.py`` with ``ast`` and reads the bindings.

    WHAT IT FORBIDS, exactly: no public name may be a route that turns
    caller-controlled input into a privileged outcome.  Two checks carry that.
    (1) The classifier and every admission-shaped name must be absent from both
    ``__all__`` and the module's non-underscore top-level bindings.  (2) Every
    public callable is CALLED with caller-controlled shapes, and the privileged
    strings must not appear anywhere in what comes back.
    """
    import ast
    import inspect

    source = (REPO / "lib" / "assurance_health.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    bound: set[str] = set()
    declared_all: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
                    if target.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                        declared_all = [element.value for element in node.value.elts
                                        if isinstance(element, ast.Constant)
                                        and isinstance(element.value, str)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)

    check("the module declares an explicit public export list",
          bool(declared_all), json.dumps(declared_all))
    public = {name for name in bound if not name.startswith("_")}
    check("every exported name is actually bound at module level",
          not sorted(set(declared_all) - bound),
          json.dumps(sorted(set(declared_all) - bound)))
    check("the parser and the imported module agree on the public names",
          public == {name for name in dir(health) if not name.startswith("_")},
          json.dumps(sorted(public
                            ^ {name for name in dir(health) if not name.startswith("_")})))

    # THE FORBIDDEN ROUTES, by the shape of what they do rather than by one name:
    # anything that classifies caller shapes into a label, and anything that
    # admits caller shapes as evidence.
    forbidden_public = sorted(
        name for name in public | set(declared_all)
        if any(token in name.lower() for token in
               ("predicate", "unwired", "admit", "fixture", "compile", "classify")))
    check("no classifier, admission, fixture or compile route is public",
          not forbidden_public, json.dumps(forbidden_public))
    for name in ("_label_predicate", "_label_predicate_row",
                 "_test_only_hypothetical_row", "_test_only_hypothetical_census"):
        check(f"{name} is module-private and unexported",
              name.startswith("_") and name in bound and name not in declared_all)

    # ---- every public callable, every caller-controlled shape ---------------
    # THE PRIVILEGED STRINGS IN THEIR DECISIVE FORM.  A bare "passing" also occurs
    # inside prose ("a current passing receipt ... is required"), and a bare "act"
    # inside ``capabilities_withdrawn``, which is the OPPOSITE of the privileged
    # outcome -- so the probe looks for each one exactly where it decides
    # something.  The control at the end of this function proves the probe fires.
    privileged = ('"state": "healthy"', '"state": "passing"',
                  '"capability_stage": "act"', '"green": true')
    perfect_evidence = _evidence()
    perfect_bundle = {"scope": SCOPE, "workflow_truth": _truth(SCOPE),
                      "evidence": perfect_evidence}
    shapes: dict[str, tuple[Any, ...]] = {
        "scope": (SCOPE, WORKFLOW_ONLY, {"workflow_key": "k", "workflow_version": 1},
                  {"workflow_key": "k", "workflow_version": 1, "state": "healthy"}),
        "scopes": ([perfect_bundle], [perfect_bundle, dict(perfect_bundle, scope=WORKFLOW_ONLY)],
                   [dict(perfect_bundle, workflow_truth={"state": "healthy",
                                                         "green": True})]),
        "workflow_truth": (_truth(SCOPE), {"state": "healthy", "green": True},
                           {"schema_version": "assurance-health.v1", "state": "healthy"}),
        "evidence": (perfect_evidence,
                     {slot: dict(_ev(slot), state="passing", admitted=True)
                      for slot in health.EVIDENCE_SLOTS},
                     {slot: "passing" for slot in health.EVIDENCE_SLOTS}),
        "now": (NOW,),
        "field": ("scope",),
    }

    reached: list[str] = []
    called = 0
    for name in sorted(set(declared_all) | public):
        member = getattr(health, name, None)
        if not callable(member) or inspect.isclass(member):
            continue
        try:
            signature = inspect.signature(member)
        except (TypeError, ValueError):  # pragma: no cover - builtins have none
            continue
        options = []
        for parameter in signature.parameters.values():
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue
            options.append([(parameter.name, value)
                            for value in shapes.get(parameter.name, (None, "healthy"))])
        if not options:
            continue
        for combination in itertools.product(*options):
            called += 1
            try:
                returned = member(**dict(combination))
            except Exception:
                continue  # a refusal is an acceptable non-privileged outcome
            rendered = json.dumps(returned, default=str)
            if any(token in rendered for token in privileged):
                reached.append(f"{name}({', '.join(key for key, _ in combination)})")
    check(f"no public callable yields a privileged outcome from any caller shape "
          f"({called} calls)", not reached, json.dumps(sorted(set(reached))[:4]))
    check("the guard actually exercised the public callables it claims to",
          called >= 20, str(called))

    # THE CONTROL. The same shapes, through the module-private classifier, DO
    # reach healthy -- which is what makes the assertion above a measurement of
    # the public surface rather than of a fixture that could never be green.
    control = health._test_only_hypothetical_row(
        scope=SCOPE, workflow_truth=_truth(SCOPE), evidence=perfect_evidence,
        now=NOW)["would_be_healthy_if_authoritative"]
    check("the control proves these shapes WOULD be healthy if they were authority",
          control["state"] == "healthy" and control["green"] is True,
          control["state"])
    rendered_control = json.dumps(control, default=str)
    check("the probe above detects every privileged string when one is really there",
          all(token in rendered_control for token in privileged),
          json.dumps([token for token in privileged if token not in rendered_control]))


MANIFEST_OWNER = "ops.job dispatcher"


def _census(*, enabled=True, surfaces=(), keys=(("assurance-fabric-child", 1),),
            shadow="accepted", canary="accepted", now=NOW):
    """One census from the real V5-F09 projection; this suite invents no census."""
    from lib.control_plane_workflow_truth import workflow_truth
    declarations, definitions, acceptances = [], [], []
    for key, version in keys:
        declarations.append({"key": key, "version": version, "enabled": enabled,
                             "legacy_schedule": {"provider": "none", "status": "disabled"}})
        definitions.append({"key": key, "version": version, "enabled": enabled,
                            "execution_contract": {}, "legacy_disabled_at": None})
        for mode, status in (("shadow", shadow), ("canary", canary)):
            if status:
                acceptances.append({"workflow_key": key, "workflow_version": version,
                                    "mode": mode, "status": status})
    return workflow_truth(
        declarations=declarations, definitions=definitions, acceptances=acceptances,
        surfaces=list(surfaces), completion={}, observation_max_age_seconds=MAX_AGE, now=now)


# Five minutes before the projection instant, so it is inside the registry's own
# 900-second observation window; OBSERVED (one hour back) is outside it and is
# used deliberately for the stale case below.
CURRENT_OBSERVED = "2026-09-09T11:55:00+00:00"


def _surface(surface_id, *, key="assurance-fabric-child", version=1,
             scheduler_state="enabled", observed=CURRENT_OBSERVED):
    surface = {"workflow_key": key, "workflow_version": version, "surface_id": surface_id,
               "locator": f"com.carr.{surface_id}", "scheduler_kind": "launchd",
               "duplicate_group": None, "disable_receipt_ref": None, "observation": None}
    if scheduler_state is not None:
        surface["observation"] = {"scheduler_state": scheduler_state, "observed_at": observed}
    return surface


def _reading(*, surfaces=(), owners=_DEFAULT, **census_kwargs):
    census = _census(surfaces=surfaces, **census_kwargs)
    return {"available": True, "census": census, "surfaces": list(surfaces),
            "owners": ({"assurance-fabric-child@v1": MANIFEST_OWNER}
                       if owners is _DEFAULT else owners)}


def source_adapter_checks() -> None:
    """The seam that binds this projection to a reading a health surface already has.

    WHAT THESE PIN.  EVERY layer is declared unread and named, never defaulted --
    the controller readback included, because a scheduler observation entry inside
    a caller-supplied snapshot is the caller's assertion and not a reading.  No
    reading that passes through this seam can produce a green or even a degraded
    row: there is nothing here that could become a determinate fact about a scope.
    """
    import lib.assurance_health_sources as sources

    unavailable = sources.assurance_health_from_snapshot(
        {"available": False, "reason": "control-plane rows unreadable (OperationalError)"},
        now=NOW)
    check("an unavailable reading is reported unavailable, never as an empty census",
          unavailable["available"] is False
          and "OperationalError" in unavailable["reason"], json.dumps(unavailable))
    for bad, label in ((None, "no reading at all"), ({}, "an empty reading"),
                       ({"available": True, "census": {"rows": []}}, "a census from nowhere")):
        result = sources.assurance_health_from_snapshot(bad, now=NOW)
        check(f"{label} is refused rather than projected",
              result["available"] is False, json.dumps(result))

    reading = _reading(surfaces=[_surface("assurance-fabric-child.launchd.v1")])
    projected = sources.assurance_health_from_snapshot(reading, now=NOW)
    check("a reading with one exact controller readback projects a bound scope",
          projected["available"] and projected["projection"]["summary"]["scopes"] == 1,
          json.dumps(projected.get("reason", "")))
    row = projected["projection"]["rows"][0]
    PROJECTED.append(row)
    controller = row["evidence"]["controller_assessment"]
    # THE DEFECT THIS PINS. This reading DOES hold a well-formed, in-window
    # observation receipt for this exact workflow, and the layer is still unread:
    # the snapshot was written by this test, and a receipt a caller wrote is not a
    # reading of the store that owns it. The earlier cut rendered it ``passing``.
    check("a well-formed in-window receipt in the supplied snapshot is still UNREAD",
          controller["state"] == "unreadable", controller["state"])
    check("the surface says what the reading held and what a reading would owe",
          any("holds one scheduler observation receipt" in note
              and "ops.legacy_schedule_observation_receipt" in note
              for note in projected["input_notes"]["assurance-fabric-child@v1"]),
          json.dumps(projected["input_notes"]["assurance-fabric-child@v1"]))
    for slot in ("artifact_assessment", "execution_assessment", "candidate_outcome_oracle",
                 "activation_readback"):
        check(f"{slot} is declared unread by this surface rather than defaulted",
              row["evidence"][slot]["state"] == "unreadable", row["evidence"][slot]["state"])
    check("the business outcome layer is unbindable: the census carries no Work Request",
          row["evidence"]["actual_business_outcome"]["state"] == "unbindable",
          row["evidence"]["actual_business_outcome"]["state"])
    check("no reading that reaches this surface can render green",
          projected["projection"]["summary"]["green"] == 0
          and not row["green"], row["state"])
    notes = projected["input_notes"]["assurance-fabric-child@v1"]
    for slot in sources.UNREAD_LAYER_SOURCE:
        if slot == "controller_assessment":
            continue  # carries its own note, asserted above with what the reading held
        check(f"the reader is told which surface would supply {slot}",
              any(note.startswith(f"{slot}:") and "would come from" in note for note in notes),
              json.dumps(notes))

    none_read = sources.assurance_health_from_snapshot(_reading(), now=NOW)
    absent = none_read["projection"]["rows"][0]["evidence"]["controller_assessment"]
    PROJECTED.append(none_read["projection"]["rows"][0])
    check("a reading holding no receipt is unread too, and says it held none",
          absent["state"] == "unreadable"
          and any("holds no scheduler observation receipt" in note
                  for note in none_read["input_notes"]["assurance-fabric-child@v1"]),
          absent["state"])

    two = sources.assurance_health_from_snapshot(_reading(surfaces=[
        _surface("assurance-fabric-child.launchd.v1"),
        _surface("assurance-fabric-child.launchd.v2")]), now=NOW)
    ambiguous = two["projection"]["rows"][0]["evidence"]["controller_assessment"]
    PROJECTED.append(two["projection"]["rows"][0])
    check("two receipts for one workflow are not silently reduced to one controller fact",
          ambiguous["state"] == "unreadable", ambiguous["state"])
    check("the ambiguity names both surface identities rather than picking one",
          all(surface_id in " ".join(two["input_notes"]["assurance-fabric-child@v1"])
              for surface_id in ("assurance-fabric-child.launchd.v1",
                                 "assurance-fabric-child.launchd.v2")),
          json.dumps(two["input_notes"]))

    # NO SUPPLIED RECEIPT MOVES A LABEL, in either direction. A receipt inside the
    # registry's own window and one two hours outside it produce the same row,
    # because neither was read by anything that could vouch for it.
    stale = sources.assurance_health_from_snapshot(
        _reading(surfaces=[_surface("assurance-fabric-child.launchd.v1",
                                    observed=OBSERVED)]), now=NOW)
    stale_row = stale["projection"]["rows"][0]
    PROJECTED.append(stale_row)
    check("an out-of-window supplied readback is unread, exactly as an in-window one is",
          stale_row["evidence"]["controller_assessment"]["state"] == "unreadable",
          stale_row["evidence"]["controller_assessment"]["state"])
    check("the freshness of a supplied receipt changes no label on this surface",
          stale_row["state"] == row["state"] and not stale_row["green"],
          f"{stale_row['state']} vs {row['state']}")
    # Capability missing because a layer could not be READ is unproven, never
    # withdrawn: nothing on this surface withdrew anything, so the row claims no
    # state at all rather than reporting a failure it cannot evidence.
    check("a row with nothing determinate claims no state rather than a failure",
          stale_row["state"] == "unknown"
          and any("no state is claimed" in reason for reason in stale_row["reasons"])
          and not any("withdrew" in reason for reason in stale_row["reasons"]),
          json.dumps(stale_row["reasons"]))

    ownerless = sources.assurance_health_from_snapshot(
        _reading(surfaces=[_surface("assurance-fabric-child.launchd.v1")], owners={}), now=NOW)
    check("a workflow with no declared owner is reported unprojectable, not given one",
          ownerless["projection"]["summary"]["scopes"] == 0
          and len(ownerless["unprojectable"]) == 1
          and "inventory.owner" in ownerless["unprojectable"][0]["reason"],
          json.dumps(ownerless["unprojectable"]))

    disabled = sources.assurance_health_from_snapshot(
        _reading(enabled=False, surfaces=[_surface("assurance-fabric-child.launchd.v1")]),
        now=NOW)
    disabled_row = disabled["projection"]["rows"][0]
    PROJECTED.append(disabled_row)
    check("a disabled workflow is disabled on this surface too, and disabled is not green",
          disabled_row["state"] == "disabled" and not disabled_row["green"],
          disabled_row["state"])

    # EACH SCOPE'S EVIDENCE IS ITS OWN. Two workflows, two receipts, one of them
    # outside the registry's window: each row's notes must describe the receipt
    # that binds IT, so nothing from one scope travels to the other through the seam.
    fenced = _reading(
        keys=(("degrading", 1), ("unaffected", 1)),
        surfaces=[_surface("degrading.launchd.v1", key="degrading", observed=OBSERVED),
                  _surface("unaffected.launchd.v1", key="unaffected")],
        owners={"degrading@v1": MANIFEST_OWNER, "unaffected@v1": MANIFEST_OWNER})
    fenced_rows = {row["scope"]["workflow_key"]: row
                   for row in sources.assurance_health_from_snapshot(
                       fenced, now=NOW)["projection"]["rows"]}
    PROJECTED.extend(fenced_rows.values())
    fenced_notes = sources.assurance_health_from_snapshot(fenced, now=NOW)["input_notes"]
    for key in ("degrading", "unaffected"):
        check(f"{key}'s notes name the receipt that binds {key}, not another scope's",
              any(f"{key}.launchd.v1" in note for note in fenced_notes[f"{key}@v1"])
              and not any(f"{'unaffected' if key == 'degrading' else 'degrading'}.launchd.v1"
                          in note for note in fenced_notes[f"{key}@v1"]),
              json.dumps(fenced_notes[f"{key}@v1"]))
    check("neither scope's controller layer is readable, whatever its receipt said",
          all(row["evidence"]["controller_assessment"]["state"] == "unreadable"
              for row in fenced_rows.values()),
          json.dumps({k: v["evidence"]["controller_assessment"]["state"]
                      for k, v in fenced_rows.items()}))
    check("with nothing determinate read, neither scope is degraded or failed",
          all(row["state"] not in ("degraded", "failed") for row in fenced_rows.values()),
          json.dumps({k: v["state"] for k, v in fenced_rows.items()}))

    # TWO UNBOUND SCOPES. The live census binds every scope by workflow identity
    # alone, so the summary cannot key any per-scope list on a Work Request
    # identity that is absent from all of them. (The degraded LIST itself is
    # exercised with two genuinely degraded scopes in scoped_degradation_checks;
    # nothing this seam can read is determinate enough to degrade a scope.)
    both_stale = _reading(
        keys=(("degrading", 1), ("also-degrading", 1)),
        surfaces=[_surface("degrading.launchd.v1", key="degrading", observed=OBSERVED),
                  _surface("also-degrading.launchd.v1", key="also-degrading",
                           observed=OBSERVED)],
        owners={"degrading@v1": MANIFEST_OWNER, "also-degrading@v1": MANIFEST_OWNER})
    many = sources.assurance_health_from_snapshot(both_stale, now=NOW)
    check("a census of unbound scopes summarises without an identity to sort on",
          many["available"] is True, json.dumps(many.get("reason", "")))
    if many["available"]:
        PROJECTED.extend(many["projection"]["rows"])
        ordered = [f"{r['scope']['workflow_key']}@v{r['scope']['workflow_version']}"
                   for r in many["projection"]["rows"]]
        check("two Work Request-less scopes sort against each other without an identity",
              ordered == ["also-degrading@v1", "degrading@v1"], json.dumps(ordered))

    source = Path(sources.__file__).read_text(encoding="utf-8")
    check("the seam reads nothing itself: no file, process, socket or database route",
          not any(token in source for token in
                  ("import os", "import subprocess", "import socket", "import psycopg",
                   "open(", "requests.", "datetime.now")), "an import would make it a reader")


def surface_wiring_checks() -> None:
    """The health surface itself, driven hermetically through its own fixture door.

    A projection nothing reads is not wired to anything.  This drives the real
    ``tools/health-check.py`` canonical reader against a fixture snapshot -- no
    database, no network, no clock of ours -- and pins that the section prints
    evidence-backed states, names the layers this surface does not read, and
    never reports a green scope out of a reading that cannot contain one.
    """
    import os
    import subprocess
    import tempfile
    from datetime import datetime, timedelta, timezone
    from lib.control_plane_workflow_truth import workflow_truth

    now = datetime.now(timezone.utc).isoformat()
    surfaces = [{"workflow_key": "assurance-fabric-child", "workflow_version": 1,
                 "surface_id": "assurance-fabric-child.launchd.v1",
                 "locator": "com.carr.assurance-fabric-child", "scheduler_kind": "launchd",
                 "duplicate_group": None, "disable_receipt_ref": None,
                 "observation": {"scheduler_state": "enabled", "observed_at": now}}]
    census = workflow_truth(
        declarations=[{"key": "assurance-fabric-child", "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}},
                      {"key": "ownerless-workflow", "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}}],
        definitions=[{"key": "assurance-fabric-child", "version": 1, "enabled": True,
                      "execution_contract": {}, "legacy_disabled_at": None}],
        acceptances=[{"workflow_key": "assurance-fabric-child", "workflow_version": 1,
                      "mode": "shadow", "status": "accepted"}],
        surfaces=surfaces, completion={}, observation_max_age_seconds=900, now=now)
    snapshot = {"errors": [], "workflows": {
        "available": True, "census": census, "surfaces": surfaces,
        "owners": {"assurance-fabric-child@v1": "ops.job dispatcher"}}}

    # OUTSIDE THE REPOSITORY ON PURPOSE: a selftest that writes into the tree it
    # is invoked in is the exact thing ops/selftest-git-isolation-check.py exists
    # to catch, and a shared fixture path would cross-wire concurrent CI runs.
    handle, path = tempfile.mkstemp(suffix=".json", prefix="assurance-health-surface-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh)
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools/health-check.py"), "--canonical",
             "--section", "jobs", "--fixture", path],
            cwd=str(REPO), text=True, capture_output=True, timeout=120)
    finally:
        os.unlink(path)
    out = proc.stdout

    check("the canonical health surface prints an assurance-health section at all",
          "Assurance health — evidence-backed state per bound workflow scope" in out,
          out[-400:])
    check("the section reports a bound scope and no green one",
          "1 bound scope(s)" in out and "; 0 green" in out, out[-400:])
    check("a workflow whose owner the manifest never declared is named, not projected",
          "UNPROJECTABLE ownerless-workflow@v1" in out
          and "inventory.owner" in out, out[-400:])
    check("the layers this surface does not read are printed as the gap they are",
          all(slot in out for slot in
              ("artifact_assessment", "execution_assessment", "candidate_outcome_oracle",
               "activation_readback"))
          and "NOT READ BY THIS SURFACE" in out, out[-400:])
    check("the unbindable outcome layer is named on the surface, not silently dropped",
          "UNBINDABLE ON THIS CENSUS" in out and "actual_business_outcome" in out, out[-400:])
    # Scoped to THIS section's own findings on purpose: the fixture carries no job
    # ledger, so the surrounding jobs section reports its own absence and the
    # process rc belongs to that, not to assurance health.
    check("a reading that holds no failure records no assurance-health finding",
          "CANONICAL_FINDING assurance_health_failed" not in out
          and "CANONICAL_FINDING assurance_health_degraded" not in out, out[-400:])
    check("no scope reached a healthy label on a reading that cannot contain one",
          " 0 healthy" in out and "1 unknown" in out, out[-400:])

    # INJECTION ON THE REAL SURFACE, AND WHAT IT MAY NOT DO. One workflow's
    # controller readback is pushed outside the registry's own observation window
    # and the other's is left current -- and NEITHER scope may move, because both
    # receipts were written into this fixture by this test. A snapshot a caller
    # composed is the caller's assertion about a store, not a reading of it, and
    # the review that produced this correction reproduced a passing controller
    # layer out of exactly such a snapshot. The surface may print what it does not
    # know; it may not print a finding it cannot evidence.
    stale_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    injected_surfaces = [
        dict(surfaces[0], surface_id="degrading.launchd.v1", workflow_key="degrading",
             observation={"scheduler_state": "enabled", "observed_at": stale_at}),
        dict(surfaces[0], surface_id="unaffected.launchd.v1", workflow_key="unaffected",
             observation={"scheduler_state": "enabled", "observed_at": now}),
    ]
    injected_census = workflow_truth(
        declarations=[{"key": key, "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}}
                      for key in ("degrading", "unaffected")],
        definitions=[{"key": key, "version": 1, "enabled": True,
                      "execution_contract": {}, "legacy_disabled_at": None}
                     for key in ("degrading", "unaffected")],
        acceptances=[{"workflow_key": key, "workflow_version": 1,
                      "mode": "shadow", "status": "accepted"}
                     for key in ("degrading", "unaffected")],
        surfaces=injected_surfaces, completion={}, observation_max_age_seconds=900, now=now)
    injected = {"errors": [], "workflows": {
        "available": True, "census": injected_census, "surfaces": injected_surfaces,
        "owners": {"degrading@v1": "ops.job dispatcher",
                   "unaffected@v1": "ops.job dispatcher"}}}
    handle, path = tempfile.mkstemp(suffix=".json", prefix="assurance-health-injected-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(injected, fh)
        injected_proc = subprocess.run(
            [sys.executable, str(REPO / "tools/health-check.py"), "--canonical",
             "--section", "jobs", "--fixture", path],
            cwd=str(REPO), text=True, capture_output=True, timeout=120)
    finally:
        os.unlink(path)
    section = injected_proc.stdout.split("Assurance health —", 1)[-1]

    check("an injected receipt cannot degrade a scope on the real surface",
          "degrading v1 DEGRADED" not in section
          and "unaffected v1 DEGRADED" not in section, section[:600])
    # PRINTED, not recorded: _canonical_finding writes one stdout line and there
    # is no record-layer seam behind it.  The claim is kept exactly that size --
    # and here the line is not printed at all, because nothing was evidenced.
    check("no CANONICAL_FINDING line is printed out of a composed snapshot",
          "CANONICAL_FINDING assurance_health_degraded" not in section
          and "CANONICAL_FINDING assurance_health_failed" not in section, section[:600])
    check("both scopes are carried as unknown rather than dropped or guessed at",
          "2 bound scope(s): 0 healthy, 0 degraded, 0 failed, 2 unknown" in section,
          section[:600])
    check("the surface names the controller layer as one it did not read",
          "NOT READ BY THIS SURFACE" in section
          and "controller_assessment" in section, section[:600])

    # TWO WORKFLOW-ONLY SCOPES, ON THE REAL SURFACE.
    #
    # THE DEFECT THIS PINS.  Every scope the live census binds is bound by
    # workflow identity alone and carries NO Work Request id, and the summary
    # ordered and listed its scopes by that absent identity.  One scope never
    # compared anything, so every fixture stayed green while the first live
    # reading with two of them raised TypeError inside the projection -- which the
    # adapter does not catch, so the whole section printed "UNAVAILABLE
    # (TypeError)" instead of the census it holds.  The assertion is therefore
    # driven through the surface with TWO of them.  The summary's degraded LIST,
    # which is the other half of that sort, is exercised with two genuinely
    # degraded scopes in workflow_only_scope_checks, because no reading this
    # surface can perform is determinate enough to degrade anything.
    both_degraded_surfaces = [
        dict(surfaces[0], surface_id=f"{key}.launchd.v1", workflow_key=key,
             observation={"scheduler_state": "enabled", "observed_at": stale_at})
        for key in ("degrading", "also-degrading")]
    both_degraded_census = workflow_truth(
        declarations=[{"key": key, "version": 1, "enabled": True,
                       "legacy_schedule": {"provider": "none", "status": "disabled"}}
                      for key in ("degrading", "also-degrading")],
        definitions=[{"key": key, "version": 1, "enabled": True,
                      "execution_contract": {}, "legacy_disabled_at": None}
                     for key in ("degrading", "also-degrading")],
        acceptances=[{"workflow_key": key, "workflow_version": 1,
                      "mode": "shadow", "status": "accepted"}
                     for key in ("degrading", "also-degrading")],
        surfaces=both_degraded_surfaces, completion={},
        observation_max_age_seconds=900, now=now)
    both_degraded = {"errors": [], "workflows": {
        "available": True, "census": both_degraded_census,
        "surfaces": both_degraded_surfaces,
        "owners": {"degrading@v1": "ops.job dispatcher",
                   "also-degrading@v1": "ops.job dispatcher"}}}
    handle, path = tempfile.mkstemp(suffix=".json", prefix="assurance-health-two-degraded-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(both_degraded, fh)
        two_proc = subprocess.run(
            [sys.executable, str(REPO / "tools/health-check.py"), "--canonical",
             "--section", "jobs", "--fixture", path],
            cwd=str(REPO), text=True, capture_output=True, timeout=120)
    finally:
        os.unlink(path)
    two_section = two_proc.stdout.split("Assurance health —", 1)[-1]
    check("two workflow-only scopes do not make the surface unavailable",
          "assurance health   UNAVAILABLE" not in two_section
          and "TypeError" not in two_section, two_section[:600])
    check("both workflow-only scopes are counted on the surface",
          "2 bound scope(s): 0 healthy, 0 degraded, 0 failed, 2 unknown" in two_section,
          two_section[:600])
    # The census-level row sort is the half of that defect this surface still
    # exercises: two rows, both with work_request_id None, ordered against each
    # other. The summary's degraded LIST is the other half, and it is driven with
    # two genuinely degraded scopes in workflow_only_scope_checks.
    check("the summary was computed over both unbound rows rather than refused",
          "; 0 green" in two_section
          and "0 disabled, 0 not-yet-operational" in two_section, two_section[:600])


def main() -> int:
    try:
        import lib.assurance_health as health
    except Exception as exc:  # red until the implementation exists
        print(f"assurance-health-selftest: implementation unavailable: {exc}")
        return 1

    display_state_checks(health)
    never_green_matrix_checks(health)
    preactivation_chain_checks(health)
    actual_outcome_separation_checks(health)
    scoped_degradation_checks(health)
    workflow_truth_governance_checks(health)
    scope_identity_checks(health)
    workflow_only_scope_checks(health)
    caller_evidence_is_never_authority_checks(health)
    public_surface_guard_checks(health)
    source_adapter_checks()
    surface_wiring_checks()
    refusal_checks(health)
    output_discipline_checks(health)

    print(f"\nassurance-health-selftest: {total-len(failures)}/{total} passed")
    if failures:
        print("FAILURES: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
