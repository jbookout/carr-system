// V5-A02 STEP A — THE GATE ZERO PRODUCER, proved the only way a seam that takes
// no argument can be proved: by staging a copy of src, editing the lines a
// human would edit, and reading what the whole module then answers.
//
// THERE IS NO INJECTION POINT AND THAT IS THE SUBJECT. `v5A02GateZeroEmitOutcome`
// has arity zero. It cannot be handed evidence, a hash, an identity, a revision,
// a reader or a store; the standing rule of 2026-09-11 forbids a public function
// that turns a caller's description of evidence into a verdict, under any name,
// and a function with nowhere to put one cannot have the defect. So every case
// below is a SOURCE EDIT in a throwaway tree under node_modules/.cache:
//
//   * the run binding's five pasted lines, which name the rows a run stands on;
//   * card 9's seat declaration, which is what binds the seam at all;
//   * the three `decision_id:` lines, which are what bind the readers;
//   * the store module file, replaced by ./gate-zero-producer-stores.v5.fixture.mjs.
//
// Nothing in src reaches those trees, no argument selects one, and no environment
// variable points at one. What runs in each is the real producer, the real
// clauses, the real readers and the real ruling gate over known rows.
//
// THE CONTROLS, and each is named where it is asserted:
//   1. bound seam + every row present            -> passable, a digest, an instant
//   2. each of the three clauses turned off      -> the gate refuses or fails
//   3. a tampered predecessor receipt hash       -> refused against a signature
//   4. the seat back to unstaffed                -> the whole slice goes dark
//   5. an injected non-green conclusion          -> status "fail", denials observed
//   6. subject maker == the reviewing seat       -> denied
//   7. a caller-supplied anything                -> unreachable, by parse and by call
//
//   node --test mcp-server/test/gate-zero-producer.v5.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { types } from "node:util";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  CONSUMER_GATE_RECEIPT_FIELDS,
  CONSUMER_GATE_RECEIPT_SCHEMA,
  GATE_ZERO_STEP_REF,
} from "../src/benchmark-minimum.v5.js";
import { V5_A02_GATE_ZERO_REASON_IDS } from "../src/gate-zero-assurance.v5.js";
import * as producer from "../src/gate-zero-producer.v5.js";
import {
  ACCEPTED_HASHES, CANARY_ABSENT, CANARY_JOINING, CANARY_UNBOUND, FORGED_HASH,
  REVISION_ALL_SUCCEED, REVISION_FAILED_ANCESTOR, REVISION_UNFINISHED, SERVICE_KEY,
} from "./gate-zero-producer-stores.v5.fixture.mjs";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const PRODUCER_FILE = "gate-zero-producer.v5.js";
const REGISTRATION_FILE = "gate-zero-producer-registration.v5.js";
const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
const STORES_FILE = "gate-zero-seam-stores.v5.js";
const GATE_FILE = "gate-zero-assurance.v5.js";
const FIXTURE_STORE =
  fileURLToPath(new URL("./gate-zero-producer-stores.v5.fixture.mjs", import.meta.url));

/** The seat that reviews, and one that is not it. Both registered in identity.js. */
const REVIEWING_SEAT_ACTOR = "codex-reviewer";
const SUBJECT_MAKER_ACTOR = "claude";

const staged = [];
after(() => {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// THE STAGING. Every line it edits is a line a human edits, and every edit is
// asserted to have matched exactly once before it is made — a staging whose
// anchor silently stopped matching would leave the case proving nothing, which
// is the failure mode the seam suites already learned once.
// ---------------------------------------------------------------------------

const UNNAMED = Object.freeze({
  head_revision: "  head_revision: null,\n",
  scheduler_service_key: "  scheduler_service_key: null,\n",
  scheduler_canary_run_key: "  scheduler_canary_run_key: null,\n",
  subject_maker_actor_id: "  subject_maker_actor_id: null,\n",
  wr40: '    "step:wr40-repository-outcome": null,\n',
  wr46: '    "step:wr46-dissolution-outcome": null,\n',
  wr54: '    "step:wr54-backup-recovery-outcome": null,\n',
});

const STAFFED_SEAT_LINE = '  holder_ref: "seat:codex-reviewer:gpt-5.6-sol",\n';
const UNSTAFFED_SEAT_LINE = "  holder_ref: null,\n";
const RULED_DECISION_LINES = Object.freeze([
  '    decision_id: "16c7cdfb-b675-4b6a-bbff-4bbdab46baf8",\n',
  '    decision_id: "f7c486d6-5bee-4c4c-a76f-c0f162f66db8",\n',
  '    decision_id: "87e9e11e-64b2-49b3-a6aa-4901c24eaa91",\n',
]);
const NULL_DECISION_LINE = "    decision_id: null,\n";

/** The binding a clean run stands on: every address a row exists for. */
function cleanBinding() {
  return {
    head_revision: REVISION_ALL_SUCCEED,
    scheduler_service_key: SERVICE_KEY,
    scheduler_canary_run_key: CANARY_JOINING,
    subject_maker_actor_id: SUBJECT_MAKER_ACTOR,
    wr40: ACCEPTED_HASHES["step:wr40-repository-outcome"],
    wr46: ACCEPTED_HASHES["step:wr46-dissolution-outcome"],
    wr54: ACCEPTED_HASHES["step:wr54-backup-recovery-outcome"],
  };
}

function pastedLine(key, value) {
  const quoted = JSON.stringify(value);
  switch (key) {
    case "head_revision": return `  head_revision: ${quoted},\n`;
    case "scheduler_service_key": return `  scheduler_service_key: ${quoted},\n`;
    case "scheduler_canary_run_key": return `  scheduler_canary_run_key: ${quoted},\n`;
    case "subject_maker_actor_id": return `  subject_maker_actor_id: ${quoted},\n`;
    case "wr40": return `    "step:wr40-repository-outcome": ${quoted},\n`;
    case "wr46": return `    "step:wr46-dissolution-outcome": ${quoted},\n`;
    case "wr54": return `    "step:wr54-backup-recovery-outcome": ${quoted},\n`;
    default: throw new Error(`${key} is not a line of the run binding`);
  }
}

/**
 * A copy of src with the run binding pasted, the store substituted, and — when a
 * case asks — the seat unstaffed or a ruling withdrawn.
 *
 * `binding: null` leaves every line at `null`, which is the tree src ships.
 */
function stageTree({ binding = cleanBinding(), staffedSeat = true,
  withdrawnCards = [], substituteStore = true } = {}) {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-producer-"));
  staged.push(base);
  const target = join(base, "src");
  cpSync(SRC, target, { recursive: true });

  if (binding !== null) {
    const path = join(target, PRODUCER_FILE);
    let source = readFileSync(path, "utf8");
    for (const [key, anchor] of Object.entries(UNNAMED)) {
      assert.equal(source.split(anchor).length - 1, 1,
        `the staging anchor no longer matches the run binding's ${key} line`);
      source = source.replace(anchor, pastedLine(key, binding[key]));
    }
    writeFileSync(path, source);
  }

  if (!staffedSeat) {
    const path = join(target, REGISTRATION_FILE);
    const source = readFileSync(path, "utf8");
    assert.equal(source.split(STAFFED_SEAT_LINE).length - 1, 1,
      "the staging anchor no longer matches the seat declaration");
    writeFileSync(path, source.replace(STAFFED_SEAT_LINE, UNSTAFFED_SEAT_LINE));
  }

  if (withdrawnCards.length > 0) {
    const path = join(target, RULINGS_FILE);
    let source = readFileSync(path, "utf8");
    RULED_DECISION_LINES.forEach((anchor, index) => {
      assert.equal(source.split(anchor).length - 1, 1,
        `the staging anchor no longer matches card ${11 + index}'s ruling line`);
      if (withdrawnCards.includes(index)) source = source.replace(anchor, NULL_DECISION_LINE);
    });
    writeFileSync(path, source);
  }

  if (substituteStore) cpSync(FIXTURE_STORE, join(target, STORES_FILE));
  return target;
}

const gateOfTree = target => import(pathToFileURL(join(target, GATE_FILE)).href);
const producerOfTree = target => import(pathToFileURL(join(target, PRODUCER_FILE)).href);

/** What the gate answers in a staged tree, which is what a consumer would get. */
async function emitFrom(options) {
  const gate = await gateOfTree(stageTree(options));
  return gate.emitGateZeroOutcome();
}

// ===========================================================================
// CONTROL 1 — the bound seam over rows that are all there.
// ===========================================================================

test("PRODUCED: with every row present the gate is passable, with a real digest and instant", async () => {
  const emitted = await emitFrom({});
  assert.equal(emitted.passable, true, emitted.unavailable_because ?? emitted.reason_id);
  assert.equal(emitted.status, "outcome_produced");
  assert.equal(emitted.decision, "report");
  assert.equal(emitted.reason_id, null);
  assert.equal(emitted.producer_bound, true);
  assert.deepEqual(emitted.owed_seams, []);
  assert.equal(emitted.receipt_status, "pass");

  // A REAL DIGEST, by the stated recipe, over the receipt that is right there —
  // recomputed here rather than trusted, so a producer that stamped a constant
  // or hashed something else is red.
  assert.match(emitted.outcome_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(emitted.outcome_digest, digest(emitted.receipt));
  assert.equal(emitted.outcome_digest_recipe.domain_tag, null);
  assert.equal(emitted.outcome_digest_recipe.schema_ref, CONSUMER_GATE_RECEIPT_SCHEMA);
  assert.equal(emitted.outcome_digest_recipe.self_digest_excluded, true);
  assert.equal(Object.hasOwn(emitted.receipt, "outcome_digest"), false,
    "the receipt carries its own digest, which the canonicalization rule forbids");

  // A REAL INSTANT, and it is the receipt's own. This value is the zero of the
  // v5 clock: everything downstream is timestamped strictly after it.
  assert.match(emitted.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  assert.equal(emitted.observed_at, emitted.receipt.observed_at);
  assert.ok(Date.parse(emitted.receipt.ttl_expires_at) > Date.parse(emitted.observed_at));

  // AND THE JOIN IS THE THREE CLAUSES, each held over rows.
  assert.deepEqual(Object.keys(emitted.join).sort(),
    ["gate_graph", "predecessor_join", "scheduler_canary"]);
  for (const [name, clause] of Object.entries(emitted.join))
    assert.equal(clause.state, "held", name);
  assert.equal(emitted.negative_admission.result, "all_required_denials_observed");
  assert.equal(emitted.negative_admission.case_count, 7);
  assert.deepEqual(emitted.negative_admission.missed_cases, []);
  assert.deepEqual(emitted.effects, V5_NO_EFFECTS);
  assert.equal(emitted.persisted, false);
  assert.equal(emitted.durable_outcome_record_required, true);
});

test("RECEIPT: all twenty-one fields of consumer-gate-receipt.v1, and nothing else", async () => {
  const { receipt } = await emitFrom({});
  assert.deepEqual(Object.keys(receipt).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.equal(Object.keys(receipt).length, 21);

  assert.equal(receipt.gate_id, "gate-zero-read-only-accepted");
  assert.equal(receipt.receipt_producer_step_ref, GATE_ZERO_STEP_REF);
  assert.equal(receipt.producer_role, "independent_control_plane_oracle");
  assert.equal(receipt.independent_oracle_ref, "oracle:gate-producer:gate-zero-read-only");
  assert.equal(receipt.oracle_version, "1.0.0");
  assert.equal(receipt.subject_environment, "candidate");
  assert.equal(receipt.evidence_scope, "candidate-and-test");
  assert.equal(receipt.status, "pass");
  assert.equal(receipt.negative_admission_result, "all_required_denials_observed");

  for (const field of ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"])
    assert.match(receipt[field], /^sha256:[0-9a-f]{64}$/, field);
  // FIVE DISTINCT DIGESTS. A producer that computed one preimage and used it
  // five times would satisfy every pattern above and mean nothing.
  const digests = ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"].map(field => receipt[field]);
  assert.equal(new Set(digests).size, 5, "two of the five digests are the same value");

  // r7's evidence_ref shape, and it is lowercase-only — a single capital is
  // refused by benchmark-minimum with a bare error naming no field.
  assert.match(receipt.evidence_ref, /^safe:[a-z0-9][a-z0-9:_./-]*$/);
  assert.ok(receipt.comparator.length >= 5 && receipt.comparator.length <= 300);

  // THE THREE IDENTITIES, each an authenticated-receipt-identity.v1, and the
  // independence r7's identity rule requires.
  for (const field of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) {
    assert.deepEqual(Object.keys(receipt[field]).sort(),
      ["actor_id", "authority_class", "session_ref"], field);
    assert.match(receipt[field].session_ref, /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/, field);
  }
  assert.equal(receipt.producer_identity.actor_id, REVIEWING_SEAT_ACTOR);
  assert.equal(receipt.evaluator_identity.actor_id, REVIEWING_SEAT_ACTOR);
  assert.equal(receipt.subject_maker_identity.actor_id, SUBJECT_MAKER_ACTOR);
  // DERIVED BY identity.js, not typed here: the seat's class is what that module
  // resolves a review-token machine identity to.
  assert.equal(receipt.producer_identity.authority_class, "review_agent");
  assert.notEqual(receipt.subject_maker_identity.actor_id, receipt.producer_identity.actor_id);
  assert.notEqual(receipt.subject_maker_identity.session_ref, receipt.producer_identity.session_ref);
});

test("DIGEST: the outcome digest moves when the evidence does, and not otherwise", async () => {
  const first = await emitFrom({});
  const second = await emitFrom({});
  // The receipts differ only by their instants, so the two runs are compared on
  // the five bound digests rather than on the whole object.
  for (const field of ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"])
    assert.equal(first.receipt[field], second.receipt[field], field);

  // A DIFFERENT CANDIDATE IS A DIFFERENT SUBJECT. Point the run at another
  // revision and the candidate and fixture-set digests both move, because both
  // stand on the address the readers were aimed at.
  const elsewhere = await emitFrom({
    binding: { ...cleanBinding(), head_revision: REVISION_FAILED_ANCESTOR } });
  assert.notEqual(elsewhere.receipt.candidate_digest, first.receipt.candidate_digest);
  assert.notEqual(elsewhere.receipt.fixture_set_digest, first.receipt.fixture_set_digest);
  assert.equal(elsewhere.receipt.subject_digest, first.receipt.subject_digest,
    "the subject is the gate, not the candidate, and must not move with it");
});

// ===========================================================================
// CONTROL 2 — each clause turned off, one at a time.
// ===========================================================================

/**
 * ONE ADDRESS MOVED PER CASE, and each moves the address of exactly one clause.
 * The rows behind the other two are untouched, so a clause that stopped being
 * read fails on one case rather than on none — which is the property a
 * conjunction over three booleans would not have.
 */
const CLAUSE_FALSIFIERS = Object.freeze([
  {
    name: "the predecessor clause, with one acceptance receipt hash forged",
    binding: { wr46: FORGED_HASH },
    reason: "gate_zero_predecessor_clause_failed",
    clause: "predecessor_join",
  },
  {
    name: "the scheduler clause, with a canary bound to no receipt of its own",
    binding: { scheduler_canary_run_key: CANARY_UNBOUND },
    reason: "gate_zero_scheduler_clause_failed",
    clause: "scheduler_canary",
  },
  {
    name: "the gate-graph clause, with a green gate over a failed ancestor",
    binding: { head_revision: REVISION_FAILED_ANCESTOR },
    reason: "gate_zero_gate_graph_clause_failed",
    clause: "gate_graph",
  },
]);

test("CLAUSES: each of the three, turned off on its own, stops the gate passing", async () => {
  for (const falsifier of CLAUSE_FALSIFIERS) {
    const emitted = await emitFrom({ binding: { ...cleanBinding(), ...falsifier.binding } });
    assert.equal(emitted.passable, false, falsifier.name);
    assert.equal(emitted.reason_id, falsifier.reason, falsifier.name);
    assert.equal(emitted.join[falsifier.clause].state, "failed", falsifier.name);
    // AND THE OTHER TWO STILL HELD, which is what makes it one clause and not
    // three failing together for an unrelated reason.
    for (const [name, clause] of Object.entries(emitted.join))
      if (name !== falsifier.clause)
        assert.equal(clause.state, "held", `${falsifier.name}: ${name} also failed`);
  }
  // THE CONTROL IS A CONTROL: the unmodified binding, staged the same way
  // through the same machinery, passes. Without this the three cases above would
  // pass just as well against a staging step that broke the file.
  assert.equal((await emitFrom({})).passable, true,
    "the staging itself breaks the run, so the falsifiers prove nothing");
});

// ===========================================================================
// CONTROL 3 — a tampered predecessor receipt hash.
// ===========================================================================

test("TAMPER: a forged acceptance-receipt hash fails to match a signature, and is refused", async () => {
  // The forged hash is well-formed and is not the hash any row carries. Card 11
  // compares it against the ACCEPTANCE RECEIPT's hash — the row a human's act
  // wrote — and never echoes it, so what a forgery fails to match is a
  // signature rather than a proposal.
  const emitted = await emitFrom({ binding: { ...cleanBinding(), wr40: FORGED_HASH } });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_predecessor_clause_failed");
  const clause = emitted.join.predecessor_join;
  assert.deepEqual(clause.detail.failed, ["step:wr40-repository-outcome"]);
  assert.equal(clause.detail.per_predecessor["step:wr46-dissolution-outcome"], "held");

  // AND THE FORGERY IS NOWHERE IN THE ANSWER. A caller who supplied a hash
  // learns only whether the store agreed with it; here the hash is a module
  // constant, and it still may not travel.
  assert.equal(JSON.stringify(emitted).includes(FORGED_HASH.slice("sha256:".length)), false,
    "the forged hash came back out of the answer");
});

test("TAMPER: one Work Request cannot close two predecessors", async () => {
  // The same acceptance-receipt hash pasted for two different predecessors. Each
  // reader still answers about its OWN Work Request — the ref comes from the
  // reader's frozen table, not from the binding — so the hash matches neither
  // row it was not written for, and the clause names both.
  const shared = ACCEPTED_HASHES["step:wr40-repository-outcome"];
  const emitted = await emitFrom({
    binding: { ...cleanBinding(), wr46: shared, wr54: shared } });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_predecessor_clause_failed");
  assert.deepEqual(emitted.join.predecessor_join.detail.failed,
    ["step:wr46-dissolution-outcome", "step:wr54-backup-recovery-outcome"]);
});

// ===========================================================================
// CONTROL 4 — the seat, and the three rulings.
// ===========================================================================

test("SEAT: putting the holder back to null takes the whole slice dark", async () => {
  // The producer module is in this tree, unedited, with a fully pasted run
  // binding and every row present. The seam is shut anyway, because the only
  // thing that opens it is card 9's declaration.
  const emitted = await emitFrom({ staffedSeat: false });
  assert.equal(emitted.producer_bound, false);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.oracle_seat_bound, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_seam_unavailable");
  assert.equal(emitted.producer_answer, null, "an unstaffed seat still reached the producer");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.observed_at, null);
  assert.deepEqual(emitted.owed_seams, ["seam:gate-zero-read-only-outcome-producer"]);
  assert.deepEqual([...emitted.undecided_governance_questions],
    ["which independent seat holds oracle:gate-producer:gate-zero-read-only"]);
});

test("READERS: withdrawing any one ruling leaves the producer with nothing to read", async () => {
  for (const [index, card] of ["card:11", "card:12", "card:13"].entries()) {
    const emitted = await emitFrom({ withdrawnCards: [index] });
    assert.equal(emitted.passable, false, card);
    // The producer is bound and was reached — it is the EVIDENCE that is gone,
    // and the reason says so rather than blaming the seam.
    assert.equal(emitted.producer_bound, true, card);
    assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable", card);
    assert.equal(emitted.outcome_digest, null, card);
    // A withdrawn ruling is not a failed clause: nothing was read, so nothing
    // failed, and the negative admission is not even attempted over it.
    assert.equal(emitted.producer_answer.negative_admission, null, card);
  }
});

test("EVIDENCE: a canary row that does not exist is unanswered, not failed", async () => {
  const emitted = await emitFrom({
    binding: { ...cleanBinding(), scheduler_canary_run_key: CANARY_ABSENT } });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable");
  assert.equal(emitted.join, null, "an outcome was emitted over a row that does not exist");
  assert.equal(emitted.producer_answer.clauses.scheduler_canary.state, "unknown");
});

test("EVIDENCE: a check with no conclusion yet is unanswered, not a failure", async () => {
  const emitted = await emitFrom({
    binding: { ...cleanBinding(), head_revision: REVISION_UNFINISHED } });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable");
  assert.equal(emitted.producer_answer.clauses.gate_graph.state, "unknown");
  assert.deepEqual(emitted.producer_answer.clauses.gate_graph.detail.not_answered,
    ["local-db-ci-migration"]);
});

// ===========================================================================
// CONTROL 5 — Q036.D1's own falsifier.
// ===========================================================================

test("PROPAGATION: an injected failure produces a FAILING outcome, kept and digested", async () => {
  // The top gate reports success and its ancestor did not. A clause that
  // conjoined self-conclusions would read this graph as green, which is the
  // false-green result Q036.D1 names as a failure of the gate itself.
  const emitted = await emitFrom({
    binding: { ...cleanBinding(), head_revision: REVISION_FAILED_ANCESTOR } });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_gate_graph_clause_failed");

  // AND IT IS AN OUTCOME, NOT AN ABSENCE. The retry policy this registration
  // carries says retryable, every run kept, failed runs retained — so a run that
  // read everything and found a failed gate has a receipt, a digest and an
  // instant, and its receipt says so in r7's own word.
  assert.equal(emitted.status, "outcome_produced");
  assert.equal(emitted.receipt_status, "fail");
  assert.equal(emitted.receipt.status, "fail");
  assert.match(emitted.outcome_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(emitted.outcome_digest, digest(emitted.receipt));
  assert.match(emitted.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);

  // AND THE DENIALS WERE OBSERVED, which is the field's whole claim.
  assert.equal(emitted.receipt.negative_admission_result, "all_required_denials_observed");
  assert.deepEqual(emitted.negative_admission.missed_cases, []);
  assert.equal(emitted.negative_admission.observed_codes.length, 7);
  assert.ok(emitted.negative_admission.observed_codes
    .includes("gate_reporting_success_over_a_failed_ancestor"));

  // THE PROPAGATION ITSELF: the top gate reported success and is still refused.
  const clause = emitted.join.gate_graph;
  assert.deepEqual(clause.detail.conclusion_not_success, ["ops-ci-strict"]);
  assert.deepEqual(clause.detail.inherited_not_success, ["main-canary"]);
});

test("PROPAGATION: the required denials are re-derived, not asserted", async () => {
  const emitted = await emitFrom({});
  // Seven cases, and every one of them is a mutation of THIS run's readings put
  // back through this module's own live clauses. The mutation control for the
  // prover is the prover itself: it reports which cases it observed, and a case
  // that stopped firing lands in `missed_cases` and refuses the whole run.
  assert.deepEqual(emitted.negative_admission.observed_codes, [
    "gate_conclusion_not_success",
    "gate_reporting_success_over_a_failed_ancestor",
    "one_outcome_record_closing_two_predecessors",
    "predecessor_acceptance_receipt_hash_mismatch",
    "predecessor_not_accepted",
    "scheduler_canary_not_bound_to_its_receipt",
    "scheduler_observation_not_after_dispatch",
  ]);
});

// ===========================================================================
// CONTROL 6 — self-review.
// ===========================================================================

test("IDENTITY: a subject maker that is the reviewing seat is denied", async () => {
  const emitted = await emitFrom({
    binding: { ...cleanBinding(), subject_maker_actor_id: REVIEWING_SEAT_ACTOR } });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.producer_answer.clauses, null,
    "a self-review reached the clauses before it was denied");
});

test("IDENTITY: a subject maker this system does not register is denied", async () => {
  const emitted = await emitFrom({
    binding: { ...cleanBinding(), subject_maker_actor_id: "whoever-ran-it" } });
  assert.equal(emitted.passable, false);
  // A well-formed slug that identity.js will not speak for. It is refused
  // BEFORE any store is opened: a receipt may not name a principal this system
  // does not register, so there is nothing to read on its behalf.
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.producer_answer.clauses, null);
});

// ===========================================================================
// CONTROL 7 — no caller-supplied anything, by parse and by call.
// ===========================================================================

test("SURFACE: the producer's export list is exactly five constants and one callable", () => {
  assert.deepEqual(Object.keys(producer).sort(), [
    "V5_A02_GATE_ZERO_CLAUSE_STATES",
    "V5_A02_GATE_ZERO_PRODUCER_REASON_IDS",
    "V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION",
    "V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE",
    "V5_A02_GATE_ZERO_RUN_BINDING_STATUS",
    "v5A02GateZeroEmitOutcome",
  ]);
  // EXACTLY ONE export is callable, and its arity is zero. An exported builder
  // is the one shape that can be handed caller-supplied references — the PR 990
  // defect — and a function with no parameters has nowhere to put one.
  const callable = Object.entries(producer)
    .filter(([, value]) => typeof value === "function").map(([name]) => name);
  assert.deepEqual(callable, ["v5A02GateZeroEmitOutcome"]);
  assert.equal(producer.v5A02GateZeroEmitOutcome.length, 0);
  // No binding door, no setter, no registry, under any spelling — asked of what
  // could BE one. A constant named for the run binding's state is a fact a
  // reader can check; only a callable can be handed something.
  for (const [name, value] of Object.entries(producer))
    if (typeof value === "function")
      assert.equal(/bind|set|register|configure|inject/i.test(name), false,
        `${name} is a binding door on the producer surface`);
  // And nothing exported is writable or extensible, so a door cannot be added
  // to the namespace after the fact either.
  for (const [name, value] of Object.entries(producer))
    if (value !== null && typeof value === "object")
      assert.ok(Object.isFrozen(value), `${name} is not frozen`);
  // And the run binding ships UNNAMED, which is what makes src's own answer a
  // refusal rather than a pass nobody reviewed.
  assert.equal(producer.V5_A02_GATE_ZERO_RUN_BINDING_STATUS, "unnamed");
});

test("SURFACE: handing the producer an argument is a contract violation, thrown synchronously", () => {
  for (const argument of [
    { emitOutcome: () => ({ passable: true }) },
    { outcomeHash: `sha256:${"a".repeat(64)}` },
    { headSha: REVISION_ALL_SUCCEED },
    "allow", 1, true, null, undefined, [],
  ])
    assert.throws(() => producer.v5A02GateZeroEmitOutcome(argument),
      error => error instanceof V5BoundaryError
        && error.code === "gate_zero_producer_takes_no_argument",
      JSON.stringify(argument ?? null));
  // TWO arguments too, and the throw is synchronous rather than a rejection: a
  // caller reading a refusal never has to catch one, and a contract violation is
  // refused before anything is awaited.
  assert.throws(() => producer.v5A02GateZeroEmitOutcome(1, 2), V5BoundaryError);
});

test("SHAPE: the one callable wears amendment 2's closed shape", () => {
  const fn = producer.v5A02GateZeroEmitOutcome;
  assert.equal(Object.hasOwn(fn, "prototype"), false, "carries a prototype");
  assert.throws(() => Reflect.construct(fn, []), TypeError);
  assert.ok(Object.isFrozen(fn), "is not frozen");
  assert.equal(types.isProxy(fn), false, "is a Proxy");
  const descriptor = Object.getOwnPropertyDescriptor(fn, Symbol.hasInstance);
  assert.ok(descriptor !== undefined, "has no own Symbol.hasInstance");
  assert.equal(descriptor.writable, false);
  assert.equal(descriptor.configurable, false);
  assert.equal(descriptor.enumerable, false);
  // The operand is never read: every trap on it throws, and `instanceof` is
  // still false rather than the caller's own error.
  const hostile = new Proxy({}, {
    get() { throw new Error("the operand was read"); },
    getPrototypeOf() { throw new Error("the operand's chain was walked"); },
  });
  assert.equal(hostile instanceof fn, false);
});

/**
 * THE REACHABILITY GUARD, in the shape PR 1004's amendment 6 requires: a closed
 * set, parsed rather than grepped, and asserted to be exactly the modules that
 * may name this file. A producer that some other module could import and drive
 * is a producer with a second caller, and a second caller is an argument wearing
 * an import's clothes.
 */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync, statSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const out = {};
    const walk = (dir, prefix) => {
      for (const name of readdirSync(dir).sort()) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) { walk(full, prefix + name + "/"); continue; }
        if (!name.endsWith(".js")) continue;
        const source = readFileSync(full, "utf8");
        out[prefix + name] =
          new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
      }
    };
    walk(process.argv[1], "");
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

test("REACHABILITY: exactly one module in src may import the producer, and none re-exports it", () => {
  const imports = moduleImports(SRC);
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");
  assert.ok(Object.hasOwn(imports, PRODUCER_FILE), "the parser did not see the producer at all");

  const importers = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one => one.endsWith(`/${PRODUCER_FILE}`)))
    .map(([name]) => name).sort();
  assert.deepEqual(importers, [GATE_FILE],
    "a module other than the gate can reach the producer");

  // AND THE GATE DOES NOT RE-EXPORT IT. The gate's own export list is asserted
  // exactly in gate-zero-assurance.v5.test.mjs; this is the other half — the
  // producer's one callable must not appear on any public namespace but its own.
  const gateSource = readFileSync(join(SRC, GATE_FILE), "utf8");
  assert.equal(/export\s*\{[^}]*v5A02GateZeroEmitOutcome/s.test(gateSource), false,
    "the gate re-exports the producer, so a caller can drive it directly");
  assert.equal(gateSource.includes(`export * from "./${PRODUCER_FILE}"`), false);

  // The producer reaches no test file, by the same parser.
  for (const specifier of imports[PRODUCER_FILE])
    assert.equal(/\/test\/|\.testonly\.|\.testhelper\.|\.fixture\./.test(specifier), false,
      `the producer imports ${specifier}`);
});

test("SURFACE: no environment variable names a row, opens a seam or moves an answer", () => {
  // A FRESH PROCESS, because a module reads its environment while it evaluates:
  // the variables are set before the import, not after it.
  const script = `
    const { pathToFileURL } = require("node:url");
    import(pathToFileURL(process.argv[1]).href).then(async gate => {
      const { digest } = await import(pathToFileURL(process.argv[2]).href);
      process.stdout.write(JSON.stringify({ emitted: digest(await gate.emitGateZeroOutcome()) }));
    }).catch(error => { process.stderr.write(String(error)); process.exit(1); });
  `;
  const hostile = {
    ...process.env,
    CARR_GATE_ZERO_HEAD_REVISION: REVISION_ALL_SUCCEED,
    CARR_GATE_ZERO_SERVICE_KEY: SERVICE_KEY,
    CARR_GATE_ZERO_CANARY_RUN_KEY: CANARY_JOINING,
    CARR_GATE_ZERO_SUBJECT_MAKER: SUBJECT_MAKER_ACTOR,
    CARR_GATE_ZERO_ACCEPTANCE_HASH: ACCEPTED_HASHES["step:wr40-repository-outcome"],
    CARR_GATE_ZERO_RUN_BINDING: "named",
    GATE_ZERO_PASSABLE: "true",
  };
  const run = spawnSync(process.execPath,
    ["-e", script, join(SRC, GATE_FILE), join(SRC, "artifact-trust.js")],
    { encoding: "utf8", env: hostile });
  assert.equal(run.status, 0, `the child failed: ${run.stderr}`);
  const withEnvironment = JSON.parse(run.stdout).emitted;
  const clean = spawnSync(process.execPath,
    ["-e", script, join(SRC, GATE_FILE), join(SRC, "artifact-trust.js")],
    { encoding: "utf8", env: process.env });
  assert.equal(clean.status, 0, `the child failed: ${clean.stderr}`);
  assert.equal(withEnvironment, JSON.parse(clean.stdout).emitted,
    "an environment variable moved the emitted answer");
});

// ===========================================================================
// The closed vocabularies, and the one place they must agree.
// ===========================================================================

test("REASONS: every reason the producer can answer with is registered by the gate", () => {
  for (const id of producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS)
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(id),
      `${id} is a producer refusal the gate cannot express`);
  assert.equal(producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.length, 7);
  // And the source cites no id outside its own closed list: `reason()` raises on
  // an unregistered one, so a citation that is not here cannot be reached at all.
  const source = readFileSync(join(SRC, PRODUCER_FILE), "utf8");
  for (const cited of [...source.matchAll(/(?:refusal|reason)\("([a-z_]+)"/g)].map(m => m[1]))
    assert.ok(producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.includes(cited), cited);
});

test("VOCABULARY: the producer answers in held/failed/unknown and no conditional mood", () => {
  assert.deepEqual([...producer.V5_A02_GATE_ZERO_CLAUSE_STATES], ["failed", "held", "unknown"]);
  const source = readFileSync(join(SRC, PRODUCER_FILE), "utf8");
  // THE CONDITIONAL MOOD IS GONE FROM PRODUCTION, which is the point of
  // promoting the clauses at all: they are applied to readings taken from ruled
  // stores, so they answer, rather than describing what a caller's shape would
  // have meant if it had been evidence.
  for (const forbidden of ["would_", "_if_authoritative", "is_not_authority",
    "caller_supplied_shapes_not_authority"])
    assert.equal(source.includes(forbidden), false,
      `${forbidden} is a conditional-mood spelling in the production producer`);
});

test("VOCABULARY: no value the producer hands the gate carries a privileged word", async () => {
  // The standing rule's closed union, swept AS EXACT MATCH AND AS SUBSTRING over
  // every key and every string leaf — the same sweep the gate surface is held to,
  // applied here because these values travel into the gate's own answer.
  const PRIVILEGED = Object.freeze([
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable",
  ]);
  // The four names main already answers with, which this module inherits from
  // the shape every V5 answer wears rather than inventing.
  // The names this module INHERITS rather than chooses: four field names every
  // V5 answer wears, and three identifiers r7 itself registers. Each carries the
  // union's "read" because Gate Zero is a READ-ONLY gate, and renaming any of
  // them would be renaming r7's own registry entry.
  const INHERITED = new Set([
    "request_read", "caller_evidence_admitted", "model_judgment_admitted",
    "gate_zero_step_ref", "step:gate-zero-read-only-outcome",
    "gate-zero-read-only-accepted", "oracle:gate-producer:gate-zero-read-only",
    "receipt:gate-zero-read-only-outcome",
  ]);
  const found = [];
  const walk = (value, path) => {
    if (Array.isArray(value)) { value.forEach((one, at) => walk(one, `${path}[${at}]`)); return; }
    if (value !== null && typeof value === "object") {
      for (const [key, one] of Object.entries(value)) {
        if (!INHERITED.has(key)) check(key, `${path}.${key} (key)`);
        walk(one, `${path}.${key}`);
      }
      return;
    }
    if (typeof value === "string" && !INHERITED.has(value)) check(value, path);
  };
  const check = (text, where) => {
    const folded = text.toLowerCase();
    for (const word of PRIVILEGED)
      if (folded === word || folded.includes(word)) found.push(`${where}: ${word} in ${text}`);
  };
  // Both answers: the refusal src ships, and a produced outcome over rows.
  walk(await producer.v5A02GateZeroEmitOutcome(), "$");
  const produced = (await emitFrom({})).producer_answer;
  // `status: "pass"` is r7's own required value for a passing consumer-gate
  // receipt and is the one string this module may not rename. It is lifted out
  // by name rather than exempted by category.
  assert.equal(produced.receipt.status, "pass");
  walk({ ...produced, receipt: { ...produced.receipt, status: null } }, "$produced");
  assert.deepEqual(found, []);
  // NON-VACUOUS: the sweep catches what it exists to catch.
  check("ruled_store_readings", "$control");
  assert.equal(found.length, 1, "the sweep did not catch a word it must catch");
});
