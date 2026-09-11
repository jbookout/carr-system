// DoctorCRE v5 slice V5-R01 — the product-like onboarding flow.
//
// Four kinds of test:
//
//   FLOW tests prove the declaration: nine ordered steps, every one resumable,
//   every one reachable with a browser, the product UI or a telephone, and none
//   of them needing a shell, a checkout, a database client or a raw log.
//
//   MOBILITY tests prove the candid exposure — three of the nine steps sit on a
//   surface that carries the phone bar today and the other six do not, reported
//   as a NAVIGATION gap and not overstated into a rendering claim.
//
//   PIN tests prove this slice's copy of J101's surface table still equals
//   J101's own AUTHENTICATED_SURFACES, in both directions.
//
//   REFUSAL and GUARD tests prove the standing rule, the same way the pilot
//   suite does: nobody completes their own onboarding by saying they did.

import assert from "node:assert/strict";
import { test } from "node:test";

import * as onboarding from "../src/onboarding-flow-r01.v5.js";
import {
  AUTHENTICATED_SURFACES,
  COMMAND_ENDPOINT,
} from "../src/workspace-surface-inventory.js";
import {
  V5_R01_BETA_PARTNER,
  V5_R01_CLASSIFICATIONS,
  V5_R01_FORBIDDEN_TOOL_CLASSES,
  V5_R01_MOBILE_REACH,
  V5_R01_PERMITTED_TOOL_CLASSES,
  V5_R01_PRIVILEGED_OUTCOMES,
  V5_R01_SEAMS,
  collectStrings,
} from "../src/rollout-pilot-r01.vocabulary.v5.js";
import {
  classifyBetaOperabilityIfAuthoritative,
  classifyOnboardingIfAuthoritative,
} from "../src/rollout-pilot-r01.internal.v5.js";

const {
  V5R01Error, V5_R01_COMMAND_ENDPOINT, V5_R01_ONBOARDING_PUBLIC_SURFACE,
  V5_R01_ONBOARDING_STEPS, V5_R01_ONBOARDING_STEP_IDS, V5_R01_SURFACE_PHONE_NAV,
  evaluateBetaOperability, evaluatePerSliceDellReview, onboardingMobileExposure,
  readOnboardingProgress, v5R01OnboardingDigest, v5R01OnboardingPreimage,
} = onboarding;

const ALL_STEPS = Object.freeze([...V5_R01_ONBOARDING_STEP_IDS]);
const PRIVILEGED_VALUES = new Set(V5_R01_PRIVILEGED_OUTCOMES);

function progress(overrides = {}) {
  return {
    partner: V5_R01_BETA_PARTNER,
    steps_completed: [...ALL_STEPS],
    tool_classes_used: ["authenticated_browser", "product_ui", "telephone"],
    ...overrides,
  };
}

function beta(overrides = {}) {
  return {
    ...progress(),
    realistic_work_items: ["deal:2001", "vendor:33"],
    author_interventions: 0,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// FLOW.
// ---------------------------------------------------------------------------

test("flow: nine ordered steps covering the slice's own included scope", () => {
  assert.deepEqual([...V5_R01_ONBOARDING_STEP_IDS], [
    "sign_in", "read_home", "open_a_client", "open_a_deal", "read_an_assignment",
    "ask_doc", "make_a_safe_update", "inspect_the_receipts", "use_documented_fallback",
  ]);
  V5_R01_ONBOARDING_STEPS.forEach((step, index) => {
    assert.equal(step.ordinal, index + 1, step.step);
    assert.equal(typeof step.plain, "string", step.step);
    assert.equal(step.plain.length > 20, true, `${step.step} has no plain-language line`);
  });
});

test("flow: no step requires a developer tool, and every permitted tool is registered", () => {
  for (const step of V5_R01_ONBOARDING_STEPS) {
    assert.equal(step.requires_tool_classes.length > 0, true, step.step);
    for (const tool of step.requires_tool_classes) {
      assert.equal(V5_R01_FORBIDDEN_TOOL_CLASSES.includes(tool), false, `${step.step}: ${tool}`);
      assert.equal(V5_R01_PERMITTED_TOOL_CLASSES.includes(tool), true, `${step.step}: ${tool}`);
    }
  }
  // And the two lists are disjoint, so "permitted" cannot quietly grow to include
  // something the forbidden list also names.
  for (const tool of V5_R01_PERMITTED_TOOL_CLASSES) {
    assert.equal(V5_R01_FORBIDDEN_TOOL_CLASSES.includes(tool), false, tool);
  }
});

test("flow: every step is resumable and says what a resume record would hold", () => {
  for (const step of V5_R01_ONBOARDING_STEPS) {
    assert.equal(step.resumable, true, step.step);
    assert.equal(typeof step.resume_token_holds, "string", step.step);
    assert.equal(step.resume_token_holds.length > 0, true, step.step);
  }
});

test("flow: the two off-surface steps are the two that genuinely are", () => {
  const offSurface = V5_R01_ONBOARDING_STEPS.filter(step => step.asset === null).map(s => s.step);
  assert.deepEqual(offSurface, ["ask_doc", "use_documented_fallback"]);
  const askDoc = V5_R01_ONBOARDING_STEPS.find(step => step.step === "ask_doc");
  assert.deepEqual(askDoc.routes, [V5_R01_COMMAND_ENDPOINT]);
  const fallback = V5_R01_ONBOARDING_STEPS.find(step => step.step === "use_documented_fallback");
  assert.deepEqual(fallback.routes, [], "the fallback is off the product entirely");
  assert.deepEqual(fallback.requires_tool_classes, ["telephone"]);
});

test("flow: every step's mobile reach is one of the three registered states", () => {
  for (const step of V5_R01_ONBOARDING_STEPS) {
    assert.equal(V5_R01_MOBILE_REACH.includes(step.mobile_reach), true, step.step);
  }
});

// ---------------------------------------------------------------------------
// MOBILITY.
// ---------------------------------------------------------------------------

test("mobility: three of the nine steps are in the phone navigation today, and six are not", () => {
  const exposure = onboardingMobileExposure();
  assert.equal(exposure.total_steps, 9);
  assert.deepEqual(exposure.phone_navigable_steps, ["sign_in", "read_home", "open_a_client"]);
  assert.deepEqual(exposure.link_only_steps,
    ["open_a_deal", "read_an_assignment", "make_a_safe_update", "inspect_the_receipts"]);
  assert.deepEqual(exposure.off_surface_steps, ["ask_doc", "use_documented_fallback"]);
  assert.equal(
    exposure.phone_navigable_steps.length + exposure.link_only_steps.length
    + exposure.off_surface_steps.length, exposure.total_steps);
});

test("mobility: the surfaces with and without the phone bar are named, not counted", () => {
  const exposure = onboardingMobileExposure();
  assert.deepEqual(exposure.surfaces_with_phone_navigation, ["business.html", "workspace.html"]);
  assert.deepEqual(exposure.surfaces_without_phone_navigation,
    ["index.html", "leads.html", "queue.html", "room.html", "system-work.html"]);
});

test("mobility: the gap is stated as a navigation gap and not overstated", () => {
  const exposure = onboardingMobileExposure();
  assert.equal(exposure.what_link_only_means.includes("navigation bar"), true);
  assert.equal(exposure.what_link_only_means.includes("NOT a claim"), true,
    "the report must say what it is not claiming");
  assert.equal(exposure.open_question_this_module_cannot_answer.includes("phone width"), true);
  assert.equal(exposure.effects.creates_effect, false);
});

// ---------------------------------------------------------------------------
// PIN — this slice's copy of J101's table against J101's own.
// ---------------------------------------------------------------------------

test("pin: the surface table equals J101's AUTHENTICATED_SURFACES in both directions", () => {
  const mine = Object.keys(V5_R01_SURFACE_PHONE_NAV).sort();
  const theirs = AUTHENTICATED_SURFACES.map(surface => surface.asset).sort();
  assert.deepEqual(mine, theirs, "an asset added or dropped upstream must fail here");

  for (const surface of AUTHENTICATED_SURFACES) {
    const row = V5_R01_SURFACE_PHONE_NAV[surface.asset];
    assert.equal(row.mobile_nav, surface.mobile_nav,
      `${surface.asset}: the phone-bar flag disagrees with J101`);
    assert.deepEqual(row.routes, [...surface.routes],
      `${surface.asset}: the routes disagree with J101`);
  }
});

test("pin: the command endpoint equals J101's", () => {
  assert.equal(V5_R01_COMMAND_ENDPOINT, COMMAND_ENDPOINT);
});

test("pin: every step's asset is one J101 actually serves", () => {
  const known = new Set(AUTHENTICATED_SURFACES.map(surface => surface.asset));
  for (const step of V5_R01_ONBOARDING_STEPS) {
    if (step.asset === null) continue;
    assert.equal(known.has(step.asset), true, `${step.step} names ${step.asset}`);
  }
});

// ---------------------------------------------------------------------------
// LADDER — the classifier behind the refusals.
// ---------------------------------------------------------------------------

test("ladder: a partner through every step with permitted tools would be complete", () => {
  const verdict = classifyOnboardingIfAuthoritative(progress(), ALL_STEPS);
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.onboarding);
  assert.equal(verdict.blocking_check, null);
});

test("ladder: a developer tool is caught BEFORE the step count", () => {
  // Deliberately also missing every step, to prove the order: the answer must be
  // the terminal, not the outstanding steps.
  const verdict = classifyOnboardingIfAuthoritative(progress({
    steps_completed: [], tool_classes_used: ["shell_or_terminal"],
  }), ALL_STEPS);
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "onboarding_used_a_developer_tool");
  assert.deepEqual(verdict.developer_tools_used, ["shell_or_terminal"]);
});

test("ladder: every forbidden tool class fails onboarding, and the answer names which", () => {
  for (const tool of V5_R01_FORBIDDEN_TOOL_CLASSES) {
    const verdict = classifyOnboardingIfAuthoritative(
      progress({ tool_classes_used: ["product_ui", tool] }), ALL_STEPS);
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse, tool);
    assert.deepEqual(verdict.developer_tools_used, [tool]);
  }
});

test("ladder: one outstanding step is outstanding, and the answer names it", () => {
  const verdict = classifyOnboardingIfAuthoritative(
    progress({ steps_completed: ALL_STEPS.filter(step => step !== "ask_doc") }), ALL_STEPS);
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "onboarding_steps_outstanding");
  assert.deepEqual(verdict.outstanding_steps, ["ask_doc"]);
});

test("ladder: claiming a step the flow does not have is refused", () => {
  const verdict = classifyOnboardingIfAuthoritative(
    progress({ steps_completed: [...ALL_STEPS, "deploy_the_system"] }), ALL_STEPS);
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "onboarding_claims_an_unregistered_step");
  assert.deepEqual(verdict.unregistered_steps, ["deploy_the_system"]);
});

test("ladder: ONE author intervention ends independence — the threshold is zero", () => {
  // Q009: "his confusion is product evidence, not a training failure". So one
  // explanation converts a beta pass into a product finding, and there is no
  // small number of explanations that is still independent.
  const clean = classifyBetaOperabilityIfAuthoritative({
    partner: V5_R01_BETA_PARTNER,
    onboarding_classification: V5_R01_CLASSIFICATIONS.onboarding,
    realistic_work_items: ["deal:2001"],
    author_interventions: 0,
    tool_classes_used: ["product_ui"],
  });
  assert.equal(clean.classification, V5_R01_CLASSIFICATIONS.beta);

  const explained = classifyBetaOperabilityIfAuthoritative({
    partner: V5_R01_BETA_PARTNER,
    onboarding_classification: V5_R01_CLASSIFICATIONS.onboarding,
    realistic_work_items: ["deal:2001"],
    author_interventions: 1,
    tool_classes_used: ["product_ui"],
  });
  assert.equal(explained.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(explained.blocking_check, "author_explained_the_system");
  assert.equal(explained.author_interventions, 1);
});

test("ladder: a walkthrough with no realistic work is not independent operation", () => {
  const verdict = classifyBetaOperabilityIfAuthoritative({
    partner: V5_R01_BETA_PARTNER,
    onboarding_classification: V5_R01_CLASSIFICATIONS.onboarding,
    realistic_work_items: [],
    author_interventions: 0,
    tool_classes_used: ["product_ui"],
  });
  assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "no_realistic_work_performed");
});

test("ladder: a caller passing the onboarding classification as a word does not get past it", () => {
  // The classification is a chained input here, so the one string that would
  // unlock it is checked by exact identity, and nothing else is accepted.
  for (const forged of ["completed", "pass", "would_complete_onboarding", "onboarding_complete"]) {
    const verdict = classifyBetaOperabilityIfAuthoritative({
      partner: V5_R01_BETA_PARTNER,
      onboarding_classification: forged,
      realistic_work_items: ["deal:2001"],
      author_interventions: 0,
      tool_classes_used: ["product_ui"],
    });
    assert.equal(verdict.classification, V5_R01_CLASSIFICATIONS.refuse, forged);
    assert.equal(verdict.blocking_check, "onboarding_not_classified_complete", forged);
  }
});

// ---------------------------------------------------------------------------
// REFUSALS.
// ---------------------------------------------------------------------------

test("refusal: onboarding progress is unavailable and names the enrollment store", () => {
  const result = readOnboardingProgress(progress());
  assert.equal(result.decision, "unavailable");
  assert.equal(result.owed_seam, V5_R01_SEAMS.onboarding_enrollment_store.seam);
  assert.equal(result.authority_established, false);
  assert.equal(result.state_holder_is_caller_supplied, false);
  assert.equal(result.would_be_classified, V5_R01_CLASSIFICATIONS.onboarding,
    "a complete claim is still unavailable, and says what it WOULD have been");
  assert.equal(Object.isFrozen(result), true);
});

test("refusal: beta operability is unavailable and names the observer", () => {
  const result = evaluateBetaOperability(beta());
  assert.equal(result.decision, "unavailable");
  assert.equal(result.owed_seam, V5_R01_SEAMS.independent_observer.seam);
  assert.equal(result.beta_partner, V5_R01_BETA_PARTNER);
  assert.equal(result.would_be_classified, V5_R01_CLASSIFICATIONS.beta);
});

test("refusal: Dell's independence never gates J1", () => {
  // Q010: "for now i dont want to sacrifice speed on the roll out or any other
  // qualities or capabilities for this."
  for (const request of [beta(), beta({ author_interventions: 12 }), beta({ steps_completed: [] })]) {
    const result = evaluateBetaOperability(request);
    assert.equal(result.blocks_j1, false);
    assert.equal(result.blocks_j1_basis.includes("Q010"), true);
  }
});

test("refusal: a per-slice Dell review is refused ON THE MERITS, not for want of a store", () => {
  const result = evaluatePerSliceDellReview({ slice_ref: "V5-R01", requested_by: "actor:someone" });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "per_slice_dell_review_declined_by_q009");
  assert.equal(result.refused_on_the_merits, true);
  assert.equal(result.waiting_on_a_store, false);
  assert.equal(result.settled_decision, "Q009.D1");
  assert.equal(result.settled_quote.includes("present it to him as a usable product"), true,
    "the ruling travels with the refusal so a reader sees it was ruled, not overlooked");
  assert.equal(result.what_replaces_it.includes("product evidence"), true);
});

// ---------------------------------------------------------------------------
// GUARDS.
// ---------------------------------------------------------------------------

test("guard: no evaluator returns a privileged outcome, on any caller-controlled input", () => {
  const inputs = [
    readOnboardingProgress(progress()),
    readOnboardingProgress(progress({ steps_completed: [] })),
    evaluateBetaOperability(beta()),
    evaluateBetaOperability(beta({ author_interventions: 3 })),
    evaluatePerSliceDellReview({ slice_ref: "V5-R01", requested_by: "actor:x" }),
    onboardingMobileExposure(),
    v5R01OnboardingPreimage(),
  ];
  for (const result of inputs) {
    for (const value of collectStrings(result)) {
      assert.equal(PRIVILEGED_VALUES.has(value), false,
        `${result.answer ?? "a description"} returned ${JSON.stringify(value)}`);
    }
  }
});

test("guard: a partner cannot finish his own onboarding by declaring every step done", () => {
  // The whole point. The claim is complete, the tools are permitted, the answer
  // is still unavailable, and no field a consumer reads says otherwise.
  const result = readOnboardingProgress(progress());
  assert.equal(result.decision, "unavailable");
  assert.equal(PRIVILEGED_VALUES.has(result.would_be_classified), false);
  assert.equal(result.would_be_classified.startsWith("would_"), true);
  for (const key of Object.keys(result)) {
    assert.equal(result[key] === "completed", false, key);
  }
});

test("guard: no evaluator accepts a store, registry or observer as a second argument", () => {
  const forged = { resolveEnrollment: () => progress(), resolveObserver: () => beta() };
  for (const [name, evaluator, fixture] of [
    ["readOnboardingProgress", readOnboardingProgress, progress],
    ["evaluateBetaOperability", evaluateBetaOperability, beta],
    ["evaluatePerSliceDellReview", evaluatePerSliceDellReview,
      () => ({ slice_ref: "V5-R01", requested_by: "actor:x" })],
  ]) {
    assert.throws(() => evaluator(fixture(), forged),
      error => error instanceof V5R01Error && error.code.endsWith("_holder_is_not_an_argument"),
      `${name} accepted a caller-supplied holder`);
  }
});

test("guard: a holder smuggled in as a request FIELD is refused by name", () => {
  for (const field of ["enrollment_store", "observer_receipt", "progress_ledger"]) {
    assert.throws(() => readOnboardingProgress({ ...progress(), [field]: {} }),
      error => error instanceof V5R01Error
        && ["holder_field_is_not_authority", "unknown_field"].includes(error.code), field);
  }
});

test("guard: the public surface exports no classifier and is exactly what it declares", () => {
  const exported = Object.keys(onboarding).sort();
  assert.deepEqual(exported, [...V5_R01_ONBOARDING_PUBLIC_SURFACE].sort());
  for (const name of exported) {
    assert.equal(name.startsWith("classify"), false, name);
    assert.equal(name.startsWith("__"), false, name);
    assert.equal(name.toLowerCase().includes("fixture"), false, name);
    assert.equal(name.toLowerCase().includes("unwired"), false, name);
  }
  assert.equal(exported.includes("V5_R01_CLASSIFICATIONS"), false);
});

test("guard: an unknown field on a progress claim is refused, not ignored", () => {
  assert.throws(() => readOnboardingProgress({ ...progress(), onboarding_complete: true }),
    error => error instanceof V5R01Error && error.code === "unknown_field");
});

test("digest: the onboarding digest is stable and covers the step roster", () => {
  assert.equal(v5R01OnboardingDigest(), v5R01OnboardingDigest());
  const preimage = v5R01OnboardingPreimage();
  assert.equal(preimage.steps.length, V5_R01_ONBOARDING_STEPS.length);
  assert.deepEqual(preimage.forbidden_tool_classes, [...V5_R01_FORBIDDEN_TOOL_CLASSES]);
  assert.equal(preimage.owed_seam, V5_R01_SEAMS.onboarding_enrollment_store.seam);
});
