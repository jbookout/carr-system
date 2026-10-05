import { DEAL_ROOM_FIELDS, assertDealRoomField, stripDealPlaceholders } from "./dealroom.js";
import { RESEARCH_EVIDENCE_SCHEMA, lockDealField, researchEvidence, resolveSubject, stampResearch } from "./verb-support.js";
import { ToolError } from "./tool-error.js";
import { withEnvelope, writeEvent } from "./versioned-write.js";
import { organizationTenantForActor, personalScopeForActor } from "./identity.js";
import { executeRegisteredTool } from "./tool-execution.js";
import { canExercisePartnerAuthority } from "./partner-authority.js";

export async function latestFieldConflict(c, dealId, field, baseEventId) {
  let base = null;
  if (baseEventId !== null && baseEventId !== undefined) {
    const found = await c.query(
      `select recorded_at, id from event
        where id=$1 and subject_type='deal' and subject_id=$2 and field=$3
        /* dealroom:base-event */`,
      [baseEventId, dealId, field],
    );
    if (!found.rows.length)
      throw new ToolError({ error: "invalid_base_event", base_event_id: baseEventId, deal_id: dealId, field });
    base = found.rows[0];
  }

  const newer = await c.query(
    `select e.id as event_id, e.actor_id, a.slug as actor, e.new_value -> $2 as value
       from event e join actor a on a.id=e.actor_id
      where e.subject_type='deal' and e.subject_id=$1 and e.field=$2
        and ($3::timestamptz is null or (e.recorded_at, e.id) > ($3::timestamptz, $4::uuid))
      order by e.recorded_at desc, e.id desc limit 1
      /* dealroom:latest-field-event */`,
    [dealId, field, base?.recorded_at || null, base?.id || null],
  );
  return newer.rows[0] || null;
}

// `provenance` is optional and defaults to empty: revert-deal-field and
// resolve-conflict pass nothing and keep automation semantics, which is what
// they are. patch-deal-field passes the partner's words through when the caller
// carried them (WR-000109), and never synthesises a cause — writeEvent derives
// it from the presence of a verbatim quote, and that derivation is the point.
async function applyDealRoomField(c, actor, dealId, field, value, idempotencyKey, verb, provenance = {}) {
  assertDealRoomField(field, value, ToolError);
  if (field === "operating_state") value = {
    state: value.state,
    reason: value.state === "parked" ? value.reason : null,
    note: value.state === "parked" ? value.note?.trim() || null : null,
  };
  const oldRow = field === "operating_state"
    ? await c.query(
      `select jsonb_build_object('state',operating_state,'reason',parking_reason,'note',parking_note) as value
         from deal where id=$1`, [dealId])
    : await c.query(field === "next_date"
      ? "select next_date::text as value from deal where id=$1"
      : `select ${field} as value from deal where id=$1`, [dealId]);
  if (!oldRow.rows.length) throw new ToolError({ error: "not_found", table: "deal", id: dealId });
  if (field === "owner") {
    // deal.owner is the board cache; deal_participant(role=lead) remains the
    // canonical operating assignment used by documents and the rest of the
    // record layer. A Deal Room change moves both in the same transaction.
    await c.query(
      `update deal_participant set to_at=now()
        where deal_id=$1 and role='lead' and to_at is null
        /* dealroom:close-lead */`, [dealId]);
    if (value) {
      const next = (await c.query("select id from actor where slug=$1 and active", [value])).rows[0];
      if (!next) throw new ToolError({ error: "unknown_owner", owner: value });
      await c.query(
        `insert into deal_participant (deal_id,actor_id,role,set_by)
         values ($1,$2,'lead',$3) /* dealroom:open-lead */`, [dealId, next.id, actor.id]);
    }
  }
  if (field === "operating_state") {
    await c.query(
      `update deal
          set operating_state=$2, parking_reason=$3, parking_note=$4,
              parked_at=case when $2='parked' then now() else null end,
              parked_by=case when $2='parked' then $5::uuid else null end,
              updated_by=$5::uuid
        where id=$1 /* dealroom:apply-operating-state */`,
      [dealId, value.state, value.state === "parked" ? value.reason : null,
       value.state === "parked" ? value.note : null, actor.id],
    );
  } else {
    await c.query(
      `update deal set ${field}=$2, updated_by=$3 where id=$1 /* dealroom:apply-field */`,
      [dealId, value, actor.id],
    );
  }
  await writeEvent(c, actor, verb, "deal", dealId, {
    field,
    // The field lock orders writes by commit. Transaction-start `now()` would
    // let a writer that waited for the lock sort behind the write it replaced.
    recorded_at_after_lock: true,
    old: { [field]: oldRow.rows[0].value },
    new: { [field]: value },
    human_quote: provenance.human_quote || null,
    agent_rationale: provenance.change_reason || null,
    idempotency_key: idempotencyKey,
  });
  // The committed identity of the event this write just made, READ BACK from the
  // record. Nothing here constructs an id: writeEvent's insert carries no
  // `returning` and is shared by every verb in this file, so it is not this
  // verb's to change, and the row is found the way any other row is found.
  //
  // What makes the read exact is the event's own idempotency_key — one intended
  // action, one key, one event row — so it names the row written immediately
  // above and can never name a partner's, which ordering by recorded_at alone
  // could under a concurrent writer. Same table and same ordering the Deal Room
  // already uses to find a cell's newest event (revert-deal-field).
  //
  // Why the answer needs it: the board's ONLY source of a field base was the
  // changes feed, which lags a write by up to a poll. A second, different edit to
  // the same cell inside that window therefore sent a base its own first edit had
  // already superseded, and the server correctly recorded a conflict — between a
  // partner and themselves. With the committed id in the answer the client can
  // advance that cell's base immediately, from the record's own word for it.
  //
  // recorded_at is serialized exactly as the changes feed serializes it
  // (dealroom.js), so the two are directly comparable on the client: a base is
  // only ever moved forward, never back onto an older event.
  const committed = (await c.query(
    `select id, to_jsonb(recorded_at)#>>'{}' as recorded_at from event
      where subject_type='deal' and subject_id=$1 and field=$2 and idempotency_key=$3
      order by recorded_at desc, id desc limit 1 /* dealroom:written-event */`,
    [dealId, field, idempotencyKey]))?.rows?.[0] || null;
  return {
      old_value: oldRow.rows[0].value,
      new_value: value,
      // Null when this write carried no key to find the row by. A client that gets
      // no id simply does not advance its base and waits for the feed, which is
      // exactly the behaviour it had before this existed.
      event_id: committed?.id ?? null,
      event_recorded_at: committed?.recorded_at ?? null,
    };
  }

  export function dealRoomTools() {
    return {
    "get-deal-room": {
      discoveryOrder: 88,
      description: "Read one complete open Deal Room work record: board/workspace and active/parked fields, next actions, notes, critical dates, activity, parties, premises, current economics, documents, and attributed history. Includes salesforce_id and base_version only so a reconciler can make one guarded follow-on write. Placeholder Salesforce fields are structurally excluded.",
      inputSchema: { type: "object", properties: { deal: { type: "string" } }, required: ["deal"] },
      handler: async (c, _actor, args) => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        const deal = await c.query(
          `select b.id, b.name, b.phase, b.owner, b.type, b.market as city, b.segment, b.attention,
                to_jsonb(b.next_date)#>>'{}' as next_date, b.next_step, b.client_ref, b.client_name,
                b.account_client_id, b.account_client_ref, b.account_name, b.account_owner,
                b.market_agent, to_jsonb(b.last_touch)#>>'{}' as last_touch,
                to_jsonb(b.last_review_at)#>>'{}' as last_review_at, b.workspace_kind,
                b.operating_state, b.parking_reason, b.parking_note,
                to_jsonb(b.invoiced_on)#>>'{}' as invoiced_on,
                to_jsonb(b.closed_on)#>>'{}' as closed_on, b.lane, b.outcome,
                (select to_jsonb(pc) from v_deal_room_phase_change pc where pc.deal_id=b.id) as phase_change,
                to_jsonb(b.parked_at)#>>'{}' as parked_at, b.parked_by,
                r.salesforce_id, r.base_version
           from v_deal_room_board b
           join v_deal_reconciliation_read r on r.id=b.id
          where b.id=$1`, [s.id]);
        if (!deal.rows.length) throw new ToolError({ error: "not_found", table: "deal", id: s.id });
        const thread = await c.query(
          "select id, kind, text, actor, to_jsonb(created_at)#>>'{}' as created_at from v_deal_room_note where deal_id=$1 order by created_at desc, id desc",
          [s.id],
        );
        const criticalDates = await c.query(
          "select id, kind, to_jsonb(due_on)#>>'{}' as due_on, note, source, status from v_deal_room_critical_date where deal_id=$1 order by due_on, id",
          [s.id],
        );
        const history = await c.query(
          `select id, to_jsonb(recorded_at)#>>'{}' as recorded_at, actor, verb, field, old_value, new_value
           from v_deal_room_event where subject_id=$1
          order by recorded_at desc, id desc`,
          [s.id],
        );
        const actions = await c.query(
          `select n.id, n.owner, n.description,
                to_jsonb(n.due_on)#>>'{}' as due_on, n.status,
                to_jsonb(n.updated_at)#>>'{}' as updated_at
           from v_deal_room_action n
          where n.deal_id=$1
          order by (n.status='open') desc, n.updated_at desc, n.id desc`, [s.id]);
        const activities = await c.query(
          `select a.id, to_jsonb(a.occurred_at)#>>'{}' as occurred_at, a.actor,
                a.kind, a.summary, a.detail, a.source
           from v_deal_room_activity a
          where a.deal_id=$1 order by a.occurred_at desc, a.id desc limit 50`, [s.id]);
        const participants = await c.query(
          `select dp.role, dp.name, dp.actor, dp.party_id
           from v_deal_room_participant dp
          where dp.deal_id=$1 order by dp.role, name`, [s.id]);
        const premises = await c.query(
          `select pr.id, pr.label, pr.building_name, pr.address, pr.city, pr.state,
                pr.suite, pr.area_amount, pr.area_basis
           from v_deal_room_premises pr
          where pr.deal_id=$1 order by pr.created_at, pr.suite`, [s.id]);
        const negotiation = await c.query(
          `select round_no, side, to_jsonb(proposed_on)#>>'{}' as proposed_on,
                rate_amount, rate_basis, rate_norm_sf_yr, ti_amount, ti_basis,
                free_rent_months, term_months, escalator, opex_note,
                to_jsonb(expires_on)#>>'{}' as expires_on, note, source
           from v_deal_room_negotiation where deal_id=$1
          order by round_no desc, proposed_on desc limit 6`, [s.id]);
        const documents = await c.query(
          `select d.id, d.sent_status, d.lint_passed, d.leak_check_passed,
                to_jsonb(d.prepared_at)#>>'{}' as prepared_at, d.note
           from v_deal_room_document d where d.deal_id=$1 order by d.prepared_at desc limit 20`, [s.id]);
        // Only the current sourced lease belongs on an obligation timeline.
        // Never derive rent commencement or an option date from a term or note.
        const lease = await c.query(
          `select id, version, status, to_jsonb(executed_on)#>>'{}' as executed_on,
                to_jsonb(commencement_on)#>>'{}' as commencement_on,
                to_jsonb(expiration_on)#>>'{}' as expiration_on,
                options_note, evidence_kind, evidence_ref, source
           from v_deal_room_current_lease where deal_id=$1`, [s.id]);
        return stripDealPlaceholders({ schema_version: "deal-timeline.v1", lease: lease.rows[0] || null,
          deal_id: s.id, ...deal.rows[0], thread: thread.rows,
          critical_dates: criticalDates.rows, next_actions: actions.rows,
          activities: activities.rows, participants: participants.rows,
          premises: premises.rows, negotiation_rounds: negotiation.rows,
          documents: documents.rows, events: history.rows });
      },
    },

    "read-deal-reconciliation": {
      discoveryOrder: 89,
      description: "Read the minimal all-deal reconciliation record. Use after update-deal closes a deal, because the Deal Room board intentionally contains only open deals. Returns the Salesforce reconciliation key, current base_version, and close-state fields; it never exposes Salesforce placeholders or source_row.",
      inputSchema: { type: "object", properties: { deal: { type: "string" } }, required: ["deal"] },
      handler: async (c, _actor, args) => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        const r = await c.query(
          `select id, name, salesforce_id, base_version, phase, outcome,
                to_jsonb(closed_on)#>>'{}' as closed_on,
                to_jsonb(invoiced_on)#>>'{}' as invoiced_on, lane
           from v_deal_reconciliation_read where id=$1`, [s.id]);
        if (!r.rows.length) throw new ToolError({ error: "not_found", table: "deal", id: s.id });
        return r.rows[0];
      },
    },

    "presence-lease": {
      discoveryOrder: 90,
      write: true,
      description: "Acquire or refresh this actor's field-level Deal Room presence for about three seconds. Expiry is read-time only and presence never enters event history.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" }, field: { type: "string" },
      }, required: ["idempotency_key", "deal", "field"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "presence-lease", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        if (typeof args.field !== "string" || !args.field.trim())
          throw new ToolError({ error: "field_required" });
        const lease = await c.query(
          `insert into deal_presence_lease (actor_id, deal_id, field, expires_at)
         values ($1,$2,$3,now() + interval '3 seconds')
         on conflict (actor_id, deal_id, field)
         do update set expires_at=excluded.expires_at
         returning expires_at /* dealroom:presence-upsert */`,
          [actor.id, s.id, args.field],
        );
        return { ok: true, deal_id: s.id, field: args.field, expires_at: lease.rows[0].expires_at };
      }),
    },

    "patch-deal-field": {
      discoveryOrder: 91,
      write: true,
      description: "Patch one Deal Room cell using its last-seen event as the field base. Only a newer event for this exact deal+field conflicts; other fields never block it.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        field: { type: "string", enum: DEAL_ROOM_FIELDS }, value: {},
        base_event_id: { anyOf: [{ type: "string" }, { type: "null" }] },
        change_reason: { type: "string", description: "why this cell changed; lands on the event as agent_rationale" },
        human_quote: { type: "string", description: "the partner's verbatim words, when they directed the change" },
      }, required: ["idempotency_key", "deal", "field", "value", "base_event_id"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "patch-deal-field", args, async () => {
        assertDealRoomField(args.field, args.value, ToolError);
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        await lockDealField(c, s.id, args.field);
        const intervening = await latestFieldConflict(c, s.id, args.field, args.base_event_id);
        if (intervening) {
          const made = await c.query(
            `insert into deal_conflict
             (deal_id, field, value_a, actor_a, event_a, value_b, actor_b)
           values ($1,$2,$3::jsonb,$4,$5,$6::jsonb,$7)
           returning id, status /* dealroom:create-conflict */`,
            [s.id, args.field, JSON.stringify(intervening.value), intervening.actor_id,
             intervening.event_id, JSON.stringify(args.value), actor.id],
          );
          return { ok: false, conflict: { id: made.rows[0].id, status: made.rows[0].status,
            deal_id: s.id, field: args.field,
            value_a: intervening.value, actor_a: intervening.actor, event_a: intervening.event_id,
            value_b: args.value, actor_b: actor.slug } };
        }
        const applied = await applyDealRoomField(c, actor, s.id, args.field, args.value,
          args.idempotency_key, "patch-deal-field",
          { change_reason: args.change_reason, human_quote: args.human_quote });
        return { ok: true, deal_id: s.id, field: args.field, ...applied };
      }),
    },

    "add-deal-note": {
      discoveryOrder: 92,
      write: true,
      description: "Append context or an answer to a deal's immutable thread. Existing rows are never edited or deleted.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" }, text: { type: "string" },
      }, required: ["idempotency_key", "deal", "text"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "add-deal-note", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        if (typeof args.text !== "string" || !args.text.trim()) throw new ToolError({ error: "text_required" });
        const note = await c.query(
          "insert into deal_note (deal_id, kind, text, actor_id) values ($1,'note',$2,$3) returning id, created_at /* dealroom:add-note */",
          [s.id, args.text.trim(), actor.id],
        );
        await writeEvent(c, actor, "add-deal-note", "deal", s.id, {
          field: "note", new: { note: args.text.trim() }, idempotency_key: args.idempotency_key,
        });
        return { ok: true, deal_id: s.id, note_id: note.rows[0].id, created_at: note.rows[0].created_at };
      }),
    },

    "set-next-step": {
      discoveryOrder: 93,
      write: true,
      description: "Append a new current next step. The prior step remains unchanged as attributed history; newest next_step wins.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" }, text: { type: "string" },
        next_date: { anyOf: [{ type: "string" }, { type: "null" }] },
      }, required: ["idempotency_key", "deal", "text"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-next-step", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        if (typeof args.text !== "string" || !args.text.trim()) throw new ToolError({ error: "text_required" });
        assertDealRoomField("next_date", args.next_date ?? null, ToolError);
        // The step also changes next_date. Take that cell's lock first so its
        // field history cannot race a direct date edit or undo.
        await lockDealField(c, s.id, "next_date");
        await lockDealField(c, s.id, "next_step");
        const oldDate = (await c.query("select next_date::text as next_date from deal where id=$1", [s.id])).rows[0]?.next_date ?? null;
        const prior = await c.query(
          "select id, text from deal_note where deal_id=$1 and kind='next_step' order by created_at desc, id desc limit 1 /* dealroom:current-step */",
          [s.id],
        );
        const note = await c.query(
          "insert into deal_note (deal_id, kind, text, actor_id, created_at) values ($1,'next_step',$2,$3,clock_timestamp()) returning id, to_jsonb(created_at)#>>'{}' as created_at /* dealroom:add-next-step */",
          [s.id, args.text.trim(), actor.id],
        );
        await c.query(
          "update deal set next_date=$2, updated_by=$3 where id=$1 /* dealroom:set-next-date */",
          [s.id, args.next_date ?? null, actor.id],
        );
        // The Deal Room's next step and the operating system's next action are
        // one fact, not parallel lists. Each partner still keeps one ball of
        // their own on the deal; replacing yours drops (does not complete) it.
        await c.query(
          `update next_action set status='dropped', updated_by=$1
          where subject_type='deal' and subject_id=$2 and owner_id=$1 and status='open'
          /* dealroom:drop-prior-action */`, [actor.id, s.id]);
        const action = await c.query(
          `insert into next_action (subject_type, subject_id, owner_id, due_on,
                                  description, created_by, updated_by)
         values ('deal',$1,$2,$3,$4,$2,$2) returning id
         /* dealroom:add-next-action */`,
          [s.id, actor.id, args.next_date ?? null, args.text.trim()]);
        await writeEvent(c, actor, "set-next-step", "deal", s.id, {
          field: "next_step",
          old: { next_step: prior.rows[0]?.text ?? null },
          new: { next_step: args.text.trim(), next_date: args.next_date ?? null },
          recorded_at_after_lock: true,
          idempotency_key: args.idempotency_key,
        });
        await writeEvent(c, actor, "set-next-step", "deal", s.id, {
          field: "next_date",
          old: { next_date: oldDate },
          new: { next_date: args.next_date ?? null },
          recorded_at_after_lock: true,
          idempotency_key: args.idempotency_key,
        });
        return { ok: true, deal_id: s.id, next_step_id: note.rows[0].id,
          next_action_id: action.rows[0].id,
          supersedes: prior.rows[0]?.id ?? null, created_at: note.rows[0].created_at };
      }),
    },

    "start-deal-review": {
      discoveryOrder: 94,
      write: true,
      description: "Start a Team Book or national-account agenda. One partner may have one open session per workspace/account; the other partner can run their own independently.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        workspace_kind: { type: "string", enum: ["team","national_account"] },
        account_client_id: { type: "string" },
      }, required: ["idempotency_key","workspace_kind"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "start-deal-review", args, async () => {
        const accountId = args.account_client_id || null;
        if (args.workspace_kind === "national_account" && !accountId)
          throw new ToolError({ error: "account_required" });
        if (args.workspace_kind === "team" && accountId)
          throw new ToolError({ error: "team_review_has_no_account" });
        if (accountId) {
          const valid = await c.query("select 1 from client where id=$1 and client_type='national_account'", [accountId]);
          if (!valid.rows.length) throw new ToolError({ error: "not_a_national_account", account_client_id: accountId });
        }
        const existing = await c.query(
          `select id from deal_review_session where started_by=$1 and workspace_kind=$2
          and account_client_id is not distinct from $3::uuid and status='open'`,
          [actor.id, args.workspace_kind, accountId]);
        if (existing.rows.length) return { ok: true, session_id: existing.rows[0].id, already_open: true };
        const made = await c.query(
          `insert into deal_review_session (workspace_kind,account_client_id,started_by)
         values ($1,$2,$3) returning id,to_jsonb(started_at)#>>'{}' as started_at`,
          [args.workspace_kind, accountId, actor.id]);
        await writeEvent(c, actor, "start-deal-review", "actor", actor.id, {
          new: { session_id: made.rows[0].id, workspace_kind: args.workspace_kind,
            account_client_id: accountId }, idempotency_key: args.idempotency_key });
        return { ok: true, session_id: made.rows[0].id, started_at: made.rows[0].started_at };
      }),
    },

    "review-deal": {
      discoveryOrder: 95,
      write: true,
      description: "Mark one deal reviewed or skipped in an open agenda. Repeating the action updates the disposition instead of double-counting it.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, session_id: { type: "string" },
        deal: { type: "string" }, disposition: { type: "string", enum: ["reviewed","skipped"] },
        note: { type: "string" },
      }, required: ["idempotency_key","session_id","deal","disposition"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "review-deal", args, async () => {
        const session = (await c.query(
          "select * from deal_review_session where id=$1 and started_by=$2 and status='open' for update",
          [args.session_id, actor.id])).rows[0];
        if (!session) throw new ToolError({ error: "review_session_not_open" });
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        const membership = await c.query(
          `select workspace_kind,account_client_id from v_deal_room_board where id=$1`, [s.id]);
        const row = membership.rows[0];
        if (!row || row.workspace_kind !== session.workspace_kind ||
            String(row.account_client_id || '') !== String(session.account_client_id || ''))
          throw new ToolError({ error: "deal_outside_review_workspace", deal_id: s.id });
        await c.query(
          `insert into deal_review_item (session_id,deal_id,disposition,note)
         values ($1,$2,$3,$4)
         on conflict (session_id,deal_id) do update
         set disposition=excluded.disposition,note=excluded.note,reviewed_at=now()`,
          [session.id, s.id, args.disposition, args.note || null]);
        return { ok: true, session_id: session.id, deal_id: s.id, disposition: args.disposition };
      }),
    },

    "end-deal-review": {
      discoveryOrder: 96,
      write: true,
      description: "Complete or abandon an open agenda and return its reviewed/skipped counts. Completed sessions become the workspace's last-review clock.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, session_id: { type: "string" },
        status: { type: "string", enum: ["completed","abandoned"], default: "completed" },
        summary: { type: "string" },
      }, required: ["idempotency_key","session_id"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "end-deal-review", args, async () => {
        const status = args.status || "completed";
        const closed = await c.query(
          `update deal_review_session set status=$3,summary=$4,ended_at=now()
          where id=$1 and started_by=$2 and status='open'
          returning id,workspace_kind,account_client_id,to_jsonb(ended_at)#>>'{}' as ended_at`,
          [args.session_id, actor.id, status, args.summary || null]);
        if (!closed.rows.length) throw new ToolError({ error: "review_session_not_open" });
        const counts = (await c.query(
          `select count(*) filter (where disposition='reviewed')::int as reviewed,
                count(*) filter (where disposition='skipped')::int as skipped
           from deal_review_item where session_id=$1`, [args.session_id])).rows[0];
        await writeEvent(c, actor, "end-deal-review", "actor", actor.id, {
          new: { session_id: args.session_id, status, ...counts },
          idempotency_key: args.idempotency_key });
        return { ok: true, ...closed.rows[0], ...counts, status };
      }),
    },

    "set-market-agent": {
      discoveryOrder: 97,
      write: true,
      description: "Set the stated local-market agent on a national-account deal. The readable name is stored as stated; an optional party id is linked only when already verified.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        agent_name: { type: "string" }, agent_party_id: { type: "string" },
        market: { type: "string" }, source: { type: "string" },
      }, required: ["idempotency_key","deal","agent_name","source"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-market-agent", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        const account = await c.query("select account_client_id from v_deal_room_board where id=$1", [s.id]);
        if (!account.rows[0]?.account_client_id)
          throw new ToolError({ error: "not_a_national_account_deal" });
        const old = (await c.query("select agent_name,market from deal_market_assignment where deal_id=$1", [s.id])).rows[0] || null;
        await c.query(
          `insert into deal_market_assignment (deal_id,agent_name,agent_party_id,market,source,set_by)
         values ($1,$2,$3,$4,$5,$6)
         on conflict (deal_id) do update set agent_name=excluded.agent_name,
           agent_party_id=excluded.agent_party_id,market=excluded.market,
           source=excluded.source,set_by=excluded.set_by,set_at=now()`,
          [s.id, args.agent_name.trim(), args.agent_party_id || null,
           args.market || null, args.source, actor.id]);
        await writeEvent(c, actor, "set-market-agent", "deal", s.id, {
          field: "market_agent", old, new: { market_agent: args.agent_name.trim(), market: args.market || null },
          idempotency_key: args.idempotency_key });
        return { ok: true, deal_id: s.id, market_agent: args.agent_name.trim() };
      }),
    },

    "set-national-account-owner": {
      discoveryOrder: 98,
      write: true,
      description: "Assign Joe or Dell as the accountable partner for a national-account portfolio without changing individual deal owners.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, account_client_id: { type: "string" },
        owner: { type: "string", enum: ["joe","dell"] },
      }, required: ["idempotency_key","account_client_id","owner"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-national-account-owner", args, async () => {
        const account = await c.query("select id from client where id=$1 and client_type='national_account'", [args.account_client_id]);
        if (!account.rows.length) throw new ToolError({ error: "not_a_national_account" });
        const owner = (await c.query("select id from actor where slug=$1 and active", [args.owner])).rows[0];
        if (!owner) throw new ToolError({ error: "unknown_owner" });
        await c.query(
          `insert into national_account_owner (account_client_id,owner_actor_id,set_by)
         values ($1,$2,$3) on conflict (account_client_id) do update
         set owner_actor_id=excluded.owner_actor_id,set_by=excluded.set_by,set_at=now()`,
          [args.account_client_id, owner.id, actor.id]);
        return { ok: true, account_client_id: args.account_client_id, owner: args.owner };
      }),
    },

    "create-national-account": {
      discoveryOrder: 99,
      write: true,
      description: "Create one national-account parent org/client and assign its accountable partner. It does not create market deals or duplicate a brand that already exists.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, name: { type: "string" },
        owner: { type: "string", enum: ["joe","dell"] }, vertical: { type: "string" },
        force_new: { type: "boolean" },
        research_evidence: RESEARCH_EVIDENCE_SCHEMA,
      }, required: ["idempotency_key","name","owner","research_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "create-national-account", args, async () => {
        const matches = await c.query(
          "select id,name from party where kind='org' and merged_into is null and lower(name)=lower($1)", [args.name.trim()]);
        if (matches.rows.length && !args.force_new)
          return { needs_confirm: true, candidates: matches.rows,
            hint: "An org with this exact name exists. Use its client or explicitly confirm a genuinely separate brand." };
        const evidence = researchEvidence(args.research_evidence,
          ["name", "company", "phone", "specialty", "market"], "create-national-account");
        const org = await c.query(
          `insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id`,
          [args.name.trim(), actor.id]);
        await stampResearch(c, actor, org.rows[0].id, evidence);
        const ref = (await c.query("select 'C-' || lpad(nextval('ref_client_seq')::text,3,'0') as ref")).rows[0].ref;
        const client = await c.query(
          `insert into client (roster_ref,party_id,client_type,vertical,status,
                             acquisition_source,owner_id,owner_label,created_by,updated_by)
         values ($1,$2,'national_account',$3,'engaged','national_account',$4,$5,$4,$4) returning id`,
          [ref, org.rows[0].id, args.vertical || null, actor.id, actor.display]);
        const owner = (await c.query("select id from actor where slug=$1", [args.owner])).rows[0];
        await c.query(
          "insert into national_account_owner (account_client_id,owner_actor_id,set_by) values ($1,$2,$3)",
          [client.rows[0].id, owner.id, actor.id]);
        await writeEvent(c, actor, "create-national-account", "client", client.rows[0].id, {
          new: { ref, name: args.name.trim(), owner: args.owner, client_type: "national_account" },
          idempotency_key: args.idempotency_key });
        return { ok: true, account_client_id: client.rows[0].id, account_client_ref: ref,
          name: args.name.trim(), owner: args.owner };
      }),
    },

    "create-national-market-deal": {
      discoveryOrder: 100,
      write: true,
      description: "Create one market transaction under a national account: reuse or create the named franchisee sub-client under the parent org, then create exactly one deal and optional stated market-agent assignment.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, account_client_id: { type: "string" },
        client_name: { type: "string" }, deal_name: { type: "string" }, market: { type: "string" },
        state: { type: "string" }, segment: { type: "string" }, agent_name: { type: "string" },
        deal_type: { type: "string", default: "startup" },
        phase: { type: "string", default: "pending" },
        research_evidence: RESEARCH_EVIDENCE_SCHEMA,
      }, required: ["idempotency_key","account_client_id","client_name","deal_name","market"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "create-national-market-deal", args, async () => {
        const account = (await c.query(
          `select c.id,c.party_id,p.name from client c join party p on p.id=c.party_id
          where c.id=$1 and c.client_type='national_account' and c.merged_into is null`,
          [args.account_client_id])).rows[0];
        if (!account) throw new ToolError({ error: "not_a_national_account" });
        const duplicate = await c.query(
          "select id,name from deal where outcome is null and lower(name)=lower($1)", [args.deal_name.trim()]);
        if (duplicate.rows.length) throw new ToolError({ error: "deal_name_exists", existing: duplicate.rows });
        let sub = (await c.query(
          `select c.id,c.roster_ref from client c join party p on p.id=c.party_id
          where p.org_id=$1 and p.merged_into is null and c.merged_into is null
            and lower(p.name)=lower($2) limit 1`, [account.party_id, args.client_name.trim()])).rows[0];
        if (!sub) {
          const evidence = researchEvidence(args.research_evidence,
            ["name", "company", "phone", "specialty", "market"], "create-national-market-deal");
          const person = await c.query(
            `insert into party (kind,name,org_id,city,state,created_by,updated_by)
           values ('person',$1,$2,$3,$4,$5,$5) returning id`,
            [args.client_name.trim(), account.party_id, args.market.trim(), args.state || null, actor.id]);
          await stampResearch(c, actor, person.rows[0].id, evidence);
          const ref = (await c.query("select 'C-' || lpad(nextval('ref_client_seq')::text,3,'0') as ref")).rows[0].ref;
          const made = await c.query(
            `insert into client (roster_ref,party_id,client_type,vertical,status,
                               acquisition_source,owner_id,owner_label,created_by,updated_by)
           values ($1,$2,'franchise',$3,'active_deal','national_account',$4,$5,$4,$4) returning id`,
            [ref, person.rows[0].id, args.segment || null, actor.id, actor.display]);
          sub = { id: made.rows[0].id, roster_ref: ref };
        }
        const deal = await c.query(
          `insert into deal (client_id,name,deal_type,phase,segment,city,lane,owner,created_by,updated_by)
         values ($1,$2,$3,$4,$5,$6,'national',$7,$8,$8) returning id`,
          [sub.id, args.deal_name.trim(), args.deal_type || 'startup', args.phase || 'pending',
           args.segment || null, args.market.trim(), actor.slug, actor.id]);
        await c.query(
          `insert into deal_participant (deal_id,actor_id,role,set_by)
         values ($1,$2,'lead',$2)`, [deal.rows[0].id, actor.id]);
        if (args.agent_name?.trim()) await c.query(
          `insert into deal_market_assignment (deal_id,agent_name,market,source,set_by)
         values ($1,$2,$3,'partner stated in Deal Room',$4)`,
          [deal.rows[0].id, args.agent_name.trim(), args.market.trim(), actor.id]);
        await writeEvent(c, actor, "create-national-market-deal", "deal", deal.rows[0].id, {
          new: { name: args.deal_name.trim(), client_ref: sub.roster_ref,
            account_client_id: account.id, market: args.market.trim(), agent_name: args.agent_name || null },
          idempotency_key: args.idempotency_key });
        return { ok: true, deal_id: deal.rows[0].id, client_ref: sub.roster_ref,
          account_client_id: account.id };
      }),
    },

    "revert-deal-field": {
      discoveryOrder: 101,
      write: true,
      description: "Undo one Deal Room field change only when it is still the latest change to that exact field. Newer partner work is never overwritten by undo.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, event_id: { type: "string" },
      }, required: ["idempotency_key","event_id"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "revert-deal-field", args, async () => {
        const scope = personalScopeForActor(actor);
        if (scope.status === "error") throw new ToolError({ error: scope.error });
        const row = (await c.query(
          `select id,subject_id,field,old_value,new_value from event
          where id=$1 and subject_type='deal' and organization_tenant_id=$2
            and (personal_scope='none' or personal_scope=$3)`,
          [args.event_id, organizationTenantForActor(actor),
            scope.status === "personal" ? `${scope.sponsor}-personal` : "none"])).rows[0];
        if (!row || !DEAL_ROOM_FIELDS.includes(row.field) ||
            !Object.prototype.hasOwnProperty.call(row.old_value || {}, row.field))
          throw new ToolError({ error: "event_not_revertible" });
        await lockDealField(c, row.subject_id, row.field);
        const latest = (await c.query(
          `select id from event where subject_type='deal' and subject_id=$1 and field=$2
          order by recorded_at desc,id desc limit 1`, [row.subject_id,row.field])).rows[0];
        if (latest?.id !== row.id)
          throw new ToolError({ error: "newer_change_exists", hint: "Open the deal and review the newer value before changing it." });
        const oldValue = row.old_value?.[row.field] ?? null;
        const applied = await applyDealRoomField(c, actor, row.subject_id, row.field, oldValue,
          args.idempotency_key, "revert-deal-field");
        return { ok: true, deal_id: row.subject_id, field: row.field,
          reverted_event_id: row.id, ...applied };
      }),
    },

    "resolve-conflict": {
      discoveryOrder: 102,
      write: true,
      description: "Resolve an open Deal Room cell conflict only while its recorded field value is still current, by applying value a or b through the normal field patch/event path.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, conflict_id: { type: "string" },
        winner: { type: "string", enum: ["a", "b"] },
      }, required: ["idempotency_key", "conflict_id", "winner"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "resolve-conflict", args, async () => {
        if (!['a', 'b'].includes(args.winner)) throw new ToolError({ error: "invalid_winner", allowed: ["a", "b"] });
        const found = await c.query(
          `select id, deal_id, field, value_a, value_b, event_a, status
           from deal_conflict where id=$1 for update /* dealroom:get-conflict */`,
          [args.conflict_id],
        );
        if (!found.rows.length) throw new ToolError({ error: "not_found", table: "deal_conflict", id: args.conflict_id });
        const conflict = found.rows[0];
        if (conflict.status !== "open") throw new ToolError({ error: "conflict_already_resolved", conflict_id: conflict.id });
        await lockDealField(c, conflict.deal_id, conflict.field);
        if (await latestFieldConflict(c, conflict.deal_id, conflict.field, conflict.event_a))
          throw new ToolError({ error: "newer_change_exists", conflict_id: conflict.id,
            hint: "This field changed again after the conflict appeared. Open the deal and review its current value before deciding." });
        const value = args.winner === "a" ? conflict.value_a : conflict.value_b;
        const applied = await applyDealRoomField(c, actor, conflict.deal_id, conflict.field,
          value, args.idempotency_key, "resolve-conflict");
        await c.query(
          `update deal_conflict set status='resolved', resolved_by=$2, winner=$3,
             resolved_at=now() where id=$1 /* dealroom:resolve-conflict */`,
          [conflict.id, actor.id, args.winner],
        );
        return { ok: true, conflict_id: conflict.id, deal_id: conflict.deal_id,
          field: conflict.field, winner: args.winner, ...applied };
      }),
    },

    "resolve-candidate": {
      discoveryOrder: 103,
      write: true,
      description: "Human gate for one capture proposal. Rejecting only skips it. Accepting invokes its mapped live verb as the confirming partner, then confirms the candidate only after that write returns a real record reference.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, candidate_id: { type: "string" },
        accept: { type: "boolean" }, note: { type: "string" },
      }, required: ["idempotency_key", "candidate_id", "accept"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "resolve-candidate", args, async () => {
        if (typeof args.accept !== "boolean") throw new ToolError({ error: "accept_required" });
        const found = await c.query(
          `select id, kind, payload, status, resulting_ref
           from capture_candidate where id=$1 for update /* capture:resolve-read */`,
          [args.candidate_id]);
        if (!found.rows.length)
          throw new ToolError({ error: "not_found", table: "capture_candidate", id: args.candidate_id });
        const candidate = found.rows[0];
        if (candidate.status !== "pending") return { ok: true, candidate_id: candidate.id,
          already: candidate.status, ref: candidate.resulting_ref || null,
          note: "already dispositioned; nothing changed" };

        if (!args.accept) {
          await c.query(
            `update capture_candidate
              set status='skipped', resolved_by=$2, resolution_note=$3, resolved_at=now()
            where id=$1 /* capture:resolve-skip */`,
            [candidate.id, actor.id, args.note || null]);
          return { ok: true, candidate_id: candidate.id, status: "skipped", ref: null };
        }

        const verbByKind = {
          phase_move: "patch-deal-field",
          next_step: "set-next-step",
          new_deal: "new-deal",
          activity: "log-activity",
          meeting_record: "log-activity",
        };
        const verb = verbByKind[candidate.kind];
        if (!verb) throw new ToolError({ error: "unknown_candidate_kind", kind: candidate.kind });
        const innerArgs = { ...candidate.payload, idempotency_key: `capture:${candidate.id}` };
        if (candidate.kind === "meeting_record") innerArgs.kind = "meeting";
        const result = await executeRegisteredTool(c, actor, verb, innerArgs);
        if (!result || result.ok === false) throw new ToolError(result || { error: "inner_write_failed" });
        const ref = candidate.kind === "phase_move" ? result.deal_id
          : candidate.kind === "next_step" ? result.next_step_id
          : candidate.kind === "new_deal" ? result.deal_id
          : result.activity_id;
        if (!ref) throw new ToolError({ error: "inner_write_missing_ref", verb });
        await c.query(
          `update capture_candidate
            set status='confirmed', resolved_by=$2, resolution_note=$3,
                resulting_ref=$4, resolved_at=now()
          where id=$1 /* capture:resolve-confirm */`,
          [candidate.id, actor.id, args.note || null, String(ref)]);
        return { ok: true, candidate_id: candidate.id, status: "confirmed", ref: String(ref),
          verb, result };
      }),
    },

    "resolve-post-call-candidate": {
      discoveryOrder: 104,
      write: true,
      description: "Human-only resolution for one Call Mode proposal. assigned_action creates a real next action for the explicit Joe or Dell assignee. email_draft only confirms metadata and its local body hash: it never creates or sends an email or Outlook draft.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, candidate_id: { type: "string" },
        accept: { type: "boolean" }, note: { type: "string" },
      }, required: ["idempotency_key", "candidate_id", "accept"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "resolve-post-call-candidate", args, async () => {
        if (!canExercisePartnerAuthority(actor)) throw new ToolError({ error: "human_only",
          hint: "this verb requires a partner or a server-verified Codex/Claude session sponsored by one" });
        if (typeof args.accept !== "boolean") throw new ToolError({ error: "accept_required" });
        const found = await c.query(
          `select id,kind,deal_id,assignee_slug,action_description,due_on,recipient_party_id,
                recipient_ref,email_subject,body_sha256,status,resulting_ref
           from capture_post_call_candidate where id=$1 for update
           /* capture:resolve-post-call-read */`, [args.candidate_id]);
        if (!found.rows.length)
          throw new ToolError({ error: "not_found", table: "capture_post_call_candidate", id: args.candidate_id });
        const candidate = found.rows[0];
        if (candidate.status !== "pending") return { ok: true, candidate_id: candidate.id,
          already: candidate.status, ref: candidate.resulting_ref || null,
          note: "already dispositioned; nothing changed" };
        if (!args.accept) {
          await c.query(
            `update capture_post_call_candidate
              set status='skipped',resolved_by=$2,resolution_note=$3,resolved_at=now()
            where id=$1 /* capture:resolve-post-call-skip */`,
            [candidate.id, actor.id, args.note || null]);
          return { ok: true, candidate_id: candidate.id, status: "skipped", ref: null };
        }
        if (candidate.kind === "email_draft") {
          await c.query(
            `update capture_post_call_candidate
              set status='confirmed',resolved_by=$2,resolution_note=$3,resolved_at=now()
            where id=$1 /* capture:resolve-post-call-email */`,
            [candidate.id, actor.id, args.note || null]);
          await writeEvent(c, actor, "resolve-post-call-candidate", "deal", candidate.deal_id, {
            field: "email_draft_metadata",
            new: { candidate_id: candidate.id, recipient_ref: candidate.recipient_ref,
              subject: candidate.email_subject, body_sha256: candidate.body_sha256, approved: true },
            idempotency_key: args.idempotency_key,
          });
          return { ok: true, candidate_id: candidate.id, status: "confirmed", ref: null,
            local_only: true, send: false };
        }
        if (candidate.kind !== "assigned_action")
          throw new ToolError({ error: "unknown_candidate_kind", kind: candidate.kind });
        const assignee = await c.query(
          "select id from actor where slug=$1 and active /* capture:resolve-post-call-assignee */",
          [candidate.assignee_slug]);
        if (!assignee.rows.length)
          throw new ToolError({ error: "assignee_not_provisioned", assignee: candidate.assignee_slug });
        const action = await c.query(
          `insert into capture_post_call_action (candidate_id,deal_id,owner_id,due_on,description,accepted_by)
         values ($1,$2,$3,$4,$5,$6) returning id
         /* capture:resolve-post-call-action */`,
          [candidate.id, candidate.deal_id, assignee.rows[0].id, candidate.due_on || null,
           candidate.action_description, actor.id]);
        await c.query(
          `update capture_post_call_candidate
            set status='confirmed',resolved_by=$2,resolution_note=$3,resulting_ref=$4,resolved_at=now()
          where id=$1 /* capture:resolve-post-call-confirm */`,
          [candidate.id, actor.id, args.note || null, String(action.rows[0].id)]);
        await writeEvent(c, actor, "resolve-post-call-candidate", "deal", candidate.deal_id, {
          field: "next_action", new: { next_action_id: action.rows[0].id,
            assignee: candidate.assignee_slug, description: candidate.action_description,
            due_on: candidate.due_on || null }, idempotency_key: args.idempotency_key,
        });
        return { ok: true, candidate_id: candidate.id, status: "confirmed",
          ref: String(action.rows[0].id), assignee: candidate.assignee_slug };
      }),
    },
  };
}
