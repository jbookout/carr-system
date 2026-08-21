import test from "node:test";
import assert from "node:assert/strict";

import { TOOLS } from "../src/tools.js";

const sponsored = (runtime, sponsor, via = "local-token") => ({
  slug: runtime, human: false, via,
  sponsoring_human_slug: sponsor, human_slug: sponsor, sponsor_required: false,
});

test("registered search-doctrine resolves joe-local sponsorship to Joe's human actor", async () => {
  const calls = [];
  const client = {
    query: async (sql, params) => {
      calls.push({ sql, params });
      if (/select id from actor/.test(sql)) return { rows: [{ id: "joe-human-id" }] };
      return { rows: [{ section_key: "diagnosis-checklist", final_score: 1 }] };
    },
  };
  const result = await TOOLS["search-doctrine"].handler(client, sponsored("joe-local", "joe"), {
    q: "record layer outage diagnosis",
    content_classes: ["runbook"],
    limit: 3,
  });

  assert.deepEqual(result.hits, [{ section_key: "diagnosis-checklist", final_score: 1 }]);
  const retrievalCall = calls.find(call => /search_doctrine_situations/.test(call.sql));
  assert.ok(retrievalCall, "search-doctrine must call the shared database ranker");
  assert.deepEqual(retrievalCall.params,
    ["record layer outage diagnosis", "joe-human-id", ["runbook"], 3, null]);
  const scopeCall = calls.find(call => /select id from actor/.test(call.sql));
  assert.deepEqual(scopeCall.params, ["joe"]);
  assert.match(scopeCall.sql, /kind='human' and active=true/);
  assert.equal(calls.some(call => /ts_rank_cd|doctrine_revision/.test(call.sql)), false,
    "the verb must not retain a duplicate ranking query");
});

test("registered search-doctrine resolves Dell sponsorship only to Dell's human actor", async () => {
  const calls = [];
  const client = { query: async (sql, params) => {
    calls.push({ sql, params });
    if (/select id from actor/.test(sql)) return { rows: [{ id: "dell-human-id" }] };
    return { rows: [] };
  } };
  await TOOLS["search-doctrine"].handler(client, sponsored("codex", "dell", "oauth-google"), {
    q: "personal operating preference", limit: 5,
  });
  assert.deepEqual(calls[0].params, ["dell"]);
  assert.deepEqual(calls.find(call => /search_doctrine_situations/.test(call.sql)).params,
    ["personal operating preference", "dell-human-id", null, 5, null]);
});

test("unsponsored search is shared-only and invalid required sponsorship is refused", async () => {
  const sharedCalls = [];
  const sharedClient = { query: async (sql, params) => {
    sharedCalls.push({ sql, params });
    return { rows: [] };
  } };
  await TOOLS["search-doctrine"].handler(sharedClient,
    { slug: "codex", human: false, via: "agent-token", sponsoring_human_slug: null },
    { q: "shared doctrine", limit: 2 });
  assert.equal(sharedCalls.some(call => /select id from actor/.test(call.sql)), false);
  assert.deepEqual(sharedCalls[0].params, ["shared doctrine", null, null, 2, null]);

  let queried = false;
  const refusedClient = { query: async () => { queried = true; return { rows: [] }; } };
  await assert.rejects(
    TOOLS["search-doctrine"].handler(refusedClient,
      { slug: "codex", human: false, via: "oauth-google",
        sponsoring_human_slug: null, sponsor_required: true },
      { q: "personal doctrine" }),
    // Typed since 2026-08-21: the refusal carries a payload rather than a
    // colon-packed message string, so mcp.js can name it instead of reporting
    // the bare "internal_error" it gives any untyped throw.
    error => {
      assert.equal(error.payload?.error, "retrieval_scope_refused");
      assert.equal(error.payload?.reason, "missing_or_ambiguous_sponsor");
      return true;
    },
  );
  assert.equal(queried, false, "invalid sponsor must be refused before any visibility query");

  const inactiveCalls = [];
  const inactiveClient = { query: async (sql, params) => {
    inactiveCalls.push({ sql, params });
    return { rows: [] };
  } };
  await assert.rejects(
    TOOLS["search-doctrine"].handler(inactiveClient, sponsored("joe-local", "joe"),
      { q: "personal doctrine" }),
    // The payload now also reports WHICH sponsor and HOW MANY rows matched,
    // because 0 and 2 are different production faults and the old message
    // distinguished neither.
    error => {
      assert.equal(error.payload?.error, "retrieval_scope_refused");
      assert.equal(error.payload?.reason, "sponsor_not_active_human");
      assert.equal(error.payload?.sponsor, "joe");
      assert.equal(error.payload?.matching_rows, 0);
      return true;
    },
  );
  assert.equal(inactiveCalls.length, 1);
  assert.match(inactiveCalls[0].sql, /kind='human' and active=true/);
});

test("situation curation verbs are registered in the MCP tool surface", () => {
  for (const name of [
    "propose-retrieval-concept", "propose-retrieval-phrase",
    "propose-retrieval-mapping", "propose-retrieval-retirement",
    "approve-retrieval-proposals", "retire-retrieval-curation",
  ]) assert.ok(TOOLS[name], `${name} is registered`);
});
