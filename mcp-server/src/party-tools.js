import { RESEARCH_EVIDENCE_SCHEMA, UUID_RE, config, fmtPhoneUS, researchEvidence, resolvePartyByRef, resolveSubject, stampResearch, validateLinkKind } from "./verb-support.js";
import { ToolError } from "./tool-error.js";
import { versionGuard, withEnvelope, writeEvent } from "./versioned-write.js";
import { dealEvidenceEntries, mergeRelationshipFields, requireRelationshipPartner, trustedOverride } from "./vendor-relationship.js";
import { bindReferralDeal } from "./relationship-network.js";

// [defect 18b12fda-b79c-43a1-86c4-51b9623e12fd, 2026-08-14] THE VIOLATION WAS OURS.
// add-party (kind='org', name='Synthetic Lodge') refused twice with
// unique_violation on party_org_identity_uniq while a read-only tap of the same
// database found zero matching rows — because the collision was with the verb's
// OWN uncommitted work. The call carried org_name restating the org itself, so
// org_party_id() minted the org inside the open transaction, the main insert then
// asserted the same normalised identity a second time, the index refused, and the
// rollback erased both rows. Deterministic under fresh keys, invisible in the data.
//
// The guard below is IDENTITY-BASED and asks the database's own org_identity_key()
// — never a JS re-implementation, per that function's comment ("EXTEND this
// function rather than invent a second normalisation rule"). An org_name with a
// genuinely different identity stays legal on an org row: party.org_id is how a
// parent/sub-org structure (a national account over its franchisees) is expressed
// — see reassign-deal. Only the self-reference is dropped, and the caller is told.
// Shared by add-party and add-premises' new_party path (rule a8c55a47: two paths
// doing the same job must be the same code).
async function employerOrgId(c, actorId, kind, name, orgName) {
  if (!orgName) return { orgId: null, selfNamed: false };
  if (kind === "org") {
    const k = await c.query(
      "select org_identity_key($1) = org_identity_key($2) as same_org", [orgName, name]);
    if (k.rows[0]?.same_org) return { orgId: null, selfNamed: true };
  }
  const o = await c.query("select org_party_id($1,$2) as id", [orgName, actorId]);
  return { orgId: o.rows[0].id, selfNamed: false };
}

// The residual collision: an existing LIVE org that slipped the similarity guard
// (or was force_new'd past it) still trips party_org_identity_uniq on the insert.
// That refusal is correct — the index's comment says a same-name collision is
// resolved by disambiguating the NAME, never by weakening the key — but a raw
// unique_violation names an index, not a next step. Run the insert under a
// SAVEPOINT, and on this one constraint roll back to it and hand back the
// surviving row so the caller can reuse it or rename theirs.
//
// The savepoint is load-bearing, not ceremony: after any SQL error the enclosing
// transaction is aborted (25P02) and every later statement — including
// withEnvelope's own tool_call insert — would fail until a rollback. Catching the
// error in JS and simply querying on (as new-deal's 23505 mapper does) trades one
// opaque error for another.
export async function insertOrgPartyGuarded(c, savepoint, insertSql, insertParams, name) {
  await c.query(`savepoint ${savepoint}`);
  try {
    return { row: (await c.query(insertSql, insertParams)).rows[0] };
  } catch (e) {
    if (e.code !== "23505" || e.constraint !== "party_org_identity_uniq") throw e;
    await c.query(`rollback to savepoint ${savepoint}`);
    const existing = await c.query(
      `select id, name, email, city from party
        where kind='org' and merged_into is null and deleted_at is null
          and org_identity_key(name) = org_identity_key($1)`, [name]);
    return { conflict: existing.rows };
  }
}

function preferredMergeSurvivor(rows) {
  if (!Array.isArray(rows) || rows.length !== 2) return null;
  const score = row => [row.has_business_ref ? 1 : 0,
    Number(row.verified_identity_fields || 0), Number(row.linked_records || 0)];
  const [a, b] = rows;
  const as = score(a), bs = score(b);
  for (let i = 0; i < as.length; i += 1) {
    if (as[i] !== bs[i]) return as[i] > bs[i] ? a : b;
  }
  return new Date(a.created_at).getTime() <= new Date(b.created_at).getTime() ? a : b;
}

// vendor.stage is a FOREIGN KEY into vendor_stage(slug), and until now nothing
// checked it before the insert — so a plausible label (`prospect`, `Prospect`,
// `building`) came back as a bare "internal error" naming neither the field nor
// the options. Measured live 2026-08-10 re-creating Synthetic contact: four calls
// died that way before the pattern was readable. Same failure class as
// new-lead's stage/lane and update-vendor's category_slug branch.
//
// Pre-validating rather than catching the FK matters: once the violation fires,
// the transaction is poisoned and cannot even run the query that would list the
// valid slugs, so the caller gets nothing to correct with.
//
// The slug is the FULL label, lowercased, every run of non-alphanumeric
// characters collapsed to one underscore — which is why `building_working_on_it`
// works and `building` does not. That is the rule the original import used to
// seed the table, so it holds for any stage added later. Both are returned so a
// caller who has the human label can map it without a second round trip.
async function validateVendorStage(c, slug) {
  const r = await c.query("select slug from vendor_stage where slug=$1", [slug]);
  if (r.rows.length) return slug;
  const all = await c.query("select slug, label from vendor_stage order by slug");
  throw new ToolError({ error: "unknown_vendor_stage", got: slug, valid: all.rows,
    hint: "stage is a foreign key into vendor_stage; pass one of the listed slugs, never " +
          "the label. The slug is the label lowercased with each run of non-alphanumeric " +
          "characters collapsed to a single underscore. A genuinely new stage is an INSERT " +
          "into vendor_stage by a human, never a guess." });
}

async function refuseActorPhone(c, value, field) {
  if (!value) return;
  const digits = String(value).replace(/\D/g, "");
  const contacts = await c.query("select phone from actor where kind='human' and phone is not null");
  const configured = await config(c, "contacts.protected_phone_numbers", []);
  const phones = [...configured, ...contacts.rows.map(row => row.phone)];
  if (phones.some(phone => {
    const own = phone.replace(/\D/g, "");
    const suffix = own.length === 11 && own.startsWith("1") ? own.slice(1) : own;
    return suffix.length >= 10 && digits.endsWith(suffix);
  })) throw new ToolError({ error: "placeholder_phone", ...(field ? { field } : {}),
    hint: "a partner's own number is never a contact; record the field as unknown instead" });
}

export function partyTools() {
  return {
    "add-party": {
      discoveryOrder: 33,
      write: true,
      description: "Create a party (person or org). CHECKS for existing matches first (email, similar name) and returns candidates INSTEAD of inserting when found — pass force_new:true only after the human confirms it is genuinely a different person. A partner's own number is refused as a placeholder.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, name: { type: "string" },
        kind: { type: "string", enum: ["person","org"], default: "person" },
        org_name: { type: "string" }, phone: { type: "string" }, email: { type: "string" },
        city: { type: "string" }, state: { type: "string" }, county: { type: "string" },
        specialty: { type: "string" }, force_new: { type: "boolean" },
        research_evidence: RESEARCH_EVIDENCE_SCHEMA },
        required: ["idempotency_key","name"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "add-party", args, async () => {
        await refuseActorPhone(c, args.phone);
        if (!args.force_new) {
          const cand = await c.query(
            `select id, name, email, city,
                  ($1::text is not null and lower(email)=lower($1)) as exact_email_match
             from party where merged_into is null and
               (($1::text is not null and lower(email)=lower($1)) or name % $2)
           order by exact_email_match desc, similarity(name,$2) desc limit 5`, [args.email || null, args.name]);
          if (cand.rows.length) {
            // DECISIVE AUTO-RESOLUTION ONLY (WR-000019 slice S6, CONFLICT TIERING).
            // The single signal in this query strong enough to resolve without a
            // human is an EXACT match on a real identifier (email) — never
            // trigram name similarity alone, which routinely scores two
            // different real people or orgs as "similar" (that is the whole
            // reason this query exists). Auto-resolve only when there is
            // EXACTLY ONE candidate and it is that exact-identifier match;
            // anything else — multiple candidates, or a candidate that matched
            // on name similarity only — stays needs_confirm exactly as before.
            if (cand.rows.length === 1 && cand.rows[0].exact_email_match) {
              const existing = cand.rows[0];
              return { ok: true, party_id: existing.id, auto_resolved: true,
                       reason: "exact email match to an existing party; no duplicate created",
                       hint: "pass force_new:true to create a separate party anyway (e.g. a shared team inbox)" };
            }
            return { needs_confirm: true, candidates: cand.rows,
                     hint: "existing similar parties; reuse one, or resubmit force_new:true" };
          }
        }
        // THE GENERATOR, CLOSED (0059, 2026-08-02). This line used to INSERT an org
        // unconditionally with no lookup, so every contact minted a private copy of
        // their own employer: Synthetic Supply Co existed as 17 org rows, one per rep,
        // Synthetic Dental Co as 10, and all 415 org rows had exactly one inbound person
        // — a distribution with a single bucket, which is the signature. That is why
        // "who do we know at X" could not be answered: there was no X, only copies.
        // 0059 consolidated the 115 surplus rows AND added a unique index, so this
        // insert would now raise unique_violation on any org that already exists.
        // org_party_id() normalises the name, returns the existing survivor, and
        // mints only when genuinely new — so the duplicate count stops being a
        // running total. Placeholders like '(TBD — enrich)' deliberately still mint
        // separately: collapsing those would assert six unrelated people share an
        // employer, which is a fabricated fact, not a merge.
        const kind = args.kind || "person";
        const evidence = kind === "person"
          ? researchEvidence(args.research_evidence,
            ["name", "company", "phone", "specialty", "market"], "add-party")
          : null;
        const emp = await employerOrgId(c, actor.id, kind, args.name, args.org_name);
        const insertSql =
          `insert into party (kind,name,org_id,phone,email,city,state,county,specialty,created_by,updated_by)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10) returning id`;
        const insertParams =
          [kind, args.name, emp.orgId, args.phone || null, args.email || null,
           args.city || null, args.state || null, args.county || null, args.specialty || null, actor.id];
        let row;
        if (kind === "org") {
          const g = await insertOrgPartyGuarded(c, "add_party_org", insertSql, insertParams, args.name);
          if (g.conflict)
            return { needs_confirm: true, candidates: g.conflict,
                     hint: "a live organisation with this exact normalised identity already exists — " +
                           "reuse it, or disambiguate the NAME (the way 'Carr Riggs Ingram (advisory)' " +
                           "does); the identity key is never weakened, even under force_new" };
          row = g.row;
        } else {
          row = (await c.query(insertSql, insertParams)).rows[0];
        }
        if (evidence) await stampResearch(c, actor, row.id, evidence);
        await writeEvent(c, actor, "add-party", "party", row.id,
          { new: { name: args.name }, idempotency_key: args.idempotency_key });
        return { ok: true, party_id: row.id,
                 ...(emp.selfNamed ? { note: "org_name ignored: it names this organisation itself " +
                   "(an org is not its own employer; pass org_name on an org only for a PARENT org)" } : {}) };
      }),
    },

  // [ORDER 27 (a) + EXT (c)] One atomic call gives a deal its physical +
    // counterparty spine: building (exact-address match or create), space rows,
    // premises + premises_space, building_ownership rows, optional listing_side
    // participant. Vocabularies are the EXISTING CHECKs — nothing reopens here.
    "add-premises": {
      discoveryOrder: 34,
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
            org_name: { type: "string" }, force_new: { type: "boolean" },
            research_evidence: RESEARCH_EVIDENCE_SCHEMA }, required: ["name"] },
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
            // index makes the old blind insert a unique_violation waiting to happen,
            // and defect 18b12fda proved the self-collision variant (org restating
            // itself in org_name) fires even against clean data. Same helpers as
            // add-party; the conflict surfaces as a THROW here, per this site's
            // deliberate divergence noted above.
            const npKind = o.new_party.kind || "person";
            // A counterparty created while capturing premises is still a new
            // contact record.  Do not let the convenience path bypass the same
            // sourced-research gate as add-party.
            const npEvidence = researchEvidence(o.new_party.research_evidence,
              ["name", "company", "phone", "specialty", "market"], "add-premises.new_party");
            const npEmp = await employerOrgId(c, actor.id, npKind, o.new_party.name, o.new_party.org_name);
            const npSql = `insert into party (kind, name, org_id, created_by, updated_by)
             values ($1,$2,$3,$4,$4) returning id`;
            const npParams = [npKind, o.new_party.name, npEmp.orgId, actor.id];
            if (npKind === "org") {
              const g = await insertOrgPartyGuarded(c, "add_premises_org", npSql, npParams, o.new_party.name);
              if (g.conflict)
                throw new ToolError({ error: "needs_confirm", name: o.new_party.name,
                  candidates: g.conflict,
                  hint: "a live organisation with this exact normalised identity already exists — " +
                        "pass its ref as party_ref, or disambiguate the NAME; the identity key is " +
                        "never weakened, even under force_new" });
              partyId = g.row.id;
            } else {
              partyId = (await c.query(npSql, npParams)).rows[0].id;
            }
            await stampResearch(c, actor, partyId, npEvidence);
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

    "new-client": {
      discoveryOrder: 39,
      write: true,
      description: "Create a client over a party; mints the next C-ref (roster_ref). Sets client_status and acquisition_source. ALWAYS ask how they found us (acquisition_source) at intake — consult attribution starts day one.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, party_id: { type: "string" },
        status: { type: "string" }, vertical: { type: "string" }, subtype: { type: "string" },
        acquisition_source: { type: "string" }, acquisition_detail: { type: "string" },
        research_evidence: RESEARCH_EVIDENCE_SCHEMA },
        required: ["idempotency_key","party_id","status","acquisition_source","research_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "new-client", args, async () => {
        const evidence = researchEvidence(args.research_evidence,
          ["practice_name", "address", "phone", "specialty", "practitioners", "hours"], "new-client");
        await stampResearch(c, actor, args.party_id, evidence);
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
      discoveryOrder: 40,
      write: true,
      description: "Create a vendor over a party; sets vendor stage; mints V-<CODE>-### (pass the category code explicitly: CPA, LEN, GC...). A Claude-found vendor enters at the prospect stage until a real call happens — that is a standing rule, not a suggestion.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, party_id: { type: "string" },
        category: { type: "string" }, ref_code: { type: "string", description: "CPA / LEN / GC / ..." },
        stage: { type: "string", description: "a vendor_stage SLUG, not the label — e.g. prospect_uncontacted, building_working_on_it. A wrong value comes back with the full valid list." },
        research_evidence: RESEARCH_EVIDENCE_SCHEMA },
        required: ["idempotency_key","party_id","category","ref_code","stage","research_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "new-vendor", args, async () => {
        const evidence = researchEvidence(args.research_evidence,
          ["company", "title", "category", "market", "phone", "website", "deal_side"], "new-vendor");
        // Before the ref is minted: a rejected call must not burn a V-### number.
        await validateVendorStage(c, args.stage);
        await stampResearch(c, actor, args.party_id, evidence);
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
      discoveryOrder: 41,
      write: true,
      description: "Field-level change to a vendor (stage, seeking, offers, referral_active, territory, out_of_market). stage takes a vendor_stage SLUG, not the label; a wrong one comes back with the full valid list. base_version required.",
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
        const allowed = ["stage","seeking","offers","referral_active","territory","rivalry_group","out_of_market","intro_notes","category_slug","verticals","loan_programs","trust_override","deal_evidence"];
        const keys = Object.keys(args.fields).filter(k => allowed.includes(k));
        if (!keys.length && !Object.hasOwn(args.fields,"deal_evidence") && !Object.hasOwn(args.fields,"verify_deal_history")) throw new ToolError({ error: "no_updatable_fields", allowed });
        // Pre-validate rather than letting the FK abort the transaction: a poisoned
        // transaction cannot even fetch the slug list to explain itself.
        if (keys.includes("category_slug") && args.fields.category_slug !== null) {
          const slugs = (await c.query("select slug from vendor_category order by sort")).rows.map(r => r.slug);
          if (!slugs.includes(args.fields.category_slug))
            throw new ToolError({ error: "unknown_category_slug", got: args.fields.category_slug, allowed: slugs,
              hint: "a rare type is an INSERT into vendor_category by a human, never a guess" });
        }
        if (keys.includes("stage") && args.fields.stage !== null)
          await validateVendorStage(c, args.fields.stage);
        if (keys.includes("verticals") && args.fields.verticals !== null &&
            !(Array.isArray(args.fields.verticals) && args.fields.verticals.every(v => typeof v === "string")))
          throw new ToolError({ error: "verticals_not_array", hint: 'pass an array of strings, e.g. ["dental","vet"]' });
        if (keys.includes("trust_override")) {
          try { args.fields.trust_override = trustedOverride(args.fields.trust_override, actor); }
          catch (error) { throw new ToolError({ error: error.code }); }
        }
        if (keys.includes("loan_programs") && args.fields.loan_programs !== null &&
            !(Array.isArray(args.fields.loan_programs) && args.fields.loan_programs.every(v => typeof v === "string" && v.length <= 200)))
          throw new ToolError({ error: "loan_programs_invalid" });
        let entries = [];
        if (Object.hasOwn(args.fields, "deal_evidence")) {
          try { entries = dealEvidenceEntries(args.fields.deal_evidence); }
          catch (error) { throw new ToolError({ error: error.code }); }
          for (const entry of entries) {
            const live = await c.query("select d.id from public.deal d join public.client dc on dc.id=d.client_id join public.party dp on dp.id=dc.party_id where d.id=$1 and dc.merged_into is null and dp.merged_into is null and dp.deleted_at is null", [entry.deal_id]);
            if (!live.rows.length) throw new ToolError({ error: "deal_evidence_deal_not_found" });
          }
          args.fields.deal_evidence = entries;
        }
        if (Object.hasOwn(args.fields, "verify_deal_history")) {
          try { requireRelationshipPartner(actor, "deal_history_verification_refused"); }
          catch (error) { throw new ToolError({ error: error.code }); }
          if (args.fields.verify_deal_history !== true) throw new ToolError({ error: "deal_history_verification_refused" });
          args.fields.deal_history_verified_at = new Date().toISOString();
          keys.push("deal_history_verified_at");
        } else if (Object.hasOwn(args.fields, "deal_evidence")) {
          args.fields.deal_history_verified_at = null; keys.push("deal_history_verified_at");
        }
        const old = keys.length ? (await c.query(`select ${keys.join(",")} from vendor where id=$1`, [s.id])).rows[0] : {};
        const sets = keys.map((k, i) => `${k}=$${i + 2}`).join(", ");
        await c.query(`update vendor set ${sets ? sets + ", " : ""}updated_by=$1 where id=$${keys.length + 2}`,
          [actor.id, ...keys.map(k => k === "deal_evidence" ? JSON.stringify(args.fields[k]) : args.fields[k]), s.id]);
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
      discoveryOrder: 45,
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
          await refuseActorPhone(c, args.fields[k], k);
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

    "link-parties": {
      discoveryOrder: 50,
      write: true,
      description: "Record an intro-graph edge (a party_link row): who knows whom, who can introduce whom, who REFERRED whom. Feeds who-do-we-know (find returns these) and the reciprocity ledger. kind comes from the party_link_kind table — knows, works_with, can_introduce, intro_requested, introduced, referred (plus the legacy intro / intro_received) — and the same edge recorded twice returns the first one, never a duplicate. AN INTRODUCTION IS TERNARY: A introduced B to C, so pass via_party for the BROKER whenever one exists. from/to are the two people connected; via_party is who connected them. That is the whole basis of the reciprocity ledger — 'count where via = us' against 'count where via = them' — so an edge recorded with the broker left out is counted as nobody's referral and earns that vendor nothing.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, from_party: { type: "string" }, to_party: { type: "string" },
        kind: { type: "string", description: "a slug from party_link_kind: knows, works_with, can_introduce, intro_requested, introduced, referred" },
        via_party: { type: "string", description: "WHO made the connection — the broker in the middle. A ref (V-/C-/L-/T-/P-) or a party uuid. Omit ONLY for a genuinely direct edge with no third party; for 'a vendor sent us this client' the vendor goes HERE, not on an end. Refused if it resolves to either end, because a broker cannot be one of the two people being connected." },
        deal_id: { type: "string", description: "Exact referred deal UUID; referral/referred only, destination must be its live client party, note required. Several deals may attach to one relationship." },
        occurred_on: { type: "string", description: "YYYY-MM-DD — when it happened. An offer and a completed introduction are different events and the gap between them is the follow-up." },
        note: { type: "string" } }, required: ["idempotency_key","from_party","to_party","kind"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "link-parties", args, async () => {
        // [ORDER 18] The old hard-coded enum (can_introduce/intro_sent/intro_received/
        // works_with/referred) is retired. It was one of the two vocabularies ORDER 17
        // found: this verb could not write a single kind the backfill used, and the
        // backfill could not write one this verb offered. One table, one vocabulary.
        const kind = await validateLinkKind(c, args.kind);

        // REFS RESOLVE HERE (2026-08-10). Both ends used to go straight into the
        // insert as uuids, so passing V-DEV-007 or L-214 — the refs this verb's own
        // schema tells callers to use — hit a uuid cast error and surfaced as a bare
        // "internal error". Measured live while recording a real referral edge: the
        // call failed twice on refs and succeeded immediately on party uuids. Same
        // failure class as loop #261 — a validation failure wearing the costume of
        // an outage.
        const ends = {};
        for (const side of ["from_party", "to_party", "via_party"]) {
          const raw = String(args[side] || "").trim();
          // via_party is optional — a direct edge has no broker. from/to are required
          // by the schema, so an empty string there still falls through to the
          // resolver and surfaces as a named subject_not_found rather than a null.
          if (!raw && side === "via_party") { ends[side] = null; continue; }
          // PostgreSQL returns uuids lowercase; compare stored and resolved ends in one spelling.
          if (UUID_RE.test(raw)) { ends[side] = raw.toLowerCase(); continue; }
          const s = await resolveSubject(c, raw);          // throws subject_not_found, named
          let pid = s.type === "party" ? s.id : null;
          if (!pid) {
            const r = await c.query(
              "select party_id from v_ref_index where subject_type=$1 and subject_id=$2", [s.type, s.id]);
            pid = r.rows[0]?.party_id || null;
          }
          if (!pid) throw new ToolError({ error: "no_party_under_ref", side, got: raw, resolved: s,
            hint: "that ref resolves to a record with no person behind it — the intro graph links PEOPLE" });
          // A merged party is a tombstone; an edge must attach to the survivor or it
          // is invisible to who-do-we-know (the same rule find applies on read).
          const hop = await c.query("select merged_into from party where id=$1", [pid]);
          ends[side] = hop.rows[0]?.merged_into || pid;
        }
        if (ends.from_party === ends.to_party)
          throw new ToolError({ error: "self_link", got: args.from_party,
            hint: "both ends resolve to the same person — an intro graph edge needs two parties" });
        // A broker sits BETWEEN the two ends. If via resolves to one of them the edge
        // is malformed, and it fails silently rather than loudly: the reciprocity
        // ledger counts "via = them", so a vendor recorded as both the broker and an
        // end double-counts on one side of the exact comparison this shape exists for.
        if (ends.via_party && (ends.via_party === ends.from_party || ends.via_party === ends.to_party))
          throw new ToolError({ error: "via_is_an_end", got: args.via_party,
            hint: "via_party is who CONNECTED the two ends, so it cannot also be one of them — " +
                  "for 'this vendor sent us this client', from = us, to = the client, via = the vendor" });

        let occurredOn = null;
        if (args.occurred_on != null && String(args.occurred_on).trim() !== "") {
          occurredOn = String(args.occurred_on).trim();
          if (!/^\d{4}-\d{2}-\d{2}$/.test(occurredOn))
            throw new ToolError({ error: "bad_occurred_on", got: args.occurred_on,
              hint: "occurred_on is a calendar date, YYYY-MM-DD" });
        }

        // The referrer is the relationship's broker as stored after this call (a
        // direct relationship credits its from end); the audit names the same party.
        const attachDeal = async (linkId, broker) => {
          let bound;
          try { bound = await bindReferralDeal(c, actor, args, ends, kind,
            { id: linkId, referred_by: broker || ends.from_party, occurred_on: occurredOn }); }
          catch (error) { throw new ToolError({error:error.code || 'referral_deal_invalid'}); }
          if (bound) await writeEvent(c,actor,'link-parties','party',bound.referred_by,{new:{link_id:linkId,deal_id:bound.deal_id,referred_by:bound.referred_by,kind:'referred'},idempotency_key:args.idempotency_key});
        };
        // Upsert against 0020's unique index. Before it, two taps wrote two identical
        // edges and nothing complained. `do nothing` returns no row on conflict, so
        // the existing edge is read back and returned — the caller gets the edge it
        // asked for either way, and learns which case it was.
        const ins = await c.query(
          `insert into party_link (from_party, to_party, kind, note, via_party, occurred_on, source, created_by)
         values ($1,$2,$3,$4,$5,$6,'stated',$7)
         on conflict (from_party, to_party, kind) do nothing
         returning id`,
          [ends.from_party, ends.to_party, kind, args.note || null,
           ends.via_party, occurredOn, actor.id]);
        if (!ins.rows.length) {
          // Locked: a concurrent caller naming another broker waits here and then
          // sees this call's backfill, instead of validating against a stale row.
          const cur = await c.query(
            "select id, via_party, occurred_on from party_link where from_party=$1 and to_party=$2 and kind=$3 for update",
            [ends.from_party, ends.to_party, kind]);
          const row = cur.rows[0];
          if (args.deal_id && row.via_party && row.via_party !== ends.via_party) throw new ToolError({error:"referral_broker_mismatch"});
          await attachDeal(row.id, row.via_party || ends.via_party);
          // BACKFILL, not overwrite. Every edge written between 0051 and 2026-08-10
          // carries a null broker, because this verb had no via_party to pass — the
          // schema was ternary and the only writer was binary. Those edges are the
          // reciprocity ledger's missing half, so a later call that DOES name the
          // broker must be able to fill the hole. It fills nulls only: a stored
          // broker or date is evidence already recorded and is never silently
          // replaced by a second caller's guess.
          const fills = {};
          if (ends.via_party && !row.via_party) fills.via_party = ends.via_party;
          if (occurredOn && !row.occurred_on) fills.occurred_on = occurredOn;
          if (Object.keys(fills).length) {
            await c.query(
              `update party_link set via_party = coalesce(via_party,$2),
                                   occurred_on = coalesce(occurred_on,$3)
              where id = $1`,
              [row.id, ends.via_party, occurredOn]);
            await writeEvent(c, actor, "link-parties", "party", ends.from_party,
              { old: { via_party: row.via_party, occurred_on: row.occurred_on },
                new: { kind, to: ends.to_party, ...fills, backfilled: true },
                idempotency_key: args.idempotency_key });
            return { ok: true, link_id: row.id, existing: true, backfilled: Object.keys(fills) };
          }
          // No event row: nothing changed in the record, and an event that says a
          // link was made when none was is the kind of fiction the ledger exists to
          // prevent. The tool_call row (envelope) still records that it was asked.
          return { ok: true, link_id: row.id, existing: true };
        }
        await writeEvent(c, actor, "link-parties", "party", ends.from_party,
          { new: { kind, to: ends.to_party, via: ends.via_party, occurred_on: occurredOn,
                   from_input: args.from_party, to_input: args.to_party },
            idempotency_key: args.idempotency_key });
        await attachDeal(ins.rows[0].id, ends.via_party);
        return { ok: true, link_id: ins.rows[0].id, existing: false };
      }),
    },

    "confirm-merge": {
      discoveryOrder: 51,
      write: true,
      humanOnly: true,
      description: "HUMAN-confirmed merge of two duplicate parties: sets merged_into on the loser so it becomes a pointer to the survivor. Only after a human has looked at both records — the Hovanian rule means nothing auto-merges, ever.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, survivor_party: { type: "string" }, merged_party: { type: "string" },
        match_basis: { type: "string", description: "The corroborating signal: exact domain, normalized org name, phone, address, or corroborated name plus city. Recorded permanently with the merge." },
        same_person_because: { type: "string", description:
          "Required ONLY when one side holds just a lead row and the other just a client row. Joe's ruling: everyone starts as a lead, so an L- and a C- ref for one person is the system working, not a duplicate. State what makes these TWO party rows for ONE human — matching NPI, address, the intake record — not that the names match." } },
        required: ["idempotency_key","survivor_party","merged_party","match_basis"] },
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
        const basis = String(args.match_basis || "").trim();
        if (basis.length < 8)
          throw new ToolError({ error: "match_basis_required",
            hint: "state the corroborating signal that established this duplicate; a name alone is never a merge basis" });

        // Serialize overlapping merges before reading scores or moving roles.
        // A stable UUID order also keeps reverse calls from deadlocking. The
        // caller's transaction holds these locks through the mutation and event.
        const endpoints = await c.query(
          `/* merge_live_endpoints */
         select id, merged_into from party where id = any($1::uuid[])
          order by id for update`, [[surv.partyId, merg.partyId]]);
        if (endpoints.rows.length !== 2)
          throw new ToolError({ error: "merge_survivorship_unavailable",
            hint: "both party rows must be readable before a merge can run" });
        for (const endpoint of endpoints.rows) {
          if (endpoint.merged_into)
            throw new ToolError({ error: "party_already_merged", party_id: endpoint.id,
              merged_into: endpoint.merged_into,
              hint: "read the live party and confirm the duplicate pair again before merging" });
        }

        // The human confirms THAT this pair is a duplicate. Code decides WHICH
        // row survives, with the rule's exact precedence, so a human cannot
        // accidentally retire the more-cited or better-evidenced record.
        const metrics = await c.query(
          `/* merge_survivorship */
         select p.id,p.created_at,
           exists(select 1 from lead l where l.party_id=p.id and l.registry_ref is not null)
            or exists(select 1 from client cl where cl.party_id=p.id and cl.roster_ref is not null and cl.merged_into is null)
            or exists(select 1 from vendor v where v.party_id=p.id and v.vendor_ref is not null) as has_business_ref,
           (select count(distinct rf.kind) from record_flag rf where rf.subject_type='party' and rf.subject_id=p.id
             and rf.kind in ('verified','address','phone','email','npi','specialty') and coalesce(rf.value->>'found','true') <> 'false') as verified_identity_fields,
           -- Activities attach to role rows, which move with the party below.
           -- EXISTS counts a multi-role activity once, without multiplying it.
           ((select count(*) from activity a
              where exists(select 1 from client cl where cl.id=a.client_id and cl.party_id=p.id)
                 or exists(select 1 from lead l where l.id=a.lead_id and l.party_id=p.id)
                 or exists(select 1 from vendor v where v.id=a.vendor_id and v.party_id=p.id))
             + (select count(*) from deal_participant dp where dp.party_id=p.id)
             + (select count(*) from party_link pl where pl.from_party=p.id or pl.to_party=p.id or pl.via_party=p.id)) as linked_records
          from party p where p.id = any($1::uuid[])`, [[surv.partyId, merg.partyId]]);
        const preferred = preferredMergeSurvivor(metrics.rows);
        if (!preferred)
          throw new ToolError({ error: "merge_survivorship_unavailable", hint: "both party rows must be readable before a merge can run" });
        if (preferred.id !== surv.partyId)
          throw new ToolError({ error: "wrong_merge_survivor", required_survivor: preferred.id,
            selected_survivor: surv.partyId, precedence: "business ref, verified identity, linked records, oldest",
            hint: "the human gate confirmed the pair; use the deterministic survivor chosen from the record" });
        const sweep = await c.query(
          `/* merge_orphan_sweep */
         select 'party_link' as attachment, count(*)::int as count from party_link where from_party=$1 or to_party=$1 or via_party=$1
         union all select 'activity', count(*)::int from activity a
           where exists(select 1 from client cl where cl.id=a.client_id and cl.party_id=$1)
              or exists(select 1 from lead l where l.id=a.lead_id and l.party_id=$1)
              or exists(select 1 from vendor v where v.id=a.vendor_id and v.party_id=$1)
         union all select 'deal_participant', count(*)::int from deal_participant where party_id=$1
         union all select 'record_flag', count(*)::int from record_flag where subject_type='party' and subject_id=$1
         union all select 'child_party', count(*)::int from party where org_id=$1`, [merg.partyId]);

        // Moving role rows cannot preserve graph endpoints or the broker. Until
        // an attachment-preserving merge handles duplicate and self edges, refuse
        // this pair before any write instead of retiring a still-cited party.
        const graphCount = sweep.rows.find(row => row.attachment === "party_link")?.count;
        if (Number(graphCount) > 0)
          throw new ToolError({ error: "merge_graph_attachments_require_resolution",
            party_id: merg.partyId, count: Number(graphCount),
            hint: "the losing party has introduction links; preserve their endpoints and broker before confirming this merge" });

        // JOE'S RULING, in his words: "Okafor is a client now duh. everyone starts
        // as a lead." A lead record and a client record for the same person are
        // NOT a duplicate — every party enters as a lead and converts, and both
        // refs coexist by design.
        //
        // WHY THIS REFUSES RATHER THAN WARNS. Merging is destructive in a way that
        // does not undo: it retires the loser's ref permanently, and a lost ref is
        // never reissued, so every piece of doctrine quoting it goes dead and has
        // to be repointed by hand.
        //
        // WHY IT IS NOT A FLAT NO. The opposite case is real and this verb's own
        // history records it: Whitfield was two party rows for one human, one
        // carrying the lead and one the client, and merging them was correct. So
        // the gate refuses the merge whose only basis is that the names match, and
        // takes `same_person_because` as the evidence that it is that shape.
        const roleKinds = async (partyId) => {
          const r = await c.query(
            `/* role_kinds_for_party */
           select 'lead' as kind from lead where party_id=$1
           union all select 'client' from client where party_id=$1 and merged_into is null
           union all select 'vendor' from vendor where party_id=$1`, [partyId]);
          return new Set(r.rows.map(x => x.kind));
        };
        const [survRoles, mergRoles] = [await roleKinds(surv.partyId), await roleKinds(merg.partyId)];
        const only = (set, kind) => set.size === 1 && set.has(kind);
        // Symmetric on purpose: swapping the arguments must not slip past it.
        const isLeadClientPair =
          (only(survRoles, "client") && only(mergRoles, "lead")) ||
          (only(survRoles, "lead") && only(mergRoles, "client"));
        if (isLeadClientPair) {
          // A throwaway word is not a basis. The bar is length rather than a
          // vocabulary list because the failure being prevented is a session
          // typing "yes" to clear a gate, and any list of banned words is one
          // synonym from useless.
          const stated = String(args.same_person_because ?? "").trim();
          if (stated.length < 20)
            throw new ToolError({ error: "lead_client_pair",
              ruling: "Joe, on the Okafor record: \"Okafor is a client now duh. everyone starts as a lead.\"",
              why: "A lead record and a client record for the same person are not a duplicate. Every party " +
                   "enters as a lead and converts to a client; both refs coexist by design. Merging them " +
                   "retires one ref permanently, and a lost ref is never reissued.",
              hint: "If these really are TWO party rows for ONE human — the Whitfield shape — pass " +
                    "same_person_because with what establishes it (matching NPI, address, the intake " +
                    "record). Not that the names match." });
        }

        // THE ROLE ROWS MOVE WITH THE PERSON. Until 2026-08-02 this verb set merged_into and
        // nothing else, so the loser's lead/client/vendor rows were left pointing at a party
        // that no longer resolves — they vanished from every party-based view while still
        // existing. Three such orphans predated the fix, and merging Whitfield produced a
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
        // is a human call, and guessing is how the wrong Castillo got merged.
        const dup = await c.query(
          `select 'lead' k, count(*) n from lead where party_id=$1 having count(*)>1
         union all select 'client', count(*) from client where party_id=$1 and merged_into is null having count(*)>1
         union all select 'vendor', count(*) from vendor where party_id=$1 having count(*)>1`,
          [args.survivor_party]);

        // The stated basis rides the event: a merge is permanent, so the reason it
        // was allowed has to outlive the session that gave it.
        await writeEvent(c, actor, "confirm-merge", "party", args.merged_party,
          { new: { merged_into: args.survivor_party, roles_moved: moved, match_basis: basis,
                   survivorship: { business_ref: !!preferred.has_business_ref,
                     verified_identity_fields: Number(preferred.verified_identity_fields || 0),
                     linked_records: Number(preferred.linked_records || 0), created_at: preferred.created_at },
                   orphan_sweep: sweep.rows,
                   ...(args.same_person_because ? { same_person_because: args.same_person_because } : {}) },
            idempotency_key: args.idempotency_key });
        return { ok: true, roles_moved: moved, match_basis: basis, orphan_sweep: sweep.rows,
                 duplicate_roles_on_survivor: dup.rows.length ? dup.rows : undefined };
      }),
    },

  // [0069, loop #199] The case confirm-merge structurally cannot do: two VENDOR
    // rows riding ONE party. The 8/1-ruled Cromwell and Wexler merges executed at
    // party level and left exactly this behind (V-GC-001+V-GC-013, V-MKT-001+
    // V-MSC-024), and the build sweep found a third pair the loop never named
    // (T-004+T-040). Backlog #119/#120's "executed" claims were true-but-incomplete.
    "merge-vendor-rows": {
      discoveryOrder: 52,
      write: true,
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
          `select id, vendor_ref, party_id, merged_into, loan_programs, deal_evidence, deal_history_verified_at, trust_override, ${FIELDS.join(",")} from vendor where id = any($1) order by id for update`,
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
        const relationship = mergeRelationshipFields(surv, merg);
        Object.assign(filled, relationship.filled);
        conflicts.push(...relationship.conflicts);
        const fk = Object.keys(filled);
        if (fk.length) {
          const sets = fk.map((k, i) => `${k}=$${i + 2}`).join(", ");
          await c.query(`update vendor set ${sets}, updated_by=$1 where id=$${fk.length + 2}`,
            [actor.id, ...fk.map(k => k === "deal_evidence" ? JSON.stringify(filled[k]) : filled[k]), survId]);
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
  };
}
