// Unit tests for the DoctorCRE v5 benchmark acceptance store.
//
// THE DATABASE HERE IS A MOCK, AND NOTHING IN THIS FILE IS EVIDENCE THAT
// ANYTHING WAS STORED OR ACCEPTED. `mockDatabase` records the statements the
// module issues and returns the rows the test hands it. That is enough to prove
// what the module SENDS, what it REFUSES, and what it refuses to send — and it
// is not, and is not treated as, a claim about durable behaviour. The
// transaction-scoped proofs against a real PostgreSQL live in
// benchmark-acceptance-postgres.sql, and NO benchmark has been accepted by
// running either file: acceptance fails closed on TWO independent unbound
// bindings, which the last group below asserts directly.
//
// The strongest test in this file is the smallest: the acceptance verb issues
// ZERO statements. A refusal that happens after a query is a refusal that can
// be mistaken for an acceptance that nearly worked.
//
// ONE THING THIS FILE CANNOT REACH, named rather than left implicit. The
// measurement coverage proof binding is refused AFTER the Gate Zero binding, and
// neither private reader is exported or injectable — deliberately, since an
// injectable stub is a configurable one. So the second refusal cannot be
// observed firing here; what is asserted instead is that it is a separate,
// separately-resolved binding that the prerequisites report and the verb
// description names. The candidate SQL's own reader IS reachable, and
// benchmark-acceptance-postgres.sql calls it directly and asserts it raises.

import test from "node:test";
import assert from "node:assert/strict";
import { digest } from "../src/artifact-trust.js";
import {
  BENCHMARK_COST_VARIANCE_THRESHOLDS, BENCHMARK_DEADLINE_CONTRACT, BENCHMARK_GATE_ID,
  BENCHMARK_MEASUREMENT_SET_SCHEMA, BENCHMARK_PAYLOAD_DOMAIN_TAG, BENCHMARK_PAYLOAD_FIELDS,
  BENCHMARK_PRODUCER_ROLE, BENCHMARK_SLO_THRESHOLDS, BENCHMARK_STEP_REF, GATE_ZERO_STEP_REF,
  P95_AGGREGATION_METHOD, benchmarkPayloadDigest, benchmarkRequiredCells,
} from "../src/benchmark-minimum.v5.js";
import * as store from "../src/benchmark-acceptance-store.v5.js";
import {
  BENCHMARK_DIMENSIONS, BENCHMARK_DRAFT_ROWS_SCHEMA, BENCHMARK_DRAFT_SCALAR_FIELDS,
  BENCHMARK_EMITTED_CONSTANT_FIELDS, BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT,
  BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT,
  BenchmarkAcceptanceStoreError, benchmarkAcceptancePrerequisites, benchmarkAcceptanceStoreTools,
  benchmarkDraftRows, benchmarkPayloadFromRows, deriveBenchmarkAcceptor,
  validateBenchmarkDraftPayload,
} from "../src/benchmark-acceptance-store.v5.js";

// --- fixtures ---------------------------------------------------------------

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const copy = x => JSON.parse(JSON.stringify(x));

const DRAFT_ID = "11111111-2222-4333-8444-555555555555";
const REVIEW_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa";
const KEY = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";

/**
 * One minimal admissible payload. Every environment dimension carries exactly
 * one member except workload_mix, which carries two so that the weight split is
 * exercised rather than trivial. The required matrix is therefore eight cells --
 * (routes x warm) + (routes x cold) + (acknowledgement_endpoints x both cache
 * states), each crossed with the two workloads -- and a complete measurement set
 * fits in a test.
 */
function payload() {
  return {
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    capacity_profiles: ["baseline"],
    workload_mix: [
      { workload_id: "search", weight_basis_points: 7000, operation_mix_digest: D(10) },
      { workload_id: "detail", weight_basis_points: 3000, operation_mix_digest: D(11) },
    ],
    request_size_distribution: [{ percentile: 50, bytes: 2048 }, { percentile: 95, bytes: 16384 }],
    concurrency_levels: [8],
    arrival_patterns: ["steady"],
    routes: ["/deals"],
    browsers: [{ name: "chrome", version: "141", build: "141.0.1" }],
    runtime_versions: ["node-24"],
    device_profiles: ["macbook-pro-m3"],
    hardware_profiles: ["m3-16gb"],
    network_profiles: ["broadband"],
    cache_states: ["cold", "warm"],
    samples_per_cell: 20,
    warmup_runs: 1,
    p95_aggregation_method: P95_AGGREGATION_METHOD,
    outlier_rule: "discard samples above five times the cell median",
    acknowledgement_endpoints: ["/commands/ack"],
    evaluator_identities: [
      { actor_id: "bench-evaluator", session_ref: "session:a00-store-evaluator", authority_class: "synthetic_oracle" },
    ],
    comparator_versions: ["comparator-1.4.0"],
    slo_thresholds: { ...BENCHMARK_SLO_THRESHOLDS },
    cost_expectation_matrix_digest: D(12),
    cost_variance_thresholds: { ...BENCHMARK_COST_VARIANCE_THRESHOLDS },
    deadline_contract: copy(BENCHMARK_DEADLINE_CONTRACT),
  };
}

/** A measurement set covering exactly the required matrix, well inside every SLO. */
function measurements(body = payload()) {
  return {
    schema_version: BENCHMARK_MEASUREMENT_SET_SCHEMA,
    benchmark_payload_digest: benchmarkPayloadDigest(body),
    outlier_rule: body.outlier_rule,
    p95_aggregation_method: body.p95_aggregation_method,
    cells: benchmarkRequiredCells(body).map(cell => ({
      cell: copy(cell),
      warmup_samples: [40],
      samples: Array.from({ length: 20 }, () => 10),
      excluded_sample_indexes: [],
    })),
  };
}

// --- the mock database and tool harness -------------------------------------

/**
 * A statement recorder. Responses are matched on a substring of the SQL, so a
 * test declares which function it expects to be called; a statement with no
 * declared response throws, which is how an unexpected query fails a test
 * rather than passing silently.
 */
function mockDatabase(responses = {}) {
  const calls = [];
  const events = [];
  return {
    calls, events,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      for (const [needle, rows] of Object.entries(responses)) {
        if (sql.includes(needle)) return { rows };
      }
      throw new Error(`mock database has no declared response for: ${sql}`);
    },
  };
}

class ToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.name = "ToolError";
    this.payload = payload;
  }
}

// The real withEnvelope opens a transaction and records a tool-call envelope.
// Here it only invokes the body: these tests are about what the handler does,
// not about the envelope, and a mock that pretended to be transactional would
// be claiming durability this file explicitly does not claim.
const withEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
const writeEvent = async (c, event) => { c.events.push(event); };

const tools = benchmarkAcceptanceStoreTools({ withEnvelope, writeEvent, ToolError });

const PARTNER = { slug: "joe", human: true, via: "oauth-google" };
const AGENT = { slug: "codex", human: false, sponsoring_human_slug: "joe" };

async function refusal(promise) {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ToolError) return error.payload;
    throw error;
  }
  throw new assert.AssertionError({ message: "expected a tool refusal, none was thrown" });
}

/**
 * The synchronous twin of `refusal`: capture the error one refusing call threw.
 *
 * assert.throws returns undefined, so `assert.throws(fn, Type).code` reads a
 * property off undefined rather than off the error. The expected constructor is
 * still asserted here when one is named -- nothing about what these tests demand
 * is relaxed, the error is simply handed back so its code and detail can be
 * asserted on the object that actually carries them.
 */
function thrown(fn, expected) {
  try {
    fn();
  } catch (error) {
    if (expected !== undefined && !(error instanceof expected)) {
      throw new assert.AssertionError({
        message: `expected a ${expected.name}, got ${error?.name}: ${error?.message}`,
        actual: error?.name, expected: expected.name,
      });
    }
    return error;
  }
  throw new assert.AssertionError({ message: "expected a refusal, none was thrown" });
}

// --- the draft contract -----------------------------------------------------

test("a valid draft reports the kernel's payload digest and accepts nothing", () => {
  const view = validateBenchmarkDraftPayload(payload());
  assert.equal(view.payload_digest, benchmarkPayloadDigest(payload()));
  assert.equal(view.payload_digest, digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload()]));
  assert.equal(view.gate_id, BENCHMARK_GATE_ID);
  assert.equal(view.producer_step_ref, BENCHMARK_STEP_REF);
  assert.equal(view.producer_role, BENCHMARK_PRODUCER_ROLE);
  assert.equal(view.accepted, false);
  assert.equal(view.effects.creates_effect, false);
});

test("each acceptance-envelope field is refused on a draft by name", () => {
  const cases = {
    accepted_at: "2026-02-02T00:00:00.000Z",
    accepted_by_identity: { actor_id: "joe", session_ref: "session:a00-store-x", authority_class: "verified_partner" },
    benchmark_manifest_digest: D(9),
    status: "accepted",
  };
  for (const [field, value] of Object.entries(cases)) {
    const error = thrown(() => validateBenchmarkDraftPayload({ ...payload(), [field]: value }),
      BenchmarkAcceptanceStoreError);
    assert.equal(error.code, "benchmark_acceptance_envelope_refused");
    assert.equal(error.detail.path, `payload.${field}`);
  }
});

test("a self-asserted authority key anywhere in a draft is refused", () => {
  for (const key of ["verified_human", "partner_confirmed", "authority_granted", "gate_zero_digest"]) {
    const body = payload();
    body.workload_mix[0][key] = true;
    const error = thrown(() => validateBenchmarkDraftPayload(body), BenchmarkAcceptanceStoreError);
    assert.equal(error.code, "self_asserted_authority_refused");
  }
});

test("a payload the kernel refuses is refused with the kernel's own code", () => {
  const body = payload();
  body.workload_mix[0].weight_basis_points = 6999;
  // No constructor is named: the kernel's own error class is deliberately not
  // imported here, because the point of the case is that its CODE survives
  // un-rewrapped.
  const error = thrown(() => validateBenchmarkDraftPayload(body));
  assert.equal(error.name, "BenchmarkMinimumError");
  assert.equal(error.code, "benchmark_weight_total_mismatch");
});

// --- the row decomposition --------------------------------------------------

test("every payload field is either stored as a row or emitted as a constant", () => {
  const persisted = new Set([
    ...BENCHMARK_DRAFT_SCALAR_FIELDS, ...BENCHMARK_DIMENSIONS,
    "workload_mix", "request_size_distribution", "concurrency_levels",
    "browsers", "evaluator_identities",
  ]);
  const accounted = BENCHMARK_PAYLOAD_FIELDS.filter(field =>
    persisted.has(field) || BENCHMARK_EMITTED_CONSTANT_FIELDS.includes(field));
  assert.deepEqual([...accounted].sort(), [...BENCHMARK_PAYLOAD_FIELDS].sort());
  // And the two sets are disjoint: a field cannot be both stored and emitted.
  for (const field of BENCHMARK_EMITTED_CONSTANT_FIELDS) assert.equal(persisted.has(field), false);
});

test("the fixed constant groups are not stored anywhere in the rows", () => {
  const rows = benchmarkDraftRows(payload());
  const serialized = JSON.stringify(rows);
  for (const field of BENCHMARK_EMITTED_CONSTANT_FIELDS) {
    assert.equal(Object.hasOwn(rows.scalars, field), false);
    assert.equal(serialized.includes(field), false, `${field} leaked into the stored rows`);
  }
});

test("rows round-trip to the same payload digest", () => {
  const body = payload();
  const rebuilt = benchmarkPayloadFromRows(benchmarkDraftRows(body));
  assert.deepEqual(rebuilt, body);
  assert.equal(benchmarkPayloadDigest(rebuilt), benchmarkPayloadDigest(body));
});

test("a rebuilt payload carries the kernel's constants, not the caller's", () => {
  const rows = benchmarkDraftRows(payload());
  const rebuilt = benchmarkPayloadFromRows(rows);
  assert.deepEqual(rebuilt.slo_thresholds, { ...BENCHMARK_SLO_THRESHOLDS });
  assert.deepEqual(rebuilt.cost_variance_thresholds, { ...BENCHMARK_COST_VARIANCE_THRESHOLDS });
  assert.deepEqual(rebuilt.deadline_contract, copy(BENCHMARK_DEADLINE_CONTRACT));
});

test("list order is part of the hash: reordering rows changes the digest", () => {
  const body = payload();
  const rows = copy(benchmarkDraftRows(body));
  // Two request-size points, ordinals swapped. The set is identical; the
  // distribution's order is not, and r7 array order participates in the digest.
  rows.request_sizes = [
    { ...rows.request_sizes[1], ordinal: 0 },
    { ...rows.request_sizes[0], ordinal: 1 },
  ];
  const reordered = benchmarkPayloadFromRows(rows);
  assert.notEqual(benchmarkPayloadDigest(reordered), benchmarkPayloadDigest(body));
});

test("a gap in the stored ordinals refuses instead of rebuilding a shorter list", () => {
  const rows = copy(benchmarkDraftRows(payload()));
  rows.dimensions = rows.dimensions.filter(row => !(row.dimension === "cache_states" && row.ordinal === 0));
  const error = thrown(() => benchmarkPayloadFromRows(rows), BenchmarkAcceptanceStoreError);
  assert.equal(error.code, "benchmark_row_ordinal_gap");
});

test("rows carry every dimension in declared order with contiguous ordinals", () => {
  const rows = benchmarkDraftRows(payload());
  assert.equal(rows.schema_version, BENCHMARK_DRAFT_ROWS_SCHEMA);
  for (const dimension of BENCHMARK_DIMENSIONS) {
    const values = rows.dimensions.filter(row => row.dimension === dimension);
    assert.ok(values.length >= 1, `${dimension} has no rows`);
    values.forEach((row, index) => assert.equal(row.ordinal, index));
    assert.deepEqual(values.map(row => row.value), payload()[dimension]);
  }
});

// --- the acceptance prerequisites -------------------------------------------

test("both private readers are private and not exported", () => {
  assert.equal("readGateZeroOutcome" in store, false);
  assert.equal("readMeasurementCoverageBinding" in store, false);
  for (const name of Object.keys(store)) {
    assert.ok(!/gateZero|gate_zero/i.test(name) || name === "BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT",
      `${name} exposes a Gate Zero surface`);
    // The coverage binding is a stated gap, not a callable surface either.
    assert.ok(!/^read[A-Z]/.test(name), `${name} exposes a reader`);
  }
});

test("the prerequisites report two unbound acceptance bindings and a bound portfolio", () => {
  const prerequisites = benchmarkAcceptancePrerequisites();
  assert.equal(prerequisites.acceptance_available, false);
  // TWO ENTRIES. Landing the Gate Zero record clears the first and leaves the
  // second, which is the whole point of keeping them separate.
  assert.deepEqual(prerequisites.acceptance_blocked_by, [
    GATE_ZERO_STEP_REF, "binding:benchmark-measurement-coverage-proof",
  ]);
  assert.equal(prerequisites.gate_zero.resolved, false);
  assert.equal(prerequisites.gate_zero.step_ref, GATE_ZERO_STEP_REF);
  assert.ok(prerequisites.gate_zero.required_to_resolve.length > 0);
  assert.ok(prerequisites.gate_zero.explicitly_refused.length > 0);
  assert.equal(prerequisites.measurement_coverage_proof.resolved, false);
  assert.equal(prerequisites.measurement_coverage_proof.independent_of, GATE_ZERO_STEP_REF);
  assert.equal(prerequisites.portfolio_constitution.resolved, true);
  assert.equal(prerequisites.portfolio_constitution.resolved_by, "ops.portfolio_accepted_revision(text)");
  // It is a statement of a MISSING BINDING, not a Gate Zero outcome.
  assert.equal(Object.hasOwn(prerequisites.gate_zero, "outcome_digest"), false);
  assert.equal(Object.hasOwn(prerequisites.gate_zero, "observed_at"), false);
});

test("the portfolio prerequisite reports what it proves and refuses to claim lineage", () => {
  const portfolio = benchmarkAcceptancePrerequisites().portfolio_constitution;
  assert.ok(portfolio.proves.length > 0);
  assert.ok(portfolio.does_not_prove.length > 0);
  // The claim that used to be made in prose -- "the portfolio this benchmark
  // descends from" -- is now stated as the thing NOT proved, and it is stated on
  // the negative side only.
  assert.ok(portfolio.does_not_prove.some(clause => /descend/i.test(clause)));
  assert.equal(portfolio.proves.some(clause => /descend/i.test(clause)), false);
  assert.ok(/lineage/i.test(portfolio.lineage_note));
});

test("the Gate Zero requirement is scoped to this record layer, not to Gate Zero itself", () => {
  const requirement = BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT;
  // The external pre-v5 producer is intentional and nothing here asks for a
  // registry entry, so the requirement says so in a field rather than treating
  // the absent producer as the defect.
  assert.equal(requirement.external_producer_is_intentional, true);
  assert.ok(/record layer/i.test(requirement.scope));
  // No clause may assert that Gate Zero produced no outcome anywhere. What this
  // module can observe is what is bindable here.
  for (const clause of requirement.why_unresolved) {
    assert.equal(/no canonical gate zero outcome record exists/i.test(clause), false,
      `why_unresolved asserts a global absence: ${clause}`);
  }
  assert.ok(requirement.explicitly_refused.includes("a claim that Gate Zero produced no outcome"));
  assert.ok(requirement.why_unresolved.some(clause => /this record layer/i.test(clause)));
});

test("the coverage requirement names a missing record, not a second coverage authority", () => {
  const requirement = BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT;
  assert.equal(requirement.resolved, false);
  assert.equal(requirement.binding_ref, "binding:benchmark-measurement-coverage-proof");
  assert.equal(requirement.independent_of, GATE_ZERO_STEP_REF);
  assert.ok(requirement.explicitly_refused.includes(
    "treating measurement_set_digest as evidence that coverage was proved"));
  assert.ok(requirement.explicitly_refused.includes(
    "a second coverage evaluator written outside benchmark-minimum.v5.js"));
  assert.ok(requirement.explicitly_refused.includes(
    "silently upgrading the assertion when the Gate Zero binding lands"));
  // And the boundary the attestation does NOT move is stated rather than left
  // for a reader to discover after building it.
  assert.ok(/does not make the record layer an independent verifier/i
    .test(requirement.remaining_trust_boundary));
  assert.ok(requirement.why_unresolved.some(clause => /trusted/i.test(clause)));
});

test("both integration requirements are frozen and name what they refuse to invent", () => {
  for (const requirement of [BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT,
    BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT]) {
    assert.ok(Object.isFrozen(requirement));
    assert.ok(Object.isFrozen(requirement.explicitly_refused));
    assert.throws(() => { requirement.resolved = true; }, TypeError);
  }
});

// --- the derived acceptor ---------------------------------------------------

test("a verified partner's authority class is derived from the live actor", () => {
  const acceptor = deriveBenchmarkAcceptor(PARTNER);
  assert.equal(acceptor.actor_id, "joe");
  assert.equal(acceptor.authority_class, "verified_partner");
  assert.equal(acceptor.authority_class_source, "identity.authorizationClassForActor");
  assert.equal(acceptor.producer_role, BENCHMARK_PRODUCER_ROLE);
});

test("no actor short of a verified human partner may be the acceptor", () => {
  const cases = [
    [AGENT, "sponsored_agent"],
    [{ slug: "codex", human: true }, "unsponsored_agent"],
    [{ slug: "joe", human: false, sponsoring_human_slug: "joe" }, "sponsored_agent"],
    [{ slug: "hermes-pilot", human: false, hermes: true }, "unsponsored_agent"],
    // A self-asserted class on the actor object. It is not consulted, so it
    // does not rescue an actor whose live shape is a sponsored agent.
    [{ slug: "codex", human: false, sponsoring_human_slug: "joe", authority_class: "verified_partner" },
      "sponsored_agent"],
  ];
  for (const [actor, expected] of cases) {
    const error = thrown(() => deriveBenchmarkAcceptor(actor), BenchmarkAcceptanceStoreError);
    assert.equal(error.code, "verified_partner_required");
    assert.equal(error.detail.derived_authority_class, expected);
  }
  assert.throws(() => deriveBenchmarkAcceptor(null), BenchmarkAcceptanceStoreError);
});

test("a self-asserted authority_class on a real partner changes nothing", () => {
  // The class is recomputed from the live actor either way. A claim that agrees
  // with the derivation is not what admits this actor, and one that disagreed
  // would not have admitted the actor above.
  const claimed = deriveBenchmarkAcceptor({ ...PARTNER, authority_class: "unsponsored_agent" });
  assert.equal(claimed.authority_class, "verified_partner");
  assert.deepEqual(claimed, deriveBenchmarkAcceptor(PARTNER));
});

// --- the verb surface -------------------------------------------------------

test("the verbs carry the expected write, humanOnly and authorityOnly metadata", () => {
  assert.deepEqual(Object.keys(tools).sort(), [
    "accept-benchmark-manifest-draft", "propose-benchmark-manifest-draft",
    "read-benchmark-manifest", "review-benchmark-manifest-draft",
  ]);
  assert.equal(tools["read-benchmark-manifest"].write, false);
  assert.equal(tools["propose-benchmark-manifest-draft"].write, true);
  assert.equal(tools["review-benchmark-manifest-draft"].write, true);
  const accept = tools["accept-benchmark-manifest-draft"];
  assert.equal(accept.write, true);
  assert.equal(accept.humanOnly, true);
  assert.equal(accept.authorityOnly, true);
  // Only acceptance is human/authority gated; a writer connection must be able
  // to propose and review, and must not be able to accept.
  for (const name of ["read-benchmark-manifest", "propose-benchmark-manifest-draft",
    "review-benchmark-manifest-draft"]) {
    assert.equal(tools[name].humanOnly, undefined);
    assert.equal(tools[name].authorityOnly, undefined);
  }
});

test("every verb takes a closed input schema and no identity or authority field", () => {
  const forbidden = /^(actor|actor_id|partner|tenant|accepted_by|accepted_by_identity|accepted_at|acceptor|verified|verified_human|partner_confirmed|human_approved|authority_class|authority_granted|gate_zero.*|status)$/;
  for (const [name, tool] of Object.entries(tools)) {
    assert.equal(tool.inputSchema.additionalProperties, false, `${name} has an open input schema`);
    for (const key of Object.keys(tool.inputSchema.properties)) {
      assert.ok(!forbidden.test(key), `${name} accepts "${key}" from a caller`);
    }
  }
});

// --- propose ----------------------------------------------------------------

test("propose sends the decomposed rows and the digest it computed itself", async () => {
  const c = mockDatabase({ benchmark_propose_manifest_draft: [{ id: DRAFT_ID }] });
  const body = payload();
  const expected = benchmarkPayloadDigest(body);
  const result = await tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: expected, payload: body,
  });

  assert.equal(c.calls.length, 1);
  const [, , , suppliedDigest, scalars, dimensions, workloads, sizes, concurrency, browsers, evaluators] =
    c.calls[0].params;
  assert.equal(suppliedDigest, expected);
  assert.deepEqual(JSON.parse(scalars).samples_per_cell, 20);
  assert.equal(JSON.parse(dimensions).length,
    BENCHMARK_DIMENSIONS.reduce((total, key) => total + body[key].length, 0));
  assert.equal(JSON.parse(workloads).length, 2);
  assert.equal(JSON.parse(sizes).length, 2);
  assert.equal(JSON.parse(concurrency).length, 1);
  assert.equal(JSON.parse(browsers).length, 1);
  assert.equal(JSON.parse(evaluators).length, 1);

  assert.equal(result.draft_id, DRAFT_ID);
  assert.equal(result.payload_digest, expected);
  assert.equal(result.accepted, false);
  assert.equal(result.admissibility.accepted, false);
  assert.equal(result.admissibility.proposed_benchmark_manifest_digest, expected);
  assert.equal(result.acceptance_prerequisites.acceptance_available, false);
  assert.equal(result.effects.creates_effect, false);
  assert.equal(result.effects.clock_started, false);
  assert.equal(c.events.length, 1);
  assert.equal(c.events[0].verb, "propose-benchmark-manifest-draft");
});

test("propose refuses a supplied digest that is not the one the payload produces", async () => {
  const c = mockDatabase({ benchmark_propose_manifest_draft: [{ id: DRAFT_ID }] });
  const refused = await refusal(tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: D(99), payload: payload(),
  }));
  assert.equal(refused.error, "benchmark_payload_digest_mismatch");
  assert.equal(refused.expected, benchmarkPayloadDigest(payload()));
  assert.equal(c.calls.length, 0, "a mismatched digest reached the database");
});

test("propose refuses an acceptance-envelope field before any statement", async () => {
  const c = mockDatabase({ benchmark_propose_manifest_draft: [{ id: DRAFT_ID }] });
  const body = { ...payload(), status: "accepted" };
  const refused = await refusal(tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: D(98), payload: body,
  }));
  assert.equal(refused.error, "benchmark_acceptance_envelope_refused");
  assert.equal(c.calls.length, 0);
});

// --- review -----------------------------------------------------------------

function liveDraftResponse(body = payload()) {
  return {
    benchmark_payload_preimage: [{
      payload: body,
      payload_digest: benchmarkPayloadDigest(body),
      structure_error: null,
    }],
  };
}

test("a passing review proves coverage against the stored payload and binds its digest", async () => {
  const body = payload();
  const c = mockDatabase({
    ...liveDraftResponse(body),
    benchmark_review_manifest_draft: [{ id: REVIEW_ID }],
  });
  const evidence = measurements(body);
  const result = await tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(body),
    verdict: "pass", review_summary: "matrix complete, every cell inside its fixed SLO",
    measurements: evidence,
  });

  assert.equal(c.calls.length, 2);
  const params = c.calls[1].params;
  // The digest written is the one read back from the rows, not the one supplied.
  assert.equal(params[2], benchmarkPayloadDigest(body));
  assert.equal(params[3], "pass");
  assert.equal(params[4], digest(evidence));
  assert.equal(result.review_id, REVIEW_ID);
  assert.equal(result.measurement_set_digest, digest(evidence));
  assert.equal(result.coverage_proved_against_stored_payload, true);
  // Proved ON THIS PATH, and the result says in its own fields that the proof
  // was not RECORDED anywhere. The row carries the name of the bytes; nothing
  // downstream may read it as verified coverage.
  assert.equal(result.measurement_coverage_proof_recorded, false);
  assert.equal(result.measurement_coverage_proof_binding,
    BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.binding_ref);
  assert.equal(result.accepted, false);
});

test("a review naming an unknown draft is a named refusal, not a raw database error", async () => {
  // The draft lookup is in the FROM clause, so an unknown id comes back as zero
  // rows. Called as bare scalars these functions RAISE, and the raise would have
  // surfaced as a database error in place of a refusal a caller can act on.
  const c = mockDatabase({ benchmark_payload_preimage: [] });
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "no such draft",
  }));
  assert.equal(refused.error, "benchmark_draft_unknown");
  assert.equal(refused.draft_id, DRAFT_ID);
  assert.equal(c.calls.length, 1, "the review continued past an unknown draft");
  assert.equal(c.events.length, 0);
  // And the statement it did issue selects the draft row rather than calling the
  // raising functions on a bare id.
  assert.ok(/from\s+ops\.benchmark_manifest_draft/i.test(c.calls[0].sql));
});

test("an over-long review summary is refused by name before any statement", async () => {
  // The column bounds this at 1000 too. Asserting it here is what turns a check
  // constraint violation into a refusal that names the field and the number, and
  // both sides count UTF-16 code units so the bound means one thing.
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "x".repeat(1001),
  }));
  assert.equal(refused.error, "invalid_review_summary");
  assert.equal(refused.detail.length, 1001);
  assert.equal(refused.detail.maximum, 1000);
  assert.equal(c.calls.length, 0);
});

test("a blank review summary is refused by name", async () => {
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "   ",
  }));
  assert.equal(refused.error, "invalid_review_summary");
  assert.equal(c.calls.length, 0);
});

test("an astral review summary is measured in UTF-16 code units, as SQL measures it", async () => {
  // 501 astral codepoints is 501 characters to SQL's char_length and 1002 code
  // units to String#length. The candidate SQL's review guard counts the same way
  // through ops.benchmark_utf16_length(), so the two sides refuse the same text.
  const summary = "\u{1F600}".repeat(501);
  assert.equal([...summary].length, 501);
  assert.equal(summary.length, 1002);
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: summary,
  }));
  assert.equal(refused.error, "invalid_review_summary");
  assert.equal(refused.detail.length, 1002);
  assert.equal(c.calls.length, 0);
});

test("a passing review with no measurement set is refused", async () => {
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "pass", review_summary: "looks fine",
  }));
  assert.equal(refused.error, "benchmark_measurement_set_required");
  assert.equal(c.calls.length, 1, "the review reached the database without measurements");
});

test("a passing review whose measurements miss a required cell is refused", async () => {
  const body = payload();
  const c = mockDatabase(liveDraftResponse(body));
  const evidence = measurements(body);
  evidence.cells = evidence.cells.slice(1);
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(body),
    verdict: "pass", review_summary: "partial run",
    measurements: evidence,
  }));
  assert.equal(refused.error, "benchmark_matrix_coverage_incomplete");
  assert.equal(c.calls.length, 1);
});

test("a passing review whose measurements miss an SLO is refused", async () => {
  const body = payload();
  const c = mockDatabase(liveDraftResponse(body));
  const evidence = measurements(body);
  const ack = evidence.cells.find(entry => entry.cell.metric === "command_acknowledgement_ms");
  ack.samples = Array.from({ length: 20 }, () => 5000);
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(body),
    verdict: "pass", review_summary: "slow ack",
    measurements: evidence,
  }));
  assert.equal(refused.error, "benchmark_slo_not_met");
  assert.equal(c.calls.length, 1);
});

test("a review naming a digest the draft no longer produces is refused", async () => {
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: D(97), verdict: "fail", review_summary: "stale",
  }));
  assert.equal(refused.error, "benchmark_review_digest_stale");
  assert.equal(refused.expected, benchmarkPayloadDigest(payload()));
  assert.equal(c.calls.length, 1);
});

test("a failing review needs no measurement set and binds none", async () => {
  const c = mockDatabase({
    ...liveDraftResponse(),
    benchmark_review_manifest_draft: [{ id: REVIEW_ID }],
  });
  const result = await tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "the outlier rule is unexecutable as written",
  });
  assert.equal(result.measurement_set_digest, null);
  assert.equal(result.coverage_proved_against_stored_payload, false);
  assert.equal(c.calls[1].params[4], null);
});

// --- acceptance, which fails closed -----------------------------------------

test("acceptance fails closed on the unresolved Gate Zero binding, before any statement", async () => {
  const c = mockDatabase();
  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "gate_zero_outcome_unresolved");
  assert.equal(refused.detail.step_ref, GATE_ZERO_STEP_REF);
  assert.equal(refused.detail.resolved, false);
  // THE ASSERTION THAT MATTERS. Nothing was attempted, so nothing can be read
  // as an acceptance that nearly succeeded.
  assert.equal(c.calls.length, 0, "the acceptance path reached the database");
  assert.equal(c.events.length, 0, "the acceptance path recorded an event");
});

test("the acceptance verb names both unbound bindings and claims neither is live", () => {
  const description = tools["accept-benchmark-manifest-draft"].description;
  // Two independent reasons, said in the description a caller actually reads.
  assert.ok(/TWO INDEPENDENT REASONS/.test(description));
  assert.ok(/record layer holds no authenticated Gate Zero outcome/i.test(description));
  assert.ok(/coverage/i.test(description));
  assert.ok(/both must be resolved separately/i.test(description));
  // And the portfolio binding is described as a prerequisite, not as lineage.
  assert.ok(/not a claim of lineage/i.test(description));
  // It does not claim Gate Zero produced nothing anywhere.
  assert.equal(/no canonical Gate Zero outcome record exists/i.test(description), false);
});

test("acceptance refuses a non-partner before it reaches the Gate Zero refusal", async () => {
  const c = mockDatabase();
  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, AGENT, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "verified_partner_required");
  assert.equal(refused.detail.derived_authority_class, "sponsored_agent");
  assert.equal(c.calls.length, 0);
});

test("no verb in this module can report an accepted benchmark today", async () => {
  // Propose and review both say so in their own results; acceptance cannot
  // produce a result at all. Together that is the whole surface.
  const c = mockDatabase({
    ...liveDraftResponse(),
    benchmark_propose_manifest_draft: [{ id: DRAFT_ID }],
    benchmark_review_manifest_draft: [{ id: REVIEW_ID }],
  });
  const proposed = await tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: benchmarkPayloadDigest(payload()), payload: payload(),
  });
  const reviewed = await tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "not yet",
  });
  for (const result of [proposed, reviewed]) {
    assert.equal(result.accepted, false);
    assert.equal(result.effects.creates_effect, false);
    assert.equal(result.effects.clock_started, false);
  }
  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "gate_zero_outcome_unresolved");
});
