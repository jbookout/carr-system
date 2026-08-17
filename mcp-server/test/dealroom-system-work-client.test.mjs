import test from "node:test";
import assert from "node:assert/strict";
import { createSystemWorkClient } from "../../dealroom/js/system-work-client.js";

function harness(responses = []) {
  const calls = [];
  const fetchImpl = async (path, init = {}) => {
    calls.push({ path, init, body: init.body ? JSON.parse(init.body) : null });
    const next = responses.shift() || { status: 200, body: { ok: true, data: {} } };
    return new Response(JSON.stringify(next.body), { status: next.status,
      headers: { "content-type": "application/json" } });
  };
  return { calls, client: createSystemWorkClient({ fetchImpl, uuid: () => "11111111-2222-4333-8444-555555555555" }) };
}

test("typed client never sends a generic verb, actor, tenant, or state", async () => {
  const { calls, client } = harness([
    { status: 200, body: { actor: { slug: "joe" }, csrf_token: "csrf", reauth_required: false } },
    { status: 200, body: { ok: true, data: { human_ref: "WR-000123" } } },
  ]);
  await client.bootstrap();
  await client.report({ situation: "Worker recovery", title: "Recovery receipt",
    desired_outcome: "Read it back", acceptance_criteria: [{ id: "criterion-1", text: "Receipt exists" }] });
  assert.equal(calls[1].path, "/api/system-work/report");
  assert.equal(calls[1].init.headers["x-carr-csrf"], "csrf");
  assert.deepEqual(Object.keys(calls[1].body).sort(),
    ["acceptance_criteria", "desired_outcome", "idempotency_key", "situation", "title"].sort());
  assert.equal(/verb|actor|tenant|state|command|sql/.test(JSON.stringify(calls[1].body)), false);
});

test("approval obtains a one-time challenge bound to the exact material", async () => {
  const { calls, client } = harness([
    { status: 200, body: { actor: { slug: "dell" }, csrf_token: "csrf", reauth_required: false } },
    { status: 200, body: { challenge: "challenge-1", expires_at: "2026-08-16T12:05:00Z" } },
    { status: 200, body: { ok: true, data: { state: "ready" } } },
  ]);
  await client.bootstrap();
  await client.acceptPlan("WR-000123", { base_version: 2, plan_hash: `sha256:${"a".repeat(64)}` });
  assert.deepEqual(calls[1].body, { action: "accept-ready-plan", human_ref: "WR-000123",
    base_version: 2, idempotency_key: "11111111-2222-4333-8444-555555555555",
    plan_hash: `sha256:${"a".repeat(64)}` });
  assert.equal(calls[2].path, "/api/system-work/WR-000123/plan/accept");
  assert.equal(calls[2].init.headers["x-carr-action-challenge"], "challenge-1");
  assert.equal(calls[2].body.idempotency_key, "11111111-2222-4333-8444-555555555555");
});

test("version conflict is a typed refusal and is never retried", async () => {
  const { calls, client } = harness([
    { status: 200, body: { actor: { slug: "joe" }, csrf_token: "csrf" } },
    { status: 409, body: { error: "version_conflict", message: "This request changed. No change was made." } },
  ]);
  await client.bootstrap();
  await assert.rejects(() => client.triage("WR-000123", { base_version: 1, classification: "operational" }),
    (error) => error.status === 409 && error.code === "version_conflict");
  assert.equal(calls.length, 2);
});
