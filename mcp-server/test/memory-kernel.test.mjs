import test from "node:test";
import assert from "node:assert/strict";

import { memoryTools } from "../src/memory.js";
import { ToolError } from "../src/tool-error.js";

async function withEnvelope(_c, _actor, _verb, args, fn) {
  if (!args.idempotency_key) throw new ToolError({ error: "missing_idempotency_key" });
  return fn();
}
const events = [];
async function writeEvent(_c, _actor, verb, subjectType, subjectId, fields) {
  events.push({ verb, subjectType, subjectId, fields });
}
const authorityGuard = args => {
  const banned = ["tenant", "tenant_id", "identity", "actor", "audience", "capabilities", "actions", "write", "calls_models"];
  if (Object.keys(args || {}).some(key => banned.includes(key))) throw new ToolError({ error: "caller_authority_field_forbidden" });
};
const TOOLS = memoryTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields: authorityGuard });
async function strictEnvelope(c, _actor, _verb, args, fn) {
  if (!args.idempotency_key) throw new ToolError({ error: "missing_idempotency_key" });
  await c.query("envelope lookup");
  return fn();
}
const STRICT_TOOLS = memoryTools({ withEnvelope: strictEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields: authorityGuard });
const KEY = "0297aaaa-0000-4000-8000-000000000001";

test("memory kernel exposes observe, recall, promote, correct, and forget", () => {
  for (const name of ["observe-memory", "recall-memory", "promote-memory", "correct-memory", "forget-memory"])
    assert.ok(TOOLS[name], name);
  assert.equal(TOOLS["recall-memory"].write, undefined);
  assert.equal(TOOLS["observe-memory"].write, true);
  assert.equal(TOOLS["promote-memory"].humanOnly, true);
  assert.equal(TOOLS["correct-memory"].humanOnly, true);
  assert.equal(TOOLS["forget-memory"].humanOnly, true);
  assert.ok(TOOLS["review-memory"]);
});

test("observe derives personal scope from the verified sponsor and stores provenance", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/from ops\.sourced_work_request_plan/.test(sql)) return { rows: [{ id: "plan-1", work_request_id: "wr-1", work_request_version: 3 }] };
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: "memory-1", version: 1, status: "candidate", scope: "personal" }] };
    if (/insert into memory_evidence/.test(sql)) return { rows: [{ id: "evidence-1" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["observe-memory"].handler(client,
    { id: "runtime", slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { idempotency_key: KEY, kind: "preference", statement: "Prefer concise summaries", context: "briefing", scope: "personal",
      plan_id: "plan-1",
      evidence: { source_type: "conversation", source_ref: "turn-1", observation: "Joe corrected a verbose draft" } });
  assert.equal(out.ok, true);
  const item = statements.find(s => /insert into memory_item/.test(s.sql));
  assert.equal(item.params[8], "joe", "personal owner comes from verified sponsor, not args");
  assert.match(item.sql, /organization_tenant_id/);
  assert.match(item.sql, /work_request_id/);
  assert.match(item.sql, /plan_id/);
  assert.match(item.sql, /actor_id/);
  const evidence = statements.find(s => /insert into memory_evidence/.test(s.sql));
  assert.equal(evidence.params[1], "conversation");
  assert.equal(evidence.params[2], "turn-1");
});

test("observe rejects a caller authority field and invalid memory kind before DB use", async () => {
  const client = { query: async () => { throw new Error("must not query"); } };
  await assert.rejects(() => TOOLS["observe-memory"].handler(client, { slug: "joe", human: true },
    { idempotency_key: KEY, kind: "not-a-kind", statement: "x", scope: "shared" }),
    error => error.payload?.error === "memory_kind_invalid");
});

test("recall is contextual and includes shared plus sponsor personal memories", async () => {
  const client = { query: async (sql, params) => {
    assert.match(sql, /memory_item/);
    assert.match(sql, /status='promoted'|status in \('promoted'/);
    assert.match(sql, /organization_tenant_id/);
    assert.match(sql, /context/);
    assert.ok(params.includes("joe"));
    return { rows: [{ id: "m1", status: "promoted", statement: "Concise summaries", scope: "personal", confidence: 0.9, relevance: 2 }] };
  } };
  const out = await TOOLS["recall-memory"].handler(client,
    { slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { query: "briefing summary", context: "morning brief", limit: 10 });
  assert.equal(out.count, 1);
  assert.equal(out.memories[0].id, "m1");
  assert.ok(!out.memories[0].evidence, "autonomous recall does not silently expose unreviewed candidate evidence");
});

test("candidate review returns evidence and current version, while authority fields cause zero queries", async () => {
  let queries = 0;
  const client = { query: async (sql) => { queries++; assert.match(sql, /memory_evidence/); return { rows: [{ id: "m1", status: "candidate", version: 2, evidence: [{ source_type: "conversation" }] }] }; } };
  const out = await TOOLS["review-memory"].handler(client, { slug: "joe", human: true }, { memory_id: "m1" });
  assert.equal(out.memory.status, "candidate");
  assert.equal(out.memory.version, 2);
  assert.equal(out.evidence[0].source_type, "conversation");
  await assert.rejects(() => TOOLS["recall-memory"].handler(client, { slug: "joe", human: true }, { query: "x", actor: "dell" }), /caller_authority_field_forbidden/);
  assert.equal(queries, 1);
});

test("promotion requires a fresh version and records the transition", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/update memory_item/.test(sql)) return { rows: [{ id: "m1", version: 2, status: "promoted" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["promote-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "m1", base_version: 1, reason: "confirmed twice" });
  assert.equal(out.memory.status, "promoted");
  assert.match(statements[0].sql, /where id=\$1 and version=\$2 and organization_tenant_id=\$3 and status='candidate'/);
  assert.match(statements[0].sql, /organization_tenant_id/);
});

test("correction creates immutable successor lineage and forget is human-only", async () => {
  const sqls = [];
  const client = { query: async (sql) => {
    sqls.push(sql);
    if (/update memory_item/.test(sql)) return { rows: [{ id: "m1", version: 3, status: "corrected", statement: "Old" }] };
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: "m2", version: 1, status: "candidate", predecessor_id: "m1", statement: "Prefer short summaries" }] };
    return { rows: [] };
  } };
  await TOOLS["correct-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "m1", base_version: 2, statement: "Prefer short summaries", reason: "Joe corrected the preference" });
  assert.ok(sqls.every(sql => !/\bdelete\b/i.test(sql)));
  assert.match(sqls.find(sql => /insert into memory_item/.test(sql)), /predecessor_id/);
  sqls.length = 0;
  client.query = async sql => { sqls.push(sql); return { rows: [{ id: "m1", version: 4, status: "forgotten" }] }; };
  await TOOLS["forget-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "m1", base_version: 3, reason: "no longer applies" });
  assert.ok(sqls.every(sql => !/\bdelete\b/i.test(sql)));
});

test("all write verbs use idempotency serialization and every mutation is owner/tenant scoped", () => {
  for (const name of ["observe-memory", "promote-memory", "correct-memory", "forget-memory"])
    assert.equal(TOOLS[name].write, true);
  // SQL contracts are asserted by the handler tests; this test locks the
  // direct-handler authority boundary separately from registered dispatch.
  assert.throws(() => authorityGuard({ tenant_id: "dell", capabilities: [] }), /caller_authority_field_forbidden/);
});

test("known private UUIDs stay partner-isolated in both recall and mutation predicates", async () => {
  const calls = [];
  const client = { query: async (sql, params) => {
    calls.push({ sql, params });
    if (/update memory_item/.test(sql)) return { rows: [] };
    return { rows: [] };
  } };
  await assert.rejects(() => TOOLS["promote-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "known-dell-private-uuid", base_version: 1, reason: "no" }),
    error => error.payload?.error === "memory_version_conflict_or_not_candidate");
  const mutation = calls.find(call => /update memory_item/.test(call.sql));
  assert.ok(mutation.sql.includes("owner_actor_id=(select id from actor where slug=$5)"));
  assert.equal(mutation.params[4], "joe");
  await TOOLS["recall-memory"].handler(client, { id: "dell", slug: "dell", human: true }, { query: "private" });
  const recall = calls.find(call => /select id, kind, statement/.test(call.sql));
  assert.equal(recall.params[1], "dell");
  assert.match(recall.sql, /organization_tenant_id=\$3/);
});

test("authority aliases are rejected before envelope/DB and nonhuman direct writes refuse before envelope", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  for (const alias of ["tenant", "tenant_id", "identity", "actor", "audience", "capabilities", "actions", "write", "calls_models"])
    await assert.rejects(() => STRICT_TOOLS["observe-memory"].handler(client, { slug: "joe", human: true },
      { idempotency_key: KEY, kind: "fact", statement: "x", scope: "shared", evidence: {}, [alias]: "injected" }),
      error => error.payload?.error === "caller_authority_field_forbidden");
  for (const name of ["promote-memory", "correct-memory", "forget-memory"])
    await assert.rejects(() => STRICT_TOOLS[name].handler(client, { slug: "codex", human: false }, { idempotency_key: KEY, memory_id: "m", base_version: 1, statement: "x", reason: "x" }),
      error => error.payload?.error === "human_only");
  assert.equal(queries, 0);
});

test("sponsor failures are typed and happen before any query", async () => {
  let queries = 0;
  const client = { query: async () => { queries++; return { rows: [] }; } };
  await assert.rejects(() => TOOLS["recall-memory"].handler(client, { slug: "codex", human: false, via: "oauth-google", sponsor_required: true }, { query: "x" }),
    error => error.payload?.error === "missing_or_ambiguous_sponsor");
  await assert.rejects(() => TOOLS["recall-memory"].handler(client, { slug: "unknown", human: false }, { query: "x" }),
    error => error.payload?.error === "invalid_runtime_principal");
  assert.equal(queries, 0);
});

test("plan anchor is validated tenant-scoped and derives work request/version", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/from ops\.sourced_work_request_plan/.test(sql)) return { rows: [{ id: "plan-1", work_request_id: "wr-1", work_request_version: 7 }] };
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: "m1", status: "candidate", version: 1 }] };
    if (/insert into memory_evidence/.test(sql)) return { rows: [{ id: "e1" }] };
    return { rows: [] };
  } };
  await TOOLS["observe-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, kind: "fact", statement: "Plan-bound fact", scope: "shared", plan_id: "plan-1",
      evidence: { source_type: "run", observation: "observed" } });
  const anchor = statements.find(s => /from ops\.sourced_work_request_plan/.test(s.sql));
  assert.match(anchor.sql, /organization_tenant_id/);
  const insert = statements.find(s => /insert into memory_item/.test(s.sql));
  assert.match(insert.sql, /work_request_id/);
  assert.equal(insert.params.includes(7), true, "stored provenance uses plan's version, not caller input");
});

test("unknown/cross-tenant plans and caller-assembled passport fields refuse before memory insert", async () => {
  const statements = [];
  const client = { query: async (sql) => { statements.push(sql); return { rows: [] }; } };
  await assert.rejects(() => TOOLS["observe-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, kind: "fact", statement: "x", scope: "shared", plan_id: "unknown-plan", evidence: { source_type: "x", observation: "x" } }),
    error => error.payload?.error === "memory_plan_not_found_or_forbidden");
  await assert.rejects(() => TOOLS["observe-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, kind: "fact", statement: "x", scope: "shared", work_request: "WR-1", evidence: { source_type: "x", observation: "x" } }),
    error => error.payload?.error === "memory_plan_anchor_only");
  assert.equal(statements.some(sql => /insert into memory_item/.test(sql)), false);
});
