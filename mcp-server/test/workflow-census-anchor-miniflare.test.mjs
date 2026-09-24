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

test("under workerd, concurrent genesis proposals: the unserialized control loses updates, the anchor does not", async () => {
  await withRuntime(async mf => {
    const proposals = Array.from({ length: CONCURRENT }, (_, i) => ({ seq: 1, row_hash: H((i % 9) + 1), prev_hash: null }));
    const distinct = proposals.filter((p, i) => proposals.findIndex(q => q.row_hash === p.row_hash) === i);
    const control = await Promise.all(distinct.map(p => post(mf, "CONTROL", "c", "advance", p)));
    const controlAdvanced = control.filter(r => r.state === "advanced").length;
    assert.ok(controlAdvanced > 1,
      `control: without serialization more than one genesis advanced (${controlAdvanced}) -- the harness sees the race`);
    for (const binding of ["SLOW", "REAL"]) {
      const answers = await Promise.all(distinct.map(p => post(mf, binding, "a", "advance", p)));
      const advanced = answers.filter(r => r.state === "advanced");
      assert.equal(advanced.length, 1, `${binding}: exactly one genesis advanced`);
      assert.ok(answers.filter(r => r.ok === false).every(r => r.error === "anchor_fork_refused"),
        `${binding}: every other proposal is a refused fork: ${JSON.stringify(answers)}`);
      const stored = await head(mf, binding, "a");
      assert.equal(stored.head.row_hash, advanced[0].head.row_hash, `${binding}: the stored head is the one that advanced`);
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
    // Several rounds of the same burst, concurrently, until the chain stops moving.
    for (let round = 0; round < 6; round += 1)
      answers.push(...await Promise.all(burst.map(p => post(mf, "SLOW", "b", "advance", p))));
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
    await post(mf, "SLOW", "r", "advance", { seq: 1, row_hash: H(1), prev_hash: null });
    const receipt = { receipt_id: "r-1", actor: "joe", recorded_at: "2026-09-24T05:00:00.000000Z",
      rows_reattested: 1, old_head: { seq: 1, row_hash: H(1) }, new_head: { seq: 2, row_hash: H(9) } };
    const [reanchor, advance] = await Promise.all([
      post(mf, "SLOW", "r", "reanchor", receipt),
      post(mf, "SLOW", "r", "advance", { seq: 2, row_hash: H(2), prev_hash: H(1) }),
    ]);
    const landed = [reanchor.ok, advance.ok].filter(Boolean).length;
    assert.equal(landed, 1, `exactly one of the two lands: ${JSON.stringify({ reanchor, advance })}`);
    const stored = await head(mf, "SLOW", "r");
    assert.equal(stored.head.seq, 2);
    assert.equal(stored.head.row_hash, reanchor.ok ? H(9) : H(2));
    assert.equal(stored.last_reanchor?.receipt_id ?? null, reanchor.ok ? "r-1" : null);
  });
});
