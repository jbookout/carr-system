import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool, TOOLS } from "../src/tools.js";
import { canonicalCensusJson, censusPayloadSha256, censusRowHash, verifyInsertedCensusRow,
  MAX_RECORDED_AT_SKEW_MS } from "../src/workflow-census.js";

// trusted_principal is what mcp.js attaches after reading back session_user.
const AGENT = { id: "10000000-0000-0000-0000-000000000041", slug: "joe-local", human: false, via: "test",
  trusted_principal: { session_principal: "carr_writer" } };
const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "oauth-google" };

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

const GUARD_FUNCTIONS = Object.freeze({ workflow_census_record_append_only: "6".repeat(64),
  workflow_census_record_chain_guard: "7".repeat(64), workflow_census_record_no_truncate: "6".repeat(64) });

// Mirrors ops.record_workflow_census / ops.read_workflow_census /
// ops.reanchor_workflow_census (migrations/0708): the write door takes the
// census, the key and the anchored head the Worker read; principal and time
// come from the server side, and the chain fields are computed there. The
// fake's anchor follows its own rows, as a Worker whose advances all landed.
class CensusFake {
  constructor({ doorError = null, readResult = undefined, anchor = "follow", pending = "accept" } = {}) {
    this.calls = []; this.toolCalls = new Map(); this.rows = []; this.anchorReads = 0;
    this.doorError = doorError; this.readResult = readResult; this.registered = [];
    // The anchor's pending-head registration (workflow-census-anchor.js /pending).
    if (pending === "accept")
      this.workflowCensusPending = async (row, key) => {
        this.registered.push({ row, key, at: this.calls.length });
        return { ok: true, state: "pending" };
      };
    else if (pending !== null)
      this.workflowCensusPending = async (row, key) => { this.registered.push({ row, key }); return pending; };
    if (anchor === "follow") {
      this.workflowCensusAnchor = async () => {
        this.anchorReads += 1;
        const last = this.rows.at(-1);
        return last ? { state: "present", seq: Number(last.seq), row_hash: last.row_hash,
          anchored_at: "2026-09-24T09:10:09.000Z", last_reanchor: null } : { state: "absent", last_reanchor: null };
      };
    } else if (anchor !== null) {
      this.workflowCensusAnchor = async () => { this.anchorReads += 1; return anchor; };
    }
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
      if (this.doorError) throw Object.assign(new Error(this.doorError), this.doorErrorDetail ?? {});
      assert.equal(params.length, 4, "the door takes the census, the key and the anchored head, nothing else");
      // The door's replay branch: a row already stored under this key comes
      // back as it is, marked replayed (whoever wrote it).
      const prior = this.rows.find(r => r.key === params[1]);
      if (prior) {
        const { payload, key, ...row } = prior;
        return { rows: [{ ...row, replayed: true }] };
      }
      // An honest door: the database's own fields, hashed as the database does.
      const seq = this.rows.length + 1;
      const payload = this.substitutePayload ?? JSON.parse(params[0]);
      const fields = { seq, recorded_at: new Date().toISOString().replace("Z", "000Z"), principal: "joe-local",
        db_session_principal: "carr_writer", prev_hash: this.rows.at(-1)?.row_hash ?? null,
        payload_sha256: await censusPayloadSha256(payload) };
      const row = { ...fields, seq: String(seq), row_hash: await censusRowHash(fields), replayed: false };
      delete row.db_session_principal;
      if (this.lie) Object.assign(row, this.lie);
      this.rows.push({ ...row, payload, key: params[1] });
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
        guards: { workflow_census_record_append_only: "A", workflow_census_record_chain_guard: "A",
          workflow_census_record_no_truncate: "A" },
        guard_functions: { ...GUARD_FUNCTIONS },
      } }] };
    }
    if (sql.includes("ops.reanchor_workflow_census")) {
      if (this.doorError) throw Object.assign(new Error(this.doorError), this.doorErrorDetail ?? {});
      assert.equal(params.length, 6);
      const [oldSeq, oldHash, newSeq, newHash, reason] = params;
      return { rows: [{ receipt_id: "0f0e0d0c-0b0a-4908-8706-050403020100",
        recorded_at: "2026-09-24T09:20:00.000000Z", actor: "joe", verified_partner: "joe", reason,
        old_seq: oldSeq === null ? null : String(oldSeq), old_row_hash: oldHash,
        new_seq: newSeq === null ? null : String(newSeq), new_row_hash: newHash,
        rows_reattested: "1", replayed: false }] };
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
  assert.deepEqual(door.params.slice(2), [null, null], "an absent anchor is passed as a null head");
  await executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-1b", census: structuredClone(CENSUS) });
  const second = c.calls.filter(call => call.sql.includes("ops.record_workflow_census"))[1];
  assert.deepEqual(second.params.slice(2), [1, out.row_hash], "the anchored head goes to the door");
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

test("record-workflow-census refuses before the door when the anchor cannot be read", async () => {
  for (const anchor of [null, { state: "unavailable", detail: "anchor_unreachable" }, { state: "present", seq: "1" }]) {
    const c = new CensusFake({ anchor });
    const payload = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
      { idempotency_key: "k-u", census: structuredClone(CENSUS) }));
    assert.equal(payload.error, "workflow_census_anchor_unavailable", JSON.stringify(anchor));
    assert.ok(!c.calls.some(call => call.sql.includes("ops.record_workflow_census")));
  }
});

test("the door's anchor refusals come back by name, with the database's detail and a hint", async () => {
  for (const [name, detail] of [["workflow_census_anchor_gap", "database_one_linked_row_ahead_of_anchor"],
    ["workflow_census_tampered", "database_head_is_not_anchored_head"]]) {
    const c = new CensusFake({ doorError: name });
    c.doorErrorDetail = { detail };
    const payload = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
      { idempotency_key: `k-${name}`, census: structuredClone(CENSUS) }));
    assert.equal(payload.error, name);
    assert.equal(payload.detail, detail);
    assert.match(payload.hint, /record-workflow-census-reanchor/);
  }
});

test("a retried key is replayed by the envelope without reading the anchor, so a gap can recover", async () => {
  const c = new CensusFake();
  const first = await executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-r", census: structuredClone(CENSUS) });
  const reads = c.anchorReads;
  c.workflowCensusAnchor = async () => { throw new Error("the anchor must not be read on a replay"); };
  const again = await executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-r", census: structuredClone(CENSUS) });
  assert.equal(again.seq, first.seq);
  assert.equal(again.row_hash, first.row_hash);
  assert.equal(reads, 1);
  assert.equal(c.calls.filter(call => call.sql.includes("ops.record_workflow_census")).length, 1);
});

test("record-workflow-census-reanchor is partner-authority only and takes no actor, head or count from the caller", () => {
  const tool = TOOLS["record-workflow-census-reanchor"];
  assert.equal(tool.write, true);
  assert.equal(tool.humanOnly, true);
  assert.equal(tool.authorityOnly, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ["accept_head", "idempotency_key", "reason"]);
  assert.equal(TOOLS["record-workflow-census"].authorityOnly, undefined);
});

test("record-workflow-census-reanchor refuses the writer's machine actor before any database call", async () => {
  const c = new CensusFake();
  const payload = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census-reanchor",
    { idempotency_key: "r-0", reason: "x", accept_head: null }));
  assert.equal(payload.error, "human_only_verb_requires_verified_partner");
  assert.equal(c.calls.length, 0);
});

test("record-workflow-census-reanchor passes the anchor it read and the head the partner accepts, and returns the receipt", async () => {
  const c = new CensusFake({ anchor: { state: "present", seq: 1, row_hash: "1".repeat(64),
    anchored_at: "2026-09-24T09:10:09.000Z", last_reanchor: null } });
  const out = await executeRegisteredTool(c, JOE, "record-workflow-census-reanchor",
    { idempotency_key: "r-1", reason: "writer lost the key of seq 2", accept_head: { seq: 2, row_hash: "2".repeat(64) } });
  const door = c.calls.find(call => call.sql.includes("ops.reanchor_workflow_census"));
  assert.deepEqual(door.params, [1, "1".repeat(64), 2, "2".repeat(64), "writer lost the key of seq 2", "r-1"]);
  assert.deepEqual(out.receipt.old_head, { seq: 1, row_hash: "1".repeat(64) });
  assert.deepEqual(out.receipt.new_head, { seq: 2, row_hash: "2".repeat(64) });
  assert.equal(out.receipt.rows_reattested, 1);
  assert.equal(out.receipt.actor, "joe");
  for (const [args, error] of [
    [{ idempotency_key: "r-2", reason: " ", accept_head: null }, "workflow_census_reanchor_reason_required"],
    [{ idempotency_key: "r-3", reason: "x", accept_head: { seq: 2, row_hash: "Z" } }, "workflow_census_anchor_invalid"],
  ]) assert.equal((await rejected(() => executeRegisteredTool(c, JOE, "record-workflow-census-reanchor", args))).error, error);
  const unread = new CensusFake({ anchor: { state: "unavailable", detail: "anchor_unreachable" } });
  assert.equal((await rejected(() => executeRegisteredTool(unread, JOE, "record-workflow-census-reanchor",
    { idempotency_key: "r-4", reason: "x", accept_head: null }))).error, "workflow_census_anchor_unavailable");
  const moved = new CensusFake({ doorError: "workflow_census_reanchor_head_moved" });
  assert.equal((await rejected(() => executeRegisteredTool(moved, JOE, "record-workflow-census-reanchor",
    { idempotency_key: "r-5", reason: "x", accept_head: null }))).error, "workflow_census_reanchor_head_moved");
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
  assert.equal(out.guards.workflow_census_record_chain_guard, "A");
  assert.deepEqual(out.guard_functions, GUARD_FUNCTIONS);
  delete c.workflowCensusAnchor;
  const unbound = await executeRegisteredTool(c, AGENT, "read-workflow-census", {});
  assert.deepEqual(unbound.anchor, { state: "unavailable", detail: "anchor_not_bound" },
    "a client with no anchor attached answers unavailable, which the reader refuses");
  const bad = await rejected(() => executeRegisteredTool(c, AGENT, "read-workflow-census", { max_rows: 0 }));
  assert.equal(bad.error, "workflow_census_max_rows_invalid");
  assert.equal(c.calls.filter(call => call.sql.startsWith("select ops.read_workflow_census")).length, 2,
    "a refused max_rows never reaches the read door");
});

test("read-workflow-census refuses a malformed store answer instead of passing it on", async () => {
  const c = new CensusFake({ readResult: { chain: "not-a-list" } });
  const payload = await rejected(() => executeRegisteredTool(c, AGENT, "read-workflow-census", {}));
  assert.equal(payload.error, "workflow_census_unavailable");
});

test("read-workflow-census reads the anchor before the chain and returns it as served", async () => {
  const c = new CensusFake();
  await executeRegisteredTool(c, AGENT, "record-workflow-census", { idempotency_key: "k-x", census: structuredClone(CENSUS) });
  const order = [];
  const inner = c.query.bind(c);
  c.query = async (text, params) => {
    if (text.includes("read_workflow_census")) order.push("chain");
    return inner(text, params);
  };
  c.workflowCensusAnchor = async () => {
    order.push("anchor");
    return { state: "present", seq: 1, row_hash: "1".repeat(64), anchored_at: "2026-09-24T09:10:01.000Z" };
  };
  const out = await executeRegisteredTool(c, AGENT, "read-workflow-census", {});
  assert.deepEqual(order, ["anchor", "chain"]);
  assert.deepEqual(out.anchor, { state: "present", seq: 1, row_hash: "1".repeat(64),
    anchored_at: "2026-09-24T09:10:01.000Z" });
});

test("read-workflow-census refuses a store answer with no guard or guard-function report", async () => {
  const base = { schema_version: "workflow-census-chain.v1", server_now: "2026-09-24T10:00:00.000000Z",
    row_count: 0, truncated: false, chain: [], latest_payload: null };
  for (const readResult of [base, { ...base, guards: {} }, { ...base, guard_functions: {} }]) {
    const c = new CensusFake({ readResult });
    const payload = await rejected(() => executeRegisteredTool(c, AGENT, "read-workflow-census", {}));
    assert.equal(payload.error, "workflow_census_unavailable");
  }
});

test("a fresh insert registers its own row as the anchor's pending head, under its key, before returning", async () => {
  const c = new CensusFake();
  const out = await executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-p1", census: structuredClone(CENSUS) });
  assert.equal(c.registered.length, 1);
  assert.deepEqual(c.registered[0].row, { seq: 1, row_hash: out.row_hash, prev_hash: null });
  assert.equal(c.registered[0].key, "k-p1");
  const doorAt = c.calls.findIndex(call => call.sql.includes("ops.record_workflow_census"));
  assert.ok(c.registered[0].at > doorAt, "registration follows the door's insert, inside the envelope");
});

test("a row the door REPLAYS (not inserted by this call) registers nothing: it cannot move the anchor", async () => {
  const c = new CensusFake();
  // The owner's forged row, stored under key K with no envelope record.
  c.rows.push({ seq: "1", recorded_at: "2026-09-24T09:10:01.000000Z", principal: "joe-local",
    row_hash: "f".repeat(64), prev_hash: null, payload_sha256: "a".repeat(64), replayed: false,
    payload: structuredClone(CENSUS), key: "K" });
  const out = await executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "K", census: structuredClone(CENSUS) });
  assert.equal(out.replayed, true);
  assert.equal(out.row_hash, "f".repeat(64));
  assert.equal(c.registered.length, 0, "a replay registers no pending head");
});

test("an anchor that refuses the pending head refuses the write by name, so the row rolls back", async () => {
  const c = new CensusFake({ pending: { ok: false, error: "anchor_pending_unlinked_refused" } });
  const refusal = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-p2", census: structuredClone(CENSUS) }));
  assert.equal(refusal.error, "workflow_census_anchor_pending_refused");
  assert.equal(refusal.detail, "anchor_pending_unlinked_refused");
  const unbound = new CensusFake({ pending: null });
  const missing = await rejected(() => executeRegisteredTool(unbound, AGENT, "record-workflow-census",
    { idempotency_key: "k-p3", census: structuredClone(CENSUS) }));
  assert.equal(missing.error, "workflow_census_anchor_pending_refused");
  assert.equal(missing.detail, "anchor_not_bound");
});

// R4-C1: the Worker verifies the row the door says it inserted.

test("the Worker's row hash is the pinned cross-language rule (the vectors the Python selftest and PG gate pin)", async () => {
  const row = { seq: 7, recorded_at: "2026-09-24T04:10:00.123456Z", principal: "joe-local",
    db_session_principal: "carr_writer", prev_hash: "a".repeat(64), payload_sha256: "b".repeat(64) };
  assert.equal(await censusRowHash(row), "d06373a7bf4840bfbc2fab8bae4caca4e2cd42cf289c116174460af02864186f");
  assert.equal(await censusRowHash({ ...row, seq: 1, prev_hash: null }),
    "c557fc0fa00e3c53b091055baa3eb469fc015e72f38e1b36a5e36ffae3f5be9d");
});

test("canonical JSON sorts keys by code point, not UTF-16 unit, and refuses what would render differently", () => {
  const value = { "𝄞": 1, "ｚ": 2, "z": null, "10": true, "2": false, "": [], "B": { "b": "é\n\u0001\"\\/" } };
  assert.equal(canonicalCensusJson(value),
    '{"":[],"10":true,"2":false,"B":{"b":"é\\n\\u0001\\"\\\\/"},"z":null,"ｚ":2,"𝄞":1}');
  assert.ok(Object.keys(value).sort().indexOf("𝄞") < Object.keys(value).sort().indexOf("ｚ"),
    "JavaScript's default sort would have put the astral key first");
  assert.throws(() => canonicalCensusJson({ n: 2 ** 53 }), /canonical_json_number_refused/);
  assert.throws(() => canonicalCensusJson({ n: 1.5 }), /canonical_json_number_refused/);
  assert.throws(() => canonicalCensusJson({ n: undefined }), /canonical_json_value_refused/);
});

test("a replaced door that stores another payload is refused before anything is registered", async () => {
  const c = new CensusFake();
  c.substitutePayload = { ...structuredClone(CENSUS), summary: { workflows: 99, false_operational: 0 } };
  const refusal = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-sub", census: structuredClone(CENSUS) }));
  assert.equal(refusal.error, "workflow_census_row_unverified");
  assert.equal(refusal.field, "payload_sha256");
  assert.equal(c.registered.length, 0, "the forged row never becomes a pending head");
});

test("every field the Worker knows is checked: a door lying about any one of them is refused by name", async () => {
  const H = "c".repeat(64);
  const skewed = new Date(Date.now() - MAX_RECORDED_AT_SKEW_MS - 60000).toISOString().replace("Z", "000Z");
  for (const [field, lie] of [
    ["seq", { seq: "2" }],
    ["prev_hash", { prev_hash: H }],
    ["principal", { principal: "someone-else" }],
    ["payload_sha256", { payload_sha256: H }],
    ["row_hash", { row_hash: H }],
    ["recorded_at", { recorded_at: skewed }],
    ["recorded_at", { recorded_at: "2026-09-24T09:10:01Z" }],
    // Near the Worker's clock but not the database's microsecond format.
    ["recorded_at", { recorded_at: new Date().toISOString() }],
  ]) {
    const c = new CensusFake();
    c.lie = lie;
    const refusal = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
      { idempotency_key: `k-lie-${field}`, census: structuredClone(CENSUS) }));
    assert.equal(refusal.error, "workflow_census_row_unverified", field);
    assert.equal(refusal.field, field, JSON.stringify(lie));
    assert.equal(c.registered.length, 0, field);
  }
  // The honest door's row, but the Worker's own session principal differs: the
  // row hash no longer matches what the Worker computes.
  const c = new CensusFake();
  const other = { ...AGENT, trusted_principal: { session_principal: "app_writer" } };
  assert.equal((await rejected(() => executeRegisteredTool(c, other, "record-workflow-census",
    { idempotency_key: "k-sess", census: structuredClone(CENSUS) }))).field, "row_hash");
  const none = { ...AGENT, trusted_principal: undefined };
  assert.equal((await rejected(() => executeRegisteredTool(new CensusFake(), none, "record-workflow-census",
    { idempotency_key: "k-none", census: structuredClone(CENSUS) }))).field, "db_session_principal");
});

test("the verification is exact about the anchored predecessor", async () => {
  const census = structuredClone(CENSUS);
  const payload_sha256 = await censusPayloadSha256(census);
  const recorded_at = new Date().toISOString().replace("Z", "000Z");
  const base = { seq: 3, recorded_at, principal: "joe-local", db_session_principal: "carr_writer",
    prev_hash: "d".repeat(64), payload_sha256 };
  const row = { ...base, row_hash: await censusRowHash(base) };
  const args = { row, census, principal: "joe-local", dbSessionPrincipal: "carr_writer", nowMs: Date.now() };
  assert.deepEqual(await verifyInsertedCensusRow({ ...args, anchored: { seq: 2, row_hash: "d".repeat(64) } }), { ok: true });
  assert.equal((await verifyInsertedCensusRow({ ...args, anchored: { seq: 1, row_hash: "d".repeat(64) } })).field, "seq");
  assert.equal((await verifyInsertedCensusRow({ ...args, anchored: { seq: 2, row_hash: "e".repeat(64) } })).field,
    "prev_hash");
  assert.equal((await verifyInsertedCensusRow({ ...args, anchored: { seq: null, row_hash: null } })).field, "seq");
});

test("a census holding an integer past 2^53 is refused before the door", async () => {
  const c = new CensusFake();
  const census = { ...structuredClone(CENSUS), summary: { workflows: 2 ** 53 + 2 } };
  const refusal = await rejected(() => executeRegisteredTool(c, AGENT, "record-workflow-census",
    { idempotency_key: "k-big", census }));
  assert.equal(refusal.error, "workflow_census_payload_unsafe_integer_refused");
  assert.ok(!c.calls.some(call => call.sql.includes("ops.record_workflow_census")));
});
