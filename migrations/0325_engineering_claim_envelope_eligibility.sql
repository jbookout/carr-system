-- 0325_engineering_claim_envelope_eligibility.sql
--
-- Make executable-envelope eligibility one database-owned predicate used at
-- every controller boundary. The original claim joined any envelope for a
-- queued job, so expired/read-only/superseded generations could receive a
-- lease. Receipt persistence must decide again because an envelope or its
-- agent session may become terminal while Codex is working.

begin;

create or replace function ops.engineering_envelope_is_executable(
  p_envelope_id uuid, p_job_id uuid
) returns boolean
language plpgsql volatile security definer
set search_path=pg_catalog,ops,public
as $$
declare lineage_plan uuid;
        lineage_slice text;
begin
  select e.slice_plan_id,e.slice_ref into lineage_plan,lineage_slice
    from ops.engineering_execution_envelope e
   where e.id=p_envelope_id and e.job_id=p_job_id;
  if not found then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:' || lineage_plan::text || ':' || lineage_slice,0));
  return exists (
    select 1
      from ops.engineering_execution_envelope e
      join ops.job j on j.id=e.job_id
      join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
      join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
      join ops.work_request w on w.id=e.work_request_id
      join ops.capability_agent_session s on s.id=e.agent_session_id
      cross join lateral (select ops.engineering_admission_source(w.ref) as source) current_source
     where e.id=p_envelope_id and j.id=p_job_id
       and d.enabled and j.definition_key='engineering-slice'
       and j.definition_version=1 and j.mode='shadow'
       and j.payload->>'work_request'=w.ref
       and j.payload->>'slice_ref'=e.slice_ref
       and j.payload->>'plan_digest'=sp.plan_digest
       and j.payload->>'generation' ~ '^[1-9][0-9]*$'
       and current_source.source is not null
       and current_source.source->'work_request'->>'id'='wr:' || e.work_request_id::text
       and (current_source.source->'work_request'->>'version')::integer=e.state_version
       and current_source.source->'work_request'->>'canonical_record_digest'=e.canonical_record_digest
       and current_source.source->'accepted_plan'->>'record_id'=e.accepted_plan_id::text
       and current_source.source->'accepted_plan'->>'digest'=sp.accepted_plan_hash
       and sp.work_request_id=e.work_request_id
       and sp.accepted_plan_id=e.accepted_plan_id
       and sp.work_request_version=e.state_version
       and s.work_request_id=e.work_request_id
       and s.state not in ('completed','cancelled')
       and e.expires_at>statement_timestamp()
       and e.envelope->'server_binding'->'authority'->>'read_only'='false'
       and e.envelope->'server_binding'->'authority'->>'capability_profile'=
           'capability:engineering-repository-write'
       and e.envelope->'server_binding'->'adapter'->>'surface'='codex_desktop'
       and e.envelope->'request'->'allowed_actions'=
           '["repository:create-worktree","repository:create-branch","repository:write-declared-scope","repository:run-checks","repository:commit","repository:push-branch","repository:open-pr"]'::jsonb
       and not exists (select 1 from ops.engineering_execution_envelope successor
                        where successor.supersedes_envelope_id=e.id)
       and not exists (select 1 from ops.engineering_slice_receipt receipt
                        where receipt.envelope_id=e.id)
  );
end $$;

create or replace function ops.engineering_claim_slice(
  p_worker text, p_limit integer default 1, p_lease_seconds integer default 1800
) returns table (
  job_id uuid, lease_token uuid, definition_key text, definition_version integer,
  payload jsonb, execution_kind text, execution_contract jsonb,
  attempt integer, timeout_seconds integer, mode text
)
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
begin
  if btrim(coalesce(p_worker,''))='' or p_limit<1 or p_lease_seconds<1 then
    raise exception 'worker, positive limit and positive lease are required';
  end if;
  perform ops.reap_expired_jobs();
  return query
  with candidate as (
    select j.id
      from ops.job j
      join ops.job_definition d on d.key=j.definition_key and d.version=j.definition_version
      join ops.engineering_execution_envelope e on e.job_id=j.id
     where d.enabled and j.definition_key='engineering-slice'
       and j.definition_version=1 and j.state in ('queued','retry_wait')
       and j.next_attempt_at<=now() and j.attempt<j.max_attempts
       and ops.engineering_envelope_is_executable(e.id,j.id)
     order by j.scheduled_for,j.created_at
     for update of j,d skip locked limit p_limit
  ), claimed as (
    update ops.job j set
      state='running',attempt=j.attempt+1,lease_owner=p_worker,lease_token=gen_random_uuid(),
      leased_until=now()+make_interval(secs=>p_lease_seconds),
      started_at=coalesce(j.started_at,now()),updated_at=now()
    from candidate c where j.id=c.id returning j.*
  ), attempts as (
    insert into ops.job_attempt as claimed_attempt(job_id,attempt,lease_owner,lease_token,state)
    select c.id,c.attempt,c.lease_owner,c.lease_token,'running' from claimed c
    returning claimed_attempt.job_id
  )
  select c.id,c.lease_token,c.definition_key,c.definition_version,c.payload,
         d.execution_kind,d.execution_contract,c.attempt,c.timeout_seconds,c.mode
    from claimed c join ops.job_definition d
      on d.key=c.definition_key and d.version=c.definition_version
    join attempts a on a.job_id=c.id;
end $$;

create or replace function ops.engineering_controller_binding(
  p_envelope_id uuid,p_job_id uuid
) returns jsonb
language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare binding jsonb;
begin
  if not ops.engineering_envelope_is_executable(p_envelope_id,p_job_id) then return null; end if;
  select jsonb_build_object(
    'envelope_id',e.id::text,'envelope_digest',e.envelope_digest,
    'slice_ref',e.slice_ref,'plan_digest',sp.plan_digest,'slice_plan',sp.plan,
    'executor_actor',jsonb_build_object('id',a.id::text,'slug',a.slug),
    'agent_session_id',s.id::text
  ) into binding
    from ops.engineering_execution_envelope e
    join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
    join ops.capability_agent_session s on s.id=e.agent_session_id
    join public.actor a on a.id=s.executor_actor_id
   where e.id=p_envelope_id and e.job_id=p_job_id;
  return binding;
end $$;

create or replace function ops.engineering_record_slice_receipt(
  p_envelope_id uuid,p_lease_token uuid,p_receipt jsonb,
  p_receipt_digest text,p_executor_actor_id uuid
) returns ops.engineering_slice_receipt
language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare e ops.engineering_execution_envelope%rowtype;
        a ops.job_attempt%rowtype;
        session_executor uuid;
        row ops.engineering_slice_receipt%rowtype;
begin
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  if not ops.engineering_envelope_is_executable(e.id,e.job_id) then
    raise exception 'engineering envelope is no longer executable';
  end if;
  select executor_actor_id into session_executor from ops.capability_agent_session
   where id=e.agent_session_id;
  if session_executor is null or p_executor_actor_id is distinct from session_executor then
    raise exception 'engineering receipt executor is not the server-bound agent session';
  end if;
  select attempt_row.* into a from ops.job_attempt attempt_row
    join ops.job j on j.id=attempt_row.job_id
   where attempt_row.job_id=e.job_id and attempt_row.attempt=j.attempt
     and attempt_row.lease_token=p_lease_token and attempt_row.state='running' for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  if p_receipt->>'envelope_digest'<>e.envelope_digest
     or p_receipt->>'slice_ref'<>e.slice_ref
     or p_receipt->>'attempt_id'<>('attempt:' || a.attempt)
     or p_receipt->>'outcome' not in ('claimed_complete','failed','blocked','reopened') then
    raise exception 'engineering receipt is not bound to the claimed envelope';
  end if;
  insert into ops.engineering_slice_receipt
    (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,
     executor_actor_id,receipt_digest,outcome,receipt)
  values (a.id,e.id,e.work_request_id,e.slice_ref,p_receipt->>'attempt_id',
          session_executor,p_receipt_digest,p_receipt->>'outcome',p_receipt)
  returning * into row;
  return row;
end $$;

create or replace function ops.guard_engineering_envelope_supersession()
returns trigger language plpgsql set search_path=pg_catalog,ops,public
as $$
declare prior ops.engineering_execution_envelope%rowtype;
        prior_count integer;
begin
  perform pg_advisory_xact_lock(hashtextextended(
    'engineering-envelope:' || new.slice_plan_id::text || ':' || new.slice_ref,0));
  select count(*) into prior_count from ops.engineering_execution_envelope
   where slice_plan_id=new.slice_plan_id and slice_ref=new.slice_ref;
  if prior_count=0 then
    if new.supersedes_envelope_id is not null then
      raise exception 'first engineering envelope cannot supersede another envelope';
    end if;
    return new;
  end if;
  if new.supersedes_envelope_id is null then
    raise exception 'later engineering envelope must name its immutable predecessor';
  end if;
  select * into prior from ops.engineering_execution_envelope where id=new.supersedes_envelope_id;
  if not found or prior.slice_plan_id<>new.slice_plan_id or prior.slice_ref<>new.slice_ref
     or prior.accepted_plan_id<>new.accepted_plan_id or prior.work_request_id<>new.work_request_id then
    raise exception 'engineering envelope predecessor is outside the exact slice binding';
  end if;
  if exists (select 1 from ops.engineering_execution_envelope
              where supersedes_envelope_id=prior.id) then
    raise exception 'engineering envelope predecessor already has a successor';
  end if;
  if exists (select 1 from ops.job j where j.id=prior.job_id and j.state='running'
              and j.lease_token is not null and j.leased_until>now()) then
    raise exception 'leased engineering envelope cannot be superseded';
  end if;
  if prior.expires_at>now()
     and coalesce((prior.envelope->'server_binding'->'authority'->>'read_only')::boolean,true)=false
     and not exists (select 1 from ops.engineering_slice_receipt r
                      where r.envelope_id=prior.id and r.outcome in ('failed','blocked','reopened')) then
    raise exception 'current executable engineering envelope cannot be superseded';
  end if;
  return new;
end $$;

revoke all on function ops.engineering_envelope_is_executable(uuid,uuid)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.engineering_claim_slice(text,integer,integer) from public;
revoke all on function ops.engineering_controller_binding(uuid,uuid) from public;
revoke all on function ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid) from public;
grant execute on function ops.engineering_claim_slice(text,integer,integer),
  ops.engineering_controller_binding(uuid,uuid),
  ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid) to carr_jobs;

do $$
begin
  if has_function_privilege('carr_jobs',
       'ops.engineering_envelope_is_executable(uuid,uuid)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
       'ops.engineering_claim_slice(text,integer,integer)'::regprocedure,'EXECUTE')
     or not has_function_privilege('carr_jobs',
       'ops.engineering_controller_binding(uuid,uuid)'::regprocedure,'EXECUTE')
     or has_table_privilege('carr_jobs','ops.work_request','SELECT')
     or has_table_privilege('carr_jobs','ops.job','UPDATE')
     or has_table_privilege('carr_jobs','ops.job_attempt','INSERT') then
    raise exception '0325 FAILED: Engineering controller least-privilege boundary widened or is incomplete';
  end if;
end $$;

commit;
