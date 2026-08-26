-- Program 5 bounded forward-fix staging rehearsal interstitial.
--
-- The full Production release identity remains in ops.release.  This adds a
-- second, explicitly STAGING-only identity for a contiguous source prefix:
-- 0315 + this interstitial, with 0316 and 0317 held back.  It never changes a
-- Production deploy's required full-tree schema identity.

begin;

create table ops.staging_forward_fix_bounded_contract (
  id uuid primary key default gen_random_uuid(),
  rehearsal_attempt_id uuid not null unique
    references ops.staging_forward_fix_rehearsal_attempt(id) on delete restrict,
  contract_sha256 text not null check (contract_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  source_artifact_digest text not null check (source_artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
  source_schema_highest_migration text not null,
  source_schema_applied_count integer not null check (source_schema_applied_count > 0),
  source_schema_ledger_sha256 text not null check (source_schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  target_schema_highest_migration text not null
    check (target_schema_highest_migration='0315a_program5_bounded_forward_fix_rehearsal.sql'),
  target_schema_applied_count integer not null check (target_schema_applied_count > 0),
  target_schema_ledger_sha256 text not null check (target_schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  selected_migrations text[] not null check (selected_migrations=array[
    '0315_program5_forward_fix_rehearsal.sql',
    '0315a_program5_bounded_forward_fix_rehearsal.sql'
  ]),
  selected_ordinals integer[] not null check (
    selected_ordinals=array[target_schema_applied_count-1,target_schema_applied_count]
  ),
  selected_migrations_sha256 text not null check (selected_migrations_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  held_back_migrations text[] not null check (held_back_migrations=array[
    '0316_rule_delivery_audit_counts.sql',
    '0317_atomic_rule_delivery_cutover.sql'
  ]),
  held_back_ordinals integer[] not null check (
    held_back_ordinals=array[target_schema_applied_count+1,target_schema_applied_count+2]
  ),
  held_back_migrations_sha256 text not null check (held_back_migrations_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  writer_session_user text not null default session_user
    check (writer_session_user='carr_jobs')
);

create or replace function ops.refuse_staging_forward_fix_bounded_contract_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'Program 5 bounded forward-fix contracts are append-only';
end $$;
create trigger staging_forward_fix_bounded_contract_append_only
before update or delete on ops.staging_forward_fix_bounded_contract
for each row execute function ops.refuse_staging_forward_fix_bounded_contract_rewrite();

alter table ops.staging_recovery_rehearsal_bundle
  add column bounded_forward_fix_contract_id uuid
    references ops.staging_forward_fix_bounded_contract(id) on delete restrict;

alter table ops.staging_recovery_rehearsal_bundle
  add constraint staging_recovery_bundle_bounded_forward_fix_shape
  check (
    (recovery_strategy='forward_fix')
    or (recovery_strategy='rollback' and bounded_forward_fix_contract_id is null)
  ) not valid;

revoke all on ops.staging_forward_fix_bounded_contract
  from public,carr_reader,carr_writer,carr_authority,carr_program5_forward_fix_verifiers;
grant select on ops.staging_forward_fix_bounded_contract to carr_jobs;

create or replace function ops.read_staging_forward_fix_bounded_contract(p_idempotency_key uuid)
returns table(expected_provider_tag text,contract_sha256 text,source_artifact_digest text,
  source_schema_highest_migration text,source_schema_applied_count integer,
  source_schema_ledger_sha256 text,target_schema_highest_migration text,
  target_schema_applied_count integer,target_schema_ledger_sha256 text,
  selected_migrations text[],selected_ordinals integer[],selected_migrations_sha256 text,
  held_back_migrations text[],held_back_ordinals integer[],held_back_migrations_sha256 text)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if session_user<>'carr_program5_forward_fix_verifier'
     or not pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member') then
    raise exception using errcode='42501',message='bounded forward-fix projection requires scoped verifier';
  end if;
  return query select a.expected_provider_tag,c.contract_sha256,c.source_artifact_digest,
    c.source_schema_highest_migration,c.source_schema_applied_count,c.source_schema_ledger_sha256,
    c.target_schema_highest_migration,c.target_schema_applied_count,c.target_schema_ledger_sha256,
    c.selected_migrations,c.selected_ordinals,c.selected_migrations_sha256,
    c.held_back_migrations,c.held_back_ordinals,c.held_back_migrations_sha256
  from ops.staging_forward_fix_rehearsal_attempt a
  join ops.staging_forward_fix_bounded_contract c on c.rehearsal_attempt_id=a.id
  where a.idempotency_key=p_idempotency_key;
end $$;

revoke all on function ops.read_staging_forward_fix_bounded_contract(uuid) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.read_staging_forward_fix_bounded_contract(uuid) to carr_program5_forward_fix_verifiers;

-- This is a separate staging-only recorder.  0315's full-tree
-- record_staging_forward_fix_rehearsal remains unchanged and is the only
-- forward-fix path that can satisfy the Production predicate.
create or replace function ops.record_staging_bounded_forward_fix_rehearsal(
  p_idempotency_key uuid, p_provider_version_id uuid, p_provider_tag text, p_verb_count integer,
  p_schema_highest_migration text, p_schema_applied_count integer, p_schema_ledger_sha256 text,
  p_doctrine_generation bigint, p_program6_actions_enabled boolean
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare attempt ops.staging_forward_fix_rehearsal_attempt%rowtype; existing ops.staging_forward_fix_rehearsal_result%rowtype;
  bounded ops.staging_forward_fix_bounded_contract%rowtype; result_uuid uuid; bundle_uuid uuid; run_uuid uuid;
  observed_time timestamptz:=clock_timestamp(); projection jsonb; projection_hash text; result_ref text;
  bundle_projection jsonb; bundle_hash text; bundle_ref text;
begin
  if session_user<>'carr_program5_forward_fix_verifier'
     or not pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member') then
    raise exception using errcode='42501',message='forward-fix rehearsal result requires scoped verifier';
  end if;
  if p_idempotency_key is null or p_provider_version_id is null
     or coalesce(p_provider_tag,'') !~ '^carr-staging-forward-fix-[0-9a-f]{32}$'
     or coalesce(p_verb_count,0)<=0
     or coalesce(p_schema_highest_migration,'') !~ '^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$'
     or coalesce(p_schema_applied_count,0)<=0
     or coalesce(p_schema_ledger_sha256,'') !~ '^sha256:[0-9a-f]{64}$'
     or coalesce(p_doctrine_generation,-1)<0 or p_program6_actions_enabled is null then
    raise exception 'invalid typed bounded forward-fix staging readback input';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,315));
  select * into attempt from ops.staging_forward_fix_rehearsal_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'forward-fix result has no prepared attempt'; end if;
  select * into bounded from ops.staging_forward_fix_bounded_contract where rehearsal_attempt_id=attempt.id;
  if not found then raise exception 'forward-fix result has no immutable bounded staging contract'; end if;
  if not exists(select 1 from ops.staging_forward_fix_rehearsal_claim where rehearsal_attempt_id=attempt.id) then
    raise exception 'forward-fix result was never claimed';
  end if;
  if p_provider_version_id=attempt.candidate_provider_version_id or p_provider_tag<>attempt.expected_provider_tag
     or p_schema_highest_migration<>bounded.target_schema_highest_migration
     or p_schema_applied_count<>bounded.target_schema_applied_count
     or p_schema_ledger_sha256<>bounded.target_schema_ledger_sha256 then
    raise exception 'forward-fix readback does not match exact bounded staging prefix';
  end if;
  select * into existing from ops.staging_forward_fix_rehearsal_result where rehearsal_attempt_id=attempt.id;
  if found then
    if (existing.provider_version_id,existing.provider_tag,existing.verb_count,existing.schema_highest_migration,
        existing.schema_applied_count,existing.schema_ledger_sha256,existing.doctrine_generation,existing.program6_actions_enabled) is distinct from
       (p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,p_schema_applied_count,
        p_schema_ledger_sha256,p_doctrine_generation,p_program6_actions_enabled) then
      raise exception 'forward-fix result idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('forward_fix_rehearsal_result_id',existing.id,'result_ref',existing.evidence_ref,
      'bundle_id',(select id from ops.staging_recovery_rehearsal_bundle where forward_fix_result_id=existing.id),
      'recovery_run_id',(select id from ops.run where recovery_rehearsal_bundle_id=(select id from ops.staging_recovery_rehearsal_bundle where forward_fix_result_id=existing.id)),
      'replayed',true);
  end if;
  projection:=jsonb_build_object('forward_fix_rehearsal_attempt_id',attempt.id,'environment','staging',
    'git_sha',attempt.git_sha,'provider_version_id',p_provider_version_id,'provider_tag',p_provider_tag,
    'schema_highest_migration',p_schema_highest_migration,'schema_applied_count',p_schema_applied_count,
    'schema_ledger_sha256',p_schema_ledger_sha256,'bounded_contract_sha256',bounded.contract_sha256);
  projection_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  result_ref:='ops.staging-forward-fix-readback:'||projection_hash;
  insert into ops.staging_forward_fix_rehearsal_result(
    idempotency_key,rehearsal_attempt_id,provider_version_id,provider_tag,verb_count,schema_highest_migration,
    schema_applied_count,schema_ledger_sha256,doctrine_generation,program6_actions_enabled,
    projection_sha256,evidence_ref,observed_at,writer_session_user)
  values(p_idempotency_key,attempt.id,p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,
    p_schema_applied_count,p_schema_ledger_sha256,p_doctrine_generation,p_program6_actions_enabled,
    projection_hash,result_ref,observed_time,session_user) returning id into result_uuid;
  bundle_projection:=jsonb_build_object('forward_fix_rehearsal_attempt_id',attempt.id,'forward_fix_result_id',result_uuid,
    'current_release_id',attempt.release_id,'git_sha',attempt.git_sha,'recovery_strategy','forward_fix',
    'recovery_plan_ref',attempt.recovery_plan_ref,'plan_hash',attempt.plan_hash,
    'bounded_contract_sha256',bounded.contract_sha256,'target_schema_ledger_sha256',bounded.target_schema_ledger_sha256);
  bundle_hash:='sha256:'||encode(public.digest(bundle_projection::text,'sha256'),'hex');
  bundle_ref:='ops.staging-recovery-bundle:'||bundle_hash;
  insert into ops.staging_recovery_rehearsal_bundle(
    recovery_attempt_id,correlation_id,current_release_id,prior_release_id,service_id,environment,
    current_before_receipt_id,prior_after_rollback_receipt_id,current_after_restore_receipt_id,
    forward_fix_result_id,candidate_git_sha,candidate_provider_version_id,recovery_strategy,recovery_plan_ref,
    plan_hash,declared_migration_set_sha256,declared_migration_count,declared_schema_highest_migration,
    declared_schema_applied_count,declared_schema_ledger_sha256,bundle_sha256,evidence_ref,completed_at,
    writer_session_user,bounded_forward_fix_contract_id)
  values(attempt.id,attempt.correlation_id,attempt.release_id,null,attempt.service_id,'staging',null,null,null,
    result_uuid,attempt.git_sha,attempt.candidate_provider_version_id,'forward_fix',attempt.recovery_plan_ref,
    attempt.plan_hash,attempt.declared_migration_set_sha256,attempt.declared_migration_count,
    attempt.declared_schema_highest_migration,attempt.declared_schema_applied_count,
    attempt.declared_schema_ledger_sha256,bundle_hash,bundle_ref,observed_time,session_user,bounded.id)
  returning id into bundle_uuid;
  insert into ops.run(correlation_id,kind,service_id,environment,run_key,state,started_at,ended_at,source_kind,
    source_ref,observed_at,evidence_ref,release_id,recovery_strategy,recovery_plan_ref,recovery_rehearsal_bundle_id)
  values(attempt.correlation_id,'check',attempt.service_id,'staging','recovery.rehearsal.forward-fix.bounded','succeeded',
    observed_time,observed_time,'wrapper','ops.record_staging_bounded_forward_fix_rehearsal',observed_time,bundle_ref,
    attempt.release_id,'forward_fix',attempt.recovery_plan_ref,bundle_uuid) returning id into run_uuid;
  return jsonb_build_object('forward_fix_rehearsal_result_id',result_uuid,'result_ref',result_ref,
    'bundle_id',bundle_uuid,'recovery_run_id',run_uuid,'replayed',false);
end $$;
revoke all on function ops.record_staging_bounded_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.record_staging_bounded_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean)
  to carr_program5_forward_fix_verifiers;

create or replace function ops.program5_exact_recovery_rehearsal(p_release_id uuid, p_not_before timestamptz default null)
returns uuid language sql stable security definer set search_path=ops,public,pg_temp as $$
  select r.id
    from ops.release rel join ops.run r on r.release_id=rel.id and r.service_id=rel.service_id
    join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id
    left join ops.staging_forward_fix_bounded_contract c on c.id=b.bounded_forward_fix_contract_id
    left join ops.staging_forward_fix_rehearsal_result x on x.id=b.forward_fix_result_id
   where rel.id=$1 and r.environment='staging' and r.state='succeeded' and r.evidence_ref=b.evidence_ref
     and b.current_release_id=rel.id and b.service_id=rel.service_id and b.recovery_strategy=rel.recovery_strategy
     and b.recovery_plan_ref=rel.rollback_plan_ref and b.plan_hash=rel.plan_hash
     and b.declared_migration_set_sha256=ops.program5_migration_set_sha256(rel.migration_set)
     and b.declared_migration_count=cardinality(rel.migration_set)
     and b.declared_schema_highest_migration=rel.schema_highest_migration
     and b.declared_schema_applied_count=rel.schema_applied_count
     and b.declared_schema_ledger_sha256=rel.schema_ledger_sha256
     and (rel.recovery_strategy='rollback' or (
          b.candidate_git_sha=rel.git_sha and b.candidate_provider_version_id::text=rel.provider_version_id
          and c.source_artifact_digest=rel.artifact_digest
          and c.source_schema_highest_migration=rel.schema_highest_migration
          and c.source_schema_applied_count=rel.schema_applied_count
          and c.source_schema_ledger_sha256=rel.schema_ledger_sha256
          and x.schema_highest_migration=c.target_schema_highest_migration
          and x.schema_applied_count=c.target_schema_applied_count
          and x.schema_ledger_sha256=c.target_schema_ledger_sha256))
     and (p_not_before is null or b.completed_at>=p_not_before)
   order by b.completed_at desc limit 1
$$;

-- This replacement deliberately derives every target-prefix fact after 0315a
-- has committed into schema_migrations. carr_jobs supplies only the two future
-- suffix rows; their hash must complete the immutable full release ledger.
create or replace function ops.prepare_staging_forward_fix_bounded_contract(
  p_idempotency_key uuid, p_held_back_migrations text[], p_held_back_sha256 text[]
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare a ops.staging_forward_fix_rehearsal_attempt%rowtype; rel ops.release%rowtype;
  c ops.staging_forward_fix_bounded_contract%rowtype; prefix_count integer; prefix_highest text;
  prefix_digest text; selected_digest text; held_digest text; full_digest text; material bytea; suffix bytea;
  contract jsonb; contract_digest text; created uuid;
begin
  if session_user<>'carr_jobs' then raise exception 'bounded forward-fix contract writer requires carr_jobs'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,315));
  select * into a from ops.staging_forward_fix_rehearsal_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'bounded forward-fix contract requires a prepared rehearsal'; end if;
  if exists(select 1 from ops.staging_forward_fix_rehearsal_claim where rehearsal_attempt_id=a.id) then
    raise exception 'bounded forward-fix contract must bind before provider mutation claim';
  end if;
  select * into rel from ops.release where id=a.release_id;
  if not found or rel.artifact_digest !~ '^sha256:[0-9a-f]{64}$'
     or rel.schema_highest_migration<>'0317_atomic_rule_delivery_cutover.sql'
     or rel.schema_ledger_sha256 !~ '^sha256:[0-9a-f]{64}$'
     or a.git_sha<>rel.git_sha or a.candidate_provider_version_id::text<>rel.provider_version_id then
    raise exception 'bounded forward-fix release is not the exact immutable full-source candidate';
  end if;
  if p_held_back_migrations is distinct from array['0316_rule_delivery_audit_counts.sql','0317_atomic_rule_delivery_cutover.sql']
     or cardinality(p_held_back_sha256)<>2
     or exists(select 1 from unnest(p_held_back_sha256) h where h !~ '^[0-9a-f]{64}$') then
    raise exception 'bounded forward-fix held-back suffix is not exact';
  end if;
  select count(*)::integer,max(filename collate "C"),
    'sha256:'||encode(public.digest(coalesce(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea order by filename collate "C"),''::bytea),'sha256'),'hex')
    into prefix_count,prefix_highest,prefix_digest from public.schema_migrations;
  if prefix_count<>rel.schema_applied_count-2 or prefix_highest<>'0315a_program5_bounded_forward_fix_rehearsal.sql'
     or exists(select 1 from public.schema_migrations where filename in ('0316_rule_delivery_audit_counts.sql','0317_atomic_rule_delivery_cutover.sql')) then
    raise exception 'staging schema is not the clean exact prefix through 0315a';
  end if;
  select 'sha256:'||encode(public.digest(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea order by filename collate "C"),'sha256'),'hex')
    into selected_digest from public.schema_migrations
   where filename in ('0315_program5_forward_fix_rehearsal.sql','0315a_program5_bounded_forward_fix_rehearsal.sql');
  if selected_digest is null or (select count(*) from public.schema_migrations where filename in ('0315_program5_forward_fix_rehearsal.sql','0315a_program5_bounded_forward_fix_rehearsal.sql'))<>2 then
    raise exception 'staging schema does not contain the exact selected 0315/0315a pair';
  end if;
  select 'sha256:'||encode(public.digest(string_agg(convert_to(x.n,'UTF8')||decode('00','hex')||convert_to(x.h,'UTF8')||decode('0a','hex'),''::bytea order by x.n collate "C"),'sha256'),'hex'),
         coalesce(string_agg(convert_to(x.n,'UTF8')||decode('00','hex')||convert_to(x.h,'UTF8')||decode('0a','hex'),''::bytea order by x.n collate "C"),''::bytea)
    into held_digest,suffix from unnest(p_held_back_migrations,p_held_back_sha256) as x(n,h);
  select coalesce(string_agg(convert_to(filename,'UTF8')||decode('00','hex')||convert_to(sha256,'UTF8')||decode('0a','hex'),''::bytea order by filename collate "C"),''::bytea)
    into material from public.schema_migrations;
  full_digest:='sha256:'||encode(public.digest(material||suffix,'sha256'),'hex');
  if full_digest<>rel.schema_ledger_sha256 then
    raise exception 'held-back suffix contents do not complete the immutable full-source ledger';
  end if;
  -- Canonical byte preimage shared with tools/release-manifest.py:
  -- fixed fields, UTF-8, exactly one LF delimiter, no JSON presentation.
  contract_digest:='sha256:'||encode(public.digest(convert_to(concat_ws(E'\n',
    'program5-forward-fix-staging-prefix','staging','false',rel.git_sha,rel.artifact_digest,
    rel.provider_version_id,rel.schema_ledger_sha256,prefix_highest,prefix_count::text,prefix_digest,
    '0315_program5_forward_fix_rehearsal.sql,0315a_program5_bounded_forward_fix_rehearsal.sql',
    (prefix_count-1)::text||','||prefix_count::text,selected_digest,
    array_to_string(p_held_back_migrations,','),
    (prefix_count+1)::text||','||(prefix_count+2)::text,held_digest),'UTF8'),'sha256'),'hex');
  select * into c from ops.staging_forward_fix_bounded_contract where rehearsal_attempt_id=a.id;
  if found then
    if c.contract_sha256<>contract_digest then raise exception 'bounded forward-fix contract replay differs'; end if;
    return jsonb_build_object('bounded_forward_fix_contract_id',c.id,'contract_sha256',c.contract_sha256,'replayed',true);
  end if;
  insert into ops.staging_forward_fix_bounded_contract(rehearsal_attempt_id,contract_sha256,source_artifact_digest,
    source_schema_highest_migration,source_schema_applied_count,source_schema_ledger_sha256,target_schema_highest_migration,
    target_schema_applied_count,target_schema_ledger_sha256,selected_migrations,selected_ordinals,selected_migrations_sha256,
    held_back_migrations,held_back_ordinals,held_back_migrations_sha256,writer_session_user)
  values(a.id,contract_digest,rel.artifact_digest,rel.schema_highest_migration,rel.schema_applied_count,rel.schema_ledger_sha256,
    prefix_highest,prefix_count,prefix_digest,array['0315_program5_forward_fix_rehearsal.sql','0315a_program5_bounded_forward_fix_rehearsal.sql'],array[prefix_count-1,prefix_count],selected_digest,
    p_held_back_migrations,array[prefix_count+1,prefix_count+2],held_digest,session_user) returning id into created;
  return jsonb_build_object('bounded_forward_fix_contract_id',created,'contract_sha256',contract_digest,'replayed',false);
end $$;

-- Bounded receipts are useful staging evidence only. They are intentionally
-- excluded from the Production approval predicate below.
create or replace function ops.program5_exact_recovery_rehearsal(p_release_id uuid, p_not_before timestamptz default null)
returns uuid language sql stable security definer set search_path=ops,public,pg_temp as $$
  select r.id from ops.release rel join ops.run r on r.release_id=rel.id and r.service_id=rel.service_id
  join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id
  where rel.id=$1 and r.environment='staging' and r.state='succeeded' and r.evidence_ref=b.evidence_ref
    and b.bounded_forward_fix_contract_id is null
    and r.run_key=case when rel.recovery_strategy='rollback' then 'recovery.rehearsal.worker' else 'recovery.rehearsal.forward-fix' end
    and b.current_release_id=rel.id and b.service_id=rel.service_id and b.recovery_strategy=rel.recovery_strategy
    and b.recovery_plan_ref=rel.rollback_plan_ref and b.plan_hash=rel.plan_hash
    and b.declared_migration_set_sha256=ops.program5_migration_set_sha256(rel.migration_set)
    and b.declared_migration_count=cardinality(rel.migration_set)
    and b.declared_schema_highest_migration=rel.schema_highest_migration
    and b.declared_schema_applied_count=rel.schema_applied_count
    and b.declared_schema_ledger_sha256=rel.schema_ledger_sha256
    and (rel.recovery_strategy='rollback' or (b.candidate_git_sha=rel.git_sha
         and b.candidate_provider_version_id::text=rel.provider_version_id
         and exists(select 1 from ops.staging_forward_fix_rehearsal_result x where x.id=b.forward_fix_result_id)))
    and (p_not_before is null or b.completed_at>=p_not_before) order by b.completed_at desc limit 1
$$;

create or replace function ops.program5_bounded_staging_forward_fix_rehearsal(p_release_id uuid)
returns uuid language sql stable security definer set search_path=ops,public,pg_temp as $$
  select r.id from ops.run r join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id
  join ops.staging_forward_fix_bounded_contract c on c.id=b.bounded_forward_fix_contract_id
  where r.release_id=$1 and r.environment='staging' and r.state='succeeded'
    and r.run_key='recovery.rehearsal.forward-fix.bounded' and r.evidence_ref=b.evidence_ref
  order by b.completed_at desc limit 1
$$;
revoke all on function ops.program5_bounded_staging_forward_fix_rehearsal(uuid) from public,carr_jobs,carr_reader,carr_writer,carr_authority;

-- The DB-derived three-argument routine is the sole pre-mutation door.
revoke all on function ops.prepare_staging_forward_fix_bounded_contract(uuid,text[],text[])
  from public,carr_reader,carr_writer,carr_authority,carr_program5_forward_fix_verifiers;
grant execute on function ops.prepare_staging_forward_fix_bounded_contract(uuid,text[],text[])
  to carr_jobs;

create or replace function ops.validate_recovery_rehearsal_run()
returns trigger language plpgsql as $$
declare b ops.staging_recovery_rehearsal_bundle%rowtype; expected_started timestamptz; expected_key text;
begin
  if new.run_key like 'recovery.rehearsal.%' and new.state='succeeded'
     and new.recovery_rehearsal_bundle_id is null then
    raise exception 'successful recovery rehearsal run requires a typed bundle';
  end if;
  if new.recovery_rehearsal_bundle_id is null then return new; end if;
  select * into strict b from ops.staging_recovery_rehearsal_bundle where id=new.recovery_rehearsal_bundle_id;
  expected_started:=case when b.recovery_strategy='rollback' then
    (select observed_at from ops.staging_release_readback_receipt where id=b.current_before_receipt_id)
    else (select observed_at from ops.staging_forward_fix_rehearsal_result where id=b.forward_fix_result_id) end;
  expected_key:=case when b.recovery_strategy='rollback' then 'recovery.rehearsal.worker'
    when b.bounded_forward_fix_contract_id is not null then 'recovery.rehearsal.forward-fix.bounded'
    else 'recovery.rehearsal.forward-fix' end;
  if new.kind<>'check' or new.run_key<>expected_key or new.state<>'succeeded'
     or new.environment<>'staging' or new.release_id<>b.current_release_id
     or new.service_id<>b.service_id or new.correlation_id<>b.correlation_id
     or new.recovery_strategy<>b.recovery_strategy or new.recovery_plan_ref<>b.recovery_plan_ref
     or new.evidence_ref<>b.evidence_ref or new.started_at is distinct from expected_started
     or new.ended_at is distinct from b.completed_at then
    raise exception 'recovery rehearsal run does not exactly match its typed bundle';
  end if;
  return new;
end $$;

commit;
