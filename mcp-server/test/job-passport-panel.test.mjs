import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import crypto from "node:crypto";
import { deriveJobPassports, jobPassportStatusLabel, parseJobPassportReceipt } from "../../dealroom/js/job-passport.js";

const fixture = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.observatory-projection.v1.json", import.meta.url)));
const envelope = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.execution-envelope.v1.json", import.meta.url)));
const portfolio = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/carr-evaluation-kernel.synthetic.v1.json", import.meta.url)));
const spatial = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.spatial-surface.v1.json", import.meta.url)));
const elapsedTelemetry = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.elapsed-time.telemetry-measurement.v1.json", import.meta.url)));
const unavailableCost = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.billed-cost.telemetry-measurement.v1.json", import.meta.url)));
const roomSource = fs.readFileSync(new URL("../../dealroom/js/room.js", import.meta.url), "utf8");
const roomCss = fs.readFileSync(new URL("../../dealroom/css/room.css", import.meta.url), "utf8");
const turn = (payload, seq = 1) => ({ seq: String(seq), kind: "receipt", at: "2026-08-24T12:00:06Z", body: JSON.stringify(payload) });
const wrap = (kind, payload) => ({ job_passport: { schema_version: "job-passport-wire.v1", kind, payload } });
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const seal = (value) => `sha256:${crypto.createHash("sha256").update(canonical(value)).digest("hex")}`;
const engineeringFor = (projection = fixture) => {
  const workRequest = { id: projection.work_request_id, state_version: projection.source_state.state_version, canonical_record_digest: projection.source_state.canonical_record_digest };
  const acceptedPlanRevision = { id: envelope.plan_revision.id, revision: envelope.plan_revision.revision, digest: projection.source_state.plan_revision_digest };
  const evidence = { ref: "evidence:baseline", redaction_class: "redacted_evidence", content_digest: "sha256:" + "a".repeat(64) };
  const slice = { slice_ref: "slice:a", ordinal: 1, objective: "Blocked synthetic slice", definition_of_done: "A typed receipt arrives", dependency_refs: [], declared_resource_refs: ["resource:worktree-a"], declared_component_refs: ["component:execution-fabric"], declared_plan_step_refs: ["step:synthetic-read"], baseline_evidence_refs: [evidence], planned_checks: [{ check_ref: "check:synthetic", failure_condition: "missing evidence", evidence_requirement: "redacted_evidence_required" }], scope_boundary: "synthetic fixture", forbidden_change_refs: ["forbidden:authority"], concurrency_posture: "parallel_safe", manual_qa_required: false, risk_class: "R1", release_requirement: "required" };
  const slicePlan = { schema_version: "engineering-slice-plan.v1", work_request: workRequest, accepted_plan_revision: acceptedPlanRevision, slices: [slice] };
  slicePlan.plan_digest = seal(slicePlan);
  const receiptEvidence = { ref: "evidence:receipt", redaction_class: "redacted_evidence", content_digest: "sha256:" + "a".repeat(64) };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: seal(envelope), attempt_id: "attempt:a", slice_ref: "slice:a", plan_digest: slicePlan.plan_digest, attribution: { actor_ref: "actor:codex", session_ref: "session:fresh", adapter_ref: "adapter:codex" }, planned_resource_refs: ["resource:worktree-a"], actual_resource_refs: ["resource:worktree-a"], planned_component_refs: ["component:execution-fabric"], actual_component_refs: ["component:execution-fabric"], checks: [{ check_ref: "check:synthetic", state: "passed", evidence_refs: [receiptEvidence] }], outcome: "claimed_complete", artifact_refs: ["artifact:a"], evidence_refs: [receiptEvidence], deviations: [], source_evidence: { worktree_ref: "worktree:isolated", branch_ref: "branch:engineering-passport", source_sha: "0e7279b4", evidence_refs: [receiptEvidence] }, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false, reconstruction_free: true, remediation_action: null }, executor_claim: { claim_state: "executor_claim", claimed_by: "actor:codex", claimed_at: "2026-08-24T12:15:00Z" }, independent_verification_required: true };
  const passport = { schema_version: "engineering-passport.v1", work_request: workRequest, accepted_plan_revision: acceptedPlanRevision, plan_digest: slicePlan.plan_digest, slice_plan: slicePlan, execution_envelopes: [structuredClone(envelope)], receipts: [receipt], reviewer_facts: [], qa_facts: [], slices: [{ slice_ref: "slice:a", ordinal: 1, dependency_refs: [], state: "claimed", planned_check_refs: ["check:synthetic"], deviation_refs: [], manual_qa_required: false, release_requirement: "required" }], operator_receipt: { what_changed: [], why: "derived from accepted plan and typed receipts", evidence_refs: [receiptEvidence], deviations: [], remaining_risk: ["slice:a"], manual_qa_items: [] }, closure: { work: { state: "unresolved", evidence_refs: [], note: "pending" }, proof: { state: "unresolved", evidence_refs: [], note: "pending" }, explanation: { state: "unresolved", evidence_refs: [], note: "pending" }, release: { state: "unresolved", evidence_refs: [], note: "pending" }, learning: { state: "unresolved", route: null, evidence_refs: [], note: "pending" } }, closure_state: "blocked", stale_conflict: { state: "none", reason: null } };
  passport.projection_digest = seal(passport);
  return passport;
};

test("Job Passport parses only strict typed wire wrappers", () => {
  assert.equal(parseJobPassportReceipt("not json").ok, false);
  assert.equal(parseJobPassportReceipt('{"job_passport":{"schema_version":"job-passport-wire.v1","kind":"nope","payload":{}}}').ok, false);
  assert.deepEqual(parseJobPassportReceipt(JSON.stringify(wrap("progress_event", { schema_version: "progress-event.v1" }))),
    { ok: true, kind: "progress_event", payload: { schema_version: "progress-event.v1" } });
});

test("the deterministic panel keeps profile identity separate from model staffing", () => {
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture))], { now: Date.parse("2026-08-24T12:00:10Z") });
  assert.equal(model.enabled, true);
  assert.equal(model.passports[0].attempt_lane.persistent_profile.display_label, "Doc");
  assert.equal(model.passports[0].attempt_lane.actual_staffing.model_id, "model:codex-synthetic");
  assert.equal(model.passports[0].status, "verified_complete");
  assert.equal(jobPassportStatusLabel(model.passports[0].status), "Independently verified complete");
});

test("newer canonical state wins, stale receipts do not overwrite it, and same-version conflicts stay visible", () => {
  const newer = structuredClone(fixture);
  newer.source_state.state_version = 2;
  newer.source_state.canonical_record_digest = "sha256:" + "9".repeat(64);
  newer.generated_at = "2026-08-24T12:00:08Z";
  const stale = structuredClone(fixture);
  const conflict = structuredClone(newer);
  conflict.source_state.canonical_record_digest = "sha256:" + "8".repeat(64);
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("observatory_projection", newer), 2),
    turn(wrap("observatory_projection", stale), 3), turn(wrap("observatory_projection", conflict), 4),
  ]);
  assert.equal(model.passports[0].source_state.state_version, 2);
  assert.equal(model.passports[0].status, "unknown_partial", "a conflicting same-version receipt cannot look certain");
  assert.ok(model.rejected.some((row) => row.reason === "stale_projection"));
  assert.ok(model.rejected.some((row) => row.reason === "same_version_conflict"));
});

test("status distinctions do not invent deviation from quiet or filesystem-only observation", () => {
  const cases = {
    aligned: ["active", [], "unknown"], deviation_candidate: ["active", [{ candidate_id: "candidate:one" }], "unknown"],
    blocked: ["blocked", [], "unknown"], quiet: ["quiet", [], "filesystem_only"], stale: ["stale", [], "stale_signal"],
    failed: ["failed", [], "unknown"], unknown_partial: ["unknown", [], "unknown"],
  };
  for (const [expected, [progress, candidates, uncertainty]] of Object.entries(cases)) {
    const value = structuredClone(fixture);
    value.state.progress = progress; value.observed_movement.progress_state = progress;
    value.observed_movement.deviation_candidates = candidates; value.observed_movement.uncertainty = uncertainty;
    if (expected === "failed") value.state.lifecycle = "failed";
    const result = deriveJobPassports([turn(wrap("observatory_projection", value))]);
    assert.equal(result.passports[0].status, expected, expected);
  }
});

test("typed facts count on the wire but cannot create a visual job from transcript-like data", () => {
  const model = deriveJobPassports([
    turn(wrap("execution_envelope", { schema_version: "execution-envelope.v1" })),
    turn(wrap("progress_event", { schema_version: "progress-event.v1" })),
    turn(wrap("attempt_receipt", { schema_version: "attempt-receipt.v1" })),
  ]);
  assert.equal(model.enabled, false);
  assert.deepEqual(model.typedCounts, { execution_envelope: 1, progress_event: 1, attempt_receipt: 1, observatory_projection: 0, evaluation_kernel: 0, eval_portfolio: 0, spatial_surface: 0, telemetry_measurement: 0 });
});

test("spatial Home Zone binds exact visual state, preserves list parity, and withholds stale/conflicting views", () => {
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("spatial_surface", spatial), 2)]);
  const view = model.passports[0].spatial_surface;
  assert.equal(view.home_zone.return_label, "Return to Job Passport Home");
  assert.deepEqual(view.list_order, view.nodes.map((node) => node.node_id));
  assert.equal(view.nodes.find((node) => node.node_type === "attempt_lane").resource_refs[0], "native:codex-thread-1");
  const stale = structuredClone(spatial); stale.canonical_binding.state_version = 0;
  const mismatch = structuredClone(spatial); mismatch.canonical_binding.source_projection_digest = "sha256:" + "0".repeat(64);
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("spatial_surface", stale), 2), turn(wrap("spatial_surface", mismatch), 3)]);
  assert.equal(withheld.passports[0].spatial_surface, null);
  assert.ok(withheld.rejected.some((row) => row.reason === "mismatched_spatial_surface"));
  assert.match(roomSource, /Job Passport Home Zone/);
  assert.match(roomSource, /Telemetry: unavailable/);
  assert.match(roomCss, /passport-spatial-list/);
});

test("typed telemetry remains attributed, unavailable, and never manufactures a quota or cost zero", () => {
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("telemetry_measurement", elapsedTelemetry), 2), turn(wrap("telemetry_measurement", unavailableCost), 3),
  ]);
  const telemetry = model.passports[0].telemetry_measurements;
  assert.equal(telemetry.length, 2);
  assert.equal(telemetry.find((row) => row.metric_kind === "elapsed_time").value.amount, 5000);
  assert.equal(telemetry.find((row) => row.metric_kind === "billed_cost").value.kind, "unavailable");
  const malformed = structuredClone(unavailableCost); malformed.value.amount = 0;
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("telemetry_measurement", malformed), 2)]);
  assert.equal(withheld.passports[0].telemetry_measurements.length, 0);
  assert.ok(withheld.rejected.some((row) => row.reason === "invalid_telemetry_measurement"));
  assert.match(roomSource, /unavailable — \$\{measurement\.value\.unavailable_reason\}/);
});

test("eval ladder binds to the exact visual projection and keeps critical regression visible without a score", () => {
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("evaluation_kernel", portfolio), 2),
  ]);
  assert.equal(model.passports[0].eval_portfolio.portfolio_id, "portfolio:carr-synthetic-read-only");
  assert.deepEqual([...new Set(model.passports[0].eval_portfolio.cases.map((row) => row.rung))], ["smoke", "regression", "hill_climb", "launch"]);
  const candidate = model.passports[0].eval_portfolio.results.find((row) => row.result_id === "result:cheaper-candidate");
  assert.equal(candidate.dimension_results.find((row) => row.dimension_id === "visual_accessibility").direction_vs_baseline, "regressed");
  const mismatched = structuredClone(portfolio); mismatched.binding.projection_digest = "sha256:" + "0".repeat(64);
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("evaluation_kernel", mismatched), 2)]);
  assert.equal(withheld.passports[0].eval_portfolio, null);
  assert.ok(withheld.rejected.some((row) => row.reason === "mismatched_eval_portfolio"));
  assert.match(roomSource, /no aggregate score/);
  assert.match(roomCss, /passport-eval-matrix/);
});

test("a malformed projection digest is visibly withheld before it can paint", () => {
  const malformed = structuredClone(fixture);
  malformed.projection_digest = "not-a-digest";
  const model = deriveJobPassports([turn(wrap("observatory_projection", malformed))]);
  assert.equal(model.enabled, false);
  assert.deepEqual(model.rejected, [{ seq: 1, reason: "invalid_projection" }]);
});

test("Engineering Passport binds to exact Observatory state and withholds stale/conflicting facts", () => {
  const matching = engineeringFor(fixture);
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", matching), 2)]);
  assert.deepEqual(model.passports[0].engineering_passport, matching);
  const stale = engineeringFor(fixture); const newerProjection = structuredClone(fixture); newerProjection.source_state.state_version = 2; newerProjection.source_state.canonical_record_digest = "sha256:" + "9".repeat(64);
  const staleModel = deriveJobPassports([turn(wrap("observatory_projection", newerProjection), 1), turn(wrap("engineering_passport", stale), 2)]);
  assert.equal(staleModel.passports[0].engineering_passport, null);
  assert.ok(staleModel.rejected.some((row) => row.reason === "stale_engineering_passport"));
  const conflict = engineeringFor(fixture); conflict.work_request.canonical_record_digest = "sha256:" + "d".repeat(64); conflict.slice_plan.work_request.canonical_record_digest = conflict.work_request.canonical_record_digest; conflict.execution_envelopes[0].state_binding.canonical_record_digest = conflict.work_request.canonical_record_digest; conflict.receipts[0].envelope_digest = seal(conflict.execution_envelopes[0]); conflict.slice_plan.plan_digest = seal({ ...conflict.slice_plan, plan_digest: undefined }); delete conflict.slice_plan.plan_digest; conflict.slice_plan.plan_digest = seal(conflict.slice_plan); conflict.plan_digest = conflict.slice_plan.plan_digest; conflict.receipts[0].plan_digest = conflict.plan_digest; delete conflict.projection_digest; conflict.projection_digest = seal(conflict);
  const conflictModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", matching), 2), turn(wrap("engineering_passport", conflict), 3)]);
  assert.equal(conflictModel.passports[0].engineering_passport, null);
  assert.ok(conflictModel.rejected.some((row) => row.reason === "conflicting_engineering_passport"));
});

test("browser refuses forged or malformed Engineering Passports before paint", () => {
  const forged = engineeringFor(fixture);
  forged.closure_state = "complete"; forged.slices[0].state = "verified_complete"; forged.projection_digest = seal(forged);
  const forgedModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", forged), 2)]);
  assert.equal(forgedModel.passports[0].engineering_passport, null);
  assert.ok(forgedModel.rejected.some((row) => row.reason === "invalid_engineering_passport"));

  const malformed = engineeringFor(fixture); malformed.slice_plan = { schema_version: "engineering-slice-plan.v1" }; malformed.projection_digest = seal(malformed);
  const malformedModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", malformed), 2)]);
  assert.equal(malformedModel.passports[0].engineering_passport, null);

  const duplicate = engineeringFor(fixture); duplicate.slices.push(structuredClone(duplicate.slices[0])); duplicate.projection_digest = seal(duplicate);
  const duplicateModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", duplicate), 2)]);
  assert.equal(duplicateModel.passports[0].engineering_passport, null);

  const badDigest = engineeringFor(fixture); badDigest.projection_digest = "sha256:" + "0".repeat(64);
  const digestModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", badDigest), 2)]);
  assert.equal(digestModel.passports[0].engineering_passport, null);
  assert.ok(digestModel.passports[0].attempt_lane, "invalid engineering fact must not remove the prior observatory card");

  const duplicateCheck = engineeringFor(fixture); duplicateCheck.slice_plan.slices[0].planned_checks.push(structuredClone(duplicateCheck.slice_plan.slices[0].planned_checks[0])); delete duplicateCheck.slice_plan.plan_digest; duplicateCheck.slice_plan.plan_digest = seal(duplicateCheck.slice_plan); duplicateCheck.plan_digest = duplicateCheck.slice_plan.plan_digest; delete duplicateCheck.projection_digest; duplicateCheck.projection_digest = seal(duplicateCheck);
  const duplicateCheckModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", duplicateCheck), 2)]);
  assert.equal(duplicateCheckModel.passports[0].engineering_passport, null);

  const wrongEnvelope = engineeringFor(fixture); wrongEnvelope.execution_envelopes[0].work_request_id = "wr-other"; delete wrongEnvelope.projection_digest; wrongEnvelope.projection_digest = seal(wrongEnvelope);
  const wrongEnvelopeModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", wrongEnvelope), 2)]);
  assert.equal(wrongEnvelopeModel.passports[0].engineering_passport, null);

  const unreferencedEnvelope = engineeringFor(fixture); const extraEnvelope = structuredClone(unreferencedEnvelope.execution_envelopes[0]); extraEnvelope.envelope_id = "env-synthetic-extra"; unreferencedEnvelope.execution_envelopes.push(extraEnvelope); unreferencedEnvelope.projection_digest = seal(unreferencedEnvelope);
  const unreferencedModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", unreferencedEnvelope), 2)]);
  assert.equal(unreferencedModel.passports[0].engineering_passport, null);
});

test("browser mirrors strict ExecutionEnvelope authority invariants without throwing", () => {
  const mutations = [
    (passport) => { passport.execution_envelopes[0].server_binding.authority.read_only = false; },
    (passport) => { passport.execution_envelopes[0].server_binding.identity.client_mutable = true; },
    (passport) => { passport.execution_envelopes[0].server_binding.authority.derived_by = "client_claim"; },
    (passport) => { passport.execution_envelopes[0].request.allowed_actions = ["write_file"]; },
    (passport) => { passport.execution_envelopes[0].handoff.capability_inherited = true; },
    (passport) => { passport.execution_envelopes[0].state_binding.compare_and_swap_required = false; },
    (passport) => { passport.execution_envelopes[0].phase_binding.switch_conditions = ["phase_boundary"]; },
    (passport) => { passport.execution_envelopes[0].evaluation_context.experiment_arm = "forged_arm"; },
  ];
  for (const mutate of mutations) {
    const forged = engineeringFor(fixture); mutate(forged); forged.projection_digest = seal(forged);
    assert.doesNotThrow(() => {
      const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", forged), 2)]);
      assert.equal(model.passports[0].engineering_passport, null);
    });
  }
});

test("browser rejects Python-invalid whitespace and identifier values", () => {
  const mutations = [
    (passport) => { passport.execution_envelopes[0].state_binding.accepted_resource_revisions[0].resource_ref = "   "; },
    (passport) => { passport.execution_envelopes[0].state_binding.accepted_resource_revisions[0].revision_ref = "revision with spaces"; },
    (passport) => { passport.execution_envelopes[0].server_binding.adapter.adapter_version = "   "; },
    (passport) => { passport.receipts[0].source_evidence.source_sha = "   "; },
  ];
  for (const mutate of mutations) {
    const forged = engineeringFor(fixture); mutate(forged); forged.projection_digest = seal(forged);
    assert.doesNotThrow(() => {
      const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", forged), 2)]);
      assert.equal(model.passports[0].engineering_passport, null);
    });
  }
});

test("browser accepts reordered binding keys but rejects semantic mismatches", () => {
  const reordered = engineeringFor(fixture);
  reordered.work_request = { canonical_record_digest: reordered.work_request.canonical_record_digest, id: reordered.work_request.id, state_version: reordered.work_request.state_version };
  reordered.slice_plan.work_request = { state_version: reordered.work_request.state_version, id: reordered.work_request.id, canonical_record_digest: reordered.work_request.canonical_record_digest };
  reordered.accepted_plan_revision = { digest: reordered.accepted_plan_revision.digest, revision: reordered.accepted_plan_revision.revision, id: reordered.accepted_plan_revision.id };
  reordered.slice_plan.accepted_plan_revision = { revision: reordered.accepted_plan_revision.revision, id: reordered.accepted_plan_revision.id, digest: reordered.accepted_plan_revision.digest };
  reordered.execution_envelopes[0].plan_revision = { digest: reordered.execution_envelopes[0].plan_revision.digest, id: reordered.execution_envelopes[0].plan_revision.id, revision: reordered.execution_envelopes[0].plan_revision.revision };
  delete reordered.projection_digest; reordered.projection_digest = seal(reordered);
  const accepted = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", reordered), 2)]);
  assert.ok(accepted.passports[0].engineering_passport);
  const mismatch = structuredClone(reordered); mismatch.slice_plan.work_request.id = "wr-other"; delete mismatch.projection_digest; mismatch.projection_digest = seal(mismatch);
  const refused = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", mismatch), 2)]);
  assert.equal(refused.passports[0].engineering_passport, null);
});

test("regression guards preserve keyboard drilldown across a poll and a real mobile grid row", () => {
  assert.match(roomSource, /card\.dataset\.detailOpen = "true"/);
  assert.match(roomSource, /detail\.open = card\.dataset\.detailOpen === "true"/);
  assert.match(roomSource, /event\.key === "Enter" \|\| event\.key === " "/);
  assert.match(roomCss, /minmax\(280px, max-content\)/);
  assert.match(roomCss, /\.job-passport \{ overflow: visible; min-height: max-content; \}/);
});
