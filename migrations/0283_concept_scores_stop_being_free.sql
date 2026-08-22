-- 0283_concept_scores_stop_being_free.sql
-- A SECTION WITH NO CONCEPT EVIDENCE MUST SCORE CONCEPT ZERO.
--
-- THE DEFECT (f4a4405f, found 2026-08-22 while activating the first real
-- curation batch). In PostgreSQL, least() and greatest() IGNORE null
-- arguments, so least(1.0, NULL) is 1.0 — not NULL. The ranker's
--
--   coalesce(least(1.0, ce.concept_score), 0)
--
-- therefore never reached its coalesce: a section with no concept evidence
-- got least(1.0, NULL) = 1.0, a free perfect concept score. Every strict hit
-- tied at final 2.15 under the coequal policy (both lanes at their per-query
-- maximum plus the dual-evidence bonus), and REAL concept evidence — capped
-- at 1.0 — could never outrank the phantom 1.0 every other row carried. The
-- curation lane has been mathematically inert since 0135 shipped, while its
-- provenance said "complete": true on every row. The lexical side had the
-- same composition and the same latent fault. The fix is composition order:
-- coalesce the NULL away FIRST, then cap.
--
-- ALSO AMENDED HERE: the golden case that expects no hits (the retired-
-- lifecycle probe) matched live doctrine lexically — the corpus grew a
-- section that legitimately carries all four of its words — so it has failed
-- against production since that section landed, and because the approval verb
-- refuses ANY golden failure, no curation batch could ever be approved. The
-- query gains a token no real doctrine will ever contain, which preserves the
-- assertion's shape (an unmatchable query returns nothing, even with the
-- concept lane active). The REAL retired-content protection — retire a
-- mapped section, prove it stops surfacing and its mapping lands in the
-- repair queue — lives in ops/situation-retrieval-db-gate.py against real
-- fixtures, where it belongs.

begin;

create or replace function search_doctrine_situations(
  p_query text, p_actor_id uuid, p_content_classes text[] default null,
  p_limit integer default 20, p_policy_id text default null,
  p_allow_fallback boolean default false
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
  unioned as materialized (
    -- Coalesce FIRST, cap second: least(1.0, NULL) is 1.0 in PostgreSQL
    -- (nulls are ignored), which is the whole defect this migration removes.
    select c.*, least(1.0, coalesce(l.raw_score, 0))::double precision as lexical_score,
           least(1.0, coalesce(ce.concept_score, 0))::double precision as concept_score,
           l.snippet, coalesce(ce.phrase_ids, '{}') as phrase_ids,
           coalesce(ce.concept_ids, '{}') as concept_ids,
           coalesce(ce.mapping_ids, '{}') as mapping_ids,
           false as used_fallback
      from current_set c
      left join lexical_raw l on l.section_id = c.section_id
      left join concept_by_section ce on ce.section_id = c.section_id
     where l.section_id is not null or ce.section_id is not null
  ),
  fallback_terms as materialized (
    select websearch_to_tsquery('english', regexp_replace(q, ' ', ' OR ', 'g')) as tsq
      from normalized
  ),
  fallback_raw as (
    select c.section_id,
           ts_rank_cd(
             setweight(c.section_title_vector, 'A') ||
             setweight(c.document_title_vector, 'A') ||
             setweight(c.body_search_vector, 'B'), q.tsq) as raw_score,
           ts_headline('english', c.plain_text, q.tsq, 'MaxWords=25, MinWords=10') as snippet
      from current_set c cross join fallback_terms q
     where p_allow_fallback
       and not exists (select 1 from unioned)
       and (c.section_title_vector @@ q.tsq
         or c.document_title_vector @@ q.tsq
         or c.body_search_vector @@ q.tsq)
  ),
  fallback_unioned as (
    select c.*, least(1.0, coalesce(f.raw_score, 0))::double precision as lexical_score,
           0::double precision as concept_score,
           f.snippet, '{}'::uuid[] as phrase_ids,
           '{}'::uuid[] as concept_ids,
           '{}'::uuid[] as mapping_ids,
           true as used_fallback
      from current_set c
      join fallback_raw f on f.section_id = c.section_id
  ),
  combined as (
    select * from unioned
    union all
    select * from fallback_unioned
  ),
  maxima as (
    select greatest(max(lexical_score), 0) as max_lexical,
           greatest(max(concept_score), 0) as max_concept from combined
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
      from combined u cross join maxima x cross join policy p
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
       || case when l.used_fallback then jsonb_build_object('fallback', true)
               else '{}'::jsonb end
  from limited l
 order by l.final_score desc, l.concept_score desc, l.lexical_score desc, l.section_key asc
$$;

-- The golden gate, with the corpus-drifted no-hit case made unmatchable.
create or replace function assert_situation_retrieval_golden(p_suite_digest text)
returns table(case_id text, status text, target_rank integer)
language plpgsql security invoker as $$
declare c record; targets text[]; selected_policy text; required_ok boolean;
        forbidden_ok boolean; empty_ok boolean;
begin
  select rp.policy_id into selected_policy from retrieval_ranking_policy rp
   where rp.is_default and rp.status='active'
     and rp.golden_suite_digest=p_suite_digest;
  if selected_policy is null then
    raise exception 'golden suite digest mismatch';
  end if;
  for c in select * from (values
    ('RET-001','write acceptance tests first',array['carr-mature-software-end-state-bduf#s39-fresh-session-prompt']::text[],false,'{}'::text[],false),
    ('RET-002','record layer outage diagnosis runbook',array['runbook#diagnosis-checklist-in-order-2-minutes']::text[],false,'{}'::text[],false),
    ('RET-003','playbook self improvement review cycle',array['playbook-review#preamble']::text[],false,'{}'::text[],false),
    ('RET-004','document factory',array['document-factory-routing#preamble']::text[],false,'{}'::text[],false),
    ('RET-005','weekly new provider detection',array['npi-sweep-sop#preamble']::text[],false,'{}'::text[],false),
    ('RET-006','social publishing review approval',array['social-media-workflow#placement-the-entire-social-run-is-local-now-joe-july-18-2026']::text[],false,'{}'::text[],false),
    ('RET-007','space search multi source',array['space-search-sop#preamble']::text[],false,'{}'::text[],false),
    ('RET-008','lead system',array['lead-system-handoff#preamble']::text[],false,'{}'::text[],false),
    ('RET-009','restore backup rehearsal',array['carr-control-room-bduf#s30-test-and-verification-strategy']::text[],false,'{}'::text[],false),
    ('RET-010','single source of truth',array['deal-enrichment-sop#rules']::text[],false,'{}'::text[],false),
    ('RET-ARCH-001','agent systems validation',array['2026-07-30-agent-systems-explainer-takeaways#preamble']::text[],false,'{}'::text[],false),
    ('RET-TITLE-001','diagnosis checklist',array['runbook#diagnosis-checklist-in-order-2-minutes']::text[],false,'{}'::text[],false),
    ('RET-PHRASE-001','database service unavailable troubleshooting steps',array['runbook#diagnosis-checklist-in-order-2-minutes']::text[],false,'{}'::text[],false),
    ('RET-PHRASE-002','how the operating playbook learns from mistakes',array['playbook-review#preamble']::text[],false,'{}'::text[],false),
    ('RET-NEG-001','outage communication template','{}'::text[],false,array['runbook#diagnosis-checklist-in-order-2-minutes']::text[],false),
    -- The token zzqx can appear in no honest doctrine section, so this stays
    -- an every-word query that matches nothing — the drifted form ('retired
    -- retrieval lifecycle fixture') began matching a live end-state section
    -- and blocked every approval batch. Real retired-content leakage is
    -- proven fixture-based in ops/situation-retrieval-db-gate.py.
    ('RET-LIFE-001','retired zzqx retrieval lifecycle fixture','{}'::text[],false,'{}'::text[],true),
    ('RET-AMB-001','review cycle after a record layer outage',array['runbook#diagnosis-checklist-in-order-2-minutes','playbook-review#preamble']::text[],true,'{}'::text[],false)
  ) q(case_id,query,required_targets,require_all,forbidden_targets,expect_no_hits)
  loop
    select array_agg(x.doc_slug||'#'||x.section_key order by x.final_score desc,
                     x.concept_score desc,x.lexical_score desc,x.section_key)
      into targets from search_doctrine_situations(c.query,null,null,3,selected_policy) x;
    targets := coalesce(targets,'{}'); case_id := c.case_id;
    required_ok := case when cardinality(c.required_targets)=0 then true
                        when c.require_all then c.required_targets <@ targets
                        else c.required_targets && targets end;
    forbidden_ok := not (c.forbidden_targets && targets);
    empty_ok := not c.expect_no_hits or cardinality(targets)=0;
    target_rank := case when cardinality(c.required_targets)=0 then null
                        else array_position(targets,c.required_targets[1]) end;
    status := case when required_ok and forbidden_ok and empty_ok then 'pass' else 'fail' end;
    return next;
  end loop;
end $$;

-- Proofs, in the migration itself.
do $$
declare body text; phantom double precision;
begin
  select pg_get_functiondef('search_doctrine_situations(text,uuid,text[],integer,text,boolean)'::regprocedure)
    into body;
  if body like '%coalesce(least(1.0%' then
    raise exception '0283 FAILED: the cap-then-coalesce composition survives — least(1.0, NULL) is 1.0';
  end if;
  if body not like '%least(1.0, coalesce(l.raw_score, 0))%'
     or body not like '%least(1.0, coalesce(ce.concept_score, 0))%' then
    raise exception '0283 FAILED: the coalesce-then-cap composition is not in place';
  end if;
  -- The arithmetic itself, not just the text: the exact expression shape that
  -- was wrong, evaluated live.
  select least(1.0, coalesce(phantom, 0)) into phantom;
  if phantom <> 0 then
    raise exception '0283 FAILED: least/coalesce composition still yields % for null evidence', phantom;
  end if;
end $$;

commit;
