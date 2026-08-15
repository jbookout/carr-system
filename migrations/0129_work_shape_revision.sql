-- 0129_work_shape_revision.sql
-- Requirements say what work must do; they do not determine whether it should
-- be a verb, UI, scheduled lane, CLI, stored table, or derived view. This adds
-- the missing, evidence-backed decision immediately upstream of implementation.

begin;

alter table ops.work_request
  add column if not exists shape_required boolean not null default false;

comment on column ops.work_request.shape_required is
  'When true, the Work Request may not enter implementation until it has a valid '
  'work_shape_revision. False by default so this migration does not retroactively '
  'block requests whose implementation form was already settled.';

create table if not exists ops.work_shape_revision (
  id                    uuid primary key default gen_random_uuid(),
  work_request_id       uuid not null references ops.work_request(id),
  work_request_version  integer not null check (work_request_version > 0),
  version               integer not null check (version > 0),
  trinity               jsonb not null check (jsonb_typeof(trinity) = 'object'),
  hidden_assumption     text not null check (btrim(hidden_assumption) <> ''),
  repo_searches         jsonb not null check (jsonb_typeof(repo_searches) = 'array'),
  maintained_repos      jsonb not null check (jsonb_typeof(maintained_repos) = 'array'),
  archetypes            jsonb not null check (jsonb_typeof(archetypes) = 'array'),
  chosen_key            text not null check (btrim(chosen_key) <> ''),
  mind_changing_fact    text not null check (btrim(mind_changing_fact) <> ''),
  builder_brief         jsonb not null check (jsonb_typeof(builder_brief) = 'object'),
  source_url            text,
  created_by_actor_id   uuid not null references public.actor(id),
  created_at            timestamptz not null default now(),
  unique (work_request_id, version)
);

comment on table ops.work_shape_revision is
  'Append-only implementation-shape reasoning for one canonical Work Request. '
  'The Worker enforces the trinity, recon, three archetypes, scores, falsifier '
  'and builder-brief contract; the database preserves every accepted revision.';

create or replace function ops.work_shape_revision_immutable()
returns trigger language plpgsql as $$
begin
  raise exception 'ops.work_shape_revision is append-only';
end;
$$;

drop trigger if exists work_shape_revision_immutable on ops.work_shape_revision;
create trigger work_shape_revision_immutable
before update or delete on ops.work_shape_revision
for each row execute function ops.work_shape_revision_immutable();

create index if not exists work_shape_revision_current_idx
  on ops.work_shape_revision (work_request_id, version desc);

create or replace view ops.v_work_shape_current
with (security_invoker = true) as
select distinct on (work_request_id) *
from ops.work_shape_revision
order by work_request_id, version desc;

grant select on ops.work_shape_revision to carr_reader;
grant select on ops.work_shape_revision to carr_writer;
grant insert on ops.work_shape_revision to carr_writer;
grant select on ops.v_work_shape_current to carr_reader, carr_writer;

commit;

do $$
declare
  v_column integer;
  v_table integer;
  v_trigger integer;
begin
  select count(*) into v_column
    from information_schema.columns
   where table_schema='ops' and table_name='work_request' and column_name='shape_required';
  select count(*) into v_table
    from information_schema.tables
   where table_schema='ops' and table_name='work_shape_revision';
  select count(*) into v_trigger
    from pg_trigger
   where tgrelid='ops.work_shape_revision'::regclass and tgname='work_shape_revision_immutable' and not tgisinternal;
  if v_column <> 1 or v_table <> 1 or v_trigger <> 1 then
    raise exception '0129 FAILED: shape column %, revision table %, immutable trigger %', v_column, v_table, v_trigger;
  end if;
  raise notice '0129: conditional shape gate, append-only revisions, and current projection present';
end $$;
