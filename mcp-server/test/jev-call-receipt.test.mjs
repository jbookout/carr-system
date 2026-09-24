import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool, TOOLS } from "../src/tools.js";
import { callTool } from "../src/mcp.js";
import { canonicalJson, canonicalSha256, jevAskBinding, prefetchJevAnswer, sha256Hex }
  from "../src/jev-call-receipt.js";

const AGENT = { id: "10000000-0000-0000-0000-000000000031", slug: "joe", human: true, via: "test" };
const OTHER = { id: "10000000-0000-0000-0000-000000000032", slug: "dell", human: true, via: "test" };
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
const ACTORS = new Map([[AGENT.slug, AGENT.id], [OTHER.slug, OTHER.id]]);

// Mirrors migrations/0587: ops.record_jev_call_receipt (append-only,
// server-stamped recorded_at, server-derived actor checked against the actor
// table, prompt_sha256 iff build_advisory), ops.read_jev_call_receipts
// (credits only rows with a matching ask-jev tool_call row by the same actor,
// oldest `limit` from since with a truncated flag, answers only for
// build_advisory) and ops.jev_call_receipt_integrity.
class JevReceiptFake {
  constructor({ prefetched } = {}) {
    this.calls = []; this.toolCalls = new Map(); this.rows = []; this.clock = 0;
    this.triggers = { jev_call_receipt_append_only: "O", jev_call_receipt_no_truncate: "O" };
    if (prefetched !== undefined) this.jevPrefetched = prefetched;
  }
  now() { this.clock += 1; return new Date(Date.UTC(2026, 8, 24, 12, 0, this.clock)).toISOString(); }
  credited(row) {
    const call = this.toolCalls.get(row.idempotency_key);
    return !!call && call.verb === "ask-jev" && call.actor_id === row.actor_id &&
      call.response?.receipt_id === row.receipt_id;
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [{}] };
    if (sql.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]);
      return { rows: row ? [row] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { verb: params[1], actor_id: params[2], request_hash: params[3],
        response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.includes("ops.record_jev_call_receipt")) {
      const [sessionId, purpose, questionIds, facets, modelRequested, modelAnswered, stateSha,
        questionsSha, answersSha, promptSha, answers, usage, actorId, actorSlug, key] = params;
      if (ACTORS.get(actorSlug) !== actorId) throw new Error("jev_call_receipt_actor_unresolved");
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
        actor_id: actorId, actor_slug: actorSlug, idempotency_key: key,
      };
      this.rows.push(row);
      return { rows: [{ receipt_id: row.receipt_id, recorded_at: row.recorded_at }] };
    }
    if (sql.startsWith("select ops.read_jev_call_receipts")) {
      const [session, since, limit, actorSlug] = params;
      const actorId = ACTORS.get(actorSlug);
      if (!actorId) throw new Error("jev_call_receipt_actor_unresolved");
      const from = since === null ? "2026-09-23T12:00:00.000Z" : new Date(since).toISOString();
      const matching = this.rows.filter(row => row.session_id === session && row.actor_id === actorId &&
        row.recorded_at >= from && this.credited(row));
      return { rows: [{ result: {
        server_now: "2026-09-24T13:00:00+00:00",
        since: from,
        truncated: matching.length > limit,
        receipts: matching.slice(0, limit).map(row => ({
          receipt_id: row.receipt_id, recorded_at: row.recorded_at, purpose: row.purpose,
          question_ids: row.question_ids, facets: row.facets, model: row.model_answered,
          state_sha256: row.state_sha256, prompt_sha256: row.prompt_sha256,
          answers: row.purpose === "build_advisory" ? row.answers : null,
        })),
      } }] };
    }
    if (sql.startsWith("select ops.jev_call_receipt_integrity")) {
      const orphans = this.rows.filter(row => !this.credited(row));
      const triggers = Object.entries(this.triggers).map(([name, tgenabled]) =>
        ({ name, tgenabled, enabled: ["O", "A"].includes(tgenabled) }));
      return { rows: [{ result: {
        receipts_total: this.rows.length,
        receipts_without_tool_call: { count: orphans.length,
          receipt_ids: orphans.slice(-20).reverse().map(row => row.receipt_id) },
        trigger_enabled: triggers.every(t => t.enabled),
        triggers,
        checked_at: "2026-09-24T13:00:00+00:00",
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

// What mcp.js's write path does: ask Jev (validated) before the transaction,
// then run the verb with the prefetched answer on the client.
async function askVia(client, actor, args, ask) {
  if (ask) client.jevPrefetched = await prefetchJevAnswer(args, ask);
  return executeRegisteredTool(client, actor, "ask-jev", args);
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
  //   v = {"b": [1, True, None, "é ☃ 日本 \n\t\"q\" \\ <U+0001> <U+2028>"],
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

test("ask-jev purpose call: records the prefetched answer with the server-derived actor", async () => {
  const jevAsk = fakeJevAsk();
  const client = new JevReceiptFake();
  const args = askArgs({ facets: ["diagnosis"] });
  const result = await askVia(client, AGENT, args, jevAsk);
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
  assert.equal(row.actor_id, AGENT.id);
  assert.equal(row.actor_slug, "joe");
  assert.equal(row.idempotency_key, args.idempotency_key);
  // The receipt precedes the envelope ledger row, which names the receipt.
  const recordAt = client.calls.findIndex(call => call.sql.includes("ops.record_jev_call_receipt"));
  const ledgerAt = client.calls.findIndex(call => call.sql.startsWith("insert into tool_call"));
  assert.ok(recordAt >= 0 && ledgerAt > recordAt);
  assert.equal(client.toolCalls.get(args.idempotency_key).response.receipt_id, row.receipt_id);
  // Same key: the replay's fresh answer is discarded and the stored response
  // returned, with no second row.
  const replay = await askVia(client, AGENT, args, fakeJevAsk({ model: "other", answers: { q1: { noul: 0 } } }));
  assert.equal(replay.replayed, true);
  assert.equal(replay.receipt_id, result.receipt_id);
  assert.deepEqual(replay.answers, ANSWERS);
  assert.equal(client.rows.length, 1);
});

test("ask-jev purpose build_advisory records prompt_sha256 of state.partner_request", async () => {
  const jevAsk = fakeJevAsk();
  const client = new JevReceiptFake();
  const state = { partner_request: "Build me a parking-ratio check — ünïcödé ok?", repo: "carr" };
  const result = await askVia(client, AGENT, askArgs({ purpose: "build_advisory", state, model: "jev-1.14.0" }), jevAsk);
  assert.equal(result.purpose, "build_advisory");
  assert.equal(result.prompt_sha256, "269e71b019a5b3a2a8bde1ecdc74ca04c8b316d6f8b6d258b7225c13c24f1368");
  assert.equal(client.rows[0].prompt_sha256, result.prompt_sha256);
  assert.equal(client.rows[0].model_requested, "jev-1.14.0");
  assert.equal(jevAsk.calls[0].model, "jev-1.14.0");
});

test("ask-jev accepts a string state and digests it as a JSON string", async () => {
  const client = new JevReceiptFake();
  const result = await askVia(client, AGENT, askArgs({ state: "plain text state" }), fakeJevAsk());
  assert.equal(result.state_sha256, await sha256Hex(JSON.stringify("plain text state")));
});

// ── the vendor call happens before the writer transaction ──────────────────

test("callTool asks Jev before it connects the writer pool, and never for an invalid request", async () => {
  const realFetch = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async url => {
    seen.push(String(url));
    return jsonResponse(200, { model: "jev-1.14.0", answers: ANSWERS });
  };
  try {
    // No DATABASE_URL_WRITER: the pool cannot connect, so reaching the vendor
    // at all proves the call came first; the connection failure follows.
    let failure;
    try { await callTool({ TYPESAFE_API_KEY: KEY }, { ...AGENT }, "ask-jev", askArgs(), "full"); }
    catch (e) { failure = e; }
    assert.ok(failure, "expected the writer connection to fail after the vendor call");
    assert.deepEqual(seen, ["https://api.typesafe.ai/v1/systemone"]);
    seen.length = 0;
    const payload = await rejected(() =>
      callTool({ TYPESAFE_API_KEY: KEY }, { ...AGENT }, "ask-jev", askArgs({ questions: {} }), "full"));
    assert.equal(payload.error, "jev_questions_invalid");
    assert.deepEqual(seen, []);
  } finally {
    globalThis.fetch = realFetch;
  }
});

// ── refusals ────────────────────────────────────────────────────────────────

test("ask-jev refuses jev_proxy_unconfigured when nothing was prefetched (no key, or break-glass)", async () => {
  const client = new JevReceiptFake();
  const payload = await rejected(() => executeRegisteredTool(client, AGENT, "ask-jev", askArgs()));
  assert.equal(payload.error, "jev_proxy_unconfigured");
  assert.match(payload.hint, /TYPESAFE_API_KEY/);
  assert.equal(client.rows.length, 0);
  assert.equal(jevAskBinding({}), null);
  assert.equal(jevAskBinding({ TYPESAFE_API_KEY: "" }), null);
  assert.equal(jevAskBinding({ TYPESAFE_API_KEY: "   " }), null);
  assert.equal(jevAskBinding(undefined), null);
  assert.equal(TOOLS["ask-jev"].jevProxy, true);
});

test("ask-jev refuses invalid inputs, and prefetch never asks Jev for them", async () => {
  const cases = [
    // An empty required string is refused as missing by the shared chokepoint.
    [{ session_id: "" }, "missing_required", "jev_session_id_invalid"],
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
    [{ purpose: "chat" }, "value_not_in_declared_vocabulary", "jev_purpose_invalid"],
    [{ facets: ["vibes"] }, "value_not_in_declared_vocabulary", "jev_facets_invalid"],
    [{ model: "" }, "jev_model_invalid"],
    [{ extra: 1 }, "unregistered_operation_fields", null],
  ];
  for (const [overrides, error, prefetchError = error] of cases) {
    const jevAsk = fakeJevAsk();
    if (prefetchError) {
      const early = await rejected(() => prefetchJevAnswer(askArgs(overrides), jevAsk));
      assert.equal(early.error, prefetchError, JSON.stringify(overrides).slice(0, 80));
    }
    assert.equal(jevAsk.calls.length, 0);
    const client = new JevReceiptFake({ prefetched: { ok: true, result: { model: "m", answers: {}, usage: null } } });
    const payload = await rejected(() => executeRegisteredTool(client, AGENT, "ask-jev", askArgs(overrides)));
    assert.equal(payload.error, error, JSON.stringify(overrides).slice(0, 80));
    assert.equal(client.rows.length, 0);
  }
  // The 96000 limit is on the canonical JSON, inclusive.
  const atLimit = "x".repeat(96000 - JSON.stringify("").length);
  const client = new JevReceiptFake();
  await askVia(client, AGENT, askArgs({ state: atLimit }), fakeJevAsk());
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

test("jevAskBinding retries 429 once inside a 10s total budget", async () => {
  let clock = 0;
  const now = () => clock;
  const sleeps = [];
  const sleep = async ms => { sleeps.push(ms); clock += ms; };
  const ok = () => jsonResponse(200, { model: "m", answers: {} });
  // One retry honouring retry-after (capped at 5s).
  let fetchImpl = fakeFetch([jsonResponse(429, "slow", { "retry-after": "60" }), ok()]);
  let out = await jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep, now })(
    { state: "s", model: "m", questions: QUESTIONS });
  assert.equal(out.model, "m");
  assert.equal(out.usage, null);
  assert.equal(fetchImpl.calls.length, 2);
  assert.deepEqual(sleeps, [5000]);

  // Never a second retry.
  clock = 0; sleeps.length = 0;
  fetchImpl = fakeFetch([jsonResponse(429, "a"), jsonResponse(429, "b"), ok()]);
  let payload = await rejected(() => jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep, now })(
    { state: "s", model: "m", questions: QUESTIONS }));
  assert.equal(payload.error, "jev_upstream_failed");
  assert.equal(payload.status, 429);
  assert.equal(fetchImpl.calls.length, 2);
  assert.equal(sleeps.length, 1);

  // No retry when the wait would leave under a second of the budget.
  clock = 0; sleeps.length = 0;
  fetchImpl = fakeFetch([async () => { clock += 6000; return jsonResponse(429, "late", { "retry-after": "4" }); }, ok()]);
  payload = await rejected(() => jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep, now })(
    { state: "s", model: "m", questions: QUESTIONS }));
  assert.equal(payload.status, 429);
  assert.equal(fetchImpl.calls.length, 1);
  assert.deepEqual(sleeps, []);
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

test("a hung upstream times out as jev_upstream_failed within the budget", async () => {
  const fetchImpl = async (_url, init) => new Promise((_, reject) => {
    init.signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
  });
  const payload = await rejected(() => jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { budgetMs: 20 })(
    { state: "s", model: "m", questions: QUESTIONS }));
  assert.equal(payload.error, "jev_upstream_failed");
  assert.equal(payload.reason, "timeout");
});

test("ask-jev surfaces a prefetched upstream failure without writing a receipt", async () => {
  const fetchImpl = fakeFetch([jsonResponse(503, `down ${KEY}`)]);
  const client = new JevReceiptFake();
  let thrown;
  try {
    await askVia(client, AGENT, askArgs(), jevAskBinding({ TYPESAFE_API_KEY: KEY }, fetchImpl, { sleep: async () => {} }));
  } catch (e) { thrown = e; }
  assert.ok(thrown instanceof ToolError);
  assert.equal(thrown.payload.error, "jev_upstream_failed");
  assert.equal(thrown.payload.status, 503);
  assert.equal(JSON.stringify(thrown.payload).includes(KEY), false);
  assert.equal(client.rows.length, 0);
  assert.equal(client.toolCalls.size, 0);
});

// ── read-jev-call-receipts ──────────────────────────────────────────────────

test("read-jev-call-receipts credits only the caller's receipts that have their tool_call row", async () => {
  const client = new JevReceiptFake();
  await askVia(client, AGENT, askArgs(), fakeJevAsk());
  await askVia(client, AGENT, askArgs({ purpose: "build_advisory",
    state: { partner_request: "build it" }, facets: ["architecture_or_design"] }), fakeJevAsk());
  await askVia(client, AGENT, askArgs({ session_id: "other-session" }), fakeJevAsk());
  await askVia(client, OTHER, askArgs(), fakeJevAsk());
  // A receipt forged straight into the table: no tool_call partner.
  client.rows.push({ ...client.rows[0], receipt_id: "40000000-0000-0000-0000-00000000ffff",
    idempotency_key: "forged-key", recorded_at: client.now() });
  const out = await executeRegisteredTool(client, AGENT, "read-jev-call-receipts", { session_id: "session-abc" });
  assert.equal(out.ok, true);
  assert.equal(out.session_id, "session-abc");
  assert.equal(typeof out.server_now, "string");
  assert.equal(typeof out.since, "string");
  assert.equal(out.truncated, false);
  assert.equal(out.receipts.length, 2);
  assert.equal(out.receipts.some(r => r.receipt_id.endsWith("ffff")), false);
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
  assert.deepEqual(readCall.params, ["session-abc", null, 200, "joe"]);
  // The OLDEST `limit` from since, with truncated=true.
  const limited = await executeRegisteredTool(client, AGENT, "read-jev-call-receipts",
    { session_id: "session-abc", limit: 1, since: "2026-09-24T00:00:00Z" });
  assert.equal(limited.receipts.length, 1);
  assert.equal(limited.receipts[0].purpose, "call");
  assert.equal(limited.truncated, true);
  // Another actor sees only its own.
  const theirs = await executeRegisteredTool(client, OTHER, "read-jev-call-receipts", { session_id: "session-abc" });
  assert.equal(theirs.receipts.length, 1);
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

// ── read-jev-call-receipt-integrity ─────────────────────────────────────────

test("read-jev-call-receipt-integrity flags uncredited receipts and a disabled trigger", async () => {
  const client = new JevReceiptFake();
  await askVia(client, AGENT, askArgs(), fakeJevAsk());
  let audit = await executeRegisteredTool(client, AGENT, "read-jev-call-receipt-integrity", {});
  assert.equal(audit.ok, true);
  assert.equal(audit.receipts_total, 1);
  assert.deepEqual(audit.receipts_without_tool_call, { count: 0, receipt_ids: [] });
  assert.equal(audit.trigger_enabled, true);
  assert.equal(typeof audit.checked_at, "string");
  client.rows.push({ ...client.rows[0], receipt_id: "forged", idempotency_key: "forged-key" });
  client.triggers.jev_call_receipt_append_only = "D";
  audit = await executeRegisteredTool(client, AGENT, "read-jev-call-receipt-integrity", {});
  assert.deepEqual(audit.receipts_without_tool_call, { count: 1, receipt_ids: ["forged"] });
  assert.equal(audit.trigger_enabled, false);
  assert.equal(TOOLS["read-jev-call-receipt-integrity"].write, false);
  const payload = await rejected(() =>
    executeRegisteredTool(client, AGENT, "read-jev-call-receipt-integrity", { extra: 1 }));
  assert.equal(payload.error, "unregistered_operation_fields");
});
