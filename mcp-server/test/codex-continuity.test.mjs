import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { codexContinuityTools } from "../src/codex-continuity.js";
import { agentActorForToken, actorFromProps } from "../src/identity.js";

class TestToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}
const actor = { id: "actor-codex", slug: "codex", human: false, native_agent_verified: true,
  sponsoring_human_slug: "joe", via: "oauth-google" };
const key = "00000000-0000-4000-8000-000000000001";
const state = { objective: "keep working", next_action: "verify result", progress: [{ text: "started", refs: [] }] };

function tools() {
  return codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async () => {},
  });
}

test("checkpoint normalizes the database bigint version and uses it in the revision", async () => {
  const statements = [];
  const events = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state, cursor: null, checkpoint_version: "1" }] };
    if (sql.startsWith("insert into codex_continuity_revision")) return { rows: [] };
    return { rows: [] };
  } };
  const checkpointTools = codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(),
    writeEvent: async (...args) => { events.push(args); },
  });
  const out = await checkpointTools["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    expected_version: 0, state,
  });
  assert.equal(out.checkpoint.checkpoint_version, 1);
  assert.match(statements.find(x => x.sql.startsWith("select pg_advisory")).sql, /hashtextextended/);
  assert.match(statements.find(x => x.sql.startsWith("insert into codex_continuity_revision")).sql, /state/);
  assert.equal(statements.find(x => x.sql.startsWith("insert into codex_continuity_checkpoint")).params[6], null);
  assert.equal(statements.find(x => x.sql.startsWith("insert into codex_continuity_revision")).params[3], null);
  assert.equal(statements.find(x => x.sql.startsWith("insert into codex_continuity_revision")).params[1], 1);
  assert.equal(events[0][5].new.version, 1);
});

test("recovery scopes owner through actor slug lookup, never a raw slug-to-uuid comparison", async () => {
  const statements = [];
  const client = { query: async (sql, params) => {
    statements.push({ sql, params });
    if (sql.includes("with prompts as")) return { rows: [{ turns: [], omitted: 0, coverage_known: true }] };
    return { rows: [] };
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
  });
  assert.equal(out.found, false);
  assert.ok(statements.every(statement => /owner_actor_id=\(select id from actor where slug=\$2\)/.test(statement.sql)));
  assert.ok(statements.every(statement => statement.params[1] === "joe"));
  assert.deepEqual(out.unincorporated_user_turns, []);
  assert.equal(out.unincorporated_user_turns_omitted, 0);
  assert.equal(out.source_coverage, "known");
});

test("checkpoint rejects stale version and immutable task binding before update", async () => {
  const client = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{ id: "cp-1", project_id: "other", cwd: "/repo", checkpoint_version: 2 }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    throw new Error("must not update a changed binding");
  } };
  await assert.rejects(() => tools()["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    expected_version: 2, state,
  }), error => error.payload?.error === "codex_checkpoint_binding_conflict");
});

test("ten repeated recovery cycles preserve correction and next action in one bounded task", async () => {
  let version = 0;
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return version ? { rows: [{ id: "cp-1", project_id: "p", cwd: "/repo", checkpoint_version: String(version) }] } : { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint")) { version = 1; return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state: params[5], checkpoint_version: String(version) }] }; }
    if (sql.startsWith("update codex_continuity_checkpoint")) { version += 1; return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state: params[3], checkpoint_version: String(version) }] }; }
    return { rows: [] };
  } };
  const verb = tools()["codex-checkpoint"];
  let current = 0;
  for (let i = 0; i < 10; i += 1) {
    const out = await verb.handler(client, actor, {
      idempotency_key: `${key}-${i + 2}`, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
      expected_version: current, state: {
        ...state,
        latest_corrections: [{ text: "rejected approach", refs: ["turn:user-correction"] }],
        decisions: [{ text: "keep the native source", why: "it preserves provenance",
          refs: ["decision:native-source"] }],
        next_action: "verify final output",
      },
    });
    current = out.checkpoint.checkpoint_version;
  }
  assert.equal(current, 10);
  assert.ok(JSON.stringify(state).length < 24000);
});

test("checkpoint refuses malformed or unsafe database bigint versions before CAS", async () => {
  const checkpoint = tools()["codex-checkpoint"];
  assert.equal(checkpoint.inputSchema.properties.expected_version.maximum,
    Number.MAX_SAFE_INTEGER);
  for (const checkpoint_version of ["01", "9007199254740992", true]) {
    let updated = false;
    const client = { query: async sql => {
      if (sql.startsWith("select pg_advisory")) return { rows: [] };
      if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
        id: "cp-1", project_id: "p", cwd: "/repo", checkpoint_version,
      }] };
      if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
      if (sql.startsWith("update codex_continuity_checkpoint")) updated = true;
      return { rows: [] };
    } };
    await assert.rejects(() => checkpoint.handler(client, actor, {
      idempotency_key: key, runtime: "codex", native_task_id: "task-1",
      project_id: "p", cwd: "/repo", expected_version: 1, state,
    }), error => error.payload?.error === "codex_checkpoint_version_invalid");
    assert.equal(updated, false);
  }
  const client = { query: async () => { throw new Error("unsafe input must not query"); } };
  await assert.rejects(() => checkpoint.handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1",
    project_id: "p", cwd: "/repo", expected_version: Number.MAX_SAFE_INTEGER + 1, state,
  }), error => error.payload?.error === "codex_checkpoint_expected_version_invalid");

  let exhaustedUpdate = false;
  const exhaustedClient = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-max", project_id: "p", cwd: "/repo",
      checkpoint_version: String(Number.MAX_SAFE_INTEGER),
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("update codex_continuity_checkpoint")) exhaustedUpdate = true;
    return { rows: [] };
  } };
  await assert.rejects(() => checkpoint.handler(exhaustedClient, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1",
    project_id: "p", cwd: "/repo", expected_version: Number.MAX_SAFE_INTEGER, state,
  }), error => error.payload?.error === "codex_checkpoint_version_exhausted");
  assert.equal(exhaustedUpdate, false, "CAS never increments beyond the JSON safe-integer boundary");
});

test("state list items are closed and required text is documented in the schema", async () => {
  const checkpoint = tools()["codex-checkpoint"];
  assert.equal(checkpoint.inputSchema.properties.state.additionalProperties, false);
  assert.deepEqual(checkpoint.inputSchema.properties.state.properties.progress.items.required, ["text"]);
  const correctionSchema = checkpoint.inputSchema.properties.state.properties.latest_corrections.items;
  assert.deepEqual(correctionSchema.required, ["text", "refs"]);
  assert.equal(correctionSchema.properties.refs.minItems, 1);
  assert.equal(correctionSchema.properties.refs.items.minLength, 1);
  assert.equal(correctionSchema.properties.refs.items.pattern, "\\S");
  const decisionSchema = checkpoint.inputSchema.properties.state.properties.decisions.items;
  assert.deepEqual(decisionSchema.required, ["text", "why", "refs"]);
  assert.equal(decisionSchema.properties.why.minLength, 1);
  assert.equal(decisionSchema.properties.why.pattern, "\\S");
  assert.equal(decisionSchema.properties.refs.minItems, 1);
  assert.equal(decisionSchema.properties.refs.items.minLength, 1);
  assert.equal(decisionSchema.properties.refs.items.pattern, "\\S");
  const client = { query: async () => { throw new Error("must not query invalid state"); } };
  for (const invalid of [
    { ...state, progress: [{ refs: [] }] },
    { ...state, progress: [{ text: "ok", unexpected: true }] },
    { ...state, latest_corrections: [{ text: "uncited correction" }] },
    { ...state, latest_corrections: [{ text: "uncited correction", refs: [] }] },
    { ...state, latest_corrections: [{ text: "blank citation", refs: ["  "] }] },
    { ...state, decisions: [{ text: "unsupported decision", refs: ["decision:1"] }] },
    { ...state, decisions: [{ text: "unsupported decision", why: "  ", refs: ["decision:1"] }] },
    { ...state, decisions: [{ text: "uncited decision", why: "because" }] },
    { ...state, decisions: [{ text: "uncited decision", why: "because", refs: [] }] },
  ]) {
    await assert.rejects(() => checkpoint.handler(client, actor, {
      idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
      expected_version: 0, state: invalid,
    }), error => error.payload?.error === "codex_checkpoint_field_invalid");
  }
});

test("Claude and unverified callers are rejected before database use", async () => {
  const client = { query: async () => { throw new Error("must not query"); } };
  for (const bad of [
    { ...actor, slug: "claude" },
    { ...actor, native_agent_verified: false },
  ]) {
    await assert.rejects(() => tools()["codex-read-recovery"].handler(client, bad, {
      runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    }), error => error.payload?.error === "codex_native_principal_required");
  }
});

test("Codex OAuth and sponsored local transport share the verified owner key", () => {
  const local = agentActorForToken("Bearer local-secret", JSON.stringify({ "joe-local": "local-secret" }), "local-token");
  const oauth = actorFromProps({ slug: "codex", human: false, client_id: "client-1", sponsoring_human_slug: "joe", via: "oauth-google" }, JSON.stringify({ "client-1": "codex" }));
  assert.equal(local.native_agent_verified, true);
  assert.equal(local.sponsoring_human_slug, "joe");
  assert.equal(oauth.native_agent_verified, true);
  assert.equal(oauth.sponsoring_human_slug, "joe");
});

test("event retries use insert-do-nothing and reject a conflicting deterministic payload", async () => {
  const event = { id: "ev-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: null, transcript_ref: null };
  let mode = "insert";
  const client = { query: async (sql) => {
    if (sql.includes("select project_id,cwd from codex_continuity_checkpoint")) return { rows: [] };
    if (sql.includes("insert into codex_continuity_event")) return { rows: mode === "insert" ? [event] : [] };
    if (sql.includes("from codex_continuity_event where")) return { rows: [{ ...event, event_type: "different" }] };
    return { rows: [] };
  } };
  let writes = 0;
  const eventTools = codexContinuityTools({
    ToolError: TestToolError, assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => { writes += 1; },
  });
  const args = { idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact" };
  await eventTools["codex-record-event"].handler(client, actor, args);
  mode = "conflict";
  await assert.rejects(() => eventTools["codex-record-event"].handler(client, actor, { ...args, event_type: "post_compact" }), error => error.payload?.error === "codex_event_key_conflict");
  assert.equal(writes, 1);
});

test("event replay treats reordered JSONB cursor keys as the same payload", async () => {
  const event = { id: "ev-2", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: { b: 2, a: 1 }, transcript_ref: null };
  let mode = "insert";
  const client = { query: async sql => {
    if (sql.includes("select project_id,cwd from codex_continuity_checkpoint")) return { rows: [] };
    if (sql.includes("insert into codex_continuity_event")) return { rows: mode === "insert" ? [event] : [] };
    if (sql.includes("from codex_continuity_event where")) return { rows: [event] };
    return { rows: [] };
  } };
  let writes = 0;
  const eventTools = codexContinuityTools({
    ToolError: TestToolError, assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (_c, _a, _v, _args, fn) => fn(), writeEvent: async () => { writes += 1; },
  });
  const args = { idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: { a: 1, b: 2 } };
  await eventTools["codex-record-event"].handler(client, actor, args);
  mode = "retry";
  await eventTools["codex-record-event"].handler(client, actor, args);
  assert.equal(writes, 2);
});

test("event rejects a task binding change once a checkpoint exists", async () => {
  const client = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{ id: "cp-1", project_id: "p", cwd: "/original", checkpoint_version: 1 }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    throw new Error("must not insert a mismatched event");
  } };
  await assert.rejects(() => tools()["codex-record-event"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo", event_type: "pre_compact",
  }), error => error.payload?.error === "codex_event_binding_conflict");
});

test("the first lifecycle event binds checkpoint identity before any checkpoint exists", async () => {
  const client = { query: async sql => {
    if (sql.startsWith("select pg_advisory")) return { rows: [] };
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event"))
      return { rows: [{ project_id: "original-project", cwd: "/original" }] };
    throw new Error("must not insert a checkpoint against the first event binding");
  } };
  await assert.rejects(() => tools()["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "other-project", cwd: "/other",
    expected_version: 0, state,
  }), error => error.payload?.error === "codex_checkpoint_binding_conflict");
});

test("checkpoint and event writers take the identical tenant-owner-task advisory lock", async () => {
  const lockParams = [];
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select pg_advisory")) { lockParams.push(params); return { rows: [] }; }
    if (sql.startsWith("select id,native_task_id,project_id") || sql.startsWith("select project_id,cwd from codex_continuity_event")) return { rows: [] };
    if (sql.startsWith("insert into codex_continuity_checkpoint"))
      return { rows: [{ id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", state, cursor: null, checkpoint_version: 1 }] };
    if (sql.startsWith("insert into codex_continuity_revision")) return { rows: [] };
    if (sql.includes("insert into codex_continuity_event"))
      return { rows: [{ id: "ev-1", native_task_id: "task-2", project_id: "p", cwd: "/repo", event_type: "pre_compact", cursor: null, transcript_ref: null }] };
    return { rows: [] };
  } };
  await tools()["codex-checkpoint"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
    expected_version: 0, state,
  });
  await tools()["codex-record-event"].handler(client, actor, {
    idempotency_key: key, runtime: "codex", native_task_id: "task-2", project_id: "p", cwd: "/repo",
    event_type: "pre_compact",
  });
  assert.deepEqual(lockParams, [["carr-internal:joe:task-1"], ["carr-internal:joe:task-2"]]);
});

test("recovery returns bounded pending prompt receipts, highwater, omission count and known coverage", async () => {
  const prompt = {
    event_type: "user_prompt_submit", cursor: { byte_offset: 900, checkpoint_version: 7 },
    transcript_ref: "/native/rollout.jsonl", created_at: "2026-09-05T12:00:00Z",
  };
  let pendingSql;
  let pendingParams;
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-1", native_task_id: "task-1", project_id: "p", cwd: "/repo", checkpoint_version: "7",
      state, cursor: { byte_offset: 800 }, updated_at: "2026-09-05T11:59:00Z",
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event"))
      return { rows: [{ project_id: "p", cwd: "/repo" }] };
    if (sql.startsWith("select cursor from codex_continuity_event"))
      return { rows: [{ cursor: { byte_offset: 900, checkpoint_version: 7 } }] };
    if (sql.includes("with prompts as")) {
      pendingSql = sql;
      pendingParams = params;
      return { rows: [{ turns: [prompt], omitted: 4, coverage_known: true }] };
    }
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
  });
  assert.deepEqual(out.source_highwater, { byte_offset: 900, checkpoint_version: 7 });
  assert.equal(out.checkpoint.native_task_id, "task-1");
  assert.equal(out.checkpoint.checkpoint_version, 7);
  assert.equal(pendingParams[5], 7);
  assert.deepEqual(out.unincorporated_user_turns, [prompt]);
  assert.equal(out.unincorporated_user_turns_omitted, 4);
  assert.equal(out.source_coverage, "known");
  assert.match(pendingSql, /order by created_at desc,id desc limit \$7/);
  assert.match(pendingSql, /order by created_at asc,id asc\) from selected/);
});

test("legacy prompt receipts remain visible and make recovery coverage unknown", async () => {
  let pendingSql;
  const client = { query: async (sql, params) => {
    if (sql.startsWith("select id,native_task_id,project_id")) return { rows: [{
      id: "cp-1", project_id: "p", cwd: "/repo", checkpoint_version: 8, state, cursor: null,
    }] };
    if (sql.startsWith("select project_id,cwd from codex_continuity_event") || sql.startsWith("select cursor from codex_continuity_event")) return { rows: [] };
    if (sql.includes("with prompts as")) {
      pendingSql = sql;
      assert.equal(params[5], 8);
      assert.equal(params[6], 25);
      return { rows: [{ turns: [{ event_type: "user_prompt_submit", cursor: {}, transcript_ref: null, created_at: "2026-09-05T12:00:00Z" }], omitted: 0, coverage_known: false }] };
    }
    throw new Error(`unexpected SQL: ${sql}`);
  } };
  const out = await tools()["codex-read-recovery"].handler(client, actor, {
    runtime: "codex", native_task_id: "task-1", project_id: "p", cwd: "/repo",
  });
  assert.match(pendingSql, /not version_known/);
  assert.equal(out.unincorporated_user_turns.length, 1);
  assert.equal(out.source_coverage, "unknown");
});

test("real PostgreSQL handlers serialize races and isolate tenant, owner, and task reads and writes", {
  skip: !process.env.CARR_CONTINUITY_EPHEMERAL_DATABASE_URL,
  timeout: 30000,
}, async () => {
  const driverModule = process.env.CARR_CONTINUITY_DATABASE_DRIVER_MODULE || "@neondatabase/serverless";
  const databaseDriver = await import(driverModule);
  const Pool = databaseDriver.Pool || databaseDriver.default?.Pool;
  assert.equal(typeof Pool, "function", `database driver ${driverModule} must export Pool`);
  // Revisions are deliberately undeletable.  This must target a disposable
  // database that the test runner destroys after the process exits.
  const pool = new Pool({ connectionString: process.env.CARR_CONTINUITY_EPHEMERAL_DATABASE_URL });
  const setup = await pool.connect();
  const task = `continuity-test-${randomUUID()}`;
  const bindingTask = `continuity-binding-${randomUUID()}`;
  const otherTask = `continuity-other-${randomUUID()}`;
  const foreignTenant = `continuity-foreign-${randomUUID()}`;
  let dbActor;
  let dellActor;
  let joeOwnerId;
  try {
    const actorRows = await setup.query("select id,slug from actor where slug in ('codex','joe','dell')");
    const actorsBySlug = Object.fromEntries(actorRows.rows.map(row => [row.slug, row.id]));
    assert.ok(actorsBySlug.joe && actorsBySlug.dell,
      "integration database needs joe and dell owner rows");
    dbActor = { ...actor, id: actorsBySlug.codex || actorsBySlug.joe };
    dellActor = { ...dbActor, sponsoring_human_slug: "dell" };
    joeOwnerId = actorsBySlug.joe;
  } finally {
    setup.release();
  }

  const transactionalTools = codexContinuityTools({
    ToolError: TestToolError,
    assertNoCallerAuthorityFields: () => {},
    withEnvelope: async (client, _actor, _verb, _args, fn) => {
      await client.query("begin");
      try {
        const result = await fn();
        await client.query("commit");
        return result;
      } catch (error) {
        await client.query("rollback");
        throw error;
      }
    },
    writeEvent: async () => {},
  });
  const invokeAs = async (invokingActor, verb, args) => {
    const client = await pool.connect();
    try { return await transactionalTools[verb].handler(client, invokingActor, args); }
    finally { client.release(); }
  };
  const invoke = (verb, args) => invokeAs(dbActor, verb, args);
  const base = { runtime: "codex", native_task_id: task, project_id: "integration", cwd: "/integration" };

  try {
    const cas = await Promise.allSettled([
      invoke("codex-checkpoint", { ...base, idempotency_key: randomUUID(), expected_version: 0, state }),
      invoke("codex-checkpoint", { ...base, idempotency_key: randomUUID(), expected_version: 0,
        state: { ...state, next_action: "the other concurrent writer" } }),
    ]);
    assert.equal(cas.filter(result => result.status === "fulfilled").length, 1,
      cas.map(result => result.status === "fulfilled" ? "fulfilled" : String(result.reason)).join("; "));
    assert.equal(cas.filter(result => result.status === "rejected" &&
      result.reason.payload?.error === "codex_checkpoint_version_conflict").length, 1);

    const eventKey = randomUUID();
    const eventArgs = { ...base, idempotency_key: eventKey, event_type: "user_prompt_submit",
      cursor: { byte_offset: 10, checkpoint_version: 1 }, transcript_ref: "/integration/rollout.jsonl" };
    const replay = await Promise.all([
      invoke("codex-record-event", eventArgs),
      invoke("codex-record-event", eventArgs),
    ]);
    assert.equal(replay[0].event.id, replay[1].event.id);
    const recovered = await invoke("codex-read-recovery", base);
    assert.equal(recovered.found, true);
    assert.equal(recovered.unincorporated_user_turns.length, 1);
    assert.equal(recovered.unincorporated_user_turns_omitted, 0);
    assert.equal(recovered.source_coverage, "known");
    assert.equal(recovered.source_highwater.checkpoint_version, 1);
    await assert.rejects(() => invoke("codex-record-event", { ...eventArgs, event_type: "pre_compact" }),
      error => error.payload?.error === "codex_event_key_conflict");

    const firstBindingRace = await Promise.allSettled([
      invoke("codex-record-event", { ...base, native_task_id: bindingTask, project_id: "event-first",
        cwd: "/event-first", idempotency_key: randomUUID(), event_type: "pre_compact" }),
      invoke("codex-checkpoint", { ...base, native_task_id: bindingTask, project_id: "checkpoint-first",
        cwd: "/checkpoint-first", idempotency_key: randomUUID(), expected_version: 0, state }),
    ]);
    assert.equal(firstBindingRace.filter(result => result.status === "fulfilled").length, 1);
    assert.equal(firstBindingRace.filter(result => result.status === "rejected" &&
      /codex_(event|checkpoint)_binding_conflict/.test(result.reason.payload?.error)).length, 1);

    const foreign = await pool.connect();
    try {
      await foreign.query(
        `insert into codex_continuity_checkpoint
          (organization_tenant_id,owner_actor_id,native_task_id,project_id,cwd,state)
         values ($1,$2,$3,'foreign-project','/foreign',$4::jsonb)`,
        [foreignTenant, joeOwnerId, otherTask, JSON.stringify({ ...state, next_action: "foreign only" })]);
    } finally {
      foreign.release();
    }

    const internalOther = { ...base, native_task_id: otherTask };
    assert.equal((await invoke("codex-read-recovery", internalOther)).found, false,
      "foreign-tenant state must not be readable through the server-derived tenant");
    await invoke("codex-checkpoint", { ...internalOther, idempotency_key: randomUUID(),
      expected_version: 0, state: { ...state, next_action: "internal only" } });

    assert.equal((await invokeAs(dellActor, "codex-read-recovery", base)).found, false,
      "another sponsor must not read Joe's checkpoint");
    await invokeAs(dellActor, "codex-checkpoint", { ...base, project_id: "dell-project", cwd: "/dell",
      idempotency_key: randomUUID(), expected_version: 0,
      state: { ...state, next_action: "dell only" } });

    const thirdTask = { ...base, native_task_id: `continuity-third-${randomUUID()}` };
    assert.equal((await invoke("codex-read-recovery", thirdTask)).found, false,
      "another native task must not read the original task");
    await invoke("codex-checkpoint", { ...thirdTask, idempotency_key: randomUUID(),
      expected_version: 0, state: { ...state, next_action: "third task only" } });

    await invoke("codex-checkpoint", { ...base, idempotency_key: randomUUID(), expected_version: 1,
      state: { ...state, next_action: "joe version two" } });
    const joeRecovered = await invoke("codex-read-recovery", base);
    const dellRecovered = await invokeAs(dellActor, "codex-read-recovery",
      { ...base, project_id: "dell-project", cwd: "/dell" });
    const otherRecovered = await invoke("codex-read-recovery", internalOther);
    const thirdRecovered = await invoke("codex-read-recovery", thirdTask);
    assert.equal(Number(joeRecovered.checkpoint.checkpoint_version), 2);
    assert.equal(joeRecovered.checkpoint.state.next_action, "joe version two");
    assert.equal(Number(dellRecovered.checkpoint.checkpoint_version), 1);
    assert.equal(dellRecovered.checkpoint.state.next_action, "dell only");
    assert.equal(Number(otherRecovered.checkpoint.checkpoint_version), 1);
    assert.equal(otherRecovered.checkpoint.state.next_action, "internal only");
    assert.equal(Number(thirdRecovered.checkpoint.checkpoint_version), 1);
    assert.equal(thirdRecovered.checkpoint.state.next_action, "third task only");

    const verify = await pool.connect();
    try {
      const rows = await verify.query(
        `select organization_tenant_id,project_id,checkpoint_version,state->>'next_action' as next_action
           from codex_continuity_checkpoint
          where native_task_id=$1 order by organization_tenant_id`, [otherTask]);
      assert.deepEqual(rows.rows.map(row => ({
        tenant: row.organization_tenant_id, project: row.project_id,
        version: Number(row.checkpoint_version), next: row.next_action,
      })), [
        { tenant: "carr-internal", project: "integration", version: 1, next: "internal only" },
        { tenant: foreignTenant, project: "foreign-project", version: 1, next: "foreign only" },
      ]);
    } finally {
      verify.release();
    }
  } finally {
    await pool.end();
  }
});
