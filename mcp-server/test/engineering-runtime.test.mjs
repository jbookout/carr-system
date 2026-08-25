import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalDigest,
  buildCodexEnvelope,
  validateReceiptBinding,
  runCodexSlice,
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
