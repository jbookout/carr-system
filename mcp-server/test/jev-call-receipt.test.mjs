import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool, TOOLS } from "../src/tools.js";
import { canonicalJson, canonicalSha256, jevAskBinding, sha256Hex } from "../src/jev-call-receipt.js";

const AGENT = { id: "10000000-0000-0000-0000-000000000031", slug: "joe", human: true, via: "test" };
const KEY = "ts_test_key_do_not_leak_4f1c9e";

async function rejected(fn) {
  try { await fn(); assert.fail("expected refusal"); }
  catch (e) { assert.ok(e instanceof ToolError, `expected ToolError, got ${e}`); return e.payload; }
}

function jsonResponse(status, body, headers = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    status, ok: status >= 200 && status < 300,
    headers: { get: name => headers[name.toLowerCase()] ?? null },
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

function fakeFetch(responses) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    if (typeof next === "function") return next(url, init);
    return next;
  };
  impl.calls = calls;
  return impl;
}

const ANSWERS = { q1: { noul: 0.82 }, q2: { choice: "b", probabilities: { a: 0.1, b: 0.9 } } };
const QUESTIONS = {
  q1: { type: "noul", instructions: "Is the plan sound?" },
  q2: { type: "choice", instructions: "Which option?", choices: ["a", "b"] },
};

// Mirrors ops.record_jev_call_receipt / ops.read_jev_call_receipts
// (migrations/0587): append-only, server-stamped recorded_at, prompt_sha256
// iff build_advisory, answers projected only for build_advisory.
class JevReceiptFake {
  constructor({ jevAsk } = {}) {
    this.calls = []; this.toolCalls = new Map(); this.rows = []; this.clock = 0;
    if (jevAsk !== undefined) this.jevAsk = jevAsk;
  }
  now() { this.clock += 1; return new Date(Date.UTC(2026, 8, 24, 12, 0, this.clock)).toISOString(); }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [{}] };
    if (sql.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]);
      return { rows: row ? [row] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.includes("ops.record_jev_call_receipt")) {
      const [sessionId, purpose, questionIds, facets, modelRequested, modelAnswered, stateSha,
        questionsSha, answersSha, promptSha, answers, usage, actorSlug, key] = params;
      if ((purpose === "build_advisory") !== (promptSha !== null))
        throw new Error("jev_call_receipt_prompt_sha256_iff_build_advisory");
      for (const digest of [stateSha, questionsSha, answersSha, ...(promptSha ? [promptSha] : [])])
        assert.match(digest, /^[0-9a-f]{64}$/);
      if (this.rows.some(row => row.idempotency_key === key)) throw new Error("duplicate key");
      const row = {
        receipt_id: `40000000-0000-0000-0000-${String(this.rows.length + 1).padStart(12, "0")}`,
        recorded_at: this.now(), session_id: sessionId, purpose, question_ids: questionIds, facets,
        model_requested: modelRequested, model_answered: modelAnswered, state_sha256: stateSha,
        questions_sha256: questionsSha, answers_sha256: answersSha, prompt_sha256: promptSha,
        answers: JSON.parse(answers), usage: usage ? JSON.parse(usage) : null,
        actor_slug: actorSlug, idempotency_key: key,
      };
      this.rows.push(row);
      return { rows: [{ receipt_id: row.receipt_id, recorded_at: row.recorded_at }] };
    }
    if (sql.startsWith("select ops.read_jev_call_receipts")) {
      const [session, since, limit] = params;
      const matching = this.rows.filter(row => row.session_id === session &&
        (since === null || row.recorded_at >= new Date(since).toISOString()));
      const recent = matching.slice(-limit);
      return { rows: [{ result: {
        server_now: "2026-09-24T13:00:00+00:00",
        receipts: recent.map(row => ({
          receipt_id: row.receipt_id, recorded_at: row.recorded_at, purpose: row.purpose,
          question_ids: row.question_ids, facets: row.facets, model: row.model_answered,
          state_sha256: row.state_sha256, prompt_sha256: row.prompt_sha256,
          answers: row.purpose === "build_advisory" ? row.answers : null,
        })),
      } }] };
    }
    throw new Error(`JevReceiptFake: unhandled query: ${sql}`);
  }
}

function fakeJevAsk(result = { model: "jev-1.14.0", answers: ANSWERS, usage: { input_tokens: 10 } }) {
  const calls = [];
  const fn = async request => { calls.push(request); return structuredClone(result); };
  fn.calls = calls;
  return fn;
}

let keySeq = 0;
function askArgs(overrides = {}) {
  keySeq += 1;
  return {
    idempotency_key: `9a000000-0000-4000-8000-${String(keySeq).padStart(12, "0")}`,
    session_id: "session-abc",
    purpose: "call",
    state: { plan: "ship it" },
    questions: QUESTIONS,
    ...overrides,
  };
}

// ── canonical JSON / digests ────────────────────────────────────────────────

test("canonical JSON digest equals Python's json.dumps(sort_keys, compact, ensure_ascii=False) sha256", async () => {
  // Python-computed:
  //   v = {"b": [1, True, None, "é ☃ 日本 \n\t\"q\" \\ \u0001 <U+2028>"],
  //        "a": {"z": "𝄞", "y": 0, "é": "x", "ｚ": 1, "𝄞": 2}, "A": -12}
  //   hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",",":"),
  //                  ensure_ascii=False).encode("utf-8")).hexdigest()
  // The keys "ｚ" (U+FF5A) and "𝄞" (U+1D11E) sort differently by UTF-16 code
  // unit than by code point; Python sorts by code point.
  const vector = { b: [1, true, null, "é ☃ 日本 \n\t\"q\" \\ \u0001 \u2028"],
    a: { z: "𝄞", y: 0, "é": "x", "ｚ": 1, "𝄞": 2 }, A: -12 };
  assert.equal(canonicalJson(vector),
    "{\"A\":-12,\"a\":{\"y\":0,\"z\":\"𝄞\",\"é\":\"x\",\"ｚ\":1,\"𝄞\":2},\"b\":[1,true,null,\"é ☃ 日本 \\n\\t\\\"q\\\" \\\\ \\u0001 \u2028\"]}");
  assert.equal(await canonicalSha256(vector),
    "92cd2cc4813ec6a8c9c0d164a594d670d6638a55d5716f0934572c9e49cdc2f1");
  // prompt_sha256 is the digest of the JSON-encoded string:
  //   hashlib.sha256(json.dumps(p, ensure_ascii=False).encode()).hexdigest()
  assert.equal(await canonicalSha256("Build me a parking-ratio check — ünïcödé ok?"),
    "269e71b019a5b3a2a8bde1ecdc74ca04c8b316d6f8b6d258b7225c13c24f1368");
  assert.equal(await sha256Hex(""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
});

// ── ask-jev happy paths ─────────────────────────────────────────────────────

test("ask-jev purpose call: asks Jev through the Worker binding, records the receipt, returns answers", async () => {
  const jevAsk = fakeJevAsk();
  const client = new JevReceiptFake({ jevAsk });
  const args = askArgs({ facets: ["diagnosis"] });
  const result = await executeRegisteredTool(client, AGENT, "ask-jev", args);
  assert.equal(jevAsk.calls.length, 1);
  assert.deepEqual(jevAsk.calls[0], { state: { plan: "ship it" }, model: "jev-latest", questions: QUESTIONS });
  assert.equal(result.ok, true);
  assert.equal(result.purpose, "call");
  assert.equal(result.session_id, "session-abc");
  assert.equal(result.model, "jev-1.14.0");
  assert.deepEqual(result.answers, ANSWERS);
  assert.deepEqual(result.usage, { input_tokens: 10 });
  assert.equal(result.prompt_sha256, null);
  assert.equal(result.state_sha256, await canonicalSha256({ plan: "ship it" }));
  assert.equal(result.receipt_id, client.rows[0].receipt_id);
  assert.equal(result.recorded_at, client.rows[0].recorded_at);
  const row = client.rows[0];
  assert.deepEqual(row.question_ids, ["q1", "q2"]);
  assert.deepEqual(row.facets, ["diagnosis"]);
  assert.equal(row.model_requested, "jev-latest");
  assert.equal(row.model_answered, "jev-1.14.0");
  assert.equal(row.questions_sha256, await canonicalSha256(QUESTIONS));
  assert.equal(row.answers_sha256, await canonicalSha256(ANSWERS));
  assert.equal(row.actor_slug, "joe");
  assert.equal(row.idempotency_key, args.idempotency_key);
  // The receipt is written before the envelope ledger row, inside the same
  // transaction the write path opened.
  const recordAt = client.calls.findIndex(call => call.sql.includes("ops.record_jev_call_receipt"));
  const ledgerAt = client.calls.findIndex(call => call.sql.startsWith("insert into tool_call"));
  assert.ok(recordAt >= 0 && ledgerAt > recordAt);
  // Same key replays the stored response without a second Jev call or row.
  const replay = await executeRegisteredTool(client, AGENT, "ask-jev", args);
  assert.equal(replay.replayed, true);
  assert.equal(replay.receipt_id, result.receipt_id);
  assert.equal(jevAsk.calls.length, 1);
  assert.equal(client.rows.length, 1);
});

test("ask-jev purpose build_advisory records prompt_sha256 of state.partner_request", async () => {
  const jevAsk = fakeJevAsk();
  const client = new JevReceiptFake({ jevAsk });
  const state = { partner_request: "Build me a parking-ratio check — ünïcödé ok?", repo: "carr" };
  const result = await executeRegisteredTool(client, AGENT, "ask-jev",
    askArgs({ purpose: "build_advisory", state, model: "jev-1.14.0" }));
  assert.equal(result.purpose, "build_advisory");
  assert.equal(result.prompt_sha256, "269e71b019a5b3a2a8bde1ecdc74ca04c8b316d6f8b6d258b7225c13c24f1368");
  assert.equal(client.rows[0].prompt_sha256, result.prompt_sha256);
  assert.equal(client.rows[0].model_requested, "jev-1.14.0");
  assert.equal(jevAsk.calls[0].model, "jev-1.14.0");
});

test("ask-jev accepts a string state and digests it as a JSON string", async () => {
  const client = new JevReceiptFake({ jevAsk: fakeJevAsk() });
  const result = await executeRegisteredTool(client, AGENT, "ask-jev", askArgs({ state: "plain text state" }));
  assert.equal(result.state_sha256, await sha256Hex(JSON.stringify("plain text state")));
});

// ── refusals ────────────────────────────────────────────────────────────────

test("ask-jev refuses jev_proxy_unconfigured when the Worker binding is absent (no key, or break-glass)", async () => {
  for (const client of [new JevReceiptFake(), new JevReceiptFake({ jevAsk: null })]) {
    const payload = await rejected(() => executeRegisteredTool(client, AGENT, "ask-jev", askArgs()));
    assert.equal(payload.error, "jev_proxy_unconfigured");
    assert.match(payload.hint, /TYPESAFE_API_KEY/);
    assert.equal(client.rows.length, 0);
  }
  assert.equal(jevAskBinding({}), null);
  assert.equal(jevAskBinding({ TYPESAFE_API_KEY: "" }), null);
  assert.equal(jevAskBinding({ TYPESAFE_API_KEY: "   " }), null);
  assert.equal(jevAskBinding(undefined), null);
  assert.equal(TOOLS["ask-jev"].jevProxy, true);
});

test("ask-jev refuses invalid inputs before calling Jev", async () => {
  const cases = [
    // An empty required string is refused as missing by the shared chokepoint.
    [{ session_id: "" }, "missing_required"],
    [{ session_id: "x".repeat(201) }, "jev_session_id_invalid"],
    [{ state: ["not", "object"] }, "jev_state_invalid"],
    [{ state: 7 }, "jev_state_invalid"],
    [{ state: { blob: "x".repeat(96001) } }, "jev_state_too_large"],
    [{ questions: {} }, "jev_questions_invalid"],
    [{ questions: Object.fromEntries(Array.from({ length: 65 }, (_, i) =>
      [`q${i}`, { type: "noul", instructions: "x" }])) }, "jev_questions_invalid"],
    [{ questions: { q1: { type: "essay", instructions: "x" } } }, "jev_questions_invalid"],
    [{ questions: { q1: { type: "noul", instructions: "  " } } }, "jev_questions_invalid"],
    [{ questions: { q1: "not an object" } }, "jev_questions_invalid"],
    [{ purpose: "build_advisory", state: "a string" }, "jev_build_advisory_state_invalid"],
    [{ purpose: "build_advisory", state: { partner_request: 5 } }, "jev_build_advisory_state_invalid"],
    [{ purpose: "build_advisory", state: { other: "x" } }, "jev_build_advisory_state_invalid"],
    [{ purpose: "chat" }, "value_not_in_declared_vocabulary"],
    [{ facets: ["vibes"] }, "value_not_in_declared_vocabulary"],
    [{ model: "" }, "jev_model_invalid"],
    [{ extra: 1 }, "unregistered_operation_fields"],
  ];
  for (const [overrides, error] of cases) {
    const jevAsk = fakeJevAsk();
    const client = new JevReceiptFake({ jevAsk });
    const payload = await rejected(() => executeRegisteredTool(client, AGENT, "ask-jev", askArgs(overrides)));
    assert.equal(payload.error, error, JSON.stringify(overrides).slice(0, 80));
    assert.equal(jevAsk.calls.length, 0);
    assert.equal(client.rows.length, 0);
  }
  // The 96000 limit is on the canonical JSON, inclusive.
  const atLimit = "x".repeat(96000 - JSON.stringify("").length);
  const client = new JevReceiptFake({ jevAsk: fakeJevAsk() });
  await executeRegisteredTool(client, AGENT, "ask-jev", askArgs({ state: atLimit }));
  assert.equal(client.rows.length, 1);
});

// ── the Worker binding: upstream behaviour ──────────────────────────────────

test("jevAskBinding posts {state, model, questions} with bearer auth and a user agent", async () => {
  const fetchImpl = fakeFetch([jsonResponse(200, { model: "jev-1.14.0", answers: ANSWERS, usage: { n: 1 } })]);
  const ask = jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep: async () => {} });
  const out = await ask({ state: { a: 1 }, model: "jev-latest", questions: QUESTIONS });
  assert.deepEqual(out, { model: "jev-1.14.0", answers: ANSWERS, usage: { n: 1 } });
  assert.equal(fetchImpl.calls.length, 1);
  const { url, init } = fetchImpl.calls[0];
  assert.equal(url, "https://api.typesafe.ai/v1/systemone");
  assert.equal(init.method, "POST");
  assert.equal(init.headers.authorization, `Bearer ${KEY}`);
  assert.equal(init.headers["content-type"], "application/json");
  assert.match(init.headers["user-agent"], /\S/);
  assert.deepEqual(JSON.parse(init.body), { state: { a: 1 }, model: "jev-latest", questions: QUESTIONS });
  assert.ok(init.signal);
});

test("jevAskBinding retries 429 at most twice, honouring retry-after capped at 5s", async () => {
  const sleeps = [];
  const sleep = async ms => { sleeps.push(ms); };
  const ok = jsonResponse(200, { model: "m", answers: {} });
  let fetchImpl = fakeFetch([jsonResponse(429, "slow", { "retry-after": "2" }),
    jsonResponse(429, "slow", { "retry-after": "60" }), ok]);
  let out = await jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep })({ state: "s", model: "m", questions: QUESTIONS });
  assert.equal(out.model, "m");
  assert.equal(out.usage, null);
  assert.equal(fetchImpl.calls.length, 3);
  assert.deepEqual(sleeps, [2000, 5000]);

  sleeps.length = 0;
  fetchImpl = fakeFetch([jsonResponse(429, "a"), jsonResponse(429, "b"), jsonResponse(429, "c"), ok]);
  const payload = await rejected(() => jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep })(
    { state: "s", model: "m", questions: QUESTIONS }));
  assert.equal(payload.error, "jev_upstream_failed");
  assert.equal(payload.status, 429);
  assert.equal(fetchImpl.calls.length, 3);
  assert.equal(sleeps.length, 2);
});

test("upstream failures refuse jev_upstream_failed and never carry the key", async () => {
  const echo = `bad request; you sent Authorization: Bearer ${KEY} ` + "y".repeat(1000);
  const scenarios = [
    [jsonResponse(500, echo), 500],
    [jsonResponse(401, `invalid key ${KEY}`), 401],
    [jsonResponse(200, "not json"), 200],
    [jsonResponse(200, { model: "m" }), 200],
    [jsonResponse(200, { answers: [], model: "m" }), 200],
    [jsonResponse(200, { answers: {}, model: 3 }), 200],
    [Object.assign(new Error(`connect failed ${KEY}`), { name: "TypeError" }), null],
  ];
  for (const [response, status] of scenarios) {
    const fetchImpl = fakeFetch([response]);
    let thrown;
    try {
      await jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep: async () => {} })(
        { state: "s", model: "m", questions: QUESTIONS });
    } catch (e) { thrown = e; }
    assert.ok(thrown instanceof ToolError);
    assert.equal(thrown.payload.error, "jev_upstream_failed");
    assert.equal(thrown.payload.status, status);
    const serialized = JSON.stringify({ payload: thrown.payload, message: thrown.message, stack: thrown.stack });
    assert.equal(serialized.includes(KEY), false, "the key leaked into the error");
    assert.ok((thrown.payload.body ?? "").length <= 300);
  }
});

test("a hung upstream times out as jev_upstream_failed", async () => {
  const fetchImpl = async (_url, init) => new Promise((_, reject) => {
    init.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
  });
  const payload = await rejected(() => jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { timeoutMs: 20 })(
    { state: "s", model: "m", questions: QUESTIONS }));
  assert.equal(payload.error, "jev_upstream_failed");
  assert.equal(payload.reason, "timeout");
});

test("ask-jev surfaces an upstream failure without writing a receipt", async () => {
  const fetchImpl = fakeFetch([jsonResponse(503, `down ${KEY}`)]);
  const client = new JevReceiptFake({ jevAsk: jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep: async () => {} }) });
  let thrown;
  try { await executeRegisteredTool(client, AGENT, "ask-jev", askArgs()); } catch (e) { thrown = e; }
  assert.ok(thrown instanceof ToolError);
  assert.equal(thrown.payload.error, "jev_upstream_failed");
  assert.equal(thrown.payload.status, 503);
  assert.equal(JSON.stringify(thrown.payload).includes(KEY), false);
  assert.equal(client.rows.length, 0);
  assert.equal(client.toolCalls.size, 0);
});

// ── read-jev-call-receipts ──────────────────────────────────────────────────

test("read-jev-call-receipts returns the session's receipts, answers only for build_advisory", async () => {
  const client = new JevReceiptFake({ jevAsk: fakeJevAsk() });
  await executeRegisteredTool(client, AGENT, "ask-jev", askArgs());
  await executeRegisteredTool(client, AGENT, "ask-jev", askArgs({ purpose: "build_advisory",
    state: { partner_request: "build it" }, facets: ["architecture_or_design"] }));
  await executeRegisteredTool(client, AGENT, "ask-jev", askArgs({ session_id: "other-session" }));
  const out = await executeRegisteredTool(client, AGENT, "read-jev-call-receipts", { session_id: "session-abc" });
  assert.equal(out.ok, true);
  assert.equal(out.session_id, "session-abc");
  assert.equal(typeof out.server_now, "string");
  assert.equal(out.receipts.length, 2);
  const [call, advisory] = out.receipts;
  assert.deepEqual(Object.keys(call).sort(), ["answers", "facets", "model", "prompt_sha256", "purpose",
    "question_ids", "receipt_id", "recorded_at", "state_sha256"]);
  assert.equal(call.purpose, "call");
  assert.equal(call.answers, null);
  assert.equal(call.prompt_sha256, null);
  assert.equal(advisory.purpose, "build_advisory");
  assert.deepEqual(advisory.answers, ANSWERS);
  assert.deepEqual(advisory.facets, ["architecture_or_design"]);
  assert.match(advisory.prompt_sha256, /^[0-9a-f]{64}$/);
  assert.ok(call.recorded_at < advisory.recorded_at);
  const readCall = client.calls.find(entry => entry.sql.startsWith("select ops.read_jev_call_receipts"));
  assert.deepEqual(readCall.params, ["session-abc", null, 200]);
  const limited = await executeRegisteredTool(client, AGENT, "read-jev-call-receipts",
    { session_id: "session-abc", limit: 1, since: "2026-09-24T00:00:00Z" });
  assert.equal(limited.receipts.length, 1);
  assert.equal(limited.receipts[0].purpose, "build_advisory");
  assert.equal(TOOLS["read-jev-call-receipts"].write, false);
});

test("read-jev-call-receipts refuses invalid inputs", async () => {
  const client = new JevReceiptFake();
  for (const [args, error] of [
    [{ session_id: "" }, "missing_required"],
    [{ session_id: "x".repeat(201) }, "jev_session_id_invalid"],
    [{ session_id: "s", since: "yesterday" }, "jev_since_invalid"],
    [{ session_id: "s", limit: 0 }, "jev_limit_invalid"],
    [{ session_id: "s", limit: 501 }, "jev_limit_invalid"],
    [{ session_id: "s", limit: 2.5 }, "jev_limit_invalid"],
  ]) {
    const payload = await rejected(() => executeRegisteredTool(client, AGENT, "read-jev-call-receipts", args));
    assert.equal(payload.error, error);
  }
  assert.equal(client.calls.length, 0);
});
