-- Program 5: bind performance and recovery evidence to a production release.
--
-- A release plan names its measurable performance budget and recovery
-- strategy before approval.  Actual receipts are ops.run rows linked directly
-- to that release; pointers on the release alone are deliberately insufficient.

begin;

alter table ops.release
  add column if not exists performance_budget_ref text,
  add column if not exists performance_budget_ms integer,
  add column if not exists recovery_strategy text;

alter table ops.run
  add column if not exists release_id uuid,
  add column if not exists budget_ms integer,
  add column if not exists recovery_strategy text,
  add column if not exists recovery_plan_ref text;

alter table ops.run
  drop constraint if exists run_release_id_fkey;

alter table ops.run
  add constraint run_release_id_fkey
  foreign key (release_id) references ops.release(id);

alter table ops.release
  drop constraint if exists production_promotion_requires_assurance;

alter table ops.release
  add constraint production_promotion_requires_assurance
  check (
    environment <> 'production'
    or state not in ('approved', 'deploying', 'verifying', 'complete')
    or (
      performance_budget_ref is not null
      and performance_budget_ms is not null
      and performance_budget_ms > 0
      and recovery_strategy is not null
      and recovery_strategy in ('rollback', 'forward_fix')
    )
  ) not valid;

alter table ops.run
  drop constraint if exists run_budget_ms_positive;

alter table ops.run
  add constraint run_budget_ms_positive
  check (budget_ms is null or budget_ms > 0) not valid;

-- A performance receipt is the production observation.  Its generated
-- duration_ms cannot exceed the plan budget after it succeeds.
alter table ops.run
  drop constraint if exists performance_run_assurance;

alter table ops.run
  add constraint performance_run_assurance
  check (
    run_key not like 'performance.%'
    or (
      release_id is not null
      and environment = 'production'
      and evidence_ref is not null
      and budget_ms is not null
      and (state <> 'succeeded'
           or (duration_ms > 0 and duration_ms <= budget_ms))
    )
  ) not valid;

-- Recovery evidence is exercised before production approval in staging or a
-- rehearsal environment.  A production-only rollback claim is not a rehearsal.
alter table ops.run
  drop constraint if exists recovery_rehearsal_assurance;

alter table ops.run
  add constraint recovery_rehearsal_assurance
  check (
    run_key not like 'recovery.rehearsal.%'
    or (
      release_id is not null
      and environment in ('staging', 'rehearsal')
      and evidence_ref is not null
      and recovery_strategy in ('rollback', 'forward_fix')
      and recovery_plan_ref is not null
    )
  ) not valid;

create index if not exists run_release_id_idx on ops.run (release_id);

-- Additive columns keep the established manifest order stable for consumers.
create or replace view ops.v_release_manifest as
select
  r.id as release_id,
  r.release_key,
  r.correlation_id,
  s.key as service_key,
  r.environment,
  r.state,
  r.git_sha as code_git_sha,
  r.artifact_digest as code_artifact_digest,
  r.dependency_lock_digest as code_dependency_lock_digest,
  r.sbom_ref as code_sbom_ref,
  r.schema_highest_migration,
  r.migration_set,
  r.config_fingerprint,
  r.declared_env_differences,
  r.asset_versions,
  r.test_evidence_ref,
  r.security_evidence_ref,
  r.maker_actor,
  r.maker_verification_ref,
  r.plan_hash as approval_plan_hash,
  r.approved_by_actor,
  r.approved_at,
  r.approval_expires_at,
  case
    when r.approved_at is null then 'unapproved'
    when r.approval_expires_at <= now() then 'expired'
    else 'live'
  end as approval_status,
  d.id as deployment_id,
  d.state as deploy_state,
  d.read_back_at as deploy_read_back_at,
  d.verification_evidence_ref as deploy_verification_evidence_ref,
  r.verifier_actor,
  r.verifier_evidence_ref,
  r.rollback_ready,
  r.rollback_plan_ref,
  r.work_request_ref,
  r.source_kind,
  r.source_ref,
  r.observed_at,
  r.expires_at,
  ops.freshness(r.observed_at, r.expires_at) as freshness,
  r.performance_budget_ref,
  r.performance_budget_ms,
  r.recovery_strategy
from ops.release r
join ops.service s on s.id = r.service_id
left join lateral (
  select * from ops.deployment d2
   where d2.release_id = r.id
   order by d2.observed_at desc
   limit 1
) d on true;

create or replace function ops.release_assurance_is_immutable()
returns trigger
language plpgsql
as $$
begin
  if old.state in ('approved', 'deploying', 'verifying', 'complete')
     and new.state <> 'candidate'
     and new.plan_hash is not distinct from old.plan_hash
     and (new.performance_budget_ref,
          new.performance_budget_ms,
          new.recovery_strategy,
          new.rollback_ready,
          new.rollback_plan_ref,
          new.service_id,
          new.environment,
          new.git_sha,
          new.artifact_digest,
          new.dependency_lock_digest,
          new.config_fingerprint,
          new.schema_highest_migration,
          new.migration_set) is distinct from
         (old.performance_budget_ref,
          old.performance_budget_ms,
          old.recovery_strategy,
          old.rollback_ready,
          old.rollback_plan_ref,
          old.service_id,
          old.environment,
          old.git_sha,
          old.artifact_digest,
          old.dependency_lock_digest,
          old.config_fingerprint,
          old.schema_highest_migration,
          old.migration_set) then
    raise exception 'Promoted release material is immutable until approval is invalidated';
  end if;
  return new;
end $$;

comment on function ops.release_assurance_is_immutable() is
  'Program 5: promoted release material is immutable until a changed plan hash invalidates approval and returns it to candidate.';

drop trigger if exists release_assurance_immutable on ops.release;

create trigger release_assurance_immutable
before update of performance_budget_ref, performance_budget_ms, recovery_strategy,
                 rollback_ready, rollback_plan_ref, service_id, environment, git_sha,
                 artifact_digest, dependency_lock_digest, config_fingerprint,
                 schema_highest_migration, migration_set, plan_hash, state
on ops.release
for each row execute function ops.release_assurance_is_immutable();

create or replace function ops.release_approval_requires_recovery_rehearsal()
returns trigger
language plpgsql
as $$
begin
  if new.environment = 'production'
     and new.state = 'approved'
     and (tg_op = 'INSERT'
          or old.environment is distinct from 'production'
          or old.state is distinct from 'approved')
     and not exists (
       select 1
         from ops.run r
        where r.release_id = new.id
          and r.service_id = new.service_id
          and r.environment in ('staging', 'rehearsal')
          and r.run_key like 'recovery.rehearsal.%'
          and r.state = 'succeeded'
          and r.evidence_ref is not null
          and r.recovery_strategy = new.recovery_strategy
          and r.recovery_plan_ref = new.rollback_plan_ref
     ) then
    raise exception 'Production release % cannot be approved: no successful recovery rehearsal receipt',
      new.release_key;
  end if;
  return new;
end $$;

comment on function ops.release_approval_requires_recovery_rehearsal() is
  'Program 5: Production approval requires a successful linked staging or rehearsal recovery receipt.';

drop trigger if exists release_approval_requires_recovery_rehearsal on ops.release;

create trigger release_approval_requires_recovery_rehearsal
before insert or update of state, environment, recovery_strategy, rollback_plan_ref on ops.release
for each row execute function ops.release_approval_requires_recovery_rehearsal();

-- Performance is a post-read-back observation, never a receipt prepared before
-- traffic identity has been independently observed.
create or replace function ops.performance_receipt_requires_read_back()
returns trigger
language plpgsql
as $$
begin
  if new.run_key like 'performance.%'
     and not exists (
       select 1
         from ops.deployment d
        where d.release_id = new.release_id
          and d.service_id = new.service_id
          and d.environment = 'production'
          and d.correlation_id = new.correlation_id
          and d.state in ('verifying', 'complete')
          and d.read_back_at is not null
     ) then
    raise exception 'performance receipt % requires an already-read-back Production deployment for the same release and correlation',
      new.run_key;
  end if;
  return new;
end $$;

comment on function ops.performance_receipt_requires_read_back() is
  'Program 5: a performance receipt must follow the same-correlation Production deployment identity read-back.';

drop trigger if exists performance_receipt_requires_read_back on ops.run;

create trigger performance_receipt_requires_read_back
before insert or update of release_id, service_id, environment, correlation_id, run_key, state
on ops.run
for each row execute function ops.performance_receipt_requires_read_back();

-- Replace the Program 5 completion guard with the stronger receipt-led rule.
-- The deployment remains the serving-identity proof; the two ops.run rows are
-- the performance and recovery evidence for this exact release.
create or replace function ops.release_completion_requires_a_read_back()
returns trigger
language plpgsql
as $$
begin
  if new.state = 'complete'
     and (tg_op = 'INSERT' or old.state is distinct from 'complete') then
    if not exists (
      select 1
        from ops.deployment d
       where d.release_id = new.id
         and d.service_id = new.service_id
         and d.environment = 'production'
         and d.state = 'complete'
         and d.read_back_at is not null
         and d.git_sha = new.git_sha
         and d.provider = new.provider
         and d.provider_version_id = new.provider_version_id
    ) then
      raise exception 'release % cannot be complete: no exact Production read-back',
        new.release_key;
    end if;

    if not exists (
      select 1
        from ops.run r
       where r.release_id = new.id
         and r.service_id = new.service_id
         and r.environment = 'production'
         and r.run_key like 'performance.%'
         and r.state = 'succeeded'
         and r.evidence_ref is not null
         and r.budget_ms = new.performance_budget_ms
         and r.duration_ms > 0
         and r.duration_ms <= r.budget_ms
         and exists (
           select 1
             from ops.deployment d
            where d.release_id = new.id
              and d.service_id = new.service_id
              and d.environment = 'production'
              and d.state = 'complete'
              and d.read_back_at is not null
              and d.git_sha = new.git_sha
              and d.provider = new.provider
              and d.provider_version_id = new.provider_version_id
              and d.correlation_id = r.correlation_id
         )
    ) then
      raise exception 'release % cannot be complete: no successful Production performance receipt within budget',
        new.release_key;
    end if;

    if not exists (
      select 1
        from ops.run r
       where r.release_id = new.id
         and r.service_id = new.service_id
         and r.environment in ('staging', 'rehearsal')
         and r.run_key like 'recovery.rehearsal.%'
         and r.state = 'succeeded'
         and r.evidence_ref is not null
         and r.recovery_strategy = new.recovery_strategy
         and r.recovery_plan_ref = new.rollback_plan_ref
    ) then
      raise exception 'release % cannot be complete: no successful staging or rehearsal recovery receipt',
        new.release_key;
    end if;
  end if;
  return new;
end $$;

comment on function ops.release_completion_requires_a_read_back() is
  'Program 5: completion requires an exact Production deployment read-back plus linked within-budget performance and recovery-plan-matched rehearsal receipts.';

drop trigger if exists release_completion_requires_a_read_back on ops.release;

create trigger release_completion_requires_a_read_back
before insert or update of state on ops.release
for each row execute function ops.release_completion_requires_a_read_back();

commit;
