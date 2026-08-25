-- 0297: CARR-native learning memory kernel, Phase 1.
-- Extends actor/event provenance and the existing profile/rule conventions.
-- A memory is context, never authority; forgetting suppresses recall but does
-- not erase evidence. Hermes native memory remains outside this migration.

begin;

create table memory_item (
  id uuid primary key default gen_random_uuid(),
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
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  search_vector tsvector generated always as
    (to_tsvector('english', coalesce(statement,'') || ' ' || coalesce(context,''))) stored,
  check ((scope='shared' and owner_actor_id is null) or
         (scope='personal' and owner_actor_id is not null)),
  check (status <> 'promoted' or promoted_by_actor_id is not null)
);
create index memory_item_search_idx on memory_item using gin(search_vector);
create index memory_item_scope_idx on memory_item(scope, owner_actor_id, status, confidence desc);

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
grant select on memory_item, memory_evidence to carr_reader;
grant select, insert, update on memory_item to carr_writer;
grant select, insert on memory_evidence to carr_writer;

commit;
