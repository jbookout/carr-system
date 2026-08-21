-- 0223_retrieval_read_path_has_no_write.sql
-- A READ VERB MUST NOT WRITE.
--
-- search-doctrine is registered write:false, so mcp.js runs it on the READER
-- connection. But search_doctrine_situations carried a data-modifying CTE
-- (insert into retrieval_query_log) inside its final statement, which made the
-- whole read a write. Called where writes are refused, the entire call dies:
--
--   ERROR:  cannot execute SELECT in a read-only transaction
--   CONTEXT:  SQL function "search_doctrine_situations" statement 1
--
-- That is a plain Postgres error rather than a typed one, so mcp.js flattens it
-- to the literal "internal error". Doctrine search returned exactly that for
-- every query while read-doctrine and doctrine-index kept working, because
-- neither of those writes. Reproduced independently by two sessions.
-- retrieval_query_log's newest row bounds it: a row only survives a fully
-- successful call, so search last worked at 2026-08-21 01:46:53Z.
--
-- THE SPLIT. The ranker becomes what it always claimed to be: STABLE, and free
-- of writes, so it cannot fail on a read-only endpoint, a moved pgcrypto, or a
-- privilege it should never have needed. Logging moves to log_retrieval_query,
-- which the Worker calls on the WRITE credential and never awaits — the same
-- shape recordReadCall already uses for read-call records. Losing a log row now
-- costs a log row instead of the answer.
--
-- digest() moves with the insert deliberately: if pgcrypto is ever absent from
-- the pinned search_path, only the logging fails, and the search still returns.

begin;

create or replace function search_doctrine_situations(
  p_query text, p_actor_id uuid, p_content_classes text[] default null,
  p_limit integer default 20, p_policy_id text default null
) returns table (
  section_id uuid, section_key text, title text, doc_slug text,
  content_class text, rank double precision, snippet text,
  lexical_score double precision, concept_score double precision,
  final_score double precision, provenance jsonb
) language sql stable security definer set search_path = public, pg_temp as $$
with
  normalized as materialized (
    select normalize_retrieval_phrase(p_query) as q
  ),
  policy as materialized (
    select * from retrieval_ranking_policy
     where policy_id = coalesce(
       p_policy_id,
       (select policy_id from retrieval_ranking_policy where is_default and status='active'))
       and status in ('candidate','active')
  ),
  query_terms as materialized (
    select websearch_to_tsquery('english', q) as tsq from normalized
  ),
  current_set as materialized (
    select * from current_retrievable_doctrine(p_actor_id, p_content_classes)
  ),
  lexical_raw as (
    select c.section_id,
           ts_rank_cd(
             setweight(c.section_title_vector, 'A') ||
             setweight(c.document_title_vector, 'A') ||
             setweight(c.body_search_vector, 'B'), q.tsq) as raw_score,
           ts_headline('english', c.plain_text, q.tsq, 'MaxWords=25, MinWords=10') as snippet
      from current_set c cross join query_terms q
     where c.section_title_vector @@ q.tsq
        or c.document_title_vector @@ q.tsq
        or c.body_search_vector @@ q.tsq
  ),
  phrase_match as (
    select p.id as phrase_id, p.concept_id, p.weight as phrase_weight,
           case p.match_mode
             when 'exact' then case when p.normalized_phrase = n.q then 1.0 else 0.0 end
             when 'fts' then case
               when to_tsvector('english', p.normalized_phrase) @@ websearch_to_tsquery('english', n.q)
               then least(1.0, ts_rank_cd(to_tsvector('english', p.normalized_phrase),
                                         websearch_to_tsquery('english', n.q))::double precision)
               else 0.0 end
             when 'trgm' then case when similarity(p.normalized_phrase, n.q) >= p.min_similarity
               then similarity(p.normalized_phrase, n.q) else 0.0 end
           end::double precision as phrase_strength
      from retrieval_phrase p cross join normalized n
     where p.status = 'approved'
  ),
  concept_evidence as (
    select m.section_id, pm.phrase_id, pm.concept_id, m.id as mapping_id,
           pm.phrase_strength, pm.phrase_weight::double precision,
           m.weight::double precision as mapping_weight,
           (pm.phrase_strength * pm.phrase_weight * m.weight)::double precision as contribution
      from phrase_match pm
      join retrieval_concept c on c.id = pm.concept_id and c.status = 'approved'
      join doctrine_concept_mapping m on m.concept_id = c.id and m.status = 'approved'
      join current_set visible on visible.section_id = m.section_id
     where pm.phrase_strength > 0
  ),
  concept_by_section as (
    select section_id, max(contribution)::double precision as concept_score,
           array_agg(distinct phrase_id order by phrase_id) as phrase_ids,
           array_agg(distinct concept_id order by concept_id) as concept_ids,
           array_agg(distinct mapping_id order by mapping_id) as mapping_ids
      from concept_evidence group by section_id
  ),
  unioned as (
    select c.*, coalesce(least(1.0, l.raw_score), 0)::double precision as lexical_score,
           coalesce(least(1.0, ce.concept_score), 0)::double precision as concept_score,
           l.snippet, coalesce(ce.phrase_ids, '{}') as phrase_ids,
           coalesce(ce.concept_ids, '{}') as concept_ids,
           coalesce(ce.mapping_ids, '{}') as mapping_ids
      from current_set c
      left join lexical_raw l on l.section_id = c.section_id
      left join concept_by_section ce on ce.section_id = c.section_id
     where l.section_id is not null or ce.section_id is not null
  ),
  maxima as (
    select greatest(max(lexical_score), 0) as max_lexical,
           greatest(max(concept_score), 0) as max_concept from unioned
  ),
  scored as (
    select u.*,
           case p.formula
             when 'weighted_sum' then
               ((p.config->>'lexical_weight')::double precision * u.lexical_score +
                (p.config->>'concept_weight')::double precision *
                  case when coalesce((p.config->>'concept_enabled')::boolean,true)
                       then u.concept_score else 0 end)
             when 'coequal_normalized' then
               (case when x.max_lexical > 0 then u.lexical_score / x.max_lexical else 0 end) +
               (case when coalesce((p.config->>'concept_enabled')::boolean,true) and x.max_concept > 0
                     then u.concept_score / x.max_concept else 0 end) +
               (case when coalesce((p.config->>'concept_enabled')::boolean,true)
                           and u.lexical_score > 0 and u.concept_score > 0
                     then (p.config->>'dual_evidence_bonus')::double precision else 0 end)
           end::double precision as final_score,
           p.policy_id, p.version as policy_version
      from unioned u cross join maxima x cross join policy p
  ),
  limited as materialized (
    select * from scored
     where final_score > 0
     order by final_score desc, concept_score desc, lexical_score desc, section_key asc
     limit greatest(1, least(coalesce(p_limit, 20), 100))
  )
select l.section_id, l.section_key, l.section_title, l.doc_slug, l.content_class,
       l.final_score as rank,
       coalesce(l.snippet, left(l.plain_text, 240)) as snippet,
       l.lexical_score, l.concept_score, l.final_score,
       jsonb_build_object(
         'complete', true, 'policy_id', l.policy_id, 'policy_version', l.policy_version,
         'lexical_score', l.lexical_score, 'concept_score', l.concept_score,
         'final_score', l.final_score, 'phrase_ids', to_jsonb(l.phrase_ids),
         'concept_ids', to_jsonb(l.concept_ids), 'mapping_ids', to_jsonb(l.mapping_ids))
  from limited l
 order by l.final_score desc, l.concept_score desc, l.lexical_score desc, l.section_key asc
$$;
-- The write half, on its own, for the write credential. Same columns, same
-- hash, same scope; computes the normalized hash internally so no caller has to
-- reproduce normalize_retrieval_phrase or reach for pgcrypto itself.
create or replace function log_retrieval_query(
  p_query text, p_result_count integer, p_section_ids uuid[],
  p_score_bands jsonb, p_policy_id text, p_policy_version bigint,
  p_explicit_hit boolean, p_scope_ref text default 'carr-internal'
) returns uuid
language sql volatile security definer set search_path = public, pg_temp as $$
  insert into retrieval_query_log
    (normalized_hash, result_count, score_bands, selected_row_ids,
     policy_id, policy_version, explicit_hit, scope_ref)
  values (
    encode(digest(convert_to(normalize_retrieval_phrase(p_query), 'UTF8'), 'sha256'), 'hex'),
    greatest(coalesce(p_result_count, 0), 0),
    coalesce(p_score_bands, jsonb_build_object('high', 0, 'medium', 0, 'low', 0)),
    coalesce(p_section_ids, '{}'),
    p_policy_id, p_policy_version,
    coalesce(p_explicit_hit, false),
    coalesce(p_scope_ref, 'carr-internal'))
  returning id
$$;

revoke all on function log_retrieval_query(text,integer,uuid[],jsonb,text,bigint,boolean,text) from public;
-- The writer only. carr_reader must never be able to reach this: the entire
-- point of the split is that the read path cannot write, by privilege and not
-- merely by convention.
grant execute on function log_retrieval_query(text,integer,uuid[],jsonb,text,bigint,boolean,text) to carr_writer;

-- ---------------------------------------------------------------------------
-- THE OUTAGE ITSELF: the read path reached the identity table it cannot read.
--
-- Commit 4abafd3b (2026-08-17) bound doctrine search visibility to the
-- authenticated sponsor, which added this lookup to the READ path:
--
--   select id from actor where slug=$1 and kind='human' and active=true
--
-- and every doctrine search since it reached production traffic has died with
-- "NeonDbError: permission denied for table actor" — nine such rows in
-- ops.incident_fact, and call-verb fell over on the same table for the same
-- reason. It surfaces as a bare "internal error" because a driver error is not
-- a ToolError.
--
-- THE PRECISE CAUSE IS NARROWER THAN "no access", and the narrowness is why it
-- slipped through review. carr_reader HAS column-level SELECT on actor.id and
-- actor.slug. It has none on kind or active. The lookup selects a granted
-- column but FILTERS on two ungranted ones, and Postgres refuses a predicate
-- over a column you cannot read. So the credential looks adequate in any check
-- that asks about the columns being returned, and fails on the where clause.
-- has_table_privilege is false here too, which is what a reviewer would most
-- likely check, and it is false whether or not the column grants exist — so
-- that check cannot tell the two situations apart.
--
-- NOT FIXED BY GRANTING. Handing carr_reader SELECT on actor would widen the
-- read credential to the identity table to solve an ergonomics problem, which
-- is the exact trade this repo spent a separate role avoiding. The lookup runs
-- behind a definer function instead, so the reader resolves a sponsor without
-- ever being able to read the table — the same posture the ranker already uses.
--
-- The function takes a SLUG, never an actor id, and hard-filters to active
-- humans: it can only ever return a partner row, so it cannot become a way to
-- enumerate or select arbitrary actors from a read connection.
create or replace function retrieval_visibility_actor_id(p_sponsor_slug text)
returns uuid
language sql stable security definer set search_path = public, pg_temp as $$
  select id from actor
   where slug = p_sponsor_slug and kind = 'human' and active = true
$$;

revoke all on function retrieval_visibility_actor_id(text) from public;
grant execute on function retrieval_visibility_actor_id(text) to carr_reader;
grant execute on function retrieval_visibility_actor_id(text) to carr_writer;

commit;
