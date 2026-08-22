// payload-budgets.test.mjs — summary/names_only flags on the three heaviest
// read verbs must cut the payload, and absent flags must return today's shape.
//
// WHY: one full loop-board read at 100+ open loops carries body-sized labels
// and blocker_detail prose for every row, and outweighs every other verb call
// a session makes. The budget flags answer "what is open / blocked / owned /
// where the program stands / which verb serves this job" without paying for it.
//
// Run with: node --test mcp-server/test/payload-budgets.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";

import { TOOLS } from "../src/tools.js";
import { capabilityProgramTools } from "../src/capability-program.js";

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "test-actor", human: true, slug: "joe" };

const dbFor = (rows) => ({ query: async () => ({ rows }) });

// ── loop-board ────────────────────────────────────────────────────────────────

const boardRows = [
  { number: "495", kind: "open_loop", domain: "system", status: "open",
    owner: "claude", marker: "none", title: null,
    label: "A long label that goes on well past eighty characters just to prove truncation works here",
    joint_owner: false, blocker_class: "human_only",
    blocker_detail: "long prose detail that must NOT appear in summary mode",
    since_text: "2026-08-22", due_on: null, version: 3 },
  { number: "170", kind: "open_loop", domain: "business", status: "open",
    owner: "joe", marker: "none", title: null, label: "Optimise Joe's outlook",
    joint_owner: false, blocker_class: null, blocker_detail: null,
    since_text: "2026-08-04", due_on: null, version: 4 },
];

// THE DECLARED SHAPE, copied from the summary flag's own description in
// tools.js. This list is the contract; the test below asserts the returned row
// matches it EXACTLY, in both directions. Adding a field here without adding it
// to the description (or the reverse) is the defect this guards.
const DECLARED_LOOP_KEYS = ["number", "kind", "label", "blocker_class", "owner", "since_text", "version"];

function boardHandler() {
  // The two modes run DIFFERENT SQL over the same table; hand back rows shaped
  // like each query's own select list so the test proves the summary SQL really
  // selects no body/detail column.
  const queries = [];
  const db = { query: async (sql) => {
    queries.push(sql);
    if (/left\(/i.test(sql)) {
      return { rows: boardRows.map(r => ({
        number: r.number, kind: r.kind, status: r.status, owner: r.owner,
        label: r.label.slice(0, 80), blocker_class: r.blocker_class,
        since_text: r.since_text, version: r.version,
      })) };
    }
    return { rows: boardRows };
  } };
  return { db, queries };
}

test("loop-board summary returns counts and slim rows only", async () => {
  const { db, queries } = boardHandler();
  const result = await TOOLS["loop-board"].handler(db, actor, { summary: true });

  assert.equal(result.summary, true);
  assert.equal(result.count, 2);
  assert.deepEqual(result.by_status, { open: 2 });
  assert.deepEqual(result.by_blocker_class, { human_only: 1, none: 1 });
  assert.deepEqual(result.by_owner, { claude: 1, joe: 1 });
  for (const loop of result.loops) {
    for (const forbidden of ["body", "blocker_detail", "domain", "marker", "joint_owner"])
      assert.equal(forbidden in loop, false, `summary rows must not carry ${forbidden}`);
    for (const required of DECLARED_LOOP_KEYS)
      assert.ok(required in loop, `summary rows must carry ${required}`);
    // EXACT KEYS, not merely the declared ones plus whatever else. The
    // inclusion-only version of this block shipped in 4b816d85 and let an
    // undeclared due_on through: an EXTRA key satisfied both loops above, so
    // nothing failed. The flag's own description is the contract a caller
    // writes code against, and a payload that carries more than it declares
    // has broken it just as surely as one that carries less.
    assert.deepEqual([...Object.keys(loop)].sort(), [...DECLARED_LOOP_KEYS].sort(),
      `summary rows must match the declared shape EXACTLY; got ${Object.keys(loop).sort().join(", ")}`);
  }
  assert.match(queries[queries.length - 1], /left\(/i, "labels are truncated in SQL");
  assert.doesNotMatch(queries[queries.length - 1], /blocker_detail|coalesce\(body|due_on/i,
    "the summary SQL must not even select the heavy columns");
});

test("loop-board without the flag returns today's full rows (backwards compatible)", async () => {
  const { db } = boardHandler();
  const result = await TOOLS["loop-board"].handler(db, actor, {});
  assert.equal(result.summary, undefined);
  assert.equal(result.count, 2);
  assert.equal("by_status" in result, false);
  assert.ok(result.loops[0].blocker_detail !== undefined, "full rows keep blocker_detail");
  assert.ok(!("current_version" in result.loops[0]), "row shape unchanged");
  assert.ok("label" in result.loops[0] && "version" in result.loops[0]);
});

// ── capability-program ────────────────────────────────────────────────────────

function programDb(rows) {
  return { query: async (sql) => {
    if (sql.includes("from ops.work_request")) return { rows };
    throw new Error(`unexpected query: ${sql}`);
  }};
}

const programRows = [
  { id: "wr-1", ref: "WR-AI-006", program_ordinal: 1, version: 5, state: "ready",
    disposition: "build", title: "Current project",
    desired_outcome: "long prose outcome that must stay out of brief rows",
    acceptance_criteria: ["c1"], executor_actor: "hermes-pilot",
    project_context: { scope: "prose" }, completion_evidence: { artifact_ref: "x" } },
  { id: "wr-2", ref: "WR-AI-007", program_ordinal: 2, version: 1, state: "confirmed_closed",
    disposition: "adopt", title: "Closed project", desired_outcome: "done prose",
    acceptance_criteria: [], executor_actor: null, project_context: {} },
];

test("capability-program summary keeps current in full, others brief", async () => {
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const result = await tools["capability-program"].handler(programDb(programRows), actor,
    { program_key: "carr-ai-engineering-suite-v1", summary: true });

  assert.equal(result.total, 2);
  assert.equal(result.completed, 1);
  assert.equal(result.program_complete, false);
  // Current stays FULL — it is the row a session acts on.
  assert.equal(result.current.ref, "WR-AI-006");
  assert.ok(result.current.desired_outcome !== undefined);
  assert.deepEqual(result.requested, { ref: "WR-AI-006", sequence: 1, title: "Current project",
    state: "ready", disposition: "build", executor_actor: "hermes-pilot" });
  for (const p of result.projects) {
    assert.deepEqual(Object.keys(p).sort(),
      ["disposition", "executor_actor", "ref", "sequence", "state", "title"]);
  }
  assert.ok(!("capability_session" in result), "no session read is needed for a queue scan");
});

test("capability-program without the flag returns today's full payload (backwards compatible)", async () => {
  const tools = capabilityProgramTools({ withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => {}, ToolError });
  const db = { query: async (sql) => {
    if (sql.includes("from ops.work_request")) return { rows: [programRows[0]] };
    if (sql.includes("from ops.capability_agent_session")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }};
  const result = await tools["capability-program"].handler(db, actor, { program_key: "carr-ai-engineering-suite-v1" });
  assert.ok(result.session_brief !== undefined, "full mode still carries session_brief");
  assert.equal(result.projects, undefined, "include_all still defaults off — shape unchanged");
  assert.equal(result.current.desired_outcome, "long prose outcome that must stay out of brief rows");
});

// ── list-verbs ────────────────────────────────────────────────────────────────

test("list-verbs filter matches name AND description, case-insensitively", async () => {
  const byName = await TOOLS["list-verbs"].handler(null, actor, { filter: "CLOSE-LOOP" });
  assert.ok(byName.verbs.some(v => v.name === "close-loop"), "name match is case-insensitive");

  // 'do-it-or-close-it' appears in loop-board's DESCRIPTION, not its name.
  const byDescription = await TOOLS["list-verbs"].handler(null, actor, { filter: "do-it-or-close-it" });
  assert.ok(byDescription.verbs.some(v => v.name === "loop-board"),
    "a description-only match must be found — rule 49c627cc, match on behavior");

  const miss = await TOOLS["list-verbs"].handler(null, actor, { filter: "zzz-no-such-behavior" });
  assert.equal(miss.count, 0);

  const all = await TOOLS["list-verbs"].handler(null, actor, {});
  assert.equal(all.count, Object.keys(TOOLS).length, "absent filter returns every verb");
  assert.ok(all.verbs.every(v => "inputSchema" in v), "default mode keeps schemas");
});

test("list-verbs names_only drops schemas and composes with filter", async () => {
  const bare = await TOOLS["list-verbs"].handler(null, actor, { names_only: true });
  assert.equal(bare.names_only, true);
  assert.ok(bare.verbs.length > 10);
  for (const v of bare.verbs) {
    assert.equal("inputSchema" in v, false, "names_only carries no schemas");
    assert.ok(v.description.length > 0 && v.description.length <= 200);
  }

  const composed = await TOOLS["list-verbs"].handler(null, actor, { filter: "close-loop", names_only: true });
  assert.ok(composed.verbs.some(v => v.name === "close-loop"));
  assert.equal(composed.verbs.some(v => v.name === "loop-board"), false, "filter still narrows under names_only");
});
