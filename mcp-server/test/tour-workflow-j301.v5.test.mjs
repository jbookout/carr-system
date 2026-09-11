// V5-J301 — attended MLS intake and the resumable Tour workflow, proved case by
// case.
//
// Everything here is synthetic and nothing reaches a database, a provider, a
// network, a clock or a real MLS. The module under test is pure, so this suite
// can prove the properties that actually matter about a staged Tour workflow:
//
//   * that THE FIVE STAGES ARE SEPARATE AND ORDERED — Q060.D1 — so a later
//     stage cannot quietly run before an earlier one produced its input, and a
//     backward move is a CORRECTION against an entry that exists rather than an
//     erasure,
//   * that the same logical action folds to the SAME step key however often it
//     is replayed, that every part of the action moves the key, and that a
//     replayed action is refused as a duplicate rather than applied twice,
//   * that INTAKE IS ATTENDED: all four unattended intents refused by name, and
//     an attended action that names no verified partner refused too,
//   * that NO TOUR PATH ANYWHERE reports an Assignment phase change or a Deal —
//     checked by sweeping every result the suite builds rather than by asserting
//     it once — and that a lifecycle field NAME inside a Tour payload is refused
//     however deeply it is buried,
//   * that NO JOURNAL A CALLER CAN SUPPLY REACHES THE CLASSIFICATION AT ALL:
//     the public path refuses `journal_view` by name, and a probe that calls
//     every public export with every shape this suite can build recovers no
//     journal-derived field and no resume position from any of them,
//   * that the staging classification itself is still proved, stage by stage,
//     against the test-tree helper that owns it, in the conditional
//     (`would_be_*`) and never as a statement that anything happened,
//   * that NO PUBLIC EXPORT, over every caller-controlled input shape this
//     suite can build, ever yields a privileged outcome string,
//   * that the module's real export list is exactly its declared public surface
//     — no test-only member on it or off it — checked through the loader and
//     through esbuild's parser rather than by eye,
//   * that no production module reaches the test tree by a STATIC OR A DYNAMIC
//     import, read out of a real parser's import records,
//   * and that every verb the stage registry names really exists in the
//     deployed record-layer registry.
//
// NO FIXTURE NAMES A REAL THING: every tour, assignment and property below is
// unmistakably test data.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import esbuild from "esbuild";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_J102_ASSIGNMENT_PHASES, V5_J102_DEAL_AXES } from "../src/cre-lifecycle.v5.js";
import { TOOLS } from "../src/tools.js";
import * as j301 from "../src/tour-workflow-j301.v5.js";
import * as command from "../src/tour-map-command-j301.v5.js";
import {
  V5J301Error,
  V5_J301_ACTOR_CLASSES,
  V5_J301_ATTENDED_INTENT,
  V5_J301_CALLER_AUTHORITY_FIELDS,
  V5_J301_CONSUMER_GATES,
  V5_J301_DECISIONS,
  V5_J301_FORBIDDEN_ACTIVITY_FIELDS,
  V5_J301_MAP_CONTRACT_GATE,
  V5_J301_MAP_CONTRACT_RECEIPT_STEP,
  V5_J301_MODEL_PERMITTED_STAGES,
  V5_J301_PUBLIC_SURFACE,
  V5_J301_REFUSED_INTENTS,
  V5_J301_SETTLED_DECISIONS,
  V5_J301_SETTLED_DECISION_IDS,
  V5_J301_STAGES,
  V5_J301_STAGE_ACTIONS,
  V5_J301_STAGE_INDEX,
  V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM,
  V5_J301_INTENDED_VERBS,
  V5_NO_EFFECTS,
  assertJ301DecisionBinding,
  assertTourWorkflowJournalEntry,
  evaluateStageAction,
  evaluateTourAssignmentActivity,
  tourWorkflowGaps,
  tourWorkflowStepKey,
  v5J301PolicyCanonicalBytes,
  v5J301PolicyDigest,
  v5J301PolicyPreimage,
  v5J301TourWorkflowProjection,
} from "../src/tour-workflow-j301.v5.js";
import {
  V5_J301_CLASSIFIER_EVIDENCE_SOURCE,
  wouldBeResumePointIfAuthoritative,
  wouldSatisfyStagingRulesIfAuthoritative,
} from "./tour-workflow-classifiers.v5.testhelper.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC_DIR = path.join(HERE, "..", "src");

const TOUR = "tour-j301-fixture-0001";
const ASSIGNMENT = "assignment-j301-fixture-0001";
const SUBJECT_A = `sha256:${"a".repeat(64)}`;
const SUBJECT_B = `sha256:${"b".repeat(64)}`;
// Slugs a caller might write into declared_actor_slug. Two of them are the real
// partner slugs, which is the point: the module must be unable to tell.
const DECLARED_SLUGS = Object.freeze([
  "joe", "dell", "claude", "codex", "mls-scraper-bot", "nobody-at-all",
]);
const PARTNER = "joe";

// Every result this suite produces is collected here and swept at the end for
// privileged outcome strings and for a Tour that moved a lifecycle.
const EVERY_RESULT = [];
function record(value) {
  EVERY_RESULT.push(value);
  return value;
}

function stageRequest(overrides = {}) {
  return {
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    tour_id: TOUR,
    assignment_id: ASSIGNMENT,
    stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation",
    declared_actor_slug: PARTNER,
    attended_intent: V5_J301_ATTENDED_INTENT,
    actor_class: "human_attended",
    action_subject_digest: SUBJECT_A,
    ...overrides,
  };
}

/** The binding every journal-shaped fixture below is claimed to be OF. */
const BINDING = Object.freeze({ tour_id: TOUR, assignment_id: ASSIGNMENT });

/** One intended action, in the position-bearing shape the helper classifies. */
function stagingAction(overrides = {}) {
  return {
    stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation",
    action_subject_digest: SUBJECT_A,
    corrects_step_key: null,
    ...overrides,
  };
}

function wouldStage(action, view) {
  return record(wouldSatisfyStagingRulesIfAuthoritative(stagingAction(action), view, BINDING));
}

function journalEntry(stage, action_kind, subject = SUBJECT_A, extra = {}) {
  return {
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    tour_id: TOUR,
    assignment_id: ASSIGNMENT,
    stage,
    action_kind,
    action_subject_digest: subject,
    declared_actor_slug: PARTNER,
    recorded_at: "2026-09-11T14:00:00Z",
    ...extra,
  };
}

/** A contiguous history up to and including `stage`. */
function historyThrough(stage) {
  const first = kind => Object.keys(V5_J301_STAGE_ACTIONS[kind])[0];
  return V5_J301_STAGES
    .slice(0, V5_J301_STAGE_INDEX[stage] + 1)
    .map(one => journalEntry(one, first(one)));
}

function evaluate(overrides) {
  return record(evaluateStageAction(stageRequest(overrides)));
}

// ---------------------------------------------------------------------------
// The settled decisions.
// ---------------------------------------------------------------------------

test("the slice binds exactly its seven settled decisions", () => {
  assert.deepEqual([...V5_J301_SETTLED_DECISION_IDS],
    ["Q014.D2", "Q059.D4", "Q060.D1", "Q072.D2", "Q080.D2", "Q123.D3", "Q124.D2"]);
  const bound = assertJ301DecisionBinding({ decisions: { ...V5_J301_SETTLED_DECISIONS } });
  assert.deepEqual([...bound.decisions_bound], [...V5_J301_SETTLED_DECISION_IDS]);
  assert.deepEqual(bound.effects, V5_NO_EFFECTS);
});

test("Q060.D1's own wording is what the five stages are named from", () => {
  const settled = V5_J301_SETTLED_DECISIONS["Q060.D1"].settled_requirement;
  assert.match(settled, /attended MLS acquisition/);
  assert.match(settled, /deterministic normalization/);
  assert.match(settled, /agent-assisted Tour assembly/);
  assert.match(settled, /deterministic generation/);
  assert.match(settled, /client-facing review/);
  assert.match(settled, /separate resumable workflow stages/);
  assert.deepEqual([...V5_J301_STAGES], [
    "attended_mls_acquisition", "deterministic_normalization",
    "agent_assisted_assembly", "deterministic_generation", "client_facing_review",
  ]);
});

test("a drifted decision subset is refused in both directions and on both fields", () => {
  const good = { ...V5_J301_SETTLED_DECISIONS };
  const missing = { ...good };
  delete missing["Q124.D2"];
  assert.throws(() => assertJ301DecisionBinding({ decisions: missing }),
    error => error instanceof V5J301Error && error.code === "decision_subset_drift");

  assert.throws(() => assertJ301DecisionBinding({
    decisions: { ...good, "Q999.D9": { settled_requirement: "x", source_evidence_digest: "c".repeat(64) } },
  }), error => error.code === "decision_subset_drift");

  assert.throws(() => assertJ301DecisionBinding({
    decisions: { ...good, "Q060.D1": { ...good["Q060.D1"], settled_requirement: "Keep the stages together." } },
  }), error => error.code === "decision_text_drift");

  assert.throws(() => assertJ301DecisionBinding({
    decisions: { ...good, "Q060.D1": { ...good["Q060.D1"], source_evidence_digest: "d".repeat(64) } },
  }), error => error.code === "decision_evidence_drift");
});

// ---------------------------------------------------------------------------
// The step key: the one derivation that needs no authority.
// ---------------------------------------------------------------------------

test("the same logical action folds to the same key however often it is replayed", () => {
  const once = tourWorkflowStepKey({
    tour_id: TOUR, stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation", action_subject_digest: SUBJECT_A,
  });
  for (let attempt = 0; attempt < 5; attempt++) {
    assert.equal(tourWorkflowStepKey({
      tour_id: TOUR, stage: "attended_mls_acquisition",
      action_kind: "capture_listing_observation", action_subject_digest: SUBJECT_A,
    }), once);
  }
  assert.match(once, /^sha256:[0-9a-f]{64}$/);
});

test("every part of the action moves the key, and nothing else can reach it", () => {
  const base = {
    tour_id: TOUR, stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation", action_subject_digest: SUBJECT_A,
  };
  const key = tourWorkflowStepKey(base);
  const moved = [
    { ...base, tour_id: "tour-j301-fixture-0002" },
    { ...base, action_subject_digest: SUBJECT_B },
    { ...base, action_kind: "attach_property_identifier" },
    { ...base, stage: "deterministic_normalization", action_kind: "normalize_property_fact" },
  ];
  for (const variant of moved) assert.notEqual(tourWorkflowStepKey(variant), key);

  // A field the contract does not read cannot be smuggled in to move the key.
  assert.throws(() => tourWorkflowStepKey({ ...base, attempt: 2 }),
    error => error.code === "unknown_field");
  // An action that does not belong to the stage is not a key, it is an error.
  assert.throws(() => tourWorkflowStepKey({ ...base, action_kind: "generate_route_version" }),
    error => error.code === "unknown_action_kind");
  assert.throws(() => tourWorkflowStepKey({ ...base, stage: "packet_delivery" }),
    error => error.code === "unknown_stage");
});

// ---------------------------------------------------------------------------
// The journal view is a view.
// ---------------------------------------------------------------------------

test("a journal entry normalizes to a view, never to a record", () => {
  const entry = assertTourWorkflowJournalEntry(journalEntry("attended_mls_acquisition", "capture_listing_observation"));
  record(entry);
  assert.equal(entry.authority, "caller_supplied_view");
  assert.equal(entry.step_key, tourWorkflowStepKey({
    tour_id: TOUR, stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation", action_subject_digest: SUBJECT_A,
  }));
  assert.ok(Object.isFrozen(entry));
});

test("an instant that does not exist on the calendar is refused, not normalized", () => {
  assert.throws(() => assertTourWorkflowJournalEntry(
    journalEntry("attended_mls_acquisition", "capture_listing_observation", SUBJECT_A,
      { recorded_at: "2026-02-31T00:00:00Z" })),
    error => error.code === "invalid_timestamp");
  assert.throws(() => assertTourWorkflowJournalEntry(
    journalEntry("attended_mls_acquisition", "capture_listing_observation", SUBJECT_A,
      { recorded_at: "2026-09-11 14:00:00" })),
    error => error.code === "invalid_timestamp");
});

test("an unknown field in a journal entry is refused rather than ignored", () => {
  assert.throws(() => assertTourWorkflowJournalEntry(
    journalEntry("attended_mls_acquisition", "capture_listing_observation", SUBJECT_A,
      { approved_by_me: true })),
    error => error.code === "unknown_field");
});

// ---------------------------------------------------------------------------
// Attendance. Q014.D2 and Q059.D4.
// ---------------------------------------------------------------------------

test("all four unattended intents are refused by name", () => {
  assert.equal(V5_J301_REFUSED_INTENTS.length, 4);
  for (const intent of V5_J301_REFUSED_INTENTS) {
    const result = evaluate({ attended_intent: intent });
    assert.equal(result.decision, "refused");
    assert.equal(result.reason_id, "unattended_intake_refused");
    assert.equal(result.attended_intent, intent);
    assert.equal(result.accepted_intent, V5_J301_ATTENDED_INTENT);
  }
});

test("an unregistered intent cannot be read at all", () => {
  assert.throws(() => evaluateStageAction(stageRequest({ attended_intent: "probably_a_human" })),
    error => error.code === "unknown_intent");
});

test("a caller writing \"joe\" does not thereby make a human present", () => {
  // THE SPOOF. This request is well formed, rule abiding, attended in intent and
  // signed with a real partner slug. Before this correction it reached the
  // ordinary journal-owner answer, which read as "attendance satisfied, only the
  // store is missing". Attendance was NOT satisfied: the slug is a string a
  // caller typed, and nothing in this repository can turn it into a verified
  // human from inside a pure evaluator.
  for (const declared_actor_slug of ["joe", "dell"]) {
    const result = evaluate({ declared_actor_slug });
    assert.equal(result.decision, "unavailable", declared_actor_slug);
    assert.equal(result.reason_id, "attended_actor_source_unavailable", declared_actor_slug);
    assert.equal(result.attended_actor_source_bound, false);
    assert.equal(result.declared_actor_slug_is_authority, false);
    assert.ok(result.owed_seams.includes(j301.V5_J301_ATTENDED_ACTOR_SOURCE_SEAM));
    assert.equal(result.identity_seam_module, "mcp-server/src/identity.js");
    // The journal hole is still named; one missing thing does not hide another.
    assert.ok(result.owed_seams.includes(j301.V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM));
  }
});

test("every attended action ends at the same answer, whoever the caller claims to be", () => {
  // INDIFFERENCE IS THE PROPERTY, not a list of rejected slugs: if no slug
  // changes the answer, no slug is authority. The answers are compared as whole
  // objects, so a future field that leaked the slug would fail here.
  const answers = DECLARED_SLUGS.map(declared_actor_slug =>
    JSON.stringify(evaluate({ declared_actor_slug })));
  for (const answer of answers) assert.equal(answer, answers[0]);
  // And the slug appears nowhere in the answer at all.
  for (const slug of DECLARED_SLUGS) assert.equal(answers[0].includes(slug), false);
});

test("the Assignment activity path refuses attendance the same way", () => {
  const answers = DECLARED_SLUGS.map(declared_actor_slug => JSON.stringify(record(
    evaluateTourAssignmentActivity({
      organization_tenant_id: ORGANIZATION_TENANT_ID,
      assignment_id: ASSIGNMENT,
      tour_id: TOUR,
      activity_kind: "tour_conducted",
      declared_actor_slug,
      attended_intent: V5_J301_ATTENDED_INTENT,
      occurred_at: "2026-09-11T15:00:00Z",
    }))));
  for (const answer of answers) assert.equal(answer, answers[0]);
  const first = JSON.parse(answers[0]);
  assert.equal(first.decision, "unavailable");
  assert.equal(first.reason_id, "attended_actor_source_unavailable");
  assert.ok(first.owed_seams.includes(j301.V5_J301_ATTENDED_ACTOR_SOURCE_SEAM));
});

test("no membership test on a slug survives anywhere in the module", () => {
  // The old shape asked `isKnownPartner(actor_slug)`. A set lookup over a string
  // answers "is this SPELLED like a partner", which is not the question.
  const source = readFileSync(path.join(SRC_DIR, "tour-workflow-j301.v5.js"), "utf8");
  const referenced = forbiddenIdentifiers(source);
  assert.equal(referenced.includes("isKnownPartner"), false,
    "a partner membership test is back in the module");
  assert.equal(referenced.includes("actor_slug"), false,
    "an unqualified actor_slug is back; the field is declared_actor_slug");
});

// ---------------------------------------------------------------------------
// The model boundary.
// ---------------------------------------------------------------------------

test("a model may occupy the assembly stage and no other", () => {
  assert.deepEqual([...V5_J301_MODEL_PERMITTED_STAGES], ["agent_assisted_assembly"]);
  const reaching = evaluate({
    stage: "deterministic_generation", action_kind: "generate_route_version",
    actor_class: "model_assisted",
  });
  assert.equal(reaching.decision, "refused");
  assert.equal(reaching.reason_id, "model_outside_declared_seam");

  // And inside its own stage it is not refused for being a model.
  const inside = evaluate({
    stage: "agent_assisted_assembly", action_kind: "rank_candidate_stops",
    actor_class: "model_assisted",
  });
  assert.equal(inside.decision, "unavailable");
  assert.equal(inside.intended_verb, null);
  assert.equal(inside.intended_verb_adapter_bound, false);
});

test("every model action produces a proposal rather than a record", () => {
  for (const [kind, action] of Object.entries(V5_J301_STAGE_ACTIONS.agent_assisted_assembly)) {
    assert.equal(action.actor_class, "model_assisted", kind);
    assert.equal(action.intended_verb, null, kind);
  }
});

test("an actor class the action does not call for is refused", () => {
  const result = evaluate({
    stage: "agent_assisted_assembly", action_kind: "draft_stop_narrative",
    actor_class: "human_attended",
  });
  assert.equal(result.decision, "refused");
  assert.equal(result.reason_id, "actor_class_mismatch");
  assert.equal(result.required_actor_class, "model_assisted");
});

// ---------------------------------------------------------------------------
// The journal a caller hands in: refused at the door, and refused for BEING
// there rather than for being malformed.
//
// This is the hinge of the whole correction. The previous shape read the list,
// applied the staging rules to it, and reported the reading in the body of its
// refusals — at which point a caller who wrote the list could read back the
// position of a workflow no authority here can place. So the list is now
// refused before a single entry of it is read.
// ---------------------------------------------------------------------------

test("a caller-supplied journal is refused by name, and the seams are named with it", () => {
  for (const view of [[], historyThrough("deterministic_normalization"),
    [journalEntry("client_facing_review", "record_client_review_note")]]) {
    const result = evaluate({ journal_view: view });
    assert.equal(result.decision, "refused");
    assert.equal(result.reason_id, "caller_supplied_journal_view_refused");
    assert.equal(result.field, "journal_view");
    assert.equal(result.field_path, "request.journal_view");
    assert.ok(result.owed_seams.includes(V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM));
    assert.ok(result.owed_seams.includes(j301.V5_J301_WORKFLOW_JOURNAL_READER_SEAM));
    assert.equal(result.caller_journal_admitted, false);
    assert.equal(result.journal_read, false);
  }

  // AN EMPTY LIST IS STILL A CALLER STATING A HISTORY, and it is the case an
  // exemption would most plausibly be written for. Reading it to answer "no
  // entries, so you stand at stage one" is a resume classification with an
  // easier-looking input, so it is refused exactly like the others — proved
  // above by including [] in the sweep, and named here so the choice is not
  // mistaken for an accident.
  assert.equal(evaluate({ journal_view: [] }).reason_id, "caller_supplied_journal_view_refused");

  // Explicit null states no history at all, so it is not a journal and the
  // request proceeds to the answers it would have reached anyway.
  const withNull = evaluate({ journal_view: null });
  assert.equal(withNull.decision, "unavailable");
  assert.equal(withNull.reason_id, "attended_actor_source_unavailable");

  // The refusal comes BEFORE the entries are read: a list this module could not
  // parse at all still produces the refusal rather than a throw.
  const garbage = evaluate({ journal_view: [{ nonsense: true }, 7, "not an entry"] });
  assert.equal(garbage.reason_id, "caller_supplied_journal_view_refused");
});

test("the refusal names the vocabulary the journal owner owes, and emits none of it", () => {
  const declared = [...j301.V5_J301_JOURNAL_OWNER_REASON_IDS];
  assert.deepEqual(declared, [
    "backward_stage_requires_correction", "correction_target_absent",
    "duplicate_step_key_replay", "journal_entry_foreign_to_tour",
    "journal_history_noncontiguous", "stage_skipped",
  ]);
  const result = evaluate({ journal_view: historyThrough("deterministic_normalization") });
  assert.deepEqual([...result.journal_owner_reason_ids], declared);
  // Declared is not produced: no public answer this suite can provoke carries
  // one of them as its own reason.
  assert.equal(declared.includes(result.reason_id), false);
});

// ---------------------------------------------------------------------------
// Staging: separate, ordered, resumable. Q060.D1.
//
// The rules are REAL and they are the journal owner's. They are proved here
// against the test-tree classifier that owns them, conditionally — every answer
// below is "what an authoritative journal saying this WOULD mean", and nothing
// in this section asserts that anything happened.
// ---------------------------------------------------------------------------

test("the conditional classifier says only what it is entitled to say", () => {
  const answer = wouldStage({}, []);
  assert.equal(answer.is_not_authority, true);
  assert.equal(answer.evidence_source, V5_J301_CLASSIFIER_EVIDENCE_SOURCE);
  for (const forbidden of ["decision", "resume_at", "admitted", "allow", "resume_stage"]) {
    assert.equal(Object.hasOwn(answer, forbidden), false, forbidden);
  }
  assert.ok(Object.isFrozen(answer));
  // And it cannot invent a reason the public module does not declare.
  assert.throws(() => wouldSatisfyStagingRulesIfAuthoritative(
    stagingAction({ stage: "not_a_stage" }), [], BINDING));
});

test("a stage cannot skip the stage before it", () => {
  // The NEAREST illegal jump, which is the one an off-by-one would let through:
  // the history reaches stage 0, and stage 2 is asked for. Proving only the
  // distant jump (0 to 3) would pass a rule that permitted every skip of one.
  const nearest = wouldStage(
    { stage: "agent_assisted_assembly", action_kind: "rank_candidate_stops" },
    [journalEntry("attended_mls_acquisition", "capture_listing_observation")]);
  assert.equal(nearest.would_be_admissible_if_authoritative, false);
  assert.equal(nearest.would_be_reason_id, "stage_skipped");
  assert.equal(nearest.detail.earliest_unstarted_stage, "deterministic_normalization");
  assert.deepEqual([...nearest.resume_point.detail.stages_seen], ["attended_mls_acquisition"]);

  const distant = wouldStage(
    { stage: "client_facing_review", action_kind: "record_client_review_note" },
    [journalEntry("attended_mls_acquisition", "capture_listing_observation")]);
  assert.equal(distant.would_be_reason_id, "stage_skipped");

  // And the boundary from an empty history: intake is the only stage that may
  // begin, and the very next stage is already a skip.
  assert.equal(wouldStage({}, []).would_be_admissible_if_authoritative, true);
  const skipFromNothing = wouldStage(
    { stage: "deterministic_normalization", action_kind: "normalize_property_fact" }, []);
  assert.equal(skipFromNothing.would_be_reason_id, "stage_skipped");
  assert.deepEqual([...skipFromNothing.resume_point.detail.stages_seen], []);
});

test("the next stage after an interruption is not a skip", () => {
  const answer = wouldStage(
    { stage: "deterministic_normalization", action_kind: "normalize_property_fact" },
    [journalEntry("attended_mls_acquisition", "capture_listing_observation")]);
  assert.equal(answer.would_be_admissible_if_authoritative, true);
  assert.equal(answer.would_be_reason_id, null);
});

test("continuing inside an interrupted stage is not a skip either", () => {
  const answer = wouldStage(
    { action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B },
    [journalEntry("attended_mls_acquisition", "capture_listing_observation")]);
  assert.equal(answer.would_be_admissible_if_authoritative, true);
});

test("a backward stage move must be a correction against an entry that exists", () => {
  const view = [
    journalEntry("attended_mls_acquisition", "capture_listing_observation"),
    journalEntry("deterministic_normalization", "normalize_property_fact", SUBJECT_B),
  ];
  const bare = wouldStage(
    { action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B }, view);
  assert.equal(bare.would_be_reason_id, "backward_stage_requires_correction");

  const absent = wouldStage({
    action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B,
    corrects_step_key: `sha256:${"e".repeat(64)}`,
  }, view);
  assert.equal(absent.would_be_reason_id, "correction_target_absent");

  // The same step key with its algorithm stripped is not a near miss to be
  // accepted helpfully; it is a different spelling, and the PUBLIC path — which
  // still validates the field's shape — cannot read it.
  assert.throws(() => evaluateStageAction(stageRequest({
    action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B,
    corrects_step_key: assertTourWorkflowJournalEntry(view[0]).step_key.replace(/^sha256:/, ""),
  })), error => error.code === "invalid_digest");
});

test("a correction whose target is in the history satisfies the staging rules", () => {
  const first = journalEntry("attended_mls_acquisition", "capture_listing_observation");
  const normalized = assertTourWorkflowJournalEntry(first);
  const view = [first, journalEntry("deterministic_normalization", "normalize_property_fact", SUBJECT_B)];
  const answer = wouldStage({
    action_kind: "capture_listing_observation",
    action_subject_digest: `sha256:${"c".repeat(64)}`,
    corrects_step_key: normalized.step_key,
  }, view);
  assert.equal(answer.would_be_admissible_if_authoritative, true);

  // And "would satisfy the staging rules" is where the conditional stops. The
  // same action on the PUBLIC path — which reads no history at all — still ends
  // at the missing authenticator, because a correction is an attended act.
  const real = evaluate({
    action_kind: "capture_listing_observation",
    action_subject_digest: `sha256:${"c".repeat(64)}`,
    corrects_step_key: normalized.step_key,
  });
  assert.equal(real.decision, "unavailable");
  assert.equal(real.reason_id, "attended_actor_source_unavailable");
  assert.ok(real.owed_seams.includes(V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM));
});

test("there is no field through which a correction can erase its target", () => {
  assert.throws(() => evaluateStageAction(stageRequest({ supersedes_step_key: `sha256:${"f".repeat(64)}` })),
    error => error.code === "unknown_field");
  assert.throws(() => evaluateStageAction(stageRequest({ delete_step_key: `sha256:${"f".repeat(64)}` })),
    error => error.code === "unknown_field");
});

test("a replayed action would be refused as a duplicate rather than applied twice", () => {
  const view = [journalEntry("attended_mls_acquisition", "capture_listing_observation")];
  const replay = wouldStage({}, view);
  assert.equal(replay.would_be_reason_id, "duplicate_step_key_replay");
  assert.equal(replay.detail.step_key, tourWorkflowStepKey({
    tour_id: TOUR, stage: "attended_mls_acquisition",
    action_kind: "capture_listing_observation", action_subject_digest: SUBJECT_A,
  }));

  // A DIFFERENT subject in the same stage is a different action, not a replay.
  assert.equal(wouldStage({ action_subject_digest: SUBJECT_B }, view)
    .would_be_admissible_if_authoritative, true);
});

test("the hypothetical resume point tracks the history, stage by stage", () => {
  const view = [];
  assert.equal(wouldBeResumePointIfAuthoritative(view, BINDING)
    .would_be_open_stage_if_authoritative, "attended_mls_acquisition");

  // An interrupted stage is resumed IN that stage, and the stage after it is
  // the one that may begin next. Nothing in a journal says a stage finished, so
  // neither answer claims one did.
  const walk = [
    ["attended_mls_acquisition", "capture_listing_observation", "deterministic_normalization"],
    ["deterministic_normalization", "normalize_property_fact", "agent_assisted_assembly"],
    ["agent_assisted_assembly", "rank_candidate_stops", "deterministic_generation"],
    ["deterministic_generation", "generate_route_version", "client_facing_review"],
    ["client_facing_review", "record_client_review_note", null],
  ];
  for (const [stage, action_kind, expectedNext] of walk) {
    view.push(journalEntry(stage, action_kind));
    const seen = record(wouldBeResumePointIfAuthoritative(view, BINDING));
    assert.equal(seen.would_be_open_stage_if_authoritative, stage, stage);
    assert.equal(seen.would_be_next_stage_if_authoritative, expectedNext, stage);
    assert.equal(seen.would_be_readable_history_if_authoritative, true, stage);
    assert.equal(seen.is_not_authority, true);
    assert.equal(seen.evidence_source, V5_J301_CLASSIFIER_EVIDENCE_SOURCE);
  }
});

// ---------------------------------------------------------------------------
// A history that cannot be this Tour's. The two shapes a reviewer found
// reaching an answer they had no business reaching.
// ---------------------------------------------------------------------------

test("a history holding only the final stage would be refused, not read as stage five", () => {
  // The four stages before client_facing_review left no trace, so this cannot be
  // a history of an ordered five-stage workflow, and it must not be read as a
  // workflow standing at stage five.
  const view = [journalEntry("client_facing_review", "record_client_review_note")];
  const answer = wouldStage(
    { stage: "client_facing_review", action_kind: "accept_route_for_client_review" }, view);
  assert.equal(answer.would_be_admissible_if_authoritative, false);
  assert.equal(answer.would_be_reason_id, "journal_history_noncontiguous");

  const seen = record(wouldBeResumePointIfAuthoritative(view, BINDING));
  assert.equal(seen.would_be_readable_history_if_authoritative, false);
  assert.equal(seen.would_be_open_stage_if_authoritative, null);
  assert.equal(seen.would_be_reason_id, "journal_history_noncontiguous");
  assert.equal(seen.detail.missing_stage, "attended_mls_acquisition");
  assert.equal(seen.detail.furthest_stage_seen, "client_facing_review");
});

test("every interior gap in a history is caught, not just the first stage", () => {
  // Stages 0 and 2 present, stage 1 missing. Naming only the first stage would
  // have let this through.
  const view = [
    journalEntry("attended_mls_acquisition", "capture_listing_observation"),
    journalEntry("agent_assisted_assembly", "rank_candidate_stops"),
  ];
  const seen = record(wouldBeResumePointIfAuthoritative(view, BINDING));
  assert.equal(seen.would_be_reason_id, "journal_history_noncontiguous");
  assert.equal(seen.detail.missing_stage, "deterministic_normalization");
});

test("an entry belonging to another Tour cannot be read as this Tour's history", () => {
  // A cross-Tour entry at a later stage would otherwise make a perfectly
  // ordinary action look like a backward move.
  const foreign = journalEntry("client_facing_review", "record_client_review_note", SUBJECT_B,
    { tour_id: "tour-j301-fixture-9999" });
  const seen = record(wouldBeResumePointIfAuthoritative(
    [journalEntry("attended_mls_acquisition", "capture_listing_observation", SUBJECT_B), foreign],
    BINDING));
  assert.equal(seen.would_be_reason_id, "journal_entry_foreign_to_tour");
  assert.equal(seen.detail.entry_tour_id, "tour-j301-fixture-9999");
  assert.equal(seen.detail.entry_index, 1);
});

test("an entry belonging to another Assignment is refused the same way", () => {
  const foreign = journalEntry("attended_mls_acquisition", "capture_listing_observation", SUBJECT_B,
    { assignment_id: "assignment-j301-fixture-9999" });
  const seen = record(wouldBeResumePointIfAuthoritative([foreign], BINDING));
  assert.equal(seen.would_be_reason_id, "journal_entry_foreign_to_tour");
  assert.equal(seen.detail.entry_assignment_id, "assignment-j301-fixture-9999");
});

test("a journal entry that does not say which Tour it belongs to cannot be read", () => {
  for (const missing of ["organization_tenant_id", "tour_id", "assignment_id"]) {
    const entry = journalEntry("attended_mls_acquisition", "capture_listing_observation");
    delete entry[missing];
    assert.throws(() => assertTourWorkflowJournalEntry(entry),
      error => error.code === "missing_field", missing);
    assert.throws(() => wouldBeResumePointIfAuthoritative([entry], BINDING),
      error => error.code === "missing_field", missing);
  }
});

// ---------------------------------------------------------------------------
// The public resume path.
// ---------------------------------------------------------------------------

test("the public resume path is unavailable and never reads its argument", () => {
  const shapes = [
    undefined, null, {}, [], "resume", 1, true,
    { journal_view: historyThrough("deterministic_generation") },
    { resume_stage: "client_facing_review", verified: true },
    historyThrough("client_facing_review"),
  ];
  const first = JSON.stringify(record(j301.readTourWorkflowResumePoint()));
  for (const shape of shapes) {
    const result = record(j301.readTourWorkflowResumePoint(shape));
    assert.equal(JSON.stringify(result), first,
      `the resume path answered differently for ${JSON.stringify(shape) ?? "undefined"}`);
    assert.equal(result.decision, "unavailable");
    assert.equal(result.reason_id, "workflow_journal_reader_unavailable");
    assert.equal(result.request_read, false);
    assert.equal(result.caller_journal_admitted, false);
    assert.equal(result.resume_stage, null);
    assert.equal(result.next_stage, null);
    assert.ok(result.owed_seams.includes(j301.V5_J301_WORKFLOW_JOURNAL_READER_SEAM));
    assert.ok(Object.isFrozen(result));
  }
});

test("the stage index and the stage order are the same order", () => {
  V5_J301_STAGES.forEach((stage, index) => assert.equal(V5_J301_STAGE_INDEX[stage], index));
  assert.equal(Object.keys(V5_J301_STAGE_INDEX).length, V5_J301_STAGES.length);
});

// ---------------------------------------------------------------------------
// A Tour never moves an Assignment or a Deal. Q072.D2 and Q080.D2.
// ---------------------------------------------------------------------------

test("the forbidden field list is J102's own lifecycle vocabulary, not a retyped copy", () => {
  for (const axis of V5_J102_DEAL_AXES) {
    assert.ok(V5_J301_FORBIDDEN_ACTIVITY_FIELDS.includes(axis), axis);
  }
  assert.ok(V5_J301_FORBIDDEN_ACTIVITY_FIELDS.includes("assignment_phase"));
  assert.deepEqual([...j301.V5_J301_ASSIGNMENT_PHASES], [...V5_J102_ASSIGNMENT_PHASES]);
});

test("a Tour action carrying an Assignment phase is refused", () => {
  for (const phase of V5_J102_ASSIGNMENT_PHASES) {
    const result = evaluate({ tour_activity: { assignment_phase: phase } });
    assert.equal(result.decision, "refused");
    assert.equal(result.reason_id, "implicit_assignment_phase_transition_refused");
    assert.equal(result.field, "assignment_phase");
  }
});

test("a Tour action carrying a Deal axis is refused, however deeply it is buried", () => {
  const buried = evaluate({
    tour_activity: { notes: { stop: { follow_up: { execution_state: "executed" } } } },
  });
  assert.equal(buried.decision, "refused");
  assert.equal(buried.reason_id, "implicit_deal_mutation_refused");
  assert.equal(buried.field, "execution_state");
  assert.equal(buried.field_path, "request.tour_activity.notes.stop.follow_up.execution_state");

  for (const axis of V5_J102_DEAL_AXES) {
    const result = evaluate({ tour_activity: { [axis]: "whatever" } });
    assert.equal(result.decision, "refused", axis);
    assert.equal(result.reason_id, "implicit_deal_mutation_refused", axis);
  }
  const dealRef = evaluate({ tour_activity: { deal_id: "deal-fixture-1" } });
  assert.equal(dealRef.reason_id, "implicit_deal_mutation_refused");
});

test("an activity record on an Assignment never reports a phase change or a Deal", () => {
  const ok = record(evaluateTourAssignmentActivity({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    assignment_id: ASSIGNMENT, tour_id: TOUR, activity_kind: "tour_created",
    declared_actor_slug: PARTNER, attended_intent: V5_J301_ATTENDED_INTENT,
    occurred_at: "2026-09-11T14:30:00Z",
    activity_payload: { stop_count: 4, market: "fixture-market" },
  }));
  assert.equal(ok.decision, "unavailable");
  assert.equal(ok.reason_id, "attended_actor_source_unavailable");
  assert.ok(ok.owed_seams.includes(j301.V5_J301_ASSIGNMENT_ACTIVITY_OWNER_SEAM));
  assert.equal(ok.assignment_phase_changed, false);
  assert.equal(ok.deal_created, false);
  assert.equal(ok.preserves_history, true);
  assert.ok(ok.owed_seams.includes(V5_J301_MAP_CONTRACT_RECEIPT_STEP));

  const phase = record(evaluateTourAssignmentActivity({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    assignment_id: ASSIGNMENT, tour_id: TOUR, activity_kind: "tour_conducted",
    declared_actor_slug: PARTNER, attended_intent: V5_J301_ATTENDED_INTENT,
    occurred_at: "2026-09-11T14:30:00Z",
    activity_payload: { assignment_phase: "negotiation" },
  }));
  assert.equal(phase.decision, "refused");
  assert.equal(phase.reason_id, "implicit_assignment_phase_transition_refused");
  assert.equal(phase.assignment_phase_changed, false);
});

test("a correction names its target, and only a correction may", () => {
  const missing = record(evaluateTourAssignmentActivity({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    assignment_id: ASSIGNMENT, tour_id: TOUR, activity_kind: "tour_corrected",
    declared_actor_slug: PARTNER, attended_intent: V5_J301_ATTENDED_INTENT,
    occurred_at: "2026-09-11T15:00:00Z",
  }));
  assert.equal(missing.reason_id, "correction_must_name_its_target");

  const wrong = record(evaluateTourAssignmentActivity({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    assignment_id: ASSIGNMENT, tour_id: TOUR, activity_kind: "tour_created",
    declared_actor_slug: PARTNER, attended_intent: V5_J301_ATTENDED_INTENT,
    occurred_at: "2026-09-11T15:00:00Z", corrects_activity_id: "activity-fixture-1",
  }));
  assert.equal(wrong.reason_id, "only_a_correction_may_name_a_prior_activity");

  const good = record(evaluateTourAssignmentActivity({
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    assignment_id: ASSIGNMENT, tour_id: TOUR, activity_kind: "tour_corrected",
    declared_actor_slug: PARTNER, attended_intent: V5_J301_ATTENDED_INTENT,
    occurred_at: "2026-09-11T15:00:00Z", corrects_activity_id: "activity-fixture-1",
  }));
  assert.equal(good.decision, "unavailable");
  assert.equal(good.preserves_history, true);
});

// ---------------------------------------------------------------------------
// A caller may not be its own gate.
// ---------------------------------------------------------------------------

test("a caller-supplied receipt, approval or override never reaches the request", () => {
  // Two different noes, and the difference is deliberate. At the top level the
  // closed schema refuses the field outright — the module cannot read the
  // request at all. Inside the Tour activity, which is an open payload by
  // nature, the same names are refused as a policy answer the caller may record.
  for (const field of V5_J301_CALLER_AUTHORITY_FIELDS) {
    const request = stageRequest();
    request[field] = { issued_by: "the caller", status: "pass" };
    assert.throws(() => evaluateStageAction(request),
      error => error instanceof V5J301Error && error.code === "unknown_field", field);

    const nested = record(evaluateStageAction(stageRequest({
      tour_activity: { evidence: { [field]: { status: "pass" } } },
    })));
    assert.equal(nested.decision, "refused", field);
    assert.equal(nested.reason_id, "caller_supplied_authority_field", field);
    assert.equal(nested.field, field);
    assert.equal(nested.field_path, `request.tour_activity.evidence.${field}`, field);
  }
});

test("the tenant is the tenant, whatever the caller says", () => {
  assert.throws(() => evaluateStageAction(stageRequest({ organization_tenant_id: "some-other-tenant" })),
    error => error.code === "tenant_mismatch");
});

// ---------------------------------------------------------------------------
// The honest unavailable, and the gaps it comes from.
// ---------------------------------------------------------------------------

test("a well-formed, rule-abiding DETERMINISTIC action is unavailable and says exactly why", () => {
  // A deterministic action is the only kind that reaches the journal answer:
  // every attended one stops one question earlier, at the missing authenticator.
  const result = evaluate({
    stage: "deterministic_normalization", action_kind: "normalize_property_fact",
    actor_class: "deterministic",
  });
  assert.equal(result.decision, "unavailable");
  assert.equal(result.reason_id, "workflow_journal_owner_unavailable");
  assert.deepEqual([...result.owed_seams],
    [V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM, V5_J301_MAP_CONTRACT_RECEIPT_STEP]);
  assert.equal(result.map_contract_gate, V5_J301_MAP_CONTRACT_GATE);
  assert.equal(result.map_contract_production_status,
    "approved_architecture_not_implemented_in_production");
  assert.equal(result.intended_verb, "append-tour-field-assertion");
  assert.equal(result.intended_verb_adapter_bound, false);
  assert.equal(result.intended_verb_adapter_seam, j301.V5_J301_VERB_ADAPTER_SEAM);
  assert.equal(result.governed_state_applied, false);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

test("the projection reads its claims off the gaps rather than beside them", () => {
  const gaps = record(tourWorkflowGaps());
  const projection = record(v5J301TourWorkflowProjection());
  assert.equal(projection.resume_reachable_today, gaps.advance_reachable_here);
  assert.equal(projection.resume_reason_id, gaps.advance_reason_id);
  assert.equal(projection.map_contract_production_status, gaps.map_contract_production_status);
  assert.equal(gaps.stage_journal_owner_exists_here, false);
  assert.equal(gaps.journal_reader_exists_here, false);
  assert.equal(gaps.attended_actor_source_exists_here, false);
  assert.equal(gaps.attended_actor_source_seam, j301.V5_J301_ATTENDED_ACTOR_SOURCE_SEAM);
  assert.equal(gaps.identity_seam_module, "mcp-server/src/identity.js");
  assert.equal(gaps.verb_adapter_exists_here, false);
  assert.equal(gaps.intended_verbs_are_named_not_traversed, true);
  assert.equal(projection.human_presence_provable_here, false);
  assert.equal(projection.attended_actions_reachable_today, false);
  assert.equal(projection.attended_actions_reason_id, "attended_actor_source_unavailable");
  assert.equal(projection.declared_actor_slug_is_authority, false);
  assert.equal(gaps.map_contract_receipt_exists_here, false);
  assert.equal(projection.tour_may_change_assignment_phase, false);
  assert.equal(projection.tour_may_create_or_execute_deal, false);
  assert.equal(projection.resume_state_read_from, "durable_record_only");
  assert.equal(projection.resume_state_read_from_session_memory, false);
  // The three surfaces this slice was told to leave alone.
  assert.equal(gaps.public_projection_here, false);
  assert.equal(gaps.share_grant_issuance_here, false);
  assert.equal(gaps.pdf_render_request_here, false);
});

test("the consumer gates are the seven the decisions carry", () => {
  assert.deepEqual([...V5_J301_CONSUMER_GATES], [
    "global-execution-contract-accepted",
    "global-phi-boundary-accepted",
    "global-prompt-injection-boundary-accepted",
    "global-secrets-boundary-accepted",
    "global-source-authority-accepted",
    "journey-three-preactivation-contract-bound",
    "tour-map-contract-1.2.0-accepted",
  ]);
});

test("the policy digest moves when the policy moves", () => {
  const first = v5J301PolicyDigest();
  assert.equal(first, v5J301PolicyDigest());
  assert.match(first, /^sha256:[0-9a-f]{64}$/);
  const preimage = v5J301PolicyPreimage();
  assert.ok(Object.isFrozen(preimage));
  assert.equal(v5J301PolicyCanonicalBytes(), JSON.stringify(JSON.parse(v5J301PolicyCanonicalBytes())));
  assert.deepEqual([...preimage.stages], [...V5_J301_STAGES]);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
});

// ---------------------------------------------------------------------------
// The stage registry against the deployed record layer.
// ---------------------------------------------------------------------------

test("every verb the stage registry names exists in the deployed registry", () => {
  assert.ok(V5_J301_INTENDED_VERBS.length >= 7);
  for (const verb of V5_J301_INTENDED_VERBS) {
    assert.ok(Object.hasOwn(TOOLS, verb), `${verb} is not a deployed verb`);
  }
  // And the control: a verb this slice invented would fail the same check.
  assert.equal(Object.hasOwn(TOOLS, "append-tour-workflow-stage"), false);
});

test("every registered action names a registered actor class", () => {
  for (const stage of V5_J301_STAGES) {
    for (const [kind, action] of Object.entries(V5_J301_STAGE_ACTIONS[stage])) {
      assert.ok(V5_J301_ACTOR_CLASSES.includes(action.actor_class), `${stage}.${kind}`);
    }
  }
});

// ---------------------------------------------------------------------------
// The public surface: no privileged outcome, from any input, under any name.
// ---------------------------------------------------------------------------

const PRIVILEGED = Object.freeze([
  "allow", "allowed", "commit", "committed", "prompt", "suppress", "release",
  // `read` is in the standing rule's own list and was missing from this guard.
  // It is the outcome that matters most here: the whole slice turns on nothing
  // being READ from an authority that does not exist.
  "read", "covered", "coverage_complete", "drafted", "proposed", "queued",
  "healthy", "passing", "passable", "green", "advance", "advanced", "resume_at",
  "admitted", "accepted", "approved", "authorized", "granted", "applied",
  "attended", "verified", "present",
]);

/**
 * Keys whose CONTENT is a list of names — field names this module refuses, and
 * vocabulary it imports from elsewhere — rather than an outcome of its own.
 * "allow" appears in the refusal list and "committed" is one of J102's
 * Assignment phases; reporting either as a privileged outcome would invert
 * their meaning. Everything outside these keys is swept, keys and values alike.
 */
const NAME_BEARING_KEYS = Object.freeze([
  "caller_authority_fields", "forbidden_activity_fields", "assignment_phases",
  "registered", "field", "field_path",
]);

function privilegedHit(value, key = "", depth = 0) {
  if (depth > 12 || NAME_BEARING_KEYS.includes(key)) return null;
  if (PRIVILEGED.includes(key)) return key;
  if (typeof value === "string") return PRIVILEGED.includes(value) ? value : null;
  if (Array.isArray(value)) {
    for (const entry of value) {
      const hit = privilegedHit(entry, key, depth + 1);
      if (hit) return hit;
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const [inner, entry] of Object.entries(value)) {
      const hit = privilegedHit(entry, inner, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

test("the privileged-string detector actually fires", () => {
  assert.equal(privilegedHit({ decision: "allow" }), "allow");
  assert.equal(privilegedHit({ nested: [{ state: "queued" }] }), "queued");
  assert.equal(privilegedHit({ deep: { deeper: { verdict: "passing" } } }), "passing");
  assert.equal(privilegedHit({ allow: true }), "allow");
  assert.equal(privilegedHit({ decision: "unavailable" }), null);
  // The documented exception, and it is narrow: a refusal that names the field
  // it refused is not a grant.
  assert.equal(privilegedHit({ reason_id: "caller_supplied_authority_field", field: "allow" }), null);
});

/**
 * EVERY CALLABLE ON THE PUBLIC SURFACE, enumerated from the module namespace
 * rather than from a hand-kept list — so a function added tomorrow is swept
 * tomorrow, without anyone remembering to add it here.
 */
function callablePublicExports() {
  return Object.entries(j301)
    .filter(([, value]) => typeof value === "function" && !/^V5J301Error$/.test(value.name))
    .map(([name, fn]) => [name, fn]);
}

/** Every caller-controlled shape any of them could ever be handed. */
function callerControlledShapes() {
  const shapes = [];
  for (const stage of V5_J301_STAGES) {
    for (const kind of Object.keys(V5_J301_STAGE_ACTIONS[stage])) {
      for (const actor_class of V5_J301_ACTOR_CLASSES) {
        for (const intent of [V5_J301_ATTENDED_INTENT, ...V5_J301_REFUSED_INTENTS]) {
          for (const declared_actor_slug of DECLARED_SLUGS) {
            shapes.push(stageRequest({
              stage, action_kind: kind, actor_class, attended_intent: intent,
              declared_actor_slug,
            }));
            // The same shape WITH a journal, so the sweep covers the refusal
            // path a caller reaches by handing one in as well as the path it
            // reaches by not.
            shapes.push(stageRequest({
              stage, action_kind: kind, actor_class, attended_intent: intent,
              declared_actor_slug,
              journal_view: V5_J301_STAGES.slice(0, V5_J301_STAGE_INDEX[stage]).map(earlier =>
                journalEntry(earlier, Object.keys(V5_J301_STAGE_ACTIONS[earlier])[0])),
            }));
          }
        }
      }
    }
  }
  // The shapes that try hardest to be a grant, plus the unreadable ones.
  shapes.push(stageRequest({ tour_activity: { gate_receipt: { status: "pass" }, allow: true } }));
  shapes.push(stageRequest({ tour_activity: { assignment_phase: "committed" } }));
  shapes.push({ ...stageRequest(), verified: true, admission: "allow", approved: true });
  shapes.push({ decision: "allow", resume_at: "client_facing_review" });
  shapes.push({}, null, undefined, "allow", 1, true, [], [{ allow: true }]);
  return shapes;
}

test("NO callable on the public surface yields a privileged outcome, from any input", () => {
  const callables = callablePublicExports();
  // The list is derived, so assert it actually found the surface rather than an
  // empty set that would make this test vacuous.
  assert.ok(callables.length >= 9, `only ${callables.length} callables found`);
  for (const name of ["evaluateStageAction", "evaluateTourAssignmentActivity",
    "readTourWorkflowResumePoint", "tourWorkflowStepKey", "assertTourWorkflowJournalEntry",
    "assertJ301DecisionBinding", "tourWorkflowGaps", "v5J301TourWorkflowProjection",
    "v5J301PolicyPreimage", "v5J301PolicyDigest", "v5J301PolicyCanonicalBytes"]) {
    assert.ok(callables.some(([one]) => one === name), `${name} is not being swept`);
  }

  const shapes = callerControlledShapes();
  assert.ok(shapes.length > 100, `expected a wide sweep, built ${shapes.length} shapes`);
  let evaluated = 0;
  for (const [name, fn] of callables) {
    for (const shape of shapes) {
      let result;
      try { result = fn(shape); } catch (error) {
        // A contract violation is a legitimate answer for a shape the module
        // cannot read. It is never a privileged outcome.
        assert.ok(error instanceof V5J301Error || error instanceof TypeError,
          `${name} threw something other than a boundary error: ${error}`);
        continue;
      }
      evaluated++;
      if (result && typeof result === "object") {
        record(result);
        if ("decision" in result) {
          assert.ok(V5_J301_DECISIONS.includes(result.decision),
            `${name} answered ${JSON.stringify(result.decision)}`);
        }
      }
      const hit = privilegedHit(result);
      assert.equal(hit, null,
        `${name} leaked privileged token "${hit}" for ${String(JSON.stringify(shape)).slice(0, 120)}`);
    }
  }
  assert.ok(evaluated > 500, `expected a wide sweep, evaluated ${evaluated}`);
});

test("every result this suite produced reports no effect and no lifecycle move", () => {
  assert.ok(EVERY_RESULT.length > 100, `swept ${EVERY_RESULT.length} results`);
  for (const result of EVERY_RESULT) {
    if (result && typeof result === "object") {
      if ("effects" in result) assert.deepEqual(result.effects, V5_NO_EFFECTS);
      if ("governed_state_applied" in result) assert.equal(result.governed_state_applied, false);
      if ("assignment_phase_changed" in result) assert.equal(result.assignment_phase_changed, false);
      if ("deal_created" in result) assert.equal(result.deal_created, false);
      assert.equal(privilegedHit(result), null);
    }
  }
});

// ---------------------------------------------------------------------------
// THE PARSER, AND WHY THIS FILE NO LONGER CONTAINS ONE.
//
// Two hand-written checks lived here and both were holes. A regex over the
// source called an identifier inside a comment a reference. A hand tokenizer
// that fixed that could not see a dynamic `import()` at all, and a scan built on
// vm.SourceTextModule's `dependencySpecifiers` could not either — that list is
// the module record's STATIC import list, so `await import("...")` is simply
// absent from it, and this repository uses dynamic imports (see
// mcp-server/src/tour-runtime.js). A guard that proves "production cannot reach
// the test helper" while being blind to the one form that most plausibly would
// reach it proves nothing.
//
// So the parse is done by esbuild, which is present in mcp-server/node_modules,
// and which reports a module's imports — static, dynamic and require() — with
// the kind of each. This is the pattern the correspondence slice already proves
// on main (mcp-server/test/governed-correspondence.v5.test.mjs); it is copied
// rather than reinvented.
//
// Two things esbuild cannot answer are answered CONSERVATIVELY, on purpose. A
// call site whose specifier is not a literal (`import(name)`) resolves to no
// path at all, and `createRequire` reaches CommonJS without the token
// `require(` appearing. Both are found by counting names in the raw text, which
// OVER-reports — a mention inside a comment counts — and that is the safe
// direction: a false alarm is a review, a missed call site is a door.
// ---------------------------------------------------------------------------

/** esbuild refuses to run at all if it is missing, rather than degrading quietly. */
assert.equal(typeof esbuild.buildSync, "function",
  "the isolation guard needs a real parser; esbuild is not loadable");

/** Parse `source` and return esbuild's import records: `{ path, kind }` each. */
function importRecords(source) {
  const built = esbuild.buildSync({
    stdin: { contents: source, loader: "js", sourcefile: "module-under-guard.js", resolveDir: SRC_DIR },
    bundle: false, write: false, metafile: true,
    format: "esm", platform: "neutral", logLevel: "silent", logLimit: 0,
  });
  const output = Object.values(built.metafile.outputs)[0];
  return output === undefined ? [] : output.imports;
}

/**
 * How many times `callee(` appears in the raw text, as a whole word.
 * Deliberately a SUPERSET, compared only against what the parser resolved.
 */
function callSiteCount(source, callee) {
  return source.split(new RegExp(`(?<![\\w$.])${callee}\\s*\\(`)).length - 1;
}

/**
 * Every module specifier a source loads, by any form that actually loads —
 * static import, re-export, dynamic `import()`, and `require()`.
 *
 * A call site the parser could not resolve to a literal is reported as
 * `<computed import>` / `<computed require>` rather than dropped, and a source
 * that so much as names `createRequire` reports it, because a closed allow-list
 * has to notice exactly the specifiers it cannot see.
 */
function importSpecifiers(source) {
  const records = importRecords(source);
  const specifiers = new Set(records.map(record => record.path));
  const resolved = kind => records.filter(record => record.kind === kind).length;
  if (callSiteCount(source, "import") > resolved("dynamic-import")) specifiers.add("<computed import>");
  if (callSiteCount(source, "require") > resolved("require-call")) specifiers.add("<computed require>");
  if (/(?<![\w$])createRequire(?![\w$])/.test(source)) specifiers.add("createRequire");
  return [...specifiers].sort();
}

/**
 * Identifiers this slice's production modules may not REFERENCE FREELY, each
 * mapped to a token that cannot occur in source.
 *
 * The key is what esbuild's `define` rewrites and the value is what it rewrites
 * it to, so finding the token in the printed output is proof that the source
 * referenced the NAME. `define` substitutes identifier references only: never a
 * string, a comment, or a property name. That is exactly the identifier-level
 * question the hand tokenizer was written to answer, asked instead of the
 * parser that already read the file.
 *
 * WHAT IT DELIBERATELY DOES NOT CATCH, said plainly rather than assumed away:
 * a name a LOCAL OR IMPORTED BINDING shadows is that binding, not a free
 * reference, and `define` leaves it alone. The self-test below proves that
 * behaviour rather than pretending otherwise, and the gap is closed from two
 * other directions — `importedBindings` reads the names actually taken from
 * identity.js out of the linker, and the slug-indifference sweep proves
 * behaviourally that no answer varies with the slug, which is the property a
 * membership test would have to break. `actor_slug` is not on this list for
 * that reason: as a field spelling it would always arrive bound, so a define
 * entry for it would have measured nothing.
 */
const FORBIDDEN_IDENTIFIER_DEFINES = {
  isKnownPartner: "__J301_FORBIDDEN_IS_KNOWN_PARTNER__",
  __V5_J301_TEST_ONLY__: "__J301_FORBIDDEN_TEST_ONLY__",
  classifyResumePoint: "__J301_FORBIDDEN_CLASSIFY_RESUME_POINT__",
};

/**
 * The names a source binds out of one specifier — the IMPORTED names, not the
 * local aliases. Read out of the linker rather than off a token stream: the
 * source is bundled against an EMPTY stub for each relative dependency, and
 * esbuild reports one "No matching export" per name the source asked for.
 * Copied from the correspondence slice's guard on main.
 */
function importedBindings(source, specifier) {
  const directory = mkdtempSync(path.join(tmpdir(), "j301-import-guard-"));
  try {
    writeFileSync(path.join(directory, "entry.js"), source);
    for (const record of importRecords(source)) {
      if (record.path.startsWith(".")) writeFileSync(path.join(directory, record.path), "export {};\n");
    }
    let diagnostics = [];
    try {
      esbuild.buildSync({
        entryPoints: [path.join(directory, "entry.js")],
        bundle: true, write: false, format: "esm", platform: "node",
        packages: "external", treeShaking: false, logLevel: "silent", logLimit: 0,
      });
    } catch (failure) {
      diagnostics = failure.errors ?? [];
    }
    const wanted = path.basename(specifier);
    const names = new Set();
    for (const diagnostic of diagnostics) {
      const match = /^No matching export in "(.+)" for import "(.+)"$/.exec(diagnostic.text);
      if (match !== null && path.basename(match[1]) === wanted) names.add(match[2]);
    }
    return [...names].sort();
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

/** Which forbidden identifiers a source actually REFERENCES, by name. */
function forbiddenIdentifiers(source) {
  const printed = esbuild.transformSync(source, {
    loader: "js", format: "esm", platform: "neutral",
    define: FORBIDDEN_IDENTIFIER_DEFINES,
    minify: false, legalComments: "none", logLevel: "silent", logLimit: 0,
  }).code;
  return Object.entries(FORBIDDEN_IDENTIFIER_DEFINES)
    .filter(([, token]) => printed.includes(token))
    .map(([name]) => name)
    .sort();
}

test("the import parser reads the forms the two hand-written guards missed", () => {
  const cases = [
    ['import "./plain.js";', ["./plain.js"]],
    ['import /* x */ "./commented.js";', ["./commented.js"]],
    ['import\u00a0"./nbsp.js";', ["./nbsp.js"]],
    ['export * from "./star-export.js";', ["./star-export.js"]],
    // THE FORM vm.SourceTextModule COULD NOT SEE. dependencySpecifiers returns
    // the static list only, so this line was invisible to the previous guard.
    ['await import("./dynamic.js");', ["./dynamic.js"]],
    ['const p = import(\n  /* lazy */ "./dynamic-multiline.js"\n);', ["./dynamic-multiline.js"]],
    ['const m = await import("./pre" + "fix.js");', ["./prefix.js"]],
    // A specifier the parser cannot fold is NAMED rather than skipped.
    ['const m = await import(name);', ["<computed import>"]],
    ['const m = require(name);', ["<computed require>"]],
    // And the shapes that only LOOK like imports.
    ['// import "./commented-out.js";\nimport "./real.js";', ["./real.js"]],
    ['const s = "import \\"./in-a-string.js\\";";', []],
    ['const u = import.meta.url;', []],
  ];
  for (const [source, expected] of cases) {
    assert.deepEqual(importSpecifiers(source), expected.sort(), source);
  }
});

test("the identifier scan reads references and ignores comments, strings and properties", () => {
  assert.deepEqual(forbiddenIdentifiers("const x = isKnownPartner(slug);"), ["isKnownPartner"]);
  assert.deepEqual(forbiddenIdentifiers("const f = isKnownPartner;"), ["isKnownPartner"]);
  assert.deepEqual(forbiddenIdentifiers("const r = classifyResumePoint(view);"), ["classifyResumePoint"]);
  assert.deepEqual(forbiddenIdentifiers("// isKnownPartner in a line comment"), []);
  assert.deepEqual(forbiddenIdentifiers("/* isKnownPartner */"), []);
  assert.deepEqual(forbiddenIdentifiers('const a = "isKnownPartner";'), []);
  assert.deepEqual(forbiddenIdentifiers("const re = /isKnownPartner/;"), []);
  // A PROPERTY of that name is not a reference to the identifier — the case the
  // hand tokenizer got wrong in the other direction.
  assert.deepEqual(forbiddenIdentifiers("const o = { isKnownPartner: 1 }; o.isKnownPartner;"), []);

  // AND THE LIMIT, MEASURED RATHER THAN ASSUMED. A name a binding shadows is
  // that binding, so define leaves it alone. This test exists so nobody reads
  // the guard as stronger than it is; `importedBindings` below is what closes
  // the case that actually matters here.
  assert.deepEqual(forbiddenIdentifiers("function isKnownPartner() {} isKnownPartner();"), []);
  assert.deepEqual(forbiddenIdentifiers('import { isKnownPartner } from "./identity.js"; isKnownPartner(x);'), []);
  assert.deepEqual(
    importedBindings('import { isKnownPartner } from "./identity.js"; isKnownPartner(x);', "./identity.js"),
    ["isKnownPartner"], "the linker must see a name the define scan cannot");
});

test("the module's real exports are EXACTLY its declared surface, with no exception", () => {
  // The previous shape of this slice asserted "the declared surface PLUS the
  // quarantined member", which blessed a real production export by naming it.
  // Excluding a name from a list does not make an ESM export private, so there
  // is no longer a name to exclude.
  assert.deepEqual(Object.keys(j301).sort(), [...V5_J301_PUBLIC_SURFACE].sort());
  for (const name of Object.keys(j301)) {
    assert.equal(name.startsWith("__"), false, `${name} is a test-shaped export`);
    assert.equal(/TEST_ONLY|TESTONLY|_test_/i.test(name), false, `${name} is a test-shaped export`);
    assert.equal(/^(classify|wouldResume)/.test(name), false,
      `${name} is a classifier name on the public surface`);
  }
  // And the map half, to the same standard.
  assert.deepEqual(Object.keys(command).sort(), [...command.V5_J301_COMMAND_PUBLIC_SURFACE].sort());
});

// ---------------------------------------------------------------------------
// ISOLATION, PARSED — STATICALLY AND DYNAMICALLY.
//
// The claim under test is exactly this: NO PRODUCTION MODULE CAN REACH THE
// CLASSIFIER HELPER. The previous guard read vm.SourceTextModule's
// `dependencySpecifiers`, which is the static import list, so a production
// module could have written `await import("../test/...testhelper.mjs")` and the
// guard would have stayed green. The control below proves the new guard catches
// exactly that line.
// ---------------------------------------------------------------------------

const TEST_TREE_SPECIFIER = /\/test\/|^\.\.\/test|\.testonly\.|\.testhelper\.|\.test-entry\./;

/** Every specifier each module under src/ loads, by any form that loads. */
function srcImports() {
  const out = {};
  for (const name of readdirSync(SRC_DIR).sort()) {
    if (!name.endsWith(".js")) continue;
    out[name] = importSpecifiers(readFileSync(path.join(SRC_DIR, name), "utf8"));
  }
  return out;
}

test("ISOLATION: src holds no test-only entry, and none of it reaches the test tree", () => {
  const strays = readdirSync(SRC_DIR).filter(name => /\.(testonly|testhelper|test-entry)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");

  const imports = srcImports();
  // The parser must have seen this slice at all, or the scan proves nothing.
  assert.ok(Object.hasOwn(imports, "tour-workflow-j301.v5.js"));
  assert.ok(Object.hasOwn(imports, "tour-map-command-j301.v5.js"));
  assert.ok(Object.keys(imports).length > 50, "every module in src must have been parsed");

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one => TEST_TREE_SPECIFIER.test(one)))
    .map(([name]) => name);
  assert.deepEqual(offenders, [], "a production module reached into the test directory");

  // And specifically: this slice's two modules load exactly these, none of them
  // a classifier helper, and none of them computed.
  assert.deepEqual(imports["tour-workflow-j301.v5.js"],
    ["./artifact-trust.js", "./cre-lifecycle.v5.js", "./global-boundaries.v5.js", "./identity.js"]);
  assert.deepEqual(imports["tour-map-command-j301.v5.js"],
    ["./artifact-trust.js", "./global-boundaries.v5.js", "./identity.js",
      "./tour-workflow-j301.v5.js"]);
});

test("ISOLATION: the guard catches a DYNAMIC import of the helper, which is the form that got past it", () => {
  const helperSpecifier = "../test/tour-workflow-classifiers.v5.testhelper.mjs";

  // (a) THE REAL MODULE, edited the way a future change plausibly would be: one
  // dynamic import appended to the actual production source. The guard must
  // report it, and it must report it for the RIGHT file.
  const real = readFileSync(path.join(SRC_DIR, "tour-workflow-j301.v5.js"), "utf8");
  const smuggled = `${real}\nexport async function reach() {\n  return await import("${helperSpecifier}");\n}\n`;
  const found = importSpecifiers(smuggled);
  assert.ok(found.includes(helperSpecifier), `the dynamic import was invisible: ${found.join(", ")}`);
  assert.ok(found.some(one => TEST_TREE_SPECIFIER.test(one)),
    "a dynamic import of the test tree did not trip the test-tree rule");

  // (b) THE CONTROL THAT PROVES THE PREVIOUS GUARD WAS BLIND. V8's own module
  // record lists static specifiers only, so the same source produces no mention
  // of the helper at all. This is not a hypothetical: it is why this block was
  // rewritten.
  const viaModuleRecord = execFileSync(process.execPath,
    ["--experimental-vm-modules", "-e", `
      const vm = require("node:vm");
      const source = require("node:fs").readFileSync(process.argv[1], "utf8");
      process.stdout.write(JSON.stringify(
        new vm.SourceTextModule(source, { identifier: "probe.js" }).dependencySpecifiers));
    `, "/dev/stdin"], { input: smuggled, encoding: "utf8" });
  assert.equal(JSON.parse(viaModuleRecord).includes(helperSpecifier), false,
    "dependencySpecifiers unexpectedly saw a dynamic import; the premise of this test has changed");

  // (c) The static form is caught too, so the new guard is a superset and not a
  // trade of one blind spot for another.
  assert.ok(importSpecifiers(`import * as helper from "${helperSpecifier}";`)
    .includes(helperSpecifier));

  // (d) A specifier assembled at runtime cannot slip past as "nothing found":
  // it is reported as computed, which fails the closed-set assertion above.
  assert.ok(importSpecifiers("const h = await import(helperPath);").includes("<computed import>"));

  // (e) And the repository really does use the form this guards against, which
  // is why a static-only scan was not good enough here.
  const runtime = readFileSync(path.join(SRC_DIR, "tour-runtime.js"), "utf8");
  assert.ok(importRecords(runtime).some(record => record.kind === "dynamic-import"),
    "tour-runtime.js was expected to carry a dynamic import");
});

test("ISOLATION: the classifier helper lives in the test tree and answers conditionally", () => {
  const helper = path.join(HERE, "tour-workflow-classifiers.v5.testhelper.mjs");
  execFileSync(process.execPath, ["--check", helper]);
  const source = readFileSync(helper, "utf8");
  // It cites the public module's own reason registry rather than inventing one.
  assert.ok(source.includes("V5_J301_JOURNAL_OWNER_REASON_IDS"));
  for (const forbidden of ["resume_at:", "decision:", "admitted:", "allow:"]) {
    assert.equal(source.includes(`\n    ${forbidden}`), false, `${forbidden} is a privileged field`);
  }
  const answer = wouldBeResumePointIfAuthoritative([], BINDING);
  assert.equal(answer.is_not_authority, true);
  assert.equal(answer.evidence_source, V5_J301_CLASSIFIER_EVIDENCE_SOURCE);
  assert.equal(Object.hasOwn(answer, "decision"), false);
  assert.equal(Object.hasOwn(answer, "resume_at"), false);
  assert.equal(answer.would_be_open_stage_if_authoritative, "attended_mls_acquisition");
});

test("ISOLATION: no production module runs a partner membership test for this slice", () => {
  const files = readdirSync(SRC_DIR).filter(name => name.startsWith("tour-") && name.includes("j301"));
  assert.deepEqual(files.sort(), ["tour-map-command-j301.v5.js", "tour-workflow-j301.v5.js"]);
  for (const name of files) {
    const referenced = forbiddenIdentifiers(readFileSync(path.join(SRC_DIR, name), "utf8"));
    // Nothing on the forbidden list at all: no membership test, no quarantined
    // export, and no resume classifier left behind in production.
    assert.deepEqual(referenced, [], name);
    execFileSync(process.execPath, ["--check", path.join(SRC_DIR, name)]);
  }
});

// ---------------------------------------------------------------------------
// THE PUBLIC-ONLY PROBE.
//
// Everything above about the classifier is an argument about where code lives.
// This is the measurement: a caller holding NOTHING but the public module, with
// every input it can construct, and the question asked directly — can any
// journal-derived field, or any resume position, be recovered from what comes
// back? The previous correction failed exactly here, so the check is the whole
// answer rather than a spot check.
// ---------------------------------------------------------------------------

/** Field names that would only ever be derived by reading a journal. */
const JOURNAL_DERIVED_FIELDS = Object.freeze([
  "stages_seen", "earliest_unstarted_stage", "missing_stage", "furthest_stage_seen",
  "open_stage", "next_stage", "resume_stage", "resume_at", "highest_seen_index",
  "step_keys", "already_recorded_in_view", "entry_index", "entry_tour_id",
  "entry_assignment_id", "journal_authority",
]);

/** Every value found under `name`, at any depth. */
function valuesForField(value, name, into = [], depth = 0) {
  if (depth > 12 || value === null || typeof value !== "object") return into;
  if (Array.isArray(value)) {
    for (const entry of value) valuesForField(entry, name, into, depth + 1);
    return into;
  }
  for (const [key, entry] of Object.entries(value)) {
    if (key === name) into.push(entry);
    valuesForField(entry, name, into, depth + 1);
  }
  return into;
}

test("PUBLIC-ONLY PROBE: no journal-derived field comes back from any public answer", () => {
  const answers = [];
  for (const request of callerControlledShapes()) {
    try {
      answers.push(evaluateStageAction(request));
    } catch (error) {
      assert.ok(error instanceof V5J301Error);
    }
  }
  // Every public export that takes no request, swept alongside them.
  for (const [, fn] of callablePublicExports()) {
    try { answers.push(fn()); } catch (error) { assert.ok(error instanceof V5J301Error); }
  }
  assert.ok(answers.length > 200, `expected the full matrix, swept ${answers.length}`);

  for (const answer of answers) {
    for (const derived of JOURNAL_DERIVED_FIELDS) {
      // A field that is PRESENT AND NULL is a statement that there is no such
      // value — `readTourWorkflowResumePoint` says `resume_stage: null` and
      // `next_stage: null` on purpose, and deleting those would make the answer
      // less honest, not more. What must never appear is CONTENT.
      for (const value of valuesForField(answer, derived)) {
        assert.equal(value, null,
          `${derived} came back carrying a value: ${JSON.stringify(answer).slice(0, 240)}`);
      }
    }
  }
});

test("PUBLIC-ONLY PROBE: no resume classification is recoverable from the public refusals", () => {
  // THE EXACT ATTACK THE REVIEWER RAN. Build a journal, hand it to the public
  // path at every stage, and try to read the position back out of the answers.
  // Under the previous shape this recovered "deterministic_normalization". The
  // recovery now has nothing to work with: every stage answers identically,
  // because the journal is refused before it is read.
  const view = historyThrough("deterministic_normalization");
  const perStage = V5_J301_STAGES.map(stage => {
    const action_kind = Object.keys(V5_J301_STAGE_ACTIONS[stage])[0];
    return record(evaluateStageAction(stageRequest({
      stage, action_kind,
      actor_class: V5_J301_STAGE_ACTIONS[stage][action_kind].actor_class,
      journal_view: view,
    })));
  });

  // Identical but for the request fields the caller itself supplied: strip
  // those and the five answers are byte-identical, so the ANSWERS carry no
  // information about the journal at all.
  const stripped = perStage.map(answer => {
    const copy = { ...answer };
    delete copy.stage; delete copy.action_kind; delete copy.step_key;
    return JSON.stringify(copy);
  });
  for (const one of stripped) assert.equal(one, stripped[0]);
  for (const answer of perStage) {
    assert.equal(answer.reason_id, "caller_supplied_journal_view_refused");
  }

  // A DIFFERENT journal gives the same answers too. If any position leaked, a
  // history reaching stage four would have to differ from one reaching stage
  // one somewhere in the result.
  for (const other of [[], historyThrough("attended_mls_acquisition"),
    historyThrough("deterministic_generation")]) {
    const answer = evaluateStageAction(stageRequest({ journal_view: other }));
    const copy = { ...answer };
    delete copy.stage; delete copy.action_kind; delete copy.step_key;
    assert.equal(JSON.stringify(copy), stripped[0],
      "two different journals produced two different public answers");
  }
});
