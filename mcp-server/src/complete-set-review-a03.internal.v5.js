// DoctorCRE v5 slice V5-A03 — INTERNAL classifier. NOT A PUBLIC SURFACE.
//
// WHY THIS FILE EXISTS AT ALL, said first, because a second file is the kind of
// thing a reviewer should be suspicious of.
//
// The four decisions V5-A03 owns — review routing, complete-finding-set
// acceptance, the review-round bound and bounded adjudication — all end in an
// authoritative store that DOES NOT EXIST in this repository (the seam census
// is in complete-set-review-a03.v5.js). So every composed public evaluator
// refuses, always, on every input. That is the honest answer, and it is also a
// trap: a decision layer that only ever says no cannot be proved to have
// decided anything, and the day its store arrives nobody will know whether the
// clauses underneath were ever right.
//
// The clauses therefore live here, as one classifier per decision, and the
// public module CALLS them to name the clause that would have blocked. They are
// load-bearing in the refusal (the public answer reports `blocking_clause` from
// exactly these functions) and they are directly testable. What they must never
// be is an authorization.
//
// THE THREE PROPERTIES THAT KEEP THEM FROM BECOMING ONE:
//
//   1. NO PRIVILEGED WORD IS EVER RETURNED. A classifier returns
//      `classification` from the closed pair
//      ["would_refuse", "would_<verb>_if_authoritative"]. There is no "pass",
//      no "allow", no "complete", no "covered", no "independent" and no
//      "released" among the values any of these functions can produce. A
//      consumer that mistook `would_route_if_authoritative` for permission
//      would be reading a word that says, in itself, that it is not one.
//
//   2. NOT REACHABLE THROUGH THE PUBLIC SURFACE. Nothing here is re-exported by
//      complete-set-review-a03.v5.js. The test suite proves that with Node's
//      own module parser (`vm.SourceTextModule`), not a regex: it reads the
//      public module's real export names and its real import specifiers, and it
//      reads every other non-test file in the tree to prove that the public
//      module is this file's only non-test importer.
//
//   3. THE ANSWER IS NOT THE DECISION. `would_route_if_authoritative` means
//      "the deterministic clauses this module can check found nothing wrong
//      with what you told me". It says nothing about whether what you told me
//      is true, because the registry that would say so is missing. The public
//      evaluators fold that in and still refuse.
//
// PURE, like every other v5 decision module: no filesystem, no network, no
// database, no clock, no environment. It also holds no state between calls.

import {
  V5_ADJUDICATOR_ROLE,
  V5_MAX_REVIEW_ROUNDS,
  V5_REVIEW_DIMENSIONS,
  V5_ROUND_REGRESSION_CLASSES,
  V5_NON_REVIEWER_ROLES,
  V5_OPPOSING_ROLE_PAIRS,
  V5_ROUTING_CHECKS,
  V5_FINDING_SET_CHECKS,
  V5_ROUND_BOUND_CHECKS,
  V5_ADJUDICATION_CHECKS,
} from "./complete-set-review-a03.vocabulary.v5.js";

/**
 * The only two classifications any function in this file may return.
 *
 * Both halves are deliberately worded as statements about a hypothetical. The
 * conditional is not decoration: it is the whole content of the answer.
 */
export const V5_A03_CLASSIFICATIONS = Object.freeze({
  refuse: "would_refuse",
  routing: "would_route_if_authoritative",
  findingSet: "would_accept_finding_set_if_authoritative",
  round: "would_admit_round_if_authoritative",
  adjudication: "would_adjudicate_if_authoritative",
});

/** Pairs that name `reviewer`, flattened to the other role in each pair. */
const ROLES_OPPOSING_REVIEWER = Object.freeze(
  V5_OPPOSING_ROLE_PAIRS.filter(pair => pair.includes("reviewer"))
    .map(pair => pair.find(role => role !== "reviewer"))
    .sort());

/** Pairs where neither side is `reviewer`; these are checked role-to-role. */
const NON_REVIEWER_OPPOSING_PAIRS = Object.freeze(
  V5_OPPOSING_ROLE_PAIRS.filter(pair => !pair.includes("reviewer")));

function blocked(clause, detail) {
  return Object.freeze({
    classification: V5_A03_CLASSIFICATIONS.refuse,
    blocking_clause: clause,
    detail: Object.freeze(detail ?? {}),
  });
}

function clear(classification) {
  return Object.freeze({ classification, blocking_clause: null, detail: Object.freeze({}) });
}

// ---------------------------------------------------------------------------
// Q154.D1 + Q107.D1 — routing a complete-set review across the eleven
// dimensions without letting any duty certify its own work.
// ---------------------------------------------------------------------------

/**
 * Which dimensions are missing from, or repeated in, an assignment list.
 *
 * Complete-set review is the whole point of the slice: a reviewer sees the
 * WHOLE delivered set, and the set of dimensions is closed, so "we reviewed the
 * interesting ones" is a gap with a name rather than a judgment call.
 */
export function reviewDimensionGap(dimensions) {
  const seen = new Map();
  for (const dimension of dimensions) seen.set(dimension, (seen.get(dimension) ?? 0) + 1);
  return Object.freeze({
    missing: Object.freeze(V5_REVIEW_DIMENSIONS.filter(dimension => !seen.has(dimension))),
    duplicated: Object.freeze([...seen.entries()]
      .filter(([, count]) => count > 1).map(([dimension]) => dimension).sort()),
  });
}

/**
 * Every opposing-role pair that resolves to one identity.
 *
 * Q107.D1 does not say the six duties must be six people — it says one model
 * "may occupy different roles at different times, but never opposing roles for
 * the same change". So this returns the COLLISIONS, not a verdict: a pair and
 * the identity standing on both sides of it. An empty list is a statement about
 * the caller's own description, never a clearance.
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
  return Object.freeze(collisions.map(entry =>
    Object.freeze({ pair: Object.freeze(entry.pair), identity_ref: entry.identity_ref })));
}

/**
 * The routing classifier. Clauses run in V5_ROUTING_CHECKS order and the FIRST
 * one that blocks is the answer, so a caller is told the earliest thing wrong
 * with its description rather than a pile of consequences.
 */
export function classifyRoutingIfAuthoritative(request) {
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
  if (stale.length) return blocked("fresh_context", { dimensions: Object.freeze(stale) });

  return clear(V5_A03_CLASSIFICATIONS.routing);
}

// ---------------------------------------------------------------------------
// Q113.D1 + Q154.D1 — the complete finding set is enumerated before any repair.
// ---------------------------------------------------------------------------

/**
 * The finding-set classifier.
 *
 * THE SCOPE CLAUSE IS THE ONE THAT MATTERS. A reviewer who reports on a subset
 * of the delivered artifacts has produced a review that reads as complete and
 * is not, and no count of findings reveals it. So each submission states the
 * digest of the set it actually read, and a digest that is not the delivered
 * set's is a narrower review by construction rather than by suspicion.
 */
export function classifyFindingSetIfAuthoritative(request) {
  const gap = reviewDimensionGap(request.submissions.map(submission => submission.dimension));
  const absent = request.submissions
    .filter(submission => submission.state !== "submitted")
    .map(submission => submission.dimension).sort();
  if (gap.missing.length || gap.duplicated.length || absent.length)
    return blocked("dimension_submission", {
      missing: gap.missing, duplicated: gap.duplicated, absent: Object.freeze(absent),
    });

  const narrowed = request.submissions
    .filter(submission => submission.reviewed_set_digest !== request.delivered_set_digest)
    .map(submission => submission.dimension);
  if (narrowed.length) return blocked("complete_set_scope", { dimensions: Object.freeze(narrowed) });

  const edited = request.submissions
    .filter(submission => submission.enumerated_before_repair !== true)
    .map(submission => submission.dimension);
  if (edited.length) return blocked("enumeration_before_repair", { dimensions: Object.freeze(edited) });

  return clear(V5_A03_CLASSIFICATIONS.findingSet);
}

// ---------------------------------------------------------------------------
// Q042.D1 — at most two full rounds, then adjudication; and the four drifts the
// settled text names by hand.
// ---------------------------------------------------------------------------

/**
 * What the round bound OBLIGES at a given ordinal. Necessary, never sufficient:
 * satisfying the bound does not admit a round, because the ledger that would
 * say which round this really is does not exist.
 */
export function reviewRoundObligation(ordinal) {
  const withinLimit = Number.isInteger(ordinal) && ordinal >= 1 && ordinal <= V5_MAX_REVIEW_ROUNDS;
  return Object.freeze({
    ordinal,
    round_limit: V5_MAX_REVIEW_ROUNDS,
    within_round_limit: withinLimit,
    required_transition: withinLimit ? "independent_review_round" : "stronger_adjudication",
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
 *   test_weakening        a regression suite that executed a check in an
 *                         earlier round and does not execute it in a later one.
 */
export function detectRoundRegressions(history) {
  const resolvedBefore = new Map();
  const repeated = new Set();
  const rejectedDigests = new Map();
  const circular = new Set();
  const stateByReviewerDimension = new Map();
  const unstable = new Set();
  const checksBySuite = new Map();
  const weakened = new Set();

  for (const entry of [...history].sort((a, b) => a.round_ordinal - b.round_ordinal)) {
    for (const ref of entry.finding_refs)
      if (resolvedBefore.has(ref) && resolvedBefore.get(ref) < entry.round_ordinal) repeated.add(ref);
    for (const ref of entry.resolved_finding_refs)
      if (!resolvedBefore.has(ref)) resolvedBefore.set(ref, entry.round_ordinal);

    if (entry.state === "changes_required")
      rejectedDigests.set(entry.post_repair_artifact_digest, entry.round_ordinal);
    else if (rejectedDigests.has(entry.post_repair_artifact_digest) &&
             rejectedDigests.get(entry.post_repair_artifact_digest) < entry.round_ordinal)
      circular.add(entry.post_repair_artifact_digest);

    const identity = `${entry.reviewer_identity_ref}|${entry.dimension}`;
    if (stateByReviewerDimension.has(identity) && stateByReviewerDimension.get(identity) !== entry.state)
      unstable.add(identity);
    stateByReviewerDimension.set(identity, entry.state);

    const previous = checksBySuite.get(entry.regression.suite_ref);
    if (previous)
      for (const check of previous)
        if (!entry.regression.checks_executed.includes(check))
          weakened.add(`${entry.regression.suite_ref}|${check}`);
    checksBySuite.set(entry.regression.suite_ref, entry.regression.checks_executed);
  }

  return Object.freeze({
    repeated_finding: Object.freeze([...repeated].sort()),
    circular_reversion: Object.freeze([...circular].sort()),
    reviewer_instability: Object.freeze([...unstable].sort()),
    test_weakening: Object.freeze([...weakened].sort()),
  });
}

/** The round classifier. */
export function classifyRoundIfAuthoritative(request) {
  if (request.adjudication_recorded === true)
    return blocked("round_reopened_after_adjudication", { round_limit: V5_MAX_REVIEW_ROUNDS });

  const obligation = reviewRoundObligation(request.requested_round_ordinal);
  if (!obligation.within_round_limit)
    return blocked("round_limit", {
      requested_round_ordinal: request.requested_round_ordinal,
      round_limit: V5_MAX_REVIEW_ROUNDS,
      required_transition: obligation.required_transition,
    });

  // A second round that starts before the first round's findings were repaired
  // AS A BATCH and fully regressed is the one-defect-at-a-time spiral Joe named
  // when he settled Q042, so it is refused by ordinal rather than by taste.
  const priorRounds = new Set(request.history.map(entry => entry.round_ordinal));
  const missingPrior = [];
  for (let ordinal = 1; ordinal < request.requested_round_ordinal; ordinal += 1)
    if (!priorRounds.has(ordinal)) missingPrior.push(ordinal);
  if (missingPrior.length)
    return blocked("prior_round_batch_regression", { missing_rounds: Object.freeze(missingPrior) });

  const regressions = detectRoundRegressions(request.history);
  const fired = V5_ROUND_REGRESSION_CLASSES.filter(name => regressions[name].length > 0);
  if (fired.length)
    return blocked("round_drift", { fired: Object.freeze(fired), regressions });

  return clear(V5_A03_CLASSIFICATIONS.round);
}

// ---------------------------------------------------------------------------
// Q042.D1 step 4 + Q028.D1 — bounded adjudication by a role that is nobody in
// the dispute.
// ---------------------------------------------------------------------------

/** The adjudication classifier. */
export function classifyAdjudicationIfAuthoritative(request) {
  if (request.adjudicator_role !== V5_ADJUDICATOR_ROLE)
    return blocked("adjudicator_role", { adjudicator_role: request.adjudicator_role });

  const parties = [request.maker_identity_ref, request.releaser_identity_ref,
    ...request.reviewer_identity_refs];
  if (parties.includes(request.adjudicator_identity_ref))
    return blocked("adjudicator_separation", {
      adjudicator_identity_ref: request.adjudicator_identity_ref,
      also_a_party_as: Object.freeze([
        ...(request.maker_identity_ref === request.adjudicator_identity_ref ? ["builder"] : []),
        ...(request.releaser_identity_ref === request.adjudicator_identity_ref
          ? ["deployment_controller"] : []),
        ...(request.reviewer_identity_refs.includes(request.adjudicator_identity_ref)
          ? ["reviewer"] : []),
      ].sort()),
    });

  // Adjudication is step 4 of the settled loop, not a shortcut past steps 1-3.
  // Reaching for a stronger judge before the bound is spent is how a maker
  // skips the review it did not want.
  if (request.rounds_completed < V5_MAX_REVIEW_ROUNDS)
    return blocked("rounds_before_adjudication", {
      rounds_completed: request.rounds_completed, round_limit: V5_MAX_REVIEW_ROUNDS,
    });

  if (request.disputed_finding_refs.length === 0)
    return blocked("disputed_set_empty", {});

  return clear(V5_A03_CLASSIFICATIONS.adjudication);
}

/** Every clause name any classifier above can name, for the vocabulary test. */
export const V5_A03_CLAUSE_NAMES = Object.freeze([
  ...V5_ROUTING_CHECKS, ...V5_FINDING_SET_CHECKS, ...V5_ROUND_BOUND_CHECKS, ...V5_ADJUDICATION_CHECKS,
].filter(name => !name.endsWith("_store") && !name.endsWith("_registry") && !name.endsWith("_ledger"))
  .sort());

/** The non-reviewer duties, re-exported so the public module has one source. */
export { V5_NON_REVIEWER_ROLES };
