// DoctorCRE v5 slice V5-A02, half one: THE GATE ZERO PUBLIC SURFACE — and it is
// a surface that cannot say yes about anything.
//
// WHAT THIS FILE MAY NOT DECIDE, said first because it is the whole point.
//
// (1) THE OUTCOME. r7 — the frozen design packet — registers NO producer,
// oracle, output schema, evidence scope or gate id for
// `step:gate-zero-read-only-outcome`. benchmark-minimum.v5.js:52-67 (slice
// V5-A00) says so in its own words and refuses to invent one.
//
// WHAT CHANGED ON 2026-09-11, AND WHAT DID NOT. Decision
// `20c83902-f150-4d59-beca-915c5c871f95` adopts Option B PROVISIONALLY: the
// producer ROLE, oracle, output schema, gate id, pass rule and retry policy are
// now decided, and gate-zero-producer-registration.v5.js carries them in r7's
// own registry shape. Five fields of the refusal below therefore stop being
// null and start naming what was ruled — which is the honest report, because
// a reader who sees null concludes nobody has decided, and somebody has.
//
// NOTHING ELSE MOVED, and the list of what did not is longer than the list of
// what did. r7 itself still has no entry, so the ruling is a copy held here and
// not the packet's own word. No seat holds the oracle. No predecessor-outcome
// reader, scheduler reader or gate-conclusion reader exists. So the producer
// SEAM is still unbound, `producer_bound` is still false, `passable` is still
// false for every caller on every input, and there is still no join. Deciding
// who should sign is not the same as somebody signing.
//
// (2) THE EVIDENCE, AND THIS HALF MOVED ON 2026-09-12. A caller object
// describing four accepted outcomes, a canary, a readback and a green gate graph
// is a DESCRIPTION OF EVIDENCE, not evidence, and a public function that turned
// such a description into `ok`, `green`, `joins_exactly` or `allow` would be
// handing out authority that nothing in this system holds. That is the exact
// defect the V5-A02 review of PR 985 named, and the standing rule learned from
// nine review rounds on 2026-09-11 forbids it under ANY name, including a
// "predicate", "fixture" or "unwired" variant. NONE OF THAT CHANGES.
//
// WHAT CHANGED is that three of the four seams stopped being empty. PR 1001
// built the readers for cards 11, 12 and 13 and Joe ruled all three, so an
// accepted predecessor outcome, a scheduler canary and a gate's own conclusion
// each have an authoritative surface to be read from. This module now IMPORTS
// those readers and asks the same ruling table they ask, so the three seams
// report BOUND here on exactly the condition they are open there — and report
// unbound again the moment a `decision_id:` line goes back to null.
//
// WHAT DID NOT CHANGE is the answer. Every function below still returns
// `unavailable`, because a bound reader is a surface and not a subject: which
// accepted outcome, which canary row, which commit and which declared check a
// Gate Zero run stands on are the PRODUCER's bindings, and cards 9 and 10 left
// the producer seam deliberately unbuilt. The refusal therefore moved forward
// rather than lifting — from "nothing exists to read with" to "nothing has the
// authority to aim it" — which is the more advanced and more honest of the two.
//
// SO THE PUBLIC SURFACE IS THIS, and it is the whole of it:
//
//   readGateZeroPredecessorJoin  -> status "unavailable"; the two ruled readers
//                                   report bound, the producer seam is named owed
//   readGateGraphAssurance       -> status "unavailable"; the ruled conclusion
//                                   reader reports bound, the producer seam owed
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
  V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF,
  V5_A02_GATE_ZERO_ORACLE_SEAT_DECISION_REF,
  V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF,
  V5_A02_GATE_ZERO_R7_ENTRY_PRESENT,
  V5_A02_SCHEDULER_STEP_REF,
} from "./gate-zero-producer-registration.v5.js";
import {
  readGateConclusionEvidence,
  readPredecessorOutcomeEvidence,
  readSchedulerCanaryEvidence,
} from "./gate-zero-seam-readers.v5.js";
import { seamRulingRef } from "./gate-zero-seam-rulings.v5.js";

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
 * The provisionally-ruled producer registration comes from the same module as
 * ONE frozen constant. There is no builder to call and no predecessor set to
 * hand in. It names a role; it staffs nobody and binds nothing.
 */
export {
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
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

/** Who or what could issue a Gate Zero outcome. r7 registers nobody. */
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
 * THE BINDINGS, AND WHAT MOVED ON 2026-09-12.
 *
 * Until PR 1001 every entry here was null and every answer below said so in the
 * only way it honestly could: no reader exists. Three of the four seams now have
 * a reader behind them — cards 11, 12 and 13, ruled by Joe on 2026-09-11 and
 * switched on by the three `decision_id:` lines of gate-zero-seam-rulings.v5.js.
 * This module IMPORTS those readers and asks that SAME ruling table, so a seam
 * is bound here on exactly the condition it is open there, and the two cannot
 * drift. Put `null` back on a card's ruling line and the seam closes in both
 * files at once.
 *
 * WHAT IS STILL NOT A DOOR. The readers arrive as a module-private import.
 * There is no argument, no setter, no registry keyed by anything a caller
 * controls, and no environment variable that binds a seam: the binding
 * condition is a decision id committed to a file, which is what a ruling is.
 *
 * AND THE PRODUCER SEAM IS STILL NULL — the one that decides everything below.
 * Cards 9 and 10 name the reviewer charter as the oracle's holder and amend r7
 * to carry the registration; naming a charter staffs no desk and an unapplied
 * amendment is not an entry. So `passable` is still false, `producer_bound` is
 * still false, and the join is still unavailable — now for the reason that is
 * actually true. These readers answer truthfully about whatever row they are
 * pointed at, and NOTHING IN THIS REPOSITORY HAS THE AUTHORITY TO POINT THEM:
 * which canary row, which accepted outcome, which commit and which check a Gate
 * Zero run stands on are the producer's bindings, and no seat holds it.
 *
 * `holder` IS A THUNK, NOT A VALUE, and that is load-order and not style.
 * gate-zero-seam-readers.v5.js imports this module for its unruled answer, so
 * the two form a cycle; whichever of them a process imports first, the other's
 * body has not run yet. A thunk reads the reader binding when a caller asks,
 * by which time both modules are evaluated. Reading it eagerly here would throw
 * on the import order that starts at the readers.
 */
const V5_A02_GATE_ZERO_SEAM_BINDINGS = Object.freeze({
  // Cards 9 and 10. No card token and no holder: nothing can rule this one open.
  [V5_A02_GATE_ZERO_PRODUCER_SEAM]: null,
  [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM]: Object.freeze({
    card_ref: "card:11",
    method: "readOutcome",
    holder: () => Object.freeze({ readOutcome: readPredecessorOutcomeEvidence }),
  }),
  [V5_A02_SCHEDULER_READER_SEAM]: Object.freeze({
    card_ref: "card:12",
    method: "readCanary",
    holder: () => Object.freeze({ readCanary: readSchedulerCanaryEvidence }),
  }),
  [V5_A02_GATE_CONCLUSION_READER_SEAM]: Object.freeze({
    card_ref: "card:13",
    method: "readConclusion",
    holder: () => Object.freeze({ readConclusion: readGateConclusionEvidence }),
  }),
});

/**
 * The bound holder for a seam, or null when it is unavailable. Takes no caller
 * input: the seam and the method are this module's own constants, and the only
 * question asked of anything outside is whether that seam's CARD is ruled.
 *
 * FAIL-CLOSED IN EVERY DIRECTION. An unknown seam, a seam with no card, a card
 * with no live ruling, a thunk that throws, a holder that is not an object or
 * does not carry the method — each answers null, which is the same "no" this
 * surface gave before any ruling existed.
 */
function boundSeam(seam, method) {
  const binding = V5_A02_GATE_ZERO_SEAM_BINDINGS[seam];
  if (!isPlainObject(binding) || binding.method !== method) return null;
  if (seamRulingRef(binding.card_ref) === null) return null;
  let holder;
  try {
    holder = binding.holder();
  } catch {
    return null;
  }
  return isPlainObject(holder) && typeof holder[method] === "function" ? holder : null;
}

/** Whether a seam has a bound holder at all, asked without naming its method. */
function seamBound(seam) {
  const binding = V5_A02_GATE_ZERO_SEAM_BINDINGS[seam];
  return isPlainObject(binding) && boundSeam(seam, binding.method) !== null;
}

/** Every seam this answer stands on, and whether anything is bound to it. */
function seamsBound(seams) {
  return deepFreeze(seams.map(seam => ({ seam, bound: seamBound(seam) })));
}

/**
 * The one shape every unavailable answer on this surface has.
 *
 * `owed_seams` is what is STILL owed — a seam with a bound holder is not owed —
 * and `seams_bound` carries the whole list with its live state, so nothing is
 * hidden by the filter. While nothing is bound the two say what they have always
 * said, byte for byte, which is the property the unruled tree is tested on.
 */
function unavailable(answer, reasonId, because, seams, extra,
  decidedBy = "no_authoritative_reader") {
  const stands_on = [...seams].sort();
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
    owed_seams: stands_on.filter(seam => !seamBound(seam)),
    seams_bound: seamsBound(stands_on),
    // No field of any request can change any field of this answer.
    request_read: false,
    caller_evidence_admitted: false,
    decided_by: decidedBy,
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
    ...extra,
  });
}

/** The three ruled reader seams, in the order a reviewer reads the cards. */
const V5_A02_GATE_ZERO_READER_SEAMS = deepFreeze([
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
  V5_A02_GATE_CONCLUSION_READER_SEAM,
]);

/** Whether all three ruled readers are bound. The producer is not among them. */
function allReaderSeamsBound() {
  return V5_A02_GATE_ZERO_READER_SEAMS.every(seamBound);
}

/**
 * The one sentence that is true of every answer here once the readers are bound:
 * the reading is available and the POINTING is not.
 */
const PRODUCER_POINTS_THE_READERS = "ruled_readers_bound_producer_unstaffed";

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
  const predecessorReader = boundSeam(V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, "readOutcome");
  const schedulerReader = boundSeam(V5_A02_SCHEDULER_READER_SEAM, "readCanary");
  const stood = {
    required_predecessors: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    scheduler_step_ref: V5_A02_SCHEDULER_STEP_REF,
    predecessor_outcome_reader_bound: predecessorReader !== null,
    scheduler_reader_bound: schedulerReader !== null,
  };
  // THE UNRULED ANSWER, unchanged to the byte. While either card is unruled the
  // sentence below is literally true and is the one this surface has always
  // given; a tree with `null` back on the ruling lines reproduces this object's
  // exact digest, and gate-zero-assurance.v5.test.mjs proves it against the
  // digest main published.
  if (predecessorReader === null || schedulerReader === null)
    return unavailable(
      "gate_zero_predecessor_join",
      "predecessor_outcome_reader_unavailable",
      "no authoritative accepted-outcome store and no scheduler reader exist to read the four predecessors, the canary or its readback from",
      [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, V5_A02_SCHEDULER_READER_SEAM],
      stood);
  // AND THE RULED ONE, which refuses for a different and more advanced reason.
  // Both readers are bound and either would answer about a row. Nothing here can
  // say WHICH row, because that is the producer's binding and no seat holds it —
  // so the refusal moves from "there is nothing to read with" to "there is
  // nobody to aim it", and the producer seam joins the seams this answer stands
  // on rather than the join being reported as available.
  return unavailable(
    "gate_zero_predecessor_join",
    "gate_zero_producer_seam_unavailable",
    "the ruled readers for the four predecessors and for the scheduler canary are bound, and no seat holds the producer that would say which accepted outcome and which canary row a Gate Zero run stands on",
    [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, V5_A02_SCHEDULER_READER_SEAM,
      V5_A02_GATE_ZERO_PRODUCER_SEAM],
    stood,
    PRODUCER_POINTS_THE_READERS);
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
  const conclusionReader = boundSeam(V5_A02_GATE_CONCLUSION_READER_SEAM, "readConclusion");
  // The conclusions vocabulary is an EXPORTED CONSTANT, not a field of this
  // answer: an unavailable answer recites nothing a caller could mistake for a
  // reading.
  const stood = { gate_conclusion_reader_bound: conclusionReader !== null };
  if (conclusionReader === null)
    return unavailable(
      "gate_graph_assurance",
      "gate_conclusion_reader_unavailable",
      "no authoritative reader of gate conclusions exists, so a gate graph can only be asserted by its caller",
      [V5_A02_GATE_CONCLUSION_READER_SEAM],
      stood);
  // Card 13 is ruled, so a conclusion CAN be read — for one commit, under one
  // check this repository declares. Which commit and which check a Gate Zero run
  // is about is the producer's binding, and a graph assembled from commits of
  // this module's own choosing would be this module deciding what it was asked
  // to report on.
  return unavailable(
    "gate_graph_assurance",
    "gate_zero_producer_seam_unavailable",
    "the ruled reader of gate conclusions is bound, and no seat holds the producer that would say which commit and which declared check a Gate Zero run stands on",
    [V5_A02_GATE_CONCLUSION_READER_SEAM, V5_A02_GATE_ZERO_PRODUCER_SEAM],
    stood,
    PRODUCER_POINTS_THE_READERS);
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
  const readersBound = allReaderSeamsBound();
  return unavailable(
    "gate_zero_outcome_emission",
    "gate_zero_producer_seam_unavailable",
    readersBound
      ? "the three ruled evidence readers are bound, and the producer role is ruled provisionally to a charter that no seat holds, with r7 still carrying no entry"
      : "the producer role is ruled provisionally but r7 carries no entry, no seat holds the oracle, and no reader exists for the evidence one would stand on",
    [...V5_A02_GATE_ZERO_OWED_SEAMS],
    {
      decision_ids: [...V5_A02_DECISION_IDS],
      // FIXED. Not derived from a join, not derived from the request, not
      // derivable by any caller, and NOT derived from the registration either:
      // a ruled role is not a signature. Binding three readers does not move it
      // one step: a reading is evidence, and this field is a signature.
      passable: false,
      not_passable_because: readersBound
        ? "the ruled producer role is unstaffed and unregistered in r7, so nothing may point the three bound readers at the rows a Gate Zero run would stand on"
        : "the ruled producer role is unstaffed and unregistered in r7, and no authoritative predecessor, scheduler or gate reader exists",
      producer_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM,
      producer_bound: boundSeam(V5_A02_GATE_ZERO_PRODUCER_SEAM, "emitOutcome") !== null,
      producer_is_caller_supplied: false,
      // What decision 20c83902 ruled, reported as ruled. These are NOT read
      // from r7 — `producer_registration.r7_entry_present` says so in the same
      // breath — and the four fields the ruling could not settle are still
      // null inside the registration's own `unresolved_without_r7`.
      producer_role: entry.producer_role,
      oracle_ref: entry.oracle_ref,
      output_schema_ref: entry.output_schema_ref,
      evidence_scope: entry.evidence_scope,
      produced_gate_id: entry.produces_gate_ids[0],
      producer_registration: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
      producer_registration_status: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS,
      producer_registration_decision_ref: V5_A02_GATE_ZERO_PRODUCER_DECISION_REF,
      r7_entry_present: V5_A02_GATE_ZERO_R7_ENTRY_PRESENT,
      // Cards 9 and 10, reported as ruled and not as done. The charter is named
      // by decision 8a1dad08; naming a charter staffs no desk, so the seat is
      // still unbound. The r7 amendment is ruled by decision 311a9af5 and is
      // owed to whoever holds the frozen packet, so the entry is still absent.
      oracle_seat_bound: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_bound,
      oracle_seat_charter_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF,
      oracle_seat_charter_decision_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_DECISION_REF,
      r7_entry_amendment_decision_ref: V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF,
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
      // The last three left this list on 2026-09-11: Joe ruled cards 11, 12 and
      // 13, and the readers those rulings switched on are bound above. They are
      // listed again the moment a ruling line goes back to null, because this
      // list is derived from the bindings rather than maintained beside them.
      undecided_governance_questions: deepFreeze(readersBound
        ? [
          "which independent seat holds oracle:gate-producer:gate-zero-read-only",
          "whether r7 itself carries the registration, which today it does not",
        ]
        : [
          "which independent seat holds oracle:gate-producer:gate-zero-read-only",
          "whether r7 itself carries the registration, which today it does not",
          "which store an accepted predecessor outcome is read from",
          "which scheduler surface a canary and its readback are read from",
          "which surface a gate's own conclusion is read from",
        ]),
    },
    // WHO DECIDED THIS REFUSAL. While no reader is bound it is decided by there
    // being nothing authoritative to read; once the three ruled readers are
    // bound it is decided by the producer seat nobody holds, which is a
    // different fact and deserves a different word.
    readersBound ? PRODUCER_POINTS_THE_READERS : "no_authoritative_reader");
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
    // DERIVED, not asserted: three of the four seams have a ruled reader behind
    // them now, and this preimage would be lying if it still said false. It says
    // false again the moment a ruling line does.
    authoritative_readers_bound: allReaderSeamsBound(),
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
