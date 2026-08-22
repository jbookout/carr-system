import test from "node:test";
import assert from "node:assert/strict";

import {
  actionForCard,
  lifecycleForCard,
  renderCurrentWorkRequests,
  renderSystemWorkCard,
  validateHumanRef,
} from "../../dealroom/js/system-work-view.js";

const card = {
  human_ref: "WR-000123",
  title: "Rehearse the Worker recovery path",
  state: "captured",
  version: 1,
  desired_outcome: "A partner can verify the recovery receipt.",
  acceptance_criteria: [{ id: "criterion-1", text: "A safe receipt reads back." }],
  source: { label: "Release recovery doctrine", freshness: "current", provenance: "shared doctrine" },
  triage: null,
  plan: null,
  pending_outcome_feedback: null,
  outcome_feedback: null,
  outcome_feedback_history: [],
  accepted_feedback_count: 0,
};

test("System work accepts only opaque human Work Request references", () => {
  assert.equal(validateHumanRef("WR-000123"), "WR-000123");
  for (const value of ["", "wr-1", "WR-abc", "550e8400-e29b-41d4-a716-446655440000", "WR-1/../../x"])
    assert.throws(() => validateHumanRef(value), /work request reference/i);
});

test("captured, triaged, and ready cards expose exactly one safe next action", () => {
  assert.deepEqual(actionForCard(card), { kind: "triage", label: "Confirm classification" });
  assert.deepEqual(actionForCard({ ...card, state: "triaged", triage: { classification: "routine_operations" } }),
    { kind: "prepare-plan", label: "Prepare bounded plan" });
  assert.deepEqual(actionForCard({ ...card, state: "triaged", plan: { plan_ref: "PLAN-1" } }),
    { kind: "accept-plan", label: "Accept this plan" });
  assert.deepEqual(actionForCard({ ...card, state: "ready", plan: { plan_ref: "PLAN-1", accepted_at: "2026-08-16T12:00:00Z" } }),
    { kind: "record-outcome", label: "Record what happened" });
  assert.deepEqual(actionForCard({ ...card, state: "ready", plan: { plan_ref: "PLAN-1" },
    pending_outcome_feedback: { feedback_ref: "FEEDBACK-1" } }),
    { kind: "accept-outcome", label: "Accept this outcome record" });
});

test("a card that becomes stale after opening is read-only with an honest explanation", () => {
  const stale = { ...card, source: { ...card.source, freshness: "stale" } };
  assert.equal(actionForCard(stale), null);
  const html = renderSystemWorkCard(stale);
  assert.match(html, /read-only until its source is current again/i);
  assert.doesNotMatch(html, /class="system-work-primary"/);
});

test("lifecycle is text and shape based, with no execution or close stage", () => {
  const life = lifecycleForCard({ ...card, state: "ready", outcome_feedback: { feedback_ref: "FEEDBACK-1" } });
  assert.deepEqual(life.map((step) => step.label), [
    "Source concern", "Captured", "Human triage", "Bounded plan",
    "Plan accepted", "Outcome proposed", "Outcome accepted",
  ]);
  assert.equal(life.at(-1).status, "recorded");
  assert.ok(life.every((step) => ["recorded", "current", "upcoming"].includes(step.status)));
  assert.doesNotMatch(JSON.stringify(life), /execute|dispatch|deploy|close/i);
});

test("rendered card is truthful, durable, and contains one primary action", () => {
  const html = renderSystemWorkCard({ ...card, state: "ready",
    plan: { plan_ref: "PLAN-0001", scope_summary: "Observe the pre-authored recovery routine.",
      runbook_label: "Worker recovery rehearsal", runbook_version: 1,
      caps: { max_steps: 6, max_minutes: 20 }, accepted_by_actor_slug: "joe", accepted_at: "2026-08-16T12:00:00Z" },
    outcome_feedback: { feedback_ref: "FEEDBACK-1", outcome: "criteria_met",
      result_summary: "The receipt read back.", observed_minutes: 12,
      interaction_surface: "workspace", heavy_session_used: false,
      manual_context_transfers: 0, accepted_by_actor_slug: "dell", accepted_at: "2026-08-16T12:10:00Z" },
    accepted_feedback_count: 1,
  });
  assert.match(html, /Observation accepted/);
  assert.match(html, /did not execute or close/i);
  assert.match(html, /No heavy AI session/);
  assert.match(html, /0 manual context transfers/);
  assert.doesNotMatch(html, />complete</i);
  assert.equal((html.match(/class="system-work-primary"/g) || []).length, 1);
  assert.doesNotMatch(html, /550e8400|SELECT |shell command|generic executor/i);
});

test("first use renders genuine current requests and a safe no-demo empty state", () => {
  const current = renderCurrentWorkRequests([{ human_ref: "WR-000123", title: "Current source review", state: "captured", source: { freshness: "current" }, next_human_action: "Review and triage" }]);
  assert.match(current, /WR-000123/);
  assert.match(current, /current source/);
  assert.match(current, /data-open-work-request="WR-000123"/);
  const empty = renderCurrentWorkRequests([]);
  assert.match(empty, /No eligible Work Requests right now/i);
  assert.match(empty, /safe empty state/i);
  assert.match(empty, /not an idea, routine question, or a demo/i);
  assert.doesNotMatch(empty, /WR-\d+/);
});
