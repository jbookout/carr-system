-- 0372 / SIEP-15 / SCAC-05: signed device facts and benchmark-gated optional Studio enrollment.
-- Source/test implementation only. Proof of possession is SIEP-16; privileges and
-- routing remain false, and applying this migration to Production remains Joe-gated.

create table ops.scac_device_enrollment (
  device_ref text primary key check (device_ref ~ '^[a-z0-9][a-z0-9._-]{2,127}$'),
  sponsor text not null check (sponsor='joe'),
  profile_key text not null check (profile_key='studio-executor'),
  device_key_digest text not null unique check (device_key_digest ~ '^sha256:[0-9a-f]{64}$'),
  device_public_key bytea not null check (octet_length(device_public_key)=32),
  policy_epoch bigint not null,
  policy_epoch_digest text not null check (policy_epoch_digest ~ '^sha256:[0-9a-f]{64}$'),
  facts_digest text not null unique check (facts_digest ~ '^sha256:[0-9a-f]{64}$'),
  lifecycle_state text not null default 'registered_pending_siep16_pop'
    check (lifecycle_state='registered_pending_siep16_pop'),
  optional_non_blocking boolean not null default true check (optional_non_blocking),
  source_of_truth boolean not null default false check (not source_of_truth),
  critical_dependency boolean not null default false check (not critical_dependency),
  routing_eligible boolean not null default false check (not routing_eligible),
  privileges_active boolean not null default false check (not privileges_active),
  production_enforcement_active boolean not null default false check (not production_enforcement_active),
  recorded_at timestamptz not null default clock_timestamp(),
  foreign key (policy_epoch,policy_epoch_digest)
    references ops.scac_policy_epoch(epoch,epoch_digest) on delete restrict,
  check (device_key_digest='sha256:'||encode(public.digest(device_public_key,'sha256'),'hex'))
);

create table ops.scac_device_fact_receipt (
  device_ref text primary key references ops.scac_device_enrollment(device_ref) on delete restrict,
  schema_version text not null check (schema_version='scac-device-facts.v1'),
  facts jsonb not null check (jsonb_typeof(facts)='object' and
    facts ?& array['architecture','cpu_core_count','cpu_identifier','exact_model_identifier',
      'filevault_state','gpu_core_count','memory_bytes','observed_at','os_build','os_version',
      'sip_state','storage_bytes','virtualization_entitlement'] and
    facts-array['architecture','cpu_core_count','cpu_identifier','exact_model_identifier',
      'filevault_state','gpu_core_count','memory_bytes','observed_at','os_build','os_version',
      'sip_state','storage_bytes','virtualization_entitlement']='{}'::jsonb and
    jsonb_typeof(facts->'architecture')='string' and
    jsonb_typeof(facts->'cpu_core_count')='number' and
    jsonb_typeof(facts->'gpu_core_count')='number' and
    jsonb_typeof(facts->'memory_bytes')='number' and
    jsonb_typeof(facts->'storage_bytes')='number' and
    jsonb_typeof(facts->'exact_model_identifier')='string' and
    jsonb_typeof(facts->'cpu_identifier')='string' and
    jsonb_typeof(facts->'os_version')='string' and
    jsonb_typeof(facts->'os_build')='string' and
    jsonb_typeof(facts->'observed_at')='string' and
    jsonb_typeof(facts->'filevault_state')='string' and
    jsonb_typeof(facts->'sip_state')='string' and
    jsonb_typeof(facts->'virtualization_entitlement')='boolean' and
    facts->>'architecture'='arm64' and facts->>'filevault_state'='enabled' and
    facts->>'sip_state'='enabled' and facts->'virtualization_entitlement'='true'::jsonb and
    (facts->>'cpu_core_count')::numeric between 1 and 9007199254740991 and
    mod((facts->>'cpu_core_count')::numeric,1)=0 and
    (facts->>'gpu_core_count')::numeric between 1 and 9007199254740991 and
    mod((facts->>'gpu_core_count')::numeric,1)=0 and
    (facts->>'memory_bytes')::numeric between 1 and 9007199254740991 and
    mod((facts->>'memory_bytes')::numeric,1)=0 and
    (facts->>'storage_bytes')::numeric between 1 and 9007199254740991 and
    mod((facts->>'storage_bytes')::numeric,1)=0 and
    char_length(facts->>'exact_model_identifier') between 1 and 200 and
    btrim(facts->>'exact_model_identifier')<>'' and
    char_length(facts->>'cpu_identifier') between 1 and 200 and
    btrim(facts->>'cpu_identifier')<>'' and
    char_length(facts->>'os_version') between 1 and 200 and
    btrim(facts->>'os_version')<>'' and
    char_length(facts->>'os_build') between 1 and 200 and
    btrim(facts->>'os_build')<>''),
  observed_at text not null check (
    observed_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$' and
    observed_at=(facts->>'observed_at') and observed_at::timestamptz is not null),
  facts_digest text not null unique check (facts_digest ~ '^sha256:[0-9a-f]{64}$'),
  algorithm text not null check (algorithm='ed25519'),
  signature_bytes bytea not null check (octet_length(signature_bytes)=64),
  signature_digest text not null unique check (signature_digest ~ '^sha256:[0-9a-f]{64}$'),
  signed_payload_digest text not null check (signed_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  verifier_contract text not null check (verifier_contract='mcp-server/src/device-enrollment.js'),
  cryptographic_device_state text not null default 'external_verification_required'
    check (cryptographic_device_state='external_verification_required'),
  recorded_at timestamptz not null default clock_timestamp(),
  production_enforcement_active boolean not null default false check (not production_enforcement_active),
  check (signature_digest='sha256:'||encode(public.digest(signature_bytes,'sha256'),'hex')),
  check (signed_payload_digest=facts_digest)
);

create table ops.scac_device_benchmark_receipt (
  device_ref text not null references ops.scac_device_enrollment(device_ref) on delete restrict,
  benchmark_kind text not null check (benchmark_kind in (
    'thermal_sustained_cpu_gpu','ssd','vm_isolation','mlx_inference_context_memory',
    'concurrent_jobs','reboot_power_loss','network_egress','workload_quotas','failover')),
  schema_version text not null check (schema_version='scac-device-benchmark.v1'),
  facts_digest text not null check (facts_digest ~ '^sha256:[0-9a-f]{64}$'),
  profile_digest text not null check (
    profile_digest='sha256:60eb0ebfb46d7155cf71ec479b38bd24e5d0c31051923382fd91ec2faf524762'),
  metrics_digest text not null check (metrics_digest ~ '^sha256:[0-9a-f]{64}$'),
  passed boolean not null check (passed),
  observed_at text not null check (
    observed_at ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$' and
    observed_at::timestamptz is not null),
  receipt_digest text not null unique check (receipt_digest ~ '^sha256:[0-9a-f]{64}$'),
  algorithm text not null check (algorithm='ed25519'),
  signature_bytes bytea not null check (octet_length(signature_bytes)=64),
  signature_digest text not null unique check (signature_digest ~ '^sha256:[0-9a-f]{64}$'),
  signed_payload_digest text not null check (signed_payload_digest ~ '^sha256:[0-9a-f]{64}$'),
  verifier_contract text not null check (verifier_contract='mcp-server/src/device-enrollment.js'),
  cryptographic_device_state text not null default 'external_verification_required'
    check (cryptographic_device_state='external_verification_required'),
  routing_eligible boolean not null default false check (not routing_eligible),
  privileges_active boolean not null default false check (not privileges_active),
  production_enforcement_active boolean not null default false check (not production_enforcement_active),
  recorded_at timestamptz not null default clock_timestamp(),
  primary key (device_ref,benchmark_kind),
  check (signature_digest='sha256:'||encode(public.digest(signature_bytes,'sha256'),'hex')),
  check (signed_payload_digest=receipt_digest)
);

comment on table ops.scac_device_enrollment is
  'SIEP-15 optional Joe-side Studio descriptor. It is never source of truth, a critical dependency, routable, privileged, or Production-active at this stage.';
comment on table ops.scac_device_fact_receipt is
  'SIEP-15 signed exact hardware/OS facts. SQL preserves structural evidence; Ed25519 and fresh PoP verification remain external/SIEP-16.';
comment on table ops.scac_device_benchmark_receipt is
  'SIEP-15 signed benchmark evidence. Complete receipts do not grant routing or privileges.';

create or replace function ops.scac_device_fact_payload_digest(
  p_device_ref text,p_sponsor text,p_profile_key text,p_facts jsonb,
  p_policy_epoch bigint,p_policy_epoch_digest text
) returns text language sql immutable set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-device-facts.v1','device_ref',p_device_ref,'sponsor',p_sponsor,
    'profile_key',p_profile_key,'facts',p_facts||jsonb_build_object(
      'cpu_core_count',(p_facts->'cpu_core_count')::numeric::bigint,
      'gpu_core_count',(p_facts->'gpu_core_count')::numeric::bigint,
      'memory_bytes',(p_facts->'memory_bytes')::numeric::bigint,
      'storage_bytes',(p_facts->'storage_bytes')::numeric::bigint),
    'policy_epoch',p_policy_epoch,
    'policy_epoch_digest',p_policy_epoch_digest)),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_device_benchmark_payload_digest(
  p_benchmark_kind text,p_device_ref text,p_facts_digest text,p_profile_digest text,
  p_metrics_digest text,p_passed boolean,p_observed_at text
) returns text language sql immutable set search_path=pg_catalog,public,ops as $fn$
  select 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-device-benchmark.v1','benchmark_kind',p_benchmark_kind,
    'device_ref',p_device_ref,'facts_digest',p_facts_digest,'profile_digest',p_profile_digest,
    'metrics_digest',p_metrics_digest,'passed',p_passed,'observed_at',p_observed_at)),'UTF8'),'sha256'),'hex')
$fn$;

create or replace function ops.scac_device_fact_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare e ops.scac_device_enrollment%rowtype; expected text;
begin
  select * into e from ops.scac_device_enrollment where device_ref=new.device_ref for key share;
  expected:=ops.scac_device_fact_payload_digest(new.device_ref,e.sponsor,e.profile_key,new.facts,
    e.policy_epoch,e.policy_epoch_digest);
  if e.device_ref is null or new.facts_digest is distinct from e.facts_digest or
     new.facts_digest is distinct from expected or new.signed_payload_digest is distinct from expected then
    raise exception 'SIEP-15 device facts do not bind the registered device and policy epoch';
  end if;
  return new;
end $fn$;

create or replace function ops.scac_device_benchmark_insert_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,public,ops as $fn$
declare e ops.scac_device_enrollment%rowtype; expected text;
begin
  select * into e from ops.scac_device_enrollment where device_ref=new.device_ref for key share;
  expected:=ops.scac_device_benchmark_payload_digest(new.benchmark_kind,new.device_ref,
    new.facts_digest,new.profile_digest,new.metrics_digest,new.passed,new.observed_at);
  if e.device_ref is null or not exists(select 1 from ops.scac_device_fact_receipt f
       where f.device_ref=new.device_ref and f.facts_digest=new.facts_digest) or
     new.facts_digest is distinct from e.facts_digest or new.receipt_digest is distinct from expected or
     new.signed_payload_digest is distinct from expected then
    raise exception 'SIEP-15 benchmark does not bind signed device facts and the reviewed profile';
  end if;
  return new;
end $fn$;

create or replace function ops.scac_device_enrollment_status(p_device_ref text)
returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops as $fn$
declare e ops.scac_device_enrollment%rowtype; fact_count integer; benchmark_count integer;
begin
  if session_user<>'carr_authority_joe' then
    raise exception 'SIEP-15 Joe Studio status authority refused';
  end if;
  select * into e from ops.scac_device_enrollment where device_ref=p_device_ref;
  if e.device_ref is null then
    return jsonb_build_object('found',false,'reason_id','scac.refusal.device_not_registered',
      'routing_eligible',false,'privileges_active',false,'production_enforcement_active',false);
  end if;
  select count(*) into fact_count from ops.scac_device_fact_receipt where device_ref=e.device_ref;
  select count(*) into benchmark_count from ops.scac_device_benchmark_receipt
    where device_ref=e.device_ref and passed and facts_digest=e.facts_digest;
  return jsonb_build_object('found',true,'device_ref',e.device_ref,'sponsor',e.sponsor,
    'profile_key',e.profile_key,'facts_digest',e.facts_digest,'enrollment_state',e.lifecycle_state,
    'cryptographic_device_state','external_verification_required','pop_state','pending_siep16',
    'benchmark_receipt_count',benchmark_count,'benchmark_receipt_required',9,
    'assurance_state',case when fact_count=1 and benchmark_count=9
      then 'structurally_complete_non_authorizing' else 'receipts_incomplete' end,
    'optional_non_blocking',true,'source_of_truth',false,'critical_dependency',false,
    'routing_eligible',false,'privileges_active',false,'production_enforcement_active',false);
end $fn$;

create or replace function ops.scac_device_enrollment_append_only_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-15 device enrollment facts are append-only'; end $fn$;
create or replace function ops.scac_device_enrollment_truncate_guard()
returns trigger language plpgsql security definer set search_path=pg_catalog,ops as $fn$
begin raise exception 'SIEP-15 device enrollment facts cannot be truncated'; end $fn$;

create trigger scac_device_fact_insert_exact before insert on ops.scac_device_fact_receipt
for each row execute function ops.scac_device_fact_insert_guard();
create trigger scac_device_benchmark_insert_exact before insert on ops.scac_device_benchmark_receipt
for each row execute function ops.scac_device_benchmark_insert_guard();
create trigger scac_device_enrollment_append_only before update or delete on ops.scac_device_enrollment
for each row execute function ops.scac_device_enrollment_append_only_guard();
create trigger scac_device_fact_append_only before update or delete on ops.scac_device_fact_receipt
for each row execute function ops.scac_device_enrollment_append_only_guard();
create trigger scac_device_benchmark_append_only before update or delete on ops.scac_device_benchmark_receipt
for each row execute function ops.scac_device_enrollment_append_only_guard();
create trigger scac_device_enrollment_no_truncate before truncate on ops.scac_device_enrollment
for each statement execute function ops.scac_device_enrollment_truncate_guard();
create trigger scac_device_fact_no_truncate before truncate on ops.scac_device_fact_receipt
for each statement execute function ops.scac_device_enrollment_truncate_guard();
create trigger scac_device_benchmark_no_truncate before truncate on ops.scac_device_benchmark_receipt
for each statement execute function ops.scac_device_enrollment_truncate_guard();

revoke all on ops.scac_device_enrollment,ops.scac_device_fact_receipt,
  ops.scac_device_benchmark_receipt from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.scac_device_fact_payload_digest(text,text,text,jsonb,bigint,text),
  ops.scac_device_benchmark_payload_digest(text,text,text,text,text,boolean,text),
  ops.scac_device_fact_insert_guard(),ops.scac_device_benchmark_insert_guard(),
  ops.scac_device_enrollment_status(text),ops.scac_device_enrollment_append_only_guard(),
  ops.scac_device_enrollment_truncate_guard()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.scac_device_enrollment_status(text)
  to carr_authority;

do $assert$
begin
  if exists(select 1 from information_schema.role_table_grants where table_schema='ops'
    and table_name in ('scac_device_enrollment','scac_device_fact_receipt','scac_device_benchmark_receipt')
    and grantee in ('PUBLIC','carr_reader','carr_writer','carr_jobs','carr_authority')) then
    raise exception 'SIEP-15 runtime roles unexpectedly received raw device enrollment authority';
  end if;
end $assert$;
