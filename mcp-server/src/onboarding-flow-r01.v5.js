// DoctorCRE v5 slice V5-R01 — the product-like onboarding flow: what a partner
// with no developer tools does, in what order, on which surface, and how much of
// it he can do from a phone.
//
// WHAT THIS FILE IS. A declaration, a surface status, and three evaluators.
//
//   THE DECLARATION is V5_R01_ONBOARDING_STEPS: nine ordered steps covering the
//   slice's own included scope — login, Home, Deals, client, assignment, Doc,
//   safe update, receipts, fallback. Each step names the surface it happens on,
//   the tool classes it requires, and what an enrollment store would have to
//   record for it to be resumable — alongside `resumable_today: false`, which is
//   the true state, because nothing in this repository enrols anybody in
//   anything. The declaration is true of the product as it stands TODAY, which is
//   the only kind of declaration worth having.
//
//   THE EVALUATORS ANSWER WITHOUT READING. "This partner has completed
//   onboarding" and "Dell can operate the product independently" are facts about
//   a person and a production run, held by an enrollment store and an independent
//   observer that do not exist here. Neither is derivable from source and neither
//   is derivable from anything a caller can pass in — so each evaluator takes one
//   argument, does not look at it, and returns one fixed value carrying
//   `request_read: false`. The third, `evaluatePerSliceDellReview`, refuses on the
//   merits instead: Q009 settled it and no store will unsettle it.
//
//   THE SURFACE STATUS, `onboardingSurfaceStatus`, answers the question the
//   review of PR 992 asked — is this flow a page a partner can walk on a phone —
//   with the two seams it would take and an explicit null for phone-width
//   behaviour, which is measured in a browser and not asserted in a module.
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
// WHAT THE PIN DOES NOT DO, because the previous round's report claimed it did
// and the review of PR 992 was right to refuse the claim: it does not make this
// module's mobility answer change by itself when J101 changes. Production reads
// the copy. An upstream flip turns the pin RED, which makes the edit here
// mandatory and visible; it does not perform the edit. "Cannot drift silently" is
// the true claim; "changes with no R01 edit" was not.
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
  V5_R01_FORBIDDEN_TOOL_CLASSES,
  V5_R01_MOBILE_REACH,
  V5_R01_ONBOARDING_SCHEMA,
  V5_R01_PERMITTED_TOOL_CLASSES,
  V5_R01_POLICY_VERSION,
  V5_R01_SEAMS,
  V5R01Error,
  assertClosedKeys,
  assertObject,
  assertRequiredKeys,
  deepFreeze,
  fail,
} from "./rollout-pilot-r01.vocabulary.v5.js";

export { V5_NO_EFFECTS, V5R01Error };

export const V5_R01_ONBOARDING_SCHEMA_VERSION = V5_R01_ONBOARDING_SCHEMA;

/** The endpoint a partner's typed command is acknowledged at. J101's COMMAND_ENDPOINT. */
export const V5_R01_COMMAND_ENDPOINT = "/mcp";

/**
 * THE PHONE-BAR OBSERVATION: THIS SLICE'S PINNED COPY OF J101'S INVENTORY.
 *
 * Seven rows, one per authenticated surface: does that surface carry the
 * workspace phone navigation bar today, and which canonical routes serve it.
 *
 * IT IS A COPY, AND SAYING SO PRECISELY IS THE POINT. The first draft of this
 * module imported J101's `AUTHENTICATED_SURFACES` directly, which is normally the
 * right answer. It is the wrong answer here: workspace-surface-inventory.js
 * imports workspace-business-read.js, which imports `@neondatabase/serverless`.
 * Every module in this v5 decision lane loads with no driver, no connection and
 * nothing installed, and dragging a database client into that lane to read seven
 * booleans is the wrong trade. So the rows are restated and the suite pins them
 * to J101 IN BOTH DIRECTIONS: an asset here J101 does not carry fails, a flag
 * that disagrees fails, a route that disagrees fails, and a surface J101 adds
 * that this table has not heard of fails too.
 *
 * WHAT THAT BUYS AND WHAT IT DOES NOT, stated because the previous round's report
 * overstated it and the review was right to call that out. It does NOT mean the
 * mobility answer changes with no edit to this file: production reads this table,
 * so an upstream flip changes nothing at runtime until someone edits this row.
 * What it means is that an upstream flip cannot land SILENTLY — the pin turns red
 * and the edit becomes required rather than optional. That is a smaller claim and
 * it is the true one.
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
 * `resume_record_would_hold` is what an enrollment store would have to remember
 * for the step to be resumable. It is a description of an owed record, not a
 * record, and the previous round's `resumable: true` beside it was the half that
 * was not true — a static boolean on a step nothing enrols anybody in. Every
 * step now carries `resumable_today: false` and the seam that would change it.
 */
const STEP_DECLARATIONS = Object.freeze([
  Object.freeze({
    step: "sign_in",
    plain: "Sign in to the workspace on your own device.",
    asset: "workspace.html",
    requires_tool_classes: Object.freeze(["authenticated_browser"]),
    resume_record_would_hold: "nothing beyond the session itself",
  }),
  Object.freeze({
    step: "read_home",
    plain: "Read Home: today's work and whether the system is healthy.",
    asset: "workspace.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resume_record_would_hold: "that Home was read at least once",
  }),
  Object.freeze({
    step: "open_a_client",
    plain: "Open a real client and read what the system holds about them.",
    asset: "business.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resume_record_would_hold: "the client reference that was opened",
  }),
  Object.freeze({
    step: "open_a_deal",
    plain: "Open a real deal in the Deal Room.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resume_record_would_hold: "the deal reference that was opened",
  }),
  Object.freeze({
    step: "read_an_assignment",
    plain: "Read the assignment on that deal — what the engagement actually covers.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resume_record_would_hold: "the assignment reference that was read",
  }),
  Object.freeze({
    step: "ask_doc",
    plain: "Ask Dr. CRE to do one meaningful thing, in your own words.",
    asset: null,
    requires_tool_classes: Object.freeze(["product_ui"]),
    resume_record_would_hold: "the command reference that was issued and acknowledged",
  }),
  Object.freeze({
    step: "make_a_safe_update",
    plain: "Change one field on the deal, knowing it can be put back.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resume_record_would_hold: "the change reference that was written",
  }),
  Object.freeze({
    step: "inspect_the_receipts",
    plain: "Look at what the system read, decided and changed, and whether it worked.",
    asset: "index.html",
    requires_tool_classes: Object.freeze(["product_ui"]),
    resume_record_would_hold: "the receipt reference that was inspected",
  }),
  Object.freeze({
    step: "use_documented_fallback",
    plain: "Practise the old-school fallback: what you do by phone, email or paper"
      + " when the system cannot be reached.",
    asset: null,
    requires_tool_classes: Object.freeze(["telephone"]),
    resume_record_would_hold: "that the fallback was walked through and by whom",
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
    // NOT a property of this file, and it was written as one. Nothing enrols a
    // partner, nothing saves a position, and nothing resumes: the store that
    // would is named instead.
    resumable_today: false,
    resumable_requires_seam: V5_R01_SEAMS.onboarding_enrollment_store.seam,
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
  if (step.resumable_today !== false) {
    throw new V5R01Error("onboarding_step_claims_to_be_resumable",
      `onboarding step "${step.step}" claims to be resumable today; no enrollment store exists`,
      { step: step.step, owed_seam: V5_R01_SEAMS.onboarding_enrollment_store.seam });
  }
  if (typeof step.resume_record_would_hold !== "string"
    || step.resume_record_would_hold.length === 0) {
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
    reported_by: "derived from V5_R01_SURFACE_PHONE_NAV, this slice's copy of J101's"
      + " AUTHENTICATED_SURFACES; the suite pins the copy to J101 in both directions, so an"
      + " upstream flip cannot land silently — but it does require an edit here, and this"
      + " module does not claim otherwise",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// THE ONBOARDING SURFACE ITSELF, and why this slice does not ship one.
// ---------------------------------------------------------------------------

/**
 * WHETHER THE FLOW IS A PAGE A PARTNER CAN WALK ON A PHONE.
 *
 * The review of PR 992 asked for exactly the right thing: an authenticated,
 * mobile-navigable product surface for this flow, phone-width verified. It is
 * not built here, and the reason is a boundary rather than an omission.
 *
 * WHAT WOULD HAVE TO HAPPEN, and who owns each part. An authenticated surface in
 * this product is three things at once: a file under dealroom/, a canonical route
 * the router serves, and a ROW IN J101'S `AUTHENTICATED_SURFACES` — the
 * workspace's own inventory of itself, which is the authority every consumer
 * (this module included) reads the shell's shape from. A file added under
 * dealroom/ with no row is not a surface; it is an unreachable page. And the row
 * is J101's to write, not R01's: this slice may not edit another slice's
 * inventory of its own shell without turning that inventory into two tables that
 * disagree, which is the very defect the derivation above just removed.
 *
 * SO THE HONEST ANSWER IS A NAMED SEAM, and phone-width behaviour stays an open
 * question rather than a claim: nothing in this repository records whether the
 * five surfaces without the phone bar lay out correctly at phone width, and this
 * module will not answer it by looking at its own steps.
 */
export function onboardingSurfaceStatus(...extra) {
  // It takes NOTHING, so any argument at all is a caller trying to hand something
  // in — a holder, a request, a fixture. The arity is the boundary.
  if (extra.length > 0) {
    fail("onboardingSurfaceStatus_holder_is_not_an_argument",
      "onboardingSurfaceStatus takes no argument; a surface registry is not one",
      { arguments_received: extra.length });
  }
  return deepFreeze({
    answer: "onboardingSurfaceStatus",
    schema_version: V5_R01_ONBOARDING_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    request_read: false,
    dedicated_onboarding_surface_exists: false,
    dedicated_onboarding_surface_asset: null,
    dedicated_onboarding_surface_routes: [],
    owed_seams: [
      V5_R01_SEAMS.onboarding_surface_registration.seam,
      V5_R01_SEAMS.onboarding_enrollment_store.seam,
    ].sort(),
    what_the_surface_seam_needs: "a row in the workspace surface inventory (V5-J101's"
      + " AUTHENTICATED_SURFACES), a canonical route the router serves, and the asset itself;"
      + " the inventory row is J101's to write, and a page with no row is unreachable",
    what_the_enrollment_seam_needs: "a durable per-partner record of which steps are behind him,"
      + " so a partner who stops on step four resumes on step four rather than starting again",
    phone_width_behaviour: null,
    phone_width_behaviour_is_unknown_because:
      "phone-width layout is a rendering fact about HTML files and nothing in this repository"
      + " records it; it is measured in a browser at a real width, not asserted in a module",
    steps_a_partner_would_walk: V5_R01_ONBOARDING_STEPS.length,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// THE EVALUATORS. All three answer without reading, and none takes a holder.
// ---------------------------------------------------------------------------

/**
 * A caller offering an authority holder has misread this module. The ARITY is the
 * boundary: there is no store parameter and no observer parameter.
 */
function assertNoHolder(extra, name) {
  if (extra === undefined) return;
  fail(`${name}_holder_is_not_an_argument`,
    `${name} takes one request; a store, registry, ledger or observer is not an argument`,
    { hint: "the owner of this fact does not exist in this repository" });
}

/**
 * THE ONE SHAPE EVERY UNAVAILABLE ANSWER HERE HAS. Every field is a module
 * constant or a literal; nothing is derived from a request. Two callers handing
 * in opposite evidence get byte-identical answers, and the suite digests every
 * caller-controlled shape it can build to prove it.
 */
function unavailable(answer, seams, reason_id, because, extra = {}) {
  return deepFreeze({
    answer,
    schema_version: V5_R01_ONBOARDING_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    status: "unavailable",
    decision: "unavailable",
    reason_id,
    unavailable_because: because,
    owed_seams: seams.map(seam => seam.seam).sort(),
    seams_bound: deepFreeze(seams.map(seam => ({ seam: seam.seam, holds: seam.holds, bound: false }))),
    request_read: false,
    caller_evidence_admitted: false,
    decided_by: "no_authoritative_reader",
    authority_established: false,
    state_holder_is_caller_supplied: false,
    model_judgment_admitted: false,
    ...extra,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Where is this partner up to, and is the flow finished?
 *
 * IT DOES NOT ANSWER, AND IT DOES NOT LOOK. How far a partner has got is a fact
 * an enrollment store holds. A caller passing `steps_completed` is passing its
 * own opinion of the answer, and a module that read it would be letting the
 * caller finish its own onboarding by declaring it finished.
 *
 * The previous round read it anyway — it ran the ladder and reported the step
 * that was outstanding along with the conditional verdict for the whole flow.
 * That was a classification of caller input coming back through a public export.
 * The ladder still exists and is still proved, one rung at a time, in
 * mcp-server/test/rollout-pilot-r01-classifiers.v5.testhelper.mjs, where a
 * consumer cannot reach it.
 */
export function readOnboardingProgress(request, ...extra) {
  assertNoHolder(extra[0], "readOnboardingProgress");
  return unavailable("readOnboardingProgress",
    [V5_R01_SEAMS.onboarding_enrollment_store],
    "onboarding_enrollment_store_absent",
    "no enrollment store exists, so how far a partner has got can only be asserted by its caller",
    {
      total_steps: V5_R01_ONBOARDING_STEPS.length,
      steps_in_order: [...V5_R01_ONBOARDING_STEP_IDS],
      steps_finished_observed: null,
      flow_is_saved_today: false,
      flow_is_resumable_today: false,
    });
}

/**
 * Can the beta partner operate the product on his own?
 *
 * TWO THINGS ARE TRUE HERE AND THEY ARE DIFFERENT.
 *
 * The first is the ordinary unavailable: nobody in this repository can say
 * whether Dell did realistic work unaided, because the observer who would say so
 * does not exist, and neither does the enrollment store behind him.
 *
 * The second is settled and survives both stores arriving. Q010: Dell must
 * eventually be able to operate the system while Joe is away, but "for now i dont
 * want to sacrifice speed on the roll out or any other qualities or capabilities
 * for this". So `blocks_j1: false` rides on every result. Dell's independence is
 * a goal of this slice; it is not a gate on J1, and this module will not become
 * one.
 */
export function evaluateBetaOperability(request, ...extra) {
  assertNoHolder(extra[0], "evaluateBetaOperability");
  return unavailable("evaluateBetaOperability",
    [V5_R01_SEAMS.outside_observer, V5_R01_SEAMS.onboarding_enrollment_store],
    "outside_observer_receipt_absent",
    "whether a partner worked unaided is an outside judgement; no outside observer receipt"
    + " store exists to read one from",
    {
      beta_partner: V5_R01_BETA_PARTNER,
      forbidden_tool_classes: [...V5_R01_FORBIDDEN_TOOL_CLASSES],
      permitted_tool_classes: [...V5_R01_PERMITTED_TOOL_CLASSES],
      author_interventions_observed: null,
      blocks_j1: false,
      blocks_j1_basis: "Q010 — Dell's eventual unaided operation must not cost rollout speed"
        + " or any other capability now",
    });
}

/**
 * Someone asking Dell to sign off a slice. Refused, on the merits, every time —
 * and without reading the request, so the refusal is the same refusal for
 * everyone.
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
  return deepFreeze({
    answer: "evaluatePerSliceDellReview",
    schema_version: V5_R01_ONBOARDING_SCHEMA_VERSION,
    policy_version: V5_R01_POLICY_VERSION,
    status: "refused",
    decision: "refuse",
    reason_id: "per_slice_dell_review_declined_by_q009",
    request_read: false,
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
      resumable_today: step.resumable_today,
      mobile_reach: step.mobile_reach,
    })),
    forbidden_tool_classes: [...V5_R01_FORBIDDEN_TOOL_CLASSES],
    permitted_tool_classes: [...V5_R01_PERMITTED_TOOL_CLASSES],
    owed_seams: [
      V5_R01_SEAMS.onboarding_enrollment_store.seam,
      V5_R01_SEAMS.onboarding_surface_registration.seam,
    ].sort(),
    dedicated_onboarding_surface_exists: false,
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
  "onboardingSurfaceStatus",
  "readOnboardingProgress",
  "v5R01OnboardingCanonicalBytes",
  "v5R01OnboardingDigest",
  "v5R01OnboardingPreimage",
]);

// This module hands back no classification at all. The property that replaced
// that load-time check is stronger and is asserted from outside: the suite reads
// the SOURCE of every file in mcp-server/src and fails if any of them contains a
// `would_*` classification token.
