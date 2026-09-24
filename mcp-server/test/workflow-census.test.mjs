import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool, TOOLS } from "../src/tools.js";

const AGENT = { id: "10000000-0000-0000-0000-000000000041", slug: "joe-local", human: false, via: "test" };

async function rejected(fn) {
  try { await fn(); assert.fail("expected refusal"); }
  catch (e) { assert.ok(e instanceof ToolError, `expected ToolError, got ${e}`); return e.payload; }
}

const CENSUS = Object.freeze({
  schema_version: "control-plane-workflow-truth.v1",
  observed_at: "2026-09-24T09:10:00+00:00",
  observation_max_age_seconds: 900,
  rows: [{ workflow_key: "calendar-fetch-daily", workflow_version: 5, state: "enabled_shadow_only" }],
  summary: { workflows: 1, false_operational: 1 },
});

// Mirrors ops.record_workflow_census / ops.read_workflow_census
// (migrations/0595): the door takes the census and the key only; principal and
// time come from the server side, and the chain fields are computed there.
class CensusFake {
  constructor({ doorError = null, readResult = undefined } = {}) {
    this.calls = []; this.toolCalls = new Map(); this.rows = [];
    this.doorError = doorError; this.readResult = readResult;
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [{}] };
    if (sql.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]);
      return { rows: row ? [row] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.includes("ops.record_workflow_census")) {
      if (this.doorError) throw new Error(this.doorError);
      assert.equal(params.length, 2, "the door takes the census and the key, nothing else");
      const seq = this.rows.length + 1;
      const row = {
        seq: String(seq), recorded_at: `2026-09-24T09:10:0${seq}.000000Z`, principal: "joe-local",
        row_hash: String(seq).repeat(64).slice(0, 64), prev_hash: seq === 1 ? null : String(seq - 1).repeat(64).slice(0, 64),
        payload_sha256: "a".repeat(64), replayed: false,
      };
      this.rows.push({ ...row, payload: JSON.parse(params[0]), key: params[1] });
      return { rows: [row] };
    }
    if (sql.startsWith("select ops.read_workflow_census")) {
      if (this.readResult !== undefined) return { rows: [{ result: this.readResult }] };
      return { rows: [{ result: {
        schema_version: "workflow-census-chain.v1", server_now: "2026-09-24T10:00:00.000000Z",
        row_count: this.rows.length, truncated: false,
        chain: this.rows.map(({ payload, key, replayed, ...rest }) => ({ ...rest, seq: Number(rest.seq),
          db_session_principal: "carr_writer" })),
        latest_payload: this.rows.at(-1)?.payload ?? null,
      } }] };
    }
    throw new Error(`CensusFake: unhandled query: ${sql}`);
  }
}

test("record-workflow-census accepts no principal and no time from the caller", () => {
  const schema = TOOLS["record-workflow-census"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(Object.keys(schema.properties).sort(), ["census", "idempotency_key"]);
  assert.equal(TOOLS["record-workflow-census"].write, true);
  assert.equal(TOOLS["read-workflow-census"].write, false);
});

test("record-workflow-census sends the census and the key to the door and returns the server's stamp", async () => {
  const c = new CensusFake();
  const out = await executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-1", census: structuredClone(CENSUS) });
  assert.equal(out.ok, true);
  assert.equal(out.seq, 1);
  assert.equal(out.principal, "joe-local");
  assert.equal(out.prev_hash, null);
  const door = c.calls.find(call => call.sql.includes("ops.record_workflow_census"));
  assert.deepEqual(JSON.parse(door.params[0]), CENSUS);
  assert.equal(door.params[1], "k-1");
  assert.ok(!door.params.includes("joe-local"), "the handler never passes the actor to the door");
});

test("record-workflow-census refuses a census the chain cannot hash identically across languages", async () => {
  const c = new CensusFake();
  const fraction = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-2", census: { ...structuredClone(CENSUS), summary: { ratio: 0.5 } } }));
  assert.equal(fraction.error, "workflow_census_payload_fraction_refused");
  const shape = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-3", census: { ...structuredClone(CENSUS), schema_version: "other.v1" } }));
  assert.equal(shape.error, "workflow_census_payload_shape_refused");
  const rowsMissing = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-4", census: { schema_version: CENSUS.schema_version, summary: {} } }));
  assert.equal(rowsMissing.error, "workflow_census_payload_shape_refused");
  assert.ok(!c.calls.some(call => call.sql.includes("ops.record_workflow_census")),
    "no refused census reaches the door");
});

test("a door refusal comes back by name, not as an unhandled failure", async () => {
  for (const name of ["workflow_census_principal_unavailable", "workflow_census_key_reuse",
    "workflow_census_time_regression_refused"]) {
    const c = new CensusFake({ doorError: `ERROR: ${name}` });
    const payload = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
      { idempotency_key: `k-${name}`, census: structuredClone(CENSUS) }));
    assert.equal(payload.error, name);
  }
});

test("read-workflow-census returns the chain as served, bounded by max_rows", async () => {
  const c = new CensusFake();
  await executeRegisteredTool(c, AGENT, "record-workflow-census", { idempotency_key: "k-a", census: structuredClone(CENSUS) });
  await executeRegisteredTool(c, AGENT, "record-workflow-census", { idempotency_key: "k-b", census: structuredClone(CENSUS) });
  const out = await executeRegisteredTool(c, AGENT, "read-workflow-census", {});
  assert.equal(out.ok, true);
  assert.equal(out.row_count, 2);
  assert.equal(out.truncated, false);
  assert.equal(out.chain.length, 2);
  assert.deepEqual(out.latest_payload, CENSUS);
  const read = c.calls.find(call => call.sql.startsWith("select ops.read_workflow_census"));
  assert.deepEqual(read.params, [20000]);
  const bad = await rejected(() => executeRegisteredTool(c, AGENT, "read-workflow-census", { max_rows: 0 }));
  assert.equal(bad.error, "workflow_census_max_rows_invalid");
  assert.equal(c.calls.filter(call => call.sql.startsWith("select ops.read_workflow_census")).length, 1,
    "a refused max_rows never reaches the read door");
});

test("read-workflow-census refuses a malformed store answer instead of passing it on", async () => {
  const c = new CensusFake({ readResult: { chain: "not-a-list" } });
  const payload = await rejected(() => executeRegisteredTool(c, AGENT, "read-workflow-census", {}));
  assert.equal(payload.error, "workflow_census_unavailable");
});
