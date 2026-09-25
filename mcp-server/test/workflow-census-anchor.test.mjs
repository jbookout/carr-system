import test from "node:test";
import assert from "node:assert/strict";
import {
  WorkflowCensusAnchor, decideAnchorAdvance, decideAnchorReanchor, advanceWorkflowCensusAnchor,
  readWorkflowCensusAnchor, WORKFLOW_CENSUS_ANCHOR_OBJECT, anchorCommittedCensusWrite,
  applyCommittedCensusReanchor, decidePendingRegister, registerWorkflowCensusPending,
  PENDING_TTL_MS, MAX_PENDING,
} from "../src/workflow-census-anchor.js";
import fs from "node:fs";

const H1 = "1".repeat(64), H2 = "2".repeat(64), H3 = "3".repeat(64), H9 = "9".repeat(64);
const NOW = "2026-09-24T04:10:02.000Z";

// The Durable Object storage API surface the anchor uses: get, put (one key or
// an object of entries, atomically), delete (a key or an array), list by
// prefix (a sorted Map), deleteAll.
class FakeStorage {
  constructor() { this.map = new Map(); this.puts = 0; }
  async get(key) { return structuredClone(this.map.get(key)); }
  async delete(keys) { for (const k of [].concat(keys)) this.map.delete(k); }
  async list({ prefix = "" } = {}) {
    return new Map([...this.map.keys()].filter(k => k.startsWith(prefix)).sort()
      .map(k => [k, structuredClone(this.map.get(k))]));
  }
  async put(keyOrEntries, value) {
    this.puts += 1;
    if (typeof keyOrEntries === "string") this.map.set(keyOrEntries, structuredClone(value));
    else for (const [k, v] of Object.entries(keyOrEntries)) this.map.set(k, structuredClone(v));
  }
  async deleteAll() { this.map.clear(); }
}

// blockConcurrencyWhile, modelled: one callback at a time, in arrival order.
function fakeCtx() {
  let tail = Promise.resolve();
  return {
    storage: new FakeStorage(),
    blockConcurrencyWhile(fn) {
      const run = tail.then(fn);
      tail = run.catch(() => {});
      return run;
    },
  };
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
      if (!objects.has(id.name)) objects.set(id.name, new WorkflowCensusAnchor(fakeCtx(), {}));
      const object = objects.get(id.name);
      return { fetch: (url, init = {}) => object.fetch(new Request(url, init)) };
    },
  };
}

const head = (seq, row_hash) => ({ seq, row_hash, anchored_at: NOW });
const LATER = new Date(Date.parse(NOW) + PENDING_TTL_MS).toISOString();
// The live pending entry a Worker registered for a proposal under key k.
const entryFor = p => ({ seq: p.seq, prev_hash: p.prev_hash, row_hash: p.row_hash, registered_at: NOW,
  expires_at: LATER });
const registered = (proposal, key = "k") => k => (k === key ? entryFor(proposal) : undefined);
const advanceWith = (stored, proposal, key = "k", history = () => undefined) =>
  decideAnchorAdvance(stored, { ...proposal, idempotency_key: key }, NOW, history, registered(proposal, key));

test("strict linkage: genesis is seq 1 with no predecessor, then only seq + 1 linked to the anchored hash", () => {
  const first = advanceWith(undefined, { seq: 1, row_hash: H1, prev_hash: null });
  assert.equal(first.write, true);
  assert.deepEqual(first.head, head(1, H1));
  assert.equal(first.clear, "k", "the advance clears the pending entry it matched");
  const next = advanceWith(first.head, { seq: 2, row_hash: H2, prev_hash: H1 });
  assert.equal(next.write, true);
  assert.equal(next.response.state, "advanced");
  for (const [label, stored, proposed, error] of [
    ["a first head that is not seq 1", undefined, { seq: 3, row_hash: H3, prev_hash: H2 }, "anchor_genesis_refused"],
    ["a skipped seq", head(1, H1), { seq: 3, row_hash: H3, prev_hash: H2 }, "anchor_gap_refused"],
    ["the next seq linked to another hash (a rewritten chain)", head(1, H1),
      { seq: 2, row_hash: H2, prev_hash: H9 }, "anchor_link_refused"],
    ["the anchored seq under another hash", head(2, H2), { seq: 2, row_hash: H9, prev_hash: H1 }, "anchor_fork_refused"],
    ["an older seq the object never recorded", head(5, H1), { seq: 4, row_hash: H2, prev_hash: H3 },
      "anchor_regression_refused"],
  ]) {
    const out = advanceWith(stored, proposed);
    assert.equal(out.write, false, label);
    assert.equal(out.status, 409, label);
    assert.equal(out.response.error, error, label);
  }
});

test("a late retry of an already-anchored seq is replayed, and a different hash there is a fork", () => {
  const stored = head(3, H3);
  const same = decideAnchorAdvance(stored, { seq: 3, row_hash: H3, prev_hash: H2 }, NOW);
  assert.equal(same.write, false);
  assert.equal(same.response.state, "replayed");
  const history = seq => ({ 2: H2 })[seq];
  const late = decideAnchorAdvance(stored, { seq: 2, row_hash: H2, prev_hash: H1 }, NOW, history);
  assert.equal(late.write, false);
  assert.equal(late.response.state, "replayed", "a late retry of seq 2 after seq 3 is replayed, not a regression");
  const forked = decideAnchorAdvance(stored, { seq: 2, row_hash: H9, prev_hash: H1 }, NOW, history);
  assert.equal(forked.response.error, "anchor_fork_refused");
});

test("the anchor refuses a malformed proposal and a corrupt stored state", () => {
  for (const bad of [null, {}, { seq: 0, row_hash: H1, prev_hash: null }, { seq: 1.5, row_hash: H1, prev_hash: null },
    { seq: true, row_hash: H1, prev_hash: null }, { seq: 1, row_hash: "A".repeat(64), prev_hash: null },
    { seq: 1, row_hash: "1", prev_hash: null }, { seq: 1, row_hash: H1, prev_hash: H2 },
    { seq: 1, row_hash: H1 }, { seq: 2, row_hash: H2, prev_hash: null }, { seq: 2, row_hash: H2 }]) {
    const out = decideAnchorAdvance(undefined, bad, NOW);
    assert.equal(out.write, false);
    assert.equal(out.response.error, "anchor_head_invalid", JSON.stringify(bad));
  }
  const corrupt = decideAnchorAdvance({ seq: "1", row_hash: H1 }, { seq: 2, row_hash: H2, prev_hash: H1 }, NOW);
  assert.equal(corrupt.write, false);
  assert.equal(corrupt.response.error, "anchor_state_invalid");
});

const RECEIPT = Object.freeze({ receipt_id: "r-1", actor: "joe", recorded_at: "2026-09-24T05:00:00.000000Z",
  rows_reattested: 1, old_head: { seq: 1, row_hash: H1 }, new_head: { seq: 2, row_hash: H2 } });

test("a re-anchor is a compare-and-set on the old head its receipt names, and replays by receipt id", () => {
  const applied = decideAnchorReanchor(head(1, H1), null, RECEIPT, NOW);
  assert.equal(applied.write, true);
  assert.deepEqual(applied.head, head(2, H2));
  assert.equal(applied.record.receipt_id, "r-1");
  assert.equal(applied.response.state, "reanchored");
  const moved = decideAnchorReanchor(head(1, H9), null, RECEIPT, NOW);
  assert.equal(moved.response.error, "anchor_reanchor_conflict");
  const replay = decideAnchorReanchor(head(2, H2), applied.record, RECEIPT, NOW);
  assert.equal(replay.write, false);
  assert.equal(replay.response.state, "replayed");
  const toEmpty = decideAnchorReanchor(head(2, H2), null,
    { ...RECEIPT, receipt_id: "r-2", old_head: { seq: 2, row_hash: H2 }, new_head: null }, NOW);
  assert.equal(toEmpty.write, true);
  assert.equal(toEmpty.head, null, "a restore to an empty store clears the head");
  for (const bad of [null, { ...RECEIPT, receipt_id: "" }, { ...RECEIPT, rows_reattested: -1 },
    { ...RECEIPT, new_head: { seq: 1, row_hash: H1 } }, { ...RECEIPT, old_head: { seq: 0, row_hash: H1 } }])
    assert.equal(decideAnchorReanchor(head(1, H1), null, bad, NOW).response.error, "anchor_reanchor_invalid");
});

test("advance, replay, refuse and re-anchor through the object's own routes, on one named object", async () => {
  const ns = fakeNamespace();
  const env = { WORKFLOW_CENSUS_ANCHOR: ns };
  assert.deepEqual(await readWorkflowCensusAnchor(env), { state: "absent", last_reanchor: null });
  const r1 = { seq: 1, row_hash: H1, prev_hash: null }, r2 = { seq: 2, row_hash: H2, prev_hash: H1 };
  assert.equal((await registerWorkflowCensusPending(env, r1, "k1")).state, "pending");
  assert.equal((await advanceWorkflowCensusAnchor(env, r1, "k1")).state, "advanced");
  assert.equal((await registerWorkflowCensusPending(env, r2, "k2")).state, "pending");
  assert.equal((await advanceWorkflowCensusAnchor(env, r2, "k2")).state, "advanced");
  assert.equal((await advanceWorkflowCensusAnchor(env, r1, "k1")).state, "replayed",
    "a late retry of seq 1 after seq 2 is replayed from the object's history");
  assert.deepEqual(await advanceWorkflowCensusAnchor(env, { seq: 3, row_hash: H3, prev_hash: H9 }),
    { ok: false, error: "anchor_link_refused" });
  assert.deepEqual(await advanceWorkflowCensusAnchor(env, { seq: 4, row_hash: H3, prev_hash: H2 }),
    { ok: false, error: "anchor_gap_refused" });
  const object = ns.objects.get(WORKFLOW_CENSUS_ANCHOR_OBJECT);
  assert.equal(object.ctx.storage.puts, 4, "two registrations and two advances; replays and refusals write nothing");
  assert.deepEqual([...(await object.ctx.storage.list({ prefix: "pending:" })).keys()], [],
    "each advance cleared the entry it matched");
  const read = await readWorkflowCensusAnchor(env);
  assert.equal(read.state, "present");
  assert.equal(read.seq, 2);
  assert.equal(read.row_hash, H2);
  assert.equal(read.last_reanchor, null);
  // Re-anchor to a database head the anchor never took (seq 3 linked elsewhere).
  const receipt = { ...RECEIPT, old_head: { seq: 2, row_hash: H2 }, new_head: { seq: 3, row_hash: H9 } };
  const out = await applyCommittedCensusReanchor(env, { ok: true, receipt }, p => Object.assign(new Error("x"), { payload: p }));
  assert.equal(out.anchor, "reanchored");
  const after = await readWorkflowCensusAnchor(env);
  assert.equal(after.seq, 3);
  assert.equal(after.row_hash, H9);
  assert.equal(after.last_reanchor.receipt_id, "r-1");
  assert.equal(after.last_reanchor.rows_reattested, 1);
  assert.equal((await applyCommittedCensusReanchor(env, { ok: true, receipt }, p => p)).anchor, "replayed");
  assert.equal((await advanceWorkflowCensusAnchor(env, { seq: 1, row_hash: H1, prev_hash: null })).error,
    "anchor_regression_refused", "a re-anchor drops the history it no longer vouches for");
  const r4 = { seq: 4, row_hash: H3, prev_hash: H9 };
  assert.equal((await registerWorkflowCensusPending(env, r4, "k4")).state, "pending");
  assert.equal((await advanceWorkflowCensusAnchor(env, r4, "k4")).state, "advanced",
    "after a re-anchor the next registered linked row advances as usual");
  await assert.rejects(applyCommittedCensusReanchor(env, { ok: true, receipt: { ...receipt, receipt_id: "r-3" } },
    p => Object.assign(new Error("refused"), { payload: p })),
  error => error.payload?.error === "workflow_census_reanchor_not_applied"
    && error.payload.detail === "anchor_reanchor_conflict");
  assert.ok(ns.names.every(name => name === WORKFLOW_CENSUS_ANCHOR_OBJECT));
  assert.equal(ns.objects.size, 1);
});

test("an unbound or failing anchor is reported, never thrown", async () => {
  assert.deepEqual(await readWorkflowCensusAnchor({}), { state: "unavailable", detail: "anchor_not_bound" });
  assert.deepEqual(await advanceWorkflowCensusAnchor({}, { seq: 1, row_hash: H1, prev_hash: null }),
    { ok: false, error: "anchor_not_bound" });
  const broken = { WORKFLOW_CENSUS_ANCHOR: {
    idFromName: () => ({}), get: () => ({ fetch: async () => { throw new Error("network"); } }) } };
  assert.deepEqual(await readWorkflowCensusAnchor(broken), { state: "unavailable", detail: "anchor_unreachable" });
  assert.equal((await advanceWorkflowCensusAnchor(broken, { seq: 1, row_hash: H1, prev_hash: null })).error,
    "anchor_unreachable");
  const lying = { WORKFLOW_CENSUS_ANCHOR: {
    idFromName: () => ({}),
    get: () => ({ fetch: async () => new Response(JSON.stringify({ ok: true, head: { seq: "1", row_hash: H1 } })) }) } };
  assert.deepEqual(await readWorkflowCensusAnchor(lying), { state: "unavailable", detail: "anchor_state_invalid" });
});

test("the object refuses an unknown route and an unparseable body", async () => {
  const object = new WorkflowCensusAnchor(fakeCtx(), {});
  assert.equal((await object.fetch(new Request("https://x/elsewhere"))).status, 404);
  for (const route of ["advance", "reanchor", "pending"]) {
    const bad = await object.fetch(new Request(`https://x/${route}`, { method: "POST", body: "not json" }));
    assert.equal(bad.status, 400);
    assert.equal((await bad.json()).error, "anchor_head_invalid");
  }
});

test("a committed census write advances the anchor with its prev_hash, and a failed advance is refused by name", async () => {
  const env = { WORKFLOW_CENSUS_ANCHOR: fakeNamespace() };
  const committed = { ok: true, seq: 1, row_hash: H1, prev_hash: null, replayed: false };
  await registerWorkflowCensusPending(env, committed, "k-c");
  const out = await anchorCommittedCensusWrite(env, committed, payload => Object.assign(new Error("x"), { payload }),
    "k-c");
  assert.deepEqual(out, { ...committed, anchor: "advanced" });
  assert.equal((await readWorkflowCensusAnchor(env)).seq, 1);
  await assert.rejects(
    anchorCommittedCensusWrite(env, { ok: true, seq: 2, row_hash: H2, prev_hash: H9 },
      payload => Object.assign(new Error("refused"), { payload }), "k-d"),
    error => error.payload?.error === "workflow_census_anchor_not_advanced"
      && error.payload.detail === "anchor_link_refused");
  await assert.rejects(
    anchorCommittedCensusWrite({}, committed, payload => Object.assign(new Error("refused"), { payload })),
    error => error.payload?.error === "workflow_census_anchor_not_advanced"
      && error.payload.detail === "anchor_not_bound" && error.payload.seq === 1);
});

test("the object runs every read-modify-write inside blockConcurrencyWhile", () => {
  const source = fs.readFileSync(new URL("../src/workflow-census-anchor.js", import.meta.url), "utf8");
  const fetchBody = source.slice(source.indexOf("  async fetch(request) {"), source.indexOf("function anchorStub"));
  assert.ok(fetchBody.includes("this.serialized(async () => {"));
  assert.equal(fetchBody.split("storage.put(").length - 1, 3, "every put sits inside the serialized block");
  assert.equal(fetchBody.split("storage.delete(").length - 1, 2, "every delete sits inside the serialized block");
  // Every storage call in fetch sits inside one of the two serialized blocks
  // (GET /head, and the POST routes), never before them.
  const getBlock = fetchBody.indexOf("this.serialized(async () => [");
  const postBlock = fetchBody.indexOf("this.serialized(async () => {");
  const postRoutes = fetchBody.indexOf('if (request.method === "POST"');
  assert.ok(getBlock > 0 && getBlock < postRoutes && postBlock > postRoutes);
  const between = fetchBody.slice(0, getBlock) + fetchBody.slice(postRoutes, postBlock);
  assert.ok(!/storage\.(get|put|delete|list|deleteAll)\(/.test(between), "no storage call outside a serialized block");
  assert.equal(fetchBody.split("storage.list(").length - 1, 3, "GET /head, register and advance list pending entries");
  assert.ok(source.includes("return this.ctx.blockConcurrencyWhile(fn);"));
});

test("mcp.js anchors the census head, and applies a re-anchor, only after the write transaction commits", () => {
  const source = fs.readFileSync(new URL("../src/mcp.js", import.meta.url), "utf8");
  const commit = source.indexOf('await client.query("commit");');
  const anchor = source.indexOf("anchorCommittedCensusWrite(env, result");
  const reanchor = source.indexOf("applyCommittedCensusReanchor(env, result");
  assert.ok(commit > 0 && anchor > commit && reanchor > commit, "both anchor calls follow the commit");
  assert.ok(source.slice(commit, anchor).includes('name === "record-workflow-census"'));
  assert.ok(source.slice(anchor, reanchor).includes('name === "record-workflow-census-reanchor"'));
  assert.equal(source.split("anchorCommittedCensusWrite(").length - 1, 1, "exactly one call site");
  assert.equal(source.split("applyCommittedCensusReanchor(").length - 1, 1, "exactly one call site");
  assert.ok(/workflowCensusAnchor: name === "read-workflow-census"\s*\? \(\) => readWorkflowCensusAnchor\(env\)/
    .test(source), "the read client gets the anchor reader for the census read verb only");
  assert.ok(/if \(name === "record-workflow-census" \|\| name === "record-workflow-census-reanchor"\)\s*client\.workflowCensusAnchor = \(\) => readWorkflowCensusAnchor\(env\);/
    .test(source), "the write client gets the anchor reader for the census write and re-anchor only");
  assert.ok(/if \(name === "record-workflow-census"\)\s*client\.workflowCensusPending = \(row, key\) => registerWorkflowCensusPending\(env, row, key\);/
    .test(source), "only the census write registers pending heads");
  const writeCommit = source.indexOf('await client.query("commit");', source.indexOf("client.workflowCensusPending ="));
  assert.ok(writeCommit > 0 && anchor > writeCommit, "registration is attached before the write commit it precedes");
  assert.ok(/anchorCommittedCensusWrite\(env, result, payload => new ToolError\(payload\),\s*args\?\.idempotency_key\)/
    .test(source), "the post-commit advance names the write's idempotency key");
});

// R3-C1: the owner forges a row linked to the anchored head under key K and
// calls the write verb with K; the door replays it. The anchor never saw the
// Worker insert that row, so it has no pending entry that matches it.
test("replay laundering: a linked row the Worker never inserted cannot move the anchor", () => {
  const stored = head(2, H2);
  const forged = { seq: 3, row_hash: H9, prev_hash: H2, idempotency_key: "K" };
  const none = decideAnchorAdvance(stored, forged, NOW);
  assert.equal(none.write, false);
  assert.equal(none.response.error, "anchor_pending_missing_refused");
  const keyless = decideAnchorAdvance(stored, { seq: 3, row_hash: H9, prev_hash: H2 }, NOW, () => undefined,
    () => entryFor({ seq: 3, row_hash: H9, prev_hash: H2 }));
  assert.equal(keyless.response.error, "anchor_pending_missing_refused", "no key, no entry");
  // K's entry names the row the Worker did insert (then rolled back or crashed);
  // the forged row under K is not it.
  const genuine = { seq: 3, row_hash: H3, prev_hash: H2 };
  const mismatch = decideAnchorAdvance(stored, forged, NOW, () => undefined, registered(genuine, "K"));
  assert.equal(mismatch.response.error, "anchor_pending_mismatch_refused");
  // Another key's entry does not lend itself to K.
  const other = decideAnchorAdvance(stored, forged, NOW, () => undefined, registered({ ...forged }, "other"));
  assert.equal(other.response.error, "anchor_pending_missing_refused");
  for (const field of ["seq", "row_hash", "prev_hash"]) {
    const entry = { ...entryFor(genuine), [field]: field === "seq" ? 4 : H1 };
    const out = decideAnchorAdvance(stored, { ...genuine, idempotency_key: "K" }, NOW, () => undefined, () => entry);
    assert.equal(out.response.error, "anchor_pending_mismatch_refused", field);
  }
  const expired = decideAnchorAdvance(stored, { ...genuine, idempotency_key: "K" }, NOW, () => undefined,
    () => ({ ...entryFor(genuine), expires_at: NOW }));
  assert.equal(expired.response.error, "anchor_pending_expired_refused");
  const matched = decideAnchorAdvance(stored, { ...genuine, idempotency_key: "K" }, NOW, () => undefined,
    registered(genuine, "K"));
  assert.equal(matched.response.state, "advanced", "the crash-gap retry of the Worker's own row advances");
  const replay = decideAnchorAdvance(head(3, H3), { ...genuine, idempotency_key: "K" }, NOW);
  assert.equal(replay.response.state, "replayed", "a late retry that moves nothing needs no entry");
});

test("a pending head registers only as the next linked row, replaces its own key, prunes the dead, and is bounded", () => {
  const stored = head(2, H2);
  const next = { seq: 3, row_hash: H3, prev_hash: H2, idempotency_key: "a" };
  const ok = decidePendingRegister(stored, {}, next, NOW);
  assert.equal(ok.write, true);
  assert.equal(ok.key, "a");
  assert.deepEqual(ok.entry, { seq: 3, prev_hash: H2, row_hash: H3, registered_at: NOW, expires_at: LATER });
  for (const [label, proposed, error] of [
    ["unlinked", { ...next, prev_hash: H9 }, "anchor_pending_unlinked_refused"],
    ["skipped seq", { ...next, seq: 4 }, "anchor_pending_unlinked_refused"],
    ["the anchored seq", { ...next, seq: 2, prev_hash: H1 }, "anchor_pending_unlinked_refused"],
    ["no key", { seq: 3, row_hash: H3, prev_hash: H2 }, "anchor_pending_invalid"],
    ["long key", { ...next, idempotency_key: "x".repeat(201) }, "anchor_pending_invalid"],
    ["bad hash", { ...next, row_hash: "3" }, "anchor_pending_invalid"],
  ]) assert.equal(decidePendingRegister(stored, {}, proposed, NOW).response.error, error, label);
  assert.equal(decidePendingRegister(undefined, {}, { seq: 1, row_hash: H1, prev_hash: null, idempotency_key: "g" },
    NOW).write, true, "genesis registers against an empty anchor");
  const live = { seq: 3, prev_hash: H2, row_hash: H9, registered_at: NOW, expires_at: LATER };
  const pending = { a: live, b: live, dead: { ...live, seq: 2 }, old: { ...live, expires_at: NOW },
    junk: { nonsense: true } };
  const replaced = decidePendingRegister(stored, pending, next, NOW);
  assert.equal(replaced.write, true, "a key re-registers (its earlier transaction rolled back)");
  assert.deepEqual(replaced.prune.sort(), ["dead", "junk", "old"]);
  const full = Object.fromEntries(Array.from({ length: MAX_PENDING }, (_, i) => [`p${i}`, live]));
  assert.equal(decidePendingRegister(stored, full, next, NOW).response.error, "anchor_pending_full_refused");
  assert.equal(decidePendingRegister(stored, full, { ...next, idempotency_key: "p0" }, NOW).write, true,
    "a key already holding an entry can always replace it");
});

test("through the object: an advance clears its entry and every entry at or below the new head; a re-anchor clears all", async () => {
  const ns = fakeNamespace();
  const env = { WORKFLOW_CENSUS_ANCHOR: ns };
  const r1 = { seq: 1, row_hash: H1, prev_hash: null };
  await registerWorkflowCensusPending(env, r1, "a");
  await registerWorkflowCensusPending(env, { ...r1, row_hash: H9 }, "b");
  const storage = ns.objects.get(WORKFLOW_CENSUS_ANCHOR_OBJECT).ctx.storage;
  assert.deepEqual([...(await storage.list({ prefix: "pending:" })).keys()], ["pending:a", "pending:b"]);
  assert.equal((await advanceWorkflowCensusAnchor(env, { ...r1, row_hash: H9 }, "a")).error,
    "anchor_pending_mismatch_refused", "a's entry names H1, not H9");
  assert.equal((await advanceWorkflowCensusAnchor(env, r1, "a")).state, "advanced");
  assert.deepEqual([...(await storage.list({ prefix: "pending:" })).keys()], [],
    "b's entry for seq 1 died with the advance");
  assert.equal((await advanceWorkflowCensusAnchor(env, { ...r1, row_hash: H9 }, "b")).error, "anchor_fork_refused");
  assert.equal((await registerWorkflowCensusPending(env, { seq: 2, row_hash: H2, prev_hash: H1 }, "c")).state, "pending");
  const receipt = { ...RECEIPT, old_head: { seq: 1, row_hash: H1 }, new_head: { seq: 2, row_hash: H3 } };
  assert.equal((await applyCommittedCensusReanchor(env, { ok: true, receipt }, p => p)).anchor, "reanchored");
  assert.deepEqual([...(await storage.list({ prefix: "pending:" })).keys()], [], "a re-anchor clears every entry");
});

test("through the object: registering prunes expired entries, and an expired entry cannot advance", async () => {
  const ns = fakeNamespace();
  const env = { WORKFLOW_CENSUS_ANCHOR: ns };
  const r1 = { seq: 1, row_hash: H1, prev_hash: null };
  await registerWorkflowCensusPending(env, r1, "fresh");
  const storage = ns.objects.get(WORKFLOW_CENSUS_ANCHOR_OBJECT).ctx.storage;
  const past = "2000-01-01T00:00:00.000Z";
  await storage.put("pending:stale", { seq: 1, prev_hash: null, row_hash: H9, registered_at: past, expires_at: past });
  assert.equal((await advanceWorkflowCensusAnchor(env, { ...r1, row_hash: H9 }, "stale")).error,
    "anchor_pending_expired_refused");
  await registerWorkflowCensusPending(env, { ...r1, row_hash: H2 }, "other");
  assert.deepEqual([...(await storage.list({ prefix: "pending:" })).keys()], ["pending:fresh", "pending:other"],
    "the expired entry was pruned by the next registration");
  const object = ns.objects.get(WORKFLOW_CENSUS_ANCHOR_OBJECT);
  const read = await (await object.fetch(new Request("https://workflow-census-anchor/head"))).json();
  assert.equal(read.pending_heads, 2);
});
