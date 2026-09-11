// V5-J301 TOUR WORKFLOW — THE TEST-ONLY CLASSIFIER ENTRY.
//
// READ THIS FIRST, BECAUSE THE FILE NAME IS THE CONTRACT. Nothing in this file
// is part of the V5-J301 public surface. `tour-workflow-j301.v5.js` does not
// import it, no production module imports it — statically or dynamically — and
// tour-workflow-j301.v5.test.mjs proves that with esbuild's parser over every
// module under src/, reading both the static and the dynamic import set, rather
// than with a promise. The only importer is the test file.
//
// WHY IT EXISTS, AND WHY THE PREVIOUS TWO SHAPES WERE BOTH WRONG. Q060.D1's
// staging rules rest on a resume CLASSIFICATION — which stage a Tour's workflow
// would stand at — and a suite that cannot reach that classification can prove
// nothing about the ordering the decision settles.
//
//   * The FIRST shape exported `__V5_J301_TEST_ONLY__` from the production
//     module. Excluding a name from a declared surface list does not make an
//     ESM export private: any consumer could import it.
//   * The SECOND shape deleted that export and reached the classification by
//     PROBING the public refusals — handing `evaluateStageAction` a journal and
//     reading `stages_seen`, `earliest_unstarted_stage` and the reason pattern
//     back out. That was worse in the way that matters: it worked, which is
//     exactly the demonstration that the public surface still handed a caller
//     the resume boundary. A refusal whose body reconstructs the position IS
//     the position.
//
// SO THE PRODUCTION MODULE NOW READS NO JOURNAL AT ALL — `journal_view` is
// refused by name — and the classification lives HERE, in the test tree, where
// production cannot reach it. This is the Gate Zero layout of
// mcp-server/src/gate-zero-assurance.v5.js and
// mcp-server/test/gate-zero-classifiers.v5.testhelper.mjs, followed
// deliberately: the public module owns the closed vocabulary, the test-tree
// helper owns the classification, and the helper answers in the conditional.
//
// THE ANSWERS ARE CONDITIONAL BY NAME, and there is no spelling in this file
// that says a thing happened:
//
//   would_be_open_stage_if_authoritative       where the workflow WOULD stand,
//   would_be_next_stage_if_authoritative       what WOULD come next, and whether
//   would_be_readable_history_if_authoritative the shape WOULD read as a history
//   would_be_admissible_if_authoritative       — IF an authoritative journal had
//                                              said this, which none did.
//
// Every result also carries `is_not_authority: true` and
// `evidence_source: "caller_supplied_shapes_not_authority"`, so a value that
// escaped into a consumer would still refuse to read as a position.
//
// EVERY REASON ID THIS FILE EMITS IS CITED FROM THE PUBLIC MODULE'S
// V5_J301_JOURNAL_OWNER_REASON_IDS. It invents none, so the vocabulary the
// journal-owner seam owes and the vocabulary this classification uses cannot
// drift apart — and an id this file misspells fails loudly instead of quietly
// describing a rule nobody will implement.
//
// Every function here is PURE — no filesystem, no network, no database, no
// clock, no environment.

import {
  V5_J301_JOURNAL_OWNER_REASON_IDS,
  V5_J301_STAGES,
  V5_J301_STAGE_ACTIONS,
  V5_J301_STAGE_INDEX,
  assertTourWorkflowJournalEntry,
  tourWorkflowStepKey,
} from "../src/tour-workflow-j301.v5.js";

/** Said on every result, so an escaped value still reads as "not authority". */
export const V5_J301_CLASSIFIER_EVIDENCE_SOURCE = "caller_supplied_shapes_not_authority";

/** The public module owns the closed reason registry; this file only cites it. */
function reason(id) {
  if (!V5_J301_JOURNAL_OWNER_REASON_IDS.includes(id)) {
    throw new Error(`${id} is not a reason the journal-owner seam declares`);
  }
  return id;
}

function frozen(value) {
  return Object.freeze({
    ...value,
    is_not_authority: true,
    evidence_source: V5_J301_CLASSIFIER_EVIDENCE_SOURCE,
  });
}

function unreadable(reasonId, detail) {
  return frozen({
    would_be_readable_history_if_authoritative: false,
    would_be_open_stage_if_authoritative: null,
    would_be_next_stage_if_authoritative: null,
    would_be_reason_id: reason(reasonId),
    detail: Object.freeze({ ...detail }),
  });
}

/**
 * Which stage a journal-shaped list WOULD say the workflow stands at.
 *
 * `binding` is `{ tour_id, assignment_id }` — the Tour the shapes are claimed to
 * be OF. It is required rather than inferred from the entries, because inferring
 * it from the entries is exactly how a list belonging to another Tour would be
 * read as this one's history.
 *
 * THE ORDERED QUESTIONS, so the classification can be checked rather than
 * trusted:
 *
 *   1. Is every entry readable at all?            -> throws (V5J301Error, from the
 *                                                    public module's own schema)
 *   2. Is every entry an entry of THIS Tour and
 *      Assignment?                                -> journal_entry_foreign_to_tour
 *   3. Does a stage with no entry sit before a
 *      stage that has one?                        -> journal_history_noncontiguous
 *   4. Otherwise, the furthest stage with any
 *      activity is the open stage, and the one
 *      after it is where work would begin next.
 *
 * WHY THE FURTHEST STAGE WITH ACTIVITY, AND NOT "THE FURTHEST COMPLETED STAGE".
 * Nothing in a journal entry says a stage FINISHED — a stage is a place work
 * happened, not a box that got ticked — so a classification claiming completion
 * would be inventing a fact. The furthest stage with activity is the strongest
 * honest reading, and it gives the two answers the staging rules need: an
 * interrupted stage is resumed (same index), and the stage after it may begin
 * (index + 1).
 */
export function wouldBeResumePointIfAuthoritative(entries, binding) {
  const { tour_id, assignment_id } = binding;
  const normalized = entries.map((entry, index) =>
    assertTourWorkflowJournalEntry(entry, `entries[${index}]`));

  for (let index = 0; index < normalized.length; index += 1) {
    const entry = normalized[index];
    if (entry.tour_id !== tour_id || entry.assignment_id !== assignment_id) {
      return unreadable("journal_entry_foreign_to_tour", {
        entry_index: index,
        entry_tour_id: entry.tour_id,
        entry_assignment_id: entry.assignment_id,
      });
    }
  }

  const seen = new Set(normalized.map(entry => entry.stage));
  let highestSeenIndex = -1;
  for (const stage of V5_J301_STAGES) {
    if (seen.has(stage)) highestSeenIndex = V5_J301_STAGE_INDEX[stage];
  }
  for (let index = 0; index < highestSeenIndex; index += 1) {
    if (!seen.has(V5_J301_STAGES[index])) {
      return unreadable("journal_history_noncontiguous", {
        missing_stage: V5_J301_STAGES[index],
        furthest_stage_seen: V5_J301_STAGES[highestSeenIndex],
      });
    }
  }

  return frozen({
    would_be_readable_history_if_authoritative: true,
    would_be_open_stage_if_authoritative:
      highestSeenIndex === -1 ? V5_J301_STAGES[0] : V5_J301_STAGES[highestSeenIndex],
    would_be_next_stage_if_authoritative: V5_J301_STAGES[highestSeenIndex + 1] ?? null,
    would_be_reason_id: null,
    detail: Object.freeze({
      stages_seen: Object.freeze(V5_J301_STAGES.filter(stage => seen.has(stage))),
      step_keys_cited: Object.freeze(normalized.map(entry => entry.step_key)),
    }),
  });
}

/**
 * Whether one intended stage action WOULD satisfy Q060.D1's staging rules
 * against a journal-shaped list.
 *
 * `action` is `{ stage, action_kind, action_subject_digest, corrects_step_key }`
 * — the position-bearing half of a stage-action request, and nothing else. The
 * attended, actor-class and lifecycle questions are the PUBLIC module's and are
 * answered there; this file duplicates none of them, so there is no second copy
 * of those rules to drift.
 *
 * THE ORDERED QUESTIONS, after the resume point above:
 *
 *   5. Does the stage jump past the stage after
 *      the furthest one with activity?            -> stage_skipped
 *   6. Does it move backwards naming no
 *      correction target?                         -> backward_stage_requires_correction
 *   7. Does it name a correction target the list
 *      does not hold?                             -> correction_target_absent
 *   8. Is this exact step key already in the
 *      list?                                      -> duplicate_step_key_replay
 *   9. Otherwise it WOULD satisfy the staging
 *      rules — and would still not be admitted,
 *      because attendance and the durable owner
 *      are both missing.
 */
export function wouldSatisfyStagingRulesIfAuthoritative(action, entries, binding) {
  const resume = wouldBeResumePointIfAuthoritative(entries, binding);
  if (!resume.would_be_readable_history_if_authoritative) {
    return frozen({
      would_be_admissible_if_authoritative: false,
      would_be_reason_id: resume.would_be_reason_id,
      resume_point: resume,
    });
  }

  if (!Object.hasOwn(V5_J301_STAGE_ACTIONS, action.stage)) {
    throw new Error(`${action.stage} is not a registered stage`);
  }
  if (!Object.hasOwn(V5_J301_STAGE_ACTIONS[action.stage], action.action_kind)) {
    throw new Error(`${action.action_kind} is not an action of ${action.stage}`);
  }

  const stepKey = tourWorkflowStepKey({
    tour_id: binding.tour_id,
    stage: action.stage,
    action_kind: action.action_kind,
    action_subject_digest: action.action_subject_digest,
  });
  const stepKeys = new Set(resume.detail.step_keys_cited);
  const openIndex = resume.detail.stages_seen.length === 0
    ? -1
    : V5_J301_STAGE_INDEX[resume.would_be_open_stage_if_authoritative];
  const requestedIndex = V5_J301_STAGE_INDEX[action.stage];
  const correctsStepKey = action.corrects_step_key ?? null;

  const refuse = (reasonId, detail) => frozen({
    would_be_admissible_if_authoritative: false,
    would_be_reason_id: reason(reasonId),
    resume_point: resume,
    detail: Object.freeze({ ...detail }),
  });

  if (requestedIndex > openIndex + 1) {
    return refuse("stage_skipped", {
      earliest_unstarted_stage: resume.would_be_next_stage_if_authoritative,
    });
  }
  if (requestedIndex < openIndex) {
    if (correctsStepKey === null) return refuse("backward_stage_requires_correction", {});
    if (!stepKeys.has(correctsStepKey)) {
      return refuse("correction_target_absent", { corrects_step_key: correctsStepKey });
    }
  }
  if (stepKeys.has(stepKey)) {
    return refuse("duplicate_step_key_replay", { step_key: stepKey });
  }

  return frozen({
    would_be_admissible_if_authoritative: true,
    would_be_reason_id: null,
    resume_point: resume,
    detail: Object.freeze({ step_key: stepKey }),
  });
}
