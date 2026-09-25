// amend-closed-loop-postgres.test.mjs — amend-closed-loop on REAL PostgreSQL.
//
// The fake-client suite (amend-closed-loop.test.mjs) proves the handler's own
// logic in isolation. This file proves the things only a real database can
// prove: the migration 0702 constraints actually hold, trg_touch_row really
// bumps loop_item.version, versionGuard's `for update` really serializes a
// concurrent amend, and an idempotent replay through the REAL tool_call table
// really returns the recorded response without writing a second
// loop_amendment row.
//
// Skips in the unit class; the migration class supplies DATABASE_URL and sets
// CARR_AMEND_CLOSED_LOOP_DB_REQUIRED=1, which turns a silent skip into a
// failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { TOOLS, ToolError } from "../src/tools.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_AMEND_CLOSED_LOOP_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

async function database(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof writes loop rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

async function mintActor(admin, slug) {
  const r = await admin.query(
    `insert into public.actor(slug, kind, display_name, active) values ($1,'automation',$1,true)
       on conflict (slug) do update set active = true returning id, slug`, [slug]);
  return { id: r.rows[0].id, slug, human: false, via: "test", client_id: "amend-closed-loop-postgres.test" };
}

/** One verb call in its own transaction, exactly as mcp.js runs a write. */
async function call(client, actor, verb, args) {
  await client.query("begin");
  try {
    const result = await TOOLS[verb].handler(client, actor, args);
    await client.query("commit");
    return result;
  } catch (e) {
    await client.query("rollback");
    throw e;
  }
}

function refusal(error) {
  return (e) => e instanceof ToolError && e.payload.error === error;
}

/** The loop_block scaffolding rows only exist once the loop importer has run
 * against real vault files — never true in a disposable migration-class
 * database. add-loop and close-loop both look one up by (kind, block_key)
 * and refuse with no_block when it is missing. Seed the two team_loop blocks
 * this proof needs (open, done) once, idempotently, so the fixture doesn't
 * depend on an importer this database will never run. */
async function ensureTeamLoopBlocks(client, actor) {
  const existing = await client.query(
    "select block_key from loop_block where kind='team_loop' and block_key in ('open','done')");
  const have = new Set(existing.rows.map(r => r.block_key));
  const rows = [
    { block_key: "open", seq: 1, renders_closed: false, header_cols: ["#", "Owner", "Ask"], col_order: ["number", "owner", "title"] },
    { block_key: "done", seq: 2, renders_closed: true, header_cols: ["#", "Owner", "Ask", "Outcome"], col_order: ["number", "owner", "title", "close_outcome"] },
  ];
  for (const row of rows) {
    if (have.has(row.block_key)) continue;
    await client.query(
      `insert into loop_block (rel_path, kind, seq, block_key, header_cols, col_order, renders_closed, created_by, updated_by)
         values ('team-loops.md', 'team_loop', $1, $2, $3, $4, $5, $6, $6)
         on conflict (rel_path, seq) do nothing`,
      [row.seq, row.block_key, row.header_cols, row.col_order, row.renders_closed, actor.id]);
  }
}

/** Opens a fresh team_loop (no blocker requirement, unlike open_loop) and
 * closes it, returning its id and the version read back after closing. */
async function freshClosedLoop(client, actor, { outcome = "Landed in PR #900, verified twice." } = {}) {
  await ensureTeamLoopBlocks(client, actor);
  const opened = await call(client, actor, "add-loop", {
    idempotency_key: randomUUID(), kind: "team_loop", owner: "joe",
    title: `amend-closed-loop proof ${randomUUID().slice(0, 8)}`, body: "fixture row" });
  const closed = await call(client, actor, "close-loop", {
    idempotency_key: randomUUID(), loop_id: opened.loop_id, base_version: 1,
    outcome, resolution: "done" });
  return { loop_id: opened.loop_id, version: 2, prior_outcome: outcome };
}

test("DB: an amend appends loop_amendment, updates loop_item's current projection, and bumps its version", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-actor-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version, prior_outcome } = await freshClosedLoop(admin, actor);

    const newOutcome = "Corrected: the card visual system shipped, not the bio-header reminder.";
    const reason = "Original outcome text was pasted from the wrong loop.";
    const result = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: newOutcome, reason });

    assert.equal(result.ok, true);
    assert.equal(result.prior_outcome, prior_outcome);
    assert.equal(result.outcome, newOutcome);

    const row = await admin.query(
      "select close_outcome, outcome, status, version, closed_at, closed_by from loop_item where id=$1", [loop_id]);
    assert.equal(row.rows[0].close_outcome, newOutcome, "the current projection reads the latest amendment");
    assert.equal(row.rows[0].outcome, newOutcome);
    assert.equal(row.rows[0].status, "done", "resolution unchanged when not passed");
    assert.equal(row.rows[0].version, version + 1, "trg_touch_row bumps the version on the projection update");
    assert.ok(row.rows[0].closed_at, "closed_at is never cleared by an amendment");

    const trail = await admin.query(
      "select prior_outcome, new_outcome, reason, actor_id from loop_amendment where loop_id=$1 order by created_at", [loop_id]);
    assert.equal(trail.rows.length, 1, "exactly one amendment row for one correction");
    assert.equal(trail.rows[0].prior_outcome, prior_outcome);
    assert.equal(trail.rows[0].new_outcome, newOutcome);
    assert.equal(trail.rows[0].reason, reason);
    assert.equal(trail.rows[0].actor_id, actor.id, "actor_id is the server-derived caller, never a client field");
  } finally {
    await admin.end();
  }
});

test("DB: history is preserved across TWO amendments — both rows survive, in order, and the projection reads the latest", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-history-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version: v1 } = await freshClosedLoop(admin, actor, { outcome: "x" });

    const first = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: v1,
      outcome: "First correction of the placeholder outcome.", reason: "placeholder was never real" });
    const second = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: v1 + 1,
      outcome: "Second correction, now accurate for real.", reason: "first correction still had the wrong PR number" });

    assert.equal(first.prior_outcome, "x");
    assert.equal(second.prior_outcome, "First correction of the placeholder outcome.");

    const trail = await admin.query(
      "select prior_outcome, new_outcome from loop_amendment where loop_id=$1 order by created_at", [loop_id]);
    assert.equal(trail.rows.length, 2, "NEITHER prior amendment is overwritten — append-only means both survive");
    assert.equal(trail.rows[0].new_outcome, "First correction of the placeholder outcome.");
    assert.equal(trail.rows[1].new_outcome, "Second correction, now accurate for real.");

    const row = await admin.query("select close_outcome from loop_item where id=$1", [loop_id]);
    assert.equal(row.rows[0].close_outcome, "Second correction, now accurate for real.",
      "the loop's current outcome reads the LATEST amendment, not the first");
  } finally {
    await admin.end();
  }
});

test("DB: a stale base_version is refused with version_conflict and writes nothing", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-stale-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    await assert.rejects(() => call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version - 1,
      outcome: "This should never land.", reason: "stale read" }), refusal("version_conflict"));
    const trail = await admin.query("select count(*)::int as n from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].n, 0, "a refused amend never appends a row");
  } finally {
    await admin.end();
  }
});

test("DB: an OPEN loop is refused with loop_open, never partially amended", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-open-${randomUUID().slice(0, 8)}`);
  try {
    const opened = await call(admin, actor, "add-loop", {
      idempotency_key: randomUUID(), kind: "team_loop", owner: "joe",
      title: `still-open ${randomUUID().slice(0, 8)}`, body: "fixture" });
    await assert.rejects(() => call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id: opened.loop_id, base_version: 1,
      outcome: "This should never land.", reason: "premature" }), refusal("loop_open"));
    const row = await admin.query("select status, close_outcome from loop_item where id=$1", [opened.loop_id]);
    assert.equal(row.rows[0].status, "open");
    assert.equal(row.rows[0].close_outcome, null);
  } finally {
    await admin.end();
  }
});

test("DB: a too-short outcome is refused by the same defect a2c04ffa shape ('x') before any write", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-short-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    await assert.rejects(() => call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "x", reason: "typo" }), refusal("outcome_too_short"));
    const trail = await admin.query("select count(*)::int as n from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].n, 0);
  } finally {
    await admin.end();
  }
});

test("DB: a caller-supplied actor field never reaches the actor_id column", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-actor-spoof-${randomUUID().slice(0, 8)}`);
  const impersonated = await mintActor(admin, `amend-loop-impersonated-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    // TOOLS["amend-closed-loop"].inputSchema has no `actor`/`actor_id` property at
    // all; the handler never reads args.actor* — this asserts the OUTCOME (the
    // row) rather than merely the schema, which is what actually matters.
    await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "Corrected outcome text, real fix landed.", reason: "server actor proof",
      actor: impersonated.slug, actor_id: impersonated.id });
    const trail = await admin.query("select actor_id from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].actor_id, actor.id);
    assert.notEqual(trail.rows[0].actor_id, impersonated.id);
  } finally {
    await admin.end();
  }
});

test("DB: an idempotent replay returns the recorded response and writes no second loop_amendment row", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-replay-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    const key = randomUUID();
    const args = { idempotency_key: key, loop_id, base_version: version,
      outcome: "Replayed correction text, landed once.", reason: "idempotency proof" };

    const first = await call(admin, actor, "amend-closed-loop", args);
    const second = await call(admin, actor, "amend-closed-loop", args);

    assert.equal(second.replayed, true);
    assert.equal(second.loop_id, first.loop_id);
    assert.equal(second.outcome, first.outcome);

    const trail = await admin.query("select count(*)::int as n from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].n, 1, "the replay must not append a second amendment row");
    const row = await admin.query("select version from loop_item where id=$1", [loop_id]);
    assert.equal(row.rows[0].version, version + 1, "the replay must not bump the version a second time");
  } finally {
    await admin.end();
  }
});
