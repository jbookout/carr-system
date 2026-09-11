// V5-J301 TOUR WORKFLOW — THE TEST-ONLY CLASSIFIER ENTRY.
//
// READ THIS FIRST, BECAUSE THE FILE NAME IS THE CONTRACT. Nothing in this file
// is part of the V5-J301 public surface. `tour-workflow-j301.v5.js` does not
// import it, no production module imports it, and
// tour-workflow-j301.v5.test.mjs proves that with a parser-backed scan of every
// module under src/ rather than a promise. The only importer is the test file.
//
// WHY IT EXISTS. The staging rules of Q060.D1 rest on a resume CLASSIFICATION —
// which stage a Tour's workflow would stand at — and a suite that could not
// reach that classification could only prove the refusals, never the reading
// underneath them. The previous shape of this slice reached it by exporting a
// `__V5_J301_TEST_ONLY__` member from the production module. A reviewer was
// right that excluding a name from a declared surface list does not make an
// ESM export private: any consumer could import it and obtain a resume
// classification derived from a journal it wrote itself. That export is gone.
//
// HOW THIS REACHES THE CLASSIFICATION INSTEAD — and this is the part worth
// reading, because it is strictly stronger than a hidden hook. It does not
// reach INTO the module at all. It PROBES the public deny-only surface: for a
// given journal view it asks `evaluateStageAction` what would happen at each of
// the five stages, and derives the position from which stages refuse and how.
//
//   * a stage BELOW the furthest one with activity refuses
//     `backward_stage_requires_correction`, so the LOWEST stage that does not
//     refuse that way is the open stage — the furthest one with activity;
//   * a stage more than one past it refuses `stage_skipped`, so the FURTHEST
//     stage that does not refuse that way is where the next stage would begin,
//     and the two readings are cross-checked against each other.
//
// So there is no second implementation to drift from the first, no private
// member to quarantine, and nothing here that production could import even by
// accident. If the module's classification changes, this derivation changes
// with it, because it is reading the module's own answers.
//
// THE ANSWERS ARE CONDITIONAL BY NAME. There is no durable stage journal in
// this repository, so a position implied by a caller-supplied view is a
// HYPOTHETICAL and nothing else:
//
//   would_resume_at_if_authoritative     — where the workflow WOULD stand IF an
//   would_next_stage_be_if_authoritative   authoritative journal said this, which
//                                          no authoritative journal did.
//
// Every result also carries `is_not_authority: true` and
// `evidence_source: "caller_supplied_view_not_authority"`, so a value that
// escaped into a consumer would still refuse to read as a position.
//
// Every function here is PURE — no filesystem, no network, no database, no
// clock, no environment.

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_J301_ATTENDED_INTENT,
  V5_J301_STAGES,
  V5_J301_STAGE_ACTIONS,
  evaluateStageAction,
} from "../src/tour-workflow-j301.v5.js";

/** Said on every result, so an escaped value still reads as "not authority". */
export const V5_J301_CLASSIFIER_EVIDENCE_SOURCE = "caller_supplied_view_not_authority";

/** A subject digest that is a real digest and means nothing in particular. */
const PROBE_SUBJECT = `sha256:${"0".repeat(63)}1`;

/**
 * The probe request for one stage. It deliberately uses that stage's FIRST
 * registered action and that action's own required actor class, so the probe is
 * never refused for a reason unrelated to position — an actor-class mismatch
 * would mask the staging answer this derivation is reading.
 */
function probe(stage, { tour_id, assignment_id, journal_view }) {
  const action_kind = Object.keys(V5_J301_STAGE_ACTIONS[stage])[0];
  return {
    organization_tenant_id: ORGANIZATION_TENANT_ID,
    tour_id,
    assignment_id,
    stage,
    action_kind,
    declared_actor_slug: "probe-not-authority",
    attended_intent: V5_J301_ATTENDED_INTENT,
    actor_class: V5_J301_STAGE_ACTIONS[stage][action_kind].actor_class,
    action_subject_digest: PROBE_SUBJECT,
    journal_view,
  };
}

/**
 * Derive the hypothetical resume point of a journal view, by probing the public
 * surface stage by stage.
 *
 * `binding` is `{ tour_id, assignment_id }` — the Tour the view is claimed to
 * be OF. It is required rather than inferred from the entries, because
 * inferring it from the entries is exactly how a journal belonging to another
 * Tour used to be read as this one's history.
 *
 * When the view is not a readable history of that Tour at all — a foreign entry
 * or a stage gap — every probe refuses for that reason, and this returns the
 * refusal rather than a position. A view that cannot be this Tour's history has
 * no position to report.
 */
export function wouldResumeAtIfAuthoritative(journal_view, binding) {
  const { tour_id, assignment_id } = binding;
  const answers = V5_J301_STAGES.map(stage =>
    evaluateStageAction(probe(stage, { tour_id, assignment_id, journal_view })));

  const unreadable = answers.find(answer =>
    answer.reason_id === "journal_entry_foreign_to_tour" ||
    answer.reason_id === "journal_history_noncontiguous");
  if (unreadable) {
    return Object.freeze({
      would_resume_at_if_authoritative: null,
      would_next_stage_be_if_authoritative: null,
      view_is_a_readable_history: false,
      refused_reason_id: unreadable.reason_id,
      is_not_authority: true,
      evidence_source: V5_J301_CLASSIFIER_EVIDENCE_SOURCE,
    });
  }

  // THE OPEN STAGE is the lowest one that is not refused as a backward move: a
  // backward refusal means the journal already reaches further than that stage,
  // so the first stage without one IS the furthest stage with activity. An
  // empty view produces no backward refusals at all and lands on stage one,
  // which is the right reading: a Tour nobody has touched resumes at the start.
  let openIndex = V5_J301_STAGES.findIndex(
    (_, index) => answers[index].reason_id !== "backward_stage_requires_correction");
  if (openIndex === -1) openIndex = V5_J301_STAGES.length - 1;

  // WHERE THE NEXT STAGE WOULD BEGIN, read independently off the SKIP refusals:
  // the furthest stage that is not refused as a skip is the last one that may be
  // acted in at all. For an empty view that is stage one itself — nothing has
  // begun, so the next thing to begin IS the beginning — and for a view that
  // reaches the final stage there is nothing after it.
  let furthestActionable = -1;
  for (let index = 0; index < V5_J301_STAGES.length; index += 1) {
    if (answers[index].reason_id !== "stage_skipped") furthestActionable = index;
  }

  // THE CROSS-CHECK. Two independent readings of the same journal — one off the
  // backward refusals, one off the skip refusals — must agree that the workflow
  // may act in the open stage and at most one stage past it. If they disagree,
  // this derivation is wrong about the module and says so, rather than reporting
  // a position it cannot stand behind.
  if (furthestActionable < openIndex || furthestActionable > openIndex + 1) {
    return Object.freeze({
      would_resume_at_if_authoritative: null,
      would_next_stage_be_if_authoritative: null,
      view_is_a_readable_history: false,
      refused_reason_id: "probe_readings_disagree",
      is_not_authority: true,
      evidence_source: V5_J301_CLASSIFIER_EVIDENCE_SOURCE,
    });
  }

  return Object.freeze({
    would_resume_at_if_authoritative: V5_J301_STAGES[openIndex],
    would_next_stage_be_if_authoritative:
      openIndex === V5_J301_STAGES.length - 1 ? null : V5_J301_STAGES[furthestActionable],
    view_is_a_readable_history: true,
    refused_reason_id: null,
    probe_reason_ids: Object.freeze(answers.map(answer => answer.reason_id)),
    is_not_authority: true,
    evidence_source: V5_J301_CLASSIFIER_EVIDENCE_SOURCE,
  });
}
