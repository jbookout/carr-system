// V5-A03 against real disposable PostgreSQL. The unit suite proves the JS
// boundary; this proves the append-only relations and transition locks.

import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";

import { V5_REVIEW_DIMENSIONS } from "../src/complete-set-review-a03.vocabulary.v5.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_V5_A03_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const digest = char => `sha256:${char.repeat(64)}`;
const session = label => `session:${label}-${randomUUID()}`;

async function database(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "V5-A03 database proof was required but DATABASE_URL is absent");
    t.skip("the migration class supplies a disposable DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN), "REFUSED: V5-A03 database proof only writes to disposable loopback PostgreSQL");
  return (await import("pg")).default ?? (await import("pg"));
}

// The whole test body runs inside one shared transaction (begin/rollback at
// the bottom), so an expected-failing statement must not leave that
// transaction aborted for everything that follows it. A bare assert.rejects
// against the shared client does exactly that: postgres refuses every later
// statement with "current transaction is aborted" until the transaction ends.
// Wrap each expected rejection in its own savepoint and roll back to it right
// after, so the outer transaction keeps going.
async function expectReject(client, fn, pattern) {
  await client.query("savepoint expect_reject");
  try {
    await assert.rejects(fn(), pattern);
  } finally {
    await client.query("rollback to savepoint expect_reject");
  }
}

async function actor(client, label) {
  const slug = `a03-${label}-${randomUUID().slice(0, 8)}`;
  return (await client.query(
    "insert into public.actor(slug,kind,display_name,active) values($1,'automation',$1,true) returning id,slug",
    [slug])).rows[0];
}

async function openCase(client, maker, label) {
  return (await client.query(
    "select * from ops.v5_a03_open_review_case($1,$2,$3,$4,$5)",
    [`change:${label}-${randomUUID()}`, digest("a"), session(`maker-${label}`), randomUUID(), maker.id])).rows[0].case_id;
}

async function participant(client, caseId, who, role, sessionRef, dimension = null, context = null) {
  return (await client.query(
    "select * from ops.v5_a03_record_participant($1,$2,$3,$4,$5,$6,$7)",
    [caseId, role, dimension, sessionRef, context, randomUUID(), who.id])).rows[0].participant_id;
}

async function findingSets(client, caseId, reviewer, reviewerSession, round, findingsByDimension = {}) {
  for (const dimension of V5_REVIEW_DIMENSIONS) {
    await client.query(
      "select * from ops.v5_a03_record_finding_set($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
      [caseId, round, dimension, reviewerSession, digest("a"), "submitted",
        findingsByDimension[dimension] ?? [], true, randomUUID(), reviewer.id]);
  }
}

async function seal(client, caseId, program, round, repaired, checks, artifact, state) {
  return (await client.query(
    "select * from ops.v5_a03_seal_review_round($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
    [caseId, round, digest(round === 1 ? "b" : "c"), repaired, "suite:a03", checks,
      digest(artifact), state, randomUUID(), program.id])).rows[0];
}

test("DB: complete findings seal in two rounds, checks never shrink, and a third loop cannot begin", async t => {
  const pg = await database(t); if (!pg) return;
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  try {
    const builder = await actor(client, "builder");
    const program = await actor(client, "program");
    const releaser = await actor(client, "releaser");
    const reviewer1 = await actor(client, "reviewer1");
    const reviewer2 = await actor(client, "reviewer2");
    const caseId = await openCase(client, builder, "two-round");

    await expectReject(client, () => participant(client, caseId, builder, "reviewer", session("self"), "architecture", "fresh"),
      /v5_a03_opposing_role_identity_reused/);
    await participant(client, caseId, program, "program_controller", session("program"));
    await participant(client, caseId, releaser, "deployment_controller", session("releaser"));
    const reviewer1Session = session("reviewer1");
    for (const dimension of V5_REVIEW_DIMENSIONS)
      await participant(client, caseId, reviewer1, "reviewer", reviewer1Session, dimension, "fresh");
    await findingSets(client, caseId, reviewer1, reviewer1Session, 1,
      { architecture: ["finding:round-one"] });
    await seal(client, caseId, program, 1, ["finding:round-one"], ["check:a", "check:b"], "d", "changes_required");

    const reviewer2Session = session("reviewer2");
    for (const dimension of V5_REVIEW_DIMENSIONS)
      await participant(client, caseId, reviewer2, "reviewer", reviewer2Session, dimension, "fresh");
    await findingSets(client, caseId, reviewer2, reviewer2Session, 2);
    await expectReject(client, () => seal(client, caseId, program, 2, [], ["check:b"], "e", "no_changes_required"),
      /v5_a03_test_weakening/);
    await seal(client, caseId, program, 2, [], ["check:a", "check:b", "check:c"], "e", "no_changes_required");

    const read = (await client.query("select ops.v5_a03_read_review_case($1) as review", [caseId])).rows[0].review;
    assert.equal(read.status, "reviewed_no_changes_required");
    assert.equal(read.submissions.length, 22);
    assert.equal(read.rounds.length, 2);
    assert.deepEqual(read.rounds[1].checks_executed, ["check:a", "check:b", "check:c"]);
    await expectReject(client, () => client.query(
      "select * from ops.v5_a03_record_finding_set($1,3,'architecture',$2,$3,'submitted',$4,true,$5,$6)",
      [caseId, reviewer2Session, digest("a"), [], randomUUID(), reviewer2.id]), /v5_a03_review_round_limit_exhausted/);
    await expectReject(client, () => client.query("update ops.v5_a03_review_round set state='changes_required' where case_id=$1", [caseId]),
      /append_only/);
  } finally {
    await client.query("rollback");
    await client.end();
  }
});

test("DB: two unresolved rounds require a distinct stronger adjudicator and persist one digested disposition", async t => {
  const pg = await database(t); if (!pg) return;
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  try {
    const builder = await actor(client, "builder");
    const program = await actor(client, "program");
    const releaser = await actor(client, "releaser");
    const reviewer1 = await actor(client, "reviewer1");
    const reviewer2 = await actor(client, "reviewer2");
    const judge = await actor(client, "judge");
    const caseId = await openCase(client, builder, "adjudication");
    await participant(client, caseId, program, "program_controller", session("program"));
    await participant(client, caseId, releaser, "deployment_controller", session("releaser"));

    const reviewer1Session = session("reviewer1");
    for (const dimension of V5_REVIEW_DIMENSIONS)
      await participant(client, caseId, reviewer1, "reviewer", reviewer1Session, dimension, "fresh");
    await findingSets(client, caseId, reviewer1, reviewer1Session, 1,
      { security: ["finding:first-dispute"] });
    await seal(client, caseId, program, 1, ["finding:first-dispute"], ["check:a"], "d", "changes_required");

    const reviewer2Session = session("reviewer2");
    for (const dimension of V5_REVIEW_DIMENSIONS)
      await participant(client, caseId, reviewer2, "reviewer", reviewer2Session, dimension, "fresh");
    await findingSets(client, caseId, reviewer2, reviewer2Session, 2,
      { product: ["finding:second-dispute"] });
    await seal(client, caseId, program, 2, ["finding:second-dispute"], ["check:a", "check:b"], "e", "changes_required");

    await expectReject(client, () => participant(client, caseId, reviewer2, "adjudicator", session("bad-judge")),
      /v5_a03_opposing_role_identity_reused/);
    const judgeSession = session("judge");
    await participant(client, caseId, judge, "adjudicator", judgeSession);
    const adjudication = (await client.query(
      "select * from ops.v5_a03_record_adjudication($1,$2,'quarantine',$3,$4,$5)",
      [caseId, judgeSession, ["finding:second-dispute"], randomUUID(), judge.id])).rows[0];
    assert.match(adjudication.receipt_digest, /^sha256:[0-9a-f]{64}$/);
    assert.equal(adjudication.outcome, "quarantine");

    const read = (await client.query("select ops.v5_a03_read_review_case($1) as review", [caseId])).rows[0].review;
    assert.equal(read.status, "adjudicated");
    assert.equal(read.adjudication.outcome, "quarantine");
    await expectReject(client, () => participant(client, caseId, judge, "architect", session("after-close")),
      /v5_a03_review_round_reopened_after_adjudication/);
  } finally {
    await client.query("rollback");
    await client.end();
  }
});

