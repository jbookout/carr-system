// DoctorCRE v5 slice V5-A02, the seam half: THE THREE READERS GATE ZERO IS
// OWED — built, tested, and reading nothing.
//
// WHAT THIS IS FOR. Gate Zero refuses today with
// `predecessor_outcome_reader_unavailable`, and it refuses BEFORE it looks at
// any Work Request, because nothing in this system can read the evidence it
// would join. Four seams are owed. Cards 11, 12 and 13 of
// JOE-GATE-ZERO-CARDS.md propose a store for three of them. Joe has not ruled.
//
// So the readers are built and the switch is left off. The moment a ruling
// lands, the only thing that changes in this repository is one `null` on one
// line of gate-zero-seam-rulings.v5.js. Nothing here is written that day.
//
// THE RULING GATE, and it is the first thing every reader does.
//
//   1. Ask the ruling table whether THIS CARD is ruled, by card token.
//   2. If it is not — which today it is not, because all three decision ids are
//      null — RETURN THE GATE'S OWN REFUSAL, by calling
//      `readGateZeroPredecessorJoin()` or `readGateGraphAssurance()` and
//      returning what they return, unmodified. Not a copy, not a lookalike: the
//      gate's own function's own answer, so the two cannot drift.
//   3. If the ruling names a store this card's reader does not serve, that is
//      also not a ruling for this reader, and step 2 stands.
//   4. Only then look at the query. Only then open the store.
//
// The order is the point. A reader that validated the query first would leak
// which queries are well-formed; a reader that opened its store first would
// have read an unruled store even if it threw the answer away. Half an answer
// from an unruled store is still an unruled read.
//
// THE PUBLIC SURFACE IS FOUR NAMES. Three readers and a schema version string.
// There is no exported classifier, no exported predicate, no exported ruling
// table, no exported findings vocabulary and no exported status object. The
// derivation — the part that turns rows into a finding — is module-private,
// below, and there is no route to it that does not go through a reader that has
// already checked a ruling and fetched the rows itself.
//
// WHAT A CALLER MAY SAY, AND WHAT IT MAY NEVER SAY. A query ADDRESSES a row:
// which predecessor step, which service and canary, which commit and check.
// Addressing is not asserting. There is no argument for a decision id, a store
// handle, a connection, a reader object, a row, a receipt or an outcome — and
// there is no environment variable that opens a seam either. The only way to
// change what these functions answer is to change what is in the store, or to
// paste a ruling into a file and commit it.
//
// NO CALLER TEXT COMES BACK OUT, AND NO STORE TEXT EITHER. Every query field is
// taken through a try/catch (a Proxy trap or a throwing getter is caught, not
// propagated) and then matched against a strict pattern. A field that passes is
// USED and never echoed. What a result carries instead is `query_digest` — a
// digest over the NORMALIZED query — plus counts, closed-vocabulary enums, and
// identifiers taken from this module's own frozen tables. Free text out of a
// store does not reach an answer either: not a feedback ref, not a stored
// outcome, not a timestamp. Hand these functions a hostile object and the answer
// contains none of its bytes; point them at a store full of hostile rows and the
// answer contains none of those either.
//
// NO PRIVILEGED WORD COMES OUT, AND THERE IS NO EXEMPTION LIST. The union the
// standing rule closes — allow, commit, prompt, suppress, release, read,
// covered, drafted, proposed, queued, healthy, passing, ok, pass, satisfied,
// complete, admitted, resumed, attended, verified, present, equivalent,
// operational, active, green, joins_exactly, coverage_complete, favorable,
// anything `would_*`, anything `*_if_authoritative` — appears in NO value any
// export of this module returns, as word, as token or as raw substring, and the
// test sweeps for it with no carve-out at all.
//
// The earlier draft of this module needed a carve-out, because it reused the
// gate's own reason ids and seam names verbatim and those contain "read"
// ("read-only", "readback") and "active" ("scheduler-active-receipt"). That
// carve-out is deleted. Two things replaced it, and neither is an exemption:
//
//   * The seams are addressed by OPAQUE CARD TOKENS — "card:11", "card:12",
//     "card:13" — everywhere a value could carry one. A gate seam name never
//     enters an answer this module builds.
//   * This module's own finding vocabulary is its own. `scheduler_readback_*`
//     became `scheduler_observation_*`. The clause is the same clause; the word
//     is one this module is free to choose, and it chose one with no privileged
//     substring in it.
//
// What remains is the UNRULED answer, which is not this module's output at all:
// it is the gate's object, returned by delegation. The test does not sweep it
// for words — it pins it, whole, by digest, to what `gate-zero-assurance.v5.js`
// returns on main. That is strictly stronger than a word sweep, because
// identity admits no new string of any kind, privileged or not.
//
// THE FOURTH SEAM IS NOT BUILT, AND IS NOT COPIED HERE.
// `seam:gate-zero-read-only-outcome-producer` is cards 9 and 10 — which
// independent seat holds the oracle, and whether r7 itself carries the
// registration. Those are not questions about where to read from; they are
// questions about who signs. Its refusal is `emitGateZeroOutcome()` in
// gate-zero-assurance.v5.js, which already says all of it; an earlier draft
// carried a written-out copy of that refusal as an exported constant, and the
// copy has been deleted rather than kept in sync. One place says it.
//
// WHAT IS STILL OWED AFTER A RULING, said now so nobody reads a ruling as more
// than it is: WHICH canary the scheduler reader should be pointed at, and WHICH
// commit and check the conclusion reader should be pointed at, are the
// consumer's bindings, and the consumer is the Gate Zero producer — the seam
// that is deliberately not built. These readers will answer truthfully about
// whatever row they are pointed at. Pointing them is the producer's job.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_SCHEDULER_STEP_REF,
  readGateGraphAssurance,
  readGateZeroPredecessorJoin,
} from "./gate-zero-assurance.v5.js";
import { seamRulingRef } from "./gate-zero-seam-rulings.v5.js";
import {
  fetchCheckConclusionRows,
  fetchPredecessorOutcomeRows,
  fetchSchedulerLedgerRows,
  isSeamStoreUnreachable,
} from "./gate-zero-seam-stores.v5.js";

/**
 * The schema id, and it deliberately says "evidence" rather than "readers": no
 * value this module returns may contain a privileged word even as a raw
 * substring, and "readers" carries one. The sweep is the reason, and keeping the
 * reason visible here is cheaper than rediscovering it.
 */
export const GATE_ZERO_SEAM_READERS_SCHEMA_VERSION = "doctorcre-v5-a02-gate-zero-seam-evidence.v1";

// ---------------------------------------------------------------------------
// The closed vocabularies. All module-private: a consumer that wants to know
// what a reader can say reads this file.
// ---------------------------------------------------------------------------

/** The opaque tokens the three cards are addressed by. Nothing else names them. */
const CARD_11 = "card:11";
const CARD_12 = "card:12";
const CARD_13 = "card:13";

/**
 * The ONE store ref each card's reader will serve, and the fetcher that serves
 * it. This is the reader's half of the two-part binding: the ruling names a
 * store, this table says which store this reader can actually open, and unless
 * they are the same string no row is fetched at all. A ruling that named
 * `github:checks` for card 11 is not a ruling this reader can act on, and the
 * gate's refusal stands — rather than the old behaviour, which reported the
 * ruled ref while querying its own hard-coded store.
 */
const CARD_STORE = Object.freeze({
  [CARD_11]: Object.freeze({ store_ref: "record-layer:work-request-outcome-feedback",
    fetch: fetchPredecessorOutcomeRows }),
  [CARD_12]: Object.freeze({ store_ref: "control-plane:ops.service+ops.run",
    fetch: fetchSchedulerLedgerRows }),
  [CARD_13]: Object.freeze({ store_ref: "github:checks",
    fetch: fetchCheckConclusionRows }),
});

/**
 * Every finding a derivation can reach. Closed, so a finding is checkable, and
 * worded in this module's own vocabulary so that no value it emits carries a
 * privileged substring.
 */
const FINDINGS = Object.freeze([
  "gate_conclusion_check_absent",
  "gate_conclusion_observed",
  "gate_conclusion_unrecognized",
  "predecessor_outcome_absent",
  "predecessor_outcome_acceptance_receipt_hash_mismatch",
  "predecessor_outcome_accepted_with_matching_hash",
  "predecessor_outcome_detail_absent",
  "predecessor_outcome_not_accepted",
  "scheduler_canary_and_observation_join",
  "scheduler_canary_not_bound_to_receipt",
  "scheduler_dispatch_row_absent",
  "scheduler_observation_absent",
  "scheduler_observation_canary_mismatch",
  "scheduler_observation_not_after_dispatch",
  "scheduler_service_row_absent",
].sort());

/** The three findings that are a reading rather than a refusal. */
const REPORTING_FINDINGS = Object.freeze([
  "gate_conclusion_observed",
  "predecessor_outcome_accepted_with_matching_hash",
  "scheduler_canary_and_observation_join",
]);

/** Every refusal id a reader can answer with that is not a finding. Closed. */
const REASON_IDS = Object.freeze([
  ...FINDINGS,
  "gate_conclusion_query_invalid",
  "gate_conclusion_source_unreachable",
  "predecessor_outcome_store_unreachable",
  "predecessor_query_invalid",
  "scheduler_ledger_unreachable",
  "scheduler_predecessor_not_outcome_backed",
  "scheduler_query_invalid",
  "store_ref_not_the_ruled_one",
  "unknown_predecessor_step",
].sort());

/**
 * How a clause came out. THREE VALUES AND NO BOOLEAN, on purpose: a `true` on
 * this surface is the shape the standing rule closes over, whatever it is
 * called, and "unknown" is a state a boolean cannot express — a clause with no
 * row to read it from has not failed, it has not been answered.
 */
const HELD = "held";
const FAILED = "failed";
const UNKNOWN = "unknown";

/**
 * GitHub's own conclusion vocabulary, closed HERE rather than trusted from the
 * wire. The conclusion is the one store value that reaches an answer, and it
 * reaches it only by being equal to one of these strings — so what a consumer
 * receives is a constant from this file, not text the checks API chose. An
 * answer GitHub gives that is not on this list is reported as unrecognized
 * rather than passed through.
 */
const GITHUB_CONCLUSIONS = Object.freeze([
  "action_required", "cancelled", "failure", "neutral",
  "skipped", "stale", "success", "timed_out",
]);

/**
 * Which predecessor step is carried by which Work Request. Module-private and
 * frozen: the caller names a STEP, and the Work Request it resolves to is this
 * module's, so no caller can point the predecessor reader at a Work Request of
 * its own choosing. The step ref itself never reaches an answer — the Work
 * Request ref does, and it comes from here. `step:scheduler-active-receipt`
 * maps to null on purpose: no Work Request carries it, and the scheduler reader
 * answers it instead.
 */
const PREDECESSOR_WORK_REQUEST_REFS = Object.freeze({
  [V5_A02_SCHEDULER_STEP_REF]: null,
  "step:wr40-repository-outcome": "WR-000040",
  "step:wr46-dissolution-outcome": "WR-000046",
  "step:wr54-backup-recovery-outcome": "WR-000054",
});

// `headSha`, not `commitSha`: the field name reaches a result as
// `invalid_field`, and "commitSha" contains a word the privileged-word sweep
// closes over. GitHub's own API calls the field head_sha anyway.
const HEAD_SHA = /^[0-9a-f]{40}$/;
const CHECK_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._/()-]{0,99}$/;
const SERVICE_KEY = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const RUN_KEY = /^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$/;
const OUTCOME_HASH = /^sha256:[0-9a-f]{64}$/;

/**
 * WHAT THE SCHEDULER WRAPPER ACTUALLY WRITES, read off the two places that
 * define it rather than imagined:
 *
 *   db/schema.sql, `run_source_kind_check` — ops.run.source_kind is one of
 *     collector, registry, wrapper, operator. There is no "scheduler". A reader
 *     that required one could never match a real row, and an earlier draft of
 *     this file required exactly that.
 *   bin/run-scheduled.sh — the scheduler wrapper writes
 *     `--source-kind wrapper --source-ref bin/run-scheduled.sh`.
 *
 * So "the scheduler wrote it, not a hand-run" is: source_kind is `wrapper` AND
 * source_ref is that script. An `operator` row is a hand-run and a `collector`
 * row is a probe; neither is a dispatch this clause will speak for.
 *
 * AND THE HONEST CONSEQUENCE, stated here because it is the reader's most
 * likely real answer on the day a ruling lands: bin/run-scheduled.sh passes no
 * `--evidence-ref`, so the ops.run rows it writes today carry evidence_ref
 * null. Pointed at a live scheduled job right now, this reader answers
 * `scheduler_canary_not_bound_to_receipt` — truthfully. Binding a canary to its
 * receipt is a change to the wrapper, and it is owed; it is not something a
 * reader can supply, and this one does not pretend otherwise.
 */
const SCHEDULER_SOURCE_KIND = "wrapper";
const SCHEDULER_SOURCE_REF = "bin/run-scheduled.sh";

/**
 * THE SAME TWO FACTS, IN THE FORM THE STORE HANDS THEM OVER. A ledger row's
 * source kind and source ref are free-form control-plane text, so the store
 * digests them rather than carrying a word it did not write; the clause is still
 * "written by the wrapper", asked as an equality against the digest of this
 * module's own constant. The constants above stay spelled out because they are
 * what a human checks against bin/run-scheduled.sh, and because a digest nobody
 * can read is not a specification.
 */
const SCHEDULER_SOURCE_KIND_DIGEST = digest(SCHEDULER_SOURCE_KIND);
const SCHEDULER_SOURCE_REF_DIGEST = digest(SCHEDULER_SOURCE_REF);

/**
 * A check run that GitHub says has finished. Same reasoning: the store digests
 * the wire's status word — "completed" carries a privileged substring and is
 * text GitHub chose besides — and the comparison happens here, against this
 * module's own copy of the word.
 */
const COMPLETED_STATUS = "completed";
const COMPLETED_STATUS_DIGEST = digest(COMPLETED_STATUS);

/**
 * The digest of each documented conclusion, in the same order. A conclusion
 * reaches a consumer by its digest MATCHING one of these and the consumer being
 * handed GITHUB_CONCLUSIONS[index] — this module's own constant — so a word
 * GitHub has not documented cannot appear in an answer even as a substring.
 */
const GITHUB_CONCLUSION_DIGESTS = Object.freeze(GITHUB_CONCLUSIONS.map(one => digest(one)));

// ---------------------------------------------------------------------------
// Small, total helpers.
// ---------------------------------------------------------------------------

/** Instants, never strings. An unparseable timestamp is a missing timestamp. */
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

/**
 * One field out of a caller's object, safely. A Proxy whose `get` trap throws,
 * a throwing getter, a null prototype, a revoked Proxy — all of them come back
 * as null rather than as an exception that would carry the caller's own message
 * up the stack. The value is returned only to be MATCHED against; it is never
 * placed in a result.
 */
function field(query, key) {
  try {
    if (query === null || query === undefined) return null;
    const value = Reflect.get(Object(query), key);
    return typeof value === "string" ? value : null;
  } catch {
    return null;
  }
}

/** A validated field, or null. Validation is total: no partial credit. */
function matched(query, key, pattern) {
  const value = field(query, key);
  return value !== null && pattern.test(value) ? value : null;
}

function reason(id) {
  if (!REASON_IDS.includes(id)) throw new TypeError(`${id} is not a registered seam reader reason`);
  return id;
}

function finding(id) {
  if (!FINDINGS.includes(id)) throw new TypeError(`${id} is not a registered seam finding`);
  return id;
}

/**
 * The answer shape. `decision` is "refuse" or "report" — never "allow", and
 * there is no third value. A report is a statement about rows in a ruled store;
 * it is not a permission and no consumer may treat it as one.
 *
 * The card is addressed by its OPAQUE TOKEN. No gate seam name, no step ref and
 * no caller string is in this object anywhere.
 */
function answer(cardRef, ruling, queryDigest, decisionValue, reasonId, body) {
  return Object.freeze({
    schema_version: GATE_ZERO_SEAM_READERS_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    card_ref: cardRef,
    store_ref: ruling.store_ref,
    ruling_decision_ref: ruling.decision_ref,
    query_digest: queryDigest,
    status: decisionValue === "refuse" ? "unavailable" : "evidence_returned",
    decision: decisionValue,
    reason_id: reason(reasonId),
    caller_evidence_admitted: false,
    model_judgment_admitted: false,
    decided_by: "ruled_store_rows",
    effects: V5_NO_EFFECTS,
    ...body,
  });
}

/** A refusal that carries no finding, because no rows were derived from. */
function refuse(cardRef, ruling, queryDigest, reasonId, body) {
  return answer(cardRef, ruling, queryDigest, "refuse", reasonId, { finding: null, ...body });
}

/** A derived finding, with the provenance the derivation itself does not have. */
function report(cardRef, ruling, queryDigest, derived, extra) {
  const decisionValue = REPORTING_FINDINGS.includes(derived.finding) ? "report" : "refuse";
  return answer(cardRef, ruling, queryDigest, decisionValue, derived.finding,
    { finding: derived.finding, ...derived.facts, ...extra });
}

/**
 * The digest a result carries in place of the caller's own strings. Built from
 * the NORMALIZED query — the values that already passed their patterns — so a
 * hostile object contributes nothing but the absence of a field.
 */
function queryDigestOf(normalized) {
  return digest(canonicalJson(normalized));
}

/**
 * THE RULING GATE. Both halves must agree: the table must carry a well-formed
 * decision id for this card, AND the store that ruling names must be the one
 * this card's reader actually serves. Anything else is null, and a null here is
 * answered by the gate's own refusal.
 */
function ruledCard(cardRef) {
  const ruling = seamRulingRef(cardRef);
  if (ruling === null) return null;
  if (ruling.store_ref !== CARD_STORE[cardRef].store_ref) return null;
  return ruling;
}

/**
 * Fetch, or hand back the finished refusal. ONE function, not one per reader,
 * so both ways a fetch can fail are written once and every reader gets the same
 * answer shape for them.
 *
 *   * The fetcher is the ONE this card serves, and `ruledCard` has already
 *     refused unless the ruling names exactly that store — so the ruling, not
 *     the reader, is what decided a query would happen at all.
 *   * `store_ref` coming back is the store's own statement of which store
 *     answered, and a disagreement with the ruling refuses instead of being
 *     reported over. The earlier reader ignored it and reported the ruled ref
 *     whatever it had actually queried.
 *   * A store that could not be reached refuses with its own closed phrase.
 */
async function fetchOrRefuse(cardRef, ruling, queryDigest, query, unreachableReason, because, body) {
  let fetched;
  try {
    fetched = await CARD_STORE[cardRef].fetch(query);
  } catch (cause) {
    return { refusal: refuse(cardRef, ruling, queryDigest, unreachableReason,
      { ...body, unavailable_because: isSeamStoreUnreachable(cause) ? cause.because : because }) };
  }
  if (fetched?.store_ref !== ruling.store_ref)
    return { refusal: refuse(cardRef, ruling, queryDigest, "store_ref_not_the_ruled_one", body) };
  return { rows: Array.isArray(fetched.rows) ? fetched.rows : [] };
}

// ---------------------------------------------------------------------------
// THE DERIVATIONS. Module-private, every one of them: they turn rows into a
// finding, and a function that does that is exactly what the standing rule of
// 2026-09-11 forbids on a public surface, under any name and with any tag. They
// are reachable only from the three readers below, each of which has already
// checked a ruling and fetched the rows itself. There is no test-only export,
// no injectable variant and no "unwired" twin; the tests reach these the same
// way any consumer would, through a reader, over fixture rows.
// ---------------------------------------------------------------------------

function derived(id, facts) {
  return { finding: finding(id), facts };
}

// Card 11 — an accepted predecessor outcome, and the hash it was asked about.
//
// THE THREE CONDITIONS, AND ALL THREE MUST HOLD.
//
//   1. A row's `status` is exactly "accepted". "pending_human_acceptance" is not
//      a near miss; a proposal nobody signed is not an outcome.
//   2. The row is COMPLETE: an acceptance receipt whose matching card detail is
//      absent is a missing row, and a missing row refuses. The store COUNTS the
//      card rows it found for the receipt rather than synthesizing a
//      null-outcome row that looks accepted — and it is a count rather than the
//      boolean it shipped as, because a bare `true` out of an exported function
//      is the shape the standing rule closes over.
//   3. That row's ACCEPTANCE RECEIPT hash equals the outcome hash this reader
//      was asked about. Not the proposal's `feedback_hash` — the receipt's,
//      because the receipt is the row a human's acceptance wrote and the
//      proposal is the row a machine wrote. A forged or stale hash therefore
//      does not merely fail to match a proposal; it fails to match a signature.
//
// None of the three can be satisfied by the `outcomeHash` argument: it is
// compared against a stored value and never returned, so a caller who supplies
// a hash learns only whether the store agrees with it.
function derivePredecessorOutcome(rows, outcomeHash) {
  const all = Array.isArray(rows) ? rows : [];
  const counts = { outcome_rows_seen: all.length, accepted_rows_seen: 0, hash_match: UNKNOWN };
  if (all.length === 0) return derived("predecessor_outcome_absent", { ...counts, hash_match: FAILED });
  const accepted = all.filter(row => text(row?.status) === "accepted");
  counts.accepted_rows_seen = accepted.length;
  if (accepted.length === 0)
    return derived("predecessor_outcome_not_accepted", { ...counts, hash_match: FAILED });
  const incomplete = accepted.filter(row => row?.detail_row_count !== 1);
  if (incomplete.length > 0)
    return derived("predecessor_outcome_detail_absent", { ...counts, hash_match: FAILED });
  const asked = typeof outcomeHash === "string" && OUTCOME_HASH.test(outcomeHash) ? outcomeHash : null;
  const matching = accepted.filter(row => {
    const receiptHash = text(row?.accepted_feedback_hash);
    return receiptHash !== null && asked !== null && receiptHash === asked;
  });
  if (matching.length === 0)
    return derived("predecessor_outcome_acceptance_receipt_hash_mismatch", { ...counts, hash_match: FAILED });
  return derived("predecessor_outcome_accepted_with_matching_hash", { ...counts, hash_match: HELD });
}

// Card 12 — the canary, its receipt binding, and an observation strictly after
// dispatch.
//
// THE THREE CLAUSES GATE ZERO NAMES, each answered from a real ops.run row and
// its timestamps, and a missing row refusing rather than defaulting.
//
//   receipt_binding             the dispatch row names its receipt: its evidence
//                               ref is there at all, and its source kind and
//                               source ref are the ones bin/run-scheduled.sh
//                               writes — `wrapper` and the script's own path —
//                               not a hand-run's `operator` row and not a probe's
//                               `collector` row. Every one of those five fields
//                               arrives as a digest, and every question asked of
//                               them is an equality, so the clause is unchanged
//                               and no ledger text is in the answer.
//   observation_after_dispatch  the observation's `observed_at` is STRICTLY after
//                               the dispatch row's `started_at`. Equal instants
//                               fail: rows written in one transaction share now().
//   canary_match                the observation is an observation OF THIS canary —
//                               same run key, same evidence ref.
//
// The dispatch row is the one with the latest `started_at`; the observation is
// the one with the latest `observed_at` among rows that also ENDED, because an
// observation of a run still in flight is not an observation of its result. In
// production these are usually THE SAME ROW: the wrapper writes one row per run
// carrying started_at, ended_at and an observed_at stamped when the row lands.
// All three clauses are reported every time rows allow them to be computed, so a
// caller sees which clause failed rather than only that one did.
function deriveSchedulerCanary(rows) {
  const all = Array.isArray(rows) ? rows : [];
  const serviceRows = all.filter(row => text(row?.service_key_digest) !== null);
  const blank = {
    service_rows_seen: serviceRows.length, run_rows_seen: 0,
    receipt_binding: UNKNOWN, observation_after_dispatch: UNKNOWN, canary_match: UNKNOWN,
  };
  if (serviceRows.length === 0) return derived("scheduler_service_row_absent", blank);
  const runRows = serviceRows.filter(row => text(row?.run_key_digest) !== null);
  blank.run_rows_seen = runRows.length;
  const dispatched = runRows.filter(row => instant(row?.started_at) !== null);
  if (dispatched.length === 0) return derived("scheduler_dispatch_row_absent", { ...blank });
  const dispatch = dispatched.reduce((a, b) => (instant(b.started_at) > instant(a.started_at) ? b : a));
  const observed = runRows.filter(row => instant(row?.observed_at) !== null && instant(row?.ended_at) !== null);
  if (observed.length === 0) return derived("scheduler_observation_absent", { ...blank });
  const observation = observed.reduce((a, b) => (instant(b.observed_at) > instant(a.observed_at) ? b : a));

  const boundToReceipt = text(dispatch.evidence_ref_digest) !== null
    && text(dispatch.source_kind_digest) === SCHEDULER_SOURCE_KIND_DIGEST
    && text(dispatch.source_ref_digest) === SCHEDULER_SOURCE_REF_DIGEST;
  const afterDispatch = instant(observation.observed_at) > instant(dispatch.started_at);
  const canaryMatch = text(observation.run_key_digest) !== null
    && text(observation.run_key_digest) === text(dispatch.run_key_digest)
    && text(observation.evidence_ref_digest) !== null
    && text(observation.evidence_ref_digest) === text(dispatch.evidence_ref_digest);

  const facts = {
    service_rows_seen: serviceRows.length,
    run_rows_seen: runRows.length,
    receipt_binding: boundToReceipt ? HELD : FAILED,
    observation_after_dispatch: afterDispatch ? HELD : FAILED,
    canary_match: canaryMatch ? HELD : FAILED,
  };
  if (!boundToReceipt) return derived("scheduler_canary_not_bound_to_receipt", facts);
  if (!canaryMatch) return derived("scheduler_observation_canary_mismatch", facts);
  if (!afterDispatch) return derived("scheduler_observation_not_after_dispatch", facts);
  return derived("scheduler_canary_and_observation_join", facts);
}

// Card 13 — what hosted CI concluded about one commit, under one check name.
//
// A check run that has not COMPLETED has no conclusion, so it is not a check run
// this derivation will speak for: `status` must be "completed" and `conclusion`
// must be one of GitHub's own documented words. Several completed runs under one
// name on one commit (a re-run) resolve to the LATEST by `completed_at`, which is
// what the merge gate itself acts on, and the count is reported so a reader can
// see there was more than one.
//
// The conclusion is returned as GitHub's own word and is NOT mapped to green,
// passing or ok — but it is returned by EQUALITY against the closed list above,
// so the string a consumer receives is a constant out of this file rather than
// text off the wire. Whatever consumes a gate conclusion decides what a
// conclusion means; a reader that translated one would be that consumer wearing
// a reader's name.
//
// THE EQUALITY IS ON DIGESTS, because the store no longer carries the wire's own
// words at all: it hands over the digest of the status and the digest of the
// conclusion, and the word a consumer receives is GITHUB_CONCLUSIONS[index] —
// this module's constant, reached by its digest having matched.
function deriveGateConclusion(rows, headSha) {
  const all = Array.isArray(rows) ? rows : [];
  const completed = all.filter(row =>
    text(row?.status_digest) === COMPLETED_STATUS_DIGEST
    && text(row?.conclusion_digest) !== null
    && text(row?.head_sha) === headSha);
  const counts = { check_runs_seen: all.length, completed_runs_seen: completed.length };
  if (completed.length === 0)
    return derived("gate_conclusion_check_absent", { ...counts, conclusion: null });
  const latest = completed.reduce((a, b) =>
    ((instant(b.ended_at) ?? 0) > (instant(a.ended_at) ?? 0) ? b : a));
  const index = GITHUB_CONCLUSION_DIGESTS.indexOf(text(latest.conclusion_digest));
  if (index < 0) return derived("gate_conclusion_unrecognized", { ...counts, conclusion: null });
  return derived("gate_conclusion_observed", { ...counts, conclusion: GITHUB_CONCLUSIONS[index] });
}

// ---------------------------------------------------------------------------
// Card 11 — the predecessor outcome reader.
// ---------------------------------------------------------------------------

/**
 * Whether the ruled store holds an ACCEPTED outcome for one predecessor step
 * whose acceptance-receipt hash is the one this reader was asked about.
 *
 * Both halves are required and neither can come from the caller: the acceptance
 * is a receipt row a human's act wrote, and the hash the caller supplies is
 * compared against that row's hash and never echoed. A forged hash therefore
 * fails to match a signature, not merely a proposal — and it fails closed.
 */
async function predecessorOutcomeEvidence(query) {
  const ruling = ruledCard(CARD_11);
  if (ruling === null) return readGateZeroPredecessorJoin();

  const stepRef = field(query, "stepRef");
  const known = typeof stepRef === "string"
    && V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.includes(stepRef) ? stepRef : null;
  const outcomeHash = matched(query, "outcomeHash", OUTCOME_HASH);
  const queryDigest = queryDigestOf({ step_ref: known, outcome_hash: outcomeHash });

  if (known === null)
    return refuse(CARD_11, ruling, queryDigest, "unknown_predecessor_step", {});
  // The Work Request ref comes from this module's frozen table, never from the
  // caller's string — so what an answer carries is this module's identifier.
  const workRequestRef = PREDECESSOR_WORK_REQUEST_REFS[known];
  if (workRequestRef === null)
    return refuse(CARD_11, ruling, queryDigest, "scheduler_predecessor_not_outcome_backed", {});
  if (outcomeHash === null)
    return refuse(CARD_11, ruling, queryDigest, "predecessor_query_invalid",
      { invalid_field: "outcomeHash" });

  const got = await fetchOrRefuse(CARD_11, ruling, queryDigest, { workRequestRef },
    "predecessor_outcome_store_unreachable", "the store did not answer",
    { work_request_ref: workRequestRef });
  if (got.refusal !== undefined) return got.refusal;
  return report(CARD_11, ruling, queryDigest, derivePredecessorOutcome(got.rows, outcomeHash),
    { work_request_ref: workRequestRef });
}

// ---------------------------------------------------------------------------
// Card 12 — the scheduler canary reader.
// ---------------------------------------------------------------------------

/**
 * The three clauses `step:scheduler-active-receipt` needs — receipt_binding,
 * observation_after_dispatch, canary_match — taken strictly from Control Plane
 * ledger rows and their timestamps, with a missing row refusing rather than
 * defaulting.
 *
 * The caller says WHICH service and WHICH run key. It cannot say what the rows
 * contain, and it cannot make a row exist: no row, no answer.
 */
async function schedulerCanaryEvidence(query) {
  const ruling = ruledCard(CARD_12);
  if (ruling === null) return readGateZeroPredecessorJoin();

  const serviceKey = matched(query, "serviceKey", SERVICE_KEY);
  const canaryRunKey = matched(query, "canaryRunKey", RUN_KEY);
  const queryDigest = queryDigestOf({ service_key: serviceKey, canary_run_key: canaryRunKey });

  if (serviceKey === null || canaryRunKey === null)
    return refuse(CARD_12, ruling, queryDigest, "scheduler_query_invalid",
      { invalid_field: serviceKey === null ? "serviceKey" : "canaryRunKey" });

  const got = await fetchOrRefuse(CARD_12, ruling, queryDigest, { serviceKey, canaryRunKey },
    "scheduler_ledger_unreachable", "the ledger did not answer", {});
  if (got.refusal !== undefined) return got.refusal;
  return report(CARD_12, ruling, queryDigest, deriveSchedulerCanary(got.rows), {});
}

// ---------------------------------------------------------------------------
// Card 13 — the gate conclusion reader.
// ---------------------------------------------------------------------------

/**
 * What hosted CI concluded for one commit (`headSha`) under one check name, in
 * GitHub's own word, or unavailable.
 *
 * The conclusion is not translated. "success" is returned as "success" and is
 * not turned into green, passing or ok — deciding what a conclusion MEANS is
 * the consuming gate's job, and a reader that did it would be that gate.
 */
async function gateConclusionEvidence(query) {
  const ruling = ruledCard(CARD_13);
  if (ruling === null) return readGateGraphAssurance();

  const headSha = matched(query, "headSha", HEAD_SHA);
  const checkName = matched(query, "checkName", CHECK_NAME);
  const queryDigest = queryDigestOf({ head_sha: headSha, check_name: checkName });

  if (headSha === null || checkName === null)
    return refuse(CARD_13, ruling, queryDigest, "gate_conclusion_query_invalid",
      { invalid_field: headSha === null ? "headSha" : "checkName" });

  const got = await fetchOrRefuse(CARD_13, ruling, queryDigest, { headSha, checkName },
    "gate_conclusion_source_unreachable", "the checks source did not answer", {});
  if (got.refusal !== undefined) return got.refusal;
  return report(CARD_13, ruling, queryDigest, deriveGateConclusion(got.rows, headSha), {});
}

// ---------------------------------------------------------------------------
// THE PUBLIC SURFACE, AND THE ONE GUARDED BOUNDARY IT PASSES THROUGH.
//
// A reader's contract is that it ANSWERS. It never throws, and a caller reading
// a refusal never has to catch one — which is only true if there is a boundary
// that makes it true, rather than three implementations each remembering not to
// let anything escape. `reason()` and `finding()` raise on an unregistered id;
// the derivations index into rows; `fetchOrRefuse` reads a property off whatever
// a store handed back. Any of those can throw, and a native throw carries an
// ENGINE-BUILT STACK — a list of the caller's own frame names and file paths,
// bytes this module did not write. A caller function named `green` calling a
// reader that threw got `at green` handed back to it.
//
// So every export is the same wrapper over a module-private implementation, and
// what it answers when anything at all is thrown is the GATE'S OWN ANSWER for
// that card — the identical object the reader returns while the card is unruled.
// That is fail-closed by construction: the worst a thrown value can do is make a
// ruled reader as unavailable as an unruled one. Nothing is re-thrown, so no
// stack, no message and no caller byte leaves this surface by the throwing door
// at all.
// ---------------------------------------------------------------------------

/**
 * EVERY EXPORTED CALLABLE HERE ANSWERS `instanceof` WITH FALSE, AND ANSWERS IT
 * WITHOUT LOOKING AT THE OPERAND. Amendment 2 of 2026-09-12, clause (b), and the
 * reason is the fifth review round's second finding: without an own
 * `Symbol.hasInstance` the intrinsic one walks the LEFT OPERAND'S prototype
 * chain, so `hostile instanceof readGateConclusionEvidence` ran the caller's own
 * getPrototypeOf trap and let the caller's own thrown text back out of an
 * exported callable. A reader has no membership question to answer anyway — it
 * is a function, and nothing is an instance of it.
 *
 * The guard is an ARROW that ignores its argument, installed as a NON-WRITABLE,
 * NON-CONFIGURABLE DATA property: it cannot be constructed, carries no
 * `prototype`, cannot be replaced, and cannot be redefined as an accessor.
 */
function closedCallable(callable) {
  // AND IT IS BOUND, NOT BARE. Clause (a) of the amendment names an arrow OR a
  // bound function, and the difference is only visible in what the ENGINE says
  // when somebody constructs one: its refusal for a bare arrow quotes the
  // function's own SOURCE TEXT back, and this file would rather the engine's
  // sentence carry no line of this module at all. A bound arrow is still not a
  // constructor and still has no `prototype`; the refusal names
  // `function () { [native code] }` and nothing else. Binding is lexical-`this`
  // neutral for an arrow, so no behaviour moves.
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return closed;
}

// AND THE BOUNDARY HAS TO COVER `new`, WHICH IT DOES BY LEAVING NOTHING TO
// CONSTRUCT. The fourth review round found that an async function has no
// [[Construct]] at all, so `Reflect.construct` on one is refused by the ENGINE
// before the function starts; the third correction answered that with a Proxy
// whose construct trap returned the gate's own answer, and the fifth round found
// what that proxy still forwarded — `get`, and with it the raw target under
// `prototype.constructor`, constructable with a `new.target` of the caller's
// choosing.
//
// So the wrapper is gone and every export is an ARROW FUNCTION. An arrow is not
// a constructor and has no `prototype`, so there is no target to reach and no
// `newTarget.prototype` read on the way in: construction is refused by the
// engine, in the caller's own frame, having run no line of this module. That is
// clause (a) of amendment 2, and the refusal is out of scope as a finding for
// exactly the reason it is safe — nothing here executed, so nothing here can
// have read the caller's object or written the sentence that comes back.
//
// The arrow returns the async work as a promise, so every caller sees what it
// saw before, and the CALLING door still answers with the gate's own object.
function guarded(gateAnswer, read) {
  const guardedRead = query => (async () => {
    try {
      return await read(query);
    } catch {
      return gateAnswer();
    }
  })();
  return closedCallable(guardedRead);
}

export const readPredecessorOutcomeEvidence =
  guarded(readGateZeroPredecessorJoin, predecessorOutcomeEvidence);
export const readSchedulerCanaryEvidence =
  guarded(readGateZeroPredecessorJoin, schedulerCanaryEvidence);
export const readGateConclusionEvidence =
  guarded(readGateGraphAssurance, gateConclusionEvidence);
