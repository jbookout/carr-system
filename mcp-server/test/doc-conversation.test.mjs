// WR-000112 — the Doc conversation acceptance proofs.
//
// The verb-surface cases are pure. The store cases need a real PostgreSQL and
// skip in the unit class; the migration class supplies DATABASE_URL and sets
// CARR_DOC_CONVERSATION_DB_REQUIRED=1, which turns a silent skip into a failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { docConversationTools, docConversationProjection } from "../src/doc-conversation.js";
import { TOOLS } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_DOC_CONVERSATION_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

/**
 * A replay cache with the ONE property this proof leans on: the same
 * idempotency key returns the recorded answer and does not run the body twice.
 * Every assertion that matters counts stored ROWS, never this cache.
 */
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
    "REFUSED: this proof writes conversation rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg, slug) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  if (slug) {
    await client.query("select set_config('carr.acting_actor_slug',$1,false)", [slug]);
    await client.query("select set_config('carr.verified_human_actor_slug',$1,false)", [slug]);
  }
  return client;
}

/** Two automation actors, minted here so the proof owns its own fixtures. */
async function actors(client) {
  const rows = [];
  for (const slug of ["wr112-author", "wr112-guest"]) {
    const r = await client.query(
      `insert into public.actor(slug, kind, display_name, active) values ($1,'automation',$1,true)
         on conflict (slug) do update set active = true returning id, slug`, [slug]);
    rows.push(r.rows[0]);
  }
  return { author: rows[0], guest: rows[1] };
}

async function newConversation(client, ownerId, title = "WR-000112 proof") {
  const r = await client.query(
    "insert into ops.doc_conversation(title, created_by_actor) values ($1,$2) returning id, version",
    [title, ownerId]);
  return r.rows[0];
}

// ---------------------------------------------------------------------------
// The verb surface.
// ---------------------------------------------------------------------------

test("DOC-VERB-REACHABLE: both verbs are registered, and neither widens an unattended profile", () => {
  assert.ok(TOOLS["add-doc-conversation-turn"], "add-doc-conversation-turn is not registered");
  assert.ok(TOOLS["read-doc-conversation"], "read-doc-conversation is not registered");
  assert.equal(TOOLS["add-doc-conversation-turn"].write, true);
  assert.equal(TOOLS["add-doc-conversation-turn"].authorityOnly, true,
    "the append function is granted to carr_authority, so the verb must declare authorityOnly");
  assert.equal(TOOLS["read-doc-conversation"].write, undefined);
  assert.equal(TOOLS["read-doc-conversation"].writerConnection, true,
    "ops.doc_conversation_facts is granted to carr_writer, and only the writer path installs the actor context");
  // Negatively asserted: widening an unattended profile is a separate ruling.
  for (const profile of ["capture", "away"]) {
    for (const name of ["add-doc-conversation-turn", "read-doc-conversation"]) {
      assert.ok(!PROFILES[profile].has(name),
        `${name} must not be in the ${profile} profile`);
    }
  }
});

test("DOC-ATTRIBUTION-SERVER-SIDE: the schema refuses a caller-named origin", () => {
  const schema = TOOLS["add-doc-conversation-turn"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  for (const forbidden of ["origin_actor", "origin_channel", "sequence", "at"]) {
    assert.ok(!Object.hasOwn(schema.properties, forbidden),
      `${forbidden} is nameable by a caller`);
  }
  assert.deepEqual(Object.keys(schema.properties).sort(),
    ["body", "conversation_id", "idempotency_key", "msg_id", "role"]);
});

test("the read shaper refuses anything that is not the definer function's own shape", () => {
  assert.throws(() => docConversationProjection(null, ToolError), /doc_conversation_not_found/);
  assert.throws(() => docConversationProjection({ ok: false }, ToolError), /doc_conversation_not_found/);
});

// ---------------------------------------------------------------------------
// The store.
// ---------------------------------------------------------------------------

test("DOC-TURN-READBACK: three turns read back in sequence order, with honest paging", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author } = await actors(client);
  const conversation = await newConversation(client, author.id);
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

  const bodies = ["first turn", "second turn", "third turn"];
  for (const body of bodies) {
    await verbs["add-doc-conversation-turn"].handler(c, { id: author.id, slug: "joe" }, {
      idempotency_key: randomUUID(), conversation_id: conversation.id, role: "human", body });
  }

  const read = await verbs["read-doc-conversation"].handler(c, { id: author.id }, {
    conversation_id: conversation.id });
  assert.deepEqual(read.turns.map(turn => turn.body), bodies, "bodies are verbatim and in order");
  assert.deepEqual(read.turns.map(turn => turn.sequence), [0, 1, 2]);
  assert.equal(read.latest_sequence, 2);
  assert.equal(read.more, false);

  const tail = await verbs["read-doc-conversation"].handler(c, { id: author.id }, {
    conversation_id: conversation.id, after_sequence: 2 });
  assert.deepEqual(tail.turns.map(turn => turn.body), ["third turn"]);
  assert.equal(tail.more, false);
  const page = await verbs["read-doc-conversation"].handler(c, { id: author.id }, {
    conversation_id: conversation.id, limit: 2 });
  assert.equal(page.turns.length, 2);
  assert.equal(page.more, true, "more is honest when a page was cut short");
});

test("DOC-IDEMPOTENT: three counted facts, and the count is the assertion", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author } = await actors(client);
  const conversation = await newConversation(client, author.id);
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });
  const actor = { id: author.id, slug: "joe" };
  const count = async () => Number((await client.query(
    "select count(*) from ops.doc_conversation_turn where conversation_id=$1",
    [conversation.id])).rows[0].count);

  // (a) the SAME request twice under ONE idempotency key.
  const key = randomUUID();
  const msgId = randomUUID();
  const args = { idempotency_key: key, conversation_id: conversation.id, role: "human",
    body: "one and only", msg_id: msgId };
  await verbs["add-doc-conversation-turn"].handler(c, actor, args);
  await verbs["add-doc-conversation-turn"].handler(c, actor, args);
  assert.equal(await count(), 1, "one key, one stored turn");

  // (b) the SAME msg_id under a FRESH key -- a genuinely separate transport, so
  //     the envelope cannot be what absorbs it.
  const again = await verbs["add-doc-conversation-turn"].handler(c, actor,
    { ...args, idempotency_key: randomUUID() });
  assert.equal(again.deduplicated, true);
  assert.equal(await count(), 1, "a fresh key over the same msg_id still stores one turn");
  const latest = await verbs["read-doc-conversation"].handler(c, { id: author.id },
    { conversation_id: conversation.id });
  assert.equal(latest.latest_sequence, 0, "latest_sequence did not move");

  // (c) that msg_id with a DIFFERENT body is a REUSE, not a duplicate.
  await assert.rejects(
    verbs["add-doc-conversation-turn"].handler(c, actor,
      { ...args, idempotency_key: randomUUID(), body: "a different body" }),
    error => error.error === "doc_conversation_msg_id_reuse");
  assert.equal(await count(), 1);
});

test("DOC-ATTRIBUTION-SERVER-SIDE: origin_actor is the session's derived actor, not any argument",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "joe");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author } = await actors(client);
    const conversation = await newConversation(client, author.id);
    const { withEnvelope, writeEvent } = harness();
    const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

    // The definer function called DIRECTLY, with no actor argument to give it.
    const direct = (await client.query(
      "select ops.append_doc_conversation_turn($1::uuid,'system','direct call',$2::uuid,$3::uuid) as appended",
      [conversation.id, randomUUID(), randomUUID()])).rows[0].appended;
    assert.equal(direct.ok, true);
    assert.equal(direct.origin_actor, "joe",
      "origin_actor is derived from the session, and there is no parameter for it");
    assert.equal(direct.origin_channel, "mcp");

    // And through the verb, whose schema cannot even name one.
    await verbs["add-doc-conversation-turn"].handler(c, { id: author.id, slug: "joe" }, {
      idempotency_key: randomUUID(), conversation_id: conversation.id,
      role: "assistant", body: "through the verb" });
    const rows = await client.query(
      "select distinct origin_actor, origin_channel from ops.doc_conversation_turn where conversation_id=$1",
      [conversation.id]);
    assert.deepEqual(rows.rows, [{ origin_actor: "joe", origin_channel: "mcp" }]);
  });

test("DOC-PRIVATE-INVISIBLE: a non-member gets not_found AND the count does not include it",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "joe");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author, guest } = await actors(client);
    const conversation = await newConversation(client, author.id, "private to the author");
    const { withEnvelope, writeEvent } = harness();
    const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

    const ownerView = await verbs["read-doc-conversation"].handler(c, { id: author.id },
      { conversation_id: conversation.id });
    assert.equal(ownerView.identity.visibility, "private");
    const ownerCount = ownerView.visible_conversation_count;

    await assert.rejects(
      verbs["read-doc-conversation"].handler(c, { id: guest.id }, { conversation_id: conversation.id }),
      error => error.error === "doc_conversation_not_found");

    // The COUNT, not only the list: an unauthorized caller must not learn a
    // private conversation exists from a number.
    const guestFacts = (await client.query(
      "select ops.doc_conversation_facts($1::uuid,$2::text,0,200) as facts",
      [conversation.id, guest.id])).rows[0].facts;
    assert.equal(guestFacts.ok, false);
    assert.equal(guestFacts.reason_id, "doc_conversation_not_found");
    assert.equal(guestFacts.visible_conversation_count, undefined,
      "a refusal returns no count at all");
    assert.ok(ownerCount >= 1);
  });

test("DOC-RENAME-RETAINS: the title moves and every turn is byte-identical", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author } = await actors(client);
  const conversation = await newConversation(client, author.id, "before the rename");
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });
  for (const body of ["turn one", "turn two"]) {
    await verbs["add-doc-conversation-turn"].handler(c, { id: author.id, slug: "joe" },
      { idempotency_key: randomUUID(), conversation_id: conversation.id, role: "human", body });
  }
  const before = (await client.query(
    "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
    [conversation.id])).rows;

  await client.query(
    `insert into ops.doc_conversation_title_revision(conversation_id, title, by_actor)
       select id, title, created_by_actor from ops.doc_conversation where id = $1`, [conversation.id]);
  await client.query(
    "update ops.doc_conversation set title = $2, version = version + 1, updated_at = now() where id = $1",
    [conversation.id, "after the rename"]);

  const after = await verbs["read-doc-conversation"].handler(c, { id: author.id },
    { conversation_id: conversation.id });
  assert.equal(after.identity.title, "after the rename");
  assert.equal(after.identity.version, conversation.version + 1);
  const revisions = await client.query(
    "select title from ops.doc_conversation_title_revision where conversation_id=$1", [conversation.id]);
  assert.deepEqual(revisions.rows.map(row => row.title), ["before the rename"]);
  const now = (await client.query(
    "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
    [conversation.id])).rows;
  assert.deepEqual(now, before, "every turn's sequence, msg_id and body is byte-identical");
});

test("DOC-PRIVATE-INVISIBLE: a granted actor sees it, and a REVOKED grant takes it away", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author, guest } = await actors(client);
  const conversation = await newConversation(client, author.id, "shared then revoked");
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

  await client.query(
    `insert into ops.doc_conversation_grant(conversation_id, grantee_actor, granted_by_actor)
       values ($1,$2,$3)`, [conversation.id, guest.id, author.id]);
  const granted = await verbs["read-doc-conversation"].handler(c, { id: guest.id },
    { conversation_id: conversation.id });
  assert.equal(granted.identity.id, conversation.id);
  assert.equal(granted.effective_grants.length, 1);

  await client.query(
    "update ops.doc_conversation_grant set revoked_at = now() where conversation_id=$1 and grantee_actor=$2",
    [conversation.id, guest.id]);
  await assert.rejects(
    verbs["read-doc-conversation"].handler(c, { id: guest.id }, { conversation_id: conversation.id }),
    error => error.error === "doc_conversation_not_found");
});

// ---------------------------------------------------------------------------
// WR-000114 — the three write doors.
// ---------------------------------------------------------------------------

const WRITE_DOORS = ["create-doc-conversation", "share-doc-conversation",
  "rename-doc-conversation"];

test("DOC-APP-CALLABLE: none of the three is authorityOnly, and each is a write on the writer connection",
  () => {
    for (const name of WRITE_DOORS) {
      assert.ok(TOOLS[name], `${name} is not registered`);
      assert.equal(TOOLS[name].write, true, `${name} must declare write`);
      assert.equal(TOOLS[name].writerConnection, true,
        `${name} reaches its function on carr_writer, which is the writer connection`);
      // mcp.js:616-618 refuses an authority-only call before any grant is
      // consulted, and the app holds no authority binding. Copying the
      // append's authorityOnly would fail HERE rather than at release time.
      assert.equal(TOOLS[name].authorityOnly, undefined,
        `${name} must not be authorityOnly: the app calls it as the signed-in partner`);
    }
    // Widening an unattended profile is a separate ruling.
    for (const profile of ["capture", "away"]) {
      for (const name of WRITE_DOORS) {
        assert.ok(!PROFILES[profile].has(name), `${name} must not be in the ${profile} profile`);
      }
    }
  });

test("DOC-ATTRIBUTION-SERVER-SIDE: no write-door schema can name an actor, a grantor or a moment",
  () => {
    for (const name of WRITE_DOORS) {
      const schema = TOOLS[name].inputSchema;
      assert.equal(schema.additionalProperties, false, `${name} accepts undeclared arguments`);
      for (const forbidden of ["created_by", "creator", "granted_by", "granted_by_actor",
        "grantee_actor", "actor", "acting_actor", "acting_actor_slug", "at", "granted_at",
        "created_at", "updated_at", "version"]) {
        assert.ok(!Object.hasOwn(schema.properties, forbidden),
          `${name} lets a caller name ${forbidden}`);
      }
    }
    // base_version is the caller's OWN read-back version, which is the one
    // version-shaped argument a compare-and-swap requires.
    assert.deepEqual(Object.keys(TOOLS["create-doc-conversation"].inputSchema.properties).sort(),
      ["idempotency_key", "title", "visibility"]);
    assert.deepEqual(Object.keys(TOOLS["share-doc-conversation"].inputSchema.properties).sort(),
      ["conversation_id", "granted", "grantee_slug", "idempotency_key"]);
    assert.deepEqual(Object.keys(TOOLS["rename-doc-conversation"].inputSchema.properties).sort(),
      ["archived", "base_version", "conversation_id", "idempotency_key", "pinned", "title"]);
  });

test("DOC-CREATE-READBACK: a created conversation reads back byte-identical, and a replayed key is one row",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "wr112-author");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author } = await actors(client);
    const verbs = docConversationTools({ ...harness(), ToolError });
    const actor = { id: author.id, slug: "wr112-author" };

    // Punctuation and a non-ASCII character, so a shaper that normalised or
    // trimmed would be caught by an EQUALITY assertion rather than a substring.
    const title = "Q4 review — Dr. Reyes' suite, 1,200 sq ft";
    const key = randomUUID();
    const created = await verbs["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title, visibility: "private" });
    assert.equal(created.ok, true);
    assert.equal(created.deduplicated, false);
    assert.equal(created.conversation_id, key, "the row id IS the idempotency key");

    const read = await verbs["read-doc-conversation"].handler(c, actor,
      { conversation_id: key });
    assert.equal(read.identity.title, title, "the title reads back byte-identical");
    assert.equal(read.identity.visibility, "private");
    assert.equal(read.identity.created_by, author.id,
      "the creator is the derived actor, not anything a caller could name");

    // THE ENVELOPE IS DELIBERATELY BYPASSED: a FRESH harness, so the store path
    // really runs a second time. An implementation that minted a fresh uuid per
    // call and leaned on the envelope for idempotency stores a SECOND row here.
    const fresh = docConversationTools({ ...harness(), ToolError });
    const replay = await fresh["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title, visibility: "private" });
    assert.equal(replay.deduplicated, true, "the replay reached the store and deduplicated there");
    assert.equal(replay.conversation_id, key, "the replay returned the same identifier");
    const rows = Number((await client.query(
      "select count(*) from ops.doc_conversation where id = $1", [key])).rows[0].count);
    assert.equal(rows, 1, "one replayed key, one STORED row");
  });

test("DOC-SHARE-TOGGLE: a grant appears in the grantee's read AND count, a revoke removes it from both, and a non-creator cannot do either",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "wr112-author");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author, guest } = await actors(client);
    const verbs = docConversationTools({ ...harness(), ToolError });
    const actor = { id: author.id, slug: "wr112-author" };
    const key = randomUUID();
    await verbs["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title: "shared then withdrawn" });

    // The grantee's OWN conversation, so the guest always has one to count
    // against -- the count assertion must not depend on the shared row alone.
    const guestOwn = randomUUID();
    await client.query(
      "insert into ops.doc_conversation(id, title, created_by_actor) values ($1,$2,$3)",
      [guestOwn, "the guest's own", guest.id]);
    const guestCount = async () => (await verbs["read-doc-conversation"].handler(
      c, { id: guest.id }, { conversation_id: guestOwn })).visible_conversation_count;
    const before = await guestCount();

    await verbs["share-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        grantee_slug: "wr112-guest", granted: true });
    const granted = await verbs["read-doc-conversation"].handler(c, { id: guest.id },
      { conversation_id: key });
    assert.equal(granted.identity.id, key, "the grantee's read returns it");
    assert.equal(await guestCount(), before + 1,
      "the COUNT rose by exactly one -- a count computed outside the access-list join would not");

    await verbs["share-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        grantee_slug: "wr112-guest", granted: false });
    await assert.rejects(
      verbs["read-doc-conversation"].handler(c, { id: guest.id }, { conversation_id: key }),
      error => error.error === "doc_conversation_not_found");
    assert.equal(await guestCount(), before, "and the count dropped back");

    // THE ROW SURVIVES THE REVOKE, stamped rather than deleted, with its
    // grantor and its moment intact. Deletion could not assert this at all.
    const stamped = (await client.query(
      `select granted_by_actor, granted_at, revoked_at from ops.doc_conversation_grant
        where conversation_id = $1 and grantee_actor = $2`, [key, guest.id])).rows;
    assert.equal(stamped.length, 1, "the revoke stamped the row rather than deleting it");
    assert.equal(stamped[0].granted_by_actor, author.id, "the grantor survives for the audit");
    assert.ok(stamped[0].revoked_at instanceof Date, "and the withdrawal is stamped");

    // A NON-CREATOR is refused, for the grant and for the withdrawal alike.
    const asGuest = await connect(pg, "wr112-guest");
    t.after(() => asGuest.end().catch(() => {}));
    const g = { query: (text, values = []) => asGuest.query(text, values) };
    await verbs["share-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        grantee_slug: "wr112-guest", granted: true });
    for (const granting of [true, false]) {
      await assert.rejects(
        verbs["share-doc-conversation"].handler(g, { id: guest.id, slug: "wr112-guest" },
          { idempotency_key: randomUUID(), conversation_id: key,
            grantee_slug: "wr112-guest", granted: granting }),
        error => error.error === "doc_conversation_creator_only",
        `a non-creator ${granting ? "grant" : "revoke"} must be refused by name`);
    }

    // A GRANT WIDENS NOTHING BUT THIS CONVERSATION, asserted NEGATIVELY against
    // the functions' own statements: there is no join from the grant table to
    // any source record. public.actor is the one non-conversation relation
    // either function names, and it is a slug lookup, not a record.
    const bodies = (await client.query(
      `select pg_get_functiondef(p.oid) body from pg_proc p join pg_namespace n
         on n.oid = p.pronamespace where n.nspname='ops'
        and p.proname in ('share_doc_conversation','rename_doc_conversation')`)).rows;
    assert.equal(bodies.length, 2);
    for (const { body } of bodies) {
      // The CREATE header names the function itself; the BODY is what reaches
      // relations, so the header line is dropped before the scan.
      const statements = body.slice(body.indexOf("$function$"));
      for (const match of statements.matchAll(/\bops\.([a-z_]+)/g)) {
        assert.ok(["doc_conversation", "doc_conversation_grant", "doc_conversation_turn",
          "doc_conversation_title_revision", "portfolio_writer_actor_id"].includes(match[1]),
          `a write door reaches ops.${match[1]}, outside the Doc conversation store`);
      }
      for (const match of statements.matchAll(/\bpublic\.([a-z_]+)/g)) {
        assert.equal(match[1], "actor",
          `a write door reaches public.${match[1]}; only the actor slug lookup is allowed`);
      }
    }
  });

test("DOC-RENAME-RETAINS: the title moves, the prior title is retained, every turn is byte-identical, and a stale version is refused",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "wr112-author");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author } = await actors(client);
    const verbs = docConversationTools({ ...harness(), ToolError });
    const actor = { id: author.id, slug: "wr112-author" };
    const key = randomUUID();
    const created = await verbs["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title: "before the rename" });

    for (const body of ["turn one", "turn two"]) {
      await client.query(
        `insert into ops.doc_conversation_turn(conversation_id, sequence, role, body, msg_id, origin_actor)
         select $1, coalesce((select max(sequence)+1 from ops.doc_conversation_turn where conversation_id=$1),0),
                'human', $2, $3, 'wr112-author'`, [key, body, randomUUID()]);
    }
    const before = (await client.query(
      "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
      [key])).rows;

    const renamed = await verbs["rename-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        base_version: created.version, title: "after the rename" });
    assert.equal(renamed.title, "after the rename");
    assert.equal(renamed.version, created.version + 1, "the version rose by exactly one");

    const revisions = (await client.query(
      "select title from ops.doc_conversation_title_revision where conversation_id=$1", [key])).rows;
    assert.deepEqual(revisions.map(row => row.title), ["before the rename"],
      "exactly one revision, carrying the PRIOR title");
    const after = (await client.query(
      "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
      [key])).rows;
    assert.deepEqual(after, before, "every turn's order, identifier and body is byte-identical");

    // THE NOW-STALE base version. The refusal must append NOTHING: the revision
    // table is immutable, so an orphan could never be cleaned up.
    await assert.rejects(
      verbs["rename-doc-conversation"].handler(c, actor,
        { idempotency_key: randomUUID(), conversation_id: key,
          base_version: created.version, title: "a third title" }),
      error => error.error === "version_conflict");
    const stillOne = (await client.query(
      "select count(*) from ops.doc_conversation_title_revision where conversation_id=$1",
      [key])).rows[0].count;
    assert.equal(Number(stillOne), 1,
      "a REFUSED rename appended no revision -- the step-2 version guard holds");

    // Pin and archive move only their own field, under the same compare-and-swap.
    const pinned = await verbs["rename-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        base_version: renamed.version, pinned: true });
    assert.equal(pinned.pinned, true);
    assert.equal(pinned.archived, false, "pinning did not move the archive field");
    assert.equal(pinned.title, "after the rename", "pinning did not move the title");
    const archived = await verbs["rename-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        base_version: pinned.version, archived: true });
    assert.equal(archived.archived, true);
    assert.equal(archived.pinned, true, "archiving did not clear the pin");
    assert.equal(Number((await client.query(
      "select count(*) from ops.doc_conversation_title_revision where conversation_id=$1",
      [key])).rows[0].count), 1, "pin and archive appended no title revision");
  });
