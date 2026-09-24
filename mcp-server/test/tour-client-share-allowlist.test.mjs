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
import {
  CLIENT_ROUTE_LABEL_PATTERN, CLIENT_TEXT_DIGIT_JOIN, CLIENT_TEXT_MAX_CHARS, CLIENT_TEXT_RULES, CLIENT_TEXT_SPACE_RUN, CLIENT_TEXT_SUITE_RANGE,
  clientSafeMetric, clientTextViolation, isClientRouteLabel, isClientSafeText, normalizeClientText,
} from "../src/tour-client-value-safety.js";
import { renderTourPacket, TourPacketRenderError } from "../src/tour-packet-render.js";
import { tourRightsProjectionTools } from "../src/tour-rights-projection.js";
import { projectTourClientMap, projectTourClientPacket, tourSharingBrowserAccess, tourSharingTools } from "../src/tour-sharing.js";

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
    assert.match(text, /and ops\.tour_public_projection_client_safe\(p\.organization_tenant_id,p\.id\)/, fn);
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
      // an ALLOWED key carrying internal text is withheld by the value rule
      fact("parking", `4 per 1000. ${SENTINEL.ownerContact}`, 7),
      fact("size", { value: 4200, unit: "SF", label: SENTINEL.accessNote }, 8),
      // an internal KEY is withheld even when its text is innocuous
      fact("access", "Side entrance", 9),
    ],
  };
  const h = rightsHarness(legacy);
  const result = await h.tools["read-tour-public-projection"].handler(h.client, actor, { projection_id: ids.projection });
  assert.deepEqual(result.projection.facts.map(item => item.display_field_key), ["display.name", "display.address"]);
  assert.equal(result.projection.withheld_internal_fact_count, 7);
  for (const leaked of [SENTINEL.accessNote, SENTINEL.caveat, SENTINEL.ownerContact, SENTINEL.note, "Side entrance"])
    assert.equal(JSON.stringify(result).includes(leaked), false, leaked);

  // A projection whose every fact is withheld is refused, not returned empty.
  const onlyInternal = rightsHarness({ ...legacy, facts: legacy.facts.filter(item => !["display.name", "display.address"].includes(item.display_field_key)) });
  await assert.rejects(
    onlyInternal.tools["read-tour-public-projection"].handler(onlyInternal.client, actor, { projection_id: ids.projection }),
    error => error instanceof ToolError && error.payload.error === "tour_public_projection_invalid" && error.payload.field === "facts");
});

// A database row that carries far more than the allowlist: every key here that
// is not an envelope key or an allowlisted column must be dropped.
const hostileStop = {
  property_ref: "property:public:abcdefghijklmnop", route_sequence: 1, route_label: "A",
  name: "Medical Plaza", address: "100 Clinic Way", suite: "Suite 200", property_type: "medical_office",
  size: { value: 4200, unit: "SF" },
  asking_economics: { value: 24, currency: "USD", period: "NNN" },
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

test("an allowlisted column holding a non-scalar value refuses the packet rather than passing through", () => {
  for (const stop of [{ ...hostileStop, suite: { text: "Suite 200", notes: SENTINEL.note } }, { ...hostileStop, parking: [SENTINEL.ownerContact] }])
    assert.equal(projectTourClientPacket({ ...hostilePacket, stops: [stop] }), null);
  // A column the database left empty (SQL null) is simply absent.
  assert.equal(projectTourClientPacket({ ...hostilePacket, stops: [{ ...hostileStop, suite: null }] }).stops[0].suite, undefined);
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

// ---------------------------------------------------------------------------
// VALUE safety (review of #1242): an allowed KEY must not carry internal TEXT.
// One rule -- tour-client-value-safety.js / ops.tour_client_text_violation() --
// guards the database seal and reads, the browser share and the PDF.
// ---------------------------------------------------------------------------

// One corpus, two engines. Ordinary CRE listing text a client is meant to see
// must pass; contact, access and internal-note text must be refused under the
// named rule. test/tour-client-share-allowlist-postgres.sql runs every one of
// these strings through ops.tour_client_text_violation() too (asserted below).
const CORPUS = JSON.parse(fs.readFileSync(path.join(root, "mcp-server/test/fixtures/tour-client-text-corpus.json"), "utf8"));
const ORDINARY = CORPUS.ordinary;
const SMUGGLED = [
  ...CORPUS.refused.map(([value]) => value),
  "A".repeat(CLIENT_TEXT_MAX_CHARS + 1),
  "Available\nnow",
];

test("the client value rule passes ordinary CRE text and refuses contact, access and internal-note text by name", () => {
  assert.ok(ORDINARY.length >= 60 && CORPUS.refused.length >= 30);
  for (const value of ORDINARY) assert.equal(clientTextViolation(value), null, JSON.stringify(value));
  for (const [value, rule] of CORPUS.refused) assert.equal(clientTextViolation(value), rule, JSON.stringify(value));
  assert.equal(clientTextViolation("A".repeat(CLIENT_TEXT_MAX_CHARS)), null);
  assert.equal(clientTextViolation("A".repeat(CLIENT_TEXT_MAX_CHARS + 1)), "too_long");
  // Counted in code points, as PostgreSQL char_length() counts.
  assert.equal(clientTextViolation("\u{1F3E5}".repeat(CLIENT_TEXT_MAX_CHARS)), null);
  assert.equal(clientTextViolation("Available\nnow"), "control_character");
  for (const [value, rule] of [[null, "not_text"], [undefined, "not_text"], [4, "not_text"], [{}, "not_text"], ["", "empty"], ["   ", "empty"],
    ["   ", "empty"], ["　", "empty"]])
    assert.equal(clientTextViolation(value), rule, JSON.stringify(value));
  // Unicode spaces are read as spaces, and space runs as one space -- the text
  // a browser shows and the PDF prints.
  assert.equal(normalizeClientText("  Suite  210  "), "Suite 210");
  assert.equal(normalizeClientText("251   555    0100"), "251 555 0100");
  // The cap is measured on that text: a padded 120-character value passes.
  assert.equal(clientTextViolation(` ${"A".repeat(CLIENT_TEXT_MAX_CHARS)} `), null);
  assert.equal(clientTextViolation(`A${" ".repeat(10)}${"A".repeat(CLIENT_TEXT_MAX_CHARS - 2)}`), null);
  for (const value of SMUGGLED) assert.equal(isClientSafeText(value), false, JSON.stringify(value));
  for (const label of ["A", "B", "12", "A1"]) assert.equal(isClientRouteLabel(label), true, label);
  for (const label of ["Stop 1", "ABCD", "", " A", "A-", "Stop A: Owner Bob 251-555-0100", null])
    assert.equal(isClientRouteLabel(label), false, String(label));
});

// How the proof spells a corpus string: a plain literal, or a U&'' literal
// with \XXXX escapes when it holds anything beyond printable ASCII (the
// no-break and thin spaces, en dashes and ± of the corpus).
function sqlLiteral(value) {
  if (/^[\x20-\x7e]*$/.test(value)) return `'${value.replaceAll("'", "''")}'`;
  const body = Array.from(value).map(ch => {
    if (ch === "'") return "''";
    if (ch === "\\") return "\\\\";
    const code = ch.codePointAt(0);
    if (code >= 0x20 && code <= 0x7e) return ch;
    return code <= 0xffff ? `\\${code.toString(16).padStart(4, "0")}` : `\\+${code.toString(16).padStart(6, "0")}`;
  }).join("");
  return `U&'${body}'`;
}

test("the Postgres proof runs the same corpus through the database rule", () => {
  const proof = fs.readFileSync(path.join(root, "mcp-server/test/tour-client-share-allowlist-postgres.sql"), "utf8");
  const literal = sqlLiteral;
  for (const value of ORDINARY) assert.ok(proof.includes(literal(value)), `proof lacks ordinary ${value}`);
  for (const [value, rule] of CORPUS.refused)
    assert.ok(proof.includes(`(${literal(value)},${literal(rule)})`), `proof lacks refused ${value} -> ${rule}`);
});

test("size and asking-economics metrics pass only when every part passes", () => {
  assert.deepEqual(clientSafeMetric({ value: 4200, unit: "SF" }), { value: 4200, unit: "SF" });
  assert.deepEqual(clientSafeMetric({ value: "4,200", unit: "RSF", label: "4,200 RSF @ $28.50/SF" }),
    { value: "4,200", unit: "RSF", label: "4,200 RSF @ $28.50/SF" });
  assert.deepEqual(clientSafeMetric({ min: 20, max: 25, currency: "USD", period: "NNN", label: "Rate" }),
    { min: 20, max: 25, currency: "USD", period: "NNN", label: "Rate" });
  for (const metric of [
    { value: 4200, unit: "SF", label: "owner cell 251-555-0100" },
    { value: 24, currency: "USD", period: "NNN gate code 4411" },
    { value: 24, unit: "U".repeat(CLIENT_TEXT_MAX_CHARS + 1) },
    { value: "call 251-555-0100" },
    { value: "251 555 01 00", unit: "SF" },
    { value: 4200, unit: "SF", verifier: "internal" },
    { value: 4200, unit: { nested: "no" } },
    { min: 30, max: 20 },
    { unit: "SF" },
    { value: Infinity },
  ]) assert.equal(clientSafeMetric(metric), undefined, JSON.stringify(metric));
});

function sqlBody(migration, fn) {
  return migration.split(new RegExp(`create or replace function ops\\.${fn}\\(`, "i"), 2)[1]?.split(/\$\$;/, 1)[0];
}

test("the JavaScript value rule and the database value rule are the same text", () => {
  const migration = fs.readFileSync(path.join(root, "migrations/0591_tour_client_field_allowlist.sql"), "utf8");
  const rules = sqlBody(migration, "tour_client_text_rules");
  assert.ok(rules, "migration defines ops.tour_client_text_rules()");
  const sqlRules = [...rules.matchAll(/\((\d+), '([a-z_]+)', '([a-z]+)', '((?:[^']|'')*)'\)/g)]
    .map(match => ({ ordinal: Number(match[1]), rule: match[2], target: match[3], pattern: match[4].replaceAll("''", "'") }));
  assert.deepEqual(sqlRules.map(({ ordinal }) => ordinal), CLIENT_TEXT_RULES.map((_, index) => index + 1));
  assert.deepEqual(sqlRules.map(({ rule, target, pattern }) => ({ rule, target, pattern })), CLIENT_TEXT_RULES.map(entry => ({ ...entry })));
  assert.ok(migration.includes(`select '${CLIENT_TEXT_DIGIT_JOIN.pattern}'::text`), "digit join pattern parity");
  assert.ok(migration.includes(`select '${CLIENT_TEXT_SUITE_RANGE.pattern}'::text`), "suite range pattern parity");
  assert.ok(migration.includes(`select '${CLIENT_TEXT_SPACE_RUN}'::text`), "space run pattern parity");
  assert.ok(sqlBody(migration, "tour_client_text_normalize").includes(
    "regexp_replace(regexp_replace(p_text, ops.tour_client_text_space_run_pattern(), ' ', 'g'), '^ | $', '', 'g')"), "normalize parity");
  assert.match(sqlBody(migration, "tour_client_text_violation"), /from \(select ops\.tour_client_text_normalize\(p_text\) t\) n/);
  const violation = sqlBody(migration, "tour_client_text_violation");
  assert.ok(violation.includes(`ops.tour_client_text_digit_join_pattern(), '${CLIENT_TEXT_DIGIT_JOIN.replacement.replace("$1", "\\1")}', 'g')`));
  assert.ok(violation.includes(`ops.tour_client_text_suite_range_pattern(), '${CLIENT_TEXT_SUITE_RANGE.replacement.replace("$1", "\\1")}', 'gi')`));
  assert.match(violation, /~\* r\.pattern\s+order by r\.ordinal/);
  assert.match(migration, new RegExp(`ops\\.tour_client_text_max_chars\\(\\)\\s*returns integer language sql immutable parallel safe as \\$\\$ select ${CLIENT_TEXT_MAX_CHARS} \\$\\$`));
  assert.ok(migration.includes(`select '${CLIENT_ROUTE_LABEL_PATTERN}'::text`), "route label pattern parity");
  // Every client field, whole metrics included, goes through the one rule.
  const valueSafe = sqlBody(migration, "tour_public_value_safe");
  assert.match(valueSafe, /when p_field_key in \('display\.name','display\.address','suite','property_type','size','asking_economics','availability','parking'\) then\s+ops\.tour_client_value_violation\(p_field_key, p_value\) is null/);
  // Every client surface is gated by the one legacy predicate.
  for (const fn of ["read_tour_share_packet", "read_tour_packet_for_render", "read_tour_share_map"])
    assert.match(sqlBody(migration, fn), /and ops\.tour_public_projection_client_safe\(p\.organization_tenant_id,p\.id\)/, fn);
  // Route acceptance refuses a label that could never be sealed.
  assert.match(migration, /before insert on ops\.tour_property_membership\s+for each row execute function ops\.tour_property_membership_client_label_guard\(\)/);
});

const cleanStop = {
  property_ref: "property:public:abcdefghijklmnop", route_sequence: 1, route_label: "A",
  name: "Bayside Medical Plaza", address: "100 Bayside Way, Pensacola, FL", suite: "Suite 210",
  property_type: "Medical office", size: { value: 4200, unit: "SF" },
  asking_economics: { value: 24, currency: "USD", period: "NNN" }, availability: "Available now", parking: "4 per 1000",
};
const secondStop = { ...cleanStop, property_ref: "property:public:qrstuvwxyzabcdef", route_sequence: 2, route_label: "B" };
const TEXT_COLUMNS = ["name", "address", "suite", "property_type", "availability", "parking"];

test("one unsafe value anywhere refuses the whole browser packet, never a trimmed copy", () => {
  const baseline = projectTourClientPacket({ as_of: "2026-08-27T12:15:00Z", stops: [cleanStop, secondStop] });
  assert.deepEqual(baseline.stops, [cleanStop, secondStop]);
  for (const ordinary of ORDINARY)
    assert.notEqual(projectTourClientPacket({ stops: [cleanStop, { ...secondStop, parking: ordinary }] }), null, ordinary);
  for (const smuggled of SMUGGLED) {
    for (const column of TEXT_COLUMNS)
      assert.equal(projectTourClientPacket({ stops: [cleanStop, { ...secondStop, [column]: smuggled }] }), null, `${column}: ${smuggled}`);
    for (const [column, key] of [["size", "label"], ["size", "unit"], ["size", "value"], ["asking_economics", "period"], ["asking_economics", "currency"]])
      assert.equal(projectTourClientPacket({ stops: [cleanStop, { ...secondStop, [column]: { ...secondStop[column], [key]: smuggled } }] }), null,
        `${column}.${key}: ${smuggled}`);
  }
  assert.equal(projectTourClientPacket({ stops: [cleanStop, { ...secondStop, route_label: "Stop B: Owner Bob 251-555-0100" }] }), null);
  // What the client receives is the text the rule judged: Unicode spaces and
  // space runs come out as one ASCII space.
  assert.equal(projectTourClientPacket({ stops: [{ ...cleanStop, parking: " 4/1,000   surface " }] }).stops[0].parking,
    "4/1,000 surface");
  // A stop the client could not identify (no name or no address) is refused too.
  for (const column of ["name", "address"]) {
    const { [column]: _dropped, ...unnamed } = secondStop;
    assert.equal(projectTourClientPacket({ stops: [cleanStop, unnamed] }), null, `missing ${column}`);
  }
});

test("the map share sends only coordinate, opaque ref, order and a stop marker, and refuses a free-text label", () => {
  const point = { latitude: 30.42, longitude: -87.21, property_ref: "property:public:abcdefghijklmnop", route_sequence: 1, route_label: "A" };
  assert.deepEqual(projectTourClientMap({ as_of: "2026-08-27T12:15:00Z", points: [point] }).points, [point]);
  const hostile = projectTourClientMap({ as_of: "2026-08-27T12:15:00Z", tour_name: SENTINEL.clientName, points: [{
    ...point, label: SENTINEL.accessNote, sequence: 1,
    access_notes: SENTINEL.accessNote, owner_contact: SENTINEL.ownerContact, property_id: ids.property,
  }] });
  assert.deepEqual(hostile.points, [point]);
  assert.doesNotMatch(JSON.stringify(hostile), SENTINEL_PATTERN);
  for (const route_label of ["Stop A: Owner Bob 251-555-0100", "Stop 1", null, undefined])
    assert.equal(projectTourClientMap({ points: [point, { ...point, route_sequence: 2, route_label }] }), null, String(route_label));
});

test("the PDF renderer admits exactly the client allowlist and refuses the same smuggled text", () => {
  const packet = { as_of: "2026-08-27T12:00:00Z", caveat: null, properties: [cleanStop] };
  const rendered = renderTourPacket(packet);
  for (const column of Object.values(CLIENT_TOUR_PACKET_COLUMNS))
    assert.ok(Object.hasOwn(rendered.facts.properties[0], column), column);
  for (const ordinary of ORDINARY.filter(value => value.length <= 80))
    renderTourPacket({ ...packet, properties: [{ ...cleanStop, parking: ordinary }] });
  const refused = code => error => error instanceof TourPacketRenderError && error.code === code;
  for (const key of ["caveat", "notes", "owner_contact", "access", "photos", "appointment_start", "brand_new_field"])
    assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...cleanStop, [key]: "x" }] }),
      error => error instanceof TourPacketRenderError && /tour_packet_(unknown|forbidden)_field/.test(error.code), key);
  for (const smuggled of SMUGGLED.filter(value => !value.includes("\n") && value.length <= CLIENT_TEXT_MAX_CHARS)) {
    for (const column of TEXT_COLUMNS)
      assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...cleanStop, [column]: smuggled }] }),
        refused("tour_packet_forbidden_contact"), `${column}: ${smuggled}`);
    assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...cleanStop, size: { value: 4200, unit: "SF", label: smuggled } }] }),
      refused("tour_packet_forbidden_contact"), `size.label: ${smuggled}`);
  }
  // A line break the PDF would print as a space is still the stored value's
  // control character: refused, as the database and the share refuse it.
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...cleanStop, parking: "Available\nnow" }] }),
    refused("tour_packet_forbidden_contact"));
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...cleanStop, parking: "Call 251      555      0100" }] }),
    refused("tour_packet_forbidden_contact"));
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...cleanStop, parking: "P".repeat(CLIENT_TEXT_MAX_CHARS + 1) }] }), refused("tour_packet_overflow"));
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...cleanStop, route_label: "Stop 1" }] }), refused("tour_packet_invalid_route_label"));
});

test("a legacy share with one unsafe stop is refused by the list, the map and the PDF alike", () => {
  const legacyStop = { ...secondStop, route_label: "Stop 2" };
  const point = stop => ({ latitude: 30.42, longitude: -87.21, property_ref: stop.property_ref, route_sequence: stop.route_sequence, route_label: stop.route_label });
  assert.equal(projectTourClientPacket({ stops: [cleanStop, legacyStop] }), null);
  assert.equal(projectTourClientMap({ points: [point(cleanStop), point(legacyStop)] }), null);
  assert.throws(() => renderTourPacket({ as_of: "2026-08-27T12:00:00Z", caveat: null, properties: [cleanStop, legacyStop] }),
    error => error instanceof TourPacketRenderError);
  // ... and all three keep both stops when both are clean.
  assert.equal(projectTourClientPacket({ stops: [cleanStop, secondStop] }).stops.length, 2);
  assert.equal(projectTourClientMap({ points: [point(cleanStop), point(secondStop)] }).points.length, 2);
  assert.equal(renderTourPacket({ as_of: "2026-08-27T12:00:00Z", caveat: null, properties: [cleanStop, secondStop] }).facts.properties.length, 2);
});

test("a browser read of a share the projection refuses is refused, not shown partially", async () => {
  const client = { async query(sql) {
    if (sql.includes("read_tour_share_packet")) return { rows: [{ packet: { as_of: "2026-08-27T12:15:00Z", stops: [cleanStop, { ...secondStop, parking: "Gate code 4411" }] } }] };
    if (sql.includes("read_tour_share_map")) return { rows: [{ map: { as_of: "2026-08-27T12:15:00Z", points: [{ latitude: 30.4, longitude: -87.2, property_ref: cleanStop.property_ref, route_sequence: 1, route_label: "Stop 1" }] } }] };
    throw new Error(sql);
  } };
  const browser = tourSharingBrowserAccess({ ToolError });
  const refusedRead = error => error instanceof ToolError && error.payload.error === "tour_share_access_refused";
  await assert.rejects(browser.readPacket(client, { session_digest: digest("b") }), refusedRead);
  await assert.rejects(browser.readMap(client, { session_digest: digest("b") }), refusedRead);
});
