-- 0153_control_plane_resilience.sql
-- Phase 5 runtime controls: provider health routing, dependency-aware proposal
-- cache invalidation, and cost reservation before provider dispatch. All
-- mutations are narrow security-definer functions for carr_jobs; the routine
-- execution role owns no table and cannot rewrite receipts.

begin;

alter table ops.cognition_result_cache
  add column if not exists dependency_refs text[] not null default '{}';

create index if not exists cognition_cache_dependency_idx
  on ops.cognition_result_cache using gin(dependency_refs);

create table if not exists ops.cost_reservation (
  id                 uuid primary key default gen_random_uuid(),
  job_id             uuid not null references ops.job(id) on delete restrict,
  attempt            integer not null check (attempt > 0),
  route_key          text not null references ops.provider_route(route_key),
  estimated_cost_usd numeric(12,6) not null check (estimated_cost_usd >= 0),
  actual_cost_usd    numeric(12,6) check (actual_cost_usd is null or actual_cost_usd >= 0),
  state              text not null default 'reserved'
    check (state in ('reserved','settled','released')),
  created_at         timestamptz not null default now(),
  settled_at         timestamptz,
  unique(job_id,attempt,route_key),
  constraint settled_reservation_has_actual check (
    state <> 'settled' or (actual_cost_usd is not null and settled_at is not null)
  )
);

drop trigger if exists cost_reservation_no_delete on ops.cost_reservation;
create trigger cost_reservation_no_delete
  before delete on ops.cost_reservation for each row
  execute function ops.refuse_job_evidence_rewrite();

create or replace function ops.select_provider_routes(p_requested text[])
returns table(route_key text,priority integer,endpoint_ref text,health text)
language sql stable security definer set search_path=ops,public,pg_temp
as $$
  select v.route_key,v.priority,v.endpoint_ref,v.health
    from ops.v_provider_route v
   where v.route_key=any(p_requested) and v.enabled
     and v.health not in ('disabled','unavailable','rate_limited')
   order by array_position(p_requested,v.route_key),v.priority,v.route_key
$$;

create or replace function ops.record_provider_observation(
  p_route_key text,p_status text,p_latency_ms integer,p_error_class text,
  p_ttl_seconds integer,p_source_ref text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare rid uuid;
begin
  if p_ttl_seconds < 1 or btrim(coalesce(p_source_ref,''))='' then
    raise exception 'positive observation TTL and source_ref are required';
  end if;
  insert into ops.provider_observation
    (route_key,status,latency_ms,error_class,expires_at,source_ref)
  values(p_route_key,p_status,p_latency_ms,p_error_class,
         now()+make_interval(secs=>p_ttl_seconds),p_source_ref)
  returning id into rid;
  return rid;
end $$;

create or replace function ops.get_cognition_cache(p_cache_key text)
returns jsonb
language sql stable security definer set search_path=ops,public,pg_temp
as $$
  select proposal from ops.cognition_result_cache
   where cache_key=p_cache_key and invalidated_at is null and expires_at>now()
$$;

create or replace function ops.put_cognition_cache(
  p_cache_key text,p_cognition_key text,p_cognition_version integer,
  p_output_schema_version integer,p_proposal jsonb,p_dependency_refs text[],
  p_ttl_seconds integer
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
begin
  if p_ttl_seconds < 1 then raise exception 'positive cache TTL is required'; end if;
  insert into ops.cognition_result_cache
    (cache_key,cognition_key,cognition_version,output_schema_version,proposal,
     dependency_refs,validated_at,expires_at)
  values(p_cache_key,p_cognition_key,p_cognition_version,p_output_schema_version,p_proposal,
         coalesce(p_dependency_refs,'{}'),now(),now()+make_interval(secs=>p_ttl_seconds))
  on conflict(cache_key) do update set
    proposal=excluded.proposal,dependency_refs=excluded.dependency_refs,
    validated_at=excluded.validated_at,expires_at=excluded.expires_at,invalidated_at=null;
  return true;
end $$;

create or replace function ops.invalidate_cognition_cache(p_dependency_ref text)
returns integer
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  update ops.cognition_result_cache set invalidated_at=now()
   where invalidated_at is null and p_dependency_ref=any(dependency_refs);
  get diagnostics n=row_count;
  return n;
end $$;

create or replace function ops.reserve_job_cost(
  p_job_id uuid,p_lease_token uuid,p_route_key text,p_estimated_cost_usd numeric
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; route ops.provider_route%rowtype; spent numeric; reserved numeric; rid uuid;
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
    raise exception 'provider route % monthly budget would be exceeded',p_route_key;
  end if;
  insert into ops.cost_reservation(job_id,attempt,route_key,estimated_cost_usd)
    values(j.id,j.attempt,p_route_key,p_estimated_cost_usd)
  returning id into rid;
  return rid;
end $$;

create or replace function ops.settle_job_cost(
  p_reservation_id uuid,p_job_id uuid,p_lease_token uuid,
  p_input_tokens integer,p_output_tokens integer,p_actual_cost_usd numeric
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; r ops.cost_reservation%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select * into r from ops.cost_reservation where id=p_reservation_id for update;
  if not found or r.job_id<>j.id or r.attempt<>j.attempt or r.state<>'reserved' then
    raise exception 'cost reservation does not belong to this live attempt';
  end if;
  if p_actual_cost_usd<0 or p_actual_cost_usd>r.estimated_cost_usd then
    raise exception 'actual cost exceeds admitted reservation';
  end if;
  update ops.cost_reservation set state='settled',actual_cost_usd=p_actual_cost_usd,
         settled_at=now() where id=r.id;
  update ops.job_attempt set provider_route=r.route_key,input_tokens=p_input_tokens,
         output_tokens=p_output_tokens,cost_usd=p_actual_cost_usd
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  return true;
end $$;

revoke all on function ops.select_provider_routes(text[]) from public;
revoke all on function ops.record_provider_observation(text,text,integer,text,integer,text) from public;
revoke all on function ops.get_cognition_cache(text) from public;
revoke all on function ops.put_cognition_cache(text,text,integer,integer,jsonb,text[],integer) from public;
revoke all on function ops.invalidate_cognition_cache(text) from public;
revoke all on function ops.reserve_job_cost(uuid,uuid,text,numeric) from public;
revoke all on function ops.settle_job_cost(uuid,uuid,uuid,integer,integer,numeric) from public;

grant select on ops.cost_reservation to carr_reader,carr_writer,carr_jobs;
grant execute on function
  ops.select_provider_routes(text[]),
  ops.record_provider_observation(text,text,integer,text,integer,text),
  ops.get_cognition_cache(text),
  ops.put_cognition_cache(text,text,integer,integer,jsonb,text[],integer),
  ops.reserve_job_cost(uuid,uuid,text,numeric),
  ops.settle_job_cost(uuid,uuid,uuid,integer,integer,numeric)
to carr_jobs;
grant execute on function ops.invalidate_cognition_cache(text) to carr_writer,carr_jobs;

commit;

do $$
begin
  if to_regclass('ops.cost_reservation') is null then
    raise exception '0153 FAILED: cost reservation ledger missing';
  end if;
  if has_table_privilege('carr_jobs','ops.cost_reservation','delete') then
    raise exception '0153 FAILED: carr_jobs can delete cost evidence';
  end if;
end $$;
