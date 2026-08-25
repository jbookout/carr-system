-- 0308_engineering_execution_fabric.sql
--
-- The first durable Engineering Passport runtime seam.  This is an execution
-- proof extension over the existing Work Request, accepted sourced plan,
-- capability session and ops.job ledgers; it is not another task database or
-- plan authority.  A typed slice plan is a projection of one accepted plan,
-- an envelope is issued only from that projection, and receipts/reviews remain
-- immutable evidence.

begin;

create table if not exists ops.engineering_slice_plan (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  accepted_plan_id uuid not null references ops.sourced_work_request_plan(id) on delete restrict,
  accepted_plan_hash text not null check (accepted_plan_hash ~ '^sha256:[0-9a-f]{64}$'),
  work_request_version integer not null check (work_request_version > 0),
  plan_digest text not null check (plan_digest ~ '^sha256:[0-9a-f]{64}$'),
  plan jsonb not null check (jsonb_typeof(plan) = 'object'),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now(),
  unique (accepted_plan_id),
  unique (work_request_id, plan_digest)
);

comment on table ops.engineering_slice_plan is
  'Typed engineering-slice projection over one accepted sourced plan. It is '
  'never an alternate Work Request or acceptance authority.';

create table if not exists ops.engineering_execution_envelope (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null unique references ops.job(id) on delete restrict,
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  accepted_plan_id uuid not null references ops.sourced_work_request_plan(id) on delete restrict,
  slice_plan_id uuid not null references ops.engineering_slice_plan(id) on delete restrict,
  slice_ref text not null check (btrim(slice_ref) <> ''),
  agent_session_id uuid not null references ops.capability_agent_session(id) on delete restrict,
  state_version integer not null check (state_version > 0),
  canonical_record_digest text not null check (canonical_record_digest ~ '^sha256:[0-9a-f]{64}$'),
  envelope_digest text not null unique check (envelope_digest ~ '^sha256:[0-9a-f]{64}$'),
  envelope jsonb not null check (jsonb_typeof(envelope) = 'object'),
  issued_at timestamptz not null,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  constraint engineering_envelope_expiry_after_issue check (expires_at > issued_at),
  unique (slice_plan_id, slice_ref),
  constraint engineering_envelope_slice_plan_matches check (
    regexp_replace(envelope->>'work_request_id', '^wr:', '') = work_request_id::text
    and envelope->'state_binding'->>'state_version' = state_version::text
    and envelope->'state_binding'->>'canonical_record_digest' = canonical_record_digest
  )
);

create index if not exists engineering_envelope_work_request_idx
  on ops.engineering_execution_envelope(work_request_id, created_at desc);

create table if not exists ops.engineering_slice_receipt (
  id uuid primary key default gen_random_uuid(),
  job_attempt_id uuid not null unique references ops.job_attempt(id) on delete restrict,
  envelope_id uuid not null references ops.engineering_execution_envelope(id) on delete restrict,
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  slice_ref text not null check (btrim(slice_ref) <> ''),
  attempt_id text not null check (attempt_id ~ '^attempt:[1-9][0-9]*$'),
  executor_actor_id uuid not null references actor(id) on delete restrict,
  receipt_digest text not null unique check (receipt_digest ~ '^sha256:[0-9a-f]{64}$'),
  outcome text not null check (outcome in ('claimed_complete','failed','blocked','reopened')),
  receipt jsonb not null check (jsonb_typeof(receipt) = 'object'),
  created_at timestamptz not null default now(),
  unique (envelope_id, job_attempt_id),
  unique (envelope_id, attempt_id)
);

create index if not exists engineering_receipt_work_request_idx
  on ops.engineering_slice_receipt(work_request_id, created_at desc);

create table if not exists ops.engineering_reviewer_fact (
  id uuid primary key default gen_random_uuid(),
  receipt_id uuid not null references ops.engineering_slice_receipt(id) on delete restrict,
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  slice_ref text not null check (btrim(slice_ref) <> ''),
  reviewer_actor_id uuid not null references actor(id) on delete restrict,
  reviewer_session_ref text not null check (btrim(reviewer_session_ref) <> ''),
  state text not null check (state in ('passed','failed','blocked')),
  fact jsonb not null check (jsonb_typeof(fact) = 'object'),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now()
);

create unique index if not exists one_engineering_reviewer_fact_per_receipt
  on ops.engineering_reviewer_fact(receipt_id);

-- The existing job ledger remains the only queue.  This definition is a
-- bounded read-only Codex execution adapter; the envelope still owns the exact
-- Work Request/plan/slice authority and the worker owns the lease.
insert into ops.job_definition
  (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
   inventory_contract,state_contract,routing_contract,filtering_contract,
   recurrence,
   validation_contract,retry_policy,deduplication,completion_contract)
values
  ('engineering-slice',1,true,'yellow','hermes','deterministic',
   '{"entrypoint":"mcp-server/src/engineering-runtime.js#runCodexSlice"}'::jsonb,
   '{"kind":"engineering-passport-slice","data_class":"metadata_only"}'::jsonb,
   '{"states":["queued","running","succeeded","failed","timed_out"]}'::jsonb,
   '{"adapter":"codex_desktop","fresh_native_session_required":true}'::jsonb,
   '{"server_selected":true,"client_selectors":[]}'::jsonb,
   '{"kind":"on_demand","schedule":null}'::jsonb,
   '{"typed_envelope":true,"typed_receipt":true,"independent_review":true}'::jsonb,
   '{"max_attempts":2,"backoff":"constant","base_seconds":30,"cap_seconds":300,"timeout_seconds":1800}'::jsonb,
   '{"key":"engineering_slice_idempotency"}'::jsonb,
   '{"completion":"typed_receipt_and_independent_review"}'::jsonb)
on conflict (key,version) do update set
  execution_contract=excluded.execution_contract,
  inventory_contract=excluded.inventory_contract,
  state_contract=excluded.state_contract,
  routing_contract=excluded.routing_contract,
  filtering_contract=excluded.filtering_contract,
  recurrence=excluded.recurrence,
  validation_contract=excluded.validation_contract,
  retry_policy=excluded.retry_policy,
  deduplication=excluded.deduplication,
  completion_contract=excluded.completion_contract,
  enabled=true,
  updated_at=now();

create or replace function ops.refuse_engineering_evidence_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception '% is append-only', tg_table_name;
end $$;

drop trigger if exists engineering_slice_plan_append_only on ops.engineering_slice_plan;
create trigger engineering_slice_plan_append_only
  before update or delete on ops.engineering_slice_plan
  for each row execute function ops.refuse_engineering_evidence_rewrite();
drop trigger if exists engineering_execution_envelope_append_only on ops.engineering_execution_envelope;
create trigger engineering_execution_envelope_append_only
  before update or delete on ops.engineering_execution_envelope
  for each row execute function ops.refuse_engineering_evidence_rewrite();
drop trigger if exists engineering_slice_receipt_append_only on ops.engineering_slice_receipt;
create trigger engineering_slice_receipt_append_only
  before update or delete on ops.engineering_slice_receipt
  for each row execute function ops.refuse_engineering_evidence_rewrite();
drop trigger if exists engineering_reviewer_fact_append_only on ops.engineering_reviewer_fact;
create trigger engineering_reviewer_fact_append_only
  before update or delete on ops.engineering_reviewer_fact
  for each row execute function ops.refuse_engineering_evidence_rewrite();

-- The private accepted-plan rows are not exposed to the routine writer.  This
-- narrow definer function returns the exact current source for admission.
create or replace function ops.engineering_admission_source(p_work_request text)
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
    'work_request', jsonb_build_object(
      'id', 'wr:' || w.id::text, 'ref', w.ref, 'state', w.state, 'version', w.version,
      'title', w.title, 'desired_outcome', w.desired_outcome,
      'acceptance_criteria', w.acceptance_criteria,
      'canonical_record_digest', 'sha256:' || encode(public.digest(
        jsonb_build_object('id',w.id,'ref',w.ref,'state',w.state,'version',w.version,
                           'title',w.title,'desired_outcome',w.desired_outcome,
                           'acceptance_criteria',w.acceptance_criteria)::text, 'sha256'), 'hex')
    ),
    'accepted_plan', jsonb_build_object(
      'id', p.plan_ref, 'record_id', p.id, 'plan_ref', p.plan_ref, 'revision', p.plan_version,
      'digest', p.plan_hash, 'work_request_version', p.work_request_version,
      'preimage', p.preimage, 'scope_summary', p.scope_summary,
      'dependency_refs', p.dependency_refs, 'recovery_ref', p.recovery_ref,
      'observability_ref', p.observability_ref, 'caps', p.caps,
      'accepted_at', ar.accepted_at, 'accepted_by_actor_id', ar.accepted_by_actor_id
    )
  )
    from ops.work_request w
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id = w.id
    join ops.sourced_work_request_plan p
      on p.id = ar.plan_id and p.work_request_id = w.id and p.plan_hash = ar.plan_hash
   where w.ref = p_work_request and w.state = 'ready';
$$;

create or replace function ops.engineering_register_slice_plan(
  p_work_request text, p_plan jsonb, p_plan_digest text, p_idempotency_key uuid
)
returns ops.engineering_slice_plan
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare source jsonb; row ops.engineering_slice_plan%rowtype;
begin
  source := ops.engineering_admission_source(p_work_request);
  if source is null then raise exception 'engineering admission requires a current accepted ready plan'; end if;
  if p_plan_digest !~ '^sha256:[0-9a-f]{64}$' then raise exception 'invalid engineering slice plan digest'; end if;
  if p_plan->'work_request'->>'id' <> source->'work_request'->>'id'
     or (p_plan->'work_request'->>'state_version')::integer <> (source->'work_request'->>'version')::integer
     or p_plan->'accepted_plan_revision'->>'id' <> source->'accepted_plan'->>'plan_ref'
     or p_plan->'accepted_plan_revision'->>'digest' <> source->'accepted_plan'->>'digest'
     or (p_plan->'accepted_plan_revision'->>'revision')::integer <> (source->'accepted_plan'->>'revision')::integer
     or p_plan->>'plan_digest' <> p_plan_digest then
    raise exception 'engineering slice plan is not bound to the exact accepted Work Request and plan';
  end if;
  insert into ops.engineering_slice_plan
    (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
  values (regexp_replace(source->'work_request'->>'id', '^wr:', '')::uuid,
          (source->'accepted_plan'->>'record_id')::uuid,
          source->'accepted_plan'->>'digest',(source->'work_request'->>'version')::integer,
          p_plan_digest,p_plan,p_idempotency_key)
  on conflict (idempotency_key) do nothing
  returning * into row;
  if row.id is null then
    select * into row from ops.engineering_slice_plan where idempotency_key=p_idempotency_key;
    if row.plan_digest <> p_plan_digest or row.plan <> p_plan then
      raise exception 'engineering slice plan idempotency key was reused with different content';
    end if;
  end if;
  return row;
end $$;

-- A read-only projection used by the server to build the Passport.  It keeps
-- closure derived from canonical rows, never from browser/model assertions.
create or replace function ops.engineering_passport_facts(p_work_request text)
returns jsonb
language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
    'source', ops.engineering_admission_source(p_work_request),
    'slice_plans', coalesce((select jsonb_agg(to_jsonb(sp) order by sp.created_at)
       from ops.engineering_slice_plan sp
       join ops.work_request w on w.id=sp.work_request_id
      where w.ref=p_work_request),'[]'::jsonb),
    'envelopes', coalesce((select jsonb_agg(to_jsonb(e) order by e.created_at)
       from ops.engineering_execution_envelope e
       join ops.work_request w on w.id=e.work_request_id
      where w.ref=p_work_request),'[]'::jsonb),
    'receipts', coalesce((select jsonb_agg(to_jsonb(r) order by r.created_at)
       from ops.engineering_slice_receipt r
       join ops.work_request w on w.id=r.work_request_id
      where w.ref=p_work_request),'[]'::jsonb),
    'reviewer_facts', coalesce((select jsonb_agg(to_jsonb(f) order by f.created_at)
       from ops.engineering_reviewer_fact f
       join ops.work_request w on w.id=f.work_request_id
      where w.ref=p_work_request),'[]'::jsonb)
  );
$$;

-- The worker never receives a broad INSERT surface.  This definer function
-- binds the receipt to the exact live lease, job attempt, envelope, and slice
-- before the append-only evidence row is created.
create or replace function ops.engineering_record_slice_receipt(
  p_envelope_id uuid, p_lease_token uuid, p_receipt jsonb,
  p_receipt_digest text, p_executor_actor_id uuid
)
returns ops.engineering_slice_receipt
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare e ops.engineering_execution_envelope%rowtype;
        a ops.job_attempt%rowtype;
        row ops.engineering_slice_receipt%rowtype;
begin
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  select attempt_row.* into a
    from ops.job_attempt attempt_row join ops.job j on j.id=attempt_row.job_id
   where attempt_row.job_id=e.job_id and attempt_row.attempt=j.attempt
     and attempt_row.lease_token=p_lease_token and attempt_row.state='running'
   for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  if p_receipt->>'envelope_digest' <> e.envelope_digest
     or p_receipt->>'slice_ref' <> e.slice_ref
     or p_receipt->>'attempt_id' <> ('attempt:' || a.attempt)
     or p_receipt->>'outcome' not in ('claimed_complete','failed','blocked','reopened') then
    raise exception 'engineering receipt is not bound to the claimed envelope';
  end if;
  insert into ops.engineering_slice_receipt
    (job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,
     executor_actor_id,receipt_digest,outcome,receipt)
  values (a.id,e.id,e.work_request_id,e.slice_ref,p_receipt->>'attempt_id',
          p_executor_actor_id,p_receipt_digest,p_receipt->>'outcome',p_receipt)
  returning * into row;
  return row;
end $$;

-- Admission may enqueue only this fixed definition and shadow mode; the
-- routine writer never receives the general queue function.
create or replace function ops.engineering_enqueue_slice_job(
  p_work_request text, p_slice_ref text, p_plan_digest text, p_idempotency_key text
)
returns ops.job
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare row ops.job%rowtype;
        facts jsonb;
        job_key text;
begin
  if btrim(p_work_request) = '' or btrim(p_slice_ref) = ''
     or p_plan_digest !~ '^sha256:[0-9a-f]{64}$' or btrim(p_idempotency_key) = '' then
    raise exception 'engineering job admission fields are invalid';
  end if;
  job_key := 'engineering-slice:' || p_plan_digest || ':' || p_work_request || ':' || p_slice_ref;
  perform pg_advisory_xact_lock(hashtextextended('engineering-slice:' || p_plan_digest || ':' || p_slice_ref, 0));
  facts := ops.engineering_passport_facts(p_work_request);
  if not exists (
    select 1
      from jsonb_array_elements(coalesce(facts->'slice_plans','[]'::jsonb)) sp,
           jsonb_array_elements(coalesce(sp->'plan'->'slices','[]'::jsonb)) s
     where sp->>'plan_digest' = p_plan_digest
       and s->>'slice_ref' = p_slice_ref
  ) then
    raise exception 'engineering slice is not registered for the exact plan';
  end if;
  if exists (
    select 1
      from jsonb_array_elements(coalesce(facts->'slice_plans','[]'::jsonb)) sp,
           jsonb_array_elements(coalesce(sp->'plan'->'slices','[]'::jsonb)) s,
           jsonb_array_elements_text(coalesce(s->'dependency_refs','[]'::jsonb)) dep
     where s->>'slice_ref' = p_slice_ref
       and not exists (
         select 1
           from jsonb_array_elements(coalesce(facts->'receipts','[]'::jsonb)) r,
                jsonb_array_elements(coalesce(facts->'reviewer_facts','[]'::jsonb)) v
          where r->>'slice_ref' = dep
            and r->>'outcome' = 'claimed_complete'
            and v->>'slice_ref' = dep
            and v->'fact'->>'attempt_id' = r->>'attempt_id'
            and v->>'state' = 'passed'
       )
  ) then
    raise exception 'engineering slice dependencies are not independently verified';
  end if;
  select * into row from ops.job where idempotency_key=job_key;
  if row.id is not null then return row; end if;
  select * into row from ops.enqueue_job(
    'engineering-slice', 1, now(),
    jsonb_build_object('work_request',p_work_request,'slice_ref',p_slice_ref,'plan_digest',p_plan_digest),
    job_key, 'shadow');
  return row;
end $$;

-- The controller claims only the fixed engineering definition.  Calling the
-- general claim function and filtering afterwards could strand an unrelated
-- job in a running state, so keep selection scoped before the lease is taken.
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
      join ops.engineering_execution_envelope e on e.job_id=j.id
     where d.enabled and j.definition_key='engineering-slice'
       and j.definition_version=1 and j.state in ('queued','retry_wait')
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

-- The worker gets only the queue functions and typed evidence insertion.  The
-- MCP writer gets read/projection and review capability; neither role gets
-- DELETE, and accepted-plan authority remains in the authority path.
grant execute on function ops.engineering_admission_source(text),
  ops.engineering_passport_facts(text) to carr_reader, carr_writer, carr_jobs;
grant execute on function ops.engineering_register_slice_plan(text,jsonb,text,uuid)
  to carr_writer;
grant execute on function ops.engineering_enqueue_slice_job(text,text,text,text)
  to carr_writer;
grant execute on function ops.engineering_claim_slice(text,integer,integer)
  to carr_jobs;
grant select on ops.engineering_slice_plan, ops.engineering_execution_envelope,
  ops.engineering_slice_receipt, ops.engineering_reviewer_fact
  to carr_reader, carr_writer, carr_jobs;
grant insert on ops.engineering_execution_envelope to carr_writer;
grant execute on function ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)
  to carr_jobs;
grant insert on ops.engineering_reviewer_fact to carr_writer;
revoke update, delete on ops.engineering_slice_plan, ops.engineering_execution_envelope,
  ops.engineering_slice_receipt, ops.engineering_reviewer_fact
  from carr_reader, carr_writer, carr_jobs;

commit;

do $$
begin
  if to_regclass('ops.engineering_slice_plan') is null
     or to_regclass('ops.engineering_execution_envelope') is null
     or to_regclass('ops.engineering_slice_receipt') is null
     or to_regclass('ops.engineering_reviewer_fact') is null then
    raise exception '0308 FAILED: typed engineering execution tables are missing';
  end if;
  if has_table_privilege('carr_jobs','ops.engineering_slice_receipt','update')
     or has_table_privilege('carr_jobs','ops.engineering_slice_receipt','delete') then
    raise exception '0308 FAILED: carr_jobs can rewrite typed receipts';
  end if;
end $$;
