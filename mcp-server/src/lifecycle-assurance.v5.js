// DoctorCRE v5 slice V5-A02, half two: WORKFLOW LIFECYCLE AND RULE DELIVERY
// ASSURANCE — the complex/short workflow lifecycle checks, the rule
// proposed -> reviewed -> tested/shadow -> active -> retired transitions, and
// the enforcement-plus-fallback coverage of every active rule.
//
// THIS IS THE OTHER HALF OF V5-A02. gate-zero-assurance.v5.js owns the Gate
// Zero join and the non-green propagation graph. This file owns the two
// lifecycles the catalog's concrete_output names beside it, and the third
// checkable_done item: "every active rule maps to enforceable control and
// fallback".
//
// NOTHING HERE INVENTS A VOCABULARY IT COULD READ INSTEAD.
//
//   * THE ELEVEN WORKFLOW LIFECYCLE STATES AND THEIR PRECEDENCE are V5-F09's,
//     lifted from the live `ops.completion_projection` view (db/schema.sql, the
//     `possibility(lifecycle_state, applies)` VALUES list) in its own order.
//     F09 is one of this slice's three source_build_dependencies and its census
//     is what "workflow lifecycle" means in this system; a second ordering here
//     would be a second authority. The projection is SQL this module cannot
//     import, so lifecycle-assurance.v5.test.mjs reads db/schema.sql and asserts
//     the two lists are identical, in order — a schema change turns this test
//     red instead of letting the two drift apart in silence.
//   * THE SIX RULE CLASSES, THE SIX ENFORCEMENT MECHANISMS and THE THREE
//     RETIREMENT BEHAVIOURS are V5-F05's and are IMPORTED from
//     rule-applicability.v5.js, never retyped, so the day F05 adds one this
//     module follows instead of drifting.
//   * THE RULE LIFECYCLE STATES are the catalog's own words for this slice:
//     "rule proposed -> reviewed -> tested/shadow -> active -> retired
//     transitions and enforcement coverage".
//
// THE TWO EXCLUSIONS ARE ENFORCED, NOT DOCUMENTED. The catalog's excluded_scope
// for V5-A02 reads "product activation", "rule presence as enforcement proof"
// and "shadow misses without disposition". Each has a named refusal below:
//
//   product activation                 -> V5_A02_RULE_ACTIVATION_SEAM is null;
//                                         emitRuleActivation cannot return an
//                                         activation for any caller on any input.
//   rule presence as enforcement proof  -> `rule_presence_is_not_enforcement`. A
//                                         rule that names only its own binding
//                                         text is UNMAPPED, however well written.
//   shadow misses without disposition   -> `shadow_miss_without_disposition`. A
//                                         shadow window with an undisposed miss
//                                         cannot advance a rule to active.
//
// A CALLER-SUPPLIED FACT IS NEVER AUTHORITY. A caller may DECLARE what it
// observed; it may not declare the conclusion. `deriveWorkflowLifecycleState`
// computes the state from the evidence and `evaluateWorkflowLifecycle` refuses a
// claimed state that the evidence does not produce, rather than believing the
// claim. There is no `verified`, `enforced_trust_me`, `waived`, `approved_by_me`
// or controller-injection field anywhere below, and a closed schema refuses one.
//
// EVERY FUNCTION IS PURE — no filesystem, no network, no database, no clock, no
// environment. Every evaluation that depends on time takes `as_of` from its
// caller. `V5_NO_EFFECTS` rides on every result.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — `decision` is "allow" or "refuse" with a
//     stable `reason_id`. A coverage answer that lists unmapped rules is an
//     ANSWER the caller records, not an exception.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, open schemas,
//     unknown enum members and unreachable transitions are not policy questions.
//
// DECISION BINDING. Q017.D1, Q036.D1, Q067.D1 and Q086.D1, carried in
// V5_A02_DECISION_IDS by gate-zero-assurance.v5.js and re-exported here so both
// halves of the slice bind one list. Only their routing is readable (see that
// file's header); this module claims no knowledge of their text.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_F05_ENFORCEMENT_MECHANISMS,
  V5_F05_RETIREMENT_BEHAVIORS,
  V5_F05_RULE_CLASSES,
} from "./rule-applicability.v5.js";
import { V5_A02_DECISION_IDS } from "./gate-zero-assurance.v5.js";

export { V5_A02_DECISION_IDS, V5_NO_EFFECTS };

export const V5_A02_LIFECYCLE_SCHEMA_VERSION = "doctorcre-v5-a02-lifecycle-assurance.v1";
export const V5_A02_LIFECYCLE_POLICY_VERSION = 1;

const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const IDENTIFIER = /^[a-z0-9][a-z0-9._-]{0,127}$/;

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

function object(value, path) {
  if (!isPlainObject(value)) fail("not_an_object", `${path} must be a plain object`, { path });
  return value;
}

function closed(value, allowed, path) {
  object(value, path);
  for (const key of Object.keys(value))
    if (!allowed.includes(key))
      fail("unknown_field", `${path}.${key} is not a field this module reads`,
        { path, key, allowed: [...allowed].sort() });
  return value;
}

function exact(value, allowed, path) {
  closed(value, allowed, path);
  for (const key of allowed)
    if (!Object.hasOwn(value, key))
      fail("missing_field", `${path}.${key} is required`, { path, key });
  return value;
}

function str(value, path) {
  if (typeof value !== "string" || value.length === 0)
    fail("not_a_string", `${path} must be a non-empty string`, { path });
  return value;
}

function pattern(value, re, code, path) {
  str(value, path);
  if (!re.test(value)) fail(code, `${path} is malformed`, { path, value });
  return value;
}

function instant(value, path) {
  str(value, path);
  if (!ISO_INSTANT.test(value) || Number.isNaN(Date.parse(value)))
    fail("malformed_instant", `${path} must be a UTC ISO-8601 instant`, { path, value });
  return Date.parse(value);
}

function member(value, allowed, path) {
  str(value, path);
  if (!allowed.includes(value))
    fail("unknown_enum_member", `${path} is not a member this module knows`,
      { path, value, allowed: [...allowed].sort() });
  return value;
}

function bool(value, path) {
  if (typeof value !== "boolean") fail("not_a_boolean", `${path} must be a boolean`, { path });
  return value;
}

function list(value, path) {
  if (!Array.isArray(value)) fail("not_an_array", `${path} must be an array`, { path });
  return value;
}

function sortedUnique(values, path, validate) {
  list(values, path);
  values.forEach((value, index) => validate(value, `${path}[${index}]`));
  for (let index = 1; index < values.length; index += 1) {
    if (values[index] === values[index - 1])
      fail("duplicate_member", `${path} repeats ${values[index]}`, { path, value: values[index] });
    if (values[index] < values[index - 1])
      fail("unsorted_list", `${path} must be C-sorted`, { path, at: index });
  }
  return values;
}

// ---------------------------------------------------------------------------
// Every refusal this module can answer with. Closed, so a reason is checkable.
// ---------------------------------------------------------------------------

export const V5_A02_LIFECYCLE_REASON_IDS = deepFreeze([
  "active_rule_fallback_absent",
  "active_rule_control_unmapped",
  "mandatory_rule_without_machine_control",
  "rule_activation_seam_unavailable",
  "rule_control_mechanism_not_for_class",
  "rule_presence_is_not_enforcement",
  "rule_retirement_successor_absent",
  "rule_reviewer_not_independent",
  "rule_state_transition_not_permitted",
  "rule_tests_absent",
  "rule_tests_not_passing",
  "shadow_miss_without_disposition",
  "shadow_window_absent",
  "short_workflow_drops_proof_dimension",
  "workflow_operational_without_proof",
  "workflow_required_dimension_missing",
  "workflow_state_claim_not_derived",
].sort());

function reason(id) {
  if (!V5_A02_LIFECYCLE_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a registered reason`, { reason_id: id });
  return id;
}

// ---------------------------------------------------------------------------
// WORKFLOW LIFECYCLE. V5-F09's eleven states, in V5-F09's precedence order.
//
// The order is load-bearing and is NOT alphabetical: `ops.completion_projection`
// picks the FIRST applicable possibility in this order, so `conflicting` beats
// `blocked` beats `planned`, and `operational` is last because it is only
// reached when nothing earlier applies. Reordering this list changes what the
// checker says a workflow IS.
// ---------------------------------------------------------------------------

export const V5_A02_WORKFLOW_LIFECYCLE_STATES = deepFreeze([
  "conflicting", "canceled", "superseded", "unknown_stale", "blocked",
  "planned", "built_unmerged", "merged_unactivated", "active_unproven",
  "partially_built", "operational",
]);

/** The positive evidence dimensions a workflow accumulates, C-sorted. */
export const V5_A02_WORKFLOW_DIMENSIONS = deepFreeze([
  "activation", "artifact", "canonical", "intent", "readback", "telemetry",
]);

/**
 * THE TWO DIMENSIONS NO WORKFLOW MAY DROP, and this is F09's rule rather than
 * this module's: `ops.completion_projection` answers `active_unproven` whenever
 * `has_activation AND (NOT has_readback OR NOT has_telemetry)`, so a workflow
 * without readback and telemetry can never be operational. A "short" workflow
 * that declared them un-required would be laundering an unproven activation
 * into operational by shortening its own requirement list, which is exactly the
 * shape this half of the slice exists to catch.
 */
export const V5_A02_MANDATORY_PROOF_DIMENSIONS = deepFreeze(["readback", "telemetry"]);

/**
 * A COMPLEX workflow is built, merged, activated and proved: it declares all six
 * dimensions. A SHORT one has no separate build artifact — its canonical form is
 * the only form it ever has, which is what a configuration, rule or policy
 * change looks like — so it may declare five, dropping `artifact` and nothing
 * else.
 *
 * WHY `canonical` IS NOT THE DROPPABLE ONE, since that is the intuitive guess
 * and it is wrong: F09's projection answers `built_unmerged` for
 * `has_artifact AND NOT has_canonical`, and that clause sits ABOVE
 * `operational` in the precedence. A workflow with an artifact and no canonical
 * is therefore permanently `built_unmerged` — it cannot reach operational by any
 * evidence at all. `canonical` is structurally undroppable under F09's own
 * derivation, and a "short" lifecycle defined as dropping it would be a
 * lifecycle nothing could ever finish. See the test that proves it.
 */
export const V5_A02_WORKFLOW_KINDS = deepFreeze(["complex", "short"]);
export const V5_A02_SHORT_WORKFLOW_DROPPABLE = deepFreeze(["artifact"]);

/** The workflow's terminal disposition, when it has one. */
export const V5_A02_WORKFLOW_DISPOSITIONS = deepFreeze(["canceled", "none", "superseded"]);

const WORKFLOW_EVIDENCE_FIELDS = Object.freeze([
  "has_activation", "has_artifact", "has_blocker", "has_canonical", "has_conflict",
  "has_intent", "has_readback", "has_stale", "has_telemetry",
]);
const WORKFLOW_REQUEST_FIELDS = Object.freeze([
  "workflow_ref", "workflow_kind", "required_dimensions", "disposition",
  "evidence", "claimed_state",
]);

/**
 * The eleven-way derivation, in F09's precedence order. Pure: it reads the nine
 * observed booleans and the disposition, and nothing else.
 *
 * `everyRequiredPresent` is supplied rather than recomputed here so the caller's
 * DECLARED requirement set is what decides `partially_built` versus
 * `operational` — which is precisely why the requirement set itself is validated
 * first, by evaluateWorkflowLifecycle.
 */
export function deriveWorkflowLifecycleState(evidence, disposition, everyRequiredPresent) {
  exact(object(evidence, "evidence"), WORKFLOW_EVIDENCE_FIELDS, "evidence");
  for (const key of WORKFLOW_EVIDENCE_FIELDS) bool(evidence[key], `evidence.${key}`);
  member(disposition, V5_A02_WORKFLOW_DISPOSITIONS, "disposition");
  bool(everyRequiredPresent, "everyRequiredPresent");

  if (evidence.has_conflict) return "conflicting";
  if (disposition === "canceled") return "canceled";
  if (disposition === "superseded") return "superseded";
  if (evidence.has_stale) return "unknown_stale";
  if (evidence.has_blocker) return "blocked";
  if (evidence.has_intent && !evidence.has_artifact && !evidence.has_canonical) return "planned";
  if (evidence.has_artifact && !evidence.has_canonical) return "built_unmerged";
  if (evidence.has_canonical && !evidence.has_activation) return "merged_unactivated";
  if (evidence.has_activation && (!evidence.has_readback || !evidence.has_telemetry))
    return "active_unproven";
  if (!everyRequiredPresent) return "partially_built";
  return "operational";
}

/**
 * The complex/short lifecycle check.
 *
 * Ordered questions, so a second reader reaches the same verdict:
 *   1. Does the declared requirement set keep both proof dimensions?  If not,
 *      `short_workflow_drops_proof_dimension` — answered before the state is
 *      derived, because a shortened requirement set would otherwise make
 *      `operational` come out true.
 *   2. Is the declared set legal for the declared kind? A complex workflow
 *      declaring fewer than all six, or a short one dropping anything but
 *      `canonical`, is `workflow_required_dimension_missing`.
 *   3. What state does the evidence DERIVE to?
 *   4. Does that state claim `operational` without readback and telemetry?
 *      (Unreachable by construction, and asserted anyway — a derivation that
 *      could reach it would be the defect.)
 *   5. Does the caller's claimed state match the derived one?
 */
export function evaluateWorkflowLifecycle(request) {
  exact(object(request, "request"), WORKFLOW_REQUEST_FIELDS, "request");
  pattern(request.workflow_ref, REF, "malformed_ref", "request.workflow_ref");
  const kind = member(request.workflow_kind, V5_A02_WORKFLOW_KINDS, "request.workflow_kind");
  sortedUnique(request.required_dimensions, "request.required_dimensions",
    (value, path) => member(value, V5_A02_WORKFLOW_DIMENSIONS, path));
  const disposition = member(request.disposition, V5_A02_WORKFLOW_DISPOSITIONS, "request.disposition");
  const claimed = member(request.claimed_state, V5_A02_WORKFLOW_LIFECYCLE_STATES, "request.claimed_state");

  const declared = new Set(request.required_dimensions);
  const droppedProof = V5_A02_MANDATORY_PROOF_DIMENSIONS.filter(dim => !declared.has(dim));
  const expected = kind === "complex"
    ? [...V5_A02_WORKFLOW_DIMENSIONS]
    : V5_A02_WORKFLOW_DIMENSIONS.filter(dim => !V5_A02_SHORT_WORKFLOW_DROPPABLE.includes(dim));
  const missingRequired = expected.filter(dim => !declared.has(dim));
  const overDeclared = [...declared].filter(dim => !expected.includes(dim)).sort();

  const dimensionPresent = dim => ({
    activation: request.evidence?.has_activation,
    artifact: request.evidence?.has_artifact,
    canonical: request.evidence?.has_canonical,
    intent: request.evidence?.has_intent,
    readback: request.evidence?.has_readback,
    telemetry: request.evidence?.has_telemetry,
  })[dim] === true;

  const everyRequiredPresent = request.required_dimensions.every(dimensionPresent);
  const derived = deriveWorkflowLifecycleState(request.evidence, disposition, everyRequiredPresent);

  const unmet = request.required_dimensions.filter(dim => !dimensionPresent(dim));
  const operationalWithoutProof = derived === "operational" &&
    V5_A02_MANDATORY_PROOF_DIMENSIONS.some(dim => !dimensionPresent(dim));

  let reasonId = null;
  if (droppedProof.length > 0) reasonId = "short_workflow_drops_proof_dimension";
  else if (missingRequired.length > 0 || overDeclared.length > 0) reasonId = "workflow_required_dimension_missing";
  else if (operationalWithoutProof) reasonId = "workflow_operational_without_proof";
  else if (claimed !== derived) reasonId = "workflow_state_claim_not_derived";

  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    answer: "workflow_lifecycle",
    tenant: ORGANIZATION_TENANT_ID,
    workflow_ref: request.workflow_ref,
    workflow_kind: kind,
    decision: reasonId === null ? "allow" : "refuse",
    reason_id: reasonId === null ? null : reason(reasonId),
    // The DERIVED state is the state. `claimed_state` is echoed so a mismatch is
    // legible, never so it can win.
    derived_state: derived,
    claimed_state: claimed,
    claim_matches_derivation: claimed === derived,
    required_dimensions: [...request.required_dimensions],
    expected_required_dimensions: expected,
    required_dimensions_missing_from_declaration: missingRequired,
    required_dimensions_over_declared: overDeclared,
    required_dimensions_unmet_by_evidence: unmet.sort(),
    proof_dimensions: [...V5_A02_MANDATORY_PROOF_DIMENSIONS],
    proof_dimensions_dropped: droppedProof,
    state_precedence: [...V5_A02_WORKFLOW_LIFECYCLE_STATES],
    caller_stated_state_honoured: false,
    decided_by: "deterministic_checker",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// RULE LIFECYCLE. proposed -> reviewed -> tested/shadow -> active -> retired.
// ---------------------------------------------------------------------------

export const V5_A02_RULE_STATES = deepFreeze([
  "proposed", "reviewed", "tested", "shadow", "active", "retired",
]);

/**
 * The permitted edges, as an explicit table rather than a rule about a rule.
 * `active -> shadow` is the catalog's "reversible activation": switching a rule
 * off returns it to observation, it does not delete it. `retired` is terminal —
 * a retired rule that could walk back to active would make retirement a mood.
 */
export const V5_A02_RULE_TRANSITIONS = deepFreeze({
  proposed: ["retired", "reviewed"],
  reviewed: ["retired", "shadow", "tested"],
  tested: ["active", "retired", "shadow"],
  shadow: ["active", "retired"],
  active: ["retired", "shadow"],
  retired: [],
});

/** What a shadow observation did about each miss it saw. "none" is not one. */
export const V5_A02_SHADOW_MISS_DISPOSITIONS = deepFreeze([
  "amended_rule", "confirmed_gap", "false_positive", "widened_control",
]);

/** What happens when the control is unavailable. Every active rule owes one. */
export const V5_A02_FALLBACK_KINDS = deepFreeze([
  "degraded_read_only", "documented_manual_procedure",
  "escalate_to_verified_partner", "refuse_closed",
]);

/**
 * The mechanisms that are MACHINE control. A mandatory rule enforced only by a
 * model reading prose or a stated preference is the defect V5-F05's class table
 * names: a rule whose only enforcement is a mind reading text cannot claim to
 * deny an action.
 */
export const V5_A02_MACHINE_ENFORCEMENT_MECHANISMS = deepFreeze([
  "behavioral_test", "code_control", "runtime_state_flag", "workflow_definition",
]);

const CONTROL_FIELDS = Object.freeze([
  "control_id", "control_ref", "enforcement_mechanism",
  "implementation_digest", "verifier_id", "verified_at",
]);
const FALLBACK_FIELDS = Object.freeze(["kind", "ref"]);
const REVIEW_FIELDS = Object.freeze(["proposer_actor_id", "reviewer_actor_id"]);
const TEST_FIELDS = Object.freeze(["test_ref", "result"]);
const SHADOW_FIELDS = Object.freeze(["window_ref", "opened_at", "closed_at", "misses"]);
const MISS_FIELDS = Object.freeze(["miss_ref", "disposition"]);
const RETIREMENT_FIELDS = Object.freeze(["behavior", "successor_rule_id"]);
const TRANSITION_FIELDS = Object.freeze([
  "rule_id", "rule_class", "mandatory", "from_state", "to_state",
  "review", "tests", "shadow_window", "control", "fallback", "retirement",
]);

function refusedTransition(reasonId, note, detail) {
  return { ok: false, reason_id: reason(reasonId), note, detail: detail ?? {} };
}

function validateControl(control, path) {
  if (control === null) return null;
  exact(object(control, path), CONTROL_FIELDS, path);
  pattern(control.control_id, IDENTIFIER, "malformed_identifier", `${path}.control_id`);
  pattern(control.control_ref, REF, "malformed_ref", `${path}.control_ref`);
  member(control.enforcement_mechanism, V5_F05_ENFORCEMENT_MECHANISMS, `${path}.enforcement_mechanism`);
  pattern(control.implementation_digest, DIGEST, "malformed_digest", `${path}.implementation_digest`);
  str(control.verifier_id, `${path}.verifier_id`);
  instant(control.verified_at, `${path}.verified_at`);
  return control;
}

function validateFallback(fallback, path) {
  if (fallback === null) return null;
  exact(object(fallback, path), FALLBACK_FIELDS, path);
  member(fallback.kind, V5_A02_FALLBACK_KINDS, `${path}.kind`);
  pattern(fallback.ref, REF, "malformed_ref", `${path}.ref`);
  return fallback;
}

/**
 * One transition, as a PURE PREDICATE. It says whether the edge is permitted and
 * whether the evidence the TARGET state demands is present. It moves nothing:
 * only emitRuleActivation could, and it cannot.
 *
 * Evidence owed per target state, each with its own refusal:
 *   reviewed -> an independent reviewer (not the proposer)
 *   tested   -> a named behavioural test that PASSED
 *   shadow   -> a closed shadow window, every miss carrying a disposition
 *   active   -> an enforceable control AND a fallback; and for a MANDATORY rule
 *               that control must be machine control
 *   retired  -> a retirement behaviour, plus a successor when the behaviour is
 *               `superseded_only`
 */
export function evaluateRuleTransition(request) {
  exact(object(request, "request"), TRANSITION_FIELDS, "request");
  pattern(request.rule_id, IDENTIFIER, "malformed_identifier", "request.rule_id");
  member(request.rule_class, V5_F05_RULE_CLASSES, "request.rule_class");
  bool(request.mandatory, "request.mandatory");
  const from = member(request.from_state, V5_A02_RULE_STATES, "request.from_state");
  const to = member(request.to_state, V5_A02_RULE_STATES, "request.to_state");

  if (request.review !== null) {
    exact(object(request.review, "request.review"), REVIEW_FIELDS, "request.review");
    str(request.review.proposer_actor_id, "request.review.proposer_actor_id");
    str(request.review.reviewer_actor_id, "request.review.reviewer_actor_id");
  }
  if (request.tests !== null) {
    list(request.tests, "request.tests");
    request.tests.forEach((test, index) => {
      const path = `request.tests[${index}]`;
      exact(object(test, path), TEST_FIELDS, path);
      pattern(test.test_ref, REF, "malformed_ref", `${path}.test_ref`);
      member(test.result, ["fail", "pass", "skipped"], `${path}.result`);
    });
  }
  if (request.shadow_window !== null) {
    const path = "request.shadow_window";
    exact(object(request.shadow_window, path), SHADOW_FIELDS, path);
    pattern(request.shadow_window.window_ref, REF, "malformed_ref", `${path}.window_ref`);
    instant(request.shadow_window.opened_at, `${path}.opened_at`);
    if (request.shadow_window.closed_at !== null) instant(request.shadow_window.closed_at, `${path}.closed_at`);
    list(request.shadow_window.misses, `${path}.misses`);
    request.shadow_window.misses.forEach((miss, index) => {
      const at = `${path}.misses[${index}]`;
      exact(object(miss, at), MISS_FIELDS, at);
      pattern(miss.miss_ref, REF, "malformed_ref", `${at}.miss_ref`);
      if (miss.disposition !== null)
        member(miss.disposition, V5_A02_SHADOW_MISS_DISPOSITIONS, `${at}.disposition`);
    });
  }
  const control = validateControl(request.control, "request.control");
  const fallback = validateFallback(request.fallback, "request.fallback");
  if (request.retirement !== null) {
    const path = "request.retirement";
    exact(object(request.retirement, path), RETIREMENT_FIELDS, path);
    member(request.retirement.behavior, V5_F05_RETIREMENT_BEHAVIORS, `${path}.behavior`);
    if (request.retirement.successor_rule_id !== null)
      pattern(request.retirement.successor_rule_id, IDENTIFIER, "malformed_identifier",
        `${path}.successor_rule_id`);
  }

  let outcome = { ok: true, reason_id: null, note: `${from} -> ${to} permitted with its evidence`, detail: {} };

  if (!V5_A02_RULE_TRANSITIONS[from].includes(to)) {
    outcome = refusedTransition("rule_state_transition_not_permitted",
      `${from} -> ${to} is not an edge of the rule lifecycle`,
      { from_state: from, to_state: to, permitted: [...V5_A02_RULE_TRANSITIONS[from]] });
  } else if (to === "reviewed") {
    if (request.review === null)
      outcome = refusedTransition("rule_reviewer_not_independent",
        "review states no reviewer at all", { rule_id: request.rule_id });
    else if (request.review.reviewer_actor_id === request.review.proposer_actor_id)
      outcome = refusedTransition("rule_reviewer_not_independent",
        "the proposer may not be the reviewer",
        { actor_id: request.review.proposer_actor_id });
  } else if (to === "tested") {
    const tests = request.tests ?? [];
    if (tests.length === 0)
      outcome = refusedTransition("rule_tests_absent",
        "a rule cannot be tested by no test", { rule_id: request.rule_id });
    else {
      const notPassing = tests.filter(test => test.result !== "pass").map(test => test.test_ref).sort();
      if (notPassing.length > 0)
        outcome = refusedTransition("rule_tests_not_passing",
          "every named test must have passed", { not_passing: notPassing });
    }
  } else if (to === "shadow") {
    if (request.shadow_window === null)
      outcome = refusedTransition("shadow_window_absent",
        "shadow observation needs a shadow window", { rule_id: request.rule_id });
  } else if (to === "active") {
    // Shadow misses are checked FIRST, because the catalog excludes "shadow
    // misses without disposition" from what may count, and an undisposed miss
    // is an open question about the very control being switched on.
    const misses = request.shadow_window?.misses ?? [];
    const undisposed = misses.filter(miss => miss.disposition === null).map(miss => miss.miss_ref).sort();
    if (undisposed.length > 0)
      outcome = refusedTransition("shadow_miss_without_disposition",
        "a shadow miss nobody dispositioned is an open question, not evidence",
        { undisposed });
    else if (control === null)
      outcome = refusedTransition("active_rule_control_unmapped",
        "an active rule must map to a control that actually enforces it",
        { rule_id: request.rule_id });
    else if (fallback === null)
      outcome = refusedTransition("active_rule_fallback_absent",
        "an active rule must say what happens when its control is unavailable",
        { rule_id: request.rule_id, control_id: control.control_id });
    else if (request.mandatory &&
      !V5_A02_MACHINE_ENFORCEMENT_MECHANISMS.includes(control.enforcement_mechanism))
      outcome = refusedTransition("mandatory_rule_without_machine_control",
        "a mandatory rule enforced only by judgment or preference denies nothing",
        { rule_id: request.rule_id, enforcement_mechanism: control.enforcement_mechanism });
  } else if (to === "retired") {
    if (request.retirement === null)
      outcome = refusedTransition("rule_retirement_successor_absent",
        "retirement states no behaviour", { rule_id: request.rule_id });
    else if (request.retirement.behavior === "superseded_only" &&
      request.retirement.successor_rule_id === null)
      outcome = refusedTransition("rule_retirement_successor_absent",
        "a superseded_only retirement must name the rule that supersedes it",
        { rule_id: request.rule_id });
  }

  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    answer: "rule_lifecycle_transition",
    tenant: ORGANIZATION_TENANT_ID,
    rule_id: request.rule_id,
    from_state: from,
    to_state: to,
    decision: outcome.ok ? "allow" : "refuse",
    reason_id: outcome.reason_id,
    note: outcome.note,
    detail: deepFreeze(outcome.detail),
    permitted_transitions_from: [...V5_A02_RULE_TRANSITIONS[from]],
    reversible_activation: from === "active" && to === "shadow",
    // An allow records that the clause is satisfied. It moves no rule.
    performs_transition: false,
    decided_by: "deterministic_checker",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// THE RULE ACTIVATION SEAM, NAMED BY THIS MODULE.
// ---------------------------------------------------------------------------

/**
 * The seam an activation would be performed through. A NAME, not a port: no
 * exported function accepts a controller as an argument and this module exports
 * no way to bind one.
 */
export const V5_A02_RULE_ACTIVATION_SEAM = "seam:rule-registry-activation-controller";

/**
 * Bound to null. The catalog's excluded_scope for V5-A02 begins "product
 * activation", and no activation controller with a reversible, receipted,
 * durable transition record exists in this repository for this seam to read.
 */
const V5_A02_RULE_ACTIVATION_CONTROLLER = null;

function ruleActivationController() {
  const controller = V5_A02_RULE_ACTIVATION_CONTROLLER;
  return isPlainObject(controller) && typeof controller.activate === "function" ? controller : null;
}

/**
 * THE ONLY FUNCTION THAT COULD ACTIVATE A RULE, AND IT CANNOT.
 *
 * One argument, and the arity IS the boundary. The answer is fixed for every
 * caller on every input: `activated: false`, naming the seam it is owed. The
 * deterministic transition predicate still runs and is still reported, because
 * an honest refusal says what it read.
 */
export function emitRuleActivation(request) {
  // eslint-disable-next-line prefer-rest-params -- the arity IS the boundary.
  if (arguments.length > 1)
    fail("rule_activation_controller_is_not_an_argument",
      "emitRuleActivation takes one request; the activation controller is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_A02_RULE_ACTIVATION_SEAM });

  const transition = evaluateRuleTransition(request);
  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    answer: "rule_activation",
    tenant: ORGANIZATION_TENANT_ID,
    rule_id: transition.rule_id,
    activated: false,
    decision: "refuse",
    reason_id: reason("rule_activation_seam_unavailable"),
    activation_seam: V5_A02_RULE_ACTIVATION_SEAM,
    controller_bound: ruleActivationController() !== null,
    controller_is_caller_supplied: false,
    transition,
    decided_by: "deterministic_checker",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// ENFORCEMENT COVERAGE — checkable_done 3.
// "Every active rule maps to enforceable control and fallback."
// ---------------------------------------------------------------------------

const COVERAGE_RULE_FIELDS = Object.freeze([
  "rule_id", "version", "rule_class", "state", "mandatory",
  "binding_text_present", "control", "fallback",
]);
const COVERAGE_REQUEST_FIELDS = Object.freeze(["as_of", "rules"]);

/**
 * The coverage answer over a whole rule set.
 *
 * ORDERED QUESTIONS per rule, so two readers reach the same verdict:
 *   1. Is the rule ACTIVE? If not it is out of scope and is reported as such —
 *      a proposed or retired rule owes no control.
 *   2. Does it name a control at all? If its only claim is that its binding text
 *      exists, it is UNMAPPED with `rule_presence_is_not_enforcement`. This is
 *      the catalog's excluded "rule presence as enforcement proof", enforced.
 *   3. Does the control's mechanism belong to the rule's class family? A
 *      code_enforced rule whose control is a partner preference is not enforced
 *      by that control.
 *   4. Is the rule mandatory with a non-machine control? Unmapped.
 *   5. Does it name a fallback? Without one, nobody knows what happens when the
 *      control is unavailable, and an unavailable control silently becomes no
 *      control.
 *
 * An unmapped rule is LISTED, with its reason, rather than hidden: the answer
 * refuses while the list is non-empty and still says exactly which rules and
 * why. `coverage_complete` is derived from that list and from nothing a caller
 * can set.
 */
export function evaluateRuleEnforcementCoverage(request) {
  exact(object(request, "request"), COVERAGE_REQUEST_FIELDS, "request");
  instant(request.as_of, "request.as_of");
  list(request.rules, "request.rules");

  const seen = new Set();
  const mapped = [];
  const unmapped = [];
  const outOfScope = [];

  request.rules.forEach((rule, index) => {
    const path = `request.rules[${index}]`;
    exact(object(rule, path), COVERAGE_RULE_FIELDS, path);
    pattern(rule.rule_id, IDENTIFIER, "malformed_identifier", `${path}.rule_id`);
    if (!Number.isInteger(rule.version) || rule.version < 1)
      fail("not_an_integer", `${path}.version must be a positive integer`, { path });
    member(rule.rule_class, V5_F05_RULE_CLASSES, `${path}.rule_class`);
    member(rule.state, V5_A02_RULE_STATES, `${path}.state`);
    bool(rule.mandatory, `${path}.mandatory`);
    bool(rule.binding_text_present, `${path}.binding_text_present`);
    const control = validateControl(rule.control, `${path}.control`);
    const fallback = validateFallback(rule.fallback, `${path}.fallback`);

    const key = `${rule.rule_id}@${rule.version}`;
    if (seen.has(key)) fail("duplicate_rule", `${path} repeats ${key}`, { path, rule: key });
    seen.add(key);

    if (rule.state !== "active") {
      outOfScope.push(deepFreeze({ rule_id: rule.rule_id, version: rule.version, state: rule.state }));
      return;
    }

    const record = {
      rule_id: rule.rule_id, version: rule.version, rule_class: rule.rule_class,
      mandatory: rule.mandatory,
      control_id: control?.control_id ?? null,
      control_ref: control?.control_ref ?? null,
      enforcement_mechanism: control?.enforcement_mechanism ?? null,
      fallback_kind: fallback?.kind ?? null,
      fallback_ref: fallback?.ref ?? null,
    };

    if (control === null) {
      unmapped.push(deepFreeze({
        ...record,
        reason_id: reason(rule.binding_text_present
          ? "rule_presence_is_not_enforcement"
          : "active_rule_control_unmapped"),
      }));
      return;
    }
    if (!classAdmitsMechanism(rule.rule_class, control.enforcement_mechanism)) {
      unmapped.push(deepFreeze({ ...record, reason_id: reason("rule_control_mechanism_not_for_class") }));
      return;
    }
    if (rule.mandatory && !V5_A02_MACHINE_ENFORCEMENT_MECHANISMS.includes(control.enforcement_mechanism)) {
      unmapped.push(deepFreeze({ ...record, reason_id: reason("mandatory_rule_without_machine_control") }));
      return;
    }
    if (fallback === null) {
      unmapped.push(deepFreeze({ ...record, reason_id: reason("active_rule_fallback_absent") }));
      return;
    }
    mapped.push(deepFreeze(record));
  });

  const complete = unmapped.length === 0;
  const blocking = complete ? null : unmapped[0].reason_id;

  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    answer: "rule_enforcement_coverage",
    tenant: ORGANIZATION_TENANT_ID,
    as_of: request.as_of,
    decision: complete ? "allow" : "refuse",
    reason_id: blocking,
    // Derived from the unmapped list and from nothing a caller can set. An
    // EMPTY active set is complete only in the trivial sense, and the counts
    // beside it say so rather than letting "0 unmapped" read as coverage.
    coverage_complete: complete,
    active_rule_count: mapped.length + unmapped.length,
    mapped_count: mapped.length,
    unmapped_count: unmapped.length,
    mapped_rules: deepFreeze(mapped),
    // Listed, not hidden. This is the "or is listed as unmapped" half.
    unmapped_rules: deepFreeze(unmapped),
    out_of_scope_rules: deepFreeze(outOfScope),
    fallback_kinds: [...V5_A02_FALLBACK_KINDS],
    machine_enforcement_mechanisms: [...V5_A02_MACHINE_ENFORCEMENT_MECHANISMS],
    caller_stated_coverage: false,
    decided_by: "deterministic_checker",
    effects: V5_NO_EFFECTS,
  });
}

/**
 * V5-F05's class table decides which mechanism belongs to which class. F05 does
 * not export the table itself, so the pairing is restated here as the
 * one-to-one map its RULE_CLASS_TABLE declares, and
 * lifecycle-assurance.v5.test.mjs asserts every class and every mechanism F05
 * exports appears here exactly once — so a class or mechanism added there
 * cannot pass unnoticed here.
 */
const CLASS_MECHANISM = deepFreeze({
  code_enforced: "code_control",
  workflow: "workflow_definition",
  test: "behavioral_test",
  scoped_judgment: "model_judgment",
  preference: "partner_preference",
  runtime_state: "runtime_state_flag",
});

function classAdmitsMechanism(ruleClass, mechanism) {
  return CLASS_MECHANISM[ruleClass] === mechanism;
}

export const V5_A02_CLASS_ENFORCEMENT_MECHANISM = CLASS_MECHANISM;

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
// ---------------------------------------------------------------------------

export function v5A02LifecyclePolicyPreimage() {
  return {
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_ids: [...V5_A02_DECISION_IDS],
    workflow_lifecycle_states_in_precedence_order: [...V5_A02_WORKFLOW_LIFECYCLE_STATES],
    workflow_dimensions: [...V5_A02_WORKFLOW_DIMENSIONS].sort(),
    workflow_kinds: [...V5_A02_WORKFLOW_KINDS].sort(),
    workflow_proof_dimensions: [...V5_A02_MANDATORY_PROOF_DIMENSIONS].sort(),
    short_workflow_droppable: [...V5_A02_SHORT_WORKFLOW_DROPPABLE].sort(),
    workflow_dispositions: [...V5_A02_WORKFLOW_DISPOSITIONS].sort(),
    rule_states: [...V5_A02_RULE_STATES],
    rule_transitions: Object.fromEntries(
      Object.keys(V5_A02_RULE_TRANSITIONS).sort()
        .map(from => [from, [...V5_A02_RULE_TRANSITIONS[from]].sort()])),
    shadow_miss_dispositions: [...V5_A02_SHADOW_MISS_DISPOSITIONS].sort(),
    fallback_kinds: [...V5_A02_FALLBACK_KINDS].sort(),
    machine_enforcement_mechanisms: [...V5_A02_MACHINE_ENFORCEMENT_MECHANISMS].sort(),
    class_enforcement_mechanism: Object.fromEntries(
      Object.keys(CLASS_MECHANISM).sort().map(key => [key, CLASS_MECHANISM[key]])),
    reason_ids: [...V5_A02_LIFECYCLE_REASON_IDS].sort(),
    rule_activation_seam: V5_A02_RULE_ACTIVATION_SEAM,
    rule_activation_controller_bound: false,
  };
}

export function v5A02LifecyclePolicyDigest() {
  return digest(v5A02LifecyclePolicyPreimage());
}

export function v5A02LifecyclePolicyCanonicalBytes() {
  return canonicalJson(v5A02LifecyclePolicyPreimage());
}
