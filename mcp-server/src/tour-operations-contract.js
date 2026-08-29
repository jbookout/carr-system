// Facts-only Tour projection contracts. Rendering and publication authority
// are deliberately elsewhere.
import { sha256 } from "./sha256.js";

export const PUBLIC_TOUR_FIELD_KEYS = new Set([
  "display.name", "display.address", "suite", "property_type", "size",
  "asking_economics", "availability", "parking", "access", "photos",
  "floor_plan", "source_attribution", "as_of", "caveat",
]);
export const REQUIRED_PUBLIC_PROPERTY_FIELDS = Object.freeze(["display.name", "display.address"]);
export const PUBLIC_ASSET_REFERENCE_RE = /^asset:public:[A-Za-z0-9_-]{16,256}$/;

const metricValue = value =>
  (typeof value === "number" && Number.isFinite(value)) ||
  (typeof value === "string" && value.trim().length > 0 && value.trim().length <= 120);
function approvedMetricIsSafe(value) {
  if (!value || Array.isArray(value) || typeof value !== "object") return false;
  const keys = new Set(["value", "unit", "min", "max", "currency", "period", "label"]);
  if (Object.keys(value).some(key => !keys.has(key)) ||
      !["value", "min", "max"].some(key => Object.hasOwn(value, key))) return false;
  for (const key of ["value", "min", "max"])
    if (Object.hasOwn(value, key) && !metricValue(value[key])) return false;
  for (const key of ["unit", "currency", "period", "label"])
    if (Object.hasOwn(value, key) &&
        (typeof value[key] !== "string" || !value[key].trim() || value[key].trim().length > 120)) return false;
  return !(typeof value.min === "number" && typeof value.max === "number" && value.min > value.max);
}
export function requiredTimestamp(value) {
  if (typeof value !== "string" ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)) return false;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return false;
  return parsed.toISOString() === value || parsed.toISOString().replace(".000Z", "Z") === value;
}

const FACT_CONFIDENCE = new Set(["low", "medium", "high", "unknown"]);
const FACT_CLASSIFICATION = new Set(["public", "client_authorized", "internal", "restricted"]);
const FACT_REVIEW_STATE = new Set(["unreviewed", "reviewed", "conflicted", "superseded", "withdrawn"]);
export const CANONICAL_FACT_REQUIRED_FIELDS = Object.freeze([
  "organization_tenant_id", "property_id", "field_key", "value", "source_evidence_id", "rights_receipt_id",
  "observed_at", "effective_from", "effective_to", "confidence",
  "data_classification", "review_state",
]);

function postgresTextIsSafe(value) {
  if (typeof value !== "string" || value.includes("\u0000")) return false;
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index++;
    } else if (code >= 0xdc00 && code <= 0xdfff) return false;
  }
  return true;
}

function canonicalJsonValueIsSafe(value, topLevel = true, seen = new Set()) {
  if (value === null) return !topLevel;
  if (typeof value === "string") return postgresTextIsSafe(value);
  if (typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (typeof value !== "object" || seen.has(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if ((Array.isArray(value) && prototype !== Array.prototype) ||
      (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null)) return false;
  for (let cursor = value; cursor; cursor = Object.getPrototypeOf(cursor)) {
    const descriptor = Object.getOwnPropertyDescriptor(cursor, "toJSON");
    if (descriptor && (!Object.hasOwn(descriptor, "value") ||
        typeof descriptor.value === "function")) return false;
  }
  if (Reflect.ownKeys(value).some(key => typeof key === "symbol")) return false;
  seen.add(value);
  let safe;
  if (Array.isArray(value)) {
    safe = Array.from({length: value.length}, (_, index) => index)
      .every(index => {
        const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
        return descriptor && Object.hasOwn(descriptor, "value") &&
          canonicalJsonValueIsSafe(descriptor.value, false, seen);
      });
  } else {
    safe = Object.keys(value).every(key => postgresTextIsSafe(key) && (() => {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      return descriptor && Object.hasOwn(descriptor, "value") &&
        canonicalJsonValueIsSafe(descriptor.value, false, seen);
    })());
  }
  seen.delete(value);
  return safe;
}

const ownEnumerableDataDescriptor = (record, field) => {
  const descriptor = Object.getOwnPropertyDescriptor(record, field);
  return descriptor && descriptor.enumerable && Object.hasOwn(descriptor, "value")
    ? descriptor : null;
};

const plainRecordEnvelopeIsSafe = record => {
  const prototype = Object.getPrototypeOf(record);
  if (prototype !== Object.prototype && prototype !== null) return false;
  for (let cursor = record; cursor; cursor = Object.getPrototypeOf(cursor)) {
    const descriptor = Object.getOwnPropertyDescriptor(cursor, "toJSON");
    if (descriptor && (!Object.hasOwn(descriptor, "value") ||
        typeof descriptor.value === "function")) return false;
  }
  return true;
};

export function validateCanonicalFieldAssertion(assertion) {
  // Foundation contract primitive only. Runtime adapter enforcement belongs to
  // the dependency-gated rights-and-public-projection service slice.
  if (!assertion || Array.isArray(assertion) || typeof assertion !== "object")
    throw new Error("FACT_ASSERTION_REQUIRED");
  if (!plainRecordEnvelopeIsSafe(assertion)) throw new Error("FACT_ASSERTION_UNSAFE");
  for (const field of CANONICAL_FACT_REQUIRED_FIELDS)
    if (!ownEnumerableDataDescriptor(assertion, field))
      throw new Error(`FACT_METADATA_REQUIRED:${field}`);
  if (typeof assertion.organization_tenant_id !== "string" || !assertion.organization_tenant_id.trim())
    throw new Error("FACT_METADATA_INVALID:organization_tenant_id");
  if (typeof assertion.field_key !== "string" || !assertion.field_key.trim())
    throw new Error("FACT_METADATA_INVALID:field_key");
  for (const field of ["property_id", "source_evidence_id", "rights_receipt_id"])
    if (typeof assertion[field] !== "string" || !FOUNDATION_UUID_RE.test(assertion[field]))
      throw new Error(`FACT_METADATA_INVALID:${field}`);
  if (!canonicalJsonValueIsSafe(assertion.value)) throw new Error("FACT_METADATA_INVALID:value");
  if (!requiredTimestamp(assertion.observed_at))
    throw new Error("FACT_METADATA_INVALID:observed_at");
  if (!requiredTimestamp(assertion.effective_from))
    throw new Error("FACT_METADATA_INVALID:effective_from");
  if (assertion.effective_to !== null && !requiredTimestamp(assertion.effective_to))
    throw new Error("FACT_METADATA_INVALID:effective_to");
  if (assertion.effective_to !== null &&
      new Date(assertion.effective_to) < new Date(assertion.effective_from))
    throw new Error("FACT_EFFECTIVE_INTERVAL_INVALID");
  if (!FACT_CONFIDENCE.has(assertion.confidence))
    throw new Error("FACT_METADATA_INVALID:confidence");
  if (!FACT_CLASSIFICATION.has(assertion.data_classification))
    throw new Error("FACT_METADATA_INVALID:data_classification");
  if (!FACT_REVIEW_STATE.has(assertion.review_state))
    throw new Error("FACT_METADATA_INVALID:review_state");
  return true;
}

const FOUNDATION_ENTITY_ENUMS = Object.freeze({
  Property: { property_status: ["active", "inactive", "withdrawn", "unknown"] },
  RightsReceipt: { status: ["active", "expired", "revoked", "unknown"] },
  SourceEvidence: {
    evidence_class: ["direct_source", "linked_artifact", "public_mirror", "inference"],
    retrieval_status: ["read", "partial", "inaccessible", "failed"],
    data_classification: ["public", "client_authorized", "internal", "restricted"],
  },
  FieldAssertion: {
    confidence: ["low", "medium", "high", "unknown"],
    data_classification: ["public", "client_authorized", "internal", "restricted"],
    review_state: ["unreviewed", "reviewed", "conflicted", "superseded", "withdrawn"],
  },
  FactConflict: { state: ["open", "resolved", "superseded"] },
  Tour: { tour_status: ["draft", "active", "completed", "cancelled", "archived"] },
  PublicTourProjection: {
    status: ["draft", "qc_blocked", "approved", "published", "superseded", "quarantined", "rolled_back"],
  },
  ProjectionFact: { display_field_key: [...PUBLIC_TOUR_FIELD_KEYS] },
  CheatSheetRevision: { status: ["draft", "saved", "superseded"] },
  ShareGrant: {
    audience: ["client", "internal"],
    status: ["active", "revoked", "expired", "rotated"],
  },
  QualityFinding: {
    artifact_type: ["public_projection", "pdf", "map", "cheat_sheet", "share_grant"],
    severity: ["blocker", "error", "warning", "info"],
    state: ["open", "accepted_risk", "resolved", "superseded"],
  },
  Publication: {
    publication_state: ["draft", "pending_qc", "approved", "published", "quarantined", "rolled_back"],
  },
});
const FOUNDATION_ARRAY_FIELDS = new Set([
  "allowed_field_classes", "allowed_use_classes", "permission_scopes",
]);
const FOUNDATION_OBJECT_FIELDS = new Set(["content", "evidence", "payload"]);
const FOUNDATION_POSITIVE_INTEGER_FIELDS = new Set([
  "receipt_version", "route_version", "route_sequence", "projection_version",
  "revision_number", "grant_version",
]);
const FOUNDATION_TIMESTAMP_FIELDS = new Set([
  "reviewed_at", "effective_at", "expires_at", "revoked_at", "retrieved_at",
  "observed_at", "effective_from", "effective_to", "opened_at", "selected_at",
  "as_of", "created_at", "occurred_at",
]);
const FOUNDATION_DIGEST_FIELDS = new Set([
  "receipt_digest", "content_digest", "assertion_set_digest", "projection_digest",
  "token_digest", "event_digest",
]);
const FOUNDATION_NULLABLE_FIELDS = new Set([
  "expires_at", "revoked_at", "supersedes_receipt_id", "effective_to",
  "rotated_from_grant_id",
]);
const FOUNDATION_UUID_FIELDS = new Set([
  "property_id", "rights_receipt_id", "supersedes_receipt_id", "source_evidence_id",
  "field_assertion_id", "conflict_id", "tour_id", "tour_stop_id", "projection_id",
  "projection_fact_id", "cheat_sheet_revision_id", "share_grant_id",
  "rotated_from_grant_id", "qc_finding_id", "artifact_id", "publication_id",
  "audit_event_id", "entity_id",
]);
const FOUNDATION_TEXT_FIELDS = new Set([
  "organization_tenant_id", "property_status", "provider", "policy_key", "terms_url",
  "reviewer", "intended_use", "status", "stable_locator", "evidence_class",
  "retrieval_status", "rights_provider", "rights_policy_key", "data_classification",
  "field_key", "confidence", "review_state", "state", "tour_name",
  "tour_status", "canonical_dataset_version", "route_label", "display_field_key",
  "editor_actor_id", "audience", "artifact_type", "check_id", "severity",
  "publication_state", "event_type", "entity_type", "actor_id",
]);
const FOUNDATION_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const FOUNDATION_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function validateFoundationEntityFixture(entityName, record, entityContract) {
  if (!record || Array.isArray(record) || typeof record !== "object" ||
      !entityContract || typeof entityContract !== "object")
    throw new Error(`FOUNDATION_ENTITY_REQUIRED:${entityName}`);
  const fields = [...new Set([...(entityContract.identity || []), ...(entityContract.required || [])])];
  for (const field of fields)
    if (!ownEnumerableDataDescriptor(record, field))
      throw new Error(`FOUNDATION_ENTITY_FIELD_REQUIRED:${entityName}.${field}`);
  for (const field of Object.keys(record))
    if (!fields.includes(field))
      throw new Error(`FOUNDATION_ENTITY_FIELD_UNKNOWN:${entityName}.${field}`);
  for (const [field, allowed] of Object.entries(FOUNDATION_ENTITY_ENUMS[entityName] || {}))
    if (!allowed.includes(record[field]))
      throw new Error(`FOUNDATION_ENTITY_ENUM_INVALID:${entityName}.${field}`);
  for (const field of fields) {
    const value = record[field];
    if (value === null && FOUNDATION_NULLABLE_FIELDS.has(field)) continue;
    if (value == null) throw new Error(`FOUNDATION_ENTITY_VALUE_REQUIRED:${entityName}.${field}`);
    if (FOUNDATION_UUID_FIELDS.has(field) &&
        (typeof value !== "string" || !FOUNDATION_UUID_RE.test(value)))
      throw new Error(`FOUNDATION_ENTITY_UUID_INVALID:${entityName}.${field}`);
    if (FOUNDATION_TEXT_FIELDS.has(field) &&
        (typeof value !== "string" || !value.trim() || !postgresTextIsSafe(value)))
      throw new Error(`FOUNDATION_ENTITY_TEXT_INVALID:${entityName}.${field}`);
    if (FOUNDATION_ARRAY_FIELDS.has(field) &&
        (!Array.isArray(value) || value.length === 0 ||
         !Array.from({length: value.length}, (_, index) => index).every(index =>
           {
             const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
             return descriptor && Object.hasOwn(descriptor, "value") &&
               typeof descriptor.value === "string" && descriptor.value.trim() &&
               postgresTextIsSafe(descriptor.value);
           })))
      throw new Error(`FOUNDATION_ENTITY_ARRAY_INVALID:${entityName}.${field}`);
    if (FOUNDATION_OBJECT_FIELDS.has(field) &&
        (Array.isArray(value) || typeof value !== "object" ||
         !canonicalJsonValueIsSafe(value, false)))
      throw new Error(`FOUNDATION_ENTITY_JSON_INVALID:${entityName}.${field}`);
    if (FOUNDATION_POSITIVE_INTEGER_FIELDS.has(field) &&
        (!Number.isInteger(value) || value < 1))
      throw new Error(`FOUNDATION_ENTITY_INTEGER_INVALID:${entityName}.${field}`);
    if (FOUNDATION_TIMESTAMP_FIELDS.has(field) && !requiredTimestamp(value))
      throw new Error(`FOUNDATION_ENTITY_TIMESTAMP_INVALID:${entityName}.${field}`);
    if (FOUNDATION_DIGEST_FIELDS.has(field) &&
        (typeof value !== "string" || !FOUNDATION_DIGEST_RE.test(value)))
      throw new Error(`FOUNDATION_ENTITY_DIGEST_INVALID:${entityName}.${field}`);
  }
  if (entityName === "FieldAssertion" && !canonicalJsonValueIsSafe(record.value))
    throw new Error("FOUNDATION_ENTITY_JSON_INVALID:FieldAssertion.value");
  if (entityName === "FieldAssertion" && record.effective_to !== null &&
      new Date(record.effective_to) < new Date(record.effective_from))
    throw new Error("FOUNDATION_ENTITY_INTERVAL_INVALID:FieldAssertion.effective_to");
  if (entityName === "RightsReceipt" && record.expires_at !== null &&
      new Date(record.expires_at) <= new Date(record.effective_at))
    throw new Error("FOUNDATION_ENTITY_INTERVAL_INVALID:RightsReceipt.expires_at");
  if (entityName === "RightsReceipt" && record.status === "revoked" && record.revoked_at === null)
    throw new Error("FOUNDATION_ENTITY_REVOCATION_INVALID:RightsReceipt.revoked_at");
  if (entityName === "ShareGrant" && record.expires_at !== null &&
      new Date(record.expires_at) <= new Date(record.created_at))
    throw new Error("FOUNDATION_ENTITY_INTERVAL_INVALID:ShareGrant.expires_at");
  if (entityName === "ShareGrant" && record.status === "revoked" && record.revoked_at === null)
    throw new Error("FOUNDATION_ENTITY_REVOCATION_INVALID:ShareGrant.revoked_at");
  if (entityName === "PublicTourProjection" && record.facts_only !== true)
    throw new Error("FOUNDATION_ENTITY_FACTS_ONLY_REQUIRED:PublicTourProjection.facts_only");
  if (entityName === "ShareGrant") {
    const scopes = record.permission_scopes;
    if (Array.from({length: scopes.length}, (_, index) => index).some(index => {
      const descriptor = Object.getOwnPropertyDescriptor(scopes, String(index));
      return !descriptor || !Object.hasOwn(descriptor, "value") ||
        !["view_packet", "view_map"].includes(descriptor.value);
    })) throw new Error("FOUNDATION_ENTITY_SCOPE_INVALID:ShareGrant.permission_scopes");
  }
  if (!canonicalJsonValueIsSafe(record, false))
    throw new Error(`FOUNDATION_ENTITY_SERIALIZATION_INVALID:${entityName}`);
  return true;
}
const requiredTime = (value, error) => {
  if (!requiredTimestamp(value)) throw new Error(error);
  return new Date(value);
};

function revocationApplies(receipt, at, revocations) {
  if (receipt.revoked_at && requiredTimestamp(receipt.revoked_at) &&
      new Date(receipt.revoked_at) <= at) return true;
  return revocations.some(item => {
    const id = item?.rights_receipt_id ?? item?.receipt_id;
    return id === receipt.id && requiredTimestamp(item.revoked_at) &&
      new Date(item.revoked_at) <= at;
  });
}

export function evaluateRightsReceipt(receipt, {
  at, fieldKey = null, useClass, lineage = [], revocations = [],
} = {}) {
  const evaluationTime = requiredTime(at, "RIGHTS_EVALUATION_TIME_REQUIRED");
  if (!receipt || typeof receipt !== "object" || !requiredTimestamp(receipt.effective_at))
    throw new Error("RIGHTS_UNKNOWN");
  if (revocationApplies(receipt, evaluationTime, Array.isArray(revocations) ? revocations : []) ||
      receipt.status === "revoked") throw new Error("RIGHTS_REVOKED");
  if (new Date(receipt.effective_at) > evaluationTime) throw new Error("RIGHTS_NOT_EFFECTIVE");
  if (receipt.status === "expired" ||
      (receipt.expires_at && requiredTimestamp(receipt.expires_at) &&
       new Date(receipt.expires_at) <= evaluationTime)) throw new Error("RIGHTS_EXPIRED");
  if (receipt.status !== "active") throw new Error("RIGHTS_UNKNOWN");
  if (!Array.isArray(receipt.allowed_use_classes) ||
      !receipt.allowed_use_classes.includes(useClass)) throw new Error("RIGHTS_USE_NOT_ALLOWED");
  if (fieldKey != null && (!Array.isArray(receipt.allowed_field_classes) ||
      !(receipt.allowed_field_classes.includes(fieldKey) ||
        receipt.allowed_field_classes.includes("*")))) throw new Error("RIGHTS_FIELD_NOT_ALLOWED");
  if ((Array.isArray(lineage) ? lineage : []).some(item => item &&
      item.organization_tenant_id === receipt.organization_tenant_id &&
      item.provider === receipt.provider && item.policy_key === receipt.policy_key &&
      Number.isInteger(item.receipt_version) && item.receipt_version > receipt.receipt_version &&
      requiredTimestamp(item.effective_at) && new Date(item.effective_at) <= evaluationTime))
    throw new Error("RIGHTS_SUPERSEDED");
  return true;
}

export function validateEvidenceRightsLineage(evidence, assertion, rightsReceipt) {
  if (!evidence || !assertion || !rightsReceipt ||
      evidence.organization_tenant_id !== assertion.organization_tenant_id ||
      assertion.organization_tenant_id !== rightsReceipt.organization_tenant_id ||
      evidence.id !== assertion.source_evidence_id ||
      evidence.rights_receipt_id !== assertion.rights_receipt_id ||
      evidence.rights_receipt_id !== rightsReceipt.id ||
      (evidence.rights_provider != null && evidence.rights_provider !== rightsReceipt.provider) ||
      (evidence.rights_policy_key != null && evidence.rights_policy_key !== rightsReceipt.policy_key) ||
      (evidence.rights_receipt_digest != null &&
       evidence.rights_receipt_digest !== rightsReceipt.receipt_digest))
    throw new Error("EVIDENCE_RIGHTS_LINEAGE_MISMATCH");
  if (!requiredTimestamp(evidence.retrieved_at)) throw new Error("RETRIEVED_AT_REQUIRED");
  if (!requiredTimestamp(assertion.observed_at)) throw new Error("OBSERVED_AT_REQUIRED");
  return true;
}

function publicAssetIsSafe(item) {
  if (!item || Array.isArray(item) || typeof item !== "object") return false;
  const keys = new Set(["asset_ref", "alt", "caption", "source"]);
  return Object.keys(item).every(key => keys.has(key)) &&
    PUBLIC_ASSET_REFERENCE_RE.test(item.asset_ref || "") &&
    Object.entries(item).every(([key, value]) => key === "asset_ref" || typeof value === "string");
}

export function publicValueIsSafe(fieldKey, value) {
  if (["display.name", "display.address"].includes(fieldKey))
    return typeof value === "string" && value.trim().length > 0 && value.trim().length <= 360;
  if (["suite", "property_type", "availability",
    "parking", "access", "source_attribution", "as_of", "caveat"].includes(fieldKey))
    return typeof value === "string";
  if (["size", "asking_economics"].includes(fieldKey))
    return approvedMetricIsSafe(value);
  if (["photos", "floor_plan"].includes(fieldKey))
    return Array.isArray(value) && value.every(publicAssetIsSafe);
  return false;
}

export function canonicalProjectionDigest(input) {
  const projection = input?.projection;
  if (!projection || typeof projection !== "object") throw new Error("PROJECTION_REQUIRED");
  if (!requiredTimestamp(projection.as_of)) throw new Error("PROJECTION_AS_OF_REQUIRED");
  const compareUtf8 = (left, right) =>
    Buffer.compare(Buffer.from(String(left), "utf8"), Buffer.from(String(right), "utf8"));
  const facts = (Array.isArray(input?.facts) ? input.facts : []).slice().sort((left, right) =>
    compareUtf8(left.property_id || "", right.property_id || "") ||
    compareUtf8(left.display_field_key || "", right.display_field_key || "") ||
    compareUtf8(left.field_assertion_id || "", right.field_assertion_id || ""));
  const mapPoints = (Array.isArray(input?.map_points) ? input.map_points : []).slice().sort((left, right) =>
    compareUtf8(left.property_id || "", right.property_id || "") ||
    compareUtf8(left.coordinate_candidate_id || "", right.coordinate_candidate_id || "") ||
    compareUtf8(left.entrance_verification_receipt_id || "", right.entrance_verification_receipt_id || ""));
  const encoded = value => Buffer.from(String(value), "utf8").toString("base64");
  const lines = [
    "public-tour-projection-digest.v2",
    encoded(projection.organization_tenant_id),
    String(projection.tour_id),
    String(projection.id),
    String(projection.projection_version),
    String(projection.route_version),
    new Date(projection.as_of).toISOString(),
    ...facts.map(fact => [
      fact.property_id,
      fact.field_assertion_id,
      fact.route_version,
      encoded(fact.display_field_key),
    ].join("|")),
    ...(mapPoints.length ? ["map", ...mapPoints.map(point => [
      point.property_id,
      point.coordinate_candidate_id,
      point.entrance_verification_receipt_id,
      point.route_version,
    ].join("|"))] : []),
  ];
  return "sha256:" + sha256(lines.join("\n"));
}

export function assertProjectionDigest(input, claimedDigest = input?.projection?.projection_digest) {
  if (canonicalProjectionDigest(input) !== claimedDigest)
    throw new Error("PROJECTION_DIGEST_MISMATCH");
  return true;
}

export function validateProjectionDraft(projection) {
  if (!projection || projection.status !== "draft")
    throw new Error("PROJECTION_STATUS_DRAFT_REQUIRED");
  return true;
}

function normalizeProjectionOptions(receiptLineage, evidence, revocations) {
  if (Array.isArray(receiptLineage)) return { lineage: receiptLineage, evidence, revocations };
  if (receiptLineage && typeof receiptLineage === "object") return {
    lineage: receiptLineage.lineage ?? [],
    evidence: receiptLineage.evidence ?? evidence,
    revocations: receiptLineage.revocations ?? revocations,
  };
  return { lineage: [], evidence, revocations };
}

export function validateProjectionFact(fact, assertion, membership, projection, rightsReceipt,
  receiptLineage = [], evidence = null, revocations = []) {
  if (!PUBLIC_TOUR_FIELD_KEYS.has(fact.display_field_key))
    throw new Error("PUBLIC_FIELD_NOT_ALLOWLISTED");
  if (!projection || !rightsReceipt) throw new Error("PROJECTION_RIGHTS_REQUIRED");
  if ([assertion, membership, projection, rightsReceipt]
    .some(item => fact.organization_tenant_id !== item.organization_tenant_id))
    throw new Error("TENANT_SCOPE_REFUSED");
  if (fact.projection_id !== projection.id || membership.tour_id !== projection.tour_id)
    throw new Error("PROJECTION_BINDING_REFUSED");
  if (fact.property_id !== assertion.property_id || fact.property_id !== membership.property_id)
    throw new Error("PROJECTION_PROPERTY_MISMATCH");
  if (fact.field_assertion_id !== assertion.id || assertion.review_state !== "reviewed" ||
      assertion.data_classification !== "public") throw new Error("PUBLIC_ASSERTION_REQUIRED");
  if (fact.display_field_key !== assertion.field_key)
    throw new Error("PUBLIC_FIELD_RELABEL_REFUSED");
  if (membership.route_version !== fact.route_version ||
      projection.route_version !== fact.route_version) throw new Error("ROUTE_VERSION_MISMATCH");
  if (!requiredTimestamp(projection.as_of) || !requiredTimestamp(membership.selected_at) ||
      !requiredTimestamp(assertion.effective_from) ||
      (assertion.observed_at != null && !requiredTimestamp(assertion.observed_at)) ||
      (assertion.effective_to != null && !requiredTimestamp(assertion.effective_to)))
    throw new Error("PUBLIC_ASSERTION_NOT_EFFECTIVE");
  const asOf = new Date(projection.as_of);
  if (new Date(membership.selected_at) > asOf || new Date(assertion.effective_from) > asOf ||
      (assertion.effective_to && new Date(assertion.effective_to) <= asOf))
    throw new Error("PUBLIC_ASSERTION_NOT_EFFECTIVE");
  if (assertion.rights_receipt_id !== rightsReceipt.id) throw new Error("PUBLIC_RIGHTS_REQUIRED");
  const options = normalizeProjectionOptions(receiptLineage, evidence, revocations);
  if (options.evidence) validateEvidenceRightsLineage(options.evidence, assertion, rightsReceipt);
  try {
    evaluateRightsReceipt(rightsReceipt, {
      at: projection.as_of, fieldKey: assertion.field_key,
      useClass: "client_public_display", lineage: options.lineage,
      revocations: options.revocations,
    });
  } catch (error) {
    if (error.message === "RIGHTS_SUPERSEDED") throw new Error("PUBLIC_RIGHTS_SUPERSEDED");
    throw new Error("PUBLIC_RIGHTS_REQUIRED");
  }
  if (!publicValueIsSafe(assertion.field_key, assertion.value)) {
    if (["photos", "floor_plan"].includes(assertion.field_key))
      throw new Error("PUBLIC_ASSET_REFERENCE_REQUIRED");
    throw new Error("PUBLIC_VALUE_UNSAFE");
  }
  return true;
}

const mapById = values =>
  new Map((Array.isArray(values) ? values : []).map(value => [value.id, value]));

export function validateProjectionComplete({
  projection, memberships = [], facts = [], assertions = [], evidence = [], rights = [],
  lineage = [], revocations = [], map_points = [], requiredFieldKeys = REQUIRED_PUBLIC_PROPERTY_FIELDS,
} = {}) {
  if (!projection || projection.status !== "approved")
    throw new Error("PROJECTION_STATUS_APPROVED_REQUIRED");
  const seal = projection.seal_receipt;
  if (!seal || seal.organization_tenant_id !== projection.organization_tenant_id ||
      seal.projection_id !== projection.id || seal.sealed_state !== "approved" ||
      !requiredTimestamp(seal.sealed_at) ||
      seal.canonical_projection_digest !== projection.projection_digest ||
      !/^sha256:[a-f0-9]{64}$/.test(seal.receipt_digest || "") ||
      typeof seal.actor_id !== "string" || !seal.actor_id)
    throw new Error("PROJECTION_SEAL_REQUIRED");
  if (!requiredTimestamp(projection.as_of) || projection.facts_only !== true)
    throw new Error("PROJECTION_INCOMPLETE");
  const selected = memberships.filter(item =>
    item.organization_tenant_id === projection.organization_tenant_id &&
    item.tour_id === projection.tour_id && item.route_version === projection.route_version &&
    requiredTimestamp(item.selected_at) &&
    new Date(item.selected_at) <= new Date(projection.as_of));
  if (!selected.length || selected.length !== memberships.length)
    throw new Error("PROJECTION_INCOMPLETE");
  const assertionById = mapById(assertions);
  const evidenceById = mapById(evidence);
  const rightsById = mapById(rights);
  for (const membership of selected) {
    const propertyFacts = facts.filter(fact => fact.property_id === membership.property_id);
    if (!requiredFieldKeys.every(fieldKey =>
      propertyFacts.some(fact => fact.display_field_key === fieldKey)))
      throw new Error("PROJECTION_INCOMPLETE");
    for (const fact of propertyFacts) {
      const assertion = assertionById.get(fact.field_assertion_id);
      const source = assertion && evidenceById.get(assertion.source_evidence_id);
      const receipt = assertion && rightsById.get(assertion.rights_receipt_id);
      if (!assertion || !source || !receipt) throw new Error("PROJECTION_INCOMPLETE");
      validateProjectionFact(fact, assertion, membership, projection, receipt, {
        lineage, evidence: source, revocations,
      });
    }
  }
  const digestFacts = facts.map(fact => {
    const assertion = assertionById.get(fact.field_assertion_id) || {};
    return {
      ...fact, value: assertion.value, source_evidence_id: assertion.source_evidence_id,
      rights_receipt_id: assertion.rights_receipt_id, observed_at: assertion.observed_at,
      effective_from: assertion.effective_from, effective_to: assertion.effective_to,
    };
  });
  assertProjectionDigest({ projection, memberships, facts: digestFacts, map_points });
  return true;
}
