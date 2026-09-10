// DoctorCRE v5 slice V5-F04: the DURABLE half of Q106's role descriptions.
//
// model-routing.v5.js can DEFINE a role — validate a job description, seal it
// with `role_digest`, and prove that no occupant field participates in that
// seal. It cannot STORE one. It says so itself, under
// `v5ModelRoutingProjection().unimplemented_dependencies`: "durable
// qualification and decision record store: this slice adds no table, no
// migration and no SQL integration". This file is the missing durable half for
// the ROLE alone: versioned immutable revisions, an append-only history, and an
// explicit current pointer moved only by a compare-and-swap under S01's
// retained system authority.
//
// IT IS NOT A SECOND ROLE CONTRACT, AND NOT A SECOND DIGEST SCHEME.
// `defineRole` is imported and called; the field list, the six role keys, the
// occupiable authority classes, the quality-floor rule, the reference grammar
// and the seal are all its. Nothing here re-decides what a role IS. There is
// exactly one hash over a role in this system — `digest(content)` as
// `defineRole` computes it — and both this module and ops/model-role-store.candidate.sql
// recompute THAT value from the persisted rows. No domain tag is wrapped around
// it here, because wrapping one would be a second digest scheme wearing the
// first one's name.
//
// A DURABLE ROLE CONFERS NOTHING. Storing a job description durably does not
// make it an authority, an occupancy, a route, a qualification or a numeric
// policy default. Every write result below carries `grants_authority: false`,
// `binds_occupant: false` and `measured_qualification_bound: false` as fields
// rather than as prose, and there is no argument, column or code path through
// which an occupant, a grant or a floor number could arrive. A role DECLARES an
// authority class; whether an actor may act is still S01's
// `evaluateActorAuthority` question, asked of a live actor at a live instant.
//
// THE ONE ACT THAT NEEDS AUTHORITY IS THE CURRENT POINTER, AND IT IS JOE'S.
// Recording a revision is inert: it appends a description nobody has selected.
// Naming WHICH revision is current is a system-design act, so it is evaluated
// through S01's own `evaluateActorAuthority` under the `system.design` action —
// authority class `system_authority`, which S01 reserves to the system-authority
// partner and makes non-delegable. This module does not restate that rule, does
// not weaken it, and accepts no `actor`, `approved`, `verified`, `principal` or
// `occupant` field from any payload: `assertNoSelfAssertedAuthority` refuses
// those by NAME at every depth, before the closed schemas even run.
//
// THE PRINCIPAL IS DERIVED TWICE, BY TWO SERVER-HELD FACTS, AND NEITHER IS A
// CALLER'S WORD. In this process, from the live actor object the server built,
// through S01. In the database, from `ops.authority_actor_slug()`, which reads
// `session_user` on the per-partner authority connection and admits nothing
// else — the same derivation migration 0161's `ops.disable_legacy_schedule`
// already uses to hold a Joe-only act. Both seams EXIST; the current-pointer
// path is therefore implemented rather than failed closed.
//   WHAT IS STILL NOT BOUND, said exactly rather than implied: nothing ties the
//   actor object this module sees to the database session that answers
//   `session_user`. Both are server-derived and neither is a caller claim, but
//   no record proves they name the same human at the same instant — an
//   authenticated-receipt-identity binding would, and this repository mints
//   none. See MODEL_ROLE_PRINCIPAL_BINDING_SCOPE, which states the claim and
//   its limit on every result rather than leaving a reader to assume the
//   stronger one.
//
// WHAT THE DATABASE RECHECKS, because a JS caller's assertion is not evidence.
// The candidate SQL rebuilds the whole role preimage FROM THE STORED ROWS,
// recomputes the digest over those bytes, re-derives the writer and the partner
// principal, and refuses on any drift — before a revision is returned and again
// before a current pointer is inserted. A digest a caller supplies is only ever
// COMPARED against one the rows produce. No current role is hardcoded anywhere:
// there is no seed, no fixture role and no default revision in this file or in
// the candidate SQL, and a role nobody has recorded reads back as absent.
//
// READS RECONSTRUCT AND REVALIDATE. Every revision the record layer returns is
// rebuilt HERE through `defineRole` and re-hashed, so a read is a statement
// about bytes this process independently re-derived rather than a column the
// database happened to hold. The authenticity that buys is stated precisely in
// MODEL_ROLE_AUTHENTICITY_SCOPE: it is re-derivation from committed rows, and
// it is NOT a signature, NOT a capability token, and NOT the routing kernel's
// process-local WeakSet provenance — model-routing.v5.js already says those
// brands cannot survive a database row, and storing a role does not change it.
//
// NAMED, NOT FILLED. This slice adds no qualification producer, no dispatch and
// no backend health source. Those gaps belong to the routing kernel's own
// projection and are re-exported below from it rather than restated, so there
// is one list and it stays one list.
//
// NOT REGISTERED. `modelRoleStoreTools` is exported and is deliberately added
// to no tool index by this slice: registering a verb — and especially a
// humanOnly/authorityOnly one — is a separate, reviewed act. Applying
// ops/model-role-store.candidate.sql as a numbered migration is another.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, authorizationClassForActor, isKnownPartner } from "./identity.js";
import {
  V5_AUTHORITY_CLASSES,
  V5_NO_EFFECTS,
  V5_SYSTEM_AUTHORITY_PARTNER,
  evaluateActorAuthority,
} from "./global-boundaries.v5.js";
import {
  V5_OCCUPIABLE_AUTHORITY_CLASSES,
  V5_PROVENANCE_SCOPE,
  V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
  V5_ROLE_KEYS,
  defineRole,
  v5ModelRoutingProjection,
} from "./model-routing.v5.js";

// Module-local ADAPTER schemas. Namespaced so they can never be mistaken for
// the role contract itself: `role-description.v1` is the kernel's and is
// imported, never redeclared.
export const MODEL_ROLE_REVISION_ROWS_SCHEMA = "doctorcre-v5-model-role-revision-rows.v1";
export const MODEL_ROLE_STORE_PROJECTION_SCHEMA = "doctorcre-v5-model-role-store-projection.v1";
export const MODEL_ROLE_READBACK_SCHEMA = "doctorcre-v5-model-role-readback.v1";
export const MODEL_ROLE_REVISION_ENTRY_SCHEMA = "doctorcre-v5-model-role-revision-entry.v1";

/**
 * The exact content fields `defineRole` hashes, C-sorted.
 *
 * This list is not a second contract: `modelRoleRevisionRows` compares it
 * against the content keys of the role the kernel actually sealed, so a future
 * field added to `defineRole` makes the row projection REFUSE rather than
 * silently hash a field it does not store.
 */
export const MODEL_ROLE_CONTENT_FIELDS = Object.freeze([
  "authority", "evidence_requirements", "minimum_strength_ref", "mission",
  "quality_floor_refs", "role_key", "rules", "schema_version", "skills",
  "task_classes", "tenant", "title",
]);

/** The fields `defineRole` adds to its content; excluded from the content set. */
const ROLE_DERIVED_FIELDS = Object.freeze([
  "effects", "occupant_bound", "occupants_replaceable", "role_digest",
  "role_confers_no_authority_by_itself",
]);

/**
 * The three ORDERED text lists. `defineRole` preserves their supplied order and
 * the order participates in the digest, so each row carries an explicit
 * zero-based ordinal and a gap is a refusal rather than a shorter list.
 */
export const MODEL_ROLE_TEXT_FIELDS = Object.freeze(["evidence_requirements", "rules", "skills"]);

/**
 * The three reference SETS. `defineRole` sorts them, so their stored order is
 * storage detail rather than hash-bearing — which is exactly why the candidate
 * SQL emits them `order by value collate "C"` rather than by ordinal, and why
 * `modelRoleFromPreimage` re-hashes the emitted bytes instead of trusting them.
 * `capability_refs` lives under `role.authority`; the other two are top level.
 */
export const MODEL_ROLE_REF_FIELDS = Object.freeze([
  "capability_refs", "quality_floor_refs", "task_classes",
]);

/** The scalar columns of a revision row. `role_key` is its own column. */
export const MODEL_ROLE_SCALAR_FIELDS = Object.freeze([
  "authority_class", "minimum_strength_ref", "mission", "title",
]);

/**
 * The two content fields that are EMITTED rather than stored per revision.
 * They are identity: `schema_version` is the kernel's constant and `tenant` is
 * identity.js's one server-held tenant. Storing either would make a fixed
 * constant something a row could move.
 */
export const MODEL_ROLE_EMITTED_CONSTANT_FIELDS = Object.freeze(["schema_version", "tenant"]);

/**
 * The S01 action a current-pointer change is evaluated under.
 *
 * Naming which revision of a durable job description is CURRENT is a system
 * design act. `system.policy`, `system.release_decision` and the other four
 * system actions would all resolve to the same authority CLASS, which is the
 * part that binds: `system_authority`, retained by the system-authority partner
 * and non-delegable. One action has to be named to ask S01 the question, and
 * this is the one named. No new action is registered anywhere.
 */
export const MODEL_ROLE_CURRENT_POINTER_ACTION = "system.design";

/**
 * What a read of a stored role is authentic TO. Written out because the
 * interesting part of an authenticity claim is its limit.
 */
export const MODEL_ROLE_AUTHENTICITY_SCOPE = Object.freeze({
  claim: "re_derived_from_committed_rows_and_resealed_here_through_defineRole",
  proves: Object.freeze([
    "the persisted rows rebuild a role description the kernel's defineRole accepts, under the same schema, role keys, occupiable authority classes and reference grammar it enforces for a fresh one",
    "those rebuilt bytes hash to the role_digest recorded beside them, and to the digest the record layer independently recomputed from the same rows",
    "the revision is one of an append-only history, and the current pointer names one of its members",
  ]),
  does_not_prove: Object.freeze([
    "that the bytes are signed: nothing in this slice issues, holds or verifies a key, a signature or a capability token",
    "that the role is authentic to model-routing.v5.js's process-local provenance registers: those are WeakSet brands over object identity and do not survive a database row, which that module states of itself",
    "that any model is qualified for this role: no measured route-qualification.v1 record is read, produced or implied here",
    "that any occupant holds this role, or that any dispatch, activation or execution is permitted by it",
  ]),
});

/**
 * The principal binding this rail can and cannot state. Both derivations are
 * server-held; the pairing between them is not recorded anywhere.
 */
export const MODEL_ROLE_PRINCIPAL_BINDING_SCOPE = Object.freeze({
  in_process_derivation: "global-boundaries.v5.js evaluateActorAuthority over the live actor object the server built, under the system.design action",
  record_layer_derivation: "ops.authority_actor_slug(), which reads session_user on the per-partner authority connection provisioned by migration 0161 and admits no other principal",
  both_are_server_derived: true,
  accepts_actor_from_payload: false,
  cross_derivation_binding_recorded: false,
  // The residual gap, named rather than implied. It does not make either
  // derivation weaker; it means a receipt may not claim they are ONE fact.
  unbound_seam: "no record ties the actor object this module evaluates to the database session that answers session_user, so a current-pointer row records two independently derived statements about the same act rather than one authenticated identity. Minting an authenticated-receipt-identity binding would be inventing the very record that is missing, and identity.js is not patched here to produce one.",
});

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// identity.js's ISO instant shape, as the record layer emits it. Parsed, never
// inferred, and never supplied by a caller: see `readServerInstant`.
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/;

/**
 * Argument keys through which a caller would be asserting the very thing this
 * rail derives. Matched on the normalized key AT EVERY DEPTH, so a wrapper
 * object cannot smuggle one past a closed schema, and so a closed schema that
 * gains a field in a later edit still refuses these.
 *
 * `authority` is deliberately ABSENT from this list and `capability_refs` is
 * too: both are legitimate CONTENT of a role description — the class it
 * declares and the capability references its jobs may draw on — and refusing
 * them would refuse the payload this rail exists to store. What is refused is
 * the shape that asserts an ACT: an actor, an approval, a verification, a
 * principal, a grant, an occupant or a qualification.
 */
const SELF_ASSERTED_AUTHORITY_FRAGMENTS = Object.freeze([
  "accepted_by", "acting_actor", "actor", "approved", "authority_granted",
  "confirmed", "granted", "human_approved", "occupancy", "occupant",
  "partner_confirmed", "principal", "qualification", "qualified",
  "session_user", "set_by", "signed", "system_authority", "verified",
]);

export class ModelRoleStoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "ModelRoleStoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new ModelRoleStoreError(code, message, detail);
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

function copy(value) {
  if (Array.isArray(value)) return value.map(copy);
  if (isPlainObject(value)) {
    const out = {};
    for (const key of Object.keys(value)) out[key] = copy(value[key]);
    return out;
  }
  return value;
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function assertObject(value, path) {
  if (!isPlainObject(value)) refuse("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      refuse("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertUuid(value, path) {
  if (typeof value !== "string" || !UUID.test(value)) {
    refuse("invalid_uuid", `${path} must be a uuid`, { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    refuse("invalid_digest", `${path} must be a "sha256:" reference`, { path });
  }
  return value;
}

function assertRoleKey(value, path) {
  if (!V5_ROLE_KEYS.includes(value)) {
    refuse("unknown_role_key", `${path} is not one of the settled v5 roles`,
      { path, role_key: typeof value === "string" ? value : null, registered: [...V5_ROLE_KEYS] });
  }
  return value;
}

function assertRevisionNo(value, path) {
  if (!Number.isSafeInteger(value) || value < 1) {
    refuse("invalid_revision_no", `${path} must be an integer of at least 1`, { path, value });
  }
  return value;
}

/**
 * The subset of the fragments above that name an OCCUPANCY or a QUALIFICATION.
 * They get their own refusal — the fail-closed one below — because "you may not
 * assert your own authority" and "no producer of measured qualification exists
 * in this repository" are different findings with different remedies.
 */
const OCCUPANCY_FRAGMENTS = Object.freeze(["occupancy", "occupant", "qualification", "qualified"]);

/**
 * The first key ANYWHERE in a caller structure that asserts an act this rail
 * derives, or null. Structural rather than textual on purpose: a rule or
 * mission is free text and may legitimately CONTAIN the word "occupant", and a
 * scan over serialized bytes would refuse the very job descriptions Q106 is
 * about. Only a KEY is a claim.
 */
function findSelfAssertedKey(value, path) {
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) {
      const hit = findSelfAssertedKey(item, `${path}[${index}]`);
      if (hit) return hit;
    }
    return null;
  }
  if (!isPlainObject(value)) return null;
  for (const key of Object.keys(value)) {
    const normalized = key.toLowerCase();
    for (const fragment of SELF_ASSERTED_AUTHORITY_FRAGMENTS) {
      if (normalized === fragment || normalized.endsWith(`_${fragment}`) ||
          normalized.startsWith(`${fragment}_`)) {
        return { path: `${path}.${key}`, key, fragment };
      }
    }
    const hit = findSelfAssertedKey(value[key], `${path}.${key}`);
    if (hit) return hit;
  }
  return null;
}

/** Refuse a self-asserted authority claim anywhere in a caller structure. */
export function assertNoSelfAssertedAuthority(value, path) {
  const hit = findSelfAssertedKey(value, path);
  if (hit) {
    refuse("self_asserted_authority_refused",
      `"${hit.key}" at ${path} asserts an act this rail derives from the server; it is never accepted from a caller`,
      hit);
  }
}

// ---------------------------------------------------------------------------
// The revision: one sealed role description decomposed into typed rows.
// ---------------------------------------------------------------------------

/** The content keys of one sealed role, C-sorted, with the derived seal removed. */
function sealedContentFields(sealed) {
  return Object.keys(sealed).filter(key => !ROLE_DERIVED_FIELDS.includes(key)).sort();
}

/**
 * Validate one proposed role description and return the kernel's sealed view.
 *
 * The self-asserted-authority sweep runs FIRST, so a payload carrying
 * `approved: true` or an `occupant` meets a refusal that names what it did
 * rather than the kernel's generic closed-shape answer.
 */
export function validateRoleDescription(description) {
  if (!isPlainObject(description)) {
    refuse("invalid_shape", "role must be an object", { path: "role" });
  }
  assertNoSelfAssertedAuthority(description, "role");
  // The kernel owns every remaining rule and throws V5RoutingError with its own
  // stable codes, which are deliberately not re-wrapped: a caller that learns
  // "role_authority_class_not_occupiable" should see that code, not a second
  // vocabulary for the same fact.
  return defineRole(description);
}

/**
 * Decompose one sealed role description into the typed rows the record layer
 * stores.
 *
 * WHAT IS NOT STORED, and the assertion that keeps it true: `schema_version`
 * and `tenant` are emitted constants, and every other content field must be
 * accounted for by a scalar, a text list, a reference set or `role_key`. If a
 * later edit added a thirteenth content field, this function refuses rather
 * than storing twelve while hashing thirteen.
 */
export function modelRoleRevisionRows(description) {
  const sealed = validateRoleDescription(description);

  const present = sealedContentFields(sealed);
  const expected = [...MODEL_ROLE_CONTENT_FIELDS];
  const unaccounted = present.filter(field => !expected.includes(field));
  const absent = expected.filter(field => !present.includes(field));
  if (unaccounted.length > 0 || absent.length > 0) {
    refuse("model_role_field_unstored",
      "the sealed role's content fields are not the ones this row projection stores; a field that is hashed but not persisted could not be rebuilt",
      { unaccounted, absent, expected });
  }
  // The same check one level down: every content field must be reachable from a
  // scalar, a text list, a reference set, `role_key`, or the two emitted
  // constants. `role.authority` is accounted for by `authority_class` plus
  // `capability_refs`, which is why it is named here rather than left implicit.
  const persisted = new Set([
    ...MODEL_ROLE_SCALAR_FIELDS, ...MODEL_ROLE_TEXT_FIELDS, ...MODEL_ROLE_REF_FIELDS,
    ...MODEL_ROLE_EMITTED_CONSTANT_FIELDS, "authority", "role_key",
  ]);
  const unreachable = expected.filter(field => !persisted.has(field));
  if (unreachable.length > 0) {
    refuse("model_role_field_unstored",
      `${unreachable.length} content field(s) would be hashed but not stored; the row projection is incomplete`,
      { fields: unreachable });
  }

  const scalars = {
    title: sealed.title,
    mission: sealed.mission,
    minimum_strength_ref: sealed.minimum_strength_ref,
    authority_class: sealed.authority.authority_class,
  };
  const scalarKeys = Object.keys(scalars).sort();
  if (scalarKeys.length !== MODEL_ROLE_SCALAR_FIELDS.length ||
      scalarKeys.some((key, index) => key !== MODEL_ROLE_SCALAR_FIELDS[index])) {
    refuse("model_role_field_unstored",
      "the scalar row does not carry exactly the declared scalar fields",
      { declared: [...MODEL_ROLE_SCALAR_FIELDS], built: scalarKeys });
  }

  // ORDER IS PART OF THE HASH for these three, so each row carries its ordinal.
  const texts = [];
  for (const field of MODEL_ROLE_TEXT_FIELDS) {
    sealed[field].forEach((value, ordinal) => texts.push({ field, ordinal, value }));
  }

  // ORDER IS NOT PART OF THE HASH for these three — the kernel sorts them — so
  // the ordinal is the position in the SORTED order the kernel produced, and
  // the record layer re-sorts on the way out rather than trusting it.
  const refs = [];
  for (const field of MODEL_ROLE_REF_FIELDS) {
    const values = field === "capability_refs" ? sealed.authority.capability_refs : sealed[field];
    values.forEach((value, ordinal) => refs.push({ field, ordinal, value }));
  }

  return deepFreeze({
    schema_version: MODEL_ROLE_REVISION_ROWS_SCHEMA,
    role_schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    role_key: sealed.role_key,
    role_digest: sealed.role_digest,
    scalars,
    texts,
    refs,
    // Properties of every role this projection can produce, carried so a
    // reviewer does not have to re-derive them from the kernel.
    occupant_bound: false,
    confers_authority: false,
  });
}

/**
 * Rebuild the exact object `defineRole` hashes from its typed rows.
 *
 * This is the JavaScript twin of ops.model_role_preimage(). It is deliberately
 * separate from the reseal below: the preimage is BYTES, and the reseal is a
 * CONTRACT CHECK over them, and conflating the two is how a rebuild that loses
 * a field ends up hashing to something nobody computed.
 */
function preimageFromRows(rows) {
  if (!isPlainObject(rows)) refuse("invalid_shape", "rows must be an object", { path: "rows" });
  if (rows.schema_version !== MODEL_ROLE_REVISION_ROWS_SCHEMA) {
    refuse("wrong_rows_schema", `rows.schema_version must be "${MODEL_ROLE_REVISION_ROWS_SCHEMA}"`,
      { actual: rows.schema_version });
  }
  const ordered = (list, field, path) => {
    if (!Array.isArray(list)) refuse("invalid_shape", `${path} must be an array`, { path });
    const rowsForField = list.filter(row => isPlainObject(row) && row.field === field);
    const sorted = [...rowsForField].sort((a, b) => a.ordinal - b.ordinal);
    sorted.forEach((row, index) => {
      // A gap is a row that was expected and is missing, which is exactly the
      // shape a partial insert leaves behind. Rebuilding from gapped rows would
      // produce a shorter list hashing to something no proposer computed.
      if (row.ordinal !== index) {
        refuse("model_role_row_ordinal_gap",
          `${path}[${field}] is not contiguously ordinaled from zero`,
          { path, field, expected: index, actual: row.ordinal });
      }
    });
    return sorted.map(row => row.value);
  };

  return {
    schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    role_key: rows.role_key,
    title: rows.scalars?.title,
    mission: rows.scalars?.mission,
    skills: ordered(rows.texts, "skills", "rows.texts"),
    rules: ordered(rows.texts, "rules", "rows.texts"),
    authority: {
      authority_class: rows.scalars?.authority_class,
      // Sorted here rather than taken on the ordinals' word: the kernel sorts
      // these, so the canonical bytes are the sorted ones whatever a row says.
      capability_refs: [...ordered(rows.refs, "capability_refs", "rows.refs")].sort(),
    },
    evidence_requirements: ordered(rows.texts, "evidence_requirements", "rows.texts"),
    quality_floor_refs: [...ordered(rows.refs, "quality_floor_refs", "rows.refs")].sort(),
    minimum_strength_ref: rows.scalars?.minimum_strength_ref,
    task_classes: [...ordered(rows.refs, "task_classes", "rows.refs")].sort(),
  };
}

/**
 * Rebuild one role from the exact bytes that were hashed, and revalidate it
 * THROUGH THE KERNEL.
 *
 * Three independent checks, and each catches something the others do not:
 *   * the emitted constants are the constants, so a preimage from another
 *     schema version or another tenant cannot occupy this relation;
 *   * `defineRole` re-runs every role rule over the rebuilt description, so a
 *     row set that hashes perfectly and describes an impossible role — system
 *     authority, no quality floor, no task class, a role key nobody settled —
 *     is refused rather than admitted on its hash;
 *   * the SUPPLIED BYTES are re-hashed and compared against the reseal, so a
 *     preimage that reseals to a valid role but whose serialization differs
 *     from the canonical one (an unsorted reference set, an absent field, an
 *     extra one) is caught instead of being quietly re-canonicalized.
 */
export function modelRoleFromPreimage(preimage, path = "preimage") {
  assertObject(preimage, path);
  assertClosedKeys(preimage, MODEL_ROLE_CONTENT_FIELDS, path);
  for (const field of MODEL_ROLE_CONTENT_FIELDS) {
    if (!hasOwn(preimage, field)) {
      refuse("missing_field", `${path}.${field} is required`, { path: `${path}.${field}` });
    }
  }
  if (preimage.schema_version !== V5_ROLE_DESCRIPTION_SCHEMA_VERSION) {
    refuse("model_role_schema_version_invalid",
      `${path}.schema_version must be ${V5_ROLE_DESCRIPTION_SCHEMA_VERSION}`,
      { path, actual: preimage.schema_version });
  }
  if (preimage.tenant !== ORGANIZATION_TENANT_ID) {
    refuse("model_role_tenant_mismatch", `${path}.tenant must be ${ORGANIZATION_TENANT_ID}`,
      { path, actual: preimage.tenant });
  }
  const authority = assertObject(preimage.authority, `${path}.authority`);
  // The kernel's refusal codes reach the caller unwrapped, for the reason
  // validateRoleDescription gives.
  const sealed = defineRole({
    role_key: preimage.role_key,
    title: preimage.title,
    mission: preimage.mission,
    skills: preimage.skills,
    rules: preimage.rules,
    authority: { authority_class: authority.authority_class, capability_refs: authority.capability_refs },
    evidence_requirements: preimage.evidence_requirements,
    quality_floor_refs: preimage.quality_floor_refs,
    minimum_strength_ref: preimage.minimum_strength_ref,
    task_classes: preimage.task_classes,
  });
  const suppliedDigest = digest(copy(preimage));
  if (suppliedDigest !== sealed.role_digest) {
    refuse("model_role_preimage_not_canonical",
      `${path} reseals to a valid role, but the supplied bytes are not the canonical ones that role hashes to`,
      { path, supplied_bytes_digest: suppliedDigest, canonical_role_digest: sealed.role_digest });
  }
  return sealed;
}

/**
 * The round trip the record layer's storage rests on:
 * `modelRoleFromRows(modelRoleRevisionRows(role)).role_digest` is
 * `defineRole(role).role_digest`, for every role the kernel accepts. A
 * decomposition that loses a field, an order or a value is a record layer that
 * cannot reproduce the digest a reader is about to check.
 */
export function modelRoleFromRows(rows) {
  const sealed = modelRoleFromPreimage(preimageFromRows(rows), "rows");
  if (typeof rows.role_digest === "string" && rows.role_digest !== sealed.role_digest) {
    refuse("model_role_digest_mismatch",
      "the rebuilt role does not hash to the role_digest stored beside it",
      { expected: sealed.role_digest, stored: rows.role_digest });
  }
  if (rows.role_key !== sealed.role_key) {
    refuse("model_role_key_mismatch", "the rebuilt role is not the role these rows are filed under",
      { expected: sealed.role_key, stored: rows.role_key });
  }
  return sealed;
}

/**
 * The exact object `defineRole` hashes, for one role description.
 *
 * The JavaScript twin of ops.model_role_preimage(). It is exported so the two
 * halves of this rail can be compared field for field rather than only through
 * a hash that agrees for reasons nobody checked, and so a reviewer can rebuild
 * the bytes by hand.
 */
export function modelRolePreimage(description) {
  const sealed = validateRoleDescription(description);
  const preimage = {};
  for (const field of MODEL_ROLE_CONTENT_FIELDS) {
    preimage[field] = field === "authority"
      ? {
        authority_class: sealed.authority.authority_class,
        capability_refs: [...sealed.authority.capability_refs],
      }
      : copy(sealed[field]);
  }
  return deepFreeze(preimage);
}

/** The canonical bytes of one role's preimage, so a reviewer can rehash by hand. */
export function modelRoleCanonicalBytes(description) {
  return canonicalJson(modelRolePreimage(description));
}

// ---------------------------------------------------------------------------
// The one act that needs authority.
// ---------------------------------------------------------------------------

/**
 * Derive the principal permitted to move a role's current pointer, from the
 * LIVE actor, through S01, at a server-held instant.
 *
 * The authority question is S01's and is asked rather than re-implemented: this
 * function selects the action and reads the answer. Three additional checks sit
 * beside it and each would be redundant with the current definitions — which is
 * why they are written out. `authorizationClassForActor` returns
 * "verified_partner" exactly when the actor is a human known partner today, and
 * S01 allows a `system_authority` action only for the system-authority partner;
 * if either definition is ever widened, a durable role's current pointer must
 * not widen with it by accident.
 *
 * `now` is the RECORD LAYER'S instant, never a caller's and never this module's:
 * this file reads no clock, and a caller-movable clock is a caller-movable
 * authority window. S01's answer for a `system_authority` action does not
 * depend on the instant at all — it is decided before any window is read — so
 * the instant is a formality here rather than a lever, and it is still taken
 * from the server so that the formality cannot become one later.
 */
export function deriveRoleCurrentPointerAuthority({ actor, now } = {}) {
  if (!isPlainObject(actor)) {
    refuse("role_authority_identity_unavailable",
      "moving a role's current pointer requires an authenticated actor; none was supplied",
      { path: "actor" });
  }
  if (typeof now !== "string" || !ISO_INSTANT.test(now)) {
    refuse("server_instant_unavailable",
      "the S01 authority evaluation takes a server-held instant; this module reads no clock and accepts none from a caller",
      { path: "now" });
  }
  const authorization_class = authorizationClassForActor(actor);
  const decision = evaluateActorAuthority({
    actor, action: MODEL_ROLE_CURRENT_POINTER_ACTION, tenant: ORGANIZATION_TENANT_ID, now,
  });
  if (decision.decision !== "allow") {
    refuse("role_current_pointer_authority_refused",
      "S01 refused this actor the system-authority action a current-pointer change is evaluated under",
      {
        action: MODEL_ROLE_CURRENT_POINTER_ACTION,
        authority_class: decision.authority_class,
        boundary_reason_id: decision.reason_id,
        derived_authorization_class: authorization_class,
        delegable: false,
      });
  }
  // A system-authority action is only ever allowed as RETAINED authority; S01
  // refuses to delegate or redecide the class at all. Asserting the grant kind
  // means this rail does not depend on that staying true silently.
  if (decision.grant_kind !== "retained_system_authority" ||
      decision.authority_class !== "system_authority" ||
      !isKnownPartner(actor.slug) || actor.human !== true ||
      authorization_class !== "verified_partner" ||
      actor.slug !== V5_SYSTEM_AUTHORITY_PARTNER) {
    refuse("role_current_pointer_authority_refused",
      "a role's current pointer moves only under retained, non-delegable system authority held by the system-authority partner",
      {
        action: MODEL_ROLE_CURRENT_POINTER_ACTION,
        boundary_reason_id: decision.reason_id,
        grant_kind: decision.grant_kind ?? null,
        derived_authorization_class: authorization_class,
        delegable: false,
      });
  }
  return deepFreeze({
    partner_slug: actor.slug,
    authority_class: decision.authority_class,
    grant_kind: decision.grant_kind,
    boundary_reason_id: decision.reason_id,
    authority_source: "global-boundaries.v5.evaluateActorAuthority",
    action: MODEL_ROLE_CURRENT_POINTER_ACTION,
    delegable: false,
    permanent_privilege_granted: false,
    principal_binding: MODEL_ROLE_PRINCIPAL_BINDING_SCOPE,
  });
}

// ---------------------------------------------------------------------------
// The gap this rail fails closed on, stated once and quoted in its refusal.
// ---------------------------------------------------------------------------

/**
 * Occupancy. A role is durable; an occupant is replaceable — and this store
 * binds NEITHER of them to the other.
 *
 * This is a description of a MISSING PRODUCER, not a policy. It evaluates
 * nothing, permits nothing and competes with nothing: the routing kernel
 * remains the only place a route is qualified, and it already reports that no
 * live producer of `route-qualification.v1` exists in this repository.
 */
export const MODEL_ROLE_OCCUPANCY_INTEGRATION_REQUIREMENT = deepFreeze({
  binding_ref: "binding:model-role-occupant-qualification",
  resolved: false,
  why_unresolved: [
    "Recording that a model occupies a durable role would be a claim that the occupant is qualified for it. Qualification is a MEASUREMENT, and model-routing.v5.js states that this repository holds no live producer of route-qualification.v1: every such record reaching it today is a fixture.",
    "This store therefore has no occupant column, no occupant argument and no occupant verb. An occupancy row written from an unmeasured assertion would be exactly the caller boolean the routing kernel's closed qualification schema refuses by name.",
    "assignOccupant in model-routing.v5.js remains the only place an occupant is named, it returns the role digest UNCHANGED, and it grants the occupant nothing. Nothing here persists its result.",
  ],
  required_to_resolve: [
    "A live producer of measured route-qualification.v1 records, projecting exactly one current measurement per exact task-class/backend/model/version/effort route.",
    "A durable record for those measurements, which this slice does not add.",
    "Only then, an occupancy relation — and even then it records who is filling a job description, never a grant: a role confers no authority whether or not anybody occupies it.",
  ],
  explicitly_refused: [
    "an occupant supplied in a role payload",
    "a qualified/approved/verified boolean standing in for a measurement",
    "a quality floor expressed as a number invented here rather than as the kernel's named reference",
    "treating a stored role as evidence that any model may act under it",
  ],
});

/**
 * THE PRIVATE FAIL-CLOSED OCCUPANCY READER.
 *
 * Deliberately not exported, deliberately parameterless and deliberately
 * without a configuration path, for the reasons benchmark-acceptance-store.v5.js
 * gives for its own two: an exported stub is a callable claim, and a stub that
 * takes an argument is one edit away from being a configuration surface. The
 * only way to reach it is to attempt to bind an occupant, and there is no verb
 * that does — it is called from the argument sweep below so that the refusal
 * exists at the point somebody would try.
 */
function readOccupantQualificationBinding() {
  refuse("model_role_occupancy_unbound",
    "binding an occupant to a durable role would claim a measured qualification, and this repository holds no producer of one: model-routing.v5.js names the gap and this store does not fill it. A role is stored, selected and read here without any occupant at all.",
    MODEL_ROLE_OCCUPANCY_INTEGRATION_REQUIREMENT);
}

// ---------------------------------------------------------------------------
// The honest, zero-effect projection.
// ---------------------------------------------------------------------------

/**
 * What this rail stores, what it refuses to invent, and what is genuinely
 * missing. The routing kernel's gap list is re-exported from the kernel rather
 * than restated, so the qualification producer, the dispatch path and the
 * backend health source stay named in exactly one place.
 */
export function modelRoleStorePrerequisites() {
  const routing = v5ModelRoutingProjection();
  return deepFreeze({
    schema_version: MODEL_ROLE_STORE_PROJECTION_SCHEMA,
    tenant: ORGANIZATION_TENANT_ID,
    role_schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    rows_schema_version: MODEL_ROLE_REVISION_ROWS_SCHEMA,
    role_keys: [...V5_ROLE_KEYS],
    occupiable_authority_classes: [...V5_OCCUPIABLE_AUTHORITY_CLASSES],
    registered_authority_classes: [...V5_AUTHORITY_CLASSES],
    current_pointer_action: MODEL_ROLE_CURRENT_POINTER_ACTION,
    current_pointer_authority_class: "system_authority",
    current_pointer_delegable: false,
    system_authority_partner: V5_SYSTEM_AUTHORITY_PARTNER,
    principal_binding: MODEL_ROLE_PRINCIPAL_BINDING_SCOPE,
    authenticity: MODEL_ROLE_AUTHENTICITY_SCOPE,
    occupancy_binding: MODEL_ROLE_OCCUPANCY_INTEGRATION_REQUIREMENT,
    // Properties, not aspirations. Each is enforced above or in the candidate
    // SQL and is exercised by the two proof files beside them.
    second_role_contract_declared: false,
    second_quality_floor_declared: false,
    numeric_policy_default_declared: false,
    role_confers_authority: false,
    occupancy_bound: false,
    measured_qualification_bound: false,
    signed_capability_token_issued: false,
    provenance_survives_serialization: false,
    routing_kernel_provenance_scope: V5_PROVENANCE_SCOPE,
    // Integration that is NOT done by this slice, named at the point a reader
    // would otherwise assume it.
    candidate_sql_applied_as_migration: false,
    tools_registered: false,
    routing_kernel_unimplemented_dependencies: [...routing.unimplemented_dependencies],
    integration_still_open: [
      "Applying ops/model-role-store.candidate.sql as a numbered migration. It is candidate source, carries no ordinal, and is not in public.schema_migrations.",
      "Registering these verbs. modelRoleStoreTools is exported and is added to no tool index here; the current-pointer verb is humanOnly and authorityOnly, and registering one of those is a separate reviewed act.",
      "Binding the in-process actor to the database session principal. Both are server-derived and neither is a caller claim, but no record pairs them; see principal_binding.unbound_seam.",
      "Cross-language canonicalization equality. ops.model_role_preimage carries no NUMBER at all, so the JavaScript number-rendering half of the divergence cannot arise; the STRING half is asserted structurally and, where the two implementations could disagree, the write REFUSES on the digest comparison rather than storing a role whose stored digest either side would compute differently.",
    ],
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The verbs.
//
// No verb takes an actor, a partner or a tenant. Recording a revision derives
// its author from the writer context the server established; moving the current
// pointer runs on the per-partner authority connection whose session_user the
// database reads, AND is separately evaluated here through S01 over the live
// actor. A caller may name a digest, a role key and a version, and the database
// will only ever compare each against one it derived itself.
//
// EVERY WRITE RESULT REPORTS EFFECTS THE RECORD-LAYER WAY, not with the kernel's
// V5_NO_EFFECTS. That constant asserts `database_writes: 0`, which is true of a
// pure evaluator and false of a store. The pure functions above do carry it,
// because for them it is true.
// ---------------------------------------------------------------------------

const RECORD_LAYER_EFFECTS = deepFreeze({
  creates_effect: false,
  jobs: 0, capabilities: 0, execution_envelopes: 0,
  admissions: 0, schedules: 0, deployments: 0,
  clock_started: false,
  grants_authority: false,
  binds_occupant: false,
  activates_route: false,
  dispatches_model: false,
});

/**
 * The current pointer's OWN effects, which a revision's are not.
 *
 * Appending a revision adds an inert description nobody has selected. Moving
 * the pointer changes which description ops.model_role_current_revision()
 * returns, and that is a real consequence worth reporting as one. It is still
 * not an execution effect: it grants nothing, binds no occupant, qualifies no
 * model and starts no clock.
 */
const CURRENT_POINTER_EFFECTS = deepFreeze({
  ...RECORD_LAYER_EFFECTS,
  current_role_description_changed: true,
  previous_revisions_preserved: true,
  authority_class_required: "system_authority",
});

export function modelRoleStoreTools({ withEnvelope, writeEvent, ToolError }) {
  const digestSchema = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
  const toolRefuse = (error, detail) => { throw new ToolError({ error, ...detail }); };

  /** Translate a module or kernel refusal into a tool refusal without losing the code. */
  const asToolError = (error) => {
    if (error instanceof ModelRoleStoreError || error?.name === "V5RoutingError" ||
        error?.name === "V5BoundaryError") {
      toolRefuse(error.code, {
        message: error.message,
        ...(error.detail !== undefined ? { detail: error.detail } : {}),
      });
    }
    throw error;
  };

  const check = (fn) => { try { return fn(); } catch (error) { return asToolError(error); } };

  /**
   * The argument sweep every verb runs first.
   *
   * The occupancy reader is called only when a caller actually reaches for one,
   * so the fail-closed gap has a refusal at the point somebody hits it rather
   * than being a comment nobody meets. The name sweep would already refuse
   * `occupant`; this makes the reason the one a reader needs.
   */
  const sweepArguments = (args, path) => {
    const hit = findSelfAssertedKey(args ?? {}, path);
    if (!hit) return;
    if (OCCUPANCY_FRAGMENTS.includes(hit.fragment)) readOccupantQualificationBinding();
    assertNoSelfAssertedAuthority(args ?? {}, path);
  };

  /**
   * The record layer's instant. Read from the database rather than from this
   * process, because the custody and write times on every row below are the
   * database's and an authority evaluation made against a different clock is an
   * evaluation about a different moment.
   */
  const readServerInstant = async (c) => {
    const now = (await c.query("select ops.model_role_server_instant() as now")).rows[0]?.now;
    if (typeof now !== "string" || !ISO_INSTANT.test(now)) {
      toolRefuse("server_instant_unavailable",
        { message: "the record layer did not return a readable instant; the authority evaluation is not made against this module's clock" });
    }
    return now;
  };

  /**
   * Revalidate one revision the record layer returned, HERE, through the
   * kernel. A read that trusted the stored digest would be reporting a column
   * rather than the bytes.
   */
  const revalidate = (entry, path) => {
    if (!isPlainObject(entry) || entry.schema_version !== MODEL_ROLE_REVISION_ENTRY_SCHEMA) {
      toolRefuse("model_role_revision_entry_schema_invalid",
        { path, expected: MODEL_ROLE_REVISION_ENTRY_SCHEMA, actual: entry?.schema_version ?? null });
    }
    const sealed = check(() => modelRoleFromPreimage(entry.preimage, `${path}.preimage`));
    if (entry.role_digest !== sealed.role_digest) {
      toolRefuse("model_role_readback_digest_mismatch",
        { path, expected: sealed.role_digest, stored: entry.role_digest });
    }
    // The record layer recomputes the digest from the same rows on its own
    // side. Disagreement between the two recomputations is a corruption the
    // read must not paper over.
    if (entry.recomputed_role_digest !== sealed.role_digest) {
      toolRefuse("model_role_stored_digest_drift",
        { path, expected: sealed.role_digest, recomputed_by_record_layer: entry.recomputed_role_digest ?? null });
    }
    if (entry.structure_error) {
      toolRefuse("model_role_revision_incomplete", { path, detail: entry.structure_error });
    }
    return {
      ...entry,
      role: sealed,
      revalidated_through_define_role: true,
      authenticity: MODEL_ROLE_AUTHENTICITY_SCOPE,
    };
  };

  const revalidateReadback = (readback) => {
    if (!isPlainObject(readback)) {
      toolRefuse("model_role_readback_unavailable", { message: "the record layer returned no readback" });
    }
    // The readback names the contract it was built under. Without this, a
    // payload from some other reader could satisfy every digest check below,
    // because those only ever ask whether the bytes hash to their own claim.
    if (readback.schema_version !== MODEL_ROLE_READBACK_SCHEMA) {
      toolRefuse("model_role_readback_schema_invalid",
        { expected: MODEL_ROLE_READBACK_SCHEMA, actual: readback.schema_version ?? null });
    }
    const history = Array.isArray(readback.history) ? readback.history : [];
    return {
      ...readback,
      history: history.map((entry, index) => revalidate(entry, `readback.history[${index}]`)),
      current: readback.current === null || readback.current === undefined
        ? null : revalidate(readback.current, "readback.current"),
    };
  };

  return {
    "read-model-role": {
      write: false,
      description: "Read one DoctorCRE v5 durable role description: its append-only revision history, which revision is current, and — for every revision returned — the role rebuilt from the persisted rows and independently revalidated here through model-routing.v5.js's defineRole, with the digest recomputed on both sides. Reports the authenticity this buys and its limits precisely: it is re-derivation from committed rows, not a signature, not a capability token, not the routing kernel's process-local provenance, and not evidence that any model is qualified for the role or occupies it. A role nobody has recorded reads back as absent; none is invented, seeded or defaulted.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { role_key: { type: "string" } }, required: ["role_key"],
      },
      handler: async (c, _actor, args) => {
        check(() => {
          sweepArguments(args, "args");
          assertRoleKey(args.role_key, "role_key");
        });
        const readback = (await c.query("select ops.model_role_readback($1::text) as readback",
          [args.role_key])).rows[0]?.readback;
        if (!readback) toolRefuse("model_role_readback_unavailable", { role_key: args.role_key });
        return {
          ok: true,
          ...revalidateReadback(readback),
          prerequisites: modelRoleStorePrerequisites(),
        };
      },
    },

    "record-model-role-revision": {
      write: true,
      description: "Append one immutable revision of a durable DoctorCRE v5 role description — skills, rules, declared authority class and capability references, evidence requirements, named quality floors, minimum-strength reference and task classes — stored as typed ordered rows rather than as a blob. The role contract is model-routing.v5.js's defineRole and is not restated: a role declaring system authority, naming no quality floor or no task class, or using an unsettled role key is refused by the kernel. THE REVISION IS INERT: it selects nothing, grants nothing, binds no occupant, qualifies no model and does not become current. Its author is the authenticated writer, never a field in this payload; the supplied digest is compared against one this module computes and again against one the database recomputes from the stored rows before the transaction may commit; and a revision whose content repeats an existing revision of the same role is refused rather than appended as history that records no change.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          role_key: { type: "string" },
          revision_no: { type: "integer", minimum: 1 },
          role_digest: digestSchema,
          role: { type: "object" },
        },
        required: ["idempotency_key", "role_key", "revision_no", "role_digest", "role"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-model-role-revision", args, async () => {
        check(() => {
          sweepArguments(args, "args");
          assertUuid(args.idempotency_key, "idempotency_key");
          assertRoleKey(args.role_key, "role_key");
          assertRevisionNo(args.revision_no, "revision_no");
          assertDigestRef(args.role_digest, "role_digest");
        });

        // Validated and decomposed in the module first, so a malformed role is
        // refused with a named contract clause from the kernel rather than with
        // a database constraint message.
        const rows = check(() => modelRoleRevisionRows(args.role));

        // THE CALLER'S HASH IS NEVER THE ANSWER. It is compared here against
        // the digest the kernel computes, and again in the database against the
        // digest the stored rows produce. A caller who believed they held one
        // role description is refused rather than silently storing another.
        if (rows.role_digest !== args.role_digest) {
          toolRefuse("model_role_digest_mismatch",
            { expected: rows.role_digest, supplied: args.role_digest });
        }
        if (rows.role_key !== args.role_key) {
          toolRefuse("model_role_key_mismatch",
            { expected: rows.role_key, supplied: args.role_key });
        }
        // The round trip, proved on the way in rather than assumed: if these
        // rows cannot rebuild the role that produced them, they are not a
        // faithful decomposition and nothing should be stored from them.
        const roundTripped = check(() => modelRoleFromRows(rows));
        if (roundTripped.role_digest !== rows.role_digest) {
          toolRefuse("model_role_digest_mismatch",
            { expected: rows.role_digest, round_tripped: roundTripped.role_digest });
        }

        const revisionId = (await c.query(
          `select ops.model_role_record_revision($1::text,$2::integer,$3::uuid,$4::text,
             $5::jsonb,$6::jsonb,$7::jsonb) as id`,
          [args.role_key, args.revision_no, args.idempotency_key, rows.role_digest,
            JSON.stringify(rows.scalars), JSON.stringify(rows.texts),
            JSON.stringify(rows.refs)])).rows[0].id;

        // READBACK REVALIDATION, through defineRole, over the bytes the record
        // layer rebuilt from the committed rows.
        const stored = (await c.query("select ops.model_role_revision_readback($1::uuid) as revision",
          [revisionId])).rows[0]?.revision;
        if (!stored) toolRefuse("model_role_readback_unavailable", { revision_id: revisionId });
        const revalidated = revalidate(stored, "revision");
        if (revalidated.role_digest !== rows.role_digest) {
          toolRefuse("model_role_digest_mismatch",
            { expected: rows.role_digest, stored: revalidated.role_digest });
        }

        await writeEvent(c, {
          subject_type: "model_role", subject_id: revisionId,
          verb: "record-model-role-revision",
          payload: { role_key: args.role_key, revision_no: args.revision_no, role_digest: rows.role_digest },
        });

        return {
          ok: true,
          revision_id: revisionId,
          role_key: args.role_key,
          revision_no: args.revision_no,
          role_digest: rows.role_digest,
          revision: revalidated,
          is_current: false,
          // Said on the result because these are the Q106 properties a reader
          // is checking, and they are true of every revision this verb writes.
          occupant_bound: false,
          confers_authority: false,
          measured_qualification_bound: false,
          authenticity: MODEL_ROLE_AUTHENTICITY_SCOPE,
          prerequisites: modelRoleStorePrerequisites(),
          effects: RECORD_LAYER_EFFECTS,
        };
      }),
    },

    "set-current-model-role-revision": {
      write: true, humanOnly: true, authorityOnly: true,
      description: "HUMAN-ONLY: name which recorded revision of a durable DoctorCRE v5 role description is CURRENT. This is the only act in this rail that needs authority, and it is evaluated through global-boundaries.v5.js's evaluateActorAuthority under the system.design action — authority class system_authority, which S01 retains for the system-authority partner and makes non-delegable; a writer connection cannot reach this verb at all. The move is a compare-and-swap: expected_current_revision_no must be stated explicitly on every call, and must be null exactly when the caller asserts there is no current pointer yet. A stale expectation, a creation against an existing pointer, a missing revision, a revision of another role, a revision whose stored content no longer rebuilds its own digest, or a re-point at the revision that is already current are each refused by name. Earlier revisions are preserved: the pointer is an append-only ledger, so the history of what was current is itself history. It grants no authority, binds no occupant, qualifies no model, activates no route and dispatches nothing.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          role_key: { type: "string" },
          revision_no: { type: "integer", minimum: 1 },
          role_digest: digestSchema,
          // REQUIRED, and explicitly nullable. An absent key would make "I did
          // not think about it" indistinguishable from "I assert this role has
          // no current pointer", which is the compare-and-swap failing open on
          // the one case it exists to guard.
          expected_current_revision_no: { type: ["integer", "null"], minimum: 1 },
        },
        required: ["idempotency_key", "role_key", "revision_no", "role_digest",
          "expected_current_revision_no"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-current-model-role-revision", args, async () => {
        check(() => {
          sweepArguments(args, "args");
          assertUuid(args.idempotency_key, "idempotency_key");
          assertRoleKey(args.role_key, "role_key");
          assertRevisionNo(args.revision_no, "revision_no");
          assertDigestRef(args.role_digest, "role_digest");
          if (!hasOwn(args, "expected_current_revision_no")) {
            refuse("model_role_expected_revision_required",
              "state the expected current revision explicitly: an integer to compare against, or null to assert this role has no current pointer yet",
              { path: "expected_current_revision_no" });
          }
          if (args.expected_current_revision_no !== null) {
            assertRevisionNo(args.expected_current_revision_no, "expected_current_revision_no");
          }
        });

        // The instant is the record layer's. One read-only statement precedes
        // the authority derivation for that reason; no writer function is
        // reached before S01 has answered.
        const now = await readServerInstant(c);
        const authority = check(() => deriveRoleCurrentPointerAuthority({ actor, now }));

        // The creation assertion travels as its OWN parameter rather than as a
        // null the database has to interpret. A null that means "create" and a
        // null that means "nobody filled this in" look identical in SQL, and
        // the difference is the whole compare-and-swap.
        const expectCreation = args.expected_current_revision_no === null;

        const pointerId = (await c.query(
          `select ops.model_role_set_current_revision($1::text,$2::uuid,$3::integer,$4::text,
             $5::boolean,$6::integer) as id`,
          [args.role_key, args.idempotency_key, args.revision_no, args.role_digest,
            expectCreation, args.expected_current_revision_no])).rows[0].id;

        const readback = (await c.query("select ops.model_role_readback($1::text) as readback",
          [args.role_key])).rows[0]?.readback;
        if (!readback) toolRefuse("model_role_readback_unavailable", { role_key: args.role_key });
        const revalidated = revalidateReadback(readback);
        if (revalidated.current?.revision_no !== args.revision_no ||
            revalidated.current?.role_digest !== args.role_digest) {
          toolRefuse("model_role_current_pointer_drift", {
            expected_revision_no: args.revision_no, expected_role_digest: args.role_digest,
            observed_revision_no: revalidated.current?.revision_no ?? null,
            observed_role_digest: revalidated.current?.role_digest ?? null,
          });
        }

        await writeEvent(c, {
          subject_type: "model_role", subject_id: pointerId,
          verb: "set-current-model-role-revision",
          payload: {
            role_key: args.role_key, revision_no: args.revision_no,
            role_digest: args.role_digest,
            expected_current_revision_no: args.expected_current_revision_no,
          },
        });

        return {
          ok: true,
          pointer_id: pointerId,
          role_key: args.role_key,
          revision_no: args.revision_no,
          role_digest: args.role_digest,
          expected_current_revision_no: args.expected_current_revision_no,
          // The in-process derivation, reported beside — never instead of — the
          // record layer's own. Both are on the result so a reader can see that
          // they are two statements and that nothing pairs them.
          authority: authority,
          record_layer_principal: revalidated.current?.set_by_partner_slug ?? null,
          principal_binding: MODEL_ROLE_PRINCIPAL_BINDING_SCOPE,
          current: revalidated.current,
          history_preserved: true,
          occupant_bound: false,
          confers_authority: false,
          measured_qualification_bound: false,
          authenticity: MODEL_ROLE_AUTHENTICITY_SCOPE,
          prerequisites: modelRoleStorePrerequisites(),
          effects: CURRENT_POINTER_EFFECTS,
        };
      }),
    },
  };
}
