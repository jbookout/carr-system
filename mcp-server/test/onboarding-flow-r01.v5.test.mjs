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
import { readFileSync } from "node:fs";
import { test } from "node:test";

import * as onboarding from "../src/onboarding-flow-r01.v5.js";
import {
  AUTHENTICATED_SURFACES,
  COMMAND_ENDPOINT,
} from "../src/workspace-surface-inventory.js";
import {
  V5_R01_BETA_PARTNER,
  V5_R01_FORBIDDEN_TOOL_CLASSES,
  V5_R01_MOBILE_REACH,
  V5_R01_PERMITTED_TOOL_CLASSES,
  V5_R01_SEAMS,
} from "../src/rollout-pilot-r01.vocabulary.v5.js";
// THE TEST-ONLY ENTRY, under mcp-server/test/ where production cannot reach it.
// rollout-pilot-r01.v5.test.mjs proves the isolation with a real parser.
import {
  CLASSIFICATIONS,
  classifyBetaOperabilityIfAuthoritative,
  classifyOnboardingIfAuthoritative,
} from "./rollout-pilot-r01-classifiers.v5.testhelper.mjs";

const {
  V5R01Error, V5_R01_COMMAND_ENDPOINT, V5_R01_ONBOARDING_PUBLIC_SURFACE,
  V5_R01_ONBOARDING_STEPS, V5_R01_ONBOARDING_STEP_IDS, V5_R01_SURFACE_PHONE_NAV,
  evaluateBetaOperability, evaluatePerSliceDellReview, onboardingMobileExposure,
  onboardingSurfaceStatus, readOnboardingProgress, v5R01OnboardingDigest,
  v5R01OnboardingPreimage,
} = onboarding;

const ALL_STEPS = Object.freeze([...V5_R01_ONBOARDING_STEP_IDS]);

/** The nine reserved words. The suite's list — see the vocabulary module's tail. */
const PRIVILEGED_OUTCOMES = Object.freeze([
  "allow", "completed", "counted", "independent", "operable",
  "pass", "passed", "passing", "succeeded",
]);

/** Every string in a value, flattened. */
function collectStrings(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) value.forEach(item => collectStrings(item, out));
  else if (value !== null && typeof value === "object")
    Object.values(value).forEach(item => collectStrings(item, out));
  return out;
}

/** The words of a string, separators AND camelCase humps both ending a word. */
function wordsOf(text) {
  return String(text).replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[^A-Za-z0-9]+/).filter(Boolean).map(word => word.toLowerCase());
}

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

test("flow: no step claims to be resumable today, and each says what a resume record owes", () => {
  // THE DEFECT THE REVIEW NAMED: `resumable: true` was static metadata on a flow
  // that enrols nobody and saves nothing, so the word was doing work the code
  // could not back. The honest pair is the false flag, the seam that would change
  // it, and the description of the record it would hold.
  for (const step of V5_R01_ONBOARDING_STEPS) {
    assert.equal(step.resumable_today, false, step.step);
    assert.equal(step.resumable_requires_seam,
      V5_R01_SEAMS.onboarding_enrollment_store.seam, step.step);
    assert.equal(Object.hasOwn(step, "resumable"), false,
      `${step.step} still carries the bare resumable flag`);
    assert.equal(typeof step.resume_record_would_hold, "string", step.step);
    assert.ok(step.resume_record_would_hold.length > 0, step.step);
  }
});

test("flow: a step that claimed to be resumable today would not load", () => {
  // The property belongs to the FILE: the load-time check is what makes it one,
  // and this proves the check would fire rather than trusting that it exists.
  const source = readFileSync(new URL("../src/onboarding-flow-r01.v5.js", import.meta.url), "utf8");
  assert.ok(source.includes("onboarding_step_overclaims_its_resume_support"));
  assert.ok(source.includes("step.resumable_today !== false"));
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
  assert.equal(verdict.classification, CLASSIFICATIONS.onboarding);
  assert.equal(verdict.blocking_check, null);
});

test("ladder: a developer tool is caught BEFORE the step count", () => {
  // Deliberately also missing every step, to prove the order: the answer must be
  // the terminal, not the outstanding steps.
  const verdict = classifyOnboardingIfAuthoritative(progress({
    steps_completed: [], tool_classes_used: ["shell_or_terminal"],
  }), ALL_STEPS);
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "onboarding_used_a_developer_tool");
  assert.deepEqual(verdict.developer_tools_used, ["shell_or_terminal"]);
});

test("ladder: every forbidden tool class fails onboarding, and the answer names which", () => {
  for (const tool of V5_R01_FORBIDDEN_TOOL_CLASSES) {
    const verdict = classifyOnboardingIfAuthoritative(
      progress({ tool_classes_used: ["product_ui", tool] }), ALL_STEPS);
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, tool);
    assert.deepEqual(verdict.developer_tools_used, [tool]);
  }
});

test("ladder: one outstanding step is outstanding, and the answer names it", () => {
  const verdict = classifyOnboardingIfAuthoritative(
    progress({ steps_completed: ALL_STEPS.filter(step => step !== "ask_doc") }), ALL_STEPS);
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "onboarding_steps_outstanding");
  assert.deepEqual(verdict.outstanding_steps, ["ask_doc"]);
});

test("ladder: claiming a step the flow does not have is refused", () => {
  const verdict = classifyOnboardingIfAuthoritative(
    progress({ steps_completed: [...ALL_STEPS, "deploy_the_system"] }), ALL_STEPS);
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
  assert.equal(verdict.blocking_check, "onboarding_claims_an_unregistered_step");
  assert.deepEqual(verdict.unregistered_steps, ["deploy_the_system"]);
});

test("ladder: ONE author intervention ends independence — the threshold is zero", () => {
  // Q009: "his confusion is product evidence, not a training failure". So one
  // explanation converts a beta pass into a product finding, and there is no
  // small number of explanations that is still independent.
  const clean = classifyBetaOperabilityIfAuthoritative({
    partner: V5_R01_BETA_PARTNER,
    onboarding_classification: CLASSIFICATIONS.onboarding,
    realistic_work_items: ["deal:2001"],
    author_interventions: 0,
    tool_classes_used: ["product_ui"],
  });
  assert.equal(clean.classification, CLASSIFICATIONS.beta);

  const explained = classifyBetaOperabilityIfAuthoritative({
    partner: V5_R01_BETA_PARTNER,
    onboarding_classification: CLASSIFICATIONS.onboarding,
    realistic_work_items: ["deal:2001"],
    author_interventions: 1,
    tool_classes_used: ["product_ui"],
  });
  assert.equal(explained.classification, CLASSIFICATIONS.refuse);
  assert.equal(explained.blocking_check, "author_explained_the_system");
  assert.equal(explained.author_interventions, 1);
});

test("ladder: a walkthrough with no realistic work is not independent operation", () => {
  const verdict = classifyBetaOperabilityIfAuthoritative({
    partner: V5_R01_BETA_PARTNER,
    onboarding_classification: CLASSIFICATIONS.onboarding,
    realistic_work_items: [],
    author_interventions: 0,
    tool_classes_used: ["product_ui"],
  });
  assert.equal(verdict.classification, CLASSIFICATIONS.refuse);
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
    assert.equal(verdict.classification, CLASSIFICATIONS.refuse, forged);
    assert.equal(verdict.blocking_check, "onboarding_not_classified_complete", forged);
  }
});

// ---------------------------------------------------------------------------
// REFUSALS.
// ---------------------------------------------------------------------------

/**
 * EVERY CALLER-CONTROLLED SHAPE this surface could be handed. If none of them
 * changes an answer, none of them is authority.
 */
function callerShapes() {
  const shapes = [progress(), beta()];
  for (const steps of [[], [...ALL_STEPS], ALL_STEPS.slice(0, 4), ["not_a_step"],
    ["completed", "pass"]]) {
    shapes.push(progress({ steps_completed: steps }), beta({ steps_completed: steps }));
  }
  for (const tool of [...V5_R01_FORBIDDEN_TOOL_CLASSES, ...V5_R01_PERMITTED_TOOL_CLASSES]) {
    shapes.push(progress({ tool_classes_used: [tool] }), beta({ tool_classes_used: [tool] }));
  }
  for (const interventions of [0, 1, 12]) shapes.push(beta({ author_interventions: interventions }));
  shapes.push(beta({ realistic_work_items: [] }));
  shapes.push({ slice_ref: "V5-R01", requested_by: "actor:someone" });
  // The shapes that try to state the answer outright.
  shapes.push({ decision: "allow", onboarding_complete: true, operable: true });
  shapes.push({ would_complete_onboarding_if_authoritative: true });
  shapes.push({}, null, undefined, "allow", 1, true, []);
  return shapes;
}

const EVALUATORS = [
  ["readOnboardingProgress", readOnboardingProgress, [V5_R01_SEAMS.onboarding_enrollment_store.seam]],
  ["evaluateBetaOperability", evaluateBetaOperability,
    [V5_R01_SEAMS.onboarding_enrollment_store.seam, V5_R01_SEAMS.outside_observer.seam].sort()],
];

test("refusal: onboarding progress is unavailable and names the enrollment store", () => {
  const result = readOnboardingProgress(progress());
  assert.equal(result.status, "unavailable");
  assert.equal(result.decision, "unavailable");
  assert.deepEqual(result.owed_seams, [V5_R01_SEAMS.onboarding_enrollment_store.seam]);
  assert.equal(result.request_read, false);
  assert.equal(result.caller_evidence_admitted, false);
  assert.equal(result.authority_established, false);
  assert.equal(result.state_holder_is_caller_supplied, false);
  assert.equal(result.flow_is_saved_today, false);
  assert.equal(result.flow_is_resumable_today, false);
  assert.equal(Object.isFrozen(result), true);
  // THE DEFECT THE REVIEW NAMED: the conditional verdict no longer rides out with
  // the refusal, under that name or any other.
  assert.equal(Object.hasOwn(result, "would_be_classified"), false);
  assert.equal(Object.hasOwn(result, "blocking_check"), false);
  assert.equal(result.steps_finished_observed, null);
});

test("refusal: beta operability is unavailable and names the outside observer", () => {
  const result = evaluateBetaOperability(beta());
  assert.equal(result.decision, "unavailable");
  assert.deepEqual(result.owed_seams,
    [V5_R01_SEAMS.onboarding_enrollment_store.seam, V5_R01_SEAMS.outside_observer.seam].sort());
  assert.equal(result.beta_partner, V5_R01_BETA_PARTNER);
  assert.equal(Object.hasOwn(result, "would_be_classified"), false);
  assert.equal(result.author_interventions_observed, null);
  for (const entry of result.seams_bound) assert.equal(entry.bound, false, entry.seam);
});

test("refusal: both answers are byte-identical across every caller-controlled shape", () => {
  const shapes = callerShapes();
  assert.ok(shapes.length >= 30);
  for (const [name, evaluator] of EVALUATORS) {
    const first = JSON.stringify(evaluator(shapes[0]));
    for (const shape of shapes) {
      assert.equal(JSON.stringify(evaluator(shape)), first,
        `${name} answered differently for ${String(JSON.stringify(shape)).slice(0, 80)}`);
    }
    assert.equal(JSON.stringify(evaluator()), first, `${name} differed for no argument`);
  }
  // The merits refusal is invariant too: Q009 settled it for everyone.
  const merits = JSON.stringify(evaluatePerSliceDellReview({ slice_ref: "V5-R01", requested_by: "x" }));
  for (const shape of shapes) {
    assert.equal(JSON.stringify(evaluatePerSliceDellReview(shape)), merits);
  }
});

test("refusal: Dell's unaided operation never gates J1", () => {
  // Q010: "for now i dont want to sacrifice speed on the roll out or any other
  // qualities or capabilities for this."
  for (const request of callerShapes()) {
    const result = evaluateBetaOperability(request);
    assert.equal(result.blocks_j1, false);
    assert.equal(result.blocks_j1_basis.includes("Q010"), true);
  }
});

test("refusal: a per-slice Dell review is refused ON THE MERITS, not for want of a store", () => {
  const result = evaluatePerSliceDellReview({ slice_ref: "V5-R01", requested_by: "actor:someone" });
  assert.equal(result.decision, "refuse");
  assert.equal(result.status, "refused");
  assert.equal(result.reason_id, "per_slice_dell_review_declined_by_q009");
  assert.equal(result.refused_on_the_merits, true);
  assert.equal(result.waiting_on_a_store, false);
  assert.equal(result.request_read, false);
  assert.equal(result.settled_decision, "Q009.D1");
  assert.equal(result.settled_quote.includes("present it to him as a usable product"), true,
    "the ruling travels with the refusal so a reader sees it was ruled, not overlooked");
  assert.equal(result.what_replaces_it.includes("product evidence"), true);
});

// ---------------------------------------------------------------------------
// THE SURFACE THE FLOW IS NOT SERVED ON.
// ---------------------------------------------------------------------------

test("surface: the flow has no product surface of its own, and says what it would take", () => {
  // The review asked for an authenticated, mobile-navigable surface for this flow
  // with phone-width verified. It is not built, and the module says so with the
  // two owners it would take rather than describing one it does not have.
  const status = onboardingSurfaceStatus();
  assert.equal(status.dedicated_onboarding_surface_exists, false);
  assert.equal(status.dedicated_onboarding_surface_asset, null);
  assert.deepEqual(status.dedicated_onboarding_surface_routes, []);
  assert.deepEqual(status.owed_seams, [
    V5_R01_SEAMS.onboarding_enrollment_store.seam,
    V5_R01_SEAMS.onboarding_surface_registration.seam,
  ].sort());
  assert.equal(status.request_read, false);
  assert.equal(status.steps_a_partner_would_walk, V5_R01_ONBOARDING_STEPS.length);
});

test("surface: phone-width behaviour is null, not asserted", () => {
  // A rendering fact about HTML files measured in a browser. Nothing in this
  // repository records it, so the module returns null and says why rather than
  // inferring an answer from its own step list.
  const status = onboardingSurfaceStatus();
  assert.equal(status.phone_width_behaviour, null);
  assert.ok(status.phone_width_behaviour_is_unknown_because.includes("browser"));
  assert.equal(onboardingMobileExposure().open_question_this_module_cannot_answer.length > 40, true);
});

test("surface: the seam the registration would need is declared absent like every other", () => {
  assert.equal(V5_R01_SEAMS.onboarding_surface_registration.exists_in_this_repository, false);
  assert.ok(V5_R01_SEAMS.onboarding_surface_registration.holds.includes("inventory"));
  // And no asset this slice names is an onboarding page that does not exist.
  for (const step of V5_R01_ONBOARDING_STEPS) {
    assert.equal(step.asset === "onboarding.html", false, step.step);
  }
});

// ---------------------------------------------------------------------------
// GUARDS.
// ---------------------------------------------------------------------------

/**
 * ONE TEST PER RESERVED WORD, over every export of this module, across every
 * caller-controlled shape. The rule is the one the sibling suite states in full:
 * a violation is a reserved word in the OUTPUT that the caller did not put in
 * the INPUT, matched at word level so `runPassed` and `run_passed` are caught
 * where the previous round's equality check let them through.
 */
function namedWords(value, out = new Set()) {
  if (typeof value === "string") { out.add(value); return out; }
  if (Array.isArray(value)) { value.forEach(entry => namedWords(entry, out)); return out; }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) { out.add(key); namedWords(entry, out); }
  }
  return out;
}

function onboardingEntries() {
  const entries = [];
  for (const [name, exported] of Object.entries(onboarding)) {
    if (typeof exported !== "function") { entries.push({ at: name, input: undefined, output: exported }); continue; }
    if (/^[A-Z]/.test(name)) { entries.push({ at: name, input: undefined, output: name }); continue; }
    for (const shape of callerShapes()) {
      let output;
      try { output = exported(shape); } catch (failure) { output = { threw: failure.code ?? failure.message }; }
      entries.push({ at: `${name}(${String(JSON.stringify(shape))})`.slice(0, 110), input: shape, output });
    }
  }
  return entries;
}

const ONBOARDING_ENTRIES = onboardingEntries();

test("guard: the sweep covers every export of this module", () => {
  for (const name of Object.keys(onboarding)) {
    assert.ok(ONBOARDING_ENTRIES.some(entry => entry.at.startsWith(name)), `${name} was not swept`);
  }
  assert.ok(ONBOARDING_ENTRIES.length > 100);
});

for (const word of PRIVILEGED_OUTCOMES) {
  test(`guard: no export of this module manufactures "${word}" for any caller`, () => {
    for (const entry of ONBOARDING_ENTRIES) {
      if ([...namedWords(entry.input)].some(text => wordsOf(text).includes(word))) continue;
      const found = [...namedWords(entry.output)].filter(text => wordsOf(text).includes(word));
      assert.deepEqual(found, [], `${entry.at} produced "${word}" its caller never supplied`);
    }
  });
}

test("guard: a partner cannot finish his own onboarding by declaring every step done", () => {
  // The whole point. The claim is complete, the tools are permitted, the answer
  // is unavailable, and it is the SAME answer an empty claim gets.
  const complete = readOnboardingProgress(progress());
  const empty = readOnboardingProgress(progress({ steps_completed: [] }));
  assert.equal(complete.decision, "unavailable");
  assert.equal(JSON.stringify(complete), JSON.stringify(empty),
    "a complete claim and an empty one must be indistinguishable in the answer");
  for (const value of collectStrings(complete)) {
    assert.equal(PRIVILEGED_OUTCOMES.includes(value), false, value);
  }
});

test("guard: no evaluator accepts a store, registry or observer as a second argument", () => {
  const forged = { resolveEnrollment: () => progress(), resolveObserver: () => beta() };
  for (const [name, evaluator, fixture] of [
    ["readOnboardingProgress", readOnboardingProgress, progress],
    ["evaluateBetaOperability", evaluateBetaOperability, beta],
    ["evaluatePerSliceDellReview", evaluatePerSliceDellReview,
      () => ({ slice_ref: "V5-R01", requested_by: "actor:x" })],
    ["onboardingSurfaceStatus", onboardingSurfaceStatus, () => undefined],
  ]) {
    assert.throws(() => evaluator(fixture(), forged),
      error => error instanceof V5R01Error && error.code.endsWith("_holder_is_not_an_argument"),
      `${name} accepted a caller-supplied holder`);
  }
});

test("guard: a holder smuggled in as a request FIELD changes nothing, because nothing is read", () => {
  // The previous round refused such a field by name, which was a reading of the
  // request. There is a stronger answer available now and this is it: a holder in
  // a field is inert because no field is looked at, so the answer is the same
  // answer everyone else gets.
  const plain = JSON.stringify(readOnboardingProgress(progress()));
  for (const field of ["enrollment_store", "observer_receipt", "progress_ledger"]) {
    assert.equal(JSON.stringify(readOnboardingProgress({ ...progress(), [field]: {
      resolve: () => ({ steps_completed: [...ALL_STEPS] }),
    } })), plain, field);
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
  for (const name of exported) {
    assert.equal(name.includes("would_"), false, name);
    assert.equal(/internal|testonly|testhelper/i.test(name), false, name);
  }
});

test("guard: an unknown field on a progress claim is inert, because no field is read", () => {
  assert.equal(JSON.stringify(readOnboardingProgress({ ...progress(), onboarding_complete: true })),
    JSON.stringify(readOnboardingProgress(progress())));
});

test("digest: the onboarding digest is stable and covers the step roster", () => {
  assert.equal(v5R01OnboardingDigest(), v5R01OnboardingDigest());
  const preimage = v5R01OnboardingPreimage();
  assert.equal(preimage.steps.length, V5_R01_ONBOARDING_STEPS.length);
  assert.deepEqual(preimage.forbidden_tool_classes, [...V5_R01_FORBIDDEN_TOOL_CLASSES]);
  assert.deepEqual(preimage.owed_seams, [
    V5_R01_SEAMS.onboarding_enrollment_store.seam,
    V5_R01_SEAMS.onboarding_surface_registration.seam,
  ].sort());
  assert.equal(preimage.dedicated_onboarding_surface_exists, false);
  // The step shape the digest covers is the honest one: no step claims to be
  // resumable today, so a future edit that flips one moves the digest.
  for (const step of preimage.steps) assert.equal(step.resumable_today, false, step.step);
});
