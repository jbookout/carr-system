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
const TOOLS = memoryTools({ withEnvelope, writeEvent, ToolError });
const KEY = "0297aaaa-0000-4000-8000-000000000001";

test("memory kernel exposes observe, recall, promote, correct, and forget", () => {
  for (const name of ["observe-memory", "recall-memory", "promote-memory", "correct-memory", "forget-memory"])
    assert.ok(TOOLS[name], name);
  assert.equal(TOOLS["recall-memory"].write, undefined);
  assert.equal(TOOLS["observe-memory"].write, true);
  assert.equal(TOOLS["promote-memory"].humanOnly, true);
});

test("observe derives personal scope from the verified sponsor and stores provenance", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (/insert into memory_item/.test(sql)) return { rows: [{ id: "memory-1", version: 1, status: "candidate", scope: "personal" }] };
    if (/insert into memory_evidence/.test(sql)) return { rows: [{ id: "evidence-1" }] };
    return { rows: [] };
  } };
  const out = await TOOLS["observe-memory"].handler(client,
    { id: "runtime", slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { idempotency_key: KEY, kind: "preference", statement: "Prefer concise summaries", context: "briefing", scope: "personal",
      evidence: { source_type: "conversation", source_ref: "turn-1", observation: "Joe corrected a verbose draft" } });
  assert.equal(out.ok, true);
  const item = statements.find(s => /insert into memory_item/.test(s.sql));
  assert.equal(item.params[4], "joe", "personal owner comes from verified sponsor, not args");
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
    assert.match(sql, /shared|personal/);
    assert.ok(params.includes("joe"));
    return { rows: [{ id: "m1", status: "promoted", statement: "Concise summaries", scope: "personal", confidence: 0.9 }] };
  } };
  const out = await TOOLS["recall-memory"].handler(client,
    { slug: "codex", human: false, sponsoring_human_slug: "joe", sponsor_required: true },
    { query: "briefing summary", context: "morning brief", limit: 10 });
  assert.equal(out.count, 1);
  assert.equal(out.memories[0].id, "m1");
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
  assert.match(statements[0].sql, /where id=\$1 and version=\$2 and status='candidate'/);
});

test("correction and forget are reversible state transitions, never deletes", async () => {
  const sqls = [];
  const client = { query: async (sql) => {
    sqls.push(sql);
    if (/update memory_item/.test(sql)) return { rows: [{ id: "m1", version: 3, status: "corrected" }] };
    return { rows: [] };
  } };
  await TOOLS["correct-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "m1", base_version: 2, statement: "Prefer short summaries", reason: "Joe corrected the preference" });
  assert.ok(sqls.every(sql => !/\bdelete\b/i.test(sql)));
  sqls.length = 0;
  client.query = async sql => { sqls.push(sql); return { rows: [{ id: "m1", version: 4, status: "forgotten" }] }; };
  await TOOLS["forget-memory"].handler(client, { id: "joe", slug: "joe", human: true },
    { idempotency_key: KEY, memory_id: "m1", base_version: 3, reason: "no longer applies" });
  assert.ok(sqls.every(sql => !/\bdelete\b/i.test(sql)));
});
