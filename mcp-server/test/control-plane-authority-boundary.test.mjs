import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { authorityDsnForActor, callTool } from "../src/mcp.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "test" };

test("control-plane acceptance and retirement are explicit human authority verbs", () => {
  for (const name of ["accept-workflow", "disable-legacy-schedule"]) {
    assert.equal(TOOLS[name].humanOnly, true);
    assert.equal(TOOLS[name].authorityOnly, true);
  }
});

test("authority DSNs are partner-scoped with a single-seat fallback", () => {
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_JOE_URL: "joe-dsn" }, joe), "joe-dsn");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback" }, joe), "fallback");
  assert.equal(authorityDsnForActor({ CARR_DB_AUTHORITY_URL: "fallback" }, { slug: "codex", human: false }), null);
});

test("authority operation fails closed instead of falling back to writer credentials", async () => {
  await assert.rejects(() => callTool({ DATABASE_URL_WRITER: "writer-only" }, joe,
    "accept-workflow", { idempotency_key: "authority-missing", workflow_key: "fixture", mode: "shadow", receipt_ref: "r" }),
  e => e instanceof ToolError && e.payload.error === "authority_connection_unavailable");
});
