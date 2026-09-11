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
import { createHash } from "node:crypto";
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
    assert.equal(result.request_examined, false, name);
    assert.equal(result.caller_evidence_weighed, false, name);
    assert.equal(result.authority_established, false, name);
    assert.equal(result.state_holder_is_caller_supplied, false, name);
    assert.equal(result.model_judgment_weighed, false, name);
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
  assert.equal(described.request_examined, false);
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
  // THE TEXT IS PINNED BY DIGEST AND HELD IN THE SOURCE, not carried on the
  // surface. The first finding of the fourth re-review sweeps every string leaf,
  // and Joe's settled answers carry `present` and `complete` because that is what
  // he wrote; this slice may not reword them and cannot resolve them against the
  // store, so it pins them instead. The digest is checked against the verbatim
  // text in the source comment above V5_R01_SETTLED_DECISIONS.
  const VERBATIM = {
    "Q009.D1": "I dont want to involve dell in the adoption. i prefer to validate it myself"
      + " and present it to him as a usable product. reason being - he is not gong to sit at the"
      + " desk and do these things the way i will. what would end up happening is each slice"
      + " would be delayed for days longer while i wait on him to complete validation. much more"
      + " effective that i work out the kinks and give him the final version. I am smart enough"
      + " to imagine whether he can navigate bc i know him well enough",
    "Q010.D1": "Yes, in the future there will be periods of time where i take vacation and he"
      + " will need to be able to use the system. however, for now i dont want to sacrifice speed"
      + " on the roll out or any other qualities or capabilities for this. we can work this"
      + " concept into the design later in the build if it helps",
  };
  for (const id of V5_R01_DECISIONS_WITH_SETTLED_TEXT) {
    const digest = r01.V5_R01_SETTLED_DECISIONS[id].settled_answer_digest;
    assert.match(digest, /^[0-9a-f]{64}$/, id);
    assert.equal(digest, createHash("sha256").update(VERBATIM[id], "utf8").digest("hex"),
      `${id} pins a digest that is not the verbatim settled answer in the source`);
    // Not carried: the verbatim string itself is not on the surface at all.
    assert.equal("settled_answer" in r01.V5_R01_SETTLED_DECISIONS[id], false, id);
  }
  // Q010's recommendation is the register's own wording and is pinned the same way.
  assert.equal(r01.V5_R01_SETTLED_DECISIONS["Q010.D1"].recommendation_digest,
    createHash("sha256").update("No production capability should depend on your laptop, memory,"
      + " private prompt habits, or ability to interpret raw logs. Dell may not be able to develop"
      + " DoctorCRE, but he must be able to operate it, recognize failure, and avoid making damage"
      + " worse.", "utf8").digest("hex"));
  // NOT VACUOUS: a digest of anything else would not match.
  assert.notEqual(r01.V5_R01_SETTLED_DECISIONS["Q009.D1"].settled_answer_digest,
    createHash("sha256").update(`${VERBATIM["Q009.D1"]} `, "utf8").digest("hex"));
  for (const id of V5_R01_DECISIONS_WITHOUT_SETTLED_TEXT) {
    // Not paraphrased: there is no settled text and no digest claiming one.
    assert.equal("settled_answer" in r01.V5_R01_SETTLED_DECISIONS[id], false, id);
    assert.equal("settled_answer_digest" in r01.V5_R01_SETTLED_DECISIONS[id], false, id);
    assert.equal(typeof r01.V5_R01_SETTLED_DECISIONS[id].why_not, "string", id);
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

const HEDGE_PREFIX = /^would_/i;
const HEDGE_INFIX = "_if_authoritative";

/**
 * CASE-INSENSITIVE, WHICH IS THE FOURTH RE-REVIEW'S SECOND FINDING.
 *
 * `HEDGE_PREFIX` had no `i` flag and `_if_authoritative` was matched with a
 * case-sensitive `includes`, so the reviewer walked `Would_allow` and
 * `x_IF_AUTHORITATIVE` straight past both. A hedged verdict is hedged whatever
 * its capitalisation; this is the only place either form is tested, and both
 * probes are in the swept shapes below so the guard is exercised rather than
 * merely written.
 */
function isHedged(text) {
  const lowered = String(text).toLowerCase();
  return HEDGE_PREFIX.test(lowered) || lowered.includes(HEDGE_INFIX);
}

/**
 * THE EXPORT-NAME HALF OF P5, THROUGH THE SAME MATCHER — the fifth re-review's
 * second finding.
 *
 * `isHedged` was made case-insensitive for values and for keys, and the export-NAME
 * check standing beside it was left as `HEDGE_PREFIX.test(name) ||
 * name.includes(HEDGE_INFIX)`: a case-SENSITIVE infix test, so an export called
 * `x_IF_AUTHORITATIVE` answered `false` to the one question that guard exists to
 * ask. There is one matcher for all three positions now, it lower-cases both
 * sides, and the two probes the reviewer used run THROUGH it rather than past it.
 */
function hedgedName(name) {
  return isHedged(String(name));
}

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
  // BOTH SIDES ARE LOWER-CASED. The token side is a lower-case literal today, so
  // this changes no verdict; it is here because the case-sensitive half of a
  // two-sided comparison is exactly what went wrong in `hedgedName` above.
  return String(text).toLowerCase().includes(String(token).toLowerCase());
}

/**
 * A QUOTATION IS RESOLVED AGAINST ITS OWNER, NOT RECOGNISED BY ITS SHAPE.
 *
 * THE FOURTH RE-REVIEW'S THIRD FINDING. The old rule was that any two-string
 * `{ quoted_from, identifier }` object was a quotation, after which `identifier`
 * was skipped unconditionally — so the reviewer minted
 * `{ quoted_from: "carr:design-basis-decision-register", identifier:
 * "read_name_minted_by_this_slice" }`, a name this slice invented wearing a
 * quotation's costume, and the sweep looked away from it. A shape anyone can
 * type is not a verification.
 *
 * THE RULE NOW HAS TWO CONDITIONS AND BOTH ARE MECHANICAL.
 *   (1) `quoted_from` must be a key of QUOTATION_OWNERS — a CLOSED allowlist of
 *       modules in this repository, not a list of names a record may claim.
 *   (2) `identifier` must actually be present in that module's namespace, which
 *       this test resolves by looking it up. A name the owner does not export is
 *       not a quotation of the owner, so it is swept like any other string.
 * Fail either and the value is an ordinary object: its `identifier` is swept for
 * every privileged token, which is what the probe test below asserts.
 *
 * WHAT THIS COST THE SLICE, and it is the deliberate second pass the correction
 * asked for. Under the old shape rule there were two owners. The design-basis
 * register is not a module in this repository and nothing it owns can be
 * resolved here, so its targets and acceptance children could never satisfy
 * condition (1) — they are provenance in a source comment now rather than
 * strings on a surface claiming a verification this slice cannot perform.
 *
 * THE SECOND PASS ASKED WHETHER ANY OF THIS IS STILL NEEDED, and the answer was
 * measured rather than argued: running the sweep with the skip deleted outright
 * reports exactly ONE distinct string, `evaluateReadContinuity`, S01's own export
 * standing as the s01_seam of three drill faults. Three quotations survive, the
 * other two carry no privileged token and would not notice the deletion, and a
 * test below pins both facts. So the machinery did not survive because it was
 * worth hardening. It survived because deleting it would force this slice either
 * to misquote S01 or to stop naming the seam that defines a fault's behaviour,
 * which is the one thing the standing rule requires an honestly deferred fault
 * to say.
 */
const QUOTATION_OWNERS = new Map([[vocabulary.V5_R01_S01_MODULE, boundaries]]);

function resolvesInOwner(quoted_from, identifier) {
  const owner = QUOTATION_OWNERS.get(quoted_from);
  if (owner === undefined) return false;
  return Object.hasOwn(owner, identifier) || identifier in owner;
}

function isQuotation(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === 2
    && typeof value.quoted_from === "string" && typeof value.identifier === "string"
    && resolvesInOwner(value.quoted_from, value.identifier);
}

const ANSWER_POSITIONS = Object.freeze([
  "answer", "status", "decision", "reason_id", "verdict", "outcome", "classification",
]);

// ---------------------------------------------------------------------------
// THE DECISION PROCEDURE, as ordered questions, so a second reader reaches the
// same verdict on the same value without sharing anyone's taste.
//
//   P1 STRING LEAF — ANY string this slice's surface carries, at any depth,
//      that CONTAINS the token. Whitespace is not a licence: the first finding of
//      the fourth re-review was that this test read only whitespace-free strings,
//      which made every sentence in the slice exempt and let
//      `V5_R01_ONBOARDING_STEPS[2].plain` hand `read` to a consumer. Prose is
//      swept exactly as hard as a code is now, and the eleven sentences that
//      failed were reworded rather than excused.
//
//   P2 KEY — an object key at any depth CONTAINING the token, WHATEVER ITS
//      VALUE'S TYPE. It used to fire only on a `true`, so `acceptance_hook` — a
//      key carrying `ok`, over an object — was never looked at. `verified: true`
//      was always a finding; `verified_at: "2026-09-07"` is one now too, and so
//      is the key over a nested record. That is why `request_read: false`, the
//      refusal-report shape this slice is built out of, is `request_examined:
//      false` now: the claim is unchanged and the word it said is gone.
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
//      hand back: one registered code, one fixed message, no detail, no cause.
//
//   P5 HEDGED VERDICT — any key or string, at any depth, beginning `would_` or
//      containing `_if_authoritative`, IN ANY CASE. Its own test, because it is a
//      form rather than a word, and case-insensitive because the third finding
//      of the fourth re-review walked `Would_allow` and `x_IF_AUTHORITATIVE`
//      past a case-sensitive one.
//
// THERE IS ONE SKIP LEFT IN THE WHOLE PROCEDURE and it is a resolution rather
// than an exemption: a `{ quoted_from, identifier }` object whose owner is a
// module in the closed QUOTATION_OWNERS allowlist AND whose identifier is really
// present in that module's namespace. One string in this slice qualifies. The
// marker sweep below still covers the prose P1 now reads, for the separate
// property it always proved: no caller string of any kind reaches any returned
// value from any export, so a privileged word in a returned sentence is the
// module's own English and cannot be a caller's smuggled answer.
// ---------------------------------------------------------------------------

/**
 * Every P1/P2/P5 finding inside one value, with the path that produced it.
 *
 * EVERY STRING LEAF AND EVERY KEY, WHICH IS THE FOURTH RE-REVIEW'S FIRST
 * FINDING. The previous walker had two escapes and the reviewer used both. P1
 * read only whitespace-free strings, so prose was exempt and
 * `V5_R01_ONBOARDING_STEPS[2].plain` carried `read` to a consumer; P2 read only
 * keys whose value was boolean `true`, so `acceptance_hook` — a key carrying
 * `ok` over an object — was never looked at. Both escapes are gone: a string is
 * swept wherever it sits and a key is swept whatever its value's type.
 *
 * WHAT THAT COST, because it is the honest measure of the finding. It was
 * twenty-nine findings on this slice's own surface, and every one of them was
 * fixed rather than excused — eleven sentences reworded (`prompt habits` is
 * `typed-instruction habits`, `misreported as healthy` is `misreported as normal
 * service`, `merely broken` is `merely damaged`, `Look at what the system read`
 * is `Review what the system saw`), the `acceptance_hook` key removed with the
 * unverifiable quotations it held, and three verbatim strings this slice may not
 * reword — Joe's two settled answers and the register's recommendation — moved
 * off the surface into a source comment with a SHA-256 pinning each. The only
 * skip left in this walker is a quotation resolved against a real module.
 */
function findingsIn(value, token, at = "$", found = []) {
  if (typeof value === "string") {
    if (containsToken(value, token)) {
      found.push(`${at} = ${JSON.stringify(value).slice(0, 80)} (P1 string leaf)`);
    }
    return found;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => findingsIn(entry, token, `${at}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    if (isQuotation(value)) {
      // Only the OWNER is swept; the quoted text belongs to that owner, and
      // `isQuotation` has already resolved the identifier in that owner.
      if (containsToken(value.quoted_from, token)) {
        found.push(`${at}.quoted_from = ${value.quoted_from} (P1 string leaf)`);
      }
      return found;
    }
    for (const [key, entry] of Object.entries(value)) {
      const where = `${at}.${key}`;
      if (containsToken(key, token)) found.push(`${where} (P2 key)`);
      findingsIn(entry, token, where, found);
    }
    return found;
  }
  return found;
}

/** Every hedged key or string in a value, at any depth. Token-independent. */
function hedgesIn(value, at = "$", found = []) {
  if (typeof value === "string") {
    if (isHedged(value)) {
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
      if (isHedged(key)) found.push(`${at}.${key} (P5 hedged key)`);
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
  // THE SECOND FINDING'S OWN PROBES, in value and in key position. A sweep that
  // only tests its matcher in a unit assertion has not proved the matcher runs
  // over the real surface, so both forms are swept shapes too.
  shapes.push({ hedge: "Would_allow", other: "x_IF_AUTHORITATIVE" });
  shapes.push({ Would_allow: true, x_IF_AUTHORITATIVE: "WOULD_READ_IF_AUTHORITATIVE" });
  shapes.push("Would_allow", "x_IF_AUTHORITATIVE");
  shapes.push({ quoted_from: "allow", identifier: "allow" });
  shapes.push({}, null, undefined, "allow", 1, true, [], [{ passed: true }]);
  // THE REGISTRY'S OWN CODES, because the class export cannot be CONSTRUCTED
  // without one — the fifth re-review's third finding. `V5R01Error` takes exactly
  // one registered code, so a sweep whose every shape is an unregistered value
  // reaches the constructor's refusal and never its instance, and then reports
  // that it swept a constructor. These shapes are what make the constructed
  // surface exist at all.
  for (const code of vocabulary.V5_R01_ERROR_CODES) shapes.push(code);
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

/**
 * A CONSTRUCTED INSTANCE, READ BY NAME RATHER THAN BY SPREAD.
 *
 * THE FIFTH RE-REVIEW'S THIRD FINDING, which was two defects wearing one coat.
 * The sweep spread the instance — `{ ...new exported(...args) }` — and a spread
 * copies own ENUMERABLE properties only, so `Error.message`, which is
 * non-enumerable, was never looked at on a constructed refusal. That went
 * unnoticed because of the second half: no generated caller shape was a
 * REGISTERED code, so the constructor never once succeeded, and the sweep's own
 * "the class was constructed" assertion was satisfied by the refusal of its
 * attempt. Both halves are closed: the registry's codes are swept shapes now, and
 * every own property name is read here, the non-enumerable ones included.
 *
 * `stack` is recorded as a BOOLEAN rather than as its text, and that is about the
 * machine rather than about this slice: a stack names absolute paths, and this
 * checkout lives under a directory whose own name contains the privileged token
 * `ok`, so sweeping it for English words would report the filesystem. The stack's
 * real property — that it carries nothing a caller wrote — is asserted by name in
 * the constructor test below, against both the caller marker and the hostile
 * sentinel.
 */
function constructedSurface(instance) {
  const surface = {
    built: true,
    built_name: instance?.name ?? null,
    built_code: instance?.code ?? null,
    built_message: instance?.message ?? null,
    built_stack_is_a_string: typeof instance?.stack === "string",
    built_own_property_names: Object.getOwnPropertyNames(instance ?? {}).sort(),
  };
  for (const key of Object.getOwnPropertyNames(instance ?? {})) {
    if (key === "stack") continue;
    surface[`own_${key}`] = instance[key];
  }
  return surface;
}

/** Did the sweep ever really BUILD the class, as opposed to being refused by it? */
function constructionHappened(entries) {
  return entries.some(entry => entry.name === "V5R01Error"
    && typeof entry.output?.built?.built_code === "string");
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
            if (construct) {
              const instance = new exported(...args);
              CONSTRUCTED.push({ at: label, instance });
              output = { built: constructedSurface(instance) };
            } else {
              output = exported(...args);
            }
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

/** Every instance the sweep really BUILT, kept so the constructor test can read it. */
const CONSTRUCTED = [];

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
  // THE CLASS EXPORT REALLY WAS CONSTRUCTED, not merely refused — and this
  // assertion now fails if it was not, which is the fifth re-review's third
  // finding. The old form accepted `thrown_code !== undefined`, and a refusal's
  // `thrown_code` is `null` on a non-coded throw, so it passed on a sweep where
  // every single attempt to build the class had been rejected.
  assert.equal(isClass(vocabulary.V5R01Error), true);
  assert.equal(constructionHappened(EXPORTED_VALUES), true,
    "the error class was never successfully constructed by the sweep");

  // The matcher: a substring is a hit wherever it sits, and there is no family
  // table left for a rename to walk through.
  assert.equal(containsToken("run_passed", "pass"), true);
  assert.equal(containsToken("bypassed", "pass"), true);
  assert.equal(containsToken("already", "read"), true);
  assert.equal(containsToken("nothing_here", "pass"), false);
  // And each position really fires.
  assert.equal(findingsIn({ verdict: "allow" }, "allow").length, 1, "P1");
  assert.equal(findingsIn({ verified: true }, "verified").length, 1, "P2 over a true");
  // P2 NO LONGER NEEDS A `true`: the first finding of the fourth re-review was a
  // key carrying `ok` over an object, which the value-typed rule never read.
  assert.equal(findingsIn({ acceptance_hook: { any: "thing" } }, "ok").length, 1, "P2 over an object");
  assert.equal(findingsIn({ verified_at: "2026-09-07" }, "verified").length, 1, "P2 over a string");
  assert.equal(findingsIn({ verified: false }, "verified").length, 1, "P2 over a false");
  assert.equal(findingsIn({ verified: null }, "verified").length, 1, "P2 over a null");
  // P1 NO LONGER SKIPS PROSE, which is the same finding in its other half.
  assert.equal(findingsIn({ note: "a sentence with allow in it" }, "allow").length, 1,
    "P1 must read prose, not only whitespace-free codes");
  assert.equal(findingsIn({ steps: [{ plain: "Open a real client and read what it holds." }] },
    "read").length, 1, "P1 must read prose at depth");
  assert.equal(hedgesIn({ would_pass_if_authoritative: true }).length, 1, "P5 key");
  assert.equal(hedgesIn({ x: "would_complete" }).length, 1, "P5 string");
  // P5 IS CASE-INSENSITIVE, the second finding: both of the reviewer's probes.
  assert.equal(hedgesIn({ x: "Would_allow" }).length, 1, "P5 Would_allow");
  assert.equal(hedgesIn({ x: "x_IF_AUTHORITATIVE" }).length, 1, "P5 x_IF_AUTHORITATIVE");
  assert.equal(hedgesIn({ Would_allow: 1 }).length, 1, "P5 capitalised key");
  assert.equal(hedgesIn({ x_IF_AUTHORITATIVE: 1 }).length, 1, "P5 upper-case infix key");

  // A QUOTATION IS RESOLVED, NOT RECOGNISED — the third finding, as four cases.
  // (a) The real one: an owner on the allowlist, an identifier that owner exports.
  assert.deepEqual(findingsIn(
    { q: { quoted_from: vocabulary.V5_R01_S01_MODULE, identifier: "evaluateReadContinuity" } },
    "read"), []);
  // (b) The reviewer's probe: the right SHAPE, an owner this repository cannot
  //     resolve, and a name this slice minted. It is swept.
  assert.equal(findingsIn({ q: { quoted_from: "carr:design-basis-decision-register",
    identifier: "read_name_minted_by_this_slice" } }, "read").length, 1,
  "an unresolvable owner must not buy an exemption");
  // (c) An owner ON the allowlist, but an identifier it does not export: swept.
  assert.equal(findingsIn({ q: { quoted_from: vocabulary.V5_R01_S01_MODULE,
    identifier: "read_name_minted_by_this_slice" } }, "read").length, 1,
  "an identifier the owner does not export must not buy an exemption");
  // (d) The owner string itself is always swept, and a three-key object is not a
  //     quotation at all.
  assert.equal(findingsIn({ q: { quoted_from: "reader", identifier: "x" } }, "read").length, 1);
  assert.equal(findingsIn({ q: { quoted_from: vocabulary.V5_R01_S01_MODULE,
    identifier: "evaluateReadContinuity", extra: 1 } }, "read").length, 1,
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

/** The P5 hits in a list of swept entries — name position and value position. */
function hedgeHitsIn(entries) {
  const hits = [];
  for (const entry of entries) {
    if (hedgedName(entry.name)) hits.push(`${entry.at} (P5 export name)`);
    for (const hedge of hedgesIn(entry.output)) hits.push(`${entry.at} ${hedge}`);
  }
  return hits;
}

test("guard: no export of this slice shows a hedged verdict, by key or by value", () => {
  assert.deepEqual(hedgeHitsIn(EXPORTED_VALUES), [],
    "a would_* or _if_authoritative string reaches a consumer");

  // NOT VACUOUS IN THE NAME POSITION, which is the half that was broken. Both of
  // the reviewer's probes, as export NAMES, through the matcher itself and then
  // through the whole loop body that uses it.
  assert.equal(hedgedName("x_IF_AUTHORITATIVE"), true, "upper-case infix, as an export name");
  assert.equal(hedgedName("Would_allow"), true, "capitalised prefix, as an export name");
  assert.equal(hedgedName("WOULD_READ_IF_AUTHORITATIVE"), true);
  assert.equal(hedgedName("would_complete_run_if_authoritative"), true);
  assert.equal(hedgedName("assertR01DecisionBinding"), false, "an ordinary export name is not hedged");
  const namedProbes = [
    { at: "probe#x_IF_AUTHORITATIVE", name: "x_IF_AUTHORITATIVE", output: null },
    { at: "probe#Would_allow", name: "Would_allow", output: null },
  ];
  assert.deepEqual(hedgeHitsIn(namedProbes).map(hit => hit.split(" ")[0]),
    ["probe#x_IF_AUTHORITATIVE", "probe#Would_allow"],
    "an export named for a hedged verdict must be caught whatever its capitalisation");
  // And the exact comparison that let them through is gone: a case-sensitive
  // infix test answers `false` on the first probe, which is the defect.
  assert.equal("x_IF_AUTHORITATIVE".includes(HEDGE_INFIX), false,
    "the case-sensitive infix test still misses the probe; hedgedName must not use it");
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
  let refusals = 0;
  for (const { at, thrown } of THROWN) {
    // THERE IS EXACTLY ONE KIND OF THROW ON THIS SURFACE NOW, and that is the
    // fifth re-review's first finding carried to its end. The three fixed
    // TypeError texts this module used to raise for a malformed refusal are gone:
    // a caller who handed a hostile object to `fail`, to `assertArity` or to the
    // constructor got an UNCODED error off a public export, which is a throw a
    // consumer cannot branch on from the very surface that exists to give it one
    // shape. Every refusal is a V5R01Error carrying a registered code.
    assert.equal(thrown instanceof V5R01Error, true,
      `${at} threw ${thrown?.name}: ${thrown?.message}`);
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
  // and refuses every arity but one — with a coded refusal of its own, not a bare
  // TypeError, so the one-shape invariant above holds on the constructor too.
  const codedInvalidShape = error => error instanceof V5R01Error
    && error.code === "invalid_shape" && !(error instanceof TypeError);
  assert.throws(() => new V5R01Error("allow"), codedInvalidShape);
  assert.throws(() => new V5R01Error({ outcome: "allow" }), codedInvalidShape);
  assert.throws(() => new V5R01Error(undefined), codedInvalidShape);
  assert.throws(() => new V5R01Error(), codedInvalidShape);
  assert.throws(() => new V5R01Error("invalid_shape", "m"), codedInvalidShape);
  assert.equal(new V5R01Error("invalid_shape").code, "invalid_shape");
});

/**
 * THE CONSTRUCTOR SWEEP, ASSERTED ON THE INSTANCE IT REALLY BUILT.
 *
 * THE FIFTH RE-REVIEW'S THIRD FINDING, as four separate claims:
 *   (1) the sweep CONSTRUCTS the class, with codes taken from the registry;
 *   (2) every constructed instance is read whole — `message`, `code`, `name`,
 *       `stack`, and every own property NAME including the non-enumerable ones;
 *   (3) nothing a caller wrote is anywhere on it, stack included;
 *   (4) the non-vacuity check FAILS on a sweep that only ever provoked refusals,
 *       which is the exact state the old assertion reported as a success.
 */
test("guard: the class export is really built, and the instance is read whole", () => {
  const registered = new Set(vocabulary.V5_R01_ERROR_CODES);

  // (1) IT WAS BUILT, more than once, with more than one code.
  assert.ok(CONSTRUCTED.length > 0, "the sweep never constructed V5R01Error");
  const codes = new Set(CONSTRUCTED.map(({ instance }) => instance.code));
  assert.ok(codes.size >= 20,
    `only ${codes.size} distinct registered codes were constructed`);
  for (const code of vocabulary.V5_R01_ERROR_CODES) {
    assert.equal(codes.has(code), true, `${code} was never constructed by the sweep`);
  }

  // (2a) THE SWEPT SURFACE carries what was read, including the two properties a
  //      spread cannot see. This is asserted on the surface the sweep actually
  //      swept, not only on the instances kept beside it, because the surface is
  //      what the per-token tests read.
  const builtSurfaces = EXPORTED_VALUES
    .filter(entry => entry.name === "V5R01Error" && entry.output?.built !== undefined)
    .map(entry => entry.output.built);
  assert.ok(builtSurfaces.length > 0, "no constructed surface reached the sweep");
  for (const surface of builtSurfaces) {
    assert.deepEqual(surface.built_own_property_names, ["code", "message", "name", "stack"],
      "the swept surface does not carry every own property name of the instance");
    assert.deepEqual(Object.keys(surface).filter(key => key.startsWith("own_")).sort(),
      ["own_code", "own_message", "own_name"],
      "the swept surface lost the non-enumerable message, which is what a spread does");
    assert.equal(surface.built_stack_is_a_string, true);
    assert.equal(typeof surface.built_message, "string");
    assert.ok(surface.built_message.length > 10);
  }

  // (2) AND (3) EVERY INSTANCE, READ WHOLE.
  for (const { at, instance } of CONSTRUCTED) {
    assert.equal(instance instanceof V5R01Error, true, at);
    assert.equal(registered.has(instance.code), true, `${at} carries an unregistered code`);
    assert.equal(instance.name, "V5R01Error", at);
    assert.equal(typeof instance.message, "string", at);
    assert.equal(instance.message, new V5R01Error(instance.code).message,
      `${at} does not carry the fixed message for its code`);
    assert.equal(instance.detail, undefined, at);
    assert.equal(instance.cause, undefined, at);
    // EVERY OWN PROPERTY NAME, the non-enumerable ones included. `message` and
    // `stack` are non-enumerable on an Error, which is precisely why a spread
    // never saw them.
    assert.deepEqual(Object.getOwnPropertyNames(instance).sort(),
      ["code", "message", "name", "stack"], `${at} carries an unexpected property`);
    assert.deepEqual(Object.keys(instance).sort(), ["code", "name"],
      `${at}: the spread's blind spot is message and stack, and this names it`);
    assert.equal(typeof instance.stack, "string", at);
    assert.equal(instance.stack.includes(CALLER_MARKER), false,
      `${at} carries the caller marker on its stack`);
    assert.equal(instance.stack.includes(CALLER_SENTINEL), false,
      `${at} carries the caller sentinel on its stack`);
    for (const token of PRIVILEGED_TOKENS) {
      assert.equal(containsToken(instance.message, token), false,
        `${at} message carries "${token}"`);
      assert.equal(containsToken(instance.code, token), false,
        `${at} code carries "${token}"`);
      assert.equal(containsToken(instance.name, token), false,
        `${at} name carries "${token}"`);
    }
    assert.equal(isHedged(instance.message), false, at);
    assert.equal(isHedged(instance.code), false, at);
  }

  // (4) THE NON-VACUITY CHECK REALLY FAILS when nothing was built. Both ways it
  //     can be empty, and the old predicate beside them, which accepted the first.
  const onlyRefused = [{ at: "control", name: "V5R01Error",
    output: { thrown_name: "TypeError", thrown_code: null, thrown_message: "x" } }];
  const nothingAtAll = [];
  const builtOne = [{ at: "control", name: "V5R01Error",
    output: { built: constructedSurface(new V5R01Error("invalid_shape")) } }];
  assert.equal(constructionHappened(onlyRefused), false,
    "a thrown surface must not count as a construction");
  assert.equal(constructionHappened(nothingAtAll), false,
    "an empty sweep must not count as a construction");
  assert.equal(constructionHappened(builtOne), true, "a real construction must count");
  const oldPredicate = entries => entries.some(entry => entry.name === "V5R01Error"
    && (entry.output?.constructed !== undefined || entry.output?.thrown_code !== undefined));
  assert.equal(oldPredicate(onlyRefused), true,
    "the old assertion accepted a sweep that built nothing; this is the proof it was vacuous");
  // And `thrown_code: null` is what made it pass, because `null !== undefined`.
  assert.equal(onlyRefused[0].output.thrown_code, null);
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
    assert.equal(isHedged(message), false, code);
  }
  assert.equal(seen.size, vocabulary.V5_R01_ERROR_CODES.length);
});

/**
 * THE MARKER SWEEP — the other half of the guarantee.
 *
 * P1 reads prose now, so this is no longer what makes prose safe; it proves the
 * separate and stronger property, that nothing a caller writes reaches a
 * consumer under any name at all.
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

/**
 * A CALLER'S OWN EXCEPTION, DELIVERED TO A CONSUMER THROUGH A PUBLIC EXPORT —
 * the sixth finding of the fourth re-review, reopened by the fifth as a class.
 *
 * THE PREVIOUS ROUND FIXED ONE OPERATION AND PROBED THREE FUNCTIONS. It guarded
 * `Object.getPrototypeOf` inside `isPlainObject` and left every other read on a
 * caller's object bare — `allowed.includes(key)`, `for (const key of required)`,
 * `value.length`, `Array.isArray` on a REVOKED Proxy, and
 * `binding.decision_ids` in the pilot module — so the reviewer read
 * `Error("allow::CALLER_SENTINEL")` back off `assertArray`, `assertClosedKeys`,
 * `assertRequiredKeys`, `assertEnum`, `assertSafeText`, `assertInternalRef` and
 * `assertR01DecisionBinding`, and an uncoded native `TypeError` off a revoked
 * array Proxy. Worse, `reflecting` — the guard itself — classified what it caught
 * with `thrown instanceof V5R01Error`, which performs [[GetPrototypeOf]] on the
 * thrown value: a hostile Proxy THROWN as the error walked its author's sentence
 * out through the very code written to stop it.
 *
 * SO THE FIX IS A BOUNDARY AND THE PROBE IS A ROSTER. The module copies a caller's
 * value into plain frozen data once, inside one try/catch, and validates the copy;
 * and this test hands every hostile shape to EVERY exported function of all three
 * modules, in three argument forms and in every argument position, rather than to
 * a hand-picked three. The invariant is the one the fixed message table states:
 * one registered code, this module's own sentence, no detail, no cause, and
 * nothing the caller wrote anywhere on it — `stack` included.
 */
const CALLER_SENTINEL = "allow::CALLER_SENTINEL";

/**
 * A VALUE WHOSE OWN MACHINERY THROWS, one per operation a validator can perform.
 *
 * THE ORDERED QUESTIONS this list is built from — every way caller-controlled
 * memory can answer a read with an exception:
 *   1. a trap throws (`has`, `get`, `ownKeys`, `getPrototypeOf`,
 *      `getOwnPropertyDescriptor`), on an OBJECT target and on an ARRAY target,
 *      because `Array.isArray` follows the target and the two take different
 *      paths through the copy;
 *   2. the handle is revoked, so EVERY operation throws including `Array.isArray`
 *      — again object and array, because the array path was the uncoded one;
 *   3. an accessor throws rather than a trap: a getter on a plain object and a
 *      getter on an array INDEX, which no trap list covers;
 *   4. the thrown value is itself hostile — a Proxy that traps
 *      [[GetPrototypeOf]] — which is what makes `instanceof` unusable as a
 *      classifier inside a catch;
 *   5. the thrown value is not an object at all, or is an Error wearing a `code`,
 *      so neither "it has a code" nor "it is an Error" can be trusted either.
 */
function hostileThrownValue() {
  return new Proxy({}, {
    getPrototypeOf() { throw new Error(CALLER_SENTINEL); },
    get() { throw new Error(CALLER_SENTINEL); },
    has() { throw new Error(CALLER_SENTINEL); },
    ownKeys() { throw new Error(CALLER_SENTINEL); },
  });
}

function hostileValues() {
  const thrower = () => { throw new Error(CALLER_SENTINEL); };
  const everyTrap = {
    has: thrower, get: thrower, ownKeys: thrower, getPrototypeOf: thrower,
    getOwnPropertyDescriptor: thrower, set: thrower, deleteProperty: thrower,
  };
  // THE TWO REVOKED HANDLES ARE CHECKED BEFORE THEY ARE REVOKED, because after
  // revocation they are indistinguishable from outside — every operation throws,
  // including the one that would report the target's kind. So a swapped target
  // would be unobservable later, and the self-check is the only place it can be
  // caught. It fires at module load.
  const revokedObject = Proxy.revocable({}, {});
  if (Array.isArray(revokedObject.proxy)) {
    throw new Error("the revoked-object probe was built over an array target");
  }
  revokedObject.revoke();
  const revokedArray = Proxy.revocable([], {});
  if (!Array.isArray(revokedArray.proxy)) {
    throw new Error("the revoked-array probe was built over an object target");
  }
  revokedArray.revoke();
  const throwingIndex = [];
  Object.defineProperty(throwingIndex, 0, {
    enumerable: true, configurable: true, get: thrower,
  });
  // THE THIRD COLUMN IS THE FLOOR: must EVERY export that copies a caller value
  // refuse this shape? A proxy that throws from a trap the copy performs must be
  // refused by all of them. The ONE shape marked `false` is the object whose only
  // hostile trap is `has`, and that is a fact about the copy rather than a gap in
  // it: the copy asks `in` of an ARRAY INDEX and never of an object's key, so an
  // object's `has` trap is never consulted and has nothing to answer. It is swept
  // for a leak like everything else.
  return [
    ["object proxy, every trap throws", new Proxy({}, everyTrap), true],
    ["array proxy, every trap throws", new Proxy([], everyTrap), true],
    ["revoked proxy, object target", revokedObject.proxy, true],
    ["revoked proxy, array target", revokedArray.proxy, true],
    ["getter throws Error(sentinel)", { get decision_ids() { throw new Error(CALLER_SENTINEL); } }, true],
    ["array index getter throws Error(sentinel)", throwingIndex, true],
    ["getter throws a hostile proxy", { get decision_ids() { throw hostileThrownValue(); } }, true],
    ["ownKeys trap throws a hostile proxy", new Proxy({}, {
      ownKeys() { throw hostileThrownValue(); },
      getPrototypeOf() { throw hostileThrownValue(); },
    }), true],
    ["getPrototypeOf trap throws a string", new Proxy({}, {
      getPrototypeOf() { throw CALLER_SENTINEL; },
    }), true],
    ["getPrototypeOf trap throws a V5R01Error lookalike", new Proxy({}, {
      getPrototypeOf() {
        throw Object.assign(new Error(CALLER_SENTINEL),
          { code: "invalid_shape", name: "V5R01Error" });
      },
    }), true],
    // ONE TRAP EACH, and these are the shapes that tell a guarded read from an
    // unguarded one. A proxy that throws from EVERY trap is refused by the first
    // operation anything performs on it, so it cannot distinguish a boundary from
    // a prototype check that happens to run first. These can: each passes the
    // prototype check and breaks exactly one later read.
    ["object proxy, ownKeys throws only", new Proxy({}, { ownKeys: thrower }), true],
    ["object proxy, get throws only", new Proxy({ a: 1 }, { get: thrower }), true],
    ["object proxy, getOwnPropertyDescriptor throws only",
      new Proxy({ a: 1 }, { getOwnPropertyDescriptor: thrower }), true],
    ["array proxy, get throws only", new Proxy(["a"], { get: thrower }), true],
    ["array proxy, has throws only", new Proxy(["a"], { has: thrower }), true],
    ["object proxy, has throws only", new Proxy({ a: 1 }, { has: thrower }), false],
  ];
}

const HOSTILE_VALUES = hostileValues();

/**
 * THE ROSTER IS PINNED, because a probe list is a fixture and a fixture that
 * quietly loses an entry reports a pass it did not earn. Losing the revoked ARRAY
 * — the shape that produced the uncoded native TypeError — changed no count and no
 * assertion until this list existed.
 */
const HOSTILE_LABELS = Object.freeze([
  "object proxy, every trap throws",
  "array proxy, every trap throws",
  "revoked proxy, object target",
  "revoked proxy, array target",
  "getter throws Error(sentinel)",
  "array index getter throws Error(sentinel)",
  "getter throws a hostile proxy",
  "ownKeys trap throws a hostile proxy",
  "getPrototypeOf trap throws a string",
  "getPrototypeOf trap throws a V5R01Error lookalike",
  "object proxy, ownKeys throws only",
  "object proxy, get throws only",
  "object proxy, getOwnPropertyDescriptor throws only",
  "array proxy, get throws only",
  "array proxy, has throws only",
  "object proxy, has throws only",
]);

/**
 * THE EXPORTS THAT COPY A CALLER VALUE, so a hostile one must refuse EVERY time.
 *
 * This is the non-vacuity floor of the probe below. The evaluators are allowed to
 * answer a hostile value — they never look at a request at all, which is this
 * slice's whole claim — but a validator that takes a value and does not refuse a
 * revoked Proxy has performed a bare read on it, and that is the defect. Every
 * name here is checked against the real export list, so a rename cannot quietly
 * empty the floor.
 */
const MUST_REFUSE_A_HOSTILE_VALUE = Object.freeze([
  "V5R01Error", "assertArity", "assertArray", "assertBoolean", "assertCalendarDate",
  "assertClosedKeys", "assertEnum", "assertExactStringSet", "assertInternalRef",
  "assertObject", "assertRequiredKeys", "assertSafeText", "calendarDayOrdinal",
  "calendarWeekday", "fail",
]);

/**
 * Benign values for the argument positions the hostile one is not standing in, so
 * the hostile value reaches the copy rather than being turned away by a neighbour.
 * Position 0 is a date, 1 a path, 2 a one-element list, 3 a registered code, which
 * between them satisfy every declared signature in the slice.
 */
const BENIGN_ARGUMENTS = Object.freeze(["2026-09-07", "path", ["a"], "invalid_shape"]);

test("guard: a hostile caller value leaves one coded refusal and no caller text", () => {
  const registered = new Set(vocabulary.V5_R01_ERROR_CODES);
  const everyFunction = [
    ...Object.entries(r01), ...Object.entries(onboarding), ...Object.entries(vocabulary),
  ].filter(([, value]) => typeof value === "function");
  assert.ok(everyFunction.length >= 35,
    `only ${everyFunction.length} exported functions were probed`);
  for (const name of MUST_REFUSE_A_HOSTILE_VALUE) {
    assert.ok(everyFunction.some(([exported]) => exported === name),
      `${name} is on the refusal floor but is not exported any more`);
  }

  assert.deepEqual(HOSTILE_VALUES.map(([label]) => label), [...HOSTILE_LABELS],
    "the hostile roster lost or renamed a shape");
  assert.equal(HOSTILE_VALUES.filter(([, , mustRefuse]) => mustRefuse).length,
    HOSTILE_LABELS.length - 1, "every shape but the object has-trap is on the floor");
  // AND EACH LABEL IS CHECKED AGAINST ITS SHAPE, because a label is not a fixture.
  // Swapping an array-target proxy for an object one keeps the name, keeps the
  // count, and silently costs the probe the whole array path — which is the path
  // the uncoded native TypeError came out of. The two revoked handles cannot be
  // checked here, because revocation makes the target's kind unobservable; they
  // are checked at construction instead, in `hostileValues` above.
  for (const [label, value] of HOSTILE_VALUES) {
    if (label.startsWith("revoked ")) {
      assert.throws(() => Array.isArray(value), TypeError, `${label} is not revoked`);
      continue;
    }
    assert.equal(Array.isArray(value), label.startsWith("array "),
      `${label} does not have the target kind its name claims`);
  }

  const leaks = [];
  const refusedBy = new Map();
  const refusedPairs = new Set();
  let refusals = 0;
  let answered = 0;

  for (const [name, fn] of everyFunction) {
    const construct = isClass(fn);
    const declared = vocabulary.V5_R01_DECLARED_ARITIES[name] ?? 1;
    for (const [label, hostile] of HOSTILE_VALUES) {
      // EVERY ARGUMENT POSITION of the declared arity, plus the two trailing
      // forms that the third review's holder defect walked through.
      const forms = [[hostile], [hostile, hostile], [hostile, undefined, hostile]];
      for (let position = 0; position < Math.max(declared, 1); position += 1) {
        const args = BENIGN_ARGUMENTS.slice(0, Math.max(declared, 1));
        args[position] = hostile;
        forms.push(args);
      }
      for (const args of forms) {
        const at = `${name}(${label} @${args.length})`;
        let returned;
        let thrown;
        try {
          returned = construct ? new fn(...args) : fn(...args);
        } catch (error) {
          thrown = error;
        }

        if (thrown === undefined) {
          answered += 1;
          // A REFUSAL REPORT IS COVERED TOO: nothing the caller wrote may be in it.
          const serialized = JSON.stringify(returned ?? null);
          if (serialized.includes(CALLER_SENTINEL)) {
            leaks.push(`${at} returned the caller sentinel`);
          }
          if (containsToken(serialized, "sentinel")) {
            leaks.push(`${at} returned something of the caller's`);
          }
          continue;
        }
        refusals += 1;
        refusedBy.set(name, (refusedBy.get(name) ?? 0) + 1);
        refusedPairs.add(`${name}|${label}`);
        // ONE SHAPE OF THROW: a V5R01Error with a registered code. Not a native
        // TypeError off a revoked Proxy, not the caller's own Error, not a string.
        if (!(thrown instanceof V5R01Error)) {
          leaks.push(`${at} threw ${String(thrown?.name)}: ${String(thrown?.message).slice(0, 60)}`);
          continue;
        }
        if (!registered.has(thrown.code)) {
          leaks.push(`${at} raised the unregistered code ${String(thrown.code)}`);
        }
        if (thrown.detail !== undefined) leaks.push(`${at} carried a detail`);
        if (thrown.cause !== undefined) leaks.push(`${at} carried a cause`);
        // MESSAGE, STACK, AND EVERY OWN PROPERTY, swept for the caller's sentinel.
        const texts = [thrown.message, thrown.stack, thrown.code, thrown.name];
        for (const [key, value] of Object.entries(thrownSurface(thrown))) {
          if (typeof value === "string") texts.push(value);
          else if (value !== undefined && value !== null) texts.push(`${key}`);
        }
        for (const text of texts) {
          const asText = String(text);
          if (asText.includes(CALLER_SENTINEL)) leaks.push(`${at} carried the caller sentinel`);
          if (asText.includes("CALLER_SENTINEL")) leaks.push(`${at} carried the sentinel marker`);
        }
        // And the message — which is this module's own, so it is swept for English
        // words too — carries no privileged token. The stack is deliberately NOT
        // swept for English: it names absolute paths, and this checkout's own
        // directory name contains `ok`.
        for (const token of PRIVILEGED_TOKENS) {
          if (containsToken(thrown.message, token)) {
            leaks.push(`${at} carried the privileged token "${token}" in its message`);
          }
        }
      }
    }
  }

  assert.deepEqual(leaks, [],
    "a caller's own exception, or its text, reached a consumer of this slice");

  // THE FLOOR, PAIR BY PAIR rather than by a count: every export that copies a
  // caller value refused every shape that breaks an operation the copy performs.
  // A count can be met by refusing one shape twice; a pair cannot.
  const missing = [];
  for (const name of MUST_REFUSE_A_HOSTILE_VALUE) {
    for (const [label, , mustRefuse] of HOSTILE_VALUES) {
      if (mustRefuse && !refusedPairs.has(`${name}|${label}`)) missing.push(`${name} | ${label}`);
    }
  }
  assert.deepEqual(missing, [],
    "an export that copies a caller value accepted a shape whose own machinery throws");
  assert.ok(refusals > 400, `only ${refusals} refusals were provoked`);
  // The evaluators ANSWER rather than refuse, because they never look at the
  // request — which is the slice's claim, and means a hostile value never reaches
  // their reflection either. Both outcomes are swept above.
  assert.ok(answered > 0, "no evaluator answered; the probe proved only the refusal path");

  // NOT VACUOUS (1): the hostile values really do carry the caller's sentence out
  // of an UNGUARDED read. This is the defect, reproduced, for each read the
  // previous round left bare.
  const by = label => {
    const found = HOSTILE_VALUES.find(([name]) => name === label);
    assert.notEqual(found, undefined, `the hostile roster no longer carries "${label}"`);
    return found[1];
  };
  const unguardedReads = [
    ["Object.getPrototypeOf", () => Object.getPrototypeOf(by("object proxy, every trap throws"))],
    ["allowed.includes", () => by("object proxy, every trap throws").includes("a")],
    ["for..of iteration", () => [...by("object proxy, every trap throws")]],
    ["Object.keys", () => Object.keys(by("ownKeys trap throws a hostile proxy"))],
    ["property read", () => by("getter throws Error(sentinel)").decision_ids],
    ["array length and item", () => by("array proxy, every trap throws").length],
    ["array item read", () => by("array index getter throws Error(sentinel)")[0]],
  ];
  for (const [what, read] of unguardedReads) {
    let escaped;
    try { read(); } catch (error) { escaped = error; }
    assert.notEqual(escaped, undefined, `${what} did not throw; the probe is stale`);
  }
  // NOT VACUOUS (2): a revoked Proxy really does make `Array.isArray` throw a
  // NATIVE TypeError — the uncoded throw the reviewer read off the array path.
  assert.throws(() => Array.isArray(by("revoked proxy, array target")),
    error => error instanceof TypeError && !(error instanceof V5R01Error));
  // NOT VACUOUS (3): `instanceof` on the thrown hostile Proxy is ITSELF a throw,
  // which is why the guard classifies what it catches by identity now.
  const thrownHostile = hostileThrownValue();
  assert.throws(() => thrownHostile instanceof V5R01Error,
    error => error.message === CALLER_SENTINEL);
  // NOT VACUOUS (4): a caller's Error wearing a registered `code` and the right
  // `name` is still not one of this module's refusals, so it cannot buy a
  // pass-through.
  const lookalike = Object.assign(new Error(CALLER_SENTINEL),
    { code: "invalid_shape", name: "V5R01Error" });
  assert.equal(lookalike instanceof V5R01Error, false);
  assert.throws(() => vocabulary.assertObject(new Proxy({}, {
    getPrototypeOf() { throw lookalike; },
  }), "path"), error => error instanceof V5R01Error
    && error.code === "invalid_shape" && !error.message.includes(CALLER_SENTINEL));

  // NOT VACUOUS (5): THE READ INSIDE THE DECISION-BINDING CHECK IS BEHIND THE
  // BOUNDARY, and this is the probe that can tell. The entry takes ONE copy now,
  // so the read that can walk a caller's sentence out is the FIRST one: a getter
  // that throws on it reaches a raw `Array.isArray(binding.decision_ids)` in the
  // pilot module unguarded, and is a coded refusal here only because that first
  // read happens inside the copy.
  let bindingReads = 0;
  const firstThrower = {
    get decision_ids() {
      bindingReads += 1;
      throw new Error(CALLER_SENTINEL);
    },
  };
  assert.throws(() => r01.assertR01DecisionBinding(firstThrower),
    error => error instanceof V5R01Error && registered.has(error.code)
      && !String(error.message).includes(CALLER_SENTINEL)
      && !String(error.stack).includes(CALLER_SENTINEL));
  assert.equal(bindingReads, 1,
    `the entry point read the caller's getter ${bindingReads} times; one entry takes one copy`);

  // AND AN HONEST VALUE IS UNTOUCHED: the boundary is a boundary, not a wall.
  assert.equal(vocabulary.assertObject({ a: 1 }, "path"), undefined);
  assert.equal(vocabulary.assertClosedKeys({ a: 1 }, ["a"], "path"), undefined);
  assert.equal(vocabulary.assertRequiredKeys({ a: 1 }, ["a"], "path"), undefined);
  assert.equal(vocabulary.assertArray(["a"], "path"), undefined);
  assert.equal(vocabulary.assertEnum("a", ["a"], "path"), undefined);
  assert.equal(vocabulary.assertSafeText("a", "path"), undefined);
  assert.equal(vocabulary.assertInternalRef("deal:1", "path"), undefined);
  assert.equal(vocabulary.assertCalendarDate("2026-09-07", "path"), undefined);
  assert.equal(vocabulary.calendarDayOrdinal("2026-09-07") > 0, true);
  assert.equal(r01.assertR01DecisionBinding(
    { decision_ids: [...V5_R01_SETTLED_DECISION_IDS] }), undefined);
});

/**
 * THE COPY IS A COPY, AND IT IS NOT A LOOSENING — the other half of the boundary.
 *
 * A snapshot that accepted what the original refused would be a hole dressed as a
 * fix, so the shapes the validators used to turn away are asserted still turned
 * away: a class instance and a Date are not plain objects, a non-enumerable or
 * symbol-keyed field cannot smuggle a key past a closed-key check, a getter's
 * value is read once rather than trusted twice, and a structure too large for the
 * copy's budget is refused with a registered code rather than copied.
 */
test("guard: the caller copy narrows what the validators accept, and never widens it", () => {
  const refusal = code => error => error instanceof V5R01Error && error.code === code;
  // A class instance, a Date and a Map are not plain objects, before or after.
  class Exotic { constructor() { this.a = 1; } }
  for (const value of [new Exotic(), new Date(), new Map(), () => 1, Symbol("s")]) {
    assert.throws(() => vocabulary.assertObject(value, "path"), refusal("invalid_shape"),
      `${String(value)} was accepted as a plain object`);
  }
  // A non-enumerable own property is not a field, so it cannot be smuggled past a
  // closed-key check — and it cannot be DEMANDED by a required-key check either.
  const hidden = {};
  Object.defineProperty(hidden, "smuggled", { value: 1, enumerable: false });
  assert.equal(vocabulary.assertClosedKeys(hidden, [], "path"), undefined);
  assert.throws(() => vocabulary.assertRequiredKeys(hidden, ["smuggled"], "path"),
    refusal("missing_field"));
  // A symbol key is not a field either.
  assert.equal(vocabulary.assertClosedKeys({ [Symbol("k")]: 1 }, [], "path"), undefined);
  // A GETTER IS READ ONCE PER VALIDATOR, so a value cannot answer one way to a
  // shape check and another way to the comparison that follows it inside the same
  // check. The honest measurement: the copy is taken, the reads stop, and the
  // verdict is drawn from the copy.
  let reads = 0;
  const settling = {
    get decision_ids() {
      reads += 1;
      return reads === 1 ? [...V5_R01_SETTLED_DECISION_IDS] : 1;
    },
  };
  assert.equal(vocabulary.assertExactStringSet(settling, "decision_ids",
    V5_R01_SETTLED_DECISION_IDS, "decision_binding_mismatch"), undefined);
  assert.equal(reads, 1, "the validator read the caller's getter more than once");
  // AND THE PUBLIC ENTRY POINT TAKES THAT COPY ONCE. This assertion was the
  // sixth re-review's first finding written down as an expectation: it used to
  // require `reads > 1` — that the entry RE-COPY — which is the multi-snapshot
  // bypass stated as a requirement. A getter that answers differently each time
  // is read once here and refused on what that one read returned.
  reads = 0;
  assert.equal(r01.assertR01DecisionBinding(settling), undefined);
  assert.equal(reads, 1,
    `the entry point copied the caller's object ${reads} times; a composite takes one copy`);
  // AND THE VERDICT IS THE ONE OBSERVED VALUE'S. Wound forward so the first and
  // only read returns the getter's SECOND answer, the same object is refused —
  // which is what "the verdict is drawn from the copy" means, and what four
  // independent copies of four different answers could never say.
  reads = 1;
  assert.throws(() => r01.assertR01DecisionBinding(settling), refusal("invalid_shape"));
  assert.equal(reads, 2, "the second call read the getter more than once");
  // A PROTOTYPE-BEARING object is not a plain object, so an inherited field can
  // never stand in for an own one.
  const inherited = Object.create({ decision_ids: [...V5_R01_SETTLED_DECISION_IDS] });
  assert.throws(() => r01.assertR01DecisionBinding(inherited), refusal("invalid_shape"));
  // And a NULL-prototype object carrying the real set is accepted, so it is the
  // prototype above that refused rather than the copy's own bare prototype.
  const bare = Object.create(null);
  bare.decision_ids = [...V5_R01_SETTLED_DECISION_IDS];
  assert.equal(r01.assertR01DecisionBinding(bare), undefined);
  // And `__proto__` is an ordinary key on the copy rather than a setter the copy
  // inherited, so a caller cannot reshape the copy by naming one.
  assert.throws(() => vocabulary.assertClosedKeys(JSON.parse('{"__proto__":{"a":1}}'), [], "path"),
    refusal("unknown_field"));
  // A structure past the copy's budget is a registered refusal, not a hang and not
  // a native RangeError.
  const wide = {};
  for (let index = 0; index < 40000; index += 1) wide[`k${index}`] = index;
  assert.throws(() => vocabulary.assertObject(wide, "path"), refusal("too_many_entries"));
  // PAST THE DEPTH CAP a value becomes opaque rather than trusted. It cannot
  // refuse a field anything actually reads, because every validator is handed the
  // value it checks as its own argument, at depth zero.
  let nested = { leaf: "deep" };
  for (let index = 0; index < 12; index += 1) nested = { down: nested };
  assert.equal(vocabulary.assertClosedKeys(nested, ["down"], "path"), undefined);
  assert.equal(vocabulary.assertSafeText("deep", "path"), undefined);
  // A cycle terminates at the depth cap instead of recursing for ever.
  const cyclic = {};
  cyclic.self = cyclic;
  assert.equal(vocabulary.assertClosedKeys(cyclic, ["self"], "path"), undefined);
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

test("guard: every quotation resolves in an allowlisted owner module", () => {
  const quotations = [];
  for (const entry of EXPORTED_VALUES) quotations.push(...everyQuotation(entry.output, entry.at));
  assert.ok(quotations.length > 0, "no quotation was found; the skip rule would be dead code");

  // THE ALLOWLIST IS CLOSED AND IT HAS ONE ENTRY. The design-basis register was
  // the second, and it left with the quotations it could not verify.
  assert.deepEqual([...QUOTATION_OWNERS.keys()], [vocabulary.V5_R01_S01_MODULE]);

  const owners = new Set();
  for (const { at, quotation } of quotations) {
    assert.equal(QUOTATION_OWNERS.has(quotation.quoted_from), true,
      `${at} names an owner that is not on the closed allowlist`);
    assert.equal(quotation.quoted_from.startsWith("v5-r01"), false,
      `${at} quotes this slice, which is not a quotation at all`);
    // RESOLVED, not asserted: the identifier is really exported by that module.
    assert.equal(resolvesInOwner(quotation.quoted_from, quotation.identifier), true,
      `${at} quotes "${quotation.identifier}", which its owner does not export`);
    // A quotation is never the answer, the status, the decision or the reason.
    for (const position of ANSWER_POSITIONS) {
      assert.equal(at.endsWith(`.${position}`), false, `${at} is an answer position`);
    }
    owners.add(quotation.quoted_from);
  }
  assert.deepEqual([...owners], [vocabulary.V5_R01_S01_MODULE]);

  // A quoted identifier is never an object KEY anywhere on this surface.
  const quotedTexts = new Set(quotations.map(entry => entry.quotation.identifier));
  for (const entry of EXPORTED_VALUES) {
    for (const key of allKeys(entry.output)) {
      assert.equal(quotedTexts.has(key), false, `${entry.at} uses a quoted identifier as a key`);
    }
  }
});

test("guard: the exemption covers exactly one string, and deleting it would cost the seam", () => {
  // THE DELIBERATE SECOND PASS, MEASURED RATHER THAN ARGUED. The correction asked
  // whether any exemption machinery is still needed once the closed union is
  // pinned, and preferred deleting it to hardening it. Run with the skip deleted,
  // the sweep reports exactly one distinct string: `evaluateReadContinuity`,
  // S01's own export, standing as the s01_seam of three drill faults.
  const quoted = new Set();
  for (const entry of EXPORTED_VALUES) {
    for (const { quotation } of everyQuotation(entry.output, entry.at)) {
      quoted.add(`${quotation.quoted_from}#${quotation.identifier}`);
    }
  }
  const s01 = vocabulary.V5_R01_S01_MODULE;
  assert.deepEqual([...quoted].sort(), [
    `${s01}#evaluateActorAuthority`,
    `${s01}#evaluateLocalPlatform`,
    `${s01}#evaluateReadContinuity`,
  ], "the quotation set grew; every new entry needs its own review");

  // OF THOSE THREE, THE SKIP ONLY SAVES ONE. The other two carry no privileged
  // token at all and would survive the machinery's deletion untouched, which is
  // what answers the second pass: the whole exemption exists for
  // `evaluateReadContinuity`, and the cost of deleting it is that three drill
  // faults stop naming the S01 seam that defines their behaviour.
  const loadBearing = [...quoted].filter(entry =>
    PRIVILEGED_TOKENS.some(token => containsToken(entry.split("#")[1], token)));
  assert.deepEqual(loadBearing, [`${s01}#evaluateReadContinuity`]);
  assert.equal(findingsIn({ plain: "evaluateReadContinuity" }, "read").length, 1);
  assert.deepEqual(findingsIn({ plain: "evaluateLocalPlatform" }, "read"), []);
});

test("guard: a name this slice minted in a quotation's shape is swept, not skipped", () => {
  // THE REVIEWER'S OWN PROBE, run as a test rather than answered in prose. Under
  // the shape rule this object qualified as a quotation and its `read` was never
  // looked at. It is swept now on every privileged token it carries, and so is
  // the same probe naming the one owner that IS on the allowlist.
  const minted = {
    quoted_from: vocabulary.V5_R01_DESIGN_BASIS_REGISTER,
    identifier: "read_name_minted_by_this_slice",
  };
  assert.equal(isQuotation(minted), false, "an unresolvable owner still passed as a quotation");
  assert.equal(findingsIn({ probe: minted }, "read").length, 1);

  const mintedUnderARealOwner = {
    quoted_from: vocabulary.V5_R01_S01_MODULE,
    identifier: "read_name_minted_by_this_slice",
  };
  assert.equal(isQuotation(mintedUnderARealOwner), false,
    "an identifier the owner does not export still passed as a quotation");
  assert.equal(findingsIn({ probe: mintedUnderARealOwner }, "read").length, 1);

  // Every privileged token, in the same costume, under both owners and a third.
  for (const token of PRIVILEGED_TOKENS) {
    for (const owner of [vocabulary.V5_R01_DESIGN_BASIS_REGISTER, vocabulary.V5_R01_S01_MODULE,
      "carr:some-other-register"]) {
      const probe = { quoted_from: owner, identifier: `${token}_minted_here` };
      assert.ok(findingsIn({ probe }, token).length >= 1,
        `a minted "${token}" quoted from ${owner} was skipped`);
    }
  }
  // AND THE REAL ONE STILL RESOLVES, so this is a verification and not a ban.
  assert.equal(isQuotation({ quoted_from: vocabulary.V5_R01_S01_MODULE,
    identifier: "evaluateReadContinuity" }), true);
  assert.equal(typeof boundaries.evaluateReadContinuity, "function");
});

test("guard: every S01 seam quotation is a real S01 export, so a misquotation fails", () => {
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

/**
 * NO FILTER. That is the fourth re-review's fourth finding.
 *
 * This list used to exclude `fail` and `assertArity` by name, and the
 * completeness test below then reported that every public function declared its
 * arity — of a roster it had first trimmed to fit. An independent enumeration of
 * the three namespaces reported both as undeclared, which is what a completeness
 * test is supposed to do. Both are ordinary exports of this slice, both are on
 * `V5_R01_DECLARED_ARITIES` now, and both refuse an extra argument with the
 * registered code that roster generates.
 */
const PUBLIC_FUNCTIONS = [
  ...Object.entries(r01), ...Object.entries(onboarding), ...Object.entries(vocabulary),
].filter(([, value]) => typeof value === "function" && !isClass(value));

test("guard: every public function of this slice declares its arity", () => {
  const undeclared = PUBLIC_FUNCTIONS.map(([name]) => name)
    .filter(name => !(name in DECLARED_ARITIES));
  assert.deepEqual(undeclared, [], "a public function has no declared arity");
  // THE TWO THE OLD FILTER HID, named so a returning filter fails here first.
  assert.equal(DECLARED_ARITIES.fail, 1);
  assert.equal(DECLARED_ARITIES.assertArity, 3);
  for (const name of ["fail", "assertArity"]) {
    assert.ok(PUBLIC_FUNCTIONS.some(([exported]) => exported === name),
      `${name} was filtered out of the roster again`);
  }
  // And the roster is enumerated independently of the module's own list, so a
  // name the module forgot to declare is caught rather than matched to itself.
  const independent = [r01, onboarding, vocabulary].flatMap(surface =>
    Object.entries(surface).filter(([, value]) => typeof value === "function" && !isClass(value))
      .map(([name]) => name));
  assert.deepEqual([...new Set(independent)].sort(), Object.keys(DECLARED_ARITIES).sort());
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
  // `fail` AND `assertArity` ARE ON THE ROSTER NOW, the fourth re-review's fourth
  // finding, so they refuse an extra argument with the registered code the roster
  // generates rather than with a bare TypeError — the same boundary every other
  // export holds, in the same shape a consumer can branch on.
  assert.throws(() => vocabulary.fail("invalid_shape", "extra"),
    error => error instanceof V5R01Error && error.code === "fail_takes_no_extra_argument");
  assert.throws(() => vocabulary.assertArity([], 0, "evaluatePilotDay", "extra"),
    error => error instanceof V5R01Error && error.code === "assertArity_takes_no_extra_argument");
  // AND A WRONG CALL IS A CODED REFUSAL TOO, not a bare TypeError — the fifth
  // re-review's first finding at its last three sites. `fail` with no code,
  // `assertArity` with a name off the roster, and `assertArity` with something
  // that is not an argument list each used to raise this module's own fixed
  // TypeError text, which is a throw a consumer cannot branch on; they raise
  // `invalid_shape` now, so EVERY throw out of this slice carries a registered
  // code.
  const codedInvalidShape = error => error instanceof V5R01Error
    && error.code === "invalid_shape" && !(error instanceof TypeError);
  assert.throws(() => vocabulary.fail(), codedInvalidShape);
  assert.throws(() => vocabulary.assertArity([], 0, "not_a_guarded_name"), codedInvalidShape);
  assert.throws(() => vocabulary.assertArity(undefined, 0, "evaluatePilotDay"), codedInvalidShape);
  // AND THE DECLARED ARITY IS STILL ACCEPTED by both, so this is a boundary.
  assert.throws(() => vocabulary.fail("invalid_shape"),
    error => error instanceof V5R01Error && error.code === "invalid_shape");
  assert.equal(vocabulary.assertArity([], 0, "evaluatePilotDay"), undefined);
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

// ---------------------------------------------------------------------------
// ONE SNAPSHOT PER ENTRY, MEASURED RATHER THAN ARRANGED.
// ---------------------------------------------------------------------------

/**
 * THE SIXTH RE-REVIEW'S FIRST FINDING, AND THE INSTRUMENT THAT CATCHES IT.
 *
 * `assertR01DecisionBinding` used to hand the caller's own object to four
 * validators in turn, each of which copied it independently. The reviewer built a
 * Proxy that answered `ownKeys` with nothing for the first two copies and with
 * the real decision set for the last two: every check passed against the copy IT
 * held, the composite returned successfully, and NO SINGLE OBSERVED OBJECT had
 * ever satisfied all four.
 *
 * A test that only re-ran the reviewer's chameleon would be a test of one shape.
 * The property is a class property, so it is measured as one: a counting Proxy
 * stands in every declared argument position of every exported callable of all
 * three modules, and the traps that READ it — `ownKeys`, and `get` per key — are
 * counted per call and checked against a declared table.
 *
 * WHY A TABLE RATHER THAN "ALWAYS ONE". Two exported shapes honestly read
 * nothing, and a blanket "exactly one" would have to be weakened to accommodate
 * them, which is how a real second copy would later slip through:
 *
 *   THE EVALUATORS never look at a request at all — that is this slice's whole
 *   claim, proved separately by the hostile-value sweep — so their declared
 *   position is 0 and a 1 there is a regression in the opposite direction.
 *
 *   `assertArity` POSITION 0 is the rest tail of a function that has promised not
 *   to examine what is in it. It reads the tail's LENGTH under `reflecting` and
 *   never traverses it, so a plain-object Proxy is not even length-read: 0.
 *
 * Every other position is 1, and the table must cover exactly the callables with
 * a declared argument, so a new export cannot be added without declaring what it
 * reads.
 */
const READS_PER_DECLARED_POSITION = Object.freeze({
  "rollout-pilot-r01.v5.js#assertR01DecisionBinding": [1],
  "rollout-pilot-r01.v5.js#evaluatePilotDay": [0],
  "rollout-pilot-r01.v5.js#evaluatePilotRun": [0],
  "rollout-pilot-r01.v5.js#evaluateRecoveryDrill": [0],
  "onboarding-flow-r01.v5.js#evaluateBetaOperability": [0],
  "onboarding-flow-r01.v5.js#evaluatePerSliceDellReview": [0],
  "onboarding-flow-r01.v5.js#onboardingProgressStatus": [0],
  "rollout-pilot-r01.vocabulary.v5.js#assertArity": [0, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertArray": [1, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertBoolean": [1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertCalendarDate": [1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertClosedKeys": [1, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertEnum": [1, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertExactStringSet": [1, 1, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertInternalRef": [1, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertObject": [1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertRequiredKeys": [1, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#assertSafeText": [1, 1, 1],
  "rollout-pilot-r01.vocabulary.v5.js#calendarDayOrdinal": [1],
  "rollout-pilot-r01.vocabulary.v5.js#calendarWeekday": [1],
  "rollout-pilot-r01.vocabulary.v5.js#fail": [1],
  // THE CLASS reads nothing off its argument: `typeof` runs no trap and a WeakMap
  // lookup is identity, so an unregistered code is refused without being read.
  // It is re-exported by both public modules, and each re-export is swept as the
  // consumer of that module sees it rather than assumed to be the same function.
  "rollout-pilot-r01.vocabulary.v5.js#V5R01Error": [0],
  "rollout-pilot-r01.v5.js#V5R01Error": [0],
  "onboarding-flow-r01.v5.js#V5R01Error": [0],
});

/** A stand-in that records every read performed on it, by trap and by key. */
function countingCallerObject() {
  const tally = { ownKeys: 0, get: [], descriptors: 0, prototypes: 0, has: 0 };
  const target = { decision_ids: [...V5_R01_SETTLED_DECISION_IDS] };
  const proxy = new Proxy(target, {
    ownKeys(on) { tally.ownKeys += 1; return Reflect.ownKeys(on); },
    get(on, key, receiver) { tally.get.push(String(key)); return Reflect.get(on, key, receiver); },
    getOwnPropertyDescriptor(on, key) {
      tally.descriptors += 1;
      return Reflect.getOwnPropertyDescriptor(on, key);
    },
    getPrototypeOf(on) { tally.prototypes += 1; return Reflect.getPrototypeOf(on); },
    has(on, key) { tally.has += 1; return Reflect.has(on, key); },
  });
  return { proxy, tally };
}

/** The benign filler for each position, so only the counted one is exotic. */
const COUNTED_BENIGN_ARGUMENTS = Object.freeze([
  { decision_ids: [...V5_R01_SETTLED_DECISION_IDS] }, "decision_ids",
  [...V5_R01_SETTLED_DECISION_IDS], "decision_binding_mismatch",
]);

function declaredArityOf(moduleName, name) {
  if (name === "V5R01Error") return 1;
  const declared = vocabulary.V5_R01_DECLARED_ARITIES[name];
  return typeof declared === "number" ? declared : null;
}

test("guard: every entry copies each caller argument exactly once, and no validator re-copies", () => {
  // THE TABLE COVERS EXACTLY THE CALLABLES THAT TAKE AN ARGUMENT, so a new export
  // cannot be added without declaring what it reads.
  const withArguments = [];
  for (const [moduleName, surface] of SWEPT_SRC_MODULES) {
    for (const [name, exported] of Object.entries(surface)) {
      if (typeof exported !== "function") continue;
      const arity = declaredArityOf(moduleName, name);
      assert.notEqual(arity, null, `${moduleName}#${name} declares no arity`);
      if (arity > 0) withArguments.push(`${moduleName}#${name}`);
    }
  }
  assert.deepEqual(withArguments.sort(), Object.keys(READS_PER_DECLARED_POSITION).sort(),
    "an export that takes an argument is missing from the read table, or the table names one that is gone");

  const wrong = [];
  for (const [moduleName, surface] of SWEPT_SRC_MODULES) {
    for (const [name, exported] of Object.entries(surface)) {
      if (typeof exported !== "function") continue;
      const at = `${moduleName}#${name}`;
      const expected = READS_PER_DECLARED_POSITION[at];
      if (expected === undefined) continue;
      const construct = isClass(exported);
      for (let position = 0; position < expected.length; position += 1) {
        const { proxy, tally } = countingCallerObject();
        const args = COUNTED_BENIGN_ARGUMENTS.slice(0, expected.length);
        args[position] = proxy;
        try {
          if (construct) new exported(...args); else exported(...args);
        } catch (thrown) {
          // A refusal is an ordinary outcome here; the reads are what is measured.
          assert.equal(thrown instanceof V5R01Error, true,
            `${at} argument ${position} raised something this slice did not write`);
        }
        if (tally.ownKeys !== expected[position]) {
          wrong.push(`${at} argument ${position}: ownKeys read ${tally.ownKeys} times, expected ${expected[position]}`);
        }
        if (tally.get.length !== expected[position]) {
          wrong.push(`${at} argument ${position}: get read ${tally.get.length} times, expected ${expected[position]}`);
        }
        // THE CLASS INVARIANT, stated separately from the table: no caller object
        // is observed twice, and no key of it is read twice, whatever the row says.
        assert.ok(tally.ownKeys <= 1, `${at} argument ${position} copied more than once`);
        assert.deepEqual([...new Set(tally.get)].sort(), [...tally.get].sort(),
          `${at} argument ${position} read one key more than once`);
      }
    }
  }
  assert.deepEqual(wrong, [], "an entry did not take exactly one copy of its caller argument");
});

test("guard: the chameleon that passed the four-copy composite is refused by the one-copy entry", () => {
  // THE REVIEWER'S OWN SHAPE. It answers `ownKeys` with nothing for the first two
  // copies and with the real decision set for the last two, so under a composite
  // that copied four times every check passed against a different object and
  // `assertR01DecisionBinding` returned successfully with `ownKeys_reads: 4`.
  let copies = 0;
  const settled = Object.freeze([...V5_R01_SETTLED_DECISION_IDS]);
  const chameleon = new Proxy({}, {
    ownKeys() { copies += 1; return copies > 2 ? ["decision_ids"] : []; },
    getOwnPropertyDescriptor() {
      return { value: [...settled], writable: true, enumerable: true, configurable: true };
    },
    get() { return [...settled]; },
    getPrototypeOf() { return Object.prototype; },
  });

  assert.throws(() => assertR01DecisionBinding(chameleon),
    error => error instanceof V5R01Error
      && vocabulary.V5_R01_ERROR_CODES.includes(error.code));
  assert.equal(copies, 1,
    `the entry took ${copies} copies; the chameleon needs more than one to work`);

  // NOT VACUOUS: the same Proxy, asked four times the way the old composite asked
  // it, really does hand out a valid binding on the copies after the second. This
  // is the bypass reproduced, so the assertion above is a fact about the fix.
  copies = 0;
  assert.deepEqual(Object.keys(chameleon), []);
  assert.deepEqual(Object.keys(chameleon), []);
  assert.deepEqual(Object.keys(chameleon), ["decision_ids"]);
  assert.deepEqual(Object.keys(chameleon), ["decision_ids"]);
  assert.equal(copies, 4);
  assert.deepEqual(chameleon.decision_ids, [...settled]);

  // AND AN HONEST BINDING STILL PASSES, read once.
  let honestCopies = 0;
  const honest = new Proxy({ decision_ids: [...settled] }, {
    ownKeys(on) { honestCopies += 1; return Reflect.ownKeys(on); },
  });
  assert.equal(assertR01DecisionBinding(honest), undefined);
  assert.equal(honestCopies, 1);
});

// ---------------------------------------------------------------------------
// A REFUSAL CARRIES NO FRAME OF THE CALLER'S.
// ---------------------------------------------------------------------------

/**
 * THE SIXTH RE-REVIEW'S SECOND FINDING. `super(...)` captures the frames that led
 * to the constructor, and those frames are the CALLER'S: the reviewer invoked
 * every export through a computed method named `allow::CALLER_SENTINEL` and read
 * that exact caller-written text back off `error.stack` on all 34 unique exported
 * callables. A function name is caller-controlled memory just as much as an
 * argument is, and the constructor test before this one checked sentinels
 * supplied as ARGUMENTS, which is a different door.
 *
 * The stack is replaced with this module's own two words now, installed as a data
 * property rather than assigned, so there is no lazy accessor left for a caller's
 * `Error.prepareStackTrace` to run through. Both of the caller's overrides are
 * exercised below anyway — they are out of scope under the 2026-09-11 amendment
 * as interpreter-level mutation, and they are cheap to cover, so they are covered
 * rather than argued about.
 */
function invokeThroughCallerFrame(exported, args, construct) {
  const caller = {
    [CALLER_SENTINEL](callee, callArgs, asConstructor) {
      return asConstructor ? new callee(...callArgs) : callee(...callArgs);
    },
  };
  return caller[CALLER_SENTINEL](exported, args, construct);
}

function callerFrameLeaks() {
  const leaks = [];
  let surfaced = 0;
  for (const [moduleName, surface] of SWEPT_SRC_MODULES) {
    for (const [name, exported] of Object.entries(surface)) {
      if (typeof exported !== "function") continue;
      const at = `${moduleName}#${name}`;
      const construct = isClass(exported);
      const argumentLists = [[], ["invalid_shape"], [{ a: 1 }, "path"],
        [{ a: 1 }, ["a"], "path"], [{ a: 1 }, "a", ["a"], "invalid_shape"]];
      for (const args of argumentLists) {
        let thrown;
        try {
          const returned = invokeThroughCallerFrame(exported, args, construct);
          if (JSON.stringify(returned ?? null).includes("CALLER_SENTINEL")) {
            leaks.push(`${at} returned the caller's frame name`);
          }
          if (construct) thrown = returned;
        } catch (caught) {
          thrown = caught;
        }
        if (thrown === undefined) continue;
        surfaced += 1;
        const parts = [String(thrown?.stack), String(thrown?.message), String(thrown?.code),
          String(thrown?.name)];
        for (const key of Object.getOwnPropertyNames(thrown ?? {})) {
          parts.push(String(thrown[key]));
        }
        for (const part of parts) {
          if (part.includes(CALLER_SENTINEL)) leaks.push(`${at} carried the caller frame sentinel`);
          if (part.includes("CALLER_SENTINEL")) leaks.push(`${at} carried the frame marker`);
        }
        if (thrown instanceof V5R01Error) {
          if (thrown.stack !== `${thrown.name}: ${thrown.message}`) {
            leaks.push(`${at} stack is not this module's own two words: ${thrown.stack}`);
          }
        }
      }
    }
  }
  return { leaks, surfaced };
}

test("guard: no refusal carries a frame of the caller's, under any stack hook", () => {
  // NOT VACUOUS FIRST: a native error raised through the same computed method
  // really does carry that method's name on its stack. This is the defect, live.
  const control = (() => {
    try {
      invokeThroughCallerFrame(() => { throw new TypeError("plain"); }, [], false);
      return null;
    } catch (caught) { return caught; }
  })();
  assert.equal(String(control.stack).includes(CALLER_SENTINEL), true,
    "a caller's frame name does not reach a native stack here, so this test proves nothing");

  const plain = callerFrameLeaks();
  assert.ok(plain.surfaced > 30, `only ${plain.surfaced} refusals were surfaced`);
  assert.deepEqual(plain.leaks, [], "a caller's own frame name reached a consumer of this slice");

  // THE CALLER'S `Error.prepareStackTrace`, which formats a lazily-read stack.
  const savedPrepare = Error.prepareStackTrace;
  const savedCapture = Error.captureStackTrace;
  const savedLimit = Error.stackTraceLimit;
  let underPrepare;
  let underCapture;
  let prepareControl;
  let captureControl;
  try {
    Error.stackTraceLimit = 100;
    Error.prepareStackTrace = () => CALLER_SENTINEL;
    prepareControl = (() => {
      try { invokeThroughCallerFrame(() => { throw new TypeError("plain"); }, [], false); return null; }
      catch (caught) { return String(caught.stack); }
    })();
    underPrepare = callerFrameLeaks();
    Error.prepareStackTrace = savedPrepare;

    Error.captureStackTrace = (holder) => { holder.stack = CALLER_SENTINEL; };
    captureControl = (() => {
      const holder = {};
      Error.captureStackTrace(holder);
      return String(holder.stack);
    })();
    underCapture = callerFrameLeaks();
  } finally {
    Error.prepareStackTrace = savedPrepare;
    Error.captureStackTrace = savedCapture;
    Error.stackTraceLimit = savedLimit;
  }

  assert.equal(prepareControl, CALLER_SENTINEL,
    "the prepareStackTrace override did not take, so that half proves nothing");
  assert.equal(captureControl, CALLER_SENTINEL,
    "the captureStackTrace override did not take, so that half proves nothing");
  assert.deepEqual(underPrepare.leaks, [],
    "a caller's prepareStackTrace reached this slice's refusals");
  assert.deepEqual(underCapture.leaks, [],
    "a caller's captureStackTrace reached this slice's refusals");
  assert.ok(underPrepare.surfaced > 30);
  assert.ok(underCapture.surfaced > 30);
});

test("guard: all 54 registered codes construct, carrying four own properties and this module's stack", () => {
  assert.equal(vocabulary.V5_R01_ERROR_CODES.length, 54,
    "the registered code set changed size; the roster or the arity table moved");
  const stacks = new Set();
  for (const code of vocabulary.V5_R01_ERROR_CODES) {
    const error = invokeThroughCallerFrame(V5R01Error, [code], true);
    assert.deepEqual(Object.getOwnPropertyNames(error).sort(),
      ["code", "message", "name", "stack"], `${code} carries an unexpected own property`);
    assert.equal(error.code, code);
    assert.equal(error.name, "V5R01Error");
    assert.equal(typeof error.message, "string");
    assert.equal(error.stack, `V5R01Error: ${error.message}`, `${code} carries a captured stack`);
    assert.equal(error.stack.includes(CALLER_SENTINEL), false);
    assert.equal(Object.isFrozen(error), true, `${code} is not frozen`);
    const descriptor = Object.getOwnPropertyDescriptor(error, "stack");
    assert.equal(Object.hasOwn(descriptor, "value"), true,
      `${code} left stack as an accessor, so a caller's hook still has a door`);
    assert.equal(descriptor.writable, false);
    assert.equal(descriptor.configurable, false);
    stacks.add(error.stack);
  }
  // Every code has its own fixed message, so every stack is distinct: a single
  // shared constant would be a message table that had collapsed.
  assert.equal(stacks.size, 54, "two codes share a stack, so two share a message");
});
