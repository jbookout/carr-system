import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalDigest,
  buildCodexEnvelope,
  validateReceiptBinding,
  runCodexSlice,
  requirePlan,
  closureProjection,
  runEngineeringWorker,
  admitEngineeringSlice,
  recordEngineeringReview,
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

test("canonical digest is stable across object key order and matches SHA-256", () => {
  assert.equal(canonicalDigest({ b: 2, a: 1 }), canonicalDigest({ a: 1, b: 2 }));
  assert.equal(canonicalDigest({ a: 1 }), "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862");
});

test("server builds a fresh Codex envelope and receipt binding rejects wrong attempt", () => {
  const envelope = buildCodexEnvelope({ source, plan, slice, jobId: "44444444-4444-4444-8444-444444444444", sessionId: "55555555-5555-4555-8555-555555555555", actor });
  assert.equal(envelope.server_binding.adapter.surface, "codex_desktop");
  assert.equal(envelope.plan_revision.id, source.plan.plan_ref);
  assert.equal(envelope.handoff.capability_inherited, false);
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
  const fakeClaim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: { server_binding: { adapter: { surface: "codex_desktop" } } }, payload: { slice_ref: "slice:one" } };
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [fakeClaim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: fakeClaim.envelope_id, work_request_id: "99999999-9999-4999-8999-999999999999", envelope: fakeClaim.envelope, envelope_digest: `sha256:${"a".repeat(64)}`, slice_ref: "slice:one" }] };
    if (sql.includes("work_request w")) return { rows: [{ ref: "WR-301" }] };
    if (sql.includes("engineering_passport_facts")) return { rows: [] };
    if (sql.includes("engineering_record_slice_receipt")) return { rows: [{ id: "receipt:one" }] };
    return { rows: [] };
  } };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: fakeClaim.envelope_digest, attempt_id: "attempt:1", slice_ref: "slice:one", plan_digest: plan.plan_digest, attribution: {}, planned_resource_refs: [], actual_resource_refs: [], planned_component_refs: [], actual_component_refs: [], checks: [{ check_ref: "check:one" }], artifact_refs: [], evidence_refs: [], deviations: [], source_evidence: {}, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false }, executor_claim: { claimed_by: "codex" }, independent_verification_required: true, outcome: "claimed_complete" };
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", actor: { id: actor.id, slug: "codex" }, desk: "hermes-desktop", dispatchEnvelope: async (_desk, _envelope, task, options) => { calls.push({ task, options }); return receipt; }, ToolError: Error });
  assert.equal(result.completed, 1);
  assert.deepEqual(calls.find(row => row.options)?.options, { fresh: true });
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
    if (sql.includes("capability_agent_session")) return { rows: [{ id: sessionId, executor_actor_id: actor.id, state: "active" }] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
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
    receipt: { deviations: [] },
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
  }, Error, async (...event) => events.push(event));
  assert.equal(result.state, "passed");
  assert.equal(events.length, 1);
  assert.equal(events[0][2], "review-engineering-slice");
});
