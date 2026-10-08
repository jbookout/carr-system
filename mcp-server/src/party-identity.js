// CORRECT-PARTY-IDENTITY (Joe, 2026-10-08, about contact enrichment: "dont
// propose corrections, just make them"; rule 578fdd91). update-party-contact
// writes contact facts and refuses identity on purpose. This is the identity
// door: a party's name, its firm (org) and its state, from a verified finding.
//
// Three properties carry the rule's trade-off (an occasional wrong edit that is
// cheap to undo, over a pile of proposals nobody applies):
//   - source is required, and every changed field writes an event holding the
//     prior value, so any correction can be reversed from the record;
//   - base_version guards the party exactly as update-party-contact does;
//   - an org change follows rule 8cddc6ad. A shared org row is never renamed,
//     because renaming it would move every other person on it to a firm they
//     may not work for. Only the target is re-pointed, and the others are read
//     back afterwards; if any moved, the call refuses and the transaction rolls
//     back.
import { ToolError } from "./tool-error.js";

export const PARTY_IDENTITY_FIELDS = Object.freeze(["name", "org", "state"]);

const US_STATES = new Set(("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN " +
  "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU").split(" "));

// Rule 54e2bcb9: a CARR agent's own line or a carr.us address standing in for a
// value nobody had. The same guard update-party-contact applies to contact fields.
function isPlaceholder(value) {
  return /@carr\.us\b/i.test(value) || value.replace(/\D/g, "").includes("2056436555");
}

export function normalizeIdentityFields(fields) {
  const keys = Object.keys(fields || {}).filter(k => PARTY_IDENTITY_FIELDS.includes(k));
  if (!keys.length) throw new ToolError({ error: "no_updatable_fields", allowed: PARTY_IDENTITY_FIELDS,
    hint: "identity fields only (name, org, state); contact facts go through update-party-contact" });
  const clean = {};
  for (const k of keys) {
    const value = typeof fields[k] === "string" ? fields[k].trim().replace(/\s+/g, " ") : "";
    if (k === "state") {
      const code = value.toUpperCase();
      if (!US_STATES.has(code)) throw new ToolError({ error: "invalid_state", got: fields[k],
        hint: "a two-letter US state code such as FL or AL" });
      clean.state = code;
      continue;
    }
    if (!value || value.length > 200) throw new ToolError({ error: `invalid_${k}`, got: fields[k] ?? null,
      hint: `${k} must be a non-empty name of at most 200 characters` });
    if (isPlaceholder(value)) throw new ToolError({ error: "placeholder_value", field: k,
      hint: "a CARR agent's own number or a carr.us address is a placeholder, never data" });
    clean[k] = value;
  }
  return clean;
}

const same = (a, b) => String(a ?? "").trim().toLowerCase() === String(b ?? "").trim().toLowerCase();

// Rule 8cddc6ad as a pure decision. Order matters:
//   1. the firm is already right            -> unchanged
//   2. a live org already carries the name  -> re-point to it (never mint a twin)
//   3. only the target rides the old row    -> rename the row in place
//   4. the row is shared, or there is none  -> mint a new org, re-point the target
export function planOrgCorrection({ currentOrg, newName, othersOnOrg, existing }) {
  if (currentOrg && currentOrg.name === newName) return { mode: "unchanged", org_id: currentOrg.id };
  const matches = existing.filter(o => same(o.name, newName) && o.id !== currentOrg?.id);
  if (matches.length > 1) throw new ToolError({ error: "org_ambiguous", candidates: matches,
    hint: "several live orgs carry that name; merge them first, then correct this party" });
  if (matches.length === 1) return { mode: "repoint_existing", org_id: matches[0].id };
  if (currentOrg && othersOnOrg.length === 0) return { mode: "rename_in_place", org_id: currentOrg.id };
  return { mode: "mint_and_repoint" };
}

export function partyIdentityTools({ withEnvelope, writeEvent, versionGuard, resolvePartyForWrite }) {
  return {
    "correct-party-identity": {
      write: true,
      description: "Correct a party's IDENTITY from a verified finding: name spelling, firm (org) and state. The companion to update-party-contact, which handles contact facts only. Per rule 578fdd91 enrichment applies its corrections rather than parking them as proposals, but only when identity is confirmed (high or medium confidence with a second corroborating field) and the value is one clean value from a re-verifiable source. source is REQUIRED, and every changed field writes an event with its prior value, so each correction can be undone. base_version is the PARTY's version from a fresh read. ORG CHANGES FOLLOW RULE 8cddc6ad: a shared org row is never renamed. If a live org already carries the corrected name the party is re-pointed to it; if only this party rides the current org row, that row is renamed in place; otherwise a new org is minted and only this party is re-pointed. The other parties on the old org are read back and returned as `untouched`. Placeholder guard: a CARR agent's own number or a carr.us address is refused.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        party: { type: "string", description: "P-#### ref, a role ref (V-/C-/L-/T-), or a name" },
        base_version: { type: "integer" },
        fields: { type: "object", additionalProperties: false, properties: {
          name: { type: "string", description: "the party's corrected name" },
          org: { type: "string", description: "the corrected firm name, for a person" },
          state: { type: "string", description: "two-letter US state code" } } },
        source: { type: "string", description: "where the correction came from: 'record-finding <kind> observed <date> <url>', a registry and identifier, a firm website" } },
        required: ["idempotency_key", "party", "base_version", "fields", "source"] },
      handler: async (c, actor, args) => {
        const source = typeof args.source === "string" ? args.source.trim() : "";
        if (!source) throw new ToolError({ error: "missing_source",
          hint: "an identity correction without provenance cannot be checked or undone; say where it came from" });
        const fields = normalizeIdentityFields(args.fields);
        return withEnvelope(c, actor, "correct-party-identity", args, async () => {
          const { partyId, hopped } = await resolvePartyForWrite(c, args.party);
          await versionGuard(c, "party", partyId, args.base_version);
          const before = (await c.query(
            `select p.kind, p.name, p.state, p.org_id, o.name as org_name from party p
               left join party o on o.id=p.org_id where p.id=$1`, [partyId])).rows[0];
          if (!before) throw new ToolError({ error: "not_found", table: "party", id: partyId });
          if (fields.org !== undefined && before.kind !== "person")
            throw new ToolError({ error: "org_on_org_party",
              hint: "an org party has no firm; correct its own name with fields.name" });

          const event = (subjectId, field, oldValue, newValue, extra = {}) =>
            writeEvent(c, actor, "correct-party-identity", "party", subjectId, {
              field, old: { [field]: oldValue }, new: { [field]: newValue, ...extra },
              agent_rationale: `source: ${source}`, idempotency_key: args.idempotency_key });
          const updated = [];
          for (const k of ["name", "state"]) {
            if (fields[k] === undefined || fields[k] === before[k]) continue;
            await c.query(`update party set ${k}=$1, updated_by=$2 where id=$3`, [fields[k], actor.id, partyId]);
            await event(partyId, k, before[k], fields[k]);
            updated.push(k);
          }

          let org;
          if (fields.org !== undefined) {
            const currentOrg = before.org_id ? { id: before.org_id, name: before.org_name } : null;
            // Lock the org row BEFORE counting: attaching a party to it takes a
            // FOR KEY SHARE lock on this row, so no one can join between the
            // count and a rename in place.
            if (currentOrg) await c.query("select id from party where id=$1 for update", [currentOrg.id]);
            const othersOnOrg = currentOrg ? (await c.query(
              `select id, name from party where org_id=$1 and id<>$2
                  and merged_into is null and deleted_at is null order by name`,
              [currentOrg.id, partyId])).rows : [];
            const existing = (await c.query(
              `select id, name from party where kind='org' and merged_into is null
                  and deleted_at is null and lower(name)=lower($1)`, [fields.org])).rows;
            const plan = planOrgCorrection({ currentOrg, newName: fields.org, othersOnOrg, existing });
            org = { mode: plan.mode, from: currentOrg, attached_before: othersOnOrg.length + 1 };
            if (plan.mode === "rename_in_place") {
              await c.query("update party set name=$1, updated_by=$2 where id=$3",
                [fields.org, actor.id, plan.org_id]);
              await event(plan.org_id, "name", currentOrg.name, fields.org, { renamed_for: partyId });
              org.org_id = plan.org_id;
            } else if (plan.mode === "repoint_existing" || plan.mode === "mint_and_repoint") {
              const orgId = plan.org_id ?? (await c.query(
                "insert into party (kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id",
                [fields.org, actor.id])).rows[0].id;
              if (!plan.org_id) await event(orgId, "name", null, fields.org, { minted_for: partyId });
              await c.query("update party set org_id=$1, updated_by=$2 where id=$3", [orgId, actor.id, partyId]);
              await event(partyId, "org_id", currentOrg ? `${currentOrg.id} (${currentOrg.name})` : null,
                `${orgId} (${fields.org})`, { mode: plan.mode });
              org.org_id = orgId;
            } else {
              org.org_id = plan.org_id;
            }
            if (plan.mode !== "unchanged") updated.push("org");
            // Rule 8cddc6ad step 4: name the untouched parties and prove they
            // still read what they read before.
            if (othersOnOrg.length) {
              const after = new Map((await c.query(
                "select id, org_id from party where id = any($1::uuid[])",
                [othersOnOrg.map(p => p.id)])).rows.map(r => [r.id, r.org_id]));
              const orgName = (await c.query("select name from party where id=$1", [currentOrg.id])).rows[0]?.name;
              const moved = othersOnOrg.filter(p => after.get(p.id) !== currentOrg.id);
              if (moved.length || orgName !== currentOrg.name)
                throw new ToolError({ error: "untouched_party_moved", moved, org_name_now: orgName ?? null });
              org.untouched = othersOnOrg.map(p => ({ id: p.id, name: p.name, org_id: currentOrg.id }));
            }
          }
          return { ok: true, party_id: partyId, updated, ...(org ? { org } : {}),
            hopped_to_survivor: hopped || undefined };
        });
      },
    },
  };
}
