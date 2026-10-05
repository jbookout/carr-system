import { ToolError } from "./tool-error.js";
import { UUID_RE } from "./verb-support.js";
import { withEnvelope, writeEvent } from "./versioned-write.js";
import { organizationTenantForActor } from "./identity.js";

// [ORDER 34 review, blocker 1] The old array-replacer form of JSON.stringify
// FILTERED nested keys instead of canonicalizing them — links[]/building/spaces
// payloads hashed as empty, so a corrected retry under a reused key replayed
// stale data silently. canon() deep-sorts instead. For FLAT args (every
// historical call that replays, incl. the frozen smoke probes) the output
// string — sorted top-level keys — is byte-identical to the old form, so
// stored hashes stay valid. A historical NESTED-args row would key_reuse
// loudly on replay rather than lie quietly; that trade is deliberate.
const INDUSTRY_EVENT_KINDS = ["conference", "association_meeting", "trade_show", "networking"];

const INDUSTRY_EVENT_ATTENDANCE_INTENTS = ["considering", "plan_to_attend", "not_attending"];

const INDUSTRY_EVENT_STATUSES = ["planned", "attended", "skipped", "cancelled"];

const PARTNER_SLUGS = ["joe", "dell"];

function industryEventText(value, field, { optional = false, max = 1000 } = {}) {
  if ((value === undefined || value === null || value === "") && optional) return null;
  if (typeof value !== "string" || !value.trim() || value.trim().length > max)
    throw new ToolError({ error: "industry_event_text_invalid", field });
  return value.trim();
}

function industryEventTimestamp(value, field) {
  const text = industryEventText(value, field, { max: 80 });
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(text) || Number.isNaN(Date.parse(text)))
    throw new ToolError({ error: "industry_event_timestamp_invalid", field,
      hint: "send an RFC3339 timestamp with an explicit timezone offset" });
  return text;
}

function industryEventUrl(value) {
  if (value === undefined || value === null || value === "") return null;
  let url;
  try { url = new URL(String(value)); } catch {
    throw new ToolError({ error: "industry_event_url_invalid" });
  }
  if (!/^https?:$/.test(url.protocol))
    throw new ToolError({ error: "industry_event_url_invalid", hint: "use an http or https URL" });
  return url.toString();
}

function industryEventValue(value, field, allowed) {
  if (!allowed.includes(value))
    throw new ToolError({ error: "industry_event_value_invalid", field, allowed });
  return value;
}

function validateIndustryEventFields(args, { partial = false } = {}) {
  const required = partial ? [] : ["title", "organizer", "kind", "starts_at", "ends_at", "source"];
  for (const field of required) industryEventText(args[field], field);
  const clean = {};
  if (!partial || Object.hasOwn(args, "title")) clean.title = industryEventText(args.title, "title", { max: 500 });
  if (!partial || Object.hasOwn(args, "organizer")) clean.organizer = industryEventText(args.organizer, "organizer", { max: 500 });
  if (!partial || Object.hasOwn(args, "kind")) clean.kind = industryEventValue(args.kind, "kind", INDUSTRY_EVENT_KINDS);
  if (!partial || Object.hasOwn(args, "starts_at")) clean.starts_at = industryEventTimestamp(args.starts_at, "starts_at");
  if (!partial || Object.hasOwn(args, "ends_at")) clean.ends_at = industryEventTimestamp(args.ends_at, "ends_at");
  if (clean.starts_at && clean.ends_at && Date.parse(clean.ends_at) <= Date.parse(clean.starts_at))
    throw new ToolError({ error: "industry_event_time_order_invalid", hint: "ends_at must be after starts_at" });
  if (!partial || Object.hasOwn(args, "location")) clean.location = industryEventText(args.location, "location", { optional: true, max: 500 });
  if (!partial || Object.hasOwn(args, "is_virtual")) {
    if (args.is_virtual !== undefined && typeof args.is_virtual !== "boolean")
      throw new ToolError({ error: "industry_event_virtual_invalid" });
    clean.is_virtual = args.is_virtual === undefined ? false : args.is_virtual;
  }
  if (!partial || Object.hasOwn(args, "url")) clean.url = industryEventUrl(args.url);
  if (!partial || Object.hasOwn(args, "relevance_note")) clean.relevance_note = industryEventText(args.relevance_note, "relevance_note", { optional: true, max: 2000 });
  if (!partial || Object.hasOwn(args, "attendance_intent"))
    clean.attendance_intent = args.attendance_intent === undefined ? "considering" : industryEventValue(args.attendance_intent, "attendance_intent", INDUSTRY_EVENT_ATTENDANCE_INTENTS);
  if (!partial || Object.hasOwn(args, "owner_partner")) clean.owner_partner = industryEventValue(args.owner_partner, "owner_partner", PARTNER_SLUGS);
  if (!partial || Object.hasOwn(args, "status"))
    clean.status = args.status === undefined ? "planned" : industryEventValue(args.status, "status", INDUSTRY_EVENT_STATUSES);
  if (!partial || Object.hasOwn(args, "source")) clean.source = industryEventText(args.source, "source", { max: 2000 });
  return clean;
}

function industryEventId(value) {
  if (typeof value !== "string" || !UUID_RE.test(value))
    throw new ToolError({ error: "industry_event_id_invalid" });
  return value;
}

export function industryEventTools() {
  return {
    "add-industry-event": {
      discoveryOrder: 83,
      write: true,
      description: "Record a healthcare CRE industry event such as a conference, association meeting, trade show, or networking event. Timestamps must include their timezone; source and the owner partner are required. Tenant is server-derived.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, title: { type: "string" }, organizer: { type: "string" },
        kind: { type: "string", enum: INDUSTRY_EVENT_KINDS }, starts_at: { type: "string" },
        ends_at: { type: "string" }, location: { type: ["string", "null"] }, is_virtual: { type: "boolean" },
        url: { type: ["string", "null"] }, relevance_note: { type: ["string", "null"] },
        attendance_intent: { type: "string", enum: INDUSTRY_EVENT_ATTENDANCE_INTENTS },
        owner_partner: { type: "string", enum: PARTNER_SLUGS },
        status: { type: "string", enum: INDUSTRY_EVENT_STATUSES }, source: { type: "string" },
      }, required: ["idempotency_key", "title", "organizer", "kind", "starts_at", "ends_at", "owner_partner", "source"] },
      handler: async (c, actor, args) => {
        const clean = validateIndustryEventFields(args);
        return withEnvelope(c, actor, "add-industry-event", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const inserted = await c.query(
            `insert into industry_event
             (organization_tenant_id,title,organizer,kind,starts_at,ends_at,location,is_virtual,url,
              relevance_note,attendance_intent,owner_partner,status,source,created_by_actor_id,updated_by_actor_id)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$15)
           returning id, organization_tenant_id, title, organizer, kind, starts_at, ends_at, location,
                     is_virtual, url, relevance_note, attendance_intent, owner_partner, status, source,
                     version, created_at, updated_at`,
            [tenant, clean.title, clean.organizer, clean.kind, clean.starts_at, clean.ends_at,
             clean.location, clean.is_virtual, clean.url, clean.relevance_note, clean.attendance_intent,
             clean.owner_partner, clean.status, clean.source, actor.id]);
          const event = inserted.rows[0];
          await writeEvent(c, actor, "add-industry-event", "industry_event", event.id,
            { new: event, idempotency_key: args.idempotency_key });
          return { ok: true, event };
        });
      },
    },

    "list-industry-events": {
      discoveryOrder: 84,
      write: false,
      description: "List tenant-scoped healthcare CRE industry events for the Events tab, ordered by start time. Returns the current version needed for a later update.",
      inputSchema: { type: "object", properties: { limit: { type: "integer" } } },
      handler: async (c, actor, args) => {
        const limit = args.limit === undefined ? 50 : args.limit;
        if (!Number.isInteger(limit) || limit < 1 || limit > 100)
          throw new ToolError({ error: "industry_event_limit_invalid", hint: "limit must be an integer from 1 through 100" });
        const result = await c.query(
          `select id, organization_tenant_id, title, organizer, kind, starts_at, ends_at, location,
                is_virtual, url, relevance_note, attendance_intent, owner_partner, status, source,
                version, created_at, updated_at
           from industry_event
          where organization_tenant_id=$1
          order by starts_at, id limit $2`, [organizationTenantForActor(actor), limit]);
        return { ok: true, events: result.rows, count: result.rows.length };
      },
    },

    "update-industry-event": {
      discoveryOrder: 85,
      write: true,
      description: "Update a tenant-scoped healthcare CRE industry event with optimistic concurrency. Send the version returned by list-industry-events as base_version; stale updates refuse without changing the row.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, event_id: { type: "string" }, base_version: { type: "integer" },
        title: { type: "string" }, organizer: { type: "string" }, kind: { type: "string", enum: INDUSTRY_EVENT_KINDS },
        starts_at: { type: "string" }, ends_at: { type: "string" }, location: { type: ["string", "null"] },
        is_virtual: { type: "boolean" }, url: { type: ["string", "null"] }, relevance_note: { type: ["string", "null"] },
        attendance_intent: { type: "string", enum: INDUSTRY_EVENT_ATTENDANCE_INTENTS },
        owner_partner: { type: "string", enum: PARTNER_SLUGS }, status: { type: "string", enum: INDUSTRY_EVENT_STATUSES },
        source: { type: "string" },
      }, required: ["idempotency_key", "event_id", "base_version"] },
      handler: async (c, actor, args) => {
        const eventId = industryEventId(args.event_id);
        if (!Number.isInteger(args.base_version) || args.base_version < 1)
          throw new ToolError({ error: "industry_event_base_version_invalid" });
        const clean = validateIndustryEventFields(args, { partial: true });
        const keys = ["title", "organizer", "kind", "starts_at", "ends_at", "location", "is_virtual",
          "url", "relevance_note", "attendance_intent", "owner_partner", "status", "source"]
          .filter(key => Object.hasOwn(clean, key));
        if (!keys.length)
          throw new ToolError({ error: "industry_event_update_empty" });
        return withEnvelope(c, actor, "update-industry-event", args, async () => {
          const tenant = organizationTenantForActor(actor);
          const values = keys.map(key => clean[key]);
          const actorParam = values.length + 1;
          const idParam = values.length + 2;
          const tenantParam = values.length + 3;
          const versionParam = values.length + 4;
          const set = keys.map((key, index) => `${key}=$${index + 1}`);
          set.push(`updated_by_actor_id=$${actorParam}`, "updated_at=now()", "version=version+1");
          const result = await c.query(
            `update industry_event set ${set.join(",")}
             where id=$${idParam} and organization_tenant_id=$${tenantParam} and version=$${versionParam}
           returning id, organization_tenant_id, title, organizer, kind, starts_at, ends_at, location,
                     is_virtual, url, relevance_note, attendance_intent, owner_partner, status, source,
                     version, created_at, updated_at`,
            [...values, actor.id, eventId, tenant, args.base_version]);
          if (!result.rows.length)
            throw new ToolError({ error: "industry_event_version_conflict",
              hint: "read the event again and retry with its current version" });
          const event = result.rows[0];
          await writeEvent(c, actor, "update-industry-event", "industry_event", event.id,
            { new: event, idempotency_key: args.idempotency_key });
          return { ok: true, event };
        });
      },
    },
  };
}
