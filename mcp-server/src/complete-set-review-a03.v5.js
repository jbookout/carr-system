// DoctorCRE v5 slice V5-A03 — THE INDEPENDENT-REVIEW PUBLIC SURFACE, and it is
// a surface that cannot say yes about anything.
//
// WHAT THIS FILE MAY NOT DECIDE, said first because it is the whole point.
//
// (1) WHETHER A REVIEW IS INDEPENDENT. There is no reviewer identity registry
// in this repository. A caller naming a builder, an architect, a releaser and
// eleven reviewers is DESCRIBING a separation, not evidencing one, and nothing
// here can check that the identity the caller calls the builder is the identity
// that built the change. So identity separation is not established by
// construction, and no exported function reports it as established.
//
// (2) WHETHER A FINDING SET IS THE COMPLETE ONE. There is no finding registry.
// The nearest relative is `receipt.receipt.deviations` — the maker's own list of
// what is wrong with the maker's own work — which is exactly the shape this
// slice must never treat as authority.
//
// (3) WHICH ROUND THIS IS, OR WHETHER THE PRIOR ROUND WAS BATCH-REPAIRED AND
// FULLY REGRESSED. There is no round ledger. `ops.engineering_reviewer_fact`
// rows could be counted, but a count of reviewer facts is a count of REVIEWERS:
// a second independent reviewer of one unchanged receipt is indistinguishable
// from a second round after a repair, and Q042.D1's bound is on rounds.
//
// (4) HOW A DISPUTE ENDED. There is no adjudication receipt store.
// `adjudicate-incident` and `adjudicate-investigation-branch` settle incidents
// and investigation branches — different subjects, with no slice, round,
// reviewer or delivered set anywhere in them. Reusing one would be a second
// authority over a different thing wearing this slice's name. AND A RECEIPT A
// CALLER HANDS IN IS NOT A READING OF THAT STORE: an earlier version of this
// file verified a caller-supplied receipt body and returned `ok: true`, so a
// party could write "outcome: pass" about its own dispute, hash its own bytes,
// omit itself from the party list and be told the receipt was well-formed. That
// is the defect the V5-A03 review of PR 987 named, and the standing rule learned
// from nine review rounds on 2026-09-11 forbids it under ANY name — including a
// "predicate", "fixture", "internal" or "unwired" variant.
//
// SO THE PUBLIC SURFACE IS THIS, and it is the whole of it:
//
//   readReviewRoutingAdmission   -> status "unavailable", naming the registry owed
//   readFindingSetExhaustiveness -> status "unavailable", naming the registry owed
//   readReviewRoundAdmission     -> status "unavailable", naming the ledger owed
//   readBoundedAdjudication      -> status "unavailable", naming the store owed,
//                                   adjudicated_outcome null, forever
//   verifyAdjudicationReceipt    -> status "unavailable", naming the store owed.
//                                   It reports NO verification: a receipt is
//                                   resolved from the store or it is not
//                                   resolved at all.
//
// NONE OF THE FIVE READS ITS REQUEST. That is not laziness, it is the boundary:
// if no field of the request can change the answer, then no caller can smuggle
// authority in through one. `request_read: false` says so in every result, and
// the suite proves the answers are byte-identical across every caller shape.
//
// WHERE THE DETERMINISTIC CLAUSES LIVE. The clauses this slice's checkable_done
// names are implemented, proved clause by clause, and kept out of production
// entirely: they live in
// `mcp-server/test/complete-set-review-a03-classifiers.v5.testhelper.mjs`, which
// this file does not import, which no module under mcp-server/src may import,
// and which answers only in the conditional — `would_be_routable_if_
// authoritative`, `would_be_whole_set_scoped_if_authoritative`,
// `would_be_within_bound_if_authoritative`, `would_be_adjudicable_if_
// authoritative`, `would_be_verifiable_if_authoritative`. Node's own ES-module
// parser proves the isolation, and a sweep over every caller-controlled shape
// proves this surface never answers with a privileged word.
//
// NO PRIVILEGED WORD AND NO OUTCOME WORD LEAVES THIS SLICE AT ALL, BY ANY
// ROUTE. Not as an answer, not as a vocabulary, not inside a policy identity,
// and not inside a quoted settled sentence. The second review of PR 987 found
// three routes still open after the first correction — `V5_ADJUDICATION_OUTCOMES`
// carried the bare word `pass`, and `v5A03PolicyPreimage` and
// `v5A03PolicyCanonicalBytes` returned and serialized it — each defended as
// "only a vocabulary". The third review found the replacement still publishing
// the same three dispositions as `adverse`, `favorable` and `isolating`: a
// consumer reading `favorable` reads "pass", so renaming was not opacity. All
// four routes are closed. The outcome vocabulary is a module-private set of
// three ordinal codes HERE, no src module exports it under any name, only its
// canonical digest is public; the preimage and its canonical bytes are
// module-private; and the settled decision text is module-private with only its
// evidence digests exported. The suite proves it per word, for `ok`, `allow`,
// `pass`, `satisfied`, `complete`, `admitted`, `favorable`, `unfavorable`,
// `adverse`, `isolating`, `quarantine` and `fail`, over EVERY export of BOTH src
// modules in this slice — every exported value, every nested value and key, and
// every value returned for every caller-controlled shape — matching whole
// strings and substrings, with no exemption for any export.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — `decision` is "refuse" with a stable
//     `reason_id`. Silence is never an allow, and on this surface there is no
//     allow at all.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Handing a registry, ledger,
//     store or receipt body in as a second argument is not a policy question.
//
// THE FIVE SETTLED DECISIONS are carried in a MODULE-PRIVATE table with their
// source-evidence digests, read from doctrine document
// `doctorcre-v5-design-basis`, normalized r7 design chunks, reassembled and
// verified against the manifest's own artifact digest
// ef34aa54740dd56508b7cebf05a2a95851aacedbbe4f2e4865a39ffede28f0ad. They are
// identity, not configuration: a caller holding a different subset is refused by
// assertA03DecisionBinding, which throws or returns nothing and never hands back
// an affirmative.
// SUPERSEDED DIGEST, kept because it is the packet these rows were copied from:
// the 2026-09-12 Gate Zero amendment re-froze r7 to
// ea40f61a9081814e53c989f2f945c61b270597cdfeafc4ec535578e60462a8f6. It changed
// exactly one decision row, Q036.D1's consumer_gates, and none of the rows below.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_A03_POLICY_VERSION,
  V5_A03_SCHEMA_VERSION,
  V5_A03_SEAMS,
  V5_ADJUDICATION_CHECKS,
  V5_ADJUDICATION_RECEIPT_KIND,
  V5_ADJUDICATION_RECEIPT_STORE_SEAM,
  V5_ADJUDICATOR_ROLE,
  V5_CONTEXT_BINDINGS,
  V5_DETERMINISTIC_ONLY_DECISIONS,
  V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM,
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

// RE-EXPORTED VOCABULARIES, minus one. The adjudication outcome vocabulary is
// deliberately absent, here and from the vocabulary module both: it is the one
// vocabulary a consumer could read as a disposition, this surface can never
// produce a disposition, and a surface that hands out the words for an answer it
// cannot give is handing out half an answer. It is module-private below, and a
// future receipt store binds to `v5A03AdjudicationOutcomeVocabularyDigest()`.
export {
  V5_A03_POLICY_VERSION,
  V5_A03_SCHEMA_VERSION,
  V5_A03_SEAMS,
  V5_ADJUDICATION_CHECKS,
  V5_ADJUDICATION_RECEIPT_KIND,
  V5_ADJUDICATION_RECEIPT_STORE_SEAM,
  V5_ADJUDICATOR_ROLE,
  V5_CONTEXT_BINDINGS,
  V5_DETERMINISTIC_ONLY_DECISIONS,
  V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM,
  V5_FINDING_SET_CHECKS,
  V5_MAX_REVIEW_ROUNDS,
  V5_MODEL_PERMITTED_ROLES,
  V5_NON_REVIEWER_ROLES,
  V5_NO_EFFECTS,
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
// The five settled decisions, carried verbatim with their source-evidence
// digests. Text is identity here, not configuration.
//
// THE TABLE IS MODULE-PRIVATE, and this slice is the one place in v5 where that
// is right. Its sibling slices export their settled text, and for them the cost
// is nothing. Here the settled sentences contain "pass, fail, or quarantine",
// "allow at most two full rounds" and "the complete finding set" — so exporting
// them would put three privileged words into a payload a consumer can read,
// out of the one module whose entire contract is that it never hands a consumer
// a word it could act on. The text is not lost: it is right here, and its
// authority was never this module in the first place — it is doctrine document
// `doctorcre-v5-design-basis`, which is where a caller building a binding gets
// it. What this module exports is the DIGEST of each decision's source
// evidence, which is what a consumer can check a binding against without ever
// reading a privileged word out of a payload.
// ---------------------------------------------------------------------------

const A03_SETTLED_DECISIONS = deepFreeze({
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

export const V5_A03_DECISION_IDS = deepFreeze(Object.keys(A03_SETTLED_DECISIONS).sort());

/**
 * The source-evidence digest of each settled decision, and nothing else.
 *
 * This is the public half of the binding: 64 hex characters per decision, which
 * a consumer can compare against its own copy of doctrine without this module
 * ever putting a settled sentence — and the privileged words inside one — into a
 * payload. The sentences themselves are checked by assertA03DecisionBinding
 * against the module-private table above.
 */
export const V5_A03_SETTLED_DECISION_DIGESTS = deepFreeze(Object.fromEntries(
  Object.keys(A03_SETTLED_DECISIONS).sort()
    .map(id => [id, A03_SETTLED_DECISIONS[id].source_evidence_digest])));

/**
 * Refuse a caller whose decision subset has drifted from the reviewed five.
 *
 * IT RETURNS NOTHING. Drift throws; agreement is silence. An affirmative return
 * value here would be one more thing a consumer could mistake for a clearance,
 * and this module hands out none.
 */
export function assertA03DecisionBinding(binding) {
  if (!isPlainObject(binding)) fail("not_an_object", "binding must be a plain object", { path: "binding" });
  const allowed = ["decisions"];
  for (const key of Object.keys(binding))
    if (!allowed.includes(key))
      fail("unknown_field", `binding.${key} is not a field this module reads`, { key });
  if (!Object.hasOwn(binding, "decisions"))
    fail("missing_field", "binding.decisions is required", { key: "decisions" });
  if (!isPlainObject(binding.decisions))
    fail("not_an_object", "binding.decisions must be a plain object", { path: "binding.decisions" });

  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_A03_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_A03_DECISION_IDS.includes(id));
  if (missing.length || extra.length)
    fail("decision_binding_drift", "the supplied decision set is not the reviewed five", { missing, extra });
  for (const id of V5_A03_DECISION_IDS) {
    const entry = binding.decisions[id];
    const expected = A03_SETTLED_DECISIONS[id];
    if (!isPlainObject(entry))
      fail("not_an_object", `binding.decisions.${id} must be a plain object`, { decision_id: id });
    const fields = ["settled_requirement", "source_evidence_digest"];
    for (const key of Object.keys(entry))
      if (!fields.includes(key))
        fail("unknown_field", `binding.decisions.${id}.${key} is not a field this module reads`,
          { decision_id: id, key });
    for (const key of fields)
      if (!Object.hasOwn(entry, key))
        fail("missing_field", `binding.decisions.${id}.${key} is required`, { decision_id: id, key });
    if (entry.settled_requirement !== expected.settled_requirement ||
        entry.source_evidence_digest !== expected.source_evidence_digest)
      fail("decision_binding_drift", `binding.decisions.${id} is not the reviewed decision`,
        { decision_id: id });
  }
}

// ---------------------------------------------------------------------------
// The reason vocabulary. Closed and C-sorted. The four seam reasons are the
// ones THIS surface gives; the rest are the clause reasons the test-only
// classifiers cite, registered here so there is exactly one registry.
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
  "duties_not_role_separated",
  "exhaustive_finding_set_registry_unavailable",
  "finding_set_dimension_absent",
  "finding_set_enumerated_after_repair",
  "prior_round_batch_regression_unreadable",
  "review_context_not_fresh",
  "review_dimension_coverage_short_of_closed_set",
  "review_round_drift_detected",
  "review_round_ledger_unavailable",
  "review_round_limit_exhausted",
  "review_round_reopened_after_adjudication",
  "review_scope_narrower_than_delivered_set",
  "reviewer_identity_registry_unavailable",
  "reviewer_not_role_separated",
]);

/** The four reasons the PUBLIC surface may give. Every one names a missing seam. */
export const V5_A03_PUBLIC_REASON_IDS = deepFreeze([
  "bounded_adjudication_receipt_store_unavailable",
  "exhaustive_finding_set_registry_unavailable",
  "review_round_ledger_unavailable",
  "reviewer_identity_registry_unavailable",
]);

function reason(id) {
  if (!V5_A03_PUBLIC_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a reason this surface may give`, { reason_id: id });
  return id;
}

// ---------------------------------------------------------------------------
// THE AUTHORITATIVE HOLDERS, NAMED BY THIS MODULE.
//
// Each is a NAME, not a port: nothing a caller passes can become one, because
// no function here accepts a holder as an argument and this module exports no
// way to bind one. The bindings are `null` because the seams do not exist. When
// a ruling builds one, this object is the single place it binds.
// ---------------------------------------------------------------------------

const V5_A03_BINDINGS = Object.freeze({
  [V5_REVIEWER_IDENTITY_REGISTRY_SEAM]: null,
  [V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM]: null,
  [V5_REVIEW_ROUND_LEDGER_SEAM]: null,
  [V5_ADJUDICATION_RECEIPT_STORE_SEAM]: null,
});

/** The bound holder for a seam, or null when nothing is bound. Takes no input. */
function boundSeam(seam, method) {
  const holder = V5_A03_BINDINGS[seam];
  return isPlainObject(holder) && typeof holder[method] === "function" ? holder : null;
}

/** Every seam an answer is owed, and whether anything is bound to it. */
function seamsOwed(seams) {
  return deepFreeze([...seams].sort().map(seam => ({ seam, bound: false })));
}

/**
 * The one shape every answer on this surface has.
 *
 * No field of it comes from a request, because no function here reads one. The
 * clause order is recited as `clauses_defined` — the names of the checks a
 * future authoritative reader would run — and `clauses_evaluated` is empty,
 * because this surface evaluated none of them and saying otherwise would be the
 * affirmative sub-result the review of PR 987 refused.
 */
function unavailable(answer, reasonId, because, seams, clauses, extra) {
  return deepFreeze({
    schema_version: V5_A03_SCHEMA_VERSION,
    policy_version: V5_A03_POLICY_VERSION,
    answer,
    tenant: ORGANIZATION_TENANT_ID,
    status: "unavailable",
    decision: "refuse",
    reason_id: reason(reasonId),
    unavailable_because: because,
    owed_seams: [...seams].sort(),
    seams_bound: seamsOwed(seams),
    clauses_defined: [...clauses],
    clauses_evaluated: [],
    clause_evaluation_is_test_only: true,
    // No field of any request can change any field of this answer.
    request_read: false,
    caller_evidence_is_authority: false,
    decided_by: "no_authoritative_reader",
    model_judgment_is_authority: false,
    state_holder_is_caller_supplied: false,
    performs_routing: false,
    performs_repair: false,
    performs_adjudication: false,
    effects: V5_NO_EFFECTS,
    ...extra,
  });
}

/** The arity IS the boundary: a holder is never an argument. */
function refuseSecondArgument(received, name, seam, code) {
  if (received > 1)
    fail(code, `${name} takes one request; the authoritative holder is bound by this module`,
      { arguments_received: received, required_seam: seam });
}

// ---------------------------------------------------------------------------
// checkable_done 1 — "maker/reviewer/releaser/session identities differ".
// ---------------------------------------------------------------------------

/**
 * May this review be routed across the delivered set, by identities that really
 * are separate?
 *
 * UNAVAILABLE, for every caller on every input. Separation over caller-named
 * identities is a separation the caller asserted about itself: nothing here can
 * check that the identity called `builder` built anything, that the identity
 * called `reviewer-security` is entitled to review security, or that two refs
 * spelled differently are two different actors. Establishing that is
 * `seam:independent-reviewer-identity-registry`, and it does not exist.
 */
export function readReviewRoutingAdmission(request) {
  // eslint-disable-next-line no-unused-vars, prefer-rest-params -- the arity IS
  // the boundary, and the request is deliberately not read.
  refuseSecondArgument(arguments.length, "readReviewRoutingAdmission",
    V5_REVIEWER_IDENTITY_REGISTRY_SEAM, "reviewer_identity_registry_is_not_an_argument");
  return unavailable(
    "review_routing_admission",
    "reviewer_identity_registry_unavailable",
    "no authoritative registry of which identities may review, in which dimension, on which change exists, so identity separation cannot be established by construction",
    [V5_REVIEWER_IDENTITY_REGISTRY_SEAM],
    V5_ROUTING_CHECKS,
    {
      dimensions_required: [...V5_REVIEW_DIMENSIONS],
      reviewer_identity_registry_seam: V5_REVIEWER_IDENTITY_REGISTRY_SEAM,
      registry_bound: boundSeam(V5_REVIEWER_IDENTITY_REGISTRY_SEAM, "resolveReviewerEntitlement") !== null,
      // FIXED. Not derived from a routing, not derivable by any caller.
      reviewers_entitled: false,
      identities_separated_by_construction: false,
      dimensions_assigned: null,
    });
}

// ---------------------------------------------------------------------------
// checkable_done 2 — "complete findings batched and regressed".
// ---------------------------------------------------------------------------

/**
 * Is this round's finding set the complete one, batch-repaired and regressed?
 *
 * UNAVAILABLE, for every caller on every input. A finding set is complete
 * relative to a registry that holds findings independently of the receipt that
 * admits them, and this repository has none, so "complete" is a word only the
 * caller can say here — which is the shape this slice exists to refuse.
 */
export function readFindingSetExhaustiveness(request) {
  // eslint-disable-next-line no-unused-vars, prefer-rest-params -- the arity IS
  // the boundary, and the request is deliberately not read.
  refuseSecondArgument(arguments.length, "readFindingSetExhaustiveness",
    V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM, "exhaustive_finding_set_registry_is_not_an_argument");
  return unavailable(
    "finding_set_exhaustiveness",
    "exhaustive_finding_set_registry_unavailable",
    "no registry holds a finding independently of the receipt that admits it, so the only available list is the maker's own and an exhaustive set cannot be established",
    [V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM],
    V5_FINDING_SET_CHECKS,
    {
      dimensions_required: [...V5_REVIEW_DIMENSIONS],
      finding_registry_seam: V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM,
      registry_bound: boundSeam(V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM, "resolveFindingSet") !== null,
      // FIXED. Batch repair is what a complete finding set would unlock, and
      // this surface can never establish one.
      batch_repair_unlocked: false,
      regression_evidence_read: null,
      dimensions_submitted: null,
    });
}

// ---------------------------------------------------------------------------
// checkable_done 3, first half — "third loop cannot silently continue".
// ---------------------------------------------------------------------------

/**
 * May another review round begin?
 *
 * UNAVAILABLE, for every caller on every input. The bound Q042.D1 sets is on
 * ROUNDS, and nothing in this repository can say which round this is: a caller
 * stating `requested_round_ordinal: 2` is a caller counting its own rounds. The
 * bound itself is exported as V5_MAX_REVIEW_ROUNDS and the transition it owes
 * past the bound as V5_ROUND_TRANSITIONS, so a consumer can read the rule; this
 * function never applies it to a caller's count.
 */
export function readReviewRoundAdmission(request) {
  // eslint-disable-next-line no-unused-vars, prefer-rest-params -- the arity IS
  // the boundary, and the request is deliberately not read.
  refuseSecondArgument(arguments.length, "readReviewRoundAdmission",
    V5_REVIEW_ROUND_LEDGER_SEAM, "review_round_ledger_is_not_an_argument");
  return unavailable(
    "review_round_admission",
    "review_round_ledger_unavailable",
    "no ledger records a review round, its batch repair or the regression bound to that batch, and a count of reviewer facts is a count of reviewers rather than of rounds",
    [V5_REVIEW_ROUND_LEDGER_SEAM],
    V5_ROUND_BOUND_CHECKS,
    {
      round_limit: V5_MAX_REVIEW_ROUNDS,
      drift_detectors: [...V5_ROUND_REGRESSION_CLASSES],
      review_round_ledger_seam: V5_REVIEW_ROUND_LEDGER_SEAM,
      ledger_bound: boundSeam(V5_REVIEW_ROUND_LEDGER_SEAM, "resolveRoundHistory") !== null,
      // FIXED. No round is admitted, and no round is refused ON ITS MERITS
      // either: a refusal that named the caller's ordinal would be reading the
      // caller's count.
      round_may_begin: false,
      requested_round_ordinal: null,
      within_round_limit: null,
      required_transition: null,
      drift_detected: null,
    });
}

// ---------------------------------------------------------------------------
// checkable_done 3, second half — "pass/fail/quarantine recorded".
// ---------------------------------------------------------------------------

/**
 * May this dispute be adjudicated, and how did it end?
 *
 * MAY IT: unavailable. HOW DID IT END: this module never says, on any input,
 * forever. `adjudicated_outcome` is null and `disposition_recorded` is false
 * because `seam:bounded-adjudication-receipt-store` does not exist — nothing in
 * this repository can write a pass, a fail or a quarantine for a review dispute,
 * and an outcome a party can state about its own dispute is not an adjudication,
 * it is a self-certification with a judge's letterhead.
 */
export function readBoundedAdjudication(request) {
  // eslint-disable-next-line no-unused-vars, prefer-rest-params -- the arity IS
  // the boundary, and the request is deliberately not read.
  refuseSecondArgument(arguments.length, "readBoundedAdjudication",
    V5_ADJUDICATION_RECEIPT_STORE_SEAM, "adjudication_receipt_store_is_not_an_argument");
  return unavailable(
    "bounded_adjudication",
    "bounded_adjudication_receipt_store_unavailable",
    "no store holds an adjudication receipt for a review dispute, so no disposition can be recorded and no outcome can be read",
    [V5_ADJUDICATION_RECEIPT_STORE_SEAM],
    V5_ADJUDICATION_CHECKS,
    {
      adjudicator_role_required: V5_ADJUDICATOR_ROLE,
      round_limit: V5_MAX_REVIEW_ROUNDS,
      adjudication_receipt_store_seam: V5_ADJUDICATION_RECEIPT_STORE_SEAM,
      store_bound: boundSeam(V5_ADJUDICATION_RECEIPT_STORE_SEAM, "resolveReceipt") !== null,
      // Null and false on every input, forever, until the store exists.
      adjudicated_outcome: null,
      outcome_is_caller_stated: false,
      disposition_recorded: false,
      adjudication_may_begin: false,
    });
}

// ---------------------------------------------------------------------------
// The receipt path. There is no verification here, and that is the correction.
// ---------------------------------------------------------------------------

/**
 * Verify one adjudication receipt.
 *
 * UNAVAILABLE. The earlier version of this function took a receipt BODY as an
 * argument, hashed it, compared it to the caller's own citation of it, checked
 * the caller's own party list, and returned `ok: true`. Every input in that
 * chain came from the same caller, so it verified a self-made receipt and said
 * the receipt was well-formed — which is the sentence a consumer reads as "this
 * dispute was adjudicated".
 *
 * A receipt is resolved from the authoritative store or it is not resolved at
 * all. The store does not exist, so this reports that and nothing else, and a
 * receipt body handed in as a second argument is a contract violation rather
 * than evidence.
 *
 * THE SHAPE RULES STILL EXIST AND ARE STILL PROVED — content addressing, digest
 * agreement, kind, change and delivered-set binding, the closed outcome
 * vocabulary and the adjudicator-is-not-a-party clause — as
 * `classifyAdjudicationReceiptIfAuthoritative` in
 * mcp-server/test/complete-set-review-a03-classifiers.v5.testhelper.mjs, which
 * production cannot import and which answers only
 * `would_be_verifiable_if_authoritative`.
 */
export function verifyAdjudicationReceipt(request) {
  // eslint-disable-next-line no-unused-vars, prefer-rest-params -- the arity IS
  // the boundary, and the receipt is deliberately not read.
  refuseSecondArgument(arguments.length, "verifyAdjudicationReceipt",
    V5_ADJUDICATION_RECEIPT_STORE_SEAM, "adjudication_receipt_is_not_an_argument");
  return unavailable(
    "adjudication_receipt_verification",
    "bounded_adjudication_receipt_store_unavailable",
    "an adjudication receipt is resolved from the authoritative store, and no such store exists; a receipt body supplied by a caller is not a reading of one",
    [V5_ADJUDICATION_RECEIPT_STORE_SEAM],
    V5_ADJUDICATION_CHECKS,
    {
      receipt_kind: V5_ADJUDICATION_RECEIPT_KIND,
      adjudication_receipt_store_seam: V5_ADJUDICATION_RECEIPT_STORE_SEAM,
      store_bound: boundSeam(V5_ADJUDICATION_RECEIPT_STORE_SEAM, "resolveReceipt") !== null,
      // FIXED. No receipt is verified, no receipt is resolved, and no outcome
      // is ever echoed back from one.
      receipt_verified: false,
      receipt_resolved: false,
      receipt_is_caller_supplied: false,
      adjudicated_outcome: null,
    });
}

// ---------------------------------------------------------------------------
// THE CLOSED ADJUDICATION OUTCOME VOCABULARY. Module-private, and nothing in
// this slice ever produces one of these values.
//
// Q042.D1 closes adjudication to three dispositions. This module holds the fact
// that there are exactly three and nothing else about them: the codes are
// ORDINAL, they carry no disposition word and no mapping to one, and no comment
// here records which ordinal means which — because a mapping written down is a
// mapping a reader can apply, and the reader this slice is defending against is
// a consumer pattern-matching bytes. Two earlier spellings failed that test: the
// bare words "fail"/"pass"/"quarantine", and then `adverse`/`favorable`/
// `isolating`, which published the same three dispositions in a thesaurus.
//
// The binding these codes are owed belongs to
// `seam:bounded-adjudication-receipt-store`, which does not exist. When it does,
// it — and only it — decides which stored disposition is which ordinal, and it
// pins this vocabulary through `v5A03AdjudicationOutcomeVocabularyDigest()`
// rather than by importing values that never leave this file.
// ---------------------------------------------------------------------------

const ADJUDICATION_OUTCOME_CODE_SET = Object.freeze([
  "adjudication-outcome-code:1-if-authoritative",
  "adjudication-outcome-code:2-if-authoritative",
  "adjudication-outcome-code:3-if-authoritative",
]);

/**
 * The identity of that closed set, and the only thing this module says about it
 * out loud.
 *
 * A receipt store binds to this digest: it changes if a code changes or a fourth
 * appears, and it recites nothing a consumer could act on. It is a sha256 ref,
 * not a disposition and not a clearance.
 */
export function v5A03AdjudicationOutcomeVocabularyDigest() {
  return digest(canonicalJson([...ADJUDICATION_OUTCOME_CODE_SET].sort()));
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no change, review, round, identity, finding or
// receipt, and no argument is read at all — so two callers describing the same
// policy reach the same digest. It is an identity for these bytes: not an
// acceptance, not a receipt, and not evidence for any consumer gate.
//
// THE PREIMAGE AND ITS BYTES ARE MODULE-PRIVATE, and only the digest is
// exported. That is the second correction of PR 987. The preimage recites every
// closed vocabulary this slice holds, so it is the one structure in the module
// that serializes a whole vocabulary into a payload — and the previous version
// exported both the object and its canonical JSON, which put the bare word
// "pass" on the public surface twice over. The defence offered for it was that a
// vocabulary is not an answer; a consumer reading the bytes cannot make that
// distinction, so the defence was worth nothing. The digest is 64 hex
// characters: it pins the policy exactly as well and recites nothing.
//
// The preimage is still readable where reading it is safe — the suite rebuilds
// it field for field in
// `mcp-server/test/complete-set-review-a03-classifiers.v5.testhelper.mjs` and
// asserts the rebuild digests to `v5A03PolicyDigest()`. A drift in either one
// moves the digest and fails, so the mirror cannot quietly disagree with the
// private original, and nothing under mcp-server/src can reach either.
// ---------------------------------------------------------------------------

function policyPreimage() {
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
    adjudication_outcome_codes: [...ADJUDICATION_OUTCOME_CODE_SET].sort(),
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

/** The bytes the digest is taken over. Module-private, like the preimage. */
function policyCanonicalBytes() {
  return canonicalJson(policyPreimage());
}

/**
 * The policy identity: a sha256 ref, and the only thing this module says about
 * its own policy out loud.
 *
 * A consumer pins this. It cannot recite a vocabulary, cannot be pattern-matched
 * for a privileged word, and changes the moment any list in the preimage
 * changes — which is the whole job the exported preimage used to do, minus the
 * part where it handed the words out.
 */
export function v5A03PolicyDigest() {
  return digest(policyCanonicalBytes());
}
