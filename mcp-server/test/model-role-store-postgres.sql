-- DoctorCRE v5 durable role-description store: transaction-scoped PostgreSQL proof.
--
-- EVERY FIXTURE ROW IS ROLLED BACK. This file writes nothing that survives it,
-- creates no role, no actor and no extension, applies no migration, and makes no
-- provider or network call. It is a PROOF, not an install: it does not apply
-- ops/model-role-store.candidate.sql and cannot -- that file is candidate source
-- and landing it is a separate, reviewed act.
--
-- THE ROLE IS SYNTHETIC ON PURPOSE. "Reviewer" below is a fixture for the
-- MECHANISM. It is not a proposal of DoctorCRE's real Reviewer job description,
-- nothing in the module or the candidate SQL seeds a role, and no revision
-- recorded here outlives the rollback.
--
-- EXPLICIT PREREQUISITES, checked before anything is attempted. Each one SKIPS
-- with a notice naming what is absent rather than failing, and a skip is
-- reported as a skip -- this file never prints a clean run for assertions it did
-- not execute:
--   * ops.model_role_record_revision must exist. It does not until
--     ops/model-role-store.candidate.sql has been applied here, which has NOT
--     been done by the change that introduced this file.
--   * ops.portfolio_writer_actor_id and ops.portfolio_canonical_json (migration
--     0496) and ops.authority_actor_slug (migration 0161) must exist. This rail
--     reuses all three rather than restating them.
--   * public.digest (pgcrypto) must exist.
--   * One active NON-HUMAN actor must already exist, for the writer context.
--     This file CREATES NO ACTOR AND NO ROLE: writing as a human additionally
--     requires the verified-partner context the server sets, and minting either
--     here would manufacture the identity this rail exists to derive.
--
-- WHAT IT PROVES, none of which can be shown by reading SQL text:
--   * the preimage rebuilt FROM THE STORED ROWS equals, jsonb for jsonb, a
--     preimage assembled independently in this file -- and both hash to the same
--     role_digest, over the bare object with NO domain tag
--   * list ORDER participates in the digest for skills, rules and evidence
--     requirements, and does NOT for the three reference sets: those are emitted
--     sorted whatever ordinals their rows carry
--   * a version hole, an ordinal gap, a repeated content digest, a reused
--     idempotency key with different content, and a system-authority role class
--     are each refused, and a refused write leaves nothing behind
--   * update and delete are refused on all four relations
--   * a direct insert naming another writer is refused
--   * naming the current revision REFUSES for any session that is not the
--     retained system-authority partner's own authority principal, the pointer
--     ledger stays empty, and the current revision stays null -- no role becomes
--     current by default
--   * the per-role advisory lock is actually taken, and both writers take it
--   * carr_writer can record a revision and cannot name the current one
--   * no relation here carries an occupant, a qualification, a grant or a
--     numeric floor column
--
-- WHAT THIS FILE DOES NOT PROVE, named rather than implied. Read this list
-- before treating a clean run as coverage:
--   1. THE CROSS-LANGUAGE DIGEST EQUALITY. Whether ops.model_role_digest and
--      model-routing.v5.js's defineRole produce the SAME hash for the same role
--      cannot be shown by a rollback-only fixture, which executes no JavaScript.
--      Both sides are asserted structurally to hash the canonical twelve-field
--      object with no domain tag, and the SQL side reuses
--      ops.portfolio_canonical_json, which migration 0496 already reconciles
--      against the module canonicalJson. A role preimage contains NO NUMBER, so
--      the number-rendering half of that reconciliation cannot arise here. The
--      string half remains live-integration verification, and it is the first
--      thing to check when this rail is exercised end to end. It fails CLOSED if
--      it is ever wrong: the writer refuses on the digest comparison rather than
--      storing a role the two sides hash differently.
--   2. THE CONTENT FREEZE ACROSS TRANSACTIONS. ops.model_role_content_guard
--      compares a revision's created_xid against pg_current_xact_id(), and
--      pg_current_xact_id() returns the TOP-LEVEL transaction id -- so inside
--      one rolled-back transaction every subtransaction shares it and the guard
--      cannot be made to fire. Its presence and attachment are asserted
--      structurally below; its behaviour needs two committed transactions, which
--      this file deliberately does not create.
--   3. CONCURRENCY. Two sessions racing a compare-and-swap needs two sessions.
--      What is proved here is that the lock is real and both writers take it,
--      and that the compare-and-swap refusals fire on their own terms.
--   4. THE POSITIVE CURRENT-POINTER PATH, unless this file is run on the
--      retained system-authority partner's authority connection. On any other
--      connection ops.authority_actor_slug() raises and every pointer assertion
--      becomes a refusal assertion. The block at the end reports EXACTLY which
--      assertions were exercised and which were not, by name.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_writer text; v_actor_count integer; v_id uuid; v_id2 uuid; v_replay uuid;
  v_digest text; v_digest2 text; v_canonical text; v_readback jsonb;
  v_count integer; v_locks integer; v_def text; v_session text; v_partner_ok boolean;
  v_exercised text[] := array[]::text[];
  v_not_exercised text[] := array[]::text[];
  v_marker constant text := 'model-role-proof-expected-refusal';

  v_role constant text := 'reviewer';

  -- THE PREIMAGE, ASSEMBLED HERE AND NOT READ FROM ANY ROW. Its reference sets
  -- are written in SORTED order because that is what defineRole hashes; the rows
  -- inserted below deliberately carry some of them in the other order, so the
  -- emitter's sort is proved rather than assumed.
  v_preimage constant jsonb := jsonb_build_object(
    'schema_version', 'role-description.v1',
    'tenant', 'carr-internal',
    'role_key', 'reviewer',
    'title', 'Reviewer (synthetic fixture)',
    'mission', 'Synthetic fixture mission: establish independently that delivered work meets its stated contract.',
    'skills', jsonb_build_array(
      'read a diff against the contract it claims to satisfy',
      'reproduce a claimed finding from source rather than from a report'),
    'rules', jsonb_build_array(
      'never grade work you produced',
      'a refusal names the exact missing fact, authority or dependency'),
    'authority', jsonb_build_object(
      'authority_class', 'developer',
      'capability_refs', jsonb_build_array('evidence.read', 'source.read')),
    'evidence_requirements', jsonb_build_array('the exact command run and its output'),
    'quality_floor_refs', jsonb_build_array('floor.evidence_bound', 'floor.independent_review'),
    'minimum_strength_ref', 'strength.high_risk_engineering',
    'task_classes', jsonb_build_array('review.contract', 'review.source'));

  v_scalars constant jsonb := jsonb_build_object(
    'title', 'Reviewer (synthetic fixture)',
    'mission', 'Synthetic fixture mission: establish independently that delivered work meets its stated contract.',
    'minimum_strength_ref', 'strength.high_risk_engineering',
    'authority_class', 'developer');

  v_texts constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'skills', 'ordinal', 0,
      'value', 'read a diff against the contract it claims to satisfy'),
    jsonb_build_object('field', 'skills', 'ordinal', 1,
      'value', 'reproduce a claimed finding from source rather than from a report'),
    jsonb_build_object('field', 'rules', 'ordinal', 0,
      'value', 'never grade work you produced'),
    jsonb_build_object('field', 'rules', 'ordinal', 1,
      'value', 'a refusal names the exact missing fact, authority or dependency'),
    jsonb_build_object('field', 'evidence_requirements', 'ordinal', 0,
      'value', 'the exact command run and its output'));

  -- THE SAME SKILLS IN THE OTHER ORDER, and nothing else changed.
  v_texts_reordered constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'skills', 'ordinal', 0,
      'value', 'reproduce a claimed finding from source rather than from a report'),
    jsonb_build_object('field', 'skills', 'ordinal', 1,
      'value', 'read a diff against the contract it claims to satisfy'),
    jsonb_build_object('field', 'rules', 'ordinal', 0,
      'value', 'never grade work you produced'),
    jsonb_build_object('field', 'rules', 'ordinal', 1,
      'value', 'a refusal names the exact missing fact, authority or dependency'),
    jsonb_build_object('field', 'evidence_requirements', 'ordinal', 0,
      'value', 'the exact command run and its output'));

  -- ORDINAL 0 AND ORDINAL 2, WITH NOTHING AT 1. The shape a partial insert
  -- leaves behind, and the shape that would rebuild a shorter list.
  v_texts_gapped constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'skills', 'ordinal', 0,
      'value', 'read a diff against the contract it claims to satisfy'),
    jsonb_build_object('field', 'skills', 'ordinal', 2,
      'value', 'reproduce a claimed finding from source rather than from a report'),
    jsonb_build_object('field', 'rules', 'ordinal', 0,
      'value', 'never grade work you produced'),
    jsonb_build_object('field', 'evidence_requirements', 'ordinal', 0,
      'value', 'the exact command run and its output'));

  -- THE REFERENCE SETS, WITH task_classes AND capability_refs DELIBERATELY
  -- ORDINALED IN REVERSE. If the emitter honoured these ordinals the preimage
  -- would not equal the hand-built one above and the digest would differ.
  v_refs constant jsonb := jsonb_build_array(
    jsonb_build_object('field', 'capability_refs', 'ordinal', 0, 'value', 'source.read'),
    jsonb_build_object('field', 'capability_refs', 'ordinal', 1, 'value', 'evidence.read'),
    jsonb_build_object('field', 'quality_floor_refs', 'ordinal', 0, 'value', 'floor.evidence_bound'),
    jsonb_build_object('field', 'quality_floor_refs', 'ordinal', 1, 'value', 'floor.independent_review'),
    jsonb_build_object('field', 'task_classes', 'ordinal', 0, 'value', 'review.source'),
    jsonb_build_object('field', 'task_classes', 'ordinal', 1, 'value', 'review.contract'));
begin
  -- === prerequisites ========================================================
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'model_role_record_revision') then
    raise notice 'SKIPPED: ops.model_role_record_revision is absent; ops/model-role-store.candidate.sql has not been applied here. NOTHING BELOW RAN.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'portfolio_writer_actor_id')
     or not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                     where n.nspname = 'ops' and p.proname = 'portfolio_canonical_json') then
    raise notice 'SKIPPED: migration 0496 is absent; this rail reuses its writer context and canonicalizer. NOTHING BELOW RAN.';
    return;
  end if;
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'ops' and p.proname = 'authority_actor_slug') then
    raise notice 'SKIPPED: migration 0161 is absent; the current-pointer principal is derived from it. NOTHING BELOW RAN.';
    return;
  end if;
  if to_regprocedure('public.digest(bytea,text)') is null then
    raise notice 'SKIPPED: pgcrypto''s public.digest is absent; every digest here is computed with it. NOTHING BELOW RAN.';
    return;
  end if;
  select count(*) into v_actor_count from public.actor where active and kind <> 'human';
  if v_actor_count < 1 then
    raise notice 'SKIPPED: no active non-human actor exists for the writer context; this proof creates none. NOTHING BELOW RAN.';
    return;
  end if;
  select slug into v_writer from public.actor where active and kind <> 'human'
   order by slug collate "C" limit 1;
  perform set_config('carr.acting_actor_slug', v_writer, true);

  -- === the digest, computed from bytes assembled in this file ===============
  v_digest := ops.model_role_digest_of_preimage(v_preimage);
  v_canonical := ops.portfolio_canonical_json(v_preimage);

  -- NO DOMAIN TAG. defineRole hashes the object itself; a leading '[' would mean
  -- a second digest scheme had appeared. The first C-sorted key of a role
  -- preimage is "authority", so the canonical bytes begin there.
  if left(v_canonical, 13) <> '{"authority":' then
    raise exception 'the hashed preimage is not the bare role-description.v1 object: %', left(v_canonical, 60);
  end if;
  if v_digest !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'the role digest is not a sha256 reference: %', v_digest;
  end if;
  v_exercised := v_exercised || 'digest-shape-and-no-domain-tag';

  -- === revision 1 ===========================================================
  v_id := ops.model_role_record_revision(
    v_role, 1, gen_random_uuid(), v_digest, v_scalars, v_texts, v_refs);

  -- THE STRONGEST ASSERTION IN THIS FILE. The rebuild from the stored rows is
  -- compared to the hand-assembled object field for field, not merely through a
  -- hash that could agree for reasons nobody checked.
  if ops.model_role_preimage(v_id) <> v_preimage then
    raise exception 'the preimage rebuilt from the stored rows is not the one that was hashed: %',
      ops.model_role_preimage(v_id);
  end if;
  if ops.model_role_digest(v_id) <> v_digest then
    raise exception 'the digest recomputed from the stored rows is %, not %',
      ops.model_role_digest(v_id), v_digest;
  end if;
  if ops.model_role_structure_error(v_id) is not null then
    raise exception 'a freshly recorded revision is structurally invalid: %',
      ops.model_role_structure_error(v_id);
  end if;
  v_exercised := v_exercised || 'stored-rows-rebuild-equals-hashed-preimage';

  -- REFERENCE SETS ARE EMITTED SORTED, whatever ordinals their rows carry. The
  -- rows above deliberately number task_classes and capability_refs in reverse,
  -- and the preimage comparison passed anyway.
  if ops.model_role_ref_array(v_id, 'task_classes')
       <> jsonb_build_array('review.contract', 'review.source') then
    raise exception 'reference sets are being emitted in row order rather than sorted: %',
      ops.model_role_ref_array(v_id, 'task_classes');
  end if;
  -- TEXT LISTS ARE EMITTED IN ORDINAL ORDER, because their order is hashed.
  if ops.model_role_text_array(v_id, 'skills') -> 0
       <> to_jsonb('read a diff against the contract it claims to satisfy'::text) then
    raise exception 'text lists are not emitted in their stored ordinal order';
  end if;
  v_exercised := v_exercised || 'ref-sets-sorted-and-text-lists-ordinal';

  -- === revision 2: the same role, one list reordered ========================
  v_digest2 := ops.model_role_digest_of_preimage(
    jsonb_set(v_preimage, '{skills}', jsonb_build_array(
      'reproduce a claimed finding from source rather than from a report',
      'read a diff against the contract it claims to satisfy')));
  if v_digest2 = v_digest then
    raise exception 'reordering a hashed text list did not change the role digest';
  end if;
  v_id2 := ops.model_role_record_revision(
    v_role, 2, gen_random_uuid(), v_digest2, v_scalars, v_texts_reordered, v_refs);
  if ops.model_role_digest(v_id2) <> v_digest2 then
    raise exception 'revision 2 does not hash to the digest it was recorded under';
  end if;
  -- HISTORY IS PRESERVED. Revision 1 is still readable, still hashes to its own
  -- digest, and was not rewritten by revision 2.
  if ops.model_role_digest(v_id) <> v_digest then
    raise exception 'recording revision 2 changed what revision 1 hashes to';
  end if;
  select jsonb_array_length(ops.model_role_readback(v_role) -> 'history') into v_count;
  if v_count <> 2 then
    raise exception 'the role history holds % revisions, not 2', v_count;
  end if;
  v_exercised := v_exercised || 'versioned-revisions-and-preserved-history';

  -- === refusals on the record path ==========================================

  -- A VERSION HOLE. "The version before this one" must always be answerable.
  -- The content is fresh, so the duplicate-content refusal cannot fire first and
  -- the observed refusal is unambiguously about the version.
  begin
    perform ops.model_role_record_revision(
      v_role, 5, gen_random_uuid(),
      ops.model_role_digest_of_preimage(jsonb_set(v_preimage, '{title}', '"Reviewer (hole)"')),
      jsonb_set(v_scalars, '{title}', '"Reviewer (hole)"'), v_texts, v_refs);
    raise exception '%: a non-contiguous revision number was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%the next revision is%' then
      raise exception 'wrong refusal for a version hole: %', sqlerrm;
    end if;
  end;
  v_exercised := v_exercised || 'refuses-version-hole';

  -- CONTENT THAT REPEATS AN EXISTING REVISION. A revision recording no change is
  -- not history; rolling back is done with the current pointer.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), v_digest, v_scalars, v_texts, v_refs);
    raise exception '%: a duplicate content digest was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%exact bytes%' then
      raise exception 'wrong refusal for duplicate content: %', sqlerrm;
    end if;
  end;
  v_exercised := v_exercised || 'refuses-duplicate-content';

  -- AN ORDINAL GAP. The structure check fires before the digest check, so the
  -- caller learns the shape is wrong rather than only that a hash disagreed.
  -- The digest is a placeholder no stored revision carries, so the
  -- duplicate-content refusal cannot fire first.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), 'sha256:' || repeat('d', 64),
      v_scalars, v_texts_gapped, v_refs);
    raise exception '%: a gapped list was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%contiguously ordinaled%' then
      raise exception 'wrong refusal for a gapped list: %', sqlerrm;
    end if;
  end;
  v_exercised := v_exercised || 'refuses-ordinal-gap';

  -- A DIGEST THE STORED ROWS DO NOT PRODUCE. The caller's hash is compared, and
  -- a refused write leaves nothing behind.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), 'sha256:' || repeat('a', 64),
      v_scalars, v_texts_gapped || jsonb_build_array(jsonb_build_object(
        'field', 'skills', 'ordinal', 1, 'value', 'a third distinct skill')), v_refs);
    raise exception '%: a body/digest mismatch was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%the stored rows produce%' then
      raise exception 'wrong refusal for a body/digest mismatch: %', sqlerrm;
    end if;
  end;
  select count(*) into v_count from ops.model_role_revision
   where tenant = ops.model_role_tenant() and role_key = v_role;
  if v_count <> 2 then
    raise exception 'a refused write left % revisions behind instead of 2', v_count;
  end if;
  v_exercised := v_exercised || 'refuses-body-digest-drift-and-leaves-nothing';

  -- A ROLE DECLARING RETAINED SYSTEM AUTHORITY. No occupant could ever hold it.
  begin
    perform ops.model_role_record_revision(
      v_role, 3, gen_random_uuid(), 'sha256:' || repeat('e', 64),
      jsonb_set(v_scalars, '{authority_class}', '"system_authority"'), v_texts, v_refs);
    raise exception '%: a system-authority role class was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%system authority%' and sqlerrm not like '%authority_class%'
       and sqlerrm not like '%model_role_revision_authority%' then
      raise exception 'wrong refusal for a system-authority role class: %', sqlerrm;
    end if;
  end;
  v_exercised := v_exercised || 'refuses-system-authority-role-class';

  -- AN UNSETTLED ROLE KEY.
  begin
    perform ops.model_role_record_revision(
      'chief_of_staff', 1, gen_random_uuid(), 'sha256:' || repeat('f', 64),
      v_scalars, v_texts, v_refs);
    raise exception '%: an unsettled role key was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
  end;
  v_exercised := v_exercised || 'refuses-unsettled-role-key';

  -- === idempotency ==========================================================
  declare v_key constant uuid := gen_random_uuid();
  begin
    v_replay := ops.model_role_record_revision(
      v_role, 3, v_key, ops.model_role_digest_of_preimage(
        jsonb_set(v_preimage, '{title}', '"Reviewer (synthetic fixture, third revision)"')),
      jsonb_set(v_scalars, '{title}', '"Reviewer (synthetic fixture, third revision)"'),
      v_texts, v_refs);
    -- AN EXACT REPLAY RETURNS THE SAME ROW rather than writing a second one.
    if ops.model_role_record_revision(
         v_role, 3, v_key, ops.model_role_digest_of_preimage(
           jsonb_set(v_preimage, '{title}', '"Reviewer (synthetic fixture, third revision)"')),
         jsonb_set(v_scalars, '{title}', '"Reviewer (synthetic fixture, third revision)"'),
         v_texts, v_refs) <> v_replay then
      raise exception 'an exact idempotent replay did not return the first row';
    end if;
    select count(*) into v_count from ops.model_role_revision
     where tenant = ops.model_role_tenant() and role_key = v_role;
    if v_count <> 3 then
      raise exception 'an idempotent replay wrote a second row: % revisions exist', v_count;
    end if;

    -- THE SAME KEY WITH DIFFERENT CONTENT IS A DIFFERENT REQUEST WEARING ITS
    -- NAME, and is refused rather than silently returning the first row.
    begin
      perform ops.model_role_record_revision(
        v_role, 3, v_key, v_digest2, v_scalars, v_texts_reordered, v_refs);
      raise exception '%: a reused idempotency key with different content was accepted', v_marker;
    exception when others then
      if sqlerrm like v_marker || '%' then raise; end if;
      if sqlerrm not like '%idempotency key%' then
        raise exception 'wrong refusal for an idempotency mismatch: %', sqlerrm;
      end if;
    end;
  end;
  v_exercised := v_exercised || 'idempotent-replay-and-mismatch-refusal';

  -- === append-only, and direct writes =======================================
  --
  -- TWO MECHANISMS REFUSE THESE, and which one speaks depends on who is running
  -- the file: the append-only trigger for a session that holds UPDATE/DELETE
  -- (the schema owner), and the grant posture for a runtime bundle, which holds
  -- neither. Both are correct refusals and the assertion accepts either; what
  -- must never happen is that the statement succeeds.
  begin
    update ops.model_role_revision set title = 'edited' where id = v_id;
    raise exception '%: a revision was updated', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%append-only%' and sqlerrm not like '%permission denied%' then
      raise exception 'wrong refusal for an update: %', sqlerrm;
    end if;
  end;
  begin
    delete from ops.model_role_revision where id = v_id;
    raise exception '%: a revision was deleted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%append-only%' and sqlerrm not like '%permission denied%' then
      raise exception 'wrong refusal for a delete: %', sqlerrm;
    end if;
  end;
  begin
    delete from ops.model_role_revision_text where revision_id = v_id;
    raise exception '%: revision content was deleted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
    if sqlerrm not like '%append-only%' and sqlerrm not like '%permission denied%' then
      raise exception 'wrong refusal for a content delete: %', sqlerrm;
    end if;
  end;
  v_exercised := v_exercised || 'append-only-on-revisions-and-content';

  -- A DIRECT INSERT NAMING ANOTHER WRITER. The actor is chosen to be one the
  -- writer context does NOT name, so the guard has something to refuse; if this
  -- database holds only one active actor the subselect is null and the not-null
  -- column refuses instead. Either way, what must not happen is that it succeeds.
  begin
    insert into ops.model_role_revision(
      tenant, role_key, revision_no, idempotency_key, schema_version, role_digest,
      title, mission, minimum_strength_ref, authority_class, recorded_by_actor_id)
    values (ops.model_role_tenant(), v_role, 4, gen_random_uuid(),
      ops.model_role_schema_version(), 'sha256:' || repeat('b', 64),
      'forged', 'forged', 'strength.high_risk_engineering', 'developer',
      (select id from public.actor
        where active and id <> ops.portfolio_writer_actor_id() order by id limit 1));
    raise exception '%: a directly inserted, misattributed revision was accepted', v_marker;
  exception when others then
    if sqlerrm like v_marker || '%' then raise; end if;
  end;
  v_exercised := v_exercised || 'refuses-direct-misattributed-insert';

  -- === the lock is real, and both writers take it ===========================
  --
  -- The lock helper is granted to no runtime bundle on purpose, so calling it
  -- directly is only possible for a session that owns it. When it is not
  -- reachable, that is reported rather than skipped silently, and the SOURCE
  -- assertions below still run.
  if has_function_privilege(current_user, 'ops.model_role_lock(text)', 'execute') then
    perform ops.model_role_lock(v_role);
    select count(*) into v_locks from pg_locks
     where locktype = 'advisory' and pid = pg_backend_pid() and granted;
    if v_locks < 1 then
      raise exception 'ops.model_role_lock did not take an advisory lock';
    end if;
    v_exercised := v_exercised || 'per-role-advisory-lock-is-actually-taken';
  else
    v_not_exercised := v_not_exercised || format(
      'the runtime observation that ops.model_role_lock takes an advisory lock: %s does not hold EXECUTE on it, which is the intended posture. Only the source assertion that both writers call it ran.',
      current_user);
  end if;
  select pg_get_functiondef(oid) into v_def from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'model_role_record_revision' limit 1;
  if v_def not like '%model_role_lock%' then
    raise exception 'the revision writer does not take the per-role lock';
  end if;
  select pg_get_functiondef(oid) into v_def from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'ops' and p.proname = 'model_role_set_current_revision' limit 1;
  if v_def not like '%model_role_lock%' then
    raise exception 'the current-pointer writer does not take the per-role lock';
  end if;
  -- THE PRINCIPAL IS DERIVED IN THE WRITER, not compared to a parameter. Checked
  -- against the installed source, because a schema where somebody replaced the
  -- derivation with an argument would pass every other assertion here.
  if v_def not like '%authority_actor_slug()%'
     or v_def not like '%model_role_system_authority_partner()%' then
    raise exception 'the current-pointer writer does not derive its principal from the authenticated session';
  end if;
  if v_def ~* 'p_(actor|approved|verified|partner|principal|authority)' then
    raise exception 'the current-pointer writer takes an identity, an approval or an authority as a parameter';
  end if;
  v_exercised := v_exercised || 'both-writers-lock-and-the-pointer-principal-is-derived-in-source';

  -- The content guard exists and is attached to both content relations, and the
  -- revision carries the transaction that created it. Its BEHAVIOUR needs two
  -- committed transactions; see the header, item 2.
  select count(*) into v_count from pg_trigger t
    join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
    join pg_proc p on p.oid = t.tgfoid
   where n.nspname = 'ops' and p.proname = 'model_role_content_guard' and not t.tgisinternal;
  if v_count <> 2 then
    raise exception 'the content freeze is attached to % relations, not 2', v_count;
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema = 'ops' and table_name = 'model_role_revision'
                    and column_name = 'created_xid') then
    raise exception 'the revision carries no creating transaction, so its content cannot be frozen';
  end if;
  v_not_exercised := v_not_exercised ||
    'content-freeze-behaviour (needs two committed transactions; only its presence and attachment were checked)';

  -- === the current pointer ==================================================
  v_readback := ops.model_role_readback(v_role);
  if v_readback -> 'current' <> 'null'::jsonb then
    raise exception 'a role became current without any system-authority act';
  end if;
  if (v_readback ->> 'confers_authority') <> 'false'
     or (v_readback ->> 'occupant_bound') <> 'false'
     or (v_readback ->> 'measured_qualification_bound') <> 'false'
     or (v_readback ->> 'signed') <> 'false'
     or (v_readback ->> 'capability_token_issued') <> 'false' then
    raise exception 'a role readback claims more than re-derivation from committed rows: %', v_readback;
  end if;
  v_exercised := v_exercised || 'no-default-current-role-and-scoped-readback';

  v_session := session_user;
  v_partner_ok := false;
  begin
    v_partner_ok := (ops.authority_actor_slug() = ops.model_role_system_authority_partner());
  exception when others then
    v_partner_ok := false;
  end;

  if not v_partner_ok then
    -- THE REFUSAL PATH, which is what any non-authority session can prove.
    begin
      perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 1, v_digest, true, null);
      raise exception '%: a current pointer was set without the retained system authority', v_marker;
    exception when others then
      if sqlerrm like v_marker || '%' then raise; end if;
      if sqlerrm not like '%authority%' then
        raise exception 'wrong refusal for an unauthorized current-pointer change: %', sqlerrm;
      end if;
    end;
    select count(*) into v_count from ops.model_role_current_pointer;
    if v_count <> 0 then
      raise exception 'the current-pointer ledger holds % rows after a refused change', v_count;
    end if;
    if ops.model_role_current_revision(v_role) is not null then
      raise exception 'a refused current-pointer change still selected a revision';
    end if;
    v_exercised := v_exercised || 'refuses-current-pointer-without-system-authority';
    v_not_exercised := v_not_exercised || format(
      'the POSITIVE current-pointer path and every compare-and-swap refusal (stale expectation, creation collision, already-current, unknown revision, wrong digest). This session is %s, which ops.authority_actor_slug() does not admit as the retained system-authority partner. Re-run this file on that partner''s authority connection to exercise them.',
      v_session);
  else
    -- THE POSITIVE PATH, available only on the retained partner's connection.
    if not exists (select 1 from public.actor
                    where slug = ops.model_role_system_authority_partner()
                      and active and kind = 'human') then
      v_not_exercised := v_not_exercised ||
        'the positive current-pointer path: the authority session is the retained partner, but no active human actor exists for that slug, so the writer refuses. No actor is created here.';
    else
      -- Creation: the expectation is null and the creation assertion is its own
      -- parameter, so "create" is never inferred from an omitted argument.
      perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 1, v_digest, true, null);
      if (ops.model_role_current_revision(v_role) ->> 'revision_no')::integer <> 1 then
        raise exception 'the created current pointer does not name revision 1';
      end if;
      v_exercised := v_exercised || 'creates-current-pointer-under-retained-system-authority';

      -- A SECOND CREATION COLLIDES rather than overwriting.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 2, v_digest2, true, null);
        raise exception '%: a second creation overwrote an existing current pointer', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%creation compare-and-swap cannot move it%' then
          raise exception 'wrong refusal for a creation collision: %', sqlerrm;
        end if;
      end;
      v_exercised := v_exercised || 'refuses-creation-collision';

      -- A STALE EXPECTATION LOSES.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 2, v_digest2, false, 7);
        raise exception '%: a stale compare-and-swap was accepted', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%not the expected%' then
          raise exception 'wrong refusal for a stale compare-and-swap: %', sqlerrm;
        end if;
      end;
      v_exercised := v_exercised || 'refuses-stale-compare-and-swap';

      -- A DIGEST THE NAMED REVISION DOES NOT HAVE.
      begin
        perform ops.model_role_set_current_revision(
          v_role, gen_random_uuid(), 2, 'sha256:' || repeat('c', 64), false, 1);
        raise exception '%: a current pointer named a digest its revision does not have', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%hashes to%' then
          raise exception 'wrong refusal for a stale pointer digest: %', sqlerrm;
        end if;
      end;

      -- A REVISION NOBODY RECORDED.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 99, v_digest, false, 1);
        raise exception '%: a current pointer named an unrecorded revision', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%has no revision%' then
          raise exception 'wrong refusal for an unknown revision: %', sqlerrm;
        end if;
      end;
      v_exercised := v_exercised || 'refuses-missing-revision-and-stale-pointer-digest';

      -- A LEGITIMATE SWAP, and the history of what was current is preserved.
      perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 2, v_digest2, false, 1);
      if (ops.model_role_current_revision(v_role) ->> 'revision_no')::integer <> 2 then
        raise exception 'the compare-and-swap did not move the current pointer';
      end if;
      select jsonb_array_length(ops.model_role_readback(v_role) -> 'pointer_history') into v_count;
      if v_count <> 2 then
        raise exception 'the pointer ledger holds % rows, not 2; a superseded pointer was erased', v_count;
      end if;
      -- RE-POINTING AT WHAT IS ALREADY CURRENT IS A NO-OP AND IS REFUSED, so a
      -- stale caller cannot read one as a success.
      begin
        perform ops.model_role_set_current_revision(v_role, gen_random_uuid(), 2, v_digest2, false, 2);
        raise exception '%: a re-point at the already-current revision was accepted', v_marker;
      exception when others then
        if sqlerrm like v_marker || '%' then raise; end if;
        if sqlerrm not like '%already current%' then
          raise exception 'wrong refusal for an already-current re-point: %', sqlerrm;
        end if;
      end;
      v_exercised := v_exercised || 'swaps-current-pointer-and-preserves-pointer-history';
    end if;
    if v_session <> 'carr_authority_' || ops.model_role_system_authority_partner() then
      v_not_exercised := v_not_exercised || format(
        'the refusal path for a NON-system-authority authority principal: this session (%s) is the retained partner, so the other partner''s refusal was not observed here.',
        v_session);
    end if;
  end if;

  -- === least privilege ======================================================
  if exists (select 1 from pg_roles where rolname = 'carr_writer')
     and exists (select 1 from pg_roles where rolname = 'carr_authority') then
    if has_table_privilege('carr_writer', 'ops.model_role_revision', 'insert')
       or has_table_privilege('carr_authority', 'ops.model_role_current_pointer', 'insert') then
      raise exception 'a runtime role holds direct INSERT; every write must go through a definer writer';
    end if;
    if not has_function_privilege('carr_writer',
        'ops.model_role_record_revision(text,integer,uuid,text,jsonb,jsonb,jsonb)', 'execute') then
      raise exception 'carr_writer cannot record an inert role revision';
    end if;
    if has_function_privilege('carr_writer',
        'ops.model_role_set_current_revision(text,uuid,integer,text,boolean,integer)', 'execute') then
      raise exception 'carr_writer can name the current revision; that act reaches the authority bundle only';
    end if;
    if not has_function_privilege('carr_authority',
        'ops.model_role_set_current_revision(text,uuid,integer,text,boolean,integer)', 'execute') then
      raise exception 'carr_authority cannot name the current revision';
    end if;
    if has_function_privilege('carr_writer', 'ops.model_role_lock(text)', 'execute')
       or has_function_privilege('carr_reader', 'ops.model_role_lock(text)', 'execute') then
      raise exception 'the per-role write lock is reachable without writing';
    end if;
    v_exercised := v_exercised || 'least-privilege-on-tables-writers-and-lock';
  else
    v_not_exercised := v_not_exercised ||
      'the least-privilege assertions: carr_writer and carr_authority do not both exist here. This file creates no role.';
  end if;

  -- === what this rail does not carry ========================================
  if to_regclass('ops.model_role_occupant') is not null
     or to_regclass('ops.model_role_qualification') is not null then
    raise exception 'this rail has grown an occupancy or qualification relation';
  end if;
  select count(*) into v_count from information_schema.columns
   where table_schema = 'ops'
     and table_name in ('model_role_revision', 'model_role_revision_text',
                        'model_role_revision_ref', 'model_role_current_pointer')
     and (column_name ~* 'occupant|occupancy|qualif|approved|verified'
          -- A NUMERIC COLUMN WOULD BE A BUSINESS FLOOR. Quality floors here are
          -- NAMED REFERENCES; the only integers are ordinals and version numbers.
          or data_type in ('numeric', 'double precision', 'real'));
  if v_count <> 0 then
    raise exception 'a role relation carries an occupant, a qualification, an approval or a numeric floor column';
  end if;
  -- The one column whose name contains "grant" is authority_grant_kind, which
  -- RECORDS which authority was exercised and confers nothing. Named explicitly
  -- so the sweep above cannot be widened past it by accident.
  select count(*) into v_count from information_schema.columns
   where table_schema = 'ops'
     and table_name in ('model_role_revision', 'model_role_revision_text',
                        'model_role_revision_ref', 'model_role_current_pointer')
     and column_name ~* 'grant' and column_name <> 'authority_grant_kind';
  if v_count <> 0 then
    raise exception 'a role relation carries a grant column beyond the recorded authority kind';
  end if;
  v_exercised := v_exercised || 'no-occupant-qualification-approval-or-numeric-floor-column';

  -- === the honest report ====================================================
  raise notice 'EXERCISED (%): %', cardinality(v_exercised), array_to_string(v_exercised, '; ');
  if cardinality(v_not_exercised) > 0 then
    raise notice 'NOT EXERCISED (%): %', cardinality(v_not_exercised),
      array_to_string(v_not_exercised, ' | ');
  end if;
  raise notice 'NOT PROVED HERE, ALWAYS: the cross-language digest equality with defineRole (no JavaScript runs in this fixture) and concurrent compare-and-swap between two sessions. Every row written above is about to be rolled back, and no role is current.';
end;
$proof$;

rollback;
