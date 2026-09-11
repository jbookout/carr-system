// DoctorCRE v5 slice V5-A02, the seam half: THE RULING TABLE, and it is the
// only switch in this build.
//
// WHAT THIS FILE IS. Three seams that Gate Zero is owed have a proposed store
// behind each of them — cards 11, 12 and 13 of JOE-GATE-ZERO-CARDS.md. The
// readers for all three are built and tested. None of them reads anything,
// because a proposal is not a ruling: each seam's `decision_id` below is null,
// and while it is null its reader returns the identical refusal Gate Zero
// returns today, without touching its store.
//
// WHAT MAKES THIS THE ONLY SWITCH, said plainly because the whole slice exists
// to refuse the alternatives:
//
//   * NO CALLER-SUPPLIED DECISION ID. The readers take a query — which step,
//     which commit — never a decision id, never a store handle, never a reader
//     object. There is no argument that could carry one.
//   * NO ENVIRONMENT OVERRIDE. Nothing in this file or in the reader module
//     reads the process environment. A seam an env var could open is a seam
//     anyone with a shell can open.
//   * NO PARTIAL EVIDENCE. While a decision id is null the reader does not
//     open its store, does not run a query, and reports nothing it might have
//     found. Half an answer read from an unruled store is still an unruled
//     read.
//
// HOW A RULING LANDS. Joe pastes the decision id — the uuid `log-decision`
// returns — in place of the `null` on the `decision_id:` line of the seam he
// ruled, and nothing else in this repository changes. The reader then reads the
// store named on the `store_ref:` line beside it. If he rules a DIFFERENT store
// than the one proposed, the `store_ref` line changes too, and it must name a
// member of GATE_ZERO_SEAM_STORE_REFS — an unknown store ref is refused by the
// reader rather than guessed at.
//
// WHY THE STORE REF IS HERE AND NOT IN THE READER. Cards 11-13 each ask two
// questions at once: is this store authoritative for this purpose, and is it
// this store. Both answers belong to the same ruling, so they sit on the same
// two lines and are read together. A reader that chose its own store would be
// deciding half of what it was asked.
//
// THE PRODUCER SEAM IS NOT HERE. `seam:gate-zero-read-only-outcome-producer` is
// cards 9 and 10 — who holds the oracle, and whether r7 carries the
// registration — and it is deliberately NOT built. Its refusal is carried
// verbatim in gate-zero-seam-readers.v5.js as
// GATE_ZERO_PRODUCER_SEAM_NOT_BUILT, checked byte-for-byte against what
// emitGateZeroOutcome actually returns, and left exactly where it is.

/** The store refs a ruling may name. Closed: an unknown ref is refused. */
export const GATE_ZERO_SEAM_STORE_REFS = Object.freeze([
  "control-plane:ops.service+ops.run",
  "github:checks",
  "record-layer:work-request-outcome-feedback",
].sort());

/**
 * THE THREE LINES. One entry per seam; `decision_id` is the line Joe's ruling
 * goes on. Frozen, so nothing can write one at runtime — a ruling is a commit,
 * not a call.
 */
export const GATE_ZERO_SEAM_RULINGS = Object.freeze({
  "seam:gate-zero-predecessor-outcome-reader": Object.freeze({
    card: 11,
    question: "which store an accepted predecessor outcome comes from",
    proposed_store: "the record layer's own Work Request outcome feedback, status accepted",
    store_ref: "record-layer:work-request-outcome-feedback",
    decision_id: null,
  }),
  "seam:scheduler-canary-reader": Object.freeze({
    card: 12,
    question: "which scheduler surface supplies a canary and the observation after it",
    proposed_store: "ops.service / the Control Plane ledger, with bin/run-scheduled.sh's ops.run rows as the observation after dispatch",
    store_ref: "control-plane:ops.service+ops.run",
    decision_id: null,
  }),
  "seam:gate-conclusion-reader": Object.freeze({
    card: 13,
    question: "which surface reports a gate's own conclusion",
    proposed_store: "hosted CI check conclusions via the GitHub checks API, which is the merge gate today",
    store_ref: "github:checks",
    decision_id: null,
  }),
});

/**
 * The shape a pasted ruling must have: the uuid `log-decision` returns. A value
 * that is not one is not a near miss to be tolerated — the reader refuses it the
 * same way it refuses null, because a malformed ruling is not a ruling.
 */
export const DECISION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * Whether a seam's ruling is on the record. The ONE test, in one place, so a
 * second reader cannot invent a looser one. Takes the entry, not a caller value.
 */
export function seamRulingDecisionRef(entry) {
  if (entry === null || typeof entry !== "object") return null;
  const id = entry.decision_id;
  return typeof id === "string" && DECISION_ID.test(id) ? id : null;
}
