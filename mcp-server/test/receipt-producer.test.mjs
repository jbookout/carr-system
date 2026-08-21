// receipt-producer.test.mjs — the writer that makes the receipt layer non-empty.
//
// WHY THIS FILE EXISTS. ops.write_receipt, its readback, the continuity reducer
// and an acceptance bar requiring at least one PROVEN receipt were all built
// before anything produced one. The table would have stayed empty and the bar
// permanently unmeetable — the inert-substrate defect one layer up, where a
// surface and the gate depending on it both ship and the producer between them
// does not. These tests drive the producer with a recording fake client.
//
// 0220 SPLITS THE DIGEST THE PRODUCER COMPUTES. What was one `claimed_digest`
// is now two: `call_digest` (proof of attachment, recomputed by the database
// from the frozen tool_call row and the receipt's own subject) and
// `material_digest` (the caller's claim about the SUBJECT, read by
// prior_digest, the conflict detector, exact reversal and the reducer). The
// producer's single digest query now returns BOTH, computed per subject, and
// the insert carries 11 columns instead of 10.

import { test } from "node:test";
import assert from "node:assert/strict";
import { writeReceiptsFor } from "../src/tools.js";

const SID = "aaaaaaaa-1111-2222-3333-444444444444";
const QUALIFIED = { id: "actor-joe", slug: "joe", human: true, via: "oauth-google",
                    organization_tenant_id: "carr-internal",
                    application_session_id: SID };
const LEGACY = { id: "actor-codex", slug: "codex", human: false, via: "agent-token" };

function fakeClient({ subjects = [], prior = null, material = "material-A" } = {}) {
  const calls = [];
  return {
    calls,
    async query(sql, params = []) {
      calls.push({ sql: sql.replace(/\s+/g, " ").trim(), params });
      if (sql.includes("from event")) return { rows: subjects };
      if (sql.includes("write_receipt_digest")) {
        // params: [verb, actor_id, tenant, sid, hash, subject_type, subject_id, key]
        // (query 8's write_receipt_material_digest args reuse positions 4,6,7).
        // THE CALL DIGEST IS COMPUTED PER SUBJECT: this fake derives it from
        // the subject_id actually passed in, so two different subjects in one
        // call get two different call digests -- the same guarantee the real
        // ops.write_receipt_digest gives by taking the subject as an argument.
        const subjectId = params[6];
        return { rows: [{ call_digest: `call-digest-for-${subjectId}`, material_digest: material }] };
      }
      // THE PER-SUBJECT LOCK. The producer serialises on the subject before it
      // reads the head, because a read-then-insert that is not atomic lets two
      // concurrent writers build on the same head and manufacture a conflict
      // nothing in the runtime can clear. Asserted below rather than merely
      // tolerated: a fake that quietly accepted any SQL it did not recognise
      // is how three separate producer mutations stayed green.
      if (sql.includes("pg_advisory_xact_lock")) {
        if (!String(params[0] ?? "").includes(":")) {
          throw new Error("the lock is not keyed on the subject it is protecting");
        }
        return { rows: [{}] };
      }
      if (sql.includes("from ops.write_receipt w")) {
        // ORDER IS THE WHOLE POINT OF THE LOCK. Taking it after reading the
        // head protects nothing: the race this closes is between the read and
        // the insert. Removing the lock entirely left the live database test
        // green, because that test cannot run two writers at once, so this is
        // the only place the ordering is checked.
        const locked = calls.some(c => c.sql.includes("pg_advisory_xact_lock"));
        if (!locked) {
          throw new Error("the producer read the head without first locking the subject");
        }
        // The head must be read PROVEN and unretracted: the prior-state guard
        // accepts nothing else, so reading the newest row regardless of proof
        // would hand the database a prior it refuses.
        if (!/is_proven/.test(sql)) {
          throw new Error("the producer read a prior without requiring it to be proven");
        }
        if (!/order by w\.seq desc/.test(sql)) {
          throw new Error("the producer read the head by something other than seq order");
        }
        return { rows: prior ? [{ material_digest: prior }] : [] };
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
  assert.equal(inserted.params[10], "origin", "the first receipt for a subject builds on origin");
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
                         prior: "state-earlier", material: "state-now" });
  await writeReceiptsFor(c, QUALIFIED, "update-deal", "key-3", "hash-3");
  const ins = c.calls.find(k => k.sql.includes("insert into ops.write_receipt"));
  assert.equal(ins.params[9], "state-now",
    "the receipt records the MATERIAL claim of what this write produced");
  assert.equal(ins.params[10], "state-earlier",
    "and what it built on — which is what makes a later conflict or reversal "
    + "checkable rather than assertable");
});

test("a no-op restatement writes no receipt", async () => {
  // Same MATERIAL as the state it built on. A chain of identical links is
  // noise and would make every restatement look like a change. This is now a
  // material-to-material comparison, never a comparison against the call
  // digest, which is a digest of the CALL and would only ever match by
  // accident.
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" }],
                         prior: "same", material: "same" });
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

test("the call digest is computed per subject — two subjects must not share one", async () => {
  // 0220 binds the call digest to the receipt's own subject, so hoisting the
  // digest query out of the per-subject loop (computing it once per CALL
  // instead of once per SUBJECT) would hand every subject the same digest and
  // make it transferable between them. This asserts the query actually runs
  // once per subject, with that subject's own identifiers, and that the two
  // resulting receipts carry DIFFERENT call digests.
  const c = fakeClient({ subjects: [{ subject_type: "deal", subject_id: "deal-1" },
                                    { subject_type: "party", subject_id: "party-9" }] });
  await writeReceiptsFor(c, QUALIFIED, "link-parties", "key-8", "hash-8");
  const digestCalls = c.calls.filter(k => k.sql.includes("write_receipt_digest("));
  assert.equal(digestCalls.length, 2, "the digest must be computed once per subject");
  assert.deepEqual(digestCalls.map(k => k.params[6]).sort(), ["deal-1", "party-9"],
    "each digest call must carry that subject's own identifiers");
  const ins = c.calls.filter(k => k.sql.includes("insert into ops.write_receipt"));
  assert.notEqual(ins[0].params[8], ins[1].params[8],
    "two different subjects must not end up with the same call digest");
});

test("a write that touched no subject produces no receipt", async () => {
  const c = fakeClient({ subjects: [] });
  await writeReceiptsFor(c, QUALIFIED, "log-activity", "key-6", "hash-6");
  assert.ok(!c.calls.some(k => k.sql.includes("insert into ops.write_receipt")),
    "a receipt about nothing is not a receipt");
});

test("nothing the caller sent can reach the receipt", async () => {
  // Its inputs are (client, actor, verb, key, hash). The digests come from
  // database functions over server-derived values, and the subject list
  // comes from rows already written under this session.
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
