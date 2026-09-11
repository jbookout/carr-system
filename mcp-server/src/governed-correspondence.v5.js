// DoctorCRE v5 slice V5-J103: governed correspondence reads, drafts, proposed
// facts and source reconciliation — the pure decision kernel.
//
// FOUR SETTLED DECISIONS SIT UNDER THIS FILE and every one of them is a
// prohibition as much as a capability:
//
//   Q005.D1  Core Journey 1 automates reads, drafts and reversible internal
//            writes only; no external effect may be emitted or certified.
//   Q076.D1  Resolve source conflicts by FIELD AUTHORITY AND EVIDENCE, never by
//            timestamp alone; expose values and provenance and route genuine
//            business conflicts for concise review.
//   Q133.D1  Outlook remains mailbox truth while DoctorCRE stores
//            provenance-linked communication threads — participants, partner and
//            source account, provider IDs, time, attachments, related records and
//            correspondence state. J1 may read, draft and infer PROPOSED facts;
//            external send authority belongs to a later workflow.
//   Q134.D1  A SOURCE-AGNOSTIC governed correspondence schema and read interface
//            that uses whichever authorized adapter is already available; it
//            excludes unrelated mail, holds ambiguity private, and never claims
//            both-partner coverage.
//
// TWO KINDS OF NO, the distinction the rest of the v5 lane draws:
//   * A POLICY ANSWER IS RETURNED — a frozen result whose `decision` is one of
//     "read", "drafted", "proposed", "queued", "refuse", "unavailable",
//     "exclude_unrelated", "withhold_ambiguous" or
//     "needs_independent_privacy_route", each with a stable `reason_id`. A caller
//     records it and moves on.
//   * A CONTRACT VIOLATION THROWS V5J103Error. An unknown field, a routable
//     address, a dispatch instruction, raw message content, a credential, or an
//     attempt to draft from a thread this module already withheld are not policy
//     questions. The module cannot read the request as a governed one at all, so
//     it fails closed rather than guessing which boundary was meant.
//
// THE STRUCTURAL REASON A DRAFT CANNOT DISPATCH, and it is not a flag.
//
//   1. THIS MODULE HOLDS NO RECIPIENT DESTINATION, ANYWHERE. A participant is a
//      `participant_ref` — an opaque CARR party reference — plus an
//      `address_digest`. There is no field on any schema here that a recipient
//      email address, phone number or mailto: URI may occupy, and
//      assertNoRoutableAddress throws on any string VALUE shaped like one. The
//      single exemption is the partner's OWN mailbox `account`, which is an
//      identity to read from rather than a destination to send to, and it is
//      pinned to the compiled binding so it cannot become one. A draft that names
//      a recipient nobody can route to is not a draft a program can send; it is a
//      draft a human opens in the client that already holds the thread. That is
//      the whole design: Joe sends.
//   2. THIS MODULE NAMES NO PROVIDER OPERATION. F10 owns the connector operation
//      registry, and every write in it — send_mail_message included — refuses
//      there by name. Re-deciding it here would be a second authority for a
//      settled thing, so nothing below returns an operation key at all.
//      `provider_operation: null` and `dispatchable: false` ride on every draft
//      and proposal result to say so in the record.
//   3. A DISPATCH INSTRUCTION CANNOT ARRIVE. assertNoDispatchFields scans field
//      NAMES for send, dispatch, transmit, deliver, outbound and schedule
//      fragments before any value is read, the same way F10 scans for
//      credentials — by the time such a field exists the caller has already
//      decided this module dispatches, and answering its question politely would
//      confirm a capability that does not exist.
//
// WHAT THIS FILE DOES NOT DECIDE, because something upstream already does:
//
//   * THE PRIVACY BOUNDARY IS S01's (global-boundaries.v5.js). Every path that
//     carries a declared class runs evaluatePrivacyBoundary and carries its
//     reason ids through verbatim; the PHI list is not copied here.
//   * FIELD AUTHORITY AND CONFLICT DETECTION ARE F01's
//     (record-source-authority.v5.js). resolveObservation decides whether an
//     observation is stale, forbidden, indeterminate or contradictory and emits
//     the reconciliation item. This module is the REVIEW SURFACE for that item:
//     it presents both values with the owning source and routes the genuine
//     business conflicts. It never re-decides which side wins, and it has no
//     code path that resolves a conflict by recency — the refused bases are
//     enumerated and hashed into the policy digest.
//   * NATIVE IDENTITY IS F01's. The (source_system, native_id, native_id_epoch)
//     triple is carried through unchanged; an epoch split is F01's refusal.
//   * ADMISSION IS F01's. evaluateProposedFact builds the exact observation
//     shape resolveObservation takes and STOPS. Every result says
//     `authority_established: false`.
//   * WHICH ADAPTERS EXIST IS THE ADAPTER'S OWN. V5_J103_ADAPTERS registers
//     adapter kinds by identity only, and F10's adapter kind is bound BY IMPORT
//     so a rename upstream breaks this module loudly at load instead of leaving
//     it trusting a literal.
//
// SOURCE-AGNOSTIC MEANS THE INTERFACE OUTLIVES THE ADAPTER (Q134). Nothing below
// mentions Outlook except as one registered authoritative home carried from F01's
// vocabulary. An installation of a different authorized adapter compiles the same
// binding and reads through the same interface. What the module will NOT do is
// invent an adapter: a binding whose availability is "unavailable" or "unknown"
// answers `unavailable` by name, exactly as F10 answers when a partner's device
// is not deployed. Silence is never read as readiness.
//
// SUBJECT LINES AND MESSAGE BODIES ARE NOT IN THE SCHEMA, and their absence is
// deliberate rather than unfinished. Q133 enumerates what DoctorCRE stores —
// participants, account, provider IDs, time, attachments, related records, state
// — and a subject line is not on that list. The mailbox remains truth; what
// crosses into CARR is typed metadata, attachment DESCRIPTORS (a media type, a
// byte length and a content digest, never bytes and never a filename), and the
// drafts CARR itself authors.
//
// THE ONE FREE-TEXT FIELD IN THIS MODULE IS `draft_body`, AND THE ASYMMETRY IS
// THE POINT. Source-side objects run assertNoSourceContentFields; the draft side
// does not, because a draft is not correspondence that was read — it is text CARR
// wrote, under the same privacy boundary, which a human will review before it
// becomes anything. Conflating the two would either forbid drafts outright or let
// a mail body ride in under a draft's name, so the two seams are separated by
// name and the closed key sets keep them apart.
//
// THE MODULE IS PURE. No filesystem, no network, no database, no scheduler, no
// environment, no clock: `now` arrives from the caller on every evaluation that
// needs one. It sends nothing, persists nothing, activates nothing and accepts
// nothing. V5_NO_EFFECTS rides on every result to say so in the record.
//
// AND IT PRODUCES NO ACCEPTANCE. Q005.D1, Q076.D1, Q133.D1 and Q134.D1 are all
// satisfied by a production-representative Journey 1 run judged by an independent
// oracle. Passing this suite is not that run, and this module says so rather than
// leaving a reader to infer it.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, isKnownPartner } from "./identity.js";
import { V5_DATA_CLASSES, V5_NO_EFFECTS, evaluatePrivacyBoundary } from "./global-boundaries.v5.js";
import {
  V5_F01_AUTHORITY_INJECTION_FRAGMENTS,
  V5_F01_HOMES,
  V5_F01_RECONCILIATION_SCHEMA_VERSION,
  V5_F01_TAINT_CLASSES,
} from "./record-source-authority.v5.js";
import {
  V5_F10_ADAPTER_KIND,
  V5_F10_AUTHORITATIVE_HOME,
  V5_F10_WRITE_OPERATIONS,
} from "./partner-mail-calendar.v5.js";

export { V5_NO_EFFECTS };

export const V5_J103_SCHEMA_VERSION = "doctorcre-v5-governed-correspondence.v1";
export const V5_J103_POLICY_VERSION = 1;

export const V5_J103_BINDING_SCHEMA_VERSION =
  "doctorcre-v5-j103-correspondence-adapter-binding.v1";
export const V5_J103_THREAD_SCHEMA_VERSION =
  "doctorcre-v5-j103-correspondence-thread.v1";
export const V5_J103_DRAFT_SCHEMA_VERSION =
  "doctorcre-v5-j103-correspondence-draft.v1";
export const V5_J103_PROPOSAL_SCHEMA_VERSION =
  "doctorcre-v5-j103-proposed-fact.v1";
export const V5_J103_CONFLICT_SCHEMA_VERSION =
  "doctorcre-v5-j103-source-conflict-queue-entry.v1";
export const V5_J103_COVERAGE_SCHEMA_VERSION =
  "doctorcre-v5-j103-correspondence-coverage.v1";
export const V5_J103_PROJECTION_SCHEMA_VERSION =
  "doctorcre-v5-j103-correspondence-projection.v1";

// ---------------------------------------------------------------------------
// Validators. Local by design: each v5 module in this lane carries its own, so a
// slice cannot be loosened by an edit to a shared helper it never reviewed.
// ---------------------------------------------------------------------------

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
// A CARR-side reference is deliberately NARROWER than an external identifier: no
// "@", so a participant_ref can never be an address wearing a reference's name.
const INTERNAL_REF = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$/;
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

/**
 * A routable destination: an address with an "@" and a dotted right-hand side, or
 * a URI scheme that carries one. Matched on VALUES rather than names, because the
 * claim this module makes is that no recipient destination exists anywhere in it —
 * a claim that has to hold whatever the caller decided to call the field.
 */
const ROUTABLE_ADDRESS =
  /(^|[\s<,;:"'([])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b(?:mailto|smtp|sms|tel|callto|skype):/i;

/**
 * A dialable number, applied to DRAFT PROSE ONLY and deliberately not to the
 * general scan.
 *
 * The suite caught the reason on its first run. A digit-run heuristic cannot tell
 * a phone number from an identifier, and this module is full of identifiers: a
 * 64-character hex digest contains a run of nine or more digits about nineteen
 * times in twenty, so scanning every value with it would refuse almost every
 * legitimate `content_digest` in the schema. A guard that refuses the schema it
 * guards is worse than no guard. Draft prose carries no identifiers, so the check
 * is both safe and useful there — a draft that embeds a number for a human to dial
 * is a draft trying to arrange contact out of band.
 */
const DIALABLE_NUMBER = /(^|[^\d])\+?\d[\d\s().-]{7,}\d($|[^\d])/;

export class V5J103Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J103Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J103Error(code, message, detail);
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

/** An open schema is an unenforced one: an unread field is a field nobody checked. */
function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertArray(value, path, { min = 0, max = 256 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min) {
    fail("invalid_shape", `${path} must hold at least ${min} entries`, { path, length: value.length });
  }
  if (value.length > max) {
    fail("too_many_entries", `${path} may hold at most ${max} entries`, { path, length: value.length });
  }
  return value;
}

function assertSafeText(value, path, { maxLength = 512 } = {}) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  if (value.length > maxLength) {
    fail("text_too_long", `${path} may be at most ${maxLength} characters`, { path, length: value.length });
  }
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  }
  if (UNSAFE_TEXT.test(value)) {
    fail("unsafe_unicode", `${path} contains a control, bidirectional or invisible format character`, { path });
  }
  return value;
}

function assertExternalIdent(value, path, { maxLength = 255 } = {}) {
  assertSafeText(value, path, { maxLength });
  if (!EXTERNAL_IDENT.test(value)) {
    fail("invalid_identifier", `${path} is not a permitted external identifier`, { path });
  }
  return value;
}

/** A CARR-side reference. Narrower than an external identifier: it cannot hold "@". */
function assertInternalRef(value, path, { maxLength = 255 } = {}) {
  assertSafeText(value, path, { maxLength });
  if (!INTERNAL_REF.test(value)) {
    fail("invalid_reference",
      `${path} is not a permitted CARR reference; a reference carries no address characters`, { path });
  }
  return value;
}

function assertEnum(value, registered, path, code) {
  if (typeof value !== "string" || !registered.includes(value)) {
    fail(code, `"${String(value)}" is not registered at ${path}`,
      { path, value: typeof value === "string" ? value : null, registered: [...registered] });
  }
  return value;
}

function assertSafeInteger(value, path, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    fail("invalid_shape", `${path} must be a safe integer between ${min} and ${max}`, { path });
  }
  return value;
}

function assertBoolean(value, path) {
  if (typeof value !== "boolean") fail("invalid_shape", `${path} must be a boolean`, { path });
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Instants are parsed, never inferred, and the CALENDAR is checked against the
 * literal fields first. Date.parse normalizes an impossible date into a different
 * one — "2026-02-31T00:00:00Z" silently becomes 3 March — and a thread stamped
 * with an instant nobody wrote is bound to the wrong moment.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path, value });
  return parsed;
}

function assertSha256Ref(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference to a 64-character lower-case digest`, { path });
  }
  return value;
}

// ---------------------------------------------------------------------------
// The closed vocabularies. Every one is hashed into the policy preimage below,
// so a vocabulary that moves moves the digest and stale readers are refused.
// ---------------------------------------------------------------------------

/**
 * Field-name fragments that mean a caller is trying to make this module dispatch.
 * Checked on NAMES, before any value is read: by the time such a field exists the
 * caller has already assumed a capability that is not here, and answering its
 * question would confirm the assumption.
 */
export const V5_J103_DISPATCH_FRAGMENTS = deepFreeze([
  "autosend", "deliver", "dispatch", "outbound", "recipient_address", "reply_to",
  "schedule_send", "send", "smtp", "transmit",
]);

/**
 * Field-name fragments that mean raw correspondence reached a SOURCE-side object.
 * Chosen not to collide with the metadata this seam does carry: `content_digest`
 * and `byte_length` name a measurement of bytes, never bytes; `attachment_id` and
 * `media_type` name a descriptor, never a file.
 */
export const V5_J103_SOURCE_CONTENT_FRAGMENTS = deepFreeze([
  "attachment_bytes", "attachment_content", "body", "file_content", "html",
  "message_text", "plaintext", "preview", "raw", "snippet", "subject", "transcript",
]);

/** Field-name fragments that mean a credential reached this process. Same rule as F10's. */
export const V5_J103_CREDENTIAL_FRAGMENTS = deepFreeze([
  "api_key", "apikey", "bearer", "certificate", "cookie", "credential", "key_material",
  "oauth", "passphrase", "password", "private_key", "refresh", "secret", "session", "token",
]);

/**
 * The adapter kinds this interface can read through, by IDENTITY only.
 *
 * Source-agnostic is the requirement, so the registry is a table rather than a
 * hard-coded Outlook path, and a second authorized adapter is a table entry
 * rather than a new code path. F10's kind is bound by import: a rename upstream
 * breaks this module at load instead of leaving it trusting a stale literal.
 */
export const V5_J103_ADAPTERS = deepFreeze({
  [V5_F10_ADAPTER_KIND]: {
    authoritative_home: V5_F10_AUTHORITATIVE_HOME,
    mode: "read",
    retrieval_class: "partner_device_correspondence_read",
  },
});

export const V5_J103_ADAPTER_KINDS = deepFreeze(Object.keys(V5_J103_ADAPTERS).sort());

/** Where a binding stands. `unknown` sits with `unavailable`: silence is not readiness. */
export const V5_J103_AVAILABILITY_STATES = deepFreeze(["available", "unavailable", "unknown"]);
export const V5_J103_NON_READING_AVAILABILITY = deepFreeze(["unavailable", "unknown"]);

/** What the origin adapter may say about a thread's business relevance. */
export const V5_J103_RELEVANCE_STATES = deepFreeze([
  "ambiguous", "relevant_business_context", "unrelated",
]);

/** Q133's correspondence state, carried on every thread this module reads. */
export const V5_J103_CORRESPONDENCE_STATES = deepFreeze([
  "awaiting_counterparty", "awaiting_us", "informational", "resolved",
]);

/** Who a participant is TO CARR. Never an address; always a classification. */
export const V5_J103_PARTICIPANT_ROLES = deepFreeze([
  "attendee", "copied", "organizer", "originator", "principal_recipient",
]);
export const V5_J103_PARTY_KINDS = deepFreeze([
  "counterparty", "internal_colleague", "partner", "unknown_party",
]);

/** CARR records a thread may be linked to. */
export const V5_J103_RELATED_RECORD_KINDS = deepFreeze([
  "deal", "document", "lead", "loop", "party", "property",
]);

/** The closed answer set of readCorrespondenceThread. */
export const V5_J103_THREAD_DECISIONS = deepFreeze([
  "exclude_unrelated", "needs_independent_privacy_route", "read", "refuse",
  "unavailable", "withhold_ambiguous",
]);

/** What a draft may be. Neither one names a destination or an operation. */
export const V5_J103_DRAFT_KINDS = deepFreeze(["new_message", "reply_in_thread"]);
export const V5_J103_DRAFT_DECISIONS = deepFreeze([
  "drafted", "needs_independent_privacy_route", "refuse",
]);

/**
 * The declared seams a model may occupy. A model drafts prose and proposes facts;
 * it never establishes authority, and `proposed_by` outside this list is not a
 * policy question because the module has no role to judge it in.
 */
export const V5_J103_MODEL_SEAMS = deepFreeze({
  correspondence_draft: {
    may: "compose draft prose for a human to review, edit and send",
    may_not: "address, schedule or dispatch anything",
  },
  proposed_fact: {
    may: "propose that a field takes a value, citing the thread it read",
    may_not: "establish, confirm or resolve authority for that field",
  },
});
export const V5_J103_MODEL_SEAM_KEYS = deepFreeze(Object.keys(V5_J103_MODEL_SEAMS).sort());

export const V5_J103_PROPOSAL_DECISIONS = deepFreeze([
  "needs_independent_privacy_route", "proposed", "refuse",
]);

/**
 * F01's four conflict kinds, and the review route each takes.
 *
 * The kinds are declared here rather than imported because F01 does not export
 * them; the suite closes that gap the honest way, by driving resolveObservation
 * itself until it emits each one and asserting this table covers exactly the set
 * F01 produced. F01 is the oracle for its own vocabulary, not this comment.
 *
 * THE SPLIT IS Q076's. Two sources disagreeing about a VALUE is a business
 * question a partner answers in a sentence. A source writing where it has no
 * authority is a question about the registry, which is a different desk.
 */
export const V5_J103_CONFLICT_ROUTES = deepFreeze({
  equal_version_contradiction: "human_business_review",
  forbidden_overwrite_by_non_owner: "source_authority_review",
  indeterminate_version_ordering: "human_business_review",
  non_owner_establishment_attempt: "source_authority_review",
});
export const V5_J103_CONFLICT_KINDS = deepFreeze(Object.keys(V5_J103_CONFLICT_ROUTES).sort());
export const V5_J103_REVIEW_ROUTES = deepFreeze(
  [...new Set(Object.values(V5_J103_CONFLICT_ROUTES))].sort());

/** The only basis on which a source conflict may be resolved. */
export const V5_J103_RESOLUTION_BASIS = "field_authority_and_evidence";

/**
 * The bases that are REFUSED BY NAME. Q076's "never timestamp alone" is a
 * prohibition, and a prohibition nobody can name is one nobody can test. Each of
 * these is a way of saying "whoever wrote last wins", which is the exact failure
 * F01 spent its resolution ladder avoiding.
 */
export const V5_J103_REFUSED_RESOLUTION_BASES = deepFreeze([
  "latest_observed_at", "latest_timestamp", "most_recent_write", "recency", "source_order",
]);

export const V5_J103_CONFLICT_DECISIONS = deepFreeze(["queued", "refuse"]);

/** Where external send authority lives. Not here, and not implied to be coming. */
export const V5_J103_SEND_AUTHORITY_HOLDER = "human_partner_outside_carr";
export const V5_J103_SEND_AUTHORITY_SEAM =
  "step:v5-representative-workflow-external-send-authority-decision";

/** Later runtime and acceptance inputs. None is produced or satisfied here. */
export const V5_J103_ACCEPTANCE_HOOK = "journey-one-production-outcome";
export const V5_J103_CONTRACT_BINDING_STEP = "step:journey-one-contract-binding-receipt";
export const V5_J103_CONSUMER_GATES = deepFreeze([
  "global-execution-contract-accepted",
  "global-phi-boundary-accepted",
  "global-prompt-injection-boundary-accepted",
  "global-secrets-boundary-accepted",
  "global-source-authority-accepted",
  "journey-one-preactivation-contract-bound",
  "journey-one-production-accepted",
]);

/** Taint is never lowered at an adapter boundary, and the caller cannot supply it. */
export const V5_J103_TAINT_CLASS = "untrusted_external";

// The upstream vocabularies this module binds to BY IMPORT rather than by
// literal. One that moves upstream must break here, loudly, at load.
if (!V5_F01_TAINT_CLASSES.includes(V5_J103_TAINT_CLASS)) {
  throw new V5J103Error("upstream_vocabulary_drift",
    `F01 no longer registers the "${V5_J103_TAINT_CLASS}" taint class`,
    { registered: [...V5_F01_TAINT_CLASSES] });
}
for (const adapter_kind of V5_J103_ADAPTER_KINDS) {
  const home = V5_J103_ADAPTERS[adapter_kind].authoritative_home;
  if (!V5_F01_HOMES.includes(home)) {
    throw new V5J103Error("upstream_vocabulary_drift",
      `F01 no longer registers "${home}" as an authoritative home`,
      { adapter_kind, registered: [...V5_F01_HOMES] });
  }
  if (V5_J103_ADAPTERS[adapter_kind].mode !== "read") {
    throw new V5J103Error("write_adapter_registered",
      `adapter "${adapter_kind}" is registered with a non-read mode; this interface reads`,
      { adapter_kind });
  }
}

// ---------------------------------------------------------------------------
// The settled decisions, bound by digest so a drifted caller is refused.
// ---------------------------------------------------------------------------

export const V5_J103_SETTLED_DECISIONS = deepFreeze({
  "Q005.D1": {
    settled_requirement: "Core Journey 1 automates reads, drafts, and reversible internal writes only; no external effect may be emitted or certified by this outcome.",
    source_evidence_digest: "c57dd68c3295d862c77f3cb0c8f3046a26ed3de5909b0dc1a9e1a415169ed533",
  },
  "Q076.D1": {
    settled_requirement: "Resolve Salesforce and DoctorCRE conflicts by field authority and evidence, never timestamp alone; expose values and provenance and route genuine business conflicts for concise review.",
    source_evidence_digest: "5a4d708f681fed7baecd689e8031d6dc97721c9c41b22e77dcc6b4b92f214435",
  },
  "Q133.D1": {
    settled_requirement: "Outlook remains mailbox truth while DoctorCRE stores provenance-linked communication threads with participants, partner and source account, provider IDs, time, attachments, related records, and correspondence state. Core Journey 1 may read, draft, and infer proposed facts through the governed adapter; external send authority belongs to the later representative workflow and typed successors.",
    source_evidence_digest: "be34f0aa05de3e58bc64fc735158b87a63f173887075653c6abb575194a6da2f",
  },
  "Q134.D1": {
    settled_requirement: "Core Journey 1 provides a source-agnostic governed business-correspondence schema and read interface that may use whichever authorized adapter is already available; it excludes unrelated mail, holds ambiguity private, and never claims both-partner coverage.",
    source_evidence_digest: "6d380bcdf0701273a792f3623ae79740561d8b67016174302976168381382cc5",
  },
});

export const V5_J103_SETTLED_DECISION_IDS =
  deepFreeze(Object.keys(V5_J103_SETTLED_DECISIONS).sort());

/**
 * The canonical digest of the reviewed four-decision subset. DERIVED rather than
 * hand-typed: a literal would be a second copy of the same fact, free to drift
 * from the table above without anything noticing.
 */
export function v5J103DecisionSubsetPreimage() {
  return {
    schema_version: "doctorcre-v5-j103-decision-subset.v1",
    decisions: V5_J103_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_J103_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_J103_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
  };
}

export function v5J103DecisionSubsetDigest() {
  return digest(v5J103DecisionSubsetPreimage());
}

/**
 * Refuse a caller whose decision subset has drifted from the reviewed one. Drift
 * is checked in BOTH directions — a missing decision and an extra one are both
 * drift — and every source-evidence digest must match exactly.
 */
export function assertJ103DecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions", "decision_subset_digest"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  assertObject(binding.decisions, "binding.decisions");
  if ("decision_subset_digest" in binding && binding.decision_subset_digest !== undefined) {
    assertSha256Ref(binding.decision_subset_digest, "binding.decision_subset_digest");
    if (binding.decision_subset_digest !== v5J103DecisionSubsetDigest()) {
      fail("decision_binding_drift", "the decision subset digest does not match the reviewed subset",
        { expected: v5J103DecisionSubsetDigest(), actual: binding.decision_subset_digest });
    }
  }
  const supplied = Object.keys(binding.decisions).sort();
  const missing = V5_J103_SETTLED_DECISION_IDS.filter(id => !supplied.includes(id));
  const extra = supplied.filter(id => !V5_J103_SETTLED_DECISION_IDS.includes(id));
  if (missing.length > 0 || extra.length > 0) {
    fail("decision_binding_drift", "the supplied decision set is not the reviewed four", { missing, extra });
  }
  for (const id of V5_J103_SETTLED_DECISION_IDS) {
    const entry = assertObject(binding.decisions[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["settled_requirement", "source_evidence_digest"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["source_evidence_digest"], `binding.decisions.${id}`);
    const supplied_digest = entry.source_evidence_digest;
    if (typeof supplied_digest !== "string" || !HEX64.test(supplied_digest)) {
      fail("invalid_digest",
        `binding.decisions.${id}.source_evidence_digest must be 64 lower-case hex characters`,
        { path: `binding.decisions.${id}.source_evidence_digest` });
    }
    if (supplied_digest !== V5_J103_SETTLED_DECISIONS[id].source_evidence_digest) {
      fail("decision_binding_drift", `source-evidence digest drift on ${id}`, {
        decision_id: id, expected: V5_J103_SETTLED_DECISIONS[id].source_evidence_digest,
        actual: supplied_digest,
      });
    }
    if ("settled_requirement" in entry && entry.settled_requirement !== undefined &&
        entry.settled_requirement !== V5_J103_SETTLED_DECISIONS[id].settled_requirement) {
      fail("decision_binding_drift", `settled requirement text drift on ${id}`, { decision_id: id });
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// The three structural scans. Each runs on FIELD NAMES or raw VALUES before the
// object is interpreted, because each names a boundary that is already crossed by
// the time the field exists.
// ---------------------------------------------------------------------------

function scanFieldNames(object, fragments, path, code, describe) {
  for (const key of Object.keys(object)) {
    const normalized = key.toLowerCase();
    const fragment = fragments.find(f => normalized.includes(f));
    if (fragment !== undefined) {
      fail(code, `${path}.${key} ${describe(fragment)}`, { path: `${path}.${key}`, key, fragment });
    }
  }
}

function assertNoDispatchFields(object, path) {
  scanFieldNames(object, V5_J103_DISPATCH_FRAGMENTS, path, "dispatch_instruction_refused",
    fragment => `looks like a dispatch instruction ("${fragment}"); there is no send capability`
      + " anywhere in CARR — drafts are produced and a human sends them");
}

function assertNoSourceContentFields(object, path) {
  scanFieldNames(object, V5_J103_SOURCE_CONTENT_FRAGMENTS, path, "source_content_must_not_cross_seam",
    fragment => `looks like it carries correspondence content ("${fragment}"); the mailbox remains`
      + " truth and this seam carries typed metadata and digests, never message text");
}

function assertNoCredentialFields(object, path) {
  scanFieldNames(object, V5_J103_CREDENTIAL_FRAGMENTS, path, "credential_in_correspondence_request",
    fragment => `looks like it carries a credential ("${fragment}"); this interface decides over`
      + " references and holds none");
}

/**
 * Refuse any string VALUE that could route a message.
 *
 * This is the load-bearing half of "structurally unable to dispatch". A flag can
 * be flipped by an edit; a schema that holds no destination cannot be talked into
 * producing one. Scanned recursively over the caller's own object, before the
 * module copies anything out of it.
 */
function assertNoRoutableAddress(value, path) {
  if (typeof value === "string") {
    if (ROUTABLE_ADDRESS.test(value)) {
      fail("routable_address_refused",
        `${path} carries something shaped like a routable address; this module holds no recipient`
        + " destination, so a draft it produces cannot be addressed by a program",
        { path });
    }
    return value;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => assertNoRoutableAddress(entry, `${path}[${index}]`));
    return value;
  }
  if (isPlainObject(value)) {
    for (const key of Object.keys(value)) assertNoRoutableAddress(value[key], `${path}.${key}`);
    return value;
  }
  return value;
}

/**
 * The one exemption from the address scan, and it is bounded rather than general.
 *
 * A mailbox `account` IS an address at most providers — it is the partner's own
 * mailbox identity, the place a human reads and sends FROM. It is not a
 * destination, and it cannot become one: the thread's account must equal the
 * compiled binding's account, and a binding names exactly one. Everything a draft
 * could be aimed AT — participants, recipients, prose — is scanned.
 *
 * The claim this module makes is therefore exact: it holds no RECIPIENT
 * destination anywhere. Stating it as "no address at all" would have been a
 * slogan the schema could not keep.
 */
const ADDRESS_SCAN_EXEMPT_KEYS = Object.freeze(["account"]);

/** Every source-side object runs all four scans, in this order, before interpretation. */
function assertSourceSideShape(object, path) {
  assertNoCredentialFields(object, path);
  assertNoSourceContentFields(object, path);
  assertNoDispatchFields(object, path);
  for (const key of Object.keys(object)) {
    if (ADDRESS_SCAN_EXEMPT_KEYS.includes(key)) continue;
    assertNoRoutableAddress(object[key], `${path}.${key}`);
  }
}

/**
 * Declared classes are checked against S01's own registry HERE, so an unknown
 * class fails as a J103 contract violation naming the field rather than as a
 * boundary error raised from two frames down. S01 still decides what the known
 * classes MEAN; this only decides that the caller named real ones.
 */
function privacyClasses(value, path, { max = 16 } = {}) {
  assertArray(value, path, { min: 1, max });
  value.forEach((cls, index) => assertEnum(cls, V5_DATA_CLASSES, `${path}[${index}]`, "unknown_data_class"));
  return [...new Set(value)].sort();
}

// ---------------------------------------------------------------------------
// The binding: one authorized adapter, one partner, one account.
//
// ONE PARTNER PER BINDING, STRUCTURALLY. There is no registry of bindings here
// and no way to hold two at once, for the same reason F10 compiles one
// installation at a time: the moment one object can speak for both partners,
// "never claims both-partner coverage" is a convention rather than a structure.
// ---------------------------------------------------------------------------

const BINDING_KEYS = Object.freeze([
  "account", "adapter_kind", "availability", "binding_version", "partner_slug", "source_system",
]);

/**
 * Compile one adapter binding into a frozen, digest-bearing identity.
 *
 * The digest binds partner, account, source system, adapter kind and availability.
 * It is an identity for THIS binding and nothing else: not an enrollment, not an
 * attestation, and not evidence for any gate.
 */
export function compileCorrespondenceBinding(config) {
  assertObject(config, "config");
  assertNoCredentialFields(config, "config");
  assertNoDispatchFields(config, "config");
  assertClosedKeys(config, BINDING_KEYS, "config");
  assertRequiredKeys(config, BINDING_KEYS, "config");

  // PARTNERHOOD IS identity.js's, here as in F10. A binding that named a partner
  // the system does not know would be a second answer to a settled question, and
  // the coverage claim below rests on the slug meaning one particular person.
  const partner_slug = assertInternalRef(config.partner_slug, "config.partner_slug", { maxLength: 64 });
  if (!isKnownPartner(partner_slug)) {
    fail("unknown_partner",
      `"${partner_slug}" is not a CARR partner; a correspondence binding belongs to exactly one`,
      { partner_slug });
  }
  const adapter_kind = assertEnum(config.adapter_kind, V5_J103_ADAPTER_KINDS,
    "config.adapter_kind", "unregistered_adapter_kind");
  const account = assertExternalIdent(config.account, "config.account", { maxLength: 255 });
  const source_system = assertExternalIdent(config.source_system, "config.source_system", { maxLength: 128 });
  const availability = assertEnum(config.availability, V5_J103_AVAILABILITY_STATES,
    "config.availability", "unknown_availability_state");
  const binding_version = assertSafeInteger(config.binding_version, "config.binding_version", { min: 1 });

  const adapter = V5_J103_ADAPTERS[adapter_kind];
  const compiled = {
    compiled: true,
    schema_version: V5_J103_BINDING_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    partner_slug,
    adapter_kind,
    adapter_mode: adapter.mode,
    authoritative_home: adapter.authoritative_home,
    retrieval_class: adapter.retrieval_class,
    account,
    source_system,
    availability,
    binding_version,
  };
  const binding_digest = digest(bindingPreimage(compiled));
  return deepFreeze({ ...compiled, binding_digest });
}

function bindingPreimage(binding) {
  return {
    schema_version: V5_J103_BINDING_SCHEMA_VERSION,
    tenant: binding.tenant,
    partner_slug: binding.partner_slug,
    adapter_kind: binding.adapter_kind,
    adapter_mode: binding.adapter_mode,
    authoritative_home: binding.authoritative_home,
    retrieval_class: binding.retrieval_class,
    account: binding.account,
    source_system: binding.source_system,
    availability: binding.availability,
    binding_version: binding.binding_version,
  };
}

/** The exact bytes a compiled binding hashes to, so a reviewer can check it by hand. */
export function correspondenceBindingCanonicalBytes(binding) {
  return canonicalJson(bindingPreimage(requireCompiledBinding(binding, "binding")));
}

const COMPILED_BINDING_KEYS = Object.freeze([
  "compiled", "schema_version", "tenant", "partner_slug", "adapter_kind", "adapter_mode",
  "authoritative_home", "retrieval_class", "account", "source_system", "availability",
  "binding_version", "binding_digest",
]);

/**
 * Accept only a binding this module compiled and nobody has edited since.
 *
 * The digest is RECOMPUTED rather than trusted, so a hand-forged object carrying
 * `compiled: true` and a copied digest is refused: the bytes have to hash to the
 * claim. This is what stops a caller routing around compile-time validation —
 * including the availability check — by fabricating the compiled shape directly.
 */
function requireCompiledBinding(binding, path) {
  assertObject(binding, path);
  assertClosedKeys(binding, COMPILED_BINDING_KEYS, path);
  assertRequiredKeys(binding, COMPILED_BINDING_KEYS, path);
  if (binding.compiled !== true || binding.schema_version !== V5_J103_BINDING_SCHEMA_VERSION) {
    fail("binding_not_compiled", `${path} must be the output of compileCorrespondenceBinding`, { path });
  }
  assertSha256Ref(binding.binding_digest, `${path}.binding_digest`);
  const recomputed = digest(bindingPreimage(binding));
  if (recomputed !== binding.binding_digest) {
    fail("binding_digest_mismatch",
      `${path} no longer hashes to its own digest; it was edited after compilation`,
      { path, expected: binding.binding_digest, actual: recomputed });
  }
  return binding;
}

// ---------------------------------------------------------------------------
// The read interface. Source-agnostic, provenance-preserving, ambiguity-private.
// ---------------------------------------------------------------------------

const READ_REQUEST_KEYS = Object.freeze(["binding", "now", "thread"]);
const THREAD_KEYS = Object.freeze([
  "account", "attachments", "correspondence_state", "declared_data_classes", "last_activity_at",
  "message_refs", "native_identity", "observed_at", "participants", "related_records",
  "relevance_state", "started_at",
]);
const NATIVE_IDENTITY_KEYS = Object.freeze(["native_id", "native_id_epoch", "source_system"]);
const PARTICIPANT_KEYS = Object.freeze(["address_digest", "participant_ref", "party_kind", "role"]);
const MESSAGE_REF_KEYS = Object.freeze(["occurred_at", "provider_message_id", "provider_thread_id"]);
const ATTACHMENT_KEYS = Object.freeze(["attachment_id", "byte_length", "content_digest", "media_type"]);
const RELATED_RECORD_KEYS = Object.freeze(["record_kind", "record_ref"]);

function assertThreadNativeIdentity(value, path) {
  assertObject(value, path);
  assertClosedKeys(value, NATIVE_IDENTITY_KEYS, path);
  assertRequiredKeys(value, NATIVE_IDENTITY_KEYS, path);
  return {
    source_system: assertExternalIdent(value.source_system, `${path}.source_system`, { maxLength: 128 }),
    native_id: assertExternalIdent(value.native_id, `${path}.native_id`, { maxLength: 255 }),
    native_id_epoch: assertExternalIdent(value.native_id_epoch, `${path}.native_id_epoch`, { maxLength: 128 }),
  };
}

function assertParticipants(value, path) {
  assertArray(value, path, { min: 1, max: 64 });
  const seen = new Set();
  return value.map((raw, index) => {
    const entryPath = `${path}[${index}]`;
    assertObject(raw, entryPath);
    assertClosedKeys(raw, PARTICIPANT_KEYS, entryPath);
    assertRequiredKeys(raw, PARTICIPANT_KEYS, entryPath);
    // A participant_ref is a CARR reference, never an address: INTERNAL_REF has no
    // "@", so the narrow validator is the structural half of the claim and
    // assertNoRoutableAddress on the whole thread is the belt.
    const participant_ref = assertInternalRef(raw.participant_ref, `${entryPath}.participant_ref`,
      { maxLength: 128 });
    if (seen.has(participant_ref)) {
      fail("duplicate_participant",
        `${entryPath}.participant_ref repeats "${participant_ref}"; one party has one role on a thread`,
        { path: entryPath, participant_ref });
    }
    seen.add(participant_ref);
    return {
      participant_ref,
      role: assertEnum(raw.role, V5_J103_PARTICIPANT_ROLES, `${entryPath}.role`, "unknown_participant_role"),
      party_kind: assertEnum(raw.party_kind, V5_J103_PARTY_KINDS, `${entryPath}.party_kind`,
        "unknown_party_kind"),
      address_digest: assertSha256Ref(raw.address_digest, `${entryPath}.address_digest`),
    };
  });
}

function assertMessageRefs(value, path, now) {
  assertArray(value, path, { min: 1, max: 256 });
  const seen = new Set();
  return value.map((raw, index) => {
    const entryPath = `${path}[${index}]`;
    assertObject(raw, entryPath);
    assertClosedKeys(raw, MESSAGE_REF_KEYS, entryPath);
    assertRequiredKeys(raw, MESSAGE_REF_KEYS, entryPath);
    const provider_message_id = assertExternalIdent(raw.provider_message_id,
      `${entryPath}.provider_message_id`, { maxLength: 255 });
    if (seen.has(provider_message_id)) {
      fail("duplicate_provider_message_id",
        `${entryPath}.provider_message_id repeats "${provider_message_id}"`,
        { path: entryPath, provider_message_id });
    }
    seen.add(provider_message_id);
    const occurredAt = assertInstant(raw.occurred_at, `${entryPath}.occurred_at`);
    if (occurredAt > now) {
      fail("message_occurs_after_now",
        `${entryPath}.occurred_at is later than the supplied instant; a message from the future is a`
        + " clock defect, not correspondence", { path: entryPath });
    }
    return {
      provider_thread_id: assertExternalIdent(raw.provider_thread_id, `${entryPath}.provider_thread_id`,
        { maxLength: 255 }),
      provider_message_id,
      occurred_at: raw.occurred_at,
    };
  });
}

function assertAttachments(value, path) {
  assertArray(value, path, { min: 0, max: 64 });
  return value.map((raw, index) => {
    const entryPath = `${path}[${index}]`;
    assertObject(raw, entryPath);
    // The descriptor's key set is closed and holds no filename: a filename is
    // author-chosen text that routinely carries a party name or a deal term, and
    // Q133 does not ask for it.
    assertClosedKeys(raw, ATTACHMENT_KEYS, entryPath);
    assertRequiredKeys(raw, ATTACHMENT_KEYS, entryPath);
    return {
      attachment_id: assertExternalIdent(raw.attachment_id, `${entryPath}.attachment_id`, { maxLength: 255 }),
      media_type: assertExternalIdent(raw.media_type, `${entryPath}.media_type`, { maxLength: 128 }),
      byte_length: assertSafeInteger(raw.byte_length, `${entryPath}.byte_length`, { min: 0 }),
      content_digest: assertSha256Ref(raw.content_digest, `${entryPath}.content_digest`),
    };
  });
}

function assertRelatedRecords(value, path) {
  assertArray(value, path, { min: 0, max: 64 });
  return value.map((raw, index) => {
    const entryPath = `${path}[${index}]`;
    assertObject(raw, entryPath);
    assertClosedKeys(raw, RELATED_RECORD_KEYS, entryPath);
    assertRequiredKeys(raw, RELATED_RECORD_KEYS, entryPath);
    return {
      record_kind: assertEnum(raw.record_kind, V5_J103_RELATED_RECORD_KINDS, `${entryPath}.record_kind`,
        "unknown_related_record_kind"),
      record_ref: assertInternalRef(raw.record_ref, `${entryPath}.record_ref`, { maxLength: 255 }),
    };
  });
}

/**
 * Read one correspondence thread through an authorized adapter binding.
 *
 * THE ORDER, and every step of it is policy:
 *   1. structural scans        — throw; a credential, raw content, a dispatch
 *                                instruction or a routable address must not be
 *                                in the request at all
 *   2. shape and identity      — throw; an unreadable thread is not a judgement
 *   3. adapter availability    — "unavailable"; an adapter nobody has observed is
 *                                not an adapter, and answering anything else
 *                                would be inventing a read that never happened
 *   4. binding isolation       — "refuse"; this account is not that partner's
 *   5. the S01 privacy boundary — UNCONDITIONAL, and BEFORE relevance, so a
 *                                thread dropped as unrelated is one that was
 *                                classified and dropped rather than one that was
 *                                never classified at all
 *   6. relevance               — read / exclude / withhold
 *
 * AMBIGUITY RESOLVES TO PRIVATE. Q134's boundary holds ambiguity private, so
 * `ambiguous` is its own answer and never falls through to a read. There is no
 * default and no confidence threshold that could turn a maybe into a yes.
 */
export function readCorrespondenceThread(request) {
  assertObject(request, "request");
  assertClosedKeys(request, READ_REQUEST_KEYS, "request");
  assertRequiredKeys(request, READ_REQUEST_KEYS, "request");
  const binding = requireCompiledBinding(request.binding, "request.binding");
  const now = assertInstant(request.now, "request.now");

  const raw = assertObject(request.thread, "request.thread");
  assertSourceSideShape(raw, "request.thread");
  assertClosedKeys(raw, THREAD_KEYS, "request.thread");
  assertRequiredKeys(raw, THREAD_KEYS, "request.thread");

  const account = assertExternalIdent(raw.account, "request.thread.account", { maxLength: 255 });
  const native_identity = assertThreadNativeIdentity(raw.native_identity, "request.thread.native_identity");
  const participants = assertParticipants(raw.participants, "request.thread.participants");
  const message_refs = assertMessageRefs(raw.message_refs, "request.thread.message_refs", now);
  const attachments = assertAttachments(raw.attachments, "request.thread.attachments");
  const related_records = assertRelatedRecords(raw.related_records, "request.thread.related_records");
  const correspondence_state = assertEnum(raw.correspondence_state, V5_J103_CORRESPONDENCE_STATES,
    "request.thread.correspondence_state", "unknown_correspondence_state");
  const relevance_state = assertEnum(raw.relevance_state, V5_J103_RELEVANCE_STATES,
    "request.thread.relevance_state", "unknown_relevance_state");
  const declared_data_classes = privacyClasses(raw.declared_data_classes,
    "request.thread.declared_data_classes");
  const startedAt = assertInstant(raw.started_at, "request.thread.started_at");
  const lastActivityAt = assertInstant(raw.last_activity_at, "request.thread.last_activity_at");
  const observedAt = assertInstant(raw.observed_at, "request.thread.observed_at");
  if (lastActivityAt < startedAt) {
    fail("thread_time_inverted",
      "request.thread.last_activity_at precedes started_at; a thread cannot end before it began",
      { path: "request.thread.last_activity_at" });
  }

  // Provenance is assembled ONCE and rides on every answer below, including the
  // refusals. A thread that was excluded is still a thread we can say where we
  // looked at — and a refusal with no provenance is a refusal nobody can audit.
  const provenance = {
    adapter_kind: binding.adapter_kind,
    retrieval_class: binding.retrieval_class,
    authoritative_home: binding.authoritative_home,
    source_system: native_identity.source_system,
    account,
    partner_slug: binding.partner_slug,
    native_identity,
    taint_class: V5_J103_TAINT_CLASS,
    binding_digest: binding.binding_digest,
  };

  const base = {
    schema_version: V5_J103_THREAD_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    binding_digest: binding.binding_digest,
    partner_slug: binding.partner_slug,
    provenance,
    declared_data_classes,
    // Q133: the mailbox is truth and CARR stores the minimum that is relevant.
    mailbox_remains_truth: true,
    minimum_necessary: true,
    source_content_observed: false,
    // Q134: single-partner, always, on every answer including the refusals.
    partner_coverage: coverageStatement(binding),
    thread: null,
    dispatchable: false,
    provider_operation: null,
    effects: V5_NO_EFFECTS,
  };

  // Step 3. An adapter nobody has observed is not an adapter.
  if (V5_J103_NON_READING_AVAILABILITY.includes(binding.availability)) {
    return deepFreeze({
      decision: "unavailable", reason_id: "authorized_adapter_unavailable", ...base,
      availability: binding.availability,
      adapter_kinds_registered: [...V5_J103_ADAPTER_KINDS],
    });
  }

  // Step 4. Isolation, checked structurally: one binding reads one account on one
  // source system for one partner.
  if (account !== binding.account) {
    return deepFreeze({ decision: "refuse", reason_id: "account_outside_binding", ...base });
  }
  if (native_identity.source_system !== binding.source_system) {
    return deepFreeze({ decision: "refuse", reason_id: "native_identity_source_mismatch", ...base });
  }
  if (observedAt > now) {
    return deepFreeze({ decision: "refuse", reason_id: "observed_after_now", ...base });
  }

  // Step 5. S01 decides, and it decides on EVERY thread.
  const privacy = evaluatePrivacyBoundary({ data_classes: declared_data_classes });
  if (privacy.decision === "refuse") {
    return deepFreeze({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      prohibited_classes: [...(privacy.prohibited_classes ?? [])],
      amendment_required: privacy.amendment_required,
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return deepFreeze({
      decision: "needs_independent_privacy_route", reason_id: privacy.reason_id, ...base,
      routed_classes: [...(privacy.routed_classes ?? [])],
      required_evidence: privacy.required_evidence,
    });
  }

  // Step 6. Relevance. Neither of the first two answers carries the thread
  // forward, and neither can be drafted against or proposed from.
  if (relevance_state === "unrelated") {
    return deepFreeze({
      decision: "exclude_unrelated", reason_id: "unrelated_correspondence_excluded", ...base,
    });
  }
  if (relevance_state === "ambiguous") {
    return deepFreeze({
      decision: "withhold_ambiguous", reason_id: "ambiguity_remains_private", ...base,
    });
  }

  const thread = {
    thread_ref: digest({
      schema_version: V5_J103_THREAD_SCHEMA_VERSION,
      source_system: native_identity.source_system,
      native_id: native_identity.native_id,
      native_id_epoch: native_identity.native_id_epoch,
      account,
    }),
    correspondence_state,
    participants,
    message_refs,
    attachments,
    related_records,
    started_at: raw.started_at,
    last_activity_at: raw.last_activity_at,
    observed_at: raw.observed_at,
  };
  return deepFreeze({
    decision: "read", reason_id: "relevant_business_context_within_boundary", ...base, thread,
  });
}

// ---------------------------------------------------------------------------
// Coverage. The one claim this module refuses to make in any input.
// ---------------------------------------------------------------------------

function coverageStatement(binding) {
  return {
    schema_version: V5_J103_COVERAGE_SCHEMA_VERSION,
    covered_partner_slug: binding.partner_slug,
    covered_account: binding.account,
    // Q134 forbids the both-partner claim, and no argument reaches an available
    // answer: one binding names one partner, and there is no second slot.
    combined_partner_coverage: "unavailable",
    combined_coverage_reason_id: "combined_partner_coverage_unavailable",
    coverage_gate_id: "partner-mail-calendar-connectors-accepted",
    coverage_gate_satisfied: false,
  };
}

const COVERAGE_REQUEST_KEYS = Object.freeze(["binding", "requested_partner_slugs"]);

/**
 * Answer an explicit question about whose correspondence this binding covers.
 *
 * A request naming ONE partner who is this binding's partner is answered.
 * Anything else — two partners, or the other partner — is `unavailable`, not
 * `refuse`: the coverage does not exist to be granted or denied, and calling it a
 * refusal would suggest a permission somewhere could change the answer.
 */
export function projectCorrespondenceCoverage(request) {
  assertObject(request, "request");
  assertClosedKeys(request, COVERAGE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, COVERAGE_REQUEST_KEYS, "request");
  const binding = requireCompiledBinding(request.binding, "request.binding");
  assertArray(request.requested_partner_slugs, "request.requested_partner_slugs", { min: 1, max: 8 });
  const requested = request.requested_partner_slugs.map((slug, index) =>
    assertInternalRef(slug, `request.requested_partner_slugs[${index}]`, { maxLength: 64 }));
  const unique = [...new Set(requested)].sort();

  const base = {
    schema_version: V5_J103_COVERAGE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    binding_digest: binding.binding_digest,
    requested_partner_slugs: unique,
    ...coverageStatement(binding),
    dispatchable: false,
    provider_operation: null,
    effects: V5_NO_EFFECTS,
  };

  if (unique.length > 1) {
    return deepFreeze({
      decision: "unavailable", reason_id: "combined_partner_coverage_unavailable", ...base,
    });
  }
  if (unique[0] !== binding.partner_slug) {
    return deepFreeze({
      decision: "unavailable", reason_id: "partner_outside_binding_not_covered", ...base,
    });
  }
  if (V5_J103_NON_READING_AVAILABILITY.includes(binding.availability)) {
    return deepFreeze({
      decision: "unavailable", reason_id: "authorized_adapter_unavailable", ...base,
      availability: binding.availability,
    });
  }
  return deepFreeze({
    decision: "covered", reason_id: "single_partner_coverage_within_binding", ...base,
  });
}

// ---------------------------------------------------------------------------
// Drafts. Produced, never dispatched.
// ---------------------------------------------------------------------------

const DRAFT_REQUEST_KEYS = Object.freeze(["binding", "draft", "now", "thread_read"]);
const DRAFT_KEYS = Object.freeze([
  "authored_by", "declared_data_classes", "draft_body", "draft_kind", "intended_participant_refs",
]);

/**
 * Require a `read` result this module produced, for a draft or a proposal.
 *
 * A WITHHELD OR EXCLUDED THREAD THROWS. Drafting against an ambiguous thread is
 * not a policy question the caller gets a second answer to — it is the caller
 * ignoring the answer it already has, the same way F10 throws on a candidate
 * built from a withheld classification.
 */
function requireReadThread(value, binding, path) {
  const read = assertObject(value, path);
  if (read.schema_version !== V5_J103_THREAD_SCHEMA_VERSION) {
    fail("uncompiled_thread_read", `${path} must be the result of readCorrespondenceThread`,
      { path, schema_version: typeof read.schema_version === "string" ? read.schema_version : null });
  }
  if (read.decision !== "read" || read.thread === null || read.thread === undefined) {
    fail("work_from_non_readable_thread",
      `a "${String(read.decision)}" thread carries nothing to work from; excluded, withheld and`
      + " unavailable correspondence does not become a draft or a proposed fact",
      { path, decision: typeof read.decision === "string" ? read.decision : null });
  }
  if (read.binding_digest !== binding.binding_digest) {
    fail("thread_outside_binding",
      `${path} was read through a different binding; one partner's thread is not another's to work from`,
      { path });
  }
  return read;
}

/**
 * Produce a draft against a thread this module read.
 *
 * WHAT COMES BACK IS NOT SENDABLE BY ANY PROGRAM, and the reasons are structural
 * rather than declarative: the recipients are CARR participant references that
 * route nowhere, the body has been scanned for routable addresses, no provider
 * operation is named, and the dispatch-field scan has already thrown on any
 * attempt to instruct otherwise. `requires_human_send` says who finishes the job.
 */
export function draftCorrespondence(request) {
  assertObject(request, "request");
  assertClosedKeys(request, DRAFT_REQUEST_KEYS, "request");
  assertRequiredKeys(request, DRAFT_REQUEST_KEYS, "request");
  const binding = requireCompiledBinding(request.binding, "request.binding");
  const now = assertInstant(request.now, "request.now");
  const read = requireReadThread(request.thread_read, binding, "request.thread_read");

  const raw = assertObject(request.draft, "request.draft");
  // The draft side runs the credential, dispatch and address scans — but NOT the
  // source-content scan, because `draft_body` is text CARR authored rather than
  // correspondence it read. The asymmetry is named in the header; this is where
  // it lives.
  assertNoCredentialFields(raw, "request.draft");
  assertNoDispatchFields(raw, "request.draft");
  assertNoRoutableAddress(raw, "request.draft");
  assertClosedKeys(raw, DRAFT_KEYS, "request.draft");
  assertRequiredKeys(raw, DRAFT_KEYS, "request.draft");

  const draft_kind = assertEnum(raw.draft_kind, V5_J103_DRAFT_KINDS, "request.draft.draft_kind",
    "unknown_draft_kind");
  const authored_by = assertEnum(raw.authored_by, ["model", "partner"], "request.draft.authored_by",
    "unknown_draft_author");
  const draft_body = assertSafeText(raw.draft_body, "request.draft.draft_body", { maxLength: 20000 });
  if (DIALABLE_NUMBER.test(draft_body)) {
    fail("routable_address_refused",
      "request.draft.draft_body carries something shaped like a dialable number; a draft that"
      + " arranges contact out of band is routing, and routing is the human's to do",
      { path: "request.draft.draft_body" });
  }
  const declared_data_classes = privacyClasses(raw.declared_data_classes,
    "request.draft.declared_data_classes");
  assertArray(raw.intended_participant_refs, "request.draft.intended_participant_refs",
    { min: 1, max: 64 });
  const intended_participant_refs = [...new Set(raw.intended_participant_refs.map((ref, index) =>
    assertInternalRef(ref, `request.draft.intended_participant_refs[${index}]`, { maxLength: 128 })))].sort();

  const base = {
    schema_version: V5_J103_DRAFT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    binding_digest: binding.binding_digest,
    partner_slug: binding.partner_slug,
    thread_ref: read.thread.thread_ref,
    provenance: read.provenance,
    draft_kind,
    authored_by,
    model_seam: authored_by === "model" ? "correspondence_draft" : null,
    declared_data_classes,
    // The four facts that make the non-dispatchability checkable rather than
    // promised. They ride on the refusals too, so no answer from this function
    // can be read as a dispatch.
    dispatchable: false,
    provider_operation: null,
    requires_human_send: true,
    send_authority_holder: V5_J103_SEND_AUTHORITY_HOLDER,
    send_authority_seam: V5_J103_SEND_AUTHORITY_SEAM,
    partner_coverage: coverageStatement(binding),
    draft: null,
    effects: V5_NO_EFFECTS,
  };

  const privacy = evaluatePrivacyBoundary({ data_classes: declared_data_classes });
  if (privacy.decision === "refuse") {
    return deepFreeze({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      prohibited_classes: [...(privacy.prohibited_classes ?? [])],
      amendment_required: privacy.amendment_required,
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return deepFreeze({
      decision: "needs_independent_privacy_route", reason_id: privacy.reason_id, ...base,
      routed_classes: [...(privacy.routed_classes ?? [])],
      required_evidence: privacy.required_evidence,
    });
  }

  // A recipient who is not on the thread is either a mistake or a widening of the
  // audience nobody asked for. Both are refused here rather than left to the
  // human to notice in a client.
  const known = new Set(read.thread.participants.map(p => p.participant_ref));
  const unknown = intended_participant_refs.filter(ref => !known.has(ref));
  if (unknown.length > 0) {
    return deepFreeze({
      decision: "refuse", reason_id: "recipient_outside_thread", ...base,
      unknown_participant_refs: unknown,
    });
  }
  if (draft_kind === "reply_in_thread" && read.thread.correspondence_state === "resolved") {
    return deepFreeze({
      decision: "refuse", reason_id: "reply_into_resolved_thread_refused", ...base,
      correspondence_state: read.thread.correspondence_state,
    });
  }
  // A draft is stamped at `now`, so `now` has to be after the thread was observed.
  // Otherwise the record says CARR drafted a reply to correspondence it had not
  // yet read, which is a clock defect rather than a draft.
  if (now < assertInstant(read.thread.observed_at, "request.thread_read.thread.observed_at")) {
    return deepFreeze({
      decision: "refuse", reason_id: "draft_precedes_thread_observation", ...base,
      thread_observed_at: read.thread.observed_at,
    });
  }

  const draft = {
    draft_ref: digest({
      schema_version: V5_J103_DRAFT_SCHEMA_VERSION,
      thread_ref: read.thread.thread_ref,
      draft_kind,
      body_digest: digest(draft_body),
      intended_participant_refs,
      drafted_at: request.now,
    }),
    body_digest: digest(draft_body),
    draft_body,
    intended_participant_refs,
    drafted_at: request.now,
    // What a human does with it. Named as a handoff rather than a queue, because a
    // queue implies something drains it.
    handoff: "human_partner_reviews_and_sends_from_the_mailbox_that_holds_the_thread",
  };
  return deepFreeze({
    decision: "drafted", reason_id: "draft_produced_for_human_send", ...base, draft,
  });
}

// ---------------------------------------------------------------------------
// Proposed facts. Inferred, never established.
// ---------------------------------------------------------------------------

const PROPOSAL_REQUEST_KEYS = Object.freeze(["binding", "now", "proposal", "thread_read"]);
const PROPOSAL_KEYS = Object.freeze([
  "declared_data_classes", "entity", "evidence_provider_message_id", "field", "proposed_by",
  "rationale", "value_digest",
]);

/**
 * Refuse a proposal carrying a FIELD that claims the authority it is asking for.
 *
 * F01 already enumerates the fragments that mean "treat this as settled", and it
 * applies them to field NAMES; they are imported and applied the same way here,
 * so the two modules cannot drift into disagreeing about what an authority claim
 * looks like. Scanning free prose for the same words would be a different and
 * much worse check: a rationale that says a landlord will GRANT an extension is
 * ordinary business English, not an attempt to establish authority.
 */
function assertNoAuthorityClaimFields(object, path) {
  scanFieldNames(object, V5_F01_AUTHORITY_INJECTION_FRAGMENTS, path, "authority_claim_in_proposal",
    fragment => `names authority the caller cannot supply ("${fragment}"); a proposed fact is a`
      + " proposal, and the authority for a field belongs to F01's registry");
}

/**
 * Evaluate one model-proposed fact inferred from a thread.
 *
 * IT BUILDS THE F01 OBSERVATION SHAPE AND STOPS. resolveObservation owns field
 * authority, holds the compiled registry and answers with its own refusal matrix;
 * a module that resolved its own proposals would be a second authority for the
 * settled thing. Every result therefore says `authority_established: false` and
 * names the one function that can change that.
 */
export function evaluateProposedFact(request) {
  assertObject(request, "request");
  assertClosedKeys(request, PROPOSAL_REQUEST_KEYS, "request");
  assertRequiredKeys(request, PROPOSAL_REQUEST_KEYS, "request");
  const binding = requireCompiledBinding(request.binding, "request.binding");
  const now = assertInstant(request.now, "request.now");
  const read = requireReadThread(request.thread_read, binding, "request.thread_read");

  const raw = assertObject(request.proposal, "request.proposal");
  assertNoCredentialFields(raw, "request.proposal");
  assertNoDispatchFields(raw, "request.proposal");
  assertNoRoutableAddress(raw, "request.proposal");
  assertNoAuthorityClaimFields(raw, "request.proposal");
  assertClosedKeys(raw, PROPOSAL_KEYS, "request.proposal");
  assertRequiredKeys(raw, PROPOSAL_KEYS, "request.proposal");

  const entity = assertExternalIdent(raw.entity, "request.proposal.entity", { maxLength: 128 });
  const field = assertExternalIdent(raw.field, "request.proposal.field", { maxLength: 128 });
  const value_digest = assertSha256Ref(raw.value_digest, "request.proposal.value_digest");
  const rationale = assertSafeText(raw.rationale, "request.proposal.rationale", { maxLength: 2000 });
  const proposed_by = assertEnum(raw.proposed_by, ["model"], "request.proposal.proposed_by",
    "unknown_proposal_author");
  const evidence_provider_message_id = assertExternalIdent(raw.evidence_provider_message_id,
    "request.proposal.evidence_provider_message_id", { maxLength: 255 });
  const declared_data_classes = privacyClasses(raw.declared_data_classes,
    "request.proposal.declared_data_classes");

  const base = {
    schema_version: V5_J103_PROPOSAL_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    binding_digest: binding.binding_digest,
    partner_slug: binding.partner_slug,
    thread_ref: read.thread.thread_ref,
    provenance: read.provenance,
    entity,
    field,
    proposed_by,
    model_seam: "proposed_fact",
    declared_data_classes,
    applied: false,
    authority_established: false,
    next_authority_step: "record-source-authority.v5.js resolveObservation",
    dispatchable: false,
    provider_operation: null,
    observation_candidate: null,
    effects: V5_NO_EFFECTS,
  };

  const privacy = evaluatePrivacyBoundary({ data_classes: declared_data_classes });
  if (privacy.decision === "refuse") {
    return deepFreeze({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      prohibited_classes: [...(privacy.prohibited_classes ?? [])],
      amendment_required: privacy.amendment_required,
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return deepFreeze({
      decision: "needs_independent_privacy_route", reason_id: privacy.reason_id, ...base,
      routed_classes: [...(privacy.routed_classes ?? [])],
      required_evidence: privacy.required_evidence,
    });
  }

  // A proposal has to cite the message it came from, and the citation has to be a
  // message ON THIS THREAD. A proposal citing correspondence nobody read is a
  // model assertion wearing an evidence reference.
  const cited = read.thread.message_refs.find(
    ref => ref.provider_message_id === evidence_provider_message_id);
  if (cited === undefined) {
    return deepFreeze({
      decision: "refuse", reason_id: "evidence_message_not_on_thread", ...base,
      evidence_provider_message_id,
    });
  }
  // The observation this builds is stamped with the cited message's instant, and
  // F01 refuses an observation from the future. Catching it here names the cause
  // — the evidence, not the observation — instead of leaving F01 to report a
  // symptom about a record it was handed.
  if (assertInstant(cited.occurred_at, "request.thread_read.thread.message_refs.occurred_at") > now) {
    return deepFreeze({
      decision: "refuse", reason_id: "evidence_message_occurs_after_now", ...base,
      evidence_provider_message_id, occurred_at: cited.occurred_at,
    });
  }

  // The exact `request.observation` shape resolveObservation takes. Built here,
  // decided there. `taint_class` is this module's, never the caller's: a field a
  // caller could set would be a laundering seam.
  const observation_candidate = {
    entity,
    field,
    tenant: ORGANIZATION_TENANT_ID,
    source_system: read.provenance.source_system,
    account: read.provenance.account,
    native_identity: read.provenance.native_identity,
    value_digest,
    observed_at: cited.occurred_at,
    provenance: {
      adapter_kind: binding.adapter_kind,
      evidence_ref: evidence_provider_message_id,
      retrieval_class: binding.retrieval_class,
    },
    declared_data_classes,
    taint_class: V5_J103_TAINT_CLASS,
  };
  return deepFreeze({
    decision: "proposed", reason_id: "proposal_cites_read_correspondence", ...base,
    rationale,
    evidence_provider_message_id,
    observation_candidate,
  });
}

// ---------------------------------------------------------------------------
// The source conflict queue. Both values, both owners, never a timestamp.
// ---------------------------------------------------------------------------

const CONFLICT_REQUEST_KEYS = Object.freeze([
  "now", "presented_values", "proposed_resolution_basis", "reconciliation_item",
]);
const PRESENTED_SIDE_KEYS = Object.freeze(["declared_data_classes", "value_digest", "value_text"]);
const PRESENTED_KEYS = Object.freeze(["established", "observed"]);

function assertPresentedSide(value, path) {
  assertObject(value, path);
  assertNoDispatchFields(value, path);
  assertClosedKeys(value, PRESENTED_SIDE_KEYS, path);
  assertRequiredKeys(value, PRESENTED_SIDE_KEYS, path);
  return {
    value_text: assertSafeText(value.value_text, `${path}.value_text`, { maxLength: 512 }),
    value_digest: assertSha256Ref(value.value_digest, `${path}.value_digest`),
    declared_data_classes: privacyClasses(value.declared_data_classes, `${path}.declared_data_classes`),
  };
}

/**
 * Build one review-queue entry from an F01 reconciliation item.
 *
 * WHY THE DIGESTS MUST MATCH. F01's reconciliation item carries value DIGESTS, not
 * values, because F01 holds the minimum necessary. Q076 asks the review surface to
 * EXPOSE the values, so the caller supplies them — and a supplied value whose
 * digest does not match the one F01 conflicted on is refused. Without that check a
 * reviewer could be shown a pair of values that were never in conflict, which is a
 * worse failure than showing nothing.
 *
 * WHY THERE IS NO WINNER FIELD. This module does not resolve the conflict; it
 * presents it, names both owners and routes it. `resolved_by_machine: false` and
 * `applied: false` say so on every entry, and the only registered resolution basis
 * is field authority and evidence. A caller proposing recency is refused BY NAME
 * rather than quietly ignored, because Q076's prohibition is the point of the
 * clause and a silently-dropped argument teaches a caller nothing.
 */
export function buildSourceConflictQueueEntry(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CONFLICT_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["now", "presented_values", "reconciliation_item"], "request");
  const now = assertInstant(request.now, "request.now");

  const item = assertObject(request.reconciliation_item, "request.reconciliation_item");
  if (item.schema_version !== V5_F01_RECONCILIATION_SCHEMA_VERSION) {
    fail("uncompiled_reconciliation_item",
      "request.reconciliation_item must be the reconciliation item F01 emitted",
      { expected: V5_F01_RECONCILIATION_SCHEMA_VERSION,
        schema_version: typeof item.schema_version === "string" ? item.schema_version : null });
  }
  const conflict_kind = assertEnum(item.conflict_kind, V5_J103_CONFLICT_KINDS,
    "request.reconciliation_item.conflict_kind", "unknown_conflict_kind");

  const presented = assertObject(request.presented_values, "request.presented_values");
  assertClosedKeys(presented, PRESENTED_KEYS, "request.presented_values");
  assertRequiredKeys(presented, ["observed"], "request.presented_values");
  const observed = assertPresentedSide(presented.observed, "request.presented_values.observed");
  const hasEstablished = item.established !== null && item.established !== undefined;
  const established = hasEstablished
    ? assertPresentedSide(
      assertObject(presented.established, "request.presented_values.established"),
      "request.presented_values.established")
    : null;
  if (!hasEstablished && presented.established !== undefined && presented.established !== null) {
    fail("presented_value_without_conflict_side",
      "request.presented_values.established was supplied for an item F01 recorded no established"
      + " value on; a review surface may not invent a side of the conflict", {});
  }

  const base = {
    schema_version: V5_J103_CONFLICT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    entity: assertExternalIdent(item.entity, "request.reconciliation_item.entity", { maxLength: 128 }),
    field: assertExternalIdent(item.field, "request.reconciliation_item.field", { maxLength: 128 }),
    conflict_kind,
    authoritative_home: item.authoritative_home,
    owner_source: item.owner_source,
    human_resolver_class: item.human_resolver_class,
    route: V5_J103_CONFLICT_ROUTES[conflict_kind],
    resolution_basis: V5_J103_RESOLUTION_BASIS,
    // Q076's prohibition, stated in the record rather than only in the code.
    timestamp_alone_is_not_authority: true,
    refused_resolution_bases: [...V5_J103_REFUSED_RESOLUTION_BASES],
    applied: false,
    resolved_by_machine: false,
    visible: true,
    queued_at: request.now,
    sides: null,
    dispatchable: false,
    provider_operation: null,
    effects: V5_NO_EFFECTS,
  };

  // A caller may propose the basis. Exactly one is registered; every way of saying
  // "whoever wrote last wins" is refused by name.
  if (request.proposed_resolution_basis !== undefined && request.proposed_resolution_basis !== null) {
    const proposed = assertSafeText(request.proposed_resolution_basis,
      "request.proposed_resolution_basis", { maxLength: 128 });
    if (V5_J103_REFUSED_RESOLUTION_BASES.includes(proposed)) {
      return deepFreeze({
        decision: "refuse", reason_id: "timestamp_alone_refused", ...base,
        proposed_resolution_basis: proposed,
      });
    }
    if (proposed !== V5_J103_RESOLUTION_BASIS) {
      return deepFreeze({
        decision: "refuse", reason_id: "unregistered_resolution_basis", ...base,
        proposed_resolution_basis: proposed,
      });
    }
  }

  // A conflict queued before the observation that caused it would put a reviewer
  // in front of a decision that had not happened yet.
  if (assertInstant(item.observed.observed_at, "request.reconciliation_item.observed.observed_at") > now) {
    return deepFreeze({
      decision: "refuse", reason_id: "conflict_observed_after_now", ...base,
      observed_at: item.observed.observed_at,
    });
  }
  if (observed.value_digest !== item.observed.value_digest) {
    return deepFreeze({
      decision: "refuse", reason_id: "presented_value_digest_mismatch", ...base,
      side: "observed",
      expected: item.observed.value_digest,
      actual: observed.value_digest,
    });
  }
  if (established !== null && established.value_digest !== item.established.value_digest) {
    return deepFreeze({
      decision: "refuse", reason_id: "presented_value_digest_mismatch", ...base,
      side: "established",
      expected: item.established.value_digest,
      actual: established.value_digest,
    });
  }

  const classes = [...new Set([
    ...observed.declared_data_classes,
    ...(established === null ? [] : established.declared_data_classes),
  ])].sort();
  const privacy = evaluatePrivacyBoundary({ data_classes: classes });
  if (privacy.decision === "refuse") {
    return deepFreeze({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      declared_data_classes: classes,
      prohibited_classes: [...(privacy.prohibited_classes ?? [])],
      amendment_required: privacy.amendment_required,
    });
  }
  if (privacy.decision === "needs_independent_privacy_route") {
    return deepFreeze({
      decision: "refuse", reason_id: privacy.reason_id, ...base,
      declared_data_classes: classes,
      routed_classes: [...(privacy.routed_classes ?? [])],
      required_evidence: privacy.required_evidence,
    });
  }

  // BOTH sides, each labelled with the source that owns it. `owns_the_field` is
  // computed from F01's own owner_source rather than asserted by the caller, so a
  // queue entry cannot present the wrong side as authoritative.
  const sides = {
    established: established === null ? null : {
      value_text: established.value_text,
      value_digest: established.value_digest,
      source_system: item.established.owner_source,
      owns_the_field: item.established.owner_source === item.owner_source,
      version: item.established.version,
      observed_at: item.established.observed_at,
      account: null,
    },
    observed: {
      value_text: observed.value_text,
      value_digest: observed.value_digest,
      source_system: item.observed.source_system,
      owns_the_field: item.observed.source_system === item.owner_source,
      version: item.observed.version,
      observed_at: item.observed.observed_at,
      account: item.observed.account ?? null,
    },
  };
  return deepFreeze({
    decision: "queued", reason_id: "conflict_routed_for_concise_human_review", ...base,
    declared_data_classes: classes,
    sides,
    both_values_visible: established !== null,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest. Nothing situational is
// bound — no instant, actor, account, session or acceptance — so two callers
// describing the same policy reach the same digest.
// ---------------------------------------------------------------------------

export function v5J103PolicyPreimage() {
  return {
    schema_version: V5_J103_SCHEMA_VERSION,
    policy_version: V5_J103_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    requirement_ids: ["Q005", "Q076", "Q133", "Q134"],
    decision_ids: [...V5_J103_SETTLED_DECISION_IDS],
    decision_subset_digest: v5J103DecisionSubsetDigest(),
    read_interface: {
      schema_version: V5_J103_THREAD_SCHEMA_VERSION,
      binding_schema_version: V5_J103_BINDING_SCHEMA_VERSION,
      source_agnostic: true,
      adapter_kinds: V5_J103_ADAPTER_KINDS.map(adapter_kind => ({
        adapter_kind,
        mode: V5_J103_ADAPTERS[adapter_kind].mode,
        authoritative_home: V5_J103_ADAPTERS[adapter_kind].authoritative_home,
        retrieval_class: V5_J103_ADAPTERS[adapter_kind].retrieval_class,
      })),
      availability_states: [...V5_J103_AVAILABILITY_STATES],
      non_reading_availability: [...V5_J103_NON_READING_AVAILABILITY],
      decisions: [...V5_J103_THREAD_DECISIONS],
      relevance_states: [...V5_J103_RELEVANCE_STATES],
      correspondence_states: [...V5_J103_CORRESPONDENCE_STATES],
      participant_roles: [...V5_J103_PARTICIPANT_ROLES],
      party_kinds: [...V5_J103_PARTY_KINDS],
      related_record_kinds: [...V5_J103_RELATED_RECORD_KINDS],
      ambiguity_resolves_to: "withhold_ambiguous",
      unrelated_resolves_to: "exclude_unrelated",
      privacy_boundary_authority: "global-boundaries.v5.js",
      privacy_evaluated_before_relevance: true,
      mailbox_remains_truth: true,
      subject_line_stored: false,
      attachment_filename_stored: false,
      source_content_field_fragments: [...V5_J103_SOURCE_CONTENT_FRAGMENTS],
      credential_field_fragments: [...V5_J103_CREDENTIAL_FRAGMENTS],
      taint_class: V5_J103_TAINT_CLASS,
      taint_caller_supplied: false,
      native_identity_authority: "record-source-authority.v5.js",
    },
    coverage: {
      schema_version: V5_J103_COVERAGE_SCHEMA_VERSION,
      partners_per_binding: 1,
      combined_partner_coverage_reachable_here: false,
      coverage_gate_id: "partner-mail-calendar-connectors-accepted",
    },
    draft_and_proposal: {
      draft_schema_version: V5_J103_DRAFT_SCHEMA_VERSION,
      proposal_schema_version: V5_J103_PROPOSAL_SCHEMA_VERSION,
      draft_kinds: [...V5_J103_DRAFT_KINDS],
      draft_decisions: [...V5_J103_DRAFT_DECISIONS],
      proposal_decisions: [...V5_J103_PROPOSAL_DECISIONS],
      model_seams: V5_J103_MODEL_SEAM_KEYS.map(seam => ({
        seam, may: V5_J103_MODEL_SEAMS[seam].may, may_not: V5_J103_MODEL_SEAMS[seam].may_not,
      })),
      dispatch_field_fragments: [...V5_J103_DISPATCH_FRAGMENTS],
      holds_routable_destination: false,
      names_provider_operation: false,
      send_operations_here: [],
      requires_human_send: true,
      send_authority_holder: V5_J103_SEND_AUTHORITY_HOLDER,
      send_authority_seam: V5_J103_SEND_AUTHORITY_SEAM,
      establishes_authority: false,
      next_authority_step: "record-source-authority.v5.js resolveObservation",
    },
    source_reconciliation: {
      schema_version: V5_J103_CONFLICT_SCHEMA_VERSION,
      f01_reconciliation_schema_version: V5_F01_RECONCILIATION_SCHEMA_VERSION,
      conflict_kinds: V5_J103_CONFLICT_KINDS.map(conflict_kind => ({
        conflict_kind, route: V5_J103_CONFLICT_ROUTES[conflict_kind],
      })),
      review_routes: [...V5_J103_REVIEW_ROUTES],
      resolution_basis: V5_J103_RESOLUTION_BASIS,
      refused_resolution_bases: [...V5_J103_REFUSED_RESOLUTION_BASES],
      decisions: [...V5_J103_CONFLICT_DECISIONS],
      both_values_exposed: true,
      owner_computed_from_f01: true,
      resolved_by_machine: false,
      conflict_detection_authority: "record-source-authority.v5.js",
    },
    acceptance: {
      acceptance_hook: V5_J103_ACCEPTANCE_HOOK,
      contract_binding_step: V5_J103_CONTRACT_BINDING_STEP,
      consumer_gates: [...V5_J103_CONSUMER_GATES],
      satisfied_by_this_module: false,
    },
  };
}

/** The deterministic `sha256:` digest of the closed V5-J103 correspondence policy. */
export function v5J103PolicyDigest() {
  return digest(v5J103PolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5J103PolicyCanonicalBytes() {
  return canonicalJson(v5J103PolicyPreimage());
}

/**
 * The zero-effect projection of the whole interface: what is settled, what the
 * policy hashes to, and the explicit statement that reading it accepts nothing.
 */
export function v5J103CorrespondenceProjection(options = {}) {
  assertObject(options, "options");
  assertClosedKeys(options, ["expected_policy_digest"], "options");
  const policy_digest = v5J103PolicyDigest();
  if (options.expected_policy_digest !== undefined) {
    if (typeof options.expected_policy_digest !== "string" ||
        !SHA256_REF.test(options.expected_policy_digest)) {
      fail("invalid_expected_digest", "options.expected_policy_digest must be a sha256: reference",
        { path: "options.expected_policy_digest" });
    }
    if (options.expected_policy_digest !== policy_digest) {
      fail("stale_expected_digest",
        "the policy no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_policy_digest, actual: policy_digest });
    }
  }
  return deepFreeze({
    schema_version: V5_J103_PROJECTION_SCHEMA_VERSION,
    policy_version: V5_J103_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_digest,
    decision_subset_digest: v5J103DecisionSubsetDigest(),
    requirement_ids: ["Q005", "Q076", "Q133", "Q134"],
    acceptance_hook: V5_J103_ACCEPTANCE_HOOK,
    journey_one_contract_binding_receipt_present: false,
    journey_one_production_outcome_present: false,
    combined_partner_coverage_available: false,
    external_send_capability_present: false,
    accepts_anything: false,
    gaps: governedCorrespondenceGaps(),
    effects: V5_NO_EFFECTS,
  });
}

/**
 * The named gaps. Each is a MISSING FACT this repository does not hold, not a
 * build task deferred out of laziness, and none of them is guessed anywhere above.
 */
export function governedCorrespondenceGaps() {
  return deepFreeze([
    {
      gap: "no_journey_one_contract_binding_receipt",
      where: V5_J103_CONTRACT_BINDING_STEP,
      what: "the slice's runtime evidence input is a Journey 1 contract-binding receipt produced"
        + " by an independent oracle. None exists, so nothing here is bound to a runtime"
        + " contract and passing this suite satisfies no consumer gate",
      landed: false,
    },
    {
      gap: "no_mailbox_field_authority_registry",
      where: "the future Neon-backed F01 field-authority registry",
      what: "F01 ships no registry entry for a correspondence-derived entity, so the observation"
        + " candidate evaluateProposedFact builds cannot yet be resolved against one. This"
        + " module invents no entry to sit under and no owner_source for a field it reads about",
      landed: false,
    },
    {
      gap: "no_available_adapter_receipt",
      where: "the partner's own device",
      what: "a binding's `availability` is a caller-supplied observation, not an attestation."
        + " Nothing in this repository proves an authorized adapter is installed and reading, so"
        + " an `available` binding is a claim the caller makes and this module answers under",
      landed: false,
    },
    {
      gap: "no_persistence",
      where: "the future Neon correspondence tables",
      what: "Q133 says DoctorCRE STORES provenance-linked threads and drafts. This module emits"
        + " the exact shapes those rows would hold and writes none of them; there is no store, no"
        + " migration and no registered verb in this slice",
      landed: false,
    },
    {
      gap: "no_external_send_authority_decision",
      where: V5_J103_SEND_AUTHORITY_SEAM,
      what: "where external send would ever be permitted is not decided, and Q133 places it in a"
        + " later representative workflow. The seam is hashed into the policy preimage, so the"
        + " day the clause is written the policy digest moves and stale readers refuse",
      landed: false,
    },
    {
      gap: "no_both_partner_coverage",
      where: "partner-mail-calendar-connectors-accepted",
      what: "combined Joe/Dell correspondence coverage is produced by an independent oracle"
        + " against production partner devices. No receipt exists, so every path here answers"
        + " `unavailable` and no argument reaches an available answer",
      landed: false,
    },
  ]);
}

/**
 * The write operations this module deliberately does NOT hold.
 *
 * Imported from F10 rather than re-listed, so the claim "J103 names none of
 * these" is checked against the registry that actually owns them. A write
 * operation added upstream lands in this list automatically, and the suite's
 * disjointness assertion keeps it honest.
 */
export function v5J103AbsentWriteOperations() {
  return deepFreeze([...V5_F10_WRITE_OPERATIONS]);
}

// ---------------------------------------------------------------------------
// Load-time self-check: no guard can ever refuse a legitimate request.
//
// Three of the four scans match on SUBSTRINGS of field names, which is what makes
// them work on a field nobody anticipated — and what makes them capable of
// refusing a field this module itself requires. F01 pays for the same guarantee
// the same way. Each seam is checked against exactly the scans it runs, because
// the asymmetry is deliberate: `draft_body` contains "body" and is legitimate
// precisely because the draft seam does not run the source-content scan.
// ---------------------------------------------------------------------------

function assertNoGuardCollision(keys, fragments, seam, guard) {
  for (const key of keys) {
    const normalized = key.toLowerCase();
    const fragment = fragments.find(f => normalized.includes(f));
    if (fragment !== undefined) {
      throw new V5J103Error("guard_refuses_own_schema",
        `the ${guard} guard would refuse "${key}", a field the ${seam} seam requires`,
        { seam, guard, key, fragment });
    }
  }
}

for (const [seam, keys, guards] of [
  ["binding", BINDING_KEYS, ["credential", "dispatch"]],
  ["thread", [
    ...THREAD_KEYS, ...NATIVE_IDENTITY_KEYS, ...PARTICIPANT_KEYS, ...MESSAGE_REF_KEYS,
    ...ATTACHMENT_KEYS, ...RELATED_RECORD_KEYS,
  ], ["credential", "source_content", "dispatch"]],
  ["draft", DRAFT_KEYS, ["credential", "dispatch"]],
  ["proposal", PROPOSAL_KEYS, ["credential", "dispatch", "authority"]],
  ["conflict", PRESENTED_SIDE_KEYS, ["dispatch"]],
]) {
  if (guards.includes("credential")) {
    assertNoGuardCollision(keys, V5_J103_CREDENTIAL_FRAGMENTS, seam, "credential");
  }
  if (guards.includes("source_content")) {
    assertNoGuardCollision(keys, V5_J103_SOURCE_CONTENT_FRAGMENTS, seam, "source content");
  }
  if (guards.includes("dispatch")) {
    assertNoGuardCollision(keys, V5_J103_DISPATCH_FRAGMENTS, seam, "dispatch");
  }
  if (guards.includes("authority")) {
    assertNoGuardCollision(keys, V5_F01_AUTHORITY_INJECTION_FRAGMENTS, seam, "authority claim");
  }
}
