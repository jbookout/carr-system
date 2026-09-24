import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { PROFILES, allowedIn, callTool } from "../src/mcp.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true, via: "test" };
const BOT = { ...JOE, human: false, slug: "codex" };
const ARGS = { idempotency_key: "10000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 1, answer_text: "Go with option B." };

async function rejected(fn) { try { await fn(); assert.fail("expected refusal"); } catch (e) { assert.ok(e instanceof ToolError); return e.payload; } }

class JoeAnswerFake {
  constructor() { this.calls = []; this.toolCalls = new Map(); this.version = 1; this.state = "needs_joe"; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) { const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] }; }
    if (sql.includes("answer_work_request_for_joe")) {
      if (params.includes(99)) return { rows: [] };
      this.state = "triaged"; this.version += 1;
      return { rows: [{ id: "20000000-0000-0000-0000-000000000001", ref: "WR-000001", state: this.state, version: this.version,
        answer_text: "Go with option B.", answered_by_actor_slug: "joe", answered_at: "2026-09-24T00:00:00Z" }] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null }); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("answer-work-request-for-joe is a closed human-only authority-only versioned write", () => {
  const tool = TOOLS["answer-work-request-for-joe"];
  assert.equal(tool.write, true); assert.equal(tool.humanOnly, true); assert.equal(tool.authorityOnly, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.required, ["idempotency_key", "human_ref", "base_version", "answer_text"]);
  for (const profile of ["capture", "hermes", "probe", "reviewer"]) assert.equal(allowedIn(profile, "answer-work-request-for-joe", tool), false, `${profile} must not receive the Joe answer`);
});

test("answer-work-request-for-joe performs only needs_joe-to-triaged and writes one audit event", async () => {
  const db = new JoeAnswerFake(); const out = await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  assert.deepEqual(out, { ok: true, human_ref: "WR-000001", state: "triaged", version: 2, answer_text: "Go with option B.",
    answered_by_actor_slug: "joe", answered_at: "2026-09-24T00:00:00Z" });
  assert.equal(db.calls.filter(x => x.sql.includes("answer_work_request_for_joe")).length, 1);
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(event.params[1], JOE.id, "audit actor is server-derived");
  assert.equal(JSON.parse(event.params[7]).answer_text, "Go with option B.");
  assert.equal(JSON.parse(event.params[6]).state, "needs_joe");
});

test("answer-work-request-for-joe refuses a machine actor, and still refuses extras, stale versions, and same-key mutation", async () => {
  const db = new JoeAnswerFake();
  const machine = await rejected(() => executeRegisteredTool(db, BOT, "answer-work-request-for-joe", structuredClone(ARGS)));
  assert.equal(machine.error, "human_only_verb_requires_verified_partner");
  assert.equal(machine.verb, "answer-work-request-for-joe");
  assert.equal(machine.actor_class, "unsponsored_agent");
  assert.equal(db.calls.length, 0, "an agent must not reach the database on a human-only verb");
  for (const extra of [{ state: "ready" }, { executor: "codex" }, { approval: "yes" }]) {
    const db = new JoeAnswerFake(); const out = await rejected(() => executeRegisteredTool(db, JOE, "answer-work-request-for-joe", { ...ARGS, ...extra }));
    assert.ok(["caller_authority_field_forbidden", "invalid_answer_work_request_for_joe_fields", "unregistered_operation_fields"].includes(out.error)); assert.equal(db.calls.length, 0);
  }
  const stale = await rejected(() => executeRegisteredTool(new JoeAnswerFake(), JOE, "answer-work-request-for-joe", { ...ARGS, base_version: 99 }));
  assert.equal(stale.error, "version_conflict");
});

test("answer-work-request-for-joe replay never makes a second transition", async () => {
  const db = new JoeAnswerFake(); await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  const out = await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  assert.equal(out.replayed, true); assert.equal(db.calls.filter(x => x.sql.includes("answer_work_request_for_joe")).length, 1);
});

test("answer-work-request-for-joe replay key is bound to the authenticated human", async () => {
  const db = new JoeAnswerFake(); await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  const dell = { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const out = await rejected(() => executeRegisteredTool(db, dell, "answer-work-request-for-joe", structuredClone(ARGS)));
  assert.equal(out.error, "key_reuse");
  assert.equal(db.calls.filter(x => x.sql.includes("answer_work_request_for_joe")).length, 1);
});

test("answer-work-request-for-joe has no routine-writer fallback when the authority connection is absent", async () => {
  const out = await rejected(() => callTool({}, JOE, "answer-work-request-for-joe", structuredClone(ARGS), "full"));
  assert.equal(out.error, "authority_connection_unavailable");
});

test("answer-work-request-for-joe refuses an empty or oversize answer", async () => {
  const db1 = new JoeAnswerFake();
  const blank = await rejected(() => executeRegisteredTool(db1, JOE, "answer-work-request-for-joe", { ...ARGS, answer_text: "   " }));
  assert.equal(blank.error, "invalid_answer_work_request_for_joe");
  const db2 = new JoeAnswerFake();
  const long = await rejected(() => executeRegisteredTool(db2, JOE, "answer-work-request-for-joe", { ...ARGS, answer_text: "x".repeat(501) }));
  assert.equal(long.error, "invalid_answer_work_request_for_joe");
});
