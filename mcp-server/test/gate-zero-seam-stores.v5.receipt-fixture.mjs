// A SECOND store fixture, for the one clause production rows cannot separate.
//
// WHY IT EXISTS. In production
// ops.accept_sourced_work_request_outcome_feedback writes the acceptance
// receipt's hash EQUAL to the proposal's, so a reader that consulted the
// proposal's `feedback_hash` instead of the receipt's `accepted_feedback_hash`
// looks correct on every row the record layer will ever hold. Mutation testing
// found exactly that: swapping the two fields changed no assertion. The clause
// is the most load-bearing one in card 11 — it is what makes a forged hash fail
// to match a SIGNATURE rather than merely a proposal — and it was unproved.
//
// So these rows do what no production row does: they pull the two hashes apart,
// one work request per case, and the same three steps address them. Each case is
// a row the store could hold if something went wrong, which is the only
// condition under which the distinction matters.
//
// It lives in test/ for the same reason the other fixture does, and the same
// parser-backed import scan proves nothing in src/ can reach it.

// MODULE-PRIVATE, exactly as in the real store module, and for the same reason:
// the substituted reader reads `.because` straight into an answer, so the set it
// can come back with has to be a set nothing outside this file can enumerate or
// extend. Neither name is exported, so the export-parity check in the test still
// sees this file's surface as the real module's.
const REGISTERED_REASONS = Object.freeze([
  "the checks source answer did not parse",
  "the checks source credentials are not configured in this process",
  "the checks source refused the request",
  "the checks source was not reachable",
  "the database client is not available in this process",
  "the connection target for this store is not configured in this process",
  "the query did not finish",
  "the query did not address a row",
  "the configured checks repository is not the one this file serves",
  "the call did not finish",
  "this error type is final",
  "the reason this store was unreachable is not a registered one",
]);

const REGISTERED_STORE_TOKENS = Object.freeze([
  "record-layer:work-request-outcome-feedback",
  "control-plane:ops.service+ops.run",
  "github:checks",
  "a-store-this-file-does-not-serve",
]);

function own(target, key, value, enumerable) {
  Object.defineProperty(target, key, { value, writable: false, enumerable, configurable: false });
}

function causeKind(cause) {
  try {
    if (cause === undefined || cause === null) return "none";
    if (cause instanceof SeamStoreUnreachable) return "a-seam-store-that-was-unreachable";
    if (cause instanceof Error) return "an-error";
    return "not-an-error";
  } catch {
    return "undetermined";
  }
}

export class SeamStoreUnreachable extends Error {
  constructor(storeRef, because, cause) {
    if (new.target !== SeamStoreUnreachable)
      throw new TypeError("this error type is final and cannot be extended");
    const store = REGISTERED_STORE_TOKENS.includes(storeRef)
      ? storeRef : "a-store-this-file-does-not-serve";
    const reason = REGISTERED_REASONS.includes(because)
      ? because : "the reason this store was unreachable is not a registered one";
    const message = `${store}: ${reason}`;
    super(message);
    own(this, "name", "SeamStoreUnreachable", false);
    own(this, "message", message, false);
    own(this, "stack", `SeamStoreUnreachable: ${message}`, false);
    own(this, "store_ref", store, true);
    own(this, "because", reason, true);
    own(this, "cause_kind", causeKind(cause), true);
    Object.freeze(this);
  }
}

/** The hash every case below is asked about. */
export const FIXTURE_ASKED_HASH = `sha256:${"4".repeat(64)}`;
/** A different well-formed hash, used to separate the two fields. */
export const FIXTURE_OTHER_HASH = `sha256:${"7".repeat(64)}`;

/**
 * WR-000046  the receipt carries the asked-about hash and the PROPOSAL carries a
 *            different one. Must ADMIT — which proves the receipt's field is the
 *            one consulted.
 * WR-000040  the mirror: the proposal carries the asked-about hash and the
 *            receipt carries none. Must REFUSE on the hash — which proves the
 *            proposal's field is not consulted.
 * WR-000054  an acceptance receipt whose work_request_card detail is absent.
 *            Must refuse as an incomplete row rather than be synthesized into an
 *            accepted one with a null outcome.
 */
const PREDECESSOR_ROWS = Object.freeze({
  "WR-000046": Object.freeze([Object.freeze({
    status: "accepted", detail_row_count: 1,
    accepted_feedback_hash: FIXTURE_ASKED_HASH,
    feedback_hash: FIXTURE_OTHER_HASH,
  })]),
  "WR-000040": Object.freeze([Object.freeze({
    status: "accepted", detail_row_count: 1,
    accepted_feedback_hash: null,
    feedback_hash: FIXTURE_ASKED_HASH,
  })]),
  "WR-000054": Object.freeze([Object.freeze({
    status: "accepted", detail_row_count: 0,
    accepted_feedback_hash: FIXTURE_ASKED_HASH,
    feedback_hash: null,
  })]),
});

export const FIXTURE_UNREACHABLE = "unreachable";

export async function fetchPredecessorOutcomeRows(query) {
  const workRequestRef = query?.workRequestRef;
  const storeRef = "record-layer:work-request-outcome-feedback";
  if (workRequestRef === FIXTURE_UNREACHABLE)
    throw new SeamStoreUnreachable(storeRef, "the query did not finish");
  return { store_ref: storeRef, rows: PREDECESSOR_ROWS[workRequestRef] ?? [] };
}

/** Card 12 and card 13 are proved by the other fixture; here they hold nothing. */
export async function fetchSchedulerLedgerRows() {
  return { store_ref: "control-plane:ops.service+ops.run", rows: [] };
}

export async function fetchCheckConclusionRows() {
  return { store_ref: "github:checks", rows: [] };
}
