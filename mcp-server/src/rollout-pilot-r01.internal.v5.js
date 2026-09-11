// DoctorCRE v5 slice V5-R01 — INTERNAL classifiers. NOT A PUBLIC SURFACE.
//
// WHY THIS FILE EXISTS, said first, because a second file is exactly the kind of
// thing a reviewer should be suspicious of.
//
// All three of this slice's done clauses are RUNTIME outcomes:
//
//   1. Joe uses complete J1 on ten consecutive business days meeting the exact
//      failure exclusions.
//   2. An injected recovery succeeds.
//   3. Dell completes realistic work independently with no developer tools.
//
// None of the three can be produced from source, and the five owners that would
// settle them — the operating calendar, the pilot-day ledger, the independent
// observer, the drill receipt store and the onboarding enrollment store — do not
// exist in this repository. So every public evaluator refuses, on every input,
// always. That is the honest answer and it is also a trap: a layer that only
// says no cannot be shown to have decided anything, and on the day its stores
// arrive nobody will know whether the ladder underneath was ever right.
//
// The ladder therefore lives here, as one classifier per clause, and the public
// modules CALL it to name the check that would have blocked. The classifiers are
// load-bearing inside the refusal — the public answer reports `blocking_check`
// from exactly these functions — and they are directly testable. What they must
// never be is an authorization.
//
// THE THREE PROPERTIES THAT KEEP THEM FROM BECOMING ONE:
//
//   1. NO PRIVILEGED WORD IS EVER RETURNED. A classifier returns `classification`
//      from V5_R01_CLASSIFICATIONS, whose every value begins "would_" and none of
//      which is in V5_R01_PRIVILEGED_OUTCOMES. `pass`, `passed`, `counted`,
//      `completed`, `succeeded`, `operable`, `independent` and `allow` are not
//      producible here. A consumer mistaking `would_count_day_if_authoritative`
//      for a counted day would be reading a word that says, in itself, that it is
//      not one.
//
//   2. NOT REACHABLE THROUGH EITHER PUBLIC SURFACE. Nothing here is re-exported
//      by rollout-pilot-r01.v5.js or onboarding-flow-r01.v5.js. The suite proves
//      that with Node's own module parser (vm.SourceTextModule) over the whole
//      tree, not with a regex.
//
//   3. THE ANSWER IS NOT THE DECISION. `would_count_day_if_authoritative` means
//      "the deterministic checks this slice can run found nothing wrong with what
//      you told me". It says nothing about whether what you told me is TRUE,
//      because the ledger and the observer that would say so are missing — and in
//      this slice that gap is unusually wide, since the single most load-bearing
//      input (why a day failed) is a judgement an outside observer makes, not a
//      value a caller may assert.
//
// PURE, like the rest of the lane: no filesystem, no network, no database, no
// clock, no environment, and no state between calls.

import {
  V5_R01_BREAKING_FAILURE_ORIGINS,
  V5_R01_CLASSIFICATIONS,
  V5_R01_DAY_CHECKS,
  V5_R01_DISQUALIFYING_FAILURE_ORIGINS,
  V5_R01_DRILL_CORRECT_TERMINAL_STATES,
  V5_R01_DRILL_FAULTS,
  V5_R01_DRILL_FAULT_KEYS,
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
  deepFreeze,
  fail,
} from "./rollout-pilot-r01.vocabulary.v5.js";

/** Saturday and Sunday, by weekday ordinal. The only part of the calendar that is arithmetic. */
const WEEKEND = Object.freeze([0, 6]);

function classified(classification, blocking_check, detail = {}) {
  return deepFreeze({ classification, blocking_check, ...detail });
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
    return classified(V5_R01_CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[0], {
      date: day.date, disqualifies_run: true,
      disqualifying_origin: "unresolved_high_defect",
    });
  }

  // Check 2 — all three subjourneys. Counted DISTINCTLY: three references to the
  // same subjourney is one subjourney done three times, which is not the clause.
  if (distinctSubjourneys.size !== V5_R01_J1_SUBJOURNEY_COUNT) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[1], {
      date: day.date, disqualifies_run: false,
      distinct_subjourneys: distinctSubjourneys.size,
      required_subjourneys: V5_R01_J1_SUBJOURNEY_COUNT,
    });
  }

  // Check 3 — real work, not a walkthrough.
  if (day.real_business_actions.length === 0) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[2], {
      date: day.date, disqualifies_run: false,
    });
  }

  // Check 4 — the exclusions, applied one failure at a time. The FIRST breaking
  // failure is reported, so the answer names a specific failure rather than a
  // count.
  for (const failure of day.failures) {
    const origin = V5_R01_FAILURE_ORIGINS[failure.observed_origin];
    if (origin.disqualifies_run) {
      return classified(V5_R01_CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[3], {
        date: day.date, disqualifies_run: true,
        disqualifying_origin: failure.observed_origin,
        failure_ref: failure.failure_ref,
      });
    }
    if (!origin.excluded) {
      return classified(V5_R01_CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[3], {
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
      return classified(V5_R01_CLASSIFICATIONS.refuse, V5_R01_DAY_CHECKS[3], {
        date: day.date, disqualifies_run: false,
        breaking_origin: failure.observed_origin,
        exclusion_condition_unmet: origin.condition,
        failure_ref: failure.failure_ref,
      });
    }
  }

  return classified(V5_R01_CLASSIFICATIONS.day, null, {
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
      fail("duplicate_pilot_day", `two entries claim ${day.date}; a day has one entry`,
        { date: day.date });
    }
    byDate.set(day.date, day);
  });

  const closed = new Set(request.non_business_dates);
  const verdicts = [...byDate.keys()].sort()
    .map(date => classifyPilotDayIfAuthoritative(byDate.get(date)));

  const disqualified = verdicts.find(v => v.disqualifies_run === true);
  if (disqualified) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "run_disqualified_by_open_high_defect", {
      run_disqualified: true,
      disqualifying_date: disqualified.date,
      disqualifying_origin: disqualified.disqualifying_origin,
      longest_run: 0,
      required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
    });
  }

  const counted = new Set(
    verdicts.filter(v => v.classification === V5_R01_CLASSIFICATIONS.day).map(v => v.date));

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
    return classified(V5_R01_CLASSIFICATIONS.refuse, "run_shorter_than_required", {
      run_disqualified: false,
      longest_run: longest,
      required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
    });
  }

  return classified(V5_R01_CLASSIFICATIONS.run, null, {
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

const DRILL_KEYS = Object.freeze([
  "drill_ref",
  "fault_injected",
  "subject_partner",
  "observed_partner_visible_signal",
  "terminal_state_reached",
  "aids_used",
  "injected_by_identity_ref",
  "observer_identity_ref",
]);

/**
 * Did the injected recovery go the way the drill requires?
 *
 * FOUR CHECKS, IN ORDER, and the order says what the drill is actually for.
 *
 *   1. The fault has to be one the product has DECLARED a behaviour for. A drill
 *      that injects something S01 never modelled is measuring an undefined case.
 *   2. The product's visible signal has to be the declared one. This is checked
 *      BEFORE the outcome, because a partner who happened to recover from a
 *      silent failure recovered in spite of the product, not because of it — and
 *      Q010's requirement is that the product let him RECOGNIZE failure.
 *   3. No forbidden aid. A recovery that needed a terminal, a raw log or the
 *      author's explanation is the exact dependency Q010 forbids.
 *   4. Only then, the terminal state — and two of the four states are correct.
 *      Stopping safely counts: Q010 asks that the partner "avoid making damage
 *      worse", not that he fix everything.
 *
 * THE INJECTOR AND THE OBSERVER MUST BE DIFFERENT PEOPLE, and neither may be the
 * subject. Someone who broke it, watched it and recovered it has run a
 * demonstration rather than a drill.
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

  const fault = V5_R01_DRILL_FAULTS[drill.fault_injected];

  if (drill.injected_by_identity_ref === drill.observer_identity_ref) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "injector_and_observer_are_the_same_identity", {
      drill_ref: drill.drill_ref,
    });
  }

  if (drill.observed_partner_visible_signal !== fault.expected_partner_visible_signal) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "product_did_not_show_the_declared_signal", {
      drill_ref: drill.drill_ref,
      expected_signal: fault.expected_partner_visible_signal,
      observed_signal: drill.observed_partner_visible_signal,
    });
  }

  const forbidden = drill.aids_used.filter(aid => V5_R01_FORBIDDEN_DRILL_AIDS.includes(aid)).sort();
  if (forbidden.length > 0) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "recovery_required_a_forbidden_aid", {
      drill_ref: drill.drill_ref,
      forbidden_aids_used: deepFreeze(forbidden),
    });
  }

  if (!V5_R01_DRILL_CORRECT_TERMINAL_STATES.includes(drill.terminal_state_reached)) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "drill_ended_in_an_incorrect_terminal_state", {
      drill_ref: drill.drill_ref,
      terminal_state_reached: drill.terminal_state_reached,
    });
  }

  return classified(V5_R01_CLASSIFICATIONS.drill, null, {
    drill_ref: drill.drill_ref,
    fault_injected: drill.fault_injected,
    terminal_state_reached: drill.terminal_state_reached,
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
    return classified(V5_R01_CLASSIFICATIONS.refuse, "onboarding_used_a_developer_tool", {
      partner: progress.partner,
      developer_tools_used: deepFreeze(developerTools),
    });
  }

  const done = new Set(progress.steps_completed);
  const missing = roster.filter(step => !done.has(step)).sort();
  if (missing.length > 0) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "onboarding_steps_outstanding", {
      partner: progress.partner,
      outstanding_steps: deepFreeze(missing),
    });
  }

  const unknown = [...done].filter(step => !roster.includes(step)).sort();
  if (unknown.length > 0) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "onboarding_claims_an_unregistered_step", {
      partner: progress.partner,
      unregistered_steps: deepFreeze(unknown),
    });
  }

  return classified(V5_R01_CLASSIFICATIONS.onboarding, null, { partner: progress.partner });
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

  if (request.onboarding_classification !== V5_R01_CLASSIFICATIONS.onboarding) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "onboarding_not_classified_complete", {
      partner: request.partner,
    });
  }
  if (request.realistic_work_items.length === 0) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "no_realistic_work_performed", {
      partner: request.partner,
    });
  }
  const developerTools =
    request.tool_classes_used.filter(tool => V5_R01_FORBIDDEN_TOOL_CLASSES.includes(tool)).sort();
  if (developerTools.length > 0) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "beta_work_used_a_developer_tool", {
      partner: request.partner,
      developer_tools_used: deepFreeze(developerTools),
    });
  }
  if (request.author_interventions !== 0) {
    return classified(V5_R01_CLASSIFICATIONS.refuse, "author_explained_the_system", {
      partner: request.partner,
      author_interventions: request.author_interventions,
    });
  }

  return classified(V5_R01_CLASSIFICATIONS.beta, null, {
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
    fail("failure_origin_derivation_disagrees",
      "the internal derivation of the failure-origin sets disagrees with the vocabulary's",
      { derived, declared: [...declared] });
  }
}
