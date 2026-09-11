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
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

import * as r01 from "../src/rollout-pilot-r01.v5.js";
import {
  V5_R01_CLASSIFICATIONS,
  V5_R01_PRIVILEGED_OUTCOMES,
  collectStrings,
} from "../src/rollout-pilot-r01.vocabulary.v5.js";
import {
  breakingFailureOrigins,
  classifyPilotDayIfAuthoritative,
  classifyPilotRunIfAuthoritative,
  classifyRecoveryDrillIfAuthoritative,
  disqualifyingFailureOrigins,
} from "../src/rollout-pilot-r01.internal.v5.js";

const {
  V5R01Error, V5_R01_BREAKING_FAILURE_ORIGINS, V5_R01_DAY_CHECKS,
  V5_R01_DECISIONS_NOT_READ, V5_R01_DECISIONS_READ_IN_FULL,
  V5_R01_DISQUALIFYING_FAILURE_ORIGINS, V5_R01_DRILL_FAULTS, V5_R01_DRILL_FAULT_KEYS,
  V5_R01_DRILL_RECEIPT_FIELDS, V5_R01_EXCLUDED_FAILURE_ORIGINS, V5_R01_FAILURE_ORIGINS,
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
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// LADDER — one candidate day.
// ---------------------------------------------------------------------------

test("ladder: a complete day with no failures would count", () => {
  const verdict = classifyPilotDayIfAuthoritative(day());
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.day);
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
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[0]);
  assert.equal(verdict.disqualifies_run, true);
  assert.equal(verdict.disqualifying_origin, "unresolved_high_defect");
});

test("ladder: two of the three subjourneys is not three", () => {
  const verdict = classifyPilotDayIfAuthoritative(day({
    subjourneys_exercised: ["j1:sub-a", "j1:sub-b"],
  }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[1]);
  assert.equal(verdict.distinct_subjourneys, 2);
  assert.equal(verdict.required_subjourneys, V5_R01_J1_SUBJOURNEY_COUNT);
});

test("ladder: the same subjourney three times is one subjourney, not three", () => {
  // The boundary case the count alone gets wrong.
  const verdict = classifyPilotDayIfAuthoritative(day({
    subjourneys_exercised: ["j1:sub-a", "j1:sub-a", "j1:sub-a"],
  }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[1]);
  assert.equal(verdict.distinct_subjourneys, 1);
});

test("ladder: a walkthrough with no real business action is not a pilot day", () => {
  const verdict = classifyPilotDayIfAuthoritative(day({ real_business_actions: [] }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, V5_R01_DAY_CHECKS[2]);
});

test("ladder: every excluded origin lets a day count, every breaking origin does not", () => {
  for (const origin of V5_R01_EXCLUDED_FAILURE_ORIGINS) {
    const verdict = classifyPilotDayIfAuthoritative(day({
      failures: [failure({ observed_origin: origin })],
    }));
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.day, origin);
    assert.deepEqual(verdict.excluded_failures, ["failure:1"]);
  }
  for (const origin of V5_R01_BREAKING_FAILURE_ORIGINS) {
    const verdict = classifyPilotDayIfAuthoritative(day({
      failures: [failure({ observed_origin: origin })],
    }));
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse, origin);
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
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.breaking_origin, "log_interpretation_required");
  assert.equal(verdict.disqualifies_run, false);
});

test("ladder: a conditional exclusion the product lied through is NOT excluded", () => {
  // The clause that keeps the exclusion list from being an alibi. Same origin,
  // same day, one boolean different, opposite answer.
  const excused = classifyPilotDayIfAuthoritative(day({
    failures: [failure({ observed_origin: "third_party_provider_outage" })],
  }));
  assert.equal(excused.classification, V5_R01_CLASSIFICATIONS.day);

  for (const dishonest of [
    { product_reported_honestly: false },
    { documented_fallback_offered: false },
  ]) {
    const verdict = classifyPilotDayIfAuthoritative(day({
      failures: [failure({ observed_origin: "third_party_provider_outage", ...dishonest })],
    }));
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse, JSON.stringify(dishonest));
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
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.day);
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
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.run);
  assert.equal(verdict.longest_run, V5_R01_REQUIRED_RUN_LENGTH);
  assert.equal(verdict.first_date, "2026-09-07");
  assert.equal(verdict.last_date, "2026-09-18");
});

test("ladder: nine days is short, and the answer says how short", () => {
  const verdict = classifyPilotRunIfAuthoritative(run({
    days: TEN_BUSINESS_DAYS.slice(0, 9).map(date => day({ date })),
  }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "run_shorter_than_required");
  assert.equal(verdict.longest_run, 9);
});

test("ladder: a business day MISSING from the ledger breaks the run", () => {
  // The defect a filter-and-count implementation cannot see: Wednesday simply
  // is not there. Nine entries, spanning eleven business days, run of five.
  const days = TEN_BUSINESS_DAYS.filter(date => date !== "2026-09-09").map(date => day({ date }));
  const verdict = classifyPilotRunIfAuthoritative(run({ days }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.longest_run, 7, "two days before the hole, seven after it");
});

test("ladder: a failed day in the middle breaks the run and the run restarts after it", () => {
  const days = TEN_BUSINESS_DAYS.map(date => (date === "2026-09-09"
    ? day({ date, failures: [failure({ observed_origin: "product_defect" })] })
    : day({ date })));
  const verdict = classifyPilotRunIfAuthoritative(run({ days }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
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
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.run);
  assert.equal(verdict.longest_run, 10);
});

test("ladder: an open high defect anywhere disqualifies the whole run, not one day", () => {
  const days = TEN_BUSINESS_DAYS.map(date => (date === "2026-09-16"
    ? day({ date, high_defect_open_against_j1: true })
    : day({ date })));
  const verdict = classifyPilotRunIfAuthoritative(run({ days }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
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
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.drill);
  assert.equal(verdict.blocking_check, null);
});

test("ladder: stopping safely counts, because Q010 asks for 'avoid making damage worse'", () => {
  const verdict = classifyRecoveryDrillIfAuthoritative(drill({
    terminal_state_reached: "stopped_safely_without_making_it_worse",
  }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.drill);
});

test("ladder: getting stuck or making it worse does not", () => {
  for (const state of ["stuck", "made_it_worse"]) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({ terminal_state_reached: state }));
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse, state);
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
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "product_did_not_show_the_declared_signal");
});

test("ladder: every forbidden aid fails the drill, and the answer names which", () => {
  for (const aid of r01.V5_R01_FORBIDDEN_DRILL_AIDS) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({ aids_used: [aid] }));
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse, aid);
    assert.equal(verdict.blocking_check, "recovery_required_a_forbidden_aid");
    assert.deepEqual(verdict.forbidden_aids_used, [aid]);
  }
});

test("ladder: one person injecting, watching and recovering is a demonstration, not a drill", () => {
  const verdict = classifyRecoveryDrillIfAuthoritative(drill({
    injected_by_identity_ref: "actor:same", observer_identity_ref: "actor:same",
  }));
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "injector_and_observer_are_the_same_identity");
});

test("ladder: every registered fault has a declared signal a drill can be run against", () => {
  for (const fault of V5_R01_DRILL_FAULT_KEYS) {
    const verdict = classifyRecoveryDrillIfAuthoritative(drill({
      fault_injected: fault,
      observed_partner_visible_signal: V5_R01_DRILL_FAULTS[fault].expected_partner_visible_signal,
    }));
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.drill, fault);
  }
});

// ---------------------------------------------------------------------------
// REFUSALS. Every public evaluator, every input.
// ---------------------------------------------------------------------------

const EVALUATOR_MATRIX = [
  ["evaluatePilotDay", evaluatePilotDay,
    () => ({ date: "2026-09-07", entry: day() }), V5_R01_SEAMS.pilot_day_store.seam],
  ["evaluatePilotRun", evaluatePilotRun, () => run(), V5_R01_SEAMS.pilot_day_store.seam],
  ["evaluateRecoveryDrill", evaluateRecoveryDrill,
    () => ({ receipt: drill() }), V5_R01_SEAMS.drill_receipt_store.seam],
];

test("refusal: every evaluator answers unavailable on its most honest input", () => {
  for (const [name, evaluator, fixture, seam] of EVALUATOR_MATRIX) {
    const result = evaluator(fixture());
    assert.equal(result.decision, "unavailable", name);
    assert.equal(result.owed_seam, seam, name);
    assert.equal(result.authority_established, false, name);
    assert.equal(result.state_holder_is_caller_supplied, false, name);
    assert.equal(result.model_judgment_admitted, false, name);
    assert.equal(result.produces_acceptance, false, name);
    assert.equal(result.injects_nothing, true, name);
    assert.equal(Object.isFrozen(result), true, name);
  }
});

test("refusal: the refusal still names the check that would have blocked", () => {
  const short = evaluatePilotRun(run({ days: TEN_BUSINESS_DAYS.slice(0, 3).map(d => day({ date: d })) }));
  assert.equal(short.decision, "unavailable");
  assert.equal(short.blocking_check, "run_shorter_than_required");
  assert.equal(short.would_be_classified, V5_R01_CLASSIFICATIONS.refuse);

  const clean = evaluatePilotRun(run());
  assert.equal(clean.decision, "unavailable");
  assert.equal(clean.blocking_check, null);
  assert.equal(clean.would_be_classified, V5_R01_CLASSIFICATIONS.run,
    "a clean run is still unavailable, and says what it WOULD have been");
});

test("refusal: the run refusal names all five owed owners, not just the first", () => {
  const result = evaluatePilotRun(run());
  assert.deepEqual([...result.all_owed_seams].sort(), [
    V5_R01_SEAMS.defect_register.seam,
    V5_R01_SEAMS.independent_observer.seam,
    V5_R01_SEAMS.j1_subjourney_roster.seam,
    V5_R01_SEAMS.operating_calendar.seam,
    V5_R01_SEAMS.pilot_day_store.seam,
  ].sort());
});

test("refusal: describeRecoveryDrill describes and injects nothing", () => {
  for (const fault of V5_R01_DRILL_FAULT_KEYS) {
    const described = describeRecoveryDrill({ fault });
    assert.equal(described.this_module_injects_nothing, true, fault);
    assert.equal(described.receipt_store_exists_in_this_repository, false, fault);
    assert.equal(described.injector_must_not_be_the_observer, true, fault);
    assert.deepEqual(described.receipt_fields, [...V5_R01_DRILL_RECEIPT_FIELDS], fault);
    assert.equal(described.effects.creates_effect, false, fault);
  }
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

// ---------------------------------------------------------------------------
// GUARDS. A caller-supplied label, class string, flag or holder is never authority.
// ---------------------------------------------------------------------------

const PRIVILEGED_VALUES = new Set(V5_R01_PRIVILEGED_OUTCOMES);

test("guard: no evaluator returns a privileged outcome, on any caller-controlled input", () => {
  const inputs = [
    evaluatePilotDay({ date: "2026-09-07", entry: day() }),
    evaluatePilotDay({ date: "2026-09-07", entry: day({ high_defect_open_against_j1: true }) }),
    evaluatePilotDay({ date: "2026-09-07", entry: day({ subjourneys_exercised: [] }) }),
    evaluatePilotRun(run()),
    evaluatePilotRun(run({ days: [] })),
    evaluateRecoveryDrill({ receipt: drill() }),
    evaluateRecoveryDrill({ receipt: drill({ terminal_state_reached: "stuck" }) }),
    describeRecoveryDrill({ fault: "record_layer_unreachable" }),
    describePilotDayLedgerEntry(),
    describeFailureExclusions(),
    rolloutPilotGaps(),
  ];
  for (const result of inputs) {
    for (const value of collectStrings(result)) {
      assert.equal(PRIVILEGED_VALUES.has(value), false,
        `${result.answer ?? "a description"} returned the privileged value ${JSON.stringify(value)}`);
    }
  }
});

test("guard: stuffing privileged labels into every caller-controlled field changes nothing", () => {
  // A caller whose subjourney references are literally "pass" and "allow". Those
  // references do not reach a field a consumer decides from, and the answer is
  // still unavailable — a caller cannot label its own day into counting.
  const result = evaluatePilotDay({ date: "2026-09-07", entry: day({
    subjourneys_exercised: ["pass", "allow", "completed"],
    real_business_actions: ["deal:passed-through"],
  }) });
  const DECISION_KEYS = ["decision", "reason_id", "blocking_check", "would_be_classified", "answer"];
  assert.equal(result.decision, "unavailable");
  for (const key of DECISION_KEYS) {
    assert.equal(PRIVILEGED_VALUES.has(result[key]), false, `${result.answer}.${key}`);
  }
  assert.equal(result.would_be_classified, V5_R01_CLASSIFICATIONS.day,
    "the references are echoed nowhere and change no verdict");
});

test("guard: a caller-supplied reference that IS a privileged word throws rather than returning", () => {
  // The refusal sweep's own failure mode, and it is reachable: a refusal carries
  // WHICH failure blocked, so a caller whose failure reference is "pass" would
  // otherwise put a privileged word into a result. It throws instead.
  assert.throws(() => evaluatePilotDay({ date: "2026-09-07", entry: day({
    failures: [failure({ failure_ref: "pass", observed_origin: "product_defect" })],
  }) }), error => error instanceof V5R01Error
    && error.code === "refusal_would_leak_privileged_outcome");

  assert.throws(() => evaluateRecoveryDrill({ receipt: drill({
    observed_partner_visible_signal: "passing",
  }) }), error => error instanceof V5R01Error
    && error.code === "refusal_would_leak_privileged_outcome");
});

test("guard: no evaluator accepts a store, ledger, calendar or observer as a second argument", () => {
  const forged = {
    resolvePilotDay: () => day(), resolveCalendar: () => [], resolveObserverReceipt: () => drill(),
  };
  for (const [name, evaluator, fixture] of EVALUATOR_MATRIX) {
    assert.throws(() => evaluator(fixture(), forged),
      error => error instanceof V5R01Error && error.code.endsWith("_holder_is_not_an_argument"),
      `${name} accepted a caller-supplied holder`);
  }
});

test("guard: a holder smuggled in as a request FIELD is refused by name", () => {
  for (const field of ["pilot_day_store", "observer_receipt", "operating_calendar", "ledger_ref"]) {
    assert.throws(() => evaluatePilotRun({ ...run(), [field]: {} }),
      error => error instanceof V5R01Error
        && ["holder_field_is_not_authority", "unknown_field"].includes(error.code),
      field);
  }
});

test("guard: the public surface exports no classifier and no conditional classification", () => {
  const exported = Object.keys(r01).sort();
  assert.deepEqual(exported, [...V5_R01_PUBLIC_SURFACE].sort(),
    "the declared public surface IS the export set");
  for (const name of exported) {
    assert.equal(name.startsWith("classify"), false, `${name} is a classifier on the public surface`);
    assert.equal(name.startsWith("__"), false, `${name} is a probe on the public surface`);
    assert.equal(name.includes("Internal"), false, name);
    assert.equal(name.toLowerCase().includes("fixture"), false, name);
    assert.equal(name.toLowerCase().includes("unwired"), false, name);
  }
  assert.equal(exported.includes("V5_R01_CLASSIFICATIONS"), false);
  for (const value of Object.values(V5_R01_CLASSIFICATIONS)) {
    assert.equal(value.startsWith("would_"), true, value);
    assert.equal(PRIVILEGED_VALUES.has(value), false, value);
  }
});

test("guard: the internal classifier is unreachable through either public surface — parsed, not grepped", () => {
  // Node's own ES-module parser, via vm.SourceTextModule, which exposes the real
  // import specifiers of each source file. A regex over the text would be fooled
  // by a comment, a string or an unusual line break; this is the same parse the
  // runtime performs.
  const script = `
    import { readdirSync, readFileSync, statSync } from "node:fs";
    import path from "node:path";
    import vm from "node:vm";
    const root = process.env.R01_REPO_ROOT;
    const internal = "rollout-pilot-r01.internal.v5.js";
    const roots = ["mcp-server/src", "mcp-server/test", "control-room", "workspace", "dealroom", "tools"];
    const files = [];
    const walk = dir => {
      let entries;
      try { entries = readdirSync(dir); } catch { return; }
      for (const entry of entries) {
        if (entry === "node_modules" || entry === ".git") continue;
        const full = path.join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\\.(mjs|js)$/.test(entry)) files.push(full);
      }
    };
    for (const dir of roots) walk(path.join(root, dir));
    const importers = [];
    let parsed = 0;
    for (const file of files) {
      let record;
      try { record = new vm.SourceTextModule(readFileSync(file, "utf8"), { identifier: file }); }
      catch { continue; }
      parsed += 1;
      if (record.dependencySpecifiers.some(spec => spec.endsWith(internal)))
        importers.push(path.relative(root, file));
    }
    console.log(JSON.stringify({ importers: importers.sort(), parsed }));
  `;
  const output = execFileSync(process.execPath,
    ["--experimental-vm-modules", "--input-type=module", "--eval", script],
    { env: { ...process.env, R01_REPO_ROOT: REPO_ROOT }, encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"] });
  const { importers, parsed } = JSON.parse(output.trim().split("\n").pop());

  // The sweep is only worth anything if it actually parsed the tree.
  assert.equal(parsed > 100, true, `only ${parsed} modules parsed`);
  // Exactly four importers: the two public modules, which do not re-export the
  // classifiers, and the two suites. Anything else would be a consumer reaching
  // past a public surface for the conditional answer.
  assert.deepEqual(importers, [
    "mcp-server/src/onboarding-flow-r01.v5.js",
    "mcp-server/src/rollout-pilot-r01.v5.js",
    "mcp-server/test/onboarding-flow-r01.v5.test.mjs",
    "mcp-server/test/rollout-pilot-r01.v5.test.mjs",
  ]);
});

test("guard: the parsed export names of the public module carry no classifier", () => {
  // Same parser, now LINKED against the real files on disk, so the export names
  // come from Node resolving the module graph exactly as the runtime would.
  const script = `
    import { readFileSync } from "node:fs";
    import path from "node:path";
    import vm from "node:vm";
    const cache = new Map();
    const load = file => {
      const resolved = path.resolve(file);
      if (cache.has(resolved)) return cache.get(resolved);
      const record = new vm.SourceTextModule(readFileSync(resolved, "utf8"),
        { identifier: resolved, initializeImportMeta: meta => { meta.url = "file://" + resolved; } });
      cache.set(resolved, record);
      return record;
    };
    const linker = async (specifier, referencing) => {
      if (specifier.startsWith("node:") || !specifier.startsWith(".")) {
        const builtin = await import(specifier);
        const names = Object.keys(builtin);
        return new vm.SyntheticModule(names, function () {
          for (const name of names) this.setExport(name, builtin[name]);
        });
      }
      return load(path.resolve(path.dirname(referencing.identifier), specifier));
    };
    const entry = load(process.env.R01_PUBLIC_MODULE);
    await entry.link(linker);
    await entry.evaluate();
    console.log(JSON.stringify(Object.keys(entry.namespace).sort()));
  `;
  const output = execFileSync(process.execPath,
    ["--experimental-vm-modules", "--input-type=module", "--eval", script],
    { env: { ...process.env,
      R01_PUBLIC_MODULE: path.join(REPO_ROOT, "mcp-server/src/rollout-pilot-r01.v5.js") },
    encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  const names = JSON.parse(output.trim().split("\n").pop());

  assert.deepEqual(names, Object.keys(r01).sort(), "the parsed surface is the runtime surface");
  for (const name of names) assert.equal(name.startsWith("classify"), false, name);
  assert.equal(names.includes("evaluatePilotRun"), true);
  assert.equal(names.includes("V5_R01_CLASSIFICATIONS"), false);
});

test("guard: the vocabulary's failure-origin sets agree with an independent derivation", () => {
  assert.deepEqual(breakingFailureOrigins(), [...V5_R01_BREAKING_FAILURE_ORIGINS].sort());
  assert.deepEqual(disqualifyingFailureOrigins(), [...V5_R01_DISQUALIFYING_FAILURE_ORIGINS].sort());
  // And the three sets partition the registry: excluded ∪ breaking = all, disjointly.
  const excluded = new Set(V5_R01_EXCLUDED_FAILURE_ORIGINS);
  const breaking = new Set(V5_R01_BREAKING_FAILURE_ORIGINS);
  assert.equal(excluded.size + breaking.size, V5_R01_FAILURE_ORIGIN_KEYS.length);
  for (const origin of V5_R01_FAILURE_ORIGIN_KEYS) {
    assert.equal(excluded.has(origin) !== breaking.has(origin), true, origin);
  }
  for (const origin of V5_R01_DISQUALIFYING_FAILURE_ORIGINS) {
    assert.equal(breaking.has(origin), true, `${origin} disqualifies but is not breaking`);
  }
});

test("guard: every exclusion carries a stated basis a reader can argue with", () => {
  const described = describeFailureExclusions();
  assert.equal(described.origins.length, V5_R01_FAILURE_ORIGIN_KEYS.length);
  for (const entry of described.origins) {
    assert.equal(entry.basis.length > 40, true, entry.origin);
    if (entry.excluded) assert.equal(entry.disqualifies_run, false, entry.origin);
  }
  assert.equal(described.attribution_seam_exists_in_this_repository, false);
  assert.equal(described.attributed_by.includes("never a caller"), true);
});
