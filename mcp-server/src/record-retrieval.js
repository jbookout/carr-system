// Bounded read-only evidence retrieval over existing doctrine and memory.
// This is a library seam, not a registered verb. It needs neither a migration
// nor a SCAC registry change. Call with the existing authenticated read client.
// No query log, sideWrite, answer generation or canonical mutation occurs here.

import { organizationTenantForActor } from "./identity.js";
import { normalizeSituationPhrase, retrievalVisibilityActorId } from "./situation-retrieval.js";
import { jevRerankPosture, rerankShortlist, JEV_RERANK_SHORTLIST_MAX } from "./jev-rerank.js";
import { ToolError } from "./tool-error.js";

const DOCTRINE_CLASSES = new Set(["playbook", "sop", "reference", "rule"]);
const MEMORY_KINDS = new Set(["preference", "fact", "episodic", "procedural"]);
const ARGUMENTS = new Set(["q", "limit"]);
const QUERY_MAX = 1000;
const AMBIGUITY_MARGIN = 0.1;

// Ordered admission: authority -> tenant/personal tier -> currentness ->
// visibility. Never let a score make an ineligible record a candidate.
// SQL enforces this before ranking; the same fail-closed checks defend the
// returned projection and support offline inspection, like situation retrieval.
export function eligibleRecord(row, scope) {
  if (!row || !scope) return false;
  if (row.record_type === "doctrine") {
    if (row.authority !== "governing" || !DOCTRINE_CLASSES.has(row.content_class)) return false;
  } else if (row.record_type === "memory") {
    if (row.authority !== "context" || !MEMORY_KINDS.has(row.content_class) || row.promoted !== true) return false;
  } else return false;
  if (!scope.tenant || row.organization_tenant_id !== scope.tenant) return false;
  if (row.scope !== "shared" && row.scope !== "personal") return false;
  if (row.scope === "personal" && (!scope.owner_actor_id || row.owner_actor_id !== scope.owner_actor_id)) return false;
  if (row.superseded !== false) return false;
  if (row.record_type === "doctrine") {
    if (row.status !== "active" || !row.revision_id || row.revision_id !== row.current_revision_id ||
        Number(row.version) < 1 || Number(row.version) !== Number(row.current_version)) return false;
  } else if (row.status !== "promoted") return false;
  if (row.visibility !== row.scope) return false;
  return true;
}

function score(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

export function rankRecordCandidates(rows, scope) {
  return rows.filter(row => eligibleRecord(row, scope))
    .map(row => ({ ...row, lexical_score: score(row.lexical_score), exact_match: row.exact_match === true }))
    .filter(row => row.exact_match || row.lexical_score > 0)
    .sort((a,b) => Number(b.exact_match) - Number(a.exact_match) || b.lexical_score - a.lexical_score ||
      a.record_id.localeCompare(b.record_id))
    .slice(0, JEV_RERANK_SHORTLIST_MAX);
}

// The materialized eligible CTE is the boundary: neither exact/lexical score
// computation, candidate cap nor semantic judgment reads rejected records.
// Doctrine belongs to the single server-owned tenant (no tenant column in its
// existing schema). Memory has an explicit tenant, plus promotion provenance.
// SUPERSEDES is a live edge, not a word in text; only current revision pointers
// are joined. Dossiers/distillations/indexes are not governing doctrine.
export const RECORD_RETRIEVAL_SQL = `
  with eligible as materialized (
    select s.id as record_id, 'doctrine'::text as record_type,
           'governing'::text as authority, $3::text as organization_tenant_id,
           d.visibility as scope, d.visibility, d.owner_actor_id, s.status,
           false as superseded, false as promoted, d.content_class,
           d.slug as doc_slug, s.section_key, coalesce(s.title,d.title) as title,
           left(rev.plain_text,600) as body, rev.id as revision_id,
           s.current_revision_id, rev.version, s.current_version, rev.content_hash,
           setweight(d.title_search_vector,'B') || setweight(s.title_search_vector,'A') ||
             setweight(rev.search_vector,'C') as vector
      from public.doctrine_section s
      join public.doctrine_document d on d.id=s.document_id
      join public.doctrine_revision rev on rev.id=s.current_revision_id
           and rev.section_id=s.id and rev.version=s.current_version
     where $3::text='carr-internal' and s.status='active' and s.current_version>0
       and d.content_class in ('playbook','sop','reference','rule')
       and (d.visibility='shared' or (d.visibility='personal' and d.owner_actor_id=$2::uuid))
       and not exists (
         select 1 from public.doctrine_edge e
          where e.target_section_id=s.id and e.edge_type='SUPERSEDES' and e.retired_by_revision_id is null
       )
    union all
    select m.id, 'memory'::text, 'context'::text, m.organization_tenant_id,
           m.scope, m.scope, m.owner_actor_id, m.status, false, true, m.kind,
           null::text, null::text, left(coalesce(m.context,m.statement),200),
           left(m.statement,600), null::uuid, null::uuid, m.version, m.version, null::text,
           m.search_vector
      from public.memory_item m
     where m.organization_tenant_id=$3::text and m.status='promoted'
       and m.kind in ('preference','fact','episodic','procedural')
       and m.promoted_by_actor_id is not null and m.promoted_at is not null
       and (m.scope='shared' or (m.scope='personal' and m.owner_actor_id=$2::uuid))
  ), query as (
    select lower(regexp_replace(btrim($1::text),'\\s+',' ','g')) as exact,
           to_tsquery('english',replace(plainto_tsquery('english',$1)::text,' & ',' | ')) as terms,
           tsvector_to_array(to_tsvector('english',$1)) as lexemes
  ), matches as (
    select e.*,
           coalesce((e.record_id::text=q.exact or
            lower(regexp_replace(btrim(e.title),'\\s+',' ','g'))=q.exact or
            (e.doc_slug || '#' || e.section_key)=q.exact),false) as exact_match,
           ts_rank(e.vector,q.terms) as lexical_score,
           coverage.matched_terms, cardinality(q.lexemes) as query_terms
      from eligible e cross join query q
      cross join lateral (
        select count(*) filter(where e.vector @@ to_tsquery('simple',quote_literal(term))) as matched_terms
          from unnest(q.lexemes) term
      ) coverage
     where (e.vector @@ q.terms and coverage.matched_terms*2 >= cardinality(q.lexemes)) or e.record_id::text=q.exact or
           lower(regexp_replace(btrim(e.title),'\\s+',' ','g'))=q.exact or
           (e.doc_slug || '#' || e.section_key)=q.exact
  )
  select record_id,record_type,authority,organization_tenant_id,scope,visibility,
         owner_actor_id,status,superseded,promoted,content_class,doc_slug,section_key,
         title,body,revision_id,current_revision_id,version,current_version,content_hash,
         exact_match,lexical_score,matched_terms,query_terms
    from matches order by exact_match desc,lexical_score desc,record_id limit $4`;

function argumentsFor(args) {
  const fields = args && typeof args === "object" && !Array.isArray(args) ? Object.keys(args) : [];
  const limit = args?.limit === undefined ? 5 : args.limit;
  if (!args || fields.some(key => !ARGUMENTS.has(key)) || typeof args.q !== "string" ||
      !args.q.trim() || args.q.length > QUERY_MAX || !Number.isInteger(limit) || limit < 1 || limit > 10)
    throw new ToolError({ error: "record_retrieval_argument_invalid",
      hint: "accepts only q (1..1000 characters) and limit (1..10); scope is authenticated" });
  return { q: args.q.trim(), limit };
}

function semanticQualification(candidates, posture) {
  if (!posture.enabled) return posture.posture === "misconfigured" ? "flag_invalid" : "flag_off";
  // Beam binds a pinned doctrine taxonomy. Mixed record knowledge does not;
  // use only the existing flat variants, never invent a second taxonomy.
  if (posture.mode === "beam") return "mixed_records_not_beam_qualified";
  if (candidates.length < 2) return "shortlist_too_small";
  if (candidates.some(row => row.exact_match)) return "exact_match";
  const top = candidates[0].lexical_score;
  if (!top || (top - candidates[1].lexical_score) / top > AMBIGUITY_MARGIN) return "lexical_margin";
  return null;
}

// options is server wiring, separate from closed caller arguments. In normal
// use no options enables Jev. Its existing flag must be explicitly configured,
// and its typed callback can reorder only this admitted shortlist.
export async function searchExistingRecords(c, actor, args, options = {}) {
  const { q, limit } = argumentsFor(args);
  const ownerId = await retrievalVisibilityActorId(c, actor);
  const tenant = organizationTenantForActor(actor);
  const scope = { tenant, owner_actor_id: ownerId };
  const result = await c.query(RECORD_RETRIEVAL_SQL, [q, ownerId, tenant, JEV_RERANK_SHORTLIST_MAX]);
  const candidates = rankRecordCandidates(result.rows, scope);
  const posture = jevRerankPosture(options.env);
  const reason = semanticQualification(candidates, posture);
  let semantic = { mode: posture.mode, judged: false, reason, model: null, requests: 0, usage: null };
  let ordered = candidates;
  if (!reason) {
    const judged = await rerankShortlist({ situation: normalizeSituationPhrase(q),
      candidates: candidates.map(row => ({ ...row, snippet: row.body })),
      variant: posture.mode, askJev: options.askJev });
    ordered = judged.order;
    semantic = { mode: judged.mode, judged: judged.judged, reason: judged.reason,
      model: judged.model, requests: judged.requests, usage: judged.usage };
  }
  const hits = ordered.slice(0,limit).map(({ snippet, ...row },index) => ({ ...row,
    provenance: { policy_id: "bounded-records-v1", rank: index+1,
      exact_match: row.exact_match, lexical_score: row.lexical_score,
      matched_terms: row.matched_terms, query_terms: row.query_terms,
      semantic_judged: semantic.judged, semantic_model: semantic.model } }));
  return { ok: true, query: q, hits, total: hits.length, semantic, generated_text: false };
}
