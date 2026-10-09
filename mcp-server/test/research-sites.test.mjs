// THE RESEARCH-SITE INDEX (Joe, 2026-10-07): an index of useful sources that
// research checks first, then goes beyond on the open web. Not a permission list.
//
// What this pins:
//   1. Every verb is open to every caller — machine and session identities
//      included. Joe ruled he does not want to be involved in adding sites.
//   2. Hosts and topic tags are normalised and validated before SQL.
//   3. Adding a listed host is a no-op that returns the existing row; removal is
//      soft and records who and why.
//   4. The read filters by topic tag or free text and reminds the caller the
//      index is a starting point.
import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { RESEARCH_SITE_WRITE_VERBS, normalizeResearchHost, normalizeTopics } from "../src/research-sites.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth", client_id: "claude-ai" };
const joeLocal = { id: "10000000-0000-0000-0000-000000000010", slug: "joe-local", human: false,
  via: "local-token", native_agent_verified: true, sponsoring_human_slug: "joe", client_id: "local-verb" };

class Fake {
  constructor(plan = {}) { this.plan = plan; this.calls = []; }
  async query(text, params = []) {
    const sql = String(text).replace(/\s+/g, " ").trim();
    this.calls.push([sql, params]);
    for (const [fragment, value] of Object.entries(this.plan))
      if (sql.includes(fragment)) return { rows: typeof value === "function" ? value(params) : value };
    return { rows: [] };
  }
}

test("the index verbs are registered and none is human-only", () => {
  for (const name of ["add-research-site", "remove-research-site", "list-research-sites"]) {
    assert.ok(TOOLS[name], name);
    assert.notEqual(TOOLS[name].humanOnly, true, name);
    assert.notEqual(TOOLS[name].authorityOnly, true, name);
  }
  assert.equal(TOOLS["add-research-site"].write, true);
  assert.equal(TOOLS["remove-research-site"].write, true);
  assert.notEqual(TOOLS["list-research-sites"].write, true);
  assert.deepEqual([...RESEARCH_SITE_WRITE_VERBS].sort(), ["add-research-site", "remove-research-site"]);
  assert.deepEqual(TOOLS["add-research-site"].inputSchema.required, ["idempotency_key", "host", "topics"]);
  assert.deepEqual(TOOLS["remove-research-site"].inputSchema.required, ["idempotency_key", "host", "reason"]);
  assert.match(TOOLS["list-research-sites"].description, /open internet/);
});

test("a machine identity passes the dispatcher into add-research-site", async () => {
  // Empty args: a refusal must come from argument validation, never from identity.
  const error = await executeRegisteredTool({ query: async () => ({ rows: [] }) }, joeLocal,
    "add-research-site", {}).then(() => null, e => e);
  assert.notEqual(error?.payload?.error, "human_only_verb_requires_verified_partner");
});

test("hosts normalise to exact public hostnames and everything else is refused", () => {
  assert.equal(normalizeResearchHost("WWW.CBRE.com."), "www.cbre.com");
  for (const bad of ["https://www.cbre.com", "www.cbre.com/path", "*.cbre.com", "cbre", "10.0.0.1",
    "www.cbre.com:8443", "user@cbre.com", "cbre..com", "", "printer.local", null]) {
    assert.throws(() => normalizeResearchHost(bad),
      e => e instanceof ToolError && e.payload.error === "research_site_host_invalid", String(bad));
  }
});

test("topic tags normalise and are validated", () => {
  assert.deepEqual(normalizeTopics(["CRE Market", "npi", "npi"]), ["cre-market", "npi"]);
  for (const bad of [[], ["bad/tag"], [""], Array(13).fill(0).map((_, i) => `t${i}`)])
    assert.throws(() => normalizeTopics(bad), e => e.payload.error === "research_site_topics_invalid");
  assert.throws(() => normalizeTopics(undefined), e => e.payload.error === "research_site_topics_required");
});

test("a machine identity adds a site: one insert, one audit event, the actor recorded", async () => {
  const fake = new Fake({
    "insert into research_site": [{ id: "30000000-0000-0000-0000-000000000001", host: "data.example.org",
      added_at: "2026-10-07T12:00:00Z" }],
  });
  const out = await TOOLS["add-research-site"].handler(fake, joeLocal, {
    idempotency_key: "site-add-1", host: "Data.Example.org", topics: ["Demographics"],
    url: "https://data.example.org/tables", note: "county tables",
  });
  assert.equal(out.ok, true);
  assert.deepEqual(out.topics, ["demographics"]);
  const insert = fake.calls.find(([sql]) => sql.includes("insert into research_site"));
  assert.deepEqual(insert[1], ["data.example.org", "https://data.example.org/tables", ["demographics"],
    "county tables", joeLocal.id]);
  assert.ok(fake.calls.some(([sql]) => sql.includes("insert into event")), "no audit event");
});

test("a start url on a different host is refused before SQL", async () => {
  const fake = new Fake();
  await assert.rejects(() => TOOLS["add-research-site"].handler(fake, joe, {
    idempotency_key: "site-add-url", host: "www.cbre.com", topics: ["cre-market"], url: "https://evil.example/x",
  }), e => e.payload.error === "research_site_url_invalid");
  assert.equal(fake.calls.length, 0);
});

test("adding a host already in the index returns the row without writing", async () => {
  const fake = new Fake({ "from research_site where host=$1 and removed_at is null":
    [{ id: "30000000-0000-0000-0000-000000000001", host: "www.cbre.com", topics: ["cre-market"] }] });
  const out = await TOOLS["add-research-site"].handler(fake, joe, {
    idempotency_key: "site-add-dup", host: "www.cbre.com", topics: ["cre-market"] });
  assert.equal(out.already_listed, true);
  assert.ok(!fake.calls.some(([sql]) => sql.includes("insert into research_site")));
});

test("any caller removes a site softly, recording who and why", async () => {
  const fake = new Fake({ "update research_site set removed_at": [{ id: "30000000-0000-0000-0000-000000000001",
    host: "www.cbre.com", removed_at: "2026-10-07T13:00:00Z" }] });
  const out = await TOOLS["remove-research-site"].handler(fake, joeLocal, {
    idempotency_key: "site-remove-1", host: "www.cbre.com", reason: "moved" });
  assert.equal(out.ok, true);
  const update = fake.calls.find(([sql]) => sql.includes("update research_site set removed_at"));
  assert.ok(update[0].includes("removed_at is null"));
  assert.deepEqual(update[1], ["www.cbre.com", joeLocal.id, "moved"]);
  assert.ok(!fake.calls.some(([sql]) => /delete from research_site/.test(sql)));
});

test("removing an unlisted host says so", async () => {
  await assert.rejects(() => TOOLS["remove-research-site"].handler(new Fake(), joe, {
    idempotency_key: "site-remove-missing", host: "not-listed.example", reason: "cleanup",
  }), e => e.payload.error === "research_site_not_listed");
});

test("the read filters by topic and text and reminds the caller to search beyond it", async () => {
  const fake = new Fake({ "from research_site s": [{ host: "www.cbre.com", topics: ["cre-market"] }],
    "unnest(topics)": [{ topic: "cre-market" }, { topic: "npi" }] });
  const out = await TOOLS["list-research-sites"].handler(fake, joe, { topic: "CRE Market", text: "Office" });
  const [sql, params] = fake.calls[0];
  assert.ok(sql.includes("removed_at is null"));
  assert.ok(sql.includes("s.topics @> array[$1]::text[]"));
  assert.deepEqual(params, ["cre-market", "%office%"]);
  assert.deepEqual(out.topics_in_use, ["cre-market", "npi"]);
  assert.match(out.reminder, /open internet/);
  const all = new Fake();
  await TOOLS["list-research-sites"].handler(all, joe, { include_removed: true });
  assert.ok(!all.calls[0][0].includes("removed_at is null"));
});
