// receipt-producer.test.mjs — the writer that makes the receipt layer non-empty.
//
// WHY THIS FILE EXISTS. ops.write_receipt, its readback, the continuity reducer
// and an acceptance bar requiring at least one PROVEN receipt were all built
// before anything produced one. The table would have stayed empty and the bar
// permanently unmeetable — the inert-substrate defect one layer up, where a
// surface and the gate depending on it both ship and the producer between them
// does not. These tests drive the producer with a recording fake client.

import { test } from "node:test";
import assert from "node:assert/strict";
import { writeReceiptsFor } from "../src/tools.js";

const SID = "aaaaaaaa-1111-2222-3333-444444444444";
const QUALIFIED = { id: "actor-joe", slug: "joe", human: true, via: "oauth-google",
                    organization_tenant_id: "carr-internal",
                    application_session_id: SID };
const LEGACY = { id: "actor-codex", slug: "codex", human: false, via: "agent-token" };

function fakeClient({ subjects = [], prior = null, digest = "digest-A" } = {}) {
  const calls = [];
  return {
    calls,
    async query(sql, params = []) {
      calls.push({ sql: sql.replace(/\s+/g, " ").trim(), params });
      if (sql.includes("from event")) return { rows: subjects };
      if (sql.includes("write_receipt_digest")) return { rows: [{ d: digest }] };
      if (sql.includes("select claimed_digest")) {
        return { rows: prior ? [{ claimed_digest: prior }] : [] };
      }
      if (sql.includes("insert into ops.write_receipt")) return { rows: [] };
      if (sql.includes("prove_write_receipt")) return { rows: [{}] };
      throw new Error(`fake received unexpected SQL: ${sql}`);
    },
  };
}

test("a qualifying write produces a receipt AND proves it in the same transaction", async () => {
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" }] });
  await writeReceiptsFor(c, QUALIFIED, "update-deal", "key-1", "hash-1");
  const inserted = c.calls.find(k => k.sql.includes("insert into ops.write_receipt"));
  const proved = c.calls.find(k => k.sql.includes("prove_write_receipt"));
  assert.ok(inserted, "a qualifying write must produce a receipt");
  assert.ok(proved, "producing an unproven receipt would replace an empty table "
                  + "with a permanently failing acceptance bar");
  assert.equal(inserted.params[1], SID, "the receipt must bind the session");
  assert.equal(inserted.params[7], "key-1", "and name the evidence it is about");
  assert.equal(inserted.params[9], "origin", "the first receipt for a subject builds on origin");
  assert.ok(c.calls.indexOf(inserted) < c.calls.indexOf(proved), "insert must precede the proof");
});

test("a LEGACY write produces nothing at all — not even a query", async () => {
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" }] });
  await writeReceiptsFor(c, LEGACY, "update-deal", "key-2", "hash-2");
  assert.equal(c.calls.length, 0,
    "a row with no session proves nothing, so vouching for it would be a proof "
    + "about something already declared unprovable");
});

test("the receipt chains onto the subject's previous result", async () => {
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" }],
                         prior: "state-earlier", digest: "state-now" });
  await writeReceiptsFor(c, QUALIFIED, "update-deal", "key-3", "hash-3");
  const ins = c.calls.find(k => k.sql.includes("insert into ops.write_receipt"));
  assert.equal(ins.params[8], "state-now", "the receipt records what this write produced");
  assert.equal(ins.params[9], "state-earlier",
    "and what it built on — which is what makes a later conflict or reversal "
    + "checkable rather than assertable");
});

test("a no-op restatement writes no receipt", async () => {
  // Same result as the state it built on. A chain of identical links is noise
  // and would make every restatement look like a change.
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" }],
                         prior: "same", digest: "same" });
  await writeReceiptsFor(c, QUALIFIED, "update-deal", "key-4", "hash-4");
  assert.ok(!c.calls.some(k => k.sql.includes("insert into ops.write_receipt")),
    "a restatement that changed nothing must not manufacture a link");
});

test("one receipt per subject the write touched", async () => {
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" },
                                    { subject_type: "party", subject_id: "party-9" }] });
  await writeReceiptsFor(c, QUALIFIED, "link-parties", "key-5", "hash-5");
  const ins = c.calls.filter(k => k.sql.includes("insert into ops.write_receipt"));
  assert.equal(ins.length, 2, "a write touching two subjects owes a receipt to each");
  assert.deepEqual(ins.map(i => i.params[5]).sort(), ["deal", "party"]);
});

test("a write that touched no subject produces no receipt", async () => {
  const c = fakeClient({ subjects: [] });
  await writeReceiptsFor(c, QUALIFIED, "log-activity", "key-6", "hash-6");
  assert.ok(!c.calls.some(k => k.sql.includes("insert into ops.write_receipt")),
    "a receipt about nothing is not a receipt");
});

test("nothing the caller sent can reach the receipt", async () => {
  // Its inputs are (client, actor, verb, key, hash). The digest comes from a
  // database function over server-derived values, and the subject list comes
  // from rows already written under this session.
  assert.equal(writeReceiptsFor.length, 5);
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" }] });
  await writeReceiptsFor(c, { ...QUALIFIED, claimed_digest: "forged" },
                         "update-deal", "key-7", "hash-7");
  const ins = c.calls.find(k => k.sql.includes("insert into ops.write_receipt"));
  assert.ok(!ins.params.includes("forged"), "an actor-supplied digest must never ride in");
});

test("the write envelope actually calls the producer", async () => {
  // Shape, and labelled as such: proving this behaviourally needs a real
  // transaction and a database this suite does not have. Without it the
  // producer could be correct and never invoked, which is the exact shape of
  // the defect that shipped an inert door earlier.
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  const env = src.slice(src.indexOf("async function withEnvelope"),
                        src.indexOf("async function writeEvent"));
  assert.match(env, /await writeReceiptsFor\(client, actor, verb, key, hash\)/,
    "withEnvelope must call the producer");
  assert.ok(env.indexOf("toolCallInsertSQL") < env.indexOf("writeReceiptsFor"),
    "receipts must be written AFTER the tool_call row, because the readback "
    + "reads that row and could not otherwise prove");
});
