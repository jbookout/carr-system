import { TOOLS } from "./tool-registry.js";
import { ToolError } from "./tool-error.js";
import { withEnvelope, writeEvent } from "./versioned-write.js";
import { resolveSubject } from "./verb-support.js";

// [loop #279] How many rulings find-precedent returns at once. A ruling's reasoning
// runs to paragraphs, so this is bounded by what a caller can actually read before
// deciding, not by what the query could return. Eight is the default; this is the
// ceiling a caller may raise it to.
const PRECEDENT_CAP = 25;

export function introspectionTools() {
  return {
    "list-verbs": {
      discoveryOrder: 105,
      description: "The LIVE verb registry — names, descriptions, write flags, input schemas — straight from the deployed Worker, bypassing the connector's cached tool list. Use when a verb you expect is missing from your tool list (a deploy since this session connected): find it here, then invoke it through call-verb without any reconnect.",
      inputSchema: { type: "object", properties: {
        filter: { type: "string", description: "case-insensitive substring match over verb name AND description" },
        names_only: { type: "boolean", description: "names plus first-sentence descriptions, no schemas. Composes with filter." } } },
      handler: async (c, actor, args) => {
        const needle = args.filter ? String(args.filter).toLowerCase() : null;
        // MATCH ON BEHAVIOR, not just the label (rule 49c627cc): a session hunting
        // for what serves a job often does not know the verb's NAME yet, so the
        // filter reads descriptions too.
        const names = Object.keys(TOOLS).sort()
          .filter(n => !needle
            || n.toLowerCase().includes(needle)
            || String(TOOLS[n].description || "").toLowerCase().includes(needle));
        if (args.names_only) {
          return { ok: true, count: names.length, names_only: true,
                   verbs: names.map(n => ({ name: n, write: !!TOOLS[n].write,
                     description: String(TOOLS[n].description || "").split(/(?<=\.)\s/)[0].slice(0, 200) })) };
        }
        return { ok: true, count: names.length,
                 verbs: names.map(n => ({ name: n, write: !!TOOLS[n].write,
                   description: (TOOLS[n].description || "").slice(0, 200),
                   inputSchema: TOOLS[n].inputSchema || null })) };
      },
    },

    "export-email-domains": {
      discoveryOrder: 106,
      description: "The email DOMAINS on record for clients and leads — never the addresses. Aggregated in SQL, so an address cannot leave through this verb even by accident. WHY IT EXISTS (decision 2026-08-19): ops/fetch-allowlist.py builds the egress guard's allowlist from these two views, and on a second machine it was the ONLY thing that needed a direct database connection — the one credential standing between Dell's Mac and needing none at all. This returns strictly less than that connection does (two columns, aggregated, no write, no other view) while keeping every read attributable through the machine door. The guard's POLICY — freemail suffixes, institutional TLDs, hostname shape — deliberately stays in the caller: this verb is a data read and must never become the place that decides what the guard trusts.",
      inputSchema: { type: "object", properties: {} },
      handler: async (c) => {
        // Constant identifiers, never caller input — these two names are the
        // whole surface of this verb and are not parameterisable on purpose.
        const SOURCES = [["v_export_clients", '"Email"'], ["v_export_leads", '"Email"']];
        const domains = new Set();
        // PER-SOURCE, not just the union. The caller reports "N seen, M kept"
        // for each view, and folding the two together here would cost it that
        // line — a report that cannot say WHICH book a domain came from is the
        // kind of small loss that gets noticed only when something is wrong.
        const by_source = {}, counts = {}, notes = [];
        for (const [view, col] of SOURCES) {
          try {
            const r = await c.query(
              `select distinct lower(split_part(${col}, '@', 2)) as domain
               from ${view} where ${col} like '%@%'`);
            const seen = [];
            for (const row of r.rows) {
              const d = String(row.domain || "").trim().replace(/^\.+|\.+$/g, "").toLowerCase();
              if (!d) continue;
              seen.push(d);
              domains.add(d);
            }
            by_source[view] = seen.sort();
            counts[view] = seen.length;
          } catch (exc) {
            // One unreadable view must not cost the caller the other one — the
            // same tolerance ops/fetch-allowlist.py has always had. A skipped
            // source is REPORTED rather than folded silently into a smaller
            // answer, because a quietly short allowlist looks exactly like a
            // correct one and the guard would refuse real client domains.
            notes.push(`${view}: skipped (${exc && exc.name ? exc.name : "error"})`);
          }
        }
        if (!Object.keys(counts).length)
          throw new ToolError({ error: "no_export_view_readable", notes,
            hint: "Neither export view could be read. Treat this as NO ANSWER and keep the previous allowlist — an empty result here is indistinguishable from 'this book has no clients', and writing it out would silently strip every client domain the guard trusts." });
        return { ok: true, domains: [...domains].sort(), by_source, counts, notes };
      },
    },

    "record-defect": {
      discoveryOrder: 107,
      write: true,
      description: "File ONE defect: a claim the system made that was not true, with what WAS true beside it. This is the record layer's only RETROSPECTIVE mechanism — every other safeguard here (a hook, a gate, a registry) is prospective and guesses in advance at what will go wrong; this one gets better as failures accumulate. NOT record-finding: a finding is something learned about a client, a commit or a platform, while a defect is something the system itself got wrong. NOT a loop either, and that distinction is the reason this verb exists — a loop is a TO-DO, so it gets closed and disappears, while a defect must ACCUMULATE to be worth anything. The four load-bearing fields are claimed / actual / source_unread / rule_violated, and claimed and actual are both required and must actually differ: a row that does not state a contradiction is a note, not a defect. detected_by is required and closed-vocabulary because it is the most diagnostic field in the table — a log where every row reads 'human' is a log saying the self-checks do not work, and that is only visible if it is counted. FILE ONE THE MOMENT IT IS FOUND, including when the session filing it is the one that erred; a defect caught and not recorded is the failure this whole mechanism exists to stop. Read them back through v_defect and v_defect_class; standing-context surfaces the class counts at session start.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        defect_class: { type: "string", description: "the KIND of failure, lowercase, kebab-ish — e.g. 'dated-artifact-read-as-present-state', 'success-signal-from-the-wrong-function'. Free text on purpose: the classes are not known in advance and a fixed vocabulary would force every new failure into an old bucket. Reuse an existing class where one fits — call this verb's read side (v_defect_class) or catch-me-up first — because the count per class is the entire point." },
        claimed: { type: "string", description: "REQUIRED. What the system asserted, in the words it asserted it." },
        actual: { type: "string", description: "REQUIRED. What was true. Must differ from claimed — the pair is what makes the row reviewable later." },
        source_unread: { type: "string", description: "the artifact that would have shown it and was not opened, or was opened partially. This is the field that turns a defect log into a reading list." },
        rule_violated: { type: "string", description: "the rule this broke — the 8-character short id is fine and is what a session can actually quote; the read view resolves it to the rule's statement." },
        detected_by: { type: "string", enum: ["human","self","gate","check","peer_review","downstream"],
          description: "who caught it. 'human' means a partner had to find it, which is the most expensive kind and the one worth counting." },
        occurred_on: { type: "string", description: "date it happened (ISO); defaults today. Pass it when filing an OLD defect — a backfilled row dated today would make the trend line lie." },
        session_key: { type: "string" },
        cost_note: { type: "string", description: "what it cost, in whatever unit is true: tokens, a wrong deliverable, a partner's evening." } },
        required: ["idempotency_key","defect_class","claimed","actual","detected_by"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-defect", args, async () => {
        const present = await c.query("select to_regclass('public.defect') is not null as t");
        if (!present.rows[0].t)
          throw new ToolError({ error: "migration_not_applied", migration: "0103_defect_log",
            hint: "the defect log needs 0103. Apply it (`~/carr-system/run.sh migrate --apply --yes`) and retry. NOTHING was written." });
        const cls = String(args.defect_class || "").trim().toLowerCase().replace(/\s+/g, " ");
        const claimed = String(args.claimed || "").trim();
        const actualTxt = String(args.actual || "").trim();
        if (!cls) throw new ToolError({ error: "defect_class_required",
          hint: "name the KIND of failure, not this one instance — the count per class is what makes the log useful" });
        if (!claimed || !actualTxt || claimed.toLowerCase() === actualTxt.toLowerCase())
          throw new ToolError({ error: "no_contradiction_stated", claimed, actual: actualTxt,
            hint: "a defect states what was CLAIMED and what was TRUE, and they must differ. If they do not, this is a note — log-decision or add-loop is its home, not the defect log." });
        const r = await c.query(
          `insert into defect (occurred_on, defect_class, claimed, actual, source_unread,
                             rule_violated, detected_by, session_key, cost_note, created_by)
         values (coalesce($1::date, current_date), $2,$3,$4,$5,$6,$7,$8,$9,$10)
         returning id, occurred_on`,
          [args.occurred_on || null, cls, claimed, actualTxt,
           args.source_unread || null, args.rule_violated || null, args.detected_by,
           args.session_key || null, args.cost_note || null, actor.id]);
        // The event is what makes a defect show up in catch-me-up without a second read
        // surface — the same reason record-finding writes one.
        await writeEvent(c, actor, "record-defect", "defect", r.rows[0].id, {
          field: cls,
          new: { detected_by: args.detected_by, rule_violated: args.rule_violated || null,
                 source_unread: args.source_unread || null },
          agent_rationale: claimed.slice(0, 300),
          idempotency_key: args.idempotency_key });
        const cnt = await c.query(
          "select occurrences, caught_by_human, first_seen from v_defect_class where defect_class=$1", [cls]);
        const row = cnt.rows[0] || {};
        // A date rendered through JS's default toString comes out as
        // "Tue Aug 04 2026 00:00:00 GMT-0500 (Central Daylight Time)", which is noise in
        // a sentence a session is meant to read at a glance (rule 80def9d2).
        const asDate = v => (v instanceof Date ? v.toISOString().slice(0, 10) : String(v).slice(0, 10));
        return { ok: true, defect_id: r.rows[0].id, defect_class: cls,
                 occurred_on: asDate(r.rows[0].occurred_on),
                 class_occurrences: row.occurrences, class_caught_by_human: row.caught_by_human,
                 class_first_seen: row.first_seen ? asDate(row.first_seen) : null,
                 note: row.occurrences > 1
                   ? `this class has now failed ${row.occurrences} times since ${asDate(row.first_seen)} — ` +
                     `${row.caught_by_human} of them caught by a human. A repeat class is a design ` +
                     "problem, not a lapse: say so rather than filing the next one quietly."
                   : "first of its class." };
      }),
    },

    "find-precedent": {
      discoveryOrder: 108,
      write: false,
      description: "\"What precedent exists for a fork shaped like this?\" — searches recorded RULING HISTORY plus activated typed precedents. Results carry record_kind: settled_ruling is a recorded decision; typed_precedent is governed guidance and must not be presented as a settled decision. Search before re-arguing a settled point or declaring a fork open. Matches titles, partner wording, and reasoning with trigram similarity; two or three concrete nouns beat a sentence. NOT for doctrine (search-doctrine), records (find), or open work (loop-board). Read-only.",
      inputSchema: { type: "object", properties: {
        query: { type: "string", description: "the fork in a few concrete words — 'party merge survivor', 'markdown vs database', 'national account modelling'. Two or three specific nouns beat a full sentence: this is trigram matching, not a question answerer." },
        limit: { type: "integer", description: `rulings returned, capped at ${PRECEDENT_CAP} (default 8)` },
        since: { type: "string", description: "ISO date; only rulings on or after it. Use when you want the CURRENT position rather than the whole history — an older ruling may have been superseded." } },
        required: ["query"] },
      handler: async (c, _a, args) => {
        const q = String(args.query || "").trim();
        if (!q) throw new ToolError({ error: "query_required",
          hint: "name the fork in a few concrete words" });
        const cap = Math.max(1, Math.min(PRECEDENT_CAP, args.limit || 8));
        const present = await c.query("select to_regclass('public.v_precedent') is not null as t");
        if (!present.rows[0].t)
          throw new ToolError({ error: "migration_not_applied", migration: "0106_precedent_and_point_in_time",
            hint: "precedent search needs 0106. Apply it (`~/carr-system/run.sh migrate --apply --yes`) and retry." });
        // Typed guidance is deliberately additive to ruling history, not a replacement for it.
        // 0168 may not yet exist on a local or older environment, so its presence and active
        // lifecycle state are both required before it can contribute searchable precedents.
        const registryPresent = await c.query(
          "select to_regclass('ops.v_guidance_registry_state') is not null as t");
        let guidanceRegistryActive = false;
        if (registryPresent.rows[0].t) {
          const registryState = await c.query(
            "select state from ops.v_guidance_registry_state limit 1");
          guidanceRegistryActive = registryState.rows[0]?.state === "active";
        }
        // WORD SIMILARITY, NOT similarity(). This was built with similarity() first and it
        // returned ZERO for every realistic query, because similarity() compares two whole
        // trigram sets: a three-word query against a thousand-character ruling scores near
        // zero no matter how exactly those words appear in it. word_similarity() scores the
        // query against the best-matching WINDOW of the document, which is the actual
        // question. Measured on the live corpus: similarity() found 0 rulings for "markdown
        // database" and word_similarity() found the database-first ruling at 0.58.
        //
        // The second branch is the one that catches an exact multi-word phrase whose words
        // are far apart in the text — every word present, order and distance irrelevant.
        const words = q.split(/\s+/).filter(w => w.length > 2);
        const precedentSource = guidanceRegistryActive
          ? `(select decision_id, entry_date, title, human_quote, agent_rationale, author,
                   provenance::text as provenance, haystack,
                   'settled_ruling'::text as record_kind from v_precedent
            union all
            select decision_id, entry_date, title, human_quote, agent_rationale, author,
                   provenance::text as provenance, haystack,
                   'typed_precedent'::text as record_kind from ops.v_guidance_precedent) as precedent_history`
          : `(select decision_id, entry_date, title, human_quote, agent_rationale, author,
                   provenance, haystack, 'settled_ruling'::text as record_kind
              from v_precedent) as precedent_history`;
        const r = await c.query(
          `select decision_id, entry_date, title, human_quote, agent_rationale, author,
                provenance, record_kind, word_similarity($1, haystack) as score
           from ${precedentSource}
          where (word_similarity($1, haystack) >= 0.3
                 or ($2::text[] <> '{}' and haystack ilike all(
                       select '%' || w || '%' from unnest($2::text[]) w)))
            and ($3::date is null or entry_date >= $3::date)
          order by word_similarity($1, haystack) desc, entry_date desc
          limit $4`,
          [q, words, args.since || null, cap]);
        // THE RATIONALE IS CUT, NOT DROPPED. A ruling's reasoning runs to paragraphs and eight
        // of them would swamp the caller; the id is here so the full text is one read away.
        const rulings = r.rows.map(x => ({
          decision_id: x.decision_id,
          date: x.entry_date,
          title: x.title,
          // The partner's own words first: that is the binding half of a ruling, and the
          // summary is the session's paraphrase of it.
          human_said: x.human_quote || null,
          reasoning_excerpt: (x.agent_rationale || "").slice(0, 400)
            + ((x.agent_rationale || "").length > 400 ? " …" : ""),
          author: x.author,
          provenance: x.provenance || null,
          record_kind: x.record_kind || "settled_ruling",
          match_score: Number(x.score?.toFixed?.(3) ?? x.score),
        }));
        return { ok: true, query: q, count: rulings.length, rulings,
          note: rulings.length
            ? "Only settled_ruling results are settled decisions. typed_precedent results are " +
              "governed guidance patterns, not proof that Joe or Dell ruled on this fork. Read " +
              "the kind, date, provenance, and full reasoning before relying on any result."
            : "No precedent matches. That is NOT proof none exists — this is trigram matching over " +
              "the words actually used, so try the partner's likely phrasing and a couple of " +
              "different concrete nouns before concluding the fork is unsettled." };
      },
    },

    "state-as-of": {
      discoveryOrder: 109,
      write: false,
      description: "\"What did the record SAY about this on date X?\" — replays the field-level change log for one record up to an instant. The material has been there since the first migration (every write stores the old and new value of the field it touched) and nothing exposed it. USE IT INSTEAD OF ASKING A PARTNER WHETHER SOMETHING IS STILL TRUE: the standing rule that a session must stop and ask before drafting off notes older than about sixty days exists because nothing could answer that mechanically, and this can. Also the way to settle which of two disagreeing surfaces went stale — knowing what we believed WHEN is the whole of that question. Every field comes back with how many times it changed AFTER the cutoff and what it says now, so a caller can always tell \"true then and still true\" from \"true then, moved since\". Read-only, and it reconstructs rather than restores: nothing is written back.",
      inputSchema: { type: "object", properties: {
        ref: { type: "string", description: "the record — C-127 / L-204 / V-CPA-006 / P-0948 or an exact deal name" },
        as_of: { type: "string", description: "the instant to reconstruct (ISO date or timestamp). Defaults to now, which returns the current state with its change counts — useful on its own for seeing what has moved recently." } },
        required: ["ref"] },
      handler: async (c, _a, args) => {
        const present = await c.query("select to_regclass('public.v_field_history') is not null as t");
        if (!present.rows[0].t)
          throw new ToolError({ error: "migration_not_applied", migration: "0106_precedent_and_point_in_time",
            hint: "point-in-time reconstruction needs 0106. Apply it and retry." });
        const s = await resolveSubject(c, args.ref);
        const at = args.as_of || null;
        const r = await c.query(
          `select field, value_at, changed_at, changed_by, verb, later_changes, current_value
           from state_as_of($1, $2, coalesce($3::timestamptz, now()))`,
          [s.type, s.id, at]);
        if (!r.rows.length)
          return { ok: true, ref: args.ref, subject_type: s.type, as_of: at || "now",
                   fields: [], count: 0,
                   note: "This record has NO field-level history at or before that instant. That " +
                         "means nothing was recorded about it then, not that it did not exist — a " +
                         "record created by an import carries its creation but no field changes." };
        const fields = r.rows.map(x => ({
          field: x.field,
          value_then: x.value_at,
          set_at: x.changed_at,
          set_by: x.changed_by,
          by_verb: x.verb,
          changes_since: x.later_changes,
          value_now: x.later_changes > 0 ? x.current_value : undefined,
          still_current: x.later_changes === 0,
        }));
        const moved = fields.filter(f => !f.still_current);
        return { ok: true, ref: args.ref, subject_type: s.type, as_of: at || "now",
                 count: fields.length, fields_changed_since: moved.length, fields,
                 note: moved.length
                   ? `${moved.length} of ${fields.length} field(s) have moved since that instant — ` +
                     "each of those carries value_now beside value_then. Anything drafted off a " +
                     "note from that date is out of step on exactly those fields and no others, " +
                     "which is a narrower and more useful answer than 'the notes are old'."
                   : "Nothing about this record has changed since that instant, so a note from " +
                     "that date is still accurate on every field the record tracks." };
      },
    },

    "call-verb": {
      discoveryOrder: 110,
      // write:true here decides PROFILE and PERMISSION treatment, NOT the database
      // connection — and the old comment claiming it "rides the writer path" was
      // wrong in a way that cost a real investigation on 2026-08-21. mcp.js
      // intercepts this verb by name and re-enters the dispatcher as the INNER
      // verb, so the inner verb's own write flag picks reader or writer. A
      // call-verb wrapping a read verb lands on the READER connection and fails
      // exactly as the direct call would; it did, on the doctrine-search outage.
      write: true,
      description: "Invoke ANY live verb by name — the deploy-gap passthrough. A freshly deployed verb is callable here the moment the Worker ships, no connector reconnect needed; its first-class tool appears at your next session start. Takes {verb, args} where args is the inner verb's own argument object (including its idempotency_key for writes). All profile and permission checks apply to the inner verb exactly as a direct call.",
      inputSchema: { type: "object", properties: {
        verb: { type: "string" },
        args: { type: "object" } },
        required: ["verb"] },
      handler: async () => {
        // Reachable only if a caller bypasses mcp.js dispatch (local-verb.mjs).
        throw new ToolError({ error: "dispatcher_only",
          hint: "call-verb is intercepted in mcp.js callTool; invoke the inner verb directly here" });
      },
    },
  };
}
