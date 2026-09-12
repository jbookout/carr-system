// DoctorCRE v5 slice V5-A02: THE GATE ZERO PRODUCER REGISTRATION, and it is a
// registration of a ROLE, not the arrival of a seat.
//
// WHY THIS FILE EXISTS. Until 2026-09-11, `step:gate-zero-read-only-outcome`
// had no producer anywhere: no role, no oracle, no output schema, no evidence
// scope, no gate id. Three modules on main say so and refuse to invent one
// (benchmark-minimum.v5.js:52-67, journey-one-clock-input-store.v5.js:1501,
// gate-zero-assurance.v5.js). Decision `20c83902-f150-4d59-beca-915c5c871f95`
// (2026-09-11) adopts Option B PROVISIONALLY: Gate Zero becomes the eighth
// independent boundary receipt, issued by an independent oracle seat, reusing
// consumer-gate-receipt.v1 rather than growing a schema of its own; retryable
// with every run kept; Joe's exact-hash signature stays downstream on the
// benchmark manifest. The ruling is open to Joe's override.
//
// WHAT THIS FILE THEREFORE IS, exactly: the repository's copy of that ruling,
// in r7's own receipt_producer_step_registry shape, so that a reader can see
// what was decided and a reviewer can check it against the ruling. It is the
// same KIND of artifact as MINIMUM_REQUIRED_MEMBERS in benchmark-minimum.v5.js,
// which is likewise a hand-copied constant table of r7 registrations.
//
// WHAT CHANGED ON 2026-09-12, and it is the reason nothing below says "null"
// any more. Joe ruled loop 589 and the amendment was applied to the frozen
// packet itself: r7 now carries the `gate-zero-read-only-accepted` gate, the
// `step:gate-zero-read-only-outcome` producer row, and the
// `independent_control_plane_oracle` role its row resolves through. Re-freezing
// moved the packet's digest from `ef34aa54…` to `ea40f61a…` and the doctrine pin
// in section 5882b0cd-16f4-4896-b567-eb0fca6554f7 moved with it. r7 IS
// reachable, contrary to what this header said before: it is 62 base64 chunk
// sections behind manifest section 6ac54e7b-0965-41f6-88c3-da187a8b5d23 in
// doctrine document `doctorcre-v5-design-basis`, and
// tools/doctorcre-v5-review.cjs validates the reconstructed pair.
//
// WHAT THIS FILE IS NOT, said plainly because the whole slice exists to refuse
// exactly these things:
//
//   * It is NOT a bound producer. Nothing implements this role. No seat holds
//     the oracle — card 9 named the reviewer CHARTER, and naming a charter is
//     not staffing a desk. gate-zero-assurance.v5.js's producer seam stays
//     unbound and its answer stays `passable: false`.
//   * It is NOT a Gate Zero receipt, and nothing here can become one.
//   * `r7_entry_present` is NOT a flag a caller can set and NOT a boolean typed
//     in by hand. It is derived, in `r7EntryPresence()` below, from the pinned
//     packet digest and the completeness of the registry entry: pin the
//     superseded digest, or leave any field of the entry null, and it reads
//     false again. `v5A02GateZeroR7Presence()` in gate-zero-assurance.v5.js is
//     the stronger check a reader or a test runs against the real packet bytes;
//     it lives there because THIS module exports nothing callable, deliberately
//     — an exported function is the shape that could hand back an authority-
//     stamped record over references a caller chose (the PR 990 defect).

import { CONSUMER_GATE_RECEIPT_SCHEMA, GATE_ZERO_STEP_REF } from "./benchmark-minimum.v5.js";

export { GATE_ZERO_STEP_REF };

export const V5_A02_PRODUCER_REGISTRATION_SCHEMA_VERSION =
  "doctorcre-v5-a02-gate-zero-producer-registration.v1";

/** The ruling this file copies. Not a receipt; the authority for the copy. */
export const V5_A02_GATE_ZERO_PRODUCER_DECISION_REF = "20c83902-f150-4d59-beca-915c5c871f95";

/**
 * "registered" — the role was ruled provisionally by 20c83902, and on
 * 2026-09-12 the frozen r7 packet was amended to carry it, so the registration
 * is no longer an orchestrator's reading of a ruling: it is in the packet the
 * validator checks. Still NOT "accepted" and still NOT "active": no partner has
 * signed a Gate Zero receipt, no seat holds the oracle, and a word that implied
 * either would be the invention this slice refuses.
 */
export const V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS = "registered";

/**
 * THE PACKET THIS REGISTRATION IS A COPY OF, by digest.
 *
 * `ea40f61a…` is the sha256 of the amended r7 design packet's bytes — the value
 * doctrine now pins as `normalized_r7_sha256`. `ef34aa54…` is the packet as it
 * stood before the amendment, kept because it is the one digest that proves a
 * reader is looking at a packet WITHOUT the Gate Zero entry.
 */
export const V5_A02_GATE_ZERO_R7_PACKET_SHA256 =
  "ea40f61a9081814e53c989f2f945c61b270597cdfeafc4ec535578e60462a8f6";
export const V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256 =
  "ef34aa54740dd56508b7cebf05a2a95851aacedbbe4f2e4865a39ffede28f0ad";

/** The amendment that put the entry into r7 (card 10, applied on loop 589). */
export const V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF = "311a9af5-3685-4c47-a158-f8dd70870ca1";

/**
 * Is the entry in r7? Derived, never asserted. Three ways this returns false,
 * and each is a real failure mode rather than a formality:
 *
 *   1. the pinned digest is the superseded packet — the amendment did not land;
 *   2. the pinned digest is not a digest at all — someone typed a label;
 *   3. any field of the registry entry is still null — the entry exists in
 *      name but carries one of the four facts only r7 could supply as a hole,
 *      and a row with a null closed-registry field is refused by
 *      tools/doctorcre-v5-review.cjs anyway.
 */
function r7EntryPresence(entry, pinnedPacketSha256) {
  if (pinnedPacketSha256 === V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256) return false;
  if (!/^[0-9a-f]{64}$/.test(pinnedPacketSha256 || "")) return false;
  return Object.values(entry).every((value) => value !== null);
}

/** The role, in the family of the seven independent boundary-receipt oracles. */
export const V5_A02_GATE_ZERO_PRODUCER_ROLE = "independent_control_plane_oracle";

/** The oracle, named the way r7 names the other seven: `oracle:gate-producer:<subject>`. */
export const V5_A02_GATE_ZERO_ORACLE_REF = "oracle:gate-producer:gate-zero-read-only";

export const V5_A02_GATE_ZERO_ORACLE_VERSION = "1.0.0";

/** The gate this producer would produce, named the way r7 names the other seven. */
export const V5_A02_GATE_ZERO_GATE_ID = "gate-zero-read-only-accepted";

/** The receipt it would produce. One receipt, one producer, one digest. */
export const V5_A02_GATE_ZERO_RECEIPT_REF = "receipt:gate-zero-read-only-outcome";

/**
 * The pass rule, taken unchanged from the seven siblings: every current
 * independent member passes, or the gate does not.
 */
export const V5_A02_GATE_ZERO_COMBINER = "all_current_independent_pass";

/**
 * THE FOUR CANONICAL PREDECESSORS, HARD-BOUND, and the one authority for them.
 * Exactly the `depends_on` set that tools/doctorcre-v5-review.cjs:1234-1244
 * asserts on `step:gate-zero-read-only-outcome`, C-sorted so two readers
 * enumerate it identically. They are restated here because that validator is
 * CommonJS plan-shape code no ESM module can import, and
 * gate-zero-assurance.v5.test.mjs reads that file and asserts the two lists are
 * identical — so a change to the frozen plan turns the test red instead of
 * letting the two drift apart in silence.
 *
 * They live in THIS file, beside the registration they stamp, because a
 * registration that took its predecessors from a caller would let any caller
 * obtain an authority-stamped record over references of their own choosing.
 * There is no such door: the list is a literal and the builders below are
 * module-private.
 */
export const V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS = Object.freeze([
  "step:scheduler-active-receipt",
  "step:wr40-repository-outcome",
  "step:wr46-dissolution-outcome",
  "step:wr54-backup-recovery-outcome",
].sort());

/** The one predecessor the scheduler canary must itself be bound to. */
export const V5_A02_SCHEDULER_STEP_REF = "step:scheduler-active-receipt";

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (value !== null && typeof value === "object") {
    Object.values(value).forEach(deepFreeze);
    return Object.freeze(value);
  }
  return value;
}

/**
 * The r7 `receipt_producer_step_registry` entry this ruling calls for, in r7's
 * own thirteen declared fields and in r7's own field order
 * (tools/doctorcre-v5-review.cjs:235-249). Nothing is null any more: the four
 * values that were, and where each was read from, are in RESOLVED_FROM_R7.
 *
 * `depends_on_step_refs` is NOT restated here as a literal — it is read from
 * gate-zero-assurance.v5.js's frozen predecessor list, which the V5-A02 test
 * already proves identical to the validator's assertion at
 * tools/doctorcre-v5-review.cjs:1234-1244. Two copies would be two authorities.
 */
function v5A02GateZeroProducerRegistryEntry(predecessorStepRefs) {
  if (!Array.isArray(predecessorStepRefs) || predecessorStepRefs.length === 0)
    throw new TypeError("the predecessor step refs are required and are not defaulted here");
  return deepFreeze({
    step_ref: GATE_ZERO_STEP_REF,
    produces_gate_ids: [V5_A02_GATE_ZERO_GATE_ID],
    produces_receipt_refs: [V5_A02_GATE_ZERO_RECEIPT_REF],
    depends_on_step_refs: [...predecessorStepRefs].sort(),
    // Gate Zero consumes accepted predecessor OUTCOMES, not gates. r7 answers
    // that an empty list is legal rather than refused: six of its thirty-eight
    // producer rows carried one before this amendment, and invariant
    // `phase-specific-registration-and-outcome-evaluation` makes only a
    // producer's DECLARED consumes_gate_ids required earlier evidence.
    consumes_gate_ids: [],
    producer_role: V5_A02_GATE_ZERO_PRODUCER_ROLE,
    oracle_ref: V5_A02_GATE_ZERO_ORACLE_REF,
    oracle_version: V5_A02_GATE_ZERO_ORACLE_VERSION,
    evidence_scope: "candidate-and-test",
    subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
    // Both read from r7's closed registries. `assurance` because Q036.D1 — the
    // decision that settled Gate Zero, and the obligation the new gate carries
    // — has target `assurance_fabric`; `pre_activation` because every producer
    // that depends on Gate Zero sits in that phase or later.
    target_dag: "assurance",
    causal_phase: "pre_activation",
  });
}

/**
 * The four fields that were null until the amendment, each with the answer r7
 * itself gave and where in the packet it was read. A reviewer should read this
 * list as the audit trail for the four values above, not as a formality.
 * `UNRESOLVED_WITHOUT_R7` is deliberately kept as an EMPTY exported array
 * rather than deleted: a reader who imports it and finds it empty learns that
 * the questions were answered, where a missing export would only look like a
 * refactor.
 */
export const RESOLVED_FROM_R7 = deepFreeze([
  {
    field: "consumes_gate_ids",
    read_from: "r7 validation_invariants + the six of thirty-eight producer rows that carry an empty list",
    answer: "[] — an empty list is legal, not refused",
  },
  {
    field: "target_dag",
    read_from: "r7 target_dag_registry (closed set of 10) via Q036.D1's target assurance_fabric",
    answer: "assurance",
  },
  {
    field: "causal_phase",
    read_from: "r7 causal_phase_registry (closed set of 8)",
    answer: "pre_activation",
  },
  {
    field: "produces_gate_ids[0]",
    read_from: "r7 consumer_gate_registry, which the amendment grew from 27 members to 28",
    answer:
      "gate-zero-read-only-accepted — admitted by the amendment under Joe's ruling on loop 589; it was NOT in the registry before, and the sibling naming convention is why that is the id that was admitted",
  },
]);

/** Empty since 2026-09-12. See RESOLVED_FROM_R7 for what each answer was. */
export const UNRESOLVED_WITHOUT_R7 = deepFreeze([]);

/**
 * Retry policy, from the ruling: a failed Gate Zero run is retryable, and every
 * run is kept. A gate whose failures vanish cannot be audited, and Gate Zero's
 * whole subject is whether the machinery is telling the truth.
 */
export const V5_A02_GATE_ZERO_RETRY_POLICY = deepFreeze({
  retryable: true,
  runs_retained: "every_run",
  failed_run_retained: true,
  retry_supersedes_prior_digest: false,
});

/**
 * The registration as one closed record. Callers read this; they cannot change
 * it, and binding a seat to the role is not something any caller can do here.
 */
function v5A02GateZeroProducerRegistration(predecessorStepRefs) {
  return deepFreeze({
    schema_version: V5_A02_PRODUCER_REGISTRATION_SCHEMA_VERSION,
    registration_status: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS,
    decision_ref: V5_A02_GATE_ZERO_PRODUCER_DECISION_REF,
    r7_entry_present: r7EntryPresence(
      v5A02GateZeroProducerRegistryEntry(predecessorStepRefs), V5_A02_GATE_ZERO_R7_PACKET_SHA256),
    r7_packet_sha256: V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    r7_amendment_decision_ref: V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF,
    registry_entry: v5A02GateZeroProducerRegistryEntry(predecessorStepRefs),
    combiner: V5_A02_GATE_ZERO_COMBINER,
    retry_policy: V5_A02_GATE_ZERO_RETRY_POLICY,
    unresolved_without_r7: [...UNRESOLVED_WITHOUT_R7],
    resolved_from_r7: [...RESOLVED_FROM_R7],
    // The seat. Registering a role does not staff one.
    oracle_seat_bound: false,
    oracle_seat_owed:
      "an independent seat, distinct from the V5-A02 builder, holding oracle:gate-producer:gate-zero-read-only",
  });
}

/**
 * THE REGISTRATION. One frozen constant over the hard-bound predecessors above
 * — the only authority-bearing export of this module. Callers read it; they
 * cannot build another one over references of their own, and binding a seat to
 * the role is not something any caller can do here.
 */
export const V5_A02_GATE_ZERO_PRODUCER_REGISTRATION = deepFreeze(
  v5A02GateZeroProducerRegistration(V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS));

/**
 * `r7_entry_present` as a module-level constant, for the readers that want the
 * one boolean. It is the SAME derivation the registration carries — read off
 * the registration rather than re-derived, so the two can never disagree.
 */
export const V5_A02_GATE_ZERO_R7_ENTRY_PRESENT =
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.r7_entry_present;
