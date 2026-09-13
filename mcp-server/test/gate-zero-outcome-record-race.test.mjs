// THE RACE PROOF FOR ops.gate_zero_record_read_only_outcome — two writers, one
// candidate, one durable row (2026-09-13, PR 1014 correction).
//
// WHY THIS FILE EXISTS AND WHY IT IS NOT A MOCK. Every other proof of this verb
// runs against a recording mock, which can show what the handler SENDS and can
// show nothing about what two concurrent transactions do to one unique index.
// The defect this file exists to catch was exactly that: the writer looked the
// candidate up, found nothing, and then inserted, so two runs of the same
// candidate arriving at once both missed the lookup and the loser got a bare
// unique_violation instead of the durable row. A retry policy that says "every
// run kept, a retry collapses onto the row that exists" has to hold WHEN TWO
// WRITERS ACTUALLY RETRY AT ONCE, and only a real PostgreSQL can be asked.
//
// HOW THE INTERLEAVING IS MADE DETERMINISTIC rather than hoped for. Two
// connections, both inside open transactions:
//
//   1. A calls the writer and returns an id. Its row is inserted and UNCOMMITTED,
//      so its entry in the candidate unique index is speculative.
//   2. B calls the writer for the SAME candidate, and its statement is sent and
//      left in flight. B blocks inside the insert, on A's index entry.
//   3. A commits, which releases B.
//   4. B takes the ON CONFLICT DO NOTHING branch, reads the committed row in its
//      fallback select — a new statement, and therefore a new snapshot — and
//      returns the same id A did.
//
// AGAINST THE LOOKUP-THEN-INSERT SHAPE this run fails at step 4 with
// unique_violation, which is what makes it a falsifier rather than a
// demonstration.
//
// IT SKIPS WITHOUT A DATABASE, and it REFUSES a database that is not loopback.
// The rows it writes are committed on purpose — an uncommitted race proves
// nothing — and the record is append-only by trigger, so they cannot be removed
// afterwards. That is safe on the throwaway database the migration class builds
// and is why this file declines to point at anything else.
//
//   DATABASE_URL=postgresql://localhost/... node --test \
//     mcp-server/test/gate-zero-outcome-record-race.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

const DSN = process.env.CARR_GATE_ZERO_RACE_DSN || process.env.DATABASE_URL || "";
// A SKIP THAT NOBODY NOTICES IS A TEST COLLECTED BY NOBODY. The migration class
// sets this, so in the one place this proof is meant to run, "no database" and
// "no reachable Postgres" FAIL instead of skipping quietly. Everywhere else --
// the unit class, a developer's shell -- the skip is the honest answer.
const REQUIRED = process.env.CARR_GATE_ZERO_RACE_REQUIRED === "1";
const LOOPBACK = /@(localhost|127\.0\.0\.1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

/** One digest per run, so a second run of this file races a fresh candidate. */
const candidate = () => `sha256:${randomUUID().replace(/-/g, "")}${randomUUID().replace(/-/g, "")}`;

/**
 * A fixture receipt, and it is a fixture DELIBERATELY. The subject here is the
 * record layer's own function, which takes a receipt and an idempotency key over
 * a raw connection the gateway is not part of — so the honest input is a
 * twenty-one-field object, not a producer run. Every proof that the RECORDED
 * receipt is the producer's own lives in gate-zero-outcome-store.v5.test.mjs,
 * which drives the verb instead.
 */
function receipt(candidateDigest) {
  const observed = new Date(Date.now() - 60_000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const expires = new Date(Date.now() + 86_400_000).toISOString().replace(/\.\d{3}Z$/, "Z");
  return {
    gate_id: "gate-zero-read-only-accepted",
    receipt_producer_step_ref: "step:gate-zero-read-only-outcome",
    subject_digest: `sha256:${"1".repeat(64)}`,
    candidate_digest: candidateDigest,
    policy_digest: `sha256:${"3".repeat(64)}`,
    environment_manifest_digest: `sha256:${"4".repeat(64)}`,
    subject_environment: "candidate",
    evidence_scope: "candidate-and-test",
    subject_maker_identity: {
      actor_id: "joe", session_ref: "session:candidate-build:race-fixture",
      authority_class: "candidate_builder",
    },
    producer_identity: {
      actor_id: "codex-reviewer", session_ref: "session:race-fixture-oracle",
      authority_class: "review_agent",
    },
    evaluator_identity: {
      actor_id: "codex-reviewer", session_ref: "session:race-fixture-oracle",
      authority_class: "review_agent",
    },
    producer_role: "independent_control_plane_oracle",
    independent_oracle_ref: "oracle:gate-producer:gate-zero-read-only",
    oracle_version: "1.0.0",
    evidence_ref: "safe:gate-zero/race/fixture",
    fixture_set_digest: `sha256:${"5".repeat(64)}`,
    observed_at: observed,
    ttl_expires_at: expires,
    status: "pass",
    comparator: "compared the four accepted predecessor outcomes, the scheduler canary readback and the gate conclusion",
    negative_admission_result: "all_required_denials_observed",
  };
}

async function seatedClient(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  // THE SERVER-ESTABLISHED ACTOR CONTEXT, set the way mcp.js sets it. It is not
  // a parameter of the writer and never was; this is the transaction-local
  // setting ops.gate_zero_producer_actor_id() derives the seat from.
  await client.query("select set_config('carr.acting_actor_slug', 'codex-reviewer', true)");
  return client;
}

test("two writers racing one candidate both receive the same durable row", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false,
      "this proof was required and no database URL was given to it");
    return t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof commits rows to an append-only record and runs against a throwaway only");
  const pg = await import("pg");
  const subject = receipt(candidate());

  let a;
  let b;
  try {
    a = await seatedClient(pg.default ?? pg);
    b = await seatedClient(pg.default ?? pg);
  } catch (error) {
    if (a) await a.end().catch(() => {});
    if (b) await b.end().catch(() => {});
    assert.equal(REQUIRED, false, `this proof was required and Postgres was unreachable: ${error.message}`);
    return t.skip(`no reachable Postgres: ${error.message}`);
  }

  t.after(async () => {
    for (const client of [a, b]) await client.end().catch(() => {});
  });

  const call = client => client.query(
    "select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb) as id",
    [randomUUID(), JSON.stringify(subject)]);

  // (1) A inserts and does NOT commit: its candidate-key entry is speculative.
  const first = (await call(a)).rows[0].id;
  assert.match(first, /^[0-9a-f-]{36}$/);

  // (2) B's statement goes out and is left in flight. It blocks inside the
  // insert, on A's uncommitted index entry.
  const pending = call(b);
  let settledEarly = false;
  pending.then(() => { settledEarly = true; }, () => { settledEarly = true; });
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(settledEarly, false,
    "the second writer did not block on the first, so the candidate key is not arbitrating");

  // (3) A commits, which releases B. (4) B answers with the row that exists.
  await a.query("commit");
  const second = (await pending).rows[0].id;
  await b.query("commit");

  assert.equal(second, first,
    "the losing writer did not receive the durable row the winner wrote");

  // AND THERE IS EXACTLY ONE ROW, which is the property the whole shape exists
  // for: every run kept, one current outcome per candidate.
  const rows = await a.query(
    "select count(*)::int as n from ops.gate_zero_read_only_outcome where candidate_digest = $1",
    [subject.candidate_digest]);
  assert.equal(rows.rows[0].n, 1, "a race minted a second outcome for one candidate");
});
