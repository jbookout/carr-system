-- 0168_guidance_registry.sql
-- One typed, revisioned Guidance Registry on top of Control Plane admission.
-- This migration installs structure and projections only.  It does NOT bulk
-- classify rules, approve a constitution, activate retrieval mappings, or
-- switch standing-context.  Those remain explicit human-approved operations.

begin;

-- Control Plane already owns capture/admission.  Extend its intake vocabulary
-- instead of creating a second intake authority.
alter table ops.guidance_intake drop constraint if exists guidance_intake_lane_check;
alter table ops.guidance_intake add constraint guidance_intake_lane_check
  check (lane in ('rule','constraint','procedure','doctrine','rubric','preference',
                  'precedent','example','fact','action'));

create table ops.guidance_registry (
  id          uuid primary key default gen_random_uuid(),
  singleton   boolean not null default true unique check (singleton),
  created_by  uuid not null references actor(id),
  created_at  timestamptz not null default now()
);

create table ops.guidance_item (
  id                  uuid primary key default gen_random_uuid(),
  source_rule_id      uuid references rule(id) on delete restrict,
  guidance_intake_id  uuid references ops.guidance_intake(id) on delete restrict,
  source_clause       text not null check (btrim(source_clause) <> ''),
  is_primary          boolean not null default true,
  split_group_id      uuid,
  created_by          uuid not null references actor(id),
  created_at          timestamptz not null default now(),
  constraint guidance_source_is_traceable check (
    source_rule_id is not null or guidance_intake_id is not null
  ),
  constraint split_child_names_group check (is_primary or split_group_id is not null),
  unique (source_rule_id, source_clause)
);

create unique index guidance_one_primary_per_rule
  on ops.guidance_item(source_rule_id)
  where source_rule_id is not null and is_primary;

create table ops.guidance_revision (
  id                       uuid primary key default gen_random_uuid(),
  guidance_item_id         uuid not null references ops.guidance_item(id) on delete restrict,
  version                  integer not null check (version > 0),
  guidance_type            text not null check (guidance_type in (
    'constraint','procedure','doctrine','rubric','preference','precedent','example')),
  scope                    jsonb not null,
  activation               jsonb not null,
  consumer                 text not null check (btrim(consumer) <> ''),
  verification             jsonb not null,
  provenance               jsonb not null,
  delivery                 jsonb not null,
  is_constitution          boolean not null default false,
  supersedes_revision_id   uuid references ops.guidance_revision(id) on delete restrict,
  classified_by            uuid not null references actor(id),
  reason                   text not null check (btrim(reason) <> ''),
  created_at               timestamptz not null default now(),
  unique (guidance_item_id, version)
);

create table ops.guidance_authority_binding (
  id                       uuid primary key default gen_random_uuid(),
  guidance_revision_id     uuid not null unique
    references ops.guidance_revision(id) on delete restrict,
  authority_receipt_id     uuid not null unique
    references ops.authority_receipt(id) on delete restrict,
  contract_hash            text not null check (contract_hash ~ '^[0-9a-f]{64}$'),
  created_at               timestamptz not null default now()
);

create table ops.guidance_lifecycle_event (
  id                       uuid primary key default gen_random_uuid(),
  guidance_revision_id     uuid not null references ops.guidance_revision(id) on delete restrict,
  state                    text not null check (state in ('active','retired','superseded')),
  authority_binding_id     uuid not null references ops.guidance_authority_binding(id) on delete restrict,
  supersedes_event_id      uuid references ops.guidance_lifecycle_event(id) on delete restrict,
  reason                   text not null check (btrim(reason) <> ''),
  created_at               timestamptz not null default now(),
  unique (guidance_revision_id, authority_binding_id, state)
);

create table ops.guidance_situation_mapping (
  id                       uuid primary key default gen_random_uuid(),
  guidance_revision_id     uuid not null references ops.guidance_revision(id) on delete restrict,
  concept_id               uuid not null references retrieval_concept(id) on delete restrict,
  doctrine_section_id      uuid not null references doctrine_section(id) on delete restrict,
  state                    text not null check (state in ('proposed','active','retired')),
  authority_binding_id     uuid references ops.guidance_authority_binding(id) on delete restrict,
  supersedes_mapping_id    uuid references ops.guidance_situation_mapping(id) on delete restrict,
  reason                   text not null check (btrim(reason) <> ''),
  created_at               timestamptz not null default now(),
  constraint active_mapping_has_authority check (
    state <> 'active' or authority_binding_id is not null
  )
);

create table ops.guidance_registry_event (
  id                    uuid primary key default gen_random_uuid(),
  registry_id           uuid not null references ops.guidance_registry(id) on delete restrict,
  state                 text not null check (state in ('active','inactive')),
  authority_receipt_id  uuid not null references ops.authority_receipt(id) on delete restrict,
  manifest_digest       text not null check (manifest_digest ~ '^[0-9a-f]{64}$'),
  reason                text not null check (btrim(reason) <> ''),
  created_at            timestamptz not null default now(),
  unique (registry_id, authority_receipt_id)
);

create or replace function ops.refuse_guidance_history_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'typed guidance identity, revisions, mappings and authority history are append-only';
end $$;

create trigger guidance_item_append_only
  before update or delete on ops.guidance_item
  for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_revision_append_only
  before update or delete on ops.guidance_revision
  for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_authority_binding_append_only
  before update or delete on ops.guidance_authority_binding
  for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_lifecycle_event_append_only
  before update or delete on ops.guidance_lifecycle_event
  for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_situation_mapping_append_only
  before update or delete on ops.guidance_situation_mapping
  for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_registry_event_append_only
  before update or delete on ops.guidance_registry_event
  for each row execute function ops.refuse_guidance_history_rewrite();

create or replace function ops.validate_guidance_revision()
returns trigger language plpgsql as $$
declare
  expected_projection text;
  prior_item uuid;
begin
  if jsonb_typeof(new.scope) <> 'object'
     or coalesce(btrim(new.scope->>'tenant'),'') = ''
     or coalesce(btrim(new.scope->>'actor'),'') = '' then
    raise exception 'guidance revision requires tenant and actor scope';
  end if;
  if jsonb_typeof(new.activation) <> 'object'
     or coalesce(btrim(new.activation->>'trigger'),'') = '' then
    raise exception 'guidance revision requires an activation trigger';
  end if;
  if jsonb_typeof(new.verification) <> 'object'
     or coalesce(btrim(new.verification->>'mechanism'),'') = '' then
    raise exception 'guidance revision requires a verification mechanism';
  end if;
  if jsonb_typeof(new.provenance) <> 'object'
     or new.provenance->>'preserve_source_record' <> 'true' then
    raise exception 'guidance revision must preserve source provenance';
  end if;
  if jsonb_typeof(new.delivery) <> 'object' then
    raise exception 'guidance revision requires a delivery contract';
  end if;
  expected_projection := case new.guidance_type
    when 'constraint' then 'constraint_enforcement'
    when 'procedure' then 'procedure_workflow'
    when 'doctrine' then 'doctrine_retrieval'
    when 'rubric' then 'verification_rubric'
    when 'preference' then 'scoped_preference'
    when 'precedent' then 'precedent_search'
    when 'example' then 'example_retrieval'
  end;
  if new.delivery->>'projection' is distinct from expected_projection then
    raise exception 'guidance type % requires delivery projection %',
      new.guidance_type, expected_projection;
  end if;
  if new.guidance_type='constraint' and (
       coalesce(btrim(new.delivery->>'enforcement_control'),'') = ''
       or jsonb_typeof(new.delivery->'evidence') <> 'array'
       or jsonb_array_length(new.delivery->'evidence') = 0
       or jsonb_typeof(new.delivery->'tests') <> 'array'
       or jsonb_array_length(new.delivery->'tests') = 0) then
    raise exception 'constraint requires installed enforcement evidence and tests';
  elsif new.guidance_type='procedure' and (
       coalesce(btrim(new.activation->>'entry_condition'),'') = ''
       or coalesce(btrim(new.verification->>'completion_condition'),'') = '') then
    raise exception 'procedure requires entry and completion conditions';
  elsif new.guidance_type='doctrine' and (
       jsonb_typeof(new.activation->'situation_mappings') <> 'array'
       or jsonb_array_length(new.activation->'situation_mappings') = 0) then
    raise exception 'doctrine requires situation mappings';
  elsif new.guidance_type='rubric' and (
       coalesce(btrim(new.verification->>'verifier'),'') = ''
       or jsonb_typeof(new.verification->'acceptance_criteria') <> 'array'
       or jsonb_array_length(new.verification->'acceptance_criteria') = 0) then
    raise exception 'rubric requires verifier and acceptance criteria';
  elsif new.guidance_type='preference'
        and new.scope->>'actor' in ('','all') then
    raise exception 'preference requires a scoped actor';
  end if;
  if new.supersedes_revision_id is not null then
    select guidance_item_id into prior_item
      from ops.guidance_revision where id=new.supersedes_revision_id;
    if prior_item is distinct from new.guidance_item_id then
      raise exception 'a revision may supersede only a revision of the same guidance item';
    end if;
  end if;
  return new;
end $$;

create trigger guidance_revision_validate
  before insert on ops.guidance_revision
  for each row execute function ops.validate_guidance_revision();

create or replace function ops.validate_guidance_authority_binding()
returns trigger language plpgsql as $$
declare
  receipt_subject uuid;
  receipt_type text;
  receipt_hash text;
  item_id uuid;
begin
  select subject_id, subject_type, contract_hash
    into receipt_subject, receipt_type, receipt_hash
    from ops.authority_receipt where id=new.authority_receipt_id;
  select guidance_item_id into item_id
    from ops.guidance_revision where id=new.guidance_revision_id;
  if receipt_type <> 'guidance' or receipt_subject <> item_id then
    raise exception 'authority receipt must name the exact guidance item';
  end if;
  if receipt_hash is distinct from new.contract_hash then
    raise exception 'authority receipt hash does not match the exact guidance revision contract';
  end if;
  return new;
end $$;

create trigger guidance_authority_binding_validate
  before insert on ops.guidance_authority_binding
  for each row execute function ops.validate_guidance_authority_binding();

create or replace view ops.v_guidance_revision_state as
select r.*,
       coalesce(e.state,'proposed') as lifecycle_status,
       e.id as lifecycle_event_id,
       e.authority_binding_id,
       e.created_at as lifecycle_at
  from ops.guidance_revision r
  left join lateral (
    select le.* from ops.guidance_lifecycle_event le
     where le.guidance_revision_id=r.id
     order by le.created_at desc,le.id desc limit 1
  ) e on true;

create or replace view ops.v_guidance_current as
select distinct on (i.id)
       i.id as guidance_item_id,
       i.source_rule_id,
       i.guidance_intake_id,
       i.source_clause,
       i.is_primary,
       i.split_group_id,
       r.id as guidance_revision_id,
       r.version,
       r.guidance_type,
       r.scope,
       r.activation,
       r.consumer,
       r.verification,
       r.provenance,
       r.delivery,
       r.is_constitution,
       r.classified_by,
       r.reason,
       r.lifecycle_at
  from ops.guidance_item i
  join ops.v_guidance_revision_state r on r.guidance_item_id=i.id
 where r.lifecycle_status='active'
 order by i.id,r.version desc,r.lifecycle_at desc,r.guidance_revision_id desc;

create or replace view ops.v_guidance_constraint as
select g.*,a.enforcement_class,a.binding_moment,a.applicability,
       ep.control_key,ep.implementation_ref,ep.test_ref,ep.verified_at
  from ops.v_guidance_current g
  join ops.rule_admission a on a.rule_id=g.source_rule_id and a.state='admitted'
  join ops.rule_enforcement_point ep
    on ep.rule_id=g.source_rule_id and ep.installed
 where g.guidance_type='constraint';

create or replace view ops.v_guidance_procedure as
select * from ops.v_guidance_current where guidance_type='procedure';
create or replace view ops.v_guidance_rubric as
select * from ops.v_guidance_current where guidance_type='rubric';
create or replace view ops.v_guidance_preference as
select * from ops.v_guidance_current where guidance_type='preference';
create or replace view ops.v_guidance_precedent as
select g.guidance_item_id as decision_id,
       coalesce(r.activated_at,r.created_at)::date as entry_date,
       left(r.statement,240) as title,
       r.human_quote,
       g.reason as agent_rationale,
       a.slug as author,
       jsonb_build_object('source','typed-guidance','source_rule_id',g.source_rule_id,
                          'guidance_revision_id',g.guidance_revision_id) as provenance,
       coalesce(r.statement,'') || ' ' || coalesce(r.human_quote,'') || ' ' || g.reason as haystack
  from ops.v_guidance_current g
  join rule r on r.id=g.source_rule_id
  left join actor a on a.id=r.taught_by
 where g.guidance_type='precedent';
create or replace view ops.v_guidance_example as
select * from ops.v_guidance_current where guidance_type='example';

create or replace view ops.v_guidance_situation_mapping_current as
select distinct on (m.guidance_revision_id,m.concept_id,m.doctrine_section_id)
       m.*
  from ops.guidance_situation_mapping m
 order by m.guidance_revision_id,m.concept_id,m.doctrine_section_id,
          m.created_at desc,m.id desc;

create or replace view ops.v_guidance_doctrine_retrieval as
select g.*,c.concept_key,s.document_id,s.section_key,
       m.id as guidance_mapping_id,dcm.id as retrieval_mapping_id
  from ops.v_guidance_current g
  join ops.v_guidance_situation_mapping_current m
    on m.guidance_revision_id=g.guidance_revision_id and m.state='active'
  join retrieval_concept c on c.id=m.concept_id and c.status='active'
  join doctrine_section s on s.id=m.doctrine_section_id and s.status='active'
  join doctrine_concept_mapping dcm
    on dcm.concept_id=m.concept_id
   and dcm.section_id=m.doctrine_section_id
   and dcm.status='approved'
 where g.guidance_type='doctrine';

create or replace view ops.v_guidance_registry_state as
select r.id as registry_id,
       coalesce(e.state,'inactive') as state,
       e.authority_receipt_id,e.manifest_digest,e.created_at as changed_at
  from ops.guidance_registry r
  left join lateral (
    select ge.* from ops.guidance_registry_event ge
     where ge.registry_id=r.id
     order by ge.created_at desc,ge.id desc limit 1
  ) e on true;

create or replace function ops.assert_guidance_registry_coverage()
returns table(source_rule_id uuid, issue text)
language sql stable as $$
  with active_rules as (
    select id from rule where status='active'
  ), primary_counts as (
    select ar.id,
           count(g.*) filter (where g.is_primary) as primary_count
      from active_rules ar
      left join ops.v_guidance_current g on g.source_rule_id=ar.id
     group by ar.id
  )
  select id,
         case when primary_count=0 then 'missing active primary guidance'
              else 'multiple active primary guidance records' end
    from primary_counts where primary_count <> 1
  union all
  select g.source_rule_id,'constraint lacks admitted installed enforcement projection'
    from ops.v_guidance_current g
   where g.is_primary and g.guidance_type='constraint'
     and not exists (
       select 1 from ops.v_guidance_constraint c
        where c.guidance_revision_id=g.guidance_revision_id)
  union all
  select g.source_rule_id,'doctrine lacks active WR-AI-006 situation bridge'
    from ops.v_guidance_current g
   where g.is_primary and g.guidance_type='doctrine'
     and not exists (
       select 1 from ops.v_guidance_doctrine_retrieval d
        where d.guidance_revision_id=g.guidance_revision_id)
$$;

create or replace function ops.standing_guidance(
  p_actor text,
  p_workflow text default null,
  p_surface text default null,
  p_tier text default null
) returns table(
  source_rule_id uuid,
  statement text,
  human_quote text,
  taught_by uuid,
  personal_to text,
  scope jsonb,
  guidance_type text,
  is_constitution boolean
)
language sql stable as $$
  select r.id,r.statement,r.human_quote,r.taught_by,r.personal_to,g.scope,
         g.guidance_type,g.is_constitution
    from ops.v_guidance_current g
    join rule r on r.id=g.source_rule_id and r.status='active'
   where exists (select 1 from ops.v_guidance_registry_state s where s.state='active')
     and (r.personal_to is null or r.personal_to=p_actor)
     and (
       g.is_constitution
       or (g.guidance_type='constraint' and exists (
         select 1 from ops.applicable_rules(p_workflow,p_surface,p_tier) ar
          where ar.rule_id=r.id))
     )
   order by g.is_constitution desc,r.personal_to nulls first,r.created_at,r.id
$$;

create or replace view ops.v_guidance_projection_summary as
select guidance_type,count(*) as active_items,
       encode(digest(string_agg(guidance_revision_id::text,',' order by guidance_revision_id),'sha256'),'hex')
         as projection_digest
  from ops.v_guidance_current group by guidance_type;

create or replace function ops.activate_guidance_registry(
  p_registry_id uuid,
  p_authority_receipt_id uuid,
  p_manifest_digest text,
  p_reason text
) returns uuid
language plpgsql as $$
declare
  receipt_actor uuid;
  event_id uuid;
  constitution_count integer;
  coverage_count integer;
begin
  select ar.actor_id into receipt_actor
    from ops.authority_receipt ar join actor a on a.id=ar.actor_id
   where ar.id=p_authority_receipt_id
     and ar.kind='activation'
     and ar.subject_type='guidance'
     and ar.subject_id=p_registry_id
     and a.kind='human';
  if receipt_actor is null then
    raise exception 'guidance registry activation requires a human authority receipt';
  end if;
  select count(*) into constitution_count
    from ops.v_guidance_current where is_constitution;
  -- Human-approved constitution must remain between 5 and 10 items.
  if constitution_count not between 5 and 10 then
    raise exception 'guidance constitution must contain between 5 and 10 active items';
  end if;
  select count(*) into coverage_count from ops.assert_guidance_registry_coverage();
  if coverage_count <> 0 then
    raise exception 'guidance registry has % coverage failure(s)',coverage_count;
  end if;
  if p_manifest_digest !~ '^[0-9a-f]{64}$' or coalesce(btrim(p_reason),'')='' then
    raise exception 'activation requires a sha256 manifest digest and reason';
  end if;
  insert into ops.guidance_registry_event
    (registry_id,state,authority_receipt_id,manifest_digest,reason)
  values (p_registry_id,'active',p_authority_receipt_id,p_manifest_digest,p_reason)
  returning id into event_id;
  return event_id;
end $$;

-- Seed only the empty registry identity.  No rule is classified or activated.
insert into ops.guidance_registry(created_by)
select id from actor where slug='joe' and kind='human'
on conflict (singleton) do nothing;

grant select on ops.guidance_registry,ops.guidance_item,ops.guidance_revision,
  ops.guidance_authority_binding,ops.guidance_lifecycle_event,
  ops.guidance_situation_mapping,ops.guidance_registry_event,
  ops.v_guidance_revision_state,ops.v_guidance_current,ops.v_guidance_constraint,
  ops.v_guidance_procedure,ops.v_guidance_doctrine_retrieval,ops.v_guidance_rubric,
  ops.v_guidance_preference,ops.v_guidance_precedent,ops.v_guidance_example,
  ops.v_guidance_registry_state,ops.v_guidance_projection_summary
  to carr_reader,carr_writer;
grant insert on ops.guidance_item,ops.guidance_revision,
  ops.guidance_authority_binding,ops.guidance_lifecycle_event,
  ops.guidance_situation_mapping,ops.guidance_registry_event to carr_writer;
grant execute on function ops.assert_guidance_registry_coverage() to carr_reader,carr_writer;
grant execute on function ops.standing_guidance(text,text,text,text)
  to carr_reader,carr_writer,carr_jobs;
grant execute on function ops.activate_guidance_registry(uuid,uuid,text,text)
  to carr_writer;

commit;

do $$
begin
  if to_regclass('ops.guidance_revision') is null
     or to_regclass('ops.guidance_authority_binding') is null then
    raise exception '0168 FAILED: typed guidance registry tables missing';
  end if;
  if to_regprocedure('ops.standing_guidance(text,text,text,text)') is null
     or to_regprocedure('ops.assert_guidance_registry_coverage()') is null then
    raise exception '0168 FAILED: typed guidance projections missing';
  end if;
end $$;
