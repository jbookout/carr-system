// DoctorCRE v5 slice V5-J301 — attended MLS intake and the resumable Tour
// workflow, as a pure deterministic kernel.
//
// Seven settled decisions (Q014.D2, Q059.D4, Q060.D1, Q072.D2, Q080.D2,
// Q123.D3, Q124.D2) are encoded here as ONE closed, versioned domain contract
// with a deterministic digest, plus pure evaluators over that contract. The
// typed map-command half of Q124.D2 lives beside this file in
// tour-map-command-j301.v5.js; everything else is here. Canonicalization and
// hashing come from artifact-trust.js, the no-effects marker comes from
// global-boundaries.v5.js, the Assignment and Deal axes come from
// cre-lifecycle.v5.js (V5-J102), and the partner predicate comes from
// identity.js. This file reimplements none of them.
//
// TWO SENTENCES THE WHOLE SLICE TURNS ON.
//
//   ATTENDED means a human is present for the intake action itself. Not
//   "a human configured it", not "a human owns the account", not "a human will
//   review it later". Q014.D2 and Q059.D4 both say the same thing from
//   different directions: Tour packet work must not DEPEND ON UNATTENDED MLS
//   ACCESS. So an unattended intent is refused by name in this module, and no
//   input can turn it back on.
//
//   RESUMABLE means the workflow's position survives an interruption because it
//   is READ FROM RECORDS, never from session memory. That is the half this repo
//   cannot finish today: the durable stage journal that would own the position
//   DOES NOT EXIST HERE. Rather than pretend, every request that would advance
//   a stage comes back `unavailable` naming the owed seam, while every request
//   that VIOLATES the staging rules is refused by real shipped code. Denial
//   never needs an authority the repository lacks; permission does.
//
// WHAT IS CODE HERE AND WHAT MUST ARRIVE AS DURABLE STATE, because the line is
// the point of the slice:
//
//   IN CODE — the STRUCTURE the seven decisions settle. That the five stages of
//   Q060.D1 are separate and ordered; that intake is attended; that models may
//   occupy the assembly stage and no other; that a Tour's activity on an
//   Assignment never carries an Assignment phase or a Deal axis with it
//   (Q072.D2, Q080.D2); that a correction is an APPEND against an existing
//   entry rather than an erasure; and that the same logical action folds to the
//   same step key however many times a caller replays it.
//
//   AS DURABLE STATE — the stage journal itself. Which actions really happened,
//   in which order, under which human, AND THEREFORE every rule that reads a
//   history: skip, backward move, replay, gap. A journal a CALLER hands in is
//   not a weaker version of that record, it is a different thing entirely, so
//   this module refuses one rather than labelling one.
//
// TWO KINDS OF NO, following the sibling v5 modules deliberately:
//   * A POLICY ANSWER is returned — a frozen result whose `decision` is one of
//     this module's two registered values (`refused`, `unavailable`) with a
//     stable `reason_id`. "The stage journal owner does not exist" is one of
//     these: an honest unavailable a caller may record.
//   * A CONTRACT VIOLATION throws V5J301Error. Unknown fields, unknown
//     vocabulary values, open schemas, malformed Unicode and unreadable
//     timestamps are not policy questions; the module cannot read the request
//     at all, so it fails closed rather than guessing which boundary was meant.
//
// THERE IS NO THIRD ANSWER, AND NO EXCEPTION TO IT. No exported function in
// this module returns an allow, a commit, an advance, a resume point, a
// completion or any other privileged outcome, under any name, from any input a
// caller controls. The module's real exports equal V5_J301_PUBLIC_SURFACE
// EXACTLY — there is no test-only member on the list and none off it.
//
// AND THERE IS NO RESUME CLASSIFICATION HERE AT ALL. The previous shape of this
// file read a caller-supplied journal for position and reported the reading in
// the BODY of its refusals — `stages_seen`, `earliest_unstarted_stage`,
// `missing_stage`, `furthest_stage_seen`. A reviewer was right that a refusal
// whose body reconstructs the resume boundary IS the resume boundary, however
// the envelope is labelled: a caller could write a journal, read the refusals,
// and recover exactly the position no authority here can establish. Labelling
// it `caller_supplied_view` did not make it less recoverable.
//
// So the public path now takes NO caller journal. `journal_view` is refused by
// name, naming the owner and reader seams, and every remaining answer is
// derived from the request's own fields and this module's frozen registries —
// never from a history a caller wrote. The staging rules of Q060.D1 are real
// and they are ORDERED CONSEQUENCES OF A JOURNAL, so they belong to the journal
// owner; their reason ids are declared here, in
// V5_J301_JOURNAL_OWNER_REASON_IDS, as the vocabulary that seam owes, and the
// suite proves the classification behind them against
// mcp-server/test/tour-workflow-classifiers.v5.testhelper.mjs — a file in the
// test tree that no production module can import, proved by a parsed static AND
// dynamic import scan of every module under src/, not by a promise.
//
// ATTENDANCE IS NOT A STRING. `declared_actor_slug` is exactly what its name
// says: something a caller typed. This module runs no membership test on it,
// echoes it in no answer, and is provably indifferent to it. The authenticated
// actor that COULD establish attendance lives behind the identity seam
// (mcp-server/src/identity.js), which reads server-derived grant props a pure
// evaluator never sees — so every attended action ends at
// `attended_actor_source_unavailable` naming that seam.
//
// A CALLER-SUPPLIED GATE RECEIPT IS REFUSED BY NAME. Tour and map-capable
// behavior is blocked until `tour-map-contract-1.2.0-accepted`, whose producer
// step `step:tour-map-contract-1.2.0-independent-acceptance-receipt` has no
// implementation in this repository — the live map-architecture verb reports
// the contract as approved architecture, not implemented in production. A
// request that arrives carrying its own receipt object is not a request with a
// gate; it is a request trying to BE the gate, and it is refused rather than
// read.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_J102_ASSIGNMENT_PHASES,
  V5_J102_DEAL_AXES,
} from "./cre-lifecycle.v5.js";

export { V5_NO_EFFECTS };

export const V5_J301_SCHEMA_VERSION = "doctorcre-v5-tour-workflow.v1";
export const V5_J301_POLICY_VERSION = 1;

export const V5_J301_JOURNAL_ENTRY_SCHEMA_VERSION =
  "doctorcre-v5-j301-tour-workflow-journal-entry.v1";
export const V5_J301_STAGE_ACTION_SCHEMA_VERSION =
  "doctorcre-v5-j301-tour-workflow-stage-action.v1";
export const V5_J301_ACTIVITY_SCHEMA_VERSION =
  "doctorcre-v5-j301-tour-assignment-activity.v1";
export const V5_J301_PROJECTION_SCHEMA_VERSION =
  "doctorcre-v5-j301-tour-workflow-projection.v1";
export const V5_J301_RESUME_SCHEMA_VERSION =
  "doctorcre-v5-j301-tour-workflow-resume.v1";

// ---------------------------------------------------------------------------
// Local primitives. Each v5 module carries its own copy on purpose: a shared
// assertion library would become a place a caller could weaken one module's
// floor by editing another's.
// ---------------------------------------------------------------------------

const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
// Digest references carry their algorithm, matching both artifact-trust.js's
// own output and the Tour domain's existing `sha256:<hex>` convention. A bare
// hex string is refused rather than accepted "helpfully": two spellings of one
// digest is how two records of one thing get created.
const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
// Control characters, bidirectional overrides, zero-width and other invisible
// format characters. An identifier that renders as another identifier is an
// identity split waiting to happen, so it is refused rather than normalized.
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

export class V5J301Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J301Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J301Error(code, message, detail);
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

function assertArray(value, path, { min = 0, max = 512 } = {}) {
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

function assertEnum(value, registered, path, code) {
  if (typeof value !== "string" || !registered.includes(value)) {
    fail(code, `"${String(value)}" is not registered at ${path}`,
      { path, value: typeof value === "string" ? value : null, registered: [...registered] });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !DIGEST_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference over 64 lower-case hex characters`,
      { path });
  }
  return value;
}

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"`,
      { path, expected: ORGANIZATION_TENANT_ID });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Instants are parsed, never inferred, and the CALENDAR is checked against the
 * literal fields before parsing. Date.parse normalizes an impossible date into
 * a different one — "2026-02-31T00:00:00Z" silently becomes 3 March — and a
 * workflow that orders stages by time cannot be careless about a clock.
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

// ---------------------------------------------------------------------------
// The settled decisions. Text and evidence digests are copied verbatim from the
// reviewed r7 design basis (document doctorcre-v5-design-basis, artifact
// doctorcre-v5-design-basis-r7-review.json, whose reassembled bytes hash to
// ef34aa54740dd56508b7cebf05a2a95851aacedbbe4f2e4865a39ffede28f0ad). They are
// identity, not configuration, and they are hashed into the policy preimage
// below so a drifted copy moves the digest instead of quietly disagreeing.
// ---------------------------------------------------------------------------

export const V5_J301_SETTLED_DECISIONS = deepFreeze({
  "Q014.D2": {
    settled_requirement:
      "Tour packet generation is a high-priority later v5 journey, but it must not block the first daily release or depend on unattended MLS access.",
    source_evidence_digest: "140451d506011d54abd49f86fc328655615daf815ab1f413d99a4171b65f30c3",
  },
  "Q059.D4": {
    settled_requirement:
      "Promote attended MLS acquisition and governed Tour generation after the core work and meeting rails.",
    source_evidence_digest: "5c58f2233ea82aab0f66bd966467e24875504900bd96fa98be4fa300436fd513",
  },
  "Q060.D1": {
    settled_requirement:
      "Keep attended MLS acquisition, deterministic normalization, agent-assisted Tour assembly, deterministic generation, and client-facing review as separate resumable workflow stages.",
    source_evidence_digest: "bfb7bc8249530da6d124cc8e88a75bc4103d841b2fc4cc40df90955ce8444a41",
  },
  "Q072.D2": {
    settled_requirement:
      "A formally created governed Tour records touring activity on its Assignment without necessarily replacing the Assignment's broader research, search, or negotiation phase; deterministic code validates the Tour and map-contract evidence and records any correction with preserved history.",
    source_evidence_digest: "8e27b8e4a5e9c902be7f8529793ca0404cb5ce778adc48bbefa40f0deb8c96bc",
  },
  "Q080.D2": {
    settled_requirement:
      "In Journey 3, Assignment owns governed Tours as a distinct activity while retaining independent research, search, and negotiation state; each Tour is validated under carr-map-tour-v1 1.2.0 and cannot implicitly create or execute a Deal.",
    source_evidence_digest: "8321243bef91e506dbc88fcf7b0627ac4393639e57a7f419545b3ce04c9d8147",
  },
  "Q123.D3": {
    settled_requirement:
      "Add governed Tour generation and sharing as the third current product journey without blocking the first launch.",
    source_evidence_digest: "55457ebf6cdb2c1959eadcb5358db6bf67a7366d4f941c28be19a5ddaf397b35",
  },
  "Q124.D2": {
    settled_requirement:
      "In Journey 3, clickable Tour maps and Doc must invoke the same typed map commands and yield equivalent governed coordinate, route, selection, and navigation state under carr-map-tour-v1 1.2.0.",
    source_evidence_digest: "716b12dab40c9f12f0b25cad565417871f028a27a0a2fe7ac0017d17b69ebfec",
  },
});

export const V5_J301_SETTLED_DECISION_IDS =
  deepFreeze(Object.keys(V5_J301_SETTLED_DECISIONS).sort());

/**
 * Refuse a caller whose decision subset has drifted from the reviewed one.
 *
 * Drift is checked in BOTH directions — a missing decision and an extra one are
 * both drift — and every source-evidence digest must match exactly. A caller
 * that believes it holds a different subset proves the disagreement here rather
 * than discovering it after a Tour was staged on the wrong rule.
 */
export function assertJ301DecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  const supplied = assertObject(binding.decisions, "binding.decisions");
  const suppliedIds = Object.keys(supplied).sort();
  for (const id of V5_J301_SETTLED_DECISION_IDS) {
    if (!suppliedIds.includes(id)) {
      fail("decision_subset_drift", `binding.decisions is missing "${id}"`,
        { missing: id, expected: [...V5_J301_SETTLED_DECISION_IDS] });
    }
  }
  for (const id of suppliedIds) {
    if (!V5_J301_SETTLED_DECISION_IDS.includes(id)) {
      fail("decision_subset_drift", `binding.decisions carries an unregistered decision "${id}"`,
        { unexpected: id, expected: [...V5_J301_SETTLED_DECISION_IDS] });
    }
    const entry = assertObject(supplied[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["settled_requirement", "source_evidence_digest"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["settled_requirement", "source_evidence_digest"], `binding.decisions.${id}`);
    const reviewed = V5_J301_SETTLED_DECISIONS[id];
    if (entry.settled_requirement !== reviewed.settled_requirement) {
      fail("decision_text_drift", `binding.decisions.${id}.settled_requirement does not match the reviewed text`,
        { decision_id: id });
    }
    if (entry.source_evidence_digest !== reviewed.source_evidence_digest) {
      fail("decision_evidence_drift", `binding.decisions.${id}.source_evidence_digest does not match the reviewed digest`,
        { decision_id: id, expected: reviewed.source_evidence_digest });
    }
  }
  return deepFreeze({
    decisions_bound: [...V5_J301_SETTLED_DECISION_IDS],
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed vocabularies. Every one of them is hashed into the policy preimage
// below, so a vocabulary that changes moves the digest and a stale reader is
// refused rather than silently reading an axis that moved under it.
// ---------------------------------------------------------------------------

/**
 * The five stages of Q060.D1, IN ORDER, named as the decision names them.
 *
 * The order is part of the contract, not a presentation choice: "separate
 * resumable stages" is only meaningful if a later stage cannot quietly run
 * before an earlier one produced its input.
 */
export const V5_J301_STAGES = deepFreeze([
  "attended_mls_acquisition",
  "deterministic_normalization",
  "agent_assisted_assembly",
  "deterministic_generation",
  "client_facing_review",
]);

export const V5_J301_STAGE_INDEX = deepFreeze(
  Object.fromEntries(V5_J301_STAGES.map((stage, index) => [stage, index])));

/** The three actor classes a stage action may carry, and no others. */
export const V5_J301_ACTOR_CLASSES = deepFreeze([
  "human_attended", "deterministic", "model_assisted",
]);

/**
 * THE STAGE REGISTRY. Every action this workflow recognizes, the actor class
 * that may perform it, and the record-layer verb the write is INTENDED to
 * traverse. `intended_verb: null` marks an action that produces a PROPOSAL
 * rather than a record — the model stage's two actions, which is the whole of
 * the model judgment boundary this slice was given.
 *
 * `intended_verb` IS A NAME AND NOTHING MORE. This module builds no argument
 * list for any of these verbs, calls none of them, and has no adapter that
 * could. The suite checks every name against the deployed registry in tools.js
 * so a renamed or retired verb fails here rather than at a caller — that proves
 * the NAME still resolves, which is not the same fact as a write traversing it.
 * The adapter is owed at V5_J301_VERB_ADAPTER_SEAM and does not exist.
 */
export const V5_J301_STAGE_ACTIONS = deepFreeze({
  attended_mls_acquisition: {
    capture_listing_observation: {
      actor_class: "human_attended", intended_verb: "append-tour-source-evidence",
    },
    attach_property_identifier: {
      actor_class: "human_attended", intended_verb: "append-tour-property-identifier-assertion",
    },
  },
  deterministic_normalization: {
    normalize_property_fact: {
      actor_class: "deterministic", intended_verb: "append-tour-field-assertion",
    },
    normalize_entrance_coordinate: {
      actor_class: "deterministic", intended_verb: "append-tour-coordinate-candidate",
    },
  },
  agent_assisted_assembly: {
    rank_candidate_stops: { actor_class: "model_assisted", intended_verb: null },
    draft_stop_narrative: { actor_class: "model_assisted", intended_verb: null },
  },
  deterministic_generation: {
    generate_route_version: {
      actor_class: "deterministic", intended_verb: "prepare-tour-route-version",
    },
    generate_selection_cart_version: {
      actor_class: "deterministic", intended_verb: "append-tour-selection-cart-version",
    },
  },
  client_facing_review: {
    record_client_review_note: {
      actor_class: "human_attended", intended_verb: "append-tour-cheat-sheet-revision",
    },
    accept_route_for_client_review: {
      actor_class: "human_attended", intended_verb: "accept-tour-route-version",
    },
  },
});

export const V5_J301_ACTION_KINDS = deepFreeze(
  [...new Set(V5_J301_STAGES.flatMap(stage => Object.keys(V5_J301_STAGE_ACTIONS[stage])))].sort());

export const V5_J301_INTENDED_VERBS = deepFreeze(
  [...new Set(V5_J301_STAGES.flatMap(stage =>
    Object.values(V5_J301_STAGE_ACTIONS[stage])
      .map(action => action.intended_verb)
      .filter(verb => verb !== null)))].sort());

/** The stages a model may occupy. Exactly one, and it is named in the decision. */
export const V5_J301_MODEL_PERMITTED_STAGES = deepFreeze(["agent_assisted_assembly"]);

/**
 * ATTENDANCE. The one intent that reads as a human being present for the action
 * itself, and the four that do not. The refused four are listed BY NAME rather
 * than inferred from the absence of the accepted one, because "we did not
 * recognize your intent" and "your intent is unattended" deserve different
 * answers and only the second is a policy statement.
 */
export const V5_J301_ATTENDED_INTENT = "human_present_for_this_action";
export const V5_J301_REFUSED_INTENTS = deepFreeze([
  "unattended_scheduled_run",
  "background_crawl",
  "delegated_subagent_seat",
  "replay_without_human",
]);
export const V5_J301_INTENTS = deepFreeze(
  [V5_J301_ATTENDED_INTENT, ...V5_J301_REFUSED_INTENTS].sort());

/** The two answers this module can give. There is deliberately no third. */
export const V5_J301_DECISIONS = deepFreeze(["refused", "unavailable"]);

/**
 * LIFECYCLE FIELD NAMES A TOUR MAY NOT CARRY.
 *
 * Built FROM V5-J102's own vocabulary rather than retyped beside it, so the day
 * J102 adds a Deal axis this refusal covers it without anyone remembering to.
 * Q072.D2 and Q080.D2 both turn on the same thing: a Tour records touring
 * activity, and the Assignment's research/search/negotiation phase and every
 * Deal axis stay independently evidenced. A field NAME reaching for one of them
 * inside a Tour activity payload is the implicit mutation those decisions
 * forbid, so it is refused structurally rather than policed by a flag.
 */
export const V5_J301_FORBIDDEN_ACTIVITY_FIELDS = deepFreeze([
  ...new Set([
    "assignment_phase", "assignment_state", "relationship_state", "engagement_state",
    "negotiation_state", "deal_id", "deal", "instrument_kind",
    ...V5_J102_DEAL_AXES,
  ])].sort());

/**
 * The Assignment phases a Tour may never set. Imported, not invented: these are
 * J102's five, and the suite proves the two lists are the same list.
 */
export const V5_J301_ASSIGNMENT_PHASES = deepFreeze([...V5_J102_ASSIGNMENT_PHASES]);

/** The Tour activity kinds an Assignment may own. Touring only, by design. */
export const V5_J301_ACTIVITY_KINDS = deepFreeze([
  "tour_created", "tour_route_accepted", "tour_conducted", "tour_corrected",
]);

// ---------------------------------------------------------------------------
// The seams. Each one names a thing that does not exist in this repository and
// says what would have to arrive for the answer to change. A seam is not a
// promise that something works; it is the exact shape of the hole.
// ---------------------------------------------------------------------------

/**
 * The durable owner of workflow position. It does not exist. No table in
 * db/schema.sql holds a Tour workflow stage journal, and this slice was
 * dispatched under a migration freeze, so the SQL is stated in the slice report
 * under migration_owed rather than written.
 */
export const V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM =
  "seam:v5-j301-durable-tour-workflow-stage-journal";

/** The Assignment activity record's owner. V5-J102 is a kernel, not a store. */
export const V5_J301_ASSIGNMENT_ACTIVITY_OWNER_SEAM =
  "seam:v5-j301-durable-assignment-tour-activity-record";

/**
 * THE AUTHENTICATED ATTENDED-ACTOR SOURCE, AND WHY NOTHING HERE CAN STAND IN
 * FOR IT.
 *
 * "Attended" is a claim about the WORLD — a human was present when this action
 * happened — and the only thing in this repository that can turn an incoming
 * request into a verified human is the identity seam, mcp-server/src/identity.js.
 * Its `actorFromProps` reads SERVER-DERIVED grant props (`via`, `client_id`, the
 * sponsoring human) that the request handler holds and a verb argument can never
 * carry; `isKnownPartner` below it is a two-name set lookup over a STRING, which
 * answers "is this spelled like a partner slug", not "is a partner here".
 *
 * This module is a pure evaluator. No grant props reach it, and accepting an
 * actor object as an argument would make the caller its own authenticator —
 * exactly the injected holder the standing rule forbids. So there is no
 * membership test on a slug anywhere in this file, and every action whose actor
 * class is `human_attended` ends at `attended_actor_source_unavailable` naming
 * this seam. A declared actor slug may still CONDEMN a request (it is carried
 * into the future journal for attribution); it can never absolve one.
 */
export const V5_J301_ATTENDED_ACTOR_SOURCE_SEAM =
  "seam:v5-j301-authenticated-attended-actor-source";

/**
 * The durable journal READER. Distinct from the journal owner above: even once
 * the table exists, something has to read it on this module's behalf, and the
 * public resume path is unavailable until it does. A caller-supplied view is
 * never that reader.
 */
export const V5_J301_WORKFLOW_JOURNAL_READER_SEAM =
  "seam:v5-j301-tour-workflow-stage-journal-reader";

/**
 * THE STAGING VOCABULARY THE JOURNAL OWNER OWES, and nothing this module emits.
 *
 * Q060.D1's staging rules are real and they are worth naming precisely, but
 * each one is a statement about a HISTORY — which actions really happened, in
 * which order — and the only thing that could make such a statement here is a
 * caller. So these six reason ids are DECLARED and not PRODUCED: they are the
 * answers `V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM` will give once something
 * authoritative can read the journal, published now so a future implementation
 * has one spelling to conform to and so a reader can see exactly which questions
 * this module is not answering.
 *
 *   journal_entry_foreign_to_tour      an entry of another tenant, Tour or Assignment
 *   journal_history_noncontiguous      a stage with no entry while a later stage has one
 *   stage_skipped                      a stage past the one after the furthest with activity
 *   backward_stage_requires_correction an earlier stage with no correction target named
 *   correction_target_absent           a correction naming a step key the journal does not hold
 *   duplicate_step_key_replay          a step key the journal already holds
 *
 * No exported function in this module returns any of them. The classification
 * behind them is proved conditionally — `would_be_*`, never `is` — in
 * mcp-server/test/tour-workflow-classifiers.v5.testhelper.mjs, which cites this
 * list so the two cannot drift apart.
 */
export const V5_J301_JOURNAL_OWNER_REASON_IDS = deepFreeze([
  "backward_stage_requires_correction",
  "correction_target_absent",
  "duplicate_step_key_replay",
  "journal_entry_foreign_to_tour",
  "journal_history_noncontiguous",
  "stage_skipped",
]);

/**
 * The adapter that would turn a named `intended_verb` into a call the deployed
 * verb's own inputSchema accepts. It does not exist, and the gap is not
 * cosmetic: `prepare-tour-route-version` requires `idempotency_key` and
 * `stop_ids`, `append-tour-selection-cart-version` requires six fields, and
 * `append-tour-coordinate-candidate` requires provenance, rights and review
 * fields this module never produces. Naming a verb is not traversing it.
 */
export const V5_J301_VERB_ADAPTER_SEAM = "seam:v5-j301-record-layer-verb-adapter";

export const V5_J301_MAP_CONTRACT = "carr-map-tour-v1";
export const V5_J301_MAP_CONTRACT_VERSION = "1.2.0";
export const V5_J301_MAP_CONTRACT_GATE = "tour-map-contract-1.2.0-accepted";
export const V5_J301_MAP_CONTRACT_RECEIPT_STEP =
  "step:tour-map-contract-1.2.0-independent-acceptance-receipt";
/**
 * Read from the live map-architecture verb on 2026-09-11, which is the
 * mandatory front door for any Tour surface. It reports contract
 * carr-workspace-market-map-route-planning 1.2.0 as
 * `approved_architecture_not_implemented_in_production`. That string is why
 * every admission below is unavailable rather than allowed.
 */
export const V5_J301_MAP_CONTRACT_PRODUCTION_STATUS =
  "approved_architecture_not_implemented_in_production";

export const V5_J301_CONSUMER_GATES = deepFreeze([
  "global-execution-contract-accepted",
  "global-phi-boundary-accepted",
  "global-prompt-injection-boundary-accepted",
  "global-secrets-boundary-accepted",
  "global-source-authority-accepted",
  "journey-three-preactivation-contract-bound",
  "tour-map-contract-1.2.0-accepted",
]);

export const V5_J301_SLICE_EVIDENCE_INPUTS = deepFreeze([
  "step:journey-three-contract-binding-receipt",
  "step:journey-two-production-outcome",
  "step:tour-map-contract-1.2.0-independent-acceptance-receipt",
]);

export const V5_J301_PRODUCTION_OUTCOME_STEP = "step:journey-three-production-outcome";

/**
 * FIELD NAMES THAT TRY TO BE THE GATE. A request carrying any of these is not
 * supplying evidence; it is supplying a verdict, and a verdict from a caller is
 * the exact shape this system refuses. Checked by NAME against the request's
 * own keys before any other policy question is asked.
 */
export const V5_J301_CALLER_AUTHORITY_FIELDS = deepFreeze([
  "gate_receipt", "map_contract_receipt", "receipt", "approved", "admission",
  "allow", "authority", "authorization_class", "actor_authority", "verified",
  "gate_status", "override", "force",
]);

// ---------------------------------------------------------------------------
// The step key. The one derivation in this module that needs no authority at
// all: it is a pure function of what the caller says it is doing, so it can be
// computed honestly even though nothing may be written.
// ---------------------------------------------------------------------------

/**
 * Fold one logical workflow action into a stable key.
 *
 * THE PROPERTY THAT MATTERS: the same logical action produces the same key
 * however many times it is replayed, and any change to the tour, the stage, the
 * action or its subject produces a different one. That is what makes an
 * interrupted stage resumable without a session remembering anything — the
 * journal can be asked whether THIS action already happened.
 *
 * The key deliberately does NOT include a timestamp, an actor or an attempt
 * counter. A retry after a crash is the same logical action by a different
 * clock, and a key that moved with the clock would turn every resume into a
 * duplicate write.
 */
export function tourWorkflowStepKey(request) {
  assertObject(request, "request");
  const keys = ["tour_id", "stage", "action_kind", "action_subject_digest"];
  assertClosedKeys(request, keys, "request");
  assertRequiredKeys(request, keys, "request");
  const stage = assertEnum(request.stage, V5_J301_STAGES, "request.stage", "unknown_stage");
  const actions = V5_J301_STAGE_ACTIONS[stage];
  if (!Object.hasOwn(actions, request.action_kind)) {
    fail("unknown_action_kind",
      `"${String(request.action_kind)}" is not an action of stage "${stage}"`,
      { stage, registered: Object.keys(actions).sort() });
  }
  return digest({
    schema_version: V5_J301_STAGE_ACTION_SCHEMA_VERSION,
    tour_id: assertExternalIdent(request.tour_id, "request.tour_id"),
    stage,
    action_kind: request.action_kind,
    action_subject_digest: assertDigestRef(request.action_subject_digest, "request.action_subject_digest"),
  });
}

// ---------------------------------------------------------------------------
// The journal ENTRY schema. This is the shape the durable stage journal will
// store and the shape its reader will return — declared here because it is part
// of the contract the seam owes, and validated here so a future writer has one
// spelling to conform to.
//
// WHAT THIS SECTION NO LONGER DOES: read a list of these as a HISTORY. Shape
// validation of one entry says nothing about position; a sequence of them read
// for position is a resume classification, and this module makes none.
// ---------------------------------------------------------------------------

const JOURNAL_ENTRY_KEYS = Object.freeze([
  "organization_tenant_id", "tour_id", "assignment_id", "stage", "action_kind",
  "action_subject_digest", "declared_actor_slug", "recorded_at", "corrects_step_key",
]);
const JOURNAL_ENTRY_REQUIRED = Object.freeze([
  "organization_tenant_id", "tour_id", "assignment_id", "stage", "action_kind",
  "action_subject_digest", "declared_actor_slug", "recorded_at",
]);

/**
 * Normalize one caller-supplied journal entry and derive its step key.
 *
 * WHAT THIS IS NOT: an admission that the entry happened. The return carries
 * `authority: "caller_supplied_view"` for exactly that reason. Shape validation
 * is not authentication, and this module never pretends otherwise.
 *
 * EVERY ENTRY NAMES ITS TENANT, TOUR AND ASSIGNMENT, and they are required
 * rather than optional, because an entry that does not say which Tour it
 * belongs to cannot be checked against the one it claims to be part of. The
 * check itself belongs to the journal owner: `evaluateStageAction` reads no
 * journal at all, so there is no answer here for a foreign entry to move.
 */
export function assertTourWorkflowJournalEntry(entry, path = "entry") {
  assertObject(entry, path);
  assertClosedKeys(entry, [...JOURNAL_ENTRY_KEYS], path);
  assertRequiredKeys(entry, [...JOURNAL_ENTRY_REQUIRED], path);
  const stage = assertEnum(entry.stage, V5_J301_STAGES, `${path}.stage`, "unknown_stage");
  const actions = V5_J301_STAGE_ACTIONS[stage];
  if (!Object.hasOwn(actions, entry.action_kind)) {
    fail("unknown_action_kind", `"${String(entry.action_kind)}" is not an action of stage "${stage}"`,
      { path: `${path}.action_kind`, stage, registered: Object.keys(actions).sort() });
  }
  const normalized = {
    schema_version: V5_J301_JOURNAL_ENTRY_SCHEMA_VERSION,
    organization_tenant_id: assertTenant(entry.organization_tenant_id, `${path}.organization_tenant_id`),
    tour_id: assertExternalIdent(entry.tour_id, `${path}.tour_id`),
    assignment_id: assertExternalIdent(entry.assignment_id, `${path}.assignment_id`),
    stage,
    action_kind: entry.action_kind,
    action_subject_digest: assertDigestRef(entry.action_subject_digest, `${path}.action_subject_digest`),
    // DECLARED, and named so at the call site. Attribution carried toward a
    // future journal; never a test this module passes anyone on.
    declared_actor_slug:
      assertExternalIdent(entry.declared_actor_slug, `${path}.declared_actor_slug`, { maxLength: 64 }),
    recorded_at: entry.recorded_at,
    recorded_at_epoch_ms: assertInstant(entry.recorded_at, `${path}.recorded_at`),
    corrects_step_key: entry.corrects_step_key === undefined || entry.corrects_step_key === null
      ? null
      : assertDigestRef(entry.corrects_step_key, `${path}.corrects_step_key`),
    authority: "caller_supplied_view",
  };
  normalized.step_key = tourWorkflowStepKey({
    tour_id: normalized.tour_id,
    stage: normalized.stage,
    action_kind: normalized.action_kind,
    action_subject_digest: normalized.action_subject_digest,
  });
  return deepFreeze(normalized);
}

// ---------------------------------------------------------------------------
// The evaluators. Deny-only, every one of them.
// ---------------------------------------------------------------------------

function refused(reason_id, detail) {
  return deepFreeze({
    schema_version: V5_J301_STAGE_ACTION_SCHEMA_VERSION,
    decision: "refused",
    reason_id,
    ...detail,
    caller_journal_admitted: false,
    journal_read: false,
    governed_state_applied: false,
    effects: V5_NO_EFFECTS,
  });
}

function unavailable(reason_id, owed_seams, detail) {
  return deepFreeze({
    schema_version: V5_J301_STAGE_ACTION_SCHEMA_VERSION,
    decision: "unavailable",
    reason_id,
    owed_seams: [...owed_seams],
    map_contract: V5_J301_MAP_CONTRACT,
    map_contract_version: V5_J301_MAP_CONTRACT_VERSION,
    map_contract_gate: V5_J301_MAP_CONTRACT_GATE,
    map_contract_production_status: V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
    ...detail,
    caller_journal_admitted: false,
    journal_read: false,
    governed_state_applied: false,
    effects: V5_NO_EFFECTS,
  });
}

/** Any key anywhere in a payload that reaches for authority or for lifecycle. */
function scanFieldNames(value, forbidden, path, depth = 0) {
  if (depth > 8) fail("too_deep", `${path} nests deeper than this contract reads`, { path });
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index++) {
      const hit = scanFieldNames(value[index], forbidden, `${path}[${index}]`, depth + 1);
      if (hit) return hit;
    }
    return null;
  }
  if (!isPlainObject(value)) return null;
  for (const key of Object.keys(value)) {
    if (forbidden.includes(key)) return { key, path: `${path}.${key}` };
    const hit = scanFieldNames(value[key], forbidden, `${path}.${key}`, depth + 1);
    if (hit) return hit;
  }
  return null;
}

const STAGE_ACTION_KEYS = Object.freeze([
  "organization_tenant_id", "tour_id", "assignment_id", "stage", "action_kind",
  "declared_actor_slug", "attended_intent", "actor_class", "action_subject_digest",
  "tour_activity", "corrects_step_key", "journal_view",
]);
const STAGE_ACTION_REQUIRED = Object.freeze([
  "organization_tenant_id", "tour_id", "assignment_id", "stage", "action_kind",
  "declared_actor_slug", "attended_intent", "actor_class", "action_subject_digest",
]);

/**
 * Evaluate one intended stage action against the rules this module can check
 * WITHOUT A JOURNAL.
 *
 * THE ORDERED QUESTIONS, in the order they are asked, because a classification
 * without its procedure cannot be checked:
 *
 *   1. Can the request be read at all?          -> throw V5J301Error
 *   2. Does it carry its own authority?          -> refused, caller_supplied_authority_field
 *   3. Does it hand in its own journal?          -> refused, caller_supplied_journal_view_refused
 *   4. Does its Tour activity reach a lifecycle
 *      phase or a Deal axis?                     -> refused, implicit_* (Q072.D2, Q080.D2)
 *   5. Is the intent unattended?                 -> refused, unattended_intake_refused (Q014.D2)
 *   6. Is a model speaking outside its stage?    -> refused, model_outside_declared_seam
 *   7. Does the actor class match the action?    -> refused, actor_class_mismatch
 *   8. Is this an attended action?               -> unavailable, attended_actor_source_unavailable
 *   9. Anything else                             -> unavailable, journal owner does not exist
 *
 * WHY QUESTION 3 EXISTS AND WHY IT COMES THIRD. Questions 4 to 7 read only the
 * REQUEST — its intent, its actor class, its own payload — and their answers
 * are facts about the request. The staging rules of Q060.D1 are different in
 * kind: "this stage skips the one before it", "this is a backward move", "this
 * step key was already recorded" are all facts about a HISTORY, and the only
 * thing that can state a history here is a caller. An earlier shape of this
 * function read that caller history and answered with `stages_seen`,
 * `earliest_unstarted_stage`, `missing_stage` and `furthest_stage_seen`, which
 * together reconstruct the resume boundary — the exact fact no authority in
 * this repository can establish. So the journal is refused BEFORE any of it is
 * read, and no answer below carries a field derived from one.
 *
 * WHERE THE STAGING RULES WENT. Nowhere: they are the journal owner's, and
 * V5_J301_JOURNAL_OWNER_REASON_IDS declares the vocabulary that seam owes so a
 * future implementation has one spelling to conform to. The classification
 * itself is proved against
 * mcp-server/test/tour-workflow-classifiers.v5.testhelper.mjs, in the test
 * tree, conditionally — `would_be_*`, never `is`.
 *
 * THERE IS NO STEP THAT SAYS YES, and the two exits at 8 and 9 are why. A
 * refusal can always be given honestly, because denial needs no authority the
 * repository lacks. Permission does, twice over here: no authenticated actor
 * source can tell this module a human is present (step 8), and no durable
 * journal can tell it where the workflow actually stands (step 9).
 *
 * WHY 8 COMES BEFORE 9. A caller writing `declared_actor_slug: "joe"` and
 * `attended_intent: "human_present_for_this_action"` used to reach the ordinary
 * journal-owner answer, which read as "attendance was satisfied, only the store
 * is missing". It was not satisfied and it cannot be here. The attended exit
 * names the missing authenticator first, and the journal seam rides along in
 * `owed_seams` so neither hole is hidden by the other.
 */
export function evaluateStageAction(request) {
  assertObject(request, "request");
  assertClosedKeys(request, [...STAGE_ACTION_KEYS], "request");
  assertRequiredKeys(request, [...STAGE_ACTION_REQUIRED], "request");
  assertTenant(request.organization_tenant_id, "request.organization_tenant_id");

  const authorityHit = scanFieldNames(request, [...V5_J301_CALLER_AUTHORITY_FIELDS], "request");
  if (authorityHit) {
    return refused("caller_supplied_authority_field", {
      field: authorityHit.key,
      field_path: authorityHit.path,
      owed_seams: [V5_J301_MAP_CONTRACT_RECEIPT_STEP],
    });
  }

  // 3. A JOURNAL IS NOT AN ARGUMENT. Refused before a single entry is read, and
  //    refused for being PRESENT rather than for being malformed — an empty
  //    array is still a caller stating this Tour's history, and reading one to
  //    say "no entries, so you are at stage one" is a resume classification.
  //    Explicit `null` is the one accepted spelling because it states no
  //    history at all; the field is kept in the closed key set on purpose, so
  //    the answer is a nameable refusal rather than an unreadable request.
  if (request.journal_view !== undefined && request.journal_view !== null) {
    return refused("caller_supplied_journal_view_refused", {
      field: "journal_view",
      field_path: "request.journal_view",
      owed_seams: [V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM, V5_J301_WORKFLOW_JOURNAL_READER_SEAM],
      journal_owner_reason_ids: [...V5_J301_JOURNAL_OWNER_REASON_IDS],
    });
  }

  const tour_id = assertExternalIdent(request.tour_id, "request.tour_id");
  const assignment_id = assertExternalIdent(request.assignment_id, "request.assignment_id");
  const stage = assertEnum(request.stage, V5_J301_STAGES, "request.stage", "unknown_stage");
  const actions = V5_J301_STAGE_ACTIONS[stage];
  if (!Object.hasOwn(actions, request.action_kind)) {
    fail("unknown_action_kind", `"${String(request.action_kind)}" is not an action of stage "${stage}"`,
      { stage, registered: Object.keys(actions).sort() });
  }
  const action = actions[request.action_kind];
  // DECLARED, not verified, and the name says so. Nothing below reads it: it is
  // validated for shape, echoed nowhere, and the suite proves every answer is
  // byte-identical across every slug a caller could write here.
  assertExternalIdent(request.declared_actor_slug, "request.declared_actor_slug", { maxLength: 64 });
  const intent = assertEnum(request.attended_intent, V5_J301_INTENTS,
    "request.attended_intent", "unknown_intent");
  const actor_class = assertEnum(request.actor_class, V5_J301_ACTOR_CLASSES,
    "request.actor_class", "unknown_actor_class");
  const action_subject_digest =
    assertDigestRef(request.action_subject_digest, "request.action_subject_digest");
  // SHAPE-CHECKED AND DELIBERATELY UNVERIFIED. Whether the step it names exists
  // is a question about the journal, so it is the journal owner's to answer
  // (`correction_target_absent`). Validating the spelling here keeps one
  // spelling of a step key in the system; it establishes nothing else, and the
  // value is echoed in no answer.
  const corrects_step_key = request.corrects_step_key === undefined || request.corrects_step_key === null
    ? null
    : assertDigestRef(request.corrects_step_key, "request.corrects_step_key");
  const tour_activity = request.tour_activity === undefined || request.tour_activity === null
    ? null
    : assertObject(request.tour_activity, "request.tour_activity");

  const step_key = tourWorkflowStepKey({ tour_id, stage, action_kind: request.action_kind, action_subject_digest });
  const common = { tour_id, assignment_id, stage, action_kind: request.action_kind, step_key };

  // 4. A Tour that carries a lifecycle phase is not recording activity; it is
  //    moving the Assignment or the Deal, which Q072.D2 and Q080.D2 forbid.
  if (tour_activity) {
    const lifecycleHit = scanFieldNames(tour_activity, [...V5_J301_FORBIDDEN_ACTIVITY_FIELDS], "request.tour_activity");
    if (lifecycleHit) {
      const dealAxis = V5_J102_DEAL_AXES.includes(lifecycleHit.key) ||
        lifecycleHit.key === "deal_id" || lifecycleHit.key === "deal" || lifecycleHit.key === "instrument_kind";
      return refused(dealAxis ? "implicit_deal_mutation_refused" : "implicit_assignment_phase_transition_refused", {
        ...common,
        field: lifecycleHit.key,
        field_path: lifecycleHit.path,
      });
    }
  }

  // 5. Attended means a human is present for THIS action.
  if (intent !== V5_J301_ATTENDED_INTENT) {
    return refused("unattended_intake_refused", {
      ...common, attended_intent: intent, accepted_intent: V5_J301_ATTENDED_INTENT,
    });
  }

  // 6. A model may occupy the assembly stage and nothing else. Asked BEFORE the
  //    actor-class match on purpose: "a model reached into the deterministic
  //    stage" and "this action wants a different kind of actor" are different
  //    facts, and the first one deserves its own answer rather than being
  //    folded into the second.
  if (actor_class === "model_assisted" && !V5_J301_MODEL_PERMITTED_STAGES.includes(stage)) {
    return refused("model_outside_declared_seam", {
      ...common, model_permitted_stages: [...V5_J301_MODEL_PERMITTED_STAGES],
    });
  }

  // 7. The actor class the action declares is the one that may perform it. There
  //    is deliberately no seventh question about WHO the actor is: a slug is a
  //    string, and a set lookup over a string is not an authentication.
  if (actor_class !== action.actor_class) {
    return refused("actor_class_mismatch", {
      ...common, actor_class, required_actor_class: action.actor_class,
    });
  }

  // THE STAGING RULES OF Q060.D1 ARE NOT ASKED HERE, and their absence is the
  // point rather than an omission. Every one of them — foreign entry, gap,
  // skip, backward move without a correction, replayed step key — is a question
  // about what really happened, and nothing in this repository can answer that.
  // V5_J301_JOURNAL_OWNER_REASON_IDS is the vocabulary the owner owes; the
  // classification behind it is proved conditionally in the test tree.

  const tail = {
    ...common,
    intended_verb: action.intended_verb,
    // Said on every answer: the verb is NAMED, and nothing here builds a call
    // its inputSchema would accept.
    intended_verb_adapter_bound: false,
    intended_verb_adapter_seam: V5_J301_VERB_ADAPTER_SEAM,
    attended_actor_source_bound: false,
    declared_actor_slug_is_authority: false,
  };

  // 8. An attended action, with no authenticated actor source to say a human
  //    was here. This exit is reached by EVERY attended action, whatever slug
  //    the caller declared, which is the property the suite sweeps.
  if (action.actor_class === "human_attended") {
    return unavailable("attended_actor_source_unavailable",
      [V5_J301_ATTENDED_ACTOR_SOURCE_SEAM, V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM,
        V5_J301_MAP_CONTRACT_RECEIPT_STEP],
      { ...tail, identity_seam_module: "mcp-server/src/identity.js" });
  }

  // 9. Well-formed, rule-abiding, and still not admissible: the durable journal
  //    that owns workflow position does not exist in this repository, and the
  //    map contract has no independent acceptance receipt.
  return unavailable("workflow_journal_owner_unavailable",
    [V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM, V5_J301_MAP_CONTRACT_RECEIPT_STEP], tail);
}

const ACTIVITY_KEYS = Object.freeze([
  "organization_tenant_id", "assignment_id", "tour_id", "activity_kind",
  "declared_actor_slug", "attended_intent", "occurred_at", "activity_payload",
  "corrects_activity_id",
]);
const ACTIVITY_REQUIRED = Object.freeze([
  "organization_tenant_id", "assignment_id", "tour_id", "activity_kind",
  "declared_actor_slug", "attended_intent", "occurred_at",
]);

/**
 * Evaluate a Tour's intended activity record on its Assignment.
 *
 * This is the Q072.D2 / Q080.D2 boundary in one function: an Assignment OWNS
 * governed Tours as a distinct activity, and the Assignment's research, search
 * and negotiation phase and every Deal axis stay independently evidenced. So a
 * request that carries any of them is refused by field NAME, and a request that
 * carries none of them still cannot be admitted, because the durable Assignment
 * activity record does not exist here either — and before that, because no
 * authenticated actor source can say a human was present for it.
 *
 * A correction is an APPEND — Q072.D2's "records any correction with preserved
 * history". A request that names a prior activity carries it as
 * `corrects_activity_id`; there is no field through which a caller can ask for
 * the prior activity to be removed, and an unknown field is refused rather than
 * ignored.
 */
export function evaluateTourAssignmentActivity(request) {
  assertObject(request, "request");
  assertClosedKeys(request, [...ACTIVITY_KEYS], "request");
  assertRequiredKeys(request, [...ACTIVITY_REQUIRED], "request");
  assertTenant(request.organization_tenant_id, "request.organization_tenant_id");

  const authorityHit = scanFieldNames(request, [...V5_J301_CALLER_AUTHORITY_FIELDS], "request");
  if (authorityHit) {
    return deepFreeze({
      schema_version: V5_J301_ACTIVITY_SCHEMA_VERSION,
      decision: "refused",
      reason_id: "caller_supplied_authority_field",
      field: authorityHit.key,
      field_path: authorityHit.path,
      assignment_phase_changed: false,
      deal_created: false,
      governed_state_applied: false,
      effects: V5_NO_EFFECTS,
    });
  }

  const assignment_id = assertExternalIdent(request.assignment_id, "request.assignment_id");
  const tour_id = assertExternalIdent(request.tour_id, "request.tour_id");
  const activity_kind = assertEnum(request.activity_kind, V5_J301_ACTIVITY_KINDS,
    "request.activity_kind", "unknown_activity_kind");
  // DECLARED, not verified, and the name says so. Nothing below reads it: it is
  // validated for shape, echoed nowhere, and the suite proves every answer is
  // byte-identical across every slug a caller could write here.
  assertExternalIdent(request.declared_actor_slug, "request.declared_actor_slug", { maxLength: 64 });
  const intent = assertEnum(request.attended_intent, V5_J301_INTENTS,
    "request.attended_intent", "unknown_intent");
  assertInstant(request.occurred_at, "request.occurred_at");
  const payload = request.activity_payload === undefined || request.activity_payload === null
    ? null
    : assertObject(request.activity_payload, "request.activity_payload");
  const corrects_activity_id =
    request.corrects_activity_id === undefined || request.corrects_activity_id === null
      ? null
      : assertExternalIdent(request.corrects_activity_id, "request.corrects_activity_id");

  const base = {
    schema_version: V5_J301_ACTIVITY_SCHEMA_VERSION,
    assignment_id,
    tour_id,
    activity_kind,
    // Stated on EVERY answer, success and refusal alike, so a reader never has
    // to infer it from the absence of a field.
    assignment_phase_changed: false,
    deal_created: false,
    preserves_history: true,
    governed_state_applied: false,
    effects: V5_NO_EFFECTS,
  };

  if (payload) {
    const hit = scanFieldNames(payload, [...V5_J301_FORBIDDEN_ACTIVITY_FIELDS], "request.activity_payload");
    if (hit) {
      const dealAxis = V5_J102_DEAL_AXES.includes(hit.key) ||
        hit.key === "deal_id" || hit.key === "deal" || hit.key === "instrument_kind";
      return deepFreeze({
        ...base,
        decision: "refused",
        reason_id: dealAxis ? "implicit_deal_mutation_refused" : "implicit_assignment_phase_transition_refused",
        field: hit.key,
        field_path: hit.path,
      });
    }
  }

  if (intent !== V5_J301_ATTENDED_INTENT) {
    return deepFreeze({
      ...base,
      decision: "refused",
      reason_id: "unattended_intake_refused",
      attended_intent: intent,
      accepted_intent: V5_J301_ATTENDED_INTENT,
    });
  }
  if (activity_kind === "tour_corrected" && corrects_activity_id === null) {
    return deepFreeze({
      ...base, decision: "refused", reason_id: "correction_must_name_its_target",
    });
  }
  if (activity_kind !== "tour_corrected" && corrects_activity_id !== null) {
    return deepFreeze({
      ...base, decision: "refused", reason_id: "only_a_correction_may_name_a_prior_activity",
    });
  }

  // EVERY Tour activity is an attended one — all four activity kinds record
  // something a human did on a tour — so this function has exactly one exit and
  // it names the missing authenticator before the missing store.
  return deepFreeze({
    ...base,
    decision: "unavailable",
    reason_id: "attended_actor_source_unavailable",
    owed_seams: [V5_J301_ATTENDED_ACTOR_SOURCE_SEAM, V5_J301_ASSIGNMENT_ACTIVITY_OWNER_SEAM,
      V5_J301_MAP_CONTRACT_RECEIPT_STEP],
    identity_seam_module: "mcp-server/src/identity.js",
    attended_actor_source_bound: false,
    declared_actor_slug_is_authority: false,
    map_contract: V5_J301_MAP_CONTRACT,
    map_contract_version: V5_J301_MAP_CONTRACT_VERSION,
    map_contract_gate: V5_J301_MAP_CONTRACT_GATE,
    map_contract_production_status: V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
  });
}

// ---------------------------------------------------------------------------
// The public resume path.
// ---------------------------------------------------------------------------

/**
 * Where does this Tour's workflow stand?
 *
 * THE ANSWER IS UNAVAILABLE, ALWAYS, AND IT IS THE SAME ANSWER FOR EVERY INPUT.
 * A resume point is a fact about what really happened, and the only thing that
 * could know it is a durable journal read by something this module trusts.
 * There is no such table and no such reader here, so this function names both
 * seams and answers nothing else. It deliberately does not look at its argument
 * at all: a function that READ a caller-supplied journal and reported a position
 * would be letting the caller choose where its own workflow resumes, which is
 * the whole defect this path exists to avoid.
 *
 * The suite proves the answer is byte-identical across every shape a caller can
 * pass, including no argument at all.
 */
export function readTourWorkflowResumePoint() {
  return deepFreeze({
    schema_version: V5_J301_RESUME_SCHEMA_VERSION,
    policy_version: V5_J301_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision: "unavailable",
    reason_id: "workflow_journal_reader_unavailable",
    owed_seams: [V5_J301_WORKFLOW_JOURNAL_READER_SEAM, V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM],
    journal_reader_bound: false,
    journal_owner_exists_here: false,
    request_read: false,
    caller_journal_admitted: false,
    resume_stage: null,
    next_stage: null,
    governed_state_applied: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// What this slice does not do, said out loud.
// ---------------------------------------------------------------------------

export function tourWorkflowGaps() {
  return deepFreeze({
    stage_journal_owner_exists_here: false,
    stage_journal_owner_seam: V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM,
    assignment_activity_owner_exists_here: false,
    assignment_activity_owner_seam: V5_J301_ASSIGNMENT_ACTIVITY_OWNER_SEAM,
    journal_reader_exists_here: false,
    journal_reader_seam: V5_J301_WORKFLOW_JOURNAL_READER_SEAM,
    caller_journal_accepted_here: false,
    staging_rules_enforced_here: false,
    journal_owner_reason_ids: [...V5_J301_JOURNAL_OWNER_REASON_IDS],
    attended_actor_source_exists_here: false,
    attended_actor_source_seam: V5_J301_ATTENDED_ACTOR_SOURCE_SEAM,
    identity_seam_module: "mcp-server/src/identity.js",
    verb_adapter_exists_here: false,
    verb_adapter_seam: V5_J301_VERB_ADAPTER_SEAM,
    intended_verbs_are_named_not_traversed: true,
    map_contract_receipt_exists_here: false,
    map_contract_receipt_step: V5_J301_MAP_CONTRACT_RECEIPT_STEP,
    map_contract_production_status: V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
    // The three surfaces this slice was told to leave alone, listed so nobody
    // reads their absence as an oversight.
    public_projection_here: false,
    share_grant_issuance_here: false,
    pdf_render_request_here: false,
    advance_reachable_here: false,
    advance_reason_id: "workflow_journal_owner_unavailable",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The policy digest. One preimage over everything above that a reader could
// disagree about, so drift is visible as a moved hash rather than as a surprise.
// ---------------------------------------------------------------------------

export function v5J301PolicyPreimage() {
  return deepFreeze({
    schema_version: V5_J301_SCHEMA_VERSION,
    policy_version: V5_J301_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    settled_decisions: Object.fromEntries(
      V5_J301_SETTLED_DECISION_IDS.map(id => [id, { ...V5_J301_SETTLED_DECISIONS[id] }])),
    stages: [...V5_J301_STAGES],
    stage_actions: Object.fromEntries(V5_J301_STAGES.map(stage => [
      stage,
      Object.fromEntries(Object.keys(V5_J301_STAGE_ACTIONS[stage]).sort()
        .map(kind => [kind, { ...V5_J301_STAGE_ACTIONS[stage][kind] }])),
    ])),
    action_kinds: [...V5_J301_ACTION_KINDS],
    intended_verbs: [...V5_J301_INTENDED_VERBS],
    actor_classes: [...V5_J301_ACTOR_CLASSES],
    model_permitted_stages: [...V5_J301_MODEL_PERMITTED_STAGES],
    attended_intent: V5_J301_ATTENDED_INTENT,
    refused_intents: [...V5_J301_REFUSED_INTENTS],
    decisions: [...V5_J301_DECISIONS],
    forbidden_activity_fields: [...V5_J301_FORBIDDEN_ACTIVITY_FIELDS],
    assignment_phases: [...V5_J301_ASSIGNMENT_PHASES],
    activity_kinds: [...V5_J301_ACTIVITY_KINDS],
    caller_authority_fields: [...V5_J301_CALLER_AUTHORITY_FIELDS],
    workflow_journal_owner_seam: V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM,
    workflow_journal_reader_seam: V5_J301_WORKFLOW_JOURNAL_READER_SEAM,
    journal_owner_reason_ids: [...V5_J301_JOURNAL_OWNER_REASON_IDS],
    attended_actor_source_seam: V5_J301_ATTENDED_ACTOR_SOURCE_SEAM,
    verb_adapter_seam: V5_J301_VERB_ADAPTER_SEAM,
    assignment_activity_owner_seam: V5_J301_ASSIGNMENT_ACTIVITY_OWNER_SEAM,
    map_contract: V5_J301_MAP_CONTRACT,
    map_contract_version: V5_J301_MAP_CONTRACT_VERSION,
    map_contract_gate: V5_J301_MAP_CONTRACT_GATE,
    map_contract_receipt_step: V5_J301_MAP_CONTRACT_RECEIPT_STEP,
    map_contract_production_status: V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
    consumer_gates: [...V5_J301_CONSUMER_GATES],
    slice_evidence_inputs: [...V5_J301_SLICE_EVIDENCE_INPUTS],
    production_outcome_step: V5_J301_PRODUCTION_OUTCOME_STEP,
  });
}

export function v5J301PolicyCanonicalBytes() {
  return canonicalJson(v5J301PolicyPreimage());
}

export function v5J301PolicyDigest() {
  return digest(v5J301PolicyPreimage());
}

/**
 * The whole slice as one readable projection, for an operator surface that
 * wants to show what the Tour workflow will and will not do before anyone turns
 * it on. Read OFF the gaps projection rather than asserted beside it, so the
 * two cannot drift apart.
 */
export function v5J301TourWorkflowProjection() {
  const gaps = tourWorkflowGaps();
  return deepFreeze({
    schema_version: V5_J301_PROJECTION_SCHEMA_VERSION,
    policy_digest: v5J301PolicyDigest(),
    policy_version: V5_J301_POLICY_VERSION,
    settled_decision_ids: [...V5_J301_SETTLED_DECISION_IDS],
    stages: [...V5_J301_STAGES],
    stages_are_separate_and_ordered: true,
    human_presence_required_at_intake: true,
    // The requirement is real and the PROOF of it is missing, which are two
    // different facts and both are stated.
    human_presence_provable_here: false,
    attended_actions_reachable_today: false,
    attended_actions_reason_id: "attended_actor_source_unavailable",
    attended_actor_source_seam: V5_J301_ATTENDED_ACTOR_SOURCE_SEAM,
    declared_actor_slug_is_authority: false,
    accepted_intent: V5_J301_ATTENDED_INTENT,
    refused_intents: [...V5_J301_REFUSED_INTENTS],
    model_permitted_stages: [...V5_J301_MODEL_PERMITTED_STAGES],
    tour_may_change_assignment_phase: false,
    tour_may_create_or_execute_deal: false,
    resume_state_read_from: "durable_record_only",
    resume_state_read_from_session_memory: false,
    resume_reachable_today: gaps.advance_reachable_here,
    resume_reason_id: gaps.advance_reason_id,
    map_contract: `${V5_J301_MAP_CONTRACT} ${V5_J301_MAP_CONTRACT_VERSION}`,
    map_contract_gate: V5_J301_MAP_CONTRACT_GATE,
    map_contract_production_status: gaps.map_contract_production_status,
    gaps,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * THE DECLARED PUBLIC SURFACE.
 *
 * Every name a consumer may import from this module. The suite enumerates the
 * module's real exports through the loader and refuses any name that is not on
 * this list — with one deliberate exception below, which is not on it.
 */
export const V5_J301_PUBLIC_SURFACE = deepFreeze([
  "V5J301Error",
  "V5_J301_ACTION_KINDS",
  "V5_J301_ACTIVITY_KINDS",
  "V5_J301_ACTIVITY_SCHEMA_VERSION",
  "V5_J301_ACTOR_CLASSES",
  "V5_J301_ASSIGNMENT_ACTIVITY_OWNER_SEAM",
  "V5_J301_ASSIGNMENT_PHASES",
  "V5_J301_ATTENDED_ACTOR_SOURCE_SEAM",
  "V5_J301_ATTENDED_INTENT",
  "V5_J301_CALLER_AUTHORITY_FIELDS",
  "V5_J301_CONSUMER_GATES",
  "V5_J301_DECISIONS",
  "V5_J301_FORBIDDEN_ACTIVITY_FIELDS",
  "V5_J301_INTENTS",
  "V5_J301_JOURNAL_ENTRY_SCHEMA_VERSION",
  "V5_J301_JOURNAL_OWNER_REASON_IDS",
  "V5_J301_MAP_CONTRACT",
  "V5_J301_MAP_CONTRACT_GATE",
  "V5_J301_MAP_CONTRACT_PRODUCTION_STATUS",
  "V5_J301_MAP_CONTRACT_RECEIPT_STEP",
  "V5_J301_MAP_CONTRACT_VERSION",
  "V5_J301_MODEL_PERMITTED_STAGES",
  "V5_J301_POLICY_VERSION",
  "V5_J301_PRODUCTION_OUTCOME_STEP",
  "V5_J301_PROJECTION_SCHEMA_VERSION",
  "V5_J301_PUBLIC_SURFACE",
  "V5_J301_RESUME_SCHEMA_VERSION",
  "V5_J301_REFUSED_INTENTS",
  "V5_J301_SCHEMA_VERSION",
  "V5_J301_SETTLED_DECISIONS",
  "V5_J301_SETTLED_DECISION_IDS",
  "V5_J301_SLICE_EVIDENCE_INPUTS",
  "V5_J301_STAGES",
  "V5_J301_STAGE_ACTIONS",
  "V5_J301_STAGE_ACTION_SCHEMA_VERSION",
  "V5_J301_STAGE_INDEX",
  "V5_J301_VERB_ADAPTER_SEAM",
  "V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM",
  "V5_J301_WORKFLOW_JOURNAL_READER_SEAM",
  "V5_J301_INTENDED_VERBS",
  "V5_NO_EFFECTS",
  "assertJ301DecisionBinding",
  "assertTourWorkflowJournalEntry",
  "evaluateStageAction",
  "evaluateTourAssignmentActivity",
  "readTourWorkflowResumePoint",
  "tourWorkflowGaps",
  "tourWorkflowStepKey",
  "v5J301PolicyCanonicalBytes",
  "v5J301PolicyDigest",
  "v5J301PolicyPreimage",
  "v5J301TourWorkflowProjection",
]);
