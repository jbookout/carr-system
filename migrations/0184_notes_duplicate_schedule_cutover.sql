-- 0184_notes_duplicate_schedule_cutover.sql
-- Retire a duplicate legacy scheduler group only when Joe binds fresh native
-- enabled -> disabled evidence for both registered surfaces in one transaction.

begin;

alter table ops.legacy_schedule_surface_registry
  add column duplicate_group text,
  add constraint legacy_schedule_duplicate_group_nonblank
    check (duplicate_group is null or btrim(duplicate_group) <> '');

alter table ops.legacy_schedule_disable_receipt
  add column sibling_surface_id text,
  add column sibling_locator text,
  add column sibling_pre_observation_ref text,
  add column sibling_post_observation_ref text,
  add constraint legacy_disable_sibling_fields_complete check (
    (sibling_surface_id is null and sibling_locator is null
      and sibling_pre_observation_ref is null and sibling_post_observation_ref is null)
    or
    (btrim(sibling_surface_id) <> '' and btrim(sibling_locator) <> ''
      and btrim(sibling_pre_observation_ref) <> '' and btrim(sibling_post_observation_ref) <> '')
  ),
  add constraint legacy_disable_sibling_pre_receipt_fk
    foreign key (sibling_pre_observation_ref)
    references ops.legacy_schedule_observation_receipt(receipt_ref),
  add constraint legacy_disable_sibling_post_receipt_fk
    foreign key (sibling_post_observation_ref)
    references ops.legacy_schedule_observation_receipt(receipt_ref);

revoke all on function ops.disable_legacy_schedule(text,text,text,text,text,text,text,text)
  from public, carr_writer, carr_authority;
drop function if exists ops.disable_legacy_schedule(text,text,text,text,text,text,text,text);

create function ops.disable_legacy_schedule(
  p_workflow_key text,p_surface_id text,p_locator text,p_reason text,
  p_pre_observation_ref text,p_post_observation_ref text,
  p_sibling_surface_id text,p_sibling_locator text,
  p_sibling_pre_observation_ref text,p_sibling_post_observation_ref text,
  p_idempotency_key text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  n integer; v integer; ref text; kind text; group_name text;
  sibling_kind text; sibling_group text;
  existing ops.legacy_schedule_disable_receipt%rowtype;
  pre ops.legacy_schedule_observation_receipt%rowtype;
  post ops.legacy_schedule_observation_receipt%rowtype;
  sibling_pre ops.legacy_schedule_observation_receipt%rowtype;
  sibling_post ops.legacy_schedule_observation_receipt%rowtype;
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
  if (p_sibling_surface_id is null) <> (p_sibling_locator is null)
     or (p_sibling_surface_id is null) <> (p_sibling_pre_observation_ref is null)
     or (p_sibling_surface_id is null) <> (p_sibling_post_observation_ref is null)
     or (p_sibling_surface_id is not null and (
       btrim(p_sibling_surface_id)='' or btrim(p_sibling_locator)=''
       or btrim(p_sibling_pre_observation_ref)='' or btrim(p_sibling_post_observation_ref)='')) then
    raise exception 'duplicate scheduler evidence fields must be supplied together';
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
       or existing.sibling_surface_id is distinct from p_sibling_surface_id
       or existing.sibling_locator is distinct from p_sibling_locator
       or existing.sibling_pre_observation_ref is distinct from p_sibling_pre_observation_ref
       or existing.sibling_post_observation_ref is distinct from p_sibling_post_observation_ref then
      raise exception 'idempotency key is bound to different verified legacy disable evidence';
    end if;
    return existing.receipt_ref;
  end if;

  select version into v from ops.job_definition
   where key=p_workflow_key order by version desc limit 1;
  if v is null then raise exception 'unknown workflow %',p_workflow_key; end if;
  select scheduler_kind,duplicate_group into kind,group_name
    from ops.legacy_schedule_surface_registry
   where workflow_key=p_workflow_key and workflow_version=v
     and surface_id=p_surface_id and locator=p_locator;
  if kind is null then raise exception 'legacy schedule surface is not registered'; end if;

  if group_name is null then
    if p_sibling_surface_id is not null then
      raise exception 'single-surface cutover does not accept sibling evidence';
    end if;
  else
    if p_sibling_surface_id is null or p_surface_id >= p_sibling_surface_id then
      raise exception 'duplicate scheduler evidence must use canonical primary and sibling order';
    end if;
    select scheduler_kind,duplicate_group into sibling_kind,sibling_group
      from ops.legacy_schedule_surface_registry
     where workflow_key=p_workflow_key and workflow_version=v
       and surface_id=p_sibling_surface_id and locator=p_sibling_locator;
    if sibling_kind is null or sibling_group is distinct from group_name
       or (select count(*) from ops.legacy_schedule_surface_registry
            where workflow_key=p_workflow_key and workflow_version=v
              and duplicate_group=group_name) <> 2
       or array[kind,sibling_kind]::text[] @> array['claude-code','launchd']::text[] is not true then
      raise exception 'Notes duplicate retirement requires exact claude-code and launchd surfaces';
    end if;
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

  if p_sibling_surface_id is not null then
    select r.* into sibling_pre from ops.legacy_schedule_observation_receipt r
     where r.receipt_ref=p_sibling_pre_observation_ref and r.scheduler_kind=sibling_kind
       and ((sibling_kind='claude-code' and exists (
         select 1 from ops.legacy_schedule_provider_contract c
          where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
            and c.workflow_version=r.workflow_version and c.locator=r.locator
            and c.cron_expression=r.cron_expression and c.timezone=r.timezone
            and c.definition_sha256=r.definition_sha256))
         or (sibling_kind='launchd' and exists (
         select 1 from ops.legacy_schedule_launchd_contract c
          where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
            and c.workflow_version=r.workflow_version and c.locator=r.locator
            and c.schedule_sha256=r.cron_expression and c.timezone=r.timezone
            and c.plist_sha256=r.definition_sha256)));
    select r.* into sibling_post from ops.legacy_schedule_observation_receipt r
     where r.receipt_ref=p_sibling_post_observation_ref and r.scheduler_kind=sibling_kind
       and ((sibling_kind='claude-code' and exists (
         select 1 from ops.legacy_schedule_provider_contract c
          where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
            and c.workflow_version=r.workflow_version and c.locator=r.locator
            and c.cron_expression=r.cron_expression and c.timezone=r.timezone
            and c.definition_sha256=r.definition_sha256))
         or (sibling_kind='launchd' and exists (
         select 1 from ops.legacy_schedule_launchd_contract c
          where c.surface_id=r.surface_id and c.workflow_key=r.workflow_key
            and c.workflow_version=r.workflow_version and c.locator=r.locator
            and c.schedule_sha256=r.cron_expression and c.timezone=r.timezone
            and c.plist_sha256=r.definition_sha256)));
    if sibling_pre.id is null or sibling_post.id is null
       or sibling_pre.workflow_key<>p_workflow_key or sibling_post.workflow_key<>p_workflow_key
       or sibling_pre.workflow_version<>v or sibling_post.workflow_version<>v
       or sibling_pre.surface_id<>p_sibling_surface_id or sibling_post.surface_id<>p_sibling_surface_id
       or sibling_pre.locator<>p_sibling_locator or sibling_post.locator<>p_sibling_locator
       or sibling_pre.scheduler_state<>'enabled' or sibling_post.scheduler_state<>'disabled'
       or sibling_pre.observed_at<now()-interval '15 minutes'
       or sibling_post.observed_at<now()-interval '15 minutes'
       or sibling_pre.observed_at>now()+interval '5 minutes'
       or sibling_post.observed_at>now()+interval '5 minutes'
       or sibling_post.observed_at<sibling_pre.observed_at
       or sibling_pre.source_fingerprint=sibling_post.source_fingerprint then
      raise exception 'native duplicate scheduler evidence is not a current enabled-to-disabled readback';
    end if;
  end if;

  ref := 'legacy-disable:' || p_idempotency_key;
  update ops.job_definition set legacy_disabled_at=now(),legacy_disable_reason=p_reason,updated_at=now()
   where key=p_workflow_key and version=v and legacy_disabled_at is null;
  get diagnostics n=row_count;
  if n<>1 then raise exception 'legacy schedule was not disabled'; end if;
  insert into ops.legacy_schedule_disable_receipt
    (receipt_ref,idempotency_key,workflow_key,workflow_version,surface_id,locator,reason,
     approved_by,pre_observation_ref,post_observation_ref,sibling_observation_ref,
     sibling_surface_id,sibling_locator,sibling_pre_observation_ref,sibling_post_observation_ref)
  values
    (ref,p_idempotency_key,p_workflow_key,v,p_surface_id,p_locator,p_reason,'joe',
     p_pre_observation_ref,p_post_observation_ref,null,
     p_sibling_surface_id,p_sibling_locator,p_sibling_pre_observation_ref,p_sibling_post_observation_ref);
  return ref;
end $$;

revoke all on function ops.disable_legacy_schedule(
  text,text,text,text,text,text,text,text,text,text,text
) from public,carr_writer;
grant execute on function ops.disable_legacy_schedule(
  text,text,text,text,text,text,text,text,text,text,text
) to carr_authority;

do $$
begin
  if to_regprocedure('ops.disable_legacy_schedule(text,text,text,text,text,text,text,text)') is not null then
    raise exception '0184 FAILED: single-pair disable authority signature remains callable';
  end if;
end $$;

commit;
