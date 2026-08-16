-- 0149_control_plane_jobs.sql
-- Phases 2, 3 and 5 substrate: the CARR ledger owns work identity and state;
-- schedulers, queues, devices and model providers are replaceable adapters.

begin;

create table if not exists ops.provider_route (
  route_key          text primary key,
  priority           integer not null unique check (priority > 0),
  enabled            boolean not null default true,
  endpoint_ref       text not null,
  capability_tags    text[] not null default '{}',
  max_concurrency    integer check (max_concurrency is null or max_concurrency > 0),
  monthly_budget_usd numeric(12,4) check (monthly_budget_usd is null or monthly_budget_usd >= 0),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create table if not exists ops.provider_observation (
  id            uuid primary key default gen_random_uuid(),
  route_key     text not null references ops.provider_route(route_key),
  status        text not null check (status in ('healthy','degraded','unavailable','rate_limited')),
  latency_ms    integer check (latency_ms is null or latency_ms >= 0),
  error_class   text,
  observed_at   timestamptz not null default now(),
  expires_at    timestamptz not null,
  source_ref    text not null,
  constraint provider_observation_expiry_is_future check (expires_at > observed_at)
);

create index if not exists provider_observation_latest_idx
  on ops.provider_observation(route_key, observed_at desc);

create or replace view ops.v_provider_route as
with latest as (
  select distinct on (route_key) route_key,status,latency_ms,error_class,observed_at,expires_at
    from ops.provider_observation
   order by route_key, observed_at desc
)
select r.*,
       case when not r.enabled then 'disabled'
            when l.route_key is null then 'unknown'
            when l.expires_at < now() then 'stale'
            else l.status end as health,
       l.latency_ms, l.error_class, l.observed_at
  from ops.provider_route r left join latest l using (route_key)
 order by r.priority;

create table if not exists ops.cognition_job (
  key                       text not null,
  version                   integer not null check (version > 0),
  input_schema_version      integer not null check (input_schema_version > 0),
  output_schema_version     integer not null check (output_schema_version > 0),
  input_schema              jsonb not null check (jsonb_typeof(input_schema)='object'),
  output_schema             jsonb not null check (jsonb_typeof(output_schema)='object'),
  max_tokens                integer not null check (max_tokens > 0),
  max_cost_usd              numeric(12,4) not null check (max_cost_usd >= 0),
  timeout_seconds           integer not null check (timeout_seconds > 0),
  provider_routes           text[] not null check (cardinality(provider_routes) > 0),
  cache_ttl_seconds         integer not null default 0 check (cache_ttl_seconds >= 0),
  canonical_write_authority boolean not null default false
    check (canonical_write_authority = false),
  active                    boolean not null default true,
  registered_at             timestamptz not null default now(),
  primary key (key,version)
);

comment on table ops.cognition_job is
  'Finite model-neutral cognition contracts. Rows describe proposals only; the '
  'schema structurally refuses canonical-write authority.';

create table if not exists ops.job_definition (
  key                 text not null,
  version             integer not null check (version > 0),
  enabled             boolean not null default true,
  risk                text not null check (risk in ('green','yellow','red')),
  owner_actor         text not null default 'system',
  execution_kind      text not null check (execution_kind in ('deterministic','cognition')),
  execution_contract  jsonb not null check (jsonb_typeof(execution_contract)='object'),
  inventory_contract  jsonb not null default '{}'::jsonb check (jsonb_typeof(inventory_contract)='object'),
  recurrence          jsonb not null check (jsonb_typeof(recurrence)='object'),
  state_contract      jsonb not null default '{}'::jsonb check (jsonb_typeof(state_contract)='object'),
  routing_contract    jsonb not null default '{}'::jsonb check (jsonb_typeof(routing_contract)='object'),
  filtering_contract  jsonb not null default '{}'::jsonb check (jsonb_typeof(filtering_contract)='object'),
  validation_contract jsonb not null default '{}'::jsonb check (jsonb_typeof(validation_contract)='object'),
  retry_policy        jsonb not null check (jsonb_typeof(retry_policy)='object'),
  deduplication       jsonb not null check (jsonb_typeof(deduplication)='object'),
  completion_contract jsonb not null check (jsonb_typeof(completion_contract)='object'),
  legacy_schedule     jsonb not null default '{}'::jsonb check (jsonb_typeof(legacy_schedule)='object'),
  legacy_disabled_at  timestamptz,
  legacy_disable_reason text,
  registered_at       timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  primary key (key,version),
  constraint deterministic_definition_names_entrypoint check (
    execution_kind <> 'deterministic' or execution_contract ? 'entrypoint'
  ),
  constraint cognition_definition_names_job check (
    execution_kind <> 'cognition' or execution_contract ? 'cognition_job'
  ),
  constraint disabled_legacy_has_reason check (
    legacy_disabled_at is null or legacy_disable_reason is not null
  )
);

create unique index if not exists one_enabled_job_definition_version
  on ops.job_definition(key) where enabled;

create table if not exists ops.job (
  id                uuid primary key default gen_random_uuid(),
  definition_key    text not null,
  definition_version integer not null,
  correlation_id    uuid not null default gen_random_uuid(),
  idempotency_key   text not null unique,
  scheduled_for     timestamptz not null,
  mode              text not null default 'live' check (mode in ('shadow','canary','live','replay')),
  state             text not null default 'queued'
    check (state in ('queued','running','retry_wait','waiting_approval','succeeded',
                     'failed','timed_out','cancelled','dead_lettered')),
  payload           jsonb not null default '{}'::jsonb,
  attempt           integer not null default 0 check (attempt >= 0),
  max_attempts      integer not null check (max_attempts > 0),
  next_attempt_at   timestamptz not null default now(),
  lease_owner       text,
  lease_token       uuid,
  leased_until      timestamptz,
  timeout_seconds   integer not null check (timeout_seconds > 0),
  last_failure_class text,
  last_failure_detail text,
  created_at        timestamptz not null default now(),
  started_at        timestamptz,
  ended_at          timestamptz,
  updated_at        timestamptz not null default now(),
  foreign key (definition_key,definition_version)
    references ops.job_definition(key,version),
  unique (definition_key,definition_version,scheduled_for),
  constraint running_job_has_a_lease check (
    state <> 'running' or (lease_owner is not null and lease_token is not null and leased_until is not null)
  ),
  constraint terminal_job_has_ended check (
    state not in ('succeeded','failed','timed_out','cancelled','dead_lettered') or ended_at is not null
  )
);

create index if not exists job_dispatch_idx
  on ops.job(state,next_attempt_at,scheduled_for)
  where state in ('queued','retry_wait','running');

create table if not exists ops.job_attempt (
  id            uuid primary key default gen_random_uuid(),
  job_id        uuid not null references ops.job(id) on delete restrict,
  attempt       integer not null check (attempt > 0),
  lease_owner   text not null,
  lease_token   uuid not null,
  provider_route text,
  state         text not null check (state in ('running','succeeded','failed','timed_out','cancelled')),
  started_at    timestamptz not null default now(),
  ended_at      timestamptz,
  input_tokens  integer check (input_tokens is null or input_tokens >= 0),
  output_tokens integer check (output_tokens is null or output_tokens >= 0),
  cost_usd      numeric(12,6) check (cost_usd is null or cost_usd >= 0),
  failure_class text,
  detail        text,
  unique (job_id,attempt)
);

create table if not exists ops.job_receipt (
  id             uuid primary key default gen_random_uuid(),
  job_id         uuid not null references ops.job(id) on delete restrict,
  attempt        integer not null,
  kind           text not null check (kind in ('completion','failure','dead_letter','approval','override')),
  receipt_ref    text not null,
  evidence       jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);

create index if not exists job_receipt_job_idx on ops.job_receipt(job_id,created_at);

create table if not exists ops.cognition_result_cache (
  cache_key             text primary key,
  cognition_key         text not null,
  cognition_version     integer not null,
  output_schema_version integer not null,
  proposal              jsonb not null,
  evidence_refs         text[] not null default '{}',
  validated_at          timestamptz not null,
  created_at            timestamptz not null default now(),
  expires_at            timestamptz not null,
  invalidated_at        timestamptz,
  foreign key (cognition_key,cognition_version)
    references ops.cognition_job(key,version),
  constraint cache_expiry_after_creation check (expires_at >= created_at)
);

create table if not exists ops.workflow_acceptance (
  id              uuid primary key default gen_random_uuid(),
  workflow_key    text not null,
  workflow_version integer not null,
  mode            text not null check (mode in ('shadow','canary')),
  status          text not null check (status in ('observed','accepted','rejected')),
  receipt_ref     text not null,
  accepted_by     text,
  note            text,
  created_at      timestamptz not null default now(),
  foreign key (workflow_key,workflow_version)
    references ops.job_definition(key,version),
  unique (workflow_key,workflow_version,mode,receipt_ref),
  constraint accepted_evidence_names_actor check (
    status <> 'accepted' or accepted_by is not null
  )
);

create or replace function ops.refuse_job_evidence_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name;
end $$;

create trigger job_attempt_append_only
  before delete on ops.job_attempt for each row execute function ops.refuse_job_evidence_rewrite();
create trigger job_receipt_append_only
  before update or delete on ops.job_receipt for each row execute function ops.refuse_job_evidence_rewrite();
create trigger provider_observation_append_only
  before update or delete on ops.provider_observation for each row execute function ops.refuse_job_evidence_rewrite();
create trigger workflow_acceptance_append_only
  before update or delete on ops.workflow_acceptance for each row execute function ops.refuse_job_evidence_rewrite();

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
    raise exception 'job definition % v% is not enabled', p_definition_key,p_definition_version;
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
  on conflict (idempotency_key) do nothing
  returning * into j;
  if j.id is null then
    select * into j from ops.job where idempotency_key=p_idempotency_key;
    if j.definition_key <> p_definition_key
       or j.definition_version <> p_definition_version
       or j.scheduled_for <> p_scheduled_for
       or j.payload <> coalesce(p_payload,'{}'::jsonb) then
      raise exception 'idempotency key % was reused with a different request',p_idempotency_key;
    end if;
  end if;
  return j;
end $$;

create or replace function ops.retry_delay_seconds(p_job ops.job)
returns integer language sql stable as $$
  select least(
    (d.retry_policy->>'cap_seconds')::integer,
    case d.retry_policy->>'backoff'
      when 'constant' then (d.retry_policy->>'base_seconds')::integer
      when 'linear' then (d.retry_policy->>'base_seconds')::integer * greatest(p_job.attempt,1)
      else (d.retry_policy->>'base_seconds')::integer
           * power(2,greatest(p_job.attempt-1,0))::integer
    end)
  from ops.job_definition d
  where d.key=p_job.definition_key and d.version=p_job.definition_version
$$;

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
  update ops.job j
     set state=case when attempt < max_attempts then 'retry_wait' else 'dead_lettered' end,
         next_attempt_at=case when attempt < max_attempts
                              then now()+make_interval(secs=>ops.retry_delay_seconds(j))
                              else next_attempt_at end,
         ended_at=case when attempt < max_attempts then null else now() end,
         last_failure_class='lease_expired', last_failure_detail='lease expired',
         lease_owner=null,lease_token=null,leased_until=null,updated_at=now()
   where state='running' and leased_until < now();
  get diagnostics n=row_count;
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
  ), attempts as (
    insert into ops.job_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c
    join ops.job_definition d on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.job_id=c.id;
end $$;

create or replace function ops.heartbeat_job(
  p_job_id uuid,p_lease_token uuid,p_lease_seconds integer default 300
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  update ops.job set leased_until=now()+make_interval(secs=>p_lease_seconds),updated_at=now()
   where id=p_job_id and state='running' and lease_token=p_lease_token
     and leased_until >= now();
  get diagnostics n=row_count;
  return n=1;
end $$;

create or replace function ops.complete_job(
  p_job_id uuid,p_lease_token uuid,p_evidence jsonb,p_receipt_ref text
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token
     or j.leased_until < now() then
    raise exception 'job % does not hold this live lease',p_job_id;
  end if;
  update ops.job_attempt set state='succeeded',ended_at=now()
   where job_id=j.id and attempt=j.attempt and lease_token=p_lease_token;
  update ops.job set state='succeeded',ended_at=now(),lease_owner=null,
         lease_token=null,leased_until=null,updated_at=now() where id=j.id;
  insert into ops.job_receipt(job_id,attempt,kind,receipt_ref,evidence)
    values(j.id,j.attempt,'completion',p_receipt_ref,coalesce(p_evidence,'{}'::jsonb));
  return true;
end $$;

create or replace function ops.fail_job(
  p_job_id uuid,p_lease_token uuid,p_failure_class text,p_detail text
) returns text
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare j ops.job%rowtype; next_state text;
begin
  select * into j from ops.job where id=p_job_id for update;
  if not found or j.state <> 'running' or j.lease_token <> p_lease_token then
    raise exception 'job % does not hold this lease',p_job_id;
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

create or replace function ops.record_workflow_acceptance(
  p_workflow_key text,p_mode text,p_status text,p_receipt_ref text,p_actor text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare v integer; rid uuid;
begin
  select version into v from ops.job_definition
   where key=p_workflow_key order by version desc limit 1;
  if v is null then raise exception 'unknown workflow %',p_workflow_key; end if;
  insert into ops.workflow_acceptance
    (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
  values(p_workflow_key,v,p_mode,p_status,p_receipt_ref,
         case when p_status='accepted' then p_actor else null end)
  returning id into rid;
  return rid;
end $$;

create or replace function ops.require_cutover_evidence()
returns trigger language plpgsql as $$
declare modes integer;
begin
  if old.legacy_disabled_at is null and new.legacy_disabled_at is not null then
    select count(distinct mode) into modes from ops.workflow_acceptance
     where workflow_key=new.key and workflow_version=new.version
       and status='accepted' and mode in ('shadow','canary');
    if modes <> 2 then
      raise exception 'workflow % v% cannot disable legacy schedule: accepted shadow and canary receipts are required',new.key,new.version;
    end if;
  end if;
  return new;
end $$;

create trigger job_definition_cutover_requires_evidence
  before update of legacy_disabled_at on ops.job_definition
  for each row execute function ops.require_cutover_evidence();

create or replace function ops.disable_legacy_schedule(
  p_workflow_key text,p_reason text
) returns boolean
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare n integer;
begin
  if btrim(coalesce(p_reason,''))='' then raise exception 'cutover reason is required'; end if;
  update ops.job_definition set legacy_disabled_at=now(),legacy_disable_reason=p_reason,updated_at=now()
   where key=p_workflow_key and version=(select max(version) from ops.job_definition where key=p_workflow_key)
     and legacy_disabled_at is null;
  get diagnostics n=row_count;
  return n=1;
end $$;

create or replace view ops.v_job_control as
select j.id,j.definition_key,j.definition_version,j.correlation_id,j.state,j.mode,
       j.attempt,j.max_attempts,j.scheduled_for,j.next_attempt_at,j.lease_owner,
       j.leased_until,j.last_failure_class,j.created_at,j.started_at,j.ended_at,
       d.risk,d.execution_kind,d.owner_actor,
       (select count(*) from ops.job_receipt r where r.job_id=j.id) as receipt_count
  from ops.job j join ops.job_definition d
    on d.key=j.definition_key and d.version=j.definition_version;

create or replace view ops.v_job_cost as
select date_trunc('month',a.started_at) as month,
       j.definition_key,a.provider_route,
       sum(coalesce(a.input_tokens,0)) as input_tokens,
       sum(coalesce(a.output_tokens,0)) as output_tokens,
       sum(coalesce(a.cost_usd,0)) as cost_usd,
       count(*) as attempts
  from ops.job_attempt a join ops.job j on j.id=a.job_id
 group by 1,2,3;

revoke all on function ops.enqueue_job(text,integer,timestamptz,jsonb,text,text) from public;
revoke all on function ops.claim_job(text,integer,integer) from public;
revoke all on function ops.heartbeat_job(uuid,uuid,integer) from public;
revoke all on function ops.complete_job(uuid,uuid,jsonb,text) from public;
revoke all on function ops.fail_job(uuid,uuid,text,text) from public;
revoke all on function ops.record_workflow_acceptance(text,text,text,text,text) from public;
revoke all on function ops.disable_legacy_schedule(text,text) from public;

grant select on ops.provider_route,ops.provider_observation,ops.v_provider_route,
                ops.cognition_job,ops.job_definition,ops.job,ops.job_attempt,
                ops.job_receipt,ops.cognition_result_cache,ops.workflow_acceptance,
                ops.v_job_control,ops.v_job_cost to carr_reader,carr_writer,carr_jobs;
grant insert,update on ops.provider_route,ops.cognition_job,ops.job_definition,
                       ops.cognition_result_cache to carr_writer;
grant insert on ops.provider_observation to carr_jobs;
grant execute on function ops.enqueue_job(text,integer,timestamptz,jsonb,text,text),
                          ops.claim_job(text,integer,integer),
                          ops.heartbeat_job(uuid,uuid,integer),
                          ops.complete_job(uuid,uuid,jsonb,text),
                          ops.fail_job(uuid,uuid,text,text)
  to carr_jobs;
grant execute on function ops.record_workflow_acceptance(text,text,text,text,text),
                          ops.disable_legacy_schedule(text,text)
  to carr_writer;

commit;

do $$
begin
  if to_regclass('ops.job') is null or to_regclass('ops.job_receipt') is null then
    raise exception '0149 FAILED: job ledger or receipt missing';
  end if;
  if has_table_privilege('carr_jobs','ops.job','delete') then
    raise exception '0149 FAILED: carr_jobs can delete jobs';
  end if;
  if has_table_privilege('carr_jobs','ops.job_receipt','update') then
    raise exception '0149 FAILED: carr_jobs can rewrite receipts';
  end if;
end $$;
