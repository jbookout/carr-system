// DoctorCRE v5 slice V5-J302 — aggregate heat-map privacy admission.
//
// Three settled decisions govern this file, quoted verbatim below:
//
//   Q033.D2  Journey 3 may admit only independently approved aggregate patient
//            heat-map artifacts and derived strategy context under scoped
//            privacy, provenance, access and anti-reidentification controls.
//   Q048.D1  Admit only approved aggregate patient-location maps with provenance
//            and access controls; preserve source privacy thresholds and block
//            raw rows, reverse geocoding and reidentifying combinations.
//   Q071.D2  Capture only an independently approved aggregate heat-map artifact
//            with immutable source provenance; any derived strategy fact stays a
//            reviewable proposal; the raw or reversible patient-level source is
//            prohibited.
//
// WHERE THIS SITS. V5-S01 (global-boundaries.v5.js) answers an aggregate heat
// map with `needs_independent_privacy_route` rather than the PHI refusal, and
// V5-F01 (record-source-authority.v5.js) carries that answer through its
// corporate-artifact admission. This module is the route those two name. It
// imports both and reimplements neither: every heat-map artifact is first put
// through F01's own admission, and only an artifact F01 ROUTES here is read at
// all. A PHI or raw-location class is refused by S01 before this module looks at
// a single cell, and an ordinary permitted artifact is refused here as not a
// heat map rather than quietly admitted by a second door.
//
// ONE ROUTE, EXACTLY ONE. A privacy route is either HIPAA Safe Harbor
// (45 CFR 164.514(b)(2)) or Expert Determination (45 CFR 164.514(b)(1)). The
// request carries a list of route receipts and the list must hold exactly one:
// none, two, or one of each refuses, because two routes that disagree about a
// cell cannot both be the one that governs it.
//
// WHAT "INDEPENDENT" MEANS HERE, AND WHAT IT CANNOT MEAN. This module checks what
// a receipt SAYS about its independence: the producer role is the independent
// heat-map privacy oracle, the issuer is not a model, and neither the issuer nor
// the expert is the requesting actor or the recipient. It cannot check that the
// receipt was really issued by that oracle — there is no authenticated retrieval
// of independently issued receipts in this repository. So the conformance
// evaluator's best answer is `conforms`, never an admission, and the intake path
// takes NO caller receipt at all: its closed schema has no field to put one in,
// and it refuses naming the retrieval seam it is owed. Building this verifier,
// or merging it, never satisfies
// step:journey-three-aggregate-heat-map-privacy-route-receipt.
//
// THE THRESHOLDS ARE NOT INVENTED HERE. The only numbers this file owns are the
// ones the slice contract states: the 20,000 Census-derived population floor for
// a three-digit ZIP under Safe Harbor, and HHS's published list of ZIP3 prefixes
// that fall below it. Every small-cell threshold is BOUND FROM EVIDENCE: the
// source's own declared threshold (Q048.D1: preserve source privacy thresholds),
// which the route receipt must acknowledge, and under Expert Determination the
// expert's numeric minimum. The effective floor is the stricter of the two. No
// platform-wide floor exists in this file, and none should be added without a
// Joe decision.
//
// BUDGETS ARE A KERNEL CONTRACT, NOT A STORE. Expert Determination binds
// repeated-query, differencing and export budgets. This module computes the next
// ledger state for one operation and names the compare-and-swap precondition the
// persistence owner must enforce atomically; it holds no ledger and writes
// nothing. The store that makes consumption atomic is owed at
// V5_J302_BUDGET_LEDGER_STORE_SEAM.
//
// PURE. No filesystem, network, database, provider, scheduler, environment or
// clock: `now` is always the caller's. V5_NO_EFFECTS rides on every result.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES,
  V5_NO_EFFECTS,
} from "./global-boundaries.v5.js";
import { admitCorporateArtifact } from "./record-source-authority.v5.js";
import {
  V5_J301_MAP_CONTRACT,
  V5_J301_MAP_CONTRACT_GATE,
  V5_J301_MAP_CONTRACT_VERSION,
} from "./tour-workflow-j301.v5.js";

export { V5_NO_EFFECTS };

export const V5_J302_SCHEMA_VERSION = "doctorcre-v5-j302-heat-map-privacy.v1";
export const V5_J302_POLICY_VERSION = 1;
export const V5_J302_BINDING_SCHEMA_VERSION = "doctorcre-v5-j302-dataset-recipient-environment.v1";
export const V5_J302_DESCRIPTOR_SCHEMA_VERSION = "doctorcre-v5-j302-aggregate-descriptor.v1";
export const V5_J302_POPULATION_TABLE_SCHEMA_VERSION = "doctorcre-v5-j302-zip3-population.v1";
export const V5_J302_LEDGER_SCHEMA_VERSION = "doctorcre-v5-j302-privacy-budget-ledger.v1";

// ---------------------------------------------------------------------------
// Local primitives. Deliberately this module's own, as in the sibling slices:
// a shared assertion library is a place one module's floor can be lowered by
// editing another's.
// ---------------------------------------------------------------------------

const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

export class V5J302PrivacyError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J302PrivacyError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J302PrivacyError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key === "symbol") fail("invalid_shape", `${path} carries a symbol key`, { path });
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor.get || descriptor.set) {
      fail("invalid_shape", `${path}.${key} is an accessor; only data properties are read`, { path });
    }
  }
  return value;
}

function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object) || object[key] === undefined) {
      fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
  }
}

function assertArray(value, path, { min = 0, max = 10000 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min || value.length > max) {
    fail("invalid_shape", `${path} must hold between ${min} and ${max} items`, { path });
  }
  return value;
}

function assertIdent(value, path) {
  if (typeof value !== "string" || !EXTERNAL_IDENT.test(value)) {
    fail("invalid_identifier", `${path} must be an external identifier`, { path });
  }
  return value;
}

function assertText(value, path, { maxLength = 2000 } = {}) {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength ||
      UNSAFE_TEXT.test(value)) {
    fail("invalid_shape", `${path} must be non-empty safe text of at most ${maxLength} characters`,
      { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !DIGEST_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference over 64 lower-case hex characters`,
      { path });
  }
  return value;
}

function assertInteger(value, path, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    fail("invalid_shape", `${path} must be an integer between ${min} and ${max}`, { path });
  }
  return value;
}

function assertBoolean(value, path) {
  if (typeof value !== "boolean") fail("invalid_shape", `${path} must be a boolean`, { path });
  return value;
}

function assertEnum(value, allowed, path, code = "unknown_value") {
  if (typeof value !== "string" || !allowed.includes(value)) {
    fail(code, `${path} must be one of ${allowed.join(", ")}`, { path, allowed: [...allowed] });
  }
  return value;
}

function daysInMonth(year, month) {
  return [31, (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28,
    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
}

/** Calendar-impossible instants throw; they are never normalized into another. */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path });
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || Number(hour) > 23 ||
      Number(minute) > 59 || Number(second) > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp", `${path} names an instant that does not exist on the calendar`, { path });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path });
  return parsed;
}

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("foreign_tenant", `${path} must be the organization tenant`, { path });
  }
}

/** A validated, frozen, accessor-free copy, so a later mutation reaches nothing. */
function snapshot(value) {
  return deepFreeze(JSON.parse(JSON.stringify(value)));
}

// ---------------------------------------------------------------------------
// The settled decisions, copied verbatim from the reviewed design basis
// (doctorcre-v5-design-basis, normalized r7, artifact sha256 4379c60e…d5e6d7).
// They are identity, not configuration.
// ---------------------------------------------------------------------------

export const V5_J302_SETTLED_DECISIONS = deepFreeze({
  "Q033.D2": {
    settled_requirement: "Journey 3 may admit only independently approved aggregate patient heat-map artifacts and derived strategy context under scoped privacy, provenance, access, and anti-reidentification controls.",
    source_evidence_digest: "1aa74e892a8bb348cccbf2f34e456fe31d0f895e75d4da7ec7f84b9f174961fb",
  },
  "Q048.D1": {
    settled_requirement: "Admit only approved aggregate patient-location maps with provenance and access controls; preserve source privacy thresholds and block raw rows, reverse geocoding, and reidentifying combinations.",
    source_evidence_digest: "32be383808cc59f2a3ea20f269de1dd07e1889ea2338b0f73013283c7a47b95d",
  },
  "Q071.D2": {
    settled_requirement: "Journey 3 may capture only an independently approved aggregate heat-map artifact with immutable source provenance; any derived strategy fact remains a reviewable proposal and the raw or reversible patient-level source is prohibited.",
    source_evidence_digest: "0011d2df6d9be7ec44948eec44a93399d25c9d10e0f101f554027d17ebc81097",
  },
});
export const V5_J302_SETTLED_DECISION_IDS = deepFreeze(Object.keys(V5_J302_SETTLED_DECISIONS).sort());

// ---------------------------------------------------------------------------
// The route vocabulary and the evidence it is owed.
// ---------------------------------------------------------------------------

export const V5_J302_ROUTES = deepFreeze(["expert_determination", "safe_harbor"]);
export const V5_J302_PRODUCER_ROLE = "independent_heat_map_privacy_oracle";
export const V5_J302_ORACLE_REF = "oracle:gate-producer:aggregate-heat-map-route";
export const V5_J302_ORACLE_VERSION = "1.0.0";
export const V5_J302_ROUTE_GATE = "journey-three-aggregate-heat-map-route-accepted";
export const V5_J302_ROUTE_RECEIPT_STEP = "step:journey-three-aggregate-heat-map-privacy-route-receipt";

/** The slice's runtime and acceptance inputs. None is produced or satisfied here. */
export const V5_J302_REQUIRED_RUNTIME_EVIDENCE = deepFreeze([
  "step:global-no-phi-boundary-independent-receipt",
  "step:journey-three-aggregate-heat-map-privacy-route-receipt",
  "step:journey-three-contract-binding-receipt",
  "step:journey-two-production-outcome",
]);

/** Owed seams, named so a refusal points at the missing owner rather than at "no". */
export const V5_J302_RECEIPT_RETRIEVAL_SEAM =
  "seam:v5-j302:authenticated-independent-privacy-route-receipt-retrieval";
export const V5_J302_BUDGET_LEDGER_STORE_SEAM =
  "seam:v5-j302:atomic-privacy-budget-ledger-store";
export const V5_J302_PROPOSAL_REVIEW_SEAM =
  "seam:v5-j302:human-review-of-derived-strategy-proposal";

export const V5_J302_ISSUER_KINDS = deepFreeze(["human", "model", "organization"]);
export const V5_J302_ENVIRONMENTS = deepFreeze(["production", "staging"]);

// ---------------------------------------------------------------------------
// HHS Safe Harbor, 45 CFR 164.514(b)(2)(i)(A)-(R): the eighteen identifier
// categories, every one of which a Safe Harbor receipt must state removed.
// ---------------------------------------------------------------------------

export const V5_J302_HHS_SAFE_HARBOR_RULE_REF = "45 CFR 164.514(b)(2)";
export const V5_J302_HHS_IDENTIFIER_CATEGORIES = deepFreeze([
  "account_numbers",
  "biometric_identifiers",
  "certificate_license_numbers",
  "dates_except_year_and_ages_over_89",
  "device_identifiers",
  "email_addresses",
  "fax_numbers",
  "full_face_photographs",
  "geographic_subdivisions_smaller_than_state",
  "health_plan_beneficiary_numbers",
  "ip_addresses",
  "medical_record_numbers",
  "names",
  "other_unique_identifying_numbers",
  "social_security_numbers",
  "telephone_numbers",
  "urls",
  "vehicle_identifiers",
]);

/**
 * The slice contract's own number: a three-digit ZIP survives only where the
 * Census-derived population EXCEEDS 20,000; otherwise it is written as 000.
 */
export const V5_J302_SAFE_HARBOR_ZIP3_POPULATION_FLOOR = 20000;
export const V5_J302_SAFE_HARBOR_SUPPRESSED_ZIP3 = "000";

/**
 * HHS's published list of three-digit ZIP prefixes whose Census population is
 * 20,000 or fewer (HHS de-identification guidance, 2000 Census). Held as a floor
 * beneath the receipt's own population table: a receipt that claims a larger
 * population for one of these cannot lift it, and an unlisted prefix is still
 * judged against the receipt's table. The floor only ever refuses.
 */
export const V5_J302_HHS_RESTRICTED_ZIP3 = deepFreeze([
  "036", "059", "063", "102", "203", "556", "692", "790", "821",
  "823", "830", "831", "878", "879", "884", "890", "893",
]);

// ---------------------------------------------------------------------------
// The aggregate descriptor vocabulary. Coarseness rises with rank.
// ---------------------------------------------------------------------------

export const V5_J302_SPATIAL_UNIT_RANK = deepFreeze({
  census_block_group: 1, census_tract: 2, zip5: 3, county: 4, zip3: 5, state: 6,
});
export const V5_J302_SPATIAL_UNITS = deepFreeze(Object.keys(V5_J302_SPATIAL_UNIT_RANK).sort());
/** Point-grade locations. Always refused: the contract excludes raw points and addresses. */
export const V5_J302_RAW_LOCATION_UNITS = deepFreeze([
  "address", "geocode", "lat_lng", "parcel", "point", "street_segment",
]);
export const V5_J302_SAFE_HARBOR_SPATIAL_UNITS = deepFreeze(["state", "zip3"]);

export const V5_J302_TEMPORAL_RANK = deepFreeze({
  day: 1, week: 2, month: 3, quarter: 4, year: 5, none: 6,
});
export const V5_J302_TEMPORAL_PRECISIONS = deepFreeze(Object.keys(V5_J302_TEMPORAL_RANK).sort());
export const V5_J302_SAFE_HARBOR_TEMPORAL_PRECISIONS = deepFreeze(["none", "year"]);

export const V5_J302_RECORD_GRAINS = deepFreeze([
  "aggregate_cell", "encounter_row", "patient_row",
]);
export const V5_J302_REVERSIBILITY = deepFreeze([
  "encrypted_rows", "hashed_identifiers", "irreversible_aggregate", "pseudonymized_rows",
]);

/** The only columns an aggregate artifact may carry. Anything else denies. */
export const V5_J302_PERMITTED_COLUMNS = deepFreeze([
  "patient_count", "period", "suppressed", "unit_id",
]);
/** Column names refused BY NAME, so the refusal says what was attempted. */
export const V5_J302_IDENTIFIER_COLUMNS = deepFreeze([
  "account_number", "address", "admission_date", "biometric", "birth_date", "city",
  "date_of_service", "device_id", "discharge_date", "dob", "email", "fax", "geocode",
  "health_plan_id", "ip_address", "lat", "latitude", "license_number", "lng", "lon",
  "longitude", "medical_record_number", "mrn", "name", "patient_id", "patient_name",
  "phone", "photo", "ssn", "street", "url", "vehicle_id", "zip", "zip5", "zip_code",
]);

const UNIT_ID_FORMAT = deepFreeze({
  state: /^[A-Z]{2}$/,
  zip3: /^\d{3}$/,
  county: /^\d{5}$/,
  zip5: /^\d{5}$/,
  census_tract: /^\d{11}$/,
  census_block_group: /^\d{12}$/,
});
const PERIOD_FORMAT = deepFreeze({
  year: /^\d{4}$/,
  quarter: /^\d{4}-Q[1-4]$/,
  month: /^\d{4}-(0[1-9]|1[0-2])$/,
  week: /^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$/,
  day: /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/,
});

// ---------------------------------------------------------------------------
// Operations over an artifact. Named refusals first: each is a way to walk back
// from an aggregate toward a person (Q048.D1).
// ---------------------------------------------------------------------------

export const V5_J302_BUDGET_CLASSES = deepFreeze(["differencing", "export", "query"]);
export const V5_J302_OPERATIONS = deepFreeze({
  view_native_precision: "query",
  rank_units: "query",
  overlay_properties_at_native_precision: "query",
  difference_between_artifacts: "differencing",
  export_sealed_artifact: "export",
});
export const V5_J302_OPERATION_KINDS = deepFreeze(Object.keys(V5_J302_OPERATIONS).sort());
export const V5_J302_REIDENTIFYING_OPERATIONS = deepFreeze([
  "count_within_radius",
  "difference_overlapping_cells",
  "disaggregate_below_native_unit",
  "impute_suppressed_cell",
  "link_to_patient_level_source",
  "reverse_geocode",
]);

export const V5_J302_PROPOSAL_KINDS = deepFreeze([
  "market_gap_context", "relocation_context", "site_selection_context",
]);
export const V5_J302_PROPOSER_KINDS = deepFreeze(["human", "model"]);

// ---------------------------------------------------------------------------
// Result helpers.
// ---------------------------------------------------------------------------

function outcome(fields) {
  return deepFreeze({
    ...fields,
    // Stated on every result, including the best one this module can give.
    admission: "unavailable",
    independent_issuance: "not_established_here",
    route_receipt_step: V5_J302_ROUTE_RECEIPT_STEP,
    effects: V5_NO_EFFECTS,
  });
}

function refusal(reason_id, base, extra = {}) {
  return outcome({ decision: "refuse", reason_id, ...base, ...extra });
}

// ---------------------------------------------------------------------------
// The artifact: F01 identity first, then the aggregate descriptor.
// ---------------------------------------------------------------------------

const ARTIFACT_KEYS = Object.freeze(["corporate_artifact", "aggregate", "dataset_digest"]);
const AGGREGATE_KEYS = Object.freeze([
  "record_grain", "reversibility", "geography_unit", "temporal_precision",
  "columns", "cells", "published_total", "source_privacy_threshold",
]);
const THRESHOLD_KEYS = Object.freeze(["minimum_cell_count", "declared_by"]);
const CELL_KEYS = Object.freeze(["unit_id", "period", "patient_count", "suppressed"]);
const CONTEXT_KEYS = Object.freeze(["recipient_id", "environment", "requesting_actor", "processors"]);

/** Shape only. Sensitivity judgements are policy answers, made in judgeAggregate. */
function readAggregate(raw, path) {
  assertObject(raw, path);
  assertClosedKeys(raw, AGGREGATE_KEYS, path);
  assertRequiredKeys(raw, AGGREGATE_KEYS.filter(k => k !== "published_total"), path);
  const threshold = assertObject(raw.source_privacy_threshold, `${path}.source_privacy_threshold`);
  assertClosedKeys(threshold, THRESHOLD_KEYS, `${path}.source_privacy_threshold`);
  assertRequiredKeys(threshold, THRESHOLD_KEYS, `${path}.source_privacy_threshold`);
  const columns = assertArray(raw.columns, `${path}.columns`, { min: 1, max: 32 })
    .map((c, i) => assertIdent(c, `${path}.columns[${i}]`));
  if (new Set(columns).size !== columns.length) {
    fail("duplicate_column", `${path}.columns repeats a column`, { path: `${path}.columns` });
  }
  const cells = assertArray(raw.cells, `${path}.cells`, { min: 1, max: 5000 }).map((cell, i) => {
    const p = `${path}.cells[${i}]`;
    assertObject(cell, p);
    assertClosedKeys(cell, CELL_KEYS, p);
    assertRequiredKeys(cell, ["unit_id", "patient_count", "suppressed"], p);
    const suppressed = assertBoolean(cell.suppressed, `${p}.suppressed`);
    if (suppressed && cell.patient_count !== null) {
      fail("suppressed_cell_carries_count",
        `${p} is suppressed and must carry patient_count null; a suppressed value is never read`,
        { path: p });
    }
    if (!suppressed) assertInteger(cell.patient_count, `${p}.patient_count`);
    return {
      unit_id: assertText(cell.unit_id, `${p}.unit_id`, { maxLength: 64 }),
      period: cell.period === undefined || cell.period === null
        ? null : assertText(cell.period, `${p}.period`, { maxLength: 32 }),
      patient_count: suppressed ? null : cell.patient_count,
      suppressed,
    };
  });
  return {
    record_grain: assertText(raw.record_grain, `${path}.record_grain`, { maxLength: 64 }),
    reversibility: assertText(raw.reversibility, `${path}.reversibility`, { maxLength: 64 }),
    geography_unit: assertText(raw.geography_unit, `${path}.geography_unit`, { maxLength: 64 }),
    temporal_precision: assertText(raw.temporal_precision, `${path}.temporal_precision`, { maxLength: 32 }),
    columns: [...columns].sort(),
    cells,
    published_total: raw.published_total === undefined || raw.published_total === null
      ? null : assertInteger(raw.published_total, `${path}.published_total`),
    source_privacy_threshold: {
      minimum_cell_count: assertInteger(threshold.minimum_cell_count,
        `${path}.source_privacy_threshold.minimum_cell_count`, { min: 1 }),
      declared_by: assertIdent(threshold.declared_by, `${path}.source_privacy_threshold.declared_by`),
    },
  };
}

function readContext(raw, path) {
  assertObject(raw, path);
  assertClosedKeys(raw, CONTEXT_KEYS, path);
  assertRequiredKeys(raw, CONTEXT_KEYS, path);
  const processors = assertArray(raw.processors, `${path}.processors`, { min: 0, max: 16 })
    .map((p, i) => assertIdent(p, `${path}.processors[${i}]`));
  if (new Set(processors).size !== processors.length) {
    fail("duplicate_processor", `${path}.processors repeats a processor`, { path });
  }
  return {
    recipient_id: assertIdent(raw.recipient_id, `${path}.recipient_id`),
    environment: assertEnum(raw.environment, V5_J302_ENVIRONMENTS, `${path}.environment`,
      "unknown_environment"),
    // DECLARED, never trusted: it is read only to refuse an issuer or expert who
    // names themselves, and no answer is ever granted because of it.
    requesting_actor: assertIdent(raw.requesting_actor, `${path}.requesting_actor`),
    processors: [...processors].sort(),
  };
}

/** The descriptor preimage: what an independent determination must have looked at. */
function descriptorPreimage(aggregate) {
  return {
    schema_version: V5_J302_DESCRIPTOR_SCHEMA_VERSION,
    record_grain: aggregate.record_grain,
    reversibility: aggregate.reversibility,
    geography_unit: aggregate.geography_unit,
    temporal_precision: aggregate.temporal_precision,
    columns: [...aggregate.columns],
    cells: aggregate.cells.map(c => ({ ...c })),
    published_total: aggregate.published_total,
    source_privacy_threshold: { ...aggregate.source_privacy_threshold },
  };
}

/** The digest an aggregate descriptor must be bound under. Exported so issuers bind the same bytes. */
export function aggregateDescriptorDigest(aggregate) {
  return digest(descriptorPreimage(readAggregate(aggregate, "aggregate")));
}

/** The dataset × recipient × environment binding an independent determination names. */
export function datasetRecipientEnvironmentDigest({ dataset_digest, recipient_id, environment }) {
  return digest({
    schema_version: V5_J302_BINDING_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    dataset_digest: assertDigestRef(dataset_digest, "binding.dataset_digest"),
    recipient_id: assertIdent(recipient_id, "binding.recipient_id"),
    environment: assertEnum(environment, V5_J302_ENVIRONMENTS, "binding.environment",
      "unknown_environment"),
  });
}

/** The digest of a ZIP3 population table, so a receipt's table cannot be edited under its digest. */
export function zip3PopulationTableDigest(table) {
  assertObject(table, "zip3_population");
  const entries = Object.keys(table).sort().map(zip3 => {
    if (!/^\d{3}$/.test(zip3)) fail("invalid_zip3", `zip3_population key "${zip3}" is not three digits`);
    return [zip3, assertInteger(table[zip3], `zip3_population.${zip3}`)];
  });
  return digest({ schema_version: V5_J302_POPULATION_TABLE_SCHEMA_VERSION, entries });
}

/**
 * Judge the artifact on its own: F01 routing, provenance, raw and reversible
 * forms, geography, columns, cell formats, the source threshold and
 * complementary suppression. Returns either a refusal or the facts later
 * stages need.
 */
function judgeArtifact(rawArtifact, context, now, base, priorRaw) {
  assertObject(rawArtifact, "request.artifact");
  assertClosedKeys(rawArtifact, ARTIFACT_KEYS, "request.artifact");
  assertRequiredKeys(rawArtifact, ARTIFACT_KEYS, "request.artifact");
  const dataset_digest = assertDigestRef(rawArtifact.dataset_digest, "request.artifact.dataset_digest");
  const aggregate = readAggregate(rawArtifact.aggregate, "request.artifact.aggregate");

  // F01 first. It validates the complete corporate identity (throwing on an
  // unreadable one) and asks S01 about the declared classes. Only its routing
  // answer lets the artifact through to this module.
  const f01 = admitCorporateArtifact({
    tenant: ORGANIZATION_TENANT_ID, artifact: rawArtifact.corporate_artifact, now: new Date(now).toISOString(),
  });
  if (f01.decision === "refuse") {
    return { refusal: refusal(f01.reason_id, base, { f01_decision: "refuse" }) };
  }
  if (f01.decision !== "needs_independent_privacy_route") {
    return { refusal: refusal("artifact_not_routed_to_heat_map_privacy", base,
      { f01_decision: f01.decision }) };
  }
  const corporate = rawArtifact.corporate_artifact;
  const routedOnly = corporate.declared_data_classes
    .every(c => V5_INDEPENDENT_PRIVACY_ROUTE_CLASSES.includes(c));
  if (!routedOnly) {
    // A heat map bundled with ordinary business data would carry that data in
    // under the privacy route's cover. It is two artifacts, not one.
    return { refusal: refusal("heat_map_artifact_mixed_with_other_classes", base) };
  }

  // F01 returns its routing answer before its time and immutability checks, so
  // this module owns both for the artifacts it is routed.
  if (assertInstant(corporate.observed_at, "request.artifact.corporate_artifact.observed_at") > now) {
    return { refusal: refusal("observation_after_now", base) };
  }
  if (priorRaw !== undefined && priorRaw !== null) {
    assertObject(priorRaw, "request.prior_artifact");
    const prior = admitCorporateArtifact({
      tenant: ORGANIZATION_TENANT_ID, artifact: priorRaw, now: new Date(now).toISOString(),
    });
    if (prior.decision !== "needs_independent_privacy_route") {
      fail("invalid_prior_artifact", "request.prior_artifact must itself be a routed heat-map artifact",
        { path: "request.prior_artifact" });
    }
    const same =
      priorRaw.source_system === corporate.source_system &&
      priorRaw.source_account === corporate.source_account &&
      priorRaw.native_identity.native_id === corporate.native_identity.native_id &&
      priorRaw.native_identity.native_id_epoch === corporate.native_identity.native_id_epoch &&
      priorRaw.native_version === corporate.native_version;
    if (same && priorRaw.content_digest !== corporate.content_digest) {
      return { refusal: refusal("artifact_identity_conflict", base, {
        established_content_digest: priorRaw.content_digest,
        observed_content_digest: corporate.content_digest,
      }) };
    }
  }

  // Raw and reversible sources: excluded scope, refused by name.
  if (!V5_J302_RECORD_GRAINS.includes(aggregate.record_grain)) {
    return { refusal: refusal("unknown_record_grain_denied", base) };
  }
  if (aggregate.record_grain !== "aggregate_cell") {
    return { refusal: refusal("raw_rows_refused", base, { record_grain: aggregate.record_grain }) };
  }
  if (!V5_J302_REVERSIBILITY.includes(aggregate.reversibility)) {
    return { refusal: refusal("unknown_reversibility_denied", base) };
  }
  if (aggregate.reversibility !== "irreversible_aggregate") {
    return { refusal: refusal("reversible_source_refused", base, { reversibility: aggregate.reversibility }) };
  }
  if (V5_J302_RAW_LOCATION_UNITS.includes(aggregate.geography_unit)) {
    return { refusal: refusal("raw_location_refused", base, { geography_unit: aggregate.geography_unit }) };
  }
  if (!V5_J302_SPATIAL_UNITS.includes(aggregate.geography_unit)) {
    return { refusal: refusal("unknown_geography_unit_denied", base) };
  }
  if (!V5_J302_TEMPORAL_PRECISIONS.includes(aggregate.temporal_precision)) {
    return { refusal: refusal("unknown_temporal_precision_denied", base) };
  }
  const identifierColumns = aggregate.columns.filter(c => V5_J302_IDENTIFIER_COLUMNS.includes(c));
  if (identifierColumns.length > 0) {
    return { refusal: refusal("identifier_column_refused", base, { columns: identifierColumns }) };
  }
  const unknownColumns = aggregate.columns.filter(c => !V5_J302_PERMITTED_COLUMNS.includes(c));
  if (unknownColumns.length > 0) {
    return { refusal: refusal("unknown_column_denied", base, { columns: unknownColumns }) };
  }

  // Cell formats follow the declared geography and period, so a zip5 cannot
  // hide inside a descriptor that says zip3.
  const unitFormat = UNIT_ID_FORMAT[aggregate.geography_unit];
  const keys = new Set();
  for (const [i, cell] of aggregate.cells.entries()) {
    const zip3Suppressed = aggregate.geography_unit === "zip3" &&
      cell.unit_id === V5_J302_SAFE_HARBOR_SUPPRESSED_ZIP3;
    if (!zip3Suppressed && !unitFormat.test(cell.unit_id)) {
      return { refusal: refusal("cell_finer_than_declared_geography", base, { cell_index: i }) };
    }
    if (aggregate.temporal_precision === "none") {
      if (cell.period !== null) {
        return { refusal: refusal("cell_period_finer_than_declared", base, { cell_index: i }) };
      }
    } else if (cell.period === null || !PERIOD_FORMAT[aggregate.temporal_precision].test(cell.period)) {
      return { refusal: refusal("cell_period_finer_than_declared", base, { cell_index: i }) };
    }
    const key = `${cell.unit_id}\u0000${cell.period ?? ""}`;
    if (keys.has(key)) fail("duplicate_cell", `request.artifact.aggregate.cells[${i}] repeats a cell`);
    keys.add(key);
  }

  // Complementary suppression. With a published total, one suppressed cell is
  // the total minus everything visible: suppression that can be subtracted back
  // is not suppression.
  const suppressed = aggregate.cells.filter(c => c.suppressed).length;
  const visibleSum = aggregate.cells.reduce((s, c) => s + (c.suppressed ? 0 : c.patient_count), 0);
  if (aggregate.published_total !== null) {
    const residual = aggregate.published_total - visibleSum;
    if (residual < 0 || (suppressed === 0 && residual !== 0)) {
      return { refusal: refusal("published_total_inconsistent", base) };
    }
    if (suppressed === 1) {
      return { refusal: refusal("complementary_suppression_missing", base) };
    }
  }

  const descriptor_digest = digest(descriptorPreimage(aggregate));
  const binding_digest = datasetRecipientEnvironmentDigest({
    dataset_digest, recipient_id: context.recipient_id, environment: context.environment,
  });
  return {
    facts: {
      aggregate, corporate, dataset_digest, descriptor_digest, binding_digest,
      artifact_digest: digest({
        schema_version: V5_J302_SCHEMA_VERSION,
        tenant: ORGANIZATION_TENANT_ID,
        corporate_content_digest: corporate.content_digest,
        native_identity: { ...corporate.native_identity },
        native_version: corporate.native_version,
        descriptor_digest,
        dataset_digest,
      }),
    },
  };
}

/** The threshold every unsuppressed, non-zero cell must meet under the chosen route. */
function smallCellBreach(aggregate, minimum) {
  const index = aggregate.cells.findIndex(c => !c.suppressed && c.patient_count > 0 &&
    c.patient_count < minimum);
  return index;
}

// ---------------------------------------------------------------------------
// Route receipts.
// ---------------------------------------------------------------------------

const RECEIPT_COMMON_KEYS = Object.freeze([
  "route", "receipt_id", "producer_role", "oracle_ref", "oracle_version", "issuer",
  "issued_at", "expires_at", "artifact_content_digest", "aggregate_descriptor_digest",
  "dataset_recipient_environment_digest", "source_privacy_threshold_acknowledged",
]);
const SAFE_HARBOR_KEYS = Object.freeze([
  ...RECEIPT_COMMON_KEYS, "hhs_rule_ref", "identifier_categories_removed",
  "census_population", "no_actual_knowledge_attestation",
]);
const EXPERT_KEYS = Object.freeze([
  ...RECEIPT_COMMON_KEYS, "expert", "method", "small_cell", "precision", "budgets",
  "processor_terms", "retention",
]);
const ISSUER_KEYS = Object.freeze(["identity", "kind"]);
const CENSUS_KEYS = Object.freeze(["source_ref", "vintage", "table_digest", "zip3_population"]);
const EXPERT_IDENTITY_KEYS = Object.freeze(["identity", "qualifications", "independent_of_recipient"]);
const METHOD_KEYS = Object.freeze(["method_ref", "method_digest", "results_digest"]);
const SMALL_CELL_KEYS = Object.freeze(["minimum_cell_count", "complementary_suppression_required"]);
const PRECISION_KEYS = Object.freeze(["finest_spatial_unit", "finest_temporal_precision"]);
const PROCESSOR_TERMS_KEYS = Object.freeze(["processor_id", "terms_digest"]);
const RETENTION_KEYS = Object.freeze(["retain_until", "deletion_rule_ref"]);

function readReceipt(raw, path) {
  assertObject(raw, path);
  const route = assertEnum(raw.route, V5_J302_ROUTES, `${path}.route`, "unknown_route");
  const keys = route === "safe_harbor" ? SAFE_HARBOR_KEYS : EXPERT_KEYS;
  assertClosedKeys(raw, keys, path);
  assertRequiredKeys(raw, keys, path);
  const issuer = assertObject(raw.issuer, `${path}.issuer`);
  assertClosedKeys(issuer, ISSUER_KEYS, `${path}.issuer`);
  assertRequiredKeys(issuer, ISSUER_KEYS, `${path}.issuer`);
  const common = {
    route,
    receipt_id: assertIdent(raw.receipt_id, `${path}.receipt_id`),
    producer_role: assertIdent(raw.producer_role, `${path}.producer_role`),
    oracle_ref: assertIdent(raw.oracle_ref, `${path}.oracle_ref`),
    oracle_version: assertIdent(raw.oracle_version, `${path}.oracle_version`),
    issuer: {
      identity: assertIdent(issuer.identity, `${path}.issuer.identity`),
      kind: assertEnum(issuer.kind, V5_J302_ISSUER_KINDS, `${path}.issuer.kind`, "unknown_issuer_kind"),
    },
    issued_at: assertInstant(raw.issued_at, `${path}.issued_at`),
    expires_at: assertInstant(raw.expires_at, `${path}.expires_at`),
    artifact_content_digest: assertDigestRef(raw.artifact_content_digest, `${path}.artifact_content_digest`),
    aggregate_descriptor_digest: assertDigestRef(raw.aggregate_descriptor_digest,
      `${path}.aggregate_descriptor_digest`),
    dataset_recipient_environment_digest: assertDigestRef(raw.dataset_recipient_environment_digest,
      `${path}.dataset_recipient_environment_digest`),
    source_privacy_threshold_acknowledged: assertInteger(raw.source_privacy_threshold_acknowledged,
      `${path}.source_privacy_threshold_acknowledged`, { min: 1 }),
  };
  if (route === "safe_harbor") {
    const census = assertObject(raw.census_population, `${path}.census_population`);
    assertClosedKeys(census, CENSUS_KEYS, `${path}.census_population`);
    assertRequiredKeys(census, CENSUS_KEYS, `${path}.census_population`);
    const categories = assertArray(raw.identifier_categories_removed,
      `${path}.identifier_categories_removed`, { min: 0, max: 32 })
      .map((c, i) => assertIdent(c, `${path}.identifier_categories_removed[${i}]`));
    return {
      ...common,
      hhs_rule_ref: assertText(raw.hhs_rule_ref, `${path}.hhs_rule_ref`, { maxLength: 64 }),
      identifier_categories_removed: [...new Set(categories)].sort(),
      census_population: {
        source_ref: assertIdent(census.source_ref, `${path}.census_population.source_ref`),
        vintage: assertIdent(census.vintage, `${path}.census_population.vintage`),
        table_digest: assertDigestRef(census.table_digest, `${path}.census_population.table_digest`),
        zip3_population: snapshot(assertObject(census.zip3_population,
          `${path}.census_population.zip3_population`)),
        recomputed_table_digest: zip3PopulationTableDigest(census.zip3_population),
      },
      no_actual_knowledge_attestation: assertBoolean(raw.no_actual_knowledge_attestation,
        `${path}.no_actual_knowledge_attestation`),
    };
  }
  const expert = assertObject(raw.expert, `${path}.expert`);
  assertClosedKeys(expert, EXPERT_IDENTITY_KEYS, `${path}.expert`);
  assertRequiredKeys(expert, EXPERT_IDENTITY_KEYS, `${path}.expert`);
  const method = assertObject(raw.method, `${path}.method`);
  assertClosedKeys(method, METHOD_KEYS, `${path}.method`);
  assertRequiredKeys(method, METHOD_KEYS, `${path}.method`);
  const smallCell = assertObject(raw.small_cell, `${path}.small_cell`);
  assertClosedKeys(smallCell, SMALL_CELL_KEYS, `${path}.small_cell`);
  assertRequiredKeys(smallCell, SMALL_CELL_KEYS, `${path}.small_cell`);
  const precision = assertObject(raw.precision, `${path}.precision`);
  assertClosedKeys(precision, PRECISION_KEYS, `${path}.precision`);
  assertRequiredKeys(precision, PRECISION_KEYS, `${path}.precision`);
  const budgets = assertObject(raw.budgets, `${path}.budgets`);
  assertClosedKeys(budgets, V5_J302_BUDGET_CLASSES, `${path}.budgets`);
  assertRequiredKeys(budgets, V5_J302_BUDGET_CLASSES, `${path}.budgets`);
  const retention = assertObject(raw.retention, `${path}.retention`);
  assertClosedKeys(retention, RETENTION_KEYS, `${path}.retention`);
  assertRequiredKeys(retention, RETENTION_KEYS, `${path}.retention`);
  const terms = assertArray(raw.processor_terms, `${path}.processor_terms`, { min: 0, max: 16 })
    .map((t, i) => {
      const p = `${path}.processor_terms[${i}]`;
      assertObject(t, p);
      assertClosedKeys(t, PROCESSOR_TERMS_KEYS, p);
      assertRequiredKeys(t, PROCESSOR_TERMS_KEYS, p);
      return { processor_id: assertIdent(t.processor_id, `${p}.processor_id`),
        terms_digest: assertDigestRef(t.terms_digest, `${p}.terms_digest`) };
    });
  if (new Set(terms.map(t => t.processor_id)).size !== terms.length) {
    fail("duplicate_processor", `${path}.processor_terms repeats a processor`, { path });
  }
  return {
    ...common,
    expert: {
      identity: assertIdent(expert.identity, `${path}.expert.identity`),
      qualifications: assertArray(expert.qualifications, `${path}.expert.qualifications`, { min: 1, max: 16 })
        .map((q, i) => assertText(q, `${path}.expert.qualifications[${i}]`, { maxLength: 500 })),
      independent_of_recipient: assertBoolean(expert.independent_of_recipient,
        `${path}.expert.independent_of_recipient`),
    },
    method: {
      method_ref: assertIdent(method.method_ref, `${path}.method.method_ref`),
      method_digest: assertDigestRef(method.method_digest, `${path}.method.method_digest`),
      results_digest: assertDigestRef(method.results_digest, `${path}.method.results_digest`),
    },
    small_cell: {
      minimum_cell_count: assertInteger(smallCell.minimum_cell_count,
        `${path}.small_cell.minimum_cell_count`, { min: 1 }),
      complementary_suppression_required: assertBoolean(smallCell.complementary_suppression_required,
        `${path}.small_cell.complementary_suppression_required`),
    },
    precision: {
      // Read as text so an unregistered unit is a policy refusal (unknown
      // sensitivity denies), not a crash that hides which bound was meant.
      finest_spatial_unit: assertText(precision.finest_spatial_unit,
        `${path}.precision.finest_spatial_unit`, { maxLength: 64 }),
      finest_temporal_precision: assertText(precision.finest_temporal_precision,
        `${path}.precision.finest_temporal_precision`, { maxLength: 32 }),
    },
    budgets: Object.fromEntries(V5_J302_BUDGET_CLASSES.map(k =>
      [k, assertInteger(budgets[k], `${path}.budgets.${k}`)])),
    processor_terms: terms.sort((a, b) => a.processor_id.localeCompare(b.processor_id)),
    retention: {
      retain_until: assertInstant(retention.retain_until, `${path}.retention.retain_until`),
      deletion_rule_ref: assertIdent(retention.deletion_rule_ref, `${path}.retention.deletion_rule_ref`),
    },
  };
}

/** Checks every route shares: independence as stated, currency, and the three bindings. */
function judgeReceiptCommon(receipt, facts, context, now, base) {
  if (receipt.producer_role !== V5_J302_PRODUCER_ROLE ||
      receipt.oracle_ref !== V5_J302_ORACLE_REF ||
      receipt.oracle_version !== V5_J302_ORACLE_VERSION) {
    return refusal("route_producer_not_independent_oracle", base);
  }
  if (receipt.issuer.kind === "model") {
    return refusal("model_cannot_issue_privacy_route", base);
  }
  if (receipt.issuer.identity === context.requesting_actor ||
      receipt.issuer.identity === context.recipient_id) {
    return refusal("route_not_independent_of_requester", base);
  }
  if (receipt.issued_at > now) return refusal("route_issued_after_now", base);
  if (receipt.expires_at <= receipt.issued_at) return refusal("route_window_empty", base);
  if (receipt.expires_at <= now) return refusal("route_expired", base);
  if (receipt.artifact_content_digest !== facts.corporate.content_digest) {
    return refusal("route_artifact_digest_mismatch", base);
  }
  if (receipt.aggregate_descriptor_digest !== facts.descriptor_digest) {
    return refusal("route_descriptor_digest_mismatch", base);
  }
  if (receipt.dataset_recipient_environment_digest !== facts.binding_digest) {
    return refusal("route_dataset_recipient_environment_mismatch", base);
  }
  if (receipt.source_privacy_threshold_acknowledged !==
      facts.aggregate.source_privacy_threshold.minimum_cell_count) {
    return refusal("source_privacy_threshold_not_preserved", base);
  }
  return null;
}

function judgeSafeHarbor(receipt, facts, base) {
  if (receipt.hhs_rule_ref !== V5_J302_HHS_SAFE_HARBOR_RULE_REF) {
    return refusal("safe_harbor_rule_ref_mismatch", base);
  }
  const missing = V5_J302_HHS_IDENTIFIER_CATEGORIES
    .filter(c => !receipt.identifier_categories_removed.includes(c));
  const unknown = receipt.identifier_categories_removed
    .filter(c => !V5_J302_HHS_IDENTIFIER_CATEGORIES.includes(c));
  if (missing.length > 0 || unknown.length > 0) {
    return refusal("safe_harbor_identifier_categories_incomplete", base,
      { missing_categories: missing, unknown_categories: unknown });
  }
  if (receipt.no_actual_knowledge_attestation !== true) {
    return refusal("safe_harbor_actual_knowledge_not_disclaimed", base);
  }
  const { aggregate } = facts;
  if (!V5_J302_SAFE_HARBOR_SPATIAL_UNITS.includes(aggregate.geography_unit)) {
    return refusal("safe_harbor_geography_smaller_than_permitted", base,
      { geography_unit: aggregate.geography_unit });
  }
  if (!V5_J302_SAFE_HARBOR_TEMPORAL_PRECISIONS.includes(aggregate.temporal_precision)) {
    return refusal("safe_harbor_date_element_refused", base,
      { temporal_precision: aggregate.temporal_precision });
  }
  if (receipt.census_population.recomputed_table_digest !== receipt.census_population.table_digest) {
    return refusal("census_population_table_digest_mismatch", base);
  }
  if (aggregate.geography_unit === "zip3") {
    const table = receipt.census_population.zip3_population;
    for (const [i, cell] of aggregate.cells.entries()) {
      if (cell.unit_id === V5_J302_SAFE_HARBOR_SUPPRESSED_ZIP3) continue;
      if (V5_J302_HHS_RESTRICTED_ZIP3.includes(cell.unit_id)) {
        return refusal("restricted_zip3_must_be_000", base, { cell_index: i });
      }
      if (!Object.prototype.hasOwnProperty.call(table, cell.unit_id)) {
        return refusal("zip3_population_unknown_denied", base, { cell_index: i });
      }
      if (table[cell.unit_id] <= V5_J302_SAFE_HARBOR_ZIP3_POPULATION_FLOOR) {
        return refusal("zip3_population_not_above_floor", base, { cell_index: i });
      }
    }
  }
  const minimum = aggregate.source_privacy_threshold.minimum_cell_count;
  const breach = smallCellBreach(aggregate, minimum);
  if (breach >= 0) {
    return refusal("source_privacy_threshold_breached", base, { cell_index: breach, minimum_cell_count: minimum });
  }
  return null;
}

function judgeExpertDetermination(receipt, facts, context, now, base) {
  if (receipt.expert.identity === context.requesting_actor ||
      receipt.expert.identity === context.recipient_id) {
    return refusal("expert_not_independent_of_requester", base);
  }
  if (receipt.expert.independent_of_recipient !== true) {
    return refusal("expert_independence_not_stated", base);
  }
  const { aggregate } = facts;
  const finestSpatial = receipt.precision.finest_spatial_unit;
  const finestTemporal = receipt.precision.finest_temporal_precision;
  if (!V5_J302_SPATIAL_UNITS.includes(finestSpatial) ||
      !V5_J302_TEMPORAL_PRECISIONS.includes(finestTemporal)) {
    return refusal("determination_precision_unknown_denied", base);
  }
  if (V5_J302_SPATIAL_UNIT_RANK[aggregate.geography_unit] < V5_J302_SPATIAL_UNIT_RANK[finestSpatial]) {
    return refusal("spatial_precision_finer_than_determination", base,
      { geography_unit: aggregate.geography_unit, finest_spatial_unit: finestSpatial });
  }
  if (V5_J302_TEMPORAL_RANK[aggregate.temporal_precision] < V5_J302_TEMPORAL_RANK[finestTemporal]) {
    return refusal("temporal_precision_finer_than_determination", base,
      { temporal_precision: aggregate.temporal_precision, finest_temporal_precision: finestTemporal });
  }
  // Q048.D1: the source threshold is preserved even when the expert would
  // accept a smaller cell. The stricter of the two governs.
  const minimum = Math.max(receipt.small_cell.minimum_cell_count,
    aggregate.source_privacy_threshold.minimum_cell_count);
  const breach = smallCellBreach(aggregate, minimum);
  if (breach >= 0) {
    return refusal("small_cell_below_determination_threshold", base,
      { cell_index: breach, minimum_cell_count: minimum });
  }
  if (receipt.small_cell.complementary_suppression_required &&
      aggregate.cells.some(c => c.suppressed) && aggregate.published_total !== null &&
      aggregate.published_total - aggregate.cells.reduce((s, c) => s + (c.suppressed ? 0 : c.patient_count), 0)
        < minimum) {
    // Two or more suppressed cells whose combined residual is below the floor
    // still bound each of them tightly enough to reveal a small group.
    return refusal("complementary_suppression_residual_below_threshold", base);
  }
  const covered = new Set(receipt.processor_terms.map(t => t.processor_id));
  const uncovered = context.processors.filter(p => !covered.has(p));
  if (uncovered.length > 0) {
    return refusal("processor_not_covered_by_terms", base, { processors: uncovered });
  }
  if (receipt.retention.retain_until <= now) {
    return refusal("retention_elapsed", base);
  }
  if (receipt.retention.retain_until > receipt.expires_at) {
    return refusal("retention_outlives_determination", base);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Public: route conformance.
// ---------------------------------------------------------------------------

const CONFORMANCE_KEYS = Object.freeze([
  "tenant", "artifact", "context", "route_receipts", "now", "prior_artifact",
]);

/**
 * Judge one artifact against the route receipts a caller holds.
 *
 * The best answer is `conforms`: the receipt, if it was independently issued,
 * would carry this artifact through its route. It is never an admission — the
 * receipt here came from the caller — and every result says so.
 */
export function evaluatePrivacyRouteConformance(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CONFORMANCE_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "artifact", "context", "route_receipts", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const context = readContext(request.context, "request.context");
  const receipts = assertArray(request.route_receipts, "request.route_receipts", { min: 0, max: 8 });
  const base = { tenant: ORGANIZATION_TENANT_ID, route: null, receipt_id: null };

  const judged = judgeArtifact(request.artifact, context, now, base, request.prior_artifact);
  if (judged.refusal) return judged.refusal;
  const { facts } = judged;
  const withArtifact = { ...base, artifact_digest: facts.artifact_digest,
    binding_digest: facts.binding_digest };

  if (receipts.length !== 1) {
    return refusal("exactly_one_privacy_route_required", withArtifact,
      { routes_presented: receipts.length });
  }
  const receipt = readReceipt(receipts[0], "request.route_receipts[0]");
  const routed = { ...withArtifact, route: receipt.route, receipt_id: receipt.receipt_id };

  const common = judgeReceiptCommon(receipt, facts, context, now, routed);
  if (common) return common;
  const specific = receipt.route === "safe_harbor"
    ? judgeSafeHarbor(receipt, facts, routed)
    : judgeExpertDetermination(receipt, facts, context, now, routed);
  if (specific) return specific;

  return outcome({
    decision: "conforms",
    reason_id: "route_conforms_pending_independent_issuance",
    ...routed,
    expires_at: new Date(receipt.expires_at).toISOString(),
    retain_until: receipt.route === "expert_determination"
      ? new Date(receipt.retention.retain_until).toISOString() : null,
    budgets: receipt.route === "expert_determination" ? { ...receipt.budgets } : null,
    required_runtime_evidence: [...V5_J302_REQUIRED_RUNTIME_EVIDENCE],
  });
}

// ---------------------------------------------------------------------------
// Public: intake. Takes no receipt, so it can never be handed one.
// ---------------------------------------------------------------------------

const INTAKE_KEYS = Object.freeze(["tenant", "artifact", "context", "now", "prior_artifact"]);

/**
 * The intake path. Artifact-level refusals are answered in full, so a raw or
 * reversible source is told exactly why; every artifact that survives them is
 * refused because the independently issued receipt cannot be retrieved here.
 * There is no request field that carries a receipt: the closed schema refuses
 * one as an unknown field before anything is read.
 */
export function admitAggregateHeatMapArtifact(request) {
  assertObject(request, "request");
  assertClosedKeys(request, INTAKE_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "artifact", "context", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const context = readContext(request.context, "request.context");
  const base = { tenant: ORGANIZATION_TENANT_ID, route: null, receipt_id: null };
  const judged = judgeArtifact(request.artifact, context, now, base, request.prior_artifact);
  if (judged.refusal) return judged.refusal;
  return refusal("independent_privacy_route_receipt_unavailable", {
    ...base, artifact_digest: judged.facts.artifact_digest, binding_digest: judged.facts.binding_digest,
  }, {
    owed_seam: V5_J302_RECEIPT_RETRIEVAL_SEAM,
    required_gate: V5_J302_ROUTE_GATE,
    required_runtime_evidence: [...V5_J302_REQUIRED_RUNTIME_EVIDENCE],
  });
}

// ---------------------------------------------------------------------------
// Public: one operation over an artifact, and its budget.
// ---------------------------------------------------------------------------

const OPERATION_REQUEST_KEYS = Object.freeze(["tenant", "route_receipt", "ledger", "operation", "now"]);
const OPERATION_KEYS = Object.freeze(["kind", "artifact_digest"]);
const LEDGER_KEYS = Object.freeze(["schema_version", "receipt_id", "ledger_version", "used"]);

function readLedger(raw, path) {
  assertObject(raw, path);
  assertClosedKeys(raw, LEDGER_KEYS, path);
  assertRequiredKeys(raw, LEDGER_KEYS, path);
  if (raw.schema_version !== V5_J302_LEDGER_SCHEMA_VERSION) {
    fail("unknown_ledger_schema", `${path}.schema_version is not ${V5_J302_LEDGER_SCHEMA_VERSION}`, { path });
  }
  const used = assertObject(raw.used, `${path}.used`);
  assertClosedKeys(used, V5_J302_BUDGET_CLASSES, `${path}.used`);
  assertRequiredKeys(used, V5_J302_BUDGET_CLASSES, `${path}.used`);
  return {
    schema_version: V5_J302_LEDGER_SCHEMA_VERSION,
    receipt_id: assertIdent(raw.receipt_id, `${path}.receipt_id`),
    ledger_version: assertInteger(raw.ledger_version, `${path}.ledger_version`),
    used: Object.fromEntries(V5_J302_BUDGET_CLASSES.map(k => [k, assertInteger(used[k], `${path}.used.${k}`)])),
  };
}

/** A fresh, zeroed ledger for one receipt. The persistence owner stores it; this returns bytes. */
export function emptyPrivacyBudgetLedger(receipt_id) {
  return deepFreeze({
    schema_version: V5_J302_LEDGER_SCHEMA_VERSION,
    receipt_id: assertIdent(receipt_id, "receipt_id"),
    ledger_version: 0,
    used: { differencing: 0, export: 0, query: 0 },
  });
}

/**
 * Judge one operation. Re-identifying operations refuse by name under every
 * route. Under Expert Determination a permitted operation consumes exactly one
 * unit of its budget class; the answer carries the next ledger and the
 * compare-and-swap precondition the store must hold atomically, so two
 * concurrent callers holding the same ledger cannot both spend its last unit.
 * Safe Harbor binds no budget, so any budgeted class beyond plain viewing is
 * refused there rather than left unmetered.
 */
export function evaluateAggregateOperation(request) {
  assertObject(request, "request");
  assertClosedKeys(request, OPERATION_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["tenant", "route_receipt", "operation", "now"], "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const operation = assertObject(request.operation, "request.operation");
  assertClosedKeys(operation, OPERATION_KEYS, "request.operation");
  assertRequiredKeys(operation, OPERATION_KEYS, "request.operation");
  const kind = assertText(operation.kind, "request.operation.kind", { maxLength: 64 });
  const artifact_digest = assertDigestRef(operation.artifact_digest, "request.operation.artifact_digest");
  const receipt = readReceipt(request.route_receipt, "request.route_receipt");
  const base = { tenant: ORGANIZATION_TENANT_ID, route: receipt.route, receipt_id: receipt.receipt_id,
    operation_kind: kind, artifact_digest, next_ledger: null, compare_and_swap: null };

  if (V5_J302_REIDENTIFYING_OPERATIONS.includes(kind)) {
    return refusal("reidentifying_operation_refused", base);
  }
  if (!V5_J302_OPERATION_KINDS.includes(kind)) {
    return refusal("unknown_operation_denied", base);
  }
  if (receipt.expires_at <= now) return refusal("route_expired", base);
  const budgetClass = V5_J302_OPERATIONS[kind];

  if (receipt.route === "safe_harbor") {
    if (budgetClass !== "query") {
      return refusal("operation_budget_not_bound_by_route", base, { budget_class: budgetClass });
    }
    return outcome({ decision: "within_route", reason_id: "safe_harbor_native_precision_query",
      ...base, budget_class: budgetClass });
  }

  if (request.ledger === undefined || request.ledger === null) {
    return refusal("budget_ledger_required", base, { owed_seam: V5_J302_BUDGET_LEDGER_STORE_SEAM });
  }
  const ledger = readLedger(request.ledger, "request.ledger");
  if (ledger.receipt_id !== receipt.receipt_id) {
    return refusal("budget_ledger_receipt_mismatch", base);
  }
  if (receipt.retention.retain_until <= now) return refusal("retention_elapsed", base);
  const limit = receipt.budgets[budgetClass];
  if (ledger.used[budgetClass] + 1 > limit) {
    return refusal("privacy_budget_exhausted", base,
      { budget_class: budgetClass, used: ledger.used[budgetClass], limit });
  }
  const next_ledger = {
    schema_version: V5_J302_LEDGER_SCHEMA_VERSION,
    receipt_id: ledger.receipt_id,
    ledger_version: ledger.ledger_version + 1,
    used: { ...ledger.used, [budgetClass]: ledger.used[budgetClass] + 1 },
  };
  return outcome({
    decision: "within_budget", reason_id: "expert_determination_budget_unit_reserved_in_kernel",
    ...base,
    budget_class: budgetClass,
    remaining_after: limit - next_ledger.used[budgetClass],
    next_ledger,
    compare_and_swap: {
      expected_ledger_version: ledger.ledger_version,
      expected_ledger_digest: digest(ledger),
      next_ledger_digest: digest(next_ledger),
    },
    atomic_store_seam: V5_J302_BUDGET_LEDGER_STORE_SEAM,
    ledger_written: false,
  });
}

// ---------------------------------------------------------------------------
// Public: derived strategy proposals (Q071.D2). A proposal stays a proposal.
// ---------------------------------------------------------------------------

const PROPOSAL_REQUEST_KEYS = Object.freeze(["tenant", "artifact", "proposal", "now"]);
const PROPOSAL_KEYS = Object.freeze([
  "proposal_kind", "statement", "cited_unit_ids", "confidence", "evidence_ref", "proposed_by",
]);
const PROPOSER_KEYS = Object.freeze(["kind", "identity"]);
/** Keys that reach past "worth a review". Refused by name before the shape is read. */
const PROPOSAL_WIDENING_FRAGMENTS = deepFreeze([
  "accept", "admit", "apply", "approve", "authority", "commit", "effect", "fact",
  "privacy", "publish", "receipt", "route", "threshold",
]);
// A statement that carries a coordinate or a five-digit ZIP has gone finer than
// any permitted aggregate, whatever the artifact said.
const COORDINATE_TEXT = /-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}/;
const ZIP5_TEXT_GLOBAL = /(^|[^\d])(\d{5})(?:-\d{4})?(?=[^\d]|$)/g;

function proposalOutcome(fields) {
  return outcome({
    ...fields,
    is_fact: false,
    applies_to_record: false,
    requires_human_review: true,
    review_seam: V5_J302_PROPOSAL_REVIEW_SEAM,
  });
}

/**
 * Judge one strategy proposal derived from an aggregate artifact. A model may
 * propose; it cannot inspect the raw source, approve a route or turn its
 * proposal into a fact. The best answer is `proposal_pending_review`.
 */
export function evaluateDerivedStrategyProposal(request) {
  assertObject(request, "request");
  assertClosedKeys(request, PROPOSAL_REQUEST_KEYS, "request");
  assertRequiredKeys(request, PROPOSAL_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");
  assertInstant(request.now, "request.now");
  const proposal = assertObject(request.proposal, "request.proposal");
  const base = { tenant: ORGANIZATION_TENANT_ID, route: null, receipt_id: null };
  for (const key of Object.keys(proposal)) {
    if (PROPOSAL_KEYS.includes(key)) continue;
    if (PROPOSAL_WIDENING_FRAGMENTS.some(f => key.toLowerCase().includes(f))) {
      return proposalOutcome({ decision: "refuse", reason_id: "proposal_widening_refused", ...base,
        offending_field: key });
    }
  }
  assertClosedKeys(proposal, PROPOSAL_KEYS, "request.proposal");
  assertRequiredKeys(proposal, PROPOSAL_KEYS, "request.proposal");
  const proposer = assertObject(proposal.proposed_by, "request.proposal.proposed_by");
  assertClosedKeys(proposer, PROPOSER_KEYS, "request.proposal.proposed_by");
  assertRequiredKeys(proposer, PROPOSER_KEYS, "request.proposal.proposed_by");
  assertEnum(proposer.kind, V5_J302_PROPOSER_KINDS, "request.proposal.proposed_by.kind", "unknown_proposer_kind");
  assertIdent(proposer.identity, "request.proposal.proposed_by.identity");
  assertEnum(proposal.proposal_kind, V5_J302_PROPOSAL_KINDS, "request.proposal.proposal_kind",
    "unknown_proposal_kind");
  const statement = assertText(proposal.statement, "request.proposal.statement", { maxLength: 2000 });
  if (!Number.isFinite(proposal.confidence) || proposal.confidence < 0 || proposal.confidence > 1) {
    fail("invalid_shape", "request.proposal.confidence must be a number between 0 and 1",
      { path: "request.proposal.confidence" });
  }
  assertIdent(proposal.evidence_ref, "request.proposal.evidence_ref");
  const cited = assertArray(proposal.cited_unit_ids, "request.proposal.cited_unit_ids", { min: 1, max: 64 })
    .map((u, i) => assertText(u, `request.proposal.cited_unit_ids[${i}]`, { maxLength: 64 }));

  const artifactRaw = assertObject(request.artifact, "request.artifact");
  assertClosedKeys(artifactRaw, ["aggregate", "descriptor_digest"], "request.artifact");
  assertRequiredKeys(artifactRaw, ["aggregate", "descriptor_digest"], "request.artifact");
  const aggregate = readAggregate(artifactRaw.aggregate, "request.artifact.aggregate");
  const descriptor_digest = digest(descriptorPreimage(aggregate));
  if (descriptor_digest !== assertDigestRef(artifactRaw.descriptor_digest, "request.artifact.descriptor_digest")) {
    return proposalOutcome({ decision: "refuse", reason_id: "proposal_artifact_digest_mismatch", ...base });
  }
  const withArtifact = { ...base, descriptor_digest };

  const units = new Map();
  for (const cell of aggregate.cells) {
    const list = units.get(cell.unit_id) ?? [];
    list.push(cell);
    units.set(cell.unit_id, list);
  }
  // A five-digit token is finer geography unless it is one of this artifact's
  // own unit ids: a county FIPS code is also five digits, and naming a unit the
  // artifact already carries at its own precision reveals nothing finer.
  const fiveDigitTokens = [...statement.matchAll(ZIP5_TEXT_GLOBAL)].map(m => m[2]);
  if (COORDINATE_TEXT.test(statement) || fiveDigitTokens.some(token => !units.has(token))) {
    return proposalOutcome({ decision: "refuse", reason_id: "proposal_finer_than_aggregate", ...withArtifact });
  }
  for (const unit of cited) {
    if (!units.has(unit)) {
      return proposalOutcome({ decision: "refuse", reason_id: "proposal_cites_unit_outside_artifact",
        ...withArtifact, unit_id: unit });
    }
    if (units.get(unit).every(c => c.suppressed)) {
      return proposalOutcome({ decision: "refuse", reason_id: "proposal_cites_suppressed_cell",
        ...withArtifact, unit_id: unit });
    }
  }
  return proposalOutcome({
    decision: "proposal_pending_review",
    reason_id: "derived_strategy_is_proposal_only",
    ...withArtifact,
    proposal_digest: digest({
      schema_version: V5_J302_SCHEMA_VERSION,
      descriptor_digest,
      proposal_kind: proposal.proposal_kind,
      statement,
      cited_unit_ids: [...cited].sort(),
      confidence: proposal.confidence,
      evidence_ref: proposal.evidence_ref,
      proposed_by: { kind: proposer.kind, identity: proposer.identity },
    }),
    proposed_by_kind: proposer.kind,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy and its digest.
// ---------------------------------------------------------------------------

export function v5J302PolicyPreimage() {
  return {
    schema_version: V5_J302_SCHEMA_VERSION,
    policy_version: V5_J302_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decisions: V5_J302_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_J302_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_J302_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
    map_contract: { id: V5_J301_MAP_CONTRACT, version: V5_J301_MAP_CONTRACT_VERSION,
      gate: V5_J301_MAP_CONTRACT_GATE },
    routes: [...V5_J302_ROUTES],
    producer_role: V5_J302_PRODUCER_ROLE,
    oracle: { ref: V5_J302_ORACLE_REF, version: V5_J302_ORACLE_VERSION },
    route_gate: V5_J302_ROUTE_GATE,
    required_runtime_evidence: [...V5_J302_REQUIRED_RUNTIME_EVIDENCE],
    safe_harbor: {
      rule_ref: V5_J302_HHS_SAFE_HARBOR_RULE_REF,
      identifier_categories: [...V5_J302_HHS_IDENTIFIER_CATEGORIES],
      zip3_population_floor: V5_J302_SAFE_HARBOR_ZIP3_POPULATION_FLOOR,
      suppressed_zip3: V5_J302_SAFE_HARBOR_SUPPRESSED_ZIP3,
      restricted_zip3: [...V5_J302_HHS_RESTRICTED_ZIP3],
      spatial_units: [...V5_J302_SAFE_HARBOR_SPATIAL_UNITS],
      temporal_precisions: [...V5_J302_SAFE_HARBOR_TEMPORAL_PRECISIONS],
    },
    spatial_unit_rank: { ...V5_J302_SPATIAL_UNIT_RANK },
    raw_location_units: [...V5_J302_RAW_LOCATION_UNITS],
    temporal_rank: { ...V5_J302_TEMPORAL_RANK },
    permitted_columns: [...V5_J302_PERMITTED_COLUMNS],
    identifier_columns: [...V5_J302_IDENTIFIER_COLUMNS],
    operations: { ...V5_J302_OPERATIONS },
    reidentifying_operations: [...V5_J302_REIDENTIFYING_OPERATIONS],
    platform_small_cell_floor: null,
    seams: [V5_J302_BUDGET_LEDGER_STORE_SEAM, V5_J302_PROPOSAL_REVIEW_SEAM,
      V5_J302_RECEIPT_RETRIEVAL_SEAM].sort(),
  };
}

export function v5J302PolicyDigest() {
  return digest(v5J302PolicyPreimage());
}

export function v5J302PolicyCanonicalBytes() {
  return canonicalJson(v5J302PolicyPreimage());
}
