// DoctorCRE v5 slice V5-F05, half two: the Context Assembler, read-only
// exploration, taint lineage and the typed correction proposal.
//
// Three settled decisions live here — Q050 (a reproducible task-specific
// manifest), Q065 (graduated behaviour under uncertainty) and Q068 (external
// content is data, never instruction). The other four (Q051, Q064, Q066, Q087)
// live in rule-applicability.v5.js, which this file imports for the rule
// universe, the coverage receipt and the shared input guards.
//
// THREE THINGS THIS MODULE DELIBERATELY DOES NOT DO ITS OWN WAY:
//   * AUTHORITY is not accepted, it is COMPUTED. The manifest calls S01's
//     evaluateActorAuthority with the actor, the boundary action and the
//     controls, and records the answer. There is no field a caller can set to
//     say it is authorized.
//   * TAINT uses F01's vocabulary (V5_F01_TAINT_CLASSES) rather than a second
//     one. The two slices label different things and both labels are true at
//     once: F01 says a Salesforce field may be the corporate SOURCE OF RECORD
//     for its value; Q068 says the same field's TEXT is untrusted and cannot
//     instruct anybody. Authority over a value is not trust in a sentence.
//   * HASHING and canonicalization come from artifact-trust.js.
//
// THE FROZEN-INPUT RULE, and it is the reason assembleContextManifest does not
// take an ordinary object. A manifest is a claim about exactly one set of
// inputs. If the assembler read a live object, the object could differ between
// the read that validated it and the read that built the manifest, and the
// digest would bind bytes nobody ever evaluated. So the caller freezes first:
// freezeAssemblyInput canonicalizes a snapshot into BYTES, and the assembler
// parses and validates those bytes. The manifest carries their digest, and
// authenticateRuntimeProjection re-derives the whole manifest from the same
// bytes before a verifier's attestation counts for anything. A digest on its
// own is not accepted anywhere: a hash proves two things match, never what
// either of them says.
//
// TWO PROJECTIONS AND THEY ARE NOT THE SAME THING. assembleContextManifest
// produces a REPRODUCIBLE PROPOSAL — pure, replayable by anyone holding the
// bytes, and evidence of nothing about the running system.
// authenticateRuntimeProjection produces an AUTHENTICATED RUNTIME PROJECTION,
// and only against a verifier attestation that binds the exact input bytes and
// the exact manifest digest. Nothing here authenticates anything by itself.
//
// The module is pure: no filesystem, no network, no database, no provider, no
// scheduler, no environment and no clock. It writes no record, attributes
// nothing to anybody, declassifies nothing, and registers no service.
// `V5_NO_EFFECTS` rides on every result to say so.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_NO_EFFECTS,
  V5_ACTION_KEYS,
  evaluateActorAuthority,
} from "./global-boundaries.v5.js";
import { V5_F01_TAINT_CLASSES } from "./record-source-authority.v5.js";
import {
  V5F05Error,
  V5_F05_GUARDS,
  V5_F05_SETTLED_DECISIONS,
  V5_F05_SETTLED_DECISION_IDS,
  V5_F05_FACT_DIMENSIONS,
  V5_F05_POLICY_VERSION,
  assertF05DecisionBinding,
  deriveRuleApplicability,
  requireCompiledUniverse,
  v5F05DecisionSubsetDigest,
  v5F05RuleKernelDigest,
} from "./rule-applicability.v5.js";

export { V5_NO_EFFECTS, V5F05Error, assertF05DecisionBinding };

const {
  fail, deepFreeze, snapshot,
  assertObject, assertArray, assertBoolean, assertSafeInteger,
  assertClosedKeys, assertRequiredKeys, assertNoAccessorsOrHiddenKeys,
  assertSafeText, assertExternalIdent, assertEnum, assertInstant,
  assertDigestRef, assertTenant,
} = V5_F05_GUARDS;

export const V5_F05_MANIFEST_SCHEMA_VERSION = "doctorcre-v5-f05-context-manifest.v1";
export const V5_F05_FROZEN_INPUT_SCHEMA_VERSION = "doctorcre-v5-f05-frozen-assembly-input.v1";
export const V5_F05_ATTESTATION_SCHEMA_VERSION = "doctorcre-v5-f05-verifier-attestation.v1";
export const V5_F05_CORRECTION_SCHEMA_VERSION = "doctorcre-v5-f05-correction-proposal.v1";
export const V5_F05_LINEAGE_SCHEMA_VERSION = "doctorcre-v5-f05-taint-lineage.v1";

export const V5_F05_MANIFEST_VERSION = 1;

/** A verifier attestation older than this describes a system that has moved on. */
export const V5_F05_MAX_ATTESTATION_AGE_SECONDS = 900;

// ---------------------------------------------------------------------------
// Q068 — origins, derivation and taint.
//
// The six origins Q068 names are external, and so is everything derived from
// them. A summary of an email is not a cleaner email; an embedding of an MLS
// remark is not a cleaner remark. Both stay tainted through the lineage, which
// is why derivation is a GRAPH here rather than a flag: the interesting case is
// the third-generation extract whose own origin field says "record_layer".
// ---------------------------------------------------------------------------

export const V5_F05_EXTERNAL_ORIGINS = deepFreeze([
  "document", "email", "mls", "salesforce", "upload", "web",
]);
export const V5_F05_ORIGINS = deepFreeze([
  "record_layer", ...V5_F05_EXTERNAL_ORIGINS,
]);
export const V5_F05_DERIVED_KINDS = deepFreeze([
  "primary", "extract", "summary", "embedding", "translation",
]);

/** F01's vocabulary, reused rather than reinvented. */
export const V5_F05_TAINT_CLASSES = V5_F01_TAINT_CLASSES;
export const V5_F05_TAINTED_CLASSES = deepFreeze(["untrusted_external", "untrusted_parsed"]);

export const V5_F05_RECORD_KINDS = deepFreeze([
  "rule", "authority_grant", "decision", "operating_fact",
  "document", "message", "listing", "opportunity", "note", "summary", "embedding",
]);
/** Kinds that carry authority. Tainted content may never wear one of these. */
export const V5_F05_AUTHORITY_BEARING_RECORD_KINDS = deepFreeze([
  "rule", "authority_grant", "decision",
]);

export const V5_F05_UNTRUSTED_FORBIDDEN_USES = deepFreeze([
  "authority_grant", "declassification", "policy_change", "recipient_change",
  "rule_authorship", "secret_request", "tool_invocation",
]);
export const V5_F05_UNTRUSTED_PERMITTED_USES = deepFreeze([
  "analysis_input", "quoted_evidence", "retrieval_candidate", "summarization",
]);
export const V5_F05_INTENDED_USES = deepFreeze([
  ...V5_F05_UNTRUSTED_FORBIDDEN_USES, ...V5_F05_UNTRUSTED_PERMITTED_USES,
].sort());

export const V5_F05_SOURCE_STATES = deepFreeze(["available", "unavailable", "conflicting"]);

export const V5_F05_MODES = deepFreeze([
  "read_only_exploration", "consequential_action_proposal",
]);

const PROVENANCE_KEYS = Object.freeze(["source_id", "retrieval_class", "evidence_ref"]);
const RECORD_KEYS = Object.freeze([
  "record_id", "record_kind", "version", "content_digest", "origin", "derived_kind",
  "derived_from", "query_id", "observed_at", "max_age_seconds", "estimated_tokens",
  "omissible", "backs_control", "provenance",
]);
const RECORD_REQUIRED = Object.freeze([
  "record_id", "record_kind", "version", "content_digest", "origin", "derived_kind",
  "query_id", "observed_at", "estimated_tokens", "provenance",
]);

function compileProvenance(raw, path) {
  assertObject(raw, path);
  assertClosedKeys(raw, PROVENANCE_KEYS, path);
  assertRequiredKeys(raw, ["source_id", "retrieval_class"], path);
  return {
    source_id: assertExternalIdent(raw.source_id, `${path}.source_id`, { maxLength: 128 }),
    retrieval_class: assertExternalIdent(raw.retrieval_class, `${path}.retrieval_class`,
      { maxLength: 128 }),
    evidence_ref: raw.evidence_ref === undefined || raw.evidence_ref === null
      ? null : assertExternalIdent(raw.evidence_ref, `${path}.evidence_ref`),
  };
}

function compileRecord(raw, index, seen) {
  const path = `request.records[${index}]`;
  assertObject(raw, path);
  assertClosedKeys(raw, RECORD_KEYS, path);
  assertRequiredKeys(raw, RECORD_REQUIRED, path);
  const record_id = assertExternalIdent(raw.record_id, `${path}.record_id`, { maxLength: 128 });
  if (seen.has(record_id)) {
    fail("duplicate_record", `${path} repeats record "${record_id}"`,
      { path, record_id, first_index: seen.get(record_id) });
  }
  seen.set(record_id, index);

  const derived_from = [];
  if (raw.derived_from !== undefined && raw.derived_from !== null) {
    assertArray(raw.derived_from, `${path}.derived_from`, { min: 0, max: 32 });
    const parents = new Set();
    raw.derived_from.forEach((parent, i) => {
      const value = assertExternalIdent(parent, `${path}.derived_from[${i}]`, { maxLength: 128 });
      if (value === record_id) {
        fail("taint_lineage_self_reference", `${path}.derived_from names its own record`, { path, record_id });
      }
      if (parents.has(value)) {
        fail("duplicate_lineage_parent", `${path}.derived_from repeats "${value}"`, { path, value });
      }
      parents.add(value);
      derived_from.push(value);
    });
    derived_from.sort();
  }

  assertInstant(raw.observed_at, `${path}.observed_at`);
  return {
    record_id,
    record_kind: assertEnum(raw.record_kind, V5_F05_RECORD_KINDS, `${path}.record_kind`,
      "unknown_record_kind"),
    version: assertSafeInteger(raw.version, `${path}.version`, { min: 1 }),
    content_digest: assertDigestRef(raw.content_digest, `${path}.content_digest`),
    origin: assertEnum(raw.origin, V5_F05_ORIGINS, `${path}.origin`, "unknown_origin"),
    derived_kind: assertEnum(raw.derived_kind, V5_F05_DERIVED_KINDS, `${path}.derived_kind`,
      "unknown_derived_kind"),
    derived_from,
    query_id: assertExternalIdent(raw.query_id, `${path}.query_id`, { maxLength: 128 }),
    observed_at: raw.observed_at,
    max_age_seconds: raw.max_age_seconds === undefined || raw.max_age_seconds === null
      ? null : assertSafeInteger(raw.max_age_seconds, `${path}.max_age_seconds`, { min: 0 }),
    estimated_tokens: assertSafeInteger(raw.estimated_tokens, `${path}.estimated_tokens`,
      { min: 0, max: 10_000_000 }),
    omissible: raw.omissible === undefined || raw.omissible === null
      ? false : assertBoolean(raw.omissible, `${path}.omissible`),
    backs_control: raw.backs_control === undefined || raw.backs_control === null
      ? false : assertBoolean(raw.backs_control, `${path}.backs_control`),
    provenance: compileProvenance(raw.provenance, `${path}.provenance`),
  };
}

/**
 * Propagate taint across the derivation graph.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. Every named parent must exist. A dangling parent is refused rather than
 *      skipped, because a skipped parent is exactly how a derived record loses
 *      the taint of the thing it was derived from.
 *   2. The graph must be acyclic. A cycle would let a fixed point settle on
 *      "clean" for a pair of records that only ever cite each other.
 *   3. A record is untrusted_external if its own origin is one of Q068's six.
 *      Otherwise it is untrusted_parsed if ANY ancestor is tainted. Otherwise
 *      it is first_party_record_layer.
 *
 * Nothing here can lower a taint class, and there is no argument that would
 * let it: declassification is not a parameter of this function.
 *
 * The records are re-validated here rather than trusted, so the function is
 * usable on its own and cannot be handed a hand-built record that skipped
 * compileRecord's checks. Re-validating an already-compiled record is a no-op.
 */
export function compileTaintLineage(rawRecords) {
  assertArray(rawRecords, "records", { min: 0, max: 1024 });
  const seen = new Map();
  const records = rawRecords
    .map((raw, index) => compileRecord(raw, index, seen))
    .sort((a, b) => (a.record_id < b.record_id ? -1 : 1));
  const byId = new Map(records.map(record => [record.record_id, record]));
  for (const record of records) {
    for (const parent of record.derived_from) {
      if (!byId.has(parent)) {
        fail("taint_lineage_dangling_parent",
          `record "${record.record_id}" is derived from "${parent}", which the manifest does not carry`,
          { record_id: record.record_id, parent });
      }
    }
  }
  const WHITE = 0, GREY = 1, BLACK = 2;
  const colour = new Map(records.map(record => [record.record_id, WHITE]));
  const order = [];
  for (const root of records) {
    if (colour.get(root.record_id) !== WHITE) continue;
    const stack = [{ id: root.record_id, index: 0, path: [root.record_id] }];
    colour.set(root.record_id, GREY);
    while (stack.length > 0) {
      const frame = stack[stack.length - 1];
      const parents = byId.get(frame.id).derived_from;
      if (frame.index >= parents.length) {
        colour.set(frame.id, BLACK);
        order.push(frame.id);
        stack.pop();
        continue;
      }
      const next = parents[frame.index++];
      const state = colour.get(next);
      if (state === GREY) {
        fail("taint_lineage_cycle",
          `the derivation graph contains a cycle through "${next}"`, { cycle: [...frame.path, next] });
      }
      if (state === WHITE) {
        colour.set(next, GREY);
        stack.push({ id: next, index: 0, path: [...frame.path, next] });
      }
    }
  }

  // Parents before children, so an ancestor's class is final when it is read.
  const labelled = new Map();
  for (const record_id of order) {
    const record = byId.get(record_id);
    const tainted_ancestors = record.derived_from
      .filter(parent => labelled.get(parent).tainted)
      .sort();
    let taint_class;
    let reason_id;
    if (V5_F05_EXTERNAL_ORIGINS.includes(record.origin)) {
      taint_class = "untrusted_external";
      reason_id = "external_origin";
    } else if (tainted_ancestors.length > 0) {
      taint_class = "untrusted_parsed";
      reason_id = "derived_from_untrusted_content";
    } else {
      taint_class = "first_party_record_layer";
      reason_id = "first_party_origin";
    }
    labelled.set(record_id, {
      record_id, origin: record.origin, derived_kind: record.derived_kind,
      derived_from: [...record.derived_from],
      taint_class, tainted: V5_F05_TAINTED_CLASSES.includes(taint_class),
      reason_id, tainted_ancestors,
      may_instruct: false, treated_as: "data",
    });
  }
  const entries = records
    .map(record => labelled.get(record.record_id))
    .sort((a, b) => (a.record_id < b.record_id ? -1 : 1));
  const lineage = {
    schema_version: V5_F05_LINEAGE_SCHEMA_VERSION,
    entries,
    tainted_record_ids: entries.filter(e => e.tainted).map(e => e.record_id),
    declassification_supported: false,
  };
  return deepFreeze({
    ...lineage,
    lineage_digest: digest(lineage),
    effects: V5_NO_EFFECTS,
  });
}

const UNTRUSTED_USE_KEYS = Object.freeze(["lineage", "record_id", "intended_use"]);

/**
 * May this record be used this way? Tainted content informs analysis and may
 * be quoted as evidence; it cannot become a rule, an authority, a tool call, a
 * recipient change or a secret request, and content that LOOKS like an
 * instruction is still content — the label is carried by the lineage, never
 * inferred from what the text says.
 */
export function evaluateUntrustedUse(request) {
  assertObject(request, "request");
  assertClosedKeys(request, UNTRUSTED_USE_KEYS, "request");
  assertRequiredKeys(request, UNTRUSTED_USE_KEYS, "request");
  assertObject(request.lineage, "request.lineage");
  if (request.lineage.schema_version !== V5_F05_LINEAGE_SCHEMA_VERSION) {
    fail("lineage_not_compiled", "request.lineage must be the output of compileTaintLineage",
      { path: "request.lineage" });
  }
  // Recomputed rather than trusted: a hand-built lineage relabelling an email
  // as first-party would otherwise decide this call.
  assertDigestRef(request.lineage.lineage_digest, "request.lineage.lineage_digest");
  const { lineage_digest, effects: _ignored, ...lineageBody } = request.lineage;
  if (digest(lineageBody) !== lineage_digest) {
    fail("lineage_digest_mismatch",
      "the lineage no longer hashes to its own digest; it was edited after compilation",
      { expected: lineage_digest, actual: digest(lineageBody) });
  }
  const record_id = assertExternalIdent(request.record_id, "request.record_id", { maxLength: 128 });
  const intended_use = assertEnum(request.intended_use, V5_F05_INTENDED_USES,
    "request.intended_use", "unknown_intended_use");
  const entry = request.lineage.entries.find(item => item.record_id === record_id);
  if (entry === undefined) {
    fail("unknown_record", `"${record_id}" is not in this lineage`, { record_id });
  }
  const base = {
    record_id, intended_use, taint_class: entry.taint_class, tainted: entry.tainted,
    tainted_ancestors: [...entry.tainted_ancestors],
    content_that_looks_like_an_instruction_is_still_data: true,
    effects: V5_NO_EFFECTS,
  };
  if (entry.tainted && V5_F05_UNTRUSTED_FORBIDDEN_USES.includes(intended_use)) {
    return deepFreeze({ decision: "refuse", reason_id: "untrusted_content_cannot_confer_authority", ...base });
  }
  if (intended_use === "declassification") {
    return deepFreeze({ decision: "refuse", reason_id: "declassification_not_supported", ...base });
  }
  return deepFreeze({
    decision: "allow",
    reason_id: entry.tainted ? "untrusted_content_as_data" : "first_party_content",
    ...base,
  });
}

// ---------------------------------------------------------------------------
// Q050 — the frozen input and the manifest.
// ---------------------------------------------------------------------------

const REQUEST_KEYS = Object.freeze([
  "schema_version", "tenant", "now", "mode", "actor", "task", "controls",
  "universe", "records", "sources", "queries", "semantic_candidates", "budget",
]);
const REQUEST_REQUIRED = Object.freeze([
  "schema_version", "tenant", "now", "mode", "actor", "task", "universe",
  "records", "sources", "queries",
]);
const TASK_KEYS = Object.freeze(["task_id", "title", "boundary_action", "facts"]);
const QUERY_KEYS = Object.freeze(["query_id", "query_kind", "parameters_digest", "retrieved_at"]);
const SOURCE_KEYS = Object.freeze(["source_id", "state", "required_for_task", "note"]);
const BUDGET_KEYS = Object.freeze(["token_budget"]);
const FROZEN_KEYS = Object.freeze(["frozen", "schema_version", "input_bytes", "input_digest"]);

/**
 * Canonicalize a request into the exact bytes a manifest will be bound to.
 *
 * The snapshot walk runs first, so an accessor or a mutated-later object cannot
 * reach the bytes. What comes back is the caller's evidence: the bytes and
 * their digest, and nothing derived from anything else.
 */
export function freezeAssemblyInput(request) {
  const frozen = snapshot(request, "request");
  const input_bytes = canonicalJson(frozen);
  return deepFreeze({
    frozen: true,
    schema_version: V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
    input_bytes,
    input_digest: digest(input_bytes),
    effects: V5_NO_EFFECTS,
  });
}

function readFrozenInput(frozen) {
  assertObject(frozen, "frozen");
  // Checked BEFORE the closed-key sweep, so handing the assembler an ordinary
  // request object reports the actual mistake rather than the first field of
  // that request that happens not to be a frozen-envelope key.
  if (frozen.frozen !== true || frozen.schema_version !== V5_F05_FROZEN_INPUT_SCHEMA_VERSION) {
    fail("input_not_frozen",
      "the assembler reads frozen bytes only; call freezeAssemblyInput first so the manifest binds one exact input",
      { path: "frozen" });
  }
  assertClosedKeys(frozen, [...FROZEN_KEYS, "effects"], "frozen");
  assertRequiredKeys(frozen, FROZEN_KEYS, "frozen");
  if (typeof frozen.input_bytes !== "string" || frozen.input_bytes.length === 0) {
    fail("invalid_shape", "frozen.input_bytes must be a non-empty string", { path: "frozen.input_bytes" });
  }
  assertDigestRef(frozen.input_digest, "frozen.input_digest");
  const recomputed = digest(frozen.input_bytes);
  if (recomputed !== frozen.input_digest) {
    fail("frozen_input_digest_mismatch",
      "the frozen bytes do not hash to their own digest; they were edited after freezing",
      { expected: frozen.input_digest, actual: recomputed });
  }
  let parsed;
  try {
    parsed = JSON.parse(frozen.input_bytes);
  } catch (error) {
    fail("frozen_input_unreadable", `the frozen bytes are not readable JSON: ${error.message}`, {});
  }
  return { request: parsed, input_bytes: frozen.input_bytes, input_digest: frozen.input_digest };
}

function compileQueries(raw) {
  assertArray(raw, "request.queries", { min: 1, max: 128 });
  const seen = new Set();
  return raw.map((entry, index) => {
    const path = `request.queries[${index}]`;
    assertObject(entry, path);
    assertClosedKeys(entry, QUERY_KEYS, path);
    assertRequiredKeys(entry, QUERY_KEYS, path);
    const query_id = assertExternalIdent(entry.query_id, `${path}.query_id`, { maxLength: 128 });
    if (seen.has(query_id)) fail("duplicate_query", `${path} repeats query "${query_id}"`, { path, query_id });
    seen.add(query_id);
    assertInstant(entry.retrieved_at, `${path}.retrieved_at`);
    return {
      query_id,
      query_kind: assertExternalIdent(entry.query_kind, `${path}.query_kind`, { maxLength: 128 }),
      // The parameters themselves may carry client data; the digest is what
      // makes the query REPRODUCIBLE without copying its inputs into a manifest.
      parameters_digest: assertDigestRef(entry.parameters_digest, `${path}.parameters_digest`),
      retrieved_at: entry.retrieved_at,
    };
  }).sort((a, b) => (a.query_id < b.query_id ? -1 : 1));
}

function compileSources(raw) {
  assertArray(raw, "request.sources", { min: 0, max: 128 });
  const seen = new Set();
  return raw.map((entry, index) => {
    const path = `request.sources[${index}]`;
    assertObject(entry, path);
    assertClosedKeys(entry, SOURCE_KEYS, path);
    assertRequiredKeys(entry, ["source_id", "state"], path);
    const source_id = assertExternalIdent(entry.source_id, `${path}.source_id`, { maxLength: 128 });
    if (seen.has(source_id)) {
      fail("duplicate_source", `${path} repeats source "${source_id}"`, { path, source_id });
    }
    seen.add(source_id);
    return {
      source_id,
      state: assertEnum(entry.state, V5_F05_SOURCE_STATES, `${path}.state`, "unknown_source_state"),
      required_for_task: entry.required_for_task === undefined || entry.required_for_task === null
        ? false : assertBoolean(entry.required_for_task, `${path}.required_for_task`),
      note: entry.note === undefined || entry.note === null
        ? null : assertSafeText(entry.note, `${path}.note`, { maxLength: 512, prose: true }),
    };
  }).sort((a, b) => (a.source_id < b.source_id ? -1 : 1));
}

function compileTask(raw) {
  assertObject(raw, "request.task");
  assertClosedKeys(raw, TASK_KEYS, "request.task");
  assertRequiredKeys(raw, ["task_id", "boundary_action", "facts"], "request.task");
  assertObject(raw.facts, "request.task.facts");
  assertClosedKeys(raw.facts, V5_F05_FACT_DIMENSIONS, "request.task.facts");
  return {
    task_id: assertExternalIdent(raw.task_id, "request.task.task_id", { maxLength: 128 }),
    title: raw.title === undefined || raw.title === null
      ? null : assertSafeText(raw.title, "request.task.title", { maxLength: 512, prose: true }),
    // The S01 action key, so the authority envelope is the one S01 computes
    // rather than a second opinion about the same question.
    boundary_action: assertEnum(raw.boundary_action, V5_ACTION_KEYS,
      "request.task.boundary_action", "unknown_boundary_action"),
    facts: raw.facts,
  };
}

function manifestPreimage(manifest) {
  const { manifest_digest, effects, ...rest } = manifest;
  return rest;
}

/**
 * Assemble one task-specific context manifest from frozen input bytes.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The frozen bytes must hash to their own digest and parse. Everything
 *      after this point reads the PARSE, never the caller's object.
 *   2. The parsed request must be readable, closed, tenant-bound and carry a
 *      compiled rule universe nobody has edited since compilation.
 *   3. The authority envelope is COMPUTED by S01 from the actor, the boundary
 *      action and the controls. An actor who is not a verified partner refuses
 *      outright: read-only exploration is a concession to UNCERTAINTY, not to
 *      an unknown principal.
 *   4. Taint lineage is compiled across every selected record. Tainted content
 *      wearing an authority-bearing record kind refuses.
 *   5. Freshness is computed per record against `now`. A stale record that
 *      backs a mandatory control is a blocking reason.
 *   6. The coverage receipt is derived from the typed facts (Q064/Q065/Q087).
 *   7. The token budget is applied LAST and may only drop guidance and records
 *      the caller marked omissible that back no control. If the budget still
 *      cannot be met, the manifest refuses rather than dropping a possible
 *      binding constraint.
 *   8. The mode decides. A consequential proposal needs everything above clean.
 *      Read-only exploration is permitted under uncertainty and says so.
 */
export function assembleContextManifest(frozen) {
  const { request, input_bytes, input_digest } = readFrozenInput(frozen);

  assertObject(request, "request");
  assertClosedKeys(request, REQUEST_KEYS, "request");
  assertRequiredKeys(request, REQUEST_REQUIRED, "request");
  if (request.schema_version !== V5_F05_MANIFEST_SCHEMA_VERSION) {
    fail("unknown_schema_version", `request.schema_version must be "${V5_F05_MANIFEST_SCHEMA_VERSION}"`,
      { expected: V5_F05_MANIFEST_SCHEMA_VERSION });
  }
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const mode = assertEnum(request.mode, V5_F05_MODES, "request.mode", "unknown_mode");
  const universe = requireCompiledUniverse(request.universe, "request.universe");
  const task = compileTask(request.task);
  const queries = compileQueries(request.queries);
  const sources = compileSources(request.sources);

  const actor = assertObject(request.actor, "request.actor");
  assertNoAccessorsOrHiddenKeys(actor, "request.actor");
  const actor_slug = typeof actor.slug === "string"
    ? assertSafeText(actor.slug, "request.actor.slug", { maxLength: 128 }) : null;

  let controls = null;
  if (request.controls !== undefined && request.controls !== null) {
    controls = assertObject(request.controls, "request.controls");
    assertNoAccessorsOrHiddenKeys(controls, "request.controls");
  }

  let budget = null;
  if (request.budget !== undefined && request.budget !== null) {
    assertObject(request.budget, "request.budget");
    assertClosedKeys(request.budget, BUDGET_KEYS, "request.budget");
    assertRequiredKeys(request.budget, BUDGET_KEYS, "request.budget");
    budget = {
      token_budget: assertSafeInteger(request.budget.token_budget, "request.budget.token_budget",
        { min: 0, max: 10_000_000 }),
    };
  }

  assertArray(request.records, "request.records", { min: 0, max: 1024 });
  const recordSeen = new Map();
  const records = request.records
    .map((raw, index) => compileRecord(raw, index, recordSeen))
    .sort((a, b) => (a.record_id < b.record_id ? -1 : 1));

  const queryIds = new Set(queries.map(q => q.query_id));
  const sourceIds = new Set(sources.map(s => s.source_id));
  for (const record of records) {
    if (!queryIds.has(record.query_id)) {
      fail("dangling_query_reference",
        `record "${record.record_id}" cites query "${record.query_id}", which the manifest does not carry`,
        { record_id: record.record_id, query_id: record.query_id });
    }
    if (!sourceIds.has(record.provenance.source_id)) {
      fail("dangling_source_reference",
        `record "${record.record_id}" cites source "${record.provenance.source_id}", which the manifest does not carry`,
        { record_id: record.record_id, source_id: record.provenance.source_id });
    }
  }

  // Step 3. Authority is computed, never accepted.
  const authorityResult = evaluateActorAuthority({
    actor, action: task.boundary_action, tenant: ORGANIZATION_TENANT_ID, now: request.now,
    ...(controls === null ? {} : { controls }),
  });
  const authority_envelope = {
    decision: authorityResult.decision,
    reason_id: authorityResult.reason_id,
    action: task.boundary_action,
    authority_class: authorityResult.authority_class,
    actor_slug: authorityResult.actor_slug,
    grant_kind: authorityResult.grant_kind ?? null,
    grant_ref: authorityResult.grant_ref ?? null,
    permanent_privilege_granted: false,
    computed_by: "global-boundaries.v5.evaluateActorAuthority",
    asserted_by_caller: false,
  };

  // Step 4. Taint.
  const lineage = compileTaintLineage(records);
  const byTaint = new Map(lineage.entries.map(entry => [entry.record_id, entry]));
  const taint_violations = [];
  for (const record of records) {
    const entry = byTaint.get(record.record_id);
    if (entry.tainted && V5_F05_AUTHORITY_BEARING_RECORD_KINDS.includes(record.record_kind)) {
      taint_violations.push({
        record_id: record.record_id, record_kind: record.record_kind,
        taint_class: entry.taint_class, reason_id: "untrusted_content_cannot_be_authority",
      });
    }
  }

  // Step 5. Freshness.
  const projectedRecords = records.map(record => {
    const observedAt = Date.parse(record.observed_at);
    const age_seconds = (now - observedAt) / 1000;
    if (age_seconds < 0) {
      fail("record_observed_after_now",
        `record "${record.record_id}" was observed after the assembly instant`,
        { record_id: record.record_id, observed_at: record.observed_at, now: request.now });
    }
    const stale = record.max_age_seconds !== null && age_seconds > record.max_age_seconds;
    const taint = byTaint.get(record.record_id);
    return {
      record_id: record.record_id, record_kind: record.record_kind, version: record.version,
      content_digest: record.content_digest, origin: record.origin,
      derived_kind: record.derived_kind, derived_from: [...record.derived_from],
      taint_class: taint.taint_class, tainted: taint.tainted, may_instruct: false,
      provenance: { ...record.provenance }, query_id: record.query_id,
      observed_at: record.observed_at, age_seconds, max_age_seconds: record.max_age_seconds,
      freshness: stale ? "stale" : "fresh",
      backs_control: record.backs_control, omissible: record.omissible && !record.backs_control,
      estimated_tokens: record.estimated_tokens,
      included: true, omission_reason_id: null,
    };
  });
  const stale_control_records = projectedRecords
    .filter(record => record.freshness === "stale" && record.backs_control)
    .map(record => ({ record_id: record.record_id, age_seconds: record.age_seconds,
      max_age_seconds: record.max_age_seconds, reason_id: "control_backing_record_stale" }));

  // Step 6. Coverage.
  const coverage = deriveRuleApplicability({
    tenant: ORGANIZATION_TENANT_ID,
    universe: request.universe,
    facts: task.facts,
    ...(request.semantic_candidates === undefined || request.semantic_candidates === null
      ? {} : { semantic_candidates: request.semantic_candidates }),
    now: request.now,
  });

  // Step 7. Budget. Guidance first, then omissible records, in id order, so two
  // callers holding the same input drop the same things.
  const deliveredRules = coverage.delivery.map(entry => ({ ...entry, included: true,
    omission_reason_id: null }));
  const guidance = coverage.semantic_additions.map(entry => ({ ...entry, included: true,
    omission_reason_id: null }));
  const omissions = [];
  const tokensOf = () =>
    deliveredRules.filter(r => r.included).reduce((sum, r) => sum + r.estimated_tokens, 0) +
    guidance.filter(g => g.included).reduce((sum, g) => sum + g.estimated_tokens, 0) +
    projectedRecords.filter(r => r.included).reduce((sum, r) => sum + r.estimated_tokens, 0);

  let budget_exceeded = false;
  if (budget !== null) {
    for (const entry of guidance) {
      if (tokensOf() <= budget.token_budget) break;
      entry.included = false;
      entry.omission_reason_id = "token_budget_guidance_dropped";
      omissions.push({ kind: "semantic_guidance", ref: entry.rule_id,
        reason_id: "token_budget_guidance_dropped", estimated_tokens: entry.estimated_tokens });
    }
    for (const record of projectedRecords) {
      if (tokensOf() <= budget.token_budget) break;
      if (!record.omissible) continue;
      record.included = false;
      record.omission_reason_id = "token_budget_omissible_record_dropped";
      omissions.push({ kind: "record", ref: record.record_id,
        reason_id: "token_budget_omissible_record_dropped", estimated_tokens: record.estimated_tokens });
    }
    budget_exceeded = tokensOf() > budget.token_budget;
  }

  // Step 8. The decision.
  const blocking_reasons = [...coverage.blocking_reasons];
  if (authority_envelope.decision !== "allow") blocking_reasons.push("authority_not_established");
  if (sources.some(s => s.required_for_task && s.state === "unavailable")) {
    blocking_reasons.push("required_source_unavailable");
  }
  if (sources.some(s => s.state === "conflicting")) blocking_reasons.push("source_conflict_unresolved");
  if (stale_control_records.length > 0) blocking_reasons.push("control_backing_record_stale");
  if (taint_violations.length > 0) blocking_reasons.push("untrusted_content_cannot_be_authority");
  if (budget_exceeded) blocking_reasons.push("budget_cannot_omit_binding_constraint");

  const actorRefused = authority_envelope.reason_id === "actor_not_verified_partner";
  // A hard refusal is one where producing the manifest at all would be a
  // fiction: an unknown principal, tainted content wearing authority, an
  // unresolved conflict, a rule that would not deliver, or a budget that cannot
  // be met without dropping a binding constraint. Everything else is
  // UNCERTAINTY, which read-only exploration is allowed to see.
  const hardRefusals = [];
  if (actorRefused) hardRefusals.push("actor_not_verified_partner");
  if (taint_violations.length > 0) hardRefusals.push("untrusted_content_cannot_be_authority");
  if (coverage.binding_conflicts.length > 0) hardRefusals.push("unresolved_binding_conflict");
  if (coverage.delivery_refusals.length > 0) hardRefusals.push("rule_delivery_failed_closed");
  if (budget_exceeded) hardRefusals.push("budget_cannot_omit_binding_constraint");

  const consequential_action_permitted =
    hardRefusals.length === 0 && blocking_reasons.length === 0 &&
    coverage.consequential_action_permitted && authority_envelope.decision === "allow";
  const read_only_exploration_permitted = hardRefusals.length === 0;

  let decision = "allow";
  let reason_id = "context_assembled";
  if (hardRefusals.length > 0) {
    decision = "refuse";
    reason_id = hardRefusals[0];
  } else if (mode === "consequential_action_proposal" && !consequential_action_permitted) {
    decision = "refuse";
    reason_id = blocking_reasons[0] ?? "authority_not_established";
  } else if (mode === "read_only_exploration" && blocking_reasons.length > 0) {
    reason_id = "read_only_exploration_under_uncertainty";
  }

  const manifest = {
    schema_version: V5_F05_MANIFEST_SCHEMA_VERSION,
    manifest_version: V5_F05_MANIFEST_VERSION,
    policy_version: V5_F05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    // A reproducible proposal, and it says so in the record. Nothing here is
    // authenticated; see authenticateRuntimeProjection.
    projection_kind: "reproducible_proposal",
    input_digest,
    rule_kernel_digest: v5F05RuleKernelDigest(),
    decision_subset_digest: v5F05DecisionSubsetDigest(),
    now: request.now,
    mode,
    decision,
    reason_id,
    task: { task_id: task.task_id, title: task.title, boundary_action: task.boundary_action,
      facts: coverage.facts, unknown_facts: coverage.unknown_facts },
    // The actor's own class where S01 reported one (it does so on the refusal
    // that turns on it); the ACTION's authority class lives in the envelope,
    // and conflating the two would read as a claim about the principal.
    actor: { slug: actor_slug, authorization_class: authorityResult.authorization_class ?? null },
    authority_envelope,
    universe_version: universe.universe_version,
    universe_digest: universe.universe_digest,
    universe_completeness: universe.completeness,
    coverage_receipt_digest: coverage.receipt_digest,
    rule_coverage: {
      universe_rule_ids: [...coverage.universe_rule_ids],
      effective: coverage.effective, possibly_binding: coverage.possibly_binding,
      not_applicable: coverage.not_applicable, retired: coverage.retired,
      superseded: coverage.superseded, overridden: coverage.overridden,
      suppressed_by_exception: coverage.suppressed_by_exception,
      pending_relations: coverage.pending_relations,
      binding_conflicts: coverage.binding_conflicts,
      delivery_refusals: coverage.delivery_refusals,
      semantic_reinforcements: coverage.semantic_reinforcements,
      semantic_may_remove_controls: false,
      coverage_complete: coverage.coverage_complete,
    },
    delivered_rules: deliveredRules,
    guidance: guidance,
    records: projectedRecords,
    taint_lineage: lineage.entries,
    taint_violations,
    declassification_supported: false,
    queries,
    sources,
    unavailable_sources: sources.filter(s => s.state === "unavailable").map(s => s.source_id),
    conflicting_sources: sources.filter(s => s.state === "conflicting").map(s => s.source_id),
    stale_records: projectedRecords.filter(r => r.freshness === "stale").map(r => r.record_id),
    stale_control_records,
    omissions,
    budget: {
      token_budget: budget === null ? null : budget.token_budget,
      estimated_tokens_total: tokensOf(),
      within_budget: budget === null ? true : !budget_exceeded,
      binding_constraint_omitted: false,
    },
    uncertainty: {
      marker: blocking_reasons.length > 0,
      unknown_fact_count: coverage.unknown_facts.length,
      possibly_binding_count: coverage.possibly_binding.length,
      pending_relation_count: coverage.pending_relations.length,
      universe_coverage_known: universe.completeness === "complete_authoritative_universe",
    },
    blocking_reasons,
    consequential_action_permitted,
    read_only_exploration_permitted,
    record_attribution_written: false,
    model_resolves_conflicts: false,
  };
  return deepFreeze({
    ...manifest,
    manifest_digest: digest(manifestPreimage(manifest)),
    effects: V5_NO_EFFECTS,
  });
}

/** Recompute a manifest's digest, so a hand-forged copy cannot pass as one. */
export function verifyContextManifest(manifest) {
  assertObject(manifest, "manifest");
  assertDigestRef(manifest.manifest_digest, "manifest.manifest_digest");
  const recomputed = digest(manifestPreimage(manifest));
  if (recomputed !== manifest.manifest_digest) {
    fail("manifest_digest_mismatch", "the manifest no longer hashes to its own digest",
      { expected: manifest.manifest_digest, actual: recomputed });
  }
  return true;
}

// ---------------------------------------------------------------------------
// The trusted-verifier interface.
//
// WHAT IT REFUSES TO ACCEPT, because each of these is a real bypass:
//   * a caller boolean — `authenticated: true` is refused by name;
//   * a hash alone — the verifier must present the BYTES, and the manifest is
//     re-derived from them, because a digest proves a match and says nothing
//     about what was matched;
//   * a serialized manifest object in place of the bytes — the manifest is an
//     OUTPUT here, never an input the attestation can define;
//   * a mutable request — there is no path from an object to a projection,
//     only from bytes.
// ---------------------------------------------------------------------------

const ATTESTATION_KEYS = Object.freeze([
  "verifier_id", "input_digest", "manifest_digest", "attested_at", "attestation_digest",
]);

/** The exact bytes a verifier signs over. Exported so a real one can produce it. */
export function verifierAttestationPreimage({ verifier_id, input_digest, manifest_digest, attested_at }) {
  return {
    schema_version: V5_F05_ATTESTATION_SCHEMA_VERSION,
    verifier_id, input_digest, manifest_digest, attested_at,
  };
}

export function verifierAttestationDigest(attestation) {
  return digest(verifierAttestationPreimage(attestation));
}

const RUNTIME_PROJECTION_KEYS = Object.freeze([
  "manifest", "input_bytes", "attestation", "now",
]);

export function authenticateRuntimeProjection(request) {
  assertObject(request, "request");
  assertClosedKeys(request, RUNTIME_PROJECTION_KEYS, "request");
  assertRequiredKeys(request, RUNTIME_PROJECTION_KEYS, "request");
  const now = assertInstant(request.now, "request.now");
  if (typeof request.input_bytes !== "string" || request.input_bytes.length === 0) {
    fail("invalid_shape", "request.input_bytes must be the exact frozen bytes, as a string",
      { path: "request.input_bytes" });
  }
  const manifest = assertObject(request.manifest, "request.manifest");
  verifyContextManifest(manifest);

  const base = {
    schema_version: V5_F05_ATTESTATION_SCHEMA_VERSION,
    manifest_digest: manifest.manifest_digest,
    input_digest: manifest.input_digest,
    verifier_id: null,
    projection_kind: "reproducible_proposal",
    authenticated_by_caller_boolean: false,
    effects: V5_NO_EFFECTS,
  };

  const bytesDigest = digest(request.input_bytes);
  if (bytesDigest !== manifest.input_digest) {
    return deepFreeze({ ...base, decision: "refuse", reason_id: "input_bytes_do_not_match_manifest",
      presented_input_digest: bytesDigest });
  }
  // The whole manifest is re-derived from the presented bytes. This is what
  // makes the attestation bind an input rather than a claim about one.
  const rederived = assembleContextManifest({
    frozen: true, schema_version: V5_F05_FROZEN_INPUT_SCHEMA_VERSION,
    input_bytes: request.input_bytes, input_digest: bytesDigest,
  });
  if (rederived.manifest_digest !== manifest.manifest_digest) {
    return deepFreeze({ ...base, decision: "refuse", reason_id: "manifest_not_reproducible_from_input",
      rederived_manifest_digest: rederived.manifest_digest });
  }

  const attestation = assertObject(request.attestation, "request.attestation");
  assertClosedKeys(attestation, ATTESTATION_KEYS, "request.attestation");
  assertRequiredKeys(attestation, ATTESTATION_KEYS, "request.attestation");
  const verifier_id = assertExternalIdent(attestation.verifier_id, "request.attestation.verifier_id",
    { maxLength: 128 });
  assertDigestRef(attestation.input_digest, "request.attestation.input_digest");
  assertDigestRef(attestation.manifest_digest, "request.attestation.manifest_digest");
  assertDigestRef(attestation.attestation_digest, "request.attestation.attestation_digest");
  const attestedAt = assertInstant(attestation.attested_at, "request.attestation.attested_at");
  const withVerifier = { ...base, verifier_id };

  if (attestation.input_digest !== manifest.input_digest) {
    return deepFreeze({ ...withVerifier, decision: "refuse", reason_id: "attestation_input_mismatch" });
  }
  if (attestation.manifest_digest !== manifest.manifest_digest) {
    return deepFreeze({ ...withVerifier, decision: "refuse", reason_id: "attestation_manifest_mismatch" });
  }
  const recomputed = verifierAttestationDigest({
    verifier_id, input_digest: attestation.input_digest,
    manifest_digest: attestation.manifest_digest, attested_at: attestation.attested_at,
  });
  if (recomputed !== attestation.attestation_digest) {
    return deepFreeze({ ...withVerifier, decision: "refuse", reason_id: "attestation_digest_mismatch",
      expected: recomputed });
  }
  if (attestedAt > now) {
    return deepFreeze({ ...withVerifier, decision: "refuse", reason_id: "attestation_not_yet_effective" });
  }
  if ((now - attestedAt) / 1000 > V5_F05_MAX_ATTESTATION_AGE_SECONDS) {
    return deepFreeze({ ...withVerifier, decision: "refuse", reason_id: "attestation_stale",
      max_age_seconds: V5_F05_MAX_ATTESTATION_AGE_SECONDS });
  }
  return deepFreeze({
    ...withVerifier,
    decision: "allow",
    reason_id: "attestation_binds_exact_input_bytes",
    projection_kind: "authenticated_runtime_projection",
    attested_at: attestation.attested_at,
  });
}

// ---------------------------------------------------------------------------
// The correction taxonomy.
//
// A model may propose a correction. It may not write one, and what it proposes
// is TYPED BOUNDED METADATA plus references to governed records — never a
// transcript, never a secret, never new rule text, and never an edit to
// AGENTS.md or any other boot file. The proposal binds nobody: a human route
// promotes it or it stays a proposal.
// ---------------------------------------------------------------------------

export const V5_F05_CORRECTION_KINDS = deepFreeze([
  "context_omission", "fact_dimension_missing", "rule_gap",
  "stale_enforcement_evidence", "suspected_rule_conflict", "taint_boundary_violation",
]);

/** The note is a pointer, not a payload. 280 characters is the bound. */
export const V5_F05_MAX_CORRECTION_NOTE_CHARS = 280;

// Shapes that mean a secret or a raw transcript is being smuggled into a
// learning store. Checked on the NOTE only, which is the one free-text field.
const SECRET_FRAGMENTS = deepFreeze([
  "api_key", "apikey", "password", "passwd", "secret", "bearer ", "authorization:",
  "private key", "begin rsa", "begin openssh", "begin private",
  "access_token", "refresh_token",
]);
// Provider key prefixes, matched at a word boundary and with a payload after
// them. A bare "sk-" fragment would refuse an ordinary note about a risk-tier
// fact, which is the kind of false refusal that teaches people to route around
// a check.
const CREDENTIAL_PREFIX = /\b(?:sk|pk|ghp|ghs|gho|xoxb|xoxp|akia)[-_][A-Za-z0-9]{8,}/i;
const OPAQUE_RUN = /[A-Za-z0-9+/=_-]{40,}/;
const TRANSCRIPT_MARKER = /^\s*(?:user|assistant|human|system)\s*:/im;

const CORRECTION_KEYS = Object.freeze([
  "correction_kind", "subject_rule_id", "note", "governed_source_refs", "observed_at",
]);
const SOURCE_REF_KEYS = Object.freeze(["record_id", "version", "content_digest"]);

export function proposeCorrection(proposal) {
  assertObject(proposal, "proposal");
  assertClosedKeys(proposal, CORRECTION_KEYS, "proposal");
  assertRequiredKeys(proposal, ["correction_kind", "note", "governed_source_refs", "observed_at"],
    "proposal");
  const correction_kind = assertEnum(proposal.correction_kind, V5_F05_CORRECTION_KINDS,
    "proposal.correction_kind", "unknown_correction_kind");
  const note = assertSafeText(proposal.note, "proposal.note",
    { maxLength: V5_F05_MAX_CORRECTION_NOTE_CHARS, prose: true });
  const normalized = note.toLowerCase();
  for (const fragment of SECRET_FRAGMENTS) {
    if (normalized.includes(fragment)) {
      fail("secret_in_correction",
        `proposal.note looks like it carries a credential ("${fragment}"); corrections are metadata, not payloads`,
        { fragment });
    }
  }
  if (CREDENTIAL_PREFIX.test(note)) {
    fail("secret_in_correction",
      "proposal.note carries something shaped like a provider key; corrections are metadata, not payloads",
      {});
  }
  if (OPAQUE_RUN.test(note)) {
    fail("opaque_blob_in_correction",
      "proposal.note carries a long opaque run; a token, key or digest belongs in a governed reference",
      {});
  }
  if (TRANSCRIPT_MARKER.test(note)) {
    fail("transcript_in_correction",
      "proposal.note looks like a transcript excerpt; the learning store holds typed metadata only", {});
  }
  assertInstant(proposal.observed_at, "proposal.observed_at");
  assertArray(proposal.governed_source_refs, "proposal.governed_source_refs", { min: 1, max: 8 });
  const seen = new Set();
  const governed_source_refs = proposal.governed_source_refs.map((raw, index) => {
    const path = `proposal.governed_source_refs[${index}]`;
    assertObject(raw, path);
    assertClosedKeys(raw, SOURCE_REF_KEYS, path);
    assertRequiredKeys(raw, SOURCE_REF_KEYS, path);
    const record_id = assertExternalIdent(raw.record_id, `${path}.record_id`, { maxLength: 128 });
    if (seen.has(record_id)) {
      fail("duplicate_source_ref", `${path} repeats record "${record_id}"`, { path, record_id });
    }
    seen.add(record_id);
    return {
      record_id,
      version: assertSafeInteger(raw.version, `${path}.version`, { min: 1 }),
      content_digest: assertDigestRef(raw.content_digest, `${path}.content_digest`),
    };
  }).sort((a, b) => (a.record_id < b.record_id ? -1 : 1));

  const record = {
    schema_version: V5_F05_CORRECTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    correction_kind,
    subject_rule_id: proposal.subject_rule_id === undefined || proposal.subject_rule_id === null
      ? null : assertExternalIdent(proposal.subject_rule_id, "proposal.subject_rule_id",
        { maxLength: 128 }),
    note,
    governed_source_refs,
    observed_at: proposal.observed_at,
    // What a proposal is NOT, stated in the record rather than in a comment.
    applies_automatically: false,
    rewrites_boot_instructions: false,
    writes_records: false,
    carries_raw_transcript: false,
    carries_secret: false,
  };
  return deepFreeze({
    decision: "allow",
    reason_id: "typed_bounded_correction_proposal",
    ...record,
    proposal_digest: digest(record),
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed projection, and the seams that are deliberately not built.
// ---------------------------------------------------------------------------

export function v5F05ContextContractPreimage() {
  return {
    schema_version: V5_F05_MANIFEST_SCHEMA_VERSION,
    manifest_version: V5_F05_MANIFEST_VERSION,
    policy_version: V5_F05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    rule_kernel_digest: v5F05RuleKernelDigest(),
    decision_subset_digest: v5F05DecisionSubsetDigest(),
    decisions: V5_F05_SETTLED_DECISION_IDS.map(decision_id => ({
      decision_id,
      settled_requirement: V5_F05_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_F05_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
    modes: [...V5_F05_MODES],
    origins: [...V5_F05_ORIGINS],
    external_origins: [...V5_F05_EXTERNAL_ORIGINS],
    derived_kinds: [...V5_F05_DERIVED_KINDS],
    taint_classes: [...V5_F05_TAINT_CLASSES],
    tainted_classes: [...V5_F05_TAINTED_CLASSES],
    untrusted_forbidden_uses: [...V5_F05_UNTRUSTED_FORBIDDEN_USES],
    untrusted_permitted_uses: [...V5_F05_UNTRUSTED_PERMITTED_USES],
    authority_bearing_record_kinds: [...V5_F05_AUTHORITY_BEARING_RECORD_KINDS],
    correction_kinds: [...V5_F05_CORRECTION_KINDS],
    max_correction_note_chars: V5_F05_MAX_CORRECTION_NOTE_CHARS,
    max_attestation_age_seconds: V5_F05_MAX_ATTESTATION_AGE_SECONDS,
    authority_is_computed_not_asserted: true,
    declassification_supported: false,
    binding_constraint_may_be_omitted_for_tokens: false,
    external_content_may_instruct: false,
  };
}

export function v5F05ContextContractDigest() {
  return digest(v5F05ContextContractPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5F05ContextContractCanonicalBytes() {
  return canonicalJson(v5F05ContextContractPreimage());
}

/**
 * WHICH RUNTIME SEAMS ARE STILL OPEN, stated rather than implied. `landed` is
 * false where this module can see the answer and null where it cannot;
 * assertContextAssemblyIntegrationComplete refuses on anything that is not
 * exactly true, so "we could not check it" never reads as "it was done".
 */
export function contextAssemblyIntegrationGaps() {
  return deepFreeze([
    {
      gap: "no_retrieval_executor",
      where: "mcp-server/src/context-assembly.v5.js",
      what: "records, queries and sources are supplied by the caller; nothing here runs a"
        + " query, reads Neon, or reaches a connector",
      landed: false,
    },
    {
      gap: "no_manifest_persistence",
      where: "domain.sql",
      what: "no table stores a manifest or binds a consequential result to the manifest that"
        + " produced it; this slice adds no migration and no ordinal",
      landed: false,
    },
    {
      gap: "no_registered_verifier",
      where: "mcp-server/src/",
      what: "no component issues verifier attestations, so every projection stays a"
        + " reproducible proposal until one exists",
      landed: false,
    },
    {
      gap: "no_correction_store",
      where: "domain.sql",
      what: "a correction proposal is returned to its caller and stored nowhere; promotion"
        + " remains a human route that does not exist yet",
      landed: false,
    },
    {
      gap: "no_connector_fixture_suite",
      where: "mcp-server/test/",
      what: "Q068 asks for prompt-injection fixtures on every connector interface; this slice"
        + " proves the boundary, not the connectors, because no connector is in its path",
      landed: false,
    },
  ]);
}

export function assertContextAssemblyIntegrationComplete() {
  const open = contextAssemblyIntegrationGaps().filter(entry => entry.landed !== true);
  if (open.length > 0) {
    fail("context_assembly_integration_incomplete",
      `the F05 context assembler is not integrated: ${open.map(e => e.gap).join(", ")}`,
      { open: open.map(e => ({ gap: e.gap, where: e.where, landed: e.landed })) });
  }
  return true;
}

// ---------------------------------------------------------------------------
// Load-time self-check. The shared guard refuses a set of field names outright;
// this proves it collides with none of the keys THIS half accepts, so the guard
// can never refuse a legitimate request. A later edit that adds a colliding key
// fails this module's own import rather than a caller's request.
// ---------------------------------------------------------------------------

for (const list of [
  REQUEST_KEYS, TASK_KEYS, QUERY_KEYS, SOURCE_KEYS, BUDGET_KEYS, FROZEN_KEYS,
  RECORD_KEYS, PROVENANCE_KEYS, ATTESTATION_KEYS, RUNTIME_PROJECTION_KEYS,
  UNTRUSTED_USE_KEYS, CORRECTION_KEYS, SOURCE_REF_KEYS,
]) {
  for (const key of list) {
    try {
      V5_F05_GUARDS.assertClosedKeys({ [key]: null }, [key], "self_check");
    } catch (error) {
      throw new V5F05Error("guard_collides_with_accepted_key",
        `the shared caller-assertion guard would refuse the accepted key "${key}"`,
        { key, cause: error.code });
    }
  }
}
