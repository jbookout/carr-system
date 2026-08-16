-- 0157_control_plane_runtime_guards.sql
-- Close the staging-discovered gaps around duplicate scheduler delivery,
-- expired/long leases, explicit timeout evidence, and human cutover authority.

begin;

create or replace function ops.enqueue_job(
  p_definition_key text,
  p_definition_version integer,
  p_scheduled_for timestamptz,
  p_payload jsonb,
  p_idempotency_key text,
  p_mode text default 'live'
) returns ops.job
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  d ops.job_definition%rowtype;
  j ops.job%rowtype;
begin
  select * into d from ops.job_definition
   where key=p_definition_key and version=p_definition_version and enabled;
  if not found then
    raise exception 'job definition % v% is not enabled',p_definition_key,p_definition_version;
  end if;
  if p_mode not in ('shadow','canary','live','replay') then
    raise exception 'invalid job mode %',p_mode;
  end if;
  insert into ops.job
    (definition_key,definition_version,idempotency_key,scheduled_for,mode,payload,
     max_attempts,timeout_seconds)
  values
    (d.key,d.version,p_idempotency_key,p_scheduled_for,p_mode,coalesce(p_payload,'{}'::jsonb),
     (d.retry_policy->>'max_attempts')::integer,
     (d.retry_policy->>'timeout_seconds')::integer)
  on conflict do nothing
  returning * into j;
  if j.id is null then
    select * into j from ops.job
     where idempotency_key=p_idempotency_key
        or (definition_key=p_definition_key
            and definition_version=p_definition_version
            and scheduled_for=p_scheduled_for)
     order by (idempotency_key=p_idempotency_key) desc
     limit 1;
    if j.id is null
       or j.definition_key <> p_definition_key
       or j.definition_version <> p_definition_version
       or j.scheduled_for <> p_scheduled_for
       or j.payload <> coalesce(p_payload,'{}'::jsonb)
       or j.mode <> p_mode then
      raise exception 'duplicate delivery conflicts with the canonical scheduled job';
    end if;
  end if;
  return j;
end $$;

create or replace function ops.fail_job(
  p_job_id uuid,p_lease_token uuid,p_failure_class text,p_detail text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; next_state text;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token
     or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  next_state := case when j.attempt < j.max_attempts then 'retry_wait' else 'dead_lettered' end;
  update ops.job_attempt set state='failed',ended_at=now(),
         failure_class=p_failure_class,detail=p_detail
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state=next_state,
         next_attempt_at=case when next_state='retry_wait'
                              then now()+make_interval(secs=>ops.retry_delay_seconds(j))
                              else next_attempt_at end,
         ended_at=case when next_state='dead_lettered' then now() else null end,
         last_failure_class=p_failure_class,last_failure_detail=p_detail,
         lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
   where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,case when next_state='dead_lettered' then 'dead_letter' else 'failure' end,
           concat('failure:',j.id,':',j.attempt),
           jsonb_build_object('failure_class',p_failure_class,'detail',p_detail,'next_state',next_state));
  return next_state;
end $$;

create or replace function ops.timeout_job(
  p_job_id uuid,p_lease_token uuid,p_detail text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; next_state text;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token
     or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  next_state := case when j.attempt < j.max_attempts then 'retry_wait' else 'dead_lettered' end;
  update ops.job_attempt set state='timed_out',ended_at=now(),
         failure_class='execution_timeout',detail=p_detail
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state=next_state,
         next_attempt_at=case when next_state='retry_wait'
                              then now()+make_interval(secs=>ops.retry_delay_seconds(j))
                              else next_attempt_at end,
         ended_at=case when next_state='dead_lettered' then now() else null end,
         last_failure_class='execution_timeout',last_failure_detail=p_detail,
         lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
   where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,case when next_state='dead_lettered' then 'dead_letter' else 'timeout' end,
           concat('timeout:',j.id,':',j.attempt),
           jsonb_build_object('failure_class','execution_timeout','detail',p_detail,'next_state',next_state));
  return next_state;
end $$;

create or replace function ops.record_workflow_acceptance(
  p_workflow_key text,p_mode text,p_status text,p_receipt_ref text,p_actor text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare v integer; rid uuid; human_actor actor%rowtype;
begin
  select version into v from ops.job_definition
   where key=p_workflow_key order by version desc limit 1;
  if v is null then raise exception 'unknown workflow %',p_workflow_key; end if;
  if p_status='accepted' then
    select * into human_actor from actor
     where slug=p_actor and kind='human' and active;
    if not found then
      raise exception 'accepted workflow evidence requires an active human actor';
    end if;
  end if;
  insert into ops.workflow_acceptance
    (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
  values(p_workflow_key,v,p_mode,p_status,p_receipt_ref,
         case when p_status='accepted' then human_actor.slug else null end)
  returning id into rid;
  return rid;
end $$;

create or replace function ops.disable_legacy_schedule(
  p_workflow_key text,p_reason text,p_actor text
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  if btrim(coalesce(p_reason,''))='' then raise exception 'cutover reason is required'; end if;
  if not exists(select 1 from actor where slug=p_actor and slug='joe' and kind='human' and active) then
    raise exception 'legacy schedule retirement requires Joe approval';
  end if;
  update ops.job_definition set legacy_disabled_at=now(),legacy_disable_reason=p_reason,updated_at=now()
   where key=p_workflow_key
     and version=(select max(version) from ops.job_definition where key=p_workflow_key)
     and legacy_disabled_at is null;
  get diagnostics n=row_count;
  return n=1;
end $$;

create or replace function ops.require_rule_admission()
returns trigger language plpgsql as $$
declare a ops.rule_admission%rowtype; n_controls integer;
begin
  if not (new.status='active' and
          (tg_op='INSERT' or old.status is distinct from 'active')) then
    return new;
  end if;
  select * into a from ops.rule_admission where rule_id=new.id;
  if not found or a.state <> 'admitted' then
    raise exception 'rule % cannot activate: admitted rule contract is missing',new.id;
  end if;
  if a.enforcement_class='machine_enforceable' then
    select count(*) into n_controls from ops.rule_enforcement_point
     where rule_id=new.id and installed;
    if n_controls=0 then
      raise exception 'rule % cannot activate: no installed enforcement point',new.id;
    end if;
  end if;
  if new.activated_by is null then
    raise exception 'rule % cannot activate without a human activator',new.id;
  end if;
  return new;
end $$;

drop trigger if exists rule_activation_requires_admission on rule;
create trigger rule_activation_requires_admission
  before insert or update of status on rule
  for each row execute function ops.require_rule_admission();

revoke all on function ops.timeout_job(uuid,uuid,text) from public;
revoke all on function ops.disable_legacy_schedule(text,text) from public;
revoke all on function ops.disable_legacy_schedule(text,text,text) from public;
grant execute on function ops.timeout_job(uuid,uuid,text) to carr_jobs;
grant execute on function ops.disable_legacy_schedule(text,text,text) to carr_writer;

commit;

do $$
begin
  if to_regprocedure('ops.timeout_job(uuid,uuid,text)') is null
     or to_regprocedure('ops.disable_legacy_schedule(text,text,text)') is null then
    raise exception '0157 FAILED: runtime guard functions missing';
  end if;
end $$;
