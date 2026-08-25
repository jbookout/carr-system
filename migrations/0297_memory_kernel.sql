-- 0297: CARR-native learning memory kernel, Phase 1.
-- Extends actor/event provenance and the existing profile/rule conventions.
-- A memory is context, never authority; forgetting suppresses recall but does
-- not erase evidence. Hermes native memory remains outside this migration.

begin;

create table memory_item (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null,
  work_request_id uuid references ops.work_request(id),
  work_request_version integer,
  plan_id uuid references ops.sourced_work_request_plan(id),
  job_attempt_id uuid references ops.job_attempt(id),
  source_state text,
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
  check (work_request_id is null or work_request_version is not null),
  check (status <> 'promoted' or promoted_by_actor_id is not null)
);
create index memory_item_search_idx on memory_item using gin(search_vector);
create index memory_item_scope_idx on memory_item(scope, owner_actor_id, status, confidence desc);
create index memory_item_tenant_scope_idx on memory_item(organization_tenant_id, scope, owner_actor_id, status);

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

revoke update, delete on memory_evidence from public;
-- Deny-by-default: handlers apply tenant and sponsor predicates; the DB roles
-- receive only the minimum table operations and no delete path.
revoke all on memory_item, memory_evidence from public;
grant select on memory_item, memory_evidence to carr_reader;
grant select, insert, update on memory_item to carr_writer;
grant select, insert on memory_evidence to carr_writer;
revoke delete on memory_item, memory_evidence from carr_reader, carr_writer, carr_jobs;

commit;
