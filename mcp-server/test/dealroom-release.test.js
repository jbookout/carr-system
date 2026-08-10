import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { TOOLS } from "../src/tools.js";
import { createLiveClient } from "../../dealroom/js/live-client.js";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const file = (path) => readFile(ROOT + path, "utf8");

function rpcResponse(payload, isError = false) {
  return new Response(JSON.stringify({ jsonrpc: "2.0", id: 1,
    result: { content: [{ type: "text", text: JSON.stringify(payload) }], isError } }),
  { headers: { "content-type": "application/json" } });
}

test("live client derives Dell identity, normalizes conflict shape, and creates as Dell", async () => {
  const calls = [];
  const fetchImpl = async (path, init) => {
    assert.equal(path, "/mcp");
    const request = JSON.parse(init.body);
    calls.push(request.params);
    const name = request.params.name;
    if (name === "deal-room-board") return rpcResponse({ actor: "dell", deals: [{
      id: "d1", name: "Market Deal", phase: "research", type: "startup",
      account_client_id: null,
    }], accounts: [] });
    if (name === "patch-deal-field") return rpcResponse({ ok: false, conflict: {
      id: "c1", deal_id: "d1", field: "owner", value_a: "joe", actor_a: "joe",
      value_b: "dell", actor_b: "dell",
    } });
    if (name === "new-deal") return rpcResponse({ ok: true, deal_id: "d2" });
    if (name === "set-lead") return rpcResponse({ ok: true, new_lead: "dell" });
    throw new Error(`unexpected ${name}`);
  };
  const client = createLiveClient({ fetchImpl });
  const home = await client.getBoard();
  assert.equal(home.actor, "dell");
  assert.equal(client.selfActor, "dell");

  const conflict = await client.patchDealField({ deal: "d1", field: "owner",
    value: "dell", base_event_id: null });
  assert.equal(conflict.status, "conflict");
  assert.deepEqual(conflict.conflict.a, { actor: "joe", value: "joe" });
  assert.deepEqual(conflict.conflict.b, { actor: "dell", value: "dell" });

  await client.createDeal({ client: "C-127", name: "New market", deal_type: "startup",
    phase: "On Deck", lane: "territory" });
  const lead = calls.find((call) => call.name === "set-lead");
  assert.equal(lead.arguments.new_lead, "dell");
  assert.equal(lead.arguments.deal, "d2");
});

test("release 1–5 verbs are registered with human gates on structural creation", () => {
  for (const name of ["start-deal-review","review-deal","end-deal-review",
    "set-market-agent","set-national-account-owner","create-national-account",
    "create-national-market-deal","revert-deal-field"]) {
    assert.ok(TOOLS[name], `${name} must be registered`);
  }
  assert.equal(TOOLS["create-national-account"].humanOnly, true);
  assert.equal(TOOLS["create-national-market-deal"].humanOnly, true);
});

test("national-account migration keeps the 0061 hierarchy and explicitly assigns Musicologie", async () => {
  const sql = await file("migrations/0090_deal_room_workspaces.sql");
  assert.match(sql, /left join v_client_account vca/);
  assert.match(sql, /lower\(p\.name\) = 'musicologie'/);
  assert.match(sql, /join actor a on a\.slug = 'dell'/);
  assert.match(sql, /create table deal_market_assignment/);
  assert.match(sql, /create table deal_review_session/);
  assert.match(sql, /case when vca\.account_client_id is null then 'team' else 'national_account'/);
  assert.doesNotMatch(sql, /update deal set client_id/i,
    "workspace migration must never reparent or duplicate an existing deal");
});

test("UI exposes two workspaces, explicit controls, mobile cards, and Call Mode", async () => {
  const [html, app, css, sw] = await Promise.all([
    file("dealroom/index.html"), file("dealroom/js/app.js"),
    file("dealroom/css/app.css"), file("dealroom/public-shell/sw.js"),
  ]);
  assert.match(html, /data-workspace="team"/);
  assert.match(html, /data-workspace="national_account"/);
  assert.match(html, /Start agenda/);
  assert.match(html, /Search work records/);
  assert.match(app, /new Date\(new Date\(\)\.toDateString\(\)\)/);
  assert.doesNotMatch(app, /new Date\('2026-/);
  assert.match(html + app, /Call Mode/);
  assert.match(app, /<select class="cell-select" data-phase/);
  assert.match(app, /<select class="cell-select" data-owner/);
  assert.match(css, /@media\(max-width:680px\)/);
  assert.match(css, /min-height:44px/);
  assert.match(sw, /dealroom-shell-v3/);
  assert.match(sw, /Deliberately no Cache API fallback/);
});

test("parking separates Salesforce record existence from active work", async () => {
  const [sql, hardening, html, app, tools] = await Promise.all([
    file("migrations/0092_deal_operating_state.sql"),
    file("migrations/0093_deal_parking_shape_hardening.sql"), file("dealroom/index.html"),
    file("dealroom/js/app.js"), file("mcp-server/src/tools.js"),
  ]);
  assert.match(sql, /operating_state text not null default 'active'/);
  assert.match(sql, /prospect_never_active.*client_paused.*other/s);
  assert.match(sql, /count\(d\.id\).*operating_state = 'active'.*as open_deals/s);
  assert.doesNotMatch(sql, /update deal set (phase|outcome)/i);
  assert.match(hardening, /parking_reason is not null/);
  assert.match(hardening, /parking_reason in \('prospect_never_active','client_paused','other'\)/);
  assert.match(html, /data-filter="active"[^>]*>Active/);
  assert.match(html, /data-filter="parked"[^>]*>Parked/);
  assert.match(app, /No active work in this agenda/);
  assert.match(app, /Salesforce link, phase, history, and participants stay intact/);
  assert.match(tools, /"operating_state"/);
  assert.match(tools, /dealroom:apply-operating-state/);
});
