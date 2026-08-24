-- 0295_staging_restore_only_recovery.sql
--
-- A failed recovery leg can leave staging in an unknown state.  Restoring the
-- current candidate is safety work, not evidence that the required
-- current_before -> prior -> current_after rehearsal completed.  These rows
-- deliberately live outside staging_deployment_attempt and
-- staging_release_readback_receipt so no query that builds or approves a
-- recovery bundle can accidentally count a repair as the third observation.

begin;

create table ops.staging_restore_only_attempt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  recovery_attempt_id uuid not null,
  correlation_id uuid not null,
  rehearsal_release_id uuid not null references ops.release(id) on delete restrict,
  prior_release_id uuid not null references ops.release(id) on delete restrict,
  service_id uuid not null references ops.service(id) on delete restrict,
  environment text not null check (environment='staging'),
  git_sha text not null check (git_sha ~ '^[0-9a-f]{40}$'),
  provider text not null check (provider='cloudflare-workers'),
  -- This is the candidate's immutable Production binding.  The separate
  -- staging provider version appears only in the result readback.
  target_provider_version_id uuid not null,
  recovery_strategy text not null check (recovery_strategy='rollback'),
  rollback_plan_ref text not null check (length(rollback_plan_ref)>0),
  plan_hash text not null check (length(plan_hash)>0),
  expected_provider_tag text not null unique
    check (expected_provider_tag ~ '^carr-staging-[0-9a-f]{32}$'),
  declared_migration_set_sha256 text not null
    check (declared_migration_set_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  declared_migration_count integer not null check (declared_migration_count>0),
  declared_schema_highest_migration text not null
    check (declared_schema_highest_migration ~ '^[0-9]{4}_[a-z0-9_.-]+\\.sql$'),
  declared_schema_applied_count integer not null check (declared_schema_applied_count>0),
  declared_schema_ledger_sha256 text not null
    check (declared_schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  writer_session_user text not null default session_user
    check (writer_session_user='carr_jobs'),
  created_at timestamptz not null default clock_timestamp(),
  check (correlation_id=recovery_attempt_id)
);

create table ops.staging_restore_only_claim (
  restore_attempt_id uuid primary key
    references ops.staging_restore_only_attempt(id) on delete restrict,
  writer_session_user text not null default session_user
    check (writer_session_user='carr_jobs'),
  claimed_at timestamptz not null default clock_timestamp()
);

create table ops.staging_restore_only_result (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  restore_attempt_id uuid not null unique
    references ops.staging_restore_only_attempt(id) on delete restrict,
  status text not null check (status in ('succeeded','failed','unknown')),
  provider_version_id uuid,
  provider_tag text,
  verb_count integer,
  schema_highest_migration text,
  schema_applied_count integer,
  doctrine_generation bigint,
  program6_actions_enabled boolean,
  reason text,
  result_sha256 text not null check (result_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique,
  observed_at timestamptz not null default clock_timestamp(),
  writer_session_user text not null default session_user
    check (writer_session_user='carr_jobs'),
  check (
    (status='succeeded' and provider_version_id is not null and provider_tag is not null
      and verb_count is not null and schema_highest_migration is not null
      and schema_applied_count is not null and doctrine_generation is not null
      and program6_actions_enabled is not null and reason is null)
    or
    (status in ('failed','unknown') and provider_version_id is null and provider_tag is null
      and verb_count is null and schema_highest_migration is null
      and schema_applied_count is null and doctrine_generation is null
      and program6_actions_enabled is null and reason is not null and length(reason) between 1 and 240
      and reason ~ '^[a-z0-9_.:-]+$')
  )
);

create trigger staging_restore_only_attempt_append_only
before update or delete on ops.staging_restore_only_attempt
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger staging_restore_only_claim_append_only
before update or delete on ops.staging_restore_only_claim
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger staging_restore_only_result_append_only
before update or delete on ops.staging_restore_only_result
for each row execute function ops.refuse_program5_evidence_mutation();

create or replace function ops.prepare_staging_restore_only_attempt(
  p_idempotency_key uuid, p_correlation_id uuid, p_release_key text,
  p_prior_release_key text, p_recovery_attempt_id uuid, p_git_sha text
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  existing ops.staging_restore_only_attempt%rowtype;
  current_release ops.release%rowtype;
  prior_release ops.release%rowtype;
  migration_hash text;
  migration_count integer;
  attempt_uuid uuid;
  expected_tag text;
  existing_result ops.staging_restore_only_result%rowtype;
begin
  if session_user<>'carr_jobs' then raise exception 'restore-only writer requires the carr_jobs session'; end if;
  if p_idempotency_key is null or p_correlation_id is null or p_recovery_attempt_id is null
     or coalesce(p_release_key,'')='' or coalesce(p_prior_release_key,'')=''
     or coalesce(p_git_sha,'') !~ '^[0-9a-f]{40}$' or p_correlation_id<>p_recovery_attempt_id then
    raise exception 'invalid typed restore-only attempt input';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  perform pg_advisory_xact_lock(hashtextextended(p_recovery_attempt_id::text,202));
  select * into current_release from ops.release where release_key=p_release_key;
  if not found or current_release.environment<>'production' or current_release.state<>'candidate'
     or current_release.recovery_strategy<>'rollback' or coalesce(current_release.rollback_plan_ref,'')=''
     or coalesce(current_release.plan_hash,'')='' or current_release.service_id is null
     or current_release.provider<>'cloudflare-workers' or current_release.provider_version_id is null then
    raise exception 'restore-only target is not an exact rollback-ready Production candidate';
  end if;
  select * into prior_release from ops.release where release_key=p_prior_release_key;
  if not found or prior_release.id=current_release.id or prior_release.environment<>'production'
     or prior_release.state<>'complete' or prior_release.service_id<>current_release.service_id then
    raise exception 'restore-only prior release is not a distinct completed Production release';
  end if;
  if not exists (select 1 from ops.deployment d where d.release_id=prior_release.id
      and d.service_id=current_release.service_id and d.environment='production'
      and d.state='complete' and d.read_back_at is not null and d.git_sha=prior_release.git_sha
      and d.provider=prior_release.provider and d.provider_version_id=prior_release.provider_version_id) then
    raise exception 'restore-only prior release has no exact completed Production readback';
  end if;
  if p_git_sha<>current_release.git_sha then raise exception 'restore-only SHA does not match the current target'; end if;
  if coalesce(cardinality(current_release.migration_set),0)<=0
     or coalesce(current_release.schema_highest_migration,'') !~ '^[0-9]{4}_[a-z0-9_.-]+\\.sql$'
     or coalesce(current_release.schema_applied_count,0)<=0
     or coalesce(current_release.schema_ledger_sha256,'') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'restore-only target does not declare an exact migration/schema set';
  end if;
  migration_hash:=ops.program5_migration_set_sha256(current_release.migration_set);
  migration_count:=cardinality(current_release.migration_set);
  expected_tag:='carr-staging-'||replace(p_idempotency_key::text,'-','');
  select * into existing from ops.staging_restore_only_attempt where idempotency_key=p_idempotency_key;
  if found then
    if (existing.correlation_id,existing.recovery_attempt_id,existing.rehearsal_release_id,
        existing.prior_release_id,existing.service_id,existing.git_sha,existing.target_provider_version_id,
        existing.recovery_strategy,existing.rollback_plan_ref,existing.plan_hash,existing.expected_provider_tag,
        existing.declared_migration_set_sha256,existing.declared_migration_count,
        existing.declared_schema_highest_migration,existing.declared_schema_applied_count,
        existing.declared_schema_ledger_sha256) is distinct from
       (p_correlation_id,p_recovery_attempt_id,current_release.id,prior_release.id,current_release.service_id,
        p_git_sha,current_release.provider_version_id,current_release.recovery_strategy,
        current_release.rollback_plan_ref,current_release.plan_hash,expected_tag,migration_hash,migration_count,
        current_release.schema_highest_migration,current_release.schema_applied_count,
        current_release.schema_ledger_sha256) then
      raise exception 'restore-only idempotency key was reused with changed input';
    end if;
    select * into existing_result from ops.staging_restore_only_result where restore_attempt_id=existing.id;
    return jsonb_build_object('restore_attempt_id',existing.id,'expected_provider_tag',existing.expected_provider_tag,
      'state',coalesce(existing_result.status,'prepared'),'mutation_claimed',exists(select 1 from ops.staging_restore_only_claim where restore_attempt_id=existing.id),
      'result_ref',existing_result.evidence_ref,'replayed',true);
  end if;
  insert into ops.staging_restore_only_attempt(idempotency_key,recovery_attempt_id,correlation_id,
    rehearsal_release_id,prior_release_id,service_id,environment,git_sha,provider,target_provider_version_id,
    recovery_strategy,rollback_plan_ref,plan_hash,expected_provider_tag,declared_migration_set_sha256,
    declared_migration_count,declared_schema_highest_migration,declared_schema_applied_count,
    declared_schema_ledger_sha256,writer_session_user)
  values(p_idempotency_key,p_recovery_attempt_id,p_correlation_id,current_release.id,prior_release.id,
    current_release.service_id,'staging',p_git_sha,'cloudflare-workers',current_release.provider_version_id,
    current_release.recovery_strategy,current_release.rollback_plan_ref,current_release.plan_hash,expected_tag,
    migration_hash,migration_count,current_release.schema_highest_migration,current_release.schema_applied_count,
    current_release.schema_ledger_sha256,session_user) returning id into attempt_uuid;
  return jsonb_build_object('restore_attempt_id',attempt_uuid,'expected_provider_tag',expected_tag,
    'state','prepared','mutation_claimed',false,'result_ref',null,'replayed',false);
end $$;

create or replace function ops.claim_staging_restore_only_attempt(p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare attempt ops.staging_restore_only_attempt%rowtype; inserted_count integer; result_row ops.staging_restore_only_result%rowtype;
begin
  if session_user<>'carr_jobs' then raise exception 'restore-only claim requires the carr_jobs session'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into attempt from ops.staging_restore_only_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'restore-only attempt must be prepared before claim'; end if;
  select * into result_row from ops.staging_restore_only_result where restore_attempt_id=attempt.id;
  if found then return jsonb_build_object('restore_attempt_id',attempt.id,'mutation_allowed',false,'state',result_row.status,'replayed',true); end if;
  insert into ops.staging_restore_only_claim(restore_attempt_id,writer_session_user) values(attempt.id,session_user) on conflict do nothing;
  get diagnostics inserted_count=row_count;
  return jsonb_build_object('restore_attempt_id',attempt.id,'mutation_allowed',inserted_count=1,'state',case when inserted_count=1 then 'claimed' else 'claimed_pending_result' end,'replayed',inserted_count=0);
end $$;

create or replace function ops.record_staging_restore_only_result(
  p_idempotency_key uuid, p_status text, p_provider_version_id uuid, p_provider_tag text,
  p_verb_count integer, p_schema_highest_migration text, p_schema_applied_count integer,
  p_doctrine_generation bigint, p_program6_actions_enabled boolean, p_reason text
) returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare attempt ops.staging_restore_only_attempt%rowtype; existing ops.staging_restore_only_result%rowtype;
  projection jsonb; result_hash text; result_ref text; result_uuid uuid;
begin
  if session_user<>'carr_jobs' then raise exception 'restore-only result requires the carr_jobs session'; end if;
  if p_idempotency_key is null or p_status not in ('succeeded','failed','unknown') then raise exception 'invalid restore-only result input'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into attempt from ops.staging_restore_only_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'restore-only result has no prepared attempt'; end if;
  if not exists(select 1 from ops.staging_restore_only_claim where restore_attempt_id=attempt.id) then raise exception 'restore-only result was never claimed'; end if;
  select * into existing from ops.staging_restore_only_result where restore_attempt_id=attempt.id;
  if found then
    if (existing.status,existing.provider_version_id,existing.provider_tag,existing.verb_count,
        existing.schema_highest_migration,existing.schema_applied_count,existing.doctrine_generation,
        existing.program6_actions_enabled,existing.reason) is distinct from
       (p_status,p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,
        p_schema_applied_count,p_doctrine_generation,p_program6_actions_enabled,p_reason) then
      raise exception 'restore-only result idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('restore_result_id',existing.id,'result_ref',existing.evidence_ref,'status',existing.status,'replayed',true);
  end if;
  if p_status='succeeded' then
    if p_provider_version_id is null or p_provider_tag<>attempt.expected_provider_tag or coalesce(p_verb_count,0)<=0
       or coalesce(p_schema_highest_migration,'')<>attempt.declared_schema_highest_migration
       or p_schema_applied_count<>attempt.declared_schema_applied_count or p_doctrine_generation is null
       or p_program6_actions_enabled is null or p_reason is not null then
      raise exception 'restore-only success lacks exact current-target readback';
    end if;
  elsif p_provider_version_id is not null or p_provider_tag is not null or p_verb_count is not null
     or p_schema_highest_migration is not null or p_schema_applied_count is not null
     or p_doctrine_generation is not null or p_program6_actions_enabled is not null
     or coalesce(p_reason,'') !~ '^[a-z0-9_.:-]{1,240}$' then
    raise exception 'restore-only non-success requires only a bounded reason';
  end if;
  projection:=jsonb_build_object('restore_attempt_id',attempt.id,'recovery_attempt_id',attempt.recovery_attempt_id,
    'target_release_id',attempt.rehearsal_release_id,'prior_release_id',attempt.prior_release_id,
    'target_provider_version_id',attempt.target_provider_version_id,'environment','staging','status',p_status,
    'provider_version_id',p_provider_version_id,'provider_tag',p_provider_tag,'verb_count',p_verb_count,
    'schema_highest_migration',p_schema_highest_migration,'schema_applied_count',p_schema_applied_count,
    'doctrine_generation',p_doctrine_generation,'program6_actions_enabled',p_program6_actions_enabled,'reason',p_reason);
  result_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  result_ref:='ops.staging-restore-only:'||result_hash;
  insert into ops.staging_restore_only_result(idempotency_key,restore_attempt_id,status,provider_version_id,
    provider_tag,verb_count,schema_highest_migration,schema_applied_count,doctrine_generation,
    program6_actions_enabled,reason,result_sha256,evidence_ref,writer_session_user)
  values(p_idempotency_key,attempt.id,p_status,p_provider_version_id,p_provider_tag,p_verb_count,
    p_schema_highest_migration,p_schema_applied_count,p_doctrine_generation,p_program6_actions_enabled,
    p_reason,result_hash,result_ref,session_user) returning id into result_uuid;
  return jsonb_build_object('restore_result_id',result_uuid,'result_ref',result_ref,'status',p_status,'replayed',false);
end $$;

revoke all on ops.staging_restore_only_attempt,ops.staging_restore_only_claim,ops.staging_restore_only_result from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant select on ops.staging_restore_only_attempt,ops.staging_restore_only_claim,ops.staging_restore_only_result to carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.prepare_staging_restore_only_attempt(uuid,uuid,text,text,uuid,text) from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.prepare_staging_restore_only_attempt(uuid,uuid,text,text,uuid,text) to carr_jobs;
revoke all on function ops.claim_staging_restore_only_attempt(uuid) from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.claim_staging_restore_only_attempt(uuid) to carr_jobs;
revoke all on function ops.record_staging_restore_only_result(uuid,text,uuid,text,integer,text,integer,bigint,boolean,text) from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.record_staging_restore_only_result(uuid,text,uuid,text,integer,text,integer,bigint,boolean,text) to carr_jobs;

do $$ begin
  if has_table_privilege('carr_jobs','ops.staging_restore_only_attempt','insert')
     or has_table_privilege('carr_jobs','ops.staging_restore_only_result','insert') then
    raise exception '0295 FAILED: restore-only evidence has direct runtime DML';
  end if;
  if not has_function_privilege('carr_jobs','ops.prepare_staging_restore_only_attempt(uuid,uuid,text,text,uuid,text)'::regprocedure,'execute')
     or not has_function_privilege('carr_jobs','ops.claim_staging_restore_only_attempt(uuid)'::regprocedure,'execute')
     or not has_function_privilege('carr_jobs','ops.record_staging_restore_only_result(uuid,text,uuid,text,integer,text,integer,bigint,boolean,text)'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.record_staging_restore_only_result(uuid,text,uuid,text,integer,text,integer,bigint,boolean,text)'::regprocedure,'execute') then
    raise exception '0295 FAILED: restore-only writer boundary is wrong';
  end if;
end $$;

commit;
