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
