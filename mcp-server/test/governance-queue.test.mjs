// governance-queue.test.mjs — WR-000019 slice S6, BATCH REVIEW QUEUE.
//
// governance-queue is a read-only projection across three pending-decision
// lanes (rule approvals, guidance import batches, retrieval proposals), read
// through one SECURITY DEFINER function (ops.read_governance_queue(),
// migration 0345) because carr_reader has no direct grant on `rule` or
// `retrieval_proposal`. This suite proves the verb's own shape: write:false,
// a single query against that function, and the three lanes surfaced with
// their counts — never a new write path.

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";

const actor = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true };

test("governance-queue is read-only and takes no arguments", () => {
  assert.equal(TOOLS["governance-queue"].write, false);
  assert.deepEqual(TOOLS["governance-queue"].inputSchema.properties, {});
  assert.equal(TOOLS["governance-queue"].inputSchema.additionalProperties, false);
});

test("governance-queue reads exactly one function call and surfaces all three lanes with counts", async () => {
  const queue = {
    pending_rule_approvals: [
      { rule_id: "rule-1", statement: "always X", enforcement_status: "blocked" },
    ],
    pending_guidance_import_batches: [
      { batch_id: "batch-1", manifest_digest: "a".repeat(64), entry_count: 3 },
    ],
    pending_retrieval_proposals: [],
  };
  const calls = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      if (/read_governance_queue/.test(sql)) return { rows: [{ queue }] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
  const result = await TOOLS["governance-queue"].handler(client, actor, {});
  assert.equal(calls.length, 1, "one call, straight through the SECURITY DEFINER projection");
  assert.equal(result.ok, true);
  assert.deepEqual(result.pending_rule_approvals, queue.pending_rule_approvals);
  assert.deepEqual(result.pending_guidance_import_batches, queue.pending_guidance_import_batches);
  assert.deepEqual(result.pending_retrieval_proposals, []);
  assert.deepEqual(result.counts, {
    pending_rule_approvals: 1,
    pending_guidance_import_batches: 1,
    pending_retrieval_proposals: 0,
    total: 2,
  });
});

test("governance-queue tolerates an empty queue without throwing", async () => {
  const client = {
    query: async (sql) => {
      if (/read_governance_queue/.test(sql)) return { rows: [{ queue: {} }] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
  const result = await TOOLS["governance-queue"].handler(client, actor, {});
  assert.equal(result.ok, true);
  assert.deepEqual(result.counts, {
    pending_rule_approvals: 0, pending_guidance_import_batches: 0,
    pending_retrieval_proposals: 0, total: 0,
  });
});
