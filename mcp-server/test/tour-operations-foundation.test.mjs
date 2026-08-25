import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { validateProjectionFact } from "../src/tour-operations-contract.js";

const root = path.resolve(import.meta.dirname, "../..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const contract = JSON.parse(read("workspace/contracts/tour-operations-foundation.v1.json"));
const fixture = JSON.parse(read("mcp-server/test/fixtures/tour-operations-foundation.v1.json"));
const migration = read("migrations/0317_tour_operations_foundation.sql");

test("foundation contract preserves provenance, conflicts, audit, rights versions and tenant integrity", () => {
  assert.equal(contract.version, "1.2.0");
  for (const field of ["property_id", "field_key", "source_evidence_id", "observed_at", "effective_from", "rights_receipt_id", "confidence", "data_classification"]) assert(contract.canonical_record_policy.required_fact_metadata.includes(field), field);
  for (const entity of ["FactConflict", "AuditEvent", "TourPropertyMembership", "ProjectionFact"]) assert.ok(contract.entities[entity], entity);
  assert.match(contract.entities.RightsReceipt.rule, /immutable versioned.*fail closed/i);
  assert.match(contract.canonical_record_policy.tenant_integrity, /tenant-qualified/i);
  assert.match(contract.entities.Property.rule, /identity-only/i);
});

test("normalized projection facts refuse cross-tenant, unreviewed, nonpublic, and route-mismatched data", () => {
  const { projection_fact: fact, field_assertion: assertion, membership } = fixture;
  assert.equal(validateProjectionFact(fact, assertion, membership), true);
  assert.throws(() => validateProjectionFact({...fact, display_field_key: "internal_note"}, assertion, membership), /PUBLIC_FIELD_NOT_ALLOWLISTED/);
  assert.throws(() => validateProjectionFact({...fact, display_field_key: "display.address"}, assertion, membership), /PUBLIC_FIELD_RELABEL_REFUSED/);
  assert.throws(() => validateProjectionFact({...fact, organization_tenant_id: "other"}, assertion, membership), /TENANT_SCOPE_REFUSED/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, review_state: "unreviewed"}, membership), /PUBLIC_ASSERTION_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, data_classification: "internal"}, membership), /PUBLIC_ASSERTION_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, route_version: 2}), /ROUTE_VERSION_MISMATCH/);
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
  assert.match(migration, /projection fact lacks selected reviewed public assertion/i);
  for (const table of ["tour_source_evidence", "tour_field_assertion", "tour_cheat_sheet_revision", "tour_audit_event"]) assert.match(migration, new RegExp(`create trigger ${table}_append_only before update or delete`, "i"), table);
  assert.match(migration, /rights receipt refuses source intake/i); assert.match(migration, /rights receipt refuses asserted field\/use/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table|delete\s+from/i); assert.match(migration, /commit;/i);
});
