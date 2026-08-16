-- 0164_control_plane_ledger_retry_modes.sql
-- A replacement is evaluated independently in shadow, canary, and live mode.
-- The old per-instant uniqueness constraint collapsed those separate ledger
-- runs, so canary could be refused merely because shadow was already queued.
-- Also make every expired lease auditable, including one that returns to retry.

begin;

alter table ops.job
  drop constraint if exists job_definition_key_definition_version_scheduled_for_key;
alter table ops.job
  add constraint job_one_schedule_per_mode
  unique (definition_key, definition_version, scheduled_for, mode);

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
    select t.id,t.attempt,
           case when t.state='dead_lettered' then 'dead_letter' else 'timeout' end,
           concat('lease-expired:',t.id,':',t.attempt),
           jsonb_build_object('failure_class','lease_expired','next_state',t.state)
      from transitioned t
    returning id
  )
  select count(*) into n from transitioned;
  return n;
end $$;

revoke all on function ops.reap_expired_jobs() from public;
grant execute on function ops.reap_expired_jobs() to carr_jobs;

do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conrelid='ops.job'::regclass and conname='job_one_schedule_per_mode'
  ) then
    raise exception '0164 FAILED: schedule uniqueness does not include mode';
  end if;
  if pg_get_functiondef('ops.reap_expired_jobs()'::regprocedure) not like '%else ''timeout''%'
     or pg_get_functiondef('ops.reap_expired_jobs()'::regprocedure) not like '%from transitioned t%'
  then
    raise exception '0164 FAILED: retryable expired leases lack timeout receipts';
  end if;
end $$;

commit;
