// Offline evaluation harness for the Jev reranking trial.
//
// It scores the CURRENT deterministic order and each Jev variant over the
// labeled shortlists in fixtures/jev-rerank-shortlists.2026-09-29.v1.json,
// which were captured from the live search-doctrine ranking and labeled by
// hand. Metrics: nDCG@10 (graded gains 2^rel - 1, ideal taken from the same
// shortlist), wrong-top-1 rate (the top row is graded below the best row the
// shortlist held), p95 per-case Jev latency, and token cost at the price in
// ops/config/jev-cost-guard.v1.json.
//
// THREE WAYS TO RUN, and only one of them measures Jev:
//   offline (default) — measures the deterministic baseline and prints an
//                       ESTIMATED token cost per variant (request characters
//                       / 3, the same pessimistic ratio ops/typesafe_client.py
//                       guards with). It reports no Jev quality number.
//   --live            — asks Jev through ops/typesafe_client.ask (receipted in
//                       out/jev-calls.jsonl, duplicate cache off so latency is
//                       real) and writes the full report, including every
//                       answer, to out/.
//   --replay <file>   — re-scores a previous live report's recorded answers
//                       without a vendor call. A request that was never
//                       recorded is a fallback, never an invented answer.
//
// THIS FILE IS A LIBRARY ON PURPOSE. It never reads the process argument
// vector and carries no run-as-main check, because either one makes a tracked
// .mjs a registered script entrypoint in the sealed source inventory (see
// isScriptEntrypoint in ops/scac-mutation-inventory.mjs). LIVE_COMMAND below
// is the one way to run it.

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { withTuningAccess } from "../tuning-access.mjs";

import {
  JEV_RERANK_MODEL, JEV_RERANK_SHORTLIST_MAX, buildFlatRerankRequest, jevRerank, rerankCandidateText,
} from "../../mcp-server/src/jev-rerank.js";
import { DOCTRINE_TAXONOMY_SNAPSHOT } from "../../mcp-server/src/doctrine-taxonomy-snapshot.v1.js";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
export const FIXTURE_PATH = resolve(REPO, "evals/retrieval/fixtures/jev-rerank-shortlists.2026-09-29.v1.json");
const COST_CONFIG = resolve(REPO, "ops/config/jev-cost-guard.v1.json");
export const VARIANTS = Object.freeze(["score", "noul", "beam"]);
export const LIVE_COMMAND =
  "node --input-type=module -e 'import(\"./evals/retrieval/jev-rerank-eval.mjs\").then(m => m.main([\"--live\"]))'";
// Carried from ops/typesafe_client.py: an estimate, deliberately pessimistic.
const CHARS_PER_TOKEN = 3;
const NOT_A_FALLBACK = new Set(["single_candidate", "flag_off", "empty_shortlist"]);

export function loadFixture(path = FIXTURE_PATH) {
  const fixture = JSON.parse(readFileSync(path, "utf8"));
  if (fixture.cases.some(c => ["final", "final_test"].includes(c.split)))
    throw new Error("final cases cannot enter rerank variant selection");
  return fixture;
}

function price() {
  return Number(JSON.parse(readFileSync(COST_CONFIG, "utf8")).price_usd_per_million_input_tokens);
}

export function ndcgAt(labels, k = 10, { allZero = 0 } = {}) {
  const gain = rel => 2 ** rel - 1;
  const dcg = list => list.slice(0, k).reduce((sum, rel, i) => sum + gain(rel) / Math.log2(i + 2), 0);
  const ideal = dcg([...labels].sort((a, b) => b - a));
  return ideal === 0 ? allZero : dcg(labels) / ideal;
}

export function wrongTop1(labels) {
  return labels.length > 0 && labels[0] < Math.max(...labels);
}

// Nearest-rank percentile.
export function percentile(values, p) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil((p / 100) * sorted.length) - 1)];
}

export function estimateTokens(request) {
  return Math.ceil(JSON.stringify(request).length / CHARS_PER_TOKEN);
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}

function requestKey(request) {
  return createHash("sha256").update(canonical({ state: request.state, questions: request.questions,
    model: request.model ?? null })).digest("hex");
}

export function recordingAsk(ask, recording) {
  return async request => {
    const result = await ask(request);
    recording[requestKey(request)] = result;
    return result;
  };
}

export function replayAsk(recording) {
  return async request => {
    const result = recording[requestKey(request)];
    if (!result) throw new Error("request was not recorded");
    return structuredClone(result);
  };
}

const PYTHON_BRIDGE = [
  "import json, sys, time",
  "sys.path.insert(0, 'ops')",
  "import typesafe_client",
  "req = json.load(sys.stdin)",
  "try:",
  "    started = time.monotonic()",
  "    result = typesafe_client.ask(req['state'], req['questions'], model=req.get('model') or typesafe_client.DEFAULT_MODEL, caller=\"jev_rerank_eval\", cache_ttl_seconds=0)",
  "    print(json.dumps({'ok': True, 'latency_ms': (time.monotonic() - started) * 1000, 'result': result}))",
  "except typesafe_client.TypeSafeError as err:",
  "    print(json.dumps({'ok': False, 'error': str(err)}))",
].join("\n");

// The live door: ops/typesafe_client.ask, the one way CARR calls Jev. It holds
// the credential, writes the receipt the spend guard counts, and refuses by
// name when ~/.config/carr/typesafe.env is absent. Latency is measured inside
// Python around ask(), so interpreter start-up is not billed to Jev.
export function pythonJevBridge({ repo = REPO, spawn = spawnSync, python = "python3" } = {}) {
  return async request => {
    const run = spawn(python, ["-c", PYTHON_BRIDGE], { cwd: repo, input: JSON.stringify(request),
      encoding: "utf8", maxBuffer: 16 * 1024 * 1024, timeout: 180000 });
    if (run.error) throw run.error;
    if (run.status !== 0) throw new Error(`jev bridge exited ${run.status}: ${String(run.stderr || "").slice(-400)}`);
    const out = JSON.parse(run.stdout);
    if (out.ok !== true) throw new Error(out.error || "jev bridge failed");
    return { ...out.result, latency_ms: out.latency_ms };
  };
}

// Refuse a live run up front when the credential is not readable, rather
// than letting every case fall back and print a report that looks measured.
export function assertLiveCredential({ repo = REPO, spawn = spawnSync, python = "python3" } = {}) {
  const run = spawn(python, ["-c",
    "import sys\nsys.path.insert(0, 'ops')\nimport typesafe_client\ntypesafe_client.read_api_key()"],
  { cwd: repo, encoding: "utf8", timeout: 30000 });
  if (run.error || run.status !== 0)
    throw new Error("a live run needs the TypeSafe credential at ~/.config/carr/typesafe.env " +
      `(TYPESAFE_API_KEY=...); ${String(run.error?.message || run.stderr || "").trim().split("\n").pop()}`);
}

function metered(askJev, now, meter) {
  return async request => {
    const started = now();
    meter.requests += 1;
    const result = await askJev(request);
    meter.latency_ms += typeof result?.latency_ms === "number" ? result.latency_ms : now() - started;
    const usage = result?.usage;
    if (usage && Number.isInteger(usage.input_tokens)) meter.input_tokens += usage.input_tokens;
    if (usage && Number.isInteger(usage.output_tokens)) meter.output_tokens += usage.output_tokens;
    return result;
  };
}

function topProbability(outcome) {
  const top = outcome?.scores?.[0];
  if (!top) return null;
  if (typeof top.relevance === "number") return top.relevance;
  return typeof top.log_score === "number" ? Number(Math.exp(top.log_score).toFixed(9)) : null;
}

export async function evaluateVariant({ fixture, variant, askJev, taxonomy = DOCTRINE_TAXONOMY_SNAPSHOT,
  now = () => performance.now(), model = JEV_RERANK_MODEL }) {
  const rows = [];
  for (const c of fixture.cases) {
    const meter = { requests: 0, latency_ms: 0, input_tokens: 0, output_tokens: 0 };
    let order = c.candidates;
    let outcome = null;
    if (variant !== "deterministic") {
      outcome = await jevRerank({ mode: variant, situation: c.situation, candidates: c.candidates,
        askJev: askJev ? metered(askJev, now, meter) : undefined, taxonomy, model });
      order = outcome.order;
    }
    const labels = order.map(x => x.relevance);
    rows.push({
      id: c.id, evaluable: c.evaluable,
      ndcg_at_10: c.evaluable ? ndcgAt(labels, 10) : null,
      wrong_top1: c.evaluable ? wrongTop1(labels) : null,
      top: `${order[0].doc_slug}#${order[0].section_key}`, top_relevance_label: labels[0],
      top_relevance: outcome ? topProbability(outcome) : null,
      judged: outcome ? outcome.judged : null, reason: outcome ? outcome.reason : null,
      model: outcome?.model ?? null, ambiguity: outcome?.ambiguity ?? null,
      requests: meter.requests, latency_ms: meter.latency_ms,
      input_tokens: meter.input_tokens, output_tokens: meter.output_tokens,
    });
  }
  const evaluable = rows.filter(r => r.evaluable);
  const inputTokens = rows.reduce((s, r) => s + r.input_tokens, 0);
  const mean = values => values.length ? values.reduce((s, v) => s + v, 0) / values.length : null;
  return {
    variant,
    summary: {
      cases: rows.length, evaluable_cases: evaluable.length,
      ndcg_at_10: mean(evaluable.map(r => r.ndcg_at_10)),
      wrong_top1_rate: mean(evaluable.map(r => (r.wrong_top1 ? 1 : 0))),
      p95_latency_ms: percentile(rows.map(r => r.latency_ms), 95),
      requests: rows.reduce((s, r) => s + r.requests, 0),
      input_tokens: inputTokens, output_tokens: rows.reduce((s, r) => s + r.output_tokens, 0),
      tokens_source: variant === "deterministic" ? "none" : "reported",
      cost_usd: inputTokens * price() / 1e6,
      fallbacks: rows.filter(r => r.judged === false && !NOT_A_FALLBACK.has(r.reason)).length,
      ambiguous: rows.filter(r => r.ambiguity?.ambiguous === true).length,
      models: [...new Set(rows.map(r => r.model).filter(Boolean))],
    },
    cases: rows,
  };
}

// Offline cost only. Flat variants are exact request sizes; the beam figure is
// an upper bound that asks every class, every document and every section.
export function estimateVariant({ fixture, variant, taxonomy = DOCTRINE_TAXONOMY_SNAPSHOT }) {
  let requests = 0;
  let inputTokens = 0;
  for (const c of fixture.cases) {
    const head = c.candidates.slice(0, JEV_RERANK_SHORTLIST_MAX);
    if (head.length < 2) continue;
    if (variant === "score" || variant === "noul") {
      requests += 1;
      inputTokens += estimateTokens(buildFlatRerankRequest(c.situation, head, variant));
      continue;
    }
    const docs = new Map(taxonomy.documents.map(d => [d.slug, d]));
    const classes = [...new Set(head.map(x => docs.get(x.doc_slug)?.class).filter(Boolean))];
    const slugs = [...new Set(head.map(x => x.doc_slug))];
    const level = texts => ({ state: { situation: c.situation },
      questions: Object.fromEntries(texts.map((t, i) => [`c${i}`, { type: "noul", instructions: t,
        criteria: { true: "x".repeat(90), false: "x".repeat(90) } }])) });
    for (const texts of [classes.map(k => `${k}: ${taxonomy.classes[k]}`),
      slugs.map(s => `${docs.get(s)?.title ?? s} (${s})`), head.map(rerankCandidateText)]) {
      if (texts.length < 2) continue;
      requests += 1;
      inputTokens += estimateTokens(level(texts)) + 60 * texts.length;
    }
  }
  return { requests, input_tokens: inputTokens, cost_usd: inputTokens * price() / 1e6,
    tokens_source: "estimated", bound: variant === "beam" ? "upper" : "exact_request_size" };
}

function parseArgs(args) {
  const opts = { live: false, replay: null, variants: [...VARIANTS], model: JEV_RERANK_MODEL, out: null,
    splitManifest: null, partition: "development" };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--live") opts.live = true;
    else if (arg === "--replay") opts.replay = args[++i];
    else if (arg === "--variants") opts.variants = String(args[++i]).split(",").filter(Boolean);
    else if (arg === "--model") opts.model = args[++i];
    else if (arg === "--out") opts.out = args[++i];
    else if (arg === "--split-manifest") opts.splitManifest = args[++i];
    else if (arg === "--partition") opts.partition = args[++i];
    else throw new Error(`unknown argument: ${arg}`);
  }
  for (const v of opts.variants) if (!VARIANTS.includes(v)) throw new Error(`unknown variant: ${v}`);
  if (opts.live && opts.replay) throw new Error("--live and --replay are exclusive");
  if (!["train", "development"].includes(opts.partition)) throw new Error("final partition forbidden during variant selection");
  return opts;
}

export async function main(args = [], {
  stdout = text => { globalThis.process?.stdout?.write(text); },
  spawn = spawnSync,
  writeReport = (path, body) => { mkdirSync(dirname(resolve(REPO, path)), { recursive: true });
    writeFileSync(resolve(REPO, path), body); },
  fixturePath = FIXTURE_PATH,
  tuningFixture = null,
} = {}) {
  const opts = parseArgs(args);
  if (opts.splitManifest && !tuningFixture) {
    const manifestPath = resolve(REPO, opts.splitManifest);
    const run = spawn("python3", [resolve(REPO, "evals/rule-delivery/freeze_split.py"), "load", manifestPath, opts.partition],
      { cwd: REPO, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
    if (run.status !== 0) throw new Error(`invalid split: ${run.stderr}`);
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
    // Only the existing fixed TypeSafe bridge may spawn while tuning. It reads
    // the client/key, not case files, and receives development state on stdin.
    const credentialCode = "import sys\nsys.path.insert(0, 'ops')\nimport typesafe_client\ntypesafe_client.read_api_key()";
    let pendingReport;
    const { result, audit } = await withTuningAccess([resolve(dirname(manifestPath), manifest.partitions.final.path),
      ...manifest.blocked_sources], () => main(args, { stdout: () => {}, spawn,
      writeReport: (path, body) => { pendingReport = { path, body }; }, fixturePath,
      tuningFixture: { suite_id: manifest.source, doctrine_generation: null,
        cases: JSON.parse(run.stdout), split_manifest_digest: manifest.digest } }),
    [["python3", ["-c", PYTHON_BRIDGE]], ["python3", ["-c", credentialCode]]], manifestPath);
    result.tuning_access = audit;
    if (pendingReport) writeReport(pendingReport.path,
      `${JSON.stringify({ ...JSON.parse(pendingReport.body), tuning_access: audit }, null, 1)}\n`);
    stdout(`${JSON.stringify(result, null, 1)}\n`);
    return result;
  }
  const fixture = tuningFixture || loadFixture(fixturePath);
  const measured = opts.live || opts.replay;
  const recording = {};
  let askJev = null;
  if (opts.live) {
    assertLiveCredential({ spawn });
    askJev = recordingAsk(pythonJevBridge({ spawn }), recording);
  }
  if (opts.replay) askJev = replayAsk(JSON.parse(readFileSync(resolve(REPO, opts.replay), "utf8")).recording || {});

  const report = {
    schema: "carr-jev-rerank-eval-v1",
    split_provenance: { evaluation_use: "development_only", final_score_eligible: false,
      manifest_digest: fixture.split_manifest_digest ?? null,
      dataset_digest: `sha256:${createHash("sha256").update(canonical(fixture.cases)).digest("hex")}` },
    suite_id: fixture.suite_id,
    doctrine_generation: fixture.doctrine_generation,
    taxonomy_snapshot_id: DOCTRINE_TAXONOMY_SNAPSHOT.snapshot_id,
    live: opts.live, replay: opts.replay, requested_model: opts.model,
    price_usd_per_million_input_tokens: price(),
    live_command: LIVE_COMMAND,
    generated_at: new Date().toISOString(),
    variants: { deterministic: { measured: true, ...(await evaluateVariant({ fixture, variant: "deterministic" })) } },
  };
  for (const variant of opts.variants) {
    report.variants[variant] = measured
      ? { measured: true, ...(await evaluateVariant({ fixture, variant, askJev, model: opts.model })) }
      : { measured: false, summary: null, estimate: estimateVariant({ fixture, variant }) };
  }
  if (opts.live) {
    const path = opts.out || `out/jev-rerank-eval.${report.generated_at.replace(/[:.]/g, "-")}.json`;
    writeReport(path, `${JSON.stringify({ ...report, recording }, null, 1)}\n`);
    report.written_to = path;
  }
  stdout(`${JSON.stringify(report, null, 1)}\n`);
  return report;
}
