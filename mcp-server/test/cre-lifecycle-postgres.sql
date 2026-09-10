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
-- ===========================================================================
-- THE PREREQUISITE THIS FIXTURE CANNOT SATISFY, STATED FIRST BECAUSE IT
-- DETERMINES WHAT EVERY GROUP BELOW CAN AND CANNOT PROVE.
--
-- `j102_fixture_bootstrap_absent`: NOTHING IN THIS SLICE CREATES THE FIRST
-- LIFECYCLE SUBJECT.
--
--   * No operation in the store creates a prospect relationship, an assignment
--     or a property negotiation (V5_J102_UNWIRED_CAPABILITIES names all three).
--   * ops.j102_apply_transition REFUSES to create the primary subject of a
--     transition -- `j102_primary_subject_creation_refused` -- because a created
--     primary has no committed row for the transition's own prerequisites,
--     instrument kind and prior-state conditions to be checked against. An
--     earlier revision of this fixture opened an assignment that way and called
--     it a positive walk; it was a bypass with a bootstrap's name on it, and the
--     receipt's `prerequisites_checked: false` was an honest description of a
--     check that never ran.
--   * The only creations the writer admits are the two COUPLED subjects the
--     kernel itself creates -- the engagement of establish-client-and-engagement
--     and the pending deal of commit-winning-property -- and both require a
--     primary subject that is already committed.
--   * This file CREATES NO ROLE, and it cannot seed a row directly either: every
--     J102 relation carries ops.j102_guard_direct_dml, which requires the write
--     to arrive through a registered ops.j102_* writer and refuses a raw INSERT
--     from this DO block regardless of who owns the table. B7 below proves that
--     refusal rather than working around it.
--
-- THE CONSEQUENCE, NAMED RATHER THAN WORKED AROUND: this fixture executes NO
-- POSITIVE TRANSITION WALK and claims none. It does not open an assignment,
-- commit a property, create a deal, close one or cancel one, and no group below
-- should be read as evidence that any of those work end to end. What it does
-- prove is the refusals -- and the refusals are the reason the file exists,
-- because every one of them is reachable only by a direct caller holding the
-- carr_writer EXECUTE grant, which is exactly the position the store and the
-- kernel cannot police.
--
-- WHAT WOULD LIFT IT: one writer that creates a relationship, an assignment or a
-- property negotiation from evidence, owned by whoever owns Journey 1's entry
-- point. That is a source gap in the slice, not a defect in this file, and it is
-- not invented here as a seed verb.
-- ===========================================================================
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
--   S5  BLOCK-1 and both root corrections structurally: the three partner-only
--       transitions are partner-only in the database's own admission map, the
--       map carries an exact target and an exact event set for every transition,
--       and the writer really does consult all of it. Runs in every session,
--       including one with no principal.
--   S6  the admission map declares NO transition that creates its own primary
--       subject, which is `j102_fixture_bootstrap_absent` read off the installed
--       policy rather than off this file's prose
--   S7  HIGH-5 and HIGH-6 structurally: every event in the map declares its whole
--       nested payload and no derived kind, the writer carries every provenance
--       and citation refusal, the citations are compared against what the RECHECK
--       read, the receipt derives its coupled facts and decision refs instead of
--       echoing them, and both history relations carry the CHECK constraints that
--       are the floor beneath all of it. Runs in every session.
--   B5  an idempotent replay returns the committed outcome, and the same key
--       over a different payload refuses (proved on the first-party record
--       writer, which is the one writer this fixture can drive to a success)
--   B6  the append-only relations refuse UPDATE and DELETE for real
--   B7  direct DML outside a registered writer refuses for real
--   B9  a closing_settlement record with no actual closing date cannot be
--       stored at all (Q094 structurally)
--   B10 the private approval reader raises, so no approval can be manufactured
--       one layer down either
--   B16 H5: whichever half this session can prove -- a sponsored agent cannot
--       author a closing_settlement, or a partner-authored one carries its
--       author class on the row
--
-- AND THE TWELVE ADVERSARIAL GROUPS, which are the reason this revision exists.
-- Every one of them is a DIRECT call on ops.j102_apply_transition, holding
-- nothing more than the carr_writer EXECUTE grant. Every one of them SUCCEEDED
-- against some earlier revision of the writer:
--
--   P0  the missing bootstrap, named and asserted against the installed policy.
--   A1  BLOCKER-2's reproducer INCLUDING the obvious fix's defeat -- the wrong
--       subject's evidence, with that subject added to the lock set so that a
--       membership check would pass.
--   A2  the primary-creation masquerade: a transition proposing to CREATE the
--       subject it claims to advance, which is how the previous revision was
--       seeded and which is now refused by name.
--   A3  Diagnostics naming a weaker operation to obtain a stronger transition,
--       and a transition that does not exist at all.
--   A4  The direct carr_writer bypass of a partner-only transition, behavioural
--       wherever this session is a sponsored agent.
--   A5  HIGH-1 and the event set in full: an event lying about its own bytes, an
--       event claiming a different transition, an event for an unrelated
--       subject, an EMPTY event array, an EXTRA event, and a plausible WRONG
--       KIND on a correctly bound subject.
--   A6  An empty manifest, an unregistered evidence kind, and real evidence
--       presented from the wrong source.
--   A7  An unrelated proposed subject, and two subjects of one kind.
--   A8  A coupled subject proposed as a CREATION by a transition that only ever
--       updates it.
--   A9  A coupled write sent as a SUBSET: the primary subject alone, with the
--       coupled subject the same transition must move left out.
--   A10 The whole coupled set present, and the EVENT set short by one.
--   A11 HIGH-5: a FULLY CANONICAL call -- real evidence, correct binding, correct
--       event, correct target -- whose subject envelope claims to have been
--       established by a DIFFERENT transition, or by no transition at all; a
--       second unchecked claim hashed into the same authoritative bytes; a
--       foreign stored-subject schema; a header naming a subject its own state
--       does not describe; and the same two shape defects on the history side.
--       PROVED behaviourally: all of them refuse before any state is read.
--   A12 M-3: a compare-and-swap operand map that is SQL NULL, and one that is a
--       JSON scalar. Both used to slip three named refusals in silence or raise
--       an unnamed cast error; both now name the attempt. PROVED behaviourally.
--   U1  The target-value rewrites -- a permitted field carrying an unpermitted
--       value, a deleted field, a counter set to an arbitrary number, a cleared
--       reference kept. These are checked against the COMMITTED row, so they are
--       UNREACHABLE while `j102_fixture_bootstrap_absent` holds: the call
--       refuses earlier, at the compare-and-swap, for want of a row. The group
--       runs anyway, asserts that the call REFUSES, and reports which refusal it
--       got -- so the day a bootstrap lands, the group starts proving the thing
--       it is aimed at instead of silently passing.
--   U2  HIGH-6 and the nested-payload half, on the same terms and for the same
--       reason: an event of the right kind, on the right subject, in the right
--       number, naming an evidence reference the transition never rested on; a
--       record citing another assignment's authentic mandate; and a record citing
--       nothing at all. All three are decided against the evidence re-read under
--       the lock AND against the committed prior row, so they cannot fire here
--       either. Each reports the refusal it actually got.
--
-- TWO PREREQUISITES, checked before anything is attempted, each SKIPPING with a
-- notice rather than failing -- the same shape work-portfolio-postgres.sql and
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
--   * ANY positive lifecycle transition. See `j102_fixture_bootstrap_absent`
--     above. Every behavioural group below is a refusal or a first-party record
--     write.
--   * The exact-target and prior-condition checks (U1), and the event-detail and
--     evidence-citation checks (U2). All of them compare against a committed row
--     and against evidence re-read beside it, and no committed row can exist
--     here. A11 and A12 are the parts of the same corrections that DO fire,
--     because they are properties of the request rather than of stored state.
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
--     and one refusal. Both sessions take the same tier-2 advisory lock on that
--     key before either reads, so the loser's null operand meets the winner's
--     committed row -- but one session cannot contend with itself.
--   * Anything about a real client, a real deal or a real Salesforce record.

\set ON_ERROR_STOP on

begin;

do $proof$
declare
  v_actor            text;
  v_class            text;
  v_now              text;
  v_behavioural      boolean := false;
  v_partner          boolean := false;
  v_role             text;
  v_predicate        text;
  v_count            bigint;
  v_result           jsonb;
  v_replay           jsonb;
  v_fact             jsonb;
  v_fact_digest      text;
  v_fact2_digest     text;
  v_policy           jsonb;
  v_transition       text;
  v_rec              jsonb;
  v_rec2             jsonb;
  v_evt              jsonb;
  v_evt2             jsonb;
  v_subjects         jsonb;
  v_events           jsonb;
  v_manifest         jsonb;
  v_manifest_wrong   jsonb;
  v_refusal          text;

  v_tenant           constant text := 'carr-internal';
  v_placeholder      constant text := 'sha256:' || repeat('0', 64);
  v_lie              constant text := 'sha256:' || repeat('3', 64);
  v_env_schema       constant text := 'doctorcre-v5-j102-stored-record-envelope.v1';
  v_subject_schema   constant text := 'doctorcre-v5-j102-stored-lifecycle-subject.v1';
  v_event_schema     constant text := 'doctorcre-v5-j102-stored-lifecycle-event.v1';
  v_fact_schema      constant text := 'doctorcre-v5-j102-stored-first-party-record.v1';
  v_ev_schema        constant text := 'doctorcre-v5-j102-lifecycle-event.v1';
  v_assignment_id    constant text := 'j102-fixture-assignment-1';
  v_assignment_id_2  constant text := 'j102-fixture-assignment-2';
  v_engagement_id    constant text := 'j102-fixture-engagement-1';
  v_negotiation_id   constant text := 'j102-fixture-negotiation-1';
  v_property_id      constant text := 'j102-fixture-property-1';
  v_deal_id          constant text := 'j102-fixture-deal-1';
  -- The unrelated deal an adversarial event names. Nothing ever creates it, and
  -- the whole point of A5 is that naming it must not create it either.
  v_deal_id_c        constant text := 'j102-fixture-deal-c';
  v_fact_id          constant text := 'j102-fixture-fact-1';
  v_fact_id_2        constant text := 'j102-fixture-fact-2';

  -- One synthetic Assignment in the shape `open-assignment` would leave it. It is
  -- never committed to the database -- nothing can commit it -- and exists here so
  -- the adversarial payloads are otherwise well formed.
  v_assignment_state constant jsonb := jsonb_build_object(
    'subject_kind', 'assignment',
    'subject_id', v_assignment_id,
    'engagement_id', v_engagement_id,
    'assignment_phase', 'search',
    'open_negotiation_count', 2,
    'selected_property_id', null,
    'active_lease_draft_target_id', null,
    'pending_deal_id', null,
    'multi_target_exception_ref', null);

  -- The SAME assignment as a commitment would leave it: committed, with a
  -- selected property and a pending deal. U1 proposes this from a routine
  -- `open-assignment` call, which is the masquerade the target check exists for.
  v_assignment_committed constant jsonb := jsonb_build_object(
    'subject_kind', 'assignment',
    'subject_id', v_assignment_id,
    'engagement_id', v_engagement_id,
    'assignment_phase', 'committed',
    'open_negotiation_count', 2,
    'selected_property_id', v_property_id,
    'active_lease_draft_target_id', v_property_id,
    'pending_deal_id', v_deal_id,
    'multi_target_exception_ref', null);

  v_negotiation_state constant jsonb := jsonb_build_object(
    'subject_kind', 'property_negotiation',
    'subject_id', v_negotiation_id,
    'assignment_id', v_assignment_id,
    'property_id', v_property_id,
    'negotiation_state', 'loi_submitted');
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
    v_partner := v_class = 'verified_partner';
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
    -- The effect interpreter is the writer's own; a callable one confers nothing
    -- and is still a surface.
    if to_regprocedure('ops.j102_expected_value(jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)') is not null
       and has_function_privilege(v_role,
             'ops.j102_expected_value(jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)', 'EXECUTE') then
      raise exception 'S2: % can execute the transition-effect interpreter, which is private to the writer',
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

  -- === S5: the admission map exists, is complete, and the writer consults it ==
  --
  -- This group needs no principal and runs in every session, which matters: a
  -- session running AS a verified partner cannot demonstrate the agent refusal
  -- behaviourally, and this is what covers that direction there.
  if to_regprocedure('ops.j102_admission_policy()') is null then
    raise exception 'S5: the SQL admission map is absent, so the transition writer has nothing to admit against and every transition is performable by any grant holder';
  end if;
  v_policy := ops.j102_admission_policy();
  foreach v_role in array array[
    'commit-winning-property', 'record-deal-closing', 'cancel-pending-deal'
  ] loop
    if (v_policy -> 'transitions' -> v_role -> 'permitted_actor_classes')
         is distinct from '["verified_partner"]'::jsonb then
      raise exception 'S5: % is not partner-only in the SQL admission map, so a sponsored agent holding the carr_writer EXECUTE grant could perform it on a direct call',
        v_role;
    end if;
  end loop;
  -- ROOT BLOCKER 2, structurally: every transition declares a subject rule set
  -- with an EXACT target for every field it moves, and an EXACT event set. A map
  -- that named the fields without naming the values would admit
  -- `assignment_phase: "committed"` from a routine open-assignment call.
  for v_transition in select * from jsonb_object_keys(v_policy -> 'transitions') loop
    if jsonb_typeof(v_policy -> 'transitions' -> v_transition -> 'subjects') is distinct from 'object' then
      raise exception 'S5: % declares which fields it may move and not what it must move them TO', v_transition;
    end if;
    if jsonb_typeof(v_policy -> 'transitions' -> v_transition -> 'events') is distinct from 'array'
       or jsonb_array_length(v_policy -> 'transitions' -> v_transition -> 'events') < 1 then
      raise exception 'S5: % declares no event set, so a call could append any history it liked', v_transition;
    end if;
    for v_role in select * from jsonb_object_keys(
      v_policy -> 'transitions' -> v_transition -> 'subjects') loop
      if (v_policy -> 'transitions' -> v_transition -> 'subjects' -> v_role ->> 'mode') = 'update'
         and jsonb_typeof(v_policy -> 'transitions' -> v_transition -> 'subjects' -> v_role -> 'effects')
               is distinct from 'object' then
        raise exception 'S5: % may write a % and declares no resulting value for any of its fields',
          v_transition, v_role;
      end if;
    end loop;
  end loop;
  -- The map is worth nothing if the writer does not consult it. These are the
  -- exact call sites, asserted from the installed definition rather than from the
  -- file on disk.
  v_predicate := pg_get_functiondef(
    to_regprocedure('ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb)'));
  foreach v_role in array array[
    'ops.j102_admission_policy()', 'ops.f01_principal()', 'ops.j102_expected_value(',
    'j102_actor_class_not_permitted', 'j102_operation_transition_mismatch',
    'j102_unknown_transition', 'j102_field_not_movable_by_transition',
    'j102_subject_kind_not_written_by_transition', 'j102_coupled_subject_not_in_chain',
    'j102_prerequisite_not_met', 'j102_event_subject_not_advanced',
    'j102_event_digest_mismatch',
    -- ROOT BLOCKER 1
    'j102_primary_subject_creation_refused', 'j102_subject_creation_not_permitted',
    'j102_primary_subject_not_found',
    -- ROOT BLOCKER 2
    'j102_required_subject_not_proposed', 'j102_prior_condition_not_met',
    'j102_transition_effect_not_canonical', 'j102_transition_effect_missing',
    'j102_created_subject_shape_mismatch', 'j102_created_subject_field_not_canonical',
    'j102_event_set_mismatch', 'j102_event_missing_or_wrong',
    'j102_event_not_produced_by_transition'
  ] loop
    if position(v_role in v_predicate) = 0 then
      raise exception 'S5: the transition writer does not carry %; the admission map exists and is not consulted', v_role;
    end if;
  end loop;
  -- BLOCK-2: the recheck is handed the primary subject rather than left to check
  -- a caller-supplied manifest against itself.
  if to_regprocedure('ops.j102_recheck_evidence(jsonb,text,text,text)') is null then
    raise exception 'S5: the evidence recheck does not take the primary subject, so a manifest bound to another subject cannot be caught';
  end if;
  if to_regprocedure('ops.j102_recheck_evidence(jsonb)') is not null then
    raise exception 'S5: the single-argument recheck still exists; it establishes "the evidence was exact" without being told which subject it was exact FOR';
  end if;

  -- === S6: the map creates no primary subject -- j102_fixture_bootstrap_absent ==
  --
  -- Read off the installed policy rather than asserted in prose. If any
  -- transition ever declares its PRIMARY subject creatable, the bypass this
  -- correction removed is back, and the positive walks this fixture no longer
  -- executes would become possible again for the wrong reason.
  for v_transition in select * from jsonb_object_keys(v_policy -> 'transitions') loop
    v_role := v_policy -> 'transitions' -> v_transition ->> 'subject_kind';
    if (v_policy -> 'transitions' -> v_transition -> 'subjects' -> v_role ->> 'mode')
         is distinct from 'update' then
      raise exception 'S6: % declares its primary % creatable; a created primary has no committed row for its own prerequisites to be checked against',
        v_transition, v_role;
    end if;
  end loop;
  if (v_policy ->> 'subject_creation') is distinct from 'coupled_only_never_the_primary_subject' then
    raise exception 'S6: the admission map does not declare that creation is coupled-only';
  end if;

  -- === S7: the history is bound as tightly as the state ======================
  --
  -- HIGH-5 and HIGH-6, structurally, and this group runs in every session because
  -- none of it needs a principal or a committed row. The behavioural halves are
  -- A11/A12 (which refuse before any state is read, so they really fire) and U2
  -- (which cannot, and says so).

  -- Every event in the map declares its WHOLE payload, not only its kind and its
  -- subject. A map that named the kinds without naming the nested facts would
  -- admit a `deal_closed` event carrying somebody else's closing date.
  for v_transition in select * from jsonb_object_keys(v_policy -> 'transitions') loop
    for v_result in select * from jsonb_array_elements(
      v_policy -> 'transitions' -> v_transition -> 'events') loop
      if jsonb_typeof(v_result -> 'detail') is distinct from 'object' then
        raise exception 'S7: %''s % event declares no nested detail, so an event of the right kind on the right subject could say anything inside',
          v_transition, v_result ->> 'event_kind';
      end if;
      if v_result ? 'event_kind_from' then
        raise exception 'S7: %''s event kind is DERIVED; the kernel spells every lifecycle event kind literally and payment_${level} is a reason_id, not an event',
          v_transition;
      end if;
    end loop;
  end loop;
  if (v_policy ->> 'derived_event_kinds') is distinct from 'false' then
    raise exception 'S7: the admission map claims to derive an event kind';
  end if;
  -- The constants the writer holds every envelope to are the ones this fixture
  -- builds its own payloads from. If they ever diverge, every group above would
  -- refuse for a shape reason and prove nothing about what it is aimed at.
  if (v_policy ->> 'stored_subject_schema_version') is distinct from v_subject_schema
     or (v_policy ->> 'stored_event_schema_version') is distinct from v_event_schema
     or (v_policy ->> 'event_schema_version') is distinct from v_ev_schema then
    raise exception 'S7: the admission map''s stored-record constants are not the ones this rail writes: %',
      jsonb_build_object(
        'subject', v_policy ->> 'stored_subject_schema_version',
        'event_record', v_policy ->> 'stored_event_schema_version',
        'event', v_policy ->> 'event_schema_version');
  end if;

  -- The writer's own call sites for the two residual classes, read off the
  -- installed definition rather than from the file on disk.
  foreach v_role in array array[
    -- HIGH-5: the subject's provenance, and the envelope's identity.
    'j102_subject_provenance_mismatch', 'j102_subject_schema_version_mismatch',
    'j102_subject_tenant_mismatch', 'j102_subject_record_shape_unrecognised',
    'j102_subject_header_state_mismatch',
    -- HIGH-6: the whole event payload, and the evidence the history cites.
    'j102_event_detail_not_canonical', 'j102_event_detail_missing',
    'j102_event_detail_not_produced_by_transition',
    'j102_event_evidence_references_not_rechecked',
    'j102_event_evidence_reference_not_rechecked',
    'j102_event_evidence_reference_duplicated',
    'j102_event_payload_schema_version_mismatch',
    -- M-3: the operand map's own type.
    'j102_expected_state_digests_not_an_object',
    -- HIGH-6 again: the citations are compared against what the RECHECK read.
    'jsonb_array_elements(v_checked)'
  ] loop
    if position(v_role in v_predicate) = 0 then
      raise exception 'S7: the transition writer does not carry %; the history can still say something the transition did not do', v_role;
    end if;
  end loop;
  -- M-2: the two derived receipt fields come off the contract, and the kernel's
  -- diagnostic reason is carried under its own name rather than as a fact.
  if position('''coupled_facts_committed'', coalesce(v_contract -> ''coupled_facts''' in v_predicate) = 0
     or position('''decision_refs'', coalesce(v_contract -> ''decision_refs''' in v_predicate) = 0 then
    raise exception 'M-2: the receipt still echoes the caller''s account of what it committed';
  end if;
  if position('''caller_reported_reason_id''' in v_predicate) = 0 then
    raise exception 'M-2: the kernel''s diagnostic reason is not labelled as the caller''s assertion';
  end if;
  -- The recheck names the canonical reference it derives from its own readers.
  if position('j102_evidence_reference_unresolved' in
       pg_get_functiondef(to_regprocedure('ops.j102_recheck_evidence(jsonb,text,text,text)'))) = 0 then
    raise exception 'S7: the recheck does not derive a reference for the history to cite';
  end if;

  -- And the relations restate the floor beneath all of it. The preflight above
  -- compares COLUMNS; these are the constraints, named, so a database carrying an
  -- earlier candidate's relation without them is visible here.
  foreach v_role in array array[
    'j102_subject_provenance_present', 'j102_subject_record_schema_version',
    'j102_subject_record_tenant', 'j102_subject_record_digest_claim'
  ] loop
    if not exists (
      select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = 'j102_subject_current' and co.conname = v_role) then
      raise exception 'S7: ops.j102_subject_current carries no %; a lifecycle row could exist with no provenance, a foreign schema or a foreign tenant',
        v_role;
    end if;
  end loop;
  foreach v_role in array array[
    'j102_event_cites_evidence', 'j102_event_record_schema_version',
    'j102_event_record_tenant', 'j102_event_payload_schema_version',
    'j102_event_record_digest_claim'
  ] loop
    if not exists (
      select 1 from pg_constraint co join pg_class c on c.oid = co.conrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'ops' and c.relname = 'j102_subject_event' and co.conname = v_role) then
      raise exception 'S7: ops.j102_subject_event carries no %; a history row could cite no evidence at all', v_role;
    end if;
  end loop;
  -- The subject readback compares a STORED claim rather than recomputing the same
  -- bytes and comparing them to themselves.
  if position('v_row.envelope ->> ''record_digest''' in
       pg_get_functiondef(to_regprocedure('ops.j102_subject(text,text)'))) = 0 then
    raise exception 'S7: ops.j102_subject still passes a recomputation as its own expected digest, which verifies nothing';
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
    raise notice 'STRUCTURAL GROUPS PASSED (S1, S2, S3, S4, S5, S6, B9, B10, M3, Q081). Behavioural groups skipped: no admitted principal.';
    return;
  end if;

  -- ========================================================================
  -- BEHAVIOURAL GROUPS. Everything below runs as the derived actor and rolls
  -- back with the transaction.
  --
  -- NONE OF THEM IS A POSITIVE TRANSITION WALK. See
  -- `j102_fixture_bootstrap_absent` at the head of this file: no lifecycle
  -- subject can be created here, so every ops.j102_apply_transition call below
  -- is an adversarial one and every one of them must REFUSE.
  -- ========================================================================

  -- Two synthetic first-party records. These CAN be written -- the record layer
  -- is seedable even though the lifecycle rail is not, deliberately, because a
  -- record binds itself to a subject id and advances nothing on its own -- and
  -- they give the adversarial groups an AUTHENTIC record about the WRONG subject
  -- to present, which is the only shape that tests a binding at all.
  --
  -- `assignment_mandate` is used rather than one of the four partner-only kinds
  -- precisely so these seeds run whichever admitted class the session holds.
  v_rec := jsonb_build_object(
    'schema_version', v_fact_schema, 'tenant', v_tenant,
    'record_kind', 'assignment_mandate', 'record_id', v_fact_id,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id,
    'reason', null, 'detail', 'synthetic fixture mandate',
    'closing_date', null, 'supporting_document_id', null,
    'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
    'recorded_at', v_now, 'advances_lifecycle_state', false);
  v_result := ops.j102_record_first_party_fact(
    jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
      'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
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

  -- The SECOND mandate: identical in every way except the assignment it is
  -- about. Authentic, unmoved, correctly pinned, and about somebody else.
  v_rec := jsonb_build_object(
    'schema_version', v_fact_schema, 'tenant', v_tenant,
    'record_kind', 'assignment_mandate', 'record_id', v_fact_id_2,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id_2,
    'reason', null, 'detail', 'synthetic fixture mandate for the other assignment',
    'closing_date', null, 'supporting_document_id', null,
    'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
    'recorded_at', v_now, 'advances_lifecycle_state', false);
  perform ops.j102_record_first_party_fact(
    jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
      'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
    'j102-fixture-key-fact-2', v_placeholder);
  v_fact2_digest := ops.j102_first_party_record('assignment_mandate', v_fact_id_2)
                      ->> 'record_digest';

  -- The manifest the adversarial groups reuse: the RIGHT mandate for assignment
  -- 1, correctly pinned and correctly bound.
  v_manifest := jsonb_build_array(jsonb_build_object(
    'evidence_kind', 'search_initiation', 'source', 'first_party_record',
    'reader', 'ops.j102_first_party_record',
    'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                   'record_id', v_fact_id),
    'expected_record_digest', v_fact_digest,
    'binding', jsonb_build_object('subject_kind', 'assignment',
                                  'subject_id', v_assignment_id)));
  -- And the same manifest for the OTHER assignment's mandate: equally authentic,
  -- equally unmoved, about somebody else.
  v_manifest_wrong := jsonb_build_array(jsonb_build_object(
    'evidence_kind', 'search_initiation', 'source', 'first_party_record',
    'reader', 'ops.j102_first_party_record',
    'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                   'record_id', v_fact_id_2),
    'expected_record_digest', v_fact2_digest,
    'binding', jsonb_build_object('subject_kind', 'assignment',
                                  'subject_id', v_assignment_id_2)));

  -- The subject and event envelopes an `open-assignment` call would carry. The
  -- operand below is a PLACEHOLDER digest rather than a real one, because no
  -- assignment row exists to have a real one -- which is itself
  -- `j102_fixture_bootstrap_absent` showing through.
  v_rec := jsonb_build_object(
    'schema_version', v_subject_schema, 'tenant', v_tenant,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id,
    'state', v_assignment_state,
    'established_by_transition', 'open-assignment',
    'prior_state_digest', v_placeholder,
    'updated_by', v_actor, 'updated_at', v_now);
  -- THE EVENT IN ITS WHOLE CANONICAL SHAPE, nested detail and all. The writer now
  -- compares every non-identity key of the nested event against the admission
  -- map's template for `assignment_opened` and the cited references against what
  -- the recheck re-read, so an event carrying only its kind and its subject is no
  -- longer "otherwise well formed" and the adversarial groups below would refuse
  -- for the wrong reason if this were left short.
  v_evt := jsonb_build_object(
    'schema_version', v_event_schema, 'tenant', v_tenant,
    'event', jsonb_build_object('schema_version', v_ev_schema,
      'event_kind', 'assignment_opened',
      'subject_kind', 'assignment', 'subject_id', v_assignment_id,
      'engagement_id', v_engagement_id,
      'assignment_phase', 'search',
      'evidence_reference', v_fact_id),
    'transition_id', 'open-assignment',
    'evidence_references', jsonb_build_array(jsonb_build_object(
      'evidence_kind', 'search_initiation', 'source', 'first_party_record',
      'reference', v_fact_id)),
    'recorded_by', v_actor, 'recorded_at', v_now);
  v_subjects := jsonb_build_array(jsonb_build_object(
    'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
    'tenant', v_tenant, 'record', v_rec, 'record_digest', ops.f01_digest_jsonb(v_rec),
    'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder));
  v_events := jsonb_build_array(jsonb_build_object(
    'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
    'tenant', v_tenant, 'record', v_evt, 'record_digest', ops.f01_digest_jsonb(v_evt),
    'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder));

  -- === P0: the missing bootstrap, behaviourally ==============================
  --
  -- The one call that would seed a lifecycle subject, made exactly as the
  -- previous revision of this fixture made it: a well-formed `open-assignment`
  -- with correct evidence, a correct event, and an explicit NULL compare-and-swap
  -- operand meaning "this subject must be absent". That is a creation of the
  -- transition's own primary subject, and it is refused by name.
  begin
    v_rec2 := jsonb_set(v_rec, '{prior_state_digest}', 'null'::jsonb);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, null),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-p0', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment',
        'reason_id', 'search_initiation_opens_assignment',
        'coupled_facts', jsonb_build_array('assignment.assignment_phase'),
        'decision_refs', jsonb_build_array('Q080.D1')));
    raise exception 'P0: a transition CREATED the primary subject it claims to advance; the prerequisites it declares had no committed row to be checked against';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_primary_subject_creation_refused' then raise; end if;
  end;
  raise notice 'P0: j102_fixture_bootstrap_absent. No lifecycle subject exists or can be created here, so NO POSITIVE TRANSITION WALK RUNS IN THIS FILE and none is claimed. Every group below is a refusal. Lifting this needs one writer that creates a relationship, an assignment or a property negotiation from evidence.';

  -- ========================================================================
  -- ADVERSARIAL GROUPS A1-A10. Every one of them is a DIRECT call, which is the
  -- threat model: everything below would have been refused by the store and by
  -- the kernel, and every one of them reached a durable row against some earlier
  -- revision of this writer. All ten run whichever admitted class this session
  -- holds, except A4, which is behavioural only for a sponsored agent.
  -- ========================================================================

  -- === A1: the wrong subject, WITH the extra compare-and-swap key included ====
  --
  -- THE REVIEW'S BLOCKER-2 REPRODUCER, in the form that defeats the obvious fix.
  -- Requiring the manifest's binding to be a MEMBER of the lock set is not enough,
  -- because the caller supplies the lock set: it simply adds the other subject to
  -- the map, with a true operand, and goes on proposing this one. The record is
  -- authentic, unmoved and correctly pinned; what is wrong is that it is about
  -- assignment 2 and this call advances assignment 1.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      -- The extra key is HERE, and a membership check would find the binding in it.
      jsonb_build_object(
        'assignment:' || v_assignment_id, v_placeholder,
        'assignment:' || v_assignment_id_2, null),
      v_subjects, v_events, v_manifest_wrong,
      'j102-fixture-key-a1', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A1: evidence about another subject advanced this one, with the lock set covering it';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_evidence_not_bound_to_primary_subject' then raise; end if;
  end;

  -- === A2: the primary-creation masquerade, one transition over ==============
  --
  -- P0 proved it for `open-assignment`. This proves the refusal is a property of
  -- the MAP rather than of one transition: `record-loi-submission` advances a
  -- property negotiation, and a caller proposing to create that negotiation is
  -- proposing a lifecycle subject nothing in this rail creates.
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
      'state', v_negotiation_state,
      'established_by_transition', 'record-loi-submission',
      'prior_state_digest', null,
      'updated_by', v_actor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object(
        'property_negotiation:' || v_negotiation_id, null,
        'assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a2', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A2: a transition created the property negotiation it claims to advance';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_primary_subject_creation_refused' then raise; end if;
  end;

  -- === A3: the diagnostics cannot choose a weaker operation ==================
  --
  -- `record-deal-axis` is the routine, agent-permitted operation. Naming it beside
  -- a partner-only transition is how a gate keyed on the OPERATION would be
  -- satisfied while the transition performed was something else entirely. The
  -- pairing is checked before the idempotency key is claimed, so this attempt does
  -- not even burn a key.
  begin
    perform ops.j102_apply_transition(
      'record-deal-closing',
      jsonb_build_object('deal:' || v_deal_id, v_placeholder),
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a3', v_placeholder,
      jsonb_build_object('operation', 'record-deal-axis', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A3: a routine operation performed a partner-only transition';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_operation_transition_mismatch' then raise; end if;
  end;

  -- And a transition that is in no contract at all: p_transition_id used to be an
  -- unchecked string that reached the event table and the receipt.
  begin
    perform ops.j102_apply_transition(
      'set-the-phase',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a3b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A3: a transition that does not exist was performed and recorded';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_unknown_transition' then raise; end if;
  end;

  -- === A4: the direct carr_writer bypass of a partner-only transition =========
  --
  -- BLOCK-1's whole point. The EXECUTE grant on the transition writer reaches
  -- carr_writer, which always resolves to a sponsored agent, and the three
  -- partner-only transitions used to be reachable from there because the writer
  -- never asked the actor's CLASS. Behavioural only in an agent session; S5 covers
  -- the same fact structurally in a partner session.
  if not v_partner then
    begin
      perform ops.j102_apply_transition(
        'cancel-pending-deal',
        jsonb_build_object('deal:' || v_deal_id, v_placeholder),
        v_subjects, v_events, v_manifest,
        'j102-fixture-key-a4', v_placeholder,
        jsonb_build_object('operation', 'cancel-pending-deal', 'reason_id', 'x',
          'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
      raise exception 'A4: a sponsored agent performed a partner-only transition on a direct call';
    exception when insufficient_privilege then
      if sqlerrm !~ 'j102_actor_class_not_permitted' then raise; end if;
    end;
  else
    raise notice 'A4 behavioural half skipped: this session holds verified_partner, so the agent refusal cannot be demonstrated from here. S5 asserts the same admission map structurally.';
  end if;

  -- === A5: the event lies, and the event SET ==================================
  --
  -- Five separate attempts, because "at least one event" -- which is all the
  -- writer used to require -- catches none of them.

  -- (a) an event that does not hash to its own claim. The insert RECOMPUTES both
  -- digests, so the table's CHECK constraints were trivially satisfied while the
  -- envelope's own record_digest said something else inside the hashed bytes.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt, 'record_digest', v_lie,
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: an event whose bytes contradict its own digest claim was appended';
  exception when data_exception then
    if sqlerrm !~ 'j102_event_digest_mismatch' then raise; end if;
  end;

  -- (b) an event for an UNRELATED deal, riding along with a legitimate call. It
  -- used to land in history, be returned by j102_read, and describe a subject
  -- whose current state never moved.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'deal_closed',
        'subject_kind', 'deal', 'subject_id', v_deal_id_c),
      'transition_id', 'open-assignment', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      v_events || jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: history was written for a deal this transition never advanced';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_set_mismatch' then raise; end if;
  end;

  -- (c) an event claiming a DIFFERENT transition than the one being applied.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_opened',
        'subject_kind', 'assignment', 'subject_id', v_assignment_id),
      'transition_id', 'commit-winning-property', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5c', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: history named a transition that did not run';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_transition_mismatch' then raise; end if;
  end;

  -- (d) NO EVENTS AT ALL: a state change nobody can audit afterwards.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, '[]'::jsonb, v_manifest,
      'j102-fixture-key-a5d', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: a transition applied with no history at all';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_no_event_envelopes' and sqlerrm !~ 'j102_event_set_mismatch' then raise; end if;
  end;

  -- (e) A PLAUSIBLE WRONG KIND on a correctly bound subject -- the residual the
  -- previous correction acknowledged and left open. The subject is one this call
  -- advances, the transition is the one being applied, the bytes hash to their
  -- claim; only the KIND is a lie, and a reader searching history by kind is
  -- exactly who it lies to.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_committed',
        'subject_kind', 'assignment', 'subject_id', v_assignment_id),
      'transition_id', 'open-assignment', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a5e', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A5: an assignment_committed event was appended by an open-assignment call';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_missing_or_wrong' then raise; end if;
  end;

  -- === A6: empty, unknown, and wrong-source evidence ==========================
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events, '[]'::jsonb,
      'j102-fixture-key-a6a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A6: a transition applied with no evidence to re-read';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_evidence_recheck_required' then raise; end if;
  end;

  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events,
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'a_convincing_email', 'source', 'first_party_record',
        'reader', 'ops.j102_first_party_record',
        'selector', jsonb_build_object('record_kind', 'assignment_mandate',
                                       'record_id', v_fact_id),
        'expected_record_digest', v_fact_digest,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-a6b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A6: an evidence kind no contract admits was accepted';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_unknown_evidence_kind' then raise; end if;
  end;

  -- Real evidence, presented from the wrong SOURCE. A first-party record
  -- announced as a document takes the document branch, which never re-reads the
  -- record's own typed binding.
  begin
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects, v_events,
      jsonb_build_array(jsonb_build_object(
        'evidence_kind', 'search_initiation', 'source', 'f01_document',
        'reader', 'ops.f01_read.document',
        'selector', jsonb_build_object('document_id', v_fact_id),
        'expected_version_no', '1', 'expected_content_digest', v_fact_digest,
        'expected_link_digest', v_placeholder,
        'binding', jsonb_build_object('subject_kind', 'assignment',
                                      'subject_id', v_assignment_id))),
      'j102-fixture-key-a6c', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A6: a first-party record was re-read as a document, skipping its own binding';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_evidence_source_mismatch' then raise; end if;
  end;

  -- === A7: the unrelated proposed subject, and the duplicate kind =============
  --
  -- An allowed operation carrying a second subject beside its real one: a new
  -- updated_by and updated_at on a client who had nothing to do with it at best,
  -- an arbitrary state change at worst.
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'deal', 'subject_id', v_deal_id_c,
      'state', jsonb_build_object(
        'subject_kind', 'deal', 'subject_id', v_deal_id_c,
        'assignment_id', v_assignment_id, 'property_id', v_property_id,
        'instrument_kind', 'lease', 'deal_state', 'closed',
        'execution_state', 'executed', 'diligence_state', 'not_applicable',
        'closing_state', 'closed', 'commission_agreement_state', 'absent',
        'invoice_state', 'not_invoiced', 'payment_state', 'unpaid',
        'completion_state', 'open', 'cancellation_reason', null,
        'closing_date', v_now),
      'established_by_transition', 'open-assignment',
      'prior_state_digest', null,
      'updated_by', v_actor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object(
        'assignment:' || v_assignment_id, v_placeholder,
        'deal:' || v_deal_id_c, null),
      v_subjects || jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a7a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A7: an open-assignment call wrote a closed deal beside its assignment';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_kind_not_written_by_transition' then raise; end if;
  end;

  -- Two subjects of one kind: which one is the primary the evidence must bind to?
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_assignment_id_2,
      'state', v_assignment_state || jsonb_build_object('subject_id', v_assignment_id_2),
      'established_by_transition', 'open-assignment',
      'prior_state_digest', v_placeholder,
      'updated_by', v_actor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object(
        'assignment:' || v_assignment_id, v_placeholder,
        'assignment:' || v_assignment_id_2, v_placeholder),
      v_subjects || jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a7b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A7: one transition advanced two assignments at once';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_duplicate_proposed_subject_kind' then raise; end if;
  end;

  -- === A8: a coupled subject proposed as a CREATION ===========================
  --
  -- `record-loi-submission` writes the negotiation AND the assignment it belongs
  -- to, and it UPDATES both. A caller proposing to create the assignment beside a
  -- genuine negotiation is asking for a row nothing in this rail creates, on the
  -- strength of a transition that never creates one.
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_assignment_id,
      'state', v_assignment_state,
      'established_by_transition', 'record-loi-submission',
      'prior_state_digest', null,
      'updated_by', v_actor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object(
        'property_negotiation:' || v_negotiation_id, v_placeholder,
        'assignment:' || v_assignment_id, null),
      jsonb_build_array(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant,
          'record', jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'updated_at', v_now),
          'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'updated_at', v_now)),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a8', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A8: a transition that only updates an assignment created one';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_subject_creation_not_permitted' then raise; end if;
  end;

  -- === A9: the coupled write, sent as a subset ================================
  --
  -- Q082: coupled facts land together or refuse together. `record-loi-submission`
  -- moves the negotiation to loi_submitted AND moves its assignment to
  -- negotiation; a call carrying only the negotiation leaves an assignment that
  -- does not know one of its negotiations was submitted. `writes` admitted this,
  -- because a subset of a permitted set is still a subset.
  begin
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object('property_negotiation:' || v_negotiation_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant,
        'record', jsonb_build_object(
          'schema_version', v_subject_schema, 'tenant', v_tenant,
          'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
          'state', v_negotiation_state,
          'established_by_transition', 'record-loi-submission',
          'prior_state_digest', v_placeholder,
          'updated_by', v_actor, 'updated_at', v_now),
        'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
          'schema_version', v_subject_schema, 'tenant', v_tenant,
          'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
          'state', v_negotiation_state,
          'established_by_transition', 'record-loi-submission',
          'prior_state_digest', v_placeholder,
          'updated_by', v_actor, 'updated_at', v_now)),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a9', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A9: half of a coupled write landed without the other half';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_required_subject_not_proposed' then raise; end if;
  end;

  -- === A10: the whole subject set, and an event set short by one ==============
  --
  -- The mirror of A9 on the history side, using the same transition: both
  -- subjects are present and the single event `record-loi-submission` appends is
  -- replaced by an event for the OTHER subject it writes. The subject check
  -- passes, the "event names a subject this call advances" check passes, and only
  -- the exact event set refuses it.
  begin
    v_evt2 := jsonb_build_object(
      'schema_version', v_event_schema, 'tenant', v_tenant,
      'event', jsonb_build_object('schema_version', v_ev_schema,
        'event_kind', 'assignment_opened',
        'subject_kind', 'assignment', 'subject_id', v_assignment_id),
      'transition_id', 'record-loi-submission', 'evidence_references', '[]'::jsonb,
      'recorded_by', v_actor, 'recorded_at', v_now);
    perform ops.j102_apply_transition(
      'record-loi-submission',
      jsonb_build_object(
        'property_negotiation:' || v_negotiation_id, v_placeholder,
        'assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant,
          'record', jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'updated_at', v_now),
          'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'property_negotiation', 'subject_id', v_negotiation_id,
            'state', v_negotiation_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'updated_at', v_now)),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
        -- The coupled assignment, provenanced to the transition THIS call
        -- applies. Reusing the open-assignment envelope here would now refuse at
        -- j102_subject_provenance_mismatch and A10 would stop proving anything
        -- about the event set, which is what it is for.
        jsonb_build_object(
          'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
          'tenant', v_tenant,
          'record', jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'assignment', 'subject_id', v_assignment_id,
            'state', v_assignment_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'updated_at', v_now),
          'record_digest', ops.f01_digest_jsonb(jsonb_build_object(
            'schema_version', v_subject_schema, 'tenant', v_tenant,
            'subject_kind', 'assignment', 'subject_id', v_assignment_id,
            'state', v_assignment_state,
            'established_by_transition', 'record-loi-submission',
            'prior_state_digest', v_placeholder,
            'updated_by', v_actor, 'updated_at', v_now)),
          'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a10', v_placeholder,
      jsonb_build_object('operation', 'record-loi-submission', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A10: a transition appended an event it does not produce, in place of the one it does';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_missing_or_wrong' then raise; end if;
  end;

  -- === A11: the subject's own provenance, and its envelope's identity =========
  --
  -- HIGH-5's reproducer, and it needs nothing exotic: a FULLY CANONICAL call --
  -- real evidence, correctly bound, correct event, correct target -- whose
  -- subject envelope claims to have been established by a DIFFERENT transition.
  -- The forged string is hashed into the envelope's own record_digest, so the
  -- digest check agrees with it; ops.j102_subject returns it as the row's
  -- provenance and this writer's own readback echoes it back. Nothing compared it
  -- to the transition that ran. These four all refuse BEFORE any state is read,
  -- so unlike U1 below they are proved here rather than reported as unproven.
  begin
    v_rec2 := jsonb_set(v_rec, '{established_by_transition}', '"record-deal-closing"'::jsonb);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: an assignment recorded its state as established by a partner-only closing that never ran';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_subject_provenance_mismatch' then raise; end if;
  end;

  -- The same field naming no transition at all. j102_unknown_transition guards
  -- only p_transition_id, so this string reached the durable row unexamined.
  begin
    v_rec2 := jsonb_set(v_rec, '{established_by_transition}', '"set-the-phase"'::jsonb);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a subject was established by a transition that does not exist';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_subject_provenance_mismatch' then raise; end if;
  end;

  -- A SECOND, UNCHECKED CLAIM hashed into the same authoritative bytes. The
  -- envelope is otherwise perfect; it simply carries one more key, which
  -- ops.j102_read would hand a reviewer as part of the row.
  begin
    v_rec2 := v_rec || jsonb_build_object('approved_by', 'joe');
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11c', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: an unchecked approval claim landed inside an authoritative lifecycle row';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_record_shape_unrecognised' then raise; end if;
  end;

  -- A FOREIGN SCHEMA VERSION on the stored subject, and a header naming a subject
  -- its own state does not describe.
  begin
    v_rec2 := jsonb_set(v_rec, '{schema_version}',
      to_jsonb('doctorcre-v5-j102-stored-lifecycle-subject.v0'::text));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11d', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a lifecycle subject was stored under a schema this rail does not write';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_schema_version_mismatch' then raise; end if;
  end;

  begin
    v_rec2 := jsonb_set(v_rec, '{subject_id}', to_jsonb(v_assignment_id_2));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id_2, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-a11e', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a row whose primary key and whose state describe different subjects was written';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_subject_header_state_mismatch' then raise; end if;
  end;

  -- The same class on the history side: an event record carrying a key the store
  -- never writes, and a nested event stamped with a foreign kernel schema.
  begin
    v_evt2 := v_evt || jsonb_build_object('authorised_by', 'joe');
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a11f', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: an unchecked authorisation claim landed inside a history row';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_record_shape_unrecognised' then raise; end if;
  end;

  begin
    v_evt2 := jsonb_set(v_evt, '{event,schema_version}',
      to_jsonb('doctorcre-v5-j102-lifecycle-event.v0'::text));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-a11g', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A11: a lifecycle event was appended under a kernel schema the kernel does not stamp';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_event_payload_schema_version_mismatch' then raise; end if;
  end;

  -- === A12: the compare-and-swap operand map that is not a map ================
  --
  -- M-3. A SQL NULL here used to propagate through every `?` and `->` in the
  -- admission loop as NULL, which `if` reads as false: BOTH creation refusals and
  -- j102_expected_state_digest_missing were skipped in silence, the call refused
  -- further down for a different reason, and a creation attempted this way was
  -- reported as `created_subject_kinds: []`. A JSON scalar was worse -- it reached
  -- jsonb_object_keys and raised an unnamed cast error rather than any J102
  -- refusal. Both now name the attempt.
  begin
    perform ops.j102_apply_transition(
      'open-assignment', null,
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a12a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A12: a transition ran with no compare-and-swap operands at all';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_expected_state_digests_not_an_object' then raise; end if;
  end;

  begin
    perform ops.j102_apply_transition(
      'open-assignment', 'null'::jsonb,
      v_subjects, v_events, v_manifest,
      'j102-fixture-key-a12b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'A12: a JSON scalar was accepted as a compare-and-swap operand map';
  exception when invalid_parameter_value then
    if sqlerrm !~ 'j102_expected_state_digests_not_an_object' then raise; end if;
  end;

  -- ========================================================================
  -- === U2: the history that lies inside a correct event, UNPROVEN here =======
  --
  -- HIGH-6 and the nested-payload half of the same class. These payloads carry
  -- the RIGHT event kind on the RIGHT subject in the RIGHT number -- the checks
  -- A5 and A10 cover all pass -- and lie about what is inside: the evidence the
  -- event names, and the evidence the record cites as what the transition rested
  -- on. The writer decides both against the evidence it RE-READ under the lock
  -- and against the committed prior row, exactly as it decides the state, so like
  -- U1 they cannot fire in a file with no committed subject: the compare-and-swap
  -- refuses first, for want of an assignment.
  --
  -- They run anyway, on the same terms as U1: a refusal is still a refusal, the
  -- day a bootstrap lands they begin proving the thing they are aimed at, and
  -- what is reported below is the refusal actually observed rather than the one
  -- intended.
  -- ========================================================================
  v_refusal := null;
  begin
    v_evt2 := jsonb_set(v_evt, '{event,evidence_reference}', to_jsonb(v_fact_id_2));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-u2a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'U2: an event named a mandate the transition never rested on';
  exception when others then
    v_refusal := sqlerrm;
    if sqlerrm !~ 'j102_event_detail_not_canonical'
       and sqlerrm !~ 'j102_stale_subject_digest'
       and sqlerrm !~ 'j102_primary_subject_not_found' then
      raise;
    end if;
  end;
  if v_refusal ~ 'j102_stale_subject_digest' or v_refusal ~ 'j102_primary_subject_not_found' then
    raise notice 'U2 (nested event fact) UNPROVEN (j102_fixture_bootstrap_absent): the lying event refused, but at the compare-and-swap for want of a committed assignment rather than at the event-detail check it is aimed at. Observed: %',
      v_refusal;
  else
    raise notice 'U2 PROVED at the event-detail check: %', v_refusal;
  end if;

  v_refusal := null;
  begin
    -- The history citing a mandate that IS authentic, IS unmoved and belongs to
    -- somebody else -- the A1 payload, moved from the manifest into the record.
    v_evt2 := jsonb_set(v_evt, '{evidence_references}', jsonb_build_array(
      jsonb_build_object('evidence_kind', 'search_initiation',
        'source', 'first_party_record', 'reference', v_fact_id_2)));
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-u2b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'U2: history cited evidence this transition never re-read';
  exception when others then
    v_refusal := sqlerrm;
    if sqlerrm !~ 'j102_event_evidence_reference_not_rechecked'
       and sqlerrm !~ 'j102_stale_subject_digest'
       and sqlerrm !~ 'j102_primary_subject_not_found' then
      raise;
    end if;
  end;
  if v_refusal ~ 'j102_stale_subject_digest' or v_refusal ~ 'j102_primary_subject_not_found' then
    raise notice 'U2 (cited evidence) UNPROVEN (j102_fixture_bootstrap_absent): refused at the compare-and-swap. Observed: %',
      v_refusal;
  end if;

  v_refusal := null;
  begin
    -- And the empty array, which is how history stops saying anything at all.
    v_evt2 := jsonb_set(v_evt, '{evidence_references}', '[]'::jsonb);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      v_subjects,
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_event',
        'tenant', v_tenant, 'record', v_evt2, 'record_digest', ops.f01_digest_jsonb(v_evt2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_manifest,
      'j102-fixture-key-u2c', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'U2: an event cited no evidence at all and was appended anyway';
  exception when others then
    v_refusal := sqlerrm;
    if sqlerrm !~ 'j102_event_evidence_references_not_rechecked'
       and sqlerrm !~ 'j102_event_cites_evidence'
       and sqlerrm !~ 'j102_stale_subject_digest'
       and sqlerrm !~ 'j102_primary_subject_not_found' then
      raise;
    end if;
  end;
  if v_refusal ~ 'j102_stale_subject_digest' or v_refusal ~ 'j102_primary_subject_not_found' then
    raise notice 'U2 (empty citation) UNPROVEN (j102_fixture_bootstrap_absent): refused at the compare-and-swap. The relation''s own j102_event_cites_evidence CHECK is the floor beneath it and is asserted structurally in S7. Observed: %',
      v_refusal;
  end if;

  -- ========================================================================
  -- === U1: the target-value rewrites, and why they are UNPROVEN here =========
  --
  -- These are the payloads the second root correction exists for: a permitted
  -- field carrying a value the kernel would never produce. They are decided
  -- against the COMMITTED row -- that is the whole point of them -- and no
  -- committed row can exist in this file, so the writer refuses them EARLIER,
  -- at the compare-and-swap, for want of a subject.
  --
  -- The group still runs, because a refusal is still a refusal and because the
  -- day a bootstrap lands these attempts start proving the thing they are aimed
  -- at. What it does NOT do is claim the target check fired: it reports the
  -- refusal it actually got.
  -- ========================================================================
  v_refusal := null;
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_assignment_id,
      -- A ROUTINE OPEN-ASSIGNMENT CALL, PROPOSING A COMMITMENT: the phase, the
      -- selected property, the lease-draft target and a pending deal id, none of
      -- which any evidence in this manifest establishes.
      'state', v_assignment_committed,
      'established_by_transition', 'open-assignment',
      'prior_state_digest', v_placeholder,
      'updated_by', v_actor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-u1a', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'U1: a routine open-assignment call committed an assignment to a property and a pending deal';
  exception when others then
    v_refusal := sqlerrm;
    if sqlerrm !~ 'j102_transition_effect_not_canonical'
       and sqlerrm !~ 'j102_field_not_movable_by_transition'
       and sqlerrm !~ 'j102_stale_subject_digest'
       and sqlerrm !~ 'j102_primary_subject_not_found' then
      raise;
    end if;
  end;
  if v_refusal ~ 'j102_stale_subject_digest' or v_refusal ~ 'j102_primary_subject_not_found' then
    raise notice 'U1 UNPROVEN (j102_fixture_bootstrap_absent): the masquerade refused, but at the compare-and-swap for want of a committed assignment rather than at the target check it is aimed at. Observed: %',
      v_refusal;
  else
    raise notice 'U1 PROVED at the target check: %', v_refusal;
  end if;

  -- The same shape, one field wide: a permitted field DELETED rather than moved.
  v_refusal := null;
  begin
    v_rec2 := jsonb_build_object(
      'schema_version', v_subject_schema, 'tenant', v_tenant,
      'subject_kind', 'assignment', 'subject_id', v_assignment_id,
      'state', v_assignment_state - 'assignment_phase',
      'established_by_transition', 'open-assignment',
      'prior_state_digest', v_placeholder,
      'updated_by', v_actor, 'updated_at', v_now);
    perform ops.j102_apply_transition(
      'open-assignment',
      jsonb_build_object('assignment:' || v_assignment_id, v_placeholder),
      jsonb_build_array(jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_lifecycle_subject',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)),
      v_events, v_manifest,
      'j102-fixture-key-u1b', v_placeholder,
      jsonb_build_object('operation', 'open-cre-assignment', 'reason_id', 'x',
        'coupled_facts', '[]'::jsonb, 'decision_refs', '[]'::jsonb));
    raise exception 'U1: an assignment was written with no phase at all';
  exception when others then
    v_refusal := sqlerrm;
    if sqlerrm !~ 'j102_transition_effect_missing'
       and sqlerrm !~ 'j102_field_not_movable_by_transition'
       and sqlerrm !~ 'j102_stale_subject_digest'
       and sqlerrm !~ 'j102_primary_subject_not_found' then
      raise;
    end if;
  end;
  if v_refusal ~ 'j102_stale_subject_digest' or v_refusal ~ 'j102_primary_subject_not_found' then
    raise notice 'U1 (deleted field) UNPROVEN (j102_fixture_bootstrap_absent): refused at the compare-and-swap. Observed: %',
      v_refusal;
  end if;

  -- === B5: an idempotent replay, on the one writer this file can drive ========
  --
  -- The transition writer cannot reach a success here, so the replay property is
  -- proved where it CAN be: the first-party record writer settled a key above,
  -- and the same key must return the same stored outcome rather than writing a
  -- second row.
  -- The SECOND mandate's own record, rebuilt here rather than reused from a
  -- variable the adversarial groups have since overwritten.
  v_rec2 := jsonb_build_object(
    'schema_version', v_fact_schema, 'tenant', v_tenant,
    'record_kind', 'assignment_mandate', 'record_id', v_fact_id_2,
    'subject_kind', 'assignment', 'subject_id', v_assignment_id_2,
    'reason', null, 'detail', 'synthetic fixture mandate for the other assignment',
    'closing_date', null, 'supporting_document_id', null,
    'recorded_by', v_actor, 'recorded_by_authorization_class', v_class,
    'recorded_at', v_now, 'advances_lifecycle_state', false);
  v_replay := ops.j102_record_first_party_fact(
    jsonb_build_object(
      'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
      'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
      'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
    'j102-fixture-key-fact-2', v_placeholder);
  if v_replay is null or (v_replay ->> 'record_id') <> v_fact_id_2 then
    raise exception 'B5: the settled key did not replay its stored outcome: %', v_replay;
  end if;
  select count(*) into v_count from ops.j102_first_party_record where tenant = v_tenant;
  if v_count <> 2 then
    raise exception 'B5: a replay appended a second row (% rows)', v_count;
  end if;
  -- The same key over a DIFFERENT payload is a substitution attempt.
  begin
    perform ops.j102_record_first_party_fact(
      jsonb_build_object(
        'schema_version', v_env_schema, 'record_kind', 'stored_first_party_record',
        'tenant', v_tenant, 'record', v_rec2, 'record_digest', ops.f01_digest_jsonb(v_rec2),
        'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder),
      'j102-fixture-key-fact-2', 'sha256:' || repeat('9', 64));
    raise exception 'B5: a different payload replayed under the same key';
  exception when unique_violation then
    if sqlerrm !~ 'j102_idempotency_payload_mismatch' then raise; end if;
  end;

  -- === B6: the append-only relations refuse UPDATE and DELETE for real ======
  begin
    update ops.j102_first_party_record set record_id = 'tampered' where tenant = v_tenant;
    raise exception 'B6: a first-party business record accepted an UPDATE';
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
  --
  -- AND THIS IS ALSO WHY THIS FIXTURE CANNOT SEED A SUBJECT. The guard reads the
  -- PL/pgSQL call stack and requires a registered ops.j102_* writer frame, so a
  -- raw INSERT from this DO block is refused whoever owns the table. Seeding
  -- around it would need either a new writer -- which is the missing bootstrap,
  -- and inventing one here would be inventing the capability the slice does not
  -- have -- or disabling the guard, which is the thing being tested.
  begin
    insert into ops.j102_subject_current
      (tenant, subject_kind, subject_id, envelope, envelope_digest, state_digest,
       parent_id, deal_state, updated_by, updated_at)
    values (v_tenant, 'assignment', v_assignment_id, '{}'::jsonb, v_placeholder,
            v_placeholder, null, null, v_actor, now());
    raise exception 'B7: a raw INSERT reached current state';
  exception when insufficient_privilege then
    if sqlerrm !~ 'j102_direct_dml_refused' and sqlerrm !~ 'permission denied' then raise; end if;
  when others then
    -- A CHECK constraint firing first is also a refusal; what must never happen
    -- is the row landing.
    null;
  end;
  if exists (select 1 from ops.j102_subject_current where tenant = v_tenant) then
    raise exception 'B7: the smuggled row landed';
  end if;

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
           'schema_version', v_env_schema,
           'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
           'record', r, 'record_digest', ops.f01_digest_jsonb(r),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', v_fact_schema,
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
           'schema_version', v_env_schema,
           'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
           'record', r, 'record_digest', ops.f01_digest_jsonb(r),
           'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
           from (select jsonb_build_object(
             'schema_version', v_fact_schema,
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
         'schema_version', v_env_schema,
         'record_kind', 'stored_first_party_record', 'tenant', v_tenant,
         'record', r, 'record_digest', ops.f01_digest_jsonb(r),
         'domain_policy_digest', v_placeholder, 'decision_subset_digest', v_placeholder)
         from (select jsonb_build_object(
           'schema_version', v_fact_schema,
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
  --
  -- NO LIFECYCLE SUBJECT AND NO EVENT, and that is now an exact number rather
  -- than a count of what the positive walks left: nothing here can create a
  -- subject, and every adversarial group above must have refused whole. A single
  -- row in either relation means one of them half-landed.
  select count(*) into v_count from ops.j102_subject_current where tenant = v_tenant;
  if v_count <> 0 then
    raise exception 'the fixture left % lifecycle subjects behind; every group above is a refusal and none of them may write a row',
      v_count;
  end if;
  select count(*) into v_count from ops.j102_subject_event where tenant = v_tenant;
  if v_count <> 0 then
    raise exception 'the fixture left % lifecycle events behind; a refused transition wrote history',
      v_count;
  end if;

  raise notice 'ALL RUNNABLE GROUPS PASSED (S1-S7, P0, A1-A12, U1 and U2 as far as they can go, B5, B6, B7, B9, B10, B16, M3, Q081). NO POSITIVE TRANSITION WALK RAN: j102_fixture_bootstrap_absent. A11 and A12 are behaviourally PROVED because they refuse before any state is read; U1 and U2 are decided against a committed row and therefore cannot fire here, and each reports the refusal it actually got rather than the one it is aimed at. Every fixture row is about to roll back.';
end
$proof$;

-- EVERYTHING ROLLS BACK. Nothing above is a real client, a real deal, an applied
-- migration, or a claim that this file has been run.
rollback;
