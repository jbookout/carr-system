// DoctorCRE v5 slice V5-A02, the seam half: THE RULING TABLE, and it is the
// only switch in this build.
//
// WHAT THIS FILE IS. Three seams that Gate Zero is owed have a store proposed
// behind each of them — cards 11, 12 and 13 of JOE-GATE-ZERO-CARDS.md. The
// readers for all three are built and tested. None of them reads anything,
// because a proposal is not a ruling: each card's `decision_id` below is null,
// and while it is null its reader returns the identical refusal Gate Zero
// returns today, without touching its store.
//
// WHAT THE CARDS PROPOSE, said here in a comment and NOT in an exported value,
// because a proposal is not a fact about the world and no consumer should be
// able to read one off this module's surface:
//
//   card 11  the record layer's own Work Request outcome feedback, status
//            accepted, as the store an accepted predecessor outcome comes from
//   card 12  ops.service / the Control Plane ledger, with bin/run-scheduled.sh's
//            ops.run rows as the observation after dispatch
//   card 13  hosted CI check conclusions via the GitHub checks API, which is the
//            merge gate today
//
// WHAT MAKES THIS THE ONLY SWITCH, said plainly because the whole slice exists
// to refuse the alternatives:
//
//   * NO CALLER-SUPPLIED DECISION ID. `seamRulingRef` takes a CARD TOKEN and
//     nothing else. There is no argument that could carry a decision id, and
//     the only decision id it can ever return is the one written on the line
//     below. A ruling is a commit, not a call.
//   * NO EXPORTED TABLE. The table itself is module-private. A consumer cannot
//     read it, cannot enumerate it, and cannot hold a reference to an entry
//     whose `decision_id` it might later hope to see change.
//   * NO ENVIRONMENT OVERRIDE. Nothing in this file or in the reader module
//     reads the process environment. A seam an env var could open is a seam
//     anyone with a shell can open.
//   * NO PARTIAL EVIDENCE. While a decision id is null the reader does not
//     open its store, does not run a query, and reports nothing it might have
//     found. Half an answer read from an unruled store is still an unruled
//     read.
//
// HOW A RULING LANDS. Joe pastes the decision id — the uuid `log-decision`
// returns — in place of the `null` on the `decision_id:` line of the card he
// ruled, and nothing else in this repository changes. The reader then reads the
// store named on the `store_ref:` line beside it. If he rules a DIFFERENT store
// than the one proposed, the `store_ref` line changes too, and it must name a
// member of STORE_REFS below — an unknown store ref makes the lookup answer
// "not ruled", so the reader keeps refusing rather than guessing.
//
// WHY THE STORE REF IS HERE AND NOT IN THE READER. Cards 11-13 each ask two
// questions at once: is this store authoritative for this purpose, and is it
// this store. Both answers belong to the same ruling, so they sit on the same
// two lines and are read together. A reader that chose its own store would be
// deciding half of what it was asked. The reader still holds a veto — it serves
// exactly one store ref per card and refuses any other — so the two halves have
// to agree before a single row is fetched.
//
// THE PRODUCER SEAM IS NOT HERE. `seam:gate-zero-read-only-outcome-producer` is
// cards 9 and 10 — who holds the oracle, and whether r7 carries the
// registration — and it is deliberately NOT built. Its refusal is
// `emitGateZeroOutcome()` in gate-zero-assurance.v5.js, unchanged, and this
// slice neither copies it nor adds a ruling line for it.

/** The store refs a ruling may name. Closed: an unknown ref is not a ruling. */
const STORE_REFS = Object.freeze([
  "control-plane:ops.service+ops.run",
  "github:checks",
  "record-layer:work-request-outcome-feedback",
]);

/**
 * The shape a pasted ruling must have: the uuid `log-decision` returns. A value
 * that is not one is not a near miss to be tolerated — the lookup treats it the
 * same way it treats null, because a malformed ruling is not a ruling.
 */
const DECISION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * THE THREE LINES. One entry per card; `decision_id` is the line Joe's ruling
 * goes on. Module-private and frozen: nothing can write one at runtime and
 * nothing outside this file can hold one.
 */
const SEAM_RULINGS = Object.freeze({
  "card:11": Object.freeze({
    question: "which store an accepted predecessor outcome comes from",
    store_ref: "record-layer:work-request-outcome-feedback",
    decision_id: null,
  }),
  "card:12": Object.freeze({
    question: "which scheduler surface supplies a canary and the observation after it",
    store_ref: "control-plane:ops.service+ops.run",
    decision_id: null,
  }),
  "card:13": Object.freeze({
    question: "which surface reports a gate's own conclusion",
    store_ref: "github:checks",
    decision_id: null,
  }),
});

/**
 * Whether a card's ruling is on the record, and which store it names.
 *
 * THE ONLY EXPORT OF THIS MODULE, and the only way anything reaches the table.
 * It takes a card token — "card:11", "card:12", "card:13" — and returns either
 * null or a frozen pair built ENTIRELY from the constants above. Every other
 * argument, of every type, answers null: an unknown token, a Proxy, a number, a
 * decision id someone hoped would be accepted as one.
 *
 * Today it returns null for all three, because all three decision ids are null.
 */
function seamRulingRefOf(cardRef) {
  if (typeof cardRef !== "string") return null;
  if (!Object.hasOwn(SEAM_RULINGS, cardRef)) return null;
  const entry = SEAM_RULINGS[cardRef];
  const id = entry.decision_id;
  if (typeof id !== "string" || !DECISION_ID.test(id)) return null;
  if (!STORE_REFS.includes(entry.store_ref)) return null;
  return Object.freeze({ decision_ref: id, store_ref: entry.store_ref });
}

/**
 * THE SAME GUARDED BOUNDARY THE OTHER TWO SEAM MODULES USE, and it is here for
 * the same reason rather than because this lookup is expected to throw.
 * `Object.hasOwn` on a frozen literal and a regular expression over a string
 * have no throwing path today — but "today" is a property of this one
 * implementation, and the invariant a caller relies on is a property of the
 * SURFACE: nothing this module exports lets an engine-built error out, so no
 * caller's frame name can ever come back in a stack. Not-ruled is the only thing
 * this lookup can say when it cannot say anything, and it is the fail-closed
 * answer: a null here shuts the seam.
 */
export function seamRulingRef(cardRef) {
  try {
    return seamRulingRefOf(cardRef);
  } catch {
    return null;
  }
}
