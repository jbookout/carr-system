-- Bind the effective Program 6 action switch into every new typed staging
-- readback.  Existing Program 5 evidence is append-only and deliberately
-- retains NULL: its historical projection hash did not contain this field.

begin;

alter table ops.staging_release_readback_receipt
  add column program6_actions_enabled boolean;

comment on column ops.staging_release_readback_receipt.program6_actions_enabled is
  'Effective Program 6 action boolean read from /release for post-0218 receipts; NULL is legacy immutable evidence.';

-- The old seven-argument recorder could create a receipt without this
-- deployment-bound fact.  Retire that write door, while leaving all rows it
-- wrote untouched and readable.
drop function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint);

create function ops.record_staging_release_readback(
  p_idempotency_key uuid, p_provider_version_id uuid, p_provider_tag text,
  p_verb_count integer, p_schema_highest_migration text,
  p_schema_applied_count integer, p_doctrine_generation bigint,
  p_program6_actions_enabled boolean
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  existing ops.staging_release_readback_receipt%rowtype;
  attempt ops.staging_deployment_attempt%rowtype;
  current_release ops.release%rowtype;
  prior_release ops.release%rowtype;
  observed_release ops.release%rowtype;
  service_uuid uuid; deployment_uuid uuid; receipt_uuid uuid;
  before_receipt ops.staging_release_readback_receipt%rowtype;
  prior_receipt ops.staging_release_readback_receipt%rowtype;
  after_receipt ops.staging_release_readback_receipt%rowtype;
  bundle_uuid uuid; run_uuid uuid; projection jsonb; projection_hash text;
  receipt_ref text; bundle_projection jsonb; bundle_hash text; bundle_ref text;
  observed_time timestamptz := clock_timestamp();
begin
  if session_user <> 'carr_jobs' then raise exception 'staging readback writer requires the carr_jobs session'; end if;
  if p_idempotency_key is null or p_provider_version_id is null
     or coalesce(p_provider_tag,'') !~ '^carr-staging-[a-z0-9-]{8,50}$'
     or coalesce(p_verb_count,0)<=0
     or coalesce(p_schema_highest_migration,'') !~ '^[0-9]{4}_[a-z0-9_.-]+\.sql$'
     or coalesce(p_schema_applied_count,0)<=0 or coalesce(p_doctrine_generation,-1)<0
     or p_program6_actions_enabled is null then
    raise exception 'invalid typed staging readback input';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into attempt from ops.staging_deployment_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'staging readback has no prepared deployment attempt'; end if;
  if not exists(select 1 from ops.staging_deployment_claim where deployment_attempt_id=attempt.id) then
    raise exception 'staging readback deployment attempt was never claimed'; end if;
  if attempt.recovery_attempt_id is not null then perform pg_advisory_xact_lock(hashtextextended(attempt.recovery_attempt_id::text,202)); end if;
  select * into existing from ops.staging_release_readback_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.deployment_attempt_id<>attempt.id
       or (existing.git_sha,existing.provider_version_id,existing.provider_tag,existing.verb_count,
           existing.schema_highest_migration,existing.schema_applied_count,existing.doctrine_generation) is distinct from
          (attempt.git_sha,p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,
           p_schema_applied_count,p_doctrine_generation)
       or (existing.program6_actions_enabled is not null
           and existing.program6_actions_enabled is distinct from p_program6_actions_enabled) then
      raise exception 'staging readback idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('receipt_id',existing.id,'receipt_ref',existing.evidence_ref,'replayed',true,
      'bundle_id',(select id from ops.staging_recovery_rehearsal_bundle where recovery_attempt_id=attempt.recovery_attempt_id),
      'recovery_run_id',(select r.id from ops.run r join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id where b.recovery_attempt_id=attempt.recovery_attempt_id));
  end if;
  select * into strict current_release from ops.release where id=attempt.rehearsal_release_id;
  if attempt.declared_migration_set_sha256<>ops.program5_migration_set_sha256(current_release.migration_set)
     or attempt.declared_migration_count<>cardinality(current_release.migration_set)
     or attempt.declared_schema_highest_migration<>current_release.schema_highest_migration
     or attempt.declared_schema_applied_count<>current_release.schema_applied_count
     or attempt.declared_schema_ledger_sha256<>current_release.schema_ledger_sha256
     or p_schema_highest_migration<>attempt.declared_schema_highest_migration
     or p_schema_applied_count<>attempt.declared_schema_applied_count then
    raise exception 'staging readback schema does not match the exact declared candidate migration set'; end if;
  if p_provider_tag<>attempt.expected_provider_tag then raise exception 'staging readback provider tag does not match its prepared attempt'; end if;
  service_uuid:=current_release.service_id;
  select * into strict observed_release from ops.release where id=attempt.observed_release_id;
  if attempt.prior_release_id is not null then select * into strict prior_release from ops.release where id=attempt.prior_release_id; end if;
  projection:=jsonb_build_object(
    'deployment_attempt_id',attempt.id,'correlation_id',attempt.correlation_id,'recovery_attempt_id',attempt.recovery_attempt_id,
    'recovery_step',attempt.recovery_step,'rehearsal_release_id',current_release.id,'observed_release_id',observed_release.id,
    'prior_release_id',attempt.prior_release_id,'service_id',service_uuid,'environment','staging','git_sha',attempt.git_sha,
    'provider','cloudflare-workers','provider_version_id',p_provider_version_id,'provider_tag',p_provider_tag,
    'verb_count',p_verb_count,'schema_highest_migration',p_schema_highest_migration,'schema_applied_count',p_schema_applied_count,
    'declared_migration_set_sha256',attempt.declared_migration_set_sha256,'declared_migration_count',attempt.declared_migration_count,
    'declared_schema_applied_count',attempt.declared_schema_applied_count,'declared_schema_ledger_sha256',attempt.declared_schema_ledger_sha256,
    'doctrine_generation',p_doctrine_generation,'program6_actions_enabled',p_program6_actions_enabled);
  projection_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex'); receipt_ref:='ops.staging-release-readback:'||projection_hash;
  insert into ops.deployment(correlation_id,service_id,environment,state,git_sha,provider,provider_version_id,release_id,deployed_by_actor,verb_count,schema_highest_migration,doctrine_generation,started_at,ended_at,read_back_at,verification_evidence_ref,source_kind,source_ref,observed_at)
  values(attempt.correlation_id,service_uuid,'staging','complete',attempt.git_sha,'cloudflare-workers',p_provider_version_id::text,observed_release.id,session_user,p_verb_count,p_schema_highest_migration,p_doctrine_generation,observed_time,observed_time,observed_time,receipt_ref,'wrapper','bin/deploy-worker.sh',observed_time) returning id into deployment_uuid;
  insert into ops.staging_release_readback_receipt(idempotency_key,deployment_attempt_id,recovery_attempt_id,recovery_step,correlation_id,deployment_id,rehearsal_release_id,observed_release_id,prior_release_id,service_id,environment,git_sha,provider,provider_version_id,provider_tag,verb_count,schema_highest_migration,schema_applied_count,declared_migration_set_sha256,declared_migration_count,declared_schema_applied_count,declared_schema_ledger_sha256,doctrine_generation,program6_actions_enabled,projection_sha256,evidence_ref,observed_at,writer_session_user)
  values(p_idempotency_key,attempt.id,attempt.recovery_attempt_id,attempt.recovery_step,attempt.correlation_id,deployment_uuid,current_release.id,observed_release.id,attempt.prior_release_id,service_uuid,'staging',attempt.git_sha,'cloudflare-workers',p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,p_schema_applied_count,attempt.declared_migration_set_sha256,attempt.declared_migration_count,attempt.declared_schema_applied_count,attempt.declared_schema_ledger_sha256,p_doctrine_generation,p_program6_actions_enabled,projection_hash,receipt_ref,observed_time,session_user) returning id into receipt_uuid;
  if attempt.recovery_step='current_after' then
    select * into before_receipt from ops.staging_release_readback_receipt where recovery_attempt_id=attempt.recovery_attempt_id and recovery_step='current_before'; if not found then raise exception 'current_after requires current_before receipt'; end if;
    select * into prior_receipt from ops.staging_release_readback_receipt where recovery_attempt_id=attempt.recovery_attempt_id and recovery_step='prior'; if not found then raise exception 'current_after requires prior receipt'; end if;
    select * into strict after_receipt from ops.staging_release_readback_receipt where id=receipt_uuid;
    if before_receipt.rehearsal_release_id<>current_release.id or prior_receipt.rehearsal_release_id<>current_release.id
       or before_receipt.prior_release_id<>prior_release.id or prior_receipt.prior_release_id<>prior_release.id
       or before_receipt.observed_release_id<>current_release.id or prior_receipt.observed_release_id<>prior_release.id or after_receipt.observed_release_id<>current_release.id
       or before_receipt.service_id<>service_uuid or prior_receipt.service_id<>service_uuid
       or before_receipt.declared_migration_set_sha256<>attempt.declared_migration_set_sha256 or prior_receipt.declared_migration_set_sha256<>attempt.declared_migration_set_sha256 or after_receipt.declared_migration_set_sha256<>attempt.declared_migration_set_sha256
       or before_receipt.declared_migration_count<>attempt.declared_migration_count or prior_receipt.declared_migration_count<>attempt.declared_migration_count or after_receipt.declared_migration_count<>attempt.declared_migration_count
       or before_receipt.schema_highest_migration<>attempt.declared_schema_highest_migration or prior_receipt.schema_highest_migration<>attempt.declared_schema_highest_migration or after_receipt.schema_highest_migration<>attempt.declared_schema_highest_migration
       or before_receipt.schema_applied_count<>attempt.declared_schema_applied_count or prior_receipt.schema_applied_count<>attempt.declared_schema_applied_count or after_receipt.schema_applied_count<>attempt.declared_schema_applied_count
       or before_receipt.declared_schema_ledger_sha256<>attempt.declared_schema_ledger_sha256 or prior_receipt.declared_schema_ledger_sha256<>attempt.declared_schema_ledger_sha256 or after_receipt.declared_schema_ledger_sha256<>attempt.declared_schema_ledger_sha256
       or not(before_receipt.observed_at<prior_receipt.observed_at and prior_receipt.observed_at<after_receipt.observed_at) or after_receipt.observed_at-before_receipt.observed_at>interval '1 hour' then
      raise exception 'recovery receipts do not form an ordered current-prior-current chain'; end if;
    bundle_projection:=jsonb_build_object('recovery_attempt_id',attempt.recovery_attempt_id,'current_release_id',current_release.id,'prior_release_id',prior_release.id,'service_id',service_uuid,'current_before_receipt_id',before_receipt.id,'prior_after_rollback_receipt_id',prior_receipt.id,'current_after_restore_receipt_id',after_receipt.id,'recovery_strategy',current_release.recovery_strategy,'recovery_plan_ref',current_release.rollback_plan_ref,'plan_hash',current_release.plan_hash,'declared_migration_set_sha256',attempt.declared_migration_set_sha256,'declared_migration_count',attempt.declared_migration_count,'declared_schema_highest_migration',attempt.declared_schema_highest_migration,'declared_schema_applied_count',attempt.declared_schema_applied_count,'declared_schema_ledger_sha256',attempt.declared_schema_ledger_sha256);
    bundle_hash:='sha256:'||encode(public.digest(bundle_projection::text,'sha256'),'hex'); bundle_ref:='ops.staging-recovery-bundle:'||bundle_hash;
    insert into ops.staging_recovery_rehearsal_bundle(recovery_attempt_id,correlation_id,current_release_id,prior_release_id,service_id,environment,current_before_receipt_id,prior_after_rollback_receipt_id,current_after_restore_receipt_id,recovery_strategy,recovery_plan_ref,plan_hash,bundle_sha256,evidence_ref,declared_migration_set_sha256,declared_migration_count,declared_schema_highest_migration,declared_schema_applied_count,declared_schema_ledger_sha256,completed_at,writer_session_user)
    values(attempt.recovery_attempt_id,attempt.correlation_id,current_release.id,prior_release.id,service_uuid,'staging',before_receipt.id,prior_receipt.id,after_receipt.id,'rollback',current_release.rollback_plan_ref,current_release.plan_hash,bundle_hash,bundle_ref,attempt.declared_migration_set_sha256,attempt.declared_migration_count,attempt.declared_schema_highest_migration,attempt.declared_schema_applied_count,attempt.declared_schema_ledger_sha256,after_receipt.observed_at,session_user) returning id into bundle_uuid;
    insert into ops.run(correlation_id,kind,service_id,environment,run_key,state,started_at,ended_at,source_kind,source_ref,observed_at,evidence_ref,release_id,recovery_strategy,recovery_plan_ref,recovery_rehearsal_bundle_id)
    values(attempt.correlation_id,'check',service_uuid,'staging','recovery.rehearsal.worker','succeeded',before_receipt.observed_at,after_receipt.observed_at,'wrapper','bin/deploy-worker.sh',after_receipt.observed_at,bundle_ref,current_release.id,'rollback',current_release.rollback_plan_ref,bundle_uuid) returning id into run_uuid;
  end if;
  return jsonb_build_object('receipt_id',receipt_uuid,'receipt_ref',receipt_ref,'replayed',false,'bundle_id',bundle_uuid,'recovery_run_id',run_uuid);
end $$;

revoke all on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean) to carr_jobs;

commit;
