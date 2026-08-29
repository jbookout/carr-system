import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  CANONICAL_FACT_REQUIRED_FIELDS,
  validateCanonicalFieldAssertion,
  validateProjectionFact,
} from "../src/tour-operations-contract.js";

const root = path.resolve(import.meta.dirname, "../..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const contract = JSON.parse(read("workspace/contracts/tour-operations-foundation.v1.json"));
const fixture = JSON.parse(read("mcp-server/test/fixtures/tour-operations-foundation.v1.json"));
const migration = read("migrations/0318_tour_operations_foundation.sql");

test("foundation contract preserves provenance, conflicts, audit, rights versions and tenant integrity", () => {
  assert.equal(contract.version, "1.7.0");
  for (const field of ["property_id", "field_key", "source_evidence_id", "observed_at", "effective_from", "rights_receipt_id", "confidence", "data_classification"]) assert(contract.canonical_record_policy.required_fact_metadata.includes(field), field);
  for (const entity of ["FactConflict", "AuditEvent", "TourPropertyMembership", "ProjectionFact"]) assert.ok(contract.entities[entity], entity);
  assert.match(contract.entities.RightsReceipt.rule, /immutable versioned.*fail closed/i);
  assert.match(contract.canonical_record_policy.tenant_integrity, /tenant-qualified/i);
  assert.match(contract.entities.Property.rule, /identity-only/i);
});

test("foundation fixture covers every accepted entity and its declared fields", () => {
  assert.deepEqual(Object.keys(fixture.contract_entities).sort(), Object.keys(contract.entities).sort());
  for (const [entityName, entityContract] of Object.entries(contract.entities)) {
    const record = fixture.contract_entities[entityName];
    for (const field of [...entityContract.identity, ...entityContract.required])
      assert.ok(Object.hasOwn(record, field), `${entityName}.${field}`);
  }
});

test("canonical factual fields require provenance, time, rights, confidence and classification", () => {
  const assertion = fixture.field_assertion;
  assert.equal(validateCanonicalFieldAssertion(assertion), true);
  assert.deepEqual(contract.canonical_record_policy.required_fact_metadata,
    ["property_id", "field_key", "value", "source_evidence_id", "observed_at", "effective_from", "effective_to", "rights_receipt_id", "confidence", "data_classification", "review_state"]);
  for (const field of CANONICAL_FACT_REQUIRED_FIELDS) {
    const invalid = {...assertion};
    delete invalid[field];
    assert.throws(() => validateCanonicalFieldAssertion(invalid),
      new RegExp(`FACT_METADATA_REQUIRED:${field}`), field);
  }
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, observed_at: "not-a-time"}), /FACT_METADATA_INVALID:observed_at/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, effective_to: "2026-07-31T00:00:00Z"}), /FACT_EFFECTIVE_INTERVAL_INVALID/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, rights_receipt_id: ""}), /FACT_METADATA_INVALID:rights_receipt_id/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, confidence: "guessed"}), /FACT_METADATA_INVALID:confidence/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, data_classification: "publicish"}), /FACT_METADATA_INVALID:data_classification/);
});

test("normalized projection facts refuse cross-tenant, unreviewed, nonpublic, and route-mismatched data", () => {
  const { projection_fact: fact, field_assertion: assertion, membership, projection, rights_receipt: rights } = fixture;
  assert.equal(validateProjectionFact(fact, assertion, membership, projection, rights), true);
  assert.throws(() => validateProjectionFact({...fact, display_field_key: "internal_note"}, assertion, membership, projection, rights), /PUBLIC_FIELD_NOT_ALLOWLISTED/);
  assert.throws(() => validateProjectionFact({...fact, display_field_key: "display.address"}, assertion, membership, projection, rights), /PUBLIC_FIELD_RELABEL_REFUSED/);
  assert.throws(() => validateProjectionFact({...fact, organization_tenant_id: "other"}, assertion, membership, projection, rights), /TENANT_SCOPE_REFUSED/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, review_state: "unreviewed"}, membership, projection, rights), /PUBLIC_ASSERTION_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, data_classification: "internal"}, membership, projection, rights), /PUBLIC_ASSERTION_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, route_version: 2}, projection, rights), /ROUTE_VERSION_MISMATCH/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, effective_from: "2026-08-26T00:00:00Z"}, membership, projection, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, value: {text: "safe", internal_note: "secret"}}, membership, projection, rights), /PUBLIC_VALUE_UNSAFE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: "not-a-date"}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: "0"}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: "2026-02-30T00:00:00Z"}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: null}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: 0}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, effective_at: "not-a-date"}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, effective_at: null}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, id: "unrelated"}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, allowed_use_classes: "client_public_display", allowed_field_classes: {"display.name": true}}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact({...fact, projection_id: "wrong"}, assertion, membership, projection, rights), /PROJECTION_BINDING_REFUSED/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, tour_id: "wrong"}, projection, rights), /PROJECTION_BINDING_REFUSED/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, selected_at: null}, projection, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, selected_at: "2026-08-25T13:00:00Z"}, projection, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
});

test("migration is additive, tenant-qualified, temporal, rights-safe and append-only", () => {
  assert.match(migration, /^begin;/m); assert.match(migration, /create table if not exists ops\.tour_fact_conflict/i);
  assert.match(migration, /create table if not exists ops\.tour_conflict_resolution_receipt/i);
  assert.match(migration, /create table if not exists ops\.tour_audit_event/i);
  assert.match(migration, /create table if not exists ops\.tour_property_membership/i);
  assert.match(migration, /create table if not exists ops\.tour_public_projection_fact/i);
  assert.match(migration, /foreign key \(organization_tenant_id, property_id\)/i);
  assert.match(migration, /allowed_field_classes jsonb not null, allowed_use_classes jsonb not null/i);
  assert.match(migration, /effective_at timestamptz not null, expires_at timestamptz, revoked_at timestamptz/i);
  assert.match(migration, /effective_to is null or effective_to >= effective_from/i);
  assert.match(migration, /projection fact lacks current public assertion, rights, or safe value/i);
  assert.match(migration, /as_of timestamptz not null/i);
  assert.match(migration, /tour_public_value_safe/i);
  for (const table of ["tour_source_evidence", "tour_field_assertion", "tour_cheat_sheet_revision", "tour_audit_event"]) assert.match(migration, new RegExp(`create trigger ${table}_append_only before update or delete`, "i"), table);
  assert.match(migration, /rights receipt refuses source intake/i); assert.match(migration, /rights receipt refuses asserted field\/use/i);
  assert.match(migration, /nested exception rolls every synthetic row[\s\S]*tour foundation acceptance proof rollback/i);
  assert.match(migration, /rollback is forward-only: revoke grants and quarantine projections/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table/i); assert.match(migration, /commit;/i);
});
