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

async function requestHash(args) {
  const data = new TextEncoder().encode(JSON.stringify(args, Object.keys(args).sort()));
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
    "insert into tool_call (idempotency_key, verb, actor_id, request_hash, response) values ($1,$2,$3,$4,$5)",
    [key, verb, actor.id, hash, JSON.stringify(result)]);
  return result;
}

async function writeEvent(client, actor, verb, subjectType, subjectId, fields = {}) {
  await client.query(
    `insert into event (occurred_at, actor_id, verb, subject_type, subject_id, field,
       old_value, new_value, cause, human_quote, agent_rationale, idempotency_key)
     values (coalesce($1::timestamptz, now()), $2, $3, $4, $5, $6, $7, $8, 'human_stated', $9, $10, $11)`,
    [fields.occurred_at || null, actor.id, verb, subjectType, subjectId, fields.field || null,
     fields.old ? JSON.stringify(fields.old) : null, fields.new ? JSON.stringify(fields.new) : null,
     fields.human_quote || null, fields.agent_rationale || null, fields.idempotency_key || null]);
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

const FK = { deal: "deal_id", client: "client_id", lead: "lead_id", vendor: "vendor_id" };

// [ORDER 18] How many intro-graph edges `find` returns per query. A cap, not a
// page: find answers "who is this and who do we know through them", and the whole
// subgraph belongs to a graph verb nobody has ordered yet.
const CONNECTIONS_CAP = 12;

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
  const ag = (await c.query(
    `select a.slug, a.display_name, a.email from deal_participant dp
       join actor a on a.id=dp.actor_id
      where dp.deal_id=$1 and dp.role='lead' and dp.to_at is null limit 1`, [dealId])).rows[0];
  bag.agent = ag ? { slug: ag.slug, display_name: ag.display_name, email: ag.email, phone: null }
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
    description: "Search people, practices, buildings, deals, leads, vendors by name (fuzzy). Use FIRST when you only have a name; returns refs (L-/C-/V-) the write verbs take. Also returns the intro-graph edges touching the match (who can introduce whom), newest first. Read-only.",
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

  "catch-me-up": {
    write: false,
    description: "The merged timeline (events + activities) for one deal, client, lead, or vendor, newest first, plus its narrative-file pointer. Use before any conversation about a record.",
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
    description: "What needs attention now: due next-actions, critical dates inside 14 days, untriaged ingest items. The morning-brief substrate.",
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
    description: "Scored, unsuppressed leads. ALL of them surface — qualification is the human's job, never pre-filtered.",
    inputSchema: { type: "object", properties: { limit: { type: "integer", default: 30 } } },
    handler: async (c, _a, args) => ({ leads: (await c.query("select * from v_lead_hot order by score desc nulls last limit $1", [args.limit || 30])).rows }),
  },

  "stale-records": {
    write: false,
    description: "Active deals gone quiet 14+ days. Replaces the hand-run staleness sweep.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ stale: (await c.query("select * from v_stale_records order by days_quiet desc nulls first")).rows }),
  },

  "integrity-digest": {
    write: false,
    description: "The heartbeat's lines: row counts, export freshness (dead-man), writes-by-dell, norm-owed, triage queue.",
    inputSchema: { type: "object", properties: {} },
    handler: async (c) => ({ digest: (await c.query("select * from v_integrity_digest")).rows }),
  },

  // ===== writes (carr_writer connection, envelope enforced) =====

  "log-activity": {
    write: true,
    description: "Log a business touch (call, email, meeting, tour, text, note, LOI...) against a deal/client/lead/vendor. THE default verb after any real-world contact. occurred_at = when it happened (defaults now); anything missing goes in 'owed', never invented.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" },
      ref: { type: "string", description: "L-/C-/V- ref or deal name" },
      kind: { type: "string", enum: ["call","email_out","email_in","meeting","tour","text","note","counter_sent","counter_received","loi","lease_signed","task"] },
      summary: { type: "string" }, detail: { type: "string" },
      occurred_at: { type: "string", description: "ISO timestamp; omit for now" },
      owed: { type: "string", description: "what is missing (a figure, a name) — recorded as owed" },
      human_quote: { type: "string", description: "the human's literal words, if dictated" },
    }, required: ["idempotency_key","ref","kind","summary"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "log-activity", args, async () => {
      const s = await resolveSubject(c, args.ref);
      const r = await c.query(
        `insert into activity (occurred_at, actor_id, kind, summary, detail, owed, ${FK[s.type]}, source)
         values (coalesce($1::timestamptz, now()), $2, $3, $4, $5, $6, $7, 'stated') returning id, occurred_at`,
        [args.occurred_at || null, actor.id, args.kind, args.summary, args.detail || null, args.owed || null, s.id]);
      await writeEvent(c, actor, "log-activity", s.type, s.id,
        { new: { activity: r.rows[0].id, kind: args.kind }, human_quote: args.human_quote, idempotency_key: args.idempotency_key });
      return { ok: true, activity_id: r.rows[0].id, subject: s };
    }),
  },

  "stamp-touch": {
    write: true,
    description: "Truck shorthand for log-activity: one-line call/text stamp. 'Called Hughes, going well' and done. Contact kinds only — a note is annotation, not a touch (it would not move Last Touch since 0017); use log-activity kind:note or an event for annotation.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, ref: { type: "string" },
      kind: { type: "string", enum: ["call","text"], default: "call" },
      summary: { type: "string" } }, required: ["idempotency_key","ref","summary"] },
    handler: async (c, actor, args) =>
      TOOLS["log-activity"].handler(c, actor, { ...args, kind: args.kind || "call" }),
  },

  "set-next-action": {
    write: true,
    description: "Set YOUR one open ball on a subject (replaces your previous open one; Dell's stays untouched — one ball per person per subject). Say whose turn it is and by when.",
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

  "add-critical-date": {
    write: true,
    description: "A date with consequences (LOI expiry, lease expiration, option window, earnout). source is REQUIRED — where did this date come from?",
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
    description: "Field-level change to a deal (phase, segment, outcome, notes_path). Requires base_version from a fresh read; a conflict means someone else wrote — ask the human, never retry blind. Phase must be an existing slug (list: pending/research/site_selection/negotiation/closing/closed + imported).",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, deal: { type: "string" },
      base_version: { type: "integer" },
      fields: { type: "object", description: "subset of: phase, segment, outcome, closed_on, won_value, notes_path" } },
      required: ["idempotency_key","deal","base_version","fields"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "update-deal", args, async () => {
      const s = await resolveSubject(c, args.deal);
      if (s.type !== "deal") throw new ToolError({ error: "not_a_deal", resolved: s });
      await versionGuard(c, "deal", s.id, args.base_version);
      const allowed = ["phase","segment","outcome","closed_on","won_value","notes_path"];
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

  "set-lead": {
    write: true,
    description: "THE handoff: make joe or dell the current lead on a deal. Closes the old lead row, opens the new one, one event. The database enforces exactly one current lead.",
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
    description: "Create a person or org. CHECKS for existing matches first (email, similar name) and returns candidates INSTEAD of inserting when found — pass force_new:true only after the human confirms it is genuinely a different person. Never store 205-643-6555 (it is Dell's placeholder, not a contact).",
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

  "new-lead": {
    write: true,
    description: "Create a lead over a new or existing party; mints the next L-ref atomically. Stage must be an existing stage slug (they were imported from the live registry).",
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

  "new-client": {
    write: true,
    description: "Create a client over a party; mints the next C-ref. ALWAYS ask how they found us (acquisition_source) at intake — consult attribution starts day one.",
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
    description: "Create a vendor over a party; mints V-<CODE>-### (pass the category code explicitly: CPA, LEN, GC...). A Claude-found vendor enters at the prospect stage until a real call happens — that is a standing rule, not a suggestion.",
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
    description: "Log a negotiation round: whose paper (side), the economics (rate REQUIRES its basis — never a bare number), TI, free rent, term. Round number auto-increments per deal+side if omitted. Out-of-band rates ask for confirm.",
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
    description: "Register (or re-version) a CARR template so prepare-document can fill it. field_map is the reviewable contract between the template's slots and the record layer — a map carrying unreviewed:true is REFUSED by prepare-document until a human has read it. Registering never touches the template file; source_path points at the real file in Templates/ and nothing writes there, ever.",
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
    description: "Record what a HUMAN says happened to a prepared document: draft -> handed_to_joe -> sent. It records a statement; it does not send anything and no verb anywhere does. Also the place to record the lint and leak-guard results and the filed attachments once the local fill run has produced them.",
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
    description: "Record an intro-graph edge: who can introduce whom, who referred whom. Feeds who-do-we-know (find returns these) and the reciprocity ledger. kind comes from the party_link_kind table — today knows, intro, intro_received, can_introduce, works_with, referral — and the same edge recorded twice returns the first one, never a duplicate.",
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
    description: "HUMAN-confirmed merge of two duplicate parties: the merged one becomes a pointer to the survivor. Only after a human has looked at both records — the Garabadian rule means nothing auto-merges, ever.",
    inputSchema: { type: "object", properties: {
      idempotency_key: { type: "string" }, survivor_party: { type: "string" }, merged_party: { type: "string" } },
      required: ["idempotency_key","survivor_party","merged_party"] },
    handler: async (c, actor, args) => withEnvelope(c, actor, "confirm-merge", args, async () => {
      await c.query("update party set merged_into=$1, updated_by=$2 where id=$3",
        [args.survivor_party, actor.id, args.merged_party]);
      await writeEvent(c, actor, "confirm-merge", "party", args.merged_party,
        { new: { merged_into: args.survivor_party }, idempotency_key: args.idempotency_key });
      return { ok: true };
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
    description: "proposed -> active. The context compiler reads ACTIVE rules only; activation is a human decision by design.",
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
};
