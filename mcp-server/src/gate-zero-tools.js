import { withEnvelope, writeEvent } from "./versioned-write.js";
import { GATE_ZERO_RECEIPT_SCHEMA, assertGateZeroReceipt, deriveGateZeroProducerSeat, gateZeroOutcomeCandidateDigest, gateZeroOutcomeDigest } from "./gate-zero-outcome-store.v5.js";
import { emitGateZeroOutcome } from "./gate-zero-assurance.v5.js";
import { ToolError } from "./tool-error.js";
import { GATE_ZERO_WRITER_SECRET_NAME } from "./gate-zero-seat-connection.v5.js";

export function gateZeroTools() {
  return {
  // ===== the Gate Zero read-only outcome (DoctorCRE v5 slice V5-A02, Step B) =====
    //
    // ONE VERB, AND IT CARRIES THE FIRST NON-HUMAN, NON-SPONSORED AUTHORITY CLASS
    // IN THIS REGISTRY. Every write verb before it either gated on a verified
    // human partner (`humanOnly: true`) or admitted any sponsored agent. This one
    // is neither: it refuses every actor except the single review-token seat that
    // holds oracle:gate-producer:gate-zero-read-only, under Joe's 2026-09-13
    // ruling d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70. The gate is enforced in
    // executeRegisteredTool through the `oracleSeatOnly` flag AND again inside the
    // handler through deriveGateZeroProducerSeat AND a third time in SQL by
    // ops.gate_zero_producer_actor_id() -- three independent derivations of one
    // fact, because a flag that nothing enforces is a label, which is exactly the
    // defect WR-000021 found on humanOnly.
    //
    // WHY IT IS DEFINED HERE rather than in a store module of its own: the
    // registry's source locator is part of the runtime mutation contract, and the
    // contract for this verb should read from the same file every other inline
    // verb's does. The receipt contract and the seat derivation live in
    // gate-zero-outcome-store.v5.js, which registers no verb of its own.
    "record-gate-zero-read-only-outcome": {
      discoveryOrder: 82,
      write: true, humanOnly: false, oracleSeatOnly: true,
      description: "ORACLE-SEAT-ONLY, AND NOT A HUMAN ACT: record the DoctorCRE v5 Gate Zero read-only outcome as one consumer-gate-receipt.v1, its recomputed digest, the instant it was observed and the seat that produced it. It refuses every actor except the one review-token seat holding oracle:gate-producer:gate-zero-read-only — a partner is refused, a sponsored agent is refused, and a review-token seat on a DIFFERENT lane is refused by name rather than admitted by authority class. That shape exists because r7 registers this producer's role as independent_control_plane_oracle and Joe ruled on 2026-09-13 (decision d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70) that the seat records the row on its own authority with no partner countersign. THE HUMAN ACT IN THIS CHAIN IS UNCHANGED AND IS DOWNSTREAM: accept-benchmark-manifest-draft is still humanOnly and still derives its acceptor from a live verified partner. THE RECEIPT IS PRODUCED IN THIS CALL, NOT SUPPLIED: the verb takes ONE argument, idempotency_key, and invokes the bound producer seam — Step A's zero-argument Gate Zero producer — which derives its own run binding, aims the three ruled evidence readers at it and assembles one consumer-gate-receipt.v1 signed with the identity the server derived for this call. There is no receipt argument and no receipt-shaped argument: any other top-level field is refused as unregistered_operation_fields before the handler runs. NOTHING HERE IS A CALLER'S WORD FOR ANYTHING — the producing seat comes from the frozen registration, the actor from the authenticated bearer match, the three receipt identities from the authenticated call, and the outcome digest is RECOMPUTED by the record layer from the stored receipt, so no caller-supplied digest is accepted and none is sent. What the producer emits is still checked against the closed twenty-one-field r7 schema, against the twelve constants the producer registry fixes, against the closed three-field authenticated-receipt-identity.v1 shape, and against the identity rule that the producer and evaluator are this call while the subject maker is not. A producer refusal records nothing and is reported as gate_zero_outcome_not_produced with the reason the producer gave. RETRYABLE, IMMUTABLE FIRST OUTCOME: any later call for the same candidate returns the row already recorded rather than writing a second one. If the offered receipt differs because its session, instant, evidence or verdict moved, it converges onto that immutable row so the caller can heal a missing audit event; the result explicitly separates recorded and offered digests, statuses and producer reasons. candidate_scoped_digest is informational only; candidate_digest is the row arbiter. It grants no dispatch, activation or execution authority, accepts no benchmark and starts no clock.",
      // ONE ARGUMENT, AND IT NAMES AN INTENDED ACT RATHER THAN A SUBJECT. The
      // caller says "record the outcome, once, under this key"; WHAT gets recorded
      // is produced here. A `receipt` property was in the first draft of this verb
      // and is deliberately gone: the closed top-level check in mutation-registry.js
      // refuses any key that is not in this list as `unregistered_operation_fields`,
      // so `receipt`, `outcome`, `consumer_gate_receipt` and every other
      // receipt-shaped spelling is refused BEFORE the handler runs.
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
        },
        required: ["idempotency_key"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-gate-zero-read-only-outcome", args, async () => {
        // ORDER IS DELIBERATE, and it is the same order the record layer uses.
        //   1. The seat is derived from the LIVE actor and the frozen
        //      registration. It runs FIRST, so an actor who may not write here
        //      never reaches a contract check that could tell them about the
        //      receipt shape.
        //   2. The receipt is checked against the derived seat.
        //   3. Only then does anything reach the database, where all three
        //      derivations happen again as the function owner.
        const seat = deriveGateZeroProducerSeat(actor);

        // THE RECEIPT IS PRODUCED, NOT RECEIVED (2026-09-13, PR 1014 correction).
        // `emitGateZeroOutcome` is the gate's bound producer seam: it calls Step
        // A's zero-argument producer, which derives its own run binding, aims the
        // three ruled readers at it, applies the three clauses over what came back
        // and assembles one consumer-gate-receipt.v1 signed with the identity
        // identity.js derived for THIS call. Nothing is handed in and nothing can
        // be: the producer's arity is zero and this verb has no receipt argument.
        //
        // A REFUSAL IS NOT A ROW. The producer refuses when it cannot authenticate,
        // when a row its binding stands on is absent, when a ruled seam returned no
        // row for the address it derived, or when its own falsifiers did not fire.
        // Each of those is a run that established nothing, so nothing is written
        // and the reason is reported by name. A clause that was READ and did not
        // hold is the opposite case: that is a real outcome with `status: "fail"`,
        // and it is recorded.
        const emitted = await emitGateZeroOutcome();
        if (emitted?.decision !== "report" || emitted?.status !== "outcome_produced"
            || !emitted?.receipt) {
          throw new ToolError({ error: "gate_zero_outcome_not_produced",
            reason_id: emitted?.reason_id ?? null,
            unavailable_because: emitted?.unavailable_because ?? null,
            decided_by: emitted?.decided_by ?? null,
            owed_seams: emitted?.owed_seams ?? null,
            producer_bound: emitted?.producer_bound ?? null,
            hint: "the Gate Zero producer did not emit an outcome in this call, so there is nothing to record. " +
                  "This is a refusal by the producer over the rows it derived, not a caller error: no receipt " +
                  "argument exists and none would have changed it." });
        }
        // CHECKED ANYWAY, against the derived seat and this call's own identity.
        // The producer already assembled the twenty-one fields, so this cannot be
        // a caller's malformed object -- which is exactly why it is checked: the
        // contract is the record layer's, not the producer's, and a producer that
        // drifted from r7's shape must be refused at the write path rather than
        // trusted because it is ours.
        const receipt = assertGateZeroReceipt(emitted.receipt, seat);

        // THE WRITE RUNS ON THE SEAT'S OWN CONNECTION, NOT ON `c` (2026-09-14,
        // PR 1014 third correction, standing-rule amendment 9).
        //
        // `c` is the ordinary writer connection every other verb uses, and that is
        // exactly why this call may not travel on it. Sol's finding 2: the record
        // layer's seat test read carr.acting_actor_slug, a GUC any session can set
        // on itself, so any carr_writer connection could have named the staffed
        // lane and been believed. Migration 0502 now revokes EXECUTE from
        // carr_writer, grants it to one capability bundle reachable by one login
        // role, and derives the seat from session_user. This opens a connection
        // that AUTHENTICATES as that role, with a secret used for nothing else.
        //
        // NO FALLBACK, BY DESIGN. A Worker that carries no such secret refuses here
        // by name. Falling back to `c` would be a deployment in which the whole
        // amendment is off and nothing said so — which is the failure this correction
        // exists to close, not a degradation worth tolerating.
        if (typeof c.seatConnection !== "function") {
          throw new ToolError({ error: "gate_zero_seat_connection_unavailable",
            required_secret: GATE_ZERO_WRITER_SECRET_NAME,
            hint: "recording a Gate Zero outcome requires the dedicated producer connection. The ordinary " +
                  "writer connection is refused by the record layer and is not used as a fallback: provision " +
                  "the login role and its secret before this verb can record anything." });
        }
        // ONE TRANSACTION ON THAT CONNECTION, carrying the write and the readback
        // together. The readback has to be inside it: on a FIRST call the row is
        // this transaction's own and is not visible to any other connection until
        // it commits.
        const row = await c.seatConnection(async seat => {
          const recordedId = (await seat.query(
            `select ops.gate_zero_record_read_only_outcome($1::uuid,$2::jsonb) as id`,
            [args.idempotency_key, JSON.stringify(receipt)])).rows[0].id;

          // READ THE DIGEST BACK OUT OF THE ROW rather than reporting the one this
          // process computed. The database recomputes it from the persisted receipt
          // with ops.gate_zero_outcome_digest(); reporting our own value would let a
          // caller believe a number the record layer never agreed to. The locally
          // computed digest is compared, not returned, so a divergence between the
          // two canonicalizations is a refusal here instead of a silent mismatch
          // that only surfaces when a benchmark acceptance binds the wrong value.
          //
          // THE STORED RECEIPT COMES BACK TOO, AND THE CHECK IS AGAINST IT rather
          // than against this call's object (PR 1014, second correction). On a
          // RETRY the row is the earlier run's -- same candidate, different
          // session_ref, different instants -- so comparing the row's digest with a
          // digest of THIS call's receipt refused every genuine second call, which
          // was the gateway half of Sol's finding 3. Recomputing from the persisted
          // receipt asks the question the check was always meant to ask: do the
          // record layer's canonical JSON and artifact-trust.js's agree about the
          // bytes that are actually stored? That holds on a first call and a retry
          // alike, and it is the stronger of the two readings.
          return (await seat.query(
            `select id, outcome_digest, candidate_scoped_digest, candidate_digest, status,
                  producing_seat_ref, receipt,
                  to_char(observed_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') as observed_at,
                  to_char(recorded_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"') as recorded_at
             from ops.gate_zero_read_only_outcome where id = $1::uuid`,
            [recordedId])).rows[0];
        });
        const localDigest = gateZeroOutcomeDigest(row.receipt);
        if (row.outcome_digest !== localDigest) {
          throw new ToolError({ error: "gate_zero_outcome_digest_divergence",
            recorded: row.outcome_digest, recomputed_here: localDigest,
            hint: "the record layer's canonical JSON and artifact-trust.js's disagreed about the stored receipt. " +
                  "The recorded value is the database's; this is a contract defect, not a caller error." });
        }
        // AND THE TWO CANONICALIZATIONS AGREE ABOUT THE PROJECTION TOO, asked over
        // the STORED receipt exactly as the full digest above is. This is the same
        // contract question in the narrower place: does the record layer's
        // projection-then-canonicalize produce the bytes artifact-trust.js's does?
        // It is a property of the row alone, so it holds on a first write and on
        // every retry that converges onto the immutable row.
        const storedCandidateDigest = gateZeroOutcomeCandidateDigest(row.receipt);
        if (row.candidate_scoped_digest !== storedCandidateDigest) {
          throw new ToolError({ error: "gate_zero_outcome_candidate_digest_divergence",
            recorded: row.candidate_scoped_digest, recomputed_here: storedCandidateDigest,
            hint: "the record layer's candidate projection and artifact-trust.js's disagreed about the stored " +
                  "receipt. The recorded value is the database's; this is a contract defect, not a caller error." });
        }
        // KEEP THE OFFERED RECEIPT SEPARATE FROM THE RECORDED ONE. A normal retry
        // necessarily carries fresh session and time bytes, and its evidence may
        // have moved. The record layer returns the immutable row for the candidate
        // so this outer transaction can heal a missing audit event; these digests
        // make that convergence visible rather than describing the offered run as
        // if it had been stored.
        const offeredDigest = gateZeroOutcomeDigest(receipt);
        const offeredCandidateDigest = gateZeroOutcomeCandidateDigest(receipt);
        const convergedOntoRecorded = row.outcome_digest !== offeredDigest;

        // ONE EVENT PER OUTCOME ROW, SERIALIZED ON THE OUTCOME. The retry is
        // exactly what makes this necessary: it exists to heal a missing event, and
        // an unguarded insert would instead write a second one every time a call
        // got past the seat commit.
        //
        // THE LOCK BLOCKS, DELIBERATELY. A try-lock let a concurrent retry return
        // `ok: true` without an event; if the holder then rolled back, the row was
        // still eventless and only a THIRD call could repair it. That is eventually
        // healable, not the unconditional second-call healing the refusal requires.
        // In production the dependency graph has no cycle: each seat transaction
        // commits before its caller reaches this lock, so a waiter holds no row the
        // lock owner needs. The former test deadlock came from its harness awaiting
        // the waiter before committing the owner transaction; the proof now stages
        // the production order instead of weakening the production invariant.
        await c.query(
          "select pg_advisory_xact_lock(hashtextextended($1, 0))",
          [`gate-zero-outcome-event:${row.id}`]);
        const eventAlready = await c.query(
          `select 1 from event
          where verb = 'record-gate-zero-read-only-outcome'
            and subject_type = 'gate_zero_outcome' and subject_id = $1::uuid
          limit 1`, [row.id]);
        if (!eventAlready.rows.length) {
          await writeEvent(c, actor, "record-gate-zero-read-only-outcome", "gate_zero_outcome", row.id,
            { field: "outcome_recorded",
              new: { outcome_digest: row.outcome_digest,
                     candidate_scoped_digest: row.candidate_scoped_digest,
                     candidate_digest: row.candidate_digest,
                     status: row.status, observed_at: row.observed_at,
                     producing_seat_ref: row.producing_seat_ref },
              idempotency_key: args.idempotency_key });
        }

        return {
          ok: true, outcome_id: row.id,
          step_ref: "step:gate-zero-read-only-outcome",
          gate_id: "gate-zero-read-only-accepted",
          receipt_schema: GATE_ZERO_RECEIPT_SCHEMA,
          outcome_digest: row.outcome_digest,
          // INFORMATIONAL PROJECTION OF THE RECORDED RECEIPT. candidate_digest,
          // not this value, is the one-row-per-candidate arbiter.
          candidate_scoped_digest: row.candidate_scoped_digest,
          candidate_digest: row.candidate_digest,
          converged_onto_recorded_outcome: convergedOntoRecorded,
          offered_outcome_digest: offeredDigest,
          offered_candidate_scoped_digest: offeredCandidateDigest,
          status: row.status,
          observed_at: row.observed_at,
          recorded_at: row.recorded_at,
          producing_seat_ref: row.producing_seat_ref,
          producing_seat_charter_decision_ref: seat.charter_decision_ref,
          producing_seat_staffing_decision_ref: seat.staffing_decision_ref,
          producer_reason_id: convergedOntoRecorded ? null : emitted.reason_id,
          receipt_status: row.receipt.status,
          offered_producer_reason_id: emitted.reason_id,
          offered_receipt_status: receipt.status,
          // WHAT THIS ROW IS WORTH, said on the result so a consumer does not read
          // more into it than it carries.
          digest_recipe: "canonical-JSON sha256 over the TAGGED two-element array " +
            "[\"consumer-gate-receipt.v1\", <receipt>], recomputed by the record layer. r7's " +
            "receipt_payload_digest_rule declares that preimage for this schema (amended 2026-09-13) and states that " +
            "a plain digest over the receipt alone does not satisfy it. candidate_scoped_digest is the UNTAGGED " +
            "canonical-JSON sha256 over a projection of the receipt — without observed_at, ttl_expires_at and the " +
            "per-call session_ref of each of its three identities — because that projection is not a " +
            "consumer-gate-receipt.v1 and r7's rule does not speak about it. It is an informational comparison aid, " +
            "not evidence or an admission key; candidate_digest is the one-row-per-candidate arbiter.",
          effects: Object.freeze({
            creates_effect: false, clock_started: false, benchmark_accepted: false,
            note: "recording an outcome binds nothing on its own. Benchmark acceptance reads the current passing " +
                  "outcome and is still humanOnly; the Journey 1 clock still starts at the first passing " +
                  "foundation-assurance-minimum receipt, which this verb neither issues nor reaches.",
          }),
        };
      }),
    },
  };
}
