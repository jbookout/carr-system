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

create or replace function ops.record_assurance_execution_manifest(
  p_lease_id uuid,p_lease_token uuid,p_fencing_generation bigint,
  p_repository_stage text,p_manifest jsonb,p_applicable_rules jsonb,
  p_coordination_snapshot jsonb,p_idempotency_key uuid
) returns jsonb language plpgsql volatile security definer
set search_path=pg_catalog,ops,public as $$
declare live jsonb; context jsonb; l ops.canonical_ownership_lease%rowtype;
        sp ops.engineering_slice_plan%rowtype; prior ops.assurance_execution_manifest%rowtype;
        actual_manifest_hash text; actual_rules_digest text; actual_coord_digest text;
        valid_until timestamptz; inserted ops.assurance_execution_manifest%rowtype;
begin
  live:=ops.canonical_ownership_validate_live(p_lease_id,p_lease_token,p_fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  context:=ops.canonical_ownership_context();
  select * into l from ops.canonical_ownership_lease where id=p_lease_id for key share;
  select * into sp from ops.engineering_slice_plan where id=l.slice_plan_id for key share;

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
     or p_manifest#>>'{input_bindings,assurance_slice_contract_digest}' is distinct from l.contract_digest
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
       and prior.lease_id=p_lease_id and prior.fencing_generation=p_fencing_generation then
      return jsonb_build_object('ok',true,'manifest_id',prior.id,'manifest_hash',prior.manifest_hash,'replayed',true);
    end if;
    return ops.assurance_refusal('IDEMPOTENCY_CONFLICT','assurance_execution_manifest.idempotency_key',
      '"exact prior request"'::jsonb,'"changed request"'::jsonb);
  end if;
  select * into prior from ops.assurance_execution_manifest where manifest_hash=p_manifest->>'manifest_hash';
  if found then
    return ops.assurance_refusal('ASSURANCE_STAGE_MISMATCH','manifest.manifest_hash',
      to_jsonb(prior.repository_stage),to_jsonb(p_repository_stage));
  end if;

  insert into ops.assurance_execution_manifest(
    organization_tenant_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,
    lease_id,fencing_generation,repository_stage,repository_commit_sha,repository_tree_sha,
    compiler_id,compiler_version,input_digest,manifest_hash,applicable_rule_snapshot_digest,
    coordination_snapshot_digest,snapshot_valid_until,applicable_rules,coordination_snapshot,
    manifest,idempotency_key)
  values(l.organization_tenant_id,l.work_request_id,l.accepted_plan_id,l.slice_plan_id,l.slice_ref,
    l.id,l.fencing_generation,p_repository_stage,
    p_manifest#>>'{input_bindings,repository,commit_sha}',p_manifest#>>'{input_bindings,repository,tree_sha}',
    p_manifest#>>'{compiler,id}',p_manifest#>>'{compiler,version}',p_manifest->>'input_digest',
    p_manifest->>'manifest_hash',actual_rules_digest,actual_coord_digest,valid_until,
    p_applicable_rules,p_coordination_snapshot,p_manifest,p_idempotency_key)
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
begin
  select * into m from ops.assurance_execution_manifest where id=p_manifest_id for key share;
  if not found then return ops.assurance_refusal('ASSURANCE_BINDING_STALE','evidence.manifest_id','"existing manifest"'::jsonb,'null'::jsonb); end if;
  live:=ops.canonical_ownership_validate_live(m.lease_id,p_lease_token,m.fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  select * into r from ops.engineering_slice_receipt where id=p_receipt_id for key share;
  select * into e from ops.engineering_execution_envelope where id=r.envelope_id for key share;
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
     or not coalesce(btrim(p_evidence#>>'{command,cwd}')<>'',false)
     or jsonb_typeof(p_evidence->'environment')<>'object'
     or jsonb_typeof(p_evidence->'toolchain')<>'object'
     or jsonb_typeof(p_evidence->'artifacts')<>'array'
     or jsonb_array_length(p_evidence->'artifacts')=0
     or jsonb_typeof(p_evidence->'requirements')<>'array'
     or p_evidence#>>'{output,stdout_digest}' !~ '^sha256:[0-9a-f]{64}$'
     or p_evidence#>>'{output,stderr_digest}' !~ '^sha256:[0-9a-f]{64}$' then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','evidence','"closed assurance-evidence.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual_digest:=ops.assurance_digest(p_evidence);
  if p_evidence_digest is distinct from actual_digest then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','evidence_digest',to_jsonb(actual_digest),to_jsonb(p_evidence_digest));
  end if;
  if r.id is null or e.id is null or r.outcome<>'claimed_complete'
     or r.work_request_id<>m.work_request_id or r.slice_ref<>m.slice_ref
     or e.id<>(select subject_envelope_id from ops.canonical_ownership_lease where id=m.lease_id)
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
    if (p_evidence#>>'{timestamps,finished_at}')::timestamptz <
       (p_evidence#>>'{timestamps,started_at}')::timestamptz then
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
    return ops.assurance_refusal('EVIDENCE_STAGE_UNSUPPORTED','assurance_evidence_extension.one_to_one','"one post-commit extension"'::jsonb,'"already exists"'::jsonb);
  end if;
  insert into ops.assurance_evidence_extension(receipt_id,manifest_id,evidence_digest,evidence,idempotency_key)
    values(p_receipt_id,p_manifest_id,p_evidence_digest,p_evidence,p_idempotency_key) returning * into inserted;
  return jsonb_build_object('ok',true,'evidence_id',inserted.id,'evidence_digest',inserted.evidence_digest,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed then
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
begin
  select * into f from ops.engineering_reviewer_fact where id=p_reviewer_fact_id for key share;
  select * into r from ops.engineering_slice_receipt where id=f.receipt_id for key share;
  select * into m from ops.assurance_execution_manifest where id=p_review_manifest_id for key share;
  select * into ev from ops.assurance_evidence_extension where id=p_evidence_id for key share;
  select * into em from ops.assurance_execution_manifest where id=ev.manifest_id for key share;
  if p_idempotency_key is null or f.id is null or r.id is null or m.id is null or ev.id is null
     or not coalesce(ops.assurance_exact_object(p_review,array[
       'schema_version','manifest_hash','evidence_digest','state','self_issued',
       'owner_acceptance','evidence_refs','reviewed_at']),false)
     or p_review->>'schema_version'<>'assurance-review.v1'
     or p_review->'self_issued'<>'false'::jsonb
     or p_review->'owner_acceptance'<>'false'::jsonb
     or jsonb_typeof(p_review->'evidence_refs')<>'array'
     or jsonb_array_length(p_review->'evidence_refs')=0 then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','review','"closed independent assurance-review.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual:=ops.assurance_digest(p_review);
  if p_review_digest is distinct from actual then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','review_digest',to_jsonb(actual),to_jsonb(p_review_digest));
  end if;
  if m.repository_stage<>'review' or em.repository_stage<>'post_commit'
     or m.repository_commit_sha<>em.repository_commit_sha or m.repository_tree_sha<>em.repository_tree_sha
     or m.work_request_id<>r.work_request_id or m.slice_ref<>r.slice_ref or ev.receipt_id<>r.id
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
  select * into prior from ops.assurance_review_extension where idempotency_key=p_idempotency_key;
  if found then
    if prior.reviewer_fact_id=p_reviewer_fact_id and prior.review_manifest_id=p_review_manifest_id
       and prior.evidence_id=p_evidence_id and prior.review=p_review and prior.review_digest=p_review_digest then
      return jsonb_build_object('ok',true,'review_id',prior.id,'review_digest',prior.review_digest,'replayed',true);
    end if;
    return ops.assurance_refusal('IDEMPOTENCY_CONFLICT','assurance_review_extension.idempotency_key','"exact prior request"'::jsonb,'"changed request"'::jsonb);
  end if;
  if exists (select 1 from ops.assurance_review_extension where reviewer_fact_id=p_reviewer_fact_id or evidence_id=p_evidence_id) then
    return ops.assurance_refusal('ASSURANCE_SELF_REVIEW','assurance_review_extension.one_to_one','"one existing independent review extension"'::jsonb,'"already exists"'::jsonb);
  end if;
  insert into ops.assurance_review_extension(reviewer_fact_id,review_manifest_id,evidence_id,review_digest,review,idempotency_key)
    values(p_reviewer_fact_id,p_review_manifest_id,p_evidence_id,p_review_digest,p_review,p_idempotency_key) returning * into inserted;
  return jsonb_build_object('ok',true,'review_id',inserted.id,'review_digest',inserted.review_digest,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed then
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
begin
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
  if p_decision is null or p_decision<>all(array['accept','hold','reject']) or p_idempotency_key is null
     or m.id is null or ev.id is null
     or not coalesce(ops.assurance_exact_object(p_acceptance,array[
       'schema_version','manifest_hash','evidence_digest','decision','owner_acceptance',
       'independent_review','actor_ref','session_ref','host_ref','reason','decided_at']),false)
     or p_acceptance->>'schema_version'<>'assurance-owner-acceptance.v1'
     or p_acceptance->'owner_acceptance'<>'true'::jsonb
     or p_acceptance->'independent_review'<>'false'::jsonb
     or not coalesce(btrim(p_acceptance->>'reason')<>'',false) then
    return ops.assurance_refusal('ASSURANCE_INPUT_INVALID','owner_acceptance','"closed assurance-owner-acceptance.v1"'::jsonb,'"invalid"'::jsonb);
  end if;
  actual:=ops.assurance_digest(p_acceptance);
  if p_acceptance_digest is distinct from actual then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH','acceptance_digest',to_jsonb(actual),to_jsonb(p_acceptance_digest));
  end if;
  if m.repository_stage<>'review' or em.repository_stage<>'post_commit'
     or m.repository_commit_sha<>em.repository_commit_sha or m.repository_tree_sha<>em.repository_tree_sha
     or m.organization_tenant_id<>context->>'tenant'
     or p_acceptance->>'manifest_hash'<>m.manifest_hash
     or p_acceptance->>'evidence_digest'<>ev.evidence_digest
     or p_acceptance->>'decision'<>p_decision
     or p_acceptance->>'actor_ref'<>'actor:'||authority_slug
     or p_acceptance->>'session_ref'<>context->>'session_ref'
     or p_acceptance->>'host_ref'<>context->>'host_ref' then
    return ops.assurance_refusal('OWNER_IDENTITY_MISMATCH','owner_acceptance.lineage','"exact authority/context/review/evidence binding"'::jsonb,'"mismatch"'::jsonb);
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
    organization_tenant_id,review_manifest_id,evidence_id,owner_actor_id,owner_actor_slug,
    owner_session_ref,owner_host_ref,decision,acceptance_digest,acceptance,idempotency_key)
  values(context->>'tenant',p_review_manifest_id,p_evidence_id,actor_row.id,authority_slug,
    context->>'session_ref',context->>'host_ref',p_decision,p_acceptance_digest,p_acceptance,p_idempotency_key)
  returning * into inserted;
  return jsonb_build_object('ok',true,'acceptance_id',inserted.id,'decision',inserted.decision,'replayed',false);
exception when invalid_text_representation or numeric_value_out_of_range or null_value_not_allowed then
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
  ops.assurance_refusal(text,text,jsonb,jsonb),ops.assurance_pinned_pointer(text),
  ops.assurance_pointer_value(jsonb,text),
  ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,uuid),
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
    'ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,uuid)',
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
