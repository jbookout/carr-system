// Durable Engineering Passport runtime seam.
//
// This module is deliberately a projection/admission adapter over the existing
// Work Request, accepted sourced plan, capability session and ops.job ledgers.
// It does not create a second queue, authority source, transcript store, or
// model identity.  Codex is the only executable adapter in this first slice;
// Claude is refused explicitly until a fresh-native-session launcher exists.

import { sha256 } from "./sha256.js";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ID = /^[A-Za-z][A-Za-z0-9._:-]{2,127}$/;
const OUTCOMES = new Set(["claimed_complete", "failed", "blocked", "reopened"]);
export const ENGINEERING_REPOSITORY_ACTIONS = Object.freeze([
  "repository:create-worktree",
  "repository:create-branch",
  "repository:write-declared-scope",
  "repository:run-checks",
  "repository:commit",
  "repository:push-branch",
  "repository:open-pr",
]);

const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.keys(value).sort().reduce((out, key) => {
      if (value[key] !== undefined) out[key] = canonicalize(value[key]);
      return out;
    }, {});
  return value;
};

export function canonicalDigest(value) {
  return `sha256:${sha256(JSON.stringify(canonicalize(value)))}`;
}

function error(ToolError, payload) { throw new ToolError(payload); }
function text(value, field, ToolError) {
  if (typeof value !== "string" || !value.trim()) error(ToolError, { error: "engineering_field_required", field });
  return value.trim();
}
function id(value, field, ToolError) {
  const result = text(value, field, ToolError);
  if (!ID.test(result)) error(ToolError, { error: "engineering_identifier_invalid", field });
  return result;
}
function digest(value, field, ToolError) {
  if (typeof value !== "string" || !DIGEST.test(value)) error(ToolError, { error: "engineering_digest_invalid", field });
  return value;
}
function evidence(value, field, ToolError) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join(",") !== "content_digest,redaction_class,ref") error(ToolError, { error: "engineering_evidence_invalid", field });
  id(value.ref, `${field}.ref`, ToolError);
  digest(value.content_digest, `${field}.content_digest`, ToolError);
  if (!["metadata_only", "redacted_evidence"].includes(value.redaction_class)) error(ToolError, { error: "engineering_evidence_invalid", field });
  return value;
}
function uuid(value, field, ToolError) {
  const result = text(value, field, ToolError);
  if (!UUID.test(result)) error(ToolError, { error: "engineering_uuid_invalid", field });
  return result;
}
function exactAuthorityFree(args, ToolError) {
  const forbidden = ["tenant", "organization_tenant_id", "sponsor", "partner", "actor", "identity",
    "authority", "capability", "runtime", "model", "provider", "surface", "adapter", "native_session_ref"];
  const found = forbidden.filter(key => Object.hasOwn(args || {}, key));
  if (found.length) error(ToolError, { error: "caller_authority_selector_forbidden", fields: found });
}

export function requirePlan(plan, ToolError) {
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) error(ToolError, { error: "engineering_slice_plan_invalid" });
  if (Object.keys(plan).sort().join(",") !== "accepted_plan_revision,plan_digest,schema_version,slices,work_request") error(ToolError, { error: "engineering_slice_plan_unknown_field" });
  for (const key of ["schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices"])
    if (!(key in plan)) error(ToolError, { error: "engineering_slice_plan_missing_field", field: key });
  if (plan.schema_version !== "engineering-slice-plan.v1") error(ToolError, { error: "engineering_slice_plan_schema_invalid" });
  const binding = plan.work_request;
  if (!binding || typeof binding !== "object" || Array.isArray(binding) || Object.keys(binding).sort().join(",") !== "canonical_record_digest,id,state_version")
    error(ToolError, { error: "engineering_slice_plan_work_binding_invalid" });
  id(binding.id, "work_request.id", ToolError); digest(binding.canonical_record_digest, "work_request.canonical_record_digest", ToolError);
  if (!Number.isInteger(binding.state_version) || binding.state_version < 1) error(ToolError, { error: "engineering_slice_plan_state_version_invalid" });
  const revision = plan.accepted_plan_revision;
  if (!revision || typeof revision !== "object" || Array.isArray(revision) || Object.keys(revision).sort().join(",") !== "digest,id,revision")
    error(ToolError, { error: "engineering_slice_plan_revision_invalid" });
  id(revision.id, "accepted_plan_revision.id", ToolError); digest(revision.digest, "accepted_plan_revision.digest", ToolError);
  if (!Number.isInteger(revision.revision) || revision.revision < 1) error(ToolError, { error: "engineering_slice_plan_revision_invalid" });
  digest(plan.plan_digest, "plan_digest", ToolError);
  if (!Array.isArray(plan.slices) || plan.slices.length < 1) error(ToolError, { error: "engineering_slice_plan_empty" });
  const refs = new Set();
  for (const slice of plan.slices) {
    const required = ["baseline_evidence_refs", "concurrency_posture", "declared_component_refs", "declared_plan_step_refs", "declared_resource_refs", "definition_of_done", "dependency_refs", "forbidden_change_refs", "manual_qa_required", "objective", "ordinal", "planned_checks", "release_requirement", "risk_class", "scope_boundary", "slice_ref"];
    if (!slice || typeof slice !== "object" || Array.isArray(slice) || Object.keys(slice).sort().join(",") !== required.join(",")) error(ToolError, { error: "engineering_slice_schema_invalid", slice_ref: slice?.slice_ref });
    id(slice.slice_ref, "slice_ref", ToolError);
    if (!Number.isInteger(slice.ordinal) || slice.ordinal < 1 || typeof slice.objective !== "string" || !slice.objective.trim() || typeof slice.definition_of_done !== "string" || !slice.definition_of_done.trim() || typeof slice.scope_boundary !== "string" || !slice.scope_boundary.trim()) error(ToolError, { error: "engineering_slice_fields_invalid", slice_ref: slice.slice_ref });
    if (!["parallel_safe", "serial_after_dependencies", "exclusive_resource"].includes(slice.concurrency_posture) || !/^R[0-6]$/.test(slice.risk_class) || !["required", "not_required"].includes(slice.release_requirement) || typeof slice.manual_qa_required !== "boolean") error(ToolError, { error: "engineering_slice_enum_invalid", slice_ref: slice.slice_ref });
    if (refs.has(slice.slice_ref)) error(ToolError, { error: "engineering_slice_duplicate", slice_ref: slice.slice_ref });
    refs.add(slice.slice_ref);
    if (!Array.isArray(slice.dependency_refs) || slice.dependency_refs.some(ref => !refs.has(ref) && !plan.slices.some(candidate => candidate.slice_ref === ref)))
      error(ToolError, { error: "engineering_slice_dependency_unknown", slice_ref: slice.slice_ref });
    for (const field of ["baseline_evidence_refs", "declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "forbidden_change_refs", "dependency_refs"])
      if (!Array.isArray(slice[field])) error(ToolError, { error: "engineering_slice_array_invalid", field, slice_ref: slice.slice_ref });
    for (const [index, item] of slice.baseline_evidence_refs.entries()) evidence(item, `baseline_evidence_refs[${index}]`, ToolError);
    for (const field of ["declared_resource_refs", "declared_component_refs", "declared_plan_step_refs", "forbidden_change_refs", "dependency_refs"])
      for (const [index, item] of slice[field].entries()) id(item, `${field}[${index}]`, ToolError);
    const checkRefs = new Set();
    if (!Array.isArray(slice.planned_checks) || slice.planned_checks.length < 1 || slice.planned_checks.some(check => !check || typeof check !== "object" || Object.keys(check).sort().join(",") !== "check_ref,evidence_requirement,failure_condition" || !id(check.check_ref, "planned_checks.check_ref", ToolError) || checkRefs.has(check.check_ref) || !checkRefs.add(check.check_ref) || typeof check.failure_condition !== "string" || !check.failure_condition.trim() || !["redacted_evidence_required", "metadata_only_sufficient"].includes(check.evidence_requirement))) error(ToolError, { error: "engineering_slice_checks_invalid", slice_ref: slice.slice_ref });
  }
  if (canonicalDigest(Object.fromEntries(Object.entries(plan).filter(([key]) => key !== "plan_digest"))) !== plan.plan_digest)
    error(ToolError, { error: "engineering_slice_plan_digest_mismatch" });
  return plan;
}

function sourceParts(source, ToolError) {
  if (!source || typeof source !== "object" || !source.work_request || !source.accepted_plan)
    error(ToolError, { error: "engineering_admission_source_missing" });
  const work = source.work_request;
  const plan = source.accepted_plan;
  id(work.id, "work_request.id", ToolError);
  if (!Number.isInteger(Number(work.version)) || Number(work.version) < 1)
    error(ToolError, { error: "engineering_work_request_version_invalid" });
  digest(work.canonical_record_digest, "work_request.canonical_record_digest", ToolError);
  id(plan.plan_ref, "accepted_plan.plan_ref", ToolError);
  digest(plan.digest, "accepted_plan.digest", ToolError);
  return { work, plan };
}

function sourcePlanRow(facts, source, ToolError) {
  const row = (facts.slice_plans || []).find(item =>
    item.accepted_plan_id === source.plan.record_id && item.accepted_plan_hash === source.plan.digest);
  if (!row) error(ToolError, { error: "engineering_slice_plan_not_registered", accepted_plan: source.plan.plan_ref });
  return row;
}

function planMatchesCurrentSource(plan, source) {
  return plan.work_request.id === source.work.id &&
    plan.work_request.state_version === Number(source.work.version) &&
    plan.work_request.canonical_record_digest === source.work.canonical_record_digest &&
    plan.accepted_plan_revision.id === source.plan.plan_ref &&
    plan.accepted_plan_revision.revision === Number(source.plan.revision) &&
    plan.accepted_plan_revision.digest === source.plan.digest;
}

function sourcePlan(facts, source, ToolError) {
  const row = sourcePlanRow(facts, source, ToolError);
  const plan = requirePlan(row.plan, ToolError);
  if (!planMatchesCurrentSource(plan, source)) error(ToolError, { error: "engineering_slice_plan_currentness_mismatch" });
  return plan;
}

function sliceFor(plan, sliceRef, ToolError) {
  const row = plan.slices.find(item => item.slice_ref === sliceRef);
  if (!row) error(ToolError, { error: "engineering_slice_not_found", slice_ref: sliceRef });
  return row;
}

function receiptLedgerRows(facts) {
  return (facts?.receipts || []).filter(row => row && typeof row === "object" &&
    typeof row.id === "string" && typeof row.envelope_id === "string");
}

function canonicalReceiptRows(facts) {
  return receiptLedgerRows(facts).filter(row => row.receipt && typeof row.receipt === "object" && !Array.isArray(row.receipt));
}

function canonicalReviewerRows(facts) {
  return (facts?.reviewer_facts || []).filter(row => row && typeof row === "object" &&
    typeof row.id === "string" && typeof row.receipt_id === "string" &&
    row.fact && typeof row.fact === "object" && !Array.isArray(row.fact));
}

function canonicalEnvelopeRows(facts) {
  return (facts?.envelopes || []).filter(row => row && typeof row === "object" && typeof row.id === "string");
}

function exactObject(value, fields) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...fields].sort().join(","));
}

function nonEmptyText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isTypedEvidenceRef(value) {
  return exactObject(value, ["content_digest", "redaction_class", "ref"]) &&
    typeof value.ref === "string" && ID.test(value.ref) &&
    typeof value.content_digest === "string" && DIGEST.test(value.content_digest) &&
    ["metadata_only", "redacted_evidence"].includes(value.redaction_class);
}

function isUniqueIdentifierArray(value) {
  return Array.isArray(value) && value.every(item => typeof item === "string" && ID.test(item)) &&
    new Set(value).size === value.length;
}

function isTypedEvidenceArray(value, nonEmpty = false) {
  return Array.isArray(value) && (!nonEmpty || value.length > 0) && value.every(isTypedEvidenceRef);
}

function isCanonicalReviewerSessionRef(value) {
  return typeof value === "string" && value.startsWith("session:") && ID.test(value);
}

function sameIdentifierSet(left, right) {
  return isUniqueIdentifierArray(left) && isUniqueIdentifierArray(right) &&
    left.length === right.length && left.every(item => right.includes(item));
}

function isCanonicalReceiptCheck(value, requirePassed = false) {
  return exactObject(value, ["check_ref", "evidence_refs", "state"]) &&
    typeof value.check_ref === "string" && ID.test(value.check_ref) &&
    ["passed", "failed", "blocked", "not_run"].includes(value.state) &&
    isTypedEvidenceArray(value.evidence_refs, value.state === "passed") &&
    (!requirePassed || value.state === "passed");
}

function isCanonicalReceiptChecks(value, requirePassed = false) {
  return Array.isArray(value) && value.length > 0 && value.every(check =>
    isCanonicalReceiptCheck(check, requirePassed)) &&
    isUniqueIdentifierArray(value.map(check => check.check_ref));
}

function isCanonicalReceiptDeviation(value, requireResolved = false) {
  return exactObject(value, [
    "category", "deviation_ref", "evidence_refs", "impact", "out_of_scope_component_refs",
    "out_of_scope_resource_refs", "plan_revision_required", "reason", "review_state",
  ]) && typeof value.deviation_ref === "string" && ID.test(value.deviation_ref) &&
    ["category", "reason", "impact"].every(field => nonEmptyText(value[field])) &&
    typeof value.plan_revision_required === "boolean" &&
    isTypedEvidenceArray(value.evidence_refs) &&
    isUniqueIdentifierArray(value.out_of_scope_resource_refs) &&
    isUniqueIdentifierArray(value.out_of_scope_component_refs) &&
    ["unreviewed", "reviewed", "resolved"].includes(value.review_state) &&
    (!requireResolved || (value.review_state === "resolved" && value.plan_revision_required === false));
}

function isCanonicalReceiptDeviations(value, requireResolved = false) {
  return Array.isArray(value) && value.every(deviation =>
    isCanonicalReceiptDeviation(deviation, requireResolved)) &&
    isUniqueIdentifierArray(value.map(deviation => deviation.deviation_ref));
}

function canonicalReceiptDeviationRefs(receipt) {
  const deviations = receipt?.deviations;
  if (!isCanonicalReceiptDeviations(deviations)) return null;
  const refs = deviations.map(deviation => deviation?.deviation_ref);
  if (!isUniqueIdentifierArray(refs)) return null;
  return refs;
}

function resolvedReceiptDeviationRefs(receipt) {
  return isCanonicalReceiptDeviations(receipt?.deviations, true) ? canonicalReceiptDeviationRefs(receipt) : null;
}

function isCanonicalSourceEvidence(value) {
  return exactObject(value, ["branch_ref", "evidence_refs", "source_sha", "worktree_ref"]) &&
    ["worktree_ref", "branch_ref"].every(field => typeof value[field] === "string" && ID.test(value[field])) &&
    nonEmptyText(value.source_sha) && isTypedEvidenceArray(value.evidence_refs);
}

function isCanonicalResetReconstruction(value) {
  return exactObject(value, ["fresh_session", "inherited_transcript_used", "reconstruction_free", "remediation_action"]) &&
    value.fresh_session === true && value.inherited_transcript_used === false &&
    typeof value.reconstruction_free === "boolean" &&
    (value.reconstruction_free
      ? (value.remediation_action === null || nonEmptyText(value.remediation_action))
      : nonEmptyText(value.remediation_action));
}

function isCanonicalExecutorClaim(value) {
  return exactObject(value, ["claim_state", "claimed_at", "claimed_by"]) &&
    value.claim_state === "executor_claim" && typeof value.claimed_by === "string" && ID.test(value.claimed_by) &&
    nonEmptyText(value.claimed_at);
}

function isCanonicalReceiptAttribution(value, envelope) {
  const expected = envelope?.envelope;
  const actorRef = expected?.server_binding?.identity?.agent_principal_id;
  const sessionRef = expected?.agent_session?.id;
  const adapterRef = expected?.server_binding?.adapter?.adapter_id;
  return exactObject(value, ["actor_ref", "adapter_ref", "session_ref"]) &&
    [actorRef, sessionRef, adapterRef].every(item => typeof item === "string" && ID.test(item)) &&
    value.actor_ref === actorRef && value.session_ref === sessionRef && value.adapter_ref === adapterRef;
}

function isCanonicalReceiptScope(receipt, slice) {
  if (!slice || !sameIdentifierSet(receipt.planned_resource_refs, slice.declared_resource_refs) ||
      !sameIdentifierSet(receipt.planned_component_refs, slice.declared_component_refs) ||
      !isCanonicalReceiptChecks(receipt.checks)) return false;
  const expectedChecks = slice.planned_checks || [];
  const expectedCheckRefs = expectedChecks.map(check => check?.check_ref);
  if (!sameIdentifierSet(receipt.checks.map(check => check.check_ref), expectedCheckRefs)) return false;
  if (receipt.checks.some(check => check.state === "passed" && !check.evidence_refs.some(evidence =>
    evidence.redaction_class === (expectedChecks.find(planned => planned.check_ref === check.check_ref)?.evidence_requirement === "redacted_evidence_required"
      ? "redacted_evidence" : "metadata_only")))) return false;
  const resolvedDeviations = receipt.deviations.filter(deviation => deviation.review_state === "resolved");
  const resolvedResourceRefs = new Set(resolvedDeviations.flatMap(deviation => deviation.out_of_scope_resource_refs));
  const resolvedComponentRefs = new Set(resolvedDeviations.flatMap(deviation => deviation.out_of_scope_component_refs));
  return receipt.actual_resource_refs.every(ref => slice.declared_resource_refs.includes(ref) || resolvedResourceRefs.has(ref)) &&
    receipt.actual_component_refs.every(ref => slice.declared_component_refs.includes(ref) || resolvedComponentRefs.has(ref));
}

function isCanonicalReceiptPayload(row, envelope, plan, slice) {
  const receipt = row?.receipt;
  const requiredFields = [
    "actual_component_refs", "actual_resource_refs", "artifact_refs", "attribution", "attempt_id", "checks",
    "deviations", "envelope_digest", "evidence_refs", "executor_claim", "independent_verification_required",
    "outcome", "plan_digest", "planned_component_refs", "planned_resource_refs", "reset_reconstruction",
    "schema_version", "slice_ref", "source_evidence",
  ];
  return exactObject(receipt, requiredFields) && row.receipt_digest === canonicalDigest(receipt) &&
    receipt.schema_version === "engineering-slice-receipt.v1" &&
    typeof receipt.attempt_id === "string" && ID.test(receipt.attempt_id) &&
    receipt.attempt_id === row.attempt_id && receipt.outcome === row.outcome &&
    receipt.slice_ref === row.slice_ref && receipt.slice_ref === slice?.slice_ref && receipt.plan_digest === plan?.plan_digest &&
    receipt.envelope_digest === envelope?.envelope_digest && OUTCOMES.has(receipt.outcome) &&
    envelope?.envelope?.envelope_id === `env:${envelope.id}` &&
    envelope.envelope?.request?.job_ref === `job:${envelope.job_id}` &&
    envelope.envelope?.agent_session?.id === `session:${envelope.agent_session_id}` &&
    receipt.independent_verification_required === true &&
    row.executor_actor_active === true && typeof row.executor_actor_slug === "string" && ID.test(row.executor_actor_slug) &&
    isUniqueIdentifierArray(receipt.planned_resource_refs) && isUniqueIdentifierArray(receipt.actual_resource_refs) &&
    isUniqueIdentifierArray(receipt.planned_component_refs) && isUniqueIdentifierArray(receipt.actual_component_refs) &&
    isUniqueIdentifierArray(receipt.artifact_refs) && isTypedEvidenceArray(receipt.evidence_refs) &&
    isCanonicalReceiptDeviations(receipt.deviations) &&
    isCanonicalSourceEvidence(receipt.source_evidence) && isCanonicalResetReconstruction(receipt.reset_reconstruction) &&
    isCanonicalExecutorClaim(receipt.executor_claim) && receipt.executor_claim.claimed_by === row.executor_actor_slug &&
    isCanonicalReceiptAttribution(receipt.attribution, envelope) &&
    isCanonicalReceiptScope(receipt, slice);
}

function isCanonicalCompletedReceipt(row, envelope, plan, slice) {
  const receipt = row?.receipt;
  return isCanonicalReceiptPayload(row, envelope, plan, slice) && receipt.outcome === "claimed_complete" &&
    receipt.artifact_refs.length > 0 && isTypedEvidenceArray(receipt.evidence_refs, true) &&
    isCanonicalReceiptChecks(receipt.checks, true) && isCanonicalReceiptDeviations(receipt.deviations, true);
}

function isCanonicalReviewerFact(row, receiptRow, receiptDeviationRefs, requirePassed = false) {
  const fact = row?.fact;
  const slug = row?.reviewer_actor_slug;
  const validReviewerRef = typeof slug === "string" && ID.test(slug) &&
    [slug, `actor:${slug}`, `reviewer:${slug}`].includes(fact?.reviewer_ref);
  return exactObject(fact, [
    "attempt_id", "evidence_refs", "is_independent", "resolved_deviation_refs",
    "reviewed_deviation_refs", "reviewer_ref", "session_ref", "slice_ref", "state",
  ]) && row?.contract_version === "engineering-review.v1" && row?.reviewer_actor_active === true && validReviewerRef &&
    typeof row.reviewer_actor_id === "string" && row.reviewer_actor_id !== receiptRow.executor_actor_id &&
    fact.attempt_id === receiptRow.attempt_id && fact.slice_ref === receiptRow.slice_ref &&
    ["passed", "failed", "blocked"].includes(fact.state) && row.state === fact.state && fact.is_independent === true &&
    isTypedEvidenceArray(fact.evidence_refs, fact.state === "passed") && isCanonicalReviewerSessionRef(fact.session_ref) &&
    typeof row.reviewer_session_ref === "string" && row.reviewer_session_ref === fact.session_ref &&
    row.reviewer_session_ref !== receiptRow.receipt?.attribution?.session_ref && isCanonicalReviewerSessionRef(row.reviewer_session_ref) &&
    sameIdentifierSet(fact.reviewed_deviation_refs, receiptDeviationRefs) &&
    isUniqueIdentifierArray(fact.resolved_deviation_refs) && fact.resolved_deviation_refs.every(ref => receiptDeviationRefs.includes(ref)) &&
    (!requirePassed || (fact.state === "passed" && sameIdentifierSet(fact.resolved_deviation_refs, receiptDeviationRefs)));
}

function compareCanonicalGeneration(left, right) {
  const leftCreated = Date.parse(String(left?.created_at || ""));
  const rightCreated = Date.parse(String(right?.created_at || ""));
  const leftEpoch = Number.isFinite(leftCreated) ? leftCreated : Number.NEGATIVE_INFINITY;
  const rightEpoch = Number.isFinite(rightCreated) ? rightCreated : Number.NEGATIVE_INFINITY;
  return (leftEpoch === rightEpoch ? 0 : (leftEpoch < rightEpoch ? -1 : 1)) ||
    String(left?.id || "").localeCompare(String(right?.id || ""));
}

function latestReceiptForSlice(facts, source, plan, sliceRef, ToolError) {
  const slicePlan = sourcePlanRow(facts, source, ToolError);
  const slice = plan.slices.find(item => item?.slice_ref === sliceRef);
  if (slicePlan.plan?.plan_digest !== plan.plan_digest || typeof slicePlan.id !== "string" || !slice) return null;
  const workRequestId = source.work.id.replace(/^wr:/, "");
  const envelopes = canonicalEnvelopeRows(facts);
  const lineage = envelopes.filter(envelope => envelope.work_request_id === workRequestId &&
    envelope.slice_plan_id === slicePlan.id && envelope.slice_ref === sliceRef);
  const leaves = lineage.filter(envelope => !envelopes.some(successor =>
    successor.supersedes_envelope_id === envelope.id));
  // A receipt belongs to the one unsuperseded lineage leaf or it cannot carry
  // closure/dependency authority. Missing-receipt successors and malformed
  // forks therefore fence an older reviewed pass immediately.
  if (leaves.length !== 1) return null;
  const leaf = leaves[0];
  const matches = receiptLedgerRows(facts).filter(row => {
    return row.work_request_id === workRequestId && row.slice_ref === sliceRef &&
      row.envelope_id === leaf.id;
  });
  return matches.sort(compareCanonicalGeneration).at(-1) || null;
}

function exactPassedReviewForReceipt(facts, source, receiptRow, sliceRef) {
  const planRow = (facts?.slice_plans || []).find(row => row?.accepted_plan_id === source?.plan?.record_id &&
    row?.accepted_plan_hash === source?.plan?.digest);
  const plan = planRow?.plan;
  const slice = plan?.slices?.find(item => item?.slice_ref === sliceRef);
  const envelope = canonicalEnvelopeRows(facts).find(row => row.id === receiptRow?.envelope_id);
  const workRequestId = source.work.id.replace(/^wr:/, "");
  if (!receiptRow || !slice || receiptRow.work_request_id !== workRequestId || receiptRow.slice_ref !== sliceRef ||
      envelope?.id !== receiptRow.envelope_id || envelope.work_request_id !== workRequestId ||
      envelope.slice_plan_id !== planRow?.id || envelope.slice_ref !== sliceRef ||
      !isCanonicalCompletedReceipt(receiptRow, envelope, plan, slice)) return null;
  const receiptSessionRef = receiptRow.receipt?.attribution?.session_ref;
  const receiptDeviationRefs = resolvedReceiptDeviationRefs(receiptRow.receipt);
  if (typeof receiptRow.executor_actor_id !== "string" || typeof receiptSessionRef !== "string" ||
      !ID.test(receiptSessionRef) || !receiptDeviationRefs) return null;
  const matches = canonicalReviewerRows(facts).filter(row => {
    const fact = row.fact;
    return row.receipt_id === receiptRow.id && row.work_request_id === workRequestId &&
      row.slice_ref === sliceRef && row.state === "passed" &&
      typeof row.reviewer_actor_id === "string" && row.reviewer_actor_id !== receiptRow.executor_actor_id &&
      isCanonicalReviewerFact(row, receiptRow, receiptDeviationRefs, true) &&
      typeof row.reviewer_session_ref === "string" &&
      row.reviewer_session_ref === fact.session_ref && row.reviewer_session_ref !== receiptSessionRef &&
      isCanonicalReviewerSessionRef(row.reviewer_session_ref);
  });
  return matches.sort(compareCanonicalGeneration).at(-1) || null;
}

function dependenciesSatisfied(facts, source, plan, slice, ToolError) {
  const passed = new Set((slice.dependency_refs || []).filter(ref => {
    const receipt = latestReceiptForSlice(facts, source, plan, ref, ToolError);
    return Boolean(exactPassedReviewForReceipt(facts, source, receipt, ref));
  }));
  const missing = (slice.dependency_refs || []).filter(ref => !passed.has(ref));
  if (missing.length) error(ToolError, { error: "engineering_dependencies_not_verified", slice_ref: slice.slice_ref, missing_dependencies: missing });
  return true;
}

function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, "Z"); }

function exactFutureInstant(value) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(value)) return false;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().replace(/\.\d{3}Z$/, "Z") === value && parsed > Date.now();
}

function canonicalInstant(value) {
  const parsed = value instanceof Date ? value.getTime() : Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString().replace(/\.\d{3}Z$/, "Z") : null;
}

const ENGINEERING_SESSION_SOURCE = "0".repeat(40);
const ENGINEERING_SESSION_WORKTREE = "engineering:server-admission";
const ENGINEERING_CLAIM_LEASE_SECONDS = 960;

function hasDispatchRunway(value, minimumSeconds = ENGINEERING_CLAIM_LEASE_SECONDS) {
  const instant = Date.parse(value);
  return Number.isFinite(instant) && instant >= Date.now() + minimumSeconds * 1000;
}

function priorEnvelopeSessionId(priorEnvelope) {
  const fromRow = priorEnvelope?.agent_session_id;
  const fromEnvelope = priorEnvelope?.envelope?.agent_session?.id;
  const fromJson = typeof fromEnvelope === "string" && /^session:([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i.exec(fromEnvelope)?.[1];
  if (!fromJson || (fromRow && fromRow !== fromJson)) return null;
  return fromRow || fromJson;
}

function isExactEngineeringAdmissionSession(session, sliceRef, executorId) {
  return session?.executor_actor_id === executorId &&
    (session.state === "claimed" || session.state === "in_progress") &&
    session.scope_ref === `slice:${sliceRef}` &&
    session.worktree_ref === ENGINEERING_SESSION_WORKTREE &&
    session.source_commit_sha === ENGINEERING_SESSION_SOURCE;
}

export function isCurrentRepositoryWriteEnvelope(row) {
  const envelope = row?.envelope;
  return Boolean(envelope && envelope.schema_version === "execution-envelope.v1" &&
    exactFutureInstant(envelope.expires_at) &&
    exactFutureInstant(envelope.agent_session?.lease_expires_at) &&
    envelope.server_binding?.authority?.read_only === false &&
    envelope.server_binding?.authority?.capability_profile === "capability:engineering-repository-write" &&
    envelope.server_binding?.identity?.agent_principal_id === "agent:codex" &&
    envelope.server_binding?.identity?.runtime_principal === "runtime:codex" &&
    envelope.server_binding?.adapter?.adapter_id === "adapter:codex-desktop" &&
    JSON.stringify(envelope.request?.allowed_actions) === JSON.stringify(ENGINEERING_REPOSITORY_ACTIONS));
}

function isDispatchableCurrentEnvelope(row, agentSessionLeaseExpiresAt) {
  const envelope = row?.envelope;
  const issued = Date.parse(envelope?.issued_at);
  const expiry = Date.parse(envelope?.expires_at);
  return isCurrentRepositoryWriteEnvelope(row) &&
    envelope.envelope_id === `env:${row?.id}` &&
    envelope.work_request_id === `wr:${row?.work_request_id}` &&
    envelope.request?.job_ref === `job:${row?.job_id}` &&
    envelope.issued_at === canonicalInstant(row?.issued_at) &&
    envelope.expires_at === canonicalInstant(row?.expires_at) &&
    Number.isFinite(issued) && Number.isFinite(expiry) && expiry > issued && expiry - issued <= 30 * 60 * 1000 &&
    hasDispatchRunway(envelope.expires_at, 930) &&
    envelope.agent_session.lease_expires_at === envelope.expires_at &&
    envelope.agent_session.lease_expires_at === agentSessionLeaseExpiresAt &&
    envelope.agent_session.id === `session:${row?.agent_session_id}`;
}

export function buildCodexEnvelope({ source, plan, slice, jobId, sessionId, actor, envelopeId = globalThis.crypto.randomUUID(), expiresAt = null, replacesEnvelope = null }) {
  const issue = nowIso();
  const expiry = expiresAt || new Date(Date.parse(issue) + 30 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const resources = slice.declared_resource_refs || [];
  const envelope = {
    schema_version: "execution-envelope.v1",
    envelope_id: `env:${envelopeId}`,
    work_request_id: source.work.id,
    plan_revision: { id: source.plan.plan_ref, revision: Number(source.plan.revision), digest: source.plan.digest },
    agent_session: { id: `session:${sessionId}`, lease_expires_at: expiry },
    issued_at: issue, expires_at: expiry,
    state_binding: {
      state_version: Number(source.work.version),
      canonical_record_digest: source.work.canonical_record_digest,
      accepted_resource_revisions: resources.map(resource_ref => ({ resource_ref, revision_ref: `revision:${source.work.version}`, digest: canonicalDigest({ resource_ref, version: source.work.version }) })),
      compare_and_swap_required: true,
    },
    phase_binding: { phase_id: `phase:${slice.slice_ref}`, session_affinity: "fresh_native_session_required", switch_conditions: ["verified_checkpoint", "phase_boundary"], native_session_transfer: "semantic_state_only" },
    evaluation_context: {
      experiment_arm: "audited_state_routed_executors", auditor_mode: "diverse_read_only_auditor",
      evaluation_kernel_ref: "kernel:engineering-passport-v1", workflow_rubric_digest: plan.plan_digest,
      case_set_digest: canonicalDigest({ work_request: source.work.id, slice: slice.slice_ref }),
    },
    request: {
      job_ref: `job:${jobId}`, input_digest: canonicalDigest({ work_request: source.work.id, plan: plan.plan_digest, slice: slice.slice_ref }),
      data_class: "metadata_only", allowed_actions: [...ENGINEERING_REPOSITORY_ACTIONS],
      declared_expectations: {
        plan_step_refs: slice.declared_plan_step_refs || [], component_refs: slice.declared_component_refs || [],
        resource_refs: resources, component_dependencies: [],
      },
    },
    server_binding: {
      identity: {
        organization_tenant_id: "tenant:carr-internal", sponsoring_human_id: actor.sponsoring_human_slug ? `human:${actor.sponsoring_human_slug}` : "human:joe",
        agent_principal_id: "agent:codex", runtime_principal: "runtime:codex", personal_brain_scope: "brain:shared",
        personal_brain_version: "brain:shared-v1", personal_rule_count: 0, derived_by: "server_identity_resolution", client_mutable: false,
      },
      authority: { environment: "rehearsal", risk_class: slice.risk_class || "R1", capability_profile: "capability:engineering-repository-write", capability_grant_ref: `grant:engineering-codex-repository-v1:${sessionId}`, read_only: false, derived_by: "server_capability_resolution", client_mutable: false },
      adapter: { surface: "codex_desktop", adapter_id: "adapter:codex-desktop", adapter_version: "v1", harness_id: "harness:codex", harness_version: "v1", provider_id: "provider:openai", model_id: "model:codex", native_session_ref: `native:codex:${sessionId}`, configuration_fingerprint: canonicalDigest({ adapter: "codex", model: "codex" }) },
    },
    handoff: replacesEnvelope ? {
      mode: "replacement", replaces_agent_session_id: replacesEnvelope.agent_session.id,
      capability_inherited: false,
      checkpoint_ref: `checkpoint:authority-reissue:${replacesEnvelope.envelope_id.replace(/^env:/, "")}`,
      native_session_transfer: "semantic_state_only",
    } : { mode: "original", replaces_agent_session_id: null, capability_inherited: false, checkpoint_ref: null, native_session_transfer: "semantic_state_only" },
  };
  return envelope;
}

export function validateReceiptBinding(receipt, envelope, slice, actor, ToolError) {
  if (!receipt || typeof receipt !== "object") error(ToolError, { error: "engineering_receipt_invalid" });
  if (receipt.schema_version !== "engineering-slice-receipt.v1") error(ToolError, { error: "engineering_receipt_schema_invalid" });
  for (const field of ["attribution", "planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "checks", "artifact_refs", "evidence_refs", "deviations", "source_evidence"])
    if (!(field in receipt)) error(ToolError, { error: "engineering_receipt_missing_field", field });
  if (!Array.isArray(receipt.checks) || receipt.checks.length < 1 || !Array.isArray(receipt.evidence_refs) || !Array.isArray(receipt.deviations))
    error(ToolError, { error: "engineering_receipt_typed_fields_invalid" });
  if (receipt.envelope_digest !== envelope.envelope_digest) error(ToolError, { error: "engineering_receipt_envelope_mismatch" });
  if (receipt.slice_ref !== slice.slice_ref || receipt.plan_digest !== slice.plan_digest) error(ToolError, { error: "engineering_receipt_slice_binding_mismatch" });
  if (!OUTCOMES.has(receipt.outcome) || receipt.independent_verification_required !== true) error(ToolError, { error: "engineering_receipt_outcome_invalid" });
  if (receipt.reset_reconstruction?.fresh_session !== true || receipt.reset_reconstruction?.inherited_transcript_used !== false) error(ToolError, { error: "engineering_receipt_fresh_session_required" });
  if (receipt.executor_claim?.claimed_by !== actor.slug) error(ToolError, { error: "engineering_receipt_executor_mismatch" });
  return receipt;
}

export function closureProjection(facts, ToolError) {
  const source = sourceParts(facts.source, ToolError);
  const plan = requirePlan(sourcePlanRow(facts, source, ToolError).plan, ToolError);
  const planCurrent = planMatchesCurrentSource(plan, source);
  // Facts deliberately retain their canonical ledger wrappers.  Flattening a
  // receipt loses its immutable receipt_id and lets an unrelated review with a
  // coincident attempt label satisfy a later generation.
  const envelopes = canonicalEnvelopeRows(facts);
  const envelopesById = new Map(envelopes.map(row => [row.id, row]));
  const receiptRows = canonicalReceiptRows(facts).filter(row => {
    const envelope = envelopesById.get(row.envelope_id);
    const slice = plan.slices.find(item => item?.slice_ref === row.slice_ref);
    return Boolean(slice && isCanonicalReceiptPayload(row, envelope, plan, slice));
  });
  const reviewerRows = canonicalReviewerRows(facts).filter(row => {
    const receiptRow = receiptRows.find(receipt => receipt.id === row.receipt_id);
    const receiptDeviationRefs = receiptRow && canonicalReceiptDeviationRefs(receiptRow.receipt);
    return Boolean(receiptRow && receiptDeviationRefs &&
      isCanonicalReviewerFact(row, receiptRow, receiptDeviationRefs));
  });
  const latest = new Map(plan.slices.map(slice => [slice.slice_ref,
    latestReceiptForSlice(facts, source, plan, slice.slice_ref, ToolError)]));
  const states = plan.slices.map(slice => {
    const receiptRow = latest.get(slice.slice_ref) || null;
    const receipt = receiptRows.find(row => row.id === receiptRow?.id)?.receipt || null;
    const reviewRow = planCurrent ? exactPassedReviewForReceipt(facts, source, receiptRow, slice.slice_ref) : null;
    const dependenciesVerified = planCurrent && (slice.dependency_refs || []).every(ref =>
      Boolean(exactPassedReviewForReceipt(facts, source, latest.get(ref), ref)));
    const state = !receiptRow ? (dependenciesVerified ? "eligible" : "blocked")
      : reviewRow ? "verified_complete"
        : receiptRow.outcome === "failed" || receiptRow.outcome === "reopened" ? "reopened" : "claimed";
    return { slice_ref: slice.slice_ref, ordinal: slice.ordinal, dependency_refs: slice.dependency_refs || [], state,
      planned_check_refs: (slice.planned_checks || []).map(check => check.check_ref), deviation_refs: (receipt?.deviations || []).map(deviation => typeof deviation === "string" ? deviation : deviation.deviation_ref).filter(Boolean),
      manual_qa_required: slice.manual_qa_required, release_requirement: slice.release_requirement };
  });
  const complete = planCurrent && states.every(row => row.state === "verified_complete");
  const evidence = receiptRows.flatMap(row => row.receipt?.evidence_refs || []).filter(item => item && typeof item === "object");
  const unresolved = states.filter(row => row.state !== "verified_complete").map(row => row.slice_ref);
  const disposition = (state, note) => ({ state, evidence_refs: evidence, note });
  const projection = {
    schema_version: "engineering-passport.v1",
    work_request: plan.work_request,
    accepted_plan_revision: plan.accepted_plan_revision,
    plan_digest: plan.plan_digest,
    slice_plan: plan,
    execution_envelopes: envelopes.map(row => row.envelope || row),
    slices: states,
    receipts: receiptRows.map(row => row.receipt),
    reviewer_facts: reviewerRows.map(row => row.fact),
    qa_facts: [],
    operator_receipt: { what_changed: [], why: "derived from the accepted plan and typed execution evidence", evidence_refs: evidence, deviations: [], remaining_risk: unresolved, manual_qa_items: [] },
    closure: {
      work: disposition(complete ? "complete" : "unresolved", complete ? "all planned slices have a bound receipt and independent pass" : "one or more planned slices remain unresolved"),
      proof: disposition(complete ? "complete" : "unresolved", complete ? "all receipts are independently reviewed" : "receipts are executor claims until independently reviewed"),
      explanation: disposition(complete ? "complete" : "unresolved", "derived from canonical persisted facts"),
      release: disposition(complete ? "complete" : "unresolved", complete ? "all required slices are verified" : "release remains closed until closure is complete"),
      learning: { state: "unresolved", route: null, evidence_refs: evidence, note: "learning remains a proposal/disposition seam" },
    },
    closure_state: complete ? "complete" : "blocked",
    stale_conflict: planCurrent ? { state: "none", reason: null } :
      { state: "stale", reason: "current Work Request or accepted plan no longer matches the registered slice plan" },
  };
  projection.projection_digest = canonicalDigest(projection);
  return projection;
}

export async function runCodexSlice({ dispatchEnvelope, desk, envelope, task }) {
  if (envelope?.server_binding?.adapter?.surface !== "codex_desktop")
    throw new Error("engineering adapter unsupported: only codex_desktop is enabled in v1");
  if (typeof dispatchEnvelope !== "function") throw new Error("engineering Codex adapter requires dispatchEnvelope");
  return dispatchEnvelope(desk, envelope, task, { fresh: true });
}

// These functions are intentionally usable by both an MCP adapter (writer
// transaction) and the supervised worker (jobs transaction).  They return
// only server-derived bindings; callers never select identity, authority,
// provider, model, or native session continuity.
export async function admitEngineeringSlice(c, actor, args, ToolError, writeEvent) {
  if (typeof writeEvent !== "function") throw new TypeError("engineering admission requires an event writer");
  exactAuthorityFree(args, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  const workRequest = text(args.work_request, "work_request", ToolError);
  const sliceRef = id(args.slice_ref, "slice_ref", ToolError);
  const sourceResult = await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRequest]);
  if (!sourceResult.rows.length || !sourceResult.rows[0].facts?.source) error(ToolError, { error: "engineering_work_request_not_found_or_not_ready" });
  let facts = sourceResult.rows[0].facts;
  let source = sourceParts(facts.source, ToolError);
  let plan = sourcePlan(facts, source, ToolError);
  let slice = sliceFor(plan, sliceRef, ToolError);
  dependenciesSatisfied(facts, source, plan, slice, ToolError);
  const locatorSourceDigest = canonicalDigest(facts.source);
  let priorEnvelopes = (facts.envelopes || [])
    .filter(row => row.slice_ref === sliceRef && row.accepted_plan_id === source.plan.record_id)
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  let priorEnvelope = priorEnvelopes.at(-1) || null;
  let priorSessionId = null;
  let priorBinding = null;
  let session = null;
  let executor = null;
  let priorSessionIsActive = false;

  // Locator reads above are intentionally unlocked. Existing mutable authority
  // is acquired in the global session -> exact actor -> lineage order before
  // any admission serialization or replacement decision.
  if (priorEnvelope) {
    const priorEnvelopeId = uuid(priorEnvelope.id, "prior_envelope.id", ToolError);
    const priorJobId = uuid(priorEnvelope.job_id, "prior_envelope.job_id", ToolError);
    priorBinding = (await c.query(
      `select e.id, e.job_id, e.work_request_id, e.accepted_plan_id, e.slice_plan_id,
              e.slice_ref, e.agent_session_id, e.envelope, j.state as job_state
         from ops.engineering_execution_envelope e
         join ops.job j on j.id=e.job_id
        where e.id=$1::uuid and e.job_id=$2::uuid`,
      [priorEnvelopeId, priorJobId],
    )).rows[0];
    if (!priorBinding || priorBinding.slice_ref !== sliceRef ||
        priorBinding.work_request_id !== source.work.id.replace(/^wr:/, "") ||
        priorBinding.accepted_plan_id !== source.plan.record_id ||
        (priorEnvelope.slice_plan_id && priorEnvelope.slice_plan_id !== priorBinding.slice_plan_id))
      error(ToolError, { error: "engineering_prior_envelope_binding_invalid", envelope_id: priorEnvelopeId });
    const factSessionId = priorEnvelopeSessionId(priorEnvelope);
    const rowSessionId = priorEnvelopeSessionId({ agent_session_id: priorBinding.agent_session_id, envelope: priorBinding.envelope });
    if (!factSessionId || !rowSessionId || factSessionId !== rowSessionId)
      error(ToolError, { error: "engineering_session_conflict", envelope_id: priorEnvelopeId });
    priorSessionId = rowSessionId;
    session = (await c.query(
      `select id, work_request_id, executor_actor_id, state, lease_expires_at, scope_ref, worktree_ref, source_commit_sha
         from ops.capability_agent_session
        where id=$1::uuid for update`,
      [priorSessionId],
    )).rows[0];
    priorSessionIsActive = ["claimed", "in_progress"].includes(session?.state);
    // Completed/cancelled sessions remain immutable, but are still valid
    // provenance for a fresh envelope generation on the same slice lineage.
    const priorSessionIsTerminal = ["completed", "cancelled"].includes(session?.state);
    if (!session || session.id !== priorSessionId ||
        session.work_request_id !== source.work.id.replace(/^wr:/, "") ||
        (!priorSessionIsActive && !priorSessionIsTerminal) ||
        session.scope_ref !== `slice:${sliceRef}` ||
        session.worktree_ref !== ENGINEERING_SESSION_WORKTREE ||
        session.source_commit_sha !== ENGINEERING_SESSION_SOURCE)
      error(ToolError, { error: "engineering_session_conflict", envelope_id: priorEnvelopeId });
    executor = (await c.query(
      "select id, slug from actor where id=$1::uuid and slug='codex' and active and kind='automation' for share",
      [session.executor_actor_id],
    )).rows[0];
    if (!executor || executor.id !== session.executor_actor_id)
      error(ToolError, { error: "engineering_codex_actor_not_provisioned" });
  } else {
    const openSessions = (await c.query(
      `select id, work_request_id, executor_actor_id, state, lease_expires_at, scope_ref, worktree_ref, source_commit_sha
         from ops.capability_agent_session
        where work_request_id=$1::uuid and state not in ('completed','cancelled')
        order by created_at desc for update`,
      [source.work.id.replace(/^wr:/, "")],
    )).rows;
    if (openSessions.length > 1) error(ToolError, { error: "engineering_session_conflict" });
    session = openSessions[0] || null;
    executor = (await c.query(
      session
        ? "select id, slug from actor where id=$1::uuid and slug='codex' and active and kind='automation' for share"
        : "select id, slug from actor where slug=$1 and active and kind='automation' for share",
      [session ? session.executor_actor_id : "codex"],
    )).rows[0];
    if (!executor) error(ToolError, { error: "engineering_codex_actor_not_provisioned" });
    if (session && (!session.lease_expires_at || !hasDispatchRunway(session.lease_expires_at)))
      error(ToolError, { error: "engineering_session_not_lease_bound" });
    if (session && !isExactEngineeringAdmissionSession(session, sliceRef, executor.id))
      error(ToolError, { error: "engineering_session_conflict" });
  }

  await c.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [`engineering-slice:${source.plan.digest}:${sliceRef}`]);
  let refreshed = await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRequest]);
  facts = refreshed.rows[0]?.facts;
  if (!facts?.source || canonicalDigest(facts.source) !== locatorSourceDigest)
    error(ToolError, { error: "engineering_admission_serialization_restart" });
  source = sourceParts(facts.source, ToolError);
  plan = sourcePlan(facts, source, ToolError);
  slice = sliceFor(plan, sliceRef, ToolError);
  dependenciesSatisfied(facts, source, plan, slice, ToolError);
  priorEnvelopes = (facts.envelopes || [])
    .filter(row => row.slice_ref === sliceRef && row.accepted_plan_id === source.plan.record_id)
    .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  const serializedPriorEnvelope = priorEnvelopes.at(-1) || null;
  if ((serializedPriorEnvelope?.id || null) !== (priorEnvelope?.id || null))
    error(ToolError, { error: "engineering_admission_serialization_restart" });
  priorEnvelope = serializedPriorEnvelope;

  if (priorEnvelope) {
    const priorSlicePlanId = uuid(priorBinding.slice_plan_id, "prior_envelope.slice_plan_id", ToolError);
    await c.query("select pg_advisory_xact_lock(hashtextextended($1, 0))", [`engineering-envelope:${priorSlicePlanId}:${sliceRef}`]);
    // A terminal session is a predecessor, never a replay candidate. Active
    // sessions retain the existing currentness and dispatch-runway boundary.
    if (priorSessionIsActive) {
      const currentness = await c.query("select ops.engineering_envelope_currentness($1::uuid,$2::uuid) as currentness", [priorEnvelope.id, priorEnvelope.job_id]);
      const priorJobReplayable = ["queued", "retry_wait", "running"].includes(priorBinding.job_state);
      if (priorJobReplayable && currentness.rows[0]?.currentness?.eligible === true &&
          currentness.rows[0]?.currentness?.dispatch_runway_sufficient === true)
        return { ok: true, replayed: true, envelope: priorEnvelope.envelope, envelope_id: priorEnvelope.id, job_id: priorEnvelope.job_id };
      if (priorJobReplayable && currentness.rows[0]?.currentness?.eligible === true)
        error(ToolError, { error: "engineering_envelope_insufficient_runway", envelope_id: priorEnvelope.id });
    }
  }

  // engineering_slice_plan is append-only: its trigger forbids UPDATE/DELETE,
  // and carr_writer intentionally has SELECT but no UPDATE authority. A row
  // lock therefore adds no currentness guarantee here and makes this governed
  // admission impossible (PostgreSQL row locks require UPDATE privilege).
  // The transaction-scoped advisory lock plus the repeated passport digest
  // check above are the serialization boundary; keep this lookup read-only.
  const registeredPlan = await c.query(
    `select id from ops.engineering_slice_plan
      where accepted_plan_id=$1::uuid and plan_digest=$2::text`,
    [source.plan.record_id, plan.plan_digest],
  );
  if (!registeredPlan.rows.length) error(ToolError, { error: "engineering_slice_plan_not_registered" });
  const lockedWork = await c.query("select id from ops.work_request where id=$1::uuid for share", [source.work.id.replace(/^wr:/, "")]);
  if (!lockedWork.rows.length) error(ToolError, { error: "engineering_work_request_not_found_or_not_ready" });
  refreshed = await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRequest]);
  facts = refreshed.rows[0]?.facts;
  if (!facts?.source || canonicalDigest(facts.source) !== locatorSourceDigest)
    error(ToolError, { error: "engineering_admission_serialization_restart" });
  source = sourceParts(facts.source, ToolError);
  plan = sourcePlan(facts, source, ToolError);
  slice = sliceFor(plan, sliceRef, ToolError);
  dependenciesSatisfied(facts, source, plan, slice, ToolError);

  if (priorEnvelope) {
    if (priorSessionIsActive) {
      await c.query(
        `update ops.capability_agent_session set state='cancelled', cancelled_at=now(), version=version+1
          where id=$1::uuid and work_request_id=$2::uuid and state not in ('completed','cancelled')`,
        [priorSessionId, source.work.id.replace(/^wr:/, "")]);
    }
    session = null;
  }
  const sessionExpiry = new Date(Date.now() + 30 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  if (!session) {
    const created = await c.query(
      `insert into ops.capability_agent_session
         (work_request_id, executor_actor_id, created_by_actor_id, source_commit_sha, worktree_ref, scope_ref, lease_expires_at)
       values ($1,$2,$3,$4,$5,$6,$7::timestamptz) returning id, executor_actor_id, state, lease_expires_at`,
      [source.work.id.replace(/^wr:/, ""), executor.id, actor.id, ENGINEERING_SESSION_SOURCE, ENGINEERING_SESSION_WORKTREE, `slice:${sliceRef}`, sessionExpiry]);
    session = created.rows[0];
  }
  const job = (await c.query(
    "select * from ops.engineering_enqueue_slice_job($1::text,$2::text,$3::text,$4::text,$5::integer)",
    [source.work.ref, sliceRef, plan.plan_digest, args.idempotency_key, priorEnvelopes.length + 1])).rows[0];
  if (!job) error(ToolError, { error: "engineering_job_admission_failed" });
  const expiry = session.lease_expires_at ? new Date(session.lease_expires_at).toISOString().replace(/\.\d{3}Z$/, "Z") : sessionExpiry;
  const envelopeId = globalThis.crypto.randomUUID();
  const envelope = buildCodexEnvelope({ source, plan, slice, jobId: job.id, sessionId: session.id, actor, envelopeId, expiresAt: expiry,
    replacesEnvelope: priorEnvelope?.envelope || null });
  const envelopeDigest = canonicalDigest(envelope);
  const inserted = await c.query(
    `insert into ops.engineering_execution_envelope
      (id,job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
       state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at,
       supersedes_envelope_id,supersession_reason)
     values ($1,$2,$3,$4,(select id from ops.engineering_slice_plan where accepted_plan_id=$4),$5,$6,
       $7,$8,$9,$10::jsonb,$11::timestamptz,$12::timestamptz,$13,$14)
     returning *`,
    [envelopeId, job.id, source.work.id.replace(/^wr:/, ""), source.plan.record_id, sliceRef, session.id,
      Number(source.work.version), source.work.canonical_record_digest, envelopeDigest,
      JSON.stringify(envelope), envelope.issued_at, envelope.expires_at, priorEnvelope?.id || null,
      priorEnvelope ? "server-derived replacement of expired or non-executable envelope" : null]);
  if (!inserted.rows.length) error(ToolError, { error: "engineering_envelope_admission_failed" });
  const row = inserted.rows[0];
  await writeEvent(c, actor, "admit-engineering-slice", "ops_work_request", source.work.id.replace(/^wr:/, ""), {
    new: { engineering_job_id: job.id, envelope_id: row.id, supersedes_envelope_id: priorEnvelope?.id || null,
      slice_ref: sliceRef, plan_digest: plan.plan_digest }, idempotency_key: args.idempotency_key,
  });
  return { ok: true, replayed: false, job_id: job.id, envelope_id: row.id, envelope_digest: envelopeDigest,
    supersedes_envelope_id: priorEnvelope?.id || null, agent_session_id: session.id, slice_ref: sliceRef };
}

export async function claimEngineeringSlice(c, worker, limit = 1) {
  const claimed = await c.query("select * from ops.engineering_claim_slice($1::text,$2::integer,960)", [worker, limit]);
  const rows = [];
  for (const job of claimed.rows) {
    // Sponsored-authority recovery may create an immutable successor envelope
    // for the same idempotent job.  Never let the controller's choice depend
    // on an unspecified row order (which could resend an expired/read-only
    // predecessor); the most recently issued envelope is the only candidate
    // that may receive this fresh lease.
    // carr_jobs intentionally cannot read capability sessions directly.  The
    // SECURITY DEFINER controller binding below supplies the revalidated
    // session lease after enforcing the canonical lock/currentness boundary.
    const bound = await c.query(
      "select e.* from ops.engineering_execution_envelope e where e.job_id=$1 order by e.issued_at desc, e.id desc limit 1",
      [job.job_id],
    );
    if (!bound.rows.length || !bound.rows[0].envelope) {
      rows.push({ ...job, controller_error: "engineering_envelope_not_found" });
      continue;
    }
    const envelopeRow = bound.rows[0];
    const bindingResult = await c.query(
      "select ops.engineering_controller_binding($1::uuid,$2::uuid,$3::uuid) as binding",
      [envelopeRow.id, job.job_id, job.lease_token],
    );
    const binding = bindingResult.rows[0]?.binding;
    if (!binding || typeof binding !== "object" ||
        !isDispatchableCurrentEnvelope(envelopeRow, binding.agent_session_lease_expires_at)) {
      rows.push({ ...job, envelope: envelopeRow.envelope, envelope_id: envelopeRow.id,
        envelope_digest: envelopeRow.envelope_digest,
        controller_error: binding && typeof binding === "object"
          ? "engineering_envelope_currentness_invalid" : "engineering_controller_binding_missing" });
      continue;
    }
    rows.push({ ...job, envelope: envelopeRow.envelope, envelope_id: envelopeRow.id,
      envelope_digest: envelopeRow.envelope_digest, controller_binding: binding });
  }
  return rows;
}

export async function submitEngineeringReceipt(c, claimed, receipt, actor, ToolError) {
  if (!claimed?.job_id || !claimed.lease_token || !claimed.envelope_id) error(ToolError, { error: "engineering_claim_required" });
  const envelopeRow = (await c.query("select * from ops.engineering_execution_envelope where id=$1", [claimed.envelope_id])).rows[0];
  if (!envelopeRow) error(ToolError, { error: "engineering_envelope_not_found" });
  const workRef = claimed?.payload?.work_request;
  if (typeof workRef !== "string" || !workRef.trim())
    error(ToolError, { error: "engineering_work_request_binding_missing" });
  const facts = (await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRef])).rows[0]?.facts;
  const source = facts ? sourceParts(facts.source, ToolError) : null;
  if (!source || source.work.id !== `wr:${envelopeRow.work_request_id}`)
    error(ToolError, { error: "engineering_work_request_binding_mismatch" });
  const plan = source ? sourcePlan(facts, source, ToolError) : null;
  const slice = plan ? sliceFor(plan, envelopeRow.slice_ref, ToolError) : { slice_ref: envelopeRow.slice_ref, plan_digest: null };
  if (plan && receipt.plan_digest !== plan.plan_digest) error(ToolError, { error: "engineering_receipt_plan_mismatch" });
  validateReceiptBinding(receipt, { ...envelopeRow.envelope, envelope_digest: envelopeRow.envelope_digest }, { ...slice, plan_digest: receipt.plan_digest }, actor, ToolError);
  const receiptDigest = canonicalDigest(receipt);
  const inserted = await c.query(
    "select * from ops.engineering_finalize_slice_receipt($1::uuid,$2::uuid,$3::jsonb,$4::text,$5::uuid)",
    [envelopeRow.id, claimed.lease_token, JSON.stringify(receipt), receiptDigest, actor.id]);
  if (!inserted.rows.length) error(ToolError, { error: "engineering_attempt_or_lease_mismatch" });
  return { ok: receipt.outcome === "claimed_complete", receipt_id: inserted.rows[0].id,
    receipt_digest: receiptDigest, outcome: receipt.outcome };
}

function controllerActor(claim, ToolError) {
  const binding = claim?.controller_binding;
  const actor = binding?.executor_actor;
  if (!actor || typeof actor !== "object") error(ToolError, { error: "engineering_controller_binding_invalid" });
  const actorId = uuid(actor.id, "controller_binding.executor_actor.id", ToolError);
  const actorSlug = id(actor.slug, "controller_binding.executor_actor.slug", ToolError);
  // The first live adapter is deliberately fixed.  A controller cannot turn a
  // job into another principal by supplying a different actor at invocation.
  if (actorSlug !== "codex") error(ToolError, { error: "engineering_executor_not_supported", executor: actorSlug });
  return { id: actorId, slug: actorSlug };
}

function controllerPlan(claim, ToolError) {
  const binding = claim?.controller_binding;
  const plan = requirePlan(binding?.slice_plan, ToolError);
  const slice = sliceFor(plan, claim?.payload?.slice_ref, ToolError);
  if (binding.envelope_id !== claim.envelope_id || binding.envelope_digest !== claim.envelope_digest
      || binding.slice_ref !== slice.slice_ref || binding.plan_digest !== plan.plan_digest)
    error(ToolError, { error: "engineering_controller_binding_mismatch" });
  if (canonicalInstant(binding.job_lease_expires_at) !== binding.job_lease_expires_at ||
      !hasDispatchRunway(binding.job_lease_expires_at, 930))
    error(ToolError, { error: "engineering_controller_job_lease_runway_invalid" });
  return { plan, slice, jobLeaseExpiresAt: binding.job_lease_expires_at };
}

async function failEngineeringClaim(c, claim, failureClass, detail) {
  const failure = await c.query(
    "select ops.engineering_fail_claim($1::uuid,$2::uuid,$3::text,$4::text) as state",
    [claim.job_id, claim.lease_token, failureClass, String(detail || "engineering controller failure").slice(0, 1000)],
  );
  return { job_id: claim.job_id, state: failure.rows[0]?.state || "failure_unreadable",
    failure_class: failureClass };
}

async function controllerReadback(c, claim, ToolError) {
  const work = claim?.payload?.work_request;
  if (typeof work !== "string" || !work.trim()) return { state: "unavailable", reason: "work_request_missing" };
  const row = await c.query("select ops.engineering_passport_facts($1::text) as facts", [work]);
  if (!row.rows[0]?.facts?.source) return { state: "unavailable", reason: "passport_facts_missing" };
  const passport = closureProjection(row.rows[0].facts, ToolError);
  const slice = passport.slices.find(item => item.slice_ref === claim.payload.slice_ref);
  return { state: "read", work_request: passport.work_request.id, slice_ref: claim.payload.slice_ref,
    slice_state: slice?.state || "unknown", projection_digest: passport.projection_digest };
}

// Dedicated controller entrypoint: claim from the existing job ledger, invoke
// the fresh-native Codex adapter, then persist its typed claim through the
// lease-bound receipt function.  The controller supplies the already audited
// room-bridge dispatcher; no Claude fallback or inherited transcript path is
// permitted here.
export async function runEngineeringWorker({ c, worker, desk, dispatchEnvelope, limit = 1, ToolError }) {
  if (typeof dispatchEnvelope !== "function") throw new Error("engineering worker requires the Codex room-bridge dispatcher");
  // Maintenance runs as separate autocommit statements.  It must not acquire
  // queue locks inside the scoped claim transaction, whose lock order begins
  // with the exact capability session and actor authority.
  await c.query("select ops.reap_expired_jobs()");
  await c.query("select ops.engineering_retire_permanently_ineligible_jobs()");
  const claims = await claimEngineeringSlice(c, worker, limit);
  const results = [];
  for (const claim of claims) {
    if (claim.definition_key !== "engineering-slice") {
      results.push(await failEngineeringClaim(c, claim, "engineering_definition_mismatch", "claimed row was not engineering-slice"));
      continue;
    }
    let persisted;
    try {
      if (claim.controller_error) error(ToolError, { error: claim.controller_error });
      const actor = controllerActor(claim, ToolError);
      const { plan, slice, jobLeaseExpiresAt } = controllerPlan(claim, ToolError);
      const task = { ...(claim.payload || {}), work_request: plan.work_request.id,
        job_ref: `job:${claim.job_id}`,
        attempt_id: `attempt:${claim.attempt}`, claim_lease_expires_at: jobLeaseExpiresAt,
        engineering_plan: plan, engineering_slice: slice };
      const receipt = await runCodexSlice({ dispatchEnvelope, desk, envelope: claim.envelope, task });
      if (!receipt || typeof receipt !== "object") throw new Error("Codex worker returned no typed receipt");
      persisted = await submitEngineeringReceipt(c, claim, receipt, actor, ToolError);
    } catch (cause) {
      try {
        results.push(await failEngineeringClaim(c, claim, "engineering_dispatch_failed",
          cause?.message || cause?.error || "engineering dispatch failed"));
      } catch {
        results.push({ job_id: claim.job_id, state: "cleanup_deferred", failure_class: "engineering_dispatch_failed" });
      }
      continue;
    }
    let operatorReadback;
    try {
      operatorReadback = await controllerReadback(c, claim, ToolError);
    } catch {
      operatorReadback = { state: "unavailable", reason: "readback_failed" };
    }
    results.push({ ...persisted, operator_readback: operatorReadback });
  }
  return { claimed: claims.length, completed: results.filter(result => result.ok === true).length, results };
}

export async function recordEngineeringReview(c, actor, args, ToolError, writeEvent) {
  if (typeof writeEvent !== "function") throw new TypeError("engineering review requires an event writer");
  exactAuthorityFree(args, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  const receiptId = uuid(args.receipt_id, "receipt_id", ToolError);
  const fact = args.fact;
  const fields = ["attempt_id", "evidence_refs", "is_independent", "resolved_deviation_refs", "reviewed_deviation_refs", "reviewer_ref", "session_ref", "slice_ref", "state"];
  if (!fact || typeof fact !== "object" || Array.isArray(fact) ||
      Object.keys(fact).sort().join(",") !== fields.join(",") ||
      !["passed", "failed", "blocked"].includes(fact.state))
    error(ToolError, { error: "engineering_reviewer_fact_invalid" });
  if (!Array.isArray(fact.evidence_refs) || !Array.isArray(fact.reviewed_deviation_refs) || !Array.isArray(fact.resolved_deviation_refs))
    error(ToolError, { error: "engineering_reviewer_fact_typed_fields_invalid" });
  id(fact.attempt_id, "fact.attempt_id", ToolError);
  id(fact.slice_ref, "fact.slice_ref", ToolError);
  id(fact.reviewer_ref, "fact.reviewer_ref", ToolError);
  id(fact.session_ref, "fact.session_ref", ToolError);
  if (!isCanonicalReviewerSessionRef(fact.session_ref))
    error(ToolError, { error: "engineering_reviewer_session_invalid" });
  for (const [index, item] of fact.evidence_refs.entries())
    evidence(item, `fact.evidence_refs[${index}]`, ToolError);
  for (const field of ["reviewed_deviation_refs", "resolved_deviation_refs"])
    for (const [index, item] of fact[field].entries()) id(item, `fact.${field}[${index}]`, ToolError);
  const receipt = (await c.query("select * from ops.engineering_slice_receipt where id=$1", [receiptId])).rows[0];
  if (!receipt) error(ToolError, { error: "engineering_receipt_not_found" });
  if (receipt.executor_actor_id === actor.id || fact.attempt_id !== receipt.attempt_id ||
      fact.slice_ref !== receipt.slice_ref || fact.is_independent !== true ||
      ![actor.slug, `actor:${actor.slug}`, `reviewer:${actor.slug}`].includes(fact.reviewer_ref) ||
      fact.session_ref === receipt.receipt?.attribution?.session_ref)
    error(ToolError, { error: "engineering_independent_review_required" });
  if (fact.state === "passed" && receipt.outcome !== "claimed_complete")
    error(ToolError, { error: "engineering_review_receipt_not_complete" });
  if (fact.state === "passed" && fact.evidence_refs.length === 0)
    error(ToolError, { error: "engineering_review_evidence_required" });
  const deviations = Array.isArray(receipt.receipt?.deviations) ? receipt.receipt.deviations : [];
  const deviationRefs = deviations.map(item => item?.deviation_ref).filter(Boolean).sort();
  const reviewed = [...new Set(fact.reviewed_deviation_refs)].sort();
  const resolved = [...new Set(fact.resolved_deviation_refs)].sort();
  if (reviewed.join(",") !== deviationRefs.join(",") || resolved.some(ref => !deviationRefs.includes(ref)))
    error(ToolError, { error: "engineering_review_deviation_coverage_required" });
  if (fact.state === "passed" && (resolved.join(",") !== deviationRefs.join(",") ||
      deviations.some(item => item.review_state !== "resolved" || item.plan_revision_required === true)))
    error(ToolError, { error: "engineering_review_unresolved_deviation" });
  const row = (await c.query(
    `insert into ops.engineering_reviewer_fact
      (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
     values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::uuid) returning *`,
    [receipt.id, receipt.work_request_id, receipt.slice_ref, actor.id, text(fact.session_ref, "fact.session_ref", ToolError), fact.state, JSON.stringify(fact), args.idempotency_key])).rows[0];
  await writeEvent(c, actor, "review-engineering-slice", "ops_work_request", receipt.work_request_id, { new: { reviewer_fact_id: row.id, receipt_id: receipt.id, state: row.state }, idempotency_key: args.idempotency_key });
  return { ok: true, reviewer_fact_id: row.id, state: row.state };
}

export function engineeringRuntimeTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "engineering-passport-source": {
      description: "Read the exact current accepted Work Request and plan binding required to construct an initial Engineering Slice Plan. This is the bootstrap read: it exposes no execution envelope, does not register, assign, dispatch, or grant authority, and remains available before any slice plan exists.",
      inputSchema: { type: "object", additionalProperties: false, properties: { work_request: { type: "string" } }, required: ["work_request"] },
      handler: async (c, _actor, args) => {
        const work = text(args.work_request, "work_request", ToolError);
        const r = await c.query("select ops.engineering_admission_source($1::text) as source", [work]);
        const source = r.rows[0]?.source;
        if (!source?.work_request || !source?.accepted_plan)
          error(ToolError, { error: "engineering_work_request_not_found_or_not_ready" });
        const parts = sourceParts(source, ToolError);
        return {
          schema_version: "engineering-passport-source.v1",
          work_request: parts.work,
          accepted_plan_revision: parts.plan,
        };
      },
    },
    "register-engineering-slice-plan": {
      write: true,
      description: "Register one typed Engineering Slice Plan as an immutable projection of the exact accepted sourced plan. It does not accept, assign, dispatch, or grant authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, work_request: { type: "string" }, plan: { type: "object" }, plan_digest: { type: "string" } }, required: ["idempotency_key", "work_request", "plan", "plan_digest"] },
      handler: async (c, actor, args) => { exactAuthorityFree(args, ToolError); return withEnvelope(c, actor, "register-engineering-slice-plan", args, async () => {
        uuid(args.idempotency_key, "idempotency_key", ToolError); const plan = requirePlan(args.plan, ToolError); const work = text(args.work_request, "work_request", ToolError); const planDigest = digest(args.plan_digest, "plan_digest", ToolError);
        if (plan.plan_digest !== planDigest) error(ToolError, { error: "engineering_slice_plan_digest_mismatch" });
        const r = await c.query("select * from ops.engineering_register_slice_plan($1::text,$2::jsonb,$3::text,$4::uuid)", [work, JSON.stringify(plan), planDigest, args.idempotency_key]);
        if (!r.rows.length) error(ToolError, { error: "engineering_slice_plan_not_registered" });
        const row = r.rows[0]; await writeEvent(c, actor, "register-engineering-slice-plan", "ops_work_request", row.work_request_id, { new: { engineering_slice_plan_id: row.id, plan_digest: row.plan_digest }, idempotency_key: args.idempotency_key });
        return { ok: true, engineering_slice_plan_id: row.id, work_request_id: row.work_request_id, accepted_plan_id: row.accepted_plan_id, plan_digest: row.plan_digest };
      }); },
    },
    "admit-engineering-slice": {
      write: true,
      description: "Admit one eligible DAG slice from the exact accepted plan. The server creates the canonical ops.job, capability session, and immutable execution envelope; caller identity, authority, adapter, and native session continuity are never accepted as input.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, work_request: { type: "string" }, slice_ref: { type: "string" } }, required: ["idempotency_key", "work_request", "slice_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "admit-engineering-slice", args, () => admitEngineeringSlice(c, actor, args, ToolError, writeEvent)),
    },
    "review-engineering-slice": {
      write: true,
      description: "Record one independent typed reviewer fact against a persisted Engineering Slice Receipt. The reviewer must be a different actor from the executor and must provide evidence for a pass.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, receipt_id: { type: "string" }, fact: { type: "object" } }, required: ["idempotency_key", "receipt_id", "fact"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "review-engineering-slice", args, () => recordEngineeringReview(c, actor, args, ToolError, writeEvent)),
    },
    "engineering-passport": {
      description: "Read the canonical typed Engineering Passport projection. Closure is derived from persisted envelopes, receipts, and independent reviewer facts; it is not a task store or authority source.",
      inputSchema: { type: "object", additionalProperties: false, properties: { work_request: { type: "string" } }, required: ["work_request"] },
      handler: async (c, _actor, args) => { const work = text(args.work_request, "work_request", ToolError); const r = await c.query("select ops.engineering_passport_facts($1::text) as facts", [work]); if (!r.rows.length || !r.rows[0].facts?.source) error(ToolError, { error: "engineering_work_request_not_found" }); return closureProjection(r.rows[0].facts, ToolError); },
    },
    "engineering-writer-runtime-preflight": {
      fullOnly: true,
      writerConnection: true,
      description: "Read the effective database identity and the exact Engineering admission privileges through the Worker's DATABASE_URL_WRITER binding. It never returns or fingerprints the credential and executes in a read-only transaction.",
      inputSchema: { type: "object", additionalProperties: false, properties: {}, required: [] },
      handler: async (c) => {
        const row = (await c.query(`select
          session_user::text as session_user,
          current_user::text as current_user,
          current_database()::text as database,
          current_setting('transaction_read_only')::text as transaction_read_only,
          pg_has_role(current_user,'carr_writer','member') as member_carr_writer,
          has_table_privilege(current_user,'ops.engineering_slice_plan','select') as select_engineering_slice_plan,
          has_table_privilege(current_user,'ops.engineering_execution_envelope','insert') as insert_engineering_execution_envelope,
          has_function_privilege(current_user,'ops.engineering_passport_facts(text)','execute') as execute_engineering_passport_facts,
          has_function_privilege(current_user,'ops.engineering_enqueue_slice_job(text,text,text,text,integer)','execute') as execute_engineering_enqueue_slice_job`)).rows[0];
        if (!row) error(ToolError, { error: "engineering_writer_runtime_preflight_unavailable" });
        const checks = {
          identity_is_app_writer: row.session_user === "app_writer" && row.current_user === "app_writer",
          database_is_neondb: row.database === "neondb",
          transaction_is_read_only: row.transaction_read_only === "on",
          member_carr_writer: row.member_carr_writer === true,
          select_engineering_slice_plan: row.select_engineering_slice_plan === true,
          insert_engineering_execution_envelope: row.insert_engineering_execution_envelope === true,
          execute_engineering_passport_facts: row.execute_engineering_passport_facts === true,
          execute_engineering_enqueue_slice_job: row.execute_engineering_enqueue_slice_job === true,
        };
        return {
          ok: Object.values(checks).every(Boolean),
          identity: { session_user: row.session_user, current_user: row.current_user, database: row.database },
          checks,
        };
      },
    },
  };
}
