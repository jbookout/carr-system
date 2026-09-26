// action-class-successor-registry.test.mjs — unit coverage for V5-D01's three
// verbs (register-action-class-successor, read-action-class-successors,
// read-action-class-gate) against a fake client. The migration's own
// guarantees (CHECK-locked status, immutability trigger, an unconditional
// gate) are proved separately on real PostgreSQL in
// action-class-successor-registry-postgres.test.mjs.
//
// Run with: node --test mcp-server/test/action-class-successor-registry.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

const GOOD = {
  idempotency_key: "11111111-1111-1111-1111-111111111111",
  action_class: "salesforce_unattended_write",
  title: "Unattended Salesforce field writes",
  goal: "Let a future, activated automation write qualifying Salesforce fields without a human in the loop.",
  owner: "joe",
  activation_predicate: { requires: ["independent conformance receipt", "partner sign-off"] },
};

class Fake {
  constructor({ existing = false } = {}) {
    this.existing = existing;
    this.writes = [];
  }
  async query(text, params) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select 1 from action_class_successor"))
      return { rows: this.existing ? [{ "?column?": 1 }] : [] };
    if (sql.startsWith("select * from read_action_class_successors")) {
      return { rows: [{ id: "row-1", action_class: params[0] ?? GOOD.action_class,
        title: GOOD.title, goal: GOOD.goal, owner: GOOD.owner,
        policy_requirements: {}, data_requirements: {}, model_requirements: {},
        activation_predicate: GOOD.activation_predicate, status: "inactive",
        actor: "joe", created_at: new Date("2026-09-25T00:00:00Z") }] };
    }
    if (sql.startsWith("select * from action_class_successor_gate")) {
      return { rows: [{ action_class: params[0], allowed: false,
        reason: "no successor registered for this action class", registered: false }] };
    }
    this.writes.push({ sql, params });
    if (sql.startsWith("insert into action_class_successor"))
      return { rows: [{ id: "new-row-1", status: "inactive", created_at: new Date("2026-09-25T00:00:00Z") }] };
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-1" }] };
    return { rows: [] };
  }
}

async function call(verb, args, fake = new Fake()) {
  try { return { fake, result: await TOOLS[verb].handler(fake, joe, args), error: null }; }
  catch (e) { return { fake, result: null, error: e instanceof ToolError ? e.payload : e }; }
}

test("all three verbs are registered with the documented write flags", () => {
  assert.equal(TOOLS["register-action-class-successor"].write, true);
  assert.equal(TOOLS["read-action-class-successors"].write, false);
  assert.equal(TOOLS["read-action-class-gate"].write, false);
});

test("register: a well-formed entry is inserted with server-derived actor and returns capability_issued:false", async () => {
  const { fake, error, result } = await call("register-action-class-successor", { ...GOOD });
  assert.equal(error, null);
  assert.equal(result.ok, true);
  assert.equal(result.status, "inactive");
  assert.equal(result.capability_issued, false);
  const insert = fake.writes.find((w) => w.sql.startsWith("insert into action_class_successor"));
  assert.ok(insert, "the insert must run");
  assert.equal(insert.params[0], GOOD.action_class);
  assert.equal(insert.params[8], joe.id, "actor_id is always the server-authenticated actor");
});

test("register: action_class must match the slug pattern", async () => {
  for (const bad of ["Salesforce", "1abc", "ab", "has space", "has-dash"]) {
    const { error, fake } = await call("register-action-class-successor", { ...GOOD, action_class: bad });
    assert.equal(error?.error, "action_class_invalid");
    assert.equal(fake.writes.length, 0);
  }
});

test("register: title/goal/owner are required and non-blank", async () => {
  for (const field of ["title", "goal", "owner"]) {
    const { error } = await call("register-action-class-successor", { ...GOOD, [field]: "  " });
    assert.equal(error?.error, `${field}_required`);
  }
});

test("register: activation_predicate must be a plain object", async () => {
  for (const bad of [null, "a string", ["array"], 5]) {
    const { error } = await call("register-action-class-successor", { ...GOOD, activation_predicate: bad });
    assert.equal(error?.error, "invalid_shape");
  }
});

test("register: refused when the action_class is already registered, and nothing is written", async () => {
  const fake = new Fake({ existing: true });
  const { error } = await call("register-action-class-successor", { ...GOOD }, fake);
  assert.equal(error?.error, "action_class_already_registered");
  assert.equal(fake.writes.length, 0, "no insert on a duplicate action_class");
});

test("register: a caller-supplied status or actor field cannot reach the row -- the schema admits no such property", () => {
  const props = TOOLS["register-action-class-successor"].inputSchema.properties;
  assert.equal(props.status, undefined);
  assert.equal(props.actor_id, undefined);
  assert.equal(TOOLS["register-action-class-successor"].inputSchema.additionalProperties, false);
});

test("read-action-class-successors: lists entries, filtered when action_class is passed", async () => {
  const { error, result } = await call("read-action-class-successors", { action_class: GOOD.action_class });
  assert.equal(error, null);
  assert.equal(result.entries.length, 1);
  assert.equal(result.entries[0].status, "inactive");
});

test("read-action-class-successors: an invalid filter is refused before any query", async () => {
  const { error } = await call("read-action-class-successors", { action_class: "NOT VALID" });
  assert.equal(error?.error, "action_class_invalid");
});

test("read-action-class-gate: denies an unregistered class", async () => {
  const { error, result } = await call("read-action-class-gate", { action_class: "email_unattended_send" });
  assert.equal(error, null);
  assert.equal(result.allowed, false);
  assert.equal(result.registered, false);
});

test("read-action-class-gate: requires action_class", () => {
  assert.deepEqual(TOOLS["read-action-class-gate"].inputSchema.required, ["action_class"]);
});
