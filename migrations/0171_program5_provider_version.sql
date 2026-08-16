-- 0171_program5_provider_version.sql
-- Program 5: bind a Production promotion to its immutable provider version.
--
-- 0169 establishes exact release SHA/environment/read-back binding.  This
-- migration closes the remaining provider ambiguity: a Production promotion
-- records the Cloudflare Workers version that was approved and observed.

begin;

alter table ops.release
  add column if not exists provider text,
  add column if not exists provider_version_id text;

alter table ops.deployment
  add column if not exists provider text,
  add column if not exists provider_version_id text;

-- Provider identity is deliberately nullable until a Production release is
-- promoted.  NOT VALID preserves historical evidence while PostgreSQL checks
-- every new insert and every updated legacy row.
alter table ops.release
  drop constraint if exists production_promotion_requires_provider_version;

alter table ops.release
  add constraint production_promotion_requires_provider_version check (
    environment <> 'production'
    or state not in ('approved', 'deploying', 'verifying', 'complete')
    or (provider = 'cloudflare-workers'
        and provider_version_id is not null
        and provider_version_id ~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
  ) not valid;

alter table ops.deployment
  drop constraint if exists production_deployment_requires_provider_version;

alter table ops.deployment
  add constraint production_deployment_requires_provider_version check (
    environment <> 'production'
    or (provider = 'cloudflare-workers'
        and provider_version_id is not null
        and provider_version_id ~
          '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
  ) not valid;

comment on constraint production_promotion_requires_provider_version
  on ops.release is
  'Program 5: a promoted Production release records the Cloudflare Workers provider and immutable provider version; drafts/candidates and history may still collect it.';

comment on constraint production_deployment_requires_provider_version
  on ops.deployment is
  'Program 5: every new Production deployment receipt names the Cloudflare Workers provider and provider version that was observed.';

comment on column ops.release.provider is
  'Program 5 provider identity. Production promoted states are Cloudflare Workers and are frozen with provider_version_id.';

comment on column ops.release.provider_version_id is
  'Program 5 immutable Cloudflare Workers version identifier approved for a Production release.';

comment on column ops.deployment.provider is
  'Program 5 provider identity observed for this deployment receipt.';

comment on column ops.deployment.provider_version_id is
  'Program 5 immutable provider version identifier observed for this deployment receipt.';

-- A Production receipt must describe the same provider/version as the exact
-- release already enforced by 0169, without weakening its approval-expiry,
-- environment, or SHA checks.
create or replace function ops.deployment_requires_a_live_approval()
returns trigger
language plpgsql
as $$
declare
  r record;
begin
  if new.environment <> 'production' then
    return new;
  end if;
  if new.release_id is null then
    raise exception 'a production deployment must name its release (ops.release), '
                    'because a deploy nobody can trace to an approved plan is the '
                    'exact gap P0-1 closes';
  end if;
  select state, approval_expires_at, release_key, git_sha, environment,
         provider, provider_version_id into r
    from ops.release where id = new.release_id;
  if r.state not in ('approved','deploying','verifying','complete') then
    raise exception 'release % is %, not approved — promotion refused',
                    r.release_key, r.state;
  end if;
  if r.approval_expires_at is null or r.approval_expires_at <= now() then
    raise exception 'release % has an expired approval (%) — re-approve before '
                    'promoting', r.release_key, r.approval_expires_at;
  end if;
  if r.environment is distinct from new.environment then
    raise exception 'release % targets %, not deployment environment % — promotion refused',
                    r.release_key, r.environment, new.environment;
  end if;
  if r.git_sha is distinct from new.git_sha then
    raise exception 'release % binds git SHA %, not deployment SHA % — promotion refused',
                    r.release_key, r.git_sha, new.git_sha;
  end if;
  if r.provider is distinct from new.provider then
    raise exception 'release % binds provider %, not deployment provider % — promotion refused',
                    r.release_key, r.provider, new.provider;
  end if;
  if r.provider_version_id is distinct from new.provider_version_id then
    raise exception 'release % binds provider version %, not deployment provider version % — promotion refused',
                    r.release_key, r.provider_version_id, new.provider_version_id;
  end if;
  return new;
end $$;

-- Provider version can be collected during draft/candidate work, but once a
-- Production release or deployment is promoted it cannot be silently swapped.
create or replace function ops.release_provider_identity_is_immutable()
returns trigger
language plpgsql
as $$
begin
  if old.environment = 'production'
     and old.state in ('approved', 'deploying', 'verifying', 'complete')
     and (new.provider is distinct from old.provider
          or new.provider_version_id is distinct from old.provider_version_id) then
    raise exception 'Production release provider identity is immutable after approval';
  end if;
  return new;
end $$;

create or replace function ops.deployment_provider_identity_is_immutable()
returns trigger
language plpgsql
as $$
begin
  if old.environment = 'production'
     and old.state in ('deploying', 'verifying', 'complete')
     and (new.provider is distinct from old.provider
          or new.provider_version_id is distinct from old.provider_version_id) then
    raise exception 'Production deployment provider identity is immutable after promotion begins';
  end if;
  return new;
end $$;

drop trigger if exists release_provider_identity_immutable on ops.release;
create trigger release_provider_identity_immutable
before update of provider, provider_version_id on ops.release
for each row execute function ops.release_provider_identity_is_immutable();

drop trigger if exists deployment_provider_identity_immutable on ops.deployment;
create trigger deployment_provider_identity_immutable
before update of provider, provider_version_id on ops.deployment
for each row execute function ops.deployment_provider_identity_is_immutable();

-- Completion is an observation of the same provider version, SHA, target, and
-- live read-back—not merely any finished Production deployment for the release.
create or replace function ops.release_completion_requires_a_read_back()
returns trigger
language plpgsql
as $$
begin
  if new.state = 'complete' and old.state is distinct from 'complete' then
    if not exists (
      select 1 from ops.deployment d
       where d.release_id = new.id
         and d.environment = 'production'
         and d.state = 'complete'
         and d.read_back_at is not null
         and d.git_sha = new.git_sha
         and d.provider = new.provider
         and d.provider_version_id = new.provider_version_id
    ) then
      raise exception 'release % cannot be complete: no attached Production deployment '
                      'completed a read-back for its recorded provider version and git SHA. '
                      'Shipped is not the same as serving.', new.release_key;
    end if;
  end if;
  return new;
end $$;

comment on function ops.release_completion_requires_a_read_back() is
  'Program 5: complete requires an attached Production deployment in complete state, with read-back and the release provider, provider version, and git SHA.';

commit;
