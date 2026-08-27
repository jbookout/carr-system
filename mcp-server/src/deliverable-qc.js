/** Pure inspection. A clean result means only that no configured blocker was
 * found; it is intentionally not an approval, publication, or dismissal. */

export const DELIVERABLE_QC_VERSION = "1.1.0";
export const DELIVERABLE_QC_RULESET_VERSION = "1.1.0";

const LEAKAGE_KEYWORDS = /(?:\b(?:provider|rights?|evidence|verifier|internal(?:\s+id)?|access\s*notes?|contact)\b|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:\+?\d[\d .()-]{7,}\d)\b)/i;
const UNSAFE_URL = /\b(?:javascript|data|file|vbscript):/i;
const URL_ATTRIBUTE = /\b(?:href|src)\s*=\s*["']\s*([^"']+)/gi;
const escapeRegExp = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export class DeliverableQcError extends Error {
  constructor(code, details = {}) { super(code); this.name = "DeliverableQcError"; this.code = code; this.details = details; }
}

function finding(ruleId, category, message, details = {}) {
  return Object.freeze({ findingVersion: DELIVERABLE_QC_VERSION, ruleId, category, severity: "block", message, details: Object.freeze(details) });
}
function required(value, field) {
  if (typeof value !== "string" || !value.trim()) throw new DeliverableQcError("deliverable_qc_invalid_input", { field });
  return value;
}
function allMatches(html, expression) { return [...html.matchAll(expression)]; }
function escapedHtmlText(value) { return String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]); }
function containsText(html, text) { return [String(text), escapedHtmlText(text)].some(value => new RegExp(escapeRegExp(value), "i").test(html)); }
function scalarFacts(value) {
  if (value === null || value === undefined) return [];
  if (["string", "number", "boolean"].includes(typeof value)) return [String(value)];
  if (Array.isArray(value)) return value.flatMap(scalarFacts);
  if (typeof value === "object") return Object.values(value).flatMap(scalarFacts);
  return [];
}
function attribute(openTag, name) {
  const match = new RegExp(`\\b${escapeRegExp(name)}=["']([^"']+)["']`, "i").exec(openTag);
  return match?.[1] || null;
}
function hasUnsafeUrl(html) {
  return UNSAFE_URL.test(html) || allMatches(html, URL_ATTRIBUTE).some(match => !match[1].startsWith("https://") && !match[1].startsWith("#"));
}

export const TOUR_PACKET_QC_ADAPTER = Object.freeze({
  artifactType: "tour-packet",
  pagePattern: /<section\b([^>]*\bdata-deliverable-page=["']property["'][^>]*)>([\s\S]*?)<\/section>/gi,
  markerAttribute: "data-property-marker",
  identityAttribute: "data-property-ref",
  sequenceAttribute: "data-route-sequence",
  requiredFactFields: ["route_label", "name", "address", "as_of", "caveat"],
  displayedFactFields: ["suite", "property_type", "size", "asking_economics", "availability", "parking"],
  brand: "CARR",
  brandColors: ["#002F6C", "#F57F29"],
});

/** A non-Tour adapter proves QC is not coupled to Tour data or markup. */
export const GENERIC_DELIVERABLE_QC_ADAPTER = Object.freeze({
  artifactType: "client-brief",
  pagePattern: /<article\b([^>]*\bdata-deliverable-page=["']brief["'][^>]*)>([\s\S]*?)<\/article>/gi,
  markerAttribute: "data-item-marker",
  requiredFactFields: ["title"],
  brand: "CARR",
});

function adapterFor(input) {
  if (input.adapter) return input.adapter;
  if (input.artifactType === TOUR_PACKET_QC_ADAPTER.artifactType) return TOUR_PACKET_QC_ADAPTER;
  return GENERIC_DELIVERABLE_QC_ADAPTER;
}
function renderedPages(html, adapter) {
  return allMatches(html, adapter.pagePattern).map(match => ({ openTag: match[1], content: match[2], marker: attribute(match[1], adapter.markerAttribute), identity: adapter.identityAttribute ? attribute(match[1], adapter.identityAttribute) : null, sequence: adapter.sequenceAttribute ? attribute(match[1], adapter.sequenceAttribute) : null }));
}
function orderedItems(items, artifactType) {
  return artifactType === "tour-packet" ? [...items].sort((left, right) => left.route_sequence - right.route_sequence) : items;
}
function expectedMarker(item, index, artifactType) {
  if (artifactType === "tour-packet") return `property-${item.route_sequence}-${item.property_ref.slice("property:public:".length)}`;
  return `item-${String(index + 1).padStart(3, "0")}`;
}

/**
 * Inspect a rendered artifact. Input is deliberately generic:
 * { artifactType, html, facts: { as_of, caveat, items|properties }, expected }.
 */
export function inspectDeliverable(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new DeliverableQcError("deliverable_qc_invalid_input", { field: "input" });
  const html = required(input.html, "html");
  const adapter = adapterFor(input);
  const artifactType = input.artifactType || adapter.artifactType;
  const expected = input.expected && typeof input.expected === "object" ? input.expected : {};
  const facts = input.facts && typeof input.facts === "object" ? input.facts : {};
  const rawItems = facts.properties || facts.items || [];
  const findings = [];

  if (LEAKAGE_KEYWORDS.test(html)) findings.push(finding("QC-LEAKAGE-001", "leakage", "Deliverable contains a forbidden contact, internal, provider, rights, evidence, verifier, or access-note token."));
  if (hasUnsafeUrl(html)) findings.push(finding("QC-URL-001", "unsafe_url", "Deliverable contains an unsafe URL or URL scheme."));

  const asOf = expected.asOf || facts.as_of;
  const caveat = expected.caveat || facts.caveat;
  if (!asOf || !containsText(html, asOf)) findings.push(finding("QC-FACT-001", "required_facts", "Required as-of value is absent from the rendered deliverable.", { asOf: asOf || null }));
  if (!caveat || !containsText(html, caveat)) findings.push(finding("QC-FACT-002", "required_facts", "Required caveat is absent from the rendered deliverable.", { caveat: caveat || null }));
  if (!Array.isArray(rawItems)) findings.push(finding("QC-FACT-003", "required_facts", "Facts collection is missing or invalid."));

  const items = Array.isArray(rawItems) ? orderedItems(rawItems, artifactType) : [];
  const pages = renderedPages(html, adapter);
  const expectedMarkers = expected.markers || items.map((item, index) => expectedMarker(item, index, artifactType));
  const expectedPageCount = expected.pageCount ?? items.length;
  if (pages.length !== expectedPageCount || pages.length !== items.length)
    findings.push(finding("QC-PAGE-001", "page_count", "Rendered page-section count must exactly equal property/item count.", { expected: expectedPageCount, facts: items.length, actual: pages.length }));
  const markers = pages.map(page => page.marker);
  if (markers.length !== expectedMarkers.length || new Set(markers).size !== markers.length || markers.some((marker, index) => marker !== expectedMarkers[index]))
    findings.push(finding("QC-PAGE-002", "unique_marker", "Page markers are missing, duplicated, or out of canonical order.", { expected: expectedMarkers, actual: markers }));
  const hasExtraPage = allMatches(html, /<(?:section|article)\b[^>]*\bdata-deliverable-page=["']([^"']+)["'][^>]*>/gi).some(match => match[1] !== (artifactType === "tour-packet" ? "property" : "brief")) || /<(?:section|article)\b[^>]*(?:\b(?:cover|overview)\b)[^>]*>/i.test(html);
  if (hasExtraPage) findings.push(finding("QC-PAGE-003", "page_structure", "Deliverable contains an extra cover or overview page/section."));

  if (Array.isArray(rawItems)) {
    for (const [index, item] of items.entries()) {
      const page = pages[index];
      if (!page) continue;
      for (const field of adapter.requiredFactFields || []) {
        const value = item?.[field];
        if (typeof value !== "string" || !value.trim() || !containsText(page.content, value))
          findings.push(finding("QC-PARITY-002", "per_page_facts", "A page does not contain its own required fact.", { index, field }));
      }
      for (const field of adapter.displayedFactFields || []) {
        const values = scalarFacts(item?.[field]);
        if (values.some(value => !containsText(page.content, value)))
          findings.push(finding("QC-PARITY-003", "per_page_facts", "A page does not contain all of its own displayed facts.", { index, field }));
      }
    }
  }

  if (artifactType === "tour-packet" && Array.isArray(rawItems)) {
    const expectedRefs = items.map(item => item?.property_ref);
    const actualRefs = pages.map(page => page.identity);
    if (expectedRefs.some(ref => typeof ref !== "string") || new Set(actualRefs).size !== actualRefs.length || actualRefs.length !== expectedRefs.length || actualRefs.some((ref, index) => ref !== expectedRefs[index]))
      findings.push(finding("QC-IDENTITY-001", "public_identity", "Each expected public property_ref must appear exactly once on its page.", { expected: expectedRefs, actual: actualRefs }));
    const expectedSequences = items.map(item => String(item?.route_sequence));
    const actualSequences = pages.map(page => page.sequence);
    if (new Set(actualSequences).size !== actualSequences.length || actualSequences.some((sequence, index) => sequence !== expectedSequences[index]))
      findings.push(finding("QC-IDENTITY-002", "route_order", "Rendered public property identity does not match immutable route order.", { expected: expectedSequences, actual: actualSequences }));
  }

  const brand = expected.brand || adapter.brand;
  if (!new RegExp(`\\bdata-brand=["']${escapeRegExp(brand)}["']`, "i").test(html)) findings.push(finding("QC-BRAND-001", "brand", "Required brand marker is absent.", { brand }));
  const brandColors = expected.brandColors || adapter.brandColors || [];
  if (brandColors.some(color => !containsText(html, color))) findings.push(finding("QC-BRAND-002", "brand", "Required brand color is absent.", { brandColors }));
  if (artifactType === "tour-packet" && (!/@page\s*{[^}]*size:\s*Letter/i.test(html) || !/break-after:\s*page/i.test(html) || !/page-break-after:\s*always/i.test(html)))
    findings.push(finding("QC-LAYOUT-001", "layout", "Letter page sizing or deterministic property page breaks are absent."));
  if (!/<html\b[^>]*\blang=["'][^"']+["']/i.test(html) || !/<title>[^<]+<\/title>/i.test(html) || (html.match(/<main\b/gi) || []).length !== 1 || /<img\b(?:(?!\balt=)[^>])*?>/i.test(html))
    findings.push(finding("QC-A11Y-001", "accessibility", "Deliverable is missing language, title, exactly one main landmark, or image alt text."));

  return Object.freeze({
    qcVersion: DELIVERABLE_QC_VERSION,
    rulesetVersion: DELIVERABLE_QC_RULESET_VERSION,
    artifactType,
    blocked: findings.length > 0,
    disposition: "review_required",
    canApprove: false,
    canPublish: false,
    canSelfDismiss: false,
    findings: Object.freeze(findings),
  });
}
