// The deliberately small Program 6 bridge: source a problem from the current
// deterministic situation index, capture it, and expose a safe read card. It
// does not own a lifecycle transition, an executor, or an approval.
import { searchDoctrineSituations } from "./situation-retrieval.js";
import { organizationTenantForActor } from "./identity.js";

const FIELDS = new Set(["idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"]);
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const CRITERION_ID = /^[A-Z][A-Z0-9-]{1,63}$/;

function text(value) { return typeof value === "string" ? value.trim() : ""; }

function validate(args, ToolError) {
  if (Object.keys(args).some(key => !FIELDS.has(key)))
    throw new ToolError({ error: "invalid_report_problem_fields" });
  if (!UUID.test(args.idempotency_key || "") || !text(args.situation) || text(args.situation).length > 1000 ||
      !text(args.title) || text(args.title).length > 200 || !text(args.desired_outcome) || text(args.desired_outcome).length > 2000 ||
      !Array.isArray(args.acceptance_criteria) || !args.acceptance_criteria.length || args.acceptance_criteria.length > 12)
    throw new ToolError({ error: "invalid_report_problem" });
  const ids = new Set();
  for (const criterion of args.acceptance_criteria) {
    if (!criterion || typeof criterion !== "object" || Object.keys(criterion).some(key => key !== "id" && key !== "text") ||
        !text(criterion.id) || !CRITERION_ID.test(criterion.id.trim()) || !text(criterion.text) || text(criterion.text).length > 500 ||
        ids.has(criterion.id.trim()))
      throw new ToolError({ error: "invalid_acceptance_criteria" });
    ids.add(criterion.id.trim());
  }
}

function sourceProjection(row) {
  return { label: row.doctrine_source_label || row.source_ref || null,
    freshness: row.source_current === false ? "stale" : "current",
    provenance: row.source_provenance || {
      doctrine_section_id: row.doctrine_section_id || null,
      doctrine_revision_id: row.doctrine_revision_id || null,
    } };
}

export function workRequestIntakeTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "report-problem": {
      write: true,
      description: "Capture one operational problem from the current deterministic situation source. It only creates a captured Work Request; it never triages, assigns, dispatches, approves, executes, or changes an existing request.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string", pattern: "^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$" },
        situation: { type: "string", minLength: 1, maxLength: 1000 }, title: { type: "string", minLength: 1, maxLength: 200 },
        desired_outcome: { type: "string", minLength: 1, maxLength: 2000 }, acceptance_criteria: { type: "array", minItems: 1, maxItems: 12,
          items: { type: "object", additionalProperties: false, required: ["id", "text"], properties: {
            id: { type: "string", minLength: 1, maxLength: 64 }, text: { type: "string", minLength: 1, maxLength: 500 },
          } } },
      }, additionalProperties: false, required: ["idempotency_key", "situation", "title", "desired_outcome", "acceptance_criteria"] },
      handler: async (c, actor, args) => {
        // Reject non-contract fields before the idempotency lock/read: first-call
        // serialization must not turn an invalid authority/source claim into I/O.
        validate(args, ToolError);
        // Bind this verb's replay hash to the authenticated actor. The shared
        // envelope predates actor-bound request hashes, so add the derived
        // identity only to its private hash input, never to the client schema.
        return withEnvelope(c, actor, "report-problem", { ...args, _server_actor_id: actor.id }, async () => {
        const retrieval = await searchDoctrineSituations(c, actor, { q: args.situation, limit: 20 });
        // Retrieval may legitimately include personal doctrine visible to this
        // actor. Program 6 cannot bind personal scope, so choose the first
        // ranked source that the database proves is shared, active, and current.
        const hits = retrieval.hits.filter(hit => hit.section_id);
        if (!hits.length) throw new ToolError({ error: "current_situation_source_not_found" });
        const shared = await c.query(
          `select ranked.section_id, s.current_revision_id
             from unnest($1::uuid[]) with ordinality as ranked(section_id, ordinal)
             join doctrine_section s on s.id=ranked.section_id
             join doctrine_document d on d.id=s.document_id
            where d.visibility='shared' and s.status='active' and s.current_revision_id is not null
            order by ranked.ordinal limit 1
             /* work-request-intake:highest-shared-source */`, [hits.map(hit => hit.section_id)]);
        const selected = shared.rows[0];
        const source = selected && hits.find(hit => hit.section_id === selected.section_id);
        if (!source || !selected.current_revision_id)
          throw new ToolError({ error: "current_situation_source_not_found" });
        const revisionId = selected.current_revision_id;
        const captured = await c.query(
          `select * from ops.capture_sourced_work_request($1::text, $2::text, $3::text,
             $4::jsonb, $5::uuid, $6::uuid, $7::uuid)
             /* work-request-intake:capture */`,
          [`doctrine:${source.doc_slug}#${source.section_key}`, args.title.trim(), args.desired_outcome.trim(),
            JSON.stringify(args.acceptance_criteria), source.section_id, revisionId, args.idempotency_key]);
        const row = captured.rows[0];
        if (!row) throw new ToolError({ error: "work_request_capture_refused" });
        await writeEvent(c, actor, "report-problem", "ops_work_request", row.id, {
          field: "state", new: { state: "captured", source_ref: `doctrine:${source.doc_slug}#${source.section_key}` }, idempotency_key: args.idempotency_key,
        });
        return { ok: true, human_ref: row.ref, state: row.state, version: Number(row.version),
          captured_at: row.captured_at || null, source: sourceProjection(row) };
        });
      },
    },
    "work-request-card": {
      write: false,
      description: "Read one same-tenant safe captured Work Request card. The card names the current or stale source and one human review label; it offers no executable actions.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        work_request: { type: "string", pattern: "^WR-[0-9]{1,12}$", minLength: 4, maxLength: 15 },
      }, required: ["work_request"] },
      handler: async (c, actor, args) => {
        const tenant = organizationTenantForActor(actor);
        const result = await c.query(
          `select * from ops.work_request_card($1::text, $2::text)
             /* work-request-intake:card */`, [args.work_request, tenant]);
        const row = result.rows[0];
        if (!row || row.state !== "captured") throw new ToolError({ error: "work_request_not_found" });
        return { ok: true, human_ref: row.ref, title: row.title, desired_outcome: row.desired_outcome,
          acceptance_criteria: row.acceptance_criteria, state: row.state, projection_state: "queued", source: sourceProjection(row),
          next_human_action: { label: "Review and triage", effect: "none" }, actions: [] };
      },
    },
  };
}
