-- 0161_control_plane_authority_boundary.sql
-- Human acceptance and Joe-only schedule retirement must not be callable with
-- the routine writer credential plus a forged actor string.  The authority
-- connection authenticates as one of the externally provisioned login roles
-- carr_authority_joe / carr_authority_dell; this migration supplies only the
-- NOLOGIN privilege bundle and derives the actor from session_user.

begin;

do $$ begin
  if not exists (select 1 from pg_roles where rolname='carr_authority') then
    create role carr_authority nologin;
  end if;
end $$;

grant usage on schema ops, public to carr_authority;

create or replace function ops.authority_actor_slug()
returns text
language plpgsql stable security definer set search_path=ops,public,pg_temp
as $$
begin
  case session_user
    when 'carr_authority_joe' then return 'joe';
    when 'carr_authority_dell' then return 'dell';
    else raise exception 'authority session user % is not an admitted human authority principal', session_user;
  end case;
end $$;

create or replace function ops.record_workflow_acceptance(
  p_workflow_key text,p_mode text,p_status text,p_receipt_ref text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare v integer; rid uuid; authority_actor text;
begin
  authority_actor := ops.authority_actor_slug();
  select version into v from ops.job_definition
   where key=p_workflow_key order by version desc limit 1;
  if v is null then raise exception 'unknown workflow %',p_workflow_key; end if;
  if p_status='accepted' and not exists (
    select 1 from ops.job j join ops.job_receipt r on r.job_id=j.id
     where j.definition_key=p_workflow_key and j.definition_version=v
       and j.mode=p_mode and r.kind='completion' and r.receipt_ref=p_receipt_ref
  ) then
    raise exception 'accepted workflow evidence must name a completion receipt from the matching workflow and mode';
  end if;
  insert into ops.workflow_acceptance
    (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
  values(p_workflow_key,v,p_mode,p_status,p_receipt_ref,
         case when p_status='accepted' then authority_actor else null end)
  returning id into rid;
  return rid;
end $$;

create or replace function ops.disable_legacy_schedule(
  p_workflow_key text,p_reason text
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  if ops.authority_actor_slug() <> 'joe' then
    raise exception 'legacy schedule retirement requires Joe authority session';
  end if;
  if btrim(coalesce(p_reason,''))='' then raise exception 'cutover reason is required'; end if;
  update ops.job_definition set legacy_disabled_at=now(),legacy_disable_reason=p_reason,updated_at=now()
   where key=p_workflow_key
     and version=(select max(version) from ops.job_definition where key=p_workflow_key)
     and legacy_disabled_at is null;
  get diagnostics n=row_count;
  return n=1;
end $$;

revoke all on function ops.authority_actor_slug() from public;
revoke all on function ops.record_workflow_acceptance(text,text,text,text,text) from public, carr_writer;
revoke all on function ops.record_workflow_acceptance(text,text,text,text) from public, carr_writer;
revoke all on function ops.disable_legacy_schedule(text,text) from public, carr_writer;
revoke all on function ops.disable_legacy_schedule(text,text,text) from public, carr_writer;
grant execute on function ops.authority_actor_slug() to carr_authority;
grant execute on function ops.record_workflow_acceptance(text,text,text,text) to carr_authority;
grant execute on function ops.disable_legacy_schedule(text,text) to carr_authority;

-- These are the envelope/audit tables used only by the two authority MCP
-- verbs.  carr_authority receives no business-record table grant and is not a
-- member of carr_writer or carr_jobs.
grant select on actor, tool_call to carr_authority;
grant insert on tool_call, event to carr_authority;

commit;

do $$
declare definition text;
begin
  if has_function_privilege('carr_writer',
       'ops.record_workflow_acceptance(text,text,text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
       'ops.disable_legacy_schedule(text,text)'::regprocedure,'execute') then
    raise exception '0161 FAILED: routine writer retains control-plane authority execution';
  end if;
  select pg_get_functiondef('ops.record_workflow_acceptance(text,text,text,text)'::regprocedure)
    into definition;
  if definition not like '%authority_actor_slug()%'
     or definition like '%p_actor%' then
    raise exception '0161 FAILED: workflow acceptance still trusts caller actor text';
  end if;
end $$;
