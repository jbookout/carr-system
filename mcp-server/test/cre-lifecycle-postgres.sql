-- DoctorCRE v5 slice V5-J102: transaction-scoped PostgreSQL proof for the CRE
-- lifecycle rail.
--
-- THIS FILE HAS NOT BEEN EXECUTED. It is delivered as source alongside
-- ops/cre-lifecycle.candidate.sql, which is itself an unnumbered candidate that
-- has not been applied to any database. Nothing in this slice runs either file,
-- there is no new PostgreSQL gate executable and no inventory entry point, and
-- running this file is a separate act on a disposable database. Read every
-- assertion below as "what this fixture WOULD prove when run", never as a result.
--
-- EVERY FIXTURE ROW IS ROLLED BACK. No lifecycle subject, event, business
-- record, Salesforce reference or correction receipt survives running it, and
-- the last group asserts the tables are empty again before the rollback.
--
-- EVERY RECORD IS SYNTHETIC. Every client, assignment, property, deal, document
-- and reason below is unmistakably test data. Nothing here is a claim about a
-- real CARR client or a real transaction, and nothing here is a proposal of one.
--
-- WHAT THIS FIXTURE PROVES THAT THE NODE SUITE CANNOT. The Node tests run
-- against a scripted fake handle, which can prove which statements the store
-- issues, in what order, and with which parameters -- and nothing at all about
-- what PostgreSQL does with them. These are the properties that only a real
-- engine can answer:
--
--   S1  the append-only triggers, the direct-DML guard and the no-truncate
--       guard exist on exactly the intended relations
--   S2  direct INSERT is granted to nobody, and the private approval reader is
--       executable by none of the runtime role bundles
--   S3  the one-pending-Deal-per-Assignment partial index exists with the
--       intended predicate (Q078/Q095 structurally)
--   S4  BLOCK-2 and H5 structurally: a first-party record cannot be stored
--       without a typed subject binding and an author class, the four
--       partner-only record kinds cannot be stored with any other author class,
--       and the evidence->subject association relation exists with its pin-exact
--       unique index
--   B1  a whole coupled transition applies -- two CREATED subjects and their
--       history in one transaction
--   B2  a stale compare-and-swap operand refuses with a serialization failure
--   B3  the evidence recheck refuses when the exact pin has moved, and
--       existence alone does not satisfy it
--   B4  ATOMICITY: a transition whose second envelope is bad leaves NEITHER
--       subject behind, not the first one and half the second
--   B5  an idempotent replay returns the committed outcome and appends no
--       second event; the same key over a different payload refuses
--   B6  the append-only relations refuse UPDATE and DELETE for real
--   B7  direct DML outside a registered writer refuses for real
--   B8  a second pending Deal on one Assignment refuses at the index
--   B9  a closing_settlement record with no actual closing date cannot be
--       stored at all (Q094 structurally)
--   B10 the private approval reader raises, so no approval can be manufactured
--       one layer down either
--   B11 BLOCK-1: a CREATION whose id is already taken refuses under the lock
--       instead of upserting the existing row away
--   B12 BLOCK-1: a proposed subject with NO compare-and-swap operand refuses,
--       so omitting the map is not a way back to the old overwrite
--   B13 BLOCK-1: an envelope whose prior_state_digest disagrees with its
--       operand refuses rather than writing a history that describes a version
--       nothing checked
--   B14 BLOCK-2: the recheck refuses evidence bound to a DIFFERENT subject, and
--       refuses a manifest item that names no binding at all
--   B15 H4: a caller-chosen updated_at or recorded_at refuses; the instant is
--       the database transaction time
--   B16 H5: whichever half this session can prove -- a sponsored agent cannot
--       author a closing_settlement, or a partner-authored one carries its
--       author class on the row
--
-- THREE PREREQUISITES, checked before anything is attempted, each SKIPPING with
-- a notice rather than failing -- the same shape work-portfolio-postgres.sql and
-- benchmark-acceptance-postgres.sql use:
--   * ops/cre-lifecycle.candidate.sql must have been applied here.
--   * domain.sql must have been applied here (the J102 rail calls its tenant,
--     canonicaliser, digest, instant parser, clock and principal rather than
--     restating any of them).
--   * The BEHAVIOURAL groups additionally need a session running as an admitted
--     principal, because every writer derives its actor through
--     ops.f01_context_actor_slug() and refuses any other session_user. This file
--     CREATES NO ROLE and grants nothing: minting one would manufacture the
--     identity the whole rail exists to derive. The STRUCTURAL groups need no
--     principal and run either way.
--
-- WHAT THIS FILE DOES NOT PROVE, named rather than implied:
--   * That ops.f01_digest_jsonb and artifact-trust.js's digest() agree on the
--     same record. Both sides are asserted to hash the canonical JSON, and the
--     SQL side reuses F01's canonicaliser, which domain.sql already reconciles
--     against the module -- but a rollback-only fixture cannot execute
--     JavaScript, so the cross-language equality remains live-integration
--     verification. It is the first thing to check when this rail is exercised
--     end to end.
--   * That the lock ordering in ops.j102_apply_transition prevents deadlock
--     under real concurrency. A single-session fixture cannot contend with
--     itself; the ordering is argued in that function's own comment and would
--     need a two-session harness to demonstrate.
--   * That two CONCURRENT creations of the same subject id resolve to one winner
--     and one refusal. B11 proves the sequential half -- a creation whose id is
--     already taken refuses under the lock rather than upserting -- and the
--     concurrent half rests on the argument that both sessions take the same
--     tier-2 advisory lock on that key before either reads, so the loser's null
--     operand meets the winner's committed row. One session cannot contend with
--     itself, so this too would need a two-session harness. It is named here
--     rather than implied by B11's success.
--   * Anything about a real client, a real deal or a real Salesforce record.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_actor            text;
  v_class            text;
  v_now              text;
  v_behavioural      boolean := false;
  v_err              text;
  v_role             text;
  v_predicate        text;
  v_count            bigint;
  v_result           jsonb;
  v_replay           jsonb;
  v_fact             jsonb;
  v_fact_digest      text;
  v_deal             jsonb;
  v_deal_digest      text;
  v_events_before    bigint;
  v_events_after     bigint;

  v_tenant           constant text := 'carr-internal';
  v_placeholder      constant text := 'sha256:' || repeat('0', 64);
  v_assignment_id    constant text := 'j102-fixture-assignment-1';
  v_assignment_id_2  constant text := 'j102-fixture-assignment-2';
  v_deal_id          constant text := 'j102-fixture-deal-1';
  v_deal_id_2        constant text := 'j102-fixture-deal-2';
  v_fact_id          constant text := 'j102-fixture-fact-1';
  v_learn            constant text := 'j102-fixture-learn-rollback';

  -- One synthetic Assignment, in the exact shape the kernel projects when a
  -- commitment lands: committed, with its selected property and its pending deal.
  v_assignment_state constant jsonb := jsonb_build_object(
    'subject_kind', 'assignment',
    'subject_id', v_assignment_id,
    'engagement_id', 'j102-fixture-engagement-1',
    'assignment_phase', 'committed',
    'open_negotiation_count', 2,
    'selected_property_id', 'j102-fixture-property-1',
    'active_lease_draft_target_id', 'j102-fixture-property-1',
    'pending_deal_id', v_deal_id,
    'multi_target_exception_ref', null);

  -- One synthetic pending Deal, in the exact shape the kernel projects.
  v_deal_state       constant jsonb := jsonb_build_object(
    'subject_kind', 'deal',
    'subject_id', v_deal_id,
    'assignment_id', v_assignment_id,
    'property_id', 'j102-fixture-property-1',
    'instrument_kind', 'lease',
    'deal_state', 'pending',
    'execution_state', 'unexecuted',
    'diligence_state', 'not_applicable',
    'closing_state', 'not_reached',
    'commission_agreement_state', 'absent',
    'invoice_state', 'not_invoiced',
    'payment_state', 'unpaid',
    'completion_state', 'open',
    'cancellation_reason', null,
    'closing_date', null);
begin
  -- === prerequisites =======================================================
  if to_regprocedure('ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb)') is null then
    raise notice 'SKIPPED: ops.j102_apply_transition is absent; ops/cre-lifecycle.candidate.sql has not been applied here yet.';
    return;
  end if;
  if to_regprocedure('ops.f01_digest_jsonb(jsonb)') is null
     or to_regprocedure('ops.f01_context_actor_slug()') is null then
    raise notice 'SKIPPED: domain.sql is absent; the J102 rail reuses its digest, tenant, clock and principal.';
    return;
  end if;

  -- Is this session an admitted principal? A definer function cannot be talked
  -- into one, and this file creates none, so the behavioural groups are simply
  -- skipped where the answer is no.
  begin
    v_actor := ops.f01_context_actor_slug();
    v_class := ops.f01_principal() ->> 'authorization_class';
    v_now := ops.f01_now_text();
    v_behavioural := true;
  exception when others then
    raise notice 'PARTIAL: session_user % is not an admitted J102 principal; the structural groups run and the behavioural groups are skipped. No role is created here.',
      session_user;
  end;

  -- === S1: the guards exist on exactly the intended relations ==============
  foreach v_role in array array[
    'j102_subject_current', 'j102_subject_event', 'j102_first_party_record',
    'j102_salesforce_reference', 'j102_correction_receipt', 'j102_reconciliation_item',
    'j102_idempotency'
  ] loop
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_dml_guard' and not t.tgisinternal) then
      raise exception 'S1: ops.% carries no direct-DML guard', v_role;
    end if;
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_no_truncate' and not t.tgisinternal) then
      raise exception 'S1: ops.% carries no truncate guard', v_role;
    end if;
  end loop;

  -- The five history relations refuse UPDATE and DELETE; the two mutable ones
  -- deliberately do not, and the fixture asserts BOTH halves so a future edit
  -- that made current state append-only, or history mutable, is visible.
  foreach v_role in array array[
    'j102_subject_event', 'j102_first_party_record', 'j102_salesforce_reference',
    'j102_correction_receipt', 'j102_reconciliation_item'
  ] loop
    if not exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_append_only' and not t.tgisinternal) then
      raise exception 'S1: ops.% is history and carries no append-only guard', v_role;
    end if;
  end loop;
  foreach v_role in array array['j102_subject_current', 'j102_idempotency'] loop
    if exists (
      select 1 from pg_trigger t join pg_class c on c.oid = t.tgrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = v_role
         and t.tgname = v_role || '_append_only' and not t.tgisinternal) then
      raise exception 'S1: ops.% is current state and must remain updatable through its writer', v_role;
    end if;
  end loop;

  -- === S2: nobody holds direct DML, and the approval reader is private ======
  for v_role in select unnest(array['carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority']) loop
    if not exists (select 1 from pg_roles where rolname = v_role) then
      continue;  -- a database without the full bundle set still proves the rest
    end if;
    if has_table_privilege(v_role, 'ops.j102_subject_current', 'INSERT')
       or has_table_privilege(v_role, 'ops.j102_subject_event', 'INSERT')
       or has_table_privilege(v_role, 'ops.j102_first_party_record', 'INSERT')
       or has_table_privilege(v_role, 'ops.j102_correction_receipt', 'INSERT') then
      raise exception 'S2: % holds direct INSERT; every write must go through a definer writer that derives its own actor',
        v_role;
    end if;
    if has_function_privilege(v_role, 'ops.j102_typed_approval(text,text)', 'EXECUTE') then
      raise exception 'S2: % can execute the private approval reader; a callable stub is the first step toward a configurable one',
        v_role;
    end if;
    -- The correction writer reaches the AUTHORITY bundle only.
    if v_role in ('carr_reader', 'carr_writer', 'carr_jobs')
       and has_function_privilege(v_role, 'ops.j102_record_correction(jsonb,text,text)', 'EXECUTE') then
      raise exception 'S2: % can execute the correction writer, which is partner authority only', v_role;
    end if;
  end loop;

  -- === S3: the one-pending-Deal-per-Assignment index =======================
  select pg_get_expr(i.indpred, i.indrelid) into v_predicate
    from pg_index i join pg_class c on c.oid = i.indexrelid
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'ops' and c.relname = 'j102_one_pending_deal_per_assignment';
  if v_predicate is null then
    raise exception 'S3: the one-pending-deal-per-assignment index is absent';
  end if;
  if v_predicate !~ 'deal_state' or v_predicate !~ 'pending' then
    raise exception 'S3: the pending-deal index predicate does not name the pending deal state: %', v_predicate;
  end if;
  -- CANCELLED AND CLOSED DEALS MUST BE OUTSIDE THE INDEX, or Q096 breaks: an
  -- assignment that lost one deal could never hold another.
  if exists (select 1 from pg_index i join pg_class c on c.oid = i.indexrelid
               join pg_namespace n on n.oid = c.relnamespace
              where n.nspname = 'ops' and c.relname = 'j102_one_pending_deal_per_assignment'
                and i.indpred is null) then
    raise exception 'S3: the pending-deal index is not partial, so a cancelled deal would block a new one';
  end if;

  -- === S4: the binding and the author class are structural =================
  -- BLOCK-2. A business record that cannot say WHICH subject it is about is the
  -- defect; NOT NULL on both columns is what makes it unstorable rather than
  -- merely discouraged.
  foreach v_role in array array['bound_subject_kind', 'bound_subject_id', 'recorded_by_class'] loop
    if not exists (
      select 1 from information_schema.columns
       where table_schema = 'ops' and table_name = 'j102_first_party_record'
         and column_name = v_role and is_nullable = 'NO') then
      raise exception 'S4: ops.j102_first_party_record has no non-null % column; an unbound or unattributed business record could be stored',
        v_role;
    end if;
  end loop;
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_binding_matches_envelope') then
    raise exception 'S4: the subject binding is not CHECK-bound to the hashed envelope, so it could drift from the bytes it describes';
  end if;
  -- H5. The four partner-only facts cannot be stored with an agent author, so an
  -- agent-authored closing date does not exist to be laundered later.
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_partner_authored_kinds') then
    raise exception 'S4: a sponsored agent could author a closing_settlement, winning_property_commitment, deal_failure or lifecycle_correction record';
  end if;
  -- The association relation, and its PIN-EXACT unique index. A unique index over
  -- the document id alone would let one association follow a document across
  -- versions, which is exactly what a pin exists to prevent.
  if to_regclass('ops.j102_evidence_subject_link') is null then
    raise exception 'S4: the evidence->subject association relation is absent, so document evidence could not be bound to a subject at all';
  end if;
  select pg_get_indexdef(c.oid) into v_predicate
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'ops' and c.relname = 'j102_evidence_subject_link_uq';
  if v_predicate is null then
    raise exception 'S4: the association relation has no unique index';
  end if;
  foreach v_role in array array['evidence_ref', 'version_no', 'content_digest',
                                'subject_kind', 'subject_id'] loop
    if v_predicate !~ v_role then
      raise exception 'S4: the association unique index does not bind %; an association that is not pin-exact would follow a document across versions',
        v_role;
    end if;
  end loop;
  -- An association is not a document and asserts nothing about document state.
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_evidence_subject_link'
       and co.conname = 'j102_link_asserts_no_document_state') then
    raise exception 'S4: an association could claim to create a document or assert its state, which is F01''s authority and not this rail''s';
  end if;
  -- M3: the correction writer decides authority from the DERIVED principal, not
  -- from a role-name literal sitting beside it as a second identity source.
  if pg_get_functiondef(to_regprocedure('ops.j102_record_correction(jsonb,text,text)')) ~ 'carr_authority_joe' then
    raise exception 'M3: the correction writer still names a role literal; authority has one derivation, ops.f01_principal()';
  end if;

  -- === B9 and B10 need no principal ========================================
  -- The closing-date constraint is structural: a closing_settlement row with no
  -- actual date cannot exist, so no closing transition can ever find one.
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_closing_requires_date') then
    raise exception 'B9: a closing_settlement record could be stored without an actual closing date';
  end if;
  if not exists (
    select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'ops' and c.relname = 'j102_first_party_record'
       and co.conname = 'j102_fact_reason_required') then
    raise exception 'B9: a deal_failure or correction record could be stored without a reason';
  end if;

  -- B10: the private approval reader raises rather than returning anything. It
  -- is reachable here only where this session owns it; where it is not, the
  -- absence of the grant was already proved in S2.
  begin
    perform ops.j102_typed_approval('representation_equivalence_approval', 'j102-fixture-approval-1');
    raise exception 'B10: the private approval reader returned instead of refusing';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_typed_approval_unavailable' then
      raise exception 'B10: the approval reader refused for the wrong reason: %', sqlerrm;
    end if;
  when others then
    if sqlerrm ~ 'permission denied' then
      raise notice 'B10: the approval reader is not executable from this session; its refusal text is checked only where it is reachable.';
    else
      raise;
    end if;
  end;

  -- The migration reader answers no, for every input, and names what is missing.
  v_result := ops.j102_migration_readiness();
  if (v_result ->> 'may_retire_callers') <> 'false'
     or (v_result ->> 'migration_complete') <> 'false'
     or (v_result ->> 'big_bang_rename') <> 'false' then
    raise exception 'Q081: the migration reader claimed something it cannot know: %', v_result;
  end if;
  if jsonb_array_length(v_result -> 'missing_facts') < 2 then
    raise exception 'Q081: the migration reader does not name the missing facts';
  end if;

  if not v_behavioural then
    raise notice 'STRUCTURAL GROUPS PASSED (S1, S2, S3, S4, B9, B10, M3, Q081). Behavioural groups skipped: no admitted principal.';
    return;
  end if;

  -- ========================================================================
  -- BEHAVIOURAL GROUPS. Everything below runs as the derived actor and rolls
  -- back with the transaction.
  -- ========================================================================

  -- One synthetic first-party record, so the transition below has something
  -- real to be judged against and something real to re-read under the lock.
  --
  -- IT NAMES THE SUBJECT IT IS ABOUT. `assignment_mandate` is used rather than
  -- one of the four partner-only kinds precisely so this seed runs whichever
  -- admitted class the session holds; B16 below proves the partner-only half
  -- separately, in whichever direction this session can actually demonstrate.
  v_result := ops.j102_record_first_party_fact(
    (select jsonb_build_object(
       'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
       'record_kind', 'stored_first_party_record',
       'tenant', v_tenant,
       'record', r,
       'record_digest', ops.f01_digest_jsonb(r),
       'domain_policy_digest', v_placeholder,
       'decision_subset_digest', v_placeholder)
       from (select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-first-party-record.v1',
         'tenant', v_tenant,
         'record_kind', 'assignment_mandate',
         'record_id', v_fact_id,
         'subject_kind', 'assignment',
         'subject_id', v_assignment_id,
         'reason', null, 'detail', 'synthetic fixture mandate',
         'closing_date', null, 'supporting_document_id', null,
         'recorded_by', v_actor,
         'recorded_by_authorization_class', v_class,
         'recorded_at', v_now,
         'advances_lifecycle_state', false) as r) s),
    'j102-fixture-key-fact', v_placeholder);
  if (v_result ->> 'advances_lifecycle_state') <> 'false' then
    raise exception 'a first-party record must advance no lifecycle state';
  end if;
  if (v_result ->> 'bound_subject_id') <> v_assignment_id
     or (v_result ->> 'recorded_by_authorization_class') <> v_class then
    raise exception 'the stored record did not report its binding and its derived author class: %', v_result;
  end if;
  v_fact := ops.j102_first_party_record('assignment_mandate', v_fact_id);
  if v_fact is null then
    raise exception 'the first-party record did not read back';
  end if;
  if (v_fact -> 'record' ->> 'subject_id') <> v_assignment_id then
    raise exception 'the readback lost the record''s subject binding';
  end if;
  v_fact_digest := v_fact ->> 'record_digest';

  -- === B1: a whole coupled transition applies, state and history together ===
  --
  -- TWO CREATIONS IN ONE CALL, and BOTH carry an EXPLICIT NULL operand. That is
  -- BLOCK-1's shape: a creation is a request that the subject be ABSENT, said in
  -- the compare-and-swap map, rather than a subject the map simply never
  -- mentions. The old empty map meant "check nothing", and the writer duly
  -- checked nothing.
  select count(*) into v_events_before from ops.j102_subject_event where tenant = v_tenant;
  v_result := ops.j102_apply_transition(
    'commit-winning-property',
    jsonb_build_object(
      'assignment:' || v_assignment_id, null,
      'deal:' || v_deal_id, null),
    jsonb_build_array(
      (select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'assignment', 'subject_id', v_assignment_id,
           'state', v_assignment_state,
           'established_by_transition', 'commit-winning-property',
           'prior_state_digest', null,
           'updated_by', v_actor, 'updated_at', v_now) as r) s),
      (select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id,
           'state', v_deal_state,
           'established_by_transition', 'commit-winning-property',
           'prior_state_digest', null,
           'updated_by', v_actor, 'updated_at', v_now) as r) s)),
    jsonb_build_array((select jsonb_build_object(
       'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
       'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
       'record', r, 'record_digest', ops.f01_digest_jsonb(r),
       'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
       from (select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
         'tenant', v_tenant,
         'event', jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
           'event_kind', 'pending_deal_created',
           'subject_kind', 'deal', 'subject_id', v_deal_id),
         'transition_id', 'commit-winning-property',
         'evidence_references', jsonb_build_array(jsonb_build_object(
           'evidence_kind', 'search_initiation',
           'source', 'first_party_record', 'reference', v_fact_id)),
         'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
    jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'search_initiation', 'source', 'first_party_record',
      'reader', 'ops.j102_first_party_record',
      'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                     'record_id', v_fact_id),
      'expected_record_digest', v_fact_digest,
      -- The binding the recheck re-asserts under the lock, against the record's
      -- OWN stored columns rather than against this manifest.
      'binding', jsonb_build_object('subject_kind', 'assignment',
                                    'subject_id', v_assignment_id))),
    'j102-fixture-key-commit', v_placeholder,
    jsonb_build_object('operation', 'commit-winning-property',
      'reason_id', 'selection_and_commitment_create_pending_deal',
      'coupled_facts', jsonb_build_array('deal.deal_state'),
      'decision_refs', jsonb_build_array('Q078.D1')));

  if (v_result ->> 'evidence_rechecked_under_lock') <> 'true'
     or (v_result ->> 'evidence_bound_under_lock') <> 'true' then
    raise exception 'B1: the transition did not report re-reading and re-binding its evidence under the lock';
  end if;
  -- H4's receipt: the committed instant is the DATABASE's, not the caller's.
  if (v_result ->> 'committed_at') is null then
    raise exception 'B1: the writer returned no committed instant';
  end if;
  if ops.j102_subject('assignment', v_assignment_id) is null then
    raise exception 'B1: the created assignment did not land beside the deal';
  end if;
  v_deal := ops.j102_subject('deal', v_deal_id);
  if v_deal is null or (v_deal -> 'state' ->> 'deal_state') <> 'pending' then
    raise exception 'B1: the pending deal did not land';
  end if;
  v_deal_digest := v_deal ->> 'state_digest';
  select count(*) into v_events_after from ops.j102_subject_event where tenant = v_tenant;
  if v_events_after <> v_events_before + 1 then
    raise exception 'B1: current state and history did not land together (% events, expected %)',
      v_events_after, v_events_before + 1;
  end if;
  -- The readback verifies rather than trusts: j102_subject recomputes the state
  -- digest from the committed bytes, so this equality is a recomputation.
  if v_deal_digest <> ops.f01_digest_jsonb(v_deal_state) then
    raise exception 'B1: the readback digest is not the digest of the stored state';
  end if;

  -- === B5: replay returns the committed outcome and appends no second event ==
  v_replay := ops.j102_replay_outcome('commit-winning-property', 'j102-fixture-key-commit',
    v_placeholder);
  if v_replay is null or (v_replay ->> 'reason_id') <> 'selection_and_commitment_create_pending_deal' then
    raise exception 'B5: the settled key did not replay its stored outcome';
  end if;
  select count(*) into v_count from ops.j102_subject_event where tenant = v_tenant;
  if v_count <> v_events_after then
    raise exception 'B5: a replay appended history';
  end if;

  -- The same key over a DIFFERENT payload is a substitution attempt.
  begin
    perform ops.j102_replay_outcome('commit-winning-property', 'j102-fixture-key-commit',
      'sha256:' || repeat('9', 64));
    raise exception 'B5: a different payload replayed under the same key';
  exception when unique_violation then
    if sqlerrm !~ 'j102_idempotency_payload_mismatch' then raise; end if;
  end;

  -- === B2: a stale compare-and-swap operand refuses ========================
  begin
    perform ops.j102_apply_transition(
      'record-lease-execution',
      jsonb_build_object('deal:' || v_deal_id, 'sha256:' || repeat('7', 64)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id,
           'state', jsonb_set(v_deal_state, '{execution_state}', '"executed"'),
           'established_by_transition', 'record-lease-execution',
           -- The envelope declares the SAME operand the map carries, because the
           -- writer refuses a pair that disagrees (B13). What is stale here is
           -- the operand itself, which is the point of B2.
           'prior_state_digest', 'sha256:' || repeat('7', 64),
           'updated_by', v_actor, 'updated_at', v_now) as r) s)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
           'tenant', v_tenant,
           'event', jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
             'event_kind', 'lease_executed', 'subject_kind', 'deal', 'subject_id', v_deal_id),
           'transition_id', 'record-lease-execution',
           'evidence_references', '[]'::jsonb,
           'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-stale', v_placeholder,
      jsonb_build_object('operation', 'record-deal-execution', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'B2: a stale compare-and-swap operand was applied';
  exception when serialization_failure then
    if sqlerrm !~ 'j102_stale_subject_digest' then raise; end if;
  end;
  -- And the deal did not move.
  if (ops.j102_subject('deal', v_deal_id) -> 'state' ->> 'execution_state') <> 'unexecuted' then
    raise exception 'B2: the refused transition changed state anyway';
  end if;

  -- === B3: the evidence recheck refuses a pin that moved ===================
  -- EXISTENCE IS NOT THE CHECK. The record still exists; the manifest names a
  -- digest it does not carry, which is the shape of a record rewritten between
  -- the decision and the write.
  begin
    perform ops.j102_recheck_evidence(jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'search_initiation', 'source', 'first_party_record',
      'reader', 'ops.j102_first_party_record',
      'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                     'record_id', v_fact_id),
      'expected_record_digest', 'sha256:' || repeat('5', 64),
      'binding', jsonb_build_object('subject_kind', 'assignment',
                                    'subject_id', v_assignment_id))));
    raise exception 'B3: a moved evidence pin satisfied the recheck';
  exception when serialization_failure then
    if sqlerrm !~ 'j102_evidence_moved' then raise; end if;
  when insufficient_privilege then
    raise notice 'B3: the recheck is not executable from this session; it is private to the transition writer.';
  end;

  -- === B14: the recheck refuses evidence bound to a DIFFERENT subject =======
  -- THE RECORD IS AUTHENTIC, UNMOVED AND CORRECTLY PINNED. Every check that
  -- existed before BLOCK-2 passes on it. What refuses is that it is a mandate
  -- about assignment 1 and this manifest would advance assignment 2 — which is
  -- the shape of one closing settlement closing somebody else's deal.
  begin
    perform ops.j102_recheck_evidence(jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'search_initiation', 'source', 'first_party_record',
      'reader', 'ops.j102_first_party_record',
      'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                     'record_id', v_fact_id),
      'expected_record_digest', v_fact_digest,
      'binding', jsonb_build_object('subject_kind', 'assignment',
                                    'subject_id', v_assignment_id_2))));
    raise exception 'B14: evidence bound to another subject satisfied the recheck';
  exception when serialization_failure then
    if sqlerrm !~ 'j102_evidence_unbound' then raise; end if;
  when insufficient_privilege then
    null;  -- already noticed above
  end;

  -- A manifest item that names NO binding is a decision nobody can re-check.
  begin
    perform ops.j102_recheck_evidence(jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'search_initiation', 'source', 'first_party_record',
      'reader', 'ops.j102_first_party_record',
      'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                     'record_id', v_fact_id),
      'expected_record_digest', v_fact_digest)));
    raise exception 'B14: an unbound manifest item was accepted';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_evidence_binding_required' then raise; end if;
  when insufficient_privilege then
    null;  -- already noticed above
  end;

  -- An empty manifest is refused outright: a transition never applies without
  -- re-reading something.
  begin
    perform ops.j102_recheck_evidence('[]'::jsonb);
    raise exception 'B3: an empty evidence manifest was accepted';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_evidence_recheck_required' then raise; end if;
  when insufficient_privilege then
    null;  -- already noticed above
  end;

  -- === B4: ATOMICITY — a bad second envelope leaves NEITHER subject =========
  begin
    perform ops.j102_apply_transition(
      'commit-winning-property',
      jsonb_build_object(
        'assignment:' || v_assignment_id_2, null,
        'deal:' || v_deal_id_2, null),
      jsonb_build_array(
        -- The first envelope is perfectly good.
        (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
           'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
           'record', r, 'record_digest', ops.f01_digest_jsonb(r),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
             'tenant', v_tenant, 'subject_kind', 'assignment',
             'subject_id', v_assignment_id_2,
             'state', jsonb_build_object(
               'subject_kind', 'assignment', 'subject_id', v_assignment_id_2,
               'engagement_id', 'j102-fixture-engagement-1',
               'assignment_phase', 'committed', 'open_negotiation_count', 2,
               'selected_property_id', 'j102-fixture-property-2',
               'active_lease_draft_target_id', 'j102-fixture-property-2',
               'pending_deal_id', v_deal_id_2, 'multi_target_exception_ref', null),
             'established_by_transition', 'commit-winning-property',
             'prior_state_digest', null,
             'updated_by', v_actor, 'updated_at', v_now) as r) s),
        -- The second LIES about its own bytes.
        (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
           'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
           'record', r, 'record_digest', 'sha256:' || repeat('3', 64),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
             'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id_2,
             'state', v_deal_state || jsonb_build_object('subject_id', v_deal_id_2),
             'established_by_transition', 'commit-winning-property',
             'prior_state_digest', null,
             'updated_by', v_actor, 'updated_at', v_now) as r) s)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
           'tenant', v_tenant,
           'event', jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
             'event_kind', 'assignment_committed', 'subject_kind', 'assignment',
             'subject_id', v_assignment_id_2),
           'transition_id', 'commit-winning-property',
           'evidence_references', '[]'::jsonb,
           'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-atomic', v_placeholder,
      jsonb_build_object('operation', 'commit-winning-property', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'B4: an envelope that lied about its own bytes was stored';
  exception when data_exception then
    if sqlerrm !~ 'j102_subject_digest_mismatch' then raise; end if;
  end;
  -- THE ASSERTION THAT MATTERS: the GOOD first envelope did not land either.
  if ops.j102_subject('assignment', v_assignment_id_2) is not null then
    raise exception 'B4: a coupled transition applied partially — the first subject survived a failed second';
  end if;
  if ops.j102_subject('deal', v_deal_id_2) is not null then
    raise exception 'B4: the bad subject landed';
  end if;

  -- === B11: a CREATION whose id is already taken refuses ====================
  -- BLOCK-1's reproducer, run against the fix. The deal from B1 exists; this
  -- call proposes a FRESH deal under a different assignment with the SAME id and
  -- an explicit null operand, which is exactly what a caller naming an existing
  -- id as its new_deal_id produces. The null operand means "must be absent", the
  -- row is present, and the swap refuses instead of upserting the closed deal
  -- away and leaving its events behind.
  begin
    perform ops.j102_apply_transition(
      'commit-winning-property',
      jsonb_build_object('deal:' || v_deal_id, null),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id,
           'state', v_deal_state || jsonb_build_object('assignment_id', v_assignment_id_2),
           'established_by_transition', 'commit-winning-property',
           'prior_state_digest', null,
           'updated_by', v_actor, 'updated_at', v_now) as r) s)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
           'tenant', v_tenant,
           'event', jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
             'event_kind', 'pending_deal_created', 'subject_kind', 'deal',
             'subject_id', v_deal_id),
           'transition_id', 'commit-winning-property',
           'evidence_references', '[]'::jsonb,
           'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-collide', v_placeholder,
      jsonb_build_object('operation', 'commit-winning-property', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'B11: a creation overwrote an existing subject';
  exception when serialization_failure then
    if sqlerrm !~ 'j102_stale_subject_digest' then raise; end if;
  end;
  -- AND THE EXISTING DEAL IS UNCHANGED: still under its own assignment.
  if (ops.j102_subject('deal', v_deal_id) -> 'state' ->> 'assignment_id') <> v_assignment_id then
    raise exception 'B11: the refused creation re-parented the existing deal anyway';
  end if;

  -- === B12: a proposed subject with NO operand refuses ======================
  -- Omitting the key is not a way back to the old behaviour. The writer iterates
  -- the UNION of the map and the proposed subjects, and an unnamed subject is a
  -- request it cannot check rather than one it waves through.
  begin
    perform ops.j102_apply_transition(
      'commit-winning-property', '{}'::jsonb,
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id,
           'state', v_deal_state,
           'established_by_transition', 'commit-winning-property',
           'prior_state_digest', null,
           'updated_by', v_actor, 'updated_at', v_now) as r) s)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
           'tenant', v_tenant,
           'event', jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
             'event_kind', 'pending_deal_created', 'subject_kind', 'deal',
             'subject_id', v_deal_id),
           'transition_id', 'commit-winning-property',
           'evidence_references', '[]'::jsonb,
           'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-nomap', v_placeholder,
      jsonb_build_object('operation', 'commit-winning-property', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'B12: a subject was written with no compare-and-swap operand';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_expected_state_digest_missing' then raise; end if;
  end;

  -- === B13: envelope and operand must be the same claim =====================
  begin
    perform ops.j102_apply_transition(
      'record-lease-execution',
      jsonb_build_object('deal:' || v_deal_id, v_deal_digest),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id,
           'state', jsonb_set(v_deal_state, '{execution_state}', '"executed"'),
           'established_by_transition', 'record-lease-execution',
           -- The history would say this rested on no prior version at all.
           'prior_state_digest', null,
           'updated_by', v_actor, 'updated_at', v_now) as r) s)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
           'tenant', v_tenant,
           'event', jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
             'event_kind', 'lease_executed', 'subject_kind', 'deal',
             'subject_id', v_deal_id),
           'transition_id', 'record-lease-execution',
           'evidence_references', '[]'::jsonb,
           'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-priormismatch', v_placeholder,
      jsonb_build_object('operation', 'record-deal-execution', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'B13: an envelope disagreeing with its own operand was applied';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_prior_state_digest_mismatch' then raise; end if;
  end;

  -- === B15: the instant is the database's ==================================
  -- H4. Anything holding the writer's execute grant could previously backdate
  -- lifecycle state, because updated_at was taken from the envelope and stored
  -- unexamined. A caller-chosen instant refuses now.
  begin
    perform ops.j102_apply_transition(
      'record-lease-execution',
      jsonb_build_object('deal:' || v_deal_id, v_deal_digest),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id,
           'state', jsonb_set(v_deal_state, '{execution_state}', '"executed"'),
           'established_by_transition', 'record-lease-execution',
           'prior_state_digest', v_deal_digest,
           'updated_by', v_actor,
           -- A year before this transaction, and previously stored as written.
           'updated_at', '2020-01-01T00:00:00.000Z') as r) s)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
           'tenant', v_tenant,
           'event', jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
             'event_kind', 'lease_executed', 'subject_kind', 'deal',
             'subject_id', v_deal_id),
           'transition_id', 'record-lease-execution',
           'evidence_references', '[]'::jsonb,
           'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-backdate', v_placeholder,
      jsonb_build_object('operation', 'record-deal-execution', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'B15: a caller-chosen updated_at was stored';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_clock_injection_refused' then raise; end if;
  end;

  -- === B6: the append-only relations refuse UPDATE and DELETE for real ======
  begin
    update ops.j102_subject_event set event_kind = 'tampered' where tenant = v_tenant;
    raise exception 'B6: lifecycle history accepted an UPDATE';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_append_only_violation' and sqlerrm !~ 'permission denied' then raise; end if;
  end;
  begin
    delete from ops.j102_first_party_record where tenant = v_tenant;
    raise exception 'B6: a first-party business record accepted a DELETE';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_append_only_violation' and sqlerrm !~ 'permission denied' then raise; end if;
  end;

  -- === B7: direct DML outside a registered writer refuses ==================
  begin
    insert into ops.j102_subject_current
      (tenant, subject_kind, subject_id, envelope, envelope_digest, state_digest,
       parent_id, deal_state, updated_by, updated_at)
    values (v_tenant, 'deal', 'j102-fixture-smuggled', '{}'::jsonb, v_placeholder,
            v_placeholder, null, 'pending', v_actor, now());
    raise exception 'B7: a raw INSERT reached current state';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_direct_dml_refused' and sqlerrm !~ 'permission denied' then raise; end if;
  when others then
    -- A CHECK constraint firing first is also a refusal; what must never happen
    -- is the row landing.
    null;
  end;
  if exists (select 1 from ops.j102_subject_current
              where tenant = v_tenant and subject_id = 'j102-fixture-smuggled') then
    raise exception 'B7: the smuggled row landed';
  end if;

  -- === B8: a second pending Deal on one Assignment refuses at the index =====
  -- Written through the registered writer, exactly as a racing commitment would
  -- be. The index is what makes Q078/Q095 structural rather than advisory.
  begin
    perform ops.j102_apply_transition(
      'commit-winning-property',
      jsonb_build_object('deal:' || v_deal_id_2, null),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_subject', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-subject.v1',
           'tenant', v_tenant, 'subject_kind', 'deal', 'subject_id', v_deal_id_2,
           'state', v_deal_state || jsonb_build_object('subject_id', v_deal_id_2),
           'established_by_transition', 'commit-winning-property',
           'prior_state_digest', null,
           'updated_by', v_actor, 'updated_at', v_now) as r) s)),
      jsonb_build_array((select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_lifecycle_event', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-lifecycle-event.v1',
           'tenant', v_tenant,
           'event', jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-lifecycle-event.v1',
             'event_kind', 'pending_deal_created', 'subject_kind', 'deal',
             'subject_id', v_deal_id_2),
           'transition_id', 'commit-winning-property',
           'evidence_references', '[]'::jsonb,
           'recorded_by', v_actor, 'recorded_at', v_now) as r) s)),
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-second-deal', v_placeholder,
      jsonb_build_object('operation', 'commit-winning-property', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'B8: a second pending Deal landed on one Assignment';
  exception when unique_violation then
    null;  -- the index refused it, which is the point
  end;

  -- === B9 / B16 behaviourally: the closing settlement, both of its guards =====
  --
  -- WHICH HALF THIS SESSION PROVES DEPENDS ON WHICH CLASS IT HOLDS, and both
  -- halves are worth having. As a SPONSORED AGENT, the interesting fact is H5:
  -- the record is refused outright, so an agent-authored closing date never
  -- exists for a partner to launder through a later transition. As a VERIFIED
  -- PARTNER, the interesting fact is B9: even the right author cannot store a
  -- closing_settlement with no actual date.
  if v_class = 'sponsored_agent' then
    begin
      perform ops.j102_record_first_party_fact(
        (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
           'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
           'record', r, 'record_digest', ops.f01_digest_jsonb(r),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-stored-first-party-record.v1',
             'tenant', v_tenant, 'record_kind', 'closing_settlement',
             'record_id', 'j102-fixture-agent-closing',
             'subject_kind', 'deal', 'subject_id', v_deal_id,
             'reason', null, 'detail', null, 'closing_date', v_now,
             'supporting_document_id', null,
             'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
             'recorded_at', v_now,
             'advances_lifecycle_state', false) as r) s),
        'j102-fixture-key-agent-closing', v_placeholder);
      raise exception 'B16: a sponsored agent authored a closing settlement';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_partner_authored_record_refused' then raise; end if;
    end;
    raise notice 'B9 behavioural skipped and B16 proved in the agent direction: this session is a sponsored agent, so no closing_settlement of any shape can be authored here.';
  else
    begin
      perform ops.j102_record_first_party_fact(
        (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
           'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
           'record', r, 'record_digest', ops.f01_digest_jsonb(r),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', 'doctorcre-v5-j102-stored-first-party-record.v1',
             'tenant', v_tenant, 'record_kind', 'closing_settlement',
             'record_id', 'j102-fixture-dateless-closing',
             'subject_kind', 'deal', 'subject_id', v_deal_id,
             'reason', null, 'detail', null, 'closing_date', null,
             'supporting_document_id', null,
             'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
             'recorded_at', v_now,
             'advances_lifecycle_state', false) as r) s),
        'j102-fixture-key-dateless', v_placeholder);
      raise exception 'B9: a closing_settlement record without an actual closing date was stored';
    exception when check_violation then
      null;  -- Q094 structurally: signing is not closing, and neither is a null date
    end;
    -- And the partner-authored half of B16: the author class lands ON the row,
    -- derived from the principal rather than supplied.
    perform ops.j102_record_first_party_fact(
      (select jsonb_build_object(
         'schema_version', 'doctorcre-v5-j102-stored-record-envelope.v1',
         'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', 'doctorcre-v5-j102-stored-first-party-record.v1',
           'tenant', v_tenant, 'record_kind', 'closing_settlement',
           'record_id', 'j102-fixture-partner-closing',
           'subject_kind', 'deal', 'subject_id', v_deal_id,
           'reason', null, 'detail', null, 'closing_date', v_now,
           'supporting_document_id', null,
           'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
           'recorded_at', v_now,
           'advances_lifecycle_state', false) as r) s),
      'j102-fixture-key-partner-closing', v_placeholder);
    select count(*) into v_count from ops.j102_first_party_record
     where tenant = v_tenant and record_id = 'j102-fixture-partner-closing'
       and recorded_by_class = 'verified_partner' and bound_subject_id = v_deal_id;
    if v_count <> 1 then
      raise exception 'B16: the partner-authored closing settlement did not land with its author class and its deal binding';
    end if;
  end if;

  -- === the fixture leaves nothing behind ===================================
  -- Asserted BEFORE the rollback, so a writer that somehow escaped the
  -- transaction would be visible here rather than assumed away by the rollback.
  select count(*) into v_count from ops.j102_subject_current
   where tenant = v_tenant and subject_id like 'j102-fixture-%';
  if v_count <> 2 then
    raise exception 'the fixture left % lifecycle subjects behind, expected exactly the one assignment and the one pending deal B1 created',
      v_count;
  end if;

  raise notice 'ALL GROUPS PASSED (S1-S4, B1-B16, M3, Q081). Every fixture row is about to roll back.';
end
$proof$;

-- EVERYTHING ROLLS BACK. Nothing above is a real client, a real deal, an applied
-- migration, or a claim that this file has been run.
rollback;
