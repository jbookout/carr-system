// The A00 -> M01 seam, exercised end to end on ONE artifact.
//
// Each module's own suite proves its side of the contract against its own
// fixtures, which is exactly how the two shapes drifted apart in the first
// place: a receipt A00 proposed could not be read by M01 at all, and the same
// receipt carried two different digests on the two sides. Everything here is
// therefore a CROSS-MODULE assertion — the artifact A00 publishes is the artifact
// M01 consumes, byte for byte and digest for digest — and nothing here re-tests
// either module in isolation.
//
// It authenticates nothing real: both gates are driven through their installed
// test-only verifiers, no clock is started, no receipt is issued, and no effect
// is claimed on either side.
import test from "node:test";
import assert from "node:assert/strict";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  BENCHMARK_COST_VARIANCE_THRESHOLDS, BENCHMARK_DEADLINE_CONTRACT, BENCHMARK_MANIFEST_SCHEMA,
  BENCHMARK_MEASUREMENT_SET_SCHEMA, BENCHMARK_PAYLOAD_DOMAIN_TAG, BENCHMARK_SLO_THRESHOLDS,
  CONSUMER_GATE_RECEIPT_FIELDS, FOUNDATION_ASSURANCE_MINIMUM_PROJECTION, GATE_ZERO_STEP_REF,
  MINIMUM_REQUIRED_MEMBERS, P95_AGGREGATION_METHOD,
  benchmarkPayloadDigest, benchmarkRequiredCells, createFoundationAssuranceMinimumGate,
  journeyOneClockMinimumReceiptView, proposeBenchmarkCoverageFact,
} from "../src/benchmark-minimum.v5.js";
import {
  DEADLINE_PLAIN, JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_DEADLINE_CONTRACT,
  chicagoThirtyDayDeadline, createJourneyOneClock,
} from "../src/journey-one-clock.v5.js";
import {
  createEphemeralJourneyOneClockJournal, createJourneyOneClockRecorder,
  createJourneyOneClockStore, journeyOneClockHistoryDigest, journeyOneClockKeyForState,
  journeyOneClockScopeKey,
} from "../src/journey-one-clock-store.v5.js";

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({
  actor_id: actor,
  session_ref: `session:j1-seam-${actor}`,
  authority_class: actor === "joe" || actor === "dell" ? "verified_partner" : "synthetic_oracle",
});
const copy = x => JSON.parse(JSON.stringify(x));
const iso = ms => new Date(ms).toISOString();
const DAY = 86400000;
const HOUR = 3600000;

const GATE_ZERO_AT = "2026-02-01T00:00:00.000Z";
const ACCEPTED_AT = "2026-02-02T00:00:00.000Z";
const COVERAGE_AT = "2026-02-02T12:00:00.000Z";
const OBSERVED_AT = "2026-02-03T00:00:00.000Z";
const AS_OF = "2026-02-04T00:00:00.000Z";
/** The M01 evaluation instant: an hour after the join that proposed the receipt. */
const CLOCK_AS_OF = iso(Date.parse(AS_OF) + HOUR);
const MINIMUM_TTL = 7 * DAY;

// --- the A00 side -----------------------------------------------------------

function payload() {
  return {
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    capacity_profiles: ["baseline"],
    workload_mix: [{ workload_id: "search", weight_basis_points: 10000, operation_mix_digest: D(10) }],
    request_size_distribution: [{ percentile: 50, bytes: 2048 }],
    concurrency_levels: [1],
    arrival_patterns: ["steady"],
    routes: ["/deals"],
    browsers: [{ name: "chrome", version: "141", build: "141.0.1" }],
    runtime_versions: ["node-24"],
    device_profiles: ["macbook-pro-m3"],
    hardware_profiles: ["m3-16gb"],
    network_profiles: ["broadband"],
    cache_states: ["cold", "warm"],
    samples_per_cell: 20,
    warmup_runs: 2,
    p95_aggregation_method: P95_AGGREGATION_METHOD,
    outlier_rule: "discard samples above five times the cell median",
    acknowledgement_endpoints: ["/commands/ack"],
    evaluator_identities: [I("bench-evaluator")],
    comparator_versions: ["comparator-1.4.0"],
    slo_thresholds: { ...BENCHMARK_SLO_THRESHOLDS },
    cost_expectation_matrix_digest: D(12),
    cost_variance_thresholds: { ...BENCHMARK_COST_VARIANCE_THRESHOLDS },
    deadline_contract: copy(BENCHMARK_DEADLINE_CONTRACT),
  };
}
function manifest(body = payload(), acceptedAt = ACCEPTED_AT) {
  return {
    ...copy(body),
    benchmark_manifest_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, body]),
    accepted_by_identity: I("joe"),
    accepted_at: acceptedAt,
    status: "accepted",
  };
}
/**
 * The measurement set and the coverage fact a trusted producer would compose
 * from it. Composed through A00's own evaluator, so the seam is exercised on the
 * numbers that evaluator actually emits.
 */
function measurements(body = payload()) {
  return {
    schema_version: BENCHMARK_MEASUREMENT_SET_SCHEMA,
    benchmark_payload_digest: benchmarkPayloadDigest(body),
    outlier_rule: body.outlier_rule,
    p95_aggregation_method: body.p95_aggregation_method,
    cells: benchmarkRequiredCells(body).map(cell => ({
      cell: copy(cell),
      warmup_samples: Array.from({ length: body.warmup_runs }, () => 999),
      samples: Array.from({ length: body.samples_per_cell }, () => 100),
      excluded_sample_indexes: [],
    })),
  };
}
function coverageFact(body = payload()) {
  return copy(proposeBenchmarkCoverageFact({
    payload: body,
    measurements: measurements(body),
    evaluator_identity: I("bench-evaluator"),
    evidence_ref: "safe:j1-seam:coverage-evidence",
    observed_at: COVERAGE_AT,
    ttl_expires_at: iso(Date.parse(COVERAGE_AT) + 7 * DAY),
  }));
}

function minimumProjection({ acceptedAt = ACCEPTED_AT } = {}) {
  return {
    schema_version: FOUNDATION_ASSURANCE_MINIMUM_PROJECTION,
    tenant: ORGANIZATION_TENANT_ID,
    as_of: AS_OF,
    binding: {
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      minimum_environment_manifest_digest: D(4),
      benchmark_manifest_digest: benchmarkPayloadDigest(payload()),
      maximum_member_receipt_ttl_ms: 30 * DAY,
      minimum_receipt_ttl_ms: MINIMUM_TTL,
    },
    gate_zero: { step_ref: GATE_ZERO_STEP_REF, outcome_digest: D(6), observed_at: GATE_ZERO_AT },
    benchmark_coverage: coverageFact(),
    members: MINIMUM_REQUIRED_MEMBERS.map(spec => spec.output_schema_ref === BENCHMARK_MANIFEST_SCHEMA
      ? { step_ref: spec.step_ref, receipt: manifest(payload(), acceptedAt) }
      : {
        step_ref: spec.step_ref,
        receipt: {
          gate_id: spec.gate_id,
          receipt_producer_step_ref: spec.step_ref,
          subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
          environment_manifest_digest: D(4),
          subject_environment: spec.subject_environment,
          evidence_scope: spec.evidence_scope,
          subject_maker_identity: I("builder"),
          producer_identity: I(`producer-${spec.gate_id}`),
          evaluator_identity: I(`evaluator-${spec.gate_id}`),
          producer_role: spec.producer_role,
          independent_oracle_ref: spec.oracle_ref,
          oracle_version: spec.oracle_version,
          evidence_ref: `safe:j1-seam:${spec.gate_id}`,
          fixture_set_digest: D(5),
          observed_at: OBSERVED_AT,
          ttl_expires_at: iso(Date.parse(OBSERVED_AT) + 7 * DAY),
          status: "pass",
          comparator: "exact closed-contract comparator",
          negative_admission_result: "all_required_denials_observed",
        },
      }),
    minimum_receipt_context: {
      subject_maker_identity: I("builder"),
      producer_identity: I("minimum-oracle"),
      evaluator_identity: I("minimum-evaluator"),
      evidence_ref: "safe:j1-seam:minimum-join",
      fixture_set_digest: D(5),
      comparator: "exact closed minimum join comparator",
    },
  };
}
/** Test-only authenticating capability, installed by trusted test code. */
function joinOnce(projection) {
  const evidence = new Map();
  const gate = createFoundationAssuranceMinimumGate({
    authenticateEvidence(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  const envelope = { synthetic_evidence_ref: "j1-seam-join" };
  evidence.set(digest(envelope), copy(projection));
  return gate.evaluate(envelope);
}

// --- the M01 side -----------------------------------------------------------

const KERNEL_ARTIFACT = D(20);
const KERNEL_FIXTURES = D(21);

function terminusReceipt(observedAt) {
  return {
    receipt_ref: "safe:receipt:journey-one-kernel-production",
    producer_step_ref: "step:j1-kernel-production-outcome",
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    rollout_environment_manifest_digest: D(7),
    artifact_digest: KERNEL_ARTIFACT,
    evidence_scope: "production",
    subject_maker_identity: I("builder"),
    producer_identity: I("kernel-outcome-oracle"),
    evaluator_identity: I("kernel-outcome-evaluator"),
    producer_role: "independent_journey_one_kernel_outcome_oracle",
    independent_oracle_ref: "oracle:rollout-component:journey-one-kernel-production",
    oracle_version: "1.0.0",
    evidence_ref: "safe:j1-seam:kernel-production-evidence",
    fixture_set_digest: KERNEL_FIXTURES,
    observed_at: observedAt,
    ttl_expires_at: iso(Date.parse(observedAt) + 2 * DAY),
    status: "pass",
    comparator: "exact kernel production comparator",
    negative_admission_result: "all_required_denials_observed",
    subject_environment: "production",
  };
}
/**
 * The M01 projection the issuance adapter would build around the SAME receipt
 * A00 proposed. The benchmark digest and its acceptance instant are carried
 * across from the join's own result rather than re-asserted, which is the only
 * reason this projection is about the same benchmark at all.
 *
 * `accepted_by_identity` is NOT carried across: A00's result publishes no
 * acceptor, so the value below is asserted by this projection the way a live
 * verifier would have to assert it — derived from the authenticated acceptance
 * record and the LIVE actor, never read back out of a stored class string. It is
 * written here as a fixture, and the two modules agreeing about it proves
 * nothing about the acceptor.
 */
function clockProjection(join, receipt, { asOf = CLOCK_AS_OF, admittedAt = AS_OF } = {}) {
  return {
    schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
    tenant: ORGANIZATION_TENANT_ID,
    as_of: asOf,
    binding: {
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      minimum_environment_manifest_digest: D(4),
      production_environment_manifest_digest: D(7),
      maximum_minimum_receipt_ttl_ms: 30 * DAY,
      maximum_completion_receipt_ttl_ms: 30 * DAY,
    },
    benchmark: {
      manifest_digest: join.benchmark_manifest_digest,
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      // DAYS on the manifest, HOURS here: the conversion is the adapter's, and
      // this is the one place in these two suites where it is performed.
      deadline_contract: copy(JOURNEY_ONE_DEADLINE_CONTRACT),
      accepted_at: join.benchmark_accepted_at,
      accepted_by_identity: I("joe"),
    },
    minimum_history: [{ admitted_at: admittedAt, receipt: copy(receipt) }],
    completion: null,
    completion_expectation: { artifact_digest: KERNEL_ARTIFACT, fixture_set_digest: KERNEL_FIXTURES },
    pauses: [], amendments: [], history: null,
  };
}
function evaluateOnce(projection) {
  const evidence = new Map();
  const clock = createJourneyOneClock({
    verifySnapshot(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  const envelope = { synthetic_receipt_ref: "j1-seam-clock" };
  evidence.set(digest(envelope), copy(projection));
  return clock.evaluate(envelope);
}

function seam() {
  const join = joinOnce(minimumProjection());
  const view = journeyOneClockMinimumReceiptView(join.proposed_receipt);
  return { join, view, result: evaluateOnce(clockProjection(join, view)) };
}

// --- the record layer, on the same one artifact ------------------------------
//
// The two modules above are pure. This third seam is the durable one: the clock
// the join started, evaluated by a REAL kernel and filed by the REAL store
// through its trusted recorder, into a non-durable reference journal. Nothing
// here is authenticated and nothing is persisted beyond the process; what is
// exercised is that the scope a revision is filed under is the scope the kernel
// verified THIS artifact's projection against.

/** A kernel whose envelopes can be handed out one at a time, for the recorder. */
function clockHarness() {
  const evidence = new Map(); let serial = 0;
  const clock = createJourneyOneClock({
    verifySnapshot(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  return { clock, envelopeFor(projection) {
    const envelope = { synthetic_receipt_ref: `j1-seam-record-${++serial}` };
    evidence.set(digest(envelope), copy(projection));
    return envelope;
  } };
}

const STORE_ACTOR = { slug: "claude", human: false, sponsoring_human_slug: "joe" };
const VERIFIER_REF = "safe:verifier:j1-seam-trusted-projection-verifier";
/**
 * The accepted scope this seam's clock belongs to, composed exactly as a trusted
 * integration would: the projection's own three binding digests and the two gate
 * ids the deadline contract names. `scope_ref` is a label and is not hashed.
 */
const SEAM_SCOPE = Object.freeze({
  benchmark_candidate_digest: D(2),
  benchmark_policy_digest: D(3),
  benchmark_subject_digest: D(1),
  clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
  clock_terminus_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
  scope_ref: "safe:clock-scope:j1-seam",
  tenant: ORGANIZATION_TENANT_ID,
});
let seamKeySerial = 0;
const uuid = () =>
  `00000000-0000-4000-8000-${(++seamKeySerial).toString(16).padStart(12, "0")}`;

function recordingSeam(scope = SEAM_SCOPE) {
  const journal = createEphemeralJourneyOneClockJournal();
  const kernel = clockHarness();
  const store = createJourneyOneClockStore({ journal, actor: STORE_ACTOR, clock_scope: scope });
  return { journal, kernel, store,
    recorder: createJourneyOneClockRecorder({ clock: kernel.clock, store, verifier_ref: VERIFIER_REF }) };
}

test("the clock the join started is stored under the scope the kernel verified this artifact against", async () => {
  const { join, view } = seam();
  const projection = clockProjection(join, view);
  const expected = evaluateOnce(copy(projection));
  const { kernel, store, recorder } = recordingSeam();

  const recorded = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(projection), expected_prior_history_digest: null,
    idempotency_key: uuid(), clock_ref: "J1-SEAM" });

  // The clock is addressed by the origin A00 published, and filed under the
  // scope derived from the binding the kernel enforced over that same artifact.
  assert.equal(recorded.clock_key, journeyOneClockKeyForState(expected.state));
  assert.equal(recorded.clock_scope_key, journeyOneClockScopeKey(SEAM_SCOPE));
  assert.equal(recorded.clock_scope_matches_verified_binding, true);
  assert.equal(expected.verified_binding.subject_digest, D(1));
  assert.equal(expected.verified_binding.candidate_digest, D(2));
  assert.equal(expected.verified_binding.policy_digest, D(3));
  assert.equal(expected.verified_binding.tenant, ORGANIZATION_TENANT_ID);

  // THE STORED HISTORY IS THE KERNEL'S, DIGEST FOR DIGEST. The binding rides
  // outside the hashed state, so storing it changed nothing about the history.
  assert.equal(recorded.history_digest, expected.state.history_digest);
  const readback = await store.read(recorded.clock_key);
  assert.deepEqual(readback.history, copy(expected.state));
  assert.equal(journeyOneClockHistoryDigest(readback.history), expected.state.history_digest);
  assert.equal(readback.history.origin_receipt_digest, join.proposed_receipt_reference_digest);
  assert.equal(readback.history.origin_at, AS_OF);
  assert.equal(readback.clock_scope_ref, SEAM_SCOPE.scope_ref);
  // And storing it accepts nothing.
  assert.equal(recorded.deadline_accepted_by_record_layer, false);
  assert.equal(recorded.kernel_verdict.deadline_success, false);
  assert.equal(recorded.effects.acceptances, 0);
  assert.equal(recorded.effects.clock_started, false);
});

test("the terminus receipt appends onto the stored clock, and the CAS is exact", async () => {
  const { join, view } = seam();
  const { kernel, store, recorder } = recordingSeam();
  const first = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(clockProjection(join, view)),
    expected_prior_history_digest: null, idempotency_key: uuid() });

  // AN EXACT REPLAY IS NOT A SECOND WRITE.
  const shared = uuid();
  const advanced = clockProjection(join, view);
  const observedAt = iso(Date.parse(AS_OF) + 20 * DAY);
  advanced.as_of = observedAt;
  advanced.history = copy((await store.read(first.clock_key)).history);
  advanced.completion = {
    gate_id: "journey-one-kernel-production-accepted",
    combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"],
    receipts: [terminusReceipt(observedAt)],
  };
  const done = await recorder.evaluateAndRecord({ envelope: kernel.envelopeFor(advanced),
    expected_prior_history_digest: first.history_digest, idempotency_key: shared });
  assert.equal(done.revision_ordinal, 1);
  assert.equal(done.kernel_verdict.status, "completed_on_time");
  const replay = await recorder.evaluateAndRecord({ envelope: kernel.envelopeFor(advanced),
    expected_prior_history_digest: first.history_digest, idempotency_key: shared });
  assert.equal(replay.replayed, true);
  assert.equal(replay.history_digest, done.history_digest);

  // A STALE PRIOR IS REFUSED, and the head is untouched by the attempt.
  await assert.rejects(recorder.evaluateAndRecord({ envelope: kernel.envelopeFor(advanced),
    expected_prior_history_digest: first.history_digest, idempotency_key: uuid() }),
    error => error.code === "clock_stale_prior_history_digest");
  const readback = await store.read(first.clock_key);
  assert.equal(readback.revision_count, 2);
  assert.equal(readback.history_digest, done.history_digest);
  assert.equal(readback.history.completion_observed_at, observedAt);
});

test("a store for another accepted scope cannot file this artifact, and files nothing", async () => {
  const { join, view } = seam();
  const projection = clockProjection(join, view);
  const expectedKey = journeyOneClockKeyForState(evaluateOnce(copy(projection)).state);

  // Each of the three binding digests, moved one at a time on the STORE's side.
  for (const field of ["benchmark_subject_digest", "benchmark_candidate_digest",
    "benchmark_policy_digest"]) {
    const { kernel, store, journal } = recordingSeam({ ...SEAM_SCOPE, [field]: D(50) });
    const recorder = createJourneyOneClockRecorder({
      clock: kernel.clock, store, verifier_ref: VERIFIER_REF });
    await assert.rejects(recorder.evaluateAndRecord({
      envelope: kernel.envelopeFor(projection), expected_prior_history_digest: null,
      idempotency_key: uuid() }), error => {
      assert.equal(error.code, "clock_scope_not_the_verified_binding");
      assert.deepEqual(error.detail.differing_fields, [field]);
      return true;
    });
    assert.equal(await journal.readClock(expectedKey), null, "nothing was written for it");
    assert.equal((await store.read(expectedKey)).exists, false);
  }
});

test("relabelling this artifact's accepted scope opens no second clock for it", async () => {
  const { join, view } = seam();
  const { journal, kernel, store, recorder } = recordingSeam();
  const first = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(clockProjection(join, view)),
    expected_prior_history_digest: null, idempotency_key: uuid() });

  // The same accepted scope under a different human name is the SAME scope, so
  // a second admitted minimum presented under it meets the clock that scope
  // already holds instead of starting a fresh one.
  const renamed = createJourneyOneClockStore({ journal, actor: STORE_ACTOR,
    clock_scope: { ...SEAM_SCOPE, scope_ref: "safe:clock-scope:j1-seam-renamed" } });
  assert.equal(renamed.clock_scope.clock_scope_key, store.clock_scope.clock_scope_key);
  // A SECOND ADMITTED MINIMUM, joined an hour later: a genuinely different
  // origin, and therefore a clock key this journal has never seen, so the
  // compare-and-swap has nothing to refuse it with.
  const laterAt = iso(Date.parse(AS_OF) + HOUR);
  const laterProjection = minimumProjection();
  laterProjection.as_of = laterAt;
  const secondJoin = joinOnce(laterProjection);
  const secondView = journeyOneClockMinimumReceiptView(secondJoin.proposed_receipt);
  const secondProjection = clockProjection(secondJoin, secondView,
    { asOf: iso(Date.parse(laterAt) + HOUR), admittedAt: laterAt });
  const rebased = evaluateOnce(copy(secondProjection)).state;
  assert.equal(rebased.origin_at, laterAt);
  assert.notEqual(journeyOneClockKeyForState(rebased), first.clock_key);

  const other = clockHarness();
  const otherRecorder = createJourneyOneClockRecorder({
    clock: other.clock, store: renamed, verifier_ref: VERIFIER_REF });
  await assert.rejects(otherRecorder.evaluateAndRecord({
    envelope: other.envelopeFor(secondProjection),
    expected_prior_history_digest: null, idempotency_key: uuid() }),
    error => error.code === "clock_scope_already_bound");
  assert.equal(await journal.readClock(journeyOneClockKeyForState(rebased)), null);
  assert.equal((await store.read(first.clock_key)).revision_count, 1);
});

// --- one receipt, one shape -------------------------------------------------

test("the receipt A00 proposes is the receipt M01 reads, with nothing added between them", () => {
  const { join, view } = seam();
  assert.deepEqual(Object.keys(join.proposed_receipt).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.deepEqual({ ...view }, { ...join.proposed_receipt });
  assert.ok(!Object.hasOwn(view, "schema_version"));
  // The raw r7-exact receipt is consumable by the clock without the view at all;
  // the view exists to REFUSE what M01 cannot read, not to make it readable.
  const raw = evaluateOnce(clockProjection(join, copy(join.proposed_receipt)));
  const adapted = evaluateOnce(clockProjection(join, view));
  assert.equal(raw.state.origin_receipt_digest, adapted.state.origin_receipt_digest);
  assert.deepEqual(raw.state, adapted.state);
});

test("one receipt has one identity on both sides of the seam", () => {
  const { join, view, result } = seam();
  // The digest A00 publishes for the issuance adapter, the digest of the view,
  // and the origin digest the clock records are all the same string. A durable
  // store keyed on either can find the origin the clock names.
  assert.equal(digest(view), digest(join.proposed_receipt));
  assert.equal(join.proposed_receipt_reference_digest, digest(join.proposed_receipt));
  assert.equal(result.state.origin_receipt_digest, join.proposed_receipt_reference_digest);
  // And that identity is what a pause binds to: an approval bound to the digest
  // A00 published is accepted by the clock without translation.
  const projection = clockProjection(join, view);
  const startsAt = iso(Date.parse(CLOCK_AS_OF) + DAY);
  const body = {
    pause_id: "safe:j1-seam:pause-one",
    clock_origin_digest: join.proposed_receipt_reference_digest,
    blocker_ref: "safe:external-blocker:seam-provider",
    starts_at: startsAt,
    approved_at: iso(Date.parse(startsAt) - HOUR),
    approved_by_identity: I("dell"),
  };
  projection.pauses = [{ ...body, ends_at: null, approval_digest: digest(["doctorcre:j1-clock-pause:v1", body]) }];
  projection.as_of = iso(Date.parse(startsAt) + 12 * HOUR);
  const paused = evaluateOnce(projection);
  assert.equal(paused.state.paused_ms, 12 * HOUR);
  assert.equal(paused.state.events.at(-1).type, "pause_approved");
});

test("the clock the join proposes actually starts, on the join's own observation instant", () => {
  const { join, result } = seam();
  assert.equal(join.proposed_receipt.observed_at, AS_OF);
  assert.equal(result.state.origin_at, AS_OF);
  assert.equal(result.state.status, "running");
  assert.equal(result.state.base_deadline_at, chicagoThirtyDayDeadline(AS_OF).due_at);
  assert.equal(result.state.base_deadline_resolution, DEADLINE_PLAIN);
  assert.equal(result.state.origin_benchmark_manifest_digest, join.benchmark_manifest_digest);
  assert.equal(result.deadline_success, false);
  assert.equal(result.replan_required, false);
  // Neither module claims to have done anything durable.
  assert.equal(join.issued, false);
  assert.equal(join.clock_started, false);
  assert.equal(result.durable_history_write_required, true);
  assert.equal(result.authority_granted, false);
  assert.equal(result.effects.database_writes, 0);
});

test("an r7-exact terminus receipt stops the clock the join started", () => {
  const { join, view } = seam();
  const projection = clockProjection(join, view);
  const observedAt = iso(Date.parse(AS_OF) + 20 * DAY);
  projection.as_of = observedAt;
  projection.completion = {
    gate_id: "journey-one-kernel-production-accepted",
    combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"],
    receipts: [terminusReceipt(observedAt)],
  };
  const done = evaluateOnce(projection);
  assert.equal(Object.keys(projection.completion.receipts[0]).length, 22);
  assert.equal(done.state.status, "completed_on_time");
  assert.equal(done.deadline_success, true);
  assert.equal(done.replan_required, false);
  assert.equal(done.state.completion_observed_at, observedAt);
  // The same terminus with a schema_version added is refused, on the same
  // closed-shape rule that refuses any other extra field.
  const decorated = copy(projection);
  decorated.completion.receipts[0].schema_version = "rollout-component-receipt.v1";
  assert.throws(() => evaluateOnce(decorated), e => e.code === "closed_shape");
  // And a terminus naming a different kernel artifact does not stop this clock.
  const other = copy(projection);
  other.completion.receipts[0].artifact_digest = D(99);
  assert.throws(() => evaluateOnce(other), e => e.code === "completion_artifact_mismatch");
});

// --- the acceptance boundary is one convention across the seam --------------

test("an acceptance at the join instant is refused by A00 because M01 could never use it", () => {
  // A00 refuses to propose it...
  assert.throws(() => joinOnce(minimumProjection({ acceptedAt: AS_OF })),
    e => e.code === "benchmark_accepted_at_or_after_reference");
  // ...and the clock refuses the same instant against the origin it would have
  // become, so the two modules draw the boundary in the same place. The join
  // below is built one millisecond earlier only so a receipt exists to test.
  const join = joinOnce(minimumProjection({ acceptedAt: iso(Date.parse(AS_OF) - 1) }));
  const view = journeyOneClockMinimumReceiptView(join.proposed_receipt);
  assert.equal(evaluateOnce(clockProjection(join, view)).state.origin_at, AS_OF);
  const equal = clockProjection(join, view);
  equal.benchmark.accepted_at = join.proposed_receipt.observed_at;
  assert.throws(() => evaluateOnce(equal), e => e.code === "benchmark_not_accepted_before_origin");
});

// --- the unevaluated manifest, and what the seam still does not carry -------

test("an unmeasured benchmark cannot reach the clock, because the join refuses it first", () => {
  // This was the surviving r7 denial category ("unevaluated"): an accepted
  // manifest with no measurement evidence used to join, propose a receipt and
  // start a clock. The join now requires the coverage fact, so the artifact M01
  // consumes cannot exist without one.
  const absent = minimumProjection();
  delete absent.benchmark_coverage;
  assert.throws(() => joinOnce(absent),
    e => e.code === "closed_shape" && e.detail.missing.includes("benchmark_coverage"));
  const stale = minimumProjection();
  stale.benchmark_coverage.ttl_expires_at = "2026-02-03T00:00:00.000Z";
  assert.throws(() => joinOnce(stale), e => e.code === "benchmark_coverage_not_current");
  const claimed = minimumProjection();
  claimed.benchmark_coverage.measured_cell_count += 1;
  claimed.benchmark_coverage.required_cell_count += 1;
  assert.throws(() => joinOnce(claimed), e => e.code === "benchmark_coverage_cell_count_mismatch");
});

test("the coverage binding does not survive the seam, and neither module pretends it does", () => {
  const { join } = seam();
  // A00 publishes the two digests a durable store must persist beside the
  // receipt, and says in the same result that it evaluated nothing itself.
  assert.equal(join.benchmark_coverage_evaluated_here, false);
  assert.equal(join.durable_coverage_verification_required, true);
  assert.equal(join.benchmark_coverage_required_cell_count, benchmarkRequiredCells(payload()).length);
  assert.equal(join.benchmark_coverage_evaluation_digest, coverageFact().evaluation_digest);
  // r7's twenty-one receipt fields have no slot for any of it, so the receipt
  // that crosses the seam carries the benchmark's identity and not its coverage.
  const view = journeyOneClockMinimumReceiptView(join.proposed_receipt);
  for (const field of Object.keys(view)) assert.ok(!field.startsWith("benchmark_coverage"));
  // M01's benchmark projection has no coverage slot either. That is the
  // remaining integration fact, stated rather than papered over: the clock
  // relies on the join having refused an unevaluated manifest upstream, and the
  // store must keep the coverage digests beside the origin receipt for anyone
  // who later has to prove the origin was measured.
  const projection = clockProjection(join, view);
  assert.deepEqual(Object.keys(projection.benchmark).sort(), [
    "accepted_at", "accepted_by_identity", "candidate_digest", "deadline_contract",
    "manifest_digest", "policy_digest", "subject_digest",
  ]);
  assert.equal(evaluateOnce(projection).state.origin_receipt_digest, join.proposed_receipt_reference_digest);
});
