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
  if (["display.name", "display.address", "suite", "property_type", "availability",
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
  lineage = [], revocations = [], requiredFieldKeys = REQUIRED_PUBLIC_PROPERTY_FIELDS,
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
  assertProjectionDigest({ projection, memberships, facts: digestFacts });
  return true;
}
