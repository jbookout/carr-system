// V5-A05 -- production door acceptance proofs (migration 0592).
//
// EVERY CASE RUNS THROUGH THE REAL VERB HANDLERS, not through SQL directly:
// the point is to prove the wired path (verb -> SQL function -> signal_event
// -> mint_notification), matching notifications.test.mjs's own rule that a
// proof calling ops.mint_notification directly proves the function rather
// than the product.
//
// These cases skip in the unit class; the migration class supplies
// DATABASE_URL, matching R03-SIGNAL-SURVIVES-MINT-FAILURE's own convention.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { deliveryCadenceA05Tools } from "../src/delivery-cadence-a05-tools.js";

const DSN = process.env.DATABASE_URL || "";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

function harness() {
  const envelopes = new Map();
  const events = [];
  return {
    events,
    withEnvelope: async (c, actor, verb, args, fn) => {
      const key = `${verb}:${args.idempotency_key}`;
      if (envelopes.has(key)) return envelopes.get(key);
      const value = await fn();
      envelopes.set(key, value);
      return value;
    },
    writeEvent: async (c, actor, verb, subjectType, subjectId, fields) => {
      events.push({ verb, subjectType, subjectId, fields });
    },
  };
}

async function skipUnlessDatabase(t) {
  if (!DSN) { t.skip("the migration class supplies DATABASE_URL"); return null; }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof writes cadence receipts and mints notifications against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg, slug = "joe") {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("select set_config('carr.acting_actor_slug',$1,false)", [slug]);
  await client.query("select set_config('carr.verified_human_actor_slug',$1,false)", [slug]);
  return client;
}

const wrap = client => ({ query: (text, values = []) => client.query(text, values) });

async function dispatched(client, fn) {
  await client.query("begin");
  await client.query("set local role carr_writer");
  try {
    const value = await fn();
    await client.query("reset role");
    await client.query("commit");
    return value;
  } catch (error) {
    await client.query("reset role").catch(() => {});
    await client.query("rollback");
    throw error;
  }
}

const PARTNER_ACTOR = id => ({ id, slug: "joe", human: true, via: "oauth-google" });

function a05(harnessed) {
  return deliveryCadenceA05Tools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError,
  });
}

test("V5A05-CADENCE-RECEIPT: record then read status current, then a backdated prior receipt reads missed", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const joe = (await client.query(
    "select id from public.actor where slug='joe' and kind='human' and active")).rows[0];
  assert.ok(joe, "this proof needs the partner actor row db/schema.sql seeds");

  const verbs = a05(harness());
  const subject = { subject_type: "engineering_program", subject_ref: `v5a05-test-${randomUUID()}` };

  // 1. no_receipt_on_record before anything is written.
  const before = await verbs["cadence-status"].handler(wrap(client), PARTNER_ACTOR(joe.id), subject);
  assert.equal(before.status, "no_receipt_on_record");
  assert.equal(before.interval_days, 14);

  // 2. record-cadence-receipt, then current.
  const receiptArgs = { idempotency_key: randomUUID(), ...subject };
  const recorded = await dispatched(client, () =>
    verbs["record-cadence-receipt"].handler(wrap(client), PARTNER_ACTOR(joe.id), receiptArgs));
  assert.equal(recorded.ok, true);
  assert.equal(recorded.deduplicated, false);
  assert.equal(recorded.replan_of, null);

  const dup = await dispatched(client, () =>
    verbs["record-cadence-receipt"].handler(wrap(client), PARTNER_ACTOR(joe.id), receiptArgs));
  assert.equal(dup.deduplicated, true, "the same idempotency_key never inserts twice");
  assert.equal(dup.receipt_id, recorded.receipt_id);

  const current = await verbs["cadence-status"].handler(wrap(client), PARTNER_ACTOR(joe.id), subject);
  assert.equal(current.status, "current");
  assert.equal(current.requires_replan, false);

  // 3. Backdate the receipt directly (test setup only -- production never
  // does this; the write function always uses clock_timestamp()) to prove
  // cadence-status correctly reports a miss once the 14-day window lapses.
  await client.query(
    `update ops.v5_a05_cadence_receipt set issued_at = now() - interval '20 days', expires_at = now() - interval '6 days'
      where id = $1`,
    [recorded.receipt_id]);
  const missed = await verbs["cadence-status"].handler(wrap(client), PARTNER_ACTOR(joe.id), subject);
  assert.equal(missed.status, "missed");
  assert.equal(missed.requires_replan, true);

  // 4. A fresh receipt now records replan_of pointing at the expired one.
  const replanArgs = { idempotency_key: randomUUID(), ...subject };
  const replanned = await dispatched(client, () =>
    verbs["record-cadence-receipt"].handler(wrap(client), PARTNER_ACTOR(joe.id), replanArgs));
  assert.equal(replanned.replan_of, recorded.receipt_id);
});

test("V5A05-ESCALATION-URGENT: raise-delivery-cadence-alert on an urgent reason mints with bypass_quiet_hours", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const joe = (await client.query(
    "select id from public.actor where slug='joe' and kind='human' and active")).rows[0];
  assert.ok(joe);

  const verbs = a05(harness());
  const args = {
    idempotency_key: randomUUID(), reason_id: "security_incident",
    subject_type: "engineering_program", subject_ref: `v5a05-urgent-${randomUUID()}`,
  };
  const result = await dispatched(client, () =>
    verbs["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), args));

  assert.equal(result.ok, true);
  assert.equal(result.routing.severity, "urgent");
  assert.equal(result.routing.routing, "deliver_immediately");
  assert.equal(result.routing.bypasses_quiet_hours, true);
  assert.equal(result.signal.severity, "critical");
  assert.equal(result.notification.minted, true, "an urgent V5-A05 alert must mint a real notification");
  assert.equal(result.notification.bypassed_quiet_hours, true);

  const stored = await client.query(
    "select id from public.signal_event where producer=$1 and signal_key like $2",
    ["v5-a05-delivery-cadence", `v5-a05:security_incident:%`]);
  assert.equal(stored.rows.length >= 1, true, "the signal row is present");
});

test("V5A05-ESCALATION-NO-QUEUE-ENTRY: an ordinary reason with no authority/intent never notifies", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const joe = (await client.query(
    "select id from public.actor where slug='joe' and kind='human' and active")).rows[0];
  assert.ok(joe);

  const verbs = a05(harness());
  const args = {
    idempotency_key: randomUUID(), reason_id: "review_blocker",
    subject_type: "engineering_program", subject_ref: `v5a05-quiet-${randomUUID()}`,
    requires_joe_authority: false, unresolved_intent: false,
  };
  const result = await dispatched(client, () =>
    verbs["raise-delivery-cadence-alert"].handler(wrap(client), PARTNER_ACTOR(joe.id), args));

  assert.equal(result.routing.routing, "no_queue_entry");
  assert.equal(result.routing.wakes_joe, false);
  assert.equal(result.signal.severity, "info");
  assert.equal(result.notification.minted, false);
  assert.equal(result.notification.reason_id, "severity_not_notifiable");
});
