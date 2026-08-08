// CARR MCP tool registry — Wave 1 verbs (tool-contracts-2026-07-30.md §2).
// Every write runs the envelope: idempotency replay via tool_call, actor from
// the verified token (never the payload), base_version conflicts ask and never
// auto-retry, every accepted write lands its event row, plausibility bands
// confirm instead of block. NO SEND VERB EXISTS.
// Descriptions are poka-yoke docstrings (contracts §5): what, when, edge cases.
// The doctrine store's verbs (P2, decision 82a2fb62) live in doctrine.js as a
// factory over this file's envelope machinery, merged at the bottom.
import { doctrineTools } from "./doctrine.js";
import { stripDealPlaceholders } from "./dealroom.js";

// ---------- envelope helpers ----------

export class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

// [ORDER 34 review, blocker 1] The old array-replacer form of JSON.stringify
// FILTERED nested keys instead of canonicalizing them — links[]/building/spaces
// payloads hashed as empty, so a corrected retry under a reused key replayed
// stale data silently. canon() deep-sorts instead. For FLAT args (every
// historical call that replays, incl. the frozen smoke probes) the output
// string — sorted top-level keys — is byte-identical to the old form, so
// stored hashes stay valid. A historical NESTED-args row would key_reuse
// loudly on replay rather than lie quietly; that trade is deliberate.
function canon(v) {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object")
    return Object.keys(v).sort().reduce((o, k) => {
      if (v[k] !== undefined) o[k] = canon(v[k]);
      return o;
    }, {});
  return v;
}

async function requestHash(args) {
  const data = new TextEncoder().encode(JSON.stringify(canon(args)));
  const d = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function withEnvelope(client, actor, verb, args, fn) {
  const key = args.idempotency_key;
  if (!key) throw new ToolError({ error: "missing_idempotency_key",
    hint: "generate a UUID per intended action; retries reuse the SAME key" });
  const hash = await requestHash({ ...args, idempotency_key: undefined });
  const prior = await client.query("select request_hash, response from tool_call where idempotency_key=$1", [key]);
  if (prior.rows.length) {
    if (prior.rows[0].request_hash !== hash) throw new ToolError({ error: "key_reuse" });
    return { replayed: true, ...prior.rows[0].response };          // A1: replay, no second write
  }
  const result = await fn();                                        // inside the open transaction
  await client.query(
    "insert into tool_call (idempotency_key, verb, actor_id, request_hash, response, via, client_id) values ($1,$2,$3,$4,$5,$6,$7)",
    [key, verb, actor.id, hash, JSON.stringify(result), actor.via || null, actor.client_id || null]);
  return result;
}

async function writeEvent(client, actor, verb, subjectType, subjectId, fields = {}) {
  await client.query(
    `insert into event (occurred_at, actor_id, verb, subject_type, subject_id, field,
       old_value, new_value, cause, human_quote, agent_rationale, idempotency_key, via, client_id)
     values (coalesce($1::timestamptz, now()), $2, $3, $4, $5, $6, $7, $8, 'human_stated', $9, $10, $11, $12, $13)`,
    [fields.occurred_at || null, actor.id, verb, subjectType, subjectId, fields.field || null,
     fields.old ? JSON.stringify(fields.old) : null, fields.new ? JSON.stringify(fields.new) : null,
     fields.human_quote || null, fields.agent_rationale || null, fields.idempotency_key || null,
     actor.via || null, actor.client_id || null]);
}

async function versionGuard(client, table, id, baseVersion) {
  const r = await client.query(`select version from ${table} where id=$1`, [id]);
  if (!r.rows.length) throw new ToolError({ error: "not_found", table, id });
  const current = r.rows[0].version;
  if (baseVersion === undefined || baseVersion === null)
    throw new ToolError({ error: "missing_base_version", current_version: current,
      hint: "read the record first; pass its version back as base_version" });
  if (current !== baseVersion) {
    const ev = await client.query(
      `select a.slug as actor, e.verb, e.field, e.old_value, e.new_value, e.recorded_at
       from event e join actor a on a.id=e.actor_id
       where e.subject_id=$1 order by e.recorded_at desc limit 5`, [id]);
    throw new ToolError({ error: "version_conflict", current_version: current,
      intervening_events: ev.rows,
      hint: "surface this to the human and re-read; NEVER auto-retry" });
  }
  return current;
}

async function config(client, key, fallback) {
  const r = await client.query("select value from system_config where key=$1", [key]);
  return r.rows.length ? r.rows[0].value : fallback;
}

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

// The deal paper writes a phone as (850) 361-2208. The actor row stores what the
// human typed (joe: 850.361.2208), so the plan formats it here — one convention
// in one place, rather than every field map carrying a format. An unrecognized
// shape passes through verbatim: a phone is never invented or truncated to fit.
function fmtPhoneUS(v) {
  if (v === null || v === undefined) return null;
  const digits = String(v).replace(/\D/g, "");
  const t = digits.length === 11 && digits[0] === "1" ? digits.slice(1) : digits;
  if (t.length !== 10) return String(v).trim() || null;
  return `(${t.slice(0, 3)}) ${t.slice(3, 6)}-${t.slice(6)}`;
}

async function resolveSubject(client, ref) {
  // Accepts 'L-204', 'C-127', 'V-CPA-006', a deal name, or a party/practice name.
  //
  // [amendment 11] Every lookup goes through v_ref_index. This used to query the
  // base tables, which carr_reader cannot see — views-only is deliberate — so the
  // read verbs returned permission-denied in production from build day until this
  // was found by ORDER 6's done-test. The security model wins; the verb adapts.
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
  // Henry Schein — 17 rows, no lead/client/vendor among them — resolvable at all.
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
    hint: "use find first; refs look like L-204 / C-127 / V-CPA-006 or a deal name" });
}

// ---------- loop helpers (one-writer Phase A, ORDER 31) ----------

// A LOOP NUMBER IS NOT AN IDENTIFIER, and pretending otherwise is how a verb
// writes to the wrong row. Measured in the source files 2026-07-31: '111' names
// two different items inside open-loops.md, '103'/'95'/'88'/'108' each name one
// hot item and a different backlog item, 'T34' names one row in team-loops' Open
// table and another in its Done table. So `number` narrows and `loop_id`
// identifies, and an ambiguous number REFUSES with the candidates listed —
// ORDER 1's needs_disambiguation behaviour, applied to a surface that is
// genuinely ambiguous rather than occasionally so.
async function resolveLoop(client, args) {
  if (args.loop_id) {
    const r = await client.query(
      `select li.id, li.kind, li.number, li.status, li.marker, li.due_on,
              li.close_outcome, lb.block_key as section
         from loop_item li join loop_block lb on lb.id = li.block_id
        where li.id = $1`, [args.loop_id]);
    if (!r.rows.length) throw new ToolError({ error: "not_found", loop_id: args.loop_id });
    return r.rows[0];
  }
  if (!args.number)
    throw new ToolError({ error: "missing_loop_ref",
      hint: "pass loop_id, or number (plus kind when the number is shared across kinds)" });
  const r = await client.query(
    `select li.id, li.kind, li.number, li.status, li.marker, li.due_on,
            li.close_outcome, lb.block_key as section, lb.rel_path
       from loop_item li join loop_block lb on lb.id = li.block_id
      where li.number = $1 and li.status = 'open'
        and ($2::text is null or li.kind = $2)`, [args.number, args.kind || null]);
  if (!r.rows.length)
    throw new ToolError({ error: "loop_not_found", number: args.number, kind: args.kind || null,
      hint: "only OPEN loops resolve by number; a closed one needs its loop_id" });
  if (r.rows.length > 1)
    throw new ToolError({ error: "needs_disambiguation", number: args.number,
      candidates: r.rows.map(x => ({ loop_id: x.id, kind: x.kind, section: x.section,
                                     renders_into: x.rel_path })),
      hint: "this number names more than one live row — pass loop_id. The collision is " +
            "real and in the source files; it is Joe's to renumber, not a verb's." });
  return r.rows[0];
}

// Next visible ref for a kind. Numeric part only, because that is the part the
// files increment; the prefix is the kind's own. Reads the MAX across every row
// including closed ones, so a number is never reused after a close.
async function nextLoopNumber(client, kind) {
  const prefix = kind === "team_loop" ? "T" : kind === "action_required" ? "A" : "";
  const r = await client.query(
    `select coalesce(max(nullif(regexp_replace(number, '\\D', '', 'g'), '')::int), 0) as m
       from loop_item where kind = $1`, [kind]);
  return `${prefix}${r.rows[0].m + 1}`;
}

async function nextRenderSeq(client, blockId) {
  const r = await client.query(
    "select coalesce(max(render_seq), 0) + 1 as n from loop_item where block_id = $1", [blockId]);
  return r.rows[0].n;
}

const FK = { deal: "deal_id", client: "client_id", lead: "lead_id", vendor: "vendor_id" };

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
// into 306 survivors plus 109 tombstones and one name alone (Henry Schein) carries
// sixteen of them; the useful facts are "sixteen exist" and "here is where to look
// them up", not sixteen refs spending the whole payload. The COUNT is always exact
// and never truncated — only the ref list is capped, and the row says so.
const RETIRED_REF_CAP = 10;

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

// [ORDER 18] The kind vocabulary lives in party_link_kind (0020) — the verb has no
// enum of its own any more, so widening it is a row a human adds, not a deploy.
// Validated against the table so an unknown kind is refused with the legal list
// rather than landing as a new de-facto vocabulary the way intro_sent did.
async function validateLinkKind(client, kind) {
  const r = await client.query(
    "select slug from party_link_kind where slug=$1", [kind]);
  if (r.rows.length) return kind;
  const all = await client.query("select slug from party_link_kind order by sort");
  throw new ToolError({ error: "unknown_kind", kind,
    valid: all.rows.map(x => x.slug),
    hint: "party_link_kind is the vocabulary; add a row there if a genuinely new kind is needed" });
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
async function require0063(c) {
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

// ---------- [0066] the marketing lane's resolvers and gates ----------
// Same split-deploy discipline require0063 established, and it matters more here:
// all four marketing verbs are brand new, so a Worker deployed ahead of the
// migration would fail on undefined_table from inside a rolled-back transaction
// and the caller would read it as "the verb is broken" rather than "the schema
// has not landed". Every marketing verb calls this FIRST, before it touches
// anything.
async function require0066(c) {
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
async function resolveCampaign(c, ref) {
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

// A placement by uuid, live URL, or Blotato external_id. The URL and the id are
// what a caller actually holds: the marketing seat's own campaign-proposal block
// names content "by placement URL or Blotato id" because those are the only
// handles that appear in the published log. Measured 2026-08-02: all 89
// placements carry a non-null external_id and url, and all 89 of each are
// distinct, so both are usable as keys and neither can silently collide.
async function resolvePlacement(c, ref) {
  const raw = String(ref || "").trim();
  if (!raw) throw new ToolError({ error: "placement_required" });
  const by = async (sql, v) => (await c.query(sql, [v])).rows;
  let rows = [];
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw))
    rows = await by("select * from placement where id=$1", raw);
  if (!rows.length && /^https?:\/\//i.test(raw))
    rows = await by("select * from placement where url = $1", raw);
  if (!rows.length) rows = await by("select * from placement where external_id = $1", raw);
  if (rows.length === 1) return rows[0];
  if (rows.length > 1) throw new ToolError({ error: "ambiguous_placement", ref: raw,
    candidates: rows.map(r => ({ placement_id: r.id, platform: r.platform, url: r.url })),
    hint: "this handle resolves to more than one placement — a data fault; surface it" });
  throw new ToolError({ error: "placement_not_found", ref: raw,
    hint: "pass the live post URL, the Blotato post id, or the placement uuid. Placements " +
          "are created by pipelines/pull_placement_metrics.py when a post publishes — if " +
          "the post is live and this fails, the pull has not run since it published. Do NOT " +
          "invent a placement to hang a number on." });
}

async function livePlatformSlugs(c) {
  const r = await c.query(
    "select slug from marketing_subject where subject_type='platform' and retired_at is null order by slug");
  return r.rows.map(x => x.slug);
}

// [ORDER 34] ref -> party.id, REFS ONLY. A name here is an error by design:
// production holds the same human twice un-merged (L-208 / C-155), and a name
// match would weld them. Same rule that makes who-do-we-know's node key the ref.
async function resolvePartyByRef(c, ref) {
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

// ---------- [ORDER 13] the document factory's resolver ----------
// A doc_template's field_map is DATA, not code: every slot names the template
// address it writes and the record path it reads. This resolves that map against
// one deal's records and returns the edits the local fill engine applies, plus
// the OWED list. The two properties that matter, both structural rather than
// remembered: a slot whose record field is missing is written as an explicit
// OWED marker (never left showing the template's own placeholder number, which
// is how a $20.00 asking rate would otherwise walk into a client's inbox), and
// no value is ever derived from prose.

function fmtValue(v, fmt) {
  if (v === null || v === undefined || v === "") return null;
  const num = typeof v === "number" ? v : Number(v);
  switch (fmt) {
    case "sf":   return isNaN(num) ? String(v) : Math.round(num).toLocaleString("en-US");
    case "usd":  return isNaN(num) ? String(v) : "$" + Math.round(num).toLocaleString("en-US");
    case "usd2": return isNaN(num) ? String(v) : num.toFixed(2);
    case "months": {
      if (isNaN(num)) return String(v);
      const n = num % 1 === 0 ? num.toFixed(0) : String(num);
      return `${n} month${num === 1 ? "" : "s"}`;
    }
    case "term_years":
      if (isNaN(num)) return String(v);
      return num % 12 === 0 ? `${num / 12} year${num === 12 ? "" : "s"}` : `${num} months`;
    case "term_years_num": return isNaN(num) ? String(v) : String(num / 12);
    case "date_long": {
      const d = new Date(String(v) + (String(v).length === 10 ? "T00:00:00Z" : ""));
      if (isNaN(d)) return String(v);
      return d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
    }
    case "date_short": {
      const d = new Date(String(v) + (String(v).length === 10 ? "T00:00:00Z" : ""));
      if (isNaN(d)) return String(v);
      const p = n => String(n).padStart(2, "0");
      return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}-${d.getUTCFullYear()}`;
    }
    case "rate": {                                   // {amount, basis} — never a bare number
      if (!v.amount || !v.basis) return null;
      const a = "$" + Number(v.amount).toFixed(2);
      if (v.basis === "usd_sf_yr") return `${a} per RSF`;
      if (v.basis === "usd_sf_mo") return `${a} per RSF per month`;
      if (v.basis === "usd_mo_gross") return `${a} per month, gross`;
      if (v.basis === "usd_yr_gross") return `${a} per year, gross`;
      return `${a} (${v.basis})`;
    }
    case "ti": {
      if (!v.amount || !v.basis) return null;
      const a = "$" + Number(v.amount).toLocaleString("en-US", { maximumFractionDigits: 2 });
      return v.basis === "usd_sf" ? `${a} per RSF` : `${a} total`;
    }
    default: return String(v);
  }
}

function bagGet(bag, path) {
  return path.split(".").reduce((o, k) => (o === null || o === undefined ? o : o[k]), bag);
}

// Segment rendering. A required segment that resolves to nothing owes the WHOLE
// slot. An `optional` one drops itself, and drops the preceding literal when
// that literal carries `with_next` — so "Practice, DDS" degrades to "Practice",
// not to "Practice, ". A missing credential must not cost the tenant its name.
function renderSegments(segments, bag) {
  const out = [];
  const missing = [];
  const soft = [];
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    if (seg.literal !== undefined) {
      if (seg.with_next) {
        const nxt = segments[i + 1];
        const nv = nxt && nxt.from ? fmtValue(bagGet(bag, nxt.from), nxt.format) : null;
        if (nxt && nxt.optional && (nv === null || nv === undefined)) continue;
      }
      out.push(seg.literal);
      continue;
    }
    const raw = bagGet(bag, seg.from);
    const val = fmtValue(raw, seg.format);
    if (val === null || val === undefined) {
      if (seg.optional) { soft.push(seg.from); continue; }
      missing.push(seg.from);
      continue;
    }
    out.push(val);
  }
  return { text: out.join(""), missing, soft };
}

function resolveSlot(name, slot, bag, options) {
  // returns { edit?, owed?, carried?, partial? }
  if (slot.kind === "carry")
    return { carried: { slot: name, label: slot.label, where: slot.where, note: slot.owed_note } };

  if (slot.kind === "option") {
    const chosen = options[name];
    let text = null;
    if (chosen !== undefined && chosen !== null) {
      text = typeof chosen === "number" ? slot.choices[chosen] : String(chosen);
      if (text === undefined)
        throw new ToolError({ error: "bad_option", slot: name, given: chosen, choices: slot.choices });
    } else if (slot.from && slot.value_map) {
      const src = bagGet(bag, slot.from);
      if (src !== null && src !== undefined) text = slot.value_map[String(src)] || null;
    }
    if (text === null && slot.default !== null && slot.default !== undefined)
      text = slot.choices[slot.default];
    if (text === null || text === undefined)
      return { owed: { slot: name, label: slot.label, where: slot.where, kind: "option",
                       wanted: slot.from ? `option, or ${slot.from}` : "an explicit option",
                       choices: slot.choices, why: slot.owed_note } };
    return { edit: { where: slot.where, text, slot: name } };
  }

  const segs = slot.segments || (slot.from ? [{ from: slot.from, format: slot.format }] : []);
  const r = renderSegments(segs, bag);
  if (r.missing.length)
    return { owed: { slot: name, label: slot.label, where: slot.where, kind: "record",
                     wanted: r.missing.join(", "), why: slot.owed_note } };
  const res = { edit: { where: slot.where, text: r.text, slot: name } };
  if (r.soft.length)
    res.partial = { slot: name, label: slot.label, where: slot.where, kind: "record_optional",
                    wanted: r.soft.join(", "), why: slot.owed_note };
  return res;
}

// The OWED marker written into the document itself. Visible, unmistakable, and
// impossible to confuse with a value: the point is that Joe opening the draft
// sees exactly what the record layer could not answer.
//
// NO EM-DASH, and that is not a style preference. The first version of this
// marker read `[OWED — label: field]`, and the writing lint returned 18 HARD
// findings on the very first C-112 draft, every one of them the factory's own
// marker rather than anything in CARR's template. A gate that the producer
// itself trips is a gate that gets switched off. Found by running the lint, not
// by reasoning about it.
function owedMarker(o) {
  return `[OWED: ${o.label} (needs ${o.wanted})]`;
}

async function buildRecordBag(c, dealId, clientId) {
  const bag = { today: new Date().toISOString().slice(0, 10) };
  const d = (await c.query(
    `select d.id, d.name, d.deal_type, d.phase, d.segment,
            c.roster_ref, c.vertical, c.subtype, c.contact_label,
            p.name as party_name, p.city as party_city, p.state as party_state,
            o.name as org_name
       from deal d join client c on c.id=d.client_id
       join party p on p.id=c.party_id
       left join party o on o.id=p.org_id
      where d.id=$1`, [dealId])).rows[0];
  if (!d) throw new ToolError({ error: "deal_not_found", deal_id: dealId });
  bag.deal = { name: d.name, type: d.deal_type, phase: d.phase, segment: d.segment };
  bag.client = { ref: d.roster_ref, display_name: d.party_name, org_name: d.org_name,
                 city: d.party_city, state: d.party_state, vertical: d.vertical,
                 subtype: d.subtype, contact_label: d.contact_label };

  // The signing agent is the deal's CURRENT lead participant, not the session's
  // actor: a document Dell prepares on Joe's deal still signs Joe.
  //
  // [ORDER 24] phone and email come off the actor row. They used to be hardcoded
  // null here, so the signature slots owed on every draft with a "no phone column"
  // reason that stopped being true the moment the column landed. Empty string is
  // normalized to null deliberately: a blank is missing, and a slot that owes is
  // worth more than a signature line rendered with nothing in it.
  const ag = (await c.query(
    `select a.slug, a.display_name, a.email, a.phone from deal_participant dp
       join actor a on a.id=dp.actor_id
      where dp.deal_id=$1 and dp.role='lead' and dp.to_at is null limit 1`, [dealId])).rows[0];
  bag.agent = ag ? { slug: ag.slug, display_name: ag.display_name,
                     email: (ag.email && String(ag.email).trim()) || null,
                     phone: fmtPhoneUS(ag.phone) }
                 : { slug: null, display_name: null, email: null, phone: null };

  const ct = (await c.query(
    `select p.name from deal_participant dp join party p on p.id=dp.party_id
      where dp.deal_id=$1 and dp.role='client_contact' and dp.to_at is null limit 1`, [dealId])).rows[0];
  if (ct) {
    const parts = String(ct.name).trim().split(/\s+/);
    bag.contact = { name: ct.name, first_name: parts[0] || null,
                    last_name: parts.length > 1 ? parts[parts.length - 1] : null };
  } else bag.contact = { name: null, first_name: null, last_name: null };

  const pr = (await c.query(
    `select pr.id, pr.label, b.address, b.city, b.state, s.suite,
            s.area_amount, s.area_basis
       from premises pr
       left join premises_space ps on ps.premises_id=pr.id
       left join space s on s.id=ps.space_id
       left join building b on b.id=s.building_id
      where pr.deal_id=$1 order by pr.created_at limit 1`, [dealId])).rows[0];
  bag.premises = pr
    ? { label: pr.label, address: pr.address, suite: pr.suite, city: pr.city,
        state: pr.state, area_sf: pr.area_basis === "sf" ? pr.area_amount : null }
    : { label: null, address: null, suite: null, city: null, state: null, area_sf: null };
  bag.premises_list = (await c.query(
    "select id from premises where deal_id=$1 order by created_at", [dealId])).rows;

  // Newest OUR-side round. tenant/buyer is our paper; the landlord's counter is
  // not what our LOI says, so side is not a caller choice here.
  const rnd = (await c.query(
    `select * from negotiation_round
      where deal_id=$1 and side in ('tenant','buyer')
      order by round_no desc, proposed_on desc limit 1`, [dealId])).rows[0];
  bag.round = rnd
    ? { round_no: rnd.round_no, side: rnd.side, proposed_on: rnd.proposed_on,
        rate: { amount: rnd.rate_amount, basis: rnd.rate_basis },
        rate_norm_sf_yr: rnd.rate_norm_sf_yr,
        ti: { amount: rnd.ti_amount, basis: rnd.ti_basis },
        free_rent_months: rnd.free_rent_months, term_months: rnd.term_months,
        options_note: rnd.options_note, escalator: rnd.escalator,
        opex_note: rnd.opex_note, expires_on: rnd.expires_on, note: rnd.note,
        purchase_price: null }
    : { rate: {}, ti: {} };

  const ls = (await c.query(
    "select * from lease where deal_id=$1 order by created_at desc limit 1", [dealId])).rows[0];
  bag.lease = ls
    ? { executed_on: ls.executed_on, commencement_on: ls.commencement_on,
        expiration_on: ls.expiration_on, term_months: ls.term_months,
        rate: { amount: ls.rate_amount, basis: ls.rate_basis },
        escalator: ls.escalator, ti_amount: ls.ti_amount,
        free_rent_months: ls.free_rent_months, options_note: ls.options_note,
        opex_structure: ls.opex_structure }
    : { rate: {} };

  // Structurally empty today and named so the map can point at it honestly:
  // nothing in the schema links a lender to a deal.
  bag.financing = { lender: null };
  bag.tenant = { credential: null };
  return bag;
}

// ---------- Deal Room helpers (field-base concurrency, not record version) ----------

const DEAL_ROOM_FIELDS = Object.freeze(["phase", "owner", "attention", "next_date"]);

function assertDealRoomField(field, value) {
  if (!DEAL_ROOM_FIELDS.includes(field))
    throw new ToolError({ error: "field_not_patchable", field, allowed: DEAL_ROOM_FIELDS });
  if (field === "attention" && typeof value !== "boolean")
    throw new ToolError({ error: "invalid_field_value", field, expected: "boolean" });
  if ((field === "phase" || field === "owner") && (typeof value !== "string" || !value.trim()))
    throw new ToolError({ error: "invalid_field_value", field, expected: "non-empty string" });
  if (field === "next_date" && value !== null &&
      (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)))
    throw new ToolError({ error: "invalid_field_value", field, expected: "YYYY-MM-DD or null" });
}

async function lockDealField(c, dealId, field) {
  // Same-field writers serialize; different fields deliberately use different
  // advisory keys and can commit independently on the same deal.
  await c.query(
    "select pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0)) /* dealroom:field-lock */",
    [dealId, field],
  );
}

async function latestFieldConflict(c, dealId, field, baseEventId) {
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

async function applyDealRoomField(c, actor, dealId, field, value, idempotencyKey, verb) {
  assertDealRoomField(field, value);
  const oldRow = await c.query(`select ${field} as value from deal where id=$1`, [dealId]);
  if (!oldRow.rows.length) throw new ToolError({ error: "not_found", table: "deal", id: dealId });
  await c.query(
    `update deal set ${field}=$2, updated_by=$3 where id=$1 /* dealroom:apply-field */`,
    [dealId, value, actor.id],
  );
  await writeEvent(c, actor, verb, "deal", dealId, {
    field,
    old: { [field]: oldRow.rows[0].value },
    new: { [field]: value },
    idempotency_key: idempotencyKey,
  });
  return { old_value: oldRow.rows[0].value, new_value: value };
}

// ---------- the registry ----------
// Each: { description, inputSchema, write: bool, humanOnly?: bool, handler(client, actor, args) }

export const TOOLS = {

  // ===== reads (carr_reader connection) =====

  "find": {
    write: false,
    description: "Search people, practices, buildings, deals, leads, vendors by name (fuzzy). Use FIRST when you only have a name; returns refs (L-/C-/V-) the write verbs take. Matches party.name / deal.name / client.roster_ref. Survivors come first and are counted separately from retired aliases: `refs`/`live_rows` are what you may write to, `retired_refs`/`retired_aliases` are tombstones of completed merges, kept navigable but never a target. Also returns the intro-graph edges touching the match (who can introduce whom), newest first. NOT the verb for a ref you already hold (catch-me-up takes that), and NOT the referral-path verb (who-do-we-know walks the graph). Read-only.",
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
      // `find "Henry Schein"` returned "Henry Pruett" — a trigram hit on one word —
      // and none of the 17 rows literally named Henry Schein.
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
      // reporting `duplicate_rows: 17` for Henry Schein and `13` for Musicologie —
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
        // role's ref. 0061 did exactly that to Musicologie — P-0111 is live and
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
        "select name, phase, lead_owner as owner, client_ref from v_deal_board where name ilike $1 limit 5",
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

      return { parties: parties.rows, deals: deals.rows, connections: connections.rows,
               organizations, note: notes.join(" ") };
    },
  },

  "who-do-we-know": {
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
        // asked for Henry Schein, which is 17 party rows, it answered "No record and
        // no graph node matches that name", which was simply false. A read-only
        // existence check has no reason to be narrower than the record.
        // MATCHING_RECORDS IS LIVE-ONLY, AND THE COUNT OF TOMBSTONES TRAVELS BESIDE
        // IT (loop #132). Unordered and capped at five, this block handed back five
        // tombstones for "Musicologie" — P-0840, P-1044, P-0909, P-0796 — as
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
      // visited-array guard is what keeps the Coleman <-> Nickelsen pair (a real
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
        // silently welds duplicate-name humans (the Tyrer condition).
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
    write: false,
    description: "The merged timeline (event + activity rows) for one deal, client, lead, or vendor, newest first, plus its narrative-file pointer (notes_path). Use before any conversation about a record.",
    inputSchema: { type: "object", properties: { ref: { type: "string", description: "L-204 / C-127 / V-CPA-006 / deal or party name" }, limit: { type: "integer", default: 20 } }, required: ["ref"] },
    handler: async (c, _a, args) => {
      const s = await resolveSubject(c, args.ref);
      const rows = await c.query(
        `select entry_kind, occurred_at, actor, verb, summary, detail, owed
         from v_subject_timeline where subject_type=$1 and subject_id=$2
         order by occurred_at desc limit $3`, [s.type, s.id, args.limit || 20]);
      return { subject: s, timeline: rows.rows };
    },
  },

  "today-triage": {
    write: false,
    description: "What needs attention now: due next-actions (next_action.due_on, suppressed by hold_until), critical_date rows inside 14 days, untriaged ingest items. The morning-brief substrate.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ items: (await c.query("select * from v_today_triage order by due_on nulls last limit 50")).rows }),
  },

  "deal-board": {
    write: false,
    description: "Open pipeline grouped by phase. Never exposes Salesforce commission/close-date placeholders (they are placeholders, not data).",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ deals: (await c.query("select * from v_deal_board where outcome is null order by phase_sort, name")).rows }),
  },

  "deal-room-board": {
    write: false,
    description: "The Deal Room's board read: every open deal with the live-board fields (owner, attention, next_date, current next step from the note thread). deal-board predates these columns and serves the pipeline render; this serves the board UI. Same placeholder rule: the two Salesforce placeholder columns never appear.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ deals: (await c.query(
      `select d.id, d.name, d.deal_type as type, d.phase, d.owner, d.attention,
              to_jsonb(d.next_date)#>>'{}' as next_date,
              (select n.text from deal_note n
                where n.deal_id = d.id and n.kind = 'next_step'
                order by n.created_at desc, n.id desc limit 1) as next_step
         from deal d
        where d.outcome is null
        order by d.name`)).rows }),
  },

  "lead-hot": {
    write: false,
    description: "Scored, unsuppressed leads (score, lane, est_lease_event, next_action_date). ALL of them surface — qualification is the human's job, never pre-filtered.",
    inputSchema: { type: "object", properties: { limit: { type: "integer", default: 30 } } },
    handler: async (c, _a, args) => ({ leads: (await c.query("select * from v_lead_hot order by score desc nulls last limit $1", [args.limit || 30])).rows }),
  },

  "stale-records": {
    write: false,
    description: "Active deals gone quiet 14+ days, measured on last_touch (see v_last_touch; a deal inherits its client's touch since 0033). Replaces the hand-run staleness sweep. Empty can mean 'nothing stale' OR 'nothing captured' — check v_capture_coverage before trusting a clean result.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ stale: (await c.query("select * from v_stale_records order by days_quiet desc nulls first")).rows }),
  },

  "integrity-digest": {
    write: false,
    description: "The heartbeat's lines: row counts, export freshness (dead-man; stale/last_ok per target), writes_by_dell_24h, norm_owed_open, merge_queue, triage queue.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ digest: (await c.query("select * from v_integrity_digest")).rows }),
  },

  // ===== writes (carr_writer connection, envelope enforced) =====

  "log-activity": {
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
    write: true,
    description: "Truck shorthand for log-activity: one-line call/text stamp. 'Called Hughes, going well' and done. Sets last_touch. Contact kinds only — a note is annotation, not a touch (it would not move Last Touch since 0017); use log-activity kind:note or an event for annotation.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, ref: { type: "string" },
      kind: { type: "string", enum: ["call","text"], default: "call" },
      summary: { type: "string" } }, required: ["idempotency_key","ref","summary"] },
    handler: async (c, actor, args) =>
      TOOLS["log-activity"].handler(c, actor, { ...args, kind: args.kind || "call" }),
  },

  // Added 2026-08-06 (loop #216): the missing half of the capture pipeline.
  // The ingest socket only ever INSERTS (status 'new'), so until this verb
  // existed nothing could LEAVE the queue — the digest's count could only
  // rise, and the Aug 6 triage found 40 rows with no way to clear one.
  // Deliberately NOT in any narrow profile (capture/away/probe/reviewer):
  // deciding what an inbox item became is an interactive judgment call.
  "triage-item": {
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
    write: true,
    description: "Set YOUR one open ball on a subject (replaces your previous open one; Dell's stays untouched — one ball per person per subject). Say whose turn it is and by when. Writes next_action (owner, due_on); set hold_until to keep a dated row from surfacing in today-triage.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, ref: { type: "string" },
      description: { type: "string" }, due_on: { type: "string", description: "YYYY-MM-DD, optional" } },
      required: ["idempotency_key","ref","description"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "set-next-action", args, async () => {
      const s = await resolveSubject(c, args.ref);
      // [amendment 8] Replacing an unfinished ball used to record the old one as
      // 'done'. It wasn't done — it was superseded. No-fabrication applies to
      // metadata too, and 'done' would inflate any completion measure built on this.
      await c.query(
        `update next_action set status='dropped', updated_by=$1 where subject_type=$2 and subject_id=$3
         and owner_id=$1 and status='open'`, [actor.id, s.type, s.id]);
      const r = await c.query(
        `insert into next_action (subject_type, subject_id, owner_id, due_on, description, created_by)
         values ($1,$2,$3,$4,$5,$3) returning id`, [s.type, s.id, actor.id, args.due_on || null, args.description]);
      await writeEvent(c, actor, "set-next-action", s.type, s.id,
        { new: { next_action: args.description, due: args.due_on }, idempotency_key: args.idempotency_key });
      return { ok: true, next_action_id: r.rows[0].id, subject: s };
    }),
  },

  "complete-action": {
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
      if (!r.rows.length) {
        const others = await c.query(
          `select a.slug as owner, n.description, n.due_on from next_action n
             join actor a on a.id = n.owner_id
            where n.subject_type=$1 and n.subject_id=$2 and n.status='open'`, [s.type, s.id]);
        throw new ToolError({ error: "no_open_action", subject: s,
          open_for_others: others.rows,
          hint: others.rows.length
            ? "the open ball on this subject belongs to someone else — only its holder can complete it"
            : "nobody holds an open action here; log-activity records what happened, set-next-action sets the next one" });
      }
      for (const row of r.rows)
        await writeEvent(c, actor, "complete-action", s.type, s.id,
          { field: "status", old: { status: "open" },
            new: { next_action: row.description, next_action_id: row.id, status: "done",
                   outcome: args.outcome || null },
            human_quote: args.outcome || null, idempotency_key: args.idempotency_key });
      return { ok: true, completed: r.rows.map(x => ({ next_action_id: x.id, description: x.description })),
               count: r.rows.length, subject: s };
    }),
  },

  "add-critical-date": {
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

  "update-deal": {
    write: true,
    description: "Field-level change to a deal (phase, segment, outcome, notes_path, salesforce_id). Requires base_version from a fresh read; a conflict means someone else wrote — ask the human, never retry blind. Phase must be an existing slug (list: pending/research/site_selection/negotiation/closing/closed + imported). salesforce_id is the reconciliation key back to the system of record and was NULL on all 40 deals as of 2026-08-02, which forced salesforce-diff to match on name — the matching that mis-filed thirteen deals in the first place; fill it during the Salesforce read. To move a deal to a DIFFERENT CLIENT, use reassign-deal: client_id is deliberately not settable here.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      base_version: { type: "integer" },
      fields: { type: "object", description: "subset of: phase, segment, outcome, closed_on, won_value, notes_path, salesforce_id, city, lane" } },
      required: ["idempotency_key","deal","base_version","fields"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-deal", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      await versionGuard(c, "deal", s.id, args.base_version);
      // city and lane joined the list in 0074, when they stopped being source_row
      // passthrough and became real columns. Before that they were unsettable,
      // which is why salesforce-diff could only ever REPORT a city move.
      const allowed = ["phase","segment","outcome","closed_on","won_value","notes_path",
                       "salesforce_id","city","lane"];
      if ("client_id" in args.fields) throw new ToolError({ error: "use_reassign_deal",
        hint: "moving a deal between clients is structural, not a field edit — use reassign-deal" });
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed });
      const old = (await c.query(`select ${keys.join(",")} from deal where id=$1`, [s.id])).rows[0];
      const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
      await c.query(`update deal set ${sets}, updated_by=$1 where id=$${keys.length + 2}`,
        [actor.id, ...keys.map(k => args.fields[k]), s.id]);
      for (const k of keys)
        await writeEvent(c, actor, "update-deal", "deal", s.id,
          { field: k, old: { [k]: old[k] }, new: { [k]: args.fields[k] }, idempotency_key: args.idempotency_key });
      return { ok: true, updated: keys };
    }),
  },

  "new-deal": {
    write: true, humanOnly: true,
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
          const held = await c.query("select name from deal where salesforce_id=$1", [args.salesforce_id]);
          throw new ToolError({ error: "salesforce_id_in_use", salesforce_id: args.salesforce_id,
            held_by: held.rows[0]?.name ?? null,
            hint: "one Opportunity maps to exactly one deal; check whether this deal already exists under another name" });
        }
        if (e.code === "23503") {
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
    write: true, humanOnly: true,
    description: "Move a deal onto the client it actually belongs to. THIS IS THE ONLY VERB THAT CHANGES deal.client_id — update-deal refuses that field on purpose, because re-pointing a deal changes whose book it sits in and is structural, not a field edit (the same reason set-lead owns the owner). Built 2026-08-02 for the Musicologie finding: an import filed THIRTEEN deals under C-131, twelve of them belonging to other franchisees who each had their own client record, so nine clients rendered as 'Active deal – no deal on file' while their deals sat under someone else's name. Requires base_version from a fresh read. Refuses a no-op, refuses a merged-away target, and records the old and new client on the event so the move is auditable. It does NOT touch the client rows themselves: a parent/sub-client structure (a national account over its franchisees) is expressed by party.org_id, not by moving deals up to the parent.",
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
    write: true,
    description: "THE handoff: make joe or dell the current lead on a deal. THIS IS THE ONLY VERB THAT SETS A DEAL'S OWNER — it writes the deal_participant row (role='lead') that v_deal_board exposes as lead_owner, so a null lead_owner is fixed here and NOT through update-deal. Closes the old lead row, opens the new one, one event. The database enforces exactly one current lead.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      new_lead: { type: "string", enum: ["joe","dell"] } },
      required: ["idempotency_key","deal","new_lead"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "set-lead", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      const na = await c.query("select id from actor where slug=$1", [args.new_lead]);
      const prev = await c.query(
        `update deal_participant set to_at=now() where deal_id=$1 and role='lead' and to_at is null
         returning actor_id`, [s.id]);
      await c.query(
        "insert into deal_participant (deal_id, actor_id, role, set_by) values ($1,$2,'lead',$3)",
        [s.id, na.rows[0].id, actor.id]);
      await writeEvent(c, actor, "set-lead", "deal", s.id,
        { old: { lead: prev.rows[0]?.actor_id || null }, new: { lead: args.new_lead }, idempotency_key: args.idempotency_key });
      return { ok: true, new_lead: args.new_lead };
    }),
  },

  "add-party": {
    write: true,
    description: "Create a party (person or org). CHECKS for existing matches first (email, similar name) and returns candidates INSTEAD of inserting when found — pass force_new:true only after the human confirms it is genuinely a different person. Never store 205-643-6555 (it is Dell's placeholder, not a contact).",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, name: { type: "string" },
      kind: { type: "string", enum: ["person","org"], default: "person" },
      org_name: { type: "string" }, phone: { type: "string" }, email: { type: "string" },
      city: { type: "string" }, state: { type: "string" }, county: { type: "string" },
      specialty: { type: "string" }, force_new: { type: "boolean" } },
      required: ["idempotency_key","name"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-party", args, async () => {
      if (args.phone && args.phone.replace(/\D/g, "").endsWith("2056436555"))
        throw new ToolError({ error: "placeholder_phone", hint: "205-643-6555 is never stored as a contact" });
      if (!args.force_new) {
        const cand = await c.query(
          `select id, name, email, city from party where merged_into is null and
             (($1::text is not null and lower(email)=lower($1)) or name % $2)
           order by similarity(name,$2) desc limit 5`, [args.email || null, args.name]);
        if (cand.rows.length)
          return { needs_confirm: true, candidates: cand.rows,
                   hint: "existing similar parties; reuse one, or resubmit force_new:true" };
      }
      // THE GENERATOR, CLOSED (0059, 2026-08-02). This line used to INSERT an org
      // unconditionally with no lookup, so every contact minted a private copy of
      // their own employer: Henry Schein existed as 17 org rows, one per rep,
      // Patterson Dental as 10, and all 415 org rows had exactly one inbound person
      // — a distribution with a single bucket, which is the signature. That is why
      // "who do we know at X" could not be answered: there was no X, only copies.
      // 0059 consolidated the 115 surplus rows AND added a unique index, so this
      // insert would now raise unique_violation on any org that already exists.
      // org_party_id() normalises the name, returns the existing survivor, and
      // mints only when genuinely new — so the duplicate count stops being a
      // running total. Placeholders like '(TBD — enrich)' deliberately still mint
      // separately: collapsing those would assert six unrelated people share an
      // employer, which is a fabricated fact, not a merge.
      let orgId = null;
      if (args.org_name) {
        const o = await c.query("select org_party_id($1,$2) as id", [args.org_name, actor.id]);
        orgId = o.rows[0].id;
      }
      const r = await c.query(
        `insert into party (kind,name,org_id,phone,email,city,state,county,specialty,created_by,updated_by)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10) returning id`,
        [args.kind || "person", args.name, orgId, args.phone || null, args.email || null,
         args.city || null, args.state || null, args.county || null, args.specialty || null, actor.id]);
      await writeEvent(c, actor, "add-party", "party", r.rows[0].id,
        { new: { name: args.name }, idempotency_key: args.idempotency_key });
      return { ok: true, party_id: r.rows[0].id };
    }),
  },

  // [ORDER 27 (a) + EXT (c)] One atomic call gives a deal its physical +
  // counterparty spine: building (exact-address match or create), space rows,
  // premises + premises_space, building_ownership rows, optional listing_side
  // participant. Vocabularies are the EXISTING CHECKs — nothing reopens here.
  "add-premises": {
    write: true,
    description: "Capture a deal's premises: the building (matched by exact address or created), its space rows (suite, SF, basis), the premises linkage, and the counterparty spine — building_ownership rows (owner / landlord_rep / property_manager / listing_agent) and optionally the deal's listing_side participant. Feeds the counterparty graph, the LOI Premises/Size slots, and the grid. Ownership parties resolve by REF, or are CREATED via new_party — an existing party is never name-matched.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      deal_ref: { type: "string", description: "deal name, or a C- ref when the client has exactly one deal" },
      label: { type: "string", description: "premises label, e.g. '4301 Spanish Trail, ~3,424 SF'" },
      building: { type: "object", properties: {
        address: { type: "string" }, city: { type: "string" }, state: { type: "string" },
        zip: { type: "string" }, name: { type: "string" } }, required: ["address"] },
      spaces: { type: "array", minItems: 1, items: { type: "object", properties: {
        suite: { type: "string" }, floor: { type: "number" },
        area_amount: { type: "number" },
        area_basis: { type: "string", enum: ["rentable","usable","county_heated","listed_unverified"],
          description: "what the PAPER says; omit for listed_unverified — never guessed upward" },
        condition: { type: "string" } }, required: [] } },
      ownership: { type: "array", maxItems: 10, items: { type: "object", properties: {
        party_ref: { type: "string", description: "ref of an EXISTING party (V-/C-/L-/T-)" },
        new_party: { type: "object", properties: {
          name: { type: "string" }, kind: { type: "string", enum: ["person","org"] },
          org_name: { type: "string" }, force_new: { type: "boolean" } }, required: ["name"] },
        kind: { type: "string", enum: ["owner","landlord_rep","property_manager","listing_agent"] },
        also_listing_side: { type: "boolean", description: "also record this party as the deal's listing_side participant" },
        source: { type: "string" } }, required: ["kind"] } },
    }, required: ["idempotency_key","deal_ref","label","building","spaces"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-premises", args, async () => {
      // deal resolution: a C- ref resolves through the client's deals — exactly
      // one or refuse (amendment 7's rule, applied to the deal hop).
      let dealId;
      const s = await resolveSubject(c, args.deal_ref);
      if (s.type === "deal") dealId = s.id;
      else if (s.type === "client") {
        const d = await c.query("select id, name from deal where client_id = $1", [s.id]);
        if (!d.rows.length) throw new ToolError({ error: "no_deal_for_client", deal_ref: args.deal_ref });
        if (d.rows.length > 1)
          throw new ToolError({ error: "needs_disambiguation", deal_ref: args.deal_ref,
            candidates: d.rows.map(x => ({ name: x.name })), hint: "pass the deal name" });
        dealId = d.rows[0].id;
      } else throw new ToolError({ error: "not_a_deal", deal_ref: args.deal_ref, resolved: s.type });

      // building: exact-address match (case-insensitive), else create. More than
      // one match is a data problem to surface, never a coin flip.
      const b = args.building;
      const match = await c.query(
        `select id, address, city from building
          where lower(address) = lower($1) and merged_into is null`, [b.address]);
      let buildingId, buildingCreated = false;
      if (match.rows.length > 1)
        throw new ToolError({ error: "needs_disambiguation", address: b.address,
          candidates: match.rows, hint: "two building rows share this address — merge or pass city" });
      if (match.rows.length === 1) buildingId = match.rows[0].id;
      else {
        const ins = await c.query(
          `insert into building (address, city, state, zip, name, created_by, updated_by)
           values ($1,$2,$3,$4,$5,$6,$6) returning id`,
          [b.address, b.city || null, b.state || null, b.zip || null, b.name || null, actor.id]);
        buildingId = ins.rows[0].id; buildingCreated = true;
      }

      const spaceIds = [];
      for (const sp of args.spaces) {
        // [ORDER 34 review, fix 6] surface the schema's band as a ToolError,
        // not a truncated raw SQL check_violation.
        if (sp.area_amount != null && (sp.area_amount < 50 || sp.area_amount > 500000))
          throw new ToolError({ error: "area_out_of_band", area_amount: sp.area_amount,
            hint: "space.area_amount accepts 50-500000 SF; a 3,424 SF suite is 3424, not 3.424" });
        const basis = sp.area_amount != null ? (sp.area_basis || "listed_unverified") : sp.area_basis || null;
        const ins = await c.query(
          `insert into space (building_id, suite, floor, area_amount, area_basis, condition, created_by, updated_by)
           values ($1,$2,$3,$4,$5,$6,$7,$7) returning id`,
          [buildingId, sp.suite || null, sp.floor ?? null, sp.area_amount ?? null, basis, sp.condition || null, actor.id]);
        spaceIds.push(ins.rows[0].id);
      }

      const pr = await c.query(
        `insert into premises (deal_id, label, created_by) values ($1,$2,$3) returning id`,
        [dealId, args.label, actor.id]);
      const premisesId = pr.rows[0].id;
      for (const sid of spaceIds)
        await c.query("insert into premises_space (premises_id, space_id) values ($1,$2)", [premisesId, sid]);

      const ownershipOut = [];
      for (const o of args.ownership || []) {
        let partyId;
        if (o.party_ref && o.new_party)
          throw new ToolError({ error: "conflicting_party_inputs",
            hint: "an ownership row carries party_ref OR new_party, never both" });
        if (o.party_ref) partyId = await resolvePartyByRef(c, o.party_ref);
        else if (o.new_party) {
          // Same dedup intent as add-party's guard, expressed as a THROW so a
          // refused attempt never lands a tool_call row (safer for key hygiene;
          // add-party returns needs_confirm inline instead — deliberate divergence).
          if (!o.new_party.force_new) {
            const cand = await c.query(
              `select id, name, city from party where merged_into is null and name % $1
               order by similarity(name, $1) desc limit 5`, [o.new_party.name]);
            if (cand.rows.length)
              throw new ToolError({ error: "needs_confirm", name: o.new_party.name,
                candidates: cand.rows,
                hint: "similar parties exist; pass party_ref if it is one of them (when it has a ref), or new_party.force_new:true after the human confirms it is a different person" });
          }
          // Same generator, second site — see the note in add-party. 0059's unique
          // index makes the old blind insert a unique_violation waiting to happen.
          let orgId = null;
          if (o.new_party.org_name) {
            const og = await c.query("select org_party_id($1,$2) as id",
                                     [o.new_party.org_name, actor.id]);
            orgId = og.rows[0].id;
          }
          const np = await c.query(
            `insert into party (kind, name, org_id, created_by, updated_by)
             values ($1,$2,$3,$4,$4) returning id`,
            [o.new_party.kind || "person", o.new_party.name, orgId, actor.id]);
          partyId = np.rows[0].id;
        } else throw new ToolError({ error: "ownership_needs_party",
          hint: "each ownership row carries party_ref or new_party" });
        await c.query(
          `insert into building_ownership (building_id, party_id, kind, source, created_by)
           values ($1,$2,$3,$4,$5)`,
          [buildingId, partyId, o.kind, o.source || "stated", actor.id]);
        if (o.also_listing_side) {
          // [ORDER 34 review, fix 5] check-before-insert: a second capture pass
          // on the same deal must not duplicate the participant row (which would
          // double-count the deal in counterparty history).
          const dup = await c.query(
            `select 1 from deal_participant
              where deal_id=$1 and party_id=$2 and role='listing_side' and to_at is null`,
            [dealId, partyId]);
          if (!dup.rows.length)
            await c.query(
              `insert into deal_participant (deal_id, party_id, role, set_by)
               values ($1,$2,'listing_side',$3)`, [dealId, partyId, actor.id]);
        }
        ownershipOut.push({ party_id: partyId, kind: o.kind,
          listing_side: !!o.also_listing_side });
      }

      await writeEvent(c, actor, "add-premises", "deal", dealId,
        { new: { premises: premisesId, building: buildingId, building_created: buildingCreated,
                 spaces: spaceIds.length, ownership: ownershipOut.length },
          idempotency_key: args.idempotency_key });
      return { ok: true, deal_id: dealId, premises_id: premisesId, building_id: buildingId,
               building_created: buildingCreated, space_ids: spaceIds, ownership: ownershipOut };
    }),
  },

  "new-lead": {
    write: true,
    description: "Create a lead over a new or existing party; mints the next L-ref atomically. Sets lead_stage and owner_id/owner_label. Stage must be an existing lead_stage slug (they were imported from the live registry).",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      party_id: { type: "string", description: "from add-party or find" },
      stage: { type: "string" }, lane: { type: "string" }, segment: { type: "string" },
      source_type: { type: "string" }, source_detail: { type: "string" } },
      required: ["idempotency_key","party_id","stage"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "new-lead", args, async () => {
      const ref = (await c.query("select 'L-' || lpad(nextval('ref_lead_seq')::text, 3, '0') as r")).rows[0].r;
      const r = await c.query(
        `insert into lead (registry_ref, party_id, stage, lane, segment, source_type, source_detail,
           owner_id, owner_label, created_by, updated_by)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$8,$8) returning id`,
        [ref, args.party_id, args.stage, args.lane || null, args.segment || null,
         args.source_type || null, args.source_detail || null, actor.id, actor.display]);
      await writeEvent(c, actor, "new-lead", "lead", r.rows[0].id,
        { new: { ref }, idempotency_key: args.idempotency_key });
      return { ok: true, lead_id: r.rows[0].id, ref };
    }),
  },

  "promote-pool": {
    write: true,
    description: "Promote a candidate_pool row into a real lead: mints the party, mints the next L-ref, copies the identity, contact and est-lease-event stamps across, points the pool row at the new lead and flips it to 'promoted'. ONE-WAY BY DESIGN — there is no demote verb; a lead created in error is worked through the lead's own lifecycle. Only a row whose status is still 'pool' can promote: a 'promoted' row would duplicate, and a 'suppressed_dup' row already points at the record it duplicates. A dup_tier 'review' row IS promotable — that tier exists precisely so a weak match never silently blocks Joe. Read the row from v_pool first and pass its version as base_version.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      pool_id: { type: "string", description: "candidate_pool.id, from v_pool" },
      base_version: { type: "integer", description: "the pool row's version, from a fresh read" },
      stage: { type: "string", description: "lead_stage slug — a promoted lead is one Joe is working, so it needs a real stage" },
      lane: { type: "string", description: "lead_lane slug (optional)" },
      source_detail: { type: "string", description: "why this one, now — free text provenance" } },
      required: ["idempotency_key","pool_id","base_version","stage"] },
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

      // The org, when the source named one, becomes its own party so the lead
      // hangs off a person who belongs to a practice — the shape add-party and
      // every export view already assume.
      let orgId = null;
      if (p.org_name) {
        orgId = (await c.query(
          "insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id",
          [p.org_name, actor.id])).rows[0].id;
      }
      const partyId = (await c.query(
        `insert into party (kind,name,org_id,phone,email,city,state,county,specialty,
                            created_by,updated_by)
         values ('person',$1,$2,$3,$4,$5,$6,$7,$8,$9,$9) returning id`,
        [p.name, orgId, p.phone || null, p.email || null, p.city || null,
         p.state || null, p.county || null, p.vertical || null, actor.id])).rows[0].id;

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

  "new-client": {
    write: true,
    description: "Create a client over a party; mints the next C-ref (roster_ref). Sets client_status and acquisition_source. ALWAYS ask how they found us (acquisition_source) at intake — consult attribution starts day one.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, party_id: { type: "string" },
      status: { type: "string" }, vertical: { type: "string" }, subtype: { type: "string" },
      acquisition_source: { type: "string" }, acquisition_detail: { type: "string" } },
      required: ["idempotency_key","party_id","status","acquisition_source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "new-client", args, async () => {
      const ref = (await c.query("select 'C-' || lpad(nextval('ref_client_seq')::text, 3, '0') as r")).rows[0].r;
      const r = await c.query(
        `insert into client (roster_ref, party_id, status, vertical, subtype, acquisition_source,
           acquisition_detail, owner_id, owner_label, created_by, updated_by)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$8,$8) returning id`,
        [ref, args.party_id, args.status, args.vertical || null, args.subtype || null,
         args.acquisition_source, args.acquisition_detail || null, actor.id, actor.display]);
      await writeEvent(c, actor, "new-client", "client", r.rows[0].id,
        { new: { ref }, idempotency_key: args.idempotency_key });
      return { ok: true, client_id: r.rows[0].id, ref };
    }),
  },

  "new-vendor": {
    write: true,
    description: "Create a vendor over a party; sets vendor stage; mints V-<CODE>-### (pass the category code explicitly: CPA, LEN, GC...). A Claude-found vendor enters at the prospect stage until a real call happens — that is a standing rule, not a suggestion.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, party_id: { type: "string" },
      category: { type: "string" }, ref_code: { type: "string", description: "CPA / LEN / GC / ..." },
      stage: { type: "string" } }, required: ["idempotency_key","party_id","category","ref_code","stage"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "new-vendor", args, async () => {
      const ref = (await c.query(
        "select 'V-' || $1 || '-' || lpad(nextval('ref_vendor_seq')::text, 3, '0') as r",
        [args.ref_code.toUpperCase()])).rows[0].r;
      const r = await c.query(
        `insert into vendor (vendor_ref, party_id, category, stage, owner_id, owner_label,
           created_by, updated_by) values ($1,$2,$3,$4,$5,$6,$5,$5) returning id`,
        [ref, args.party_id, args.category, args.stage, actor.id, actor.display]);
      await writeEvent(c, actor, "new-vendor", "vendor", r.rows[0].id,
        { new: { ref }, idempotency_key: args.idempotency_key });
      return { ok: true, vendor_id: r.rows[0].id, ref };
    }),
  },

  "update-vendor": {
    write: true,
    description: "Field-level change to a vendor (stage, seeking, offers, referral_active, territory, out_of_market). base_version required.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, vendor: { type: "string" },
      base_version: { type: "integer" }, fields: { type: "object" } },
      required: ["idempotency_key","vendor","base_version","fields"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-vendor", args, async () => {
      const s = await resolveSubject(c, args.vendor);
      if (s.type !== "vendor") throw new ToolError({ error: "not_a_vendor", resolved: s });
      await versionGuard(c, "vendor", s.id, args.base_version);
      // [0069] category_slug + verticals joined the list — the columns existed since
      // 0001/0050 but no verb could reach them, which left the 63 null-category
      // vendors unfixable (loop #199). category_slug, not free-text category: 0050
      // deprecated the free-text field after a stage value got stored as a
      // profession, and reopening it here would reopen that defect.
      const allowed = ["stage","seeking","offers","referral_active","territory","rivalry_group","out_of_market","intro_notes","category_slug","verticals"];
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed });
      // Pre-validate rather than letting the FK abort the transaction: a poisoned
      // transaction cannot even fetch the slug list to explain itself.
      if (keys.includes("category_slug") && args.fields.category_slug !== null) {
        const slugs = (await c.query("select slug from vendor_category order by sort")).rows.map(r => r.slug);
        if (!slugs.includes(args.fields.category_slug))
          throw new ToolError({ error: "unknown_category_slug", got: args.fields.category_slug, allowed: slugs,
            hint: "a rare type is an INSERT into vendor_category by a human, never a guess" });
      }
      if (keys.includes("verticals") && args.fields.verticals !== null &&
          !(Array.isArray(args.fields.verticals) && args.fields.verticals.every(v => typeof v === "string")))
        throw new ToolError({ error: "verticals_not_array", hint: 'pass an array of strings, e.g. ["dental","vet"]' });
      const old = (await c.query(`select ${keys.join(",")} from vendor where id=$1`, [s.id])).rows[0];
      const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
      await c.query(`update vendor set ${sets}, updated_by=$1 where id=$${keys.length + 2}`,
        [actor.id, ...keys.map(k => args.fields[k]), s.id]);
      for (const k of keys)
        await writeEvent(c, actor, "update-vendor", "vendor", s.id,
          { field: k, old: { [k]: old[k] }, new: { [k]: args.fields[k] }, idempotency_key: args.idempotency_key });
      return { ok: true, updated: keys };
    }),
  },

  // [0069, loop #199] The promotion path from evidence to live contact data. Before
  // this verb, record-finding could store a verified cell/email/title beside the
  // record but NOTHING could write it onto the party — contact-enrichment-weekly
  // hit the same wall every Thursday, and the 2026-08-06 Outlook mining run left
  // 8 verified facts stranded in record_flag.
  "update-party-contact": {
    write: true,
    description: "Promote a VERIFIED contact fact onto a party: phone (office), cell (mobile), email, title, city, county — CONTACT FACTS ONLY. Identity fields (name, org, npi, specialty) are deliberately out of reach: a discrepancy there goes through record-finding's proposes_correction and is applied by the owning partner, never by this verb (rule 5d44d3f3). source is REQUIRED on every call — provenance is binding, and the usual value is the record-finding row or thread being promoted. Accepts any ref (P-####, V-/C-/L-/T-, or a name); a role ref resolves to the PERSON under it, and a merged party hops to its survivor (reported in the result). base_version is the PARTY's version, from a fresh read. Placeholder guard: a CARR agent's own number or any carr.us address in a client/vendor contact field is a placeholder, never data — refused, not stored.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      party: { type: "string", description: "P-#### ref, a role ref (V-/C-/L-/T-), or a name" },
      base_version: { type: "integer" },
      fields: { type: "object", properties: {
        phone: { type: ["string","null"] }, cell: { type: ["string","null"] },
        email: { type: ["string","null"] }, title: { type: ["string","null"] },
        city: { type: ["string","null"] }, county: { type: ["string","null"] } },
        additionalProperties: false },
      source: { type: "string", description: "where the fact came from: 'record-finding <kind> observed <date>', 'outlook thread <subject> <date>', 'practice website', ..." } },
      required: ["idempotency_key","party","base_version","fields","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-party-contact", args, async () => {
      if (!args.source || !args.source.trim())
        throw new ToolError({ error: "missing_source", hint: "a contact fact without provenance is a rumour; say where it came from" });
      const s = await resolveSubject(c, args.party);
      if (s.type === "deal")
        throw new ToolError({ error: "not_a_party", hint: "a deal has no contact fields; pass the person or their role ref" });
      let partyId;
      if (s.type === "party") partyId = s.id;
      else {
        const r = await c.query(
          "select party_id from v_ref_index where subject_type=$1 and subject_id=$2", [s.type, s.id]);
        if (!r.rows.length || !r.rows[0].party_id)
          throw new ToolError({ error: "no_party_under_ref", resolved: s });
        partyId = r.rows[0].party_id;
      }
      // A merged party is a pointer; writing to a tombstone strands the fact.
      const hop = await c.query("select merged_into from party where id=$1", [partyId]);
      if (!hop.rows.length) throw new ToolError({ error: "not_found", table: "party", id: partyId });
      const hopped = hop.rows[0].merged_into !== null;
      if (hopped) partyId = hop.rows[0].merged_into;

      const allowed = ["phone","cell","email","title","city","county"];
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed,
        hint: "contact facts only; identity corrections go through record-finding proposes_correction" });

      // Placeholder rule 54e2bcb9: an agent's own details standing in for a contact
      // nobody had. Stored, they read as enriched while being emptier than a blank.
      for (const k of ["phone","cell"]) {
        if (args.fields[k] && String(args.fields[k]).replace(/\D/g, "").endsWith("2056436555"))
          throw new ToolError({ error: "placeholder_phone", field: k,
            hint: "205-643-6555 is a CARR agent's own line, never a contact — record the field as unknown instead" });
      }
      if (args.fields.email && /@carr\.us\s*$/i.test(String(args.fields.email).trim()))
        throw new ToolError({ error: "placeholder_email",
          hint: "a carr.us address in a contact field is a placeholder, never data — record the field as unknown instead" });

      await versionGuard(c, "party", partyId, args.base_version);
      const clean = {};
      for (const k of keys)
        clean[k] = (k === "phone" || k === "cell") ? fmtPhoneUS(args.fields[k]) : args.fields[k];
      const old = (await c.query(`select ${keys.join(",")} from party where id=$1`, [partyId])).rows[0];
      const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
      await c.query(`update party set ${sets}, updated_by=$1 where id=$${keys.length + 2}`,
        [actor.id, ...keys.map(k => clean[k]), partyId]);
      for (const k of keys)
        await writeEvent(c, actor, "update-party-contact", "party", partyId,
          { field: k, old: { [k]: old[k] }, new: { [k]: clean[k] },
            agent_rationale: `source: ${args.source}`, idempotency_key: args.idempotency_key });
      return { ok: true, party_id: partyId, updated: keys, hopped_to_survivor: hopped || undefined };
    }),
  },

  "record-counter": {
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

  // ===== [ORDER 13] the document factory =====

  "register-template": {
    write: true,
    description: "Register (or re-version) a CARR template so prepare-document can fill it. Writes the template row; field_map is the reviewable contract between the template's slots and the record layer — a map carrying unreviewed:true is REFUSED by prepare-document until a human has read it. Registering never touches the template file; source_path points at the real file in Templates/ and nothing writes there, ever.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      slug: { type: "string", description: "stable id, e.g. loi-lease / loi-purchase / loi-grid" },
      name: { type: "string" },
      source_path: { type: "string", description: "vault-relative path to the REAL template file" },
      template_version: { type: "string" },
      field_map: { type: "object", description: "{unreviewed, template_kind, slots{...}} — see fill-engine/field-maps/" },
      output_kinds: { type: "array", items: { type: "string" }, description: "defaults to {working,pdf}" },
      replace: { type: "boolean", description: "true to re-version an existing slug; without it an existing slug is refused so a map is never silently overwritten" } },
      required: ["idempotency_key","slug","name","source_path","template_version","field_map"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "register-template", args, async () => {
      const map = args.field_map;
      if (!map || typeof map !== "object" || !map.slots)
        throw new ToolError({ error: "bad_field_map", hint: "field_map needs a slots object; see fill-engine/field-maps/" });
      const kinds = args.output_kinds && args.output_kinds.length ? args.output_kinds : ["working","pdf"];
      const prior = await c.query("select id, template_version, field_map from doc_template where slug=$1", [args.slug]);
      if (prior.rows.length && !args.replace)
        throw new ToolError({ error: "template_exists", slug: args.slug,
          current_version: prior.rows[0].template_version,
          hint: "pass replace:true to re-version; a field map is a reviewed artifact and is never overwritten by accident" });
      let id;
      if (prior.rows.length) {
        id = prior.rows[0].id;
        await c.query(
          `update doc_template set name=$1, source_path=$2, template_version=$3,
             field_map=$4, output_kinds=$5, active=true where id=$6`,
          [args.name, args.source_path, args.template_version, JSON.stringify(map), kinds, id]);
        await writeEvent(c, actor, "register-template", "doc_template", id,
          { field: "field_map", old: { template_version: prior.rows[0].template_version },
            new: { template_version: args.template_version, unreviewed: !!map.unreviewed },
            idempotency_key: args.idempotency_key });
      } else {
        const r = await c.query(
          `insert into doc_template (slug, name, source_path, template_version, field_map, output_kinds, created_by)
           values ($1,$2,$3,$4,$5,$6,$7) returning id`,
          [args.slug, args.name, args.source_path, args.template_version,
           JSON.stringify(map), kinds, actor.id]);
        id = r.rows[0].id;
        await writeEvent(c, actor, "register-template", "doc_template", id,
          { new: { slug: args.slug, template_version: args.template_version, unreviewed: !!map.unreviewed },
            idempotency_key: args.idempotency_key });
      }
      const slots = Object.keys(map.slots).length;
      return { ok: true, template_id: id, slug: args.slug, slots,
               unreviewed: !!map.unreviewed,
               note: map.unreviewed
                 ? "map is UNREVIEWED: prepare-document refuses it unless allow_unreviewed:true"
                 : "map is reviewed" };
    }),
  },

  "prepare-document": {
    write: true,
    description: "Produce a document RECORD and its fill plan for one deal from a registered template: pulls the deal, client, premises, newest our-side negotiation round and lease, resolves every mapped slot, and returns the exact edits the local fill engine applies plus the OWED list. A field the records cannot answer is written into the document as a visible OWED marker — never invented, and never left showing the template's own placeholder number. Produces a draft; NOTHING is ever sent.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      deal: { type: "string", description: "deal name or a C-### client ref" },
      template: { type: "string", description: "doc_template slug" },
      options: { type: "object", description: "choices for the template's option slots, by slot name: an index or the literal text" },
      allow_unreviewed: { type: "boolean", description: "required to run against a field map still marked unreviewed" },
      note: { type: "string" } },
      required: ["idempotency_key","deal","template"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "prepare-document", args, async () => {
      const t = (await c.query(
        "select * from doc_template where slug=$1 and active", [args.template])).rows[0];
      if (!t) throw new ToolError({ error: "template_not_found", template: args.template,
        hint: "register-template first; slugs look like loi-lease / loi-purchase / loi-grid" });
      const map = t.field_map;
      if (map.unreviewed && !args.allow_unreviewed)
        throw new ToolError({ error: "template_unreviewed", template: args.template,
          hint: "the field map has not been reviewed by a human. Review it, re-register with unreviewed removed, or pass allow_unreviewed:true for a deliberate draft run." });

      const s = await resolveSubject(c, args.deal);
      let dealId = null, clientId = null;
      if (s.type === "deal") {
        dealId = s.id;
        clientId = (await c.query("select client_id from deal where id=$1", [dealId])).rows[0].client_id;
      } else if (s.type === "client") {
        clientId = s.id;
        const open = await c.query(
          "select id, name from deal where client_id=$1 and outcome is null order by created_at desc", [clientId]);
        if (open.rows.length !== 1)
          throw new ToolError({ error: open.rows.length ? "needs_disambiguation" : "no_open_deal",
            candidates: open.rows.map(x => ({ name: x.name })),
            hint: "a document belongs to a deal; name the deal" });
        dealId = open.rows[0].id;
      } else throw new ToolError({ error: "not_a_deal_or_client", resolved: s });

      const bag = await buildRecordBag(c, dealId, clientId);
      const options = args.options || {};
      if (options.tenant_credential) bag.tenant.credential = options.tenant_credential;

      const edits = [], owed = [], carried = [], partial = [];
      for (const [name, slot] of Object.entries(map.slots)) {
        const r = resolveSlot(name, slot, bag, options);
        if (r.carried) { carried.push(r.carried); continue; }
        if (r.owed) { owed.push(r.owed); edits.push({ where: r.owed.where, text: owedMarker(r.owed), slot: name, owed: true }); continue; }
        edits.push(r.edit);
        if (r.partial) partial.push(r.partial);
      }
      // A repeat block (the LOI grid's four property column-pairs) resolves as a
      // unit: with no premises rows there is nothing to iterate, and 160 owed
      // entries would bury the one fact that matters.
      if (map.repeat) {
        const list = bagGet(bag, map.repeat.over) || [];
        if (!list.length)
          owed.push({ slot: map.repeat.name, label: map.repeat.label, where: "repeat",
                      kind: "repeat", wanted: map.repeat.over, why: map.repeat.owed_note });
        else
          owed.push({ slot: map.repeat.name, label: map.repeat.label, where: "repeat",
                      kind: "repeat_unimplemented", wanted: `${list.length} rows present`,
                      why: "premises rows exist but the repeat filler is not built; ORDER 13 built the single-subject path." });
      }

      const doc = await c.query(
        `insert into document (template_id, deal_id, client_id, prepared_by, sent_status, note)
         values ($1,$2,$3,$4,'draft',$5) returning id, prepared_at`,
        [t.id, dealId, clientId, actor.id, args.note || null]);
      await writeEvent(c, actor, "prepare-document", "deal", dealId,
        { field: "document", new: { template: t.slug, document_id: doc.rows[0].id,
            filled: edits.length - owed.filter(o => o.where !== "repeat").length, owed: owed.length },
          agent_rationale: `prepare-document ${t.slug}: ${owed.length} owed field(s)`,
          idempotency_key: args.idempotency_key });

      const safe = v => String(v || "document").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "");
      const d = new Date();
      const p = n => String(n).padStart(2, "0");
      const stamp = `${p(d.getMonth() + 1)}-${p(d.getDate())}-${d.getFullYear()}`;
      return {
        ok: true,
        document_id: doc.rows[0].id,
        template: { slug: t.slug, name: t.name, source_path: t.source_path,
                    template_version: t.template_version, kind: map.template_kind,
                    unreviewed: !!map.unreviewed, output_kinds: t.output_kinds },
        deal: { id: dealId, name: bag.deal.name, client_ref: bag.client.ref,
                client_name: bag.client.display_name, org_name: bag.client.org_name },
        // Deal name, not the client party name: it is what Joe's own OneDrive
        // deal folders are named, and the org row can carry an fka suffix.
        basename: `${safe(bag.deal.name || bag.client.display_name)}-${safe(t.name)}-DRAFT-${stamp}`,
        edits, owed, partial, carried,
        status: "draft",
        human_gate: "This is a DRAFT for Joe to review. No send verb exists; update-document-status records his word, it does not send.",
      };
    }),
  },

  "update-document-status": {
    write: true,
    description: "Record what a HUMAN says happened to a prepared document — sets document status: draft -> handed_to_joe -> sent. It records a statement; it does not send anything and no verb anywhere does. Also the place to record the lint and leak-guard results and the filed attachments once the local fill run has produced them.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      document_id: { type: "string" },
      status: { type: "string", enum: ["draft","handed_to_joe","sent"] },
      human_quote: { type: "string", description: "the partner's own words, when he said it" },
      working_attachment: { type: "string" }, pdf_attachment: { type: "string" },
      lint_passed: { type: "boolean" }, leak_check_passed: { type: "boolean" },
      note: { type: "string" } },
      required: ["idempotency_key","document_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-document-status", args, async () => {
      const cur = (await c.query("select * from document where id=$1", [args.document_id])).rows[0];
      if (!cur) throw new ToolError({ error: "document_not_found", document_id: args.document_id });
      // A 'sent' claim is a human statement about the world. Automation saying it
      // would be the system asserting a send that no code in it can perform.
      if (args.status === "sent" && actor.kind !== "human")
        throw new ToolError({ error: "human_only",
          hint: "'sent' records that a partner sent it; only a human can state that" });
      const sets = [], vals = [];
      const put = (col, v) => { if (v !== undefined && v !== null) { vals.push(v); sets.push(`${col}=$${vals.length}`); } };
      put("sent_status", args.status);
      put("working_attachment", args.working_attachment);
      put("pdf_attachment", args.pdf_attachment);
      put("lint_passed", args.lint_passed);
      put("leak_check_passed", args.leak_check_passed);
      put("note", args.note);
      if (!sets.length) throw new ToolError({ error: "nothing_to_update" });
      vals.push(args.document_id);
      await c.query(`update document set ${sets.join(", ")} where id=$${vals.length}`, vals);
      await writeEvent(c, actor, "update-document-status", "deal", cur.deal_id,
        { field: "document.sent_status", old: { sent_status: cur.sent_status },
          new: { document_id: args.document_id, sent_status: args.status || cur.sent_status },
          human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
      return { ok: true, document_id: args.document_id, sent_status: args.status || cur.sent_status };
    }),
  },

  "link-parties": {
    write: true,
    description: "Record an intro-graph edge (a party_link row): who can introduce whom, who referred whom. Feeds who-do-we-know (find returns these) and the reciprocity ledger. kind comes from the party_link_kind table — today knows, intro, intro_received, can_introduce, works_with, referral — and the same edge recorded twice returns the first one, never a duplicate.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, from_party: { type: "string" }, to_party: { type: "string" },
      kind: { type: "string", description: "a slug from party_link_kind: knows, intro, intro_received, can_introduce, works_with, referral" },
      note: { type: "string" } }, required: ["idempotency_key","from_party","to_party","kind"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "link-parties", args, async () => {
      // [ORDER 18] The old hard-coded enum (can_introduce/intro_sent/intro_received/
      // works_with/referred) is retired. It was one of the two vocabularies ORDER 17
      // found: this verb could not write a single kind the backfill used, and the
      // backfill could not write one this verb offered. One table, one vocabulary.
      const kind = await validateLinkKind(c, args.kind);
      // Upsert against 0020's unique index. Before it, two taps wrote two identical
      // edges and nothing complained. `do nothing` returns no row on conflict, so
      // the existing edge is read back and returned — the caller gets the edge it
      // asked for either way, and learns which case it was.
      const ins = await c.query(
        `insert into party_link (from_party, to_party, kind, note, source, created_by)
         values ($1,$2,$3,$4,'stated',$5)
         on conflict (from_party, to_party, kind) do nothing
         returning id`,
        [args.from_party, args.to_party, kind, args.note || null, actor.id]);
      if (!ins.rows.length) {
        const cur = await c.query(
          "select id from party_link where from_party=$1 and to_party=$2 and kind=$3",
          [args.from_party, args.to_party, kind]);
        // No event row: nothing changed in the record, and an event that says a
        // link was made when none was is the kind of fiction the ledger exists to
        // prevent. The tool_call row (envelope) still records that it was asked.
        return { ok: true, link_id: cur.rows[0].id, existing: true };
      }
      await writeEvent(c, actor, "link-parties", "party", args.from_party,
        { new: { kind, to: args.to_party }, idempotency_key: args.idempotency_key });
      return { ok: true, link_id: ins.rows[0].id, existing: false };
    }),
  },

  "confirm-merge": {
    write: true, humanOnly: true,
    description: "HUMAN-confirmed merge of two duplicate parties: sets merged_into on the loser so it becomes a pointer to the survivor. Only after a human has looked at both records — the Garabadian rule means nothing auto-merges, ever.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, survivor_party: { type: "string" }, merged_party: { type: "string" } },
      required: ["idempotency_key","survivor_party","merged_party"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "confirm-merge", args, async () => {
      // [0069] Inputs used to be assumed party uuids; a V- ref passed here died in
      // the update below as "invalid input syntax for type uuid" — an internal
      // error where a routing answer belonged (loop #199). Resolve refs properly,
      // and name the one case this verb structurally cannot do.
      const toParty = async (input) => {
        if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(input))
          return { partyId: input, via: "uuid" };
        const s = await resolveSubject(c, input);
        if (s.type === "party") return { partyId: s.id, via: "party" };
        if (s.type === "deal")
          throw new ToolError({ error: "not_a_party", ref: input, hint: "a deal cannot be merged; pass a party or role ref" });
        const r = await c.query(
          "select party_id from v_ref_index where subject_type=$1 and subject_id=$2", [s.type, s.id]);
        if (!r.rows.length || !r.rows[0].party_id)
          throw new ToolError({ error: "no_party_under_ref", ref: input, resolved: s });
        return { partyId: r.rows[0].party_id, via: s.type, roleId: s.id };
      };
      const surv = await toParty(args.survivor_party);
      const merg = await toParty(args.merged_party);
      if (surv.partyId === merg.partyId) {
        if (surv.via === "vendor" && merg.via === "vendor" && surv.roleId !== merg.roleId)
          throw new ToolError({ error: "one_party_two_vendor_rows",
            hint: "these two vendor refs ride ONE party — that is a vendor-row duplicate, not a party duplicate. Use merge-vendor-rows." });
        throw new ToolError({ error: "same_party", hint: "a party cannot be merged into itself" });
      }
      args = { ...args, survivor_party: surv.partyId, merged_party: merg.partyId };

      // THE ROLE ROWS MOVE WITH THE PERSON. Until 2026-08-02 this verb set merged_into and
      // nothing else, so the loser's lead/client/vendor rows were left pointing at a party
      // that no longer resolves — they vanished from every party-based view while still
      // existing. Three such orphans predated the fix, and merging Petersen produced a
      // fourth: his lead L-201 disappeared and he read as "Client" only, when the entire
      // point of the merge was one person holding BOTH roles.
      const moved = {};
      for (const t of ["lead", "client", "vendor"]) {
        const r = await c.query(
          `update ${t} set party_id=$1, updated_by=$2 where party_id=$3 returning id`,
          [args.survivor_party, actor.id, args.merged_party]);
        if (r.rows.length) moved[t] = r.rows.length;
      }

      await c.query("update party set merged_into=$1, updated_by=$2 where id=$3",
        [args.survivor_party, actor.id, args.merged_party]);

      // A survivor holding two rows of the SAME role is a second duplicate hiding behind
      // the first. Reported, never auto-resolved: which of two lead records is authoritative
      // is a human call, and guessing is how the wrong Beasley got merged.
      const dup = await c.query(
        `select 'lead' k, count(*) n from lead where party_id=$1 having count(*)>1
         union all select 'client', count(*) from client where party_id=$1 and merged_into is null having count(*)>1
         union all select 'vendor', count(*) from vendor where party_id=$1 having count(*)>1`,
        [args.survivor_party]);

      await writeEvent(c, actor, "confirm-merge", "party", args.merged_party,
        { new: { merged_into: args.survivor_party, roles_moved: moved }, idempotency_key: args.idempotency_key });
      return { ok: true, roles_moved: moved,
               duplicate_roles_on_survivor: dup.rows.length ? dup.rows : undefined };
    }),
  },

  // [0069, loop #199] The case confirm-merge structurally cannot do: two VENDOR
  // rows riding ONE party. The 8/1-ruled Crowley and Woulston merges executed at
  // party level and left exactly this behind (V-GC-001+V-GC-013, V-MKT-001+
  // V-MSC-024), and the build sweep found a third pair the loop never named
  // (T-004+T-040). Backlog #119/#120's "executed" claims were true-but-incomplete.
  "merge-vendor-rows": {
    write: true, humanOnly: true,
    description: "HUMAN-confirmed merge of two vendor rows that ride the SAME party — a duplicate role, not a duplicate person. Survivorship is deterministic (rule 4c21d86b applied at role level): the survivor keeps every value it has, its NULLs fill from the loser, and a field where both rows disagree is REPORTED untouched for a human to settle — never coin-flipped. Activities, findings and next actions move to the survivor; the loser becomes a tombstone (merged_into) that v_ref_index still resolves with merged=true, and renders exclude. Different-party duplicates are confirm-merge's lane, and this verb refuses them. Nothing auto-merges, ever: a human picks the pair and the survivor. Survivor choice per rule 4c21d86b: more corroborated identity, then more linked records, then oldest.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      survivor_vendor: { type: "string", description: "V-/T- ref of the row that keeps the ref cited elsewhere" },
      merged_vendor: { type: "string", description: "V-/T- ref of the row that becomes the tombstone" } },
      required: ["idempotency_key","survivor_vendor","merged_vendor"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "merge-vendor-rows", args, async () => {
      const resolveVendor = async (ref) => {
        const s = await resolveSubject(c, ref);
        if (s.type !== "vendor") throw new ToolError({ error: "not_a_vendor", ref, resolved: s.type });
        return s.id;
      };
      const survId = await resolveVendor(args.survivor_vendor);
      const mergId = await resolveVendor(args.merged_vendor);
      if (survId === mergId)
        throw new ToolError({ error: "same_vendor_row", hint: "both refs resolve to one row; nothing to merge" });

      const FIELDS = ["category","category_slug","verticals","stage","owner_id","owner_label",
        "referral_active","territory","offers","seeking","rivalry_group","originated",
        "intro_notes","links_label","last_touch","relationship_level"];
      const rows = (await c.query(
        `select id, vendor_ref, party_id, merged_into, ${FIELDS.join(",")} from vendor where id = any($1)`,
        [[survId, mergId]])).rows;
      const surv = rows.find(r => r.id === survId), merg = rows.find(r => r.id === mergId);
      if (surv.merged_into || merg.merged_into)
        throw new ToolError({ error: "already_merged",
          which: [surv, merg].filter(r => r.merged_into).map(r => r.vendor_ref),
          hint: "a tombstone cannot merge again; resolve to the live survivor first" });
      if (surv.party_id !== merg.party_id)
        throw new ToolError({ error: "different_parties",
          hint: "these vendor rows sit on two different people — that is a PARTY duplicate. confirm-merge is the verb, and it moves the vendor rows with the person." });

      // Survivorship: fill the survivor's NULLs, report disagreements, change nothing else.
      const filled = {}, conflicts = [];
      for (const f of FIELDS) {
        const a = surv[f], b = merg[f];
        const empty = (v) => v === null || v === undefined || (Array.isArray(v) && v.length === 0);
        if (empty(a) && !empty(b)) filled[f] = b;
        else if (!empty(a) && !empty(b) && JSON.stringify(a) !== JSON.stringify(b))
          conflicts.push({ field: f, survivor: a, merged: b });
      }
      const fk = Object.keys(filled);
      if (fk.length) {
        const sets = fk.map((k, i) => `${k}=$${i + 2}`).join(", ");
        await c.query(`update vendor set ${sets}, updated_by=$1 where id=$${fk.length + 2}`,
          [actor.id, ...fk.map(k => filled[k]), survId]);
      }

      // Dependents move; event rows stay where they happened (history is immutable).
      const moved = {};
      const act = await c.query(
        "update activity set vendor_id=$1 where vendor_id=$2 returning id", [survId, mergId]);
      if (act.rows.length) moved.activities = act.rows.length;
      const rf = await c.query(
        "update record_flag set subject_id=$1 where subject_type='vendor' and subject_id=$2 returning id",
        [survId, mergId]);
      if (rf.rows.length) moved.findings = rf.rows.length;
      // next_action: a unique index guards one OPEN action per (subject, owner).
      // A colliding open action on the loser is dropped and reported, never lost silently.
      const droppedActions = [];
      const na = await c.query(
        "select id, owner_id, description, status from next_action where subject_type='vendor' and subject_id=$1", [mergId]);
      for (const row of na.rows) {
        const clash = row.status === "open" && (await c.query(
          `select 1 from next_action where subject_type='vendor' and subject_id=$1
            and owner_id=$2 and status='open'`, [survId, row.owner_id])).rows.length;
        if (clash) {
          await c.query("update next_action set status='dropped', updated_by=$1 where id=$2", [actor.id, row.id]);
          droppedActions.push(row.description);
        } else {
          await c.query("update next_action set subject_id=$1, updated_by=$2 where id=$3", [survId, actor.id, row.id]);
          moved.next_actions = (moved.next_actions || 0) + 1;
        }
      }

      await c.query("update vendor set merged_into=$1, updated_by=$2 where id=$3",
        [survId, actor.id, mergId]);
      await writeEvent(c, actor, "merge-vendor-rows", "vendor", mergId,
        { new: { merged_into: args.survivor_vendor, filled, conflicts, moved },
          idempotency_key: args.idempotency_key });
      return { ok: true, survivor: surv.vendor_ref, tombstone: merg.vendor_ref,
               fields_filled: fk.length ? filled : undefined,
               conflicts_left_for_human: conflicts.length ? conflicts : undefined,
               moved, dropped_duplicate_open_actions: droppedActions.length ? droppedActions : undefined };
    }),
  },

  "record-finding": {
    write: true,
    description: "Land ONE open-source research or enrichment finding as a record_flag row. This is the only path a verification result becomes part of the record — findings do not go into a markdown report (Joe, 2026-08-02: 'we dont write to markdown in the new system only the database'). IT NEVER EDITS AN IDENTITY FIELD. A finding is stored BESIDE the record with its source; a disagreement with name/phone/email/title/specialty is passed as proposes_correction, which is recorded as a proposal for the owning partner and applied by them, never by this verb. STORE NOTHING-FOUND TOO: pass found:false and the empty result becomes a real row, so a record nobody searched is distinguishable from one that was searched and came up dry — that difference is the whole meaning of a verified stamp. source is REQUIRED on every row; provenance is binding, and a finding without it is a rumour. Pass expires_on for anything volatile: title and company change with promotions and job moves, so an expired verification reads as unverified rather than as fact. Common kinds: verified (an identity pass, value lists what was checked), email, cell, office_phone, social, website, npi, license_status, title, entity_filing, address, discrepancy. A near-match on a similar name is contamination, not confirmation — record both candidates and pick neither. Also writes an event, so the finding shows up in catch-me-up without a second read surface. NOT ONLY PEOPLE SINCE 0066: subject_kind campaign / platform / pillar / format files a finding against a THING — a platform, a content pillar, a format, a campaign — which is how the marketing seat's measured conclusions finally get a home. Read them back through v_record_flag_subject, which resolves every branch to a name.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      subject: { type: "string", description: "C-127 / L-204 / V-CPA-006 / P-0301, an exact deal name, or — when subject_kind is campaign/platform/pillar/format — a campaign name or a marketing_subject slug ('twitter', 'reel')" },
      subject_kind: { type: "string", enum: ["auto","party","campaign","platform","pillar","format"], default: "auto",
        description: "'party' pins the flag to the person/org behind a ref instead of the client/lead/vendor record. THE FOUR MARKETING KINDS (0066) are how a finding about a THING rather than a PERSON gets recorded: 'X has returned no analytics for any of 42 placements' is a platform finding, 'reels outperform statics on reach' is a format finding. Before 0066 those had no subject at all and the marketing seat's core output went nowhere. A platform/pillar/format subject must already exist in marketing_subject — this verb registers nothing, because a typo'd slug minting a new pillar is how a taxonomy becomes noise." },
      kind: { type: "string", description: "what was looked for: verified, email, cell, social, npi, title, discrepancy..." },
      value: { type: "object", description: "the finding, structured. Omit when found:false." },
      found: { type: "boolean", default: true, description: "false records a searched-and-empty result" },
      epistemic_status: { type: "string",
        enum: ["proposed","observed","reproduced","accepted","disputed","superseded","inferred","source_backed","speculative"],
        description: "what CLASS of knowledge this row is, so a reader can tell a verified invariant from a provisional interpretation from a hypothesis (idea #57, from the repo-centric agent-stack study 2026-08-07). Defaults: source_backed when found with an external source; inferred when internal. Set disputed/superseded when filing against an earlier finding. Stored in value; query as value->>'epistemic_status'." },
      source: { type: "string", description: "REQUIRED. Where it came from: a URL, 'NPPES', 'Sunbiz', 'practice website'. For EXTERNAL findings this must be a re-verifiable LOCATOR (a URL, a registry name + identifier, a file+section, a thread + date) — wave 1 C5, decision a317439f: the re-verify queue is only as good as the pointer it re-checks. A bare label like 'research' is refused." },
      internal: { type: "boolean", description: "true = an internally observed finding (derived from the record layer itself, a session's own computation, or partner testimony) — exempt from the external-locator requirement, and stored flagged so readers know no outside source backs it." },
      observed_at: { type: "string", description: "when the source was read (ISO); defaults to now" },
      expires_on: { type: "string", description: "date after which this reads as unverified again (volatile fields)" },
      proposes_correction: { type: "object",
        description: "{field, current, proposed} — RECORDED ONLY. The owning partner applies it." } },
      required: ["idempotency_key","subject","kind","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-finding", args, async () => {
      // [wave 1 C5, decision a317439f — scoped per the Codex judge: external and
      // decision-bearing findings need a re-verifiable locator; internal
      // observations are exempt but flagged. A universal requirement would
      // manufacture placeholder citations, which is worse than none.]
      if (!args.internal) {
        const src = String(args.source || "");
        const locator = /https?:\/\//i.test(src)
          || /\b(nppes|sunbiz|npi|bbb|linkedin|zoominfo|rocketreach|facebook|instagram|chamber|arxiv|github)\b/i.test(src)
          || /[\/#§@]|\bp\.?\s?\d|\bevent\b|\bthread\b|\bv_[a-z_]+/i.test(src);
        if (!locator || src.trim().length < 12)
          throw new ToolError({ error: "source_not_a_locator",
            got: src.slice(0, 80),
            hint: "an external finding's source must be re-verifiable: a URL, a registry name + identifier, a file+section, or a thread+date. If this finding was derived internally (from the record itself or partner testimony), resubmit with internal:true and it will be stored flagged as such." });
      }
      const src = String(args.source || "").trim();
      if (!src) throw new ToolError({ error: "source_required",
        hint: "every finding carries its provenance; a finding without a source is a rumour" });

      const found = args.found !== false;
      if (found && (!args.value || typeof args.value !== "object" || !Object.keys(args.value).length))
        throw new ToolError({ error: "value_required",
          hint: "pass the finding as value{}, or pass found:false to record a searched-and-empty result" });

      let subjectType, subjectId;
      const MARKETING_KINDS = ["campaign", "platform", "pillar", "format"];
      if (args.subject_kind === "party") {
        subjectType = "party";
        subjectId = await resolvePartyByRef(c, args.subject);
      } else if (MARKETING_KINDS.includes(args.subject_kind)) {
        // [0066] The non-party branch. It resolves through the SAME
        // (subject_type, subject_id) pointer every other branch uses — a campaign
        // already has a uuid, and marketing_subject exists to give platforms,
        // pillars and formats one, rather than bolting a second pointer column
        // onto record_flag.
        await require0066(c);
        if (args.subject_kind === "campaign") {
          subjectType = "campaign";
          subjectId = (await resolveCampaign(c, args.subject)).id;
        } else {
          const slug = String(args.subject || "").trim().toLowerCase();
          const r = await c.query(
            "select id, retired_at from marketing_subject where subject_type=$1 and slug=$2",
            [args.subject_kind, slug]);
          if (!r.rows.length) {
            const known = await c.query(
              "select slug from marketing_subject where subject_type=$1 and retired_at is null order by slug",
              [args.subject_kind]);
            throw new ToolError({ error: "marketing_subject_not_found",
              subject_kind: args.subject_kind, slug,
              known_slugs: known.rows.map(x => x.slug),
              hint: known.rows.length
                ? "use one of the registered slugs, or register a new one deliberately — this verb never mints one"
                : `no ${args.subject_kind} is registered yet. 0066 deliberately seeds ZERO pillars ` +
                  "because none is evidenced anywhere in the record; naming the first one is a " +
                  "human modelling act, not a side effect of filing a finding." });
          }
          subjectType = args.subject_kind;
          subjectId = r.rows[0].id;
          if (r.rows[0].retired_at)
            throw new ToolError({ error: "marketing_subject_retired", slug,
              retired_at: r.rows[0].retired_at,
              hint: "findings stay readable against a retired subject, but new ones do not attach to it" });
        }
      } else {
        const s = await resolveSubject(c, args.subject);
        subjectType = s.type; subjectId = s.id;
      }

      // The correction is DATA, not an instruction. It rides inside value so it is
      // impossible to store one without its provenance, and no code path applies it.
      const value = {
        found,
        ...(found ? args.value : { searched_for: args.kind }),
        ...(args.proposes_correction
            ? { proposes_correction: { ...args.proposes_correction, applied: false,
                                       note: "proposal only — the owning partner applies identity changes" } }
            : {}),
        // [wave 1 C5] an internally observed finding is stored FLAGGED, so a
        // reader knows no outside source backs it — the exemption is visible,
        // never silent.
        ...(args.internal ? { internal: true } : {}),
        // Epistemic status (idea #57): explicit wins; otherwise internal
        // observations are inferences and externally-sourced rows are
        // source-backed. Never silently "known".
        epistemic_status: args.epistemic_status
          || (args.internal ? "inferred" : "source_backed"),
      };

      const r = await c.query(
        `insert into record_flag (subject_type, subject_id, kind, value, source, observed_at, expires_on, created_by)
         values ($1,$2,$3,$4,$5, coalesce($6::timestamptz, now()), $7::date, $8) returning id, observed_at`,
        [subjectType, subjectId, args.kind, JSON.stringify(value), src,
         args.observed_at || null, args.expires_on || null, actor.id]);

      await writeEvent(c, actor, "record-finding", subjectType, subjectId, {
        occurred_at: args.observed_at || null,
        field: args.kind,
        new: { found, source: src, expires_on: args.expires_on || null,
               proposes_correction: args.proposes_correction ? args.proposes_correction.field : null },
        agent_rationale: found ? null : "searched, nothing found",
        idempotency_key: args.idempotency_key });

      return { ok: true, flag_id: r.rows[0].id, subject_type: subjectType, subject_id: subjectId,
               kind: args.kind, found, observed_at: r.rows[0].observed_at,
               correction_proposed: !!args.proposes_correction };
    }),
  },

  "teach": {
    write: true, humanOnly: true,
    description: "Write a rule from the human's own words (status: proposed — it activates only via activate-rule, also human-gated). Capture the verbatim quote. Personal-scope rules (voice, format) set personal_to. WHEN TO CALL IT — the test is 'would the system have to ask this again?', NOT whether the partner phrased it as 'always X' or 'never Y'. Standing lessons arrive as ordinary sentences: a modeling ruling ('musicologie is one national account'), a correction to a fact in the record, a choice between options you offered with the reasoning attached, a rejection of a draft. Capture on the spot, never at 'session close' — the same event-not-session-close rule protocol 27b already settles. Pass supersedes when this rule replaces an earlier one; the old rule is NOT retired by that alone (use retire-rule), but the link is recorded so nobody re-litigates a settled point from a stale row.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, statement: { type: "string" },
      human_quote: { type: "string" }, scope: { type: "object" },
      personal: { type: "boolean", description: "true = applies to this partner only" },
      supersedes: { type: "string", description: "rule_id this one replaces; recorded as a link, does not retire it" } },
      required: ["idempotency_key","statement","human_quote"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "teach", args, async () => {
      // A supersedes pointer at a rule that does not exist is a silent lie in the
      // audit trail, so it is checked rather than trusted.
      if (args.supersedes) {
        const prior = await c.query("select id, status from rule where id=$1", [args.supersedes]);
        if (!prior.rows.length) throw new ToolError({ error: "supersedes_not_found",
          rule_id: args.supersedes, hint: "pass the id of a real rule, or omit supersedes" });
      }
      const r = await c.query(
        `insert into rule (statement, human_quote, taught_by, scope, personal_to, supersedes)
         values ($1,$2,$3,$4,$5,$6) returning id, personal_to`,
        [args.statement, args.human_quote, actor.id, JSON.stringify(args.scope || {}),
         args.personal ? actor.id : null, args.supersedes || null]);
      await writeEvent(c, actor, "teach", "rule", r.rows[0].id,
        { new: { statement: args.statement, supersedes: args.supersedes || null },
          human_quote: args.human_quote, idempotency_key: args.idempotency_key });
      // SCOPE IS ECHOED BACK, added 2026-08-03, because it defaulted silently
      // once and nothing in the response could show it. A rule taught with
      // personal:true landed SHARED, and the only thing that caught it was a
      // row-count comparison between two exported files minutes after it was
      // already ACTIVE and binding both partners. `personal_to` is derived from
      // args.personal AND a resolved actor, so a caller genuinely cannot know
      // which scope it got from an envelope that says only {ok, rule_id,
      // status}. The failure direction is always toward binding MORE people
      // than intended, which is the direction that matters least to the caller
      // and most to the other partner. So the verb now states what it did.
      const scopeApplied = r.rows[0].personal_to ? `personal:${actor.slug}` : "shared";
      const scopeMismatch = args.personal === true && !r.rows[0].personal_to;
      return { ok: true, rule_id: r.rows[0].id, status: "proposed",
               scope_applied: scopeApplied,
               personal_requested: args.personal === true,
               supersedes: args.supersedes || null,
               ...(scopeMismatch ? { warning:
                 "personal:true was requested but this rule was stored SHARED — activating it " +
                 "will bind BOTH partners, including any wording specific to one of them or to " +
                 "one machine. Retire it and re-teach before activating if that is wrong." } : {}) };
    }),
  },

  "activate-rule": {
    write: true, humanOnly: true,
    description: "Set a rule's status proposed -> active. The context compiler (compiled-rules exports) reads ACTIVE rules only; activation is a human decision by design.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, rule_id: { type: "string" } },
      required: ["idempotency_key","rule_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "activate-rule", args, async () => {
      const r = await c.query(
        `update rule set status='active', activated_by=$1, activated_at=now()
         where id=$2 and status='proposed' returning id`, [actor.id, args.rule_id]);
      if (!r.rows.length) throw new ToolError({ error: "not_proposed_or_missing" });
      await writeEvent(c, actor, "activate-rule", "rule", args.rule_id,
        { new: { status: "active" }, idempotency_key: args.idempotency_key });
      return { ok: true };
    }),
  },

  "retire-rule": {
    write: true, humanOnly: true,
    description: "Withdraw a rule — proposed OR active — by setting status='retired'. THE PRESSURE VALVE THE RULE STORE WAS MISSING: until 2026-08-02 a rule could only go proposed -> active, so a rule taught in a wrong scope, a duplicate, or a draft the partner never wanted could never be taken back. 56 proposed rules had piled up by then, including two that stated Joe's own start date differently and no way to kill the wrong one. Retiring is NOT deleting: the row stays, the statement stays readable, and the compiled-rules exports simply stop carrying it (they read active only). A reason is REQUIRED — an unexplained retirement is indistinguishable from a mistake six months later, and the reason is the only thing that stops the same rule being re-taught. Pass superseded_by when a replacement already exists, so the pair reads as one decision rather than two unrelated events. Retiring an ACTIVE rule changes what binds every session, so it is human-gated like teach and activate-rule.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, rule_id: { type: "string" },
      reason: { type: "string", description: "REQUIRED. Why it is being withdrawn — wrong scope, duplicate, superseded, never wanted." },
      superseded_by: { type: "string", description: "rule_id of the replacement, when there is one" } },
      required: ["idempotency_key","rule_id","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "retire-rule", args, async () => {
      const reason = String(args.reason || "").trim();
      if (!reason) throw new ToolError({ error: "reason_required",
        hint: "an unexplained retirement reads as a mistake later; say why in one line" });

      const cur = await c.query("select status, statement, personal_to from rule where id=$1", [args.rule_id]);
      if (!cur.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
      if (cur.rows[0].status === "retired") throw new ToolError({ error: "already_retired",
        rule_id: args.rule_id, hint: "nothing was written; the rule is already withdrawn" });

      if (args.superseded_by) {
        const rep = await c.query("select id from rule where id=$1", [args.superseded_by]);
        if (!rep.rows.length) throw new ToolError({ error: "superseded_by_not_found",
          rule_id: args.superseded_by, hint: "pass the id of a real replacement rule, or omit it" });
        if (args.superseded_by === args.rule_id) throw new ToolError({ error: "self_supersede",
          hint: "a rule cannot replace itself" });
      }

      const was = cur.rows[0].status;
      await c.query("update rule set status='retired' where id=$1", [args.rule_id]);
      await writeEvent(c, actor, "retire-rule", "rule", args.rule_id, {
        field: "status", old: { status: was }, new: { status: "retired" },
        agent_rationale: reason,
        idempotency_key: args.idempotency_key });
      return { ok: true, rule_id: args.rule_id, was, now: "retired", reason,
               superseded_by: args.superseded_by || null,
               note: was === "active"
                 ? "this rule was BINDING — re-export compiled-rules so sessions stop loading it"
                 : "it bound nobody; no re-export needed" };
    }),
  },

  // ---------- amend-rule (2026-08-02) ----------
  // The store shipped one-way: teach -> activate -> retire. There was no way to
  // fix the WORDS of a rule that was otherwise right, so every wording fix meant
  // retire + re-teach: a new id (breaking every citation), a lost created_at and
  // activation event, and a REQUIRED fresh human_quote — forcing the partner to
  // re-say something he already said, to fix prose he never wrote.
  //
  // 53 of the 54 proposed rules carry no quote at all; they were imported from
  // ai-operating-notes.md by a pipeline that correctly refused to fabricate one.
  // Their statement is our articulation, not his testimony. `update-decision`
  // already established that a durable record can be corrected rather than
  // re-litigated; rules simply never got the same affordance.
  "amend-rule": {
    write: true, humanOnly: true,
    description: "Correct the WORDS of an existing rule in place, keeping its id, created_at, taught_by, quote and activation history. THE LINE: amend = same rule, better words; teach + retire = a different rule. Use it to fix compiled prose, tighten an over-broad statement, drop a clause that has gone stale, or re-scope a rule shipped in the wrong scope — anywhere the RULE is right and the SENTENCE is not. Do NOT use it to change what a rule means: a genuinely different ruling is a new rule (teach, with the partner's own words) plus retire-rule on the old one, so the change reads as a decision instead of an edit. human_quote is IMMUTABLE once set — it is the partner's testimony, not prose, and this verb refuses to overwrite it. It WILL fill a NULL quote, which is the backfill path for the imported rules that never had one. Requires base_version from a fresh read; a conflict means someone else wrote, so ask the human and never retry blind. Amending an ACTIVE rule changes what binds every session, so it is human-gated like teach, activate-rule and retire-rule, and the old text is written onto the event so the change is auditable and reversible.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      rule_id: { type: "string" },
      base_version: { type: "integer", description: "the rule's version, from a fresh read" },
      statement: { type: "string", description: "the corrected rule text. Omit to leave it as-is." },
      human_quote: { type: "string", description: "ONLY to fill a quote that is currently absent — the partner's literal words. Refused if the rule already carries one. Never paraphrase into this field." },
      scope: { type: "object", description: "replacement scope object, e.g. {\"section\":\"...\"}. Omit to leave it as-is." },
      reason: { type: "string", description: "REQUIRED. Why the wording is being corrected — an unexplained edit to a binding rule is indistinguishable from drift." } },
      required: ["idempotency_key","rule_id","base_version","reason"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "amend-rule", args, async () => {
      const reason = String(args.reason || "").trim();
      if (!reason) throw new ToolError({ error: "reason_required",
        hint: "say in one line why the wording is wrong; a silent edit to a binding rule reads as drift later" });

      const cur = await c.query(
        "select status, statement, human_quote, scope, version from rule where id=$1", [args.rule_id]);
      if (!cur.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
      const row = cur.rows[0];

      // A retired rule is history. Editing a tombstone rewrites the past instead
      // of correcting the present.
      if (row.status === "retired") throw new ToolError({ error: "rule_retired",
        rule_id: args.rule_id, current_status: row.status,
        hint: "a withdrawn rule stays as written; teach a new one rather than editing the tombstone" });

      await versionGuard(c, "rule", args.rule_id, args.base_version);

      const hasQuote = !!String(row.human_quote || "").trim();
      const quoteIn  = args.human_quote === undefined ? undefined : String(args.human_quote).trim();
      if (quoteIn !== undefined && hasQuote && quoteIn !== String(row.human_quote).trim())
        throw new ToolError({ error: "human_quote_immutable",
          current_quote: row.human_quote,
          hint: "the partner's words are testimony, not prose. Amend the statement instead; to record something DIFFERENT he said, teach a new rule and retire this one." });

      const nextStatement = args.statement === undefined ? row.statement : String(args.statement).trim();
      if (!nextStatement) throw new ToolError({ error: "empty_statement",
        hint: "a rule with no words binds nothing — pass the corrected text, or omit the field to leave it alone" });

      const nextQuote = (!hasQuote && quoteIn) ? quoteIn : row.human_quote;
      const nextScope = args.scope === undefined ? row.scope : args.scope;

      const changed = [];
      if (nextStatement !== row.statement) changed.push("statement");
      if (nextQuote !== row.human_quote) changed.push("human_quote");
      if (JSON.stringify(nextScope) !== JSON.stringify(row.scope)) changed.push("scope");
      if (!changed.length) throw new ToolError({ error: "no_change",
        hint: "nothing was written; the rule already reads exactly this way" });

      await c.query("update rule set statement=$1, human_quote=$2, scope=$3 where id=$4",
        [nextStatement, nextQuote, JSON.stringify(nextScope), args.rule_id]);

      await writeEvent(c, actor, "amend-rule", "rule", args.rule_id, {
        field: changed.join(","),
        old: { statement: row.statement, human_quote: row.human_quote, scope: row.scope },
        new: { statement: nextStatement, human_quote: nextQuote, scope: nextScope },
        human_quote: nextQuote || null,
        agent_rationale: reason,
        idempotency_key: args.idempotency_key });

      const after = await c.query("select version from rule where id=$1", [args.rule_id]);
      return { ok: true, rule_id: args.rule_id, status: row.status,
               changed, version: after.rows[0].version, reason,
               note: row.status === "active"
                 ? "this rule is BINDING — re-export compiled-rules so sessions load the corrected words"
                 : "it binds nobody yet; activate-rule is still the gate" };
    }),
  },

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
    write: true,
    description: "Record a SETTLED decision and its rationale — the thing that stops it being relitigated next session. Writes a decision event (subject_type='decision', verb='log-decision') that v_decision_entry reads and decision-history.md renders; never hand-edit that file. NOT the same as add-loop marker:'decision', which is an OPEN question awaiting a ruling, and not the same as teach, which stores a standing rule that binds future sessions. Use this when a fork has been closed: what was decided, why, what lost. human_quote is Joe's or Dell's literal words when he said them — omit it and the entry is flagged quote_absent rather than paraphrase being passed off as a quote.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      title: { type: "string", description: "the decision itself, in one line, stated as settled" },
      rationale: { type: "string", description: "why — including alternatives considered and why they lost, and any condition that would reopen it" },
      human_quote: { type: "string", description: "the partner's literal words, when he said them. Never paraphrase into this field." },
      session_key: { type: "string", description: "groups entries per session (rule 29). Defaults to <date>-<actor>." },
      provenance: { type: "string", description: "where this came from — a session, a call, a document" },
      occurred_at: { type: "string", description: "when it was decided; defaults now" } },
      required: ["idempotency_key","title","rationale"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "log-decision", args, async () => {
      const decisionId = (await c.query("select gen_random_uuid() as id")).rows[0].id;
      const r = await c.query(
        `insert into event (occurred_at, actor_id, verb, subject_type, subject_id,
           new_value, cause, human_quote, agent_rationale, idempotency_key, via, client_id)
         values (coalesce($1::timestamptz, now()), $2, 'log-decision', 'decision', $3,
                 $4, 'human_stated', $5, $6, $7, $8, $9)
         -- to_char, not ::date: node-postgres hands a ::date back as a JS Date, and
         -- interpolating that into session_key produced
         -- "Sun Aug 02 2026 00:00:00 GMT+0000 (Coordinated Universal Time)-joe"
         -- on the first live decision. A text date interpolates as a text date.
         returning id, to_char(coalesce($1::timestamptz, now()), 'YYYY-MM-DD') as entry_date`,
        [args.occurred_at || null, actor.id, decisionId,
         JSON.stringify({ title: args.title,
                          quote_absent: !args.human_quote,
                          provenance: args.provenance || null }),
         args.human_quote || null, args.rationale, args.idempotency_key,
         actor.via || null, actor.client_id || null]);

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

      return { ok: true, decision_id: decisionId, event_id: ev.id,
               session_key: sessionKey, quote_absent: !args.human_quote,
               renders_into: "00_Context/decision-history.md" };
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
    write: true,
    description: "Correct a decision entry already recorded by log-decision — a wrong or missing title, rationale, human_quote or provenance. Use for a DEFECTIVE record (a quote that was lost, a rationale that stated something untrue), never to rewrite what was actually decided: a decision that CHANGED is a new log-decision, because the old one really was the call at the time. Pass only the fields you are correcting. Re-derives quote_absent from whether a quote is present afterwards, and appends an amend-decision event recording the change.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      decision_id: { type: "string", description: "the decision_id returned by log-decision" },
      title: { type: "string" }, rationale: { type: "string" },
      human_quote: { type: "string", description: "the partner's literal words. Never paraphrase into this field." },
      provenance: { type: "string" },
      reason: { type: "string", description: "why the entry needed correcting — recorded on the amendment" } },
      required: ["idempotency_key","decision_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-decision", args, async () => {
      const cur = (await c.query(
        `select id, new_value, human_quote, agent_rationale from event
          where subject_id = $1 and verb = 'log-decision' limit 1`, [args.decision_id])).rows[0];
      if (!cur) throw new ToolError({ error: "decision_not_found", decision_id: args.decision_id,
        hint: "pass the decision_id log-decision returned, not the event_id" });

      const nv = cur.new_value || {};
      const quote = args.human_quote !== undefined ? args.human_quote : cur.human_quote;
      const next = { ...nv,
        title: args.title !== undefined ? args.title : nv.title,
        provenance: args.provenance !== undefined ? args.provenance : nv.provenance,
        quote_absent: !quote };

      await c.query(
        `update event set new_value = $1, human_quote = $2, agent_rationale = $3 where id = $4`,
        [JSON.stringify(next), quote || null,
         args.rationale !== undefined ? args.rationale : cur.agent_rationale, cur.id]);

      const changed = ["title","rationale","human_quote","provenance"].filter(f => args[f] !== undefined);
      await writeEvent(c, actor, "amend-decision", "decision", args.decision_id,
        { old: { quote_absent: nv.quote_absent }, new: { fields: changed, quote_absent: !quote },
          agent_rationale: args.reason || null, idempotency_key: args.idempotency_key });

      return { ok: true, decision_id: args.decision_id, amended: changed, quote_absent: !quote };
    }),
  },

  // ---------- the loop accumulators (one-writer Phase A, ORDER 31) ----------
  // open-loops.md, open-loops-backlog.md, action-required.md and team-loops.md
  // are generated renders of loop_item after this lands. Sessions stop editing
  // those four files and use these three verbs instead — which is the whole
  // point of Joe's ruling: "if i do something in my session, i want dell to be
  // able to instantly recall it in his session."

  "add-loop": {
    write: true,
    description: "Open a new loop — a Joe/Dell task (kind open_loop), a partner handoff (team_loop), a cross-brain interrupt (action_required), or a parked idea (kind idea, which renders into 00_Context/idea-bank.md and is personal, never shared). Do NOT hand-edit open-loops.md, open-loops-backlog.md, action-required.md or team-loops.md; they are rendered from this. Markers carry meaning the heartbeat obeys: `bell` = actionable THIS WEEK (hard cap 3 PER DOMAIN — more than 3 means re-tier, not stack; read v_loop_bell_cap for breaches. The old cap was 5 across the whole hot list, written before domains existed: with six lanes that was under one bell each, so everything drifted to 'none' until the hot list held 21 items against a cap of 5), `dated` + due_on = silent until its day, `decision` = a ❓ the Monday brief surfaces, `none` = backlog. An open_loop with bell, or a dated one already due, lands hot; everything else lands in the backlog, which is the file's own rule. The action_required bar is deliberately high: only a new shared mechanism, a build the other side must replicate, or a protocol change — if everything is urgent, nothing is.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      kind: { type: "string", enum: ["open_loop", "team_loop", "action_required", "idea"] },
      domain: { type: "string", enum: ["deals","prospecting","networking","marketing","business","system"],
        description: "deals | prospecting | networking | marketing | business | system. Classify by WHAT THE WORK IS, not who appears in it: a vendor introducing a PROSPECT normally means real intent and is DEALS (prospecting only while no deal has formed); a vendor introducing a VENDOR is networking; connecting a prospect to a vendor is networking; connecting a client to a vendor on a LIVE deal is deals. Omit only when genuinely unclear — an unclassified loop renders in its own unsorted section, which is honest, but a loop nobody can find is a loop nobody does." },
      title: { type: "string", description: "team_loop 'Ask' / action_required 'Action needed'. Not used by open_loop, whose text is `body`." },
      body: { type: "string", description: "open_loop 'Item' / team_loop 'Notes / links'" },
      owner: { type: "string", description: "the label the file uses: 'Joe', 'Joe/Claude', 'Dell', 'Joe→Dell'" },
      unblocks: { type: "string", description: "what it unblocks / why it matters" },
      source_note: { type: "string", description: "source / detail / links" },
      marker: { type: "string", enum: ["bell", "dated", "decision", "none"] },
      due_on: { type: "string", description: "YYYY-MM-DD; required when marker is 'dated'" },
      drift_critical: { type: "boolean", description: "the ⚡ — leaving it undone causes system drift; BOTH brains' heartbeats surface it daily" },
      number: { type: "string", description: "override the auto-assigned ref. Only pass this to reproduce a number that already exists somewhere; the files already contain collisions." },
      since: { type: "string", description: "YYYY-MM-DD; defaults to today" } },
      required: ["idempotency_key", "kind", "owner"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "add-loop", args, async () => {
      if (!args.title && !args.body)
        throw new ToolError({ error: "empty_loop",
          hint: "a loop needs text: `body` for an open_loop, `title` for a team_loop or action_required" });
      if (args.marker === "dated" && !args.due_on)
        throw new ToolError({ error: "dated_marker_needs_date",
          hint: "a 🗓 row is silent until its day — without a date it would be silent forever" });

      const marker = args.marker || (args.due_on ? "dated" : "none");
      const literal = marker === "bell" ? "🔔"
        : marker === "decision" ? "❓"
        : marker === "dated" ? `🗓${args.due_on}` : null;

      // Placement follows the files' own rule. It is STORED, not derived, so a
      // later promotion is a recorded act (see v_loop_promotion_due).
      const nowDue = marker === "dated" && args.due_on <= new Date().toISOString().slice(0, 10);
      // 'idea' has no "open" block — its live section is 'parked' (44 rows) and
      // 'retired' is its closed state. Asking for "open" would throw no_block.
      const wantKey = args.kind === "idea" ? "parked"
        : args.kind !== "open_loop" ? "open"
        : (marker === "bell" || nowDue) ? "hot" : "backlog";
      const b = await c.query(
        "select id, rel_path, col_order from loop_block where kind=$1 and block_key=$2",
        [args.kind, wantKey]);
      if (!b.rows.length)
        throw new ToolError({ error: "no_block", kind: args.kind, section: wantKey,
          hint: "the loop importer has not run for this kind — nothing to render into" });
      const block = b.rows[0];

      const num = args.number || await nextLoopNumber(c, args.kind);
      const seq = await nextRenderSeq(c, block.id);
      const tier = (args.kind === "open_loop" || args.kind === "idea") ? "personal" : "shared";
      const personal = tier === "personal" ? actor.id : null;

      const r = await c.query(
        `insert into loop_item (kind, number, block_id, render_seq, title, body, owner,
           since_text, unblocks, source_note, marker, marker_literal, due_on,
           drift_critical, status, tier, personal_to, created_by, updated_by, domain)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'open',$15,$16,$17,$17,$18)
         returning id`,
        [args.kind, num, block.id, seq, args.title || null, args.body || null, args.owner,
         args.since || new Date().toISOString().slice(0, 10), args.unblocks || null,
         args.source_note || null, marker, literal, args.due_on || null,
         args.drift_critical === true, tier, personal, actor.id, args.domain || null]);

      await writeEvent(c, actor, "add-loop", "loop", r.rows[0].id,
        { new: { number: num, kind: args.kind, section: wantKey, marker,
                 due_on: args.due_on || null, owner: args.owner, domain: args.domain || null },
          idempotency_key: args.idempotency_key });
      return { ok: true, loop_id: r.rows[0].id, number: num, kind: args.kind,
               section: wantKey, renders_into: block.rel_path };
    }),
  },

  "update-loop": {
    write: true,
    description: "Change an open loop — its text, its owner, its marker, or which section it sits in. This is also how a due backlog row gets PROMOTED to the hot list: pass section 'hot'. Promotion is a recorded act by an actor, never something a view does to Joe's file behind his back — read v_loop_promotion_due for what has come due. Pass only the fields you are changing; anything omitted is left alone. Closing is a different act: use close-loop, which requires an outcome.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      loop_id: { type: "string" },
      number: { type: "string", description: "alternative to loop_id; refuses when the number is ambiguous, and several are" },
      kind: { type: "string", enum: ["open_loop", "team_loop", "action_required", "idea"], description: "narrows an ambiguous number" },
      base_version: { type: "integer" },
      title: { type: "string" }, body: { type: "string" }, owner: { type: "string" },
      unblocks: { type: "string" }, source_note: { type: "string" },
      domain: { type: "string", enum: ["deals","prospecting","networking","marketing","business","system"],
        description: "reclassify the loop. Same rule as add-loop: classify by what the WORK is, not who appears in it." },
      marker: { type: "string", enum: ["bell", "dated", "decision", "none"] },
      due_on: { type: "string", description: "YYYY-MM-DD" },
      drift_critical: { type: "boolean" },
      section: { type: "string", enum: ["hot", "backlog", "open"], description: "move the row to this section of its file" } },
      required: ["idempotency_key"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-loop", args, async () => {
      const cur = await resolveLoop(c, args);
      await versionGuard(c, "loop_item", cur.id, args.base_version);
      if (cur.status !== "open")
        throw new ToolError({ error: "loop_not_open", loop_id: cur.id, status: cur.status,
          hint: "a closed loop is history; open a new one rather than editing the record of what happened" });

      const sets = [], vals = [];
      const set = (col, v) => { vals.push(v); sets.push(`${col}=$${vals.length}`); };
      for (const f of ["title", "body", "owner", "unblocks", "source_note", "domain"])
        if (args[f] !== undefined) set(f, args[f]);
      if (args.drift_critical !== undefined) set("drift_critical", args.drift_critical === true);

      if (args.marker !== undefined || args.due_on !== undefined) {
        const marker = args.marker !== undefined ? args.marker : cur.marker;
        const due = args.due_on !== undefined ? args.due_on : cur.due_on;
        if (marker === "dated" && !due)
          throw new ToolError({ error: "dated_marker_needs_date",
            hint: "a 🗓 row without a date is silent forever" });
        set("marker", marker);
        set("due_on", marker === "dated" ? due : null);
        set("marker_literal", marker === "bell" ? "🔔"
          : marker === "decision" ? "❓"
          : marker === "dated" ? `🗓${due}` : null);
      }

      let moved = null;
      if (args.section && args.section !== cur.section) {
        const b = await c.query(
          "select id, rel_path from loop_block where kind=$1 and block_key=$2",
          [cur.kind, args.section]);
        if (!b.rows.length)
          throw new ToolError({ error: "no_such_section", kind: cur.kind, section: args.section,
            hint: `open_loop has hot and backlog; team_loop and action_required have open` });
        set("block_id", b.rows[0].id);
        set("render_seq", await nextRenderSeq(c, b.rows[0].id));
        moved = { from: cur.section, to: args.section };
      }

      if (!sets.length)
        throw new ToolError({ error: "nothing_to_update",
          hint: "pass at least one field; base_version alone changes nothing" });

      vals.push(actor.id); sets.push(`updated_by=$${vals.length}`);
      vals.push(cur.id);
      await c.query(`update loop_item set ${sets.join(", ")} where id=$${vals.length}`, vals);
      await writeEvent(c, actor, "update-loop", "loop", cur.id,
        { new: { changed: sets.map(s => s.split("=")[0]), moved },
          idempotency_key: args.idempotency_key });
      return { ok: true, loop_id: cur.id, number: cur.number, moved };
    }),
  },

  "close-loop": {
    write: true,
    description: "Close a loop — done, or deliberately dropped. AN OUTCOME IS REQUIRED and the verb refuses without one: team-loops states the reason in its own words, 'outcomes are how the asker finds out without asking twice.' Say what actually came of it, not that it is closed. A team_loop or action_required row moves to its file's Done table carrying the outcome; an open_loop leaves the hot/backlog render, the same thing closing a row has always done. Use resolution 'dropped' when it is being abandoned rather than finished — recording an abandonment as done inflates every completion measure built on this.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      loop_id: { type: "string" },
      number: { type: "string", description: "alternative to loop_id; refuses when ambiguous" },
      kind: { type: "string", enum: ["open_loop", "team_loop", "action_required", "idea"] },
      base_version: { type: "integer" },
      outcome: { type: "string", description: "REQUIRED: what came of it, in your words" },
      resolution: { type: "string", enum: ["done", "dropped"] } },
      required: ["idempotency_key", "outcome"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "close-loop", args, async () => {
      // The refusal is first and unconditional. A whitespace-only outcome is no
      // outcome: the record-level CHECK would accept ' ' and the asker would still
      // never find out, which is the failure this rule exists to prevent.
      const outcome = (args.outcome || "").trim();
      if (!outcome)
        throw new ToolError({ error: "outcome_required",
          hint: "close-loop will not close a loop silently — say what came of it. " +
                "If nothing came of it and you are abandoning it, say that and pass resolution 'dropped'." });

      const cur = await resolveLoop(c, args);
      await versionGuard(c, "loop_item", cur.id, args.base_version);
      if (cur.status !== "open")
        throw new ToolError({ error: "loop_not_open", loop_id: cur.id, status: cur.status,
          closed_outcome: cur.close_outcome,
          hint: "already closed — this is what came of it" });

      const resolution = args.resolution || "done";

      // A file with a Done table keeps its closed rows visible in the render; that
      // is the file's own convention, not a new one. open_loop has no Done table
      // in either of its two files, so a closed one simply leaves the list.
      const done = await c.query(
        "select id, rel_path from loop_block where kind=$1 and block_key='done'", [cur.kind]);
      const sets = ["status=$1", "close_outcome=$2", "closed_by=$3", "closed_at=now()",
                    "outcome=$2", "closed_text=to_char(now(),'YYYY-MM-DD')", "updated_by=$3"];
      const vals = [resolution, outcome, actor.id];
      let movedTo = null;
      if (done.rows.length) {
        vals.push(done.rows[0].id); sets.push(`block_id=$${vals.length}`);
        vals.push(await nextRenderSeq(c, done.rows[0].id)); sets.push(`render_seq=$${vals.length}`);
        movedTo = done.rows[0].rel_path;
      }
      vals.push(cur.id);
      await c.query(`update loop_item set ${sets.join(", ")} where id=$${vals.length}`, vals);

      await writeEvent(c, actor, "close-loop", "loop", cur.id,
        { field: "status", old: { status: "open" },
          new: { status: resolution, outcome }, human_quote: outcome,
          idempotency_key: args.idempotency_key });
      return { ok: true, loop_id: cur.id, number: cur.number, status: resolution,
               moved_to_done_table_in: movedTo };
    }),
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // MARKETING (0066) — the four verbs that give the lane an intent and an answer
  //
  // WHAT WAS BROKEN, measured on 2026-08-02 and not inferred. `campaign` held 0
  // rows. `content_piece` held 89 and every single campaign_id was null. 259
  // placement_metric rows existed and could not answer whether anything worked,
  // because nothing in the database ever said what any of it was FOR. The only
  // writer of any of these tables was pipelines/pull_placement_metrics.py, a
  // scheduled ingest that creates pieces and placements from Blotato and sets
  // campaign_id to nothing. The marketing COO seat could SPECIFY a campaign in
  // prose and could not RECORD one.
  //
  // WHY FOUR VERBS AND NOT THREE — the close/score question, answered.
  // open-campaign and score-campaign are separate on this system's own
  // precedent: activate-rule and retire-rule are separate, and update-loop and
  // close-loop are separate, because a state transition that carries a JUDGMENT
  // needs arguments the opening act must never accept. Folding them together
  // would mean a verb whose required fields depend on a mode flag, and a mode
  // flag is how a campaign gets closed by accident. More concretely: scoring
  // requires a verdict, evidence, and a measurement-coverage check that
  // open-campaign has no business running, and it must REFUSE a "worked" verdict
  // formed over unmeasured placements — a refusal that only makes sense at the
  // closing end. The cost is one more verb; the benefit is that "we decided this
  // worked" can never be a side effect of editing a start date.
  //
  // WHY NO VERB CREATES A content_piece. Checked before deciding, which is the
  // only reason the answer is trustworthy: all 89 existing pieces were born in
  // pull_placement_metrics.py, keyed on placement.external_id, at publish time.
  // A second birth path would mint duplicates the moment the ingest next runs,
  // because the ingest matches on external_id and a hand-made piece has none.
  // So pieces arrive by publishing, and attach-to-campaign BINDS them. The real
  // consequence, stated rather than hidden: content that is PLANNED but not yet
  // published has no record-layer home at all, and that is a genuine gap for
  // Joe to rule on, not something to paper over by minting orphan rows here.
  //
  // NOTHING HERE IS OUTBOUND AND NOTHING HERE SPENDS. These four verbs write
  // records about content that already exists. No verb publishes, schedules,
  // boosts, funds or touches a platform credential — the one human gate is
  // unchanged.

  "open-campaign": {
    write: true,
    description: "Open a campaign: the object that says what a run of content is FOR, so its results can later be judged instead of admired. THIS IS THE MISSING MIDDLE OF THE WHOLE MARKETING LANE — as of 2026-08-02 the campaign table held 0 rows while 89 content pieces and 259 metrics existed, so nothing published in the system's entire recorded history had a stated objective. Requires the objective (goal), the WINDOW (starts_on, optionally ends_on), the CHANNELS, and a success_criterion written so it can be CHECKED: 'X drives three practice-owner replies by Sept 30' is a criterion; 'grow awareness on X' is a restated goal and this verb refuses it. The criterion is required at OPEN because a criterion invented after the numbers arrive is not a criterion, and score-campaign quotes this one back before it accepts any verdict. ONE CAMPAIGN PER NAME, enforced by a unique index — reopening the same name is refused with the existing campaign's id, because the way this system produced 415 organisation rows for 306 real organisations was writers that never looked first. Backdating over content that already published is legitimate and normal (all 89 existing pieces are historical), so a start far in the past asks for confirm rather than refusing. NOT a publishing verb: it schedules nothing, spends nothing, and posts nothing. NOT the place for a piece of content — attach-to-campaign binds those, and it can only bind pieces that already published.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      name: { type: "string", description: "short, human, no ids. Unique — one campaign per name." },
      goal: { type: "string", description: "the objective in one sentence: what this run of content is FOR" },
      success_criterion: { type: "string",
        description: "REQUIRED. What would have to be observably TRUE for this to have worked, stated so it can be checked against the record. Name the observable and, where you can, the number and the date." },
      starts_on: { type: "string", description: "YYYY-MM-DD. Required — a campaign is a window." },
      ends_on: { type: "string", description: "YYYY-MM-DD. Omit for an open-ended run; scoring works either way." },
      channels: { type: "array", items: { type: "string" },
        description: "platform slugs this runs on: facebook, instagram, linkedin, twitter. Validated against the registered platforms; never empty." },
      note: { type: "string", description: "anything a later reader needs in order to judge the verdict" },
      human_quote: { type: "string", description: "the partner's literal words when he set this campaign" },
      confirm: { type: "boolean", description: "acknowledge an implausible window (deep backdate, or over a year long)" } },
      required: ["idempotency_key","name","goal","success_criterion","starts_on","channels"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "open-campaign", args, async () => {
      await require0066(c);

      const name = String(args.name || "").trim();
      const goal = String(args.goal || "").trim();
      const crit = String(args.success_criterion || "").trim();
      if (!name) throw new ToolError({ error: "name_required" });
      if (!goal) throw new ToolError({ error: "goal_required",
        hint: "one sentence: what is this run of content FOR?" });
      if (!crit) throw new ToolError({ error: "success_criterion_required",
        hint: "what would have to be observably true for this to have worked?" });
      // A criterion that merely restates the goal is the shape that lets a
      // campaign be declared a success on vibes. The check is deliberately crude
      // — it catches the copy-paste, not the merely vague — because a verb
      // cannot judge prose and pretending otherwise would be worse.
      if (crit.toLowerCase() === goal.toLowerCase())
        throw new ToolError({ error: "criterion_restates_goal",
          hint: "the success criterion must be CHECKABLE, and different from the objective: " +
                "name the observable, and where you can the number and the date" });

      const existing = await c.query(
        "select id, status, starts_on, ends_on from campaign where lower(btrim(name))=lower($1)",
        [name]);
      if (existing.rows.length)
        throw new ToolError({ error: "campaign_exists", campaign: existing.rows[0],
          hint: "one campaign per name. Attach to the existing one, or pick a name that says " +
                "what makes this run different. Nothing was written." });

      const channels = Array.isArray(args.channels)
        ? [...new Set(args.channels.map(x => String(x || "").trim().toLowerCase()).filter(Boolean))]
        : [];
      if (!channels.length) throw new ToolError({ error: "channels_required",
        hint: "a campaign with no channel cannot be measured; name at least one platform" });
      const live = await livePlatformSlugs(c);
      const unknown = channels.filter(ch => !live.includes(ch));
      if (unknown.length) throw new ToolError({ error: "unknown_channel", unknown,
        known_platforms: live,
        hint: "register a platform in marketing_subject before running a campaign on it — a " +
              "channel nobody registered is a channel no view will ever roll up" });

      const start = String(args.starts_on || "").trim();
      const end = args.ends_on ? String(args.ends_on).trim() : null;
      if (!/^\d{4}-\d{2}-\d{2}$/.test(start))
        throw new ToolError({ error: "bad_date", field: "starts_on", got: args.starts_on });
      if (end && !/^\d{4}-\d{2}-\d{2}$/.test(end))
        throw new ToolError({ error: "bad_date", field: "ends_on", got: args.ends_on });
      if (end && end < start) throw new ToolError({ error: "window_inverted", starts_on: start, ends_on: end,
        hint: "an end before its start would exclude every piece from every date filter" });

      // PLAUSIBILITY, NOT PROHIBITION. Backdating a campaign over content that
      // already published is exactly what this lane needs first — all 89 pieces
      // are historical — so a deep backdate ASKS rather than refuses.
      const today = new Date().toISOString().slice(0, 10);
      const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
      if (!args.confirm) {
        if (days(start, today) > 90)
          throw new ToolError({ error: "needs_confirm",
            reason: `starts_on is ${days(start, today)} days in the past`,
            hint: "backdating over already-published content is legitimate — resubmit with " +
                  "confirm:true if that is what you mean, and say so in note" });
        if (end && days(start, end) > 365)
          throw new ToolError({ error: "needs_confirm",
            reason: `the window is ${days(start, end)} days long`,
            hint: "a campaign longer than a year is usually a pillar wearing a campaign's " +
                  "clothes, and it will never be scorable. Resubmit with confirm:true if intended" });
      }

      const r = await c.query(
        `insert into campaign (name, goal, success_criterion, starts_on, ends_on, channels,
                               status, created_by, updated_by)
         values ($1,$2,$3,$4::date,$5::date,$6,'active',$7,$7)
         returning id, version, starts_on, ends_on`,
        [name, goal, crit, start, end, channels, actor.id]);
      const row = r.rows[0];

      // WHAT EVIDENCE THIS CAMPAIGN CAN EVEN HOPE FOR, returned at open time
      // rather than discovered at scoring time. On these channels, in this
      // window: how many placements exist and how many of them are measured. If
      // the answer is "42 placements, 0 measured", the caller learns NOW that
      // this campaign is unscorable, instead of six weeks from now.
      const ev = await c.query(
        `select count(*)::int as placements,
                count(*) filter (where measured)::int as measured,
                count(*) filter (where campaign_id is null)::int as unattached
           from v_placement_measurement
          where platform = any($1)
            and (live_at is null or live_at::date >= $2::date)
            and ($3::date is null or live_at is null or live_at::date <= $3::date)`,
        [channels, start, end]);

      await writeEvent(c, actor, "open-campaign", "campaign", row.id,
        { new: { name, goal, success_criterion: crit, starts_on: start, ends_on: end,
                 channels, status: "active" },
          human_quote: args.human_quote || null,
          agent_rationale: args.note || null,
          idempotency_key: args.idempotency_key });

      return { ok: true, campaign_id: row.id, name, version: row.version,
               starts_on: row.starts_on, ends_on: row.ends_on, channels, status: "active",
               success_criterion: crit,
               evidence_available_in_window: {
                 placements: ev.rows[0].placements,
                 measured: ev.rows[0].measured,
                 unmeasured: ev.rows[0].placements - ev.rows[0].measured,
                 unattached_to_any_campaign: ev.rows[0].unattached },
               note: ev.rows[0].measured === 0
                 ? "NOTHING in this window on these channels is measured today. This campaign " +
                   "is not scorable until measure-placement or the Blotato pull lands metrics — " +
                   "say that to the human rather than letting the campaign imply evidence it has not got."
                 : null };
    }),
  },

  "score-campaign": {
    write: true,
    description: "Close a campaign with a VERDICT against the criterion it was opened with — the act that turns a pile of metrics into an answer. Separate from open-campaign on purpose (the same reason retire-rule is separate from activate-rule): a verdict is a judgment, it needs arguments opening must never accept, and it must be impossible to reach by accident while editing a date. THE REFUSAL THAT MATTERS: it will not accept 'worked' or 'did_not_work' over ZERO measured placements, and it asks for confirm below the coverage floor in system_config (marketing.scoring_min_coverage_pct). 73 of 89 placements in this system have never been measured, including all 42 on X, so a verdict formed over them would be a guess wearing a number. 'inconclusive' is always available and is the HONEST answer when the measurement never happened — use it rather than reaching for confirm. Snapshots the coverage into coverage_at_scoring so nobody can later re-read a thin verdict as a thick one. Requires base_version from a fresh read. Refuses a campaign that is already scored: changing a recorded verdict is a new fact, not an edit, and Joe rules on it.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      campaign: { type: "string", description: "campaign name (exact) or uuid" },
      base_version: { type: "integer", description: "from a fresh read; a conflict is a question for the human, never a retry" },
      verdict: { type: "string", enum: ["worked","did_not_work","inconclusive"],
        description: "measured against the success_criterion this campaign was OPENED with — the verb quotes it back to you in the response" },
      evidence: { type: "string",
        description: "REQUIRED. What in the record supports this verdict, in one or two lines. Name the numbers you read." },
      close: { type: "boolean", default: true,
        description: "false scores it but leaves status active — for a mid-flight read-out. The verdict is still recorded and still requires evidence." },
      human_quote: { type: "string", description: "the partner's literal words when he called it" },
      confirm: { type: "boolean", description: "acknowledge scoring below the coverage floor" } },
      required: ["idempotency_key","campaign","base_version","verdict","evidence"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "score-campaign", args, async () => {
      await require0066(c);

      const evidence = String(args.evidence || "").trim();
      if (!evidence) throw new ToolError({ error: "evidence_required",
        hint: "a verdict with no evidence is an opinion the record will later quote as a fact" });

      const cam = await resolveCampaign(c, args.campaign);
      await versionGuard(c, "campaign", cam.id, args.base_version);
      if (cam.scored_at) throw new ToolError({ error: "already_scored",
        campaign_id: cam.id, verdict: cam.outcome_verdict, scored_at: cam.scored_at,
        outcome_note: cam.outcome_note,
        hint: "this campaign already carries a verdict. Changing it is a new fact, not an " +
              "edit — surface the existing verdict to the human and let him rule. Nothing was written." });

      const sc = (await c.query(
        `select placements, pieces, measured_placements, unmeasured_placements,
                coverage_pct, views_total, interactions_total
           from v_campaign_scorecard where campaign_id=$1`, [cam.id])).rows[0]
        || { placements: 0, pieces: 0, measured_placements: 0, unmeasured_placements: 0,
             coverage_pct: null, views_total: null, interactions_total: null };

      const measured = Number(sc.measured_placements || 0);
      const coverage = sc.coverage_pct === null ? null : Number(sc.coverage_pct);

      // THE HARD FLOOR, and confirm cannot cross it. A campaign over which
      // nothing at all was measured has no evidence of any kind, so 'worked' and
      // 'did_not_work' are both unsupportable — not merely thin. 'inconclusive'
      // is the true answer and is always allowed, which is why this refuses
      // instead of asking: an unmeasured campaign is exactly the case where a
      // confirm prompt would be clicked through.
      if (measured === 0 && args.verdict !== "inconclusive")
        throw new ToolError({ error: "no_measured_evidence",
          campaign_id: cam.id, placements: Number(sc.placements || 0),
          measured_placements: 0, unmeasured_placements: Number(sc.unmeasured_placements || 0),
          hint: "not one placement on this campaign carries a metric, so '" + args.verdict +
                "' cannot be supported by anything. Record 'inconclusive' with the reason, or " +
                "measure the placements first. An unmeasured placement is NOT a zero result." });

      // THE SOFT FLOOR: thin but real evidence asks rather than refuses.
      const floor = Number(await config(c, "marketing.scoring_min_coverage_pct", 50));
      if (!args.confirm && coverage !== null && coverage < floor && args.verdict !== "inconclusive")
        throw new ToolError({ error: "needs_confirm",
          reason: `only ${coverage}% of this campaign's placements are measured ` +
                  `(${measured} of ${sc.placements}); the floor is ${floor}%`,
          measured_placements: measured, unmeasured_placements: Number(sc.unmeasured_placements || 0),
          hint: "the unmeasured placements are not zeros, they are unknowns. Either measure " +
                "more, record 'inconclusive', or resubmit with confirm:true and say in evidence " +
                "why the measured subset is representative." });

      const snapshot = { placements: Number(sc.placements || 0), pieces: Number(sc.pieces || 0),
                         measured_placements: measured,
                         unmeasured_placements: Number(sc.unmeasured_placements || 0),
                         coverage_pct: coverage,
                         views_total: sc.views_total === null ? null : Number(sc.views_total),
                         interactions_total: sc.interactions_total === null ? null : Number(sc.interactions_total),
                         floor_pct: floor, confirmed_below_floor: !!args.confirm,
                         snapshot_at: new Date().toISOString() };

      const close = args.close !== false;
      await c.query(
        `update campaign set outcome_verdict=$1, outcome_note=$2, coverage_at_scoring=$3,
                             scored_at=now(), scored_by=$4, updated_by=$4,
                             status = case when $5 then 'closed' else status end
          where id=$6`,
        [args.verdict, evidence, JSON.stringify(snapshot), actor.id, close, cam.id]);

      await writeEvent(c, actor, "score-campaign", "campaign", cam.id, {
        field: "outcome_verdict",
        old: { status: cam.status, outcome_verdict: null },
        new: { status: close ? "closed" : cam.status, outcome_verdict: args.verdict,
               coverage: snapshot },
        agent_rationale: evidence,
        human_quote: args.human_quote || null,
        idempotency_key: args.idempotency_key });

      return { ok: true, campaign_id: cam.id, name: cam.name,
               verdict: args.verdict, status: close ? "closed" : cam.status,
               scored_against_criterion: cam.success_criterion,
               coverage: snapshot,
               note: coverage !== null && coverage < 100
                 ? `${snapshot.unmeasured_placements} of ${snapshot.placements} placements on this ` +
                   "campaign were never measured. Say that beside the verdict — the totals above " +
                   "cover the measured subset only and are not the campaign's whole result."
                 : null };
    }),
  },

  "attach-to-campaign": {
    write: true,
    description: "Bind published content to the campaign it belongs to — the link that makes 259 metrics answerable. Takes content by the handles a caller actually holds: the live post URL, the Blotato post id, or a placement/piece uuid. IT DOES NOT CREATE CONTENT, and that is a decision from evidence rather than a limitation: all 89 existing pieces were created by pipelines/pull_placement_metrics.py at publish time, keyed on placement.external_id, so a second birth path here would mint a duplicate the moment the ingest next ran. Content that is PLANNED but unpublished therefore has no record-layer home yet — say so plainly rather than inventing a row. ATOMIC: if any item cannot be resolved the WHOLE call refuses and nothing is written, because a partial attach that silently skips two items is a campaign that quietly under-reports its own content. A piece already attached to a DIFFERENT campaign is refused; moving one is `reattach` with base_version and a reason, one piece at a time, because re-pointing content rewrites what a past verdict was based on. Attaching content that published outside the campaign's window asks for confirm. The response always reports how many of the attached placements are actually MEASURED — usually the answer is few, and the caller needs to know that before quoting any total.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      campaign: { type: "string", description: "campaign name (exact) or uuid" },
      items: { type: "array", items: { type: "string" },
        description: "post URLs, Blotato post ids, placement uuids or content_piece uuids. Max 100." },
      reattach: { type: "boolean",
        description: "move a piece off another campaign. Requires exactly ONE item, a reason, and piece_base_version." },
      piece_base_version: { type: "integer", description: "content_piece.version, from a fresh read. reattach only." },
      reason: { type: "string", description: "REQUIRED for reattach: why this content belongs to a different campaign than the one it was filed under" },
      confirm: { type: "boolean", description: "acknowledge attaching content published outside the campaign window" } },
      required: ["idempotency_key","campaign","items"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "attach-to-campaign", args, async () => {
      await require0066(c);

      const items = Array.isArray(args.items) ? args.items.filter(x => String(x || "").trim()) : [];
      if (!items.length) throw new ToolError({ error: "items_required" });
      if (items.length > 100) throw new ToolError({ error: "too_many_items", count: items.length,
        hint: "max 100 per call — split the batch" });

      const cam = await resolveCampaign(c, args.campaign);
      if (cam.scored_at && !args.confirm)
        throw new ToolError({ error: "needs_confirm",
          reason: "this campaign is already scored; adding content changes what the recorded verdict covers",
          verdict: cam.outcome_verdict, scored_at: cam.scored_at,
          hint: "resubmit with confirm:true only if the verdict is still honest with this " +
                "content in it, and expect to re-state the coverage" });

      // RESOLVE EVERYTHING FIRST, WRITE NOTHING UNTIL IT ALL RESOLVES. A partial
      // attach is the false-completeness failure in this domain: the campaign
      // would look complete while quietly missing whatever did not resolve.
      const resolved = [];
      const failed = [];
      for (const raw of items) {
        const ref = String(raw).trim();
        try {
          let piece = null, placement = null;
          if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ref)) {
            const p = await c.query("select id, campaign_id, version, status from content_piece where id=$1", [ref]);
            if (p.rows.length) piece = p.rows[0];
          }
          if (!piece) {
            placement = await resolvePlacement(c, ref);
            const p = await c.query(
              "select id, campaign_id, version, status from content_piece where id=$1", [placement.piece_id]);
            piece = p.rows[0];
          }
          resolved.push({ ref, piece, placement });
        } catch (e) {
          failed.push({ ref, error: e.payload ? e.payload.error : "unresolved",
                        detail: e.payload ? e.payload.hint : String(e) });
        }
      }
      if (failed.length)
        throw new ToolError({ error: "unresolved_items", unresolved: failed,
          resolved_count: resolved.length,
          hint: "NOTHING was written. Every item must resolve, because a campaign that " +
                "silently dropped two of its twelve posts under-reports its own content and " +
                "nothing downstream can tell." });

      // Reattach is a different act with a different blast radius, so it has
      // different rules: one piece, a version guard, and a stated reason. The
      // plain attach path needs no version guard because it only ever writes
      // null -> value and the update is conditional on the null, so it cannot
      // clobber a concurrent writer — it loses the race visibly instead.
      const reattach = args.reattach === true;
      if (reattach) {
        if (resolved.length !== 1) throw new ToolError({ error: "reattach_is_one_at_a_time",
          count: resolved.length,
          hint: "moving content between campaigns rewrites what a past verdict was based on; " +
                "do it deliberately, one piece at a time" });
        if (!String(args.reason || "").trim()) throw new ToolError({ error: "reason_required",
          hint: "say why this content belongs to a different campaign than the one it was filed under" });
        await versionGuard(c, "content_piece", resolved[0].piece.id, args.piece_base_version);
      }

      // Window plausibility, across the batch, once.
      if (!args.confirm) {
        const ids = resolved.map(r => r.piece.id);
        const out = await c.query(
          `select count(*)::int as n from placement p
            where p.piece_id = any($1) and p.live_at is not null
              and (p.live_at::date < $2::date
                   or ($3::date is not null and p.live_at::date > $3::date))`,
          [ids, cam.starts_on, cam.ends_on]);
        if (out.rows[0].n > 0)
          throw new ToolError({ error: "needs_confirm",
            reason: `${out.rows[0].n} of these placements published outside the campaign window ` +
                    `(${cam.starts_on} to ${cam.ends_on || "open"})`,
            hint: "either the window is wrong or this content is not part of this campaign. " +
                  "Resubmit with confirm:true if you mean it." });
      }

      const attached = [], already = [], conflicts = [];
      for (const r of resolved) {
        if (r.piece.campaign_id === cam.id) { already.push(r.ref); continue; }
        if (r.piece.campaign_id && !reattach) {
          const other = await c.query("select name from campaign where id=$1", [r.piece.campaign_id]);
          conflicts.push({ ref: r.ref, piece_id: r.piece.id,
                           currently_on: other.rows[0] ? other.rows[0].name : r.piece.campaign_id });
          continue;
        }
        const upd = reattach
          ? await c.query(
              "update content_piece set campaign_id=$1, updated_by=$2 where id=$3 returning id",
              [cam.id, actor.id, r.piece.id])
          : await c.query(
              "update content_piece set campaign_id=$1, updated_by=$2 where id=$3 and campaign_id is null returning id",
              [cam.id, actor.id, r.piece.id]);
        if (!upd.rows.length) { // lost a race with a concurrent attach
          const now = await c.query("select campaign_id from content_piece where id=$1", [r.piece.id]);
          conflicts.push({ ref: r.ref, piece_id: r.piece.id,
                           currently_on: now.rows[0] ? now.rows[0].campaign_id : null,
                           note: "another writer attached this piece first — nothing was overwritten" });
          continue;
        }
        attached.push({ ref: r.ref, piece_id: r.piece.id,
                        moved_from: reattach ? r.piece.campaign_id : null });
        await writeEvent(c, actor, reattach ? "attach-to-campaign:reattach" : "attach-to-campaign",
          "content_piece", r.piece.id,
          { field: "campaign_id",
            old: { campaign_id: r.piece.campaign_id },
            new: { campaign_id: cam.id, campaign: cam.name },
            agent_rationale: args.reason || null,
            idempotency_key: args.idempotency_key });
      }

      if (conflicts.length)
        throw new ToolError({ error: "already_on_another_campaign", conflicts,
          hint: "a piece belongs to one campaign. Use reattach (one item, a reason and " +
                "piece_base_version) if it really moved. This call is rolled back whole." });

      // THE COVERAGE LINE. Attaching content does not measure it, and a caller
      // who reads only "12 attached" will quote totals that cover almost none of
      // them. As of 2026-08-02 that is 73 placements out of 89.
      const cov = (await c.query(
        `select count(*)::int as placements,
                count(*) filter (where measured)::int as measured
           from v_placement_measurement where campaign_id=$1`, [cam.id])).rows[0];

      return { ok: true, campaign_id: cam.id, campaign: cam.name,
               attached: attached.length, attached_items: attached,
               already_attached: already,
               campaign_now_covers: {
                 placements: cov.placements, measured: cov.measured,
                 unmeasured: cov.placements - cov.measured },
               note: cov.measured < cov.placements
                 ? `${cov.placements - cov.measured} of this campaign's ${cov.placements} ` +
                   "placements carry NO metrics. They are unmeasured, not zero — do not " +
                   "average or total over them as if they scored nothing."
                 : null };
    }),
  },

  "measure-placement": {
    write: true,
    description: "Record what one placement actually did — including, and especially, that it could not be measured. THE RULE THIS VERB EXISTS FOR: an unmeasured placement must stay VISIBLY unmeasured and must never read as a zero. 73 of 89 placements have never been measured, among them all 42 on X, and a reader who totals metrics by platform is currently handed 0 for X, which is a lie about performance rather than a fact about it. So `unavailable:true` with a reason is a first-class outcome here, exactly the way record-finding's found:false is: it lands a real placement_measurement row saying we looked and there was nothing, which is a different fact from nobody having looked. USE THIS FOR MEASUREMENTS THE SCHEDULED PULL CANNOT SEE — a figure read off a platform's own dashboard, an off-platform outcome (a DM, a reply, a consult that traced back to a post), or a confirmed 'this platform returns no analytics for us'. It REFUSES source 'blotato_api': that provenance belongs to pipelines/pull_placement_metrics.py, and a hand-written row claiming it would make the pull's output untrustworthy — use 'blotato_ui_manual', 'platform_native', 'joe_observed' or similar. A genuine measured zero IS allowed and is common (173 of 259 existing metric values are 0), but a payload where EVERY value is zero asks for confirm, because that is the shape an empty API response takes and it is precisely how an unmeasured post becomes a measured zero. Metrics are snapshots keyed (placement, kind, observed_at), so re-recording the same instant is a no-op rather than a duplicate.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      placement: { type: "string", description: "the live post URL, the Blotato post id, or the placement uuid" },
      source: { type: "string",
        description: "REQUIRED. Where the number came from: 'blotato_ui_manual', 'platform_native', 'joe_observed', a URL. 'blotato_api' is REFUSED — that source string belongs to the scheduled pull." },
      metrics: { type: "object",
        description: "{kind: number}. Kinds are stored verbatim as the source names them, snake_cased — views_count, reach_count, likes_count, comments_count, shares_count, saves_count, follows_count, interactions_sum, profile_visits_count, profile_activity_count. Do NOT map a platform's word onto a different platform's word; an equivalence nobody ruled is a wrong number later." },
      unavailable: { type: "boolean",
        description: "true records that measurement was ATTEMPTED and returned nothing. Requires reason. This is the honest alternative to silence, and to a zero." },
      reason: { type: "string", description: "REQUIRED with unavailable: why there is no number — 'the platform exposes no analytics for this account', 'post deleted', 'API returns 404'." },
      observed_at: { type: "string", description: "when the number was read (ISO); defaults to now. The snapshot key — pass the real read time, not the time you typed it." },
      note: { type: "string", description: "anything a later reader needs" },
      confirm: { type: "boolean", description: "acknowledge an all-zero payload or an out-of-band value" } },
      required: ["idempotency_key","placement","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "measure-placement", args, async () => {
      await require0066(c);

      const source = String(args.source || "").trim();
      if (!source) throw new ToolError({ error: "source_required",
        hint: "a number with no provenance is a rumour; say where you read it" });
      if (source.toLowerCase() === "blotato_api")
        throw new ToolError({ error: "reserved_source", source,
          hint: "'blotato_api' is the scheduled pull's provenance " +
                "(pipelines/pull_placement_metrics.py). A hand-written row wearing it would " +
                "make every API row unverifiable. Use 'blotato_ui_manual' for a figure read " +
                "off Blotato's own screen, 'platform_native' for the platform's dashboard, or " +
                "'joe_observed' for something Joe saw happen." });

      const unavailable = args.unavailable === true;
      const metrics = (args.metrics && typeof args.metrics === "object") ? args.metrics : null;
      const kinds = metrics ? Object.keys(metrics).filter(k => metrics[k] !== undefined && metrics[k] !== null) : [];

      // THE TWO REFUSALS THAT KEEP SILENCE OUT OF THE RECORD.
      if (unavailable && kinds.length)
        throw new ToolError({ error: "ambiguous_measurement",
          hint: "unavailable:true says there was nothing to record. Send the metrics, or send " +
                "the unavailability — never both in one act." });
      if (!unavailable && !kinds.length)
        throw new ToolError({ error: "nothing_to_record",
          hint: "pass metrics{}, or pass unavailable:true with a reason. An empty call would " +
                "leave this placement looking exactly like the 73 nobody has ever measured, " +
                "which is the one outcome this verb exists to prevent." });
      if (unavailable && !String(args.reason || "").trim())
        throw new ToolError({ error: "reason_required",
          hint: "'no data' with no reason cannot be acted on. Say whether the platform gives " +
                "us nothing, the post is gone, or the pull has simply never run — those lead " +
                "to three different next moves." });

      const pl = await resolvePlacement(c, args.placement);
      const observedAt = args.observed_at || null;

      if (unavailable) {
        const att = await c.query(
          `insert into placement_measurement (placement_id, attempted_at, source, outcome, reason,
                                              metric_kinds, note, recorded_by)
           values ($1, coalesce($2::timestamptz, now()), $3, 'unavailable', $4, '{}', $5, $6)
           on conflict (placement_id, source, attempted_at) do nothing
           returning id, attempted_at`,
          [pl.id, observedAt, source, String(args.reason).trim(), args.note || null, actor.id]);
        await writeEvent(c, actor, "measure-placement", "placement", pl.id, {
          occurred_at: observedAt,
          field: "measurement",
          new: { outcome: "unavailable", source, reason: String(args.reason).trim() },
          agent_rationale: "attempted and returned nothing — recorded so it is not mistaken for zero",
          idempotency_key: args.idempotency_key });
        return { ok: true, placement_id: pl.id, platform: pl.platform,
                 outcome: "unavailable", source, reason: String(args.reason).trim(),
                 recorded: !!att.rows.length,
                 measured: false,
                 note: "This placement is now recorded as ATTEMPTED AND UNMEASURED. It still " +
                       "reports measured:false in v_placement_measurement and it still has no " +
                       "number — that is the point. Do not read it as a zero." };
      }

      // ── values: validated one at a time, and the refusals are specific ──────
      const band = await config(c, "marketing.metric_value_band", { max: 1000000 });
      const clean = {};
      let allZero = true;
      for (const k of kinds) {
        const key = String(k).trim();
        if (!/^[a-z][a-z0-9_]*$/.test(key))
          throw new ToolError({ error: "bad_metric_kind", kind: k,
            hint: "kinds are the source's own names, snake_cased: views_count, reach_count, " +
                  "interactions_sum. Never invent an equivalence between two platforms' words." });
        const v = Number(metrics[k]);
        if (!Number.isFinite(v))
          throw new ToolError({ error: "bad_metric_value", kind: key, got: metrics[k],
            hint: "values are numbers. A missing number is not 0 — omit the kind entirely, or " +
                  "record the whole placement as unavailable." });
        if (v < 0)
          throw new ToolError({ error: "negative_metric", kind: key, got: v,
            hint: "no engagement count is negative; this is a sign error or a delta pasted as a total" });
        if (!args.confirm && Number(band.max) && v > Number(band.max))
          throw new ToolError({ error: "needs_confirm",
            reason: `${key} = ${v} exceeds the plausibility band (${band.max})`,
            hint: "the largest real value in placement_metric on 2026-08-02 was 845,877 " +
                  "(view_time_ms_sum). Check for a units error, then resubmit with confirm:true if real." });
        if (v !== 0) allZero = false;
        clean[key] = v;
      }

      // THE ALL-ZERO GATE, and the number behind it. Real zeros are ordinary: 173
      // of 259 existing metric values are 0. But across 26 real analytics
      // snapshots, ZERO of them were all-zero — an entirely zero payload is not
      // what real data looks like, it is what an empty API response looks like.
      // Writing one turns an unmeasured placement into a measured zero, which is
      // exactly the false completeness this whole verb guards against.
      if (allZero && !args.confirm)
        throw new ToolError({ error: "needs_confirm",
          reason: `every one of the ${Object.keys(clean).length} values is 0`,
          hint: "0 of 26 real snapshots in this system were all-zero, so this is far more " +
                "likely an empty response than a measured nothing. If the platform genuinely " +
                "returned no data, use unavailable:true with a reason — that keeps the " +
                "placement UNMEASURED instead of recording it as a zero result. Resubmit with " +
                "confirm:true only if the post truly earned zero of everything." });

      let landed = 0, unchanged = 0;
      for (const [kind, value] of Object.entries(clean)) {
        const r = await c.query(
          `insert into placement_metric (placement_id, observed_at, kind, value, source)
           values ($1, coalesce($2::timestamptz, now()), $3, $4, $5)
           on conflict (placement_id, kind, observed_at) do nothing returning kind`,
          [pl.id, observedAt, kind, value, source]);
        if (r.rows.length) landed++; else unchanged++;
      }

      await c.query(
        `insert into placement_measurement (placement_id, attempted_at, source, outcome, reason,
                                            metric_kinds, note, recorded_by)
         values ($1, coalesce($2::timestamptz, now()), $3, 'recorded', null, $4, $5, $6)
         on conflict (placement_id, source, attempted_at) do nothing`,
        [pl.id, observedAt, source, Object.keys(clean), args.note || null, actor.id]);

      // The same status catch-up the scheduled pull performs, for the same
      // reason: a piece whose placements gained metrics is 'measured'. Guarded on
      // the current status so it can never walk a retired or rejected piece back
      // into the live funnel.
      const promoted = await c.query(
        `update content_piece set status='measured', updated_by=$1
          where id=$2 and status in ('live','scheduled') returning id`,
        [actor.id, pl.piece_id]);

      await writeEvent(c, actor, "measure-placement", "placement", pl.id, {
        occurred_at: observedAt,
        field: "metrics",
        new: { outcome: "recorded", source, kinds: Object.keys(clean), values: clean },
        agent_rationale: args.note || null,
        idempotency_key: args.idempotency_key });

      return { ok: true, placement_id: pl.id, platform: pl.platform, piece_id: pl.piece_id,
               outcome: "recorded", source, measured: true,
               metrics_written: landed, metrics_already_present: unchanged,
               piece_marked_measured: !!promoted.rows.length,
               note: unchanged && !landed
                 ? "every kind already had a row at this exact observed_at — nothing changed. " +
                   "Pass the real read time if this was a new pull."
                 : null };
    }),
  },
};


// Deal Room contract. Durable writes use the same envelope and event helper as
// the rest of this registry; the one explicit exception is the ephemeral lease.
Object.assign(TOOLS, {
  "get-deal-room": {
    description: "Read one Deal Room record: board fields, append-only note/next-step thread, critical dates, and attributed event history. Placeholder Salesforce fields are structurally excluded.",
    inputSchema: { type: "object", properties: { deal: { type: "string" } }, required: ["deal"] },
    handler: async (c, _actor, args) => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      const deal = await c.query(
        "select d.name, v.phase, v.owner, v.type, v.city, v.segment, v.attention, to_jsonb(v.next_date)#>>'{}' as next_date from v_deal_room_deal v join deal d on d.id = v.id where v.id=$1",
        [s.id],
      );
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
      return stripDealPlaceholders({ deal_id: s.id, ...deal.rows[0], thread: thread.rows,
        critical_dates: criticalDates.rows, events: history.rows });
    },
  },

  "presence-lease": {
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
    write: true,
    description: "Patch one Deal Room cell using its last-seen event as the field base. Only a newer event for this exact deal+field conflicts; other fields never block it.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      field: { type: "string", enum: DEAL_ROOM_FIELDS }, value: {},
      base_event_id: { anyOf: [{ type: "string" }, { type: "null" }] },
    }, required: ["idempotency_key", "deal", "field", "value", "base_event_id"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "patch-deal-field", args, async () => {
      assertDealRoomField(args.field, args.value);
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
        args.idempotency_key, "patch-deal-field");
      return { ok: true, deal_id: s.id, field: args.field, ...applied };
    }),
  },

  "add-deal-note": {
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
      assertDealRoomField("next_date", args.next_date ?? null);
      await lockDealField(c, s.id, "next_step");
      const prior = await c.query(
        "select id, text from deal_note where deal_id=$1 and kind='next_step' order by created_at desc, id desc limit 1 /* dealroom:current-step */",
        [s.id],
      );
      const note = await c.query(
        "insert into deal_note (deal_id, kind, text, actor_id) values ($1,'next_step',$2,$3) returning id, created_at /* dealroom:add-next-step */",
        [s.id, args.text.trim(), actor.id],
      );
      await c.query(
        "update deal set next_date=$2, updated_by=$3 where id=$1 /* dealroom:set-next-date */",
        [s.id, args.next_date ?? null, actor.id],
      );
      await writeEvent(c, actor, "set-next-step", "deal", s.id, {
        field: "next_step",
        old: { next_step: prior.rows[0]?.text ?? null },
        new: { next_step: args.text.trim(), next_date: args.next_date ?? null },
        idempotency_key: args.idempotency_key,
      });
      return { ok: true, deal_id: s.id, next_step_id: note.rows[0].id,
        supersedes: prior.rows[0]?.id ?? null, created_at: note.rows[0].created_at };
    }),
  },

  "resolve-conflict": {
    write: true,
    description: "Resolve an open Deal Room cell conflict in one call by applying value a or b through the normal field patch/event path.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, conflict_id: { type: "string" },
      winner: { type: "string", enum: ["a", "b"] },
    }, required: ["idempotency_key", "conflict_id", "winner"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "resolve-conflict", args, async () => {
      if (!['a', 'b'].includes(args.winner)) throw new ToolError({ error: "invalid_winner", allowed: ["a", "b"] });
      const found = await c.query(
        `select id, deal_id, field, value_a, value_b, status
           from deal_conflict where id=$1 for update /* dealroom:get-conflict */`,
        [args.conflict_id],
      );
      if (!found.rows.length) throw new ToolError({ error: "not_found", table: "deal_conflict", id: args.conflict_id });
      const conflict = found.rows[0];
      if (conflict.status !== "open") throw new ToolError({ error: "conflict_already_resolved", conflict_id: conflict.id });
      await lockDealField(c, conflict.deal_id, conflict.field);
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
});

// The deploy-gap pair (2026-08-08, Joe's reconnect complaint). call-verb's
// dispatch lives in mcp.js callTool (interception, so profile checks apply to
// the INNER verb by recursion); these registry entries exist so both tools are
// listed with schemas. list-verbs is the discovery half: a session whose
// cached tool list predates a deploy asks the live registry what exists now.
Object.assign(TOOLS, {
  "list-verbs": {
    description: "The LIVE verb registry — names, descriptions, write flags, input schemas — straight from the deployed Worker, bypassing the connector's cached tool list. Use when a verb you expect is missing from your tool list (a deploy since this session connected): find it here, then invoke it through call-verb without any reconnect.",
    inputSchema: { type: "object", properties: {
      filter: { type: "string", description: "substring match on verb name" } } },
    handler: async (c, actor, args) => {
      const names = Object.keys(TOOLS).sort()
        .filter(n => !args.filter || n.includes(args.filter));
      return { ok: true, count: names.length,
               verbs: names.map(n => ({ name: n, write: !!TOOLS[n].write,
                 description: (TOOLS[n].description || "").slice(0, 200),
                 inputSchema: TOOLS[n].inputSchema || null })) };
    },
  },
  "call-verb": {
    write: true,   // rides the writer path so inner writes work; inner reads work there too
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
});

// Doctrine store verbs (P2, decision 82a2fb62) — same envelope, same contracts.
Object.assign(TOOLS, doctrineTools({ withEnvelope, writeEvent, ToolError }));
