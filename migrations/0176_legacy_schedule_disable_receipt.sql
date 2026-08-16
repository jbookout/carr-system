-- 0176_legacy_schedule_disable_receipt.sql
-- A Joe-only legacy-disable action must emit its own immutable receipt.  A
-- canary acceptance is evidence for retirement, never the retirement approval.

begin;

-- This is the FK-bound database projection of the versioned scheduler
-- registry.  It starts empty: ``tools/control-plane.py sync`` validates the
-- complete checked-in registry and populates it transactionally *after* its
-- matching ops.job_definition rows exist.  Do not seed it in this migration.
create table ops.legacy_schedule_surface_registry (
  workflow_key text not null,
  workflow_version integer not null,
  surface_id text not null unique,
  locator text not null,
  scheduler_kind text not null check (scheduler_kind in ('launchd','claude-code')),
  primary key (workflow_key, workflow_version, surface_id),
  unique (workflow_key, workflow_version, locator),
  foreign key (workflow_key, workflow_version) references ops.job_definition(key, version)
);

create table ops.legacy_schedule_disable_receipt (
  id uuid primary key default gen_random_uuid(),
  receipt_ref text not null unique,
  idempotency_key text not null unique,
  workflow_key text not null,
  workflow_version integer not null,
  surface_id text not null,
  locator text not null,
  reason text not null check (btrim(reason) <> ''),
  approved_by text not null check (approved_by = 'joe'),
  approved_at timestamptz not null default now(),
  unique (workflow_key, workflow_version, surface_id, locator),
  foreign key (workflow_key, workflow_version) references ops.job_definition(key, version)
);

create or replace function ops.refuse_legacy_schedule_disable_receipt_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'legacy schedule disable receipts are append-only';
end $$;

create trigger legacy_schedule_disable_receipt_append_only
 before update or delete on ops.legacy_schedule_disable_receipt
 for each row execute function ops.refuse_legacy_schedule_disable_receipt_rewrite();

-- Retire the unbound two-argument action: there must be no human-only disable
-- path that can omit the immutable surface/locator/idempotency receipt.
revoke all on function ops.disable_legacy_schedule(text,text) from public, carr_writer, carr_authority;
revoke all on function ops.disable_legacy_schedule(text,text,text) from public, carr_writer, carr_authority;
drop function if exists ops.disable_legacy_schedule(text,text);
drop function if exists ops.disable_legacy_schedule(text,text,text);

create function ops.disable_legacy_schedule(
  p_workflow_key text,p_surface_id text,p_locator text,p_reason text,p_idempotency_key text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer; v integer; ref text; existing ops.legacy_schedule_disable_receipt%rowtype;
begin
  if ops.authority_actor_slug() <> 'joe' then
    raise exception 'legacy schedule retirement requires Joe authority session';
  end if;
  if btrim(coalesce(p_surface_id,''))='' or btrim(coalesce(p_locator,''))='' then
    raise exception 'legacy schedule surface and locator are required';
  end if;
  if btrim(coalesce(p_reason,''))='' or btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'cutover reason and idempotency key are required';
  end if;
  -- A successful idempotent call is bound to the caller's exact immutable
  -- request, not to today's registry.  The registry can be reconciled after
  -- this surface has been retired; that must never turn a prior receipt into
  -- a non-replayable write.  New requests are checked against it below.
  select * into existing from ops.legacy_schedule_disable_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if existing.workflow_key <> p_workflow_key
       or existing.surface_id <> p_surface_id or existing.locator <> p_locator
       or existing.reason <> p_reason then
      raise exception 'idempotency key is bound to different legacy disable evidence';
    end if;
    return existing.receipt_ref;
  end if;
  select version into v from ops.job_definition
   where key=p_workflow_key order by version desc limit 1;
  if v is null then raise exception 'unknown workflow %', p_workflow_key; end if;
  if not exists (
    select 1 from ops.legacy_schedule_surface_registry
     where workflow_key=p_workflow_key and workflow_version=v
       and surface_id=p_surface_id and locator=p_locator
  ) then raise exception 'unknown or mismatched legacy schedule surface/locator'; end if;
  ref := 'legacy-disable:' || p_idempotency_key;
  update ops.job_definition set legacy_disabled_at=now(),legacy_disable_reason=p_reason,updated_at=now()
   where key=p_workflow_key and version=v and legacy_disabled_at is null;
  get diagnostics n=row_count;
  if n = 0 then
    select * into existing from ops.legacy_schedule_disable_receipt where idempotency_key=p_idempotency_key;
    if not found then raise exception 'legacy schedule was not disabled'; end if;
    if existing.workflow_key <> p_workflow_key
       or existing.surface_id <> p_surface_id or existing.locator <> p_locator
       or existing.reason <> p_reason then
      raise exception 'idempotency key is bound to different legacy disable evidence';
    end if;
    return existing.receipt_ref;
  end if;
  insert into ops.legacy_schedule_disable_receipt
    (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,approved_by)
  values (ref,p_idempotency_key,p_workflow_key,v,p_surface_id,p_locator,p_reason,'joe');
  return ref;
end $$;

revoke all on ops.legacy_schedule_disable_receipt from public, carr_writer;
grant select on ops.legacy_schedule_disable_receipt to carr_jobs, carr_reader;
revoke all on function ops.disable_legacy_schedule(text,text,text,text,text) from public, carr_writer;
grant execute on function ops.disable_legacy_schedule(text,text,text,text,text) to carr_authority;

do $$
begin
  if to_regprocedure('ops.disable_legacy_schedule(text,text)') is not null
     or to_regprocedure('ops.disable_legacy_schedule(text,text,text)') is not null then
    raise exception '0176 FAILED: legacy unbound disable signature remains callable';
  end if;
end $$;

commit;
