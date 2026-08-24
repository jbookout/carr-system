// Job Passport wire reader — a deterministic, read-only projection consumer.
//
// The Model Room owns no task state. It reads typed, redacted receipts already
// on the partner wire, keeps the newest complete projection for each durable
// Work Request, and makes uncertainty visible instead of filling gaps with
// transcript guesses. The controller, not this browser, validates/promotes
// canonical state before it is wrapped for the wire.

const WIRE_VERSION = "job-passport-wire.v1";
const KINDS = new Set(["execution_envelope", "progress_event", "attempt_receipt", "observatory_projection", "eval_portfolio"]);
const PROGRESS = new Set(["active", "quiet", "stale", "blocked", "failed", "unknown", "verified_complete"]);
const LIFECYCLE = new Set(["succeeded", "failed", "timed_out", "cancelled", "partial", "unknown"]);
const VERIFICATION = new Set(["verified_success", "verified_failure", "partial", "unknown", "not_attempted"]);
const DIGEST = /^sha256:[a-f0-9]{64}$/;

function object(value) { return value && typeof value === "object" && !Array.isArray(value); }
function string(value) { return typeof value === "string" && value.length > 0; }
function list(value) { return Array.isArray(value); }
function at(value) { const ms = Date.parse(value || ""); return Number.isFinite(ms) ? ms : 0; }
function number(value) { return Number.isInteger(value) && value >= 1; }

/** Parse only the explicit wrapper. Other receipts stay ordinary wire traffic. */
export function parseJobPassportReceipt(body) {
  if (typeof body !== "string" || !body.trim().startsWith("{")) return { ok: false, reason: "not_json" };
  let outer;
  try { outer = JSON.parse(body); } catch { return { ok: false, reason: "malformed_json" }; }
  if (!object(outer) || Object.keys(outer).length !== 1 || !object(outer.job_passport)) return { ok: false, reason: "not_job_passport" };
  const wire = outer.job_passport;
  if (Object.keys(wire).length !== 3 || wire.schema_version !== WIRE_VERSION || !KINDS.has(wire.kind) || !object(wire.payload)) {
    return { ok: false, reason: "malformed_job_passport" };
  }
  return { ok: true, kind: wire.kind, payload: wire.payload };
}

function validProjection(value) {
  if (!object(value) || value.schema_version !== "observatory-attempt-projection.v1" || !DIGEST.test(value.projection_digest)
    || !string(value.work_request_id) || !string(value.generated_at) || !object(value.source_state)
    || !number(value.source_state.state_version) || !DIGEST.test(value.source_state.canonical_record_digest)
    || !DIGEST.test(value.source_state.plan_revision_digest) || !object(value.attempt_lane) || !object(value.attempt_lane.persistent_profile)
    || !object(value.attempt_lane.actual_staffing) || !object(value.state) || !list(value.component_map)
    || !object(value.observed_movement) || !list(value.timeline) || !list(value.evidence_refs)) return false;
  if (!string(value.attempt_lane.attempt_id) || !string(value.attempt_lane.persistent_profile.profile_id)
    || !string(value.attempt_lane.persistent_profile.display_label) || !string(value.attempt_lane.actual_staffing.surface)
    || !string(value.attempt_lane.actual_staffing.adapter_id) || !string(value.attempt_lane.actual_staffing.harness_id)
    || !string(value.attempt_lane.actual_staffing.model_id) || !PROGRESS.has(value.state.progress)
    || !LIFECYCLE.has(value.state.lifecycle) || !VERIFICATION.has(value.state.verification)) return false;
  let sequence = 0;
  return value.timeline.every((event) => object(event) && number(event.sequence) && event.sequence > sequence
    && ((sequence = event.sequence) || true) && string(event.occurred_at) && string(event.event_type)
    && list(event.evidence_refs));
}

function validEvalPortfolio(value) {
  if (!object(value) || value.schema_version !== "job-passport-eval-portfolio.v1" || value.data_class !== "synthetic_only"
    || !object(value.binding) || !string(value.binding.work_request_id) || !number(value.binding.state_version)
    || !DIGEST.test(value.binding.plan_revision_digest) || !DIGEST.test(value.binding.canonical_record_digest)
    || !DIGEST.test(value.binding.projection_digest) || !list(value.cases) || !list(value.results)
    || !list(value.frontier_comparisons) || !list(value.taxonomy?.failure_modes)) return false;
  const rungs = new Set(["smoke", "regression", "hill_climb", "launch"]);
  const outcomes = new Set(["passed", "failed", "blocked", "unknown"]);
  return value.cases.every((row) => object(row) && string(row.case_id) && rungs.has(row.rung) && list(row.job_stages)
    && object(row.adapter_configuration) && string(row.adapter_configuration.surface))
    && value.results.every((row) => object(row) && string(row.result_id) && string(row.case_id) && rungs.has(row.rung)
      && outcomes.has(row.status) && list(row.dimension_results) && list(row.stage_results)
      && row.dimension_results.every((dimension) => object(dimension) && string(dimension.dimension_id) && outcomes.has(dimension.status)
        && ["improved", "equivalent", "regressed", "not_compared"].includes(dimension.direction_vs_baseline)));
}

function compareProjection(a, b) {
  const av = a.source_state.state_version;
  const bv = b.source_state.state_version;
  if (av !== bv) return av - bv;
  // Same version but a different canonical digest is a conflict, never a
  // winner. A genuine later wire sequence may only refresh the same digest.
  if (a.source_state.canonical_record_digest !== b.source_state.canonical_record_digest) return null;
  return at(a.generated_at) - at(b.generated_at);
}

function statusFor(projection) {
  const observed = projection.observed_movement;
  if (projection.state.verification === "verified_success" && projection.state.progress === "verified_complete") return "verified_complete";
  if (projection.state.lifecycle === "failed" || projection.state.progress === "failed") return "failed";
  if (projection.state.progress === "blocked") return "blocked";
  if (projection.state.progress === "stale") return "stale";
  if (projection.state.progress === "quiet") return "quiet";
  if (projection.state.lifecycle === "partial" || projection.state.progress === "unknown" || projection.state.verification === "partial" || projection.state.verification === "unknown") return "unknown_partial";
  if (Array.isArray(observed.deviation_candidates) && observed.deviation_candidates.length) return "deviation_candidate";
  return "aligned";
}

/**
 * Derive the feature-gated cards from the existing receipt stream. Malformed,
 * mismatched, and stale data never replaces a newer projection; conflict is a
 * visible state, not a silent tie-breaker. Auxiliary typed facts are retained
 * for wire accounting but only a controller-built projection paints a card.
 */
export function deriveJobPassports(turns, { now = Date.now() } = {}) {
  const rows = new Map();
  const rejected = [];
  const typedCounts = { execution_envelope: 0, progress_event: 0, attempt_receipt: 0, observatory_projection: 0, eval_portfolio: 0 };
  const portfolios = new Map();
  for (const turn of turns || []) {
    if (turn?.kind !== "receipt") continue;
    const parsed = parseJobPassportReceipt(turn.body);
    if (!parsed.ok) continue;
    typedCounts[parsed.kind] += 1;
    if (parsed.kind === "eval_portfolio") {
      if (!validEvalPortfolio(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_eval_portfolio" }); continue; }
      const prior = portfolios.get(parsed.payload.binding.work_request_id);
      if (!prior || (Number(turn.seq) || 0) > prior.seq) portfolios.set(parsed.payload.binding.work_request_id, { payload: parsed.payload, seq: Number(turn.seq) || 0 });
      continue;
    }
    if (parsed.kind !== "observatory_projection") continue;
    if (!validProjection(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_projection" }); continue; }
    const incoming = parsed.payload;
    const prior = rows.get(incoming.work_request_id);
    if (!prior) { rows.set(incoming.work_request_id, { projection: incoming, seq: Number(turn.seq) || 0, conflict: false }); continue; }
    const comparison = compareProjection(prior.projection, incoming);
    if (comparison === null) {
      prior.conflict = true;
      rejected.push({ seq: Number(turn.seq) || 0, reason: "same_version_conflict" });
    } else if (comparison < 0 || (comparison === 0 && (Number(turn.seq) || 0) > prior.seq)) {
      rows.set(incoming.work_request_id, { projection: incoming, seq: Number(turn.seq) || 0, conflict: false });
    } else {
      rejected.push({ seq: Number(turn.seq) || 0, reason: "stale_projection" });
    }
  }
  const passports = [...rows.values()].map((row) => {
    const projection = row.projection;
    const observedAt = Math.max(at(projection.generated_at), ...projection.timeline.map((event) => at(event.occurred_at)));
    const portfolio = portfolios.get(projection.work_request_id)?.payload;
    const portfolioBound = portfolio && portfolio.binding.state_version === projection.source_state.state_version
      && portfolio.binding.canonical_record_digest === projection.source_state.canonical_record_digest
      && portfolio.binding.projection_digest === projection.projection_digest;
    if (portfolio && !portfolioBound) rejected.push({ seq: portfolios.get(projection.work_request_id).seq, reason: "mismatched_eval_portfolio" });
    return {
      ...projection, wire_seq: row.seq, conflict: row.conflict, status: row.conflict ? "unknown_partial" : statusFor(projection),
      observed_at: observedAt || now, freshness: projection.state.progress === "stale" ? "stale" : "current_as_observed",
      eval_portfolio: portfolioBound ? portfolio : null,
    };
  }).sort((a, b) => b.wire_seq - a.wire_seq);
  return { enabled: passports.length > 0, passports, rejected, typedCounts };
}

export const JOB_PASSPORT_STATUS_LABEL = {
  aligned: "Aligned", deviation_candidate: "Deviation candidate", blocked: "Blocked", quiet: "Quiet", stale: "Stale",
  failed: "Failed", unknown_partial: "Unknown / partial", verified_complete: "Independently verified complete",
};

export function jobPassportStatusLabel(status) { return JOB_PASSPORT_STATUS_LABEL[status] || "Unknown / partial"; }
