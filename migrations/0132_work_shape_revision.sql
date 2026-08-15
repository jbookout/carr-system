-- 0132_work_shape_revision.sql
-- Requirements say what work must do; they do not determine whether it should
-- be a verb, UI, scheduled lane, CLI, stored table, or derived view. Every Work
-- Request must now make that question explicit before implementation starts.

begin;

alter table ops.work_request
  add column if not exists shape_disposition text,
  add column if not exists shape_fixed_surface_ref text,
  add column if not exists shape_rationale text,
  add column if not exists shape_decided_by_actor_id uuid references public.actor(id),
  add column if not exists shape_decided_at timestamptz;

alter table ops.work_request
  add constraint work_request_shape_disposition_valid check (
    shape_disposition is null or shape_disposition in ('required','not_required')
  ),
  add constraint work_request_shape_disposition_complete check (
    (shape_disposition is null
      and shape_fixed_surface_ref is null
      and shape_rationale is null
      and shape_decided_by_actor_id is null
      and shape_decided_at is null)
    or
    (shape_disposition = 'required'
      and shape_fixed_surface_ref is null
      and shape_rationale is not null
      and btrim(shape_rationale) <> ''
      and shape_decided_by_actor_id is not null
      and shape_decided_at is not null)
    or
    (shape_disposition = 'not_required'
      and shape_fixed_surface_ref is not null
      and btrim(shape_fixed_surface_ref) <> ''
      and shape_rationale is not null
      and btrim(shape_rationale) <> ''
      and shape_decided_by_actor_id is not null
      and shape_decided_at is not null)
  );

comment on column ops.work_request.shape_disposition is
  'Mandatory before a request enters implementation: required runs the evidence-backed '
  'shape method; not_required cites the already-fixed implementation surface. Null is '
  'permitted while work is captured or triaged and on pre-0130 ready rows, which the '
  'implementation-entry gate still refuses until classified.';

-- Work already beyond the implementation boundary cannot be sent backward for a
-- new preflight. Mark only those legacy rows explicitly. Existing ready rows that
-- including the fixed capability program remain undecided and refuse at claim.
update ops.work_request
   set shape_disposition='not_required',
       shape_fixed_surface_ref='legacy:implementation-already-started-before-0130',
       shape_rationale='Implementation started before the mandatory shape-disposition gate existed; future Work Requests and any reopened scope must decide shape before claim.',
       shape_decided_by_actor_id=(select id from public.actor where slug='system' limit 1),
       shape_decided_at=now()
 where state in ('claimed','in_progress','verification','awaiting_release','released','confirmed_closed')
   and shape_disposition is null;

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

create or replace function ops.work_request_shape_gate()
returns trigger language plpgsql as $$
declare
  shape_work_request_version integer;
begin
  if tg_op = 'INSERT' then
    if new.state in ('claimed','in_progress','verification','awaiting_release','released','confirmed_closed') then
      raise exception 'new Work Request cannot enter implementation directly';
    end if;
    if new.state = 'ready' then
      if new.shape_disposition is null then
        raise exception 'ready Work Request requires an explicit shape disposition';
      elsif new.shape_disposition = 'required' then
        raise exception 'required shape analysis needs a captured or triaged Work Request before ready';
      end if;
    end if;
    return new;
  end if;

  if old.state not in ('captured','triaged','ready')
     and (new.shape_disposition,
          new.shape_fixed_surface_ref,
          new.shape_rationale,
          new.shape_decided_by_actor_id,
          new.shape_decided_at)
         is distinct from
         (old.shape_disposition,
          old.shape_fixed_surface_ref,
          old.shape_rationale,
          old.shape_decided_by_actor_id,
          old.shape_decided_at) then
    raise exception 'implementation shape disposition is frozen after claim';
  end if;

  if new.state = 'ready' and old.state is distinct from 'ready' then
    if new.shape_disposition is null then
      raise exception 'ready Work Request requires an explicit shape disposition';
    end if;
    if new.shape_disposition = 'required' then
      select r.work_request_version into shape_work_request_version
        from ops.work_shape_revision r
       where r.work_request_id = old.id
       order by r.version desc
       limit 1;
      if shape_work_request_version is null
         or shape_work_request_version <> old.version then
        raise exception 'required work shape is missing or stale for Work Request version %', old.version;
      end if;
    end if;
  end if;

  -- This is the actual implementation-entry gate. It catches application code,
  -- future verbs, and direct carr_writer SQL equally. Later transitions inside
  -- the implementation-state set use the disposition frozen at first entry.
  if new.state in ('claimed','in_progress','verification','awaiting_release','released','confirmed_closed')
     and old.state not in ('claimed','in_progress','verification','awaiting_release','released','confirmed_closed') then
    if (new.shape_disposition,
        new.shape_fixed_surface_ref,
        new.shape_rationale,
        new.shape_decided_by_actor_id,
        new.shape_decided_at)
       is distinct from
       (old.shape_disposition,
        old.shape_fixed_surface_ref,
        old.shape_rationale,
        old.shape_decided_by_actor_id,
        old.shape_decided_at) then
      raise exception 'shape disposition must be recorded before the implementation transition';
    end if;
    if new.shape_disposition is null then
      raise exception 'Work Request requires an explicit shape disposition before implementation';
    end if;
    if new.shape_disposition = 'required' then
      select r.work_request_version into shape_work_request_version
        from ops.work_shape_revision r
       where r.work_request_id = old.id
       order by r.version desc
       limit 1;
      if shape_work_request_version is null
         or shape_work_request_version <> old.version then
        raise exception 'required work shape is missing or stale for Work Request version %', old.version;
      end if;
    end if;
  end if;

  return new;
end;
$$;

drop trigger if exists work_request_shape_gate on ops.work_request;
create trigger work_request_shape_gate
before insert or update on ops.work_request
for each row execute function ops.work_request_shape_gate();

grant select on ops.work_shape_revision to carr_reader;
grant select on ops.work_shape_revision to carr_writer;
grant insert on ops.work_shape_revision to carr_writer;
grant select on ops.v_work_shape_current to carr_reader, carr_writer;

commit;

do $$
declare
  v_table integer;
  v_revision_trigger integer;
  v_request_trigger integer;
begin
  select count(*) into v_table
    from information_schema.tables
   where table_schema='ops' and table_name='work_shape_revision';
  select count(*) into v_revision_trigger
    from pg_trigger
   where tgrelid='ops.work_shape_revision'::regclass
     and tgname='work_shape_revision_immutable' and not tgisinternal;
  select count(*) into v_request_trigger
    from pg_trigger
   where tgrelid='ops.work_request'::regclass
     and tgname='work_request_shape_gate' and not tgisinternal;
  if v_table <> 1 or v_revision_trigger <> 1 or v_request_trigger <> 1 then
    raise exception '0130 FAILED: revision table %, immutable trigger %, request gate %',
      v_table, v_revision_trigger, v_request_trigger;
  end if;
  if exists (
    select 1 from ops.work_request
     where state in ('claimed','in_progress','verification','awaiting_release','released','confirmed_closed')
       and shape_disposition is null
  ) then
    raise exception '0130 FAILED: an existing implementation-stage request has no shape disposition';
  end if;
  raise notice '0130: mandatory shape disposition and implementation-entry gate present';
end $$;
