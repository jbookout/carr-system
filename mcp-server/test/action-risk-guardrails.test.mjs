// Deterministic guardrail coverage: untrusted authority fields must stop at the
// registered-tool choke point, and the human ownership handoff must be both
// human-gated and optimistic-concurrency guarded.

import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { callTool } from "../src/mcp.js";

const CANARY = "CARR-SECRET-CANARY-7F4A";
const DEAL_ID = "50000000-0000-0000-0000-000000000001";
const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "test" };
const AGENT = { id: "10000000-0000-0000-0000-000000000099", slug: "codex", display: "Codex", human: false, via: "test" };

function payload(extra = {}) {
  return { idempotency_key: "set-lead-guardrail", deal: "Synthetic Deal", new_lead: "dell", base_version: 1, ...extra };
}

async function rejected(fn) {
  try {
    await fn();
    assert.fail("expected ToolError");
  } catch (error) {
    assert.ok(error instanceof ToolError);
    return error.payload;
  }
}

class SetLeadFake {
  constructor(version = 1) {
    this.version = version;
    this.handlerCalls = 0;
    this.participantWrites = 0;
    this.eventWrites = 0;
    this.envelopeWrites = 0;
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response from tool_call")) return { rows: [] };
    if (sql.includes("from v_ref_index where subject_type='deal' and display_name ilike"))
      return { rows: [{ subject_id: DEAL_ID }] };
    if (sql === "select version from deal where id=$1 for update")
      return { rows: [{ version: this.version }] };
    if (sql === "select created_at from deal where id=$1") return { rows: [{ created_at: "2026-08-15T00:00:00Z" }] };
    if (sql.includes("from event e join actor a")) return { rows: [] };
    if (sql === "select id from actor where slug=$1") return { rows: [{ id: `actor-${params[0]}` }] };
    if (sql.startsWith("update deal_participant set to_at=now()")) {
      this.participantWrites += 1;
      return { rows: [{ actor_id: "actor-joe" }] };
    }
    if (sql.startsWith("insert into deal_participant")) {
      this.participantWrites += 1;
      return { rows: [] };
    }
    if (sql.startsWith("update deal set owner=")) {
      this.handlerCalls += 1;
      this.version += 1;
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      this.eventWrites += 1;
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.envelopeWrites += 1;
      return { rows: [] };
    }
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("reserved authority fields are rejected recursively before a direct handler or database access", async () => {
  const reserved = [
    "tenant", "tenant_id", "organization_tenant_id", "sponsor", "sponsoring_human_id",
    "sponsoring_human_slug", "human_slug", "identity", "actor", "runtime_principal",
    "authorization", "authorization_class", "profile", "capability", "capabilities",
    "action", "actions", "action_authority", "action_authorities", "allowed_actions",
    "write", "writes_records", "calls_models", "call_models",
  ];
  for (const field of reserved) {
    const db = new SetLeadFake();
    const out = await rejected(() => executeRegisteredTool(db, JOE, "set-lead", payload({ nested: { [field]: CANARY } })));
    assert.deepEqual(out, { error: "caller_authority_field_forbidden" });
    assert.equal(db.handlerCalls, 0, `${field} reached a handler`);
    assert.equal(db.participantWrites, 0, `${field} reached a write`);
    assert.ok(!JSON.stringify(out).includes(CANARY));
  }
});

test("the same reserved-field gate covers composite executeRegisteredTool dispatch", async () => {
  const db = { query: async () => { throw new Error("database must not be reached"); } };
  const out = await rejected(() => executeRegisteredTool(db, JOE, "set-next-step", {
    idempotency_key: "composite-guard", text: "synthetic", inner: { profile: CANARY },
  }));
  assert.deepEqual(out, { error: "caller_authority_field_forbidden" });
  assert.ok(!JSON.stringify(out).includes(CANARY));
});

test("direct MCP and call-verb recursion refuse authority claims before a writer pool", async () => {
  const direct = await rejected(() => callTool({}, JOE, "set-lead", payload({ profile: CANARY })));
  assert.deepEqual(direct, { error: "caller_authority_field_forbidden" });
  assert.ok(!JSON.stringify(direct).includes(CANARY));

  const recursive = await rejected(() => callTool({}, JOE, "call-verb", {
    verb: "set-lead", args: payload({ nested: { action_authority: CANARY } }),
  }));
  assert.deepEqual(recursive, { error: "caller_authority_field_forbidden" });
  assert.ok(!JSON.stringify(recursive).includes(CANARY));
});

test("set-lead is a human-only optimistic-concurrency mutation", async () => {
  const tool = TOOLS["set-lead"];
  assert.equal(tool.humanOnly, true);
  assert.ok(tool.inputSchema.required.includes("base_version"));
  assert.ok(Object.hasOwn(tool.inputSchema.properties, "base_version"));

  const nonhuman = await rejected(() => executeRegisteredTool(new SetLeadFake(), AGENT, "set-lead", payload()));
  assert.equal(nonhuman.error, "human_only");

  const missing = await rejected(() => executeRegisteredTool(new SetLeadFake(), JOE, "set-lead", payload({ base_version: undefined })));
  assert.equal(missing.error, "missing_required");

  const invalid = await rejected(() => executeRegisteredTool(new SetLeadFake(), JOE, "set-lead", payload({ base_version: "one" })));
  assert.equal(invalid.error, "invalid_number");
});

test("two same-version set-lead proposals produce one event and one version_conflict", async () => {
  const db = new SetLeadFake(1);
  const first = await executeRegisteredTool(db, JOE, "set-lead", payload({ idempotency_key: "race-one" }));
  assert.deepEqual(first, { ok: true, new_lead: "dell" });
  assert.equal(db.eventWrites, 1);
  assert.equal(db.participantWrites, 2);

  const stale = await rejected(() => executeRegisteredTool(db, JOE, "set-lead", payload({ idempotency_key: "race-two" })));
  assert.equal(stale.error, "version_conflict");
  assert.equal(db.eventWrites, 1, "the stale second proposal must not record a second event");
  assert.equal(db.participantWrites, 2, "the stale second proposal must not mutate participants");
});
