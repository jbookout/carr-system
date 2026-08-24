import test from "node:test";
import assert from "node:assert/strict";
import { aggregateCardState, summarizeWorkspacePayload, validWorkspacePayload } from "../js/workspace-command-center-model.js";
import { readCommandCenterSummary } from "../../mcp-server/src/workspace-command-center.js";

const payload = (overrides = {}) => ({
  viewer: "joe",
  needs_you_now: [{ kind: "owned_flagged_deals", count: 2, destination: "/deals?workspace=team&filter=flagged&owner=me" }],
  this_week: [],
  metrics: [{ owned_active_deals: 4, owned_flagged_deals: 2, source: { source: "v_deal_room_board", observed_at: "2099-08-24T15:00:00.000Z", valid_until: "2099-08-24T15:01:00.000Z", freshness: "fresh", correlation_id: "corr-test" } }],
  recent_calls: [],
  doc_at_work: [{ kind: "active_nonhuman_work", count: 2, source: { source: "ops.work_request", observed_at: "2099-08-24T15:00:00.000Z", valid_until: "2099-08-24T15:01:00.000Z", freshness: "fresh", correlation_id: "corr-test" } }],
  recent_activity: [{ kind: "changed_work", count: 3, observed_at: "2099-08-24T14:30:00.000Z", source: { source: "ops.work_request", observed_at: "2099-08-24T15:00:00.000Z", valid_until: "2099-08-24T15:01:00.000Z", freshness: "fresh", correlation_id: "corr-test" } }],
  source: { source: "command_center", source_ref: "v_deal_room_board+ops.work_request", observed_at: "2099-08-24T15:00:00.000Z", valid_until: "2099-08-24T15:01:00.000Z", freshness: "fresh", correlation_id: "corr-test", safe_explanation: "Fresh because this is a no-store request-time canonical database aggregate; valid for 60 seconds." },
  ...overrides,
});

test("malformed aggregate payload is unavailable and never becomes a zero", () => {
  assert.equal(validWorkspacePayload({}), false);
  assert.deepEqual(summarizeWorkspacePayload({}), { state: "unavailable", count: null, active: null });
});

test("stale aggregate payload withholds counts while preserving the owning destination", () => {
  const result = summarizeWorkspacePayload(payload({ source: { ...payload().source, freshness: "stale" } }));
  assert.deepEqual(result, { state: "stale", count: null, active: null, destination: "/deals?workspace=team&filter=flagged&owner=me" });
});

test("contract-shaped API payload renders an attention state when fresh and valid", () => {
  assert.equal(validWorkspacePayload(payload()), true);
  assert.deepEqual(summarizeWorkspacePayload(payload()), { state: "attention", count: 2, active: 4, destination: "/deals?workspace=team&filter=flagged&owner=me" });
});

test("expired, missing, malformed, or unsafe contract freshness withholds counts", () => {
  for (const source of [
    { ...payload().source, valid_until: "2026-08-24T14:59:59.000Z" },
    { ...payload().source, valid_until: null },
    { ...payload().source, freshness: "unknown" },
    { ...payload().source, valid_until: "not-a-date" },
  ]) {
    const result = summarizeWorkspacePayload(payload({ source }));
    assert.equal(result.count, null);
    assert.equal(result.active, null);
  }
});

test("top-level expiry withholds every aggregate card", () => {
  const expired = payload({ source: { ...payload().source, valid_until: "2099-08-24T15:00:30.000Z" } });
  assert.deepEqual(summarizeWorkspacePayload(expired, () => Date.parse("2099-08-24T15:00:31.000Z")), { state: "stale", count: null, active: null, destination: "/deals?workspace=team&filter=flagged&owner=me" });
  assert.deepEqual(aggregateCardState(expired, () => Date.parse("2099-08-24T15:00:31.000Z")), { needs: "stale", doc: "stale", recent: "stale" });
});

test("individual card expiry withholds only that card and empty work cards are invalid", () => {
  const expiredDoc = payload({ doc_at_work: [{ kind: "active_nonhuman_work", count: 2, source: { ...payload().doc_at_work[0].source, valid_until: "2099-08-24T15:00:30.000Z" } }] });
  assert.deepEqual(aggregateCardState(expiredDoc, () => Date.parse("2099-08-24T15:00:31.000Z")), { needs: "fresh", doc: "unavailable", recent: "fresh" });
  assert.equal(validWorkspacePayload(payload({ doc_at_work: [] })), false);
  assert.equal(validWorkspacePayload(payload({ recent_activity: [] })), false);
});

test("arbitrary array-shaped payloads are not accepted", () => {
  assert.equal(validWorkspacePayload({ viewer: "joe", needs_you_now: [], this_week: [], metrics: [], recent_calls: [], doc_at_work: [], recent_activity: [], source: { source: "command_center", observed_at: "now", correlation_id: "x", freshness: "fresh" } }), false);
});

test("actual server response is accepted by the browser model and renders attention", async () => {
  const client = { query: async (sql) => ({ rows: [sql.includes("v_deal_room_board") ? { owned_active: "4", owned_flagged: "2" } : { needs_viewer: "0", doc_at_work: "1", changed_count: "2", changed_at: "2099-08-24T14:30:00.000Z", legacy_unscoped_held: "0", legacy_unscoped_recent: "0" }] }) };
  const serverPayload = await readCommandCenterSummary({ client, actor: { slug: "joe" }, correlationId: "corr-integration", now: () => new Date("2099-08-24T15:00:00.000Z") });
  assert.equal(validWorkspacePayload(serverPayload), true);
  assert.equal(summarizeWorkspacePayload(serverPayload, () => Date.parse("2099-08-24T15:00:30.000Z")).state, "attention");
});
