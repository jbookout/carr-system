// Job Passport wire reader — a deterministic, read-only projection consumer.
//
// The Model Room owns no task state. It reads typed, redacted receipts already
// on the partner wire, keeps the newest complete projection for each durable
// Work Request, and makes uncertainty visible instead of filling gaps with
// transcript guesses. The controller, not this browser, validates/promotes
// canonical state before it is wrapped for the wire.

const WIRE_VERSION = "job-passport-wire.v1";
const KINDS = new Set(["execution_envelope", "progress_event", "attempt_receipt", "observatory_projection", "evaluation_kernel", "eval_portfolio", "spatial_surface", "telemetry_measurement", "engineering_passport"]);
const PROGRESS = new Set(["active", "quiet", "stale", "blocked", "failed", "unknown", "verified_complete"]);
const LIFECYCLE = new Set(["succeeded", "failed", "timed_out", "cancelled", "partial", "unknown"]);
const VERIFICATION = new Set(["verified_success", "verified_failure", "partial", "unknown", "not_attempted"]);
const DIGEST = /^sha256:[a-f0-9]{64}$/;

function object(value) { return value && typeof value === "object" && !Array.isArray(value); }
function string(value) { return typeof value === "string" && value.length > 0; }
function list(value) { return Array.isArray(value); }
function at(value) { const ms = Date.parse(value || ""); return Number.isFinite(ms) ? ms : 0; }
function number(value) { return Number.isInteger(value) && value >= 1; }
function exactKeys(value, keys) { return object(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key)); }
function canonicalize(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalize).join(",")}]`;
  if (object(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

// Small synchronous SHA-256 for the controller-sealed projection digest. The
// browser must validate the seal before painting; WebCrypto's async API would
// otherwise leave a race in the read-only render path.
function sha256(text) {
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const bytes = new TextEncoder().encode(text); const bitLength = bytes.length * 8; const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6); padded.set(bytes); padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer); view.setUint32(padded.length - 4, bitLength >>> 0, false); view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000), false);
  let h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
  const rotr=(x,n)=>(x>>>n)|(x<<(32-n));
  for (let offset=0; offset<padded.length; offset+=64) {
    const w = new Uint32Array(64); for (let i=0;i<16;i++) w[i]=view.getUint32(offset+i*4,false);
    for (let i=16;i<64;i++) { const s0=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>>3); const s1=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>>10); w[i]=(w[i-16]+s0+w[i-7]+s1)>>>0; }
    let a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,hh=h7;
    for (let i=0;i<64;i++) { const S1=rotr(e,6)^rotr(e,11)^rotr(e,25); const ch=(e&f)^(~e&g); const t1=(hh+S1+ch+K[i]+w[i])>>>0; const S0=rotr(a,2)^rotr(a,13)^rotr(a,22); const maj=(a&b)^(a&c)^(b&c); const t2=(S0+maj)>>>0; hh=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0; }
    h0=(h0+a)>>>0;h1=(h1+b)>>>0;h2=(h2+c)>>>0;h3=(h3+d)>>>0;h4=(h4+e)>>>0;h5=(h5+f)>>>0;h6=(h6+g)>>>0;h7=(h7+hh)>>>0;
  }
  return [h0,h1,h2,h3,h4,h5,h6,h7].map((n)=>n.toString(16).padStart(8,"0")).join("");
}
function canonicalDigest(value) { return `sha256:${sha256(canonicalize(value))}`; }

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
  if (!object(value) || value.schema_version !== "carr-evaluation-kernel.v1" || value.data_class !== "synthetic_only"
    || !object(value.workflow) || !string(value.workflow.workflow_id) || !object(value.policy) || value.policy.default_effect !== "deny"
    || !object(value.provenance) || !object(value.binding) || !string(value.binding.work_request_id) || !number(value.binding.state_version)
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

function validSpatialSurface(value) {
  if (!object(value) || value.schema_version !== "spatial-surface-projection.v1" || !DIGEST.test(value.projection_digest)
    || !object(value.canonical_binding) || !string(value.canonical_binding.work_request_id) || !number(value.canonical_binding.state_version)
    || !DIGEST.test(value.canonical_binding.canonical_record_digest) || !DIGEST.test(value.canonical_binding.source_projection_digest)
    || !list(value.nodes) || !list(value.edges) || !object(value.semantic_zoom) || !object(value.home_zone) || !list(value.list_order)) return false;
  const ids = new Set();
  for (const node of value.nodes) {
    if (!object(node) || !string(node.node_id) || ids.has(node.node_id) || !object(node.geometry) || !object(node.presentation)
      || !object(node.status) || !object(node.accessibility) || !string(node.accessibility.label) || !string(node.accessibility.non_color_status_token)) return false;
    ids.add(node.node_id);
  }
  return value.list_order.length === ids.size && value.list_order.every((id) => ids.has(id)) && ids.has(value.home_zone.home_node_id)
    && value.edges.every((edge) => object(edge) && ids.has(edge.from_node_id) && ids.has(edge.to_node_id));
}

function validTelemetryMeasurement(value) {
  if (!object(value) || value.schema_version !== "telemetry-measurement.v1" || !string(value.measurement_id)
    || !["subscription_quota", "session_tokens", "billed_cost", "elapsed_time", "lifecycle_activity", "other"].includes(value.metric_kind)
    || !string(value.unit) || !string(value.scope) || !object(value.attribution) || !string(value.attribution.attempt_id)
    || !object(value.source) || !object(value.value) || !string(value.observed_at)
    || !["fresh", "stale", "unknown"].includes(value.freshness)) return false;
  const sourceTypes = new Set(["structured_provider_event", "official_provider_api", "documented_cli_json", "deterministic_local_clock", "unavailable"]);
  const kinds = new Set(["actual", "estimate", "unavailable"]);
  if (!sourceTypes.has(value.source.type) || !Number.isInteger(value.source.priority) || !kinds.has(value.value.kind)) return false;
  if (value.source.type === "unavailable" && value.value.kind !== "unavailable") return false;
  if (value.value.kind === "unavailable") return value.value.amount === null && string(value.value.unavailable_reason);
  if (typeof value.value.amount !== "number" || !Number.isFinite(value.value.amount)) return false;
  return value.value.kind !== "estimate" || (string(value.value.estimate_method) && string(value.value.uncertainty));
}

function validId(value) { return string(value) && /^[A-Za-z][A-Za-z0-9._:-]{2,127}$/.test(value); }
function validEvidence(value, required = false) {
  return list(value) && (required ? value.length > 0 : true) && value.every((row) => exactKeys(row, ["ref", "redaction_class", "content_digest"])
    && validId(row.ref) && ["metadata_only", "redacted_evidence"].includes(row.redaction_class) && DIGEST.test(row.content_digest));
}
function sameSet(a, b) { return list(a) && list(b) && a.length === b.length && [...a].sort().every((item, index) => item === [...b].sort()[index]); }
function validBinding(value) { return exactKeys(value, ["id", "state_version", "canonical_record_digest"]) && validId(value.id) && number(value.state_version) && DIGEST.test(value.canonical_record_digest); }
function validPlanRef(value) { return exactKeys(value, ["id", "revision", "digest"]) && validId(value.id) && number(value.revision) && DIGEST.test(value.digest); }
function validPlannedCheck(value) { return exactKeys(value, ["check_ref", "failure_condition", "evidence_requirement"]) && validId(value.check_ref) && string(value.failure_condition) && ["redacted_evidence_required", "metadata_only_sufficient"].includes(value.evidence_requirement); }
function validEngineeringPlan(value) {
  if (!exactKeys(value, ["schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices"]) || value.schema_version !== "engineering-slice-plan.v1"
    || !validBinding(value.work_request) || !validPlanRef(value.accepted_plan_revision) || !DIGEST.test(value.plan_digest) || !list(value.slices) || value.slices.length === 0) return false;
  const refs = new Set(), ordinals = new Set();
  for (const slice of value.slices) {
    if (!exactKeys(slice, ["slice_ref", "ordinal", "objective", "definition_of_done", "dependency_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "baseline_evidence_refs", "planned_checks", "scope_boundary", "forbidden_change_refs", "concurrency_posture", "manual_qa_required", "risk_class", "release_requirement"])
      || !validId(slice.slice_ref) || !number(slice.ordinal) || refs.has(slice.slice_ref) || ordinals.has(slice.ordinal)
      || !string(slice.objective) || !string(slice.definition_of_done) || !list(slice.dependency_refs) || !slice.dependency_refs.every(validId)
      || !list(slice.declared_resource_refs) || !slice.declared_resource_refs.every(validId) || !list(slice.declared_component_refs) || !slice.declared_component_refs.every(validId)
      || !list(slice.declared_plan_step_refs) || !slice.declared_plan_step_refs.every(validId) || !validEvidence(slice.baseline_evidence_refs)
      || !list(slice.planned_checks) || slice.planned_checks.length === 0 || !slice.planned_checks.every(validPlannedCheck)
      || !string(slice.scope_boundary) || !list(slice.forbidden_change_refs) || !slice.forbidden_change_refs.every(validId)
      || !["parallel_safe", "serial_after_dependencies", "exclusive_resource"].includes(slice.concurrency_posture)
      || typeof slice.manual_qa_required !== "boolean" || !/^R[0-6]$/.test(slice.risk_class) || !["required", "not_required"].includes(slice.release_requirement)) return false;
    refs.add(slice.slice_ref); ordinals.add(slice.ordinal);
  }
  if (!value.slices.every((slice) => slice.dependency_refs.every((dep) => refs.has(dep)))) return false;
  const visit = (ref, visiting = new Set(), visited = new Set()) => { if (visiting.has(ref)) return false; if (visited.has(ref)) return true; visiting.add(ref); const slice = value.slices.find((row) => row.slice_ref === ref); if (!slice || !slice.dependency_refs.every((dep) => visit(dep, visiting, visited))) return false; visiting.delete(ref); visited.add(ref); return true; };
  if (![...refs].every((ref) => visit(ref))) return false;
  const copy = structuredClone(value); delete copy.plan_digest;
  return canonicalDigest(copy) === value.plan_digest;
}
function validReceipt(value, plan, receiptRefs, attemptRefs) {
  if (!exactKeys(value, ["schema_version", "envelope_digest", "attempt_id", "slice_ref", "plan_digest", "attribution", "planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "checks", "outcome", "artifact_refs", "evidence_refs", "deviations", "source_evidence", "reset_reconstruction", "executor_claim", "independent_verification_required"])
    || value.schema_version !== "engineering-slice-receipt.v1" || !DIGEST.test(value.envelope_digest) || !validId(value.attempt_id) || !validId(value.slice_ref) || receiptRefs.has(value.slice_ref) || attemptRefs.has(value.attempt_id) || value.plan_digest !== plan.plan_digest) return false;
  const slice = plan.slices.find((row) => row.slice_ref === value.slice_ref); if (!slice || !exactKeys(value.attribution, ["actor_ref", "session_ref", "adapter_ref"]) || !validId(value.attribution.actor_ref) || !validId(value.attribution.session_ref) || !validId(value.attribution.adapter_ref)) return false;
  for (const field of ["planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "artifact_refs"]) if (!list(value[field]) || !value[field].every(validId)) return false;
  if (!sameSet(value.planned_resource_refs, slice.declared_resource_refs) || !sameSet(value.planned_component_refs, slice.declared_component_refs)) return false;
  if (!list(value.checks) || value.checks.length !== slice.planned_checks.length) return false;
  const planned = new Map(slice.planned_checks.map((check) => [check.check_ref, check])); const seen = new Set();
  for (const check of value.checks) {
    if (!exactKeys(check, ["check_ref", "state", "evidence_refs"]) || !validId(check.check_ref) || seen.has(check.check_ref) || !planned.has(check.check_ref) || !["passed", "failed", "blocked", "not_run"].includes(check.state) || !validEvidence(check.evidence_refs, check.state === "passed") || (check.state === "passed" && !check.evidence_refs.some((evidence) => evidence.redaction_class === (planned.get(check.check_ref).evidence_requirement === "redacted_evidence_required" ? "redacted_evidence" : "metadata_only")))) return false;
    seen.add(check.check_ref);
  }
  const deviationRefs = new Set(); if (!list(value.deviations) || !value.deviations.every((deviation) => exactKeys(deviation, ["deviation_ref", "category", "reason", "impact", "plan_revision_required", "evidence_refs", "out_of_scope_resource_refs", "out_of_scope_component_refs", "review_state"]) && validId(deviation.deviation_ref) && !deviationRefs.has(deviation.deviation_ref) && string(deviation.category) && string(deviation.reason) && string(deviation.impact) && typeof deviation.plan_revision_required === "boolean" && validEvidence(deviation.evidence_refs) && deviation.evidence_refs.length > 0 && list(deviation.out_of_scope_resource_refs) && deviation.out_of_scope_resource_refs.every(validId) && list(deviation.out_of_scope_component_refs) && deviation.out_of_scope_component_refs.every(validId) && ["unreviewed", "reviewed", "resolved"].includes(deviation.review_state) && (deviationRefs.add(deviation.deviation_ref), true))) return false;
  const allowedResources = new Set(slice.declared_resource_refs.concat(value.deviations.filter((deviation) => deviation.review_state === "resolved").flatMap((deviation) => deviation.out_of_scope_resource_refs))); const allowedComponents = new Set(slice.declared_component_refs.concat(value.deviations.filter((deviation) => deviation.review_state === "resolved").flatMap((deviation) => deviation.out_of_scope_component_refs)));
  if (!value.actual_resource_refs.every((ref) => allowedResources.has(ref)) || !value.actual_component_refs.every((ref) => allowedComponents.has(ref)) || !["claimed_complete", "failed", "blocked", "reopened"].includes(value.outcome) || !validEvidence(value.evidence_refs, value.outcome === "claimed_complete") || (value.outcome === "claimed_complete" && (value.artifact_refs.length === 0 || value.checks.some((check) => check.state !== "passed")))) return false;
  if (!exactKeys(value.source_evidence, ["worktree_ref", "branch_ref", "source_sha", "evidence_refs"]) || !validId(value.source_evidence.worktree_ref) || !validId(value.source_evidence.branch_ref) || !string(value.source_evidence.source_sha) || !validEvidence(value.source_evidence.evidence_refs)) return false;
  if (!exactKeys(value.reset_reconstruction, ["fresh_session", "inherited_transcript_used", "reconstruction_free", "remediation_action"]) || value.reset_reconstruction.fresh_session !== true || value.reset_reconstruction.inherited_transcript_used !== false || typeof value.reset_reconstruction.reconstruction_free !== "boolean" || (value.reset_reconstruction.reconstruction_free ? !(value.reset_reconstruction.remediation_action === null || string(value.reset_reconstruction.remediation_action)) : !string(value.reset_reconstruction.remediation_action))) return false;
  return exactKeys(value.executor_claim, ["claim_state", "claimed_by", "claimed_at"]) && value.executor_claim.claim_state === "executor_claim" && validId(value.executor_claim.claimed_by) && string(value.executor_claim.claimed_at) && value.independent_verification_required === true;
}
function validReviewerFacts(value, receipts) {
  if (!list(value)) return false; const bySlice = new Map(receipts.map((receipt) => [receipt.slice_ref, receipt])); const seen = new Set();
  return value.every((fact) => exactKeys(fact, ["slice_ref", "attempt_id", "reviewer_ref", "session_ref", "state", "evidence_refs", "is_independent", "reviewed_deviation_refs", "resolved_deviation_refs"]) && validId(fact.slice_ref) && !seen.has(fact.slice_ref) && bySlice.has(fact.slice_ref) && fact.attempt_id === bySlice.get(fact.slice_ref).attempt_id && validId(fact.attempt_id) && validId(fact.reviewer_ref) && validId(fact.session_ref) && fact.reviewer_ref !== bySlice.get(fact.slice_ref).attribution.actor_ref && fact.session_ref !== bySlice.get(fact.slice_ref).attribution.session_ref && fact.is_independent === true && ["passed", "failed", "blocked"].includes(fact.state) && validEvidence(fact.evidence_refs, fact.state === "passed") && list(fact.reviewed_deviation_refs) && fact.reviewed_deviation_refs.every(validId) && sameSet(fact.reviewed_deviation_refs, bySlice.get(fact.slice_ref).deviations.map((deviation) => deviation.deviation_ref)) && list(fact.resolved_deviation_refs) && fact.resolved_deviation_refs.every(validId) && fact.resolved_deviation_refs.every((ref) => fact.reviewed_deviation_refs.includes(ref)) && (fact.state !== "passed" || (sameSet(fact.resolved_deviation_refs, fact.reviewed_deviation_refs) && bySlice.get(fact.slice_ref).deviations.every((deviation) => deviation.review_state === "resolved")) ) && (seen.add(fact.slice_ref), true));
}
function validQAFacts(value, receipts) { if (!list(value)) return false; const known = new Set(receipts.map((receipt) => receipt.slice_ref)); const seen = new Set(); return value.every((fact) => exactKeys(fact, ["slice_ref", "state", "evidence_refs", "note"]) && validId(fact.slice_ref) && known.has(fact.slice_ref) && !seen.has(fact.slice_ref) && ["passed", "failed", "blocked"].includes(fact.state) && validEvidence(fact.evidence_refs, fact.state === "passed") && string(fact.note) && (seen.add(fact.slice_ref), true)); }
function validDisposition(value, states) { return exactKeys(value, ["state", "evidence_refs", "note"]) && states.has(value.state) && validEvidence(value.evidence_refs, ["complete", "passed", "released", "resolved", "proposed"].includes(value.state)) && string(value.note); }
function validLearning(value) { return exactKeys(value, ["state", "route", "evidence_refs", "note"]) && ["unresolved", "proposed", "rejected", "nothing_durable"].includes(value.state) && (value.route === null || ["regression_test", "gate_or_validator", "decision_record", "skill_or_workflow", "memory_or_rule_candidate", "incident_finding", "speculative_finding", "nothing_durable"].includes(value.route)) && (value.state === "unresolved" ? value.route === null : value.route !== null) && validEvidence(value.evidence_refs, ["proposed", "rejected"].includes(value.state)) && string(value.note); }
function derivedOperator(slices, receipts, qa) { const evidence = new Map(receipts.flatMap((receipt) => receipt.evidence_refs.map((item) => [item.ref, item]))); return { what_changed: slices.filter((slice) => slice.state === "verified_complete").map((slice) => slice.slice_ref), why: "derived from accepted plan and typed receipts", evidence_refs: [...evidence.keys()].sort().map((key) => evidence.get(key)), deviations: [...new Set(receipts.flatMap((receipt) => receipt.deviations.map((deviation) => deviation.deviation_ref)))].sort(), remaining_risk: slices.filter((slice) => slice.state !== "verified_complete").map((slice) => slice.slice_ref), manual_qa_items: slices.filter((slice) => slice.manual_qa_required && !qa.has(slice.slice_ref)).map((slice) => slice.slice_ref) }; }
function validEngineeringPassport(value) {
  if (!exactKeys(value, ["schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slice_plan", "slices", "receipts", "reviewer_facts", "qa_facts", "operator_receipt", "closure", "closure_state", "stale_conflict", "projection_digest"]) || value.schema_version !== "engineering-passport.v1" || !validBinding(value.work_request) || !validPlanRef(value.accepted_plan_revision) || !DIGEST.test(value.plan_digest) || !validEngineeringPlan(value.slice_plan) || value.slice_plan.plan_digest !== value.plan_digest || JSON.stringify(value.slice_plan.work_request) !== JSON.stringify(value.work_request) || JSON.stringify(value.slice_plan.accepted_plan_revision) !== JSON.stringify(value.accepted_plan_revision) || !["blocked", "complete"].includes(value.closure_state) || !exactKeys(value.stale_conflict, ["state", "reason"]) || !["none", "stale", "conflict", "uncertain"].includes(value.stale_conflict.state) || (value.stale_conflict.state === "none" ? value.stale_conflict.reason !== null : !string(value.stale_conflict.reason))) return false;
  const expectedOrder = [...value.slice_plan.slices].sort((a, b) => a.ordinal - b.ordinal); if (!list(value.slices) || value.slices.length !== expectedOrder.length || value.slices.some((slice, index) => !object(slice) || slice.slice_ref !== expectedOrder[index].slice_ref || slice.ordinal !== expectedOrder[index].ordinal)) return false;
  const states = new Set(["eligible", "blocked", "claimed", "reopened", "verified_complete"]); const receipts = []; const receiptRefs = new Set(); const attemptRefs = new Set(); if (!list(value.receipts) || !value.receipts.every((receipt) => validReceipt(receipt, value.slice_plan, receiptRefs, attemptRefs) && (receiptRefs.add(receipt.slice_ref), attemptRefs.add(receipt.attempt_id), receipts.push(receipt), true))) return false;
  const receiptBySlice = new Map(receipts.map((receipt) => [receipt.slice_ref, receipt])); const reviewers = list(value.reviewer_facts) ? value.reviewer_facts : []; if (!validReviewerFacts(reviewers, receipts)) return false; const reviewerBy = new Map(reviewers.map((fact) => [fact.slice_ref, fact])); if (!validQAFacts(value.qa_facts, receipts)) return false; const qaBy = new Map(value.qa_facts.map((fact) => [fact.slice_ref, fact]));
  const projected = value.slices; if (!projected.every((slice) => exactKeys(slice, ["slice_ref", "ordinal", "dependency_refs", "state", "planned_check_refs", "deviation_refs", "manual_qa_required", "release_requirement"]) && list(slice.dependency_refs) && slice.dependency_refs.every(validId) && states.has(slice.state) && list(slice.planned_check_refs) && JSON.stringify(slice.planned_check_refs) === JSON.stringify(expectedOrder.find((row) => row.slice_ref === slice.slice_ref).planned_checks.map((check) => check.check_ref)) && list(slice.deviation_refs) && slice.deviation_refs.every(validId) && typeof slice.manual_qa_required === "boolean" && ["required", "not_required"].includes(slice.release_requirement))) return false;
  const expectedEligible = new Set(expectedOrder.filter((slice) => !receiptBySlice.has(slice.slice_ref) && slice.dependency_refs.every((dep) => reviewerBy.has(dep) && reviewerBy.get(dep).state === "passed" && receiptBySlice.get(dep)?.outcome === "claimed_complete")).map((slice) => slice.slice_ref));
  if (!projected.every((slice) => { const source = expectedOrder.find((row) => row.slice_ref === slice.slice_ref); const receipt = receiptBySlice.get(slice.slice_ref); const review = reviewerBy.get(slice.slice_ref); const qa = qaBy.get(slice.slice_ref); const expected = !receipt ? (expectedEligible.has(slice.slice_ref) ? "eligible" : "blocked") : review?.state === "passed" && (!source.manual_qa_required || qa?.state === "passed") ? "verified_complete" : receipt.outcome === "failed" || receipt.outcome === "reopened" || qa?.state === "failed" ? "reopened" : "claimed"; return JSON.stringify(slice.dependency_refs) === JSON.stringify(source.dependency_refs) && slice.state === expected && JSON.stringify(slice.deviation_refs) === JSON.stringify((receipt?.deviations || []).map((deviation) => deviation.deviation_ref)) && slice.manual_qa_required === source.manual_qa_required && slice.release_requirement === source.release_requirement; })) return false;
  if (!exactKeys(value.operator_receipt, ["what_changed", "why", "evidence_refs", "deviations", "remaining_risk", "manual_qa_items"]) || !list(value.operator_receipt.what_changed) || !list(value.operator_receipt.deviations) || !list(value.operator_receipt.remaining_risk) || !list(value.operator_receipt.manual_qa_items) || !validEvidence(value.operator_receipt.evidence_refs) || !string(value.operator_receipt.why) || JSON.stringify(value.operator_receipt) !== JSON.stringify(derivedOperator(projected, receipts, qaBy))) return false;
  if (!exactKeys(value.closure, ["work", "proof", "explanation", "release", "learning"]) || !validDisposition(value.closure.work, new Set(["unresolved", "complete"])) || !validDisposition(value.closure.proof, new Set(["unresolved", "complete"])) || !validDisposition(value.closure.explanation, new Set(["unresolved", "complete"])) || !validDisposition(value.closure.release, new Set(["unresolved", "released", "not_required"])) || !validLearning(value.closure.learning)) return false;
  const complete = value.stale_conflict.state === "none" && projected.length > 0 && receipts.length === projected.length && projected.every((slice) => slice.state === "verified_complete") && receipts.every((receipt) => receipt.outcome === "claimed_complete" && receipt.artifact_refs.length > 0 && receipt.evidence_refs.length > 0) && projected.every((slice) => reviewerBy.get(slice.slice_ref)?.state === "passed") && projected.every((slice) => !slice.manual_qa_required || qaBy.get(slice.slice_ref)?.state === "passed") && value.closure.work.state === "complete" && value.closure.proof.state === "complete" && value.closure.explanation.state === "complete" && (value.closure.release.state === "released" || (value.closure.release.state === "not_required" && projected.every((slice) => slice.release_requirement === "not_required"))) && ["proposed", "rejected", "nothing_durable"].includes(value.closure.learning.state) && value.closure.learning.route !== null && receipts.every((receipt) => receipt.deviations.every((deviation) => !deviation.plan_revision_required && deviation.review_state === "resolved" && reviewerBy.get(receipt.slice_ref).resolved_deviation_refs.includes(deviation.deviation_ref)));
  if ((value.closure_state === "complete") !== complete) return false; const copy = structuredClone(value); delete copy.projection_digest; return canonicalDigest(copy) === value.projection_digest;
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
  const typedCounts = { execution_envelope: 0, progress_event: 0, attempt_receipt: 0, observatory_projection: 0, evaluation_kernel: 0, eval_portfolio: 0, spatial_surface: 0, telemetry_measurement: 0 };
  const portfolios = new Map();
  const surfaces = new Map();
  const telemetryByAttempt = new Map();
  const engineering = new Map();
  for (const turn of turns || []) {
    if (turn?.kind !== "receipt") continue;
    const parsed = parseJobPassportReceipt(turn.body);
    if (!parsed.ok) continue;
    if (parsed.kind === "engineering_passport") {
      typedCounts.engineering_passport = (typedCounts.engineering_passport || 0) + 1;
      if (!validEngineeringPassport(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_engineering_passport" }); continue; }
      const prior = engineering.get(parsed.payload.work_request.id);
      if (prior && prior.payload.work_request.state_version === parsed.payload.work_request.state_version
        && prior.payload.work_request.canonical_record_digest !== parsed.payload.work_request.canonical_record_digest) {
        prior.conflict = true;
        rejected.push({ seq: Number(turn.seq) || 0, reason: "conflicting_engineering_passport" });
      } else if (!prior || parsed.payload.work_request.state_version > prior.payload.work_request.state_version
        || (parsed.payload.work_request.state_version === prior.payload.work_request.state_version && (Number(turn.seq) || 0) > prior.seq)) {
        engineering.set(parsed.payload.work_request.id, { payload: parsed.payload, seq: Number(turn.seq) || 0, conflict: false });
      } else if (prior && parsed.payload.work_request.state_version < prior.payload.work_request.state_version) {
        rejected.push({ seq: Number(turn.seq) || 0, reason: "stale_engineering_passport" });
      }
      continue;
    }
    typedCounts[parsed.kind] += 1;
    if (parsed.kind === "evaluation_kernel" || parsed.kind === "eval_portfolio") {
      if (!validEvalPortfolio(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_eval_portfolio" }); continue; }
      const prior = portfolios.get(parsed.payload.binding.work_request_id);
      if (!prior || (Number(turn.seq) || 0) > prior.seq) portfolios.set(parsed.payload.binding.work_request_id, { payload: parsed.payload, seq: Number(turn.seq) || 0 });
      continue;
    }
    if (parsed.kind === "spatial_surface") {
      if (!validSpatialSurface(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_spatial_surface" }); continue; }
      const binding = parsed.payload.canonical_binding;
      const prior = surfaces.get(binding.work_request_id);
      if (prior && binding.state_version < prior.payload.canonical_binding.state_version) { rejected.push({ seq: Number(turn.seq) || 0, reason: "stale_spatial_surface" }); continue; }
      if (prior && binding.state_version === prior.payload.canonical_binding.state_version && binding.canonical_record_digest !== prior.payload.canonical_binding.canonical_record_digest) { rejected.push({ seq: Number(turn.seq) || 0, reason: "conflicting_spatial_surface" }); continue; }
      if (!prior || (Number(turn.seq) || 0) > prior.seq) surfaces.set(binding.work_request_id, { payload: parsed.payload, seq: Number(turn.seq) || 0 });
      continue;
    }
    if (parsed.kind === "telemetry_measurement") {
      if (!validTelemetryMeasurement(parsed.payload)) { rejected.push({ seq: Number(turn.seq) || 0, reason: "invalid_telemetry_measurement" }); continue; }
      const attemptId = parsed.payload.attribution.attempt_id;
      const prior = telemetryByAttempt.get(attemptId) || new Map();
      const existing = prior.get(parsed.payload.metric_kind);
      if (!existing || at(parsed.payload.observed_at) > at(existing.payload.observed_at) || (at(parsed.payload.observed_at) === at(existing.payload.observed_at) && (Number(turn.seq) || 0) > existing.seq)) prior.set(parsed.payload.metric_kind, { payload: parsed.payload, seq: Number(turn.seq) || 0 });
      telemetryByAttempt.set(attemptId, prior);
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
    const spatial = surfaces.get(projection.work_request_id)?.payload;
    const spatialBound = spatial && spatial.canonical_binding.state_version === projection.source_state.state_version
      && spatial.canonical_binding.canonical_record_digest === projection.source_state.canonical_record_digest
      && spatial.canonical_binding.source_projection_digest === projection.projection_digest;
    if (spatial && !spatialBound) rejected.push({ seq: surfaces.get(projection.work_request_id).seq, reason: "mismatched_spatial_surface" });
    const engineeringCandidate = engineering.get(projection.work_request_id);
    let engineeringPassport = null;
    if (engineeringCandidate) {
      const binding = engineeringCandidate.payload;
      const exactBinding = binding.work_request.state_version === projection.source_state.state_version
        && binding.work_request.canonical_record_digest === projection.source_state.canonical_record_digest
        && binding.accepted_plan_revision.digest === projection.source_state.plan_revision_digest;
      if (engineeringCandidate.conflict) rejected.push({ seq: engineeringCandidate.seq, reason: "conflicting_engineering_passport" });
      else if (binding.work_request.state_version < projection.source_state.state_version) rejected.push({ seq: engineeringCandidate.seq, reason: "stale_engineering_passport" });
      else if (!exactBinding) rejected.push({ seq: engineeringCandidate.seq, reason: "mismatched_engineering_passport" });
      else engineeringPassport = binding;
    }
    return {
      ...projection, wire_seq: row.seq, conflict: row.conflict, status: row.conflict ? "unknown_partial" : statusFor(projection),
      observed_at: observedAt || now, freshness: projection.state.progress === "stale" ? "stale" : "current_as_observed",
      eval_portfolio: portfolioBound ? portfolio : null,
      spatial_surface: spatialBound ? spatial : null,
      telemetry_measurements: [...(telemetryByAttempt.get(projection.attempt_lane.attempt_id)?.values() || [])].map((row) => row.payload),
      engineering_passport: engineeringPassport,
    };
  }).sort((a, b) => b.wire_seq - a.wire_seq);
  return { enabled: passports.length > 0, passports, rejected, typedCounts };
}

export const JOB_PASSPORT_STATUS_LABEL = {
  aligned: "Aligned", deviation_candidate: "Deviation candidate", blocked: "Blocked", quiet: "Quiet", stale: "Stale",
  failed: "Failed", unknown_partial: "Unknown / partial", verified_complete: "Independently verified complete",
};

export function jobPassportStatusLabel(status) { return JOB_PASSPORT_STATUS_LABEL[status] || "Unknown / partial"; }
