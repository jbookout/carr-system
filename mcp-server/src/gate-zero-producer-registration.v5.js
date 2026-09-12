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
// WHAT THIS FILE IS NOT, said plainly because the whole slice exists to refuse
// exactly these things:
//
//   * It is NOT an r7 entry. r7 — the frozen design packet — is not a file in
//     this repository and is not a section of the doctrine store. It is fed to
//     tools/doctorcre-v5-review.cjs on STDIN, and that validator's header says
//     the database remains the design authority. Registering this producer in
//     r7 ITSELF is a design write on the frozen packet, owed to whoever holds
//     it, and it has not happened. `R7_ENTRY_PRESENT` is false and stays false
//     until it does.
//   * It is NOT a bound producer. Nothing implements this role. No seat holds
//     the oracle. gate-zero-assurance.v5.js's producer seam stays unbound and
//     its answer stays `passable: false`.
//   * It is NOT a Gate Zero receipt, and nothing here can become one.
//
// THREE FIELDS CANNOT BE FILLED FROM ANYTHING REACHABLE HERE, and they are null
// rather than guessed. r7's `target_dag_registry`, `causal_phase_registry` and
// the gate-id vocabulary its `consumer_gate_registry` closes are validated by
// tools/doctorcre-v5-review.cjs against the packet it is handed — this
// repository never states their members. A plausible-looking string in any of
// the three would be indistinguishable from a real one to every later reader,
// which is the defect this slice was built to make impossible. They are listed
// in `UNRESOLVED_WITHOUT_R7` and are the concrete missing facts a reviewer
// should ask about.

import { CONSUMER_GATE_RECEIPT_SCHEMA, GATE_ZERO_STEP_REF } from "./benchmark-minimum.v5.js";

export { GATE_ZERO_STEP_REF };

export const V5_A02_PRODUCER_REGISTRATION_SCHEMA_VERSION =
  "doctorcre-v5-a02-gate-zero-producer-registration.v1";

/** The ruling this file copies. Not a receipt; the authority for the copy. */
export const V5_A02_GATE_ZERO_PRODUCER_DECISION_REF = "20c83902-f150-4d59-beca-915c5c871f95";

/**
 * "provisional" — adopted by the orchestrator under the ruling above and open
 * to Joe's override. Never "accepted", never "active": no partner has signed
 * this, and a word that implied one would be the invention this slice refuses.
 */
export const V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS = "provisional";

/** False until the frozen r7 packet itself carries the entry below. */
export const V5_A02_GATE_ZERO_R7_ENTRY_PRESENT = false;

/**
 * CARD 9, AND IT NAMES A CHARTER RATHER THAN A PERSON. Decision
 * `8a1dad08-8707-4bb0-a159-c2831a00cea2` (2026-09-11) rules that
 * `oracle:gate-producer:gate-zero-read-only` is held by the REVIEWER charter —
 * the only one of the eight whose subject is independent verification of builds
 * it did not make, which is the independence `oracle_seat_owed` asks for.
 *
 * WHAT IT DOES NOT DO, and the ruling says this in its own words: naming a
 * charter is not staffing a desk. `oracle_seat_bound` stays false, because no
 * seat holds it and staffing one is not something this repository can do. What
 * changes here is that the seat is no longer OPEN TO ANYONE — a later seat that
 * claimed the oracle without holding the reviewer charter would now be checkable
 * against a named authority instead of against nothing.
 */
export const V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF = "charter:reviewer";

/** Joe's ruling that puts the charter above on the record. Card 9. */
export const V5_A02_GATE_ZERO_ORACLE_SEAT_DECISION_REF = "8a1dad08-8707-4bb0-a159-c2831a00cea2";

/**
 * CARD 10, AND IT IS A RULING THIS REPOSITORY CANNOT EXECUTE. Decision
 * `311a9af5-3685-4c47-a158-f8dd70870ca1` (2026-09-11) rules AMEND: r7 is to
 * carry the registration below. r7 is neither a file here nor a section of the
 * doctrine store — it is a sealed artifact fed to tools/doctorcre-v5-review.cjs
 * on STDIN, pinned in doctrine only by the digest `normalized_r7_sha256`, so
 * applying the amendment is a design write owed to whoever holds the packet and
 * it re-pins that digest in the same act.
 *
 * So the ruling is CARRIED here and the entry is still ABSENT there. That gap is
 * the honest state and it is why `V5_A02_GATE_ZERO_R7_ENTRY_PRESENT` is still
 * false: a ruling to amend is not an amendment, and this constant is the thing a
 * later reader checks to find out which of the two happened.
 */
export const V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF = "311a9af5-3685-4c47-a158-f8dd70870ca1";

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
 * (tools/doctorcre-v5-review.cjs:235-249). Three are null because this
 * repository cannot know them; see UNRESOLVED_WITHOUT_R7.
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
    // Gate Zero consumes accepted predecessor OUTCOMES, not gates. Whether r7
    // expresses that as an empty list or refuses one is a property of the
    // packet's validation_invariants, which this repository does not hold.
    consumes_gate_ids: null,
    producer_role: V5_A02_GATE_ZERO_PRODUCER_ROLE,
    oracle_ref: V5_A02_GATE_ZERO_ORACLE_REF,
    oracle_version: V5_A02_GATE_ZERO_ORACLE_VERSION,
    evidence_scope: "candidate-and-test",
    subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
    target_dag: null,
    causal_phase: null,
  });
}

/**
 * The fields above that are null, each with the exact reason. A reviewer should
 * read this list as the question set, not as a formality.
 */
export const UNRESOLVED_WITHOUT_R7 = deepFreeze([
  {
    field: "consumes_gate_ids",
    owed_from: "r7 validation_invariants",
    missing_fact: "whether a producer that consumes no gate declares an empty list or is refused",
  },
  {
    field: "target_dag",
    owed_from: "r7 target_dag_registry",
    missing_fact: "the closed member set of the target DAG registry, which this repository never states",
  },
  {
    field: "causal_phase",
    owed_from: "r7 causal_phase_registry",
    missing_fact: "the closed member set of the causal phase registry, which this repository never states",
  },
  {
    field: "produces_gate_ids[0]",
    owed_from: "r7 consumer_gate_registry",
    missing_fact:
      "whether `gate-zero-read-only-accepted` is the id r7 would accept; the name follows the seven siblings' convention and is not read from r7",
  },
]);

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
    r7_entry_present: V5_A02_GATE_ZERO_R7_ENTRY_PRESENT,
    registry_entry: v5A02GateZeroProducerRegistryEntry(predecessorStepRefs),
    combiner: V5_A02_GATE_ZERO_COMBINER,
    retry_policy: V5_A02_GATE_ZERO_RETRY_POLICY,
    unresolved_without_r7: [...UNRESOLVED_WITHOUT_R7],
    // The seat. Registering a role does not staff one, and naming the charter
    // that holds it does not either — which is exactly what card 9 ruled and
    // exactly what these three fields say together.
    oracle_seat_bound: false,
    oracle_seat_charter_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF,
    oracle_seat_charter_decision_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_DECISION_REF,
    // UNCHANGED TEXT, deliberately. Card 9 is carried in the two fields above,
    // so this sentence stays exactly what main published and the whole card-9
    // change is ADDITIVE — which is what lets the gate's switch test prove that
    // nothing else in the emitted answer moved.
    oracle_seat_owed:
      "an independent seat, distinct from the V5-A02 builder, holding oracle:gate-producer:gate-zero-read-only",
    // Card 10: ruled to amend r7, owed to the packet holder, not applied.
    r7_entry_amendment_decision_ref: V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF,
    r7_entry_amendment_applied: false,
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
