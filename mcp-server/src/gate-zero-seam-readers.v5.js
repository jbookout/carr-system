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
//   1. Look up this seam in the module-private ruling table.
//   2. If its `decision_id` is not the uuid shape `log-decision` returns —
//      which today it is not, because it is null — RETURN THE GATE'S OWN
//      REFUSAL, the identical frozen object `readGateZeroPredecessorJoin()` or
//      `readGateGraphAssurance()` returns to every other caller. Not an equal
//      copy: the same object, so the two cannot drift and no test can pass on a
//      lookalike.
//   3. Only then look at the query. Only then open the store.
//
// The order is the point. A reader that validated the query first would leak
// which queries are well-formed; a reader that opened its store first would
// have read an unruled store even if it threw the answer away. Half an answer
// from an unruled store is still an unruled read.
//
// WHAT A CALLER MAY SAY, AND WHAT IT MAY NEVER SAY. A query ADDRESSES a row:
// which predecessor step, which service and canary, which commit and check.
// Addressing is not asserting. There is no argument for a decision id, a store
// handle, a connection, a reader object, a row, a receipt or an outcome — and
// there is no environment variable that opens a seam either. The only way to
// change what these functions answer is to change what is in the store, or to
// paste a ruling into a file and commit it.
//
// NO CALLER TEXT COMES BACK OUT. Every query field is taken through a
// try/catch (a Proxy trap or a throwing getter is caught, not propagated) and
// then matched against a strict pattern. A field that fails is refused; a field
// that passes is USED and not echoed. What a result carries instead is
// `query_digest` — a digest over the normalized query — plus, for the
// predecessor reader, the step ref taken from THIS MODULE'S frozen table rather
// than from the string the caller handed in. Hand these functions a hostile
// object and the answer contains none of its bytes.
//
// NO PRIVILEGED WORD COMES OUT EITHER. The union the standing rule closes —
// allow, commit, prompt, suppress, release, read, covered, drafted, proposed,
// queued, healthy, passing, ok, pass, satisfied, complete, admitted, resumed,
// attended, verified, present, equivalent, operational, active, green,
// joins_exactly, coverage_complete, favorable, anything `would_*`, anything
// `*_if_authoritative` — appears in no value any export of this module returns,
// and the test sweeps for it over every export and every input shape. The ONE
// structural exception is not an exception to the rule but a consequence of
// obeying it: the invariant refusal must name `seam:gate-zero-read-only-outcome-producer`
// and `step:gate-zero-read-only-outcome`, which contain the letters "read", and
// it must name them because the gate names them. The test carves out exactly
// those refs, by identity against the gate's own exported constants, and
// nothing else.
//
// THE FOURTH SEAM IS NOT BUILT. `seam:gate-zero-read-only-outcome-producer` is
// cards 9 and 10 — which independent seat holds the oracle, and whether r7
// itself carries the registration. Those are not questions about where to read
// from; they are questions about who signs. Its refusal is carried below
// VERBATIM, as a written-out constant rather than a call, and the test asserts
// the written words are byte-identical to what `emitGateZeroOutcome` actually
// returns — so if the gate's refusal ever changes, this copy goes red instead
// of going stale.
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
  GATE_ZERO_STEP_REF,
  V5_A02_GATE_CONCLUSION_READER_SEAM,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
  V5_A02_SCHEDULER_STEP_REF,
  readGateGraphAssurance,
  readGateZeroPredecessorJoin,
} from "./gate-zero-assurance.v5.js";
import {
  GATE_ZERO_SEAM_RULINGS,
  GATE_ZERO_SEAM_STORE_REFS,
  seamRulingDecisionRef,
} from "./gate-zero-seam-rulings.v5.js";
import {
  GATE_ZERO_SEAM_FINDINGS,
  wouldAdmitPredecessorOutcome,
  wouldReportGateConclusion,
  wouldReportSchedulerCanary,
} from "./gate-zero-seam-evidence.v5.js";
import {
  SeamStoreUnreachable,
  fetchCheckConclusionRows,
  fetchPredecessorOutcomeRows,
  fetchSchedulerLedgerRows,
} from "./gate-zero-seam-stores.v5.js";

/**
 * The schema id, and it deliberately says "evidence" rather than "readers": no
 * value this module returns may contain a privileged word even as a raw
 * substring, and "readers" carries one. The sweep is the reason, and keeping the
 * reason visible here is cheaper than rediscovering it.
 */
export const GATE_ZERO_SEAM_READERS_SCHEMA_VERSION = "doctorcre-v5-a02-gate-zero-seam-evidence.v1";

/** The three seams built here. The fourth is below, and it is not built. */
export const GATE_ZERO_SEAM_READER_SEAMS = Object.freeze([
  V5_A02_GATE_CONCLUSION_READER_SEAM,
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
].sort());

/**
 * THE PRODUCER SEAM'S REFUSAL, WRITTEN OUT AND LEFT WHERE IT IS.
 *
 * Card 9 asks which independent seat holds the oracle; card 10 asks whether r7
 * carries the registration. Neither is a question about a store, so neither is
 * answered by a reader, and building one would be building a seat. These are
 * the gate's own words, copied — and the test proves the copy is exact against
 * a live `emitGateZeroOutcome()` call, so it cannot rot.
 */
export const GATE_ZERO_PRODUCER_SEAM_NOT_BUILT = Object.freeze({
  seam: "seam:gate-zero-read-only-outcome-producer",
  cards: Object.freeze([9, 10]),
  built: false,
  reason_id: "gate_zero_producer_seam_unavailable",
  unavailable_because:
    "the producer role is ruled provisionally but r7 carries no entry, no seat holds the oracle, and no reader exists for the evidence one would stand on",
  not_passable_because:
    "the ruled producer role is unstaffed and unregistered in r7, and no authoritative predecessor, scheduler or gate reader exists",
  not_built_because:
    "cards 9 and 10 ask which seat signs and whether r7 carries the registration; neither is a question about where evidence comes from, and a seat is not something this repository can staff",
});

/** Every refusal or report id these readers can answer with. Closed. */
export const GATE_ZERO_SEAM_READER_REASON_IDS = Object.freeze([
  ...GATE_ZERO_SEAM_FINDINGS,
  "gate_conclusion_query_invalid",
  "gate_conclusion_source_unreachable",
  "predecessor_outcome_store_unreachable",
  "predecessor_query_invalid",
  "scheduler_ledger_unreachable",
  "scheduler_predecessor_not_outcome_backed",
  "scheduler_query_invalid",
  "unknown_predecessor_step",
  "unknown_seam_store_ref",
].sort());

/**
 * Which predecessor step is carried by which Work Request. Module-private and
 * frozen: the caller names a STEP, and the Work Request it resolves to is this
 * module's, so no caller can point the predecessor reader at a Work Request of
 * its own choosing. `step:scheduler-active-receipt` maps to null on purpose —
 * no Work Request carries it, and the scheduler reader answers it instead.
 */
const PREDECESSOR_WORK_REQUEST_REFS = Object.freeze({
  "step:scheduler-active-receipt": null,
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
 * One field out of a caller's object, safely. A Proxy whose `get` trap throws,
 * a throwing getter, a null prototype, a revoked Proxy — all of them come back
 * as null rather than as an exception that would carry the caller's own message
 * up the stack. The value is returned only for the caller to be MATCHED
 * against; it is never placed in a result.
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
  if (!GATE_ZERO_SEAM_READER_REASON_IDS.includes(id))
    throw new TypeError(`${id} is not a registered seam reader reason`);
  return id;
}

/**
 * The answer shape. `decision` is "refuse" or "report" — never "allow", and
 * there is no third value. A report is a statement about rows in a ruled store;
 * it is not a permission and no consumer may treat it as one.
 */
function answer(seam, storeRef, decisionRef, queryDigest, decisionValue, reasonId, body) {
  return Object.freeze({
    schema_version: GATE_ZERO_SEAM_READERS_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    seam,
    store_ref: storeRef,
    ruling_decision_ref: decisionRef,
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

/**
 * The digest a result carries in place of the caller's own strings. Built from
 * the NORMALIZED query — the values that already passed their patterns — so a
 * hostile object contributes nothing but the absence of a field.
 */
function queryDigestOf(normalized) {
  return digest(canonicalJson(normalized));
}

/**
 * The ruling gate. Returns the decision ref when the seam is ruled and the
 * store it names is one this repository can reach; otherwise returns null, and
 * the caller returns the gate's own refusal.
 */
function ruledSeam(seam) {
  const entry = GATE_ZERO_SEAM_RULINGS[seam] ?? null;
  const decisionRef = seamRulingDecisionRef(entry);
  if (decisionRef === null) return null;
  return { decision_ref: decisionRef, store_ref: entry.store_ref };
}

/** What the conditional derivation said, restated as a finding with provenance. */
function report(seam, ruling, queryDigest, conditional, extra) {
  const { would_admit_if_rows_were_authoritative: admits, conditional_finding: finding,
    evidence_basis: _basis, ...facts } = conditional;
  return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest,
    admits ? "report" : "refuse", finding, { finding, ...facts, ...extra });
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
export async function readPredecessorOutcomeEvidence(query) {
  const seam = V5_A02_PREDECESSOR_OUTCOME_READER_SEAM;
  const ruling = ruledSeam(seam);
  if (ruling === null) return readGateZeroPredecessorJoin();

  const stepRef = field(query, "stepRef");
  const known = typeof stepRef === "string"
    && V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.includes(stepRef) ? stepRef : null;
  const outcomeHash = matched(query, "outcomeHash", OUTCOME_HASH);
  const normalized = { step_ref: known, outcome_hash: outcomeHash };
  const queryDigest = queryDigestOf(normalized);

  if (known === null)
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "unknown_predecessor_step",
      { required_predecessors: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS], finding: null });
  const workRequestRef = PREDECESSOR_WORK_REQUEST_REFS[known];
  if (workRequestRef === null)
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "scheduler_predecessor_not_outcome_backed",
      { step_ref: known, scheduler_step_ref: V5_A02_SCHEDULER_STEP_REF, finding: null });
  if (outcomeHash === null)
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "predecessor_query_invalid",
      { step_ref: known, invalid_field: "outcomeHash", finding: null });
  if (!GATE_ZERO_SEAM_STORE_REFS.includes(ruling.store_ref))
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "unknown_seam_store_ref", { step_ref: known, finding: null });

  let fetched;
  try {
    fetched = await fetchPredecessorOutcomeRows({ workRequestRef });
  } catch (cause) {
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "predecessor_outcome_store_unreachable",
      { step_ref: known, finding: null,
        unavailable_because: cause instanceof SeamStoreUnreachable ? cause.because : "the store did not answer" });
  }
  return report(seam, ruling, queryDigest,
    wouldAdmitPredecessorOutcome(fetched.rows, { outcomeHash }),
    { step_ref: known, work_request_ref: workRequestRef });
}

// ---------------------------------------------------------------------------
// Card 12 — the scheduler canary reader.
// ---------------------------------------------------------------------------

/**
 * The three clauses `step:scheduler-active-receipt` needs — bound_to_receipt,
 * readback_after_dispatch, canary_match — taken strictly from Control Plane
 * ledger rows and their timestamps, with a missing row refusing rather than
 * defaulting.
 *
 * The caller says WHICH service and WHICH run key. It cannot say what the rows
 * contain, and it cannot make a row exist: no row, no answer.
 */
export async function readSchedulerCanaryEvidence(query) {
  const seam = V5_A02_SCHEDULER_READER_SEAM;
  const ruling = ruledSeam(seam);
  if (ruling === null) return readGateZeroPredecessorJoin();

  const serviceKey = matched(query, "serviceKey", SERVICE_KEY);
  const canaryRunKey = matched(query, "canaryRunKey", RUN_KEY);
  const queryDigest = queryDigestOf({ service_key: serviceKey, canary_run_key: canaryRunKey });

  if (serviceKey === null || canaryRunKey === null)
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "scheduler_query_invalid",
      { invalid_field: serviceKey === null ? "serviceKey" : "canaryRunKey", finding: null });
  if (!GATE_ZERO_SEAM_STORE_REFS.includes(ruling.store_ref))
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "unknown_seam_store_ref", { finding: null });

  let fetched;
  try {
    fetched = await fetchSchedulerLedgerRows({ serviceKey, canaryRunKey });
  } catch (cause) {
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "scheduler_ledger_unreachable",
      { finding: null,
        unavailable_because: cause instanceof SeamStoreUnreachable ? cause.because : "the ledger did not answer" });
  }
  return report(seam, ruling, queryDigest, wouldReportSchedulerCanary(fetched.rows),
    { scheduler_step_ref: V5_A02_SCHEDULER_STEP_REF });
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
export async function readGateConclusionEvidence(query) {
  const seam = V5_A02_GATE_CONCLUSION_READER_SEAM;
  const ruling = ruledSeam(seam);
  if (ruling === null) return readGateGraphAssurance();

  const headSha = matched(query, "headSha", HEAD_SHA);
  const checkName = matched(query, "checkName", CHECK_NAME);
  const queryDigest = queryDigestOf({ head_sha: headSha, check_name: checkName });

  if (headSha === null || checkName === null)
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "gate_conclusion_query_invalid",
      { invalid_field: headSha === null ? "headSha" : "checkName", finding: null });
  if (!GATE_ZERO_SEAM_STORE_REFS.includes(ruling.store_ref))
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "unknown_seam_store_ref", { finding: null });

  let fetched;
  try {
    fetched = await fetchCheckConclusionRows({ commitSha: headSha, checkName });
  } catch (cause) {
    return answer(seam, ruling.store_ref, ruling.decision_ref, queryDigest, "refuse",
      "gate_conclusion_source_unreachable",
      { finding: null,
        unavailable_because: cause instanceof SeamStoreUnreachable ? cause.because : "the checks source did not answer" });
  }
  return report(seam, ruling, queryDigest, wouldReportGateConclusion(fetched.rows, { commitSha: headSha }), {});
}

// ---------------------------------------------------------------------------
// What is ruled, reported without reading anything.
// ---------------------------------------------------------------------------

/**
 * One line per seam: the card that asks it, the store its ruling would name,
 * and whether a ruling is on the record. Takes no argument, touches no store,
 * and is the honest answer to "is this wired yet".
 */
export function gateZeroSeamRulingStatus() {
  return Object.freeze(GATE_ZERO_SEAM_READER_SEAMS.map(seam => {
    const entry = GATE_ZERO_SEAM_RULINGS[seam];
    return Object.freeze({
      seam,
      card: entry.card,
      question: entry.question,
      store_ref: entry.store_ref,
      ruling_on_record: seamRulingDecisionRef(entry) !== null,
      gate_zero_step_ref: GATE_ZERO_STEP_REF,
    });
  }));
}
