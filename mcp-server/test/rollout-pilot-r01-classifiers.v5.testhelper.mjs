// V5-R01 DETERMINISTIC CLASSIFIERS — THE TEST-ONLY ENTRY.
//
// READ THIS FIRST, BECAUSE THE LOCATION IS THE CONTRACT. Nothing in this file is
// part of any public surface. It lives under mcp-server/test/, no module in
// mcp-server/src imports it, and rollout-pilot-r01.v5.test.mjs proves that with
// a real parser over every module in src — static imports, dynamic imports,
// require calls and the call sites the parser could not fold — rather than with
// a promise. The only importers are the two suites.
//
// WHY IT EXISTS AT ALL. All three of this slice's done clauses are RUNTIME
// outcomes:
//
//   1. Joe uses complete J1 on ten consecutive business days meeting the exact
//      failure exclusions.
//   2. An injected recovery succeeds.
//   3. Dell completes realistic work independently with no developer tools.
//
// None of the three can be produced from source, and the five owners that would
// settle them — the operating calendar, the pilot-day ledger, the independent
// observer, the drill receipt store and the onboarding enrollment store — do not
// exist in this repository. So every public evaluator answers `unavailable` on
// every input and reads no field of its request at all. That is the honest
// answer and it is also a trap: a layer that only says no cannot be shown to
// have decided anything, and on the day its stores arrive nobody will know
// whether the ladder underneath was ever right.
//
// The ladder therefore lives HERE, as one classifier per clause, reachable only
// from the test tree. The previous round of this slice kept it in src as
// `rollout-pilot-r01.internal.v5.js` and had the public evaluators call it, so
// the classification rode back out inside the refusal as
// `would_complete_run_if_authoritative`. The review of PR 992 named that: an
// accompanying `decision: "unavailable"` does not make a caller-derived
// classification unreachable, and naming a file `internal` is not an access
// boundary. Both are fixed by this file's address.
//
// THE THREE PROPERTIES THAT KEEP A CLASSIFIER FROM BECOMING AN AUTHORIZATION:
//
//   1. NO PRIVILEGED WORD IS EVER RETURNED. A classifier answers with a value
//      from CLASSIFICATIONS below, every one of which begins "would_" and names
//      a hypothetical. `pass`, `passed`, `counted`, `completed`, `succeeded`,
//      `operable`, `independent` and `allow` are not producible here.
//
//   2. UNREACHABLE FROM PRODUCTION. Not re-exported, not imported, not
//      importable — the guard is the parser scan named above, and it covers the
//      dynamic form as well as the static one.
//
//   3. THE ANSWER IS NOT THE DECISION. `would_count_day_if_authoritative` means
//      "the deterministic checks this slice can run found nothing wrong with
//      what you told me". It says nothing about whether what you told me is
//      TRUE, because the ledger and the observer that would say so are missing —
//      and in this slice that gap is unusually wide, since the single most
//      load-bearing input (why a day failed) is a judgement an outside observer
//      makes, not a value a caller may assert.
//
// PURE, like the rest of the lane: no filesystem, no network, no database, no
// clock, no environment, and no state between calls.

import {
  V5_R01_BREAKING_FAILURE_ORIGINS,
  V5_R01_DAY_CHECKS,
  V5_R01_DISQUALIFYING_FAILURE_ORIGINS,
  V5_R01_DRILL_CORRECT_TERMINAL_STATES,
  V5_R01_DRILL_FAULTS,
  V5_R01_DRILL_FAULT_KEYS,
  V5_R01_DRILL_RECEIPT_FIELDS,
  V5_R01_DRILL_TERMINAL_STATES,
  V5_R01_FAILURE_ORIGINS,
  V5_R01_FAILURE_ORIGIN_KEYS,
  V5_R01_FORBIDDEN_DRILL_AIDS,
  V5_R01_FORBIDDEN_TOOL_CLASSES,
  V5_R01_J1_SUBJOURNEY_COUNT,
  V5_R01_REQUIRED_RUN_LENGTH,
  assertArray,
  assertBoolean,
  assertCalendarDate,
  assertClosedKeys,
  assertEnum,
  assertInternalRef,
  assertObject,
  assertRequiredKeys,
  calendarDayOrdinal,
  calendarWeekday,
  fail,
} from "../src/rollout-pilot-r01.vocabulary.v5.js";

/**
 * PRIVATE TO THIS HELPER. `deepFreeze` left the vocabulary's export list because
 * `deepFreeze(x)` returns `x`, which made a public function of this slice hand a
 * caller's own object — privileged tokens and all — straight back. Every module
 * that freezes keeps its own copy now.
 */
function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (value !== null && typeof value === "object"
    && [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    Object.values(value).forEach(deepFreeze);
    return Object.freeze(value);
  }
}

/**
 * THE CLASSIFIER'S OWN FAILURE SHAPE, and why it is not production's.
 *
 * This helper raises four refusals that no production module can raise —
 * a duplicated pilot day, an unreadable instant, a classification that forgot to
 * be hypothetical, and a derivation that disagrees with the vocabulary. They used
 * to travel as V5R01Error codes, which meant the production error class had to
 * accept codes only a test could produce. Production's `V5_R01_ERROR_CODES` is a
 * CLOSED set now — that is what stopped `new V5R01Error(callerValue)` from
 * putting caller input on `error.code` — so test-only codes get a test-only
 * class rather than a hole in the production one.
 */
export const V5_R01_CLASSIFIER_ERROR_CODES = Object.freeze([
  "classification_is_not_conditional",
  "duplicate_pilot_day",
  "failure_origin_derivation_disagrees",
  "not_an_instant",
]);

export class V5R01ClassifierError extends Error {
  constructor(code, message, detail) {
    if (!V5_R01_CLASSIFIER_ERROR_CODES.includes(code)) {
      throw new TypeError("v5_r01_classifier_error_code_is_not_registered");
    }
    super(message);
    this.name = "V5R01ClassifierError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function classifierFail(code, message, detail) {
  throw new V5R01ClassifierError(code, message, detail);
}

/** Saturday and Sunday, by weekday ordinal. The only part of the calendar that is arithmetic. */
const WEEKEND = Object.freeze([0, 6]);

/**
 * What each verdict is called once it leaves a classifier.
 *
 * THIS VOCABULARY LIVES HERE AND NOWHERE ELSE. It used to sit in the production
 * vocabulary module, which meant a `would_` token was an exported production
 * string that a consumer could match on. No module in mcp-server/src carries one
 * now, and the suite asserts that by reading the source of every src file.
 *
 * Every value begins "would_" and names a hypothetical; none of them is one of
 * the words a consumer acts on.
 */
export const CLASSIFICATIONS = deepFreeze({
  refuse: "would_refuse",
  day: "would_count_day_if_authoritative",
  run: "would_complete_run_if_authoritative",
  drill: "would_succeed_drill_if_authoritative",
  onboarding: "would_complete_onboarding_if_authoritative",
  beta: "would_be_beta_operable_if_authoritative",
});

for (const value of Object.values(CLASSIFICATIONS)) {
  if (!value.startsWith("would_")) {
    classifierFail("classification_is_not_conditional",
      `classification "${value}" does not name a hypothetical`, { value });
  }
}

/** Said on every result, so a value that escaped still reads as "not authority". */
export const CLASSIFIER_EVIDENCE_SOURCE = "caller_supplied_shapes_not_authority";

const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;

/**
 * An ISO-8601 UTC instant, and the epoch milliseconds it names.
 *
 * A receipt carries two of them and their ORDER is a real check, so the value has
 * to be comparable rather than merely well-shaped: a string that matches the
 * pattern but is not a date the engine can read (2026-02-30) is refused here
 * rather than silently becoming NaN in a comparison downstream.
 */
function assertInstant(value, path) {
  if (typeof value !== "string" || !ISO_INSTANT.test(value)) {
    classifierFail("not_an_instant", `${path} must be an ISO-8601 UTC instant`, { path, value });
  }
  const epoch = Date.parse(value);
  if (!Number.isFinite(epoch)) {
    classifierFail("not_an_instant", `${path} is not a readable instant`, { path, value });
  }
  // A DATE THAT ROLLS OVER IS NOT THE DATE THAT WAS WRITTEN. Date.parse happily
  // reads 2026-02-30 and hands back March 2, so a finite epoch proves only that
  // the engine made something of the string. The round trip is the real check:
  // the instant the engine produced must spell the same instant back.
  if (new Date(epoch).toISOString().slice(0, 19) !== value.replace(/\.\d{1,3}Z$/, "Z").slice(0, 19)) {
    classifierFail("not_an_instant", `${path} names no such instant`, { path, value });
  }
  return epoch;
}

function classified(classification, blocking_check, detail = {}) {
  return deepFreeze({
    classification,
    blocking_check,
    is_not_authority: true,
    evidence_source: CLASSIFIER_EVIDENCE_SOURCE,
    ...detail,
  });
}

// ---------------------------------------------------------------------------
// CLAUSE ONE — a single candidate pilot day.
// ---------------------------------------------------------------------------

const DAY_KEYS = Object.freeze([
  "date",
  "subjourneys_exercised",
  "real_business_actions",
  "high_defect_open_against_j1",
  "failures",
]);

const FAILURE_KEYS = Object.freeze([
  "failure_ref",
  "observed_origin",
  "product_reported_honestly",
  "documented_fallback_offered",
]);

/**
 * The ordered questions for one day, asked in V5_R01_DAY_CHECKS order.
 *
 * THE ORDER IS PART OF THE DEFINITION. An unresolved high defect is asked about
 * FIRST, before anyone looks at whether the day's work got done, because a pilot
 * does not run over one: a perfect day inside a disqualified run is still inside
 * a disqualified run, and reporting "the work was fine" first would bury that.
 *
 * `observed_origin` is the field this whole slice turns on, and it is the field a
 * caller least deserves to set. It is here because a classifier answers a
 * hypothetical; the public evaluator will not read it from a caller at all.
 */
export function classifyPilotDayIfAuthoritative(day) {
  assertObject(day, "day");
  assertClosedKeys(day, DAY_KEYS, "day");
  assertRequiredKeys(day, DAY_KEYS, "day");
  assertCalendarDate(day.date, "day.date");
  assertArray(day.subjourneys_exercised, "day.subjourneys_exercised", { max: 16 });
  day.subjourneys_exercised.forEach((ref, index) =>
    assertInternalRef(ref, `day.subjourneys_exercised[${index}]`));
  assertArray(day.real_business_actions, "day.real_business_actions", { max: 256 });
  day.real_business_actions.forEach((ref, index) =>
    assertInternalRef(ref, `day.real_business_actions[${index}]`));
  assertBoolean(day.high_defect_open_against_j1, "day.high_defect_open_against_j1");
  assertArray(day.failures, "day.failures", { max: 64 });
  day.failures.forEach((failure, index) => {
    const path = `day.failures[${index}]`;
    assertObject(failure, path);
    assertClosedKeys(failure, FAILURE_KEYS, path);
    assertRequiredKeys(failure, FAILURE_KEYS, path);
    assertInternalRef(failure.failure_ref, `${path}.failure_ref`);
    assertEnum(failure.observed_origin, V5_R01_FAILURE_ORIGIN_KEYS, `${path}.observed_origin`);
    assertBoolean(failure.product_reported_honestly, `${path}.product_reported_honestly`);
    assertBoolean(failure.documented_fallback_offered, `${path}.documented_fallback_offered`);
  });

  const distinctSubjourneys = new Set(day.subjourneys_exercised);

  // Check 1 — the run-level disqualifier, asked before anything else.
  if (day.high_defect_open_against_j1) {
    return classified(CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[0], {
      date: day.date, disqualifies_run: true,
      disqualifying_origin: "unresolved_high_defect",
    });
  }

  // Check 2 — all three subjourneys. Counted DISTINCTLY: three references to the
  // same subjourney is one subjourney done three times, which is not the clause.
  if (distinctSubjourneys.size !== V5_R01_J1_SUBJOURNEY_COUNT) {
    return classified(CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[1], {
      date: day.date, disqualifies_run: false,
      distinct_subjourneys: distinctSubjourneys.size,
      required_subjourneys: V5_R01_J1_SUBJOURNEY_COUNT,
    });
  }

  // Check 3 — real work, not a walkthrough.
  if (day.real_business_actions.length === 0) {
    return classified(CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[2], {
      date: day.date, disqualifies_run: false,
    });
  }

  // Check 4 — the exclusions, applied one failure at a time. The FIRST breaking
  // failure is reported, so the answer names a specific failure rather than a
  // count.
  for (const failure of day.failures) {
    const origin = V5_R01_FAILURE_ORIGINS[failure.observed_origin];
    if (origin.disqualifies_run) {
      return classified(CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[3], {
        date: day.date, disqualifies_run: true,
        disqualifying_origin: failure.observed_origin,
        failure_ref: failure.failure_ref,
      });
    }
    if (!origin.excluded) {
      return classified(CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[3], {
        date: day.date, disqualifies_run: false,
        breaking_origin: failure.observed_origin,
        failure_ref: failure.failure_ref,
      });
    }
    // A CONDITIONAL exclusion. This is the clause that makes the exclusion list
    // honest rather than an alibi: an outage excused only because the product
    // announced it is not excused when the product claimed to be healthy.
    if (origin.condition === "product_reported_honestly_and_offered_documented_fallback"
      && !(failure.product_reported_honestly && failure.documented_fallback_offered)) {
      return classified(CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[3], {
        date: day.date, disqualifies_run: false,
        breaking_origin: failure.observed_origin,
        exclusion_condition_unmet: origin.condition,
        failure_ref: failure.failure_ref,
      });
    }
  }

  return classified(CLASSIFICATIONS.day, null, {
    date: day.date, disqualifies_run: false,
    excluded_failures: day.failures.map(f => f.failure_ref).sort(),
  });
}

// ---------------------------------------------------------------------------
// CLAUSE ONE, CONTINUED — the run of ten.
// ---------------------------------------------------------------------------

const RUN_KEYS = Object.freeze(["days", "non_business_dates"]);

/**
 * Ten consecutive business days.
 *
 * "CONSECUTIVE" IS THE HARD PART AND IT IS WHERE A LEDGER GETS CHEATED. Three
 * separate things could each be called a gap and only one of them is:
 *
 *   * A weekend or a declared non-business date is SKIPPED. That is what
 *     "business days" means, and skipping it is not a gap.
 *   * A business day with no ledger entry at all is a gap. Joe did not use the
 *     product that day, and a run that steps over the silence is not a run of
 *     ten consecutive days — it is ten days picked out of a longer stretch.
 *   * A business day whose entry fails its checks is also a gap, for the same
 *     reason, and the run restarts after it rather than resuming.
 *
 * So this function does not filter the ledger down to the good days and count
 * them. It walks the calendar day by day, which is the only walk that can see a
 * day that is missing.
 */
export function classifyPilotRunIfAuthoritative(request) {
  assertObject(request, "request");
  assertClosedKeys(request, RUN_KEYS, "request");
  assertRequiredKeys(request, RUN_KEYS, "request");
  assertArray(request.days, "request.days", { max: 512 });
  assertArray(request.non_business_dates, "request.non_business_dates", { max: 512 });
  request.non_business_dates.forEach((date, index) =>
    assertCalendarDate(date, `request.non_business_dates[${index}]`));

  const byDate = new Map();
  request.days.forEach((day, index) => {
    assertObject(day, `request.days[${index}]`);
    assertCalendarDate(day.date, `request.days[${index}].date`);
    if (byDate.has(day.date)) {
      classifierFail("duplicate_pilot_day", `two entries claim ${day.date}; a day has one entry`,
        { date: day.date });
    }
    byDate.set(day.date, day);
  });

  const closed = new Set(request.non_business_dates);
  const verdicts = [...byDate.keys()].sort()
    .map(date => classifyPilotDayIfAuthoritative(byDate.get(date)));

  const disqualified = verdicts.find(v => v.disqualifies_run === true);
  if (disqualified) {
    return classified(CLASSIFICATIONS.refuse, "run_disqualified_by_open_high_defect", {
      run_disqualified: true,
      disqualifying_date: disqualified.date,
      disqualifying_origin: disqualified.disqualifying_origin,
      longest_run: 0,
      required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
    });
  }

  const counted = new Set(
    verdicts.filter(v => v.classification === CLASSIFICATIONS.day).map(v => v.date));

  // The calendar walk. From the earliest to the latest date the ledger mentions,
  // one calendar day at a time.
  const dates = [...byDate.keys()].sort();
  let longest = 0;
  let current = 0;
  let runStart = null;
  let bestStart = null;
  let bestEnd = null;
  if (dates.length > 0) {
    const first = calendarDayOrdinal(dates[0]);
    const last = calendarDayOrdinal(dates[dates.length - 1]);
    for (let ordinal = first; ordinal <= last; ordinal += 1) {
      const date = new Date(ordinal * 86400000).toISOString().slice(0, 10);
      if (WEEKEND.includes(calendarWeekday(date)) || closed.has(date)) continue;
      if (counted.has(date)) {
        current += 1;
        if (current === 1) runStart = date;
        if (current > longest) { longest = current; bestStart = runStart; bestEnd = date; }
      } else {
        current = 0;
        runStart = null;
      }
    }
  }

  if (longest < V5_R01_REQUIRED_RUN_LENGTH) {
    return classified(CLASSIFICATIONS.refuse, "run_shorter_than_required", {
      run_disqualified: false,
      longest_run: longest,
      required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
    });
  }

  return classified(CLASSIFICATIONS.run, null, {
    run_disqualified: false,
    longest_run: longest,
    required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
    first_date: bestStart,
    last_date: bestEnd,
  });
}

// ---------------------------------------------------------------------------
// CLAUSE TWO — the injected recovery drill.
// ---------------------------------------------------------------------------

/**
 * THE RECEIPT SCHEMA, TAKEN FROM THE REGISTRY RATHER THAN RETYPED.
 *
 * The previous round declared twelve fields in V5_R01_DRILL_RECEIPT_FIELDS and
 * then validated eight, so a complete receipt built to the published schema was
 * rejected as carrying four unknown fields — `injected_at`, `recovered_at`,
 * `producer_step_ref` and `recovery_path_taken`. The review of PR 992
 * reproduced it. Binding this list to the exported constant is the fix that
 * cannot drift back: a field added to the registry is a field this classifier
 * must then read, because `assertRequiredKeys` will demand it.
 */
const DRILL_KEYS = V5_R01_DRILL_RECEIPT_FIELDS;

/**
 * Did the injected recovery go the way the drill requires?
 *
 * THE RECEIPT IS THE REGISTERED TWELVE-FIELD ONE. `DRILL_KEYS` IS
 * `V5_R01_DRILL_RECEIPT_FIELDS` itself, so the schema this reads and the schema
 * the slice publishes are one list rather than two that agreed once.
 *
 * SEVEN CHECKS, IN ORDER, and the order says what the drill is actually for.
 *
 *   0. THREE ROLES, THREE PEOPLE. The subject may not be the injector, the
 *      subject may not be the observer, and the injector may not be the
 *      observer. All three pairs, each reported by name. Someone who broke it,
 *      watched it and recovered it has run a demonstration rather than a drill.
 *   0b. The recovery followed the injection. An interval that runs backwards
 *      describes no drill.
 *   1. The fault has to be one the product has DECLARED a behaviour for. A drill
 *      that injects something S01 never modelled is measuring an undefined case.
 *   2. The product's visible signal has to be the declared one. This is checked
 *      BEFORE the outcome, because a partner who happened to recover from a
 *      silent failure recovered in spite of the product, not because of it — and
 *      Q010's requirement is that the product let him RECOGNIZE failure.
 *   3. No forbidden aid. A recovery that needed a terminal, a raw log or the
 *      author's explanation is the exact dependency Q010 forbids.
 *   4. The path out was the declared one for that fault.
 *   5. Only then, the terminal state — and two of the four states are correct.
 *      Stopping safely counts: Q010 asks that the partner "avoid making damage
 *      worse", not that he fix everything.
 */
export function classifyRecoveryDrillIfAuthoritative(drill) {
  assertObject(drill, "drill");
  assertClosedKeys(drill, DRILL_KEYS, "drill");
  assertRequiredKeys(drill, DRILL_KEYS, "drill");
  assertInternalRef(drill.drill_ref, "drill.drill_ref");
  assertEnum(drill.fault_injected, V5_R01_DRILL_FAULT_KEYS, "drill.fault_injected");
  assertInternalRef(drill.subject_partner, "drill.subject_partner");
  assertInternalRef(drill.observed_partner_visible_signal, "drill.observed_partner_visible_signal");
  assertEnum(drill.terminal_state_reached, V5_R01_DRILL_TERMINAL_STATES,
    "drill.terminal_state_reached");
  assertArray(drill.aids_used, "drill.aids_used", { max: 32 });
  drill.aids_used.forEach((aid, index) => assertInternalRef(aid, `drill.aids_used[${index}]`));
  assertInternalRef(drill.injected_by_identity_ref, "drill.injected_by_identity_ref");
  assertInternalRef(drill.observer_identity_ref, "drill.observer_identity_ref");
  assertInternalRef(drill.producer_step_ref, "drill.producer_step_ref");
  assertInternalRef(drill.recovery_path_taken, "drill.recovery_path_taken");
  const injectedAt = assertInstant(drill.injected_at, "drill.injected_at");
  const recoveredAt = assertInstant(drill.recovered_at, "drill.recovered_at");

  const fault = V5_R01_DRILL_FAULTS[drill.fault_injected];

  // Check 0 — THREE ROLES, THREE PEOPLE, and all three pairs are checked.
  //
  // The definition has always said that neither the injector nor the observer may
  // be the partner recovering, and the previous round compared only the injector
  // with the observer. So a receipt naming the subject as its own injector, or as
  // its own observer, reached the end of the ladder — someone who broke it and
  // recovered it, or recovered it and vouched for himself, ran a demonstration
  // rather than a drill. Each pair is reported under its own name, because
  // "someone was wearing two hats" is not as useful as which two.
  if (drill.subject_partner === drill.injected_by_identity_ref) {
    return classified(CLASSIFICATIONS.refuse, "subject_injected_its_own_fault", {
      drill_ref: drill.drill_ref,
      subject_partner: drill.subject_partner,
    });
  }
  if (drill.subject_partner === drill.observer_identity_ref) {
    return classified(CLASSIFICATIONS.refuse, "subject_observed_its_own_recovery", {
      drill_ref: drill.drill_ref,
      subject_partner: drill.subject_partner,
    });
  }
  if (drill.injected_by_identity_ref === drill.observer_identity_ref) {
    return classified(CLASSIFICATIONS.refuse, "injector_and_observer_are_the_same_identity", {
      drill_ref: drill.drill_ref,
    });
  }

  // Check 0b — a recovery cannot precede the fault it recovered from. The two
  // instants are in the receipt because the drill is a thing that happened over
  // an interval, and an interval that runs backwards describes no drill at all.
  if (recoveredAt <= injectedAt) {
    return classified(CLASSIFICATIONS.refuse, "recovery_does_not_follow_the_injection", {
      drill_ref: drill.drill_ref,
      injected_at: drill.injected_at,
      recovered_at: drill.recovered_at,
    });
  }

  if (drill.observed_partner_visible_signal !== fault.expected_partner_visible_signal) {
    return classified(CLASSIFICATIONS.refuse, "product_did_not_show_the_declared_signal", {
      drill_ref: drill.drill_ref,
      expected_signal: fault.expected_partner_visible_signal,
      observed_signal: drill.observed_partner_visible_signal,
    });
  }

  const forbidden = drill.aids_used.filter(aid => V5_R01_FORBIDDEN_DRILL_AIDS.includes(aid)).sort();
  if (forbidden.length > 0) {
    return classified(CLASSIFICATIONS.refuse, "recovery_required_a_forbidden_aid", {
      drill_ref: drill.drill_ref,
      forbidden_aids_used: deepFreeze(forbidden),
    });
  }

  // Check 4 — the path out was the one the product declares for this fault. The
  // fault table names a `recovery_is` per fault, and a receipt describing some
  // other route describes a partner who got out of it some other way. That may
  // well be resourceful; it is not evidence that the declared recovery works.
  if (drill.recovery_path_taken !== fault.recovery_is) {
    return classified(CLASSIFICATIONS.refuse, "recovery_path_was_not_the_declared_one", {
      drill_ref: drill.drill_ref,
      declared_recovery_path: fault.recovery_is,
      recovery_path_taken: drill.recovery_path_taken,
    });
  }

  if (!V5_R01_DRILL_CORRECT_TERMINAL_STATES.includes(drill.terminal_state_reached)) {
    return classified(CLASSIFICATIONS.refuse, "drill_ended_in_an_incorrect_terminal_state", {
      drill_ref: drill.drill_ref,
      terminal_state_reached: drill.terminal_state_reached,
    });
  }

  return classified(CLASSIFICATIONS.drill, null, {
    drill_ref: drill.drill_ref,
    fault_injected: drill.fault_injected,
    terminal_state_reached: drill.terminal_state_reached,
    producer_step_ref: drill.producer_step_ref,
    injected_at: drill.injected_at,
    recovered_at: drill.recovered_at,
  });
}

// ---------------------------------------------------------------------------
// CLAUSE THREE — onboarding completion and beta operability.
// ---------------------------------------------------------------------------

const PROGRESS_KEYS = Object.freeze([
  "partner",
  "steps_completed",
  "tool_classes_used",
]);

/**
 * Has this partner been through the whole flow, using only permitted tools?
 *
 * The step roster is passed in from the onboarding module rather than imported,
 * so this classifier cannot drift from the flow it is judging: there is one
 * roster, defined once, and this function is handed it.
 */
export function classifyOnboardingIfAuthoritative(progress, roster) {
  assertObject(progress, "progress");
  assertClosedKeys(progress, PROGRESS_KEYS, "progress");
  assertRequiredKeys(progress, PROGRESS_KEYS, "progress");
  assertInternalRef(progress.partner, "progress.partner");
  assertArray(progress.steps_completed, "progress.steps_completed", { max: 64 });
  progress.steps_completed.forEach((step, index) =>
    assertInternalRef(step, `progress.steps_completed[${index}]`));
  assertArray(progress.tool_classes_used, "progress.tool_classes_used", { max: 32 });
  progress.tool_classes_used.forEach((tool, index) =>
    assertInternalRef(tool, `progress.tool_classes_used[${index}]`));
  assertArray(roster, "roster", { min: 1, max: 64 });

  const developerTools =
    progress.tool_classes_used.filter(tool => V5_R01_FORBIDDEN_TOOL_CLASSES.includes(tool)).sort();
  if (developerTools.length > 0) {
    return classified(CLASSIFICATIONS.refuse, "onboarding_used_a_developer_tool", {
      partner: progress.partner,
      developer_tools_used: deepFreeze(developerTools),
    });
  }

  const done = new Set(progress.steps_completed);
  const missing = roster.filter(step => !done.has(step)).sort();
  if (missing.length > 0) {
    return classified(CLASSIFICATIONS.refuse, "onboarding_steps_outstanding", {
      partner: progress.partner,
      outstanding_steps: deepFreeze(missing),
    });
  }

  const unknown = [...done].filter(step => !roster.includes(step)).sort();
  if (unknown.length > 0) {
    return classified(CLASSIFICATIONS.refuse, "onboarding_claims_an_unregistered_step", {
      partner: progress.partner,
      unregistered_steps: deepFreeze(unknown),
    });
  }

  return classified(CLASSIFICATIONS.onboarding, null, { partner: progress.partner });
}

const BETA_KEYS = Object.freeze([
  "partner",
  "onboarding_classification",
  "realistic_work_items",
  "author_interventions",
  "tool_classes_used",
]);

/**
 * Could Dell operate the product on his own?
 *
 * THE INTERVENTION COUNT IS THE CLAUSE, and it is a zero rather than a
 * threshold. Q009's settled text is that "his confusion is product evidence, not
 * a training failure" — so one explanation from the author converts a beta pass
 * into a product finding, and there is no small number of explanations that is
 * still independent. `author_interventions` must be exactly zero.
 */
export function classifyBetaOperabilityIfAuthoritative(request) {
  assertObject(request, "request");
  assertClosedKeys(request, BETA_KEYS, "request");
  assertRequiredKeys(request, BETA_KEYS, "request");
  assertInternalRef(request.partner, "request.partner");
  assertInternalRef(request.onboarding_classification, "request.onboarding_classification");
  assertArray(request.realistic_work_items, "request.realistic_work_items", { max: 256 });
  request.realistic_work_items.forEach((item, index) =>
    assertInternalRef(item, `request.realistic_work_items[${index}]`));
  if (!Number.isInteger(request.author_interventions) || request.author_interventions < 0) {
    fail("invalid_shape", "request.author_interventions must be a non-negative integer",
      { path: "request.author_interventions" });
  }
  assertArray(request.tool_classes_used, "request.tool_classes_used", { max: 32 });
  request.tool_classes_used.forEach((tool, index) =>
    assertInternalRef(tool, `request.tool_classes_used[${index}]`));

  if (request.onboarding_classification !== CLASSIFICATIONS.onboarding) {
    return classified(CLASSIFICATIONS.refuse, "onboarding_not_classified_complete", {
      partner: request.partner,
    });
  }
  if (request.realistic_work_items.length === 0) {
    return classified(CLASSIFICATIONS.refuse, "no_realistic_work_performed", {
      partner: request.partner,
    });
  }
  const developerTools =
    request.tool_classes_used.filter(tool => V5_R01_FORBIDDEN_TOOL_CLASSES.includes(tool)).sort();
  if (developerTools.length > 0) {
    return classified(CLASSIFICATIONS.refuse, "beta_work_used_a_developer_tool", {
      partner: request.partner,
      developer_tools_used: deepFreeze(developerTools),
    });
  }
  if (request.author_interventions !== 0) {
    return classified(CLASSIFICATIONS.refuse, "author_explained_the_system", {
      partner: request.partner,
      author_interventions: request.author_interventions,
    });
  }

  return classified(CLASSIFICATIONS.beta, null, {
    partner: request.partner,
    realistic_work_item_count: request.realistic_work_items.length,
  });
}

/** The breaking origins, re-derived here so a test can compare the two derivations. */
export function breakingFailureOrigins() {
  return deepFreeze(V5_R01_FAILURE_ORIGIN_KEYS
    .filter(key => !V5_R01_FAILURE_ORIGINS[key].excluded).sort());
}

/** The disqualifying origins, likewise. */
export function disqualifyingFailureOrigins() {
  return deepFreeze(V5_R01_FAILURE_ORIGIN_KEYS
    .filter(key => V5_R01_FAILURE_ORIGINS[key].disqualifies_run).sort());
}

// A derivation that disagrees with the vocabulary's own is a bug in one of them,
// and it is better found at load than in whichever suite happens to run first.
for (const [derived, declared] of [
  [breakingFailureOrigins(), V5_R01_BREAKING_FAILURE_ORIGINS],
  [disqualifyingFailureOrigins(), V5_R01_DISQUALIFYING_FAILURE_ORIGINS],
]) {
  if (derived.join("|") !== [...declared].sort().join("|")) {
    classifierFail("failure_origin_derivation_disagrees",
      "the internal derivation of the failure-origin sets disagrees with the vocabulary's",
      { derived, declared: [...declared] });
  }
}
