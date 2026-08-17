-- 0182_launchd_scheduler_observation_receipt.sql
-- Bind launchd retirement to the same immutable native enabled -> disabled
-- evidence boundary already required for Claude scheduler surfaces.

begin;

create table ops.legacy_schedule_launchd_contract (
  surface_id text primary key references ops.legacy_schedule_surface_registry(surface_id) on delete cascade,
  workflow_key text not null,
  workflow_version integer not null,
  locator text not null,
  repo_plist_relpath text not null check (btrim(repo_plist_relpath) <> ''),
  installed_plist_name text not null check (btrim(installed_plist_name) <> ''),
  program_arguments jsonb not null check (jsonb_typeof(program_arguments)='array'),
  plist_sha256 text not null check (plist_sha256 ~ '^[0-9a-f]{64}$'),
  schedule_sha256 text not null check (schedule_sha256 ~ '^[0-9a-f]{64}$'),
  timezone text not null check (btrim(timezone) <> ''),
  unique (workflow_key,workflow_version,locator),
  foreign key (workflow_key,workflow_version,surface_id)
    references ops.legacy_schedule_surface_registry(workflow_key,workflow_version,surface_id)
    on delete cascade
);

alter table ops.legacy_schedule_observation_receipt
  add column scheduler_kind text not null default 'claude-code'
    check (scheduler_kind in ('claude-code','launchd'));

create function ops.record_launchd_scheduler_observation(
  p_surface_id text,
  p_label text,
  p_timezone text,
  p_enabled boolean,
  p_plist_sha256 text,
  p_schedule_sha256 text,
  p_launchctl_revision text,
  p_source_fingerprint text,
  p_observed_at timestamptz,
  p_idempotency_key text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  principal ops.device_evidence_principal%rowtype;
  contract ops.legacy_schedule_launchd_contract%rowtype;
  existing ops.legacy_schedule_observation_receipt%rowtype;
  state text;
  ref text;
begin
  select * into principal from ops.device_evidence_principal
   where login_role=session_user and active;
  if not found then
    raise exception 'launchd observation session user is not an active provisioned device principal';
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'launchd observation idempotency key is required';
  end if;
  if btrim(coalesce(p_surface_id,''))='' or btrim(coalesce(p_label,''))=''
     or btrim(coalesce(p_timezone,''))='' or p_enabled is null then
    raise exception 'launchd observation identity, timezone, and state are required';
  end if;
  if p_observed_at > now() + interval '5 minutes'
     or p_observed_at < now() - interval '15 minutes' then
    raise exception 'launchd observation is outside the freshness window';
  end if;
  if coalesce(p_plist_sha256,'') !~ '^[0-9a-f]{64}$'
     or coalesce(p_schedule_sha256,'') !~ '^[0-9a-f]{64}$'
     or coalesce(p_launchctl_revision,'') !~ '^[0-9a-f]{64}$'
     or coalesce(p_source_fingerprint,'') !~ '^[0-9a-f]{64}$' then
    raise exception 'launchd observation provenance is malformed';
  end if;

  select * into contract from ops.legacy_schedule_launchd_contract
   where surface_id=p_surface_id for share;
  if not found then raise exception 'launchd scheduler surface is not registered'; end if;
  if p_label <> contract.locator or p_timezone <> contract.timezone
     or p_plist_sha256 <> contract.plist_sha256
     or p_schedule_sha256 <> contract.schedule_sha256 then
    raise exception 'launchd observation does not match the registered native contract';
  end if;
  state := case when p_enabled then 'enabled' else 'disabled' end;

  select * into existing from ops.legacy_schedule_observation_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if existing.surface_id is distinct from p_surface_id
       or existing.locator is distinct from p_label
       or existing.scheduler_kind is distinct from 'launchd'
       or existing.scheduler_state is distinct from state
       or existing.cron_expression is distinct from p_schedule_sha256
       or existing.timezone is distinct from p_timezone
       or existing.definition_sha256 is distinct from p_plist_sha256
       or existing.provider_revision is distinct from p_launchctl_revision
       or existing.source_fingerprint is distinct from p_source_fingerprint
       or existing.observed_at is distinct from p_observed_at
       or existing.device_id is distinct from principal.device_id then
      raise exception 'launchd observation idempotency key was reused with different evidence';
    end if;
    return existing.receipt_ref;
  end if;

  ref := 'scheduler-observation:' || p_idempotency_key;
  insert into ops.legacy_schedule_observation_receipt
    (receipt_ref,idempotency_key,surface_id,workflow_key,workflow_version,locator,
     scheduler_kind,scheduler_state,cron_expression,timezone,definition_sha256,
     provider_revision,source_fingerprint,observed_at,device_id)
  values
    (ref,p_idempotency_key,contract.surface_id,contract.workflow_key,contract.workflow_version,
     contract.locator,'launchd',state,contract.schedule_sha256,contract.timezone,
     contract.plist_sha256,p_launchctl_revision,p_source_fingerprint,p_observed_at,
     principal.device_id);
  return ref;
end $$;

revoke all on ops.legacy_schedule_launchd_contract from public;
revoke all on function ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text)
  from public,carr_jobs,carr_reader,carr_writer,carr_authority;
grant select on ops.legacy_schedule_launchd_contract to carr_jobs,carr_reader;
grant execute on function ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text)
  to carr_device_evidence;

create or replace function ops.disable_legacy_schedule(
  p_workflow_key text,p_surface_id text,p_locator text,p_reason text,
  p_pre_observation_ref text,p_post_observation_ref text,p_sibling_observation_ref text,
  p_idempotency_key text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v integer;
  n integer;
  ref text;
  kind text;
  existing ops.legacy_schedule_disable_receipt%rowtype;
  pre ops.legacy_schedule_observation_receipt%rowtype;
  post ops.legacy_schedule_observation_receipt%rowtype;
begin
  if ops.authority_actor_slug() <> 'joe' then
    raise exception 'legacy schedule retirement requires Joe authority session';
  end if;
  if btrim(coalesce(p_workflow_key,''))='' or btrim(coalesce(p_surface_id,''))=''
     or btrim(coalesce(p_locator,''))='' or btrim(coalesce(p_reason,''))=''
     or btrim(coalesce(p_pre_observation_ref,''))=''
     or btrim(coalesce(p_post_observation_ref,''))=''
     or btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'cutover subject, reason, native observations, and idempotency key are required';
  end if;
  if p_sibling_observation_ref is not null then
    raise exception 'single-surface cutover does not accept caller-supplied sibling evidence';
  end if;

  select * into existing from ops.legacy_schedule_disable_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if existing.workflow_key is distinct from p_workflow_key
       or existing.surface_id is distinct from p_surface_id
       or existing.locator is distinct from p_locator
       or existing.reason is distinct from p_reason
       or existing.pre_observation_ref is distinct from p_pre_observation_ref
       or existing.post_observation_ref is distinct from p_post_observation_ref
       or existing.sibling_observation_ref is distinct from p_sibling_observation_ref then
      raise exception 'idempotency key is bound to different verified legacy disable evidence';
    end if;
    return existing.receipt_ref;
  end if;

  select version into v from ops.job_definition
   where key=p_workflow_key order by version desc limit 1;
  if v is null then raise exception 'unknown workflow %',p_workflow_key; end if;
  if p_workflow_key='notes-sweep-hourly' then
    raise exception 'Notes duplicate retirement requires two-surface native evidence and remains fail-closed';
  end if;
  select scheduler_kind into kind from ops.legacy_schedule_surface_registry
   where workflow_key=p_workflow_key and workflow_version=v
     and surface_id=p_surface_id and locator=p_locator;
  if kind is null then raise exception 'legacy schedule surface is not registered'; end if;
  if kind='claude-code' and not exists (
    select 1 from ops.legacy_schedule_provider_contract
     where workflow_key=p_workflow_key and workflow_version=v
       and surface_id=p_surface_id and locator=p_locator
  ) then
    raise exception 'legacy schedule lacks a current native Claude provider contract';
  elsif kind='launchd' and not exists (
    select 1 from ops.legacy_schedule_launchd_contract
     where workflow_key=p_workflow_key and workflow_version=v
       and surface_id=p_surface_id and locator=p_locator
  ) then
    raise exception 'legacy schedule lacks a current native launchd contract';
  end if;
  if not exists (
    select 1 from ops.workflow_acceptance
     where workflow_key=p_workflow_key and workflow_version=v and mode='shadow' and status='accepted'
  ) or not exists (
    select 1 from ops.workflow_acceptance
     where workflow_key=p_workflow_key and workflow_version=v and mode='canary' and status='accepted'
  ) then
    raise exception 'legacy schedule retirement requires accepted shadow and canary evidence';
  end if;

  select r.* into pre from ops.legacy_schedule_observation_receipt r
   where r.receipt_ref=p_pre_observation_ref and r.scheduler_kind=kind
     and ((kind='claude-code' and exists (
       select 1 from ops.legacy_schedule_provider_contract c
        where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
          and c.workflow_version=r.workflow_version and c.locator=r.locator
          and c.cron_expression=r.cron_expression and c.timezone=r.timezone
          and c.definition_sha256=r.definition_sha256))
       or (kind='launchd' and exists (
       select 1 from ops.legacy_schedule_launchd_contract c
        where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
          and c.workflow_version=r.workflow_version and c.locator=r.locator
          and c.schedule_sha256=r.cron_expression and c.timezone=r.timezone
          and c.plist_sha256=r.definition_sha256)));
  select r.* into post from ops.legacy_schedule_observation_receipt r
   where r.receipt_ref=p_post_observation_ref and r.scheduler_kind=kind
     and ((kind='claude-code' and exists (
       select 1 from ops.legacy_schedule_provider_contract c
        where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
          and c.workflow_version=r.workflow_version and c.locator=r.locator
          and c.cron_expression=r.cron_expression and c.timezone=r.timezone
          and c.definition_sha256=r.definition_sha256))
       or (kind='launchd' and exists (
       select 1 from ops.legacy_schedule_launchd_contract c
        where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
          and c.workflow_version=r.workflow_version and c.locator=r.locator
          and c.schedule_sha256=r.cron_expression and c.timezone=r.timezone
          and c.plist_sha256=r.definition_sha256)));
  if pre.id is null or post.id is null
     or pre.workflow_key<>p_workflow_key or post.workflow_key<>p_workflow_key
     or pre.workflow_version<>v or post.workflow_version<>v
     or pre.surface_id<>p_surface_id or post.surface_id<>p_surface_id
     or pre.locator<>p_locator or post.locator<>p_locator
     or pre.scheduler_state<>'enabled' or post.scheduler_state<>'disabled'
     or pre.observed_at<now()-interval '15 minutes'
     or post.observed_at<now()-interval '15 minutes'
     or pre.observed_at>now()+interval '5 minutes'
     or post.observed_at>now()+interval '5 minutes'
     or post.observed_at<pre.observed_at
     or pre.source_fingerprint=post.source_fingerprint then
    raise exception 'native scheduler evidence is not a current enabled-to-disabled readback';
  end if;

  ref := 'legacy-disable:' || p_idempotency_key;
  update ops.job_definition set legacy_disabled_at=now(),legacy_disable_reason=p_reason,updated_at=now()
   where key=p_workflow_key and version=v and legacy_disabled_at is null;
  get diagnostics n=row_count;
  if n<>1 then raise exception 'legacy schedule was not disabled'; end if;
  insert into ops.legacy_schedule_disable_receipt
    (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,
     approved_by,pre_observation_ref,post_observation_ref,sibling_observation_ref)
  values
    (ref,p_idempotency_key,p_workflow_key,v,p_surface_id,p_locator,p_reason,'joe',
     p_pre_observation_ref,p_post_observation_ref,null);
  return ref;
end $$;

do $$ begin
  if has_function_privilege('carr_jobs',
       'ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text)'::regprocedure,'execute')
     or has_table_privilege('carr_jobs','ops.legacy_schedule_observation_receipt','insert') then
    raise exception '0182 FAILED: routine jobs can mint launchd observations';
  end if;
  if not has_function_privilege('carr_device_evidence',
       'ops.record_launchd_scheduler_observation(text,text,text,boolean,text,text,text,text,timestamptz,text)'::regprocedure,'execute') then
    raise exception '0182 FAILED: device evidence role cannot append launchd observations';
  end if;
end $$;

commit;
