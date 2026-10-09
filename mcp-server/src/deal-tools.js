import { config, lockDealField, resolveSubject } from "./verb-support.js";
import { ToolError } from "./tool-error.js";
import { versionGuard, withEnvelope, writeEvent } from "./versioned-write.js";
import { isCalendarDate } from "./calendar-date.js";

async function rateConfirm(client, args, normValue, bandKey) {
  if (normValue == null || args.confirm) return;
  const band = await config(client, bandKey, { min: 5, max: 120 });
  if (normValue < band.min || normValue > band.max)
    throw new ToolError({ error: "needs_confirm",
      reason: `normalized rate ${normValue} $/SF/yr is outside ${band.min}-${band.max}`,
      hint: "a 12x miss usually means $/SF/mo vs $/SF/yr; resubmit with confirm:true if intended" });
}

function normRate(amount, basis) {
  if (amount == null) return null;
  if (basis === "usd_sf_yr") return amount;
  if (basis === "usd_sf_mo") return amount * 12;
  return null; // gross bases: tool computes from area when space known, else norm_owed
}

// ---------- [0063] the counterparty-observation vocabularies ----------
//
// Same posture as validateLinkKind above and for the same reason: 0063 put
// submarket_condition and negotiation_claim_type in ref TABLES, with
// `falsifiable` and `derived` as columns rather than as lists hardcoded in a
// view, so widening either vocabulary is a row a human adds and not a deploy.
// A verb that carried its own enum would put a second copy of that list in a
// file only a deploy can change — the exact drift 0052/0053 had to unpick.
async function validateSubmarket(c, slug) {
  const r = await c.query("select slug from submarket_condition where slug=$1", [slug]);
  if (r.rows.length) return slug;
  const all = await c.query("select slug, label, tightness from submarket_condition order by sort");
  throw new ToolError({ error: "unknown_submarket_condition", got: slug, valid: all.rows,
    hint: "submarket_condition is a ref table (0063) — add a row there if a genuinely new " +
          "value is needed. Omitting it means NOT RECORDED, which is never a synonym for " +
          "'balanced'; do not pick one to fill the field." });
}

async function validateClaimType(c, slug) {
  const r = await c.query(
    "select slug, label, falsifiable, derived, reversal_test from negotiation_claim_type where slug=$1",
    [slug]);
  if (!r.rows.length) {
    const all = await c.query(
      "select slug, label, falsifiable, derived from negotiation_claim_type order by sort");
    throw new ToolError({ error: "unknown_claim_type", got: slug, valid: all.rows,
      hint: "negotiation_claim_type is the vocabulary (0063); widening it is a row a human adds" });
  }
  // The composite FK in 0063 makes a derived class physically unloggable. Catching
  // it here turns a foreign_key_violation into the sentence that says what to do.
  if (r.rows[0].derived)
    throw new ToolError({ error: "derived_claim_type", got: slug,
      reversal_test: r.rows[0].reversal_test,
      hint: "this claim class is already recorded elsewhere on the round, and two homes for " +
            "one fact is the 0045 fault. 'deadline' IS negotiation_round.expires_on — pass " +
            "expires_on on this same call instead." });
  return r.rows[0];
}

// 0063 lands as a migration Joe applies by hand, and this Worker deploys
// separately. Either order is possible, so the new arguments check for their own
// schema and say which half is missing instead of surfacing an undefined_column
// from inside a rolled-back transaction. Only runs when a new argument is used —
// an old-shaped record-counter call never pays for it.
export async function require0063(c) {
  const r = await c.query(
    `select to_regclass('public.negotiation_claim') is not null as claims,
            exists (select 1 from information_schema.columns
                     where table_schema='public' and table_name='negotiation_round'
                       and column_name='submarket_condition') as submarket`);
  if (r.rows[0].claims && r.rows[0].submarket) return;
  throw new ToolError({ error: "migration_not_applied", migration: "0063_counterparty_observation",
    present: r.rows[0],
    hint: "submarket_condition and claims[] need migration 0063. Apply it " +
          "(`~/carr-system/run.sh migrate --apply --yes`) and retry; every other argument on " +
          "this verb works without it." });
}

export function dealTools() {
  return {
    "record-executed-lease": {
      discoveryOrder: 28, serialization: "idempotency-key",
      write: true,
      authorityOnly: true,
      description: "Record the current executed lease/abstract that CARR actually holds for a deal. This is the authenticated first-party renewal authority: expiration_on must be an exact sourced date, evidence_kind/evidence_ref are mandatory, and a replacement needs the current lease version. It never infers a date from a term, listing, comp, NPPES, or web research. Only the deal's current owning partner may write it.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        base_version: { type: "integer" },
        executed_on: { type: "string" }, commencement_on: { type: "string" },
        expiration_on: { type: "string" }, term_months: { type: "integer" },
        evidence_kind: { type: "string", enum: ["executed_lease", "lease_amendment", "lease_abstract"] },
        evidence_ref: { type: "string" }, source: { type: "string" },
      }, required: ["idempotency_key", "deal", "executed_on", "expiration_on", "evidence_kind", "evidence_ref", "source"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-executed-lease", args, async () => {
        const isoDate = (value, field, required = false) => {
          if ((value === null || value === undefined || value === "") && !required) return null;
          const text = String(value || "");
          if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || Number.isNaN(Date.parse(`${text}T00:00:00Z`)))
            throw new ToolError({ error: "invalid_lease_date", field, expected: "YYYY-MM-DD" });
          return text;
        };
        const executedOn = isoDate(args.executed_on, "executed_on", true);
        const commencementOn = isoDate(args.commencement_on, "commencement_on");
        const expirationOn = isoDate(args.expiration_on, "expiration_on", true);
        if (commencementOn && expirationOn <= commencementOn)
          throw new ToolError({ error: "invalid_lease_dates", hint: "expiration_on must be after commencement_on" });
        if (args.term_months !== undefined &&
            (!Number.isInteger(args.term_months) || args.term_months < 1 || args.term_months > 480))
          throw new ToolError({ error: "invalid_term_months", allowed: "1..480" });
        const evidenceKinds = new Set(["executed_lease", "lease_amendment", "lease_abstract"]);
        if (!evidenceKinds.has(args.evidence_kind))
          throw new ToolError({ error: "invalid_lease_evidence_kind", valid: [...evidenceKinds] });
        const evidenceRef = String(args.evidence_ref || "").trim();
        const source = String(args.source || "").trim();
        if (!evidenceRef || evidenceRef.length > 1000)
          throw new ToolError({ error: "lease_evidence_ref_required" });
        if (!source || source.length > 500)
          throw new ToolError({ error: "lease_source_required" });

        let inserted;
        try {
          inserted = (await c.query(
            `select lease_id,version,superseded_lease_id,deal_id,client_id
             from ops.record_executed_lease($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
            [args.deal, args.base_version ?? null, executedOn, commencementOn, expirationOn,
             args.term_months ?? null, args.evidence_kind, evidenceRef, source])).rows[0];
        } catch (error) {
          const message = String(error?.message || "");
          if (message.includes("does not own the current deal"))
            throw new ToolError({ error: "not_deal_owner" });
          if (message.includes("version conflict"))
            throw new ToolError({ error: "version_conflict" });
          if (message.includes("deal was not found"))
            throw new ToolError({ error: "deal_not_found" });
          if (message.includes("needs exact disambiguation"))
            throw new ToolError({ error: "needs_disambiguation", ref: args.deal });
          if (message.includes("no current lease exists"))
            throw new ToolError({ error: "no_current_lease" });
          throw error;
        }
        if (!inserted) throw new ToolError({ error: "lease_write_no_readback" });
        await writeEvent(c, actor, "record-executed-lease", "deal", inserted.deal_id,
          { new: { lease_id: inserted.lease_id, expiration_on: expirationOn, evidence_kind: args.evidence_kind,
              evidence_ref: evidenceRef, source }, idempotency_key: args.idempotency_key });
        return { ok: true, lease_id: inserted.lease_id, version: inserted.version,
          superseded_lease_id: inserted.superseded_lease_id || null };
      }),
    },

    "update-deal": {
      discoveryOrder: 29,
      write: true,
      description: "Field-level change to a deal (deal_type, phase, segment, outcome, notes_path, salesforce_id, city, lane, invoiced_on). invoiced_on (YYYY-MM-DD) marks the deal invoiced, which takes it out of the Salesforce reconciliation absence scope (V5-RW02). deal_type uses the closed deal_type_ref vocabulary. Requires base_version from a fresh read; a same-field conflict means someone else wrote the same column — ask the human, never retry blind. A version bump from a DIFFERENT field (someone else's disjoint edit) is rebased automatically and reported back as `rebased`/`rebase_receipt`; nothing about this call's own fields is ever silently changed. To move a deal to a DIFFERENT CLIENT, use reassign-deal: client_id is deliberately not settable here.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        base_version: { type: "integer" },
        fields: { type: "object", description: "subset of: deal_type, phase, segment, outcome, closed_on, won_value, notes_path, salesforce_id, city, lane, invoiced_on" } },
        required: ["idempotency_key","deal","base_version","fields"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "update-deal", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        // city and lane joined the list in 0074, when they stopped being source_row
        // passthrough and became real columns. Before that they were unsettable,
        // which is why salesforce-diff could only ever REPORT a city move.
        // invoiced_on joined in 0733 (V5-RW02): a won deal stays in the Salesforce
        // reconciliation absence scope until it is marked invoiced.
        const allowed = ["deal_type","phase","segment","outcome","closed_on","won_value","notes_path",
                         "salesforce_id","city","lane","invoiced_on"];
        if ("client_id" in args.fields) throw new ToolError({ error: "use_reassign_deal",
          hint: "moving a deal between clients is structural, not a field edit — use reassign-deal" });
        const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
        if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed });
        // A real calendar date: the pattern, then a round trip, so 2026-13-45 or
        // 2026-02-30 is refused here instead of failing later as a raw cast error.
        if (keys.includes("invoiced_on") && args.fields.invoiced_on !== null &&
            !isCalendarDate(args.fields.invoiced_on))
          throw new ToolError({ error: "invalid_invoiced_on",
            hint: "invoiced_on is a calendar date YYYY-MM-DD, or null to clear it" });
        // Legacy phase edits share the Deal Room event stream. Acquire its lock
        // before versionGuard can lock the deal row, matching Deal Room lock order.
        if (keys.includes("phase")) await lockDealField(c, s.id, "phase");
        // touchedFields = keys: computed BEFORE the guard so a version bump from
        // some OTHER field can be told apart from a bump on one of THESE fields.
        // See versionGuard's own comment (CONFLICT TIERING, slice S6).
        const guard = await versionGuard(c, "deal", s.id, args.base_version, keys);
        const old = (await c.query(`select ${keys.join(",")} from deal where id=$1`, [s.id])).rows[0];
        const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
        await c.query(`update deal set ${sets}, updated_by=$1 where id=$${keys.length + 2}`,
          [actor.id, ...keys.map(k => args.fields[k]), s.id]);
        for (const k of keys)
          await writeEvent(c, actor, "update-deal", "deal", s.id,
            { field: k, old: { [k]: old[k] }, new: { [k]: args.fields[k] },
              recorded_at_after_lock: k === "phase" || k === "invoiced_on", idempotency_key: args.idempotency_key });
        return { ok: true, updated: keys,
                 ...(guard.rebased ? { rebased: true, rebase_receipt: guard.rebase_receipt } : {}) };
      }),
    },

    "new-deal": {
      discoveryOrder: 30,
      write: true,
      description: "Create a deal on an existing client. THE GAP THIS CLOSES: until 2026-08-07 nothing in the record layer could create a deal — new-client makes only a client row, reassign-deal and set-lead both need a deal that already exists, and the ONLY insert into `deal` in the whole repo was pipelines/import_wave1.py, the one-time bulk import. So every deal in the book traced back to that import, and the six deals the 2026-08-07 Salesforce read found had nowhere to land. The client must exist first (new-client over a party): a deal hangs off a client, never free-floating, and this verb will not invent one. humanOnly on purpose — a new deal is a real commitment in a partner's book, and salesforce-diff deliberately never auto-adds one. deal_type and phase are validated by the database against deal_type_ref and deal_phase, so a bad slug is refused with the live list rather than guessed at. Refuses a duplicate name and a salesforce_id already in use, naming the deal that holds it. Commission and close date from Salesforce are PLACEHOLDERS and land in the two labelled placeholder columns, never in won_value.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        client: { type: "string", description: "C-ref or exact name of the client this deal belongs to" },
        name: { type: "string", description: "deal name; keep it the Salesforce Deal Name where one exists" },
        deal_type: { type: "string", description: "slug from deal_type_ref, e.g. startup / relocation / additional_office / renewal / expansion / other" },
        phase: { type: "string", description: "slug from deal_phase, e.g. pending / research / negotiation / legal / due_diligence / closing / closed" },
        segment: { type: "string" },
        city: { type: "string", description: "city of transaction" },
        lane: { type: "string", description: "slug from deal_lane: territory (CARR represents) or national (out-of-market referral). Salesforce's Out of Market Deal checkbox is the truth here — never infer it from the city." },
        salesforce_id: { type: "string", description: "Opportunity id (006...), the reconciliation key back to the system of record" },
        notes_path: { type: "string" },
        sf_commission_placeholder: { type: "number", description: "Salesforce commission figure — a PLACEHOLDER, never summed and never shown as pipeline value" },
        sf_close_date_placeholder: { type: "string", description: "Salesforce close date (YYYY-MM-DD) — a PLACEHOLDER, never a forecast" },
        reason: { type: "string", description: "why this deal is being opened; lands on the event as agent_rationale" },
        human_quote: { type: "string", description: "the partner's verbatim words, when they directed it" } },
        required: ["idempotency_key","client","name","deal_type","phase"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "new-deal", args, async () => {
        const s = await resolveSubject(c, args.client);
        if (s.type !== "client") throw new ToolError({ error: "not_a_client", resolved: s,
          hint: "a deal hangs off a client. Create the client first with new-client over a party." });

        // A second deal with the same name is nearly always a double-add, and the damage
        // (two records drifting apart, each half-updated) is worse than the inconvenience.
        const dupe = await c.query(
          "select subject_id, display_name from v_ref_index where subject_type='deal' and lower(display_name)=lower($1)",
          [args.name]);
        if (dupe.rows.length) throw new ToolError({ error: "deal_name_exists",
          existing: dupe.rows.map(r => ({ id: r.subject_id, name: r.display_name })),
          hint: "if this is genuinely a second deal for the same client, give it a distinguishing name" });

        let r;
        // SAVEPOINT, for the same reason insertOrgPartyGuarded takes one (defect
        // 18b12fda's review, 2026-08-14): after the insert fails, the enclosing
        // transaction is aborted (25P02) until rolled back, so the diagnostic
        // queries below used to die on the poisoned transaction and replace both
        // friendly answers with an opaque error. The mapping was dead code.
        await c.query("savepoint new_deal_insert");
        try {
          r = await c.query(
            `insert into deal (client_id, name, deal_type, phase, segment, city, lane, salesforce_id,
             notes_path, sf_commission_placeholder, sf_close_date_placeholder, created_by, updated_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$12) returning id`,
            [s.id, args.name, args.deal_type, args.phase, args.segment || null,
             args.city || null, args.lane || null,
             args.salesforce_id || null, args.notes_path || null,
             args.sf_commission_placeholder ?? null, args.sf_close_date_placeholder || null, actor.id]);
        } catch (e) {
          // Map the database's own guards to answers a caller can act on, rather than
          // leaking a raw driver error. The DB stays the authority on both vocabularies.
          if (e.code === "23505") {
            await c.query("rollback to savepoint new_deal_insert");
            const held = await c.query("select name from deal where salesforce_id=$1", [args.salesforce_id]);
            throw new ToolError({ error: "salesforce_id_in_use", salesforce_id: args.salesforce_id,
              held_by: held.rows[0]?.name ?? null,
              hint: "one Opportunity maps to exactly one deal; check whether this deal already exists under another name" });
          }
          if (e.code === "23503") {
            await c.query("rollback to savepoint new_deal_insert");
            // deal has three closed vocabularies behind FKs: deal_type_ref,
            // deal_phase and (since 0074) deal_lane. Name the right one.
            const con = e.constraint || "";
            const which = /deal_type/.test(con) ? "deal_type" : /lane/.test(con) ? "lane" : "phase";
            const table = { deal_type: "deal_type_ref", lane: "deal_lane", phase: "deal_phase" }[which];
            let valid = [];
            try { valid = (await c.query(`select slug from ${table} order by slug`)).rows.map(x => x.slug); }
            catch { /* the role may not read the ref table; the error below still names the field */ }
            throw new ToolError({ error: `unknown_${which}`, given: args[which], valid });
          }
          throw e;
        }

        await writeEvent(c, actor, "new-deal", "deal", r.rows[0].id,
          { new: { name: args.name, client: args.client, deal_type: args.deal_type, phase: args.phase,
                   salesforce_id: args.salesforce_id || null },
            human_quote: args.human_quote, agent_rationale: args.reason,
            idempotency_key: args.idempotency_key });
        return { ok: true, deal_id: r.rows[0].id, name: args.name, client_ref: args.client };
      }),
    },

    "reassign-deal": {
      discoveryOrder: 31,
      write: true,
      description: "Move a deal onto the client it actually belongs to. THIS IS THE ONLY VERB THAT CHANGES deal.client_id — update-deal refuses that field on purpose, because re-pointing a deal changes whose book it sits in and is structural, not a field edit (the same reason set-lead owns the owner). Requires base_version from a fresh read. Refuses a no-op, refuses a merged-away target, and records the old and new client on the event so the move is auditable. It does NOT touch the client rows themselves: a parent/sub-client structure (a national account over its franchisees) is expressed by party.org_id, not by moving deals up to the parent.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        base_version: { type: "integer" },
        new_client: { type: "string", description: "C-ref or exact name of the client the deal really belongs to" },
        reason: { type: "string", description: "why it moved; lands on the event as agent_rationale" },
        human_quote: { type: "string", description: "the partner's verbatim words, when they directed the move" } },
        required: ["idempotency_key","deal","base_version","new_client"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "reassign-deal", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        await versionGuard(c, "deal", s.id, args.base_version);

        const t = await resolveSubject(c, args.new_client);
        if (t.type !== "client") throw new ToolError({ error: "not_a_client", resolved: t,
          hint: "new_client must resolve to a client (C-ref or exact name), not a lead, vendor or deal" });

        // A merged-away client is a tombstone, not a destination. Moving a deal onto
        // one would hide it behind a pointer — the exact shape this verb exists to undo.
        const tgt = (await c.query("select merged_into from client where id=$1", [t.id])).rows[0];
        if (!tgt) throw new ToolError({ error: "not_found", table: "client", id: t.id });
        if (tgt.merged_into) throw new ToolError({ error: "client_merged_away",
          merged_into: tgt.merged_into, hint: "re-point to the surviving client instead" });

        const cur = (await c.query("select client_id from deal where id=$1", [s.id])).rows[0];
        if (cur.client_id === t.id) throw new ToolError({ error: "no_op",
          hint: "the deal already belongs to that client; nothing was written" });

        const label = async (id) => (await c.query(
          `select ref, display_name from v_ref_index where subject_type='client' and subject_id=$1`, [id]
        )).rows[0] || { ref: null, display_name: null };
        const from = await label(cur.client_id);
        const to = await label(t.id);

        await c.query("update deal set client_id=$1, updated_by=$2 where id=$3", [t.id, actor.id, s.id]);
        await writeEvent(c, actor, "reassign-deal", "deal", s.id, {
          field: "client_id",
          old: { client_id: cur.client_id, ref: from.ref, name: from.display_name },
          new: { client_id: t.id, ref: to.ref, name: to.display_name },
          agent_rationale: args.reason || null,
          human_quote: args.human_quote || null,
          idempotency_key: args.idempotency_key });
        return { ok: true, deal: s.id, from: { ref: from.ref, name: from.display_name },
                 to: { ref: to.ref, name: to.display_name } };
      }),
    },

    "set-lead": {
      discoveryOrder: 32,
      write: true,
      description: "THE human ownership handoff: make joe or dell the current lead on a deal. THIS IS THE ONLY VERB THAT SETS A DEAL'S OWNER — it writes the deal_participant row (role='lead') that v_deal_board exposes as lead_owner, so a null lead_owner is fixed here and NOT through update-deal. Ownership is a matter between the two humans, never a machine's call. Requires base_version from a fresh read; the locked deal version makes simultaneous handoffs conflict instead of silently replacing one another. Closes the old lead row, opens the new one, one event. The database enforces exactly one current lead.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        new_lead: { type: "string", enum: ["joe","dell"] },
        base_version: { type: "integer" } },
        required: ["idempotency_key","deal","new_lead","base_version"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-lead", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        await lockDealField(c, s.id, "owner");
        await versionGuard(c, "deal", s.id, args.base_version);
        const previousOwner = (await c.query("select owner from deal where id=$1", [s.id])).rows[0]?.owner ?? null;
        const na = await c.query("select id from actor where slug=$1", [args.new_lead]);
        const prev = await c.query(
          `update deal_participant set to_at=now() where deal_id=$1 and role='lead' and to_at is null
         returning actor_id`, [s.id]);
        await c.query(
          "insert into deal_participant (deal_id, actor_id, role, set_by) values ($1,$2,'lead',$3)",
          [s.id, na.rows[0].id, actor.id]);
        await c.query("update deal set owner=$1, updated_by=$2 where id=$3", [args.new_lead, actor.id, s.id]);
        await writeEvent(c, actor, "set-lead", "deal", s.id,
          { field: "owner", old: { owner: previousOwner, lead: prev.rows[0]?.actor_id || null },
            new: { owner: args.new_lead, lead: args.new_lead }, recorded_at_after_lock: true,
            idempotency_key: args.idempotency_key });
        return { ok: true, new_lead: args.new_lead };
      }),
    },

    "record-counter": {
      discoveryOrder: 46,
      write: true,
      description: "Log a negotiation round: whose paper (side), the economics (rate REQUIRES its basis — never a bare number), TI, free rent, term, PLUS what that side CLAIMED about its own position (\"best and final\", \"the owner won't go below 18\", \"we walk Friday\") and how the submarket stood when they said it. Use it after every counter, and log the claims at the same time — a claim is only ever falsifiable against the rounds that come after it, so a claim not captured now is uncomputable for ever. Round number auto-increments per deal+side if omitted. Out-of-band rates ask for confirm. NOT a place for a characterisation of a human being: 'aggressive', 'bluffs', 'reasonable' have no field here and never will — claims[] records what was SAID, and whether it was later contradicted is computed at read time.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        side: { type: "string", enum: ["tenant","landlord","buyer","seller"] },
        proposed_on: { type: "string", description: "YYYY-MM-DD; defaults today" },
        rate_amount: { type: "number" },
        rate_basis: { type: "string", enum: ["usd_sf_yr","usd_sf_mo","usd_mo_gross","usd_yr_gross"] },
        ti_amount: { type: "number" }, ti_basis: { type: "string", enum: ["usd_total","usd_sf"] },
        free_rent_months: { type: "number" }, term_months: { type: "integer" },
        options_note: { type: "string" }, escalator: { type: "string" },
        expires_on: { type: "string", description: "YYYY-MM-DD. THE DEADLINE LIVES HERE — 'this offer dies Friday' is this field, never a claims[] row; a deadline claim is refused on purpose (0063), because a later round from the same side dated after this date already falsifies it." },
        submarket_condition: { type: "string", description: "soft | balanced | tight — how the submarket stood WHEN THIS ROUND was proposed (0063; validated against the submarket_condition ref table, so widening it is a row a human adds). It separates leverage from skill: a landlord in a tight market concedes nothing because he need not, and scoring that as toughness credits the market to the man. Record it once per deal — the scorecard reads the latest non-null. OMIT IT when you do not know; blank means not recorded and is never a synonym for 'balanced'." },
        claims: { type: "array", maxItems: 6, description:
          "[0063] What this side CLAIMED about its own position ON THIS ROUND. A list, not a field, because a side routinely makes three at once (\"best and final, the owner won't go below eighteen, and we have another tenant looking\") and one slot would keep one and discard two. Observations only — what was said, on this round. 'deadline' is not loggable here; that is expires_on.",
          items: { type: "object", properties: {
            type: { type: "string", description: "negotiation_claim_type slug: finality (best and final), authority (\"the owner won't go below X\"), walk_away (\"we're done\"), competing_interest (\"another tenant is looking\" — logged for the history, permanently excluded from every score because nothing could ever falsify it)" },
            stated_floor: { type: "number", description: "the number named in an AUTHORITY claim when it differs from this round's own rate — \"won't go below 18\" while offering 19. Omit when the claim was about the round's own position; never a guess." },
            stated_floor_basis: { type: "string", enum: ["usd_sf_yr","usd_sf_mo","usd_mo_gross","usd_yr_gross","usd_total","usd_sf_total"], description: "REQUIRED whenever stated_floor is given — the same no-bare-numbers rule the rate follows" },
            quote: { type: "string", description: "their words, as close to verbatim as was heard. Evidence for a human reader; no score ever reads this text." },
            note: { type: "string" } }, required: ["type"] } },
        note: { type: "string" }, confirm: { type: "boolean" } },
        required: ["idempotency_key","deal","side"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-counter", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        if (args.rate_amount != null && !args.rate_basis)
          throw new ToolError({ error: "missing_basis", hint: "a rate is meaningless without its basis" });
        await rateConfirm(c, args, normRate(args.rate_amount, args.rate_basis), "rate.asking_confirm_band_sf_yr");

        // [0063] EVERYTHING NEW IS VALIDATED BEFORE THE ROUND IS INSERTED. The
        // envelope would roll a late failure back cleanly, but the caller would get
        // a foreign_key_violation where it deserves the legal vocabulary — and the
        // 'deadline' refusal in particular is a sentence, not a constraint name.
        let submarket = null;
        const claims = Array.isArray(args.claims) ? args.claims : [];
        if (args.submarket_condition || claims.length) {
          await require0063(c);
          if (args.submarket_condition)
            submarket = await validateSubmarket(c, args.submarket_condition);
        }
        const claimTypes = [];
        for (const cl of claims) {
          const t = await validateClaimType(c, cl.type);
          if (claimTypes.some(x => x.slug === t.slug))
            throw new ToolError({ error: "duplicate_claim", claim_type: t.slug,
              hint: "one row per (round, claim class) — a class said twice in one breath is " +
                    "still one claim. Put the second wording in `quote` or `note`." });
          if (cl.stated_floor != null && !cl.stated_floor_basis)
            throw new ToolError({ error: "missing_basis", claim_type: t.slug,
              hint: "a stated floor is meaningless without its basis, exactly as a rate is" });
          claimTypes.push(t);
        }

        const round = (await c.query(
          "select coalesce(max(round_no),0)+1 as n from negotiation_round where deal_id=$1 and side=$2",
          [s.id, args.side])).rows[0].n;
        // submarket_condition joins the column list ONLY when it was given, so this
        // verb keeps working byte-for-byte on a database where 0063 has not been
        // applied yet. The Worker deploy and the migration are two separate human
        // taps and either can come first.
        const params = [s.id, round, args.side, args.proposed_on || null, args.rate_amount || null,
          args.rate_basis || null, args.ti_amount || null, args.ti_basis || null,
          args.free_rent_months || null, args.term_months || null, args.options_note || null,
          args.escalator || null, args.expires_on || null, args.note || null, actor.id];
        if (submarket) params.push(submarket);
        const r = await c.query(
          `insert into negotiation_round (deal_id, round_no, side, proposed_on, rate_amount, rate_basis,
           ti_amount, ti_basis, free_rent_months, term_months, options_note, escalator, expires_on,
           note, created_by, updated_by${submarket ? ", submarket_condition" : ""})
         values ($1,$2,$3,coalesce($4::date,current_date),$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15${submarket ? ", $16" : ""})
         returning id, round_no`, params);

        const claimsOut = [];
        for (let i = 0; i < claims.length; i++) {
          const cl = claims[i], t = claimTypes[i];
          const ins = await c.query(
            `insert into negotiation_claim (round_id, claim_type, stated_floor, stated_floor_basis,
             quote, note, source, created_by)
           values ($1,$2,$3,$4,$5,$6,'stated',$7) returning id`,
            [r.rows[0].id, t.slug, cl.stated_floor ?? null, cl.stated_floor_basis || null,
             cl.quote || null, cl.note || null, actor.id]);
          claimsOut.push({ claim_id: ins.rows[0].id, type: t.slug, label: t.label,
                           falsifiable: t.falsifiable, reversal_test: t.reversal_test });
        }

        await writeEvent(c, actor, "record-counter", "deal", s.id,
          { new: { round, side: args.side, rate: args.rate_amount, basis: args.rate_basis,
                   submarket_condition: submarket, claims: claimsOut.map(x => x.type) },
            idempotency_key: args.idempotency_key });

        const notes = [];
        if (claimsOut.some(x => !x.falsifiable))
          notes.push("One or more of these claims is NOT falsifiable (" +
            claimsOut.filter(x => !x.falsifiable).map(x => x.type).join(", ") +
            ") — logged so the tactic is visible in the history, and permanently excluded from " +
            "every score. Nothing we could ever observe would contradict it.");
        if (!submarket)
          notes.push("No submarket_condition on this round. That is fine — the scorecard reads " +
            "the latest non-null value on the deal — but if nobody has ever recorded one, " +
            "leverage and skill stay welded together in the numbers.");
        if (claimsOut.length)
          notes.push("Each claim is checked against the rounds that come AFTER it; " +
            "reversal_test says how. Nothing is scored now.");

        return { ok: true, round_id: r.rows[0].id, round_no: r.rows[0].round_no,
                 submarket_condition: submarket, claims: claimsOut,
                 note: notes.join(" ") || undefined };
      }),
    },
  };
}
