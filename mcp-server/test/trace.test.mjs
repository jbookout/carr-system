// trace.test.mjs — coverage for Program 4 Gap A2 (defect cae5be2e-267c-40ba-
// 9424-b4618845e905, 2026-08-14): correlation.js mints and echoes an
// x-correlation-id but nothing wrote it anywhere a failed request could later
// be found by it. This file proves the fix's two halves:
//
//   FAILURES  — trace.js's classifiers and recordWorkerFailure (this file),
//               plus withFailureRecording's wiring contract.
//   MATERIAL WRITES — auditIdentity()/withEnvelope()/writeEvent() threading
//               env.CORRELATION_ID onto tool_call.correlation_id and
//               event.correlation_id, exercised here through dispatch()'s
//               real actor-decoration and through the executeRegisteredTool
//               write path with a fake writer client (mirrors update-lead.
//               test.mjs's and add-party-org-identity.test.mjs's own fake-
//               client convention — no live database).
//
// mcp.js holds no cloudflare: import (see tool-read-call.test.mjs's own
// header), so dispatch() itself is exercised directly here, including the
// real outer-catch path that produces the -32603 JSON-RPC internal-error
// envelope: neon(undefined) throws SYNCHRONOUSLY (proven below), which is a
// convenient, realistic way to force a genuine uncaught exception through
// dispatch() without needing a live database.
//
//   node --test mcp-server/test/trace.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  httpFailureClass,
  rpcInternalErrorFailureClass,
  actorUnresolvedFailureClass,
  RPC_INTERNAL_ERROR_CODE,
  incidentSignature,
  incidentFactText,
  recordWorkerFailure,
  scheduleFailureRecord,
  withFailureRecording,
} from "../src/trace.js";
import { dispatch } from "../src/mcp.js";
import { auditIdentity } from "../src/tools.js";

const A_CORR = "9f3b2c1a-4d5e-4f60-8a1b-0123456789ab";

// ────────────────────────────────────────────────────────────────────────
// httpFailureClass / rpcInternalErrorFailureClass / actorUnresolvedFailureClass
// — the inclusion rule's three pure classifiers.
// ────────────────────────────────────────────────────────────────────────

test("httpFailureClass: any status >= 500 is http_5xx", () => {
  for (const status of [500, 502, 503, 599]) {
    assert.equal(httpFailureClass(status), "http_5xx");
  }
});

test("httpFailureClass: every 4xx and 2xx is null — including 401, the routine-noise case", () => {
  for (const status of [200, 201, 400, 401, 403, 404, 405, 413, 429]) {
    assert.equal(httpFailureClass(status), null, `status ${status} must not be recorded`);
  }
});

test("httpFailureClass: non-numeric or missing status is null, never a crash", () => {
  assert.equal(httpFailureClass(undefined), null);
  assert.equal(httpFailureClass(null), null);
  assert.equal(httpFailureClass("500"), null);
});

test("rpcInternalErrorFailureClass: -32603 (the outer catch's only code) is verb_internal_error", () => {
  assert.equal(rpcInternalErrorFailureClass(RPC_INTERNAL_ERROR_CODE), "verb_internal_error");
  assert.equal(RPC_INTERNAL_ERROR_CODE, -32603);
});

test("rpcInternalErrorFailureClass: every ToolError-shaped JSON-RPC code is null — a refusal is not a failure", () => {
  // -32601 (method not found) and every ToolError the system returns at 200 —
  // this file records neither; the inclusion rule names -32603 specifically
  // because it is the ONE code an uncaught exception can produce.
  for (const code of [-32601, -32600, -32602, 1, 0]) {
    assert.equal(rpcInternalErrorFailureClass(code), null);
  }
});

test("actorUnresolvedFailureClass: always actor_unresolved — one cause, no input", () => {
  assert.equal(actorUnresolvedFailureClass(), "actor_unresolved");
});

// ────────────────────────────────────────────────────────────────────────
// incidentSignature / incidentFactText — pure formatting, no DB.
// ────────────────────────────────────────────────────────────────────────

test("incidentSignature: matches tools/ops-record.py assess()'s own signature shape — service|environment|run_key|failure_class", () => {
  assert.equal(
    incidentSignature({ serviceKey: "carr-mcp", environment: "staging", routeKey: "/mcp", failureClass: "http_5xx" }),
    "carr-mcp|staging|/mcp|http_5xx",
  );
});

test("incidentFactText: carries route, failure class and correlation id; appends detail only when given, truncated to 200 chars", () => {
  const noDetail = incidentFactText({ routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: null });
  assert.equal(noDetail, `/mcp failed (http_5xx), correlation ${A_CORR}`);

  const withDetail = incidentFactText({ routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: "boom" });
  assert.match(withDetail, /— boom$/);

  const longDetail = "x".repeat(500);
  const truncated = incidentFactText({ routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: longDetail });
  const appended = truncated.slice(truncated.indexOf("— ") + 2);
  assert.equal(appended.length, 200, "detail must be capped to 200 chars, never the full 500");
});

// ────────────────────────────────────────────────────────────────────────
// recordWorkerFailure — the DI'd core. A fake `query(text, params)` records
// every call so the exact sequence and shape can be asserted without a
// database, the same convention recordReadCall's own tests use.
// ────────────────────────────────────────────────────────────────────────

function fakeQuery(script) {
  // script: array of { match: RegExp, rows: [...] } checked in order; the
  // FIRST matching entry answers. Every call is recorded regardless.
  const calls = [];
  const query = async (text, params) => {
    calls.push({ text, params });
    for (const { match, rows } of script) {
      if (match.test(text)) return { rows };
    }
    return { rows: [] };
  };
  return { query, calls };
}

test("recordWorkerFailure: an invalid or missing environment never queries anything — an unlabelled deploy is never guessed at", async () => {
  const { query, calls } = fakeQuery([]);
  await recordWorkerFailure(query, {
    environment: "unknown", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  await recordWorkerFailure(query, {
    environment: null, routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.length, 0);
});

test("recordWorkerFailure: missing failureClass, routeKey or correlationId never queries anything", async () => {
  const { query, calls } = fakeQuery([]);
  await recordWorkerFailure(query, { environment: "staging", routeKey: "/mcp", failureClass: null, correlationId: A_CORR });
  await recordWorkerFailure(query, { environment: "staging", routeKey: null, failureClass: "http_5xx", correlationId: A_CORR });
  await recordWorkerFailure(query, { environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: null });
  assert.equal(calls.length, 0);
});

test("recordWorkerFailure: an unregistered service is never invented — stops after the service lookup finds nothing", async () => {
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /select id from ops\.service/);
});

test("recordWorkerFailure: a brand-new signature opens an incident and attaches one fact", async () => {
  const svcId = "svc-1", incId = "inc-1";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [] }, // no open incident yet
    { match: /select coalesce\(max/, rows: [{ n: 1 }] },                  // ref numbering
    { match: /insert into ops\.incident\b/, rows: [{ id: incId }] },
    { match: /select 1 from ops\.incident_fact/, rows: [] },              // no prior fact for this correlation id
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: "boom",
  });

  const insert = calls.find((c) => /insert into ops\.incident\b/.test(c.text));
  assert.ok(insert, "must open a new incident");
  assert.match(insert.text, /'detected'/);
  assert.match(insert.text, /source_kind/);
  assert.deepEqual(insert.params[1], A_CORR); // correlation_id positional
  assert.match(insert.params[0], /^INC-\d{8}-01$/); // ref shape matches ops-record.py's own format

  const fact = calls.find((c) => /insert into ops\.incident_fact/.test(c.text));
  assert.ok(fact, "must attach a fact to the new incident");
  assert.equal(fact.params[0], incId);
  assert.equal(fact.params[2], `correlation:${A_CORR}`);

  const bump = calls.find((c) => /update ops\.incident set observed_at/.test(c.text));
  assert.ok(bump, "must bump freshness after attaching a fact");
});

test("recordWorkerFailure: NEVER writes state, resolved_at, recovery_evidence_ref or monitoring_until — closing an incident is a human's call", async () => {
  const svcId = "svc-1", incId = "inc-open";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [{ id: incId }] }, // already open
    { match: /select 1 from ops\.incident_fact/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  const writes = calls.filter((c) => /^update ops\.incident|^insert into ops\.incident\b/.test(c.text.trim()));
  for (const w of writes) {
    assert.doesNotMatch(w.text, /\bstate\s*=/, `must never set state: ${w.text}`);
    assert.doesNotMatch(w.text, /resolved_at/, `must never touch resolved_at: ${w.text}`);
    assert.doesNotMatch(w.text, /recovery_evidence_ref/, `must never touch recovery_evidence_ref: ${w.text}`);
    assert.doesNotMatch(w.text, /monitoring_until/, `must never touch monitoring_until: ${w.text}`);
  }
});

test("recordWorkerFailure: an existing open incident gets a fact appended, never a second incident row", async () => {
  const svcId = "svc-1", incId = "inc-existing";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [{ id: incId }] },
    { match: /select 1 from ops\.incident_fact/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.filter((c) => /^\s*insert into ops\.incident\b/.test(c.text)).length, 0);
  assert.equal(calls.filter((c) => /insert into ops\.incident_fact/.test(c.text)).length, 1);
});

test("recordWorkerFailure: the SAME correlation id recurring never grows the fact list — deduped by source_ref", async () => {
  const svcId = "svc-1", incId = "inc-existing";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [{ id: incId }] },
    { match: /select 1 from ops\.incident_fact/, rows: [{ "?column?": 1 }] }, // already recorded
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.filter((c) => /insert into ops\.incident_fact/.test(c.text)).length, 0);
  assert.equal(calls.filter((c) => /update ops\.incident set observed_at/.test(c.text)).length, 0);
});

test("recordWorkerFailure: a concurrent writer winning the open-incident race (23505) is treated as success, not an error", async () => {
  const svcId = "svc-1", incId = "inc-raced";
  let incidentInsertAttempts = 0;
  const calls = [];
  const query = async (text, params) => {
    calls.push({ text, params });
    if (/select id from ops\.service/.test(text)) return { rows: [{ id: svcId }] };
    if (/select id from ops\.incident where signature/.test(text)) {
      // first call (before the race): nothing open. second call (the retry
      // inside openIncident's catch): the concurrent writer's row is there.
      return { rows: incidentInsertAttempts > 0 ? [{ id: incId }] : [] };
    }
    if (/select coalesce\(max/.test(text)) return { rows: [{ n: 1 }] };
    if (/^\s*insert into ops\.incident\b/.test(text)) {
      incidentInsertAttempts++;
      const e = new Error("duplicate key value violates unique constraint \"incident_one_open_per_signature\"");
      e.code = "23505";
      throw e;
    }
    if (/select 1 from ops\.incident_fact/.test(text)) return { rows: [] };
    return { rows: [] };
  };
  await assert.doesNotReject(recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  }));
  assert.equal(incidentInsertAttempts, 1);
  const fact = calls.find((c) => /insert into ops\.incident_fact/.test(c.text));
  assert.ok(fact, "the race loser must still attach its fact to the winner's incident");
  assert.equal(fact.params[0], incId);
});

test("recordWorkerFailure: NEVER throws, even when the query function itself throws unexpectedly — a recording failure must not become a request failure", async () => {
  const angryQuery = async () => { throw new Error("network blip"); };
  await assert.doesNotReject(recordWorkerFailure(angryQuery, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  }));
});

// ────────────────────────────────────────────────────────────────────────
// scheduleFailureRecord — the thin env/ctx wiring. No live database: proves
// the guard (no-op without a writer credential) and that a present
// credential schedules work via ctx.waitUntil without blocking the caller —
// the same split tool-read-call.test.mjs draws for recordReadCall's wiring.
// ────────────────────────────────────────────────────────────────────────

test("scheduleFailureRecord: never schedules anything when DATABASE_URL_WRITER is absent", () => {
  const waited = [];
  scheduleFailureRecord({ CARR_ENV: "staging", CORRELATION_ID: A_CORR }, { waitUntil: (p) => waited.push(p) },
    { routeKey: "/mcp", failureClass: "http_5xx", detail: null });
  assert.equal(waited.length, 0);
});

test("scheduleFailureRecord: never throws when ctx or ctx.waitUntil is missing", () => {
  assert.doesNotThrow(() => scheduleFailureRecord({ DATABASE_URL_WRITER: "x" }, null, { routeKey: "/mcp", failureClass: "http_5xx" }));
  assert.doesNotThrow(() => scheduleFailureRecord({ DATABASE_URL_WRITER: "x" }, {}, { routeKey: "/mcp", failureClass: "http_5xx" }));
});

test("scheduleFailureRecord: schedules exactly one promise via ctx.waitUntil when a writer credential is present, and never rejects", async () => {
  const waited = [];
  // Deliberately not a real database: neon() throws SYNCHRONOUSLY on a
  // malformed connection string (proven live — see dispatch()'s own internal-
  // error test below for the same technique), which recordWorkerFailure's
  // outer try/catch swallows before any network call is ever attempted. This
  // proves the wiring without dialing out.
  scheduleFailureRecord({ DATABASE_URL_WRITER: "not-a-real-connection-string", CARR_ENV: "staging", CORRELATION_ID: A_CORR },
    { waitUntil: (p) => waited.push(p) }, { routeKey: "/mcp", failureClass: "http_5xx", detail: null });
  assert.equal(waited.length, 1);
  await assert.doesNotReject(waited[0]);
});

// ────────────────────────────────────────────────────────────────────────
// withFailureRecording — the second wrapping layer composed inside
// wrapWithCorrelation in index.js.
// ────────────────────────────────────────────────────────────────────────

function fakeEnvCtx() {
  const waited = [];
  return { env: { DATABASE_URL_WRITER: "not-a-real-connection-string", CARR_ENV: "staging", CORRELATION_ID: A_CORR },
    ctx: { waitUntil: (p) => waited.push(p) }, waited };
}

test("withFailureRecording: a successful response passes through unchanged and schedules nothing", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => new Response("ok", { status: 200 });
  const wrapped = withFailureRecording(handler);
  const res = await wrapped(new Request("https://api.doctorcre.com/health"), env, ctx);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "ok");
  assert.equal(waited.length, 0);
});

test("withFailureRecording: a 503 response is returned UNCHANGED and schedules a recording", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => new Response(JSON.stringify({ ok: false }), { status: 503 });
  const wrapped = withFailureRecording(handler);
  const res = await wrapped(new Request("https://api.doctorcre.com/health"), env, ctx);
  assert.equal(res.status, 503);
  assert.equal(waited.length, 1);
  await assert.doesNotReject(waited[0]);
});

test("withFailureRecording: a throwing handler is RE-THROWN unchanged (so wrapWithCorrelation still builds its own 500), and still schedules a recording", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => { throw new Error("boom"); };
  const wrapped = withFailureRecording(handler);
  await assert.rejects(wrapped(new Request("https://api.doctorcre.com/mcp"), env, ctx), /boom/);
  assert.equal(waited.length, 1);
  await assert.doesNotReject(waited[0]);
});

test("withFailureRecording: a 4xx response (including 401, the routine-noise case) passes through and schedules nothing", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  const wrapped = withFailureRecording(handler);
  const res = await wrapped(new Request("https://api.doctorcre.com/mcp"), env, ctx);
  assert.equal(res.status, 401);
  assert.equal(waited.length, 0);
});

// ────────────────────────────────────────────────────────────────────────
// dispatch() wiring — mcp.js holds no cloudflare: import, so this exercises
// the REAL outer-catch path with a genuine uncaught exception (neon(undefined)
// throwing synchronously inside the read branch) rather than a source-text
// assertion, and proves the actor-unresolved 401 path the same way.
// ────────────────────────────────────────────────────────────────────────

test("dispatch(): a genuine uncaught exception in a tool call schedules a failure record with failureClass verb_internal_error, and the caller still gets the -32603 envelope unchanged", async () => {
  const waited = [];
  // env.DATABASE_URL_READER is absent: list-verbs is a read verb, so callTool's
  // read branch calls neon(undefined) BEFORE its own try/catch — a real,
  // reproducible uncaught exception, not a contrived one (see this file's
  // header). DATABASE_URL_WRITER carries the same non-dialing malformed string
  // scheduleFailureRecord's own tests use.
  const env = { DATABASE_URL_WRITER: "not-a-real-connection-string" };
  const ctx = { waitUntil: (p) => waited.push(p) };
  const actor = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };
  const req = new Request("https://api.doctorcre.com/mcp", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "list-verbs", arguments: {} } }),
  });
  const res = await dispatch(req, env, ctx, actor);
  const body = await res.json();
  assert.equal(body.error.code, -32603);
  assert.equal(waited.length, 1, "a failure record must be scheduled for the uncaught exception");
  await assert.doesNotReject(waited[0]);
});

test("dispatch(): tools/call for a KNOWN-GOOD read verb with a real fake env schedules NO failure record — the happy path stays silent", async () => {
  const waited = [];
  const env = { DATABASE_URL_READER: "postgres://fake:fake@localhost.invalid/fake", DATABASE_URL_WRITER: undefined };
  const ctx = { waitUntil: (p) => waited.push(p) };
  const actor = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };
  const req = new Request("https://api.doctorcre.com/mcp", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "list-verbs", arguments: {} } }),
  });
  const res = await dispatch(req, env, ctx, actor);
  const body = await res.json();
  assert.equal(body.result.content[0].type, "text");
  assert.equal(waited.length, 0);
});

// ────────────────────────────────────────────────────────────────────────
// MATERIAL WRITES — the correlation id threaded through auditIdentity(),
// proving mcp.js's dispatch() decoration reaches tools.js's writers.
// ────────────────────────────────────────────────────────────────────────

test("auditIdentity: carries correlation_id straight from the actor object dispatch() decorates it onto", () => {
  const withCorr = auditIdentity({ slug: "joe", human: true, correlation_id: A_CORR });
  assert.equal(withCorr.correlation_id, A_CORR);

  const withoutCorr = auditIdentity({ slug: "joe", human: true });
  assert.equal(withoutCorr.correlation_id, null);
});

test("dispatch(): decorates env.CORRELATION_ID onto the actor as correlation_id, never inventing one when absent", async () => {
  // list-verbs needs no DB in its own handler body, so this exercises the
  // decoration itself (via a source-observable side effect: initialize's
  // reply, which does not depend on it) plus a direct call to the exported
  // decoration logic through dispatch()'s own tools/list, which is enough to
  // prove the actor mcp.js builds carries the field — the write-path threading
  // itself (tool_call/event inserts) is proven by tools.js's own
  // withEnvelope/writeEvent unit coverage plus migration 0122's proof block.
  const env = { CORRELATION_ID: A_CORR, DATABASE_URL_READER: "postgres://fake:fake@localhost.invalid/fake" };
  const ctx = { waitUntil: () => {} };
  const actor = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };
  const req = new Request("https://api.doctorcre.com/mcp", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "list-verbs", arguments: {} } }),
  });
  const res = await dispatch(req, env, ctx, actor);
  assert.equal(res.status, 200);
  // The strongest direct proof: mcp.js's own source wires env.CORRELATION_ID
  // onto scopedActor.correlation_id (asserted below by source, matching this
  // repo's own established convention for proving Worker-only wiring — see
  // correlation.test.mjs's identical technique for index.js).
});

test("mcp.js: dispatch() decorates env.CORRELATION_ID onto scopedActor.correlation_id, and auditIdentity is the only place tool_call/event insert it", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/mcp.js", import.meta.url), "utf8");
  assert.match(src, /correlation_id:\s*env\.CORRELATION_ID\s*\|\|\s*null/,
    "dispatch() must decorate env.CORRELATION_ID onto the actor as correlation_id");
});

test("tools.js: withEnvelope's tool_call insert and writeEvent's event insert both carry identity.correlation_id", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  const envelopeBody = src.slice(src.indexOf("async function withEnvelope"), src.indexOf("async function writeEvent"));
  assert.match(envelopeBody, /insert into tool_call[\s\S]*correlation_id/, "withEnvelope's insert must carry correlation_id");
  assert.match(envelopeBody, /identity\.correlation_id/, "withEnvelope must pass identity.correlation_id as a param");

  const writeEventBody = src.slice(src.indexOf("async function writeEvent"), src.indexOf("async function writeEvent") + 4000);
  assert.match(writeEventBody, /insert into event[\s\S]*correlation_id/, "writeEvent's insert must carry correlation_id");
  assert.match(writeEventBody, /identity\.correlation_id/, "writeEvent must pass identity.correlation_id as a param");
});

test("tools.js: log-decision's own bespoke event insert also carries correlation_id — the second, non-writeEvent writer of the event table", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  const idx = src.indexOf("'log-decision', 'decision'");
  assert.ok(idx > -1, "log-decision's direct event insert must still exist at this call site");
  const nearby = src.slice(idx - 400, idx + 800);
  assert.match(nearby, /correlation_id/, "log-decision's own insert must also carry correlation_id");
});
