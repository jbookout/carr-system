// V5-A02 half one — the Gate Zero read-only checker, proved clause by clause.
//
// THE NEGATIVE COMES FIRST, deliberately. `emitGateZeroOutcome` cannot reach a
// pass for any caller on any input, because r7 registers no producer for
// `step:gate-zero-read-only-outcome`. So the first suite is the one that walks
// every caller-controlled input shape and asserts the privileged outcome stays
// unreachable — including the input that makes the JOIN come out perfect.
//
// The join and the graph are then proved as PURE PREDICATES over fixture
// shapes, which is the honest thing a checker can prove before its authoritative
// store exists. Every negative is a single NAMED mutation of one clean request
// that allows, and each `clean*()` returns a fresh deep copy so a mutation in
// one test cannot leak into another.
//
//   node --test mcp-server/test/gate-zero-assurance.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { GATE_ZERO_STEP_REF } from "../src/benchmark-minimum.v5.js";
import {
  V5_A02_GATE_ZERO_SCHEMA_VERSION,
  V5_A02_POLICY_VERSION,
  V5_A02_DECISION_IDS,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_SCHEDULER_STEP_REF,
  V5_A02_OBSERVATION_STATES,
  V5_A02_ACCEPTANCE_STATES,
  V5_A02_CRITERIA_STATES,
  V5_A02_GATE_CONCLUSIONS,
  V5_A02_GATE_ZERO_REASON_IDS,
  V5_A02_GATE_ZERO_PRODUCER_SEAM,
  evaluatePredecessorObservation,
  evaluateSchedulerCanary,
  evaluateGateGraph,
  evaluateGateZeroJoin,
  emitGateZeroOutcome,
  v5A02GateZeroPolicyPreimage,
  v5A02GateZeroPolicyDigest,
  v5A02GateZeroPolicyCanonicalBytes,
} from "../src/gate-zero-assurance.v5.js";

const AS_OF = "2026-09-11T18:00:00Z";
const copy = value => JSON.parse(JSON.stringify(value));

function outcomeBody(stepRef, suffix) {
  return {
    step_ref: stepRef,
    outcome_ref: `outcome:${suffix}`,
    acceptance_state: "accepted",
    criteria_state: "criteria_met",
    accepted_by_identity: "joe",
    accepted_at: "2026-09-06T16:03:09Z",
  };
}

function observation(stepRef, suffix) {
  const outcome = outcomeBody(stepRef, suffix);
  return {
    step_ref: stepRef,
    state: "observed",
    outcome_digest: digest(outcome),
    outcome,
    observed_at: "2026-09-06T16:03:09Z",
  };
}

const SCHEDULER_OUTCOME_DIGEST = digest(outcomeBody(V5_A02_SCHEDULER_STEP_REF, "scheduler"));
const CANARY_DIGEST = `sha256:${"a".repeat(64)}`;

function cleanCanary() {
  return {
    canary_id: "canary-2026-09-11-0001",
    dispatched_at: "2026-09-11T17:00:00Z",
    expected_digest: CANARY_DIGEST,
    scheduler_outcome_digest: SCHEDULER_OUTCOME_DIGEST,
  };
}

function cleanReadback() {
  return {
    canary_id: "canary-2026-09-11-0001",
    state: "observed",
    observed_at: "2026-09-11T17:00:30Z",
    observed_digest: CANARY_DIGEST,
  };
}

function cleanGates() {
  return [
    { gate_id: "assurance-fabric-preactivation", conclusion: "success", depends_on: ["repo-hygiene"] },
    { gate_id: "repo-hygiene", conclusion: "success", depends_on: [] },
    { gate_id: "scheduler-truth", conclusion: "success", depends_on: ["repo-hygiene"] },
  ];
}

function cleanJoin() {
  return {
    as_of: AS_OF,
    predecessor_observations: [
      observation("step:scheduler-active-receipt", "scheduler"),
      observation("step:wr40-repository-outcome", "wr40"),
      observation("step:wr46-dissolution-outcome", "wr46"),
      observation("step:wr54-backup-recovery-outcome", "wr54"),
    ],
    scheduler_canary: cleanCanary(),
    scheduler_readback: cleanReadback(),
    gates: cleanGates(),
  };
}

// ---------------------------------------------------------------------------
// THE PRIVILEGED OUTCOME IS UNREACHABLE.
// ---------------------------------------------------------------------------

test("GATE ZERO: the join can be made perfect and the outcome is still not passable", () => {
  const result = emitGateZeroOutcome(cleanJoin());
  assert.equal(result.joins_exactly, true, "the fixture join must be the perfect one");
  assert.equal(result.passable, false);
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "gate_zero_producer_seam_unavailable");
  assert.equal(result.not_passable_because, "producer undecided");
  assert.equal(result.producer_bound, false);
  assert.equal(result.producer_seam, V5_A02_GATE_ZERO_PRODUCER_SEAM);
  assert.equal(result.gate_zero_step_ref, GATE_ZERO_STEP_REF);
});

test("GATE ZERO: r7 registers no producer contract, so every such field is null", () => {
  const result = emitGateZeroOutcome(cleanJoin());
  for (const field of ["producer_role", "oracle_ref", "output_schema_ref", "evidence_scope",
    "produced_gate_id", "outcome_digest", "observed_at"]) {
    assert.equal(result[field], null, `${field} must be null, not invented`);
  }
  assert.deepEqual(result.undecided_governance_questions, [
    "who or what issues the Gate Zero outcome",
    "the closed field set the outcome carries",
    "the pass rule over that field set",
    "whether a failed Gate Zero run is retryable",
  ]);
});

test("GATE ZERO: no caller-controlled input shape can reach a pass", () => {
  // Every field a caller controls, varied across its whole domain. The outcome
  // is fixed: there is no input that makes `passable` true.
  const shapes = [];
  for (const state of V5_A02_OBSERVATION_STATES) {
    for (const acceptance of V5_A02_ACCEPTANCE_STATES) {
      for (const criteria of V5_A02_CRITERIA_STATES) {
        const request = cleanJoin();
        const target = request.predecessor_observations[1];
        target.state = state;
        if (state === "observed") {
          target.outcome.acceptance_state = acceptance;
          target.outcome.criteria_state = criteria;
          target.outcome_digest = digest(target.outcome);
        } else {
          target.outcome = null;
          target.outcome_digest = null;
          target.observed_at = null;
        }
        shapes.push(request);
      }
    }
  }
  for (const conclusion of V5_A02_GATE_CONCLUSIONS) {
    const request = cleanJoin();
    request.gates[0].conclusion = conclusion;
    shapes.push(request);
  }
  for (const readbackState of V5_A02_OBSERVATION_STATES) {
    const request = cleanJoin();
    request.scheduler_readback.state = readbackState;
    if (readbackState !== "observed") {
      request.scheduler_readback.observed_at = null;
      request.scheduler_readback.observed_digest = null;
    }
    shapes.push(request);
  }
  assert.equal(shapes.length, 35, "the sweep must cover every caller-controlled domain");
  for (const request of shapes) {
    const result = emitGateZeroOutcome(request);
    assert.equal(result.passable, false);
    assert.equal(result.reason_id, "gate_zero_producer_seam_unavailable");
    assert.equal(result.producer_bound, false);
  }
});

test("GATE ZERO: a producer cannot be handed in as a second argument", () => {
  assert.throws(
    () => emitGateZeroOutcome(cleanJoin(), { emitOutcome: () => ({ passable: true }) }),
    error => error instanceof V5BoundaryError &&
      error.code === "gate_zero_producer_is_not_an_argument");
});

test("GATE ZERO: the refusal still reports the exact evidence it read", () => {
  const request = cleanJoin();
  request.predecessor_observations[2].outcome.criteria_state = "criteria_not_met";
  request.predecessor_observations[2].outcome_digest =
    digest(request.predecessor_observations[2].outcome);
  const result = emitGateZeroOutcome(request);
  assert.equal(result.passable, false);
  const read = result.predecessor_evidence_read;
  assert.equal(read.length, 4);
  const wr46 = read.find(entry => entry.step_ref === "step:wr46-dissolution-outcome");
  assert.equal(wr46.criteria_state, "criteria_not_met");
  assert.equal(wr46.satisfied, false);
  assert.equal(wr46.reason_id, "predecessor_criteria_not_met");
  assert.equal(wr46.outcome_ref, "outcome:wr46");
});

// ---------------------------------------------------------------------------
// THE FOUR PREDECESSORS ARE THE FROZEN PLAN'S.
// ---------------------------------------------------------------------------

test("PREDECESSORS: the four bound predecessors match the frozen plan validator exactly", () => {
  const validator = readFileSync(
    fileURLToPath(new URL("../../tools/doctorcre-v5-review.cjs", import.meta.url)), "utf8");
  const block = validator.slice(validator.indexOf("const gateZeroExpected = ["));
  const stated = [...block.slice(0, block.indexOf("]")).matchAll(/"(step:[a-z0-9-]+)"/g)]
    .map(match => match[1]).sort();
  assert.equal(stated.length, 4, "the validator must still name four predecessors");
  assert.deepEqual([...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS], stated);
});

test("PREDECESSORS: the scheduler step ref is one of the four", () => {
  assert.ok(V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.includes(V5_A02_SCHEDULER_STEP_REF));
});

test("JOIN: the clean four join exactly", () => {
  const result = evaluateGateZeroJoin(cleanJoin());
  assert.equal(result.joins_exactly, true);
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, null);
  assert.deepEqual(result.predecessors_missing, []);
  assert.deepEqual(result.predecessors_unsatisfied, []);
  assert.equal(result.is_gate_zero_pass, false, "a join is never a Gate Zero pass");
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

test("JOIN: a missing predecessor refuses and names it", () => {
  const request = cleanJoin();
  request.predecessor_observations = request.predecessor_observations
    .filter(entry => entry.step_ref !== "step:wr54-backup-recovery-outcome");
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.joins_exactly, false);
  assert.equal(result.reason_id, "predecessor_set_incomplete");
  assert.deepEqual(result.predecessors_missing, ["step:wr54-backup-recovery-outcome"]);
});

test("JOIN: a pending predecessor is a stand-in and is named absent", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.state = "pending";
  target.outcome = null;
  target.outcome_digest = null;
  target.observed_at = null;
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.joins_exactly, false);
  assert.equal(result.predecessor_results["step:wr40-repository-outcome"].reason_id,
    "predecessor_outcome_absent");
});

test("JOIN: an accepted criteria_not_met outcome clears no milestone", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.criteria_state = "criteria_not_met";
  target.outcome_digest = digest(target.outcome);
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.joins_exactly, false);
  assert.equal(result.reason_id, "predecessor_criteria_not_met");
});

test("JOIN: a proposal pending human acceptance does not close a predecessor", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.acceptance_state = "pending_human_acceptance";
  target.outcome_digest = digest(target.outcome);
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.reason_id, "predecessor_outcome_not_accepted");
});

test("JOIN: an outcome that does not hash to its own digest is refused", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].outcome.outcome_ref = "outcome:swapped-after-hashing";
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.reason_id, "predecessor_outcome_digest_mismatch");
});

test("JOIN: an outcome body naming another step cannot stand in", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.step_ref = "step:wr46-dissolution-outcome";
  target.outcome_digest = digest(target.outcome);
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.reason_id, "predecessor_outcome_bound_to_other_step");
});

test("JOIN: one outcome record cannot close two predecessors", () => {
  const request = cleanJoin();
  // Two DIFFERENT bodies, each correctly naming its own step, but both citing
  // the SAME outcome record. Every binding check passes; only the reuse clause
  // can catch it.
  const wr40 = outcomeBody("step:wr40-repository-outcome", "wr46");
  const wr46 = outcomeBody("step:wr46-dissolution-outcome", "wr46");
  request.predecessor_observations[1] = {
    step_ref: "step:wr40-repository-outcome", state: "observed",
    outcome_digest: digest(wr40), outcome: wr40, observed_at: "2026-09-06T16:03:09Z",
  };
  request.predecessor_observations[2] = {
    step_ref: "step:wr46-dissolution-outcome", state: "observed",
    outcome_digest: digest(wr46), outcome: wr46, observed_at: "2026-09-06T16:03:09Z",
  };
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.joins_exactly, false);
  assert.equal(result.reason_id, "duplicate_predecessor_outcome");
  assert.equal(result.predecessor_results["step:wr46-dissolution-outcome"].reason_id,
    "duplicate_predecessor_outcome");
  assert.equal(result.predecessor_results["step:wr40-repository-outcome"].ok, true,
    "the first citation stands; the second one is the reuse");
});

test("JOIN: an outcome observed after the reference instant is not evidence at it", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].observed_at = "2026-09-12T00:00:00Z";
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.reason_id, "predecessor_observed_after_reference");
});

test("JOIN: a step outside the four is unreadable, not refusable", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].step_ref = "step:some-other-outcome";
  assert.throws(() => evaluateGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_predecessor_step");
});

test("JOIN: the same predecessor twice is unreadable", () => {
  const request = cleanJoin();
  request.predecessor_observations.push(observation("step:wr40-repository-outcome", "again"));
  assert.throws(() => evaluateGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "duplicate_predecessor_observation");
});

test("JOIN: an unknown field on an observation is unreadable", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].verified = true;
  assert.throws(() => evaluateGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("JOIN: a pending observation may not smuggle an outcome alongside it", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].state = "pending";
  assert.throws(() => evaluateGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unobserved_states_an_outcome");
});

// ---------------------------------------------------------------------------
// SCHEDULER CANARY AND READBACK.
// ---------------------------------------------------------------------------

test("SCHEDULER: the clean canary and readback join exactly", () => {
  const result = evaluateSchedulerCanary(cleanCanary(), cleanReadback(), SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.ok, true);
});

test("SCHEDULER: a readback of another canary does not join", () => {
  const readback = cleanReadback();
  readback.canary_id = "canary-from-last-week";
  const result = evaluateSchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.reason_id, "scheduler_readback_canary_mismatch");
});

test("SCHEDULER: a readback carrying a different digest does not join", () => {
  const readback = cleanReadback();
  readback.observed_digest = `sha256:${"b".repeat(64)}`;
  const result = evaluateSchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.reason_id, "scheduler_canary_digest_mismatch");
});

test("SCHEDULER: a readback at its own dispatch instant proves nothing ran", () => {
  const canary = cleanCanary();
  const readback = cleanReadback();
  readback.observed_at = canary.dispatched_at;
  const result = evaluateSchedulerCanary(canary, readback, SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.reason_id, "scheduler_readback_not_after_dispatch");
});

test("SCHEDULER: an absent readback is a canary nobody heard", () => {
  const readback = { canary_id: "canary-2026-09-11-0001", state: "absent", observed_at: null, observed_digest: null };
  const result = evaluateSchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.reason_id, "scheduler_readback_absent");
});

test("SCHEDULER: a canary not bound to the accepted scheduler outcome does not join", () => {
  const canary = cleanCanary();
  canary.scheduler_outcome_digest = `sha256:${"c".repeat(64)}`;
  const result = evaluateSchedulerCanary(canary, cleanReadback(), SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.reason_id, "scheduler_canary_not_bound_to_receipt");
});

test("SCHEDULER: with no accepted scheduler outcome the canary is unbindable", () => {
  const result = evaluateSchedulerCanary(cleanCanary(), cleanReadback(), null);
  assert.equal(result.reason_id, "scheduler_canary_not_bound_to_receipt");
});

test("JOIN: a failed scheduler predecessor makes its canary unbindable inside the join", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations.find(
    entry => entry.step_ref === V5_A02_SCHEDULER_STEP_REF);
  target.state = "absent";
  target.outcome = null;
  target.outcome_digest = null;
  target.observed_at = null;
  const result = evaluateGateZeroJoin(request);
  assert.equal(result.joins_exactly, false);
  assert.equal(result.scheduler_canary_result.reason_id, "scheduler_canary_not_bound_to_receipt");
});

// ---------------------------------------------------------------------------
// NON-GREEN PROPAGATION. "A failed injected gate cannot claim green."
// ---------------------------------------------------------------------------

test("GRAPH: the clean graph is green", () => {
  const result = evaluateGateGraph(cleanGates());
  assert.equal(result.green, true);
  assert.deepEqual(result.non_green_gates, []);
  assert.deepEqual(result.inherited_non_green_gates, []);
});

test("GRAPH: an injected failed gate makes the graph non-green and is named", () => {
  const gates = cleanGates();
  gates[2].conclusion = "failure";
  const result = evaluateGateGraph(gates);
  assert.equal(result.green, false);
  assert.equal(result.reason_id, "gate_graph_not_green");
  assert.deepEqual(result.non_green_gates, ["scheduler-truth"]);
});

test("GRAPH: a gate reporting success over a failed ancestor cannot claim green", () => {
  const gates = cleanGates();
  gates[1].conclusion = "failure";               // repo-hygiene, the root
  const result = evaluateGateGraph(gates);
  assert.equal(result.green, false);
  assert.deepEqual(result.non_green_gates, ["repo-hygiene"]);
  assert.deepEqual(result.inherited_non_green_gates,
    ["assurance-fabric-preactivation", "scheduler-truth"]);
});

test("GRAPH: non-green propagates transitively, not just one hop", () => {
  const gates = [
    { gate_id: "leaf", conclusion: "success", depends_on: ["middle"] },
    { gate_id: "middle", conclusion: "success", depends_on: ["root"] },
    { gate_id: "root", conclusion: "failure", depends_on: [] },
  ];
  const result = evaluateGateGraph(gates);
  assert.equal(result.green, false);
  assert.deepEqual(result.inherited_non_green_gates, ["leaf", "middle"]);
});

test("GRAPH: every non-success conclusion is non-green", () => {
  for (const conclusion of ["cancelled", "failure", "pending", "unknown"]) {
    const gates = cleanGates();
    gates[1].conclusion = conclusion;
    assert.equal(evaluateGateGraph(gates).green, false, `${conclusion} must not be green`);
  }
  const gates = cleanGates();
  gates[1].conclusion = "success";
  assert.equal(evaluateGateGraph(gates).green, true);
});

test("GRAPH: a gate whose ancestor is not in the graph is not green", () => {
  const gates = cleanGates();
  gates[0].depends_on = ["never-read"];
  const result = evaluateGateGraph(gates);
  assert.equal(result.green, false);
  assert.equal(result.reason_id, "gate_dependency_unknown");
  assert.deepEqual(result.unknown_dependencies, ["assurance-fabric-preactivation->never-read"]);
});

test("GRAPH: an empty graph is not green", () => {
  assert.equal(evaluateGateGraph([]).green, false);
});

test("GRAPH: a self-asserted green field is unreadable", () => {
  const gates = cleanGates();
  gates[1].conclusion = "failure";
  gates[1].claimed_green = true;
  assert.throws(() => evaluateGateGraph(gates),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("GRAPH: a cyclic dependency graph is unreadable", () => {
  const gates = [
    { gate_id: "a", conclusion: "success", depends_on: ["b"] },
    { gate_id: "b", conclusion: "success", depends_on: ["a"] },
  ];
  assert.throws(() => evaluateGateGraph(gates),
    error => error instanceof V5BoundaryError && error.code === "cyclic_gate_graph");
});

test("GRAPH: an unsorted or repeating dependency list is unreadable", () => {
  const unsorted = cleanGates();
  unsorted[0].depends_on = ["scheduler-truth", "repo-hygiene"];
  assert.throws(() => evaluateGateGraph(unsorted),
    error => error instanceof V5BoundaryError && error.code === "unsorted_list");
  const repeating = cleanGates();
  repeating[0].depends_on = ["repo-hygiene", "repo-hygiene"];
  assert.throws(() => evaluateGateGraph(repeating),
    error => error instanceof V5BoundaryError && error.code === "duplicate_member");
});

test("JOIN: a non-green gate graph blocks the join even with four clean predecessors", () => {
  const request = cleanJoin();
  request.gates[1].conclusion = "failure";
  const result = evaluateGateZeroJoin(request);
  assert.deepEqual(result.predecessors_unsatisfied, []);
  assert.equal(result.joins_exactly, false);
  assert.equal(result.reason_id, "gate_graph_not_green");
});

// ---------------------------------------------------------------------------
// ONE PREDICATE, STANDING ALONE.
// ---------------------------------------------------------------------------

test("PREDICATE: one observation can be judged without the rest of the join", () => {
  const result = evaluatePredecessorObservation(
    observation("step:wr46-dissolution-outcome", "wr46"),
    { step_ref: "step:wr46-dissolution-outcome", as_of_ms: Date.parse(AS_OF) });
  assert.equal(result.ok, true);
  assert.equal(result.detail.step_ref, "step:wr46-dissolution-outcome");
});

test("PREDICATE: an observation bound to a different step is refused by name", () => {
  const result = evaluatePredecessorObservation(
    observation("step:wr46-dissolution-outcome", "wr46"),
    { step_ref: "step:wr40-repository-outcome", as_of_ms: Date.parse(AS_OF) });
  assert.equal(result.reason_id, "predecessor_outcome_bound_to_other_step");
});

// ---------------------------------------------------------------------------
// POLICY IDENTITY.
// ---------------------------------------------------------------------------

test("POLICY: the preimage is stable and carries the slice's four decision ids", () => {
  const preimage = v5A02GateZeroPolicyPreimage();
  assert.deepEqual(preimage.decision_ids, ["Q017.D1", "Q036.D1", "Q067.D1", "Q086.D1"]);
  assert.deepEqual(preimage.decision_ids, [...V5_A02_DECISION_IDS]);
  assert.equal(preimage.schema_version, V5_A02_GATE_ZERO_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_A02_POLICY_VERSION);
  assert.equal(preimage.gate_zero_passable, false);
  assert.equal(preimage.producer_bound, false);
});

test("POLICY: the digest is deterministic and matches its canonical bytes", () => {
  assert.equal(v5A02GateZeroPolicyDigest(), v5A02GateZeroPolicyDigest());
  assert.equal(v5A02GateZeroPolicyDigest(), digest(v5A02GateZeroPolicyPreimage()));
  assert.equal(v5A02GateZeroPolicyCanonicalBytes(),
    JSON.stringify(JSON.parse(v5A02GateZeroPolicyCanonicalBytes())));
});

test("POLICY: every reason this module can answer with is registered", () => {
  const source = readFileSync(
    fileURLToPath(new URL("../src/gate-zero-assurance.v5.js", import.meta.url)), "utf8");
  const used = [...source.matchAll(/reason\("([a-z_]+)"\)/g)].map(match => match[1]);
  assert.ok(used.length > 0);
  for (const id of used)
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(id), `${id} is not registered`);
});

test("POLICY: every result carries the no-effects marker", () => {
  const request = cleanJoin();
  for (const result of [evaluateGateZeroJoin(request), emitGateZeroOutcome(request),
    evaluateGateGraph(cleanGates())]) {
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(result));
  }
});
