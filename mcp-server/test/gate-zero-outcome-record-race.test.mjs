// THE GATE ZERO RETRY PROOF — two REAL authenticated verb calls, one candidate,
// one durable row (2026-09-13, PR 1014 second correction).
//
// WHAT THIS FILE IS FOR, AND WHAT IT USED TO BE. Its first version handed ONE
// hand-built fixture receipt to two raw connections and called the SQL writer
// directly. That proved the candidate key arbitrates a race between two callers
// holding identical bytes — and nothing at all about a retry, because two
// genuine authenticated calls never hold identical bytes. identity.js derives
// `session_ref` from the request's own correlation id, and the producer stamps
// `observed_at` and `ttl_expires_at` when the run happens, so the second real
// call for one candidate differed from the first in three fields, the writer
// compared the FULL receipt digest, and it refused. That was Sol's finding 3,
// and a fixture-fed race test is exactly why a green suite did not see it.
//
// SO EVERY CALL HERE IS THE REGISTERED VERB, through `executeRegisteredTool`,
// over a staged candidate tree, with the receipt produced inside the call by the
// real Step A producer. Nothing is handed in. The two calls differ in the ONE
// thing two requests differ in: the correlation id the server stamps.
//
// THE THREE PROPERTIES IT ESTABLISHES:
//
//   1. IDEMPOTENT ON THE REAL VERB PATH. Two authenticated calls for one
//      candidate produce one row, and BOTH callers receive it.
//   2. ATOMICALLY, under concurrency. The interleaving is made deterministic
//      rather than hoped for: A calls the verb and leaves its transaction open,
//      so its candidate-key entry is speculative; B's call is sent and blocks
//      inside the insert; A commits; B takes the ON CONFLICT DO NOTHING branch,
//      reads the committed row in its fallback select -- a new statement and so
//      a new snapshot -- and answers with the same id. Against a
//      lookup-then-insert writer this fails with a unique_violation.
//   3. STILL REFUSING A GENUINELY DIFFERENT OUTCOME. Narrowing what a retry is
//      compared on is only safe if a run that reached a different verdict for
//      one candidate still raises. It does.
//
// AND THE MUTATION CONTROL IS EXECUTED, NOT DESCRIBED. The writer's own
// definition is read back out of the catalog with pg_get_functiondef, the ONE
// comparison is put back to the full receipt digest, and the result is created
// in pg_temp and called with the second call's real receipt. It raises. So
// "keying on the full digest again turns this red" is a statement this file
// makes by running it, and the function it mutates is the one the database is
// actually carrying rather than a retyped copy of it.
//
// THE CLIENTS ARE REAL, and the recorder around them is a passthrough: every
// statement reaches PostgreSQL, and the wrapper keeps a copy of the receipt each
// call sent so the two can be compared afterwards. Nothing is answered locally.
//
// IT SKIPS WITHOUT A DATABASE, and it REFUSES a database that is not loopback.
// The rows it writes are committed on purpose -- an uncommitted race proves
// nothing -- and the record is append-only by trigger, so they cannot be removed
// afterwards. That is safe on the throwaway database the migration class builds
// and is why this file declines to point at anything else.
//
//   DATABASE_URL=postgresql://localhost/... node --test \
//     mcp-server/test/gate-zero-outcome-record-race.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { gateZeroOutcomeCandidateDigest, gateZeroOutcomeDigest }
  from "../src/gate-zero-outcome-store.v5.js";
import { cleanupStagedTrees, moduleOfTree, recordedReviewerActor, stageTree }
  from "./gate-zero-candidate-tree.testhelper.mjs";

const DSN = process.env.CARR_GATE_ZERO_RACE_DSN || process.env.DATABASE_URL || "";
// A SKIP THAT NOBODY NOTICES IS A TEST COLLECTED BY NOBODY. The migration class
// sets this, so in the one place this proof is meant to run, "no database" and
// "no reachable Postgres" FAIL instead of skipping quietly. Everywhere else --
// the unit class, a developer's shell -- the skip is the honest answer.
const REQUIRED = process.env.CARR_GATE_ZERO_RACE_REQUIRED === "1";
const LOOPBACK = /@(localhost|127\.0\.0\.1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

const VERB = "record-gate-zero-read-only-outcome";
/** The seat lane the record layer staffs. Not a choice this file makes. */
const SEAT_SLUG = "codex-reviewer";

after(cleanupStagedTrees);

/**
 * ONE REAL CONNECTION, in an open transaction, carrying the server-established
 * actor context mcp.js sets. It is not a parameter of the writer and never was.
 *
 * The returned object is a PASSTHROUGH RECORDER: `query` forwards every
 * statement to PostgreSQL and answers with what PostgreSQL answered, and keeps
 * the receipt this connection's call sent to the writer so the two calls can be
 * compared afterwards. It answers nothing on its own.
 */
async function seatedClient(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  await client.query("select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
  const recorder = {
    client,
    sent: null,
    query: async (sql, params = []) => {
      if (typeof sql === "string" && sql.includes("ops.gate_zero_record_read_only_outcome"))
        recorder.sent = JSON.parse(params[1]);
      return client.query(sql, params);
    },
  };
  return recorder;
}

test("two authenticated calls for one candidate converge on one durable row", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false,
      "this proof was required and no database URL was given to it");
    return t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof commits rows to an append-only record and runs against a throwaway only");
  const pg = await import("pg");

  let a;
  let b;
  try {
    a = await seatedClient(pg.default ?? pg);
    b = await seatedClient(pg.default ?? pg);
  } catch (error) {
    if (a) await a.client.end().catch(() => {});
    if (b) await b.client.end().catch(() => {});
    assert.equal(REQUIRED, false, `this proof was required and Postgres was unreachable: ${error.message}`);
    return t.skip(`no reachable Postgres: ${error.message}`);
  }
  t.after(async () => {
    for (const one of [a, b]) await one.client.end().catch(() => {});
  });

  // THE ACTOR ROW IS THE DATABASE'S, read rather than asserted into existence.
  // The gateway needs the id for its audit envelope; the record layer derives
  // the same row again from the transaction's actor context, and neither takes
  // it from the other.
  const seatRow = (await a.query(
    "select id, kind, active from public.actor where slug = $1", [SEAT_SLUG])).rows[0];
  assert.ok(seatRow, `this database has no ${SEAT_SLUG} actor to act as`);
  assert.equal(seatRow.active, true);
  assert.notEqual(seatRow.kind, "human");

  // ONE CANDIDATE TREE, ONE MODULE GRAPH, TWO CALLS. Both calls read the same
  // candidate, so they must reach the same candidate digest; what differs is
  // the correlation id, which is what differs between two real requests.
  //
  // BOTH ACTORS ARE MINTED BY THE STAGED identity.js, through the real review
  // door, from the recorded bearer and the recorded shape of the server's sealed
  // token map. Under amendment 8 the brand is object identity, so the actor is
  // DECORATED in place with the correlation id and the audit id rather than
  // spread into a copy -- a copy would authenticate as nobody, which is the
  // whole design.
  const { target } = stageTree({});
  const identity = await moduleOfTree(target, "identity.js");
  const tools = await moduleOfTree(target, "tools.js");
  const first = Object.assign(
    recordedReviewerActor(identity, "2c8f5a91-7d3e-4b06-9a14-6e0d8b5f37c2"), { id: seatRow.id });
  const second = Object.assign(
    recordedReviewerActor(identity, "9e14b7d2-035a-4c68-b1f7-4a2d6c90e8b3"), { id: seatRow.id });
  assert.notEqual(first.correlation_id, second.correlation_id);

  // (1) A calls the verb and does NOT commit: its candidate-key entry is
  //     speculative, and its receipt was produced inside this call.
  const firstResult = await tools.executeRegisteredTool(a, first, VERB,
    { idempotency_key: randomUUID() });
  assert.equal(firstResult.ok, true);
  assert.match(firstResult.outcome_id, /^[0-9a-f-]{36}$/);
  assert.equal(firstResult.status, "pass");

  // (2) B's call goes out and is left in flight. It blocks inside the insert,
  //     on A's uncommitted index entry.
  const pending = tools.executeRegisteredTool(b, second, VERB,
    { idempotency_key: randomUUID() });
  let settledEarly = false;
  pending.then(() => { settledEarly = true; }, () => { settledEarly = true; });
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(settledEarly, false,
    "the second writer did not block on the first, so the candidate key is not arbitrating");

  // (3) A commits, which releases B. (4) B answers with the row that exists.
  await a.client.query("commit");
  const secondResult = await pending;
  await b.client.query("commit");

  // BOTH CALLERS RECEIVED THE SAME DURABLE ROW.
  assert.equal(secondResult.ok, true);
  assert.equal(secondResult.outcome_id, firstResult.outcome_id,
    "the second authenticated call did not receive the row the first wrote");
  assert.equal(secondResult.candidate_digest, firstResult.candidate_digest);
  assert.equal(secondResult.outcome_digest, firstResult.outcome_digest);
  assert.equal(secondResult.candidate_scoped_digest, firstResult.candidate_scoped_digest);

  // AND EXACTLY ONE ROW EXISTS, which is the property the whole shape is for:
  // every run kept, one current outcome per candidate.
  const rows = await a.query(
    "select count(*)::int as n from ops.gate_zero_read_only_outcome where candidate_digest = $1",
    [firstResult.candidate_digest]);
  assert.equal(rows.rows[0].n, 1, "a retry minted a second outcome for one candidate");

  // THE TWO CALLS REALLY WERE TWO CALLS. Without this the case above could pass
  // on two identical receipts, which is the fixture-shaped proof this file
  // replaced. The session refs are the server's own, one per request.
  const sentA = a.sent;
  const sentB = b.sent;
  assert.ok(sentA && sentB, "one of the two calls never reached the writer");
  assert.equal(sentA.producer_identity.session_ref, `session:${first.correlation_id}`);
  assert.equal(sentB.producer_identity.session_ref, `session:${second.correlation_id}`);
  assert.notEqual(sentB.producer_identity.session_ref, sentA.producer_identity.session_ref);
  assert.equal(sentA.candidate_digest, sentB.candidate_digest);
  assert.notEqual(gateZeroOutcomeDigest(sentB), gateZeroOutcomeDigest(sentA),
    "the two calls produced byte-identical receipts, so this proves nothing about a retry");
  assert.equal(gateZeroOutcomeCandidateDigest(sentB), gateZeroOutcomeCandidateDigest(sentA));

  // AND THE DATABASE AGREES WITH THE GATEWAY about both values, computed by its
  // own canonicalization rather than by artifact-trust.js.
  const digests = await a.query(
    `select ops.gate_zero_outcome_digest($1::jsonb) as full_b,
            ops.gate_zero_outcome_candidate_digest($1::jsonb) as candidate_b,
            ops.gate_zero_outcome_candidate_digest($2::jsonb) as candidate_a`,
    [JSON.stringify(sentB), JSON.stringify(sentA)]);
  assert.equal(digests.rows[0].full_b, gateZeroOutcomeDigest(sentB));
  assert.equal(digests.rows[0].candidate_b, gateZeroOutcomeCandidateDigest(sentB));
  assert.equal(digests.rows[0].candidate_a, digests.rows[0].candidate_b);

  // ===================================================================
  // MUTATION CONTROL — put the full digest back and this turns red.
  // ===================================================================
  // The writer's own definition, read out of the catalog rather than retyped, so
  // the thing being mutated is what the database is carrying.
  const def = (await a.query(
    `select pg_get_functiondef(p.oid) as def
       from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname = 'ops' and p.proname = 'gate_zero_record_read_only_outcome'`)).rows[0].def;
  const NAME = "ops.gate_zero_record_read_only_outcome(";
  const CANDIDATE_KEY = "v_digest := ops.gate_zero_outcome_candidate_digest(p_receipt);";
  const CANDIDATE_COMPARE = "v_existing.candidate_scoped_digest <> v_digest";
  for (const [anchor, what] of [[NAME, "the writer's name"],
    [CANDIDATE_KEY, "the retry comparison value"], [CANDIDATE_COMPARE, "the retry comparison"]])
    assert.equal(def.split(anchor).length - 1, 1,
      `the mutation anchor no longer matches ${what}`);
  const mutant = def
    .replace(NAME, "pg_temp.gate_zero_record_read_only_outcome_full_digest_key(")
    .replace(CANDIDATE_KEY, "v_digest := ops.gate_zero_outcome_digest(p_receipt);")
    .replace(CANDIDATE_COMPARE, "v_existing.outcome_digest <> v_digest");

  // THE ACTOR CONTEXT IS TRANSACTION-LOCAL, so it is set again here: A's first
  // transaction committed above and took its setting with it, and the writer
  // refuses a transaction that has none long before it reaches any comparison.
  await a.client.query("begin");
  await a.client.query("select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
  await a.client.query(mutant);
  await assert.rejects(
    a.client.query(
      "select pg_temp.gate_zero_record_read_only_outcome_full_digest_key($1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify(sentB)]),
    /a different Gate Zero outcome is already recorded/,
    "the full-digest key accepted the second real call, so this control proves nothing");
  await a.client.query("rollback");

  // ===================================================================
  // AND THE NARROWER KEY STILL REFUSES A DIFFERENT OUTCOME.
  // ===================================================================
  // The same candidate, a different verdict. A retry may not change this, and
  // the record is append-only, so the writer must raise rather than keep either.
  await a.client.query("begin");
  await a.client.query("select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
  await assert.rejects(
    a.client.query("select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify({ ...sentB, status: "fail" })]),
    /a different Gate Zero outcome is already recorded/,
    "a different verdict for one candidate was accepted as a retry");
  await a.client.query("rollback");
});
