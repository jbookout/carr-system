// DoctorCRE v5 slice V5-A02, half one: THE GATE ZERO READ-ONLY CHECKER — the
// predecessor/scheduler join, the non-green propagation graph, and the honest
// refusal that stands where the Gate Zero OUTCOME would be.
//
// WHAT THIS FILE MAY NOT DECIDE, said first because it is the whole point.
// r7 — the frozen design packet — registers NO producer, oracle, output schema,
// evidence scope or gate id for `step:gate-zero-read-only-outcome`.
// benchmark-minimum.v5.js:52-67 (slice V5-A00) says so in its own words and
// refuses to invent one. This file makes the same refusal, one layer earlier and
// one layer louder: `emitGateZeroOutcome` CANNOT RETURN A PASS AT ALL, for any
// caller, on any input, because the producer seam it would have to emit through
// (V5_A02_GATE_ZERO_PRODUCER_SEAM) is bound to null. Choosing a producer and a
// receipt schema is a governance ruling with the same weight as the rulings
// already in the design; an author seat that picks one has silently legislated.
//
// SO WHAT IS REAL HERE. The three deterministic predicates the catalog's
// checkable_done names, proved against fixture shapes exactly as V5-F02 proves
// `verifyReleaseReceipt` and `evaluateReleaseCheck` before its Deployment
// Controller exists:
//
//   1. evaluateGateZeroJoin  — "WR prerequisites and scheduler canary/readback
//      join exactly". The four bound predecessors, each exactly once, each
//      self-binding, plus a scheduler canary whose readback joins it and whose
//      dispatch is bound to the scheduler receipt itself.
//   2. evaluateGateGraph     — "failed injected gate cannot claim green". A
//      non-green gate propagates to every descendant, so a gate cannot report
//      success over a failed ancestor.
//   3. emitGateZeroOutcome   — the privileged path, and the one that refuses.
//
// THE FOUR PREDECESSORS ARE NOT THIS FILE'S CHOICE. They are the frozen plan's,
// enforced today at tools/doctorcre-v5-review.cjs:1234-1244, which asserts that
// `step:gate-zero-read-only-outcome`'s `depends_on` is exactly
// {wr46-dissolution, wr40-repository, wr54-backup-recovery, scheduler-active}.
// They are restated here because that validator is CommonJS plan-shape code that
// this module cannot import, and gate-zero-assurance.v5.test.mjs reads that file
// and asserts the two lists are identical — so a change to the frozen plan turns
// this module's test red instead of letting the two drift apart in silence.
//
// A CALLER-SUPPLIED FACT IS NEVER AUTHORITY. There is no `verified`,
// `accepted_by_me`, `claimed_green`, `stand_in_ok` or holder-injection field
// anywhere below, and a closed schema refuses one rather than ignoring it. Every
// predicate is PURE — no filesystem, no network, no database, no clock, no
// environment — and takes its reference instant from the caller's `as_of`.
// `V5_NO_EFFECTS` rides on every result to say so in the record.
//
// WHAT A "STAND-IN" IS, PROCEDURALLY, because a checker that cannot tell one
// from a real outcome measures nothing. An observation is a stand-in when it
// fails any of the five structural bindings below, and each refuses by its own
// name rather than by a judgment call:
//   (a) it states no outcome at all                  -> predecessor_outcome_absent
//   (b) its body names a different step              -> predecessor_outcome_bound_to_other_step
//   (c) its digest is not the digest of its body     -> predecessor_outcome_digest_mismatch
//   (d) it is not an ACCEPTED criteria_met outcome   -> predecessor_outcome_not_accepted
//                                                    -> predecessor_criteria_not_met
//   (e) it reuses another predecessor's outcome      -> duplicate_predecessor_outcome
// (d) is the trap the Gate Zero readiness study names explicitly: accepting
// WR-000040's pending `criteria_not_met` proposal closes the review and does NOT
// clear milestone 4. An accepted refusal is still a refusal.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — `decision` is "allow" or "refuse" with a
//     stable `reason_id`. Silence is never an allow.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, open schemas,
//     unknown enum members, malformed digests and cyclic graphs are not policy
//     questions: the module cannot read the request, so it fails closed.
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

export { GATE_ZERO_STEP_REF, V5_NO_EFFECTS };

export const V5_A02_GATE_ZERO_SCHEMA_VERSION = "doctorcre-v5-a02-gate-zero-assurance.v1";
export const V5_A02_POLICY_VERSION = 1;

/** The four decisions the catalog maps to V5-A02. Carried, not interpreted. */
export const V5_A02_DECISION_IDS = deepFreeze(["Q017.D1", "Q036.D1", "Q067.D1", "Q086.D1"]);

const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const STEP_REF = /^step:[a-z0-9][a-z0-9-]{0,127}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const GATE_ID = /^[a-z0-9][a-z0-9-]{0,127}$/;

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

function object(value, path) {
  if (!isPlainObject(value)) fail("not_an_object", `${path} must be a plain object`, { path });
  return value;
}

/** An open schema is an unenforced one: every request object is closed. */
function closed(value, allowed, path) {
  object(value, path);
  for (const key of Object.keys(value))
    if (!allowed.includes(key))
      fail("unknown_field", `${path}.${key} is not a field this module reads`,
        { path, key, allowed: [...allowed].sort() });
  return value;
}

function exact(value, allowed, path) {
  closed(value, allowed, path);
  for (const key of allowed)
    if (!Object.hasOwn(value, key))
      fail("missing_field", `${path}.${key} is required`, { path, key });
  return value;
}

function str(value, path) {
  if (typeof value !== "string" || value.length === 0)
    fail("not_a_string", `${path} must be a non-empty string`, { path });
  return value;
}

function pattern(value, re, code, path) {
  str(value, path);
  if (!re.test(value)) fail(code, `${path} is malformed`, { path, value });
  return value;
}

function instant(value, path) {
  str(value, path);
  if (!ISO_INSTANT.test(value) || Number.isNaN(Date.parse(value)))
    fail("malformed_instant", `${path} must be a UTC ISO-8601 instant`, { path, value });
  return Date.parse(value);
}

function member(value, allowed, path) {
  str(value, path);
  if (!allowed.includes(value))
    fail("unknown_enum_member", `${path} is not a member this module knows`,
      { path, value, allowed: [...allowed].sort() });
  return value;
}

function list(value, path) {
  if (!Array.isArray(value)) fail("not_an_array", `${path} must be an array`, { path });
  return value;
}

// ---------------------------------------------------------------------------
// The frozen plan's four bound predecessors, and the scheduler among them.
// ---------------------------------------------------------------------------

/**
 * Exactly the `depends_on` set that tools/doctorcre-v5-review.cjs:1234-1244
 * asserts on `step:gate-zero-read-only-outcome`, C-sorted so two readers
 * enumerate it identically. A member absent here is not admissible evidence for
 * Gate Zero; a member present here is mandatory.
 */
export const V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS = deepFreeze([
  "step:scheduler-active-receipt",
  "step:wr40-repository-outcome",
  "step:wr46-dissolution-outcome",
  "step:wr54-backup-recovery-outcome",
].sort());

/** The one predecessor the scheduler canary must itself be bound to. */
export const V5_A02_SCHEDULER_STEP_REF = "step:scheduler-active-receipt";

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

/** Every refusal this module can answer with. Closed, so a reason is checkable. */
export const V5_A02_GATE_ZERO_REASON_IDS = deepFreeze([
  "duplicate_predecessor_observation",
  "duplicate_predecessor_outcome",
  "gate_dependency_unknown",
  "gate_graph_not_green",
  "gate_zero_producer_seam_unavailable",
  "predecessor_criteria_not_met",
  "predecessor_observed_after_reference",
  "predecessor_outcome_absent",
  "predecessor_outcome_bound_to_other_step",
  "predecessor_outcome_digest_mismatch",
  "predecessor_outcome_not_accepted",
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
// THE PRODUCER SEAM, NAMED BY THIS MODULE.
//
// The seam a Gate Zero OUTCOME could be emitted through. It is a NAME, not a
// port: nothing a caller passes can become this, because no exported function
// accepts a producer as an argument and this module exports no way to bind one.
// ---------------------------------------------------------------------------
export const V5_A02_GATE_ZERO_PRODUCER_SEAM = "seam:gate-zero-read-only-outcome-producer";

/**
 * And here is the binding: `null`, because r7 registers no producer for
 * `step:gate-zero-read-only-outcome` — no role, no oracle, no output schema, no
 * evidence scope and no gate id. When that ruling is recorded, this constant is
 * the single place it binds, and `emitGateZeroOutcome` below is the clause it
 * will be read through.
 */
const V5_A02_GATE_ZERO_PRODUCER = null;

/** The bound producer, or null when the seam is unavailable. Takes no input. */
function gateZeroProducer() {
  const producer = V5_A02_GATE_ZERO_PRODUCER;
  return isPlainObject(producer) && typeof producer.emitOutcome === "function" ? producer : null;
}

// ---------------------------------------------------------------------------
// One predecessor observation.
// ---------------------------------------------------------------------------

const OBSERVATION_FIELDS = Object.freeze([
  "step_ref", "state", "outcome_digest", "outcome", "observed_at",
]);
const OUTCOME_FIELDS = Object.freeze([
  "step_ref", "outcome_ref", "acceptance_state", "criteria_state",
  "accepted_by_identity", "accepted_at",
]);

function refused(reasonId, note, detail) {
  return deepFreeze({ ok: false, reason_id: reason(reasonId), note, detail: detail ?? {} });
}

function satisfied(note, detail) {
  return deepFreeze({ ok: true, reason_id: null, note, detail: detail ?? {} });
}

/**
 * A PURE PREDICATE over one predecessor observation. Returns `{ ok }` plus the
 * refusal that stopped it. It decides nothing about Gate Zero — it only says
 * whether this observation is a real, accepted, self-binding outcome of the
 * step it claims to be an outcome of.
 *
 * `binding` is `{ step_ref, as_of_ms }`: the predecessor this observation must
 * be OF, and the reference instant nothing may be observed after.
 */
export function evaluatePredecessorObservation(observation, binding) {
  exact(object(binding, "binding"), ["step_ref", "as_of_ms"], "binding");
  const stepRef = member(binding.step_ref, V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS, "binding.step_ref");
  if (!Number.isInteger(binding.as_of_ms))
    fail("not_an_integer", "binding.as_of_ms must be an integer", { path: "binding.as_of_ms" });

  const path = `observation(${stepRef})`;
  exact(object(observation, path), OBSERVATION_FIELDS, path);
  pattern(observation.step_ref, STEP_REF, "malformed_step_ref", `${path}.step_ref`);
  member(observation.state, V5_A02_OBSERVATION_STATES, `${path}.state`);

  if (observation.step_ref !== stepRef)
    return refused("predecessor_outcome_bound_to_other_step",
      "an observation of another step is not an observation of this one",
      { expected_step_ref: stepRef, observed_step_ref: observation.step_ref });

  // (a) NOTHING WAS SEEN. "pending" and "absent" state no outcome, and an
  // outcome nobody observed is the missing one this join exists to name.
  if (observation.state !== "observed") {
    for (const key of ["outcome", "outcome_digest", "observed_at"])
      if (observation[key] !== null)
        fail("unobserved_states_an_outcome",
          `${path}.${key} must be null when nothing was observed`, { path, key });
    return refused("predecessor_outcome_absent",
      "this predecessor states no observed outcome", { step_ref: stepRef, state: observation.state });
  }

  const outcomePath = `${path}.outcome`;
  exact(object(observation.outcome, outcomePath), OUTCOME_FIELDS, outcomePath);
  pattern(observation.outcome.step_ref, STEP_REF, "malformed_step_ref", `${outcomePath}.step_ref`);
  pattern(observation.outcome.outcome_ref, REF, "malformed_ref", `${outcomePath}.outcome_ref`);
  member(observation.outcome.acceptance_state, V5_A02_ACCEPTANCE_STATES, `${outcomePath}.acceptance_state`);
  member(observation.outcome.criteria_state, V5_A02_CRITERIA_STATES, `${outcomePath}.criteria_state`);
  pattern(observation.outcome_digest, DIGEST, "malformed_digest", `${path}.outcome_digest`);
  const observedMs = instant(observation.observed_at, `${path}.observed_at`);

  // (b) THE BODY NAMES ANOTHER STEP. The envelope agreeing with the binding is
  // not enough: the outcome itself has to be an outcome of this step.
  if (observation.outcome.step_ref !== stepRef)
    return refused("predecessor_outcome_bound_to_other_step",
      "the outcome body is an outcome of a different step",
      { expected_step_ref: stepRef, outcome_step_ref: observation.outcome.step_ref });

  // (c) SELF-BINDING. The cited digest must be the digest of the body that came
  // back. This is the one check a stand-in cannot survive by relabelling.
  const computed = digest(observation.outcome);
  if (computed !== observation.outcome_digest)
    return refused("predecessor_outcome_digest_mismatch",
      "the outcome does not hash to the digest that cited it",
      { cited: observation.outcome_digest, computed });

  // (d) AN ACCEPTED REFUSAL IS STILL A REFUSAL, and the two fields are separate
  // because accepting a criteria_not_met outcome closes a review and clears no
  // milestone.
  if (observation.outcome.acceptance_state !== "accepted")
    return refused("predecessor_outcome_not_accepted",
      "a proposed or rejected outcome does not close its predecessor",
      { acceptance_state: observation.outcome.acceptance_state });
  for (const key of ["accepted_by_identity", "accepted_at"])
    str(observation.outcome[key], `${outcomePath}.${key}`);
  instant(observation.outcome.accepted_at, `${outcomePath}.accepted_at`);
  if (observation.outcome.criteria_state !== "criteria_met")
    return refused("predecessor_criteria_not_met",
      "an accepted outcome that did not meet its criteria clears no milestone",
      { criteria_state: observation.outcome.criteria_state });

  if (observedMs > binding.as_of_ms)
    return refused("predecessor_observed_after_reference",
      "an outcome observed after the reference instant is not evidence at it",
      { observed_at: observation.observed_at });

  return satisfied("accepted criteria_met outcome, self-binding, observed at or before the reference",
    { step_ref: stepRef, outcome_digest: observation.outcome_digest });
}

// ---------------------------------------------------------------------------
// The scheduler canary and its readback.
// ---------------------------------------------------------------------------

const CANARY_FIELDS = Object.freeze([
  "canary_id", "dispatched_at", "expected_digest", "scheduler_outcome_digest",
]);
const READBACK_FIELDS = Object.freeze([
  "canary_id", "state", "observed_at", "observed_digest",
]);

/**
 * "Scheduler canary/readback join exactly", as four bindings rather than a
 * judgment: the readback is OF this canary, it hashes to what the canary
 * expected, it happened strictly after the dispatch, and the canary itself is
 * bound to the scheduler-active outcome the predecessor join proved. Without
 * the fourth, a canary proves a scheduler ran — not that THIS receipt's
 * scheduler ran.
 *
 * `schedulerOutcomeDigest` is null when the scheduler predecessor did not
 * itself pass; the canary is then unbindable and says so.
 */
export function evaluateSchedulerCanary(canary, readback, schedulerOutcomeDigest) {
  exact(object(canary, "canary"), CANARY_FIELDS, "canary");
  str(canary.canary_id, "canary.canary_id");
  const dispatchedMs = instant(canary.dispatched_at, "canary.dispatched_at");
  pattern(canary.expected_digest, DIGEST, "malformed_digest", "canary.expected_digest");
  pattern(canary.scheduler_outcome_digest, DIGEST, "malformed_digest", "canary.scheduler_outcome_digest");

  exact(object(readback, "readback"), READBACK_FIELDS, "readback");
  member(readback.state, V5_A02_OBSERVATION_STATES, "readback.state");

  if (schedulerOutcomeDigest !== null)
    pattern(schedulerOutcomeDigest, DIGEST, "malformed_digest", "schedulerOutcomeDigest");

  if (readback.state !== "observed") {
    for (const key of ["observed_at", "observed_digest"])
      if (readback[key] !== null)
        fail("unobserved_states_a_readback",
          `readback.${key} must be null when nothing was read back`, { path: `readback.${key}`, key });
    return refused("scheduler_readback_absent",
      "a canary nobody read back is a canary nobody heard", { state: readback.state });
  }

  str(readback.canary_id, "readback.canary_id");
  const readbackMs = instant(readback.observed_at, "readback.observed_at");
  pattern(readback.observed_digest, DIGEST, "malformed_digest", "readback.observed_digest");

  if (readback.canary_id !== canary.canary_id)
    return refused("scheduler_readback_canary_mismatch",
      "the readback is of a different canary",
      { canary_id: canary.canary_id, readback_canary_id: readback.canary_id });
  if (readback.observed_digest !== canary.expected_digest)
    return refused("scheduler_canary_digest_mismatch",
      "the readback does not carry what the canary dispatched",
      { expected: canary.expected_digest, observed: readback.observed_digest });
  if (readbackMs <= dispatchedMs)
    return refused("scheduler_readback_not_after_dispatch",
      "a readback at or before its own dispatch proves nothing ran",
      { dispatched_at: canary.dispatched_at, observed_at: readback.observed_at });
  if (schedulerOutcomeDigest === null || canary.scheduler_outcome_digest !== schedulerOutcomeDigest)
    return refused("scheduler_canary_not_bound_to_receipt",
      "the canary is not bound to the accepted scheduler-active outcome",
      { canary_binds: canary.scheduler_outcome_digest, scheduler_outcome_digest: schedulerOutcomeDigest });

  return satisfied("canary and readback join exactly and bind the accepted scheduler outcome",
    { canary_id: canary.canary_id });
}

// ---------------------------------------------------------------------------
// Non-green propagation. "A failed injected gate cannot claim green."
// ---------------------------------------------------------------------------

const GATE_FIELDS = Object.freeze(["gate_id", "conclusion", "depends_on"]);

/**
 * A gate is green when its OWN conclusion is "success" AND every gate it
 * descends from is green. The second half is what makes an injected failure
 * impossible to claim past: a gate that reports success over a failed ancestor
 * is answered `inherited_non_green`, and there is no field on the request that
 * can say otherwise — `claimed_green`, `override`, `waived` and every other
 * self-assertion are unknown fields, and an unknown field is unreadable.
 *
 * A cycle is a CONTRACT VIOLATION: a dependency graph that is not a DAG cannot
 * be walked, so the module fails closed rather than answering over it.
 */
export function evaluateGateGraph(gates) {
  list(gates, "gates");
  const byId = new Map();
  gates.forEach((gate, index) => {
    const path = `gates[${index}]`;
    exact(object(gate, path), GATE_FIELDS, path);
    pattern(gate.gate_id, GATE_ID, "malformed_gate_id", `${path}.gate_id`);
    member(gate.conclusion, V5_A02_GATE_CONCLUSIONS, `${path}.conclusion`);
    list(gate.depends_on, `${path}.depends_on`);
    gate.depends_on.forEach((dep, at) =>
      pattern(dep, GATE_ID, "malformed_gate_id", `${path}.depends_on[${at}]`));
    for (let i = 1; i < gate.depends_on.length; i += 1) {
      if (gate.depends_on[i] === gate.depends_on[i - 1])
        fail("duplicate_member", `${path}.depends_on repeats ${gate.depends_on[i]}`, { path });
      if (gate.depends_on[i] < gate.depends_on[i - 1])
        fail("unsorted_list", `${path}.depends_on must be C-sorted`, { path, at: i });
    }
    if (byId.has(gate.gate_id))
      fail("duplicate_gate_id", `${path}.gate_id repeats ${gate.gate_id}`, { path, gate_id: gate.gate_id });
    byId.set(gate.gate_id, gate);
  });

  // Cycle detection first: an unwalkable graph is unreadable, not refusable.
  const colour = new Map();
  const walk = id => {
    const state = colour.get(id);
    if (state === "done") return;
    if (state === "open") fail("cyclic_gate_graph", `gate ${id} depends on itself`, { gate_id: id });
    const gate = byId.get(id);
    if (!gate) return;
    colour.set(id, "open");
    for (const dep of gate.depends_on) walk(dep);
    colour.set(id, "done");
  };
  for (const id of byId.keys()) walk(id);

  const selfNonGreen = [];
  const unknownDeps = [];
  for (const gate of byId.values()) {
    if (gate.conclusion !== "success") selfNonGreen.push(gate.gate_id);
    for (const dep of gate.depends_on) if (!byId.has(dep)) unknownDeps.push(`${gate.gate_id}->${dep}`);
  }

  // Transitive closure over the DAG, memoised.
  const greenMemo = new Map();
  const isGreen = id => {
    if (greenMemo.has(id)) return greenMemo.get(id);
    const gate = byId.get(id);
    // An ancestor this graph does not contain is not green: an unknown gate is
    // an unread gate, and unread is never success.
    if (!gate) { greenMemo.set(id, false); return false; }
    greenMemo.set(id, false); // pessimistic while the walk is open
    const green = gate.conclusion === "success" && gate.depends_on.every(isGreen);
    greenMemo.set(id, green);
    return green;
  };

  const inherited = [];
  for (const gate of byId.values())
    if (gate.conclusion === "success" && !isGreen(gate.gate_id)) inherited.push(gate.gate_id);

  const allGreen = byId.size > 0 && [...byId.keys()].every(isGreen);
  const reasonId = unknownDeps.length > 0
    ? "gate_dependency_unknown"
    : (allGreen ? null : "gate_graph_not_green");

  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    answer: "gate_graph_green",
    // An EMPTY graph is not green. A Gate Zero that read no gate read nothing.
    green: allGreen,
    decision: allGreen ? "allow" : "refuse",
    reason_id: allGreen ? null : reason(reasonId ?? "gate_graph_not_green"),
    gates_read: [...byId.keys()].sort(),
    non_green_gates: selfNonGreen.sort(),
    inherited_non_green_gates: inherited.sort(),
    unknown_dependencies: unknownDeps.sort(),
    caller_stated_green: false,
    decided_by: "deterministic_checker",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The join.
// ---------------------------------------------------------------------------

const JOIN_FIELDS = Object.freeze([
  "as_of", "predecessor_observations", "scheduler_canary", "scheduler_readback", "gates",
]);

/**
 * "WR prerequisites and scheduler canary/readback join exactly."
 *
 * EXACTLY means exactly: the four bound predecessors, each present once, each a
 * real accepted criteria_met self-binding outcome, no two of them the same
 * outcome, plus a canary/readback pair bound to the scheduler receipt, plus a
 * green gate graph. Anything missing or standing in is named.
 *
 * This is a PURE PREDICATE over evidence the caller supplies. It is NOT a Gate
 * Zero pass and it does not claim to read an authoritative store — there is no
 * such store in this repository, and `emitGateZeroOutcome` below is where that
 * is answered. A `joins_exactly: true` here says only "the shapes you showed me
 * join"; it confers nothing.
 */
export function evaluateGateZeroJoin(request) {
  exact(object(request, "request"), JOIN_FIELDS, "request");
  const asOfMs = instant(request.as_of, "request.as_of");
  list(request.predecessor_observations, "request.predecessor_observations");

  const seenSteps = new Set();
  const seenOutcomeRefs = new Map();
  const perPredecessor = {};
  const evidenceRead = [];

  request.predecessor_observations.forEach((observation, index) => {
    const path = `request.predecessor_observations[${index}]`;
    object(observation, path);
    const stepRef = pattern(observation.step_ref, STEP_REF, "malformed_step_ref", `${path}.step_ref`);
    if (!V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.includes(stepRef))
      fail("unknown_predecessor_step",
        `${path}.step_ref is not one of Gate Zero's four bound predecessors`,
        { path, step_ref: stepRef, allowed: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS] });
    if (seenSteps.has(stepRef))
      fail("duplicate_predecessor_observation",
        `${path}.step_ref repeats ${stepRef}`, { path, step_ref: stepRef });
    seenSteps.add(stepRef);
    const outcome = evaluatePredecessorObservation(observation, { step_ref: stepRef, as_of_ms: asOfMs });
    perPredecessor[stepRef] = outcome;
    evidenceRead.push(deepFreeze({
      step_ref: stepRef,
      state: observation.state,
      outcome_digest: observation.state === "observed" ? observation.outcome_digest : null,
      outcome_ref: observation.state === "observed" ? observation.outcome.outcome_ref : null,
      acceptance_state: observation.state === "observed" ? observation.outcome.acceptance_state : null,
      criteria_state: observation.state === "observed" ? observation.outcome.criteria_state : null,
      observed_at: observation.state === "observed" ? observation.observed_at : null,
      satisfied: outcome.ok,
      reason_id: outcome.reason_id,
    }));
  });

  // (e) ONE OUTCOME RECORD MAY NOT CLOSE TWO PREDECESSORS. Keyed on the
  // outcome_ref, not the digest: two bodies naming two different steps can never
  // share a digest, so a digest key would be a clause that can never fire. The
  // ref is the record's identity, and citing WR-000046's accepted outcome as
  // though it were also WR-000040's is exactly the stand-in this catches.
  // Checked after the per-step pass so the reuse is reported against the step
  // that reused it, in supplied order.
  for (const entry of evidenceRead) {
    if (entry.outcome_ref === null || !perPredecessor[entry.step_ref].ok) continue;
    const firstHolder = seenOutcomeRefs.get(entry.outcome_ref);
    if (firstHolder !== undefined) {
      perPredecessor[entry.step_ref] = refused("duplicate_predecessor_outcome",
        "one outcome record cannot close two predecessors",
        { step_ref: entry.step_ref, also_claimed_by: firstHolder, outcome_ref: entry.outcome_ref });
    } else {
      seenOutcomeRefs.set(entry.outcome_ref, entry.step_ref);
    }
  }

  const missing = V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.filter(step => !seenSteps.has(step));
  for (const step of missing)
    perPredecessor[step] = refused("predecessor_set_incomplete",
      "this predecessor was not supplied at all", { step_ref: step });

  const schedulerOutcome = perPredecessor[V5_A02_SCHEDULER_STEP_REF];
  const schedulerDigest = schedulerOutcome?.ok
    ? schedulerOutcome.detail.outcome_digest ?? null
    : null;
  const scheduler = evaluateSchedulerCanary(
    request.scheduler_canary, request.scheduler_readback, schedulerDigest);

  const gateGraph = evaluateGateGraph(request.gates);

  const unsatisfied = V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.filter(step => !perPredecessor[step].ok);
  const joinsExactly = unsatisfied.length === 0 && scheduler.ok && gateGraph.green;
  const blocking = unsatisfied.length > 0
    ? perPredecessor[unsatisfied[0]].reason_id
    : (!scheduler.ok ? scheduler.reason_id : (gateGraph.green ? null : gateGraph.reason_id));

  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    answer: "gate_zero_predecessor_join",
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    as_of: request.as_of,
    joins_exactly: joinsExactly,
    decision: joinsExactly ? "allow" : "refuse",
    reason_id: joinsExactly ? null : reason(blocking),
    required_predecessors: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    predecessors_supplied: [...seenSteps].sort(),
    predecessors_missing: missing,
    predecessors_unsatisfied: unsatisfied,
    predecessor_results: deepFreeze(perPredecessor),
    // Exactly what was read, per predecessor, said back as CITED facts and never
    // as verified ones. This is the "evidence it read" a blocked Gate Zero owes.
    predecessor_evidence_read: deepFreeze(evidenceRead),
    scheduler_canary_result: scheduler,
    gate_graph_result: gateGraph,
    // This predicate is not wired to an authoritative store and says so.
    evidence_source: "caller_supplied_observations",
    is_gate_zero_pass: false,
    decided_by: "deterministic_checker",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The privileged path, and the refusal that stands where it would be.
// ---------------------------------------------------------------------------

/**
 * THE ONLY FUNCTION THAT COULD EMIT A GATE ZERO OUTCOME, AND IT CANNOT.
 *
 * It takes ONE argument, and the arity IS the boundary: there is no producer
 * parameter, so no caller can hand in the authority that is missing. The seam
 * is bound to null because r7 registers no producer, oracle, output schema,
 * evidence scope or gate id for Gate Zero, so the answer is fixed for every
 * caller on every input:
 *
 *     passable: false, reason "gate_zero_producer_seam_unavailable"
 *
 * ...returned WITH the full join result, so a reader learns exactly which
 * predecessors were read and what each one said. Refusing is not the same as
 * saying nothing.
 */
export function emitGateZeroOutcome(request) {
  // eslint-disable-next-line prefer-rest-params -- the arity IS the boundary.
  if (arguments.length > 1)
    fail("gate_zero_producer_is_not_an_argument",
      "emitGateZeroOutcome takes one request; the producer is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM });

  const join = evaluateGateZeroJoin(request);
  const producerBound = gateZeroProducer() !== null;

  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    answer: "gate_zero_outcome_emission",
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    decision_ids: [...V5_A02_DECISION_IDS],
    // FIXED. Not derived from the join, not derived from the request, not
    // derivable by any caller: the producer is undecided, so nothing here can
    // be passable, not even a join that joins exactly.
    passable: false,
    decision: "refuse",
    reason_id: reason("gate_zero_producer_seam_unavailable"),
    not_passable_because: "producer undecided",
    producer_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM,
    producer_bound: producerBound,
    producer_is_caller_supplied: false,
    // r7 declares none of these for this step. Null is the honest value; a
    // placeholder string here would be the invention this slice exists to
    // refuse.
    producer_role: null,
    oracle_ref: null,
    output_schema_ref: null,
    evidence_scope: null,
    produced_gate_id: null,
    // The outcome fields a consumer would need. Null for the same reason.
    outcome_digest: null,
    observed_at: null,
    undecided_governance_questions: deepFreeze([
      "who or what issues the Gate Zero outcome",
      "the closed field set the outcome carries",
      "the pass rule over that field set",
      "whether a failed Gate Zero run is retryable",
    ]),
    join,
    joins_exactly: join.joins_exactly,
    predecessor_evidence_read: join.predecessor_evidence_read,
    decided_by: "deterministic_checker",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no observation, canary, gate or instant — so
// two callers describing the same policy reach the same digest. It is an
// identity for these bytes: not an acceptance, not a receipt, and not evidence
// for any consumer gate.
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
    producer_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM,
    producer_bound: false,
    gate_zero_passable: false,
  };
}

export function v5A02GateZeroPolicyDigest() {
  return digest(v5A02GateZeroPolicyPreimage());
}

export function v5A02GateZeroPolicyCanonicalBytes() {
  return canonicalJson(v5A02GateZeroPolicyPreimage());
}
