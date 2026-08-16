-- 0160_reap_expired_job_locking.sql
-- A reaper must claim the expired job row before it marks its attempt timed
-- out.  The prior two-statement form could mark an attempt timed_out, then
-- race a lease heartbeat that renewed the job before the state transition.
-- Lock/select first, then transition the attempt and job from that same finite
-- set so one live lease cannot acquire contradictory attempt evidence.

begin;

create or replace function ops.reap_expired_jobs()
returns integer
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  with expired as materialized (
    select j.id,j.attempt
      from ops.job j
     where j.state='running' and j.leased_until < now()
     for update skip locked
  ), attempts as (
    update ops.job_attempt a
       set state='timed_out',ended_at=now(),failure_class='lease_expired',detail='lease expired'
      from expired e
     where a.job_id=e.id and a.attempt=e.attempt and a.state='running'
    returning a.job_id
  ), transitioned as (
    update ops.job j
       set state=case when j.attempt < j.max_attempts then 'retry_wait' else 'dead_lettered' end,
           next_attempt_at=case when j.attempt < j.max_attempts
                                then now()+make_interval(secs=>ops.retry_delay_seconds(j))
                                else j.next_attempt_at end,
           ended_at=case when j.attempt < j.max_attempts then null else now() end,
           last_failure_class='lease_expired',last_failure_detail='lease expired',
           lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
      from expired e
     where j.id=e.id
    returning j.id,j.attempt,j.state
  ), receipts as (
    insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    select t.id,t.attempt,'dead_letter',concat('lease-expired:',t.id,':',t.attempt),
           jsonb_build_object('failure_class','lease_expired','next_state',t.state)
      from transitioned t where t.state='dead_lettered'
    returning id
  )
  select count(*) into n from transitioned;
  return n;
end $$;

revoke all on function ops.reap_expired_jobs() from public;
grant execute on function ops.reap_expired_jobs() to carr_jobs;

-- A scheduled shadow/canary adapter may consume only jobs from its own mode.
-- Generic manual run-once remains mode-agnostic by calling ops.claim_job.
create or replace function ops.claim_job_mode(
  p_worker text,p_mode text,p_limit integer default 1,p_lease_seconds integer default 300
) returns table (
  job_id uuid, lease_token uuid, definition_key text, definition_version integer,
  payload jsonb, execution_kind text, execution_contract jsonb,
  attempt integer, timeout_seconds integer, mode text
)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_mode not in ('shadow','canary','live','replay')
     or p_limit < 1 or p_lease_seconds < 1 then
    raise exception 'worker, valid mode, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id from ops.job j
     where j.state in ('queued','retry_wait') and j.next_attempt_at <= now() and j.mode=p_mode
     order by j.scheduled_for,j.created_at
     for update skip locked limit p_limit
  ), claimed as (
    update ops.job j set
      state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
    from candidate c where j.id=c.id
    returning j.*
  ), attempts(claimed_job_id) as (
    insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning ops.job_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c
    join ops.job_definition d on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.claimed_job_id=c.id;
end $$;

revoke all on function ops.claim_job_mode(text,text,integer,integer) from public;
grant execute on function ops.claim_job_mode(text,text,integer,integer) to carr_jobs;

-- Cutover acceptance names a completion receipt produced by the replacement
-- workflow in the required mode.  A human can accept or reject that result,
-- but cannot manufacture shadow/canary evidence out of an arbitrary string.
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
    if not exists (
      select 1
        from ops.job j join ops.job_receipt r on r.job_id=j.id
       where j.definition_key=p_workflow_key and j.definition_version=v
         and j.mode=p_mode and r.kind='completion' and r.receipt_ref=p_receipt_ref
    ) then
      raise exception 'accepted workflow evidence must name a completion receipt from the matching workflow and mode';
    end if;
  end if;
  insert into ops.workflow_acceptance
    (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
  values(p_workflow_key,v,p_mode,p_status,p_receipt_ref,
         case when p_status='accepted' then human_actor.slug else null end)
  returning id into rid;
  return rid;
end $$;

revoke all on function ops.record_workflow_acceptance(text,text,text,text,text) from public;
grant execute on function ops.record_workflow_acceptance(text,text,text,text,text) to carr_writer;

commit;

do $$
declare definition text;
begin
  select pg_get_functiondef('ops.reap_expired_jobs()'::regprocedure) into definition;
  if definition not like '%for update skip locked%'
     or position('with expired as materialized' in lower(definition)) = 0
     or position('update ops.job_attempt' in lower(definition))
        < position('with expired as materialized' in lower(definition)) then
    raise exception '0160 FAILED: expired-job reaper does not lock jobs before attempt transition';
  end if;
  select pg_get_functiondef('ops.record_workflow_acceptance(text,text,text,text,text)'::regprocedure)
    into definition;
  if definition not like '%ops.job_receipt%'
     or definition not like '%matching workflow and mode%' then
    raise exception '0160 FAILED: cutover acceptance is not bound to a matching completion receipt';
  end if;
  if to_regprocedure('ops.claim_job_mode(text,text,integer,integer)') is null then
    raise exception '0160 FAILED: mode-filtered claim function missing';
  end if;
end $$;
