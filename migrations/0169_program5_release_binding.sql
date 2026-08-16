-- 0169_program5_release_binding.sql
-- Program 5: bind promotion evidence to the exact release and Production target.
--
-- 0131 made a release traceable and required a live approval.  Its remaining
-- gaps were structural: an approved release could omit independent-attestation
-- and rollback evidence, and a production deployment could point at an approved
-- release for a different SHA or environment.  These controls live in the
-- database so every writer is subject to the same refusal.

begin;

-- Drafts and candidates remain evidence-collection objects.  Once a release
-- enters a promotion state, it must carry a named independent verifier and a
-- tested recovery path; a non-null boolean flag alone is not recovery evidence.
alter table ops.release
  drop constraint if exists promotion_release_requires_independent_attestation;

alter table ops.release
  add constraint promotion_release_requires_independent_attestation check (
    state not in ('approved', 'deploying', 'verifying', 'complete')
    or (verifier_actor is not null and verifier_evidence_ref is not null)
  ) not valid;

alter table ops.release
  drop constraint if exists promotion_release_requires_rollback_readiness;

alter table ops.release
  add constraint promotion_release_requires_rollback_readiness check (
    state not in ('approved', 'deploying', 'verifying', 'complete')
    or (rollback_ready and rollback_plan_ref is not null)
  ) not valid;

comment on constraint promotion_release_requires_independent_attestation
  on ops.release is
  'Program 5: approved through complete releases carry a named independent verifier and evidence; drafts and candidates may still collect it.';

comment on constraint promotion_release_requires_rollback_readiness
  on ops.release is
  'Program 5: approved through complete releases must name a ready rollback or forward-fix plan; a boolean alone is insufficient.';

-- Preserve 0131's live-approval/expiry rule and add exact release binding.  A
-- deployment row may not use a valid approval for one artifact to claim another
-- artifact, or use a release prepared for another environment.
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
  select state, approval_expires_at, release_key, git_sha, environment into r
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
  return new;
end $$;

-- Completing a release means the exact Production artifact was observed serving.
-- A staging/local read-back or a timestamp on a different SHA cannot close it.
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
    ) then
      raise exception 'release % cannot be complete: no attached Production deployment '
                      'completed a read-back for its recorded git SHA. Shipped is not '
                      'the same as serving.', new.release_key;
    end if;
  end if;
  return new;
end $$;

comment on function ops.release_completion_requires_a_read_back() is
  'Program 5: complete requires an attached Production deployment in complete state, with read-back and the release git SHA.';

commit;
