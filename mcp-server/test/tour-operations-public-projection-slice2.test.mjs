import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  assertProjectionDigest,
  canonicalProjectionDigest,
  validateProjectionComplete,
  validateProjectionDraft,
} from "../src/tour-operations-contract.js";

const root = path.resolve(import.meta.dirname, "../..");
const fixture = JSON.parse(fs.readFileSync(path.join(root, "mcp-server/test/fixtures/tour-operations-foundation.v1.json"), "utf8"));
const { projection_fact: fact, field_assertion: assertion, membership, projection, source_evidence: evidence, rights_receipt: rights } = fixture;
const addressAssertion = { ...assertion, id: "address-assertion", field_key: "display.address", value: "1 Synthetic Way" };
const addressFact = { ...fact, id: "address-fact", field_assertion_id: addressAssertion.id, display_field_key: addressAssertion.field_key };
const completeFacts = [fact, addressFact];
const completeAssertions = [assertion, addressAssertion];
const completeInput = {
  projection,
  memberships: [membership],
  facts: completeFacts,
  assertions: completeAssertions,
  evidence: [evidence],
  rights: [rights],
  evidenceById: { [evidence.id]: evidence },
  rightsById: { [rights.id]: rights },
  lineage: [],
  revocations: [],
};
const withProjectionBody = (status = "approved") => ({
  ...completeInput,
  projection: { ...projection, status },
});

function sealedProjectionInput() {
  const input = withProjectionBody("draft");
  input.facts = input.facts.map(item => { const itemAssertion = completeAssertions.find(candidate => candidate.id === item.field_assertion_id); return { ...item, value: itemAssertion.value, source_evidence_id: itemAssertion.source_evidence_id, rights_receipt_id: itemAssertion.rights_receipt_id, observed_at: itemAssertion.observed_at, effective_from: itemAssertion.effective_from, effective_to: itemAssertion.effective_to }; });
  const digest = canonicalProjectionDigest(input);
  return {
    input,
    digest,
    projection: {
      ...input.projection,
      status: "approved",
      projection_digest: digest,
      seal_receipt: {
        id: "seal-1",
        organization_tenant_id: fixture.tenant,
        projection_id: projection.id,
        sealed_at: "2026-08-25T12:05:00Z",
        sealed_state: "approved",
        canonical_projection_digest: digest,
        actor_id: "actor:joe",
        receipt_digest: "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      },
    },
  };
}

test("canonical projection digest is key-order independent and detects tampering", () => {
  const first = { ...withProjectionBody("approved"), facts: [fact] };
  const reordered = {
    revocations: [],
    rightsById: { [rights.id]: { ...rights } },
    evidenceById: { [evidence.id]: { ...evidence } },
    assertions: { [assertion.id]: { ...assertion } },
    facts: [{ ...fact }],
    memberships: [{ ...membership }],
    projection: { status: "approved", as_of: projection.as_of, route_version: projection.route_version, projection_version: projection.projection_version, tour_id: projection.tour_id, organization_tenant_id: projection.organization_tenant_id, id: projection.id, facts_only: projection.facts_only },
    lineage: [],
  };
  const digest = canonicalProjectionDigest(first);
  assert.match(digest, /^sha256:[a-f0-9]{64}$/);
  assert.equal(canonicalProjectionDigest(reordered), digest);
  assert.equal(assertProjectionDigest(first, digest), true);
  assert.throws(() => assertProjectionDigest({ ...first, facts: [{ ...fact, display_field_key: "display.address" }] }, digest), /PROJECTION_DIGEST_MISMATCH|DIGEST/);
  assert.throws(() => assertProjectionDigest(first, "sha256:" + "0".repeat(64)), /PROJECTION_DIGEST_MISMATCH|DIGEST/);
});

test("canonical projection digest matches the database UTF-8 byte contract vector", () => {
  const input = {
    projection: {
      organization_tenant_id: "tenant-α",
      tour_id: "10000000-0000-4000-8000-000000000040",
      id: "10000000-0000-4000-8000-000000000060",
      projection_version: 1,
      route_version: 2,
      as_of: "2026-08-25T12:00:00Z",
    },
    facts: [
      {
        property_id: "10000000-0000-4000-8000-000000000011",
        field_assertion_id: "10000000-0000-4000-8000-000000000031",
        route_version: 2,
        display_field_key: "display.address",
      },
      {
        property_id: "10000000-0000-4000-8000-000000000010",
        field_assertion_id: "10000000-0000-4000-8000-000000000030",
        route_version: 2,
        display_field_key: "display.name",
      },
    ],
  };
  assert.equal(canonicalProjectionDigest(input),
    "sha256:73c90187e235a2e7262bf8de28ea4b61f69721cb8e60e8876092d3337d134bb7");
});

test("canonical projection digest binds immutable public-map coordinate selection", () => {
  const base = { ...withProjectionBody("approved"), map_points: [{
    property_id: membership.property_id,
    coordinate_candidate_id: "10000000-0000-4000-8000-000000000070",
    entrance_verification_receipt_id: "10000000-0000-4000-8000-000000000071",
    route_version: projection.route_version,
  }] };
  const digest = canonicalProjectionDigest(base);
  assert.notEqual(digest, canonicalProjectionDigest({ ...base, map_points: [] }));
  assert.throws(() => assertProjectionDigest({ ...base, map_points: [{ ...base.map_points[0], coordinate_candidate_id: "10000000-0000-4000-8000-000000000072" }] }, digest), /PROJECTION_DIGEST_MISMATCH/);
});

test("projection creation is draft-only and a complete seal requires the full selected, rights-checked fact set", () => {
  assert.equal(validateProjectionDraft(projection), true);
  assert.throws(() => validateProjectionDraft({ ...projection, status: "approved" }), /PROJECTION_DRAFT_REQUIRED|DRAFT/);
  assert.throws(() => validateProjectionDraft({ ...projection, status: "published" }), /PROJECTION_DRAFT_REQUIRED|DRAFT/);

  const sealed = sealedProjectionInput();
  const result = validateProjectionComplete({ ...sealed.input, projection: sealed.projection });
  assert.ok(result, "complete projection with a matching seal must be accepted");
  assert.equal(assertProjectionDigest({ ...sealed.input, projection: sealed.projection }, sealed.digest), true);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: sealed.projection, memberships: [] }), /PROJECTION_INCOMPLETE|MEMBERSHIP|COMPLETE/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: sealed.projection, facts: [] }), /PROJECTION_INCOMPLETE|FACT|COMPLETE/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: (({ seal_receipt: _ignored, ...withoutSeal }) => withoutSeal)(sealed.projection) }), /PROJECTION_SEAL_REQUIRED|SEAL|INCOMPLETE|PROJECTION/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: { ...sealed.projection, status: "draft" } }), /PROJECTION_STATUS_APPROVED_REQUIRED/);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: { ...sealed.projection, seal_receipt: { ...sealed.projection.seal_receipt, canonical_projection_digest: "sha256:" + "0".repeat(64) } } }), /PROJECTION_SEAL_REQUIRED/);
  const tamperedDigest = "sha256:" + "0".repeat(64);
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: {
    ...sealed.projection,
    projection_digest: tamperedDigest,
    seal_receipt: { ...sealed.projection.seal_receipt, canonical_projection_digest: tamperedDigest },
  } }), /PROJECTION_DIGEST_MISMATCH|DIGEST/);
});

test("internal-only assertions neither enter nor perturb the public projection, but an internal fact is refused", () => {
  const sealed = sealedProjectionInput();
  const internalAssertion = {
    ...assertion,
    id: "internal-assertion",
    field_key: "internal_note",
    value: "broker-only note",
    data_classification: "internal",
  };
  const withUnreferencedInternal = {
    ...sealed.input,
    projection: sealed.projection,
    assertions: [...completeAssertions, internalAssertion], evidence: [evidence], rights: [rights],
  };
  assert.ok(validateProjectionComplete(withUnreferencedInternal), "unreferenced internal material must not interfere with the public read model");
  const internalFact = { ...fact, field_assertion_id: internalAssertion.id, display_field_key: "internal_note" };
  assert.throws(() => validateProjectionComplete({ ...sealed.input, projection: sealed.projection, facts: [internalFact], assertions: [internalAssertion] }), /PUBLIC_FIELD|PUBLIC_ASSERTION|INTERNAL|PROJECTION/);
});
