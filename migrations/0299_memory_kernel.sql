-- 0299: CARR-native learning memory kernel, Phase 1.
-- Extends actor/event provenance and the existing profile/rule conventions.
-- A memory is context, never authority; forgetting suppresses recall but does
-- not erase evidence. Hermes native memory remains outside this migration.

begin;

create or replace function public.resolve_memory_plan_anchor(p_plan_id uuid, p_tenant text)
returns table(plan_id uuid, work_request_id uuid, work_request_version integer)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select p.id, p.work_request_id, p.work_request_version
    from ops.sourced_work_request_plan p
    join ops.work_request w on w.id=p.work_request_id
   where p.id=$1 and w.organization_tenant_id=$2;
$$;
revoke all on function public.resolve_memory_plan_anchor(uuid,text) from public, carr_reader, carr_jobs, carr_authority;
grant execute on function public.resolve_memory_plan_anchor(uuid,text) to carr_writer;

create table memory_item (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  work_request_id uuid references ops.work_request(id),
  work_request_version integer,
  plan_id uuid references ops.sourced_work_request_plan(id),
  kind text not null check (kind in ('preference','fact','episodic','procedural')),
  statement text not null check (length(btrim(statement)) > 0),
  context text,
  scope text not null check (scope in ('shared','personal')),
  owner_actor_id uuid references actor(id),
  observed_by_actor_id uuid not null references actor(id),
  status text not null default 'candidate'
    check (status in ('candidate','promoted','corrected','forgotten')),
  confidence numeric(4,3) not null default 0.500
    check (confidence >= 0 and confidence <= 1),
  version bigint not null default 1,
  promoted_by_actor_id uuid references actor(id),
  promoted_at timestamptz,
  corrected_by_actor_id uuid references actor(id),
  correction_reason text,
  corrected_at timestamptz,
  forgotten_by_actor_id uuid references actor(id),
  forget_reason text,
  forgotten_at timestamptz,
  predecessor_id uuid references memory_item(id),
  lineage_root_id uuid references memory_item(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  search_vector tsvector generated always as
    (to_tsvector('english', coalesce(statement,'') || ' ' || coalesce(context,''))) stored,
  check ((scope='shared' and owner_actor_id is null) or
         (scope='personal' and owner_actor_id is not null)),
  check (length(btrim(organization_tenant_id)) > 0),
  check ((plan_id is null and work_request_id is null and work_request_version is null) or
         (plan_id is not null and work_request_id is not null and work_request_version is not null)),
  check (status <> 'promoted' or promoted_by_actor_id is not null)
);
create index memory_item_search_idx on memory_item using gin(search_vector);
create index memory_item_scope_idx on memory_item(scope, owner_actor_id, status, confidence desc);
create index memory_item_tenant_scope_idx on memory_item(organization_tenant_id, scope, owner_actor_id, status);

create or replace function public.memory_item_insert_valid()
returns trigger language plpgsql security definer
set search_path = pg_catalog, public
as $$
declare prior memory_item%rowtype; expected_root uuid;
begin
  -- Timestamps are server-owned; caller-supplied values are ignored.
  new.created_at := now();
  new.updated_at := new.created_at;
  if new.status <> 'candidate' or new.version <> 1
     or new.promoted_by_actor_id is not null or new.promoted_at is not null
     or new.corrected_by_actor_id is not null or new.correction_reason is not null or new.corrected_at is not null
     or new.forgotten_by_actor_id is not null or new.forget_reason is not null or new.forgotten_at is not null then
    raise exception 'new memory rows must start as clean candidate version 1';
  end if;
  if new.predecessor_id is null or new.lineage_root_id is null then
    if new.predecessor_id is not null or new.lineage_root_id is not null then
      raise exception 'new memory roots cannot carry partial lineage';
    end if;
    return new;
  end if;
  select * into prior from public.memory_item where id=new.predecessor_id;
  expected_root := coalesce(prior.lineage_root_id, prior.id);
  if not found or prior.status <> 'corrected'
     or prior.organization_tenant_id is distinct from new.organization_tenant_id
     or prior.scope is distinct from new.scope
     or prior.owner_actor_id is distinct from new.owner_actor_id
     or prior.kind is distinct from new.kind
     or prior.context is distinct from new.context
     or prior.confidence is distinct from new.confidence
     or prior.work_request_id is distinct from new.work_request_id
     or prior.work_request_version is distinct from new.work_request_version
     or prior.plan_id is distinct from new.plan_id
     or new.lineage_root_id is distinct from expected_root then
    raise exception 'memory successor lineage does not match corrected predecessor';
  end if;
  return new;
end $$;
drop trigger if exists memory_item_insert_valid on memory_item;
create trigger memory_item_insert_valid
before insert on memory_item for each row execute function public.memory_item_insert_valid();

create or replace function memory_item_plan_anchor_valid()
returns trigger language plpgsql security definer
set search_path = pg_catalog, public, ops
as $$
declare plan_row record;
begin
  if new.plan_id is null then return new; end if;
  select plan.work_request_id, plan.work_request_version, w.organization_tenant_id
    into plan_row
    from ops.sourced_work_request_plan plan
    join ops.work_request w on w.id=plan.work_request_id
   where plan.id=new.plan_id;
  if not found or plan_row.work_request_id is distinct from new.work_request_id
     or plan_row.work_request_version is distinct from new.work_request_version
     or plan_row.organization_tenant_id is distinct from new.organization_tenant_id then
    raise exception 'memory plan anchor is missing, cross-tenant, or mismatched';
  end if;
  return new;
end $$;
drop trigger if exists memory_item_plan_anchor_valid on memory_item;
create trigger memory_item_plan_anchor_valid
before insert or update on memory_item for each row execute function memory_item_plan_anchor_valid();

create table memory_evidence (
  id uuid primary key default gen_random_uuid(),
  memory_id uuid not null references memory_item(id),
  source_type text not null check (length(btrim(source_type)) > 0),
  source_ref text,
  observation text not null check (length(btrim(observation)) > 0),
  human_quote text,
  observed_by_actor_id uuid not null references actor(id),
  provenance jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index memory_evidence_memory_idx on memory_evidence(memory_id, observed_at desc);

create or replace function memory_item_immutable_core()
returns trigger language plpgsql as $$
begin
  if new.created_at is distinct from old.created_at then
    raise exception 'memory_item created_at is immutable';
  end if;
  if new.kind is distinct from old.kind
     or new.context is distinct from old.context
     or new.confidence is distinct from old.confidence
     or new.observed_by_actor_id is distinct from old.observed_by_actor_id
     or new.statement is distinct from old.statement
     or new.scope is distinct from old.scope
     or new.owner_actor_id is distinct from old.owner_actor_id
     or new.organization_tenant_id is distinct from old.organization_tenant_id
     or new.work_request_id is distinct from old.work_request_id
     or new.work_request_version is distinct from old.work_request_version
     or new.plan_id is distinct from old.plan_id
     or new.predecessor_id is distinct from old.predecessor_id
     or new.lineage_root_id is distinct from old.lineage_root_id then
    raise exception 'memory_item core is immutable; create a successor for corrections';
  end if;
  if new.version <> old.version + 1 then
    raise exception 'memory_item lifecycle updates must increment version exactly once';
  end if;
  if new.status is not distinct from old.status then
    raise exception 'memory_item status must make one allowed lifecycle transition';
  end if;
  if old.status='candidate' and new.status='promoted' then
    if old.promoted_by_actor_id is not null or old.promoted_at is not null
       or new.promoted_by_actor_id is null or new.promoted_at is null
       or new.corrected_by_actor_id is distinct from old.corrected_by_actor_id
       or new.correction_reason is distinct from old.correction_reason
       or new.corrected_at is distinct from old.corrected_at
       or new.forgotten_by_actor_id is distinct from old.forgotten_by_actor_id
       or new.forget_reason is distinct from old.forget_reason
       or new.forgotten_at is distinct from old.forgotten_at then
      raise exception 'candidate promotion requires actor and timestamp';
    end if;
  elsif old.status in ('candidate','promoted') and new.status='corrected' then
    if old.corrected_by_actor_id is not null or old.correction_reason is not null or old.corrected_at is not null
       or new.corrected_by_actor_id is null or new.correction_reason is null or new.corrected_at is null
       or new.promoted_by_actor_id is distinct from old.promoted_by_actor_id
       or new.promoted_at is distinct from old.promoted_at
       or new.forgotten_by_actor_id is distinct from old.forgotten_by_actor_id
       or new.forget_reason is distinct from old.forget_reason
       or new.forgotten_at is distinct from old.forgotten_at then
      raise exception 'memory correction requires actor, reason, and timestamp';
    end if;
  elsif old.status in ('candidate','promoted','corrected') and new.status='forgotten' then
    if old.forgotten_by_actor_id is not null or old.forget_reason is not null or old.forgotten_at is not null
       or new.forgotten_by_actor_id is null or new.forget_reason is null or new.forgotten_at is null
       or new.promoted_by_actor_id is distinct from old.promoted_by_actor_id
       or new.promoted_at is distinct from old.promoted_at
       or new.corrected_by_actor_id is distinct from old.corrected_by_actor_id
       or new.correction_reason is distinct from old.correction_reason
       or new.corrected_at is distinct from old.corrected_at then
      raise exception 'memory forgetting requires actor, reason, and timestamp';
    end if;
  else
    raise exception 'memory_item status transition is not an allowed lifecycle transition';
  end if;
  return new;
end $$;
drop trigger if exists memory_item_immutable_core on memory_item;
create trigger memory_item_immutable_core
before update on memory_item for each row execute function memory_item_immutable_core();

revoke update, delete on memory_evidence from public;
-- Deny-by-default: handlers apply tenant and sponsor predicates; the DB roles
-- receive only the minimum table operations and no delete path.
revoke all on memory_item, memory_evidence from public;
grant select on memory_item, memory_evidence to carr_reader;
grant select, insert, update on memory_item to carr_writer;
grant select, insert on memory_evidence to carr_writer;
revoke delete on memory_item, memory_evidence from carr_reader, carr_writer, carr_jobs;

do $$
begin
  if not has_function_privilege('carr_writer', 'public.resolve_memory_plan_anchor(uuid,text)', 'execute') then
    raise exception '0299 FAILED: carr_writer cannot execute the narrow plan resolver';
  end if;
  if has_table_privilege('carr_writer', 'ops.sourced_work_request_plan', 'select') then
    raise exception '0299 FAILED: carr_writer gained broad plan-table SELECT';
  end if;
end $$;

commit;
