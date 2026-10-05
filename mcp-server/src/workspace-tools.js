import { LEAD_WORKSPACE_SCHEMA, readLeadWorkspace } from "./lead-workspace.js";
import { LOOP_KINDS } from "./verb-support.js";
import { personalScopeForActor } from "./identity.js";
import { ToolError } from "./tool-error.js";
import { executeRegisteredTool } from "./tool-execution.js";
import { DEAL_ROOM_FIELDS } from "./dealroom.js";

// read-loop's amendment attachment. loop_amendment_history() (migration 0702)
// is a SECURITY DEFINER function so carr_reader can call it without a
// base-table grant on loop_amendment — read-loop runs on the reader
// connection like every other write:false verb, and the append-only table's
// grant stays exactly select+insert to carr_writer, nothing wider.
async function withAmendmentHistory(client, loop) {
  const r = await client.query(
    `select id, prior_outcome, new_outcome, prior_resolution, new_resolution,
            reason, actor, to_jsonb(created_at)#>>'{}' as created_at
       from loop_amendment_history($1)`, [loop.loop_id]);
  return { loop, amended: r.rows.length > 0, amendments: r.rows };
}

export function workspaceTools() {
  return {
    "today-triage": {
      discoveryOrder: 8,
      write: false,
      description: "What needs attention now: due next-actions (next_action.due_on, suppressed by hold_until), critical_date rows inside 14 days, untriaged ingest items. The morning-brief substrate.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => ({ items: (await c.query("select * from v_today_triage order by due_on nulls last limit 50")).rows }),
    },

    "morning-brief": {
      discoveryOrder: 9,
      write: false,
      description: "The record-native morning brief for the authenticated Joe or Dell context. It composes live triage, claim-card, deal-room, loop-board, and the redacted renewal decision queue. Every section reports ready, empty, or unavailable; unavailable is never rewritten as empty. Takes no audience, sponsor, or partner argument.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c, actor, args) => {
        // This is an audience boundary, not a convenience filter.  A shared-only
        // runtime cannot choose Joe or Dell by argument, model name, or cwd.
        const scope = personalScopeForActor(actor);
        if (scope.status !== "personal")
          throw new ToolError({ error: "morning_brief_requires_partner_scope",
            hint: "Reconnect through a verified Joe- or Dell-sponsored context; this brief never accepts a caller-selected audience." });
        if (Object.keys(args || {}).length)
          throw new ToolError({ error: "morning_brief_context_not_selectable" });

        // A section returns a deliberately tiny error shape.  Driver errors can
        // include connection or relation details, so returning them would turn a
        // freshness signal into a disclosure.  Empty is reserved for a completed
        // read with zero rows; a missing/stale source is unavailable.
        const section = async (load) => {
          try {
            const value = await load();
            const items = Array.isArray(value.items) ? value.items : [];
            return { state: items.length ? "ready" : "empty", ...value, items };
          } catch {
            return { state: "unavailable", reason: "source_unavailable", items: [] };
          }
        };
        const ownOrShared = (row) => !row?.owner || String(row.owner).toLowerCase() === scope.sponsor;

        const today = await section(async () => {
          const value = await executeRegisteredTool(c, actor, "today-triage", {});
          return { items: value.items.filter(ownOrShared) };
        });
        const claimCard = await section(async () => {
          const value = await executeRegisteredTool(c, actor, "claim-card", { limit: 5, include_needs_contact: false });
          return { items: value.candidates, claimable: value.claimable,
            needs_contact_count: value.needs_contact_count };
        });
        const deals = await section(async () => {
          const value = await executeRegisteredTool(c, actor, "deal-room-board", { workspace: "all" });
          // deal-room-board is shared by design; the personal morning composite is
          // not.  Account ownership is therefore filtered from authenticated
          // sponsor scope exactly as deal ownership is, never from caller input.
          return { items: value.deals.filter(ownOrShared),
            accounts: value.accounts.filter((row) => String(row?.account_owner || "").toLowerCase() === scope.sponsor) };
        });
        const loops = await section(async () => {
          const value = await executeRegisteredTool(c, actor, "loop-board",
            { kind: "open_loop", status: "open", owner: scope.sponsor, limit: 60 });
          return { items: value.loops };
        });
        const renewals = await section(async () => {
          const status = await c.query(
            "select t1_candidate_count, source_observed_at, freshness_state from v_renewal_decision_queue_status where owner_slug=$1",
            [scope.sponsor]);
          if (status.rows.length !== 1)
            return { state: "unavailable", reason: "source_unavailable", items: [] };
          if (status.rows[0].freshness_state === "empty")
            return { state: "empty", items: [], t1_candidate_count: 0,
              source_observed_at: status.rows[0].source_observed_at, freshness_state: "empty" };
          if (status.rows[0].freshness_state !== "ready")
            return { state: "unavailable", reason: "source_unavailable", items: [] };
          const rows = await c.query(
            `select display_name, org_name, vertical, city, county, state, est_lease_event,
                  tier_status, flag_status, has_channel, decision_count, source_observed_at,
                  freshness_state
             from v_renewal_decision_queue
            where owner_slug=$1
            order by est_lease_event nulls last, display_name
            limit 20`, [scope.sponsor]);
          return { items: rows.rows, t1_candidate_count: status.rows[0].t1_candidate_count,
            source_observed_at: status.rows[0].source_observed_at,
            freshness_state: status.rows[0].freshness_state };
        });
        // Only the immutable source-run states may reach section().  Do not let
        // section() derive ready/empty from a stale or altered source.
        if (renewals.state !== "unavailable" && !["ready", "empty"].includes(renewals.freshness_state)) {
          renewals.state = "unavailable";
          renewals.reason = "source_unavailable";
          renewals.items = [];
        }
        // V5-A05's morning approval batch: unread notifications minted by
        // raise-delivery-cadence-alert (producer 'v5-a05-delivery-cadence'),
        // for the authenticated sponsor's own actor. This is the "morning
        // approval batch goes through the existing morning brief and
        // notification feed" wiring -- no separate queue is built; a batched
        // item is simply an unread notification from this producer, surfaced
        // here AND reachable through notification-feed like any other.
        const assuranceCadence = await section(async () => {
          // Reads through ops.v5_a05_assurance_cadence_batch (migration 0617,
          // sealed as SCAC v75 by 0618), a narrow SECURITY DEFINER function
          // granted to carr_reader that refuses any non-partner recipient and
          // hides items still held for the morning window -- morning-brief
          // runs on the reader connection, and ops.notification/
          // ops.notification_read themselves carry no carr_reader grant
          // (tools/test-handler-reads-are-granted.py). Never read those tables
          // directly from a handler.
          const result = await c.query(
            "select ops.v5_a05_assurance_cadence_batch($1) as batch", [scope.sponsor]);
          const batch = result.rows[0]?.batch;
          return { items: Array.isArray(batch) ? batch : [] };
        });
        const sections = { today, claim_card: claimCard, deals, loops, renewals, assurance_cadence: assuranceCadence };
        return {
          state: Object.values(sections).some((value) => value.state === "unavailable")
            ? "unavailable"
            : "ready",
          sponsor: scope.sponsor,
          sections,
        };
      },
    },

    "deal-board": {
      discoveryOrder: 10,
      write: false,
      description: "Open pipeline grouped by phase. Never exposes Salesforce commission/close-date placeholders (they are placeholders, not data).",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => ({ deals: (await c.query(`select b.id, b.name, b.client_ref, b.client_name, b.deal_type,
      b.phase, b.phase_sort, b.segment, b.outcome, b.lead_owner, b.last_touch, b.notes_path,
      d.operating_state, d.parking_note, to_jsonb(d.invoiced_on)#>>'{}' as invoiced_on,
      to_jsonb(d.closed_on)#>>'{}' as closed_on, d.lane
      from v_deal_board b join v_deal_room_board d on d.id=b.id
      order by b.phase_sort, b.name /* dealboard:operating-state */`)).rows }),
    },

    "deal-room-board": {
      discoveryOrder: 11,
      write: false,
      description: "The Deal Room home read: Salesforce-linked work records plus their active/parked operating state, national-account portfolio summaries, current partner identity, review clocks, market-agent assignments, and one open review session. Each record also carries field_base — the latest committed event id and time for every editable cell, read in the same statement as the values, which is what patch-deal-field takes as base_event_id. A cell with no history has no entry. workspace may be team, national_account, or all; no row is duplicated between workspaces.",
      inputSchema: { type: "object", properties: {
        workspace: { type: "string", enum: ["team","national_account","all"], default: "all" },
        account_client_id: { type: "string", description: "optional national-account client uuid" },
      } },
      handler: async (c, actor, args) => {
        const workspace = args.workspace || "all";
        // field_base: the latest committed event for each EDITABLE cell, read in
        // the SAME statement as the values it belongs to.
        //
        // Why it is here and not a second read: patch-deal-field bases on an event
        // id, and until now the board had no way to learn one except the changes
        // feed, which starts at the beginning of the log. So the first edit of a
        // session to a cell with any history sent no base at all, which this verb's
        // own concurrency rule treats as "any prior event conflicts" — a refusal
        // naming a months-old change as concurrent. This closes that.
        //
        // ONE STATEMENT is the point, not an economy. A value and its base read in
        // two statements can straddle a commit, and the dangerous half of that is a
        // base NEWER than the value shown: the next write would then be accepted
        // over a value the person never saw. Inside one statement both come from
        // one snapshot, so a concurrent write is either wholly visible here or
        // wholly invisible, and a partner's later edit still conflicts.
        //
        // One correlated subquery, not one query per deal: the ordering is the
        // record layer's own (recorded_at desc, id desc), the same pair
        // latestFieldConflict and revert-deal-field sort by, and a cell that has
        // never been edited simply has no key — which the client must read as "no
        // base", never as a base of null-meaning-anything.
        const deals = await c.query(
          `select b.id, b.name, b.type, b.phase, b.owner, b.attention,
                to_jsonb(b.next_date)#>>'{}' as next_date, b.next_step, b.market, b.segment,
                b.client_id, b.client_ref, b.client_name, b.account_client_id, b.account_client_ref,
                b.account_name, b.account_owner, b.market_agent,
                to_jsonb(b.last_touch)#>>'{}' as last_touch,
                to_jsonb(b.last_review_at)#>>'{}' as last_review_at, b.workspace_kind,
                b.operating_state, b.parking_reason, b.parking_note,
                to_jsonb(b.invoiced_on)#>>'{}' as invoiced_on,
                to_jsonb(b.closed_on)#>>'{}' as closed_on, b.lane, b.outcome,
                (select to_jsonb(pc) from v_deal_room_phase_change pc where pc.deal_id=b.id) as phase_change,
                to_jsonb(b.parked_at)#>>'{}' as parked_at, b.parked_by,
                coalesce((
                  select jsonb_object_agg(latest.field,
                           jsonb_build_object('id', latest.id,
                             'recorded_at', to_jsonb(latest.recorded_at)#>>'{}'))
                    from (select distinct on (e.field) e.field, e.id, e.recorded_at
                            from v_deal_room_event e
                           where e.subject_type='deal' and e.subject_id=b.id
                             and e.field = any($3::text[])
                           order by e.field, e.recorded_at desc, e.id desc) latest
                ), '{}'::jsonb) as field_base
           from v_deal_room_board b
          where ($1 = 'all' or b.workspace_kind = $1)
            and ($2::uuid is null or b.account_client_id = $2::uuid)
          order by b.attention desc, b.next_date nulls last, b.name
          /* dealroom:board-field-base */`,
          [workspace, args.account_client_id || null, [...DEAL_ROOM_FIELDS]]);
        const accounts = await c.query(
          `select account_client_id, account_client_ref, account_name, account_owner,
                open_deals, attention_deals, overdue_deals, stale_deals,
                to_jsonb(last_review_at)#>>'{}' as last_review_at, parked_deals
           from v_deal_room_account order by account_name`);
        const session = await c.query(
          `select session_id, workspace_kind, account_client_id,
                to_jsonb(started_at)#>>'{}' as started_at
           from v_deal_room_session
          where started_by=$1 and status='open'
            and ($2 = 'all' or workspace_kind=$2)
            and ($3::uuid is null or account_client_id=$3::uuid)
          order by started_at desc limit 1`,
          [actor.slug, workspace, args.account_client_id || null]);
        return { schema_version: 'local-deals-board.v1', actor: actor.slug, deals: deals.rows, accounts: accounts.rows,
          open_session: session.rows[0] || null };
      },
    },

    "capture-queue": {
      discoveryOrder: 12,
      write: false,
      description: "Pending capture proposals for active sessions. These are untrusted suggestions only; no confidence score confirms one. A partner must call resolve-candidate for every disposition.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => ({ candidates: (await c.query(
        `select id, session_id, kind, payload, evidence_quote, confidence::float8 as confidence, deal_name,
              to_jsonb(created_at)#>>'{}' as created_at
         from v_capture_candidate_queue
        order by confidence desc, created_at, id`)).rows }),
    },

    "get-call-context": {
      discoveryOrder: 13,
      write: false,
      description: "Read the exact active Call Mode context for an explicit list of deal UUIDs. It never searches by name: an unknown, closed, or parked UUID refuses the whole request. Current participant party refs, names, emails, and roles are returned only for the requested deals.",
      inputSchema: { type: "object", properties: {
        deal_ids: { type: "array", minItems: 1, maxItems: 50, items: { type: "string" } },
      }, required: ["deal_ids"] },
      handler: async (c, _actor, args) => {
        const ids = args.deal_ids;
        const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
        if (!Array.isArray(ids) || !ids.length || ids.length > 50 || ids.some(id => !uuid.test(id)) ||
            new Set(ids).size !== ids.length)
          throw new ToolError({ error: "invalid_call_context_deals" });
        const rows = (await c.query(
          `select deal_id,deal_name,owner,operating_state,participant_party_id,participant_party_ref,
                participant_name,participant_email,participant_role
           from capture_call_context($1::uuid[])
          order by deal_name,deal_id,participant_role,participant_name
          /* capture:tool-call-context */`, [ids])).rows;
        if (new Set(rows.map(row => String(row.deal_id))).size !== ids.length)
          throw new ToolError({ error: "invalid_call_context_deals" });
        const byDeal = new Map();
        for (const row of rows) {
          const deal = byDeal.get(row.deal_id) || { id: row.deal_id, name: row.deal_name,
            owner: row.owner, operating_state: row.operating_state, participants: [] };
          if (row.participant_role) deal.participants.push({ party_id: row.participant_party_id || null,
            ref: row.participant_party_ref || null, name: row.participant_name || null,
            email: row.participant_email || null, role: row.participant_role });
          byDeal.set(row.deal_id, deal);
        }
        return { deals: [...byDeal.values()] };
      },
    },

    "lead-hot": {
      discoveryOrder: 14,
      write: false,
      description: "Scored, unsuppressed leads (score, lane, est_lease_event, next_action_date). ALL of them surface — qualification is the human's job, never pre-filtered.",
      inputSchema: { type: "object", properties: { limit: { type: "integer", default: 30 } } },
      handler: async (c, _a, args) => ({ leads: (await c.query("select * from v_lead_hot order by score desc nulls last limit $1", [args.limit || 30])).rows }),
    },

    "lead-board": {
      discoveryOrder: 15,
      write: false,
      description: "The complete, safe worked-lead board. All leads surface, including weak, suppressed, and terminal rows: qualification is the human's job and this read is never pre-qualified or silently truncated. Returns the authoritative base_version needed by update-lead, ordered stage vocabulary including empty stages, score/confidence/freshness signals, and no phone, email, address, notes, or raw source detail.",
      inputSchema: LEAD_WORKSPACE_SCHEMA,
      handler: async (c, _actor, args = {}) => {
        if (args.workspace === "leads") return readLeadWorkspace(c, args);
        const stages = (await c.query(
          `select slug,label,sort
           from v_lead_board_stage
          order by sort,slug`)).rows;
        const leads = (await c.query(
          `select id,registry_ref,name,specialty,city,county,state,lane,stage,
                (select party_id from lead where lead.id=v_lead_board.id) as party_id,
                stage_label,stage_sort,score,segment,suppressed,est_lease_event,
                event_confidence,last_touch,next_action_date,owner,owner_label,
                base_version,created_at,updated_at,
                (stage <> 'archived') as conversion_eligible,
                (select client_id is not null from lead where lead.id=v_lead_board.id) as converted,
                coalesce((select jsonb_agg(jsonb_build_object('move_id',m.id,'from_stage',m.from_stage,
                  'to_stage',m.to_stage,'reason',m.reason,'evidence_ref',m.evidence_ref,'status',m.status,
                  'created_at',m.created_at,'undone_at',m.undone_at,'undone_by',a.display_name) order by m.created_at,m.id)
                  from lead_stage_move m left join actor a on a.id=m.undone_by where m.lead_id=v_lead_board.id),'[]') as stage_moves
           from v_lead_board
          order by stage_sort,suppressed,score desc nulls last,name,registry_ref`)).rows;
        const eligible=leads.filter(l=>l.stage!=="archived");
        return { generated_at: new Date().toISOString(), stages, leads,
          metrics:{nurture_count:eligible.filter(l=>l.stage==="nurture_drip" && !l.suppressed).length,
            conversion_denominator:eligible.length,converted_count:eligible.filter(l=>l.converted).length} };
      },
    },

    "claim-card": {
      discoveryOrder: 16, completionClass: "write",
      write: false,
      description: "The claimable candidate reservoir: who Joe or Dell could turn into a lead today, nearest lease window first. THE GAP THIS CLOSES, and it is the same one read-loop closed for loops: promote-pool and decline-candidate both refuse without base_version and both tell the caller to 'read the row from v_pool / v_claim_card first' — and nothing in the verb layer could perform that read. The only reader was a generated markdown card in the vault, which the doctrine cutoff retired on 2026-08-19; without this verb the two claim verbs would name a surface that no longer exists. Returns pool_id and base_version on every row, so a promote or decline follows directly with no guess and no version_conflict. SAFE COLUMNS ONLY — the view carries no email, phone or address by construction (has_channel says a channel exists; the human reads the number off the lead record after claiming). Ranked, never filtered: rows whose window has already PASSED are shown with a negative days_to_window rather than dropped, because a passed window is still a live conversation and three of them expired unread the last time this list had no reader. `needs_contact_count` is the tail with no channel at all — research, not calls, counted rather than hidden.",
      inputSchema: { type: "object", properties: {
        limit: { type: "integer", default: 5, description: "how many claimable rows to return, nearest window first" },
        include_needs_contact: { type: "boolean", default: false, description: "also return the rows with no phone or email on file — a research queue, not a call list" },
      } },
      handler: async (c, _a, args) => {
        // SAME ORDERING AS pipelines/brief_pack.py's claim card, deliberately:
        // dated rows before undated, future windows before passed ones, then
        // nearest window, then score. Rule a8c55a47 — a manual path and an
        // automated path that do the same job must be the same code; this is the
        // closest that gets across two languages, so the clause is copied
        // verbatim rather than reinvented, and any change belongs in both.
        const order = `order by (est_lease_event is null),
                              (days_to_window < 0),
                              abs(days_to_window) nulls last,
                              score desc nulls last`;
        const channel = args.include_needs_contact ? "" : "where has_channel";
        const rows = (await c.query(
          `select pool_id, base_version, lane, display_name, org_name, vertical,
                city, county, state, segment, segment_play, score, score_basis,
                est_lease_event, est_basis, days_to_window, has_channel,
                needs_contact, dup_tier, dup_ref, dup_basis
           from v_claim_card ${channel} ${order} limit $1`,
          [args.limit || 5])).rows;
        const totals = (await c.query(
          `select count(*)::int as claimable,
                count(*) filter (where not has_channel)::int as needs_contact_count
           from v_claim_card`)).rows[0];
        return {
          showing: rows.length,
          claimable: totals.claimable,
          needs_contact_count: totals.needs_contact_count,
          candidates: rows,
          hint: "promote-pool or decline-candidate with the row's own pool_id and base_version. Every decline shortens this list permanently, which is the only thing that makes it shorter.",
        };
      },
    },

    "stale-records": {
      discoveryOrder: 17,
      write: false,
      description: "Active deals gone quiet 14+ days, measured on last_touch (see v_last_touch; a deal inherits its client's touch since 0033). Replaces the hand-run staleness sweep. Empty can mean 'nothing stale' OR 'nothing captured' — check v_capture_coverage before trusting a clean result.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => ({ stale: (await c.query("select * from v_stale_records order by days_quiet desc nulls first")).rows }),
    },

    "integrity-digest": {
      discoveryOrder: 18,
      write: false,
      description: "The heartbeat's lines: row counts, export freshness (dead-man; stale/last_ok per target), writes_by_dell_24h, norm_owed_open, merge_queue, triage queue.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => ({ digest: (await c.query("select * from v_integrity_digest")).rows }),
    },

    "read-loop": {
      discoveryOrder: 19,
      write: false,
      description: "Read ONE loop and its current version. THE GAP THIS CLOSES: update-loop and close-loop both refuse without base_version and tell the caller to 'read the record first' — and until this verb existed, nothing could perform that read. The only way to learn a loop's version was to guess, take a version_conflict, and lift the number out of the error message. Pass `number` (the '#142' a human says, with or without the hash) or `loop_id`. A number can repeat across kinds, so an ambiguous number returns the candidates rather than picking one for you. Also returns `amended` (true once amend-closed-loop has ever corrected this loop's outcome) and `amendments`, the full correction trail oldest first — each with prior_outcome, new_outcome, prior_resolution, new_resolution, reason, actor and created_at.",
      inputSchema: { type: "object", properties: {
        number: { type: "string", description: "the loop number as a human writes it, with or without the leading #" },
        loop_id: { type: "string", description: "exact uuid; wins over number" },
        kind: { type: "string", enum: LOOP_KINDS, description: "narrows an ambiguous number" },
      } },
      handler: async (c, _a, args) => {
        const cols = `id as loop_id, kind, number, domain, blocker_class, blocker_detail, status,
                    title, body, owner, marker, since_text, unblocks, source_note, tier, personal_to,
                    to_jsonb(due_on)#>>'{}' as due_on, close_outcome,
                    to_jsonb(closed_at)#>>'{}' as closed_at, version,
                    to_jsonb(created_at)#>>'{}' as created_at,
                    to_jsonb(updated_at)#>>'{}' as updated_at`;
        if (args.loop_id) {
          const r = await c.query(`select ${cols} from loop_item where id=$1`, [args.loop_id]);
          if (!r.rows.length) return { error: "not_found", hint: "no loop carries that id" };
          return await withAmendmentHistory(c, r.rows[0]);
        }
        const num = String(args.number || "").replace(/^#/, "").trim();
        if (!num) return { error: "need_number_or_id", hint: "pass number (e.g. '142') or loop_id" };
        const params = [num];
        let sql = `select ${cols} from loop_item where number=$1`;
        if (args.kind) { params.push(args.kind); sql += ` and kind=$${params.length}`; }
        const r = await c.query(sql, params);
        if (!r.rows.length) return { error: "not_found", number: num };
        if (r.rows.length > 1) {
          return {
            error: "ambiguous_number",
            candidates: r.rows.map((x) => ({ loop_id: x.loop_id, kind: x.kind, status: x.status, title: x.title })),
            hint: "same number in more than one kind — pass kind to narrow",
          };
        }
        return await withAmendmentHistory(c, r.rows[0]);
      },
    },

    "loop-board": {
      discoveryOrder: 20,
      write: false,
      description: "Every open loop with its domain, what it is blocked on, and its version — the live answer to 'what is still open and what is it waiting on'. THE GAP THIS CLOSES: that question used to be answered by reading a generated markdown render, which splits loops across four files by kind and is only as fresh as the last export; a session counting from those files gets a number that is both stale and partial. Defaults to open work loops. Pass blocker:'none' for the rows that predate the blocker requirement — that is the do-it-or-close-it pile, and the standing rule is never to re-file them. Every row carries its version, so a close needs no second read.",
      inputSchema: { type: "object", properties: {
        kind: { type: "string", enum: LOOP_KINDS, default: "open_loop" },
        status: { type: "string", enum: ["open", "done", "dropped", "any"], default: "open" },
        domain: { type: "string", description: "deals | prospecting | networking | marketing | business | system" },
        blocker: { type: "string", description: "a blocker class to filter to, or 'none' for rows naming no blocker, or 'any' for rows that name one" },
        owner: { type: "string", description: "'claude' for the autonomous drain queue — rows the system may finish and close on its own evidence. 'joe' or 'dell' for a person's pile. 'joint' for the legacy rows owned by two people at once, which no query can select for and nobody picks up." },
        search: { type: "string", description: "case-insensitive match against the title" },
        limit: { type: "integer", default: 60 },
        summary: { type: "boolean", description: "Payload budget: counts by status, blocker class and owner, plus per-loop only {number, kind, label (title or first 80 chars of body's first line), blocker_class, owner, since_text, due_on, version}. No bodies, no blocker_detail. Absent/false returns today's full rows." },
      } },
      handler: async (c, _a, args) => {
        const where = ["kind = $1"];
        const params = [args.kind || "open_loop"];
        const st = args.status || "open";
        if (st !== "any") { params.push(st); where.push(`status = $${params.length}`); }
        if (args.domain) { params.push(args.domain); where.push(`domain = $${params.length}`); }
        if (args.blocker === "none") where.push("blocker_class is null");
        else if (args.blocker === "any") where.push("blocker_class is not null");
        else if (args.blocker) { params.push(args.blocker); where.push(`blocker_class = $${params.length}`); }
        if (args.owner === "joint") where.push("owner ~ '[/+&,]|→|->'");
        else if (args.owner) { params.push(args.owner); where.push(`lower(owner) = lower($${params.length})`); }
        if (args.search) {
          params.push(`%${args.search}%`);
          // Search BOTH columns. 148 of the 150 open work loops carry a null
          // title and hold their text in body, so a title-only search matches
          // almost nothing — which looks identical to "no such loop".
          where.push(`(coalesce(title,'') || ' ' || coalesce(body,'')) ilike $${params.length}`);
        }
        params.push(Math.min(Number(args.limit) || 60, 300));
        if (args.summary) {
          // PAYLOAD BUDGET, not a new view. The board's full rows carry body-sized
          // labels and blocker_detail prose; at 100+ loops one board read can
          // outweigh every other verb call in a session. Summary mode answers the
          // three questions the board actually exists for — what is open, what is
          // it blocked on, who owns it — from the SAME filtered query, so both
          // modes always agree on which rows exist.
          const s = await c.query(
            `select number, kind, status, owner,
                  -- LABEL: title or first 80 chars of body's first line, bold
                  -- markers stripped — the same fallback the full row uses.
                  left(coalesce(
                    nullif(title, ''),
                    nullif(regexp_replace(split_part(body, E'\\n', 1), '\\*\\*', '', 'g'), '')
                  ), 80) as label,
                  blocker_class, since_text,
                  to_jsonb(due_on)#>>'{}' as due_on, version
             from loop_item
            where ${where.join(" and ")}
            order by domain nulls last,
                     coalesce(nullif(regexp_replace(number, '[^0-9]', '', 'g'), '')::int, 999999)
            limit $${params.length}`, params);
          const tally = (key) => {
            const out = {};
            for (const row of s.rows) {
              const k = row[key] == null ? "none" : String(row[key]);
              out[k] = (out[k] || 0) + 1;
            }
            return out;
          };
          return {
            summary: true, count: s.rows.length,
            by_status: tally("status"),
            by_blocker_class: tally("blocker_class"),
            by_owner: tally("owner"),
            loops: s.rows.map(({ status, ...loop }) => loop),
          };
        }
        const r = await c.query(
          `select number, kind, domain, status, owner, marker, title,
                -- LABEL, not title. Almost every loop predates the title column
                -- and keeps its text in body, so a board keyed on title alone
                -- returns a column of nulls and cannot identify anything. Fall
                -- back to the first line of body with the bold markers stripped.
                coalesce(
                  nullif(title, ''),
                  nullif(regexp_replace(split_part(body, E'\\n', 1), '\\*\\*', '', 'g'), '')
                ) as label,
                -- Surfaced rather than silently tolerated: a row owned by two
                -- people is owned by neither, and 110 of the 150 open work
                -- loops were written that way. New writes are refused; these
                -- are the legacy rows waiting to be split.
                (owner ~ '[/+&,]|→|->') as joint_owner,
                blocker_class, blocker_detail, since_text,
                to_jsonb(due_on)#>>'{}' as due_on, version
           from loop_item
          where ${where.join(" and ")}
          order by domain nulls last,
                   coalesce(nullif(regexp_replace(number, '[^0-9]', '', 'g'), '')::int, 999999)
          limit $${params.length}`, params);
        return { count: r.rows.length, loops: r.rows };
      },
    },
  };
}
