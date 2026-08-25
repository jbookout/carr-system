// Foundation-level validation only. Rendering is intentionally elsewhere.
export const PUBLIC_TOUR_FIELD_KEYS = new Set([
  "display.name", "display.address", "suite", "property_type", "size",
  "asking_economics", "availability", "parking", "access", "photos",
  "floor_plan", "source_attribution", "as_of", "caveat"
]);

export function validateProjectionFact(fact, assertion, membership) {
  if (!PUBLIC_TOUR_FIELD_KEYS.has(fact.display_field_key)) throw new Error("PUBLIC_FIELD_NOT_ALLOWLISTED");
  if (fact.organization_tenant_id !== assertion.organization_tenant_id || fact.organization_tenant_id !== membership.organization_tenant_id) throw new Error("TENANT_SCOPE_REFUSED");
  if (fact.property_id !== assertion.property_id || fact.property_id !== membership.property_id) throw new Error("PROJECTION_PROPERTY_MISMATCH");
  if (fact.field_assertion_id !== assertion.id || assertion.review_state !== "reviewed" || assertion.data_classification !== "public") throw new Error("PUBLIC_ASSERTION_REQUIRED");
  if (fact.display_field_key !== assertion.field_key) throw new Error("PUBLIC_FIELD_RELABEL_REFUSED");
  if (membership.route_version !== fact.route_version) throw new Error("ROUTE_VERSION_MISMATCH");
  return true;
}
