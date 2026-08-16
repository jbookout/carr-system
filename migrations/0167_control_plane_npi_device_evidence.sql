-- 0167_control_plane_npi_device_evidence.sql
-- NPPES extraction is an external, signed-in collection boundary.  The jobs
-- role can only read one immutable receipt already bound to its ledger job.

begin;

create table ops.npi_device_evidence_receipt (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id) on delete restrict,
  builder_key text not null check (builder_key='npi.weekly-delta'),
  workflow_key text not null check (workflow_key='npi-sweep-weekly'),
  workflow_version integer not null,
  mode text not null check (mode in ('shadow','canary','live','replay')),
  scheduled_for timestamptz not null,
  device_id text not null,
  observed_at timestamptz not null,
  source_release text not null check (btrim(source_release)<>''),
  source_checksum text not null check (source_checksum ~ '^[0-9a-f]{64}$'),
  results jsonb not null check (jsonb_typeof(results)='array' and jsonb_array_length(results)>0),
  idempotency_key text not null unique check (btrim(idempotency_key)<>''),
  created_at timestamptz not null default now(),
  unique(job_id,builder_key)
);
create trigger npi_device_evidence_append_only before update or delete on ops.npi_device_evidence_receipt
  for each row execute function ops.refuse_job_evidence_rewrite();

create or replace function ops.record_npi_device_evidence(
  p_job_id uuid,p_observed_at timestamptz,p_source_release text,p_source_checksum text,
  p_results jsonb,p_idempotency_key text
) returns uuid language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare principal ops.device_evidence_principal%rowtype; j ops.job%rowtype;
        existing ops.npi_device_evidence_receipt%rowtype; result jsonb; item jsonb; rid uuid;
begin
  select * into principal from ops.device_evidence_principal where login_role=session_user and active;
  if not found then raise exception 'NPI evidence session user is not an active provisioned principal'; end if;
  select * into j from ops.job where id=p_job_id for share;
  if not found or j.definition_key<>'npi-sweep-weekly' or j.state not in ('queued','retry_wait','running') then
    raise exception 'NPI evidence must bind an open npi-sweep-weekly ledger job'; end if;
  if p_observed_at < j.scheduled_for-interval '24 hours' or p_observed_at > j.scheduled_for+interval '24 hours'
     or p_observed_at > now()+interval '5 minutes' then raise exception 'NPI evidence is outside its ledger freshness window'; end if;
  if btrim(coalesce(p_source_release,''))='' or coalesce(p_source_checksum,'') !~ '^[0-9a-f]{64}$'
     or jsonb_typeof(p_results)<>'array' or jsonb_array_length(p_results)=0 then
    raise exception 'NPI evidence requires release, SHA-256 checksum, and nonempty result array'; end if;
  for item in select value from jsonb_array_elements(p_results) loop
    if jsonb_typeof(item)<>'object' or (item - array['source_ref','npi','enumeration_type','last_updated','addresses','taxonomies'])<>'{}'::jsonb
       or btrim(coalesce(item->>'source_ref',''))='' or coalesce(item->>'npi','') !~ '^[0-9]{10}$'
       or btrim(coalesce(item->>'enumeration_type',''))='' or btrim(coalesce(item->>'last_updated',''))=''
       or jsonb_typeof(item->'addresses')<>'array' or jsonb_typeof(item->'taxonomies')<>'array'
       or jsonb_array_length(item->'taxonomies')=0
       or exists (select 1 from jsonb_array_elements(item->'taxonomies') code where jsonb_typeof(code)<>'string')
       or exists (select 1 from jsonb_array_elements(item->'addresses') address
                   where jsonb_typeof(address)<>'object' or jsonb_typeof(address->'postal_code')<>'string') then
      raise exception 'NPI result does not match the typed raw NPPES contract'; end if;
  end loop;
  select * into existing from ops.npi_device_evidence_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.job_id<>p_job_id or existing.device_id<>principal.device_id or existing.observed_at<>p_observed_at
       or existing.source_release<>p_source_release or existing.source_checksum<>p_source_checksum or existing.results<>p_results then
      raise exception 'NPI evidence idempotency key was reused with different evidence'; end if;
    return existing.id;
  end if;
  insert into ops.npi_device_evidence_receipt(job_id,builder_key,workflow_key,workflow_version,mode,scheduled_for,
      device_id,observed_at,source_release,source_checksum,results,idempotency_key)
    values(j.id,'npi.weekly-delta',j.definition_key,j.definition_version,j.mode,j.scheduled_for,
      principal.device_id,p_observed_at,p_source_release,p_source_checksum,p_results,p_idempotency_key)
    returning id into rid;
  return rid;
end $$;

revoke all on ops.npi_device_evidence_receipt from public;
revoke all on function ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text) from public,carr_jobs,carr_writer;
grant select on ops.npi_device_evidence_receipt to carr_jobs;
grant execute on function ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text) to carr_device_evidence;

do $$ begin
  if has_table_privilege('carr_jobs','ops.npi_device_evidence_receipt','insert')
     or has_function_privilege('carr_jobs','ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.record_npi_device_evidence(uuid,timestamptz,text,text,jsonb,text)'::regprocedure,'execute') then
    raise exception '0167 FAILED: routine role can mint NPI evidence'; end if;
end $$;
commit;
