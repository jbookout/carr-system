import { FK, RESEARCH_EVIDENCE_SCHEMA, researchEvidence, resolveSubject, stampResearch } from "./verb-support.js";
import { versionGuard, withEnvelope, writeEvent } from "./versioned-write.js";
import { ToolError } from "./tool-error.js";
import { canExercisePartnerAuthority, partnerAuthoritySlugForActor } from "./partner-authority.js";
import { lockLeadLifecycle, validateStageReview } from "./lead-workspace.js";

const LEAD_OWNERS = ["joe", "dell"];
const LEAD_SCORE_FIELDS = {
  score: { type: ["integer", "null"], minimum: 0, maximum: 100 },
  score_reason: { type: ["string", "null"] },
  owner: { type: "string", enum: LEAD_OWNERS },
};

function validateLeadScoreFields(fields) {
  if (Object.hasOwn(fields, "score") && fields.score !== null &&
      (!Number.isInteger(fields.score) || fields.score < 0 || fields.score > 100))
    throw new ToolError({ error: "invalid_score", hint: "score must be an integer from 0 to 100, or null" });
  if (Object.hasOwn(fields, "score_reason") && fields.score_reason !== null && typeof fields.score_reason !== "string")
    throw new ToolError({ error: "invalid_score_reason", hint: "score_reason must be a string, or null" });
  if (Object.hasOwn(fields, "owner") && !LEAD_OWNERS.includes(fields.owner))
    throw new ToolError({ error: "invalid_owner", valid: LEAD_OWNERS });
}

async function resolveLeadOwner(client, slug) {
  const owner = (await client.query("select id,slug,display_name from actor where slug=$1 and active", [slug])).rows[0];
  if (!owner) throw new ToolError({ error: "actor_not_provisioned", slug });
  return { owner: owner.slug, owner_id: owner.id, owner_label: owner.display_name };
}

export function leadTools() {
  return {
    "new-lead": {
      discoveryOrder: 35,
      write: true,
      description: "Create a lead over a new or existing party; mints the next L-ref atomically. Accepts score (integer 0-100 or null), score_reason (string or null), and owner (joe|dell). Alabama parties default to Dell when owner is omitted; other parties default to the caller. Stage must be an existing lead_stage slug (they were imported from the live registry).",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        party_id: { type: "string", description: "from add-party or find" },
        stage: { type: "string" }, lane: { type: "string" }, segment: { type: "string" },
        ...LEAD_SCORE_FIELDS,
        source_type: { type: "string" }, source_detail: { type: "string" } },
        required: ["idempotency_key","party_id","stage"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "new-lead", args, async () => {
        validateLeadScoreFields(args);
        // stage and lane are FOREIGN KEYS (lead_stage.slug, lead_lane.slug). They used
        // to go straight into the insert, so a plausible-but-wrong value — `lane:
        // "referral"`, which reads like an obvious lane and is not one — came back as
        // a bare "internal error" with nothing naming the field or the options.
        // Measured live 2026-08-10 creating Dr. Harlan's lead: three attempts failed
        // opaquely and the bare call succeeded, which tells the caller nothing about
        // WHICH field was wrong. Same failure class as loop #261.
        for (const [field, table] of [["stage", "lead_stage"], ["lane", "lead_lane"]]) {
          const v = args[field];
          if (!v) continue;
          const hit = await c.query(`select 1 from ${table} where slug=$1`, [v]);
          if (!hit.rows.length) {
            const all = await c.query(`select slug from ${table} order by slug`);
            throw new ToolError({ error: `unknown_${field}`, got: v,
              valid: all.rows.map(x => x.slug),
              hint: `${field} is a foreign key into ${table}; pass one of the listed slugs. Inventing a plausible one fails at the database, not here.` });
          }
        }
        const party = (await c.query("select state from party where id=$1", [args.party_id])).rows[0];
        if (!party) throw new ToolError({ error: "party_not_found" });
        const ownerSlug = args.owner ?? (party.state === "AL" ? "dell" : null);
        const owner = ownerSlug ? await resolveLeadOwner(c, ownerSlug)
          : { owner: actor.slug, owner_id: actor.id, owner_label: actor.display };
        const ref = (await c.query("select 'L-' || lpad(nextval('ref_lead_seq')::text, 3, '0') as r")).rows[0].r;
        const r = await c.query(
          `insert into lead (registry_ref, party_id, stage, lane, segment, source_type, source_detail,
             owner_id, owner_label, created_by, updated_by, score, score_reason)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10,$11,$12) returning id`,
          [ref, args.party_id, args.stage, args.lane || null, args.segment || null,
           args.source_type || null, args.source_detail || null, owner.owner_id, owner.owner_label,
           actor.id, args.score ?? null, args.score_reason ?? null]);
        await writeEvent(c, actor, "new-lead", "lead", r.rows[0].id,
          { new: { ref, score: args.score ?? null, score_reason: args.score_reason ?? null, ...owner }, idempotency_key: args.idempotency_key });
        return { ok: true, lead_id: r.rows[0].id, ref };
      }),
    },
    "promote-pool": {
      discoveryOrder: 36,
      write: true,
      description: "Promote a candidate_pool row into a real lead: mints the party, mints the next L-ref, copies the identity, contact and est-lease-event stamps across, points the pool row at the new lead and flips it to 'promoted'. ONE-WAY BY DESIGN — there is no demote verb; a lead created in error is worked through the lead's own lifecycle. Only a row whose status is still 'pool' can promote: a 'promoted' row would duplicate, and a 'suppressed_dup' row already points at the record it duplicates. A dup_tier 'review' row IS promotable — that tier exists precisely so a weak match never silently blocks Joe. Read the row from v_pool first and pass its version as base_version.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        pool_id: { type: "string", description: "candidate_pool.id, from v_pool" },
        base_version: { type: "integer", description: "the pool row's version, from a fresh read" },
        stage: { type: "string", description: "lead_stage slug — a promoted lead is one Joe is working, so it needs a real stage" },
        lane: { type: "string", description: "lead_lane slug (optional)" },
        source_detail: { type: "string", description: "why this one, now — free text provenance" },
        research_evidence: RESEARCH_EVIDENCE_SCHEMA },
        required: ["idempotency_key","pool_id","base_version","stage","research_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "promote-pool", args, async () => {
        await versionGuard(c, "candidate_pool", args.pool_id, args.base_version);
        const p = (await c.query(
          `select id, source, source_key, status, dup_tier, dup_ref, name, org_name, vertical,
                city, county, state, email, phone, segment, est_lease_event, est_basis
           from candidate_pool where id = $1`, [args.pool_id])).rows[0];
        if (p.status !== "pool")
          throw new ToolError({ error: "not_promotable", status: p.status, dup_ref: p.dup_ref,
            hint: p.status === "promoted"
              ? "this row already became a lead; read v_pool for its promoted_ref"
              : "this row is marked as a duplicate of an existing record — work that record instead" });
        const evidence = researchEvidence(args.research_evidence,
          ["name", "company", "phone", "specialty", "market"], "promote-pool");

        // The org, when the source named one, becomes its own party so the lead
        // hangs off a person who belongs to a practice — the shape add-party and
        // every export view already assume.
        let orgId = null;
        if (p.org_name) {
          orgId = (await c.query(
            "insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id",
            [p.org_name, actor.id])).rows[0].id;
          await stampResearch(c, actor, orgId, evidence);
        }
        const partyId = (await c.query(
          `insert into party (kind,name,org_id,phone,email,city,state,county,specialty,
                            created_by,updated_by)
         values ('person',$1,$2,$3,$4,$5,$6,$7,$8,$9,$9) returning id`,
          [p.name, orgId, p.phone || null, p.email || null, p.city || null,
           p.state || null, p.county || null, p.vertical || null, actor.id])).rows[0].id;
        await stampResearch(c, actor, partyId, evidence);

        const ref = (await c.query(
          "select 'L-' || lpad(nextval('ref_lead_seq')::text, 3, '0') as r")).rows[0].r;
        // est_lease_event rides along per Joe's ruling 3, and it keeps its est-
        // naming on the far side: it lands in lead.est_lease_event with its basis
        // in event_source, never in a field that reads as a confirmed date.
        const lead = (await c.query(
          `insert into lead (registry_ref, party_id, stage, lane, segment, source_type,
           source_detail, est_lease_event, event_source, owner_id, owner_label,
           created_by, updated_by)
         values ($1,$2,$3,$4,$5,'prospect-pool',$6,$7,$8,$9,$10,$9,$9) returning id`,
          [ref, partyId, args.stage, args.lane || null, p.segment || null,
           args.source_detail || `promoted from ${p.source} ${p.source_key}`,
           p.est_lease_event || null, p.est_basis || null, actor.id, actor.display])).rows[0].id;

        await c.query(
          `update candidate_pool set status='promoted', promoted_lead_id=$1, updated_by=$2
          where id=$3 and status='pool'`, [lead, actor.id, args.pool_id]);

        await writeEvent(c, actor, "promote-pool", "lead", lead,
          { new: { ref, from_pool: p.source_key, est_lease_event: p.est_lease_event },
            idempotency_key: args.idempotency_key });
        await writeEvent(c, actor, "promote-pool", "candidate_pool", args.pool_id,
          { field: "status", old: { status: "pool" }, new: { status: "promoted", lead: ref },
            idempotency_key: args.idempotency_key });
        return { ok: true, lead_id: lead, ref, party_id: partyId,
                 est_lease_event: p.est_lease_event, est_basis: p.est_basis };
      }),
    },

    "decline-candidate": {
      discoveryOrder: 37,
      write: true,
      description: "Record that a HUMAN looked at a candidate and said no. This is promote-pool's missing counterpart, and it is the only thing that makes the claim card shorter. Measured 2026-08-09: six lanes had accumulated 9,870 candidates and promoted zero, ever, because a candidate rejected at the board stayed exactly as claimable as before and came back on every future card forever. A decline is NOT a suppression: suppression is a machine's assertion about identity and can be wrong, a decline is a human's judgment about fit and no sweep re-litigates it. The reason is REQUIRED and is the input to the lane-retirement decision, since 'no contact channel' is a fixable lane defect, 'out of territory' is a mis-scoped lane, and 'not a fit' is a lane working correctly with a low hit rate. Nothing is deleted: the row keeps its research and its provenance, it just stops being presented. Read the row from v_claim_card or v_pool first and pass its version as base_version.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        pool_id: { type: "string", description: "candidate_pool.id, from v_claim_card" },
        base_version: { type: "integer", description: "the pool row's version, from a fresh read" },
        reason: { type: "string", description: "why, in the human's own words. Required. One line is enough, but it must say something a lane owner could act on." } },
        required: ["idempotency_key","pool_id","base_version","reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "decline-candidate", args, async () => {
        const reason = (args.reason || "").trim();
        // Refused rather than defaulted. A blank reason would satisfy the database
        // constraint's letter if it were only NOT NULL, and would tell the lane
        // owner nothing — which is the whole point of collecting it.
        if (!reason)
          throw new ToolError({ error: "reason_required",
            hint: "say why in your own words: no contact channel, out of territory, "
                + "corporate-owned, already represented, not a fit. The reason is what "
                + "makes a lane's decline pattern readable." });

        await versionGuard(c, "candidate_pool", args.pool_id, args.base_version);
        const p = (await c.query(
          `select id, source, source_key, name, status, promoted_lead_id
           from candidate_pool where id = $1`, [args.pool_id])).rows[0];
        if (!p)
          throw new ToolError({ error: "not_found", table: "candidate_pool", id: args.pool_id });

        // Idempotent on an already-declined row, and REFUSING on a promoted one.
        // Declining something that already became a lead would silently strand the
        // lead: promote-pool is one-way by design and there is no demote, so the
        // honest answer is to work the lead's own lifecycle instead.
        if (p.status === "declined")
          return { ok: true, pool_id: p.id, already: "declined",
                   note: "already declined; nothing changed" };
        if (p.status !== "pool")
          throw new ToolError({ error: "not_declinable", status: p.status,
            lead_id: p.promoted_lead_id || null,
            hint: p.status === "promoted"
              ? "this candidate already became a lead. Declining here would strand it; "
                + "work the lead's own lifecycle instead."
              : "this row is already marked as a duplicate of a record we hold" });

        await c.query(
          `update candidate_pool
            set status='declined', declined_at=now(), declined_by=$1,
                decline_reason=$2, updated_by=$1
          where id=$3 and status='pool'`, [actor.id, reason, args.pool_id]);

        await writeEvent(c, actor, "decline-candidate", "candidate_pool", args.pool_id,
          { field: "status", old: { status: "pool" },
            new: { status: "declined", reason, lane: p.source },
            idempotency_key: args.idempotency_key });

        return { ok: true, pool_id: p.id, name: p.name, lane: p.source,
                 status: "declined", reason,
                 note: "off the claim card permanently; the research and provenance are kept" };
      }),
    },

    "log-outreach": {
      discoveryOrder: 38,
      write: true,
      description: "THE DISPOSITION STEP: say what happened after you actually tried to reach someone, in ONE action. Completes your open ball on that subject, logs the touch at its real time, and either sets the next ball or closes the lead out. Use this instead of calling log-activity and set-next-action separately, because separately is how a touch gets logged with no next step or a next step gets set with no touch, and both halves are needed for the follow-up cadence to run. Outcomes: 'connected' you spoke with them · 'left_message' you tried and did not reach them · 'sent' you sent an email or text · 'no_channel' the number or address does not work · 'not_interested' they said no · 'do_not_contact' they asked you to stop. The first four REQUIRE a next date, because a touch with no next step is how a lead dies quietly. The last two close the lead and refuse a next date.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        ref: { type: "string", description: "L-/C-/V- ref or deal name" },
        channel: { type: "string", enum: ["call","email","text","meeting","tour"],
                   description: "how you reached out. Ignored when outcome is no_channel, since nothing was reached." },
        outcome: { type: "string",
                   enum: ["connected","left_message","sent","no_channel","not_interested","do_not_contact"] },
        summary: { type: "string", description: "what happened, in your words. Required: this is the line a future session reads instead of guessing." },
        occurred_at: { type: "string", description: "ISO timestamp of the ACTUAL contact. Omit only when it just happened. Never backfill a past touch with the current time: a false recent date suppresses the staleness alarm the field exists to raise." },
        next_on: { type: "string", description: "YYYY-MM-DD, the next step's date. Required for connected, left_message, sent and no_channel." },
        next_step: { type: "string", description: "what you will do next. Required with next_on." },
        detail: { type: "string" },
        human_quote: { type: "string", description: "their literal words, if worth keeping" } },
        required: ["idempotency_key","ref","outcome","summary"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "log-outreach", args, async () => {
        const OPEN = ["connected","left_message","sent","no_channel"];
        const CLOSING = { not_interested: "closed_lost", do_not_contact: "do_not_contact" };
        const isOpen = OPEN.includes(args.outcome);

        // A touch with no next step is how a lead dies quietly, so the open
        // outcomes refuse to be recorded without one. This is the same posture
        // decline-candidate takes on its reason: the field that makes the record
        // useful is required at the door rather than nagged about afterwards.
        if (isOpen && !(args.next_on && args.next_step))
          throw new ToolError({ error: "next_step_required", outcome: args.outcome,
            hint: "pass next_on (YYYY-MM-DD) and next_step. If there genuinely is no "
                + "next step because they said no, the outcome is 'not_interested', "
                + "not a touch with an empty future." });
        if (!isOpen && (args.next_on || args.next_step))
          throw new ToolError({ error: "closing_outcome_takes_no_next", outcome: args.outcome,
            hint: "this outcome closes the lead; a next step would contradict it" });

        const s = await resolveSubject(c, args.ref);

        // no_channel is NOT a contact and must never move last_touch: nothing was
        // reached. 'note' is is_contact=false in activity_kind, which is exactly
        // the honest record — the attempt happened, the touch did not.
        const KIND = { call: "call", email: "email_out", text: "text",
                       meeting: "meeting", tour: "tour" };
        const kind = args.outcome === "no_channel" ? "note"
                   : (KIND[args.channel] || (args.outcome === "sent" ? "email_out" : "call"));

        const act = await c.query(
          `insert into activity (occurred_at, actor_id, kind, summary, detail, ${FK[s.type]}, source)
         values (coalesce($1::timestamptz, now()), $2, $3, $4, $5, $6, 'stated')
         returning id, occurred_at`,
          [args.occurred_at || null, actor.id, kind,
           `[${args.outcome}] ${args.summary}`, args.detail || null, s.id]);

        // COMPLETING THE OPEN BALL IS THE POINT, not a side effect. The cadence
        // engine fires on next_action.status='done' and has spawned 0 actions ever
        // because complete-action has been called exactly ONCE in the system's
        // history. Wiring completion into the verb a human actually reaches for
        // after a call is what starts that engine, with no change to the engine.
        // Only the caller's own ball, exactly like complete-action: the partner's
        // stays untouched.
        const done = await c.query(
          `update next_action set status='done', updated_by=$1
          where subject_type=$2 and subject_id=$3 and owner_id=$1 and status='open'
          returning id, description`, [actor.id, s.type, s.id]);
        const postCallDone = s.type === "deal" ? (await c.query(
          `update capture_post_call_action
            set status='done',updated_at=now(),completed_at=now()
          where deal_id=$1 and owner_id=$2 and status='open'
          returning id,description,due_on /* capture:outreach-complete-post-call-actions */`,
          [s.id, actor.id])).rows : [];

        let nextId = null, closed = null;
        if (isOpen) {
          const n = await c.query(
            `insert into next_action (subject_type, subject_id, owner_id, description,
                                    due_on, created_by)
           values ($1,$2,$3,$4,$5::date,$3) returning id`,
            [s.type, s.id, actor.id, args.next_step, args.next_on]);
          nextId = n.rows[0].id;
        } else if (s.type === "lead") {
          // do_not_contact sets the EXISTING suppressed flag as well as the stage.
          // The flag is the mechanical gate every surface already honours; the
          // stage is the human-readable reason. Setting only the stage would leave
          // a future sweep free to pick the person back up, which is the one
          // mistake here that costs more than a lost deal.
          const stage = CLOSING[args.outcome];
          await c.query(
            `update lead set stage=$1, suppressed=$2, updated_by=$3 where id=$4`,
            [stage, args.outcome === "do_not_contact", actor.id, s.id]);
          closed = stage;
        } else {
          throw new ToolError({ error: "closing_outcome_needs_a_lead", subject: s,
            hint: "not_interested and do_not_contact set a LEAD's terminal stage. For a "
                + "client or a deal, the outcome belongs on the deal itself via update-deal." });
        }

        await writeEvent(c, actor, "log-outreach", s.type, s.id,
          { new: { activity: act.rows[0].id, outcome: args.outcome, kind,
                   completed: done.rows[0]?.id || postCallDone[0]?.id || null,
                   completed_post_call_action_ids: postCallDone.map(row => row.id),
                   next_action: nextId, stage: closed },
            human_quote: args.human_quote, idempotency_key: args.idempotency_key });

        const completedActions = [
          ...done.rows.map(row => ({ id: row.id, description: row.description, source: "next_action" })),
          ...postCallDone.map(row => ({ id: row.id, description: row.description, source: "post_call_action" })),
        ];

        return { ok: true, subject: s, activity_id: act.rows[0].id,
                 occurred_at: act.rows[0].occurred_at, outcome: args.outcome,
                 completed_action: done.rows[0]?.description || postCallDone[0]?.description || null,
                 completed_actions: completedActions,
                 completed_post_call_action_ids: postCallDone.map(row => row.id),
                 next_action_id: nextId, next_on: args.next_on || null,
                 stage: closed,
                 note: completedActions.length
                   ? "your open ball on this subject was completed, which is what feeds the follow-up cadence"
                   : "no open ball of yours existed on this subject; nothing to complete" };
      }),
    },

  // [loop #383] Found by the 2026-08-14 health audit: L-118 and L-135 are live
    // clients stuck on lead_stage 'nurture_drip' mid-deal, still on the receiving
    // end of the prospecting newsletter, because nothing in the 89-verb registry
    // could move an EXISTING lead's stage. new-lead and promote-pool are
    // creation-only. log-outreach's disposition step touches lead.stage too, but
    // only on the two CLOSING outcomes (not_interested -> closed_lost,
    // do_not_contact -> do_not_contact) — there was no way to move a lead FORWARD
    // through its own funnel, or to correct one an import or a stuck drip left
    // behind. update-lead is that writer.
    "claim-lead": {
      discoveryOrder: 42, serialization: "idempotency-key",
      write: true, humanOnly: true,
      description: "Claim an unowned New lead for the authenticated human. Preserves stage, checks the current version and exact lifecycle links, and records the ownership change.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, expected_actor: { type: "string", minLength: 1 }, lead: { type: "string" }, base_version: { type: "integer" }
      }, required: ["idempotency_key","lead","base_version","expected_actor"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "claim-lead", args, async () => {
        if (args.expected_actor && args.expected_actor !== actor.slug) throw new ToolError({ error: "account_changed" });
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: "human_confirmation_required" });
        const subject = await resolveSubject(c, args.lead);
        if (subject.type !== "lead") throw new ToolError({ error: "not_a_lead" });
        await versionGuard(c, "lead", subject.id, args.base_version);
        const { current: row } = await lockLeadLifecycle(c, subject.id);
        if (!row || !row.live_party || row.stage !== "new" || !row.contact_eligible || row.owner_id || row.client_id || row.is_client || row.linked_client)
          throw new ToolError({ error: "lead_not_claimable" });
        const owner = (await c.query("select id,slug,display_name from actor where slug=$1 and active", [partnerAuthoritySlugForActor(actor)])).rows[0];
        if (!owner) throw new ToolError({ error: "human_owner_unavailable" });
        await c.query("update lead set owner_id=$1,owner_label=$2,updated_by=$3 where id=$4", [owner.id, owner.display_name, actor.id, subject.id]);
        await writeEvent(c, actor, "claim-lead", "lead", subject.id, { field: "owner_id", old: { owner_id: null },
          new: { owner_id: owner.id }, idempotency_key: args.idempotency_key });
        return { ok: true, lead_id: subject.id, owner: owner.slug };
      }),
    },

    "link-lead-client": {
      discoveryOrder: 43, serialization: "idempotency-key",
      write: true, humanOnly: true,
      description: "Confirm one lead belongs to an existing client, by exact IDs and an explicit human choice. Records the client pointer without merging parties or creating a client/deal. Refuses suppression, stale versions and an already linked lead.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, expected_actor: { type: "string", minLength: 1 }, lead: { type: "string" }, base_version: { type: "integer" },
        client_id: { type: "string" }, confirmed: { type: "boolean", const: true }
      }, required: ["idempotency_key","lead","base_version","client_id","confirmed","expected_actor"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "link-lead-client", args, async () => {
        if (args.expected_actor && args.expected_actor !== actor.slug) throw new ToolError({ error: "account_changed" });
        if (!canExercisePartnerAuthority(actor) || args.confirmed !== true) throw new ToolError({ error: "human_confirmation_required" });
        const subject = await resolveSubject(c, args.lead);
        if (subject.type !== "lead") throw new ToolError({ error: "not_a_lead" });
        await versionGuard(c, "lead", subject.id, args.base_version);
        const { current, target } = await lockLeadLifecycle(c, subject.id, args.client_id);
        if (!current || !current.live_party || current.is_client || !current.contact_eligible || current.client_id || current.linked_client)
          throw new ToolError({ error: "lead_not_linkable" });
        if (!target) throw new ToolError({ error: "client_not_found" });
        await c.query("update lead set client_id=$1,updated_by=$2 where id=$3", [target.id, actor.id, subject.id]);
        await writeEvent(c, actor, "link-lead-client", "lead", subject.id, { field: "client_id",
          old: { client_id: null }, new: { client_id: target.id }, idempotency_key: args.idempotency_key });
        return { ok: true, lead_id: subject.id, client_id: target.id };
      }),
    },

    "update-lead": {
      discoveryOrder: 44, serialization: "idempotency-key",
      write: true,
      description: "Field-level change to a lead (score, score_reason, owner, stage, lane, segment, source_type, source_detail, suppressed, est_lease_event, next_action_date, notes_path, notes, event_source, event_confidence, report_back_due, drip_campaign, drip_added, sf_deal). stage and lane are FOREIGN KEYS into lead_stage/lead_lane; a wrong slug comes back with the full valid list rather than a bare internal error. do_not_contact is inseparable from suppressed=true, and only a human may clear an existing suppression instruction. base_version required from a fresh read; a conflict means someone else wrote — surface it to the human, never auto-retry. party_id (identity) and client_id (the lead-to-client conversion pointer) are deliberately absent from fields: neither is a field edit through this verb, the same posture update-deal takes on client_id and update-party-contact takes on identity fields generally (rule 5d44d3f3) — a discrepancy there is a different kind of correction, not a value to overwrite in place.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, expected_actor: { type: "string", minLength: 1 }, lead: { type: "string" },
        base_version: { type: "integer" },
        stage_review: { type: "object", additionalProperties: false, properties: { reason: { type: "string", minLength: 1, maxLength: 1000 }, evidence_ids: { type: "array", items: { type: "string" }, maxItems: 20 }, undo_event_id: { type: "string" }, human_quote: { type: "string", maxLength: 1000 } }, required: ["reason", "evidence_ids"] },
        fields: { type: "object", properties: LEAD_SCORE_FIELDS, description: "subset of: score, score_reason, owner, stage, lane, segment, source_type, source_detail, suppressed, est_lease_event, next_action_date, notes_path, notes, event_source, event_confidence, report_back_due, drip_campaign, drip_added, sf_deal" } },
        required: ["idempotency_key","lead","base_version","fields"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "update-lead", args, async () => {
        validateLeadScoreFields(args.fields);
        const reviewed = Object.hasOwn(args, "stage_review") ? validateStageReview(args.stage_review, args.fields, ToolError) : null;
        if (reviewed?.undo_event_id) {
          if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: "human_confirmation_required" });
          if (!reviewed.human_quote?.trim()) throw new ToolError({ error: "undo_human_quote_required" });
        }
        if (args.expected_actor && args.expected_actor !== actor.slug) throw new ToolError({ error: "account_changed" });
        const s = await resolveSubject(c, args.lead);
        if (s.type !== "lead") throw new ToolError({ error: "not_a_lead", resolved: s });
        await versionGuard(c, "lead", s.id, args.base_version);
        const allowed = ["stage","lane","segment","source_type","source_detail","suppressed",
                         "est_lease_event","next_action_date","notes_path","notes","event_source",
                         "event_confidence","report_back_due","drip_campaign","drip_added","sf_deal","score","score_reason","owner"];
        const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
        if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed });
        // Pre-validate rather than letting the FK abort the transaction, same reason
        // new-lead checks stage/lane up front: once the violation fires the
        // transaction is poisoned and cannot even run the query that would list the
        // valid slugs, so the caller gets nothing to correct with.
        for (const [field, table] of [["stage", "lead_stage"], ["lane", "lead_lane"]]) {
          if (!keys.includes(field) || args.fields[field] === null) continue;
          const hit = await c.query(`select 1 from ${table} where slug=$1`, [args.fields[field]]);
          if (!hit.rows.length) {
            const all = await c.query(`select slug from ${table} order by slug`);
            throw new ToolError({ error: `unknown_${field}`, got: args.fields[field],
              valid: all.rows.map(x => x.slug),
              hint: `${field} is a foreign key into ${table}; pass one of the listed slugs, never the label.` });
          }
        }
        const current = (await c.query("select stage,suppressed from lead where id=$1", [s.id])).rows[0];
        const nextStage = keys.includes("stage") ? args.fields.stage : current.stage;
        const nextSuppressed = keys.includes("suppressed") ? args.fields.suppressed : current.suppressed;
        if (keys.includes("stage") && (current.stage === "archived" || nextStage === "archived") && !canExercisePartnerAuthority(actor))
          throw new ToolError({error:"archive_requires_partner"});
        if (nextStage === "do_not_contact" && nextSuppressed !== true) {
          throw new ToolError({ error: "do_not_contact_requires_suppression",
            hint: "do_not_contact is a standing instruction, not only a funnel label; pass stage='do_not_contact' and suppressed=true together" });
        }
        if (current.stage === "do_not_contact" && nextStage !== "do_not_contact" && nextSuppressed !== false) {
          throw new ToolError({ error: "suppression_clear_required",
            hint: "moving a do_not_contact lead requires the same explicit human correction to set suppressed=false" });
        }
        if (current.suppressed && nextSuppressed === false && !canExercisePartnerAuthority(actor)) {
          throw new ToolError({ error: "suppression_clear_requires_human",
            hint: "a standing suppression instruction may be cleared only by an authenticated human" });
        }
        let stageReview = null;
        if (reviewed) {
          const ids = reviewed.evidence_ids;
          const evidence = ids.length ? (await c.query(
            "select id,occurred_at,kind,connected from activity where lead_id=$1 and id=any($2::uuid[]) and occurred_at<=now()", [s.id, ids])).rows : [];
          if (evidence.length !== ids.length) throw new ToolError({ error: "stage_evidence_mismatch" });
          if (args.fields.stage === "engaged" && evidence.some(row => ["call","text"].includes(row.kind) && row.connected !== true))
            throw new ToolError({ error: "stage_evidence_not_contact" });
          if (reviewed.undo_event_id) {
            const last = (await c.query(`select * from v_lead_stage_transition
              where lead_id=$1 order by mutation_order desc limit 1`, [s.id])).rows[0];
            if (!last || !last.automatic || last.event_id !== reviewed.undo_event_id.toLowerCase() || last.prior_stage !== args.fields.stage || last.stage !== current.stage)
              throw new ToolError({ error: "undo_changed" });
          }
          stageReview = { ...reviewed, evidence_ids: ids,
            evidence_date: evidence.map(row => new Date(row.occurred_at).toISOString()).sort().at(-1) || null };
        }
        const owner = keys.includes("owner") ? await resolveLeadOwner(c, args.fields.owner) : null;
        const storage = Object.fromEntries(keys.filter(k => k !== "owner").map(k => [k, args.fields[k]]));
        if (owner) Object.assign(storage, { owner_id: owner.owner_id, owner_label: owner.owner_label });
        const columns = Object.keys(storage);
        const old = (await c.query(`select ${columns.join(",")}${owner ? ",(select slug from actor where id=lead.owner_id) as owner" : ""} from lead where id=$1`, [s.id])).rows[0];
        const sets = columns.map((k, i) => `${k}=$${i + 2}`).join(", ");
        await c.query(`update lead set ${sets}, updated_by=$1 where id=$${columns.length + 2}`,
          [actor.id, ...Object.values(storage), s.id]);
        for (const k of keys)
          await writeEvent(c, actor, "update-lead", "lead", s.id,
            { recorded_at_after_lock: k === "stage", field: k, old: { [k]: old[k], ...(k === "owner" ? { owner_id: old.owner_id, owner_label: old.owner_label } : {}) }, new: { [k]: args.fields[k], ...(k === "owner" ? owner : {}), ...(k === "stage" && stageReview ? { stage_review: stageReview } : {}) },
              ...(k === "stage" && stageReview ? { cause: stageReview.undo_event_id ? "human_correction" : undefined,
                human_quote: stageReview.human_quote, agent_rationale: stageReview.reason } : {}), idempotency_key: args.idempotency_key });
        return { ok: true, updated: keys };
      }, { serialized: true }),
    },  };
}
