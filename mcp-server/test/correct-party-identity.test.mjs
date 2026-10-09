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

// org_identity_key() as the database defines it (0059), for the fake only.
const idKey = n => (n == null || !String(n).trim()) ? null : String(n).trim().replace(/\s+/g, " ").toLowerCase();

function basePlan({ org = { id: OLD_ORG, name: "Harbr Point Legal" }, others = [], refs = null,
  refsAfter = [], existing = [], kind = "person", name = "Alx Morgan", version = 4,
  merged_into = null } = {}) {
  // By default the only references to the old org are the other people on it.
  const refsBefore = refs ?? (others.length ? [{ source: "party.org_id", n: String(others.length) }] : []);
  return {
    "select subject_id from v_ref_index where subject_type='party' and ref ilike $1": [{ subject_id: TARGET }],
    "select merged_into from party where id=$1": [{ merged_into }],
    "select version from party where id=$1 for update": [{ version }],
    "select p.kind, p.name, p.state, p.org_id, o.name as org_name from party p": [{
      kind, name, state: null, org_id: org?.id ?? null, org_name: org?.name ?? null }],
    "select org_identity_key($1) as new_key, org_identity_key($2) as cur_key":
      ([a, b]) => [{ new_key: idKey(a), cur_key: idKey(b) }],
    "and org_identity_key(name)=$1": existing,
    "from party_reference_counts($1,$2)": ([, exclude]) => exclude === null ? refsAfter : refsBefore,
    "select id, name from party where org_id=$1": others,
    "select org_party_id($1,$2) as id": [{ id: NEW_ORG }],
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
  const base = { currentOrg: { id: OLD_ORG, name: "Coastln Bank" }, newName: "Coastline Bank",
    existing: [], sameIdentity: false, shared: false };
  assert.deepEqual(planOrgCorrection(base), { mode: "rename_in_place", org_id: OLD_ORG });
  assert.deepEqual(planOrgCorrection({ ...base, shared: true }), { mode: "mint_and_repoint" });
  assert.deepEqual(planOrgCorrection({ ...base, shared: true,
    existing: [{ id: EXISTING_ORG, name: "Coastline Bank" }] }),
    { mode: "repoint_existing", org_id: EXISTING_ORG });
  // An existing org with the right identity wins even when the old row is
  // unshared: renaming in place would make a second row for the same firm.
  assert.deepEqual(planOrgCorrection({ ...base, existing: [{ id: EXISTING_ORG, name: "Coastline Bank" }] }),
    { mode: "repoint_existing", org_id: EXISTING_ORG });
  assert.deepEqual(planOrgCorrection({ ...base, currentOrg: null, newName: "Brightside Exchange, LLC" }),
    { mode: "mint_and_repoint" });
  assert.deepEqual(planOrgCorrection({ ...base, newName: "Coastln Bank" }), { mode: "unchanged", org_id: OLD_ORG });
  // A re-spelling of the same identity (case or spacing only)
  assert.deepEqual(planOrgCorrection({ ...base, newName: "COASTLN Bank", sameIdentity: true }),
    { mode: "rename_in_place", org_id: OLD_ORG });
  assert.throws(() => planOrgCorrection({ ...base, newName: "COASTLN Bank", sameIdentity: true, shared: true }),
    e => e.payload.error === "shared_org_respelling");
  assert.throws(() => planOrgCorrection({ ...base, existing: [
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

const eventPayloads = fake => fake.events().map(([, p]) => JSON.stringify(p));

test("an unshared org row is renamed in place, with the old name and the mode on the org's event", async () => {
  const fake = new Fake(basePlan({ org: { id: OLD_ORG, name: "Coastln Bank" } }));
  const out = await call(fake, { fields: { org: "Coastline Bank" } });
  assert.equal(out.org.mode, "rename_in_place");
  assert.deepEqual(fake.writes("update party set name=")[0][1], ["Coastline Bank", joe.id, OLD_ORG]);
  assert.equal(fake.writes("update party set org_id=").length, 0);
  assert.ok(fake.writes("select id from party where id=$1 for update").length, "the org row is locked first");
  assert.ok(fake.writes("pg_advisory_xact_lock").length, "the new identity is locked");
  assert.ok(eventPayloads(fake).some(e => e.includes("Coastln Bank") && e.includes("rename_in_place")));
});

test("a shared org row is NEVER renamed: the target alone is re-pointed to a minted org, the others are verified", async () => {
  const others = [{ id: OTHER, name: "Pat Example" }];
  const plan = basePlan({ others, refsAfter: [{ source: "party.org_id", n: "1" }] });
  // the read-back after the write: the other party still points at the old org
  plan["select id, org_id from party where id = any($1::uuid[])"] = [{ id: OTHER, org_id: OLD_ORG }];
  plan["select name from party where id=$1"] = [{ name: "Harbr Point Legal" }];
  const fake = new Fake(plan);
  const out = await call(fake, { fields: { org: "Harbor Point Legal LLC" } });
  assert.equal(out.org.mode, "mint_and_repoint");
  assert.deepEqual(out.org.references_before, [{ source: "party.org_id", n: 1 }]);
  assert.equal(fake.writes("update party set name=").length, 0, "the shared row keeps its name");
  assert.deepEqual(fake.writes("select org_party_id($1,$2) as id")[0][1], ["Harbor Point Legal LLC", joe.id],
    "minting goes through the shared find-or-create");
  assert.deepEqual(fake.writes("update party set org_id=")[0][1], [NEW_ORG, joe.id, TARGET]);
  assert.deepEqual(out.org.untouched, [{ id: OTHER, name: "Pat Example", org_id: OLD_ORG }]);
  assert.equal(out.org.old_org_left_empty, undefined, "the old org still has a person on it");
  // the org_id event stores raw ids in the field, names beside them, and the mode
  // (writeEvent params: [occurred_at, actor, verb, subject_type, subject_id, field, old, new, ...])
  const orgEvent = fake.events().map(([, p]) => p).find(p => p[5] === "org_id");
  assert.deepEqual(JSON.parse(orgEvent[6]), { org_id: OLD_ORG, org_name: "Harbr Point Legal" });
  assert.deepEqual(JSON.parse(orgEvent[7]),
    { org_id: NEW_ORG, org_name: "Harbor Point Legal LLC", mode: "mint_and_repoint" });
  const mintEvent = fake.events().map(([, p]) => p).find(p => p[4] === NEW_ORG);
  assert.equal(JSON.parse(mintEvent[7]).mode, "mint_and_repoint", "the mint event carries the mode too");
});

test("a reference other than a person (a deal participant on the org row) also makes it shared", async () => {
  const plan = basePlan({ refs: [{ source: "deal_participant.party_id", n: "2" }],
    refsAfter: [{ source: "deal_participant.party_id", n: "2" }] });
  const fake = new Fake(plan);
  const out = await call(fake, { fields: { org: "Harbor Point Legal LLC" } });
  assert.equal(out.org.mode, "mint_and_repoint");
  assert.equal(fake.writes("update party set name=").length, 0, "a deal-linked org is never renamed");
  assert.deepEqual(out.org.references_before, [{ source: "deal_participant.party_id", n: 2 }]);
});

test("a re-point that moved an untouched party is refused, so the transaction rolls back", async () => {
  const plan = basePlan({ others: [{ id: OTHER, name: "Pat Example" }] });
  plan["select id, org_id from party where id = any($1::uuid[])"] = [{ id: OTHER, org_id: NEW_ORG }];
  plan["select name from party where id=$1"] = [{ name: "Harbr Point Legal" }];
  await assert.rejects(call(new Fake(plan), { fields: { org: "Harbor Point Legal LLC" } }),
    e => e.payload.error === "untouched_party_moved");
});

test("an existing org with the corrected identity is reused, and an emptied old org is reported", async () => {
  const plan = basePlan({ org: { id: OLD_ORG, name: "Coastln Bank" },
    refs: [{ source: "client.party_id", n: "1" }], refsAfter: [],
    existing: [{ id: EXISTING_ORG, name: "Coastline Bank" }] });
  const fake = new Fake(plan);
  const out = await call(fake, { fields: { org: "coastline  bank" } });
  assert.equal(out.org.mode, "repoint_existing");
  assert.equal(fake.writes("org_party_id").length, 0);
  assert.deepEqual(fake.writes("update party set org_id=")[0][1], [EXISTING_ORG, joe.id, TARGET]);
  assert.deepEqual(out.org.old_org_left_empty, { id: OLD_ORG, name: "Coastln Bank" });
  assert.match(fake.writes("and org_identity_key(name)=$1")[0][0], /for update/, "the reused org is locked");
});

test("a case-only re-spelling of a shared org is refused rather than renamed or duplicated", async () => {
  const plan = basePlan({ org: { id: OLD_ORG, name: "Coastline bank" }, others: [{ id: OTHER, name: "Pat Example" }] });
  const fake = new Fake(plan);
  await assert.rejects(call(fake, { fields: { org: "Coastline Bank" } }),
    e => e.payload.error === "shared_org_respelling");
  assert.equal(fake.writes("update party set").length, 0);
});

test("a case-only re-spelling of an unshared org renames it in place", async () => {
  const fake = new Fake(basePlan({ org: { id: OLD_ORG, name: "Coastline bank" } }));
  const out = await call(fake, { fields: { org: "Coastline Bank" } });
  assert.equal(out.org.mode, "rename_in_place");
  assert.equal(fake.writes("and org_identity_key(name)=$1").length, 0, "same identity: no twin lookup");
});

test("the same org name again is a no-op: no write, no event", async () => {
  const fake = new Fake(basePlan({ org: { id: OLD_ORG, name: "Coastline Bank" } }));
  const out = await call(fake, { fields: { org: "Coastline Bank" } });
  assert.equal(out.org.mode, "unchanged");
  assert.deepEqual(out.updated, []);
  assert.equal(fake.writes("update party set").length, 0);
  assert.equal(fake.events().length, 0);
});

test("name, state and org together each write their own event", async () => {
  const fake = new Fake(basePlan({ org: { id: OLD_ORG, name: "Coastln Bank" } }));
  const out = await call(fake, { fields: { name: "Alex Morgan", state: "al", org: "Coastline Bank" } });
  assert.deepEqual(out.updated, ["name", "state", "org"]);
  assert.equal(fake.events().length, 3);
});

test("a merged party hops to its survivor and says so", async () => {
  const SURVIVOR = "20000000-0000-0000-0000-000000000009";
  const fake = new Fake(basePlan({ merged_into: SURVIVOR }));
  const out = await call(fake, { fields: { name: "Alex Morgan" } });
  assert.equal(out.party_id, SURVIVOR);
  assert.equal(out.hopped_to_survivor, true);
  assert.deepEqual(fake.writes("update party set name=")[0][1], ["Alex Morgan", joe.id, SURVIVOR]);
});

test("an org party's own name is refused while people are attached, naming them", async () => {
  const plan = basePlan({ kind: "org", name: "Harbr Point Legal", org: null,
    others: [{ id: OTHER, name: "Pat Example" }] });
  const fake = new Fake(plan);
  await assert.rejects(call(fake, { fields: { name: "Harbor Point Legal LLC" } }),
    e => e.payload.error === "shared_org_rename" && e.payload.attached[0].name === "Pat Example");
  assert.equal(fake.writes("update party set").length, 0);
});

test("an org party referenced by more than its own role row (deal participants) is refused", async () => {
  const plan = basePlan({ kind: "org", name: "Harbr Point Legal", org: null,
    refs: [{ source: "vendor.party_id", n: "1" }, { source: "deal_participant.party_id", n: "2" }] });
  const fake = new Fake(plan);
  await assert.rejects(call(fake, { fields: { name: "Harbor Point Legal LLC" } }),
    e => e.payload.error === "shared_org_rename" && e.payload.references.length === 2);
  assert.equal(fake.writes("update party set").length, 0);
});

test("an org party with nobody attached is renamed, unless another org already has that identity", async () => {
  const ok = new Fake(basePlan({ kind: "org", name: "Harbr Point Legal", org: null,
    refs: [{ source: "vendor.party_id", n: "1" }] }));
  const out = await call(ok, { fields: { name: "Harbor Point Legal LLC" } });
  assert.deepEqual(out.updated, ["name"]);
  const taken = new Fake(basePlan({ kind: "org", name: "Harbr Point Legal", org: null,
    existing: [{ id: EXISTING_ORG, name: "Harbor Point Legal LLC" }] }));
  await assert.rejects(call(taken, { fields: { name: "Harbor Point Legal LLC" } }),
    e => e.payload.error === "org_name_taken");
  assert.equal(taken.writes("update party set").length, 0);
});

test("org is a person's field; an org party's own name goes through fields.name", async () => {
  const fake = new Fake(basePlan({ kind: "org", org: null }));
  await assert.rejects(call(fake, { fields: { org: "Anything" } }),
    e => e.payload.error === "org_on_org_party");
});

test("update-party-contact still resolves through the shared resolver and refuses identity fields", async () => {
  const plan = basePlan();
  plan["select phone from party where id=$1"] = [{ phone: null }];
  const fake = new Fake(plan);
  const out = await TOOLS["update-party-contact"].handler(fake, joe, {
    idempotency_key: "upc-1", party: "P-0301", base_version: 4,
    fields: { phone: "850-555-0100" }, source: "practice website" });
  assert.equal(out.party_id, TARGET);
  assert.deepEqual(out.updated, ["phone"]);
  await assert.rejects(TOOLS["update-party-contact"].handler(new Fake(basePlan()), joe, {
    idempotency_key: "upc-2", party: "P-0301", base_version: 4,
    fields: { name: "Alex Morgan" }, source: "practice website" }),
  e => e.payload.error === "no_updatable_fields" && /correct-party-identity/.test(e.payload.hint));
});


test("an org used by a client and vendor with no people cannot be renamed", async () => {
  const refs = [{ source: "client.party_id", n: "1" }, { source: "vendor.party_id", n: "1" }];
  const fake = new Fake(basePlan({ kind: "org", name: "Harbr Point Legal", org: null, refs }));
  await assert.rejects(call(fake, { fields: { name: "Harbor Point Legal LLC" } }),
    e => e.payload.error === "shared_org_rename" &&
      JSON.stringify(e.payload.references) === JSON.stringify(refs.map(r => ({ ...r, n: Number(r.n) }))));
  assert.equal(fake.writes("update party set").length, 0);
  assert.equal(fake.events().length, 0);
});

test("a person on an org used by a client and vendor moves alone to the corrected org", async () => {
  const refs = [{ source: "client.party_id", n: "1" }, { source: "vendor.party_id", n: "1" }];
  const fake = new Fake(basePlan({ refs, refsAfter: refs }));
  const out = await call(fake, { fields: { org: "Harbor Point Legal LLC" } });
  assert.equal(out.org.mode, "mint_and_repoint");
  assert.equal(fake.writes("update party set name=").length, 0);
  assert.deepEqual(fake.writes("update party set org_id=")[0][1], [NEW_ORG, joe.id, TARGET]);
  assert.deepEqual(out.org.references_before, refs.map(r => ({ ...r, n: Number(r.n) })));
  assert.equal(out.org.old_org_left_empty, undefined);
});

test("a concurrent org creator wins without a false name creation event", async () => {
  const plan = basePlan({ org: null });
  plan["select org_party_id($1,$2) as id"] = [{ id: EXISTING_ORG }];
  plan["and org_identity_key(name)=$1"] = (_, db) =>
    db.writes("lock table party in exclusive mode").length
      ? [{ id: EXISTING_ORG, name: "HARBOR Point Legal LLC" }] : [];
  const fake = new Fake(plan);
  const out = await call(fake, { fields: { org: "Harbor Point Legal LLC" } });
  assert.equal(out.org.mode, "repoint_existing");
  const lock = fake.calls.findIndex(([sql]) => sql === "lock table party in exclusive mode");
  const rowLock = fake.calls.findIndex(([sql]) => sql === "select version from party where id=$1 for update");
  assert.ok(lock >= 0 && lock < rowLock, "take the table lock before a party row lock to avoid lock upgrades");
  assert.equal(fake.writes("org_party_id").length, 0);
  assert.deepEqual(fake.writes("update party set org_id=")[0][1], [EXISTING_ORG, joe.id, TARGET]);
  const events = fake.events().map(([, p]) => p);
  assert.equal(events.length, 1);
  assert.equal(events[0][4], TARGET);
  assert.equal(events[0][5], "org_id");
  assert.deepEqual(JSON.parse(events[0][6]), { org_id: null, org_name: null });
  assert.deepEqual(JSON.parse(events[0][7]), {
    org_id: EXISTING_ORG, org_name: "HARBOR Point Legal LLC", mode: "repoint_existing" });
});
