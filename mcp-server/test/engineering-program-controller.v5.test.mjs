// V5-F02 — the deterministic program controller, proved clause by clause.
//
// The positive case comes first on purpose: a rule that only ever refuses
// cannot be told apart from a broken one, so every negative below is a single
// NAMED mutation of one clean request that allows. Each `clean*()` helper
// returns a fresh deep copy, so a mutation in one test cannot leak into
// another and quietly turn a later refusal into a pass.
//
// The admission suite feeds in a REAL width answer from evaluateProgramWidth,
// not a hand-written stand-in — so if the width answer's shape changes, this
// suite breaks rather than the wiring silently rotting.
//
//   node --test mcp-server/test/engineering-program-controller.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { ENGINEERING_REPOSITORY_ACTIONS } from "../src/engineering-runtime.js";
import { GATE_ZERO_STEP_REF } from "../src/benchmark-minimum.v5.js";
import {
  V5_PROGRAM_CONTROLLER_SCHEMA_VERSION,
  V5_PROGRAM_CONTROLLER_POLICY_VERSION,
  V5_PROGRAM_CONTROLLER_DECISION_IDS,
  V5_PROGRAM_WIDTH_LADDER,
  V5_WIDTH_EVIDENCE_CLASSES,
  V5_SERIALIZED_SURFACES,
  V5_DATA_DISPOSITIONS,
  V5_REFUSED_DATA_DISPOSITIONS,
  V5_REUSE_DISPOSITIONS,
  V5_MODEL_ROLES,
  V5_DISJOINTNESS_SOURCES,
  V5_MAX_CENSUS_AGE_SECONDS,
  V5_WIDTH_CHECKS,
  V5_ADMISSION_CHECKS,
  V5_CHECKPOINT_CHECKS,
  V5_RELEASE_CHECKS,
  V5_PRE_MERGE_RELEASE_CHECKS,
  V5_PROGRAM_CONTROLLER_REASON_IDS,
  V5_AUTO_RELEASE_UPSTREAM_STEPS,
  evaluateProgramWidth,
  evaluateSliceAdmission,
  evaluateCheckpointResume,
  evaluateReleaseEligibility,
  v5ProgramControllerPolicyPreimage,
  v5ProgramControllerPolicyDigest,
  v5ProgramControllerPolicyCanonicalBytes,
} from "../src/engineering-program-controller.v5.js";

const MAIN = "52fe8c39033547b806cd35a3ddb4c2d0f36eb42b";
const OLD_MAIN = "36ba65c9a1b2c3d4e5f60718293a4b5c6d7e8f90";
const OTHER_HEAD = "0123456789abcdef0123456789abcdef01234567";
const HEAD = "fedcba9876543210fedcba9876543210fedcba98";
const NOW = "2026-09-11T12:00:00Z";
const NOW_MS = Date.parse(NOW);
const at = offsetSeconds => new Date(NOW_MS + offsetSeconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");

const copy = value => JSON.parse(JSON.stringify(value));

/** Every width evidence class accepted and bound to the current base. */
function acceptedEvidence(baseSha = MAIN) {
  return Object.fromEntries(V5_WIDTH_EVIDENCE_CLASSES.map(cls => [cls, {
    state: "accepted",
    evidence_ref: `evidence:width-${cls.replace(/_/g, "-")}`,
    base_sha: baseSha,
    observed_at: at(-60),
  }]));
}

const cleanWidth = () => copy({
  program_ref: "program:foundation-and-control-plane",
  current_width: 1,
  requested_width: 2,
  base: { origin_main_sha: MAIN, observed_at: at(-30) },
  evidence: acceptedEvidence(),
});

const cleanAdmission = () => copy({
  slice_ref: "slice:v5-f02",
  observed_at: NOW,
  reuse_disposition: "extend",
  model_roles: ["author", "reviewer"],
  lease: {
    worktree_ref: "worktree:v5-f02-slice-8dcd",
    worktree_path: "/repo/.claude/worktrees/v5-f02-slice-8dcd",
    branch_ref: "branch:v5-f02-slice-8dcd",
    base_sha: MAIN,
    source_paths: [
      "mcp-server/src/engineering-program-controller.v5.js",
      "mcp-server/test/engineering-program-controller.v5.test.mjs",
    ],
    database_disposition: "no_database",
    database_resources: [],
    serialized_surfaces: [],
    repository_actions: ["repository:create-worktree", "repository:open-pr"],
  },
  census: {
    source: "live_lease_census",
    observed_at: at(-10),
    origin_main_sha: MAIN,
    repository_root: "/repo",
    active_leases: [{
      slice_ref: "slice:v5-a04",
      worktree_path: "/repo/.claude/worktrees/v5-a04-slice-8dcd",
      source_paths: ["mcp-server/src/qualification-cost-ledger.v5.js"],
      database_resources: ["table:ops.cost_ledger"],
      serialized_surfaces: [],
    }],
  },
  width_answer: null,
});

/** The admission fixture with a REAL width answer bound in. */
function admissionRequest(mutate = () => {}) {
  const request = cleanAdmission();
  request.width_answer = evaluateProgramWidth(cleanWidth());
  mutate(request);
  return request;
}

const cleanResume = () => copy({
  slice_ref: "slice:v5-f02",
  observed_at: NOW,
  checkpoint: {
    checkpoint_ref: "checkpoint:v5-f02-step-3",
    recorded_at: at(-600),
    base_sha: MAIN,
    worktree_ref: "worktree:v5-f02-slice-8dcd",
    completed_step_refs: ["step:f02-module-written", "step:f02-tests-written"],
    next_step_ref: "step:f02-mutation-checked",
    record_evidence_refs: ["evidence:f02-checkpoint-receipt"],
  },
  census: {
    source: "live_lease_census",
    observed_at: at(-10),
    origin_main_sha: MAIN,
    repository_root: "/repo",
    active_leases: [{
      slice_ref: "slice:v5-f02",
      worktree_path: "/repo/.claude/worktrees/v5-f02-slice-8dcd",
      source_paths: ["mcp-server/src/engineering-program-controller.v5.js"],
      database_resources: [],
      serialized_surfaces: [],
    }],
  },
  reconstruction: { source: "durable_records", inherited_transcript_used: false },
});

const cleanRelease = () => copy({
  slice_ref: "slice:v5-f02",
  head_sha: HEAD,
  observed_head_sha: HEAD,
  review: {
    state: "accepted",
    reviewer_actor_id: "actor:independent-reviewer",
    maker_actor_id: "actor:author-seat",
    reviewed_head_sha: HEAD,
  },
  required_checks: [
    { name: "gates", conclusion: "success", head_sha: HEAD },
    { name: "unit", conclusion: "success", head_sha: HEAD },
  ],
  merge_slot: { state: "free", held_by_slice_ref: null },
  revalidation: { required: false, revalidated_against_sha: null, current_main_sha: MAIN },
  readback: {
    state: "verified",
    main_sha: MAIN,
    delivered_source_digest: "sha256:aa",
    expected_source_digest: "sha256:aa",
  },
  auto_release_requested: false,
});

function refusalOf(answer, reasonId, blockingCheck) {
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, reasonId);
  assert.equal(answer.blocking_check, blockingCheck);
  assert.equal(answer.check_states[blockingCheck].state, "refused");
}

function throwsWith(fn, code) {
  assert.throws(fn, error => {
    assert.ok(error instanceof V5BoundaryError, `expected V5BoundaryError, got ${error?.name}`);
    assert.equal(error.code, code);
    return true;
  });
}

// ---------------------------------------------------------------------------
// 1. Earned width.
// ---------------------------------------------------------------------------

test("width: one step up the ladder on six current accepted evidence classes", () => {
  const answer = evaluateProgramWidth(cleanWidth());
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "width_granted_on_current_accepted_evidence");
  assert.equal(answer.granted_width, 2);
  assert.deepEqual(answer.missing_evidence_classes, []);
  assert.deepEqual(answer.stale_evidence_classes, []);
  assert.deepEqual(answer.checks_satisfied, [...V5_WIDTH_CHECKS]);
  assert.equal(answer.blocking_check, null);
  assert.equal(answer.schema_version, V5_PROGRAM_CONTROLLER_SCHEMA_VERSION);
  assert.equal(answer.policy_version, V5_PROGRAM_CONTROLLER_POLICY_VERSION);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
  assert.equal(answer.decided_by, "deterministic_controller");
  assert.equal(answer.model_judgment_admitted, false);
});

test("width: two to three is also one step, on the same six classes", () => {
  const request = cleanWidth();
  request.current_width = 2;
  request.requested_width = 3;
  const answer = evaluateProgramWidth(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.granted_width, 3);
});

test("width: staying at the current width needs no new evidence", () => {
  const request = cleanWidth();
  request.requested_width = 1;
  request.evidence = {};
  const answer = evaluateProgramWidth(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "width_at_or_below_current_earned");
  assert.equal(answer.granted_width, 1);
  assert.deepEqual(answer.missing_evidence_classes, [...V5_WIDTH_EVIDENCE_CLASSES]);
});

test("width: one to three skips the ladder and refuses", () => {
  const request = cleanWidth();
  request.requested_width = 3;
  const answer = evaluateProgramWidth(request);
  refusalOf(answer, "width_ladder_step_skipped", "ladder_step");
  assert.equal(answer.granted_width, 1, "a refused increase leaves the program at its earned width");
  assert.deepEqual(answer.checks_not_reached, ["evidence_complete", "evidence_current"]);
});

test("width: five of six accepted classes is not six", () => {
  for (const held of V5_WIDTH_EVIDENCE_CLASSES) {
    const request = cleanWidth();
    delete request.evidence[held];
    const answer = evaluateProgramWidth(request);
    refusalOf(answer, "width_evidence_incomplete", "evidence_complete");
    assert.deepEqual(answer.missing_evidence_classes, [held]);
    assert.equal(answer.granted_width, 1);
  }
});

test("width: a pending or failed class is not an accepted one", () => {
  for (const state of ["pending", "failed"]) {
    const request = cleanWidth();
    request.evidence.race.state = state;
    const answer = evaluateProgramWidth(request);
    refusalOf(answer, "width_evidence_incomplete", "evidence_complete");
    assert.deepEqual(answer.missing_evidence_classes, ["race"]);
  }
});

test("width: accepted evidence bound to an older base is not current evidence", () => {
  const request = cleanWidth();
  request.evidence.merge_serialization.base_sha = OLD_MAIN;
  const answer = evaluateProgramWidth(request);
  refusalOf(answer, "width_evidence_stale_base", "evidence_current");
  assert.deepEqual(answer.stale_evidence_classes, ["merge_serialization"]);
  assert.deepEqual(answer.checks_not_reached, []);
  assert.equal(answer.check_states.evidence_complete.state, "satisfied");
});

test("width: an unknown evidence class is unreadable, not merely unaccepted", () => {
  const request = cleanWidth();
  request.evidence.vibes = { state: "accepted", evidence_ref: "evidence:vibes", base_sha: MAIN, observed_at: at(-60) };
  throwsWith(() => evaluateProgramWidth(request), "unknown_field");
});

test("width: a width off the ladder is unreadable", () => {
  const request = cleanWidth();
  request.requested_width = 4;
  throwsWith(() => evaluateProgramWidth(request), "unknown_enum_member");
});

// ---------------------------------------------------------------------------
// 2. Slice admission — refusal before edit.
// ---------------------------------------------------------------------------

test("admission: a disjoint slice inside the earned width is admitted for edit", () => {
  const answer = evaluateSliceAdmission(admissionRequest());
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "admitted_for_edit_within_earned_width");
  assert.equal(answer.admitted_for_edit, true);
  assert.equal(answer.refused_before_edit, false);
  assert.deepEqual(answer.checks_satisfied, [...V5_ADMISSION_CHECKS]);
  assert.equal(answer.granted_width, 2);
  assert.equal(answer.active_after_admission, 2);
  assert.equal(answer.reuse_disposition, "extend");
  assert.deepEqual(answer.census_binding.active_peer_slice_refs, ["slice:v5-a04"]);
  // An allow is a decision, not a lease.
  assert.equal(answer.lease_claimed, false);
  assert.equal(answer.worktree_created, false);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
});

test("admission: disjointness claimed from the candidate grouping is refused", () => {
  const request = admissionRequest(r => { r.census.source = "candidate_group_catalog"; });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "disjointness_not_computed_from_live_census", "census_provenance");
  assert.equal(answer.refused_before_edit, true);
  assert.deepEqual(answer.checks_not_reached, V5_ADMISSION_CHECKS.slice(1));
  assert.deepEqual(answer.check_states.fresh_base.detail, { blocked_by: "census_provenance" });
});

test("admission: a census observed outside the window is an old story", () => {
  const request = admissionRequest(r => { r.census.observed_at = at(-(V5_MAX_CENSUS_AGE_SECONDS + 1)); });
  refusalOf(evaluateSliceAdmission(request), "census_observation_stale", "census_provenance");
  const edge = admissionRequest(r => { r.census.observed_at = at(-V5_MAX_CENSUS_AGE_SECONDS); });
  assert.equal(evaluateSliceAdmission(edge).decision, "allow", "the window boundary itself is inside the window");
});

test("admission: a census observed after the admission instant is refused", () => {
  const request = admissionRequest(r => { r.census.observed_at = at(30); });
  refusalOf(evaluateSliceAdmission(request), "census_observation_stale", "census_provenance");
});

test("admission: a stale base refuses before any edit", () => {
  const request = admissionRequest(r => { r.lease.base_sha = OLD_MAIN; });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "stale_base_refused_before_edit", "fresh_base");
  assert.deepEqual(answer.check_states.fresh_base.detail,
    { lease_base_sha: OLD_MAIN, origin_main_sha: MAIN });
});

test("admission: the shared repository root is not an owned writer tree", () => {
  const request = admissionRequest(r => { r.lease.worktree_path = "/repo"; });
  refusalOf(evaluateSliceAdmission(request), "shared_root_editing_refused", "owned_worktree");
});

test("admission: two slices may not share one writer tree", () => {
  const request = admissionRequest(r => {
    r.census.active_leases[0].worktree_path = r.lease.worktree_path;
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "writer_tree_shared_with_active_lease", "owned_worktree");
  assert.equal(answer.check_states.owned_worktree.detail.conflicting_slice_ref, "slice:v5-a04");
});

test("admission: whole-tree and glob staging are refused as inexact", () => {
  for (const path of [".", "mcp-server/src/*.v5.js", "/etc/passwd", "../other-worktree/file.js", "mcp-server/src/"]) {
    const request = admissionRequest(r => { r.lease.source_paths = [path]; });
    const answer = evaluateSliceAdmission(request);
    refusalOf(answer, "explicit_path_staging_required", "explicit_path_staging");
    assert.equal(answer.check_states.explicit_path_staging.detail.path, path);
  }
});

test("admission: a directory lease and a file inside it are one resource with two writers", () => {
  const asParent = admissionRequest(r => {
    r.lease.source_paths = ["mcp-server/src"];
    r.census.active_leases[0].source_paths = ["mcp-server/src/qualification-cost-ledger.v5.js"];
  });
  const answer = evaluateSliceAdmission(asParent);
  refusalOf(answer, "source_path_overlap_denied", "source_path_overlap");
  assert.deepEqual(answer.check_states.source_path_overlap.detail, {
    conflicting_slice_ref: "slice:v5-a04",
    path: "mcp-server/src",
    conflicting_path: "mcp-server/src/qualification-cost-ledger.v5.js",
  });

  const asChild = admissionRequest(r => {
    r.lease.source_paths = ["mcp-server/src/engineering-program-controller.v5.js"];
    r.census.active_leases[0].source_paths = ["mcp-server/src"];
  });
  refusalOf(evaluateSliceAdmission(asChild), "source_path_overlap_denied", "source_path_overlap");

  const identical = admissionRequest(r => {
    r.census.active_leases[0].source_paths = ["mcp-server/src/engineering-program-controller.v5.js"];
  });
  refusalOf(evaluateSliceAdmission(identical), "source_path_overlap_denied", "source_path_overlap");
});

test("admission: overlap is decided by path segment, not by string prefix", () => {
  const request = admissionRequest(r => {
    r.lease.source_paths = ["mcp-server/src"];
    r.census.active_leases[0].source_paths = ["mcp-server/srcache/entry.js"];
  });
  assert.equal(evaluateSliceAdmission(request).decision, "allow",
    "'src' must not be read as containing 'srcache'");
});

test("admission: a slice's own earlier census row is not a conflict with itself", () => {
  const request = admissionRequest(r => {
    r.census.active_leases.push({
      slice_ref: r.slice_ref,
      worktree_path: r.lease.worktree_path,
      source_paths: [...r.lease.source_paths],
      database_resources: [],
      serialized_surfaces: [],
    });
  });
  const answer = evaluateSliceAdmission(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.active_after_admission, 2, "re-admitting one slice does not consume a second lane");
});

test("admission: a raw production data fork is refused by name, not by schema error", () => {
  for (const disposition of V5_REFUSED_DATA_DISPOSITIONS) {
    const request = admissionRequest(r => { r.lease.database_disposition = disposition; });
    const answer = evaluateSliceAdmission(request);
    refusalOf(answer, "database_disposition_refused", "database_disposition");
    assert.equal(answer.check_states.database_disposition.detail.database_disposition, disposition);
  }
});

test("admission: every admissible data disposition is admitted", () => {
  for (const disposition of V5_DATA_DISPOSITIONS) {
    const request = admissionRequest(r => { r.lease.database_disposition = disposition; });
    assert.equal(evaluateSliceAdmission(request).decision, "allow", disposition);
  }
});

test("admission: two writers may not overlap on a database resource", () => {
  const request = admissionRequest(r => {
    r.lease.database_resources = ["table:ops.cost_ledger"];
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "database_resource_overlap_denied", "database_resource_overlap");
  assert.equal(answer.check_states.database_resource_overlap.detail.database_resource, "table:ops.cost_ledger");
});

test("admission: a serialized surface has one owner at a time even when paths are disjoint", () => {
  for (const surface of V5_SERIALIZED_SURFACES) {
    const request = admissionRequest(r => {
      r.lease.serialized_surfaces = [surface];
      r.census.active_leases[0].serialized_surfaces = [surface];
    });
    const answer = evaluateSliceAdmission(request);
    refusalOf(answer, "serialized_surface_single_owner_required", "serialized_surface_ownership");
    assert.equal(answer.check_states.serialized_surface_ownership.detail.serialized_surface, surface);
  }
});

test("admission: an unowned serialized surface is admitted", () => {
  const request = admissionRequest(r => { r.lease.serialized_surfaces = ["migration_number_frontier"]; });
  assert.equal(evaluateSliceAdmission(request).decision, "allow");
});

test("admission: an action outside the registered repository actions is a generic shell", () => {
  const request = admissionRequest(r => {
    r.lease.repository_actions = ["repository:commit", "shell:rm -rf"].sort();
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "generic_shell_action_refused", "repository_action_scope");
  assert.deepEqual(answer.check_states.repository_action_scope.detail.refused_actions, ["shell:rm -rf"]);
});

test("admission: every registered repository action is admitted", () => {
  const request = admissionRequest(r => {
    r.lease.repository_actions = [...ENGINEERING_REPOSITORY_ACTIONS].sort();
  });
  assert.equal(evaluateSliceAdmission(request).decision, "allow");
});

test("admission: a third lane refuses while only two are earned", () => {
  const request = admissionRequest(r => {
    r.census.active_leases.push({
      slice_ref: "slice:v5-j201",
      worktree_path: "/repo/.claude/worktrees/v5-j201-slice-8dcd",
      source_paths: ["mcp-server/src/meeting-call-mode.v5.js"],
      database_resources: [],
      serialized_surfaces: [],
    });
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "program_width_exceeded", "program_width");
  assert.deepEqual(answer.check_states.program_width.detail,
    { active_after_admission: 3, granted_width: 2 });
});

test("admission: the same three lanes are admitted once width three is earned", () => {
  const wide = cleanWidth();
  wide.current_width = 2;
  wide.requested_width = 3;
  const request = admissionRequest(r => {
    r.width_answer = evaluateProgramWidth(wide);
    r.census.active_leases.push({
      slice_ref: "slice:v5-j201",
      worktree_path: "/repo/.claude/worktrees/v5-j201-slice-8dcd",
      source_paths: ["mcp-server/src/meeting-call-mode.v5.js"],
      database_resources: [],
      serialized_surfaces: [],
    });
  });
  assert.equal(evaluateSliceAdmission(request).decision, "allow");
});

test("admission: a refused width answer caps admission at the earned width, not the asked one", () => {
  const skipped = cleanWidth();
  skipped.requested_width = 3;
  const widthAnswer = evaluateProgramWidth(skipped);
  assert.equal(widthAnswer.decision, "refuse");
  const request = admissionRequest(r => { r.width_answer = widthAnswer; });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "program_width_exceeded", "program_width");
  assert.equal(answer.granted_width, 1);
});

test("admission: the per-slice reuse decision cannot be skipped", () => {
  const missing = admissionRequest(r => { delete r.reuse_disposition; });
  throwsWith(() => evaluateSliceAdmission(missing), "missing_field");
  const unknown = admissionRequest(r => { r.reuse_disposition = "rewrite"; });
  throwsWith(() => evaluateSliceAdmission(unknown), "unknown_enum_member");
  for (const disposition of V5_REUSE_DISPOSITIONS) {
    const request = admissionRequest(r => { r.reuse_disposition = disposition; });
    assert.equal(evaluateSliceAdmission(request).reuse_disposition, disposition);
  }
});

test("admission: a model may declare a role and may not carry a verdict", () => {
  const roles = admissionRequest(r => { r.model_roles = [...V5_MODEL_ROLES].sort(); });
  assert.deepEqual(evaluateSliceAdmission(roles).model_roles_declared, [...V5_MODEL_ROLES].sort());
  const verdict = admissionRequest(r => { r.model_recommendation = "admit"; });
  throwsWith(() => evaluateSliceAdmission(verdict), "unknown_field");
  const override = admissionRequest(r => { r.model_roles = ["judge"]; });
  throwsWith(() => evaluateSliceAdmission(override), "unknown_enum_member");
});

test("admission: a hand-written width answer is not an answer", () => {
  const forged = admissionRequest(r => {
    r.width_answer = { schema_version: V5_PROGRAM_CONTROLLER_SCHEMA_VERSION, policy_version: 99, answer: "program_width", granted_width: 3 };
  });
  throwsWith(() => evaluateSliceAdmission(forged), "width_answer_not_this_policy");
  const wrongAnswer = admissionRequest(r => {
    r.width_answer = evaluateCheckpointResume(cleanResume());
  });
  throwsWith(() => evaluateSliceAdmission(wrongAnswer), "width_answer_not_this_policy");
});

test("admission: an unsorted or repeating path lease is unreadable", () => {
  const unsorted = admissionRequest(r => { r.lease.source_paths = ["b.js", "a.js"]; });
  throwsWith(() => evaluateSliceAdmission(unsorted), "unsorted_list");
  const repeating = admissionRequest(r => { r.lease.source_paths = ["a.js", "a.js"]; });
  throwsWith(() => evaluateSliceAdmission(repeating), "duplicate_member");
});

test("admission: an unknown field anywhere in the lease is unreadable", () => {
  const request = admissionRequest(r => { r.lease.skip_checks = ["source_path_overlap"]; });
  throwsWith(() => evaluateSliceAdmission(request), "unknown_field");
});

// ---------------------------------------------------------------------------
// 3. Checkpoint resume.
// ---------------------------------------------------------------------------

test("resume: a recorded checkpoint on the current base resumes at its next step", () => {
  const answer = evaluateCheckpointResume(cleanResume());
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "resumes_from_recorded_checkpoint");
  assert.equal(answer.resumes_from_records, true);
  assert.equal(answer.next_step_ref, "step:f02-mutation-checked");
  assert.equal(answer.replays_completed_steps, false);
  assert.deepEqual(answer.checks_satisfied, [...V5_CHECKPOINT_CHECKS]);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
});

test("resume: an inherited transcript is not a record", () => {
  const fromTranscript = cleanResume();
  fromTranscript.reconstruction = { source: "inherited_transcript", inherited_transcript_used: true };
  const answer = evaluateCheckpointResume(fromTranscript);
  refusalOf(answer, "checkpoint_resume_requires_record_evidence", "checkpoint_record_evidence");
  assert.equal(answer.next_step_ref, null);

  const mixed = cleanResume();
  mixed.reconstruction.inherited_transcript_used = true;
  refusalOf(evaluateCheckpointResume(mixed), "checkpoint_resume_requires_record_evidence", "checkpoint_record_evidence");
});

test("resume: a checkpoint with no recorded evidence is a claim, not a checkpoint", () => {
  const request = cleanResume();
  request.checkpoint.record_evidence_refs = [];
  refusalOf(evaluateCheckpointResume(request), "checkpoint_resume_requires_record_evidence", "checkpoint_record_evidence");
});

test("resume: main moving under a checkpoint requires revalidation first", () => {
  const request = cleanResume();
  request.checkpoint.base_sha = OLD_MAIN;
  const answer = evaluateCheckpointResume(request);
  refusalOf(answer, "checkpoint_base_moved_revalidation_required", "checkpoint_base_current");
  assert.deepEqual(answer.check_states.checkpoint_base_current.detail,
    { checkpoint_base_sha: OLD_MAIN, origin_main_sha: MAIN });
});

test("resume: a slice holding no active lease must be re-admitted first", () => {
  const request = cleanResume();
  request.census.active_leases = [];
  refusalOf(evaluateCheckpointResume(request), "checkpoint_lease_not_held", "checkpoint_lease_held");
});

test("resume: resuming into a completed step is a duplicate queue", () => {
  const request = cleanResume();
  request.checkpoint.next_step_ref = request.checkpoint.completed_step_refs[0];
  refusalOf(evaluateCheckpointResume(request), "checkpoint_next_step_already_complete", "checkpoint_next_step");
});

test("resume: a catalog-sourced census refuses here too, before any checkpoint fact is read", () => {
  const request = cleanResume();
  request.census.source = "candidate_group_catalog";
  const answer = evaluateCheckpointResume(request);
  refusalOf(answer, "disjointness_not_computed_from_live_census", "census_provenance");
  assert.deepEqual(answer.checks_not_reached, V5_CHECKPOINT_CHECKS.slice(1));
});

// ---------------------------------------------------------------------------
// 4. Release.
// ---------------------------------------------------------------------------

test("release: review, green checks, exact head, a free merge slot and a readback", () => {
  const answer = evaluateReleaseEligibility(cleanRelease());
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "release_complete_after_serialized_merge_and_readback");
  assert.equal(answer.released, true);
  assert.equal(answer.merge_admitted, true);
  assert.deepEqual(answer.checks_satisfied, [...V5_RELEASE_CHECKS]);
  assert.equal(answer.performs_merge, false);
  assert.equal(answer.performs_deployment, false);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
});

test("release: before the merge, the merge is admitted and the release is not", () => {
  const request = cleanRelease();
  request.readback = { state: "pending", main_sha: null, delivered_source_digest: null, expected_source_digest: null };
  const answer = evaluateReleaseEligibility(request);
  refusalOf(answer, "release_readback_not_verified", "merged_readback");
  assert.equal(answer.merge_admitted, true, "every pre-merge gate passed");
  assert.equal(answer.released, false);
  assert.deepEqual(answer.checks_satisfied, [...V5_PRE_MERGE_RELEASE_CHECKS]);
});

test("release: an absent or rejecting review refuses", () => {
  const absent = cleanRelease();
  absent.review = { state: "absent", reviewer_actor_id: null, maker_actor_id: "actor:author-seat", reviewed_head_sha: null };
  const answer = evaluateReleaseEligibility(absent);
  refusalOf(answer, "release_review_not_accepted", "independent_review");
  assert.equal(answer.merge_admitted, false);

  const changes = cleanRelease();
  changes.review.state = "requested_changes";
  refusalOf(evaluateReleaseEligibility(changes), "release_review_not_accepted", "independent_review");
});

test("release: the maker may not be the reviewer", () => {
  const request = cleanRelease();
  request.review.reviewer_actor_id = request.review.maker_actor_id;
  refusalOf(evaluateReleaseEligibility(request), "release_reviewer_not_independent", "independent_review");
});

test("release: a review of another head is not a review of this one", () => {
  const request = cleanRelease();
  request.review.reviewed_head_sha = OTHER_HEAD;
  refusalOf(evaluateReleaseEligibility(request), "release_review_bound_to_other_head", "independent_review");
});

test("release: every required check must be green, and there must be one", () => {
  for (const conclusion of ["failure", "pending", "cancelled"]) {
    const request = cleanRelease();
    request.required_checks[1].conclusion = conclusion;
    const answer = evaluateReleaseEligibility(request);
    refusalOf(answer, "release_required_check_not_green", "green_required_checks");
    assert.deepEqual(answer.check_states.green_required_checks.detail.not_green, ["unit"]);
  }
  const none = cleanRelease();
  none.required_checks = [];
  refusalOf(evaluateReleaseEligibility(none), "release_required_check_not_green", "green_required_checks");
});

test("release: a green check on another head proves nothing about this one", () => {
  const request = cleanRelease();
  request.required_checks[0].head_sha = OTHER_HEAD;
  const answer = evaluateReleaseEligibility(request);
  refusalOf(answer, "release_required_check_bound_to_other_head", "green_required_checks");
  assert.deepEqual(answer.check_states.green_required_checks.detail.bound_to_other_head, ["gates"]);
});

test("release: a branch that moved after review refuses on the exact head", () => {
  const request = cleanRelease();
  request.observed_head_sha = OTHER_HEAD;
  refusalOf(evaluateReleaseEligibility(request), "release_head_moved", "exact_head");
});

test("release: merge is serialized — another slice holding the slot refuses", () => {
  const held = cleanRelease();
  held.merge_slot = { state: "held", held_by_slice_ref: "slice:v5-a04" };
  refusalOf(evaluateReleaseEligibility(held), "serialized_merge_slot_held", "serialized_merge_slot");

  const mine = cleanRelease();
  mine.merge_slot = { state: "held", held_by_slice_ref: mine.slice_ref };
  assert.equal(evaluateReleaseEligibility(mine).decision, "allow", "holding one's own slot is not contention");
});

test("release: an affected descendant is rebased and revalidated before its delivery", () => {
  const notRevalidated = cleanRelease();
  notRevalidated.revalidation = { required: true, revalidated_against_sha: null, current_main_sha: MAIN };
  const answer = evaluateReleaseEligibility(notRevalidated);
  refusalOf(answer, "descendant_revalidation_required", "descendant_revalidation");
  assert.equal(answer.merge_admitted, false);

  const staleRevalidation = cleanRelease();
  staleRevalidation.revalidation = { required: true, revalidated_against_sha: OLD_MAIN, current_main_sha: MAIN };
  refusalOf(evaluateReleaseEligibility(staleRevalidation), "descendant_revalidation_required", "descendant_revalidation");

  const revalidated = cleanRelease();
  revalidated.revalidation = { required: true, revalidated_against_sha: MAIN, current_main_sha: MAIN };
  assert.equal(evaluateReleaseEligibility(revalidated).decision, "allow");
});

test("release: what landed on main must be what was reviewed", () => {
  const request = cleanRelease();
  request.readback.delivered_source_digest = "sha256:bb";
  refusalOf(evaluateReleaseEligibility(request), "release_readback_digest_mismatch", "merged_readback");
});

test("release: auto-release is unavailable and names the receipts it waits on", () => {
  for (const requested of [false, true]) {
    const request = cleanRelease();
    request.auto_release_requested = requested;
    const answer = evaluateReleaseEligibility(request);
    assert.equal(answer.decision, "allow", "a fully evidenced release is still a release");
    assert.equal(answer.auto_release_state, "unavailable");
    assert.equal(answer.auto_release_decided, false);
    assert.equal(answer.auto_release_unavailable_reason_id, "auto_release_upstream_receipt_absent");
    assert.deepEqual(answer.auto_release_missing_steps, [...V5_AUTO_RELEASE_UPSTREAM_STEPS]);
    assert.ok(answer.auto_release_missing_steps.includes(GATE_ZERO_STEP_REF));
  }
});

test("release: a caller cannot assert the upstream receipts it does not have", () => {
  const request = cleanRelease();
  request.gate_zero_receipt = { accepted: true };
  throwsWith(() => evaluateReleaseEligibility(request), "unknown_field");
});

test("release: a free merge slot may not name a holder, and an absent review may not name a reviewer", () => {
  const slot = cleanRelease();
  slot.merge_slot = { state: "free", held_by_slice_ref: "slice:v5-a04" };
  throwsWith(() => evaluateReleaseEligibility(slot), "free_slot_names_a_holder");

  const review = cleanRelease();
  review.review = { state: "absent", reviewer_actor_id: "actor:someone", maker_actor_id: "actor:author-seat", reviewed_head_sha: null };
  throwsWith(() => evaluateReleaseEligibility(review), "absent_review_states_a_reviewer");
});

// ---------------------------------------------------------------------------
// The policy identity.
// ---------------------------------------------------------------------------

test("policy: the digest is deterministic and covers every closed vocabulary", () => {
  assert.equal(v5ProgramControllerPolicyDigest(), v5ProgramControllerPolicyDigest());
  const preimage = v5ProgramControllerPolicyPreimage();
  assert.deepEqual(JSON.parse(v5ProgramControllerPolicyCanonicalBytes()), preimage);
  for (const vocabulary of [
    V5_PROGRAM_WIDTH_LADDER, V5_WIDTH_EVIDENCE_CLASSES, V5_SERIALIZED_SURFACES,
    V5_DATA_DISPOSITIONS, V5_REUSE_DISPOSITIONS, V5_MODEL_ROLES,
    V5_DISJOINTNESS_SOURCES, V5_PROGRAM_CONTROLLER_REASON_IDS,
    V5_ADMISSION_CHECKS, V5_RELEASE_CHECKS,
  ]) {
    const serialized = JSON.stringify([...vocabulary]);
    assert.ok(v5ProgramControllerPolicyCanonicalBytes().includes(serialized.slice(1, -1)),
      `vocabulary missing from the policy preimage: ${serialized.slice(0, 60)}`);
  }
  assert.equal(preimage.auto_release_state, "unavailable");
  assert.equal(preimage.caller_may_assert_upstream_receipt, false);
  assert.equal(preimage.caller_may_claim_catalog_disjointness, false);
  assert.equal(preimage.path_overlap_is_segment_wise, true);
  assert.equal(preimage.claims_lease, false);
});

test("policy: the reason vocabulary is sorted, unique, and fully exercised", () => {
  const sorted = [...V5_PROGRAM_CONTROLLER_REASON_IDS].sort();
  assert.deepEqual([...V5_PROGRAM_CONTROLLER_REASON_IDS], sorted);
  assert.equal(new Set(V5_PROGRAM_CONTROLLER_REASON_IDS).size, V5_PROGRAM_CONTROLLER_REASON_IDS.length);
  // The four decisions this module makes, each carrying its own answer name.
  const answers = [
    evaluateProgramWidth(cleanWidth()),
    evaluateSliceAdmission(admissionRequest()),
    evaluateCheckpointResume(cleanResume()),
    evaluateReleaseEligibility(cleanRelease()),
  ];
  assert.deepEqual(answers.map(answer => answer.answer),
    ["program_width", "slice_admission", "checkpoint_resume", "release_eligibility"]);
  for (const answer of answers) {
    assert.ok(V5_PROGRAM_CONTROLLER_REASON_IDS.includes(answer.reason_id));
    assert.equal(answer.decided_by, "deterministic_controller");
    assert.equal(answer.model_judgment_admitted, false);
    assert.ok(Object.isFrozen(answer));
  }
});

test("policy: the nine catalog decisions are carried in the binding", () => {
  assert.deepEqual([...V5_PROGRAM_CONTROLLER_DECISION_IDS], [
    "Q012.D2", "Q025.D1", "Q026.D1", "Q037.D1", "Q038.D1",
    "Q039.D1", "Q044.D1", "Q111.D1", "Q112.D1",
  ]);
  assert.deepEqual(v5ProgramControllerPolicyPreimage().decision_ids,
    [...V5_PROGRAM_CONTROLLER_DECISION_IDS].sort());
});
