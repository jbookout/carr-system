import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  evaluateRightsReceipt,
  publicValueIsSafe,
  validateEvidenceRightsLineage,
  validateProjectionFact,
} from "../src/tour-operations-contract.js";

const root = path.resolve(import.meta.dirname, "../..");
const fixture = JSON.parse(fs.readFileSync(path.join(root, "mcp-server/test/fixtures/tour-operations-foundation.v1.json"), "utf8"));
const at = fixture.projection.as_of;
const rightsRequest = { at, fieldKey: "display.name", useClass: "client_public_display", lineage: [], revocations: [] };
const accepts = result => assert.ok(result, "valid rights/provenance input must be accepted");

test("rights are current only when effective, unexpired, unrevoked, and explicitly allowed", () => {
  accepts(evaluateRightsReceipt(fixture.rights_receipt, rightsRequest));
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, effective_at: "2026-08-26T00:00:00Z" }, rightsRequest), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_(?:NOT_)?YET_EFFECTIVE|NOT_EFFECTIVE/);
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, expires_at: "2026-08-25T12:00:00Z" }, rightsRequest), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_EXPIRED|EXPIRED/);
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, status: "revoked", revoked_at: "2026-08-24T00:00:00Z" }, rightsRequest), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_REVOKED|REVOKED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, { ...rightsRequest, useClass: "internal_export" }), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_USE_NOT_ALLOWED|USE_NOT_ALLOWED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, { ...rightsRequest, fieldKey: "internal_note" }), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_FIELD_NOT_ALLOWED|FIELD_NOT_ALLOWED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    lineage: [{ ...fixture.rights_receipt, id: "successor", receipt_version: 2, effective_at: "2026-08-20T00:00:00Z", supersedes_receipt_id: fixture.rights_receipt.id }],
  }), /PUBLIC_RIGHTS_SUPERSEDED|RIGHTS_SUPERSEDED|SUPERSEDED/);
  accepts(evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    lineage: [{ ...fixture.rights_receipt, id: "other-provider-successor", provider: "other-provider", receipt_version: 2, effective_at: "2026-08-20T00:00:00Z" }],
  }));
});

test("evidence-to-assertion rights lineage binds tenant, receipt, provider, policy, and separate observation/retrieval times", () => {
  accepts(validateEvidenceRightsLineage(fixture.source_evidence, fixture.field_assertion, fixture.rights_receipt));
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_receipt_id: "wrong" }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS|PROVENANCE|RIGHTS/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_provider: "other-provider" }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS|PROVENANCE|PROVIDER/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_policy_key: "other-policy" }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS|PROVENANCE|POLICY/);
  assert.throws(() => validateEvidenceRightsLineage(fixture.source_evidence, { ...fixture.field_assertion, rights_receipt_id: "wrong" }, fixture.rights_receipt), /ASSERTION_RIGHTS|PROVENANCE|RIGHTS/);
  assert.throws(() => validateEvidenceRightsLineage(fixture.source_evidence, { ...fixture.field_assertion, observed_at: "not-a-time" }, fixture.rights_receipt), /OBSERVED|EFFECTIVE|RIGHTS/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, retrieved_at: "not-a-time" }, fixture.field_assertion, fixture.rights_receipt), /RETRIEVED|EFFECTIVE|RIGHTS/);
  assert.notEqual(fixture.field_assertion.observed_at, fixture.source_evidence.retrieved_at, "observation and retrieval timestamps must remain distinct provenance facts");
});

test("public assets use opaque public references and never arbitrary URLs", () => {
  assert.equal(publicValueIsSafe("photos", [{ asset_ref: "asset:public:photo-0000000001", alt: "Front", source: "county-records" }]), true);
  assert.equal(publicValueIsSafe("photos", [{ asset_ref: "asset:public:cGhvdG8tMQ0000000", alt: "Front", source: "county-records" }]), true);
  for (const url of ["https://evil.invalid/photo.jpg", "http://provider.invalid/photo.jpg", "javascript:alert(1)", "asset:private:photo-1"]) {
    assert.equal(publicValueIsSafe("photos", [{ asset_ref: url, alt: "unsafe", source: "untrusted" }]), false, url);
  }
});

test("existing projection fact validation retains established fail-closed rights errors", () => {
  const { projection_fact: fact, field_assertion: assertion, membership, projection, rights_receipt: rights } = fixture;
  assert.equal(validateProjectionFact(fact, assertion, membership, projection, rights), true);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, { ...rights, effective_at: "2026-08-26T00:00:00Z" }), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, { ...rights, status: "revoked", revoked_at: "2026-08-24T00:00:00Z" }), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, rights, [{ ...rights, receipt_version: 2, effective_at: "2026-08-20T00:00:00Z" }]), /PUBLIC_RIGHTS_SUPERSEDED/);
});
