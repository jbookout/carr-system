// WR-000113 — the R03 notification acceptance proofs.
//
// EVERY MINT CASE RUNS THROUGH record-signal, not through SQL. The mint has ONE
// production call site and a proof that calls ops.mint_notification directly
// proves the function rather than the product.
//
// The store cases skip in the unit class; the migration class supplies
// DATABASE_URL and sets CARR_R03_DB_REQUIRED=1, which turns a skip into a
// failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { notificationTools, notificationFeedProjection } from "../src/notifications.js";
import { investigationTools } from "../src/investigation.js";
import { docConversationTools } from "../src/doc-conversation.js";
import { TOOLS } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_R03_DB_REQUIRED === "1";
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
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof mints notifications and runs against a disposable loopback only");
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

/**
 * The dispatcher owns ONE transaction per verb (mcp.js:707 begin, :730 commit),
 * and the savepoint the mint runs inside only exists in a transaction block. A
 * proof that called the handler bare would be proving a shape production never
 * uses, so every handler call here is wrapped the way mcp.js wraps it.
 */
async function dispatched(client, fn) {
  await client.query("begin");
  try {
    const value = await fn();
    await client.query("commit");
    return value;
  } catch (error) {
    await client.query("rollback");
    throw error;
  }
}

/** The partner actor the mint resolves to, and the automation actor that signals. */
async function fixtureActors(client) {
  const joe = (await client.query(
    "select id from public.actor where slug='joe' and kind='human' and active")).rows[0];
  assert.ok(joe, "this proof needs the partner actor row db/schema.sql seeds");
  const producer = (await client.query(
    `insert into public.actor(slug, kind, display_name, active) values ('codex','automation','Codex',true)
       on conflict (slug) do update set active = true returning id`)).rows[0];
  return { joe, producer };
}

const signalArgs = (overrides = {}) => ({
  idempotency_key: randomUUID(),
  producer: "wr113-proof",
  signal_key: `key-${randomUUID()}`,
  signal_kind: "budget_threshold",
  subject_type: "deal",
  subject_ref: "deal-1",
  metric_name: "spend_units",
  observed_value: 120,
  threshold_value: 100,
  comparison: "gt",
  severity: "critical",
  detected_at: "2026-09-17T12:00:00.000Z",
  evidence_refs: ["evidence:one"],
  payload: {},
  ...overrides,
});

/** Joe's own verified session: personalScopeForActor returns personal/joe. */
const PARTNER_ACTOR = id => ({ id, slug: "joe", human: true, via: "oauth-google" });
/** An unsponsored runtime token: personalScopeForActor returns status "none". */
const UNSPONSORED_ACTOR = id => ({ id, slug: "codex", human: false, via: "cli" });

function investigation(harnessed) {
  return investigationTools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError,
  });
}

// ---------------------------------------------------------------------------
// The verb surface.
// ---------------------------------------------------------------------------

test("R03-VERBS-REACHABLE: both verbs are registered with the flags their grants require", () => {
  assert.ok(TOOLS["acknowledge-notification"]);
  assert.ok(TOOLS["notification-feed"]);
  assert.equal(TOOLS["acknowledge-notification"].write, true);
  assert.equal(TOOLS["acknowledge-notification"].authorityOnly, undefined,
    "the acknowledge function is granted to carr_writer; an authorityOnly flag would refuse it");
  assert.equal(TOOLS["notification-feed"].write, undefined);
  assert.equal(TOOLS["notification-feed"].writerConnection, true,
    "the feed resolves the caller's actor from a context only the writer path installs");
  for (const profile of ["capture", "away"]) {
    for (const name of ["acknowledge-notification", "notification-feed"]) {
      assert.ok(!PROFILES[profile].has(name), `${name} must not be in the ${profile} profile`);
    }
  }
  // The criterion's own words are "reachable through /mcp on the deployed
  // Worker". That is a step-twelve fact and this case does not claim it.
});

test("R03-NO-PROGRESS-SPAM: the severity vocabulary has no informational value", () => {
  const signalSeverities = TOOLS["record-signal"].inputSchema.properties.severity.enum;
  assert.deepEqual(signalSeverities, ["info", "warning", "critical"]);
  // ops.notification.severity is action_required | completion | failure — no
  // 'info'. There is no severity a progress ping could be minted under.
  assert.ok(!signalSeverities.includes("action_required"));
});

test("the feed shaper refuses anything that is not the definer function's own shape", () => {
  assert.throws(() => notificationFeedProjection(null, ToolError), /notification_feed_unavailable/);
  assert.throws(() => notificationFeedProjection({ ok: false }, ToolError), /notification_feed_unavailable/);
});

test("R03-RECIPIENT-RESOLVER (JS half): the ninth argument is not reachable from args", () => {
  const schema = TOOLS["record-signal"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  for (const forbidden of ["recipient", "recipient_slug", "sponsor", "dedupe_key", "deep_link"]) {
    assert.ok(!Object.hasOwn(schema.properties, forbidden),
      `${forbidden} is nameable by a caller and must not be`);
  }
});

// ---------------------------------------------------------------------------
// The mint, through record-signal.
// ---------------------------------------------------------------------------

test("R03-DEDUPE: a replayed signal never reaches the genuine-insert branch, so it mints nothing",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    const verbs = investigation(harness());
    const actor = PARTNER_ACTOR(joe.id);
    const args = signalArgs();

    const first = await dispatched(client, () => verbs["record-signal"].handler(c, actor, args));
    assert.equal(first.duplicate, false);
    assert.equal(first.notification.minted, true);

    const second = await dispatched(client, () => verbs["record-signal"].handler(c, actor,
      { ...args, idempotency_key: randomUUID() }));
    assert.equal(second.duplicate, true);
    assert.equal(second.notification, undefined,
      "the duplicate branch returns before the mint and names no notification");

    const counts = (await client.query(
      `select (select count(*) from ops.notification where dedupe_key=$1) items,
              (select count(*) from ops.notification_delivery d
                 join ops.notification n on n.id=d.notification_id
                where n.dedupe_key=$1 and d.channel='in_app') in_app`,
      [`signal:${args.producer}:${args.signal_key}`])).rows[0];
    assert.equal(Number(counts.items), 1);
    assert.equal(Number(counts.in_app), 1);
  });

test("R03-NO-PROGRESS-SPAM: one signal_key mints exactly one item, and an info signal mints none",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    const verbs = investigation(harness());
    const actor = PARTNER_ACTOR(joe.id);

    const progress = signalArgs();
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await dispatched(client, () => verbs["record-signal"].handler(c, actor,
        { ...progress, idempotency_key: randomUUID() }));
    }
    const items = Number((await client.query(
      "select count(*) from ops.notification where dedupe_key=$1",
      [`signal:${progress.producer}:${progress.signal_key}`])).rows[0].count);
    assert.equal(items, 1, "a run reporting progress under one key produces exactly one item");

    const info = signalArgs({ severity: "info" });
    const recorded = await dispatched(client, () => verbs["record-signal"].handler(c, actor, info));
    assert.equal(recorded.duplicate, false);
    assert.equal(recorded.notification.minted, false);
    assert.equal(recorded.notification.reason_id, "severity_not_notifiable");
    assert.equal(Number((await client.query(
      "select count(*) from ops.notification where dedupe_key=$1",
      [`signal:${info.producer}:${info.signal_key}`])).rows[0].count), 0);

    // And the column check refuses an 'info' notification outright.
    await assert.rejects(client.query(
      `insert into ops.notification(subject_type,subject_ref,event_ref,event_source,recipient_actor,
         reason,severity,deep_link,dedupe_key)
       values ('deal','d',gen_random_uuid(),'signal_event',$1,'r','info','/x','k')`, [joe.id]),
      /violates check constraint|permission denied/);
  });

test("R03-NO-SPONSOR: no sponsor, no notification, and the signal is still recorded", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const c = wrap(client);
  const { producer } = await fixtureActors(client);
  const verbs = investigation(harness());
  const args = signalArgs();

  const result = await dispatched(client, () =>
    verbs["record-signal"].handler(c, UNSPONSORED_ACTOR(producer.id), args));
  assert.equal(result.ok, true);
  assert.equal(result.duplicate, false);
  assert.equal(result.notification.minted, false);
  assert.equal(result.notification.reason_id, "no_sponsoring_partner");

  // ASSERT THE ROW, not only the return value.
  const stored = await client.query(
    "select id from public.signal_event where producer=$1 and signal_key=$2",
    [args.producer, args.signal_key]);
  assert.equal(stored.rows.length, 1, "the signal row is present");
  assert.equal(Number((await client.query(
    "select count(*) from ops.notification where dedupe_key=$1",
    [`signal:${args.producer}:${args.signal_key}`])).rows[0].count), 0);
});

test("R03-SIGNAL-SURVIVES-MINT-FAILURE: a mint that cannot mint leaves the signal committed",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const owner = await connect(pg);
    t.after(() => owner.end().catch(() => {}));
    const { joe } = await fixtureActors(owner);
    const verbs = investigation(harness());
    const args = signalArgs();

    // The cheapest honest way to make the mint raise at the REAL call site: take
    // the grant away for the duration of this case, which produces a real 42501.
    await owner.query(
      "revoke execute on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text) from carr_writer");
    try {
      await owner.query("begin");
      await owner.query("set local role carr_writer");
      const result = await verbs["record-signal"].handler(wrap(owner), PARTNER_ACTOR(joe.id), args);
      await owner.query("reset role");
      await owner.query("commit");

      assert.equal(result.ok, true, "the verb still returns ok");
      assert.equal(result.notification.minted, false);
      assert.equal(result.notification.reason_id, "notification_mint_unavailable",
        "the refusal is named rather than swallowed");
    } finally {
      await owner.query("reset role").catch(() => {});
      await owner.query("rollback").catch(() => {});
      await owner.query(
        "grant execute on function ops.mint_notification(text,uuid,text,text,text,text,text,text,text) to carr_writer");
    }

    // THE SIGNAL ROW IS COMMITTED. This is the whole clause: the notification is
    // a courtesy and the signal is the product.
    const stored = await owner.query(
      "select id from public.signal_event where producer=$1 and signal_key=$2",
      [args.producer, args.signal_key]);
    assert.equal(stored.rows.length, 1);
  });

test("R03-QUIET-HOURS: suppressed is RECORDED, and an opt-out writes no device row at all",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg);
    t.after(() => client.end().catch(() => {}));
    const c = wrap(client);
    const { joe } = await fixtureActors(client);
    const verbs = investigation(harness());
    const actor = PARTNER_ACTOR(joe.id);
    const deliveries = async key => (await client.query(
      `select d.channel, d.state from ops.notification_delivery d
         join ops.notification n on n.id = d.notification_id
        where n.dedupe_key = $1 order by d.channel`, [key])).rows;

    // The DEFAULT, on a preference row nobody wrote: no device row at all.
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
    const off = signalArgs();
    await dispatched(client, () => verbs["record-signal"].handler(c, actor, off));
    assert.deepEqual(await deliveries(`signal:${off.producer}:${off.signal_key}`),
      [{ channel: "in_app", state: "pending" }]);

    // Opted in, inside a window that covers the whole day.
    await client.query(
      `insert into ops.notification_preference(actor, device_opt_in, quiet_hours_start, quiet_hours_end, timezone)
         values ($1,true,'00:00','23:59','UTC')
       on conflict (actor) do update set device_opt_in = true,
         quiet_hours_start = '00:00', quiet_hours_end = '23:59', timezone = 'UTC'`, [joe.id]);
    const quiet = signalArgs();
    await dispatched(client, () => verbs["record-signal"].handler(c, actor, quiet));
    assert.deepEqual(await deliveries(`signal:${quiet.producer}:${quiet.signal_key}`),
      [{ channel: "device", state: "suppressed_quiet_hours" },
       { channel: "in_app", state: "pending" }]);

    // Opted in, with no window at all.
    await client.query(
      `update ops.notification_preference set quiet_hours_start = null, quiet_hours_end = null
        where actor = $1`, [joe.id]);
    const loud = signalArgs();
    await dispatched(client, () => verbs["record-signal"].handler(c, actor, loud));
    assert.deepEqual(await deliveries(`signal:${loud.producer}:${loud.signal_key}`),
      [{ channel: "device", state: "pending" }, { channel: "in_app", state: "pending" }]);
    await client.query("delete from ops.notification_preference where actor=$1", [joe.id]);
  });

test("R03-STATUS-DISTINCT: acknowledging moves a read receipt and nothing else", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const c = wrap(client);
  const { joe } = await fixtureActors(client);
  const harnessed = harness();
  const verbs = investigation(harnessed);
  const notifications = notificationTools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError });
  const actor = PARTNER_ACTOR(joe.id);
  const args = signalArgs();
  const minted = await dispatched(client, () => verbs["record-signal"].handler(c, actor, args));
  const notificationId = minted.notification.notification_id;

  const before = (await client.query(
    "select to_jsonb(n) as row from ops.notification n where id=$1", [notificationId])).rows[0].row;
  const task = (await client.query(
    "select to_jsonb(s) as row from public.signal_event s where id=$1", [minted.signal.id])).rows[0].row;

  const acknowledged = await dispatched(client, () =>
    notifications["acknowledge-notification"].handler(c, actor,
      { idempotency_key: randomUUID(), notification_id: notificationId }));
  assert.equal(acknowledged.ok, true);
  assert.equal(acknowledged.deduplicated, false);

  const after = (await client.query(
    "select to_jsonb(n) as row from ops.notification n where id=$1", [notificationId])).rows[0].row;
  const taskAfter = (await client.query(
    "select to_jsonb(s) as row from public.signal_event s where id=$1", [minted.signal.id])).rows[0].row;
  assert.deepEqual(after, before, "ops.notification is byte-identical");
  assert.deepEqual(taskAfter, task, "the subject task row is byte-identical");
  assert.equal(taskAfter.status, "open", "the source task is still open");

  // Enforced by the ABSENCE of a grant, not by discipline.
  const privilege = (await client.query(
    `select has_table_privilege('carr_writer','ops.notification','update') as writer_update,
            has_table_privilege('carr_authority','ops.notification','update') as authority_update`)).rows[0];
  assert.deepEqual(privilege, { writer_update: false, authority_update: false });

  // The acknowledgement is idempotent.
  const again = await dispatched(client, () =>
    notifications["acknowledge-notification"].handler(c, actor,
      { idempotency_key: randomUUID(), notification_id: notificationId }));
  assert.equal(again.deduplicated, true);
  assert.equal(Number((await client.query(
    "select count(*) from ops.notification_read where notification_id=$1", [notificationId])).rows[0].count), 1);
});

test("R03-DEEP-LINK-RECHECKS: the item survives a revocation and the access does not", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const c = wrap(client);
  const { joe, producer } = await fixtureActors(client);
  const harnessed = harness();
  const verbs = investigation(harnessed);
  const notifications = notificationTools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError });
  const conversations = docConversationTools({
    withEnvelope: harnessed.withEnvelope, writeEvent: harnessed.writeEvent, ToolError });

  // 1. A conversation the recipient is on, and a signal whose subject is it.
  const conversation = (await client.query(
    "insert into ops.doc_conversation(title, created_by_actor) values ('follow-time proof',$1) returning id",
    [producer.id])).rows[0];
  await client.query(
    "insert into ops.doc_conversation_grant(conversation_id, grantee_actor, granted_by_actor) values ($1,$2,$3)",
    [conversation.id, joe.id, producer.id]);

  const args = signalArgs({ subject_type: "doc_conversation", subject_ref: conversation.id });
  const minted = await dispatched(client, () =>
    verbs["record-signal"].handler(c, PARTNER_ACTOR(joe.id), args));
  assert.equal(minted.notification.minted, true);
  const stored = (await client.query(
    "select deep_link from ops.notification where id=$1", [minted.notification.notification_id])).rows[0];
  assert.equal(stored.deep_link, `/doc-conversations/${conversation.id}`);
  // A RELATIVE PATH ONLY: no scheme, no host, no query, no token.
  assert.match(stored.deep_link, /^\/([A-Za-z0-9._~-][A-Za-z0-9._~/-]*)?$/);
  assert.ok(stored.deep_link.length <= 301);

  // 2. Following it as the recipient returns the conversation.
  const granted = await conversations["read-doc-conversation"].handler(c, { id: joe.id },
    { conversation_id: conversation.id });
  assert.equal(granted.identity.id, conversation.id);

  // 3. A REAL revocation.
  await client.query(
    "update ops.doc_conversation_grant set revoked_at = now() where conversation_id=$1 and grantee_actor=$2",
    [conversation.id, joe.id]);

  // 4. Following it again is refused, and the count is gone too.
  await assert.rejects(
    conversations["read-doc-conversation"].handler(c, { id: joe.id }, { conversation_id: conversation.id }),
    error => error.error === "doc_conversation_not_found");

  // 5. The notification and its feed entry are UNCHANGED. That is what makes it
  //    a follow-time check rather than a mint-time one: the item stays, the
  //    access does not, and the answer changes only when the link is followed.
  const feed = await notifications["notification-feed"].handler(c, { id: joe.id }, {});
  const entry = feed.notifications.find(row => row.id === minted.notification.notification_id);
  assert.ok(entry, "the notification is still in the recipient's feed");
  assert.equal(entry.deep_link, `/doc-conversations/${conversation.id}`);

  // The named limit: no worker route serves a conversation, so "following" is
  // modelled as resolving the stored path to the read verb above. An HTTP
  // surface for it is a later request.
});

test("R03-DEDUPE: the dedupe key is built from the STORED row and nothing else", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const c = wrap(client);
  const { joe } = await fixtureActors(client);
  const verbs = investigation(harness());
  const args = signalArgs();
  const minted = await dispatched(client, () =>
    verbs["record-signal"].handler(c, PARTNER_ACTOR(joe.id), args));
  const row = (await client.query(
    "select dedupe_key, event_source, event_ref, severity, subject_type, subject_ref from ops.notification where id=$1",
    [minted.notification.notification_id])).rows[0];
  assert.equal(row.dedupe_key, `signal:${args.producer}:${args.signal_key}`);
  assert.equal(row.event_source, "signal_event");
  assert.equal(row.event_ref, minted.signal.id, "the mint names the row record-signal just inserted");
  assert.equal(row.severity, "failure", "critical maps to failure");
  assert.equal(row.subject_type, args.subject_type);
  assert.equal(row.subject_ref, args.subject_ref);
});

test("R03-DEDUPE: a warning maps to action_required", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg);
  t.after(() => client.end().catch(() => {}));
  const c = wrap(client);
  const { joe } = await fixtureActors(client);
  const verbs = investigation(harness());
  const args = signalArgs({ severity: "warning" });
  const minted = await dispatched(client, () =>
    verbs["record-signal"].handler(c, PARTNER_ACTOR(joe.id), args));
  const row = (await client.query(
    "select severity from ops.notification where id=$1", [minted.notification.notification_id])).rows[0];
  assert.equal(row.severity, "action_required");
});
