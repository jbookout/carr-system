import { redact } from "./tool-execution.js";
import { ToolError } from "./tool-error.js";

// A CHECK THAT SPANS MORE THAN ONE COLUMN REPORTS `column: null`, and 219 of
// this database's constraints do. Postgres is not being unhelpful -- it cannot
// name one column for a rule about several -- but the caller is then told that
// a rule broke without being told which of their inputs broke it, which is the
// difference between a refusal they can act on and a dead end. Measured
// 2026-09-18: 219 multi-column constraints, of which 86 leave the field
// completely unidentifiable and 212 leave the required fix unknowable.
//
// The catalog knows the answer. pg_constraint.conkey holds exactly the columns
// the rule is about, and pg_get_constraintdef prints the rule itself, so one
// lookup keyed on the constraint name the error already carries turns
// `column: null` into the list of fields involved plus the condition they must
// satisfy. That is a read of the system catalogs only -- no row of anyone's
// data is touched -- and it runs on the connection the failed statement was
// already using, after its rollback, so it costs no new connection.
const CONSTRAINT_COLUMNS_SQL = `
  select t.relname as table_name,
         pg_get_constraintdef(c.oid) as definition,
         coalesce(array_agg(a.attname order by a.attname)
                    filter (where a.attname is not null), '{}') as columns
    from pg_constraint c
    join pg_class t on t.oid = c.conrelid
    left join pg_attribute a
      on a.attrelid = c.conrelid and a.attnum = any(c.conkey) and not a.attisdropped
   where c.conname = $1
   group by t.relname, c.oid
   limit 1`;

/** Add the columns and the rule text to a constraint refusal, when we can.
 *
 * Deliberately best-effort: the refusal is already correct and already useful
 * without this, so a catalog lookup that fails must NOT replace a precise
 * refusal with a database error about the lookup. Any failure returns the
 * refusal untouched.
 */
export async function describeConstraint(client, refusal) {
  const name = refusal?.payload?.constraint;
  if (!client || typeof name !== "string" || !name) return refusal;
  let row;
  try {
    const out = await client.query(CONSTRAINT_COLUMNS_SQL, [name]);
    row = out?.rows?.[0];
  } catch {
    return refusal;   // see the doc comment: never trade a good refusal for this
  }
  if (!row) return refusal;
  const columns = Array.isArray(row.columns) ? row.columns : [];
  if (columns.length === 0) return refusal;
  refusal.payload.table = refusal.payload.table || row.table_name || null;
  refusal.payload.columns = columns;
  refusal.payload.rule = redact(row.definition) || null;
  // Only overwrite the hint when the original was the unhelpful case: a rule
  // broke and no field was named. A single-column violation already told the
  // caller which field, and that hint is better than this one.
  if (!refusal.payload.column) {
    refusal.payload.hint =
      `this rule is about ${columns.length} fields together (${columns.join(", ")}), which is why ` +
      `no single field is named: the database cannot attribute a multi-column rule to one column. ` +
      `\`rule\` is the exact condition your values must satisfy. Check the combination, not each ` +
      `field on its own -- each may be individually valid.`;
  }
  return refusal;
}

// Research is evidence, not a checkbox supplied after the write.  These
// validators run before every intake write that turns a contact into a client
// or vendor (and before a directly-created contact party).  The resulting
// evidence is then persisted as a `verified` finding in the same envelope.
export function researchEvidence(raw, requiredFields, gate) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    throw new ToolError({ error: "research_evidence_required", gate,
      hint: "research before creating this contact; pass typed HTTPS sources, per-field source indexes, and discrepancies[] (empty when none)" });
  const sources = Array.isArray(raw.sources) ? raw.sources.map((source, index) => {
    if (!source || typeof source !== "object" || Array.isArray(source))
      throw new ToolError({ error: "research_source_invalid", gate, source_index: index });
    let url;
    try { url = new URL(String(source.url || "")); }
    catch { throw new ToolError({ error: "research_source_invalid", gate, source_index: index }); }
    const observed = new Date(String(source.observed_at || ""));
    if (url.protocol !== "https:" || Number.isNaN(observed.getTime()) || observed.getTime() > Date.now() + 300000)
      throw new ToolError({ error: "research_source_invalid", gate, source_index: index,
        hint: "each source needs an HTTPS URL and a non-future observed_at timestamp" });
    return { url: url.toString(), observed_at: observed.toISOString() };
  }) : [];
  const links = raw.field_evidence;
  const linked = links && typeof links === "object" && !Array.isArray(links) ? links : {};
  const validLinks = field => Array.isArray(linked[field]) && linked[field].length > 0 &&
    linked[field].every(i => Number.isInteger(i) && i >= 0 && i < sources.length);
  const missing = requiredFields.filter(field => !validLinks(field));
  if (!sources.length || missing.length || !Array.isArray(raw.discrepancies))
    throw new ToolError({ error: "research_evidence_incomplete", gate,
      missing_fields: missing, has_sources: sources.length > 0,
      hint: "link every required field to one or more typed source indexes and pass discrepancies[] even when it is empty" });
  return { sources, checked_fields: requiredFields, field_evidence: linked,
    discrepancies: raw.discrepancies };
}

export const RESEARCH_EVIDENCE_SCHEMA = {
  type: "object",
  required: ["sources", "field_evidence", "discrepancies"],
  properties: {
    sources: { type: "array", items: { type: "object", required: ["url", "observed_at"],
      properties: { url: { type: "string" }, observed_at: { type: "string" } } } },
    field_evidence: { type: "object" },
    discrepancies: { type: "array" },
  },
};

export async function stampResearch(c, actor, partyId, evidence) {
  await c.query(
    `insert into record_flag (subject_type,subject_id,kind,value,source,created_by)
     values ('party',$1,'verified',$2,$3,$4)`,
    [partyId, JSON.stringify({ found: true, checked_fields: evidence.checked_fields,
      field_evidence: evidence.field_evidence, sources: evidence.sources,
      discrepancies: evidence.discrepancies, epistemic_status: "source_backed" }),
     evidence.sources.map(source => `${source.url} observed ${source.observed_at}`).join(" | "), actor.id]);
}

export async function config(client, key, fallback) {
  const r = await client.query("select value from system_config where key=$1", [key]);
  return r.rows.length ? r.rows[0].value : fallback;
}

// Unrecognized phone shapes pass through unchanged.
export function fmtPhoneUS(v) {
  if (v === null || v === undefined) return null;
  const digits = String(v).replace(/\D/g, "");
  const t = digits.length === 11 && digits[0] === "1" ? digits.slice(1) : digits;
  if (t.length !== 10) return String(v).trim() || null;
  return `(${t.slice(0, 3)}) ${t.slice(3, 6)}-${t.slice(6)}`;
}

export async function resolveSubject(client, ref) {
  // Accepts 'L-204', 'C-127', 'V-CPA-006', a deal name, or a party/practice name.
  //
  // [amendment 11] Every lookup goes through v_ref_index. This used to query the
  // base tables, which carr_reader cannot see — views-only is deliberate — so the
  // read verbs returned permission-denied in production from build day until this
  // was found by ORDER 6's done-test. The security model wins; the verb adapts.
  // A raw UUID resolves exactly (the Deal Room board addresses deals by id;
  // v_ref_index carries the id, so views-only holds).
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ref)) {
    const r = await client.query(
      "select subject_type, subject_id from v_ref_index where subject_id=$1 limit 2", [ref]);
    if (r.rows.length === 1) return { type: r.rows[0].subject_type, id: r.rows[0].subject_id };
    if (r.rows.length > 1) {
      const deal = r.rows.find(x => x.subject_type === "deal");
      if (deal) return { type: "deal", id: deal.subject_id };
    }
  }
  if (/^L-\d+/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='lead' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "lead", id: r.rows[0].subject_id };
  }
  if (/^C-\d+/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='client' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "client", id: r.rows[0].subject_id };
  }
  if (/^[VT]-/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='vendor' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "vendor", id: r.rows[0].subject_id };
  }
  // P- party refs. Added 2026-08-09 after update-decision refused about:'P-0948' during
  // the loop #278 backfill: party was the one record class whose OWN printed ref form
  // this resolver could not take back, so every verb built on it — catch-me-up, find,
  // update-decision, record-finding, all of them — pushed party work onto name matching
  // instead. record-finding's description had been advertising 'P-0301' as a valid
  // subject the whole time, so the documented contract and the resolver disagreed.
  if (/^P-\d+/i.test(ref)) {
    const r = await client.query(
      "select subject_id from v_ref_index where subject_type='party' and ref ilike $1", [ref]);
    if (r.rows.length) return { type: "party", id: r.rows[0].subject_id };
  }
  // [amendment 7] Both name fallbacks used to take the single newest/closest match.
  // On an ambiguous name that silently wrote to the WRONG record, with no signal —
  // exactly the failure tool-contracts §5 says a verb must never produce. Fetch up
  // to 5 and refuse to guess when more than one matches.
  // [amendment 7] Name paths fetch up to 5 and refuse to guess past one match.
  let r = await client.query(
    `select subject_id, display_name, status, client_ref from v_ref_index
      where subject_type='deal' and display_name ilike $1 limit 5`, [`%${ref}%`]);
  if (r.rows.length === 1) return { type: "deal", id: r.rows[0].subject_id };
  if (r.rows.length > 1) {
    throw new ToolError({ error: "needs_disambiguation", ref,
      candidates: r.rows.map(x => ({ name: x.display_name, phase: x.status, client_ref: x.client_ref })),
      hint: "pass the exact ref or full name" });
  }
  // Merge tombstones are excluded: a merged record is not a resolution target, and
  // leaving them in would make every merged pair permanently ambiguous. That is what
  // the view's `merged` flag is for — no column added to satisfy this.
  r = await client.query(
    `select subject_type, subject_id, display_name, ref, city from v_ref_index
      where subject_type in ('lead','client','vendor') and not merged and display_name ilike $1
      order by similarity(display_name, $2) desc limit 5`, [`%${ref}%`, ref]);
  if (r.rows.length === 1) return { type: r.rows[0].subject_type, id: r.rows[0].subject_id };
  if (r.rows.length > 1) {
    throw new ToolError({ error: "needs_disambiguation", ref,
      candidates: r.rows.map(x => ({ name: x.display_name, ref: x.ref, kind: x.subject_type, city: x.city })),
      hint: "pass the exact ref or full name" });
  }
  // BARE PARTIES ARE A FALLBACK, NEVER A COMPETITOR (0056, 2026-08-02). Migration
  // 0056 put every party in v_ref_index, which is what finally makes an org like
  // Synthetic Supply Co — 17 rows, no lead/client/vendor among them — resolvable at all.
  // But this is the WRITE path: folding parties into the query above would let a
  // bare party outrank the client or vendor a name resolves to today and quietly
  // move where writes land. So the role query runs first and unchanged, and this
  // runs ONLY when it found nothing. Purely additive: anything that resolves today
  // resolves to exactly the same record.
  r = await client.query(
    `select subject_type, subject_id, display_name, ref, city from v_ref_index
      where subject_type='party' and not merged and display_name ilike $1
      order by similarity(display_name, $2) desc limit 5`, [`%${ref}%`, ref]);
  if (r.rows.length === 1) return { type: "party", id: r.rows[0].subject_id };
  if (r.rows.length > 1) {
    throw new ToolError({ error: "needs_disambiguation", ref,
      candidates: r.rows.map(x => ({ name: x.display_name, ref: x.ref, kind: x.subject_type, city: x.city })),
      hint: "more than one party carries this name — often duplicate org rows for one company; pass the exact P-ref" });
  }
  throw new ToolError({ error: "subject_not_found", ref,
    hint: "use find first; refs look like L-204 / C-127 / V-CPA-006 / P-0948 or a deal name" });
}

export const FK = { deal: "deal_id", client: "client_id", lead: "lead_id", vendor: "vendor_id" };

// [ORDER 18] The kind vocabulary lives in party_link_kind (0020) — the verb has no
// enum of its own any more, so widening it is a row a human adds, not a deploy.
// Validated against the table so an unknown kind is refused with the legal list
// rather than landing as a new de-facto vocabulary the way intro_sent did.
export async function validateLinkKind(client, kind) {
  const r = await client.query(
    "select slug from party_link_kind where slug=$1", [kind]);
  if (r.rows.length) return kind;
  const all = await client.query("select slug from party_link_kind order by sort");
  throw new ToolError({ error: "unknown_kind", kind,
    valid: all.rows.map(x => x.slug),
    hint: "party_link_kind is the vocabulary; add a row there if a genuinely new kind is needed" });
}

// ---------- [0066] the marketing lane's resolvers and gates ----------
// Same split-deploy discipline require0063 established, and it matters more here:
// all four marketing verbs are brand new, so a Worker deployed ahead of the
// migration would fail on undefined_table from inside a rolled-back transaction
// and the caller would read it as "the verb is broken" rather than "the schema
// has not landed". Every marketing verb calls this FIRST, before it touches
// anything.
export async function require0066(c) {
  const r = await c.query(
    `select to_regclass('public.marketing_subject')       is not null as subjects,
            to_regclass('public.placement_measurement')   is not null as attempts,
            to_regclass('public.v_campaign_scorecard')    is not null as scorecard,
            to_regclass('public.v_placement_measurement') is not null as coverage,
            exists (select 1 from information_schema.columns
                     where table_schema='public' and table_name='campaign'
                       and column_name='success_criterion') as campaign_shape`);
  const s = r.rows[0];
  if (s.subjects && s.attempts && s.scorecard && s.coverage && s.campaign_shape) return;
  throw new ToolError({ error: "migration_not_applied",
    migration: "0066_marketing_campaign_and_measurement", present: s,
    hint: "the marketing verbs need 0066 (campaign window/criterion/verdict columns, " +
          "marketing_subject, placement_measurement, and the measurement views). Apply it " +
          "(`~/carr-system/run.sh migrate --apply --yes`) and retry. NOTHING was written." });
}

// A campaign by uuid or by name. Name matching is normalised the same way
// campaign_name_uniq normalises it, so the verb and the index agree about what
// "the same campaign" means — 0059's whole lesson was two layers disagreeing
// about identity and minting 415 rows for 306 organisations.
export async function resolveCampaign(c, ref) {
  const raw = String(ref || "").trim();
  if (!raw) throw new ToolError({ error: "campaign_required" });
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)) {
    const r = await c.query("select * from campaign where id=$1", [raw]);
    if (r.rows.length) return r.rows[0];
    throw new ToolError({ error: "campaign_not_found", campaign: raw });
  }
  const r = await c.query(
    "select * from campaign where lower(btrim(name)) = lower(btrim($1))", [raw]);
  if (r.rows.length) return r.rows[0];
  const near = await c.query(
    "select name, status from campaign order by created_at desc limit 5");
  throw new ToolError({ error: "campaign_not_found", campaign: raw,
    recent_campaigns: near.rows,
    hint: near.rows.length
      ? "match the name exactly, or pass the campaign uuid"
      : "no campaign exists yet — open-campaign is what creates one. Do NOT attach content " +
        "to a campaign that was never stated; the whole point of the object is that the " +
        "objective was written down BEFORE the results came in." });
}

// [ORDER 34] ref -> party.id, REFS ONLY. A name here is an error by design:
// production holds the same human twice un-merged (L-208 / C-155), and a name
// match would weld them. Same rule that makes who-do-we-know's node key the ref.
export async function resolvePartyByRef(c, ref) {
  if (!/^[LCVT]-/i.test(ref || ""))
    throw new ToolError({ error: "ref_required", got: ref || null,
      hint: "links take refs only (L-### / C-### / V-XXX-### / T-###), never names; use find first" });
  // [ORDER 34 review, blocker 2] Follows party.merged_into to the SURVIVOR
  // (A3: reads follow merge pointers) and excludes client tombstones — an edge
  // written to a merged-away party would defeat the merge silently. And more
  // than one live match for a ref is a data fault to surface, never rows[0].
  const r = await c.query(
    `select distinct coalesce(p.merged_into, p.id) as party_id
       from (
         select c2.party_id, c2.roster_ref  as ref from client c2 where c2.merged_into is null
         union all select v.party_id, v.vendor_ref   from vendor v
         union all select l.party_id, l.registry_ref from lead l
       ) x
       join party p on p.id = x.party_id
      where x.ref ilike $1`, [ref]);
  if (!r.rows.length) throw new ToolError({ error: "ref_not_found", ref,
    hint: "no live record carries this ref, or it has no party row; use find" });
  if (r.rows.length > 1) throw new ToolError({ error: "ambiguous_ref", ref,
    candidates: r.rows.map(x => x.party_id),
    hint: "this ref string resolves to more than one live party — a data fault; surface to the human" });
  return r.rows[0].party_id;
}

// ---------- the deferral gate (add-loop; migration 0081, Joe 2026-08-09) ----------
//
// Every class below is a state of the world OUTSIDE the session. That is the
// whole design: there is no cell to write "later" into, so a session either
// names something real or discovers it can do the work. Kept in one place
// because the migration's check constraint and this list are the same contract
// (rule a8c55a47 — a manual path and an automated path that do the same job must
// be the same code); the DB is the backstop, this is the surface that explains.
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// THE THIRD LEG OF THAT SAME DEFECT (found 2026-08-14): kind is documented in
// add-loop's inputSchema as a REQUIRED enum, but inputSchema is advisory (see
// LOOP_MARKERS above) and nothing in the handler ever checked it. A call that
// simply OMITTED kind sailed through: the placement ternary fell through to
// section "open" (undefined is not "idea" and not "open_loop"), the loop_block
// lookup ran with kind=NULL and matched nothing, and the caller got
// {"error":"no_block","section":"open"} whose hint blamed "the loop importer"
// — which had in fact run, for every kind, two weeks earlier. The thrown
// payload even carried `kind: args.kind`, but undefined never survives JSON
// serialization, so the one field that would have named the real mistake was
// invisible. Validated up front like marker/domain/blocker, so a missing or
// misspelled kind fails as itself instead of as a phantom importer failure.
export const LOOP_KINDS = Object.freeze(["open_loop", "team_loop", "action_required", "idea"]);

// ---------- Deal Room helpers (field-base concurrency, not record version) ----------
export async function lockDealField(c, dealId, field) {
  // Same-field writers serialize; different fields deliberately use different
  // advisory keys and can commit independently on the same deal.
  await c.query(
    "select pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0)) /* dealroom:field-lock */",
    [dealId, field],
  );
}

