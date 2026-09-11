// V5-A03 COMPLETE-SET REVIEW DETERMINISTIC CLASSIFIERS — THE TEST-ONLY ENTRY.
//
// READ THIS FIRST, BECAUSE THE FILE'S LOCATION IS THE CONTRACT. Nothing in this
// file is part of the V5-A03 public surface. `complete-set-review-a03.v5.js`
// does not import it, no module under mcp-server/src imports it, and
// complete-set-review-a03.v5.test.mjs proves that with a parser-backed import
// scan of the whole tree rather than a promise. The only importer is the test
// file.
//
// WHY THE PREVIOUS ARRANGEMENT WAS NOT PRIVATE. These functions used to live in
// `mcp-server/src/complete-set-review-a03.internal.v5.js`. An exported ESM
// binding in a production directory is reachable by anything that can spell its
// path, so "internal" in the filename was a label, and a label is not access
// control. Four of them were re-exported on the public surface outright. The
// V5-A03 review of PR 987 named both, and the fix is this file's location, not a
// better comment.
//
// WHY IT EXISTS AT ALL. The deterministic clauses V5-A03's checkable_done names
// are real and are worth proving clause by clause:
//
//   1. every one of the eleven review dimensions is covered exactly once, by
//      someone who holds no role opposing `reviewer`, in a session that is not
//      the maker's;
//   2. every dimension's finding set covers the WHOLE delivered set, by digest,
//      and was enumerated before any repair began;
//   3. the review bound is two rounds, a third is refused by name, and the four
//      drifts Q042.D1 lists are each detected;
//   4. an adjudicator is nobody in the dispute, holds the stronger-adjudicator
//      role, and arrives only after the bound is spent;
//   5. an adjudication receipt is content-addressed, hashes to its own citation,
//      is of the right kind, binds this change and this delivered set, carries
//      an outcome from the closed vocabulary, and is not signed by a party.
//
// WHAT THEY ARE NOT. There is NO reviewer identity registry, NO finding
// registry, NO round ledger and NO adjudication receipt store in this
// repository. A caller object is therefore not evidence and never becomes
// evidence, so these functions may not say `ok`, `pass`, `allow`, `satisfied`,
// `complete` or `covered` about anything. They answer in the conditional and in
// the conditional only:
//
//   would_be_routable_if_authoritative          the SHAPE the caller described
//   would_be_whole_set_scoped_if_authoritative  would satisfy the clause IF it
//   would_be_within_bound_if_authoritative      had come from an authoritative
//   would_be_adjudicable_if_authoritative       reader, which it did not and
//   would_be_verifiable_if_authoritative        today cannot have.
//
// Every result also carries `is_not_authority: true` and
// `evidence_source: "caller_supplied_shapes_not_authority"`, so a value that
// escaped into a consumer would still refuse to read as a clearance.
//
// TWO CLAUSES ARE NOT CLASSIFIABLE AT ALL, and say so rather than guessing:
//
//   prior_round_batch_regression  Q042.D1 wants the prior round's COMPLETE
//                                 finding set, its repair AS A BATCH, and a full
//                                 regression bound to that batch. The earlier
//                                 version accepted the mere presence of a
//                                 history entry carrying the prior ordinal, so a
//                                 single architecture entry with an empty check
//                                 list read as satisfied. None of the three
//                                 facts is readable without the round ledger, so
//                                 for any round past the first this clause
//                                 blocks as `prior_round_batch_regression_
//                                 unreadable`.
//
//   identity separation           Proved only over the identities the CALLER
//                                 names. `would_be_routable_if_authoritative`
//                                 means the described identities do not collide;
//                                 it never means they are the identities that
//                                 built, reviewed and released the change.
//
// Every function here is PURE — no filesystem, no network, no database, no
// clock, no environment — and holds no state between calls.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A CLASSIFICATION is RETURNED: `classification: "would_refuse"` with the
//     blocking clause and a stable reason_id from the public module's registry.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, open schemas,
//     unknown enum members, malformed digests and unsorted lists are not
//     classification questions: the shape cannot be read at all.

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError } from "../src/global-boundaries.v5.js";
import {
  V5_A03_DECISION_IDS,
  V5_A03_POLICY_VERSION,
  V5_A03_PUBLIC_REASON_IDS,
  V5_A03_REASON_IDS,
  V5_A03_SCHEMA_VERSION,
  V5_A03_SEAMS,
  V5_ADJUDICATION_CHECKS,
  V5_ADJUDICATION_RECEIPT_KIND,
  V5_ADJUDICATOR_ROLE,
  V5_CONTEXT_BINDINGS,
  V5_FINDING_SET_CHECKS,
  V5_MAX_REVIEW_ROUNDS,
  V5_NON_REVIEWER_ROLES,
  V5_OPPOSING_ROLE_PAIRS,
  V5_DETERMINISTIC_ONLY_DECISIONS,
  V5_MODEL_PERMITTED_ROLES,
  V5_REVIEW_DIMENSIONS,
  V5_REVIEW_ROLES,
  V5_REVIEW_STATES,
  V5_ROUND_BOUND_CHECKS,
  V5_ROUND_REGRESSION_CLASSES,
  V5_ROUND_TRANSITIONS,
  V5_ROUTING_CHECKS,
  V5_SUBMISSION_STATES,
} from "../src/complete-set-review-a03.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
// The outcome vocabulary is NOT re-exported by the public module — that is the
// second correction of PR 987 — so the test-only classifier reaches the opaque
// codes where they live. Production cannot follow it here: nothing under
// mcp-server/src imports this file, and the suite proves that with Node's own
// module parser.
import {
  V5_ADJUDICATION_OUTCOME_CODE_SET,
} from "../src/complete-set-review-a03.vocabulary.v5.js";

/** Said on every result, so an escaped value still reads as "not authority". */
export const V5_A03_CLASSIFIER_EVIDENCE_SOURCE = "caller_supplied_shapes_not_authority";

/**
 * The only classifications any function in this file may return.
 *
 * Every affirmative half is worded as a statement about a hypothetical. The
 * conditional is not decoration: it is the whole content of the answer.
 */
export const V5_A03_CLASSIFICATIONS = Object.freeze({
  refuse: "would_refuse",
  routing: "would_be_routable_if_authoritative",
  findingSet: "would_be_whole_set_scoped_if_authoritative",
  round: "would_be_within_bound_if_authoritative",
  adjudication: "would_be_adjudicable_if_authoritative",
  receipt: "would_be_verifiable_if_authoritative",
});

/** Which reason each clause gives when it blocks. */
export const V5_A03_CLAUSE_REASONS = Object.freeze({
  dimension_coverage: "review_dimension_coverage_short_of_closed_set",
  reviewer_role_separation: "reviewer_not_role_separated",
  duty_role_separation: "duties_not_role_separated",
  fresh_context: "review_context_not_fresh",
  dimension_submission: "finding_set_dimension_absent",
  delivered_set_scope: "review_scope_narrower_than_delivered_set",
  enumeration_before_repair: "finding_set_enumerated_after_repair",
  round_reopened_after_adjudication: "review_round_reopened_after_adjudication",
  round_limit: "review_round_limit_exhausted",
  round_drift: "review_round_drift_detected",
  prior_round_batch_regression: "prior_round_batch_regression_unreadable",
  adjudicator_role: "adjudicator_role_unknown",
  adjudicator_separation: "adjudicator_is_a_party_to_the_dispute",
  rounds_before_adjudication: "adjudication_before_round_limit",
  disputed_set_empty: "adjudication_disputed_set_empty",
  receipt_content_addressed: "adjudication_receipt_not_content_addressed",
  receipt_resolvable: "adjudication_receipt_unresolvable",
  receipt_digest: "adjudication_receipt_digest_mismatch",
  receipt_kind: "adjudication_receipt_kind_mismatch",
  receipt_change_binding: "adjudication_receipt_bound_to_other_change",
  receipt_delivered_set_binding: "adjudication_receipt_bound_to_other_delivered_set",
  receipt_outcome_vocabulary: "adjudication_receipt_outcome_unknown",
  receipt_adjudicator_separation: "adjudication_receipt_adjudicator_is_a_party",
});

/** The clause orders a classifier runs, minus the seam clause it cannot reach. */
export const V5_A03_CLASSIFIER_CLAUSE_ORDERS = Object.freeze({
  routing: Object.freeze(V5_ROUTING_CHECKS.filter(check => check !== "reviewer_identity_registry")),
  finding_set: Object.freeze(
    V5_FINDING_SET_CHECKS.filter(check => check !== "exhaustive_finding_set_registry")),
  round: Object.freeze(V5_ROUND_BOUND_CHECKS.filter(check => check !== "review_round_ledger")),
  adjudication: Object.freeze(
    V5_ADJUDICATION_CHECKS.filter(check => check !== "bounded_adjudication_receipt_store")),
  receipt: Object.freeze(["receipt_content_addressed", "receipt_resolvable", "receipt_digest",
    "receipt_kind", "receipt_change_binding", "receipt_delivered_set_binding",
    "receipt_outcome_vocabulary", "receipt_adjudicator_separation"]),
});

const SHA256_HEX = /^[0-9a-f]{64}$/;
const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
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

// ---------------------------------------------------------------------------
// Shape reading. An open schema is an unenforced one, so every request object
// is CLOSED: a field this file does not read is a field the caller believes is
// being read, and that is how a contract rots quietly.
// ---------------------------------------------------------------------------

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
    fail("not_a_positive_integer", `${path} must be an integer of at least 1`,
      { path, value: value ?? null });
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
 * Order and uniqueness are REQUEST SHAPE, not policy: two callers who disagree
 * about the order of a finding set are describing two different finding sets,
 * and silently sorting one would hand back an identity its producer never wrote.
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
// The two answer shapes. Neither of them is ever a clearance.
// ---------------------------------------------------------------------------

function reason(clause) {
  const id = V5_A03_CLAUSE_REASONS[clause];
  if (!V5_A03_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a registered reason`, { clause, reason_id: id ?? null });
  return id;
}

function blocked(clause, detail) {
  return deepFreeze({
    classification: V5_A03_CLASSIFICATIONS.refuse,
    blocking_clause: clause,
    reason_id: reason(clause),
    is_not_authority: true,
    evidence_source: V5_A03_CLASSIFIER_EVIDENCE_SOURCE,
    detail: detail ?? {},
  });
}

function clear(classification) {
  return deepFreeze({
    classification,
    blocking_clause: null,
    reason_id: null,
    is_not_authority: true,
    evidence_source: V5_A03_CLASSIFIER_EVIDENCE_SOURCE,
    detail: {},
  });
}

// ---------------------------------------------------------------------------
// Q154.D1 + Q107.D1 — routing a complete-set review across the eleven
// dimensions without letting any duty certify its own work.
// ---------------------------------------------------------------------------

/** Pairs that name `reviewer`, flattened to the other role in each pair. */
const ROLES_OPPOSING_REVIEWER = Object.freeze(
  V5_OPPOSING_ROLE_PAIRS.filter(pair => pair.includes("reviewer"))
    .map(pair => pair.find(role => role !== "reviewer"))
    .sort());

/** Pairs where neither side is `reviewer`; these are checked role-to-role. */
const NON_REVIEWER_OPPOSING_PAIRS = Object.freeze(
  V5_OPPOSING_ROLE_PAIRS.filter(pair => !pair.includes("reviewer")));

/**
 * Which dimensions are missing from, or repeated in, a dimension list.
 *
 * Complete-set review is the whole point of the slice: the set of dimensions is
 * closed, so "we reviewed the interesting ones" is a gap with a name rather than
 * a judgment call. An empty gap is a statement about the caller's list, never a
 * statement that the review happened.
 */
export function reviewDimensionGap(dimensions) {
  const seen = new Map();
  for (const dimension of dimensions) seen.set(dimension, (seen.get(dimension) ?? 0) + 1);
  return deepFreeze({
    missing: V5_REVIEW_DIMENSIONS.filter(dimension => !seen.has(dimension)),
    duplicated: [...seen.entries()].filter(([, times]) => times > 1).map(([dimension]) => dimension).sort(),
  });
}

/**
 * Every opposing-role pair that resolves to one identity.
 *
 * Q107.D1 does not say the six duties must be six people — it says one model
 * "may occupy different roles at different times, but never opposing roles for
 * the same change". So this returns the COLLISIONS, not a verdict: a pair and
 * the identity standing on both sides of it. An empty list says the caller's own
 * description contains no collision, which is not the same fact as the
 * identities being separate, and only the registry can supply that one.
 */
export function roleSeparationCollisions(roleIdentities, reviewerIdentities) {
  const collisions = [];
  for (const [left, right] of NON_REVIEWER_OPPOSING_PAIRS) {
    if (roleIdentities[left] === roleIdentities[right])
      collisions.push({ pair: [left, right], identity_ref: roleIdentities[left] });
  }
  for (const role of ROLES_OPPOSING_REVIEWER) {
    for (const reviewer of reviewerIdentities) {
      if (reviewer === roleIdentities[role])
        collisions.push({ pair: [role, "reviewer"].sort(), identity_ref: reviewer });
    }
  }
  collisions.sort((a, b) =>
    a.pair.join(",").localeCompare(b.pair.join(",")) || a.identity_ref.localeCompare(b.identity_ref));
  return deepFreeze(collisions.map(entry => ({ pair: entry.pair, identity_ref: entry.identity_ref })));
}

export function normalizeRoutingRequest(request) {
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
 * The routing classifier. Clauses run in order and the FIRST one that blocks is
 * the answer, so a caller is told the earliest thing wrong with its description
 * rather than a pile of consequences.
 */
export function classifyRoutingIfAuthoritative(request) {
  normalizeRoutingRequest(request);
  const dimensions = request.assignments.map(assignment => assignment.dimension);
  const gap = reviewDimensionGap(dimensions);
  if (gap.missing.length || gap.duplicated.length)
    return blocked("dimension_coverage", { missing: gap.missing, duplicated: gap.duplicated });

  const reviewers = request.assignments.map(assignment => assignment.reviewer_identity_ref);
  const collisions = roleSeparationCollisions(request.role_identities, reviewers);
  const reviewerCollisions = collisions.filter(entry => entry.pair.includes("reviewer"));
  if (reviewerCollisions.length)
    return blocked("reviewer_role_separation", { collisions: reviewerCollisions });
  if (collisions.length)
    return blocked("duty_role_separation", { collisions });

  // Q028.D1 asks for fresh-context review, and the mechanical form of "fresh"
  // is that the reviewing session is not the making session. An inherited
  // context is the self-certification this slice exists to stop, wearing the
  // costume of a second opinion.
  const stale = request.assignments
    .filter(assignment => assignment.context_binding !== "fresh" ||
      assignment.reviewer_session_ref === request.maker_session_ref)
    .map(assignment => assignment.dimension);
  if (stale.length) return blocked("fresh_context", { dimensions: stale });

  return clear(V5_A03_CLASSIFICATIONS.routing);
}

// ---------------------------------------------------------------------------
// Q113.D1 + Q154.D1 — the complete finding set is enumerated before any repair.
// ---------------------------------------------------------------------------

export function normalizeFindingSetRequest(request) {
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
  return request;
}

/**
 * The finding-set classifier.
 *
 * THE SCOPE CLAUSE IS THE ONE THAT MATTERS, and it is why the affirmative
 * classification is named for it: a reviewer who reports on a subset of the
 * delivered artifacts has produced a review that READS as complete and is not,
 * and no count of findings reveals it. Each submission states the digest of the
 * set it actually read, so a narrower review is caught by arithmetic rather than
 * by suspicion — over the digests the caller supplied.
 */
export function classifyFindingSetIfAuthoritative(request) {
  normalizeFindingSetRequest(request);
  const gap = reviewDimensionGap(request.submissions.map(submission => submission.dimension));
  const absent = request.submissions
    .filter(submission => submission.state !== "submitted")
    .map(submission => submission.dimension).sort();
  if (gap.missing.length || gap.duplicated.length || absent.length)
    return blocked("dimension_submission", { missing: gap.missing, duplicated: gap.duplicated, absent });

  const narrowed = request.submissions
    .filter(submission => submission.reviewed_set_digest !== request.delivered_set_digest)
    .map(submission => submission.dimension);
  if (narrowed.length) return blocked("delivered_set_scope", { dimensions: narrowed });

  const edited = request.submissions
    .filter(submission => submission.enumerated_before_repair !== true)
    .map(submission => submission.dimension);
  if (edited.length) return blocked("enumeration_before_repair", { dimensions: edited });

  return clear(V5_A03_CLASSIFICATIONS.findingSet);
}

// ---------------------------------------------------------------------------
// Q042.D1 — at most two full rounds, then adjudication; and the four drifts the
// settled text names by hand.
// ---------------------------------------------------------------------------

export function normalizeRoundRequest(request) {
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
 * What the round bound OBLIGES at a given ordinal. Necessary, never sufficient:
 * satisfying the bound does not admit a round, because the ledger that would say
 * which round this really is does not exist.
 */
export function reviewRoundObligation(value) {
  const withinLimit = Number.isInteger(value) && value >= 1 && value <= V5_MAX_REVIEW_ROUNDS;
  return deepFreeze({
    ordinal: value,
    round_limit: V5_MAX_REVIEW_ROUNDS,
    within_round_limit: withinLimit,
    required_transition: withinLimit ? "independent_review_round" : "stronger_adjudication",
    is_not_authority: true,
    evidence_source: V5_A03_CLASSIFIER_EVIDENCE_SOURCE,
  });
}

/**
 * The four drifts Q042.D1 names verbatim: "repeated findings, circular
 * reversions, reviewer instability, and 'fixes' that merely weaken tests".
 *
 * Each is a set comparison across the round history, so each is decidable and
 * none of them is a judgment about quality:
 *
 *   repeated_finding      a finding marked resolved in one round and reported
 *                         again in a later one — the repair did not take.
 *   circular_reversion    a later round's post-repair artifact digest equals a
 *                         digest an earlier round already rejected; the tree is
 *                         going round rather than forward.
 *   reviewer_instability  one reviewer returning both states for the same
 *                         dimension on the same delivered set.
 *   test_weakening        a regression suite that executed a check in one ROUND
 *                         and does not execute it in the next.
 *
 * TEST WEAKENING IS AGGREGATED BY ROUND, and that is a correction. The earlier
 * version compared entry to entry, so two dimensions in the SAME round reporting
 * different partitions of one suite read as weakening, and one entry's list
 * silently replaced the round's baseline. A round's regression evidence is the
 * union of its entries' executed checks per suite; weakening is a shrink between
 * one round's union and the next round's, for a suite both rounds ran. Whether a
 * later round ran the suite AT ALL is a round-ledger question, not a set
 * comparison, so it is not claimed here.
 */
export function detectRoundRegressions(history) {
  const resolvedBefore = new Map();
  const repeated = new Set();
  const rejectedDigests = new Map();
  const circular = new Set();
  const stateByReviewerDimension = new Map();
  const unstable = new Set();
  const weakened = new Set();
  const checksByRoundSuite = new Map();

  const ordered = [...history].sort((a, b) => a.round_ordinal - b.round_ordinal);
  for (const entry of ordered) {
    for (const finding of entry.finding_refs)
      if (resolvedBefore.has(finding) && resolvedBefore.get(finding) < entry.round_ordinal)
        repeated.add(finding);
    for (const finding of entry.resolved_finding_refs)
      if (!resolvedBefore.has(finding)) resolvedBefore.set(finding, entry.round_ordinal);

    if (entry.state === "changes_required")
      rejectedDigests.set(entry.post_repair_artifact_digest, entry.round_ordinal);
    else if (rejectedDigests.has(entry.post_repair_artifact_digest) &&
             rejectedDigests.get(entry.post_repair_artifact_digest) < entry.round_ordinal)
      circular.add(entry.post_repair_artifact_digest);

    const identity = `${entry.reviewer_identity_ref}|${entry.dimension}`;
    if (stateByReviewerDimension.has(identity) && stateByReviewerDimension.get(identity) !== entry.state)
      unstable.add(identity);
    stateByReviewerDimension.set(identity, entry.state);

    if (!checksByRoundSuite.has(entry.round_ordinal)) checksByRoundSuite.set(entry.round_ordinal, new Map());
    const suites = checksByRoundSuite.get(entry.round_ordinal);
    if (!suites.has(entry.regression.suite_ref)) suites.set(entry.regression.suite_ref, new Set());
    for (const check of entry.regression.checks_executed) suites.get(entry.regression.suite_ref).add(check);
  }

  const rounds = [...checksByRoundSuite.keys()].sort((a, b) => a - b);
  for (let index = 1; index < rounds.length; index += 1) {
    const earlier = checksByRoundSuite.get(rounds[index - 1]);
    const later = checksByRoundSuite.get(rounds[index]);
    for (const [suite, checks] of earlier) {
      if (!later.has(suite)) continue;
      for (const check of checks) if (!later.get(suite).has(check)) weakened.add(`${suite}|${check}`);
    }
  }

  return deepFreeze({
    repeated_finding: [...repeated].sort(),
    circular_reversion: [...circular].sort(),
    reviewer_instability: [...unstable].sort(),
    test_weakening: [...weakened].sort(),
    is_not_authority: true,
    evidence_source: V5_A03_CLASSIFIER_EVIDENCE_SOURCE,
  });
}

/**
 * The round classifier.
 *
 * Note the clause ORDER: the drift detectors run BEFORE the prior-round clause,
 * because the prior-round clause can no longer be satisfied at all and a caller
 * whose history is visibly drifting should be told that rather than the
 * unreadable clause.
 */
export function classifyRoundIfAuthoritative(request) {
  normalizeRoundRequest(request);
  if (request.adjudication_recorded === true)
    return blocked("round_reopened_after_adjudication", { round_limit: V5_MAX_REVIEW_ROUNDS });

  const obligation = reviewRoundObligation(request.requested_round_ordinal);
  if (!obligation.within_round_limit)
    return blocked("round_limit", {
      requested_round_ordinal: request.requested_round_ordinal,
      round_limit: V5_MAX_REVIEW_ROUNDS,
      required_transition: obligation.required_transition,
    });

  const regressions = detectRoundRegressions(request.history);
  const fired = V5_ROUND_REGRESSION_CLASSES.filter(name => regressions[name].length > 0);
  if (fired.length) return blocked("round_drift", { fired, regressions });

  // THE CLAUSE THAT CANNOT BE SATISFIED. Q042.D1 requires the prior round's
  // complete finding set, its repair as ONE batch, and a full regression bound
  // to that batch. A history entry carrying the prior ordinal evidences none of
  // the three — it is the caller's own account of a round nobody recorded — so
  // any round past the first blocks here until the ledger exists.
  if (request.requested_round_ordinal > 1)
    return blocked("prior_round_batch_regression", {
      requested_round_ordinal: request.requested_round_ordinal,
      required_seam: "seam:review-round-ledger",
      unreadable_facts: ["complete prior finding set", "batch repair of that set",
        "regression run bound to that batch"],
    });

  return clear(V5_A03_CLASSIFICATIONS.round);
}

// ---------------------------------------------------------------------------
// Q042.D1 step 4 + Q028.D1 — bounded adjudication by a role that is nobody in
// the dispute.
// ---------------------------------------------------------------------------

export function normalizeAdjudicationRequest(request) {
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

/** The adjudication classifier. */
export function classifyAdjudicationIfAuthoritative(request) {
  normalizeAdjudicationRequest(request);
  // The caller's own word is NOT echoed into the detail. A caller who names its
  // role "pass" would otherwise get that string back inside a refusal, and a
  // sweep looking for privileged words in an answer cannot tell whose word it
  // is. What is owed is the role that WOULD be required, which is this module's.
  if (request.adjudicator_role !== V5_ADJUDICATOR_ROLE)
    return blocked("adjudicator_role", { adjudicator_role_required: V5_ADJUDICATOR_ROLE });

  const parties = [request.maker_identity_ref, request.releaser_identity_ref,
    ...request.reviewer_identity_refs];
  if (parties.includes(request.adjudicator_identity_ref))
    return blocked("adjudicator_separation", {
      adjudicator_identity_ref: request.adjudicator_identity_ref,
      also_a_party_as: [
        ...(request.maker_identity_ref === request.adjudicator_identity_ref ? ["builder"] : []),
        ...(request.releaser_identity_ref === request.adjudicator_identity_ref
          ? ["deployment_controller"] : []),
        ...(request.reviewer_identity_refs.includes(request.adjudicator_identity_ref)
          ? ["reviewer"] : []),
      ].sort(),
    });

  // Adjudication is step 4 of the settled loop, not a shortcut past steps 1-3.
  // Reaching for a stronger judge before the bound is spent is how a maker skips
  // the review it did not want.
  if (request.rounds_completed < V5_MAX_REVIEW_ROUNDS)
    return blocked("rounds_before_adjudication", {
      rounds_completed: request.rounds_completed, round_limit: V5_MAX_REVIEW_ROUNDS,
    });

  if (request.disputed_finding_refs.length === 0) return blocked("disputed_set_empty", {});

  return clear(V5_A03_CLASSIFICATIONS.adjudication);
}

// ---------------------------------------------------------------------------
// The receipt SHAPE clauses. Shape only, and never a verification.
// ---------------------------------------------------------------------------

/**
 * Classify one adjudication receipt BODY against the reference that cited it, in
 * the order a forger has to survive: the reference is content-addressed,
 * something came back, the bytes hash to the reference, it is the kind that was
 * asked for, it binds this change and this delivered set, its outcome is in the
 * closed vocabulary, and its adjudicator is nobody in the dispute.
 *
 * THIS IS NOT A VERIFICATION, AND THE NAME OF ITS ANSWER SAYS SO. Every input is
 * the caller's: the body, its citation, the binding and the party list. A caller
 * can therefore write a receipt about its own dispute, hash it, cite the hash,
 * omit itself from the party list, and reach `would_be_verifiable_if_
 * authoritative` — which means precisely "these bytes are SHAPED like a receipt",
 * and nothing whatever about whether such a receipt exists or what it decided.
 * The public surface exposes none of this, and the outcome the body carries is
 * never echoed back from here either.
 */
export function classifyAdjudicationReceiptIfAuthoritative(kind, receiptRef, body, binding) {
  member(kind, [V5_ADJUDICATION_RECEIPT_KIND], "kind");
  exact(binding, ["change_ref", "delivered_set_digest", "party_identity_refs"], "binding");
  ref(binding.change_ref, "binding.change_ref");
  sha256Hex(binding.delivered_set_digest, "binding.delivered_set_digest");
  sortedUnique(binding.party_identity_refs, "binding.party_identity_refs", ref);

  if (typeof receiptRef !== "string" || !RECEIPT_REF.test(receiptRef))
    return blocked("receipt_content_addressed", { receipt_ref: typeof receiptRef === "string" ? receiptRef : null });
  if (!isPlainObject(body)) return blocked("receipt_resolvable", { receipt_ref: receiptRef });
  const bodyDigest = digest(body);
  if (bodyDigest !== receiptRef.slice(RECEIPT_REF_PREFIX.length))
    return blocked("receipt_digest", { receipt_ref: receiptRef, resolved_digest: bodyDigest });
  if (body.kind !== kind) return blocked("receipt_kind", { receipt_ref: receiptRef });

  exact(body, ["adjudicator_identity_ref", "change_ref", "delivered_set_digest", "disputed_finding_refs",
    "kind", "outcome", "rounds_completed"], `receipt(${kind})`);
  ref(body.adjudicator_identity_ref, `receipt(${kind}).adjudicator_identity_ref`);
  ref(body.change_ref, `receipt(${kind}).change_ref`);
  sha256Hex(body.delivered_set_digest, `receipt(${kind}).delivered_set_digest`);
  sortedUnique(body.disputed_finding_refs, `receipt(${kind}).disputed_finding_refs`, ref);
  count(body.rounds_completed, `receipt(${kind}).rounds_completed`);

  if (body.change_ref !== binding.change_ref)
    return blocked("receipt_change_binding", { receipt_ref: receiptRef });
  if (body.delivered_set_digest !== binding.delivered_set_digest)
    return blocked("receipt_delivered_set_binding", { receipt_ref: receiptRef });
  if (typeof body.outcome !== "string" || !V5_ADJUDICATION_OUTCOME_CODE_SET.includes(body.outcome))
    return blocked("receipt_outcome_vocabulary", { receipt_ref: receiptRef });
  if (binding.party_identity_refs.includes(body.adjudicator_identity_ref))
    return blocked("receipt_adjudicator_separation", { receipt_ref: receiptRef });

  return clear(V5_A03_CLASSIFICATIONS.receipt);
}

// ---------------------------------------------------------------------------
// THE POLICY PREIMAGE MIRROR.
//
// `v5A03PolicyPreimage` and `v5A03PolicyCanonicalBytes` are module-private in
// production as of the second correction of PR 987, because the preimage
// recites every closed vocabulary this slice holds and a consumer reading those
// bytes cannot tell a vocabulary from a verdict. Only the digest is exported.
//
// This is the readable copy, and it lives on the test side of the wall with the
// classifiers. It is not a second source of truth: the suite asserts that its
// canonical bytes digest to `v5A03PolicyDigest()`, so if production's preimage
// and this mirror ever disagree by one field, one key or one sort order, the
// digests differ and the suite fails. That is what makes it safe to read this
// instead of the original.
// ---------------------------------------------------------------------------

/** The fields the private production preimage carries, in the same shapes. */
export function v5A03PolicyPreimageMirror() {
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
    adjudication_outcome_codes: [...V5_ADJUDICATION_OUTCOME_CODE_SET].sort(),
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
    reason_ids: [...V5_A03_REASON_IDS].sort(),
    public_reason_ids: [...V5_A03_PUBLIC_REASON_IDS].sort(),
    seams: Object.fromEntries(Object.keys(V5_A03_SEAMS).sort().map(key => [key, V5_A03_SEAMS[key]])),
    authoritative_holders_bound: false,
    public_surface_answers: "unavailable",
    clause_evaluation_is_test_only: true,
  };
}
