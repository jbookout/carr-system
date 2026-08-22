// current-work-item.test.mjs — one verb answers "what is being worked right now".
//
// WHY IT EXISTS. The tune-up council of 2026-08-21 ruled that the binding
// dysfunction was a missing completion surface: work entered cheaply and never
// formally left, and no single read could say what was current. Answering it
// took a read of the engineering suite, a read of the sourced queue, and a
// guess — which is how the suite sat at "0 complete of 51" while six of its
// projects were finished and nobody could tell.
//
// WHAT THE COUNCIL ASKED FOR, and what these cases pin:
//   the active item(s), with owner, done-predicate, and blocker-or-null.
//
// Each of those four words is a case below, because each was a way the previous
// answer went wrong:
//   * ACTIVE — a blocked or needs-Joe row is STILL CURRENT and is never skipped.
//     Dropping it is how a queue looks empty while nothing can move.
//   * OWNER — who answers for it and who has hands on it are different
//     questions; one "assignee" field loses one of them.
//   * DONE-PREDICATE — the acceptance predicates if the row carries them, else
//     the written completion definition, else null. Never invented.
//   * BLOCKER-OR-NULL — present even when null, because an absent field and
//     "not blocked" read identically in a payload and mean different things.
//
// Run with: node --test mcp-server/test/current-work-item.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude",
  organization_tenant_id: "carr-internal" };

const ROWS = [
  { ref: "WR-AI-006", title: "RAG pipeline", state: "in_progress",
    owner_actor: "joe", executor_actor: "codex",
    acceptance_criteria: ["Golden queries identify current source in top results"],
    project_context: { completion_definition: "Extended when retrieval quality is measured" },
    blocker_code: null, blocker_detail: null,
    program_key: "carr-ai-engineering-suite-v1", program_ordinal: 1,
    claimed_at: "2026-08-20T00:00:00Z", started_at: null,
    updated_at: "2026-08-22T00:00:00Z", hours_since_change: 4 },
  { ref: "WR-91", title: "Blocked on a credential", state: "blocked",
    owner_actor: "joe", executor_actor: null,
    acceptance_criteria: [], project_context: {},
    blocker_code: "capability", blocker_detail: "needs the authority credential Joe holds",
    program_key: null, program_ordinal: null,
    claimed_at: null, started_at: null,
    updated_at: "2026-08-18T00:00:00Z", hours_since_change: 96 },
  { ref: "WR-92", title: "Second thing codex is holding", state: "claimed",
    owner_actor: "joe", executor_actor: "codex",
    acceptance_criteria: [], project_context: {},
    blocker_code: null, blocker_detail: null,
    program_key: null, program_ordinal: null,
    claimed_at: "2026-08-22T00:00:00Z", started_at: null,
    updated_at: "2026-08-22T00:00:00Z", hours_since_change: 1 },
];

class Fake {
  constructor(rows = ROWS) { this.rows = rows; this.sql = []; }
  async query(text) {
    const s = text.replace(/\s+/g, " ").trim();
    this.sql.push(s);
    if (s.includes("work-request-intake:current-item")) return { rows: this.rows };
    return { rows: [] };
  }
}

const call = async (fake = new Fake(), args = {}) =>
  TOOLS["current-work-item"].handler(fake, joe, args);

test("the verb exists, reads nothing, and takes no arguments", async () => {
  const tool = TOOLS["current-work-item"];
  assert.ok(tool, "the council asked for ONE verb that answers this");
  assert.equal(tool.write, false, "answering what is current must never mutate");
  assert.deepEqual(tool.inputSchema.properties, {},
    "a caller-chosen filter is how a queue answer gets shaped to the answer someone wanted");
  assert.equal(tool.inputSchema.additionalProperties, false);
  await assert.rejects(() => call(new Fake(), { owner: "joe" }),
    (e) => e instanceof ToolError && e.payload.error === "invalid_current_work_item_fields");
});

test("a blocked row is STILL CURRENT and is never dropped", async () => {
  const out = await call();
  const refs = out.current.map(i => i.human_ref);
  assert.ok(refs.includes("WR-91"),
    "the council was explicit that a blocked row stays current and can never be skipped");
  const blocked = out.current.find(i => i.human_ref === "WR-91");
  assert.equal(blocked.blocker.code, "capability");
  assert.match(blocked.blocker.detail, /authority credential/);
});

test("blocker is present and null when there is none, never absent", async () => {
  const out = await call();
  const clear = out.current.find(i => i.human_ref === "WR-AI-006");
  assert.ok("blocker" in clear, "an absent field and 'not blocked' read the same and are not");
  assert.equal(clear.blocker, null);
});

test("owner and executor stay separate questions", async () => {
  const out = await call();
  const item = out.current.find(i => i.human_ref === "WR-AI-006");
  assert.equal(item.owner, "joe", "who answers for it");
  assert.equal(item.executor, "codex", "who has hands on it");
});

test("the done predicate prefers acceptance criteria, falls back, and is never invented", async () => {
  const out = await call();
  const withCriteria = out.current.find(i => i.human_ref === "WR-AI-006");
  assert.deepEqual(withCriteria.done_predicate,
    ["Golden queries identify current source in top results"],
    "acceptance predicates win over the prose completion definition");

  const fake = new Fake([{ ...ROWS[1], acceptance_criteria: [],
    project_context: { completion_definition: "Extended when the gap is enforced" } }]);
  const fell = await call(fake);
  assert.deepEqual(fell.current[0].done_predicate, ["Extended when the gap is enforced"]);

  const bare = new Fake([{ ...ROWS[1], acceptance_criteria: [], project_context: {} }]);
  const none = await call(bare);
  assert.equal(none.current[0].done_predicate, null,
    "a row with no stated predicate reports null rather than a plausible sentence");
});

test("the work-in-progress limit is reported, and over-commitment is named", async () => {
  const out = await call();
  assert.equal(out.wip.limit_system_wide, 2);
  assert.equal(out.wip.limit_per_executor, 1);
  // WR-AI-006 in_progress + WR-92 claimed, both codex; the blocked row is held
  // but not in flight.
  assert.equal(out.wip.in_flight, 2);
  assert.deepEqual(out.wip.executors_over_limit, [{ executor: "codex", in_flight: 2 }]);
  assert.match(out.wip.note, /enforced in the claim path/,
    "reporting a limit and enforcing it are different jobs and must not be confused");
});

test("an item nobody has touched for two days is surfaced", async () => {
  const out = await call();
  const stale = out.unchanged_over_48h.map(s => s.human_ref);
  assert.deepEqual(stale, ["WR-91"],
    "the council forces a 48-hour-idle item to blocked, split or closed; surfacing it is this read's half");
});

test("an empty queue says so rather than looking like a broken read", async () => {
  const out = await call(new Fake([]));
  assert.equal(out.count, 0);
  assert.deepEqual(out.current, []);
  assert.match(out.say, /nothing is held/);
  assert.match(out.say, /ready work/,
    "silence about ready work is how an empty answer gets read as an empty system");
});

test("ready and captured rows are not treated as current", async () => {
  const fake = new Fake();
  await call(fake);
  const q = fake.sql.find(s => s.includes("work-request-intake:current-item"));
  assert.ok(q.includes("'claimed','in_progress','verification','needs_joe','blocked'"),
    "the queue is not the current item; only held work is");
  assert.ok(!q.includes("'ready'") && !q.includes("'captured'"));
});

test("the read is tenant-scoped by the server, never by the caller", async () => {
  const fake = new Fake();
  await call(fake);
  const q = fake.sql.find(s => s.includes("work-request-intake:current-item"));
  assert.ok(q.includes("organization_tenant_id"),
    "a caller-supplied tenant is the shape this registry refuses everywhere else");
});
