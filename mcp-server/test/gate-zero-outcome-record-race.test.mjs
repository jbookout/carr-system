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
import { cleanupStagedTrees, DYNAMIC_PREDECESSOR_WORLD, inServedReview,
  moduleOfTree, stageTree, withStamps }
  from "./gate-zero-candidate-tree.testhelper.mjs";
import { authorizeAs, ensureProducerRoles }
  from "./gate-zero-producer-role.testhelper.mjs";

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
 * TWO REAL CONNECTIONS PER CALLER, WHICH IS WHAT PRODUCTION OPENS
 * (2026-09-14, PR 1014 third correction, standing-rule amendment 9).
 *
 *   * `client` is the ordinary writer connection every verb runs on. It carries
 *     the actor lookup and the audit event, and it still sets the server-
 *     established actor context mcp.js sets -- which on this path is now
 *     INFORMATIONAL: the record layer stopped reading it.
 *   * `seat` AUTHENTICATES AS THE DEDICATED PRODUCER LOGIN ROLE, and it is the
 *     only connection here that may execute the writer at all. carr_writer's
 *     EXECUTE is revoked; session_user is what the record layer derives the
 *     producing seat from.
 *
 * THE SEAT'S TRANSACTION IS THE TEST'S, not gateZeroSeatConnection's, and that
 * is the point of this file: the production door begins and commits its own
 * transaction, which would settle the race before it could be staged. Handing
 * the handler a conforming door whose transaction stays open is what makes the
 * interleaving deterministic rather than hoped for.
 *
 * The returned object is a PASSTHROUGH RECORDER: every statement reaches
 * PostgreSQL and answers with what PostgreSQL answered, and the receipt this
 * caller sent to the writer is kept so the two calls can be compared afterwards.
 */
async function seatedClient(pg, producerLoginRole) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  await client.query("select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);

  const seat = new pg.Client({ connectionString: DSN });
  await seat.connect();
  await authorizeAs(seat, producerLoginRole);

  const recorder = {
    client,
    seat,
    sent: null,
    query: async (sql, params = []) => client.query(sql, params),
    // THE TRANSACTION OPENS HERE, NOT AT CONNECT, and that is not a detail.
    // ops.gate_zero_read_only_outcome.recorded_at defaults to now(), which is
    // TRANSACTION START, and the constraint is recorded_at >= observed_at. The
    // production door begins its transaction immediately before the write, after
    // the producer has stamped observed_at, so the order holds by construction.
    // A transaction opened at connect time -- before staging a candidate tree
    // and running the producer, which takes well over a second -- makes now()
    // EARLIER than observed_at and the row is refused. What this file needs is
    // only that the transaction stay OPEN afterwards, which it does: nothing
    // commits it but the test.
    seatConnection: async run => {
      await seat.query("begin");
      return run({
        query: async (sql, params = []) => {
          if (typeof sql === "string" && sql.includes("ops.gate_zero_record_read_only_outcome"))
            recorder.sent = JSON.parse(params[1]);
          return seat.query(sql, params);
        },
      });
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
  let setup;
  try {
    // The dedicated producer login role 0502 deliberately does not create. On a
    // throwaway database this helper makes it; in production the provisioner
    // does, as a third LoginProfile with its own secret.
    setup = new (pg.default ?? pg).Client({ connectionString: DSN });
    await setup.connect();
    const roles = await ensureProducerRoles(setup);
    a = await seatedClient(pg.default ?? pg, roles.login);
    b = await seatedClient(pg.default ?? pg, roles.login);
  } catch (error) {
    if (setup) await setup.end().catch(() => {});
    for (const one of [a, b]) {
      if (one) await one.client.end().catch(() => {});
      if (one) await one.seat.end().catch(() => {});
    }
    assert.equal(REQUIRED, false, `this proof was required and Postgres was unreachable: ${error.message}`);
    return t.skip(`no reachable Postgres: ${error.message}`);
  }
  t.after(async () => {
    await setup.end().catch(() => {});
    for (const one of [a, b]) {
      await one.client.end().catch(() => {});
      await one.seat.end().catch(() => {});
    }
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
  // NEITHER ACTOR IS CHOSEN HERE, and since PR 1013's fifth correction there is
  // no way one could be. The staged identity.js exports no minter and no
  // dispatcher; `inServedReview` hands the staged review door the recorded
  // Authorization header and the server's per-request correlation id, and the
  // door authenticates, derives and enters the call itself. What the
  // continuation receives is that call's own actor, decorated IN PLACE with the
  // audit row id the way index.js decorates it -- so the two calls below differ
  // by exactly what two real requests differ by.
  const FIRST_CALL = "2c8f5a91-7d3e-4b06-9a14-6e0d8b5f37c2";
  const SECOND_CALL = "9e14b7d2-035a-4c68-b1f7-4a2d6c90e8b3";
  assert.notEqual(FIRST_CALL, SECOND_CALL);
  const { target, stamps } = stageTree({});
  const tools = await moduleOfTree(target, "tools.js");
  // UNDER THIS DEPLOY'S BUILD STAMPS (amendment 9). The producer derives its
  // candidate from the three vars bin/deploy-worker.sh writes at build time, not
  // from a repository at request time, and `stageTree` seals the tree it just
  // staged into exactly those vars. `withStamps` is `wrangler --var` for the
  // duration of the call: without it a staged tree is a deploy that carries no
  // candidate stamp, which is its own refusal.
  const callVerb = (correlationId, client) => withStamps(stamps,
    () => inServedReview(target, { correlationId },
      actor => tools.executeRegisteredTool(client, Object.assign(actor, { id: seatRow.id }),
        VERB, { idempotency_key: randomUUID() })));

  // (1) A calls the verb and does NOT commit: its candidate-key entry is
  //     speculative, and its receipt was produced inside this call.
  const firstServed = await callVerb(FIRST_CALL, a);
  assert.equal(firstServed.served, true, "the recorded review bearer was not served");
  const firstResult = firstServed.answered;
  assert.equal(firstResult.ok, true);
  assert.match(firstResult.outcome_id, /^[0-9a-f-]{36}$/);
  assert.equal(firstResult.status, "pass");

  // (2) B's call goes out and is left in flight. It blocks inside the insert,
  //     on A's uncommitted index entry.
  const pending = callVerb(SECOND_CALL, b).then(served => {
    assert.equal(served.served, true, "the recorded review bearer was not served");
    return served.answered;
  });
  let settledEarly = false;
  pending.then(() => { settledEarly = true; }, () => { settledEarly = true; });
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(settledEarly, false,
    "the second writer did not block on the first, so the candidate key is not arbitrating");

  // (3) A's seat commits, which releases B's seat transaction. B then reaches
  //     the OUTER audit-event lock and waits for A's still-open outer
  //     transaction. This is the production order: no caller waits on B while
  //     holding A open.
  await a.seat.query("commit");
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(settledEarly, false,
    "the second caller returned before the first caller settled its audit event");
  await a.client.query("commit");
  const secondResult = await pending;
  await b.seat.query("commit");
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
  assert.equal(sentA.producer_identity.session_ref, `session:${FIRST_CALL}`);
  assert.equal(sentB.producer_identity.session_ref, `session:${SECOND_CALL}`);
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

  // WHAT THE CONTROL READS, AND WHY IT MOVED (2026-09-13, the third release
  // candidate's refusal, finding 3). Until migration 0505 this branch RAISED, so
  // the control read a rejection. 0505 makes it return the recorded row instead
  // -- that is the whole of the fix, because the raise was what made an
  // outcome-without-event unrepairable -- and what the branch now emits is a
  // NOTICE naming the two projections. So the falsifier is the notice: the REAL
  // writer stays silent for a genuine retry, and the full-digest mutant does not,
  // because the full digest carries this call's session_ref and its two instants.
  // Keying on it would make every genuine second call a divergence, which is
  // exactly Sol's finding 3 and is still the thing this control refuses to let
  // back in.
  const noticesOf = async run => {
    const seen = [];
    const listener = message => seen.push(String(message?.message ?? ""));
    a.seat.on("notice", listener);
    try { await run(); } finally { a.seat.removeListener("notice", listener); }
    return seen.filter(text => /already recorded from a run whose projection differs/.test(text));
  };

  // THE MUTANT RUNS ON THE SEAT'S CONNECTION, because that is the only
  // connection the record layer admits at all now. It is CREATED with the
  // connection's own authenticated identity rather than the seat's -- a SECURITY
  // DEFINER function owned by the producer login role would reach neither the
  // authority test nor the table -- and then the session is put back to the seat
  // before it is called, so the mutant is exercised under exactly the identity
  // the real writer is.
  await a.seat.query("reset session authorization");
  await a.seat.query(mutant);
  await authorizeAs(a.seat, (await ensureProducerRoles(setup)).login);
  await a.seat.query("begin");
  const mutantDivergences = await noticesOf(() => a.seat.query(
    "select pg_temp.gate_zero_record_read_only_outcome_full_digest_key($1::uuid, $2::jsonb)",
    [randomUUID(), JSON.stringify(sentB)]));
  assert.equal(mutantDivergences.length, 1,
    "the full-digest key called the second real call a retry, so this control proves nothing");
  await a.seat.query("rollback");

  // AND THE REAL WRITER SAYS NOTHING ABOUT THE SAME CALL, which is the other
  // half of the same control: what separates the two is the comparison value,
  // not the fact that one of them happens to be a mutant.
  await a.seat.query("begin");
  const realDivergences = await noticesOf(() => a.seat.query(
    "select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb)",
    [randomUUID(), JSON.stringify(sentB)]));
  assert.equal(realDivergences.length, 0,
    "the shipped writer called a genuine retry a divergence");
  await a.seat.query("rollback");

  // ===================================================================
  // AND A DIFFERENT OUTCOME FOR ONE CANDIDATE CONVERGES, REPLACING NOTHING.
  // ===================================================================
  // The same candidate, a different verdict. Before 0505 the writer raised here,
  // and that raise is what the outside review refused: the seat's transaction
  // commits before the outer one writes the audit event, so a failure between
  // them leaves an outcome with no event -- and evidence legitimately moves
  // between two runs of one candidate, so the retry meant to write the lost event
  // was the retry the raise turned away.
  //
  // CONVERGING IS NOT REPLACING, and both halves are asserted. The call receives
  // the row that exists; the row's own receipt, digest and status are read back
  // afterwards and must be byte-identical to what the first call stored. The
  // append-only triggers are untouched and nothing here updates anything -- this
  // is what makes "the first receipt wins forever" a measurement rather than a
  // description.
  const before = (await a.query(
    `select outcome_digest, candidate_scoped_digest, status, receipt
       from ops.gate_zero_read_only_outcome where id = $1::uuid`,
    [firstResult.outcome_id])).rows[0];
  await a.seat.query("begin");
  const divergent = { ...sentB, status: "fail" };
  assert.equal(divergent.candidate_digest, sentB.candidate_digest);
  const convergedId = (await a.seat.query(
    "select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb) as id",
    [randomUUID(), JSON.stringify(divergent)])).rows[0].id;
  assert.equal(convergedId, firstResult.outcome_id,
    "a different verdict for one candidate did not converge onto the recorded row");
  await a.seat.query("commit");

  const after = (await a.query(
    `select count(*)::int as n from ops.gate_zero_read_only_outcome where candidate_digest = $1`,
    [firstResult.candidate_digest])).rows[0];
  assert.equal(after.n, 1, "converging minted a second outcome for one candidate");
  const unchanged = (await a.query(
    `select outcome_digest, candidate_scoped_digest, status, receipt
       from ops.gate_zero_read_only_outcome where id = $1::uuid`,
    [firstResult.outcome_id])).rows[0];
  assert.deepEqual(unchanged, before,
    "the divergent receipt moved the recorded row, which the append-only record forbids");
  assert.equal(unchanged.status, "pass");
});

test("an outer failure after the seat commit leaves no event, and the retry writes exactly one",
  async t => {
    // FINDING 3 OF THE THIRD RELEASE CANDIDATE'S REFUSAL, RUN RATHER THAN
    // ARGUED. Standing-rule amendment 9 puts the outcome write on the seat's own
    // login role and the audit event on the ordinary writer's, so they cannot
    // share a transaction and the seat's commits first. The reviewer asked what
    // happens when the outer one then fails, and answered it: an outcome with no
    // event, reachable, and -- before migration 0505 -- unrepairable.
    //
    // THE INTERLEAVING IS STAGED, NOT HOPED FOR. The seat's transaction is
    // committed by hand, exactly where gateZeroSeatConnection commits it, and the
    // outer transaction is then rolled back: the durable state is precisely what
    // a crash between the two leaves behind.
    if (!DSN) {
      assert.equal(REQUIRED, false,
        "this proof was required and no database URL was given to it");
      return t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
    }
    assert.ok(LOOPBACK.test(DSN),
      "REFUSED: this proof commits rows to an append-only record and runs against a throwaway only");
    const pg = await import("pg");
    const PG = pg.default ?? pg;

    let setup;
    let caller;
    try {
      setup = new PG.Client({ connectionString: DSN });
      await setup.connect();
      const roles = await ensureProducerRoles(setup);
      caller = await seatedClient(PG, roles.login);
    } catch (error) {
      if (setup) await setup.end().catch(() => {});
      if (caller) {
        await caller.client.end().catch(() => {});
        await caller.seat.end().catch(() => {});
      }
      assert.equal(REQUIRED, false,
        `this proof was required and Postgres was unreachable: ${error.message}`);
      return t.skip(`no reachable Postgres: ${error.message}`);
    }
    t.after(async () => {
      await setup.end().catch(() => {});
      await caller.client.end().catch(() => {});
      await caller.seat.end().catch(() => {});
    });

    const seatRow = (await setup.query(
      "select id from public.actor where slug = $1", [SEAT_SLUG])).rows[0];
    assert.ok(seatRow, `this database has no ${SEAT_SLUG} actor to act as`);

    // A CANDIDATE OF ITS OWN, so this proof's row is not the one the race above
    // already recorded -- the record is append-only and both tests share one
    // database. The world is left exactly as it is: what moves is one comment
    // inside the sealed bytes, which moves the candidate manifest digest and
    // therefore the candidate, and nothing the producer reads.
    const { target, stamps } = stageTree({
      candidateEdit: "the outer-failure heal proof's own candidate",
      mutablePredecessorWorld: true,
    });
    globalThis[DYNAMIC_PREDECESSOR_WORLD] = "clean";
    t.after(() => { delete globalThis[DYNAMIC_PREDECESSOR_WORLD]; });
    const tools = await moduleOfTree(target, "tools.js");
    const callVerb = correlationId => withStamps(stamps,
      () => inServedReview(target, { correlationId },
        actor => tools.executeRegisteredTool(caller,
          Object.assign(actor, { id: seatRow.id }), VERB, { idempotency_key: randomUUID() })));

    const eventsFor = async outcomeId => (await setup.query(
      `select count(*)::int as n from event
        where verb = 'record-gate-zero-read-only-outcome'
          and subject_type = 'gate_zero_outcome' and subject_id = $1::uuid`,
      [outcomeId])).rows[0].n;
    const outcomesFor = async candidateDigest => (await setup.query(
      "select count(*)::int as n from ops.gate_zero_read_only_outcome where candidate_digest = $1",
      [candidateDigest])).rows[0].n;

    // (1) THE CALL SUCCEEDS, AND THEN THE OUTER TRANSACTION FAILS. The seat's
    //     work is committed where production commits it; everything the ordinary
    //     writer did -- the audit event and the idempotency envelope's tool_call
    //     row -- goes away with the rollback.
    const served = await callVerb("1f7e4c20-6a35-4b8d-9c42-0e1a7b5d38f6");
    assert.equal(served.served, true, "the recorded review bearer was not served");
    const first = served.answered;
    assert.equal(first.ok, true);
    assert.equal(first.converged_onto_recorded_outcome, false,
      "a first call reported itself as having converged onto somebody else's row");
    assert.equal(first.receipt_status, "pass");
    await caller.seat.query("commit");
    await caller.client.query("rollback");

    // (2) THE STATE THE REVIEWER NAMED, MEASURED. One outcome, no event.
    assert.equal(await outcomesFor(first.candidate_digest), 1);
    assert.equal(await eventsFor(first.outcome_id), 0,
      "the outer rollback did not remove the audit event, so this proof is not staging the failure it claims");

    // (3) THE RETRY, AFTER THE WORLD MOVES. A new request -- its own correlation
    //     id, idempotency key and transactions -- reaches the same candidate,
    //     but the predecessor join now fails. The candidate-scoped projection
    //     therefore differs from the stored pass. Before 0505 the record layer
    //     refused THIS retry, leaving the event unreachable.
    globalThis[DYNAMIC_PREDECESSOR_WORLD] = "receipt-card-mismatch";
    await caller.client.query("begin");
    await caller.client.query(
      "select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
    const retried = (await callVerb("2a8d5b31-7c46-4d9e-8f53-1b2c8e6a49b7")).answered;
    assert.equal(retried.ok, true);
    assert.equal(retried.outcome_id, first.outcome_id,
      "the retry did not converge on the row the failed call left behind");
    assert.equal(retried.converged_onto_recorded_outcome, true,
      "the retry did not report that its changed evidence lost to the recorded row");
    assert.notEqual(retried.offered_candidate_scoped_digest,
      retried.candidate_scoped_digest,
      "the changed-evidence retry did not actually change the candidate projection");
    assert.equal(retried.receipt_status, "pass",
      "the response did not report the status of the stored receipt");
    assert.equal(retried.offered_receipt_status, "fail",
      "the response did not label the changed run's offered status separately");
    assert.equal(retried.producer_reason_id, null,
      "a converged response attributed the offered run's reason to the stored row");
    assert.ok(retried.offered_producer_reason_id,
      "the changed run's offered reason was not reported separately");
    await caller.seat.query("commit");
    await caller.client.query("commit");

    // (4) EXACTLY ONE ROW AND EXACTLY ONE EVENT. The healed state.
    assert.equal(await outcomesFor(first.candidate_digest), 1,
      "the retry minted a second outcome for one candidate");
    assert.equal(await eventsFor(first.outcome_id), 1,
      "the retry did not write the audit event the failed call lost");

    // (5) AND A THIRD CALL WRITES NO SECOND EVENT. Healing a missing event and
    //     duplicating an existing one are the same statement with the guard
    //     removed, so the guard is asserted rather than assumed: one event per
    //     outcome row, however many times the verb is called.
    await caller.client.query("begin");
    await caller.client.query(
      "select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
    const thrice = (await callVerb("3b9e6c42-8d57-4e0f-9a64-2c3d9f7b5ac8")).answered;
    assert.equal(thrice.outcome_id, first.outcome_id);
    await caller.seat.query("commit");
    await caller.client.query("commit");
    assert.equal(await eventsFor(first.outcome_id), 1,
      "a third call wrote a second audit event for one outcome row");
    assert.equal(await outcomesFor(first.candidate_digest), 1);
  });
