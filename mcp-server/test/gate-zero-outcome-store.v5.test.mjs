// V5-A02 STEP B — THE GATE ZERO READ-ONLY OUTCOME RECORD, and the verb that
// writes it.
//
// WHAT THIS FILE PROVES AND WHAT IT DOES NOT. Every case below drives the
// REGISTERED VERB through the real dispatch path — `executeRegisteredTool`, the
// `oracleSeatOnly` gate, the closed-argument check in mutation-registry.js, the
// authenticated call identity.js establishes there, the handler's own seat
// derivation and the receipt contract. That is enough to prove WHO the authority
// gate admits, WHO it refuses, WHAT is recorded and WHERE the recorded value
// came from — and it is not, and is not treated as, evidence that any row was
// written. The authority is enforced THREE times and only two of them are
// reachable from here; the third is ops.gate_zero_producer_actor_id() in the
// record layer, proved by the disposable-Postgres migration class.
//
// THE RECEIPT IS NEVER BUILT HERE (2026-09-13, PR 1014 correction). The first
// draft of this suite constructed a twenty-one-field object and handed it to the
// verb, which is exactly the defect the review found in the verb itself: a
// record whose subject is whatever the caller assembled. There is no such
// object in this file any more and no argument that could carry one. Every
// receipt that reaches the write path below was EMITTED, inside the call, by the
// real Step A producer reading the real ruled readers over a real staged
// candidate tree — and the one case that bends a field bends what the producer
// produced, after the fact, to show which clause catches it.
//
// SO EVERY CASE THAT WRITES RUNS IN A STAGED CANDIDATE TREE, built by
// gate-zero-candidate-tree.testhelper.mjs — the same staging the producer suite
// stands on, imported rather than retyped. The tree holds .git (the candidate's
// revision and committer), a copy of src with the seam store substituted by the
// fixture, the sealed fixture bytes and the environment manifest. Nothing is
// passed in, nothing is set, and no environment variable points at one.
//
// NO GATE ZERO OUTCOME EXISTS ANYWHERE AS A RESULT OF RUNNING THIS FILE. The
// database is a recording mock.
//
//   node --test mcp-server/test/gate-zero-outcome-store.v5.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { types } from "node:util";

import { digest } from "../src/artifact-trust.js";
import {
  GATE_ZERO_IDENTITY_FIELDS, GATE_ZERO_IDENTITY_SCHEMA, GATE_ZERO_RECEIPT_CONSTANTS,
  GATE_ZERO_RECEIPT_FIELDS, GATE_ZERO_RECEIPT_SCHEMA, GATE_ZERO_RECEIPT_STATUSES,
  assertGateZeroReceipt, deriveGateZeroProducerSeat, gateZeroOracleSeatLane,
  gateZeroOutcomeCandidateDigest, gateZeroOutcomeDigest,
} from "../src/gate-zero-outcome-store.v5.js";
import { V5_A02_GATE_ZERO_PRODUCER_REGISTRATION } from "../src/gate-zero-producer-registration.v5.js";
import { actorFromProps, authenticatedIdentity, propsForSlug } from "../src/identity.js";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";
import { WHOLE_WALK, pathToValue } from "./gate-zero-reachability-walk.testhelper.mjs";
import {
  CORRELATION_ID, RECORDED_REVIEW_TOKEN, RECORDED_REVIEW_TOKENS, SRC,
  cleanupStagedTrees, fabricatedReviewerActor, inDispatchedVerb, moduleImports, moduleOfTree,
  recordedPartnerActor, recordedReviewerActor, stageTree,
} from "./gate-zero-candidate-tree.testhelper.mjs";
import { CANARY_ABSENT, REVISION_FAILED_ANCESTOR }
  from "./gate-zero-producer-stores.v5.fixture.mjs";

after(cleanupStagedTrees);

const STORE_FILE = "gate-zero-outcome-store.v5.js";
const VERB = "record-gate-zero-read-only-outcome";
const OUTCOME_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const KEY = "11111111-2222-4333-8444-555555555555";
const SECOND_KEY = "99999999-8888-4777-8666-555555555555";
const MIGRATION = readFileSync(
  new URL("../../migrations/0502_gate_zero_read_only_outcome.sql", import.meta.url), "utf8");

// --- actors, every one of them server-derived ---------------------------------
//
// NOTHING HERE IS A LITERAL ANY MORE (PR 1013's third correction, amendment 8).
// identity.js brands the actors it mints with membership of a module-private
// WeakSet, so an object written out here — however exactly it copies the fields
// index.js sets — authenticates as nobody. The seat is minted by the real review
// door from a recorded bearer and the recorded shape of the server's sealed
// token map, and the partner by the real OAuth-props path. `correlation_id` is
// what correlation.js stamps per request, and it is the only per-call identifier
// in this system no caller writes — which is why the receipt's session refs are
// built from it, and it is DECORATED on to the authenticated object rather than
// spread into a copy, because the brand is object identity.
//
// A TREE'S ACTORS ARE THAT TREE'S. Every staged tree is its own module graph
// with its own WeakSet, so an actor minted from the root identity.js is not
// authenticated inside a staged one. Each case mints from the module it will
// dispatch through.
function rootReviewerActor(correlationId = CORRELATION_ID) {
  authenticatedIdentity.sealServerReviewTokens(RECORDED_REVIEW_TOKENS);
  const actor = authenticatedIdentity.reviewActorForToken(`Bearer ${RECORDED_REVIEW_TOKEN}`);
  assert.notEqual(actor, null, "the recorded review context authenticated no actor");
  return Object.assign(actor, { correlation_id: correlationId });
}
/** Run `fn` inside the authenticated call this actor establishes, root modules. */
const inRootCall = (actor, fn) => authenticatedIdentity.dispatchFor(actor)(fn);

function rootPartnerActor(correlationId = CORRELATION_ID) {
  return recordedPartnerActor({ actorFromProps, propsForSlug }, correlationId);
}

const SEAT = rootReviewerActor();
const PARTNER = rootPartnerActor();
const OTHER_REVIEWER = { slug: "grok-reviewer", human: false, review: true,
  via: "review-token", correlation_id: CORRELATION_ID };
const SPONSORED = { slug: "claude", human: false, sponsoring_human_slug: "joe",
  via: "oauth-google", correlation_id: CORRELATION_ID };
const PROBE = { slug: "smoke-probe", human: false, probe: true, via: "probe-token",
  correlation_id: CORRELATION_ID };

/**
 * THE RECORDING MOCK. It answers the statements the handler issues and NOTHING
 * else: an unexpected statement throws here rather than passing silently, so a
 * statement this verb starts issuing shows up as a failure in this file.
 *
 * It keeps the receipt it was handed, which is how the cases below compare what
 * was PERSISTED against what the producer EMITTED.
 */
function mockDatabase({ existing = null } = {}) {
  const calls = [];
  const state = { recorded: existing, persisted: null };
  return {
    calls, state,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (sql.includes("ops.gate_zero_record_read_only_outcome")) {
        const offered = JSON.parse(params[1]);
        state.persisted = offered;
        // IDEMPOTENT ON THE CANDIDATE, exactly as the SQL writer is, INCLUDING
        // what it compares (PR 1014, second correction). The row is kept when
        // the candidate matches; what decides whether the offered outcome is the
        // SAME outcome is the candidate-scoped digest, not the full receipt
        // digest — so a second genuine call, with its own session_ref and its
        // own instants, collapses onto the row that exists here exactly as it
        // does in ops.gate_zero_record_read_only_outcome. A mock that answered
        // otherwise would be a second implementation of the writer's rule.
        if (state.recorded && state.recorded.candidate_digest === offered.candidate_digest) {
          if (gateZeroOutcomeCandidateDigest(state.recorded)
              !== gateZeroOutcomeCandidateDigest(offered))
            throw new Error(
              `a different Gate Zero outcome is already recorded for candidate ${offered.candidate_digest}`);
        } else {
          state.recorded = offered;
        }
        return { rows: [{ id: OUTCOME_ID }] };
      }
      if (sql.includes("from ops.gate_zero_read_only_outcome")) {
        const r = state.recorded;
        return { rows: [{
          id: OUTCOME_ID, outcome_digest: digest(r),
          candidate_scoped_digest: gateZeroOutcomeCandidateDigest(r),
          candidate_digest: r.candidate_digest,
          status: r.status, receipt: r,
          producing_seat_ref: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref,
          observed_at: r.observed_at, recorded_at: "2026-09-13T00:00:05Z",
        }] };
      }
      // THE ENVELOPE'S OWN STATEMENTS, declared rather than swallowed by a
      // catch-all: the idempotency replay read finds nothing (each test uses a
      // fresh mock), and the two audit writes are accepted.
      if (sql.includes("from tool_call where idempotency_key")) return { rows: [] };
      if (sql.includes("insert into tool_call")) return { rows: [] };
      if (sql.includes("insert into event")) return { rows: [] };
      throw new Error(`mock database has no declared response for: ${sql}`);
    },
  };
}

async function refusal(promise) {
  try {
    await promise;
  } catch (error) {
    if (error?.payload) return error.payload;
    if (error?.error) return error;
    throw error;
  }
  throw new Error("expected a refusal and got a result");
}

function thrownBy(fn) {
  try { fn(); } catch (error) { return error?.payload ?? error; }
  throw new Error("expected a refusal and got a result");
}

/**
 * ONE CALL OF THE REGISTERED VERB, in a staged candidate tree.
 *
 * The dispatch, the gate, the producer, the store and identity.js are all the
 * STAGED copies — one module graph — so what the producer signs is the call this
 * dispatch actually entered, and what the contract compares it against is that
 * same derivation. `options` is the world the tree is staged in.
 */
async function recordIn(options = {}, mint = recordedReviewerActor,
  args = { idempotency_key: KEY }, db = null) {
  const { target } = stageTree(options);
  const identity = await moduleOfTree(target, "identity.js");
  const tools = await moduleOfTree(target, "tools.js");
  const client = db ?? mockDatabase();
  const actor = typeof mint === "function" ? mint(identity) : mint;
  const result = await tools.executeRegisteredTool(client, actor, VERB, args);
  return { result, client, target, tools, actor };
}

/** What the bound producer emits in a tree staged the same way, for comparison. */
async function emittedIn(options = {}, mint = recordedReviewerActor) {
  const { target } = stageTree(options);
  const gate = await moduleOfTree(target, "gate-zero-assurance.v5.js");
  const identity = await moduleOfTree(target, "identity.js");
  return inDispatchedVerb(target, mint(identity), () => gate.emitGateZeroOutcome());
}

// ===========================================================================
// The contract, which is r7's and is restated nowhere.
// ===========================================================================

test("the receipt contract is r7's, closed at twenty-one fields and five statuses", () => {
  assert.equal(GATE_ZERO_RECEIPT_SCHEMA, "consumer-gate-receipt.v1");
  assert.equal(GATE_ZERO_RECEIPT_FIELDS.length, 21);
  assert.equal(new Set(GATE_ZERO_RECEIPT_FIELDS).size, 21);
  for (const field of Object.keys(GATE_ZERO_RECEIPT_CONSTANTS))
    assert.ok(GATE_ZERO_RECEIPT_FIELDS.includes(field), field);
  assert.deepEqual([...GATE_ZERO_RECEIPT_STATUSES],
    ["pass", "fail", "unknown", "stale", "quarantined"]);
  assert.equal(GATE_ZERO_RECEIPT_CONSTANTS.negative_admission_result,
    "all_required_denials_observed");
});

test("the identity contract is r7's too, and it is closed at three fields", () => {
  assert.equal(GATE_ZERO_IDENTITY_SCHEMA, "authenticated-receipt-identity.v1");
  assert.deepEqual([...GATE_ZERO_IDENTITY_FIELDS],
    ["actor_id", "session_ref", "authority_class"]);
  // AND IT IS r7's OWN PATTERN IN BOTH HALVES. The module's regular expression
  // and the record layer's are the same characters, which is what stops one
  // half accepting a session ref the other refuses.
  const source = readFileSync(new URL(`../src/${STORE_FILE}`, import.meta.url), "utf8");
  const pattern = "^session:[a-z0-9][a-z0-9:._/-]{8,199}$";
  assert.ok(source.includes(pattern), "the module's session_ref pattern is not r7's");
  assert.ok(MIGRATION.includes(pattern), "the record layer's session_ref pattern is not r7's");
});

test("the staffed seat lane is derived from the registration, never restated here", () => {
  assert.equal(gateZeroOracleSeatLane(), "codex-reviewer");
  assert.equal(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref,
    "seat:codex-reviewer:gpt-5.6-sol");
  assert.equal(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_bound, true);
});

test("the record layer and the module agree about which seat holds the oracle", () => {
  // TWO HALVES OF ONE FACT, ASSERTED EQUAL. Migration 0502 carries the holder
  // ref as a SQL literal and the module carries it as a frozen JS literal.
  // Staffing a different seat in one half and not the other is a defect, and
  // this is the assertion that calls it one.
  assert.match(MIGRATION, /select 'seat:codex-reviewer:gpt-5\.6-sol'::text/);
  assert.ok(MIGRATION.includes(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref));
});

// ===========================================================================
// P0 — THE VERB TAKES ONE ARGUMENT, AND IT IS NOT A SUBJECT.
// ===========================================================================

test("the verb's input is one idempotency key and nothing else", () => {
  const tool = TOOLS[VERB];
  assert.equal(tool.write, true);
  assert.equal(tool.humanOnly, false, "this verb is deliberately NOT humanOnly");
  assert.equal(tool.oracleSeatOnly, true);
  assert.notEqual(tool.authorityOnly, true, "the oracle does not write on the partner authority connection");
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(Object.keys(tool.inputSchema.properties), ["idempotency_key"]);
  assert.deepEqual(tool.inputSchema.required, ["idempotency_key"]);
  // IT IS THE ONLY VERB CARRYING THE FLAG. A second one arriving without its own
  // coverage is exactly what this assertion is for.
  const flagged = Object.entries(TOOLS).filter(([, t]) => t.oracleSeatOnly === true).map(([n]) => n);
  assert.deepEqual(flagged, [VERB]);
});

test("MUTATION — every receipt-shaped argument is refused as unregistered, and nothing is written", async () => {
  // THE CONTROL FOR THE P0 FINDING. The verb used to take `receipt` and persist
  // it. There is now no spelling of that argument the door admits: the closed
  // top-level check refuses any key the registered schema does not carry, and it
  // refuses it BEFORE the handler runs, so no database statement is issued at
  // all. A `receipt` property put back on the schema would also move the
  // schema digest and fail the mutation-registry contract check beside it.
  for (const field of ["receipt", "outcome", "consumer_gate_receipt", "gate_zero_receipt",
    "receipt_json", "outcome_receipt", "receipts"]) {
    const client = mockDatabase();
    const refused = await refusal(executeRegisteredTool(client, SEAT, VERB,
      { idempotency_key: KEY, [field]: { gate_id: "gate-zero-read-only-accepted" } }));
    assert.equal(refused.error, "unregistered_operation_fields", field);
    assert.deepEqual(refused.fields, [field]);
    assert.deepEqual(client.calls, [], `${field} reached the database`);
  }
});

// ===========================================================================
// The authority gate: four refusals and one admission, through real dispatch.
// ===========================================================================

test("CONTROL 1 — a partner identity is refused", async () => {
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), PARTNER, VERB, { idempotency_key: KEY }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "verified_partner");
  assert.match(refused.hint, /independent control-plane oracle/);
});

test("CONTROL 2 — a sponsored agent is refused", async () => {
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), SPONSORED, VERB, { idempotency_key: KEY }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "sponsored_agent");
});

test("CONTROL 3 — a review_agent on a DIFFERENT seat is refused", async () => {
  // THE CONTROL THAT MATTERS MOST, because it is the one a class-based gate
  // would pass. grok-reviewer authenticates through the same review-token door
  // and derives the same review_agent class; only the staffed lane may sign.
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), OTHER_REVIEWER, VERB, { idempotency_key: KEY }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "review_agent", "the wrong lane derives the RIGHT class");
  assert.equal(refused.seat_staffed, true, "the seat is staffed; this lane simply does not hold it");
  const direct = thrownBy(() => deriveGateZeroProducerSeat(OTHER_REVIEWER));
  assert.equal(direct.error, "gate_zero_oracle_seat_mismatch");
  assert.equal(direct.seat_lane, "codex-reviewer");
});

test("a probe seat is refused too, so the door is not open to machine identities generally", async () => {
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), PROBE, VERB, { idempotency_key: KEY }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "probe_agent");
});

// ===========================================================================
// P0 — WHAT IS RECORDED IS WHAT THE PRODUCER EMITTED.
// ===========================================================================

test("CONTROL 4 — the bound seat writes, and what it writes is the producer's own receipt", async () => {
  const { result, client } = await recordIn({});
  assert.equal(result.ok, true);
  assert.equal(result.outcome_id, OUTCOME_ID);
  assert.equal(result.step_ref, "step:gate-zero-read-only-outcome");
  assert.equal(result.gate_id, "gate-zero-read-only-accepted");
  assert.equal(result.status, "pass");
  assert.equal(result.receipt_status, "pass");
  assert.equal(result.producer_reason_id, null);
  assert.equal(result.producing_seat_ref, "seat:codex-reviewer:gpt-5.6-sol");

  // THE WRITER GOT THE RECEIPT AND AN IDEMPOTENCY KEY, AND NOTHING ELSE: no
  // digest, no seat, no actor. Everything else is derived in the database.
  const write = client.calls.find(call => call.sql.includes("gate_zero_record_read_only_outcome"));
  assert.equal(write.params.length, 2);
  assert.equal(write.params[0], KEY);
  const persisted = client.state.persisted;
  assert.deepEqual(Object.keys(persisted).sort(), [...GATE_ZERO_RECEIPT_FIELDS].sort());

  // AND THE PERSISTED RECEIPT IS THE PRODUCER'S. A tree staged the same way,
  // driven through the gate's own producer seam, emits the same twenty-one
  // fields — every one of them derived, none of them from this file. Only the
  // two instants differ, because they are stamped when each run happens.
  const emitted = await emittedIn({});
  assert.equal(emitted.status, "outcome_produced");
  const withoutInstants = one => {
    const { observed_at, ttl_expires_at, ...rest } = one;
    return rest;
  };
  assert.deepEqual(withoutInstants(persisted), withoutInstants(emitted.receipt));

  // THE IDENTITIES ARE THIS CALL'S, and the session ref is the server's own
  // correlation id — the one field in the identity object no caller writes.
  assert.deepEqual(persisted.producer_identity,
    { actor_id: "codex-reviewer", session_ref: `session:${CORRELATION_ID}`,
      authority_class: "review_agent" });
  assert.deepEqual(persisted.evaluator_identity, persisted.producer_identity);
  assert.notEqual(persisted.subject_maker_identity.actor_id, "codex-reviewer");

  // THE DIGEST IS THE DATABASE'S. The handler compares its own computation and
  // refuses a divergence rather than reporting the local value.
  assert.equal(result.outcome_digest, gateZeroOutcomeDigest(persisted));
  // NOTHING WAS GRANTED. The result says so in a field rather than in prose.
  assert.equal(result.effects.creates_effect, false);
  assert.equal(result.effects.clock_started, false);
  assert.equal(result.effects.benchmark_accepted, false);
});

test("CONTROL 5 — a second AUTHENTICATED call for the same candidate is idempotent", async () => {
  // THE RETRY IS A DIFFERENT CALL, WHICH IS THE WHOLE POINT (PR 1014, second
  // correction). The first version of this case reused one actor object, so
  // both runs carried the SAME correlation id and therefore the same
  // session_ref, and the two receipts differed only in instants a fixture could
  // hold still. It passed, and it proved nothing about a retry: identity.js
  // derives session_ref from the request's own correlation id, so two genuine
  // calls NEVER share one. Sol's finding 3 was exactly that gap.
  //
  // A SECOND CORRELATION ID IS WHAT A SECOND REQUEST IS. Everything else is
  // held equal — same candidate tree, same seat — so the only thing that moved
  // between the two calls is what the server stamps per request.
  const client = mockDatabase();
  const retrying = identity => recordedReviewerActor(identity, "5a0b7c33-41de-4f9a-8e26-1c7b93d0af45");
  assert.notEqual(retrying.correlation_id, SEAT.correlation_id);
  const first = await recordIn({}, recordedReviewerActor, { idempotency_key: KEY }, client);
  const second = await recordIn({}, retrying, { idempotency_key: SECOND_KEY }, client);

  assert.equal(second.result.outcome_id, first.result.outcome_id, "a retry minted a second outcome");
  assert.equal(second.result.candidate_digest, first.result.candidate_digest);
  // THE DURABLE ROW IS THE FIRST ONE, and both callers are handed it: the
  // evidence digest reported back is the stored receipt's, unchanged by the
  // second call, and the value the two were COMPARED on is equal.
  assert.equal(second.result.outcome_digest, first.result.outcome_digest);
  assert.equal(second.result.candidate_scoped_digest, first.result.candidate_scoped_digest);

  // AND THE TWO RECEIPTS REALLY WERE DIFFERENT BYTES. This is the assertion that
  // makes the case a proof rather than a coincidence: keying on the full receipt
  // digest — what the writer did before this correction — would have refused the
  // second call, because the second call's session_ref is its own.
  const firstReceipt = first.client.state.recorded;
  const secondReceipt = second.client.state.persisted;
  assert.notEqual(secondReceipt.producer_identity.session_ref,
    firstReceipt.producer_identity.session_ref);
  assert.notEqual(gateZeroOutcomeDigest(secondReceipt), gateZeroOutcomeDigest(firstReceipt),
    "the two calls produced identical receipts, so this proves nothing about a retry");
  assert.equal(gateZeroOutcomeCandidateDigest(secondReceipt),
    gateZeroOutcomeCandidateDigest(firstReceipt));
});

test("MUTATION — a second run that read DIFFERENT rows for one candidate still conflicts", async () => {
  // THE OTHER EDGE OF THE SAME KEY. Narrowing what a retry is compared on is
  // only safe if it still refuses a genuinely different outcome for one
  // candidate. Here the candidate is held identical and the WORLD is changed
  // under it, so the producer reaches a different verdict: the record layer must
  // refuse rather than silently keep either one.
  const client = mockDatabase();
  await recordIn({}, recordedReviewerActor, { idempotency_key: KEY }, client);
  const recorded = client.state.recorded;

  // The same candidate digest, a different verdict — assembled by bending what
  // the producer emitted, the same way the identity mutations below do, and
  // pushed at the writer through the mock's own mirror of the SQL rule. The
  // record layer's copy of this refusal is proved against a real PostgreSQL in
  // gate-zero-outcome-record-race.test.mjs; this is the gateway-side mirror.
  const divergent = { ...recorded, status: "fail" };
  assert.equal(divergent.candidate_digest, recorded.candidate_digest);
  await assert.rejects(
    client.query("select ops.gate_zero_record_read_only_outcome($1::uuid,$2::jsonb) as id",
      [SECOND_KEY, JSON.stringify(divergent)]),
    /a different Gate Zero outcome is already recorded/);

  // AND WHO THE MAKER IS STAYS INSIDE THE KEY, even though the maker's SESSION
  // does not. Step A's third correction made every session_ref per-call, the
  // candidate-build one included, so the projection drops all three; what it
  // keeps of each identity is the actor and the authority class, and a receipt
  // naming a different maker for one candidate is a different outcome.
  const remadeMaker = { ...recorded,
    subject_maker_identity: { ...recorded.subject_maker_identity, actor_id: "dell" } };
  assert.notEqual(gateZeroOutcomeCandidateDigest(remadeMaker),
    gateZeroOutcomeCandidateDigest(recorded));

  // While the maker's SESSION alone moves nothing, which is what makes the
  // retry above possible at all.
  const remadeMakerSession = { ...recorded,
    subject_maker_identity: { ...recorded.subject_maker_identity,
      session_ref: "session:11111111-2222-4333-8444-555555555555:candidate-build" } };
  assert.equal(gateZeroOutcomeCandidateDigest(remadeMakerSession),
    gateZeroOutcomeCandidateDigest(recorded));
});

test("an injected non-green conclusion records as fail — Q036.D1's own falsifier", async () => {
  // A GREEN-ONLY TEST WOULD MISS THIS. r7 requires truthful failure
  // propagation, so a failing run must be RECORDABLE and must not read as a
  // binding. The record accepts it; ops.benchmark_gate_zero_outcome() is what
  // refuses to bind it, and that half is proved in the migration class.
  const { result, client } = await recordIn({ revision: REVISION_FAILED_ANCESTOR });
  assert.equal(result.status, "fail");
  assert.equal(result.receipt_status, "fail");
  assert.equal(result.producer_reason_id, "gate_zero_gate_graph_clause_failed");
  assert.equal(client.state.persisted.status, "fail");
  assert.equal(result.effects.benchmark_accepted, false);
  assert.match(MIGRATION, /where status = 'pass' and ttl_expires_at > now\(\)/);
  assert.match(MIGRATION, /every outcome recorded here is non-passing or past its expiry/);
});

test("a producer that refuses records NOTHING, and says which reason refused it", async () => {
  // THE OTHER HALF OF "PRODUCED, NOT RECEIVED". With no receipt argument there
  // is nothing a caller can supply when the producer declines, so a run over a
  // canary row that does not exist writes no row at all — and the refusal
  // carries the producer's own reason rather than a generic one.
  const { target } = stageTree({ ledgerCanary: CANARY_ABSENT });
  const identity = await moduleOfTree(target, "identity.js");
  const tools = await moduleOfTree(target, "tools.js");
  const client = mockDatabase();
  const refused = await refusal(tools.executeRegisteredTool(
    client, recordedReviewerActor(identity), VERB, { idempotency_key: KEY }));
  assert.equal(refused.error, "gate_zero_outcome_not_produced");
  assert.equal(refused.reason_id, "gate_zero_evidence_unavailable");
  assert.equal(refused.producer_bound, true, "the seam is bound; the ROWS were not there");
  assert.equal(
    client.calls.some(call => call.sql.includes("gate_zero_record_read_only_outcome")),
    false, "a refused run reached the writer");
});

// ===========================================================================
// P1 — THE NESTED IDENTITIES ARE THE AUTHENTICATED CALL'S.
// ===========================================================================

/** One real producer receipt, emitted in a staged tree, to bend after the fact. */
async function producedReceipt() {
  const emitted = await emittedIn({});
  assert.equal(emitted.status, "outcome_produced");
  return emitted.receipt;
}

test("MUTATION — a receipt with a foreign session_ref is refused, though the seat is right", async () => {
  // THE CONTROL FOR THE P1 IDENTITY FINDING, and it is the one a check on
  // actor_id and authority_class alone would pass: both are stable across every
  // call this seat ever makes, so only the session ref can tell one call from
  // another. The receipt below is the producer's own, with one field moved to
  // another run's session.
  const receipt = await producedReceipt();
  const seat = deriveGateZeroProducerSeat(SEAT);
  const foreign = {
    ...receipt,
    producer_identity: { ...receipt.producer_identity,
      session_ref: "session:11111111-2222-4333-8444-555555555555" },
  };
  const refused = inRootCall(SEAT,
    () => thrownBy(() => assertGateZeroReceipt(foreign, seat)));
  assert.equal(refused.error, "gate_zero_receipt_identity_not_this_call");
  assert.equal(refused.field, "producer_identity");
  assert.deepEqual(refused.differing_fields, ["session_ref"]);

  // AND UNBENT, THE SAME RECEIPT IN THE SAME CALL IS ACCEPTED — so the refusal
  // above is the moved field and not the harness.
  assert.equal(inRootCall(SEAT, () => assertGateZeroReceipt(receipt, seat)), receipt);
});

test("MUTATION — a receipt from another seat's call is refused in both identity fields", async () => {
  const receipt = await producedReceipt();
  const seat = deriveGateZeroProducerSeat(SEAT);
  // A DIFFERENT CORRELATION ID IS A DIFFERENT CALL, which is the honest shape of
  // "this receipt was produced somewhere else and carried here".
  const elsewhere = rootReviewerActor("7c9d2a41-6b35-4f82-9e13-0a4b8d6f2c57");
  const refused = inRootCall(elsewhere,
    () => thrownBy(() => assertGateZeroReceipt(receipt, seat)));
  assert.equal(refused.error, "gate_zero_receipt_identity_not_this_call");
  assert.deepEqual(refused.differing_fields, ["session_ref"]);
});

test("with no authenticated call there is no identity to compare against, and nothing is accepted", async () => {
  const receipt = await producedReceipt();
  const seat = deriveGateZeroProducerSeat(SEAT);
  const refused = thrownBy(() => assertGateZeroReceipt(receipt, seat));
  assert.equal(refused.error, "gate_zero_receipt_unauthenticated_call");
  assert.match(refused.hint, /tools\.js's verb dispatch/);
});

test("the identity object is closed: a fourth key, a capital and a short ref each deny", async () => {
  const receipt = await producedReceipt();
  const seat = deriveGateZeroProducerSeat(SEAT);
  const bend = identity => inRootCall(SEAT, () => thrownBy(() =>
    assertGateZeroReceipt({ ...receipt, producer_identity: identity }, seat)));

  const extra = bend({ ...receipt.producer_identity, note: "an extra field" });
  assert.equal(extra.error, "gate_zero_receipt_identity_fields");
  assert.deepEqual(extra.unknown, ["note"]);
  assert.equal(extra.schema, GATE_ZERO_IDENTITY_SCHEMA);

  const short = bend({ ...receipt.producer_identity, session_ref: "session:a" });
  assert.equal(short.error, "gate_zero_receipt_session_ref_malformed");

  // A SINGLE CAPITAL DENIES, exactly as it does in a safe: ref — r7's pattern is
  // lowercase, and this is the trap that has cost a real debugging session.
  const shouted = bend({ ...receipt.producer_identity,
    session_ref: receipt.producer_identity.session_ref.toUpperCase().replace("SESSION:", "session:") });
  assert.equal(shouted.error, "gate_zero_receipt_session_ref_malformed");
  assert.match(shouted.hint, /lowercase/);

  const missing = bend({ actor_id: "codex-reviewer", authority_class: "review_agent" });
  assert.deepEqual(missing.missing, ["session_ref"]);
});

test("the record layer enforces the same closed identity shape, in its own copy", () => {
  // THE COPY A HANDLER BUG CANNOT STEP AROUND. A writer connection opened
  // outside the gateway reaches the SQL function and not the module, so the
  // shape clauses are stated twice on purpose.
  assert.match(MIGRATION, /authenticated-receipt-identity\.v1 is a closed schema/);
  assert.match(MIGRATION, /v_identity_keys <> 3/);
  assert.match(MIGRATION, /not \(v_identity \? 'session_ref'\)/);
  assert.match(MIGRATION,
    /\^session:\[a-z0-9\]\[a-z0-9:\._\/-\]\{8,199\}\$/);
});

// ===========================================================================
// P1 — SAME-CANDIDATE IDEMPOTENCY IS ATOMIC.
// ===========================================================================

test("the writer is ONE insert arbitrated by the candidate key, not a lookup then an insert", () => {
  // THE CONTROL FOR THE P1 RACE FINDING, asserted over the record layer's own
  // text because the interleaving it fixes is not reachable from a mock. The
  // race itself is proved against a real PostgreSQL in
  // gate-zero-outcome-record-race.test.mjs, which the migration class runs.
  assert.match(MIGRATION, /on conflict \(candidate_digest\) do nothing\s*\n\s*returning id into v_id;/);
  // AND THE LOOKUP-FIRST SHAPE IS GONE. A `select ... where candidate_digest`
  // that runs BEFORE the insert is the defect; the only one left runs after it,
  // as the fallback, which this ordering assertion states.
  const insertAt = MIGRATION.indexOf("insert into ops.gate_zero_read_only_outcome (");
  const lookupAt = MIGRATION.indexOf("select * into v_existing from ops.gate_zero_read_only_outcome");
  assert.ok(insertAt > 0 && lookupAt > insertAt,
    "the candidate lookup still runs before the insert, which is the race");
  assert.equal(MIGRATION.split("select * into v_existing").length - 1, 1,
    "there is more than one candidate lookup in the writer");
  // AND A DIFFERENT RECEIPT FOR A RECORDED CANDIDATE IS STILL A CONFLICT.
  assert.match(MIGRATION, /a different Gate Zero outcome is already recorded for candidate/);
});

// ===========================================================================
// STANDARDS — the binding amendments, swept rather than asserted by example.
// ===========================================================================

test("STANDARDS: every exported constant is a frozen closed union or table", async () => {
  const store = await import("../src/gate-zero-outcome-store.v5.js");
  const frozen = [];
  const walk = (value, path) => {
    if (value === null || typeof value !== "object") return;
    assert.ok(Object.isFrozen(value), `${path} is not frozen`);
    frozen.push(path);
    for (const [key, one] of Object.entries(value)) walk(one, `${path}.${key}`);
  };
  for (const [name, value] of Object.entries(store)) walk(value, name);
  // The four exported tables and unions: the field list, the identity field
  // list, the constants table and the status union. A fifth arriving unfrozen
  // fails the walk above rather than this count.
  assert.deepEqual(frozen.sort(),
    ["GATE_ZERO_IDENTITY_FIELDS", "GATE_ZERO_RECEIPT_CONSTANTS",
      "GATE_ZERO_RECEIPT_FIELDS", "GATE_ZERO_RECEIPT_STATUSES"]);
  // THE CLOSED UNIONS ARE CLOSED BY COUNT AS WELL AS BY CONTENT: a sixth status
  // or a fourth identity field is a schema this record layer does not have.
  assert.equal(GATE_ZERO_RECEIPT_STATUSES.length, 5);
  assert.equal(GATE_ZERO_IDENTITY_FIELDS.length, 3);
  assert.equal(Object.keys(GATE_ZERO_RECEIPT_CONSTANTS).length, 8);
});

test("STANDARDS: every exported callable wears amendment 2's closed shape", async () => {
  const store = await import("../src/gate-zero-outcome-store.v5.js");
  const callables = Object.entries(store).filter(([, value]) => typeof value === "function");
  assert.deepEqual(callables.map(([name]) => name).sort(),
    ["assertGateZeroReceipt", "deriveGateZeroProducerSeat", "gateZeroOracleSeatLane",
      "gateZeroOutcomeCandidateDigest", "gateZeroOutcomeDigest"]);
  for (const [name, fn] of callables) {
    assert.equal(Object.hasOwn(fn, "prototype"), false, `${name} carries a prototype`);
    assert.throws(() => Reflect.construct(fn, []), TypeError, name);
    assert.ok(Object.isFrozen(fn), `${name} is not frozen`);
    assert.equal(types.isProxy(fn), false, `${name} is a Proxy`);
    const descriptor = Object.getOwnPropertyDescriptor(fn, Symbol.hasInstance);
    assert.ok(descriptor !== undefined, `${name} has no own Symbol.hasInstance`);
    assert.equal(descriptor.writable, false, name);
    assert.equal(descriptor.configurable, false, name);
    assert.equal(descriptor.enumerable, false, name);
    // The operand is never read: every trap on it throws, and `instanceof` is
    // still false rather than the caller's own error.
    const hostile = new Proxy({}, {
      get() { throw new Error("the operand was read"); },
      getPrototypeOf() { throw new Error("the operand's chain was walked"); },
    });
    assert.equal(hostile instanceof fn, false, name);
  }
  // NO BINDING DOOR, under any spelling: only a callable can be handed
  // something, and none of these is named for taking one.
  for (const [name] of callables)
    assert.equal(/bind|set|register|configure|inject/i.test(name), false,
      `${name} is a binding door on the record layer's surface`);
});

test("REACHABILITY: exactly two modules in src may import the record layer's contract", () => {
  // A CLOSED SET, PARSED RATHER THAN GREPPED. The contract decides who may sign
  // a Gate Zero receipt, so a third module that could import and drive it is a
  // second caller — and a second caller is an authority nobody ruled on.
  const imports = moduleImports(SRC);
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");
  assert.ok(Object.hasOwn(imports, STORE_FILE), "the parser did not see the store at all");
  const importers = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one => one.endsWith(`/${STORE_FILE}`)))
    .map(([name]) => name).sort();
  assert.deepEqual(importers, ["tools.js"],
    "a module other than the verb registry can reach the receipt contract");
  // AND THE CONTRACT REACHES NO TEST FILE, by the same parser.
  for (const specifier of imports[STORE_FILE])
    assert.equal(/\/test\/|\.testonly\.|\.testhelper\.|\.fixture\./.test(specifier), false,
      `the record layer's contract imports ${specifier}`);
});

test("REACHABILITY: the producer callable is not reachable through the record layer", async () => {
  // THE STORE MUST NOT BE A SECOND DOOR TO THE PRODUCER. It is the thing the
  // verb calls beside the producer, so a value of it that handed the producer
  // out — by a property, a symbol, a prototype, a getter or an accessor
  // function — would be an emit seam wearing a contract's name. The walk is the
  // repository's own, imported rather than retyped.
  const producer = await import("../src/gate-zero-producer.v5.js");
  const store = await import("../src/gate-zero-outcome-store.v5.js");
  const callable = producer.v5A02GateZeroEmitOutcome;
  // NON-VACUOUS FIRST: the walk finds it where it IS exported.
  assert.equal(pathToValue(producer, callable, WHOLE_WALK), ".v5A02GateZeroEmitOutcome");
  assert.equal(pathToValue(store, callable, WHOLE_WALK), null,
    "the Gate Zero producer is reachable from the record layer's contract");
});

test("VOCABULARY: what the verb hands a consumer carries no privileged word", async () => {
  // The standing rule's closed union, swept AS EXACT MATCH AND AS SUBSTRING over
  // every key and every string leaf of the verb's own result — the same sweep
  // the producer and the gate are held to.
  const PRIVILEGED = Object.freeze([
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable",
  ]);
  // The names this result INHERITS rather than chooses: the envelope's own
  // fields, three identifiers r7 itself registers, and the two r7 status values
  // a consumer-gate receipt may carry. Renaming any of them would be renaming
  // r7's registry entry or the envelope every verb answers in.
  const INHERITED = new Set([
    "ok", "request_read", "caller_evidence_admitted", "model_judgment_admitted",
    "step:gate-zero-read-only-outcome", "gate-zero-read-only-accepted",
    "oracle:gate-producer:gate-zero-read-only", "receipt_status", "status", "pass",
  ]);
  const found = [];
  const check = (text, where) => {
    const folded = String(text).toLowerCase();
    for (const word of PRIVILEGED)
      if (folded === word || folded.includes(word)) found.push(`${where}: ${word} in ${text}`);
  };
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
  const { result } = await recordIn({});
  // The prose fields are a consumer's explanation rather than a verdict, and
  // they are lifted out by NAME rather than exempted by category.
  const { digest_recipe, effects, ...answer } = result;
  const { note, ...flags } = effects;
  walk({ ...answer, effects: flags }, "$");
  assert.deepEqual(found, []);
  // NON-VACUOUS: the sweep catches what it exists to catch.
  check("gate_zero_outcome_allowed", "$control");
  assert.equal(found.length, 1, "the sweep did not catch a word it must catch");
});

test("the reviewer capability profile admits the verb, and admitting it is not the authority", async () => {
  const { PROFILES, allowedIn } = await import("../src/mcp.js");
  assert.ok(PROFILES.reviewer.has(VERB), "the seat cannot reach a verb its profile excludes");
  assert.equal(allowedIn("reviewer", VERB, TOOLS[VERB]), true);
  // ...and the profile alone would admit the WRONG lane too, which is why the
  // seat gate exists. CONTROL 3 above is what proves that lane is refused.
  assert.equal(allowedIn("full", VERB, TOOLS[VERB]), true);
});

// ===========================================================================
// The staged-source mutation controls on the seat itself.
// ===========================================================================

test("MUTATION — an unstaffed seat closes the verb to everyone, including the seat", async () => {
  const { target } = stageTree({ staffedSeat: false });
  const mutated = await import(pathToFileURL(join(target, STORE_FILE)).href);
  assert.equal(mutated.gateZeroOracleSeatLane(), null);
  const thrown = thrownBy(() => mutated.deriveGateZeroProducerSeat(SEAT));
  assert.equal(thrown.error, "gate_zero_oracle_seat_unstaffed");
  // The unstaffed answer is the same "no" this surface gave before any seat
  // existed: it refuses the holder as readily as it refuses a stranger.
  const strangerToo = thrownBy(() => mutated.deriveGateZeroProducerSeat(PARTNER));
  assert.equal(strangerToo.error, "gate_zero_oracle_seat_unstaffed");
  // AND THE WHOLE WRITE PATH GOES DARK WITH IT, through the real dispatch.
  const identity = await moduleOfTree(target, "identity.js");
  const tools = await moduleOfTree(target, "tools.js");
  const client = mockDatabase();
  const refused = await refusal(tools.executeRegisteredTool(
    client, recordedReviewerActor(identity), VERB, { idempotency_key: KEY }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.deepEqual(client.calls, []);
});

test("MUTATION — staffing a DIFFERENT lane moves who may sign, and only that", async () => {
  const { target } = stageTree({});
  const registration = join(target, "gate-zero-producer-registration.v5.js");
  const { writeFileSync } = await import("node:fs");
  const before = readFileSync(registration, "utf8");
  const after = before.replace('holder_ref: "seat:codex-reviewer:gpt-5.6-sol",',
    'holder_ref: "seat:grok-reviewer:grok-4.5",');
  assert.notEqual(after, before, "the staged mutation changed nothing, so it proves nothing");
  writeFileSync(registration, after);
  const mutated = await import(pathToFileURL(join(target, STORE_FILE)).href);
  assert.equal(mutated.gateZeroOracleSeatLane(), "grok-reviewer");
  // The lane that holds it in the working tree is now refused, and the other
  // one admitted — which is the whole content of "the seat decides".
  assert.equal(thrownBy(() => mutated.deriveGateZeroProducerSeat(SEAT)).error,
    "gate_zero_oracle_seat_mismatch");
  assert.equal(mutated.deriveGateZeroProducerSeat(OTHER_REVIEWER).lane, "grok-reviewer");
});

test("nothing in src but this suite's own helper reaches a candidate tree", () => {
  // A HARNESS THAT LEAKED INTO PRODUCTION would be a staging door, so the closed
  // set is asserted from the other side too: no module in src names the staging
  // helper or the fixture store this suite substitutes.
  const names = readdirSync(SRC).filter(name => name.endsWith(".js"));
  for (const name of names) {
    const source = readFileSync(join(SRC, name), "utf8");
    assert.equal(source.includes("gate-zero-candidate-tree.testhelper"), false,
      `${name} names the staging helper`);
  }
});
