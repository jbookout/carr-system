import { ToolError } from "./tool-error.js";
import { fmtPhoneUS, resolveSubject } from "./verb-support.js";
import { withEnvelope, writeEvent } from "./versioned-write.js";

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

export function documentTools() {
  return {
  // ===== [ORDER 13] the document factory =====

    "register-template": {
      discoveryOrder: 47,
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
      discoveryOrder: 48,
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
      discoveryOrder: 49,
      write: true,
      description: "Record what a HUMAN says happened to a prepared document — sets document status: draft -> handed_to_joe -> sent. It records a statement; it does not send anything and no verb anywhere does. Also the place to record the lint and leak-guard results and the filed attachments once the local fill run has produced them.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        document_id: { type: "string" },
        status: { type: "string", enum: ["draft","handed_to_joe","sent"] },
        human_quote: { type: "string", description: "the partner's own words, when he said it" },
        working_attachment: { type: "string" }, pdf_attachment: { type: "string" },
        lint_passed: { type: "boolean" }, leak_check_passed: { type: "boolean" },
        format_exception: { type: "string", description:
          "Required ONLY to record a send that goes against Joe's format split — an LOI or letter leaving as PDF, or a spreadsheet leaving as a live file. State why (the listing agent's system blocks .docx, the client asked for the live sheet). Joe's rule ends with 'unless the partner says otherwise'; this is where the partner says otherwise." },
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

        // JOE'S SPLIT ON OUTBOUND FORMAT, in his words: "the LOI is sent over to the
        // listing agent in word format so they can easily edit or revise. i know i
        // said everything goes out pdf but thats really just spreadsheets that go
        // out pdf so noone can see the formulas"
        //
        // TWO RULES, and the correction is the important half. An LOI or letter
        // goes out in WORD, because the listing agent editing it IS the
        // negotiation; a PDF makes them retype it. A SPREADSHEET goes out as PDF so
        // nobody reads our formulas. The older blanket "everything goes out PDF" is
        // superseded and would break the negotiation it exists to start.
        //
        // THIS IS NOT ABOUT WHETHER THE FILE WAS MADE. output_kinds {working,pdf}
        // names ROLES, not formats, and both files exist by now. The question here
        // is which one we handed over — and this verb is the only place a human
        // states that a document went out at all.
        if (args.status === "sent") {
          const tk = await c.query(
            `/* outbound_template_kind */
           select coalesce(t.field_map->>'template_kind',
                           lower(regexp_replace(t.source_path, '^.*\\.', ''))) as template_kind
             from document d join doc_template t on t.id = d.template_id
            where d.id = $1`, [args.document_id]);
          const kind = (tk.rows[0]?.template_kind || "").toLowerCase();
          // Attachments and the status change often land in one call, so the gate
          // reads the MERGED state rather than the stored row alone.
          const working = args.working_attachment ?? cur.working_attachment;
          const pdf = args.pdf_attachment ?? cur.pdf_attachment;
          // A throwaway word is not a decision. Length rather than a banned-word
          // list, for the same reason as confirm-merge's basis: any such list is one
          // synonym from useless.
          const excused = String(args.format_exception ?? "").trim().length >= 20;

          const wordKinds = ["docx", "doc", "dotx", "rtf"];
          const sheetKinds = ["xlsx", "xls", "xlsm", "csv"];
          if (!excused) {
            if (wordKinds.includes(kind) && !working)
              throw new ToolError({ error: "outbound_format", template_kind: kind,
                ruling: "Joe: \"the LOI is sent over to the listing agent in word format so they can " +
                        "easily edit or revise.\"",
                why: "An LOI or letter goes out in WORD. The listing agent being able to edit or revise " +
                     "it IS the negotiation workflow; a PDF makes them retype it. No working file is " +
                     "recorded on this document, so what is on file to have been sent is the PDF.",
                hint: "Record the Word file as working_attachment. If the send genuinely went out as PDF " +
                      "— their system blocks .docx, say — pass format_exception with the reason." });
            if (sheetKinds.includes(kind) && !pdf)
              throw new ToolError({ error: "outbound_format", template_kind: kind,
                ruling: "Joe: \"thats really just spreadsheets that go out pdf so noone can see the formulas\"",
                why: "A spreadsheet goes out as PDF. A live workbook carries our formulas, and the " +
                     "recipient can read every one of them.",
                hint: "Record the PDF as pdf_attachment. If the client genuinely needed the live sheet, " +
                      "pass format_exception with the reason." });
          }
        }

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
            new: { document_id: args.document_id, sent_status: args.status || cur.sent_status,
                   // An exception nobody can find later is indistinguishable from an
                   // inconsistency, so it rides the event.
                   ...(args.format_exception ? { format_exception: args.format_exception } : {}) },
            human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        return { ok: true, document_id: args.document_id, sent_status: args.status || cur.sent_status };
      }),
    },
  };
}
