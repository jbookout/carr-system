import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  V5_A03_STORE_SCHEMA_VERSION,
  assertCompleteFindingBatch,
  assertReviewParticipantSeparation,
  assertReviewRoundTransition,
  completeSetReviewA03StoreTools,
} from "../src/independent-review-cycle-store.v5.js";
import { V5_REVIEW_DIMENSIONS } from "../src/complete-set-review-a03.vocabulary.v5.js";

const KEY = "11111111-1111-4111-8111-111111111111";
const CASE = "22222222-2222-4222-8222-222222222222";
const DIGEST = `sha256:${"a".repeat(64)}`;
const NEXT_DIGEST = `sha256:${"b".repeat(64)}`;
const ACTOR = Object.freeze({ id: "33333333-3333-4333-8333-333333333333", slug: "reviewer" });

class TestToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.payload = payload;
  }
}

function fakeEnvelope(_c, _actor, _verb, _args, fn) { return fn(); }

function fakeClient(replies = []) {
  const calls = [];
  return {
    calls,
    async query(sql, params = []) {
      calls.push({ sql, params });
      const next = replies.shift();
      if (next instanceof Error) throw next;
      return { rows: next ?? [] };
    },
  };
}

const TOOLS = completeSetReviewA03StoreTools({
  withEnvelope: fakeEnvelope,
  writeEvent: async () => {},
  ToolError: TestToolError,
});

async function refused(promise, code) {
  await assert.rejects(promise, error => error instanceof TestToolError && error.payload.error === code);
}

function participants(overrides = {}) {
  return [
    { role: "builder", actor_ref: "actor:builder", session_ref: "session:builder" },
    { role: "deployment_controller", actor_ref: "actor:releaser", session_ref: "session:releaser" },
    { role: "reviewer", dimension: "architecture", actor_ref: "actor:reviewer", session_ref: "session:reviewer", context_binding: "fresh" },
    { role: "program_controller", actor_ref: "actor:program", session_ref: "session:program" },
  ].map(row => ({ ...row, ...(overrides[row.role] ?? {}) }));
}

function submissions(overrides = {}) {
  return V5_REVIEW_DIMENSIONS.map((dimension, index) => ({
    dimension,
    state: "submitted",
    reviewed_set_digest: DIGEST,
    enumerated_before_repair: true,
    finding_refs: index === 0 ? ["finding:a03-001"] : [],
    ...overrides[dimension],
  }));
}

function batch(overrides = {}) {
  return {
    delivered_set_digest: DIGEST,
    submissions: submissions(),
    repair: {
      repaired_finding_refs: ["finding:a03-001"],
      batch_repair_digest: NEXT_DIGEST,
      regression_suite_ref: "suite:a03",
      checks_executed: ["check:a", "check:b"],
      prior_checks_executed: ["check:a"],
    },
    ...overrides,
  };
}

test("store contract exposes the bounded record/read workflow and no generic verdict setter", () => {
  assert.equal(V5_A03_STORE_SCHEMA_VERSION, "doctorcre-v5-complete-set-review-store.v1");
  assert.deepEqual(Object.keys(TOOLS).sort(), [
    "open-complete-set-review",
    "read-complete-set-review",
    "record-complete-set-adjudication",
    "record-complete-set-finding-set",
    "record-complete-set-participant",
    "seal-complete-set-review-round",
  ]);
  assert.ok(!Object.keys(TOOLS).some(name => /set.*(pass|complete)|approve/i.test(name)));
});

test("identity guard requires maker, reviewer and releaser actor/session separation and fresh review context", () => {
  assert.doesNotThrow(() => assertReviewParticipantSeparation(participants()));
  assert.throws(() => assertReviewParticipantSeparation(participants({ reviewer: { actor_ref: "actor:builder" } })),
    error => error.code === "reviewer_not_role_separated");
  assert.throws(() => assertReviewParticipantSeparation(participants({ reviewer: { session_ref: "session:builder" } })),
    error => error.code === "review_session_not_fresh");
  assert.throws(() => assertReviewParticipantSeparation(participants({ reviewer: { context_binding: "inherited_from_maker" } })),
    error => error.code === "review_context_not_fresh");
  assert.throws(() => assertReviewParticipantSeparation(participants({ deployment_controller: { actor_ref: "actor:reviewer" } })),
    error => error.code === "reviewer_not_role_separated");
});

test("finding-set guard requires all eleven dimensions, whole-set scope, pre-repair enumeration and one full regression", () => {
  assert.doesNotThrow(() => assertCompleteFindingBatch(batch()));
  assert.throws(() => assertCompleteFindingBatch(batch({ submissions: submissions().slice(1) })),
    error => error.code === "finding_set_dimension_absent");
  assert.throws(() => assertCompleteFindingBatch(batch({ submissions: submissions({ architecture: { reviewed_set_digest: NEXT_DIGEST } }) })),
    error => error.code === "review_scope_narrower_than_delivered_set");
  assert.throws(() => assertCompleteFindingBatch(batch({ submissions: submissions({ architecture: { enumerated_before_repair: false } }) })),
    error => error.code === "finding_set_enumerated_after_repair");
  assert.throws(() => assertCompleteFindingBatch(batch({ repair: { ...batch().repair, repaired_finding_refs: [] } })),
    error => error.code === "batch_repair_not_complete");
  assert.throws(() => assertCompleteFindingBatch(batch({ repair: { ...batch().repair, checks_executed: [] } })),
    error => error.code === "regression_evidence_empty");
  assert.throws(() => assertCompleteFindingBatch(batch({ repair: { ...batch().repair, checks_executed: ["check:b"] } })),
    error => error.code === "test_weakening");
});

test("round guard stops a third loop, reopening after adjudication and early or party adjudication", () => {
  assert.doesNotThrow(() => assertReviewRoundTransition({ recorded_rounds: 0, requested_round_ordinal: 1, adjudication: null }));
  assert.doesNotThrow(() => assertReviewRoundTransition({ recorded_rounds: 1, requested_round_ordinal: 2, adjudication: null }));
  assert.throws(() => assertReviewRoundTransition({ recorded_rounds: 2, requested_round_ordinal: 3, adjudication: null }),
    error => error.code === "review_round_limit_exhausted");
  assert.throws(() => assertReviewRoundTransition({ recorded_rounds: 2, requested_round_ordinal: 2, adjudication: { outcome: "fail" } }),
    error => error.code === "review_round_reopened_after_adjudication");
  assert.throws(() => assertReviewRoundTransition({ recorded_rounds: 1, requested_round_ordinal: null,
    adjudication: { outcome: "quarantine", adjudicator_actor_ref: "actor:judge", party_actor_refs: [] } }),
  error => error.code === "adjudication_before_round_limit");
  assert.throws(() => assertReviewRoundTransition({ recorded_rounds: 2, requested_round_ordinal: null,
    adjudication: { outcome: "pass", adjudicator_actor_ref: "actor:reviewer", party_actor_refs: ["actor:reviewer"] } }),
  error => error.code === "adjudicator_is_a_party_to_the_dispute");
});

test("write tools derive the acting actor and delegate transition authority to named SQL functions", async () => {
  const c = fakeClient([
    [{ case_id: CASE, status: "open" }],
    [{ participant_id: "44444444-4444-4444-8444-444444444444" }],
    [{ submission_id: "55555555-5555-4555-8555-555555555555" }],
  ]);
  const opened = await TOOLS["open-complete-set-review"].handler(c, ACTOR, {
    idempotency_key: KEY, change_ref: "change:v5-a03", delivered_set_digest: DIGEST,
    maker_session_ref: "session:maker",
  });
  assert.equal(opened.case_id, CASE);
  assert.equal(c.calls[0].params.at(-1), ACTOR.id);
  assert.match(c.calls[0].sql, /ops\.v5_a03_open_review_case/);

  await TOOLS["record-complete-set-participant"].handler(c, ACTOR, {
    idempotency_key: KEY, case_id: CASE, role: "reviewer", dimension: "architecture",
    session_ref: "session:reviewer", context_binding: "fresh",
  });
  assert.equal(c.calls[1].params.at(-1), ACTOR.id);
  assert.match(c.calls[1].sql, /ops\.v5_a03_record_participant/);

  await TOOLS["record-complete-set-finding-set"].handler(c, ACTOR, {
    idempotency_key: KEY, case_id: CASE, round_ordinal: 1, dimension: "architecture",
    reviewer_session_ref: "session:reviewer", reviewed_set_digest: DIGEST,
    state: "submitted", finding_refs: ["finding:a03-001"], enumerated_before_repair: true,
  });
  assert.equal(c.calls[2].params.at(-1), ACTOR.id);
  assert.match(c.calls[2].sql, /ops\.v5_a03_record_finding_set/);
});

test("caller cannot supply actor identity, completion, round count or adjudication receipt", async () => {
  for (const [name, args] of [
    ["open-complete-set-review", { idempotency_key: KEY, change_ref: "change:v5-a03", delivered_set_digest: DIGEST,
      maker_session_ref: "session:maker", maker_actor_id: ACTOR.id }],
    ["record-complete-set-participant", { idempotency_key: KEY, case_id: CASE, role: "reviewer",
      dimension: "architecture", session_ref: "session:reviewer", context_binding: "fresh", actor_id: ACTOR.id }],
    ["seal-complete-set-review-round", { idempotency_key: KEY, case_id: CASE, round_ordinal: 1,
      batch_repair_digest: NEXT_DIGEST, repaired_finding_refs: [], regression_suite_ref: "suite:a03",
      checks_executed: ["check:a"], recorded_rounds: 0 }],
    ["record-complete-set-adjudication", { idempotency_key: KEY, case_id: CASE,
      adjudicator_session_ref: "session:judge", outcome: "fail", disputed_finding_refs: ["finding:a"],
      receipt: { outcome: "pass" } }],
  ]) {
    const c = fakeClient();
    await refused(TOOLS[name].handler(c, ACTOR, args), "unregistered_field");
    assert.equal(c.calls.length, 0, name);
  }
});

async function importMutant(replaceFrom, replaceTo) {
  const source = await readFile(new URL("../src/independent-review-cycle-store.v5.js", import.meta.url), "utf8");
  assert.ok(source.includes(replaceFrom), `mutation anchor missing: ${replaceFrom}`);
  const vocabularyUrl = new URL("../src/complete-set-review-a03.vocabulary.v5.js", import.meta.url).href;
  const boundaryUrl = new URL("../src/global-boundaries.v5.js", import.meta.url).href;
  const mutated = source.replaceAll("./complete-set-review-a03.vocabulary.v5.js", vocabularyUrl)
    .replaceAll("./global-boundaries.v5.js", boundaryUrl).replace(replaceFrom, replaceTo);
  return import(`data:text/javascript;base64,${Buffer.from(mutated).toString("base64")}`);
}

test("identity planted bug is killed: actor equality may not be replaced by session equality", async () => {
  const mutant = await importMutant("/* MUTANT identity */ actorCollision || sessionCollision",
    "/* MUTANT identity */ actorCollision && sessionCollision");
  assert.throws(() => assert.throws(() => mutant.assertReviewParticipantSeparation(
    participants({ reviewer: { actor_ref: "actor:builder", session_ref: "session:other" } })),
  error => error.code === "reviewer_not_role_separated"), assert.AssertionError);
});

test("finding-set planted bug is killed: every dimension must match the delivered set", async () => {
  const mutant = await importMutant("/* MUTANT scope */ narrowed.length > 0",
    "/* MUTANT scope */ narrowed.length > 1");
  assert.throws(() => assert.throws(() => mutant.assertCompleteFindingBatch(
    batch({ submissions: submissions({ architecture: { reviewed_set_digest: NEXT_DIGEST } }) })),
  error => error.code === "review_scope_narrower_than_delivered_set"), assert.AssertionError);
});

test("round planted bug is killed: ordinal three is already beyond the bound", async () => {
  const mutant = await importMutant("/* MUTANT round-limit */ requested > V5_MAX_REVIEW_ROUNDS",
    "/* MUTANT round-limit */ requested > V5_MAX_REVIEW_ROUNDS + 1");
  assert.throws(() => assert.throws(() => mutant.assertReviewRoundTransition(
    { recorded_rounds: 2, requested_round_ordinal: 3, adjudication: null }),
  error => error.code === "review_round_limit_exhausted"), assert.AssertionError);
});
