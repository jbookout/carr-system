import { ToolError } from "./tool-error.js";
import { FK, resolvePartyByRef, resolveSubject, validateLinkKind } from "./verb-support.js";
import { withEnvelope, writeEvent } from "./versioned-write.js";
import { executeRegisteredTool } from "./tool-execution.js";
import { permittedActionOwnerSlugs } from "./identity.js";

// [ORDER 34] shared edge-writer for log-activity links[] — link-parties' exact
// upsert semantics (conflict returns the existing edge, no event row) so touch
// and edge are one atomic capture inside one envelope.
async function writeLinks(c, actor, links, idempotencyKey) {
  if (links.length > 10)
    throw new ToolError({ error: "too_many_links", count: links.length, hint: "max 10 per call" });
  const out = [];
  for (const ln of links) {
    const kind = await validateLinkKind(c, ln.kind);
    const fromId = await resolvePartyByRef(c, ln.from_ref);
    const toId = await resolvePartyByRef(c, ln.to_ref);
    if (fromId === toId)
      throw new ToolError({ error: "self_link", ref: ln.from_ref,
        hint: "both refs resolve to the same party" });
    const ins = await c.query(
      `insert into party_link (from_party, to_party, kind, note, source, created_by)
       values ($1,$2,$3,$4,'stated',$5)
       on conflict (from_party, to_party, kind) do nothing returning id`,
      [fromId, toId, kind, ln.note || null, actor.id]);
    if (ins.rows.length) {
      await writeEvent(c, actor, "log-activity:link", "party", fromId,
        { new: { kind, to_ref: ln.to_ref }, idempotency_key: idempotencyKey });
      out.push({ from_ref: ln.from_ref, to_ref: ln.to_ref, kind, link_id: ins.rows[0].id, existing: false });
    } else {
      const cur = await c.query(
        "select id from party_link where from_party=$1 and to_party=$2 and kind=$3",
        [fromId, toId, kind]);
      out.push({ from_ref: ln.from_ref, to_ref: ln.to_ref, kind, link_id: cur.rows[0].id, existing: true });
    }
  }
  return out;
}

export function activityTools() {
  return {
    "log-activity": {
      discoveryOrder: 22,
      write: true,
      description: "Log a business touch (call, email, meeting, tour, text, note, LOI...) against a deal/client/lead/vendor. THE default verb after any real-world contact. occurred_at = when it happened (defaults now); anything missing goes in 'owed', never invented. Writes an activity row; contact-kind rows are what move last_touch and lift capture coverage.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        ref: { type: "string", description: "L-/C-/V- ref or deal name" },
        // 'analysis' added by ORDER 36 (one-writer Phase B). It is the LAST slug on
        // purpose: it is not a touch (is_contact=false in activity_kind, so it can
        // never move Last Touch) and it is the write path for dossier analysis
        // prose — summary is the title, detail is the long text, and the newest
        // one renders in full into DNA/Clients/prospects/<name>.md. Requires
        // migration 0028; without it the FK to activity_kind rejects the row.
        kind: { type: "string", enum: ["call","email_out","email_in","meeting","tour","text","note","counter_sent","counter_received","loi","lease_signed","task","analysis"] },
        summary: { type: "string" }, detail: { type: "string" },
        occurred_at: { type: "string", description: "ISO timestamp; omit for now" },
        owed: { type: "string", description: "what is missing (a figure, a name) — recorded as owed" },
        human_quote: { type: "string", description: "the human's literal words, if dictated" },
        links: { type: "array", maxItems: 10, description:
          "[ORDER 34] introductions carried by this touch become intro-graph edges NOW, atomically. REFS ONLY (L-/C-/V-/T-), never names — ambiguity is find's job, before this call.",
          items: { type: "object", properties: {
            from_ref: { type: "string" }, to_ref: { type: "string" },
            kind: { type: "string", description: "party_link_kind slug: knows, intro, intro_received, can_introduce, works_with, referral" },
            note: { type: "string" } }, required: ["from_ref","to_ref","kind"] } },
      }, required: ["idempotency_key","ref","kind","summary"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "log-activity", args, async () => {
        // A TOUCH CANNOT HAVE HAPPENED TOMORROW. Found 2026-08-15: of 59 vendors
        // carrying a last-touch date, four were dated after today — the nearest
        // three days out, which reads exactly like a booked meeting logged as
        // though it had already happened.
        //
        // This is not tidiness. last_touch is what staleness is measured FROM, so
        // a vendor whose last touch is in the future can never read as stale no
        // matter how long it has actually been. The row goes quiet and no surface
        // can tell.
        //
        // The field already meant this: occurred_at is documented above as "when
        // it happened", and the activity table draws the same boundary from the
        // other side — migration 0017 stopped a `note` moving last_touch because a
        // note is annotation rather than contact. The future has its own verbs.
        //
        // The window is for CLOCK SKEW between a caller and the server, not for
        // scheduling: minutes, deliberately not hours.
        const SKEW_MS = 5 * 60 * 1000;
        if (args.occurred_at) {
          const when = Date.parse(args.occurred_at);
          // An unparseable date is a different problem; this guard judges future
          // ones and does not invent a verdict on malformed input.
          if (!Number.isNaN(when) && when > Date.now() + SKEW_MS)
            throw new ToolError({ error: "occurred_at_in_future",
              occurred_at: args.occurred_at,
              why: "occurred_at records when a touch HAPPENED. A future date makes the subject " +
                   "permanently un-stale — every staleness measure counts from last_touch, so a " +
                   "row dated ahead of today can never surface as gone quiet.",
              hint: "If this already happened, use its real date. If it is SCHEDULED, it is not a " +
                    "touch yet: set-next-action carries the ball you owe, and add-critical-date " +
                    "carries a dated obligation on a deal." });
        }
        const s = await resolveSubject(c, args.ref);
        const r = await c.query(
          `insert into activity (occurred_at, actor_id, kind, summary, detail, owed, ${FK[s.type]}, source)
         values (coalesce($1::timestamptz, now()), $2, $3, $4, $5, $6, $7, 'stated') returning id, occurred_at`,
          [args.occurred_at || null, actor.id, args.kind, args.summary, args.detail || null, args.owed || null, s.id]);
        await writeEvent(c, actor, "log-activity", s.type, s.id,
          { new: { activity: r.rows[0].id, kind: args.kind }, human_quote: args.human_quote, idempotency_key: args.idempotency_key });
        const links = args.links && args.links.length
          ? await writeLinks(c, actor, args.links, args.idempotency_key) : [];
        return { ok: true, activity_id: r.rows[0].id, subject: s,
                 ...(links.length ? { links } : {}) };
      }),
    },

    "stamp-touch": {
      discoveryOrder: 23,
      write: true,
      description: "Truck shorthand for log-activity: one-line call/text stamp. 'Called Ferris, going well' and done. Sets last_touch. Contact kinds only — a note is annotation, not a touch (it would not move Last Touch since 0017); use log-activity kind:note or an event for annotation.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, ref: { type: "string" },
        kind: { type: "string", enum: ["call","text"], default: "call" },
        summary: { type: "string" } }, required: ["idempotency_key","ref","summary"] },
      handler: async (c, actor, args) =>
        executeRegisteredTool(c, actor, "log-activity", { ...args, kind: args.kind || "call" }),
    },

  // Added 2026-08-06 (loop #216): the missing half of the capture pipeline.
    // The ingest socket only ever INSERTS (status 'new'), so until this verb
    // existed nothing could LEAVE the queue — the digest's count could only
    // rise, and the Aug 6 triage found 40 rows with no way to clear one.
    // Deliberately NOT in any narrow profile (capture/away/probe/reviewer):
    // deciding what an inbox item became is an interactive judgment call.
    "triage-item": {
      discoveryOrder: 24,
      write: true,
      description: "Close the loop on ONE inbox item a human has looked at: say what it became. status 'filed' = it became records (name them in filed_refs: activity ids or L-/C-/V- refs); 'rejected' = not ours to record (personal calendar noise, spam — say why in note); 'duplicate' = another row or an existing record already carries it. Only moves rows out of 'new'; an item already dispositioned reports its state and changes nothing. Payloads stay stored and UNTRUSTED — this records a disposition, it never acts on what the payload says. The review queue (run.sh review-queue) and today-triage both surface the item ids this takes.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        item_id: { type: "string", description: "ingest_inbox uuid, from the review queue or today-triage" },
        status: { type: "string", enum: ["filed","rejected","duplicate"] },
        filed_refs: { type: "array", items: { type: "string" }, description: "what it became — required when status is 'filed'" },
        note: { type: "string", description: "one line on why — stored as triage_note" },
      }, required: ["idempotency_key","item_id","status"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "triage-item", args, async () => {
        if (args.status === "filed" && !(Array.isArray(args.filed_refs) && args.filed_refs.length))
          throw new ToolError({ error: "filed_needs_refs",
            hint: "status 'filed' must name what the item became — pass filed_refs" });
        const r = await c.query(
          `update ingest_inbox set status=$2, triage_note=$3, filed_refs=$4
          where id=$1 and status='new'
          returning id, source, status`,
          [args.item_id, args.status, args.note || null,
           args.filed_refs ? JSON.stringify(args.filed_refs) : null]);
        if (!r.rows.length) {
          const cur = await c.query("select status from ingest_inbox where id=$1", [args.item_id]);
          if (!cur.rows.length) throw new ToolError({ error: "not_found", item_id: args.item_id });
          return { ok: true, item_id: args.item_id, already: cur.rows[0].status,
                   note: "already dispositioned; nothing changed" };
        }
        await writeEvent(c, actor, "triage-item", "inbox", args.item_id,
          { new: { status: args.status, filed_refs: args.filed_refs || null },
            idempotency_key: args.idempotency_key });
        return { ok: true, item_id: r.rows[0].id, source: r.rows[0].source, status: r.rows[0].status };
      }),
    },

    "set-next-action": {
      discoveryOrder: 25,
      write: true,
      description: "Set ONE open ball on a subject (replaces that owner's previous open one; the other partner's stays untouched). Defaults to your ball. Only the server-issued Hermes CoS door may name its Joe sponsor as owner; created_by remains the runtime.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, ref: { type: "string" },
        description: { type: "string" }, due_on: { type: "string", description: "YYYY-MM-DD, optional" },
        owner: { type: "string", description: "optional server-derived owner; only the caller or a Hermes CoS runtime's Joe sponsor" } },
        required: ["idempotency_key","ref","description"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-next-action", args, async () => {
        const s = await resolveSubject(c, args.ref);
        const permitted = permittedActionOwnerSlugs(actor);
        const ownerSlug = args.owner === undefined || args.owner === null || args.owner === ""
          ? actor.slug : String(args.owner).trim().toLowerCase();
        if (!permitted.includes(ownerSlug))
          throw new ToolError({ error: "owner_not_permitted", owner: ownerSlug, permitted,
            hint: "a ball may be set for yourself; only the Hermes CoS door may hand one to its verified Joe sponsor, never Dell" });
        let ownerId = actor.id;
        if (ownerSlug !== actor.slug) {
          const owner = await c.query("select id from actor where slug=$1", [ownerSlug]);
          if (!owner.rows.length) throw new ToolError({ error: "actor_not_provisioned", slug: ownerSlug,
            hint: "the selected owner has no actor row" });
          ownerId = owner.rows[0].id;
        }
        // [amendment 8] Replacing an unfinished ball used to record the old one as
        // 'done'. It wasn't done — it was superseded. No-fabrication applies to
        // metadata too, and 'done' would inflate any completion measure built on this.
        await c.query(
          `update next_action set status='dropped', updated_by=$4 where subject_type=$2 and subject_id=$3
         and owner_id=$1 and status='open'`, [ownerId, s.type, s.id, actor.id]);
        const droppedPostCall = s.type === "deal" ? (await c.query(
          `update capture_post_call_action
            set status='dropped',updated_at=now(),completed_at=null
          where deal_id=$1 and owner_id=$2 and status='open'
          returning id,description /* capture:replace-post-call-actions */`,
          [s.id, ownerId])).rows : [];
        const r = await c.query(
          `insert into next_action (subject_type, subject_id, owner_id, due_on, description, created_by)
         values ($1,$2,$3,$4,$5,$6) returning id`,
          [s.type, s.id, ownerId, args.due_on || null, args.description, actor.id]);
        await writeEvent(c, actor, "set-next-action", s.type, s.id,
          { old: droppedPostCall.length ? { post_call_actions: droppedPostCall.map(x =>
              ({ id: x.id, description: x.description, status: "open" })) } : null,
            new: { summary: `ball → ${ownerSlug}: ${args.description}`,
              next_action: args.description, due: args.due_on, owner: ownerSlug,
              set_by: actor.slug,
              dropped_post_call_action_ids: droppedPostCall.map(x => x.id) },
            idempotency_key: args.idempotency_key });
        return { ok: true, next_action_id: r.rows[0].id, subject: s, owner: ownerSlug,
          dropped_post_call_action_ids: droppedPostCall.map(x => x.id) };
      }),
    },

    "complete-action": {
      discoveryOrder: 26,
      write: true,
      description: "Mark YOUR open ball on a subject DONE — the thing actually happened. Say what came of it in `outcome` if there is anything to say. DONE MEANS DONE: if you are abandoning the ball or replacing it with something else, use set-next-action instead (that records the old one as 'dropped', which is what it was). Completing is what feeds the follow-up cadence, so a false 'done' schedules a real touch on a fiction.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, ref: { type: "string" },
        outcome: { type: "string", description: "optional: what came of it, in your words" } },
        required: ["idempotency_key","ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "complete-action", args, async () => {
        const s = await resolveSubject(c, args.ref);
        // Only the caller's own ball, exactly like set-next-action: Dell's stays
        // untouched. The next_action_touch trigger stamps updated_at, and the
        // cadence engine reads THAT as the completion date, so nothing here sets
        // it by hand.
        const r = await c.query(
          `update next_action set status='done', updated_by=$1
          where subject_type=$2 and subject_id=$3 and owner_id=$1 and status='open'
          returning id, description, due_on`, [actor.id, s.type, s.id]);
        const postCall = s.type === "deal" ? (await c.query(
          `update capture_post_call_action
            set status='done',updated_at=now(),completed_at=now()
          where deal_id=$1 and owner_id=$2 and status='open'
          returning id,description,due_on /* capture:complete-post-call-actions */`,
          [s.id, actor.id])).rows : [];
        if (!r.rows.length && !postCall.length) {
          const others = (await c.query(
            `select a.slug as owner, n.description, n.due_on from next_action n
             join actor a on a.id = n.owner_id
            where n.subject_type=$1 and n.subject_id=$2 and n.status='open'`, [s.type, s.id])).rows;
          const otherPostCall = s.type === "deal" ? (await c.query(
            `select a.slug as owner,pca.description,pca.due_on
             from capture_post_call_action pca join actor a on a.id=pca.owner_id
            where pca.deal_id=$1 and pca.status='open'
            /* capture:other-post-call-actions */`, [s.id])).rows : [];
          const openForOthers = [...others, ...otherPostCall];
          throw new ToolError({ error: "no_open_action", subject: s,
            open_for_others: openForOthers,
            hint: openForOthers.length
              ? "the open ball on this subject belongs to someone else — only its holder can complete it"
              : "nobody holds an open action here; log-activity records what happened, set-next-action sets the next one" });
        }
        for (const row of r.rows)
          await writeEvent(c, actor, "complete-action", s.type, s.id,
            { field: "status", old: { status: "open" },
              new: { next_action: row.description, next_action_id: row.id, status: "done",
                     outcome: args.outcome || null },
              human_quote: args.outcome || null, idempotency_key: args.idempotency_key });
        for (const row of postCall)
          await writeEvent(c, actor, "complete-action", "deal", s.id,
            { field: "status", old: { status: "open" },
              new: { post_call_action: row.description, post_call_action_id: row.id, status: "done",
                     outcome: args.outcome || null },
              human_quote: args.outcome || null, idempotency_key: args.idempotency_key });
        const completed = [
          ...r.rows.map(x => ({ next_action_id: x.id, description: x.description, source: "next_action" })),
          ...postCall.map(x => ({ next_action_id: x.id, description: x.description, source: "post_call_action" })),
        ];
        return { ok: true, completed, count: completed.length, subject: s };
      }),
    },

    "add-critical-date": {
      discoveryOrder: 27,
      write: true,
      description: "A critical_date — a date with consequences (LOI expiry, lease expiration, option window, earnout). Surfaces in today-triage inside 14 days. source is REQUIRED — where did this date come from?",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, deal: { type: "string" },
        kind: { type: "string" }, due_on: { type: "string" }, note: { type: "string" },
        source: { type: "string" } }, required: ["idempotency_key","deal","kind","due_on","source"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "add-critical-date", args, async () => {
        const s = await resolveSubject(c, args.deal);
        if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
        const r = await c.query(
          `insert into critical_date (deal_id, kind, due_on, note, source, created_by)
         values ($1,$2,$3,$4,$5,$6) returning id`,
          [s.id, args.kind, args.due_on, args.note || null, args.source, actor.id]);
        await writeEvent(c, actor, "add-critical-date", "deal", s.id,
          { new: { kind: args.kind, due_on: args.due_on }, idempotency_key: args.idempotency_key });
        return { ok: true, critical_date_id: r.rows[0].id };
      }),
    },
  };
}
