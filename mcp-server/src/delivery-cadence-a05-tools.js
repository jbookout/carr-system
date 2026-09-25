// DoctorCRE v5 slice V5-A05 -- the production door onto
// delivery-cadence-a05.v5.js's pure classifiers (migration 0606).
//
// Three verbs:
//   cadence-status              read-only. ops.v5_a05_cadence_status mirrors
//                                evaluateCadenceReceipt; this is what the
//                                daily sweep job and the read-only live
//                                acceptance check both call.
//   record-cadence-receipt      write. The one door onto
//                                ops.v5_a05_cadence_receipt. Performs no
//                                escalation itself.
//   raise-delivery-cadence-alert  write. THE call site that actually invokes
//                                classifyEscalationReason/evaluateEscalationRouting
//                                from the pure v5 module -- resolving the gap
//                                Jev named: a library nothing calls cannot
//                                pass live. It writes a signal_event (the
//                                existing WR-000113 evidence mechanism) and
//                                mints through the existing
//                                ops.mint_notification door (0606 extends it
//                                with p_bypass_quiet_hours; it does not
//                                duplicate it), so an urgent alert reaches the
//                                same notification queue and the same
//                                quiet-hours preference row as everything
//                                else in the system -- it only sometimes
//                                bypasses the suppression that reads that row.
//
// The scheduled sweep (ops/delivery-cadence-a05-sweep.py, no seal owed -- decision
// 05e144eb) is the caller that turns a "missed" cadence-status read into a
// raise-delivery-cadence-alert call for "replan on miss". A human or another
// system component reporting an urgent security/data-loss/outward-harm event
// calls raise-delivery-cadence-alert directly.

import { personalScopeForActor } from "./identity.js";
import {
  V5_A05_ORDINARY_REASON_IDS, V5_A05_URGENT_REASON_IDS,
  evaluateEscalationRouting,
} from "./delivery-cadence-a05.v5.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SUBJECT_TYPE = /^[a-z][a-z0-9_]{0,63}$/;
const SUBJECT_REF = /^[a-z0-9][a-z0-9._-]{0,127}$/i;

// R03_SEVERITY-equivalent mapping for V5-A05: the escalation router's
// severity/routing maps onto the SAME closed signal_event severity vocabulary
// (info|warning|critical) record-signal already uses, and mint_notification's
// OWN existing map (critical->failure, warning->action_required, info->no
// notification) is reused unchanged -- see investigation.js R03_SEVERITY.
function signalSeverityFor(routing) {
  if (routing.routing === "deliver_immediately") return "critical";
  if (routing.routing === "batch_for_morning") return "warning";
  return "info"; // no_queue_entry: evidence only, never notified.
}
const MINT_SEVERITY = Object.freeze({ critical: "failure", warning: "action_required" });

export function deliveryCadenceA05Tools({ withEnvelope, writeEvent, ToolError }) {
  async function require0606(c) {
    const r = await c.query(
      `select to_regprocedure('ops.v5_a05_cadence_status(text,text)') is not null as status_fn,
              to_regprocedure('ops.v5_a05_record_cadence_receipt(text,text,uuid)') is not null as record_fn,
              to_regprocedure('ops.mint_notification(text,uuid,text,text,text,text,text,text,text,boolean)') is not null as mint_fn`);
    const s = r.rows[0];
    if (s.status_fn && s.record_fn && s.mint_fn) return;
    throw new ToolError({ error: "migration_not_applied",
      migration: "0606_delivery_cadence_a05", present: s,
      hint: "apply migration 0606 before using V5-A05 verbs; nothing was written" });
  }

  function assertSubject(args) {
    if (typeof args.subject_type !== "string" || !SUBJECT_TYPE.test(args.subject_type))
      throw new ToolError({ error: "invalid_subject_type", subject_type: args.subject_type });
    if (typeof args.subject_ref !== "string" || !SUBJECT_REF.test(args.subject_ref))
      throw new ToolError({ error: "invalid_subject_ref", subject_ref: args.subject_ref });
  }

  async function mintNotificationGuarded(c, params) {
    await c.query("savepoint carr_v5_a05_mint");
    try {
      const minted = await c.query(
        "select ops.mint_notification($1::text,$2::uuid,$3::text,$4::text," +
        "$5::text,$6::text,$7::text,$8::text,$9::text,$10::boolean) as minted",
        [params.event_source, params.event_ref, params.subject_type, params.subject_ref,
         params.reason, params.severity, params.deep_link, params.dedupe_key,
         params.recipient_slug, params.bypass_quiet_hours === true]);
      await c.query("release savepoint carr_v5_a05_mint");
      return minted.rows[0].minted;
    } catch (error) {
      await c.query("rollback to savepoint carr_v5_a05_mint");
      await c.query("release savepoint carr_v5_a05_mint");
      return { ok: true, minted: false, reason_id: "notification_mint_unavailable",
        detail: String(error?.message || error).slice(0, 160) };
    }
  }

  return {
    "cadence-status": {
      write: false,
      description: "Read-only V5-A05 cadence status for one subject: current, missed, or no_receipt_on_record, mirroring evaluateCadenceReceipt over ops.v5_a05_cadence_receipt. Takes no `now` argument -- the server clock is the only clock this reads (excluded_scope: clock reset).",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        subject_type: { type: "string" }, subject_ref: { type: "string" },
      }, required: ["subject_type", "subject_ref"] },
      handler: async (c, _actor, args) => {
        await require0606(c);
        assertSubject(args);
        const r = await c.query("select ops.v5_a05_cadence_status($1::text,$2::text) as status",
          [args.subject_type, args.subject_ref]);
        return r.rows[0].status;
      },
    },

    "record-cadence-receipt": {
      write: true,
      description: "Record that a subject's V5-A05 assurance cadence checked in. Inserts one append-only row into ops.v5_a05_cadence_receipt, expiring 14 days from now. Performs no escalation -- raise-delivery-cadence-alert and the daily sweep own that. Evidence is computed server-side by ops.v5_a05_record_cadence_receipt itself (Q008.D2: no caller-supplied evidence can reset the clock) -- a caller cannot pass or influence it. Disclosed gap (migration 0606's own header comment): no Completion Register producer exists yet for any V5-A05 subject, so the server-computed evidence records that gap rather than a fabricated outcome-row foreign key.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        subject_type: { type: "string" }, subject_ref: { type: "string" },
      }, required: ["idempotency_key", "subject_type", "subject_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-cadence-receipt", args, async () => {
        await require0606(c);
        assertSubject(args);
        const r = await c.query(
          "select ops.v5_a05_record_cadence_receipt($1::text,$2::text,$3::uuid) as receipt",
          [args.subject_type, args.subject_ref, args.idempotency_key]);
        const receipt = r.rows[0].receipt;
        if (receipt.deduplicated !== true) {
          await writeEvent(c, actor, "record-cadence-receipt", "v5_a05_cadence_receipt", receipt.receipt_id, {
            field: "issued", new: { subject_type: args.subject_type, subject_ref: args.subject_ref,
              expires_at: receipt.expires_at, replan_of: receipt.replan_of },
            cause: actor.human ? "human_stated" : "automation_job",
            idempotency_key: args.idempotency_key,
          });
        }
        return { ok: true, ...receipt };
      }),
    },

    // Review finding 8 (Opus adversarial review of PR #1236, round 1): a miss
    // must leave a durable miss record, not only notify. The signal_event
    // insert below is that durable record -- it is written unconditionally,
    // before the notify branch runs, upserted idempotently (on conflict do
    // nothing) rather than ever overwritten or deleted, and its signal_kind
    // carries the exact miss reason_id (cadence_miss_replan_required /
    // cadence_interval_exceeded_since_activation), so the miss survives
    // regardless of whether the notification mints, dedupes, or fails.
    // Disclosed gap: the finding also asked for the miss to "degrade rollout
    // state." No rollout-state, release-health, or deployment-health concept
    // or table exists anywhere in this repository today (confirmed by
    // repo-wide search) for a V5 delivery-program miss to degrade -- this PR
    // does not invent one. Per the same fallback this PR already used for
    // finding 2's Completion Register gap: disclosing the missing mechanism
    // here, rather than fabricating a table/column no other system reads, is
    // the safer choice until a real rollout-state surface exists to wire
    // into.
    "raise-delivery-cadence-alert": {
      write: true,
      description: "Raise a V5-A05 escalation candidate: urgent security/data-loss/outward-harm reasons deliver immediately and bypass quiet hours; an ordinary reason needing Joe's authority or naming unresolved intent batches for the morning brief; anything else is recorded as evidence only and never notifies. Classification is the closed, tested vocabulary in delivery-cadence-a05.v5.js -- reason_id alone never grants urgency, and requires_joe_authority/unresolved_intent are the only other inputs that can (excluded_scope: automatic authority widening). A durable signal_event row is written for every call before any notification branch runs -- see the finding-8 comment above this verb.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        reason_id: { type: "string", enum: [...V5_A05_URGENT_REASON_IDS, ...V5_A05_ORDINARY_REASON_IDS] },
        subject_type: { type: "string" }, subject_ref: { type: "string" },
        requires_joe_authority: { type: "boolean" }, unresolved_intent: { type: "boolean" },
        detail: { type: "string" },
      }, required: ["idempotency_key", "reason_id", "subject_type", "subject_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "raise-delivery-cadence-alert", args, async () => {
        await require0606(c);
        assertSubject(args);

        // THE ACTUAL PRODUCTION CALL SITE for the pure V5-A05 classifier.
        const routing = evaluateEscalationRouting({
          reason_id: args.reason_id,
          requires_joe_authority: args.requires_joe_authority === true,
          unresolved_intent: args.unresolved_intent === true,
          quiet_now: false, // this verb does not read quiet-hours itself; mint_notification does, from the preference row, at mint time.
        });
        const severity = signalSeverityFor(routing);
        const signalKey = `v5-a05:${args.reason_id}:${args.subject_type}:${args.subject_ref}:${args.idempotency_key}`;

        const inserted = await c.query(
          `insert into signal_event
             (producer,signal_key,signal_kind,subject_type,subject_ref,metric_name,
              observed_value,baseline_value,threshold_value,comparison,severity,detected_at,
              evidence_refs,payload,created_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
           on conflict (producer,signal_key) do nothing returning *`,
          ["v5-a05-delivery-cadence", signalKey, args.reason_id, args.subject_type, args.subject_ref,
           "urgent_harm_or_authority_occurrence", 1, null, 1, "gte", severity, new Date().toISOString(),
           JSON.stringify([`v5-a05:reason:${args.reason_id}`]),
           JSON.stringify({ routing: routing.routing, wakes_joe: routing.wakes_joe,
             requires_joe_authority: routing.requires_joe_authority,
             unresolved_intent: routing.unresolved_intent, detail: args.detail || null }),
           actor.id]);
        let row = inserted.rows[0];
        let duplicate = false;
        if (!row) {
          duplicate = true;
          row = (await c.query(`select * from signal_event where producer=$1 and signal_key=$2`,
            ["v5-a05-delivery-cadence", signalKey])).rows[0];
        } else {
          await writeEvent(c, actor, "raise-delivery-cadence-alert", "signal", row.id, {
            field: "threshold_crossing",
            new: { signal_kind: row.signal_kind, reason_id: args.reason_id, routing: routing.routing },
            cause: actor.human ? "human_stated" : "automation_job",
            idempotency_key: args.idempotency_key,
          });
        }

        const dedupeKey = `signal:${row.producer}:${row.signal_key}`;
        const mintSeverity = MINT_SEVERITY[row.severity];
        const scope = personalScopeForActor(actor);
        let notification = { ok: true, minted: false, reason_id: "no_sponsoring_partner" };
        if (duplicate) {
          notification = { ok: true, minted: false, reason_id: "duplicate_alert" };
        } else if (!mintSeverity) {
          notification = { ok: true, minted: false, reason_id: "severity_not_notifiable" };
        } else if (scope.status === "personal") {
          notification = await mintNotificationGuarded(c, {
            event_source: "signal_event", event_ref: row.id,
            subject_type: row.subject_type, subject_ref: row.subject_ref,
            reason: `V5-A05 ${args.reason_id}: ${routing.routing}`.slice(0, 500),
            severity: mintSeverity, deep_link: `/signals/${row.id}`, dedupe_key: dedupeKey,
            recipient_slug: scope.sponsor,
            bypass_quiet_hours: routing.bypasses_quiet_hours === true,
          });
        }

        return { ok: true, duplicate, signal: row, routing, notification };
      }),
    },
  };
}
