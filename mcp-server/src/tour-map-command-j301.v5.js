// DoctorCRE v5 slice V5-J301 — the typed map-command contract, and the proof
// that a finger on a map and a sentence to Doc are the SAME command.
//
// This is the Q124.D2 half of the slice:
//
//   "In Journey 3, clickable Tour maps and Doc must invoke the same typed map
//    commands and yield equivalent governed coordinate, route, selection, and
//    navigation state under carr-map-tour-v1 1.2.0."
//
// HOW THAT IS MADE TRUE HERE RATHER THAN PROMISED. Both origins are normalized
// into one envelope over one closed command registry, and the ORIGIN IS NOT
// PART OF THE COMMAND DIGEST. A map click and a Doc sentence that mean the same
// thing therefore produce byte-identical canonical arguments and the same
// digest; anything that differs in a governed argument produces a different
// digest and is reported as a divergence, by argument name. Equivalence is a
// statement about two things the caller supplied, and this module never lets it
// become a statement that either of them may run: `admission` is `unavailable`
// on every envelope and every comparison.
//
// EVERY COMMAND NAMES THE DEPLOYED VERB ITS WRITE MUST TRAVERSE. That is what
// "no bypass" means in a system where the record layer already owns the writes:
// there is no second path to governed state, so an equivalent command from
// either origin reaches the same handler under the same idempotency envelope.
// The suite checks each named verb against the deployed registry in tools.js,
// so a renamed verb fails here rather than at a caller. This file adds no verb,
// rewrites no Tour module, and issues nothing.
//
// THE FOUR AXES ARE Q124.D2'S OWN LIST — coordinate, route, selection,
// navigation — and the registry has exactly one command family per axis. A
// fifth axis is not an omission to be patched in silently; it would be a
// contract change, and the policy digest below moves when it happens.
//
// WHAT IS REFUSED OUTRIGHT, BY NAME:
//   * a command issued against Journey 1 — Q124.D2 splits J1 (inert later
//     surfaces) from J3 (map commands behind the mandatory map contract), and
//     an ACTIVE J1 map command is named as a failure in its own acceptance
//     predicate;
//   * a caller-supplied gate receipt, approval or override field. Here that
//     refusal is the CLOSED SCHEMA doing it rather than a name scan: a map
//     command has exactly the arguments its registry entry declares and the
//     request has exactly five keys, so `gate_receipt` or `approved` is an
//     unknown field at either level and cannot be read at all. (The workflow
//     module scans by name instead, because its Tour activity payload is open
//     by nature and a closed schema cannot reach inside one.) The suite walks
//     the whole authority-field list from both positions to prove it;
//   * navigation handoff without the human promotion receipt the map doctrine
//     requires — which does not exist here, so it is unavailable rather than
//     performed.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_J301_MAP_CONTRACT,
  V5_J301_MAP_CONTRACT_GATE,
  V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
  V5_J301_MAP_CONTRACT_RECEIPT_STEP,
  V5_J301_MAP_CONTRACT_VERSION,
  V5_J301_SETTLED_DECISIONS,
} from "./tour-workflow-j301.v5.js";

export { V5_NO_EFFECTS };

export const V5_J301_COMMAND_SCHEMA_VERSION = "doctorcre-v5-j301-map-command.v1";
export const V5_J301_COMMAND_POLICY_VERSION = 1;
export const V5_J301_COMMAND_ENVELOPE_SCHEMA_VERSION =
  "doctorcre-v5-j301-map-command-envelope.v1";
export const V5_J301_COMMAND_EQUIVALENCE_SCHEMA_VERSION =
  "doctorcre-v5-j301-map-command-equivalence.v1";
export const V5_J301_COMMAND_PROJECTION_SCHEMA_VERSION =
  "doctorcre-v5-j301-map-command-projection.v1";

// ---------------------------------------------------------------------------
// Local primitives. Deliberately this module's own, for the same reason the
// sibling modules keep theirs: a shared assertion library is a place one
// module's floor can be lowered by editing another's.
// ---------------------------------------------------------------------------

const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;

export class V5J301CommandError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J301CommandError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J301CommandError(code, message, detail);
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

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

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

function assertSafeText(value, path, { maxLength = 256 } = {}) {
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

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"`,
      { path, expected: ORGANIZATION_TENANT_ID });
  }
  return value;
}

/**
 * Latitudes and longitudes are checked as NUMBERS in range, and a coordinate
 * that arrives as a string is refused rather than coerced. "-80.1" and -80.1
 * are different bytes and would produce different digests, which would make two
 * identical commands look divergent — the exact failure this module exists to
 * make impossible.
 */
function assertLatitude(value, path) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < -90 || value > 90) {
    fail("invalid_coordinate", `${path} must be a finite latitude between -90 and 90`, { path });
  }
  return value;
}

function assertLongitude(value, path) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < -180 || value > 180) {
    fail("invalid_coordinate", `${path} must be a finite longitude between -180 and 180`, { path });
  }
  return value;
}

function assertPositiveInteger(value, path, { max = 2147483647 } = {}) {
  if (!Number.isSafeInteger(value) || value < 1 || value > max) {
    fail("invalid_shape", `${path} must be an integer between 1 and ${max}`, { path });
  }
  return value;
}

function assertIdentList(value, path, { min = 1, max = 64 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min || value.length > max) {
    fail("invalid_shape", `${path} must hold between ${min} and ${max} entries`,
      { path, length: value.length });
  }
  const seen = new Set();
  return value.map((entry, index) => {
    const ident = assertExternalIdent(entry, `${path}[${index}]`);
    if (seen.has(ident)) {
      fail("duplicate_entry", `${path}[${index}] repeats "${ident}"`, { path, value: ident });
    }
    seen.add(ident);
    return ident;
  });
}

// ---------------------------------------------------------------------------
// The closed vocabularies.
// ---------------------------------------------------------------------------

/** The two origins Q124.D2 names. A third origin is a contract change. */
export const V5_J301_COMMAND_ORIGINS = deepFreeze(["clickable_map", "doc_command"]);

/** Q124.D2 splits J1 from J3. A map command belongs to Journey 3 and nowhere else. */
export const V5_J301_COMMAND_JOURNEYS = deepFreeze(["product_journey_1", "product_journey_3"]);
export const V5_J301_COMMAND_PERMITTED_JOURNEY = "product_journey_3";

/** Q124.D2's own four axes of governed state. */
export const V5_J301_COMMAND_AXES = deepFreeze(["coordinate", "route", "selection", "navigation"]);

/** The coordinate roles the map doctrine's entrance-verification rule admits. */
export const V5_J301_POSITION_ROLES = deepFreeze([
  "entrance", "driveway", "parking_access", "start", "end",
]);

/** Native navigation platforms. The handoff target, never the route authority. */
export const V5_J301_NAVIGATION_PLATFORMS = deepFreeze(["apple_maps", "google_maps"]);
export const V5_J301_TRAVEL_MODES = deepFreeze(["driving", "walking"]);

const ARGUMENT_CONTRACTS = Object.freeze({
  property_id: (value, path) => assertExternalIdent(value, path),
  tour_id: (value, path) => assertExternalIdent(value, path),
  route_version_id: (value, path) => assertExternalIdent(value, path),
  base_route_version_id: (value, path) => assertExternalIdent(value, path),
  route_stop_id: (value, path) => assertExternalIdent(value, path),
  expected_route_version: (value, path) => assertPositiveInteger(value, path),
  ordered_route_stop_ids: (value, path) => assertIdentList(value, path),
  selected_property_ids: (value, path) => assertIdentList(value, path),
  latitude: (value, path) => assertLatitude(value, path),
  longitude: (value, path) => assertLongitude(value, path),
  position_role: (value, path) => assertEnum(value, V5_J301_POSITION_ROLES, path, "unknown_position_role"),
  platform: (value, path) => assertEnum(value, V5_J301_NAVIGATION_PLATFORMS, path, "unknown_platform"),
  travel_mode: (value, path) => assertEnum(value, V5_J301_TRAVEL_MODES, path, "unknown_travel_mode"),
});

/**
 * THE COMMAND REGISTRY. One family per axis, each naming the deployed
 * record-layer verb its governed write must traverse and the exact arguments
 * that make up its identity.
 *
 * `governed_arguments` is the closed list that goes into the digest. Nothing
 * else does — not the origin, not a session id, not a device, not a timestamp.
 * That omission IS the equivalence property: two origins expressing the same
 * intent cannot differ in anything the digest reads.
 */
export const V5_J301_MAP_COMMANDS = deepFreeze({
  set_entrance_coordinate: {
    axis: "coordinate",
    writes_through_verb: "append-tour-coordinate-candidate",
    governed_arguments: ["property_id", "latitude", "longitude", "position_role"],
    requires_human_promotion_receipt: false,
  },
  reorder_route_stops: {
    axis: "route",
    writes_through_verb: "prepare-tour-route-version",
    governed_arguments: ["tour_id", "base_route_version_id", "expected_route_version", "ordered_route_stop_ids"],
    requires_human_promotion_receipt: false,
  },
  set_selection_cart: {
    axis: "selection",
    writes_through_verb: "append-tour-selection-cart-version",
    governed_arguments: ["tour_id", "selected_property_ids"],
    requires_human_promotion_receipt: false,
  },
  hand_off_native_navigation: {
    axis: "navigation",
    writes_through_verb: "record-tour-map-promotion-receipt",
    governed_arguments: ["route_version_id", "route_stop_id", "platform", "travel_mode"],
    // The map doctrine's promotion gate: exact approved coordinates reach native
    // navigation only behind a human promotion receipt. There is no such receipt
    // store in this repository, so this command is unavailable rather than done.
    requires_human_promotion_receipt: true,
  },
});

export const V5_J301_COMMAND_NAMES = deepFreeze(Object.keys(V5_J301_MAP_COMMANDS).sort());

export const V5_J301_COMMAND_VERBS = deepFreeze(
  [...new Set(V5_J301_COMMAND_NAMES.map(name => V5_J301_MAP_COMMANDS[name].writes_through_verb))].sort());

export const V5_J301_HUMAN_PROMOTION_RECEIPT_SEAM =
  "seam:v5-j301-human-map-promotion-receipt-store";

/** The two answers this module can give. There is deliberately no third. */
export const V5_J301_COMMAND_DECISIONS = deepFreeze(["refused", "unavailable"]);

// ---------------------------------------------------------------------------
// Normalization.
// ---------------------------------------------------------------------------

const REQUEST_KEYS = Object.freeze([
  "organization_tenant_id", "journey", "origin", "command", "arguments",
]);

/**
 * Normalize one map command from either origin into the shared envelope.
 *
 * The ordered questions:
 *   1. Can the request be read?                      -> throw
 *   2. Does it carry its own authority, at either
 *      level?                                        -> throw unknown_field (the closed schema)
 *   3. Is it an ACTIVE Journey 1 map command?         -> throw j1_map_command_refused (Q124.D2)
 *   4. Is the command registered?                     -> throw unknown_command
 *   5. Are its governed arguments exactly right?      -> throw missing/unknown_field
 *   6. Otherwise                                      -> an envelope whose admission is unavailable
 *
 * WHAT THE ENVELOPE IS NOT: an admitted command. `admission` is `unavailable`
 * and `governed_state_applied` is false on every envelope this function can
 * produce, because the map contract's independent acceptance receipt does not
 * exist in this repository.
 */
export function normalizeMapCommand(request) {
  assertObject(request, "request");
  assertClosedKeys(request, [...REQUEST_KEYS], "request");
  assertRequiredKeys(request, [...REQUEST_KEYS], "request");
  assertTenant(request.organization_tenant_id, "request.organization_tenant_id");

  const journey = assertEnum(request.journey, V5_J301_COMMAND_JOURNEYS, "request.journey", "unknown_journey");
  if (journey !== V5_J301_COMMAND_PERMITTED_JOURNEY) {
    fail("j1_map_command_refused",
      "Q124.D2 keeps map commands inside Journey 3; an active Journey 1 map command fails",
      { journey, permitted_journey: V5_J301_COMMAND_PERMITTED_JOURNEY });
  }
  const origin = assertEnum(request.origin, V5_J301_COMMAND_ORIGINS, "request.origin", "unknown_origin");
  const command = assertEnum(request.command, V5_J301_COMMAND_NAMES, "request.command", "unknown_command");
  const contract = V5_J301_MAP_COMMANDS[command];

  const supplied = assertObject(request.arguments, "request.arguments");
  assertClosedKeys(supplied, [...contract.governed_arguments], "request.arguments");
  assertRequiredKeys(supplied, [...contract.governed_arguments], "request.arguments");
  // Built in the contract's own argument order, then canonicalized, so the two
  // origins cannot differ merely by the order their arguments arrived in.
  const governed_arguments = {};
  for (const name of contract.governed_arguments) {
    governed_arguments[name] = ARGUMENT_CONTRACTS[name](supplied[name], `request.arguments.${name}`);
  }

  const command_preimage = {
    schema_version: V5_J301_COMMAND_SCHEMA_VERSION,
    policy_version: V5_J301_COMMAND_POLICY_VERSION,
    map_contract: V5_J301_MAP_CONTRACT,
    map_contract_version: V5_J301_MAP_CONTRACT_VERSION,
    journey,
    command,
    axis: contract.axis,
    writes_through_verb: contract.writes_through_verb,
    governed_arguments,
    // NOTE THE ABSENCE: no origin, no session, no device, no clock. That is the
    // equivalence property, written as an omission rather than as a promise.
  };

  return deepFreeze({
    schema_version: V5_J301_COMMAND_ENVELOPE_SCHEMA_VERSION,
    command,
    axis: contract.axis,
    journey,
    origin,
    writes_through_verb: contract.writes_through_verb,
    governed_arguments,
    command_preimage,
    command_digest: digest(command_preimage),
    command_canonical_bytes_length: canonicalJson(command_preimage).length,
    requires_human_promotion_receipt: contract.requires_human_promotion_receipt,
    admission: "unavailable",
    admission_reason_id: "map_contract_receipt_unavailable",
    governed_state_applied: false,
    effects: V5_NO_EFFECTS,
  });
}

const ENVELOPE_SHAPE_KEYS = Object.freeze([
  "command", "axis", "journey", "origin", "writes_through_verb", "governed_arguments",
]);

function assertEnvelope(envelope, path) {
  assertObject(envelope, path);
  for (const key of ENVELOPE_SHAPE_KEYS) {
    if (!(key in envelope)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
  if (envelope.schema_version !== V5_J301_COMMAND_ENVELOPE_SCHEMA_VERSION) {
    fail("unknown_envelope_schema",
      `${path}.schema_version must be "${V5_J301_COMMAND_ENVELOPE_SCHEMA_VERSION}"`, { path });
  }
  // Re-derived rather than trusted: a caller that hands in an envelope with an
  // edited digest gets the digest its own arguments actually produce.
  const rederived = normalizeMapCommand({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    journey: envelope.journey,
    origin: envelope.origin,
    command: envelope.command,
    arguments: envelope.governed_arguments,
  });
  return rederived;
}

/**
 * Compare one command expressed from each origin.
 *
 * THIS IS A STATEMENT ABOUT TWO THINGS THE CALLER SUPPLIED, and nothing else.
 * `equivalent` says the two envelopes mean the same command; it does not say
 * either of them may run, and `admission` stays `unavailable` on the result so
 * that no reader can take it for a grant.
 *
 * Both envelopes are RE-DERIVED from their own governed arguments before the
 * comparison, so an envelope carrying a hand-edited digest is compared on what
 * it actually says rather than on what it claims.
 */
export function compareMapCommandOrigins(request) {
  assertObject(request, "request");
  assertClosedKeys(request, ["clickable_map", "doc_command"], "request");
  assertRequiredKeys(request, ["clickable_map", "doc_command"], "request");

  const left = assertEnvelope(request.clickable_map, "request.clickable_map");
  const right = assertEnvelope(request.doc_command, "request.doc_command");
  if (left.origin !== "clickable_map") {
    fail("origin_mismatch", 'request.clickable_map must carry origin "clickable_map"',
      { origin: left.origin });
  }
  if (right.origin !== "doc_command") {
    fail("origin_mismatch", 'request.doc_command must carry origin "doc_command"',
      { origin: right.origin });
  }

  const divergences = [];
  if (left.command !== right.command) {
    divergences.push({ field: "command", clickable_map: left.command, doc_command: right.command });
  } else {
    for (const name of V5_J301_MAP_COMMANDS[left.command].governed_arguments) {
      const a = canonicalJson(left.governed_arguments[name]);
      const b = canonicalJson(right.governed_arguments[name]);
      if (a !== b) {
        divergences.push({
          field: name,
          clickable_map: left.governed_arguments[name],
          doc_command: right.governed_arguments[name],
        });
      }
    }
  }
  const digestsMatch = left.command_digest === right.command_digest;

  return deepFreeze({
    schema_version: V5_J301_COMMAND_EQUIVALENCE_SCHEMA_VERSION,
    equivalent: divergences.length === 0 && digestsMatch,
    command_digests_match: digestsMatch,
    clickable_map_command_digest: left.command_digest,
    doc_command_command_digest: right.command_digest,
    traverses_same_verb: left.writes_through_verb === right.writes_through_verb,
    writes_through_verb: left.writes_through_verb === right.writes_through_verb
      ? left.writes_through_verb : null,
    divergences,
    // Said on every comparison, equivalent or not: this is a reading of two
    // requests, not a decision about either.
    admission: "unavailable",
    admission_reason_id: "map_contract_receipt_unavailable",
    owed_seams: [V5_J301_MAP_CONTRACT_RECEIPT_STEP],
    governed_state_applied: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Ask whether a normalized command may be admitted against the map contract.
 *
 * The answer is always no, and there are exactly two shapes of no. A navigation
 * handoff is refused the promotion receipt it needs (the store does not exist);
 * everything else is unavailable because
 * step:tour-map-contract-1.2.0-independent-acceptance-receipt has no
 * implementation here and the live map-architecture verb reports the contract
 * as approved architecture, not implemented in production.
 *
 * There is no argument, field or flag through which a caller can obtain a
 * third answer. A request that tries to supply one is refused by field name.
 */
export function evaluateMapCommandAdmission(request) {
  assertObject(request, "request");
  assertClosedKeys(request, ["envelope"], "request");
  assertRequiredKeys(request, ["envelope"], "request");
  const envelope = assertEnvelope(request.envelope, "request.envelope");
  const contract = V5_J301_MAP_COMMANDS[envelope.command];

  const base = {
    schema_version: V5_J301_COMMAND_SCHEMA_VERSION,
    command: envelope.command,
    axis: envelope.axis,
    origin: envelope.origin,
    command_digest: envelope.command_digest,
    writes_through_verb: envelope.writes_through_verb,
    map_contract: V5_J301_MAP_CONTRACT,
    map_contract_version: V5_J301_MAP_CONTRACT_VERSION,
    map_contract_gate: V5_J301_MAP_CONTRACT_GATE,
    map_contract_production_status: V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
    governed_state_applied: false,
    effects: V5_NO_EFFECTS,
  };

  if (contract.requires_human_promotion_receipt) {
    return deepFreeze({
      ...base,
      decision: "refused",
      reason_id: "human_promotion_receipt_unavailable",
      owed_seams: [V5_J301_HUMAN_PROMOTION_RECEIPT_SEAM, V5_J301_MAP_CONTRACT_RECEIPT_STEP],
    });
  }
  return deepFreeze({
    ...base,
    decision: "unavailable",
    reason_id: "map_contract_receipt_unavailable",
    owed_seams: [V5_J301_MAP_CONTRACT_RECEIPT_STEP],
  });
}

// ---------------------------------------------------------------------------
// Digest and projection.
// ---------------------------------------------------------------------------

export function v5J301CommandPolicyPreimage() {
  return deepFreeze({
    schema_version: V5_J301_COMMAND_SCHEMA_VERSION,
    policy_version: V5_J301_COMMAND_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    // The one decision this module implements, carried verbatim from the
    // workflow module's reviewed copy rather than retyped beside it.
    settled_decision: { "Q124.D2": { ...V5_J301_SETTLED_DECISIONS["Q124.D2"] } },
    origins: [...V5_J301_COMMAND_ORIGINS],
    journeys: [...V5_J301_COMMAND_JOURNEYS],
    permitted_journey: V5_J301_COMMAND_PERMITTED_JOURNEY,
    axes: [...V5_J301_COMMAND_AXES],
    commands: Object.fromEntries(V5_J301_COMMAND_NAMES.map(name => [name, {
      ...V5_J301_MAP_COMMANDS[name],
      governed_arguments: [...V5_J301_MAP_COMMANDS[name].governed_arguments],
    }])),
    command_verbs: [...V5_J301_COMMAND_VERBS],
    position_roles: [...V5_J301_POSITION_ROLES],
    navigation_platforms: [...V5_J301_NAVIGATION_PLATFORMS],
    travel_modes: [...V5_J301_TRAVEL_MODES],
    decisions: [...V5_J301_COMMAND_DECISIONS],
    human_promotion_receipt_seam: V5_J301_HUMAN_PROMOTION_RECEIPT_SEAM,
    map_contract: V5_J301_MAP_CONTRACT,
    map_contract_version: V5_J301_MAP_CONTRACT_VERSION,
    map_contract_gate: V5_J301_MAP_CONTRACT_GATE,
    map_contract_receipt_step: V5_J301_MAP_CONTRACT_RECEIPT_STEP,
    map_contract_production_status: V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
  });
}

export function v5J301CommandPolicyCanonicalBytes() {
  return canonicalJson(v5J301CommandPolicyPreimage());
}

export function v5J301CommandPolicyDigest() {
  return digest(v5J301CommandPolicyPreimage());
}

export function v5J301MapCommandProjection() {
  return deepFreeze({
    schema_version: V5_J301_COMMAND_PROJECTION_SCHEMA_VERSION,
    policy_digest: v5J301CommandPolicyDigest(),
    policy_version: V5_J301_COMMAND_POLICY_VERSION,
    settled_decision_id: "Q124.D2",
    origins: [...V5_J301_COMMAND_ORIGINS],
    origin_is_part_of_command_identity: false,
    commands: [...V5_J301_COMMAND_NAMES],
    axes: [...V5_J301_COMMAND_AXES],
    every_command_traverses_a_deployed_verb: true,
    command_verbs: [...V5_J301_COMMAND_VERBS],
    journey_one_map_commands_permitted: false,
    navigation_handoff_reachable_today: false,
    navigation_handoff_reason_id: "human_promotion_receipt_unavailable",
    admission_reachable_today: false,
    admission_reason_id: "map_contract_receipt_unavailable",
    map_contract: `${V5_J301_MAP_CONTRACT} ${V5_J301_MAP_CONTRACT_VERSION}`,
    map_contract_gate: V5_J301_MAP_CONTRACT_GATE,
    map_contract_receipt_step: V5_J301_MAP_CONTRACT_RECEIPT_STEP,
    map_contract_production_status: V5_J301_MAP_CONTRACT_PRODUCTION_STATUS,
    public_projection_here: false,
    share_grant_issuance_here: false,
    pdf_render_request_here: false,
    effects: V5_NO_EFFECTS,
  });
}

/** Every name a consumer may import from this module. The suite enumerates the
 * real exports through the loader and refuses any name not on this list. */
export const V5_J301_COMMAND_PUBLIC_SURFACE = deepFreeze([
  "V5J301CommandError",
  "V5_J301_COMMAND_AXES",
  "V5_J301_COMMAND_DECISIONS",
  "V5_J301_COMMAND_ENVELOPE_SCHEMA_VERSION",
  "V5_J301_COMMAND_EQUIVALENCE_SCHEMA_VERSION",
  "V5_J301_COMMAND_JOURNEYS",
  "V5_J301_COMMAND_NAMES",
  "V5_J301_COMMAND_ORIGINS",
  "V5_J301_COMMAND_PERMITTED_JOURNEY",
  "V5_J301_COMMAND_POLICY_VERSION",
  "V5_J301_COMMAND_PROJECTION_SCHEMA_VERSION",
  "V5_J301_COMMAND_PUBLIC_SURFACE",
  "V5_J301_COMMAND_SCHEMA_VERSION",
  "V5_J301_COMMAND_VERBS",
  "V5_J301_HUMAN_PROMOTION_RECEIPT_SEAM",
  "V5_J301_MAP_COMMANDS",
  "V5_J301_NAVIGATION_PLATFORMS",
  "V5_J301_POSITION_ROLES",
  "V5_J301_TRAVEL_MODES",
  "V5_NO_EFFECTS",
  "compareMapCommandOrigins",
  "evaluateMapCommandAdmission",
  "normalizeMapCommand",
  "v5J301CommandPolicyCanonicalBytes",
  "v5J301CommandPolicyDigest",
  "v5J301CommandPolicyPreimage",
  "v5J301MapCommandProjection",
]);
