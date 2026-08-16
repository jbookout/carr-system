-- 0154_control_plane_cost_release.sql
-- A failed provider attempt must release its pre-dispatch reservation before
-- failover. The release is lease-bound and remains as evidence; it never deletes
-- or rewrites a settled charge.

begin;

create or replace function ops.release_job_cost(
  p_reservation_id uuid,p_job_id uuid,p_lease_token uuid
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; n integer;
begin
  select * into j from ops.job where id=p_job_id;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  update ops.cost_reservation set state='released',settled_at=now()
   where id=p_reservation_id and job_id=j.id and attempt=j.attempt and state='reserved';
  get diagnostics n=row_count;
  return n=1;
end $$;

revoke all on function ops.release_job_cost(uuid,uuid,uuid) from public;
grant execute on function ops.release_job_cost(uuid,uuid,uuid) to carr_jobs;

commit;

do $$
begin
  if to_regprocedure('ops.release_job_cost(uuid,uuid,uuid)') is null then
    raise exception '0154 FAILED: cost release function missing';
  end if;
end $$;
