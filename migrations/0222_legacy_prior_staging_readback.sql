-- Restore the retired legacy recorder only for a historical Production prior
-- observation in a prepared, claimed current-prior-current rehearsal.  It
-- deliberately writes NULL Program 6 posture, preserving the pre-0218 receipt
-- projection instead of claiming that an old caller supplied that fact.

begin;

-- This is a one-time migration-time ledger, not a reusable recovery class.
-- Only a pre-existing, still-unobserved prior attempt is captured.  The writer
-- still requires a later normal claim before it can record provider truth.
create table ops.legacy_prior_staging_readback_allowlist (
  idempotency_key uuid primary key,
  deployment_attempt_id uuid not null unique references ops.staging_deployment_attempt(id) on delete restrict,
  captured_at timestamptz not null default clock_timestamp()
);

-- Freeze the source ledger while capturing it: a concurrent post-boundary
-- prepare/readback cannot slip into this one-time compatibility set.
lock table ops.staging_deployment_attempt, ops.staging_release_readback_receipt
  in share row exclusive mode;

insert into ops.legacy_prior_staging_readback_allowlist(idempotency_key,deployment_attempt_id)
select a.idempotency_key,a.id
from ops.staging_deployment_attempt a
join ops.release prior_release on prior_release.id=a.prior_release_id
where a.recovery_step='prior'
  and a.recovery_attempt_id is not null
  and a.observed_release_id=a.prior_release_id
  and prior_release.environment='production'
  and prior_release.state='complete'
  and not exists(select 1 from ops.staging_release_readback_receipt r where r.deployment_attempt_id=a.id);

-- The Program 6 recorder must never relabel a NULL-posture legacy receipt as
-- a new typed fact.  Keep the old implementation private and put the boundary
-- check in front of its public eight-argument signature.
alter function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)
  rename to record_staging_release_readback_program6;
revoke all on function ops.record_staging_release_readback_program6(uuid,uuid,text,integer,text,integer,bigint,boolean)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

create function ops.record_staging_release_readback(
  p_idempotency_key uuid, p_provider_version_id uuid, p_provider_tag text,
  p_verb_count integer, p_schema_highest_migration text,
  p_schema_applied_count integer, p_doctrine_generation bigint,
  p_program6_actions_enabled boolean
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare legacy_posture boolean;
begin
  if session_user <> 'carr_jobs' then raise exception 'staging readback writer requires the carr_jobs session'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select program6_actions_enabled into legacy_posture
  from ops.staging_release_readback_receipt where idempotency_key=p_idempotency_key;
  if found and legacy_posture is null then
    raise exception 'Program 6 recorder cannot replay a legacy NULL-posture receipt';
  end if;
  return ops.record_staging_release_readback_program6(p_idempotency_key,p_provider_version_id,p_provider_tag,
    p_verb_count,p_schema_highest_migration,p_schema_applied_count,p_doctrine_generation,p_program6_actions_enabled);
end $$;

revoke all on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint,boolean) to carr_jobs;

create function ops.record_staging_release_readback(
  p_idempotency_key uuid, p_provider_version_id uuid, p_provider_tag text,
  p_verb_count integer, p_schema_highest_migration text,
  p_schema_applied_count integer, p_doctrine_generation bigint
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  existing ops.staging_release_readback_receipt%rowtype;
  attempt ops.staging_deployment_attempt%rowtype;
  current_release ops.release%rowtype;
  prior_release ops.release%rowtype;
  deployment_uuid uuid; receipt_uuid uuid; projection jsonb; projection_hash text;
  receipt_ref text; observed_time timestamptz := clock_timestamp();
begin
  if session_user <> 'carr_jobs' then raise exception 'staging readback writer requires the carr_jobs session'; end if;
  if p_idempotency_key is null or p_provider_version_id is null
     or coalesce(p_provider_tag,'') !~ '^carr-staging-[a-z0-9-]{8,50}$'
     or coalesce(p_verb_count,0)<=0
     or coalesce(p_schema_highest_migration,'') !~ '^[0-9]{4}_[a-z0-9_.-]+\.sql$'
     or coalesce(p_schema_applied_count,0)<=0 or coalesce(p_doctrine_generation,-1)<0 then
    raise exception 'invalid typed staging readback input';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into attempt from ops.staging_deployment_attempt where idempotency_key=p_idempotency_key;
  if not found then raise exception 'staging readback has no prepared deployment attempt'; end if;
  if attempt.recovery_step <> 'prior' or attempt.recovery_attempt_id is null
     or attempt.prior_release_id is null or attempt.observed_release_id is distinct from attempt.prior_release_id then
    raise exception 'legacy staging readback is limited to the exact historical Production prior observation';
  end if;
  if not exists(select 1 from ops.legacy_prior_staging_readback_allowlist l
                where l.idempotency_key=p_idempotency_key and l.deployment_attempt_id=attempt.id) then
    raise exception 'legacy staging readback idempotency key was not captured at migration time';
  end if;
  if not exists(select 1 from ops.staging_deployment_claim where deployment_attempt_id=attempt.id) then
    raise exception 'staging readback deployment attempt was never claimed'; end if;
  perform pg_advisory_xact_lock(hashtextextended(attempt.recovery_attempt_id::text,202));
  select * into existing from ops.staging_release_readback_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.deployment_attempt_id<>attempt.id
       or (existing.git_sha,existing.provider_version_id,existing.provider_tag,existing.verb_count,
           existing.schema_highest_migration,existing.schema_applied_count,existing.doctrine_generation,
           existing.program6_actions_enabled) is distinct from
          (attempt.git_sha,p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,
           p_schema_applied_count,p_doctrine_generation,null::boolean) then
      raise exception 'staging readback idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('receipt_id',existing.id,'receipt_ref',existing.evidence_ref,'replayed',true,
      'bundle_id',(select id from ops.staging_recovery_rehearsal_bundle where recovery_attempt_id=attempt.recovery_attempt_id),
      'recovery_run_id',(select r.id from ops.run r join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id where b.recovery_attempt_id=attempt.recovery_attempt_id));
  end if;
  select * into strict current_release from ops.release where id=attempt.rehearsal_release_id;
  select * into strict prior_release from ops.release where id=attempt.prior_release_id;
  if prior_release.environment <> 'production' or prior_release.state <> 'complete' then
    raise exception 'legacy staging readback requires a completed historical Production prior release'; end if;
  if not exists(select 1 from ops.staging_release_readback_receipt before_receipt
                where before_receipt.recovery_attempt_id=attempt.recovery_attempt_id
                  and before_receipt.recovery_step='current_before'
                  and before_receipt.rehearsal_release_id=current_release.id
                  and before_receipt.observed_release_id=current_release.id
                  and before_receipt.prior_release_id=prior_release.id) then
    raise exception 'legacy staging readback prior requires its exact current_before receipt'; end if;
  if attempt.declared_migration_set_sha256<>ops.program5_migration_set_sha256(current_release.migration_set)
     or attempt.declared_migration_count<>cardinality(current_release.migration_set)
     or attempt.declared_schema_highest_migration<>current_release.schema_highest_migration
     or attempt.declared_schema_applied_count<>current_release.schema_applied_count
     or attempt.declared_schema_ledger_sha256<>current_release.schema_ledger_sha256
     or p_schema_highest_migration<>attempt.declared_schema_highest_migration
     or p_schema_applied_count<>attempt.declared_schema_applied_count then
    raise exception 'staging readback schema does not match the exact declared candidate migration set'; end if;
  if p_provider_tag<>attempt.expected_provider_tag then raise exception 'staging readback provider tag does not match its prepared attempt'; end if;
  projection:=jsonb_build_object(
    'deployment_attempt_id',attempt.id,'correlation_id',attempt.correlation_id,'recovery_attempt_id',attempt.recovery_attempt_id,
    'recovery_step',attempt.recovery_step,'rehearsal_release_id',current_release.id,'observed_release_id',prior_release.id,
    'prior_release_id',attempt.prior_release_id,'service_id',current_release.service_id,'environment','staging','git_sha',attempt.git_sha,
    'provider','cloudflare-workers','provider_version_id',p_provider_version_id,'provider_tag',p_provider_tag,
    'verb_count',p_verb_count,'schema_highest_migration',p_schema_highest_migration,'schema_applied_count',p_schema_applied_count,
    'declared_migration_set_sha256',attempt.declared_migration_set_sha256,'declared_migration_count',attempt.declared_migration_count,
    'declared_schema_applied_count',attempt.declared_schema_applied_count,'declared_schema_ledger_sha256',attempt.declared_schema_ledger_sha256,
    'doctrine_generation',p_doctrine_generation);
  projection_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex'); receipt_ref:='ops.staging-release-readback:'||projection_hash;
  insert into ops.deployment(correlation_id,service_id,environment,state,git_sha,provider,provider_version_id,release_id,deployed_by_actor,verb_count,schema_highest_migration,doctrine_generation,started_at,ended_at,read_back_at,verification_evidence_ref,source_kind,source_ref,observed_at)
  values(attempt.correlation_id,current_release.service_id,'staging','complete',attempt.git_sha,'cloudflare-workers',p_provider_version_id::text,prior_release.id,session_user,p_verb_count,p_schema_highest_migration,p_doctrine_generation,observed_time,observed_time,observed_time,receipt_ref,'wrapper','bin/deploy-worker.sh',observed_time) returning id into deployment_uuid;
  insert into ops.staging_release_readback_receipt(idempotency_key,deployment_attempt_id,recovery_attempt_id,recovery_step,correlation_id,deployment_id,rehearsal_release_id,observed_release_id,prior_release_id,service_id,environment,git_sha,provider,provider_version_id,provider_tag,verb_count,schema_highest_migration,schema_applied_count,declared_migration_set_sha256,declared_migration_count,declared_schema_applied_count,declared_schema_ledger_sha256,doctrine_generation,program6_actions_enabled,projection_sha256,evidence_ref,observed_at,writer_session_user)
  values(p_idempotency_key,attempt.id,attempt.recovery_attempt_id,attempt.recovery_step,attempt.correlation_id,deployment_uuid,current_release.id,prior_release.id,attempt.prior_release_id,current_release.service_id,'staging',attempt.git_sha,'cloudflare-workers',p_provider_version_id,p_provider_tag,p_verb_count,p_schema_highest_migration,p_schema_applied_count,attempt.declared_migration_set_sha256,attempt.declared_migration_count,attempt.declared_schema_applied_count,attempt.declared_schema_ledger_sha256,p_doctrine_generation,null,projection_hash,receipt_ref,observed_time,session_user) returning id into receipt_uuid;
  return jsonb_build_object('receipt_id',receipt_uuid,'receipt_ref',receipt_ref,'replayed',false,'bundle_id',null,'recovery_run_id',null);
end $$;

revoke all on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint) to carr_jobs;

revoke all on ops.legacy_prior_staging_readback_allowlist from public,carr_reader,carr_writer,carr_jobs,carr_authority;

commit;
