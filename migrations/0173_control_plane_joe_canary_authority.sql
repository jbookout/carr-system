-- 0173_control_plane_joe_canary_authority.sql
-- Accepted canary evidence is the production-cutover decision.  It belongs to
-- Joe; Dell remains an admitted human authority for shadow acceptance.

begin;

create or replace function ops.record_workflow_acceptance(
  p_workflow_key text,p_mode text,p_status text,p_receipt_ref text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare v integer; rid uuid; authority_actor text;
begin
  authority_actor := ops.authority_actor_slug();
  if p_status='accepted' and p_mode='canary' and authority_actor <> 'joe' then
    raise exception 'accepted canary workflow evidence requires Joe authority session';
  end if;
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

comment on function ops.record_workflow_acceptance(text,text,text,text) is
  'Control Plane: accepted canary evidence requires Joe; shadow acceptance remains available to admitted human authority sessions.';

revoke all on function ops.record_workflow_acceptance(text,text,text,text) from public, carr_writer;
grant execute on function ops.record_workflow_acceptance(text,text,text,text) to carr_authority;

commit;

do $$
declare definition text;
begin
  select pg_get_functiondef('ops.record_workflow_acceptance(text,text,text,text)'::regprocedure)
    into definition;
  definition := regexp_replace(lower(definition), '\s+', '', 'g');
  if definition not like '%p_mode=''canary''%' or definition not like '%authority_actor<>''joe''%' then
    raise exception '0173 FAILED: accepted canary workflow authority is not Joe-only';
  end if;
end $$;
