-- 0502_release_candidate_authority_provenance.sql
-- A RELEASE CANDIDATE'S MAKER BECOMES A FACT THE DATABASE ASSERTS, not a pair of
-- strings whichever role holds the ledger writer credential typed into a row.
--
-- WHAT THE SEVENTH REVIEW ROUND FOUND. `tools/ops-record.py release candidate`
-- derived its maker honestly -- `ops.authority_actor_slug()` over the authority
-- connection, the same function `release approve` opens with -- and then CLOSED
-- that connection and inserted the row over the generic ledger writer. Two
-- consequences followed, and both are closed here rather than in the wrapper:
--
--   1. The derivation was not the row's provenance. Any role holding INSERT on
--      ops.release could write `source_kind = 'wrapper'` beside
--      `maker_verification_ref = 'ops.authority-principal:joe'` itself, and the
--      Gate Zero seam store, which trusts exactly that pair, would have read it
--      back as the authenticated record of who made the build.
--
--   2. Nothing in the database said the pair came from an authority session, so
--      no reader could tell a derived row from a typed one. A predicate over two
--      caller-writable text columns is a naming convention, not a control.
--
-- THE SHAPE. One SECURITY DEFINER door, `ops.record_release_candidate()`,
-- executable only by carr_authority; it asks `ops.authority_actor_slug()` and
-- inserts the row in the SAME statement on the SAME connection, so the derived
-- maker and the row it lands in cannot come apart. A new column,
-- `maker_authority_verified`, is the fact the reader keys on, and a BEFORE
-- trigger makes it unforgeable: it is settable only from a session whose
-- session_user IS an admitted human authority principal, which the ledger writer
-- credential can never be, whatever it puts in the two text columns.
--
-- AND EXACTLY ONE PER REVISION. `release_authority_candidate_sha_uniq` is the
-- cardinality the Gate Zero producer's subject-maker seat needs: a revision that
-- resolved to two authenticated candidate rows would leave a reader picking one,
-- and an oracle that picks its own subject maker is not reading a record.

-- NO EXPLICIT TRANSACTION CONTROL: from 0339 onward tools/migrate.py wraps the
-- whole file in ONE transaction, proof blocks included, so a failing proof below
-- rolls this DDL back with it.

-- ── the fact a reader can key on ─────────────────────────────────────────────
alter table ops.release
  add column if not exists maker_authority_verified boolean not null default false;

comment on column ops.release.maker_authority_verified is
  'TRUE only for a row inserted through ops.record_release_candidate() from a '
  'session whose session_user is an admitted human authority principal. The '
  'a_release_candidate_authority_provenance trigger is what makes that true: no '
  'other role can set this column, and no update may move it. maker_actor and '
  'maker_verification_ref are ordinary text and always were -- this column is '
  'the reason a reader may believe them. Existing rows stay FALSE on purpose: '
  'they were written before the door existed and their pair was forgeable, so '
  'the Gate Zero seam store does not read them.';

-- ── the guard that makes the column mean something ───────────────────────────
create or replace function ops.release_candidate_authority_provenance()
returns trigger
language plpgsql
as $$
declare
  v_owner      text;
  v_privileged boolean;
  v_slug       text;
begin
  -- THE PRIVILEGED SESSION IS THE TABLE'S OWNER, and that is what a SECURITY
  -- DEFINER door authenticates as: current_user becomes the function's owner for
  -- the duration of the call. carr_writer and carr_jobs are neither, so this
  -- boolean is the line between "came through the door" and "did not".
  select pg_get_userbyid(relowner) into v_owner
    from pg_class where oid = 'ops.release'::regclass;
  v_privileged := pg_has_role(current_user, v_owner, 'usage');

  if tg_op = 'INSERT' then
    if new.state = 'candidate' and not v_privileged then
      raise exception using errcode = '42501',
        message = format('a release candidate is filed through '
                         'ops.record_release_candidate() on a human authority '
                         'credential; role %L may not insert one directly',
                         current_user);
    end if;

    if new.maker_authority_verified
       or new.maker_verification_ref like 'ops.authority-principal:%' then
      if not v_privileged then
        raise exception using errcode = '42501',
          message = format('the authenticated-maker marker is written only by '
                           'ops.record_release_candidate(); role %L may not '
                           'write it', current_user);
      end if;
      -- AND THE DOOR IS NOT ENOUGH BY ITSELF. The owner reaches this branch too,
      -- so the marker still costs an authority SESSION: authority_actor_slug()
      -- reads session_user, which SECURITY DEFINER does not change, and raises
      -- for any principal that is not carr_authority_joe or carr_authority_dell.
      v_slug := ops.authority_actor_slug();
      if new.maker_actor is distinct from v_slug then
        raise exception using errcode = '42501',
          message = format('the recorded maker %L is not the authority principal '
                           'this session authenticated as (%L)',
                           new.maker_actor, v_slug);
      end if;
      if new.maker_verification_ref
         is distinct from 'ops.authority-principal:' || v_slug then
        raise exception using errcode = '42501',
          message = 'the maker verification ref must be the derivation marker for '
                    'this session''s own authority principal';
      end if;
      if new.source_kind <> 'wrapper' or new.state <> 'candidate' then
        raise exception using errcode = '42501',
          message = 'the authenticated-maker marker belongs only to a wrapper-'
                    'written release candidate';
      end if;
      new.maker_authority_verified := true;
    else
      new.maker_authority_verified := false;
    end if;
    return new;
  end if;

  -- UPDATE: the provenance is written once and never moves.
  if old.maker_authority_verified then
    if new.maker_actor is distinct from old.maker_actor
       or new.maker_verification_ref is distinct from old.maker_verification_ref
       or new.maker_authority_verified is distinct from old.maker_authority_verified then
      raise exception using errcode = '42501',
        message = 'the authenticated maker provenance on a release row is immutable';
    end if;
  elsif new.maker_authority_verified then
    raise exception using errcode = '42501',
      message = 'maker_authority_verified is written once, at candidacy, by '
                'ops.record_release_candidate(); it cannot be added to an '
                'existing row';
  end if;
  return new;
end $$;

comment on function ops.release_candidate_authority_provenance() is
  'Makes ops.release.maker_authority_verified unforgeable, and refuses a direct '
  'candidate insert from any role that is not the table owner: candidacy is '
  'ops.record_release_candidate()''s act. Named a_ so it fires before every '
  'other row trigger on this table.';

drop trigger if exists a_release_candidate_authority_provenance on ops.release;
create trigger a_release_candidate_authority_provenance
  before insert or update on ops.release
  for each row execute function ops.release_candidate_authority_provenance();

-- ── exactly one authenticated candidate per revision ─────────────────────────
create unique index if not exists release_authority_candidate_sha_uniq
  on ops.release (git_sha) where maker_authority_verified;

comment on index ops.release_authority_candidate_sha_uniq is
  'The Gate Zero producer reads its subject maker back by git_sha alone. Two '
  'authenticated candidate rows for one revision would leave that reader '
  'choosing between them, so the second one is refused here instead.';

-- ── the door ─────────────────────────────────────────────────────────────────
create or replace function ops.record_release_candidate(
  p_correlation_id            uuid,
  p_release_key               text,
  p_service_key               text,
  p_environment               text,
  p_git_sha                   text,
  p_provider                  text,
  p_provider_version_id       text,
  p_performance_budget_ref    text,
  p_performance_budget_ms     integer,
  p_recovery_strategy         text,
  p_artifact_digest           text,
  p_dependency_lock_digest    text,
  p_sbom_ref                  text,
  p_migration_set             text[],
  p_schema_highest_migration  text,
  p_schema_applied_count      integer,
  p_schema_ledger_sha256      text,
  p_config_fingerprint        text,
  p_declared_env_differences  text,
  p_asset_versions            jsonb,
  p_verifier_actor            text,
  p_verifier_evidence_ref     text,
  p_test_evidence_ref         text,
  p_security_evidence_ref     text,
  p_rollback_ready            boolean,
  p_rollback_plan_ref         text,
  p_work_request_ref          text,
  p_plan_hash                 text,
  p_expires_at                timestamptz
) returns table (candidate_id uuid, candidate_key text,
                 candidate_maker text, candidate_verification_ref text)
language plpgsql security definer set search_path = ops, public, pg_temp
as $$
declare
  v_slug    text;
  v_ref     text;
  v_service uuid;
  v_id      uuid;
begin
  -- WHO IS ASKING, decided by the credential and not by an argument. There is no
  -- maker parameter on this function and there is nowhere to put one.
  v_slug := ops.authority_actor_slug();
  v_ref  := 'ops.authority-principal:' || v_slug;

  select id into v_service from ops.service where key = p_service_key;
  if v_service is null then
    raise exception 'no service registered with key %; add it to '
                    'ops/config/services.json and run sync-registry', p_service_key
      using errcode = '23503';
  end if;

  begin
    insert into ops.release
      (correlation_id, release_key, service_id, environment, state, git_sha,
       provider, provider_version_id,
       performance_budget_ref, performance_budget_ms, recovery_strategy,
       artifact_digest, dependency_lock_digest, sbom_ref, migration_set,
       schema_highest_migration, schema_applied_count, schema_ledger_sha256,
       config_fingerprint, declared_env_differences, asset_versions,
       maker_actor, maker_verification_ref, maker_authority_verified,
       verifier_actor, verifier_evidence_ref,
       test_evidence_ref, security_evidence_ref,
       rollback_ready, rollback_plan_ref, work_request_ref, plan_hash,
       source_kind, source_ref, expires_at)
    values
      (p_correlation_id, p_release_key, v_service, p_environment, 'candidate',
       p_git_sha, p_provider, p_provider_version_id,
       p_performance_budget_ref, p_performance_budget_ms, p_recovery_strategy,
       p_artifact_digest, p_dependency_lock_digest, p_sbom_ref, p_migration_set,
       p_schema_highest_migration, p_schema_applied_count, p_schema_ledger_sha256,
       p_config_fingerprint, p_declared_env_differences, p_asset_versions,
       v_slug, v_ref, true,
       p_verifier_actor, p_verifier_evidence_ref,
       p_test_evidence_ref, p_security_evidence_ref,
       coalesce(p_rollback_ready, false), p_rollback_plan_ref, p_work_request_ref,
       p_plan_hash, 'wrapper', 'tools/release-manifest.py', p_expires_at)
    returning id into v_id;
  exception when unique_violation then
    raise exception 'revision % already carries an authenticated release '
                    'candidate; exactly one may exist, so this build was not '
                    'filed', p_git_sha
      using errcode = '23505';
  end;

  return query select v_id, p_release_key, v_slug, v_ref;
end $$;

comment on function ops.record_release_candidate(
  uuid, text, text, text, text, text, text, text, integer, text, text, text,
  text, text[], text, integer, text, text, text, jsonb, text, text, text, text,
  boolean, text, text, text, timestamptz) is
  'The ONE door onto a release candidate row. Derives the maker from '
  'ops.authority_actor_slug() and inserts the row in the same call on the same '
  'connection, so the derivation and the row cannot come apart. Executable by '
  'carr_authority and by nobody else.';

revoke all on function ops.record_release_candidate(
  uuid, text, text, text, text, text, text, text, integer, text, text, text,
  text, text[], text, integer, text, text, text, jsonb, text, text, text, text,
  boolean, text, text, text, timestamptz) from public;

-- WHO MAY OPEN IT, AND WHY NO ROLE MAY YET. `revoke all ... from public` above
-- leaves this function executable by its owner alone, and that is the whole of
-- its ACL on purpose.
--
-- The one line this migration deliberately does NOT carry is
--
--     grant execute on function ops.record_release_candidate(...) to carr_authority;
--
-- because it is a NEW DB MUTATION CAPABILITY, and SIEP-11 seals those. Measured
-- on a disposable PostgreSQL carrying every migration through this one: the
-- column, the guard trigger, the partial unique index and this function itself
-- move the sealed capability census by ZERO rows; that grant alone moves
-- secdef_execute from 462 to 463 and adds exactly one ingress key,
--
--     db-function-acl:ops.record_release_candidate(...):carr_authority:execute
--
-- which ops/siep11-, siep12- and siep18-*-local-pg-gate.py then refuse against
-- the v25 seal. Admitting it is a SCAC mutation-registry successor's act (v26)
-- and nothing else's -- there is no allowlist and no partial admission door --
-- and a registry successor is substrate work this branch may not spawn under
-- Joe's 2026-09-08 moratorium (decision 019146bd-15fb-4f5e-8849-ed63911469e0):
-- existing substrate work may finish, it may not spawn child work.
--
-- SO THE STATE IS STAGED AND FAIL-CLOSED, not half-finished. The forgery this
-- migration exists to close IS closed the moment it applies: no role can write
-- the marker the Gate Zero seam store trusts, so no forgeable row is read. What
-- waits on the successor is the ability to write a GENUINE one -- and until it
-- lands, tools/ops-record.py refuses at the door by name rather than falling
-- back to a connection that could forge it. The proof block below asserts that
-- state rather than leaving it to be discovered, and open loop #594 carries the
-- admission with the measurement above.



-- ── proof, in the same run ───────────────────────────────────────────────────
-- Every invariant is PROVEN to bite (0114's rule, and 0131 kept it).
do $$
declare
  v_sig      text := 'ops.record_release_candidate(uuid,text,text,text,text,text,'
                     'text,text,integer,text,text,text,text,text[],text,integer,'
                     'text,text,text,jsonb,text,text,text,text,boolean,text,text,'
                     'text,timestamptz)';
  v_unique   boolean;
  v_partial  boolean;
  v_refused  boolean;
  v_message  text;
begin
  -- 1. the door is the authority's and nobody else's
  if has_function_privilege('carr_writer', v_sig, 'execute') then
    raise exception '0502 FAILED: carr_writer may execute the release-candidate door';
  end if;
  if has_function_privilege('carr_jobs', v_sig, 'execute') then
    raise exception '0502 FAILED: carr_jobs may execute the release-candidate door';
  end if;
  -- AND NOT YET THE AUTHORITY'S EITHER. This is the staged state described above,
  -- asserted so it is a decision on the record rather than a missing line: the
  -- capability is defined here and admitted by the SCAC registry successor.
  if has_function_privilege('carr_authority', v_sig, 'execute') then
    raise exception '0502 FAILED: the release-candidate door is executable before '
                    'its capability was admitted by a mutation-registry successor';
  end if;

  -- 2. the cardinality index is unique AND partial
  select i.indisunique, i.indpred is not null into v_unique, v_partial
    from pg_index i
    join pg_class c on c.oid = i.indexrelid
   where c.relname = 'release_authority_candidate_sha_uniq';
  if v_unique is null then
    raise exception '0502 FAILED: the one-candidate-per-revision index does not exist';
  end if;
  if not v_unique or not v_partial then
    raise exception '0502 FAILED: the one-candidate-per-revision index is not a '
                    'partial unique index (unique=%, partial=%)', v_unique, v_partial;
  end if;

  -- 3. NO SESSION MAY MARK A ROW AUTHENTICATED WITHOUT AN AUTHORITY PRINCIPAL,
  --    and this one runs as the migration's own owner: the widest privilege on
  --    this table still cannot mint the marker, because session_user is not an
  --    admitted human authority.
  v_refused := false;
  begin
    insert into ops.release
      (release_key, service_id, environment, state, git_sha, maker_actor,
       maker_verification_ref, maker_authority_verified, source_kind, source_ref)
    values ('0502-proof-forged-marker', gen_random_uuid(), 'production', 'candidate',
            repeat('b', 40), 'joe', 'ops.authority-principal:joe', true,
            'wrapper', '0502-proof');
  exception when others then
    v_refused := true;
    get stacked diagnostics v_message = message_text;
  end;
  if not v_refused then
    raise exception '0502 FAILED: a session that is not a human authority principal '
                    'wrote the authenticated-maker marker';
  end if;
  -- AND IT WAS THE GUARD THAT REFUSED IT, not some other constraint further down
  -- the insert. A probe that passes because its service_id had no referent proves
  -- nothing about provenance.
  if v_message not like '%not an admitted human authority principal%' then
    raise exception '0502 FAILED: the forged-marker insert was refused for the '
                    'wrong reason (%), so the provenance guard is not what stopped '
                    'it', v_message;
  end if;

  -- 4. and neither may it write the derivation marker in the text column alone
  v_refused := false;
  begin
    insert into ops.release
      (release_key, service_id, environment, state, git_sha, maker_actor,
       maker_verification_ref, source_kind, source_ref)
    values ('0502-proof-forged-ref', gen_random_uuid(), 'production', 'draft',
            repeat('c', 40), 'joe', 'ops.authority-principal:joe',
            'wrapper', '0502-proof');
  exception when others then
    v_refused := true;
    get stacked diagnostics v_message = message_text;
  end;
  if not v_refused then
    raise exception '0502 FAILED: the derivation marker is writable as ordinary text';
  end if;
  if v_message not like '%not an admitted human authority principal%' then
    raise exception '0502 FAILED: the forged-ref insert was refused for the wrong '
                    'reason (%), so the marker spelling is not what stopped it',
                    v_message;
  end if;

  raise notice '0502: the authenticated-maker marker cannot be minted without an '
               'authority principal, one revision may carry one authenticated '
               'candidate, and the door awaits its capability admission -- grant '
               'execute on ops.record_release_candidate(...) to carr_authority in '
               'the SCAC mutation-registry successor that seals it';
end $$;

-- 5. AND THE LEDGER WRITER IS REFUSED BY NAME. Split out of the block above
--    because it needs SET ROLE, which needs the migration runner to be a member
--    of carr_writer; where it is not, the run says so rather than passing quietly.
do $$
declare v_refused boolean := false; v_state text;
begin
  if not pg_has_role(current_user, 'carr_writer', 'member') then
    raise notice '0502: SKIPPED the carr_writer refusal probe — % is not a member '
                 'of carr_writer, so this run cannot assume that role. The grant '
                 'facts above are role-independent and did hold.', current_user;
    return;
  end if;
  begin
    -- DYNAMIC ON PURPOSE, and this is the only reason: tools/schema_snapshot_grants.py
    -- refuses a pending migration that names carr_writer OUTSIDE a parsed ACL
    -- statement, so the canonical grant plan stays derivable from GRANT/REVOKE
    -- alone. This migration grants that role nothing; the name appears here as a
    -- probe subject, in a string literal, exactly as it does in the privilege
    -- questions above.
    execute 'set local role carr_writer';
    insert into ops.release
      (release_key, service_id, environment, state, git_sha, maker_actor,
       source_kind, source_ref)
    values ('0502-proof-writer-candidate', gen_random_uuid(), 'production',
            'candidate', repeat('d', 40), 'joe', 'wrapper', '0502-proof');
  exception when others then
    v_refused := true;
    get stacked diagnostics v_state = returned_sqlstate;
  end;
  reset role;
  if not v_refused then
    raise exception '0502 FAILED: carr_writer filed a release candidate directly';
  end if;
  if v_state <> '42501' then
    raise exception '0502 FAILED: carr_writer''s candidate insert was refused for '
                    'the wrong reason (sqlstate %), so the provenance guard is not '
                    'what stopped it', v_state;
  end if;
  raise notice '0502: carr_writer cannot file a release candidate (%), so the '
               'ledger writer credential can no longer assert who made a build',
               v_state;
end $$;
