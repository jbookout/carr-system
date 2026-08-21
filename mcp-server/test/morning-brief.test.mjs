// morning-brief.test.mjs — safe record-native morning delivery.
//
// This is deliberately a fake-client suite.  It proves the handler's source
// and authority contract without a Worker, credential, or database connection.

import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool } from "../src/tools.js";
import { readCallInsertSQL } from "../src/mcp.js";

const JOE = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "fixture" };
const DELL = { slug: "dell", display: "Dell", human: true, via: "oauth-google", client_id: "fixture" };
const UNSPONSORED = { slug: "codex", display: "Codex", human: false, via: "agent-token" };

function client({ unavailable = null, accounts = [], renewalState = "ready", renewalRows = null } = {}) {
  const queries = [];
  return {
    queries,
    query: async (sql, params = []) => {
      queries.push({ sql, params });
      if (unavailable && sql.includes(unavailable)) throw new Error("fixture source unavailable");
      if (sql.includes("from v_today_triage")) return { rows: [
        { item_kind: "next_action", owner: "joe", what: "Call the client", subject_name: "Example Practice" },
      ] };
      if (sql.includes("from v_claim_card") && sql.includes("count(*)")) return { rows: [
        { claimable: 3, needs_contact_count: 1 },
      ] };
      if (sql.includes("from v_claim_card")) return { rows: [
        { display_name: "Candidate Safe", city: "Mobile", has_channel: true, pool_id: "opaque-handler-id" },
      ] };
      if (sql.includes("from v_deal_room_board")) return { rows: [
        { name: "Example Deal", owner: "joe", attention: true },
      ] };
      if (sql.includes("from v_deal_room_account")) return { rows: accounts };
      if (sql.includes("from v_deal_room_session")) return { rows: [] };
      if (sql.includes("from loop_item")) return { rows: [
        { number: 7, owner: "joe", label: "Resolve the lease item" },
      ] };
      if (sql.includes("from v_renewal_decision_queue_status")) return { rows: [
        { t1_candidate_count: renewalState === "empty" ? 0 : 1, source_observed_at: "2026-08-21T06:00:00Z", freshness_state: renewalState },
      ] };
      if (sql.includes("from v_renewal_decision_queue")) return { rows: renewalRows || [
        { display_name: "Renewal Safe", city: "Pensacola", est_lease_event: "2027-01-01",
          tier_status: "t1", flag_status: "clear", has_channel: true },
      ] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

test("morning-brief derives Joe's sponsor from authenticated context and composes only record views", async () => {
  const c = client();
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  assert.equal(result.state, "ready");
  assert.equal(result.sponsor, "joe");
  assert.deepEqual(Object.keys(result.sections).sort(), ["claim_card", "deals", "renewals", "today", "loops"].sort());
  assert.equal(result.sections.renewals.state, "ready");
  assert.equal(result.sections.renewals.items[0].display_name, "Renewal Safe");
  const sql = c.queries.map(({ sql }) => sql).join("\n");
  assert.match(sql, /v_today_triage/);
  assert.match(sql, /v_claim_card/);
  assert.match(sql, /v_deal_room_board/);
  assert.match(sql, /loop_item/);
  assert.match(sql, /v_renewal_decision_queue/);
  assert.doesNotMatch(sql, /candidate_pool|v_export_pool|source_row|\bemail\b|\bphone\b|\baddress\b/i);
});

test("morning-brief derives Dell separately; no caller field may select an audience or sponsor", async () => {
  const c = client();
  const dell = await executeRegisteredTool(c, DELL, "morning-brief", {});
  assert.equal(dell.sponsor, "dell");
  for (const args of [{ sponsor: "joe" }, { audience: "joe" }, { partner: "joe" }]) {
    await assert.rejects(
      executeRegisteredTool(client(), DELL, "morning-brief", args),
      (error) => error instanceof ToolError,
    );
  }
});

test("morning-brief filters accounts by authenticated sponsor rather than exposing a shared deal-room account list", async () => {
  const accounts = [
    { account_name: "Joe Account", account_owner: "joe" },
    { account_name: "Dell Account", account_owner: "dell" },
    { account_name: "Unowned Account", account_owner: null },
  ];
  const joe = await executeRegisteredTool(client({ accounts }), JOE, "morning-brief", {});
  const dell = await executeRegisteredTool(client({ accounts }), DELL, "morning-brief", {});
  assert.deepEqual(joe.sections.deals.accounts.map((row) => row.account_name), ["Joe Account"]);
  assert.deepEqual(dell.sections.deals.accounts.map((row) => row.account_name), ["Dell Account"]);
  assert.doesNotMatch(JSON.stringify(joe), /Dell Account/);
  assert.doesNotMatch(JSON.stringify(dell), /Joe Account/);
  assert.doesNotMatch(JSON.stringify(joe), /Unowned Account/);
  assert.doesNotMatch(JSON.stringify(dell), /Unowned Account/);
});

test("morning-brief refuses an unsponsored runtime rather than selecting a shared or caller-supplied brain", async () => {
  await assert.rejects(
    executeRegisteredTool(client(), UNSPONSORED, "morning-brief", {}),
    (error) => error instanceof ToolError && error.payload.error === "morning_brief_requires_partner_scope",
  );
});

test("a missing required source is unavailable, never silently rendered as an empty brief", async () => {
  const c = client({ unavailable: "v_claim_card" });
  const result = await executeRegisteredTool(c, JOE, "morning-brief", {});
  assert.equal(result.state, "unavailable");
  assert.equal(result.sections.claim_card.state, "unavailable");
  assert.equal(result.sections.claim_card.reason, "source_unavailable");
  assert.notEqual(result.sections.claim_card.items, undefined);
  assert.equal(result.sections.today.state, "ready");
  assert.doesNotMatch(JSON.stringify(result), /fixture source unavailable/);
});

test("a sealed true-zero renewal source is explicit empty, never unavailable or fabricated rows", async () => {
  const result = await executeRegisteredTool(client({ renewalState: "empty", renewalRows: [] }), JOE, "morning-brief", {});
  assert.equal(result.state, "ready");
  assert.equal(result.sections.renewals.state, "empty");
  assert.equal(result.sections.renewals.t1_candidate_count, 0);
  assert.deepEqual(result.sections.renewals.items, []);
});

test("tool_read_call remains metadata-only for the composite response", () => {
  const output = { private_body: "Candidate Safe", sections: { renewals: { items: [{ display_name: "Renewal Safe" }] } } };
  const receipt = readCallInsertSQL(JOE, "morning-brief", true, null);
  assert.doesNotMatch(receipt.text, /response|argument|body|sponsor.*\$11/i);
  assert.doesNotMatch(JSON.stringify(receipt.params), /Candidate Safe|Renewal Safe|private_body/);
  assert.equal(output.sections.renewals.items.length, 1); // Guard against a vacuous metadata-only test.
});
