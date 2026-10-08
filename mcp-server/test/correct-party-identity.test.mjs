// CORRECT-PARTY-IDENTITY (Joe, 2026-10-08: "dont propose corrections, just make
// them"; rule 578fdd91, enrichment applies its corrections). The one door that
// writes party.name, party.org_id and party.state from a verified finding.
//
// What this pins:
//   1. source is required and every changed field writes an event carrying the
//      prior value, so each correction is reversible.
//   2. base_version optimistic concurrency, the same guard update-party-contact uses.
//   3. Org changes follow rule 8cddc6ad: a shared org row is never renamed. The
//      target alone is re-pointed (to an existing org of that name, or a minted
//      one), and the other parties on the old org are read back unchanged.
//   4. The placeholder guard: a CARR agent's own line or a carr.us address is
//      never stored as a name or a firm.
//   5. The verb sits in the full profile only, beside update-party-contact.
import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";
import { planOrgCorrection, normalizeIdentityFields } from "../src/party-identity.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe",
  human: true, via: "oauth", client_id: "claude-ai" };

const TARGET = "20000000-0000-0000-0000-000000000001";
const OTHER = "20000000-0000-0000-0000-000000000002";
const OLD_ORG = "30000000-0000-0000-0000-000000000001";
const EXISTING_ORG = "30000000-0000-0000-0000-000000000002";
const NEW_ORG = "30000000-0000-0000-0000-000000000003";

// A fake that answers by SQL fragment and records every call, so a test can
// assert exactly which writes ran and with which values.
class Fake {
  constructor(plan = {}) { this.plan = plan; this.calls = []; }
  async query(text, params = []) {
    const sql = String(text).replace(/\s+/g, " ").trim();
    this.calls.push([sql, params]);
    for (const [fragment, value] of Object.entries(this.plan))
      if (sql.includes(fragment)) return { rows: typeof value === "function" ? value(params, this) : value };
    return { rows: [] };
  }
  writes(fragment) { return this.calls.filter(([sql]) => sql.includes(fragment)); }
  events() {
    return this.calls.filter(([sql]) => /insert into event\b/i.test(sql));
  }
}

function basePlan({ org = { id: OLD_ORG, name: "Harbr Point Legal" }, others = [],
  existing = [], kind = "person", version = 4 } = {}) {
  return {
    "select subject_id from v_ref_index where subject_type='party' and ref ilike $1": [{ subject_id: TARGET }],
    "select merged_into from party where id=$1": [{ merged_into: null }],
    "select version from party where id=$1 for update": [{ version }],
    "select p.kind, p.name, p.state, p.org_id, o.name as org_name from party p": [{
      kind, name: "Alx Morgan", state: null, org_id: org?.id ?? null, org_name: org?.name ?? null }],
    "select id, name from party where org_id=$1": others,
    "select id, name from party where kind='org'": existing,
    "insert into party (kind,name,created_by,updated_by) values ('org'": [{ id: NEW_ORG }],
  };
}

function call(fake, args) {
  return TOOLS["correct-party-identity"].handler(fake, joe, {
    idempotency_key: "cpi-1", party: "P-0301", base_version: 4,
    source: "record-finding name observed 2026-10-08 example-it.test", ...args,
  });
}

test("registered as a write verb in tools.js with the required arguments", () => {
  const tool = TOOLS["correct-party-identity"];
  assert.ok(tool);
  assert.equal(tool.write, true);
  assert.notEqual(tool.authorityOnly, true);
  assert.equal(tool.registrySource, "mcp-server/src/party-identity.js");
  assert.deepEqual(tool.inputSchema.required, ["idempotency_key", "party", "base_version", "fields", "source"]);
  assert.deepEqual(Object.keys(tool.inputSchema.properties.fields.properties).sort(), ["name", "org", "state"]);
  assert.match(tool.description, /8cddc6ad|shared org/i);
});

test("profiles: held by the full profile, never by the unattended capture or away sets", () => {
  assert.equal(PROFILES.full, null, "full is every verb");
  for (const name of ["capture", "away", "read", "probe", "hermes", "hermes-cos"]) {
    if (!PROFILES[name]) continue;
    assert.equal(PROFILES[name].has("correct-party-identity"), false, name);
    assert.equal(PROFILES[name].has("update-party-contact"), false, `${name} parity with update-party-contact`);
  }
});

test("source is required; an empty one is refused before any read", async () => {
  const fake = new Fake(basePlan());
  await assert.rejects(call(fake, { source: "  ", fields: { name: "Alex Morgan" } }),
    e => e instanceof ToolError && e.payload.error === "missing_source");
  assert.equal(fake.writes("update party").length, 0);
});

test("field normalisation: state is a two-letter US code, names are trimmed, placeholders refused", () => {
  assert.deepEqual(normalizeIdentityFields({ state: " fl ", name: " Alex Morgan " }),
    { state: "FL", name: "Alex Morgan" });
  for (const bad of ["Florida", "F", "XX1", "ZZ"])
    assert.throws(() => normalizeIdentityFields({ state: bad }),
      e => e.payload.error === "invalid_state", bad);
  assert.throws(() => normalizeIdentityFields({}), e => e.payload.error === "no_updatable_fields");
  assert.throws(() => normalizeIdentityFields({ npi: "123" }), e => e.payload.error === "no_updatable_fields");
  assert.throws(() => normalizeIdentityFields({ name: "" }), e => e.payload.error === "invalid_name");
  assert.throws(() => normalizeIdentityFields({ org: "dell.mccraney@carr.us" }),
    e => e.payload.error === "placeholder_value");
  assert.throws(() => normalizeIdentityFields({ name: "(205) 643-6555" }),
    e => e.payload.error === "placeholder_value");
});

test("the org plan follows rule 8cddc6ad", () => {
  const base = { currentOrg: { id: OLD_ORG, name: "Coastln Bank" }, newName: "Coastline Bank" };
  assert.deepEqual(planOrgCorrection({ ...base, othersOnOrg: [], existing: [] }),
    { mode: "rename_in_place", org_id: OLD_ORG });
  assert.deepEqual(planOrgCorrection({ ...base, othersOnOrg: [{ id: OTHER }], existing: [] }),
    { mode: "mint_and_repoint" });
  assert.deepEqual(planOrgCorrection({ ...base, othersOnOrg: [{ id: OTHER }],
    existing: [{ id: EXISTING_ORG, name: "Coastline Bank" }] }),
    { mode: "repoint_existing", org_id: EXISTING_ORG });
  // An existing org of the right name wins even when the old row is unshared:
  // renaming in place would mint a second row carrying the same firm.
  assert.deepEqual(planOrgCorrection({ ...base, othersOnOrg: [],
    existing: [{ id: EXISTING_ORG, name: "Coastline Bank" }] }),
    { mode: "repoint_existing", org_id: EXISTING_ORG });
  assert.deepEqual(planOrgCorrection({ currentOrg: null, newName: "Brightside Exchange, LLC",
    othersOnOrg: [], existing: [] }), { mode: "mint_and_repoint" });
  assert.deepEqual(planOrgCorrection({ ...base, newName: "Coastln Bank", othersOnOrg: [], existing: [] }),
    { mode: "unchanged", org_id: OLD_ORG });
  assert.throws(() => planOrgCorrection({ ...base, othersOnOrg: [], existing: [
    { id: EXISTING_ORG, name: "Coastline Bank" }, { id: NEW_ORG, name: "coastline bank" }] }),
  e => e.payload.error === "org_ambiguous" && e.payload.candidates.length === 2);
});

test("a name correction checks base_version, writes the name, and records the prior value", async () => {
  const fake = new Fake(basePlan());
  const out = await call(fake, { fields: { name: "Alex Morgan" } });
  assert.equal(out.ok, true);
  assert.deepEqual(out.updated, ["name"]);
  assert.ok(fake.writes("select version from party where id=$1 for update").length, "version guard ran");
  const [sql, params] = fake.writes("update party set name=")[0];
  assert.match(sql, /where id=\$3/);
  assert.deepEqual(params, ["Alex Morgan", joe.id, TARGET]);
  const events = fake.events().map(([, p]) => JSON.stringify(p));
  assert.ok(events.some(e => e.includes("Alx Morgan") && e.includes("Alex Morgan")),
    "the event carries old and new name");
  assert.ok(events.some(e => e.includes("example-it.test")), "the event carries the source");
});

test("a stale base_version is refused and nothing is written", async () => {
  const fake = new Fake({ ...basePlan({ version: 5 }) });
  await assert.rejects(call(fake, { fields: { name: "Alex Morgan" } }),
    e => e instanceof ToolError && /conflict/.test(e.payload.error));
  assert.equal(fake.writes("update party set").length, 0);
});

test("state is written uppercase with its prior value", async () => {
  const fake = new Fake(basePlan());
  const out = await call(fake, { fields: { state: "fl" } });
  assert.deepEqual(out.updated, ["state"]);
  assert.deepEqual(fake.writes("update party set state=")[0][1], ["FL", joe.id, TARGET]);
});

test("an unshared org row is renamed in place, with the old name kept on the org's event", async () => {
  const fake = new Fake(basePlan({ org: { id: OLD_ORG, name: "Coastln Bank" } }));
  const out = await call(fake, { fields: { org: "Coastline Bank" } });
  assert.equal(out.org.mode, "rename_in_place");
  assert.deepEqual(fake.writes("update party set name=")[0][1], ["Coastline Bank", joe.id, OLD_ORG]);
  assert.equal(fake.writes("update party set org_id=").length, 0);
  assert.ok(fake.events().some(([, p]) => JSON.stringify(p).includes("Coastln Bank")));
});

test("a shared org row is NEVER renamed: the target alone is re-pointed to a minted org, the others are verified", async () => {
  const others = [{ id: OTHER, name: "Pat Example" }];
  const plan = basePlan({ others });
  // the read-back after the write: the other party still points at the old org
  plan["select id, org_id from party where id = any($1::uuid[])"] = [{ id: OTHER, org_id: OLD_ORG }];
  plan["select name from party where id=$1"] = [{ name: "Harbr Point Legal" }];
  const fake = new Fake(plan);
  const out = await call(fake, { fields: { org: "Harbor Point Legal LLC" } });
  assert.equal(out.org.mode, "mint_and_repoint");
  assert.equal(out.org.attached_before, 2);
  assert.equal(fake.writes("update party set name=").length, 0, "the shared row keeps its name");
  assert.deepEqual(fake.writes("insert into party (kind,name,created_by,updated_by) values ('org'")[0][1],
    ["Harbor Point Legal LLC", joe.id]);
  assert.deepEqual(fake.writes("update party set org_id=")[0][1], [NEW_ORG, joe.id, TARGET]);
  assert.deepEqual(out.org.untouched, [{ id: OTHER, name: "Pat Example", org_id: OLD_ORG }]);
});

test("a re-point that moved an untouched party is refused, so the transaction rolls back", async () => {
  const plan = basePlan({ others: [{ id: OTHER, name: "Pat Example" }] });
  plan["select id, org_id from party where id = any($1::uuid[])"] = [{ id: OTHER, org_id: NEW_ORG }];
  plan["select name from party where id=$1"] = [{ name: "Harbr Point Legal" }];
  await assert.rejects(call(new Fake(plan), { fields: { org: "Harbor Point Legal LLC" } }),
    e => e.payload.error === "untouched_party_moved");
});

test("an existing org with the corrected name is reused rather than duplicated", async () => {
  const plan = basePlan({ org: { id: OLD_ORG, name: "Coastln Bank" },
    existing: [{ id: EXISTING_ORG, name: "Coastline Bank" }] });
  const fake = new Fake(plan);
  const out = await call(fake, { fields: { org: "Coastline Bank" } });
  assert.equal(out.org.mode, "repoint_existing");
  assert.equal(fake.writes("insert into party (kind,name").length, 0);
  assert.deepEqual(fake.writes("update party set org_id=")[0][1], [EXISTING_ORG, joe.id, TARGET]);
});

test("org is a person's field; an org party's own name goes through fields.name", async () => {
  const fake = new Fake(basePlan({ kind: "org", org: null }));
  await assert.rejects(call(fake, { fields: { org: "Anything" } }),
    e => e.payload.error === "org_on_org_party");
});
