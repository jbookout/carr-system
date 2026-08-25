import assert from "node:assert/strict";
import test from "node:test";
import { evidenceActivationTools, isAttemptReliabilityVisibilityRefusal } from "../src/evidence-activation.js";

class TestToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.payload = payload;
  }
}

const actor = { slug: "joe", human: true };
const visibilityError = () => Object.assign(new Error("attempt reliability is not visible to tenant"), { code: "P0001" });

function readHandler(queryImpl) {
  const calls = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      return queryImpl(sql, params);
    },
  };
  const tools = evidenceActivationTools({
    withEnvelope: async (_client, _actor, _verb, _args, fn) => fn(),
    ToolError: TestToolError,
  });
  return { handler: tools["read-attempt-reliability"].handler, client, calls };
}

function allTools(queryImpl) {
  const calls = [];
  const client = { query: async (sql, params) => { calls.push({ sql, params }); return queryImpl(sql, params); } };
  const tools = evidenceActivationTools({
    withEnvelope: async (_client, _actor, verb, args, fn) => ({ verb, args, result: await fn() }),
    ToolError: TestToolError,
  });
  return { tools, client, calls };
}

test("a nonexistent attempt is a stable not-found refusal", async () => {
  const { handler, client } = readHandler((sql) => /set_config/.test(sql) ? { rows: [] } : { rows: [{ reliability: null }] });
  await assert.rejects(
    handler(client, actor, { attempt_id: "attempt:missing" }),
    (error) => error instanceof TestToolError && error.payload.error === "attempt_reliability_not_found",
  );
});

test("a foreign-tenant visibility refusal is mapped without exposing the database error", async () => {
  const { handler, client } = readHandler((sql) => /set_config/.test(sql) ? { rows: [] } : (() => { throw visibilityError(); })());
  await assert.rejects(
    handler(client, actor, { attempt_id: "attempt:foreign" }),
    (error) => error instanceof TestToolError && error.payload.error === "attempt_reliability_not_found" && !("cause" in error.payload),
  );
});

test("only the exact expected refusal is translated; unexpected database faults still escape", async () => {
  assert.equal(isAttemptReliabilityVisibilityRefusal(visibilityError()), true);
  assert.equal(isAttemptReliabilityVisibilityRefusal(Object.assign(new Error("attempt reliability is not visible to tenant"), { code: "XX000" })), false);
  assert.equal(isAttemptReliabilityVisibilityRefusal(Object.assign(new Error("different database failure"), { code: "P0001" })), false);

  const unexpected = Object.assign(new Error("connection reset"), { code: "08006" });
  const { handler, client } = readHandler((sql) => /set_config/.test(sql) ? { rows: [] } : (() => { throw unexpected; })());
  await assert.rejects(
    handler(client, actor, { attempt_id: "attempt:db-fault" }),
    (error) => error === unexpected,
  );
});

test("execution environment registry is a bounded read and lifecycle writes remain human authority only", async () => {
  const registry = [{ provider_ref: "environment-provider:hermes-local:v1", state: "active", grants_authority: false }];
  const { tools, client, calls } = allTools((sql) => {
    if (/read_execution_environment_providers/.test(sql)) return { rows: [{ providers: registry }] };
    if (/register_execution_environment_provider/.test(sql)) return { rows: [{ provider_ref: "environment-provider:fixture:v1", manifest_digest: `sha256:${"a".repeat(64)}`, state: "discovered", replayed: false }] };
    throw new Error(`unexpected query: ${sql}`);
  });
  assert.equal(tools["register-execution-environment-provider"].humanOnly, true);
  assert.equal(tools["register-execution-environment-provider"].authorityOnly, true);
  assert.equal(tools["attest-execution-environment-conformance"].humanOnly, true);
  assert.equal(tools["transition-execution-environment-provider"].authorityOnly, true);
  assert.deepEqual(await tools["read-execution-environment-providers"].handler(client, actor, {}), { ok: true, providers: registry });

  const manifest = { schema_version: "execution-environment-provider.v1" };
  const response = await tools["register-execution-environment-provider"].handler(client, actor, { manifest, idempotency_key: "11111111-1111-4111-8111-111111111111" });
  assert.equal(response.verb, "register-execution-environment-provider");
  assert.equal(response.result.state, "discovered");
  assert.equal(calls.some(({ sql }) => /register_execution_environment_provider/.test(sql)), true);
});

test("malformed provider lifecycle inputs refuse before touching the database", async () => {
  const { tools, client, calls } = allTools(() => { throw new Error("database should not be reached"); });
  await assert.rejects(
    tools["register-execution-environment-provider"].handler(client, actor, { manifest: {}, idempotency_key: "not-a-uuid" }),
    (error) => error instanceof TestToolError && error.payload.error === "execution_environment_manifest_invalid",
  );
  await assert.rejects(
    tools["transition-execution-environment-provider"].handler(client, actor, { provider_ref: "bad", expected_state: "active", target_state: "disabled", evidence_refs: [], idempotency_key: "bad" }),
    (error) => error instanceof TestToolError && error.payload.error === "execution_environment_transition_invalid",
  );
  assert.equal(calls.length, 0);
});
