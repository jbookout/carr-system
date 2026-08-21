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
import { writeReceiptsFor, executeRegisteredTool } from "../src/tools.js";

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

// ─────────────────────────────────────────────────────────────────────────
// THE ENVELOPE ACTUALLY CALLS THE PRODUCER — behaviourally.
//
// WHAT WAS HERE BEFORE, AND WHY IT WAS NOT ENOUGH. This assertion used to read
// src/tools.js as TEXT and regex-match `await writeReceiptsFor(client, actor,
// verb, key, hash)` inside withEnvelope, with a comment conceding it was
// "shape, and labelled as such". Two things are wrong with that, and the second
// is the serious one:
//
//   IT ASSERTS ON SPELLING. Rename a parameter, reorder the arguments, wrap the
//   call in a helper, or pass the same values from different variables, and a
//   correct envelope fails while a broken one that happens to contain the
//   literal string passes.
//
//   IT PROVES THE CALL IS WRITTEN, NEVER THAT IT RUNS. A `return` above it, a
//   condition around it, an early replay path -- none of that moves the text.
//   The defect this file exists for was a producer that shipped and was never
//   invoked; an assertion that cannot distinguish "present in the source" from
//   "executed" is blind to exactly that.
//
// So: drive a REAL registered write verb through executeRegisteredTool with a
// recording fake, and assert on what the envelope actually did. triage-item is
// used because it is the simplest write in the registry -- one update, one
// event -- so the recorded call list is about the envelope rather than about
// the verb.
const INBOX_ITEM = "cccccccc-1111-2222-3333-444444444444";

function envelopeClient() {
  const calls = [];
  return {
    calls,
    async query(text, params = []) {
      const sql = text.replace(/\s+/g, " ").trim();
      calls.push({ sql, params });
      if (/^select request_hash, response/.test(sql)) return { rows: [] };
      if (sql.includes("update ingest_inbox"))
        return { rows: [{ id: INBOX_ITEM, source: "email", status: "rejected" }] };
      if (sql.includes("insert into event")) return { rows: [] };
      if (sql.includes("insert into tool_call")) return { rows: [] };
      // writeReceiptsFor's own queries, from here down.
      if (sql.includes("from event"))
        return { rows: [{ subject_type: "inbox", subject_id: INBOX_ITEM }] };
      if (sql.includes("write_receipt_digest"))
        return { rows: [{ call_digest: "call-d", material_digest: "material-d" }] };
      if (sql.includes("pg_advisory_xact_lock")) return { rows: [{}] };
      if (sql.includes("from ops.write_receipt w")) return { rows: [] };
      if (sql.includes("insert into ops.write_receipt")) return { rows: [] };
      if (sql.includes("prove_write_receipt")) return { rows: [{}] };
      throw new Error(`envelope fake received unexpected SQL: ${sql}`);
    },
  };
}

const TRIAGE_ARGS = { idempotency_key: "env-key-1", item_id: INBOX_ITEM,
                      status: "rejected", note: "not ours" };

test("the write envelope actually calls the producer, and only after the tool_call row", async () => {
  const c = envelopeClient();
  await executeRegisteredTool(c, QUALIFIED, "triage-item", TRIAGE_ARGS);

  const toolCallAt = c.calls.findIndex(k => k.sql.includes("insert into tool_call"));
  const receiptAt  = c.calls.findIndex(k => k.sql.includes("insert into ops.write_receipt"));
  assert.ok(toolCallAt >= 0, "the envelope did not write a tool_call row at all");
  assert.ok(receiptAt >= 0,
    "a qualified write went through the envelope and produced NO receipt — the "
    + "producer is written but never invoked, which is the defect this file exists for");
  assert.ok(toolCallAt < receiptAt,
    "the receipt was written BEFORE the tool_call row; the readback recomputes the "
    + "call digest from that row and could not prove against one that does not exist yet");
  // AND IT WAS PROVEN IN THE SAME TRANSACTION. A receipt left unproven blocks
  // the acceptance bar, so producing one and walking away replaces an empty
  // table with a permanently failing one.
  const proveAt = c.calls.findIndex(k => k.sql.includes("prove_write_receipt"));
  assert.ok(proveAt > receiptAt, "the receipt was never proven after being written");
  // The receipt must be about THIS call, not some other one.
  assert.ok(c.calls[receiptAt].params.includes("env-key-1"),
    "the receipt does not carry the idempotency key of the call that produced it");
});

test("a replayed write writes neither a tool_call row nor a receipt", async () => {
  // THE OTHER HALF OF "IT RUNS": the envelope's replay branch returns before
  // either write. A source-text assertion cannot see this path at all, and a
  // producer invoked on a replay would file a SECOND receipt for one call.
  //
  // requestHash is not exported, so the hash is LEARNED from a first honest
  // run rather than recomputed here -- a locally reimplemented hash would be a
  // fixture carrying a value production never supplies, which is one of the six
  // ways a suite in this repository has lied before.
  const first = envelopeClient();
  await executeRegisteredTool(first, QUALIFIED, "triage-item", TRIAGE_ARGS);
  const digestCall = first.calls.find(k => k.sql.includes("write_receipt_digest"));
  const hash = digestCall.params[4];   // [verb, actor, tenant, sid, hash, ...]
  assert.ok(hash, "could not learn the envelope's request hash from the first run");

  const c = envelopeClient();
  const inner = c.query.bind(c);
  c.query = async function (text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (/^select request_hash, response/.test(sql)) {
      c.calls.push({ sql, params });
      return { rows: [{ request_hash: hash,
                        response: { ok: true, replayed_fixture: true },
                        actor_id: QUALIFIED.id,
                        organization_tenant_id: QUALIFIED.organization_tenant_id,
                        application_session_id: QUALIFIED.application_session_id }] };
    }
    return inner(text, params);
  };

  const out = await executeRegisteredTool(c, QUALIFIED, "triage-item", TRIAGE_ARGS);
  assert.equal(out.replayed, true,
    "the fixture did not actually take the replay path, so this test proves nothing");
  assert.ok(!c.calls.some(k => k.sql.includes("insert into tool_call")),
    "a replay wrote a second tool_call row");
  assert.ok(!c.calls.some(k => k.sql.includes("insert into ops.write_receipt")),
    "a replay filed a SECOND receipt for one call");
});

test("an unqualified (legacy) write still records the call, and files no receipt", async () => {
  // The producer's own first line returns when there is no session. Asserted
  // through the envelope so it is the SYSTEM's behaviour being checked, not the
  // function's: the tool_call row must still be written, or a legacy write
  // would stop being recorded at all.
  const c = envelopeClient();
  await executeRegisteredTool(c, LEGACY, "triage-item", TRIAGE_ARGS);
  assert.ok(c.calls.some(k => k.sql.includes("insert into tool_call")),
    "a legacy write stopped recording its tool_call row");
  assert.ok(!c.calls.some(k => k.sql.includes("insert into ops.write_receipt")),
    "a write with no authenticated session produced a receipt, which would be a "
    + "receipt vouching for nothing");
});
