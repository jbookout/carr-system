-- 0169_control_plane_canary_fencing.sql
-- A disabled definition is revoked authority, not merely hidden metadata.
-- Claims must therefore join only the enabled definition, and disabling a
-- version must cancel every not-yet-running job for that exact version with an
-- immutable receipt.  This fences an old worker checkout at the database
-- boundary after a versioned rollout.  Already-running work is intentionally
-- not rewritten: deployment must drain workers before definition sync because
-- a database cannot undo an external command that has already started.

begin;

create or replace function ops.fence_definition_jobs(
  p_definition_key text,p_definition_version integer
) returns integer
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  with transitioned as (
    update ops.job j
       set state='cancelled',ended_at=now(),
           last_failure_class='definition_disabled',
           last_failure_detail='definition version disabled before dispatch',
           lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
     where j.definition_key=p_definition_key
       and j.definition_version=p_definition_version
       and j.state in ('queued','retry_wait')
    returning j.id,j.attempt,j.definition_key,j.definition_version
  ), receipts as (
    insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    select t.id,t.attempt,'override',
           concat('definition-disabled:',t.id,':v',t.definition_version),
           jsonb_build_object(
             'failure_class','definition_disabled',
             'definition_key',t.definition_key,
             'definition_version',t.definition_version,
             'next_state','cancelled')
      from transitioned t
    returning id
  )
  select count(*) into n from receipts;
  return n;
end $$;

revoke all on function ops.fence_definition_jobs(text,integer) from public;

create or replace function ops.fence_jobs_when_definition_disabled()
returns trigger
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
begin
  if old.enabled and not new.enabled then
    perform ops.fence_definition_jobs(new.key,new.version);
  end if;
  return new;
end $$;

drop trigger if exists job_definition_fence_queued_jobs on ops.job_definition;
create trigger job_definition_fence_queued_jobs
  after update of enabled on ops.job_definition
  for each row execute function ops.fence_jobs_when_definition_disabled();

-- Fence anything already tied to a disabled version when this migration is
-- installed.  Re-running is free because only queued/retry_wait rows move.
select ops.fence_definition_jobs(key,version)
  from ops.job_definition
 where not enabled;

create or replace function ops.claim_job(
  p_worker text,
  p_limit integer default 1,
  p_lease_seconds integer default 300
) returns table (
  job_id uuid, lease_token uuid, definition_key text, definition_version integer,
  payload jsonb, execution_kind text, execution_contract jsonb,
  attempt integer, timeout_seconds integer, mode text
)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_limit < 1 or p_lease_seconds < 1 then
    raise exception 'worker, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id
      from ops.job j
      join ops.job_definition d
        on d.key=j.definition_key and d.version=j.definition_version
     where d.enabled and j.state in ('queued','retry_wait')
       and j.next_attempt_at <= now()
     order by j.scheduled_for,j.created_at
     for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set
      state='running',attempt=j.attempt+1,lease_owner=p_worker,
      lease_token=gen_random_uuid(),
      leased_until=now()+make_interval(secs=>p_lease_seconds),
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
    select j.id
      from ops.job j
      join ops.job_definition d
        on d.key=j.definition_key and d.version=j.definition_version
     where d.enabled and j.state in ('queued','retry_wait')
       and j.next_attempt_at <= now() and j.mode=p_mode
     order by j.scheduled_for,j.created_at
     for update of j,d skip locked limit p_limit
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

revoke all on function ops.claim_job(text,integer,integer) from public;
revoke all on function ops.claim_job_mode(text,text,integer,integer) from public;
grant execute on function ops.claim_job(text,integer,integer) to carr_jobs;
grant execute on function ops.claim_job_mode(text,text,integer,integer) to carr_jobs;

do $$
begin
  if position('d.enabled' in pg_get_functiondef(
       'ops.claim_job(text,integer,integer)'::regprocedure))=0
     or position('d.enabled' in pg_get_functiondef(
       'ops.claim_job_mode(text,text,integer,integer)'::regprocedure))=0 then
    raise exception '0169 FAILED: disabled definitions remain claimable';
  end if;
  if not exists (
    select 1 from pg_trigger
     where tgrelid='ops.job_definition'::regclass
       and tgname='job_definition_fence_queued_jobs'
       and not tgisinternal
  ) then
    raise exception '0169 FAILED: definition disable fencing trigger missing';
  end if;
end $$;

commit;
