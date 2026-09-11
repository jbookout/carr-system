// DoctorCRE v5 slice V5-A03 — independent complete-set review and bounded
// adjudication.
//
// Three kinds of test live here and they are not interchangeable:
//
//   CLAUSE tests prove the deterministic content — that a missing dimension,
//   a maker reviewing itself, a narrowed review scope, a third round and an
//   adjudicator who is a party to the dispute are each caught, by the clause
//   that names them, before anything else.
//
//   REFUSAL tests prove the honest unavailable — that every composed evaluator
//   refuses on every input because its authoritative holder does not exist, and
//   names the seam it is owed rather than guessing.
//
//   GUARD tests prove the standing rule: no caller-supplied label, fixture or
//   injected holder is authority. They sweep the whole public export surface
//   against caller-controlled input and assert the privileged outcome never
//   comes back, and they use NODE'S OWN MODULE PARSER (vm.SourceTextModule),
//   not a regex, to prove the internal classifier is unreachable through the
//   public surface and imported by nothing else in the tree.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { V5BoundaryError } from "../src/global-boundaries.v5.js";
import * as a03 from "../src/complete-set-review-a03.v5.js";
import {
  V5_A03_CLASSIFICATIONS,
  classifyAdjudicationIfAuthoritative,
  classifyFindingSetIfAuthoritative,
  classifyRoundIfAuthoritative,
  classifyRoutingIfAuthoritative,
} from "../src/complete-set-review-a03.internal.v5.js";

const {
  V5_A03_DECISION_IDS, V5_A03_REASON_IDS, V5_A03_SCHEMA_VERSION, V5_A03_SEAMS,
  V5_A03_SETTLED_DECISIONS, V5_ADJUDICATION_CHECKS, V5_ADJUDICATION_OUTCOMES,
  V5_ADJUDICATION_RECEIPT_KIND, V5_ADJUDICATOR_ROLE, V5_FINDING_SET_CHECKS,
  V5_MAX_REVIEW_ROUNDS, V5_NON_REVIEWER_ROLES, V5_OPPOSING_ROLE_PAIRS, V5_REVIEW_DIMENSIONS,
  V5_ROUND_BOUND_CHECKS, V5_ROUND_REGRESSION_CLASSES, V5_ROUTING_CHECKS,
  assertA03DecisionBinding, detectRoundRegressions, evaluateAdjudication,
  evaluateFindingSetCompleteness, evaluateReviewRoundAdmission, evaluateReviewRouting,
  reviewDimensionGap, reviewRoundObligation, roleSeparationCollisions,
  v5A03PolicyCanonicalBytes, v5A03PolicyDigest, v5A03PolicyPreimage, verifyAdjudicationReceipt,
} = a03;

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");

const SET_DIGEST = "a".repeat(64);
const OTHER_SET_DIGEST = "b".repeat(64);
const CHANGE = "change:v5-a03";

// ---------------------------------------------------------------------------
// Fixtures. Every one is the HONEST shape — complete, separated, fresh — so a
// test that mutates one field is testing exactly that field.
// ---------------------------------------------------------------------------

const ROLE_IDENTITIES = Object.freeze({
  adjudicator: "actor:adjudicator",
  architect: "actor:architect",
  builder: "actor:builder",
  deployment_controller: "actor:releaser",
  integration_controller: "actor:integrator",
  program_controller: "actor:program",
});

function routingRequest(overrides = {}) {
  return {
    change_ref: CHANGE,
    delivered_set_digest: SET_DIGEST,
    maker_session_ref: "session:maker",
    role_identities: { ...ROLE_IDENTITIES, ...(overrides.role_identities ?? {}) },
    assignments: overrides.assignments ?? V5_REVIEW_DIMENSIONS.map(dimension => ({
      dimension,
      reviewer_identity_ref: `actor:reviewer-${dimension}`,
      reviewer_session_ref: `session:review-${dimension}`,
      context_binding: "fresh",
    })),
    ...Object.fromEntries(Object.entries(overrides)
      .filter(([key]) => !["role_identities", "assignments"].includes(key))),
  };
}

function findingSetRequest(overrides = {}) {
  return {
    change_ref: CHANGE,
    delivered_set_digest: SET_DIGEST,
    round_ordinal: 1,
    submissions: overrides.submissions ?? V5_REVIEW_DIMENSIONS.map(dimension => ({
      dimension,
      state: "submitted",
      reviewed_set_digest: SET_DIGEST,
      enumerated_before_repair: true,
      finding_refs: [`finding:${dimension}-1`],
    })),
    ...Object.fromEntries(Object.entries(overrides).filter(([key]) => key !== "submissions")),
  };
}

function historyEntry(overrides = {}) {
  return {
    round_ordinal: 1,
    dimension: "architecture",
    reviewer_identity_ref: "actor:reviewer-architecture",
    state: "changes_required",
    finding_refs: ["finding:one"],
    resolved_finding_refs: [],
    post_repair_artifact_digest: "c".repeat(64),
    regression: { suite_ref: "suite:unit", checks_executed: ["alpha", "beta"] },
    ...overrides,
  };
}

function roundRequest(overrides = {}) {
  return {
    change_ref: CHANGE,
    delivered_set_digest: SET_DIGEST,
    requested_round_ordinal: 2,
    adjudication_recorded: false,
    history: overrides.history ?? [historyEntry()],
    ...Object.fromEntries(Object.entries(overrides).filter(([key]) => key !== "history")),
  };
}

function adjudicationRequest(overrides = {}) {
  return {
    change_ref: CHANGE,
    delivered_set_digest: SET_DIGEST,
    adjudicator_identity_ref: "actor:adjudicator",
    adjudicator_role: V5_ADJUDICATOR_ROLE,
    maker_identity_ref: "actor:builder",
    releaser_identity_ref: "actor:releaser",
    reviewer_identity_refs: ["actor:reviewer-architecture", "actor:reviewer-security"],
    disputed_finding_refs: ["finding:one"],
    rounds_completed: V5_MAX_REVIEW_ROUNDS,
    ...overrides,
  };
}

function receiptBody(overrides = {}) {
  return {
    kind: V5_ADJUDICATION_RECEIPT_KIND,
    change_ref: CHANGE,
    delivered_set_digest: SET_DIGEST,
    adjudicator_identity_ref: "actor:adjudicator",
    disputed_finding_refs: ["finding:one"],
    rounds_completed: V5_MAX_REVIEW_ROUNDS,
    outcome: "quarantine",
    ...overrides,
  };
}

function receiptRefFor(body) {
  return `adjudication-receipt:${digest(body)}`;
}

function receiptBinding(overrides = {}) {
  return {
    change_ref: CHANGE,
    delivered_set_digest: SET_DIGEST,
    party_identity_refs: ["actor:builder", "actor:releaser", "actor:reviewer-architecture"],
    ...overrides,
  };
}

/** The reason a result gave, plus the clause that produced it. */
function blocked(result) {
  return { reason_id: result.reason_id, blocking_check: result.blocking_check };
}

// ---------------------------------------------------------------------------
// The settled decisions and the policy identity.
// ---------------------------------------------------------------------------

test("binding: the five catalog decisions are carried verbatim with their evidence digests", () => {
  assert.deepEqual(V5_A03_DECISION_IDS, ["Q028.D1", "Q042.D1", "Q107.D1", "Q113.D1", "Q154.D1"]);
  assert.match(V5_A03_SETTLED_DECISIONS["Q042.D1"].settled_requirement,
    /at most two full rounds, then stronger adjudication and pass, fail, or quarantine/);
  assert.match(V5_A03_SETTLED_DECISIONS["Q107.D1"].settled_requirement,
    /no role may certify or weaken its own work/);
  for (const id of V5_A03_DECISION_IDS)
    assert.match(V5_A03_SETTLED_DECISIONS[id].source_evidence_digest, /^[0-9a-f]{64}$/);
});

test("binding: a caller whose decision subset drifted is refused in both directions", () => {
  const exactBinding = { decisions: Object.fromEntries(V5_A03_DECISION_IDS
    .map(id => [id, { ...V5_A03_SETTLED_DECISIONS[id] }])) };
  assert.equal(assertA03DecisionBinding(exactBinding), true);

  const missing = { decisions: { ...exactBinding.decisions } };
  delete missing.decisions["Q154.D1"];
  assert.throws(() => assertA03DecisionBinding(missing),
    error => error instanceof V5BoundaryError && error.code === "decision_binding_drift" &&
      error.detail.missing.includes("Q154.D1"));

  const extra = { decisions: { ...exactBinding.decisions, "Q999.D1": { settled_requirement: "x", source_evidence_digest: "0".repeat(64) } } };
  assert.throws(() => assertA03DecisionBinding(extra),
    error => error.detail.extra.includes("Q999.D1"));

  const reworded = { decisions: { ...exactBinding.decisions,
    "Q042.D1": { ...exactBinding.decisions["Q042.D1"], settled_requirement: "allow as many rounds as needed" } } };
  assert.throws(() => assertA03DecisionBinding(reworded),
    error => error.detail.decision_id === "Q042.D1");
});

test("policy: the digest is deterministic and moves when any closed vocabulary moves", () => {
  assert.equal(v5A03PolicyDigest(), v5A03PolicyDigest());
  assert.equal(v5A03PolicyCanonicalBytes(), canonicalJson(v5A03PolicyPreimage()));
  const preimage = v5A03PolicyPreimage();
  for (const key of ["review_dimensions", "opposing_role_pairs", "reason_ids", "routing_checks",
    "round_regression_classes", "adjudication_outcomes"]) {
    const moved = { ...preimage, [key]: [...preimage[key]].slice(1) };
    assert.notEqual(digest(moved), v5A03PolicyDigest(), `${key} is not in the policy preimage`);
  }
  assert.equal(preimage.max_review_rounds, 2);
  assert.notEqual(digest({ ...preimage, max_review_rounds: 3 }), v5A03PolicyDigest());
});

test("policy: the reason vocabulary is sorted, unique, and every reason is reachable", () => {
  assert.deepEqual([...V5_A03_REASON_IDS].sort(), [...V5_A03_REASON_IDS]);
  assert.equal(new Set(V5_A03_REASON_IDS).size, V5_A03_REASON_IDS.length);
});

test("policy: the eleven review dimensions are exactly Q154.D1's list", () => {
  assert.deepEqual([...V5_REVIEW_DIMENSIONS], ["architecture", "business", "context", "cost",
    "migration", "operations", "product", "repository", "resilience", "security", "sequencing"]);
  assert.equal(V5_REVIEW_DIMENSIONS.length, 11);
});

// ---------------------------------------------------------------------------
// Review routing — Q154.D1 and Q107.D1.
// ---------------------------------------------------------------------------

test("routing: a complete, separated, fresh routing still refuses — the registry does not exist", () => {
  const result = evaluateReviewRouting(routingRequest());
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "reviewer_identity_registry_unavailable");
  assert.equal(result.blocking_check, "reviewer_identity_registry");
  assert.equal(result.registry_bound, false);
  assert.equal(result.reviewers_entitled, false);
  assert.equal(result.state_holder_is_caller_supplied, false);
  assert.equal(result.reviewer_identity_registry_seam, V5_A03_SEAMS.reviewer_identity_registry);
  // The four deterministic clauses really ran and really passed; the refusal is
  // the missing seam alone, not a clause failing quietly behind it.
  assert.deepEqual(result.checks_satisfied,
    ["dimension_coverage", "reviewer_role_separation", "duty_role_separation", "fresh_context"]);
  assert.deepEqual(result.checks_not_reached, []);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(result.effects.creates_effect, false);
});

test("routing: a dimension nobody reviewed is named, and later clauses are not reached", () => {
  const assignments = routingRequest().assignments.filter(a => a.dimension !== "security");
  const result = evaluateReviewRouting(routingRequest({ assignments }));
  assert.deepEqual(blocked(result),
    { reason_id: "review_dimension_coverage_incomplete", blocking_check: "dimension_coverage" });
  assert.deepEqual(result.check_states.dimension_coverage.detail.missing, ["security"]);
  assert.deepEqual(result.checks_not_reached,
    ["reviewer_role_separation", "duty_role_separation", "fresh_context", "reviewer_identity_registry"]);
  assert.equal(result.check_states.reviewer_identity_registry.state, "not_reached");
});

test("routing: reviewing one dimension twice is not reviewing eleven", () => {
  const assignments = routingRequest().assignments
    .filter(a => a.dimension !== "cost")
    .concat([{ dimension: "security", reviewer_identity_ref: "actor:reviewer-security-2",
      reviewer_session_ref: "session:review-security-2", context_binding: "fresh" }]);
  const result = evaluateReviewRouting(routingRequest({ assignments }));
  assert.equal(result.blocking_check, "dimension_coverage");
  assert.deepEqual(result.check_states.dimension_coverage.detail.missing, ["cost"]);
  assert.deepEqual(result.check_states.dimension_coverage.detail.duplicated, ["security"]);
});

test("routing: the maker may not review its own work, in any dimension", () => {
  const assignments = routingRequest().assignments.map(a => (a.dimension === "repository"
    ? { ...a, reviewer_identity_ref: ROLE_IDENTITIES.builder } : a));
  const result = evaluateReviewRouting(routingRequest({ assignments }));
  assert.deepEqual(blocked(result),
    { reason_id: "reviewer_not_role_separated", blocking_check: "reviewer_role_separation" });
  assert.deepEqual(result.check_states.reviewer_role_separation.detail.collisions,
    [{ pair: ["builder", "reviewer"], identity_ref: "actor:builder" }]);
});

test("routing: the architect may not certify its own design", () => {
  const assignments = routingRequest().assignments.map(a => (a.dimension === "architecture"
    ? { ...a, reviewer_identity_ref: ROLE_IDENTITIES.architect } : a));
  const result = evaluateReviewRouting(routingRequest({ assignments }));
  assert.equal(result.reason_id, "reviewer_not_role_separated");
  assert.deepEqual(result.check_states.reviewer_role_separation.detail.collisions[0].pair,
    ["architect", "reviewer"]);
});

test("routing: the releaser may not be a reviewer — the checkable_done clause verbatim", () => {
  const assignments = routingRequest().assignments.map(a => (a.dimension === "operations"
    ? { ...a, reviewer_identity_ref: ROLE_IDENTITIES.deployment_controller } : a));
  const result = evaluateReviewRouting(routingRequest({ assignments }));
  assert.equal(result.reason_id, "reviewer_not_role_separated");
  assert.deepEqual(result.check_states.reviewer_role_separation.detail.collisions[0].pair,
    ["deployment_controller", "reviewer"]);
});

test("routing: one identity holding two opposing duties is caught duty-to-duty", () => {
  const result = evaluateReviewRouting(routingRequest({
    role_identities: { adjudicator: ROLE_IDENTITIES.builder } }));
  assert.deepEqual(blocked(result),
    { reason_id: "duties_not_role_separated", blocking_check: "duty_role_separation" });
  assert.deepEqual(result.check_states.duty_role_separation.detail.collisions,
    [{ pair: ["adjudicator", "builder"], identity_ref: "actor:builder" }]);
});

test("routing: the builder may not verify its own merge or promote its own artifact", () => {
  for (const role of ["integration_controller", "deployment_controller", "program_controller"]) {
    const result = evaluateReviewRouting(routingRequest({
      role_identities: { [role]: ROLE_IDENTITIES.builder } }));
    assert.equal(result.reason_id, "duties_not_role_separated", role);
    assert.deepEqual(result.check_states.duty_role_separation.detail.collisions[0].pair,
      ["builder", role].sort(), role);
  }
});

test("routing: the separation matrix is the accepted one, not 'everything must differ'", () => {
  // Q107.D1's recommendation is explicit that a model may hold several roles;
  // only OPPOSING ones are refused. These two pairs are deliberately absent
  // from the matrix, so an honest routing that shares them must not refuse.
  for (const shared of [
    { architect: ROLE_IDENTITIES.builder },
    { deployment_controller: ROLE_IDENTITIES.integration_controller },
  ]) {
    const result = evaluateReviewRouting(routingRequest({ role_identities: shared }));
    assert.equal(result.blocking_check, "reviewer_identity_registry",
      `${JSON.stringify(shared)} is not an opposing pair and must not refuse on separation`);
  }
});

test("routing: a reviewer sitting in the maker's session is not a second opinion", () => {
  const assignments = routingRequest().assignments.map(a => (a.dimension === "context"
    ? { ...a, reviewer_session_ref: "session:maker" } : a));
  const result = evaluateReviewRouting(routingRequest({ assignments }));
  assert.deepEqual(blocked(result),
    { reason_id: "review_context_not_fresh", blocking_check: "fresh_context" });
  assert.deepEqual(result.check_states.fresh_context.detail.dimensions, ["context"]);
});

test("routing: a context inherited from the maker is refused even from a new session", () => {
  const assignments = routingRequest().assignments.map(a => (a.dimension === "cost"
    ? { ...a, context_binding: "inherited_from_maker" } : a));
  const result = evaluateReviewRouting(routingRequest({ assignments }));
  assert.equal(result.reason_id, "review_context_not_fresh");
  assert.deepEqual(result.check_states.fresh_context.detail.dimensions, ["cost"]);
});

test("routing: an unreadable request fails closed rather than deciding", () => {
  assert.throws(() => evaluateReviewRouting({ ...routingRequest(), extra: 1 }),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
  const missingRole = routingRequest();
  delete missingRole.role_identities.program_controller;
  assert.throws(() => evaluateReviewRouting(missingRole),
    error => error.code === "missing_field");
  assert.throws(() => evaluateReviewRouting(routingRequest({
    assignments: [{ dimension: "governance", reviewer_identity_ref: "actor:x",
      reviewer_session_ref: "session:x", context_binding: "fresh" }] })),
  error => error.code === "unknown_enum_member");
  assert.throws(() => evaluateReviewRouting(routingRequest({ delivered_set_digest: "short" })),
    error => error.code === "malformed_digest");
});

// ---------------------------------------------------------------------------
// Complete finding set — Q113.D1 and Q154.D1.
// ---------------------------------------------------------------------------

test("finding set: a complete, whole-set, pre-repair enumeration still refuses", () => {
  const result = evaluateFindingSetCompleteness(findingSetRequest());
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "complete_finding_set_registry_unavailable");
  assert.equal(result.blocking_check, "complete_finding_set_registry");
  assert.equal(result.batch_repair_admitted, false);
  assert.equal(result.registry_bound, false);
  assert.equal(result.finding_registry_seam, V5_A03_SEAMS.complete_finding_set_registry);
  assert.deepEqual(result.checks_satisfied,
    ["dimension_submission", "complete_set_scope", "enumeration_before_repair"]);
});

test("finding set: a dimension that reported nothing is a hole, not a clean review", () => {
  const submissions = findingSetRequest().submissions.map(s => (s.dimension === "resilience"
    ? { ...s, state: "absent", finding_refs: [] } : s));
  const result = evaluateFindingSetCompleteness(findingSetRequest({ submissions }));
  assert.deepEqual(blocked(result),
    { reason_id: "finding_set_dimension_absent", blocking_check: "dimension_submission" });
  assert.deepEqual(result.check_states.dimension_submission.detail.absent, ["resilience"]);
  assert.equal(result.dimensions_submitted.includes("resilience"), false);
});

test("finding set: a reviewer who read a narrower set produced a narrower review", () => {
  // The clause this whole slice turns on. A subset review reads as complete and
  // is not, and no count of findings reveals it — so the digest does.
  const submissions = findingSetRequest().submissions.map(s => (s.dimension === "security"
    ? { ...s, reviewed_set_digest: OTHER_SET_DIGEST } : s));
  const result = evaluateFindingSetCompleteness(findingSetRequest({ submissions }));
  assert.deepEqual(blocked(result),
    { reason_id: "review_scope_narrower_than_delivered_set", blocking_check: "complete_set_scope" });
  assert.deepEqual(result.check_states.complete_set_scope.detail.dimensions, ["security"]);
});

test("finding set: findings enumerated after repair began are the spiral Q042 refuses", () => {
  const submissions = findingSetRequest().submissions.map(s => (s.dimension === "product"
    ? { ...s, enumerated_before_repair: false } : s));
  const result = evaluateFindingSetCompleteness(findingSetRequest({ submissions }));
  assert.deepEqual(blocked(result),
    { reason_id: "finding_set_enumerated_after_repair", blocking_check: "enumeration_before_repair" });
  assert.deepEqual(result.check_states.enumeration_before_repair.detail.dimensions, ["product"]);
});

test("finding set: a missing dimension and a duplicate are both reported by the same clause", () => {
  const base = findingSetRequest().submissions;
  const submissions = base.filter(s => s.dimension !== "migration")
    .concat([{ ...base[0] }]);
  const result = evaluateFindingSetCompleteness(findingSetRequest({ submissions }));
  assert.equal(result.blocking_check, "dimension_submission");
  assert.deepEqual(result.check_states.dimension_submission.detail.missing, ["migration"]);
  assert.deepEqual(result.check_states.dimension_submission.detail.duplicated, ["architecture"]);
});

test("finding set: an unsorted or repeating finding list is a shape error, never sorted for you", () => {
  const submissions = findingSetRequest().submissions.map(s => (s.dimension === "business"
    ? { ...s, finding_refs: ["finding:b", "finding:a"] } : s));
  assert.throws(() => evaluateFindingSetCompleteness(findingSetRequest({ submissions })),
    error => error instanceof V5BoundaryError && error.code === "unsorted_list");
  const repeated = findingSetRequest().submissions.map(s => (s.dimension === "business"
    ? { ...s, finding_refs: ["finding:a", "finding:a"] } : s));
  assert.throws(() => evaluateFindingSetCompleteness(findingSetRequest({ submissions: repeated })),
    error => error.code === "duplicate_member");
});

// ---------------------------------------------------------------------------
// The round bound — Q042.D1.
// ---------------------------------------------------------------------------

test("round: a third round cannot silently continue", () => {
  const result = evaluateReviewRoundAdmission(roundRequest({
    requested_round_ordinal: 3,
    history: [historyEntry(), historyEntry({ round_ordinal: 2, state: "changes_required",
      post_repair_artifact_digest: "d".repeat(64) })],
  }));
  assert.deepEqual(blocked(result),
    { reason_id: "review_round_limit_exhausted", blocking_check: "round_limit" });
  assert.equal(result.within_round_limit, false);
  assert.equal(result.required_transition, "stronger_adjudication");
  assert.equal(result.round_admitted, false);
  assert.equal(result.round_limit, 2);
});

test("round: a second round with a clean history still refuses — there is no ledger", () => {
  const result = evaluateReviewRoundAdmission(roundRequest());
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "review_round_ledger_unavailable");
  assert.equal(result.blocking_check, "review_round_ledger");
  assert.equal(result.ledger_bound, false);
  assert.equal(result.round_admitted, false);
  assert.equal(result.within_round_limit, true);
  assert.equal(result.required_transition, "independent_review_round");
});

test("round: a dispute adjudication closed does not reopen as another round", () => {
  const result = evaluateReviewRoundAdmission(roundRequest({ adjudication_recorded: true }));
  assert.deepEqual(blocked(result), {
    reason_id: "review_round_reopened_after_adjudication",
    blocking_check: "round_reopened_after_adjudication",
  });
});

test("round: round two without round one's batch repair and regression is refused", () => {
  const result = evaluateReviewRoundAdmission(roundRequest({ history: [] }));
  assert.deepEqual(blocked(result), {
    reason_id: "prior_round_batch_regression_absent",
    blocking_check: "prior_round_batch_regression",
  });
  assert.deepEqual(result.check_states.prior_round_batch_regression.detail.missing_rounds, [1]);
});

test("round: a finding marked resolved and reported again is a repair that did not take", () => {
  const history = [
    historyEntry({ round_ordinal: 1, finding_refs: ["finding:one"], resolved_finding_refs: ["finding:one"] }),
    historyEntry({ round_ordinal: 2, finding_refs: ["finding:one"] }),
  ];
  const result = evaluateReviewRoundAdmission(roundRequest({ requested_round_ordinal: 2, history }));
  assert.deepEqual(blocked(result),
    { reason_id: "review_round_drift_detected", blocking_check: "round_drift" });
  assert.deepEqual(result.check_states.round_drift.detail.fired, ["repeated_finding"]);
  assert.deepEqual(result.drift_detected.repeated_finding, ["finding:one"]);
});

test("round: a repair that restores an already-rejected tree is going round, not forward", () => {
  const reverted = "e".repeat(64);
  const history = [
    historyEntry({ round_ordinal: 1, state: "changes_required", post_repair_artifact_digest: reverted }),
    historyEntry({ round_ordinal: 2, state: "passed", post_repair_artifact_digest: reverted }),
  ];
  const result = evaluateReviewRoundAdmission(roundRequest({ requested_round_ordinal: 2, history }));
  assert.equal(result.reason_id, "review_round_drift_detected");
  assert.deepEqual(result.drift_detected.circular_reversion, [reverted]);
});

test("round: a reviewer giving both answers for one dimension is unstable, not decisive", () => {
  const history = [
    historyEntry({ round_ordinal: 1, state: "changes_required" }),
    historyEntry({ round_ordinal: 2, state: "passed", post_repair_artifact_digest: "f".repeat(64) }),
  ];
  const result = evaluateReviewRoundAdmission(roundRequest({ requested_round_ordinal: 2, history }));
  assert.equal(result.reason_id, "review_round_drift_detected");
  assert.deepEqual(result.drift_detected.reviewer_instability,
    ["actor:reviewer-architecture|architecture"]);
});

test("round: a regression that stopped running a check is a fix that weakened the test", () => {
  const history = [
    historyEntry({ round_ordinal: 1, reviewer_identity_ref: "actor:reviewer-a" }),
    historyEntry({ round_ordinal: 2, reviewer_identity_ref: "actor:reviewer-b",
      post_repair_artifact_digest: "f".repeat(64),
      regression: { suite_ref: "suite:unit", checks_executed: ["alpha"] } }),
  ];
  const result = evaluateReviewRoundAdmission(roundRequest({ requested_round_ordinal: 2, history }));
  assert.equal(result.reason_id, "review_round_drift_detected");
  assert.deepEqual(result.drift_detected.test_weakening, ["suite:unit|beta"]);
});

test("round: the obligation at each ordinal is the accepted bound, and nothing else", () => {
  assert.deepEqual(reviewRoundObligation(1), { ordinal: 1, round_limit: 2, within_round_limit: true,
    required_transition: "independent_review_round" });
  assert.deepEqual(reviewRoundObligation(2), { ordinal: 2, round_limit: 2, within_round_limit: true,
    required_transition: "independent_review_round" });
  assert.deepEqual(reviewRoundObligation(3), { ordinal: 3, round_limit: 2, within_round_limit: false,
    required_transition: "stronger_adjudication" });
  assert.equal(reviewRoundObligation(0).within_round_limit, false);
});

test("round: the four drift detectors are silent on a clean history", () => {
  const clean = detectRoundRegressions([historyEntry()]);
  for (const name of V5_ROUND_REGRESSION_CLASSES) assert.deepEqual(clean[name], [], name);
});

// ---------------------------------------------------------------------------
// Bounded adjudication — Q042.D1 step 4 and Q028.D1.
// ---------------------------------------------------------------------------

test("adjudication: a well-formed dispute still refuses, and no outcome is ever returned", () => {
  const result = evaluateAdjudication(adjudicationRequest());
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "bounded_adjudication_receipt_store_unavailable");
  assert.equal(result.blocking_check, "bounded_adjudication_receipt_store");
  assert.equal(result.adjudicated_outcome, null);
  assert.equal(result.disposition_recorded, false);
  assert.equal(result.outcome_is_caller_stated, false);
  assert.equal(result.store_bound, false);
  assert.equal(result.adjudication_receipt_store_seam, V5_A03_SEAMS.bounded_adjudication_receipt_store);
  assert.deepEqual(result.checks_satisfied,
    ["adjudicator_role", "adjudicator_separation", "rounds_before_adjudication", "disputed_set_empty"]);
});

test("adjudication: the adjudicator may not be anyone in the dispute", () => {
  for (const [field, role] of [["maker_identity_ref", "builder"],
    ["releaser_identity_ref", "deployment_controller"]]) {
    const request = adjudicationRequest();
    const result = evaluateAdjudication({ ...request, adjudicator_identity_ref: request[field] });
    assert.deepEqual(blocked(result), {
      reason_id: "adjudicator_is_a_party_to_the_dispute", blocking_check: "adjudicator_separation",
    }, field);
    assert.deepEqual(result.check_states.adjudicator_separation.detail.also_a_party_as, [role], field);
  }
  const asReviewer = evaluateAdjudication(adjudicationRequest({
    adjudicator_identity_ref: "actor:reviewer-security" }));
  assert.equal(asReviewer.reason_id, "adjudicator_is_a_party_to_the_dispute");
  assert.deepEqual(asReviewer.check_states.adjudicator_separation.detail.also_a_party_as, ["reviewer"]);
});

test("adjudication: only the accepted stronger-adjudicator role adjudicates", () => {
  const result = evaluateAdjudication(adjudicationRequest({ adjudicator_role: "peer_reviewer" }));
  assert.deepEqual(blocked(result),
    { reason_id: "adjudicator_role_unknown", blocking_check: "adjudicator_role" });
  assert.equal(result.adjudicator_role_required, "stronger_adjudicator");
});

test("adjudication: reaching for a judge before the review bound is spent is refused", () => {
  const result = evaluateAdjudication(adjudicationRequest({ rounds_completed: 1 }));
  assert.deepEqual(blocked(result),
    { reason_id: "adjudication_before_round_limit", blocking_check: "rounds_before_adjudication" });
  assert.equal(result.rounds_completed, 1);
});

test("adjudication: there is nothing to adjudicate without a named disagreement", () => {
  const result = evaluateAdjudication(adjudicationRequest({ disputed_finding_refs: [] }));
  assert.deepEqual(blocked(result),
    { reason_id: "adjudication_disputed_set_empty", blocking_check: "disputed_set_empty" });
  assert.equal(result.disputed_finding_count, 0);
});

// ---------------------------------------------------------------------------
// The receipt verifier.
// ---------------------------------------------------------------------------

test("receipt: a well-formed receipt verifies, and its outcome is deliberately not echoed", () => {
  const body = receiptBody();
  const result = verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND,
    receiptRefFor(body), body, receiptBinding());
  assert.equal(result.ok, true);
  assert.equal(result.reason_id, null);
  // The whole point: verifying the bytes is not reading the verdict.
  assert.equal(JSON.stringify(result).includes("quarantine"), false);
  assert.equal("outcome" in result, false);
  assert.equal("body" in result, false);
});

test("receipt: every way a forged or mismatched receipt fails is named", () => {
  const body = receiptBody();
  const ref = receiptRefFor(body);
  const binding = receiptBinding();
  const cases = [
    ["adjudication_receipt_not_content_addressed", "adjudication-receipt:named-not-hashed", body],
    ["adjudication_receipt_unresolvable", ref, null],
    ["adjudication_receipt_digest_mismatch", ref, receiptBody({ rounds_completed: 5 })],
  ];
  for (const [reasonId, receiptRef, receiptBodyValue] of cases) {
    const result = verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRef,
      receiptBodyValue, binding);
    assert.equal(result.ok, false, reasonId);
    assert.equal(result.reason_id, reasonId);
  }

  const wrongKind = receiptBody({ kind: "review" });
  assert.equal(verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRefFor(wrongKind),
    wrongKind, binding).reason_id, "adjudication_receipt_kind_mismatch");

  const otherChange = receiptBody({ change_ref: "change:something-else" });
  assert.equal(verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRefFor(otherChange),
    otherChange, binding).reason_id, "adjudication_receipt_bound_to_other_change");

  const otherSet = receiptBody({ delivered_set_digest: OTHER_SET_DIGEST });
  assert.equal(verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRefFor(otherSet),
    otherSet, binding).reason_id, "adjudication_receipt_bound_to_other_delivered_set");

  const openOutcome = receiptBody({ outcome: "needs_more_thought" });
  assert.equal(verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRefFor(openOutcome),
    openOutcome, binding).reason_id, "adjudication_receipt_outcome_unknown");

  const selfSigned = receiptBody({ adjudicator_identity_ref: "actor:builder" });
  assert.equal(verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRefFor(selfSigned),
    selfSigned, binding).reason_id, "adjudication_receipt_adjudicator_is_a_party");
});

test("receipt: the outcome vocabulary is closed to pass, fail and quarantine", () => {
  assert.deepEqual([...V5_ADJUDICATION_OUTCOMES], ["fail", "pass", "quarantine"]);
  for (const outcome of V5_ADJUDICATION_OUTCOMES) {
    const body = receiptBody({ outcome });
    assert.equal(verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRefFor(body),
      body, receiptBinding()).ok, true, outcome);
  }
});

// ---------------------------------------------------------------------------
// GUARDS. A caller-supplied label, fixture or holder is never authority.
// ---------------------------------------------------------------------------

/** Every request shape a caller controls, in its honest form. */
const EVALUATOR_MATRIX = [
  ["evaluateReviewRouting", evaluateReviewRouting, routingRequest],
  ["evaluateFindingSetCompleteness", evaluateFindingSetCompleteness, findingSetRequest],
  ["evaluateReviewRoundAdmission", evaluateReviewRoundAdmission, roundRequest],
  ["evaluateAdjudication", evaluateAdjudication, adjudicationRequest],
];

const PRIVILEGED_VALUES = new Set(["allow", "allowed", "approved", "commit", "complete", "completed",
  "covered", "drafted", "healthy", "independent", "pass", "passed", "passing", "proposed", "prompt",
  "queued", "read", "release", "released", "suppress"]);

function stringValues(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) value.forEach(item => stringValues(item, out));
  else if (value !== null && typeof value === "object")
    Object.values(value).forEach(item => stringValues(item, out));
  return out;
}

test("guard: no evaluator returns a privileged outcome, on any caller-controlled input", () => {
  // The inputs below include every shape the clause tests above drive through,
  // plus the honest one. None of them contains a privileged token, so any
  // privileged word in a result would be one the MODULE produced.
  const inputs = [
    evaluateReviewRouting(routingRequest()),
    evaluateReviewRouting(routingRequest({ role_identities: { adjudicator: ROLE_IDENTITIES.builder } })),
    evaluateReviewRouting(routingRequest({ assignments: [] })),
    evaluateFindingSetCompleteness(findingSetRequest()),
    evaluateFindingSetCompleteness(findingSetRequest({ submissions: [] })),
    evaluateReviewRoundAdmission(roundRequest()),
    evaluateReviewRoundAdmission(roundRequest({ requested_round_ordinal: 9 })),
    evaluateAdjudication(adjudicationRequest()),
    evaluateAdjudication(adjudicationRequest({ rounds_completed: 0 })),
  ];
  for (const result of inputs) {
    for (const value of stringValues(result))
      assert.equal(PRIVILEGED_VALUES.has(value), false,
        `${result.answer} returned the privileged value ${JSON.stringify(value)}`);
    assert.equal(result.decision, "refuse");
    assert.equal(result.model_judgment_admitted, false);
    assert.equal(result.state_holder_is_caller_supplied, false);
    assert.equal(result.performs_adjudication, false);
    assert.equal(Object.isFrozen(result), true);
  }
});

test("guard: stuffing privileged labels into every field does not produce a privileged decision", () => {
  // A caller that calls itself "pass" gets its own word echoed back inside the
  // refusal detail, and NOT in any field a consumer decides from.
  const DECISION_KEYS = ["decision", "reason_id", "adjudicated_outcome", "required_transition",
    "blocking_check", "answer"];
  const stuffed = [
    evaluateReviewRouting(routingRequest({
      role_identities: { builder: "actor:pass", architect: "actor:allow" },
      maker_session_ref: "session:complete" })),
    evaluateAdjudication(adjudicationRequest({ adjudicator_role: "pass",
      adjudicator_identity_ref: "actor:allow" })),
    evaluateReviewRoundAdmission(roundRequest({ history: [historyEntry({
      reviewer_identity_ref: "actor:independent" })] })),
  ];
  for (const result of stuffed) {
    for (const key of DECISION_KEYS)
      if (key in result)
        assert.equal(PRIVILEGED_VALUES.has(result[key]), false,
          `${result.answer}.${key} came back privileged`);
    assert.equal(result.decision, "refuse");
    assert.equal(result.adjudicated_outcome ?? null, null);
  }
  for (const flag of ["reviewers_entitled", "batch_repair_admitted", "round_admitted",
    "disposition_recorded", "registry_bound", "ledger_bound", "store_bound"]) {
    for (const result of stuffed) if (flag in result) assert.equal(result[flag], false, flag);
  }
});

test("guard: no evaluator accepts a store, holder, registry or ledger as a second argument", () => {
  const forged = { resolveReviewerEntitlement: () => true, resolveFindingSet: () => true,
    resolveRoundHistory: () => [], resolveReceipt: () => receiptBody() };
  for (const [name, evaluator, fixture] of EVALUATOR_MATRIX) {
    assert.throws(() => evaluator(fixture(), forged),
      error => error instanceof V5BoundaryError && error.code.endsWith("_is_not_an_argument"),
      `${name} accepted a caller-supplied holder`);
  }
});

test("guard: the public surface exports no classifier and no conditional classification", () => {
  const exported = Object.keys(a03).sort();
  for (const name of exported) {
    assert.equal(name.startsWith("classify"), false, `${name} is a classifier on the public surface`);
    assert.equal(name.includes("Internal"), false, name);
  }
  assert.equal(exported.includes("V5_A03_CLASSIFICATIONS"), false);
  // And the classifications themselves are conditional by construction.
  for (const value of Object.values(V5_A03_CLASSIFICATIONS))
    assert.equal(value === "would_refuse" || value.endsWith("_if_authoritative"), true, value);
});

test("guard: the internal classifiers answer only in the conditional", () => {
  const clean = [
    classifyRoutingIfAuthoritative(routingRequest()),
    classifyFindingSetIfAuthoritative(findingSetRequest()),
    classifyRoundIfAuthoritative(roundRequest()),
    classifyAdjudicationIfAuthoritative(adjudicationRequest()),
  ];
  const expected = [V5_A03_CLASSIFICATIONS.routing, V5_A03_CLASSIFICATIONS.findingSet,
    V5_A03_CLASSIFICATIONS.round, V5_A03_CLASSIFICATIONS.adjudication];
  clean.forEach((result, index) => {
    assert.equal(result.classification, expected[index]);
    assert.equal(result.blocking_clause, null);
    for (const value of stringValues(result))
      assert.equal(PRIVILEGED_VALUES.has(value), false, value);
  });
});

test("guard: the internal classifier is unreachable through the public surface — parsed, not grepped", () => {
  // Node's own ES-module parser, via vm.SourceTextModule, which exposes the
  // real import specifiers of each source file. A regex over the text would be
  // fooled by a comment, a string or an unusual line break; this is the same
  // parse the runtime performs.
  const script = `
    import { readdirSync, readFileSync, statSync } from "node:fs";
    import path from "node:path";
    import vm from "node:vm";
    const root = process.env.A03_REPO_ROOT;
    const internal = "complete-set-review-a03.internal.v5.js";
    const roots = ["mcp-server/src", "mcp-server/test", "control-room", "workspace", "tools"];
    const files = [];
    const walk = dir => {
      let entries;
      try { entries = readdirSync(dir); } catch { return; }
      for (const entry of entries) {
        if (entry === "node_modules" || entry === ".git") continue;
        const full = path.join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\\.(mjs|js)$/.test(entry)) files.push(full);
      }
    };
    for (const dir of roots) walk(path.join(root, dir));
    const importers = [];
    let parsed = 0;
    for (const file of files) {
      let record;
      try { record = new vm.SourceTextModule(readFileSync(file, "utf8"), { identifier: file }); }
      catch { continue; }
      parsed += 1;
      if (record.dependencySpecifiers.some(spec => spec.endsWith(internal)))
        importers.push(path.relative(root, file));
    }
    console.log(JSON.stringify({ importers: importers.sort(), parsed }));
  `;
  const output = execFileSync(process.execPath,
    ["--experimental-vm-modules", "--input-type=module", "--eval", script],
    { env: { ...process.env, A03_REPO_ROOT: REPO_ROOT }, encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"] });
  const { importers, parsed } = JSON.parse(output.trim().split("\n").pop());

  // The sweep is only worth anything if it actually parsed the tree.
  assert.equal(parsed > 100, true, `only ${parsed} modules parsed`);
  // Exactly two importers: the public module (which does not re-export the
  // classifiers) and this test file. Anything else would be a consumer reaching
  // past the public surface for the conditional answer.
  assert.deepEqual(importers, [
    "mcp-server/src/complete-set-review-a03.v5.js",
    "mcp-server/test/complete-set-review-a03.v5.test.mjs",
  ]);
});

test("guard: the parsed export names of the public module carry no classifier", () => {
  // Same parser, now LINKED against the real files on disk rather than stubs,
  // so the export names come from Node resolving the module graph exactly as
  // the runtime would. No fallback: if the link fails, the guard fails.
  const script = `
    import { readFileSync } from "node:fs";
    import path from "node:path";
    import vm from "node:vm";
    const cache = new Map();
    const load = file => {
      const resolved = path.resolve(file);
      if (cache.has(resolved)) return cache.get(resolved);
      const record = new vm.SourceTextModule(readFileSync(resolved, "utf8"),
        { identifier: resolved, initializeImportMeta: meta => { meta.url = "file://" + resolved; } });
      cache.set(resolved, record);
      return record;
    };
    const linker = async (specifier, referencing) => {
      if (specifier.startsWith("node:") || !specifier.startsWith(".")) {
        const builtin = await import(specifier);
        const names = Object.keys(builtin);
        return new vm.SyntheticModule(names, function () {
          for (const name of names) this.setExport(name, builtin[name]);
        });
      }
      return load(path.resolve(path.dirname(referencing.identifier), specifier));
    };
    const entry = load(process.env.A03_PUBLIC_MODULE);
    await entry.link(linker);
    // Evaluated so the namespace's bindings are initialised; these modules are
    // pure, so evaluating them reads nothing and writes nothing.
    await entry.evaluate();
    console.log(JSON.stringify(Object.keys(entry.namespace).sort()));
  `;
  const output = execFileSync(process.execPath,
    ["--experimental-vm-modules", "--input-type=module", "--eval", script],
    { env: { ...process.env,
      A03_PUBLIC_MODULE: path.join(REPO_ROOT, "mcp-server/src/complete-set-review-a03.v5.js") },
    encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  const names = JSON.parse(output.trim().split("\n").pop());

  assert.deepEqual(names, Object.keys(a03).sort(), "the parsed surface is the runtime surface");
  for (const name of names) assert.equal(name.startsWith("classify"), false, name);
  assert.equal(names.includes("evaluateReviewRouting"), true);
  assert.equal(names.includes("V5_A03_CLASSIFICATIONS"), false);
});

test("guard: every reason this module may give is produced by something here", () => {
  const produced = new Set();
  const collect = result => { if (result?.reason_id) produced.add(result.reason_id); };
  const assignments = routingRequest().assignments;

  collect(evaluateReviewRouting(routingRequest()));
  collect(evaluateReviewRouting(routingRequest({ assignments: assignments.slice(1) })));
  collect(evaluateReviewRouting(routingRequest({
    assignments: assignments.map((a, i) => (i ? a : { ...a, reviewer_identity_ref: ROLE_IDENTITIES.builder })) })));
  collect(evaluateReviewRouting(routingRequest({ role_identities: { adjudicator: ROLE_IDENTITIES.builder } })));
  collect(evaluateReviewRouting(routingRequest({
    assignments: assignments.map((a, i) => (i ? a : { ...a, context_binding: "inherited_from_maker" })) })));

  const submissions = findingSetRequest().submissions;
  collect(evaluateFindingSetCompleteness(findingSetRequest()));
  collect(evaluateFindingSetCompleteness(findingSetRequest({ submissions: submissions.slice(1) })));
  collect(evaluateFindingSetCompleteness(findingSetRequest({
    submissions: submissions.map((s, i) => (i ? s : { ...s, reviewed_set_digest: OTHER_SET_DIGEST })) })));
  collect(evaluateFindingSetCompleteness(findingSetRequest({
    submissions: submissions.map((s, i) => (i ? s : { ...s, enumerated_before_repair: false })) })));

  collect(evaluateReviewRoundAdmission(roundRequest()));
  collect(evaluateReviewRoundAdmission(roundRequest({ requested_round_ordinal: 3 })));
  collect(evaluateReviewRoundAdmission(roundRequest({ adjudication_recorded: true })));
  collect(evaluateReviewRoundAdmission(roundRequest({ history: [] })));
  collect(evaluateReviewRoundAdmission(roundRequest({ history: [
    historyEntry({ finding_refs: ["finding:one"], resolved_finding_refs: ["finding:one"] }),
    historyEntry({ round_ordinal: 2, finding_refs: ["finding:one"],
      post_repair_artifact_digest: "9".repeat(64) })] })));

  collect(evaluateAdjudication(adjudicationRequest()));
  collect(evaluateAdjudication(adjudicationRequest({ adjudicator_role: "peer" })));
  collect(evaluateAdjudication(adjudicationRequest({ adjudicator_identity_ref: "actor:builder" })));
  collect(evaluateAdjudication(adjudicationRequest({ rounds_completed: 0 })));
  collect(evaluateAdjudication(adjudicationRequest({ disputed_finding_refs: [] })));

  const body = receiptBody();
  const binding = receiptBinding();
  for (const [receiptRef, receiptValue] of [
    ["adjudication-receipt:not-a-digest", body],
    [receiptRefFor(body), null],
    [receiptRefFor(body), receiptBody({ rounds_completed: 7 })],
    [receiptRefFor(receiptBody({ kind: "review" })), receiptBody({ kind: "review" })],
    [receiptRefFor(receiptBody({ change_ref: "change:other" })), receiptBody({ change_ref: "change:other" })],
    [receiptRefFor(receiptBody({ delivered_set_digest: OTHER_SET_DIGEST })),
      receiptBody({ delivered_set_digest: OTHER_SET_DIGEST })],
    [receiptRefFor(receiptBody({ outcome: "maybe" })), receiptBody({ outcome: "maybe" })],
    [receiptRefFor(receiptBody({ adjudicator_identity_ref: "actor:builder" })),
      receiptBody({ adjudicator_identity_ref: "actor:builder" })],
  ]) collect(verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRef, receiptValue, binding));

  assert.deepEqual([...produced].sort(), [...V5_A03_REASON_IDS],
    "a reason nothing can produce is a reason nobody can act on");
});

test("guard: every declared check order is exercised and every clause can block", () => {
  for (const order of [V5_ROUTING_CHECKS, V5_FINDING_SET_CHECKS, V5_ROUND_BOUND_CHECKS,
    V5_ADJUDICATION_CHECKS]) {
    assert.equal(new Set(order).size, order.length);
    // The holder clause is LAST in every order, so a caller whose own
    // description is self-inconsistent hears that instead of the missing seam.
    assert.match(order[order.length - 1], /_registry$|_ledger$|_store$/);
    assert.equal(order.slice(0, -1).some(check => /_registry$|_ledger$|_store$/.test(check)), false);
  }
  assert.deepEqual([...V5_NON_REVIEWER_ROLES].sort(), [...V5_NON_REVIEWER_ROLES]);
  assert.equal(V5_OPPOSING_ROLE_PAIRS.length, 9);
  for (const pair of V5_OPPOSING_ROLE_PAIRS) assert.deepEqual([...pair].sort(), [...pair]);
});

test("clause predicates: a gap report and a collision list are facts, not clearances", () => {
  assert.deepEqual(reviewDimensionGap([...V5_REVIEW_DIMENSIONS]), { missing: [], duplicated: [] });
  assert.deepEqual(reviewDimensionGap(["architecture", "architecture"]).duplicated, ["architecture"]);
  assert.equal(reviewDimensionGap([]).missing.length, 11);
  assert.deepEqual(roleSeparationCollisions(ROLE_IDENTITIES, ["actor:reviewer-x"]), []);
  assert.deepEqual(roleSeparationCollisions(ROLE_IDENTITIES, [ROLE_IDENTITIES.builder]),
    [{ pair: ["builder", "reviewer"], identity_ref: "actor:builder" }]);
  assert.equal(V5_A03_SCHEMA_VERSION, "doctorcre-v5-complete-set-review.v1");
});
