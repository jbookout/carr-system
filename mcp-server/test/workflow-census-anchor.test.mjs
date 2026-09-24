import test from "node:test";
import assert from "node:assert/strict";
import {
  WorkflowCensusAnchor, decideAnchorAdvance, advanceWorkflowCensusAnchor, readWorkflowCensusAnchor,
  WORKFLOW_CENSUS_ANCHOR_OBJECT, anchorCommittedCensusWrite,
} from "../src/workflow-census-anchor.js";
import fs from "node:fs";

const H1 = "1".repeat(64), H2 = "2".repeat(64), H3 = "3".repeat(64);
const NOW = "2026-09-24T04:10:02.000Z";

class FakeStorage {
  constructor() { this.map = new Map(); this.puts = 0; }
  async get(key) { return this.map.get(key); }
  async put(key, value) { this.puts += 1; this.map.set(key, structuredClone(value)); }
}

// A stand-in for the DurableObjectNamespace binding: one real WorkflowCensusAnchor
// instance per name, reached through fetch like the runtime's stub.
function fakeNamespace() {
  const objects = new Map();
  const names = [];
  return {
    names,
    objects,
    idFromName(name) { names.push(name); return { name }; },
    get(id) {
      if (!objects.has(id.name)) objects.set(id.name, new WorkflowCensusAnchor({ storage: new FakeStorage() }, {}));
      const object = objects.get(id.name);
      return { fetch: (url, init = {}) => object.fetch(new Request(url, init)) };
    },
  };
}

test("the anchor advances only forward, replays the same head, and refuses a fork or a regression", () => {
  const first = decideAnchorAdvance(undefined, { seq: 1, row_hash: H1 }, NOW);
  assert.equal(first.write, true);
  assert.deepEqual(first.head, { seq: 1, row_hash: H1, anchored_at: NOW });
  const stored = first.head;
  assert.deepEqual(decideAnchorAdvance(stored, { seq: 2, row_hash: H2 }, NOW).head.seq, 2);
  const skip = decideAnchorAdvance(stored, { seq: 3, row_hash: H3 }, NOW);
  assert.equal(skip.write, true, "a skipped seq (an earlier failed advance) still moves forward");
  const replay = decideAnchorAdvance(stored, { seq: 1, row_hash: H1 }, NOW);
  assert.equal(replay.write, false);
  assert.equal(replay.response.state, "replayed");
  const fork = decideAnchorAdvance(stored, { seq: 1, row_hash: H2 }, NOW);
  assert.equal(fork.write, false);
  assert.equal(fork.status, 409);
  assert.equal(fork.response.error, "anchor_fork_refused");
  const later = decideAnchorAdvance({ seq: 5, row_hash: H1, anchored_at: NOW }, { seq: 4, row_hash: H2 }, NOW);
  assert.equal(later.write, false);
  assert.equal(later.response.error, "anchor_regression_refused");
});

test("the anchor refuses a malformed head and a corrupt stored state", () => {
  for (const bad of [null, {}, { seq: 0, row_hash: H1 }, { seq: 1.5, row_hash: H1 },
    { seq: true, row_hash: H1 }, { seq: 1, row_hash: "A".repeat(64) }, { seq: 1, row_hash: "1" }]) {
    const out = decideAnchorAdvance(undefined, bad, NOW);
    assert.equal(out.write, false);
    assert.equal(out.response.error, "anchor_head_invalid", JSON.stringify(bad));
  }
  const corrupt = decideAnchorAdvance({ seq: "1", row_hash: H1 }, { seq: 2, row_hash: H2 }, NOW);
  assert.equal(corrupt.write, false);
  assert.equal(corrupt.response.error, "anchor_state_invalid");
});

test("advance then read, through the object's own fetch routes, on one named object", async () => {
  const ns = fakeNamespace();
  const env = { WORKFLOW_CENSUS_ANCHOR: ns };
  assert.deepEqual(await readWorkflowCensusAnchor(env), { state: "absent" });
  assert.deepEqual((await advanceWorkflowCensusAnchor(env, { seq: 1, row_hash: H1 })).state, "advanced");
  assert.deepEqual((await advanceWorkflowCensusAnchor(env, { seq: 1, row_hash: H1 })).state, "replayed");
  const fork = await advanceWorkflowCensusAnchor(env, { seq: 1, row_hash: H2 });
  assert.deepEqual(fork, { ok: false, error: "anchor_fork_refused" });
  const read = await readWorkflowCensusAnchor(env);
  assert.equal(read.state, "present");
  assert.equal(read.seq, 1);
  assert.equal(read.row_hash, H1);
  assert.equal(typeof read.anchored_at, "string");
  assert.ok(ns.names.every(name => name === WORKFLOW_CENSUS_ANCHOR_OBJECT));
  assert.equal(ns.objects.size, 1);
  assert.equal(ns.objects.get(WORKFLOW_CENSUS_ANCHOR_OBJECT).ctx.storage.puts, 1,
    "a replay and a refused fork write nothing");
});

test("an unbound or failing anchor is reported, never thrown", async () => {
  assert.deepEqual(await readWorkflowCensusAnchor({}), { state: "unavailable", detail: "anchor_not_bound" });
  assert.deepEqual(await advanceWorkflowCensusAnchor({}, { seq: 1, row_hash: H1 }),
    { ok: false, error: "anchor_not_bound" });
  const broken = { WORKFLOW_CENSUS_ANCHOR: {
    idFromName: () => ({}), get: () => ({ fetch: async () => { throw new Error("network"); } }) } };
  assert.deepEqual(await readWorkflowCensusAnchor(broken), { state: "unavailable", detail: "anchor_unreachable" });
  assert.equal((await advanceWorkflowCensusAnchor(broken, { seq: 1, row_hash: H1 })).error, "anchor_unreachable");
  const lying = { WORKFLOW_CENSUS_ANCHOR: {
    idFromName: () => ({}),
    get: () => ({ fetch: async () => new Response(JSON.stringify({ ok: true, head: { seq: "1", row_hash: H1 } })) }) } };
  assert.deepEqual(await readWorkflowCensusAnchor(lying), { state: "unavailable", detail: "anchor_state_invalid" });
});

test("the object refuses an unknown route and an unparseable body", async () => {
  const object = new WorkflowCensusAnchor({ storage: new FakeStorage() }, {});
  assert.equal((await object.fetch(new Request("https://x/elsewhere"))).status, 404);
  const bad = await object.fetch(new Request("https://x/advance", { method: "POST", body: "not json" }));
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).error, "anchor_head_invalid");
});

test("a committed census write advances the anchor, and a failed advance is refused by name", async () => {
  const env = { WORKFLOW_CENSUS_ANCHOR: fakeNamespace() };
  const committed = { ok: true, seq: 1, row_hash: H1, replayed: false };
  const out = await anchorCommittedCensusWrite(env, committed, payload => Object.assign(new Error("x"), { payload }));
  assert.deepEqual(out, { ...committed, anchor: "advanced" });
  assert.equal((await readWorkflowCensusAnchor(env)).seq, 1);
  await assert.rejects(
    anchorCommittedCensusWrite({}, committed, payload => Object.assign(new Error("refused"), { payload })),
    error => error.payload?.error === "workflow_census_anchor_not_advanced"
      && error.payload.detail === "anchor_not_bound" && error.payload.seq === 1);
});

test("mcp.js anchors the census head only after the write transaction commits", () => {
  const source = fs.readFileSync(new URL("../src/mcp.js", import.meta.url), "utf8");
  const commit = source.indexOf('await client.query("commit");');
  const anchor = source.indexOf("anchorCommittedCensusWrite(env, result");
  assert.ok(commit > 0 && anchor > commit, "the anchor call follows the commit");
  assert.ok(source.slice(commit, anchor).includes('name === "record-workflow-census"'));
  assert.equal(source.split("anchorCommittedCensusWrite(").length - 1, 1, "exactly one call site");
  assert.ok(/workflowCensusAnchor: name === "read-workflow-census"\s*\? \(\) => readWorkflowCensusAnchor\(env\)/
    .test(source), "the read client gets the anchor reader for the census read verb only");
});
