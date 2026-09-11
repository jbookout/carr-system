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
//   * that NO JOURNAL A CALLER CAN SUPPLY produces an advance, because the
//     durable owner that would make "resume" true does not exist here and a
//     list in a request is not that owner,
//   * that NO PUBLIC EXPORT, over every caller-controlled input shape this
//     suite can build, ever yields a privileged outcome string,
//   * that the module's real export list is exactly its declared public surface
//     plus the quarantined test-only member, checked through the loader and
//     through a tokenizer over the source rather than by eye,
//   * and that every verb the stage registry names really exists in the
//     deployed record-layer registry.
//
// NO FIXTURE NAMES A REAL THING: every tour, assignment and property below is
// unmistakably test data.

import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

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
  wouldResumeAtIfAuthoritative,
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
    journal_view: [],
    ...overrides,
  };
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
  const identifiers = identifiersOutsideLiterals(source);
  assert.equal(identifiers.has("isKnownPartner"), false,
    "a partner membership test is back in the module");
  assert.equal(identifiers.has("actor_slug"), false,
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
    journal_view: [
      journalEntry("attended_mls_acquisition", "capture_listing_observation"),
      journalEntry("deterministic_normalization", "normalize_property_fact"),
    ],
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
    journal_view: [
      journalEntry("attended_mls_acquisition", "capture_listing_observation"),
      journalEntry("deterministic_normalization", "normalize_property_fact"),
    ],
  });
  assert.equal(result.decision, "refused");
  assert.equal(result.reason_id, "actor_class_mismatch");
  assert.equal(result.required_actor_class, "model_assisted");
});

// ---------------------------------------------------------------------------
// Staging: separate, ordered, resumable. Q060.D1.
// ---------------------------------------------------------------------------

test("a stage cannot skip the stage before it", () => {
  // The NEAREST illegal jump, which is the one an off-by-one would let through:
  // the journal reaches stage 0, and stage 2 is asked for. Proving only the
  // distant jump (0 to 3) would pass a rule that permitted every skip of one.
  const nearest = evaluate({
    stage: "agent_assisted_assembly", action_kind: "rank_candidate_stops",
    actor_class: "model_assisted",
    journal_view: [journalEntry("attended_mls_acquisition", "capture_listing_observation")],
  });
  assert.equal(nearest.decision, "refused");
  assert.equal(nearest.reason_id, "stage_skipped");
  assert.equal(nearest.earliest_unstarted_stage, "deterministic_normalization");
  assert.deepEqual([...nearest.stages_seen], ["attended_mls_acquisition"]);

  const distant = evaluate({
    stage: "client_facing_review", action_kind: "record_client_review_note",
    journal_view: [journalEntry("attended_mls_acquisition", "capture_listing_observation")],
  });
  assert.equal(distant.decision, "refused");
  assert.equal(distant.reason_id, "stage_skipped");

  // And the boundary from an empty journal: intake is the only stage that may
  // begin, and the very next stage is already a skip.
  const fromNothing = evaluate({ journal_view: [] });
  assert.equal(fromNothing.decision, "unavailable");
  const skipFromNothing = evaluate({
    stage: "deterministic_normalization", action_kind: "normalize_property_fact",
    actor_class: "deterministic", journal_view: [],
  });
  assert.equal(skipFromNothing.decision, "refused");
  assert.equal(skipFromNothing.reason_id, "stage_skipped");
  assert.deepEqual([...skipFromNothing.stages_seen], []);
});

test("the next stage after an interruption is not a skip", () => {
  const result = evaluate({
    stage: "deterministic_normalization", action_kind: "normalize_property_fact",
    actor_class: "deterministic",
    journal_view: [journalEntry("attended_mls_acquisition", "capture_listing_observation")],
  });
  assert.equal(result.decision, "unavailable");
  assert.equal(result.reason_id, "workflow_journal_owner_unavailable");
});

test("continuing inside an interrupted stage is not a skip either", () => {
  const result = evaluate({
    action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B,
    journal_view: [journalEntry("attended_mls_acquisition", "capture_listing_observation")],
  });
  assert.equal(result.decision, "unavailable");
});

test("a backward stage move must be a correction against an entry that exists", () => {
  const view = [
    journalEntry("attended_mls_acquisition", "capture_listing_observation"),
    journalEntry("deterministic_normalization", "normalize_property_fact", SUBJECT_B),
  ];
  const bare = evaluate({
    action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B, journal_view: view,
  });
  assert.equal(bare.decision, "refused");
  assert.equal(bare.reason_id, "backward_stage_requires_correction");

  const absent = evaluate({
    action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B,
    corrects_step_key: `sha256:${"e".repeat(64)}`, journal_view: view,
  });
  assert.equal(absent.decision, "refused");
  assert.equal(absent.reason_id, "correction_target_absent");

  // The same step key with its algorithm stripped is not a near miss to be
  // accepted helpfully; it is a different spelling, and it cannot be read.
  assert.throws(() => evaluateStageAction(stageRequest({
    action_kind: "attach_property_identifier", action_subject_digest: SUBJECT_B,
    corrects_step_key: assertTourWorkflowJournalEntry(view[0]).step_key.replace(/^sha256:/, ""),
    journal_view: view,
  })), error => error.code === "invalid_digest");
});

test("a correction whose target is in the view passes the staging rules", () => {
  const first = journalEntry("attended_mls_acquisition", "capture_listing_observation");
  const normalized = assertTourWorkflowJournalEntry(first);
  const view = [first, journalEntry("deterministic_normalization", "normalize_property_fact", SUBJECT_B)];
  const result = evaluate({
    action_kind: "capture_listing_observation", action_subject_digest: `sha256:${"c".repeat(64)}`,
    corrects_step_key: normalized.step_key, journal_view: view,
  });
  // It passes the STAGING rules — no skip, no unbacked backward move — and then
  // stops at the missing authenticator, because a correction is an attended act.
  assert.equal(result.decision, "unavailable");
  assert.equal(result.reason_id, "attended_actor_source_unavailable");
  assert.ok(result.owed_seams.includes(V5_J301_WORKFLOW_JOURNAL_OWNER_SEAM));
});

test("there is no field through which a correction can erase its target", () => {
  assert.throws(() => evaluateStageAction(stageRequest({ supersedes_step_key: `sha256:${"f".repeat(64)}` })),
    error => error.code === "unknown_field");
  assert.throws(() => evaluateStageAction(stageRequest({ delete_step_key: `sha256:${"f".repeat(64)}` })),
    error => error.code === "unknown_field");
});

test("a replayed action is refused as a duplicate rather than applied twice", () => {
  const view = [journalEntry("attended_mls_acquisition", "capture_listing_observation")];
  const replay = evaluate({ journal_view: view });
  assert.equal(replay.decision, "refused");
  assert.equal(replay.reason_id, "duplicate_step_key_replay");
  assert.equal(replay.already_recorded_in_view, true);

  // A DIFFERENT subject in the same stage is a different action, not a replay.
  const fresh = evaluate({ action_subject_digest: SUBJECT_B, journal_view: view });
  assert.equal(fresh.decision, "unavailable");
});

test("the hypothetical resume point tracks the journal, stage by stage", () => {
  const view = [];
  assert.equal(wouldResumeAtIfAuthoritative(view,
    { tour_id: TOUR, assignment_id: ASSIGNMENT }).would_resume_at_if_authoritative,
    "attended_mls_acquisition");

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
    const seen = record(wouldResumeAtIfAuthoritative(view,
      { tour_id: TOUR, assignment_id: ASSIGNMENT }));
    assert.equal(seen.would_resume_at_if_authoritative, stage, stage);
    assert.equal(seen.would_next_stage_be_if_authoritative, expectedNext, stage);
    assert.equal(seen.view_is_a_readable_history, true, stage);
    assert.equal(seen.is_not_authority, true);
    assert.equal(seen.evidence_source, V5_J301_CLASSIFIER_EVIDENCE_SOURCE);
  }
});

// ---------------------------------------------------------------------------
// A journal that cannot be this Tour's history. The two shapes a reviewer found
// reaching an answer they had no business reaching.
// ---------------------------------------------------------------------------

test("a journal holding only the final stage is refused, not read as stage five", () => {
  // The four stages before client_facing_review left no trace, so this cannot be
  // a history of an ordered five-stage workflow. It used to reach the ordinary
  // journal-owner answer, which treated it as a workflow standing at stage five.
  const view = [journalEntry("client_facing_review", "record_client_review_note")];
  const result = evaluate({
    stage: "client_facing_review", action_kind: "accept_route_for_client_review",
    journal_view: view,
  });
  assert.equal(result.decision, "refused");
  assert.equal(result.reason_id, "journal_history_noncontiguous");
  assert.equal(result.missing_stage, "attended_mls_acquisition");
  assert.equal(result.furthest_stage_seen, "client_facing_review");

  const seen = record(wouldResumeAtIfAuthoritative(view,
    { tour_id: TOUR, assignment_id: ASSIGNMENT }));
  assert.equal(seen.view_is_a_readable_history, false);
  assert.equal(seen.would_resume_at_if_authoritative, null);
  assert.equal(seen.refused_reason_id, "journal_history_noncontiguous");
});

test("every interior gap in a history is caught, not just the first stage", () => {
  // Stages 0 and 2 present, stage 1 missing. Naming only the first stage would
  // have let this through.
  const view = [
    journalEntry("attended_mls_acquisition", "capture_listing_observation"),
    journalEntry("agent_assisted_assembly", "rank_candidate_stops"),
  ];
  const result = evaluate({
    stage: "agent_assisted_assembly", action_kind: "draft_stop_narrative",
    actor_class: "model_assisted", journal_view: view,
  });
  assert.equal(result.decision, "refused");
  assert.equal(result.reason_id, "journal_history_noncontiguous");
  assert.equal(result.missing_stage, "deterministic_normalization");
});

test("an entry belonging to another Tour cannot change this Tour's answer", () => {
  // A cross-Tour entry at a later stage used to make a perfectly ordinary
  // request look like a backward move.
  const foreign = journalEntry("client_facing_review", "record_client_review_note", SUBJECT_B,
    { tour_id: "tour-j301-fixture-9999" });
  const result = evaluate({
    journal_view: [journalEntry("attended_mls_acquisition", "capture_listing_observation", SUBJECT_B), foreign],
  });
  assert.equal(result.decision, "refused");
  assert.equal(result.reason_id, "journal_entry_foreign_to_tour");
  assert.equal(result.entry_tour_id, "tour-j301-fixture-9999");
  assert.equal(result.entry_index, 1);
});

test("an entry belonging to another Assignment is refused the same way", () => {
  const foreign = journalEntry("attended_mls_acquisition", "capture_listing_observation", SUBJECT_B,
    { assignment_id: "assignment-j301-fixture-9999" });
  const result = evaluate({ journal_view: [foreign] });
  assert.equal(result.decision, "refused");
  assert.equal(result.reason_id, "journal_entry_foreign_to_tour");
  assert.equal(result.entry_assignment_id, "assignment-j301-fixture-9999");
});

test("a journal entry that does not say which Tour it belongs to cannot be read", () => {
  for (const missing of ["organization_tenant_id", "tour_id", "assignment_id"]) {
    const entry = journalEntry("attended_mls_acquisition", "capture_listing_observation");
    delete entry[missing];
    assert.throws(() => evaluateStageAction(stageRequest({ journal_view: [entry] })),
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
    journal_view: historyThrough("attended_mls_acquisition"),
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
// The export guard. Loader-enumerated, tokenizer-checked; no regex over source.
// ---------------------------------------------------------------------------

/**
 * A small JavaScript tokenizer: it walks the source character by character and
 * skips string literals, template literals, regular-expression literals and
 * comments, so an identifier mentioned inside a comment or a string is not
 * mistaken for a reference. `node --check` runs first, so the source it walks
 * is known to parse.
 */
function identifiersOutsideLiterals(source) {
  const identifiers = new Set();
  let index = 0;
  let current = "";
  let previousSignificant = "";
  const flush = () => {
    if (current) { identifiers.add(current); previousSignificant = current; current = ""; }
  };
  while (index < source.length) {
    const char = source[index];
    const next = source[index + 1];
    if (/[A-Za-z0-9_$]/.test(char)) { current += char; index++; continue; }
    flush();
    if (char === "/" && next === "/") {
      while (index < source.length && source[index] !== "\n") index++;
      continue;
    }
    if (char === "/" && next === "*") {
      index += 2;
      while (index < source.length && !(source[index] === "*" && source[index + 1] === "/")) index++;
      index += 2;
      continue;
    }
    if (char === '"' || char === "'" || char === "`") {
      const quote = char;
      index++;
      while (index < source.length) {
        if (source[index] === "\\") { index += 2; continue; }
        if (source[index] === quote) { index++; break; }
        index++;
      }
      continue;
    }
    if (char === "/" && !/[A-Za-z0-9_$)\]]/.test(previousSignificant.slice(-1) || "")) {
      index++;
      let inClass = false;
      while (index < source.length) {
        if (source[index] === "\\") { index += 2; continue; }
        if (source[index] === "[") inClass = true;
        else if (source[index] === "]") inClass = false;
        else if (source[index] === "/" && !inClass) { index++; break; }
        else if (source[index] === "\n") break;
        index++;
      }
      continue;
    }
    if (!/\s/.test(char)) previousSignificant = char;
    index++;
  }
  flush();
  return identifiers;
}

test("the tokenizer ignores identifiers that live inside comments and strings", () => {
  const sample = [
    "// isKnownPartner in a line comment",
    "/* isKnownPartner in a block comment */",
    'const a = "isKnownPartner";',
    "const re = /isKnownPartner/;",
    "const real = realIdentifier;",
  ].join("\n");
  const found = identifiersOutsideLiterals(sample);
  assert.equal(found.has("isKnownPartner"), false);
  assert.equal(found.has("realIdentifier"), true);
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
// ISOLATION, PARSED RATHER THAN GREPPED.
//
// V8's own ESM parser, through vm.SourceTextModule in a child process — a real
// parser, and the one Node itself uses. `dependencySpecifiers` is the module
// record's own import list, so a dynamic or concatenated specifier cannot hide
// from it the way it could from a regex.
// ---------------------------------------------------------------------------

function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const dir = process.argv[1];
    const out = {};
    for (const name of readdirSync(dir).sort()) {
      if (!name.endsWith(".js")) continue;
      const source = readFileSync(join(dir, name), "utf8");
      out[name] = new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
    }
    process.stdout.write(JSON.stringify(out));
  `;
  const stdout = execFileSync(process.execPath,
    ["--experimental-vm-modules", "-e", script, directory], { encoding: "utf8" });
  return JSON.parse(stdout);
}

test("ISOLATION: src holds no test-only entry, and none of it reaches the test tree", () => {
  const strays = readdirSync(SRC_DIR).filter(name => /\.(testonly|testhelper|test-entry)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");

  const imports = moduleImports(SRC_DIR);
  // The parser must have seen this slice at all, or the scan proves nothing.
  assert.ok(Object.hasOwn(imports, "tour-workflow-j301.v5.js"));
  assert.ok(Object.hasOwn(imports, "tour-map-command-j301.v5.js"));
  assert.ok(Object.keys(imports).length > 50, "every module in src must have been parsed");

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one =>
      one.includes("/test/") || one.startsWith("../test") ||
      one.includes(".testonly.") || one.includes(".testhelper.")))
    .map(([name]) => name);
  assert.deepEqual(offenders, [], "a production module reached into the test directory");

  // And specifically: this slice's two modules import exactly these, none of
  // them a classifier helper.
  assert.deepEqual(imports["tour-workflow-j301.v5.js"],
    ["./artifact-trust.js", "./identity.js", "./global-boundaries.v5.js", "./cre-lifecycle.v5.js"]);
  assert.deepEqual(imports["tour-map-command-j301.v5.js"],
    ["./artifact-trust.js", "./identity.js", "./global-boundaries.v5.js",
      "./tour-workflow-j301.v5.js"]);
});

test("ISOLATION: the classifier helper lives in the test tree and answers conditionally", () => {
  const helper = path.join(HERE, "tour-workflow-classifiers.v5.testhelper.mjs");
  execFileSync(process.execPath, ["--check", helper]);
  const source = readFileSync(helper, "utf8");
  // It reaches the classification by PROBING the public surface; it holds no
  // second copy of the staging rules to drift from the first.
  assert.ok(source.includes("evaluateStageAction"));
  for (const forbidden of ["resume_at:", "decision:", "admitted:", "allow:"]) {
    assert.equal(source.includes(`\n    ${forbidden}`), false, `${forbidden} is a privileged field`);
  }
  const answer = wouldResumeAtIfAuthoritative([], { tour_id: TOUR, assignment_id: ASSIGNMENT });
  assert.equal(answer.is_not_authority, true);
  assert.equal(answer.evidence_source, V5_J301_CLASSIFIER_EVIDENCE_SOURCE);
  assert.equal(Object.hasOwn(answer, "decision"), false);
  assert.equal(Object.hasOwn(answer, "resume_at"), false);
  assert.equal(answer.would_resume_at_if_authoritative, "attended_mls_acquisition");
});

test("ISOLATION: no production module runs a partner membership test for this slice", () => {
  const files = readdirSync(SRC_DIR).filter(name => name.startsWith("tour-") && name.includes("j301"));
  assert.deepEqual(files.sort(), ["tour-map-command-j301.v5.js", "tour-workflow-j301.v5.js"]);
  for (const name of files) {
    const identifiers = identifiersOutsideLiterals(readFileSync(path.join(SRC_DIR, name), "utf8"));
    assert.equal(identifiers.has("isKnownPartner"), false, name);
    assert.equal(identifiers.has("__V5_J301_TEST_ONLY__"), false, name);
    execFileSync(process.execPath, ["--check", path.join(SRC_DIR, name)]);
  }
});
