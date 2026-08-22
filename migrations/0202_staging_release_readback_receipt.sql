-- 0202_staging_release_readback_receipt.sql
-- Program 5: an immutable, typed staging readback and current->prior->current
-- recovery rehearsal are the only evidence that can unlock Production approval.

begin;

create or replace function ops.program5_migration_set_sha256(p_migration_set text[])
returns text language sql immutable strict set search_path=ops,public,pg_temp
as $$
  select 'sha256:'||encode(public.digest(to_jsonb(p_migration_set)::text,'sha256'),'hex')
$$;

alter table ops.release
  add column schema_applied_count integer check (schema_applied_count > 0),
  add column schema_ledger_sha256 text
    check (schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$');

create or replace function ops.release_assurance_is_immutable()
returns trigger language plpgsql as $$
begin
  if old.state in ('approved','deploying','verifying','complete')
     and new.state <> 'candidate'
     and new.plan_hash is not distinct from old.plan_hash
     and (new.performance_budget_ref,new.performance_budget_ms,
          new.recovery_strategy,new.rollback_ready,new.rollback_plan_ref,
          new.service_id,new.environment,new.git_sha,new.artifact_digest,
          new.dependency_lock_digest,new.config_fingerprint,
          new.schema_highest_migration,new.schema_applied_count,
          new.schema_ledger_sha256,new.migration_set) is distinct from
         (old.performance_budget_ref,old.performance_budget_ms,
          old.recovery_strategy,old.rollback_ready,old.rollback_plan_ref,
          old.service_id,old.environment,old.git_sha,old.artifact_digest,
          old.dependency_lock_digest,old.config_fingerprint,
          old.schema_highest_migration,old.schema_applied_count,
          old.schema_ledger_sha256,old.migration_set) then
    raise exception 'Promoted release material is immutable until approval is invalidated';
  end if;
  return new;
end $$;

drop trigger if exists release_assurance_immutable on ops.release;
create trigger release_assurance_immutable
before update of performance_budget_ref,performance_budget_ms,recovery_strategy,
  rollback_ready,rollback_plan_ref,service_id,environment,git_sha,
  artifact_digest,dependency_lock_digest,config_fingerprint,
  schema_highest_migration,schema_applied_count,schema_ledger_sha256,
  migration_set,plan_hash,state on ops.release
for each row execute function ops.release_assurance_is_immutable();

create table ops.staging_deployment_attempt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  recovery_attempt_id uuid,
  recovery_step text not null
    check (recovery_step in ('standalone','current_before','prior','current_after')),
  correlation_id uuid not null,
  rehearsal_release_id uuid not null references ops.release(id) on delete restrict,
  observed_release_id uuid not null references ops.release(id) on delete restrict,
  prior_release_id uuid references ops.release(id) on delete restrict,
  service_id uuid not null references ops.service(id) on delete restrict,
  environment text not null check (environment='staging'),
  git_sha text not null check (git_sha ~ '^[0-9a-f]{40}$'),
  provider text not null check (provider='cloudflare-workers'),
  expected_provider_tag text not null unique
    check (expected_provider_tag ~ '^carr-staging-[0-9a-f]{32}$'),
  declared_migration_set_sha256 text not null
    check (declared_migration_set_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  declared_migration_count integer not null check (declared_migration_count > 0),
  declared_schema_highest_migration text not null
    check (declared_schema_highest_migration ~ '^[0-9]{4}_[a-z0-9_.-]+\.sql$'),
  declared_schema_applied_count integer not null
    check (declared_schema_applied_count > 0),
  declared_schema_ledger_sha256 text not null
    check (declared_schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  prepared_at timestamptz not null default clock_timestamp(),
  writer_session_user text not null check (writer_session_user='carr_jobs'),
  constraint staging_attempt_recovery_shape check (
    (recovery_step='standalone' and recovery_attempt_id is null and prior_release_id is null)
    or
    (recovery_step<>'standalone' and recovery_attempt_id is not null and prior_release_id is not null
      and correlation_id=recovery_attempt_id)
  ),
  unique (recovery_attempt_id,recovery_step)
);

create table ops.staging_deployment_claim (
  deployment_attempt_id uuid primary key
    references ops.staging_deployment_attempt(id) on delete restrict,
  claimed_at timestamptz not null default clock_timestamp(),
  writer_session_user text not null check (writer_session_user='carr_jobs')
);

create table ops.staging_release_readback_receipt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  deployment_attempt_id uuid not null unique
    references ops.staging_deployment_attempt(id) on delete restrict,
  recovery_attempt_id uuid,
  recovery_step text not null
    check (recovery_step in ('standalone','current_before','prior','current_after')),
  correlation_id uuid not null,
  deployment_id uuid not null unique references ops.deployment(id) on delete restrict,
  rehearsal_release_id uuid not null references ops.release(id) on delete restrict,
  observed_release_id uuid not null references ops.release(id) on delete restrict,
  prior_release_id uuid references ops.release(id) on delete restrict,
  service_id uuid not null references ops.service(id) on delete restrict,
  environment text not null check (environment = 'staging'),
  git_sha text not null check (git_sha ~ '^[0-9a-f]{40}$'),
  provider text not null check (provider = 'cloudflare-workers'),
  provider_version_id uuid not null unique,
  provider_tag text not null unique
    check (provider_tag ~ '^carr-staging-[a-z0-9-]{8,50}$'),
  verb_count integer not null check (verb_count > 0),
  schema_highest_migration text not null
    check (schema_highest_migration ~ '^[0-9]{4}_[a-z0-9_.-]+\.sql$'),
  schema_applied_count integer not null check (schema_applied_count > 0),
  declared_migration_set_sha256 text not null
    check (declared_migration_set_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  declared_migration_count integer not null check (declared_migration_count > 0),
  declared_schema_applied_count integer not null
    check (declared_schema_applied_count > 0),
  declared_schema_ledger_sha256 text not null
    check (declared_schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  doctrine_generation bigint not null check (doctrine_generation >= 0),
  projection_sha256 text not null unique
    check (projection_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique
    check (evidence_ref ~ '^ops\.staging-release-readback:sha256:[0-9a-f]{64}$'),
  observed_at timestamptz not null,
  recorded_at timestamptz not null default clock_timestamp(),
  writer_session_user text not null check (writer_session_user = 'carr_jobs'),
  constraint staging_readback_recovery_shape check (
    (recovery_step = 'standalone' and recovery_attempt_id is null and prior_release_id is null)
    or
    (recovery_step <> 'standalone' and recovery_attempt_id is not null and prior_release_id is not null
      and correlation_id = recovery_attempt_id)
  ),
  unique (recovery_attempt_id, recovery_step)
);

create table ops.staging_recovery_rehearsal_bundle (
  id uuid primary key default gen_random_uuid(),
  recovery_attempt_id uuid not null unique,
  correlation_id uuid not null unique,
  current_release_id uuid not null references ops.release(id) on delete restrict,
  prior_release_id uuid not null references ops.release(id) on delete restrict,
  service_id uuid not null references ops.service(id) on delete restrict,
  environment text not null check (environment = 'staging'),
  current_before_receipt_id uuid not null unique
    references ops.staging_release_readback_receipt(id) on delete restrict,
  prior_after_rollback_receipt_id uuid not null unique
    references ops.staging_release_readback_receipt(id) on delete restrict,
  current_after_restore_receipt_id uuid not null unique
    references ops.staging_release_readback_receipt(id) on delete restrict,
  recovery_strategy text not null check (recovery_strategy = 'rollback'),
  recovery_plan_ref text not null,
  plan_hash text not null,
  declared_migration_set_sha256 text not null
    check (declared_migration_set_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  declared_migration_count integer not null check (declared_migration_count > 0),
  declared_schema_highest_migration text not null,
  declared_schema_applied_count integer not null
    check (declared_schema_applied_count > 0),
  declared_schema_ledger_sha256 text not null
    check (declared_schema_ledger_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  bundle_sha256 text not null unique check (bundle_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique
    check (evidence_ref ~ '^ops\.staging-recovery-bundle:sha256:[0-9a-f]{64}$'),
  completed_at timestamptz not null,
  recorded_at timestamptz not null default clock_timestamp(),
  writer_session_user text not null check (writer_session_user = 'carr_jobs'),
  constraint recovery_bundle_distinct_releases check (current_release_id <> prior_release_id),
  constraint recovery_bundle_distinct_receipts check (
    current_before_receipt_id <> prior_after_rollback_receipt_id
    and current_before_receipt_id <> current_after_restore_receipt_id
    and prior_after_rollback_receipt_id <> current_after_restore_receipt_id
  )
);

alter table ops.run
  add column recovery_rehearsal_bundle_id uuid
    references ops.staging_recovery_rehearsal_bundle(id) on delete restrict;

create unique index run_recovery_rehearsal_bundle_once
  on ops.run(recovery_rehearsal_bundle_id)
  where recovery_rehearsal_bundle_id is not null;

alter table ops.run drop constraint if exists recovery_rehearsal_assurance;
alter table ops.run add constraint recovery_rehearsal_assurance check (
  run_key not like 'recovery.rehearsal.%'
  or (
    release_id is not null
    and environment = 'staging'
    and evidence_ref is not null
    and recovery_strategy = 'rollback'
    and recovery_plan_ref is not null
    and (state <> 'succeeded' or recovery_rehearsal_bundle_id is not null)
  )
) not valid;

create table ops.release_approval_receipt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  release_id uuid not null references ops.release(id) on delete restrict,
  recovery_run_id uuid not null unique references ops.run(id) on delete restrict,
  recovery_bundle_id uuid not null unique
    references ops.staging_recovery_rehearsal_bundle(id) on delete restrict,
  plan_hash text not null,
  approved_by_actor text not null check (approved_by_actor = 'joe'),
  approved_at timestamptz not null,
  approval_expires_at timestamptz not null,
  approval_sha256 text not null unique check (approval_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique
    check (evidence_ref ~ '^ops\.program5-release-approval:sha256:[0-9a-f]{64}$'),
  constraint approval_expiry_after_approval check (approval_expires_at > approved_at)
);

alter table ops.release
  add column approval_receipt_id uuid
    references ops.release_approval_receipt(id) on delete restrict;

create unique index release_approval_receipt_once
  on ops.release(approval_receipt_id)
  where approval_receipt_id is not null;

create or replace function ops.release_plan_revision_invalidates_approval()
returns trigger language plpgsql as $$
begin
  if new.plan_hash is distinct from old.plan_hash
     and old.state in ('approved','deploying','verifying') then
    new.state:= 'candidate';
    new.approved_by_actor:=null;
    new.approved_at:=null;
    new.approval_expires_at:=null;
    new.approval_receipt_id:=null;
    raise notice 'release %: the plan changed, so the approval and typed receipt pointer are gone. Re-approve against the new plan hash.',old.release_key;
  end if;
  new.updated_at:=now();
  return new;
end $$;

create or replace function ops.refuse_program5_evidence_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'Program 5 evidence is append-only';
end $$;

create trigger staging_release_readback_append_only
before update or delete on ops.staging_release_readback_receipt
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger staging_recovery_bundle_append_only
before update or delete on ops.staging_recovery_rehearsal_bundle
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger release_approval_receipt_append_only
before update or delete on ops.release_approval_receipt
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger staging_deployment_attempt_append_only
before update or delete on ops.staging_deployment_attempt
for each row execute function ops.refuse_program5_evidence_mutation();
create trigger staging_deployment_claim_append_only
before update or delete on ops.staging_deployment_claim
for each row execute function ops.refuse_program5_evidence_mutation();

create or replace function ops.protect_staging_readback_deployment()
returns trigger language plpgsql as $$
begin
  if exists (select 1 from ops.staging_release_readback_receipt where deployment_id=old.id) then
    raise exception 'Program 5 evidence deployment is append-only';
  end if;
  return case when tg_op='DELETE' then old else new end;
end $$;

create trigger protect_staging_readback_deployment
before update or delete on ops.deployment
for each row execute function ops.protect_staging_readback_deployment();

create or replace function ops.validate_recovery_rehearsal_run()
returns trigger language plpgsql as $$
declare b ops.staging_recovery_rehearsal_bundle%rowtype;
begin
  if new.recovery_rehearsal_bundle_id is null then return new; end if;
  select * into strict b from ops.staging_recovery_rehearsal_bundle
   where id=new.recovery_rehearsal_bundle_id;
  if new.kind <> 'check' or new.run_key <> 'recovery.rehearsal.worker'
     or new.state <> 'succeeded' or new.environment <> 'staging'
     or new.release_id <> b.current_release_id or new.service_id <> b.service_id
     or new.correlation_id <> b.correlation_id
     or new.recovery_strategy <> b.recovery_strategy
     or new.recovery_plan_ref <> b.recovery_plan_ref
     or new.evidence_ref <> b.evidence_ref
     or new.started_at is distinct from (
       select observed_at from ops.staging_release_readback_receipt where id=b.current_before_receipt_id)
     or new.ended_at is distinct from b.completed_at then
    raise exception 'recovery rehearsal run does not exactly match its typed bundle';
  end if;
  return new;
end $$;

create trigger validate_recovery_rehearsal_run
before insert or update of recovery_rehearsal_bundle_id,release_id,service_id,
  environment,correlation_id,run_key,state,evidence_ref,recovery_strategy,
  recovery_plan_ref,started_at,ended_at on ops.run
for each row execute function ops.validate_recovery_rehearsal_run();

create or replace function ops.prepare_staging_deployment_attempt(
  p_idempotency_key uuid,
  p_correlation_id uuid,
  p_release_key text,
  p_prior_release_key text,
  p_recovery_attempt_id uuid,
  p_recovery_step text,
  p_git_sha text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  existing ops.staging_deployment_attempt%rowtype;
  current_release ops.release%rowtype;
  prior_release ops.release%rowtype;
  observed_release ops.release%rowtype;
  attempt_uuid uuid;
  expected_tag text;
  migration_hash text;
  migration_count integer;
  receipt ops.staging_release_readback_receipt%rowtype;
begin
  if session_user<>'carr_jobs' then
    raise exception 'staging attempt writer requires the carr_jobs session';
  end if;
  if p_idempotency_key is null or p_correlation_id is null
     or coalesce(p_release_key,'')='' or coalesce(p_git_sha,'') !~ '^[0-9a-f]{40}$'
     or p_recovery_step not in ('standalone','current_before','prior','current_after') then
    raise exception 'invalid typed staging attempt input';
  end if;
  if (p_recovery_step='standalone' and (p_recovery_attempt_id is not null or p_prior_release_key is not null))
     or (p_recovery_step<>'standalone' and
         (p_recovery_attempt_id is null or p_prior_release_key is null
          or p_correlation_id<>p_recovery_attempt_id)) then
    raise exception 'recovery fields do not match the requested staging step';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  if p_recovery_attempt_id is not null then
    perform pg_advisory_xact_lock(hashtextextended(p_recovery_attempt_id::text,202));
  end if;
  select * into current_release from ops.release where release_key=p_release_key;
  if not found or current_release.service_id is null then
    raise exception 'staging attempt release does not exist';
  end if;
  if coalesce(cardinality(current_release.migration_set),0)<=0
     or coalesce(current_release.schema_highest_migration,'') !~ '^[0-9]{4}_[a-z0-9_.-]+\.sql$'
     or coalesce(current_release.schema_applied_count,0)<=0
     or coalesce(current_release.schema_ledger_sha256,'') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'release does not declare an exact migration/schema set';
  end if;
  migration_hash:=ops.program5_migration_set_sha256(current_release.migration_set);
  migration_count:=cardinality(current_release.migration_set);
  if p_recovery_step='standalone' then
    if current_release.environment not in ('staging','production')
       or current_release.state not in ('candidate','approved','deploying','verifying') then
      raise exception 'standalone staging attempt release is not deployable';
    end if;
    observed_release:=current_release;
  else
    if current_release.environment<>'production' or current_release.state<>'candidate'
       or current_release.recovery_strategy is distinct from 'rollback'
       or coalesce(current_release.rollback_plan_ref,'')=''
       or coalesce(current_release.plan_hash,'')='' then
      raise exception 'current release is not a rollback-ready Production candidate';
    end if;
    select * into prior_release from ops.release where release_key=p_prior_release_key;
    if not found or prior_release.id=current_release.id
       or prior_release.environment<>'production' or prior_release.state<>'complete'
       or prior_release.service_id<>current_release.service_id then
      raise exception 'prior release is not a distinct completed Production release';
    end if;
    if not exists (
      select 1 from ops.deployment d where d.release_id=prior_release.id
       and d.service_id=current_release.service_id and d.environment='production'
       and d.state='complete' and d.read_back_at is not null
       and d.git_sha=prior_release.git_sha and d.provider=prior_release.provider
       and d.provider_version_id=prior_release.provider_version_id) then
      raise exception 'prior release has no exact completed Production readback';
    end if;
    observed_release:=case when p_recovery_step='prior' then prior_release else current_release end;
  end if;
  if p_git_sha<>observed_release.git_sha then
    raise exception 'staging attempt SHA does not match the release required for this step';
  end if;
  expected_tag:='carr-staging-'||replace(p_idempotency_key::text,'-','');
  select * into existing from ops.staging_deployment_attempt where idempotency_key=p_idempotency_key;
  if found then
    if (existing.correlation_id,existing.recovery_attempt_id,existing.recovery_step,
        existing.rehearsal_release_id,existing.observed_release_id,existing.prior_release_id,
        existing.service_id,existing.git_sha,existing.expected_provider_tag,
        existing.declared_migration_set_sha256,existing.declared_migration_count,
        existing.declared_schema_highest_migration,
        existing.declared_schema_applied_count,existing.declared_schema_ledger_sha256) is distinct from
       (p_correlation_id,p_recovery_attempt_id,p_recovery_step,current_release.id,
        observed_release.id,case when p_recovery_step='standalone' then null else prior_release.id end,
        current_release.service_id,p_git_sha,expected_tag,migration_hash,migration_count,
        current_release.schema_highest_migration,current_release.schema_applied_count,
        current_release.schema_ledger_sha256) then
      raise exception 'staging attempt idempotency key was reused with changed input';
    end if;
    select * into receipt from ops.staging_release_readback_receipt
      where deployment_attempt_id=existing.id;
    return jsonb_build_object('attempt_id',existing.id,
      'expected_provider_tag',existing.expected_provider_tag,
      'state',case when receipt.id is null then 'prepared' else 'observed' end,
      'deploy_claimed',exists(select 1 from ops.staging_deployment_claim where deployment_attempt_id=existing.id),
      'provider_version_id',receipt.provider_version_id,'receipt_ref',receipt.evidence_ref,
      'replayed',true);
  end if;
  insert into ops.staging_deployment_attempt(
    idempotency_key,recovery_attempt_id,recovery_step,correlation_id,
    rehearsal_release_id,observed_release_id,prior_release_id,service_id,
    environment,git_sha,provider,expected_provider_tag,
    declared_migration_set_sha256,declared_migration_count,
    declared_schema_highest_migration,declared_schema_applied_count,
    declared_schema_ledger_sha256,writer_session_user)
  values(p_idempotency_key,p_recovery_attempt_id,p_recovery_step,p_correlation_id,
    current_release.id,observed_release.id,
    case when p_recovery_step='standalone' then null else prior_release.id end,
    current_release.service_id,'staging',p_git_sha,'cloudflare-workers',expected_tag,
    migration_hash,migration_count,current_release.schema_highest_migration,
    current_release.schema_applied_count,current_release.schema_ledger_sha256,session_user)
  returning id into attempt_uuid;
  return jsonb_build_object('attempt_id',attempt_uuid,'expected_provider_tag',expected_tag,
    'state','prepared','deploy_claimed',false,'provider_version_id',null,
    'receipt_ref',null,'replayed',false);
end $$;

create or replace function ops.claim_staging_deployment_attempt(p_idempotency_key uuid)
returns jsonb language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare attempt ops.staging_deployment_attempt%rowtype; inserted_count integer;
begin
  if session_user<>'carr_jobs' then
    raise exception 'staging attempt claim requires the carr_jobs session';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into attempt from ops.staging_deployment_attempt
    where idempotency_key=p_idempotency_key;
  if not found then raise exception 'staging attempt must be prepared before claim'; end if;
  if exists(select 1 from ops.staging_release_readback_receipt
            where deployment_attempt_id=attempt.id) then
    return jsonb_build_object('attempt_id',attempt.id,'deploy_allowed',false,
      'replayed',true,'state','observed');
  end if;
  insert into ops.staging_deployment_claim(deployment_attempt_id,writer_session_user)
    values(attempt.id,session_user) on conflict do nothing;
  get diagnostics inserted_count=row_count;
  return jsonb_build_object('attempt_id',attempt.id,
    'deploy_allowed',inserted_count=1,'replayed',inserted_count=0,
    'state',case when inserted_count=1 then 'claimed' else 'claimed_pending_readback' end);
end $$;

create or replace function ops.record_staging_release_readback(
  p_idempotency_key uuid,
  p_provider_version_id uuid,
  p_provider_tag text,
  p_verb_count integer,
  p_schema_highest_migration text,
  p_schema_applied_count integer,
  p_doctrine_generation bigint
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  existing ops.staging_release_readback_receipt%rowtype;
  attempt ops.staging_deployment_attempt%rowtype;
  current_release ops.release%rowtype;
  prior_release ops.release%rowtype;
  observed_release ops.release%rowtype;
  service_uuid uuid;
  deployment_uuid uuid;
  receipt_uuid uuid;
  before_receipt ops.staging_release_readback_receipt%rowtype;
  prior_receipt ops.staging_release_readback_receipt%rowtype;
  after_receipt ops.staging_release_readback_receipt%rowtype;
  bundle_uuid uuid;
  run_uuid uuid;
  projection jsonb;
  projection_hash text;
  receipt_ref text;
  bundle_projection jsonb;
  bundle_hash text;
  bundle_ref text;
  observed_time timestamptz := clock_timestamp();
begin
  if session_user <> 'carr_jobs' then
    raise exception 'staging readback writer requires the carr_jobs session';
  end if;
  if p_idempotency_key is null or p_provider_version_id is null
     or coalesce(p_provider_tag,'') !~ '^carr-staging-[a-z0-9-]{8,50}$'
     or coalesce(p_verb_count,0)<=0
     or coalesce(p_schema_highest_migration,'') !~ '^[0-9]{4}_[a-z0-9_.-]+\.sql$'
     or coalesce(p_schema_applied_count,0)<=0 or coalesce(p_doctrine_generation,-1)<0 then
    raise exception 'invalid typed staging readback input';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into attempt from ops.staging_deployment_attempt
    where idempotency_key=p_idempotency_key;
  if not found then raise exception 'staging readback has no prepared deployment attempt'; end if;
  if not exists(select 1 from ops.staging_deployment_claim
                where deployment_attempt_id=attempt.id) then
    raise exception 'staging readback deployment attempt was never claimed';
  end if;
  if attempt.recovery_attempt_id is not null then
    perform pg_advisory_xact_lock(hashtextextended(attempt.recovery_attempt_id::text,202));
  end if;
  select * into existing from ops.staging_release_readback_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if existing.deployment_attempt_id<>attempt.id
       or (existing.git_sha,existing.provider_version_id,existing.provider_tag,
        existing.verb_count,existing.schema_highest_migration,
        existing.schema_applied_count,existing.doctrine_generation) is distinct from
       (attempt.git_sha,p_provider_version_id,p_provider_tag,p_verb_count,
        p_schema_highest_migration,p_schema_applied_count,p_doctrine_generation) then
      raise exception 'staging readback idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('receipt_id',existing.id,'receipt_ref',existing.evidence_ref,
      'replayed',true,'bundle_id',(select id from ops.staging_recovery_rehearsal_bundle
       where recovery_attempt_id=attempt.recovery_attempt_id),'recovery_run_id',(
       select r.id from ops.run r join ops.staging_recovery_rehearsal_bundle b
         on b.id=r.recovery_rehearsal_bundle_id
        where b.recovery_attempt_id=attempt.recovery_attempt_id));
  end if;

  select * into strict current_release from ops.release where id=attempt.rehearsal_release_id;
  if attempt.declared_migration_set_sha256<>ops.program5_migration_set_sha256(current_release.migration_set)
     or attempt.declared_migration_count<>cardinality(current_release.migration_set)
     or attempt.declared_schema_highest_migration<>current_release.schema_highest_migration
     or attempt.declared_schema_applied_count<>current_release.schema_applied_count
     or attempt.declared_schema_ledger_sha256<>current_release.schema_ledger_sha256
     or p_schema_highest_migration<>attempt.declared_schema_highest_migration
     or p_schema_applied_count<>attempt.declared_schema_applied_count then
    raise exception 'staging readback schema does not match the exact declared candidate migration set';
  end if;
  if p_provider_tag<>attempt.expected_provider_tag then
    raise exception 'staging readback provider tag does not match its prepared attempt';
  end if;
  service_uuid := current_release.service_id;
  select * into strict observed_release from ops.release where id=attempt.observed_release_id;
  if attempt.prior_release_id is not null then
    select * into strict prior_release from ops.release where id=attempt.prior_release_id;
  end if;

  projection := jsonb_build_object(
    'deployment_attempt_id',attempt.id,'correlation_id',attempt.correlation_id,
    'recovery_attempt_id',attempt.recovery_attempt_id,
    'recovery_step',attempt.recovery_step,'rehearsal_release_id',current_release.id,
    'observed_release_id',observed_release.id,'prior_release_id',
    attempt.prior_release_id,'service_id',service_uuid,'environment','staging','git_sha',attempt.git_sha,
    'provider','cloudflare-workers','provider_version_id',p_provider_version_id,
    'provider_tag',p_provider_tag,'verb_count',p_verb_count,
    'schema_highest_migration',p_schema_highest_migration,
    'schema_applied_count',p_schema_applied_count,
    'declared_migration_set_sha256',attempt.declared_migration_set_sha256,
    'declared_migration_count',attempt.declared_migration_count,
    'declared_schema_applied_count',attempt.declared_schema_applied_count,
    'declared_schema_ledger_sha256',attempt.declared_schema_ledger_sha256,
    'doctrine_generation',p_doctrine_generation);
  projection_hash := 'sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  receipt_ref := 'ops.staging-release-readback:'||projection_hash;

  insert into ops.deployment(
    correlation_id,service_id,environment,state,git_sha,provider,provider_version_id,
    release_id,deployed_by_actor,verb_count,schema_highest_migration,
    doctrine_generation,started_at,ended_at,read_back_at,
    verification_evidence_ref,source_kind,source_ref,observed_at)
  values(attempt.correlation_id,service_uuid,'staging','complete',attempt.git_sha,
    'cloudflare-workers',p_provider_version_id::text,observed_release.id,
    session_user,p_verb_count,p_schema_highest_migration,p_doctrine_generation,
    observed_time,observed_time,observed_time,receipt_ref,'wrapper',
    'bin/deploy-worker.sh',observed_time)
  returning id into deployment_uuid;

  insert into ops.staging_release_readback_receipt(
    idempotency_key,deployment_attempt_id,recovery_attempt_id,recovery_step,correlation_id,deployment_id,
    rehearsal_release_id,observed_release_id,prior_release_id,service_id,environment,
    git_sha,provider,provider_version_id,provider_tag,verb_count,
    schema_highest_migration,schema_applied_count,declared_migration_set_sha256,
    declared_migration_count,declared_schema_applied_count,
    declared_schema_ledger_sha256,doctrine_generation,
    projection_sha256,evidence_ref,observed_at,writer_session_user)
  values(p_idempotency_key,attempt.id,attempt.recovery_attempt_id,attempt.recovery_step,attempt.correlation_id,
    deployment_uuid,current_release.id,observed_release.id,
    attempt.prior_release_id,service_uuid,'staging',attempt.git_sha,'cloudflare-workers',p_provider_version_id,
    p_provider_tag,p_verb_count,p_schema_highest_migration,p_schema_applied_count,
    attempt.declared_migration_set_sha256,attempt.declared_migration_count,
    attempt.declared_schema_applied_count,attempt.declared_schema_ledger_sha256,
    p_doctrine_generation,projection_hash,receipt_ref,observed_time,session_user)
  returning id into receipt_uuid;

  if attempt.recovery_step='current_after' then
    select * into before_receipt from ops.staging_release_readback_receipt
     where recovery_attempt_id=attempt.recovery_attempt_id and recovery_step='current_before';
    if not found then raise exception 'current_after requires current_before receipt'; end if;
    select * into prior_receipt from ops.staging_release_readback_receipt
     where recovery_attempt_id=attempt.recovery_attempt_id and recovery_step='prior';
    if not found then raise exception 'current_after requires prior receipt'; end if;
    select * into strict after_receipt from ops.staging_release_readback_receipt where id=receipt_uuid;
    if before_receipt.rehearsal_release_id<>current_release.id
       or prior_receipt.rehearsal_release_id<>current_release.id
       or before_receipt.prior_release_id<>prior_release.id
       or prior_receipt.prior_release_id<>prior_release.id
       or before_receipt.observed_release_id<>current_release.id
       or prior_receipt.observed_release_id<>prior_release.id
       or after_receipt.observed_release_id<>current_release.id
       or before_receipt.service_id<>service_uuid or prior_receipt.service_id<>service_uuid
       or before_receipt.declared_migration_set_sha256<>attempt.declared_migration_set_sha256
       or prior_receipt.declared_migration_set_sha256<>attempt.declared_migration_set_sha256
       or after_receipt.declared_migration_set_sha256<>attempt.declared_migration_set_sha256
       or before_receipt.declared_migration_count<>attempt.declared_migration_count
       or prior_receipt.declared_migration_count<>attempt.declared_migration_count
       or after_receipt.declared_migration_count<>attempt.declared_migration_count
       or before_receipt.schema_highest_migration<>attempt.declared_schema_highest_migration
       or prior_receipt.schema_highest_migration<>attempt.declared_schema_highest_migration
       or after_receipt.schema_highest_migration<>attempt.declared_schema_highest_migration
       or before_receipt.schema_applied_count<>attempt.declared_schema_applied_count
       or prior_receipt.schema_applied_count<>attempt.declared_schema_applied_count
       or after_receipt.schema_applied_count<>attempt.declared_schema_applied_count
       or before_receipt.declared_schema_ledger_sha256<>attempt.declared_schema_ledger_sha256
       or prior_receipt.declared_schema_ledger_sha256<>attempt.declared_schema_ledger_sha256
       or after_receipt.declared_schema_ledger_sha256<>attempt.declared_schema_ledger_sha256
       or not (before_receipt.observed_at < prior_receipt.observed_at
               and prior_receipt.observed_at < after_receipt.observed_at)
       or after_receipt.observed_at-before_receipt.observed_at > interval '1 hour' then
      raise exception 'recovery receipts do not form an ordered current-prior-current chain';
    end if;
    bundle_projection := jsonb_build_object(
      'recovery_attempt_id',attempt.recovery_attempt_id,'current_release_id',current_release.id,
      'prior_release_id',prior_release.id,'service_id',service_uuid,
      'current_before_receipt_id',before_receipt.id,
      'prior_after_rollback_receipt_id',prior_receipt.id,
      'current_after_restore_receipt_id',after_receipt.id,
      'recovery_strategy',current_release.recovery_strategy,
      'recovery_plan_ref',current_release.rollback_plan_ref,
      'plan_hash',current_release.plan_hash,
      'declared_migration_set_sha256',attempt.declared_migration_set_sha256,
      'declared_migration_count',attempt.declared_migration_count,
      'declared_schema_highest_migration',attempt.declared_schema_highest_migration,
      'declared_schema_applied_count',attempt.declared_schema_applied_count,
      'declared_schema_ledger_sha256',attempt.declared_schema_ledger_sha256);
    bundle_hash := 'sha256:'||encode(public.digest(bundle_projection::text,'sha256'),'hex');
    bundle_ref := 'ops.staging-recovery-bundle:'||bundle_hash;
    insert into ops.staging_recovery_rehearsal_bundle(
      recovery_attempt_id,correlation_id,current_release_id,prior_release_id,
      service_id,environment,current_before_receipt_id,
      prior_after_rollback_receipt_id,current_after_restore_receipt_id,
      recovery_strategy,recovery_plan_ref,plan_hash,bundle_sha256,evidence_ref,
      declared_migration_set_sha256,declared_migration_count,
      declared_schema_highest_migration,declared_schema_applied_count,
      declared_schema_ledger_sha256,completed_at,writer_session_user)
    values(attempt.recovery_attempt_id,attempt.correlation_id,current_release.id,prior_release.id,
      service_uuid,'staging',before_receipt.id,prior_receipt.id,after_receipt.id,
      'rollback',current_release.rollback_plan_ref,current_release.plan_hash,
      bundle_hash,bundle_ref,attempt.declared_migration_set_sha256,
      attempt.declared_migration_count,attempt.declared_schema_highest_migration,
      attempt.declared_schema_applied_count,attempt.declared_schema_ledger_sha256,
      after_receipt.observed_at,session_user)
    returning id into bundle_uuid;

    insert into ops.run(correlation_id,kind,service_id,environment,run_key,state,
      started_at,ended_at,source_kind,source_ref,observed_at,evidence_ref,
      release_id,recovery_strategy,recovery_plan_ref,recovery_rehearsal_bundle_id)
    values(attempt.correlation_id,'check',service_uuid,'staging','recovery.rehearsal.worker',
      'succeeded',before_receipt.observed_at,after_receipt.observed_at,'wrapper',
      'bin/deploy-worker.sh',after_receipt.observed_at,bundle_ref,current_release.id,
      'rollback',current_release.rollback_plan_ref,bundle_uuid)
    returning id into run_uuid;
  end if;

  return jsonb_build_object('receipt_id',receipt_uuid,'receipt_ref',receipt_ref,
    'replayed',false,'bundle_id',bundle_uuid,'recovery_run_id',run_uuid);
end $$;

create or replace function ops.release_approval_requires_recovery_rehearsal()
returns trigger language plpgsql as $$
begin
  if new.environment='production' and new.state='approved'
     and (tg_op='INSERT' or old.environment is distinct from 'production'
          or old.state is distinct from 'approved')
     and not exists (
       select 1 from ops.run r
       join ops.staging_recovery_rehearsal_bundle b
         on b.id=r.recovery_rehearsal_bundle_id
       where r.release_id=new.id and r.service_id=new.service_id
        and r.environment='staging' and r.run_key='recovery.rehearsal.worker'
        and r.state='succeeded' and r.evidence_ref=b.evidence_ref
        and b.current_release_id=new.id and b.service_id=new.service_id
        and b.recovery_strategy=new.recovery_strategy
        and b.recovery_plan_ref=new.rollback_plan_ref and b.plan_hash=new.plan_hash
        and b.declared_migration_set_sha256=ops.program5_migration_set_sha256(new.migration_set)
        and b.declared_migration_count=cardinality(new.migration_set)
        and b.declared_schema_highest_migration=new.schema_highest_migration
        and b.declared_schema_applied_count=new.schema_applied_count
        and b.declared_schema_ledger_sha256=new.schema_ledger_sha256) then
    raise exception 'Production release % cannot be approved: no exact typed recovery bundle',new.release_key;
  end if;
  return new;
end $$;

create or replace function ops.release_completion_requires_a_read_back()
returns trigger language plpgsql as $$
begin
  if new.state='complete' and (tg_op='INSERT' or old.state is distinct from 'complete') then
    if not exists (select 1 from ops.deployment d where d.release_id=new.id
      and d.service_id=new.service_id and d.environment='production'
      and d.state='complete' and d.read_back_at is not null and d.git_sha=new.git_sha
      and d.provider=new.provider and d.provider_version_id=new.provider_version_id) then
      raise exception 'release % cannot be complete: no exact Production read-back',new.release_key;
    end if;
    if not exists (select 1 from ops.run r join ops.deployment d
      on d.release_id=r.release_id and d.service_id=r.service_id
      and d.correlation_id=r.correlation_id
      where r.release_id=new.id and r.service_id=new.service_id
      and r.environment='production' and r.run_key like 'performance.%'
      and r.state='succeeded' and r.evidence_ref is not null
      and r.budget_ms=new.performance_budget_ms and r.duration_ms>0
      and r.duration_ms<=r.budget_ms and d.environment='production'
      and d.state='complete' and d.read_back_at is not null
      and d.git_sha=new.git_sha and d.provider=new.provider
      and d.provider_version_id=new.provider_version_id) then
      raise exception 'release % cannot be complete: no within-budget Production performance receipt',new.release_key;
    end if;
    if not exists (select 1 from ops.run r
      join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id
      where r.release_id=new.id and r.service_id=new.service_id
      and r.environment='staging' and r.run_key='recovery.rehearsal.worker'
      and r.state='succeeded' and r.evidence_ref=b.evidence_ref
      and b.current_release_id=new.id and b.service_id=new.service_id
      and b.recovery_strategy=new.recovery_strategy
      and b.recovery_plan_ref=new.rollback_plan_ref and b.plan_hash=new.plan_hash
      and b.declared_migration_set_sha256=ops.program5_migration_set_sha256(new.migration_set)
      and b.declared_migration_count=cardinality(new.migration_set)
      and b.declared_schema_highest_migration=new.schema_highest_migration
      and b.declared_schema_applied_count=new.schema_applied_count
      and b.declared_schema_ledger_sha256=new.schema_ledger_sha256
      and b.completed_at between new.approved_at-interval '24 hours' and new.approved_at) then
      raise exception 'release % cannot be complete: no exact typed recovery bundle',new.release_key;
    end if;
  end if;
  return new;
end $$;

create or replace function ops.program5_release_approval_is_joe_owned()
returns trigger language plpgsql as $$
begin
  if new.environment='production'
     and new.state in ('approved','deploying','verifying','complete')
     and (tg_op='INSERT' or old.state not in ('approved','deploying','verifying','complete')) then
    if new.state<>'approved' or session_user<>'carr_authority_joe'
       or new.approved_by_actor<>'joe'
       or new.approval_receipt_id is null or not exists (
         select 1 from ops.release_approval_receipt a
          where a.id=new.approval_receipt_id and a.release_id=new.id
            and a.plan_hash=new.plan_hash and a.approved_by_actor='joe'
            and a.approved_at=new.approved_at
            and a.approval_expires_at=new.approval_expires_at) then
      raise exception 'Program 5 Production approval requires Joe authority and its typed receipt';
    end if;
  end if;
  if tg_op='UPDATE'
     and old.environment='production'
     and old.state in ('approved','deploying','verifying','complete')
     and new.state in ('approved','deploying','verifying','complete')
     and (new.approved_by_actor,new.approved_at,new.approval_expires_at,new.approval_receipt_id)
         is distinct from
         (old.approved_by_actor,old.approved_at,old.approval_expires_at,old.approval_receipt_id) then
    raise exception 'Program 5 approval projection is immutable across promoted states';
  end if;
  return new;
end $$;

create trigger program5_release_approval_is_joe_owned
before insert or update of state,approved_by_actor,approved_at,approval_expires_at,
  approval_receipt_id on ops.release
for each row execute function ops.program5_release_approval_is_joe_owned();

create or replace function ops.approve_program5_release(
  p_release_key text,p_plan_hash text,p_idempotency_key uuid,p_expires_hours integer
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare actor_slug text; rel ops.release%rowtype; existing ops.release_approval_receipt%rowtype;
  rehearsal_run ops.run%rowtype; approval_uuid uuid; approved_time timestamptz;
  expiry_time timestamptz; projection jsonb; approval_hash text; approval_ref text;
begin
  actor_slug:=ops.authority_actor_slug();
  if actor_slug<>'joe' then raise exception 'Program 5 Production approval requires Joe authority'; end if;
  if p_idempotency_key is null or coalesce(p_release_key,'')='' or coalesce(p_plan_hash,'')=''
     or coalesce(p_expires_hours,0) not between 1 and 24 then
    raise exception 'invalid Program 5 approval input';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into existing from ops.release_approval_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.plan_hash<>p_plan_hash
       or existing.approval_expires_at-existing.approved_at
          <> make_interval(hours=>p_expires_hours)
       or not exists (
      select 1 from ops.release where id=existing.release_id and release_key=p_release_key
        and plan_hash=existing.plan_hash and approval_receipt_id=existing.id
        and state in ('approved','deploying','verifying','complete')
        and approved_at=existing.approved_at
        and approval_expires_at=existing.approval_expires_at) then
      raise exception 'Program 5 approval idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('approval_receipt_id',existing.id,
      'approval_ref',existing.evidence_ref,'approval_expires_at',existing.approval_expires_at,
      'replayed',true);
  end if;
  select * into rel from ops.release where release_key=p_release_key for update;
  if not found or rel.environment<>'production' or rel.state<>'candidate'
     or rel.plan_hash is distinct from p_plan_hash then
    raise exception 'release is not the exact Production candidate plan requested';
  end if;
  select r.* into rehearsal_run from ops.run r
   join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id
   where r.release_id=rel.id and r.service_id=rel.service_id
     and r.environment='staging' and r.run_key='recovery.rehearsal.worker'
     and r.state='succeeded' and r.evidence_ref=b.evidence_ref
     and b.current_release_id=rel.id and b.service_id=rel.service_id
     and b.recovery_strategy=rel.recovery_strategy
     and b.recovery_plan_ref=rel.rollback_plan_ref and b.plan_hash=rel.plan_hash
     and b.declared_migration_set_sha256=ops.program5_migration_set_sha256(rel.migration_set)
     and b.declared_migration_count=cardinality(rel.migration_set)
     and b.declared_schema_highest_migration=rel.schema_highest_migration
     and b.declared_schema_applied_count=rel.schema_applied_count
     and b.declared_schema_ledger_sha256=rel.schema_ledger_sha256
     and b.completed_at between clock_timestamp()-interval '24 hours' and clock_timestamp()
   order by r.ended_at desc limit 1;
  if not found then raise exception 'release has no exact typed recovery rehearsal'; end if;
  approved_time:=clock_timestamp(); expiry_time:=approved_time+make_interval(hours=>p_expires_hours);
  projection:=jsonb_build_object('release_id',rel.id,'plan_hash',rel.plan_hash,
    'recovery_run_id',rehearsal_run.id,'recovery_bundle_id',rehearsal_run.recovery_rehearsal_bundle_id,
    'approved_by_actor',actor_slug,'approved_at',approved_time,'approval_expires_at',expiry_time);
  approval_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  approval_ref:='ops.program5-release-approval:'||approval_hash;
  insert into ops.release_approval_receipt(idempotency_key,release_id,recovery_run_id,
    recovery_bundle_id,plan_hash,approved_by_actor,approved_at,approval_expires_at,
    approval_sha256,evidence_ref)
  values(p_idempotency_key,rel.id,rehearsal_run.id,rehearsal_run.recovery_rehearsal_bundle_id,
    rel.plan_hash,actor_slug,approved_time,expiry_time,approval_hash,approval_ref)
  returning id into approval_uuid;
  update ops.release set state='approved',approved_by_actor=actor_slug,
    approved_at=approved_time,approval_expires_at=expiry_time,
    approval_receipt_id=approval_uuid where id=rel.id;
  return jsonb_build_object('approval_receipt_id',approval_uuid,'approval_ref',approval_ref,
    'approval_expires_at',expiry_time,'replayed',false);
end $$;

revoke all on ops.staging_deployment_attempt from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on ops.staging_deployment_claim from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on ops.staging_release_readback_receipt from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on ops.staging_recovery_rehearsal_bundle from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on ops.release_approval_receipt from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant select on ops.staging_deployment_attempt,ops.staging_deployment_claim,
  ops.staging_release_readback_receipt,ops.staging_recovery_rehearsal_bundle,
  ops.release_approval_receipt to carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.program5_migration_set_sha256(text[]) from public;
revoke all on function ops.prepare_staging_deployment_attempt(uuid,uuid,text,text,uuid,text,text)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.prepare_staging_deployment_attempt(uuid,uuid,text,text,uuid,text,text)
  to carr_jobs;
revoke all on function ops.claim_staging_deployment_attempt(uuid)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.claim_staging_deployment_attempt(uuid) to carr_jobs;
revoke all on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)
  from public,carr_reader,carr_writer,carr_authority;
grant execute on function ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)
  to carr_jobs;
revoke all on function ops.approve_program5_release(text,text,uuid,integer)
  from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_program5_release(text,text,uuid,integer) to carr_authority;

commit;

do $$
begin
  if has_table_privilege('carr_jobs','ops.staging_release_readback_receipt','insert')
     or has_table_privilege('carr_jobs','ops.staging_deployment_attempt','insert')
     or has_table_privilege('carr_jobs','ops.staging_deployment_claim','insert')
     or has_table_privilege('carr_writer','ops.staging_recovery_rehearsal_bundle','insert')
     or has_table_privilege('carr_authority','ops.release_approval_receipt','insert') then
    raise exception '0202 FAILED: runtime roles retain direct Program 5 evidence DML';
  end if;
  if not has_function_privilege('carr_jobs',
      'ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)'::regprocedure,'execute')
     or has_function_privilege('carr_writer',
      'ops.record_staging_release_readback(uuid,uuid,text,integer,text,integer,bigint)'::regprocedure,'execute')
     or not has_function_privilege('carr_jobs',
      'ops.prepare_staging_deployment_attempt(uuid,uuid,text,text,uuid,text,text)'::regprocedure,'execute')
     or not has_function_privilege('carr_jobs',
      'ops.claim_staging_deployment_attempt(uuid)'::regprocedure,'execute') then
    raise exception '0202 FAILED: staging receipt execution boundary is wrong';
  end if;
  if has_function_privilege('carr_jobs','ops.approve_program5_release(text,text,uuid,integer)'::regprocedure,'execute')
     or not has_function_privilege('carr_authority','ops.approve_program5_release(text,text,uuid,integer)'::regprocedure,'execute') then
    raise exception '0202 FAILED: Joe approval execution boundary is wrong';
  end if;
end $$;
