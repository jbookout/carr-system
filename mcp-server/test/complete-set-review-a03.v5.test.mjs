// DoctorCRE v5 slice V5-A03 — independent complete-set review and bounded
// adjudication.
//
// Three kinds of test live here and they are not interchangeable:
//
//   SURFACE tests prove the honest unavailable — that each of the five public
//   functions answers `unavailable` naming the seam it is owed, for every
//   caller on every input, with no affirmative sub-result anywhere in the
//   answer, and that the answers are BYTE-IDENTICAL across every caller shape.
//   If no field of a request can change any field of an answer, then no caller
//   can smuggle authority in through one.
//
//   CLAUSE tests prove the deterministic content — that a missing dimension, a
//   maker reviewing itself, a narrowed review scope, a third round, a drifting
//   history, an adjudicator who is a party and a forged receipt are each caught
//   by the clause that names them. They run against the TEST-ONLY classifier
//   entry, which production cannot import.
//
//   GUARD tests prove the standing rule: no caller-supplied label, fixture,
//   receipt or injected holder is authority. They sweep the whole public export
//   surface against caller-controlled input and assert the privileged outcome
//   never comes back, and they use NODE'S OWN MODULE PARSER
//   (vm.SourceTextModule), not a regex, to prove no module under mcp-server/src
//   can reach the classifiers at all.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError } from "../src/global-boundaries.v5.js";
import * as a03 from "../src/complete-set-review-a03.v5.js";
import * as a03Vocabulary from "../src/complete-set-review-a03.vocabulary.v5.js";
import {
  V5_A03_CLASSIFICATIONS,
  V5_A03_CLASSIFIER_EVIDENCE_SOURCE,
  V5_A03_CLAUSE_REASONS,
  classifyAdjudicationIfAuthoritative,
  classifyAdjudicationReceiptIfAuthoritative,
  classifyFindingSetIfAuthoritative,
  classifyRoundIfAuthoritative,
  classifyRoutingIfAuthoritative,
  detectRoundRegressions,
  reviewDimensionGap,
  reviewRoundObligation,
  roleSeparationCollisions,
  v5A03PolicyPreimageMirror,
} from "./complete-set-review-a03-classifiers.v5.testhelper.mjs";

const {
  V5_A03_DECISION_IDS, V5_A03_PUBLIC_REASON_IDS, V5_A03_REASON_IDS, V5_A03_SCHEMA_VERSION,
  V5_A03_SEAMS, V5_A03_SETTLED_DECISION_DIGESTS, V5_ADJUDICATION_CHECKS,
  V5_ADJUDICATION_RECEIPT_KIND, V5_ADJUDICATOR_ROLE, V5_FINDING_SET_CHECKS, V5_MAX_REVIEW_ROUNDS,
  V5_NON_REVIEWER_ROLES, V5_NO_EFFECTS, V5_OPPOSING_ROLE_PAIRS, V5_REVIEW_DIMENSIONS,
  V5_ROUND_BOUND_CHECKS, V5_ROUND_REGRESSION_CLASSES, V5_ROUTING_CHECKS,
  assertA03DecisionBinding, readBoundedAdjudication, readFindingSetExhaustiveness,
  readReviewRoundAdmission, readReviewRoutingAdmission,
  v5A03PolicyDigest, verifyAdjudicationReceipt,
} = a03;

// The outcome vocabulary is no longer on the public surface. A fixture that
// needs a well-formed receipt disposition takes it from the vocabulary module,
// which is where the opaque codes live.
const { V5_ADJUDICATION_OUTCOME_CODES, V5_ADJUDICATION_OUTCOME_CODE_SET } = a03Vocabulary;

/**
 * The five settled sentences, verbatim, held HERE rather than imported.
 *
 * Production keeps them module-private: they contain "pass, fail, or
 * quarantine", "allow at most two full rounds" and "the complete finding set",
 * and this slice's contract is that no privileged word reaches a consumer out
 * of a payload. A binding is built from doctrine, not from the module that
 * checks it, so the suite builds one the way a real caller must — from its own
 * copy of the text. If production's private table drifts from these five
 * sentences by one character, assertA03DecisionBinding refuses the binding
 * below and this file fails.
 */
const A03_SETTLED_REQUIREMENT_TEXT = Object.freeze({
  "Q028.D1": "CI never certifies its own specification; every consequential slice receives independent fresh-context review, automatic remediation, and unresolved-material-disagreement escalation only.",
  "Q042.D1": "Each review round discovers the complete finding set before batch repair; allow at most two full rounds, then stronger adjudication and pass, fail, or quarantine without endless spirals.",
  "Q107.D1": "Separate architect, builder, reviewer, integration, deployment, and program-control duties for the same change; no role may certify or weaken its own work.",
  "Q113.D1": "Test deterministic behavior, adapters, database, state machines, failures, browsers, staging, and production outcomes through deep-module interfaces; discover all findings, batch repair, fully regress, and independently review outcomes.",
  "Q154.D1": "Run separate exhaustive architecture, sequencing, repository, migration, context, security, business, product, cost, resilience, and operations reviews; collect complete findings, batch repair, and conduct one fresh full-plan review.",
});

/** The binding a caller holding doctrine would hand in. */
function heldDecisionBinding() {
  return { decisions: Object.fromEntries(V5_A03_DECISION_IDS.map(id => [id, {
    settled_requirement: A03_SETTLED_REQUIREMENT_TEXT[id],
    source_evidence_digest: V5_A03_SETTLED_DECISION_DIGESTS[id],
  }])) };
}

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
    outcome: V5_ADJUDICATION_OUTCOME_CODES.isolating,
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

/** The clause a classifier blocked on, plus the reason it gave. */
function blockedAt(result) {
  return { blocking_clause: result.blocking_clause, reason_id: result.reason_id };
}

// ---------------------------------------------------------------------------
// The settled decisions and the policy identity.
// ---------------------------------------------------------------------------

test("binding: the public half of the binding is digests only, never the settled text", () => {
  assert.deepEqual([...V5_A03_DECISION_IDS], ["Q028.D1", "Q042.D1", "Q107.D1", "Q113.D1", "Q154.D1"]);
  assert.deepEqual(Object.keys(V5_A03_SETTLED_DECISION_DIGESTS).sort(), [...V5_A03_DECISION_IDS]);
  for (const id of V5_A03_DECISION_IDS)
    assert.match(V5_A03_SETTLED_DECISION_DIGESTS[id], /^[0-9a-f]{64}$/);
  assert.equal(Object.isFrozen(V5_A03_SETTLED_DECISION_DIGESTS), true);
  // The settled sentences are not reachable from the module under any name. They
  // carry three privileged words, and this slice hands a consumer none.
  assert.equal("V5_A03_SETTLED_DECISIONS" in a03, false);
  for (const value of Object.values(a03))
    assert.equal(typeof value === "object" && value !== null &&
      Object.values(value).some(entry => entry && entry.settled_requirement !== undefined), false);
});

test("binding: agreement is silence, and drift is refused in both directions", () => {
  const held = heldDecisionBinding();
  // No affirmative return value: a consumer has nothing here to mistake for a
  // clearance. It throws, or it says nothing at all.
  assert.equal(assertA03DecisionBinding(held), undefined);

  const dropped = { decisions: { ...held.decisions } };
  delete dropped.decisions["Q107.D1"];
  assert.throws(() => assertA03DecisionBinding(dropped),
    error => error instanceof V5BoundaryError && error.code === "decision_binding_drift");

  const added = { decisions: { ...held.decisions, "Q999.D1": { settled_requirement: "x",
    source_evidence_digest: "0".repeat(64) } } };
  assert.throws(() => assertA03DecisionBinding(added),
    error => error instanceof V5BoundaryError && error.code === "decision_binding_drift");

  const reworded = { decisions: { ...held.decisions,
    "Q042.D1": { ...held.decisions["Q042.D1"], settled_requirement: "allow three rounds" } } };
  assert.throws(() => assertA03DecisionBinding(reworded),
    error => error instanceof V5BoundaryError && error.code === "decision_binding_drift");
});

test("policy: only the digest is public, and the readable mirror is bound to it", () => {
  // The preimage and its canonical bytes are module-private now. The suite reads
  // the mirror under test/, and the digest is what proves the mirror has not
  // drifted from the private original by so much as a sort order.
  assert.equal("v5A03PolicyPreimage" in a03, false);
  assert.equal("v5A03PolicyCanonicalBytes" in a03, false);
  assert.match(v5A03PolicyDigest(), /^sha256:[0-9a-f]{64}$/);

  const mirror = v5A03PolicyPreimageMirror();
  assert.equal(digest(mirror), v5A03PolicyDigest(),
    "the mirror under test/ has drifted from the private production preimage");
  for (const key of ["decision", "status", "answer", "ok", "reason_id"])
    assert.equal(key in mirror, false, `${key} is an answer field and does not belong in a policy identity`);
  assert.equal(mirror.public_surface_answers, "unavailable");
  assert.equal(mirror.authoritative_holders_bound, false);

  // NO EXEMPTION HERE EITHER. The preimage recites every closed vocabulary this
  // slice holds, and after the second correction not one of those vocabularies
  // contains a privileged word — so the whole structure is swept, with no
  // vocabulary keys skipped, which is what the earlier version of this test did.
  assert.deepEqual(privilegedStrings(mirror), []);
  assert.deepEqual([...mirror.adjudication_outcome_codes], [...V5_ADJUDICATION_OUTCOME_CODE_SET]);
  assert.deepEqual([...mirror.review_states], ["changes_required", "no_changes_required"]);
});

test("policy: the digest moves when a vocabulary moves — the identity is not decorative", () => {
  // A digest nothing can change is not an identity. The mirror is the lever: a
  // one-word change to any recited vocabulary must produce different bytes.
  const mirror = v5A03PolicyPreimageMirror();
  const moved = { ...mirror, review_dimensions: [...mirror.review_dimensions, "twelfth"] };
  assert.notEqual(digest(moved), v5A03PolicyDigest());
});

test("policy: the reason vocabulary is sorted, unique, and the public subset is the seam reasons", () => {
  assert.deepEqual([...V5_A03_REASON_IDS], [...new Set(V5_A03_REASON_IDS)].sort());
  assert.deepEqual([...V5_A03_PUBLIC_REASON_IDS], [...V5_A03_PUBLIC_REASON_IDS].sort());
  for (const id of V5_A03_PUBLIC_REASON_IDS) {
    assert.equal(V5_A03_REASON_IDS.includes(id), true, id);
    assert.match(id, /_unavailable$/, "every reason the public surface may give names a missing seam");
  }
  assert.equal(V5_A03_PUBLIC_REASON_IDS.length, 4);
});

test("policy: the eleven review dimensions are exactly Q154.D1's list", () => {
  assert.deepEqual([...V5_REVIEW_DIMENSIONS], ["architecture", "business", "context", "cost",
    "migration", "operations", "product", "repository", "resilience", "security", "sequencing"]);
  assert.deepEqual([...V5_REVIEW_DIMENSIONS], [...V5_REVIEW_DIMENSIONS].sort());
});

// ---------------------------------------------------------------------------
// THE PUBLIC SURFACE. Five functions, five unavailable answers, no exceptions.
// ---------------------------------------------------------------------------

const EXPECTED_PUBLIC_EXPORTS = [
  "V5_A03_DECISION_IDS",
  "V5_A03_POLICY_VERSION",
  "V5_A03_PUBLIC_REASON_IDS",
  "V5_A03_REASON_IDS",
  "V5_A03_SCHEMA_VERSION",
  "V5_A03_SEAMS",
  "V5_A03_SETTLED_DECISION_DIGESTS",
  "V5_ADJUDICATION_CHECKS",
  "V5_ADJUDICATION_RECEIPT_KIND",
  "V5_ADJUDICATION_RECEIPT_STORE_SEAM",
  "V5_ADJUDICATOR_ROLE",
  "V5_CONTEXT_BINDINGS",
  "V5_DETERMINISTIC_ONLY_DECISIONS",
  "V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM",
  "V5_FINDING_SET_CHECKS",
  "V5_MAX_REVIEW_ROUNDS",
  "V5_MODEL_PERMITTED_ROLES",
  "V5_NON_REVIEWER_ROLES",
  "V5_NO_EFFECTS",
  "V5_OPPOSING_ROLE_PAIRS",
  "V5_REVIEWER_IDENTITY_REGISTRY_SEAM",
  "V5_REVIEW_DIMENSIONS",
  "V5_REVIEW_ROLES",
  "V5_REVIEW_ROUND_LEDGER_SEAM",
  "V5_REVIEW_STATES",
  "V5_ROUND_BOUND_CHECKS",
  "V5_ROUND_REGRESSION_CLASSES",
  "V5_ROUND_TRANSITIONS",
  "V5_ROUTING_CHECKS",
  "V5_SUBMISSION_STATES",
  "assertA03DecisionBinding",
  "readBoundedAdjudication",
  "readFindingSetExhaustiveness",
  "readReviewRoundAdmission",
  "readReviewRoutingAdmission",
  "v5A03PolicyDigest",
  "verifyAdjudicationReceipt",
];

/** The vocabulary module's whole export surface, swept exactly like the public one. */
const EXPECTED_VOCABULARY_EXPORTS = [
  "V5_A03_POLICY_VERSION",
  "V5_A03_SCHEMA_VERSION",
  "V5_A03_SEAMS",
  "V5_ADJUDICATION_CHECKS",
  "V5_ADJUDICATION_OUTCOME_CODES",
  "V5_ADJUDICATION_OUTCOME_CODE_SET",
  "V5_ADJUDICATION_RECEIPT_KIND",
  "V5_ADJUDICATION_RECEIPT_STORE_SEAM",
  "V5_ADJUDICATOR_ROLE",
  "V5_CONTEXT_BINDINGS",
  "V5_DETERMINISTIC_ONLY_DECISIONS",
  "V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM",
  "V5_FINDING_SET_CHECKS",
  "V5_MAX_REVIEW_ROUNDS",
  "V5_MODEL_PERMITTED_ROLES",
  "V5_NON_REVIEWER_ROLES",
  "V5_OPPOSING_ROLE_PAIRS",
  "V5_REVIEWER_IDENTITY_REGISTRY_SEAM",
  "V5_REVIEW_DIMENSIONS",
  "V5_REVIEW_ROLES",
  "V5_REVIEW_ROUND_LEDGER_SEAM",
  "V5_REVIEW_STATES",
  "V5_ROUND_BOUND_CHECKS",
  "V5_ROUND_REGRESSION_CLASSES",
  "V5_ROUND_TRANSITIONS",
  "V5_ROUTING_CHECKS",
  "V5_SUBMISSION_STATES",
];

/**
 * THE SIX PRIVILEGED WORDS, and the sweep that may not exempt one export.
 *
 * The first correction of PR 987 swept five reader functions and deliberately
 * exempted the outcome vocabulary; the second review found the bare word `pass`
 * still leaving the module through that exemption, from `V5_ADJUDICATION_OUTCOMES`
 * and out of the exported policy preimage and its canonical bytes. So the sweep
 * is rebuilt on the opposite principle: EVERY export of EVERY src module in this
 * slice, matched against each word on its own, as a whole string and as a
 * substring, in the export's NAME, in its value walked to the leaves (object
 * keys included), and in every value it returns for every caller-controlled
 * shape. There is no exemption list, and adding one is the defect.
 */
const PRIVILEGED_WORDS = ["ok", "allow", "pass", "satisfied", "complete", "admitted"];

const SWEPT_SRC_MODULES = Object.freeze({
  "mcp-server/src/complete-set-review-a03.v5.js": a03,
  "mcp-server/src/complete-set-review-a03.vocabulary.v5.js": a03Vocabulary,
});

/** The words a consumer would act on. None may come back from this surface. */
const PRIVILEGED_TRUE_KEYS = new Set([
  "ok", "allow", "allowed", "approved", "verified", "receipt_verified", "receipt_resolved",
  "reviewers_entitled", "identities_separated_by_construction", "batch_repair_unlocked",
  "round_may_begin", "adjudication_may_begin", "disposition_recorded", "within_round_limit",
  "outcome_is_caller_stated", "receipt_is_caller_supplied", "registry_bound", "ledger_bound",
  "store_bound", "request_read", "caller_evidence_is_authority", "model_judgment_is_authority",
  "state_holder_is_caller_supplied", "performs_routing", "performs_repair", "performs_adjudication",
  "satisfied", "complete", "covered",
]);

const PRIVILEGED_VALUES = new Set(["active", "allow", "allowed", "approved", "commit", "complete",
  "completed", "covered", "drafted", "green", "healthy", "independent", "operational", "pass",
  "passable", "passed", "passing", "proposed", "prompt", "queued", "read", "release", "released",
  "satisfied", "suppress"]);

function stringValues(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) value.forEach(item => stringValues(item, out));
  else if (value !== null && typeof value === "object")
    Object.values(value).forEach(item => stringValues(item, out));
  return out;
}

/** Every string in a value INCLUDING its object keys, walked to the leaves. */
function stringsAndKeys(value, out = []) {
  if (typeof value === "string") { out.push(value); return out; }
  if (Array.isArray(value)) { value.forEach(item => stringsAndKeys(item, out)); return out; }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) { out.push(key); stringsAndKeys(entry, out); }
    return out;
  }
  return out;
}

/** The strings in a value that carry one of the six words, whole or as a part. */
function privilegedStrings(value) {
  return stringsAndKeys(value).filter(found =>
    PRIVILEGED_WORDS.some(word => found.toLowerCase().includes(word)));
}

/**
 * Every string one export can put in front of a consumer: its own name, its
 * value, and — when it is a function — every value it returns for every
 * caller-controlled shape, including no argument at all.
 *
 * A THROW IS NOT A RETURN VALUE, and the difference is the point rather than an
 * exemption. `assertA03DecisionBinding` refuses a malformed binding by throwing,
 * and its message quotes the caller's own field name back — so a caller passing
 * a field called `pass` would see that word in the message it caused. What the
 * sweep requires is that the module never HANDS BACK a privileged word, so a
 * throw is checked for what it is: a V5BoundaryError whose stable `code` is
 * swept like any other returned string. The message, which is caller text, is
 * not a value any consumer can read as an answer.
 */
function everyStringAnExportCanShow(name, value) {
  const found = [name];
  if (typeof value !== "function") return stringsAndKeys(value, found);
  const shapes = [...callerControlledShapes(), heldDecisionBinding(), NO_ARGUMENT];
  for (const shape of shapes) {
    try {
      stringsAndKeys(shape === NO_ARGUMENT ? value() : value(shape), found);
    } catch (error) {
      assert.equal(error instanceof V5BoundaryError, true,
        `${name} threw something other than a boundary refusal`);
      found.push(error.code);
    }
  }
  return found;
}

/** A sentinel for "called with no argument at all", distinct from `undefined`. */
const NO_ARGUMENT = Symbol("no-argument");

/** Every string, key and boolean in a returned value, walked to the leaves. */
function privilegedFindings(value, at = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedFindings(entry, `${at}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const where = `${at}.${key}`;
      if (entry === true && PRIVILEGED_TRUE_KEYS.has(key)) found.push(`${where} === true`);
      if (key.startsWith("would_")) found.push(`${where} is a classifier field on the public surface`);
      privilegedFindings(entry, where, found);
    }
    return found;
  }
  if (typeof value === "string" && PRIVILEGED_VALUES.has(value)) found.push(`${at} === ${value}`);
  return found;
}

/**
 * Every caller-controlled shape this surface could ever be handed, including the
 * reviewer's reproduction: a receipt a party wrote about its own dispute. If
 * none of them changes the answer, none of them is authority.
 */
function callerControlledShapes() {
  const selfMade = receiptBody({ outcome: "pass", adjudicator_identity_ref: "actor:builder" });
  return [
    routingRequest(),
    routingRequest({ assignments: [] }),
    routingRequest({ role_identities: { adjudicator: ROLE_IDENTITIES.builder } }),
    findingSetRequest(),
    findingSetRequest({ submissions: [] }),
    roundRequest(),
    roundRequest({ requested_round_ordinal: 3 }),
    roundRequest({ requested_round_ordinal: 9, adjudication_recorded: true }),
    adjudicationRequest(),
    adjudicationRequest({ rounds_completed: 0 }),
    // The receipt path's old arguments, now just another caller shape.
    { kind: V5_ADJUDICATION_RECEIPT_KIND, receipt_ref: receiptRefFor(selfMade), body: selfMade,
      binding: receiptBinding({ party_identity_refs: ["actor:reviewer-architecture"] }) },
    selfMade,
    // And the shapes that try to say the answer outright.
    { verified: true, decision: "allow", ok: true, outcome: "pass", round_may_begin: true },
    { adjudicated_outcome: "pass", disposition_recorded: true },
    {}, null, undefined, "allow", 1, true, [],
  ];
}

const PUBLIC_ANSWERS = [
  ["readReviewRoutingAdmission", readReviewRoutingAdmission, "reviewer_identity_registry_unavailable"],
  ["readFindingSetExhaustiveness", readFindingSetExhaustiveness, "exhaustive_finding_set_registry_unavailable"],
  ["readReviewRoundAdmission", readReviewRoundAdmission, "review_round_ledger_unavailable"],
  ["readBoundedAdjudication", readBoundedAdjudication, "bounded_adjudication_receipt_store_unavailable"],
  ["verifyAdjudicationReceipt", verifyAdjudicationReceipt, "bounded_adjudication_receipt_store_unavailable"],
];

test("surface: the public export list is exactly the unavailable surface", () => {
  assert.deepEqual(Object.keys(a03).sort(), EXPECTED_PUBLIC_EXPORTS);
  for (const name of Object.keys(a03)) {
    assert.equal(/^(classify|evaluate|derive)/.test(name), false,
      `${name} is a classifier name on the public surface`);
    assert.equal(name.includes("would_"), false, name);
    assert.equal(name.includes("Internal"), false, name);
  }
  // The outcome vocabulary and the policy preimage left the public surface in the
  // second correction. Naming them here means a re-export cannot come back
  // quietly under the general export-list assertion.
  for (const gone of ["V5_ADJUDICATION_OUTCOMES", "V5_ADJUDICATION_OUTCOME_CODES",
    "V5_ADJUDICATION_OUTCOME_CODE_SET", "V5_A03_SETTLED_DECISIONS", "v5A03PolicyPreimage",
    "v5A03PolicyCanonicalBytes"])
    assert.equal(gone in a03, false, `${gone} is back on the public surface`);
});

test("surface: the vocabulary module's export list is exactly the vocabularies", () => {
  assert.deepEqual(Object.keys(a03Vocabulary).sort(), EXPECTED_VOCABULARY_EXPORTS);
  // It is a vocabulary module: it holds no behaviour a caller could invoke.
  for (const [name, value] of Object.entries(a03Vocabulary))
    assert.equal(typeof value === "function", false, `${name} is a function in a vocabulary module`);
});

test("surface: the sweep covers every export of every src module in this slice", () => {
  // The list this sweep walks is the module's own export list, not a hand-kept
  // subset — the defect the second review found was a sweep that covered five
  // reader functions out of forty exports.
  assert.deepEqual(Object.keys(SWEPT_SRC_MODULES).sort(), [
    "mcp-server/src/complete-set-review-a03.v5.js",
    "mcp-server/src/complete-set-review-a03.vocabulary.v5.js",
  ]);
  const sliceSources = readdirSync(path.join(REPO_ROOT, "mcp-server/src"))
    .filter(name => name.startsWith("complete-set-review-a03")).sort();
  assert.deepEqual(sliceSources, Object.keys(SWEPT_SRC_MODULES).sort()
    .map(name => path.basename(name)), "a src module of this slice is not being swept");
  const swept = Object.values(SWEPT_SRC_MODULES).reduce((n, ns) => n + Object.keys(ns).length, 0);
  assert.equal(swept, EXPECTED_PUBLIC_EXPORTS.length + EXPECTED_VOCABULARY_EXPORTS.length);
});

// ONE TEST PER PRIVILEGED WORD. Separate tests rather than one loop inside a
// single assertion, so a failure names the word that leaked without the other
// five hiding behind the first failed assert.
for (const word of PRIVILEGED_WORDS) {
  test(`guard: no export of this slice shows the privileged word "${word}"`, () => {
    const hits = [];
    for (const [moduleName, namespace] of Object.entries(SWEPT_SRC_MODULES))
      for (const [name, value] of Object.entries(namespace))
        for (const shown of everyStringAnExportCanShow(name, value)) {
          const lowered = shown.toLowerCase();
          if (lowered === word) hits.push(`${moduleName} ${name}: whole string "${shown}"`);
          else if (lowered.includes(word)) hits.push(`${moduleName} ${name}: contains "${shown}"`);
        }
    assert.deepEqual(hits, [],
      `the word "${word}" reaches a consumer from this slice's public surface`);
  });
}

test("guard: the per-word sweep is not vacuous — each of the six is really detected", () => {
  // A mutation check on the sweep itself. If a planted value slips through for
  // any one of the six, that word's test above proves nothing.
  for (const word of PRIVILEGED_WORDS) {
    assert.deepEqual(privilegedStrings({ outcome: word }), [word], `whole string: ${word}`);
    assert.deepEqual(privilegedStrings({ outcome: `x-${word}-y` }), [`x-${word}-y`], `substring: ${word}`);
    assert.deepEqual(privilegedStrings({ nested: [{ deep: word.toUpperCase() }] }),
      [word.toUpperCase()], `case and nesting: ${word}`);
    assert.deepEqual(privilegedStrings({ [`${word}_flag`]: 1 }), [`${word}_flag`], `key: ${word}`);
  }
  // And a function export really is called, not just named.
  const seen = everyStringAnExportCanShow("planted", () => ({ verdict: "pass" }));
  assert.equal(seen.includes("pass"), true, "a function export's return value is not being swept");
});

test("surface: every answer is unavailable, names its seam, and admits no caller evidence", () => {
  for (const [name, fn, reasonId] of PUBLIC_ANSWERS) {
    const result = fn(routingRequest());
    assert.equal(result.status, "unavailable", name);
    assert.equal(result.decision, "refuse", name);
    assert.equal(result.reason_id, reasonId, name);
    assert.equal(V5_A03_PUBLIC_REASON_IDS.includes(result.reason_id), true, name);
    assert.equal(result.request_read, false, name);
    assert.equal(result.caller_evidence_is_authority, false, name);
    assert.equal(result.decided_by, "no_authoritative_reader", name);
    assert.equal(result.clause_evaluation_is_test_only, true, name);
    assert.deepEqual(result.clauses_evaluated, [], name);
    assert.equal(result.owed_seams.length > 0, true, name);
    for (const entry of result.seams_bound) assert.equal(entry.bound, false, name);
    assert.deepEqual(result.effects, V5_NO_EFFECTS, name);
    assert.equal(Object.isFrozen(result), true, name);
  }
});

test("surface: no caller-controlled shape produces a privileged outcome", () => {
  const shapes = callerControlledShapes();
  assert.equal(shapes.length >= 20, true, "the sweep must cover the caller-controlled domain");
  for (const [name, fn] of PUBLIC_ANSWERS) {
    for (const shape of shapes) {
      const result = fn(shape);
      assert.deepEqual(privilegedFindings(result), [],
        `${name} leaked a privileged outcome for ${String(JSON.stringify(shape)).slice(0, 90)}`);
      assert.equal(result.decision, "refuse", name);
    }
  }
});

test("surface: the answer is byte-identical across every caller shape", () => {
  for (const [name, fn] of PUBLIC_ANSWERS) {
    const first = digest(fn(routingRequest()));
    for (const shape of callerControlledShapes())
      assert.equal(digest(fn(shape)), first, `${name} answered differently for a caller shape`);
    assert.equal(digest(fn()), first, `${name} answered differently for no argument at all`);
  }
});

test("surface: routing never reports identities separated, and never entitles a reviewer", () => {
  const result = readReviewRoutingAdmission(routingRequest());
  assert.equal(result.answer, "review_routing_admission");
  assert.equal(result.reviewers_entitled, false);
  assert.equal(result.identities_separated_by_construction, false);
  assert.equal(result.dimensions_assigned, null, "a caller's assignment list is not read back");
  assert.deepEqual([...result.dimensions_required], [...V5_REVIEW_DIMENSIONS]);
  assert.equal(result.reviewer_identity_registry_seam, V5_A03_SEAMS.reviewer_identity_registry);
});

test("surface: the finding set never unlocks a batch repair and reads no regression", () => {
  const result = readFindingSetExhaustiveness(findingSetRequest());
  assert.equal(result.batch_repair_unlocked, false);
  assert.equal(result.regression_evidence_read, null);
  assert.equal(result.dimensions_submitted, null);
  assert.equal(result.finding_registry_seam, V5_A03_SEAMS.exhaustive_finding_set_registry);
});

test("surface: no round is admitted, and no caller's ordinal is read back", () => {
  for (const shape of [roundRequest(), roundRequest({ requested_round_ordinal: 1 }),
    roundRequest({ requested_round_ordinal: 3 })]) {
    const result = readReviewRoundAdmission(shape);
    assert.equal(result.round_may_begin, false);
    assert.equal(result.requested_round_ordinal, null);
    assert.equal(result.within_round_limit, null);
    assert.equal(result.required_transition, null);
    assert.equal(result.drift_detected, null);
    assert.equal(result.round_limit, V5_MAX_REVIEW_ROUNDS);
    assert.deepEqual([...result.drift_detectors], [...V5_ROUND_REGRESSION_CLASSES]);
  }
});

test("surface: adjudication records no disposition and states no outcome, ever", () => {
  for (const shape of callerControlledShapes()) {
    const result = readBoundedAdjudication(shape);
    assert.equal(result.adjudicated_outcome, null);
    assert.equal(result.disposition_recorded, false);
    assert.equal(result.adjudication_may_begin, false);
    assert.equal(result.outcome_is_caller_stated, false);
  }
});

test("surface: a self-made receipt is not verified — the PR 987 reproduction", () => {
  // The reviewer's exact construction: a party writes a receipt about its own
  // dispute, signs an outcome, hashes its own bytes, cites the hash, and omits
  // itself from the party list. The old surface answered
  // `{"ok":true,...,"the receipt is well-formed and binds this dispute"}`.
  const body = receiptBody({ outcome: "pass", adjudicator_identity_ref: "actor:builder" });
  const result = verifyAdjudicationReceipt({
    kind: V5_ADJUDICATION_RECEIPT_KIND,
    receipt_ref: receiptRefFor(body),
    body,
    binding: receiptBinding({ party_identity_refs: ["actor:reviewer-architecture"] }),
  });
  assert.equal("ok" in result, false, "there is no ok field to read as a verification");
  assert.equal(result.status, "unavailable");
  assert.equal(result.receipt_verified, false);
  assert.equal(result.receipt_resolved, false);
  assert.equal(result.adjudicated_outcome, null);
  assert.equal(result.reason_id, "bounded_adjudication_receipt_store_unavailable");
  assert.equal(result.owed_seams.includes(V5_A03_SEAMS.bounded_adjudication_receipt_store), true);
  // And the outcome the body carried is nowhere in the answer.
  assert.equal(stringValues(result).includes("pass"), false);
});

test("surface: no holder, ledger, store or receipt body is accepted as a second argument", () => {
  const forged = { resolveReviewerEntitlement: () => true, resolveFindingSet: () => true,
    resolveRoundHistory: () => [], resolveReceipt: () => receiptBody() };
  for (const [name, fn] of PUBLIC_ANSWERS) {
    assert.throws(() => fn(routingRequest(), forged),
      error => error instanceof V5BoundaryError && error.code.endsWith("_is_not_an_argument"),
      `${name} accepted a caller-supplied holder`);
  }
  // And specifically the old four-argument receipt call, which no longer has a
  // shape that reaches an answer.
  assert.throws(
    () => verifyAdjudicationReceipt(V5_ADJUDICATION_RECEIPT_KIND, receiptRefFor(receiptBody()),
      receiptBody(), receiptBinding()),
    error => error instanceof V5BoundaryError &&
      error.code === "adjudication_receipt_is_not_an_argument");
});

// ---------------------------------------------------------------------------
// CLAUSES — routing. Test-only entry; none of this is reachable from production.
// ---------------------------------------------------------------------------

test("routing clause: a complete, separated, fresh routing is conditional and nothing more", () => {
  const result = classifyRoutingIfAuthoritative(routingRequest());
  assert.equal(result.classification, "would_be_routable_if_authoritative");
  assert.equal(result.blocking_clause, null);
  assert.equal(result.is_not_authority, true);
  assert.equal(result.evidence_source, V5_A03_CLASSIFIER_EVIDENCE_SOURCE);
});

test("routing clause: a dimension nobody reviewed is named", () => {
  const assignments = routingRequest().assignments.filter(a => a.dimension !== "security");
  const result = classifyRoutingIfAuthoritative(routingRequest({ assignments }));
  assert.deepEqual(blockedAt(result),
    { blocking_clause: "dimension_coverage", reason_id: "review_dimension_coverage_short_of_closed_set" });
  assert.deepEqual([...result.detail.missing], ["security"]);
});

test("routing clause: reviewing one dimension twice is not reviewing eleven", () => {
  const assignments = routingRequest().assignments
    .map(a => (a.dimension === "security" ? { ...a, dimension: "architecture" } : a));
  const result = classifyRoutingIfAuthoritative(routingRequest({ assignments }));
  assert.equal(result.blocking_clause, "dimension_coverage");
  assert.deepEqual([...result.detail.duplicated], ["architecture"]);
  assert.deepEqual([...result.detail.missing], ["security"]);
});

test("routing clause: the maker may not review its own work, in any dimension", () => {
  for (const dimension of V5_REVIEW_DIMENSIONS) {
    const assignments = routingRequest().assignments.map(a =>
      (a.dimension === dimension ? { ...a, reviewer_identity_ref: ROLE_IDENTITIES.builder } : a));
    const result = classifyRoutingIfAuthoritative(routingRequest({ assignments }));
    assert.deepEqual(blockedAt(result),
      { blocking_clause: "reviewer_role_separation", reason_id: "reviewer_not_role_separated" });
    assert.deepEqual(result.detail.collisions.map(c => c.pair.join("/")), ["builder/reviewer"]);
  }
});

test("routing clause: the architect may not certify its own design, and the releaser may not review", () => {
  for (const [identity, pair] of [[ROLE_IDENTITIES.architect, "architect/reviewer"],
    [ROLE_IDENTITIES.deployment_controller, "deployment_controller/reviewer"],
    [ROLE_IDENTITIES.adjudicator, "adjudicator/reviewer"],
    [ROLE_IDENTITIES.program_controller, "program_controller/reviewer"]]) {
    const assignments = routingRequest().assignments
      .map((a, index) => (index ? a : { ...a, reviewer_identity_ref: identity }));
    const result = classifyRoutingIfAuthoritative(routingRequest({ assignments }));
    assert.equal(result.blocking_clause, "reviewer_role_separation", pair);
    assert.deepEqual(result.detail.collisions.map(c => c.pair.join("/")), [pair]);
  }
});

test("routing clause: one identity holding two opposing duties is caught duty-to-duty", () => {
  for (const [role, pair] of [["integration_controller", "builder/integration_controller"],
    ["deployment_controller", "builder/deployment_controller"],
    ["program_controller", "builder/program_controller"],
    ["adjudicator", "adjudicator/builder"]]) {
    const result = classifyRoutingIfAuthoritative(
      routingRequest({ role_identities: { [role]: ROLE_IDENTITIES.builder } }));
    assert.deepEqual(blockedAt(result),
      { blocking_clause: "duty_role_separation", reason_id: "duties_not_role_separated" }, pair);
    assert.equal(result.detail.collisions.some(c => c.pair.join("/") === pair), true, pair);
  }
});

test("routing clause: the separation matrix is the accepted one, not 'everything must differ'", () => {
  // Q107.D1's recommendation is explicit that only OPPOSING roles are forbidden,
  // so these two must NOT block — a matrix that refused them would make honest
  // routings fail and would be a separation nobody settled.
  for (const overrides of [{ architect: ROLE_IDENTITIES.builder },
    { deployment_controller: ROLE_IDENTITIES.integration_controller }]) {
    const result = classifyRoutingIfAuthoritative(routingRequest({ role_identities: overrides }));
    assert.equal(result.blocking_clause, null, JSON.stringify(overrides));
    assert.equal(result.classification, V5_A03_CLASSIFICATIONS.routing);
  }
});

test("routing clause: a reviewer in the maker's session, or carrying the maker's context, is not fresh", () => {
  for (const patch of [{ reviewer_session_ref: "session:maker" },
    { context_binding: "inherited_from_maker" }]) {
    const assignments = routingRequest().assignments
      .map((a, index) => (index ? a : { ...a, ...patch }));
    const result = classifyRoutingIfAuthoritative(routingRequest({ assignments }));
    assert.deepEqual(blockedAt(result),
      { blocking_clause: "fresh_context", reason_id: "review_context_not_fresh" });
    assert.deepEqual([...result.detail.dimensions], ["architecture"]);
  }
});

test("routing clause: an unreadable request fails closed rather than deciding", () => {
  const cases = [
    [{ ...routingRequest(), surprise: true }, "unknown_field"],
    [{ ...routingRequest(), change_ref: "not a ref" }, "malformed_ref"],
    [{ ...routingRequest(), delivered_set_digest: "short" }, "malformed_digest"],
    [routingRequest({ assignments: [{ dimension: "astrology", reviewer_identity_ref: "actor:x",
      reviewer_session_ref: "session:x", context_binding: "fresh" }] }), "unknown_enum_member"],
    [{ ...routingRequest(), maker_session_ref: undefined }, "not_a_string"],
  ];
  for (const [request, code] of cases)
    assert.throws(() => classifyRoutingIfAuthoritative(request),
      error => error instanceof V5BoundaryError && error.code === code, code);
});

// ---------------------------------------------------------------------------
// CLAUSES — the complete finding set.
// ---------------------------------------------------------------------------

test("finding-set clause: a complete, whole-set, pre-repair enumeration is conditional", () => {
  const result = classifyFindingSetIfAuthoritative(findingSetRequest());
  assert.equal(result.classification, "would_be_whole_set_scoped_if_authoritative");
  assert.equal(result.blocking_clause, null);
});

test("finding-set clause: a dimension that reported nothing is a hole, not a clean review", () => {
  const submissions = findingSetRequest().submissions
    .map((s, index) => (index ? s : { ...s, state: "absent", finding_refs: [] }));
  const result = classifyFindingSetIfAuthoritative(findingSetRequest({ submissions }));
  assert.deepEqual(blockedAt(result),
    { blocking_clause: "dimension_submission", reason_id: "finding_set_dimension_absent" });
  assert.deepEqual([...result.detail.absent], ["architecture"]);
});

test("finding-set clause: a reviewer who read a narrower set produced a narrower review", () => {
  const submissions = findingSetRequest().submissions
    .map((s, index) => (index ? s : { ...s, reviewed_set_digest: OTHER_SET_DIGEST }));
  const result = classifyFindingSetIfAuthoritative(findingSetRequest({ submissions }));
  assert.deepEqual(blockedAt(result),
    { blocking_clause: "delivered_set_scope", reason_id: "review_scope_narrower_than_delivered_set" });
  assert.deepEqual([...result.detail.dimensions], ["architecture"]);
});

test("finding-set clause: findings enumerated after repair began are the spiral Q042 refuses", () => {
  const submissions = findingSetRequest().submissions
    .map((s, index) => (index ? s : { ...s, enumerated_before_repair: false }));
  const result = classifyFindingSetIfAuthoritative(findingSetRequest({ submissions }));
  assert.deepEqual(blockedAt(result),
    { blocking_clause: "enumeration_before_repair", reason_id: "finding_set_enumerated_after_repair" });
});

test("finding-set clause: a missing dimension and a duplicate are reported by the same clause", () => {
  const submissions = findingSetRequest().submissions
    .map(s => (s.dimension === "security" ? { ...s, dimension: "architecture" } : s));
  const result = classifyFindingSetIfAuthoritative(findingSetRequest({ submissions }));
  assert.equal(result.blocking_clause, "dimension_submission");
  assert.deepEqual([...result.detail.missing], ["security"]);
  assert.deepEqual([...result.detail.duplicated], ["architecture"]);
});

test("finding-set clause: an unsorted or repeating finding list is a shape error, never sorted for you", () => {
  const unsorted = findingSetRequest().submissions
    .map((s, index) => (index ? s : { ...s, finding_refs: ["finding:b", "finding:a"] }));
  assert.throws(() => classifyFindingSetIfAuthoritative(findingSetRequest({ submissions: unsorted })),
    error => error instanceof V5BoundaryError && error.code === "unsorted_list");
  const repeated = findingSetRequest().submissions
    .map((s, index) => (index ? s : { ...s, finding_refs: ["finding:a", "finding:a"] }));
  assert.throws(() => classifyFindingSetIfAuthoritative(findingSetRequest({ submissions: repeated })),
    error => error instanceof V5BoundaryError && error.code === "duplicate_member");
});

// ---------------------------------------------------------------------------
// CLAUSES — the round bound.
// ---------------------------------------------------------------------------

test("round clause: a third round cannot silently continue", () => {
  const result = classifyRoundIfAuthoritative(roundRequest({ requested_round_ordinal: 3,
    history: [historyEntry(), historyEntry({ round_ordinal: 2 })] }));
  assert.deepEqual(blockedAt(result),
    { blocking_clause: "round_limit", reason_id: "review_round_limit_exhausted" });
  assert.equal(result.detail.required_transition, "stronger_adjudication");
  assert.equal(result.detail.round_limit, V5_MAX_REVIEW_ROUNDS);
});

test("round clause: round two's batch repair and regression are UNREADABLE, not satisfied", () => {
  // The PR 987 defect: one arbitrary history entry carrying the prior ordinal
  // used to satisfy this clause. Q042.D1 wants the prior round's complete
  // finding set, its repair as one batch, and a regression bound to that batch.
  // None of the three is in a history a caller wrote.
  for (const history of [
    [historyEntry()],
    [historyEntry({ regression: { suite_ref: "suite:unit", checks_executed: [] } })],
    [],
  ]) {
    const result = classifyRoundIfAuthoritative(roundRequest({ history }));
    assert.deepEqual(blockedAt(result), { blocking_clause: "prior_round_batch_regression",
      reason_id: "prior_round_batch_regression_unreadable" }, JSON.stringify(history));
    assert.equal(result.detail.required_seam, "seam:review-round-ledger");
    assert.equal(result.detail.unreadable_facts.length, 3);
  }
});

test("round clause: only a first round with a clean history is conditionally within the bound", () => {
  const result = classifyRoundIfAuthoritative(roundRequest({ requested_round_ordinal: 1, history: [] }));
  assert.equal(result.classification, "would_be_within_bound_if_authoritative");
  assert.equal(result.blocking_clause, null);
});

test("round clause: a dispute adjudication closed does not reopen as another round", () => {
  const result = classifyRoundIfAuthoritative(roundRequest({ adjudication_recorded: true,
    requested_round_ordinal: 1, history: [] }));
  assert.deepEqual(blockedAt(result), { blocking_clause: "round_reopened_after_adjudication",
    reason_id: "review_round_reopened_after_adjudication" });
});

test("round clause: a finding marked resolved and reported again is a repair that did not take", () => {
  const result = classifyRoundIfAuthoritative(roundRequest({ history: [
    historyEntry({ finding_refs: ["finding:one"], resolved_finding_refs: ["finding:one"] }),
    historyEntry({ round_ordinal: 2, finding_refs: ["finding:one"],
      post_repair_artifact_digest: "d".repeat(64) }),
  ] }));
  assert.deepEqual(blockedAt(result),
    { blocking_clause: "round_drift", reason_id: "review_round_drift_detected" });
  assert.deepEqual([...result.detail.fired], ["repeated_finding"]);
  assert.deepEqual([...result.detail.regressions.repeated_finding], ["finding:one"]);
});

test("round clause: a repair that restores an already-rejected tree is going round, not forward", () => {
  const result = classifyRoundIfAuthoritative(roundRequest({ history: [
    historyEntry({ post_repair_artifact_digest: "e".repeat(64) }),
    historyEntry({ round_ordinal: 2, state: "no_changes_required", finding_refs: [],
      reviewer_identity_ref: "actor:reviewer-two",
      post_repair_artifact_digest: "e".repeat(64) }),
  ] }));
  assert.equal(result.blocking_clause, "round_drift");
  assert.deepEqual([...result.detail.fired], ["circular_reversion"]);
});

test("round clause: a reviewer giving both answers for one dimension is unstable, not decisive", () => {
  const result = classifyRoundIfAuthoritative(roundRequest({ history: [
    historyEntry({ finding_refs: [] , state: "no_changes_required" }),
    historyEntry({ round_ordinal: 2, state: "changes_required", finding_refs: [],
      post_repair_artifact_digest: "f".repeat(64) }),
  ] }));
  assert.equal(result.blocking_clause, "round_drift");
  assert.deepEqual([...result.detail.fired], ["reviewer_instability"]);
});

test("round clause: test weakening is a ROUND-to-ROUND shrink, not an entry-to-entry one", () => {
  // THE DEFECT THIS REPLACES: two dimensions in the SAME round reporting
  // different partitions of one suite used to read as weakening, because the
  // comparison was entry to entry and each entry's list replaced the baseline.
  const sameRound = [
    historyEntry({ dimension: "architecture", regression: { suite_ref: "suite:unit",
      checks_executed: ["alpha", "beta"] } }),
    historyEntry({ dimension: "security", reviewer_identity_ref: "actor:reviewer-security",
      regression: { suite_ref: "suite:unit", checks_executed: ["gamma"] } }),
  ];
  assert.deepEqual([...detectRoundRegressions(sameRound).test_weakening], [],
    "two dimensions splitting one round's suite is not a weakened test");

  // AND THE CASE THAT MUST STILL FIRE: the next round's aggregate is smaller
  // than the previous round's aggregate for a suite both rounds ran.
  const shrinking = [
    ...sameRound,
    historyEntry({ round_ordinal: 2, dimension: "architecture", finding_refs: [],
      post_repair_artifact_digest: "1".repeat(64),
      regression: { suite_ref: "suite:unit", checks_executed: ["alpha", "gamma"] } }),
  ];
  assert.deepEqual([...detectRoundRegressions(shrinking).test_weakening], ["suite:unit|beta"]);
  const blocked = classifyRoundIfAuthoritative(roundRequest({ history: shrinking }));
  assert.equal(blocked.blocking_clause, "round_drift");
  assert.deepEqual([...blocked.detail.fired], ["test_weakening"]);
});

test("round clause: the obligation at each ordinal is the accepted bound, and nothing else", () => {
  assert.deepEqual({ ...reviewRoundObligation(1) }, { ordinal: 1, round_limit: 2,
    within_round_limit: true, required_transition: "independent_review_round",
    is_not_authority: true, evidence_source: V5_A03_CLASSIFIER_EVIDENCE_SOURCE });
  assert.equal(reviewRoundObligation(2).within_round_limit, true);
  assert.equal(reviewRoundObligation(3).within_round_limit, false);
  assert.equal(reviewRoundObligation(3).required_transition, "stronger_adjudication");
  assert.equal(reviewRoundObligation(0).within_round_limit, false);
});

test("round clause: the four drift detectors are silent on a clean history", () => {
  const clean = detectRoundRegressions([historyEntry(),
    historyEntry({ round_ordinal: 2, state: "no_changes_required", finding_refs: [], resolved_finding_refs: ["finding:one"],
      reviewer_identity_ref: "actor:reviewer-two", post_repair_artifact_digest: "2".repeat(64) })]);
  for (const name of V5_ROUND_REGRESSION_CLASSES) assert.deepEqual([...clean[name]], [], name);
  assert.equal(clean.is_not_authority, true);
});

// ---------------------------------------------------------------------------
// CLAUSES — bounded adjudication and the receipt shape.
// ---------------------------------------------------------------------------

test("adjudication clause: a well-formed dispute is conditional, and states no outcome", () => {
  const result = classifyAdjudicationIfAuthoritative(adjudicationRequest());
  assert.equal(result.classification, "would_be_adjudicable_if_authoritative");
  assert.equal("outcome" in result, false);
  assert.equal("adjudicated_outcome" in result, false);
});

test("adjudication clause: the adjudicator may not be anyone in the dispute", () => {
  for (const [identity, as] of [["actor:builder", "builder"], ["actor:releaser", "deployment_controller"],
    ["actor:reviewer-security", "reviewer"]]) {
    const result = classifyAdjudicationIfAuthoritative(
      adjudicationRequest({ adjudicator_identity_ref: identity }));
    assert.deepEqual(blockedAt(result), { blocking_clause: "adjudicator_separation",
      reason_id: "adjudicator_is_a_party_to_the_dispute" }, identity);
    assert.deepEqual([...result.detail.also_a_party_as], [as]);
  }
});

test("adjudication clause: only the accepted stronger-adjudicator role adjudicates", () => {
  for (const role of ["peer", "reviewer", "stronger", "pass"]) {
    const result = classifyAdjudicationIfAuthoritative(adjudicationRequest({ adjudicator_role: role }));
    assert.deepEqual(blockedAt(result),
      { blocking_clause: "adjudicator_role", reason_id: "adjudicator_role_unknown" }, role);
  }
});

test("adjudication clause: reaching for a judge before the bound is spent, or with nothing in dispute", () => {
  const early = classifyAdjudicationIfAuthoritative(adjudicationRequest({ rounds_completed: 1 }));
  assert.deepEqual(blockedAt(early),
    { blocking_clause: "rounds_before_adjudication", reason_id: "adjudication_before_round_limit" });
  const empty = classifyAdjudicationIfAuthoritative(adjudicationRequest({ disputed_finding_refs: [] }));
  assert.deepEqual(blockedAt(empty),
    { blocking_clause: "disputed_set_empty", reason_id: "adjudication_disputed_set_empty" });
});

test("receipt clause: a well-shaped receipt is only ever SHAPED like one", () => {
  const body = receiptBody();
  const result = classifyAdjudicationReceiptIfAuthoritative(V5_ADJUDICATION_RECEIPT_KIND,
    receiptRefFor(body), body, receiptBinding());
  assert.equal(result.classification, "would_be_verifiable_if_authoritative");
  assert.equal(result.is_not_authority, true);
  // The outcome the body carried is not echoed back, even here.
  assert.equal(stringValues(result).includes("quarantine"), false);
  assert.equal("ok" in result, false);
});

test("receipt clause: every way a forged or mismatched receipt fails is named", () => {
  const binding = receiptBinding();
  const body = receiptBody();
  const cases = [
    ["adjudication-receipt:not-a-digest", body, "receipt_content_addressed"],
    ["receipt:" + digest(body).slice(7), body, "receipt_content_addressed"],
    [receiptRefFor(body), null, "receipt_resolvable"],
    [receiptRefFor(body), receiptBody({ rounds_completed: 7 }), "receipt_digest"],
    [receiptRefFor(receiptBody({ kind: "review" })), receiptBody({ kind: "review" }), "receipt_kind"],
    [receiptRefFor(receiptBody({ change_ref: "change:other" })), receiptBody({ change_ref: "change:other" }),
      "receipt_change_binding"],
    [receiptRefFor(receiptBody({ delivered_set_digest: OTHER_SET_DIGEST })),
      receiptBody({ delivered_set_digest: OTHER_SET_DIGEST }), "receipt_delivered_set_binding"],
    [receiptRefFor(receiptBody({ outcome: "maybe" })), receiptBody({ outcome: "maybe" }),
      "receipt_outcome_vocabulary"],
    [receiptRefFor(receiptBody({ adjudicator_identity_ref: "actor:builder" })),
      receiptBody({ adjudicator_identity_ref: "actor:builder" }), "receipt_adjudicator_separation"],
  ];
  for (const [receiptRef, value, clause] of cases) {
    const result = classifyAdjudicationReceiptIfAuthoritative(V5_ADJUDICATION_RECEIPT_KIND,
      receiptRef, value, binding);
    assert.deepEqual(blockedAt(result),
      { blocking_clause: clause, reason_id: V5_A03_CLAUSE_REASONS[clause] }, clause);
  }
  // The closed outcome vocabulary is three OPAQUE codes. None of them is one of
  // Q042.D1's three words, and none of them contains one: the settled words live
  // in a comment in the vocabulary module, where no consumer reads them.
  assert.deepEqual([...V5_ADJUDICATION_OUTCOME_CODE_SET], [
    "adjudication-outcome:adverse-if-authoritative",
    "adjudication-outcome:favorable-if-authoritative",
    "adjudication-outcome:isolating-if-authoritative",
  ]);
  assert.deepEqual(Object.keys(V5_ADJUDICATION_OUTCOME_CODES).sort(),
    ["adverse", "favorable", "isolating"]);
  assert.deepEqual(privilegedStrings(V5_ADJUDICATION_OUTCOME_CODES), []);
});

// ---------------------------------------------------------------------------
// GUARDS.
// ---------------------------------------------------------------------------

test("guard: every classifier answers in the conditional and carries no privileged value", () => {
  const answers = [
    classifyRoutingIfAuthoritative(routingRequest()),
    classifyRoutingIfAuthoritative(routingRequest({ assignments: [] })),
    classifyFindingSetIfAuthoritative(findingSetRequest()),
    classifyRoundIfAuthoritative(roundRequest({ requested_round_ordinal: 1, history: [] })),
    classifyRoundIfAuthoritative(roundRequest()),
    classifyAdjudicationIfAuthoritative(adjudicationRequest()),
    // A caller that calls its own role "pass" must not see that word anywhere in
    // the answer: a privileged word in a result is indistinguishable from one
    // the module produced.
    classifyAdjudicationIfAuthoritative(adjudicationRequest({ adjudicator_role: "pass" })),
    classifyRoutingIfAuthoritative(routingRequest({ maker_session_ref: "session:pass",
      role_identities: { builder: "actor:allow" } })),
    classifyAdjudicationReceiptIfAuthoritative(V5_ADJUDICATION_RECEIPT_KIND,
      receiptRefFor(receiptBody()), receiptBody(), receiptBinding()),
  ];
  for (const answer of answers) {
    assert.equal(answer.classification === V5_A03_CLASSIFICATIONS.refuse ||
      answer.classification.startsWith("would_be_"), true, answer.classification);
    assert.equal(answer.classification === "would_refuse" ||
      answer.classification.endsWith("_if_authoritative"), true, answer.classification);
    assert.equal(answer.is_not_authority, true);
    assert.equal(answer.evidence_source, V5_A03_CLASSIFIER_EVIDENCE_SOURCE);
    assert.equal(Object.isFrozen(answer), true);
    for (const value of stringValues(answer))
      assert.equal(PRIVILEGED_VALUES.has(value), false,
        `${answer.classification} answered with the privileged word ${value}`);
  }
  for (const value of Object.values(V5_A03_CLASSIFICATIONS))
    assert.equal(value === "would_refuse" || /^would_be_.*_if_authoritative$/.test(value), true, value);
});

test("guard: the privileged sweep would catch a leak — the detector is not vacuous", () => {
  // A mutation check on the check itself: if privilegedFindings cannot see a
  // planted value, every sweep above proves nothing.
  assert.deepEqual(privilegedFindings({ outcome: "pass" }), ["$.outcome === pass"]);
  assert.deepEqual(privilegedFindings({ ok: true }), ["$.ok === true"]);
  assert.deepEqual(privilegedFindings({ would_be_fine: 1 }),
    ["$.would_be_fine is a classifier field on the public surface"]);
  assert.deepEqual(privilegedFindings({ nested: [{ decision: "allow" }] }),
    ["$.nested[0].decision === allow"]);
});

test("guard: no test-only entry sits in the production source directory", () => {
  const directory = path.join(REPO_ROOT, "mcp-server/src");
  const strays = readdirSync(directory).filter(name => /\.(testonly|testhelper)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");
  // And the file this slice's classifiers used to live in is gone, not renamed.
  assert.equal(readdirSync(directory).includes("complete-set-review-a03.internal.v5.js"), false);
});

test("guard: production cannot reach the classifiers — parsed, not grepped", () => {
  // Node's own ES-module parser, via vm.SourceTextModule, which exposes the real
  // import specifiers of each source file. A regex over the text would be fooled
  // by a comment, a string or an unusual line break; this is the same parse the
  // runtime performs.
  const script = `
    import { readdirSync, readFileSync, statSync } from "node:fs";
    import path from "node:path";
    import vm from "node:vm";
    const root = process.env.A03_REPO_ROOT;
    const helper = "complete-set-review-a03-classifiers.v5.testhelper.mjs";
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
    const srcReachingTest = [];
    let parsed = 0;
    let srcParsed = 0;
    for (const file of files) {
      let record;
      try { record = new vm.SourceTextModule(readFileSync(file, "utf8"), { identifier: file }); }
      catch { continue; }
      parsed += 1;
      const relative = path.relative(root, file);
      const specifiers = record.dependencySpecifiers;
      if (specifiers.some(spec => spec.endsWith(helper))) importers.push(relative);
      if (relative.startsWith("mcp-server/src/")) {
        srcParsed += 1;
        if (specifiers.some(spec => spec.includes("/test/") || spec.startsWith("../test") ||
            spec.includes(".testhelper.") || spec.includes(".testonly.")))
          srcReachingTest.push(relative);
      }
    }
    console.log(JSON.stringify({ importers: importers.sort(), srcReachingTest: srcReachingTest.sort(),
      parsed, srcParsed }));
  `;
  const output = execFileSync(process.execPath,
    ["--experimental-vm-modules", "--input-type=module", "--eval", script],
    { env: { ...process.env, A03_REPO_ROOT: REPO_ROOT }, encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"] });
  const { importers, srcReachingTest, parsed, srcParsed } = JSON.parse(output.trim().split("\n").pop());

  // The sweep is only worth anything if it actually parsed the tree.
  assert.equal(parsed > 100, true, `only ${parsed} modules parsed`);
  assert.equal(srcParsed > 50, true, `only ${srcParsed} production modules parsed`);
  // No production module reaches into the test tree by ANY route, not just this
  // helper: that is the property "internal" in a filename never had.
  assert.deepEqual(srcReachingTest, []);
  // And the classifier entry has exactly one importer: this test file.
  assert.deepEqual(importers, ["mcp-server/test/complete-set-review-a03.v5.test.mjs"]);
});

test("guard: the parsed export names of the public module are the runtime surface", () => {
  // Same parser, now LINKED against the real files on disk rather than stubs, so
  // the export names come from Node resolving the module graph exactly as the
  // runtime would. No fallback: if the link fails, the guard fails.
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

  assert.deepEqual(names, EXPECTED_PUBLIC_EXPORTS, "the parsed surface is the declared surface");
  assert.deepEqual(names, Object.keys(a03).sort(), "the parsed surface is the runtime surface");
  for (const name of names) {
    assert.equal(/^(classify|evaluate|derive)/.test(name), false, name);
    assert.equal(name.startsWith("normalize"), false, name);
  }
  for (const gone of ["reviewDimensionGap", "roleSeparationCollisions", "detectRoundRegressions",
    "reviewRoundObligation", "V5_A03_CLASSIFICATIONS", "V5_CHECK_STATES"])
    assert.equal(names.includes(gone), false, `${gone} is still on the public surface`);
});

test("guard: every reason this slice may give is produced by something here", () => {
  const produced = new Set();
  const collect = result => { if (result?.reason_id) produced.add(result.reason_id); };

  for (const [, fn] of PUBLIC_ANSWERS) collect(fn(routingRequest()));

  const assignments = routingRequest().assignments;
  collect(classifyRoutingIfAuthoritative(routingRequest({ assignments: assignments.slice(1) })));
  collect(classifyRoutingIfAuthoritative(routingRequest({ assignments: assignments
    .map((a, i) => (i ? a : { ...a, reviewer_identity_ref: ROLE_IDENTITIES.builder })) })));
  collect(classifyRoutingIfAuthoritative(
    routingRequest({ role_identities: { adjudicator: ROLE_IDENTITIES.builder } })));
  collect(classifyRoutingIfAuthoritative(routingRequest({ assignments: assignments
    .map((a, i) => (i ? a : { ...a, context_binding: "inherited_from_maker" })) })));

  const submissions = findingSetRequest().submissions;
  collect(classifyFindingSetIfAuthoritative(findingSetRequest({ submissions: submissions.slice(1) })));
  collect(classifyFindingSetIfAuthoritative(findingSetRequest({ submissions: submissions
    .map((s, i) => (i ? s : { ...s, reviewed_set_digest: OTHER_SET_DIGEST })) })));
  collect(classifyFindingSetIfAuthoritative(findingSetRequest({ submissions: submissions
    .map((s, i) => (i ? s : { ...s, enumerated_before_repair: false })) })));

  collect(classifyRoundIfAuthoritative(roundRequest()));
  collect(classifyRoundIfAuthoritative(roundRequest({ requested_round_ordinal: 3 })));
  collect(classifyRoundIfAuthoritative(roundRequest({ adjudication_recorded: true })));
  collect(classifyRoundIfAuthoritative(roundRequest({ history: [
    historyEntry({ finding_refs: ["finding:one"], resolved_finding_refs: ["finding:one"] }),
    historyEntry({ round_ordinal: 2, finding_refs: ["finding:one"],
      post_repair_artifact_digest: "9".repeat(64) })] })));

  collect(classifyAdjudicationIfAuthoritative(adjudicationRequest({ adjudicator_role: "peer" })));
  collect(classifyAdjudicationIfAuthoritative(
    adjudicationRequest({ adjudicator_identity_ref: "actor:builder" })));
  collect(classifyAdjudicationIfAuthoritative(adjudicationRequest({ rounds_completed: 0 })));
  collect(classifyAdjudicationIfAuthoritative(adjudicationRequest({ disputed_finding_refs: [] })));

  const binding = receiptBinding();
  const body = receiptBody();
  for (const [receiptRef, value] of [
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
  ]) collect(classifyAdjudicationReceiptIfAuthoritative(V5_ADJUDICATION_RECEIPT_KIND, receiptRef,
    value, binding));

  assert.deepEqual([...produced].sort(), [...V5_A03_REASON_IDS],
    "a reason nothing can produce is a reason nobody can act on");
});

test("guard: every declared clause order ends at the seam it is owed", () => {
  for (const order of [V5_ROUTING_CHECKS, V5_FINDING_SET_CHECKS, V5_ROUND_BOUND_CHECKS,
    V5_ADJUDICATION_CHECKS]) {
    assert.equal(new Set(order).size, order.length);
    assert.match(order[order.length - 1], /_registry$|_ledger$|_store$/);
    assert.equal(order.slice(0, -1).some(check => /_registry$|_ledger$|_store$/.test(check)), false);
  }
  // Drift runs BEFORE the unreadable prior-round clause, so a caller whose
  // history is visibly drifting hears that rather than the missing ledger.
  assert.equal(V5_ROUND_BOUND_CHECKS.indexOf("round_drift") <
    V5_ROUND_BOUND_CHECKS.indexOf("prior_round_batch_regression"), true);
  assert.deepEqual([...V5_NON_REVIEWER_ROLES].sort(), [...V5_NON_REVIEWER_ROLES]);
  assert.equal(V5_OPPOSING_ROLE_PAIRS.length, 9);
  for (const pair of V5_OPPOSING_ROLE_PAIRS) assert.deepEqual([...pair].sort(), [...pair]);
});

test("clause predicates: a gap report and a collision list are facts, not clearances", () => {
  assert.deepEqual({ ...reviewDimensionGap([...V5_REVIEW_DIMENSIONS]) }, { missing: [], duplicated: [] });
  assert.deepEqual([...reviewDimensionGap(["architecture", "architecture"]).duplicated], ["architecture"]);
  assert.equal(reviewDimensionGap([]).missing.length, 11);
  assert.deepEqual(roleSeparationCollisions(ROLE_IDENTITIES, ["actor:reviewer-x"]), []);
  assert.deepEqual(roleSeparationCollisions(ROLE_IDENTITIES, [ROLE_IDENTITIES.builder])
    .map(entry => ({ pair: [...entry.pair], identity_ref: entry.identity_ref })),
  [{ pair: ["builder", "reviewer"], identity_ref: "actor:builder" }]);
  assert.equal(V5_A03_SCHEMA_VERSION, "doctorcre-v5-exhaustive-set-review.v1");
});
