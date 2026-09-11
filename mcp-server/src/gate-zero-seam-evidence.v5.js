// DoctorCRE v5 slice V5-A02, the seam half: THE DERIVATION, AND IT TOUCHES NO
// STORE.
//
// Three pure functions. Each takes rows someone else fetched and returns what
// those rows would support IF THEY WERE AUTHORITATIVE — which is exactly what
// their names say, and why their names say it. Nothing here knows where a row
// came from, so nothing here can claim a row is authoritative. That claim is
// made in one place only: gate-zero-seam-readers.v5.js, after it has checked
// that the seam's ruling is on the record and fetched the rows itself from the
// ruled store.
//
// WHY THE CONDITIONAL NAMING IS LOAD-BEARING. The standing rule learned from
// nine review rounds on 2026-09-11 forbids any exported function that turns
// caller input into a privileged outcome, under any name, including a
// "predicate" or "fixture" variant. These three are the decision logic. Hand
// them fabricated rows and they will tell you what those fabricated rows would
// support — which is the honest answer to the question they were asked, and is
// why the answer is called `would_*_if_rows_were_authoritative` and never `ok`,
// `pass`, `green` or `allow`. No word in the union appears in any value any of
// them returns.
//
// AND WHY THE FINDING IDS ARE SHARED. The reader restates the SAME id under the
// field name `finding` once it has provenance. One vocabulary, two field names:
// conditional without provenance, asserted with it. A consumer that reads
// `finding` is reading a store; a consumer that reads
// `would_admit_if_rows_were_authoritative` is reading a hypothetical, and the
// field name is the only place that distinction could live where it cannot be
// lost.
//
// TIMESTAMPS ARE COMPARED AS INSTANTS, NEVER AS STRINGS. `Date.parse` or
// nothing: an unparseable timestamp is a missing timestamp, and a missing
// timestamp refuses. Two run rows written inside one transaction share an
// instant (now() is constant in a transaction), so "strictly after" is strictly
// after — equal instants do not satisfy it.

const SHA256 = /^sha256:[0-9a-f]{64}$/;

/**
 * Every finding this module can reach. Closed, so a finding is checkable.
 *
 * NINE OF THESE ARE THE GATE'S OWN REASON IDS, reused verbatim from
 * V5_A02_GATE_ZERO_REASON_IDS rather than paraphrased, so a reader's refusal and
 * the gate's refusal are the same word for the same condition. The four that are
 * new are new conditions a reader can see and the gate could not: a row that is
 * simply absent, a service the ledger does not carry, an acceptance receipt whose
 * hash is not the one asked about, and a conclusion actually observed.
 */
export const GATE_ZERO_SEAM_FINDINGS = Object.freeze([
  "gate_conclusion_check_absent",
  "gate_conclusion_observed",
  "predecessor_outcome_absent",
  "predecessor_outcome_acceptance_receipt_hash_mismatch",
  "predecessor_outcome_accepted_with_matching_hash",
  "predecessor_outcome_not_accepted",
  "scheduler_canary_and_observation_join",
  "scheduler_canary_not_bound_to_receipt",
  "scheduler_dispatch_row_absent",
  "scheduler_readback_absent",
  "scheduler_readback_canary_mismatch",
  "scheduler_readback_not_after_dispatch",
  "scheduler_service_row_absent",
].sort());

function instant(value) {
  if (typeof value === "string" || value instanceof Date) {
    const parsed = Date.parse(value instanceof Date ? value.toISOString() : value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function text(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function conditional(finding, facts) {
  if (!GATE_ZERO_SEAM_FINDINGS.includes(finding))
    throw new TypeError(`${finding} is not a registered seam finding`);
  return Object.freeze({
    would_admit_if_rows_were_authoritative: finding === "predecessor_outcome_accepted_with_matching_hash"
      || finding === "scheduler_canary_and_observation_join"
      || finding === "gate_conclusion_observed",
    conditional_finding: finding,
    evidence_basis: "rows_supplied_to_this_function",
    ...facts,
  });
}

// ---------------------------------------------------------------------------
// Card 11 — an accepted predecessor outcome, and the hash it was asked about.
// ---------------------------------------------------------------------------

/**
 * THE TWO CONDITIONS, AND BOTH MUST HOLD.
 *
 *   1. A row's `status` is exactly "accepted". "pending_human_acceptance" is
 *      not a near miss; a proposal nobody signed is not an outcome.
 *   2. That row's ACCEPTANCE RECEIPT hash equals the outcome hash this reader
 *      was asked about. Not the proposal's `feedback_hash` — the receipt's,
 *      because the receipt is the row a human's acceptance wrote and the
 *      proposal is the row a machine wrote. A forged or stale hash therefore
 *      does not merely fail to match a proposal; it fails to match a signature.
 *
 * Neither condition can be satisfied by the `outcomeHash` argument alone: it is
 * compared against a stored value and never returned, so a caller who supplies
 * a hash learns only whether the store agrees with it.
 */
export function wouldAdmitPredecessorOutcome(rows, { outcomeHash }) {
  const all = Array.isArray(rows) ? rows : [];
  if (all.length === 0)
    return conditional("predecessor_outcome_absent", {
      outcome_rows_seen: 0, accepted_rows_seen: 0, hash_matches: false,
    });
  const accepted = all.filter(row => text(row?.status) === "accepted");
  if (accepted.length === 0)
    return conditional("predecessor_outcome_not_accepted", {
      outcome_rows_seen: all.length, accepted_rows_seen: 0, hash_matches: false,
    });
  const asked = typeof outcomeHash === "string" && SHA256.test(outcomeHash) ? outcomeHash : null;
  const matching = accepted.filter(row => {
    const receiptHash = text(row?.accepted_feedback_hash);
    return receiptHash !== null && asked !== null && receiptHash === asked;
  });
  if (matching.length === 0)
    return conditional("predecessor_outcome_acceptance_receipt_hash_mismatch", {
      outcome_rows_seen: all.length, accepted_rows_seen: accepted.length, hash_matches: false,
    });
  const row = matching[0];
  return conditional("predecessor_outcome_accepted_with_matching_hash", {
    outcome_rows_seen: all.length,
    accepted_rows_seen: accepted.length,
    hash_matches: true,
    feedback_ref: text(row?.feedback_ref),
    stored_outcome: text(row?.outcome),
    accepted_at: text(row?.accepted_at) ?? (row?.accepted_at instanceof Date ? row.accepted_at.toISOString() : null),
  });
}

// ---------------------------------------------------------------------------
// Card 12 — the canary, its receipt binding, and a readback strictly after
// dispatch.
// ---------------------------------------------------------------------------

/**
 * THE THREE CLAUSES GATE ZERO NAMES, each answered from a ledger row and its
 * timestamps, and a missing row refuses instead of defaulting.
 *
 *   bound_to_receipt        the dispatch row names its receipt: `evidence_ref`
 *                           is present and `source_kind`/`source_ref` say the
 *                           scheduler wrote it, not a hand-run.
 *   readback_after_dispatch the readback's `observed_at` is STRICTLY after the
 *                           dispatch row's `started_at`. Equal instants fail:
 *                           rows written in one transaction share now().
 *   canary_match            the readback is a readback OF THIS canary — same
 *                           `run_key`, same `evidence_ref`.
 *
 * The dispatch row is the one with the latest `started_at`; the readback is the
 * one with the latest `observed_at` among rows that also ENDED, because an
 * observation of a run still in flight is not a readback of its result. All
 * three booleans are returned every time rows allow them to be computed, so a
 * caller sees which clause failed rather than only that one did.
 */
export function wouldReportSchedulerCanary(rows) {
  const all = Array.isArray(rows) ? rows : [];
  const serviceRows = all.filter(row => text(row?.service_key) !== null);
  if (serviceRows.length === 0)
    return conditional("scheduler_service_row_absent", {
      service_rows_seen: 0, run_rows_seen: 0,
      bound_to_receipt: null, readback_after_dispatch: null, canary_match: null,
    });
  const runRows = serviceRows.filter(row => text(row?.run_key) !== null);
  const dispatched = runRows.filter(row => instant(row?.started_at) !== null);
  if (dispatched.length === 0)
    return conditional("scheduler_dispatch_row_absent", {
      service_rows_seen: serviceRows.length, run_rows_seen: runRows.length,
      bound_to_receipt: null, readback_after_dispatch: null, canary_match: null,
    });
  const dispatch = dispatched.reduce((a, b) => (instant(b.started_at) > instant(a.started_at) ? b : a));
  const observed = runRows.filter(row => instant(row?.observed_at) !== null && instant(row?.ended_at) !== null);
  if (observed.length === 0)
    return conditional("scheduler_readback_absent", {
      service_rows_seen: serviceRows.length, run_rows_seen: runRows.length,
      bound_to_receipt: null, readback_after_dispatch: null, canary_match: null,
    });
  const readback = observed.reduce((a, b) => (instant(b.observed_at) > instant(a.observed_at) ? b : a));

  const boundToReceipt = text(dispatch.evidence_ref) !== null
    && text(dispatch.source_kind) === "scheduler"
    && text(dispatch.source_ref) !== null;
  const readbackAfterDispatch = instant(readback.observed_at) > instant(dispatch.started_at);
  const canaryMatch = text(readback.run_key) !== null
    && text(readback.run_key) === text(dispatch.run_key)
    && text(readback.evidence_ref) !== null
    && text(readback.evidence_ref) === text(dispatch.evidence_ref);

  const facts = {
    service_rows_seen: serviceRows.length,
    run_rows_seen: runRows.length,
    bound_to_receipt: boundToReceipt,
    readback_after_dispatch: readbackAfterDispatch,
    canary_match: canaryMatch,
    dispatched_at: text(dispatch.started_at),
    observed_at: text(readback.observed_at),
  };
  if (!boundToReceipt) return conditional("scheduler_canary_not_bound_to_receipt", facts);
  if (!canaryMatch) return conditional("scheduler_readback_canary_mismatch", facts);
  if (!readbackAfterDispatch) return conditional("scheduler_readback_not_after_dispatch", facts);
  return conditional("scheduler_canary_and_observation_join", facts);
}

// ---------------------------------------------------------------------------
// Card 13 — what hosted CI concluded about one commit, under one check name.
// ---------------------------------------------------------------------------

/**
 * The conclusion GitHub holds, or nothing.
 *
 * A check run that has not COMPLETED has no conclusion, so it is not a check
 * run this function will speak for: `status` must be "completed" and
 * `conclusion` must be a non-empty string. Several completed runs under one
 * name on one commit (a re-run) resolve to the LATEST by `completed_at`, which
 * is what the merge gate itself acts on, and the count is reported so a reader
 * can see there was more than one.
 *
 * The conclusion is returned verbatim as GitHub's own word — "success",
 * "failure", "neutral", "cancelled", "timed_out", "action_required", "skipped",
 * "stale". This function does NOT map it to green, passing or ok. Whatever
 * consumes a gate conclusion decides what a conclusion means; a reader that
 * translated one would be that consumer wearing a reader's name.
 */
export function wouldReportGateConclusion(rows, { commitSha }) {
  const all = Array.isArray(rows) ? rows : [];
  const completed = all.filter(row =>
    text(row?.status) === "completed"
    && text(row?.conclusion) !== null
    && text(row?.head_sha) === commitSha);
  if (completed.length === 0)
    return conditional("gate_conclusion_check_absent", {
      check_runs_seen: all.length, completed_runs_seen: 0, conclusion: null,
    });
  const latest = completed.reduce((a, b) =>
    ((instant(b.completed_at) ?? 0) > (instant(a.completed_at) ?? 0) ? b : a));
  return conditional("gate_conclusion_observed", {
    check_runs_seen: all.length,
    completed_runs_seen: completed.length,
    conclusion: text(latest.conclusion),
    completed_at: text(latest.completed_at),
  });
}
