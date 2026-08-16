import test from "node:test";
import assert from "node:assert/strict";

import { TOOLS } from "../src/tools.js";

test("search-doctrine delegates to the sole situation retrieval database function", async () => {
  const calls = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      if (/select id from actor/.test(sql)) return { rows: [{ id: "actor-id" }] };
      return { rows: [{ section_key: "diagnosis-checklist", final_score: 1 }] };
    },
  };
  const result = await TOOLS["search-doctrine"].handler(client, { slug: "joe-local" }, {
    q: "record layer outage diagnosis",
    content_classes: ["runbook"],
    limit: 3,
  });

  assert.deepEqual(result.hits, [{ section_key: "diagnosis-checklist", final_score: 1 }]);
  const retrievalCall = calls.find(call => /search_doctrine_situations/.test(call.sql));
  assert.ok(retrievalCall, "search-doctrine must call the shared database ranker");
  assert.deepEqual(retrievalCall.params,
    ["record layer outage diagnosis", "actor-id", ["runbook"], 3, null]);
  assert.equal(calls.some(call => /ts_rank_cd|doctrine_revision/.test(call.sql)), false,
    "the verb must not retain a duplicate ranking query");
});

test("situation curation verbs are registered in the MCP tool surface", () => {
  for (const name of [
    "propose-retrieval-concept", "propose-retrieval-phrase",
    "propose-retrieval-mapping", "propose-retrieval-retirement",
    "approve-retrieval-proposals", "retire-retrieval-curation",
  ]) assert.ok(TOOLS[name], `${name} is registered`);
});
