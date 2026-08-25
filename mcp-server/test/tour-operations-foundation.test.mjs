import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const contract = JSON.parse(read("workspace/contracts/tour-operations-foundation.v1.json"));
const fixture = JSON.parse(read("mcp-server/test/fixtures/tour-operations-foundation.v1.json"));
const migration = read("migrations/0317_tour_operations_foundation.sql");

test("tour foundation contract makes provenance, time, rights, confidence and classification mandatory", () => {
  assert.equal(contract.status, "foundation_contract_not_a_client_surface");
  assert.deepEqual(contract.canonical_record_policy.classification, ["public", "client_authorized", "internal", "restricted"]);
  for (const field of ["property_id", "field_key", "value", "source_evidence_id", "observed_at", "effective_from", "effective_to", "rights_receipt_id", "confidence", "data_classification", "review_state"]) {
    assert(contract.canonical_record_policy.required_fact_metadata.includes(field), field);
  }
  assert.match(contract.entities.PublicTourProjection.forbidden.join(" "), /recommendation.*internal_contact.*broker_opinion/i);
  assert.match(contract.public_projection_policy.parity_rule, /exactly one property page/i);
});

test("synthetic fixture is facts-only and internally linked", () => {
  assert.equal(fixture.synthetic, true);
  assert.equal(fixture.field_assertion.property_id, fixture.property.property_id);
  assert.equal(fixture.field_assertion.source_evidence_id, fixture.source_evidence.source_evidence_id);
  assert.equal(fixture.field_assertion.rights_receipt_id, fixture.rights_receipt.rights_receipt_id);
  assert.equal(fixture.public_projection.facts_only, true);
  assert.equal(fixture.public_projection.property_count, fixture.public_projection.render_payload.properties.length);
  assert.equal(Object.keys(fixture.public_projection.render_payload).some(key => contract.public_projection_policy.explicitly_excluded.includes(key)), false);
});

test("migration is additive, protects temporal assertions and refuses public projection leakage", () => {
  assert.match(migration, /^begin;/m);
  assert.match(migration, /create table if not exists ops\.tour_property/i);
  assert.match(migration, /create table if not exists ops\.tour_source_evidence/i);
  assert.match(migration, /create table if not exists ops\.tour_field_assertion/i);
  assert.match(migration, /effective_to is null or effective_to >= effective_from/i);
  assert.match(migration, /rights_receipt_id uuid not null/i);
  assert.match(migration, /facts_only boolean not null check \(facts_only\)/i);
  assert.match(migration, /broker_recommendation.*ranking.*internal_contact.*listing_agent_contact.*internal_note.*client_requirements.*source_credentials.*share_token.*audit_detail/is);
  assert.match(migration, /token_digest text not null unique/i);
  assert.match(migration, /commit;/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table|delete\s+from/i);
});
