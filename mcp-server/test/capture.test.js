import test from "node:test";
import assert from "node:assert/strict";
import { createCaptureHandler } from "../src/capture.js";
import { TOOLS, ToolError } from "../src/tools.js";

const ids = {
  device: "10000000-0000-0000-0000-000000000001",
  joe: "10000000-0000-0000-0000-000000000002",
  deal: "20000000-0000-0000-0000-000000000001",
  session: "30000000-0000-0000-0000-000000000001",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "dealroom-cookie", client_id: "dealroom-pwa" };

class CaptureFake {
  constructor() {
    this.actors = new Map([["mac-studio", ids.device], ["joe", ids.joe]]);
    this.sessions = [];
    this.candidates = [];
    this.activities = [];
    this.events = [];
    this.toolCalls = new Map();
    this.sequence = 1;
    this.failActivity = false;
    this.queryCount = 0;
  }

  uuid(prefix) {
    return `${prefix}0000000-0000-0000-0000-${String(this.sequence++).padStart(12, "0")}`;
  }

  async query(text, params = []) {
    this.queryCount += 1;
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.includes("capture:claim-nonce"))
      return { rows: this.sessions.filter(session => session.nonce === params[0]).map(session => ({ id: session.id })) };
    if (sql.includes("capture:device-actor"))
      return { rows: this.actors.has(params[0]) ? [{ id: this.actors.get(params[0]) }] : [] };
    if (sql.includes("capture:claim-insert")) {
      this.sessions.push({ id: ids.session, nonce: params[0], device_id: params[1], actor_id: params[2],
        mode: params[3], started_at: params[4], consent_announced_at: params[5], token_hash: params[6],
        state: "recording", state_at: params[4] });
      return { rows: [] };
    }
    if (sql.includes("capture:session-auth")) {
      const session = this.sessions.find(row => row.token_hash === params[0]);
      return { rows: session ? [{ id: session.id, state: session.state, state_at: session.state_at }] : [] };
    }
    if (sql.includes("capture:status-update")) {
      const session = this.sessions.find(row => row.id === params[0]);
      session.state = params[1];
      session.state_at = params[2];
      session.detail = params[3];
      return { rows: [{ state: session.state, state_at: session.state_at }] };
    }
    if (sql.includes("capture:candidates-insert")) {
      const items = JSON.parse(params[3]);
      items.forEach((item, item_index) => {
        if (this.candidates.some(row => row.session_id === params[0] && row.idempotency_key === params[1] && row.item_index === item_index)) return;
        this.candidates.push({ id: this.uuid("4"), session_id: params[0], idempotency_key: params[1],
          batch_hash: params[2], item_index, ...structuredClone(item), status: "pending", resulting_ref: null });
      });
      return { rows: [] };
    }
    if (sql.includes("capture:candidates-prior")) {
      const row = this.candidates.find(candidate => candidate.session_id === params[0] && candidate.idempotency_key === params[1]);
      return { rows: row ? [{ batch_hash: row.batch_hash }] : [] };
    }
    if (sql.includes("capture:candidates-result"))
      return { rows: this.candidates.filter(row => row.session_id === params[0] && row.idempotency_key === params[1])
        .sort((a, b) => a.item_index - b.item_index).map(row => ({ id: row.id, item_index: row.item_index })) };
    if (sql.includes("capture:session-read")) {
      const session = this.sessions.find(row => row.id === params[0]);
      const rows = this.candidates.filter(row => row.session_id === params[0]);
      const meeting = rows.find(row => row.kind === "meeting_record" && row.status === "confirmed");
      return { rows: [{ state: session.state,
        pending: rows.filter(row => row.status === "pending").length,
        confirmed: rows.filter(row => row.status === "confirmed").length,
        skipped: rows.filter(row => row.status === "skipped").length,
        meeting_record: meeting?.resulting_ref || null }] };
    }
    if (sql.includes("from v_capture_candidate_queue"))
      return { rows: this.candidates.filter(row => row.status === "pending")
        .sort((a, b) => b.confidence - a.confidence)
        .map(row => ({ id: row.id, session_id: row.session_id, kind: row.kind,
          payload: row.payload, evidence_quote: row.evidence_quote, confidence: row.confidence,
          deal_name: row.payload.deal || row.payload.ref || null,
          created_at: "2026-08-08 15:00:00+00" })) };
    if (sql.startsWith("select request_hash, response from tool_call")) {
      const prior = this.toolCalls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.includes("capture:resolve-read")) {
      const row = this.candidates.find(candidate => candidate.id === params[0]);
      return { rows: row ? [{ id: row.id, kind: row.kind, payload: row.payload,
        status: row.status, resulting_ref: row.resulting_ref }] : [] };
    }
    if (sql.includes("capture:resolve-skip")) {
      const row = this.candidates.find(candidate => candidate.id === params[0]);
      Object.assign(row, { status: "skipped", resolved_by: params[1], note: params[2] });
      return { rows: [] };
    }
    if (sql.includes("capture:resolve-confirm")) {
      const row = this.candidates.find(candidate => candidate.id === params[0]);
      Object.assign(row, { status: "confirmed", resolved_by: params[1], note: params[2], resulting_ref: params[3] });
      return { rows: [] };
    }
    if (sql.includes("from v_ref_index where subject_type='deal'"))
      return { rows: [{ subject_id: ids.deal, display_name: "Deal Alpha", status: "negotiation", client_ref: "C-1" }] };
    if (sql.startsWith("insert into activity")) {
      if (this.failActivity) throw new ToolError({ error: "inner_write_failed" });
      const id = this.uuid("5");
      this.activities.push({ id, actor_id: params[1], kind: params[2], summary: params[3], detail: params[4] });
      return { rows: [{ id, occurred_at: "2026-08-08 10:00:00+00" }] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ actor_id: params[1], verb: params[2], subject_type: params[3], subject_id: params[4] });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

function rig(db) {
  let randomCalls = 0;
  const handler = createCaptureHandler({
    randomStringFn: () => `opaque-session-${++randomCalls}`,
    withWriter: fn => fn(db),
  });
  return { handler, get randomCalls() { return randomCalls; } };
}

function claimRequest(body, token = "device-secret") {
  return new Request("https://worker.test/capture/claim", { method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify(body) });
}

const validClaim = { nonce: "nonce-1", device_id: "mac-studio", mode: "meeting",
  started_at: "2026-08-08T15:00:00.000Z", consent: { announced_at: "2026-08-08T14:59:58.000Z" } };

async function claimed() {
  const db = new CaptureFake();
  const r = rig(db);
  const response = await r.handler.fetch(claimRequest(validClaim), { CAPTURE_TOKENS: '{"mac-studio":"device-secret"}' });
  return { db, r, token: (await response.json()).session_token };
}

async function post(handler, path, body) {
  return handler.fetch(new Request(`https://worker.test${path}`, { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify(body) }), {});
}

test("claim authenticates first, requires consent, and never mints on nonce replay", async () => {
  const db = new CaptureFake();
  const r = rig(db);
  const environment = { CAPTURE_TOKENS: '{"mac-studio":"device-secret"}' };
  const unauthorized = await r.handler.fetch(claimRequest({ transcript: "must not parse" }, "wrong"), environment);
  assert.equal(unauthorized.status, 401);
  assert.equal(db.queryCount, 0);
  const missing = await r.handler.fetch(claimRequest({ ...validClaim, consent: { announced_at: null } }), environment);
  assert.equal(missing.status, 400);
  const forbidden = await r.handler.fetch(claimRequest({ ...validClaim, transcript: "must never land" }), environment);
  assert.equal(forbidden.status, 400);
  assert.equal(db.sessions.length, 0);
  const first = await r.handler.fetch(claimRequest(validClaim), environment);
  assert.equal(first.status, 200);
  assert.equal(r.randomCalls, 1);
  const replay = await r.handler.fetch(claimRequest(validClaim), environment);
  assert.equal(replay.status, 409);
  assert.equal(r.randomCalls, 1);
  assert.equal(db.sessions.length, 1);
});

test("status transitions move forward, reject every backward edge, and stop at terminals", async () => {
  for (const terminal of ["done", "failed"]) {
    const { db, r, token } = await claimed();
    const path = terminal === "done" ? ["transcribing", "distilling", "done"] : ["transcribing", "failed"];
    for (const [index, state] of path.entries()) {
      const response = await post(r.handler, "/capture/status", { session_token: token, state,
        at: `2026-08-08T15:0${index + 1}:00.000Z` });
      assert.equal(response.status, 200);
      assert.equal(typeof (await response.json()).at, "string");
      if (state !== "failed") {
        for (const backward of ["recording", "transcribing", "distilling"].filter(s =>
          ["recording", "transcribing", "distilling", "done"].indexOf(s) <
          ["recording", "transcribing", "distilling", "done"].indexOf(state))) {
          const denied = await post(r.handler, "/capture/status", { session_token: token, state: backward,
            at: "2026-08-08T16:00:00.000Z" });
          assert.equal(denied.status, 409, `${state} -> ${backward}`);
        }
      }
    }
    for (const state of ["recording", "transcribing", "distilling", "done", "failed"]) {
      const after = await post(r.handler, "/capture/status", { session_token: token, state,
        at: "2026-08-08T17:00:00.000Z" });
      assert.equal(after.status, 409, `${terminal} -> ${state}`);
    }
    assert.equal(db.sessions[0].state, terminal);
  }
});

test("candidate batches are atomic at validation, idempotent, and never auto-write", async () => {
  const { db, r, token } = await claimed();
  const tooLong = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen";
  let response = await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-bad",
    items: [{ kind: "activity", payload: { ref: "Deal Alpha", summary: "Call" }, evidence_quote: "fine", confidence: 0.5 },
      { kind: "activity", payload: { ref: "Deal Alpha", summary: "Call" }, evidence_quote: tooLong, confidence: 0.5 }] });
  assert.equal(response.status, 400);
  assert.equal(db.candidates.length, 0);
  response = await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-transcript",
    items: [{ kind: "activity", payload: { ref: "Deal Alpha", transcript: "must never land" },
      evidence_quote: "fine", confidence: 0.5 }] });
  assert.equal(response.status, 400);
  assert.equal(db.candidates.length, 0);

  const batch = { session_token: token, idempotency_key: "batch-1", items: [{ kind: "activity",
    payload: { ref: "Deal Alpha", summary: "Discussed lease", kind: "call" },
    evidence_quote: "Please call me next week", confidence: 0.99 }] };
  response = await post(r.handler, "/capture/candidates", batch);
  assert.equal(response.status, 200);
  await post(r.handler, "/capture/candidates", batch);
  assert.equal(db.candidates.length, 1);
  response = await post(r.handler, "/capture/candidates", { ...batch,
    items: [{ ...batch.items[0], evidence_quote: "different evidence" }] });
  assert.equal(response.status, 409);
  assert.equal(db.candidates.length, 1);
  assert.equal(db.candidates[0].status, "pending");
  assert.equal(db.activities.length, 0);
  assert.equal(db.events.length, 0);
  const queue = await TOOLS["capture-queue"].handler(db, joe, {});
  assert.equal(queue.candidates.length, 1);
  assert.equal(queue.candidates[0].confidence, 0.99);
  assert.equal(typeof queue.candidates[0].created_at, "string");
});

test("resolve accept executes once as confirmer; reject only skips; double resolve is inert", async () => {
  const { db, r, token } = await claimed();
  await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-resolve", items: [
    { kind: "activity", payload: { ref: "Deal Alpha", kind: "call", summary: "Called client" }, evidence_quote: "Call went well", confidence: 0.99 },
    { kind: "activity", payload: { ref: "Deal Alpha", kind: "call", summary: "Ignore" }, evidence_quote: "Not relevant", confidence: 0.2 },
  ] });
  const [accepted, rejected] = db.candidates;
  const result = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-1", candidate_id: accepted.id, accept: true });
  assert.equal(result.status, "confirmed");
  assert.equal(result.ref, db.activities[0].id);
  assert.equal(db.activities.length, 1);
  assert.equal(db.activities[0].actor_id, ids.joe);
  assert.equal(db.events[0].actor_id, ids.joe);
  assert.equal(accepted.resulting_ref, db.activities[0].id);

  const skipped = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-2", candidate_id: rejected.id, accept: false });
  assert.equal(skipped.status, "skipped");
  assert.equal(db.activities.length, 1);
  const again = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-3", candidate_id: accepted.id, accept: false });
  assert.equal(again.already, "confirmed");
  assert.equal(db.activities.length, 1);
});

test("inner failure remains pending and meeting record appears only after a real write", async () => {
  const { db, r, token } = await claimed();
  await post(r.handler, "/capture/candidates", { session_token: token, idempotency_key: "batch-meeting", items: [
    { kind: "meeting_record", payload: { ref: "Deal Alpha", summary: "Lease strategy meeting", detail: "Agreed to review options." },
      evidence_quote: "Review both lease options", confidence: 0.8 },
  ] });
  const candidate = db.candidates[0];
  const poll = () => r.handler.fetch(new Request("https://worker.test/capture/session",
    { headers: { authorization: `Bearer ${token}` } }), {});
  assert.equal((await (await poll()).json()).meeting_record, null);

  db.failActivity = true;
  await assert.rejects(TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-fail", candidate_id: candidate.id, accept: true }),
  error => error instanceof ToolError && error.payload.error === "inner_write_failed");
  assert.equal(candidate.status, "pending");
  assert.equal(candidate.resulting_ref, null);
  assert.equal((await (await poll()).json()).meeting_record, null);

  db.failActivity = false;
  const landed = await TOOLS["resolve-candidate"].handler(db, joe,
    { idempotency_key: "resolve-success", candidate_id: candidate.id, accept: true });
  assert.equal(db.activities[0].kind, "meeting");
  assert.equal(landed.ref, db.activities[0].id);
  assert.equal((await (await poll()).json()).meeting_record, db.activities[0].id);
});

test("capture SQL casts every temporal response and the contract has no transcript field", async () => {
  const source = await import("node:fs/promises").then(fs => fs.readFile(new URL("../src/capture.js", import.meta.url), "utf8"));
  assert.match(source, /to_jsonb\(state_at\)#>>'\{\}' as state_at/g);
  const dealroom = await import("node:fs/promises").then(fs => fs.readFile(new URL("../src/dealroom.js", import.meta.url), "utf8"));
  assert.match(dealroom, /to_jsonb\(started_at\)#>>'\{\}' as started_at/);
  assert.match(dealroom, /to_jsonb\(state_at\)#>>'\{\}' as state_at/);
  const tools = await import("node:fs/promises").then(fs => fs.readFile(new URL("../src/tools.js", import.meta.url), "utf8"));
  assert.match(tools, /to_jsonb\(created_at\)#>>'\{\}' as created_at/);
  const migration = await import("node:fs/promises").then(fs => fs.readFile(new URL("../../migrations/0081_capture_bridge.sql", import.meta.url), "utf8"));
  assert.doesNotMatch(migration, /^\s*transcript\w*\s+/im);
});
