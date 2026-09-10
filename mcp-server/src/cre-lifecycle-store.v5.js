// DoctorCRE v5 slice V5-J102: the persistence tail for the CRE lifecycle.
//
// The pure kernel in cre-lifecycle.v5.js decides. This module is what makes
// those decisions RECORDS: it loads the stored subjects and the stored evidence,
// derives the actor and the instant from the server, runs the kernel unchanged,
// and hands the exact canonical preimages to the security-definer functions in
// ops/cre-lifecycle.candidate.sql that own the compare-and-swap, the append-only
// history and the evidence recheck under lock.
//
// THE DIVISION OF LABOUR, because it is the whole design:
//
//   KERNEL      Judges. Owns the vocabulary, the transition table, the refusal
//               matrix and the exact coupled facts. Unchanged and unwrapped:
//               this module imports it and reimplements none of it.
//   THIS MODULE Derives and loads. Resolves every caller REFERENCE into a
//               server-loaded record, takes the actor from the authenticated
//               transaction context and the instant from the server, refuses
//               caller authority and asserted-fact injection before any write,
//               and builds the envelopes.
//   DATABASE    Enforces. Recomputes every digest from committed bytes, refuses
//               a stale subject digest, RE-READS the exact evidence under the
//               lock it already holds, refuses direct DML, and returns the
//               readback.
//
// A CALLER SUPPLIES REFERENCES, NEVER FACTS. Every write operation below takes
// ids, digests and a small closed set of declared choices. It cannot supply a
// tenant, an actor, a clock, a subject, an evidence record, a document state, a
// decision or a digest of anything this module computes; the derived-field guard
// refuses each of those BY NAME, so the refusal says what was attempted rather
// than reporting a generic unknown field.
//
// WHICH SCHEMA THIS MODULE REQUIRES, said plainly. It calls ops.j102_* from
// ops/cre-lifecycle.candidate.sql, which is an UNNUMBERED CANDIDATE and is not
// applied anywhere by this slice. It also calls four functions that arrive with
// domain.sql — ops.f01_principal, ops.f01_now_text, ops.f01_read and
// ops.f01_stored_artifact — and it calls them rather than restating them,
// because the authenticated principal, the server clock and the document and
// artifact readbacks each have exactly one home and this is not it. Against a
// database carrying neither hunk every operation fails on a missing function,
// which is loud and closed.
//
// TWO EVIDENCE READERS DO NOT EXIST, AND THE PATHS THAT NEED THEM FAIL CLOSED.
// See V5_J102_ABSENT_EVIDENCE_READERS below: nothing in this record layer
// authenticates a representation-equivalence approval or a multi-target
// exception approval, because nothing PRODUCES one. Those two paths refuse with
// the missing fact named. They are not stubbed, defaulted, or satisfied from a
// caller field, and landing a producer for one does not silently open the other.
//
// WHAT THIS MODULE IS NOT. It registers nothing: v5J102ToolRegistrations() below
// is a DESCRIPTION the parent may register from, and this file does not touch
// tools.js, mcp.js, the mutation registry or any generated catalog. It performs
// no provider call, calls no Salesforce API, sends nothing, and completes no
// acceptance.

import { canonicalJson, digest } from "./artifact-trust.js";
import {
  ORGANIZATION_TENANT_ID,
  isKnownActor,
  authorizationClassForActor,
} from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_J102_ACTOR_CLASSES,
  V5_J102_AUTHORITY_INJECTION_FRAGMENTS,
  V5_J102_ASSERTED_FACT_FRAGMENTS,
  V5_J102_DEAL_AXES,
  V5_J102_EVIDENCE_INTEGRITY,
  V5_J102_EVIDENCE_LOADER,
  V5_J102_EVIDENCE_KINDS,
  V5_J102_SUBJECT_KINDS,
  V5_J102_TRANSITION_IDS,
  evaluateLifecycleTransition,
  projectSalesforceReference,
  v5J102DecisionSubsetDigest,
  v5J102EvidenceContract,
  v5J102PolicyDigest,
} from "./cre-lifecycle.v5.js";

export const V5_J102_STORE_SCHEMA_VERSION =
  "doctorcre-v5-j102-cre-lifecycle-store.v1";
export const V5_J102_ENVELOPE_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-record-envelope.v1";

export const V5_J102_STORED_SUBJECT_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-lifecycle-subject.v1";
export const V5_J102_STORED_EVENT_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-lifecycle-event.v1";
export const V5_J102_STORED_FACT_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-first-party-record.v1";
export const V5_J102_STORED_REFERENCE_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-salesforce-reference.v1";
export const V5_J102_STORED_CORRECTION_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-correction-receipt.v1";

/** The exact record_kind vocabulary the ops relations enforce. */
export const V5_J102_STORE_RECORD_KINDS = Object.freeze([
  "stored_lifecycle_subject",
  "stored_lifecycle_event",
  "stored_first_party_record",
  "stored_salesforce_reference",
  "stored_correction_receipt",
]);

export const V5_J102_OPERATIONS = Object.freeze([
  "read-cre-lifecycle",
  "record-lifecycle-fact",
  "record-representation-agreement",
  "open-cre-assignment",
  "record-loi-submission",
  "record-loi-acceptance",
  "commit-winning-property",
  "record-deal-execution",
  "record-diligence-outcome",
  "record-deal-closing",
  "cancel-pending-deal",
  "record-deal-axis",
  "link-salesforce-reference",
  "record-lifecycle-correction",
]);

/**
 * THE TWO EVIDENCE READERS THIS RECORD LAYER DOES NOT HAVE.
 *
 * Both are typed_approval kinds, and the gap is a PRODUCER gap rather than a
 * reader gap: no workflow anywhere writes a representation-equivalence approval
 * or a multi-target exception approval, so there is nothing for a reader to
 * return and nothing for a transition to bind to. The honest consequence is that
 * both paths refuse, today and until a producer lands.
 *
 * WHAT IS DELIBERATELY NOT DONE INSTEAD, because each would manufacture the
 * authority the absent record is supposed to carry: accepting the approval from
 * the caller, reading one out of configuration, treating an approval REFERENCE
 * stored on an assignment row as the approval itself, defaulting the equivalence
 * to "an ETL-like document counts", or treating "the reader is not built" as
 * "the approval is not required".
 *
 * THE TWO ARE INDEPENDENT ON PURPOSE. Landing a producer for representation
 * equivalence must not silently open multi-target exceptions, so each names its
 * own missing fact and each is checked separately.
 *
 * ops.j102_typed_approval() in the candidate SQL is the same refusal one layer
 * down: it is granted to no role and always raises. This list exists so the
 * refusal is a POLICY ANSWER a caller can record rather than a database error,
 * and so the two halves cannot drift apart without the suite noticing.
 */
export const V5_J102_ABSENT_EVIDENCE_READERS = Object.freeze({
  approved_representation_equivalent: Object.freeze({
    evidence_kind: "approved_representation_equivalent",
    approval_kind: "representation_equivalence_approval",
    missing_fact: "authenticated_representation_equivalence_approval",
    why: "Q077 admits a representation agreement other than an ETL, but only on a typed authenticated approval that this class of agreement counts. No producer writes one and no relation holds one, so the equivalence cannot be established without inventing the business policy the approval is supposed to carry.",
    produced_by: "not_produced_by_this_slice",
  }),
  multi_target_exception_approval: Object.freeze({
    evidence_kind: "multi_target_exception_approval",
    approval_kind: "multi_target_exception",
    missing_fact: "authenticated_multi_target_exception_approval",
    why: "Q095 permits a second selected property or lease-draft target only on an explicitly approved exception. No producer writes one, so the single-target constraint holds unconditionally today.",
    produced_by: "not_produced_by_this_slice",
  }),
});

export class V5J102StoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J102StoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J102StoreError(code, message, detail);
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

// ---------------------------------------------------------------------------
// The closed caller surface.
//
// THREE GUARDS, and they name different attempts on purpose.
//
//   AUTHORITY INJECTION — a field purporting to confer authority. Reused from
//     the kernel by IMPORT rather than copied, so the two lists cannot drift.
//   ASSERTED-FACT INJECTION — a field stating the outcome the transition exists
//     to establish. Also imported.
//   DERIVED-VALUE INJECTION — a field the SERVER owns: the tenant, the instant,
//     the loaded subject, the loaded evidence, the evaluated decision, a digest
//     of something this module computes. These are not authority claims and not
//     outcome claims, so neither kernel list names them, but a caller supplying
//     one would be choosing the evidence its own write is judged against. That
//     is the same failure wearing different clothes.
//
// A DIGEST IS NOT ALWAYS AN INJECTION, and the difference matters. A caller may
// name `expected_state_digest` to state the version it decided against, and may
// name `expected_content_digest` to pin the document version it means. Both are
// CHECKED against a readback or a recomputation and never trusted: a pin that
// does not match refuses. Everything else in the derived list is refused by name.
// ---------------------------------------------------------------------------

export const V5_J102_DERIVED_ONLY_FIELDS = deepFreeze([
  "tenant", "now", "server_time", "recorded_at", "evaluated_at", "updated_at",
  "recorded_by", "updated_by", "sponsor", "sponsoring_human_slug",
  "authenticated_identity", "human", "via", "client_id",
  "subject", "subjects", "related", "current_state", "prior_state",
  "evidence", "evidence_record", "document", "artifact", "record", "approval",
  "provenance", "loaded_by", "integrity",
  "decision", "reason_id", "outcome", "applied", "proposed_state", "events",
  "coupled_facts_committed", "reversibility", "decision_refs",
  "policy_digest", "domain_policy_digest", "decision_subset_digest",
  "state_digest", "event_digest", "record_digest", "envelope_digest",
  "subject_digest", "reference_digest", "receipt_digest", "reconciliation_digest",
  "event_seq", "last_event_digest", "freshness_age_seconds",
  "owner_known", "freshness_known", "active_automation_known",
  // The lifecycle state axes themselves. A caller that could name one would be
  // performing the free-form stage update Q082 removed, which is why every one
  // of them is refused here rather than merely absent from a schema.
  "relationship_state", "engagement_state", "assignment_phase", "negotiation_state",
  ...V5_J102_DEAL_AXES,
  "representation_basis", "selected_property_id", "active_lease_draft_target_id",
  "pending_deal_id", "cancellation_reason", "closing_date",
]);

function assertNoAccessorsOrHiddenKeys(object, path) {
  if (Object.prototype.hasOwnProperty.call(object, "__proto__")) {
    fail("prototype_key_refused", `${path}.__proto__ is an own key; the shape is refused rather than read`,
      { path });
  }
  if (Object.getOwnPropertySymbols(object).length > 0) {
    fail("symbol_key_refused", `${path} carries symbol keys, which would ride along unread`, { path });
  }
  for (const key of Object.getOwnPropertyNames(object)) {
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    if (descriptor.get !== undefined || descriptor.set !== undefined) {
      fail("accessor_property_refused",
        `${path}.${key} is an accessor; a value that can change between reads cannot bind a write`,
        { path: `${path}.${key}` });
    }
  }
}

function assertNoInjectedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (allowed.includes(key)) continue;
    const normalized = key.toLowerCase();
    for (const fragment of V5_J102_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_authority_field_refused",
          `${path}.${key} names authority the caller cannot supply; the handler derives actor and tenant`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
    for (const fragment of V5_J102_ASSERTED_FACT_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_asserted_fact_refused",
          `${path}.${key} asserts a fact the transition establishes from evidence; supply a reference, not a verdict`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
    if (V5_J102_DERIVED_ONLY_FIELDS.includes(normalized)) {
      fail("caller_derived_field_refused",
        `${path}.${key} is derived by the server or loaded from the database; a caller may not supply it`,
        { path: `${path}.${key}`, key });
    }
  }
}

/** An open schema is a contract violation: an unread field is an unenforced one. */
function assertClosed(object, allowed, required, path) {
  if (!isPlainObject(object)) fail("invalid_shape", `${path} must be a plain object`, { path });
  assertNoAccessorsOrHiddenKeys(object, path);
  assertNoInjectedKeys(object, allowed, path);
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
  for (const key of required) {
    if (!(key in object) || object[key] === undefined || object[key] === null) {
      fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
  }
  return object;
}

function assertIdempotencyKey(value, path) {
  if (typeof value !== "string" || value.length === 0 || value.length > 200 ||
      !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)) {
    fail("invalid_idempotency_key", `${path} is not a permitted idempotency key`, { path });
  }
  return value;
}

function assertIdent(value, path, { maxLength = 128 } = {}) {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength ||
      !/^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]*$/.test(value)) {
    fail("invalid_identifier", `${path} is not a permitted identifier`, { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference over 64 lower-case hex characters`,
      { path });
  }
  return value;
}

// ---------------------------------------------------------------------------
// Evidence REFERENCES. A caller names one of four shapes, and each resolves to a
// server-loaded record through exactly one reader.
//
// THE PIN IS THE POINT OF THE DOCUMENT SHAPE. A caller that names only a
// document id is asking about "whatever that document says now", and a decision
// taken against a version that moved between the read and the write is the
// concurrency defect Q103 exists to prevent. So a document reference carries the
// exact version and the exact content digest it means; the load refuses when the
// stored version differs, and ops.j102_apply_transition re-reads the same pin
// under its lock.
// ---------------------------------------------------------------------------

// The union, used only to read `evidence_kind` before the shape is known. The
// per-source lists below are what actually bind: a document reference that also
// carried an artifact digest would be a request naming two different pieces of
// evidence, and the narrower check refuses it rather than silently reading one.
const EVIDENCE_REF_KEYS = Object.freeze([
  "evidence_kind", "document_id", "expected_version_no", "expected_content_digest",
  "artifact_digest", "record_id", "approval_ref",
]);
const DOCUMENT_REF_KEYS = Object.freeze([
  "evidence_kind", "document_id", "expected_version_no", "expected_content_digest",
]);
const ARTIFACT_REF_KEYS = Object.freeze(["evidence_kind", "artifact_digest"]);
const RECORD_REF_KEYS = Object.freeze(["evidence_kind", "record_id"]);
const APPROVAL_REF_KEYS = Object.freeze(["evidence_kind", "approval_ref"]);

function assertEvidenceRef(raw, path) {
  assertClosed(raw, EVIDENCE_REF_KEYS, ["evidence_kind"], path);
  const evidence_kind = raw.evidence_kind;
  if (!V5_J102_EVIDENCE_KINDS.includes(evidence_kind)) {
    fail("unknown_evidence_kind", `"${String(evidence_kind)}" is not a registered evidence kind`,
      { path: `${path}.evidence_kind`, registered: [...V5_J102_EVIDENCE_KINDS] });
  }
  const contract = v5J102EvidenceContract(evidence_kind);
  const out = { evidence_kind, source: contract.source };
  if (contract.source === "f01_document") {
    assertClosed(raw, DOCUMENT_REF_KEYS, DOCUMENT_REF_KEYS, path);
    out.document_id = assertIdent(raw.document_id, `${path}.document_id`);
    if (!Number.isSafeInteger(raw.expected_version_no) || raw.expected_version_no < 1) {
      fail("invalid_shape", `${path}.expected_version_no must be a positive integer`,
        { path: `${path}.expected_version_no` });
    }
    out.expected_version_no = raw.expected_version_no;
    out.expected_content_digest = assertDigestRef(raw.expected_content_digest,
      `${path}.expected_content_digest`);
  } else if (contract.source === "f01_corporate_artifact") {
    assertClosed(raw, ARTIFACT_REF_KEYS, ARTIFACT_REF_KEYS, path);
    out.artifact_digest = assertDigestRef(raw.artifact_digest, `${path}.artifact_digest`);
  } else if (contract.source === "first_party_record") {
    assertClosed(raw, RECORD_REF_KEYS, RECORD_REF_KEYS, path);
    out.record_id = assertIdent(raw.record_id, `${path}.record_id`);
    // The record KIND is taken from the evidence contract, never from the
    // caller — and `record_kind` is deliberately absent from every reference
    // shape above so it cannot be supplied at all. A caller able to name it
    // could point a closing transition at an invoice record and have the
    // kernel's kind check pass against the caller's own claim.
    out.record_kind = contract.record_kind;
  } else {
    assertClosed(raw, APPROVAL_REF_KEYS, APPROVAL_REF_KEYS, path);
    out.approval_ref = assertIdent(raw.approval_ref, `${path}.approval_ref`, { maxLength: 255 });
    out.approval_kind = contract.approval_kind;
  }
  return deepFreeze(out);
}

// ---------------------------------------------------------------------------
// The closed caller schemas, one per operation.
// ---------------------------------------------------------------------------

const SUBJECT_REF_KEYS = Object.freeze(["subject_kind", "subject_id", "expected_state_digest"]);

function assertSubjectRef(raw, path, expected_kind) {
  assertClosed(raw, SUBJECT_REF_KEYS, ["subject_kind", "subject_id"], path);
  if (raw.subject_kind !== expected_kind) {
    fail("subject_kind_mismatch", `${path}.subject_kind must be "${expected_kind}"`,
      { path: `${path}.subject_kind`, expected: expected_kind, actual: raw.subject_kind });
  }
  return deepFreeze({
    subject_kind: expected_kind,
    subject_id: assertIdent(raw.subject_id, `${path}.subject_id`),
    // NULLABLE ON PURPOSE, and null means something exact: "I decided against
    // this subject not existing yet". It is compared against the stored digest
    // the same way a present one is, so a subject that appeared underneath the
    // caller refuses rather than being created twice.
    expected_state_digest: raw.expected_state_digest === undefined ||
      raw.expected_state_digest === null
      ? null : assertDigestRef(raw.expected_state_digest, `${path}.expected_state_digest`),
  });
}

const FACT_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "fact",
]);
const FACT_BODY_KEYS = Object.freeze([
  "record_kind", "record_id", "reason", "detail", "closing_date", "supporting_document_id",
]);

const TRANSITION_PAYLOAD_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "subject_ref", "related_refs", "evidence_refs", "declared",
]);
const RELATED_REF_KEYS = Object.freeze([
  "relationship", "engagement", "assignment", "property_negotiation", "deal",
]);
// THE DECLARED CHOICES COME IN TWO HALVES, and they are not the same kind of
// thing.
//
//   DOMAIN FIELDS are the kernel's own vocabulary: the mandate scope, the
//     instrument kind, the payment level, the returned-to phase, the diligence
//     result, and the ids of subjects a transition creates. They are forwarded
//     verbatim, and the kernel's closed `request.declared` is what validates
//     them — this module adds no vocabulary of its own to that judgement.
//   THE SELECTOR is consumed HERE and travels no further. `axis` names which of
//     the four orthogonal deal verbs record-deal-axis is asking for; it is the
//     store's dispatch input, not a fact about a deal, and the kernel's declared
//     contract does not carry it. Forwarding it would ask the kernel to widen a
//     closed surface for a value it has no use for, so it is dropped at this
//     boundary instead.
//
// A SELECTOR CANNOT SMUGGLE ANYTHING, because it does not reach the judgement:
// it chooses among registered transition ids by exact name (AXIS_TRANSITIONS
// below) and an unregistered name determines no transition and writes nothing.
const DECLARED_DOMAIN_KEYS = Object.freeze([
  "mandate_scope", "instrument_kind", "return_phase", "payment_level",
  "diligence_result", "new_subject_id", "new_deal_id",
]);
const DECLARED_SELECTOR_KEYS = Object.freeze(["axis"]);
const DECLARED_KEYS = Object.freeze([...DECLARED_DOMAIN_KEYS, ...DECLARED_SELECTOR_KEYS]);

/** The two halves, exported so the suite can prove the split has not drifted. */
export const V5_J102_DECLARED_DOMAIN_FIELDS = DECLARED_DOMAIN_KEYS;
export const V5_J102_DECLARED_SELECTOR_FIELDS = DECLARED_SELECTOR_KEYS;

const REFERENCE_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "opportunity_id", "opportunity_name",
  "opportunity_phase", "observed_at", "linked_subject_kind", "linked_subject_id",
]);

const CORRECTION_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "subject_ref", "correction_record_id",
  "corrected_fields", "reason",
]);

const READ_SELECTOR_KEYS = Object.freeze([
  "kind", "subject_kind", "subject_id", "legacy_row_id", "opportunity_id",
]);

export const V5_J102_READ_KINDS = Object.freeze([
  "subject", "subject_events", "first_party_record", "salesforce_references",
  "correction_receipts", "reconciliation_items", "compatibility_view", "migration_shadow",
]);

/**
 * WHICH TRANSITION EACH WRITE OPERATION PERFORMS, and where the answer comes
 * from when there is more than one.
 *
 * Two operations dispatch on LOADED state rather than on a caller field, which
 * is the point: `record-deal-execution` chooses the lease or the purchase
 * transition from the stored deal's own instrument kind, so a caller cannot ask
 * for the purchase semantics on a lease deal, and `record-deal-axis` chooses
 * among the four orthogonal axes from a declared axis name that is validated
 * against the registry.
 */
const AXIS_TRANSITIONS = Object.freeze({
  commission_agreement_state: "record-commission-agreement",
  invoice_state: "record-invoice-issued",
  payment_state: "record-payment",
  completion_state: "record-completion",
});

const OPERATION_SCHEMAS = deepFreeze({
  "read-cre-lifecycle": {
    write: false, humanOnly: false, authorityOnly: false, transition: null,
    keys: ["schema_version", "selector"], required: ["selector"],
  },
  // The one write that is NOT a transition. It records a first-party business
  // fact — a mandate, a commitment, a diligence outcome, a closing date, an
  // invoice, a payment, a completion, a failure reason — so that a later
  // transition has something server-held to be judged against. It advances no
  // lifecycle state on its own, which is stated on its every answer.
  "record-lifecycle-fact": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    keys: FACT_KEYS, required: ["idempotency_key", "fact"],
  },
  "record-representation-agreement": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "establish-client-and-engagement",
    subject_kind: "relationship",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "open-cre-assignment": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "open-assignment", subject_kind: "assignment",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-loi-submission": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "record-loi-submission", subject_kind: "property_negotiation",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-loi-acceptance": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "record-loi-acceptance", subject_kind: "property_negotiation",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  // authorityOnly: the kernel admits only a verified partner, and the check is
  // made HERE as well so an agent that somehow reached this function still
  // refuses before any load happens.
  "commit-winning-property": {
    write: true, humanOnly: false, authorityOnly: true,
    transition: "commit-winning-property", subject_kind: "assignment",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-deal-execution": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "dispatch_on_instrument_kind", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-diligence-outcome": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "record-diligence-outcome", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-deal-closing": {
    write: true, humanOnly: false, authorityOnly: true,
    transition: "record-deal-closing", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "cancel-pending-deal": {
    write: true, humanOnly: false, authorityOnly: true,
    transition: "cancel-pending-deal", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-deal-axis": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "dispatch_on_declared_axis", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs", "declared"],
  },
  "link-salesforce-reference": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    keys: REFERENCE_KEYS,
    required: ["idempotency_key", "opportunity_id", "opportunity_name", "opportunity_phase",
      "observed_at"],
  },
  // humanOnly AND authorityOnly. Q082's correction is the one path that changes
  // state without new business evidence, so it is the one path that must be a
  // person: an agent cannot correct the record on its own authority, and no
  // assistant text is ever the approval.
  "record-lifecycle-correction": {
    write: true, humanOnly: true, authorityOnly: true, transition: null,
    keys: CORRECTION_KEYS,
    required: ["idempotency_key", "subject_ref", "correction_record_id", "corrected_fields", "reason"],
  },
});

/** The closed caller schemas, for the parent's registration and for tests. */
export function v5J102StoreOperationSchemas() {
  return OPERATION_SCHEMAS;
}

/**
 * The registration description the parent may build a tool surface from.
 *
 * A DESCRIPTION, NOT A REGISTRATION. This module does not reach tools.js, mcp.js
 * or the mutation registry, and calling this function registers nothing. The
 * four false flags at the foot of each entry say what the parent still owes.
 */
export function v5J102ToolRegistrations() {
  const roles = {
    "read-cre-lifecycle":
      "Read lifecycle subjects, events, references, receipts and compatibility projections with recomputed integrity; no side write.",
    "record-lifecycle-fact":
      "Append one authenticated first-party business record so a later transition has server-held evidence; advances no lifecycle state.",
    "record-representation-agreement":
      "Establish Client status and the active Engagement together from an active signed representation agreement, atomically or not at all.",
    "open-cre-assignment":
      "Open one Assignment in research or search under an active Engagement; never duplicates the client.",
    "record-loi-submission":
      "Record an LOI submission and move the Assignment to negotiation; creates no Deal.",
    "record-loi-acceptance":
      "Record a counterparty acceptance on one property negotiation; creates no Deal and supersedes no alternative.",
    "commit-winning-property":
      "Select and commit to the winning accepted LOI, creating the one pending negotiation Deal; retains every alternative negotiation.",
    "record-deal-execution":
      "Mark the executed lease, or the executed-but-pending purchase contract entering due diligence, from the stored deal's own instrument kind.",
    "record-diligence-outcome":
      "Record a waived, satisfied or failed diligence outcome; never cancels the deal.",
    "record-deal-closing":
      "Close the Deal on the actual final closing date, committing the business state, the closing axis and the date together or refusing together.",
    "cancel-pending-deal":
      "Cancel a pending Deal with its preserved reason and return the Assignment to search or negotiation; the Client relationship is untouched.",
    "record-deal-axis":
      "Advance exactly one of the commission, invoice, payment or completion axes, leaving every other axis unchanged.",
    "link-salesforce-reference":
      "Record one external Salesforce opportunity reference with its own name and phase and progressively link it; never sets DoctorCRE lifecycle state.",
    "record-lifecycle-correction":
      "Append one human, authority-held correction receipt with its reason and evidence; history is preserved and nothing is overwritten silently.",
  };
  const handlers = {
    "read-cre-lifecycle": "readCreLifecycle",
    "record-lifecycle-fact": "recordLifecycleFact",
    "record-representation-agreement": "recordRepresentationAgreement",
    "open-cre-assignment": "openCreAssignment",
    "record-loi-submission": "recordLoiSubmission",
    "record-loi-acceptance": "recordLoiAcceptance",
    "commit-winning-property": "commitWinningProperty",
    "record-deal-execution": "recordDealExecution",
    "record-diligence-outcome": "recordDiligenceOutcome",
    "record-deal-closing": "recordDealClosing",
    "cancel-pending-deal": "cancelPendingDeal",
    "record-deal-axis": "recordDealAxis",
    "link-salesforce-reference": "linkSalesforceReference",
    "record-lifecycle-correction": "recordLifecycleCorrection",
  };
  return deepFreeze(V5_J102_OPERATIONS.map(name => ({
    name,
    write: OPERATION_SCHEMAS[name].write,
    humanOnly: OPERATION_SCHEMAS[name].humanOnly,
    authorityOnly: OPERATION_SCHEMAS[name].authorityOnly,
    role: roles[name],
    handler: handlers[name],
    input_keys: [...OPERATION_SCHEMAS[name].keys],
    required_keys: [...OPERATION_SCHEMAS[name].required],
    // The parent still owes all four of these; naming them keeps the seam honest
    // rather than implying this module closed them.
    registered_in_scac: false,
    registered_in_mutation_registry: false,
    migration_bound: false,
    accepted: false,
  })));
}

// ---------------------------------------------------------------------------
// The authenticated transaction context.
//
// The actor arrives from the handler's own authenticated context — never from a
// tool payload — and is then CHECKED AGAINST THE DATABASE's independently
// derived answer. Two derivations that disagree is not a value to reconcile; it
// is a request that cannot be attributed, so it refuses before any write.
// ---------------------------------------------------------------------------

const CONTEXT_KEYS = Object.freeze(["actor"]);

function assertAuthenticatedContext(context) {
  assertClosed(context ?? {}, CONTEXT_KEYS, CONTEXT_KEYS, "context");
  const actor = context.actor;
  if (!isPlainObject(actor) || !isKnownActor(actor.slug)) {
    fail("unauthenticated_actor",
      "context.actor must be an authenticated actor from the server-established grant",
      { path: "context.actor" });
  }
  const authorization_class = authorizationClassForActor(actor);
  // identity.js knows more classes than this lifecycle admits — probe, review
  // and unsponsored agents among them. Rather than letting the kernel throw an
  // unknown-vocabulary error on a class that is simply not entitled to move a
  // business record, the boundary is drawn here, by name.
  if (!V5_J102_ACTOR_CLASSES.includes(authorization_class)) {
    fail("actor_class_not_admitted_for_lifecycle",
      `${actor.slug} holds ${authorization_class}, which is not admitted to the CRE lifecycle`,
      { actor_slug: actor.slug, authorization_class,
        admitted: [...V5_J102_ACTOR_CLASSES] });
  }
  return deepFreeze({
    slug: actor.slug,
    human: actor.human === true,
    authorization_class,
    derived_by: "authenticated_handler_context",
  });
}

function assertOperationAuthority(operation, principal) {
  const schema = OPERATION_SCHEMAS[operation];
  if (schema.humanOnly && principal.human !== true) {
    fail("human_only_operation_refused",
      `${operation} is humanOnly; ${principal.slug} is not a human principal`,
      { operation, actor_slug: principal.slug });
  }
  if (schema.authorityOnly && principal.authorization_class !== "verified_partner") {
    fail("authority_only_operation_refused",
      `${operation} is authorityOnly; ${principal.slug} holds ${principal.authorization_class}`,
      { operation, actor_slug: principal.slug, authorization_class: principal.authorization_class });
  }
  return true;
}

// ---------------------------------------------------------------------------
// Envelopes.
//
// `record` is the exact preimage the kernel produced (or, for the store-own
// kinds, the exact preimage defined here). `record_digest` is its digest, and
// the ops CHECK constraints recompute both inside PostgreSQL, so an envelope
// that lies about its own bytes cannot be stored at all.
// ---------------------------------------------------------------------------

function storeEnvelope(record_kind, record, extra = {}) {
  if (!V5_J102_STORE_RECORD_KINDS.includes(record_kind)) {
    fail("unknown_record_kind", `"${record_kind}" is not a registered stored record kind`,
      { record_kind });
  }
  return deepFreeze({
    schema_version: V5_J102_ENVELOPE_SCHEMA_VERSION,
    record_kind,
    tenant: ORGANIZATION_TENANT_ID,
    record,
    record_digest: digest(record),
    domain_policy_digest: v5J102PolicyDigest(),
    decision_subset_digest: v5J102DecisionSubsetDigest(),
    ...extra,
  });
}

/** The exact canonical bytes an envelope hashes to, for the byte-for-byte fixtures. */
export function v5J102EnvelopeCanonicalBytes(envelope) {
  return canonicalJson(envelope);
}

export function v5J102StoreEnvelope(record_kind, record, extra = {}) {
  return storeEnvelope(record_kind, record, extra);
}

export function storedSubjectRecord({ subject, transition_id, prior_state_digest, updated_by, updated_at }) {
  return {
    schema_version: V5_J102_STORED_SUBJECT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    subject_kind: subject.subject_kind,
    subject_id: subject.subject_id,
    state: subject,
    established_by_transition: transition_id,
    prior_state_digest: prior_state_digest ?? null,
    updated_by,
    updated_at,
  };
}

export function storedEventRecord({ event, transition_id, evidence_references, recorded_by, recorded_at }) {
  return {
    schema_version: V5_J102_STORED_EVENT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    event,
    transition_id,
    // The exact evidence the event rests on, by reference, so the history says
    // what a transition was judged against rather than only what it changed.
    evidence_references: [...evidence_references],
    recorded_by,
    recorded_at,
  };
}

export function storedFirstPartyFactRecord({ fact, recorded_by, recorded_at }) {
  return {
    schema_version: V5_J102_STORED_FACT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    record_kind: fact.record_kind,
    record_id: fact.record_id,
    reason: fact.reason ?? null,
    detail: fact.detail ?? null,
    closing_date: fact.closing_date ?? null,
    supporting_document_id: fact.supporting_document_id ?? null,
    recorded_by,
    recorded_at,
    // Stated in the record itself, because this is the shape most easily
    // mistaken for a state change: it is a business fact with an author, and a
    // transition still has to accept it.
    advances_lifecycle_state: false,
  };
}

export function storedSalesforceReferenceRecord({ reference, recorded_by, recorded_at }) {
  return {
    schema_version: V5_J102_STORED_REFERENCE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    opportunity_id: reference.opportunity_id,
    // Salesforce's own name and phase, preserved verbatim and never mapped.
    opportunity_name: reference.opportunity_name,
    opportunity_phase: reference.opportunity_phase,
    linked_subject_kind: reference.linked_subject_kind,
    linked_subject_id: reference.linked_subject_id,
    observed_at: reference.observed_at,
    is_external_corporate_reference: true,
    phase_label_is_doctorcre_state: false,
    recorded_by,
    recorded_at,
  };
}

export function storedCorrectionReceiptRecord({
  subject_kind, subject_id, correction_record_id, corrected_fields, reason,
  prior_state_digest, corrected_by, corrected_at,
}) {
  return {
    schema_version: V5_J102_STORED_CORRECTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    subject_kind,
    subject_id,
    correction_record_id,
    corrected_fields: [...corrected_fields],
    reason,
    prior_state_digest: prior_state_digest ?? null,
    corrected_by,
    corrected_at,
    // The three properties that make a correction reviewable rather than a
    // quiet edit.
    append_only: true,
    prior_state_preserved: true,
    derived_from_assistant_text: false,
  };
}

// ---------------------------------------------------------------------------
// The store.
// ---------------------------------------------------------------------------

function requireDb(db) {
  if (!db || typeof db.query !== "function") {
    fail("database_handle_required",
      "createCreLifecycleStore requires an injected database handle with query(text, params)");
  }
  return db;
}

async function one(client, text, params = []) {
  const result = await client.query(text, params);
  const rows = result?.rows ?? [];
  return rows.length > 0 ? rows[0] : null;
}

const J = value => JSON.stringify(value ?? null);
const parse = value => (typeof value === "string" ? JSON.parse(value) : value);

/**
 * Build the store. Everything it touches is injected: the database handle, and
 * the authenticated context supplied per call. It opens no connection, reads no
 * environment, discovers no credential and holds no clock.
 */
export function createCreLifecycleStore({ db } = {}) {
  const handle = requireDb(db);

  async function withTransaction(fn) {
    if (typeof handle.transaction === "function") return handle.transaction(fn);
    await handle.query("BEGIN");
    try {
      const result = await fn(handle);
      await handle.query("COMMIT");
      return result;
    } catch (error) {
      try { await handle.query("ROLLBACK"); } catch { /* the original error is the answer */ }
      throw error;
    }
  }

  /**
   * Open one operation: derive the actor and the instant FROM THE SERVER, and
   * refuse when the database's independently derived actor is not the one the
   * handler authenticated.
   */
  async function openOperation(client, operation, principal) {
    const row = await one(client,
      "SELECT ops.f01_principal() AS principal, ops.f01_now_text() AS server_now");
    if (!row) {
      fail("transaction_context_unavailable",
        "the database did not return a principal; the transaction context was never established",
        { operation });
    }
    const dbPrincipal = parse(row.principal);
    if (dbPrincipal?.actor_slug !== principal.slug) {
      fail("actor_context_mismatch",
        "the database-derived actor is not the handler's authenticated actor; the write cannot be attributed",
        { operation, handler_actor: principal.slug, database_actor: dbPrincipal?.actor_slug ?? null });
    }
    if (OPERATION_SCHEMAS[operation].authorityOnly &&
        (dbPrincipal.human !== principal.human ||
         dbPrincipal.authorization_class !== principal.authorization_class)) {
      fail("actor_context_mismatch", "the database principal does not attest the required authority",
        { operation, actor_slug: principal.slug });
    }
    return { now: row.server_now, database_principal: dbPrincipal };
  }

  function requestDigest(operation, payload, principal) {
    // The idempotency key is bound to the OPERATION, the exact payload and the
    // actor. A replay of the same bytes returns the same record; the same key
    // over different bytes refuses rather than substituting one write for
    // another.
    return digest({
      schema_version: V5_J102_STORE_SCHEMA_VERSION,
      operation,
      actor_slug: principal.slug,
      payload,
    });
  }

  function begin(operation, payload, context) {
    const schema = OPERATION_SCHEMAS[operation];
    const principal = assertAuthenticatedContext(context);
    assertOperationAuthority(operation, principal);
    const validated = assertClosed(payload ?? {}, schema.keys, schema.required, "payload");
    if (validated.schema_version !== undefined &&
        validated.schema_version !== V5_J102_STORE_SCHEMA_VERSION) {
      fail("unknown_schema_version",
        `payload.schema_version must be "${V5_J102_STORE_SCHEMA_VERSION}"`,
        { operation, expected: V5_J102_STORE_SCHEMA_VERSION });
    }
    if (schema.write) assertIdempotencyKey(validated.idempotency_key, "payload.idempotency_key");
    return { principal, payload: validated };
  }

  function result(operation, decision, reason_id, extra = {}) {
    return deepFreeze({
      schema_version: V5_J102_STORE_SCHEMA_VERSION,
      operation,
      tenant: ORGANIZATION_TENANT_ID,
      decision,
      reason_id,
      ...extra,
      // Stated on every result: persistence is a record, never an act in the
      // world. Nothing here signs, sends, files, pays, calls Salesforce or
      // touches a Tour.
      provider_calls: 0,
      salesforce_calls: 0,
      documents_sent: 0,
      creates_or_activates_tour: false,
      effects: V5_NO_EFFECTS,
    });
  }

  /**
   * SQL constructs and integrity-checks the saved outcome. Initial application
   * and replay use this same projection, so no freshly evaluated decision can
   * overwrite the meaning of a previously committed mutation — a replay reports
   * what LANDED, not what the world would say now.
   */
  function resultFromOutcome(operation, outcome, principal) {
    if (!isPlainObject(outcome) || outcome.operation !== operation ||
        outcome.actor_slug !== principal.slug) {
      fail("invalid_stored_outcome", "stored outcome operation or actor does not match", { operation });
    }
    if (typeof outcome.reason_id !== "string" || outcome.reason_id.length === 0) {
      fail("invalid_stored_outcome", "stored outcome is missing its original reason", { operation });
    }
    const extra = { actor_slug: outcome.actor_slug, readback: outcome.readback ?? null };
    if (OPERATION_SCHEMAS[operation].transition !== null) {
      Object.assign(extra, {
        transition_id: outcome.transition_id,
        subject_digests: outcome.subject_digests ?? null,
        event_digests: outcome.event_digests ?? null,
        coupled_facts_committed: outcome.coupled_facts_committed ?? [],
        evidence_rechecked_under_lock: outcome.evidence_rechecked_under_lock === true,
        // Said on every applied transition, because it is what Q082 buys: the
        // whole coupled set landed, or none of it did.
        partial_application: false,
        free_form_stage_update: false,
      });
    } else if (operation === "record-lifecycle-fact") {
      Object.assign(extra, {
        record_kind: outcome.record_kind, record_id: outcome.record_id,
        record_digest: outcome.record_digest, advances_lifecycle_state: false,
      });
    } else if (operation === "link-salesforce-reference") {
      Object.assign(extra, {
        opportunity_id: outcome.opportunity_id, reference_digest: outcome.reference_digest,
        linked_subject_kind: outcome.linked_subject_kind ?? null,
        sets_lifecycle_state: false, phase_label_is_doctorcre_state: false,
      });
    } else if (operation === "record-lifecycle-correction") {
      Object.assign(extra, {
        receipt_digest: outcome.receipt_digest,
        corrected_fields: outcome.corrected_fields ?? [],
        append_only: true, prior_state_preserved: true,
        derived_from_assistant_text: false,
      });
    } else {
      fail("invalid_stored_outcome", "not a replayable write operation", { operation });
    }
    return result(operation, outcome.decision ?? "allow", outcome.reason_id, extra);
  }

  async function replayOutcome(client, operation, request, principal) {
    const row = await one(client,
      "SELECT ops.j102_replay_outcome($1::text, $2::text, $3::text) AS outcome",
      [operation, request.idempotency_key, requestDigest(operation, request, principal)]);
    const outcome = parse(row?.outcome);
    return outcome == null ? null : resultFromOutcome(operation, outcome, principal);
  }

  // -- loading ---------------------------------------------------------------

  /**
   * Load one subject and its CAS digest. A subject that does not exist is not an
   * error here: `establish-client-and-engagement` legitimately creates the
   * engagement, and the transitions that require an existing subject refuse in
   * the kernel with their own reason.
   */
  async function loadSubject(client, subject_kind, subject_id) {
    const row = await one(client, "SELECT ops.j102_subject($1::text, $2::text) AS subject",
      [subject_kind, subject_id]);
    const stored = parse(row?.subject);
    if (stored == null) return { state: null, state_digest: null };
    if (stored.state_digest !== digest(stored.state)) {
      fail("corrupt_stored_subject",
        "the stored subject no longer hashes to its recorded digest; it is refused, not repaired",
        { subject_kind, subject_id });
    }
    return { state: stored.state, state_digest: stored.state_digest };
  }

  function evidenceProvenance(reader, now) {
    return {
      loaded_by: V5_J102_EVIDENCE_LOADER,
      reader,
      loaded_at: now,
      integrity: V5_J102_EVIDENCE_INTEGRITY,
    };
  }

  /**
   * Resolve one caller REFERENCE into a server-loaded evidence record.
   *
   * Returns `{ evidence, recheck }`. The recheck manifest is what travels to
   * ops.j102_apply_transition so the SAME pin is re-read under the lock: a
   * document that gained a version, an artifact that vanished, or a first-party
   * record that was superseded between this read and the write refuses there
   * rather than being applied against a picture that has moved.
   */
  async function loadEvidence(client, ref, now, operation) {
    // hasOwnProperty, not a bare lookup. The key arrives from a caller payload,
    // and `{}["constructor"]` answers with something truthy — a fail-closed
    // branch that could be entered or skipped by naming an inherited property is
    // not fail-closed. The kind is already validated against the registry
    // upstream; this makes the guard structural rather than dependent on that.
    const absent = Object.prototype.hasOwnProperty.call(V5_J102_ABSENT_EVIDENCE_READERS,
      ref.evidence_kind)
      ? V5_J102_ABSENT_EVIDENCE_READERS[ref.evidence_kind]
      : undefined;
    if (absent !== undefined) {
      // FAIL CLOSED, with the missing fact NAMED. This is returned as a policy
      // refusal rather than thrown, so a caller can record why the path is shut
      // and what would have to exist to open it.
      return { refusal: absent };
    }
    if (ref.source === "f01_document") {
      const row = await one(client, "SELECT ops.f01_read('document', $1::jsonb) AS body",
        [J({ document_id: ref.document_id })]);
      const verified = parse(row?.body)?.body ?? null;
      if (verified == null || !isPlainObject(verified.record)) {
        return { refusal: { missing_fact: "f01_document_not_found",
          why: `no current document version exists for ${ref.document_id}`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_document" } };
      }
      const record = verified.record;
      const identity = record.neon_identity ?? {};
      // THE PIN IS CHECKED, NOT BELIEVED. A caller that named a version or a
      // digest the record layer does not hold has decided against a document
      // that is not the stored one, and that refuses here rather than being
      // silently upgraded to whatever is current.
      if (identity.version_no !== ref.expected_version_no) {
        return { refusal: { missing_fact: "f01_document_version_moved",
          why: `document ${ref.document_id} is at version ${identity.version_no ?? "unknown"}, not the ${ref.expected_version_no} this request was decided against`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_document" } };
      }
      if (identity.content_digest !== ref.expected_content_digest) {
        return { refusal: { missing_fact: "f01_document_content_digest_mismatch",
          why: `document ${ref.document_id} version ${ref.expected_version_no} does not carry the content digest this request named`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_document" } };
      }
      return {
        evidence: {
          evidence_kind: ref.evidence_kind,
          source: "f01_document",
          reference: ref.document_id,
          document: {
            document_id: identity.document_id,
            document_class: record.document_class,
            version_no: identity.version_no,
            content_digest: identity.content_digest,
            preparation_state: record.preparation_state,
            delivery_state: record.delivery_state,
            signature_state: record.signature_state,
            validity_state: record.validity_state,
            version_state: record.version_state,
            // F01 carries no dated effective window; see the kernel's contract
            // note on signed_engagement_letter for why that is reported rather
            // than filled in.
            effective_from: null,
            effective_to: null,
          },
          provenance: evidenceProvenance("ops.f01_read.document", now),
        },
        recheck: {
          evidence_kind: ref.evidence_kind, source: "f01_document",
          reader: "ops.f01_read.document",
          selector: { document_id: ref.document_id },
          expected_version_no: ref.expected_version_no,
          expected_content_digest: ref.expected_content_digest,
        },
      };
    }
    if (ref.source === "f01_corporate_artifact") {
      const row = await one(client, "SELECT ops.f01_stored_artifact($1::text) AS artifact",
        [ref.artifact_digest]);
      const stored = parse(row?.artifact);
      if (stored == null || !isPlainObject(stored.artifact)) {
        return { refusal: { missing_fact: "f01_artifact_not_found",
          why: `no stored corporate artifact exists for ${ref.artifact_digest}`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_artifact" } };
      }
      const artifact = stored.artifact;
      return {
        evidence: {
          evidence_kind: ref.evidence_kind,
          source: "f01_corporate_artifact",
          reference: ref.artifact_digest,
          artifact: {
            artifact_digest: stored.artifact_digest,
            content_digest: artifact.content_digest,
            source_system: artifact.source_system,
            evidence_class: artifact.evidence_class ?? null,
            observed_at: artifact.observed_at,
          },
          provenance: evidenceProvenance("ops.f01_stored_artifact", now),
        },
        recheck: {
          evidence_kind: ref.evidence_kind, source: "f01_corporate_artifact",
          reader: "ops.f01_stored_artifact",
          selector: { artifact_digest: ref.artifact_digest },
        },
      };
    }
    const row = await one(client,
      "SELECT ops.j102_first_party_record($1::text, $2::text) AS record",
      [ref.record_kind, ref.record_id]);
    const stored = parse(row?.record);
    if (stored == null || !isPlainObject(stored.record)) {
      return { refusal: { missing_fact: "first_party_record_not_found",
        why: `no ${ref.record_kind} record exists with id ${ref.record_id}; record-lifecycle-fact writes one`,
        evidence_kind: ref.evidence_kind, produced_by: "j102_record_first_party_fact" } };
    }
    const record = stored.record;
    return {
      evidence: {
        evidence_kind: ref.evidence_kind,
        source: "first_party_record",
        reference: ref.record_id,
        record: {
          record_kind: record.record_kind,
          record_id: record.record_id,
          content_digest: stored.record_digest,
          recorded_by: record.recorded_by,
          recorded_at: record.recorded_at,
          reason: record.reason ?? null,
          detail: record.detail ?? null,
          closing_date: record.closing_date ?? null,
          supporting_document_id: record.supporting_document_id ?? null,
        },
        provenance: evidenceProvenance("ops.j102_first_party_record", now),
      },
      recheck: {
        evidence_kind: ref.evidence_kind, source: "first_party_record",
        reader: "ops.j102_first_party_record",
        selector: { record_kind: ref.record_kind, record_id: ref.record_id },
        expected_record_digest: stored.record_digest,
      },
    };
  }

  // -- the shared transition path -------------------------------------------

  /**
   * Every lifecycle transition runs through here, so there is ONE place that
   * loads, judges, envelopes and applies — and one place a reviewer has to read
   * to know what any of the eleven transition operations does.
   *
   * ORDERED, and the order is load-bearing:
   *   1. Authenticate, validate the closed payload, claim authority.
   *   2. Open the transaction, derive actor and instant from the SERVER.
   *   3. REPLAY FIRST, before any state read. A settled key must return its
   *      stored result even though the world has moved since; a replay after the
   *      CAS would refuse a request that had already succeeded.
   *   4. Load the subject and every related subject, with their CAS digests.
   *   5. Resolve every evidence reference into a server-loaded record. A missing
   *      reader or a moved pin refuses here, with the fact named.
   *   6. Run the KERNEL. It judges; nothing here second-guesses it.
   *   7. On allow, build one envelope per proposed subject and per event, and
   *      hand them to ops.j102_apply_transition with the CAS digests and the
   *      recheck manifest. The database re-reads the evidence under its lock and
   *      writes the whole coupled set or none of it.
   */
  async function runTransition(operation, payload, context, { chooseTransition } = {}) {
    const schema = OPERATION_SCHEMAS[operation];
    const { principal, payload: request } = begin(operation, payload, context);
    const subject_ref = assertSubjectRef(request.subject_ref, "payload.subject_ref", schema.subject_kind);

    const relatedRefs = {};
    if (request.related_refs !== undefined && request.related_refs !== null) {
      const raw = assertClosed(request.related_refs, RELATED_REF_KEYS, [], "payload.related_refs");
      for (const key of RELATED_REF_KEYS) {
        if (raw[key] === undefined || raw[key] === null) continue;
        relatedRefs[key] = assertSubjectRef(raw[key], `payload.related_refs.${key}`, key);
      }
    }

    const rawEvidenceRefs = request.evidence_refs;
    if (!Array.isArray(rawEvidenceRefs) || rawEvidenceRefs.length < 1 || rawEvidenceRefs.length > 8) {
      fail("invalid_shape", "payload.evidence_refs must name between 1 and 8 evidence references",
        { path: "payload.evidence_refs" });
    }
    const evidenceRefs = rawEvidenceRefs.map((raw, i) =>
      assertEvidenceRef(raw, `payload.evidence_refs[${i}]`));

    // `declared` is what the caller declared, and it is what the dispatchers in
    // this module read. `domainDeclared` is the subset the KERNEL's closed
    // contract accepts, and it is the only one that reaches the judgement.
    const declared = {};
    const domainDeclared = {};
    if (request.declared !== undefined && request.declared !== null) {
      const raw = assertClosed(request.declared, DECLARED_KEYS, [], "payload.declared");
      for (const key of DECLARED_KEYS) {
        if (raw[key] === undefined || raw[key] === null) continue;
        declared[key] = raw[key];
        if (DECLARED_DOMAIN_KEYS.includes(key)) domainDeclared[key] = raw[key];
      }
    }

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;

      const loadedSubject = await loadSubject(client, subject_ref.subject_kind, subject_ref.subject_id);
      if (loadedSubject.state === null) {
        return result(operation, "refuse", "subject_not_found", {
          actor_slug: principal.slug,
          subject_kind: subject_ref.subject_kind, subject_id: subject_ref.subject_id,
          records_written: 0, readback: null,
        });
      }
      // THE COMPARE-AND-SWAP IS DECIDED AGAINST THE STORED SUBJECT, not against
      // the caller's belief about it. The database re-checks the same digest
      // under its lock; this early check exists so a stale caller learns which
      // subject moved rather than getting a generic serialization error.
      if (subject_ref.expected_state_digest !== null &&
          subject_ref.expected_state_digest !== loadedSubject.state_digest) {
        return result(operation, "refuse", "stale_subject_digest", {
          actor_slug: principal.slug,
          subject_kind: subject_ref.subject_kind, subject_id: subject_ref.subject_id,
          stored_state_digest: loadedSubject.state_digest,
          expected_state_digest: subject_ref.expected_state_digest,
          records_written: 0, readback: null,
        });
      }

      const related = {};
      const casDigests = {
        [`${subject_ref.subject_kind}:${subject_ref.subject_id}`]: loadedSubject.state_digest,
      };
      for (const [key, ref] of Object.entries(relatedRefs)) {
        const loaded = await loadSubject(client, ref.subject_kind, ref.subject_id);
        if (loaded.state === null) {
          return result(operation, "refuse", "related_subject_not_found", {
            actor_slug: principal.slug, related_kind: key, related_id: ref.subject_id,
            records_written: 0, readback: null,
          });
        }
        if (ref.expected_state_digest !== null && ref.expected_state_digest !== loaded.state_digest) {
          return result(operation, "refuse", "stale_related_subject_digest", {
            actor_slug: principal.slug, related_kind: key, related_id: ref.subject_id,
            stored_state_digest: loaded.state_digest,
            expected_state_digest: ref.expected_state_digest,
            records_written: 0, readback: null,
          });
        }
        related[key] = loaded.state;
        casDigests[`${ref.subject_kind}:${ref.subject_id}`] = loaded.state_digest;
      }

      const evidence = [];
      const rechecks = [];
      for (const ref of evidenceRefs) {
        const loaded = await loadEvidence(client, ref, now, operation);
        if (loaded.refusal !== undefined) {
          return result(operation, "refuse", "required_evidence_unavailable", {
            actor_slug: principal.slug,
            evidence_kind: loaded.refusal.evidence_kind ?? ref.evidence_kind,
            // The whole point of this branch: the caller is told WHICH fact is
            // missing and who would have to produce it, rather than being told
            // the transition failed.
            missing_fact: loaded.refusal.missing_fact,
            missing_fact_reason: loaded.refusal.why,
            produced_by: loaded.refusal.produced_by,
            fabricated_authority: false,
            records_written: 0, readback: null,
          });
        }
        evidence.push(loaded.evidence);
        rechecks.push(loaded.recheck);
      }

      const transition_id = typeof chooseTransition === "function"
        ? chooseTransition({ subject: loadedSubject.state, declared })
        : schema.transition;
      if (typeof transition_id !== "string" || !V5_J102_TRANSITION_IDS.includes(transition_id)) {
        return result(operation, "refuse", "transition_not_determined", {
          actor_slug: principal.slug, records_written: 0, readback: null,
        });
      }

      const evaluated = evaluateLifecycleTransition({
        tenant: ORGANIZATION_TENANT_ID,
        transition_id,
        subject: loadedSubject.state,
        related,
        evidence,
        actor: principal,
        // The selector chose the transition above and stops here; only the
        // kernel's own declared vocabulary crosses into the judgement.
        declared: domainDeclared,
        now,
      });
      if (evaluated.decision !== "allow") {
        return result(operation, evaluated.decision, evaluated.reason_id, {
          actor_slug: principal.slug,
          transition_id,
          subject_kind: subject_ref.subject_kind, subject_id: subject_ref.subject_id,
          refusal_detail: refusalDetail(evaluated),
          records_written: 0, readback: null,
        });
      }

      const evidence_references = evidence.map(e => ({
        evidence_kind: e.evidence_kind, source: e.source, reference: e.reference,
      }));
      const subjectEnvelopes = Object.entries(evaluated.proposed_state).map(([kind, state]) =>
        storeEnvelope("stored_lifecycle_subject", storedSubjectRecord({
          subject: state,
          transition_id,
          prior_state_digest: casDigests[`${kind}:${state.subject_id}`] ?? null,
          updated_by: principal.slug,
          updated_at: now,
        }), { alone_sufficient: false }));
      const eventEnvelopes = evaluated.events.map(event =>
        storeEnvelope("stored_lifecycle_event", storedEventRecord({
          event, transition_id, evidence_references,
          recorded_by: principal.slug, recorded_at: now,
        }), { append_only: true }));

      const row = await one(client,
        `SELECT ops.j102_apply_transition($1::text, $2::jsonb, $3::jsonb, $4::jsonb,
                                          $5::jsonb, $6::text, $7::text, $8::jsonb) AS outcome`,
        [transition_id, J(casDigests), J(subjectEnvelopes), J(eventEnvelopes), J(rechecks),
         request.idempotency_key, requestDigest(operation, request, principal),
         J({ operation, reason_id: evaluated.reason_id,
             coupled_facts: evaluated.coupled_facts_committed,
             decision_refs: evaluated.decision_refs })]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  /** The refusal fields worth carrying back, without echoing the whole answer. */
  function refusalDetail(evaluated) {
    const detail = {};
    for (const key of ["unmet_axis", "observed", "permitted", "evidence_kind", "document_axis",
      "required", "expected_subject_kind", "permitted_actor_classes", "actor_authorization_class",
      "supplied_evidence_kinds", "required_evidence_alternatives", "unexpected_evidence_kinds",
      "closing_date", "negotiation_state", "assignment_phase", "engagement_state",
      "relationship_state", "diligence_state", "pending_deal_id", "selected_property_id",
      "open_negotiation_count", "instrument_kind", "permitted_instrument_kinds"]) {
      if (evaluated[key] !== undefined) detail[key] = evaluated[key];
    }
    return deepFreeze(detail);
  }

  // -- 1. read-cre-lifecycle -------------------------------------------------

  async function readCreLifecycle(payload, context) {
    const operation = "read-cre-lifecycle";
    const { principal, payload: request } = begin(operation, payload, context);
    const selector = assertClosed(request.selector, READ_SELECTOR_KEYS, ["kind"], "payload.selector");
    if (!V5_J102_READ_KINDS.includes(selector.kind)) {
      fail("unknown_read_kind", `"${selector.kind}" is not a registered read kind`,
        { kind: selector.kind, registered: [...V5_J102_READ_KINDS] });
    }
    if (selector.subject_kind !== undefined && selector.subject_kind !== null &&
        !V5_J102_SUBJECT_KINDS.includes(selector.subject_kind)) {
      fail("unknown_subject_kind", `"${selector.subject_kind}" is not a registered subject kind`,
        { registered: [...V5_J102_SUBJECT_KINDS] });
    }
    return withTransaction(async client => {
      await openOperation(client, operation, principal);
      const row = await one(client, "SELECT ops.j102_read($1::text, $2::jsonb) AS body",
        [selector.kind, J(Object.fromEntries(
          READ_SELECTOR_KEYS.filter(k => k !== "kind" && selector[k] !== undefined)
            .map(k => [k, selector[k]])))]);
      return result(operation, "allow", "read_recomputed_from_committed_rows", {
        actor_slug: principal.slug,
        kind: selector.kind,
        readback: parse(row?.body) ?? null,
        integrity: "recomputed_not_trusted",
        stale_fallback_permitted: false,
      });
    });
  }

  // -- 2. record-lifecycle-fact ---------------------------------------------

  async function recordLifecycleFact(payload, context) {
    const operation = "record-lifecycle-fact";
    const { principal, payload: request } = begin(operation, payload, context);
    const fact = assertClosed(request.fact, FACT_BODY_KEYS, ["record_kind", "record_id"], "payload.fact");
    // The kind must be one some evidence contract actually consumes. A record
    // nothing can ever be judged against is not a business fact, it is a note,
    // and this store is not a place to keep notes.
    const consumed = V5_J102_EVIDENCE_KINDS
      .map(kind => v5J102EvidenceContract(kind))
      .filter(c => c.source === "first_party_record")
      .map(c => c.record_kind);
    if (!consumed.includes(fact.record_kind)) {
      fail("unknown_first_party_record_kind",
        `"${fact.record_kind}" is consumed by no lifecycle evidence contract`,
        { record_kind: fact.record_kind, registered: [...new Set(consumed)].sort() });
    }
    assertIdent(fact.record_id, "payload.fact.record_id");

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const record = storedFirstPartyFactRecord({
        fact, recorded_by: principal.slug, recorded_at: now,
      });
      const envelope = storeEnvelope("stored_first_party_record", record,
        { append_only: true, advances_lifecycle_state: false });
      const row = await one(client,
        "SELECT ops.j102_record_first_party_fact($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- 3..13. the transition operations -------------------------------------

  const recordRepresentationAgreement = (payload, context) =>
    runTransition("record-representation-agreement", payload, context);
  const openCreAssignment = (payload, context) =>
    runTransition("open-cre-assignment", payload, context);
  const recordLoiSubmission = (payload, context) =>
    runTransition("record-loi-submission", payload, context);
  const recordLoiAcceptance = (payload, context) =>
    runTransition("record-loi-acceptance", payload, context);
  const commitWinningProperty = (payload, context) =>
    runTransition("commit-winning-property", payload, context);
  const recordDiligenceOutcome = (payload, context) =>
    runTransition("record-diligence-outcome", payload, context);
  const recordDealClosing = (payload, context) =>
    runTransition("record-deal-closing", payload, context);
  const cancelPendingDeal = (payload, context) =>
    runTransition("cancel-pending-deal", payload, context);

  /**
   * The instrument kind comes from the STORED deal, never from the caller.
   *
   * Q094 gives purchase execution different consequences from lease execution —
   * diligence opens for one and not the other — so a caller able to choose which
   * transition ran could obtain the lease semantics on a purchase and skip
   * diligence entirely. Dispatching on the loaded row makes that unreachable.
   */
  const recordDealExecution = (payload, context) =>
    runTransition("record-deal-execution", payload, context, {
      chooseTransition: ({ subject }) => subject.instrument_kind === "purchase"
        ? "record-purchase-contract-execution"
        : "record-lease-execution",
    });

  const recordDealAxis = (payload, context) =>
    runTransition("record-deal-axis", payload, context, {
      // hasOwnProperty for the same reason loadEvidence uses it: the axis name is
      // a caller value, and an inherited property must not be able to answer for
      // a registered one.
      chooseTransition: ({ declared }) =>
        (typeof declared.axis === "string" &&
         Object.prototype.hasOwnProperty.call(AXIS_TRANSITIONS, declared.axis))
          ? AXIS_TRANSITIONS[declared.axis]
          : null,
    });

  // -- 14. link-salesforce-reference ----------------------------------------

  async function linkSalesforceReference(payload, context) {
    const operation = "link-salesforce-reference";
    const { principal, payload: request } = begin(operation, payload, context);
    // The kernel decides the shape and refuses any attempt to map a Salesforce
    // label onto lifecycle state; this module adds nothing to that judgement.
    const projected = projectSalesforceReference({
      tenant: ORGANIZATION_TENANT_ID,
      opportunity_id: request.opportunity_id,
      opportunity_name: request.opportunity_name,
      opportunity_phase: request.opportunity_phase,
      observed_at: request.observed_at,
      linked_subject_kind: request.linked_subject_kind ?? null,
      linked_subject_id: request.linked_subject_id ?? null,
    });

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      // A link target must EXIST. Q083 links the opportunity progressively to a
      // real prospect, engagement, assignment or Deal; a link to an id nobody
      // holds is a dangling reference wearing the shape of provenance.
      if (projected.linked_subject_kind !== null) {
        const loaded = await loadSubject(client, projected.linked_subject_kind,
          projected.linked_subject_id);
        if (loaded.state === null) {
          return result(operation, "refuse", "link_target_not_found", {
            actor_slug: principal.slug,
            linked_subject_kind: projected.linked_subject_kind,
            linked_subject_id: projected.linked_subject_id,
            records_written: 0, readback: null,
          });
        }
      }
      const envelope = storeEnvelope("stored_salesforce_reference",
        storedSalesforceReferenceRecord({
          reference: projected, recorded_by: principal.slug, recorded_at: now,
        }), { sets_lifecycle_state: false, phase_label_is_doctorcre_state: false });
      const row = await one(client,
        "SELECT ops.j102_record_salesforce_reference($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- 15. record-lifecycle-correction --------------------------------------

  /**
   * A correction is a RECEIPT, not an overwrite.
   *
   * It requires a human verified partner, a reason, and a first-party
   * lifecycle_correction record loaded from the record layer — so the correction
   * itself has an author and durable content, and cannot be conjured from an
   * assistant's summary of what somebody meant. The prior state digest is bound
   * into the receipt, so the history says exactly which version was corrected.
   */
  async function recordLifecycleCorrection(payload, context) {
    const operation = "record-lifecycle-correction";
    const { principal, payload: request } = begin(operation, payload, context);
    const subject_ref = assertClosed(request.subject_ref, SUBJECT_REF_KEYS,
      ["subject_kind", "subject_id"], "payload.subject_ref");
    if (!V5_J102_SUBJECT_KINDS.includes(subject_ref.subject_kind)) {
      fail("unknown_subject_kind", `"${subject_ref.subject_kind}" is not a registered subject kind`,
        { registered: [...V5_J102_SUBJECT_KINDS] });
    }
    assertIdent(subject_ref.subject_id, "payload.subject_ref.subject_id");
    assertIdent(request.correction_record_id, "payload.correction_record_id");
    if (!Array.isArray(request.corrected_fields) || request.corrected_fields.length < 1 ||
        request.corrected_fields.length > 64) {
      fail("invalid_shape", "payload.corrected_fields must name between 1 and 64 fields",
        { path: "payload.corrected_fields" });
    }
    const corrected_fields = request.corrected_fields.map((field, i) =>
      assertIdent(field, `payload.corrected_fields[${i}]`));
    if (typeof request.reason !== "string" || request.reason.trim().length === 0 ||
        request.reason.length > 1000) {
      fail("invalid_shape", "payload.reason must be a non-empty reason of at most 1000 characters",
        { path: "payload.reason" });
    }

    return withTransaction(async client => {
      const { now } = await openOperation(client, operation, principal);
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const loaded = await loadSubject(client, subject_ref.subject_kind, subject_ref.subject_id);
      if (loaded.state === null) {
        return result(operation, "refuse", "subject_not_found", {
          actor_slug: principal.slug, subject_kind: subject_ref.subject_kind,
          subject_id: subject_ref.subject_id, records_written: 0, readback: null,
        });
      }
      if (subject_ref.expected_state_digest !== undefined &&
          subject_ref.expected_state_digest !== null &&
          subject_ref.expected_state_digest !== loaded.state_digest) {
        return result(operation, "refuse", "stale_subject_digest", {
          actor_slug: principal.slug, stored_state_digest: loaded.state_digest,
          expected_state_digest: subject_ref.expected_state_digest,
          records_written: 0, readback: null,
        });
      }
      // The correction's own record must EXIST and be a lifecycle_correction. An
      // approval that lives only in a chat transcript is not one of these.
      const recordRow = await one(client,
        "SELECT ops.j102_first_party_record($1::text, $2::text) AS record",
        ["lifecycle_correction", request.correction_record_id]);
      const storedRecord = parse(recordRow?.record);
      if (storedRecord == null || !isPlainObject(storedRecord.record)) {
        return result(operation, "refuse", "correction_record_not_found", {
          actor_slug: principal.slug,
          missing_fact: "first_party_lifecycle_correction_record",
          missing_fact_reason: "a correction is receipted against a durable authored record; record-lifecycle-fact writes one",
          produced_by: "j102_record_first_party_fact",
          derived_from_assistant_text: false,
          records_written: 0, readback: null,
        });
      }
      const envelope = storeEnvelope("stored_correction_receipt",
        storedCorrectionReceiptRecord({
          subject_kind: subject_ref.subject_kind,
          subject_id: subject_ref.subject_id,
          correction_record_id: request.correction_record_id,
          corrected_fields,
          reason: request.reason,
          prior_state_digest: loaded.state_digest,
          corrected_by: principal.slug,
          corrected_at: now,
        }), { append_only: true, humanOnly: true, authorityOnly: true });
      const row = await one(client,
        "SELECT ops.j102_record_correction($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  return Object.freeze({
    readCreLifecycle,
    recordLifecycleFact,
    recordRepresentationAgreement,
    openCreAssignment,
    recordLoiSubmission,
    recordLoiAcceptance,
    commitWinningProperty,
    recordDealExecution,
    recordDiligenceOutcome,
    recordDealClosing,
    cancelPendingDeal,
    recordDealAxis,
    linkSalesforceReference,
    recordLifecycleCorrection,
  });
}

// ---------------------------------------------------------------------------
// Load-time self-checks, for the invariants a later edit could break silently.
// ---------------------------------------------------------------------------

for (const name of V5_J102_OPERATIONS) {
  const schema = OPERATION_SCHEMAS[name];
  if (schema === undefined) {
    throw new V5J102StoreError("contract_self_check_failed", `${name} has no operation schema`);
  }
  if (schema.transition !== null && schema.transition !== "dispatch_on_instrument_kind" &&
      schema.transition !== "dispatch_on_declared_axis" &&
      !V5_J102_TRANSITION_IDS.includes(schema.transition)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `${name} names unregistered transition "${schema.transition}"`);
  }
  if (schema.subject_kind !== undefined && !V5_J102_SUBJECT_KINDS.includes(schema.subject_kind)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `${name} names unregistered subject kind "${schema.subject_kind}"`);
  }
}

for (const [axis, transition] of Object.entries(AXIS_TRANSITIONS)) {
  if (!V5_J102_DEAL_AXES.includes(axis)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the axis dispatch names "${axis}", which is not a registered deal axis`);
  }
  if (!V5_J102_TRANSITION_IDS.includes(transition)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the axis dispatch names unregistered transition "${transition}"`);
  }
}

// Every absent-reader entry must name a REAL evidence kind, or the fail-closed
// branch would never fire and the path it is meant to shut would quietly open.
for (const [kind, entry] of Object.entries(V5_J102_ABSENT_EVIDENCE_READERS)) {
  if (!V5_J102_EVIDENCE_KINDS.includes(kind) || entry.evidence_kind !== kind) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the absent-reader registry names "${kind}", which is not a registered evidence kind`);
  }
  if (v5J102EvidenceContract(kind).source !== "typed_approval") {
    throw new V5J102StoreError("contract_self_check_failed",
      `the absent-reader registry names "${kind}", which is not established from a typed approval`);
  }
}
