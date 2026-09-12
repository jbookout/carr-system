// DoctorCRE v5 slice V5-A02, half one: THE GATE ZERO PUBLIC SURFACE — and it is
// a surface that cannot say yes about anything.
//
// WHAT THIS FILE MAY NOT DECIDE, said first because it is the whole point.
//
// (1) THE OUTCOME. Until 2026-09-11 r7 — the frozen design packet — registered
// NO producer, oracle, output schema, evidence scope or gate id for
// `step:gate-zero-read-only-outcome`. Decision
// `20c83902-f150-4d59-beca-915c5c871f95` then ruled the role provisionally, and
// on 2026-09-12 the amendment ruled on loop 589 wrote it into the packet: r7
// carries the producer row, the `gate-zero-read-only-accepted` gate and the
// `independent_control_plane_oracle` role, and the packet re-froze from
// `ef34aa54…` to `ea40f61a…`. gate-zero-producer-registration.v5.js pins that
// digest and derives `r7_entry_present` from it.
//
// NOTHING ELSE MOVED, and the list of what did not is longer than the list of
// what did. NO SEAT HOLDS THE ORACLE — card 9 named the reviewer charter, and
// naming a charter is not staffing a desk. No predecessor-outcome reader,
// scheduler reader or gate-conclusion reader exists. So the producer SEAM is
// still unbound, `producer_bound` is still false, `passable` is still false for
// every caller on every input, and there is still no join. A registered role is
// not a signature, and deciding who should sign is not somebody signing.
//
// (2) THE EVIDENCE. There is no authoritative predecessor-outcome reader, no
// scheduler reader and no gate-conclusion reader in this repository either. A
// caller object describing four accepted outcomes, a canary, a readback and a
// green gate graph is a DESCRIPTION OF EVIDENCE, not evidence, and a public
// function that turned such a description into `ok`, `green`, `joins_exactly`
// or `allow` would be handing out authority that nothing in this system holds.
// That is the exact defect the V5-A02 review of PR 985 named, and the standing
// rule learned from nine review rounds on 2026-09-11 forbids it under ANY name,
// including a "predicate", "fixture" or "unwired" variant.
//
// SO THE PUBLIC SURFACE IS THIS, and it is the whole of it:
//
//   readGateZeroPredecessorJoin  -> status "unavailable", naming the readers owed
//   readGateGraphAssurance       -> status "unavailable", naming the reader owed
//   emitGateZeroOutcome          -> passable false, for every caller on every
//                                   input, and it embeds NO join — a successful
//                                   join inside a refusal is still a successful
//                                   join, and there is none to be had.
//
// NONE OF THE THREE READS ITS REQUEST. That is not laziness, it is the boundary:
// if no field of the request can change the answer, then no caller can smuggle
// authority in through one. `request_read: false` says so in every result.
//
// WHERE THE REAL DECISION LOGIC LIVES. The three deterministic clauses V5-A02's
// checkable_done names are implemented, proved clause by clause, and kept
// MODULE-PRIVATE to the public surface: they live in
// gate-zero-classifiers.v5.testonly.js, which this file does not import, which
// no production module imports, and which answers only in the conditional
// (`would_satisfy_if_authoritative`, `would_be_green_if_authoritative`,
// `would_join_exactly_if_authoritative`). gate-zero-assurance.v5.test.mjs proves
// the isolation with a parser-backed import scan of every module in
// mcp-server/src, and proves this surface with a sweep over every
// caller-controlled shape — including the reviewer's one-gate construction.
//
// THE FOUR PREDECESSORS ARE NOT THIS FILE'S CHOICE. They are the frozen plan's,
// enforced today at tools/doctorcre-v5-review.cjs:1234-1244, which asserts that
// `step:gate-zero-read-only-outcome`'s `depends_on` is exactly
// {wr46-dissolution, wr40-repository, wr54-backup-recovery, scheduler-active}.
// They are restated ONCE, in gate-zero-producer-registration.v5.js beside the
// registration they stamp, because that validator is CommonJS plan-shape code no
// ESM module can import; this module re-exports that one list rather than
// keeping a second. gate-zero-assurance.v5.test.mjs reads the validator and
// asserts the two lists are identical — so a change to the frozen plan turns
// this module's test red instead of letting the two drift apart in silence.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — `decision` is "refuse" with a stable
//     `reason_id`. Silence is never an allow, and on this surface there is no
//     allow at all.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Handing a producer in as a
//     second argument is not a policy question.
//
// DECISION BINDING. The catalog maps Q017.D1, Q036.D1, Q067.D1 and Q086.D1 to
// this slice. Only their ROUTING is readable: the v5 design-basis register
// carries Q001-Q015 in full and compacts everything after it to pointers whose
// own canonicalization note says to recompile the text from source item IDs in
// the originating Codex thread, which is not reachable from here. Q017's
// readable disposition is `target: assurance_fabric`, `acceptance_hook:
// assurance-fabric-child-outcome` (doctrine `doctorcre-v5-design-basis`, section
// `requirements-q016-q020`, content hash
// 68f7f22e7964d08d724bacd43e49f0ccb0d20914e354e6a87f9645331d591710). The four
// ids are CARRIED in V5_A02_DECISION_IDS as a binding; this file claims no
// knowledge of their text and derives no behaviour from a guess at it.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { GATE_ZERO_STEP_REF } from "./benchmark-minimum.v5.js";
import {
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_DECISION_REF,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS,
  V5_A02_GATE_ZERO_R7_ENTRY_PRESENT,
  V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
  V5_A02_SCHEDULER_STEP_REF,
} from "./gate-zero-producer-registration.v5.js";

export { GATE_ZERO_STEP_REF, V5_NO_EFFECTS };

export const V5_A02_GATE_ZERO_SCHEMA_VERSION = "doctorcre-v5-a02-gate-zero-assurance.v1";

/**
 * 2, not 1: version 1 answered from caller-supplied observations. This version
 * answers `unavailable` and names what it is owed.
 */
export const V5_A02_POLICY_VERSION = 2;

/** The four decisions the catalog maps to V5-A02. Carried, not interpreted. */
export const V5_A02_DECISION_IDS = deepFreeze(["Q017.D1", "Q036.D1", "Q067.D1", "Q086.D1"]);

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

// ---------------------------------------------------------------------------
// The frozen plan's four bound predecessors, and the scheduler among them.
// ---------------------------------------------------------------------------

/**
 * The four predecessors and the scheduler among them, re-exported from
 * gate-zero-producer-registration.v5.js — which holds them as a hard-bound
 * literal beside the registration they stamp. This module does not restate
 * them: two copies would be two authorities. A member absent there is not
 * admissible evidence for Gate Zero; a member present there is mandatory.
 *
 * The producer registration comes from the same module as ONE frozen constant.
 * There is no builder to call and no predecessor set to hand in. Since the
 * 2026-09-12 amendment the frozen r7 packet carries the registration and its
 * `r7_entry_present` reads true — derived from the pinned packet digest, never
 * from a caller. It still names a role and staffs nobody, so it binds nothing.
 */
export {
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
  V5_A02_SCHEDULER_STEP_REF,
};

/** What an observation says it saw. "pending" and "absent" are both "no". */
export const V5_A02_OBSERVATION_STATES = deepFreeze(["absent", "observed", "pending"]);

/** Whether the outcome was accepted. Only "accepted" is a closed review. */
export const V5_A02_ACCEPTANCE_STATES = deepFreeze([
  "accepted", "pending_human_acceptance", "rejected",
]);

/**
 * What the accepted outcome CONCLUDED. Accepting a `criteria_not_met` outcome
 * closes a review and clears no milestone, so the two are separate fields and
 * both must be right.
 */
export const V5_A02_CRITERIA_STATES = deepFreeze([
  "criteria_met", "criteria_not_met", "not_observed",
]);

/** A gate's own reported conclusion. Only "success" is green. */
export const V5_A02_GATE_CONCLUSIONS = deepFreeze([
  "cancelled", "failure", "pending", "success", "unknown",
]);

/**
 * Every refusal this slice's Gate Zero half can answer with — the public
 * surface's three reader-unavailable reasons, and the clause reasons the
 * module-private classifiers cite. Closed, so a reason is checkable.
 */
export const V5_A02_GATE_ZERO_REASON_IDS = deepFreeze([
  "duplicate_predecessor_observation",
  "duplicate_predecessor_outcome",
  "gate_conclusion_reader_unavailable",
  "gate_dependency_unknown",
  "gate_graph_not_green",
  "gate_zero_producer_seam_unavailable",
  "predecessor_criteria_not_met",
  "predecessor_observed_after_reference",
  "predecessor_outcome_absent",
  "predecessor_outcome_bound_to_other_step",
  "predecessor_outcome_digest_mismatch",
  "predecessor_outcome_not_accepted",
  "predecessor_outcome_reader_unavailable",
  "predecessor_set_incomplete",
  "scheduler_canary_digest_mismatch",
  "scheduler_canary_not_bound_to_receipt",
  "scheduler_readback_absent",
  "scheduler_readback_canary_mismatch",
  "scheduler_readback_not_after_dispatch",
  "unknown_predecessor_step",
].sort());

function reason(id) {
  if (!V5_A02_GATE_ZERO_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a registered reason`, { reason_id: id });
  return id;
}

// ---------------------------------------------------------------------------
// THE SEAMS, NAMED BY THIS MODULE.
//
// Each is a NAME, not a port: nothing a caller passes can become one, because no
// exported function accepts a producer or a reader as an argument and this
// module exports no way to bind one.
// ---------------------------------------------------------------------------

/** Who or what could issue a Gate Zero outcome. r7 registers a role; nobody holds it. */
export const V5_A02_GATE_ZERO_PRODUCER_SEAM = "seam:gate-zero-read-only-outcome-producer";

/** Where an ACCEPTED predecessor outcome could be read from. Nothing today. */
export const V5_A02_PREDECESSOR_OUTCOME_READER_SEAM = "seam:gate-zero-predecessor-outcome-reader";

/** Where a scheduler dispatch and its readback could be read from. Nothing today. */
export const V5_A02_SCHEDULER_READER_SEAM = "seam:scheduler-canary-reader";

/** Where a gate's own conclusion could be read from. Nothing today. */
export const V5_A02_GATE_CONCLUSION_READER_SEAM = "seam:gate-conclusion-reader";

/** Everything this half is owed before any of its answers could be yes. */
export const V5_A02_GATE_ZERO_OWED_SEAMS = deepFreeze([
  V5_A02_GATE_CONCLUSION_READER_SEAM,
  V5_A02_GATE_ZERO_PRODUCER_SEAM,
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
].sort());

/**
 * The bindings: all null, because none of these exist. When a ruling records
 * one, this object is the single place it binds and the three functions below
 * are the clauses it will be read through.
 */
const V5_A02_GATE_ZERO_BINDINGS = Object.freeze({
  [V5_A02_GATE_ZERO_PRODUCER_SEAM]: null,
  [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM]: null,
  [V5_A02_SCHEDULER_READER_SEAM]: null,
  [V5_A02_GATE_CONCLUSION_READER_SEAM]: null,
});

/** The bound holder for a seam, or null when it is unavailable. Takes no input. */
function boundSeam(seam, method) {
  const holder = V5_A02_GATE_ZERO_BINDINGS[seam];
  return isPlainObject(holder) && typeof holder[method] === "function" ? holder : null;
}

/** Every seam this answer is owed, and whether anything is bound to it. */
function seamsOwed(seams) {
  return deepFreeze(seams.map(seam => ({ seam, bound: false })));
}

/** The one shape every unavailable answer on this surface has. */
function unavailable(answer, reasonId, because, seams, extra) {
  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    answer,
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    status: "unavailable",
    decision: "refuse",
    reason_id: reason(reasonId),
    unavailable_because: because,
    owed_seams: [...seams].sort(),
    seams_bound: seamsOwed([...seams].sort()),
    // No field of any request can change any field of this answer.
    request_read: false,
    caller_evidence_admitted: false,
    decided_by: "no_authoritative_reader",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
    ...extra,
  });
}

// ---------------------------------------------------------------------------
// checkable_done 1 — "WR prerequisites and scheduler canary/readback join
// exactly". The clause is implemented and proved; the READING is not available.
// ---------------------------------------------------------------------------

/**
 * What a Gate Zero predecessor join would need and cannot get.
 *
 * The four predecessors would have to be read from an accepted-outcome store,
 * and the canary and its readback from the scheduler that dispatched them.
 * Neither reader exists, so this answers `unavailable` and names both — for
 * every caller, on every input, with or without one.
 */
export function readGateZeroPredecessorJoin() {
  return unavailable(
    "gate_zero_predecessor_join",
    "predecessor_outcome_reader_unavailable",
    "no authoritative accepted-outcome store and no scheduler reader exist to read the four predecessors, the canary or its readback from",
    [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, V5_A02_SCHEDULER_READER_SEAM],
    {
      required_predecessors: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
      scheduler_step_ref: V5_A02_SCHEDULER_STEP_REF,
      predecessor_outcome_reader_bound: boundSeam(V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, "readOutcome") !== null,
      scheduler_reader_bound: boundSeam(V5_A02_SCHEDULER_READER_SEAM, "readCanary") !== null,
    });
}

// ---------------------------------------------------------------------------
// checkable_done 2 — "failed injected gate cannot claim green". The propagation
// clause is implemented and proved; the CONCLUSIONS cannot be read.
// ---------------------------------------------------------------------------

/**
 * What a gate-graph assurance would need and cannot get.
 *
 * Non-green propagation is only as good as the conclusions it propagates, and a
 * caller-supplied list of gates with their own conclusions on them is a claim
 * about CI, not a reading of it. No gate-conclusion reader exists.
 */
export function readGateGraphAssurance() {
  return unavailable(
    "gate_graph_assurance",
    "gate_conclusion_reader_unavailable",
    "no authoritative reader of gate conclusions exists, so a gate graph can only be asserted by its caller",
    [V5_A02_GATE_CONCLUSION_READER_SEAM],
    // The conclusions vocabulary is an EXPORTED CONSTANT, not a field of this
    // answer: an unavailable answer recites nothing a caller could mistake for a
    // reading.
    { gate_conclusion_reader_bound: boundSeam(V5_A02_GATE_CONCLUSION_READER_SEAM, "readConclusion") !== null });
}

// ---------------------------------------------------------------------------
// The privileged path, and the refusal that stands where it would be.
// ---------------------------------------------------------------------------

/**
 * THE ONLY FUNCTION THAT COULD EMIT A GATE ZERO OUTCOME, AND IT CANNOT.
 *
 * It takes ONE argument, and the arity IS the boundary: there is no producer
 * parameter, so no caller can hand in the authority that is missing. The answer
 * is fixed for every caller on every input:
 *
 *     passable: false, reason "gate_zero_producer_seam_unavailable"
 *
 * AND IT CARRIES NO JOIN. The earlier version of this file returned the full
 * predecessor join beside the refusal, so a caller who supplied a clean fixture
 * got `join.decision: "allow"` and `joins_exactly: true` inside a response whose
 * outer field said refuse. A successful join inside a refusal is still a
 * successful join, and it was reachable from nothing but caller input. What a
 * reader gets instead is the truth: the join is unavailable, and here is every
 * seam it is owed.
 */
export function emitGateZeroOutcome(request) {
  // eslint-disable-next-line no-unused-vars, prefer-rest-params -- the arity IS
  // the boundary, and the request is deliberately not read.
  if (arguments.length > 1)
    fail("gate_zero_producer_is_not_an_argument",
      "emitGateZeroOutcome takes one request; the producer is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM });

  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;
  return unavailable(
    "gate_zero_outcome_emission",
    "gate_zero_producer_seam_unavailable",
    "r7 now carries the producer registration, but no seat holds the oracle and no reader exists for the evidence a Gate Zero outcome would stand on",
    [...V5_A02_GATE_ZERO_OWED_SEAMS],
    {
      decision_ids: [...V5_A02_DECISION_IDS],
      // FIXED. Not derived from a join, not derived from the request, not
      // derivable by any caller, and NOT derived from the registration either:
      // a ruled role is not a signature.
      passable: false,
      not_passable_because:
        "the registered producer role is unstaffed, and no authoritative predecessor, scheduler or gate reader exists",
      producer_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM,
      producer_bound: boundSeam(V5_A02_GATE_ZERO_PRODUCER_SEAM, "emitOutcome") !== null,
      producer_is_caller_supplied: false,
      // What decision 20c83902 ruled and the 2026-09-12 amendment then wrote
      // into r7 itself. `producer_registration.r7_entry_present` carries the
      // derivation, `producer_registration.r7_packet_sha256` the packet it is
      // derived from, and the four fields the ruling could not settle are
      // answered in the registration's own `resolved_from_r7`.
      producer_role: entry.producer_role,
      oracle_ref: entry.oracle_ref,
      output_schema_ref: entry.output_schema_ref,
      evidence_scope: entry.evidence_scope,
      produced_gate_id: entry.produces_gate_ids[0],
      producer_registration: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
      producer_registration_status: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS,
      producer_registration_decision_ref: V5_A02_GATE_ZERO_PRODUCER_DECISION_REF,
      r7_entry_present: V5_A02_GATE_ZERO_R7_ENTRY_PRESENT,
      // The outcome fields a consumer would need. Null because no run has
      // happened and no seat could have run it.
      outcome_digest: null,
      observed_at: null,
      // AND the join fields. Null because there is no join to report, not
      // because this one happened to fail.
      join: null,
      join_unavailable_because: "no authoritative predecessor-outcome or scheduler reader exists to join",
      predecessor_evidence_read: null,
      // The four the ruling answered are gone from this list and reported
      // above instead. What is left is what is still genuinely open, plus the
      // two the ruling created by deciding a role nothing holds.
      undecided_governance_questions: deepFreeze([
        "which independent seat holds oracle:gate-producer:gate-zero-read-only",
        "which store an accepted predecessor outcome is read from",
        "which scheduler surface a canary and its readback are read from",
        "which surface a gate's own conclusion is read from",
      ]),
    });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no observation, canary, gate or instant, and no
// argument is read at all — so two callers describing the same policy reach the
// same digest. It is an identity for these bytes: not an acceptance, not a
// receipt, and not evidence for any consumer gate.
// ---------------------------------------------------------------------------

export function v5A02GateZeroPolicyPreimage() {
  return {
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_ids: [...V5_A02_DECISION_IDS],
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    predecessor_step_refs: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    scheduler_step_ref: V5_A02_SCHEDULER_STEP_REF,
    observation_states: [...V5_A02_OBSERVATION_STATES].sort(),
    acceptance_states: [...V5_A02_ACCEPTANCE_STATES].sort(),
    criteria_states: [...V5_A02_CRITERIA_STATES].sort(),
    gate_conclusions: [...V5_A02_GATE_CONCLUSIONS].sort(),
    reason_ids: [...V5_A02_GATE_ZERO_REASON_IDS].sort(),
    owed_seams: [...V5_A02_GATE_ZERO_OWED_SEAMS],
    producer_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM,
    producer_registration: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
    producer_bound: false,
    authoritative_readers_bound: false,
    public_surface_answers: "unavailable",
    gate_zero_passable: false,
  };
}

export function v5A02GateZeroPolicyDigest() {
  return digest(v5A02GateZeroPolicyPreimage());
}

export function v5A02GateZeroPolicyCanonicalBytes() {
  return canonicalJson(v5A02GateZeroPolicyPreimage());
}

// ---------------------------------------------------------------------------
// THE R7 CHECK. Proving the registration is in the packet, from the packet.
// ---------------------------------------------------------------------------

/**
 * `registration.r7_entry_present` is derived from the pinned packet digest, and
 * this is the check that proves the pin is honest: hand it the r7 design
 * packet's BYTES — reconstructed from the 62 chunk sections behind manifest
 * 6ac54e7b-0965-41f6-88c3-da187a8b5d23 in doctrine document
 * `doctorcre-v5-design-basis` — and it answers whether those bytes are the
 * packet this repository pins, and whether they really carry the registration.
 *
 * Four findings, reported separately rather than collapsed, because a packet
 * that hashes right and says something else is a different failure from a
 * packet that says the right thing and is not the pinned one:
 *
 *   digest_matches   the bytes hash to the pinned `normalized_r7_sha256`
 *   entry_matches    the packet's producer row for step:gate-zero-read-only-outcome
 *                    is field-for-field the registration's registry entry
 *   gate_registered  `gate-zero-read-only-accepted` is in consumer_gate_registry
 *                    and names this producer
 *   role_registered  `independent_control_plane_oracle` is in producer_role_registry
 *
 * `present` is all four. NOTHING A CALLER CAN PASS makes it true except the
 * packet: there is no flag, no digest string and no row argument. It reads;
 * it produces no receipt, stamps no authority and changes no seam.
 */
export function v5A02GateZeroR7Presence(r7DesignPacketBytes) {
  if (typeof r7DesignPacketBytes !== "string" && !Buffer.isBuffer(r7DesignPacketBytes))
    throw new V5BoundaryError("r7_packet_bytes_required",
      "the r7 design packet bytes are required; there is no flag to pass instead",
      { pinned_sha256: V5_A02_GATE_ZERO_R7_PACKET_SHA256 });
  const observed = digest(r7DesignPacketBytes).replace("sha256:", "");
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;
  const finding = {
    observed_sha256: observed,
    expected_sha256: V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    superseded_sha256: V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
    digest_matches: observed === V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    is_superseded_packet: observed === V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
    parsed: false,
    entry_matches: false,
    gate_registered: false,
    role_registered: false,
    present: false,
  };
  let packet;
  try {
    packet = JSON.parse(r7DesignPacketBytes.toString("utf8"));
  } catch {
    return deepFreeze(finding);
  }
  finding.parsed = true;
  const row = (packet.receipt_producer_step_registry || [])
    .find(item => item && item.step_ref === GATE_ZERO_STEP_REF) || null;
  finding.entry_matches = row !== null && canonicalJson(row) === canonicalJson(entry);
  const gate = (packet.consumer_gate_registry || [])
    .find(item => item && item.gate_id === entry.produces_gate_ids[0]) || null;
  finding.gate_registered = gate !== null && Array.isArray(gate.receipt_producer_step_refs)
    && gate.receipt_producer_step_refs.includes(GATE_ZERO_STEP_REF);
  finding.role_registered = Array.isArray(packet.producer_role_registry)
    && packet.producer_role_registry.includes(entry.producer_role);
  finding.present = finding.digest_matches && finding.entry_matches
    && finding.gate_registered && finding.role_registered;
  return deepFreeze(finding);
}
