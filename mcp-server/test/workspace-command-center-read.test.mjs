import test from "node:test";
import assert from "node:assert/strict";
import { readCommandCenterSummary } from "../src/workspace-command-center.js";

const clientFor = (work = {}) => ({ query: async (sql) => ({ rows: [sql.includes("v_deal_room_board") ? { owned_active: "2", owned_flagged: "1" } : { needs_viewer: "1", doc_at_work: "2", changed_count: "3", changed_at: "2026-08-24T14:30:00.000Z", legacy_unscoped_held: "0", legacy_unscoped_recent: "0", ...work }] }) });

test("Command Center returns the contract-shaped aggregate read with canonical freshness", async () => {
  const queries = [];
  const client = { async query(sql, params) {
    queries.push({ sql, params });
    return { rows: [sql.includes("v_deal_room_board") ? { owned_active: "2", owned_flagged: "1" } : { needs_viewer: "1", doc_at_work: "2", changed_count: "3", changed_at: "2026-08-24T14:30:00.000Z", legacy_unscoped_held: "0", legacy_unscoped_recent: "0" }] };
  } };
  const result = await readCommandCenterSummary({ client, actor: { slug: "joe" }, tenant: "carr-internal", correlationId: "corr-joe", now: () => new Date("2026-08-24T15:00:00.000Z") });
  assert.deepEqual(Object.keys(result).sort(), ["doc_at_work", "metrics", "needs_you_now", "this_week", "recent_calls", "recent_activity", "source", "viewer"].sort());
  assert.equal(result.viewer, "joe");
  assert.equal(result.metrics[0].owned_active_deals, 2);
  assert.equal(result.metrics[0].owned_flagged_deals, 1);
  assert.equal(result.source.source, "command_center");
  assert.equal(result.source.freshness, "fresh");
  assert.match(result.source.safe_explanation, /no-store request-time canonical/i);
  assert.equal(result.source.correlation_id, "corr-joe");
  assert.equal(result.source.valid_until, "2026-08-24T15:01:00.000Z");
  assert.equal(queries.length, 2);
  assert.equal(queries[1].params[0], "carr-internal");
  assert.equal(queries[1].params[1], "needs_joe");
  assert.match(queries[1].sql, /awaiting_release/);
  assert.match(queries[1].sql, /released/);
  assert.doesNotMatch(queries[1].sql, /select\s+.*title|select\s+.*notes/i);
});

test("Command Center fails closed when the actor is outside the bound CARR tenant", async () => {
  await assert.rejects(() => readCommandCenterSummary({ client: { query: async () => ({ rows: [] }) }, actor: { slug: "joe" }, tenant: "other-tenant", correlationId: "corr" }), /TENANT_SCOPE_REFUSED/);
});

test("needs_you_now is bound to the authenticated viewer", async () => {
  const dell = await readCommandCenterSummary({ client: clientFor(), actor: { slug: "dell" }, correlationId: "corr-dell" });
  assert.deepEqual(dell.needs_you_now, [{ kind: "owned_flagged_deals", count: 1, destination: "/deals?workspace=team&filter=flagged&owner=me" }]);
  const joe = await readCommandCenterSummary({ client: clientFor(), actor: { slug: "joe" }, correlationId: "corr-joe" });
  assert.equal(joe.needs_you_now[1].kind, "needs_joe_work");
});

test("legacy unscoped work withholds work cards but keeps deal metrics available", async () => {
  const result = await readCommandCenterSummary({ client: clientFor({ legacy_unscoped_held: "1" }), actor: { slug: "joe" }, correlationId: "corr-legacy" });
  assert.equal(result.metrics[0].owned_active_deals, 2);
  assert.equal(result.doc_at_work[0].state, "unavailable");
  assert.equal(result.recent_activity[0].state, "unavailable");
  assert.deepEqual(result.needs_you_now, [{ kind: "owned_flagged_deals", count: 1, destination: "/deals?workspace=team&filter=flagged&owner=me" }]);
});

test("correlation id is required and read failures stay typed", async () => {
  await assert.rejects(() => readCommandCenterSummary({ client: clientFor(), actor: { slug: "joe" } }), /INTERNAL_ERROR/);
  await assert.rejects(() => readCommandCenterSummary({ client: { query: async () => { throw Object.assign(new Error("db down"), { code: "DEPENDENCY_UNAVAILABLE" }); } }, actor: { slug: "joe" }, correlationId: "corr" }), /DEPENDENCY_UNAVAILABLE/);
  await assert.rejects(() => readCommandCenterSummary({ client: { query: async () => { throw new Error("unexpected"); } }, actor: { slug: "joe" }, correlationId: "corr" }), /INTERNAL_ERROR/);
});
