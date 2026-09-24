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
  bindHeatMapPrivacyKernel,
  evaluateHeatMapAudienceProjection,
  readHeatMapPrivacyConfig,
  datasetRecipientEnvironmentDigest,
  emptyPrivacyBudgetLedger,
  privacyBudgetLedgerKey,
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
import { V5_J302_PRIVACY_CONFIG } from "../src/tour-heat-map-privacy-config-j302.v5.js";
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
      { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
      { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false },
      { unit_id: "56005", period: "2025-Q1", patient_count: null, suppressed: true },
      { unit_id: "56007", period: "2025-Q1", patient_count: null, suppressed: true },
    ],
    published_total: 105,
    ...overrides,
  });
}

function artifactOf(aggregate = zip3Aggregate(), corp = corporate(), dataset = DATASET) {
  return { corporate_artifact: corp, aggregate, dataset_digest: dataset };
}

const POPULATION = Object.freeze({ "480": 250000, "481": 48000, "482": 20000 });

// The shipped config leaves the 2020 Census ZIP3 slot unknown, so Safe Harbor
// ZIP3 cells deny through the default exports. Positive Safe Harbor paths run
// through the SAME code bound to a config that pins a synthetic 2020 table.
const COUNTY_CODES = Object.freeze(["56001", "56003", "56005", "56007", "56009"]);
function pinnedKernel(population = POPULATION, overrides = {}) {
  return bindHeatMapPrivacyKernel({
    ...V5_J302_PRIVACY_CONFIG,
    census_2020_zip3_population: { vintage: "2020", status: "pinned",
      table_digest: zip3PopulationTableDigest(population) },
    county_fips_codes: { status: "pinned", codes: [...COUNTY_CODES] },
    ...overrides,
  });
}
const PINNED = pinnedKernel();

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
      source_ref: "synthetic-census-table", vintage: "2020",
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
  prior_artifact, kernel = PINNED } = {}) {
  const request = { tenant: ORGANIZATION_TENANT_ID, artifact, context, route_receipts: receipts, now };
  if (prior_artifact !== undefined) request.prior_artifact = prior_artifact;
  return kernel.evaluatePrivacyRouteConformance(request);
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
  const ok = conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg, {}, above)],
    kernel: pinnedKernel(above) });
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
    refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg, {}, inflated)],
      kernel: pinnedKernel(inflated) }),
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
    { unit_id: "56001", period: "2025", patient_count: 40, suppressed: false }], published_total: null });
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
    { unit_id: "56001", period: "2025-Q1", patient_count: 15, suppressed: false }], published_total: null });
  refuses(conformExpert(agg, { small_cell: { minimum_cell_count: 20, complementary_suppression_required: true } }),
    "small_cell_below_determination_threshold");
  // The expert would accept a cell of 5; the source said 11 (Q048.D1).
  const small = countyAggregate({ cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 8, suppressed: false }], published_total: null });
  const r = conformExpert(small, { small_cell: { minimum_cell_count: 5, complementary_suppression_required: true } });
  refuses(r, "small_cell_below_determination_threshold");
  assert.equal(r.minimum_cell_count, 11);
});

test("AC3: complementary suppression binds — a residual below the floor refuses", () => {
  const agg = countyAggregate({ published_total: 70 }); // two suppressed cells share a residual of 5
  refuses(conformExpert(agg), "complementary_suppression_residual_below_floor");
});

test("AC3: spatial and temporal precision bind", () => {
  const tract = countyAggregate({ geography_unit: "census_tract", cells: [
    { unit_id: "56001000100", period: "2025-Q1", patient_count: 40, suppressed: false }], published_total: null });
  refuses(conformExpert(tract), "spatial_precision_finer_than_determination");
  const monthly = countyAggregate({ temporal_precision: "month", cells: [
    { unit_id: "56001", period: "2025-01", patient_count: 40, suppressed: false }], published_total: null });
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

// Operations take the artifact, context and receipts and RE-RUN conformance.
// A prior release as the release-history store hands it over: an already-
// released fact to this recipient. Tests pass {artifact, route_receipts} and
// this wraps it; an entry that already names a recipient is passed as is.
function released(entry, { recipient_id = CONTEXT.recipient_id, environment = CONTEXT.environment } = {}) {
  return { tenant: ORGANIZATION_TENANT_ID, recipient_id, environment, ...entry };
}
const asHistory = history => history.map(h => (h && typeof h === "object" && "recipient_id" in h ? h : released(h)));

// release_history defaults to an explicit empty list; `history: null` omits it.
function opRequest(kind, { aggregate = countyAggregate(), receipt, ledger, counterpart, corp, history = [] } = {}) {
  const request = { tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(aggregate, corp ?? corporate()),
    context: CONTEXT, route_receipts: [receipt ?? expert(aggregate)], operation: { kind }, recipient_class: "internal",
    now: NOW };
  if (ledger !== undefined) request.ledger = ledger;
  if (counterpart !== undefined) request.counterpart = counterpart;
  if (history !== null) request.release_history = Array.isArray(history) ? asHistory(history) : history;
  return request;
}
function op(kind, opts = {}, kernel = PINNED) {
  return kernel.evaluateAggregateOperation(opRequest(kind, opts));
}
// A differencing operation against a clean second release of the same cells
// (other bytes, identical counts): it spends one differencing unit.
function diffOp(ledger, aggregate = countyAggregate(), extra = {}) {
  const counterpart = secondArtifact(aggregate.cells, { published_total: aggregate.published_total });
  return op("difference_between_artifacts", { aggregate, ledger, counterpart, ...extra });
}
function ledgerFor(aggregate = countyAggregate()) {
  const r = conform({ artifact: artifactOf(aggregate), receipts: [expert(aggregate)] });
  assert.equal(r.decision, "conforms");
  return emptyPrivacyBudgetLedger(r.ledger_key);
}

test("AC3: repeated-query, differencing and export budgets bind and exhaust", () => {
  let ledger = ledgerFor();
  for (let i = 0; i < 5; i++) {
    const r = op("view_native_precision", { ledger });
    assert.equal(r.decision, "within_budget");
    assert.equal(r.compare_and_swap.expected_ledger_version, ledger.ledger_version);
    assert.equal(r.ledger_written, false);
    ledger = r.next_ledger;
  }
  refuses(op("rank_units", { ledger }), "privacy_budget_exhausted");
  ledger = diffOp(ledger).next_ledger;
  refuses(diffOp(ledger), "privacy_budget_exhausted");
  assert.deepEqual(ledger.used, { differencing: 1, export: 0, query: 5 });
  // Export is blocked before any budget is read (see M1).
  assert.equal(op("export_sealed_artifact", { ledger }).decision, "unavailable");
});

test("AC3: budget consumption is compare-and-swap — two callers on one ledger race for one version", () => {
  const agg = countyAggregate();
  const receipt = expert(agg, { budgets: { query: 1, differencing: 0, export: 0 } });
  const ledger = ledgerFor(agg);
  const a = op("view_native_precision", { receipt, ledger });
  const b = op("view_native_precision", { receipt, ledger });
  assert.equal(a.compare_and_swap.expected_ledger_version, 0);
  assert.deepEqual(a.compare_and_swap, b.compare_and_swap);
  assert.equal(a.compare_and_swap.ledger_key, ledger.ledger_key);
  refuses(op("view_native_precision", { receipt, ledger: a.next_ledger }), "privacy_budget_exhausted");
});

test("AC3: a ledger for another artifact, an inconsistent ledger, or none at all refuses", () => {
  refuses(op("view_native_precision", { ledger: emptyPrivacyBudgetLedger(D("another-key")) }),
    "budget_ledger_key_mismatch");
  refuses(op("view_native_precision"), "budget_ledger_required");
  // A1e: a caller-built ledger claiming nothing spent at version 999.
  const forged = { ...ledgerFor(), ledger_version: 999 };
  const r = op("view_native_precision", { ledger: forged });
  refuses(r, "budget_ledger_inconsistent");
  assert.equal(r.units_spent, 0);
  // Mutant 1194: an unknown ledger schema is a contract violation.
  throwsCode(() => op("view_native_precision", { ledger: { ...ledgerFor(), schema_version: "other" } }),
    "unknown_ledger_schema");
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

function proposalRequest(proposalOverrides = {}, aggregate = zip3Aggregate(), receipts) {
  return {
    tenant: ORGANIZATION_TENANT_ID,
    artifact: artifactOf(aggregate), context: CONTEXT,
    route_receipts: receipts ?? [aggregate.geography_unit === "zip3" ? safeHarbor(aggregate) : expert(aggregate)],
    proposal: {
      proposal_kind: "site_selection_context",
      statement: "Unit 480 carries the largest aggregate count in the artifact year.",
      cited_unit_ids: ["480"], confidence: 0.7, evidence_ref: "synthetic-evidence-2",
      proposed_by: { kind: "model", identity: "synthetic-model" },
      ...proposalOverrides,
    },
    release_history: [],
    recipient_class: "internal",
    now: NOW,
  };
}
function propose(proposalOverrides = {}, aggregate = zip3Aggregate(), receipts, kernel = PINNED) {
  return kernel.evaluateDerivedStrategyProposal(proposalRequest(proposalOverrides, aggregate, receipts));
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

test("AC6: a proposal cannot cite a suppressed or absent unit", () => {
  const agg = countyAggregate();
  refuses(propose({ cited_unit_ids: ["56005"], statement: "County unit 56005 looks underserved." }, agg),
    "proposal_cites_suppressed_cell");
  refuses(propose({ cited_unit_ids: ["999"] }), "proposal_cites_unit_outside_artifact");
});

test("F5: a proposal is derived only from an artifact that conforms — raw rows, small cells, no route all refuse", () => {
  // A5: patient rows, hashed identifiers, lat/lng and a count of 1.
  const raw = zip3Aggregate({ record_grain: "patient_row", reversibility: "hashed_identifiers", geography_unit: "lat_lng",
    cells: [{ unit_id: "480", period: "2025", patient_count: 1, suppressed: false }],
    source_privacy_threshold: { minimum_cell_count: 1, declared_by: "x" } });
  const r = propose({ statement: "Unit 480 has exactly 1 patient." }, raw);
  refuses(r, "proposal_artifact_not_conforming");
  assert.equal(r.conformance_reason_id, "raw_rows_refused");
  const tiny = zip3Aggregate({ cells: [{ unit_id: "480", period: "2025", patient_count: 2, suppressed: false }] });
  assert.equal(propose({}, tiny).conformance_reason_id, "small_cell_below_effective_floor");
  assert.equal(propose({}, zip3Aggregate(), []).conformance_reason_id, "exactly_one_privacy_route_required");
  const phi = evaluateDerivedStrategyProposal({ ...proposalRequest(),
    artifact: artifactOf(zip3Aggregate(), corporate({ declared_data_classes: ["phi"] })) });
  assert.equal(phi.conformance_reason_id, "phi_or_raw_patient_location_refused");
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
    opRequest("export_sealed_artifact", { ledger: ledgerFor() }),
    proposalRequest(),
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
    const aggregate = receipt.route === "safe_harbor" ? zip3Aggregate() : countyAggregate();
    for (const kind of V5_J302_REIDENTIFYING_OPERATIONS) {
      refuses(op(kind, { aggregate, receipt, ledger: ledgerFor() }), "reidentifying_operation_refused");
    }
  }
  refuses(op("nearest_patient", { aggregate: zip3Aggregate(), receipt: safeHarbor() }), "unknown_operation_denied");
});

test("REID: Safe Harbor binds no differencing or export budget, so both refuse rather than run unmetered", () => {
  const aggregate = zip3Aggregate();
  refuses(op("export_sealed_artifact", { aggregate, receipt: safeHarbor() }), "operation_budget_not_bound_by_route");
  const view = op("overlay_properties_at_native_precision", { aggregate, receipt: safeHarbor() });
  assert.equal(view.decision, "within_route");
});

test("REID: one suppressed cell beside a published total is subtractable, and refuses", () => {
  const agg = countyAggregate({ cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: null, suppressed: true }], published_total: 47 });
  refuses(conform({ artifact: artifactOf(agg), receipts: [expert(agg)] }), "complementary_suppression_missing");
});

test("REID: a published total that does not add up refuses", () => {
  const agg = zip3Aggregate({ published_total: 1 });
  refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }), "published_total_inconsistent");
});

test("REID: a small cell under the source threshold refuses under Safe Harbor", () => {
  const agg = zip3Aggregate({ cells: [{ unit_id: "480", period: "2025", patient_count: 3, suppressed: false }] });
  const r = conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] });
  refuses(r, "small_cell_below_effective_floor");
  assert.equal(r.minimum_cell_count, 11);
});

test("REID: a suppressed cell cannot carry its value alongside the flag", () => {
  throwsCode(() => conform({ artifact: artifactOf(zip3Aggregate({ cells: [
    { unit_id: "480", period: "2025", patient_count: 4, suppressed: true }] })) }), "suppressed_cell_carries_count");
});

test("REID: a proposal that writes a coordinate, any ZIP form or a street address is finer than any aggregate", () => {
  for (const statement of [
    "Cluster centered near 12.3456, -45.6789 is strongest.",
    "Unit 480 near 12.34, -45.67.",               // two-decimal coordinate
    "Most demand sits in 99998.",
    "Unit 480, concentrated near ZIP 9 9 9 9 8.",  // spaced ZIP
    "Unit 480 near 999981234.",                    // nine digits, no dash
    "Unit 480 near 99998-1234.",
    "Unit 480 near 123 Synthetic Main Street.",    // street address
    "Unit 480 near 4 Oak Ave.",
  ]) {
    refuses(propose({ statement }), "proposal_finer_than_aggregate");
  }
  // A county FIPS the artifact itself carries is not finer.
  const agg = countyAggregate();
  assert.equal(propose({ statement: "County 56001 leads.", cited_unit_ids: ["56001"] }, agg).decision,
    "proposal_pending_review");
  // Years and three-digit units are not geography.
  assert.equal(propose({ statement: "Unit 480 grew in 2025." }).decision, "proposal_pending_review");
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

test("SEAM: the policy binds the map contract and the config floor, and hashes stably", () => {
  const p = v5J302PolicyPreimage();
  assert.deepEqual(p.map_contract, { id: "carr-map-tour-v1", version: "1.2.0",
    gate: "tour-map-contract-1.2.0-accepted" });
  assert.equal(p.platform_small_cell_floor, 11);
  assert.equal(p.config.provenance, "default_set_by_orchestrator_reversible");
  assert.equal(v5J302PolicyDigest(), v5J302PolicyDigest());
  assert.match(v5J302PolicyDigest(), /^sha256:[0-9a-f]{64}$/);
});

test("SEAM: fixtures carry no real-looking personal identifiers", () => {
  const text = JSON.stringify([artifactOf(), safeHarbor(), expert(), CONTEXT]);
  assert.doesNotMatch(text, /\b\d{3}-\d{2}-\d{4}\b/); // no SSN shapes
  assert.doesNotMatch(text, /@[a-z]+\.[a-z]+/i);      // no e-mail addresses
});

// ===========================================================================
// Orchestrator defaults (reversible), each exercised through the real code.
// ===========================================================================

test("DEFAULT 1: a platform floor of 11 binds under both routes; the source replaces it only when stricter", () => {
  assert.equal(V5_J302_PRIVACY_CONFIG.platform_small_cell_floor, 11);
  // Source says 5, cell is 8: the platform floor refuses it under Safe Harbor...
  const loose = zip3Aggregate({ source_privacy_threshold: { minimum_cell_count: 5, declared_by: "src" },
    cells: [{ unit_id: "480", period: "2025", patient_count: 8, suppressed: false }] });
  const sh = conform({ artifact: artifactOf(loose), receipts: [safeHarbor(loose)] });
  refuses(sh, "small_cell_below_effective_floor");
  assert.equal(sh.minimum_cell_count, 11);
  // ...and under Expert Determination, even with an expert minimum of 5.
  const looseCounty = countyAggregate({ source_privacy_threshold: { minimum_cell_count: 5, declared_by: "src" },
    cells: [{ unit_id: "56001", period: "2025-Q1", patient_count: 8, suppressed: false }], published_total: null });
  const ed = conformExpert(looseCounty, { small_cell: { minimum_cell_count: 5, complementary_suppression_required: true } });
  refuses(ed, "small_cell_below_determination_threshold");
  assert.equal(ed.minimum_cell_count, 11);
  // A stricter source (20) replaces the floor.
  const strict = zip3Aggregate({ source_privacy_threshold: { minimum_cell_count: 20, declared_by: "src" },
    cells: [{ unit_id: "480", period: "2025", patient_count: 15, suppressed: false }] });
  const r = conform({ artifact: artifactOf(strict), receipts: [safeHarbor(strict)] });
  refuses(r, "small_cell_below_effective_floor");
  assert.equal(r.minimum_cell_count, 20);
  // A cell at exactly 11 under a looser source conforms, and says which floor bound it.
  const at = zip3Aggregate({ source_privacy_threshold: { minimum_cell_count: 5, declared_by: "src" },
    cells: [{ unit_id: "480", period: "2025", patient_count: 11, suppressed: false }] });
  const ok = conform({ artifact: artifactOf(at), receipts: [safeHarbor(at)] });
  assert.equal(ok.decision, "conforms");
  assert.equal(ok.effective_small_cell_floor, 11);
});

test("DEFAULT 2: no heat-map-derived content reaches a client audience", () => {
  for (const content_kind of J302.V5_J302_HEAT_MAP_CONTENT_KINDS) {
    const r = evaluateHeatMapAudienceProjection({ tenant: ORGANIZATION_TENANT_ID, audience: "client", content_kind });
    refuses(r, "heat_map_content_not_client_visible");
    assert.equal(r.client_visibility_decision_ref, "decision:4ab3933e");
    const internal = evaluateHeatMapAudienceProjection({ tenant: ORGANIZATION_TENANT_ID, audience: "internal",
      content_kind });
    assert.equal(internal.decision, "internal_only");
    assert.equal(internal.admission, "unavailable");
  }
  assert.deepEqual(V5_J302_PRIVACY_CONFIG.client_visible_heat_map_content, []);
  throwsCode(() => evaluateHeatMapAudienceProjection({ tenant: ORGANIZATION_TENANT_ID, audience: "public",
    content_kind: "heat_map_render" }), "unknown_audience");
});

test("DEFAULT 3: Safe Harbor differencing and export stay refused, and the config cannot say otherwise", () => {
  assert.equal(V5_J302_PRIVACY_CONFIG.safe_harbor_unbudgeted_operations, "refuse");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, safe_harbor_unbudgeted_operations: "allow" }),
    "invalid_config");
});

test("DEFAULT 4: with the 2020 Census slot unknown, every Safe Harbor ZIP3 is denied; 000 and state survive", () => {
  assert.equal(V5_J302_PRIVACY_CONFIG.census_2020_zip3_population.table_digest, null);
  const r = conform({ kernel: { evaluatePrivacyRouteConformance } });
  refuses(r, "zip3_census_2020_population_unknown_denied");
  assert.equal(r.census_2020_status, "unavailable_offline");
  const onlySuppressed = zip3Aggregate({ cells: [{ unit_id: "000", period: "2025", patient_count: 40, suppressed: false }] });
  assert.equal(conform({ kernel: { evaluatePrivacyRouteConformance }, artifact: artifactOf(onlySuppressed),
    receipts: [safeHarbor(onlySuppressed)] }).decision, "conforms");
});

test("DEFAULT 4: once pinned, the 2020 table must be exactly the pinned one, and either census reading can refuse", () => {
  // A receipt carrying some other table (or another vintage) is refused.
  const other = { ...POPULATION, "480": 260000 };
  refuses(conform({ receipts: [safeHarbor(undefined, {}, other)] }), "census_2020_table_not_the_pinned_table");
  const oldVintage = safeHarbor();
  oldVintage.census_population.vintage = "2010";
  refuses(conform({ receipts: [oldVintage] }), "census_2020_table_not_the_pinned_table");
  // 2020 says 20,000 or fewer -> refused even though HHS's 2000 list is silent.
  const agg = zip3Aggregate({ cells: [{ unit_id: "482", period: "2025", patient_count: 30, suppressed: false }] });
  refuses(conform({ artifact: artifactOf(agg), receipts: [safeHarbor(agg)] }), "zip3_population_not_above_floor");
  // 2000 list restricts -> refused even though 2020 puts it high.
  const restricted = V5_J302_HHS_RESTRICTED_ZIP3[0];
  const high = { ...POPULATION, [restricted]: 500000 };
  const r = zip3Aggregate({ cells: [{ unit_id: restricted, period: "2025", patient_count: 30, suppressed: false }] });
  refuses(conform({ artifact: artifactOf(r), receipts: [safeHarbor(r, {}, high)], kernel: pinnedKernel(high) }),
    "restricted_zip3_must_be_000");
});

test("CONFIG: a malformed config is a contract violation, never a looser setting", () => {
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, platform_small_cell_floor: 0 }), "invalid_shape");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, platform_small_cell_floor: 10 }), "invalid_shape");
  // Mutants 741, 747, 753: schema, vintage and digest shape are all checked.
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, schema_version: "other" }), "unknown_config_schema");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, census_2020_zip3_population:
    { vintage: "2010", status: "unavailable_offline", table_digest: null } }), "invalid_config");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, census_2020_zip3_population:
    { vintage: "2020", status: "pinned", table_digest: "not-a-digest" } }), "invalid_digest");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, county_fips_codes:
    { status: "pinned", codes: ["36602", "99001"] } }), "invalid_config");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, county_fips_codes:
    { status: "pinned", codes: null } }), "invalid_config");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, census_2020_zip3_population:
    { vintage: "2020", status: "pinned", table_digest: null } }), "invalid_config");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, client_visible_heat_map_content:
    ["raw_rows"] }), "invalid_config");
  throwsCode(() => readHeatMapPrivacyConfig({ ...V5_J302_PRIVACY_CONFIG, extra: 1 }), "unknown_field");
  throwsCode(() => bindHeatMapPrivacyKernel({}), "missing_field");
});

// ===========================================================================
// Independent review of 9c063f3c (REQUEST_CHANGES): each attack is a test.
// ===========================================================================

test("F1: an operation re-runs conformance — forged, model-issued, future-dated and wrong-artifact receipts refuse", () => {
  const agg = countyAggregate();
  const cases = [
    [expert(agg, { issuer: { identity: "synthetic-requester", kind: "model" }, producer_role: "anything",
      budgets: { query: 1e12, differencing: 1e12, export: 1e12 } }), "route_producer_not_independent_oracle"],
    [expert(agg, { issuer: { identity: "some-model", kind: "model" } }), "model_cannot_issue_privacy_route"],
    [expert(agg, { issued_at: "2026-12-01T00:00:00Z" }), "route_issued_after_now"],
    [expert(agg, { artifact_content_digest: D("other") }), "route_artifact_digest_mismatch"],
    [expert(agg, { small_cell: { minimum_cell_count: 1, complementary_suppression_required: false },
      source_privacy_threshold_acknowledged: 1 }), "source_privacy_threshold_not_preserved"],
  ];
  for (const [receipt, reason] of cases) {
    const r = op("export_sealed_artifact", { receipt, ledger: ledgerFor() });
    refuses(r, "route_not_conforming");
    assert.equal(r.conformance_reason_id, reason);
    assert.equal(r.next_ledger, null);
  }
  // A forged Safe Harbor receipt on the query path refuses too.
  const sh = safeHarbor(zip3Aggregate(), { issuer: { identity: "synthetic-requester", kind: "model" },
    identifier_categories_removed: [], no_actual_knowledge_attestation: false });
  const r = op("overlay_properties_at_native_precision", { aggregate: zip3Aggregate(), receipt: sh });
  refuses(r, "route_not_conforming");
  // An operation no longer takes a caller-supplied artifact digest at all.
  throwsCode(() => PINNED.evaluateAggregateOperation({ ...opRequest("rank_units", { ledger: ledgerFor() }),
    operation: { kind: "rank_units", artifact_digest: D("unrelated") } }), "unknown_field");
});

test("F1: minting a new receipt id does not mint a new budget — the ledger is keyed by what the receipt binds", () => {
  const agg = countyAggregate();
  const one = conform({ artifact: artifactOf(agg), receipts: [expert(agg)] });
  const two = conform({ artifact: artifactOf(agg), receipts: [expert(agg, { receipt_id: "rid-2" })] });
  assert.equal(one.ledger_key, two.ledger_key);
  assert.equal(one.ledger_key, privacyBudgetLedgerKey({ content_digest: CONTENT,
    descriptor_digest: aggregateDescriptorDigest(agg), binding_digest: one.binding_digest }));
  // The spent ledger under receipt 1 is the ledger receipt 2 must present.
  let ledger = ledgerFor(agg);
  ledger = diffOp(ledger, agg).next_ledger;
  refuses(diffOp(ledger, agg, { receipt: expert(agg, { receipt_id: "rid-2" }) }), "privacy_budget_exhausted");
  // Another environment is another binding, and so another ledger.
  const prod = conform({ artifact: artifactOf(agg), receipts: [expert(agg)],
    context: { ...CONTEXT, environment: "production" } });
  assert.equal(prod.decision, "refuse"); // the receipt is bound to staging
});

test("F2: complementary-suppression residual is held to the floor under BOTH routes, whatever the receipt says", () => {
  // A2: Safe Harbor, state geography, two suppressed cells sharing a residual of 1.
  const stateAgg = zip3Aggregate({ geography_unit: "state", cells: [
    { unit_id: "AA", period: "2025", patient_count: 500, suppressed: false },
    { unit_id: "BB", period: "2025", patient_count: null, suppressed: true },
    { unit_id: "CC", period: "2025", patient_count: null, suppressed: true }], published_total: 501 });
  refuses(conform({ artifact: artifactOf(stateAgg), receipts: [safeHarbor(stateAgg)] }),
    "complementary_suppression_residual_below_floor");
  // A2b: Expert Determination whose receipt says complementary suppression is not required.
  const edAgg = countyAggregate({ published_total: 66 });
  refuses(conformExpert(edAgg, { small_cell: { minimum_cell_count: 11, complementary_suppression_required: false } }),
    "complementary_suppression_residual_below_floor");
  // Intake applies the same floor before any receipt is read.
  refuses(admitAggregateHeatMapArtifact({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(stateAgg),
    context: CONTEXT, now: NOW }), "complementary_suppression_residual_below_floor");
  // A residual at the floor conforms; above the platform floor but below a
  // stricter source threshold refuses at the effective floor.
  const at = countyAggregate({ published_total: 76 });
  assert.equal(conformExpert(at).decision, "conforms");
  const strict = countyAggregate({ published_total: 76,
    source_privacy_threshold: { minimum_cell_count: 12, declared_by: "src" } });
  refuses(conformExpert(strict), "complementary_suppression_residual_below_floor");
});

test("F3: a county is a real FIPS code — a ZIP5 declared as a county refuses", () => {
  // A3: two ZIP5 codes declared as counties. The state prefix happens to be
  // valid, so only the bound county list can tell them apart.
  const zipAsCounty = countyAggregate({ cells: [
    { unit_id: "36602", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "36604", period: "2025-Q1", patient_count: 25, suppressed: false }], published_total: null });
  refuses(conformExpert(zipAsCounty), "cell_not_a_valid_county_fips");
  // An impossible state prefix refuses without any list.
  const badState = countyAggregate({ cells: [
    { unit_id: "99001", period: "2025-Q1", patient_count: 40, suppressed: false }], published_total: null });
  refuses(conformExpert(badState), "cell_not_a_valid_county_fips");
  // A tract nested in an unlisted county refuses the same way.
  const tract = countyAggregate({ geography_unit: "census_tract", cells: [
    { unit_id: "56011000100", period: "2025-Q1", patient_count: 40, suppressed: false }], published_total: null });
  refuses(conformExpert(tract, { precision: { finest_spatial_unit: "census_tract", finest_temporal_precision: "quarter" } }),
    "cell_not_a_valid_county_fips");
  // With no county list pinned (the shipped config), county-bearing cells deny.
  const r = evaluatePrivacyRouteConformance({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(countyAggregate()),
    context: CONTEXT, route_receipts: [expert()], now: NOW });
  refuses(r, "county_fips_code_list_unknown_denied");
  assert.equal(r.county_fips_status, "unavailable_offline");
});

test("F4: a bound config cannot lower the floor, open a client audience, or pass as the shipped config", () => {
  // A4: floor 1 is refused by the kernel minimum.
  throwsCode(() => bindHeatMapPrivacyKernel({ ...V5_J302_PRIVACY_CONFIG, platform_small_cell_floor: 1 }), "invalid_shape");
  assert.equal(J302.V5_J302_KERNEL_MINIMUM_SMALL_CELL_FLOOR, 11);
  // A4c: no config can list client-visible content.
  throwsCode(() => bindHeatMapPrivacyKernel({ ...V5_J302_PRIVACY_CONFIG,
    client_visible_heat_map_content: ["heat_map_render"] }), "invalid_config");
  // A4b: an attacker-pinned 2020 table yields results stamped non-authoritative.
  const fakePop = { "480": 250000, "999": 999999 };
  const agg999 = zip3Aggregate({ cells: [{ unit_id: "999", period: "2025", patient_count: 40, suppressed: false }] });
  const r = pinnedKernel(fakePop).evaluatePrivacyRouteConformance({ tenant: ORGANIZATION_TENANT_ID,
    artifact: artifactOf(agg999), context: CONTEXT, route_receipts: [safeHarbor(agg999, {}, fakePop)], now: NOW });
  assert.equal(r.config_authority, "non_authoritative_binding");
  assert.match(r.config_digest, /^sha256:/);
  assert.equal(r.admission, "unavailable");
  // The default exports carry no such stamp and cannot be re-bound.
  const shipped = evaluatePrivacyRouteConformance({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(agg999),
    context: CONTEXT, route_receipts: [safeHarbor(agg999, {}, fakePop)], now: NOW });
  assert.equal(shipped.config_authority, undefined);
  refuses(shipped, "zip3_census_2020_population_unknown_denied");
});

test("F6: a client audience is always refused, through the default and a bound kernel alike", () => {
  for (const kernel of [{ evaluateHeatMapAudienceProjection }, PINNED]) {
    for (const content_kind of J302.V5_J302_HEAT_MAP_CONTENT_KINDS) {
      refuses(kernel.evaluateHeatMapAudienceProjection({ tenant: ORGANIZATION_TENANT_ID, audience: "client",
        content_kind }), "heat_map_content_not_client_visible");
    }
  }
});

test("F7: identities are normalized before independence is judged", () => {
  // A6: a case variant of the issuer as requester.
  refuses(conform({ context: { ...CONTEXT, requesting_actor: "Synthetic-Independent-Oracle" } }),
    "route_not_independent_of_requester");
  // A full-width Unicode variant folds to the same identity under NFKC.
  refuses(conform({ context: { ...CONTEXT, recipient_id: "carr-synthetic-recipient" },
    receipts: [safeHarbor(undefined, { issuer: { identity: "CARR-SYNTHETIC-RECIPIENT", kind: "organization" } })] }),
  "route_not_independent_of_requester");
  refuses(conformExpert(undefined, { expert: { identity: "SYNTHETIC-REQUESTER", qualifications: ["q"],
    independent_of_recipient: true } }), "expert_not_independent_of_requester");
  // A7: the issuer may not be the expert whose determination it attests.
  refuses(conformExpert(undefined, { issuer: { identity: "Synthetic-Expert-1", kind: "human" } }),
    "issuer_is_the_expert");
});

function secondArtifact(cells, overrides = {}) {
  const agg = countyAggregate({ cells, published_total: null, ...overrides });
  const corp = corporate({ content_digest: D("artifact-bytes-2"), native_version: "rev-2" });
  const receipt = expert(agg, { receipt_id: "route-receipt-synthetic-2", artifact_content_digest: D("artifact-bytes-2") });
  return { artifact: artifactOf(agg, corp), route_receipts: [receipt] };
}

test("DIFF: differencing two artifacts refuses any shared cell whose difference is below the floor", () => {
  const first = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false }] });
  const ledger = ledgerFor(first);
  const diff = cells => op("difference_between_artifacts", { aggregate: first, ledger,
    counterpart: secondArtifact(cells) });
  // 40 vs 43: the difference of 3 is a small group revealed by subtraction.
  const r = diff([{ unit_id: "56001", period: "2025-Q1", patient_count: 43, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false }]);
  refuses(r, "difference_below_floor");
  assert.equal(r.unit_id, "56001");
  // A difference of 0 or at least the floor is safe, and spends one differencing unit.
  const ok = diff([{ unit_id: "56001", period: "2025-Q1", patient_count: 51, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false }]);
  assert.equal(ok.decision, "within_budget");
  assert.equal(ok.budget_class, "differencing");
  // A suppressed cell on either side is not differenced.
  assert.equal(diff([{ unit_id: "56001", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56003", period: "2025-Q1", patient_count: null, suppressed: true }]).decision, "within_budget");
});

test("DIFF: the counterpart is required, must itself conform, differ, and match precision", () => {
  const first = countyAggregate();
  const ledger = ledgerFor(first);
  refuses(op("difference_between_artifacts", { aggregate: first, ledger }), "differencing_counterpart_required");
  const bad = secondArtifact([{ unit_id: "56001", period: "2025-Q1", patient_count: 3, suppressed: false }]);
  const r = op("difference_between_artifacts", { aggregate: first, ledger, counterpart: bad });
  refuses(r, "differencing_counterpart_not_conforming");
  assert.equal(r.conformance_reason_id, "small_cell_below_determination_threshold");
  refuses(op("difference_between_artifacts", { aggregate: first, ledger,
    counterpart: { artifact: artifactOf(first), route_receipts: [expert(first)] } }), "differencing_same_artifact");
  const yearly = secondArtifact([{ unit_id: "56001", period: "2025", patient_count: 40, suppressed: false }],
    { temporal_precision: "year" });
  refuses(op("difference_between_artifacts", { aggregate: first, ledger, counterpart: yearly }),
    "differencing_precision_mismatch");
  throwsCode(() => op("rank_units", { aggregate: first, ledger, counterpart: bad }), "unexpected_counterpart");
});

test("MUTANTS: descriptor-level refusals each have a witness", () => {
  // 477: an unsuppressed cell must carry an integer count.
  throwsCode(() => conform({ artifact: artifactOf(zip3Aggregate({ cells: [
    { unit_id: "480", period: "2025", patient_count: 12.5, suppressed: false }] })) }), "invalid_shape");
  // 610: a prior artifact must itself be a routed heat-map artifact.
  throwsCode(() => conform({ prior_artifact: corporate({ declared_data_classes: ["market_comp"] }) }),
    "invalid_prior_artifact");
  // 635 / 647: unregistered reversibility and temporal precision are denied.
  refuses(conform({ artifact: artifactOf(zip3Aggregate({ reversibility: "mystery" })) }), "unknown_reversibility_denied");
  refuses(conform({ artifact: artifactOf(zip3Aggregate({ temporal_precision: "fortnight" })) }),
    "unknown_temporal_precision_denied");
  // 669 / 670: with no temporal precision, a cell may not carry a period; without one it conforms.
  const none = zip3Aggregate({ temporal_precision: "none", cells: [
    { unit_id: "480", period: "2025", patient_count: 40, suppressed: false }] });
  refuses(conform({ artifact: artifactOf(none), receipts: [safeHarbor(none)] }), "cell_period_finer_than_declared");
  const noneOk = zip3Aggregate({ temporal_precision: "none", cells: [
    { unit_id: "480", patient_count: 40, suppressed: false }] });
  assert.equal(conform({ artifact: artifactOf(noneOk), receipts: [safeHarbor(noneOk)] }).decision, "conforms");
  // 677: a repeated cell is a contract violation.
  throwsCode(() => conform({ artifact: artifactOf(zip3Aggregate({ cells: [
    { unit_id: "480", period: "2025", patient_count: 40, suppressed: false },
    { unit_id: "480", period: "2025", patient_count: 41, suppressed: false }] })) }), "duplicate_cell");
});

test("MUTANTS: privacy guards that only fire in combination each have a witness", () => {
  // 699: the state prefix refuses on its own, before any county list is consulted.
  const badState = countyAggregate({ cells: [
    { unit_id: "99001", period: "2025-Q1", patient_count: 40, suppressed: false }], published_total: null });
  refuses(evaluatePrivacyRouteConformance({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(badState),
    context: CONTEXT, route_receipts: [expert(badState)], now: NOW }), "cell_not_a_valid_county_fips");
  // 1153: a published total with nothing suppressed carries no residual to protect.
  const totalled = countyAggregate({ published_total: 65, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false }] });
  assert.equal(conformExpert(totalled).decision, "conforms");
  // 1457: a cell with no counterpart is not differenced (and does not crash).
  const first = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56009", period: "2025-Q1", patient_count: 30, suppressed: false }] });
  const r = op("difference_between_artifacts", { aggregate: first, ledger: ledgerFor(first),
    counterpart: secondArtifact([{ unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false }]) });
  assert.equal(r.decision, "within_budget");
  // 1550: confidence outside [0, 1] is a contract violation.
  for (const confidence of [1.5, -0.1, Number.NaN]) {
    throwsCode(() => propose({ confidence }), "invalid_shape");
  }
  // The widening scan covers every key because no declared key contains a fragment.
  for (const key of J302.V5_J302_PROPOSAL_KEYS) {
    assert.ok(!J302.V5_J302_PROPOSAL_WIDENING_FRAGMENTS.some(f => key.includes(f)), key);
  }
});

test("MUTANTS: duplicates in columns, processors, processor terms and population keys are contract violations", () => {
  throwsCode(() => conform({ artifact: artifactOf(zip3Aggregate({ columns: ["unit_id", "unit_id", "patient_count"] })) }),
    "duplicate_column");
  throwsCode(() => conform({ context: { ...CONTEXT, processors: ["p", "p"] } }), "duplicate_processor");
  throwsCode(() => conformExpert(undefined, { processor_terms: [
    { processor_id: "synthetic-processor-a", terms_digest: D("t1") },
    { processor_id: "synthetic-processor-a", terms_digest: D("t2") }] }), "duplicate_processor");
  throwsCode(() => zip3PopulationTableDigest({ "4800": 1 }), "invalid_zip3");
});

test("MUTANTS: the shape primitives refuse what they cannot read", () => {
  const base = { tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(), context: CONTEXT,
    route_receipts: [safeHarbor()], now: NOW };
  // Accessors, symbol keys and non-plain objects are never read.
  const getter = { ...base };
  Object.defineProperty(getter, "now", { get: () => NOW, enumerable: true });
  throwsCode(() => evaluatePrivacyRouteConformance(getter), "invalid_shape");
  throwsCode(() => evaluatePrivacyRouteConformance({ ...base, [Symbol("x")]: 1 }), "invalid_shape");
  throwsCode(() => evaluatePrivacyRouteConformance(new Map()), "invalid_shape");
  throwsCode(() => evaluatePrivacyRouteConformance({ ...base, route_receipts: "one" }), "invalid_shape");
  throwsCode(() => conform({ context: { ...CONTEXT, recipient_id: "has space" } }), "invalid_identifier");
  throwsCode(() => propose({ statement: "hidden‮text" }), "invalid_shape");
  throwsCode(() => conform({ receipts: [safeHarbor(undefined, { no_actual_knowledge_attestation: "yes" })] }),
    "invalid_shape");
  throwsCode(() => conform({ now: "yesterday" }), "invalid_timestamp");
  // An error carries the detail it names.
  try { conform({ context: { ...CONTEXT, extra: 1 } }); assert.fail("expected a throw"); }
  catch (err) { assert.equal(err.detail.key, "extra"); }
  // Every result is deeply frozen, so a caller cannot edit an answer after the fact.
  const r = conform();
  assert.ok(Object.isFrozen(r));
  assert.ok(Object.isFrozen(r.required_runtime_evidence));
  assert.ok(Object.isFrozen(r.effects));
});

test("DIFF: the stricter floor of the two artifacts governs their differences", () => {
  const first = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false }] });
  // The counterpart's source declared 20; a difference of 15 clears 11 but not 20.
  const counterpart = secondArtifact([{ unit_id: "56001", period: "2025-Q1", patient_count: 55, suppressed: false }],
    { source_privacy_threshold: { minimum_cell_count: 20, declared_by: "synthetic-source-privacy-office" } });
  const r = op("difference_between_artifacts", { aggregate: first, ledger: ledgerFor(first), counterpart });
  refuses(r, "difference_below_floor");
  assert.equal(r.minimum_cell_count, 20);
  // The same holds for a prior release in the history.
  const h = op("view_native_precision", { aggregate: first, ledger: ledgerFor(first), history: [counterpart] });
  refuses(h, "difference_below_floor");
  assert.equal(h.minimum_cell_count, 20);
});

test("N2: residual checks need a published total and a revealed cell — nothing else is combined", () => {
  // A has no total: B revealing one of A's suppressed cells subtracts from nothing.
  const noTotal = countyAggregate({ published_total: null });
  const reveal = secondArtifact([{ unit_id: "56005", period: "2025-Q1", patient_count: 30, suppressed: false }]);
  assert.equal(op("view_native_precision", { aggregate: noTotal, ledger: ledgerFor(noTotal), history: [reveal] })
    .decision, "within_budget");
  // A's residual of 38 clears its own floor of 11. A stricter prior (floor 20)
  // that reveals none of A's cells does not re-judge A's residual at 20.
  const a = countyAggregate({ published_total: 103 });
  const strictPrior = secondArtifact([{ unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false }],
    { source_privacy_threshold: { minimum_cell_count: 20, declared_by: "synthetic-source-privacy-office" } });
  assert.equal(op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [strictPrior] }).decision,
    "within_budget");
});

test("N3: a history entry with the same bytes but another descriptor is a second release, not the artifact itself", () => {
  const first = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false }] });
  const other = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 43, suppressed: false }] });
  const entry = { artifact: artifactOf(other), route_receipts: [expert(other)] };
  refuses(op("view_native_precision", { aggregate: first, ledger: ledgerFor(first), history: [entry] }),
    "difference_below_floor");
});

// ===========================================================================
// Round 2 (N1-N6): relabelling, cross-release residuals, required release
// history, protection intervals, widened proposal filter, store contract.
// ===========================================================================

function relabelled() {
  return corporate({ native_version: "rev-99", native_identity: { source_system: "synthetic-corporate-source",
    native_id: "hm-9999", native_id_epoch: "epoch-9" } });
}

test("N1: relabelling an artifact (native id or version) does not reset its budget", () => {
  const agg = countyAggregate();
  const one = conform({ artifact: artifactOf(agg), receipts: [expert(agg)] });
  const two = conform({ artifact: artifactOf(agg, relabelled()), receipts: [expert(agg)] });
  assert.equal(two.decision, "conforms");
  assert.notEqual(one.artifact_digest, two.artifact_digest);
  assert.equal(one.ledger_key, two.ledger_key);
  let ledger = ledgerFor(agg);
  ledger = diffOp(ledger, agg).next_ledger;
  refuses(diffOp(ledger, agg, { corp: relabelled() }), "privacy_budget_exhausted");
  // Other bytes, another descriptor or another binding is another ledger.
  const other = countyAggregate({ published_total: 106 });
  assert.notEqual(conformExpert(other).ledger_key, one.ledger_key);
});

// attack3: A publishes a total with 56005/56007 suppressed (residual 15);
// B shows 56005 = 11 and suppresses 56007 with no total, so A's 56007 = 4.
function attack3() {
  const a = countyAggregate({ published_total: 80 });
  const b = secondArtifact([
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false },
    { unit_id: "56005", period: "2025-Q1", patient_count: 11, suppressed: false },
    { unit_id: "56007", period: "2025-Q1", patient_count: null, suppressed: true }]);
  return { a, b };
}

test("N2: a cell visible in one release is subtracted from the other's suppressed residual (attack3)", () => {
  const { a, b } = attack3();
  assert.equal(conformExpert(a).decision, "conforms");
  assert.equal(conform({ artifact: b.artifact, receipts: b.route_receipts }).decision, "conforms");
  const viaCounterpart = op("difference_between_artifacts", { aggregate: a, ledger: ledgerFor(a), counterpart: b });
  refuses(viaCounterpart, "suppressed_residual_recovered_by_release");
  assert.equal(viaCounterpart.exposure, "complementary_suppression_missing");
  // The same pair refuses through a plain view once B is in the release history,
  // and in the other direction (B operated on, A released before).
  refuses(op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [b] }),
    "suppressed_residual_recovered_by_release");
  const bLedger = emptyPrivacyBudgetLedger(conform({ artifact: b.artifact, receipts: b.route_receipts }).ledger_key);
  const reverse = PINNED.evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, recipient_class: "internal", artifact: b.artifact,
    context: CONTEXT, route_receipts: b.route_receipts, ledger: bLedger, operation: { kind: "rank_units" },
    release_history: [released({ artifact: artifactOf(a), route_receipts: [expert(a)] })], now: NOW });
  refuses(reverse, "suppressed_residual_recovered_by_release");
  assert.equal(reverse.release_index, 0);
});

test("N2: the remainder after substitution keeps the floor, the interval and consistency", () => {
  // Three suppressed cells, residual 45; B reveals one at 30, leaving 15 over two
  // cells: above the floor and not pinned, so the pair clears.
  const a = countyAggregate({ published_total: 110, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false },
    { unit_id: "56005", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56007", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56009", period: "2025-Q1", patient_count: null, suppressed: true }] });
  assert.equal(conformExpert(a).decision, "conforms");
  const bWith = value => secondArtifact([
    { unit_id: "56005", period: "2025-Q1", patient_count: value, suppressed: false },
    { unit_id: "56007", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56009", period: "2025-Q1", patient_count: null, suppressed: true }]);
  const view = b => op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [b] });
  assert.equal(view(bWith(30)).decision, "within_budget");
  // Leaves 10 over two cells: below the floor.
  const low = view(bWith(35));
  refuses(low, "suppressed_residual_recovered_by_release");
  assert.equal(low.exposure, "complementary_suppression_residual_below_floor");
  // Leaves 20 over two cells: each pinned at 10.
  const pinned = view(bWith(25));
  refuses(pinned, "suppressed_residual_recovered_by_release");
  assert.equal(pinned.exposure, "suppressed_cells_pinned_by_residual");
  // A revealed value larger than the residual is an inconsistency, and refuses.
  refuses(view(bWith(60)), "suppressed_residual_recovered_by_release");
  // Every suppressed cell revealed: a small leftover is itself a small group.
  const all = secondArtifact([
    { unit_id: "56005", period: "2025-Q1", patient_count: 20, suppressed: false },
    { unit_id: "56007", period: "2025-Q1", patient_count: 11, suppressed: false },
    { unit_id: "56009", period: "2025-Q1", patient_count: 11, suppressed: false }]);
  refuses(view(all), "suppressed_residual_recovered_by_release");
});

test("N2/N3 (attack2): two plain queries on two releases, and a suppressed cell shown in the next release", () => {
  // attack2 N3: A shows 56001 = 40, B shows 43. Querying B with A in its
  // history refuses exactly as the differencing operation does.
  const a = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false }] });
  const b = secondArtifact([{ unit_id: "56001", period: "2025-Q1", patient_count: 43, suppressed: false }]);
  const bLedger = emptyPrivacyBudgetLedger(conform({ artifact: b.artifact, receipts: b.route_receipts }).ledger_key);
  refuses(PINNED.evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, recipient_class: "internal", artifact: b.artifact,
    context: CONTEXT, route_receipts: b.route_receipts, ledger: bLedger, operation: { kind: "view_native_precision" },
    release_history: [released({ artifact: artifactOf(a), route_receipts: [expert(a)] })], now: NOW }),
  "difference_below_floor");
  // attack2 N4b: A suppresses 56005/56007 (residual 25); B shows 56005 = 12,
  // so A's 56007 = 13 is derivable — one cell left under the residual.
  const a2 = countyAggregate({ published_total: 90 });
  const b2 = secondArtifact([{ unit_id: "56005", period: "2025-Q1", patient_count: 12, suppressed: false }]);
  const r = op("view_native_precision", { aggregate: a2, ledger: ledgerFor(a2), history: [b2] });
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "complementary_suppression_missing");
});

test("N2: the difference between two published totals must clear the floor", () => {
  const a = countyAggregate({ published_total: 90 });
  const bTotal = total => secondArtifact(countyAggregate().cells, { published_total: total });
  const r = op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [bTotal(93)] });
  refuses(r, "published_total_difference_below_floor");
  assert.equal(r.minimum_cell_count, 11);
  assert.equal(op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [bTotal(90)] }).decision,
    "within_budget");
  // Identical shown cells with totals 11 apart: the same suppressed pair
  // cannot hold both, beyond the revision band, so the pair is inconsistent
  // and refuses rather than being skipped (R1).
  const drift = op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [bTotal(101)] });
  refuses(drift, "suppressed_cell_bounded_across_releases");
  assert.equal(drift.exposure, "releases_inconsistent_beyond_revision_tolerance");
  refuses(op("difference_between_artifacts", { aggregate: a, ledger: ledgerFor(a), counterpart: bTotal(93) }),
    "published_total_difference_below_floor");
});

test("N3: every native-precision operation requires the release history, or answers unavailable", () => {
  for (const kind of Object.keys(J302.V5_J302_OPERATIONS)) {
    const counterpart = kind === "difference_between_artifacts"
      ? secondArtifact(countyAggregate().cells, { published_total: 105 }) : undefined;
    const r = op(kind, { ledger: ledgerFor(), counterpart, history: null });
    assert.equal(r.decision, "unavailable", kind);
    assert.equal(r.reason_id, "release_history_unavailable");
    assert.equal(r.owed_seam, J302.V5_J302_RELEASE_HISTORY_STORE_SEAM);
    assert.equal(r.admission, "unavailable");
    assert.ok(!r.next_ledger && !r.compare_and_swap, kind);
  }
  assert.equal(J302.V5_J302_RELEASE_HISTORY_STORE_SEAM, "seam:v5-j302:release-history-store");
  assert.ok(v5J302PolicyPreimage().seams.includes(J302.V5_J302_RELEASE_HISTORY_STORE_SEAM));
  // An explicit empty history is accepted and reported.
  assert.equal(op("rank_units", { ledger: ledgerFor() }).releases_checked, 0);
});

test("N3/H1c: each prior release is read as an already-released fact of the same recipient and precision", () => {
  const ledger = ledgerFor();
  // Structure still binds: a raw-row prior cannot be read, so it cannot be cleared.
  const raw = secondArtifact([{ unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false }],
    { record_grain: "patient_row" });
  const r = op("view_native_precision", { ledger, history: [raw] });
  refuses(r, "release_history_entry_unreadable");
  assert.equal(r.entry_reason_id, "raw_rows_refused");
  // Receipt currency does not: a prior below today's route rules is still a fact.
  const small = secondArtifact([{ unit_id: "56001", period: "2025-Q1", patient_count: 3, suppressed: false }]);
  assert.equal(op("view_native_precision", { ledger, history: [small] }).decision, "within_budget");
  // The receipt must describe the entry it travels with.
  const mismatched = { ...secondArtifact(countyAggregate().cells, { published_total: 105 }) };
  mismatched.route_receipts = [expert(countyAggregate(), { artifact_content_digest: D("someone-else") })];
  refuses(op("view_native_precision", { ledger, history: [mismatched] }), "release_history_entry_receipt_mismatch");
  // An entry that says production while its receipt binds staging is not self-consistent.
  refuses(op("view_native_precision", { ledger, history: [released(secondArtifact(countyAggregate().cells,
    { published_total: 105 }), { environment: "production" })] }), "release_history_entry_receipt_mismatch");
  // Another recipient's release does not belong in this history; a case variant of
  // the same recipient does.
  const clean = secondArtifact(countyAggregate().cells, { published_total: 105 });
  refuses(op("view_native_precision", { ledger, history: [released(clean, { recipient_id: "another-recipient" })] }),
    "release_history_foreign_recipient");
  const variantReceipt = expert(countyAggregate({ published_total: 105 }), {
    receipt_id: "route-receipt-synthetic-2", artifact_content_digest: D("artifact-bytes-2"),
    dataset_recipient_environment_digest: datasetRecipientEnvironmentDigest({ dataset_digest: DATASET,
      recipient_id: "CARR-Synthetic-Recipient", environment: CONTEXT.environment }) });
  assert.equal(op("view_native_precision", { ledger, history: [released({ artifact: clean.artifact,
    route_receipts: [variantReceipt] }, { recipient_id: "CARR-Synthetic-Recipient" })] }).decision, "within_budget");
  throwsCode(() => op("view_native_precision", { ledger, history: [{ ...released(clean), tenant: "other" }] }),
    "foreign_tenant");
  throwsCode(() => op("view_native_precision", { ledger, history: [released(clean, { environment: "dev" })] }),
    "unknown_environment");
  const yearly = secondArtifact([{ unit_id: "56001", period: "2025", patient_count: 40, suppressed: false }],
    { temporal_precision: "year" });
  refuses(op("view_native_precision", { ledger, history: [yearly] }), "release_history_precision_mismatch");
  // The artifact itself, or a relabelled copy of it, in its own history compares clean.
  const self = { artifact: artifactOf(countyAggregate(), relabelled()), route_receipts: [expert()] };
  assert.equal(op("view_native_precision", { ledger, history: [self] }).decision, "within_budget");
  const ok = op("view_native_precision", { ledger, history: [self, clean] });
  assert.equal(ok.decision, "within_budget");
  assert.equal(ok.releases_checked, 2);
  throwsCode(() => op("view_native_precision", { ledger, history: [{ ...released(clean), note: "x" }] }), "unknown_field");
  throwsCode(() => op("view_native_precision", { ledger, history: [released({ artifact: clean.artifact })] }),
    "missing_field");
  throwsCode(() => op("view_native_precision", { ledger, history: "none" }), "invalid_shape");
});

test("N4: a residual that pins the suppressed cells to exact values refuses", () => {
  // Two cells sharing 20 must each be 10 when a primary suppression is 1..10.
  const pinned = countyAggregate({ published_total: 85 });
  refuses(conformExpert(pinned), "suppressed_cells_pinned_by_residual");
  // Intake applies the same interval at the platform floor (state geography,
  // so the shipped config's unpinned county list does not answer first).
  const stateAgg = zip3Aggregate({ geography_unit: "state", cells: [
    { unit_id: "AA", period: "2025", patient_count: 500, suppressed: false },
    { unit_id: "BB", period: "2025", patient_count: null, suppressed: true },
    { unit_id: "CC", period: "2025", patient_count: null, suppressed: true }], published_total: 520 });
  refuses(admitAggregateHeatMapArtifact({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(stateAgg),
    context: CONTEXT, now: NOW }), "suppressed_cells_pinned_by_residual");
  // 21 leaves a range; three cells sharing 30 are pinned too. (19, where each
  // cell is 9 or 10, is too narrow under L1 below.)
  assert.equal(conformExpert(countyAggregate({ published_total: 86 })).decision, "conforms");
  const three = countyAggregate({ published_total: 95, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false },
    { unit_id: "56005", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56007", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56009", period: "2025-Q1", patient_count: null, suppressed: true }] });
  refuses(conformExpert(three), "suppressed_cells_pinned_by_residual");
  // At a stricter source floor of 12, 22 over two cells is pinned at 11 each.
  const strict = countyAggregate({ published_total: 87,
    source_privacy_threshold: { minimum_cell_count: 12, declared_by: "synthetic-source-privacy-office" } });
  refuses(conformExpert(strict, { source_privacy_threshold_acknowledged: 12,
    small_cell: { minimum_cell_count: 12, complementary_suppression_required: true } }),
  "suppressed_cells_pinned_by_residual");
  // The helper's edges, at an interval width of 2 (exact pins only).
  const { suppressionExposure } = J302;
  assert.equal(suppressionExposure(0, 0, 11, 2), null);
  assert.equal(suppressionExposure(1, 50, 11, 2), "complementary_suppression_missing");
  assert.equal(suppressionExposure(2, 10, 11, 2), "complementary_suppression_residual_below_floor");
  assert.equal(suppressionExposure(2, 11, 11, 2), null);
  assert.equal(suppressionExposure(2, 20, 11, 2), "suppressed_cells_pinned_by_residual");
  assert.equal(suppressionExposure(2, 21, 11, 2), null);
  assert.equal(suppressionExposure(3, 29, 11, 2), null);
  assert.equal(suppressionExposure(3, 30, 11, 2), "suppressed_cells_pinned_by_residual");
  // Eleven cells sharing 11 are each exactly 1; sharing 12 leaves each in {1, 2}.
  assert.equal(suppressionExposure(11, 11, 11, 2), "suppressed_cells_pinned_by_residual");
  assert.equal(suppressionExposure(11, 12, 11, 2), null);
});

test("N5: hemisphere, degree-minute, spelled-out and keyword ZIP forms are finer than any aggregate", () => {
  for (const statement of [
    "Unit 480 clusters near 12.34 N 45.67 W.",
    "Unit 480 clusters near 12.34N, 45.67W.",
    "Unit 480 clusters near 12°20'N 45°40'W.",
    "Unit 480 clusters near 12 deg 20 min.",
    "Unit 480 clusters near 12° north.",
    "Unit 480 sits at 20' N of the line.",
    "Unit 480 leads in zip nine nine nine nine eight.",
    "Unit 480 leads around nine nine nine nine eight.",
    "Unit 480 leads around nine-nine-nine-nine-eight.",
    "Unit 480 leads in zip 9999 8.",
    "Unit 480 leads in zip code 12.",
    "Unit 480 leads in ZIP: 9.",
  ]) {
    refuses(propose({ statement }), "proposal_finer_than_aggregate");
  }
  // Ordinary words and counts are not caught.
  for (const statement of [
    "Unit 480 grew in 2025.",
    "Unit 480 leads by one or two points.",
    "Unit 480 is north of unit 481 and zippy growth continued.",
    "Unit 480 was 2x unit 481 in 2025.",
  ]) {
    assert.equal(propose({ statement }).decision, "proposal_pending_review", statement);
  }
});

test("N6: the store's compare-and-swap compares the FULL ledger digest, not the version", () => {
  const contract = J302.V5_J302_BUDGET_LEDGER_STORE_CONTRACT;
  assert.equal(contract.compare_and_swap_compares, "expected_ledger_digest");
  assert.equal(contract.version_only_compare_sufficient, false);
  assert.ok(Object.isFrozen(contract));
  assert.deepEqual(v5J302PolicyPreimage().budget_ledger_store_contract, { ...contract });
  // attack2 N2: spend moved between classes at the same version. The kernel
  // accepts both (each is internally consistent); only their digests differ,
  // which is exactly what the store must compare.
  const agg = countyAggregate();
  let real = ledgerFor(agg);
  real = diffOp(real, agg).next_ledger;
  const reshuffled = { ...real, used: { differencing: 0, export: 0, query: 1 } };
  const a = op("view_native_precision", { aggregate: agg, ledger: real });
  const b = op("view_native_precision", { aggregate: agg, ledger: reshuffled });
  assert.equal(a.compare_and_swap.expected_ledger_version, b.compare_and_swap.expected_ledger_version);
  assert.notEqual(a.compare_and_swap.expected_ledger_digest, b.compare_and_swap.expected_ledger_digest);
  assert.equal(a.compare_and_swap.store_must_compare, "expected_ledger_digest");
  // With differencing spent, the real ledger refuses a second one; the
  // reshuffled one would not — the residual only the store closes.
  refuses(diffOp(real, agg), "privacy_budget_exhausted");
  assert.equal(diffOp(reshuffled, agg).decision, "within_budget");
});

// ===========================================================================
// Round 3 (H1c, H2, H3, L1, L2): recipient-scoped history of released facts,
// capacity, interval width, proposals against history.
// ===========================================================================

// attack4 H2: A under dataset D1 publishes total 80 with 56005/56007
// suppressed; B under a refreshed dataset D2, same recipient, shows 56005 = 11.
function refreshed(aggregate, { dataset = D("dataset-refresh-2"), environment = CONTEXT.environment,
  content = D("bytes-B"), receiptOverrides = {} } = {}) {
  const corp = corporate({ content_digest: content, native_version: "rev-B" });
  const receipt = expert(aggregate, { receipt_id: "rB2", artifact_content_digest: content,
    dataset_recipient_environment_digest: datasetRecipientEnvironmentDigest({ dataset_digest: dataset,
      recipient_id: CONTEXT.recipient_id, environment }), ...receiptOverrides });
  return { artifact: artifactOf(aggregate, corp, dataset), route_receipts: [receipt], environment };
}

test("H2 (attack4): history is scoped to the recipient, across dataset refreshes and environments", () => {
  const a = countyAggregate({ published_total: 80 });
  const bAgg = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false },
    { unit_id: "56005", period: "2025-Q1", patient_count: 11, suppressed: false },
    { unit_id: "56007", period: "2025-Q1", patient_count: null, suppressed: true }] });
  const b = refreshed(bAgg);
  const bConform = conform({ artifact: b.artifact, receipts: b.route_receipts });
  assert.equal(bConform.decision, "conforms");
  const viewB = history => PINNED.evaluateAggregateOperation({ tenant: ORGANIZATION_TENANT_ID, recipient_class: "internal", artifact: b.artifact,
    context: CONTEXT, route_receipts: b.route_receipts, ledger: emptyPrivacyBudgetLedger(bConform.ledger_key),
    operation: { kind: "view_native_precision" }, release_history: history, now: NOW });
  // A under D1 is in B's recipient-scoped history; 80 - 65 - 11 = 4 is refused.
  const r = viewB([released({ artifact: artifactOf(a), route_receipts: [expert(a)] })]);
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "complementary_suppression_missing");
  // Staging vs production for the same recipient: the production release counts too.
  const prod = refreshed(bAgg, { dataset: DATASET, environment: "production" });
  refuses(op("view_native_precision", { aggregate: a, ledger: ledgerFor(a),
    history: [released({ artifact: prod.artifact, route_receipts: prod.route_receipts },
      { environment: "production" })] }), "suppressed_residual_recovered_by_release");
  // The contract says so, and binds the policy digest.
  const contract = J302.V5_J302_RELEASE_HISTORY_STORE_CONTRACT;
  assert.equal(contract.scoped_by, "recipient");
  assert.deepEqual([...contract.spans], ["dataset", "environment"]);
  assert.equal(contract.receipt_currency_judged, false);
  assert.equal(contract.may_drop_or_truncate, false);
  assert.ok(Object.isFrozen(contract));
  assert.equal(v5J302PolicyPreimage().release_history_store_contract.scoped_by, "recipient");
});

test("H1c: an expired prior receipt does not un-release its cells, nor block the recipient", () => {
  const a = countyAggregate({ published_total: 80 });
  const bAgg = countyAggregate({ published_total: null, cells: [
    { unit_id: "56005", period: "2025-Q1", patient_count: 11, suppressed: false }] });
  const expired = { expires_at: T.past, retention: { retain_until: "2026-09-19T00:00:00Z",
    deletion_rule_ref: "synthetic-deletion-rule" } };
  const b = refreshed(bAgg, { dataset: DATASET, receiptOverrides: expired });
  assert.equal(conform({ artifact: b.artifact, receipts: b.route_receipts }).decision, "refuse");
  refuses(op("view_native_precision", { aggregate: a, ledger: ledgerFor(a),
    history: [{ artifact: b.artifact, route_receipts: b.route_receipts }] }), "suppressed_residual_recovered_by_release");
  // A harmless expired prior clears.
  const harmless = refreshed(countyAggregate(), { dataset: DATASET, receiptOverrides: expired });
  assert.equal(op("view_native_precision", { history: [{ artifact: harmless.artifact,
    route_receipts: harmless.route_receipts }], ledger: ledgerFor() }).decision, "within_budget");
  // A prior whose own residual pins its cells (released before that rule) is read,
  // not refused; its cells still count.
  const pinnedPrior = refreshed(countyAggregate({ published_total: 85 }), { dataset: DATASET });
  const shown = countyAggregate({ cells: [q1("56001", 40), q1("56003", 25)], published_total: null });
  assert.equal(op("view_native_precision", { aggregate: shown, history: [{ artifact: pinnedPrior.artifact,
    route_receipts: pinnedPrior.route_receipts }], ledger: ledgerFor(shown) }).decision, "within_budget");
  // The prior's floor at release (an expert minimum of 20) governs the pair.
  const first = countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false }] });
  const strict = refreshed(countyAggregate({ published_total: null, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 55, suppressed: false }] }),
  { dataset: DATASET, receiptOverrides: { ...expired,
    small_cell: { minimum_cell_count: 20, complementary_suppression_required: true } } });
  const h = op("view_native_precision", { aggregate: first, ledger: ledgerFor(first),
    history: [{ artifact: strict.artifact, route_receipts: strict.route_receipts }] });
  refuses(h, "difference_below_floor");
  assert.equal(h.minimum_cell_count, 20);
});

test("H3: history up to 512 entries is judged; above it the answer is unavailable, never a throw", () => {
  assert.equal(J302.V5_J302_MAX_RELEASE_HISTORY, 512);
  assert.equal(J302.V5_J302_RELEASE_HISTORY_STORE_CONTRACT.max_entries, 512);
  assert.equal(J302.V5_J302_RELEASE_HISTORY_STORE_CONTRACT.over_capacity, "refuse_never_drop_or_truncate");
  const clean = secondArtifact(countyAggregate().cells, { published_total: 105 });
  const full = op("view_native_precision", { ledger: ledgerFor(), history: Array(512).fill(clean) });
  assert.equal(full.decision, "within_budget");
  assert.equal(full.releases_checked, 512);
  const over = op("view_native_precision", { ledger: ledgerFor(), history: Array(513).fill(clean) });
  assert.equal(over.decision, "unavailable");
  assert.equal(over.reason_id, "release_history_over_kernel_capacity");
  assert.equal(over.owed_seam, J302.V5_J302_RELEASE_HISTORY_STORE_SEAM);
  assert.equal(over.releases_presented, 513);
  assert.ok(!over.next_ledger);
});

test("L1: suppressed cells bounded to fewer than three values each refuse", () => {
  // Two cells sharing 19 are each 9 or 10: too narrow; sharing 18 leaves 8..10.
  refuses(conformExpert(countyAggregate({ published_total: 84 })), "suppressed_cells_interval_too_narrow");
  assert.equal(conformExpert(countyAggregate({ published_total: 83 })).decision, "conforms");
  refuses(conformExpert(countyAggregate({ published_total: 85 })), "suppressed_cells_pinned_by_residual");
  // Across releases too: B reveals one of three cells, leaving 19 over two.
  const a = countyAggregate({ published_total: 110, cells: [
    { unit_id: "56001", period: "2025-Q1", patient_count: 40, suppressed: false },
    { unit_id: "56003", period: "2025-Q1", patient_count: 25, suppressed: false },
    { unit_id: "56005", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56007", period: "2025-Q1", patient_count: null, suppressed: true },
    { unit_id: "56009", period: "2025-Q1", patient_count: null, suppressed: true }] });
  const b = secondArtifact([{ unit_id: "56005", period: "2025-Q1", patient_count: 26, suppressed: false }]);
  const r = op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [b] });
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "suppressed_cells_interval_too_narrow");
  // The width is config (default set by the orchestrator, reversible): 2 allows
  // two-value intervals; below 2 is refused by the kernel.
  assert.equal(V5_J302_PRIVACY_CONFIG.minimum_protection_interval_values, 3);
  assert.equal(pinnedKernel(POPULATION, { minimum_protection_interval_values: 2 })
    .evaluatePrivacyRouteConformance({ tenant: ORGANIZATION_TENANT_ID,
      artifact: artifactOf(countyAggregate({ published_total: 84 })),
      context: CONTEXT, route_receipts: [expert(countyAggregate({ published_total: 84 }))], now: NOW }).decision,
  "conforms");
  throwsCode(() => bindHeatMapPrivacyKernel({ ...V5_J302_PRIVACY_CONFIG, minimum_protection_interval_values: 1 }),
    "invalid_shape");
  assert.equal(J302.V5_J302_KERNEL_MINIMUM_PROTECTION_INTERVAL_VALUES, 2);
  // The width also binds at a stricter effective floor: at 12, two cells sharing
  // 21 are each 10 or 11, although the platform floor never looks at 21.
  const strict = countyAggregate({ published_total: 86,
    source_privacy_threshold: { minimum_cell_count: 12, declared_by: "synthetic-source-privacy-office" } });
  refuses(conformExpert(strict, { source_privacy_threshold_acknowledged: 12,
    small_cell: { minimum_cell_count: 12, complementary_suppression_required: true } }),
  "suppressed_cells_interval_too_narrow");
  const { suppressionExposure } = J302;
  assert.equal(suppressionExposure(2, 19, 11, 3), "suppressed_cells_interval_too_narrow");
  assert.equal(suppressionExposure(2, 18, 11, 3), null);
  assert.equal(suppressionExposure(2, 20, 11, 3), "suppressed_cells_pinned_by_residual");
  assert.equal(suppressionExposure(3, 29, 11, 3), "suppressed_cells_interval_too_narrow");
  assert.equal(suppressionExposure(3, 28, 11, 3), null);
  assert.equal(suppressionExposure(11, 12, 11, 3), "suppressed_cells_interval_too_narrow");
  assert.equal(suppressionExposure(11, 13, 11, 3), null);
});

test("L2: proposals take the release history, and cannot describe change between releases", () => {
  // No history: unavailable, naming the store seam, still a proposal-shaped answer.
  const request = proposalRequest();
  delete request.release_history;
  const r = PINNED.evaluateDerivedStrategyProposal(request);
  assert.equal(r.decision, "unavailable");
  assert.equal(r.reason_id, "release_history_unavailable");
  assert.equal(r.owed_seam, J302.V5_J302_RELEASE_HISTORY_STORE_SEAM);
  assert.equal(r.is_fact, false);
  assert.equal(r.admission, "unavailable");
  // attack3's pair refuses through a proposal on A with B in history.
  const { a, b } = attack3();
  const withHistory = proposalRequest({ statement: "County 56001 leads.", cited_unit_ids: ["56001"] }, a);
  withHistory.release_history = asHistory([b]);
  const leak = PINNED.evaluateDerivedStrategyProposal(withHistory);
  assert.equal(leak.decision, "refuse");
  assert.equal(leak.reason_id, "suppressed_residual_recovered_by_release");
  assert.equal(leak.is_fact, false);
  // Change between releases for a unit, in any wording, is a difference in words.
  for (const statement of [
    "Unit 480 is 2 patients above last year.",
    "Unit 480, which rose by 3 since the Q1 release.",
    "Unit 480 grew 12%.",
    "Unit 480 fell by three.",
    "Unit 480 is up compared to the prior release.",
    "Unit 480 doubled.",
    "Unit 480 has fewer patients than before.",
  ]) {
    const p = propose({ statement });
    assert.equal(p.decision, "refuse", statement);
    assert.equal(p.reason_id, "proposal_describes_change_between_releases", statement);
  }
  for (const statement of [
    "Unit 480 grew in 2025.",
    "Unit 480 carries the largest aggregate count in the artifact year.",
    "Unit 480 leads by one or two points.",
    "Unit 480 is north of unit 481 and zippy growth continued.",
  ]) {
    assert.equal(propose({ statement }).decision, "proposal_pending_review", statement);
  }
});

// ===========================================================================
// Round 4 (P1, M1, F7, C1, wording): joint bounds across releases, recipient
// class, released priors and the county list, cell capacity.
// ===========================================================================

const q1 = (unit, count) => ({ unit_id: unit, period: "2025-Q1", patient_count: count, suppressed: count === null });
const q2 = (unit, count) => ({ unit_id: unit, period: "2025-Q2", patient_count: count, suppressed: count === null });
// View `current` with `prior` as its only released history (and the reverse).
function jointView(current, prior) {
  const b = secondArtifact(prior.cells, { published_total: prior.published_total });
  return op("view_native_precision", { aggregate: current, ledger: ledgerFor(current), history: [b] });
}

test("P1 (attack5): nested suppressed sets recover a cell exactly, in both directions", () => {
  // A: 56001 = 50, X and Y suppressed, total 63 (X + Y = 13).
  // B: 56001 = 50, 56003 = 30, X, Y and Z suppressed, total 97 (X + Y + Z = 17), so Z = 4.
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56005", null), q1("56007", null)], published_total: 63 });
  const b = countyAggregate({ cells: [q1("56001", 50), q1("56003", 30), q1("56005", null), q1("56007", null),
    q1("56009", null)], published_total: 97 });
  assert.equal(conformExpert(a).decision, "conforms");
  assert.equal(conformExpert(b).decision, "conforms");
  for (const [current, prior] of [[b, a], [a, b]]) {
    const r = jointView(current, prior);
    refuses(r, "suppressed_cell_bounded_across_releases");
    assert.equal(r.exposure, "complementary_suppression_missing");
    assert.equal(r.unit_id, "56009");
  }
});

test("P1: three-cell nested groups keep the floor and the interval width", () => {
  // A: Q1 X + Y = 15. B adds three Q2 cells to the same suppressed set.
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null)], published_total: 65 });
  // B also shows a Q2 cell of 30, so the totals themselves differ by at least the floor.
  const bWith = group => countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null),
    q2("56001", 30), q2("56003", null), q2("56005", null), q2("56007", null)], published_total: 95 + group });
  // The three extra cells sum to 9: below the floor.
  let r = jointView(bWith(9), a);
  refuses(r, "suppressed_cell_bounded_across_releases");
  assert.equal(r.exposure, "complementary_suppression_residual_below_floor");
  // Sum 29: each of the three is 9 or 10.
  r = jointView(bWith(29), a);
  refuses(r, "suppressed_cell_bounded_across_releases");
  assert.equal(r.exposure, "suppressed_cells_interval_too_narrow");
  // Sum 25: each of 5..10, and the pair clears.
  assert.equal(jointView(bWith(25), a).decision, "within_budget");
  // A group sum of -1 is not "no exposure": within the revision band (2) it may
  // be a revision of -2 with Z = 1, so Z is effectively exact (R1).
  const revised = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null),
    q1("56007", null), q2("56001", 30)], published_total: 94 });
  assert.equal(conformExpert(revised).decision, "conforms");
  const rr = jointView(revised, a);
  refuses(rr, "suppressed_cell_bounded_across_releases");
  assert.equal(rr.exposure, "complementary_suppression_missing");
  assert.equal(Math.abs(rr.revision_offsets[0]) + Math.abs(rr.revision_offsets[1]), 2);
});

test("P1: partially overlapping suppressed sets narrow a cell below the interval width", () => {
  // A: X + Y + Z = 20; B: X + Y + W = 28. Alone each keeps >= 3 values per cell;
  // together W - Z = 8, so Z is 1 or 2 and W is 9 or 10.
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null), q1("56007", null)],
    published_total: 70 });
  // B also shows a Q2 cell of 30, so the totals themselves differ by at least the floor.
  const b = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null), q1("56009", null),
    q2("56001", 30)], published_total: 108 });
  assert.equal(conformExpert(a).decision, "conforms");
  assert.equal(conformExpert(b).decision, "conforms");
  const r = jointView(b, a);
  refuses(r, "suppressed_cell_bounded_across_releases");
  assert.equal(r.exposure, "suppressed_cells_interval_too_narrow");
  // B at 27 (W - Z = 7) clears without drift, but a revision of +1 makes it 8:
  // refused (R2). At 25 (W - Z = 5, up to 7 in the band) the pair clears.
  refuses(jointView(countyAggregate({ cells: b.cells, published_total: 107 }), a),
    "suppressed_cell_bounded_across_releases");
  assert.equal(jointView(countyAggregate({ cells: b.cells, published_total: 105 }), a).decision, "within_budget");
  // attack5 P2 (X + Y = 18 and X + W = 12): with no revision every cell keeps
  // three values; within the band a revision of 2 would pin Y = 10 and W = 2,
  // so the default (tolerance 2) refuses and a tolerance of 0 clears.
  const p2a = countyAggregate({ cells: [q1("56001", 50), q1("56005", null), q1("56007", null)], published_total: 68 });
  const p2b = countyAggregate({ cells: [q1("56001", 50), q1("56003", 20), q1("56005", null), q1("56009", null)],
    published_total: 82 });
  const p2 = jointView(p2b, p2a);
  refuses(p2, "suppressed_cell_bounded_across_releases");
  assert.notDeepEqual(p2.revision_offsets, [0, 0]);
  const exact = pinnedKernel(POPULATION, { revision_tolerance_patients: 0 });
  assert.equal(exact.evaluateAggregateOperation(opRequest("view_native_precision", { aggregate: p2b,
    ledger: ledgerFor(p2b), history: [secondArtifact(p2a.cells, { published_total: 68 })] })).decision,
  "within_budget");
  // Identical releases combine to nothing new.
  assert.equal(jointView(a, a).decision, "within_budget");
});

test("M1: operations and proposals take a recipient class; only internal proceeds", () => {
  for (const recipient_class of ["client", "public"]) {
    for (const kind of ["export_sealed_artifact", "view_native_precision"]) {
      const r = PINNED.evaluateAggregateOperation({ ...opRequest(kind, { ledger: ledgerFor() }), recipient_class });
      refuses(r, "recipient_class_not_permitted");
      assert.equal(r.recipient_class, recipient_class);
      assert.equal(r.decision_ref, "decision:4ab3933e");
      assert.equal(r.map_contract, J302.V5_J302_EXPORT_PROMOTION_REQUIREMENT.map_contract);
      assert.equal(r.owed_seam, J302.V5_J302_RECIPIENT_CLASS_SEAM);
      assert.ok(!r.next_ledger);
    }
    const p = PINNED.evaluateDerivedStrategyProposal({ ...proposalRequest(), recipient_class });
    assert.equal(p.decision, "refuse");
    assert.equal(p.reason_id, "recipient_class_not_permitted");
    assert.equal(p.is_fact, false);
  }
  throwsCode(() => PINNED.evaluateAggregateOperation({ ...opRequest("rank_units", { ledger: ledgerFor() }),
    recipient_class: "partner" }), "unknown_recipient_class");
  const missing = opRequest("rank_units", { ledger: ledgerFor() });
  delete missing.recipient_class;
  throwsCode(() => PINNED.evaluateAggregateOperation(missing), "missing_field");
  // HARD BLOCKER: even an internal export answers unavailable until the
  // authenticated recipient-class registry exists, before any budget is read,
  // and names the human promotion receipt it will still owe.
  const exp = op("export_sealed_artifact", { ledger: ledgerFor() });
  assert.equal(exp.decision, "unavailable");
  assert.equal(exp.reason_id, "export_blocked_until_recipient_class_registry");
  assert.equal(exp.owed_seam, J302.V5_J302_RECIPIENT_CLASS_SEAM);
  assert.equal(J302.V5_J302_EXPORT_BLOCKED_UNTIL, J302.V5_J302_RECIPIENT_CLASS_SEAM);
  assert.equal(v5J302PolicyPreimage().export_blocked_until, J302.V5_J302_RECIPIENT_CLASS_SEAM);
  assert.ok(!exp.next_ledger && !exp.compare_and_swap);
  // No ledger at all still answers unavailable, not budget_ledger_required.
  assert.equal(op("export_sealed_artifact").reason_id, "export_blocked_until_recipient_class_registry");
  // A bound config cannot open it.
  assert.equal(pinnedKernel().evaluateAggregateOperation(opRequest("export_sealed_artifact",
    { ledger: ledgerFor() })).decision, "unavailable");
  assert.deepEqual(exp.owed_human_promotion_receipt, {
    method_id: "human_promotion_receipt", verb: "record-tour-map-promotion-receipt",
    map_contract: J302.V5_J302_EXPORT_PROMOTION_REQUIREMENT.map_contract,
    required_before: ["client_use", "public_use"] });
  assert.equal(op("view_native_precision", { ledger: ledgerFor() }).owed_human_promotion_receipt, undefined);
  assert.ok(v5J302PolicyPreimage().seams.includes(J302.V5_J302_RECIPIENT_CLASS_SEAM));
  assert.deepEqual(v5J302PolicyPreimage().permitted_recipient_classes, ["internal"]);
});

test("F7 (attack5): a released prior naming a county later dropped from the list is still read", () => {
  const narrow = pinnedKernel(POPULATION, { county_fips_codes: { status: "pinned", codes: ["56001", "56003", "56005"] } });
  const current = countyAggregate({ cells: [q1("56001", 40), q1("56003", 25)], published_total: null });
  const prior = secondArtifact(countyAggregate().cells, { published_total: 105 }); // names 56007
  const r = narrow.evaluateAggregateOperation(opRequest("view_native_precision",
    { aggregate: current, ledger: ledgerFor(current), history: [prior] }));
  assert.equal(r.decision, "within_budget");
  // Its cells still count: a prior 56001 = 43 is three away from 40.
  const close = secondArtifact([q1("56001", 43), q1("56007", 30)]);
  refuses(narrow.evaluateAggregateOperation(opRequest("view_native_precision",
    { aggregate: current, ledger: ledgerFor(current), history: [close] })), "difference_below_floor");
  // The state prefix is structure and still binds a released prior.
  const badState = secondArtifact([q1("99001", 40)]);
  const r2 = narrow.evaluateAggregateOperation(opRequest("view_native_precision",
    { aggregate: current, ledger: ledgerFor(current), history: [badState] }));
  refuses(r2, "release_history_entry_unreadable");
  assert.equal(r2.entry_reason_id, "cell_not_a_valid_county_fips");
  // The artifact being operated on is still held to the current list.
  refuses(narrow.evaluateAggregateOperation(opRequest("view_native_precision", { ledger: ledgerFor() })),
    "route_not_conforming");
});

test("C1: history capacity is counted in cells as well as entries", () => {
  assert.equal(J302.V5_J302_RELEASE_HISTORY_STORE_CONTRACT.max_cells, 250000);
  const cellsOf = n => Array.from({ length: n }, (_, i) => ({ unit_id: ["56001", "56003", "56005", "56007", "56009"][i % 5],
    period: `${3000 + Math.floor(i / 5)}-Q1`, patient_count: 20, suppressed: false }));
  const big = secondArtifact(cellsOf(5000)); // no period shared with the current artifact
  const atCap = op("view_native_precision", { ledger: ledgerFor(), history: Array(50).fill(big) });
  assert.equal(atCap.decision, "within_budget");
  assert.equal(atCap.releases_checked, 50);
  const over = op("view_native_precision", { ledger: ledgerFor(), history: [...Array(50).fill(big),
    secondArtifact(cellsOf(1))] });
  assert.equal(over.decision, "unavailable");
  assert.equal(over.reason_id, "release_history_over_kernel_capacity");
  assert.equal(over.cells_presented, 250001);
  assert.equal(over.max_cells, 250000);
  assert.equal(over.owed_seam, J302.V5_J302_RELEASE_HISTORY_STORE_SEAM);
});

test("L2b (attack5): more change wordings are differences in words", () => {
  for (const statement of [
    "Unit 480 went from 137 to 140.",
    "Unit 480 added 3 patients.",
    "Unit 480 has 3 new patients this cycle.",
    "Unit 480 now carries 140, not 137.",
    "Unit 480 moved by two.",
    "Unit 480 reached 140.",
  ]) {
    const p = propose({ statement });
    assert.equal(p.reason_id, "proposal_describes_change_between_releases", statement);
  }
  for (const statement of [
    "Unit 480 was 2x unit 481 in 2025.",
    "Unit 480 carries the largest aggregate count in the artifact year.",
  ]) {
    assert.equal(propose({ statement }).decision, "proposal_pending_review", statement);
  }
});

test("P1: the joint equations substitute cells the other release shows, and leave a fully shown release alone", () => {
  // A suppresses Q, X and Y (total 93: Q + X + Y = 43). B shows Q = 30 and
  // suppresses X, Y and Z (X + Y + Z = 17), plus an unrelated Q2 cell. With
  // Q substituted, A says X + Y = 13, so Z = 4.
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null), q1("56007", null)],
    published_total: 93 });
  const b = countyAggregate({ cells: [q1("56001", 50), q1("56003", 30), q1("56005", null), q1("56007", null),
    q1("56009", null), q2("56001", 30)], published_total: 127 });
  assert.equal(conformExpert(a).decision, "conforms");
  assert.equal(conformExpert(b).decision, "conforms");
  const r = jointView(a, b);
  refuses(r, "suppressed_cell_bounded_across_releases");
  assert.equal(r.unit_id, "56009");
  // A prior that suppresses nothing but carries a total and a stricter floor
  // (20) combines with nothing: the current residual of 38 over two cells is
  // judged at its own floor, not re-judged at 20.
  const current = countyAggregate({ published_total: 103 });
  const strictPrior = secondArtifact([q1("56001", 40)], { published_total: 40,
    source_privacy_threshold: { minimum_cell_count: 20, declared_by: "synthetic-source-privacy-office" } });
  assert.equal(op("view_native_precision", { aggregate: current, ledger: ledgerFor(current),
    history: [strictPrior] }).decision, "within_budget");
});

test("P1: overlapping sets of different sizes are not nested, and joint propagation can pin a cell exactly", () => {
  // A: X + Y = 15; B: Y + Z + W = 18 (plus an unrelated shown Q2 cell). Not nested:
  // no group sum follows, and every cell keeps three or more values.
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null)], published_total: 65 });
  const b = countyAggregate({ cells: [q1("56001", 50), q1("56005", null), q1("56007", null), q1("56009", null),
    q2("56001", 30)], published_total: 98 });
  assert.equal(conformExpert(b).decision, "conforms");
  assert.equal(jointView(b, a).decision, "within_budget");
  // Found by exhaustive search over five cells: A: V + X + Y = 11 and
  // B: V + X + Z + W = 30. Their difference Z + W - Y = 19 forces Y = 1 and
  // Z = W = 10 once every cell is at most 10.
  const pa = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null), q1("56007", null)],
    published_total: 61 });
  const pb = countyAggregate({ cells: [q1("56001", 50), q1("56003", null), q1("56005", null), q1("56009", null),
    q2("56003", null), q2("56001", 30)], published_total: 110 });
  assert.equal(conformExpert(pa).decision, "conforms");
  assert.equal(conformExpert(pb).decision, "conforms");
  const r = jointView(pb, pa);
  refuses(r, "suppressed_cell_bounded_across_releases");
  assert.equal(r.exposure, "suppressed_cells_pinned_by_residual");
});

// ===========================================================================
// Round 5 (R1, R2, R3): a revision tolerance band. "No solution" is never
// "no exposure": every residual difference within +/- tolerance is checked,
// and a pair no variant explains refuses as inconsistent.
// ===========================================================================

const SIX = pinnedKernel(POPULATION, { county_fips_codes: { status: "pinned",
  codes: ["56001", "56003", "56005", "56007", "56009", "56011"] } });
function sixView(current, prior, kernel = SIX) {
  const c = kernel.evaluatePrivacyRouteConformance({ tenant: ORGANIZATION_TENANT_ID, artifact: artifactOf(current),
    context: CONTEXT, route_receipts: [expert(current)], now: NOW });
  assert.equal(c.decision, "conforms", JSON.stringify(c));
  return kernel.evaluateAggregateOperation(opRequest("view_native_precision",
    { aggregate: current, ledger: emptyPrivacyBudgetLedger(c.ledger_key),
      history: [secondArtifact(prior.cells, { published_total: prior.published_total })] }));
}

test("R1 (attack6): an inconsistent pair is refused, never read as no exposure", () => {
  // A: X + Y = 13. B adds Z with a residual of 13 (R1a), 12 (R1b): the
  // difference 0 or -1 is Z within a revision of 1 or 2, so Z is effectively exact.
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56005", null), q1("56007", null)], published_total: 63 });
  for (const total of [93, 92]) {
    const b = countyAggregate({ cells: [q1("56001", 50), q1("56003", 30), q1("56005", null), q1("56007", null),
      q1("56009", null)], published_total: total });
    assert.equal(conformExpert(b).decision, "conforms");
    const r = jointView(b, a);
    refuses(r, "suppressed_cell_bounded_across_releases");
    assert.equal(r.exposure, "complementary_suppression_missing");
  }
  // R1d: B adds Z and W with a residual of 13 (Z + W = 0 before revision).
  const d = countyAggregate({ cells: [q1("56001", 50), q1("56003", 30), q1("56005", null), q1("56007", null),
    q1("56009", null), q1("56011", null)], published_total: 93 });
  const r = sixView(d, a);
  refuses(r, "suppressed_cell_bounded_across_releases");
  assert.equal(r.exposure, "complementary_suppression_residual_below_floor");
  // Beyond the band: the same pair X, Y summing to 13 in A and 17 in B (B also
  // shows 56003 = 30, so the totals differ by more than the floor) — 4 apart,
  // no variant explains both, and the pair is refused as inconsistent.
  const far = countyAggregate({ cells: [q1("56001", 50), q1("56003", 30), q1("56005", null), q1("56007", null)],
    published_total: 97 });
  assert.equal(conformExpert(far).decision, "conforms");
  const f = jointView(far, a);
  refuses(f, "suppressed_cell_bounded_across_releases");
  assert.equal(f.exposure, "releases_inconsistent_beyond_revision_tolerance");
  // Directly: a pair no variant explains returns the inconsistency, not null.
  const { jointSuppressionExposure } = J302;
  assert.deepEqual(jointSuppressionExposure(far, a, 11, 3, 2),
    { exposure: "releases_inconsistent_beyond_revision_tolerance" });
  // Equal suppressed sets with different sums are inconsistent by the nested
  // check itself, whatever the propagation depth.
  assert.deepEqual(jointSuppressionExposure(far, a, 11, 3, 2, { maxRounds: 1 }),
    { exposure: "releases_inconsistent_beyond_revision_tolerance" });
});

test("R2 (attack6): a revision of +1 cannot lift a refused group over the floor", () => {
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56005", null), q1("56007", null)], published_total: 63 });
  const withGroup = group => countyAggregate({ cells: [q1("56001", 50), q1("56003", 30), q1("56005", null),
    q1("56007", null), q1("56009", null), q1("56011", null)], published_total: 93 + group });
  // True Z + W = 10; a +1 revision shows 11; +2 shows 12; all refuse.
  for (const group of [10, 11, 12]) {
    const r = sixView(withGroup(group), a);
    refuses(r, "suppressed_cell_bounded_across_releases");
    assert.equal(r.exposure, "complementary_suppression_residual_below_floor", String(group));
  }
  // 13 is three past the floor's edge: every variant (11..15) clears.
  assert.equal(sixView(withGroup(13), a).decision, "within_budget");
  // With the tolerance set to 0, 11 clears — the band is what closes R2.
  const exact = pinnedKernel(POPULATION, { revision_tolerance_patients: 0, county_fips_codes: { status: "pinned",
    codes: ["56001", "56003", "56005", "56007", "56009", "56011"] } });
  assert.equal(sixView(withGroup(11), a, exact).decision, "within_budget");
});

test("R2: the revision band also covers a residual after substituting shown cells", () => {
  // Every suppressed cell of A shown by B: a leftover of 1..10 is a small group,
  // and 11 or more is an inconsistency — both refuse, neither is skipped.
  const a = countyAggregate({ cells: [q1("56001", 50), q1("56005", null), q1("56007", null)], published_total: 80 });
  const show = (x, y) => secondArtifact([q1("56005", x), q1("56007", y)]);
  const view = b => op("view_native_precision", { aggregate: a, ledger: ledgerFor(a), history: [b] });
  assert.equal(view(show(15, 15)).decision, "within_budget");
  let r = view(show(14, 15));
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "suppressed_residual_recovered_by_release");
  r = view(show(40, 40));
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "releases_inconsistent_beyond_revision_tolerance");
  // Two cells left of three (residual 45): over two cells, 20 pins both, 19
  // leaves two values, 21 or more clears. A remainder of 22 clears at 0 and
  // at -1, and pins at -2 — only the full band sees it. 23 clears everywhere.
  const three = countyAggregate({ published_total: 110, cells: [q1("56001", 40), q1("56003", 25), q1("56005", null),
    q1("56007", null), q1("56009", null)] });
  const threeView = shown => op("view_native_precision", { aggregate: three, ledger: ledgerFor(three),
    history: [secondArtifact([q1("56005", shown)])] });
  r = threeView(23);
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "suppressed_cells_pinned_by_residual");
  assert.equal(threeView(22).decision, "within_budget");
  // A remainder of 0 over two cells: only +2 has a solution (both cells 1),
  // and that variant is a group below the floor — not an inconsistency.
  r = threeView(45);
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "complementary_suppression_residual_below_floor");
  // The differencing operation runs the same band.
  const diffThree = shown => op("difference_between_artifacts", { aggregate: three, ledger: ledgerFor(three),
    counterpart: secondArtifact([q1("56005", shown)]) });
  r = diffThree(23);
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "suppressed_cells_pinned_by_residual");
  assert.equal(diffThree(22).decision, "within_budget");
  // A remainder no variant can hold is inconsistent.
  r = op("view_native_precision", { aggregate: three, ledger: ledgerFor(three),
    history: [secondArtifact([q1("56005", 48)])] });
  refuses(r, "suppressed_residual_recovered_by_release");
  assert.equal(r.exposure, "releases_inconsistent_beyond_revision_tolerance");
});

test("R2: the revision tolerance is config, defaulted to 2, and never negative", () => {
  assert.equal(V5_J302_PRIVACY_CONFIG.revision_tolerance_patients, 2);
  throwsCode(() => bindHeatMapPrivacyKernel({ ...V5_J302_PRIVACY_CONFIG, revision_tolerance_patients: -1 }),
    "invalid_shape");
  throwsCode(() => bindHeatMapPrivacyKernel({ ...V5_J302_PRIVACY_CONFIG, revision_tolerance_patients: 11 }),
    "invalid_shape");
  const { revision_tolerance_patients: _drop, ...missing } = V5_J302_PRIVACY_CONFIG;
  throwsCode(() => bindHeatMapPrivacyKernel(missing), "missing_field");
});

// A seeded corpus of release pairs, some with a per-cell revision in B.
function driftCorpus(count, seed) {
  let rng = seed;
  const rand = n => { rng = (rng * 1103515245 + 12345) & 0x7fffffff; return rng % n; };
  const pairs = [];
  while (pairs.length < count) {
    const n = 2 + rand(4);
    const truth = Array.from({ length: n }, () => (rand(6) === 0 ? 11 + rand(15) : 1 + rand(10)));
    const supA = truth.map(() => rand(2) === 0), supB = truth.map(() => rand(2) === 0);
    const drift = rand(3) === 0 ? truth.map(() => rand(3) - 1) : truth.map(() => 0);
    const mk = (sup, dr) => ({ cells: truth.map((t, i) => ({ unit_id: `U${i}`, period: null,
      patient_count: sup[i] ? null : Math.max(1, t + dr[i]), suppressed: sup[i] })),
      published_total: truth.reduce((sum, t, i) => sum + Math.max(1, t + dr[i]), 0) });
    pairs.push([mk(supA, truth.map(() => 0)), mk(supB, drift)]);
  }
  return pairs;
}

test("R3: the round cap never changes a refuse-or-pass answer (differential)", () => {
  const { jointSuppressionExposure } = J302;
  let refusedByFull = 0;
  for (const [a, b] of driftCorpus(4000, 99)) {
    const full = Boolean(jointSuppressionExposure(a, b, 11, 3, 2));
    if (full) refusedByFull++;
    for (const maxRounds of [1, 2]) {
      assert.equal(Boolean(jointSuppressionExposure(a, b, 11, 3, 2, { maxRounds })), full,
        JSON.stringify({ a, b, maxRounds }));
    }
  }
  assert.ok(refusedByFull > 0, "the corpus exercises refusals");
});

test("R1-R3: a drift-aware exhaustive adversary finds nothing the kernel lets through", () => {
  // For every variant |da| + |db| <= 2, enumerate every integer assignment of
  // the still-suppressed cells; an exposure in ANY variant (a pinned cell, a
  // cell confined below the floor to under 3 values, a nested group below the
  // floor), or no consistent variant at all, must be refused.
  const { jointSuppressionExposure, suppressionExposure } = J302;
  const T = 2, FLOOR = 11, MINV = 3, TOP = 10;
  const key = c => `${c.unit_id}\u0000${c.period ?? ""}`;
  const equation = (x, y) => {
    const yi = new Map(y.cells.map(c => [key(c), c]));
    const vars = []; let total = x.published_total;
    for (const c of x.cells) {
      if (!c.suppressed) { total -= c.patient_count; continue; }
      const o = yi.get(key(c));
      if (o && !o.suppressed) total -= o.patient_count; else vars.push(key(c));
    }
    return { vars, total };
  };
  const valueSets = (ea, eb, cap) => {
    const vars = [...new Set([...ea.vars, ...eb.vars])];
    const sets = vars.map(() => new Set()); let any = false; const assign = [];
    const rec = (j, sa, sb) => {
      if (sa > ea.total || sb > eb.total) return;
      if (j === vars.length) {
        if (sa === ea.total && sb === eb.total) { any = true; assign.forEach((v, q) => sets[q].add(v)); }
        return;
      }
      for (let v = 1; v <= cap; v++) {
        assign[j] = v;
        rec(j + 1, sa + (ea.vars.includes(vars[j]) ? v : 0), sb + (eb.vars.includes(vars[j]) ? v : 0));
      }
    };
    rec(0, 0, 0);
    return { any, sets };
  };
  const own = x => {
    const k = x.cells.filter(c => c.suppressed).length;
    const r = x.published_total - x.cells.reduce((sum, c) => sum + (c.suppressed ? 0 : c.patient_count), 0);
    return k === 0 ? null : suppressionExposure(k, r, FLOOR, MINV);
  };
  let judged = 0, exposures = 0;
  for (const [a, b] of driftCorpus(1500, 7)) {
    if (own(a) || own(b)) continue;
    const ea = equation(a, b), eb = equation(b, a);
    if (!ea.vars.length || !eb.vars.length) continue;
    judged++;
    let consistent = false, exposed = false;
    for (let da = -T; da <= T; da++) for (let db = -T; db <= T; db++) {
      if (Math.abs(da) + Math.abs(db) > T) continue;
      const va = { vars: ea.vars, total: ea.total + da }, vb = { vars: eb.vars, total: eb.total + db };
      const pos = valueSets(va, vb, Math.max(va.total, vb.total, 1));
      if (!pos.any) continue;
      consistent = true;
      for (const [sm, lg] of [[va, vb], [vb, va]]) {
        if (!sm.vars.every(v => lg.vars.includes(v))) continue;
        const extra = lg.vars.filter(v => !sm.vars.includes(v));
        if (extra.length && suppressionExposure(extra.length, lg.total - sm.total, FLOOR, MINV)) exposed = true;
      }
      for (const set of pos.sets) {
        if (set.size === 1 || (Math.max(...set) <= TOP && set.size < MINV)) exposed = true;
      }
      const pri = valueSets(va, vb, TOP);
      if (pri.any && pri.sets.some(set => set.size < MINV)) exposed = true;
    }
    const kernel = jointSuppressionExposure(a, b, FLOOR, MINV, T);
    if (exposed || !consistent) {
      exposures++;
      assert.ok(kernel, JSON.stringify({ a, b, consistent }));
    }
  }
  assert.ok(judged > 500 && exposures > 0, JSON.stringify({ judged, exposures }));
});

test("R1: jointSuppressionExposure's positivity bounds, called directly (non-nested sets)", () => {
  // Through the operations these cases are already refused by each release's
  // own residual rule and by the banded step-2 check; the exported function
  // keeps its own contract as defense in depth.
  const { jointSuppressionExposure } = J302;
  const cell = (u, c) => ({ unit_id: u, period: null, patient_count: c, suppressed: c === null });
  const pair = (totalA, totalB) => [
    { cells: [cell("U0", 50), cell("X", null), cell("Y", null)], published_total: 50 + totalA },
    { cells: [cell("U0", 50), cell("Y", null), cell("Z", null)], published_total: 50 + totalB }];
  // X + Y = 2 and Y + Z = 30: X and Y are exactly 1; the all-primary system has no solution.
  assert.equal(jointSuppressionExposure(...pair(2, 30), 11, 3, 0).exposure, "suppressed_cell_recovered_exactly");
  // X + Y = 3: X and Y in {1, 2}, below the floor with two values.
  assert.equal(jointSuppressionExposure(...pair(3, 30), 11, 3, 0).exposure, "suppressed_cells_interval_too_narrow");
  // X + Y = -1: no positive solution in any variant within +/-2: inconsistent, never a pass.
  assert.deepEqual(jointSuppressionExposure(...pair(-1, 30), 11, 3, 2),
    { exposure: "releases_inconsistent_beyond_revision_tolerance" });
  // Wide on both sides: nothing to refuse.
  assert.equal(jointSuppressionExposure(...pair(40, 30), 11, 3, 2), null);
});
