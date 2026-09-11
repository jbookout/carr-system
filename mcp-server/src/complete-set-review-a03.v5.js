// DoctorCRE v5 slice V5-A03 — independent complete-set review and bounded
// adjudication.
//
// This module answers four questions and nothing else:
//
//   1. May this review be routed across the delivered set?  evaluateReviewRouting
//   2. Is this round's finding set the complete one?         evaluateFindingSetCompleteness
//   3. May another review round begin?                       evaluateReviewRoundAdmission
//   4. May this dispute be adjudicated, and how did it end?  evaluateAdjudication
//
// WHAT THIS FILE IS NOT. It routes nobody, dispatches no reviewer, records no
// finding, opens no round and adjudicates nothing. Like
// engineering-program-controller.v5.js, whose shape it follows deliberately, it
// is a PURE EVALUATOR over typed observations the caller supplies: no
// filesystem, no network, no database, no clock, no environment, every result
// frozen and carrying `effects: V5_NO_EFFECTS`.
//
// EVERY ONE OF THE FOUR REFUSES, ON EVERY INPUT, TODAY. That is the honest
// state of this repository and not a placeholder. Each decision ends at an
// authoritative holder that does not exist here, and the census below is the
// search that establishes it, one decision at a time. The test, inherited from
// F02, is: (a) does a code path in this repository PERFORM the action being
// decided, (b) does that path hold the facts the decision consumes from a
// source OTHER than the party being decided about, and (c) can the call be made
// without a new migration, table, entrypoint or registry seal?
//
//   * REVIEW ROUTING. (a) PARTLY — `review-engineering-slice`
//     (engineering-runtime.js recordEngineeringReview) is the door that records
//     a review, and it already enforces two of this slice's clauses honestly:
//     the reviewer actor may not be the receipt's executor actor, and the
//     reviewer session may not be the maker's session. (b) NO, for routing.
//     That door records ONE reviewer fact per receipt. It has no dimension, no
//     assignment, no per-dimension identity and no notion of a delivered SET —
//     the complete-set idea this slice exists for is absent from the schema, so
//     there is nothing authoritative to ask "is every dimension covered by
//     someone entitled to cover it". (c) NO: per-dimension assignments need
//     columns or a table, and migrations are a serialized surface this wave
//     does not own. MISSING SEAM:
//     `seam:independent-reviewer-identity-registry` — the authoritative
//     statement of which identities may review, in which dimension, on which
//     change.
//
//   * COMPLETE FINDING SET. (a) NO. The nearest relative is
//     `receipt.receipt.deviations`, and it is the exact thing this slice must
//     not treat as authority: the deviation list is written BY THE MAKER, in
//     the maker's own receipt, and recordEngineeringReview then checks the
//     reviewer covered every entry in it. That proves the reviewer read the
//     maker's list. It cannot prove the list was complete, because the party
//     being reviewed wrote it. (b) NO — there is no store anywhere in this tree
//     that holds a finding independently of the receipt that admits it. (c) NO.
//     MISSING SEAM: `seam:complete-finding-set-registry`.
//
//   * REVIEW ROUND BOUND. (a) NO. There is no round door. `ops.engineering_
//     reviewer_fact` rows are append-only per receipt and could be COUNTED, but
//     a count of reviewer facts is a count of REVIEWERS, not of rounds: a
//     second independent reviewer of one unchanged receipt is indistinguishable
//     from a second round after a repair, and Q042.D1's bound is on rounds.
//     Nothing records a repair batch, a regression run or a round ordinal.
//     MISSING SEAM: `seam:review-round-ledger`.
//
//   * BOUNDED ADJUDICATION. (a) NO. Two adjudication verbs exist —
//     `adjudicate-incident` and `adjudicate-investigation-branch` — and neither
//     is this: they settle an incident's disposition and an investigation
//     branch's, over incident and investigation records, with no slice, no
//     review round, no reviewer and no delivered set anywhere in their subject.
//     Reusing one would be a second authority over a different thing wearing
//     this slice's name. (b) NO. (c) NO. MISSING SEAM:
//     `seam:bounded-adjudication-receipt-store`.
//
// SO WHAT IS ACTUALLY PROVED HERE. The deterministic clauses, as pure
// predicates over the shapes a caller describes — which is the honest thing a
// decision layer can prove before its ledgers exist, and it is not nothing:
// every clause below runs on every call and the FIRST one that blocks is the
// answer, so a caller whose own description is self-inconsistent is told which
// clause caught it rather than being handed a missing-seam refusal that hides a
// real defect. The seam clause is last in every check order for that reason.
//
// AND THE HOLE THAT IS DELIBERATELY NOT LEFT OPEN. No evaluator here takes a
// store, holder, registry, ledger or receipt-resolver as an argument, and none
// can be given one: passing a second argument is a contract violation, not a
// port. There is no "unwired", "fixture", "predicate" or test-only variant that
// returns the privileged answer with a wired:false tag, because a label is not
// access control. No exported function in this file can return "pass",
// "allow", "covered", "complete" or "independent" as a decision from any
// caller-controlled input, under any name; the test suite sweeps the whole
// export surface against every input shape to say so. The clause logic lives in
// complete-set-review-a03.internal.v5.js, which the public surface does not
// re-export and which answers only in the conditional
// (`would_route_if_authoritative`).
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — `decision` is "refuse" with a stable
//     `reason_id`. An observation that does not state a fact BLOCKS; silence is
//     never an allow.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, open
//     schemas, unknown enum members and malformed digests are not policy
//     questions — the module cannot read the request, so it fails closed.
//
// THE FIVE SETTLED DECISIONS are carried in V5_A03_SETTLED_DECISIONS with their
// source-evidence digests, read from doctrine document
// `doctorcre-v5-design-basis`, normalized r7 design chunks, reassembled and
// verified against the manifest's own artifact digest
// ef34aa54740dd56508b7cebf05a2a95851aacedbbe4f2e4865a39ffede28f0ad. They are
// identity, not configuration: a caller holding a different subset proves the
// disagreement through assertA03DecisionBinding rather than discovering it
// later.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_A03_POLICY_VERSION,
  V5_A03_SCHEMA_VERSION,
  V5_A03_SEAMS,
  V5_ADJUDICATION_CHECKS,
  V5_ADJUDICATION_OUTCOMES,
  V5_ADJUDICATION_RECEIPT_KIND,
  V5_ADJUDICATION_RECEIPT_STORE_SEAM,
  V5_ADJUDICATOR_ROLE,
  V5_CHECK_STATES,
  V5_COMPLETE_FINDING_SET_REGISTRY_SEAM,
  V5_CONTEXT_BINDINGS,
  V5_DETERMINISTIC_ONLY_DECISIONS,
  V5_FINDING_SET_CHECKS,
  V5_MAX_REVIEW_ROUNDS,
  V5_MODEL_PERMITTED_ROLES,
  V5_NON_REVIEWER_ROLES,
  V5_OPPOSING_ROLE_PAIRS,
  V5_REVIEWER_IDENTITY_REGISTRY_SEAM,
  V5_REVIEW_DIMENSIONS,
  V5_REVIEW_ROLES,
  V5_REVIEW_ROUND_LEDGER_SEAM,
  V5_REVIEW_STATES,
  V5_ROUND_BOUND_CHECKS,
  V5_ROUND_REGRESSION_CLASSES,
  V5_ROUND_TRANSITIONS,
  V5_ROUTING_CHECKS,
  V5_SUBMISSION_STATES,
} from "./complete-set-review-a03.vocabulary.v5.js";
import {
  classifyAdjudicationIfAuthoritative,
  classifyFindingSetIfAuthoritative,
  classifyRoundIfAuthoritative,
  classifyRoutingIfAuthoritative,
  detectRoundRegressions,
  reviewRoundObligation,
} from "./complete-set-review-a03.internal.v5.js";

export {
  V5_A03_POLICY_VERSION,
  V5_A03_SCHEMA_VERSION,
  V5_A03_SEAMS,
  V5_ADJUDICATION_CHECKS,
  V5_ADJUDICATION_OUTCOMES,
  V5_ADJUDICATION_RECEIPT_KIND,
  V5_ADJUDICATION_RECEIPT_STORE_SEAM,
  V5_ADJUDICATOR_ROLE,
  V5_CHECK_STATES,
  V5_COMPLETE_FINDING_SET_REGISTRY_SEAM,
  V5_CONTEXT_BINDINGS,
  V5_DETERMINISTIC_ONLY_DECISIONS,
  V5_FINDING_SET_CHECKS,
  V5_MAX_REVIEW_ROUNDS,
  V5_MODEL_PERMITTED_ROLES,
  V5_NON_REVIEWER_ROLES,
  V5_OPPOSING_ROLE_PAIRS,
  V5_REVIEWER_IDENTITY_REGISTRY_SEAM,
  V5_REVIEW_DIMENSIONS,
  V5_REVIEW_ROLES,
  V5_REVIEW_ROUND_LEDGER_SEAM,
  V5_REVIEW_STATES,
  V5_ROUND_BOUND_CHECKS,
  V5_ROUND_REGRESSION_CLASSES,
  V5_ROUND_TRANSITIONS,
  V5_ROUTING_CHECKS,
  V5_SUBMISSION_STATES,
};

const SHA256_HEX = /^[0-9a-f]{64}$/;
const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
// A receipt is cited by the digest of its own bytes, in the exact form
// artifact-trust.js emits ("sha256:" + 64 hex), so the citation and the
// computed digest are comparable without either side reformatting the other.
const RECEIPT_REF_PREFIX = "adjudication-receipt:";
const RECEIPT_REF = new RegExp(`^${RECEIPT_REF_PREFIX}sha256:[0-9a-f]{64}$`);

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

/** An open schema is an unenforced one: every request object is closed. */
function closed(value, allowed, path) {
  object(value, path);
  for (const key of Object.keys(value))
    if (!allowed.includes(key))
      fail("unknown_field", `${path}.${key} is not a field this module reads`,
        { path, key, allowed: [...allowed].sort() });
}

function exact(value, allowed, path) {
  closed(value, allowed, path);
  for (const key of allowed)
    if (!Object.hasOwn(value, key)) fail("missing_field", `${path}.${key} is required`, { path, key });
  return value;
}

function str(value, path) {
  if (typeof value !== "string" || value.length === 0)
    fail("not_a_string", `${path} must be a non-empty string`, { path });
  return value;
}

function ref(value, path) {
  str(value, path);
  if (!REF.test(value)) fail("malformed_ref", `${path} must be a typed reference`, { path, value });
  return value;
}

function sha256Hex(value, path) {
  if (typeof value !== "string" || !SHA256_HEX.test(value))
    fail("malformed_digest", `${path} must be a 64-character lower-case sha256 digest`, { path });
  return value;
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

function ordinal(value, path) {
  if (!Number.isInteger(value) || value < 1)
    fail("not_a_positive_integer", `${path} must be an integer of at least 1`, { path, value: value ?? null });
  return value;
}

function count(value, path) {
  if (!Number.isInteger(value) || value < 0)
    fail("not_a_count", `${path} must be an integer of at least 0`, { path, value: value ?? null });
  return value;
}

/**
 * A C-sorted, duplicate-free list of exact strings.
 *
 * Order and uniqueness are REQUEST SHAPE, not policy, for the same reason F02
 * gives about a path lease: two callers who disagree about the order of a
 * finding set are describing two different finding sets, and silently sorting
 * one would hand back an identity its producer never wrote.
 */
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
// The five settled decisions, carried verbatim with their source-evidence
// digests. Text is identity here, not configuration.
// ---------------------------------------------------------------------------

export const V5_A03_SETTLED_DECISIONS = deepFreeze({
  "Q028.D1": {
    settled_requirement: "CI never certifies its own specification; every consequential slice receives independent fresh-context review, automatic remediation, and unresolved-material-disagreement escalation only.",
    source_evidence_digest: "1a6147884a3ecb971522fd46f2e71736f8556ed05597afae6d33a21a85464ba4",
  },
  "Q042.D1": {
    settled_requirement: "Each review round discovers the complete finding set before batch repair; allow at most two full rounds, then stronger adjudication and pass, fail, or quarantine without endless spirals.",
    source_evidence_digest: "1379eda95029150e86ccd2e10aaaab17ade2362a3fe1a13d027e4856e3c3726f",
  },
  "Q107.D1": {
    settled_requirement: "Separate architect, builder, reviewer, integration, deployment, and program-control duties for the same change; no role may certify or weaken its own work.",
    source_evidence_digest: "7e07cbb8d8ba1cb5ee8408e0d0ad26c7504df2024e4e9d6a2d1ddbbe305cad5b",
  },
  "Q113.D1": {
    settled_requirement: "Test deterministic behavior, adapters, database, state machines, failures, browsers, staging, and production outcomes through deep-module interfaces; discover all findings, batch repair, fully regress, and independently review outcomes.",
    source_evidence_digest: "90f42c3074aa7486429c9e8c5a7b8ccb08ba5cfd2a975c124a1c139586f2542f",
  },
  "Q154.D1": {
    settled_requirement: "Run separate exhaustive architecture, sequencing, repository, migration, context, security, business, product, cost, resilience, and operations reviews; collect complete findings, batch repair, and conduct one fresh full-plan review.",
    source_evidence_digest: "d053c86ef6487dcc94e53e25e6e00d120bb03661d813587542de5c81cc9efc8a",
  },
});

export const V5_A03_DECISION_IDS = deepFreeze(Object.keys(V5_A03_SETTLED_DECISIONS).sort());

/**
 * Refuse a caller whose decision subset has drifted from the reviewed five.
 *
 * Drift is checked in both directions and every source-evidence digest must
 * match exactly, so a caller that believes it holds these decisions and holds
 * different text finds out here rather than after acting on them.
 */
export function assertA03DecisionBinding(binding) {
  exact(binding, ["decisions"], "binding");
  object(binding.decisions, "binding.decisions");
  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_A03_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_A03_DECISION_IDS.includes(id));
  if (missing.length || extra.length)
    fail("decision_binding_drift", "the supplied decision set is not the reviewed five", { missing, extra });
  for (const id of V5_A03_DECISION_IDS) {
    const entry = exact(binding.decisions[id], ["settled_requirement", "source_evidence_digest"],
      `binding.decisions.${id}`);
    const expected = V5_A03_SETTLED_DECISIONS[id];
    if (entry.settled_requirement !== expected.settled_requirement ||
        entry.source_evidence_digest !== expected.source_evidence_digest)
      fail("decision_binding_drift", `binding.decisions.${id} is not the reviewed decision`, { decision_id: id });
  }
  return true;
}

// ---------------------------------------------------------------------------
// The reason vocabulary. Closed, C-sorted and fully exercised by the suite: a
// reason nothing can produce is a reason nobody can act on.
// ---------------------------------------------------------------------------

export const V5_A03_REASON_IDS = deepFreeze([
  "adjudication_before_round_limit",
  "adjudication_disputed_set_empty",
  "adjudication_receipt_adjudicator_is_a_party",
  "adjudication_receipt_bound_to_other_change",
  "adjudication_receipt_bound_to_other_delivered_set",
  "adjudication_receipt_digest_mismatch",
  "adjudication_receipt_kind_mismatch",
  "adjudication_receipt_not_content_addressed",
  "adjudication_receipt_outcome_unknown",
  "adjudication_receipt_unresolvable",
  "adjudicator_is_a_party_to_the_dispute",
  "adjudicator_role_unknown",
  "bounded_adjudication_receipt_store_unavailable",
  "complete_finding_set_registry_unavailable",
  "duties_not_role_separated",
  "finding_set_dimension_absent",
  "finding_set_enumerated_after_repair",
  "prior_round_batch_regression_absent",
  "review_context_not_fresh",
  "review_dimension_coverage_incomplete",
  "review_round_drift_detected",
  "review_round_ledger_unavailable",
  "review_round_limit_exhausted",
  "review_round_reopened_after_adjudication",
  "review_scope_narrower_than_delivered_set",
  "reviewer_identity_registry_unavailable",
  "reviewer_not_role_separated",
]);

function reason(id) {
  if (!V5_A03_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a reason this module may give`, { reason_id: id });
  return id;
}

/** Which reason each clause gives when it blocks. */
const CLAUSE_REASONS = deepFreeze({
  dimension_coverage: "review_dimension_coverage_incomplete",
  reviewer_role_separation: "reviewer_not_role_separated",
  duty_role_separation: "duties_not_role_separated",
  fresh_context: "review_context_not_fresh",
  reviewer_identity_registry: "reviewer_identity_registry_unavailable",
  dimension_submission: "finding_set_dimension_absent",
  complete_set_scope: "review_scope_narrower_than_delivered_set",
  enumeration_before_repair: "finding_set_enumerated_after_repair",
  complete_finding_set_registry: "complete_finding_set_registry_unavailable",
  round_reopened_after_adjudication: "review_round_reopened_after_adjudication",
  round_limit: "review_round_limit_exhausted",
  prior_round_batch_regression: "prior_round_batch_regression_absent",
  round_drift: "review_round_drift_detected",
  review_round_ledger: "review_round_ledger_unavailable",
  adjudicator_role: "adjudicator_role_unknown",
  adjudicator_separation: "adjudicator_is_a_party_to_the_dispute",
  rounds_before_adjudication: "adjudication_before_round_limit",
  disputed_set_empty: "adjudication_disputed_set_empty",
  bounded_adjudication_receipt_store: "bounded_adjudication_receipt_store_unavailable",
});

// ---------------------------------------------------------------------------
// THE AUTHORITATIVE HOLDERS, NAMED BY THIS MODULE.
//
// Each is a NAME, not a port: nothing a caller passes can become one, because
// no evaluator accepts a holder as an argument and this module exports no way
// to bind one. The bindings are `null` because the seams do not exist — see the
// census in the header for the search that establishes each. When a seam is
// built, the constant below is the single place it binds.
// ---------------------------------------------------------------------------

const V5_REVIEWER_IDENTITY_REGISTRY = null;
const V5_COMPLETE_FINDING_SET_REGISTRY = null;
const V5_REVIEW_ROUND_LEDGER = null;
const V5_ADJUDICATION_RECEIPT_STORE = null;

/** A holder counts as bound only if it can actually resolve. Takes no input. */
function bound(holder, methodName) {
  return isPlainObject(holder) && typeof holder[methodName] === "function";
}

function satisfied(check, note) {
  return deepFreeze({ check, state: "satisfied", reason_id: null, note, detail: {} });
}

function refused(check, note, detail) {
  return deepFreeze({
    check, state: "refused", reason_id: reason(CLAUSE_REASONS[check]), note, detail: detail ?? {},
  });
}

/**
 * Run a check order and stop at the first refusal.
 *
 * Later clauses report "not_reached" rather than a guess, because a clause that
 * never ran has not been satisfied and saying so is the whole difference
 * between an unfinished check and a passing one.
 */
function runChecks(order, evaluators) {
  const states = {};
  const satisfiedChecks = [];
  const notReached = [];
  let blocking = null;
  for (const check of order) {
    if (blocking !== null) {
      states[check] = deepFreeze({ check, state: "not_reached", reason_id: null, note: null, detail: {} });
      notReached.push(check);
      continue;
    }
    const outcome = evaluators[check]();
    states[check] = outcome;
    if (outcome.state === "refused") blocking = check;
    else satisfiedChecks.push(check);
  }
  return { states: deepFreeze(states), satisfiedChecks, notReached, blocking };
}

/**
 * Turn one internal classification into this order's clause outcomes.
 *
 * The classifier names at most one blocking clause. Every clause BEFORE it in
 * the order is satisfied (the classifier reached past them), the blocking one
 * is refused, and everything after is not reached — which is exactly what
 * runChecks produces when the deterministic clauses are replayed in order.
 */
function clauseEvaluators(order, classification, notes) {
  const evaluators = {};
  for (const check of order) {
    evaluators[check] = () => (classification.blocking_clause === check
      ? refused(check, notes[check], classification.detail)
      : satisfied(check, notes[check]));
  }
  return evaluators;
}

const ROUTING_NOTES = deepFreeze({
  dimension_coverage: "every accepted review dimension is assigned exactly once",
  reviewer_role_separation: "no dimension reviewer also holds a role that opposes reviewing",
  duty_role_separation: "no identity holds two opposing duties for this change",
  fresh_context: "every reviewing session is fresh and is not the making session",
  reviewer_identity_registry: "the authoritative reviewer identity registry does not exist",
});

const FINDING_SET_NOTES = deepFreeze({
  dimension_submission: "every accepted review dimension submitted a finding set",
  complete_set_scope: "every reviewer read the whole delivered set, not a slice of it",
  enumeration_before_repair: "every finding set was enumerated before any repair began",
  complete_finding_set_registry: "the authoritative complete-finding-set registry does not exist",
});

const ROUND_NOTES = deepFreeze({
  round_reopened_after_adjudication: "no round is requested after adjudication closed the dispute",
  round_limit: "the requested round is within the accepted bound",
  prior_round_batch_regression: "every prior round is present with its batch repair and regression",
  round_drift: "none of the four accepted drift detectors fired",
  review_round_ledger: "the authoritative review round ledger does not exist",
});

const ADJUDICATION_NOTES = deepFreeze({
  adjudicator_role: "the adjudicator holds the accepted stronger-adjudicator role",
  adjudicator_separation: "the adjudicator is nobody in the dispute",
  rounds_before_adjudication: "the review bound is spent, so adjudication is the next step",
  disputed_set_empty: "there is a named disagreement to adjudicate",
  bounded_adjudication_receipt_store: "the authoritative adjudication receipt store does not exist",
});

/** Fields every evaluator's answer carries, so a record is self-describing. */
function envelope(answer, request) {
  return {
    schema_version: V5_A03_SCHEMA_VERSION,
    policy_version: V5_A03_POLICY_VERSION,
    answer,
    change_ref: request.change_ref,
    delivered_set_digest: request.delivered_set_digest,
    decided_by: "deterministic_controller",
    model_judgment_admitted: false,
    state_holder_is_caller_supplied: false,
    performs_routing: false,
    performs_repair: false,
    performs_adjudication: false,
    effects: V5_NO_EFFECTS,
  };
}

// ---------------------------------------------------------------------------
// 1. Review routing — Q154.D1 and Q107.D1.
// ---------------------------------------------------------------------------

function normalizeRoutingRequest(request) {
  exact(request, ["assignments", "change_ref", "delivered_set_digest", "maker_session_ref",
    "role_identities"], "request");
  ref(request.change_ref, "request.change_ref");
  sha256Hex(request.delivered_set_digest, "request.delivered_set_digest");
  ref(request.maker_session_ref, "request.maker_session_ref");
  const roles = exact(request.role_identities, [...V5_NON_REVIEWER_ROLES], "request.role_identities");
  for (const role of V5_NON_REVIEWER_ROLES) ref(roles[role], `request.role_identities.${role}`);
  list(request.assignments, "request.assignments");
  request.assignments.forEach((assignment, index) => {
    const path = `request.assignments[${index}]`;
    exact(assignment, ["context_binding", "dimension", "reviewer_identity_ref", "reviewer_session_ref"], path);
    member(assignment.dimension, V5_REVIEW_DIMENSIONS, `${path}.dimension`);
    ref(assignment.reviewer_identity_ref, `${path}.reviewer_identity_ref`);
    ref(assignment.reviewer_session_ref, `${path}.reviewer_session_ref`);
    member(assignment.context_binding, V5_CONTEXT_BINDINGS, `${path}.context_binding`);
  });
  return request;
}

/**
 * May this review be routed across the delivered set?
 *
 * NO — on every input, because the authoritative reviewer identity registry
 * does not exist. The four deterministic clauses still run first and still
 * decide the answer's `blocking_check`, so a routing whose own description
 * fails role separation is told THAT, not the missing seam.
 *
 * THERE IS NO REGISTRY ARGUMENT. A second argument is a contract violation, not
 * a port: a caller offering its own registry is a caller entitling its own
 * reviewers.
 */
export function evaluateReviewRouting(request) {
  // eslint-disable-next-line prefer-rest-params -- the arity IS the boundary.
  if (arguments.length > 1)
    fail("reviewer_identity_registry_is_not_an_argument",
      "evaluateReviewRouting takes one request; the reviewer identity registry is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_REVIEWER_IDENTITY_REGISTRY_SEAM });
  normalizeRoutingRequest(request);
  const classification = classifyRoutingIfAuthoritative(request);
  const registryBound = bound(V5_REVIEWER_IDENTITY_REGISTRY, "resolveReviewerEntitlement");
  const evaluators = clauseEvaluators(V5_ROUTING_CHECKS, classification, ROUTING_NOTES);
  evaluators.reviewer_identity_registry = () => (registryBound
    ? satisfied("reviewer_identity_registry", ROUTING_NOTES.reviewer_identity_registry)
    : refused("reviewer_identity_registry", ROUTING_NOTES.reviewer_identity_registry,
      { required_seam: V5_REVIEWER_IDENTITY_REGISTRY_SEAM }));
  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_ROUTING_CHECKS, evaluators);

  return deepFreeze({
    ...envelope("review_routing", request),
    decision: "refuse",
    reason_id: states[blocking].reason_id,
    dimensions_required: [...V5_REVIEW_DIMENSIONS],
    dimensions_assigned: request.assignments.map(assignment => assignment.dimension).sort(),
    reviewer_identity_registry_seam: V5_REVIEWER_IDENTITY_REGISTRY_SEAM,
    registry_bound: registryBound,
    // A routing this module cannot entitle is a routing nobody may act on. The
    // field says so in the record rather than leaving it to be inferred from
    // the reason id.
    reviewers_entitled: false,
    checks_required: [...V5_ROUTING_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
  });
}

// ---------------------------------------------------------------------------
// 2. Complete finding set — Q113.D1 and Q154.D1.
// ---------------------------------------------------------------------------

function normalizeFindingSetRequest(request) {
  exact(request, ["change_ref", "delivered_set_digest", "round_ordinal", "submissions"], "request");
  ref(request.change_ref, "request.change_ref");
  sha256Hex(request.delivered_set_digest, "request.delivered_set_digest");
  ordinal(request.round_ordinal, "request.round_ordinal");
  list(request.submissions, "request.submissions");
  request.submissions.forEach((submission, index) => {
    const path = `request.submissions[${index}]`;
    exact(submission, ["dimension", "enumerated_before_repair", "finding_refs", "reviewed_set_digest",
      "state"], path);
    member(submission.dimension, V5_REVIEW_DIMENSIONS, `${path}.dimension`);
    member(submission.state, V5_SUBMISSION_STATES, `${path}.state`);
    sha256Hex(submission.reviewed_set_digest, `${path}.reviewed_set_digest`);
    bool(submission.enumerated_before_repair, `${path}.enumerated_before_repair`);
    sortedUnique(submission.finding_refs, `${path}.finding_refs`, ref);
  });
  // Missing and duplicated dimensions are a POLICY answer, not a shape error:
  // the dimension_submission clause reports exactly which, the same way routing
  // reports coverage, so a caller learns what to send rather than that it was
  // malformed.
  return request;
}

/**
 * Is this round's finding set the complete one?
 *
 * NO — on every input. A finding set is complete relative to a registry that
 * holds findings independently of the receipt admitting them, and this
 * repository has none: the nearest relative is the maker's own deviation list,
 * which is the party being reviewed describing what is wrong with its own work.
 *
 * The scope clause is the one worth reading twice. A reviewer who read a subset
 * produces a report that LOOKS complete and is not, and no count of findings
 * reveals it — so each submission states the digest of the set it actually read
 * and a narrower one is caught by arithmetic rather than by suspicion.
 */
export function evaluateFindingSetCompleteness(request) {
  // eslint-disable-next-line prefer-rest-params -- the arity IS the boundary.
  if (arguments.length > 1)
    fail("complete_finding_set_registry_is_not_an_argument",
      "evaluateFindingSetCompleteness takes one request; the finding registry is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_COMPLETE_FINDING_SET_REGISTRY_SEAM });
  normalizeFindingSetRequest(request);
  const classification = classifyFindingSetIfAuthoritative(request);
  const registryBound = bound(V5_COMPLETE_FINDING_SET_REGISTRY, "resolveFindingSet");
  const evaluators = clauseEvaluators(V5_FINDING_SET_CHECKS, classification, FINDING_SET_NOTES);
  evaluators.complete_finding_set_registry = () => (registryBound
    ? satisfied("complete_finding_set_registry", FINDING_SET_NOTES.complete_finding_set_registry)
    : refused("complete_finding_set_registry", FINDING_SET_NOTES.complete_finding_set_registry,
      { required_seam: V5_COMPLETE_FINDING_SET_REGISTRY_SEAM }));
  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_FINDING_SET_CHECKS, evaluators);

  return deepFreeze({
    ...envelope("finding_set_completeness", request),
    decision: "refuse",
    reason_id: states[blocking].reason_id,
    round_ordinal: request.round_ordinal,
    dimensions_required: [...V5_REVIEW_DIMENSIONS],
    dimensions_submitted: request.submissions
      .filter(submission => submission.state === "submitted")
      .map(submission => submission.dimension).sort(),
    finding_registry_seam: V5_COMPLETE_FINDING_SET_REGISTRY_SEAM,
    registry_bound: registryBound,
    // Never true. Batch repair is what a complete finding set unlocks, and this
    // module cannot establish one, so it never says the batch may start.
    batch_repair_admitted: false,
    checks_required: [...V5_FINDING_SET_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
  });
}

// ---------------------------------------------------------------------------
// 3. The round bound — Q042.D1.
// ---------------------------------------------------------------------------

function normalizeRoundRequest(request) {
  exact(request, ["adjudication_recorded", "change_ref", "delivered_set_digest", "history",
    "requested_round_ordinal"], "request");
  ref(request.change_ref, "request.change_ref");
  sha256Hex(request.delivered_set_digest, "request.delivered_set_digest");
  ordinal(request.requested_round_ordinal, "request.requested_round_ordinal");
  bool(request.adjudication_recorded, "request.adjudication_recorded");
  list(request.history, "request.history");
  request.history.forEach((entry, index) => {
    const path = `request.history[${index}]`;
    exact(entry, ["dimension", "finding_refs", "post_repair_artifact_digest", "regression",
      "resolved_finding_refs", "reviewer_identity_ref", "round_ordinal", "state"], path);
    ordinal(entry.round_ordinal, `${path}.round_ordinal`);
    member(entry.dimension, V5_REVIEW_DIMENSIONS, `${path}.dimension`);
    ref(entry.reviewer_identity_ref, `${path}.reviewer_identity_ref`);
    member(entry.state, V5_REVIEW_STATES, `${path}.state`);
    sha256Hex(entry.post_repair_artifact_digest, `${path}.post_repair_artifact_digest`);
    sortedUnique(entry.finding_refs, `${path}.finding_refs`, ref);
    sortedUnique(entry.resolved_finding_refs, `${path}.resolved_finding_refs`, ref);
    const regression = exact(entry.regression, ["checks_executed", "suite_ref"], `${path}.regression`);
    ref(regression.suite_ref, `${path}.regression.suite_ref`);
    sortedUnique(regression.checks_executed, `${path}.regression.checks_executed`, str);
  });
  return request;
}

/**
 * May another review round begin?
 *
 * NO — on every input, because there is no round ledger and a count of reviewer
 * facts is a count of reviewers. The bound itself is still real and still
 * decides the answer: a third round is refused as `review_round_limit_
 * exhausted` naming the transition it owes, which is the checkable_done clause
 * "third loop cannot silently continue".
 *
 * SILENTLY IS THE OPERATIVE WORD. This function cannot stop a third round —
 * nothing calls it yet — but it cannot be made to bless one either, and a
 * consumer that asks is given the refusal and the required transition rather
 * than nothing.
 */
export function evaluateReviewRoundAdmission(request) {
  // eslint-disable-next-line prefer-rest-params -- the arity IS the boundary.
  if (arguments.length > 1)
    fail("review_round_ledger_is_not_an_argument",
      "evaluateReviewRoundAdmission takes one request; the round ledger is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_REVIEW_ROUND_LEDGER_SEAM });
  normalizeRoundRequest(request);
  const classification = classifyRoundIfAuthoritative(request);
  const ledgerBound = bound(V5_REVIEW_ROUND_LEDGER, "resolveRoundHistory");
  const evaluators = clauseEvaluators(V5_ROUND_BOUND_CHECKS, classification, ROUND_NOTES);
  evaluators.review_round_ledger = () => (ledgerBound
    ? satisfied("review_round_ledger", ROUND_NOTES.review_round_ledger)
    : refused("review_round_ledger", ROUND_NOTES.review_round_ledger,
      { required_seam: V5_REVIEW_ROUND_LEDGER_SEAM }));
  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_ROUND_BOUND_CHECKS, evaluators);
  const obligation = reviewRoundObligation(request.requested_round_ordinal);

  return deepFreeze({
    ...envelope("review_round_admission", request),
    decision: "refuse",
    reason_id: states[blocking].reason_id,
    requested_round_ordinal: request.requested_round_ordinal,
    round_limit: V5_MAX_REVIEW_ROUNDS,
    within_round_limit: obligation.within_round_limit,
    required_transition: obligation.required_transition,
    drift_detectors: [...V5_ROUND_REGRESSION_CLASSES],
    drift_detected: detectRoundRegressions(request.history),
    review_round_ledger_seam: V5_REVIEW_ROUND_LEDGER_SEAM,
    ledger_bound: ledgerBound,
    round_admitted: false,
    checks_required: [...V5_ROUND_BOUND_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
  });
}

// ---------------------------------------------------------------------------
// 4. Bounded adjudication — Q042.D1 step 4 and Q028.D1.
// ---------------------------------------------------------------------------

function normalizeAdjudicationRequest(request) {
  exact(request, ["adjudicator_identity_ref", "adjudicator_role", "change_ref", "delivered_set_digest",
    "disputed_finding_refs", "maker_identity_ref", "releaser_identity_ref", "reviewer_identity_refs",
    "rounds_completed"], "request");
  ref(request.change_ref, "request.change_ref");
  sha256Hex(request.delivered_set_digest, "request.delivered_set_digest");
  ref(request.adjudicator_identity_ref, "request.adjudicator_identity_ref");
  str(request.adjudicator_role, "request.adjudicator_role");
  ref(request.maker_identity_ref, "request.maker_identity_ref");
  ref(request.releaser_identity_ref, "request.releaser_identity_ref");
  sortedUnique(request.reviewer_identity_refs, "request.reviewer_identity_refs", ref);
  sortedUnique(request.disputed_finding_refs, "request.disputed_finding_refs", ref);
  count(request.rounds_completed, "request.rounds_completed");
  return request;
}

/**
 * May this dispute be adjudicated, and how did it end?
 *
 * MAY IT: no, on every input. HOW DID IT END: this module never says. The
 * adjudicated outcome comes from the receipt store and from nowhere else, so
 * `adjudicated_outcome` is `null` on every call and no argument, field or
 * receipt a caller can construct will change it. That is the one thing this
 * slice most has to get right: an outcome a party can state about its own
 * dispute is not an adjudication, it is a self-certification with a judge's
 * letterhead.
 */
export function evaluateAdjudication(request) {
  // eslint-disable-next-line prefer-rest-params -- the arity IS the boundary.
  if (arguments.length > 1)
    fail("adjudication_receipt_store_is_not_an_argument",
      "evaluateAdjudication takes one request; the adjudication receipt store is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_ADJUDICATION_RECEIPT_STORE_SEAM });
  normalizeAdjudicationRequest(request);
  const classification = classifyAdjudicationIfAuthoritative(request);
  const storeBound = bound(V5_ADJUDICATION_RECEIPT_STORE, "resolveReceipt");
  const evaluators = clauseEvaluators(V5_ADJUDICATION_CHECKS, classification, ADJUDICATION_NOTES);
  evaluators.bounded_adjudication_receipt_store = () => (storeBound
    ? satisfied("bounded_adjudication_receipt_store", ADJUDICATION_NOTES.bounded_adjudication_receipt_store)
    : refused("bounded_adjudication_receipt_store", ADJUDICATION_NOTES.bounded_adjudication_receipt_store,
      { required_seam: V5_ADJUDICATION_RECEIPT_STORE_SEAM }));
  const { states, satisfiedChecks, notReached, blocking } = runChecks(V5_ADJUDICATION_CHECKS, evaluators);

  return deepFreeze({
    ...envelope("bounded_adjudication", request),
    decision: "refuse",
    reason_id: states[blocking].reason_id,
    adjudicator_role_required: V5_ADJUDICATOR_ROLE,
    rounds_completed: request.rounds_completed,
    round_limit: V5_MAX_REVIEW_ROUNDS,
    disputed_finding_count: request.disputed_finding_refs.length,
    adjudication_receipt_store_seam: V5_ADJUDICATION_RECEIPT_STORE_SEAM,
    store_bound: storeBound,
    // Null on every input, forever, until the store exists. The vocabulary of
    // outcomes is exported as V5_ADJUDICATION_OUTCOMES so a consumer knows what
    // the store may say; this module never says one of them.
    adjudicated_outcome: null,
    outcome_is_caller_stated: false,
    disposition_recorded: false,
    checks_required: [...V5_ADJUDICATION_CHECKS],
    checks_satisfied: satisfiedChecks,
    checks_not_reached: notReached,
    blocking_check: blocking,
    check_states: states,
  });
}

// ---------------------------------------------------------------------------
// The pure receipt verifier.
// ---------------------------------------------------------------------------

/**
 * Verify one adjudication receipt BODY against the reference that cited it, in
 * the order a forger has to survive: the reference is content-addressed,
 * something came back, the bytes hash to the reference, it is the kind that was
 * asked for, it binds this change and this delivered set, its outcome is in the
 * closed vocabulary, and its adjudicator is nobody in the dispute.
 *
 * PURE, and deliberately shape-only. It takes the body as an argument rather
 * than a store to fetch it from, so it can be proved against fixture receipts
 * without any caller getting to supply the ledger.
 *
 * IT RETURNS NO OUTCOME, AND THAT IS THE POINT. `{ ok: true }` says the bytes
 * are a well-formed receipt of this dispute; it does NOT say the dispute was
 * decided that way, and the verified outcome is deliberately not echoed back,
 * so no consumer can launder a self-made receipt into an adjudicated result by
 * reading it off this function's return value. The outcome is read from the
 * store, which does not exist.
 */
export function verifyAdjudicationReceipt(kind, receiptRef, body, binding) {
  member(kind, [V5_ADJUDICATION_RECEIPT_KIND], "kind");
  exact(binding, ["change_ref", "delivered_set_digest", "party_identity_refs"], "binding");
  ref(binding.change_ref, "binding.change_ref");
  sha256Hex(binding.delivered_set_digest, "binding.delivered_set_digest");
  sortedUnique(binding.party_identity_refs, "binding.party_identity_refs", ref);
  const detail = { receipt_kind: kind, receipt_ref: typeof receiptRef === "string" ? receiptRef : null };
  const no = (reasonId, note, extra) =>
    deepFreeze({ ok: false, reason_id: reason(reasonId), note, detail: { ...detail, ...(extra ?? {}) } });

  if (typeof receiptRef !== "string" || !RECEIPT_REF.test(receiptRef))
    return no("adjudication_receipt_not_content_addressed",
      "an adjudication receipt is cited by the digest of its own bytes, not by a name");
  if (!isPlainObject(body))
    return no("adjudication_receipt_unresolvable",
      "the authoritative store does not hold this receipt");
  const bodyDigest = digest(body);
  if (bodyDigest !== receiptRef.slice(RECEIPT_REF_PREFIX.length))
    return no("adjudication_receipt_digest_mismatch",
      "what came back is not the receipt that was cited", { resolved_digest: bodyDigest });
  if (body.kind !== kind)
    return no("adjudication_receipt_kind_mismatch",
      "a receipt of another kind does not answer this question");
  exact(body, ["adjudicator_identity_ref", "change_ref", "delivered_set_digest", "disputed_finding_refs",
    "kind", "outcome", "rounds_completed"], `receipt(${kind})`);
  ref(body.adjudicator_identity_ref, `receipt(${kind}).adjudicator_identity_ref`);
  ref(body.change_ref, `receipt(${kind}).change_ref`);
  sha256Hex(body.delivered_set_digest, `receipt(${kind}).delivered_set_digest`);
  sortedUnique(body.disputed_finding_refs, `receipt(${kind}).disputed_finding_refs`, ref);
  count(body.rounds_completed, `receipt(${kind}).rounds_completed`);
  if (body.change_ref !== binding.change_ref)
    return no("adjudication_receipt_bound_to_other_change",
      "a receipt bound to another change proves nothing about this one");
  if (body.delivered_set_digest !== binding.delivered_set_digest)
    return no("adjudication_receipt_bound_to_other_delivered_set",
      "a receipt bound to another delivered set proves nothing about this one");
  if (typeof body.outcome !== "string" || !V5_ADJUDICATION_OUTCOMES.includes(body.outcome))
    return no("adjudication_receipt_outcome_unknown",
      "a receipt whose outcome is outside the closed vocabulary is not a bounded adjudication");
  if (binding.party_identity_refs.includes(body.adjudicator_identity_ref))
    return no("adjudication_receipt_adjudicator_is_a_party",
      "a receipt signed by a party to the dispute is not an adjudication");
  return deepFreeze({ ok: true, reason_id: null, note: "the receipt is well-formed and binds this dispute", detail });
}

// ---------------------------------------------------------------------------
// Pure clause predicates, re-exported for consumers that need one clause rather
// than a decision. None of them authorizes anything: a gap report, a collision
// list, an obligation and a drift report are all statements about what the
// CALLER described, and the composed evaluators above still refuse.
// ---------------------------------------------------------------------------

export { reviewDimensionGap, roleSeparationCollisions } from "./complete-set-review-a03.internal.v5.js";
export { detectRoundRegressions, reviewRoundObligation };

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no change, review, round, identity or finding
// — so two callers describing the same policy reach the same digest. It is an
// identity for these bytes: not an acceptance, not a receipt, and not evidence
// for any consumer gate. Every closed vocabulary this module decides against is
// enumerated and EXPLICITLY sorted, so the digest is stable by construction
// rather than by the luck of a list that happens to be alphabetical today.
// ---------------------------------------------------------------------------

export function v5A03PolicyPreimage() {
  return {
    schema_version: V5_A03_SCHEMA_VERSION,
    policy_version: V5_A03_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_ids: [...V5_A03_DECISION_IDS].sort(),
    review_dimensions: [...V5_REVIEW_DIMENSIONS].sort(),
    review_roles: [...V5_REVIEW_ROLES].sort(),
    non_reviewer_roles: [...V5_NON_REVIEWER_ROLES].sort(),
    opposing_role_pairs: V5_OPPOSING_ROLE_PAIRS.map(pair => [...pair].sort())
      .sort((a, b) => a.join(",").localeCompare(b.join(","))),
    adjudicator_role: V5_ADJUDICATOR_ROLE,
    adjudication_outcomes: [...V5_ADJUDICATION_OUTCOMES].sort(),
    adjudication_receipt_kind: V5_ADJUDICATION_RECEIPT_KIND,
    max_review_rounds: V5_MAX_REVIEW_ROUNDS,
    round_transitions: [...V5_ROUND_TRANSITIONS].sort(),
    round_regression_classes: [...V5_ROUND_REGRESSION_CLASSES].sort(),
    review_states: [...V5_REVIEW_STATES].sort(),
    context_bindings: [...V5_CONTEXT_BINDINGS].sort(),
    submission_states: [...V5_SUBMISSION_STATES].sort(),
    model_permitted_roles: [...V5_MODEL_PERMITTED_ROLES].sort(),
    deterministic_only_decisions: [...V5_DETERMINISTIC_ONLY_DECISIONS].sort(),
    routing_checks: [...V5_ROUTING_CHECKS],
    finding_set_checks: [...V5_FINDING_SET_CHECKS],
    round_bound_checks: [...V5_ROUND_BOUND_CHECKS],
    adjudication_checks: [...V5_ADJUDICATION_CHECKS],
    check_states: [...V5_CHECK_STATES].sort(),
    reason_ids: [...V5_A03_REASON_IDS].sort(),
    seams: Object.fromEntries(Object.keys(V5_A03_SEAMS).sort().map(key => [key, V5_A03_SEAMS[key]])),
  };
}

export function v5A03PolicyDigest() {
  return digest(v5A03PolicyPreimage());
}

export function v5A03PolicyCanonicalBytes() {
  return canonicalJson(v5A03PolicyPreimage());
}
