// CARR-native learning memory kernel (Phase 1).
// Memory is evidence-backed context, never authority. Candidates can be
// observed by an agent; promotion remains an interactive human act. Personal
// scope is resolved from the verified sponsor, never from caller claims.

import { personalScopeForActor } from "./identity.js";

const KINDS = ["preference", "fact", "episodic", "procedural"];
const SCOPES = ["shared", "personal"];

function sponsorFor(actor) {
  const scope = personalScopeForActor(actor);
  if (scope.status === "error") throw new Error(scope.error);
  return scope;
}

function validText(value, field) {
  if (typeof value !== "string" || !value.trim())
    throw new ToolError({ error: "memory_field_required", field });
  return value.trim();
}

export function memoryTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "observe-memory": {
      write: true,
      description: "Record an evidence-backed memory candidate. Scope is shared or the authenticated partner's personal scope; actor and sponsor are server-derived. Observation never grants authority and does not promote the memory.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, kind: { type: "string", enum: KINDS },
        statement: { type: "string" }, context: { type: "string" }, scope: { type: "string", enum: SCOPES },
        evidence: { type: "object" }, confidence: { type: "number" },
      }, required: ["idempotency_key", "kind", "statement", "scope", "evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "observe-memory", args, async () => {
        if (!KINDS.includes(args.kind)) throw new ToolError({ error: "memory_kind_invalid", allowed: KINDS });
        if (!SCOPES.includes(args.scope)) throw new ToolError({ error: "memory_scope_invalid", allowed: SCOPES });
        const text = validText(args.statement, "statement");
        const evidence = args.evidence;
        if (!evidence || typeof evidence !== "object") throw new ToolError({ error: "memory_evidence_required" });
        const scope = sponsorFor(actor);
        const owner = args.scope === "personal" ? scope.sponsor : null;
        if (args.scope === "personal" && !owner)
          throw new ToolError({ error: "personal_memory_scope_unavailable", hint: "a personal memory requires a verified partner sponsor" });
        const confidence = args.confidence === undefined ? 0.5 : Number(args.confidence);
        if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)
          throw new ToolError({ error: "memory_confidence_invalid" });
        const item = await c.query(
          `insert into memory_item (kind, statement, context, scope, owner_actor_id, observed_by_actor_id, confidence)
           values ($1,$2,$3,$4,(select id from actor where slug=$5),$6,$7)
           returning id, kind, statement, context, scope, owner_actor_id, status, confidence, version, created_at`,
          [args.kind, text, args.context ? String(args.context).trim() : null, args.scope, owner,
           actor.id, confidence]);
        const row = item.rows[0];
        const ev = await c.query(
          `insert into memory_evidence (memory_id, source_type, source_ref, observation, human_quote, observed_by_actor_id, provenance)
           values ($1,$2,$3,$4,$5,$6,$7) returning id`,
          [row.id, validText(evidence.source_type, "evidence.source_type"), evidence.source_ref ? String(evidence.source_ref) : null,
           validText(evidence.observation, "evidence.observation"), evidence.human_quote ? String(evidence.human_quote) : null,
           actor.id, JSON.stringify(evidence.provenance || {})]);
        await writeEvent(c, actor, "observe-memory", "memory_item", row.id,
          { kind: row.kind, scope: row.scope, evidence_id: ev.rows[0].id, status: row.status });
        return { ok: true, memory: row, evidence_id: ev.rows[0].id };
      }),
    },

    "recall-memory": {
      description: "Recall promoted or candidate memories relevant to a context, combining shared memories with the authenticated partner's personal scope. Recalled memory is context only and never authority.",
      inputSchema: { type: "object", properties: {
        query: { type: "string" }, context: { type: "string" }, limit: { type: "integer" },
      }, required: ["query"] },
      handler: async (c, actor, args) => {
        const scope = sponsorFor(actor);
        const limit = Math.min(Math.max(Number(args.limit || 20), 1), 100);
        const r = await c.query(
          `select id, kind, statement, context, scope, confidence, status, version, created_at,
                  ts_rank(search_vector, plainto_tsquery('english',$1)) as relevance
             from memory_item
            where status in ('candidate','promoted')
              and (scope='shared' or (scope='personal' and owner_actor_id=(select id from actor where slug=$2)))
              and search_vector @@ plainto_tsquery('english',$1)
            order by relevance desc, confidence desc, created_at desc limit $3`,
          [validText(args.query, "query"), scope.sponsor, limit]);
        return { ok: true, query: args.query, context: args.context || null, memories: r.rows, count: r.rows.length };
      },
    },

    "promote-memory": {
      write: true, humanOnly: true,
      description: "Promote one candidate memory after a human confirms it. Requires a fresh memory version; promotion changes recall eligibility, never authority.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, memory_id: { type: "string" }, base_version: { type: "integer" }, reason: { type: "string" },
      }, required: ["idempotency_key", "memory_id", "base_version", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "promote-memory", args, async () => {
        const r = await c.query(
          `update memory_item set status='promoted', version=version+1, promoted_by_actor_id=$3, promoted_at=now(), updated_at=now()
             where id=$1 and version=$2 and status='candidate'
           returning id, status, version`, [args.memory_id, args.base_version, actor.id]);
        if (!r.rows.length) throw new ToolError({ error: "memory_version_conflict_or_not_candidate", hint: "read the memory again; promotion requires candidate status and the current version" });
        await writeEvent(c, actor, "promote-memory", "memory_item", args.memory_id, { reason: args.reason, status: "promoted" });
        return { ok: true, memory: r.rows[0] };
      }),
    },

    "correct-memory": {
      write: true,
      description: "Correct a memory without rewriting history. The prior row becomes corrected and a new version carries the replacement statement.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, memory_id: { type: "string" }, base_version: { type: "integer" }, statement: { type: "string" }, reason: { type: "string" },
      }, required: ["idempotency_key", "memory_id", "base_version", "statement", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "correct-memory", args, async () => {
        const text = validText(args.statement, "statement");
        const r = await c.query(
          `update memory_item set status='corrected', corrected_by_actor_id=$3, correction_reason=$4, corrected_at=now(), statement=$5, version=version+1, updated_at=now()
             where id=$1 and version=$2 and status in ('candidate','promoted') returning id, status, statement, version`,
          [args.memory_id, args.base_version, actor.id, validText(args.reason, "reason"), text]);
        if (!r.rows.length) throw new ToolError({ error: "memory_version_conflict" });
        await writeEvent(c, actor, "correct-memory", "memory_item", args.memory_id, { reason: args.reason, statement: text });
        return { ok: true, memory: r.rows[0] };
      }),
    },

    "forget-memory": {
      write: true,
      description: "Forget a memory by suppressing it from recall while retaining its evidence and audit history. This is not a DELETE.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, memory_id: { type: "string" }, base_version: { type: "integer" }, reason: { type: "string" },
      }, required: ["idempotency_key", "memory_id", "base_version", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "forget-memory", args, async () => {
        const r = await c.query(
          `update memory_item set status='forgotten', forgotten_by_actor_id=$3, forget_reason=$4, forgotten_at=now(), version=version+1, updated_at=now()
             where id=$1 and version=$2 and status in ('candidate','promoted','corrected') returning id, status, version`,
          [args.memory_id, args.base_version, actor.id, validText(args.reason, "reason")]);
        if (!r.rows.length) throw new ToolError({ error: "memory_version_conflict" });
        await writeEvent(c, actor, "forget-memory", "memory_item", args.memory_id, { reason: args.reason });
        return { ok: true, memory: r.rows[0] };
      }),
    },
  };
}
