-- 0297_restore_only_provider_uuid_repair.sql
--
-- 0295/0296 stored release.provider_version_id as text while the append-only
-- restore-only attempt binds a UUID column. Keep applied migrations intact;
-- replace only the typed prepare writer after first proving the stored value
-- has the exact canonical UUID shape.

begin;

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
  target_provider_version uuid;
  expected_tag text;
  existing_result ops.staging_restore_only_result%rowtype;
begin
  if session_user<>'carr_jobs' then raise exception 'restore-only writer requires the carr_jobs session'; end if;
  if p_idempotency_key is null or p_correlation_id is null or p_recovery_attempt_id is null
     or coalesce(p_release_key,'')='' or coalesce(p_prior_release_key,'' )=''
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
  if current_release.provider_version_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    raise exception 'restore-only target provider version is not a canonical UUID';
  end if;
  target_provider_version:=current_release.provider_version_id::uuid;
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
     or coalesce(current_release.schema_highest_migration,'') !~ '^[0-9]{4}_[a-z0-9_.-]+\.sql$'
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
        p_git_sha,target_provider_version,current_release.recovery_strategy,
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
    current_release.service_id,'staging',p_git_sha,'cloudflare-workers',target_provider_version,
    current_release.recovery_strategy,current_release.rollback_plan_ref,current_release.plan_hash,expected_tag,
    migration_hash,migration_count,current_release.schema_highest_migration,current_release.schema_applied_count,
    current_release.schema_ledger_sha256,session_user) returning id into attempt_uuid;
  return jsonb_build_object('restore_attempt_id',attempt_uuid,'expected_provider_tag',expected_tag,
    'state','prepared','mutation_claimed',false,'result_ref',null,'replayed',false);
end $$;

commit;
