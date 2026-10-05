import { unpackJevState } from "../src/jev-spend-authority.js";
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import {
  FIXTURE_PATH, LIVE_COMMAND, loadFixture, ndcgAt, wrongTop1, percentile, estimateTokens,
  evaluateVariant, pythonJevBridge, recordingAsk, replayAsk, main,
} from "../../evals/retrieval/jev-rerank-eval.mjs";
import { DOCTRINE_TAXONOMY_SNAPSHOT } from "../src/doctrine-taxonomy-snapshot.v1.js";
import { isScriptEntrypoint } from "../../ops/scac-mutation-inventory.mjs";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const PRICE = JSON.parse(readFileSync(resolve(REPO, "ops/config/jev-cost-guard.v1.json"), "utf8"))
  .price_usd_per_million_input_tokens;

function close(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(actual - expected) < epsilon, `${actual} != ${expected}`);
}

// TEST-ONLY synthetic Jev: answers from the fixture's own labels. It proves
// the metric plumbing reaches 1.0 when the order is ideal. It is never a
// measurement of Jev and the harness has no mode that uses it.
function labelOracle(fixture) {
  const bySituation = new Map(fixture.cases.map(c => [c.situation, c.candidates]));
  return async request => {
    const candidates = bySituation.get(unpackJevState(request.state).state.situation) || [];
    const answers = {};
    for (const [key, q] of Object.entries(request.questions)) {
      const hit = candidates.find(x => q.instructions.includes(`"${x.title}" in document "${x.doc_slug}"`));
      const label = hit ? hit.relevance : 0;
      answers[key] = q.type === "noul" ? { type: "noul", noul: label / 3 }
        : { type: "score", score: label * 3, confidence: 0.8 };
    }
    return { model: "synthetic-label-oracle", answers, usage: { input_tokens: 1000, output_tokens: 10 } };
  };
}

test("nDCG@10, wrong-top-1 and p95 are computed the textbook way", () => {
  close(ndcgAt([3, 2, 1, 0], 10), 1);
  close(ndcgAt([0, 3], 10), (7 / Math.log2(3)) / 7);
  close(ndcgAt([0, 0], 10), 0);
  assert.equal(ndcgAt([0, 0], 10, { allZero: null }), null);
  // Only the first k positions count.
  close(ndcgAt([0, 0, 3], 2), 0);
  assert.equal(wrongTop1([1, 3]), true);
  assert.equal(wrongTop1([3, 3]), false);
  assert.equal(wrongTop1([2, 1]), false);
  assert.equal(percentile([5, 1, 3, 2, 4], 95), 5);
  assert.equal(percentile(Array.from({ length: 20 }, (_, i) => i + 1), 95), 19);
  assert.equal(percentile([], 95), null);
  assert.equal(estimateTokens({ state: { situation: "abc" }, questions: {} }), Math.ceil(
    JSON.stringify({ state: { situation: "abc" }, questions: {} }).length / 3));
});

test("the fixture is thirteen real labeled shortlists that map onto the pinned taxonomy", () => {
  const fixture = loadFixture();
  assert.match(FIXTURE_PATH, /evals\/retrieval\/fixtures\/jev-rerank-shortlists\.2026-09-29\.v1\.json$/);
  assert.equal(fixture.schema_version, "carr-jev-rerank-shortlists-v1");
  assert.equal(fixture.doctrine_generation, 1247);
  assert.equal(fixture.cases.length, 13);
  const bySlug = new Map(DOCTRINE_TAXONOMY_SNAPSHOT.documents.map(d => [d.slug, d]));
  for (const c of fixture.cases) {
    assert.ok(c.situation.length > 0 && c.situation.length <= 1000);
    assert.ok(c.candidates.length >= 1 && c.candidates.length <= 10, c.id);
    c.candidates.forEach((x, i) => {
      assert.equal(x.deterministic_rank, i + 1);
      assert.match(x.section_id, /^[0-9a-f-]{36}$/);
      assert.ok([0, 1, 2, 3].includes(x.relevance));
      assert.equal(bySlug.get(x.doc_slug)?.class, x.content_class, `${c.id} ${x.doc_slug}`);
      assert.doesNotMatch(x.snippet, /<\/?b>|@[a-z0-9-]+\.[a-z]/i);
    });
    assert.equal(c.evaluable, Math.max(...c.candidates.map(x => x.relevance)) > 0);
  }
  assert.deepEqual(fixture.cases.filter(c => !c.evaluable).map(c => c.id), ["JRR-002", "JRR-012"]);
});

test("the deterministic baseline is measured, not assumed", async () => {
  const fixture = loadFixture();
  const report = await evaluateVariant({ fixture, variant: "deterministic" });
  assert.equal(report.summary.evaluable_cases, 11);
  close(report.summary.wrong_top1_rate, 4 / 11);
  assert.deepEqual(report.cases.filter(c => c.wrong_top1).map(c => c.id), ["JRR-003", "JRR-005", "JRR-010", "JRR-011"]);
  assert.ok(report.summary.ndcg_at_10 > 0 && report.summary.ndcg_at_10 < 1);
  assert.equal(report.summary.requests, 0);
  assert.equal(report.summary.input_tokens, 0);
  assert.equal(report.summary.cost_usd, 0);
  assert.equal(report.summary.p95_latency_ms, 0);
});

test("with an ideal judge the flat variants reach nDCG 1 and count reported tokens and cost", async () => {
  const fixture = loadFixture();
  let tick = 0;
  const now = () => (tick += 5);
  for (const variant of ["score", "noul"]) {
    const report = await evaluateVariant({ fixture, variant, askJev: labelOracle(fixture), now });
    close(report.summary.ndcg_at_10, 1);
    assert.equal(report.summary.wrong_top1_rate, 0);
    // Two single-candidate shortlists ask nothing.
    assert.equal(report.summary.requests, 11);
    assert.equal(report.summary.input_tokens, 11000);
    assert.equal(report.summary.output_tokens, 110);
    assert.equal(report.summary.tokens_source, "reported");
    close(report.summary.cost_usd, 11000 * PRICE / 1e6);
    assert.equal(report.summary.p95_latency_ms, 5);
    assert.equal(report.summary.fallbacks, 0);
    const abstention = report.cases.find(c => c.id === "JRR-002");
    assert.equal(abstention.evaluable, false);
    assert.equal(abstention.top_relevance, 0);
  }
  const beam = await evaluateVariant({ fixture, variant: "beam", askJev: labelOracle(fixture), now,
    taxonomy: DOCTRINE_TAXONOMY_SNAPSHOT });
  assert.equal(beam.summary.fallbacks, 0);
  assert.ok(beam.cases.every(c => c.requests <= 3));
  assert.ok(beam.cases.filter(c => c.evaluable).every(c => typeof c.ambiguity?.margin === "number" || c.requests === 0));
});

test("a failed Jev case is scored on the deterministic order it fell back to", async () => {
  const fixture = loadFixture();
  const report = await evaluateVariant({ fixture, variant: "noul", askJev: async () => { throw new Error("down"); } });
  const baseline = await evaluateVariant({ fixture, variant: "deterministic" });
  assert.equal(report.summary.fallbacks, 11);
  close(report.summary.ndcg_at_10, baseline.summary.ndcg_at_10);
  assert.equal(report.summary.input_tokens, 0);
});

test("the live bridge goes through ops/typesafe_client.ask with the duplicate cache off", async () => {
  const calls = [];
  const spawn = (cmd, args, options) => {
    calls.push({ cmd, args, options });
    return { status: 0, stdout: JSON.stringify({ ok: true, latency_ms: 812.5,
      result: { model: "jev-1.13", answers: { c00: { type: "noul", noul: 0.7 } }, usage: { input_tokens: 400, output_tokens: 3 } } }), stderr: "" };
  };
  const ask = pythonJevBridge({ repo: REPO, spawn });
  const out = await ask({ state: { situation: "x" }, questions: { c00: { type: "noul", instructions: "i" } }, model: "jev-latest" });
  assert.equal(out.model, "jev-1.13");
  assert.equal(out.latency_ms, 812.5);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].cmd, "python3");
  assert.equal(calls[0].args[0], "-c");
  assert.match(calls[0].args[1], /import typesafe_client/);
  assert.match(calls[0].args[1], /cache_ttl_seconds=0/);
  assert.match(calls[0].args[1], /caller="jev_rerank_eval"/);
  assert.deepEqual(JSON.parse(calls[0].options.input).questions, { c00: { type: "noul", instructions: "i" } });
  const failing = pythonJevBridge({ repo: REPO, spawn: () => ({ status: 0,
    stdout: JSON.stringify({ ok: false, error: "cannot read the TypeSafe credential" }), stderr: "" }) });
  await assert.rejects(failing({ state: {}, questions: {}, model: "m" }), /cannot read the TypeSafe credential/);
});

test("recorded live answers replay without a vendor call", async () => {
  const fixture = loadFixture();
  const recording = {};
  await evaluateVariant({ fixture, variant: "noul", askJev: recordingAsk(labelOracle(fixture), recording) });
  assert.equal(Object.keys(recording).length, 11);
  const replayed = await evaluateVariant({ fixture, variant: "noul", askJev: replayAsk(recording) });
  close(replayed.summary.ndcg_at_10, 1);
  const missing = await evaluateVariant({ fixture, variant: "score", askJev: replayAsk(recording) });
  assert.equal(missing.summary.fallbacks, 11, "a request that was never recorded is a fallback, never an invented answer");
});

test("main offline reports the measured baseline and only ESTIMATED Jev cost, never Jev quality", async () => {
  let printed = "";
  const spawn = () => assert.fail("offline must not spawn the live bridge");
  const report = await main([], { stdout: text => { printed += text; }, spawn, writeReport: () => {} });
  const parsed = JSON.parse(printed);
  assert.deepEqual(parsed, report);
  assert.equal(parsed.live, false);
  assert.equal(parsed.live_command, LIVE_COMMAND);
  assert.match(LIVE_COMMAND, /--live/);
  assert.ok(parsed.variants.deterministic.summary.ndcg_at_10 > 0);
  for (const variant of ["score", "noul", "beam"]) {
    assert.equal(parsed.variants[variant].measured, false);
    assert.equal(parsed.variants[variant].summary, null);
    assert.ok(parsed.variants[variant].estimate.input_tokens > 0);
    assert.equal(parsed.variants[variant].estimate.tokens_source, "estimated");
  }
  assert.equal(parsed.variants.beam.estimate.bound, "upper");
});

test("main --live runs every requested variant through the bridge and writes the report", async () => {
  const written = [];
  let asked = 0;
  let preflight = 0;
  const spawn = (cmd, args, options) => {
    if (args[1].includes("worker_ready")) { preflight += 1; return { status: 0, stdout: "", stderr: "" }; }
    asked += 1;
    const request = JSON.parse(options.input);
    const answers = Object.fromEntries(Object.entries(request.questions).map(([k, q]) =>
      [k, q.type === "noul" ? { type: "noul", noul: 0.5 } : { type: "score", score: 4.5, confidence: 0.5 }]));
    return { status: 0, stdout: JSON.stringify({ ok: true, latency_ms: 100,
      result: { model: "jev-x", answers, usage: { input_tokens: 10, output_tokens: 1 } } }), stderr: "" };
  };
  const report = await main(["--live", "--variants", "noul", "--out", "out/x.json"],
    { stdout: () => {}, spawn, writeReport: (path, body) => written.push([path, JSON.parse(body)]) });
  assert.equal(report.live, true);
  assert.equal(preflight, 1);
  assert.equal(asked, 11);
  assert.equal(report.variants.noul.measured, true);
  assert.equal(report.variants.noul.summary.p95_latency_ms, 100);
  assert.equal(report.variants.score, undefined);
  assert.equal(written[0][0], "out/x.json");
  assert.equal(Object.keys(written[0][1].recording).length, 11);
});

test("main --live refuses before any request when the Worker capability is unavailable", async () => {
  let asked = 0;
  const spawn = (cmd, args) => {
    if (args[1].includes("worker_ready"))
      return { status: 1, stdout: "", stderr: "Traceback\ntypesafe_client.TypeSafeError: cannot read the TypeSafe credential" };
    asked += 1;
    return { status: 0, stdout: "{}", stderr: "" };
  };
  await assert.rejects(main(["--live"], { stdout: () => {}, spawn, writeReport: () => assert.fail("no report") }),
    /needs authenticated Worker spend authority.*cannot read the TypeSafe credential/);
  assert.equal(asked, 0);
});

test("no new file is a script entrypoint, so the sealed source inventory does not move", () => {
  for (const path of ["evals/retrieval/jev-rerank-eval.mjs", "mcp-server/src/jev-rerank.js",
    "mcp-server/src/doctrine-taxonomy-snapshot.v1.js"])
    assert.equal(isScriptEntrypoint(path, false, readFileSync(resolve(REPO, path), "utf8")), false, path);
});


test('the Python bridge strips transport attribution and keeps replay keys stable', async () => {
  const captured = [];
  const result = { model: 'fake', answers: {}, usage: { input_tokens: 1, output_tokens: 0 } };
  const bridge = pythonJevBridge({ spawn: (cmd, args, opts) => {
    captured.push(JSON.parse(opts.input));
    return { status: 0, stdout: JSON.stringify({ ok: true, latency_ms: 0, result }) };
  } });
  const req = session => ({ state: { input: { situation: 'x' }, jev_attribution: {
    caller: 'worker.rerank', session_id: session, unattended: false } }, questions: {}, model: 'fake' });
  await bridge(req('first'));
  assert.deepEqual(captured[0].state, { situation: 'x' });
  const recording = {};
  await recordingAsk(async () => result, recording)(req('first'));
  assert.deepEqual(await replayAsk(recording)(req('second')), result);
});


test('the live eval caller passes Worker admission and is the bridge caller', async () => {
  const { jevCallSite, spendPolicy } = await import('../src/jev-spend-authority.js');
  const caller = 'jev_rerank_eval';
  assert.equal(jevCallSite({caller, session_id:'native-eval', unattended:false}).caller, caller);
  assert.ok(!spendPolicy.sites.some(s => s.caller === 'worker.rerank'));
  await pythonJevBridge({spawn: (cmd, args, options) => {
    assert.match(args[1], /caller="jev_rerank_eval"/);
    assert.doesNotMatch(args[1], /worker_ready/);
    return {status:0, stdout:JSON.stringify({ok:true,result:{answers:{}},latency_ms:1})};
  }})({state:'state', questions:{}});
});
