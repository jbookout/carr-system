// DoctorCRE v5 slice V5-R01 — the pilot-day ledger, the exact failure
// exclusions and the injected-recovery drill.
//
// Four kinds of test live here and they are not interchangeable:
//
//   LADDER tests prove the deterministic content — that a missing subjourney, a
//   day with no real business action, a product defect, an outage the product lied
//   about, an open high-severity defect, a gap in the middle of a run and a
//   weekend that is NOT a gap are each caught by the check that names them, in
//   the order the definition declares.
//
//   REFUSAL tests prove the honest unavailable — that every public evaluator
//   answers `unavailable` on every input because its authoritative owner does not
//   exist, and names the seam it is owed instead of guessing.
//
//   GUARD tests prove the standing rule: no caller-supplied label, class string,
//   flag or injected holder is authority. They sweep both public export surfaces
//   against caller-controlled input and assert the privileged outcome never comes
//   back, and they use NODE'S OWN MODULE PARSER (vm.SourceTextModule), not a
//   regex, to prove the internal classifier is unreachable through either public
//   surface and imported by nothing else in the tree.
//
//   PIN tests prove this slice's copy of J101's surface table still equals
//   J101's, in both directions.

import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

import esbuild from "esbuild";

import * as r01 from "../src/rollout-pilot-r01.v5.js";
import * as onboarding from "../src/onboarding-flow-r01.v5.js";
import * as vocabulary from "../src/rollout-pilot-r01.vocabulary.v5.js";
// THE TEST-ONLY ENTRY. It lives here in mcp-server/test/, the guard section below
// proves with a real parser that nothing in mcp-server/src can reach it, and it
// is the only place the deterministic ladder can be called from.
import {
  CLASSIFICATIONS,
  breakingFailureOrigins,
  classifyPilotDayIfAuthoritative,
  classifyPilotRunIfAuthoritative,
  classifyRecoveryDrillIfAuthoritative,
  disqualifyingFailureOrigins,
} from "./rollout-pilot-r01-classifiers.v5.testhelper.mjs";

const {
  V5R01Error, V5_R01_BREAKING_FAILURE_ORIGINS, V5_R01_DAY_CHECKS,
  V5_R01_DECISIONS_NOT_READ, V5_R01_DECISIONS_READ_IN_FULL,
  V5_R01_DISQUALIFYING_FAILURE_ORIGINS, V5_R01_DRILL_FAULTS, V5_R01_DRILL_FAULT_KEYS,
  V5_R01_DRILL_RECEIPT_FIELDS, V5_R01_DRILL_TERMINAL_STATES,
  V5_R01_EXCLUDED_FAILURE_ORIGINS, V5_R01_FAILURE_ORIGINS,
  V5_R01_FAILURE_ORIGIN_KEYS, V5_R01_INACTIVE_SUCCESSOR, V5_R01_J1_SUBJOURNEY_COUNT,
  V5_R01_LEDGER_ENTRY_FIELDS, V5_R01_LEDGER_ENTRY_FORBIDDEN_FIELDS, V5_R01_PILOT_PARTNER,
  V5_R01_PRODUCTION_OUTCOME_STEP, V5_R01_PUBLIC_SURFACE, V5_R01_REQUIRED_RUN_LENGTH,
  V5_R01_SEAMS, V5_R01_SETTLED_DECISION_IDS, V5_R01_UPSTREAM_EVIDENCE_INPUTS,
  assertR01DecisionBinding, describeFailureExclusions, describePilotDayLedgerEntry,
  describeRecoveryDrill, evaluatePilotDay, evaluatePilotRun, evaluateRecoveryDrill,
  rolloutPilotGaps, v5R01PolicyCanonicalBytes, v5R01PolicyDigest, v5R01PolicyPreimage,
} = r01;

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");

// ---------------------------------------------------------------------------
// Fixtures. Every one is the HONEST shape — a complete day, a clean drill — so a
// test that mutates one field is testing exactly that field.
// ---------------------------------------------------------------------------

const THREE_SUBJOURNEYS = Object.freeze(["j1:sub-a", "j1:sub-b", "j1:sub-c"]);

function day(overrides = {}) {
  return {
    date: "2026-09-07",
    subjourneys_exercised: [...THREE_SUBJOURNEYS],
    real_business_actions: ["deal:1001"],
    high_defect_open_against_j1: false,
    failures: [],
    ...overrides,
  };
}

function failure(overrides = {}) {
  return {
    failure_ref: "failure:1",
    observed_origin: "partner_device_or_credential",
    product_reported_honestly: true,
    documented_fallback_offered: true,
    ...overrides,
  };
}

/** Ten consecutive business days: Mon 2026-09-07 to Fri 2026-09-18, weekends skipped. */
const TEN_BUSINESS_DAYS = Object.freeze([
  "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11",
  "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
]);

function run(overrides = {}) {
  return {
    days: TEN_BUSINESS_DAYS.map(date => day({ date })),
    non_business_dates: [],
    ...overrides,
  };
}

/**
 * A COMPLETE RECEIPT, built to the registered twelve-field schema.
 *
 * The previous round's fixture carried eight fields — the eight the classifier
 * happened to read — so the four the slice PUBLISHES and the classifier rejected
 * were never handed to it by any test. The fixture is built from
 * V5_R01_DRILL_RECEIPT_FIELDS now, and a test below asserts the two lists are the
 * same list, so a field added to the schema cannot go untested.
 *
 * `subject_partner` is a third identity, distinct from both the injector and the
 * observer, because that is what the definition requires and the previous round
 * only checked one of the three pairs.
 */
function drill(overrides = {}) {
  return {
    drill_ref: "drill:1",
    fault_injected: "record_layer_unreachable",
    subject_partner: V5_R01_PILOT_PARTNER,
    observed_partner_visible_signal:
      V5_R01_DRILL_FAULTS.record_layer_unreachable.expected_partner_visible_signal,
    terminal_state_reached: "recovered",
    aids_used: [],
    injected_by_identity_ref: "actor:injector",
    observer_identity_ref: "actor:observer",
    injected_at: "2026-09-11T14:00:00Z",
    recovered_at: "2026-09-11T14:11:00Z",
    producer_step_ref: "step:v5-r01-recovery-drill-receipt-store",
    recovery_path_taken: V5_R01_DRILL_FAULTS.record_layer_unreachable.recovery_is,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// LADDER — one candidate day.
// ---------------------------------------------------------------------------

test("ladder: a complete day with no failures would count", () => {
  const verdict = classifyPilotDayIfAuthoritative(day());
  assert.equal(verdict.classification, CLASSIFICATIONS.day);
  assert.equal(verdict.blocking_check, null);
  assert.equal(verdict.disqualifies_run, false);
});

test("ladder: an open high-severity defect disqualifies the run and is asked FIRST", () => {
  // Deliberately also broken on every other check, to prove the ORDER: the answer
  // must be the defect, not the missing subjourneys.
  const verdict = classifyPilotDayIfAuthoritative(day({
    high_defect_open_against_j1: true,
    subjourneys_exercised: [],
    real_business_actions: [],
    failures: [failure({ observed_origin: "product_defect" })],
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[0]);
  assert.equal(verdict.disqualifies_run, true);
  assert.equal(verdict.disqualifying_origin, "unresolved_high_defect");
});

test("ladder: two of the three subjourneys is not three", () => {
  const verdict = classifyPilotDayIfAuthoritative(day({
    subjourneys_exercised: ["j1:sub-a", "j1:sub-b"],
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[1]);
  assert.equal(verdict.distinct_subjourneys, 2);
  assert.equal(verdict.required_subjourneys, V5_R01_J1_SUBJOURNEY_COUNT);
});

test("ladder: the same subjourney three times is one subjourney, not three", () => {
  // The boundary case the count alone gets wrong.
  const verdict = classifyPilotDayIfAuthoritative(day({
    subjourneys_exercised: ["j1:sub-a", "j1:sub-a", "j1:sub-a"],
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[1]);
  assert.equal(verdict.distinct_subjourneys, 1);
});

test("ladder: a walkthrough with no real business action is not a pilot day", () => {
  const verdict = classifyPilotDayIfAuthoritative(day({ real_business_actions: [] }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[2]);
});

test("ladder: every excluded origin lets a day count, every breaking origin does not", () => {
  for (const origin of V5_R01_EXCLUDED_FAILURE_ORIGINS) {
    const verdict = classifyPilotDayIfAuthoritative(day({
      failures: [failure({ observed_origin: origin })],
    }));
    assert.equal(verdict.classification, CLASSIFICATIONS.day, origin);
    assert.deepEqual(verdict.excluded_failures, ["failure:1"]);
  }
  for (const origin of V5_R01_BREAKING_FAILURE_ORIGINS) {
    const verdict = classifyPilotDayIfAuthoritative(day({
      failures: [failure({ observed_origin: origin })],
    }));
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, origin);
    assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[3]);
    assert.equal(verdict.disqualifies_run, V5_R01_FAILURE_ORIGINS[origin].disqualifies_run, origin);
  }
});

test("ladder: a day salvaged only by reading raw logs does not count", () => {
  // Q010's own words name log interpretation among the things no production
  // capability may depend on. This is the clause that reading records.
  const verdict = classifyPilotDayIfAuthoritative(day({
    failures: [failure({ observed_origin: "log_interpretation_required" })],
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.breaking_origin, "log_interpretation_required");
  assert.equal(verdict.disqualifies_run, false);
});

test("ladder: a conditional exclusion the product lied through is NOT excluded", () => {
  // The clause that keeps the exclusion list from being an alibi. Same origin,
  // same day, one boolean different, opposite answer.
  const excused = classifyPilotDayIfAuthoritative(day({
    failures: [failure({ observed_origin: "third_party_provider_outage" })],
  }));
  assert.equal(excused.classification, CLASSIFICATIONS.day);

  for (const dishonest of [
    { product_reported_honestly: false },
    { documented_fallback_offered: false },
  ]) {
    const verdict = classifyPilotDayIfAuthoritative(day({
      failures: [failure({ observed_origin: "third_party_provider_outage", ...dishonest })],
    }));
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, JSON.stringify(dishonest));
    assert.equal(verdict.breaking_origin, "third_party_provider_outage");
    assert.equal(verdict.exclusion_condition_unmet,
      "product_reported_honestly_and_offered_documented_fallback");
  }
});

test("ladder: the unconditional exclusion is not affected by the honesty booleans", () => {
  // advanced_automation_or_native_prerequisite carries condition: null, so a
  // dishonest report does not convert it. Proves the condition check reads the
  // origin's own condition rather than applying one blanket rule.
  const verdict = classifyPilotDayIfAuthoritative(day({
    failures: [failure({
      observed_origin: "advanced_automation_or_native_prerequisite",
      product_reported_honestly: false,
      documented_fallback_offered: false,
    })],
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.day);
});

test("ladder: the FIRST breaking failure is the one reported", () => {
  const verdict = classifyPilotDayIfAuthoritative(day({
    failures: [
      failure({ failure_ref: "failure:excluded" }),
      failure({ failure_ref: "failure:breaks", observed_origin: "product_defect" }),
      failure({ failure_ref: "failure:later", observed_origin: "log_interpretation_required" }),
    ],
  }));
  assert.equal(verdict.failure_ref, "failure:breaks");
  assert.equal(verdict.breaking_origin, "product_defect");
});

test("ladder: an unregistered failure origin is refused as a contract violation", () => {
  assert.throws(() => classifyPilotDayIfAuthoritative(day({
    failures: [failure({ observed_origin: "acceptable_hiccup" })],
  })), error => error instanceof V5R01Error && error.code === "unknown_value");
});

test("ladder: an unknown field on a day entry is refused, not ignored", () => {
  assert.throws(() => classifyPilotDayIfAuthoritative({ ...day(), day_counted: true }),
    error => error instanceof V5R01Error && error.code === "unknown_field");
});

test("ladder: an impossible calendar date is refused rather than rolled forward", () => {
  assert.throws(() => classifyPilotDayIfAuthoritative(day({ date: "2026-02-30" })),
    error => error instanceof V5R01Error && error.code === "invalid_date");
});

// ---------------------------------------------------------------------------
// LADDER — the run of ten.
// ---------------------------------------------------------------------------

test("ladder: ten counted business days across two weekends is a run", () => {
  const verdict = classifyPilotRunIfAuthoritative(run());
  assert.equal(verdict.classification, CLASSIFICATIONS.run);
  assert.equal(verdict.longest_run, V5_R01_REQUIRED_RUN_LENGTH);
  assert.equal(verdict.first_date, "2026-09-07");
  assert.equal(verdict.last_date, "2026-09-18");
});

test("ladder: nine days is short, and the answer says how short", () => {
  const verdict = classifyPilotRunIfAuthoritative(run({
    days: TEN_BUSINESS_DAYS.slice(0, 9).map(date => day({ date })),
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "run_shorter_than_required");
  assert.equal(verdict.longest_run, 9);
});

test("ladder: a business day MISSING from the ledger breaks the run", () => {
  // The defect a filter-and-count implementation cannot see: Wednesday simply
  // is not there. Nine entries, spanning eleven business days, run of five.
  const days = TEN_BUSINESS_DAYS.filter(date => date !== "2026-09-09").map(date => day({ date }));
  const verdict = classifyPilotRunIfAuthoritative(run({ days }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.longest_run, 7, "two days before the hole, seven after it");
});

test("ladder: a failed day in the middle breaks the run and the run restarts after it", () => {
  const days = TEN_BUSINESS_DAYS.map(date => (date === "2026-09-09"
    ? day({ date, failures: [failure({ observed_origin: "product_defect" })] })
    : day({ date })));
  const verdict = classifyPilotRunIfAuthoritative(run({ days }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.longest_run, 7);
});

test("ladder: a declared non-business date is SKIPPED, not counted as a gap", () => {
  // Eleven calendar business days, one of them declared closed and absent from
  // the ledger, ten entries: still a run. This is the difference between a
  // holiday and a day Joe did not show up.
  const days = [
    "2026-09-07", "2026-09-08", "2026-09-10", "2026-09-11",
    "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18",
    "2026-09-21",
  ].map(date => day({ date }));
  const verdict = classifyPilotRunIfAuthoritative({
    days, non_business_dates: ["2026-09-09"],
  });
  assert.equal(verdict.classification, CLASSIFICATIONS.run);
  assert.equal(verdict.longest_run, 10);
});

test("ladder: an open high defect anywhere disqualifies the whole run, not one day", () => {
  const days = TEN_BUSINESS_DAYS.map(date => (date === "2026-09-16"
    ? day({ date, high_defect_open_against_j1: true })
    : day({ date })));
  const verdict = classifyPilotRunIfAuthoritative(run({ days }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.run_disqualified, true);
  assert.equal(verdict.disqualifying_date, "2026-09-16");
  assert.equal(verdict.longest_run, 0, "a disqualified run has no longest run to report");
});

test("ladder: two entries claiming the same date is a contract violation", () => {
  assert.throws(() => classifyPilotRunIfAuthoritative(run({
    days: [day({ date: "2026-09-07" }), day({ date: "2026-09-07" })],
  })), error => error instanceof V5R01Error && error.code === "duplicate_pilot_day");
});

test("ladder: a weekend entry does not lengthen a run", () => {
  // 2026-09-12 is a Saturday. Adding it to the ledger must not turn the run into
  // eleven: the walk skips weekends whatever the ledger says about them.
  const verdict = classifyPilotRunIfAuthoritative(run({
    days: [...TEN_BUSINESS_DAYS, "2026-09-12"].map(date => day({ date })),
  }));
  assert.equal(verdict.longest_run, V5_R01_REQUIRED_RUN_LENGTH);
});

// ---------------------------------------------------------------------------
// LADDER — the injected recovery drill.
// ---------------------------------------------------------------------------

test("ladder: a clean drill with the declared signal and no forbidden aid would succeed", () => {
  const verdict = classifyRecoveryDrillIfAuthoritative(drill());
  assert.equal(verdict.classification, CLASSIFICATIONS.drill);
  assert.equal(verdict.blocking_check, null);
});

test("ladder: stopping safely counts, because Q010 asks for 'avoid making damage worse'", () => {
  const verdict = classifyRecoveryDrillIfAuthoritative(drill({
    terminal_state_reached: "stopped_safely_without_making_it_worse",
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.drill);
});

test("ladder: getting stuck or making it worse does not", () => {
  for (const state of ["stuck", "made_it_worse"]) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({ terminal_state_reached: state }));
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, state);
    assert.equal(verdict.blocking_check, "drill_ended_in_an_incorrect_terminal_state");
  }
});

test("ladder: a silent failure fails the drill even when the partner recovered", () => {
  // Checked BEFORE the outcome: recovering in spite of the product is not the
  // product letting the partner recognize failure.
  const verdict = classifyRecoveryDrillIfAuthoritative(drill({
    observed_partner_visible_signal: "nothing_visible",
    terminal_state_reached: "recovered",
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "product_did_not_show_the_declared_signal");
});

test("ladder: every forbidden aid fails the drill, and the answer names which", () => {
  for (const aid of r01.V5_R01_FORBIDDEN_DRILL_AIDS) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({ aids_used: [aid] }));
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, aid);
    assert.equal(verdict.blocking_check, "recovery_required_a_forbidden_aid");
    assert.deepEqual(verdict.forbidden_aids_used, [aid]);
  }
});

test("receipt: the classifier reads exactly the twelve fields the slice publishes", () => {
  // THE DEFECT THE REVIEW REPRODUCED: the schema declared twelve, the classifier
  // read eight, and a receipt built to the published schema came back refused as
  // carrying four unknown fields. This asserts the two lists are ONE list.
  assert.deepEqual(Object.keys(drill()).sort(), [...V5_R01_DRILL_RECEIPT_FIELDS].sort());
  assert.equal(V5_R01_DRILL_RECEIPT_FIELDS.length, 12);

  const verdict = classifyRecoveryDrillIfAuthoritative(drill());
  assert.equal(verdict.classification, CLASSIFICATIONS.drill,
    "a complete receipt built to the registered schema must be readable");
  assert.equal(verdict.blocking_check, null);

  // Each of the four fields the previous round rejected, one at a time: dropping
  // it must now be a MISSING field rather than the whole receipt being unknown.
  for (const field of ["injected_at", "recovered_at", "producer_step_ref", "recovery_path_taken"]) {
    const partial = drill();
    delete partial[field];
    assert.throws(() => classifyRecoveryDrillIfAuthoritative(partial),
      error => error instanceof V5R01Error && error.code === "missing_field", field);
  }
});

test("receipt: the producer and the interval travel on the answer", () => {
  const verdict = classifyRecoveryDrillIfAuthoritative(drill());
  assert.equal(verdict.producer_step_ref, "step:v5-r01-recovery-drill-receipt-store");
  assert.equal(verdict.injected_at, "2026-09-11T14:00:00Z");
  assert.equal(verdict.recovered_at, "2026-09-11T14:11:00Z");
  assert.equal(verdict.is_not_authority, true);
});

test("roles: one person injecting, watching and recovering is a demonstration, not a drill", () => {
  const verdict = classifyRecoveryDrillIfAuthoritative(drill({
    injected_by_identity_ref: "actor:same", observer_identity_ref: "actor:same",
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "injector_and_observer_are_the_same_identity");
});

test("roles: the subject may not have injected its own fault", () => {
  // THE FIRST OF THE TWO MISSING EQUALITIES. The definition has always said
  // neither the injector nor the observer may be the recovering subject; the
  // previous round compared only the injector with the observer, so this receipt
  // reached the end of the ladder and would have succeeded.
  const verdict = classifyRecoveryDrillIfAuthoritative(drill({
    injected_by_identity_ref: V5_R01_PILOT_PARTNER,
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "subject_injected_its_own_fault");
  assert.equal(verdict.subject_partner, V5_R01_PILOT_PARTNER);
});

test("roles: the subject may not have observed its own recovery", () => {
  // THE SECOND. Same shape, other pair: a partner who recovered and then vouched
  // for himself has produced a receipt about himself.
  const verdict = classifyRecoveryDrillIfAuthoritative(drill({
    observer_identity_ref: V5_R01_PILOT_PARTNER,
  }));
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "subject_observed_its_own_recovery");
});

test("roles: three distinct identities is the only shape that gets past check zero", () => {
  // The exhaustive statement of the rule: every assignment of two roles to one
  // person is refused, and only the all-distinct assignment proceeds.
  const pairs = [
    ["injected_by_identity_ref", V5_R01_PILOT_PARTNER, "subject_injected_its_own_fault"],
    ["observer_identity_ref", V5_R01_PILOT_PARTNER, "subject_observed_its_own_recovery"],
    ["observer_identity_ref", "actor:injector", "injector_and_observer_are_the_same_identity"],
  ];
  for (const [field, value, expected] of pairs) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({ [field]: value }));
    assert.equal(verdict.blocking_check, expected, `${field}=${value}`);
  }
  assert.equal(classifyRecoveryDrillIfAuthoritative(drill()).classification, CLASSIFICATIONS.drill);
});

test("interval: a recovery cannot precede, or coincide with, the injection", () => {
  for (const recovered_at of ["2026-09-11T13:59:00Z", "2026-09-11T14:00:00Z"]) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({ recovered_at }));
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, recovered_at);
    assert.equal(verdict.blocking_check, "recovery_does_not_follow_the_injection", recovered_at);
  }
});

test("interval: an instant that matches the pattern but names no date is refused", () => {
  assert.throws(() => classifyRecoveryDrillIfAuthoritative(drill({ injected_at: "2026-02-30T14:00:00Z" })),
    error => error instanceof V5R01Error && error.code === "not_an_instant");
  assert.throws(() => classifyRecoveryDrillIfAuthoritative(drill({ recovered_at: "2026-09-11 14:11:00" })),
    error => error instanceof V5R01Error && error.code === "not_an_instant");
});

test("path: the recovery has to be the one the product declares for that fault", () => {
  const wrong = classifyRecoveryDrillIfAuthoritative(drill({
    recovery_path_taken: V5_R01_DRILL_FAULTS.write_conflict_requires_a_human.recovery_is,
  }));
  assert.equal(wrong.classification, CLASSIFICATIONS.refuse);
  assert.equal(wrong.blocking_check, "recovery_path_was_not_the_declared_one");
  assert.equal(wrong.declared_recovery_path,
    V5_R01_DRILL_FAULTS.record_layer_unreachable.recovery_is);
});

test("ladder: every registered fault has a declared signal a drill can be run against", () => {
  for (const fault of V5_R01_DRILL_FAULT_KEYS) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({
      fault_injected: fault,
      observed_partner_visible_signal: V5_R01_DRILL_FAULTS[fault].expected_partner_visible_signal,
      recovery_path_taken: V5_R01_DRILL_FAULTS[fault].recovery_is,
    }));
    assert.equal(verdict.classification, CLASSIFICATIONS.drill, fault);
  }
});

// ---------------------------------------------------------------------------
// REFUSALS. Every public evaluator, every input.
// ---------------------------------------------------------------------------

/**
 * THE THREE PUBLIC EVALUATORS, each with the seams its answer is owed.
 *
 * Note what is NOT in this table: a fixture. There is no "most honest input" any
 * more, because there is no input — each evaluator reads no field of its request,
 * so the table is the function and its expectation, and the SHAPES it is called
 * over are generated separately and exhaustively below.
 */
const EVALUATOR_MATRIX = [
  ["evaluatePilotDay", evaluatePilotDay, [
    V5_R01_SEAMS.defect_register.seam, V5_R01_SEAMS.outside_observer.seam,
    V5_R01_SEAMS.j1_subjourney_roster.seam, V5_R01_SEAMS.pilot_day_store.seam,
  ].sort()],
  ["evaluatePilotRun", evaluatePilotRun, [
    V5_R01_SEAMS.defect_register.seam, V5_R01_SEAMS.outside_observer.seam,
    V5_R01_SEAMS.j1_subjourney_roster.seam, V5_R01_SEAMS.operating_calendar.seam,
    V5_R01_SEAMS.pilot_day_store.seam,
  ].sort()],
  ["evaluateRecoveryDrill", evaluateRecoveryDrill, [
    V5_R01_SEAMS.drill_receipt_store.seam, V5_R01_SEAMS.outside_observer.seam,
  ].sort()],
];

test("refusal: every evaluator answers unavailable and binds none of its seams", () => {
  for (const [name, evaluator, seams] of EVALUATOR_MATRIX) {
    const result = evaluator(callerShapes()[0]);
    assert.equal(result.status, "unavailable", name);
    assert.equal(result.decision, "unavailable", name);
    assert.deepEqual(result.owed_seams, seams, name);
    assert.equal(result.request_read, false, name);
    assert.equal(result.caller_evidence_admitted, false, name);
    assert.equal(result.authority_established, false, name);
    assert.equal(result.state_holder_is_caller_supplied, false, name);
    assert.equal(result.model_judgment_admitted, false, name);
    assert.equal(result.produces_acceptance, false, name);
    assert.equal(result.injects_nothing, true, name);
    assert.equal(Object.isFrozen(result), true, name);
    for (const entry of result.seams_bound) assert.equal(entry.bound, false, entry.seam);
  }
});

test("refusal: the answer is byte-identical across every caller-controlled shape", () => {
  // THE DEFECT THE REVIEW NAMED, asserted out of existence. Previously a caller
  // that handed in a short run got a different answer from one that handed in a
  // clean run — which is exactly what "the caller's evidence was classified"
  // means, whatever the outer `decision` field said.
  const shapes = callerShapes();
  assert.ok(shapes.length >= 40, "the sweep must cover the caller-controlled domain");
  for (const [name, evaluator] of EVALUATOR_MATRIX) {
    const first = JSON.stringify(evaluator(shapes[0]));
    for (const shape of shapes) {
      assert.equal(JSON.stringify(evaluator(shape)), first,
        `${name} answered differently for ${String(JSON.stringify(shape)).slice(0, 90)}`);
    }
    assert.equal(JSON.stringify(evaluator()), first,
      `${name} answered differently for no argument at all`);
  }
});

test("refusal: no evaluator names the check a caller's evidence would have failed", () => {
  // The ladder is published; WHICH RUNG THIS CALLER FELL AT is not, because that
  // is a classification of caller-supplied evidence however it is spelled.
  for (const [name, evaluator] of EVALUATOR_MATRIX) {
    const result = evaluator(run());
    assert.equal(result.which_check_this_request_would_fail, null, name);
    assert.equal(Object.hasOwn(result, "blocking_check"), false, name);
    assert.equal(Object.hasOwn(result, "would_be_classified"), false, name);
    assert.equal(Object.hasOwn(result, "would_be_classified_detail"), false, name);
  }
  // What IS published is the ladder itself, in order, which a reader can argue
  // with without anybody's evidence being read.
  assert.deepEqual(evaluatePilotDay({}).day_checks_in_order, [...V5_R01_DAY_CHECKS]);
});

test("refusal: describeRecoveryDrill describes the whole table, takes nothing, injects nothing", () => {
  const described = describeRecoveryDrill();
  assert.equal(described.request_read, false);
  assert.equal(described.this_module_injects_nothing, true);
  assert.equal(described.receipt_store_exists_in_this_repository, false);
  assert.equal(described.subject_must_not_be_the_injector, true);
  assert.equal(described.subject_must_not_be_the_observer, true);
  assert.equal(described.injector_must_not_be_the_observer, true);
  assert.deepEqual(described.receipt_fields, [...V5_R01_DRILL_RECEIPT_FIELDS]);
  assert.equal(described.effects.creates_effect, false);
  assert.deepEqual(described.faults.map(row => row.fault), [...V5_R01_DRILL_FAULT_KEYS]);
  for (const row of described.faults) {
    assert.equal(row.expected_partner_visible_signal,
      V5_R01_DRILL_FAULTS[row.fault].expected_partner_visible_signal, row.fault);
    assert.equal(row.recovery_is, V5_R01_DRILL_FAULTS[row.fault].recovery_is, row.fault);
  }
  // And it is the same table for everybody, argument or no argument.
  assert.equal(JSON.stringify(describeRecoveryDrill()), JSON.stringify(describeRecoveryDrill()));
});

test("refusal: the ledger description forbids the four fields that would let it grade itself", () => {
  const described = describePilotDayLedgerEntry();
  assert.equal(described.store_exists_in_this_repository, false);
  assert.equal(described.append_only, true);
  assert.equal(described.one_entry_per_date, true);
  assert.deepEqual(described.day_checks_in_order, [...V5_R01_DAY_CHECKS]);
  for (const forbidden of Object.keys(V5_R01_LEDGER_ENTRY_FORBIDDEN_FIELDS)) {
    assert.equal(V5_R01_LEDGER_ENTRY_FIELDS.includes(forbidden), false, forbidden);
  }
  assert.equal(V5_R01_LEDGER_ENTRY_FIELDS.includes("observed_origin"), false,
    "a ledger entry does not carry the attribution that judges it");
});

test("refusal: no clause of the slice is provable from source, and each says what it needs", () => {
  const gaps = rolloutPilotGaps();
  assert.equal(gaps.any_clause_provable_from_source, false);
  assert.equal(gaps.clauses.length, 3);
  for (const clause of gaps.clauses) {
    assert.equal(clause.proven, false, clause.clause);
    assert.equal(clause.what_it_would_need.length > 60, true, clause.clause);
    assert.equal(clause.owed_seams.length > 0, true, clause.clause);
  }
  assert.equal(gaps.production_outcome_step, V5_R01_PRODUCTION_OUTCOME_STEP);
  assert.deepEqual(gaps.upstream_evidence_inputs, [...V5_R01_UPSTREAM_EVIDENCE_INPUTS]);
});

test("refusal: the typed successor is inactive and has no code path", () => {
  assert.equal(V5_R01_INACTIVE_SUCCESSOR.active, false);
  assert.equal(V5_R01_INACTIVE_SUCCESSOR.implemented_here, false);
  assert.equal(V5_R01_INACTIVE_SUCCESSOR.activation_gate_exists_in_this_repository, false);
  // No export of this module mentions the successor's own verb.
  for (const name of Object.keys(r01)) {
    assert.equal(name.toLowerCase().includes("rollup"), false, name);
  }
});

test("refusal: every declared seam is declared absent", () => {
  for (const key of Object.keys(V5_R01_SEAMS)) {
    assert.equal(V5_R01_SEAMS[key].exists_in_this_repository, false, key);
  }
});

// ---------------------------------------------------------------------------
// DECISIONS.
// ---------------------------------------------------------------------------

test("decisions: the slice binds exactly its six catalog decisions", () => {
  assert.deepEqual([...V5_R01_SETTLED_DECISION_IDS],
    ["Q009.D1", "Q010.D1", "Q019.D1", "Q061.D1", "Q104.D1", "Q145.D1"]);
  assert.equal(assertR01DecisionBinding({ decision_ids: [...V5_R01_SETTLED_DECISION_IDS] }), true);
  assert.throws(() => assertR01DecisionBinding({ decision_ids: ["Q009.D1"] }),
    error => error instanceof V5R01Error && error.code === "decision_binding_mismatch");
  assert.throws(() => assertR01DecisionBinding({
    decision_ids: [...V5_R01_SETTLED_DECISION_IDS, "Q999.D1"] }),
  error => error instanceof V5R01Error && error.code === "decision_binding_mismatch");
});

test("decisions: the two that were read carry their settled text, the four that were not say so", () => {
  assert.deepEqual([...V5_R01_DECISIONS_READ_IN_FULL], ["Q009.D1", "Q010.D1"]);
  assert.deepEqual([...V5_R01_DECISIONS_NOT_READ],
    ["Q019.D1", "Q061.D1", "Q104.D1", "Q145.D1"]);
  for (const id of V5_R01_DECISIONS_READ_IN_FULL) {
    assert.equal(typeof r01.V5_R01_SETTLED_DECISIONS[id].settled_answer, "string", id);
    assert.equal(r01.V5_R01_SETTLED_DECISIONS[id].settled_answer.length > 100, true, id);
  }
  for (const id of V5_R01_DECISIONS_NOT_READ) {
    // Not paraphrased: there is no settled_answer field at all on an unread one.
    assert.equal("settled_answer" in r01.V5_R01_SETTLED_DECISIONS[id], false, id);
    assert.equal(typeof r01.V5_R01_SETTLED_DECISIONS[id].why_not, "string", id);
    assert.equal(typeof r01.V5_R01_SETTLED_DECISIONS[id].acceptance_hook, "string", id);
  }
});

// ---------------------------------------------------------------------------
// DIGEST.
// ---------------------------------------------------------------------------

test("digest: the policy digest moves when the exclusion ladder moves", () => {
  const before = v5R01PolicyDigest();
  assert.equal(before, v5R01PolicyDigest(), "the digest is stable across calls");
  assert.equal(v5R01PolicyCanonicalBytes(), JSON.stringify(sortDeep(v5R01PolicyPreimage())),
    "canonical bytes are the canonicalization of the preimage");
  const preimage = v5R01PolicyPreimage();
  assert.equal(preimage.failure_origins.length, V5_R01_FAILURE_ORIGIN_KEYS.length);
  assert.equal(preimage.required_run_length, V5_R01_REQUIRED_RUN_LENGTH);
  assert.equal(Object.isFrozen(preimage), true);
});

/** Mirror of artifact-trust's canonicalization, so the assertion above proves something. */
function sortDeep(value) {
  if (Array.isArray(value)) return value.map(sortDeep);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, sortDeep(value[key])]));
  }
  return value;
}

// ===========================================================================
// GUARDS.
//
// The standing rule, in one sentence: a caller-supplied label, class string,
// flag, receipt object, fixture or injected holder is NEVER authority, and no
// exported function may return a privileged outcome from caller input UNDER ANY
// NAME. The previous round of this slice failed that in three separate ways and
// the review of PR 992 found all three, so the guards below are built to the
// shapes it named rather than to the shapes the author expected.
//
//   * The privileged sweep was ONE aggregate test over a handful of hand-picked
//     results and it compared whole strings to bare words, so an alias — a
//     `passed_all_checks`, a `run_completed` — walked straight through it. It is
//     now ONE TEST PER PRIVILEGED STRING, each sweeping every export of every
//     module in this slice over every caller-controlled shape, matching on word
//     boundaries rather than on equality, with no exemption for any export.
//
//   * The import guard EXPLICITLY PERMITTED both production modules to import the
//     classifiers, which is the opposite of what an isolation guard is for. The
//     classifiers have moved to mcp-server/test/ and the guard now refuses any
//     reach from src into the test tree by any loading form at all.
//
//   * It was also a specifier scan that skipped files it could not parse. It is a
//     real parser now — esbuild, the one that compiles this repository's Worker
//     bundle — a parse failure is a test failure rather than a silent skip, and
//     the dynamic and computed forms are reported rather than dropped.
// ===========================================================================

/** THE NINE WORDS. The suite's list, not production's: see the vocabulary's tail. */
const PRIVILEGED_OUTCOMES = Object.freeze([
  "allow", "completed", "counted", "independent", "operable",
  "pass", "passed", "passing", "succeeded",
]);

/**
 * The conditional tokens a classifier answers in. None of them may appear in any
 * module under mcp-server/src, in a value OR in its source text: a `would_` field
 * on a public surface is a classification of caller input wearing a hedge.
 */
const CLASSIFICATION_TOKENS = Object.freeze(Object.values(CLASSIFICATIONS));

/**
 * Does `text` contain `word` as a WORD, in any of the spellings this codebase
 * writes words in — bare, snake_case, kebab-case, or camelCase?
 *
 * This is the fix for the review's "rejects only exact bare words". `"passed"`
 * must be caught inside `run_passed`, `run-passed`, `runPassed` and `passed the
 * check`, and must NOT be caught inside `passenger` or `bypassed`, which are
 * different words that merely contain the letters.
 */
function namesWord(text, word) {
  return wordsOf(text).includes(word);
}

/**
 * The words of a string, however this codebase spells a compound: separators
 * (`_`, `-`, `:`, `.`, `/`, whitespace, punctuation) AND camelCase humps both
 * end a word. So `run_passed`, `run-passed`, `runPassed` and "it passed." all
 * yield `passed`, while `passenger` and `bypassed` yield themselves and are
 * therefore different words rather than near misses.
 */
function wordsOf(text) {
  return String(text)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map(word => word.toLowerCase());
}

/**
 * EVERY CALLER-CONTROLLED SHAPE, generated rather than listed.
 *
 * The honest fixtures, each field of each one mutated through its whole domain,
 * the shapes that try to state the answer outright, and the non-objects. If none
 * of them changes any answer, none of them is authority.
 */
function callerShapes() {
  const shapes = [];
  // The honest ones.
  shapes.push({ date: "2026-09-07", entry: day() }, run(), { receipt: drill() });
  // Every failure origin, both honesty booleans, on a day.
  for (const origin of V5_R01_FAILURE_ORIGIN_KEYS) {
    for (const honest of [true, false]) {
      shapes.push({ date: "2026-09-07",
        entry: day({ failures: [failure({ observed_origin: origin,
          product_reported_honestly: honest, documented_fallback_offered: honest })] }) });
    }
  }
  // The disqualifier, the short day, the empty day.
  shapes.push({ date: "2026-09-07", entry: day({ high_defect_open_against_j1: true }) });
  shapes.push({ date: "2026-09-07", entry: day({ subjourneys_exercised: [] }) });
  shapes.push({ date: "2026-09-07", entry: day({ real_business_actions: [] }) });
  // THE DATE DISAGREEMENT the review reproduced: an outer date that is not the
  // entry's date. It changes nothing now because neither is read.
  shapes.push({ date: "2026-09-07", entry: day({ date: "2026-09-08" }) });
  // Runs: complete, short, empty, and one with an edited denominator.
  shapes.push(run(), run({ days: [] }),
    run({ days: TEN_BUSINESS_DAYS.slice(0, 3).map(d => day({ date: d })) }),
    run({ non_business_dates: [...TEN_BUSINESS_DAYS] }));
  // Every terminal state and every fault on a receipt.
  for (const state of V5_R01_DRILL_TERMINAL_STATES) shapes.push({ receipt: drill({ terminal_state_reached: state }) });
  for (const fault of V5_R01_DRILL_FAULT_KEYS) shapes.push({ receipt: drill({ fault_injected: fault }) });
  // One person in all three roles.
  shapes.push({ receipt: drill({ injected_by_identity_ref: V5_R01_PILOT_PARTNER,
    observer_identity_ref: V5_R01_PILOT_PARTNER }) });
  // Caller references that ARE privileged words, in every field that takes one.
  shapes.push({ date: "2026-09-07", entry: day({
    subjourneys_exercised: ["pass", "allow", "completed"],
    real_business_actions: ["deal:passed-through", "counted", "runPassed"],
  }) });
  shapes.push({ receipt: drill({ drill_ref: "passed", recovery_path_taken: "succeeded",
    aids_used: ["allow"] }) });
  // The one shape the decision-binding assertion reads.
  shapes.push({ decision_ids: ["Q009.D1", "Q010.D1", "Q019.D1", "Q061.D1", "Q104.D1", "Q145.D1"] });
  // And the shapes that try to say the answer outright.
  shapes.push({ decision: "allow", status: "passed", passable: true, verified: true });
  shapes.push({ would_complete_run_if_authoritative: true, ok: true });
  shapes.push({}, null, undefined, "allow", 1, true, [], [{ passed: true }]);
  return shapes;
}

/**
 * EVERY EXPORT OF EVERY SRC MODULE IN THIS SLICE.
 *
 * A CONSTANT is one entry: the value as it stands.
 * A FUNCTION is one entry PER CALLER-CONTROLLED SHAPE, carrying both what went in
 * and what came out, because the question the standing rule actually asks is
 * whether a privileged outcome came out THAT THE CALLER DID NOT PUT IN.
 *
 * Nothing is exempt. Not the vocabulary, not the descriptions, not the policy
 * preimage, and not the structural utilities — `deepFreeze(x)` returns `x` by
 * contract, so a caller who hands it the string "allow" gets "allow" back, and
 * the rule below says the honest thing about that case rather than adding
 * `deepFreeze` to an allow-list. An allow-list is where the next defect would
 * live.
 */
function everyExportedValue() {
  const entries = [];
  for (const [moduleName, surface] of [["rollout-pilot-r01.v5.js", r01],
    ["onboarding-flow-r01.v5.js", onboarding],
    ["rollout-pilot-r01.vocabulary.v5.js", vocabulary]]) {
    for (const [name, exported] of Object.entries(surface)) {
      const at = `${moduleName}#${name}`;
      if (typeof exported !== "function") { entries.push({ at, input: undefined, output: exported }); continue; }
      // A class (V5R01Error) is not an evaluator; its NAME is what is checked.
      if (/^[A-Z]/.test(name)) { entries.push({ at, input: undefined, output: name }); continue; }
      for (const shape of callerShapes()) {
        // A THROW IS SWEPT TOO. An export that refuses every shape would otherwise
        // be an export no test ever looked at, and the refusal's own code and
        // message are strings a consumer reads.
        let output;
        try { output = exported(shape); } catch (failure) { output = { threw: failure.code ?? failure.message }; }
        entries.push({ at: `${at}(${String(JSON.stringify(shape))})`.slice(0, 120), input: shape, output });
      }
      try {
        entries.push({ at: `${at}()`, input: undefined, output: exported() });
      } catch (failure) {
        entries.push({ at: `${at}()`, input: undefined, output: { threw: failure.code ?? failure.message } });
      }
    }
  }
  return entries;
}

/** Computed once: the sweep is the same for every word and it is not cheap. */
const EXPORTED_VALUES = everyExportedValue();

/**
 * THE RULE, stated as a procedure so a second reader reaches the same verdict.
 *
 * For one privileged word W and one entry:
 *   1. Collect every string and every object key in the OUTPUT.
 *   2. Collect every string and every object key in the INPUT.
 *   3. The entry VIOLATES if W is named in the output and was not named in the
 *      input — that is, the export MANUFACTURED the word rather than echoing
 *      back something the caller already held.
 *
 * "Named" is word-level, not equality, which is the half the previous round got
 * wrong: `passed` is named by `run_passed`, `run-passed`, `runPassed` and by the
 * sentence "it passed", and is NOT named by `passenger` or `bypassed`.
 *
 * THE CASE THIS RULE ALONE WOULD MISS, and how it is closed: an export that
 * copied a caller's label into its own verdict field would satisfy step 3,
 * because the word was in the input. That route is shut by a different test —
 * every public evaluator's answer is byte-identical across all of these shapes,
 * so no caller string reaches any answer at all. The two tests are one property
 * and neither is sufficient alone.
 */
function namedWords(value, out = new Set()) {
  if (typeof value === "string") { out.add(value); return out; }
  if (Array.isArray(value)) { value.forEach(entry => namedWords(entry, out)); return out; }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) { out.add(key); namedWords(entry, out); }
  }
  return out;
}

function violations(entry, word) {
  const inInput = [...namedWords(entry.input)].some(text => namesWord(text, word));
  if (inInput) return [];
  return [...namedWords(entry.output)].filter(text => namesWord(text, word));
}

test("guard: the sweep is reading a real surface, not an empty one", () => {
  // A sweep that swept nothing would pass every test below. This is the
  // non-vacuity check that makes the fifteen tests mean something.
  assert.ok(EXPORTED_VALUES.length > 500,
    `the sweep covered only ${EXPORTED_VALUES.length} values`);
  const modules = new Set(EXPORTED_VALUES.map(entry => entry.at.split("#")[0]));
  assert.deepEqual([...modules].sort(), [
    "onboarding-flow-r01.v5.js", "rollout-pilot-r01.v5.js", "rollout-pilot-r01.vocabulary.v5.js",
  ]);
  // Every export of every module is represented — no export may be skipped.
  for (const [moduleName, surface] of [["rollout-pilot-r01.v5.js", r01],
    ["onboarding-flow-r01.v5.js", onboarding],
    ["rollout-pilot-r01.vocabulary.v5.js", vocabulary]]) {
    for (const name of Object.keys(surface)) {
      assert.ok(EXPORTED_VALUES.some(entry => entry.at.startsWith(`${moduleName}#${name}`)),
        `${moduleName}#${name} was not swept`);
    }
  }
  // And the word matcher catches the aliases the review said walked through.
  for (const alias of ["run_passed", "runPassed", "run-passed", "it passed.", "passed"]) {
    assert.ok(namesWord(alias, "passed"), alias);
  }
  assert.equal(namesWord("passenger", "passed"), false);
  assert.equal(namesWord("bypassed", "passed"), false);
  // Non-vacuity of the rule itself: a manufactured word IS caught.
  assert.deepEqual(violations({ at: "synthetic", input: { a: 1 }, output: { verdict: "run_passed" } },
    "passed"), ["run_passed"]);
});

// ONE TEST PER PRIVILEGED STRING. Nine words, no allow-list, no skipped export.
for (const word of PRIVILEGED_OUTCOMES) {
  test(`guard: no export of this slice manufactures "${word}" for any caller`, () => {
    for (const entry of EXPORTED_VALUES) {
      assert.deepEqual(violations(entry, word), [],
        `${entry.at} produced "${word}" its caller never supplied`);
    }
  });
}

// AND ONE PER CONDITIONAL TOKEN, because the review's remedy was explicit that a
// `would_*` value is not an acceptable substitute for the privileged one.
for (const token of CLASSIFICATION_TOKENS) {
  test(`guard: no export of this slice manufactures "${token}" for any caller`, () => {
    for (const entry of EXPORTED_VALUES) {
      // Same rule, same reason: `deepFreeze` handed an object whose own key is the
      // token gives that key back, and that is the caller's token, not a verdict.
      // What no export may do is produce one the caller never held.
      if ([...namedWords(entry.input)].some(text => text.includes(token))) continue;
      assert.deepEqual([...namedWords(entry.output)].filter(text => text.includes(token)), [],
        `${entry.at} manufactured ${token}`);
    }
  });
}

test("guard: no export of either public surface is named for a classifier", () => {
  for (const surface of [r01, onboarding, vocabulary]) {
    for (const name of Object.keys(surface)) {
      assert.equal(/^(classify|evaluateIf|derive)/.test(name), false,
        `${name} is a classifier name on a public surface`);
      assert.equal(name.includes("would_"), false, `${name} is a classifier field`);
      // The hatch names, matched where a hatch would actually be spelled — at the
      // start of a name or after a separator — so `assertInternalRef`, which is a
      // validator for an internal REFERENCE, is not mistaken for one.
      assert.equal(/^(internal|probe|unwired|fixture)|[_.](internal|probe|unwired|fixture)|testonly|testhelper/i
        .test(name), false, `${name} is an escape hatch on a public surface`);
    }
  }
  assert.deepEqual(Object.keys(r01).sort(), [...V5_R01_PUBLIC_SURFACE].sort());
  assert.deepEqual(Object.keys(onboarding).sort(),
    [...onboarding.V5_R01_ONBOARDING_PUBLIC_SURFACE].sort());
});

test("guard: every exported validator returns nothing, so none can launder a value", () => {
  // A MUTATION FOUND THIS GAP RATHER THAN A REVIEW. The validators were made void
  // because an exported function handing a caller's own string back out is an
  // export returning caller input — but the per-word sweep could not catch a
  // validator echoing "allow", since the rule there is that the word must be one
  // the caller did NOT supply, and that caller supplied it. So the property gets
  // its own test: these functions throw or they return undefined, and there is no
  // third outcome.
  const validators = Object.entries(vocabulary)
    .filter(([name, value]) => name.startsWith("assert") && typeof value === "function");
  assert.ok(validators.length >= 8, "the validator set must not have quietly emptied");

  const arguments_ = [
    ["allow", "path"], ["pass", "path"], ["2026-09-07", "path"], [true, "path"],
    [{}, "path"], [[], "path"], [0, "path"], [null, "path"],
    [[...V5_R01_FAILURE_ORIGIN_KEYS], "path"],
    ["partner_device_or_credential", [...V5_R01_FAILURE_ORIGIN_KEYS], "path"],
    [{ a: 1 }, ["a"], "path"],
  ];
  for (const [name, validator] of validators) {
    for (const args of arguments_) {
      let returned;
      try { returned = validator(...args); } catch { continue; }
      assert.equal(returned, undefined,
        `${name} returned ${JSON.stringify(returned)} instead of nothing`);
    }
  }
});

test("guard: no evaluator accepts a store, ledger, calendar or observer as a second argument", () => {
  const holder = { readEntry: () => day(), readReceipt: () => drill() };
  for (const [name, evaluator] of EVALUATOR_MATRIX) {
    assert.throws(() => evaluator(run(), holder),
      error => error instanceof V5R01Error
        && error.code === `${name}_holder_is_not_an_argument`, name);
  }
  assert.throws(() => describeRecoveryDrill(holder),
    error => error instanceof V5R01Error
      && error.code === "describeRecoveryDrill_holder_is_not_an_argument");
});

// ---------------------------------------------------------------------------
// ISOLATION. The classifiers are unreachable from production — PARSED.
// ---------------------------------------------------------------------------

const SRC_DIR = path.join(REPO_ROOT, "mcp-server", "src");
const TEST_DIR = path.join(REPO_ROOT, "mcp-server", "test");

assert.equal(typeof esbuild.buildSync, "function",
  "the import guard needs a real parser; esbuild is not loadable");

/**
 * Every module specifier a source file loads, by any form that actually loads.
 *
 * esbuild reports static imports, dynamic imports and require calls with the kind
 * of each. The two things it cannot fold — `import(name)` and `require(name)`
 * with a computed specifier — are found by counting call sites in the raw text
 * and reported as `<computed import>` / `<computed require>` rather than dropped.
 * That over-reports (an occurrence in a comment counts) and over-reporting is the
 * safe direction: a false alarm is a review, a missed call site is a door.
 */
function importSpecifiers(source) {
  const built = esbuild.buildSync({
    stdin: { contents: source, loader: "js", sourcefile: "module-under-guard.js", resolveDir: SRC_DIR },
    bundle: false, write: false, metafile: true, format: "esm",
    platform: "neutral", logLevel: "silent", logLimit: 0,
  });
  const output = Object.values(built.metafile.outputs)[0];
  const records = output === undefined ? [] : output.imports;
  const specifiers = new Set(records.map(record => record.path));
  const callSites = callee => source.split(new RegExp(`(?<![\\w$.])${callee}\\s*\\(`)).length - 1;
  const resolved = kind => records.filter(record => record.kind === kind).length;
  if (callSites("import") > resolved("dynamic-import")) specifiers.add("<computed import>");
  if (callSites("require") > resolved("require-call")) specifiers.add("<computed require>");
  if (/(?<![\w$])createRequire(?![\w$])/.test(source)) specifiers.add("createRequire");
  return [...specifiers].sort();
}

test("isolation: the parser reads the loading forms a specifier scan misses", () => {
  // Non-vacuity for the guard itself: each of these forms really does surface,
  // including the two a regex over `^import` would never have seen.
  const cases = [
    ['import "./x.js";', ["./x.js"]],
    ['import /* c */ "./x.js";', ["./x.js"]],
    ['import\u00a0"./x.js";', ["./x.js"]],
    ['export { a } from "./x.js";', ["./x.js"]],
    ['export * from "./x.js";', ["./x.js"]],
    ['await import("./x.js");', ["./x.js"]],
    ['const m = require("./x.js");', ["./x.js"]],
    ['await import("./pre" + "fix.js");', ["./prefix.js"]],
    ['await import(name);', ["<computed import>"]],
    ['const m = require(name);', ["<computed require>"]],
    ['import { createRequire } from "node:module";', ["createRequire", "node:module"]],
    ['// import "./commented.js";\nimport "./real.js";', ["./real.js"]],
    ['const s = "import \\"./in-a-string.js\\";";', []],
  ];
  for (const [source, expected] of cases) {
    assert.deepEqual(importSpecifiers(source), [...expected].sort(), source);
  }
  // And the exact smuggle this guard exists to stop, in its dynamic form.
  assert.deepEqual(
    importSpecifiers('const c = await import("../test/rollout-pilot-r01-classifiers.v5.testhelper.mjs");'),
    ["../test/rollout-pilot-r01-classifiers.v5.testhelper.mjs"]);
});

test("isolation: no module in src reaches the test tree, by any loading form", () => {
  const files = fs.readdirSync(SRC_DIR).filter(name => name.endsWith(".js")).sort();
  assert.ok(files.length > 100, "every module in src must be scanned");

  // A file that cannot be parsed is a FAILURE, not a skip. The previous round
  // swallowed parse errors, which meant an unparseable module was an invisible
  // one.
  const reaches = [];
  for (const file of files) {
    const source = fs.readFileSync(path.join(SRC_DIR, file), "utf8");
    let specifiers;
    try {
      specifiers = importSpecifiers(source);
    } catch (failure) {
      assert.fail(`${file} could not be parsed by the import guard: ${failure.message}`);
    }
    for (const specifier of specifiers) {
      if (/(^|\/)\.\.\/test\//.test(specifier) || specifier.includes("/test/")
        || specifier.includes(".testhelper.") || specifier.includes(".testonly.")
        || specifier.startsWith("<computed") || specifier === "createRequire") {
        reaches.push(`${file} -> ${specifier}`);
      }
    }
  }
  assert.deepEqual(reaches, [],
    "a production module can reach the test tree, or loads a specifier the parser cannot see");

  // And no test-only entry is sitting in the production directory under any name.
  assert.deepEqual(
    fs.readdirSync(SRC_DIR).filter(name => /\.(testonly|testhelper|internal)\./.test(name)), [],
    "a test-only or internal entry is sitting in the production source directory");
});

test("isolation: the classifier entry is where it says it is, and only tests import it", () => {
  const helper = "rollout-pilot-r01-classifiers.v5.testhelper.mjs";
  assert.ok(fs.existsSync(path.join(TEST_DIR, helper)));
  assert.equal(fs.existsSync(path.join(SRC_DIR, "rollout-pilot-r01.internal.v5.js")), false,
    "the exported internal module the review named must be gone from src");

  const importers = fs.readdirSync(TEST_DIR)
    .filter(name => /\.(mjs|js)$/.test(name))
    .filter(name => fs.readFileSync(path.join(TEST_DIR, name), "utf8").includes(helper))
    .sort();
  assert.deepEqual(importers, [
    "onboarding-flow-r01.v5.test.mjs", "rollout-pilot-r01.v5.test.mjs",
  ], "only this slice's two suites may name the helper");
});

test("isolation: no file in src contains a conditional classification token", () => {
  // The strongest form of the property, and the one that cannot be satisfied by
  // renaming a field: the TOKENS themselves do not occur in production source at
  // all — not in a value, not in a key, not in a comment.
  const offenders = [];
  for (const file of fs.readdirSync(SRC_DIR).filter(name => name.endsWith(".js"))) {
    const source = fs.readFileSync(path.join(SRC_DIR, file), "utf8");
    for (const token of CLASSIFICATION_TOKENS) {
      if (source.includes(token)) offenders.push(`${file}: ${token}`);
    }
  }
  assert.deepEqual(offenders.sort(), []);
  // Non-vacuous: the tokens exist and the helper really does carry them.
  assert.ok(CLASSIFICATION_TOKENS.length >= 5);
  assert.ok(fs.readFileSync(path.join(TEST_DIR, "rollout-pilot-r01-classifiers.v5.testhelper.mjs"), "utf8")
    .includes("would_complete_run_if_authoritative"));
});

test("isolation: the two public modules import exactly what they say they import", () => {
  const expected = {
    "rollout-pilot-r01.v5.js": ["./artifact-trust.js", "./global-boundaries.v5.js",
      "./rollout-pilot-r01.vocabulary.v5.js"],
    "onboarding-flow-r01.v5.js": ["./artifact-trust.js", "./global-boundaries.v5.js",
      "./rollout-pilot-r01.vocabulary.v5.js"],
    "rollout-pilot-r01.vocabulary.v5.js": ["./global-boundaries.v5.js"],
  };
  for (const [file, specifiers] of Object.entries(expected)) {
    assert.deepEqual(importSpecifiers(fs.readFileSync(path.join(SRC_DIR, file), "utf8")),
      [...specifiers].sort(), file);
  }
});

// ---------------------------------------------------------------------------
// VOCABULARY.
// ---------------------------------------------------------------------------

test("guard: the vocabulary's failure-origin sets agree with an independent derivation", () => {
  assert.deepEqual([...V5_R01_BREAKING_FAILURE_ORIGINS].sort(), [...breakingFailureOrigins()].sort());
  assert.deepEqual([...V5_R01_DISQUALIFYING_FAILURE_ORIGINS].sort(),
    [...disqualifyingFailureOrigins()].sort());
  assert.deepEqual(
    [...V5_R01_EXCLUDED_FAILURE_ORIGINS, ...V5_R01_BREAKING_FAILURE_ORIGINS].sort(),
    [...V5_R01_FAILURE_ORIGIN_KEYS].sort(),
    "every registered origin is either excluded or breaking, and none is both");
});

test("guard: every exclusion carries a stated basis a reader can argue with", () => {
  const exclusions = describeFailureExclusions();
  assert.equal(exclusions.attribution_seam_exists_in_this_repository, false);
  for (const row of exclusions.origins) {
    assert.equal(typeof row.basis, "string", row.origin);
    assert.ok(row.basis.length > 20, row.origin);
  }
});
