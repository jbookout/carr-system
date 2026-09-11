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

import json
import sys
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
    from lib.assurance_health import assurance_health_row
    row = assurance_health_row(
        scope=scope,
        workflow_truth=_truth(scope) if truth is _DEFAULT else truth,
        evidence=_evidence(scope) if evidence is _DEFAULT else evidence,
        now=now)
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
    from lib.assurance_health import assurance_health

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
          and projection["summary"]["degraded_scopes"] == ["wr-a01-0001"])


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
    for bad_version in (True, False, 1.9, 1.0, "1", "01", None, [], {}, 0, -1, 2):
        truth = {**_truth(SCOPE), "workflow_version": bad_version}
        try:
            _row(truth=truth)
            refused = False
        except health.AssuranceHealthContractError:
            refused = True
        check(f"workflow truth version {bad_version!r} ({type(bad_version).__name__}) "
              "cannot join integer scope version 1", refused)

    for bad_key in (None, 123, True, [], {}, "", " " + SCOPE["workflow_key"]):
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

    from lib.assurance_health import assurance_health
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

    both = health.assurance_health(scopes=[
        {"scope": WORKFLOW_ONLY, "workflow_truth": _truth(WORKFLOW_ONLY),
         "evidence": _evidence(WORKFLOW_ONLY)},
        {"scope": SCOPE, "workflow_truth": _truth(SCOPE), "evidence": _evidence(SCOPE)},
    ], now=NOW)
    PROJECTED.extend(both["rows"])
    check("the same workflow bound with and without a Work Request is two scopes",
          both["summary"]["scopes"] == 2, json.dumps(both["summary"]))
    check("a workflow-only row and a Work Request row sort deterministically",
          [r["scope"]["work_request_id"] for r in both["rows"]] == [None, "wr-a01-0001"],
          json.dumps([r["scope"]["work_request_id"] for r in both["rows"]]))

    for bad in ("", "   ", 7, True, [], {}):
        try:
            health.assurance_health_row(
                scope={**WORKFLOW_ONLY, "work_request_id": bad},
                workflow_truth=_truth(WORKFLOW_ONLY), evidence=_evidence(WORKFLOW_ONLY),
                now=NOW)
            refused = False
        except health.AssuranceHealthContractError:
            refused = True
        check(f"a malformed Work Request identity {bad!r} is refused, never coerced to unbound",
              refused)

    check("unbindable is a declared evidence state that is not a passing one",
          "unbindable" in health.EVIDENCE_STATES
          and "unbindable" not in health.DETERMINATE_NONPASS_EVIDENCE_STATES
          and "unbindable" not in health.INDETERMINATE_EVIDENCE_STATES)


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
    refusal_checks(health)
    output_discipline_checks(health)

    print(f"\nassurance-health-selftest: {total-len(failures)}/{total} passed")
    if failures:
        print("FAILURES: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
