// V5-A02 half one — the Gate Zero surface, proved in two halves that must not
// be confused with each other.
//
// HALF A, THE PUBLIC SURFACE. Everything `gate-zero-assurance.v5.js` exports,
// enumerated from the module namespace, swept with every caller-controlled
// input shape — including the exact one-gate `{ conclusion: "success" }`
// construction the PR 985 reviewer used to get `green: true` and
// `decision: "allow"` out of the old module — and asserted never to produce a
// privileged outcome under any name. The surface is also asserted INDIFFERENT to
// its input: every shape produces byte-identical output, so there is no field a
// caller could reach for. Plus a parser-backed scan (V8's own ESM parser, via
// vm.SourceTextModule in a child process — not a regex) proving that src/ holds
// no test-only entry at all and that no module in src/ names a specifier under
// ../test/.
//
// HALF B, THE CLAUSES. The three deterministic clauses, proved clause by clause
// through `./gate-zero-classifiers.v5.testhelper.mjs` — a helper in THIS
// directory, not a module in src/, so a production import of it is impossible
// rather than merely absent. Their answers are conditional by name
// (`would_satisfy_if_authoritative`, `would_be_green_if_authoritative`,
// `would_join_exactly_if_authoritative`) because no authoritative
// predecessor-outcome, scheduler or gate-conclusion reader exists to make them
// anything else. Every negative is a single NAMED mutation of one clean request
// that satisfies, and each `clean*()` returns a fresh deep copy so a mutation in
// one test cannot leak into another.
//
//   node --test mcp-server/test/gate-zero-assurance.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { GATE_ZERO_STEP_REF } from "../src/benchmark-minimum.v5.js";

import * as surface from "../src/gate-zero-assurance.v5.js";
import * as producerModule from "../src/gate-zero-producer-registration.v5.js";
import {
  V5_A02_GATE_ZERO_GATE_ID,
  V5_A02_GATE_ZERO_PRODUCER_ROLE,
} from "../src/gate-zero-producer-registration.v5.js";
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
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_OWED_SEAMS,
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
  V5_A02_GATE_CONCLUSION_READER_SEAM,
  readGateZeroPredecessorJoin,
  readGateGraphAssurance,
  emitGateZeroOutcome,
  v5A02GateZeroPolicyPreimage,
  v5A02GateZeroPolicyDigest,
  v5A02GateZeroPolicyCanonicalBytes,
  v5A02GateZeroR7Presence,
  V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
} from "../src/gate-zero-assurance.v5.js";

import {
  V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
  classifyPredecessorObservation,
  classifySchedulerCanary,
  classifyGateGraph,
  classifyGateZeroJoin,
} from "./gate-zero-classifiers.v5.testhelper.mjs";

const AS_OF = "2026-09-11T18:00:00Z";

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

// ===========================================================================
// HALF A — THE PUBLIC SURFACE.
// ===========================================================================

/**
 * Exactly what this module may export. A new name here is a deliberate decision
 * a reader has to make, which is the point: the four classifiers used to be on
 * this list, and that was the defect.
 */
const EXPECTED_PUBLIC_EXPORTS = [
  "GATE_ZERO_STEP_REF",
  "V5_A02_ACCEPTANCE_STATES",
  "V5_A02_CRITERIA_STATES",
  "V5_A02_DECISION_IDS",
  "V5_A02_GATE_CONCLUSIONS",
  "V5_A02_GATE_CONCLUSION_READER_SEAM",
  "V5_A02_GATE_ZERO_OWED_SEAMS",
  "V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS",
  "V5_A02_GATE_ZERO_PRODUCER_REGISTRATION",
  "V5_A02_GATE_ZERO_PRODUCER_SEAM",
  "V5_A02_GATE_ZERO_R7_PACKET_SHA256",
  "V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256",
  "V5_A02_GATE_ZERO_REASON_IDS",
  "V5_A02_GATE_ZERO_SCHEMA_VERSION",
  "V5_A02_OBSERVATION_STATES",
  "V5_A02_POLICY_VERSION",
  "V5_A02_PREDECESSOR_OUTCOME_READER_SEAM",
  "V5_A02_SCHEDULER_READER_SEAM",
  "V5_A02_SCHEDULER_STEP_REF",
  "V5_NO_EFFECTS",
  "emitGateZeroOutcome",
  "readGateGraphAssurance",
  "readGateZeroPredecessorJoin",
  "v5A02GateZeroPolicyCanonicalBytes",
  "v5A02GateZeroPolicyDigest",
  "v5A02GateZeroPolicyPreimage",
  "v5A02GateZeroR7Presence",
];

/**
 * THE CLOSED PRIVILEGED UNION, copied from the standing rule learned over nine
 * review rounds on 2026-09-11, swept AS EXACT MATCH AND AS SUBSTRING the rule
 * says to. The earlier revision of this file listed only the handful of words
 * this slice happened to use, which is how `present` — a union member — was
 * exported from two modules under the sweep's nose, as `present` on the r7
 * finding and as `r7_entry_present` on the registration. A sweep that only
 * knows the words the author already avoided catches nothing.
 */
const PRIVILEGED_WORDS = [
  "allow", "commit", "prompt", "suppress", "release", "read", "covered",
  "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
  "satisfied", "complete", "admitted", "resumed", "attended", "verified",
  "present", "equivalent", "operational", "active", "green", "joins_exactly",
  "coverage_complete", "favorable",
];

/** The slice's own privileged names, beyond the union's words. */
const PRIVILEGED_TRUE_KEYS = new Set([
  "allowed", "is_gate_zero_pass", "claim_matches_derivation", "producer_bound",
]);

/**
 * A key whose `true` would be a consumer-actionable claim: a union word exactly,
 * a union word anywhere inside the name, this slice's own list, a `would_`
 * classifier field, or an `_if_authoritative` conditional leaking outward.
 */
function privilegedKey(key) {
  const lower = key.toLowerCase();
  if (PRIVILEGED_TRUE_KEYS.has(key)) return true;
  if (lower.startsWith("would_") || lower.includes("_if_authoritative")) return true;
  return PRIVILEGED_WORDS.some(word => lower === word || lower.includes(word));
}

test("SWEEP: the privileged-word set is the standing rule's closed union", () => {
  // The union is a copied constant, so the copy is checked rather than trusted:
  // every word the rule names is swept, and the two names this correction
  // renamed are caught under both their exact and their embedded spellings.
  assert.equal(PRIVILEGED_WORDS.length, 28);
  for (const word of ["present", "read", "pass", "verified", "active", "complete"])
    assert.ok(PRIVILEGED_WORDS.includes(word), `${word} is missing from the union`);
  for (const key of ["present", "r7_entry_present", "gate_zero_present", "would_allow",
    "is_read", "coverage_complete", "was_verified_if_authoritative"])
    assert.equal(privilegedKey(key), true, `${key} must be swept`);
  // And the names this slice now uses are NOT swept, so the sweep is a filter
  // rather than a blanket that would fire on anything.
  for (const key of ["r7_entry_witness", "witness_conjunction", "digest_matches",
    "entry_matches", "gate_registered", "role_registered", "parsed", "retryable",
    "is_superseded_packet", "failed_run_retained"])
    assert.equal(privilegedKey(key), false, `${key} must not be swept`);
});
const PRIVILEGED_VALUES = new Set([
  "allow", "allowed", "green", "pass", "passed", "passable", "operational", "active",
]);

/** Every string, key and boolean in a returned value, walked to the leaves. */
function privilegedFindings(value, path = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const at = `${path}.${key}`;
      if (entry === true && privilegedKey(key)) found.push(`${at} === true`);
      if (key.startsWith("would_")) found.push(`${at} is a classifier field on the public surface`);
      privilegedFindings(entry, at, found);
    }
    return found;
  }
  if (typeof value === "string" && PRIVILEGED_VALUES.has(value)) found.push(`${path} === ${value}`);
  return found;
}

/**
 * Every caller-controlled shape this surface could ever be handed, including
 * the reviewer's. If none of them changes the answer, none of them is authority.
 */
function callerControlledShapes() {
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
  // THE REVIEWER'S CONSTRUCTION, verbatim in spirit: one gate, conclusion
  // success, no dependencies. Against the old module this returned
  // `green: true` and `decision: "allow"`.
  shapes.push({ gates: [{ gate_id: "one-gate", conclusion: "success", depends_on: [] }] });
  shapes.push([{ gate_id: "one-gate", conclusion: "success", depends_on: [] }]);
  // And the shapes that try to say the answer outright.
  shapes.push({ ...cleanJoin(), verified: true, decision: "allow", green: true, passable: true });
  shapes.push({ joins_exactly: true, ok: true });
  shapes.push({}, null, undefined, "allow", 1, true, []);
  return shapes;
}

const PUBLIC_FUNCTIONS_OVER_CALLER_INPUT = [
  ["emitGateZeroOutcome", emitGateZeroOutcome],
  ["readGateZeroPredecessorJoin", readGateZeroPredecessorJoin],
  ["readGateGraphAssurance", readGateGraphAssurance],
];

test("SURFACE: the public export list is exactly the unavailable surface", () => {
  assert.deepEqual(Object.keys(surface).sort(), EXPECTED_PUBLIC_EXPORTS);
  for (const name of Object.keys(surface)) {
    assert.ok(!/^(classify|evaluate|derive)/.test(name),
      `${name} is a classifier name on the public surface`);
    assert.ok(!name.includes("would_"), `${name} is a classifier field on the public surface`);
  }
});

/**
 * Exactly what the producer registration module may export. The authority-
 * bearing record is ONE frozen constant over four hard-bound predecessors; the
 * rest are the pure constants it is assembled from. There is no builder, so
 * there is no argument, so there is no caller-supplied predecessor set — which
 * was the PR 990 defect: an exported builder handed back an authority-stamped
 * provisional registration over whatever references the caller passed in.
 */
const EXPECTED_PRODUCER_EXPORTS = [
  "GATE_ZERO_STEP_REF",
  "RESOLVED_FROM_R7",
  "UNRESOLVED_WITHOUT_R7",
  "V5_A02_GATE_ZERO_COMBINER",
  "V5_A02_GATE_ZERO_GATE_ID",
  "V5_A02_GATE_ZERO_ORACLE_REF",
  "V5_A02_GATE_ZERO_ORACLE_VERSION",
  "V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS",
  "V5_A02_GATE_ZERO_PRODUCER_DECISION_REF",
  "V5_A02_GATE_ZERO_PRODUCER_REGISTRATION",
  "V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS",
  "V5_A02_GATE_ZERO_PRODUCER_ROLE",
  "V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF",
  "V5_A02_GATE_ZERO_R7_ENTRY_WITNESS",
  "V5_A02_GATE_ZERO_R7_PACKET_SHA256",
  "V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256",
  "V5_A02_GATE_ZERO_RECEIPT_REF",
  "V5_A02_GATE_ZERO_RETRY_POLICY",
  "V5_A02_PRODUCER_REGISTRATION_SCHEMA_VERSION",
  "V5_A02_SCHEDULER_STEP_REF",
  "v5A02GateZeroR7Presence",
];

test("PRODUCER: the registration module exports the frozen record and one bytes-only reader", () => {
  assert.deepEqual(Object.keys(producerModule).sort(), EXPECTED_PRODUCER_EXPORTS);
  // EXACTLY ONE export is callable, and it is the byte verifier. The PR 990
  // defect was an exported BUILDER — the only shape that can take a predecessor
  // reference and hand back an authority-stamped record over it. The verifier is
  // the opposite shape: its one argument is bytes, and every other argument
  // throws rather than being interpreted. Proved by value, not by name.
  const callable = Object.entries(producerModule)
    .filter(([, value]) => typeof value === "function").map(([name]) => name);
  assert.deepEqual(callable, ["v5A02GateZeroR7Presence"],
    "an exported builder can be handed caller-supplied predecessor references");
  const reader = producerModule.v5A02GateZeroR7Presence;
  assert.equal(reader.length, 1, "the reader takes bytes and nothing else");
  // An arrow, per amendment 2 of the standing rule: no .prototype, not
  // constructable, and Symbol.hasInstance answers false without touching its
  // left operand.
  assert.equal(Object.hasOwn(reader, "prototype"), false);
  assert.throws(() => Reflect.construct(reader, [""]), TypeError);
  assert.equal({} instanceof reader, false);
  assert.equal(reader[Symbol.hasInstance]({ r7_entry_witness: true }), false);
  // Every shape that is not bytes throws, including the ones that try to name
  // the answer or to hand in a reference set.
  for (const shape of [undefined, null, true, false, 0, 1, {}, [],
    { r7_entry_present: true }, { witness_conjunction: true },
    { registry_entry: {}, predecessor_step_refs: ["step:anything"] },
    ["step:scheduler-active-receipt"], () => true, Symbol("packet"),
    new Proxy({}, { get: () => true })])
    assert.throws(() => reader(shape), V5BoundaryError,
      `${String(typeof shape)} was interpreted instead of refused`);
  // And proved again in the source, so a SECOND callable is red on sight rather
  // than red only once someone adds it to the list above.
  const source = readFileSync(
    fileURLToPath(new URL("../src/gate-zero-producer-registration.v5.js", import.meta.url)), "utf8");
  for (const shape of [/\bexport\s+function\b/, /\bexport\s+default\b/])
    assert.equal(shape.test(source), false,
      `the registration module exports a callable: ${shape}`);
  const arrowExports = [...source.matchAll(
    /\bexport\s+(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function\b|\()/g)]
    .map(match => match[1]);
  assert.deepEqual(arrowExports, ["v5A02GateZeroR7Presence"],
    "only the byte verifier may be an exported callable");
});

test("PRODUCER: the four canonical predecessors are hard-bound into the frozen registration", () => {
  const registration = producerModule.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION;
  assert.deepEqual([...registration.registry_entry.depends_on_step_refs],
    [...producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS]);
  assert.deepEqual([...producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS], [
    "step:scheduler-active-receipt",
    "step:wr40-repository-outcome",
    "step:wr46-dissolution-outcome",
    "step:wr54-backup-recovery-outcome",
  ]);
  // Frozen all the way down: no caller can edit the record in place either.
  assert.ok(Object.isFrozen(registration));
  assert.ok(Object.isFrozen(registration.registry_entry));
  assert.ok(Object.isFrozen(registration.registry_entry.depends_on_step_refs));
  assert.ok(Object.isFrozen(producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS));
  assert.throws(() => { registration.registry_entry.depends_on_step_refs.push("step:mine"); },
    TypeError);
});

test("PRODUCER: the checker's binding constant IS the frozen registration", () => {
  // Same object, not an equal copy — the checker cannot be reading a second
  // registration built over some other predecessor set.
  assert.equal(surface.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
    producerModule.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION);
  assert.equal(surface.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
    producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS);
  assert.equal(surface.V5_A02_SCHEDULER_STEP_REF, producerModule.V5_A02_SCHEDULER_STEP_REF);
  // And what the checker reports comes from that one record.
  const result = emitGateZeroOutcome(cleanJoin());
  assert.equal(result.producer_role,
    producerModule.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry.producer_role);
  assert.equal(result.producer_registration, surface.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION);
});

test("SURFACE: no caller-controlled shape produces a privileged outcome", () => {
  const shapes = callerControlledShapes();
  assert.ok(shapes.length >= 40, "the sweep must cover the caller-controlled domain");
  for (const [name, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT) {
    for (const shape of shapes) {
      const result = fn(shape);
      assert.equal(result.status, "unavailable", name);
      assert.equal(result.decision, "refuse", name);
      assert.equal(result.request_read, false, name);
      assert.equal(result.caller_evidence_admitted, false, name);
      assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(result.reason_id), name);
      assert.deepEqual(privilegedFindings(result), [],
        `${name} leaked a privileged outcome for ${String(JSON.stringify(shape)).slice(0, 80)}`);
      assert.ok(Object.isFrozen(result), name);
      assert.deepEqual(result.effects, V5_NO_EFFECTS, name);
    }
  }
});

test("SURFACE: the answer is byte-identical across every caller shape", () => {
  for (const [name, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT) {
    const first = digest(fn(cleanJoin()));
    for (const shape of callerControlledShapes())
      assert.equal(digest(fn(shape)), first, `${name} answered differently for a caller shape`);
    assert.equal(digest(fn()), first, `${name} answered differently for no argument at all`);
  }
});

test("SURFACE: the Gate Zero outcome is not passable and carries no join", () => {
  for (const shape of [cleanJoin(), undefined, { gates: [] }]) {
    const result = emitGateZeroOutcome(shape);
    assert.equal(result.passable, false);
    assert.equal(result.reason_id, "gate_zero_producer_seam_unavailable");
    assert.equal(result.producer_bound, false);
    assert.equal(result.producer_seam, V5_A02_GATE_ZERO_PRODUCER_SEAM);
    assert.equal(result.gate_zero_step_ref, GATE_ZERO_STEP_REF);
    // The defect the reviewer named: a successful join inside a refusal.
    assert.equal(result.join, null, "no join may ride inside the refusal");
    assert.equal(result.predecessor_evidence_read, null);
    assert.equal(Object.hasOwn(result, "joins_exactly"), false);
    assert.deepEqual(result.owed_seams, [...V5_A02_GATE_ZERO_OWED_SEAMS]);
  }
});

test("SURFACE: the producer contract is reported as REGISTERED, and registered is not staffed", () => {
  const result = emitGateZeroOutcome(cleanJoin());
  // The five the 2026-09-11 ruling settled are reported, and each one matches
  // the registration rather than a literal typed twice.
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;
  assert.equal(result.producer_role, entry.producer_role);
  assert.equal(result.oracle_ref, entry.oracle_ref);
  assert.equal(result.output_schema_ref, entry.output_schema_ref);
  assert.equal(result.evidence_scope, entry.evidence_scope);
  assert.equal(result.produced_gate_id, entry.produces_gate_ids[0]);
  assert.equal(result.producer_registration_status, "registered");
  assert.equal(result.producer_registration_decision_ref,
    "20c83902-f150-4d59-beca-915c5c871f95");
  // Whether r7 carries the entry is UNDETERMINED here — the packet's bytes are
  // not in this repository — and the seat is empty. Those are separate facts and
  // the refusal depends on the second one, not the first.
  assert.equal(result.r7_entry_witness, null);
  assert.equal(Object.hasOwn(result, "r7_entry_present"), false,
    "the privileged spelling must not come back under any value");
  assert.equal(result.r7_entry_witness_decided_by,
    "v5A02GateZeroR7Presence(<r7 design packet bytes>).witness_conjunction");
  assert.equal(result.producer_registration.oracle_seat_bound, false);
  // No run has happened, so no outcome exists to report.
  for (const field of ["outcome_digest", "observed_at"])
    assert.equal(result[field], null, `${field} must be null, not invented`);
  // Every field of the entry is now a value read from r7; a null would mean the
  // amendment left a hole, and tools/doctorcre-v5-review.cjs refuses such a row.
  for (const [field, value] of Object.entries(entry))
    assert.notEqual(value, null, `${field} must carry r7's answer, not a hole`);
  assert.deepEqual(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.unresolved_without_r7, []);
  assert.deepEqual(
    V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.resolved_from_r7.map(item => item.field).sort(),
    ["causal_phase", "consumes_gate_ids", "produces_gate_ids[0]", "target_dag"]);
  assert.ok(result.undecided_governance_questions.includes(
    "which store an accepted predecessor outcome is read from"));
  assert.ok(!result.undecided_governance_questions.some(
    question => question.includes("whether r7 itself carries the registration")),
  "r7 carries it; the question must not still be listed as open");
});

/**
 * THE R7 ENTRY IS PROVEN BY BYTES, NOT BY A FLAG AND NOT BY A PIN COMPARED TO
 * ANOTHER PIN. The registration's `r7_entry_witness` is routed through
 * `v5A02GateZeroR7Presence`, whose only argument is the packet's bytes; these
 * cases are that derivation's falsifiers. They are exercised against a packet
 * built to carry the entry but not hashing to the pin, against packets missing
 * the gate and the role, against a drifted row, and — in the case below that
 * takes the real reconstructed packets — against the amended bytes and the
 * superseded bytes themselves.
 */
function r7PacketLike(overrides = {}) {
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;
  return JSON.stringify({
    receipt_producer_step_registry: [{ ...entry }],
    consumer_gate_registry: [{
      gate_id: V5_A02_GATE_ZERO_GATE_ID,
      receipt_producer_step_refs: [GATE_ZERO_STEP_REF],
    }],
    producer_role_registry: [V5_A02_GATE_ZERO_PRODUCER_ROLE],
    ...overrides,
  });
}

test("PRODUCER: r7 presence is read from the packet, and no argument short of it answers true", () => {
  assert.throws(() => v5A02GateZeroR7Presence(true), V5BoundaryError);
  assert.throws(() => v5A02GateZeroR7Presence({ r7_entry_present: true }), V5BoundaryError);
  assert.throws(() => v5A02GateZeroR7Presence(), V5BoundaryError);
  // THE DERIVATION IS NOT A COMPARISON OF TWO PINS. Without bytes the answer is
  // null, not true: any revision that goes back to deciding this from the
  // pinned constants alone turns THIS assertion red.
  assert.equal(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.r7_entry_witness, null);
  assert.equal(producerModule.V5_A02_GATE_ZERO_R7_ENTRY_WITNESS, null);
  assert.equal(Object.hasOwn(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION, "r7_entry_present"), false);
  // A packet whose CONTENT is right but whose bytes are not the pinned packet:
  // every content finding holds and the conjunction is still false. This is the
  // case that goes red if the digest term is dropped from the conjunction.
  const lookalike = v5A02GateZeroR7Presence(r7PacketLike());
  assert.equal(lookalike.entry_matches, true);
  assert.equal(lookalike.gate_registered, true);
  assert.equal(lookalike.role_registered, true);
  assert.equal(lookalike.digest_matches, false);
  assert.equal(lookalike.witness_conjunction, false,
    "content without the pinned digest is not the packet");
  assert.equal(Object.hasOwn(lookalike, "present"), false,
    "the privileged spelling must not come back under any value");
  // Each content finding fails on its own mutation.
  const noRole = v5A02GateZeroR7Presence(r7PacketLike({ producer_role_registry: [] }));
  assert.equal(noRole.role_registered, false);
  const noGate = v5A02GateZeroR7Presence(r7PacketLike({ consumer_gate_registry: [] }));
  assert.equal(noGate.gate_registered, false);
  const drifted = JSON.parse(r7PacketLike());
  drifted.receipt_producer_step_registry[0].causal_phase = "production_outcome";
  assert.equal(v5A02GateZeroR7Presence(JSON.stringify(drifted)).entry_matches, false,
    "a row that differs by one closed-registry value is not this registration");
  // Unparseable bytes deny rather than throw.
  assert.equal(v5A02GateZeroR7Presence("not json").witness_conjunction, false);
  // NO caller-controlled byte shape reaches a true conjunction, and none of them
  // brings a privileged word back either.
  const byteShapes = [r7PacketLike(), r7PacketLike({ producer_role_registry: [] }),
    r7PacketLike({ consumer_gate_registry: [] }), JSON.stringify(drifted), "not json", "",
    JSON.stringify({}), JSON.stringify({ present: true, r7_entry_present: true }),
    JSON.stringify({ receipt_producer_step_registry: "everything" }),
    Buffer.from(r7PacketLike()), Buffer.alloc(0)];
  for (const bytes of byteShapes) {
    const finding = v5A02GateZeroR7Presence(bytes);
    assert.equal(finding.witness_conjunction, false,
      `a caller-supplied byte shape reached a true conjunction: ${String(bytes).slice(0, 60)}`);
    assert.deepEqual(privilegedFindings(finding), [],
      `the r7 finding leaked a privileged outcome for ${String(bytes).slice(0, 60)}`);
    assert.ok(Object.isFrozen(finding));
  }
  // The two pins are different packets, and the superseded one is named.
  assert.notEqual(V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256);
  for (const sha of [V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256])
    assert.match(sha, /^[0-9a-f]{64}$/);
});

/**
 * THE SAME CHECK AGAINST THE TWO REAL PACKETS, WHICH IS THE ONLY PLACE A TRUE
 * CONJUNCTION CAN COME FROM.
 *
 * r7 is not a file in this repository — it is 62 base64 chunk sections in the
 * doctrine store, 740KB reassembled, and the amended and superseded packets
 * together are 1.4MB — so it is supplied by path, exactly as
 * tools/doctorcre-v5-review.cjs is handed the design and constitution by path
 * rather than carrying them. Point CARR_R7_DESIGN_PACKET at the amended bytes
 * and CARR_R7_DESIGN_PACKET_SUPERSEDED at the pre-amendment bytes and both run.
 * They are skipped, never faked: a fixture standing in for the packet would
 * prove nothing about the pin, which is the entire point of a pin.
 *
 * Run on this branch 2026-09-12 against the packets read back out of the
 * doctrine store after the loop-589 amendment:
 *
 *   amended    sha256 ea40f61a…  digest_matches true   witness_conjunction true
 *   superseded sha256 ef34aa54…  digest_matches false  witness_conjunction false
 */
test("PRODUCER: the amended r7 packet witnesses the registration and the superseded one does not", (t) => {
  const amendedPath = process.env.CARR_R7_DESIGN_PACKET;
  const supersededPath = process.env.CARR_R7_DESIGN_PACKET_SUPERSEDED;
  if (!amendedPath && !supersededPath)
    return t.skip("set CARR_R7_DESIGN_PACKET and CARR_R7_DESIGN_PACKET_SUPERSEDED to the reconstructed r7 packets");

  if (amendedPath) {
    const finding = v5A02GateZeroR7Presence(readFileSync(amendedPath));
    assert.equal(finding.observed_sha256, V5_A02_GATE_ZERO_R7_PACKET_SHA256,
      `CARR_R7_DESIGN_PACKET is not the pinned packet: ${finding.observed_sha256}`);
    assert.equal(finding.digest_matches, true);
    assert.equal(finding.is_superseded_packet, false);
    assert.equal(finding.entry_matches, true);
    assert.equal(finding.gate_registered, true);
    assert.equal(finding.role_registered, true);
    assert.equal(finding.witness_conjunction, true);
  }

  if (supersededPath) {
    // The packet as it stood BEFORE the amendment. Every finding must fail:
    // these bytes hash to the superseded pin, and the packet has no Gate Zero
    // producer row, gate or role in it at all.
    const finding = v5A02GateZeroR7Presence(readFileSync(supersededPath));
    assert.equal(finding.observed_sha256, V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
      `CARR_R7_DESIGN_PACKET_SUPERSEDED is not the superseded packet: ${finding.observed_sha256}`);
    assert.equal(finding.digest_matches, false);
    assert.equal(finding.is_superseded_packet, true);
    assert.equal(finding.parsed, true, "the superseded packet must parse, so the false is about content");
    assert.equal(finding.entry_matches, false);
    assert.equal(finding.gate_registered, false);
    assert.equal(finding.role_registered, false);
    assert.equal(finding.witness_conjunction, false);
  }
});

test("SURFACE: a ruled producer role does not make the gate passable", () => {
  // The exact confusion this PR could have introduced: five fields stop being
  // null, so a reader might take the gate for decided-and-therefore-runnable.
  const result = emitGateZeroOutcome(cleanJoin());
  assert.equal(result.passable, false);
  assert.equal(result.producer_bound, false);
  assert.equal(result.status, "unavailable");
  assert.equal(result.decision, "refuse");
  assert.equal(result.join, null);
  assert.deepEqual(result.owed_seams, [...V5_A02_GATE_ZERO_OWED_SEAMS]);
  assert.equal(v5A02GateZeroPolicyPreimage().gate_zero_passable, false);
});

test("SURFACE: every unavailable answer names the seams it is owed and binds none", () => {
  const join = readGateZeroPredecessorJoin();
  assert.equal(join.reason_id, "predecessor_outcome_reader_unavailable");
  assert.deepEqual(join.owed_seams,
    [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, V5_A02_SCHEDULER_READER_SEAM].sort());
  assert.equal(join.predecessor_outcome_reader_bound, false);
  assert.equal(join.scheduler_reader_bound, false);

  const graph = readGateGraphAssurance();
  assert.equal(graph.reason_id, "gate_conclusion_reader_unavailable");
  assert.deepEqual(graph.owed_seams, [V5_A02_GATE_CONCLUSION_READER_SEAM]);
  assert.equal(graph.gate_conclusion_reader_bound, false);

  for (const result of [join, graph, emitGateZeroOutcome(cleanJoin())])
    for (const entry of result.seams_bound)
      assert.equal(entry.bound, false, `${entry.seam} must be unbound`);
});

test("SURFACE: a producer cannot be handed in as a second argument", () => {
  assert.throws(
    () => emitGateZeroOutcome(cleanJoin(), { emitOutcome: () => ({ passable: true }) }),
    error => error instanceof V5BoundaryError &&
      error.code === "gate_zero_producer_is_not_an_argument");
});

// ---------------------------------------------------------------------------
// The classifier entry is unreachable from production. Parsed, not grepped.
// ---------------------------------------------------------------------------

/**
 * V8's own ESM parser, through vm.SourceTextModule in a child process — a real
 * parser, and the one Node itself uses, rather than a regex over source text.
 * `dependencySpecifiers` is the module record's own import list, so a dynamic
 * concatenated specifier cannot hide from it the way it could from a grep.
 */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const dir = process.argv[1];
    const out = {};
    for (const name of readdirSync(dir).sort()) {
      if (!name.endsWith(".js")) continue;
      const source = readFileSync(join(dir, name), "utf8");
      out[name] = new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
    }
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

test("ISOLATION: src holds no test-only entry, and none of it reaches the test tree", () => {
  const directory = fileURLToPath(new URL("../src", import.meta.url));
  const strays = readdirSync(directory).filter(name => /\.(testonly|testhelper)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");

  const imports = moduleImports(directory);
  // The parser must have seen this slice at all, or the scan proves nothing.
  assert.ok(Object.hasOwn(imports, "gate-zero-assurance.v5.js"));
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one =>
      one.includes("/test/") || one.startsWith("../test") ||
      one.includes(".testonly.") || one.includes(".testhelper.")))
    .map(([name]) => name);
  assert.deepEqual(offenders, [], "a production module reached into the test directory");

  // And specifically: the public surface imports five modules, none of them this
  // slice's classifiers. The fifth is the producer registration, which is a
  // frozen constant table plus the one byte verifier; it reaches the sha256 and
  // canonical-JSON helpers that verifier needs and the boundary error it throws,
  // and nothing else.
  assert.deepEqual(imports["gate-zero-assurance.v5.js"],
    ["./artifact-trust.js", "./global-boundaries.v5.js", "./identity.js",
      "./benchmark-minimum.v5.js", "./gate-zero-producer-registration.v5.js"]);
  assert.deepEqual(imports["gate-zero-producer-registration.v5.js"],
    ["./artifact-trust.js", "./global-boundaries.v5.js", "./benchmark-minimum.v5.js"]);
});

/**
 * The same privileged words, checked as VALUES ONLY. The public sweep also flags
 * any `would_` FIELD, which is right there and wrong here — a clause answers in
 * `would_` fields by design. What a clause may never do is put one of the words
 * a consumer acts on into a value, under any field name at all.
 */
function privilegedValueFindings(value, path = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedValueFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value))
      privilegedValueFindings(entry, `${path}.${key}`, found);
    return found;
  }
  if (typeof value === "string" && CLAUSE_PRIVILEGED_VALUES.has(value)) found.push(`${path} === ${value}`);
  return found;
}

/** The words a consumer would act on, as VALUES, for the clause sweep below. */
const CLAUSE_PRIVILEGED_VALUES = new Set([
  ...PRIVILEGED_VALUES, "joins_exactly", "coverage_complete", "ok", "covered", "activated",
]);

test("VOCABULARY: no clause answer contains a privileged word as a value", () => {
  // The sibling half of this slice answered `would_derive_state_if_authoritative:
  // "operational"` — a conditional field with the privileged word sitting inside
  // it — and a reviewer was right that the value is what a consumer reads. These
  // clauses answer in booleans under `would_*` names and carry no such value; the
  // sweep is here so a later edit cannot introduce one unnoticed.
  const answers = [
    classifyGateGraph(cleanGates()),
    classifyGateZeroJoin(cleanJoin()),
    classifySchedulerCanary(cleanCanary(), cleanReadback(), SCHEDULER_OUTCOME_DIGEST),
  ];
  for (const stepRef of V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS)
    answers.push(classifyPredecessorObservation(observation(stepRef, "sweep"),
      { step_ref: stepRef, as_of_ms: Date.parse(AS_OF) }));
  for (const conclusion of V5_A02_GATE_CONCLUSIONS)
    answers.push(classifyGateGraph([
      { gate_id: "root", conclusion, depends_on: [] },
      { gate_id: "leaf", conclusion: "success", depends_on: ["root"] },
    ]));
  assert.ok(answers.length >= 8, "the clause sweep must cover the clause domain");
  for (const answer of answers)
    assert.deepEqual(privilegedValueFindings(answer), [],
      `${answer.classification ?? "clause"} answered with a privileged word`);
});

test("ISOLATION: the clause helper answers only in the conditional", () => {
  const source = readFileSync(
    fileURLToPath(new URL("./gate-zero-classifiers.v5.testhelper.mjs", import.meta.url)), "utf8");
  // No privileged result field is even spelled in the classifier's returns.
  for (const forbidden of ["decision:", "green:", "joins_exactly:", "passable:", "ok:"])
    assert.equal(source.includes(`\n    ${forbidden}`), false,
      `${forbidden} is a privileged result field`);
  for (const result of [
    classifyGateGraph(cleanGates()),
    classifyGateZeroJoin(cleanJoin()),
    classifyPredecessorObservation(observation("step:wr46-dissolution-outcome", "wr46"),
      { step_ref: "step:wr46-dissolution-outcome", as_of_ms: Date.parse(AS_OF) }),
    classifySchedulerCanary(cleanCanary(), cleanReadback(), SCHEDULER_OUTCOME_DIGEST),
  ]) {
    assert.equal(result.is_not_authority, true);
    assert.equal(result.evidence_source, V5_A02_CLASSIFIER_EVIDENCE_SOURCE);
    assert.equal(Object.hasOwn(result, "decision"), false);
  }
});

// ===========================================================================
// HALF B — THE CLASSIFIERS, CLAUSE BY CLAUSE.
// ===========================================================================

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

test("JOIN: the clean four would join exactly, and that is a shape statement", () => {
  const result = classifyGateZeroJoin(cleanJoin());
  assert.equal(result.would_join_exactly_if_authoritative, true);
  assert.equal(result.reason_id, null);
  assert.deepEqual(result.predecessors_missing, []);
  assert.deepEqual(result.predecessors_unsatisfied, []);
  assert.equal(result.is_gate_zero_pass, false, "a shape join is never a Gate Zero pass");
  assert.equal(result.is_not_authority, true);
  assert.equal(result.evidence_source, V5_A02_CLASSIFIER_EVIDENCE_SOURCE);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

test("JOIN: a missing predecessor refuses and names it", () => {
  const request = cleanJoin();
  request.predecessor_observations = request.predecessor_observations
    .filter(entry => entry.step_ref !== "step:wr54-backup-recovery-outcome");
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
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
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.predecessor_results["step:wr40-repository-outcome"].reason_id,
    "predecessor_outcome_absent");
});

test("JOIN: an accepted criteria_not_met outcome clears no milestone", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.criteria_state = "criteria_not_met";
  target.outcome_digest = digest(target.outcome);
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.reason_id, "predecessor_criteria_not_met");
});

test("JOIN: a proposal pending human acceptance does not close a predecessor", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.acceptance_state = "pending_human_acceptance";
  target.outcome_digest = digest(target.outcome);
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_outcome_not_accepted");
});

test("JOIN: an outcome that does not hash to its own digest is refused", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].outcome.outcome_ref = "outcome:swapped-after-hashing";
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_outcome_digest_mismatch");
});

test("JOIN: an outcome body naming another step cannot stand in", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.step_ref = "step:wr46-dissolution-outcome";
  target.outcome_digest = digest(target.outcome);
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_outcome_bound_to_other_step");
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
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.reason_id, "duplicate_predecessor_outcome");
  assert.equal(result.predecessor_results["step:wr46-dissolution-outcome"].reason_id,
    "duplicate_predecessor_outcome");
  assert.equal(
    result.predecessor_results["step:wr40-repository-outcome"].would_satisfy_if_authoritative, true,
    "the first citation stands; the second one is the reuse");
});

test("JOIN: an outcome observed after the reference instant is not evidence at it", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].observed_at = "2026-09-12T00:00:00Z";
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_observed_after_reference");
});

test("JOIN: the refusal still reports the exact shapes it was shown", () => {
  const request = cleanJoin();
  request.predecessor_observations[2].outcome.criteria_state = "criteria_not_met";
  request.predecessor_observations[2].outcome_digest =
    digest(request.predecessor_observations[2].outcome);
  const cited = classifyGateZeroJoin(request).predecessor_shapes_cited;
  assert.equal(cited.length, 4);
  const wr46 = cited.find(entry => entry.step_ref === "step:wr46-dissolution-outcome");
  assert.equal(wr46.criteria_state, "criteria_not_met");
  assert.equal(wr46.would_satisfy_if_authoritative, false);
  assert.equal(wr46.reason_id, "predecessor_criteria_not_met");
  assert.equal(wr46.outcome_ref, "outcome:wr46");
});

test("JOIN: a step outside the four is unreadable, not refusable", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].step_ref = "step:some-other-outcome";
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_predecessor_step");
});

test("JOIN: the same predecessor twice is unreadable", () => {
  const request = cleanJoin();
  request.predecessor_observations.push(observation("step:wr40-repository-outcome", "again"));
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "duplicate_predecessor_observation");
});

test("JOIN: an unknown field on an observation is unreadable", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].verified = true;
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("JOIN: a pending observation may not smuggle an outcome alongside it", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].state = "pending";
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unobserved_states_an_outcome");
});

// ---------------------------------------------------------------------------
// SCHEDULER CANARY AND READBACK.
// ---------------------------------------------------------------------------

test("SCHEDULER: the clean canary and readback would join exactly", () => {
  const result = classifySchedulerCanary(cleanCanary(), cleanReadback(), SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.would_satisfy_if_authoritative, true);
});

test("SCHEDULER: a readback of another canary does not join", () => {
  const readback = cleanReadback();
  readback.canary_id = "canary-from-last-week";
  assert.equal(classifySchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_readback_canary_mismatch");
});

test("SCHEDULER: a readback carrying a different digest does not join", () => {
  const readback = cleanReadback();
  readback.observed_digest = `sha256:${"b".repeat(64)}`;
  assert.equal(classifySchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_canary_digest_mismatch");
});

test("SCHEDULER: a readback at its own dispatch instant describes nothing running", () => {
  const canary = cleanCanary();
  const readback = cleanReadback();
  readback.observed_at = canary.dispatched_at;
  assert.equal(classifySchedulerCanary(canary, readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_readback_not_after_dispatch");
});

test("SCHEDULER: an absent readback is a canary nobody heard", () => {
  const readback = {
    canary_id: "canary-2026-09-11-0001", state: "absent",
    observed_at: null, observed_digest: null,
  };
  assert.equal(classifySchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_readback_absent");
});

test("SCHEDULER: a canary not bound to the accepted scheduler outcome does not join", () => {
  const canary = cleanCanary();
  canary.scheduler_outcome_digest = `sha256:${"c".repeat(64)}`;
  assert.equal(classifySchedulerCanary(canary, cleanReadback(), SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_canary_not_bound_to_receipt");
});

test("SCHEDULER: with no accepted scheduler outcome the canary is unbindable", () => {
  assert.equal(classifySchedulerCanary(cleanCanary(), cleanReadback(), null).reason_id,
    "scheduler_canary_not_bound_to_receipt");
});

test("JOIN: a failed scheduler predecessor makes its canary unbindable inside the join", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations.find(
    entry => entry.step_ref === V5_A02_SCHEDULER_STEP_REF);
  target.state = "absent";
  target.outcome = null;
  target.outcome_digest = null;
  target.observed_at = null;
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.scheduler_canary_result.reason_id, "scheduler_canary_not_bound_to_receipt");
});

// ---------------------------------------------------------------------------
// NON-GREEN PROPAGATION. "A failed injected gate cannot claim green."
// ---------------------------------------------------------------------------

test("GRAPH: the clean graph would be green", () => {
  const result = classifyGateGraph(cleanGates());
  assert.equal(result.would_be_green_if_authoritative, true);
  assert.deepEqual(result.non_green_gates, []);
  assert.deepEqual(result.inherited_non_green_gates, []);
});

test("GRAPH: an injected failed gate makes the graph non-green and is named", () => {
  const gates = cleanGates();
  gates[2].conclusion = "failure";
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
  assert.equal(result.reason_id, "gate_graph_not_green");
  assert.deepEqual(result.non_green_gates, ["scheduler-truth"]);
});

test("GRAPH: a gate reporting success over a failed ancestor cannot claim green", () => {
  const gates = cleanGates();
  gates[1].conclusion = "failure";               // repo-hygiene, the root
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
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
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
  assert.deepEqual(result.inherited_non_green_gates, ["leaf", "middle"]);
});

test("GRAPH: every non-success conclusion is non-green", () => {
  for (const conclusion of ["cancelled", "failure", "pending", "unknown"]) {
    const gates = cleanGates();
    gates[1].conclusion = conclusion;
    assert.equal(classifyGateGraph(gates).would_be_green_if_authoritative, false,
      `${conclusion} must not be green`);
  }
  const gates = cleanGates();
  gates[1].conclusion = "success";
  assert.equal(classifyGateGraph(gates).would_be_green_if_authoritative, true);
});

test("GRAPH: a gate whose ancestor is not in the graph is not green", () => {
  const gates = cleanGates();
  gates[0].depends_on = ["never-read"];
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
  assert.equal(result.reason_id, "gate_dependency_unknown");
  assert.deepEqual(result.unknown_dependencies, ["assurance-fabric-preactivation->never-read"]);
});

test("GRAPH: an empty graph is not green", () => {
  assert.equal(classifyGateGraph([]).would_be_green_if_authoritative, false);
});

test("GRAPH: a self-asserted green field is unreadable", () => {
  const gates = cleanGates();
  gates[1].conclusion = "failure";
  gates[1].claimed_green = true;
  assert.throws(() => classifyGateGraph(gates),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("GRAPH: a cyclic dependency graph is unreadable", () => {
  const gates = [
    { gate_id: "a", conclusion: "success", depends_on: ["b"] },
    { gate_id: "b", conclusion: "success", depends_on: ["a"] },
  ];
  assert.throws(() => classifyGateGraph(gates),
    error => error instanceof V5BoundaryError && error.code === "cyclic_gate_graph");
});

test("GRAPH: an unsorted or repeating dependency list is unreadable", () => {
  const unsorted = cleanGates();
  unsorted[0].depends_on = ["scheduler-truth", "repo-hygiene"];
  assert.throws(() => classifyGateGraph(unsorted),
    error => error instanceof V5BoundaryError && error.code === "unsorted_list");
  const repeating = cleanGates();
  repeating[0].depends_on = ["repo-hygiene", "repo-hygiene"];
  assert.throws(() => classifyGateGraph(repeating),
    error => error instanceof V5BoundaryError && error.code === "duplicate_member");
});

test("JOIN: a non-green gate graph blocks the join even with four clean predecessors", () => {
  const request = cleanJoin();
  request.gates[1].conclusion = "failure";
  const result = classifyGateZeroJoin(request);
  assert.deepEqual(result.predecessors_unsatisfied, []);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.reason_id, "gate_graph_not_green");
});

// ---------------------------------------------------------------------------
// ONE CLAUSE, STANDING ALONE.
// ---------------------------------------------------------------------------

test("CLAUSE: one observation can be judged without the rest of the join", () => {
  const result = classifyPredecessorObservation(
    observation("step:wr46-dissolution-outcome", "wr46"),
    { step_ref: "step:wr46-dissolution-outcome", as_of_ms: Date.parse(AS_OF) });
  assert.equal(result.would_satisfy_if_authoritative, true);
  assert.equal(result.detail.step_ref, "step:wr46-dissolution-outcome");
});

test("CLAUSE: an observation bound to a different step is refused by name", () => {
  const result = classifyPredecessorObservation(
    observation("step:wr46-dissolution-outcome", "wr46"),
    { step_ref: "step:wr40-repository-outcome", as_of_ms: Date.parse(AS_OF) });
  assert.equal(result.reason_id, "predecessor_outcome_bound_to_other_step");
});

// ---------------------------------------------------------------------------
// POLICY IDENTITY.
// ---------------------------------------------------------------------------

test("POLICY: the preimage is stable, caller-independent, and says the surface refuses", () => {
  const preimage = v5A02GateZeroPolicyPreimage();
  assert.deepEqual(preimage.decision_ids, ["Q017.D1", "Q036.D1", "Q067.D1", "Q086.D1"]);
  assert.deepEqual(preimage.decision_ids, [...V5_A02_DECISION_IDS]);
  assert.equal(preimage.schema_version, V5_A02_GATE_ZERO_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_A02_POLICY_VERSION);
  assert.equal(preimage.gate_zero_passable, false);
  assert.equal(preimage.producer_bound, false);
  assert.equal(preimage.authoritative_readers_bound, false);
  assert.equal(preimage.public_surface_answers, "unavailable");
  assert.deepEqual(preimage.owed_seams, [...V5_A02_GATE_ZERO_OWED_SEAMS]);
  // It takes no caller input, and proves it by ignoring some.
  assert.equal(digest(v5A02GateZeroPolicyPreimage({ passable: true })),
    digest(v5A02GateZeroPolicyPreimage()));
});

test("POLICY: the digest is deterministic and matches its canonical bytes", () => {
  assert.equal(v5A02GateZeroPolicyDigest(), v5A02GateZeroPolicyDigest());
  assert.equal(v5A02GateZeroPolicyDigest(), digest(v5A02GateZeroPolicyPreimage()));
  assert.equal(v5A02GateZeroPolicyCanonicalBytes(),
    JSON.stringify(JSON.parse(v5A02GateZeroPolicyCanonicalBytes())));
});

test("POLICY: every reason either half can answer with is registered", () => {
  // Three spellings, because a reason reachable by ANY route must be in the
  // closed registry: the clauses answer through `wouldNot("id", ...)` and
  // `reason("id")`, and the public surface passes the id as the second argument
  // of its one `unavailable(...)` shape.
  const citations = path => {
    const source = readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
    return [
      ...[...source.matchAll(/reason\("([a-z_]+)"\)/g)].map(match => match[1]),
      ...[...source.matchAll(/wouldNot\("([a-z_]+)"/g)].map(match => match[1]),
      ...[...source.matchAll(/unavailable\(\s*"[a-z_]+",\s*"([a-z_]+)"/g)].map(match => match[1]),
    ];
  };
  const publicCitations = citations("../src/gate-zero-assurance.v5.js");
  assert.ok(publicCitations.length >= 3, "the public surface must cite its own refusals");
  const classifierCitations = citations("./gate-zero-classifiers.v5.testhelper.mjs");
  assert.ok(classifierCitations.length >= 10, "the clauses must cite their own refusals");
  for (const id of [...publicCitations, ...classifierCitations])
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(id), `${id} is not registered`);
  // And the behavioural half: whatever the surface actually answers is registered.
  for (const result of [emitGateZeroOutcome({}), readGateZeroPredecessorJoin(), readGateGraphAssurance()])
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(result.reason_id), result.reason_id);
});

test("POLICY: every result on both halves carries the no-effects marker", () => {
  for (const result of [
    emitGateZeroOutcome(cleanJoin()), readGateZeroPredecessorJoin(), readGateGraphAssurance(),
    classifyGateZeroJoin(cleanJoin()), classifyGateGraph(cleanGates()),
  ]) {
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(result));
  }
});
