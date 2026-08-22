import test from "node:test";
import assert from "node:assert/strict";

import { readDealAttentionSummary } from "../src/workspace-command-center.js";
import { workspaceCommandCenterPosture } from "../src/workspace-feature-flag.js";
import { resolveDealroomDeepLink, dealMatchesDeepLink } from "../../dealroom/js/deep-link.js";

const NOW = new Date("2026-08-22T02:50:00.000Z");

function fakeClient(rows) {
  const calls = [];
  return {
    calls,
    async query(text, params) {
      calls.push({ text, params });
      return { rows };
    },
  };
}

test("deal-attention summary is scoped to the signed-in actor and canonical Team Book read", async () => {
  const client = fakeClient([
    { id: "j1", workspace_kind: "team", owner: "joe", attention: true, operating_state: "active" },
    { id: "j2", workspace_kind: "team", owner: "joe", attention: false, operating_state: "active" },
    { id: "d1", workspace_kind: "team", owner: "dell", attention: true, operating_state: "active" },
    { id: "parked", workspace_kind: "team", owner: "joe", attention: true, operating_state: "parked" },
    { id: "national", workspace_kind: "national_account", owner: "joe", attention: true, operating_state: "active" },
  ]);
  const payload = await readDealAttentionSummary({
    client,
    actor: { slug: "joe" },
    now: () => NOW,
  });

  assert.equal(client.calls.length, 1);
  assert.match(client.calls[0].text, /from v_deal_room_board/);
  assert.match(client.calls[0].text, /owner = \$3::text/);
  assert.match(client.calls[0].text, /operating_state = \$4::text/);
  assert.deepEqual(client.calls[0].params, ["team", null, "joe", "active"]);
  assert.deepEqual(payload, {
    schema_version: "workspace-command-center-deal-attention/v1",
    state: "attention",
    actor: { slug: "joe" },
    source: { kind: "canonical_view", ref: "v_deal_room_board" },
    observed_at: NOW.toISOString(),
    freshness: { status: "unknown", basis: "read_time_only" },
    summary: { owned_active: 2, owned_flagged: 1 },
    destination: "/deals?workspace=team&filter=flagged&owner=me",
  });
});

test("deal-attention summary has an authored empty state and never accepts caller ownership", async () => {
  const client = fakeClient([]);
  const payload = await readDealAttentionSummary({
    client,
    actor: { slug: "dell" },
    now: () => NOW,
  });
  assert.equal(payload.state, "empty");
  assert.deepEqual(payload.summary, { owned_active: 0, owned_flagged: 0 });
  assert.deepEqual(client.calls[0].params, ["team", null, "dell", "active"]);
  assert.equal(JSON.stringify(payload).includes("joe"), false);
});

test("Workspace Command Center flag is literal-only and fails closed", () => {
  assert.deepEqual(workspaceCommandCenterPosture({ WORKSPACE_COMMAND_CENTER_READ_ENABLED: "true" }),
    { enabled: true, posture: "enabled", reason: null });
  assert.deepEqual(workspaceCommandCenterPosture({ WORKSPACE_COMMAND_CENTER_READ_ENABLED: "false" }),
    { enabled: false, posture: "disabled", reason: null });
  for (const value of [undefined, "TRUE", "1", ""]) {
    const posture = workspaceCommandCenterPosture({ WORKSPACE_COMMAND_CENTER_READ_ENABLED: value });
    assert.equal(posture.enabled, false);
    assert.equal(posture.posture, "misconfigured");
    assert.match(posture.reason, /must be exactly true or false/);
  }
});

test("exact Deal Room deep link filters active flagged Team Book rows for me", () => {
  const selection = resolveDealroomDeepLink("?workspace=team&filter=flagged&owner=me");
  assert.deepEqual(selection, { workspace: "team", filter: "flagged", owner: "me" });
  const rows = [
    { id: "yes", workspace_kind: "team", owner: "joe", attention: true, operating_state: "active" },
    { id: "overdue-only", workspace_kind: "team", owner: "joe", attention: false, operating_state: "active", next_date: "2020-01-01" },
    { id: "other-owner", workspace_kind: "team", owner: "dell", attention: true, operating_state: "active" },
    { id: "parked", workspace_kind: "team", owner: "joe", attention: true, operating_state: "parked" },
    { id: "national", workspace_kind: "national_account", owner: "joe", attention: true, operating_state: "active" },
  ];
  assert.deepEqual(rows.filter((row) => dealMatchesDeepLink(row, "joe", selection)).map((row) => row.id), ["yes"]);
});

test("unknown, partial, or authority-shaped query input cannot select a deep-link view", () => {
  for (const search of [
    "", "?workspace=all&filter=flagged&owner=me", "?workspace=team&filter=attention&owner=me",
    "?workspace=team&filter=flagged&owner=dell", "?workspace=team&filter=flagged&owner=me&actor=dell",
  ]) assert.equal(resolveDealroomDeepLink(search), null, search);
});
