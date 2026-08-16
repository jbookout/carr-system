-- 0165_control_plane_cost_refusals.sql
-- Budget refusal is a durable pre-dispatch decision, not an exception whose
-- transaction rollback erases the measurement.  The typed admission function
-- lets the runner stop before a provider call while preserving one immutable
-- refusal per job attempt and route.

begin;

create table ops.cost_refusal (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id) on delete restrict,
  attempt integer not null check (attempt > 0),
  route_key text not null references ops.provider_route(route_key),
  estimated_cost_usd numeric(12,6) not null check (estimated_cost_usd >= 0),
  monthly_budget_usd numeric(12,4) not null check (monthly_budget_usd >= 0),
  spent_usd numeric(12,6) not null check (spent_usd >= 0),
  reserved_usd numeric(12,6) not null check (reserved_usd >= 0),
  reason text not null check (reason in ('monthly_budget_exceeded')),
  refused_at timestamptz not null default now(),
  unique (job_id,attempt,route_key)
);

create trigger cost_refusal_append_only
  before update or delete on ops.cost_refusal
  for each row execute function ops.refuse_job_evidence_rewrite();

create or replace function ops.admit_job_cost(
  p_job_id uuid,p_lease_token uuid,p_route_key text,p_estimated_cost_usd numeric
) returns table (
  admitted boolean,reservation_id uuid,refusal_id uuid,reason text
)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; route ops.provider_route%rowtype;
        spent numeric; reserved numeric; rid uuid; refusal ops.cost_refusal%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select * into route from ops.provider_route where route_key=p_route_key and enabled;
  if not found then raise exception 'provider route % is not enabled',p_route_key; end if;
  if p_estimated_cost_usd<0 then raise exception 'estimated cost must be non-negative'; end if;
  select coalesce(sum(a.cost_usd),0) into spent from ops.job_attempt a
   where a.provider_route=p_route_key and a.started_at>=date_trunc('month',now());
  select coalesce(sum(r.estimated_cost_usd),0) into reserved from ops.cost_reservation r
   where r.route_key=p_route_key and r.state='reserved'
     and r.created_at>=date_trunc('month',now());
  if route.monthly_budget_usd is not null
     and spent+reserved+p_estimated_cost_usd>route.monthly_budget_usd then
    insert into ops.cost_refusal
      (job_id,attempt,route_key,estimated_cost_usd,monthly_budget_usd,spent_usd,reserved_usd,reason)
    values
      (j.id,j.attempt,p_route_key,p_estimated_cost_usd,route.monthly_budget_usd,spent,reserved,
       'monthly_budget_exceeded')
    on conflict (job_id,attempt,route_key) do nothing
    returning * into refusal;
    if refusal.id is null then
      select * into refusal from ops.cost_refusal
       where job_id=j.id and attempt=j.attempt and route_key=p_route_key;
    end if;
    return query select false,null::uuid,refusal.id,refusal.reason;
    return;
  end if;
  insert into ops.cost_reservation(job_id,attempt,route_key,estimated_cost_usd)
    values(j.id,j.attempt,p_route_key,p_estimated_cost_usd)
  returning id into rid;
  return query select true,rid,null::uuid,null::text;
end $$;

create or replace view ops.v_cost_refusal_metric as
select date_trunc('month',refused_at) as month,route_key,reason,
       count(*) as refusal_count,
       sum(estimated_cost_usd) as refused_estimated_cost_usd,
       max(refused_at) as last_refused_at
  from ops.cost_refusal
 group by 1,2,3;

revoke all on ops.cost_refusal from public;
revoke all on function ops.admit_job_cost(uuid,uuid,text,numeric) from public;
grant select on ops.cost_refusal,ops.v_cost_refusal_metric to carr_reader,carr_writer,carr_jobs;
grant execute on function ops.admit_job_cost(uuid,uuid,text,numeric) to carr_jobs;

do $$
begin
  if to_regclass('ops.cost_refusal') is null
     or to_regclass('ops.v_cost_refusal_metric') is null
     or to_regprocedure('ops.admit_job_cost(uuid,uuid,text,numeric)') is null then
    raise exception '0165 FAILED: budget-refusal evidence contract missing';
  end if;
  if has_table_privilege('carr_jobs','ops.cost_refusal','insert')
     or has_table_privilege('carr_jobs','ops.cost_refusal','update')
     or has_table_privilege('carr_jobs','ops.cost_refusal','delete') then
    raise exception '0165 FAILED: jobs role can rewrite budget-refusal evidence';
  end if;
end $$;

commit;
