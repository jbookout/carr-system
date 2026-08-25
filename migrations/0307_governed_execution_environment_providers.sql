-- 0307: governed execution-environment providers for Hermes and CARR.
--
-- Hermes owns terminal backend mechanics. CARR owns admission, immutable
-- provider selection, exact ExecutionEnvelope binding, receipt evidence,
-- human promotion, and rollback. This extends the existing 0303 spine; it
-- creates no workflow engine, receipt type, capability source, or plugin loader.

begin;

create table ops.execution_environment_provider (
  id uuid primary key default gen_random_uuid(),
  provider_key text not null check (provider_key ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'),
  provider_version integer not null check (provider_version > 0),
  source_class text not null check (source_class in ('built_in','plugin')),
  backend_kind text not null check (backend_kind in ('none','local','container','remote','cloud')),
  manifest_digest text not null unique check (manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  manifest jsonb not null check (jsonb_typeof(manifest)='object' and manifest->>'schema_version'='execution-environment-provider.v1' and manifest->>'contains_secrets'='false'),
  protected_builtin boolean not null,
  created_by_actor_id uuid not null references actor(id),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp(),
  unique(provider_key,provider_version)
);

create table ops.execution_environment_provider_event (
  id uuid primary key default gen_random_uuid(),
  provider_id uuid not null references ops.execution_environment_provider(id),
  from_state text,
  to_state text not null check (to_state in ('discovered','quarantined','conformance_passed','shadow','canary','active','disabled','retired')),
  evidence_refs jsonb not null check (jsonb_typeof(evidence_refs)='array' and jsonb_array_length(evidence_refs)>0),
  ruled_by_actor_id uuid not null references actor(id),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp()
);

create index execution_environment_provider_event_latest_idx
  on ops.execution_environment_provider_event(provider_id,created_at desc,id desc);

create table ops.execution_environment_conformance (
  id uuid primary key default gen_random_uuid(),
  provider_id uuid not null references ops.execution_environment_provider(id),
  contract_ref text not null,
  contract_digest text not null check (contract_digest ~ '^sha256:[0-9a-f]{64}$'),
  run_ref text not null,
  run_digest text not null check (run_digest ~ '^sha256:[0-9a-f]{64}$'),
  manifest_digest text not null check (manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  implementation_digest text not null check (implementation_digest ~ '^sha256:[0-9a-f]{64}$'),
  package_digest text not null check (package_digest ~ '^sha256:[0-9a-f]{64}$'),
  configuration_schema_digest text not null check (configuration_schema_digest ~ '^sha256:[0-9a-f]{64}$'),
  status text not null check (status in ('passed','failed')),
  check_refs jsonb not null check (jsonb_typeof(check_refs)='array' and jsonb_array_length(check_refs)>0),
  evidence_refs jsonb not null check (jsonb_typeof(evidence_refs)='array' and jsonb_array_length(evidence_refs)>0),
  observation jsonb not null check (jsonb_typeof(observation)='object'),
  observed_at timestamptz not null,
  recorded_by_actor_id uuid not null references actor(id),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp(),
  unique(provider_id,run_ref,run_digest)
);

create table ops.work_request_execution_environment_binding (
  id uuid primary key default gen_random_uuid(),
  assignment_id uuid not null unique references ops.work_request_execution_assignment(id),
  provider_id uuid not null references ops.execution_environment_provider(id),
  conformance_id uuid not null references ops.execution_environment_conformance(id),
  requirement jsonb not null check (jsonb_typeof(requirement)='object'),
  requirement_digest text not null check (requirement_digest ~ '^sha256:[0-9a-f]{64}$'),
  configuration jsonb not null check (jsonb_typeof(configuration)='object'),
  configuration_digest text not null check (configuration_digest ~ '^sha256:[0-9a-f]{64}$'),
  binding jsonb not null check (jsonb_typeof(binding)='object'),
  binding_digest text not null check (binding_digest ~ '^sha256:[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp()
);

create or replace function ops.execution_environment_append_only_guard()
returns trigger language plpgsql as $$
begin
  raise exception 'execution environment evidence is append-only; append a lifecycle event instead';
end $$;

create trigger execution_environment_provider_append_only
  before update or delete on ops.execution_environment_provider
  for each row execute function ops.execution_environment_append_only_guard();
create trigger execution_environment_provider_event_append_only
  before update or delete on ops.execution_environment_provider_event
  for each row execute function ops.execution_environment_append_only_guard();
create trigger execution_environment_conformance_append_only
  before update or delete on ops.execution_environment_conformance
  for each row execute function ops.execution_environment_append_only_guard();
create trigger work_request_execution_environment_binding_append_only
  before update or delete on ops.work_request_execution_environment_binding
  for each row execute function ops.execution_environment_append_only_guard();

create or replace function ops.execution_environment_provider_current_state(p_provider_id uuid)
returns text language sql stable set search_path=ops,pg_temp as $$
  select e.to_state from ops.execution_environment_provider_event e
   where e.provider_id=p_provider_id order by e.created_at desc,e.id desc limit 1
$$;

create or replace function ops.register_execution_environment_provider(
  p_manifest jsonb,p_idempotency_key uuid
) returns table(provider_ref text,manifest_digest text,state text,replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare actor_row actor%rowtype; existing ops.execution_environment_provider%rowtype;
  row_out ops.execution_environment_provider%rowtype; digest_value text; allowed text[] := array[
    'schema_version','provider_key','provider_version','display_name','source_class','backend_kind',
    'implementation_ref','implementation_digest','capability_refs','operation_refs','isolation_class',
    'egress_policy_ref','secret_policy_ref','persistence_mode','resource_policy_ref','cleanup_policy_ref',
    'threat_model_ref','conformance_contract_ref','conformance_contract_digest','configuration_schema_digest',
    'package_provenance','collision_policy','contains_secrets','manifest_digest'];
begin
  if session_user !~ '^carr_authority_' then raise exception 'provider registration requires human authority'; end if;
  select * into actor_row from actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  if actor_row.id is null or jsonb_typeof(p_manifest)<>'object'
     or not (p_manifest ?& allowed)
     or exists(select 1 from jsonb_object_keys(p_manifest) k where k<>all(allowed))
     or p_manifest->>'schema_version'<>'execution-environment-provider.v1'
     or p_manifest->>'source_class'<>'plugin' or p_manifest->>'collision_policy'<>'digest_pinned'
     or p_manifest->>'contains_secrets'<>'false'
     or p_manifest->>'provider_key' !~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'
     or p_manifest->>'provider_key'=any(array['hermes-local','hermes-docker','hermes-ssh','hermes-singularity','hermes-modal','hermes-daytona','hermes-vercel-sandbox'])
     or coalesce((p_manifest->>'provider_version')::integer,0)<1
     or p_manifest->>'backend_kind' not in ('none','local','container','remote','cloud')
     or p_manifest->>'isolation_class' not in ('none','host_process','container','microvm','remote_host')
     or jsonb_typeof(p_manifest->'capability_refs')<>'array' or jsonb_array_length(p_manifest->'capability_refs')=0
     or jsonb_typeof(p_manifest->'operation_refs')<>'array'
     or not (p_manifest->'operation_refs' ?& array['operation:create','operation:exec','operation:cancel','operation:destroy','operation:health'])
     or jsonb_typeof(p_manifest->'package_provenance')<>'object'
     or not (p_manifest->'package_provenance' ?& array['package_ref','package_digest','signature_ref','sbom_ref'])
     or exists(select 1 from jsonb_object_keys(p_manifest->'package_provenance') k where k<>all(array['package_ref','package_digest','signature_ref','sbom_ref']))
     or p_manifest->>'display_name' !~ '^.{1,80}$'
     or p_manifest->>'display_name' ~ '[[:cntrl:]]'
     or p_manifest->>'implementation_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'implementation_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->>'conformance_contract_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'conformance_contract_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->>'configuration_schema_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->'package_provenance'->>'package_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->'package_provenance'->>'package_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->'package_provenance'->>'signature_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->'package_provenance'->>'sbom_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'persistence_mode' not in ('none','command_scoped','session_scoped','durable_workspace')
     or exists(select 1 from unnest(array['egress_policy_ref','secret_policy_ref','resource_policy_ref','cleanup_policy_ref','threat_model_ref']) field where p_manifest->>field !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or exists(select 1 from jsonb_array_elements_text(p_manifest->'operation_refs') op where op !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or (select count(*) from jsonb_array_elements_text(p_manifest->'operation_refs'))<>(select count(distinct op) from jsonb_array_elements_text(p_manifest->'operation_refs') op)
     or (select count(*) from jsonb_array_elements_text(p_manifest->'capability_refs'))<>(select count(distinct cap) from jsonb_array_elements_text(p_manifest->'capability_refs') cap)
     or exists(select 1 from jsonb_array_elements_text(p_manifest->'capability_refs') c where c not in ('environment:none','environment:exec','environment:filesystem','environment:process','environment:network-governed','environment:snapshot','environment:transfer','environment:persistent-workspace')) then
    raise exception 'execution environment plugin manifest is not closed, safe, or complete';
  end if;
  digest_value := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_manifest-'manifest_digest'),'sha256'),'hex');
  if p_manifest->>'manifest_digest' is distinct from digest_value then raise exception 'execution environment plugin manifest digest mismatch'; end if;
  select * into existing from ops.execution_environment_provider where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.manifest is distinct from p_manifest then raise exception 'execution environment provider idempotency conflict'; end if;
    return query select 'environment-provider:'||existing.provider_key||':v'||existing.provider_version,existing.manifest_digest,ops.execution_environment_provider_current_state(existing.id),true; return;
  end if;
  if exists(select 1 from ops.execution_environment_provider p where p.provider_key=p_manifest->>'provider_key' and (p.protected_builtin or p.provider_version>=(p_manifest->>'provider_version')::integer)) then
    raise exception 'execution environment provider key/version is protected, stale, or already registered';
  end if;
  insert into ops.execution_environment_provider(provider_key,provider_version,source_class,backend_kind,manifest_digest,manifest,protected_builtin,created_by_actor_id,idempotency_key)
  values(p_manifest->>'provider_key',(p_manifest->>'provider_version')::integer,'plugin',p_manifest->>'backend_kind',digest_value,p_manifest,false,actor_row.id,p_idempotency_key) returning * into row_out;
  insert into ops.execution_environment_provider_event(provider_id,from_state,to_state,evidence_refs,ruled_by_actor_id,idempotency_key)
  values(row_out.id,null,'discovered',jsonb_build_array('evidence:human-provider-registration'),actor_row.id,p_idempotency_key);
  return query select 'environment-provider:'||row_out.provider_key||':v'||row_out.provider_version,row_out.manifest_digest,'discovered',false;
end $$;

create or replace function ops.attest_execution_environment_conformance(
  p_provider_ref text,p_observation jsonb,p_idempotency_key uuid
) returns table(conformance_id uuid,replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare actor_row actor%rowtype; provider ops.execution_environment_provider%rowtype; existing ops.execution_environment_conformance%rowtype;
  allowed text[] := array['schema_version','provider_ref','manifest_digest','implementation_digest','package_digest','configuration_schema_digest','contract_ref','contract_digest','run_ref','status','check_results','version_ref','backend_kind','evidence_refs','contains_secrets','run_digest','observed_at'];
  derived_run_digest text; observed_at_value timestamptz;
begin
  if session_user !~ '^carr_authority_' then raise exception 'environment conformance attestation requires human authority'; end if;
  select * into actor_row from actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  select * into provider from ops.execution_environment_provider p where p_provider_ref='environment-provider:'||p.provider_key||':v'||p.provider_version for share;
  begin observed_at_value := (p_observation->>'observed_at')::timestamptz; exception when others then raise exception 'environment conformance observed_at is invalid'; end;
  derived_run_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_observation-'run_digest'-'observed_at'),'sha256'),'hex');
  if actor_row.id is null or provider.id is null or jsonb_typeof(p_observation)<>'object'
     or not (p_observation ?& allowed) or exists(select 1 from jsonb_object_keys(p_observation) k where k<>all(allowed))
     or p_observation->>'schema_version'<>'execution-environment-conformance.v1'
     or p_observation->>'provider_ref'<>p_provider_ref
     or p_observation->>'manifest_digest'<>provider.manifest_digest
     or p_observation->>'implementation_digest'<>provider.manifest->>'implementation_digest'
     or p_observation->>'package_digest'<>provider.manifest->'package_provenance'->>'package_digest'
     or p_observation->>'configuration_schema_digest'<>provider.manifest->>'configuration_schema_digest'
     or p_observation->>'contract_ref'<>provider.manifest->>'conformance_contract_ref'
     or p_observation->>'contract_digest'<>provider.manifest->>'conformance_contract_digest'
     or p_observation->>'run_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_observation->>'run_digest' is distinct from derived_run_digest
     or p_observation->>'status' not in ('passed','failed')
     or p_observation->>'backend_kind'<>provider.backend_kind
     or p_observation->>'version_ref' !~ '^[^[:cntrl:]]{1,160}$'
     or p_observation->>'contains_secrets'<>'false'
     or jsonb_typeof(p_observation->'check_results')<>'object' or p_observation->'check_results'='{}'::jsonb
     or exists(select 1 from jsonb_each(p_observation->'check_results') c where c.key !~ '^check:[a-z0-9-]+$' or jsonb_typeof(c.value)<>'boolean')
     or (p_observation->>'status'='passed' and exists(select 1 from jsonb_each(p_observation->'check_results') c where c.value<>'true'::jsonb))
     or (p_observation->>'status'='failed' and not exists(select 1 from jsonb_each(p_observation->'check_results') c where c.value='false'::jsonb))
     or jsonb_typeof(p_observation->'evidence_refs')<>'array' or jsonb_array_length(p_observation->'evidence_refs')=0
     or exists(select 1 from jsonb_array_elements_text(p_observation->'evidence_refs') value where value !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or observed_at_value>clock_timestamp() then
    raise exception 'environment conformance attestation is invalid';
  end if;
  select * into existing from ops.execution_environment_conformance where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.provider_id<>provider.id or existing.observation is distinct from p_observation then raise exception 'environment conformance idempotency conflict'; end if;
    return query select existing.id,true; return;
  end if;
  insert into ops.execution_environment_conformance(provider_id,contract_ref,contract_digest,run_ref,run_digest,manifest_digest,implementation_digest,package_digest,configuration_schema_digest,status,check_refs,evidence_refs,observation,observed_at,recorded_by_actor_id,idempotency_key)
  values(provider.id,provider.manifest->>'conformance_contract_ref',provider.manifest->>'conformance_contract_digest',p_observation->>'run_ref',derived_run_digest,provider.manifest_digest,provider.manifest->>'implementation_digest',provider.manifest->'package_provenance'->>'package_digest',provider.manifest->>'configuration_schema_digest',p_observation->>'status',to_jsonb(array(select key from jsonb_each(p_observation->'check_results') order by key)),p_observation->'evidence_refs',p_observation,observed_at_value,actor_row.id,p_idempotency_key)
  returning id into conformance_id;
  return query select conformance_id,false;
end $$;

create or replace function ops.transition_execution_environment_provider(
  p_provider_ref text,p_expected_state text,p_target_state text,p_evidence_refs jsonb,p_idempotency_key uuid
) returns table(provider_ref text,state text,replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare actor_row actor%rowtype; provider ops.execution_environment_provider%rowtype; current_state text; existing ops.execution_environment_provider_event%rowtype;
begin
  if session_user !~ '^carr_authority_' then raise exception 'environment provider transition requires human authority'; end if;
  select * into actor_row from actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  -- The immutable provider row is the lifecycle stream's serialization head.
  -- The second concurrent CAS caller cannot inspect state until the first
  -- commits, so it must then fail the expected-state comparison below.
  select * into provider from ops.execution_environment_provider p where p_provider_ref='environment-provider:'||p.provider_key||':v'||p.provider_version for update;
  if actor_row.id is null or provider.id is null or jsonb_typeof(p_evidence_refs)<>'array' or jsonb_array_length(p_evidence_refs)=0
     or exists(select 1 from jsonb_array_elements_text(p_evidence_refs) value where value !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$') then
    raise exception 'environment provider transition lacks valid authority, provider, or evidence';
  end if;
  select * into existing from ops.execution_environment_provider_event where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.provider_id<>provider.id or existing.from_state is distinct from p_expected_state or existing.to_state<>p_target_state or existing.evidence_refs is distinct from p_evidence_refs then raise exception 'environment provider transition idempotency conflict'; end if;
    return query select p_provider_ref,existing.to_state,true; return;
  end if;
  current_state := ops.execution_environment_provider_current_state(provider.id);
  if current_state is distinct from p_expected_state
     or not ((p_expected_state='discovered' and p_target_state='quarantined')
       or (p_expected_state='quarantined' and p_target_state='conformance_passed')
       or (p_expected_state='conformance_passed' and p_target_state='shadow')
       or (p_expected_state='shadow' and p_target_state='canary')
       or (p_expected_state='canary' and p_target_state='active')
       or (p_expected_state='active' and p_target_state='disabled')
       or (p_expected_state='disabled' and p_target_state='canary')
       or (p_expected_state<>'retired' and p_target_state='retired')) then
    raise exception 'environment provider transition is stale or forbidden';
  end if;
  if p_target_state in ('conformance_passed','shadow','canary','active') and coalesce((
    select c.status from ops.execution_environment_conformance c where c.provider_id=provider.id order by c.observed_at desc,c.id desc limit 1),'unavailable')<>'passed' then
    raise exception 'environment provider cannot advance without passed conformance';
  end if;
  insert into ops.execution_environment_provider_event(provider_id,from_state,to_state,evidence_refs,ruled_by_actor_id,idempotency_key)
  values(provider.id,p_expected_state,p_target_state,p_evidence_refs,actor_row.id,p_idempotency_key);
  return query select p_provider_ref,p_target_state,false;
end $$;

do $$
declare joe_id uuid; provider_id uuid; conformance_id uuid; base jsonb; manifest jsonb; manifest_digest text; run_digest text; observation jsonb; observed_at_value timestamptz;
begin
  select id into joe_id from actor where slug='joe' and kind='human' and active;
  if joe_id is null then raise exception '0307 requires active Joe actor for approved reference-provider admission'; end if;
  base := jsonb_build_object(
    'schema_version','execution-environment-provider.v1','provider_key','hermes-local','provider_version',1,
    'display_name','Hermes Local Terminal','source_class','built_in','backend_kind','local',
    'implementation_ref','hermes:tools.environments.local.LocalEnvironment',
    'implementation_digest','sha256:7d680c252bedc88ff7b80d50a5bfbdb9b926823d8bbc521f606e7b58237cbc1e',
    'capability_refs',jsonb_build_array('environment:exec','environment:filesystem','environment:process'),
    'operation_refs',jsonb_build_array('operation:create','operation:exec','operation:cancel','operation:destroy','operation:health'),
    'isolation_class','host_process','egress_policy_ref','egress:host-governed','secret_policy_ref','secrets:never-in-manifest',
    'persistence_mode','session_scoped','resource_policy_ref','resources:bounded-local-v1','cleanup_policy_ref','cleanup:process-tree-v1',
    'threat_model_ref','threat-model:local-trusted-input-v1','conformance_contract_ref','conformance:execution-environment-v1',
    'conformance_contract_digest','sha256:'||encode(public.digest('conformance:execution-environment-v1','sha256'),'hex'),
    'configuration_schema_digest','sha256:'||encode(public.digest('hermes:terminal.backend:local:v1','sha256'),'hex'),
    'package_provenance',jsonb_build_object('package_ref','package:nous-hermes-agent','package_digest','sha256:'||encode(public.digest('hermes-upstream:1bbb6e5bce56e721ab685af4cd87df21bbff4d35','sha256'),'hex'),'signature_ref','signature:upstream-git-commit','sbom_ref','sbom:hermes-installed-tree'),
    'collision_policy','protected_builtin','contains_secrets',false);
  manifest_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(base),'sha256'),'hex');
  manifest := base||jsonb_build_object('manifest_digest',manifest_digest);
  insert into ops.execution_environment_provider(provider_key,provider_version,source_class,backend_kind,manifest_digest,manifest,protected_builtin,created_by_actor_id,idempotency_key)
  values('hermes-local',1,'built_in','local',manifest_digest,manifest,true,joe_id,'03070000-0000-4000-8000-000000000001') returning id into provider_id;
  observed_at_value := clock_timestamp();
  observation := jsonb_build_object(
    'schema_version','execution-environment-conformance.v1',
    'provider_ref','environment-provider:hermes-local:v1',
    'manifest_digest',manifest_digest,
    'implementation_digest',manifest->>'implementation_digest',
    'package_digest',manifest->'package_provenance'->>'package_digest',
    'configuration_schema_digest',manifest->>'configuration_schema_digest',
    'contract_ref',manifest->>'conformance_contract_ref',
    'contract_digest',manifest->>'conformance_contract_digest',
    'run_ref','conformance-run:hermes-local-release-20260825',
    'status','passed',
    'check_results',jsonb_build_object(
      'check:base-environment-contract-present',true,
      'check:cleanup-contract-declared',true,
      'check:hermes-version-bounded',true,
      'check:implementation-digest-exact',true,
      'check:local-environment-present',true,
      'check:source-secret-scan',true,
      'check:terminal-backend-local',true),
    'version_ref','Hermes Agent v0.20.5 (2026.8.19) · upstream 1bbb6e5b · local 706f33d4 (+1 carried commit)',
    'backend_kind','local',
    'evidence_refs',jsonb_build_array('evidence:hermes-version-readback','evidence:terminal-backend-readback','evidence:installed-environment-contract'),
    'contains_secrets',false,
    'observed_at',to_jsonb(observed_at_value));
  run_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(observation-'observed_at'),'sha256'),'hex');
  observation := observation||jsonb_build_object('run_digest',run_digest);
  insert into ops.execution_environment_conformance(provider_id,contract_ref,contract_digest,run_ref,run_digest,manifest_digest,implementation_digest,package_digest,configuration_schema_digest,status,check_refs,evidence_refs,observation,observed_at,recorded_by_actor_id,idempotency_key)
  values(provider_id,manifest->>'conformance_contract_ref',manifest->>'conformance_contract_digest',observation->>'run_ref',run_digest,manifest_digest,manifest->>'implementation_digest',manifest->'package_provenance'->>'package_digest',manifest->>'configuration_schema_digest','passed',to_jsonb(array(select key from jsonb_each(observation->'check_results') order by key)),observation->'evidence_refs',observation,observed_at_value,joe_id,'03070000-0000-4000-8000-000000000002') returning id into conformance_id;
  insert into ops.execution_environment_provider_event(provider_id,from_state,to_state,evidence_refs,ruled_by_actor_id,idempotency_key) values
    (provider_id,null,'discovered',jsonb_build_array('evidence:tony-simons-terminal-provider-source'),joe_id,'03070000-0000-4000-8000-000000000003'),
    (provider_id,'discovered','quarantined',jsonb_build_array('evidence:provider-contract-review'),joe_id,'03070000-0000-4000-8000-000000000004'),
    (provider_id,'quarantined','conformance_passed',jsonb_build_array('evidence:test-execution-environment-unit'),joe_id,'03070000-0000-4000-8000-000000000005'),
    (provider_id,'conformance_passed','shadow',jsonb_build_array('evidence:hermes-local-existing-baseline'),joe_id,'03070000-0000-4000-8000-000000000006'),
    (provider_id,'shadow','canary',jsonb_build_array('evidence:hermes-local-config-readback'),joe_id,'03070000-0000-4000-8000-000000000007'),
    (provider_id,'canary','active',jsonb_build_array('evidence:joe-approved-provider-foundation'),joe_id,'03070000-0000-4000-8000-000000000008');
end $$;

create or replace function ops.bind_execution_environment_to_assignment()
returns trigger language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare provider ops.execution_environment_provider%rowtype; conformance ops.execution_environment_conformance%rowtype;
  requirement jsonb; configuration jsonb; requirement_digest text; configuration_digest text; binding jsonb; binding_digest text;
begin
  select p.* into provider from ops.execution_environment_provider p
   where p.provider_key='hermes-local' and ops.execution_environment_provider_current_state(p.id)='active'
   order by p.provider_version desc limit 1;
  select c.* into conformance from ops.execution_environment_conformance c
   where c.provider_id=provider.id order by c.observed_at desc,c.id desc limit 1;
  if provider.id is null or conformance.id is null or conformance.status<>'passed' then raise exception 'no admitted execution environment provider satisfies the route'; end if;
  requirement := jsonb_build_object('authority_capability_ref','capability:metadata-only','required_operation_refs',jsonb_build_array('operation:health'),'selection_policy_ref','selection:server-active-provider-v1','side_effect_policy_ref','side-effects:none','network_policy_ref','egress:denied-by-capability');
  configuration := jsonb_build_object('backend','local','persistent_shell',false,'timeout_seconds',180,'resource_policy_ref',provider.manifest->>'resource_policy_ref','secret_forwarding','none');
  requirement_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(requirement),'sha256'),'hex');
  configuration_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(configuration),'sha256'),'hex');
  binding := jsonb_build_object('provider_ref','environment-provider:'||provider.provider_key||':v'||provider.provider_version,'provider_version',provider.provider_version,'provider_digest',provider.manifest_digest,'requirement_digest',requirement_digest,'configuration_digest',configuration_digest,'backend_kind',provider.backend_kind,'source_class',provider.source_class,'isolation_class',provider.manifest->>'isolation_class','capability_refs',provider.manifest->'capability_refs','conformance_ref',conformance.run_ref,'conformance_digest',conformance.run_digest);
  binding_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(binding),'sha256'),'hex');
  binding := binding||jsonb_build_object('binding_digest',binding_digest);
  insert into ops.work_request_execution_environment_binding(assignment_id,provider_id,conformance_id,requirement,requirement_digest,configuration,configuration_digest,binding,binding_digest)
  values(new.id,provider.id,conformance.id,requirement,requirement_digest,configuration,configuration_digest,binding,binding_digest);
  return new;
end $$;

create trigger work_request_execution_environment_bind
  after insert on ops.work_request_execution_assignment
  for each row execute function ops.bind_execution_environment_to_assignment();

insert into ops.work_request_execution_environment_binding(assignment_id,provider_id,conformance_id,requirement,requirement_digest,configuration,configuration_digest,binding,binding_digest)
select a.id,p.id,c.id,x.requirement,x.requirement_digest,x.configuration,x.configuration_digest,x.binding||jsonb_build_object('binding_digest',x.binding_digest),x.binding_digest
from ops.work_request_execution_assignment a
cross join lateral (select p.* from ops.execution_environment_provider p where p.provider_key='hermes-local' and ops.execution_environment_provider_current_state(p.id)='active' order by p.provider_version desc limit 1) p
cross join lateral (select c.* from ops.execution_environment_conformance c where c.provider_id=p.id order by c.observed_at desc,c.id desc limit 1) c
cross join lateral (select jsonb_build_object('authority_capability_ref','capability:metadata-only','required_operation_refs',jsonb_build_array('operation:health'),'selection_policy_ref','selection:server-active-provider-v1','side_effect_policy_ref','side-effects:none','network_policy_ref','egress:denied-by-capability') requirement,jsonb_build_object('backend','local','persistent_shell',false,'timeout_seconds',180,'resource_policy_ref',p.manifest->>'resource_policy_ref','secret_forwarding','none') configuration) q
cross join lateral (select 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(q.requirement),'sha256'),'hex') requirement_digest,'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(q.configuration),'sha256'),'hex') configuration_digest) d
cross join lateral (select q.requirement,d.requirement_digest,q.configuration,d.configuration_digest,jsonb_build_object('provider_ref','environment-provider:'||p.provider_key||':v'||p.provider_version,'provider_version',p.provider_version,'provider_digest',p.manifest_digest,'requirement_digest',d.requirement_digest,'configuration_digest',d.configuration_digest,'backend_kind',p.backend_kind,'source_class',p.source_class,'isolation_class',p.manifest->>'isolation_class','capability_refs',p.manifest->'capability_refs','conformance_ref',c.run_ref,'conformance_digest',c.run_digest) binding) b
cross join lateral (select b.*, 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(b.binding),'sha256'),'hex') binding_digest) x
where c.status='passed' and not exists(select 1 from ops.work_request_execution_environment_binding eb where eb.assignment_id=a.id);

create or replace function ops.bind_execution_environment_to_envelope()
returns trigger language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare route ops.work_request_execution_environment_binding%rowtype; provider ops.execution_environment_provider%rowtype; runtime jsonb; topology jsonb;
begin
  select eb.* into route from ops.work_request_execution_assignment a join ops.work_request_execution_environment_binding eb on eb.assignment_id=a.id where a.work_request_id=new.work_request_id;
  select * into provider from ops.execution_environment_provider where id=route.provider_id;
  if route.id is null or provider.id is null or ops.execution_environment_provider_current_state(provider.id)<>'active'
     or route.conformance_id is distinct from (select c.id from ops.execution_environment_conformance c where c.provider_id=provider.id order by c.observed_at desc,c.id desc limit 1)
     or not exists(select 1 from ops.execution_environment_conformance c where c.id=route.conformance_id and c.status='passed') then
    raise exception 'execution envelope requires an active conformance-passed environment provider binding';
  end if;
  runtime := (new.runtime_profile-'digest')||jsonb_build_object(
    'environment_provider_ref',route.binding->>'provider_ref','environment_provider_version',(route.binding->>'provider_version')::integer,
    'environment_provider_digest',route.binding->>'provider_digest','environment_requirement_digest',route.requirement_digest,
    'environment_configuration_digest',route.configuration_digest,'environment_backend_kind',route.binding->>'backend_kind',
    'environment_source_class',route.binding->>'source_class','environment_isolation_class',route.binding->>'isolation_class',
    'environment_capability_refs',route.binding->'capability_refs','environment_conformance_ref',route.binding->>'conformance_ref',
    'environment_conformance_digest',route.binding->>'conformance_digest','environment_binding_digest',route.binding_digest);
  new.runtime_profile := runtime||jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(runtime),'sha256'),'hex'));
  topology := (new.execution_topology-'digest')||jsonb_build_object('sandbox_ref',route.binding->>'provider_ref');
  new.execution_topology := topology||jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(topology),'sha256'),'hex'));
  new.configuration_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(new.runtime_profile||new.execution_topology||new.evaluation_plan),'sha256'),'hex');
  new.envelope := jsonb_set(jsonb_set(new.envelope,'{runtime_profile}',new.runtime_profile,true),'{execution_topology}',new.execution_topology,true);
  new.envelope := jsonb_set(new.envelope,'{server_binding,adapter,configuration_fingerprint}',to_jsonb(new.configuration_digest),true);
  new.envelope_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(new.envelope),'sha256'),'hex');
  return new;
end $$;

create trigger execution_envelope_environment_binding
  before insert on ops.execution_envelope_v1
  for each row execute function ops.bind_execution_environment_to_envelope();

-- The kill switch must govern both a new INSERT and replay of an already
-- issued immutable envelope. A BEFORE INSERT trigger alone cannot see the
-- replay path, so retain the 0303 issuer behind this state-checking door.
alter function ops.issue_execution_envelope_v1(text,text,uuid)
  rename to issue_execution_envelope_v1_without_environment_gate;

create or replace function ops.issue_execution_envelope_v1(
  p_work_request text,p_binding_id text,p_idempotency_key uuid
) returns table(envelope_id uuid,envelope_digest text,envelope jsonb,replayed boolean)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare tenant text := current_setting('carr.organization_tenant_id',true); provider_id uuid; provider_state text;
begin
  select eb.provider_id into provider_id
    from ops.context_activation_binding b
    join ops.work_request w on w.id=b.work_request_id
    join ops.work_request_execution_assignment a on a.work_request_id=w.id
    join ops.work_request_execution_environment_binding eb on eb.assignment_id=a.id
    join ops.execution_environment_conformance c on c.id=eb.conformance_id and c.status='passed'
   where b.binding_id=p_binding_id and b.organization_tenant_id=tenant
     and w.ref=p_work_request and w.organization_tenant_id=tenant
     and b.expires_at>now() and w.version=b.work_request_version
     and c.id=(select latest.id from ops.execution_environment_conformance latest where latest.provider_id=eb.provider_id order by latest.observed_at desc,latest.id desc limit 1);
  provider_state := ops.execution_environment_provider_current_state(provider_id);
  if provider_id is null or provider_state<>'active' then
    raise exception 'execution envelope requires an active conformance-passed environment provider binding';
  end if;
  return query select * from ops.issue_execution_envelope_v1_without_environment_gate(
    p_work_request,p_binding_id,p_idempotency_key);
end $$;

-- Extend the existing strict AttemptReceipt validator only by permitting the
-- two provider-evidence fields. All original binding, redaction, evaluator,
-- outcome, and server-derived closure checks remain byte-for-byte equivalent;
-- the provider-specific trigger below validates the new nested contract.
create or replace function ops.attempt_receipt_binding_valid()
returns trigger language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare binding ops.context_activation_binding%rowtype; envelope_row ops.execution_envelope_v1%rowtype; expected_unresolved jsonb; expected_closure_state text;
  disposition jsonb; reliability jsonb; expected_reliability_state text; expected_reliability_reasons jsonb; required_evaluator_kinds text[]; held_out_count integer;
begin
  select * into binding from ops.context_activation_binding where id=new.activation_binding_id;
  select * into envelope_row from ops.execution_envelope_v1 where id=new.execution_envelope_id;
  if not found or binding.organization_tenant_id is distinct from new.organization_tenant_id
     or binding.work_request_id is distinct from new.work_request_id
     or binding.plan_hash is distinct from new.plan_hash
     or binding.expires_at < now()
     or exists (select 1 from ops.work_request current_work where current_work.id=binding.work_request_id and (current_work.version<>binding.work_request_version or current_work.organization_tenant_id<>binding.organization_tenant_id))
     or envelope_row.organization_tenant_id is distinct from new.organization_tenant_id
     or envelope_row.work_request_id is distinct from new.work_request_id
     or envelope_row.plan_hash is distinct from new.plan_hash
     or envelope_row.activation_binding_id is distinct from binding.id
     or envelope_row.envelope_digest is distinct from new.envelope_digest
     or envelope_row.expires_at < now()
     or jsonb_typeof(new.receipt) is distinct from 'object'::text
     or new.receipt->>'schema_version' <> 'attempt-receipt.v1'
     or new.receipt->>'envelope_digest' is distinct from new.envelope_digest
     or new.receipt->>'attempt_id' is distinct from new.attempt_id
     or not (new.receipt ?& array['schema_version','attempt_id','envelope_digest','attempt_ordinal','adapter','lifecycle','result','attestation','negative_knowledge','telemetry','tool_event_summaries','observation','interventions','handoff_proposal','visual_artifacts','evaluation_binding'])
     or exists (select 1 from jsonb_object_keys(new.receipt) key where key <> all(array['schema_version','attempt_id','envelope_digest','attempt_ordinal','adapter','lifecycle','result','attestation','negative_knowledge','telemetry','tool_event_summaries','observation','interventions','handoff_proposal','visual_artifacts','evaluation_binding','knowledge_activation','reliability']))
     or ops.attempt_receipt_contains_raw_content(new.receipt)
     or not (new.receipt ?& array['knowledge_activation','reliability'])
     or coalesce(new.receipt->'knowledge_activation'->'closure'->>'derived_by','') <> 'server' then
    raise exception 'attempt receipt is cross-bound, malformed, or contains raw content';
  end if;
  -- A `derived_by: server` label is not proof.  Recompute the required-item
  -- closure from the immutable activation rows and reject any forged or stale
  -- nested receipt before it is admitted.
  if jsonb_typeof(new.receipt->'knowledge_activation') <> 'object'
     or not (new.receipt->'knowledge_activation' ?& array['bundle_digest','item_dispositions','closure','mode','canonical_binding'])
     or exists (select 1 from jsonb_object_keys(new.receipt->'knowledge_activation') key where key <> all(array['bundle_digest','item_dispositions','closure','mode','canonical_binding']))
     or jsonb_typeof(new.receipt->'knowledge_activation'->'canonical_binding') <> 'object'
     or not (new.receipt->'knowledge_activation'->'canonical_binding' ?& array['work_request_id','work_request_version','accepted_plan_digest','envelope_digest','activation_binding_ref'])
     or exists (select 1 from jsonb_object_keys(new.receipt->'knowledge_activation'->'canonical_binding') key where key <> all(array['work_request_id','work_request_version','accepted_plan_digest','envelope_digest','activation_binding_ref']))
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'work_request_id' <> (select ref from ops.work_request where id=binding.work_request_id)
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'work_request_version' <> binding.work_request_version::text
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'accepted_plan_digest' <> binding.plan_hash
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'envelope_digest' <> new.envelope_digest
     or new.receipt->'knowledge_activation'->'canonical_binding'->>'activation_binding_ref' <> binding.binding_id
     or new.receipt->'knowledge_activation'->>'bundle_digest' <> binding.bundle_digest
     or jsonb_typeof(new.receipt->'knowledge_activation'->'item_dispositions') <> 'array'
     or (select count(*) from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions'))
          <> (select count(*) from ops.context_activation_item where binding_id=binding.id)
     or exists (
       select 1 from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions') d
        where jsonb_typeof(d) <> 'object'
           or not (d ?& array['item_ref','disposition','evidence_refs','reason_ref'])
           or exists (select 1 from jsonb_object_keys(d) key where key <> all(array['item_ref','disposition','evidence_refs','reason_ref','stage_ref','tool_ref']))
           or d->>'disposition' not in ('applied','not_applicable','conflicted','stale','missing')
           or jsonb_typeof(d->'evidence_refs') <> 'array'
           or coalesce(d->>'reason_ref','') !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
           or (d->>'disposition'='applied' and (jsonb_array_length(d->'evidence_refs')=0 or not (d ?| array['stage_ref','tool_ref'])))
     )
     or exists (
       select 1 from ops.context_activation_item i
       left join lateral (
         select d from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions') d
          where d->>'item_ref'=i.canonical_ref limit 1
       ) supplied on true
       where i.binding_id=binding.id and supplied.d is null
          or (i.binding_id=binding.id and i.required and coalesce(supplied.d->>'disposition','missing') <> 'applied'
              and (jsonb_typeof(supplied.d->'evidence_refs') <> 'array' or jsonb_array_length(supplied.d->'evidence_refs')=0))
     ) then
    raise exception 'attempt receipt knowledge activation is not exactly bound';
  end if;
  select coalesce(jsonb_agg(i.canonical_ref order by i.canonical_ref),'[]'::jsonb)
    into expected_unresolved
    from ops.context_activation_item i
    left join lateral (
      select d from jsonb_array_elements(new.receipt->'knowledge_activation'->'item_dispositions') d
       where d->>'item_ref'=i.canonical_ref limit 1
    ) disposition on true
   where i.binding_id=binding.id and i.required
     and coalesce(disposition.d->>'disposition','missing') <> 'applied';
  expected_closure_state := case when binding.mode='shadow' then 'not_activated'
    when expected_unresolved='[]'::jsonb then 'closed' else 'blocked' end;
  if new.receipt->'knowledge_activation'->'closure'->'unresolved_required_item_refs' is distinct from expected_unresolved
     or new.receipt->'knowledge_activation'->'closure'->>'state' is distinct from expected_closure_state then
    raise exception 'attempt receipt knowledge closure was not server-derived';
  end if;
  reliability := new.receipt->'reliability';
  if jsonb_typeof(reliability) <> 'object'
     or not (reliability ?& array['route_digest','topology_digest','evaluation_plan_digest','grounding_sufficiency','deterministic_checks','model_judgement','human_acceptance','trajectory','evaluator_results','corrections','defects','incidents','downstream_outcome','outcome_horizon','process_metrics','eval_candidates','shadow_comparisons','learning_disposition','telemetry','closure'])
     or exists (select 1 from jsonb_object_keys(reliability) key where key <> all(array['route_digest','topology_digest','evaluation_plan_digest','grounding_sufficiency','deterministic_checks','model_judgement','human_acceptance','trajectory','evaluator_results','corrections','defects','incidents','downstream_outcome','outcome_horizon','process_metrics','eval_candidates','shadow_comparisons','learning_disposition','telemetry','closure','environment_binding_digest','environment_evidence']))
     or jsonb_typeof(reliability->'deterministic_checks') <> 'array'
     or exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where jsonb_typeof(check_row) <> 'object' or not (check_row ?& array['check_id','state','critical','evidence_refs']) or check_row->>'check_id' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or check_row->>'state' not in ('passed','failed','unknown','not_run') or jsonb_typeof(check_row->'critical')<>'boolean' or jsonb_typeof(check_row->'evidence_refs')<>'array')
     or jsonb_typeof(reliability->'trajectory') <> 'array'
     or exists (select 1 from jsonb_array_elements(reliability->'trajectory') trace_row where jsonb_typeof(trace_row) <> 'object' or not (trace_row ?& array['sequence','stage_ref','parent_event_ref','decision_class','tool_class','result_state','fallback_state','guardrail_state','latency_ms','evidence_refs']) or exists (select 1 from jsonb_object_keys(trace_row) key where key <> all(array['sequence','stage_ref','parent_event_ref','decision_class','tool_class','result_state','fallback_state','guardrail_state','latency_ms','evidence_refs'])))
     or jsonb_typeof(reliability->'evaluator_results') <> 'array' or jsonb_array_length(reliability->'evaluator_results')=0
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where jsonb_typeof(evaluator) <> 'object' or not (evaluator ?& array['kind','evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','status','confidence','critical','independence_state','held_out_case_count','check_refs','dimension_refs','evidence_refs','judge_provenance','calibration_evidence_refs']) or exists (select 1 from jsonb_object_keys(evaluator) key where key <> all(array['kind','evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','status','confidence','critical','independence_state','held_out_case_count','check_refs','dimension_refs','evidence_refs','judge_provenance','calibration_evidence_refs'])) or evaluator->>'kind' not in ('deterministic','judge','human_acceptance') or evaluator->>'status' not in ('passed','failed','blocked','unknown','not_run') or evaluator->>'independence_state' not in ('not_independent','unknown') or jsonb_typeof(evaluator->'held_out_case_count')<>'number' or (evaluator->>'held_out_case_count')::integer<0 or jsonb_typeof(evaluator->'check_refs')<>'array' or jsonb_typeof(evaluator->'dimension_refs')<>'array')
     or jsonb_typeof(reliability->'grounding_sufficiency') <> 'object' or not (reliability->'grounding_sufficiency' ?& array['state','evidence_refs','required_supplied','required_used','required_missing','advisory_supplied','advisory_used','freshness_failures','retrieval_failures'])
     -- Learning facts are canonical, redacted metadata.  The executor cannot
     -- smuggle a candidate, free-text case body, or a fact into the wrong lane.
     or jsonb_typeof(reliability->'eval_candidates') <> 'array' or jsonb_array_length(reliability->'eval_candidates') <> 0
     -- Executor receipts cannot carry a shadow route or its side-effect
     -- claims. Governed shadows are admitted only by Policy Learning.
     or jsonb_typeof(reliability->'shadow_comparisons') <> 'array' or jsonb_array_length(reliability->'shadow_comparisons') <> 0
     or exists (select 1 from jsonb_array_elements(reliability->'corrections') event_row where jsonb_typeof(event_row)<>'object' or not (event_row ?& array['event_ref','kind','evidence_refs','summary']) or exists(select 1 from jsonb_object_keys(event_row) key where key<>all(array['event_ref','kind','evidence_refs','summary'])) or event_row->>'kind'<>'correction' or event_row->>'event_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or event_row->>'summary' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or jsonb_typeof(event_row->'evidence_refs')<>'array' or jsonb_array_length(event_row->'evidence_refs')=0)
     or exists (select 1 from jsonb_array_elements(reliability->'defects') event_row where jsonb_typeof(event_row)<>'object' or not (event_row ?& array['event_ref','kind','evidence_refs','summary']) or exists(select 1 from jsonb_object_keys(event_row) key where key<>all(array['event_ref','kind','evidence_refs','summary'])) or event_row->>'kind'<>'defect' or event_row->>'event_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or event_row->>'summary' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or jsonb_typeof(event_row->'evidence_refs')<>'array' or jsonb_array_length(event_row->'evidence_refs')=0)
     or exists (select 1 from jsonb_array_elements(reliability->'incidents') event_row where jsonb_typeof(event_row)<>'object' or not (event_row ?& array['event_ref','kind','evidence_refs','summary']) or exists(select 1 from jsonb_object_keys(event_row) key where key<>all(array['event_ref','kind','evidence_refs','summary'])) or event_row->>'kind'<>'incident' or event_row->>'event_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or event_row->>'summary' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or jsonb_typeof(event_row->'evidence_refs')<>'array' or jsonb_array_length(event_row->'evidence_refs')=0)
     or jsonb_typeof(reliability->'human_acceptance')<>'object' or not (reliability->'human_acceptance' ?& array['state','actor_ref','evidence_refs','outcome_feedback_ref','outcome_feedback_hash'])
     or reliability->'human_acceptance'->>'state' not in ('accepted','rejected','absent','unknown')
     or (reliability->'human_acceptance'->>'state'='accepted' and (reliability->'human_acceptance'->>'outcome_feedback_ref' is null or reliability->'human_acceptance'->>'outcome_feedback_hash' !~ '^sha256:[0-9a-f]{64}$'))
     or (reliability->'human_acceptance'->>'state'<>'accepted' and ((reliability->'human_acceptance'->'outcome_feedback_ref')<>'null'::jsonb or (reliability->'human_acceptance'->'outcome_feedback_hash')<>'null'::jsonb))
     or jsonb_typeof(reliability->'downstream_outcome')<>'object' or not (reliability->'downstream_outcome' ?& array['state','brokerage_ref','evidence_refs','outcome_feedback_ref','outcome_feedback_hash'])
     or reliability->'downstream_outcome'->>'state' not in ('observed','not_observed','unknown')
     or (reliability->'downstream_outcome'->>'state'='observed' and (reliability->'downstream_outcome'->>'outcome_feedback_ref' is null or reliability->'downstream_outcome'->>'outcome_feedback_hash' !~ '^sha256:[0-9a-f]{64}$'))
     or (reliability->'downstream_outcome'->>'state'<>'observed' and ((reliability->'downstream_outcome'->'outcome_feedback_ref')<>'null'::jsonb or (reliability->'downstream_outcome'->'outcome_feedback_hash')<>'null'::jsonb))
     or jsonb_typeof(reliability->'outcome_horizon') <> 'object' or not (reliability->'outcome_horizon' ?& array['state','ends_at','as_of','evidence_refs'])
     or jsonb_typeof(reliability->'process_metrics') <> 'object' or not (reliability->'process_metrics' ?& array['latency_ms','cost_usd','input_tokens','output_tokens','cached_input_tokens','retry_count','recovery_count','context_reconstruction_ms','human_intervention_count','security_event_refs'])
     or jsonb_typeof(reliability->'closure') <> 'object' or not (reliability->'closure' ?& array['state','reasons','derived_by']) or reliability->'closure'->>'derived_by'<>'server'
     or jsonb_typeof(reliability->'telemetry') <> 'array' or jsonb_array_length(reliability->'telemetry') <> 0 then
    raise exception 'attempt receipt reliability extension is malformed';
  end if;
  if reliability ? 'route_digest' and reliability->>'route_digest' <> envelope_row.runtime_profile->>'digest'
     or reliability ? 'topology_digest' and reliability->>'topology_digest' <> envelope_row.execution_topology->>'digest'
     or reliability ? 'evaluation_plan_digest' and reliability->>'evaluation_plan_digest' <> envelope_row.evaluation_plan->>'digest' then
    raise exception 'attempt receipt route/topology/evaluation digests do not bind the server-issued envelope';
  end if;
  if (select coalesce(jsonb_agg(check_row->>'check_id' order by check_row->>'check_id'),'[]'::jsonb) from jsonb_array_elements(reliability->'deterministic_checks') check_row)
       is distinct from (select coalesce(jsonb_agg(value order by value),'[]'::jsonb) from jsonb_array_elements_text(envelope_row.evaluation_plan->'required_deterministic_check_refs') value)
     or exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where (check_row->>'critical')::boolean is not true)
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'kind'='deterministic' and (jsonb_array_length(evaluator->'check_refs')<>1 or evaluator->'dimension_refs' is distinct from envelope_row.evaluation_plan->'critical_dimensions'))
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'kind'<>'deterministic' and (jsonb_array_length(evaluator->'check_refs')<>0 or evaluator->'dimension_refs' is distinct from envelope_row.evaluation_plan->'critical_dimensions')) then
    raise exception 'evaluation evidence does not exactly bind server-required checks and critical dimensions';
  end if;
  -- Executor-submitted actor/status labels are never acceptance evidence.  A
  -- claimed human acceptance/outcome must resolve to the existing immutable,
  -- human-authority Program 6 feedback receipt for this exact Work Request and
  -- accepted plan; absent/unknown feedback remains visibly unavailable.
  if reliability->'human_acceptance'->>'state'='accepted' and not exists (
    select 1 from ops.sourced_work_request_outcome_feedback f
      join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
      join public.actor a on a.id=fr.accepted_by_actor_id
     where f.work_request_id=new.work_request_id and f.plan_id=binding.plan_id
       and f.feedback_ref=reliability->'human_acceptance'->>'outcome_feedback_ref'
       and f.feedback_hash=reliability->'human_acceptance'->>'outcome_feedback_hash'
       and ('actor:'||a.slug)=reliability->'human_acceptance'->>'actor_ref'
  ) then raise exception 'attempt receipt human acceptance is not bound to accepted Work Request outcome feedback'; end if;
  if reliability->'downstream_outcome'->>'state'='observed' and (
    reliability->'downstream_outcome'->>'outcome_feedback_ref' is distinct from reliability->'human_acceptance'->>'outcome_feedback_ref'
    or reliability->'downstream_outcome'->>'outcome_feedback_hash' is distinct from reliability->'human_acceptance'->>'outcome_feedback_hash'
    or not exists (
      select 1 from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
       where f.work_request_id=new.work_request_id and f.plan_id=binding.plan_id
         and f.feedback_ref=reliability->'downstream_outcome'->>'outcome_feedback_ref'
         and f.feedback_hash=reliability->'downstream_outcome'->>'outcome_feedback_hash'
    )
  ) then raise exception 'attempt receipt observed outcome is not bound to accepted Work Request feedback'; end if;
  select coalesce(array_agg(value),array[]::text[]) into required_evaluator_kinds from jsonb_array_elements_text(envelope_row.evaluation_plan->'requirements'->'required_evaluator_kinds');
  select coalesce(sum((evaluator->>'held_out_case_count')::integer),0) into held_out_count from jsonb_array_elements(reliability->'evaluator_results') evaluator;
  if exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where (check_row->>'critical')::boolean and check_row->>'state'='failed')
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where (evaluator->>'critical')::boolean and evaluator->>'status' in ('failed','blocked')) then
    expected_reliability_state := 'blocked';
  elsif reliability->'grounding_sufficiency'->>'state'<>'sufficient'
     or coalesce((envelope_row.evaluation_plan->'requirements'->>'outcome_horizon_required')::boolean,false) and reliability->'outcome_horizon'->>'state'<>'mature'
     or reliability->'model_judgement'->>'state'<>'pass'
     or exists (select 1 from jsonb_array_elements(reliability->'deterministic_checks') check_row where (check_row->>'critical')::boolean and check_row->>'state' in ('unknown','not_run'))
     or exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where (evaluator->>'critical')::boolean and evaluator->>'status' in ('unknown','not_run'))
     or exists (select 1 from unnest(required_evaluator_kinds) kind where not exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'kind'=kind))
     or held_out_count < coalesce((envelope_row.evaluation_plan->'requirements'->>'minimum_held_out_case_count')::integer,0)
     or coalesce((envelope_row.evaluation_plan->'requirements'->>'independent_review_required')::boolean,false) and not exists (select 1 from jsonb_array_elements(reliability->'evaluator_results') evaluator where evaluator->>'independence_state'='independent')
     or coalesce((envelope_row.evaluation_plan->'requirements'->>'human_acceptance_required')::boolean,false) and reliability->'human_acceptance'->>'state'<>'accepted' then
    expected_reliability_state := 'insufficient_evidence';
  -- AttemptReceipt is executor evidence, never the authoritative evaluator
  -- event.  It cannot promote its own judge/held-out/calibration claims.
  else expected_reliability_state := 'insufficient_evidence';
  end if;
  if reliability->'closure'->>'state' is distinct from expected_reliability_state then
    raise exception 'attempt receipt reliability closure was not server-derived for attempt %, expected %, got %',new.attempt_id,expected_reliability_state,reliability->'closure'->>'state';
  end if;
  expected_reliability_reasons := case when expected_reliability_state='blocked' then
    jsonb_build_array('reason:authority_evaluation_evidence_missing','reason:critical_deterministic_or_evaluator_failure')
  else jsonb_build_array('reason:authority_evaluation_evidence_missing') end;
  if reliability->'closure'->'reasons' is distinct from expected_reliability_reasons then
    raise exception 'attempt receipt reliability closure reasons were not server-derived';
  end if;
  return new;
end $$;


create or replace function ops.validate_attempt_environment_evidence()
returns trigger language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare runtime jsonb; evidence jsonb; allowed text[] := array['binding_digest','session_ref','lease_state','operation_count','policy_refusal_refs','security_event_refs','cleanup_state','cleanup_evidence_refs','side_effect_state','resource_usage','evidence_refs'];
begin
  select e.runtime_profile into runtime from ops.execution_envelope_v1 e where e.id=new.execution_envelope_id;
  if runtime ? 'environment_binding_digest' then
    evidence := new.receipt->'reliability'->'environment_evidence';
    if new.receipt->'reliability'->>'environment_binding_digest' is distinct from runtime->>'environment_binding_digest'
       or jsonb_typeof(evidence)<>'object' or not (evidence ?& allowed)
       or exists(select 1 from jsonb_object_keys(evidence) k where k<>all(allowed))
       or evidence->>'binding_digest' is distinct from runtime->>'environment_binding_digest'
       or evidence->>'session_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
       or evidence->>'lease_state' not in ('active','released','expired','failed','unknown')
       or jsonb_typeof(evidence->'operation_count')<>'number' or evidence->>'operation_count' !~ '^[0-9]+$'
       or evidence->>'cleanup_state' not in ('not_required','pending','verified','failed','unknown')
       or evidence->>'side_effect_state' not in ('none','attempted','refused','observed','unknown')
       or exists(select 1 from unnest(array['policy_refusal_refs','security_event_refs','cleanup_evidence_refs','evidence_refs']) field
         where jsonb_typeof(evidence->field)<>'array' or exists(select 1 from jsonb_array_elements_text(evidence->field) value where value !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'))
       or jsonb_typeof(evidence->'evidence_refs')<>'array' or jsonb_array_length(evidence->'evidence_refs')=0
       or (evidence->>'cleanup_state' in ('verified','failed') and (jsonb_typeof(evidence->'cleanup_evidence_refs')<>'array' or jsonb_array_length(evidence->'cleanup_evidence_refs')=0))
       or jsonb_typeof(evidence->'resource_usage')<>'object'
       or not (evidence->'resource_usage' ?& array['cpu_ms','memory_peak_mb','disk_peak_mb','network_egress_bytes'])
       or exists(select 1 from jsonb_object_keys(evidence->'resource_usage') key where key<>all(array['cpu_ms','memory_peak_mb','disk_peak_mb','network_egress_bytes']))
       or exists(select 1 from jsonb_each(evidence->'resource_usage') metric where jsonb_typeof(metric.value)<>'number' or metric.value#>>'{}' !~ '^[0-9]+$') then
      raise exception 'AttemptReceipt environment evidence does not bind the issued provider and cleanup contract';
    end if;
  elsif new.receipt->'reliability' ?| array['environment_binding_digest','environment_evidence'] then
    raise exception 'legacy ExecutionEnvelope cannot accept execution environment evidence';
  end if;
  return new;
end $$;

create trigger attempt_receipt_environment_evidence
  before insert on ops.attempt_receipt
  for each row execute function ops.validate_attempt_environment_evidence();

-- Preserve the 0304 identity/plan/tenant verifier as the first rung, then add
-- the exact provider assignment.  This wrapper remains a read-only admission
-- projection; it never chooses a provider or grants a capability.
alter function ops.hermes_runtime_admission_for_brief(text,text,text,text,text)
  rename to hermes_runtime_admission_for_brief_v1;

create or replace function ops.hermes_runtime_admission_for_brief(
  p_runtime_slug text,
  p_profile_key text,
  p_sponsor_slug text,
  p_work_request text,
  p_binding_id text
) returns jsonb
language plpgsql stable security definer
set search_path=pg_catalog,ops,public,pg_temp
as $$
declare
  base jsonb;
  tenant text := current_setting('carr.organization_tenant_id',true);
  runtime jsonb;
  route ops.work_request_execution_environment_binding%rowtype;
  provider ops.execution_environment_provider%rowtype;
  conformance ops.execution_environment_conformance%rowtype;
begin
  base := ops.hermes_runtime_admission_for_brief_v1(
    p_runtime_slug,p_profile_key,p_sponsor_slug,p_work_request,p_binding_id);
  if coalesce((base->>'authorized')::boolean,false) is not true then return base; end if;

  select e.runtime_profile
    into runtime
    from ops.execution_envelope_v1 e
    join ops.context_activation_binding b on b.id=e.activation_binding_id
    join ops.work_request w on w.id=b.work_request_id
   where e.organization_tenant_id=tenant and b.organization_tenant_id=tenant
     and w.organization_tenant_id=tenant and w.ref=p_work_request
     and b.binding_id=p_binding_id and e.activation_binding_id=b.id
     and b.expires_at>now() and e.expires_at>now() and w.version=b.work_request_version;
  if not found then
    return jsonb_build_object('status','stale','authorized',false,'reason','execution_environment_binding_not_exact');
  end if;
  select eb.* into route
    from ops.context_activation_binding b
    join ops.work_request_execution_assignment a on a.work_request_id=b.work_request_id
    join ops.work_request_execution_environment_binding eb on eb.assignment_id=a.id
   where b.binding_id=p_binding_id and b.organization_tenant_id=tenant;
  select * into provider from ops.execution_environment_provider where id=route.provider_id;
  select * into conformance from ops.execution_environment_conformance where id=route.conformance_id;
  if route.id is null or provider.id is null or conformance.id is null
     or ops.execution_environment_provider_current_state(provider.id)<>'active' or conformance.status<>'passed'
     or conformance.id is distinct from (select c.id from ops.execution_environment_conformance c where c.provider_id=provider.id order by c.observed_at desc,c.id desc limit 1)
     or runtime->>'environment_provider_ref' is distinct from route.binding->>'provider_ref'
     or runtime->>'environment_provider_version' is distinct from route.binding->>'provider_version'
     or runtime->>'environment_provider_digest' is distinct from provider.manifest_digest
     or runtime->>'environment_requirement_digest' is distinct from route.requirement_digest
     or runtime->>'environment_configuration_digest' is distinct from route.configuration_digest
     or runtime->>'environment_backend_kind' is distinct from provider.backend_kind
     or runtime->>'environment_source_class' is distinct from provider.source_class
     or runtime->>'environment_isolation_class' is distinct from provider.manifest->>'isolation_class'
     or runtime->'environment_capability_refs' is distinct from provider.manifest->'capability_refs'
     or runtime->>'environment_conformance_ref' is distinct from conformance.run_ref
     or runtime->>'environment_conformance_digest' is distinct from conformance.run_digest
     or runtime->>'environment_binding_digest' is distinct from route.binding_digest then
    return jsonb_build_object('status','stale','authorized',false,'reason','execution_environment_binding_not_exact');
  end if;

  return base||jsonb_build_object(
    'environment_provider_ref',runtime->>'environment_provider_ref',
    'environment_provider_version',(runtime->>'environment_provider_version')::integer,
    'environment_provider_digest',runtime->>'environment_provider_digest',
    'environment_requirement_digest',runtime->>'environment_requirement_digest',
    'environment_configuration_digest',runtime->>'environment_configuration_digest',
    'environment_backend_kind',runtime->>'environment_backend_kind',
    'environment_source_class',runtime->>'environment_source_class',
    'environment_isolation_class',runtime->>'environment_isolation_class',
    'environment_capability_refs',runtime->'environment_capability_refs',
    'environment_conformance_ref',runtime->>'environment_conformance_ref',
    'environment_conformance_digest',runtime->>'environment_conformance_digest',
    'environment_binding_digest',runtime->>'environment_binding_digest',
    'execution_environment_operator_surface','job-passport:route-and-agent-topology',
    'execution_environment_telemetry_ref','observatory:execution-environment:'||provider.provider_key);
end $$;

create or replace function ops.read_execution_environment_providers()
returns jsonb language sql stable security definer set search_path=ops,public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'provider_ref','environment-provider:'||p.provider_key||':v'||p.provider_version,
    'display_name',p.manifest->>'display_name','source_class',p.source_class,'backend_kind',p.backend_kind,
    'manifest_digest',p.manifest_digest,'state',ops.execution_environment_provider_current_state(p.id),
    'capability_refs',p.manifest->'capability_refs','isolation_class',p.manifest->>'isolation_class',
    'conformance',case when c.id is null then jsonb_build_object('state','unavailable') else jsonb_build_object(
      'state',c.status,'run_ref',c.run_ref,'run_digest',c.run_digest,'observed_at',c.observed_at,
      'manifest_digest',c.manifest_digest,'implementation_digest',c.implementation_digest,
      'package_digest',c.package_digest,'configuration_schema_digest',c.configuration_schema_digest) end,
    'evidence_register',jsonb_build_object(
      'source_ref',p.manifest->>'implementation_ref','source_digest',p.manifest->>'implementation_digest',
      'consumer_ref','consumer:hermes-bot-brief+execution-envelope','trigger_ref','trigger:work-request-execution-assignment',
      'admission_ref','admission:active+conformance-passed','retrieval_ref','tool:read-execution-environment-providers',
      'enforcement_ref','enforcement:assignment+envelope+receipt-db-triggers',
      'operator_surface','job-passport:route-and-agent-topology','telemetry_ref','observatory:execution-environment:'||p.provider_key,
      'canary',case when c.id is null then jsonb_build_object('state','unavailable') else jsonb_build_object('state',c.status,'evidence_refs',c.evidence_refs) end,
      'rollback_ref','rollback:human-active-to-disabled','freshness',case when c.id is null then jsonb_build_object('state','unavailable') else jsonb_build_object('state','observed','observed_at',c.observed_at) end),
    'operator_surface','job-passport:route-and-agent-topology','telemetry_ref','observatory:execution-environment:'||p.provider_key,
    'rollback','active-to-disabled-human-only','grants_authority',false) order by p.provider_key,p.provider_version),'[]'::jsonb)
  from ops.execution_environment_provider p
  left join lateral (select c.* from ops.execution_environment_conformance c where c.provider_id=p.id order by c.observed_at desc,c.id desc limit 1) c on true
$$;

revoke all on ops.execution_environment_provider,ops.execution_environment_provider_event,ops.execution_environment_conformance,ops.work_request_execution_environment_binding from public,carr_reader,carr_writer,carr_jobs;
grant select on ops.execution_environment_provider,ops.execution_environment_provider_event,ops.execution_environment_conformance,ops.work_request_execution_environment_binding to carr_authority;
grant execute on function ops.register_execution_environment_provider(jsonb,uuid) to carr_authority;
grant execute on function ops.attest_execution_environment_conformance(text,jsonb,uuid) to carr_authority;
grant execute on function ops.transition_execution_environment_provider(text,text,text,jsonb,uuid) to carr_authority;
grant execute on function ops.read_execution_environment_providers() to carr_reader,carr_writer,carr_authority;
grant execute on function ops.hermes_runtime_admission_for_brief(text,text,text,text,text) to carr_reader,carr_writer;
revoke all on function ops.hermes_runtime_admission_for_brief_v1(text,text,text,text,text) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.issue_execution_envelope_v1(text,text,uuid) to carr_writer;
revoke all on function ops.issue_execution_envelope_v1_without_environment_gate(text,text,uuid) from public,carr_reader,carr_writer,carr_jobs;
revoke all on function ops.register_execution_environment_provider(jsonb,uuid),ops.attest_execution_environment_conformance(text,jsonb,uuid),ops.transition_execution_environment_provider(text,text,text,jsonb,uuid),ops.read_execution_environment_providers() from public;

commit;
