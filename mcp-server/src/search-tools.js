import { ToolError } from "./tool-error.js";
import { FK, resolveSubject } from "./verb-support.js";
import { executeRegisteredTool } from "./tool-execution.js";

// [ORDER 18] How many intro-graph edges `find` returns per query. A cap, not a
// page: find answers "who is this and who do we know through them", and the whole
// subgraph belongs to a graph verb nobody has ordered yet.
const CONNECTIONS_CAP = 12;

// [ORDER 32] who-do-we-know: the multi-hop half of the intro graph.
//
// DEPTH IS CAPPED AT 3 AND THE CAP IS NOT A PREFERENCE. A referral path four
// people long is not an asset — nobody makes that ask — and an uncapped
// recursive walk over a graph that will keep growing is a Worker timeout waiting
// for a busy night. The order says depth <= 3; this is where it is enforced, and
// a caller asking for more gets 3 rather than an error, because the answer at 3
// is still the right answer.
const WHO_MAX_DEPTH = 3;

const WHO_PATH_CAP = 25;

// [loop #132] How many RETIRED refs `find` lists per organisation group.
//
// A tombstone list is navigation, not an answer. 0059 consolidated 415 org rows
// into 306 survivors plus 109 tombstones and one name alone (Synthetic Supply Co) carries
// sixteen of them; the useful facts are "sixteen exist" and "here is where to look
// them up", not sixteen refs spending the whole payload. The COUNT is always exact
// and never truncated — only the ref list is capped, and the row says so.
const RETIRED_REF_CAP = 10;

// [loop #127] How many lead↔client links, and how many deals reached through them,
// `find` returns per search. Production carries 30 linked leads in total, so this is
// headroom rather than a real cut today; it exists so a future search on a common
// org name cannot spend the whole payload on traversal rows. A search that hits it
// says so in the note rather than truncating silently.
const LINK_CAP = 20;

// THE NODE KEY IS THE REF, NOT THE NAME, and that choice is load-bearing.
// v_party_graph carries exactly one ref per party (0020's `distinct on`), so a
// ref identifies a party. A name does not: production holds one real lead
// twice — L-208 and C-155, the same human as two un-merged records — and
// joining paths on the name string would silently weld those two records into
// one node and invent hops that do not exist. Refs keep them separate, which is
// the truth of the book today, duplicate and all.
// (Example sanitized 2026-08-06, ORDER 42b — the original named the real lead.)
//
// The cost, stated rather than hidden: an edge whose endpoint carries NO ref
// cannot be walked. Today that is zero edges of 31. The verb counts them and
// returns the count as `edges_unwalkable_total` rather than dropping them
// quietly, so the day it stops being zero the answer says so instead of just
// getting smaller. That field is graph-wide; the per-target list beside it in
// the response is `unwalkable_edges`, and the two carry different scopes.
const WHO_EDGES = `
  select from_ref, from_name, kind, to_ref, to_name, note
    from v_party_graph
   where from_ref is not null and to_ref is not null`;

const FIND_CATCH_UP_QUERY_MAX = 200;

const FIND_CATCH_UP_LIMIT_MAX = 50;

const FIND_CATCH_UP_CANDIDATE_CAP = 25;

const CONVERSATION_TIMELINE_MAX = 20;

const CONVERSATION_PATH_MAX = 10;

function kindFromRef(ref) {
  if (/^L-/i.test(ref)) return "lead";
  if (/^C-/i.test(ref)) return "client";
  if (/^[VT]-/i.test(ref)) return "vendor";
  if (/^P-/i.test(ref)) return "party";
  return "record";
}

// Turn find's deliberately rich result into the one thing a composition may
// act on: an explicit LIVE target. Related lead/client links and deals_via_link
// are context, not matches, so they never enter this list. Ref-bearing rows are
// deduplicated because find can surface the same survivor in both parties and
// organizations; deal-name rows are not deduplicated because two deals with one
// name are still two records and therefore must stop for disambiguation.
function findCatchUpCandidates(found) {
  if (!found || typeof found !== "object" || Array.isArray(found) ||
      !Array.isArray(found.parties) || !Array.isArray(found.organizations) ||
      !Array.isArray(found.deals))
    throw new ToolError({ error: "find_result_invalid" });

  const refs = new Map();
  const addRef = (target, name, kind) => {
    if (typeof target !== "string" || !target.trim()) return;
    const clean = target.trim();
    if (!refs.has(clean)) refs.set(clean, {
      kind: typeof kind === "string" && kind ? kind : kindFromRef(clean),
      name: typeof name === "string" && name ? name : clean,
      target: clean,
    });
  };

  for (const row of found.parties) {
    if (row && row.merged === false) addRef(row.ref, row.name, row.kind);
  }
  for (const row of found.organizations) {
    if (!row || typeof row !== "object") continue;
    for (const ref of [...(Array.isArray(row.refs) ? row.refs : []),
                       ...(Array.isArray(row.role_refs) ? row.role_refs : [])])
      addRef(ref, row.name, kindFromRef(ref));
  }

  const deals = found.deals.flatMap((row) =>
    row && typeof row.name === "string" && row.name.trim()
      ? [{ kind: "deal", name: row.name.trim(), target: row.name.trim() }]
      : []);
  return [...refs.values(), ...deals].sort((a, b) =>
    a.target.localeCompare(b.target) || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
}

export function searchTools() {
  return {
    "find": {
      discoveryOrder: 1,
      write: false,
      description: "Search people, practices, buildings, deals, leads, vendors by name (fuzzy). Use FIRST when you only have a name; returns refs (L-/C-/V-) the write verbs take. Matches party.name / deal.name / client.roster_ref. Survivors come first and are counted separately from retired aliases: `refs`/`live_rows` are what you may write to, `retired_refs`/`retired_aliases` are tombstones of completed merges, kept navigable but never a target. Also returns the intro-graph edges touching the match (who can introduce whom), newest first. FOLLOWS THE LEAD ↔ CLIENT LINK SINCE 0102: `lead_client_links` pairs a matched lead with the client it became (or sits under), by exact key and never by name, and `deals_via_link` carries the deals filed under that client — which is how a search for a doctor's name finally surfaces the deal filed under their practice's name. `deals` remains the name-match list and the two are never blended. NOT the verb for a ref you already hold (catch-me-up takes that), and NOT the referral-path verb (who-do-we-know walks the graph). Read-only.",
      inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
      handler: async (c, _a, args) => {
        const q = args.query;
        // [amendment 11] Through v_ref_index, not the base tables. Merged records are
        // KEPT here (unlike resolveSubject) and carry the flag: someone searching a
        // merged name should learn the record exists and where it went, rather than
        // be told nothing matched.
        // SURVIVORS FIRST (loop #132). The flag was already on every row, but the
        // ordering was pure similarity, so a name carrying tombstones could spend the
        // ten-row budget on retired refs and push its own survivor out of the answer.
        // Sorting on `merged` first costs nothing — the tombstones still come back,
        // they just stop outranking the record that is actually live.
        const parties = await c.query(
          `select display_name as name, city, specialty, org_name, ref, subject_type as kind, merged
         from v_ref_index
         where subject_type in ('lead','client','vendor')
           and (display_name % $1 or display_name ilike $2)
         order by merged, similarity(display_name,$1) desc limit 10`, [q, `%${q}%`]);
        // ORGS AND UNLINKED PEOPLE, GROUPED (0056, 2026-08-02). Until migration 0056
        // v_ref_index held only role records, so 415 org parties were invisible here:
        // `find "Synthetic Supply Co"` returned "Synthetic Person" — a trigram hit on one word —
        // and none of the 17 rows literally named Synthetic Supply Co.
        // GROUPED BY NAME ON PURPOSE. Those 17 rows are one company minted 17 times,
        // once per rep, and listing them raw would spend the whole 10-row budget on
        // copies of one answer and push every other match out. One row per name, with
        // the count and the refs, answers "who do we know at X" AND surfaces the
        // duplication instead of hiding it. Kept separate from the role query above so
        // a bare party never outranks a real client or vendor.
        //
        // LIVE AND RETIRED ARE COUNTED SEPARATELY, AND THE BLEND WAS THE BUG (loop
        // #132, 2026-08-02). This grouping shipped in b0fda91, BEFORE 0059 consolidated
        // the orgs, and it was never taught about merged_into. Afterwards it kept
        // reporting `duplicate_rows: 17` for Synthetic Supply Co and `13` for Synthetic Studio —
        // both of which are ONE live row plus sixteen and twelve tombstones. That
        // number then read as "the book is still full of duplicates", which is the
        // opposite of what 0059 did, and every ref in the list read as a live target.
        // There is no single honest count here, so there is no single count: the
        // survivors and the tombstones are two facts and they travel as two fields.
        // party_org_identity_uniq makes live_rows=1 the invariant for any consolidated
        // org, so a live_rows above 1 is now a real signal rather than noise.
        const orgs = await c.query(
          // 2026-08-02: the subject_type='party' restriction moved OFF the WHERE and
          // ONTO each aggregate. It has to, because v_ref_index indexes SUBJECTS rather
          // than roles (0056): the moment an org party gains a client, lead or vendor
          // record it stops appearing as a party row and starts appearing under that
          // role's ref. 0061 did exactly that to Synthetic Studio — P-0111 is live and
          // unmerged, but it now indexes as client C-161, so a party-only query saw its
          // twelve tombstones, reported live_rows:0, and fired the all_retired note
          // claiming the survivor "carries a DIFFERENT name and is not in this result"
          // while the survivor sat in the SAME payload under the SAME name. Counting
          // live rows of any subject_type is what makes all_retired mean what it says.
          `select display_name as name,
                count(*) filter (where not merged and subject_type = 'party')::int
                  as live_rows,
                count(*) filter (where merged and subject_type = 'party')::int
                  as retired_aliases,
                coalesce(array_agg(ref order by ref)
                           filter (where not merged and subject_type = 'party'),
                         '{}'::text[]) as refs,
                coalesce((array_agg(ref order by ref)
                            filter (where merged and subject_type = 'party'))
                           [1:${RETIRED_REF_CAP}],
                         '{}'::text[]) as retired_refs,
                count(*) filter (where not merged and subject_type <> 'party')::int
                  as live_as_role,
                coalesce(array_agg(ref order by ref)
                           filter (where not merged and subject_type <> 'party'),
                         '{}'::text[]) as role_refs
         from v_ref_index
         where (display_name % $1 or display_name ilike $2)
         group by display_name
        having count(*) filter (where subject_type='party') > 0
         order by similarity(display_name,$1) desc limit 5`, [q, `%${q}%`]);
        const deals = await c.query(
          // The column is lead_owner; `owner` never existed on this view, so this
          // query has always thrown. It stayed invisible because the query above it
          // threw first (amendment 11) — one bug hiding another.
          `select name, phase, lead_owner as owner, client_ref,
                to_jsonb(invoiced_on)#>>'{}' as invoiced_on,
                to_jsonb(closed_on)#>>'{}' as closed_on, lane, outcome
           from v_deal_board where name ilike $1 limit 5`,
          [`%${q}%`]);
        // [ORDER 18] The intro graph, through v_party_graph — SAFE COLUMNS ONLY, the
        // same views-only posture as v_ref_index. Capped deliberately: a hub like
        // V-CPA-006 carries 16 edges on its own and the whole graph is not an answer
        // to a name search. Newest first, because a fresh intro is the useful one;
        // names break the tie so the 28 backfilled edges (one timestamp between them)
        // still come back in a stable order.
        const connections = await c.query(
          `select from_ref, from_name, kind, to_ref, to_name, note
         from v_party_graph
         where from_name ilike $1 or to_name ilike $1
            or from_ref  ilike $2 or to_ref  ilike $2
         order by linked_at desc, from_name, to_name limit $3`,
          [`%${q}%`, q, CONNECTIONS_CAP]);

        // The org rows carry their own truncation flag rather than a silent slice:
        // retired_aliases is the exact count, retired_refs may be the first
        // RETIRED_REF_CAP of them, and the reader is told which it is looking at.
        const organizations = orgs.rows.map(r => ({
          name: r.name,
          live_rows: r.live_rows,
          refs: r.refs,
          retired_aliases: r.retired_aliases,
          retired_refs: r.retired_refs,
          retired_refs_truncated: r.retired_aliases > r.retired_refs.length,
          // live_as_role / role_refs: the survivor is live but now carries a client,
          // lead or vendor ref instead of its bare party ref. all_retired means NOBODY
          // under this name is live ANYWHERE, which is the only case where telling the
          // reader to go search another name is true.
          live_as_role: r.live_as_role,
          role_refs: r.role_refs,
          all_retired: r.live_rows === 0 && r.live_as_role === 0,
        }));
        const retiredSeen = parties.rows.filter(r => r.merged).length
          + organizations.reduce((n, r) => n + r.retired_aliases, 0);
        const orphanNames = organizations.filter(r => r.all_retired).map(r => r.name);
        const promoted = organizations.filter(r => r.live_rows === 0 && r.live_as_role > 0);

        // ── THE LEAD ↔ CLIENT LINK, FOLLOWED (0102, loop #127) ────────────────────
        // Until now this verb matched deals BY NAME ONLY, so a search that landed on a
        // lead returned deals:[] even when that lead's own client carried a live deal —
        // the deal is filed under the practice's name and the search was for the
        // doctor's. The link was in the data the whole time and no read verb followed it.
        //
        // EXACT KEYS ONLY, NEVER A NAME. v_lead_client_best ranks three uuid equalities
        // (conversion pointer, shared party, shared org) and hands back one row per lead;
        // this verb does no matching of its own. That constraint is in the loop's own
        // body for a reason: this system once welded Jenna Castillo to Jeff Castillo, DMD —
        // two different people — through an import that matched on a surname.
        const matchedRefs = [
          ...parties.rows.map(r => r.ref).filter(Boolean),
          ...organizations.flatMap(r => [...(r.refs || []), ...(r.role_refs || [])]),
        ];
        let linked = [], linkedDeals = [];
        if (matchedRefs.length) {
          const lr = await c.query(
            `select lead_ref, lead_name, client_ref, client_name, link_basis, either_merged
             from v_lead_client_best
            where lead_ref = any($1) or client_ref = any($1)
            order by link_basis, lead_ref limit $2`, [matchedRefs, LINK_CAP]);
          linked = lr.rows;
          // The deals reachable THROUGH that link — the answer the caller wanted and did
          // not get. Kept in their own field rather than folded into `deals` so a reader
          // can always tell which of the two paths produced a row.
          const clientRefs = [...new Set(linked.map(r => r.client_ref).filter(Boolean))];
          const named = new Set(deals.rows.map(d => d.name));
          if (clientRefs.length) {
            const dr = await c.query(
              `select name, phase, lead_owner as owner, client_ref,
                    to_jsonb(invoiced_on)#>>'{}' as invoiced_on,
                    to_jsonb(closed_on)#>>'{}' as closed_on, lane, outcome
               from v_deal_board where client_ref = any($1)
              order by client_ref, name limit $2`, [clientRefs, LINK_CAP]);
            linkedDeals = dr.rows.filter(d => !named.has(d.name));
          }
        }

        const notes = [];
        if (retiredSeen)
          notes.push("LIVE and RETIRED are counted separately here and are never blended. " +
            "`refs` / `live_rows` (and any row with merged:false) are the survivors — those are " +
            "the refs write verbs take. `retired_refs` / `retired_aliases` and any row with " +
            "merged:true are tombstones of completed merges: they stay listed so a note, email or " +
            "document citing an old ref is still navigable, but they are not duplicates and are " +
            "never a write target.");
        else
          notes.push("No retired aliases among these matches — every ref listed is live.");
        if (promoted.length)
          notes.push(promoted.map(r =>
            `"${r.name}" has no live PARTY row, but it is not gone: the survivor is live in this ` +
            `same result under ${r.role_refs.join(", ")}. It stopped indexing as a bare party ` +
            `when it gained that record, which is how this index works — it indexes subjects, ` +
            `not roles. Write to ${r.role_refs.join(", ")}, not to a retired P- ref.`).join(" "));
        if (orphanNames.length)
          notes.push("Every row under " + orphanNames.map(n => `"${n}"`).join(", ") +
            " is retired, and nothing under that name is live anywhere: that merge's survivor " +
            "carries a DIFFERENT name and is not in this result. Search the survivor's name, or " +
            "catch-me-up one of the retired refs to see where it went. This is not a claim that " +
            "we do not know them.");

        if (linked.length) {
          const conv = linked.filter(r => r.link_basis === "conversion").length;
          const org = linked.filter(r => r.link_basis === "same_org").length;
          notes.push(
            `lead_client_links carries ${linked.length} lead/client pair(s) touching this ` +
            "search, resolved by exact key and never by name. link_basis says which: " +
            "`conversion` is lead.client_id, the pointer set when the lead became that " +
            "client; `same_party` is one person holding both records; `same_org` is the " +
            "lead sitting under the client's practice, which answers \"is this practice " +
            "already a client\" and is NOT a conversion." +
            (conv ? "" : " None of these is a conversion pointer.") +
            (org ? " Read the same_org rows as neighbours, not as the same record." : ""));
          if (linkedDeals.length)
            notes.push(`deals_via_link carries ${linkedDeals.length} deal(s) that this ` +
              "search would otherwise have missed entirely: they are filed under the linked " +
              "client, whose name does not contain the search term. `deals` is still the " +
              "name-match list and the two are never blended.");
        }

        return { parties: parties.rows, deals: deals.rows, connections: connections.rows,
                 organizations,
                 lead_client_links: linked, deals_via_link: linkedDeals,
                 note: notes.join(" ") };
      },
    },

    "who-do-we-know": {
      discoveryOrder: 2,
      write: false,
      description: "\"Who gets me to X?\" — walks the intro graph BACKWARD from a target (a ref like C-155 / V-CPA-006, or a name) and returns every referral path up to 3 hops (walks the party_link table), shortest first, each rendered as a readable chain (\"A. Vendor -knows-> B. Referrer -intro-> Dr. Example Target\"). The first name in a chain is who Joe asks. Use it before asking for an introduction; NOT for looking a record up (that is `find`) and NOT for what happened with a record (that is `catch-me-up`). Read-only, and it never guesses: it resolves to the SURVIVOR of a merge and never offers a tombstone as a target, an ambiguous LIVE name returns needs_disambiguation with the candidates, and a target that exists but carries no walkable edges says which of those two it is rather than returning an empty list that reads like 'no such person'.",
      inputSchema: { type: "object", properties: {
        target: { type: "string", description: "who you want to reach — C-155, V-CPA-006, L-208, or a full name" },
        max_depth: { type: "integer", description: `hops to walk, 1-${WHO_MAX_DEPTH} (default ${WHO_MAX_DEPTH})` },
        limit: { type: "integer", description: `paths returned, capped at ${WHO_PATH_CAP}` } },
        required: ["target"] },
      handler: async (c, _a, args) => {
        const q = String(args.target || "").trim();
        if (!q) throw new ToolError({ error: "missing_target", hint: "pass a ref (C-155) or a name" });
        const depth = Math.max(1, Math.min(WHO_MAX_DEPTH, args.max_depth || WHO_MAX_DEPTH));
        const cap = Math.max(1, Math.min(WHO_PATH_CAP, args.limit || WHO_PATH_CAP));

        // ── resolve the target to ONE node of the graph ──────────────────────
        // Ref first and exactly, name second and only on a distinct-ref basis: two
        // rows for one ref is the same party appearing on both ends of edges, not
        // an ambiguity, so the distinct is what makes the count mean something.
        //
        // MERGE-AWARE SINCE loop #132 (2026-08-02). Two changes, both about not
        // handing back a retired record. The null-ref filter: a NULL endpoint used to
        // resolve to a node with ref=null, which then walked nothing and reported
        // in_graph:true with zero paths — a live-looking answer built on a node the
        // walker cannot address. Those edges are now reported as unwalkable below,
        // by name, which is the truth. And the merged flag: a graph node can be a
        // tombstone (C-050 is one today, a real client name), so the survivor is
        // preferred whenever both are matched, and a tombstone that resolves anyway — because
        // the caller named it, or because it is the only match and its edges are
        // real — comes back FLAGGED rather than silently standing in for the survivor.
        const nodes = await c.query(
          `with n as (
           select from_ref as ref, from_name as name from v_party_graph
            where from_ref is not null
           union
           select to_ref, to_name from v_party_graph
            where to_ref is not null)
         select n.ref, n.name, coalesce(bool_or(ri.merged), false) as merged
           from n left join v_ref_index ri on ri.ref = n.ref
          where n.ref ilike $1 or n.name ilike $2
          group by n.ref, n.name
          order by merged, n.ref`, [q, `%${q}%`]);
        let node = null;
        if (nodes.rows.length) {
          // An exact ref among several name-ish matches is not ambiguous.
          const exact = nodes.rows.filter(r => (r.ref || "").toLowerCase() === q.toLowerCase());
          const live = nodes.rows.filter(r => !r.merged);
          const retired = nodes.rows.filter(r => r.merged);
          if (exact.length === 1) node = exact[0];
          else if (live.length === 1) node = live[0];
          else if (live.length > 1) throw new ToolError({ error: "needs_disambiguation", target: q,
            candidates: live.map(r => ({ ref: r.ref, name: r.name })),
            retired_aliases: retired.map(r => ({ ref: r.ref, name: r.name })),
            hint: "more than one LIVE party in the intro graph matches — pass the exact ref. " +
                  "retired_aliases are tombstones of completed merges, listed so you can see them; " +
                  "never pass one back as the target" });
          else if (retired.length === 1) node = retired[0];
          else throw new ToolError({ error: "needs_disambiguation", target: q,
            candidates: [],
            retired_aliases: retired.map(r => ({ ref: r.ref, name: r.name })),
            hint: "every graph node matching this name is a RETIRED alias of a completed merge, " +
                  "and more than one matched. Run `find` on the name to see which survivor each " +
                  "one points at, then target the survivor" });
        }

        // EDGES THIS NAME OWNS BUT THE WALKER CANNOT FOLLOW (loop #133). An edge whose
        // endpoint carries no business ref is invisible to WHO_EDGES, and staying
        // silent about it produced a flat lie: Joe Bookout is party P-1084 with no
        // client/lead/vendor row, so all six of his `can_introduce` edges — the single
        // most valuable edge class in the book — have a NULL from_ref, and asking for
        // him answered "this record exists but carries no intro-graph edges yet". It
        // carries six. Scoped to the query so the answer names the actual edges rather
        // than a global count nobody can act on.
        // Matched on REF AS WELL AS NAME. Half of every unwalkable edge has a ref on
        // the other end, and asking by that ref — who gets me to V-CPA-036 — is the
        // normal way this verb is called. Name-only matching would have answered
        // "nobody reaches her" while the Joe Bookout -> V-CPA-036 edge sat right there.
        const blocked = await c.query(
          `select from_ref, from_name, kind, to_ref, to_name, note
           from v_party_graph
          where (from_ref is null or to_ref is null)
            and (from_name ilike $1 or to_name ilike $1
                 or from_ref ilike $2 or to_ref ilike $2)
          order by from_name, to_name limit $3`, [`%${q}%`, q, CONNECTIONS_CAP]);
        const unwalkableHere = blocked.rows.map(r => ({
          from: r.from_name, from_ref: r.from_ref, kind: r.kind,
          to: r.to_name, to_ref: r.to_ref, evidence: r.note,
          blocked_end: r.from_ref === null ? "from" : "to" }));
        const blockedNote = unwalkableHere.length
          ? `${unwalkableHere.length} intro-graph edge(s) touching this name CANNOT be walked: ` +
            "one endpoint carries no business ref (a bare party — a CARR agent with no " +
            "client/lead/vendor row, or a party a link still points at after a merge). They are " +
            "listed in unwalkable_edges and they are real; the path walker simply has no node to " +
            "address. Do not read their absence from `paths` as an absence of the relationship."
          : "";

        if (!node) {
          // NOT the same answer as "no path". A record that exists and simply has
          // no edges is a gap in the Links data; a name nobody has ever recorded is
          // a different problem, and collapsing the two would hide both.
          // 'party' INCLUDED (0056, 2026-08-02). This block is the verb's honesty
          // guarantee — "exists but has no edges" must never collapse into "no such
          // person". Restricted to role records it was breaking exactly that promise:
          // asked for Synthetic Supply Co, which is 17 party rows, it answered "No record and
          // no graph node matches that name", which was simply false. A read-only
          // existence check has no reason to be narrower than the record.
          // MATCHING_RECORDS IS LIVE-ONLY, AND THE COUNT OF TOMBSTONES TRAVELS BESIDE
          // IT (loop #132). Unordered and capped at five, this block handed back five
          // tombstones for "Synthetic Studio" — P-0840, P-1044, P-0909, P-0796 — as
          // selectable records while the survivor P-0111 never appeared. A caller that
          // links or writes to one of those defeats the merge. So: survivors in
          // matching_records, tombstones as a COUNT only (find lists them with their
          // refs; that is find's job, and it is where they stay navigable), and the
          // window counts are computed before the limit so the numbers are exact even
          // when the list is truncated.
          const known = await c.query(
            `select display_name as name, ref, subject_type as kind, merged,
                  (count(*) filter (where not merged) over ())::int as live_total,
                  (count(*) filter (where merged)     over ())::int as retired_total
             from v_ref_index
            where subject_type in ('lead','client','vendor','party')
              and (ref ilike $1 or display_name ilike $2)
            order by merged, ref limit 10`, [q, `%${q}%`]);
          const liveTotal = known.rows.length ? known.rows[0].live_total : 0;
          const retiredTotal = known.rows.length ? known.rows[0].retired_total : 0;
          const liveRows = known.rows.filter(r => !r.merged)
            .map(r => ({ name: r.name, ref: r.ref, kind: r.kind }));

          let note;
          if (liveTotal) {
            note = unwalkableHere.length
              // "log the connection" would be wrong advice here: the connection IS
              // logged, it just has no walkable node. Telling Joe to record it again
              // would mint a duplicate edge and hide the actual defect.
              ? "This record exists and its intro-graph edges ARE logged — they are the " +
                "unwalkable ones above, not missing. Nothing to re-record with link-parties."
              : "This record exists but carries no WALKABLE intro-graph edges — the " +
                "connection may simply not be logged. Record it with link-parties.";
            if (retiredTotal)
              note += ` Plus ${retiredTotal} retired alias(es) under this name from completed ` +
                      "merges; they are history, never link targets — run `find` to see them.";
          } else if (retiredTotal) {
            note = `Every record under this name is a RETIRED alias (${retiredTotal}) of a ` +
                   "completed merge. The survivor carries a different name and is not in this " +
                   "result — run `find` on it, or catch-me-up one of the retired refs to see " +
                   "where it went. This is NOT a claim that we do not know them.";
          } else {
            note = "No record and no graph node matches that name. Try `find` first.";
          }
          if (blockedNote) note = blockedNote + " " + note;

          return { target: q, resolved: null, paths: [],
                   in_graph: false,
                   matching_records: liveRows,
                   live_record_count: liveTotal,
                   retired_alias_count: retiredTotal,
                   unwalkable_edges: unwalkableHere,
                   note };
        }

        // ── walk BACKWARD from the target, following edge direction ──────────
        // Direction is the semantics: an edge A -> B means A can reach B, so the
        // people who get Joe to the target are the ones upstream of it. The
        // visited-array guard is what keeps the Colby <-> Nordin pair (a real
        // two-cycle in the book) from generating paths for ever.
        const paths = await c.query(
          `with recursive e as (${WHO_EDGES}),
         back as (
           select e.from_ref as head_ref, e.from_name as head_name, 1 as hops,
                  array[e.from_ref, e.to_ref] as ref_path,
                  e.from_name || ' -' || e.kind || '-> ' || e.to_name as chain,
                  e.note as first_note
             from e where e.to_ref = $1
           union all
           select e.from_ref, e.from_name, b.hops + 1,
                  array_prepend(e.from_ref, b.ref_path),
                  e.from_name || ' -' || e.kind || '-> ' || b.chain,
                  e.note
             from e join back b on e.to_ref = b.head_ref
            where b.hops < $2 and not (e.from_ref = any(b.ref_path)))
         select hops, head_ref as ask_ref, head_name as ask_name,
                ref_path, chain, first_note
           from back order by hops, head_name, chain limit $3`,
          [node.ref, depth, cap]);

        const unwalkable = await c.query(
          `select count(*)::int as n from v_party_graph
          where from_ref is null or to_ref is null`);

        const notes = [];
        if (node.merged)
          notes.push("WARNING: " + node.ref + " is a RETIRED alias of a completed merge, not the " +
            "survivor — it resolved because you named it, or because it is the only node matching. " +
            "Its edges below are real, but run `find` on the name and re-target the survivor before " +
            "you write anything.");
        if (paths.rows.length)
          notes.push("The FIRST name in each chain is who to ask. Run the pairing through DNA/Network/introduction-rules.md before making the ask — a path existing does not make it a clean ask.");
        else if (unwalkableHere.length)
          // "may not be logged" would be the wrong diagnosis when the edges are
          // sitting right there. Zero walkable paths and a non-empty unwalkable list
          // is a REF problem, not a capture problem, and it needs the opposite action.
          notes.push("Zero WALKABLE paths, but that is not the same as no relationship — see " +
            "unwalkable_edges. The gap is a missing ref on an endpoint, not a missing capture; " +
            "logging the connection again would only duplicate it.");
        else
          notes.push("Nobody in the intro graph reaches this record within " + depth + " hops. That may mean the connection is not logged rather than not real.");
        if (blockedNote) notes.push(blockedNote);

        return {
          target: q,
          resolved: { ref: node.ref, name: node.name, merged: node.merged },
          in_graph: true,
          max_depth: depth,
          path_count: paths.rows.length,
          capped: paths.rows.length === cap,
          // SYSTEM-WIDE total, and the name now says so. Renamed 2026-08-03: it
          // sat directly beside the per-target list reading `6` next to `[]`, and
          // a session read that pair as data corruption and started diagnosing an
          // integrity failure that did not exist. Both values were always correct
          // — the scalar counts every unwalkable edge in the graph (its purpose,
          // per opus-work-orders-2026-07-31: "if a ref-less party ever joins the
          // graph, this goes non-zero and THAT is the trigger"), while the list
          // below carries only the edges touching THIS target. Two scopes, two
          // names that looked like one. Nothing consumed the old name.
          edges_unwalkable_total: unwalkable.rows[0].n,
          unwalkable_edges: unwalkableHere,
          paths: paths.rows.map(r => ({
            hops: r.hops, ask_ref: r.ask_ref, ask_name: r.ask_name,
            path: r.chain, ref_path: r.ref_path, evidence: r.first_note })),
          note: notes.join(" "),
        };
      },
    },

  // [ORDER 27 EXT (d)] "We're negotiating against X — where have we faced X,
    // what happened?" Reads v_counterparty_history ONLY (safe columns; [D5]:
    // internal-seat, never client-facing). Counterparties mostly carry no ref,
    // so resolution is name-based with party_id disambiguation — ORDER 32's
    // three-answer convention: disambiguate / exists-but-empty / no match.
    "counterparty-history": {
      discoveryOrder: 3,
      write: false,
      description: "Counterparty intelligence: every deal where we have faced this listing agent, landlord, owner, or property manager, and what happened. Ask before any negotiation. Name or ref; ambiguous names return candidates with party_id — retry with party_id.",
      inputSchema: { type: "object", properties: {
        target: { type: "string", description: "counterparty name, or a ref if they are also in the book" },
        party_id: { type: "string", description: "exact party_id from a disambiguation retry" } },
        required: ["target"] },
      handler: async (c, _a, args) => {
        if (args.party_id) {
          const rows = await c.query(
            `select * from v_counterparty_history where party_id = $1
           order by closed_on desc nulls first`, [args.party_id]);
          return { target: args.target, party_id: args.party_id, deals: rows.rows,
                   note: rows.rows.length ? noteFor(rows.rows) : "This party carries no counterparty history rows." };
        }
        let name = args.target;
        if (/^[LCVT]-/i.test(args.target)) {
          // [ORDER 34 review, fix 3] Ref -> party_id via 0027's v_ref_index
          // column, then filter the view by party_id — never by name, which
          // silently welds duplicate-name humans (the Okafor condition).
          const r = await c.query(
            "select party_id, display_name from v_ref_index where ref ilike $1", [args.target]);
          if (!r.rows.length)
            return { target: args.target, deals: [], note: "No record matches this ref." };
          if (r.rows[0].party_id) {
            const rows = await c.query(
              `select * from v_counterparty_history where party_id = $1
             order by closed_on desc nulls first`, [r.rows[0].party_id]);
            return { target: args.target, resolved_name: r.rows[0].display_name, deals: rows.rows,
                     note: rows.rows.length
                       ? `${rows.rows.filter(x => x.deal_id).length} deal(s) against this counterparty.`
                       : "This record exists but carries no counterparty history rows." };
          }
          name = r.rows[0].display_name;
        }
        const parties = await c.query(
          `select distinct party_id, party_name, party_city, party_state
           from v_counterparty_history where party_name ilike $1`, [`%${name}%`]);
        if (!parties.rows.length)
          return { target: args.target, deals: [],
                   note: "No counterparty history under this name. That may mean we have not captured the relationship (add-premises / ownership rows), not that we have never faced them." };
        if (parties.rows.length > 1)
          throw new ToolError({ error: "needs_disambiguation", target: args.target,
            candidates: parties.rows,
            hint: "more than one counterparty matches; retry with party_id" });
        const rows = await c.query(
          `select * from v_counterparty_history where party_id = $1
         order by closed_on desc nulls first`, [parties.rows[0].party_id]);
        function noteFor(rr) {
          const won = rr.filter(x => x.outcome === "won").length;
          const withDeal = rr.filter(x => x.deal_id).length;
          return `${withDeal} deal(s) against this counterparty (${won} won). Rounds and counters live on each deal — catch-me-up the deal for the blow-by-blow.`;
        }
        return { target: args.target, party: parties.rows[0], deals: rows.rows,
                 note: rows.rows.length && rows.rows.some(x => x.deal_id)
                   ? noteFor(rows.rows)
                   : "Known counterparty, no deal linkage captured yet." };
      },
    },

  // [ORDER 33 (b)] Which lane has ever produced a commission. Reads
    // v_source_attribution; the honest-limits note travels with every answer.
    "source-attribution": {
      discoveryOrder: 4,
      write: false,
      description: "Prospecting ROI: per source lane, the funnel pool -> promoted -> leads -> clients -> deals -> commissions. Answers 'which radar lane has ever produced a commission'. Reads acquisition_source per lane. Optional lane filter.",
      inputSchema: { type: "object", properties: {
        lane: { type: "string", description: "one lane slug to filter (e.g. lead-router, renewal-radar, direct:renewal, __unattributed__)" } },
        required: [] },
      handler: async (c, _a, args) => {
        const rows = args.lane
          ? await c.query("select * from v_source_attribution where lane = $1", [args.lane])
          : await c.query("select * from v_source_attribution order by lane");
        return { lanes: rows.rows,
          note: "Attribution walks pool.promoted_lead_id -> lead.client_id -> deal -> commission. " +
            "Limits, honestly: conversion links were only restored at the 7/30 cutover; leads with no lane " +
            "read direct:unknown; deals with no lead linkage sit in '__unattributed__' so totals reconcile " +
            "against the whole book; the commission table is the ONLY money source here (placeholders never sum)." };
      },
    },

    "catch-me-up": {
      discoveryOrder: 5,
      write: false,
      // The canonical replay ledger is not granted to the views-only reader.
      // This route supplies actor context inside a read-only transaction.
      writerConnection: true,
      description: "The merged timeline (event + activity rows) for one deal, client, lead, or vendor, newest first, plus its narrative-file pointer (notes_path). Use before any conversation about a record.",
      inputSchema: { type: "object", properties: { ref: { type: "string", description: "L-204 / C-127 / V-CPA-006 / deal or party name" }, limit: { type: "integer", default: 20 } }, required: ["ref"] },
      handler: async (c, _a, args) => {
        const s = await resolveSubject(c, args.ref);
        const rows = await c.query(
          `select entry_kind, occurred_at, actor, verb, summary, detail, owed
         from v_subject_timeline where subject_type=$1 and subject_id=$2
         order by occurred_at desc limit $3`, [s.type, s.id, args.limit || 20]);
        const captured = await c.query(
          `select t.idempotency_key as key, a.id::text as activity_id, a.summary
           from tool_call t join activity a on a.id::text=t.response->>'activity_id'
          where t.verb='log-activity' and t.actor_id=$1
            and a.${FK[s.type]}=$2 and t.idempotency_key like 'calcap-%'
          order by t.idempotency_key limit 5001`, [_a.id, s.id]);
        if (captured.rows.length > 5000)
          throw new ToolError({ error: "calendar_history_too_large" });
        return { subject: s, timeline: rows.rows, calendar_history: captured.rows };
      },
    },

    "find-and-catch-up": {
      discoveryOrder: 6,
      write: false,
      writerConnection: true, // catch-me-up reads the actor-bound replay ledger.
      description: "Find one live person, practice, vendor, or deal by name and immediately return that record's catch-me-up timeline. This is the bounded read-only composition of find then catch-me-up: exactly one live match proceeds; zero returns not_found; multiple matches return needs_disambiguation and no timeline. Retired aliases, linked neighbours, and related deals are never selected as the target. It performs no model call, retry, write, send, or arbitrary tool dispatch.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        query: { type: "string", description: `name to find, at most ${FIND_CATCH_UP_QUERY_MAX} characters` },
        limit: { type: "integer", minimum: 1, maximum: FIND_CATCH_UP_LIMIT_MAX, default: 20,
          description: `timeline rows returned, 1-${FIND_CATCH_UP_LIMIT_MAX}` },
      }, required: ["query"] },
      handler: async (c, actor, args) => {
        const allowed = new Set(["query", "limit"]);
        if (!args || typeof args !== "object" || Array.isArray(args) ||
            Object.keys(args).some((key) => !allowed.has(key)))
          throw new ToolError({ error: "unexpected_arguments" });

        if (typeof args.query !== "string" || !args.query.trim() ||
            args.query.trim().length > FIND_CATCH_UP_QUERY_MAX)
          throw new ToolError({ error: "invalid_query",
            hint: `query must be a nonempty string of at most ${FIND_CATCH_UP_QUERY_MAX} characters` });
        const query = args.query.trim();
        const limit = args.limit === undefined ? 20 : args.limit;
        if (!Number.isInteger(limit) || limit < 1 || limit > FIND_CATCH_UP_LIMIT_MAX)
          throw new ToolError({ error: "invalid_limit",
            hint: `limit must be an integer from 1 to ${FIND_CATCH_UP_LIMIT_MAX}` });

        // Reuse the registered read handlers on the same read-only client.
        // This is not a generic composite dispatcher: the two names are fixed in
        // code, no callback/tool name/provider is accepted, and the second handler
        // is unreachable until the first yields exactly one live target.
        const found = await executeRegisteredTool(c, actor, "find", { query });
        const candidates = findCatchUpCandidates(found);
        if (candidates.length === 0) {
          const retiredMatches = found.parties.filter((row) => row?.merged === true).length +
            found.organizations.reduce((total, row) => total +
              (Number.isInteger(row?.retired_aliases) ? row.retired_aliases : 0), 0);
          return { state: "not_found", query, candidates: [], retired_matches: retiredMatches,
            hint: retiredMatches
              ? "Only retired aliases matched; search the survivor name or call catch-me-up with a known retired ref."
              : "No live record matched this name." };
        }
        if (candidates.length !== 1) {
          return { state: "needs_disambiguation", query,
            candidate_count: candidates.length,
            candidates: candidates.slice(0, FIND_CATCH_UP_CANDIDATE_CAP),
            candidates_truncated: candidates.length > FIND_CATCH_UP_CANDIDATE_CAP,
            hint: "Choose one exact target and call catch-me-up; this verb never guesses." };
        }

        const match = candidates[0];
        const catchUp = await executeRegisteredTool(c, actor, "catch-me-up", { ref: match.target, limit });
        return { state: "completed", query, match: { ...match }, catch_up: catchUp };
      },
    },

    "prepare-conversation": {
      discoveryOrder: 7, completionClass: "write",
      write: false,
      writerConnection: true, // fixed composition includes catch-me-up.
      description: "Prepare for one conversation by resolving a name to exactly one live record, returning its recent catch-up timeline, and—when the target is a person or organization—showing the existing introduction paths to that exact ref. This is a fixed bounded read composition: ambiguous or missing identity stops before timeline/graph reads; deals receive timeline context but are never pretended to be intro-graph people. It performs no model call, retry, write, send, or arbitrary tool dispatch.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        query: { type: "string", description: `person, organization, vendor, or deal name; at most ${FIND_CATCH_UP_QUERY_MAX} characters` },
        timeline_limit: { type: "integer", minimum: 1, maximum: CONVERSATION_TIMELINE_MAX, default: 10,
          description: `recent timeline rows, 1-${CONVERSATION_TIMELINE_MAX}` },
        path_limit: { type: "integer", minimum: 1, maximum: CONVERSATION_PATH_MAX, default: 10,
          description: `introduction paths, 1-${CONVERSATION_PATH_MAX}` },
        max_depth: { type: "integer", minimum: 1, maximum: WHO_MAX_DEPTH, default: WHO_MAX_DEPTH,
          description: `introduction hops, 1-${WHO_MAX_DEPTH}` },
      }, required: ["query"] },
      handler: async (c, actor, args) => {
        const allowed = new Set(["query", "timeline_limit", "path_limit", "max_depth"]);
        if (!args || typeof args !== "object" || Array.isArray(args) ||
            Object.keys(args).some((key) => !allowed.has(key)))
          throw new ToolError({ error: "unexpected_arguments" });
        if (typeof args.query !== "string" || !args.query.trim() ||
            args.query.trim().length > FIND_CATCH_UP_QUERY_MAX)
          throw new ToolError({ error: "invalid_query",
            hint: `query must be a nonempty string of at most ${FIND_CATCH_UP_QUERY_MAX} characters` });

        const timelineLimit = args.timeline_limit === undefined ? 10 : args.timeline_limit;
        const pathLimit = args.path_limit === undefined ? 10 : args.path_limit;
        const maxDepth = args.max_depth === undefined ? WHO_MAX_DEPTH : args.max_depth;
        if (!Number.isInteger(timelineLimit) || timelineLimit < 1 ||
            timelineLimit > CONVERSATION_TIMELINE_MAX)
          throw new ToolError({ error: "invalid_timeline_limit" });
        if (!Number.isInteger(pathLimit) || pathLimit < 1 || pathLimit > CONVERSATION_PATH_MAX)
          throw new ToolError({ error: "invalid_path_limit" });
        if (!Number.isInteger(maxDepth) || maxDepth < 1 || maxDepth > WHO_MAX_DEPTH)
          throw new ToolError({ error: "invalid_max_depth" });

        // Three fixed read stages, no caller-selected route: find, catch-up, then
        // (only for a live non-deal target) the introduction graph. The first two
        // already live behind find-and-catch-up's exact one-candidate gate.
        const located = await executeRegisteredTool(c, actor, "find-and-catch-up", {
          query: args.query.trim(),
          limit: timelineLimit,
        });
        if (located.state !== "completed")
          return { workflow: "prepare-conversation", ...located };

        if (located.match.kind === "deal") {
          return { workflow: "prepare-conversation", ...located,
            introduction: {
              status: "not_applicable",
              reason: "Deals do not represent people or organizations in the introduction graph.",
            } };
        }

        const graph = await executeRegisteredTool(c, actor, "who-do-we-know", {
          target: located.match.target,
          max_depth: maxDepth,
          limit: pathLimit,
        });
        return { workflow: "prepare-conversation", ...located,
          introduction: { status: "evaluated", ...graph } };
      },
    },
  };
}
