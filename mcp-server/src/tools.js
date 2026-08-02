// CARR MCP tool registry — Wave 1 verbs (tool-contracts-2026-07-30.md §2).
// Every write runs the envelope: idempotency replay via tool_call, actor from
// the verified token (never the payload), base_version conflicts ask and never
// auto-retry, every accepted write lands its event row, plausibility bands
// confirm instead of block. NO SEND VERB EXISTS.
// Descriptions are poka-yoke docstrings (contracts §5): what, when, edge cases.

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

// THE NODE KEY IS THE REF, NOT THE NAME, and that choice is load-bearing.
// v_party_graph carries exactly one ref per party (0020's `distinct on`), so a
// ref identifies a party. A name does not: production holds `Dr. James Allen
// Tyrer` twice — L-208 and C-155, the same human as two un-merged records — and
// joining paths on the name string would silently weld those two records into
// one node and invent hops that do not exist. Refs keep them separate, which is
// the truth of the book today, duplicate and all.
//
// The cost, stated rather than hidden: an edge whose endpoint carries NO ref
// cannot be walked. Today that is zero edges of 31. The verb counts them and
// returns the count as `edges_unwalkable` rather than dropping them quietly, so
// the day it stops being zero the answer says so instead of just getting smaller.
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

// ---------- the registry ----------
// Each: { description, inputSchema, write: bool, humanOnly?: bool, handler(client, actor, args) }

export const TOOLS = {

  // ===== reads (carr_reader connection) =====

  "find": {
    write: false,
    description: "Search people, practices, buildings, deals, leads, vendors by name (fuzzy). Use FIRST when you only have a name; returns refs (L-/C-/V-) the write verbs take. Matches party.name / deal.name / client.roster_ref. Also returns the intro-graph edges touching the match (who can introduce whom), newest first. Read-only.",
    inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
    handler: async (c, _a, args) => {
      const q = args.query;
      // [amendment 11] Through v_ref_index, not the base tables. Merged records are
      // KEPT here (unlike resolveSubject) and carry the flag: someone searching a
      // merged name should learn the record exists and where it went, rather than
      // be told nothing matched.
      const parties = await c.query(
        `select display_name as name, city, specialty, org_name, ref, subject_type as kind, merged
         from v_ref_index
         where subject_type in ('lead','client','vendor')
           and (display_name % $1 or display_name ilike $2)
         order by similarity(display_name,$1) desc limit 10`, [q, `%${q}%`]);
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
      return { parties: parties.rows, deals: deals.rows, connections: connections.rows };
    },
  },

  "who-do-we-know": {
    write: false,
    description: "\"Who gets me to X?\" — walks the intro graph BACKWARD from a target (a ref like C-155 / V-CPA-006, or a name) and returns every referral path up to 3 hops (walks the party_link table), shortest first, each rendered as a readable chain (\"Dion Moniz -knows-> Jon Shaw -intro-> Dr. James Allen Tyrer\"). The first name in a chain is who Joe asks. Read-only, and it never guesses: an ambiguous name returns needs_disambiguation with the candidates, and a target that exists but carries no edges says so rather than returning an empty list that reads like 'no such person'.",
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
      const nodes = await c.query(
        `with n as (
           select from_ref as ref, from_name as name from v_party_graph
           union
           select to_ref,   to_name             from v_party_graph)
         select distinct ref, name from n
          where ref ilike $1 or name ilike $2
          order by ref`, [q, `%${q}%`]);
      let node = null;
      if (nodes.rows.length === 1) {
        node = nodes.rows[0];
      } else if (nodes.rows.length > 1) {
        // An exact ref among several name-ish matches is not ambiguous.
        const exact = nodes.rows.filter(r => (r.ref || "").toLowerCase() === q.toLowerCase());
        if (exact.length === 1) node = exact[0];
        else throw new ToolError({ error: "needs_disambiguation", target: q,
          candidates: nodes.rows.map(r => ({ ref: r.ref, name: r.name })),
          hint: "more than one party in the intro graph matches — pass the exact ref" });
      }

      if (!node) {
        // NOT the same answer as "no path". A record that exists and simply has
        // no edges is a gap in the Links data; a name nobody has ever recorded is
        // a different problem, and collapsing the two would hide both.
        const known = await c.query(
          `select display_name as name, ref, subject_type as kind from v_ref_index
            where subject_type in ('lead','client','vendor')
              and (ref ilike $1 or display_name ilike $2) limit 5`, [q, `%${q}%`]);
        return { target: q, resolved: null, paths: [],
                 in_graph: false,
                 matching_records: known.rows,
                 note: known.rows.length
                   ? "This record exists but carries no intro-graph edges yet — the connection may simply not be logged. Record it with link-parties."
                   : "No record and no graph node matches that name. Try `find` first." };
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

      return {
        target: q,
        resolved: { ref: node.ref, name: node.name },
        in_graph: true,
        max_depth: depth,
        path_count: paths.rows.length,
        capped: paths.rows.length === cap,
        edges_unwalkable: unwalkable.rows[0].n,
        paths: paths.rows.map(r => ({
          hops: r.hops, ask_ref: r.ask_ref, ask_name: r.ask_name,
          path: r.chain, ref_path: r.ref_path, evidence: r.first_note })),
        note: paths.rows.length
          ? "The FIRST name in each chain is who to ask. Run the pairing through DNA/Network/introduction-rules.md before making the ask — a path existing does not make it a clean ask."
          : "Nobody in the intro graph reaches this record within " + depth + " hops. That may mean the connection is not logged rather than not real.",
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
      fields: { type: "object", description: "subset of: phase, segment, outcome, closed_on, won_value, notes_path, salesforce_id" } },
      required: ["idempotency_key","deal","base_version","fields"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-deal", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      await versionGuard(c, "deal", s.id, args.base_version);
      const allowed = ["phase","segment","outcome","closed_on","won_value","notes_path","salesforce_id"];
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
      let orgId = null;
      if (args.org_name) {
        const o = await c.query(
          `insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id`,
          [args.org_name, actor.id]);
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
          let orgId = null;
          if (o.new_party.org_name) {
            const og = await c.query(
              "insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id",
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
      const allowed = ["stage","seeking","offers","referral_active","territory","rivalry_group","out_of_market","intro_notes"];
      const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
      if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed });
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

  "record-counter": {
    write: true,
    description: "Log a negotiation round: whose paper (side), the economics (rate REQUIRES its basis — never a bare number), TI, free rent, term. Writes a counter row (side, rate + basis, ti, free_rent, term). Round number auto-increments per deal+side if omitted. Out-of-band rates ask for confirm.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      side: { type: "string", enum: ["tenant","landlord","buyer","seller"] },
      proposed_on: { type: "string", description: "YYYY-MM-DD; defaults today" },
      rate_amount: { type: "number" },
      rate_basis: { type: "string", enum: ["usd_sf_yr","usd_sf_mo","usd_mo_gross","usd_yr_gross"] },
      ti_amount: { type: "number" }, ti_basis: { type: "string", enum: ["usd_total","usd_sf"] },
      free_rent_months: { type: "number" }, term_months: { type: "integer" },
      options_note: { type: "string" }, escalator: { type: "string" }, expires_on: { type: "string" },
      note: { type: "string" }, confirm: { type: "boolean" } },
      required: ["idempotency_key","deal","side"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-counter", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      if (args.rate_amount != null && !args.rate_basis)
        throw new ToolError({ error: "missing_basis", hint: "a rate is meaningless without its basis" });
      await rateConfirm(c, args, normRate(args.rate_amount, args.rate_basis), "rate.asking_confirm_band_sf_yr");
      const round = (await c.query(
        "select coalesce(max(round_no),0)+1 as n from negotiation_round where deal_id=$1 and side=$2",
        [s.id, args.side])).rows[0].n;
      const r = await c.query(
        `insert into negotiation_round (deal_id, round_no, side, proposed_on, rate_amount, rate_basis,
           ti_amount, ti_basis, free_rent_months, term_months, options_note, escalator, expires_on,
           note, created_by, updated_by)
         values ($1,$2,$3,coalesce($4::date,current_date),$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15)
         returning id, round_no`,
        [s.id, round, args.side, args.proposed_on || null, args.rate_amount || null,
         args.rate_basis || null, args.ti_amount || null, args.ti_basis || null,
         args.free_rent_months || null, args.term_months || null, args.options_note || null,
         args.escalator || null, args.expires_on || null, args.note || null, actor.id]);
      await writeEvent(c, actor, "record-counter", "deal", s.id,
        { new: { round, side: args.side, rate: args.rate_amount, basis: args.rate_basis },
          idempotency_key: args.idempotency_key });
      return { ok: true, round_id: r.rows[0].id, round_no: r.rows[0].round_no };
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
      if (args.survivor_party === args.merged_party)
        throw new ToolError({ error: "same_party", hint: "a party cannot be merged into itself" });

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

  "record-finding": {
    write: true,
    description: "Land ONE open-source research or enrichment finding as a record_flag row. This is the only path a verification result becomes part of the record — findings do not go into a markdown report (Joe, 2026-08-02: 'we dont write to markdown in the new system only the database'). IT NEVER EDITS AN IDENTITY FIELD. A finding is stored BESIDE the record with its source; a disagreement with name/phone/email/title/specialty is passed as proposes_correction, which is recorded as a proposal for the owning partner and applied by them, never by this verb. STORE NOTHING-FOUND TOO: pass found:false and the empty result becomes a real row, so a record nobody searched is distinguishable from one that was searched and came up dry — that difference is the whole meaning of a verified stamp. source is REQUIRED on every row; provenance is binding, and a finding without it is a rumour. Pass expires_on for anything volatile: title and company change with promotions and job moves, so an expired verification reads as unverified rather than as fact. Common kinds: verified (an identity pass, value lists what was checked), email, cell, office_phone, social, website, npi, license_status, title, entity_filing, address, discrepancy. A near-match on a similar name is contamination, not confirmation — record both candidates and pick neither. Also writes an event, so the finding shows up in catch-me-up without a second read surface.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      subject: { type: "string", description: "C-127 / L-204 / V-CPA-006 / P-0301, or an exact deal name" },
      subject_kind: { type: "string", enum: ["auto","party"], default: "auto",
        description: "'party' pins the flag to the person/org behind a ref instead of the client/lead/vendor record" },
      kind: { type: "string", description: "what was looked for: verified, email, cell, social, npi, title, discrepancy..." },
      value: { type: "object", description: "the finding, structured. Omit when found:false." },
      found: { type: "boolean", default: true, description: "false records a searched-and-empty result" },
      source: { type: "string", description: "REQUIRED. Where it came from: a URL, 'NPPES', 'Sunbiz', 'practice website'." },
      observed_at: { type: "string", description: "when the source was read (ISO); defaults to now" },
      expires_on: { type: "string", description: "date after which this reads as unverified again (volatile fields)" },
      proposes_correction: { type: "object",
        description: "{field, current, proposed} — RECORDED ONLY. The owning partner applies it." } },
      required: ["idempotency_key","subject","kind","source"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "record-finding", args, async () => {
      const src = String(args.source || "").trim();
      if (!src) throw new ToolError({ error: "source_required",
        hint: "every finding carries its provenance; a finding without a source is a rumour" });

      const found = args.found !== false;
      if (found && (!args.value || typeof args.value !== "object" || !Object.keys(args.value).length))
        throw new ToolError({ error: "value_required",
          hint: "pass the finding as value{}, or pass found:false to record a searched-and-empty result" });

      let subjectType, subjectId;
      if (args.subject_kind === "party") {
        subjectType = "party";
        subjectId = await resolvePartyByRef(c, args.subject);
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
    description: "Write a rule from the human's own words (status: proposed — it activates only via activate-rule, also human-gated). Capture the verbatim quote. Personal-scope rules (voice, format) set personal_to.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, statement: { type: "string" },
      human_quote: { type: "string" }, scope: { type: "object" },
      personal: { type: "boolean", description: "true = applies to this partner only" } },
      required: ["idempotency_key","statement","human_quote"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "teach", args, async () => {
      const r = await c.query(
        `insert into rule (statement, human_quote, taught_by, scope, personal_to)
         values ($1,$2,$3,$4,$5) returning id`,
        [args.statement, args.human_quote, actor.id, JSON.stringify(args.scope || {}),
         args.personal ? actor.id : null]);
      await writeEvent(c, actor, "teach", "rule", r.rows[0].id,
        { new: { statement: args.statement }, human_quote: args.human_quote,
          idempotency_key: args.idempotency_key });
      return { ok: true, rule_id: r.rows[0].id, status: "proposed" };
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
};
