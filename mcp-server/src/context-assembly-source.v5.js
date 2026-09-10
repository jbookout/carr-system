// DoctorCRE v5 slice V5-F05: the bounded, read-only F01 -> F05 source adapter.
//
// WHAT THIS IS. context-assembly.v5.js takes `records`, `queries` and `sources`
// from its caller and runs no query of its own (`no_retrieval_executor`). This
// module fills exactly one narrow part of that seam: given a caller-declared,
// deterministic selection of STORED F01 ARTIFACTS, it reads them through the
// reviewed F01 store adapter's ONE read operation, maps the verified envelopes
// into F05 records, and — only when every selected thing mapped — freezes the
// request and calls the existing assembler. It runs no SQL of its own, opens no
// connection, registers no tool, writes nothing and invents no attestation.
//
// WHAT IT IS NOT, said first because the interesting part of this seam is what it
// REFUSES to produce:
//
//   * IT IS NOT A RETRIEVAL PLANNER. The selection is the caller's decision. This
//     module turns a selection into records; it does not decide what is relevant,
//     does not search, and does not widen a selection it was handed.
//   * IT IS NOT A RULE-UNIVERSE READER. The compiled universe still arrives from
//     the caller, because the guidance store carries no per-rule provenance,
//     rule_class, typed trigger or control_effect to compile one from. That gap
//     is `no_rule_universe_reader` below and this module does not paper over it.
//   * IT IS NOT A VERIFIER. Every answer is `reproducible_proposal`,
//     `authenticated: false`, `trust_anchor: null`. authenticateRuntimeProjection
//     is deliberately NOT called: it takes a verifier attestation, and the only
//     way this module could supply one is by minting it, which is the exact
//     self-issued credential that function's own header refuses to honour. The
//     manifest is re-checked with verifyContextManifest instead, which recomputes
//     a digest and claims nothing about a running system.
//   * IT IS NOT A LINEAGE ORACLE. See the three named blockers below.
//
// THE THREE THINGS F01 CANNOT ANSWER TODAY, and what this module does instead of
// guessing. Each is a MISSING FIELD, not a build task deferred out of laziness:
//
//   1. ORIGIN IS DERIVABLE FOR TWO EVIDENCE CLASSES AND NO MORE. Q068's origins
//      are a closed set; F01 stores `source_system`/`source_class` as
//      unconstrained external idents and no registry maps one to an origin. Only
//      `corporate_document_bytes` and `corporate_mailbox_item` name a single
//      Q068 origin without a judgement call. `corporate_record_export`,
//      `corporate_field_snapshot` and `corporate_report_render` are returned
//      UNMAPPED, and an unmapped selector blocks assembly for the whole request
//      rather than being quietly dropped from a manifest that then reads as
//      complete.
//   2. ARTIFACT VERSION IS FREE TEXT. `native_version` is assertSafeText, so
//      there is no numeric revision to read. F05's `version` is a safe integer
//      >= 1 used for identity and drift comparison. The ONLY mapping taken here
//      is the lossless one: a canonical decimal string maps to the integer it
//      renders as, and every other token — "v2", "02", "2.0", "2024-05-01" — is
//      unmapped. No default, no ordinal, no invented "1". The mapping is an
//      identity-preserving lexical one and is NOT an ordering claim: F01 gives an
//      artifact's native_version no comparator at all.
//   3. DOCUMENT PROVENANCE IS NOT READABLE THROUGH A REGISTERED READ KIND. The
//      three-valued statement (`derived_from_stored_artifact`,
//      `original_first_party`, `legacy_provenance_unknown`) lives behind
//      ops.f01_document_version_source, which is not in V5_F01_READ_KINDS. A
//      stored document version also carries no source-observed instant at all —
//      only `recorded_at`, which is CUSTODY. So document selectors are refused
//      with a named reason and NO read is issued for them: this module does not
//      reach for an unregistered function to fill a field it is missing, and it
//      never substitutes a custody stamp for an observation.
//
// AND THE ONE THING NOBODY MAY INFER FROM SILENCE. ops.f01_derivative_coverage
// answers `unknown` for every artifact by design, and an empty link set is what
// an artifact with no derivatives and an artifact whose producers registered
// nothing both look like. This module carries that answer through verbatim and
// consumes NO OTHER STATE: a row claiming coverage is established — or claiming
// it while its own exhaustiveness flag stays false — is refused rather than read
// past. Registered links are reported as OBSERVATIONS and are never mapped into
// records, because a link row carries no version and no observed instant for the
// derivative it names. Every mapped record takes an EXTERNAL origin, so F05
// labels it `untrusted_external` whatever F01's own taint class says; taint is
// never lowered here and `record_layer` is never emitted.
//
// AND THE LINEAGE STATE THAT GOES WITH IT. A mapped record declares the kernel's
// `unknown_upstream`, never `primary`: F01 shows what was registered as derived
// FROM an artifact and has no read for what the artifact was itself derived from,
// so "derived from nothing" is a claim this adapter cannot make. The kernel
// carries that state onto the record, into the taint lineage and into the
// manifest, where it blocks the manifest's OWN consequential action and leaves
// marked read-only exploration open.
//
// WHICH SOURCE ANSWERED, and it is not the corporate system. `sources[]` names
// one stable seat — F01's stored artifact copy — because that copy is the only
// thing this adapter reached. The external system the bytes were captured from is
// kept as evidence on the observation and inside the record's `evidence_ref`;
// nothing here contacted it, so nothing here reports its availability.
//
// FRESHNESS AND THE CLOCK. `now` is the server instant F01 returned with the
// read, never a caller value; `observed_at` is the artifact's own
// SOURCE-OBSERVED instant out of its digest-bound preimage, never
// `recorded_at`. Importing an old artifact leaves it old. No `max_age_seconds`
// is invented, so nothing here manufactures a freshness verdict the caller never
// asked for.
//
// TOKENS. `estimated_tokens` is required by F05 and cannot be invented: this
// projection carries METADATA ONLY and no content, so every mapped record
// contributes zero content tokens, which is measurement rather than estimation.
// Because that is only true for a metadata projection, a caller `budget` is
// refused by name: a budget verdict computed against token counts that describe
// no delivered content would be a number nobody can act on.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_F01_READ_KINDS,
  V5_F01_STORE_SCHEMA_VERSION,
} from "./record-source-authority-store.v5.js";
import { V5_F05_GUARDS, V5F05Error } from "./rule-applicability.v5.js";
import {
  V5_F05_AUTHORITY_BEARING_RECORD_KINDS,
  V5_F05_EXTERNAL_ORIGINS,
  V5_F05_MANIFEST_SCHEMA_VERSION,
  V5_F05_RECORD_KINDS,
  V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND,
  assembleContextManifest,
  freezeAssemblyInput,
  verifyContextManifest,
} from "./context-assembly.v5.js";

export { V5F05Error };

const {
  deepFreeze, snapshot, isPlainObject,
  assertExternalIdent, assertDigestRef, assertInstant, assertSafeInteger,
} = V5_F05_GUARDS;

export const V5_F05_SOURCE_SCHEMA_VERSION =
  "doctorcre-v5-f05-context-assembly-source.v1";
export const V5_F05_SOURCE_PROJECTION_SCHEMA_VERSION =
  "doctorcre-v5-f05-source-projection.v1";
export const V5_F05_SOURCE_ASSEMBLY_SCHEMA_VERSION =
  "doctorcre-v5-f05-source-assembly.v1";

/** One selection may name at most this many stored records. */
export const V5_F05_SOURCE_MAX_SELECTION = 32;

/** The only F01 read kind this module can turn into an F05 record. */
export const V5_F05_SOURCE_MAPPABLE_READ_KINDS = deepFreeze(["artifact"]);

/**
 * The two evidence classes whose Q068 origin and F05 record kind are decided by
 * the stored value alone. The other three registered classes name no single
 * origin and are unmapped rather than labelled.
 */
export const V5_F05_SOURCE_EVIDENCE_CLASS_MAP = deepFreeze({
  corporate_document_bytes: { origin: "document", record_kind: "document" },
  corporate_mailbox_item: { origin: "email", record_kind: "message" },
});

/**
 * The one derivative-coverage state this module consumes. F01 produces exactly
 * this one and says why the other is unreachable; anything else is refused rather
 * than carried, so no future row can hand a completeness claim through this seam.
 */
export const V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE = "unknown";

/** Every reason a selector can fail to become a record. Closed, so it is testable. */
export const V5_F05_SOURCE_UNMAPPED_REASONS = deepFreeze([
  "artifact_content_digest_unreadable",
  "artifact_native_version_not_canonical_decimal",
  "artifact_not_stored",
  "artifact_observed_after_read_instant",
  "artifact_observed_at_unreadable",
  "artifact_provenance_incomplete",
  "artifact_source_system_not_a_usable_source_id",
  "derivative_record_not_readable_through_registered_read_kinds",
  "document_provenance_not_readable_through_registered_read_kinds",
  "f05_record_guard_refused",
  "origin_not_derivable_from_evidence_class",
  "read_kind_not_mappable_to_f05_record",
]);

/** What the caller may state about the task; every other request field is derived. */
export const V5_F05_SOURCE_TEMPLATE_KEYS = deepFreeze([
  "task", "controls", "universe", "semantic_candidates", "enforcement_evidence_policy",
]);

/**
 * What a template may NOT state, refused BY NAME so the refusal reports the
 * attempt. `records`, `queries` and `sources` are the whole point: a caller that
 * could add one could put a record of its own choosing — at an id of its own
 * choosing — beside the store's answer and have the manifest carry both as
 * though F01 had returned them.
 */
export const V5_F05_SOURCE_REFUSED_TEMPLATE_KEYS = deepFreeze([
  "actor", "budget", "mode", "now", "queries", "records", "schema_version",
  "sources", "tenant",
]);

/** The one mode this module assembles in. A consequential proposal is not offered. */
export const V5_F05_SOURCE_MODE = "read_only_exploration";

/** The F05 retrieval class every mapped record carries: this adapter's own path. */
export const V5_F05_SOURCE_RETRIEVAL_CLASS = "f01_read:artifact";
const ARTIFACT_RETRIEVAL_CLASS = V5_F05_SOURCE_RETRIEVAL_CLASS;

/**
 * THE SEAT THAT ACTUALLY ANSWERED, and it is not the corporate system.
 *
 * `sources[].state: "available"` is a claim about a source, and the source this
 * adapter reached is F01's stored copy — one row that was captured at some past
 * instant and has been immutable since. Naming the artifact's `source_system`
 * there said Outlook or Salesforce answered, which nothing here contacted: a
 * mailbox that has been unreachable for a month produces exactly the same
 * `available` from a stored artifact. So every mapped record cites ONE stable
 * namespaced seat, and the external system it was captured from is kept as
 * EVIDENCE — on the observation, and reachable through the record's own
 * `evidence_ref` — rather than as a liveness claim.
 */
export const V5_F05_SOURCE_STORED_COPY_SOURCE_ID = "f01:stored_artifact";
export const V5_F05_SOURCE_STORED_COPY_NOTE =
  "F01's stored corporate-artifact copy answered this read. The external source "
  + "system was not contacted and its availability was not checked.";

const SELECTOR_KEYS = Object.freeze(["kind", "artifact_digest", "document_id"]);
const REQUEST_KEYS = Object.freeze(["schema_version", "selection"]);
const ASSEMBLY_KEYS = Object.freeze(["projection", "template"]);

// A canonical decimal, and nothing else. No leading zero, no sign, no separator,
// no exponent, and short enough that Number() is exact.
const CANONICAL_DECIMAL = /^[1-9][0-9]{0,14}$/;

export class V5F05SourceError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5F05SourceError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5F05SourceError(code, message, detail);
}

function assertClosed(object, allowed, required, path) {
  if (!isPlainObject(object)) fail("invalid_shape", `${path} must be a plain object`, { path });
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
  for (const key of required) {
    if (object[key] === undefined || object[key] === null) {
      fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
  }
  return object;
}

/** Two objects hold the same bytes, by the canonicalization every digest here uses. */
function sameBytes(a, b) {
  return canonicalJson(a) === canonicalJson(b);
}

// ---------------------------------------------------------------------------
// The read envelope, recomputed rather than believed.
//
// F01 already recomputes every digest inside PostgreSQL and says so
// (`integrity: "recomputed_not_trusted"`). This module recomputes the ones it
// can from the bytes it was handed anyway, because "the row hashed correctly in
// the database" and "the object in front of me is that row" are two different
// facts, and only the second one decides what lands in a manifest. A mismatch is
// a CONTRACT VIOLATION and throws: bytes that do not hash to their own digest are
// not a policy question, and returning them as an unmapped selector would file a
// corrupt read under the same heading as an evidence class nobody mapped yet.
// ---------------------------------------------------------------------------

function readBody(read, kind, selector) {
  if (!isPlainObject(read)) {
    fail("store_answer_unreadable", "the F01 store returned no readable answer", { kind });
  }
  if (read.schema_version !== V5_F01_STORE_SCHEMA_VERSION) {
    fail("store_schema_mismatch",
      `the F01 store answered with schema "${read.schema_version}"`, { kind });
  }
  if (read.operation !== "read-record-source-authority" || read.decision !== "allow" ||
      read.reason_id !== "read_recomputed_from_committed_rows") {
    fail("store_answer_not_a_read",
      "the F01 answer is not an allowed read of committed rows",
      { kind, operation: read.operation ?? null, decision: read.decision ?? null });
  }
  if (read.kind !== kind) {
    fail("read_kind_mismatch", `asked F01 for "${kind}" and was answered "${read.kind}"`, { kind });
  }
  if (read.integrity !== "recomputed_not_trusted" || read.stale_fallback_permitted !== false) {
    fail("read_integrity_not_recomputed",
      "the F01 read does not carry recomputed integrity", { kind });
  }
  if (read.tenant !== ORGANIZATION_TENANT_ID) {
    fail("read_tenant_mismatch",
      `the F01 read is bound to tenant "${read.tenant}", not "${ORGANIZATION_TENANT_ID}"`,
      { kind, tenant: read.tenant ?? null, expected: ORGANIZATION_TENANT_ID });
  }
  const body = read.readback;
  if (!isPlainObject(body)) {
    fail("read_body_unreadable", "the F01 read carries no readable body", { kind });
  }
  if (body.tenant !== ORGANIZATION_TENANT_ID) {
    fail("read_tenant_mismatch",
      `the F01 read body is bound to tenant "${body.tenant}", not "${ORGANIZATION_TENANT_ID}"`,
      { kind, tenant: body.tenant ?? null, expected: ORGANIZATION_TENANT_ID });
  }
  if (body.kind !== kind || body.operation !== "read-record-source-authority") {
    fail("read_kind_mismatch", `the F01 read body answers for "${body.kind}"`, { kind });
  }
  if (body.integrity !== "recomputed_not_trusted" || body.external_effects !== false) {
    fail("read_integrity_not_recomputed",
      "the F01 read body does not carry recomputed integrity and no external effects", { kind });
  }
  // The database's OWN actor, derived from the connection, beside the one the
  // store authenticated. They must be the same principal or the read cannot be
  // attributed at all.
  if (typeof body.actor_slug !== "string" || body.actor_slug !== read.actor_slug) {
    fail("actor_context_mismatch",
      "the F01 read body names a different actor from the store answer",
      { kind, store_actor: read.actor_slug ?? null, database_actor: body.actor_slug ?? null });
  }
  assertInstant(body.server_time, `f01_read(${kind}).server_time`);
  return { body, selector };
}

function verifiedStoredArtifact(body, artifact_digest) {
  const stored = body.body;
  if (stored === null || stored === undefined) return null;
  if (!isPlainObject(stored) || !isPlainObject(stored.artifact) ||
      !isPlainObject(stored.envelope)) {
    fail("stored_artifact_unreadable",
      "the stored artifact carries no readable preimage; it is refused, not repaired",
      { artifact_digest });
  }
  if (stored.artifact_digest !== artifact_digest) {
    fail("stored_artifact_identity_mismatch",
      "F01 answered with an artifact other than the one selected",
      { artifact_digest, answered: stored.artifact_digest ?? null });
  }
  if (stored.integrity !== "recomputed_from_committed_row") {
    fail("stored_artifact_integrity_missing",
      "the stored artifact does not carry recomputed integrity", { artifact_digest });
  }
  if (stored.envelope.record_kind !== "stored_corporate_artifact" ||
      stored.envelope.tenant !== ORGANIZATION_TENANT_ID ||
      stored.artifact.tenant !== ORGANIZATION_TENANT_ID) {
    fail("stored_artifact_not_tenant_bound",
      "the stored artifact envelope is not one tenant's corporate artifact",
      { artifact_digest, record_kind: stored.envelope.record_kind ?? null });
  }
  // The three recomputations. Any one of them failing means the object in hand is
  // not the row F01 hashed.
  if (digest(stored.envelope) !== stored.envelope_digest) {
    fail("stored_artifact_envelope_digest_mismatch",
      "the stored envelope no longer hashes to its own digest",
      { artifact_digest, expected: stored.envelope_digest ?? null,
        actual: digest(stored.envelope) });
  }
  if (digest(stored.artifact) !== artifact_digest ||
      stored.envelope.record_digest !== artifact_digest) {
    fail("stored_artifact_record_digest_mismatch",
      "the stored artifact preimage no longer hashes to its own record digest",
      { artifact_digest, actual: digest(stored.artifact) });
  }
  if (!sameBytes(stored.envelope.record, stored.artifact)) {
    fail("stored_artifact_envelope_record_mismatch",
      "the returned artifact preimage is not the one inside its own envelope",
      { artifact_digest });
  }
  // THE SOURCE'S INSTANT, CHECKED AGAINST ITSELF. `source_observed_at` is the
  // same column the preimage's `observed_at` came from; `recorded_at` is custody
  // and is deliberately read by nothing below.
  if (stored.source_observed_at !== stored.artifact.observed_at ||
      stored.created_at !== stored.artifact.observed_at) {
    fail("stored_artifact_observed_at_incoherent",
      "the stored artifact's observed instant disagrees with its own hashed preimage",
      { artifact_digest, row: stored.source_observed_at ?? null,
        preimage: stored.artifact.observed_at ?? null });
  }
  return stored;
}

function verifiedCoverage(body, artifact_digest) {
  const coverage = body.body;
  if (!isPlainObject(coverage)) {
    fail("derivative_coverage_unreadable",
      "F01 returned no readable derivative-coverage answer", { artifact_digest });
  }
  if (coverage.artifact_digest !== artifact_digest) {
    fail("derivative_coverage_identity_mismatch",
      "the coverage answer is about a different artifact",
      { artifact_digest, answered: coverage.artifact_digest ?? null });
  }
  if (coverage.integrity !== "recomputed_from_committed_rows") {
    fail("derivative_coverage_unreadable",
      "the coverage answer carries no recomputed integrity", { artifact_digest });
  }
  // ONE STATE IS CONSUMABLE AND IT IS `unknown`. F01 produces no other today, and
  // its own note is that establishing coverage needs a closed producer set,
  // registered completions and independently verified producer outputs — none of
  // which exists. So a row that says anything else is either malformed or is
  // asserting a fact this module has no way to check, and both are refused rather
  // than carried. A state that claimed completeness while
  // `is_exhaustive_inventory` stayed false is CONTRADICTORY, and reading past the
  // contradiction to whichever field is convenient is how a manifest ends up
  // carrying a lineage claim nobody made.
  if (coverage.state !== V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE) {
    fail("derivative_coverage_state_not_consumable",
      `the coverage answer is in state "${String(coverage.state)}"; this module consumes only "${V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE}" and establishes no other`,
      { artifact_digest, state: typeof coverage.state === "string" ? coverage.state : null,
        consumable: V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE });
  }
  if (coverage.reason_id !== undefined && coverage.reason_id !== null &&
      typeof coverage.reason_id !== "string") {
    fail("derivative_coverage_unreadable",
      "the coverage answer carries an unreadable reason", { artifact_digest });
  }
  // UNKNOWN STAYS UNKNOWN, and a claim to the contrary is refused rather than
  // consumed — including one that contradicts the state above.
  if (coverage.is_exhaustive_inventory !== false ||
      coverage.empty_link_set_means_verified_absence !== false) {
    fail("derivative_coverage_claims_completeness",
      "the coverage answer claims an exhaustive inventory; this module cannot verify one",
      { artifact_digest });
  }
  const links = Array.isArray(coverage.registered_links) ? coverage.registered_links : null;
  if (links === null) {
    fail("derivative_coverage_unreadable",
      "the coverage answer carries no readable link list", { artifact_digest });
  }
  // EVERY LINK MUST BE BOUND TO THE ARTIFACT ACTUALLY READ. A link naming another
  // source is a parent this projection never loaded, and a lineage edge whose
  // parent was never read is exactly the hole F05 refuses on later as
  // `taint_lineage_dangling_parent`. It is refused here, where the reason is
  // still legible.
  for (const link of links) {
    if (!isPlainObject(link) || link.source_artifact_digest !== artifact_digest) {
      fail("derivative_link_parent_not_read",
        "a registered link names a source artifact this projection did not read",
        { artifact_digest,
          named: isPlainObject(link) ? link.source_artifact_digest ?? null : null });
    }
  }
  return coverage;
}

// ---------------------------------------------------------------------------
// The mapping. One artifact in, one F05 record out — or one named reason why not.
// ---------------------------------------------------------------------------

function mapArtifactRecord({ artifact, artifact_digest, query_id, now }) {
  const unmapped = (reason_id, detail = {}) =>
    ({ record: null, external_source_system: null, reason_id, detail });

  const mapping = Object.prototype.hasOwnProperty.call(
    V5_F05_SOURCE_EVIDENCE_CLASS_MAP, artifact.evidence_class)
    ? V5_F05_SOURCE_EVIDENCE_CLASS_MAP[artifact.evidence_class]
    : null;
  if (mapping === null) {
    return unmapped("origin_not_derivable_from_evidence_class",
      { evidence_class: typeof artifact.evidence_class === "string"
        ? artifact.evidence_class : null,
        derivable_from: Object.keys(V5_F05_SOURCE_EVIDENCE_CLASS_MAP).sort() });
  }
  if (typeof artifact.native_version !== "string" ||
      !CANONICAL_DECIMAL.test(artifact.native_version)) {
    return unmapped("artifact_native_version_not_canonical_decimal",
      { native_version: typeof artifact.native_version === "string"
        ? artifact.native_version : null });
  }
  const version = Number(artifact.native_version);
  if (!Number.isSafeInteger(version) || String(version) !== artifact.native_version) {
    return unmapped("artifact_native_version_not_canonical_decimal",
      { native_version: artifact.native_version });
  }
  if (!isPlainObject(artifact.provenance) ||
      typeof artifact.provenance.evidence_ref !== "string" ||
      typeof artifact.provenance.retrieval_class !== "string") {
    return unmapped("artifact_provenance_incomplete", {});
  }
  // Each stored value is checked with the guard that will read it later, so the
  // reason a selector did not map names the FIELD rather than the guard.
  const guarded = (reason_id, check) => {
    try {
      return { value: check(), reason_id: null };
    } catch (error) {
      if (!(error instanceof V5F05Error)) throw error;
      return { value: null, reason_id, detail: { code: error.code } };
    }
  };
  const contentDigest = guarded("artifact_content_digest_unreadable",
    () => assertDigestRef(artifact.content_digest, "artifact.content_digest"));
  if (contentDigest.reason_id !== null) {
    return unmapped(contentDigest.reason_id, contentDigest.detail);
  }
  const sourceId = guarded("artifact_source_system_not_a_usable_source_id",
    () => assertExternalIdent(artifact.source_system, "artifact.source_system",
      { maxLength: 128 }));
  if (sourceId.reason_id !== null) return unmapped(sourceId.reason_id, sourceId.detail);
  const observed = guarded("artifact_observed_at_unreadable",
    () => assertInstant(artifact.observed_at, "artifact.observed_at"));
  if (observed.reason_id !== null) return unmapped(observed.reason_id, observed.detail);
  const observedAt = observed.value;
  if (observedAt > now) {
    return unmapped("artifact_observed_after_read_instant",
      { observed_at: artifact.observed_at });
  }

  // Built, then validated with the SAME guards the assembler will use, so a
  // stored value the assembler would refuse is reported here as an unmapped
  // selector instead of throwing out of a manifest half-built.
  const record = {
    record_id: artifact_digest,
    record_kind: mapping.record_kind,
    version,
    content_digest: contentDigest.value,
    origin: mapping.origin,
    // NOT `primary`. The kernel is explicit that primary means derived from
    // nothing, and F01 has no read that establishes that: it shows what was
    // registered as derived FROM an artifact and nothing about what the artifact
    // was itself derived from. So the record says the upstream is unestablished,
    // which the kernel carries into the manifest and pays for by blocking the
    // consequential action.
    derived_kind: V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND,
    derived_from: [],
    query_id,
    observed_at: artifact.observed_at,
    // No caller policy, so no invented freshness window and no manufactured
    // staleness verdict.
    max_age_seconds: null,
    // Measured, not estimated: this projection delivers metadata and no content.
    estimated_tokens: 0,
    omissible: false,
    backs_control: false,
    provenance: {
      // The seat that answered, not the system the bytes came from originally.
      // The original system stays on the observation and inside `evidence_ref`.
      source_id: V5_F05_SOURCE_STORED_COPY_SOURCE_ID,
      // How THIS record reached THIS context, which is the question F05's field
      // asks. The artifact's own retrieval_class describes how the source system
      // was read months ago and is a different fact.
      retrieval_class: ARTIFACT_RETRIEVAL_CLASS,
      evidence_ref: artifact.provenance.evidence_ref,
    },
  };
  try {
    assertExternalIdent(record.record_id, "record.record_id", { maxLength: 128 });
    assertSafeInteger(record.version, "record.version", { min: 1 });
    assertExternalIdent(record.provenance.evidence_ref, "record.provenance.evidence_ref");
    assertExternalIdent(record.query_id, "record.query_id", { maxLength: 128 });
  } catch (error) {
    if (!(error instanceof V5F05Error)) throw error;
    return unmapped("f05_record_guard_refused",
      { code: error.code, path: error.detail?.path ?? null });
  }
  // Three invariants this module refuses to leave to review. The kernel enforces
  // all three itself for an unknown-upstream record; asserting them here means a
  // bad evidence-class entry fails in THIS module, naming the map that is wrong.
  if (!V5_F05_EXTERNAL_ORIGINS.includes(record.origin) ||
      !V5_F05_RECORD_KINDS.includes(record.record_kind) ||
      V5_F05_AUTHORITY_BEARING_RECORD_KINDS.includes(record.record_kind)) {
    fail("mapped_record_not_external_evidence",
      "a mapped record must take an external origin and a registered, non-authority-bearing record kind",
      { origin: record.origin, record_kind: record.record_kind });
  }
  // The external system is returned BESIDE the record rather than inside it: it
  // is evidence about where the bytes were captured from, not a claim that the
  // system answered.
  return { record, external_source_system: sourceId.value, reason_id: null, detail: null };
}

function projectionPreimage(projection) {
  const { source_projection_digest, effects, ...rest } = projection;
  return rest;
}

// ---------------------------------------------------------------------------
// The adapter.
// ---------------------------------------------------------------------------

/**
 * Build the adapter over an ALREADY-CONSTRUCTED F01 store.
 *
 * IT HOLDS ONE FUNCTION, NOT THE STORE. `readRecordSourceAuthority` is captured
 * and the rest of the store object is dropped on the floor, so no path from this
 * module reaches a writer even by mistake. The store itself is trusted code the
 * parent constructs; nothing here accepts a database handle, a credential, a
 * verifier, a clock or a tenant.
 */
export function createContextAssemblySource({ store } = {}) {
  if (!store || typeof store.readRecordSourceAuthority !== "function") {
    fail("store_required",
      "createContextAssemblySource requires a store exposing readRecordSourceAuthority");
  }
  if (store.schema_version !== V5_F01_STORE_SCHEMA_VERSION) {
    fail("store_schema_mismatch",
      `the injected store reports schema "${store.schema_version}"`,
      { expected: V5_F01_STORE_SCHEMA_VERSION });
  }
  const read = store.readRecordSourceAuthority;

  async function readOne(kind, selector, context) {
    const answer = await read({
      schema_version: V5_F01_STORE_SCHEMA_VERSION,
      selector: { kind, ...selector },
    }, context);
    return readBody(answer, kind, selector);
  }

  /**
   * Read one deterministic selection and project it into F05 shapes.
   *
   * ORDERED, so a second reader reaches the same answer from the transcript:
   *   1. The request is snapshotted, closed and bounded. A duplicate selector
   *      refuses BEFORE any read: two copies of one record cannot both be carried
   *      and choosing one silently is how a manifest starts differing from its
   *      own selection.
   *   2. A selector this module cannot map to a record — a document, a version
   *      list, anything but an artifact — is recorded UNMAPPED with its reason and
   *      is not read at all. No unregistered function is called to fill the field
   *      it is missing.
   *   3. Each artifact is read, its digests recomputed, and its derivative
   *      coverage read as an OBSERVATION.
   *   4. The mapping runs. Anything that does not map is named.
   *   5. `now` is the latest server instant F01 reported across the reads.
   *   6. Assembly is permitted only when NOTHING is unmapped and at least one
   *      record was produced.
   */
  async function readSourceProjection(request, context) {
    const asked = snapshot(request ?? {}, "request");
    assertClosed(asked, REQUEST_KEYS, ["selection"], "request");
    if (asked.schema_version !== undefined &&
        asked.schema_version !== V5_F05_SOURCE_SCHEMA_VERSION) {
      fail("unknown_schema_version",
        `request.schema_version must be "${V5_F05_SOURCE_SCHEMA_VERSION}"`,
        { expected: V5_F05_SOURCE_SCHEMA_VERSION });
    }
    if (!Array.isArray(asked.selection) || asked.selection.length === 0 ||
        asked.selection.length > V5_F05_SOURCE_MAX_SELECTION) {
      fail("invalid_selection",
        `request.selection must name 1..${V5_F05_SOURCE_MAX_SELECTION} stored records`,
        { path: "request.selection" });
    }

    const seen = new Set();
    const selection = asked.selection.map((raw, index) => {
      const path = `request.selection[${index}]`;
      assertClosed(raw, SELECTOR_KEYS, ["kind"], path);
      if (!V5_F01_READ_KINDS.includes(raw.kind)) {
        fail("unknown_read_kind", `"${raw.kind}" is not a registered F01 read kind`,
          { path, kind: raw.kind, registered: [...V5_F01_READ_KINDS] });
      }
      const selector = { kind: raw.kind };
      if (raw.artifact_digest !== undefined) {
        selector.artifact_digest = assertDigestRef(raw.artifact_digest, `${path}.artifact_digest`);
      }
      if (raw.document_id !== undefined) {
        selector.document_id = assertExternalIdent(raw.document_id, `${path}.document_id`,
          { maxLength: 128 });
      }
      if (raw.kind === "artifact" && selector.artifact_digest === undefined) {
        fail("missing_field", `${path}.artifact_digest is required for an artifact selector`,
          { path });
      }
      const key = canonicalJson(selector);
      if (seen.has(key)) {
        fail("duplicate_selector",
          `${path} repeats a selector already named; one record cannot be carried twice`,
          { path, selector });
      }
      seen.add(key);
      return Object.freeze({ ...selector, selector_index: index });
    });

    const records = [];
    const queries = [];
    const sources = new Map();
    // Kept apart from `sources` on purpose: these are the systems the bytes were
    // captured FROM, and none of them was contacted by this read.
    const externalSourceSystems = new Set();
    const unmapped = [];
    const observations = [];
    const readInstants = [];
    // THE ACTOR THE MANIFEST WILL BE ASSEMBLED FOR, taken from the DATABASE's own
    // answer rather than from the caller's object. Every read must name the same
    // principal; a projection assembled across two actors would attribute a
    // manifest to whichever read happened to be looked at.
    let actor_slug = null;
    const noteActor = body => {
      if (actor_slug === null) {
        actor_slug = assertExternalIdent(body.actor_slug, "f01_read.actor_slug",
          { maxLength: 128 });
        return;
      }
      if (body.actor_slug !== actor_slug) {
        fail("actor_context_mismatch",
          "two reads in one projection name different principals",
          { first: actor_slug, second: body.actor_slug ?? null });
      }
    };

    const noteUnmapped = (selector, reason_id, detail) => {
      // The closed list is BINDING rather than documentary: a later edit that
      // invents a reason fails here instead of shipping one no consumer can read.
      if (!V5_F05_SOURCE_UNMAPPED_REASONS.includes(reason_id)) {
        fail("unregistered_unmapped_reason",
          `"${reason_id}" is not a registered unmapped reason`,
          { reason_id, registered: [...V5_F05_SOURCE_UNMAPPED_REASONS] });
      }
      unmapped.push({
        selector_index: selector.selector_index,
        kind: selector.kind,
        artifact_digest: selector.artifact_digest ?? null,
        document_id: selector.document_id ?? null,
        reason_id,
        detail: detail ?? {},
      });
    };

    for (const selector of selection) {
      if (selector.kind !== "artifact") {
        // NO READ IS ISSUED. A document version carries no source-observed
        // instant and its origin statement lives behind a function that is not a
        // registered read kind; the other kinds carry no F05 record at all.
        noteUnmapped(selector,
          ["document", "document_versions"].includes(selector.kind)
            ? "document_provenance_not_readable_through_registered_read_kinds"
            : ["derivative_links", "derivative_coverage"].includes(selector.kind)
              ? "derivative_record_not_readable_through_registered_read_kinds"
              : "read_kind_not_mappable_to_f05_record",
          { mappable_kinds: [...V5_F05_SOURCE_MAPPABLE_READ_KINDS] });
        continue;
      }

      const artifact_digest = selector.artifact_digest;
      const artifactRead = await readOne("artifact", { artifact_digest }, context);
      readInstants.push(artifactRead.body.server_time);
      noteActor(artifactRead.body);
      const stored = verifiedStoredArtifact(artifactRead.body, artifact_digest);

      const coverageRead = await readOne("derivative_coverage", { artifact_digest }, context);
      readInstants.push(coverageRead.body.server_time);
      noteActor(coverageRead.body);
      const coverage = stored === null
        ? null : verifiedCoverage(coverageRead.body, artifact_digest);

      const query_id = `f01_read:artifact:${artifact_digest}`;
      queries.push({
        query_id,
        query_kind: "f01_read.artifact",
        parameters_digest: digest({ kind: "artifact", artifact_digest }),
        retrieved_at: artifactRead.body.server_time,
      });
      queries.push({
        query_id: `f01_read:derivative_coverage:${artifact_digest}`,
        query_kind: "f01_read.derivative_coverage",
        parameters_digest: digest({ kind: "derivative_coverage", artifact_digest }),
        retrieved_at: coverageRead.body.server_time,
      });

      if (stored === null) {
        noteUnmapped(selector, "artifact_not_stored", {});
        continue;
      }

      observations.push({
        artifact_digest,
        evidence_class: typeof stored.artifact.evidence_class === "string"
          ? stored.artifact.evidence_class : null,
        f01_taint_class: typeof stored.artifact.taint_class === "string"
          ? stored.artifact.taint_class : null,
        // THE EXTERNAL SYSTEM THE BYTES WERE CAPTURED FROM, kept as evidence and
        // deliberately not the `source_id` any record cites. Nothing here reached
        // it, so nothing here may report on it.
        source_system: typeof stored.artifact.source_system === "string"
          ? stored.artifact.source_system : null,
        external_source_contacted: false,
        external_source_liveness_checked: false,
        answered_from: V5_F05_SOURCE_STORED_COPY_SOURCE_ID,
        // F01's own answer, carried verbatim and never upgraded.
        derivative_coverage_state: coverage.state,
        derivative_coverage_reason_id: coverage.reason_id ?? null,
        registered_derivative_kinds: Array.isArray(coverage.registered_derivative_kinds)
          ? [...coverage.registered_derivative_kinds] : [],
        registered_link_count: coverage.registered_link_count ?? coverage.registered_links.length,
        // Observations only. A link row names a derivative but carries neither a
        // version nor an instant that derivative was observed at, so mapping one
        // into a record would mean inventing both.
        derivatives_mapped_into_records: 0,
        derivative_records_unmapped_reason_id:
          "derivative_record_not_readable_through_registered_read_kinds",
        empty_link_set_means_verified_absence: false,
        lineage_complete: false,
      });

      const mapped = mapArtifactRecord({
        artifact: stored.artifact,
        artifact_digest,
        query_id,
        // Each artifact is checked against the instant of its OWN read, which is
        // the latest instant that read can vouch for.
        now: assertInstant(artifactRead.body.server_time, "f01_read(artifact).server_time"),
      });
      if (mapped.record === null) {
        noteUnmapped(selector, mapped.reason_id, mapped.detail);
        continue;
      }
      records.push(mapped.record);
      // ONE SEAT, whatever the artifacts' external systems are. "available" is
      // true of F01's stored copy, which is the only thing this adapter reached,
      // and the note says so in the manifest rather than only in this comment.
      if (!sources.has(V5_F05_SOURCE_STORED_COPY_SOURCE_ID)) {
        sources.set(V5_F05_SOURCE_STORED_COPY_SOURCE_ID, {
          source_id: V5_F05_SOURCE_STORED_COPY_SOURCE_ID,
          state: "available",
          required_for_task: false,
          note: V5_F05_SOURCE_STORED_COPY_NOTE,
        });
      }
      externalSourceSystems.add(mapped.external_source_system);
    }

    // THE ASSEMBLY INSTANT, derived from the server. The latest instant any read
    // reported: the projection is at least as late as everything in it, and no
    // caller clock reaches this field.
    const now = readInstants.length === 0 ? null : readInstants.reduce(
      (latest, candidate) => (assertInstant(candidate, "f01_read.server_time") >
        assertInstant(latest, "f01_read.server_time") ? candidate : latest));

    records.sort((a, b) => (a.record_id < b.record_id ? -1 : 1));
    queries.sort((a, b) => (a.query_id < b.query_id ? -1 : 1));
    observations.sort((a, b) => (a.artifact_digest < b.artifact_digest ? -1 : 1));
    unmapped.sort((a, b) => a.selector_index - b.selector_index);
    const sourceList = [...sources.values()].sort((a, b) => (a.source_id < b.source_id ? -1 : 1));

    const assembly_permitted =
      unmapped.length === 0 && records.length > 0 && now !== null && actor_slug !== null;
    const projection = {
      schema_version: V5_F05_SOURCE_PROJECTION_SCHEMA_VERSION,
      adapter_version: V5_F05_SOURCE_SCHEMA_VERSION,
      store_schema_version: V5_F01_STORE_SCHEMA_VERSION,
      tenant: ORGANIZATION_TENANT_ID,
      operation: "project-f05-source-records",
      decision: assembly_permitted ? "allow" : "refuse",
      reason_id: assembly_permitted
        ? "source_records_projected"
        : unmapped.length > 0 ? unmapped[0].reason_id : "no_record_projected",
      projection_kind: "reproducible_proposal",
      authenticated: false,
      trust_anchor: null,
      // THE DATABASE'S OWN ACTOR, and one honest caveat beside it. The slug comes
      // from ops.f01_context_actor_slug() through the store's answer. `human`
      // comes from the handler's authenticated context: F01 makes the database
      // attest it only on its two authorityOnly operations, and a read is not one,
      // so it is reported as unverified rather than passed off as derived. F05
      // reads both to compute authority, and the manifest it produces is a
      // proposal for exactly that reason.
      actor: actor_slug === null ? null : Object.freeze({
        slug: actor_slug,
        human: isPlainObject(context) && isPlainObject(context.actor) &&
          context.actor.human === true,
        human_verified_by_database: false,
      }),
      selection: selection.map(entry => ({ ...entry })),
      now,
      mode: V5_F05_SOURCE_MODE,
      records,
      queries,
      sources: sourceList,
      unmapped,
      observations,
      assembly_permitted,
      // WHAT THIS PROJECTION DOES NOT SAY, in the record rather than in a comment.
      lineage: {
        // Every mapped record declares the kernel's unknown-upstream state, so the
        // manifest below blocks its own consequential action and says why. None of
        // them claims to be an original.
        record_derived_kind: V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND,
        upstream_lineage_established: false,
        primary_lineage_claimed: false,
        derived_from_scope: "parents_carried_in_this_projection_only",
        // F01's own states, carried rather than summarised into one word. This
        // module never upgrades one and never claims completeness whatever they
        // say, which is what the flag below means.
        derivative_coverage_states:
          [...new Set(observations.map(entry => entry.derivative_coverage_state))].sort(),
        lineage_complete: false,
        absent_link_means_no_derivative: false,
        absent_link_means_first_party_origin: false,
        upstream_derivation_inside_source_system_unknown: true,
        derivative_records_mapped: 0,
      },
      uncertainty: {
        marker: true,
        origin_derivable_from_evidence_classes:
          Object.keys(V5_F05_SOURCE_EVIDENCE_CLASS_MAP).sort(),
        version_mapping: "artifact_native_version_canonical_decimal",
        version_is_source_ordering_claim: false,
        record_content_carried: false,
        record_token_estimate_available: false,
        record_max_age_policy_supplied: false,
        freshness_verdict_invented: false,
        observed_at_source: "artifact_preimage_observed_at",
        custody_recorded_at_used_as_observed_at: false,
        // The read reached F01's stored copy and nothing else. The external
        // systems those bytes were captured from are listed as evidence, and no
        // claim is made about any of them.
        answered_by: V5_F05_SOURCE_STORED_COPY_SOURCE_ID,
        external_source_contacted: false,
        external_source_liveness_checked: false,
        external_source_systems: [...externalSourceSystems].sort(),
        actor_human_verified_by_database: false,
        rule_universe_read_from_store: false,
        consequential_action_supported_by_this_projection: false,
      },
      records_written: 0,
      provider_calls: 0,
    };
    return deepFreeze({
      ...projection,
      source_projection_digest: digest(projectionPreimage(projection)),
      effects: V5_NO_EFFECTS,
    });
  }

  return Object.freeze({
    schema_version: V5_F05_SOURCE_SCHEMA_VERSION,
    readSourceProjection,
    assembleContextFromSource,
  });
}

/**
 * The assembly seam: frozen bytes and the existing assembler, or nothing.
 *
 * A PURE FUNCTION, deliberately outside the store closure. It reads no database,
 * so a projection can be assembled by anyone holding it — which is what makes the
 * answer a reproducible proposal rather than a claim about a live system.
 *
 * ORDERED:
 *   1. The projection is RECOMPUTED, not believed. `assembly_permitted` is a
 *      field, and a field can be edited; the digest binds the whole projection
 *      and the permission is re-derived from `unmapped` and `records` anyway.
 *   2. The template is closed, and every derived field is refused BY NAME —
 *      above all `records`, `queries` and `sources`.
 *   3. The request is built from the projection's own values, frozen with
 *      freezeAssemblyInput, and handed to assembleContextManifest unchanged.
 *   4. The manifest's digest is recomputed. No attestation is minted, no verifier
 *      is named, and `authenticated` stays false.
 */
export function assembleContextFromSource(input) {
  const asked = assertClosed(input ?? {}, ASSEMBLY_KEYS, ASSEMBLY_KEYS, "input");
  const projection = asked.projection;
  if (!isPlainObject(projection) ||
      projection.schema_version !== V5_F05_SOURCE_PROJECTION_SCHEMA_VERSION) {
    fail("projection_not_compiled",
      "input.projection must be the output of readSourceProjection",
      { path: "input.projection" });
  }
  assertDigestRef(projection.source_projection_digest, "input.projection.source_projection_digest");
  const recomputed = digest(projectionPreimage(projection));
  if (recomputed !== projection.source_projection_digest) {
    fail("source_projection_digest_mismatch",
      "the projection no longer hashes to its own digest; it was edited after it was read",
      { expected: projection.source_projection_digest, actual: recomputed });
  }
  if (projection.tenant !== ORGANIZATION_TENANT_ID) {
    fail("read_tenant_mismatch", `the projection is bound to tenant "${projection.tenant}"`,
      { expected: ORGANIZATION_TENANT_ID });
  }
  // Re-derived rather than read off the projection.
  const permitted = Array.isArray(projection.unmapped) && projection.unmapped.length === 0 &&
    Array.isArray(projection.records) && projection.records.length > 0 &&
    projection.now !== null && isPlainObject(projection.actor) &&
    projection.mode === V5_F05_SOURCE_MODE && projection.assembly_permitted === true;
  if (!permitted) {
    return deepFreeze({
      schema_version: V5_F05_SOURCE_ASSEMBLY_SCHEMA_VERSION,
      tenant: ORGANIZATION_TENANT_ID,
      decision: "refuse",
      reason_id: "source_projection_incomplete",
      projection_kind: "reproducible_proposal",
      authenticated: false,
      trust_anchor: null,
      unmapped: Array.isArray(projection.unmapped)
        ? projection.unmapped.map(entry => ({ ...entry })) : [],
      unmapped_reason_ids: Array.isArray(projection.unmapped)
        ? [...new Set(projection.unmapped.map(entry => entry.reason_id))].sort() : [],
      source_projection_digest: projection.source_projection_digest,
      manifest: null,
      input_bytes: null,
      input_digest: null,
      manifest_digest: null,
      attestation_minted: false,
      verifier_registered: false,
      execution_gap_id: "no_registered_verifier",
      consequential_execution_permitted: false,
      consequential_action_supported_by_this_projection: false,
      mode: V5_F05_SOURCE_MODE,
      records_written: 0,
      provider_calls: 0,
      effects: V5_NO_EFFECTS,
    });
  }

  const template = snapshot(asked.template ?? {}, "input.template");
  for (const key of Object.keys(template)) {
    if (V5_F05_SOURCE_REFUSED_TEMPLATE_KEYS.includes(key)) {
      fail("caller_derived_field_refused",
        `input.template.${key} is derived from the store read or fixed by this adapter; a caller may not supply it`,
        { path: `input.template.${key}`, key });
    }
  }
  assertClosed(template, V5_F05_SOURCE_TEMPLATE_KEYS, ["task", "universe"], "input.template");

  const request = {
    schema_version: V5_F05_MANIFEST_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    now: projection.now,
    // Exploration only. This projection carries no content, no token estimate and
    // no established lineage, and asking for a consequential proposal over it
    // would be asking a manifest to stand behind facts nobody established.
    mode: V5_F05_SOURCE_MODE,
    actor: { slug: projection.actor.slug, human: projection.actor.human },
    task: template.task,
    universe: template.universe,
    records: projection.records.map(record => ({ ...record,
      derived_from: [...record.derived_from], provenance: { ...record.provenance } })),
    sources: projection.sources.map(entry => ({ ...entry })),
    queries: projection.queries.map(entry => ({ ...entry })),
    ...(template.controls === undefined ? {} : { controls: template.controls }),
    ...(template.semantic_candidates === undefined
      ? {} : { semantic_candidates: template.semantic_candidates }),
    ...(template.enforcement_evidence_policy === undefined
      ? {} : { enforcement_evidence_policy: template.enforcement_evidence_policy }),
  };

  const frozen = freezeAssemblyInput(request);
  const manifest = assembleContextManifest(frozen);
  verifyContextManifest(manifest);

  return deepFreeze({
    schema_version: V5_F05_SOURCE_ASSEMBLY_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision: manifest.decision,
    reason_id: manifest.reason_id,
    projection_kind: "reproducible_proposal",
    // Said on every answer, including the allowed one: this is a proposal that
    // reproduces from its own bytes and evidence of nothing about a running
    // system. No verifier exists to say more; see contextAssemblySourceGaps().
    authenticated: false,
    trust_anchor: null,
    attestation_minted: false,
    verifier_registered: false,
    execution_gap_id: "no_registered_verifier",
    consequential_execution_permitted: false,
    mode: V5_F05_SOURCE_MODE,
    source_projection_digest: projection.source_projection_digest,
    input_bytes: frozen.input_bytes,
    input_digest: frozen.input_digest,
    manifest_digest: manifest.manifest_digest,
    manifest,
    unmapped: [],
    unmapped_reason_ids: [],
    // TWO DIFFERENT QUESTIONS, BOTH REPORTED. The first is F05's own answer about
    // the caller's universe, facts and actor, carried verbatim rather than hidden
    // or overwritten. The second is the one a reader of a SOURCE projection is
    // actually asking, and its answer does not change: a metadata-only projection
    // over records whose derivative coverage is unknown does not support a
    // consequential action, whatever the rule half of the manifest concluded.
    manifest_consequential_action_permitted: manifest.consequential_action_permitted,
    consequential_action_supported_by_this_projection: false,
    records_written: 0,
    provider_calls: 0,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed contract, and the seams this module does not close.
// ---------------------------------------------------------------------------

export function v5F05SourceContractPreimage() {
  return {
    schema_version: V5_F05_SOURCE_SCHEMA_VERSION,
    projection_schema_version: V5_F05_SOURCE_PROJECTION_SCHEMA_VERSION,
    assembly_schema_version: V5_F05_SOURCE_ASSEMBLY_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    store_schema_version: V5_F01_STORE_SCHEMA_VERSION,
    manifest_schema_version: V5_F05_MANIFEST_SCHEMA_VERSION,
    mappable_read_kinds: [...V5_F05_SOURCE_MAPPABLE_READ_KINDS],
    evidence_class_map: V5_F05_SOURCE_EVIDENCE_CLASS_MAP,
    record_derived_kind: V5_F05_UNKNOWN_UPSTREAM_DERIVED_KIND,
    stored_copy_source_id: V5_F05_SOURCE_STORED_COPY_SOURCE_ID,
    consumable_coverage_state: V5_F05_SOURCE_CONSUMABLE_COVERAGE_STATE,
    unmapped_reasons: [...V5_F05_SOURCE_UNMAPPED_REASONS],
    template_keys: [...V5_F05_SOURCE_TEMPLATE_KEYS],
    refused_template_keys: [...V5_F05_SOURCE_REFUSED_TEMPLATE_KEYS],
    max_selection: V5_F05_SOURCE_MAX_SELECTION,
    mode: V5_F05_SOURCE_MODE,
    // What this adapter does NOT establish, hashed into the contract so a
    // consumer cannot read a projection as more than it is.
    writes_records: false,
    // It causes the reviewed store to run its ONE read operation; it composes,
    // edits and executes no statement of its own, and holds no database handle.
    issues_sql_of_its_own: false,
    reads_only_through_registered_store_read: true,
    opens_connection: false,
    registers_tool: false,
    accepts_caller_clock: false,
    accepts_caller_records: false,
    accepts_caller_queries: false,
    accepts_caller_sources: false,
    accepts_caller_tenant: false,
    accepts_caller_verifier: false,
    mints_attestation: false,
    emits_authenticated_projection: false,
    emits_first_party_origin: false,
    lowers_taint: false,
    establishes_derivative_coverage: false,
    claims_complete_lineage: false,
    // The three the owner review named, hashed rather than described.
    claims_primary_lineage: false,
    claims_external_source_answered: false,
    consumes_established_coverage_state: false,
    substitutes_custody_instant_for_observation: false,
    refreshes_observed_at_on_import: false,
    invents_record_version: false,
    invents_estimated_tokens: false,
    invents_freshness_window: false,
    reads_unregistered_read_kind: false,
    unmapped_selector_blocks_assembly: true,
    consequential_action_supported: false,
  };
}

export function v5F05SourceContractDigest() {
  return digest(v5F05SourceContractPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5F05SourceContractCanonicalBytes() {
  return canonicalJson(v5F05SourceContractPreimage());
}

/**
 * WHICH SEAMS THIS MODULE LEAVES OPEN, stated rather than implied. Landing this
 * adapter closes no gap context-assembly.v5.js reports: that module still runs no
 * query of its own, and this one runs beside it rather than inside it.
 */
export function contextAssemblySourceGaps() {
  return deepFreeze([
    {
      gap: "no_rule_universe_reader",
      where: "mcp-server/src/",
      what: "the compiled rule universe still arrives from the caller; the guidance store"
        + " carries no per-rule provenance, rule_class, typed trigger or control_effect to"
        + " compile one from, so this adapter supplies records and never rules",
      landed: false,
    },
    {
      gap: "no_document_provenance_read_kind",
      where: "domain.sql",
      what: "a document version's three-valued origin statement is reachable only through"
        + " ops.f01_document_version_source, which is not a registered f01_read kind, and a"
        + " stored version carries no source-observed instant; document selectors are refused"
        + " rather than filled from custody metadata",
      landed: false,
    },
    {
      gap: "no_derivative_record_reader",
      where: "domain.sql",
      what: "a derivative link names a derived record but carries no version and no instant"
        + " that record was observed at, so derivatives are reported as observations and never"
        + " mapped into F05 records; derivative coverage stays unknown",
      landed: false,
    },
    {
      gap: "no_manifest_persistence",
      where: "domain.sql",
      what: "the assembled manifest is returned to its caller and stored nowhere; this module"
        + " adds no table, no migration and no ordinal",
      landed: false,
    },
    {
      gap: "no_registered_verifier",
      where: "mcp-server/src/",
      what: "no component issues verifier attestations, so every answer here is a reproducible"
        + " proposal with trust_anchor null and authenticated false, and no projection can"
        + " authorize a consequential execution",
      landed: false,
    },
    {
      gap: "no_runtime_read_verification",
      where: "mcp-server/test/",
      what: "the suite proves this adapter against a scripted fake handle; that the real"
        + " ops.f01_read answers with these shapes is the disposable-database gate's job and is"
        + " not asserted twice here",
      landed: false,
    },
  ]);
}

// ---------------------------------------------------------------------------
// Load-time self-checks. Each is an invariant a later edit could break silently.
// ---------------------------------------------------------------------------

for (const [evidence_class, mapping] of Object.entries(V5_F05_SOURCE_EVIDENCE_CLASS_MAP)) {
  if (!V5_F05_EXTERNAL_ORIGINS.includes(mapping.origin)) {
    throw new V5F05SourceError("mapped_origin_not_external",
      `${evidence_class} maps to origin "${mapping.origin}", which is not one of Q068's external origins`,
      { evidence_class });
  }
  if (!V5_F05_RECORD_KINDS.includes(mapping.record_kind)) {
    throw new V5F05SourceError("mapped_record_kind_unregistered",
      `${evidence_class} maps to unregistered record kind "${mapping.record_kind}"`,
      { evidence_class });
  }
  // The kernel refuses an authority-bearing kind on an unknown-upstream record;
  // an entry added here that would hit that refusal fails at import instead.
  if (V5_F05_AUTHORITY_BEARING_RECORD_KINDS.includes(mapping.record_kind)) {
    throw new V5F05SourceError("mapped_record_kind_bears_authority",
      `${evidence_class} maps to authority-bearing record kind "${mapping.record_kind}"`,
      { evidence_class });
  }
}
for (const kind of V5_F05_SOURCE_MAPPABLE_READ_KINDS) {
  if (!V5_F01_READ_KINDS.includes(kind)) {
    throw new V5F05SourceError("mappable_kind_not_registered",
      `"${kind}" is not a registered F01 read kind`, { kind });
  }
}
for (const key of V5_F05_SOURCE_TEMPLATE_KEYS) {
  if (V5_F05_SOURCE_REFUSED_TEMPLATE_KEYS.includes(key)) {
    throw new V5F05SourceError("template_key_both_accepted_and_refused",
      `"${key}" is listed as both an accepted and a refused template key`, { key });
  }
}
