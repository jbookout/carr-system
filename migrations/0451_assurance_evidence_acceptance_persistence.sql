-- 0451_assurance_evidence_acceptance_persistence.sql
-- doctrine: carr-production-maturity-baseline
--
-- A3a is persistence only.  It extends the immutable Engineering Passport
-- receipt/reviewer lineage with assurance evidence and owner acceptance; it
-- does not grant execution, install a runtime, or make the A1a compiler's
-- deliberately non-authorizing manifest authoritative.

begin;

create table ops.assurance_execution_manifest (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (btrim(organization_tenant_id)<>''),
  work_request_id uuid not null references ops.work_request(id) on delete restrict,
  accepted_plan_id uuid not null references ops.sourced_work_request_plan(id) on delete restrict,
  slice_plan_id uuid not null references ops.engineering_slice_plan(id) on delete restrict,
  slice_ref text not null check (slice_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  lease_id uuid not null references ops.canonical_ownership_lease(id) on delete restrict,
  fencing_generation bigint not null check (fencing_generation>0),
  repository_stage text not null check (repository_stage in
    ('write','run_check','post_commit','push','pull_request','review','merge')),
  repository_commit_sha text not null check (repository_commit_sha ~ '^[0-9a-f]{40}$'),
  repository_tree_sha text not null check (repository_tree_sha ~ '^[0-9a-f]{40}$'),
  compiler_id text not null check (compiler_id='carr-assurance-slice-compiler'),
  compiler_version text not null check (btrim(compiler_version)<>''),
  input_digest text not null check (input_digest ~ '^sha256:[0-9a-f]{64}$'),
  manifest_hash text not null unique check (manifest_hash ~ '^sha256:[0-9a-f]{64}$'),
  applicable_rule_snapshot_digest text not null check
    (applicable_rule_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'),
  coordination_snapshot_digest text not null check
    (coordination_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'),
  snapshot_valid_until timestamptz not null,
  applicable_rules jsonb not null check (jsonb_typeof(applicable_rules)='object'),
  coordination_snapshot jsonb not null check (jsonb_typeof(coordination_snapshot)='object'),
  compiler_input jsonb not null check (jsonb_typeof(compiler_input)='object'),
  ownership_contract_digest text not null check
    (ownership_contract_digest ~ '^sha256:[0-9a-f]{64}$'),
  manifest jsonb not null check (jsonb_typeof(manifest)='object'),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp(),
  foreign key (organization_tenant_id,lease_id)
    references ops.canonical_ownership_lease(organization_tenant_id,id) on delete restrict
);

create index assurance_manifest_slice_stage_idx
  on ops.assurance_execution_manifest(slice_plan_id,slice_ref,repository_stage,created_at desc);

create table ops.assurance_evidence_extension (
  id uuid primary key default gen_random_uuid(),
  receipt_id uuid not null unique references ops.engineering_slice_receipt(id) on delete restrict,
  manifest_id uuid not null unique references ops.assurance_execution_manifest(id) on delete restrict,
  evidence_digest text not null unique check (evidence_digest ~ '^sha256:[0-9a-f]{64}$'),
  evidence jsonb not null check (jsonb_typeof(evidence)='object'),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp()
);

create table ops.assurance_review_extension (
  id uuid primary key default gen_random_uuid(),
  reviewer_fact_id uuid not null unique references ops.engineering_reviewer_fact(id) on delete restrict,
  review_manifest_id uuid not null references ops.assurance_execution_manifest(id) on delete restrict,
  evidence_id uuid not null unique references ops.assurance_evidence_extension(id) on delete restrict,
  review_digest text not null unique check (review_digest ~ '^sha256:[0-9a-f]{64}$'),
  review jsonb not null check (jsonb_typeof(review)='object'),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp()
);

create table ops.assurance_owner_acceptance_fact (
  id uuid primary key default gen_random_uuid(),
  organization_tenant_id text not null check (btrim(organization_tenant_id)<>''),
  review_manifest_id uuid not null references ops.assurance_execution_manifest(id) on delete restrict,
  evidence_id uuid not null references ops.assurance_evidence_extension(id) on delete restrict,
  review_id uuid not null references ops.assurance_review_extension(id) on delete restrict,
  owner_actor_id uuid not null references public.actor(id) on delete restrict,
  owner_actor_slug text not null check (owner_actor_slug in ('joe','dell')),
  owner_session_ref text not null check (owner_session_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  owner_host_ref text not null check (owner_host_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'),
  decision text not null check (decision in ('accept','hold','reject')),
  acceptance_digest text not null unique check (acceptance_digest ~ '^sha256:[0-9a-f]{64}$'),
  acceptance jsonb not null check (jsonb_typeof(acceptance)='object'),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default clock_timestamp()
);

comment on table ops.assurance_execution_manifest is
  'Immutable A1a manifest persistence plus normalized rule/coordination preimages. Rows remain compiled_not_authorized and grant no action.';
comment on table ops.assurance_evidence_extension is
  'One-to-one immutable post-commit evidence extension of the canonical Engineering slice receipt; the receipt is never duplicated or rewritten.';
comment on table ops.assurance_review_extension is
  'One-to-one assurance binding over the existing independently guarded Engineering reviewer fact; it is never owner acceptance.';
comment on table ops.assurance_owner_acceptance_fact is
  'Append-only Joe/Dell accept/hold/reject history. owner acceptance is structurally distinct from and cannot satisfy independent review.';

create or replace function ops.assurance_exact_object(p_value jsonb,p_keys text[])
returns boolean language sql immutable strict set search_path=pg_catalog as $$
  select jsonb_typeof(p_value)='object'
     and (select coalesce(array_agg(k order by k),'{}'::text[]) from jsonb_object_keys(p_value) k)
         = (select coalesce(array_agg(k order by k),'{}'::text[]) from unnest(p_keys) k)
$$;

create or replace function ops.assurance_digest(p_value jsonb)
returns text language sql immutable strict set search_path=pg_catalog,ops,public as $$
  select 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_value),'sha256'),'hex')
$$;

create or replace function ops.assurance_identifier_valid(p_value text)
returns boolean language sql immutable set search_path=pg_catalog as $$
  select coalesce(p_value ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
$$;

create or replace function ops.assurance_timestamp_valid(p_value text)
returns boolean language plpgsql immutable set search_path=pg_catalog as $$
declare parsed timestamptz;
begin
  if p_value is null
     or p_value !~ '^[1-9][0-9]{3}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$' then
    return false;
  end if;
  parsed:=p_value::timestamptz;
  return isfinite(parsed)
     and to_char(parsed at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')=p_value;
exception when others then return false;
end $$;

create or replace function ops.assurance_normalized_set(p_value jsonb)
returns jsonb language sql immutable strict set search_path=pg_catalog,ops,public as $$
  select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
    from jsonb_array_elements(p_value)
$$;

create or replace function ops.assurance_sorted_strings(p_value jsonb)
returns jsonb language sql immutable strict set search_path=pg_catalog as $$
  select coalesce(jsonb_agg(to_jsonb(value) order by value),'[]'::jsonb)
    from jsonb_array_elements_text(p_value)
$$;

create or replace function ops.assurance_unique_array(p_value jsonb)
returns boolean language sql immutable strict set search_path=pg_catalog,ops as $$
  select jsonb_typeof(p_value)='array'
     and jsonb_array_length(p_value)=(
       select count(distinct ops.assurance_digest(value))
         from jsonb_array_elements(p_value))
$$;

create or replace function ops.assurance_token_absent(p_value jsonb,p_token uuid)
returns boolean language sql immutable strict set search_path=pg_catalog,ops as $$
  select position(p_token::text in ops.guidance_import_canonical_json(p_value))=0
$$;

create or replace function ops.assurance_refusal(
  p_code text,p_causal_object text,p_expected jsonb,p_actual jsonb
) returns jsonb language plpgsql immutable set search_path=pg_catalog,ops as $$
begin
  if p_code is null or p_code<>all(array[
    'ASSURANCE_INPUT_INVALID','ASSURANCE_DIGEST_MISMATCH','ASSURANCE_BINDING_STALE',
    'ASSURANCE_STAGE_MISMATCH','ASSURANCE_SNAPSHOT_EXPIRED','ASSURANCE_RULE_SNAPSHOT_STALE',
    'ASSURANCE_COORDINATION_SNAPSHOT_STALE','REVIEWER_POLICY_UNSUPPORTED',
    'EVIDENCE_STAGE_UNSUPPORTED','EVIDENCE_REQUIREMENT_MISMATCH','EVIDENCE_FIELD_UNSUPPORTED',
    'EVIDENCE_POINTER_INVALID','EVIDENCE_ARTIFACT_MISMATCH','ASSURANCE_SELF_REVIEW',
    'OWNER_IDENTITY_MISMATCH','OWNER_ACCEPTANCE_NOT_REVIEW','IDEMPOTENCY_CONFLICT'
  ]::text[]) then
    raise exception 'assurance persistence refusal code is not registered';
  end if;
  return jsonb_build_object('ok',false,'refusal',jsonb_build_object(
    'code',p_code,'causal_object',p_causal_object,'expected',p_expected,'actual',p_actual));
end $$;

create or replace function ops.assurance_pinned_pointer(p_field text)
returns text language sql immutable strict set search_path=pg_catalog as $$
  select case p_field
    when 'argv' then '/command/argv'
    when 'cwd' then '/command/cwd'
    when 'commit_sha' then '/repository/commit_sha'
    when 'tree_sha' then '/repository/tree_sha'
    when 'environment' then '/environment'
    when 'toolchain' then '/toolchain'
    when 'output' then '/output'
    when 'timestamps' then '/timestamps'
    when 'artifacts' then '/artifacts'
    when 'exit_code' then '/output/exit_code'
    when 'stdout_digest' then '/output/stdout_digest'
    when 'stderr_digest' then '/output/stderr_digest'
    else null end
$$;

create or replace function ops.assurance_pointer_value(p_value jsonb,p_pointer text)
returns jsonb language sql immutable strict set search_path=pg_catalog as $$
  select case p_pointer
    when '/command/argv' then p_value#>'{command,argv}'
    when '/command/cwd' then p_value#>'{command,cwd}'
    when '/repository/commit_sha' then p_value#>'{repository,commit_sha}'
    when '/repository/tree_sha' then p_value#>'{repository,tree_sha}'
    when '/environment' then p_value->'environment'
    when '/toolchain' then p_value->'toolchain'
    when '/output' then p_value->'output'
    when '/timestamps' then p_value->'timestamps'
    when '/artifacts' then p_value->'artifacts'
    when '/output/exit_code' then p_value#>'{output,exit_code}'
    when '/output/stdout_digest' then p_value#>'{output,stdout_digest}'
    when '/output/stderr_digest' then p_value#>'{output,stderr_digest}'
    else null end
$$;

create or replace function ops.assurance_validate_compiler_input(
  p_lease_id uuid,p_compiler_input jsonb,p_manifest jsonb
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare l ops.canonical_ownership_lease%rowtype; sp ops.engineering_slice_plan%rowtype;
        contract jsonb; rules jsonb; coord jsonb; selected jsonb; required_test jsonb;
        planned_check jsonb; expected_claims jsonb; expected_dependencies jsonb;
        expected_coord_dependencies jsonb; expected_lease jsonb; expected_slice jsonb;
        expected_currentness jsonb; expected_base jsonb; expected_manifest jsonb;
        rule jsonb; requirement jsonb; forbidden jsonb;
        evaluation_at timestamptz; snapshot_as_of timestamptz; snapshot_until timestamptz;
        lease_expires_text text; selected_count integer;
begin
  select * into l from ops.canonical_ownership_lease where id=p_lease_id for key share;
  select * into sp from ops.engineering_slice_plan where id=l.slice_plan_id for key share;
  if l.id is null or sp.id is null then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','compiler_input.lineage',
      '"existing lease and Engineering Slice Plan"'::jsonb,'"absent"'::jsonb);
  end if;
  if not coalesce(ops.assurance_exact_object(p_compiler_input,array[
       'schema_version','work_request','accepted_plan_revision','engineering_slice_plan',
       'assurance_slice','repository','applicable_rules','coordination_snapshot',
       'declared_evaluation_time']),false)
     or p_compiler_input->>'schema_version' is distinct from 'assurance-compiler-input.v1'
     or p_compiler_input->'engineering_slice_plan' is distinct from sp.plan
     or not coalesce(ops.assurance_exact_object(p_compiler_input->'work_request',
          array['id','state_version','canonical_record_digest']),false)
     or not coalesce(ops.assurance_exact_object(p_compiler_input->'accepted_plan_revision',
          array['id','revision','digest']),false)
     or not coalesce(ops.assurance_exact_object(p_compiler_input->'repository',
          array['repository_id','commit_sha','tree_sha']),false)
     or p_compiler_input#>>'{repository,repository_id}' is distinct from 'repo:jbookout-carr-system'
     or not coalesce(p_compiler_input#>>'{repository,commit_sha}' ~ '^[0-9a-f]{40}$',false)
     or not coalesce(p_compiler_input#>>'{repository,tree_sha}' ~ '^[0-9a-f]{40}$',false)
     or not ops.assurance_timestamp_valid(p_compiler_input->>'declared_evaluation_time') then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input',
      '"closed normalized assurance-compiler-input.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  contract:=p_compiler_input->'assurance_slice';
  rules:=p_compiler_input->'applicable_rules';
  coord:=p_compiler_input->'coordination_snapshot';
  if not coalesce(ops.assurance_exact_object(contract,array[
       'schema_version','contract_digest','work_request','accepted_plan_revision',
       'engineering_slice_plan_digest','slice_ref','outcome','risk','path_claims',
       'forbidden_paths','dependencies','required_tests','evidence_requirements',
       'reviewer_policy','observable_output','rollback','release_class','unfinished_work',
       'repository_binding','rule_snapshot_binding','lease_binding','executor_identity']),false)
     or contract->>'schema_version' is distinct from 'assurance-slice-contract.v1'
     or not coalesce(contract->>'contract_digest' ~ '^sha256:[0-9a-f]{64}$',false)
     or not coalesce(ops.assurance_exact_object(contract->'work_request',
          array['id','state_version','canonical_record_digest']),false)
     or not coalesce(ops.assurance_exact_object(contract->'accepted_plan_revision',
          array['id','revision','digest']),false)
     or not coalesce(ops.assurance_identifier_valid(contract->>'slice_ref'),false)
     or jsonb_typeof(contract->'outcome') is distinct from 'string'
     or not coalesce(btrim(contract->>'outcome')<>'',false)
     or not coalesce(ops.assurance_exact_object(contract->'risk',array['risk_class','summary']),false)
     or not coalesce(contract#>>'{risk,risk_class}'=any(array['R0','R1','R2','R3','R4','R5','R6']),false)
     or not coalesce(btrim(contract#>>'{risk,summary}')<>'',false)
     or jsonb_typeof(contract->'path_claims') is distinct from 'array'
     or jsonb_typeof(contract->'forbidden_paths') is distinct from 'array'
     or jsonb_typeof(contract->'dependencies') is distinct from 'array'
     or jsonb_typeof(contract->'required_tests') is distinct from 'array'
     or jsonb_typeof(contract->'evidence_requirements') is distinct from 'array'
     or jsonb_typeof(contract->'unfinished_work') is distinct from 'array'
     or not coalesce(ops.assurance_unique_array(contract->'path_claims'),false)
     or not coalesce(ops.assurance_unique_array(contract->'forbidden_paths'),false)
     or not coalesce(ops.assurance_unique_array(contract->'dependencies'),false)
     or not coalesce(ops.assurance_unique_array(contract->'required_tests'),false)
     or not coalesce(ops.assurance_unique_array(contract->'evidence_requirements'),false)
     or not coalesce(ops.assurance_unique_array(contract->'unfinished_work'),false)
     or contract->'path_claims' is distinct from ops.assurance_normalized_set(contract->'path_claims')
     or contract->'forbidden_paths' is distinct from ops.assurance_normalized_set(contract->'forbidden_paths')
     or contract->'dependencies' is distinct from ops.assurance_normalized_set(contract->'dependencies')
     or contract->'required_tests' is distinct from ops.assurance_normalized_set(contract->'required_tests')
     or contract->'evidence_requirements' is distinct from ops.assurance_normalized_set(contract->'evidence_requirements')
     or contract->'unfinished_work' is distinct from ops.assurance_normalized_set(contract->'unfinished_work') then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.assurance_slice',
      '"full normalized assurance-slice-contract.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  select count(*),(jsonb_agg(value)->0) into selected_count,selected
    from jsonb_array_elements(sp.plan->'slices') value
   where value->>'slice_ref'=l.slice_ref;
  if selected_count<>1
     or contract->'work_request' is distinct from p_compiler_input->'work_request'
     or contract->'accepted_plan_revision' is distinct from p_compiler_input->'accepted_plan_revision'
     or contract->>'engineering_slice_plan_digest' is distinct from sp.plan_digest
     or contract->>'slice_ref' is distinct from l.slice_ref
     or contract#>>'{risk,risk_class}' is distinct from selected->>'risk_class'
     or p_compiler_input->'work_request' is distinct from sp.plan->'work_request'
     or p_compiler_input->'accepted_plan_revision' is distinct from sp.plan->'accepted_plan_revision'
     or p_compiler_input#>>'{work_request,id}' is distinct from 'wr:'||l.work_request_id::text
     or (p_compiler_input#>>'{work_request,state_version}')::integer is distinct from l.work_request_version
     or p_compiler_input#>>'{work_request,canonical_record_digest}' is distinct from l.work_request_digest
     or p_compiler_input#>>'{accepted_plan_revision,id}' is distinct from
        (select plan_ref from ops.sourced_work_request_plan where id=l.accepted_plan_id)
     or p_compiler_input#>>'{accepted_plan_revision,digest}' is distinct from l.accepted_plan_digest
     or p_compiler_input#>>'{repository,repository_id}' is distinct from contract#>>'{repository_binding,repository_id}'
     or p_compiler_input#>>'{repository,commit_sha}' is distinct from contract#>>'{repository_binding,commit_sha}'
     or p_compiler_input#>>'{repository,tree_sha}' is distinct from contract#>>'{repository_binding,tree_sha}' then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','compiler_input.canonical_lineage',
      '"exact live Work Request, plan, slice, and repository bindings"'::jsonb,'"mismatch"'::jsonb);
  end if;
  select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
    into expected_claims from (
      select jsonb_build_object('path',claim_value,'mode',claim_mode,'operation',operation) value
        from ops.canonical_ownership_claim
       where lease_id=l.id and claim_kind='path') q;
  select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
    into expected_dependencies from (
      select jsonb_build_object('slice_ref',dependency_slice_ref,'required_state',required_state) value
        from ops.canonical_ownership_dependency where lease_id=l.id) q;
  select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
    into expected_coord_dependencies from (
      select jsonb_build_object(
        'slice_ref',d.dependency_slice_ref,'state',d.required_state,
        'evidence_digest',case when d.required_state='independently_verified'
          then ops.assurance_digest(f.fact) else r.receipt_digest end) value
        from ops.canonical_ownership_dependency d
        join ops.engineering_slice_receipt r on r.id=d.observed_receipt_id
        left join ops.engineering_reviewer_fact f on f.id=d.observed_reviewer_fact_id
       where d.lease_id=l.id) q;
  if exists(select 1 from jsonb_array_elements(contract->'path_claims') x
       where not coalesce(ops.assurance_exact_object(x,array['path','mode','operation']),false)
          or not ops.canonical_ownership_path_valid(x->>'path')
          or x->>'mode'<>all(array['file','tree'])
          or x->>'operation'<>all(array['write','delete']))
     or exists(select 1 from jsonb_array_elements(contract->'dependencies') x
       where not coalesce(ops.assurance_exact_object(x,array['slice_ref','required_state']),false)
          or not ops.assurance_identifier_valid(x->>'slice_ref')
          or x->>'required_state'<>all(array['completed','independently_verified']))
     or contract->'path_claims' is distinct from expected_claims
     or contract->'dependencies' is distinct from expected_dependencies
     or exists(select 1 from ops.canonical_ownership_claim
                where lease_id=l.id and claim_kind<>'path')
     or exists(select 1 from jsonb_array_elements(contract->'forbidden_paths') x
       where not coalesce(ops.assurance_exact_object(x,array['path','mode']),false)
          or not ops.canonical_ownership_path_valid(x->>'path')
          or x->>'mode'<>all(array['file','tree']))
     or exists(select 1 from jsonb_array_elements(contract->'path_claims') a,
                    jsonb_array_elements(contract->'forbidden_paths') f
                where ops.canonical_ownership_paths_overlap(
                  a->>'path',a->>'mode',f->>'path',f->>'mode')) then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','compiler_input.assurance_slice.scope',
      '"exact A2 claims/dependencies and non-overlapping forbidden paths"'::jsonb,'"mismatch"'::jsonb);
  end if;
  if jsonb_array_length(selected->'planned_checks')<>1
     or jsonb_array_length(contract->'required_tests')<>1 then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.required_tests',
      '"one compiler-registered planned check"'::jsonb,'"invalid"'::jsonb);
  end if;
  planned_check:=selected->'planned_checks'->0;
  required_test:=contract->'required_tests'->0;
  if planned_check->>'check_ref' is distinct from 'check:compiler'
     or not coalesce(ops.assurance_exact_object(required_test,array[
       'check_ref','planned_check_digest','check_profile_ref','runner','test_artifact',
       'environment','environment_gate','argv','cwd','causal_failure','evidence_fields']),false)
     or required_test->>'check_ref' is distinct from 'check:compiler'
     or required_test->>'planned_check_digest' is distinct from ops.assurance_digest(planned_check)
     or required_test->>'check_profile_ref' is distinct from 'check-profile:assurance-compiler-v1'
     or required_test->>'runner' is distinct from 'python_pytest'
     or required_test->'argv' is distinct from
        '["python3","-m","pytest","-q","tools/test-assurance-slice-compiler.py"]'::jsonb
     or required_test->>'cwd' is distinct from '.'
     or required_test->'environment_gate' is distinct from
        '{"argv":["python3","-c","import pytest"],"must_pass_before_test":true,
          "causal_failure":{"code":"TEST_ENVIRONMENT_NOT_MATERIALIZED",
          "object":"environment:repository-python-lock",
          "expected":"pytest importable from requirements.lock"}}'::jsonb
     or not coalesce(ops.assurance_exact_object(required_test->'test_artifact',array['path','digest']),false)
     or required_test#>>'{test_artifact,path}' is distinct from 'tools/test-assurance-slice-compiler.py'
     or not coalesce(required_test#>>'{test_artifact,digest}' ~ '^sha256:[0-9a-f]{64}$',false)
     or not coalesce(ops.assurance_exact_object(required_test->'environment',
          array['environment_ref','runtime','version_source','dependency_lock']),false)
     or required_test#>>'{environment,environment_ref}' is distinct from 'environment:repository-python-lock'
     or required_test#>>'{environment,runtime}' is distinct from 'python3'
     or required_test#>>'{environment,version_source,path}' is distinct from '.python-version'
     or required_test#>>'{environment,dependency_lock,path}' is distinct from 'requirements.lock'
     or not coalesce(required_test#>>'{environment,version_source,digest}' ~ '^sha256:[0-9a-f]{64}$',false)
     or not coalesce(required_test#>>'{environment,dependency_lock,digest}' ~ '^sha256:[0-9a-f]{64}$',false)
     or required_test->'causal_failure' is distinct from jsonb_build_object(
          'code','REQUIRED_CHECK_FAILED','object','check:compiler',
          'expected',planned_check->>'failure_condition')
     or jsonb_typeof(required_test->'evidence_fields') is distinct from 'array'
     or jsonb_array_length(required_test->'evidence_fields')=0
     or required_test->'evidence_fields' is distinct from
        ops.assurance_sorted_strings(required_test->'evidence_fields')
     or exists(select 1 from jsonb_array_elements(required_test->'evidence_fields') x
                where jsonb_typeof(x)<>'string' or btrim(x#>>'{}')='') then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.required_tests.check:compiler',
      '"exact compiler-registered check refinement"'::jsonb,'"invalid"'::jsonb);
  end if;
  if jsonb_array_length(contract->'evidence_requirements')=0
     or exists(select 1 from jsonb_array_elements(contract->'evidence_requirements') x
       where not coalesce(ops.assurance_exact_object(x,array[
          'evidence_ref','artifact_kind','required_fields']),false)
          or not ops.assurance_identifier_valid(x->>'evidence_ref')
          or not ops.assurance_identifier_valid(x->>'artifact_kind')
          or jsonb_typeof(x->'required_fields')<>'array'
          or jsonb_array_length(x->'required_fields')=0
          or exists(select 1 from jsonb_array_elements(x->'required_fields') f
                     where jsonb_typeof(f)<>'string'
                        or btrim(f#>>'{}')='')
          or x->'required_fields' is distinct from ops.assurance_sorted_strings(x->'required_fields')
          or (select count(*) from jsonb_array_elements(x->'required_fields'))<>
             (select count(distinct f#>>'{}') from jsonb_array_elements(x->'required_fields') f))
     or not coalesce(ops.assurance_exact_object(contract->'reviewer_policy',array[
          'minimum_independent_reviewers','executor_actor_ref','executor_session_ref',
          'owner_acceptance_is_review','distinct_actor_and_session']),false)
     or jsonb_typeof(contract#>'{reviewer_policy,minimum_independent_reviewers}')<>'number'
     or not coalesce(contract#>>'{reviewer_policy,minimum_independent_reviewers}'~'^[1-9][0-9]*$',false)
     or contract#>'{reviewer_policy,owner_acceptance_is_review}' is distinct from 'false'::jsonb
     or contract#>'{reviewer_policy,distinct_actor_and_session}' is distinct from 'true'::jsonb
     or contract#>>'{reviewer_policy,executor_actor_ref}' is distinct from 'actor:'||l.holder_actor_slug
     or contract#>>'{reviewer_policy,executor_session_ref}' is distinct from l.holder_session_ref
     or not coalesce(ops.assurance_exact_object(contract->'observable_output',
          array['description','evidence_ref']),false)
     or not coalesce(btrim(contract#>>'{observable_output,description}')<>'',false)
     or not ops.assurance_identifier_valid(contract#>>'{observable_output,evidence_ref}')
     or not coalesce(ops.assurance_exact_object(contract->'rollback',
          array['strategy','argv','cwd','observable_success']),false)
     or not coalesce(btrim(contract#>>'{rollback,strategy}')<>'',false)
     or jsonb_typeof(contract#>'{rollback,argv}')<>'array'
     or jsonb_array_length(contract#>'{rollback,argv}')=0
     or exists(select 1 from jsonb_array_elements(contract#>'{rollback,argv}') x
                where jsonb_typeof(x)<>'string' or btrim(x#>>'{}')='')
     or not (contract#>>'{rollback,cwd}'='.'
             or ops.canonical_ownership_path_valid(contract#>>'{rollback,cwd}'))
     or not coalesce(btrim(contract#>>'{rollback,observable_success}')<>'',false)
     or contract->>'release_class'<>all(array['none','repository_only','runtime','production'])
     or exists(select 1 from jsonb_array_elements(contract->'unfinished_work') x
                where jsonb_typeof(x)<>'string' or btrim(x#>>'{}')='')
     or not coalesce(ops.assurance_exact_object(contract->'repository_binding',
          array['repository_id','commit_sha','tree_sha']),false)
     or not coalesce(ops.assurance_exact_object(contract->'rule_snapshot_binding',
          array['snapshot_ref','snapshot_digest']),false)
     or not coalesce(ops.assurance_exact_object(contract->'lease_binding',
          array['lease_id','fencing_generation','holder_session_id','holder_host_id']),false)
     or not coalesce(ops.assurance_exact_object(contract->'executor_identity',
          array['actor_ref','session_ref','host_ref']),false)
     or contract#>>'{lease_binding,lease_id}' is distinct from 'lease:'||l.id::text
     or (contract#>>'{lease_binding,fencing_generation}')::bigint is distinct from l.fencing_generation
     or contract#>>'{lease_binding,holder_session_id}' is distinct from l.holder_session_ref
     or contract#>>'{lease_binding,holder_host_id}' is distinct from l.holder_host_ref
     or contract#>>'{executor_identity,actor_ref}' is distinct from 'actor:'||l.holder_actor_slug
     or contract#>>'{executor_identity,session_ref}' is distinct from l.holder_session_ref
     or contract#>>'{executor_identity,host_ref}' is distinct from l.holder_host_ref then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.assurance_slice.contract',
      '"complete typed A1a contract"'::jsonb,'"invalid"'::jsonb);
  end if;
  if contract->>'contract_digest' is distinct from
       ops.assurance_digest(contract-'contract_digest') then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','compiler_input.assurance_slice.contract_digest',
      to_jsonb(ops.assurance_digest(contract-'contract_digest')),
      contract->'contract_digest');
  end if;
  if not coalesce(ops.assurance_exact_object(rules,array[
       'schema_version','snapshot_ref','snapshot_digest','rules']),false)
     or rules->>'schema_version' is distinct from 'applicable-rule-snapshot.v1'
     or not ops.assurance_identifier_valid(rules->>'snapshot_ref')
     or jsonb_typeof(rules->'rules') is distinct from 'array'
     or not coalesce(ops.assurance_unique_array(rules->'rules'),false)
     or rules->'rules' is distinct from ops.assurance_normalized_set(rules->'rules')
     or exists(select 1 from jsonb_array_elements(rules->'rules') x
       where not coalesce(ops.assurance_exact_object(x,array['rule_ref','revision','digest']),false)
          or not ops.assurance_identifier_valid(x->>'rule_ref')
          or jsonb_typeof(x->'revision')<>'number'
          or not coalesce((x->>'revision') ~ '^[1-9][0-9]*$',false)
          or not coalesce(x->>'digest' ~ '^sha256:[0-9a-f]{64}$',false))
     or exists(select 1 from jsonb_array_elements(rules->'rules') x
               group by x->>'rule_ref' having count(*)>1)
     or rules->>'snapshot_digest' is distinct from ops.assurance_digest(
          jsonb_build_object('snapshot_ref',rules->'snapshot_ref','rules',rules->'rules'))
     or contract#>>'{rule_snapshot_binding,snapshot_ref}' is distinct from rules->>'snapshot_ref'
     or contract#>>'{rule_snapshot_binding,snapshot_digest}' is distinct from rules->>'snapshot_digest' then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.applicable_rules',
      '"unique normalized typed rule snapshot"'::jsonb,'"invalid"'::jsonb);
  end if;
  lease_expires_text:=to_char(l.expires_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"');
  expected_lease:=jsonb_build_object(
    'lease_id','lease:'||l.id::text,'state','active',
    'holder_session_id',l.holder_session_ref,'holder_host_id',l.holder_host_ref,
    'expires_at',lease_expires_text,'fencing_generation',l.fencing_generation,
    'claims',expected_claims);
  if not coalesce(ops.assurance_exact_object(coord,array[
       'schema_version','snapshot_digest','as_of','valid_until','manifest_phase',
       'requesting_session_id','requesting_host_id','leases','dependencies']),false)
     or coord->>'schema_version' is distinct from 'assurance-coordination-snapshot.v1'
     or coord->>'manifest_phase' is distinct from 'baseline'
     or coord->>'requesting_session_id' is distinct from l.holder_session_ref
     or coord->>'requesting_host_id' is distinct from l.holder_host_ref
     or jsonb_typeof(coord->'leases') is distinct from 'array'
     or jsonb_typeof(coord->'dependencies') is distinct from 'array'
     or not coalesce(ops.assurance_unique_array(coord->'leases'),false)
     or not coalesce(ops.assurance_unique_array(coord->'dependencies'),false)
     or coord->'leases' is distinct from ops.assurance_normalized_set(coord->'leases')
     or coord->'dependencies' is distinct from ops.assurance_normalized_set(coord->'dependencies')
     or exists(select 1 from jsonb_array_elements(coord->'leases') x
       where not coalesce(ops.assurance_exact_object(x,array[
         'lease_id','state','holder_session_id','holder_host_id','expires_at',
         'fencing_generation','claims']),false)
          or x->>'state'<>all(array['active','released'])
          or not ops.assurance_identifier_valid(x->>'lease_id')
          or not ops.assurance_identifier_valid(x->>'holder_session_id')
          or not ops.assurance_identifier_valid(x->>'holder_host_id')
          or not ops.assurance_timestamp_valid(x->>'expires_at')
          or jsonb_typeof(x->'fencing_generation')<>'number'
          or not coalesce((x->>'fencing_generation')~'^[1-9][0-9]*$',false)
          or jsonb_typeof(x->'claims')<>'array'
          or not coalesce(ops.assurance_unique_array(x->'claims'),false)
          or x->'claims' is distinct from ops.assurance_normalized_set(x->'claims'))
     or exists(select 1 from jsonb_array_elements(coord->'dependencies') x
       where not coalesce(ops.assurance_exact_object(x,array[
         'slice_ref','state','evidence_digest']),false)
          or not ops.assurance_identifier_valid(x->>'slice_ref')
          or x->>'state'<>all(array['pending','completed','independently_verified'])
          or (x->'evidence_digest'<>'null'::jsonb
              and not coalesce(x->>'evidence_digest'~'^sha256:[0-9a-f]{64}$',false)))
     or exists(select 1 from jsonb_array_elements(coord->'leases') x
               group by x->>'lease_id' having count(*)>1)
     or exists(select 1 from jsonb_array_elements(coord->'dependencies') x
               group by x->>'slice_ref' having count(*)>1)
     or coord->'leases' is distinct from jsonb_build_array(expected_lease)
     or coord->'dependencies' is distinct from expected_coord_dependencies
     or not ops.assurance_timestamp_valid(coord->>'as_of')
     or not ops.assurance_timestamp_valid(coord->>'valid_until')
     or coord->>'snapshot_digest' is distinct from ops.assurance_digest(coord-'snapshot_digest') then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.coordination_snapshot',
      '"exact normalized live A2 coordination snapshot"'::jsonb,'"invalid"'::jsonb);
  end if;
  evaluation_at:=(p_compiler_input->>'declared_evaluation_time')::timestamptz;
  snapshot_as_of:=(coord->>'as_of')::timestamptz;
  snapshot_until:=(coord->>'valid_until')::timestamptz;
  if snapshot_until<=snapshot_as_of or evaluation_at<snapshot_as_of
     or evaluation_at>=snapshot_until or clock_timestamp()>=snapshot_until
     or snapshot_until>l.expires_at then
    return ops.assurance_refusal('ASSURANCE_SNAPSHOT_EXPIRED','compiler_input.coordination_snapshot.valid_until',
      '"as_of <= evaluation < valid_until <= live lease expiry"'::jsonb,'"outside current window"'::jsonb);
  end if;
  if p_compiler_input is distinct from jsonb_build_object(
       'schema_version',p_compiler_input->'schema_version',
       'work_request',p_compiler_input->'work_request',
       'accepted_plan_revision',p_compiler_input->'accepted_plan_revision',
       'engineering_slice_plan',p_compiler_input->'engineering_slice_plan',
       'assurance_slice',contract,'repository',p_compiler_input->'repository',
       'applicable_rules',rules,'coordination_snapshot',coord,
       'declared_evaluation_time',p_compiler_input->'declared_evaluation_time') then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.normalization',
      '"canonical normalized input"'::jsonb,'"noncanonical"'::jsonb);
  end if;
  expected_slice:=jsonb_build_object(
    'slice_ref',contract->'slice_ref','objective',selected->'objective',
    'outcome',contract->'outcome','risk',contract->'risk',
    'allowed_paths',contract->'path_claims','forbidden_paths',contract->'forbidden_paths',
    'dependency_gates',contract->'dependencies','required_tests',contract->'required_tests',
    'evidence_requirements',contract->'evidence_requirements',
    'reviewer_policy',contract->'reviewer_policy','observable_output',contract->'observable_output',
    'rollback',contract->'rollback','release_class',contract->'release_class',
    'unfinished_work',contract->'unfinished_work','lease_binding',contract->'lease_binding',
    'executor_identity',contract->'executor_identity');
  expected_currentness:=jsonb_build_object(
    'manifest_phase','baseline','authorizes_action',false,
    'currentness_state','declared_window_consistent_not_live_verified',
    'live_currentness_verified',false,
    'declared_evaluation_time',p_compiler_input->'declared_evaluation_time',
    'snapshot_as_of',coord->'as_of','snapshot_valid_until',coord->'valid_until',
    'lease_expires_at',to_jsonb(lease_expires_text),
    'usable_only_as_preflight_for','[]'::jsonb,
    'requires_live_currentness_check_before',
      '["write","test","commit","push","pr_update","review","merge","runtime_action"]'::jsonb,
    'recompile_against_resulting_commit_tree_before',
      '["commit","push","pr_update","review","merge","runtime_action"]'::jsonb);
  expected_base:=jsonb_build_object(
    'schema_version','assurance-execution-manifest.v1',
    'compiler',jsonb_build_object('id','carr-assurance-slice-compiler','version','1.0.0'),
    'authority_state','compiled_not_authorized','verification_state','unverified',
    'self_certification',false,'input_digest',ops.assurance_digest(p_compiler_input),
    'input_bindings',jsonb_build_object(
      'work_request',p_compiler_input->'work_request',
      'accepted_plan_revision',p_compiler_input->'accepted_plan_revision',
      'engineering_slice_plan_digest',to_jsonb(sp.plan_digest),
      'assurance_slice_contract_digest',contract->'contract_digest',
      'repository',p_compiler_input->'repository',
      'applicable_rule_snapshot_digest',rules->'snapshot_digest',
      'coordination_snapshot_digest',coord->'snapshot_digest'),
    'slice',expected_slice,'currentness',expected_currentness,
    'refusal_vocabulary','["INPUT_NOT_OBJECT","INPUT_UNKNOWN_FIELD","INPUT_MISSING_FIELD",
      "INPUT_SCHEMA_UNSUPPORTED","FIELD_INVALID","ENGINEERING_SLICE_PLAN_INVALID",
      "ASSURANCE_SLICE_ABSENT","ASSURANCE_CONTRACT_DIGEST_MISMATCH",
      "WORK_REQUEST_BINDING_MISMATCH","ACCEPTED_PLAN_BINDING_MISMATCH",
      "ENGINEERING_SLICE_PLAN_BINDING_MISMATCH","SLICE_BINDING_MISMATCH",
      "REPOSITORY_IDENTITY_MISMATCH","RULE_SNAPSHOT_DIGEST_MISMATCH",
      "RULE_SNAPSHOT_STALE","COORDINATION_SNAPSHOT_DIGEST_MISMATCH",
      "COORDINATION_SNAPSHOT_STALE","LEASE_NOT_FOUND","LEASE_RELEASED","LEASE_EXPIRED",
      "LEASE_BINDING_MISMATCH","REQUESTER_IDENTITY_MISMATCH","LEASE_CLAIMS_MISMATCH",
      "FOREIGN_LEASE_COLLISION","DEPENDENCY_MISSING","DEPENDENCY_UNSATISFIED","PATH_INVALID",
      "PATH_CASE_ALIAS","PATH_SCOPE_COLLISION","REQUIRED_TEST_BINDING_MISMATCH",
      "REVIEWER_POLICY_INVALID","COMPILER_INTERNAL_ERROR"]'::jsonb);
  expected_manifest:=expected_base||jsonb_build_object(
    'manifest_hash',ops.assurance_digest(expected_base));
  if p_manifest is distinct from expected_manifest then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','manifest.compiler_output',
      jsonb_build_object('manifest_hash',expected_manifest->'manifest_hash',
        'input_digest',expected_manifest->'input_digest'),
      jsonb_build_object('exact_compiler_output',false));
  end if;
  return jsonb_build_object('ok',true,'input_digest',p_manifest->'input_digest',
    'manifest_hash',p_manifest->'manifest_hash');
exception when others then
  return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','compiler_input.typed_shape',
    '"well-typed recursively closed A1a input"'::jsonb,
    jsonb_build_object('sqlstate',sqlstate));
end $$;

create or replace function ops.record_assurance_execution_manifest(
  p_lease_id uuid,p_lease_token uuid,p_fencing_generation bigint,
  p_repository_stage text,p_compiler_input jsonb,p_manifest jsonb,p_applicable_rules jsonb,
  p_coordination_snapshot jsonb,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare live jsonb; context jsonb; l ops.canonical_ownership_lease%rowtype;
        sp ops.engineering_slice_plan%rowtype; prior ops.assurance_execution_manifest%rowtype;
        actual_manifest_hash text; actual_rules_digest text; actual_coord_digest text;
        valid_until timestamptz; inserted ops.assurance_execution_manifest%rowtype;
        compiler_validation jsonb;
begin
  perform pg_advisory_xact_lock(hashtextextended('assurance-manifest-door',0));
  live:=ops.canonical_ownership_validate_live(p_lease_id,p_lease_token,p_fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  context:=ops.canonical_ownership_context();
  select * into l from ops.canonical_ownership_lease where id=p_lease_id for key share;
  select * into sp from ops.engineering_slice_plan where id=l.slice_plan_id for key share;
  if not ops.assurance_token_absent(p_compiler_input,p_lease_token)
     or not ops.assurance_token_absent(p_manifest,p_lease_token)
     or not ops.assurance_token_absent(p_applicable_rules,p_lease_token)
     or not ops.assurance_token_absent(p_coordination_snapshot,p_lease_token) then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','assurance.token_nondisclosure',
      '"lease token absent from all persisted and returned values"'::jsonb,'"token_present"'::jsonb);
  end if;
  if p_applicable_rules is distinct from p_compiler_input->'applicable_rules'
     or p_coordination_snapshot is distinct from p_compiler_input->'coordination_snapshot' then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','compiler_input.preimages',
      '"exact compiler rule and coordination preimages"'::jsonb,'"mismatch"'::jsonb);
  end if;
  compiler_validation:=ops.assurance_validate_compiler_input(
    p_lease_id,p_compiler_input,p_manifest);
  if not coalesce((compiler_validation->>'ok')::boolean,false) then
    return compiler_validation;
  end if;

  if p_repository_stage is null or p_repository_stage<>all(array[
       'write','run_check','post_commit','push','pull_request','review','merge'])
     or p_idempotency_key is null
     or not coalesce(ops.assurance_exact_object(p_manifest,array[
       'schema_version','compiler','authority_state','verification_state','self_certification',
       'input_digest','input_bindings','slice','currentness','refusal_vocabulary','manifest_hash']),false)
     or p_manifest->>'schema_version'<>'assurance-execution-manifest.v1'
     or p_manifest->>'authority_state'<>'compiled_not_authorized'
     or p_manifest->>'verification_state'<>'unverified'
     or p_manifest->'self_certification'<>'false'::jsonb
     or p_manifest#>>'{compiler,id}'<>'carr-assurance-slice-compiler'
     or not coalesce(btrim(p_manifest#>>'{compiler,version}')<>'',false) then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','manifest',
      '"closed A1a non-authorizing manifest and supported repository stage"'::jsonb,'"invalid"'::jsonb);
  end if;

  actual_manifest_hash:=ops.assurance_digest(p_manifest-'manifest_hash');
  if p_manifest->>'manifest_hash' is distinct from actual_manifest_hash then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','manifest.manifest_hash',
      to_jsonb(actual_manifest_hash),to_jsonb(p_manifest->>'manifest_hash'));
  end if;
  if not coalesce(ops.assurance_exact_object(p_applicable_rules,array[
       'schema_version','snapshot_ref','snapshot_digest','rules']),false)
     or p_applicable_rules->>'schema_version'<>'applicable-rule-snapshot.v1'
     or jsonb_typeof(p_applicable_rules->'rules')<>'array' then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','applicable_rules',
      '"normalized applicable-rule-snapshot.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual_rules_digest:=ops.assurance_digest(jsonb_build_object(
    'snapshot_ref',p_applicable_rules->'snapshot_ref','rules',p_applicable_rules->'rules'));
  if p_applicable_rules->>'snapshot_digest' is distinct from actual_rules_digest
     or p_manifest#>>'{input_bindings,applicable_rule_snapshot_digest}' is distinct from actual_rules_digest then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','applicable_rules.snapshot_digest',
      to_jsonb(actual_rules_digest),to_jsonb(p_applicable_rules->>'snapshot_digest'));
  end if;
  if not coalesce(ops.assurance_exact_object(p_coordination_snapshot,array[
       'schema_version','snapshot_digest','as_of','valid_until','manifest_phase',
       'requesting_session_id','requesting_host_id','leases','dependencies']),false)
     or p_coordination_snapshot->>'schema_version'<>'assurance-coordination-snapshot.v1'
     or jsonb_typeof(p_coordination_snapshot->'leases')<>'array'
     or jsonb_typeof(p_coordination_snapshot->'dependencies')<>'array' then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','coordination_snapshot',
      '"normalized assurance-coordination-snapshot.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual_coord_digest:=ops.assurance_digest(p_coordination_snapshot-'snapshot_digest');
  if p_coordination_snapshot->>'snapshot_digest' is distinct from actual_coord_digest
     or p_manifest#>>'{input_bindings,coordination_snapshot_digest}' is distinct from actual_coord_digest then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','coordination_snapshot.snapshot_digest',
      to_jsonb(actual_coord_digest),to_jsonb(p_coordination_snapshot->>'snapshot_digest'));
  end if;
  begin valid_until:=(p_coordination_snapshot->>'valid_until')::timestamptz;
  exception when others then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','coordination_snapshot.valid_until',
      '"valid timestamp"'::jsonb,'"invalid"'::jsonb);
  end;
  if clock_timestamp()>=valid_until
     or p_manifest#>>'{currentness,snapshot_valid_until}' is distinct from p_coordination_snapshot->>'valid_until' then
    return ops.assurance_refusal('ASSURANCE_SNAPSHOT_EXPIRED','coordination_snapshot.valid_until',
      to_jsonb(valid_until),to_jsonb(clock_timestamp()));
  end if;

  if p_manifest#>>'{input_bindings,work_request,id}' is distinct from 'wr:'||l.work_request_id::text
     or (p_manifest#>>'{input_bindings,work_request,state_version}')::integer is distinct from l.work_request_version
     or p_manifest#>>'{input_bindings,work_request,canonical_record_digest}' is distinct from l.work_request_digest
     or p_manifest#>>'{input_bindings,accepted_plan_revision,id}' is distinct from
        (select plan_ref from ops.sourced_work_request_plan where id=l.accepted_plan_id)
     or p_manifest#>>'{input_bindings,accepted_plan_revision,digest}' is distinct from l.accepted_plan_digest
     or p_manifest#>>'{input_bindings,engineering_slice_plan_digest}' is distinct from l.slice_plan_digest
     or p_manifest#>>'{slice,slice_ref}' is distinct from l.slice_ref
     or p_manifest#>>'{slice,lease_binding,lease_id}' is distinct from 'lease:'||l.id::text
     or (p_manifest#>>'{slice,lease_binding,fencing_generation}')::bigint is distinct from l.fencing_generation
     or p_manifest#>>'{slice,lease_binding,holder_session_id}' is distinct from l.holder_session_ref
     or p_manifest#>>'{slice,lease_binding,holder_host_id}' is distinct from l.holder_host_ref
     or p_manifest#>>'{slice,executor_identity,actor_ref}' is distinct from 'actor:'||l.holder_actor_slug
     or p_manifest#>>'{slice,executor_identity,session_ref}' is distinct from l.holder_session_ref
     or p_manifest#>>'{slice,executor_identity,host_ref}' is distinct from l.holder_host_ref
     or p_manifest#>>'{input_bindings,repository,commit_sha}' !~ '^[0-9a-f]{40}$'
     or p_manifest#>>'{input_bindings,repository,tree_sha}' !~ '^[0-9a-f]{40}$'
     or p_coordination_snapshot->>'requesting_session_id' is distinct from l.holder_session_ref
     or p_coordination_snapshot->>'requesting_host_id' is distinct from l.holder_host_ref
     or not exists (select 1 from jsonb_array_elements(p_coordination_snapshot->'leases') x
       where x->>'lease_id'='lease:'||l.id::text
         and (x->>'fencing_generation')::bigint=l.fencing_generation
         and x->>'holder_session_id'=l.holder_session_ref and x->>'holder_host_id'=l.holder_host_ref)
     or context->>'tenant' is distinct from l.organization_tenant_id
     or (context->>'actor_id')::uuid is distinct from l.holder_actor_id
     or context->>'session_ref' is distinct from l.holder_session_ref
     or context->>'host_ref' is distinct from l.holder_host_ref then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','manifest.lineage',
      '"exact current Work Request/plan/slice/lease/context bindings"'::jsonb,'"mismatch"'::jsonb);
  end if;
  if p_manifest#>>'{slice,reviewer_policy,minimum_independent_reviewers}'<>'1'
     or p_manifest#>'{slice,reviewer_policy,owner_acceptance_is_review}'<>'false'::jsonb
     or p_manifest#>'{slice,reviewer_policy,distinct_actor_and_session}'<>'true'::jsonb then
    return ops.assurance_refusal('REVIEWER_POLICY_UNSUPPORTED','manifest.slice.reviewer_policy',
      '{"minimum_independent_reviewers":1,"owner_acceptance_is_review":false,"distinct_actor_and_session":true}'::jsonb,
      p_manifest#>'{slice,reviewer_policy}');
  end if;

  select * into prior from ops.assurance_execution_manifest where idempotency_key=p_idempotency_key;
  if found then
    if prior.repository_stage=p_repository_stage and prior.manifest=p_manifest
       and prior.applicable_rules=p_applicable_rules and prior.coordination_snapshot=p_coordination_snapshot
       and prior.compiler_input=p_compiler_input
       and prior.lease_id=p_lease_id and prior.fencing_generation=p_fencing_generation then
      return jsonb_build_object('ok',true,'manifest_id',prior.id,'manifest_hash',prior.manifest_hash,'replayed',true);
    end if;
    return ops.assurance_refusal('IDEMPOTENCY_CONFLICT','assurance_execution_manifest.idempotency_key',
      '"exact prior request"'::jsonb,'"changed request"'::jsonb);
  end if;
  select * into prior from ops.assurance_execution_manifest where manifest_hash=p_manifest->>'manifest_hash';
  if found then
    if prior.repository_stage=p_repository_stage and prior.manifest=p_manifest
       and prior.compiler_input=p_compiler_input then
      return jsonb_build_object('ok',true,'manifest_id',prior.id,
        'manifest_hash',prior.manifest_hash,'replayed',true);
    end if;
    return ops.assurance_refusal('ASSURANCE_STAGE_MISMATCH','manifest.manifest_hash',
      to_jsonb(prior.repository_stage),to_jsonb(p_repository_stage));
  end if;

  insert into ops.assurance_execution_manifest(
    organization_tenant_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,
    lease_id,fencing_generation,repository_stage,repository_commit_sha,repository_tree_sha,
    compiler_id,compiler_version,input_digest,manifest_hash,applicable_rule_snapshot_digest,
    coordination_snapshot_digest,snapshot_valid_until,applicable_rules,coordination_snapshot,
    compiler_input,ownership_contract_digest,manifest,idempotency_key)
  values(l.organization_tenant_id,l.work_request_id,l.accepted_plan_id,l.slice_plan_id,l.slice_ref,
    l.id,l.fencing_generation,p_repository_stage,
    p_manifest#>>'{input_bindings,repository,commit_sha}',p_manifest#>>'{input_bindings,repository,tree_sha}',
    p_manifest#>>'{compiler,id}',p_manifest#>>'{compiler,version}',p_manifest->>'input_digest',
    p_manifest->>'manifest_hash',actual_rules_digest,actual_coord_digest,valid_until,
    p_applicable_rules,p_coordination_snapshot,p_compiler_input,l.contract_digest,
    p_manifest,p_idempotency_key)
  returning * into inserted;
  return jsonb_build_object('ok',true,'manifest_id',inserted.id,'manifest_hash',inserted.manifest_hash,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed then
  return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','manifest.typed_binding',
    '"well-typed exact bindings"'::jsonb,'"invalid"'::jsonb);
end $$;

create or replace function ops.assurance_manifest_currentness(
  p_manifest_id uuid,p_required_stage text,p_observed_commit_sha text,p_observed_tree_sha text,
  p_observed_rule_snapshot_digest text,p_observed_coordination_snapshot_digest text,p_lease_token uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare m ops.assurance_execution_manifest%rowtype; live jsonb;
begin
  select * into m from ops.assurance_execution_manifest where id=p_manifest_id for key share;
  if not found then return ops.assurance_refusal('ASSURANCE_BINDING_STALE','manifest.id','"existing manifest"'::jsonb,'null'::jsonb); end if;
  live:=ops.canonical_ownership_validate_live(m.lease_id,p_lease_token,m.fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  if clock_timestamp()>=m.snapshot_valid_until then
    return ops.assurance_refusal('ASSURANCE_SNAPSHOT_EXPIRED','manifest.snapshot_valid_until',to_jsonb(m.snapshot_valid_until),to_jsonb(clock_timestamp()));
  end if;
  if p_required_stage is distinct from m.repository_stage then
    return ops.assurance_refusal('ASSURANCE_STAGE_MISMATCH','manifest.repository_stage',to_jsonb(m.repository_stage),to_jsonb(p_required_stage));
  end if;
  if p_observed_commit_sha is distinct from m.repository_commit_sha
     or p_observed_tree_sha is distinct from m.repository_tree_sha then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','manifest.repository',
      jsonb_build_object('commit_sha',m.repository_commit_sha,'tree_sha',m.repository_tree_sha),
      jsonb_build_object('commit_sha',p_observed_commit_sha,'tree_sha',p_observed_tree_sha));
  end if;
  if p_observed_rule_snapshot_digest is distinct from m.applicable_rule_snapshot_digest then
    return ops.assurance_refusal('ASSURANCE_RULE_SNAPSHOT_STALE','manifest.applicable_rule_snapshot_digest',
      to_jsonb(m.applicable_rule_snapshot_digest),to_jsonb(p_observed_rule_snapshot_digest));
  end if;
  if p_observed_coordination_snapshot_digest is distinct from m.coordination_snapshot_digest then
    return ops.assurance_refusal('ASSURANCE_COORDINATION_SNAPSHOT_STALE','manifest.coordination_snapshot_digest',
      to_jsonb(m.coordination_snapshot_digest),to_jsonb(p_observed_coordination_snapshot_digest));
  end if;
  return jsonb_build_object('ok',true,'manifest_id',m.id,'repository_stage',m.repository_stage,
    'repository_commit_sha',m.repository_commit_sha,'repository_tree_sha',m.repository_tree_sha,
    'authorizes_action',false,'evaluated_at',clock_timestamp());
end $$;

create or replace function ops.record_assurance_evidence_extension(
  p_receipt_id uuid,p_manifest_id uuid,p_lease_token uuid,p_evidence jsonb,
  p_evidence_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare m ops.assurance_execution_manifest%rowtype; r ops.engineering_slice_receipt%rowtype;
        e ops.engineering_execution_envelope%rowtype; prior ops.assurance_evidence_extension%rowtype;
        live jsonb; actual_digest text; requirement jsonb; result jsonb; field text;
        artifact_ref jsonb; inserted ops.assurance_evidence_extension%rowtype;
        l ops.canonical_ownership_lease%rowtype; sp ops.engineering_slice_plan%rowtype;
        executor_slug text; started_at timestamptz; finished_at timestamptz;
begin
  perform pg_advisory_xact_lock(hashtextextended('assurance-evidence-door',0));
  select * into m from ops.assurance_execution_manifest where id=p_manifest_id for key share;
  if not found then return ops.assurance_refusal('ASSURANCE_BINDING_STALE','evidence.manifest_id','"existing manifest"'::jsonb,'null'::jsonb); end if;
  live:=ops.canonical_ownership_validate_live(m.lease_id,p_lease_token,m.fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  select * into r from ops.engineering_slice_receipt where id=p_receipt_id for key share;
  select * into e from ops.engineering_execution_envelope where id=r.envelope_id for key share;
  select * into l from ops.canonical_ownership_lease where id=m.lease_id for key share;
  select * into sp from ops.engineering_slice_plan where id=e.slice_plan_id for key share;
  select slug into executor_slug from public.actor
   where id=r.executor_actor_id and active for share;
  if m.repository_stage<>'post_commit' then
    return ops.assurance_refusal('EVIDENCE_STAGE_UNSUPPORTED','manifest.repository_stage','"post_commit"'::jsonb,to_jsonb(m.repository_stage));
  end if;
  if p_idempotency_key is null
     or not coalesce(ops.assurance_exact_object(p_evidence,array[
       'schema_version','manifest_hash','engineering_receipt_digest','repository','command',
       'environment','toolchain','output','timestamps','artifacts','requirements','fencing_generation']),false)
     or p_evidence->>'schema_version'<>'assurance-evidence.v1'
     or not coalesce(ops.assurance_exact_object(p_evidence->'repository',array['commit_sha','tree_sha','stage']),false)
     or not coalesce(ops.assurance_exact_object(p_evidence->'command',array['argv','cwd']),false)
     or not coalesce(ops.assurance_exact_object(p_evidence->'output',array['exit_code','stdout_digest','stderr_digest']),false)
     or not coalesce(ops.assurance_exact_object(p_evidence->'timestamps',array['started_at','finished_at']),false)
     or jsonb_typeof(p_evidence#>'{command,argv}')<>'array'
     or jsonb_array_length(p_evidence#>'{command,argv}')=0
     or exists(select 1 from jsonb_array_elements(p_evidence#>'{command,argv}') x
                where jsonb_typeof(x)<>'string' or btrim(x#>>'{}')='')
     or not (p_evidence#>>'{command,cwd}'='.'
             or ops.canonical_ownership_path_valid(p_evidence#>>'{command,cwd}'))
     or not coalesce(ops.assurance_exact_object(p_evidence->'environment',
          array['environment_ref','network_access']),false)
     or not ops.assurance_identifier_valid(p_evidence#>>'{environment,environment_ref}')
     or p_evidence#>'{environment,network_access}' is distinct from 'false'::jsonb
     or not coalesce(ops.assurance_exact_object(p_evidence->'toolchain',
          array['runtime','runtime_version','database','database_version']),false)
     or exists(select 1 from unnest(array['runtime','runtime_version','database','database_version']) key
                where jsonb_typeof(p_evidence->'toolchain'->key)<>'string'
                   or btrim(p_evidence->'toolchain'->>key)='')
     or jsonb_typeof(p_evidence->'artifacts')<>'array'
     or jsonb_array_length(p_evidence->'artifacts')=0
     or jsonb_typeof(p_evidence->'requirements')<>'array'
     or jsonb_typeof(p_evidence#>'{output,exit_code}')<>'number'
     or not coalesce(p_evidence#>>'{output,exit_code}' ~ '^(0|[1-9][0-9]*)$',false)
     or (p_evidence#>>'{output,exit_code}')::numeric>255
     or p_evidence#>>'{output,stdout_digest}' !~ '^sha256:[0-9a-f]{64}$'
     or p_evidence#>>'{output,stderr_digest}' !~ '^sha256:[0-9a-f]{64}$'
     or not ops.assurance_timestamp_valid(p_evidence#>>'{timestamps,started_at}')
     or not ops.assurance_timestamp_valid(p_evidence#>>'{timestamps,finished_at}')
     or not ops.assurance_token_absent(p_evidence,l.lease_token) then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','evidence','"closed assurance-evidence.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual_digest:=ops.assurance_digest(p_evidence);
  if p_evidence_digest is distinct from actual_digest then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','evidence_digest',to_jsonb(actual_digest),to_jsonb(p_evidence_digest));
  end if;
  if r.id is null or e.id is null or r.outcome<>'claimed_complete'
     or r.work_request_id<>m.work_request_id or r.slice_ref<>m.slice_ref
     or e.id<>l.subject_envelope_id or e.work_request_id<>m.work_request_id
     or e.accepted_plan_id<>m.accepted_plan_id or e.slice_plan_id<>m.slice_plan_id
     or e.slice_ref<>m.slice_ref or sp.plan_digest<>m.manifest#>>'{input_bindings,engineering_slice_plan_digest}'
     or r.executor_actor_id<>l.holder_actor_id or executor_slug<>l.holder_actor_slug
     or e.envelope#>>'{agent_session,id}'<>l.holder_session_ref
     or m.manifest#>>'{slice,executor_identity,host_ref}'<>l.holder_host_ref
     or m.organization_tenant_id<>l.organization_tenant_id
     or p_evidence->>'manifest_hash'<>m.manifest_hash
     or p_evidence->>'engineering_receipt_digest'<>r.receipt_digest
     or p_evidence#>>'{repository,stage}'<>'post_commit'
     or p_evidence#>>'{repository,commit_sha}'<>m.repository_commit_sha
     or p_evidence#>>'{repository,tree_sha}'<>m.repository_tree_sha
     or (p_evidence->>'fencing_generation')::bigint<>m.fencing_generation then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','evidence.lineage',
      '"exact post-commit manifest/receipt/envelope/fence"'::jsonb,'"mismatch"'::jsonb);
  end if;
  begin
    started_at:=(p_evidence#>>'{timestamps,started_at}')::timestamptz;
    finished_at:=(p_evidence#>>'{timestamps,finished_at}')::timestamptz;
    if finished_at<started_at or started_at<date_trunc('second',l.acquired_at)
       or finished_at>clock_timestamp() or finished_at>m.snapshot_valid_until then
      return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','evidence.timestamps','"finished_at >= started_at"'::jsonb,'"reversed"'::jsonb);
    end if;
  exception when others then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','evidence.timestamps','"valid timestamps"'::jsonb,'"invalid"'::jsonb);
  end;
  if exists (select 1 from jsonb_array_elements(p_evidence->'artifacts') a
      where not coalesce(ops.assurance_exact_object(a,array['artifact_ref','path','digest','artifact_kind']),false)
         or not ops.canonical_ownership_path_valid(a->>'path')
         or a->>'digest' !~ '^sha256:[0-9a-f]{64}$'
         or a->>'artifact_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
         or a->>'artifact_kind' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or exists (select 1 from jsonb_array_elements(p_evidence->'artifacts') a group by a->>'artifact_ref' having count(*)>1)
     or exists (select 1 from jsonb_array_elements(p_evidence->'artifacts') a group by lower(a->>'path') having count(*)>1) then
    return ops.assurance_refusal('EVIDENCE_ARTIFACT_MISMATCH','evidence.artifacts','"unique valid artifacts"'::jsonb,'"invalid"'::jsonb);
  end if;
  if jsonb_array_length(p_evidence->'requirements')<>
     jsonb_array_length(m.manifest#>'{slice,evidence_requirements}') then
    return ops.assurance_refusal('EVIDENCE_REQUIREMENT_MISMATCH','evidence.requirements','"exact manifest requirement set"'::jsonb,'"cardinality mismatch"'::jsonb);
  end if;
  for requirement in select value from jsonb_array_elements(m.manifest#>'{slice,evidence_requirements}') loop
    select value into result from jsonb_array_elements(p_evidence->'requirements')
      where value->>'evidence_ref'=requirement->>'evidence_ref';
    if result is null or not coalesce(ops.assurance_exact_object(result,array[
         'evidence_ref','artifact_kind','field_bindings','artifact_refs']),false)
       or result->>'artifact_kind'<>requirement->>'artifact_kind'
       or jsonb_typeof(result->'field_bindings')<>'object'
       or jsonb_typeof(result->'artifact_refs')<>'array'
       or jsonb_array_length(result->'artifact_refs')=0 then
      return ops.assurance_refusal('EVIDENCE_REQUIREMENT_MISMATCH','evidence.requirements.'||(requirement->>'evidence_ref'),requirement,result);
    end if;
    if (select coalesce(array_agg(k order by k),'{}'::text[]) from jsonb_object_keys(result->'field_bindings') k)
       is distinct from
       (select coalesce(array_agg(value order by value),'{}'::text[]) from jsonb_array_elements_text(requirement->'required_fields')) then
      return ops.assurance_refusal('EVIDENCE_REQUIREMENT_MISMATCH','evidence.requirements.field_bindings',requirement->'required_fields',result->'field_bindings');
    end if;
    for field in select value from jsonb_array_elements_text(requirement->'required_fields') loop
      if ops.assurance_pinned_pointer(field) is null then
        return ops.assurance_refusal('EVIDENCE_FIELD_UNSUPPORTED','evidence.required_field','"pinned A3a field"'::jsonb,to_jsonb(field));
      end if;
      if result->'field_bindings'->>field is distinct from ops.assurance_pinned_pointer(field)
         or ops.assurance_pointer_value(p_evidence,result->'field_bindings'->>field) is null
         or ops.assurance_pointer_value(p_evidence,result->'field_bindings'->>field)='null'::jsonb then
        return ops.assurance_refusal('EVIDENCE_POINTER_INVALID','evidence.field_bindings.'||field,to_jsonb(ops.assurance_pinned_pointer(field)),to_jsonb(result->'field_bindings'->>field));
      end if;
    end loop;
    for artifact_ref in select value from jsonb_array_elements(result->'artifact_refs') loop
      if jsonb_typeof(artifact_ref)<>'string' or not exists (
        select 1 from jsonb_array_elements(p_evidence->'artifacts') a
         where a->>'artifact_ref'=artifact_ref#>>'{}' and a->>'artifact_kind'=requirement->>'artifact_kind') then
        return ops.assurance_refusal('EVIDENCE_ARTIFACT_MISMATCH','evidence.requirements.artifact_refs',
          to_jsonb(requirement->>'artifact_kind'),artifact_ref);
      end if;
    end loop;
    result:=null;
  end loop;
  if exists (select 1 from jsonb_array_elements(p_evidence->'requirements') x
      where not exists (select 1 from jsonb_array_elements(m.manifest#>'{slice,evidence_requirements}') q
        where q->>'evidence_ref'=x->>'evidence_ref')) then
    return ops.assurance_refusal('EVIDENCE_REQUIREMENT_MISMATCH','evidence.requirements','"no extra requirement"'::jsonb,'"extra"'::jsonb);
  end if;

  select * into prior from ops.assurance_evidence_extension where idempotency_key=p_idempotency_key;
  if found then
    if prior.receipt_id=p_receipt_id and prior.manifest_id=p_manifest_id
       and prior.evidence=p_evidence and prior.evidence_digest=p_evidence_digest then
      return jsonb_build_object('ok',true,'evidence_id',prior.id,'evidence_digest',prior.evidence_digest,'replayed',true);
    end if;
    return ops.assurance_refusal('IDEMPOTENCY_CONFLICT','assurance_evidence_extension.idempotency_key','"exact prior request"'::jsonb,'"changed request"'::jsonb);
  end if;
  if exists (select 1 from ops.assurance_evidence_extension where receipt_id=p_receipt_id or manifest_id=p_manifest_id) then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','assurance_evidence_extension.one_to_one','"one post-commit extension"'::jsonb,'"already exists"'::jsonb);
  end if;
  insert into ops.assurance_evidence_extension(receipt_id,manifest_id,evidence_digest,evidence,idempotency_key)
    values(p_receipt_id,p_manifest_id,p_evidence_digest,p_evidence,p_idempotency_key) returning * into inserted;
  return jsonb_build_object('ok',true,'evidence_id',inserted.id,'evidence_digest',inserted.evidence_digest,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed
                    or check_violation or foreign_key_violation or unique_violation then
  return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','evidence.typed_binding','"well-typed exact evidence"'::jsonb,'"invalid"'::jsonb);
end $$;

create or replace function ops.record_assurance_review_extension(
  p_reviewer_fact_id uuid,p_review_manifest_id uuid,p_evidence_id uuid,
  p_review jsonb,p_review_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare f ops.engineering_reviewer_fact%rowtype; r ops.engineering_slice_receipt%rowtype;
        m ops.assurance_execution_manifest%rowtype; ev ops.assurance_evidence_extension%rowtype;
        em ops.assurance_execution_manifest%rowtype; prior ops.assurance_review_extension%rowtype;
        actual text; inserted ops.assurance_review_extension%rowtype;
        l ops.canonical_ownership_lease%rowtype; context jsonb; reviewer public.actor%rowtype;
        reviewed_at timestamptz; finished_at timestamptz;
begin
  perform pg_advisory_xact_lock(hashtextextended('assurance-review-door',0));
  context:=ops.canonical_ownership_context();
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  select * into f from ops.engineering_reviewer_fact where id=p_reviewer_fact_id for key share;
  select * into r from ops.engineering_slice_receipt where id=f.receipt_id for key share;
  select * into m from ops.assurance_execution_manifest where id=p_review_manifest_id for key share;
  select * into ev from ops.assurance_evidence_extension where id=p_evidence_id for key share;
  select * into em from ops.assurance_execution_manifest where id=ev.manifest_id for key share;
  select * into l from ops.canonical_ownership_lease where id=m.lease_id for key share;
  select * into reviewer from public.actor where id=f.reviewer_actor_id and active for share;
  if p_idempotency_key is null or f.id is null or r.id is null or m.id is null or ev.id is null
     or not coalesce(ops.assurance_exact_object(p_review,array[
       'schema_version','manifest_hash','evidence_digest','state','self_issued',
       'owner_acceptance','reviewer_actor_ref','reviewer_session_ref','reviewer_host_ref',
       'evidence_refs','reviewed_deviation_refs','resolved_deviation_refs','reviewed_at']),false)
     or p_review->>'schema_version'<>'assurance-review.v1'
     or p_review->'self_issued'<>'false'::jsonb
     or p_review->'owner_acceptance'<>'false'::jsonb
     or not ops.assurance_identifier_valid(p_review->>'reviewer_actor_ref')
     or not ops.assurance_identifier_valid(p_review->>'reviewer_session_ref')
     or not ops.assurance_identifier_valid(p_review->>'reviewer_host_ref')
     or jsonb_typeof(p_review->'evidence_refs')<>'array'
     or jsonb_array_length(p_review->'evidence_refs')=0
     or jsonb_typeof(p_review->'reviewed_deviation_refs')<>'array'
     or jsonb_typeof(p_review->'resolved_deviation_refs')<>'array'
     or not ops.assurance_timestamp_valid(p_review->>'reviewed_at')
     or not ops.assurance_token_absent(p_review,l.lease_token) then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','review','"closed independent assurance-review.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual:=ops.assurance_digest(p_review);
  if p_review_digest is distinct from actual then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','review_digest',to_jsonb(actual),to_jsonb(p_review_digest));
  end if;
  if m.repository_stage<>'review' or em.repository_stage<>'post_commit'
     or m.repository_commit_sha<>em.repository_commit_sha or m.repository_tree_sha<>em.repository_tree_sha
     or m.organization_tenant_id<>em.organization_tenant_id
     or m.work_request_id<>em.work_request_id or m.accepted_plan_id<>em.accepted_plan_id
     or m.slice_plan_id<>em.slice_plan_id or m.slice_ref<>em.slice_ref
     or m.lease_id<>em.lease_id or m.fencing_generation<>em.fencing_generation
     or m.manifest->'slice'<>em.manifest->'slice'
     or m.work_request_id<>r.work_request_id or m.slice_ref<>r.slice_ref or ev.receipt_id<>r.id
     or f.contract_version is distinct from 'engineering-review.v1'
     or reviewer.id is null or context->>'tenant' is distinct from m.organization_tenant_id
     or (context->>'actor_id')::uuid is distinct from f.reviewer_actor_id
     or context->>'session_ref' is distinct from f.reviewer_session_ref
     or p_review->>'reviewer_actor_ref' is distinct from 'actor:'||reviewer.slug
     or p_review->>'reviewer_session_ref' is distinct from f.reviewer_session_ref
     or p_review->>'reviewer_host_ref' is distinct from context->>'host_ref'
     or f.work_request_id<>r.work_request_id or f.slice_ref<>r.slice_ref
     or f.state is distinct from 'passed'
     or f.fact->'is_independent' is distinct from 'true'::jsonb
     or f.fact->>'attempt_id' is distinct from r.attempt_id
     or f.fact->>'slice_ref' is distinct from r.slice_ref
     or f.fact->>'state' is distinct from f.state
     or f.fact->>'session_ref' is distinct from f.reviewer_session_ref
     or f.fact->'evidence_refs' is distinct from p_review->'evidence_refs'
     or f.fact->'reviewed_deviation_refs' is distinct from p_review->'reviewed_deviation_refs'
     or f.fact->'resolved_deviation_refs' is distinct from p_review->'resolved_deviation_refs'
     or p_review->>'manifest_hash'<>m.manifest_hash or p_review->>'evidence_digest'<>ev.evidence_digest
     or p_review->>'state'<>f.state then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','review.lineage','"exact review-stage manifest plus post-commit evidence"'::jsonb,'"mismatch"'::jsonb);
  end if;
  if f.reviewer_actor_id=r.executor_actor_id
     or f.reviewer_session_ref=r.receipt#>>'{attribution,session_ref}' then
    return ops.assurance_refusal('ASSURANCE_SELF_REVIEW','review.reviewer','"distinct actor and session"'::jsonb,'"self"'::jsonb);
  end if;
  if f.state='passed' and (ev.evidence#>>'{output,exit_code}')::integer<>0 then
    return ops.assurance_refusal('EVIDENCE_REQUIREMENT_MISMATCH','review.pass','"exit_code 0"'::jsonb,ev.evidence#>'{output,exit_code}');
  end if;
  reviewed_at:=(p_review->>'reviewed_at')::timestamptz;
  finished_at:=(ev.evidence#>>'{timestamps,finished_at}')::timestamptz;
  if reviewed_at<finished_at or reviewed_at>clock_timestamp()
     or reviewed_at>m.snapshot_valid_until or l.state<>'active'
     or l.expires_at<=reviewed_at then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','review.reviewed_at',
      '"evidence completion <= reviewed_at <= live manifest window"'::jsonb,'"invalid"'::jsonb);
  end if;
  select * into prior from ops.assurance_review_extension where idempotency_key=p_idempotency_key;
  if found then
    if prior.reviewer_fact_id=p_reviewer_fact_id and prior.review_manifest_id=p_review_manifest_id
       and prior.evidence_id=p_evidence_id and prior.review=p_review and prior.review_digest=p_review_digest then
      return jsonb_build_object('ok',true,'review_id',prior.id,'review_digest',prior.review_digest,'replayed',true);
    end if;
    return ops.assurance_refusal('IDEMPOTENCY_CONFLICT','assurance_review_extension.idempotency_key','"exact prior request"'::jsonb,'"changed request"'::jsonb);
  end if;
  if exists (select 1 from ops.assurance_review_extension where reviewer_fact_id=p_reviewer_fact_id or evidence_id=p_evidence_id) then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','assurance_review_extension.one_to_one','"one existing independent review extension"'::jsonb,'"already exists"'::jsonb);
  end if;
  insert into ops.assurance_review_extension(reviewer_fact_id,review_manifest_id,evidence_id,review_digest,review,idempotency_key)
    values(p_reviewer_fact_id,p_review_manifest_id,p_evidence_id,p_review_digest,p_review,p_idempotency_key) returning * into inserted;
  return jsonb_build_object('ok',true,'review_id',inserted.id,'review_digest',inserted.review_digest,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed
                    or check_violation or foreign_key_violation or unique_violation then
  return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','review.typed_binding','"well-typed exact review"'::jsonb,'"invalid"'::jsonb);
end $$;

create or replace function ops.record_assurance_owner_acceptance(
  p_review_manifest_id uuid,p_evidence_id uuid,p_decision text,p_acceptance jsonb,
  p_acceptance_digest text,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare authority_slug text; context jsonb; actor_row public.actor%rowtype;
        m ops.assurance_execution_manifest%rowtype; ev ops.assurance_evidence_extension%rowtype;
        em ops.assurance_execution_manifest%rowtype; prior ops.assurance_owner_acceptance_fact%rowtype;
        actual text; inserted ops.assurance_owner_acceptance_fact%rowtype;
        review_row ops.assurance_review_extension%rowtype;
        l ops.canonical_ownership_lease%rowtype; decided_at timestamptz; reviewed_at timestamptz;
begin
  perform pg_advisory_xact_lock(hashtextextended('assurance-owner-door',0));
  authority_slug:=ops.authority_actor_slug();
  context:=ops.canonical_ownership_context();
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  select * into actor_row from public.actor where slug=authority_slug and kind='human' and active for share;
  if authority_slug<>all(array['joe','dell']) or actor_row.id is null
     or context->>'actor_slug' is distinct from authority_slug
     or (context->>'actor_id')::uuid is distinct from actor_row.id then
    return ops.assurance_refusal('OWNER_IDENTITY_MISMATCH','owner.identity',
      jsonb_build_object('authority_slug',authority_slug,'context_actor_slug',authority_slug),
      jsonb_build_object('authority_context_equal',false));
  end if;
  select * into m from ops.assurance_execution_manifest where id=p_review_manifest_id for key share;
  select * into ev from ops.assurance_evidence_extension where id=p_evidence_id for key share;
  select * into em from ops.assurance_execution_manifest where id=ev.manifest_id for key share;
  select * into l from ops.canonical_ownership_lease where id=m.lease_id for key share;
  select * into review_row from ops.assurance_review_extension
   where review_manifest_id=p_review_manifest_id and evidence_id=p_evidence_id for key share;
  if m.id is not null and not ops.assurance_token_absent(p_acceptance,l.lease_token) then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','assurance.token_nondisclosure',
      '"lease token absent from owner acceptance"'::jsonb,'"token_present"'::jsonb);
  end if;
  if review_row.id is null then
    return ops.assurance_refusal('OWNER_ACCEPTANCE_NOT_REVIEW',
      'owner_acceptance.assurance_review',
      '"persisted independent assurance review extension"'::jsonb,'"absent"'::jsonb);
  end if;
  if p_decision is null or p_decision<>all(array['accept','hold','reject']) or p_idempotency_key is null
     or m.id is null or ev.id is null
     or not coalesce(ops.assurance_exact_object(p_acceptance,array[
       'schema_version','manifest_hash','evidence_digest','decision','owner_acceptance',
       'independent_review','actor_ref','session_ref','host_ref','review_digest',
       'reason','decided_at']),false)
     or p_acceptance->>'schema_version'<>'assurance-owner-acceptance.v1'
     or p_acceptance->'owner_acceptance'<>'true'::jsonb
     or p_acceptance->'independent_review'<>'false'::jsonb
     or not coalesce(btrim(p_acceptance->>'reason')<>'',false)
     or not ops.assurance_timestamp_valid(p_acceptance->>'decided_at') then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance','"closed assurance-owner-acceptance.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual:=ops.assurance_digest(p_acceptance);
  if p_acceptance_digest is distinct from actual then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','acceptance_digest',to_jsonb(actual),to_jsonb(p_acceptance_digest));
  end if;
  if m.repository_stage<>'review' or em.repository_stage<>'post_commit'
     or m.repository_commit_sha<>em.repository_commit_sha or m.repository_tree_sha<>em.repository_tree_sha
     or m.work_request_id<>em.work_request_id or m.accepted_plan_id<>em.accepted_plan_id
     or m.slice_plan_id<>em.slice_plan_id or m.slice_ref<>em.slice_ref
     or m.lease_id<>em.lease_id or m.fencing_generation<>em.fencing_generation
     or m.manifest->'slice'<>em.manifest->'slice'
     or m.organization_tenant_id<>context->>'tenant'
     or p_acceptance->>'manifest_hash'<>m.manifest_hash
     or p_acceptance->>'evidence_digest'<>ev.evidence_digest
     or p_acceptance->>'decision'<>p_decision
     or p_acceptance->>'actor_ref'<>'actor:'||authority_slug
     or p_acceptance->>'session_ref'<>context->>'session_ref'
     or p_acceptance->>'host_ref'<>context->>'host_ref' then
    return ops.assurance_refusal('OWNER_IDENTITY_MISMATCH','owner_acceptance.lineage','"exact authority/context/review/evidence binding"'::jsonb,'"mismatch"'::jsonb);
  end if;
  if p_acceptance->>'review_digest' is distinct from review_row.review_digest then
    return ops.assurance_refusal('OWNER_ACCEPTANCE_NOT_REVIEW',
      'owner_acceptance.review_digest',to_jsonb(review_row.review_digest),
      p_acceptance->'review_digest');
  end if;
  decided_at:=(p_acceptance->>'decided_at')::timestamptz;
  reviewed_at:=(review_row.review->>'reviewed_at')::timestamptz;
  if decided_at<reviewed_at or decided_at>clock_timestamp()
     or decided_at>m.snapshot_valid_until or l.state<>'active'
     or l.expires_at<=decided_at then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance.decided_at',
      '"reviewed_at <= decided_at <= live manifest window"'::jsonb,'"invalid"'::jsonb);
  end if;
  select * into prior from ops.assurance_owner_acceptance_fact where idempotency_key=p_idempotency_key;
  if found then
    if prior.review_manifest_id=p_review_manifest_id and prior.evidence_id=p_evidence_id
       and prior.decision=p_decision and prior.acceptance=p_acceptance
       and prior.acceptance_digest=p_acceptance_digest and prior.owner_actor_id=actor_row.id then
      return jsonb_build_object('ok',true,'acceptance_id',prior.id,'decision',prior.decision,'replayed',true);
    end if;
    return ops.assurance_refusal('IDEMPOTENCY_CONFLICT','assurance_owner_acceptance_fact.idempotency_key','"exact prior request"'::jsonb,'"changed request"'::jsonb);
  end if;
  insert into ops.assurance_owner_acceptance_fact(
    organization_tenant_id,review_manifest_id,evidence_id,review_id,owner_actor_id,owner_actor_slug,
    owner_session_ref,owner_host_ref,decision,acceptance_digest,acceptance,idempotency_key)
  values(context->>'tenant',p_review_manifest_id,p_evidence_id,review_row.id,actor_row.id,authority_slug,
    context->>'session_ref',context->>'host_ref',p_decision,p_acceptance_digest,p_acceptance,p_idempotency_key)
  returning * into inserted;
  return jsonb_build_object('ok',true,'acceptance_id',inserted.id,'decision',inserted.decision,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed
                    or check_violation or foreign_key_violation or unique_violation then
  return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance.typed_binding','"well-typed exact acceptance"'::jsonb,'"invalid"'::jsonb);
end $$;

create or replace function ops.refuse_assurance_persistence_rewrite()
returns trigger language plpgsql set search_path=pg_catalog,ops as $$
begin raise exception '% is append-only',tg_table_name; end $$;

create trigger assurance_manifest_append_only before update or delete on ops.assurance_execution_manifest
for each row execute function ops.refuse_assurance_persistence_rewrite();
create trigger assurance_evidence_append_only before update or delete on ops.assurance_evidence_extension
for each row execute function ops.refuse_assurance_persistence_rewrite();
create trigger assurance_review_append_only before update or delete on ops.assurance_review_extension
for each row execute function ops.refuse_assurance_persistence_rewrite();
create trigger assurance_owner_acceptance_append_only before update or delete on ops.assurance_owner_acceptance_fact
for each row execute function ops.refuse_assurance_persistence_rewrite();

revoke all on ops.assurance_execution_manifest,ops.assurance_evidence_extension,
  ops.assurance_review_extension,ops.assurance_owner_acceptance_fact
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function
  ops.assurance_exact_object(jsonb,text[]),ops.assurance_digest(jsonb),
  ops.assurance_identifier_valid(text),ops.assurance_timestamp_valid(text),
  ops.assurance_normalized_set(jsonb),ops.assurance_sorted_strings(jsonb),
  ops.assurance_unique_array(jsonb),ops.assurance_token_absent(jsonb,uuid),
  ops.assurance_refusal(text,text,jsonb,jsonb),ops.assurance_pinned_pointer(text),
  ops.assurance_pointer_value(jsonb,text),
  ops.assurance_validate_compiler_input(uuid,jsonb,jsonb),
  ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid),
  ops.assurance_manifest_currentness(uuid,text,text,text,text,text,uuid),
  ops.record_assurance_evidence_extension(uuid,uuid,uuid,jsonb,text,uuid),
  ops.record_assurance_review_extension(uuid,uuid,uuid,jsonb,text,uuid),
  ops.record_assurance_owner_acceptance(uuid,uuid,text,jsonb,text,uuid),
  ops.refuse_assurance_persistence_rewrite()
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

do $$
declare rel text; fn text;
begin
  foreach rel in array array[
    'ops.assurance_execution_manifest','ops.assurance_evidence_extension',
    'ops.assurance_review_extension','ops.assurance_owner_acceptance_fact'] loop
    if to_regclass(rel) is null then raise exception '0451 FAILED: missing table %',rel; end if;
    if has_table_privilege('carr_reader',rel,'SELECT') or has_table_privilege('carr_writer',rel,'INSERT')
       or has_table_privilege('carr_jobs',rel,'INSERT') or has_table_privilege('carr_authority',rel,'INSERT') then
      raise exception '0451 FAILED: assurance persistence ACL widened for %',rel;
    end if;
  end loop;
  foreach fn in array array[
    'ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)',
    'ops.assurance_manifest_currentness(uuid,text,text,text,text,text,uuid)',
    'ops.record_assurance_evidence_extension(uuid,uuid,uuid,jsonb,text,uuid)',
    'ops.record_assurance_review_extension(uuid,uuid,uuid,jsonb,text,uuid)',
    'ops.record_assurance_owner_acceptance(uuid,uuid,text,jsonb,text,uuid)'] loop
    if exists (select 1 from pg_proc p cross join lateral
         aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
       where p.oid=fn::regprocedure and acl.grantee=0 and acl.privilege_type='EXECUTE')
       or has_function_privilege('carr_reader',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_writer',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_jobs',fn::regprocedure,'EXECUTE')
       or has_function_privilege('carr_authority',fn::regprocedure,'EXECUTE') then
      raise exception '0451 FAILED: assurance door is not dark: %',fn;
    end if;
  end loop;
end $$;

commit;
