import { UUID_RE, resolveSubject } from "./verb-support.js";
import { auditIdentity, withEnvelope, writeEvent } from "./versioned-write.js";
import { ToolError } from "./tool-error.js";

// [loop #278] The ONE place a decision gets mirrored onto the record it governs.
// log-decision calls it at creation and update-decision calls it after the fact, so
// attaching late and attaching at the time produce byte-identical rows — a manual path
// and an automated path that do the same job must be the same code (rule a8c55a47).
//
// `about` takes a single ref or several. Several is not a nicety: the real population
// includes rulings like the 2026-08-06 vendor merges, one ruling settling V-GC-001,
// V-MSC-024 and T-004 together, which a single-ref parameter can only record a third of.
//
// Re-attaching is safe. A mirror already written for the same (subject, decision) pair
// is skipped, not duplicated, so calling update-decision twice does not stack pointers
// on a timeline.
async function mirrorDecision(client, actor, d) {
  const refs = (Array.isArray(d.about) ? d.about : [d.about])
    .map(r => String(r || "").trim()).filter(Boolean);
  if (!refs.length) return [];

  // Resolve EVERY ref before writing anything: a bad ref in position three must not
  // leave two pointers behind from positions one and two.
  const seen = new Map();
  for (const ref of refs) {
    const s = await resolveSubject(client, ref);
    seen.set(`${s.type}:${s.id}`, { ...s, ref });   // dedupe refs naming one record
  }

  const attached = [];
  for (const s of seen.values()) {
    // A RETRACTED pointer does not count as already-attached. Re-attaching after a
    // detach-decision writes a fresh live pointer and leaves the retracted one standing,
    // so the timeline shows the whole history — attached, retracted, attached again —
    // rather than quietly resurrecting a row somebody deliberately struck through.
    const dup = await client.query(
      `select 1 from event
        where subject_type = $1 and subject_id = $2 and field = 'decision'
          and new_value->>'decision_id' = $3
          and coalesce((new_value->>'retracted')::boolean, false) = false limit 1`,
      [s.type, s.id, d.decision_id]);
    if (dup.rows.length) { attached.push({ ...s, already: true }); continue; }

    await writeEvent(client, actor, "log-decision", s.type, s.id, {
      occurred_at: d.occurred_at || null,
      field: "decision",
      new: { summary: d.title, decision_id: d.decision_id, decision_event_id: d.decision_event_id },
      human_quote: d.human_quote || null,
      agent_rationale: d.rationale || null,
      idempotency_key: d.idempotency_key,
    });
    attached.push({ ...s, already: false });
  }
  return attached;
}

// ---------- following a decision pointer the system itself printed ----------
//
// SAME DEFECT CLASS AS resolveRuleId ABOVE, and the same day (defect 69fb49b1,
// 2026-08-22). `update-decision` took `decision_id` straight into `where
// subject_id = $1`, a uuid column, so the EIGHT-CHARACTER form this system
// prints everywhere — loop source_notes cite "decisions f58ffba8, 91020f79",
// log-decision's own envelope, decision-history's rendered index — could not be
// passed back into the verb that corrects them. It died as 22P02, which until
// pull request 465 surfaced as a bare "internal error" naming nothing.
//
// A pointer nobody can follow is not a pointer. That reasoning is written out at
// length above resolveRuleId and applies here unchanged; the only reason this is
// a second function rather than one shared one is that rules and decisions live
// in different tables and a prefix must be matched against the right one.
//
// AMBIGUITY IS REPORTED, NEVER GUESSED, for a sharper reason than the rules
// case: amending the wrong decision rewrites a settled ruling's stated
// rationale, and the entry it overwrote gives no sign it was ever different.
async function resolveDecisionId(c, value, field = "decision_id") {
  const raw = String(value || "").trim();
  if (!raw) throw new ToolError({ error: "decision_id_required", field });

  if (UUID_RE.test(raw)) return raw;

  if (!/^[0-9a-f]{4,}$/i.test(raw))
    throw new ToolError({ error: "decision_id_malformed", field, got: raw,
      hint: "a decision id is either the full 36-character uuid or the 8-character short " +
            "form this system prints, e.g. 'f58ffba8'" });

  const m = await c.query(
    `select distinct on (subject_id) subject_id::text as id,
            left(coalesce(new_value->>'title', '(untitled)'), 70) as title,
            recorded_at
       from event
      where verb = 'log-decision' and subject_id::text like $1 || '%'
      order by subject_id, recorded_at`, [raw.toLowerCase()]);
  if (!m.rows.length)
    throw new ToolError({ error: "decision_not_found", field, got: raw,
      hint: "no decision id begins with that prefix — check decision-history, and note " +
            "that the id log-decision returns is the decision_id, not the event_id" });
  if (m.rows.length > 1)
    throw new ToolError({ error: "ambiguous_decision_id", field, got: raw,
      candidates: m.rows.map(r => ({ decision_id: r.id, title: r.title })),
      hint: "that prefix matches more than one decision; pass more characters or the full uuid" });
  return m.rows[0].id;
}

export function decisionTools() {
  return {
  // ---------- decisions (the verb 0031 named and nobody built) ----------
    // 0031 built v_decision_entry as "the read side of decision-history-as-events"
    // and said verb='log-decision' is what "a future present-tense verb would
    // write". That verb was never written, so decision-history.md could only ever
    // render the one-time import — which is why its export has last_ok = null to
    // this day, and why a 2026-08-02 session with a settled decision to record had
    // nowhere to put it and hand-wrote a DECISIONS.md instead. The generated header
    // on decision-history.md has been telling readers "to record one, use the verb"
    // the whole time. This is that verb.
    //
    // Shape is dictated by v_decision_entry, not invented: an event row carrying
    // title/quote_absent/provenance in new_value plus human_quote and
    // agent_rationale, and a record_source row keyed '<source_file>#<session_key>'
    // under source_system='decision-history'. Grouping stays the render's job
    // (rule 29, one entry per session) — this verb exposes session_key and groups
    // nothing, exactly as the view does.
    // [0070] The Source Material capture log as a verb. The markdown INDEX was a
    // table wearing prose: append-only rows plus a check-before-capture dedup step
    // that two concurrent sessions could race. The check now runs inside the write
    // transaction and cannot.
    "log-capture": {
      discoveryOrder: 68,
      write: true,
      description: "Log a learning-source capture into the Source Material capture log — one row per source (podcast, article, video, portal session, thread). Its ONE job is the dedup guard: it CHECKS for an existing capture first (exact URL, then session-name similarity) and returns candidates INSTEAD of inserting when found — if it's already here, it's already absorbed; pass force_new:true only after a human confirms it is genuinely a different source. The knowledge itself NEVER lives here: it merges into the domain playbooks per the knowledge policy, and merge_note records where it merged and what was declined, honestly. status: merged (absorbed), declined (evaluated, not adopted — say why), queued (spotted, capture later; merge_note may be empty only here). Renders to DNA/Marketing/Source Material/INDEX.md — never hand-edit that file.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        session: { type: "string", description: "what the source is, in the words a future dedup check would search — include the author/platform and [public source] / [colleague source] style markers as before" },
        merge_note: { type: "string", description: "where it merged and what was declined, with reasons. Required unless status is queued." },
        captured_on: { type: "string", description: "YYYY-MM-DD; defaults today" },
        source_url: { type: "string", description: "primary link when one exists — it becomes the exact-match dedup key" },
        visibility: { type: "string", enum: ["public","member_gated","colleague","internal"], default: "public" },
        status: { type: "string", enum: ["merged","declined","queued"], default: "merged" },
        force_new: { type: "boolean", description: "insert despite dedup candidates — only after a human confirmed it is a different source" } },
        required: ["idempotency_key","session"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "log-capture", args, async () => {
        const status = args.status || "merged";
        if (status !== "queued" && !(args.merge_note && args.merge_note.trim()))
          throw new ToolError({ error: "missing_merge_note",
            hint: "a merged or declined capture must say where it went or why it was declined; only a queued row may be empty" });
        if (!args.force_new) {
          const cand = await c.query(
            `select captured_on, session, status, left(merge_note, 160) as merge_note
             from source_capture
            where ($1::text is not null and source_url is not null
                   and lower(source_url) = lower($1))
               or session % $2
            order by similarity(session, $2) desc limit 5`,
            [args.source_url || null, args.session]);
          if (cand.rows.length)
            return { needs_confirm: true, candidates: cand.rows,
                     hint: "similar captures exist — if it's here, it's already absorbed; resubmit force_new:true only for a genuinely different source" };
        }
        const r = await c.query(
          `insert into source_capture
           (captured_on, session, source_url, visibility, status, merge_note,
            created_by, updated_by)
         values (coalesce($1::date, current_date),$2,$3,$4,$5,$6,$7,$7)
         returning id, captured_on`,
          [args.captured_on || null, args.session, args.source_url || null,
           args.visibility || "public", status, args.merge_note || "", actor.id]);
        await writeEvent(c, actor, "log-capture", "source_capture", r.rows[0].id,
          { new: { session: args.session, status }, idempotency_key: args.idempotency_key });
        return { ok: true, capture_id: r.rows[0].id, captured_on: r.rows[0].captured_on,
                 status, renders_into: "DNA/Marketing/Source Material/INDEX.md" };
      }),
    },

    "log-decision": {
      discoveryOrder: 69,
      write: true,
      description: "Record a SETTLED decision and its rationale — the thing that stops it being relitigated next session. Writes a decision event (subject_type='decision', verb='log-decision') that v_decision_entry reads and decision-history.md renders; never hand-edit that file. NOT the same as add-loop marker:'decision', which is an OPEN question awaiting a ruling, and not the same as teach, which stores a standing rule that binds future sessions. Use this when a fork has been closed: what was decided, why, what lost. human_quote is Joe's or Dell's literal words when he said them — omit it and the entry is flagged quote_absent rather than paraphrase being passed off as a quote. PASS `about` WHENEVER THE RULING CONCERNS ONE RECORD: a decision without it is filed in decision-history and reachable from nothing, which is how 363 rulings ended up invisible to catch-me-up on the very deals they governed. PRICE IT WHEN THE RULING CHANGES HOW THE SYSTEM WORKS: cost_delta and quality_delta record what a build cost and what it bought, together or not at all, because a build with no before-and-after number can never be shown to have worked, only asserted to have. If the after-measure does not exist yet, log it unpriced and add both halves later with update-decision.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        title: { type: "string", description: "the decision itself, in one line, stated as settled" },
        rationale: { type: "string", description: "why — including alternatives considered and why they lost, and any condition that would reopen it" },
        about: { description: "the record(s) this ruling is ABOUT — one ref or several: \"C-127\" or [\"V-GC-001\",\"V-MSC-024\",\"T-004\"]. Mirrors the decision onto each record's timeline so catch-me-up on it shows what was decided. Omit only for a genuinely system-wide ruling that belongs to no one record, which most build and doctrine rulings are; a bad ref is refused and NOTHING is written, so a mistyped ref never leaves a decision behind. Forgot it? update-decision takes `about` too.",
          oneOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] },
        human_quote: { type: "string", description: "the partner's literal words, when he said them. Never paraphrase into this field." },
        session_key: { type: "string", description: "groups entries per session (rule 29). Defaults to <date>-<actor>." },
        provenance: { type: "string", description: "where this came from — a session, a call, a document" },
        cost_delta: { type: "string", description: "WHAT IT COST, in the unit that actually matters for this build — model calls, dollars, minutes of a partner's attention, added latency. Free text on purpose, because the unit changes per build and forcing a number would force a fake one. Must be passed together with quality_delta: half a price is not a price. Example: \"+60% inference cost per finished draft, 3 model calls where there was 1\"." },
        quality_delta: { type: "string", description: "WHAT IT BOUGHT, stated as before and after against a named baseline, never as an after-value alone. A delta with no baseline is the same unfalsifiable claim the skeptic chair already refuses on client work. Must be passed together with cost_delta. Example: \"approval 45% -> 82.5%, measured on the same 40 drafts\"." },
        occurred_at: { type: "string", description: "when it was decided; defaults now" } },
        required: ["idempotency_key","title","rationale"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "log-decision", args, async () => {
        // [loop #278] Resolve `about` BEFORE the decision is inserted. resolveSubject
        // throws not_found / needs_disambiguation, and a throw here must leave no
        // decision behind — an orphaned ruling written by a mistyped ref is exactly the
        // record this loop exists to stop creating. Resolution happens twice on the happy
        // path (here to validate, again inside mirrorDecision to write); that is a couple
        // of indexed reads against correctness, which is not a trade worth making.
        const aboutRefs = args.about
          ? (Array.isArray(args.about) ? args.about : [args.about]).map(r => String(r || "").trim()).filter(Boolean)
          : [];
        for (const ref of aboutRefs) await resolveSubject(c, ref);

        // [idea 68, 0085] BOTH HALVES OF A PRICE OR NEITHER. A cost with no
        // quality number is a complaint and a quality number with no cost is a
        // boast; either alone is the selective reporting the discipline exists to
        // stop, so one without the other is refused rather than stored. This is a
        // doctrine rule about honest reporting, which is why it lives here and not
        // in a CHECK constraint — the same reasoning R-40a applies to grouping.
        const costDelta = (args.cost_delta || "").trim() || null;
        const qualityDelta = (args.quality_delta || "").trim() || null;
        if (Boolean(costDelta) !== Boolean(qualityDelta))
          throw new ToolError({ error: "half_a_price",
            got: costDelta ? "cost_delta only" : "quality_delta only",
            hint: "pass cost_delta AND quality_delta together, or neither. What a build cost is only meaningful beside what it bought, and a quality claim with no cost beside it is unfalsifiable. If the other half genuinely is not known yet, log the decision unpriced and add both later with update-decision." });
        const decisionId = (await c.query("select gen_random_uuid() as id")).rows[0].id;
        const identity = auditIdentity(actor);
        const r = await c.query(
          `insert into event (occurred_at, actor_id, verb, subject_type, subject_id,
           new_value, cause, human_quote, agent_rationale, idempotency_key, via, client_id,
           organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class, correlation_id)
         values (coalesce($1::timestamptz, now()), $2, 'log-decision', 'decision', $3,
                 $4, 'human_stated', $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
         -- to_char, not ::date: node-postgres hands a ::date back as a JS Date, and
         -- interpolating that into session_key produced
         -- "Sun Aug 02 2026 00:00:00 GMT+0000 (Coordinated Universal Time)-joe"
         -- on the first live decision. A text date interpolates as a text date.
         returning id, to_char(coalesce($1::timestamptz, now()), 'YYYY-MM-DD') as entry_date`,
          [args.occurred_at || null, actor.id, decisionId,
           JSON.stringify({ title: args.title,
                            quote_absent: !args.human_quote,
                            provenance: args.provenance || null,
                            // Keys are OMITTED when unpriced rather than written as
                            // null, because v_decision_entry.priced tests key
                            // PRESENCE (new_value ? 'cost_delta'). A null-valued key
                            // would read as priced-with-no-price.
                            ...(costDelta ? { cost_delta: costDelta,
                                              quality_delta: qualityDelta } : {}) }),
           args.human_quote || null, args.rationale, args.idempotency_key,
           actor.via || null, actor.client_id || null, identity.organization_tenant_id,
           identity.sponsoring_human_slug, identity.personal_scope, identity.authorization_class,
           identity.correlation_id]);

        const ev = r.rows[0];
        const sessionKey = args.session_key || `${ev.entry_date}-${actor.slug}`;
        // source_file 'live' distinguishes verb-written entries from the imported
        // ones, whose key carries the markdown file they came out of.
        //
        // The event id is the THIRD segment and it is load-bearing: record_source is
        // unique on (source_system, external_key) and v_decision_entry JOINS through
        // it, so a key of just 'live#<session>' would make the second decision of any
        // session collide and vanish from the render entirely. The view reads
        // source_file from segment 1 and session_key from segment 2, so a third
        // segment costs nothing and buys per-decision uniqueness.
        await c.query(
          `insert into record_source (entity_type, entity_id, source_system, external_key)
         values ('event', $1, 'decision-history', $2)
         on conflict (source_system, external_key) do nothing`,
          [ev.id, `live#${sessionKey}#${ev.id}`]);

        // [loop #278] Mirror the ruling onto the record it governs. A SECOND event row,
        // not a rewrite of the first: v_decision_entry keys off subject_type='decision'
        // and decision-history.md must keep rendering exactly as it did, so the decision
        // row is left untouched and the record gets its own pointer at it.
        //
        // event.idempotency_key is NON-unique by design (0001, [A1]: "one tool call may
        // write several event rows; replay is tool_call's job"), so both rows carry the
        // same key and the tool_call replay table still guards the call as one unit.
        //
        // new_value.summary is the 0082 hook: v_subject_timeline reads it as the row's
        // summary, so catch-me-up on the record shows WHAT was decided instead of the
        // bare verb. decision_id and event_id ride along so the full entry is one hop away.
        const attached = await mirrorDecision(c, actor, {
          about: aboutRefs, decision_id: decisionId, decision_event_id: ev.id,
          title: args.title, human_quote: args.human_quote, rationale: args.rationale,
          occurred_at: args.occurred_at, idempotency_key: args.idempotency_key });

        return { ok: true, decision_id: decisionId, event_id: ev.id,
                 session_key: sessionKey, quote_absent: !args.human_quote,
                 about: attached.map(a => ({ type: a.type, id: a.id, ref: a.ref })),
                 renders_into: "00_Context/decision-history.md",
                 ...(attached.length ? {} : { hint: "no `about` ref given — this ruling is reachable from decision-history only, not from any record's timeline. That is correct for a build or doctrine ruling and wrong for anything about a client, lead, vendor or deal. update-decision takes `about` if you want to attach it later." }) };
      }),
    },

  // ---------- correcting a decision already recorded ----------
    // Added 2026-08-02 because two decision entries landed with quote_absent:true when the
    // session malformed the human_quote parameter twice. The flag then read as "no quote
    // existed" when the truth was that one existed and was fumbled — a defective record, not
    // history worth preserving. Joe: "create an update-decision verb and then update them".
    //
    // MUTATES THE ENTRY, APPENDS THE AMENDMENT. The decision event itself is corrected in
    // place so v_decision_entry and decision-history.md read the truth, AND a separate
    // amend-decision event records what changed and why. Correcting the record without a
    // trace would be the worse half of both options.
    "update-decision": {
      discoveryOrder: 70,
      write: true,
      description: "Correct a decision entry already recorded by log-decision — a wrong or missing title, rationale, human_quote or provenance — or ATTACH it to the record(s) it governs after the fact with `about`. Use for a DEFECTIVE record (a quote that was lost, a rationale that stated something untrue), never to rewrite what was actually decided: a decision that CHANGED is a new log-decision, because the old one really was the call at the time. Attaching is different from correcting and is always safe: it adds a pointer on the record's timeline, changes nothing about the entry itself, and re-attaching the same record is a no-op rather than a second pointer. Pass only the fields you are correcting. Re-derives quote_absent from whether a quote is present afterwards, and appends an amend-decision event recording the change.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        decision_id: { type: "string", description: "the decision this corrects — the full uuid log-decision returned, OR the 8-character short form this system prints in loop source_notes and decision-history, e.g. 'f58ffba8'. A prefix matching more than one decision is refused with the candidates listed rather than guessed at." },
        title: { type: "string" }, rationale: { type: "string" },
        about: { description: "attach this ruling to the record(s) it is ABOUT, now — one ref or several: \"C-063\" or [\"V-GC-001\",\"V-MSC-024\",\"T-004\"]. This is how a decision logged without `about` gets connected later. Only pass records the ruling is genuinely ABOUT: a session-level build decision that merely MENTIONS a ref in its rationale is not about that record, and attaching it there puts noise on a timeline a human reads before a client conversation.",
          oneOf: [{ type: "string" }, { type: "array", items: { type: "string" } }] },
        human_quote: { type: "string", description: "the partner's literal words. Never paraphrase into this field." },
        provenance: { type: "string" },
        cost_delta: { type: "string", description: "WHAT IT COST — see log-decision. This is the path for pricing a build whose numbers were not known at the moment it shipped, which is the common case: you rarely have the after-measure on the day. Must be passed together with quality_delta." },
        quality_delta: { type: "string", description: "WHAT IT BOUGHT, before and after against a named baseline — see log-decision. Must be passed together with cost_delta." },
        reason: { type: "string", description: "why the entry needed correcting — recorded on the amendment" } },
        required: ["idempotency_key","decision_id"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "update-decision", args, async () => {
        // Resolved BEFORE the read, so the short form the system prints is a first-class
        // argument rather than a 22P02 on a uuid column (defect 69fb49b1).
        const decisionId = await resolveDecisionId(c, args.decision_id);
        const cur = (await c.query(
          `select id, new_value, human_quote, agent_rationale from event
          where subject_id = $1 and verb = 'log-decision' limit 1`, [decisionId])).rows[0];
        if (!cur) throw new ToolError({ error: "decision_not_found", decision_id: args.decision_id,
          hint: "pass the decision_id log-decision returned, not the event_id" });

        const nv = cur.new_value || {};
        const quote = args.human_quote !== undefined ? args.human_quote : cur.human_quote;

        // [idea 68, 0085] Same both-or-neither rule log-decision enforces, applied to
        // the after-the-fact path — which is the COMMON path for pricing, because the
        // after-measure rarely exists on the day a build ships. Judged against the
        // MERGED state, not the arguments alone, so passing one half to a decision
        // that already carries the other is a completion rather than a violation.
        const mergedCost = args.cost_delta !== undefined
          ? ((args.cost_delta || "").trim() || null) : (nv.cost_delta || null);
        const mergedQuality = args.quality_delta !== undefined
          ? ((args.quality_delta || "").trim() || null) : (nv.quality_delta || null);
        if (Boolean(mergedCost) !== Boolean(mergedQuality))
          throw new ToolError({ error: "half_a_price",
            got: mergedCost ? "cost_delta only" : "quality_delta only",
            hint: "a decision carries both halves of a price or neither. Pass whichever half is missing in the same call." });

        const next = { ...nv,
          title: args.title !== undefined ? args.title : nv.title,
          provenance: args.provenance !== undefined ? args.provenance : nv.provenance,
          quote_absent: !quote };
        // Presence, not null — v_decision_entry.priced tests for the key itself.
        if (mergedCost) { next.cost_delta = mergedCost; next.quality_delta = mergedQuality; }
        else { delete next.cost_delta; delete next.quality_delta; }

        await c.query(
          `update event set new_value = $1, human_quote = $2, agent_rationale = $3 where id = $4`,
          [JSON.stringify(next), quote || null,
           args.rationale !== undefined ? args.rationale : cur.agent_rationale, cur.id]);

        // [loop #278] Attach after the fact, through the SAME helper log-decision uses, so
        // a late attachment is indistinguishable from one made at the time. The mirror
        // carries the CURRENT title, which is right: an entry corrected here should not
        // leave the old wording sitting on a record's timeline.
        const attached = await mirrorDecision(c, actor, {
          about: args.about, decision_id: decisionId, decision_event_id: cur.id,
          title: next.title, human_quote: quote,
          rationale: args.rationale !== undefined ? args.rationale : cur.agent_rationale,
          idempotency_key: args.idempotency_key });

        const changed = ["title","rationale","human_quote","provenance","cost_delta","quality_delta"]
          .filter(f => args[f] !== undefined);
        await writeEvent(c, actor, "amend-decision", "decision", decisionId,
          { old: { quote_absent: nv.quote_absent },
            new: { fields: changed, quote_absent: !quote,
                   ...(attached.length ? { attached_to: attached.map(a => a.ref) } : {}) },
            agent_rationale: args.reason || null, idempotency_key: args.idempotency_key });

        return { ok: true, decision_id: decisionId, amended: changed, quote_absent: !quote,
                 about: attached.map(a => ({ type: a.type, id: a.id, ref: a.ref, already_attached: a.already })) };
      }),
    },

  // ---------- taking a decision back off a record ----------
    // Joe, 2026-08-09, on whether a wrong pointer should be deletable: "okay then keep it
    // for the audit log but just make sure its clear".
    //
    // So this does NOT delete. The pointer row stays exactly where it is, because someone
    // attaching a ruling to the wrong client is a thing that happened and the event log is
    // where things that happened live. What changes is that the row now SAYS SO, in the one
    // field a human reads: its timeline summary is struck through with RETRACTED and the
    // reason, so the ruling can never be mistaken for a live one at a glance.
    //
    // Same mutate-in-place-plus-append-the-amendment shape update-decision already uses:
    // the pointer is corrected where it is read, and a separate detach-decision event
    // records who retracted it and why.
    "detach-decision": {
      discoveryOrder: 71,
      write: true,
      description: "Take a decision back off a record it was wrongly attached to. NOTHING IS DELETED: the pointer stays on the timeline as a permanent audit row, restated so a reader sees at a glance that it was retracted and why — a wrong attachment is a thing that happened, and hiding it would be the worse record. Use when a ruling was attached to a client, lead, vendor or party it is not actually about. NOT for a decision that has CHANGED (that is a new log-decision) and NOT for fixing the entry's own wording (that is update-decision). Re-attaching afterwards is allowed and writes a fresh live pointer beside the retracted one, so the timeline shows attached → retracted → attached rather than a row quietly coming back to life.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        decision_id: { type: "string", description: "the decision the pointer carries — the full uuid, or the 8-character short form this system prints. Resolved the same way update-decision resolves it, so a ref read off a timeline can be passed straight back." },
        from: { type: "string", description: "the record to take it off — C-127 / L-204 / V-CPA-006 / P-0948 / a deal name" },
        reason: { type: "string", description: "why it does not belong there. REQUIRED — this is what the retracted row shows a future reader, and 'wrong' with no cause is how the same mistake gets made again." } },
        required: ["idempotency_key","decision_id","from","reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "detach-decision", args, async () => {
        if (!String(args.reason || "").trim())
          throw new ToolError({ error: "missing_reason",
            hint: "say why the ruling does not belong on this record; the retracted row carries it forward" });

        // The pointer stores the decision_id as TEXT, so a short form here never
        // raised 22P02 the way update-decision did — it simply matched nothing and
        // reported not_attached, which reads as "no such pointer" rather than "wrong
        // form of id". Same defect, quieter symptom, same one-line resolution.
        const decisionId = await resolveDecisionId(c, args.decision_id);
        const s = await resolveSubject(c, args.from);
        const ptr = (await c.query(
          `select id, new_value from event
          where subject_type = $1 and subject_id = $2 and field = 'decision'
            and new_value->>'decision_id' = $3
          order by coalesce((new_value->>'retracted')::boolean, false), recorded_at desc
          limit 1`,
          [s.type, s.id, decisionId])).rows[0];
        if (!ptr) throw new ToolError({ error: "not_attached", decision_id: decisionId, from: args.from,
          hint: "this decision has no pointer on that record — check catch-me-up on it first" });

        const nv = ptr.new_value || {};
        if (nv.retracted)
          return { ok: true, decision_id: decisionId, from: args.from,
                   already_retracted: true, reason: nv.retracted_reason || null,
                   hint: "this pointer was already retracted; nothing changed" };

        // The summary is the ONLY field v_subject_timeline surfaces, so the retraction has
        // to live there or a reader never sees it. The original text is kept verbatim
        // behind the marker and preserved whole in summary_before_retraction.
        const original = nv.summary || "";
        const next = { ...nv,
          retracted: true,
          retracted_reason: args.reason,
          retracted_by: actor.slug,
          summary_before_retraction: original,
          summary: `RETRACTED — not about this record (${args.reason}) — was: ${original}` };

        await c.query("update event set new_value = $1 where id = $2",
          [JSON.stringify(next), ptr.id]);

        await writeEvent(c, actor, "detach-decision", s.type, s.id, {
          field: "decision_retracted",
          old: { summary: original, decision_id: decisionId },
          new: { summary: `retracted a decision pointer: ${args.reason}`, decision_id: decisionId },
          agent_rationale: args.reason, idempotency_key: args.idempotency_key });

        return { ok: true, decision_id: decisionId,
                 from: { type: s.type, id: s.id, ref: args.from },
                 retracted: true, retained_as_audit_row: true, pointer_event_id: ptr.id };
      }),
    },
  };
}
