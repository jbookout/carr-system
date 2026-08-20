-- 0195_control_plane_cache_observations.sql
-- Phase 5 measurement substrate.  Cache reads/stores/invalidation are immutable
-- ledger evidence, not a mutable dashboard counter.  The jobs role can append
-- only through lease-bound functions and cannot write the table directly.

begin;

create table ops.cognition_cache_observation (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id) on delete restrict,
  attempt integer not null check (attempt > 0),
  workflow_key text not null,
  workflow_version integer not null check (workflow_version > 0),
  mode text not null check (mode in ('shadow','canary','live','replay')),
  cache_key text not null check (btrim(cache_key) <> ''),
  observation_kind text not null check (observation_kind in ('hit','miss','store','invalidate','invalidated','expired')),
  dependency_ref text,
  observed_at timestamptz not null default now(),
  foreign key (workflow_key,workflow_version) references ops.job_definition(key,version),
  unique (job_id,attempt,cache_key,observation_kind),
  constraint cache_invalidation_names_dependency check (
    (observation_kind='invalidate' and btrim(coalesce(dependency_ref,'')) <> '')
    or (observation_kind <> 'invalidate' and dependency_ref is null)
  )
);

create index cognition_cache_observation_window_idx
  on ops.cognition_cache_observation(mode,observed_at,workflow_key,workflow_version);

create trigger cognition_cache_observation_append_only
  before update or delete on ops.cognition_cache_observation
  for each row execute function ops.refuse_job_evidence_rewrite();

create or replace function ops.get_cognition_cache_for_job(
  p_job_id uuid,p_lease_token uuid,p_cache_key text
) returns table(cache_state text,proposal jsonb)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; c ops.cognition_result_cache%rowtype; observed_kind text; execution_kind text;
        declared_cognition_key text; active_contract_count integer; contract ops.cognition_job%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select d.execution_kind,d.execution_contract->>'cognition_job' into execution_kind,declared_cognition_key from ops.job_definition d
   where d.key=j.definition_key and d.version=j.definition_version;
  if execution_kind is distinct from 'cognition' then
    raise exception 'job % is not a cognition workflow',p_job_id;
  end if;
  select count(*) into active_contract_count from ops.cognition_job
   where key=declared_cognition_key and active;
  if active_contract_count <> 1 then raise exception 'job % lacks one active cognition contract',p_job_id; end if;
  select * into contract from ops.cognition_job where key=declared_cognition_key and active;
  if btrim(coalesce(p_cache_key,''))='' then raise exception 'cache key is required'; end if;
  select * into c from ops.cognition_result_cache where cache_key=p_cache_key;
  observed_kind := case when c.cache_key is null then 'miss'
                        when c.cognition_key<>contract.key or c.cognition_version<>contract.version
                          or c.output_schema_version<>contract.output_schema_version then 'miss'
                        when c.invalidated_at is not null then 'invalidated'
                        when c.expires_at<=now() then 'expired' else 'hit' end;
  insert into ops.cognition_cache_observation
    (job_id,attempt,workflow_key,workflow_version,mode,cache_key,observation_kind)
  values (j.id,j.attempt,j.definition_key,j.definition_version,j.mode,p_cache_key,observed_kind)
  on conflict (job_id,attempt,cache_key,observation_kind) do nothing;
  return query select observed_kind,case when observed_kind='hit' then c.proposal else null end;
end $$;

create or replace function ops.put_cognition_cache_for_job(
  p_job_id uuid,p_lease_token uuid,p_cache_key text,p_cognition_key text,
  p_cognition_version integer,p_output_schema_version integer,p_proposal jsonb,
  p_dependency_refs text[],p_ttl_seconds integer
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; execution_kind text; declared_cognition_key text; active_contract_count integer; contract ops.cognition_job%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select d.execution_kind,d.execution_contract->>'cognition_job'
    into execution_kind,declared_cognition_key from ops.job_definition d
   where d.key=j.definition_key and d.version=j.definition_version;
  if execution_kind is distinct from 'cognition'
     or declared_cognition_key is distinct from p_cognition_key then
    raise exception 'job % is not bound to cognition contract %',p_job_id,p_cognition_key;
  end if;
  select count(*) into active_contract_count from ops.cognition_job where key=declared_cognition_key and active;
  if active_contract_count <> 1 then raise exception 'job % lacks one active cognition contract',p_job_id; end if;
  select * into contract from ops.cognition_job where key=declared_cognition_key and active;
  if p_cognition_key<>contract.key or p_cognition_version<>contract.version
     or p_output_schema_version<>contract.output_schema_version then
    raise exception 'cache write does not match active cognition contract';
  end if;
  if btrim(coalesce(p_cache_key,''))='' or p_ttl_seconds<1 then
    raise exception 'cache key and positive cache TTL are required';
  end if;
  insert into ops.cognition_result_cache
    (cache_key,cognition_key,cognition_version,output_schema_version,proposal,
     dependency_refs,validated_at,expires_at)
  values (p_cache_key,p_cognition_key,p_cognition_version,p_output_schema_version,p_proposal,
          coalesce(p_dependency_refs,'{}'),now(),now()+make_interval(secs=>p_ttl_seconds))
  on conflict(cache_key) do update set proposal=excluded.proposal,
    dependency_refs=excluded.dependency_refs,validated_at=excluded.validated_at,
    expires_at=excluded.expires_at,invalidated_at=null;
  insert into ops.cognition_cache_observation
    (job_id,attempt,workflow_key,workflow_version,mode,cache_key,observation_kind)
  values (j.id,j.attempt,j.definition_key,j.definition_version,j.mode,p_cache_key,'store')
  on conflict (job_id,attempt,cache_key,observation_kind) do nothing;
  return true;
end $$;

create or replace function ops.invalidate_cognition_cache_for_job(
  p_job_id uuid,p_lease_token uuid,p_dependency_ref text
) returns integer
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; n integer; execution_kind text;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state<>'running' or j.lease_token<>p_lease_token or j.leased_until<now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  select d.execution_kind into execution_kind from ops.job_definition d
   where d.key=j.definition_key and d.version=j.definition_version;
  if execution_kind is distinct from 'cognition' then
    raise exception 'job % is not a cognition workflow',p_job_id;
  end if;
  if btrim(coalesce(p_dependency_ref,''))='' then raise exception 'dependency ref is required'; end if;
  with changed as (
    update ops.cognition_result_cache set invalidated_at=now()
     where invalidated_at is null and p_dependency_ref=any(dependency_refs)
     returning cache_key
  ), evidence as (
    insert into ops.cognition_cache_observation
      (job_id,attempt,workflow_key,workflow_version,mode,cache_key,observation_kind,dependency_ref)
    select j.id,j.attempt,j.definition_key,j.definition_version,j.mode,cache_key,'invalidate',p_dependency_ref
      from changed
    on conflict (job_id,attempt,cache_key,observation_kind) do nothing
    returning 1
  ) select count(*) into n from evidence;
  return n;
end $$;

revoke all on ops.cognition_cache_observation from public;
revoke all on function ops.get_cognition_cache_for_job(uuid,uuid,text) from public;
revoke all on function ops.put_cognition_cache_for_job(uuid,uuid,text,text,integer,integer,jsonb,text[],integer) from public;
revoke all on function ops.invalidate_cognition_cache_for_job(uuid,uuid,text) from public;
grant select on ops.cognition_cache_observation to carr_reader,carr_writer,carr_jobs;
grant execute on function ops.get_cognition_cache_for_job(uuid,uuid,text),
  ops.put_cognition_cache_for_job(uuid,uuid,text,text,integer,integer,jsonb,text[],integer),
  ops.invalidate_cognition_cache_for_job(uuid,uuid,text) to carr_jobs;

commit;

do $$
begin
  if to_regclass('ops.cognition_cache_observation') is null
     or to_regprocedure('ops.get_cognition_cache_for_job(uuid,uuid,text)') is null
     or to_regprocedure('ops.put_cognition_cache_for_job(uuid,uuid,text,text,integer,integer,jsonb,text[],integer)') is null
     or to_regprocedure('ops.invalidate_cognition_cache_for_job(uuid,uuid,text)') is null then
    raise exception '0195 FAILED: cache observation evidence contract missing';
  end if;
  if has_table_privilege('carr_jobs','ops.cognition_cache_observation','insert')
     or has_table_privilege('carr_jobs','ops.cognition_cache_observation','update')
     or has_table_privilege('carr_jobs','ops.cognition_cache_observation','delete') then
    raise exception '0195 FAILED: jobs role can rewrite cache observations';
  end if;
end $$;
