import assert from "node:assert/strict";
import test from "node:test";
import { tourPropertyJurisdictionTools } from "../src/tour-property-jurisdiction.js";

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const actor = { id: "actor-00000000-0000-4000-8000-000000000001", slug: "joe", human: true };
const ids = {
  property: "10000000-0000-4000-8000-000000000001",
  identifier: "20000000-0000-4000-8000-000000000001",
  candidate: "30000000-0000-4000-8000-000000000001",
  receipt: "40000000-0000-4000-8000-000000000001",
  evidence: "50000000-0000-4000-8000-000000000001",
  rights: "60000000-0000-4000-8000-000000000001",
};
const idempotency = "70000000-0000-4000-8000-000000000001";
const digest = value => `sha256:${value.repeat(64)}`;

function harness() {
  const calls = [];
  const envelopes = [];
  const events = [];
  const client = {
    async query(sql, params) {
      calls.push({ sql, params });
      if (sql.includes("append_tour_property_identifier_assertion")) return { rows: [{ property_identifier_assertion_id: ids.identifier }] };
      if (sql.includes("append_tour_coordinate_candidate")) return { rows: [{ coordinate_candidate_id: ids.candidate }] };
      if (sql.includes("append_tour_entrance_verification_receipt")) return { rows: [{ verification_receipt_id: ids.receipt }] };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
  const withEnvelope = async (c, a, verb, args, fn) => {
    assert.equal(c, client);
    assert.equal(a, actor);
    assert.equal(args.idempotency_key, idempotency);
    envelopes.push({ verb, args });
    return fn();
  };
  const writeEvent = async (...args) => { events.push(args); };
  return { client, calls, envelopes, events, tools: tourPropertyJurisdictionTools({ withEnvelope, writeEvent, ToolError }) };
}

const source = {
  source_evidence_id: ids.evidence,
  rights_receipt_id: ids.rights,
  observed_at: "2026-08-27T12:00:00Z",
};

test("Slice 3 exposes only the three reviewed 0395 seams and sends exact database payload keys", async () => {
  const h = harness();
  assert.deepEqual(Object.keys(h.tools).sort(), [
    "append-tour-coordinate-candidate",
    "append-tour-entrance-verification-receipt",
    "append-tour-property-identifier-assertion",
  ]);
  assert.equal(h.tools["append-tour-property-identifier-assertion"].authorityOnly, true);
  assert.equal(h.tools["append-tour-entrance-verification-receipt"].authorityOnly, true);
  assert.equal(h.tools["append-tour-coordinate-candidate"].authorityOnly, undefined);

  assert.deepEqual(await h.tools["append-tour-property-identifier-assertion"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, identifier_scheme: "county_parcel",
    identifier_value: "012S290100000000", normalized_identifier: "012s290100000000",
    ...source, confidence: "high", review_state: "reviewed", assertion_digest: digest("a"),
  }), { ok: true, property_identifier_assertion_id: ids.identifier });
  const identifier = JSON.parse(h.calls.at(-1).params[0]);
  assert.deepEqual(Object.keys(identifier).sort(), [
    "assertion_digest", "confidence", "identifier_scheme", "identifier_value", "normalized_identifier",
    "observed_at", "organization_tenant_id", "property_id", "review_state", "rights_receipt_id", "source_evidence_id",
  ]);
  assert.equal(identifier.organization_tenant_id, "carr-internal");
  assert.equal(identifier.actor_id, undefined);

  assert.deepEqual(await h.tools["append-tour-coordinate-candidate"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, coordinate_role: "entrance",
    latitude: 30.445123, longitude: -87.189321, precision_class: "entrance",
    ...source, provider: null, review_state: "reviewed", access_notes: null,
  }), { ok: true, coordinate_candidate_id: ids.candidate });
  const coordinate = JSON.parse(h.calls.at(-1).params[0]);
  assert.deepEqual(Object.keys(coordinate).sort(), [
    "access_notes", "coordinate_role", "latitude", "longitude", "observed_at", "organization_tenant_id",
    "precision_class", "property_id", "provider", "review_state", "rights_receipt_id", "source_evidence_id",
  ]);
  assert.equal(coordinate.provider, "");
  assert.equal(coordinate.access_notes, "");

  assert.deepEqual(await h.tools["append-tour-entrance-verification-receipt"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, coordinate_candidate_id: ids.candidate,
    verified_at: "2026-08-27T12:30:00Z", evidence_reference: "inspection:2026-08-27:entrance",
    native_navigation_proof: {
      platform: "apple_maps", tested_at: "2026-08-27T12:31:00Z", travel_mode: "driving", evidence_digest: digest("b"),
    },
    receipt_digest: digest("c"),
  }), { ok: true, verification_receipt_id: ids.receipt });
  const receipt = JSON.parse(h.calls.at(-1).params[0]);
  assert.deepEqual(Object.keys(receipt).sort(), [
    "coordinate_candidate_id", "evidence_reference", "native_navigation_proof", "organization_tenant_id",
    "property_id", "receipt_digest", "verified_at", "verifier_actor_id",
  ]);
  assert.equal(receipt.verifier_actor_id, actor.id);
  assert.equal(h.envelopes.length, 3);
  assert.equal(h.events.length, 3);
});

test("tenant and verifier are server-derived; caller authority selectors are refused before a database call", async () => {
  for (const injected of [
    { organization_tenant_id: "other" }, { tenant: "other" }, { actor_id: "other" }, { reviewer: "other" },
  ]) {
    const h = harness();
    await assert.rejects(
      h.tools["append-tour-property-identifier-assertion"].handler(h.client, actor, {
        idempotency_key: idempotency, property_id: ids.property, identifier_scheme: "legacy",
        identifier_value: "legacy-1", normalized_identifier: "legacy-1",
        ...source, confidence: "unknown", review_state: "unreviewed", assertion_digest: digest("d"), ...injected,
      }),
      error => error instanceof ToolError && error.payload.error === "caller_authority_field_forbidden",
    );
    assert.equal(h.calls.length, 0);
    assert.equal(h.envelopes.length, 0);
  }
});

test("entrance verification refuses sponsored and machine actors before database access", async () => {
  const h = harness();
  await assert.rejects(
    h.tools["append-tour-entrance-verification-receipt"].handler(h.client, {
      id: "actor-00000000-0000-4000-8000-000000000002", slug: "codex", human: false,
      sponsoring_human_slug: "joe",
    }, {
      idempotency_key: idempotency, property_id: ids.property, coordinate_candidate_id: ids.candidate,
      verified_at: "2026-08-27T12:30:00Z", evidence_reference: "inspection:2026-08-27:entrance",
      native_navigation_proof: {
        platform: "apple_maps", tested_at: "2026-08-27T12:31:00Z", travel_mode: "driving", evidence_digest: digest("b"),
      },
      receipt_digest: digest("c"),
    }),
    error => error instanceof ToolError && error.payload.error === "tour_human_verification_required",
  );
  assert.equal(h.calls.length, 0);
  assert.equal(h.envelopes.length, 0);
});

test("unknown fields, noncanonical provider roles, unnormalized identifiers, and malformed receipt fields fail closed", async () => {
  const h = harness();
  await assert.rejects(h.tools["append-tour-coordinate-candidate"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, coordinate_role: "entrance",
    latitude: 30.4, longitude: -87.1, precision_class: "entrance",
    ...source, provider: "geocoder", review_state: "reviewed", access_notes: null,
  }), error => error instanceof ToolError && error.payload.error === "tour_provider_coordinate_role_invalid");
  await assert.rejects(h.tools["append-tour-property-identifier-assertion"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, identifier_scheme: "county_parcel",
    identifier_value: "ABC", normalized_identifier: "ABC",
    ...source, confidence: "high", review_state: "reviewed", assertion_digest: digest("e"),
  }), error => error instanceof ToolError && error.payload.error === "tour_identifier_not_normalized");
  await assert.rejects(h.tools["append-tour-entrance-verification-receipt"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, coordinate_candidate_id: ids.candidate,
    verified_at: "bad", evidence_reference: "inspection",
    native_navigation_proof: { platform: "google_maps", tested_at: "bad", travel_mode: "flying", evidence_digest: "bad", extra: true },
    receipt_digest: "bad", extra: true,
  }), error => error instanceof ToolError && error.payload.error === "tour_input_unknown_field");
  await assert.rejects(h.tools["append-tour-entrance-verification-receipt"].handler(h.client, actor, {
    idempotency_key: idempotency, property_id: ids.property, coordinate_candidate_id: ids.candidate,
    verified_at: "2026-08-27T12:30:00Z", evidence_reference: "inspection",
    native_navigation_proof: { platform: "google_maps", tested_at: "2026-08-27T12:31:00Z", travel_mode: "driving", evidence_digest: digest("f"), extra: true },
    receipt_digest: "bad",
  }), error => error instanceof ToolError && error.payload.error === "tour_native_navigation_proof_invalid");
  assert.equal(h.calls.length, 0);
  assert.equal(h.envelopes.length, 0);
});
