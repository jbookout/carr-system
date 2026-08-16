-- 0163_control_plane_device_evidence.sql
-- Signed-in platform collectors cross a different authority boundary from the
-- routine job runner.  They may append one evidence receipt for an already
-- registered ledger job; carr_jobs can read that immutable receipt but cannot
-- mint, rewrite, or delete it.  Per-device LOGIN roles and principal rows are
-- provisioned outside routine automation after explicit human approval.

begin;

do $$ begin
  if not exists (select 1 from pg_roles where rolname='carr_device_evidence') then
    create role carr_device_evidence nologin;
  end if;
end $$;

create table if not exists ops.device_evidence_principal (
  login_role name primary key,
  device_id text not null unique check (btrim(device_id) <> ''),
  active boolean not null default true,
  provisioned_at timestamptz not null default now()
);

create table if not exists ops.device_evidence_receipt (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id) on delete restrict,
  builder_key text not null check (builder_key in ('linkedin.source-posts','x.source-posts')),
  workflow_key text not null,
  workflow_version integer not null,
  mode text not null check (mode in ('shadow','canary','live','replay')),
  scheduled_for timestamptz not null,
  device_id text not null,
  observed_at timestamptz not null,
  evidence jsonb not null,
  idempotency_key text not null unique check (btrim(idempotency_key) <> ''),
  created_at timestamptz not null default now(),
  unique (job_id,builder_key)
);

create trigger device_evidence_receipt_append_only
  before update or delete on ops.device_evidence_receipt
  for each row execute function ops.refuse_job_evidence_rewrite();

create or replace function ops.record_device_evidence(
  p_job_id uuid,
  p_builder_key text,
  p_observed_at timestamptz,
  p_values jsonb,
  p_idempotency_key text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  principal ops.device_evidence_principal%rowtype;
  j ops.job%rowtype;
  existing ops.device_evidence_receipt%rowtype;
  expected_workflow text;
  expected_platform text;
  posts jsonb;
  post jsonb;
  result_id uuid;
begin
  select * into principal from ops.device_evidence_principal
   where login_role=session_user and active;
  if not found then
    raise exception 'device evidence session user is not an active provisioned principal';
  end if;

  select * into j from ops.job where id=p_job_id for share;
  if not found then raise exception 'device evidence job does not exist'; end if;
  if j.state not in ('queued','retry_wait','running') then
    raise exception 'device evidence job is not open for collection';
  end if;

  case p_builder_key
    when 'linkedin.source-posts' then
      expected_workflow := 'linkedin-engagement-daily';
      expected_platform := 'linkedin';
    when 'x.source-posts' then
      expected_workflow := 'x-reply-run-daily';
      expected_platform := 'x';
    else raise exception 'device evidence builder is not registered';
  end case;
  if j.definition_key <> expected_workflow then
    raise exception 'device evidence builder does not match ledger workflow';
  end if;
  if p_observed_at > now() + interval '5 minutes'
     or p_observed_at < j.scheduled_for - interval '24 hours'
     or p_observed_at > j.scheduled_for + interval '24 hours' then
    raise exception 'device evidence is outside the registered job freshness window';
  end if;
  if jsonb_typeof(p_values) <> 'object'
     or (p_values - array['platform','collector_state','source_posts','voice_version']) <> '{}'::jsonb
     or p_values->>'platform' <> expected_platform
     or p_values->>'collector_state' <> 'available'
     or jsonb_typeof(p_values->'voice_version') <> 'number'
     or (p_values->>'voice_version')::integer <= 0
     or jsonb_typeof(p_values->'source_posts') <> 'array' then
    raise exception 'device evidence envelope does not match the registered schema';
  end if;
  posts := p_values->'source_posts';
  if (p_builder_key='linkedin.source-posts' and jsonb_array_length(posts) not between 3 and 5)
     or (p_builder_key='x.source-posts' and jsonb_array_length(posts) not between 1 and 20) then
    raise exception 'device evidence post count is outside the registered range';
  end if;
  for post in select value from jsonb_array_elements(posts) loop
    if jsonb_typeof(post) <> 'object'
       or btrim(coalesce(post->>'url',''))='' then
      raise exception 'device evidence post lacks its source URL';
    end if;
    if p_builder_key='linkedin.source-posts'
       and jsonb_typeof(post->'network_priority') <> 'boolean' then
      raise exception 'LinkedIn device evidence lacks network priority';
    end if;
    if p_builder_key='x.source-posts'
       and btrim(coalesce(post->>'read_at',''))='' then
      raise exception 'X device evidence lacks actual-read timestamp';
    end if;
  end loop;

  select * into existing from ops.device_evidence_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if existing.job_id<>p_job_id or existing.builder_key<>p_builder_key
       or existing.device_id<>principal.device_id
       or existing.observed_at<>p_observed_at or existing.evidence<>p_values then
      raise exception 'device evidence idempotency key was reused with different evidence';
    end if;
    return existing.id;
  end if;

  insert into ops.device_evidence_receipt
    (job_id,builder_key,workflow_key,workflow_version,mode,scheduled_for,
     device_id,observed_at,evidence,idempotency_key)
  values
    (j.id,p_builder_key,j.definition_key,j.definition_version,j.mode,j.scheduled_for,
     principal.device_id,p_observed_at,p_values,p_idempotency_key)
  returning id into result_id;
  return result_id;
end $$;

revoke all on ops.device_evidence_principal, ops.device_evidence_receipt from public;
revoke all on function ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)
  from public, carr_jobs, carr_writer;
grant usage on schema ops to carr_device_evidence;
grant execute on function ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)
  to carr_device_evidence;
grant select on ops.device_evidence_receipt to carr_jobs;

do $$ begin
  if not has_table_privilege('carr_jobs','ops.device_evidence_receipt','select')
     or has_table_privilege('carr_jobs','ops.device_evidence_receipt','insert')
     or has_table_privilege('carr_jobs','ops.device_evidence_receipt','update')
     or has_table_privilege('carr_jobs','ops.device_evidence_receipt','delete') then
    raise exception '0163 FAILED: jobs role device evidence privileges are not read-only';
  end if;
  if has_function_privilege(
       'carr_jobs','ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,'execute')
     or has_function_privilege(
       'carr_writer','ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,'execute') then
    raise exception '0163 FAILED: routine roles can mint device evidence';
  end if;
  if not has_function_privilege(
       'carr_device_evidence','ops.record_device_evidence(uuid,text,timestamptz,jsonb,text)'::regprocedure,'execute') then
    raise exception '0163 FAILED: device evidence bundle cannot append through its narrow function';
  end if;
end $$;

commit;
