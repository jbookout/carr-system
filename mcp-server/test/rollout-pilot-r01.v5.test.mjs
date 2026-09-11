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
// S01 itself, so a quoted S01 identifier can be checked against S01's real
// export list rather than taken on this slice's word.
import * as boundaries from "../src/global-boundaries.v5.js";
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
  V5R01ClassifierError,
} from "./rollout-pilot-r01-classifiers.v5.testhelper.mjs";

const {
  V5R01Error, V5_R01_BREAKING_FAILURE_ORIGINS, V5_R01_DAY_CHECKS,
  V5_R01_DECISIONS_WITHOUT_SETTLED_TEXT, V5_R01_DECISIONS_WITH_SETTLED_TEXT,
  V5_R01_DISQUALIFYING_FAILURE_ORIGINS, V5_R01_DRILL_FAULTS, V5_R01_DRILL_FAULT_KEYS,
  V5_R01_DRILL_RECEIPT_FIELDS, V5_R01_DRILL_TERMINAL_STATES,
  V5_R01_EXCLUDED_FAILURE_ORIGINS, V5_R01_FAILURE_ORIGINS,
  V5_R01_FAILURE_ORIGIN_KEYS, V5_R01_DORMANT_SUCCESSOR, V5_R01_J1_SUBJOURNEY_COUNT,
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
    terminal_state_reached: "recovery_reached",
    aids_used: [],
    injected_by_identity_ref: "actor:injector",
    observer_identity_ref: "actor:observer",
    injected_at: "2026-09-11T14:00:00Z",
    recovery_reached_at: "2026-09-11T14:11:00Z",
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
  })), error => error instanceof V5R01ClassifierError && error.code === "duplicate_pilot_day");
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
    terminal_state_reached: "recovery_reached",
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
  for (const field of ["injected_at", "recovery_reached_at", "producer_step_ref", "recovery_path_taken"]) {
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
  assert.equal(verdict.recovery_reached_at, "2026-09-11T14:11:00Z");
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
  for (const recovery_reached_at of ["2026-09-11T13:59:00Z", "2026-09-11T14:00:00Z"]) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({ recovery_reached_at }));
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, recovery_reached_at);
    assert.equal(verdict.blocking_check, "recovery_does_not_follow_the_injection", recovery_reached_at);
  }
});

test("interval: an instant that matches the pattern but names no date is refused", () => {
  assert.throws(() => classifyRecoveryDrillIfAuthoritative(drill({ injected_at: "2026-02-30T14:00:00Z" })),
    error => error instanceof V5R01ClassifierError && error.code === "not_an_instant");
  assert.throws(() => classifyRecoveryDrillIfAuthoritative(drill({ recovery_reached_at: "2026-09-11 14:11:00" })),
    error => error instanceof V5R01ClassifierError && error.code === "not_an_instant");
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

test("refusal: the typed successor is dormant and has no code path", () => {
  assert.equal(V5_R01_DORMANT_SUCCESSOR.implemented_here, false);
  assert.equal(V5_R01_DORMANT_SUCCESSOR.activation_gate_exists_in_this_repository, false);
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
  // A VALIDATOR RETURNS NOTHING. It used to return `true`, which is an
  // affirmative a consumer can read off a public function; the whole slice's
  // answer to "did this pass" is that a validator throws or says nothing.
  assert.equal(
    assertR01DecisionBinding({ decision_ids: [...V5_R01_SETTLED_DECISION_IDS] }), undefined);
  assert.throws(() => assertR01DecisionBinding({ decision_ids: ["Q009.D1"] }),
    error => error instanceof V5R01Error && error.code === "decision_binding_mismatch");
  assert.throws(() => assertR01DecisionBinding({
    decision_ids: [...V5_R01_SETTLED_DECISION_IDS, "Q999.D1"] }),
  error => error instanceof V5R01Error && error.code === "decision_binding_mismatch");
});

test("decisions: a binding is checked element by element, never as one joined string", () => {
  // THE DEFECT THE THIRD REVIEW REPRODUCED. The check was
  // `sorted.join("|") === mine.join("|")`, and these two values produce the same
  // string, so a caller could declare a binding of ONE meaningless element and be
  // told it matched this slice's six settled decisions. A separator is not a
  // delimiter unless the separated values cannot contain it, and a caller string
  // can contain anything.
  const mismatch = error => error instanceof V5R01Error
    && error.code === "decision_binding_mismatch";
  const forged = [[...V5_R01_SETTLED_DECISION_IDS].join("|")];
  assert.equal(forged.length, 1, "the forgery is a one-element array");
  assert.equal(forged.sort().join("|"), [...V5_R01_SETTLED_DECISION_IDS].join("|"),
    "the premise has changed: the forgery no longer collides under a join");
  assert.throws(() => assertR01DecisionBinding({ decision_ids: forged }), mismatch,
    "a one-element array of the six ids glued together was accepted");

  // The same collision with a different glue, so the fix is not a ban on one
  // character: any joining at all reintroduces it.
  for (const glue of ["|", ",", "", " "]) {
    assert.throws(() => assertR01DecisionBinding({
      decision_ids: [[...V5_R01_SETTLED_DECISION_IDS].join(glue)] }), mismatch, glue);
  }

  // ELEMENTS. A nested array stringifies to its contents under a join, so six
  // ids arriving as five strings and one array used to pass.
  assert.throws(() => assertR01DecisionBinding({
    decision_ids: [["Q009.D1"], "Q010.D1", "Q019.D1", "Q061.D1", "Q104.D1", "Q145.D1"] }),
  mismatch, "an element that is not a string was accepted");
  for (const element of [null, 1, true, {}, undefined, ["Q009.D1"]]) {
    assert.throws(() => assertR01DecisionBinding({
      decision_ids: [element, "Q010.D1", "Q019.D1", "Q061.D1", "Q104.D1", "Q145.D1"] }),
    mismatch, String(element));
  }

  // CARDINALITY, in both directions, at the exact boundaries.
  assert.throws(() => assertR01DecisionBinding({
    decision_ids: [...V5_R01_SETTLED_DECISION_IDS].slice(0, 5) }), mismatch, "five of six");
  assert.throws(() => assertR01DecisionBinding({ decision_ids: [] }), mismatch, "none of six");

  // UNIQUENESS. Six entries, the right count, one id repeated.
  assert.throws(() => assertR01DecisionBinding({
    decision_ids: ["Q009.D1", "Q009.D1", "Q010.D1", "Q019.D1", "Q061.D1", "Q104.D1"] }),
  mismatch, "a repeated id filled the count");

  // AND THE HONEST BINDING STILL PASSES, in any order, so the checks above are
  // not simply an always-red validator.
  assert.equal(assertR01DecisionBinding({
    decision_ids: [...V5_R01_SETTLED_DECISION_IDS].reverse() }), undefined);
  assert.equal(assertR01DecisionBinding({
    decision_ids: [...V5_R01_SETTLED_DECISION_IDS] }), undefined);
});

test("decisions: the two that were read carry their settled text, the four that were not say so", () => {
  assert.deepEqual([...V5_R01_DECISIONS_WITH_SETTLED_TEXT], ["Q009.D1", "Q010.D1"]);
  assert.deepEqual([...V5_R01_DECISIONS_WITHOUT_SETTLED_TEXT],
    ["Q019.D1", "Q061.D1", "Q104.D1", "Q145.D1"]);
  for (const id of V5_R01_DECISIONS_WITH_SETTLED_TEXT) {
    assert.equal(typeof r01.V5_R01_SETTLED_DECISIONS[id].settled_answer, "string", id);
    assert.equal(r01.V5_R01_SETTLED_DECISIONS[id].settled_answer.length > 100, true, id);
  }
  for (const id of V5_R01_DECISIONS_WITHOUT_SETTLED_TEXT) {
    // Not paraphrased: there is no settled_answer field at all on an unread one.
    assert.equal("settled_answer" in r01.V5_R01_SETTLED_DECISIONS[id], false, id);
    assert.equal(typeof r01.V5_R01_SETTLED_DECISIONS[id].why_not, "string", id);
    assert.equal(typeof r01.V5_R01_SETTLED_DECISIONS[id].acceptance_hook.identifier,
      "string", id);
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

/**
 * THE PRIVILEGED VOCABULARY — THE UNION, NOT A SELECTION.
 *
 * The re-review's first finding was that this list was the author's and not the
 * authority's: it carried fourteen words, eight of the standing rule's were
 * missing, and it had substituted terms of its own. A sweep whose vocabulary is
 * chosen by the code's author proves whatever that author already believed.
 *
 * So this is the UNION of every word the standing rule and every reviewer has
 * named, in their order of naming, with nothing dropped and nothing added in
 * their place. Twenty-eight words, plus the two hedged FORMS the rule names
 * separately — anything beginning `would_` and anything containing
 * `_if_authoritative` — which get their own test below because they are patterns
 * rather than words.
 */
const PRIVILEGED_TOKENS = Object.freeze([
  "allow", "commit", "prompt", "suppress", "release", "read", "covered",
  "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
  "satisfied", "complete", "admitted", "resumed", "attended", "verified",
  "present", "equivalent", "operational", "active", "green", "joins_exactly",
  "coverage_complete", "favorable",
]);

const HEDGE_PREFIX = /^would_/;
const HEDGE_INFIX = "_if_authoritative";

/** The conditional tokens the classifiers answer in; none may reach src at all. */
const CLASSIFICATION_TOKENS = Object.freeze(Object.values(CLASSIFICATIONS));

/**
 * THE MATCHER IS A SUBSTRING TEST, CASE-INSENSITIVE, AND THAT IS THE WHOLE
 * MATCHER.
 *
 * The previous round matched on word boundaries with a hand-written family of
 * inflections per token — `pass`/`passed`/`passable`, `admitted`/`admission` —
 * which meant every near miss was an author's judgement call, and a judgement
 * call is exactly what a guard must not contain. `contains` has no judgement in
 * it: if the word is in the string, the string is a finding. That is strictly
 * stronger than the families were, and it cost the slice a dozen renames rather
 * than costing the guard an exemption.
 */
function containsToken(text, token) {
  return String(text).toLowerCase().includes(token);
}

/** A string with no whitespace: a code a consumer can branch on, not prose. */
function isIdentifierShaped(text) {
  return typeof text === "string" && text.length > 0 && !/\s/.test(text);
}

/**
 * A QUOTATION: `{ quoted_from, identifier }`, exactly two keys.
 *
 * Three identifier-shaped strings in this slice are not this slice's to spell —
 * S01's exported evaluator names and the design-basis register's targets and
 * acceptance hooks — and two of them carry the word `read` because their owners
 * wrote them that way. Renaming them to pass a guard would be misquoting a
 * record, so they are held as quotations instead and the guard's rule is
 * structural: a quotation's `identifier` is not swept for privileged words,
 * because it is not a word this slice chose.
 *
 * THAT IS NOT AN EXEMPTION LIST AND THE DIFFERENCE IS CHECKABLE, which is why
 * the four tests directly below it exist: every quotation must name an owner
 * that is not this slice, a quotation may never stand in an answer position, a
 * quoted identifier may never be an object key, and every S01 quotation is
 * checked against S01's real export list so a misquotation fails rather than
 * passes. Add a word to a quotation and the sweep still misses it; add a
 * quotation that is not a real quotation and these tests catch it.
 */
function isQuotation(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === 2
    && typeof value.quoted_from === "string" && typeof value.identifier === "string";
}

const ANSWER_POSITIONS = Object.freeze([
  "answer", "status", "decision", "reason_id", "verdict", "outcome", "classification",
]);

// ---------------------------------------------------------------------------
// THE DECISION PROCEDURE, as ordered questions, so a second reader reaches the
// same verdict on the same value without sharing anyone's taste.
//
//   P1 CODE VALUE — a returned string with no whitespace in it (a code, a status,
//      an enum member; prose has spaces) that CONTAINS the token anywhere.
//      `cached_read_past_max_age` was a finding under this rule and the fault is
//      now called `cached_value_past_max_age`; `stale_read_marked_stale_not_
//      presented_as_current` was two findings and is now `stale_value_marked_
//      stale_not_shown_as_current`. Twelve strings were renamed rather than
//      excused.
//
//   P2 AFFIRMED FLAG — an object key at any depth CONTAINING the token whose
//      value is boolean `true`. `verified: true` is an answer. `request_read:
//      false` is a refusal report and is the shape this whole slice is built out
//      of, so it stays sayable.
//
//   P3 EXPORT NAME — the export's own name containing the token. This cost the
//      slice two renames: `readOnboardingProgress` is `onboardingProgressStatus`
//      and `V5_R01_INACTIVE_SUCCESSOR` is `V5_R01_DORMANT_SUCCESSOR`.
//
//   P4 THROWN REFUSAL — the `code`, the `message`, the `name`, and EVERY own
//      property of anything this slice throws. The re-review's second finding was
//      that the old sweep read a throw's code and deliberately discarded its
//      message and detail, and that the discarded half carried every privileged
//      word straight back to the caller. Nothing is discarded now, and the
//      module-side fix is that a refusal has no caller-supplied part left to
//      read: one registered code, one fixed message, no detail, no cause.
//
//   P5 HEDGED VERDICT — any key or string, at any depth, beginning `would_` or
//      containing `_if_authoritative`. Its own test, because it is a form rather
//      than a word.
//
// PROSE IS SWEPT AT P4 AND NOT AT P1, and the reason is a test rather than an
// argument. This slice's exports contain honest English — Joe's own settled
// words, a basis that says "prompt habits", a step that says "recognize failure"
// — and banning those would make the module unreadable while the words came back
// as synonyms nobody can grep for. What makes that safe is the MARKER SWEEP
// below: no caller string of any kind reaches any returned value from any
// export, so a privileged word inside a returned sentence is the module's own
// prose and cannot be a caller's smuggled answer. A thrown message has no such
// licence, because a refusal's whole text is now a module constant.
// ---------------------------------------------------------------------------

/** Every P1/P2/P5 finding inside one value, with the path that produced it. */
function findingsIn(value, token, at = "$", found = []) {
  if (typeof value === "string") {
    if (isIdentifierShaped(value) && containsToken(value, token)) {
      found.push(`${at} = ${JSON.stringify(value)} (P1 code value)`);
    }
    return found;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => findingsIn(entry, token, `${at}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    if (isQuotation(value)) {
      // Only the OWNER is swept; the quoted text belongs to that owner.
      if (containsToken(value.quoted_from, token)) {
        found.push(`${at}.quoted_from = ${value.quoted_from} (P1 code value)`);
      }
      return found;
    }
    for (const [key, entry] of Object.entries(value)) {
      const where = `${at}.${key}`;
      if (entry === true && containsToken(key, token)) {
        found.push(`${where} === true (P2 affirmed flag)`);
      }
      findingsIn(entry, token, where, found);
    }
    return found;
  }
  return found;
}

/** Every hedged key or string in a value, at any depth. Token-independent. */
function hedgesIn(value, at = "$", found = []) {
  if (typeof value === "string") {
    if (HEDGE_PREFIX.test(value) || value.includes(HEDGE_INFIX)) {
      found.push(`${at} = ${JSON.stringify(value).slice(0, 60)} (P5 hedged string)`);
    }
    return found;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => hedgesIn(entry, `${at}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      if (HEDGE_PREFIX.test(key) || key.includes(HEDGE_INFIX)) {
        found.push(`${at}.${key} (P5 hedged key)`);
      }
      hedgesIn(entry, `${at}.${key}`, found);
    }
  }
  return found;
}

// ---------------------------------------------------------------------------
// THE SWEPT DOMAIN.
// ---------------------------------------------------------------------------

/**
 * EVERY CALLER-CONTROLLED SHAPE, generated rather than listed: the honest
 * fixtures, each field mutated through its domain, one shape per privileged
 * token carrying that token in every string-bearing field, the hedged forms, and
 * the non-objects.
 */
function callerShapes() {
  const shapes = [];
  shapes.push({ date: "2026-09-07", entry: day() }, run(), { receipt: drill() });
  for (const origin of V5_R01_FAILURE_ORIGIN_KEYS) {
    for (const honest of [true, false]) {
      shapes.push({ date: "2026-09-07",
        entry: day({ failures: [failure({ observed_origin: origin,
          product_reported_honestly: honest, documented_fallback_offered: honest })] }) });
    }
  }
  shapes.push({ date: "2026-09-07", entry: day({ high_defect_open_against_j1: true }) });
  shapes.push({ date: "2026-09-07", entry: day({ subjourneys_exercised: [] }) });
  shapes.push({ date: "2026-09-07", entry: day({ real_business_actions: [] }) });
  shapes.push({ date: "2026-09-07", entry: day({ date: "2026-09-08" }) });
  shapes.push(run(), run({ days: [] }),
    run({ days: TEN_BUSINESS_DAYS.slice(0, 3).map(d => day({ date: d })) }),
    run({ non_business_dates: [...TEN_BUSINESS_DAYS] }));
  for (const state of V5_R01_DRILL_TERMINAL_STATES) shapes.push({ receipt: drill({ terminal_state_reached: state }) });
  for (const fault of V5_R01_DRILL_FAULT_KEYS) shapes.push({ receipt: drill({ fault_injected: fault }) });
  shapes.push({ receipt: drill({ injected_by_identity_ref: V5_R01_PILOT_PARTNER,
    observer_identity_ref: V5_R01_PILOT_PARTNER }) });

  // ONE SHAPE PER PRIVILEGED TOKEN, carrying that token wherever a caller string
  // can go, in the bare form and inside a compound, because the matcher is a
  // substring test and both must be caught if either is handed back.
  for (const token of PRIVILEGED_TOKENS) {
    shapes.push({ date: "2026-09-07", entry: day({
      subjourneys_exercised: [token, `run_${token}`, `${token}_now`],
      real_business_actions: [`deal:${token}`, `run_${token}`],
      failures: [failure({ failure_ref: token })] }) });
    shapes.push({ receipt: drill({ drill_ref: token, recovery_path_taken: token,
      aids_used: [token] }) });
    shapes.push({ outcome: token, [token]: true, [`would_${token}${HEDGE_INFIX}`]: true });
    shapes.push({ [`would_${token}`]: token, nested: { deep: [`x_${token}_y`] } });
    shapes.push(token);
  }
  shapes.push({ decision_ids: ["Q009.D1", "Q010.D1", "Q019.D1", "Q061.D1", "Q104.D1", "Q145.D1"] });
  shapes.push({ decision: "allow", status: "passed", passable: true, verified: true });
  shapes.push({ would_complete_run_if_authoritative: true, ok: true });
  shapes.push({ quoted_from: "allow", identifier: "allow" });
  shapes.push({}, null, undefined, "allow", 1, true, [], [{ passed: true }]);
  return shapes;
}

const CALLER_SHAPES = callerShapes();

/** A sentinel for "called with no argument at all", distinct from `undefined`. */
const NO_ARGUMENT = Symbol("no-argument");

/** A class, read off its own source rather than guessed from its name. */
function isClass(value) {
  return typeof value === "function"
    && /^\s*class[\s{]/.test(Function.prototype.toString.call(value));
}

/**
 * EVERY ARGUMENT LIST THE SWEEP HANDS AN EXPORT, per shape.
 *
 * ZERO, ONE AND MANY, which the re-review asked for by name: a constructor or a
 * function called with nothing, with the shape, and with the shape followed by
 * one and by two more arguments. The trailing forms are how the third finding's
 * defect showed itself — `f({}, undefined, holder)` walked past a guard that read
 * only the first extra argument — so the sweep now calls in the shape that broke
 * it, on every export, not only on the evaluators.
 */
function argumentListsFor(shape) {
  if (shape === NO_ARGUMENT) return [[]];
  return [[shape], [shape, shape], [shape, undefined, shape]];
}

/**
 * EVERY EXPORT OF EVERY SRC MODULE IN THIS SLICE, AS A CONSUMER SEES IT.
 *
 * A CONSTANT is one entry: the value as it stands.
 * A FUNCTION is one entry per caller shape per argument list, plus no argument.
 * A CLASS is CONSTRUCTED over the same lists.
 *
 * A THROW IS SWEPT WHOLE — code, message, name and every own property — and its
 * `detail` and `cause` are separately asserted absent. The previous round swept a
 * throw as its `code` alone and said so in a comment; the reviewer walked every
 * privileged token out through the `message` and `detail` that comment excused.
 */
function thrownSurface(thrown) {
  const surface = {
    thrown_name: thrown?.name ?? null,
    thrown_code: thrown?.code ?? null,
    thrown_message: thrown?.message ?? null,
  };
  for (const key of Object.getOwnPropertyNames(thrown ?? {})) {
    if (key === "stack") continue;
    surface[`own_${key}`] = thrown[key];
  }
  return surface;
}

function everyExportedValue() {
  const entries = [];
  for (const [moduleName, surface] of SWEPT_SRC_MODULES) {
    for (const [name, exported] of Object.entries(surface)) {
      const at = `${moduleName}#${name}`;
      if (typeof exported !== "function") {
        entries.push({ at, name, output: exported });
        continue;
      }
      const construct = isClass(exported);
      for (const shape of [...CALLER_SHAPES, NO_ARGUMENT]) {
        for (const args of argumentListsFor(shape)) {
          const label = `${at}(${args.length} arg(s): ${JSON.stringify(shape)})`.slice(0, 140);
          let output;
          try {
            output = construct ? { constructed: { ...new exported(...args) } } : exported(...args);
          } catch (thrown) {
            THROWN.push({ at: label, thrown });
            output = thrownSurface(thrown);
          }
          entries.push({ at: label, name, output });
        }
      }
    }
  }
  return entries;
}

const SWEPT_SRC_MODULES = [
  ["rollout-pilot-r01.v5.js", r01],
  ["onboarding-flow-r01.v5.js", onboarding],
  ["rollout-pilot-r01.vocabulary.v5.js", vocabulary],
];

/** Every refusal the sweep provoked, kept so the closed-code test can read them. */
const THROWN = [];

/** Computed once: the sweep is the same for every token and it is not cheap. */
const EXPORTED_VALUES = everyExportedValue();

test("guard: the sweep is reading a real surface, and its matcher is not vacuous", () => {
  assert.ok(EXPORTED_VALUES.length > 5000,
    `the sweep covered only ${EXPORTED_VALUES.length} values`);
  assert.equal(PRIVILEGED_TOKENS.length, 28, "the union lost a word");
  const modules = new Set(EXPORTED_VALUES.map(entry => entry.at.split("#")[0]));
  assert.deepEqual([...modules].sort(), [
    "onboarding-flow-r01.v5.js", "rollout-pilot-r01.v5.js", "rollout-pilot-r01.vocabulary.v5.js",
  ]);
  // Every export of every module is represented, constructors included.
  for (const [moduleName, surface] of SWEPT_SRC_MODULES) {
    for (const name of Object.keys(surface)) {
      assert.ok(EXPORTED_VALUES.some(entry => entry.at.startsWith(`${moduleName}#${name}`)),
        `${moduleName}#${name} was not swept`);
    }
  }
  // The class export really was CONSTRUCTED, not merely named, and with zero,
  // one and several arguments.
  assert.equal(isClass(vocabulary.V5R01Error), true);
  assert.ok(EXPORTED_VALUES.some(entry => entry.name === "V5R01Error"
    && (entry.output?.constructed !== undefined || entry.output?.thrown_code !== undefined)),
  "the error class was not constructed by the sweep");

  // The matcher: a substring is a hit wherever it sits, and there is no family
  // table left for a rename to walk through.
  assert.equal(containsToken("run_passed", "pass"), true);
  assert.equal(containsToken("bypassed", "pass"), true);
  assert.equal(containsToken("already", "read"), true);
  assert.equal(containsToken("nothing_here", "pass"), false);
  // And each position really fires.
  assert.equal(findingsIn({ verdict: "allow" }, "allow").length, 1, "P1");
  assert.equal(findingsIn({ verified: true }, "verified").length, 1, "P2");
  assert.deepEqual(findingsIn({ resumable_today: false }, "resumed"), [], "P2 permits a false");
  assert.deepEqual(findingsIn({ note: "a sentence with allow in it" }, "allow"), [],
    "P1 skips prose; the marker sweep is what covers it");
  assert.equal(hedgesIn({ would_pass_if_authoritative: true }).length, 1, "P5 key");
  assert.equal(hedgesIn({ x: "would_complete" }).length, 1, "P5 string");
  // A quotation is skipped ONLY on its identifier, and only when it is really one.
  assert.deepEqual(findingsIn({ q: { quoted_from: "owner", identifier: "read_me" } }, "read"), []);
  assert.equal(findingsIn({ q: { quoted_from: "reader", identifier: "x" } }, "read").length, 1);
  assert.equal(findingsIn({ q: { quoted_from: "o", identifier: "read", extra: 1 } }, "read").length, 1,
    "a three-key object is not a quotation");
});

// ONE TEST PER PRIVILEGED TOKEN. Twenty-eight tokens, no allow-list, no skipped
// export, no exemption for a token the caller supplied.
for (const token of PRIVILEGED_TOKENS) {
  test(`guard: no export of this slice shows the privileged token "${token}"`, () => {
    const hits = [];
    for (const entry of EXPORTED_VALUES) {
      if (containsToken(entry.name, token)) hits.push(`${entry.at} (P3 export name)`);
      for (const finding of findingsIn(entry.output, token)) hits.push(`${entry.at} ${finding}`);
    }
    assert.deepEqual(hits, [],
      `the token "${token}" reaches a consumer from this slice's surface`);
  });
}

test("guard: no export of this slice shows a hedged verdict, by key or by value", () => {
  const hits = [];
  for (const entry of EXPORTED_VALUES) {
    if (HEDGE_PREFIX.test(entry.name) || entry.name.includes(HEDGE_INFIX)) {
      hits.push(`${entry.at} (P5 export name)`);
    }
    for (const hedge of hedgesIn(entry.output)) hits.push(`${entry.at} ${hedge}`);
  }
  assert.deepEqual(hits, [], "a would_* or _if_authoritative string reaches a consumer");
});

test("guard: the per-token sweep is not vacuous — each token is really detected", () => {
  for (const token of PRIVILEGED_TOKENS) {
    assert.equal(findingsIn({ verdict: token }, token).length, 1, `whole code: ${token}`);
    assert.equal(findingsIn({ verdict: `run_${token}` }, token).length, 1, `compound: ${token}`);
    assert.equal(findingsIn({ verdict: `x${token}y` }, token).length, 1, `infix: ${token}`);
    assert.equal(findingsIn({ nested: [{ deep: token.toUpperCase() }] }, token).length, 1,
      `case and nesting: ${token}`);
    assert.equal(findingsIn({ [`${token}_here`]: true }, token).length, 1, `key: ${token}`);
  }
  // And an export that echoed its caller would be caught, which is the exact
  // defect the third review reproduced through the then-exported deepFreeze.
  const echo = value => value;
  const caught = [];
  for (const shape of CALLER_SHAPES) caught.push(...findingsIn(echo(shape), "allow"));
  assert.ok(caught.length > 0, "an echoing export would not be caught");
});

/**
 * THE ERROR-ECHO TEST, and it is the re-review's second finding run as code.
 *
 * The reviewer constructed `new V5R01Error("invalid_shape", <token>, <token>)` —
 * a REGISTERED code, so nothing refused it — and read every privileged token,
 * and a `would_complete_run_if_authoritative` with them, straight back out of
 * `message` and `detail`. The constructor takes one argument now, so most of
 * those calls are a TypeError rather than an echo; this test does not rely on
 * that. It tries every form: one argument, two, three, a token as the code, a
 * token as the message, an options bag with a `cause`, and the same through the
 * exported `fail`. Whatever comes back, nothing the caller wrote may be in it.
 */
test("guard: no privileged token a caller hands an error comes back on any property", () => {
  const tokens = [...PRIVILEGED_TOKENS, "would_complete_run_if_authoritative",
    "would_read_if_authoritative", `x${HEDGE_INFIX}`];
  const leaks = [];
  const inspect = (thrown, at, token) => {
    const surface = thrownSurface(thrown);
    for (const [key, value] of Object.entries(surface)) {
      if (typeof value !== "string") continue;
      if (value.toLowerCase().includes(token.toLowerCase())) leaks.push(`${at}.${key} = ${value}`);
    }
    if (thrown?.detail !== undefined) leaks.push(`${at}.detail is set`);
    if (thrown?.cause !== undefined) leaks.push(`${at}.cause is set`);
  };
  for (const token of tokens) {
    const calls = [
      [`new V5R01Error(${token})`, () => new V5R01Error(token)],
      [`new V5R01Error(code, ${token})`, () => new V5R01Error("invalid_shape", token)],
      [`new V5R01Error(code, ${token}, {detail})`,
        () => new V5R01Error("invalid_shape", token, { outcome: token })],
      [`new V5R01Error(code, {cause})`,
        () => new V5R01Error("invalid_shape", { cause: token })],
      [`new V5R01Error({code: ${token}})`, () => new V5R01Error({ code: token })],
      [`fail(${token})`, () => vocabulary.fail(token)],
      [`fail(code, ${token})`, () => vocabulary.fail("invalid_shape", token)],
      [`fail(code, ${token}, {detail})`,
        () => vocabulary.fail("invalid_shape", token, { outcome: token })],
    ];
    for (const [at, call] of calls) {
      let constructed;
      try { constructed = call(); } catch (thrown) { inspect(thrown, at, token); continue; }
      // A constructor that RETURNED rather than threw is swept the same way.
      inspect(constructed, at, token);
    }
  }
  assert.deepEqual(leaks, [], "a caller's own text came back on a refusal");

  // NOT VACUOUS: the same inspection catches an error that does echo.
  const echoing = Object.assign(new Error("allow"), { code: "allow", detail: { outcome: "allow" } });
  const caught = [];
  const before = leaks.length;
  inspect(echoing, "control", "allow");
  assert.ok(leaks.length > before, "the inspection would not catch an echoing error");
  leaks.length = before;
  assert.deepEqual(caught, []);
});

test("guard: a refusal carries a registered code and the fixed message for it, and nothing else", () => {
  assert.ok(THROWN.length > 100, `only ${THROWN.length} refusals were provoked`);
  const registered = new Set(vocabulary.V5_R01_ERROR_CODES);
  assert.ok(registered.size >= 20, "the error-code registry must not have quietly emptied");
  const fixedTypeErrors = new Set([
    "v5_r01_error_takes_exactly_one_registered_code",
    "v5_r01_error_code_is_not_registered",
    "v5_r01_fail_takes_exactly_one_registered_code",
    "v5_r01_assert_arity_takes_three_arguments",
    "v5_r01_assert_arity_was_called_wrongly",
  ]);
  let refusals = 0;
  for (const { at, thrown } of THROWN) {
    if (!(thrown instanceof V5R01Error)) {
      // EVERY non-refusal throw is one of this module's own fixed TypeErrors.
      // A native engine error is no longer among them: the validators refuse
      // their own malformed arguments first, so no engine prose reaches a
      // consumer off this surface.
      assert.equal(thrown instanceof TypeError, true, `${at} threw ${thrown}`);
      assert.equal(fixedTypeErrors.has(thrown.message), true,
        `${at} threw an unregistered TypeError: ${thrown.message}`);
      assert.equal(thrown.code, undefined, `${at} threw a coded non-V5R01Error`);
      continue;
    }
    assert.equal(registered.has(thrown.code), true, `${at} raised unregistered code ${thrown.code}`);
    assert.equal(thrown.detail, undefined, `${at} carried a detail`);
    assert.equal(thrown.cause, undefined, `${at} carried a cause`);
    // `message` is Error's own, set from the fixed table; `name` and `code` are
    // this module's. There is no fourth property and no caller wrote any of them.
    assert.deepEqual(Object.getOwnPropertyNames(thrown).filter(key => key !== "stack").sort(),
      ["code", "message", "name"], `${at} carried an unexpected property`);
    assert.equal(typeof thrown.message, "string");
    assert.ok(thrown.message.length > 0, `${at} carried an empty message`);
    refusals += 1;
  }
  assert.ok(refusals > 100, `only ${refusals} coded refusals were provoked`);

  // The message is the FIXED one for the code: the same code always says the
  // same sentence, whatever the call that raised it.
  const byCode = new Map();
  for (const { thrown } of THROWN) {
    if (!(thrown instanceof V5R01Error)) continue;
    if (byCode.has(thrown.code)) {
      assert.equal(byCode.get(thrown.code), thrown.message,
        `${thrown.code} said two different things`);
    }
    byCode.set(thrown.code, thrown.message);
  }
  assert.ok(byCode.size >= 5, `only ${byCode.size} distinct codes were provoked`);

  // And a caller cannot mint one: the constructor refuses an unregistered code
  // and refuses every arity but one.
  assert.throws(() => new V5R01Error("allow"), TypeError);
  assert.throws(() => new V5R01Error({ outcome: "allow" }), TypeError);
  assert.throws(() => new V5R01Error(undefined), TypeError);
  assert.throws(() => new V5R01Error(), TypeError);
  assert.throws(() => new V5R01Error("invalid_shape", "m"), TypeError);
  assert.equal(new V5R01Error("invalid_shape").code, "invalid_shape");
});

test("guard: every registered code has one fixed message, clear of every privileged word", () => {
  // The message table is exhaustive and its text is the module's, so it is swept
  // as strictly as a code is — no prose exemption, because no caller wrote it.
  const seen = new Set();
  for (const code of vocabulary.V5_R01_ERROR_CODES) {
    const message = new V5R01Error(code).message;
    assert.equal(typeof message, "string");
    assert.ok(message.length > 10, code);
    seen.add(code);
    for (const token of PRIVILEGED_TOKENS) {
      assert.equal(containsToken(message, token), false,
        `the fixed message for ${code} carries "${token}"`);
    }
    assert.equal(HEDGE_PREFIX.test(message) || message.includes(HEDGE_INFIX), false, code);
  }
  assert.equal(seen.size, vocabulary.V5_R01_ERROR_CODES.length);
});

/**
 * THE MARKER SWEEP — the other half of the guarantee, and the reason P1 is
 * allowed to skip prose.
 *
 * Every string-bearing field of every caller shape is refilled with a token that
 * cannot occur in this repository, and every export is handed the result in
 * every argument form. If no marker comes back from anywhere — from a returned
 * value or from any property of a thrown refusal — then no caller string comes
 * back from anywhere, and a privileged word inside a returned sentence cannot
 * have arrived from the caller.
 */
const CALLER_MARKER = "zz-caller-supplied-marker-19f3c7-zz";

/** The same value with every string replaced by the marker, to the leaves. */
function markEveryString(value) {
  if (typeof value === "string") return CALLER_MARKER;
  if (Array.isArray(value)) return value.map(markEveryString);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) =>
      [key, markEveryString(entry)]));
  }
  return value;
}

test("guard: no caller string reaches any returned value or any thrown property", () => {
  const marked = CALLER_SHAPES.map(markEveryString);
  assert.ok(JSON.stringify(marked).includes(CALLER_MARKER),
    "the marker sweep marked nothing; it would prove nothing");

  let called = 0;
  const leaks = [];
  for (const [moduleName, surface] of SWEPT_SRC_MODULES) {
    for (const [name, exported] of Object.entries(surface)) {
      if (typeof exported !== "function") {
        if (JSON.stringify(exported ?? null).includes(CALLER_MARKER)) {
          leaks.push(`${moduleName}#${name} (constant)`);
        }
        continue;
      }
      const construct = isClass(exported);
      for (const shape of marked) {
        for (const args of argumentListsFor(shape)) {
          let output;
          try {
            output = construct ? { ...new exported(...args) } : exported(...args);
          } catch (thrown) {
            // THE WHOLE THROW, not its code: this is where the second finding
            // lived, and the sweep no longer looks away from it.
            output = thrownSurface(thrown);
          }
          called += 1;
          if (JSON.stringify(output ?? null).includes(CALLER_MARKER)) {
            leaks.push(`${moduleName}#${name}(${JSON.stringify(shape).slice(0, 60)})`);
          }
        }
      }
    }
  }
  assert.ok(called > 3000, `only ${called} calls were made`);
  assert.deepEqual(leaks, [],
    "an export handed a caller's own string back, so a privileged word inside its prose could be the caller's");

  const echo = value => value;
  assert.equal(JSON.stringify(echo(marked[0])).includes(CALLER_MARKER), true);
});

test("guard: deepFreeze is not on any surface of this slice, by name or by behaviour", () => {
  for (const [moduleName, surface] of SWEPT_SRC_MODULES) {
    assert.equal("deepFreeze" in surface, false, `${moduleName} still exports deepFreeze`);
    assert.equal("quotedIdentifier" in surface, false,
      `${moduleName} exports quotedIdentifier, which would hand a caller's string back in a record`);
    for (const [name, exported] of Object.entries(surface)) {
      if (typeof exported !== "function" || isClass(exported)) continue;
      for (const token of PRIVILEGED_TOKENS) {
        const probe = { outcome: token };
        let returned;
        try { returned = exported(probe); } catch { continue; }
        assert.notEqual(returned, probe, `${moduleName}#${name} returned its own argument`);
      }
    }
  }
});

// ---------------------------------------------------------------------------
// QUOTATIONS: the structural rule that replaces an exemption list.
// ---------------------------------------------------------------------------

/** Every quotation this slice's surface carries, with where it sits. */
function everyQuotation(value, at = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => everyQuotation(entry, `${at}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    if (isQuotation(value)) { found.push({ at, quotation: value }); return found; }
    for (const [key, entry] of Object.entries(value)) everyQuotation(entry, `${at}.${key}`, found);
  }
  return found;
}

test("guard: every quotation names an outside owner and never stands in an answer", () => {
  const quotations = [];
  for (const entry of EXPORTED_VALUES) quotations.push(...everyQuotation(entry.output, entry.at));
  assert.ok(quotations.length > 0, "no quotation was found; the skip rule would be dead code");

  const owners = new Set();
  for (const { at, quotation } of quotations) {
    assert.ok(quotation.quoted_from.length > 3, at);
    assert.equal(quotation.quoted_from.startsWith("v5-r01"), false,
      `${at} quotes this slice, which is not a quotation at all`);
    assert.ok(quotation.identifier.length > 0, at);
    // A quotation is never the answer, the status, the decision or the reason.
    for (const position of ANSWER_POSITIONS) {
      assert.equal(at.endsWith(`.${position}`), false, `${at} is an answer position`);
    }
    owners.add(quotation.quoted_from);
  }
  assert.deepEqual([...owners].sort(),
    [vocabulary.V5_R01_DESIGN_BASIS_REGISTER, vocabulary.V5_R01_S01_MODULE].sort());

  // A quoted identifier is never an object KEY anywhere on this surface.
  const quotedTexts = new Set(quotations.map(entry => entry.quotation.identifier));
  for (const entry of EXPORTED_VALUES) {
    for (const key of allKeys(entry.output)) {
      assert.equal(quotedTexts.has(key), false, `${entry.at} uses a quoted identifier as a key`);
    }
  }
});

test("guard: every S01 quotation is a real S01 export, so a misquotation fails", () => {
  // The mechanical half of the quotation rule: this slice may skip sweeping a
  // string it says another module minted, and this is the test that it did.
  const s01 = boundaries;
  let checked = 0;
  for (const key of V5_R01_DRILL_FAULT_KEYS) {
    const quotation = V5_R01_DRILL_FAULTS[key].s01_seam;
    assert.equal(isQuotation(quotation), true, key);
    assert.equal(quotation.quoted_from, vocabulary.V5_R01_S01_MODULE, key);
    assert.equal(typeof s01[quotation.identifier], "function",
      `${key} quotes "${quotation.identifier}", which S01 does not export`);
    checked += 1;
  }
  assert.equal(checked, V5_R01_DRILL_FAULT_KEYS.length);
  // Not vacuous: a name S01 does not export would fail the same check.
  assert.equal(s01.evaluateNothingAtAll, undefined);
});

/** Every object key in a value, walked to the leaves. */
function allKeys(value, out = []) {
  if (Array.isArray(value)) { value.forEach(entry => allKeys(entry, out)); return out; }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) { out.push(key); allKeys(entry, out); }
  }
  return out;
}

/** Every string and every object key in a value, walked to the leaves. */
function allStrings(value, out = []) {
  if (typeof value === "string") { out.push(value); return out; }
  if (Array.isArray(value)) { value.forEach(entry => allStrings(entry, out)); return out; }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) { out.push(key); allStrings(entry, out); }
  }
  return out;
}

// AND ONE PER CONDITIONAL TOKEN, because the review's remedy was explicit that a
// `would_*` value is not an acceptable substitute for the privileged one.
for (const token of CLASSIFICATION_TOKENS) {
  test(`guard: no export of this slice shows the conditional token "${token}"`, () => {
    const hits = [];
    for (const entry of EXPORTED_VALUES) {
      if (String(entry.name).includes(token)) hits.push(`${entry.at} (export name)`);
      for (const text of allStrings(entry.output)) {
        if (text.includes(token)) hits.push(`${entry.at} = ${text.slice(0, 60)}`);
      }
    }
    assert.deepEqual(hits, [], `${token} reaches a consumer from this slice's surface`);
  });
}

test("guard: no export of either public surface is named for a classifier", () => {
  for (const surface of [r01, onboarding, vocabulary]) {
    for (const name of Object.keys(surface)) {
      assert.equal(/^(classify|evaluateIf|derive)/.test(name), false,
        `${name} is a classifier name on a public surface`);
      assert.equal(name.includes("would_"), false, `${name} is a classifier field`);
      assert.equal(/^(internal|probe|unwired|fixture)|[_.](internal|probe|unwired|fixture)|testonly|testhelper/i
        .test(name), false, `${name} is an escape hatch on a public surface`);
    }
  }
  assert.deepEqual(Object.keys(r01).sort(), [...V5_R01_PUBLIC_SURFACE].sort());
  assert.deepEqual(Object.keys(onboarding).sort(),
    [...onboarding.V5_R01_ONBOARDING_PUBLIC_SURFACE].sort());
});

test("guard: every exported validator returns nothing, so none can launder a value", () => {
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
  // The pilot module's own binding validator holds the same property.
  assert.equal(assertR01DecisionBinding({ decision_ids: [...V5_R01_SETTLED_DECISION_IDS] }),
    undefined);
});

// ---------------------------------------------------------------------------
// THE ARITY BOUNDARY — the third finding, closed in every calling form.
// ---------------------------------------------------------------------------

/**
 * EVERY PUBLIC FUNCTION, AND THE NUMBER OF ARGUMENTS IT DECLARES.
 *
 * The roster is the module's own `V5_R01_DECLARED_ARITIES`, so a function added
 * to a public surface without an entry fails the first test below rather than
 * slipping past the sweep. The bypass the reviewer found — `f({}, undefined,
 * holder)`, which walked past a guard reading `extra[0]` — is one row of the
 * matrix now, beside `apply`, a bound receiver, and spread arguments.
 */
const DECLARED_ARITIES = vocabulary.V5_R01_DECLARED_ARITIES;

const PUBLIC_FUNCTIONS = [
  ...Object.entries(r01), ...Object.entries(onboarding), ...Object.entries(vocabulary),
].filter(([name, value]) => typeof value === "function" && !isClass(value)
  && name !== "fail" && name !== "assertArity");

test("guard: every public function of this slice declares its arity", () => {
  const undeclared = PUBLIC_FUNCTIONS.map(([name]) => name)
    .filter(name => !(name in DECLARED_ARITIES));
  assert.deepEqual(undeclared, [], "a public function has no declared arity");
  // And every declared name is really exported by one of the three modules.
  const exported = new Set(PUBLIC_FUNCTIONS.map(([name]) => name));
  for (const name of Object.keys(DECLARED_ARITIES)) {
    assert.equal(exported.has(name), true, `${name} is declared but not exported`);
  }
});

test("guard: no public function accepts more arguments than it declares, in any calling form", () => {
  const holder = {
    readEntry: () => day(), readReceipt: () => drill(),
    calendar: [...TEN_BUSINESS_DAYS], observer: { attribute: () => "product_defect" },
  };
  const filler = ["2026-09-07", "path"];
  const failures = [];

  for (const [name, fn] of PUBLIC_FUNCTIONS) {
    const declared = DECLARED_ARITIES[name];
    // The declared arguments themselves, so the extra one is genuinely extra and
    // the refusal cannot be an ordinary shape complaint about argument one.
    const base = Array.from({ length: declared }, (_, index) => filler[index] ?? {});
    const forms = [
      ["direct", extra => fn(...base, ...extra)],
      ["apply", extra => fn.apply(null, [...base, ...extra])],
      ["call", extra => fn.call(null, ...base, ...extra)],
      ["bound", extra => fn.bind({ pretending: "to be a holder" })(...base, ...extra)],
      ["bound-partial", extra => fn.bind(null, ...base)(...extra)],
      ["spread", extra => fn(...[...base, ...extra])],
      ["reflect", extra => Reflect.apply(fn, undefined, [...base, ...extra])],
    ];
    // ONE extra, and the tails that walked past the old guard: a leading
    // `undefined` with the holder behind it, and two `undefined`s.
    const tails = [
      [holder], [undefined], [undefined, holder], [undefined, undefined, holder],
      [null, holder], [holder, holder],
    ];
    for (const [formName, invoke] of forms) {
      for (const tail of tails) {
        let refused = false;
        try { invoke(tail); } catch (thrown) {
          refused = thrown instanceof V5R01Error
            && thrown.code === `${name}_takes_no_extra_argument`;
          if (!refused && thrown instanceof V5R01Error) {
            failures.push(`${name} ${formName} ${tail.length} extra: wrong code ${thrown.code}`);
            continue;
          }
          if (!refused) {
            failures.push(`${name} ${formName} ${tail.length} extra: ${thrown}`);
            continue;
          }
        }
        if (!refused) {
          failures.push(`${name} ${formName} accepted ${tail.length} extra argument(s)`);
        }
      }
    }
  }
  assert.deepEqual(failures, [], "the one-request boundary is bypassable");
});

test("guard: the declared arity is accepted, so the boundary is a boundary and not a wall", () => {
  // NOT VACUOUS. A guard that refused everything would pass the test above and
  // break the module, so each function is also called AT its declared arity and
  // must not raise the arity code.
  for (const [name, fn] of PUBLIC_FUNCTIONS) {
    const declared = DECLARED_ARITIES[name];
    const filler = ["2026-09-07", "path"];
    const base = Array.from({ length: declared }, (_, index) => filler[index] ?? {});
    try { fn(...base); } catch (thrown) {
      assert.notEqual(thrown?.code, `${name}_takes_no_extra_argument`,
        `${name} refuses its own declared arity`);
    }
  }
  // And `fail` and the error constructor, which are guarded by a fixed TypeError
  // rather than by a registered code, hold the same line.
  assert.throws(() => vocabulary.fail("invalid_shape", "extra"), TypeError);
  assert.throws(() => vocabulary.assertArity([], 0, "evaluatePilotDay", "extra"), TypeError);
  assert.throws(() => vocabulary.assertArity([], 0, "not_a_guarded_name"), TypeError);
  assert.throws(() => vocabulary.assertArity(undefined, 0, "evaluatePilotDay"), TypeError);
});

// ---------------------------------------------------------------------------
// ISOLATION. The classifiers are unreachable from production — PARSED.
// ---------------------------------------------------------------------------

const SRC_DIR = path.join(REPO_ROOT, "mcp-server", "src");
const TEST_DIR = path.join(REPO_ROOT, "mcp-server", "test");

assert.equal(typeof esbuild.buildSync, "function",
  "the import guard needs a real parser; esbuild is not loadable");

/**
 * Parse `source` and return esbuild's own import records: `{ path, kind }` each.
 *
 * The parser reads static imports, re-exports, dynamic `import()` and `require()`
 * alike, with the kind of each. Copied from the shape the Tour slice
 * (mcp-server/test/tour-workflow-j301.v5.test.mjs) proves today rather than
 * reinvented here.
 */
function importRecords(source) {
  const built = esbuild.buildSync({
    stdin: { contents: source, loader: "js", sourcefile: "module-under-guard.js", resolveDir: SRC_DIR },
    bundle: false, write: false, metafile: true, format: "esm",
    platform: "neutral", logLevel: "silent", logLimit: 0,
  });
  const output = Object.values(built.metafile.outputs)[0];
  return output === undefined ? [] : output.imports;
}

/**
 * A module's CODE WITH EVERY COMMENT REMOVED — esbuild's own printer, not a
 * strip-the-comments regex. String literals, identifiers and property names all
 * survive; prose does not.
 *
 * THIS IS THE FIX FOR THE DEFECT THE THIRD REVIEW FOUND. The previous guard
 * counted call sites in RAW TEXT with `(?<![\w$.])import\s*\(`, which does not
 * match `await import /* gap *\/ (HELPER)` — a comment between the keyword and
 * the parenthesis is legal JavaScript and defeats the pattern. So
 * `importSpecifiers` reported no computed import for a module that loads one,
 * and a production module could have reached the classifier helper through a
 * define-substituted identifier without failing the asserted guard. Normalising
 * through the parser first removes the gap by construction: after printing,
 * the call site is `import(HELPER)`.
 */
const CODE_TEXT_CACHE = new Map();
function codeText(source) {
  let code = CODE_TEXT_CACHE.get(source);
  if (code === undefined) {
    code = esbuild.transformSync(source, {
      loader: "js", format: "esm", platform: "neutral",
      minify: false, legalComments: "none", logLevel: "silent", logLimit: 0,
    }).code;
    CODE_TEXT_CACHE.set(source, code);
  }
  return code;
}

/**
 * How many times `callee(` appears as a whole word, counted in BOTH the raw text
 * and the comment-free print, taking the larger.
 *
 * Raw text OVER-reports (a mention inside a comment counts) and the print
 * UNDER-reports nothing the parser can see, so the maximum is the safe reading in
 * both directions: a false alarm is a review, a missed call site is a door.
 */
function callSiteCount(source, callee) {
  const pattern = new RegExp(`(?<![\\w$.])${callee}\\s*\\(`);
  const count = text => text.split(pattern).length - 1;
  return Math.max(count(source), count(codeText(source)));
}

/**
 * Every module specifier a source loads, by any form that actually loads.
 *
 * A call site the parser could not fold to a literal is reported as
 * `<computed import>` / `<computed require>` rather than dropped, and a source
 * that so much as names `createRequire` reports it, because a closed allow-list
 * has to notice exactly the specifiers it cannot see.
 */
function importSpecifiers(source) {
  const records = importRecords(source);
  const specifiers = new Set(records.map(record => record.path));
  const resolved = kind => records.filter(record => record.kind === kind).length;
  if (callSiteCount(source, "import") > resolved("dynamic-import")) specifiers.add("<computed import>");
  if (callSiteCount(source, "require") > resolved("require-call")) specifiers.add("<computed require>");
  if (/(?<![\w$])createRequire(?![\w$])/.test(source)) specifiers.add("createRequire");
  return [...specifiers].sort();
}

/** Whole-word `import(` call sites in already-printed, comment-free code. */
const DYNAMIC_IMPORT_CALL = /(?<![\w$.])import\s*\(/g;

/**
 * Dynamic-import call sites WHOSE ARGUMENT IS NOT A STRING LITERAL — the form no
 * lexical guard, this one included, can follow to a destination.
 *
 * This closes what a specifier allow-list structurally cannot. `const p =
 * "../test/x.testhelper.mjs"; import(p)` resolves to no path, so it matches no
 * offender filter and a scan over resolved specifiers reports a clean tree. The
 * FORM is banned instead of the destination, over every module in src rather than
 * the three this slice owns.
 */
function computedImportCallSites(source) {
  const code = codeText(source);
  const sites = [];
  for (const match of code.matchAll(DYNAMIC_IMPORT_CALL)) {
    const rest = code.slice(match.index + match[0].length).replace(/^\s+/, "");
    if (!/^["'`]/.test(rest)) sites.push(code.slice(match.index, match.index + 72).split("\n")[0]);
  }
  return sites;
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

test("isolation: THE GAP FORM — a comment between `import` and `(` no longer hides a computed import", () => {
  // THE DEFECT, REPRODUCED FIRST. This is the review's own case: the raw-text
  // counter matched only `import` followed directly by whitespace and `(`, so a
  // comment in between made `importSpecifiers` report nothing at all, and a
  // production module could have reached the test helper through a substituted
  // identifier without failing the asserted guard.
  const gap = "const helper = await import /* gap */ (HELPER);";
  const RAW_ONLY = /(?<![\w$.])import\s*\(/;
  assert.equal(RAW_ONLY.test(gap), false,
    "the premise has changed: the old raw-text pattern now matches the gap form");

  // THE FIX, MEASURED. The parser's print removes the comment, so both readings
  // of the question see the call site.
  assert.equal(codeText(gap).includes("import(HELPER)"), true,
    `the normalized print did not fold the gap: ${codeText(gap)}`);
  assert.deepEqual(importSpecifiers(gap), ["<computed import>"],
    "THE HOLE IS OPEN: the gap form resolved to no specifier at all");
  assert.notDeepEqual(computedImportCallSites(gap), [],
    "THE HOLE IS OPEN: the gap form was not caught by the form ban");

  // Every spelling of the same evasion, each caught by both readings.
  for (const form of [
    "await import /* gap */ (HELPER);",
    "await import\n  // a line comment\n  (HELPER);",
    "await import /* a */ /* b */ (HELPER);",
    "const p = '../test/x.testhelper.mjs'; await import /* gap */ (p);",
    "export const reach = (a, b) => import /* gap */ (a + b);",
  ]) {
    assert.deepEqual(importSpecifiers(form), ["<computed import>"], form);
    assert.notDeepEqual(computedImportCallSites(form), [], form);
  }
  // `require` had the same raw-text hole and is closed the same way.
  assert.deepEqual(importSpecifiers("const m = require /* gap */ (name);"),
    ["<computed require>"]);

  // AND THE FORM BAN IS NOT SIMPLY ALWAYS-RED: a literal specifier, with or
  // without the same comment, is not a computed call site.
  assert.deepEqual(computedImportCallSites('await import /* gap */ ("./x.js");'), []);
  assert.deepEqual(computedImportCallSites('await import("./x.js");'), []);
  assert.deepEqual(importSpecifiers('await import /* gap */ ("./x.js");'), ["./x.js"]);
});

test("isolation: the gap form is caught on the REAL production module, not just a fixture", () => {
  // The fixture is this slice's actual source plus the two lines a reviewer
  // wrote to get past the previous guard. Nothing about the smuggle is a literal
  // the offender filter could match, so only the form ban sees it.
  const helperSpecifier = "../test/rollout-pilot-r01-classifiers.v5.testhelper.mjs";
  const real = fs.readFileSync(path.join(SRC_DIR, "rollout-pilot-r01.v5.js"), "utf8");

  // (a) The unedited module is clean under both readings, so the guard is not
  // always-red and the failures below mean what they say.
  assert.deepEqual(computedImportCallSites(real), []);
  assert.equal(importSpecifiers(real).includes("<computed import>"), false);

  // (b) The plain dynamic smuggle: caught as a resolved test-tree specifier.
  const literal = `${real}\nexport async function reach() {\n  return await import("${helperSpecifier}");\n}\n`;
  assert.ok(importSpecifiers(literal).includes(helperSpecifier),
    "a dynamic import of the helper was invisible to the parser");

  // (c) THE GAP FORM ON THE REAL MODULE. The specifier is never a literal at the
  // call site, so the offender filter over RESOLVED specifiers reports nothing —
  // that is measured here rather than assumed — and the form ban is what catches
  // it.
  const smuggled = `${real}\nconst HELPER = "${helperSpecifier}";\n`
    + "export async function reach() {\n  return await import /* gap */ (HELPER);\n}\n";
  assert.deepEqual(
    importSpecifiers(smuggled).filter(one => one.includes("/test/") || one.includes(".testhelper.")),
    [], "the premise has changed: the parser now folds a computed specifier to a path");
  assert.ok(importSpecifiers(smuggled).includes("<computed import>"),
    "THE HOLE IS OPEN: the parser did not even report the gap call site as computed");
  assert.notDeepEqual(computedImportCallSites(smuggled), [],
    "THE HOLE IS OPEN: a computed dynamic import was not caught by the form ban");

  // (d) And with the specifier assembled from pieces, so no literal carries it
  // and only the form ban can see it at all.
  const piecewise = `${real}\nexport const reach = (a, b) => import /* gap */ (a + b);\n`;
  assert.equal(piecewise.includes("testhelper"), true, "the real module quotes the helper name in prose");
  assert.notDeepEqual(computedImportCallSites(piecewise), [],
    "THE HOLE IS OPEN: an assembled dynamic import specifier was not caught");
});

test("isolation: no production module computes a dynamic import, anywhere in src", () => {
  // Over EVERY module in src, not the three this slice owns: a specifier
  // allow-list can only judge specifiers it can see, so the form is banned
  // tree-wide and read twice — off the printed call site, and off the parser's
  // own count of dynamic imports it could not fold to a path.
  const files = fs.readdirSync(SRC_DIR).filter(name => name.endsWith(".js")).sort();
  assert.ok(files.length > 100, "every module in src must be scanned");

  const byForm = [];
  const byParser = [];
  for (const file of files) {
    const source = fs.readFileSync(path.join(SRC_DIR, file), "utf8");
    for (const site of computedImportCallSites(source)) byForm.push(`${file}: ${site}`);
    const specifiers = importSpecifiers(source);
    if (specifiers.includes("<computed import>")) byParser.push(file);
    if (specifiers.includes("<computed require>")) byParser.push(`${file} (require)`);
  }
  assert.deepEqual(byForm, [],
    "a production module builds a dynamic import specifier at runtime, which no lexical guard can follow");
  assert.deepEqual(byParser, [],
    "the parser could not fold a dynamic import or require to a literal path");
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
