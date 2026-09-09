// V5-M01 pure clock kernel for decision 66427d3b (Q008.D1). This is NOT a
// receipt issuer, gateway, clock, persistence layer, or live acceptance path. A
// separately installed trusted verifier authenticates the complete envelope,
// authoritative history/inventory, exact benchmark acceptance, identities, and
// policy-derived bindings. Ordinary request JSON cannot install that verifier or
// assert verification with a flag. The projection below is an internal adapter
// seam, not a new approval policy: a projection is evidence that the verifier
// read the record, never evidence that a raw producer admitted anything.
//
// WHAT REMAINS INTEGRATION WORK, stated rather than implied:
//   * Raw-schema admission, authentication and durable append/CAS adapters.
//     Returning a history does not claim it was persisted; the caller owns the
//     durable append, which is why every result carries
//     durable_history_write_required.
//   * The terminus accepts exactly one rollout-component-receipt.v1 and checks
//     `all_current_exact_distinct_pass` as an exact contract string rather than
//     re-implementing it, so "distinct" does no work at arity one.
//   * An amendment applies to a clock that already has history. The first
//     evaluation must present the pre-origin benchmark, because there is no
//     recorded original for an amendment to preserve yet.
// No live clock is read anywhere: every instant comes from the verified `as_of`.
// Chicago wall time requires a full-ICU Node build.
import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, isKnownPartner } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";

export const JOURNEY_ONE_CLOCK_SCHEMA = "doctorcre-v5-journey-one-clock.v1";
export const JOURNEY_ONE_CLOCK_PROJECTION = "doctorcre-v5-journey-one-clock-projection.v1";
export const JOURNEY_ONE_CLOCK_RULE_REF =
  "native-task:01a0869f-fe0d-7493-bda3-ab8b3c0d6683:user-turn:01a086d1-2f70-7a73-b0ea-14e68da841ca";
export const JOURNEY_ONE_DEADLINE_CONTRACT = Object.freeze({
  timezone: "America/Chicago", calendar_days: 30,
  clock_origin_gate_id: "foundation-assurance-minimum-accepted",
  clock_origin_rule: "observed_at of the first current passing foundation-assurance-minimum receipt that makes Journey 1 admissible",
  // Hours, not days: the pause budget is actual elapsed time, and a day is the
  // one unit this file exists to stop anybody from assuming is 24 hours long.
  maximum_external_blocker_pause_hours: 120,
  reset_policy: "never_reset_or_rebase_elapsed_history",
  amendment_policy: "verified_partner_exact_hash_amendment_preserves_original_origin_and_elapsed_history",
  clock_terminus_gate_id: "journey-one-kernel-production-accepted",
  kernel_obligation_decision_ids: Object.freeze(["Q002.D1", "Q014.D1", "Q123.D1"]),
  miss_consequence: "mark_deadline_missed_require_replan_preserve_origin_and_elapsed_continue_safe_construction_without_claiming_deadline_success",
});
const HOUR = 3600000;
const CAP = JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours * HOUR;
const IDENTITY = ["actor_id", "session_ref", "authority_class"];
const COMMON_RECEIPT = ["schema_version", "subject_digest", "candidate_digest", "policy_digest",
  "subject_environment", "evidence_scope", "subject_maker_identity", "producer_identity",
  "evaluator_identity", "producer_role", "independent_oracle_ref", "oracle_version",
  "evidence_ref", "fixture_set_digest", "observed_at", "ttl_expires_at", "status",
  "comparator", "negative_admission_result"];
const MINIMUM = [...COMMON_RECEIPT, "gate_id", "receipt_producer_step_ref", "environment_manifest_digest"];
const COMPLETION = [...COMMON_RECEIPT, "receipt_ref", "producer_step_ref", "rollout_environment_manifest_digest", "artifact_digest"];
const STATE = ["schema_version", "origin_receipt_digest", "origin_at",
  "origin_benchmark_manifest_digest", "current_benchmark_manifest_digest",
  "base_deadline_at", "due_at", "paused_ms", "status", "miss_at", "completion_receipt_digest",
  "completion_observed_at", "evaluated_at", "pause_intervals", "events", "history_digest"];
const EVENT_KEYS = ["type", "at", "recorded_at", "evidence_digest", "previous_event_digest", "event_digest"];
const EVENT_TYPES = ["clock_started", "pause_approved", "amendment_recorded", "deadline_missed", "completion_observed"];
const STATUSES = ["running", "missed", "completed_on_time", "completed_late",
  "unresolved_deadline", "completed_unresolved_deadline"];
const PAUSE_KEYS = ["pause_id", "clock_origin_digest", "blocker_ref", "starts_at", "ends_at",
  "approved_at", "approved_by_identity", "approval_digest"];
const AMENDMENT_KEYS = ["amendment_ref", "clock_origin_digest", "benchmark_manifest_digest",
  "description_digest", "accepted_at", "accepted_by_identity", "amendment_digest"];
// A recorded attempt that simply did not pass, or was no longer current when it
// was admitted, is an ordinary fact of the ledger: it can never become the
// origin, and it must not make the clock permanently uncomputable. Every other
// refusal below stays fatal, because a misbound or malformed receipt in an
// authoritative inventory means the projection itself is wrong.
const INADMISSIBLE = new Set(["nonpassing_receipt", "receipt_not_current", "receipt_ttl_policy_exceeded"]);

export class JourneyOneClockError extends Error {
  constructor(code, detail) { super(code); this.name = "JourneyOneClockError"; this.code = code; this.detail = detail; }
}
function fail(code, detail) { throw new JourneyOneClockError(code, detail); }
function json(value, path = "input") {
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) fail("invalid_unicode", path);
    return;
  }
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (typeof value !== "object" || value === null) fail("not_json", path);
  if (!Array.isArray(value) && ![Object.prototype, null].includes(Object.getPrototypeOf(value))) fail("invalid_object", path);
  if (Object.getOwnPropertySymbols(value).length) fail("hidden_key", path);
  for (const key of Object.getOwnPropertyNames(value)) {
    if (Array.isArray(value) && key === "length") continue;
    const d = Object.getOwnPropertyDescriptor(value, key);
    if (d.get || d.set || !d.enumerable || key === "__proto__") fail("hidden_key", path);
    json(key, path); json(d.value, `${path}.${key}`);
  }
  if (Array.isArray(value) && Object.keys(value).length !== value.length) fail("sparse_array", path);
}
function closed(value, keys, path) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid_object", path);
  if (Object.keys(value).length !== keys.length || keys.some(k => !Object.hasOwn(value, k))) fail("closed_shape", path);
  return value;
}
function ref(value, prefix = "safe:") {
  if (typeof value !== "string" || !value.startsWith(prefix) || !/^[a-zA-Z0-9:._/-]{3,300}$/.test(value)) fail("invalid_reference", value);
}
function hash(value) { if (typeof value !== "string" || !/^sha256:[a-f0-9]{64}$/.test(value)) fail("invalid_digest", value); }
function freeze(v) { if (v && typeof v === "object") { Object.values(v).forEach(freeze); Object.freeze(v); } return v; }
function copy(v) { return JSON.parse(JSON.stringify(v)); }
function same(a, b, code) { if (digest(a) !== digest(b)) fail(code); }
function order(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
function stamp(value) {
  const m = typeof value === "string" && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!m) fail("invalid_timestamp", value);
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number);
  const date = new Date(0); date.setUTCFullYear(y, mo - 1, d); date.setUTCHours(h, mi, s, 0);
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d || h > 23 || mi > 59 || s > 59 ||
      (m[8] !== "Z" && (Number(m[8].slice(1, 3)) > 23 || Number(m[8].slice(4)) > 59))) fail("invalid_timestamp", value);
  const ms = Date.parse(value); if (!Number.isSafeInteger(ms)) fail("invalid_timestamp", value);
  return ms;
}
const iso = ms => new Date(ms).toISOString();
const format = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", hourCycle: "h23",
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
function wall(ms) {
  const p = Object.fromEntries(format.formatToParts(ms).map(x => [x.type, x.value]));
  const date = new Date(0);
  date.setUTCFullYear(Number(p.year), Number(p.month) - 1, Number(p.day));
  date.setUTCHours(Number(p.hour), Number(p.minute), Number(p.second), ((ms % 1000) + 1000) % 1000);
  return date.getTime();
}
/** Same Chicago wall time 30 dates later. Ambiguous/nonexistent target refuses resolution. */
export function chicagoThirtyDayDeadline(origin) {
  const start = stamp(origin);
  const targetDate = new Date(wall(start)); targetDate.setUTCDate(targetDate.getUTCDate() + 30);
  const target = targetDate.getTime();
  const offsets = new Set();
  for (let h = -48; h <= 48; h += 6) { const probe = target + h * HOUR; offsets.add(wall(probe) - probe); }
  const candidates = [...offsets].map(offset => target - offset).filter(ms => wall(ms) === target);
  return freeze({ status: candidates.length === 1 ? "resolved" : "unresolved_deadline",
    due_at: candidates.length === 1 ? iso(candidates[0]) : null,
    reason_id: candidates.length === 0 ? "nonexistent_chicago_wall_time" : candidates.length > 1 ? "ambiguous_chicago_wall_time" : "same_chicago_wall_time_after_30_dates" });
}
function identity(value, partner = false) {
  closed(value, IDENTITY, "identity"); ref(value.session_ref, "session:");
  if (typeof value.actor_id !== "string" || !value.actor_id || typeof value.authority_class !== "string" || !value.authority_class) fail("invalid_identity");
  if (partner && (!isKnownPartner(value.actor_id) || value.authority_class !== "verified_partner")) fail("verified_partner_required");
}
/**
 * One receipt, read against the exact producer contract it claims, as of `at`.
 * `at` is the instant the receipt has to have been current for: the historical
 * admission instant for a minimum, the evaluation instant for the terminus.
 */
function receipt(r, completion, binding, at) {
  closed(r, completion ? COMPLETION : MINIMUM, "receipt");
  const step = completion ? "step:j1-kernel-production-outcome" : "step:foundation-assurance-minimum-receipt";
  if (r.schema_version !== (completion ? "rollout-component-receipt.v1" : "consumer-gate-receipt.v1") ||
      (completion ? r.producer_step_ref : r.receipt_producer_step_ref) !== step) fail("wrong_producer_or_schema");
  if (completion) {
    if (r.receipt_ref !== "safe:receipt:journey-one-kernel-production") fail("wrong_terminus_receipt");
    hash(r.artifact_digest);
  } else if (r.gate_id !== JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id) fail("wrong_origin_gate");
  for (const k of ["subject_digest", "candidate_digest", "policy_digest"]) {
    hash(r[k]); if (r[k] !== binding[k]) fail("receipt_binding_mismatch", k);
  }
  const environment = completion ? r.rollout_environment_manifest_digest : r.environment_manifest_digest;
  if (environment !== binding[completion ? "production_environment_manifest_digest" : "minimum_environment_manifest_digest"]) fail("receipt_environment_mismatch");
  hash(r.fixture_set_digest); ref(r.evidence_ref);
  if (r.evidence_scope !== (completion ? "production" : "candidate-and-test") ||
      r.subject_environment !== (completion ? "production" : "candidate")) fail("receipt_scope_mismatch");
  if (r.producer_role !== (completion ? "independent_journey_one_kernel_outcome_oracle" : "independent_foundation_assurance_minimum_oracle") ||
      r.independent_oracle_ref !== (completion ? "oracle:rollout-component:journey-one-kernel-production" : "oracle:gate-producer:foundation-assurance-minimum") ||
      r.oracle_version !== "1.0.0") fail("receipt_oracle_mismatch");
  for (const k of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) identity(r[k]);
  // Independence is against the SUBJECT MAKER, and both of the other seats are
  // checked: a producer that is the maker attests to its own work exactly as
  // much as an evaluator that is.
  for (const k of ["producer_identity", "evaluator_identity"]) {
    if (r.subject_maker_identity.actor_id === r[k].actor_id ||
        r.subject_maker_identity.session_ref === r[k].session_ref) fail("self_attestation");
  }
  if (r.status !== "pass" || r.negative_admission_result !== "all_required_denials_observed") fail("nonpassing_receipt");
  if (typeof r.comparator !== "string" || r.comparator.length < 5 || r.comparator.length > 300) fail("invalid_comparator");
  const observed = stamp(r.observed_at), expires = stamp(r.ttl_expires_at);
  if (observed > at) fail("receipt_observed_after_reference");
  if (expires <= observed) fail("invalid_receipt_window");
  if (expires - observed > binding.maximum_receipt_ttl_ms) fail("receipt_ttl_policy_exceeded");
  if (expires <= at) fail("receipt_not_current");
  return observed;
}
/**
 * Append one event. `at` is when the fact happened; `recorded_at` is when this
 * kernel first saw it. The chain is ordered on recorded_at, never on `at`, so a
 * legitimately earlier approval reported late appends honestly instead of
 * deadlocking every later evaluation of the ledger.
 */
function event(state, type, at, recordedAt, evidence) {
  if (state.events.some(e => e.type === type && e.evidence_digest === evidence)) return;
  if (at > recordedAt) fail("event_recorded_before_it_happened", type);
  const previous = state.events.at(-1);
  if (previous && recordedAt < stamp(previous.recorded_at)) fail("history_time_reversed");
  const data = { type, at: iso(at), recorded_at: iso(recordedAt), evidence_digest: evidence,
    previous_event_digest: previous?.event_digest ?? null };
  state.events.push({ ...data, event_digest: digest(data) });
}
function readHistory(history, now) {
  if (history === null) return null;
  closed(history, STATE, "history");
  const { history_digest, ...body } = history;
  if (history.schema_version !== JOURNEY_ONE_CLOCK_SCHEMA || digest(body) !== history_digest) fail("corrupt_history");
  hash(history.origin_receipt_digest);
  hash(history.origin_benchmark_manifest_digest); hash(history.current_benchmark_manifest_digest);
  const origin = stamp(history.origin_at), evaluated = stamp(history.evaluated_at);
  if (origin > now || evaluated > now || evaluated < origin) fail("history_time_reversed");
  if ((history.base_deadline_at === null) !== (history.due_at === null)) fail("corrupt_history");
  if (history.base_deadline_at !== null && stamp(history.due_at) < stamp(history.base_deadline_at)) fail("corrupt_history");
  if (!Number.isSafeInteger(history.paused_ms) || history.paused_ms < 0 || history.paused_ms > CAP) fail("corrupt_history");
  if (!STATUSES.includes(history.status)) fail("corrupt_history");
  if (history.miss_at !== null) stamp(history.miss_at);
  if ((history.completion_receipt_digest === null) !== (history.completion_observed_at === null)) fail("corrupt_history");
  if (history.completion_receipt_digest !== null) {
    hash(history.completion_receipt_digest);
    if (stamp(history.completion_observed_at) < origin) fail("corrupt_history");
  }
  if (!Array.isArray(history.pause_intervals)) fail("corrupt_history");
  const pauseIds = new Set();
  for (const interval of history.pause_intervals) {
    closed(interval, ["pause_id", "ends_at"], "history.pause_interval");
    ref(interval.pause_id);
    if (interval.ends_at !== null) stamp(interval.ends_at);
    if (pauseIds.has(interval.pause_id)) fail("corrupt_history"); pauseIds.add(interval.pause_id);
  }
  if (!Array.isArray(history.events) || !history.events.length) fail("corrupt_history");
  let previous = null, lastRecorded = -Infinity;
  for (const e of history.events) {
    closed(e, EVENT_KEYS, "history.event");
    const { event_digest, ...data } = e; hash(e.evidence_digest);
    const at = stamp(e.at), recorded = stamp(e.recorded_at);
    if (!EVENT_TYPES.includes(e.type) || data.previous_event_digest !== previous ||
        digest(data) !== event_digest || at > recorded || recorded < lastRecorded || recorded > now) fail("corrupt_history");
    previous = event_digest; lastRecorded = recorded;
  }
  if (history.events[0].type !== "clock_started" || history.events[0].evidence_digest !== history.origin_receipt_digest ||
      stamp(history.events[0].at) !== origin) fail("corrupt_history");
  if ((history.miss_at !== null) !== history.events.some(e => e.type === "deadline_missed")) fail("erased_miss_history");
  const recordedCompletion = history.events.find(e => e.type === "completion_observed") ?? null;
  if ((history.completion_receipt_digest !== null) !== (recordedCompletion !== null)) fail("erased_completion_history");
  if (recordedCompletion && (recordedCompletion.evidence_digest !== history.completion_receipt_digest ||
      recordedCompletion.at !== history.completion_observed_at)) fail("erased_completion_history");
  return copy(history);
}
/** Dependency installation is trusted server code, never a caller tool argument. */
export function createJourneyOneClock({ verifySnapshot } = {}) {
  if (typeof verifySnapshot !== "function") fail("authenticated_verifier_required");
  return Object.freeze({ evaluate(envelope) {
    json(envelope);
    const input = freeze(copy(envelope));
    const verified = verifySnapshot(input);
    json(verified);
    closed(verified, ["envelope_digest", "snapshot"], "verification");
    if (verified.envelope_digest !== digest(input)) fail("verification_binding_mismatch");
    const p = copy(verified.snapshot);
    closed(p, ["schema_version", "tenant", "as_of", "binding", "benchmark", "minimum_history", "completion", "pauses", "amendments", "history"], "snapshot");
    if (p.schema_version !== JOURNEY_ONE_CLOCK_PROJECTION || p.tenant !== ORGANIZATION_TENANT_ID) fail("wrong_projection_or_tenant");
    const now = stamp(p.as_of), b = p.binding;
    closed(b, ["subject_digest", "candidate_digest", "policy_digest", "minimum_environment_manifest_digest", "production_environment_manifest_digest", "maximum_receipt_ttl_ms"], "binding");
    for (const [key, value] of Object.entries(b)) if (key !== "maximum_receipt_ttl_ms") hash(value);
    if (!Number.isSafeInteger(b.maximum_receipt_ttl_ms) || b.maximum_receipt_ttl_ms <= 0) fail("invalid_receipt_ttl_policy");
    const benchmark = p.benchmark;
    closed(benchmark, ["manifest_digest", "subject_digest", "candidate_digest", "policy_digest", "deadline_contract", "accepted_at", "accepted_by_identity"], "benchmark");
    hash(benchmark.manifest_digest); identity(benchmark.accepted_by_identity, true);
    same(benchmark.deadline_contract, JOURNEY_ONE_DEADLINE_CONTRACT, "wrong_deadline_contract");
    for (const k of ["subject_digest", "candidate_digest", "policy_digest"]) if (benchmark[k] !== b[k]) fail("benchmark_binding_mismatch");

    // ORIGIN. The first admissible current passing minimum, by its own
    // observed_at — not by inventory order, and not by whether its TTL has since
    // lapsed. Ties break on the receipt digest so two receipts sharing an
    // observed_at cannot make the answer depend on array order.
    if (!Array.isArray(p.minimum_history) || !p.minimum_history.length) fail("origin_unavailable");
    let first = null, inadmissible = 0; const seen = new Set();
    for (const admission of p.minimum_history) {
      closed(admission, ["admitted_at", "receipt"], "minimum_admission");
      const admitted = stamp(admission.admitted_at);
      if (admitted > now) fail("future_admission");
      const receiptDigest = digest(admission.receipt);
      if (seen.has(receiptDigest)) fail("duplicate_minimum"); seen.add(receiptDigest);
      let observed;
      try { observed = receipt(admission.receipt, false, b, admitted); }
      catch (e) {
        if (e instanceof JourneyOneClockError && INADMISSIBLE.has(e.code)) { inadmissible += 1; continue; }
        throw e;
      }
      if (!first || observed < first.observed ||
          (observed === first.observed && receiptDigest < first.receiptDigest)) first = { observed, receiptDigest };
    }
    if (!first) fail("origin_unavailable", { inadmissible_admissions: inadmissible });

    const old = readHistory(p.history, now);
    const base = chicagoThirtyDayDeadline(iso(first.observed));
    if (old && (old.origin_receipt_digest !== first.receiptDigest || stamp(old.origin_at) !== first.observed ||
        old.base_deadline_at !== base.due_at)) fail("origin_reset_or_rebase");
    const state = old ?? { schema_version: JOURNEY_ONE_CLOCK_SCHEMA,
      origin_receipt_digest: first.receiptDigest, origin_at: iso(first.observed),
      origin_benchmark_manifest_digest: benchmark.manifest_digest,
      current_benchmark_manifest_digest: benchmark.manifest_digest,
      base_deadline_at: base.due_at, due_at: base.due_at, paused_ms: 0, status: "running", miss_at: null,
      completion_receipt_digest: null, completion_observed_at: null, evaluated_at: iso(now), pause_intervals: [], events: [] };
    if (!old) event(state, "clock_started", first.observed, now, first.receiptDigest);
    const pending = [];
    if (!Array.isArray(p.pauses) || !Array.isArray(p.amendments)) fail("invalid_inventory");

    // AMENDMENTS. An amendment records a partner-signed change and may authorize
    // one exact replacement benchmark manifest. It never moves the origin, the
    // base deadline or a recorded event; that is the whole of its power here.
    const amendedManifests = new Map(), amendmentRefs = new Set();
    for (const amendment of p.amendments) {
      closed(amendment, AMENDMENT_KEYS, "amendment");
      ref(amendment.amendment_ref); hash(amendment.description_digest); identity(amendment.accepted_by_identity, true);
      if (amendment.benchmark_manifest_digest !== null) hash(amendment.benchmark_manifest_digest);
      const { amendment_digest, ...payload } = amendment;
      if (amendment_digest !== digest(["doctorcre:j1-clock-amendment:v1", payload])) fail("amendment_digest_mismatch");
      if (amendmentRefs.has(amendment.amendment_ref)) fail("duplicate_amendment"); amendmentRefs.add(amendment.amendment_ref);
      const accepted = stamp(amendment.accepted_at);
      if (amendment.clock_origin_digest !== first.receiptDigest || accepted < first.observed || accepted > now) fail("amendment_reset_or_time");
      if (amendment.benchmark_manifest_digest !== null) amendedManifests.set(amendment.benchmark_manifest_digest, amendment_digest);
      pending.push(["amendment_recorded", accepted, amendment_digest]);
    }
    // BENCHMARK ACCEPTANCE. The originating benchmark must have been accepted by
    // an exact verified partner strictly BEFORE the origin pass. Any other
    // manifest is a replacement, and a replacement is legitimate only when an
    // exact-hash partner amendment names it.
    // Future-dating is refused FIRST and the order is load-bearing. The origin
    // never postdates `as_of`, so on the originating-manifest path every
    // future-dated acceptance is also at-or-after the origin; testing lateness
    // first would report the weaker refusal and leave the future-dated case
    // unreachable there. A replacement reaches the future check on its own.
    const originManifest = old ? old.origin_benchmark_manifest_digest : benchmark.manifest_digest;
    const acceptedAt = stamp(benchmark.accepted_at);
    if (acceptedAt > now) fail("benchmark_accepted_in_the_future");
    if (benchmark.manifest_digest === originManifest) {
      if (acceptedAt >= first.observed) fail("benchmark_not_accepted_before_origin");
    } else if (!amendedManifests.has(benchmark.manifest_digest)) fail("unamended_benchmark_replacement");
    state.current_benchmark_manifest_digest = benchmark.manifest_digest;

    const intervals = [], pauseIds = new Set();
    for (const pause of p.pauses) {
      closed(pause, PAUSE_KEYS, "pause");
      ref(pause.pause_id); ref(pause.blocker_ref, "safe:external-blocker:"); identity(pause.approved_by_identity, true);
      const { approval_digest, ends_at, ...payload } = pause;
      if (approval_digest !== digest(["doctorcre:j1-clock-pause:v1", payload])) fail("pause_approval_digest_mismatch");
      if (pauseIds.has(pause.pause_id)) fail("duplicate_pause"); pauseIds.add(pause.pause_id);
      if (pause.clock_origin_digest !== state.origin_receipt_digest) fail("pause_origin_mismatch");
      const start = stamp(pause.starts_at), approved = stamp(pause.approved_at);
      const end = pause.ends_at === null ? now : stamp(pause.ends_at);
      // Approval is by Joe or Dell, strictly before the pause starts, never
      // backdated before the clock itself, and never dated ahead of the record.
      if (approved >= start || approved > now || approved < first.observed || start < first.observed ||
          (pause.ends_at !== null && end < start)) fail("pause_backdating_or_order");
      const priorPause = state.pause_intervals.find(x => x.pause_id === pause.pause_id);
      if (priorPause && ((priorPause.ends_at !== null && priorPause.ends_at !== pause.ends_at) ||
          (priorPause.ends_at === null && pause.ends_at !== null && end < stamp(state.evaluated_at)))) fail("pause_history_rewritten");
      intervals.push([start, Math.min(end, now)]);
      pending.push(["pause_approved", approved, approval_digest]);
    }
    // Complete inventories are verifier-owned. Previously recorded approvals
    // cannot disappear or mutate when an untrusted caller supplies a new view.
    for (const e of state.events.filter(e => ["pause_approved", "amendment_recorded"].includes(e.type))) {
      if (!pending.some(([type, , d]) => type === e.type && d === e.evidence_digest)) fail("erased_approval_history");
    }
    let completion = null;
    if (p.completion !== null) {
      closed(p.completion, ["gate_id", "combiner", "obligation_decision_ids", "receipts"], "completion");
      if (p.completion.gate_id !== JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id || p.completion.combiner !== "all_current_exact_distinct_pass") fail("wrong_terminus_gate");
      same(p.completion.obligation_decision_ids, JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids, "wrong_kernel_obligations");
      if (!Array.isArray(p.completion.receipts) || p.completion.receipts.length !== 1) fail("missing_or_duplicate_kernel_receipt");
      const r = p.completion.receipts[0], observed = receipt(r, true, b, now);
      if (observed < first.observed) fail("completion_before_origin");
      completion = { observed, receiptDigest: digest(r) };
      if (state.completion_receipt_digest !== null && state.completion_receipt_digest !== completion.receiptDigest) fail("completion_history_replacement");
    }
    // Completion is a FACT and is recorded whether or not the deadline resolved.
    // Only the on-time judgement depends on a resolvable deadline.
    if (completion && state.completion_receipt_digest === null) {
      state.completion_receipt_digest = completion.receiptDigest;
      state.completion_observed_at = iso(completion.observed);
      pending.push(["completion_observed", completion.observed, completion.receiptDigest]);
    }
    const stop = state.completion_observed_at !== null ? stamp(state.completion_observed_at) : now;
    const union = [];
    for (const [start, rawEnd] of intervals.sort((a, b) => a[0] - b[0])) {
      const end = Math.min(rawEnd, stop);
      if (end <= start) continue;
      if (union.length && start <= union.at(-1)[1]) union.at(-1)[1] = Math.max(union.at(-1)[1], end);
      else union.push([start, end]);
    }
    let pauseMs = 0;
    if (base.due_at !== null) {
      const baseMs = stamp(base.due_at);
      for (const [start, end] of union) {
        // A pause beginning after an already missed deadline cannot revive it.
        if (start <= baseMs + pauseMs) pauseMs = Math.min(CAP, pauseMs + end - start);
      }
      state.paused_ms = pauseMs; state.due_at = iso(baseMs + pauseMs);
      if (state.miss_at === null && stop > baseMs + pauseMs) {
        state.miss_at = state.due_at;
        pending.push(["deadline_missed", stamp(state.miss_at), digest(["deadline_missed", state.due_at, state.origin_receipt_digest])]);
      }
      state.status = state.completion_receipt_digest !== null
        ? (state.miss_at === null ? "completed_on_time" : "completed_late")
        : (state.miss_at === null ? "running" : "missed");
    } else {
      for (const [start, end] of union) pauseMs = Math.min(CAP, pauseMs + end - start);
      state.paused_ms = pauseMs; state.due_at = null;
      state.status = state.completion_receipt_digest !== null ? "completed_unresolved_deadline" : "unresolved_deadline";
    }
    for (const [type, at, evidence] of pending.sort((x, y) => x[1] - y[1] || order(x[0], y[0]) || order(x[2], y[2]))) {
      event(state, type, at, now, evidence);
    }
    state.evaluated_at = iso(now);
    state.pause_intervals = p.pauses.map(x => ({ pause_id: x.pause_id, ends_at: x.ends_at }));
    delete state.history_digest; state.history_digest = digest(state);
    return freeze({ schema_version: JOURNEY_ONE_CLOCK_SCHEMA, state,
      deadline_success: state.status === "completed_on_time",
      replan_required: state.miss_at !== null, safe_construction_may_continue: true,
      completion_currently_usable: completion !== null,
      benchmark_amended: state.current_benchmark_manifest_digest !== state.origin_benchmark_manifest_digest,
      unresolved_reason: base.status === "unresolved_deadline" ? base.reason_id : null,
      durable_history_write_required: true, authority_granted: false, effects: V5_NO_EFFECTS });
  } });
}
