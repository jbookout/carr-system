// V5-J303 client-shared Tours: the client field allowlist is explicit and
// default-deny. Joe's ruling (decision 4ab3933e, 2026-09-24): a client sees
// exactly what is on today's Tour PDF -- property name, address, suite, space
// type, size, asking economics, availability and parking. Notes, owner
// contacts, access notes, internal ids and every other field stay internal,
// and a new field is internal until someone adds it to the allowlist.
//
// These are negative tests: each one plants internal material in the input a
// sealed projection or share response is built from and proves it never comes
// out the other side.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  CLIENT_TOUR_FIELD_KEYS, CLIENT_TOUR_PACKET_COLUMNS, CLIENT_TOUR_STOP_ENVELOPE_KEYS,
  isClientTourFieldKey,
} from "../src/tour-operations-contract.js";
import { tourRightsProjectionTools } from "../src/tour-rights-projection.js";
import { projectTourClientPacket, tourSharingBrowserAccess, tourSharingTools } from "../src/tour-sharing.js";

const root = path.resolve(import.meta.dirname, "../..");

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const actor = { id: "actor-00000000-0000-4000-8000-000000000001", slug: "codex" };
const digest = value => `sha256:${value.repeat(64)}`;
const idempotency = "70000000-0000-4000-8000-000000000001";
const ids = {
  rights: "10000000-0000-4000-8000-000000000001",
  evidence: "20000000-0000-4000-8000-000000000001",
  property: "40000000-0000-4000-8000-000000000001",
  tour: "50000000-0000-4000-8000-000000000001",
  projection: "60000000-0000-4000-8000-000000000001",
  grant: "80000000-0000-4000-8000-000000000001",
  successor: "80000000-0000-4000-8000-000000000002",
};
const assertionId = n => `30000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

// Sentinels: if any of these strings reaches a client-facing output, a test
// fails. They stand for the categories Joe named as internal.
const SENTINEL = {
  accessNote: "Gate code 4411, key under the mat",
  ownerContact: "Owner Bob Landlord 251-555-0100 bob@owner.example",
  note: "Internal note: client is tight on budget",
  clientName: "Acme Pediatrics Client",
  caveat: "Broker-only caveat about the roof",
  appointment: "2026-08-28T14:00:00Z",
};
const SENTINEL_PATTERN = new RegExp(
  [...Object.values(SENTINEL), ids.property, ids.tour, ids.projection, ids.rights, ids.evidence]
    .map(value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"));

const INTERNAL_FIELD_KEYS = [
  // Fields the older public list accepted that the ruling makes internal.
  "access", "photos", "floor_plan", "source_attribution", "as_of", "caveat",
  // Fields that were never client-facing.
  "notes", "owner_contact", "owner_phone", "access_notes", "internal_id",
  "client_name", "tour_name", "appointment_start", "broker_notes",
  // A field nobody has thought of yet is internal by default.
  "brand_new_field",
];

test("the client allowlist is exactly the eight fields on today's Tour PDF and cannot be widened at runtime", () => {
  assert.deepEqual([...CLIENT_TOUR_FIELD_KEYS], [
    "display.name", "display.address", "suite", "property_type", "size",
    "asking_economics", "availability", "parking",
  ]);
  assert.ok(Object.isFrozen(CLIENT_TOUR_FIELD_KEYS));
  assert.ok(Object.isFrozen(CLIENT_TOUR_PACKET_COLUMNS));
  assert.ok(Object.isFrozen(CLIENT_TOUR_STOP_ENVELOPE_KEYS));
  assert.throws(() => { CLIENT_TOUR_FIELD_KEYS.push("access"); }, TypeError);
  for (const key of INTERNAL_FIELD_KEYS) assert.equal(isClientTourFieldKey(key), false, key);
  for (const key of [undefined, null, 1, {}, ["display.name"], "DISPLAY.NAME", " suite"])
    assert.equal(isClientTourFieldKey(key), false, String(key));
  assert.deepEqual(Object.keys(CLIENT_TOUR_PACKET_COLUMNS).sort(), [...CLIENT_TOUR_FIELD_KEYS].sort());
});

test("the JavaScript allowlist and the database allowlist are the same list", () => {
  const migration = fs.readFileSync(path.join(root, "migrations/0591_tour_client_field_allowlist.sql"), "utf8");
  const body = migration.split(/create or replace function ops\.tour_client_field_keys\(\)/i, 2)[1];
  assert.ok(body, "migration defines ops.tour_client_field_keys()");
  const array = body.match(/array\[([^\]]*)\]::text\[\]/i);
  assert.ok(array, "ops.tour_client_field_keys() returns one literal text[]");
  const sqlKeys = [...array[1].matchAll(/'([^']*)'/g)].map(match => match[1]);
  assert.deepEqual(sqlKeys, [...CLIENT_TOUR_FIELD_KEYS]);
  // Both share-facing reads name only allowlisted columns: no caveat, access,
  // photo, floor-plan or appointment column is selected for a client.
  for (const fn of ["read_tour_share_packet", "read_tour_packet_for_render"]) {
    const text = migration.split(new RegExp(`create or replace function ops\\.${fn}\\(`, "i"), 2)[1]?.split(/\$\$;/, 1)[0];
    assert.ok(text, fn);
    assert.match(text, /ops\.tour_client_field_allowed\(f\.display_field_key\)/, fn);
    // The packet-level 'caveat' key stays an explicit null (0586); what must
    // be gone is any per-property column read from a non-allowlisted fact.
    const columns = [...text.matchAll(/display_field_key='([^']*)'/g)].map(match => match[1]);
    assert.deepEqual(columns, [...CLIENT_TOUR_FIELD_KEYS], `${fn} selects exactly the allowlisted columns`);
    for (const forbidden of ["appointment_start", "tour_name", "access_coordinate_status", "notes"])
      assert.equal(text.includes(forbidden), false, `${fn} names ${forbidden}`);
  }
  assert.match(migration, /before insert on ops\.tour_public_projection_fact[\s\S]*tour_projection_fact_client_allowlist_guard/i);
});

function rightsHarness(projection) {
  const calls = [];
  const client = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes("seal_tour_public_projection")) return { rows: [{ projection_digest: digest("f") }] };
    if (sql.includes("read_tour_public_projection")) return { rows: [{ projection }] };
    throw new Error(`unexpected query: ${sql}`);
  } };
  const withEnvelope = async (_c, _a, _verb, _args, fn) => fn();
  return { calls, client, tools: tourRightsProjectionTools({ withEnvelope, writeEvent: async () => {}, ToolError }) };
}

test("sealing refuses every internal or unknown field before the database is touched", async () => {
  for (const display_field_key of INTERNAL_FIELD_KEYS) {
    const h = rightsHarness();
    await assert.rejects(
      h.tools["seal-tour-public-projection"].handler(h.client, actor, {
        idempotency_key: idempotency, projection_id: ids.projection, receipt_digest: digest("d"),
        selected_facts: [
          { property_id: ids.property, field_assertion_id: assertionId(1), display_field_key: "display.name" },
          { property_id: ids.property, field_assertion_id: assertionId(2), display_field_key },
        ],
      }),
      error => error instanceof ToolError && error.payload.error === "tour_selected_facts_invalid"
        && error.payload.reason === "field_not_client_allowlisted" && error.payload.index === 1,
      display_field_key,
    );
    assert.equal(h.calls.length, 0, `${display_field_key} reached the database`);
  }
});

test("sealing accepts the full client field set", async () => {
  const h = rightsHarness();
  const selected = CLIENT_TOUR_FIELD_KEYS.map((display_field_key, index) =>
    ({ property_id: ids.property, field_assertion_id: assertionId(index + 1), display_field_key }));
  const result = await h.tools["seal-tour-public-projection"].handler(h.client, actor, {
    idempotency_key: idempotency, projection_id: ids.projection, receipt_digest: digest("d"), selected_facts: selected,
  });
  assert.deepEqual(result, { ok: true, projection_id: ids.projection, projection_digest: digest("f"), status: "approved" });
  assert.equal(h.calls.length, 1);
});

test("a legacy sealed projection that already holds internal facts reads back without them", async () => {
  const fact = (display_field_key, value, n) => ({
    property_id: ids.property, field_assertion_id: assertionId(n), display_field_key, value,
    source_evidence_id: ids.evidence, rights_receipt_id: ids.rights,
    observed_at: "2026-08-27T12:05:00Z", effective_from: "2026-08-27T00:00:00Z", effective_to: null,
  });
  const legacy = {
    projection_id: ids.projection, tour_id: ids.tour, projection_version: 1, route_version: 1,
    as_of: "2026-08-27T12:15:00Z", projection_digest: digest("f"),
    facts: [
      fact("display.name", "Medical Plaza", 1),
      fact("display.address", "100 Clinic Way", 2),
      fact("access", SENTINEL.accessNote, 3),
      fact("caveat", SENTINEL.caveat, 4),
      fact("source_attribution", SENTINEL.ownerContact, 5),
      fact("photos", [{ asset_ref: "asset:public:abcdefghijklmnop", caption: SENTINEL.note }], 6),
    ],
  };
  const h = rightsHarness(legacy);
  const result = await h.tools["read-tour-public-projection"].handler(h.client, actor, { projection_id: ids.projection });
  assert.deepEqual(result.projection.facts.map(item => item.display_field_key), ["display.name", "display.address"]);
  assert.equal(result.projection.withheld_internal_fact_count, 4);
  for (const leaked of [SENTINEL.accessNote, SENTINEL.caveat, SENTINEL.ownerContact, SENTINEL.note])
    assert.equal(JSON.stringify(result).includes(leaked), false, leaked);
});

// A database row that carries far more than the allowlist: every key here that
// is not an envelope key or an allowlisted column must be dropped.
const hostileStop = {
  property_ref: "property:public:abcdefghijklmnop", route_sequence: 1, route_label: "A",
  name: "Medical Plaza", address: "100 Clinic Way", suite: "Suite 200", property_type: "medical_office",
  size: { value: 4200, unit: "SF", verifier: SENTINEL.note },
  asking_economics: { value: 24, currency: "USD", period: "NNN", owner_floor: SENTINEL.ownerContact },
  availability: "available", parking: "4/1000",
  access: SENTINEL.accessNote, access_notes: SENTINEL.accessNote, notes: SENTINEL.note,
  owner_contact: SENTINEL.ownerContact, owner_phone: "251-555-0100", caveat: SENTINEL.caveat,
  client_name: SENTINEL.clientName, tour_name: SENTINEL.clientName,
  appointment_start: SENTINEL.appointment, appointment_end: SENTINEL.appointment,
  property_id: ids.property, tour_id: ids.tour, projection_id: ids.projection,
  field_assertion_id: assertionId(9), rights_receipt_id: ids.rights, source_evidence_id: ids.evidence,
  photos: [{ asset_ref: "asset:public:abcdefghijklmnop", caption: SENTINEL.note }],
  floor_plan: [{ asset_ref: "asset:public:abcdefghijklmnop" }],
  brand_new_field: SENTINEL.note, label: SENTINEL.note, sequence: 1,
};
const hostilePacket = {
  as_of: "2026-08-27T12:15:00Z", caveat: SENTINEL.caveat, tour_name: SENTINEL.clientName,
  client_name: SENTINEL.clientName, projection_id: ids.projection, notes: SENTINEL.note, stops: [hostileStop],
};

test("a share packet carries only envelope keys and allowlisted columns, never internal material", () => {
  const packet = projectTourClientPacket(hostilePacket);
  assert.deepEqual(Object.keys(packet).sort(), ["as_of", "stops"]);
  assert.equal(packet.stops.length, 1);
  const allowedStopKeys = new Set([...CLIENT_TOUR_STOP_ENVELOPE_KEYS, ...Object.values(CLIENT_TOUR_PACKET_COLUMNS)]);
  for (const key of Object.keys(packet.stops[0])) assert.ok(allowedStopKeys.has(key), `stop leaked key ${key}`);
  assert.deepEqual(packet.stops[0], {
    property_ref: "property:public:abcdefghijklmnop", route_sequence: 1, route_label: "A",
    name: "Medical Plaza", address: "100 Clinic Way", suite: "Suite 200", property_type: "medical_office",
    size: { value: 4200, unit: "SF" }, asking_economics: { value: 24, currency: "USD", period: "NNN" },
    availability: "available", parking: "4/1000",
  });
  assert.doesNotMatch(JSON.stringify(packet), SENTINEL_PATTERN);
});

test("an allowlisted column holding a non-scalar value is dropped rather than passed through", () => {
  const packet = projectTourClientPacket({ ...hostilePacket, stops: [{
    ...hostileStop, suite: { text: "Suite 200", notes: SENTINEL.note }, parking: [SENTINEL.ownerContact],
  }] });
  assert.equal(packet.stops[0].suite, undefined);
  assert.equal(packet.stops[0].parking, undefined);
  assert.doesNotMatch(JSON.stringify(packet), SENTINEL_PATTERN);
});

function sharingHarness() {
  const calls = [], events = [];
  const client = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes("issue_tour_share_grant")) return { rows: [{ share_grant_id: ids.grant, tour_name: SENTINEL.clientName, projection_id: ids.projection }] };
    if (sql.includes("rotate_tour_share_grant")) return { rows: [{ share_grant_id: ids.successor, notes: SENTINEL.note }] };
    if (sql.includes("revoke_tour_share_grant")) return { rows: [{ share_grant_id: ids.grant, owner_contact: SENTINEL.ownerContact }] };
    if (sql.includes("exchange_tour_share_token")) return { rows: [{ exchange: {
      expires_at: "2026-08-28T00:00:00Z", permission_scopes: ["view_packet"], tour_name: SENTINEL.clientName,
      projection_id: ids.projection, client_name: SENTINEL.clientName } }] };
    if (sql.includes("read_tour_share_packet")) return { rows: [{ packet: hostilePacket }] };
    throw new Error(sql);
  } };
  const withEnvelope = async (_c, _a, _verb, _args, fn) => fn();
  return { calls, events, client,
    tools: tourSharingTools({ withEnvelope, writeEvent: async (...event) => events.push(event), ToolError }),
    browser: tourSharingBrowserAccess({ ToolError }) };
}

test("share grant responses carry only the grant id and status", async () => {
  const h = sharingHarness();
  const issue = await h.tools["issue-tour-share-grant"].handler(h.client, actor, {
    idempotency_key: idempotency, projection_id: ids.projection, token_digest: digest("a"),
    permission_scopes: ["view_packet"], expires_at: "2026-08-28T00:00:00Z", receipt_digest: digest("c") });
  assert.deepEqual(issue, { ok: true, share_grant_id: ids.grant, status: "active" });
  const rotate = await h.tools["rotate-tour-share-grant"].handler(h.client, actor, {
    idempotency_key: idempotency, share_grant_id: ids.grant, projection_id: ids.projection, token_digest: digest("b"),
    permission_scopes: ["view_packet"], expires_at: "2026-08-28T00:00:00Z", receipt_digest: digest("c") });
  assert.deepEqual(rotate, { ok: true, share_grant_id: ids.successor, supersedes_share_grant_id: ids.grant, status: "active" });
  const revoke = await h.tools["revoke-tour-share-grant"].handler(h.client, actor, {
    idempotency_key: idempotency, share_grant_id: ids.grant, reason: "Client tour ended",
    receipt_digest: digest("c"), revoked_at: "2026-08-28T00:00:00Z" });
  assert.deepEqual(revoke, { ok: true, share_grant_id: ids.grant, status: "revoked" });
  assert.doesNotMatch(JSON.stringify([issue, rotate, revoke]), new RegExp([SENTINEL.clientName, SENTINEL.note, SENTINEL.ownerContact].join("|")));
});

test("the client's browser exchange and packet read return no internal material", async () => {
  const h = sharingHarness();
  const exchange = await h.browser.exchange(h.client, { token_digest: digest("a"), session_digest: digest("b"),
    session_expires_at: "2026-08-28T00:00:00Z", audit_digest: digest("d") });
  assert.deepEqual(exchange, { ok: true, expires_at: "2026-08-28T00:00:00Z", permission_scopes: ["view_packet"] });
  const packet = await h.browser.readPacket(h.client, { session_digest: digest("b") });
  assert.doesNotMatch(JSON.stringify([exchange, packet]), SENTINEL_PATTERN);
  assert.deepEqual(Object.keys(packet.packet).sort(), ["as_of", "stops"]);
});

test("the client share link is an origin, a fixed path and a random token, with nothing about the client", () => {
  const app = fs.readFileSync(path.join(root, "dealroom/tours/app.js"), "utf8");
  const urls = [...app.matchAll(/const url = `([^`]*)`;/g)].map(match => match[1]);
  assert.equal(urls.length, 1, "exactly one client share URL is built");
  assert.match(urls[0], /^https:\/\/[a-z0-9.-]+\/share#token=\$\{raw\}$/);
  // The only interpolation is `raw`, and `raw` is a fresh 32-byte random token.
  assert.match(app, /const raw = newShareToken\(\);/);
  assert.match(app, /function newShareToken\(\) \{ const bytes = new Uint8Array\(32\); crypto\.getRandomValues\(bytes\); return base64url\(bytes\); \}/);
  // The token travels in the fragment, so it never reaches a server log, and
  // the path carries no tour, projection, grant, client or deal identifier.
  assert.doesNotMatch(urls[0], /projection|tour_?id|grant|client|deal|name|\?/i);
});
