import test from "node:test";
import assert from "node:assert/strict";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_DEADLINE_CONTRACT,
  createJourneyOneClock, chicagoThirtyDayDeadline } from "../src/journey-one-clock.v5.js";

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({ actor_id: actor, session_ref: `session:synthetic-${actor}`, authority_class: actor === "joe" || actor === "dell" ? "verified_partner" : "synthetic_oracle" });
const copy = x => JSON.parse(JSON.stringify(x));
const iso = ms => new Date(ms).toISOString();
const HOUR = 3600000;
const ORIGIN = "2026-09-09T15:00:00.000Z";
function minimum(origin = ORIGIN) {
  return {
    schema_version: "consumer-gate-receipt.v1", gate_id: "foundation-assurance-minimum-accepted",
    receipt_producer_step_ref: "step:foundation-assurance-minimum-receipt",
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3), environment_manifest_digest: D(4),
    subject_environment: "candidate", evidence_scope: "candidate-and-test",
    subject_maker_identity: I("maker"), producer_identity: I("producer"), evaluator_identity: I("evaluator"),
    producer_role: "independent_foundation_assurance_minimum_oracle",
    independent_oracle_ref: "oracle:gate-producer:foundation-assurance-minimum", oracle_version: "1.0.0",
    evidence_ref: "safe:synthetic:min-evidence", fixture_set_digest: D(5), observed_at: origin,
    ttl_expires_at: iso(Date.parse(origin) + 24 * HOUR), status: "pass", comparator: "synthetic-exact-comparator",
    negative_admission_result: "all_required_denials_observed",
  };
}
function completed(at) {
  const m = minimum(at);
  delete m.gate_id; delete m.receipt_producer_step_ref; delete m.environment_manifest_digest;
  return { gate_id: "journey-one-kernel-production-accepted", combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"], receipts: [{ ...m,
      schema_version: "rollout-component-receipt.v1", receipt_ref: "safe:receipt:journey-one-kernel-production",
      producer_step_ref: "step:j1-kernel-production-outcome", rollout_environment_manifest_digest: D(6), artifact_digest: D(7),
      subject_environment: "production", evidence_scope: "production",
      producer_role: "independent_journey_one_kernel_outcome_oracle",
      independent_oracle_ref: "oracle:rollout-component:journey-one-kernel-production",
    }] };
}
function snapshot(as_of = ORIGIN, origin = ORIGIN) {
  return { schema_version: JOURNEY_ONE_CLOCK_PROJECTION, tenant: ORGANIZATION_TENANT_ID, as_of,
    binding: { subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3), minimum_environment_manifest_digest: D(4),
      production_environment_manifest_digest: D(6), maximum_receipt_ttl_ms: 48 * HOUR },
    benchmark: { manifest_digest: D(8), subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      deadline_contract: copy(JOURNEY_ONE_DEADLINE_CONTRACT), accepted_at: iso(Date.parse(origin) - HOUR), accepted_by_identity: I("joe") },
    minimum_history: [{ admitted_at: origin, receipt: minimum(origin) }],
    completion: null, pauses: [], amendments: [], history: null,
  };
}
// Test-only authenticating capability. The WeakMap is installed by trusted test
// code; no JSON flag can cause an unknown envelope to acquire a projection.
function harness() {
  const evidence = new Map(); let serial = 0;
  const clock = createJourneyOneClock({ verifySnapshot(envelope) {
    const found = evidence.get(digest(envelope));
    if (!found) throw new Error("synthetic-authentication-refused");
    return { envelope_digest: digest(envelope), snapshot: copy(found) };
  } });
  return { clock, evaluate(p) { const e = { synthetic_receipt_ref: `test-${++serial}` }; evidence.set(digest(e), copy(p)); return clock.evaluate(e); } };
}
function run(p) { return harness().evaluate(p); }
function refuse(p, code) { assert.throws(() => run(p), e => e.code === code); }
function pause(p, start, end, id = "one", partner = "joe", approved = iso(Date.parse(start) - 1)) {
  const payload = { pause_id: `safe:synthetic:pause-${id}`, clock_origin_digest: digest(p.minimum_history[0].receipt),
    blocker_ref: "safe:external-blocker:synthetic-provider", starts_at: start,
    approved_at: approved, approved_by_identity: I(partner) };
  return { ...payload, ends_at: end, approval_digest: digest(["doctorcre:j1-clock-pause:v1", payload]) };
}
function resignPause(pause) { const { approval_digest, ends_at, ...body } = pause; pause.approval_digest = digest(["doctorcre:j1-clock-pause:v1", body]); }
function amend(originDigest, at, { name = "amendment", manifest = null, partner = "dell" } = {}) {
  const body = { amendment_ref: `safe:synthetic:${name}`, clock_origin_digest: originDigest,
    benchmark_manifest_digest: manifest, description_digest: D(42), accepted_at: at, accepted_by_identity: I(partner) };
  return { ...body, amendment_digest: digest(["doctorcre:j1-clock-amendment:v1", body]) };
}
function reseal(history) { const { history_digest, ...body } = history; history.history_digest = digest(body); return history; }

// --- the deadline itself: 30 Chicago dates, not 720 hours ------------------

for (const [origin, due, hours] of [
  ["2026-02-20T16:15:00.000Z", "2026-03-22T15:15:00.000Z", 719],
  ["2026-10-10T15:15:00.000Z", "2026-11-09T16:15:00.000Z", 721],
  [ORIGIN, "2026-10-09T15:00:00.000Z", 720],
]) test(`thirty Chicago dates from ${origin} is ${hours} elapsed hours`, () => {
  const result = chicagoThirtyDayDeadline(origin);
  assert.equal(result.due_at, due);
  assert.equal((Date.parse(due) - Date.parse(origin)) / HOUR, hours);
});

test("DST gap and fold targets remain explicitly unresolved", () => {
  assert.equal(chicagoThirtyDayDeadline("2026-02-06T08:30:00.000Z").reason_id, "nonexistent_chicago_wall_time");
  assert.equal(chicagoThirtyDayDeadline("2026-10-02T06:30:00.000Z").reason_id, "ambiguous_chicago_wall_time");
  const p = snapshot("2026-03-10T15:00:00.000Z", "2026-02-06T08:30:00.000Z");
  const r = run(p);
  assert.equal(r.state.status, "unresolved_deadline"); assert.equal(r.deadline_success, false);
  assert.equal(r.state.due_at, null); assert.equal(r.unresolved_reason, "nonexistent_chicago_wall_time");
});

test("an unresolvable deadline withholds the on-time judgement but never discards a completion", () => {
  const p = snapshot("2026-03-10T15:00:00.000Z", "2026-02-06T08:30:00.000Z");
  p.completion = completed(p.as_of);
  const r = run(p);
  assert.equal(r.state.status, "completed_unresolved_deadline");
  assert.equal(r.state.completion_observed_at, p.as_of);
  assert.equal(r.state.events.at(-1).type, "completion_observed");
  assert.equal(r.deadline_success, false); assert.equal(r.completion_currently_usable, true);
  assert.equal(r.replan_required, false); assert.equal(r.state.due_at, null);
});

// --- origin selection -------------------------------------------------------

test("first historical admissible minimum fixes origin after its TTL has elapsed", () => {
  const p = snapshot("2026-09-15T15:00:00.000Z");
  p.minimum_history.unshift({ admitted_at: "2026-09-14T15:00:00.000Z", receipt: minimum("2026-09-14T15:00:00.000Z") });
  const r = run(p); assert.equal(r.state.origin_at, ORIGIN); assert.equal(r.state.due_at, "2026-10-09T15:00:00.000Z");
});

test("a nonpassing or lapsed earlier attempt is skipped rather than deadlocking the clock", () => {
  const p = snapshot("2026-09-15T15:00:00.000Z");
  const failed = "2026-09-07T15:00:00.000Z";
  p.minimum_history.unshift({ admitted_at: failed, receipt: { ...minimum(failed), status: "fail" } });
  const lapsed = "2026-09-08T15:00:00.000Z";
  p.minimum_history.unshift({ admitted_at: "2026-09-09T16:00:00.000Z", receipt: minimum(lapsed) });
  const r = run(p);
  assert.equal(r.state.origin_at, ORIGIN);
  assert.equal(r.state.origin_receipt_digest, digest(minimum()));
  const q = snapshot();
  q.minimum_history = [{ admitted_at: ORIGIN, receipt: { ...minimum(), status: "fail" } }];
  refuse(q, "origin_unavailable");
});

test("a structurally wrong minimum refuses the projection even beside an admissible one", () => {
  for (const [key, value, code] of [
    ["gate_id", "journey-one-production-accepted", "wrong_origin_gate"],
    ["candidate_digest", D(99), "receipt_binding_mismatch"],
    ["environment_manifest_digest", D(99), "receipt_environment_mismatch"],
    ["receipt_producer_step_ref", "step:foundation-assurance-receipt", "wrong_producer_or_schema"],
  ]) {
    const p = snapshot();
    p.minimum_history.push({ admitted_at: ORIGIN, receipt: { ...minimum(), [key]: value } });
    refuse(p, code);
  }
});

test("two minimums sharing an observed_at pick a digest-ordered origin, not an array-ordered one", () => {
  const one = minimum(), two = { ...minimum(), evidence_ref: "safe:synthetic:min-evidence-two" };
  const expected = [digest(one), digest(two)].sort()[0];
  const forward = snapshot(), reverse = snapshot();
  forward.minimum_history = [{ admitted_at: ORIGIN, receipt: one }, { admitted_at: ORIGIN, receipt: two }];
  reverse.minimum_history = [{ admitted_at: ORIGIN, receipt: two }, { admitted_at: ORIGIN, receipt: one }];
  assert.equal(run(forward).state.origin_receipt_digest, expected);
  assert.equal(run(reverse).state.origin_receipt_digest, expected);
});

test("missing minimum and duplicates refuse", () => {
  const p = snapshot(); p.minimum_history = []; refuse(p, "origin_unavailable");
  p.minimum_history = [{ admitted_at: ORIGIN, receipt: minimum() }, { admitted_at: ORIGIN, receipt: minimum() }]; refuse(p, "duplicate_minimum");
});

test("an inadmissible or impossible admission cannot start the clock", () => {
  for (const mode of ["expired", "future", "status", "admitted_ahead"]) {
    const p = snapshot("2026-09-11T15:00:00.000Z");
    if (mode === "expired") p.minimum_history[0].admitted_at = p.minimum_history[0].receipt.ttl_expires_at;
    if (mode === "future") p.minimum_history[0].receipt.observed_at = "2026-09-10T15:00:00.000Z";
    if (mode === "status") p.minimum_history[0].receipt.status = "fail";
    if (mode === "admitted_ahead") p.minimum_history[0].admitted_at = "2026-09-12T15:00:00.000Z";
    refuse(p, { expired: "origin_unavailable", status: "origin_unavailable",
      future: "receipt_observed_after_reference", admitted_ahead: "future_admission" }[mode]);
  }
});

// --- benchmark acceptance and amendment ------------------------------------

test("benchmark must be partner accepted strictly before the first pass", () => {
  // Late acceptance and future-dated acceptance are distinct defects and are
  // presented one at a time. `as_of` sits a day past the origin pass here, so
  // these acceptances are late without also being ahead of the verified instant.
  for (const accepted of [ORIGIN, "2026-09-10T12:00:00.000Z"]) {
    const late = snapshot("2026-09-10T15:00:00.000Z"); late.benchmark.accepted_at = accepted;
    refuse(late, "benchmark_not_accepted_before_origin");
  }
  const edge = snapshot(); edge.benchmark.accepted_at = iso(Date.parse(ORIGIN) - 1);
  assert.equal(run(edge).state.origin_benchmark_manifest_digest, D(8));
  // An acceptance dated past the verified instant is refused on its own terms,
  // before any question of where it falls relative to the origin.
  const ahead = snapshot(); ahead.benchmark.accepted_at = iso(Date.parse(ORIGIN) + 1);
  refuse(ahead, "benchmark_accepted_in_the_future");
  const p = snapshot(); p.benchmark.accepted_by_identity = I("model"); refuse(p, "verified_partner_required");
  const q = snapshot(); q.benchmark.deadline_contract.calendar_days = 31; refuse(q, "wrong_deadline_contract");
  const r = snapshot(); r.benchmark.subject_digest = D(99); refuse(r, "benchmark_binding_mismatch");
});

test("a replacement benchmark manifest needs an exact partner amendment and never rebases the clock", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z");
  const before = run(p);
  p.history = before.state;
  p.benchmark.manifest_digest = D(9);
  p.benchmark.accepted_at = "2026-09-10T12:00:00.000Z";
  refuse(p, "unamended_benchmark_replacement");
  const other = copy(p);
  other.amendments = [amend(before.state.origin_receipt_digest, "2026-09-10T13:00:00.000Z", { manifest: D(10) })];
  refuse(other, "unamended_benchmark_replacement");
  p.amendments = [amend(before.state.origin_receipt_digest, "2026-09-10T13:00:00.000Z", { manifest: D(9) })];
  const after = run(p);
  assert.equal(after.state.origin_at, before.state.origin_at);
  assert.equal(after.state.origin_receipt_digest, before.state.origin_receipt_digest);
  assert.equal(after.state.base_deadline_at, before.state.base_deadline_at);
  assert.equal(after.state.due_at, before.state.due_at);
  assert.equal(after.state.origin_benchmark_manifest_digest, D(8));
  assert.equal(after.state.current_benchmark_manifest_digest, D(9));
  assert.equal(after.benchmark_amended, true);
  assert.equal(after.state.events.at(-1).type, "amendment_recorded");
  assert.deepEqual(after.state.events.slice(0, before.state.events.length), before.state.events);
  // A clock with no recorded history has no original benchmark to preserve, so
  // the first evaluation must still present the pre-origin accepted manifest.
  // `as_of` again trails the origin, isolating lateness from future-dating.
  const fresh = snapshot("2026-09-10T15:00:00.000Z");
  fresh.benchmark.manifest_digest = D(9); fresh.benchmark.accepted_at = "2026-09-09T16:00:00.000Z";
  refuse(fresh, "benchmark_not_accepted_before_origin");
  // An amendment naming the manifest cannot stand in for that acceptance: with
  // no history there is no original for the amendment to be a replacement of.
  const unhistoried = copy(fresh);
  unhistoried.amendments = [amend(digest(minimum()), "2026-09-10T13:00:00.000Z", { manifest: D(9) })];
  refuse(unhistoried, "benchmark_not_accepted_before_origin");
  // The future-dating guard covers an amended replacement too.
  const ahead = copy(p); ahead.benchmark.accepted_at = "2026-09-10T15:00:00.001Z";
  refuse(ahead, "benchmark_accepted_in_the_future");
});

test("an exact partner amendment appends metadata without resetting origin or deadline", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z"); const before = run(p);
  p.amendments = [amend(before.state.origin_receipt_digest, p.as_of)]; p.history = before.state;
  const after = run(p);
  assert.equal(after.state.origin_at, before.state.origin_at); assert.equal(after.state.due_at, before.state.due_at);
  assert.equal(after.state.events.at(-1).type, "amendment_recorded");
  assert.equal(after.benchmark_amended, false);
  const forged = copy(p); forged.amendments[0].description_digest = D(43); refuse(forged, "amendment_digest_mismatch");
  const agent = copy(p); agent.amendments = [amend(before.state.origin_receipt_digest, p.as_of, { partner: "model" })];
  refuse(agent, "verified_partner_required");
  const rebased = copy(p); rebased.amendments = [amend(D(99), p.as_of)]; refuse(rebased, "amendment_reset_or_time");
  const twice = copy(p); twice.amendments.push(copy(twice.amendments[0])); refuse(twice, "duplicate_amendment");
  p.amendments[0].reset_at = p.as_of; refuse(p, "closed_shape");
});

// --- the terminus -----------------------------------------------------------

for (const offset of [-1, 0, 1]) test(`completion deadline offset ${offset} milliseconds`, () => {
  const at = iso(Date.parse("2026-10-09T15:00:00.000Z") + offset), p = snapshot(at);
  p.completion = completed(at); const r = run(p);
  assert.equal(r.deadline_success, offset <= 0); assert.equal(r.replan_required, offset > 0);
  assert.equal(r.state.status, offset <= 0 ? "completed_on_time" : "completed_late");
});

test("passing full migration and wrong kernel obligation joins never substitute", () => {
  const p = snapshot(); p.completion = completed(ORIGIN);
  p.completion.gate_id = "journey-one-production-accepted"; refuse(p, "wrong_terminus_gate");
  p.completion.gate_id = "journey-one-kernel-production-accepted";
  p.completion.combiner = "any_current_pass"; refuse(p, "wrong_terminus_gate");
  p.completion.combiner = "all_current_exact_distinct_pass";
  p.completion.obligation_decision_ids = ["Q002.D1", "Q014.D1", "Q014.D1"]; refuse(p, "wrong_kernel_obligations");
  p.completion.obligation_decision_ids = ["Q002.D1", "Q123.D1", "Q014.D1"]; refuse(p, "wrong_kernel_obligations");
  p.completion.obligation_decision_ids = ["Q002.D1", "Q014.D1", "Q123.D1"];
  p.completion.receipts.push(copy(p.completion.receipts[0])); refuse(p, "missing_or_duplicate_kernel_receipt");
});

test("kernel receipt exact schema, identities, currentness, scope and digests are enforced", () => {
  const cases = [
    ["schema_version", "consumer-gate-receipt.v1", "wrong_producer_or_schema"],
    ["producer_step_ref", "step:j1-core-production-outcome", "wrong_producer_or_schema"],
    ["receipt_ref", "safe:receipt:journey-one-production", "wrong_terminus_receipt"],
    ["candidate_digest", D(99), "receipt_binding_mismatch"],
    ["rollout_environment_manifest_digest", D(99), "receipt_environment_mismatch"],
    ["evidence_scope", "candidate-and-test", "receipt_scope_mismatch"],
    ["status", "stale", "nonpassing_receipt"],
    ["negative_admission_result", "denials_not_exercised", "nonpassing_receipt"],
    ["ttl_expires_at", ORIGIN, "invalid_receipt_window"],
    ["ttl_expires_at", iso(Date.parse(ORIGIN) + 72 * HOUR), "receipt_ttl_policy_exceeded"],
    ["observed_at", "2026-09-10T15:00:00.000Z", "receipt_observed_after_reference"],
    ["evaluator_identity", I("maker"), "self_attestation"],
    ["producer_identity", I("maker"), "self_attestation"],
    ["producer_role", "independent_journey_one_outcome_oracle", "receipt_oracle_mismatch"],
    ["oracle_version", "1.1.0", "receipt_oracle_mismatch"],
    ["comparator", "x", "invalid_comparator"],
  ];
  for (const [key, value, code] of cases) { const p = snapshot(); p.completion = completed(ORIGIN); p.completion.receipts[0][key] = value; refuse(p, code); }
  const lapsed = snapshot("2026-09-11T15:00:00.000Z");
  lapsed.completion = completed(ORIGIN); refuse(lapsed, "receipt_not_current");
});

// --- pauses: actual elapsed hours, capped, approved in advance --------------

test("actual elapsed pauses union overlapping windows and clip ongoing time", () => {
  const p = snapshot("2026-09-11T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", "2026-09-11T03:00:00.000Z"),
    pause(p, "2026-09-10T21:00:00.000Z", null, "two", "dell")];
  const r = run(p); assert.equal(r.state.paused_ms, 24 * HOUR); assert.equal(r.state.due_at, "2026-10-10T15:00:00.000Z");
});

test("pause counts actual DST elapsed hours rather than calendar dates", () => {
  const origin = "2026-02-20T16:00:00.000Z", p = snapshot("2026-03-09T05:00:00.000Z", origin);
  p.pauses = [pause(p, "2026-03-08T06:00:00.000Z", "2026-03-09T05:00:00.000Z")];
  assert.equal(run(p).state.paused_ms, 23 * HOUR);
});

test("pause cap is 120 actual hours, even for an ongoing long pause", () => {
  const p = snapshot("2026-10-20T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", null)];
  const r = run(p); assert.equal(r.state.paused_ms, 120 * HOUR); assert.equal(r.state.due_at, "2026-10-14T15:00:00.000Z"); assert.equal(r.state.status, "missed");
  assert.equal(JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours, 120);
});

test("two separate pauses are capped on their total, not each", () => {
  const p = snapshot("2026-10-20T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", "2026-09-14T15:00:00.000Z"),
    pause(p, "2026-09-20T15:00:00.000Z", "2026-09-27T15:00:00.000Z", "two", "dell")];
  assert.equal(run(p).state.paused_ms, 120 * HOUR);
});

test("a future pause gets no unelapsed credit and a post-deadline pause cannot rescue a miss", () => {
  const p = snapshot(); p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", null)];
  p.pauses[0].approved_at = ORIGIN; resignPause(p.pauses[0]); assert.equal(run(p).state.paused_ms, 0);
  const q = snapshot("2026-10-11T15:00:00.000Z"); q.pauses = [pause(q, "2026-10-10T15:00:00.000Z", null)];
  const r = run(q); assert.equal(r.state.paused_ms, 0); assert.equal(r.state.status, "missed");
});

test("pause prior approval must be Joe or Dell and cannot be backdated", () => {
  for (const mode of ["at_start", "after_start", "before_origin", "agent", "wrong_hash", "wrong_origin", "not_external"]) {
    const p = snapshot("2026-09-11T15:00:00.000Z"); const a = pause(p, "2026-09-10T15:00:00.000Z", null);
    if (mode === "at_start") a.approved_at = a.starts_at;
    if (mode === "after_start") a.approved_at = "2026-09-10T16:00:00.000Z";
    if (mode === "before_origin") a.approved_at = "2026-09-09T14:00:00.000Z";
    if (mode === "agent") a.approved_by_identity = I("model");
    if (mode === "wrong_origin") a.clock_origin_digest = D(99);
    if (mode === "not_external") a.blocker_ref = "safe:internal:build-delay";
    resignPause(a); if (mode === "wrong_hash") a.approval_digest = D(99); p.pauses = [a];
    refuse(p, { at_start: "pause_backdating_or_order", after_start: "pause_backdating_or_order",
      before_origin: "pause_backdating_or_order", agent: "verified_partner_required",
      wrong_hash: "pause_approval_digest_mismatch", wrong_origin: "pause_origin_mismatch", not_external: "invalid_reference" }[mode]);
  }
});

test("a duplicate pause id refuses rather than double-counting", () => {
  const p = snapshot("2026-09-11T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", "2026-09-10T18:00:00.000Z"),
    pause(p, "2026-09-10T19:00:00.000Z", "2026-09-10T21:00:00.000Z", "one", "dell")];
  refuse(p, "duplicate_pause");
});

// --- miss, replan, and an append-only ledger --------------------------------

test("miss requires replan, survives late completion and never deadlocks safe construction", () => {
  const p = snapshot("2026-10-10T15:00:00.000Z"), missed = run(p);
  assert.equal(missed.replan_required, true); assert.equal(missed.safe_construction_may_continue, true);
  p.history = missed.state; p.as_of = "2026-10-11T15:00:00.000Z"; p.completion = completed(p.as_of);
  const late = run(p); assert.equal(late.state.status, "completed_late"); assert.equal(late.deadline_success, false); assert.equal(late.completion_currently_usable, true);
  assert.deepEqual(late.state.events.slice(0, missed.state.events.length), missed.state.events);
  p.history = late.state; p.completion = null; p.as_of = "2026-10-12T15:00:00.000Z";
  const stale = run(p); assert.equal(stale.state.status, "completed_late"); assert.equal(stale.completion_currently_usable, false); assert.equal(stale.replan_required, true);
});

test("later report of an earlier pass cannot erase an already recorded miss", () => {
  const p = snapshot("2026-10-09T15:00:00.001Z"); p.history = run(p).state;
  p.completion = completed("2026-10-09T15:00:00.000Z");
  const r = run(p); assert.equal(r.state.status, "completed_late"); assert.equal(r.deadline_success, false);
});

test("a second, different terminus receipt cannot replace a recorded completion", () => {
  const p = snapshot("2026-10-01T15:00:00.000Z"); p.completion = completed(p.as_of);
  p.history = run(p).state; p.as_of = "2026-10-02T15:00:00.000Z";
  p.completion = completed(p.as_of); refuse(p, "completion_history_replacement");
});

test("later minimum cannot replace an immutable origin", () => {
  const p = snapshot(); p.history = run(p).state; p.as_of = "2026-09-10T15:00:00.000Z";
  p.minimum_history = [{ admitted_at: p.as_of, receipt: minimum(p.as_of) }]; refuse(p, "origin_reset_or_rebase");
});

test("an earlier approval reported after a later event appends instead of deadlocking the ledger", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T10:00:00.000Z", "2026-09-10T12:00:00.000Z", "late", "joe", "2026-09-10T09:00:00.000Z")];
  const before = run(p);
  assert.equal(before.state.events.at(-1).type, "pause_approved");
  p.history = before.state; p.as_of = "2026-09-11T15:00:00.000Z";
  p.pauses.push(pause(p, "2026-09-10T06:00:00.000Z", "2026-09-10T08:00:00.000Z", "earlier", "dell", "2026-09-10T05:00:00.000Z"));
  const after = run(p);
  assert.equal(after.state.paused_ms, 4 * HOUR);
  assert.deepEqual(after.state.events.slice(0, before.state.events.length), before.state.events);
  const appended = after.state.events.at(-1), previous = after.state.events.at(-2);
  assert.equal(appended.type, "pause_approved");
  assert.equal(appended.at, "2026-09-10T05:00:00.000Z");
  assert.equal(Date.parse(appended.at) < Date.parse(previous.at), true);
  assert.equal(Date.parse(appended.recorded_at) > Date.parse(previous.recorded_at), true);
  assert.equal(appended.previous_event_digest, previous.event_digest);
});

test("tampering with history hashes or deleting a recorded fact refuses", () => {
  const p = snapshot("2026-10-10T15:00:00.000Z"); p.history = copy(run(p).state);
  p.history.origin_at = "2026-09-10T15:00:00.000Z"; refuse(p, "corrupt_history");
  const missed = copy(run({ ...p, history: null }).state);
  p.history = reseal({ ...copy(missed), miss_at: null }); refuse(p, "erased_miss_history");
  p.history = reseal({ ...copy(missed), paused_ms: 121 * HOUR }); refuse(p, "corrupt_history");
  p.history = reseal({ ...copy(missed), status: "on_time" }); refuse(p, "corrupt_history");
  p.history = reseal({ ...copy(missed), events: missed.events.slice(0, 1) }); refuse(p, "erased_miss_history");
  const tampered = copy(missed);
  tampered.events.at(-1).evidence_digest = D(99); p.history = reseal(tampered); refuse(p, "corrupt_history");

  const q = snapshot("2026-10-01T15:00:00.000Z"); q.completion = completed(q.as_of);
  const done = copy(run(q).state); q.as_of = "2026-10-02T15:00:00.000Z";
  q.history = reseal({ ...copy(done), completion_receipt_digest: null, completion_observed_at: null });
  refuse(q, "erased_completion_history");
  q.history = reseal({ ...copy(done), completion_observed_at: "2026-10-01T14:00:00.000Z" });
  refuse(q, "erased_completion_history");
});

test("previous pause approvals cannot vanish; ongoing pause may end only without rewriting accounted time", () => {
  const p = snapshot("2026-09-11T15:00:00.000Z"); p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", null)];
  p.history = run(p).state;
  const omitted = copy(p); omitted.pauses = []; refuse(omitted, "erased_approval_history");
  p.as_of = "2026-09-12T15:00:00.000Z"; p.pauses[0].ends_at = "2026-09-11T14:59:59.999Z"; refuse(p, "pause_history_rewritten");
  p.pauses[0].ends_at = "2026-09-12T03:00:00.000Z";
  assert.equal(run(p).state.paused_ms, 36 * HOUR);
});

test("a previously recorded amendment cannot vanish from a later inventory", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z"); const before = run(p);
  p.amendments = [amend(before.state.origin_receipt_digest, p.as_of)]; p.history = before.state;
  p.history = run(p).state; p.as_of = "2026-09-11T15:00:00.000Z";
  const omitted = copy(p); omitted.amendments = []; refuse(omitted, "erased_approval_history");
  assert.equal(run(p).state.events.filter(e => e.type === "amendment_recorded").length, 1);
});

// --- shape, time and effect boundaries -------------------------------------

for (const value of ["2026-02-30T12:00:00Z", "2026-09-09", "2026-09-09T12:00:00", "2026-09-09T24:00:00Z", "2026-09-09T12:00:00.0001Z"]) {
  test(`invalid or unsupported precision timestamp refuses: ${value}`, () => assert.throws(() => chicagoThirtyDayDeadline(value), e => e.code === "invalid_timestamp"));
}

test("JSON verification flags cannot authenticate a snapshot", () => {
  assert.throws(() => createJourneyOneClock(), e => e.code === "authenticated_verifier_required");
  assert.throws(() => harness().clock.evaluate({ verified: true, accepted: true, snapshot: snapshot() }), /synthetic-authentication-refused/);
  const clock = createJourneyOneClock({ verifySnapshot: () => true });
  assert.throws(() => clock.evaluate({}), e => e.code === "invalid_object");
  const wrong = createJourneyOneClock({ verifySnapshot: () => ({ envelope_digest: D(99), snapshot: snapshot() }) });
  assert.throws(() => wrong.evaluate({}), e => e.code === "verification_binding_mismatch");
});

test("closed projections, hidden fields, mutation and side effects remain bounded", () => {
  const p = snapshot(); p.reset = true; refuse(p, "closed_shape");
  const q = snapshot(); q.minimum_history[0].receipt.authorized = true; refuse(q, "closed_shape");
  const t = snapshot(); t.tenant = "other-tenant"; refuse(t, "wrong_projection_or_tenant");
  const before = copy(snapshot()), r = run(before); assert.deepEqual(before, snapshot());
  assert.equal(Object.isFrozen(r.state.events), true); assert.equal(r.authority_granted, false);
  assert.equal(r.effects.database_writes, 0); assert.equal(r.effects.provider_actions, 0); assert.equal(r.durable_history_write_required, true);
  assert.throws(() => harness().clock.evaluate(Object.defineProperty({}, "hidden", { get() { throw new Error("must not invoke"); } })), e => e.code === "hidden_key");
});
