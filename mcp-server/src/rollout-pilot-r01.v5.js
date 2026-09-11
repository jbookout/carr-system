// DoctorCRE v5 slice V5-R01 — the pilot-day ledger, the exact failure
// exclusions, and the injected-recovery drill.
//
// THE ONE THING TO UNDERSTAND ABOUT THIS SLICE BEFORE READING THE CODE: its
// three done clauses are not source properties, and no amount of source can make
// them one.
//
//   1. "Joe 10/10 consecutive business days meets exact failure exclusions"
//   2. "injected recovery succeeds"
//   3. "Dell completes realistic work independently with no developer tools"
//
// Each is a statement about ten real days, one real injected failure, and one
// real person doing real work in production. A test suite cannot produce any of
// them, a merge cannot, and a configuration cannot. So this slice builds the half
// that source CAN own — the ledger's shape, the exclusion ladder, the drill
// definition and the evidence each outcome would need — and every evaluator here
// answers `unavailable`, names the owner that is missing, and stops.
//
// WHAT IS ACTUALLY DECIDED HERE, and it is not nothing:
//
//   * WHAT A PILOT DAY IS, as four ordered questions rather than a description,
//     with the order itself load-bearing (V5_R01_DAY_CHECKS).
//   * THE EXACT FAILURE EXCLUSIONS, as a closed registry of six origins, three
//     excluded and three not, two of the three exclusions CONDITIONAL on the
//     product having told the truth about the failure (V5_R01_FAILURE_ORIGINS).
//     That list is the slice's most load-bearing declaration and every entry
//     carries the clause it answers to.
//   * WHAT "CONSECUTIVE" MEANS, walked day by day across the calendar rather
//     than counted off a filtered list, because the only way to see a day that is
//     MISSING from the ledger is to walk the days the ledger does not mention.
//   * WHAT AN INJECTED RECOVERY DRILL IS — which faults, which visible signal,
//     which aids are forbidden, and who may inject versus who may observe.
//
// THE FIELD THIS SLICE TURNS ON IS THE ONE A CALLER MAY NOT SET. Everything
// above hinges on WHY a day's failure happened: a laptop fault is excluded, a
// product defect is not. That attribution is a judgement, the slice contract
// gives it to "deterministic evidence and an independent observer", and no
// observer receipt store exists in this repository. A caller that labels its own
// outage `partner_device_or_credential` is labelling, not attributing, and a
// module that took the label would be letting the subject of the pilot grade it.
//
// SO THE PUBLIC EVALUATORS READ NO FIELD OF THEIR REQUEST AT ALL, and that is
// the whole boundary. Each takes one argument, does not look at it, and returns
// one fixed value — `decision: "unavailable"`, the owed seams, `request_examined:
// false`. If no field of the request can change the answer, then no caller can
// smuggle authority in through one.
//
// THE PREVIOUS ROUND DID NOT HAVE THIS PROPERTY and the review of PR 992 named
// it exactly. The evaluators classified the caller's entry, calendar and receipt
// through a module in src called `rollout-pilot-r01.internal.v5.js`, and returned
// the conditional run verdict and its supporting detail beside the
// `unavailable`. An outer field saying "unavailable" does not make an inner
// classification unreachable, and a file named `internal` is not an access
// boundary — it was an ordinary ESM export any consumer could import. The
// classifiers now live at mcp-server/test/rollout-pilot-r01-classifiers.v5
// .testhelper.mjs, no module in src imports them, and the suite proves it with a
// real parser rather than a grep.
//
// A DUPLICATE DATE WENT WITH THEM. `evaluatePilotDay` used to take both a
// `date` and an `entry`, never compared them, and would happily label a result
// with one date whose detail came from the other. There is nothing left to
// disagree: the request is not read.
//
// THE DRILL IS DEFINED HERE AND INJECTED NOWHERE. Injecting a fault is an effect
// on a running system. Nothing in this file injects, schedules, triggers or
// arranges anything; `describeRecoveryDrill` takes no argument and returns the
// whole declared drill table, and `evaluateRecoveryDrill` refuses to read the
// receipt a caller hands it. A drill is run by people, against production, watched by someone who is
// not the person recovering.
//
// AND THE TYPED SUCCESSOR IS INACTIVE BY CONSTRUCTION. The common slice contract
// asks that any future autonomy be designed now as an inactive typed successor
// whose activation takes a separate action-specific gate.
// V5_R01_DORMANT_SUCCESSOR describes the one this slice can see coming — a
// nightly roll-up that would assemble the pilot ledger without a human — and it
// is a description with `active: false`, no code path, and a named activation
// gate that does not exist. It is not a feature flag: there is nothing here for a
// flag to turn on.
//
// PURE. No filesystem, no network, no database, no clock, no environment, no
// state between calls. `now` is never read, and neither is any date: the only
// dates in this file are in its published tables.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_R01_BREAKING_FAILURE_ORIGINS,
  V5_R01_BUSINESS_DAY_BASIS,
  V5_R01_DAY_CHECKS,
  V5_R01_DISQUALIFYING_FAILURE_ORIGINS,
  V5_R01_DRILL_CORRECT_TERMINAL_STATES,
  V5_R01_DRILL_FAULTS,
  V5_R01_DRILL_FAULT_KEYS,
  V5_R01_DRILL_RECEIPT_FIELDS,
  V5_R01_DRILL_RECEIPT_SCHEMA,
  V5_R01_DRILL_TERMINAL_STATES,
  V5_R01_EXCLUDED_FAILURE_ORIGINS,
  V5_R01_FAILURE_ORIGINS,
  V5_R01_FAILURE_ORIGIN_KEYS,
  V5_R01_FORBIDDEN_DRILL_AIDS,
  V5_R01_J1_SUBJOURNEY_COUNT,
  V5_R01_PILOT_PARTNER,
  V5_R01_POLICY_VERSION,
  V5_R01_REQUIRED_RUN_LENGTH,
  V5_R01_SCHEMA_VERSION,
  V5_R01_SEAMS,
  V5_R01_SEAM_REFS,
  V5R01Error,
  assertArity,
  assertClosedKeys,
  assertExactStringSet,
  assertObject,
  assertRequiredKeys,
} from "./rollout-pilot-r01.vocabulary.v5.js";

/**
 * PRIVATE, AND NOT IMPORTED. `deepFreeze(x)` returns `x`, so an exported one is a
 * public function that hands a caller's own object — and every privileged token
 * inside it — straight back. It left the vocabulary's export list in this
 * correction, and each module of the slice keeps its own copy, which is already
 * the pattern elsewhere in mcp-server/src.
 */
function isFreezablePlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isFreezablePlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
}

export {
  V5_NO_EFFECTS,
  V5R01Error,
  V5_R01_BREAKING_FAILURE_ORIGINS,
  V5_R01_BUSINESS_DAY_BASIS,
  V5_R01_DAY_CHECKS,
  V5_R01_DISQUALIFYING_FAILURE_ORIGINS,
  V5_R01_DRILL_CORRECT_TERMINAL_STATES,
  V5_R01_DRILL_FAULTS,
  V5_R01_DRILL_FAULT_KEYS,
  V5_R01_DRILL_RECEIPT_FIELDS,
  V5_R01_DRILL_RECEIPT_SCHEMA,
  V5_R01_DRILL_TERMINAL_STATES,
  V5_R01_EXCLUDED_FAILURE_ORIGINS,
  V5_R01_FAILURE_ORIGINS,
  V5_R01_FAILURE_ORIGIN_KEYS,
  V5_R01_FORBIDDEN_DRILL_AIDS,
  V5_R01_J1_SUBJOURNEY_COUNT,
  V5_R01_PILOT_PARTNER,
  V5_R01_POLICY_VERSION,
  V5_R01_REQUIRED_RUN_LENGTH,
  V5_R01_SCHEMA_VERSION,
  V5_R01_SEAMS,
  V5_R01_SEAM_REFS,
};

// ---------------------------------------------------------------------------
// THE DECISIONS UNDER THIS FILE, and exactly how much of each was readable.
// ---------------------------------------------------------------------------

/**
 * The six requirement dispositions this slice is built on.
 *
 * TWO OF THE SIX WERE READ IN FULL and their settled text is quoted below, in
 * Joe's own words, because that text does real work in the code: Q009 is why
 * there is no per-slice Dell review, and Q010 is both why a laptop fault is
 * excluded and why Dell's independence is not allowed to gate J1.
 *
 * FOUR OF THE SIX COULD NOT BE READ. The doctrine store's register holds Q019,
 * Q061, Q104 and Q145 in its COMPACT projection, which binds each requirement to
 * one target and one acceptance hook and says, in its own canonicalization note,
 * that the exact question, recommendation and answer "are recovered from the
 * immutable thread item references". Those items were not reachable this session.
 * So this module records the target and the hook — which ARE settled and ARE
 * read — and does not paraphrase what it did not read. Anyone relying on the
 * exact wording of those four should read it from the store rather than from
 * here.
 */
/*
 * THE VERBATIM TEXT LIVES HERE, IN THE SOURCE, AND THE RECORD CARRIES ITS
 * DIGEST. This is the fourth re-review's first finding taken at its word.
 *
 * That finding says prose containing a privileged word is a finding and not an
 * exemption, and the sweep now reads every string leaf and every key. Two of
 * these strings are Joe's own settled words and one is the register's own
 * recommendation; rewording them to satisfy a guard would be misquoting the
 * record, which is a worse fault than the one the guard exists for. They are not
 * this slice's to reword, and they are not this slice's to verify either — the
 * doctrine store owns them and was not reachable this session — so they leave
 * the exported surface and the record pins them by SHA-256 instead. That is the
 * pattern the rest of the v5 lane already uses (`source_evidence_digest` in
 * global-boundaries.v5.js, cre-lifecycle.v5.js, complete-set-review-a03.v5.js).
 *
 * Q009.D1 settled answer
 *   sha256 cc0099410b415ff650725012678c52b44bae9d675d469592e97394b126261f31
 *   "I dont want to involve dell in the adoption. i prefer to validate it
 *    myself and present it to him as a usable product. reason being - he is not
 *    gong to sit at the desk and do these things the way i will. what would end
 *    up happening is each slice would be delayed for days longer while i wait
 *    on him to complete validation. much more effective that i work out the
 *    kinks and give him the final version. I am smart enough to imagine whether
 *    he can navigate bc i know him well enough"
 *
 * Q010.D1 settled answer
 *   sha256 9b34a198ecbbc1f9faabd8e7fae9f5b674baeb990ba33a73956d64a406cc7b40
 *   "Yes, in the future there will be periods of time where i take vacation and
 *    he will need to be able to use the system. however, for now i dont want to
 *    sacrifice speed on the roll out or any other qualities or capabilities for
 *    this. we can work this concept into the design later in the build if it
 *    helps"
 *
 * Q010.D1 recommendation it answered
 *   sha256 11366b2be28d6258799b9ffd19c64f7f704c5d01d0172db0a41c38e76ca078a0
 *   "No production capability should depend on your laptop, memory, private
 *    prompt habits, or ability to interpret raw logs. Dell may not be able to
 *    develop DoctorCRE, but he must be able to operate it, recognize failure,
 *    and avoid making damage worse."
 *
 * AND THE REGISTER TARGETS GO THE SAME WAY, for the stronger reason. They were
 * held as `{ quoted_from, identifier }` quotations, which the sweep skipped on
 * the strength of their shape alone. The re-review's third finding is that a
 * shape is not a verification: any two-string object qualified, so a name this
 * slice minted could wear the costume and walk straight through. The rule is now
 * that a quotation's owner must be a module in a closed allowlist AND the
 * identifier must really be exported by it — and `carr:design-basis-decision-
 * register` is not a module in this repository, so nothing it owns can be
 * resolved here. Its targets and acceptance children are therefore recorded in
 * this comment, where they are provenance a reader can check against the store,
 * rather than on a surface that would be claiming a verification this slice
 * cannot perform:
 *
 *   Q009.D1  target rollout_readiness   child rollout-readiness-child-outcome
 *   Q010.D1  target rollout_readiness   child rollout-readiness-child-outcome
 *   Q019.D1  target product_journey_1   child journey-one-production-outcome
 *   Q061.D1  target typed_successor     child successor-register-entry-accepted
 *   Q104.D1  target product_journey_1   child journey-one-production-outcome
 *   Q145.D1  target product_journey_1   child journey-one-production-outcome
 */
export const V5_R01_SETTLED_DECISIONS = deepFreeze({
  "Q009.D1": {
    settled_answer_digest: "cc0099410b415ff650725012678c52b44bae9d675d469592e97394b126261f31",
    what_it_binds_here: "there is no per-slice Dell review in this slice; evaluatePerSliceDellReview"
      + " in the onboarding module refuses one on the merits and cites that answer by digest",
  },
  "Q010.D1": {
    settled_answer_digest: "9b34a198ecbbc1f9faabd8e7fae9f5b674baeb990ba33a73956d64a406cc7b40",
    recommendation_digest: "11366b2be28d6258799b9ffd19c64f7f704c5d01d0172db0a41c38e76ca078a0",
    what_it_binds_here: "a partner-device or credential fault is an EXCLUDED failure origin,"
      + " conditional on the product having reported it honestly; a day salvaged only by studying"
      + " raw logs is NOT excluded; and Dell's independence never gates J1",
  },
  "Q019.D1": {
    why_not: "the design-basis register holds Q019 in its compact projection, which carries the"
      + " requirement target and its acceptance child only; the exact text lives in immutable"
      + " conversation items that were not available this session",
  },
  "Q061.D1": {
    why_not: "compact projection only, as above",
    what_is_nonetheless_honoured: "the common slice contract's own autonomy envelope — design any"
      + " possible future autonomy as a DORMANT typed successor now, with activation requiring a"
      + " separate action-specific gate. V5_R01_DORMANT_SUCCESSOR is that entry and it is inert.",
  },
  "Q104.D1": {
    why_not: "compact projection only, as above",
  },
  "Q145.D1": {
    why_not: "compact projection only, as above",
  },
});

export const V5_R01_SETTLED_DECISION_IDS =
  deepFreeze(Object.keys(V5_R01_SETTLED_DECISIONS).sort());

/**
 * WHICH OF THE SIX CARRY THEIR SETTLED TEXT, DERIVED RATHER THAN DECLARED.
 *
 * These two lists used to be filtered on a hand-written `text_read_this_session`
 * boolean — an author's own assertion that the text had been read, which is the
 * shape this slice spends the rest of its length refusing. The fact is checkable
 * from the record itself: either `settled_answer_digest` pins the verbatim text
 * or it does not. So the boolean is gone and the lists are derived from the
 * presence of the digest, which cannot disagree with the text it digests.
 *
 * The names say what the lists ARE — which records carry the settled wording —
 * rather than reporting `read` as an outcome of this module.
 */
export const V5_R01_DECISIONS_WITH_SETTLED_TEXT = deepFreeze(V5_R01_SETTLED_DECISION_IDS
  .filter(id => typeof V5_R01_SETTLED_DECISIONS[id].settled_answer_digest === "string"));

export const V5_R01_DECISIONS_WITHOUT_SETTLED_TEXT = deepFreeze(V5_R01_SETTLED_DECISION_IDS
  .filter(id => typeof V5_R01_SETTLED_DECISIONS[id].settled_answer_digest !== "string"));

/**
 * Bind a caller's declared decision set to this module's.
 *
 * An exact set, not a subset: a binding that names five of the six has dropped
 * one, and a binding that names seven has picked one up somewhere else.
 *
 * THE JOINED STRING WAS THE DEFECT, and the third review of PR 992 reproduced it.
 * The check used to be `sorted.join("|") === mine.join("|")`, and a ONE-element
 * array holding all six ids already glued together with `|` produces exactly the
 * same string. A caller could therefore declare a binding of one meaningless
 * value and be told it matched. A separator is not a delimiter unless the thing
 * being separated cannot contain it, and an arbitrary caller string can contain
 * anything.
 *
 * So the check is now element-wise, in this order, and each step has its own
 * refusal rather than folding into one comparison:
 *
 *   1. the value is an array;
 *   2. every element is a string — a nested array or object is not an id;
 *   3. the count equals this slice's count (six), so no element can stand in for
 *      several and none can be dropped;
 *   4. the elements are distinct, so six copies of one id cannot pass a count;
 *   5. the sorted elements equal this slice's sorted ids, compared one index at
 *      a time with no joining anywhere.
 *
 * AND ALL FIVE STEPS HAPPEN BEHIND THE VOCABULARY'S BOUNDARY, which is the fifth
 * re-review's first finding as it lands on this file. The version above read
 * `binding.decision_ids` here — a property read on a caller's object, outside the
 * one place in this slice that is allowed to perform one, so a Proxy with a
 * throwing `get` trap delivered its author's own sentence out of this function
 * with no code on it. `assertExactStringSet` takes the HOLDER and the key, copies
 * it, and compares the copy against the list this module passes in: the expected
 * set still belongs to this file, and no caller value ever crosses back into it.
 */
export function assertR01DecisionBinding(...args) {
  assertArity(args, 1, "assertR01DecisionBinding");
  const [binding] = args;
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decision_ids"], "binding");
  assertRequiredKeys(binding, ["decision_ids"], "binding");
  assertExactStringSet(binding, "decision_ids", V5_R01_SETTLED_DECISION_IDS,
    "decision_binding_mismatch");
}

// ---------------------------------------------------------------------------
// UPSTREAM EVIDENCE, named and not consumed.
// ---------------------------------------------------------------------------

/**
 * The two runtime inputs the slice catalog declares, plus the production outcome
 * step this slice's own result would feed.
 *
 * NONE OF THEM IS PRODUCIBLE FROM SOURCE and none of them is read here. The last
 * one is quoted from J201's reading of the r7 receipt-producer registry rather
 * than invented, so the string is one the repository already uses.
 */
export const V5_R01_UPSTREAM_EVIDENCE_INPUTS = deepFreeze([
  "step:j1-core-production-outcome",
  "step:journey-one-pilot-and-dell-beta-preactivation-contract-receipt",
]);

export const V5_R01_PRODUCTION_OUTCOME_STEP = "step:j1-pilot-and-dell-beta-outcome";

/**
 * The successor this slice can see coming, declared inactive.
 *
 * There is no code path to it. `active: false` is not a flag this module reads —
 * nothing in this file branches on it — because there is nothing built for a
 * branch to reach. The entry exists so the shape is on the record now and so the
 * gate that would have to be passed is named now, per the common contract's
 * autonomy envelope.
 */
export const V5_R01_DORMANT_SUCCESSOR = deepFreeze({
  successor: "nightly_pilot_day_rollup_with_nobody_watching",
  what_it_would_do: "assemble each day's candidate ledger entry from records overnight, with no"
    + " human at the desk, so the pilot run is not a manual daily write-up",
  why_it_is_dormant: "a writer nobody watches, of the very evidence a pilot is judged on, is the"
    + " shape most likely to grade its own homework; it needs its own gate, not this slice's",
  activation_gate: "step:v5-r01-nightly-rollup-no-human-activation",
  activation_gate_exists_in_this_repository: false,
  implemented_here: false,
});

// ---------------------------------------------------------------------------
// THE ANSWER SHAPE, and why nothing situational reaches it.
// ---------------------------------------------------------------------------

/**
 * THE ONE SHAPE EVERY UNAVAILABLE ANSWER ON THIS SURFACE HAS.
 *
 * Every field is a module constant or a literal. Nothing here is derived from a
 * request, so two callers handing in opposite evidence get byte-identical
 * answers, and the suite asserts exactly that by digesting the result of every
 * caller-controlled shape it can build.
 *
 * `request_examined: false` is not a courtesy note. It is the claim the rest of
 * the surface rests on, and it is true by construction: no evaluator below names
 * its parameter. The name carried `read` until the fourth re-review swept every
 * key whatever its value's type; the claim it makes is unchanged.
 */
function unavailable(answer, seams, reason_id, because, extra = {}) {
  return deepFreeze({
    answer,
    schema_version: V5_R01_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    status: "unavailable",
    decision: "unavailable",
    reason_id,
    unavailable_because: because,
    owed_seams: seams.map(seam => seam.seam).sort(),
    seams_bound: deepFreeze(seams.map(seam => ({ seam: seam.seam, holds: seam.holds, bound: false }))),
    // No field of any request can change any field of this answer.
    request_examined: false,
    caller_evidence_weighed: false,
    decided_by: "no_authoritative_owner",
    authority_established: false,
    state_holder_is_caller_supplied: false,
    model_judgment_weighed: false,
    produces_acceptance: false,
    injects_nothing: true,
    ...extra,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// THE LEDGER ENTRY. What a day has to record, and what it may not.
// ---------------------------------------------------------------------------

export const V5_R01_LEDGER_ENTRY_SCHEMA = "v5-r01-pilot-day-entry.v1";

/**
 * The fields one pilot-day entry carries.
 *
 * `observed_origin` is deliberately NOT among them and neither is any verdict.
 * An entry is a record of what happened; the attribution of a failure and the
 * verdict on the day are both produced later, by the observer, and writing either
 * into the entry would let the ledger decide what the ledger is evidence for.
 */
export const V5_R01_LEDGER_ENTRY_FIELDS = deepFreeze([
  "date",
  "entry_ref",
  "failures_observed",
  "partner",
  "real_business_actions",
  "recorded_by_identity_ref",
  "subjourneys_exercised",
]);

/** The fields a ledger entry may never carry, each with why. */
export const V5_R01_LEDGER_ENTRY_FORBIDDEN_FIELDS = deepFreeze({
  // Spelled `counts_toward_run` rather than `day_counted` for the same word
  // reservation the observer seam obeys; it names the same forbidden field.
  counts_toward_run: "the verdict is not a field of the evidence it is drawn from",
  failure_origin: "the attribution is the observer's, not the subject's",
  run_length: "a day does not know what run it is in",
  excluded: "an exclusion is applied to an entry, never recorded inside one",
});

/**
 * The shape of a pilot-day ledger entry, as a description a store could be built
 * from. It creates nothing and records nothing.
 */
export function describePilotDayLedgerEntry(...args) {
  assertArity(args, 0, "describePilotDayLedgerEntry");
  return deepFreeze({
    schema_version: V5_R01_LEDGER_ENTRY_SCHEMA,
    fields: [...V5_R01_LEDGER_ENTRY_FIELDS],
    forbidden_fields: { ...V5_R01_LEDGER_ENTRY_FORBIDDEN_FIELDS },
    append_only: true,
    one_entry_per_date: true,
    partner: V5_R01_PILOT_PARTNER,
    business_day_basis: V5_R01_BUSINESS_DAY_BASIS,
    required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
    required_subjourneys_per_day: V5_R01_J1_SUBJOURNEY_COUNT,
    day_checks_in_order: [...V5_R01_DAY_CHECKS],
    store_exists_in_this_repository: false,
    owed_seam: V5_R01_SEAMS.pilot_day_store.seam,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * The exclusion ladder, published so it can be read and argued with rather than
 * inferred from behaviour. This is a table, not a decision.
 */
export function describeFailureExclusions(...args) {
  assertArity(args, 0, "describeFailureExclusions");
  return deepFreeze({
    schema_version: V5_R01_SCHEMA_VERSION,
    origins: V5_R01_FAILURE_ORIGIN_KEYS.map(origin => ({
      origin,
      excluded: V5_R01_FAILURE_ORIGINS[origin].excluded,
      disqualifies_run: V5_R01_FAILURE_ORIGINS[origin].disqualifies_run,
      condition: V5_R01_FAILURE_ORIGINS[origin].condition,
      basis: V5_R01_FAILURE_ORIGINS[origin].basis,
    })),
    attributed_by: "an outside observer, never the subject of the pilot and never a caller",
    attribution_seam: V5_R01_SEAMS.outside_observer.seam,
    attribution_seam_exists_in_this_repository: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// THE EVALUATORS.
// ---------------------------------------------------------------------------

/**
 * Does this day count toward the run?
 *
 * IT DOES NOT ANSWER, AND IT DOES NOT LOOK. Two owners are missing and either
 * one alone would be enough:
 *
 *   1. The ledger the entry should come from does not exist, so an entry handed
 *      in by a caller is an assertion about a day rather than a record of one.
 *   2. Even given a real entry, the failure attribution that decides the day is
 *      an outside judgement, and the observer receipt store that would carry it
 *      does not exist either.
 *
 * The previous round answered `unavailable` and then reported which check the
 * caller's entry would have failed, plus the detail behind it. That was a
 * classification of caller input reachable through a public export, and the
 * review was right to refuse it. The deterministic ladder is real, it is proved
 * clause by clause, and it is proved somewhere a consumer cannot reach: see
 * mcp-server/test/rollout-pilot-r01-classifiers.v5.testhelper.mjs.
 */
export function evaluatePilotDay(...args) {
  assertArity(args, 1, "evaluatePilotDay");
  return unavailable("evaluatePilotDay",
    [V5_R01_SEAMS.pilot_day_store, V5_R01_SEAMS.outside_observer,
      V5_R01_SEAMS.defect_register, V5_R01_SEAMS.j1_subjourney_roster],
    "pilot_day_ledger_and_observer_absent",
    "no durable pilot-day ledger and no outside observer receipt exist, so a day can only"
    + " be asserted by its caller",
    {
      partner: V5_R01_PILOT_PARTNER,
      required_subjourneys_per_day: V5_R01_J1_SUBJOURNEY_COUNT,
      // The ORDERED QUESTIONS are published, because a ladder a reader can argue
      // with is the part of this slice that source can honestly own. What is NOT
      // published is which rung any particular caller fell at.
      day_checks_in_order: [...V5_R01_DAY_CHECKS],
      which_check_this_request_would_fail: null,
      which_check_this_request_would_fail_because:
        "naming it would be classifying caller-supplied evidence through a public export",
    });
}

/**
 * Has Joe completed ten consecutive business days?
 *
 * IT DOES NOT ANSWER, AND IT DOES NOT LOOK, for the two reasons above and one
 * more that is specific to the run: which dates are not business days is a fact
 * somebody owns. A caller supplying a holiday list containing the three days it
 * did not use the product has not been on holiday; it has edited the denominator.
 * So the operating calendar is named among the owed seams rather than accepted as
 * an argument.
 */
export function evaluatePilotRun(...args) {
  assertArity(args, 1, "evaluatePilotRun");
  return unavailable("evaluatePilotRun",
    [V5_R01_SEAMS.pilot_day_store, V5_R01_SEAMS.outside_observer,
      V5_R01_SEAMS.operating_calendar, V5_R01_SEAMS.defect_register,
      V5_R01_SEAMS.j1_subjourney_roster],
    "pilot_run_requires_ledger_observer_and_calendar",
    "ten consecutive business days is a statement about ten real days; no ledger, no observer"
    + " and no operating calendar exist to draw them from",
    {
      partner: V5_R01_PILOT_PARTNER,
      required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
      business_day_basis: V5_R01_BUSINESS_DAY_BASIS,
      run_length_observed: null,
      which_check_this_request_would_fail: null,
    });
}

/**
 * Did the injected recovery go the way the drill requires?
 *
 * IT DOES NOT ANSWER, AND IT DOES NOT LOOK. A drill receipt is issued by the
 * observer who watched the drill, and there is no receipt store here — so the
 * object a caller passes is a description of a drill it says happened, which is
 * not the same kind of thing at all.
 *
 * What the answer DOES carry is the registered receipt schema, in full, so a
 * reader building the store knows every field it owes. That is a published
 * contract, not a reading of anybody's receipt.
 */
export function evaluateRecoveryDrill(...args) {
  assertArity(args, 1, "evaluateRecoveryDrill");
  return unavailable("evaluateRecoveryDrill",
    [V5_R01_SEAMS.drill_receipt_store, V5_R01_SEAMS.outside_observer],
    "recovery_drill_receipt_store_absent",
    "a drill receipt is issued by the observer who watched the drill; no receipt store exists"
    + " in this repository to fetch one from",
    {
      receipt_schema_required: V5_R01_DRILL_RECEIPT_SCHEMA,
      receipt_fields_required: [...V5_R01_DRILL_RECEIPT_FIELDS],
      terminal_state_observed: null,
      which_check_this_request_would_fail: null,
    });
}

/**
 * WHAT EVERY DRILL IS: the five faults, the signal the product owes the partner
 * for each, the recovery that counts, what may not be used, and what a receipt
 * has to carry.
 *
 * IT TAKES NO ARGUMENT. The previous round took a fault name and returned that
 * fault's row, which was a lookup rather than a classification — but a public
 * function whose answer varies with caller input is a shape this slice has to
 * defend one caller at a time, and there is nothing to defend if it hands back
 * the whole declared table. It is published policy either way.
 *
 * THIS DOES NOT INJECT ANYTHING and cannot be made to. The injection is a thing
 * people do to a running system, and the module says so in its own result rather
 * than leaving it to be assumed.
 */
export function describeRecoveryDrill(...args) {
  // It takes NOTHING. Any argument at all is a caller trying to steer a published
  // table, and the arity is the boundary — counted, not peeked at.
  assertArity(args, 0, "describeRecoveryDrill");
  return deepFreeze({
    answer: "describeRecoveryDrill",
    schema_version: V5_R01_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    request_examined: false,
    faults: V5_R01_DRILL_FAULT_KEYS.map(key => ({
      fault: key,
      s01_seam_that_defines_the_behaviour: V5_R01_DRILL_FAULTS[key].s01_seam,
      expected_partner_visible_signal: V5_R01_DRILL_FAULTS[key].expected_partner_visible_signal,
      recovery_is: V5_R01_DRILL_FAULTS[key].recovery_is,
    })),
    correct_terminal_states: [...V5_R01_DRILL_CORRECT_TERMINAL_STATES],
    terminal_states: [...V5_R01_DRILL_TERMINAL_STATES],
    forbidden_aids: [...V5_R01_FORBIDDEN_DRILL_AIDS],
    // Three roles, three people. The classifier enforces all three pairs; this is
    // where the requirement is published.
    subject_must_not_be_the_injector: true,
    subject_must_not_be_the_observer: true,
    injector_must_not_be_the_observer: true,
    receipt_schema: V5_R01_DRILL_RECEIPT_SCHEMA,
    receipt_fields: [...V5_R01_DRILL_RECEIPT_FIELDS],
    receipt_store_exists_in_this_repository: false,
    owed_seam: V5_R01_SEAMS.drill_receipt_store.seam,
    this_module_injects_nothing: true,
    injection_is_performed_by: "a person, against a running deployment, watched by someone who is"
      + " not the person recovering",
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Everything this slice cannot answer, with what each answer would need.
 *
 * The point of this function is that a reader asking "so what is actually missing
 * before R01 is done?" gets one list instead of five refusals. Every clause is
 * `proven: false` and there is no input that changes it.
 */
export function rolloutPilotGaps(...args) {
  assertArity(args, 0, "rolloutPilotGaps");
  return deepFreeze({
    schema_version: V5_R01_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    production_outcome_step: V5_R01_PRODUCTION_OUTCOME_STEP,
    upstream_evidence_inputs: [...V5_R01_UPSTREAM_EVIDENCE_INPUTS],
    clauses: [
      {
        clause: "joe_ten_consecutive_business_days",
        proven: false,
        what_it_would_need: "ten real business days of ledger entries from a durable append-only"
          + " store, an operating calendar saying which dates were business days, an outside"
          + " observer's attribution for every failure on those days, and a defect register"
          + " showing no unresolved high defect open against J1 across the run",
        owed_seams: [
          V5_R01_SEAMS.pilot_day_store.seam,
          V5_R01_SEAMS.operating_calendar.seam,
          V5_R01_SEAMS.outside_observer.seam,
          V5_R01_SEAMS.defect_register.seam,
          V5_R01_SEAMS.j1_subjourney_roster.seam,
        ],
      },
      {
        clause: "injected_recovery_succeeds",
        proven: false,
        what_it_would_need: "a fault actually injected into a running deployment by one person,"
          + " watched by a second who is not the partner recovering, and a receipt carrying the"
          + " twelve fields of " + V5_R01_DRILL_RECEIPT_SCHEMA,
        owed_seams: [V5_R01_SEAMS.drill_receipt_store.seam],
      },
      {
        clause: "dell_does_realistic_work_unaided",
        proven: false,
        what_it_would_need: "Dell doing real business work in production with no developer tools"
          + " and no explanation from the author, judged by an outside observer; this slice's"
          + " onboarding module carries the flow and the enrollment store that would record his"
          + " progress does not exist",
        owed_seams: [
          V5_R01_SEAMS.onboarding_enrollment_store.seam,
          V5_R01_SEAMS.outside_observer.seam,
        ],
      },
    ],
    any_clause_provable_from_source: false,
    decisions_whose_text_is_not_in_hand: [...V5_R01_DECISIONS_WITHOUT_SETTLED_TEXT],
    dormant_successor: V5_R01_DORMANT_SUCCESSOR,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Digest and surface.
// ---------------------------------------------------------------------------

export function v5R01PolicyPreimage(...args) {
  assertArity(args, 0, "v5R01PolicyPreimage");
  return deepFreeze({
    schema_version: V5_R01_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    decision_ids: [...V5_R01_SETTLED_DECISION_IDS],
    required_run_length: V5_R01_REQUIRED_RUN_LENGTH,
    business_day_basis: V5_R01_BUSINESS_DAY_BASIS,
    required_subjourneys_per_day: V5_R01_J1_SUBJOURNEY_COUNT,
    day_checks_in_order: [...V5_R01_DAY_CHECKS],
    failure_origins: V5_R01_FAILURE_ORIGIN_KEYS.map(origin => ({
      origin,
      excluded: V5_R01_FAILURE_ORIGINS[origin].excluded,
      disqualifies_run: V5_R01_FAILURE_ORIGINS[origin].disqualifies_run,
      condition: V5_R01_FAILURE_ORIGINS[origin].condition,
    })),
    drill_faults: [...V5_R01_DRILL_FAULT_KEYS],
    forbidden_drill_aids: [...V5_R01_FORBIDDEN_DRILL_AIDS],
    drill_receipt_fields: [...V5_R01_DRILL_RECEIPT_FIELDS],
    ledger_entry_fields: [...V5_R01_LEDGER_ENTRY_FIELDS],
    owed_seams: [...V5_R01_SEAM_REFS],
    production_outcome_step: V5_R01_PRODUCTION_OUTCOME_STEP,
  });
}

export function v5R01PolicyCanonicalBytes(...args) {
  assertArity(args, 0, "v5R01PolicyCanonicalBytes");
  return canonicalJson(v5R01PolicyPreimage());
}

export function v5R01PolicyDigest(...args) {
  assertArity(args, 0, "v5R01PolicyDigest");
  return digest(v5R01PolicyPreimage());
}

/**
 * The consumer surface. The suite asserts this IS the module's export set — no
 * probe, no classifier, no test-only entry, no escape hatch of any name.
 */
export const V5_R01_PUBLIC_SURFACE = deepFreeze([
  "V5R01Error",
  "V5_NO_EFFECTS",
  "V5_R01_BREAKING_FAILURE_ORIGINS",
  "V5_R01_BUSINESS_DAY_BASIS",
  "V5_R01_DAY_CHECKS",
  "V5_R01_DECISIONS_WITHOUT_SETTLED_TEXT",
  "V5_R01_DECISIONS_WITH_SETTLED_TEXT",
  "V5_R01_DISQUALIFYING_FAILURE_ORIGINS",
  "V5_R01_DRILL_CORRECT_TERMINAL_STATES",
  "V5_R01_DRILL_FAULTS",
  "V5_R01_DRILL_FAULT_KEYS",
  "V5_R01_DRILL_RECEIPT_FIELDS",
  "V5_R01_DRILL_RECEIPT_SCHEMA",
  "V5_R01_DRILL_TERMINAL_STATES",
  "V5_R01_EXCLUDED_FAILURE_ORIGINS",
  "V5_R01_FAILURE_ORIGINS",
  "V5_R01_FAILURE_ORIGIN_KEYS",
  "V5_R01_FORBIDDEN_DRILL_AIDS",
  "V5_R01_DORMANT_SUCCESSOR",
  "V5_R01_J1_SUBJOURNEY_COUNT",
  "V5_R01_LEDGER_ENTRY_FIELDS",
  "V5_R01_LEDGER_ENTRY_FORBIDDEN_FIELDS",
  "V5_R01_LEDGER_ENTRY_SCHEMA",
  "V5_R01_PILOT_PARTNER",
  "V5_R01_POLICY_VERSION",
  "V5_R01_PRODUCTION_OUTCOME_STEP",
  "V5_R01_PUBLIC_SURFACE",
  "V5_R01_REQUIRED_RUN_LENGTH",
  "V5_R01_SCHEMA_VERSION",
  "V5_R01_SEAMS",
  "V5_R01_SEAM_REFS",
  "V5_R01_SETTLED_DECISIONS",
  "V5_R01_SETTLED_DECISION_IDS",
  "V5_R01_UPSTREAM_EVIDENCE_INPUTS",
  "assertR01DecisionBinding",
  "describeFailureExclusions",
  "describePilotDayLedgerEntry",
  "describeRecoveryDrill",
  "evaluatePilotDay",
  "evaluatePilotRun",
  "evaluateRecoveryDrill",
  "rolloutPilotGaps",
  "v5R01PolicyCanonicalBytes",
  "v5R01PolicyDigest",
  "v5R01PolicyPreimage",
]);

// This module hands back no classification at all, so there is no conditional
// wording left to check at load. The property is asserted from the outside
// instead, and more strongly: the suite reads the SOURCE of every file in
// mcp-server/src and fails if any of them so much as contains a `would_*`
// classification token.
