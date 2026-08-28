import assert from "node:assert/strict";
import test from "node:test";
import { setWriterActorContext } from "../src/mcp.js";

function client() {
  const calls = [];
  return {
    calls,
    async query(sql, params) { calls.push({ sql, params }); return { rows: [{}] }; },
  };
}

test("writer transactions set server-derived actor context before Tour mutations", async () => {
  const human = client();
  await setWriterActorContext(human, {
    slug: "joe", human: true,
  });
  assert.match(human.calls[0].sql, /carr\.acting_actor_slug/);
  assert.match(human.calls[0].sql, /carr\.verified_human_actor_slug/);
  assert.deepEqual(human.calls[0].params, ["joe", "joe"]);

  const sponsored = client();
  await setWriterActorContext(sponsored, {
    slug: "codex", human: false, authorization_class: "sponsored_agent",
  });
  assert.deepEqual(sponsored.calls[0].params, ["codex", ""]);
});
