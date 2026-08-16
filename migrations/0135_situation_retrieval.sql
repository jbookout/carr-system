-- 0135_situation_retrieval.sql — WR-AI-006 deterministic situation retrieval.
-- Human-approved concepts run beside weighted title/body FTS.  No model call,
-- generated answer, raw query log, or revision-body rewrite exists on this path.

begin;

create extension if not exists pg_trgm;
create extension if not exists pgcrypto;

-- The append-only proof is captured before any title-index DDL.  Adding stored
-- title vectors may backfill those new columns; it may never touch revision
-- body, plain_text, or content_hash.
create temporary table _0135_revision_hash_before on commit drop as
select id, content_hash from doctrine_revision;

alter table doctrine_document
  add column title_search_vector tsvector generated always as
    (to_tsvector('english', coalesce(title, '') || ' ' || replace(slug, '-', ' '))) stored;
alter table doctrine_section
  add column title_search_vector tsvector generated always as
    (to_tsvector('english', coalesce(title, '') || ' ' || replace(section_key, '-', ' '))) stored;
create index doctrine_document_title_search_idx
  on doctrine_document using gin (title_search_vector);
create index doctrine_section_title_search_idx
  on doctrine_section using gin (title_search_vector);

create or replace function normalize_retrieval_phrase(value text)
returns text language sql immutable strict parallel safe as $$
  select regexp_replace(lower(btrim(value)), '\s+', ' ', 'g')
$$;

-- Four governed curation tables.  The policy and log tables below are runtime
-- configuration/evidence, not a second curation authority.
create table retrieval_concept (
  id            uuid primary key default gen_random_uuid(),
  concept_key   text not null unique check (concept_key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  label         text not null check (btrim(label) <> ''),
  definition    text not null check (btrim(definition) <> ''),
  status        text not null default 'proposed'
                  check (status in ('proposed','approved','retired')),
  version       bigint not null default 1,
  review_after  timestamptz,
  proposer_id   uuid not null references actor(id),
  approver_id   uuid references actor(id),
  approved_at   timestamptz,
  retired_at    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  check ((status = 'approved') = (approver_id is not null and approved_at is not null)
         or status = 'retired')
);

create table retrieval_phrase (
  id                uuid primary key default gen_random_uuid(),
  concept_id        uuid not null references retrieval_concept(id),
  display_phrase    text not null check (btrim(display_phrase) <> ''),
  normalized_phrase text generated always as (normalize_retrieval_phrase(display_phrase)) stored,
  match_mode        text not null check (match_mode in ('exact','fts','trgm')),
  min_similarity    numeric(4,3) not null default 0.350
                      check (min_similarity between 0 and 1),
  weight            numeric(5,4) not null default 1
                      check (weight > 0 and weight <= 1),
  status            text not null default 'proposed'
                      check (status in ('proposed','approved','retired')),
  version           bigint not null default 1,
  review_after      timestamptz,
  source            text not null
                      check (source in ('golden_miss','no_hit_log','session_proposal','manual')),
  source_ref        text not null check (btrim(source_ref) <> ''),
  proposer_id       uuid not null references actor(id),
  approver_id       uuid references actor(id),
  approved_at       timestamptz,
  retired_at        timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  unique (concept_id, normalized_phrase),
  check (array_length(regexp_split_to_array(normalized_phrase, '\s+'), 1) >= 2),
  check ((status = 'approved') = (approver_id is not null and approved_at is not null)
         or status = 'retired')
);
-- A phrase cannot silently mean two active concepts.  Retirement releases it;
-- an explicit disambiguated phrase can then be proposed for the other concept.
create unique index retrieval_phrase_global_active_uq
  on retrieval_phrase (normalized_phrase) where status = 'approved';
create index retrieval_phrase_fts_idx
  on retrieval_phrase using gin (to_tsvector('english', normalized_phrase))
  where status = 'approved';
create index retrieval_phrase_trgm_idx
  on retrieval_phrase using gin (normalized_phrase gin_trgm_ops)
  where status = 'approved';

create table doctrine_concept_mapping (
  id            uuid primary key default gen_random_uuid(),
  concept_id    uuid not null references retrieval_concept(id),
  section_id    uuid not null references doctrine_section(id),
  role          text not null check (role in ('governs','supports')),
  weight        numeric(5,4) not null default 1 check (weight > 0 and weight <= 1),
  rationale     text not null check (btrim(rationale) <> ''),
  status        text not null default 'proposed'
                  check (status in ('proposed','approved','retired','needs_repair')),
  version       bigint not null default 1,
  review_after  timestamptz,
  proposer_id   uuid not null references actor(id),
  approver_id   uuid references actor(id),
  approved_at   timestamptz,
  retired_at    timestamptz,
  repair_reason text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (concept_id, section_id, role),
  check ((status = 'approved') = (approver_id is not null and approved_at is not null)
         or status in ('retired','needs_repair'))
);
create index doctrine_concept_mapping_active_section_idx
  on doctrine_concept_mapping (section_id) where status = 'approved';

create table retrieval_proposal (
  id                uuid primary key default gen_random_uuid(),
  proposal_type     text not null check (proposal_type in ('concept','phrase','mapping','retire')),
  payload           jsonb not null check (jsonb_typeof(payload) = 'object'),
  reason            text not null check (btrim(reason) <> ''),
  proposer_id       uuid not null references actor(id),
  status            text not null default 'pending'
                      check (status in ('pending','approved','rejected','superseded')),
  reviewer_id       uuid references actor(id),
  resulting_row_ids uuid[] not null default '{}',
  idempotency_key   uuid not null unique,
  version           bigint not null default 1,
  created_at        timestamptz not null default now(),
  reviewed_at       timestamptz,
  check ((status = 'pending') = (reviewer_id is null and reviewed_at is null))
);
create index retrieval_proposal_pending_idx
  on retrieval_proposal (created_at) where status = 'pending';

create table retrieval_ranking_policy (
  policy_id           text primary key,
  version             bigint not null default 1,
  formula             text not null check (formula in ('weighted_sum','coequal_normalized')),
  config              jsonb not null check (jsonb_typeof(config) = 'object'),
  golden_suite_digest text not null check (golden_suite_digest ~ '^[0-9a-f]{64}$'),
  status              text not null default 'candidate' check (status in ('candidate','active','retired')),
  is_default          boolean not null default false,
  created_at          timestamptz not null default now(),
  approved_at         timestamptz
);
create unique index retrieval_ranking_policy_one_default
  on retrieval_ranking_policy (is_default) where is_default;

create table retrieval_query_log (
  id                uuid primary key default gen_random_uuid(),
  normalized_hash   text not null check (normalized_hash ~ '^[0-9a-f]{64}$'),
  result_count      integer not null check (result_count >= 0),
  score_bands       jsonb not null,
  selected_row_ids  uuid[] not null,
  policy_id         text not null references retrieval_ranking_policy(policy_id),
  policy_version    bigint not null,
  explicit_hit      boolean not null,
  scope_ref         text,
  created_at        timestamptz not null default now()
);
create index retrieval_query_log_miss_idx
  on retrieval_query_log (created_at desc) where not explicit_hit;

-- The one lifecycle predicate shared by both lanes.  Scope is applied here,
-- before either score exists.  This exactly preserves search-doctrine's current
-- active-section, current-revision, visibility, and content-class rules.
create or replace function current_retrievable_doctrine(
  p_actor_id uuid, p_content_classes text[] default null
) returns table (
  section_id uuid, section_key text, section_title text, doc_slug text,
  doc_title text, content_class text, plain_text text,
  body_search_vector tsvector, section_title_vector tsvector,
  document_title_vector tsvector
) language sql stable security invoker as $$
  select s.id, s.section_key, s.title, d.slug, d.title, d.content_class,
         r.plain_text, r.search_vector, s.title_search_vector, d.title_search_vector
    from doctrine_section s
    join doctrine_document d on d.id = s.document_id
    join doctrine_revision r on r.id = s.current_revision_id
   where s.status = 'active'
     and (d.visibility <> 'personal' or d.owner_actor_id = p_actor_id)
     and (p_content_classes is null or d.content_class = any(p_content_classes))
$$;

-- The sole production ranker.  Constants are read from the versioned policy
-- row.  The raw websearch tsquery is never rewritten by concept matching.
create or replace function search_doctrine_situations(
  p_query text, p_actor_id uuid, p_content_classes text[] default null,
  p_limit integer default 20, p_policy_id text default null
) returns table (
  section_id uuid, section_key text, title text, doc_slug text,
  content_class text, rank double precision, snippet text,
  lexical_score double precision, concept_score double precision,
  final_score double precision, provenance jsonb
) language sql volatile security definer set search_path = public, pg_temp as $$
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
  ),
  log_write as (
    insert into retrieval_query_log
      (normalized_hash, result_count, score_bands, selected_row_ids,
       policy_id, policy_version, explicit_hit, scope_ref)
    select encode(digest(convert_to((select q from normalized), 'UTF8'), 'sha256'), 'hex'),
           count(l.section_id)::integer,
           jsonb_build_object(
             'high', count(*) filter (where final_score >= .75),
             'medium', count(*) filter (where final_score >= .25 and final_score < .75),
             'low', count(*) filter (where final_score < .25)),
           coalesce(array_agg(section_id order by final_score desc, concept_score desc,
                              lexical_score desc, section_key asc)
                    filter (where section_id is not null), '{}'),
           p.policy_id, p.version, count(l.section_id) > 0, 'carr-internal'
      from policy p left join limited l on true
     group by p.policy_id, p.version
    returning id
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
  from limited l cross join (select count(*) from log_write) logged
 order by l.final_score desc, l.concept_score desc, l.lexical_score desc, l.section_key asc
$$;

-- Section retirement is the synchronous repair transition; the current-set
-- join above is the query-time safety net if any future writer bypasses it.
create or replace function mark_retrieval_mappings_for_repair()
returns trigger language plpgsql as $$
begin
  if old.status = 'active' and new.status <> 'active' then
    update doctrine_concept_mapping
       set status = 'needs_repair', repair_reason = 'target section is no longer active',
           version = version + 1, updated_at = now()
     where section_id = new.id and status = 'approved';
  end if;
  return new;
end $$;
create trigger doctrine_section_retrieval_repair_after_update
after update of status on doctrine_section
for each row execute function mark_retrieval_mappings_for_repair();

create view v_retrieval_mapping_repair as
select m.*, d.slug as doc_slug, s.section_key, s.status as section_status
  from doctrine_concept_mapping m
  join doctrine_section s on s.id = m.section_id
  join doctrine_document d on d.id = s.document_id
 where m.status = 'needs_repair' or s.status <> 'active' or s.current_revision_id is null;

create view v_retrieval_concepts_without_targets as
select c.id, c.concept_key, c.label, c.review_after
  from retrieval_concept c
 where c.status = 'approved'
   and not exists (
     select 1 from doctrine_concept_mapping m
     join doctrine_section s on s.id = m.section_id
                            and s.status = 'active'
                            and s.current_revision_id is not null
      where m.concept_id = c.id and m.status = 'approved'
   );

create view v_retrieval_health as
select 'mapping_repair_queue'::text as metric,
       count(*)::bigint as count,
       'nonzero -> work v_retrieval_mapping_repair before approving more curation'::text as bound_action
  from v_retrieval_mapping_repair
union all
select 'concepts_without_active_targets', count(*)::bigint,
       'nonzero -> map or retire every row in v_retrieval_concepts_without_targets'
  from v_retrieval_concepts_without_targets;

-- Proposal rows are an immutable trail except for the one reviewed transition.
create or replace function retrieval_proposal_guard()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then raise exception 'retrieval proposals are append-only'; end if;
  if old.proposal_type is distinct from new.proposal_type
     or old.payload is distinct from new.payload
     or old.reason is distinct from new.reason
     or old.proposer_id is distinct from new.proposer_id
     or old.idempotency_key is distinct from new.idempotency_key
     or old.created_at is distinct from new.created_at then
    raise exception 'retrieval proposal evidence is immutable';
  end if;
  if old.status <> 'pending' or new.status not in ('approved','rejected','superseded') then
    raise exception 'retrieval proposal review transition invalid';
  end if;
  return new;
end $$;
create trigger retrieval_proposal_guard_before_update
before update on retrieval_proposal for each row execute function retrieval_proposal_guard();
create trigger retrieval_proposal_guard_before_delete
before delete on retrieval_proposal for each row execute function retrieval_proposal_guard();

create or replace function retire_retrieval_curation(
  p_target_type text, p_target_id uuid, p_base_version bigint,
  p_actor_id uuid, p_reason text
) returns table(id uuid, version bigint, status text)
language plpgsql security invoker as $$
begin
  if btrim(p_reason) = '' then raise exception 'retirement reason required'; end if;
  if not exists (select 1 from actor where actor.id=p_actor_id and kind='human') then
    raise exception 'human_only';
  end if;
  if p_target_type = 'concept' then
    update retrieval_concept set status='retired', retired_at=now(), version=version+1, updated_at=now()
     where retrieval_concept.id=p_target_id and retrieval_concept.version=p_base_version and status='approved'
     returning retrieval_concept.id, retrieval_concept.version, retrieval_concept.status into id, version, status;
    if id is not null then
      update retrieval_phrase set status='retired', retired_at=now(), version=version+1, updated_at=now()
       where concept_id=p_target_id and status='approved';
      update doctrine_concept_mapping set status='retired', retired_at=now(), version=version+1, updated_at=now()
       where concept_id=p_target_id and status in ('approved','needs_repair');
    end if;
  elsif p_target_type = 'phrase' then
    update retrieval_phrase set status='retired', retired_at=now(), version=version+1, updated_at=now()
     where retrieval_phrase.id=p_target_id and retrieval_phrase.version=p_base_version and status='approved'
     returning retrieval_phrase.id, retrieval_phrase.version, retrieval_phrase.status into id, version, status;
  elsif p_target_type = 'mapping' then
    update doctrine_concept_mapping set status='retired', retired_at=now(), version=version+1, updated_at=now()
     where doctrine_concept_mapping.id=p_target_id and doctrine_concept_mapping.version=p_base_version
       and status in ('approved','needs_repair')
     returning doctrine_concept_mapping.id, doctrine_concept_mapping.version,
               doctrine_concept_mapping.status into id, version, status;
  else raise exception 'unknown retrieval curation type %', p_target_type;
  end if;
  if id is not null then return next; end if;
end $$;

create or replace function promote_retrieval_proposal(p_proposal_id uuid, p_approver_id uuid)
returns uuid language plpgsql security invoker as $$
declare p retrieval_proposal%rowtype; landed uuid; cid uuid; sid uuid;
begin
  if not exists (select 1 from actor where actor.id=p_approver_id and kind='human') then
    raise exception 'human_only';
  end if;
  select * into p from retrieval_proposal where id=p_proposal_id for update;
  if not found or p.status <> 'pending' then raise exception 'proposal is not pending'; end if;
  if p.proposal_type = 'concept' then
    insert into retrieval_concept
      (concept_key,label,definition,status,review_after,proposer_id,approver_id,approved_at)
    values (p.payload->>'concept_key',p.payload->>'label',p.payload->>'definition','approved',
            (p.payload->>'review_after')::timestamptz,p.proposer_id,p_approver_id,now()) returning id into landed;
  elsif p.proposal_type = 'phrase' then
    select id into cid from retrieval_concept
     where concept_key=p.payload->>'concept_key' and status='approved';
    if cid is null then raise exception 'approved concept not found'; end if;
    insert into retrieval_phrase
      (concept_id,display_phrase,match_mode,min_similarity,weight,status,source,source_ref,
       proposer_id,approver_id,approved_at)
    values (cid,p.payload->>'phrase',p.payload->>'match_mode',
            coalesce((p.payload->>'min_similarity')::numeric,.35),
            (p.payload->>'weight')::numeric,'approved',p.payload->>'source',p.payload->>'source_ref',
            p.proposer_id,p_approver_id,now()) returning id into landed;
  elsif p.proposal_type = 'mapping' then
    select id into cid from retrieval_concept
     where concept_key=p.payload->>'concept_key' and status='approved';
    select s.id into sid from doctrine_section s join doctrine_document d on d.id=s.document_id
     where d.slug=split_part(p.payload->>'section_address','#',1)
       and s.section_key=split_part(p.payload->>'section_address','#',2) and s.status='active';
    if cid is null or sid is null then raise exception 'approved concept or current section not found'; end if;
    insert into doctrine_concept_mapping
      (concept_id,section_id,role,weight,rationale,status,proposer_id,approver_id,approved_at)
    values (cid,sid,p.payload->>'role',(p.payload->>'weight')::numeric,p.payload->>'rationale',
            'approved',p.proposer_id,p_approver_id,now()) returning id into landed;
  elsif p.proposal_type = 'retire' then
    select r.id into landed from retire_retrieval_curation(
      p.payload->>'target_type',(p.payload->>'target_id')::uuid,
      (p.payload->>'base_version')::bigint,p_approver_id,p.reason) r;
    if landed is null then raise exception 'retirement version conflict'; end if;
  end if;
  update retrieval_proposal set status='approved', reviewer_id=p_approver_id,
         resulting_row_ids=array[landed], reviewed_at=now(), version=version+1
   where id=p.id;
  return landed;
end $$;

-- Both council policies stay implemented.  The observed winner is the default;
-- the other remains selectable so the comparison and rollback are reproducible.
insert into retrieval_ranking_policy
  (policy_id, formula, config, golden_suite_digest, status, is_default, approved_at)
values
  ('lexical-dominant-v1','weighted_sum',
   '{"lexical_weight":0.75,"concept_weight":0.25,"concept_enabled":true}',
   'b1a5a61945c5e5fc5f7c74f45c3403f2c5df3e61db29e58f281d49015f63dae3','candidate',false,null),
  ('coequal-normalized-v1','coequal_normalized',
   '{"dual_evidence_bonus":0.15,"concept_enabled":true}',
   'b1a5a61945c5e5fc5f7c74f45c3403f2c5df3e61db29e58f281d49015f63dae3','active',true,now());

-- The gate function binds approval to the committed suite digest.  CI runs the
-- complete grown scorecard; transaction-time approval reruns the two measured
-- conceptual misses, the title gap, and the near-miss negative against the
-- uncommitted candidate rows before allowing them to commit.
create or replace function assert_situation_retrieval_golden(p_suite_digest text)
returns table(case_id text, status text, target_rank integer)
language plpgsql security invoker as $$
declare c record; targets text[]; selected_policy text; required_ok boolean;
        forbidden_ok boolean; empty_ok boolean;
begin
  select policy_id into selected_policy from retrieval_ranking_policy
   where is_default and status='active' and golden_suite_digest=p_suite_digest;
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
    ('RET-LIFE-001','retired retrieval lifecycle fixture','{}'::text[],false,'{}'::text[],true),
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

-- Seed evidence only.  These rows bind the two measured misses and the
-- deliberately ambiguous case, but activate nothing until Joe or Dell approves
-- the batch through the human-only verb.
do $$
declare proposer uuid;
begin
  select id into proposer from actor where slug in ('codex','joe') order by (slug='codex') desc limit 1;
  if proposer is null then raise exception '0135 requires a provisioned proposer actor'; end if;
  insert into retrieval_proposal
    (proposal_type,payload,reason,proposer_id,idempotency_key) values
    ('concept','{"concept_key":"record-layer-outage-diagnosis","label":"Record layer outage diagnosis","definition":"Diagnosing an unavailable or failing CARR record layer before attempting repair."}',
     'RET-002 measured conceptual miss',proposer,'13500000-0000-4000-8000-000000000001'),
    ('phrase','{"concept_key":"record-layer-outage-diagnosis","phrase":"record layer outage diagnosis runbook","match_mode":"exact","min_similarity":0.35,"weight":1,"source":"golden_miss","source_ref":"RET-002"}',
     'RET-002 exact situation language',proposer,'13500000-0000-4000-8000-000000000002'),
    ('phrase','{"concept_key":"record-layer-outage-diagnosis","phrase":"database service unavailable troubleshooting steps","match_mode":"fts","min_similarity":0.35,"weight":0.9,"source":"golden_miss","source_ref":"RET-PHRASE-001"}',
     'RET-002 approved paraphrase candidate',proposer,'13500000-0000-4000-8000-000000000003'),
    ('phrase','{"concept_key":"record-layer-outage-diagnosis","phrase":"review cycle after a record layer outage diagnosis","match_mode":"fts","min_similarity":0.35,"weight":0.8,"source":"golden_miss","source_ref":"RET-AMB-001"}',
     'RET-AMB-001 explicit outage-side ambiguity evidence',proposer,'13500000-0000-4000-8000-000000000009'),
    ('mapping','{"concept_key":"record-layer-outage-diagnosis","section_address":"runbook#diagnosis-checklist-in-order-2-minutes","role":"governs","weight":1,"rationale":"This current runbook section is the ordered diagnosis procedure."}',
     'RET-002 target mapping',proposer,'13500000-0000-4000-8000-000000000004'),
    ('concept','{"concept_key":"playbook-self-improvement-review","label":"Playbook self-improvement review","definition":"Reviewing operating evidence so the playbook learns from mistakes and improves."}',
     'RET-003 measured conceptual miss',proposer,'13500000-0000-4000-8000-000000000005'),
    ('phrase','{"concept_key":"playbook-self-improvement-review","phrase":"playbook self improvement review cycle","match_mode":"exact","min_similarity":0.35,"weight":1,"source":"golden_miss","source_ref":"RET-003"}',
     'RET-003 exact situation language',proposer,'13500000-0000-4000-8000-000000000006'),
    ('phrase','{"concept_key":"playbook-self-improvement-review","phrase":"how the operating playbook learns from mistakes","match_mode":"fts","min_similarity":0.35,"weight":0.9,"source":"golden_miss","source_ref":"RET-PHRASE-002"}',
     'RET-003 approved paraphrase candidate',proposer,'13500000-0000-4000-8000-000000000007'),
    ('phrase','{"concept_key":"playbook-self-improvement-review","phrase":"review cycle after a record layer outage playbook","match_mode":"fts","min_similarity":0.35,"weight":0.8,"source":"golden_miss","source_ref":"RET-AMB-001"}',
     'RET-AMB-001 explicit review-side ambiguity evidence',proposer,'13500000-0000-4000-8000-000000000010'),
    ('mapping','{"concept_key":"playbook-self-improvement-review","section_address":"playbook-review#preamble","role":"governs","weight":1,"rationale":"The current preamble states the playbook review and improvement cycle."}',
     'RET-003 target mapping',proposer,'13500000-0000-4000-8000-000000000008');
end $$;

-- Least privilege: proposals and approved rows are verb-writable; nothing is
-- deletable.  Read functions are available wherever existing doctrine reads
-- are available, while promotion/retirement stay behind registered verbs.
do $$
begin
  if exists (select 1 from pg_roles where rolname='carr_reader') then
    grant select on retrieval_concept,retrieval_phrase,doctrine_concept_mapping,
                    retrieval_ranking_policy,v_retrieval_mapping_repair,
                    v_retrieval_concepts_without_targets,v_retrieval_health to carr_reader;
    grant execute on function current_retrievable_doctrine(uuid,text[]),
                              search_doctrine_situations(text,uuid,text[],integer,text)
      to carr_reader;
  end if;
  if exists (select 1 from pg_roles where rolname='carr_writer') then
    grant select, insert, update on retrieval_concept,retrieval_phrase,
                                    doctrine_concept_mapping,retrieval_proposal to carr_writer;
    grant select on retrieval_ranking_policy,v_retrieval_mapping_repair,
                    v_retrieval_concepts_without_targets,v_retrieval_health to carr_writer;
    grant insert on retrieval_query_log to carr_writer;
    grant execute on function normalize_retrieval_phrase(text),
                              current_retrievable_doctrine(uuid,text[]),
                              search_doctrine_situations(text,uuid,text[],integer,text),
                              promote_retrieval_proposal(uuid,uuid),
                              retire_retrieval_curation(text,uuid,bigint,uuid,text),
                              assert_situation_retrieval_golden(text) to carr_writer;
  end if;
end $$;
revoke all on function promote_retrieval_proposal(uuid,uuid),
                       retire_retrieval_curation(text,uuid,bigint,uuid,text),
                       assert_situation_retrieval_golden(text) from public;
revoke all on function search_doctrine_situations(text,uuid,text[],integer,text) from public;
revoke delete on retrieval_concept,retrieval_phrase,doctrine_concept_mapping,
                 retrieval_proposal,retrieval_ranking_policy,retrieval_query_log from public;

-- Backfill and grant proofs.  digest(query_bytes, 'sha256') is the only logged
-- query identity; there is deliberately no raw-query column.
do $$
declare changed bigint;
begin
  select count(*) into changed
    from doctrine_revision r full join _0135_revision_hash_before b using (id)
   where r.id is null or b.id is null or r.content_hash is distinct from b.content_hash;
  if changed <> 0 then raise exception '0135 FAILED: % append-only revision hashes changed', changed; end if;
  if exists (select 1 from pg_roles where rolname='carr_writer') then
    if not has_table_privilege('carr_writer','retrieval_proposal','insert')
       or not has_table_privilege('carr_writer','retrieval_proposal','update') then
      raise exception '0135 FAILED: proposal verb writer lacks its table grants';
    end if;
    if has_table_privilege('carr_writer','retrieval_proposal','delete') then
      raise exception '0135 FAILED: proposal audit trail is deletable';
    end if;
    if not has_table_privilege('carr_writer','doctrine_concept_mapping','update') then
      raise exception '0135 FAILED: section retire trigger cannot mark mappings for repair';
    end if;
  end if;
  if (select count(*) from retrieval_ranking_policy) <> 2
     or (select count(*) from retrieval_ranking_policy where is_default) <> 1 then
    raise exception '0135 FAILED: both ranking policies or the single default are missing';
  end if;
  if (select count(*) from retrieval_proposal where status='pending') < 8 then
    raise exception '0135 FAILED: two-miss curation proposals were not seeded';
  end if;
end $$;

commit;
