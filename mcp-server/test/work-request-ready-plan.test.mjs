import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { allowedIn, callTool } from "../src/mcp.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true, via: "test" };
const BOT = { ...JOE, human: false, slug: "codex" };
const PROPOSE = { idempotency_key: "10000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 2,
  scope_summary: "Bounded scope", runbook_ref: "doctrine:runbook#safe-plan", dependency_refs: [],
  recovery_ref: "safe:recovery:rollback", observability_ref: "safe:observe:logs", caps: { max_steps: 2, max_duration_minutes: 30 } };
const ACCEPT = { idempotency_key: "20000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 2,
  plan_hash: `sha256:${"a".repeat(64)}` };

async function refused(fn) { try { await fn(); assert.fail("expected refusal"); } catch (error) { assert.ok(error instanceof ToolError); return error.payload; } }

class PlanFake {
  constructor() { this.calls = []; this.toolCalls = new Map(); }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response from tool_call")) { const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] }; }
    if (sql.includes("propose_sourced_work_request_plan")) return { rows: [{ plan_id: "30000000-0000-0000-0000-000000000001", plan_ref: "PLAN-000001", plan_hash: ACCEPT.plan_hash,
      work_request_id: "40000000-0000-0000-0000-000000000001", ref: "WR-000001", state: "triaged", version: 2,
      scope_summary: PROPOSE.scope_summary, runbook_ref: PROPOSE.runbook_ref,
      runbook_revision_id: "50000000-0000-0000-0000-000000000001", runbook_content_hash: `sha256:${"b".repeat(64)}` }] };
    if (sql.includes("accept_sourced_work_request_plan")) return { rows: [{ work_request_id: "40000000-0000-0000-0000-000000000001", ref: "WR-000001", state: "ready", version: 3,
      plan_id: "30000000-0000-0000-0000-000000000001", plan_ref: "PLAN-000001", plan_hash: ACCEPT.plan_hash,
      accepted_by_actor_slug: "joe", accepted_at: "2026-08-16T00:00:00Z", shape_disposition: "not_required", shape_fixed_surface_ref: `sourced-plan:PLAN-000001#${ACCEPT.plan_hash}` }] };
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) }); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("ready-plan schemas are closed and acceptance is human authority only", () => {
  assert.equal(TOOLS["propose-ready-plan"].inputSchema.additionalProperties, false);
  assert.equal(TOOLS["accept-ready-plan"].inputSchema.additionalProperties, false);
  assert.equal(TOOLS["accept-ready-plan"].humanOnly, true);
  assert.equal(TOOLS["accept-ready-plan"].authorityOnly, true);
  for (const profile of ["capture", "hermes", "probe", "reviewer"]) assert.equal(allowedIn(profile, "propose-ready-plan", TOOLS["propose-ready-plan"]), false);
});

test("proposal returns explicit plan readback and audits the Work Request entity", async () => {
  const db = new PlanFake(); const out = await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  assert.deepEqual(out, { ok: true, human_ref: "WR-000001", state: "triaged", version: 2, plan_ref: "PLAN-000001", plan_hash: ACCEPT.plan_hash,
    scope_summary: PROPOSE.scope_summary, runbook_ref: PROPOSE.runbook_ref, runbook_revision_id: "50000000-0000-0000-0000-000000000001", runbook_content_hash: `sha256:${"b".repeat(64)}` });
  const event = db.calls.find(call => call.sql.startsWith("insert into event"));
  assert.equal(event.params[4], "40000000-0000-0000-0000-000000000001");
  assert.equal(JSON.parse(event.params[7]).plan_ref, "PLAN-000001");
});

test("acceptance returns exact durable readback and audits Work Request state", async () => {
  const db = new PlanFake(); const out = await executeRegisteredTool(db, JOE, "accept-ready-plan", structuredClone(ACCEPT));
  assert.equal(out.state, "ready"); assert.equal(out.plan_ref, "PLAN-000001"); assert.equal(out.accepted_by_actor_slug, "joe");
  const event = db.calls.find(call => call.sql.startsWith("insert into event"));
  assert.equal(event.params[4], "40000000-0000-0000-0000-000000000001");
  assert.equal(JSON.parse(event.params[7]).state, "ready");
});

test("actor-bound replay does not repeat proposal SQL or event and changed payload refuses", async () => {
  const db = new PlanFake(); await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  const replay = await executeRegisteredTool(db, JOE, "propose-ready-plan", structuredClone(PROPOSE));
  assert.equal(replay.replayed, true); assert.equal(db.calls.filter(c => c.sql.includes("propose_sourced_work_request_plan")).length, 1);
  assert.equal(db.calls.filter(c => c.sql.startsWith("insert into event")).length, 1);
  const changed = await refused(() => executeRegisteredTool(db, JOE, "propose-ready-plan", { ...PROPOSE, scope_summary: "changed" }));
  assert.equal(changed.error, "key_reuse");
  const dell = { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const foreign = await refused(() => executeRegisteredTool(db, dell, "propose-ready-plan", structuredClone(PROPOSE)));
  assert.equal(foreign.error, "key_reuse");
});

test("acceptance replay is actor-bound and never repeats acceptance SQL or event", async () => {
  const db = new PlanFake();
  const first = await executeRegisteredTool(db, JOE, "accept-ready-plan", structuredClone(ACCEPT));
  assert.equal(first.shape_fixed_surface_ref, `sourced-plan:PLAN-000001#${ACCEPT.plan_hash}`);
  const replay = await executeRegisteredTool(db, JOE, "accept-ready-plan", structuredClone(ACCEPT));
  assert.equal(replay.replayed, true);
  assert.equal(db.calls.filter(c => c.sql.includes("accept_sourced_work_request_plan")).length, 1);
  assert.equal(db.calls.filter(c => c.sql.startsWith("insert into event")).length, 1);
  const changed = await refused(() => executeRegisteredTool(db, JOE, "accept-ready-plan", { ...ACCEPT, plan_hash: `sha256:${"b".repeat(64)}` }));
  assert.equal(changed.error, "key_reuse");
  const dell = { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const foreign = await refused(() => executeRegisteredTool(db, dell, "accept-ready-plan", structuredClone(ACCEPT)));
  assert.equal(foreign.error, "key_reuse");
});

test("validation and human authority boundaries refuse before DB I/O", async () => {
  const db = { query: async () => { throw new Error("database must not be called"); } };
  for (const args of [{ ...PROPOSE, runbook_ref: "doctrine:other#x" }, { ...PROPOSE, recovery_ref: `safe:${"x".repeat(301)}` }, { ...PROPOSE, dependency_refs: ["safe:a", "safe:a"] }, { ...PROPOSE, caps: { max_steps: 21, max_duration_minutes: 30 } }]) {
    const out = await refused(() => executeRegisteredTool(db, JOE, "propose-ready-plan", args)); assert.equal(out.error, "invalid_ready_plan");
  }
  const nonhuman = await refused(() => executeRegisteredTool(db, BOT, "accept-ready-plan", structuredClone(ACCEPT))); assert.equal(nonhuman.error, "human_only");
  const noAuthority = await refused(() => callTool({}, JOE, "accept-ready-plan", structuredClone(ACCEPT), "full")); assert.equal(noAuthority.error, "authority_connection_unavailable");
});
