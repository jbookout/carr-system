import test from "node:test";
import assert from "node:assert/strict";
import { createLeadBoardClient } from "../js/leads-client.js";

function jsonResponse(body, ok = true, status = 200) {
  return { ok, status, json: async () => body, text: async () => JSON.stringify(body) };
}

test("getLeadBoard uses same-origin cookie-authenticated MCP JSON-RPC", async () => {
  const calls = [];
  const client = createLeadBoardClient({ fetchImpl: async (path, init) => {
    calls.push({ path, init });
    return jsonResponse({ jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: JSON.stringify({ generated_at: "2026-08-24T12:00:00Z", stages: [], leads: [] }) }] } });
  } });
  const board = await client.getLeadBoard();
  assert.equal(board.leads.length, 0);
  assert.equal(calls[0].path, "/mcp");
  assert.equal(calls[0].init.credentials, "same-origin");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "lead-board", arguments: {} },
  });
});

test("typed tool errors preserve code and payload", async () => {
  const client = createLeadBoardClient({ fetchImpl: async () => jsonResponse({
    jsonrpc: "2.0", id: 1, result: { isError: true, content: [{ type: "text", text: JSON.stringify({ error: "not_authenticated", message: "Sign in required." }) }] },
  }) });
  await assert.rejects(client.getLeadBoard(), (error) => {
    assert.equal(error.code, "not_authenticated");
    assert.equal(error.payload.message, "Sign in required.");
    return true;
  });
});

test("moveLeadStage submits exact versioned stage-only update and does not retry conflicts", async () => {
  const calls = [];
  const client = createLeadBoardClient({ uuid: () => "test-key", fetchImpl: async (path, init) => {
    calls.push({ path, init });
    return jsonResponse({ jsonrpc: "2.0", id: 1, result: { isError: true, content: [{ type: "text", text: JSON.stringify({ error: "version_conflict", message: "Changed elsewhere." }) }] } });
  } });
  await assert.rejects(client.moveLeadStage({ id: "lead-1", registry_ref: "L-100", base_version: 7 }, "contacted"), (error) => {
    assert.equal(error.code, "version_conflict");
    return true;
  });
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].init.body).params, {
    name: "update-lead",
    arguments: { lead: "L-100", base_version: 7, fields: { stage: "contacted" }, idempotency_key: "test-key" },
  });
});
