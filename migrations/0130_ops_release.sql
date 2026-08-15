-- 0130_ops_release.sql
-- P0-1, CANONICAL RELEASE TRUTH: the one release object, and the teeth that
-- make it mean something.
--
-- THE ACCEPTANCE, verbatim from the Phase 0 action register: "identical
-- artifact rebuild from recorded SHA; seeded failures block promotion; release
-- record links code/schema/config/tests/approval/deploy/verification." The
-- executable form of that sentence was written BEFORE this file and lives at
-- ops/p0-1-release-gate.py (rule e65efc68). Everything below exists to make
-- those seven assertions pass.
--
-- WHAT WAS MISSING, verified 2026-08-15 by reading the tree rather than the
-- roadmap. Migration 0115 gave ops.deployment a `release_ref text` column and
-- there has never been a release table for it to point at. The Program 0
-- inventory named the hole in one sentence — "no one release object joins code,
-- configuration, migrations, generated assets, security, tests, approval,
-- deploy, and verification" — and the consequence is that a deploy today can
-- say WHAT SHA it shipped and nothing else. Not the digest that would let
-- anyone rebuild it. Not the migrations that rode along. Not the tests that
-- passed, who approved it, against what plan, or what came back when it landed.
-- Every one of those exists somewhere; none of them is attached to the thing
-- that shipped, which is why the maturity baseline calls production evidence
-- "fragmented rather than attached to one release."
--
-- WHY ops AND NOT public, unchanged from 0114 and 0115: one database, one
-- store, a namespace an ops-scoped role can hold without ever holding business
-- tables. A release is operational metadata about the machine. No business
-- payload lives here — every piece of evidence is a REFERENCE, never a copy.
--
-- THE ONE DESIGN DECISION THAT MATTERS MOST: THE APPROVAL IS A PERISHABLE
-- OBJECT, NOT A FLAG. A boolean `approved` column would keep its value through
-- any subsequent edit, which is precisely the failure the promotion rules name:
-- "Material plan revision invalidates prior approval." So approval here is four
-- columns that live and die together — approver, plan hash, approved_at,
-- expiry — and a trigger that DESTROYS all four the moment the plan hash moves.
-- Fail closed: the alternative is a release shipping under a yes that was given
-- to a different plan.

begin;

-- ── the release object ───────────────────────────────────────────────────────
create table if not exists ops.release (
  id             uuid primary key default gen_random_uuid(),

  -- Threads to the run, deployment and incident chain of 0115. A release that
  -- cannot be correlated to the journey it broke is an island.
  correlation_id uuid not null default gen_random_uuid(),

  release_key    text not null unique,
  service_id     uuid not null references ops.service(id),
  environment    text not null
    check (environment in ('local','rehearsal','staging','production')),

  state          text not null default 'draft'
    check (state in ('draft','candidate','approved','deploying','verifying',
                     'complete','failed','superseded','abandoned')),

  -- ── CODE ──────────────────────────────────────────────────────────────────
  -- The SHA is the identity. Everything else about the code is derived from it,
  -- which is what makes "identical artifact rebuild from recorded SHA" a
  -- question anyone can ask later rather than a claim only the builder can make.
  git_sha        text not null
    check (git_sha ~ '^[0-9a-f]{40}$'),
  artifact_digest        text,
  dependency_lock_digest text,
  sbom_ref               text,

  -- ── SCHEMA ────────────────────────────────────────────────────────────────
  migration_set            text[],
  schema_highest_migration text,

  -- ── CONFIGURATION ─────────────────────────────────────────────────────────
  config_fingerprint       text,
  declared_env_differences text,

  -- ── GENERATED ASSETS ──────────────────────────────────────────────────────
  asset_versions jsonb,

  -- ── TESTS, SECURITY, VERIFICATION ─────────────────────────────────────────
  maker_actor            text not null,
  maker_verification_ref text,
  test_evidence_ref      text,
  security_evidence_ref  text,
  verifier_actor         text,
  verifier_evidence_ref  text,

  -- ── ROLLBACK READINESS ────────────────────────────────────────────────────
  rollback_ready    boolean not null default false,
  rollback_plan_ref text,

  -- ── APPROVAL, the perishable object ───────────────────────────────────────
  plan_hash            text,
  approved_by_actor    text,
  approved_at          timestamptz,
  approval_expires_at  timestamptz,

  work_request_ref text,

  failure_class text,
  superseded_by uuid references ops.release(id),

  -- Same provenance and freshness shape every other ops table carries, so the
  -- manifest can answer "where did this come from and how old is it" in the
  -- same words as every other operational row.
  source_kind text not null
    check (source_kind in ('collector','registry','wrapper','operator')),
  source_ref  text not null,
  observed_at timestamptz not null default now(),
  expires_at  timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  ended_at   timestamptz,

  -- 2. APPROVAL DEMANDS THE REBUILD EVIDENCE. Without the digest and the lock,
  -- "identical artifact rebuild from recorded SHA" cannot even be attempted, so
  -- the evidence has to exist before the approval, never after it.
  constraint an_approved_release_can_be_rebuilt check (
    state in ('draft','candidate','abandoned')
      or (artifact_digest is not null and dependency_lock_digest is not null)
  ),

  -- 3. AN APPROVAL NAMES ITS APPROVER, ITS PLAN AND ITS EXPIRY. An approval
  -- that never goes stale is how a plan-hash check gets quietly bypassed by
  -- time.
  constraint an_approved_release_names_its_approval check (
    state in ('draft','candidate','abandoned')
      or (plan_hash is not null and approved_by_actor is not null
          and approved_at is not null and approval_expires_at is not null)
  ),
  constraint an_approval_expires_after_it_is_given check (
    approval_expires_at is null or approved_at is null
      or approval_expires_at > approved_at
  ),

  -- The tests and the security pass are evidence of promotion-readiness, so
  -- they bind at the same moment the approval does.
  constraint an_approved_release_carries_its_evidence check (
    state in ('draft','candidate','abandoned')
      or (test_evidence_ref is not null and security_evidence_ref is not null
          and maker_verification_ref is not null)
  ),

  -- 6. THE VERIFIER IS NOT THE MAKER. Rule 2b66211d and the engineering work
  -- contract both require a verifier working from the artifact rather than the
  -- maker's summary. Structural beats instructional: a session cannot forget.
  constraint independent_verification_is_not_the_maker check (
    verifier_actor is null or verifier_actor <> maker_actor
  ),
  constraint verification_evidence_names_its_verifier check (
    (verifier_actor is null) = (verifier_evidence_ref is null)
  ),

  constraint a_failed_release_names_its_class check (
    state <> 'failed' or failure_class is not null
  ),
  constraint a_terminal_release_has_ended check (
    state not in ('complete','failed','abandoned','superseded')
      or ended_at is not null
  ),
  constraint a_superseded_release_names_its_successor check (
    state <> 'superseded' or superseded_by is not null
  )
);

comment on table ops.release is
  'P0-1 canonical release truth: the ONE object joining code, schema, config, '
  'generated assets, tests, security, approval, deployment and verification. '
  'ops.deployment.release_id points here; before 0130 it pointed at nothing.';

comment on column ops.release.plan_hash is
  'The hash of the plan the approver actually read. Changing it DESTROYS the '
  'approval (trigger release_plan_revision_invalidates_approval), because the '
  'promotion rule is "material plan revision invalidates prior approval" and a '
  'flag that survives the plan it approved is the failure, not the control.';

comment on column ops.release.artifact_digest is
  'Digest of the built artifact, produced by tools/release-manifest.py from the '
  'recorded SHA. Recomputing it from that SHA and getting this value back IS '
  'the "identical artifact rebuild" half of P0-1 acceptance.';

create index if not exists release_correlation_idx on ops.release (correlation_id);
create index if not exists release_service_state_idx on ops.release (service_id, state);
create index if not exists release_sha_idx on ops.release (git_sha);

-- ── the deployment finally points at something ───────────────────────────────
-- release_ref (0115) was a text column with no referent. It stays, unread, so
-- nothing that wrote it breaks; release_id is the real edge from here on.
alter table ops.deployment
  add column if not exists release_id uuid references ops.release(id);

create index if not exists deployment_release_idx on ops.deployment (release_id);

comment on column ops.deployment.release_ref is
  'SUPERSEDED by release_id (0130). Kept because nothing silently rots (rule '
  'def3e84e); no new writer should set it.';

-- ── 4. material plan revision invalidates prior approval ─────────────────────
create or replace function ops.release_plan_revision_invalidates_approval()
returns trigger
language plpgsql
as $$
begin
  if new.plan_hash is distinct from old.plan_hash
     and old.state in ('approved','deploying','verifying') then
    new.state               := 'candidate';
    new.approved_by_actor   := null;
    new.approved_at         := null;
    new.approval_expires_at := null;
    raise notice 'release %: the plan changed, so the approval is gone. Re-approve '
                 'against the new plan hash.', old.release_key;
  end if;
  new.updated_at := now();
  return new;
end $$;

create trigger release_plan_revision_invalidates_approval
  before update on ops.release
  for each row
  execute function ops.release_plan_revision_invalidates_approval();

-- ── 5. seeded failures block promotion ───────────────────────────────────────
-- A production deployment may only name a release that is approved and whose
-- approval has not expired. This is the database half of the phase gate;
-- ops/ci.sh is the other half and already seeds one failure per check class.
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
  select state, approval_expires_at, release_key into r
    from ops.release where id = new.release_id;
  if r.state not in ('approved','deploying','verifying','complete') then
    raise exception 'release % is %, not approved — promotion refused',
                    r.release_key, r.state;
  end if;
  if r.approval_expires_at is null or r.approval_expires_at <= now() then
    raise exception 'release % has an expired approval (%) — re-approve before '
                    'promoting', r.release_key, r.approval_expires_at;
  end if;
  return new;
end $$;

create trigger deployment_requires_a_live_approval
  before insert or update on ops.deployment
  for each row
  execute function ops.deployment_requires_a_live_approval();

-- ── 7. completion requires a production read-back ────────────────────────────
-- ops.deployment already refuses to call itself complete without a read-back.
-- This extends the same bar upward, so "released" and "proven serving" cannot
-- drift apart the way they did on 2026-08-13, when a real deploy sat behind a
-- stale marker for two hours and a verification pass nearly called it unshipped.
create or replace function ops.release_completion_requires_a_read_back()
returns trigger
language plpgsql
as $$
begin
  if new.state = 'complete' and old.state is distinct from 'complete' then
    if not exists (
      select 1 from ops.deployment d
       where d.release_id = new.id and d.read_back_at is not null
    ) then
      raise exception 'release % cannot be complete: no deployment attached to it '
                      'recorded a read-back. Shipped is not the same as serving.',
                      new.release_key;
    end if;
  end if;
  return new;
end $$;

create trigger release_completion_requires_a_read_back
  before update on ops.release
  for each row
  execute function ops.release_completion_requires_a_read_back();

-- ── 1. one query returns the whole manifest ──────────────────────────────────
-- Seven classes, one row, each carrying where it came from and how old it is.
-- The freshness function is 0115's, so a release answers the staleness question
-- in exactly the same words as a run, a deployment and a service.
create or replace view ops.v_release_manifest as
select
  r.id                       as release_id,
  r.release_key,
  r.correlation_id,
  s.key                      as service_key,
  r.environment,
  r.state,

  r.git_sha                  as code_git_sha,
  r.artifact_digest          as code_artifact_digest,
  r.dependency_lock_digest   as code_dependency_lock_digest,
  r.sbom_ref                 as code_sbom_ref,

  r.schema_highest_migration,
  r.migration_set,

  r.config_fingerprint,
  r.declared_env_differences,
  r.asset_versions,

  r.test_evidence_ref,
  r.security_evidence_ref,
  r.maker_actor,
  r.maker_verification_ref,

  r.plan_hash                as approval_plan_hash,
  r.approved_by_actor,
  r.approved_at,
  r.approval_expires_at,
  case
    when r.approved_at is null then 'unapproved'
    when r.approval_expires_at <= now() then 'expired'
    else 'live'
  end                        as approval_status,

  d.id                       as deployment_id,
  d.state                    as deploy_state,
  d.read_back_at             as deploy_read_back_at,
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
  ops.freshness(r.observed_at, r.expires_at) as freshness
from ops.release r
join ops.service s on s.id = r.service_id
left join lateral (
  select * from ops.deployment d2
   where d2.release_id = r.id
   order by d2.observed_at desc
   limit 1
) d on true;

comment on view ops.v_release_manifest is
  'P0-1 assertion 1: ONE query returns code, schema, config, tests, approval, '
  'deploy and verification for one release. Seven lookups was the fragmentation '
  'this replaces.';

-- ── least privilege, same shape as 0115 ──────────────────────────────────────
grant select on ops.release, ops.v_release_manifest to carr_reader;
grant select, insert, update on ops.release to carr_writer;
grant select on ops.v_release_manifest to carr_writer;
grant select on ops.release to carr_jobs;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
-- Every invariant is PROVEN to bite. A constraint nobody has seen refuse
-- anything is a comment with punctuation (0114's rule, kept).
do $$
declare
  v_service uuid;
  v_release uuid;
  v_state   text;
  v_approver text;
begin
  insert into ops.service (key, name, family, criticality, owner_actor)
    values ('migration-0130-proof', 'proof', 'Platform', 'low', 'system')
    returning id into v_service;

  -- 1. An approved release with no artifact digest is refused.
  begin
    insert into ops.release (release_key, service_id, environment, state, git_sha,
                             maker_actor, plan_hash, approved_by_actor, approved_at,
                             approval_expires_at, test_evidence_ref,
                             security_evidence_ref, maker_verification_ref,
                             source_kind, source_ref)
      values ('0130-proof-1', v_service, 'production', 'approved', repeat('a', 40),
              'system', 'plan:x', 'joe', now(), now() + interval '1 day',
              't', 's', 'm', 'wrapper', 'proof');
    raise exception '0130 FAILED: an approved release was accepted with no artifact digest';
  exception when check_violation then null;
  end;

  -- 2. An approved release with no approver is refused.
  begin
    insert into ops.release (release_key, service_id, environment, state, git_sha,
                             maker_actor, artifact_digest, dependency_lock_digest,
                             test_evidence_ref, security_evidence_ref,
                             maker_verification_ref, source_kind, source_ref)
      values ('0130-proof-2', v_service, 'production', 'approved', repeat('a', 40),
              'system', 'sha256:x', 'sha256:y', 't', 's', 'm', 'wrapper', 'proof');
    raise exception '0130 FAILED: an approved release was accepted with no approval';
  exception when check_violation then null;
  end;

  -- 3. The verifier may not be the maker.
  begin
    insert into ops.release (release_key, service_id, environment, state, git_sha,
                             maker_actor, verifier_actor, verifier_evidence_ref,
                             source_kind, source_ref)
      values ('0130-proof-3', v_service, 'local', 'candidate', repeat('a', 40),
              'claude', 'claude', 'e', 'wrapper', 'proof');
    raise exception '0130 FAILED: the maker was accepted as its own verifier';
  exception when check_violation then null;
  end;

  -- 4. A plan revision destroys the approval.
  insert into ops.release (release_key, service_id, environment, state, git_sha,
                           maker_actor, artifact_digest, dependency_lock_digest,
                           test_evidence_ref, security_evidence_ref,
                           maker_verification_ref, plan_hash, approved_by_actor,
                           approved_at, approval_expires_at, source_kind, source_ref)
    values ('0130-proof-4', v_service, 'production', 'approved', repeat('a', 40),
            'system', 'sha256:x', 'sha256:y', 't', 's', 'm', 'plan:one', 'joe',
            now(), now() + interval '1 day', 'wrapper', 'proof')
    returning id into v_release;

  update ops.release set plan_hash = 'plan:two' where id = v_release;
  select state, approved_by_actor into v_state, v_approver
    from ops.release where id = v_release;
  if v_state <> 'candidate' or v_approver is not null then
    raise exception '0130 FAILED: a plan revision left the approval standing (state %, approver %)',
                    v_state, v_approver;
  end if;

  -- 5. A production deployment of an unapproved release is refused.
  begin
    insert into ops.deployment (service_id, environment, state, git_sha, release_id,
                                started_at, source_kind, source_ref)
      values (v_service, 'production', 'deploying', repeat('a', 40), v_release,
              now(), 'wrapper', 'proof');
    raise exception '0130 FAILED: an unapproved release was promoted to production';
  exception when raise_exception then null;
  end;

  -- 6. A release cannot complete with no deployment read-back.
  begin
    update ops.release set state = 'complete', ended_at = now() where id = v_release;
    raise exception '0130 FAILED: a release completed with no production read-back';
  exception when raise_exception then null;
  end;

  -- 7. And the control: with a read-back attached, completion IS accepted, or
  --    the trigger is simply refusing everything and proving nothing.
  update ops.release
     set state = 'approved', plan_hash = 'plan:two', approved_by_actor = 'joe',
         approved_at = now(), approval_expires_at = now() + interval '1 day'
   where id = v_release;

  insert into ops.deployment (service_id, environment, state, git_sha, release_id,
                              started_at, ended_at, read_back_at,
                              source_kind, source_ref)
    values (v_service, 'production', 'complete', repeat('a', 40), v_release,
            now(), now(), now(), 'wrapper', 'proof');

  update ops.release set state = 'complete', ended_at = now() where id = v_release;
  select state into v_state from ops.release where id = v_release;
  if v_state is distinct from 'complete' then
    raise exception '0130 FAILED: a release with a read-back could not complete (state %)',
                    v_state;
  end if;

  delete from ops.deployment where release_id = v_release;
  delete from ops.release where service_id = v_service;
  delete from ops.service where id = v_service;

  raise notice '0130: seven proofs passed — six invariants refused their violation, '
               'and a release with a production read-back completed';
end $$;
