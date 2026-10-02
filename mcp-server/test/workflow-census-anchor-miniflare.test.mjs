// The census anchor under the real Durable Object runtime (workerd, through
// Miniflare -- already in the lockfile as wrangler's own dependency, with the
// linux-64 workerd binary npm ci installs on the runner). The unit suite
// (workflow-census-anchor.test.mjs) models blockConcurrencyWhile; this one
// proves the object's serialization against the runtime that actually
// delivers concurrent requests.
//
// THE CONTROL. A Durable Object's storage input gate holds other requests only
// while a storage call is in flight. A read-modify-write that awaits anything
// else between its get and its put (here a 5 ms timer inside storage.get) lets
// a second request interleave. The control class runs the anchor's own code
// with its `serialized` step removed; under concurrent genesis proposals more
// than one of them "advances", which is the lost update. The real class, with
// the same slow storage, advances exactly one.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

// Miniflare is not a direct dependency of this package; it is wrangler's
// (pinned exactly there). Resolve it FROM wrangler so the import follows the
// version wrangler itself runs, whatever npm's hoisting does.
const fromWrangler = createRequire(createRequire(import.meta.url).resolve("wrangler/package.json"));
const { Miniflare, convertV4MiniflareOptions } = await import(pathToFileURL(fromWrangler.resolve("miniflare")).href);

// A worker's main module may export only handlers and classes, so the module's
// constants and helper functions lose their `export` keyword; the class keeps it.
const anchorSource = fs.readFileSync(new URL("../src/workflow-census-anchor.js", import.meta.url), "utf8")
  .replace(/^export (const|function|async function) /gm, "$1 ");

const script = `${anchorSource}
function slowCtx(ctx) {
  const storage = ctx.storage;
  const slow = {
    get: async key => { const value = await storage.get(key); await new Promise(r => setTimeout(r, 5)); return value; },
    put: (...args) => storage.put(...args),
    delete: (...args) => storage.delete(...args),
    list: async (...args) => { const value = await storage.list(...args); await new Promise(r => setTimeout(r, 5)); return value; },
    deleteAll: () => storage.deleteAll(),
  };
  return { storage: slow, blockConcurrencyWhile: fn => ctx.blockConcurrencyWhile(fn) };
}
export class SlowAnchor extends WorkflowCensusAnchor {
  constructor(ctx, env) { super(slowCtx(ctx), env); }
}
export class UnserializedAnchor extends SlowAnchor {
  serialized(fn) { return fn(); }
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const [, binding, name, route] = url.pathname.split("/");
    const ns = env[binding];
    return ns.get(ns.idFromName(name)).fetch(new Request("https://anchor/" + route, request));
  },
};
`;

const H = n => String(n).repeat(64).slice(0, 64);

async function withRuntime(fn) {
  const mf = new Miniflare(convertV4MiniflareOptions({
    compatibilityDate: "2026-07-01",
    // No request.cf data: Miniflare would otherwise fetch it over the network
    // and cache it under the working directory.
    cf: false,
    modules: true,
    script,
    durableObjects: {
      REAL: { className: "WorkflowCensusAnchor", useSQLite: true },
      SLOW: { className: "SlowAnchor", useSQLite: true },
      CONTROL: { className: "UnserializedAnchor", useSQLite: true },
    },
  }));
  try { return await fn(mf); } finally { await mf.dispose(); }
}

async function post(mf, binding, name, route, body) {
  const response = await mf.dispatchFetch(`http://anchor.test/${binding}/${name}/${route}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  return response.json();
}

async function head(mf, binding, name) {
  return (await mf.dispatchFetch(`http://anchor.test/${binding}/${name}/head`)).json();
}

const CONCURRENT = 12;

// The Worker's two steps for one fresh insert: register the pending head under
// its key (before commit), then advance with the same key (after commit).
const keyOf = p => `k-${p.seq}-${p.row_hash.slice(0, 4)}`;
const register = (mf, binding, name, p, key = keyOf(p)) => post(mf, binding, name, "pending", { ...p, idempotency_key: key });
const advance = (mf, binding, name, p, key = keyOf(p)) => post(mf, binding, name, "advance", { ...p, idempotency_key: key });

test("under workerd, concurrent genesis proposals: the unserialized control loses updates, the anchor does not", async () => {
  await withRuntime(async mf => {
    const proposals = Array.from({ length: CONCURRENT }, (_, i) => ({ seq: 1, row_hash: H((i % 9) + 1), prev_hash: null }));
    const distinct = proposals.filter((p, i) => proposals.findIndex(q => q.row_hash === p.row_hash) === i);
    for (const p of distinct) assert.equal((await register(mf, "CONTROL", "c", p)).state, "pending");
    const control = await Promise.all(distinct.map(p => advance(mf, "CONTROL", "c", p)));
    const controlAdvanced = control.filter(r => r.state === "advanced").length;
    assert.ok(controlAdvanced > 1,
      `control: without serialization more than one genesis advanced (${controlAdvanced}) -- the harness sees the race`);
    for (const binding of ["SLOW", "REAL"]) {
      for (const p of distinct) assert.equal((await register(mf, binding, "a", p)).state, "pending");
      const answers = await Promise.all(distinct.map(p => advance(mf, binding, "a", p)));
      const advanced = answers.filter(r => r.state === "advanced");
      assert.equal(advanced.length, 1, `${binding}: exactly one genesis advanced`);
      assert.ok(answers.filter(r => r.ok === false).every(r => r.error === "anchor_fork_refused"),
        `${binding}: every other proposal is a refused fork: ${JSON.stringify(answers)}`);
      const stored = await head(mf, binding, "a");
      assert.equal(stored.head.row_hash, advanced[0].head.row_hash, `${binding}: the stored head is the one that advanced`);
      assert.equal(stored.pending_entries, 0, `${binding}: the advance cleared every genesis entry`);
    }
  });
});

test("under workerd, a burst of linked and forged proposals leaves one contiguous chain", async () => {
  await withRuntime(async mf => {
    // seq n links to H(n-1); a forged seq n links to a hash the anchor never held.
    const burst = [];
    for (let n = 1; n <= 6; n += 1) {
      burst.push({ seq: n, row_hash: H(n), prev_hash: n === 1 ? null : H(n - 1) });
      if (n > 1) burst.push({ seq: n, row_hash: "f".repeat(64), prev_hash: "e".repeat(64) });
    }
    const answers = [];
    // Several rounds of the same burst, concurrently: every proposal tries to
    // register, then every proposal tries to advance. Only the row linked to
    // the anchored head can register, so only it can advance.
    const registrations = [];
    for (let round = 0; round < 6; round += 1) {
      registrations.push(...await Promise.all(burst.map(p => register(mf, "SLOW", "b", p))));
      answers.push(...await Promise.all(burst.map(p => advance(mf, "SLOW", "b", p))));
    }
    assert.ok(!registrations.some(r => r.ok && r.pending.row_hash === "f".repeat(64)), "no forged link ever registered");
    const stored = await head(mf, "SLOW", "b");
    assert.equal(stored.head.seq, 6);
    assert.equal(stored.head.row_hash, H(6));
    const advancedSeqs = answers.filter(r => r.state === "advanced").map(r => r.head.seq).sort((a, b) => a - b);
    assert.deepEqual(advancedSeqs, [1, 2, 3, 4, 5, 6], "each seq advanced exactly once, in order");
    assert.ok(!answers.some(r => r.state === "advanced" && r.head.row_hash === "f".repeat(64)),
      "no forged link ever advanced");
    // Late retries of every anchored seq are replays, from the object's own history.
    const late = await Promise.all(burst.filter(p => p.row_hash !== "f".repeat(64))
      .map(p => post(mf, "SLOW", "b", "advance", p)));
    assert.ok(late.every(r => r.state === "replayed"), JSON.stringify(late));
  });
});

test("under workerd, a re-anchor and a racing advance cannot both land on the old head", async () => {
  await withRuntime(async mf => {
    const r1 = { seq: 1, row_hash: H(1), prev_hash: null }, r2 = { seq: 2, row_hash: H(2), prev_hash: H(1) };
    await register(mf, "SLOW", "r", r1);
    await advance(mf, "SLOW", "r", r1);
    await register(mf, "SLOW", "r", r2);
    const receipt = { receipt_id: "r-1", actor: "joe", recorded_at: "2026-09-24T05:00:00.000000Z",
      rows_reattested: 1, old_head: { seq: 1, row_hash: H(1) }, new_head: { seq: 2, row_hash: H(9) } };
    const [reanchor, advanced] = await Promise.all([
      post(mf, "SLOW", "r", "reanchor", receipt),
      advance(mf, "SLOW", "r", r2),
    ]);
    const landed = [reanchor.ok, advanced.ok].filter(Boolean).length;
    assert.equal(landed, 1, `exactly one of the two lands: ${JSON.stringify({ reanchor, advanced })}`);
    const stored = await head(mf, "SLOW", "r");
    assert.equal(stored.head.seq, 2);
    assert.equal(stored.head.row_hash, reanchor.ok ? H(9) : H(2));
    assert.equal(stored.last_reanchor?.receipt_id ?? null, reanchor.ok ? "r-1" : null);
    assert.equal(stored.pending_entries, 0, "whichever landed cleared the pending entry");
  });
});

test("under workerd, R3-C1: a forged linked row replayed under a key cannot advance; the Worker's own row still can", async () => {
  await withRuntime(async mf => {
    const r1 = { seq: 1, row_hash: H(1), prev_hash: null };
    await register(mf, "SLOW", "l", r1);
    await advance(mf, "SLOW", "l", r1);
    const forged = { seq: 2, row_hash: "f".repeat(64), prev_hash: H(1) };
    // The owner's row under K, which the Worker never inserted: no entry.
    assert.equal((await advance(mf, "SLOW", "l", forged, "K")).error, "anchor_pending_missing_refused");
    // The Worker inserted its own row under K (then crashed); the forged row under K is not it.
    const genuine = { seq: 2, row_hash: H(2), prev_hash: H(1) };
    assert.equal((await register(mf, "SLOW", "l", genuine, "K")).state, "pending");
    assert.equal((await advance(mf, "SLOW", "l", forged, "K")).error, "anchor_pending_mismatch_refused");
    assert.equal((await head(mf, "SLOW", "l")).head.seq, 1, "the anchor did not move");
    // The crash-gap retry of the genuine row under K advances, and clears K.
    assert.equal((await advance(mf, "SLOW", "l", genuine, "K")).state, "advanced");
    const after = await head(mf, "SLOW", "l");
    assert.equal(after.head.row_hash, H(2));
    assert.equal(after.pending_entries, 0);
    assert.equal((await advance(mf, "SLOW", "l", genuine, "K")).state, "replayed", "a late retry is a replay");
  });
});

test("under workerd, concurrent pending entries for the next seq: one advances, none survive, a late registration is refused", async () => {
  await withRuntime(async mf => {
    const rows = Array.from({ length: 9 }, (_, i) => ({ seq: 1, row_hash: H(i + 1), prev_hash: null }));
    const registered = await Promise.all(rows.map(p => register(mf, "SLOW", "p", p)));
    assert.ok(registered.every(r => r.state === "pending"), JSON.stringify(registered));
    assert.equal((await head(mf, "SLOW", "p")).pending_heads, rows.length);
    // Every advance and a fresh registration race; exactly one advance lands.
    const late = { seq: 1, row_hash: "c".repeat(64), prev_hash: null };
    const answers = await Promise.all([...rows.map(p => advance(mf, "SLOW", "p", p)), register(mf, "SLOW", "p", late)]);
    const advanced = answers.filter(r => r.state === "advanced");
    assert.equal(advanced.length, 1, JSON.stringify(answers));
    const lateAnswer = answers.at(-1);
    // Registered before the advance: cleared by it. After: unlinked, refused.
    assert.ok(lateAnswer.state === "pending" || lateAnswer.error === "anchor_pending_unlinked_refused",
      JSON.stringify(lateAnswer));
    const stored = await head(mf, "SLOW", "p");
    assert.equal(stored.pending_entries, 0, "no entry at or below the head survives the advance");
    // A cross-key swap: key B's entry never advances row A.
    const next = { seq: 2, row_hash: H(7), prev_hash: stored.head.row_hash };
    assert.equal((await register(mf, "SLOW", "p", next, "B")).state, "pending");
    assert.equal((await advance(mf, "SLOW", "p", next, "A")).error, "anchor_pending_missing_refused");
    assert.equal((await advance(mf, "SLOW", "p", next, "B")).state, "advanced");
  });
});
