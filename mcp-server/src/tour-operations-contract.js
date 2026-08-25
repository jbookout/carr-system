// Foundation-level validation only. Rendering is intentionally elsewhere.
export const PUBLIC_TOUR_FIELD_KEYS = new Set(["display.name", "display.address", "suite", "property_type", "size", "asking_economics", "availability", "parking", "access", "photos", "floor_plan", "source_attribution", "as_of", "caveat"]);
const scalar = value => value === null || ["string", "number", "boolean"].includes(typeof value);
const objectWithOnly = (value, keys) => value && !Array.isArray(value) && typeof value === "object" && Object.entries(value).every(([key, nested]) => keys.has(key) && scalar(nested));

export function publicValueIsSafe(fieldKey, value) {
  if (["display.name", "display.address", "suite", "property_type", "availability", "parking", "access", "source_attribution", "as_of", "caveat"].includes(fieldKey)) return typeof value === "string";
  if (["size", "asking_economics"].includes(fieldKey)) return objectWithOnly(value, new Set(["value", "unit", "min", "max", "currency", "period", "label"]));
  if (["photos", "floor_plan"].includes(fieldKey)) return Array.isArray(value) && value.every(item => objectWithOnly(item, new Set(["url", "alt", "caption", "source"])) && Object.values(item).every(value => typeof value === "string"));
  return false;
}

export function validateProjectionFact(fact, assertion, membership, projection, rightsReceipt, receiptLineage = []) {
  if (!PUBLIC_TOUR_FIELD_KEYS.has(fact.display_field_key)) throw new Error("PUBLIC_FIELD_NOT_ALLOWLISTED");
  if (!projection || !rightsReceipt) throw new Error("PROJECTION_RIGHTS_REQUIRED");
  if ([assertion, membership, projection, rightsReceipt].some(item => fact.organization_tenant_id !== item.organization_tenant_id)) throw new Error("TENANT_SCOPE_REFUSED");
  if (fact.property_id !== assertion.property_id || fact.property_id !== membership.property_id) throw new Error("PROJECTION_PROPERTY_MISMATCH");
  if (fact.field_assertion_id !== assertion.id || assertion.review_state !== "reviewed" || assertion.data_classification !== "public") throw new Error("PUBLIC_ASSERTION_REQUIRED");
  if (fact.display_field_key !== assertion.field_key) throw new Error("PUBLIC_FIELD_RELABEL_REFUSED");
  if (membership.route_version !== fact.route_version || projection.route_version !== fact.route_version) throw new Error("ROUTE_VERSION_MISMATCH");
  const asOf = new Date(projection.as_of), starts = new Date(assertion.effective_from), ends = assertion.effective_to && new Date(assertion.effective_to);
  if (Number.isNaN(asOf) || starts > asOf || (ends && ends <= asOf)) throw new Error("PUBLIC_ASSERTION_NOT_EFFECTIVE");
  if (rightsReceipt.status !== "active" || rightsReceipt.revoked_at || new Date(rightsReceipt.effective_at) > asOf || (rightsReceipt.expires_at && new Date(rightsReceipt.expires_at) <= asOf) || !rightsReceipt.allowed_use_classes.includes("client_public_display") || !(rightsReceipt.allowed_field_classes.includes(assertion.field_key) || rightsReceipt.allowed_field_classes.includes("*"))) throw new Error("PUBLIC_RIGHTS_REQUIRED");
  if (receiptLineage.some(item => item.organization_tenant_id === rightsReceipt.organization_tenant_id && item.policy_key === rightsReceipt.policy_key && item.receipt_version > rightsReceipt.receipt_version && new Date(item.effective_at) <= asOf)) throw new Error("PUBLIC_RIGHTS_SUPERSEDED");
  if (!publicValueIsSafe(assertion.field_key, assertion.value)) throw new Error("PUBLIC_VALUE_UNSAFE");
  return true;
}
