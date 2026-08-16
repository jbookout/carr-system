-- 0150_control_plane_job_fixes.sql
-- Staging acceptance of 0149 exposed a PL/pgSQL output-column collision in
-- claim_job. Keep the forward-only ledger honest: repair it here rather than
-- rewriting the already-applied migration. Also make lease exhaustion produce
-- the immutable dead-letter receipt required for an auditable terminal state.

begin;

create or replace function ops.reap_expired_jobs()
returns integer
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  update ops.job_attempt a
     set state='timed_out', ended_at=now(), failure_class='lease_expired', detail='lease expired'
    from ops.job j
   where a.job_id=j.id and a.attempt=j.attempt and a.state='running'
     and j.state='running' and j.leased_until < now();

  with expired as (
    select j.id from ops.job j
     where j.state='running' and j.leased_until < now()
     for update
  ), transitioned as (
    update ops.job j
       set state=case when attempt < max_attempts then 'retry_wait' else 'dead_lettered' end,
           next_attempt_at=case when attempt < max_attempts
                                then now()+make_interval(secs=>ops.retry_delay_seconds(j))
                                else next_attempt_at end,
           ended_at=case when attempt < max_attempts then null else now() end,
           last_failure_class='lease_expired', last_failure_detail='lease expired',
           lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
      from expired e where j.id=e.id
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
    select j.id from ops.job j
     where j.state in ('queued','retry_wait') and j.next_attempt_at <= now()
     order by j.scheduled_for,j.created_at
     for update skip locked limit p_limit
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

revoke all on function ops.reap_expired_jobs() from public;
grant execute on function ops.reap_expired_jobs() to carr_jobs;

commit;

do $$
begin
  if to_regprocedure('ops.reap_expired_jobs()') is null
     or to_regprocedure('ops.claim_job(text,integer,integer)') is null then
    raise exception '0150 FAILED: corrected lease functions missing';
  end if;
end $$;
