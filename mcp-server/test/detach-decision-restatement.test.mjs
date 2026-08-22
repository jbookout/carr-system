// THE ONE LINE THAT MAKES detach-decision SURVIVE 0238, and the reason it needs
// a test of its own.
//
// WHAT BROKE. 0238 section (H) stops an event row a write receipt rests on from
// being rewritten underneath it, because the receipt's material digest is a fold
// over exactly those rows. log-decision with an `about` ref writes its pointer
// through the shared writeEvent helper, which carries the application session,
// so on a qualified write the producer files and PROVES a receipt on that
// pointer. detach-decision then restates that same row in place -- which is what
// the verb IS, and what 0232 deliberately left event.UPDATE open for -- and the
// guard refused the whole call. Every about-attached decision became permanently
// un-detachable, and the remedy the error message named did not exist: a proven
// receipt cannot be retracted.
//
// WHAT FIXES IT. The receipt is taken back on the record first, by the primitive
// that undoes a PROVEN claim: a reversal. reverseRestatedReceipts files it, in
// the envelope, after the tool_call row exists -- and it only looks for work when
// a verb has DECLARED that it restated evidence, because nothing in the envelope
// can otherwise tell that a handler ran an UPDATE against public.event.
//
// SO THE DECLARATION IS THE FIX. Delete `noteEvidenceRestated` from the handler
// and every other test in this repo still passes: the database guard is intact,
// the producer function is intact, and the verb is broken again exactly as it
// shipped. This file is the test that notices.
//
// IT ASSERTS THE OBSERVABLE, not the call. A spy on the exported function would
// pass against a handler that declared the WRONG subject, which is the same
// failure wearing a different hat. What is asserted here is what the envelope
// then does: it goes looking for receipts to take back, on the subject the
// pointer was attached to.
import assert from "node:assert/strict";
import test from "node:test";
import { TOOLS } from "../src/tools.js";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  session: "40000000-0000-0000-0000-000000000001",
  client: "50000000-0000-0000-0000-000000000009",
  pointer: "60000000-0000-0000-0000-000000000003",
  decision: "70000000-0000-0000-0000-000000000004",
};

// A QUALIFIED actor: one carrying an application session, which is the only kind
// the receipt producer does anything for. With a legacy actor the whole path
// below returns early and this file would assert nothing.
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "dealroom-cookie", client_id: "dealroom-pwa",
  organization_tenant_id: "carr-internal", application_session_id: ids.session,
  sponsoring_human_slug: "joe", personal_scope: "none",
  authorization_class: "verified_partner" };

class DetachFake {
  constructor() { this.queries = []; }
  async query(text, params = []) {
    this.queries.push({ text, params });
    const t = text.replace(/\s+/g, " ").trim();
    // The envelope's replay read: no prior call under this key.
    if (t.startsWith("select request_hash, response, actor_id")) return { rows: [] };
    // resolveSubject, taking the UUID branch -- one query, which is why `from`
    // is passed as a raw id here rather than as a C-nnn ref.
    if (t.startsWith("select subject_type, subject_id from v_ref_index"))
      return { rows: [{ subject_type: "client", subject_id: ids.client }] };
    // The live pointer the verb is about to restate.
    if (t.startsWith("select id, new_value from event"))
      return { rows: [{ id: ids.pointer,
                        new_value: { summary: "signed the LOI", decision_id: ids.decision } }] };
    if (t.startsWith("update event set new_value")) return { rows: [], rowCount: 1 };
    if (t.startsWith("insert into event")) return { rows: [] };
    if (t.startsWith("insert into tool_call")) return { rows: [] };
    // reverseRestatedReceipts' drift query, and the producer's subject query.
    // Both return nothing: this fake is asserting that the QUESTION is asked,
    // not standing in for a database that could answer it.
    if (t.includes("from ops.write_receipt w")) return { rows: [] };
    if (t.startsWith("select distinct subject_type, subject_id from event")) return { rows: [] };
    if (t.startsWith("select pg_advisory_xact_lock")) return { rows: [{}] };
    throw new Error("unhandled fake query: " + t);
  }
}

const driftQueries = fake => fake.queries.filter(
  q => q.text.includes("ops.write_receipt_material_digest") &&
       q.text.includes("ops.receipt_is_disavowed"));

test("detach-decision declares the evidence it restated, so its receipt gets taken back",
  async () => {
    const fake = new DetachFake();
    const result = await TOOLS["detach-decision"].handler(fake, joe, {
      idempotency_key: "11111111-1111-1111-1111-111111111111",
      decision_id: ids.decision,
      from: ids.client,
      reason: "this ruling is about the vendor, not this client",
    });
    assert.equal(result.retracted, true, "the verb did not do its own job");
    assert.equal(result.retained_as_audit_row, true,
      "detach-decision must keep the pointer as an audit row, never delete it");

    const drift = driftQueries(fake);
    assert.equal(drift.length, 1,
      "detach-decision restated a receipted event and the envelope never went " +
      "looking for the receipt resting on it -- under 0238 that transaction is " +
      "refused at COMMIT and the verb is permanently broken for every " +
      "about-attached decision");
    assert.deepEqual(drift[0].params.slice(0, 2), ["client", ids.client],
      "the restatement was declared against the wrong subject, so the receipt " +
      "actually resting on the pointer is never found");
  });

test("a call that restates nothing declares nothing", async () => {
  // THE OTHER HALF, without which the assertion above passes against an envelope
  // that simply asks the drift question on every write. That would be the fix in
  // the wrong place: it would file reversals for drift this call did not cause,
  // and it would put a receipt scan on every qualified write in the system.
  //
  // Re-detaching an ALREADY-RETRACTED pointer is the natural no-op: the handler
  // returns early, having rewritten nothing, so it has nothing to declare.
  const fake = new DetachFake();
  const already = { id: ids.pointer, new_value: {
    summary: "RETRACTED - not about this record (wrong client) - was: signed the LOI",
    decision_id: ids.decision, retracted: true, retracted_reason: "wrong client" } };
  const base = fake.query.bind(fake);
  fake.query = async (text, params = []) => {
    const t = text.replace(/\s+/g, " ").trim();
    if (t.startsWith("select id, new_value from event")) {
      fake.queries.push({ text, params });
      return { rows: [already] };
    }
    if (t.startsWith("update event set new_value"))
      throw new Error("the no-op path rewrote the pointer anyway");
    return base(text, params);
  };
  const result = await TOOLS["detach-decision"].handler(fake, joe, {
    idempotency_key: "22222222-2222-2222-2222-222222222222",
    decision_id: ids.decision,
    from: ids.client,
    reason: "already taken off this record once",
  });
  assert.equal(result.already_retracted, true, "the no-op path did not report itself");
  assert.equal(driftQueries(fake).length, 0,
    "the envelope went looking for receipts to take back after a call that " +
    "restated nothing, so the declaration is not what drives it");
});
