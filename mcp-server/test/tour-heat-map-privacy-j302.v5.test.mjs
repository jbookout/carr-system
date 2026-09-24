// V5-J302 aggregate heat-map privacy admission — behavioral suite.
//
// Organized by the slice's checkable_done clauses (AC1..AC7), then the
// re-identification attempts Q048.D1 names, then the seams to S01, F01 and the
// map contract. Every fixture is synthetic: no client, practice, patient,
// property or address appears anywhere in this file.

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import * as J302 from "../src/tour-heat-map-privacy-j302.v5.js";
import {
  V5J302PrivacyError,
  admitAggregateHeatMapArtifact,
  aggregateDescriptorDigest,
  datasetRecipientEnvironmentDigest,
  emptyPrivacyBudgetLedger,
  evaluateAggregateOperation,
  evaluateDerivedStrategyProposal,
  evaluatePrivacyRouteConformance,
  v5J302PolicyDigest,
  v5J302PolicyPreimage,
  zip3PopulationTableDigest,
  V5_J302_HHS_IDENTIFIER_CATEGORIES,
  V5_J302_HHS_RESTRICTED_ZIP3,
  V5_J302_ORACLE_REF,
  V5_J302_ORACLE_VERSION,
  V5_J302_PRODUCER_ROLE,
  V5_J302_RECEIPT_RETRIEVAL_SEAM,
  V5_J302_REIDENTIFYING_OPERATIONS,
  V5_J302_ROUTE_RECEIPT_STEP,
  V5_J302_SETTLED_DECISIONS,
} from "../src/tour-heat-map-privacy-j302.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_SETTLED_DECISIONS } from "../src/global-boundaries.v5.js";
import { V5_F01_SETTLED_DECISIONS } from "../src/record-source-authority.v5.js";

const D = label => `sha256:${createHash("sha256").update(`j302-synthetic:${label}`).digest("hex")}`;
const NOW = "2026-09-24T12:00:00Z";
const T = {
  observed: "2026-09-01T00:00:00Z",
  issued: "2026-09-10T00:00:00Z",
  expires: "2027-03-01T00:00:00Z",
  retain: "2027-01-01T00:00:00Z",
  past: "2026-09-20T00:00:00Z",
  future: "2026-10-01T00:00:00Z",
};
const CONTEXT = Object.freeze({
  recipient_id: "carr-synthetic-recipient",
  environment: "staging",
  requesting_actor: "synthetic-requester",
  processors: ["synthetic-processor-a"],
});
const CONTENT = D("artifact-bytes");
const DATASET = D("dataset");

function corporate(overrides = {}) {
  return {
    source_system: "synthetic-corporate-source", source_class: "heat_map_report",
    source_account: "acct-synthetic-1",
    native_identity: { source_system: "synthetic-corporate-source", native_id: "hm-0001",
      native_id_epoch: "epoch-1" },
    native_version: "rev-1",
    content_digest: CONTENT, byte_length: 4096,
    observed_at: T.observed,
    provenance: { adapter_kind: "manual_upload", evidence_ref: "synthetic-evidence-1",
      retrieval_class: "attended_upload" },
    evidence_class: "corporate_report_render",
    taint_class: "corporate_source_of_record",
    declared_data_classes: ["aggregate_patient_location_heatmap"],
    ...overrides,
  };
}

// Synthetic ZIP3 prefixes chosen outside the HHS restricted list.
function zip3Aggregate(overrides = {}) {
  return {
    record_grain: "aggregate_cell",
    reversibility: "irreversible_aggregate",
    geography_unit: "zip3",
    temporal_precision: "year",
    columns: ["unit_id", "period", "patient_count", "suppressed"],
    cells: [
      { unit_id: "480", period: "2025", patient_count: 140, suppressed: false },
      { unit_id: "481", period: "2025", patient_count: 88, suppressed: false },
      { unit_id: "000", period: "2025", patient_count: 21, suppressed: false },
    ],
    published_total: null,
    source_privacy_threshold: { minimum_cell_count: 11, declared_by: "synthetic-source-privacy-office" },
    ...overrides,
  };
}

function countyAggregate(overrides = {}) {
  return zip3Aggregate({
    geography_unit: "county",
    temporal_precision: "quarter",
    cells: [
      { unit_id: "99001", period: "2025-Q1", patient_count: 40, suppressed: false },
      { unit_id: "99003", period: "2025-Q1", patient_count: 25, suppressed: false },
      { unit_id: "99005", period: "2025-Q1", patient_count: null, suppressed: true },
      { unit_id: "99007", period: "2025-Q1", patient_count: null, suppressed: true },
    ],
    published_total: 105,
    ...overrides,
  });
}

function artifactOf(aggregate = zip3Aggregate(), corp = corporate(), dataset = DATASET) {
  return { corporate_artifact: corp, aggregate, dataset_digest: dataset };
}

const POPULATION = Object.freeze({ "480": 250000, "481": 48000, "482": 20000 });

function commonReceipt(aggregate, overrides = {}) {
  return {
    receipt_id: "route-receipt-synthetic-1",
    producer_role: V5_J302_PRODUCER_ROLE,
    oracle_ref: V5_J302_ORACLE_REF,
    oracle_version: V5_J302_ORACLE_VERSION,
    issuer: { identity: "synthetic-independent-oracle", kind: "organization" },
    issued_at: T.issued,
    expires_at: T.expires,
    artifact_content_digest: CONTENT,
    aggregate_descriptor_digest: aggregateDescriptorDigest(aggregate),
    dataset_recipient_environment_digest: datasetRecipientEnvironmentDigest({
      dataset_digest: DATASET, recipient_id: CONTEXT.recipient_id, environment: CONTEXT.environment,
    }),
    source_privacy_threshold_acknowledged: aggregate.source_privacy_threshold.minimum_cell_count,
    ...overrides,
  };
}

function safeHarbor(aggregate = zip3Aggregate(), overrides = {}, population = POPULATION) {
  return {
    route: "safe_harbor",
    ...commonReceipt(aggregate),
    hhs_rule_ref: "45 CFR 164.514(b)(2)",
    identifier_categories_removed: [...V5_J302_HHS_IDENTIFIER_CATEGORIES],
    census_population: {
      source_ref: "synthetic-census-table", vintage: "synthetic-vintage",
      table_digest: zip3PopulationTableDigest(population), zip3_population: { ...population },
    },
    no_actual_knowledge_attestation: true,
    ...overrides,
  };
}

function expert(aggregate = countyAggregate(), overrides = {}) {
  return {
    route: "expert_determination",
    ...commonReceipt(aggregate),
    expert: { identity: "synthetic-expert-1", qualifications: ["synthetic statistical qualification"],
      independent_of_recipient: true },
    method: { method_ref: "synthetic-method-1", method_digest: D("method"), results_digest: D("results") },
    small_cell: { minimum_cell_count: 11, complementary_suppression_required: true },
    precision: { finest_spatial_unit: "county", finest_temporal_precision: "quarter" },
    budgets: { query: 5, differencing: 1, export: 1 },
    processor_terms: [{ processor_id: "synthetic-processor-a", terms_digest: D("terms-a") }],
    retention: { retain_until: T.retain, deletion_rule_ref: "synthetic-deletion-rule" },
    ...overrides,
  };
}

function conform({ artifact = artifactOf(), receipts = [safeHarbor()], context = CONTEXT, now = NOW,
  prior_artifact } = {}) {
  const request = { tenant: ORGANIZATION_TENANT_ID, artifact, context, route_receipts: receipts, now };
  if (prior_artifact !== undefined) request.prior_artifact = prior_artifact;
  return evaluatePrivacyRouteConformance(request);
}

function conformExpert(aggregate = countyAggregate(), overrides = {}, context = CONTEXT) {
  return conform({ artifact: artifactOf(aggregate), receipts: [expert(aggregate, overrides)], context });
}

function throwsCode(fn, code) {
  assert.throws(fn, err => err instanceof V5J302PrivacyError && err.code === code, `expected ${code}`);
}

function refuses(result, reason) {
  assert.equal(result.decision, "refuse", `expected refusal ${reason}, got ${result.decision}/${result.reason_id}`);
  assert.equal(result.reason_id, reason);
  assert.equal(result.admission, "unavailable");
}

// ===========================================================================
// AC1 — exactly one current independent Safe Harbor or Expert Determination
// route passes.
// ===========================================================================

test("AC1: one current independent Safe Harbor route conforms", () => {
  const r = conform();
  assert.equal(r.decision, "conforms");
  assert.equal(r.route, "safe_harbor");
  assert.equal(r.reason_id, "route_conforms_pending_independent_issuance");
  assert.equal(r.admission, "unavailable");
  assert.equal(r.independent_issuance, "not_established_here");
  assert.equal(r.route_receipt_step, V5_J302_ROUTE_RECEIPT_STEP);
});

test("AC1: one current independent Expert Determination route conforms", () => {
  const r = conformExpert();
  assert.equal(r.decision, "conforms");
  assert.equal(r.route, "expert_determination");
  assert.deepEqual(r.budgets, { differencing: 1, export: 1, query: 5 });
  assert.equal(r.retain_until, "2027-01-01T00:00:00.000Z");
});

test("AC1: zero routes, two routes, or one of each refuse", () => {
  refuses(conform({ receipts: [] }), "exactly_one_privacy_route_required");
  refuses(conform({ receipts: [safeHarbor(), safeHarbor()] }), "exactly_one_privacy_route_required");
  const agg = zip3Aggregate();
  const mixed = conform({ receipts: [safeHarbor(agg), expert(agg, {
    precision: { finest_spatial_unit: "zip3", finest_temporal_precision: "year" } })] });
  refuses(mixed, "exactly_one_privacy_route_required");
  assert.equal(mixed.routes_presented, 2);
});

test("AC1: a route that is not current refuses", () => {
  refuses(conform({ receipts: [safeHarbor(undefined, { expires_at: T.past })] }), "route_expired");
  refuses(conform({ receipts: [safeHarbor(undefined, { issued_at: T.future, expires_at: T.expires })] }),
    "route_issued_after_now");
  refuses(conform({ receipts: [safeHarbor(undefined, { issued_at: T.issued, expires_at: T.issued })] }),
    "route_window_empty");
});

test("AC1: independence as stated — model issuer, self-issued, recipient-issued, wrong oracle all refuse", () => {
  refuses(conform({ receipts: [safeHarbor(undefined, { issuer: { identity: "some-model", kind: "model" } })] }),
    "model_cannot_issue_privacy_route");
  refuses(conform({ receipts: [safeHarbor(undefined,
    { issuer: { identity: CONTEXT.requesting_actor, kind: "human" } })] }), "route_not_independent_of_requester");
  refuses(conform({ receipts: [safeHarbor(undefined,
    { issuer: { identity: CONTEXT.recipient_id, kind: "organization" } })] }), "route_not_independent_of_requester");
  for (const field of [{ producer_role: "self_attested" }, { oracle_ref: "oracle:other" },
    { oracle_version: "0.9.0" }]) {
    refuses(conform({ receipts: [safeHarbor(undefined, field)] }), "route_producer_not_independent_oracle");
  }
});

// ===========================================================================
// AC2 — Safe Harbor ZIP/population rule and geography/identifier negatives.
// ===========================================================================

test("AC2: a ZIP3 survives only above 20,000 people; at 20,000 it must be 000", () => {
  const agg = zip3Aggregate({ cells: [
    { unit_id: "482", period: "2025", patient_count: 30, suppressed: false }] });
  const r = conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] });
  refuses(r, "zip3_population_not_above_floor");
  assert.equal(J302.V5_J302_SAFE_HARBOR_ZIP3_POPULATION_FLOOR, 20000);

  const above = { ...POPULATION, "482": 20001 };
  const ok = conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg, {}, above)] });
  assert.equal(ok.decision, "conforms");
});

test("AC2: a ZIP3 with no population in the receipt's table is denied, not assumed", () => {
  const agg = zip3Aggregate({ cells: [
    { unit_id: "483", period: "2025", patient_count: 30, suppressed: false }] });
  refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }), "zip3_population_unknown_denied");
});

test("AC2: an HHS-restricted ZIP3 refuses even when the receipt claims a large population", () => {
  for (const zip3 of V5_J302_HHS_RESTRICTED_ZIP3) {
    const agg = zip3Aggregate({ cells: [{ unit_id: zip3, period: "2025", patient_count: 30, suppressed: false }] });
    const inflated = { ...POPULATION, [zip3]: 9_999_999 };
    refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg, {}, inflated)] }),
      "restricted_zip3_must_be_000");
  }
});

test("AC2: a population table edited under its digest refuses", () => {
  const agg = zip3Aggregate({ cells: [{ unit_id: "482", period: "2025", patient_count: 30, suppressed: false }] });
  const receipt = safeHarbor(agg);
  receipt.census_population.zip3_population["482"] = 900000; // digest still names 20,000
  refuses(conform({ artifact: artifactOf(agg), receipts: [receipt] }), "census_population_table_digest_mismatch");
});

test("AC2: geography smaller than a state (other than a qualifying ZIP3) refuses under Safe Harbor", () => {
  const agg = countyAggregate({ temporal_precision: "year", cells: [
    { unit_id: "99001", period: "2025", patient_count: 40, suppressed: false }], published_total: null });
  refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }),
    "safe_harbor_geography_smaller_than_permitted");
});

test("AC2: a date element finer than a year refuses under Safe Harbor", () => {
  const agg = zip3Aggregate({ temporal_precision: "month", cells: [
    { unit_id: "480", period: "2025-03", patient_count: 40, suppressed: false }] });
  refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }), "safe_harbor_date_element_refused");
});

test("AC2: every one of the eighteen identifier categories must be stated removed", () => {
  for (const category of V5_J302_HHS_IDENTIFIER_CATEGORIES) {
    const r = conform({ receipts: [safeHarbor(undefined, {
      identifier_categories_removed: V5_J302_HHS_IDENTIFIER_CATEGORIES.filter(c => c !== category) })] });
    refuses(r, "safe_harbor_identifier_categories_incomplete");
    assert.deepEqual(r.missing_categories, [category]);
  }
  assert.equal(V5_J302_HHS_IDENTIFIER_CATEGORIES.length, 18);
});

test("AC2: identifier and unknown columns refuse by name", () => {
  for (const column of ["latitude", "patient_name", "zip5", "mrn", "dob"]) {
    const agg = zip3Aggregate({ columns: ["unit_id", "patient_count", "suppressed", column] });
    const r = conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] });
    refuses(r, "identifier_column_refused");
    assert.deepEqual(r.columns, [column]);
  }
  const agg = zip3Aggregate({ columns: ["unit_id", "patient_count", "suppressed", "payer_mix"] });
  refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }), "unknown_column_denied");
});

test("AC2: the Safe Harbor rule reference and the actual-knowledge disclaimer are both required", () => {
  refuses(conform({ receipts: [safeHarbor(undefined, { hhs_rule_ref: "45 CFR 164.514(b)(1)" })] }),
    "safe_harbor_rule_ref_mismatch");
  refuses(conform({ receipts: [safeHarbor(undefined, { no_actual_knowledge_attestation: false })] }),
    "safe_harbor_actual_knowledge_not_disclaimed");
});

// ===========================================================================
// AC3 — Expert Determination fields all bind; mismatches refuse.
// ===========================================================================

test("AC3: expert identity must be independent of requester and recipient", () => {
  refuses(conformExpert(undefined, { expert: { identity: CONTEXT.requesting_actor,
    qualifications: ["q"], independent_of_recipient: true } }), "expert_not_independent_of_requester");
  refuses(conformExpert(undefined, { expert: { identity: "synthetic-expert-1",
    qualifications: ["q"], independent_of_recipient: false } }), "expert_independence_not_stated");
});

test("AC3: identity, qualifications, method and results are required fields", () => {
  throwsCode(() => conformExpert(undefined, { expert: { identity: "synthetic-expert-1",
    qualifications: [], independent_of_recipient: true } }), "invalid_shape");
  throwsCode(() => conformExpert(undefined, { method: { method_ref: "m", method_digest: D("m") } }),
    "missing_field");
  throwsCode(() => conformExpert(undefined, { method: { method_ref: "m", method_digest: "not-a-digest",
    results_digest: D("r") } }), "invalid_digest");
});

test("AC3: numeric small-cell threshold binds, and the stricter of expert and source governs", () => {
  const agg = countyAggregate({ cells: [
    { unit_id: "99001", period: "2025-Q1", patient_count: 15, suppressed: false }], published_total: null });
  refuses(conformExpert(agg, { small_cell: { minimum_cell_count: 20, complementary_suppression_required: true } }),
    "small_cell_below_determination_threshold");
  // The expert would accept a cell of 5; the source said 11 (Q048.D1).
  const small = countyAggregate({ cells: [
    { unit_id: "99001", period: "2025-Q1", patient_count: 8, suppressed: false }], published_total: null });
  const r = conformExpert(small, { small_cell: { minimum_cell_count: 5, complementary_suppression_required: true } });
  refuses(r, "small_cell_below_determination_threshold");
  assert.equal(r.minimum_cell_count, 11);
});

test("AC3: complementary suppression binds — a residual below the floor refuses", () => {
  const agg = countyAggregate({ published_total: 70 }); // two suppressed cells share a residual of 5
  refuses(conformExpert(agg), "complementary_suppression_residual_below_threshold");
});

test("AC3: spatial and temporal precision bind", () => {
  const tract = countyAggregate({ geography_unit: "census_tract", cells: [
    { unit_id: "99001020100", period: "2025-Q1", patient_count: 40, suppressed: false }], published_total: null });
  refuses(conformExpert(tract), "spatial_precision_finer_than_determination");
  const monthly = countyAggregate({ temporal_precision: "month", cells: [
    { unit_id: "99001", period: "2025-01", patient_count: 40, suppressed: false }], published_total: null });
  refuses(conformExpert(monthly), "temporal_precision_finer_than_determination");
  refuses(conformExpert(undefined, { precision: { finest_spatial_unit: "hexagon", finest_temporal_precision: "quarter" } }),
    "determination_precision_unknown_denied");
});

test("AC3: processor terms bind — an uncovered processor refuses", () => {
  const r = conformExpert(undefined, {}, { ...CONTEXT, processors: ["synthetic-processor-a", "synthetic-processor-b"] });
  refuses(r, "processor_not_covered_by_terms");
  assert.deepEqual(r.processors, ["synthetic-processor-b"]);
});

test("AC3: retention and expiry bind", () => {
  refuses(conformExpert(undefined, { retention: { retain_until: T.past, deletion_rule_ref: "r" } }), "retention_elapsed");
  refuses(conformExpert(undefined, { retention: { retain_until: "2027-06-01T00:00:00Z", deletion_rule_ref: "r" } }),
    "retention_outlives_determination");
  refuses(conformExpert(undefined, { expires_at: T.past }), "route_expired");
});

test("AC3: repeated-query, differencing and export budgets bind and exhaust", () => {
  const receipt = expert();
  let ledger = emptyPrivacyBudgetLedger(receipt.receipt_id);
  const op = kind => evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: receipt,
    ledger, operation: { kind, artifact_digest: D("artifact") }, now: NOW });
  for (let i = 0; i < 5; i++) {
    const r = op("view_native_precision");
    assert.equal(r.decision, "within_budget");
    assert.equal(r.compare_and_swap.expected_ledger_version, ledger.ledger_version);
    assert.equal(r.ledger_written, false);
    ledger = r.next_ledger;
  }
  refuses(op("rank_units"), "privacy_budget_exhausted");
  const diff = op("difference_between_artifacts");
  assert.equal(diff.decision, "within_budget");
  ledger = diff.next_ledger;
  refuses(op("difference_between_artifacts"), "privacy_budget_exhausted");
  ledger = op("export_sealed_artifact").next_ledger;
  refuses(op("export_sealed_artifact"), "privacy_budget_exhausted");
  assert.deepEqual(ledger.used, { differencing: 1, export: 1, query: 5 });
});

test("AC3: budget consumption is compare-and-swap — two callers on one ledger race for one version", () => {
  const receipt = expert(undefined, { budgets: { query: 1, differencing: 0, export: 0 } });
  const ledger = emptyPrivacyBudgetLedger(receipt.receipt_id);
  const call = () => evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: receipt,
    ledger, operation: { kind: "view_native_precision", artifact_digest: D("artifact") }, now: NOW });
  const a = call(), b = call();
  // Both name the SAME precondition, so the store can apply only one of them.
  assert.equal(a.compare_and_swap.expected_ledger_version, 0);
  assert.deepEqual(a.compare_and_swap, b.compare_and_swap);
  // After the winner lands, the loser's retry sees the spent unit.
  refuses(evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: receipt,
    ledger: a.next_ledger, operation: { kind: "view_native_precision", artifact_digest: D("artifact") }, now: NOW }),
  "privacy_budget_exhausted");
});

test("AC3: a ledger for another receipt, or none at all, refuses", () => {
  const receipt = expert();
  refuses(evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: receipt,
    ledger: emptyPrivacyBudgetLedger("some-other-receipt"),
    operation: { kind: "view_native_precision", artifact_digest: D("artifact") }, now: NOW }),
  "budget_ledger_receipt_mismatch");
  refuses(evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: receipt,
    operation: { kind: "view_native_precision", artifact_digest: D("artifact") }, now: NOW }),
  "budget_ledger_required");
});

// ===========================================================================
// AC4 — raw, reversible, expired and mismatched routes refuse.
// ===========================================================================

test("AC4: raw rows and reversible sources refuse by name", () => {
  for (const grain of ["patient_row", "encounter_row"]) {
    refuses(conform({ artifact: artifactOf(zip3Aggregate({ record_grain: grain })) }), "raw_rows_refused");
  }
  for (const rev of ["pseudonymized_rows", "hashed_identifiers", "encrypted_rows"]) {
    refuses(conform({ artifact: artifactOf(zip3Aggregate({ reversibility: rev })) }), "reversible_source_refused");
  }
  refuses(conform({ artifact: artifactOf(zip3Aggregate({ record_grain: "mystery" })) }), "unknown_record_grain_denied");
});

test("AC4: points, addresses and geocodes refuse; an unregistered geography is denied", () => {
  for (const unit of J302.V5_J302_RAW_LOCATION_UNITS) {
    refuses(conform({ artifact: artifactOf(zip3Aggregate({ geography_unit: unit })) }), "raw_location_refused");
  }
  refuses(conform({ artifact: artifactOf(zip3Aggregate({ geography_unit: "h3_res9" })) }),
    "unknown_geography_unit_denied");
});

test("AC4: a cell finer than the declared geography or period refuses", () => {
  const zip5InZip3 = zip3Aggregate({ cells: [{ unit_id: "99998", period: "2025", patient_count: 40, suppressed: false }] });
  refuses(conform({ artifact: artifactOf(zip5InZip3), receipts: [safeHarbor(zip5InZip3)] }),
    "cell_finer_than_declared_geography");
  const dayInYear = zip3Aggregate({ cells: [{ unit_id: "480", period: "2025-03-04", patient_count: 40, suppressed: false }] });
  refuses(conform({ artifact: artifactOf(dayInYear), receipts: [safeHarbor(dayInYear)] }),
    "cell_period_finer_than_declared");
});

test("AC4: a receipt for other bytes or another descriptor refuses", () => {
  refuses(conform({ receipts: [safeHarbor(undefined, { artifact_content_digest: D("other-bytes") })] }),
    "route_artifact_digest_mismatch");
  // The caller edits one cell after the determination was made.
  const agg = zip3Aggregate();
  const receipt = safeHarbor(agg);
  const edited = zip3Aggregate({ cells: [
    { unit_id: "480", period: "2025", patient_count: 141, suppressed: false },
    ...agg.cells.slice(1)] });
  refuses(conform({ artifact: artifactOf(edited), receipts: [receipt] }), "route_descriptor_digest_mismatch");
});

test("AC4: the source privacy threshold must be acknowledged by the route exactly", () => {
  refuses(conform({ receipts: [safeHarbor(undefined, { source_privacy_threshold_acknowledged: 5 })] }),
    "source_privacy_threshold_not_preserved");
});

// ===========================================================================
// AC5 — the artifact digest and the independent determination bind dataset,
// recipient and environment.
// ===========================================================================

test("AC5: changing the environment, recipient or dataset breaks the binding", () => {
  refuses(conform({ context: { ...CONTEXT, environment: "production" } }),
    "route_dataset_recipient_environment_mismatch");
  refuses(conform({ context: { ...CONTEXT, recipient_id: "carr-synthetic-other" } }),
    "route_dataset_recipient_environment_mismatch");
  refuses(conform({ artifact: artifactOf(zip3Aggregate(), corporate(), D("other-dataset")) }),
    "route_dataset_recipient_environment_mismatch");
});

test("AC5: the artifact digest moves with bytes, descriptor and dataset, and with nothing else", () => {
  const base = conform().artifact_digest;
  assert.equal(conform().artifact_digest, base);
  assert.notEqual(conform({ artifact: artifactOf(zip3Aggregate(), corporate(), D("d2")) }).artifact_digest, base);
  const agg = zip3Aggregate({ cells: [{ unit_id: "480", period: "2025", patient_count: 140, suppressed: false }] });
  assert.notEqual(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }).artifact_digest, base);
});

// ===========================================================================
// AC6 — derived facts remain proposals until reviewed.
// ===========================================================================

function propose(proposalOverrides = {}, aggregate = zip3Aggregate()) {
  return evaluateDerivedStrategyProposal({
    tenant: ORGANIZATION_TENANT_ID,
    artifact: { aggregate, descriptor_digest: aggregateDescriptorDigest(aggregate) },
    proposal: {
      proposal_kind: "site_selection_context",
      statement: "Unit 480 carries the largest aggregate count in the artifact year.",
      cited_unit_ids: ["480"], confidence: 0.7, evidence_ref: "synthetic-evidence-2",
      proposed_by: { kind: "model", identity: "synthetic-model" },
      ...proposalOverrides,
    },
    now: NOW,
  });
}

test("AC6: a derived strategy statement is a proposal pending human review, never a fact", () => {
  const r = propose();
  assert.equal(r.decision, "proposal_pending_review");
  assert.equal(r.is_fact, false);
  assert.equal(r.applies_to_record, false);
  assert.equal(r.requires_human_review, true);
  assert.equal(r.admission, "unavailable");
  assert.match(r.proposal_digest, /^sha256:/);
});

test("AC6: a model cannot approve a route, set a threshold or promote itself to a fact", () => {
  for (const key of ["approve_route", "privacy_route", "treat_as_fact", "accept_now", "apply_to_deal",
    "threshold_override", "receipt"]) {
    const r = propose({ [key]: true });
    refuses(r, "proposal_widening_refused");
    assert.equal(r.offending_field, key);
    assert.equal(r.is_fact, false);
  }
});

test("AC6: a proposal cannot cite a suppressed or absent unit, or a different descriptor", () => {
  const agg = countyAggregate();
  refuses(propose({ cited_unit_ids: ["99005"], statement: "County unit 99005 looks underserved." }, agg),
    "proposal_cites_suppressed_cell");
  refuses(propose({ cited_unit_ids: ["999"] }), "proposal_cites_unit_outside_artifact");
  const r = evaluateDerivedStrategyProposal({ tenant: ORGANIZATION_TENANT_ID,
    artifact: { aggregate: zip3Aggregate(), descriptor_digest: D("other") },
    proposal: { proposal_kind: "market_gap_context", statement: "x", cited_unit_ids: ["480"],
      confidence: 0.5, evidence_ref: "e", proposed_by: { kind: "human", identity: "h" } }, now: NOW });
  refuses(r, "proposal_artifact_digest_mismatch");
});

// ===========================================================================
// AC7 — intake refuses until the independently issued receipt passes; building
// the verifier never satisfies it.
// ===========================================================================

test("AC7: a fully conforming artifact is still refused at intake, naming the owed retrieval seam", () => {
  assert.equal(conform().decision, "conforms"); // the verifier would carry it...
  const r = admitAggregateHeatMapArtifact({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(),
    context: CONTEXT, now: NOW });
  refuses(r, "independent_privacy_route_receipt_unavailable"); // ...intake still refuses.
  assert.equal(r.owed_seam, V5_J302_RECEIPT_RETRIEVAL_SEAM);
  assert.equal(r.required_gate, "journey-three-aggregate-heat-map-route-accepted");
  assert.ok(r.required_runtime_evidence.includes(V5_J302_ROUTE_RECEIPT_STEP));
});

test("AC7: intake has no field a caller could put a receipt in", () => {
  for (const key of ["route_receipts", "route_receipt", "receipt", "gate_receipt", "approved"]) {
    throwsCode(() => admitAggregateHeatMapArtifact({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(),
      context: CONTEXT, now: NOW, [key]: [safeHarbor()] }), "unknown_field");
  }
});

test("AC7: intake still answers artifact-level refusals in full", () => {
  refuses(admitAggregateHeatMapArtifact({ tenant: ORGANIZATION_TENANT_ID,
    artifact: artifactOf(zip3Aggregate({ record_grain: "patient_row" })), context: CONTEXT, now: NOW }),
  "raw_rows_refused");
});

test("AC7: no export returns an admission, for any shape a caller can build", () => {
  const shapes = [
    undefined, null, {}, [], "", 0,
    { tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(), context: CONTEXT, route_receipts: [safeHarbor()], now: NOW },
    { tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(), context: CONTEXT, now: NOW },
    { tenant: ORGANIZATION_TENANT_ID, route_receipt: expert(), ledger: emptyPrivacyBudgetLedger("route-receipt-synthetic-1"),
      operation: { kind: "export_sealed_artifact", artifact_digest: D("a") }, now: NOW },
  ];
  let calls = 0;
  for (const [name, fn] of Object.entries(J302)) {
    if (typeof fn !== "function" || /^[A-Z]/.test(name)) continue;
    for (const shape of shapes) {
      let result;
      try { result = fn(shape); calls++; } catch { continue; }
      const text = JSON.stringify(result ?? null);
      assert.doesNotMatch(text, /"admission":"(?!unavailable)/, `${name} returned an admission`);
      assert.doesNotMatch(text, /"(admitted|accepted|approved|is_fact)":true/, `${name} claimed a privileged outcome`);
    }
  }
  assert.ok(calls > 5);
});

// ===========================================================================
// Re-identification attempts (Q048.D1): each must be refused.
// ===========================================================================

test("REID: every named re-identifying operation refuses under both routes", () => {
  for (const receipt of [safeHarbor(), expert()]) {
    for (const kind of V5_J302_REIDENTIFYING_OPERATIONS) {
      refuses(evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: receipt,
        ledger: emptyPrivacyBudgetLedger(receipt.receipt_id),
        operation: { kind, artifact_digest: D("a") }, now: NOW }), "reidentifying_operation_refused");
    }
  }
  for (const kind of ["reverse_geocode", "impute_suppressed_cell", "difference_overlapping_cells"]) {
    assert.ok(V5_J302_REIDENTIFYING_OPERATIONS.includes(kind));
  }
  refuses(evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: safeHarbor(),
    operation: { kind: "nearest_patient", artifact_digest: D("a") }, now: NOW }), "unknown_operation_denied");
});

test("REID: Safe Harbor binds no differencing or export budget, so both refuse rather than run unmetered", () => {
  for (const kind of ["difference_between_artifacts", "export_sealed_artifact"]) {
    refuses(evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: safeHarbor(),
      operation: { kind, artifact_digest: D("a") }, now: NOW }), "operation_budget_not_bound_by_route");
  }
  const view = evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, route_receipt: safeHarbor(),
    operation: { kind: "overlay_properties_at_native_precision", artifact_digest: D("a") }, now: NOW });
  assert.equal(view.decision, "within_route");
});

test("REID: one suppressed cell beside a published total is subtractable, and refuses", () => {
  const agg = countyAggregate({ cells: [
    { unit_id: "99001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "99003", period: "2025-Q1", patient_count: null, suppressed: true }], published_total: 47 });
  refuses(conform({ artifact: artifactOf(agg), receipts: [expert(agg)] }), "complementary_suppression_missing");
});

test("REID: a published total that does not add up refuses", () => {
  const agg = zip3Aggregate({ published_total: 1 });
  refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }), "published_total_inconsistent");
});

test("REID: a small cell under the source threshold refuses under Safe Harbor", () => {
  const agg = zip3Aggregate({ cells: [{ unit_id: "480", period: "2025", patient_count: 3, suppressed: false }] });
  const r = conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] });
  refuses(r, "source_privacy_threshold_breached");
  assert.equal(r.minimum_cell_count, 11);
});

test("REID: a suppressed cell cannot carry its value alongside the flag", () => {
  throwsCode(() => conform({ artifact: artifactOf(zip3Aggregate({ cells: [
    { unit_id: "480", period: "2025", patient_count: 4, suppressed: true }] })) }), "suppressed_cell_carries_count");
});

test("REID: a proposal that writes a coordinate or a five-digit ZIP is finer than any aggregate", () => {
  refuses(propose({ statement: "Cluster centered near 12.3456, -45.6789 is strongest." }), "proposal_finer_than_aggregate");
  refuses(propose({ statement: "Most demand sits in 99998." }), "proposal_finer_than_aggregate");
});

// ===========================================================================
// Seams: S01, F01, immutability and the map contract.
// ===========================================================================

test("SEAM: S01 refuses PHI before this module reads a cell; permitted data is not a heat map", () => {
  refuses(conform({ artifact: artifactOf(zip3Aggregate(), corporate({ declared_data_classes: ["phi"] })) }),
    "phi_or_raw_patient_location_refused");
  refuses(conform({ artifact: artifactOf(zip3Aggregate(), corporate({ declared_data_classes: ["market_comp"] })) }),
    "artifact_not_routed_to_heat_map_privacy");
  refuses(conform({ artifact: artifactOf(zip3Aggregate(), corporate({
    declared_data_classes: ["aggregate_patient_location_heatmap", "lease_economics"] })) }),
  "heat_map_artifact_mixed_with_other_classes");
});

test("SEAM: F01 identity is enforced — an unreadable corporate identity throws F01's own error", () => {
  assert.throws(() => conform({ artifact: artifactOf(zip3Aggregate(), corporate({ content_digest: "bad" })) }),
    err => err.code === "invalid_digest");
});

test("SEAM: immutable provenance — the same identity cannot describe other bytes; future observations refuse", () => {
  const prior = corporate({ content_digest: D("earlier-bytes") });
  refuses(conform({ prior_artifact: prior }), "artifact_identity_conflict");
  assert.equal(conform({ prior_artifact: corporate({ native_version: "rev-0", content_digest: D("earlier") }) }).decision,
    "conforms");
  refuses(conform({ artifact: artifactOf(zip3Aggregate(), corporate({ observed_at: T.future })) }),
    "observation_after_now");
});

test("SEAM: foreign tenant and unknown request fields are contract violations", () => {
  throwsCode(() => evaluatePrivacyRouteConformance({ tenant: "other", artifact: artifactOf(), context: CONTEXT,
    route_receipts: [], now: NOW }), "foreign_tenant");
  throwsCode(() => conform({ context: { ...CONTEXT, actor_verified: true } }), "unknown_field");
  throwsCode(() => conform({ now: "2026-02-31T00:00:00Z" }), "invalid_timestamp");
});

test("SEAM: settled decisions are the reviewed text, sharing evidence with S01 and F01", () => {
  assert.deepEqual(Object.keys(V5_J302_SETTLED_DECISIONS).sort(), ["Q033.D2", "Q048.D1", "Q071.D2"]);
  assert.equal(V5_J302_SETTLED_DECISIONS["Q033.D2"].source_evidence_digest,
    V5_SETTLED_DECISIONS["Q033.D1"].source_evidence_digest);
  assert.equal(V5_J302_SETTLED_DECISIONS["Q071.D2"].source_evidence_digest,
    V5_F01_SETTLED_DECISIONS["Q071.D1"].source_evidence_digest);
});

test("SEAM: the policy binds the map contract, owns no platform small-cell floor, and hashes stably", () => {
  const p = v5J302PolicyPreimage();
  assert.deepEqual(p.map_contract, { id: "carr-map-tour-v1", version: "1.2.0",
    gate: "tour-map-contract-1.2.0-accepted" });
  assert.equal(p.platform_small_cell_floor, null);
  assert.equal(v5J302PolicyDigest(), v5J302PolicyDigest());
  assert.match(v5J302PolicyDigest(), /^sha256:[0-9a-f]{64}$/);
});

test("SEAM: fixtures carry no real-looking personal identifiers", () => {
  const text = JSON.stringify([artifactOf(), safeHarbor(), expert(), CONTEXT]);
  assert.doesNotMatch(text, /\b\d{3}-\d{2}-\d{4}\b/); // no SSN shapes
  assert.doesNotMatch(text, /@[a-z]+\.[a-z]+/i);      // no e-mail addresses
});
