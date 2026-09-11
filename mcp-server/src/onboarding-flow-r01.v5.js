// DoctorCRE v5 slice V5-R01 — the product-like onboarding flow: what a partner
// with no developer tools does, in what order, on which surface, and how much of
// it he can do from a phone.
//
// WHAT THIS FILE IS. A declaration and two evaluators.
//
//   THE DECLARATION is V5_R01_ONBOARDING_STEPS: nine ordered steps covering the
//   slice's own included scope — login, Home, Deals, client, assignment, Doc,
//   safe update, receipts, fallback. Each step names the surface it happens on,
//   the tool classes it requires, whether it is resumable, and what would have to
//   be recorded for it to be resumable. The declaration is true of the product as
//   it stands TODAY, which is the only kind of declaration worth having.
//
//   THE EVALUATORS both refuse. "This partner has completed onboarding" and
//   "Dell can operate the product independently" are facts about a person and a
//   production run, held by an enrollment store and an independent observer that
//   do not exist here. Neither is derivable from source and neither is derivable
//   from anything a caller can pass in.
//
// "NO DEVELOPER TOOLS" IS A PROPERTY OF THIS FILE, NOT A PROMISE ABOUT IT.
// V5_R01_FORBIDDEN_TOOL_CLASSES lists ten things — a shell, a checkout, a
// package manager, a database client, a raw log, and so on — and the load-time
// check below refuses to load this module if any registered step requires one.
// A future edit that adds a step needing a terminal does not produce a failing
// assertion somewhere; it produces a module that will not import.
//
// THE MOBILE PATH IS DESIGNED FIRST AND ITS GAP IS REPORTED, NOT SMOOTHED OVER.
// Joe wants to do as much as possible from his phone. So every step names the
// surface it happens on and carries that surface's real phone-bar state. Today
// two of the seven authenticated surfaces carry the workspace phone bar, which
// means SIX of the nine onboarding steps sit on surfaces a partner cannot reach
// through the product's own phone navigation. `onboardingMobileExposure()` says
// so by name, with the surfaces and the steps listed.
//
// WHY THAT TABLE IS RESTATED HERE RATHER THAN IMPORTED, since importing is
// normally the right answer and it is what the first draft did. J101's inventory
// module (workspace-surface-inventory.js) holds the truth, and importing it pulls
// workspace-business-read.js and with it the Neon serverless client. Every module
// in this v5 decision lane is pure — no driver, no connection, no install needed
// to load it — and dragging a database client into that lane to read seven
// booleans is the wrong trade. So V5_R01_SURFACE_PHONE_NAV below is this slice's
// own copy, and the suite proves it equals J101's AUTHENTICATED_SURFACES IN BOTH
// DIRECTIONS: an asset here that J101 does not carry fails, a phone-bar flag that
// disagrees fails, and a surface J101 adds that this table has not heard of fails
// too. That is J101's own pattern — it does the same thing to live-client.js —
// and it means the copy cannot drift into a wish without turning a test red.
//
// BE PRECISE ABOUT WHAT THAT GAP IS AND IS NOT, because overstating it would be
// its own dishonesty. `mobile_nav: false` means the surface does not carry the
// workspace phone navigation bar. It does NOT mean the page fails on a phone, and
// this module does not claim that: the honest reading is "reachable by typing or
// following a link, not reachable by navigating", which is why
// V5_R01_MOBILE_REACH has three states and not two. The exposure this reports is
// a navigation gap. Whether each of those six surfaces also LAYS OUT correctly at
// phone width is a rendering question about six HTML files, and nothing in this
// repository records the answer — so it is named as an open question rather than
// answered here.
//
// PURE. No filesystem, no network, no database, no clock, no environment, no
// state between calls, and nothing is enrolled, saved, activated or accepted.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_R01_BETA_PARTNER,
  V5_R01_CLASSIFICATIONS,
  V5_R01_FORBIDDEN_TOOL_CLASSES,
  V5_R01_MOBILE_REACH,
  V5_R01_ONBOARDING_SCHEMA,
  V5_R01_PERMITTED_TOOL_CLASSES,
  V5_R01_POLICY_VERSION,
  V5_R01_PRIVILEGED_OUTCOMES,
  V5_R01_SEAMS,
  V5R01Error,
  assertClosedKeys,
  assertObject,
  assertRequiredKeys,
  collectStrings,
  deepFreeze,
  fail,
} from "./rollout-pilot-r01.vocabulary.v5.js";
import {
  classifyBetaOperabilityIfAuthoritative,
  classifyOnboardingIfAuthoritative,
} from "./rollout-pilot-r01.internal.v5.js";

export { V5_NO_EFFECTS, V5R01Error };

export const V5_R01_ONBOARDING_SCHEMA_VERSION = V5_R01_ONBOARDING_SCHEMA;

/** The endpoint a partner's typed command is acknowledged at. J101's COMMAND_ENDPOINT. */
export const V5_R01_COMMAND_ENDPOINT = "/mcp";

/**
 * This slice's copy of J101's phone-bar observation: asset file → does that
 * surface carry the workspace phone navigation bar today, and which canonical
 * routes serve it.
 *
 * Seven rows, because J101 declares seven authenticated surfaces. The suite
 * compares this table to J101's AUTHENTICATED_SURFACES in both directions, so a
 * row that disappears upstream, a flag that flips upstream, and a route that
 * changes upstream each turn a test red rather than quietly making this module's
 * mobility report wrong. See the header for why it is a copy.
 */
export const V5_R01_SURFACE_PHONE_NAV = deepFreeze({
  "workspace.html": { mobile_nav: true, routes: ["/"] },
  "business.html": { mobile_nav: true, routes: ["/clients", "/vendors"] },
  "index.html": { mobile_nav: false, routes: ["/deals"] },
  "leads.html": { mobile_nav: false, routes: ["/leads"] },
  "room.html": { mobile_nav: false, routes: ["/room.html"] },
  "queue.html": { mobile_nav: false, routes: ["/queue.html"] },
  "system-work.html": { mobile_nav: false, routes: ["/system-work.html"] },
});

/**
 * The nine steps, in order.
 *
 * `asset` is the file under dealroom/ that serves the step, matched against the
 * surface table at load. Two steps have no asset: `ask_doc` happens at the
 * command endpoint the Deal Room board posts to, and `use_documented_fallback`
 * happens off the product entirely — it is a phone call or an email, which is
 * the point of it. Both declare `asset: null` and take the mobile reach
 * `not_a_surface`, so neither can be quietly counted as phone-navigable coverage
 * and neither can be counted against it.
 *
 * `resume_token_holds` is what an enrollment store would have to remember for
 * the step to be resumable. It is a description of an owed record, not a record.
 */
const STEP_DECLARATIONS = Object.freeze([
  Object.freeze({
    step: "sign_in",
    plain: "Sign in to the workspace on your own device.",
    asset: "workspace.html",
    requires_tool_classes: Object.freeze(["authenticated_browser"]),
    resumable: true,
    resume_token_holds: "nothing beyond the session itself",
  }),
  Object.freeze({
    step: "read_home",
    plain: "Read Home: today's work and whether the system is healthy.",
    asset: "workspace.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resumable: true,
    resume_token_holds: "that Home was read at least once",
  }),
  Object.freeze({
    step: "open_a_client",
    plain: "Open a real client and read what the system holds about them.",
    asset: "business.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resumable: true,
    resume_token_holds: "the client reference that was opened",
  }),
  Object.freeze({
    step: "open_a_deal",
    plain: "Open a real deal in the Deal Room.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resumable: true,
    resume_token_holds: "the deal reference that was opened",
  }),
  Object.freeze({
    step: "read_an_assignment",
    plain: "Read the assignment on that deal — what the engagement actually covers.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resumable: true,
    resume_token_holds: "the assignment reference that was read",
  }),
  Object.freeze({
    step: "ask_doc",
    plain: "Ask Dr. CRE to do one meaningful thing, in your own words.",
    asset: null,
    requires_tool_classes: Object.freeze(["product_ui"]),
    resumable: true,
    resume_token_holds: "the command reference that was issued and acknowledged",
  }),
  Object.freeze({
    step: "make_a_safe_update",
    plain: "Change one field on the deal, knowing it can be put back.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resumable: true,
    resume_token_holds: "the change reference that was written",
  }),
  Object.freeze({
    step: "inspect_the_receipts",
    plain: "Look at what the system read, decided and changed, and whether it worked.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resumable: true,
    resume_token_holds: "the receipt reference that was inspected",
  }),
  Object.freeze({
    step: "use_documented_fallback",
    plain: "Practise the old-school fallback: what you do by phone, email or paper"
      + " when the system cannot be reached.",
    asset: null,
    requires_tool_classes: Object.freeze(["telephone"]),
    resumable: true,
    resume_token_holds: "that the fallback was walked through and by whom",
  }),
]);

/** The surface row for an asset, or null for a step that is not on a surface. */
function surfaceRow(asset) {
  if (asset === null) return null;
  if (!Object.prototype.hasOwnProperty.call(V5_R01_SURFACE_PHONE_NAV, asset)) {
    throw new V5R01Error("onboarding_step_names_an_unknown_surface",
      `onboarding names asset "${asset}", which the surface table does not carry`, { asset });
  }
  return V5_R01_SURFACE_PHONE_NAV[asset];
}

/**
 * The reach, derived from J101 and never asserted.
 *
 * A step with no asset is `not_a_surface`. A step whose surface carries the phone
 * bar is `in_phone_navigation`. Everything else is `reachable_by_link_only` —
 * the deliberately weaker claim explained in the header.
 */
function mobileReach(row) {
  if (row === null) return "not_a_surface";
  return row.mobile_nav === true ? "in_phone_navigation" : "reachable_by_link_only";
}

export const V5_R01_ONBOARDING_STEPS = deepFreeze(STEP_DECLARATIONS.map((declaration, index) => {
  const row = surfaceRow(declaration.asset);
  return {
    ...declaration,
    ordinal: index + 1,
    requires_tool_classes: [...declaration.requires_tool_classes],
    routes: row === null
      ? (declaration.step === "ask_doc" ? [V5_R01_COMMAND_ENDPOINT] : [])
      : [...row.routes],
    mobile_reach: mobileReach(row),
  };
}));

export const V5_R01_ONBOARDING_STEP_IDS =
  deepFreeze(V5_R01_ONBOARDING_STEPS.map(step => step.step));

// ---------------------------------------------------------------------------
// LOAD-TIME CHECKS. "No developer tools" and "every step resumable" are
// properties of this file: an edit that breaks either one breaks the import.
// ---------------------------------------------------------------------------

for (const step of V5_R01_ONBOARDING_STEPS) {
  for (const tool of step.requires_tool_classes) {
    if (V5_R01_FORBIDDEN_TOOL_CLASSES.includes(tool)) {
      throw new V5R01Error("onboarding_step_requires_a_developer_tool",
        `onboarding step "${step.step}" requires "${tool}", which a partner with no developer`
        + " tools does not have",
        { step: step.step, tool });
    }
    if (!V5_R01_PERMITTED_TOOL_CLASSES.includes(tool)) {
      throw new V5R01Error("onboarding_step_requires_an_unregistered_tool",
        `onboarding step "${step.step}" requires unregistered tool class "${tool}"`,
        { step: step.step, tool });
    }
  }
  if (step.resumable !== true) {
    throw new V5R01Error("onboarding_step_is_not_resumable",
      `onboarding step "${step.step}" is not resumable; the flow is saved and resumable`,
      { step: step.step });
  }
  if (typeof step.resume_token_holds !== "string" || step.resume_token_holds.length === 0) {
    throw new V5R01Error("onboarding_step_declares_no_resume_record",
      `onboarding step "${step.step}" does not say what a resume record would hold`,
      { step: step.step });
  }
  if (!V5_R01_MOBILE_REACH.includes(step.mobile_reach)) {
    throw new V5R01Error("onboarding_step_has_an_unregistered_mobile_reach",
      `onboarding step "${step.step}" carries reach "${step.mobile_reach}"`,
      { step: step.step });
  }
}

if (new Set(V5_R01_ONBOARDING_STEP_IDS).size !== V5_R01_ONBOARDING_STEP_IDS.length) {
  throw new V5R01Error("duplicate_onboarding_step", "onboarding step ids must be distinct",
    { steps: [...V5_R01_ONBOARDING_STEP_IDS] });
}

// ---------------------------------------------------------------------------
// THE MOBILE EXPOSURE, stated candidly.
// ---------------------------------------------------------------------------

/**
 * How much of onboarding a partner can do through the phone navigation the
 * product actually ships today, and exactly what is missing.
 *
 * Everything here is DERIVED. `phone_navigable` is the count of steps whose
 * surface carries J101's `mobile_nav` flag; `surfaces_without_phone_navigation`
 * is read straight off the inventory. If someone adds the phone bar to the Deal
 * Room, this function's answer changes without anyone editing it, which is the
 * only way a number like this stays true.
 */
export function onboardingMobileExposure() {
  const byReach = reach => V5_R01_ONBOARDING_STEPS
    .filter(step => step.mobile_reach === reach).map(step => step.step);
  const assets = Object.keys(V5_R01_SURFACE_PHONE_NAV);
  const withPhoneBar =
    assets.filter(asset => V5_R01_SURFACE_PHONE_NAV[asset].mobile_nav === true).sort();
  const withoutPhoneBar =
    assets.filter(asset => V5_R01_SURFACE_PHONE_NAV[asset].mobile_nav !== true).sort();

  return deepFreeze({
    schema_version: V5_R01_ONBOARDING_SCHEMA_VERSION,
    total_steps: V5_R01_ONBOARDING_STEPS.length,
    phone_navigable_steps: byReach("in_phone_navigation"),
    link_only_steps: byReach("reachable_by_link_only"),
    off_surface_steps: byReach("not_a_surface"),
    surfaces_with_phone_navigation: withPhoneBar,
    surfaces_without_phone_navigation: withoutPhoneBar,
    what_link_only_means:
      "the surface does not carry the workspace phone navigation bar, so a partner on a phone"
      + " reaches it by following a link rather than by navigating to it; this is a navigation"
      + " gap and is NOT a claim that the page fails at phone width",
    open_question_this_module_cannot_answer:
      "whether each surface without the phone bar also lays out correctly at phone width;"
      + " nothing in this repository records that, so it is named rather than answered",
    reported_by: "derived from V5_R01_SURFACE_PHONE_NAV, which the suite pins to J101's"
      + " AUTHENTICATED_SURFACES in both directions",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// THE EVALUATORS. Both refuse, and neither takes a holder.
// ---------------------------------------------------------------------------

const HOLDER_FRAGMENTS = Object.freeze([
  "store", "registry", "ledger", "resolver", "holder", "adapter", "client", "observer",
]);

/** A caller offering a holder has misread this module; it does not take one. */
function assertNoHolder(extra, name) {
  if (extra === undefined) return;
  fail(`${name}_holder_is_not_an_argument`,
    `${name} takes one request object; a store, registry, ledger or observer is not an argument`,
    { hint: "the owner of this fact does not exist in this repository" });
}

function assertNoHolderFields(request, path) {
  for (const key of Object.keys(request)) {
    const lowered = key.toLowerCase();
    for (const fragment of HOLDER_FRAGMENTS) {
      if (lowered.includes(fragment)) {
        fail("holder_field_is_not_authority",
          `${path}.${key} offers an authority holder; this module resolves its own`,
          { path: `${path}.${key}`, fragment });
      }
    }
  }
}

function refusal(answer, seam, reason_id, detail) {
  const result = deepFreeze({
    answer,
    decision: "unavailable",
    reason_id,
    schema_version: V5_R01_ONBOARDING_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    owed_seam: seam.seam,
    owed_seam_holds: seam.holds,
    authority_established: false,
    state_holder_is_caller_supplied: false,
    model_judgment_admitted: false,
    blocking_check: detail.blocking_check ?? null,
    ...detail,
    effects: V5_NO_EFFECTS,
  });
  for (const value of collectStrings(result)) {
    if (V5_R01_PRIVILEGED_OUTCOMES.includes(value)) {
      fail("refusal_would_leak_privileged_outcome",
        `a refusal carried the privileged value "${value}"`, { value });
    }
  }
  return result;
}

const PROGRESS_REQUEST_KEYS = Object.freeze(["partner", "steps_completed", "tool_classes_used"]);

/**
 * Where is this partner up to, and is the flow finished?
 *
 * IT REFUSES, ALWAYS, AND THE REASON IS NOT SHYNESS. How far a partner has got is
 * a fact an enrollment store holds. A caller passing `steps_completed` is passing
 * its own opinion of the answer, and a module that read it would be letting the
 * caller finish its own onboarding by declaring it finished. So the ladder runs —
 * the refusal names the step that is outstanding or the developer tool that was
 * used, which is genuinely useful — and the answer stays `unavailable`.
 */
export function readOnboardingProgress(request, ...extra) {
  assertNoHolder(extra[0], "readOnboardingProgress");
  assertObject(request, "request");
  assertNoHolderFields(request, "request");
  assertClosedKeys(request, PROGRESS_REQUEST_KEYS, "request");
  assertRequiredKeys(request, PROGRESS_REQUEST_KEYS, "request");

  const classification = classifyOnboardingIfAuthoritative(request, [...V5_R01_ONBOARDING_STEP_IDS]);
  return refusal("readOnboardingProgress", V5_R01_SEAMS.onboarding_enrollment_store,
    "onboarding_enrollment_store_absent", {
      partner: request.partner,
      blocking_check: classification.blocking_check,
      total_steps: V5_R01_ONBOARDING_STEPS.length,
      would_be_classified: classification.classification,
    });
}

const BETA_REQUEST_KEYS = Object.freeze([
  "partner", "steps_completed", "tool_classes_used", "realistic_work_items", "author_interventions",
]);

/**
 * Can the beta partner operate the product on his own?
 *
 * TWO THINGS ARE REFUSED HERE AND THEY ARE DIFFERENT REFUSALS.
 *
 * The first is the ordinary one: nobody in this repository can say whether Dell
 * did realistic work unaided, because the observer who would say so does not
 * exist. So the answer is `unavailable`.
 *
 * The second is a REFUSAL ON THE MERITS and it survives the store arriving.
 * Q009 is settled: Joe declined to put Dell in the per-slice adoption loop —
 * "I dont want to involve dell in the adoption. i prefer to validate it myself
 * and present it to him as a usable product." So there is no per-slice Dell
 * review in this slice and `evaluatePerSliceDellReview` below refuses one by
 * name. And Q010 is settled the other way round: Dell must eventually be able to
 * operate the system while Joe is away, but "for now i dont want to sacrifice
 * speed on the roll out or any other qualities or capabilities for this". So
 * `blocks_j1: false` rides on every result here. Dell's independence is a goal of
 * this slice; it is not a gate on J1, and this module will not become one.
 */
export function evaluateBetaOperability(request, ...extra) {
  assertNoHolder(extra[0], "evaluateBetaOperability");
  assertObject(request, "request");
  assertNoHolderFields(request, "request");
  assertClosedKeys(request, BETA_REQUEST_KEYS, "request");
  assertRequiredKeys(request, BETA_REQUEST_KEYS, "request");

  const onboarding = classifyOnboardingIfAuthoritative({
    partner: request.partner,
    steps_completed: request.steps_completed,
    tool_classes_used: request.tool_classes_used,
  }, [...V5_R01_ONBOARDING_STEP_IDS]);

  const beta = classifyBetaOperabilityIfAuthoritative({
    partner: request.partner,
    onboarding_classification: onboarding.classification,
    realistic_work_items: request.realistic_work_items,
    author_interventions: request.author_interventions,
    tool_classes_used: request.tool_classes_used,
  });

  return refusal("evaluateBetaOperability", V5_R01_SEAMS.independent_observer,
    "independent_observer_receipt_absent", {
      partner: request.partner,
      beta_partner: V5_R01_BETA_PARTNER,
      blocking_check: beta.blocking_check ?? onboarding.blocking_check,
      would_be_classified: beta.classification,
      blocks_j1: false,
      blocks_j1_basis: "Q010 — Dell's eventual independent operation must not cost rollout speed"
        + " or any other capability now",
    });
}

const DELL_REVIEW_KEYS = Object.freeze(["slice_ref", "requested_by"]);

/**
 * Someone asking Dell to sign off a slice. Refused, on the merits, every time.
 *
 * This is not an unavailable and it is not waiting for a store. Joe considered
 * per-slice Dell review and declined it, in his own words, for a stated reason:
 * each slice would wait days on a validation Dell would not perform the way Joe
 * does. A later decision may reopen it; until one does, the refusal is the
 * answer, and the quote travels with it so a reader can see it was ruled rather
 * than overlooked.
 */
export function evaluatePerSliceDellReview(request, ...extra) {
  assertNoHolder(extra[0], "evaluatePerSliceDellReview");
  assertObject(request, "request");
  assertClosedKeys(request, DELL_REVIEW_KEYS, "request");
  assertRequiredKeys(request, DELL_REVIEW_KEYS, "request");

  return deepFreeze({
    answer: "evaluatePerSliceDellReview",
    decision: "refuse",
    reason_id: "per_slice_dell_review_declined_by_q009",
    schema_version: V5_R01_ONBOARDING_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    slice_ref: request.slice_ref,
    refused_on_the_merits: true,
    waiting_on_a_store: false,
    settled_quote: "I dont want to involve dell in the adoption. i prefer to validate it myself"
      + " and present it to him as a usable product.",
    settled_decision: "Q009.D1",
    what_replaces_it: "Joe validates each slice himself and presents the finished product;"
      + " Dell's confusion, when it comes, is read as product evidence rather than as training",
    authority_established: false,
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Digest and surface.
// ---------------------------------------------------------------------------

export function v5R01OnboardingPreimage() {
  return deepFreeze({
    schema_version: V5_R01_ONBOARDING_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    steps: V5_R01_ONBOARDING_STEPS.map(step => ({
      ordinal: step.ordinal,
      step: step.step,
      asset: step.asset,
      routes: [...step.routes],
      requires_tool_classes: [...step.requires_tool_classes],
      resumable: step.resumable,
      mobile_reach: step.mobile_reach,
    })),
    forbidden_tool_classes: [...V5_R01_FORBIDDEN_TOOL_CLASSES],
    permitted_tool_classes: [...V5_R01_PERMITTED_TOOL_CLASSES],
    owed_seam: V5_R01_SEAMS.onboarding_enrollment_store.seam,
  });
}

export function v5R01OnboardingCanonicalBytes() {
  return canonicalJson(v5R01OnboardingPreimage());
}

export function v5R01OnboardingDigest() {
  return digest(v5R01OnboardingPreimage());
}

/**
 * The consumer surface, named so a reviewer checks the export list against a list
 * rather than against a memory of one. The suite asserts this IS the module's
 * export set — no probe, no classifier, no escape hatch.
 */
export const V5_R01_ONBOARDING_PUBLIC_SURFACE = deepFreeze([
  "V5R01Error",
  "V5_NO_EFFECTS",
  "V5_R01_ONBOARDING_PUBLIC_SURFACE",
  "V5_R01_ONBOARDING_SCHEMA_VERSION",
  "V5_R01_ONBOARDING_STEPS",
  "V5_R01_ONBOARDING_STEP_IDS",
  "V5_R01_COMMAND_ENDPOINT",
  "V5_R01_SURFACE_PHONE_NAV",
  "evaluateBetaOperability",
  "evaluatePerSliceDellReview",
  "onboardingMobileExposure",
  "readOnboardingProgress",
  "v5R01OnboardingCanonicalBytes",
  "v5R01OnboardingDigest",
  "v5R01OnboardingPreimage",
]);

// A classification value must never be mistakable for an outcome, and the
// onboarding classification is the one this module hands back inside a refusal.
if (!V5_R01_CLASSIFICATIONS.onboarding.startsWith("would_")) {
  throw new V5R01Error("classification_is_not_conditional",
    "the onboarding classification no longer names a hypothetical", {});
}
