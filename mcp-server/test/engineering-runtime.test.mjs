import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalDigest,
  ENGINEERING_REPOSITORY_ACTIONS,
  isCurrentRepositoryWriteEnvelope,
  buildCodexEnvelope,
  validateReceiptBinding,
  runCodexSlice,
  requirePlan,
  closureProjection,
  runEngineeringWorker,
  admitEngineeringSlice,
  recordEngineeringReview,
  engineeringRuntimeTools,
} from "../src/engineering-runtime.js";

const digest = value => canonicalDigest(value);
const source = {
  work: {
    id: "wr:11111111-1111-4111-8111-111111111111", ref: "WR-301", version: 3,
    canonical_record_digest: `sha256:${"1".repeat(64)}`,
  },
  plan: {
    record_id: "22222222-2222-4222-8222-222222222222", plan_ref: "plan:301",
    revision: 2, digest: `sha256:${"2".repeat(64)}`,
  },
};
const slice = {
  slice_ref: "slice:one", ordinal: 1, dependency_refs: [],
  declared_resource_refs: ["resource:worktree"], declared_component_refs: ["component:runtime"],
  declared_plan_step_refs: ["step:one"], risk_class: "R1",
};
const plan = { plan_digest: `sha256:${"3".repeat(64)}`, slices: [slice] };
const actor = { id: "33333333-3333-4333-8333-333333333333", slug: "codex", sponsoring_human_slug: "joe" };
class EngineeringToolError extends Error {
  constructor(payload) { super(payload.error || "engineering tool error"); Object.assign(this, payload); }
}

function currentClaimEnvelope(sessionId = "99999999-9999-4999-8999-999999999999") {
  const issued = new Date(Date.now()).toISOString().replace(/\.\d{3}Z$/, "Z");
  const expiry = new Date(Date.parse(issued) + 29 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  return {
    schema_version: "execution-envelope.v1", envelope_id: "env:88888888-8888-4888-8888-888888888888", work_request_id: "wr:11111111-1111-4111-8111-111111111111", issued_at: issued, expires_at: expiry,
    agent_session: { id: `session:${sessionId}`, lease_expires_at: expiry },
    request: { job_ref: "job:66666666-6666-4666-8666-666666666666", allowed_actions: [...ENGINEERING_REPOSITORY_ACTIONS] },
    server_binding: { authority: { read_only: false, capability_profile: "capability:engineering-repository-write" },
      identity: { agent_principal_id: "agent:codex", runtime_principal: "runtime:codex" }, adapter: { surface: "codex_desktop", adapter_id: "adapter:codex-desktop" } },
  };
}

function controllerPlan() {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typed = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typed.plan_digest = digest(typed);
  return typed;
}

function engineeringSlice(sliceRef, ordinal, dependency_refs = []) {
  return {
    slice_ref: sliceRef, ordinal, objective: `Execute ${sliceRef}`, definition_of_done: "A typed receipt exists",
    dependency_refs, declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: `check:${sliceRef}`, failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: `one bounded ${sliceRef}`, forbidden_change_refs: [], concurrency_posture: "serial_after_dependencies", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
}

function typedEngineeringPlan(slices) {
  const typed = {
    schema_version: "engineering-slice-plan.v1",
    work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest },
    accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest },
    slices,
  };
  typed.plan_digest = digest(typed);
  return typed;
}

function passportFacts(plan, { envelopes = [], receipts = [], reviewer_facts = [] } = {}) {
  return {
    source: { work_request: source.work, accepted_plan: source.plan },
    slice_plans: [{ id: "12121212-1212-4212-8212-121212121212", accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan }],
    envelopes, receipts, reviewer_facts,
  };
}

function receiptRow(id, envelope_id, slice_ref, outcome, created_at, attempt_id = "attempt:1") {
  const evidence = { ref: "evidence:receipt", redaction_class: "metadata_only", content_digest: `sha256:${"a".repeat(64)}` };
  return {
    id, envelope_id, work_request_id: source.work.id.replace(/^wr:/, ""), slice_ref, attempt_id, outcome,
    executor_actor_id: actor.id, executor_actor_active: true, executor_actor_slug: actor.slug, created_at,
    receipt: {
      actual_component_refs: [], actual_resource_refs: [], artifact_refs: ["artifact:receipt"], attribution: null,
      attempt_id, checks: [{ check_ref: `check:${slice_ref}`, evidence_refs: [evidence], state: "passed" }],
      deviations: [{
        category: "scope", deviation_ref: "deviation:resolved", evidence_refs: [evidence], impact: "low",
        out_of_scope_component_refs: [], out_of_scope_resource_refs: [], plan_revision_required: false,
        reason: "fixture resolved deviation", review_state: "resolved",
      }],
      envelope_digest: null, evidence_refs: [evidence], executor_claim: { claim_state: "executor_claim", claimed_at: created_at, claimed_by: actor.slug },
      independent_verification_required: true, outcome, plan_digest: null, planned_component_refs: [], planned_resource_refs: [],
      reset_reconstruction: { fresh_session: true, inherited_transcript_used: false, reconstruction_free: true, remediation_action: null },
      schema_version: "engineering-slice-receipt.v1", slice_ref, source_evidence: { branch_ref: "branch:fixture", evidence_refs: [evidence], source_sha: "0".repeat(40), worktree_ref: "worktree:fixture" },
    },
  };
}

function envelopeRow(id, slice_ref, created_at, supersedes_envelope_id = null) {
  const job_id = id.replace(/-/g, "").slice(0, 32);
  const envelope_digest = `sha256:${"e".repeat(64)}`;
  return { id, job_id, agent_session_id: id, envelope_digest, work_request_id: source.work.id.replace(/^wr:/, ""), slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref, created_at, issued_at: created_at, supersedes_envelope_id,
    envelope: { envelope_id: `env:${id}`, request: { job_ref: `job:${job_id}` }, agent_session: { id: `session:${id}` },
      server_binding: { identity: { agent_principal_id: "agent:codex" }, adapter: { adapter_id: "adapter:codex-desktop" } } } };
}

function bindReceiptLineage(row, plan, envelope) {
  row.receipt.plan_digest = plan.plan_digest;
  row.receipt.envelope_digest = envelope.envelope_digest;
  row.receipt.attribution = {
    actor_ref: "agent:codex", adapter_ref: "adapter:codex-desktop", session_ref: envelope.envelope.agent_session.id,
  };
  row.receipt_digest = digest(row.receipt);
  return row;
}

function reviewerRow(id, receipt_id, slice_ref, state = "passed", created_at = "2026-08-26T00:00:00Z", attempt_id = "attempt:1") {
  return {
    id, receipt_id, work_request_id: source.work.id.replace(/^wr:/, ""), slice_ref, reviewer_actor_id: "99999999-9999-4999-8999-999999999999", reviewer_actor_active: true, reviewer_actor_slug: "reviewer", contract_version: "engineering-review.v1", reviewer_session_ref: `session:reviewer:${id}`, state, created_at,
    fact: { attempt_id, slice_ref, reviewer_ref: "reviewer:reviewer", session_ref: `session:reviewer:${id}`, state, evidence_refs: state === "passed" ? [{ ref: "evidence:review", redaction_class: "metadata_only", content_digest: `sha256:${"c".repeat(64)}` }] : [], is_independent: true, reviewed_deviation_refs: ["deviation:resolved"], resolved_deviation_refs: ["deviation:resolved"] },
  };
}

test("canonical digest is stable across object key order and matches SHA-256", () => {
  assert.equal(canonicalDigest({ b: 2, a: 1 }), canonicalDigest({ a: 1, b: 2 }));
  assert.equal(canonicalDigest({ a: 1 }), "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862");
});

test("passport source is readable before an initial slice plan is registered", async () => {
  const tools = engineeringRuntimeTools({ withEnvelope: async (_c, _a, _verb, _args, fn) => fn(), writeEvent: async () => {}, ToolError: EngineeringToolError });
  const sourcePayload = { work_request: source.work, accepted_plan: source.plan };
  const c = { query: async (sql, params) => {
    assert.match(sql, /ops\.engineering_admission_source/);
    assert.deepEqual(params, ["WR-301"]);
    return { rows: [{ source: sourcePayload }] };
  } };
  const result = await tools["engineering-passport-source"].handler(c, actor, { work_request: "WR-301" });
  assert.equal(result.schema_version, "engineering-passport-source.v1");
  assert.deepEqual(result.work_request, source.work);
  assert.deepEqual(result.accepted_plan_revision, source.plan);
});

test("server builds a fresh Codex envelope and receipt binding rejects wrong attempt", () => {
  const envelope = buildCodexEnvelope({ source, plan, slice, jobId: "44444444-4444-4444-8444-444444444444", sessionId: "55555555-5555-4555-8555-555555555555", actor });
  assert.equal(envelope.server_binding.adapter.surface, "codex_desktop");
  assert.equal(envelope.plan_revision.id, source.plan.plan_ref);
  assert.equal(envelope.handoff.capability_inherited, false);
  assert.equal(envelope.server_binding.authority.read_only, false);
  assert.equal(envelope.server_binding.authority.capability_profile, "capability:engineering-repository-write");
  assert.deepEqual(envelope.request.allowed_actions, [
    "repository:create-worktree", "repository:create-branch", "repository:write-declared-scope",
    "repository:run-checks", "repository:commit", "repository:push-branch", "repository:open-pr",
  ]);
  assert.ok(!envelope.request.allowed_actions.some(action => /merge|deploy|production|review/.test(action)));
  const receipt = {
    schema_version: "engineering-slice-receipt.v1", envelope_digest: digest(envelope),
    attempt_id: "attempt:1", slice_ref: slice.slice_ref, plan_digest: plan.plan_digest,
    attribution: { actor_ref: "actor:codex", session_ref: "session:fresh", adapter_ref: "adapter:codex" },
    planned_resource_refs: [], actual_resource_refs: [], planned_component_refs: [], actual_component_refs: [],
    checks: [{ check_ref: "check:one", state: "passed", evidence_refs: [] }], artifact_refs: [], evidence_refs: [], deviations: [], source_evidence: {},
    outcome: "claimed_complete", independent_verification_required: true,
    reset_reconstruction: { fresh_session: true, inherited_transcript_used: false },
    executor_claim: { claimed_by: actor.slug },
  };
  assert.doesNotThrow(() => validateReceiptBinding(receipt, { ...envelope, envelope_digest: receipt.envelope_digest }, { ...slice, plan_digest: plan.plan_digest }, actor, Error));
  assert.throws(() => validateReceiptBinding({ ...receipt, envelope_digest: `sha256:${"9".repeat(64)}` }, { ...envelope, envelope_digest: receipt.envelope_digest }, { ...slice, plan_digest: plan.plan_digest }, actor, Error));
});

test("runtime refuses malformed, expired, read-only, and session-lease-mismatched packets before dispatch", () => {
  const envelope = currentClaimEnvelope();
  assert.equal(isCurrentRepositoryWriteEnvelope({ envelope }), true);
  for (const invalid of [
    { ...envelope, expires_at: "not-a-date" },
    { ...envelope, server_binding: { ...envelope.server_binding, authority: { ...envelope.server_binding.authority, read_only: true } } },
    { ...envelope, agent_session: { ...envelope.agent_session, lease_expires_at: "2000-01-01T00:00:00Z" } },
    { ...envelope, schema_version: "other" },
  ]) assert.equal(isCurrentRepositoryWriteEnvelope({ envelope: invalid }), false);
});

test("the first adapter dispatches only fresh Codex sessions and refuses Claude", async () => {
  let options;
  const result = await runCodexSlice({
    desk: "hermes-desktop", envelope: { server_binding: { adapter: { surface: "codex_desktop" } } }, task: { slice_ref: slice.slice_ref },
    dispatchEnvelope: async (_desk, _envelope, task, dispatchOptions) => { options = dispatchOptions; return { task }; },
  });
  assert.deepEqual(result, { task: { slice_ref: slice.slice_ref } });
  assert.deepEqual(options, { fresh: true });
  await assert.rejects(() => runCodexSlice({ dispatchEnvelope: async () => ({}), desk: "hermes-desktop", envelope: { server_binding: { adapter: { surface: "claude_desktop" } } }, task: {} }), /only codex_desktop/);
});

test("the committed plan shape is validated and Passport closure is schema-shaped and sealed", () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: "plan:301", revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  assert.equal(requirePlan(typedPlan, Error), typedPlan);
  assert.throws(() => requirePlan({ ...typedPlan, unexpected: true }, Error));
  assert.throws(() => requirePlan({ ...typedPlan, slices: [{ ...item, planned_checks: [{ ...item.planned_checks[0], extra: true }] }] }, Error));
  assert.throws(() => requirePlan({ ...typedPlan, slices: [{ ...item, baseline_evidence_refs: [{ ref: "evidence:one" }] }] }, Error));
  assert.throws(() => requirePlan({ ...typedPlan, slices: [{ ...item, risk_class: "R9" }] }, Error));
  const facts = { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }], envelopes: [], receipts: [], reviewer_facts: [] };
  const projection = closureProjection(facts, Error);
  assert.deepEqual(Object.keys(projection).sort(), ["accepted_plan_revision", "closure", "closure_state", "execution_envelopes", "operator_receipt", "plan_digest", "projection_digest", "qa_facts", "receipts", "reviewer_facts", "schema_version", "slice_plan", "slices", "stale_conflict", "work_request"].sort());
  for (const key of ["slice_plan", "execution_envelopes", "receipts", "reviewer_facts", "qa_facts", "operator_receipt", "projection_digest"]) assert.ok(projection[key]);
  for (const key of ["work", "proof", "explanation", "release"]) assert.deepEqual(Object.keys(projection.closure[key]).sort(), ["evidence_refs", "note", "state"]);
  assert.deepEqual(Object.keys(projection.closure.learning).sort(), ["evidence_refs", "note", "route", "state"]);
  assert.equal(projection.projection_digest, digest({ ...projection, projection_digest: undefined }));
  assert.equal(projection.closure.work.state, "unresolved");
  assert.match(projection.projection_digest, /^sha256:[0-9a-f]{64}$/);
});

test("the worker invokes the fresh Codex path and submits the returned typed receipt", async () => {
  const calls = [];
  const typed = controllerPlan();
  const fakeClaim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: currentClaimEnvelope(), payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [fakeClaim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: fakeClaim.envelope_id, job_id: fakeClaim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: fakeClaim.envelope.issued_at, expires_at: fakeClaim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: fakeClaim.envelope.agent_session.lease_expires_at, envelope: fakeClaim.envelope, envelope_digest: `sha256:${"a".repeat(64)}`, slice_ref: "slice:one" }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: fakeClaim.envelope_id, envelope_digest: fakeClaim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, job_lease_expires_at: fakeClaim.envelope.expires_at } }] };
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts: passportFacts(typed) }] };
    if (sql.includes("engineering_finalize_slice_receipt")) return { rows: [{ id: "receipt:one" }] };
    return { rows: [] };
  } };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: fakeClaim.envelope_digest, attempt_id: "attempt:1", slice_ref: "slice:one", plan_digest: typed.plan_digest, attribution: {}, planned_resource_refs: [], actual_resource_refs: [], planned_component_refs: [], actual_component_refs: [], checks: [{ check_ref: "check:one" }], artifact_refs: [], evidence_refs: [], deviations: [], source_evidence: {}, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false }, executor_claim: { claimed_by: "codex" }, independent_verification_required: true, outcome: "claimed_complete" };
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "hermes-desktop", dispatchEnvelope: async (_desk, _envelope, task, options) => { calls.push({ task, options }); return receipt; }, ToolError: Error });
  assert.equal(result.completed, 1);
  assert.ok(calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.reap_expired_jobs")) <
    calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.engineering_claim_slice")));
  assert.ok(calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.engineering_retire_permanently_ineligible_jobs")) <
    calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.engineering_claim_slice")));
  assert.deepEqual(calls.find(row => row.options)?.options, { fresh: true });
  assert.equal(calls.find(row => row.options)?.task.engineering_plan.plan_digest, typed.plan_digest);
  assert.equal(calls.find(row => row.options)?.task.work_request, typed.work_request.id);
  assert.equal(calls.find(row => row.options)?.task.claim_lease_expires_at, fakeClaim.envelope.expires_at);
  assert.ok(!calls.some(sql => typeof sql === "string" && /from ops\.work_request/i.test(sql)));
  assert.equal(calls.filter(sql => typeof sql === "string" && /ops\.(?:complete_job|fail_job)/.test(sql)).length, 0,
    "runtime must leave post-receipt job finalization to the transactional database seam");
});

test("an atomically finalized receipt survives controller readback failure without a compensating scoped failure", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope: currentClaimEnvelope(), envelope_digest: `sha256:${"a".repeat(64)}`, payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  let receiptPersisted = false;
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest, slice_ref: "slice:one" }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("work_request w")) return { rows: [{ ref: "WR-301" }] };
    if (sql.includes("engineering_passport_facts")) {
      if (receiptPersisted) throw new Error("readback database unavailable");
      return { rows: [{ facts: { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typed }], envelopes: [], receipts: [], reviewer_facts: [] } }] };
    }
    if (sql.includes("engineering_finalize_slice_receipt")) { receiptPersisted = true; return { rows: [{ id: "receipt:one" }] }; }
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: claim.envelope_digest, attempt_id: "attempt:1", slice_ref: "slice:one", plan_digest: typed.plan_digest, attribution: {}, planned_resource_refs: [], actual_resource_refs: [], planned_component_refs: [], actual_component_refs: [], checks: [{ check_ref: "check:one" }], artifact_refs: [], evidence_refs: [], deviations: [], source_evidence: {}, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false }, executor_claim: { claimed_by: "codex" }, independent_verification_required: true, outcome: "claimed_complete" };
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => receipt });
  assert.equal(result.results[0].ok, true);
  assert.deepEqual(result.results[0].operator_readback, { state: "unavailable", reason: "readback_failed" });
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_fail_claim")).length, 0);
});

test("a failed cleanup lease is reported as cleanup_deferred without redispatch", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope: currentClaimEnvelope(), envelope_digest: `sha256:${"a".repeat(64)}`, payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  let dispatched = 0;
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("ops.engineering_fail_claim")) throw new Error("lease already lost");
    return { rows: [] };
  } };
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => { dispatched += 1; throw new Error("adapter unavailable"); } });
  assert.equal(dispatched, 1);
  assert.equal(result.results[0].job_id, claim.job_id);
  assert.equal(result.results[0].state, "cleanup_deferred");
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_claim_slice")).length, 1);
});

test("a controller dispatch failure records the canonical retry receipt without a duplicate dispatch", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: currentClaimEnvelope(), payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  let dispatched = 0;
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => { dispatched += 1; throw new Error("adapter unavailable"); } });
  assert.equal(dispatched, 1);
  assert.equal(result.completed, 0);
  assert.deepEqual(result.results[0], { job_id: claim.job_id, state: "retry_wait", failure_class: "engineering_dispatch_failed" });
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_fail_claim")).length, 1);
});

test("the controller fails closed without launching Codex when post-claim currentness is invalid", async () => {
  const typed = controllerPlan();
  const envelope = { ...currentClaimEnvelope(), agent_session: { id: "session:99999999-9999-4999-8999-999999999999", lease_expires_at: "2000-01-01T00:00:00Z" } };
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: "2000-01-01T00:00:00Z", envelope, envelope_digest: claim.envelope_digest }] };
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  let launched = 0;
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error, dispatchEnvelope: async () => { launched += 1; return {}; } });
  assert.equal(launched, 0);
  assert.equal(result.completed, 0);
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_fail_claim")).length, 1);
});

test("the controller chooses the newest immutable successor envelope for an idempotent job", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: currentClaimEnvelope(), payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  let envelopeQuery = "";
  const c = { query: async (sql) => {
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) {
      envelopeQuery = sql;
      return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest }] };
    }
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => { throw new Error("stop after controller binding"); } });
  assert.match(envelopeQuery, /order by e\.issued_at desc, e\.id desc limit 1/i);
});

test("dependency admission binds the pass to the newest exact receipt and does not open a write path on refusal", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2, ["slice:one"])]);
  const oldEnvelope = envelopeRow("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "slice:one", "2026-08-26T00:00:01Z");
  const newEnvelope = envelopeRow("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "slice:one", "2026-08-26T00:00:02Z", oldEnvelope.id);
  const oldReceipt = bindReceiptLineage(receiptRow("cccccccc-cccc-4ccc-8ccc-cccccccccccc", oldEnvelope.id, "slice:one", "failed", "2026-08-26T00:00:03Z"), plan, oldEnvelope);
  const newReceipt = bindReceiptLineage(receiptRow("dddddddd-dddd-4ddd-8ddd-dddddddddddd", newEnvelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:04Z"), plan, newEnvelope);
  const facts = passportFacts(plan, {
    envelopes: [oldEnvelope, newEnvelope], receipts: [oldReceipt, newReceipt],
    reviewer_facts: [reviewerRow("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", oldReceipt.id, "slice:one")],
  });
  const calls = [];
  const c = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    return { rows: [] };
  } };
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, { idempotency_key: "99999999-9999-4999-8999-999999999999", work_request: source.work.ref, slice_ref: "slice:two" }, EngineeringToolError, async () => {}),
    error => error.error === "engineering_dependencies_not_verified",
  );
  assert.equal(calls.some(call => /pg_advisory_xact_lock|engineering_enqueue_slice_job|insert into ops\./i.test(call.sql)), false,
    "unreviewed successor must be refused before advisory lock or enqueue/write paths");

  facts.reviewer_facts.push(reviewerRow("ffffffff-ffff-4fff-8fff-ffffffffffff", newReceipt.id, "slice:one", "passed", "2026-08-26T00:00:05Z"));
  const writes = [];
  const success = { query: async (sql, params = []) => {
    writes.push({ sql, params });
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan")) return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request")) return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("insert into ops.capability_agent_session")) return { rows: [{ id: "22222222-2222-4222-8222-222222222222" }] };
    if (sql.includes("capability_agent_session")) return { rows: [] };
    if (sql.includes("engineering_enqueue_slice_job")) return { rows: [{ id: "11111111-1111-4111-8111-111111111111" }] };
    if (sql.includes("insert into ops.engineering_execution_envelope")) return { rows: [{ id: "33333333-3333-4333-8333-333333333333" }] };
    return { rows: [] };
  } };
  const admitted = await admitEngineeringSlice(success, actor, {
    idempotency_key: "44444444-4444-4444-8444-444444444444", work_request: source.work.ref, slice_ref: "slice:two",
  }, EngineeringToolError, async () => {});
  assert.equal(admitted.envelope_id, "33333333-3333-4333-8333-333333333333");
  assert.ok(writes.some(call => call.sql.includes("engineering_enqueue_slice_job")));
});

test("closure projection is generation-aware: exact review completes, unreviewed successor blocks, later failure reopens", () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  const e1 = envelopeRow("11111111-1111-4111-8111-111111111111", "slice:one", "2026-08-26T00:00:01Z");
  const e2 = envelopeRow("22222222-2222-4222-8222-222222222222", "slice:one", "2026-08-26T00:00:03Z", e1.id);
  const e3 = envelopeRow("33333333-3333-4333-8333-333333333333", "slice:one", "2026-08-26T00:00:05Z", e2.id);
  const failed = bindReceiptLineage(receiptRow("44444444-4444-4444-8444-444444444444", e1.id, "slice:one", "failed", "2026-08-26T00:00:02Z"), plan, e1);
  const success = bindReceiptLineage(receiptRow("55555555-5555-4555-8555-555555555555", e2.id, "slice:one", "claimed_complete", "2026-08-26T00:00:04Z"), plan, e2);
  const laterFailed = bindReceiptLineage(receiptRow("66666666-6666-4666-8666-666666666666", e3.id, "slice:one", "failed", "2026-08-26T00:00:06Z"), plan, e3);
  const oldPass = reviewerRow("77777777-7777-4777-8777-777777777777", failed.id, "slice:one", "passed", "2026-08-26T00:00:02Z");
  const exactPass = reviewerRow("88888888-8888-4888-8888-888888888888", success.id, "slice:one", "passed", "2026-08-26T00:00:04Z");
  const base = passportFacts(plan, { envelopes: [e1, e2, e3], receipts: [failed, success, laterFailed], reviewer_facts: [oldPass, exactPass] });
  const verified = closureProjection(base, Error);
  assert.equal(verified.slices[0].state, "reopened", "the later failed generation must override the earlier verified success");
  assert.equal(verified.closure_state, "blocked");
  assert.deepEqual(verified.receipts, [failed.receipt, success.receipt, laterFailed.receipt]);
  assert.deepEqual(verified.reviewer_facts, [oldPass.fact, exactPass.fact]);

  const throughSuccess = passportFacts(plan, { envelopes: [e1, e2], receipts: [failed, success], reviewer_facts: [oldPass] });
  const unreviewed = closureProjection(throughSuccess, Error);
  assert.equal(unreviewed.slices[0].state, "claimed");
  assert.equal(unreviewed.closure_state, "blocked");
  assert.deepEqual(unreviewed.receipts, [failed.receipt, success.receipt]);
  assert.deepEqual(unreviewed.reviewer_facts, [oldPass.fact]);

  const exact = closureProjection({ ...throughSuccess, reviewer_facts: [oldPass, exactPass] }, Error);
  assert.equal(exact.slices[0].state, "verified_complete");
  assert.equal(exact.closure_state, "complete");
  assert.deepEqual(exact.receipts, [failed.receipt, success.receipt]);
  assert.deepEqual(exact.reviewer_facts, [oldPass.fact, exactPass.fact]);

  const staleFacts = structuredClone({ ...throughSuccess, reviewer_facts: [oldPass, exactPass] });
  staleFacts.source.work_request.version += 1;
  staleFacts.source.work_request.canonical_record_digest = `sha256:${"f".repeat(64)}`;
  const stale = closureProjection(staleFacts, Error);
  assert.deepEqual(stale.work_request, plan.work_request, "a stale projection must remain bound to its registered plan");
  assert.equal(stale.slices[0].state, "claimed");
  assert.equal(stale.closure_state, "blocked");
  assert.equal(stale.closure.release.state, "unresolved");
  assert.equal(stale.stale_conflict.state, "stale");
  assert.match(stale.stale_conflict.reason, /no longer matches/);

  const noReceiptSuccessor = closureProjection({ ...base, receipts: [failed, success], reviewer_facts: [oldPass, exactPass] }, Error);
  assert.equal(noReceiptSuccessor.slices[0].state, "eligible", "an unsuperseded leaf without a receipt must fence an older reviewed pass");
  assert.equal(noReceiptSuccessor.closure_state, "blocked");
});

test("dependency preflight and closure fail closed on malformed latest receipt or reviewer lineage", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2, ["slice:one"])]);
  const envelope = envelopeRow("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "slice:one", "2026-08-26T00:00:01Z");
  const receipt = bindReceiptLineage(receiptRow("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", envelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:02Z"), plan, envelope);
  const review = reviewerRow("cccccccc-cccc-4ccc-8ccc-cccccccccccc", receipt.id, "slice:one");
  const cases = [
    ["receipt digest mismatch", facts => { facts.receipts[0].receipt_digest = `sha256:${"f".repeat(64)}`; }],
    ["receipt schema mismatch", facts => { facts.receipts[0].receipt.schema_version = "legacy.receipt"; }],
    ["receipt extra top-level field", facts => { facts.receipts[0].receipt.unexpected = true; }],
    ["empty artifacts", facts => { facts.receipts[0].receipt.artifact_refs = []; }],
    ["malformed evidence", facts => { facts.receipts[0].receipt.evidence_refs = [{}]; }],
    ["malformed checks", facts => { facts.receipts[0].receipt.checks = [{}]; }],
    ["bogus planned check ref", facts => { facts.receipts[0].receipt.checks[0].check_ref = "check:bogus"; }],
    ["missing planned check ref", facts => { delete facts.receipts[0].receipt.checks[0].check_ref; }],
    ["wrong evidence redaction class", facts => { facts.receipts[0].receipt.checks[0].evidence_refs[0].redaction_class = "redacted_evidence"; }],
    ["attribution actor mismatch", facts => { facts.receipts[0].receipt.attribution.actor_ref = "agent:wrong"; }],
    ["attribution adapter mismatch", facts => { facts.receipts[0].receipt.attribution.adapter_ref = "adapter:wrong"; }],
    ["attribution session mismatch", facts => { facts.receipts[0].receipt.attribution.session_ref = "session:wrong"; }],
    ["deviations object instead of array", facts => { facts.receipts[0].receipt.deviations = {}; }],
    ["malformed deviation", facts => { facts.receipts[0].receipt.deviations = [{ deviation_ref: "deviation:resolved", review_state: "unreviewed", plan_revision_required: true }]; }],
    ["duplicate receipt deviation", facts => { facts.receipts[0].receipt.deviations.push({ ...facts.receipts[0].receipt.deviations[0] }); }],
    ["inactive reviewer", facts => { facts.reviewer_facts[0].reviewer_actor_active = false; }],
    ["reviewer actor slug mismatch", facts => { facts.reviewer_facts[0].reviewer_actor_slug = "other"; }],
    ["reviewer ref mismatch", facts => { facts.reviewer_facts[0].fact.reviewer_ref = "reviewer:other"; }],
    ["malformed reviewer session", facts => { facts.reviewer_facts[0].reviewer_session_ref = ""; facts.reviewer_facts[0].fact.session_ref = ""; }],
    ["duplicate reviewed deviation refs", facts => { facts.reviewer_facts[0].fact.reviewed_deviation_refs.push("deviation:resolved"); }],
    ["duplicate resolved deviation refs", facts => { facts.reviewer_facts[0].fact.resolved_deviation_refs.push("deviation:resolved"); }],
  ];
  const isolatedCases = cases.map(([label, mutate]) => [label, label === "receipt digest mismatch" ? mutate : facts => {
    mutate(facts);
    facts.receipts[0].receipt_digest = digest(facts.receipts[0].receipt);
  }]);
  for (const [label, mutate] of isolatedCases) {
    const facts = passportFacts(plan, {
      envelopes: [structuredClone(envelope)], receipts: [structuredClone(receipt)], reviewer_facts: [structuredClone(review)],
    });
    mutate(facts);
    const calls = [];
    const c = { query: async sql => { calls.push(sql); if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] }; return { rows: [] }; } };
    await assert.rejects(
      () => admitEngineeringSlice(c, actor, { idempotency_key: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", work_request: source.work.ref, slice_ref: "slice:two" }, EngineeringToolError, async () => {}),
      error => error.error === "engineering_dependencies_not_verified",
      label,
    );
    assert.equal(calls.some(sql => /pg_advisory_xact_lock|engineering_enqueue_slice_job|insert into ops\./i.test(sql)), false, `${label} opened a write path`);
    const projection = closureProjection(facts, EngineeringToolError);
    assert.notEqual(projection.slices.find(row => row.slice_ref === "slice:one")?.state, "verified_complete", label);
    assert.equal(projection.closure_state, "blocked", label);
  }
});

test("malformed newer relational receipt cannot fall back to an older verified generation", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2, ["slice:one"])]);
  const oldEnvelope = envelopeRow("11111111-1111-4111-8111-111111111111", "slice:one", "2026-08-26T00:00:01Z");
  const newEnvelope = envelopeRow("22222222-2222-4222-8222-222222222222", "slice:one", "2026-08-26T00:00:03Z", oldEnvelope.id);
  const oldReceipt = bindReceiptLineage(receiptRow("33333333-3333-4333-8333-333333333333", oldEnvelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:02Z"), plan, oldEnvelope);
  const malformedNewReceipt = bindReceiptLineage(receiptRow("44444444-4444-4444-8444-444444444444", newEnvelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:04Z"), plan, newEnvelope);
  malformedNewReceipt.receipt.attribution.session_ref = "session:wrong";
  malformedNewReceipt.receipt_digest = digest(malformedNewReceipt.receipt);
  const oldReview = reviewerRow("55555555-5555-4555-8555-555555555555", oldReceipt.id, "slice:one");
  const facts = passportFacts(plan, { envelopes: [oldEnvelope, newEnvelope], receipts: [oldReceipt, malformedNewReceipt], reviewer_facts: [oldReview] });
  const calls = [];
  const c = { query: async sql => { calls.push(sql); if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] }; return { rows: [] }; } };
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, { idempotency_key: "66666666-6666-4666-8666-666666666666", work_request: source.work.ref, slice_ref: "slice:two" }, EngineeringToolError, async () => {}),
    error => error.error === "engineering_dependencies_not_verified",
  );
  assert.equal(calls.some(sql => /pg_advisory_xact_lock|engineering_enqueue_slice_job|insert into ops\./i.test(sql)), false);
  const projection = closureProjection(facts, EngineeringToolError);
  assert.notEqual(projection.slices.find(row => row.slice_ref === "slice:one")?.state, "verified_complete");
  assert.equal(projection.closure_state, "blocked");
  assert.deepEqual(projection.reviewer_facts, [oldReview.fact]);
});

test("admission fails closed when a DAG dependency lacks a passed independent review", async () => {
  const first = {
    slice_ref: "slice:one", ordinal: 1, objective: "First", definition_of_done: "A receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const second = { ...first, slice_ref: "slice:two", ordinal: 2, objective: "Second", dependency_refs: ["slice:one"], planned_checks: [{ ...first.planned_checks[0], check_ref: "check:two" }] };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [first, second] };
  typedPlan.plan_digest = digest(typedPlan);
  const c = { query: async () => ({ rows: [{ facts: { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }], envelopes: [], receipts: [], reviewer_facts: [] } }] }) };
  await assert.rejects(() => admitEngineeringSlice(c, actor, { idempotency_key: "99999999-9999-4999-8999-999999999999", work_request: source.work.ref, slice_ref: "slice:two" }, Error));
});

test("successful admission persists its event through the injected writer", async () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  const facts = { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }], envelopes: [], receipts: [], reviewer_facts: [] };
  const sessionId = "44444444-4444-4444-8444-444444444444";
  const jobId = "55555555-5555-4555-8555-555555555555";
  const envelopeId = "66666666-6666-4666-8666-666666666666";
  const calls = [];
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("capability_agent_session")) return { rows: [{ id: sessionId, executor_actor_id: actor.id, state: "claimed", lease_expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(), scope_ref: "slice:slice:one", worktree_ref: "engineering:server-admission", source_commit_sha: "0".repeat(40) }] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan")) return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request")) return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("engineering_enqueue_slice_job")) return { rows: [{ id: jobId }] };
    if (sql.includes("insert into ops.engineering_execution_envelope")) return { rows: [{ id: envelopeId }] };
    return { rows: [] };
  } };
  const events = [];
  const result = await admitEngineeringSlice(c, actor, {
    idempotency_key: "77777777-7777-4777-8777-777777777777",
    work_request: source.work.ref,
    slice_ref: item.slice_ref,
  }, Error, async (...event) => events.push(event));
  assert.equal(result.envelope_id, envelopeId);
  assert.equal(events.length, 1);
  assert.equal(events[0][2], "admit-engineering-slice");
  const planRead = calls.find(sql => sql.trimStart().startsWith("select id from ops.engineering_slice_plan"));
  assert.ok(planRead, "admission must verify the registered immutable slice plan");
  assert.doesNotMatch(planRead, /for\s+(?:key\s+)?share|for\s+update/i,
    "carr_writer has SELECT-only plan authority, so admission must not request a row lock");
});

test("admission refuses source drift after serialization before any write", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  const currentFacts = passportFacts(plan);
  const staleFacts = structuredClone(currentFacts);
  staleFacts.source.work_request.version += 1;
  staleFacts.source.work_request.canonical_record_digest = `sha256:${"f".repeat(64)}`;
  const calls = [];
  let passportReads = 0;
  const c = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("engineering_passport_facts"))
      return { rows: [{ facts: passportReads++ === 0 ? currentFacts : staleFacts }] };
    if (sql.includes("capability_agent_session")) return { rows: [] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.includes("pg_advisory_xact_lock")) return { rows: [] };
    return { rows: [] };
  } };
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, {
      idempotency_key: "89898989-8989-4989-8989-898989898989",
      work_request: source.work.ref,
      slice_ref: "slice:one",
    }, EngineeringToolError, async () => {}),
    error => error.error === "engineering_admission_serialization_restart",
  );
  assert.equal(calls.some(call => /engineering_enqueue_slice_job|insert into ops\.|update ops\./i.test(call.sql)), false);
  assert.ok(calls.findIndex(call => call.sql.includes("from actor")) <
            calls.findIndex(call => call.sql.includes("pg_advisory_xact_lock")));
});

test("admission replaces a stale read-only envelope whose prior job is terminal with a new immutable generation", async () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  const priorId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const priorSession = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const legacy = buildCodexEnvelope({ source, plan: typedPlan, slice: item,
    jobId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", sessionId: priorSession, actor });
  legacy.request.allowed_actions = [];
  legacy.server_binding.authority.capability_profile = "capability:engineering-read-only";
  legacy.server_binding.authority.capability_grant_ref = "grant:engineering-read-only-v1";
  legacy.server_binding.authority.read_only = true;
  legacy.expires_at = "2026-08-24T00:00:00Z";
  legacy.agent_session.lease_expires_at = legacy.expires_at;
  const facts = {
    source: { work_request: source.work, accepted_plan: source.plan },
    slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }],
    envelopes: [{ id: priorId, job_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref,
      created_at: "2026-08-24T00:00:00Z", envelope: legacy }],
    receipts: [], reviewer_facts: [],
  };
  const newSession = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const newJob = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
  const newEnvelopeId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
  let insertParams;
  let enqueueParams;
  const lockOrder = [];
  const c = { query: async (sql, params = []) => {
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from ops.engineering_execution_envelope e")) {
      lockOrder.push("prior-binding");
      return { rows: [{ id: priorId, job_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", work_request_id: source.work.id.replace(/^wr:/, ""), accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref, agent_session_id: priorSession, envelope: legacy, job_state: "dead_lettered" }] };
    }
    if (sql.includes("pg_advisory_xact_lock")) {
      lockOrder.push(String(params[0]).startsWith("engineering-slice:") ? "admission-advisory" : "lineage-advisory");
      return { rows: [] };
    }
    if (sql.includes("where id=$1::uuid for update")) {
      lockOrder.push("session-lock");
      return { rows: [{ id: priorSession, work_request_id: source.work.id.replace(/^wr:/, ""), executor_actor_id: actor.id,
        state: "claimed", scope_ref: `slice:${item.slice_ref}`, worktree_ref: "engineering:server-admission",
        source_commit_sha: "0".repeat(40), executor_slug: "codex" }] };
    }
    if (sql.includes("engineering_envelope_currentness")) { lockOrder.push("currentness"); return { rows: [{ currentness: { eligible: false, dispatch_runway_sufficient: false } }] }; }
    if (sql.includes("update ops.capability_agent_session")) { lockOrder.push("session-cancellation"); return { rows: [] }; }
    if (sql.includes("insert into ops.capability_agent_session"))
      return { rows: [{ id: newSession, executor_actor_id: actor.id, state: "active" }] };
    if (sql.includes("select id, executor_actor_id")) return { rows: [] };
    if (sql.includes("from actor")) { lockOrder.push("actor-lock"); return { rows: [{ id: actor.id, slug: actor.slug }] }; }
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan")) return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request")) return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("engineering_enqueue_slice_job")) { enqueueParams = params; return { rows: [{ id: newJob }] }; }
    if (sql.includes("insert into ops.engineering_execution_envelope")) {
      insertParams = params;
      return { rows: [{ id: newEnvelopeId }] };
    }
    return { rows: [] };
  } };
  const result = await admitEngineeringSlice(c, actor, {
    idempotency_key: "12121212-1212-4212-8212-121212121212",
    work_request: source.work.ref,
    slice_ref: item.slice_ref,
  }, Error, async () => {});
  assert.equal(result.replayed, false);
  assert.equal(result.supersedes_envelope_id, priorId);
  assert.equal(enqueueParams[4], 2);
  assert.equal(insertParams[12], priorId);
  const sessionIndex = lockOrder.indexOf("session-lock");
  const actorIndex = lockOrder.indexOf("actor-lock");
  const admissionIndex = lockOrder.indexOf("admission-advisory");
  const lineageIndex = lockOrder.indexOf("lineage-advisory");
  assert.ok(sessionIndex > lockOrder.indexOf("prior-binding"), lockOrder.join(","));
  assert.ok(actorIndex > sessionIndex, lockOrder.join(","));
  assert.ok(admissionIndex > actorIndex, lockOrder.join(","));
  assert.ok(lineageIndex > admissionIndex, lockOrder.join(","));
  assert.ok(lineageIndex < lockOrder.indexOf("currentness"), lockOrder.join(","));
  assert.ok(lineageIndex < lockOrder.indexOf("session-cancellation"), lockOrder.join(","));
  const replacement = JSON.parse(insertParams[9]);
  assert.equal(replacement.handoff.mode, "replacement");
  assert.equal(replacement.handoff.replaces_agent_session_id, `session:${priorSession}`);
  assert.equal(replacement.handoff.capability_inherited, false);
  assert.equal(replacement.server_binding.authority.read_only, false);
});

test("admission rejects a stale read-only envelope bound to a verification session before any replacement writes", async () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  const priorId = "abababab-abab-4aba-8aba-abababababab";
  const priorSession = "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc";
  const priorJob = "cdcdcdcd-cdcd-4cdc-8cdc-cdcdcdcdcdcd";
  const legacy = buildCodexEnvelope({ source, plan: typedPlan, slice: item, jobId: priorJob, sessionId: priorSession, actor });
  legacy.server_binding.authority.read_only = true;
  legacy.server_binding.authority.capability_profile = "capability:engineering-read-only";
  legacy.expires_at = "2026-08-24T00:00:00Z";
  legacy.agent_session.lease_expires_at = legacy.expires_at;
  const facts = {
    source: { work_request: source.work, accepted_plan: source.plan },
    slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }],
    envelopes: [{ id: priorId, job_id: priorJob, accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref, created_at: "2026-08-24T00:00:00Z", envelope: legacy }],
    receipts: [], reviewer_facts: [],
  };
  const calls = [];
  let provenanceParams;
  const c = { query: async (sql, params = []) => {
    calls.push(sql);
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from ops.engineering_execution_envelope e"))
      return { rows: [{ id: priorId, job_id: priorJob, work_request_id: source.work.id.replace(/^wr:/, ""), accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref, agent_session_id: priorSession, envelope: legacy, job_state: "dead_lettered" }] };
    if (sql.includes("where id=$1::uuid for update")) {
      provenanceParams = params;
      return { rows: [{ id: priorSession, work_request_id: source.work.id.replace(/^wr:/, ""), executor_actor_id: actor.id,
        state: "claimed", scope_ref: "verification:read-only-review", worktree_ref: "engineering:server-admission",
        source_commit_sha: "f".repeat(40), executor_slug: "codex" }] };
    }
    return { rows: [] };
  } };
  class ToolError extends Error {
    constructor(payload) { super(payload.error); Object.assign(this, payload); }
  }
  let typedError;
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, {
      idempotency_key: "dededede-dede-4ded-8ded-dededededede", work_request: source.work.ref, slice_ref: item.slice_ref,
    }, ToolError, async () => {}),
    error => { typedError = error; return error.error === "engineering_session_conflict" && error.envelope_id === priorId; },
  );
  assert.equal(typedError.error, "engineering_session_conflict");
  assert.deepEqual(provenanceParams, [priorSession]);
  assert.ok(calls.some(sql => sql.includes("where id=$1::uuid for update")));
  assert.equal(calls.filter(sql => sql.includes("engineering_envelope_currentness")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("update ops.capability_agent_session")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("insert into ops.capability_agent_session")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("engineering_enqueue_slice_job")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("insert into ops.engineering_execution_envelope")).length, 0);
});

test("successful independent review persists its event through the injected writer", async () => {
  const receiptId = "88888888-8888-4888-8888-888888888888";
  const reviewer = { id: "99999999-9999-4999-8999-999999999999", slug: "reviewer" };
  const receiptRow = {
    id: receiptId,
    work_request_id: "11111111-1111-4111-8111-111111111111",
    slice_ref: "slice:one",
    attempt_id: "attempt:1",
    executor_actor_id: actor.id,
    outcome: "claimed_complete",
    receipt: { outcome: "claimed_complete", deviations: [] },
  };
  const c = { query: async sql => {
    if (sql.includes("from ops.engineering_slice_receipt")) return { rows: [receiptRow] };
    if (sql.includes("insert into ops.engineering_reviewer_fact")) return { rows: [{ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", state: "passed" }] };
    return { rows: [] };
  } };
  const events = [];
  const result = await recordEngineeringReview(c, reviewer, {
    idempotency_key: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    receipt_id: receiptId,
    fact: {
      slice_ref: "slice:one", attempt_id: "attempt:1", reviewer_ref: "reviewer:reviewer", session_ref: "session:reviewer",
      state: "passed", evidence_refs: [{ ref: "evidence:review", redaction_class: "metadata_only", content_digest: `sha256:${"c".repeat(64)}` }],
      is_independent: true, reviewed_deviation_refs: [], resolved_deviation_refs: [],
    },
  }, EngineeringToolError, async (...event) => events.push(event));
  assert.equal(result.state, "passed");
  assert.equal(events.length, 1);
  assert.equal(events[0][2], "review-engineering-slice");
});

test("review admission refuses lineage, independence, evidence, deviation, and noncomplete violations before writing", async () => {
  const receiptId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const reviewer = { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", slug: "reviewer" };
  const completeReceipt = {
    id: receiptId, work_request_id: source.work.id.replace(/^wr:/, ""), slice_ref: "slice:one", attempt_id: "attempt:1", executor_actor_id: actor.id, outcome: "claimed_complete",
    receipt: { outcome: "claimed_complete", slice_ref: "slice:one", attempt_id: "attempt:1", deviations: [], attribution: { session_ref: "session:executor" } },
  };
  const validFact = {
    attempt_id: "attempt:1", evidence_refs: [{ ref: "evidence:review", redaction_class: "metadata_only", content_digest: `sha256:${"c".repeat(64)}` }],
    is_independent: true, resolved_deviation_refs: [], reviewed_deviation_refs: [], reviewer_ref: "reviewer:reviewer", session_ref: "session:reviewer", slice_ref: "slice:one", state: "passed",
  };
  const cases = [
    ["slice mismatch", { fact: { ...validFact, slice_ref: "slice:wrong" } }],
    ["attempt mismatch", { fact: { ...validFact, attempt_id: "attempt:2" } }],
    ["self review", { actor, fact: validFact }],
    ["reviewer session equals executor", { fact: { ...validFact, session_ref: "session:executor" } }],
    ["empty evidence", { fact: { ...validFact, evidence_refs: [] } }],
    ["malformed evidence", { fact: { ...validFact, evidence_refs: [{ ref: "evidence:review" }] } }],
    ["passed noncomplete", { fact: validFact, receipt: { ...completeReceipt, outcome: "failed", receipt: { ...completeReceipt.receipt, outcome: "failed" } } }],
    ["unresolved deviation", { fact: validFact, receipt: { ...completeReceipt, receipt: { ...completeReceipt.receipt, deviations: [{ deviation_ref: "deviation:one", review_state: "unreviewed" }] } } }],
  ];
  for (const [label, override] of cases) {
    const writes = [];
    const targetReceipt = override.receipt || completeReceipt;
    const c = { query: async (sql, params = []) => {
      if (sql.includes("from ops.engineering_slice_receipt")) return { rows: [targetReceipt] };
      if (sql.includes("insert into ops.engineering_reviewer_fact")) { writes.push({ sql, params }); return { rows: [{ id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", state: "passed" }] }; }
      return { rows: [] };
    } };
    const targetActor = override.actor || reviewer;
    await assert.rejects(
      () => recordEngineeringReview(c, targetActor, { idempotency_key: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", receipt_id: receiptId, fact: override.fact }, EngineeringToolError, async () => {}),
      error => Boolean(error?.error),
      label,
    );
    assert.equal(writes.length, 0, `${label} must leave no immutable reviewer row`);
  }
});
