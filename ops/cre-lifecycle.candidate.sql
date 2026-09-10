-- DoctorCRE v5 slice V5-J102: durable persistence for the healthcare CRE
-- lifecycle -- Prospect through Client/Engagement/Assignment/Deal (requirements
-- Q069, Q072, Q077-Q083, Q094-Q096, Q103; gate journey-one-production-accepted).
--
-- CANDIDATE SQL. This file is source, not a migration. It carries no migration
-- ordinal, is not listed in public.schema_migrations, creates no role and no
-- user, and is not applied to Production by anything in this slice. Landing it
-- as a numbered migration is a separate, Joe-gated act.
--
-- WHAT THIS IS. cre-lifecycle.v5.js can judge a transition against loaded state
-- and loaded evidence, and cre-lifecycle-store.v5.js can build the exact
-- envelopes for it. Neither can store anything. This file is the missing durable
-- half: authoritative current state per subject, append-only events beside it,
-- the first-party business records evidence is read from, the external
-- Salesforce references, the correction receipts, and the one writer that
-- applies a whole coupled transition or none of it.
--
-- IT IS NOT A SECOND DECISION LAYER. The transition table, the refusal matrix
-- and the coupled-fact sets live in the kernel and are not restated here. What
-- this file enforces is the half a kernel cannot: that the state a decision was
-- taken against has not moved, that the EXACT evidence is still what it was when
-- read, that the whole coupled set lands together, and that nobody writes any of
-- it except through a registered writer that derives its own actor.
--
-- NOTHING IS RE-DERIVED THAT ALREADY HAS A HOME. The tenant, the canonical JSON,
-- the digest, the instant parser, the server clock and the authenticated
-- principal all come from ops.f01_* in domain.sql, and this file CALLS them
-- rather than restating them. Two canonicalizers that agree today are two that
-- can disagree after one edit, and a second principal derivation would be a
-- second answer to "who is writing". The prerequisite block below refuses to
-- apply this file at all against a database that does not carry them.
--
-- THE ONE APPROVAL READER IS PRIVATE AND ALWAYS RAISES, deliberately, on the
-- same terms as ops.benchmark_gate_zero_outcome(): see
-- ops.j102_typed_approval() below. Q077's "approved representation equivalent"
-- and Q095's "explicit approved exception" both rest on a typed authenticated
-- approval, and nothing in this record layer PRODUCES one. Refusing is the
-- honest state; the alternative would be inventing the business policy the
-- approval is supposed to carry.
--
-- FOUR THINGS ARE DELIBERATELY NOT DONE HERE, because each would move an
-- authority this slice does not hold:
--
--   1. NO SALESFORCE MAPPING. ops.j102_salesforce_reference stores the
--      opportunity's own name and phase and a link to a DoctorCRE subject. There
--      is no column, function or view anywhere below that turns a Salesforce
--      phase into a lifecycle state, and ops.j102_read exposes none.
--   2. NO LEGACY REWRITE. The compatibility view PROJECTS the new records into
--      the old shape for reading. It is not updatable, nothing writes back
--      through it, and no legacy table is renamed, dropped or altered.
--   3. NO CALLER RETIREMENT. ops.j102_migration_readiness() reports that the old
--      interface may not be retired, for every input, because no verified caller
--      census exists. It is a reader, and it says no.
--   4. NO TOUR. Journey 3 owns active Tour behaviour. No relation, function or
--      view here creates, activates or reads one.
--   5. NO F01 SCHEMA PATCH. Evidence has to name the subject it advances, and an
--      F01 document carries no lifecycle binding. Rather than adding a column to
--      a schema this slice does not own, the association lives in the J102-scoped
--      ops.j102_evidence_subject_link below, written by its own registered
--      partner-only producer. Nothing here alters, extends or writes to any
--      ops.f01_* relation; F01 remains the sole authority for document identity
--      and document state, and an association asserts neither.

-- ---------------------------------------------------------------------------
-- Prerequisites. This file REFUSES TO APPLY rather than creating a second copy
-- of anything domain.sql already owns.
-- ---------------------------------------------------------------------------
do $$
declare
  v_missing text[] := array[]::text[];
  v_name text;
begin
  foreach v_name in array array[
    'ops.f01_tenant()', 'ops.f01_canonical_json(jsonb)', 'ops.f01_digest_jsonb(jsonb)',
    'ops.f01_is_digest_ref(text)', 'ops.f01_instant(text)', 'ops.f01_instant_text(timestamptz)',
    'ops.f01_now_text()', 'ops.f01_context_actor_slug()', 'ops.f01_principal()',
    'ops.f01_read(text,jsonb)', 'ops.f01_stored_artifact(text)'
  ] loop
    if to_regprocedure(v_name) is null then
      v_missing := v_missing || v_name;
    end if;
  end loop;
  if array_length(v_missing, 1) > 0 then
    raise exception 'j102_prerequisites_missing: domain.sql must be applied first; absent: %',
      array_to_string(v_missing, ', ');
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- Guards.
--
-- WHY A CALL-STACK CHECK RATHER THAN A FLAG, restated because it is the same
-- reasoning F01 records and the same trap: a transaction-local flag is something
-- a caller with DML rights could set for itself, so it would prove nothing.
-- PG_CONTEXT names the actual PL/pgSQL frames beneath the trigger, so the guard
-- can require that a write genuinely arrived through a registered ops.j102_*
-- writer.
--
-- THE SCHEMA HALF, and how far it can honestly be taken. PG_CONTEXT renders each
-- frame through format_procedure, which OMITS the schema when the function is
-- visible on the current search_path -- and this guard's own search_path puts
-- `ops` ahead of `public`, so a genuine ops.j102_apply_transition frame prints
-- unqualified. Requiring a literal `ops.` prefix would therefore refuse every
-- legitimate write, which is why the prefix stays optional. What makes the
-- optional prefix safe is that an unqualified `j102_apply_transition(` frame can
-- only be the function that search_path resolves that name to, and the
-- application-time check below asserts that every writer name resolves to the
-- ops one under exactly this search_path. A same-named function in `public` is
-- shadowed by ops and prints QUALIFIED, so `public.j102_apply_transition(` does
-- not satisfy the pattern.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_guard_direct_dml()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_context text;
begin
  get diagnostics v_context = pg_context;
  if regexp_replace(v_context, 'PL/pgSQL function (ops\.)?j102_guard_direct_dml\(\)[^\n]*', '', 'g')
       !~ 'PL/pgSQL function (ops\.)?j102_(apply_transition|record_first_party_fact|record_evidence_subject_link|record_salesforce_reference|record_correction|record_reconciliation_item|claim_idempotency|settle_idempotency)\('
  then
    raise exception 'j102_direct_dml_refused: %.% is written only through the registered ops.j102_* writers',
      tg_table_schema, tg_table_name using errcode = '42501';
  end if;
  return new;
end;
$$;

create or replace function ops.j102_guard_append_only()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  raise exception 'j102_append_only_violation: %.% admits INSERT only; % refused',
    tg_table_schema, tg_table_name, tg_op using errcode = '42501';
  return null;
end;
$$;

create or replace function ops.j102_guard_no_truncate()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  raise exception 'j102_truncate_refused: %.% is append-only history',
    tg_table_schema, tg_table_name using errcode = '42501';
  return null;
end;
$$;

-- ---------------------------------------------------------------------------
-- The relations.
--
-- EVERY RECORD IS AN ENVELOPE, exactly as F01 stores one. `envelope` is the
-- canonical preimage the record hashes to, `envelope -> 'record'` is the domain
-- record the kernel produced, and both digests are CHECK-bound to a
-- recomputation inside PostgreSQL -- so a readback that agrees with the stored
-- claim has genuinely verified it, and a tampered row cannot read back healthy.
--
-- CURRENT STATE IS ONE TABLE, NOT FIVE. Q079 keeps the four entities distinct in
-- MEANING; it does not require four physical homes, and one keyed relation makes
-- the compare-and-swap, the lock ordering and the append-only history uniform
-- across every subject kind rather than five near-identical copies that can
-- drift. The kind is a closed enum and every kind-specific invariant that can be
-- expressed structurally is expressed as a constraint or a partial index below.
-- ---------------------------------------------------------------------------

create table if not exists ops.j102_subject_current (
  tenant            text not null,
  subject_kind      text not null check (subject_kind in
                      ('relationship', 'engagement', 'assignment', 'property_negotiation', 'deal')),
  subject_id        text not null check (subject_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  envelope          jsonb not null,
  envelope_digest   text not null,
  state_digest      text not null,
  -- The one structural parent, extracted so the partial indexes below can reach
  -- it, and CHECK-bound to the envelope so it can never drift from the hashed
  -- bytes. Relationship rows have none.
  parent_id         text,
  -- Extracted for the pending-deal index and for the compatibility projection,
  -- likewise CHECK-bound to the envelope.
  deal_state        text,
  updated_by        text not null,
  updated_at        timestamptz not null,
  primary key (tenant, subject_kind, subject_id),
  constraint j102_subject_tenant check (tenant = ops.f01_tenant()),
  constraint j102_subject_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_subject_state_digest
    check (state_digest = ops.f01_digest_jsonb(envelope -> 'record' -> 'state')),
  constraint j102_subject_kind_matches_envelope
    check (subject_kind = envelope -> 'record' ->> 'subject_kind'),
  constraint j102_subject_id_matches_envelope
    check (subject_id = envelope -> 'record' ->> 'subject_id'),
  constraint j102_subject_parent_matches_envelope
    check (parent_id is not distinct from coalesce(
      envelope -> 'record' -> 'state' ->> 'relationship_id',
      envelope -> 'record' -> 'state' ->> 'engagement_id',
      envelope -> 'record' -> 'state' ->> 'assignment_id')),
  constraint j102_subject_deal_state_matches_envelope
    check (deal_state is not distinct from envelope -> 'record' -> 'state' ->> 'deal_state'),
  constraint j102_subject_updated_by_matches_envelope
    check (updated_by = envelope -> 'record' ->> 'updated_by')
);

comment on table ops.j102_subject_current is
  'Authoritative current state for one DoctorCRE lifecycle subject. state_digest is recomputed from the hashed bytes on every read and is the compare-and-swap operand for every transition. Updated only by ops.j102_apply_transition.';

-- Q078 and Q095, structurally. An assignment holds at most ONE pending Deal, so
-- a second commitment racing the first cannot both succeed: the index refuses
-- the loser rather than leaving two pending deals for a human to discover.
create unique index if not exists j102_one_pending_deal_per_assignment
  on ops.j102_subject_current (tenant, parent_id)
  where subject_kind = 'deal' and deal_state = 'pending';

comment on index ops.j102_one_pending_deal_per_assignment is
  'Q078/Q095 structurally: one pending Deal per Assignment. Cancelled and closed deals are excluded, so an assignment may hold many historical deals and only one live one.';

create table if not exists ops.j102_subject_event (
  tenant            text not null,
  event_seq         bigserial primary key,
  subject_kind      text not null,
  subject_id        text not null,
  event_kind        text not null,
  transition_id     text not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  event_digest      text not null,
  recorded_by       text not null,
  recorded_at       timestamptz not null,
  idempotency_key   text not null,
  constraint j102_event_tenant check (tenant = ops.f01_tenant()),
  constraint j102_event_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_event_digest_bound
    check (event_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_event_kind_matches_envelope
    check (event_kind = envelope -> 'record' -> 'event' ->> 'event_kind'),
  constraint j102_event_subject_matches_envelope
    check (subject_kind = envelope -> 'record' -> 'event' ->> 'subject_kind'
       and subject_id = envelope -> 'record' -> 'event' ->> 'subject_id'),
  constraint j102_event_transition_matches_envelope
    check (transition_id = envelope -> 'record' ->> 'transition_id')
);

comment on table ops.j102_subject_event is
  'Append-only lifecycle history. Every event names the transition that produced it and the exact evidence references it rested on, so the history says what a change was judged against and not only what changed. Q096: a cancelled deal keeps every event it ever had.';

create index if not exists j102_subject_event_by_subject
  on ops.j102_subject_event (tenant, subject_kind, subject_id, event_seq);

-- The first-party business records evidence is read from: a mandate, a winning
-- property commitment, a diligence outcome, a closing settlement and its actual
-- date, an invoice, a payment, a completion, a deal failure and its reason, a
-- correction.
--
-- WHY THIS IS NOT A CALLER BOOLEAN WITH EXTRA STEPS. A row here is written by an
-- authenticated actor through a registered writer, carries the server's own
-- instant, is append-only, and is addressable afterwards. A transition names one
-- by id and the record layer loads it; the fact never travels inside the
-- transition request. That is the difference between "the broker recorded that
-- the closing happened on the 14th" and "the caller said closed: true".
-- THE TYPED SUBJECT BINDING IS PART OF THE ROW, and it is the correction this
-- table most needed. A record used to say WHAT happened and WHO recorded it and
-- never WHICH deal, assignment or client it happened to, so one closing
-- settlement with one date could close any number of unrelated deals and a
-- commitment recorded for assignment A could commit assignment B. The columns
-- below are CHECK-bound to the hashed envelope, so the binding cannot be edited
-- beside the bytes it is supposed to describe.
create table if not exists ops.j102_first_party_record (
  tenant            text not null,
  record_kind       text not null check (record_kind in
                      ('assignment_mandate', 'winning_property_commitment', 'diligence_outcome',
                       'closing_settlement', 'deal_failure', 'invoice', 'payment', 'completion',
                       'lifecycle_correction')),
  record_id         text not null check (record_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  envelope          jsonb not null,
  envelope_digest   text not null,
  record_digest     text not null,
  bound_subject_kind text not null check (bound_subject_kind in
                      ('relationship', 'engagement', 'assignment', 'property_negotiation', 'deal')),
  bound_subject_id  text not null check (bound_subject_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  closing_date      timestamptz,
  recorded_by       text not null,
  -- H5. The author's own authorization class, stamped from the derived principal
  -- at write time. It is what makes "a partner stated this" a checkable property
  -- of the row rather than a property of whoever presents it later.
  recorded_by_class text not null check (recorded_by_class in
                      ('verified_partner', 'sponsored_agent')),
  recorded_at       timestamptz not null,
  idempotency_key   text not null,
  primary key (tenant, record_kind, record_id),
  constraint j102_fact_tenant check (tenant = ops.f01_tenant()),
  constraint j102_fact_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_fact_record_digest
    check (record_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_fact_kind_matches_envelope
    check (record_kind = envelope -> 'record' ->> 'record_kind'),
  constraint j102_fact_id_matches_envelope
    check (record_id = envelope -> 'record' ->> 'record_id'),
  constraint j102_fact_binding_matches_envelope
    check (bound_subject_kind = envelope -> 'record' ->> 'subject_kind'
       and bound_subject_id = envelope -> 'record' ->> 'subject_id'),
  constraint j102_fact_recorded_by_matches_envelope
    check (recorded_by = envelope -> 'record' ->> 'recorded_by'),
  constraint j102_fact_recorded_by_class_matches_envelope
    check (recorded_by_class = envelope -> 'record' ->> 'recorded_by_authorization_class'),
  -- H5 structurally: the four facts only a partner may state cannot be stored
  -- with any other author class, so an agent-authored closing date, winning
  -- property commitment, failure reason or correction proof does not exist to be
  -- laundered through a partner-performed transition later.
  constraint j102_fact_partner_authored_kinds
    check (record_kind not in ('winning_property_commitment', 'closing_settlement',
                               'deal_failure', 'lifecycle_correction')
        or recorded_by_class = 'verified_partner'),
  -- M1 structurally. A reason or detail that is not a JSON string stores
  -- perfectly well and then makes the record UNREADABLE as evidence, which turns
  -- a policy question into a thrown contract violation at read time.
  constraint j102_fact_reason_is_text
    check (jsonb_typeof(envelope -> 'record' -> 'reason') in ('string', 'null')),
  constraint j102_fact_detail_is_text
    check (jsonb_typeof(envelope -> 'record' -> 'detail') in ('string', 'null')),
  constraint j102_fact_supporting_document_is_ident
    check (envelope -> 'record' ->> 'supporting_document_id' is null
        or envelope -> 'record' ->> 'supporting_document_id'
             ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  -- Q094 structurally: a closing_settlement record without an actual closing
  -- date cannot exist, so no closing transition can ever find one to read.
  constraint j102_fact_closing_requires_date
    check (record_kind <> 'closing_settlement' or closing_date is not null),
  constraint j102_fact_closing_date_matches_envelope
    check (closing_date is not distinct from
      case when envelope -> 'record' ->> 'closing_date' is null then null
           else ops.f01_instant(envelope -> 'record' ->> 'closing_date') end),
  -- A reason is mandatory where the kernel's evidence contract requires one.
  -- Stated twice on purpose: once where the decision is made, once where the row
  -- lives, so a row that could never satisfy a transition cannot be stored.
  constraint j102_fact_reason_required
    check (record_kind not in ('deal_failure', 'lifecycle_correction')
        or btrim(coalesce(envelope -> 'record' ->> 'reason', '')) <> '')
);

comment on table ops.j102_first_party_record is
  'Append-only authenticated first-party business records, each bound to the exact subject it is about and carrying its author''s authorization class. A lifecycle transition names one by id and the record layer loads it; the fact never travels inside a transition request, which is what keeps a caller from asserting the outcome it is asking for.';

create index if not exists j102_fact_by_bound_subject
  on ops.j102_first_party_record (tenant, bound_subject_kind, bound_subject_id, record_kind);

-- ---------------------------------------------------------------------------
-- THE EVIDENCE -> SUBJECT ASSOCIATION, and why it lives here rather than in F01.
--
-- A first-party record can carry its own binding because this rail writes it. An
-- F01 DOCUMENT cannot: F01 owns document identity, versions, signature, validity
-- and version state, and it holds no lifecycle concept at all -- there is no
-- column on an F01 document that says which DoctorCRE deal it belongs to, and
-- adding one would be this slice editing a schema it does not own to make its
-- own problem easier. The same is true of a corporate artifact.
--
-- So the association is held HERE, scoped to J102, and it is a RECORD rather
-- than a claim on a request: one partner-authored, append-only, digest-bound row
-- saying that one exact document version, or one exact artifact, belongs to one
-- lifecycle subject. A transition reads it; nobody asserts it in passing.
--
-- IT IS PINNED, NOT NAMED. The association binds document_id + version_no +
-- content_digest, so a document that gains a version is not the document that
-- was associated and needs its own association. That is deliberately the same
-- rule the evidence pin itself follows, for the same reason: "the document still
-- exists" is not the question.
--
-- WHAT AN ASSOCIATION IS NOT. It is not a document, it does not create one, and
-- it asserts nothing whatever about signature, validity or version state -- every
-- one of those is read from F01 at judgement time and none of them is copied
-- here. A partner saying "this lease is that deal's lease" is not a partner
-- saying the lease is signed.
-- ---------------------------------------------------------------------------
create table if not exists ops.j102_evidence_subject_link (
  tenant            text not null,
  link_seq          bigserial primary key,
  evidence_source   text not null check (evidence_source in
                      ('f01_document', 'f01_corporate_artifact')),
  -- The document id, or the artifact digest. The pin's identity half.
  evidence_ref      text not null check (btrim(evidence_ref) <> ''),
  -- The document version. An artifact has none, and 0 says so rather than a null
  -- the unique index would have to work around.
  version_no        integer not null,
  -- The document version's content digest, or -- for an artifact, whose pin IS
  -- its digest -- the artifact digest again.
  content_digest    text not null,
  subject_kind      text not null check (subject_kind in
                      ('relationship', 'engagement', 'assignment', 'property_negotiation', 'deal')),
  subject_id        text not null check (subject_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$'),
  envelope          jsonb not null,
  envelope_digest   text not null,
  link_digest       text not null,
  associated_by     text not null,
  associated_by_class text not null check (associated_by_class = 'verified_partner'),
  associated_at     timestamptz not null,
  idempotency_key   text not null,
  constraint j102_link_tenant check (tenant = ops.f01_tenant()),
  constraint j102_link_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_link_digest_bound
    check (link_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_link_content_digest_shape check (ops.f01_is_digest_ref(content_digest)),
  constraint j102_link_version_matches_source
    check ((evidence_source = 'f01_document') = (version_no > 0)),
  constraint j102_link_matches_envelope
    check (evidence_source = envelope -> 'record' ->> 'evidence_source'
       and evidence_ref = envelope -> 'record' ->> 'evidence_ref'
       and content_digest = envelope -> 'record' ->> 'content_digest'
       and subject_kind = envelope -> 'record' ->> 'subject_kind'
       and subject_id = envelope -> 'record' ->> 'subject_id'
       and associated_by = envelope -> 'record' ->> 'associated_by'),
  -- An association says nothing about the document's own states, and a record
  -- claiming otherwise is refused rather than stored and ignored.
  constraint j102_link_asserts_no_document_state
    check ((envelope -> 'record' ->> 'asserts_document_state') = 'false'
       and (envelope -> 'record' ->> 'creates_document') = 'false')
);

comment on table ops.j102_evidence_subject_link is
  'The J102-scoped association between one EXACT evidence pin (an F01 document id + version + content digest, or a corporate artifact digest) and one lifecycle subject. Written only by ops.j102_record_evidence_subject_link, only by a verified partner, append-only. It creates no document and asserts no document state; F01 remains the sole authority for both.';

-- One association per (pin, subject). A repeat is the same fact, not a second one.
create unique index if not exists j102_evidence_subject_link_uq
  on ops.j102_evidence_subject_link
     (tenant, evidence_source, evidence_ref, version_no, content_digest, subject_kind, subject_id);

create table if not exists ops.j102_salesforce_reference (
  tenant            text not null,
  opportunity_id    text not null,
  reference_seq     bigserial primary key,
  -- SALESFORCE'S OWN LABELS, preserved verbatim and never interpreted. There is
  -- deliberately no mapping column, no derived lifecycle state, and no
  -- constraint relating either of these to any DoctorCRE vocabulary.
  opportunity_name  text not null check (btrim(opportunity_name) <> ''),
  opportunity_phase text not null check (btrim(opportunity_phase) <> ''),
  linked_subject_kind text check (linked_subject_kind in
                      ('relationship', 'engagement', 'assignment', 'deal')),
  linked_subject_id text,
  observed_at       timestamptz not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  reference_digest  text not null,
  recorded_by       text not null,
  recorded_at       timestamptz not null,
  idempotency_key   text not null,
  constraint j102_reference_tenant check (tenant = ops.f01_tenant()),
  constraint j102_reference_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_reference_digest_bound
    check (reference_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_reference_link_complete
    check ((linked_subject_kind is null) = (linked_subject_id is null)),
  constraint j102_reference_matches_envelope
    check (opportunity_id = envelope -> 'record' ->> 'opportunity_id'
       and opportunity_name = envelope -> 'record' ->> 'opportunity_name'
       and opportunity_phase = envelope -> 'record' ->> 'opportunity_phase')
);

comment on table ops.j102_salesforce_reference is
  'Q083: external corporate references, progressively linked. The Salesforce name and phase are stored as SALESFORCE facts about its own record. No column here is a DoctorCRE lifecycle state and nothing in this file maps one onto the other.';

create index if not exists j102_reference_by_opportunity
  on ops.j102_salesforce_reference (tenant, opportunity_id, reference_seq);

create table if not exists ops.j102_correction_receipt (
  tenant            text not null,
  receipt_seq       bigserial primary key,
  subject_kind      text not null,
  subject_id        text not null,
  correction_record_id text not null,
  reason            text not null check (btrim(reason) <> ''),
  prior_state_digest text not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  receipt_digest    text not null,
  corrected_by      text not null,
  corrected_at      timestamptz not null,
  idempotency_key   text not null,
  constraint j102_receipt_tenant check (tenant = ops.f01_tenant()),
  constraint j102_receipt_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_receipt_digest_bound
    check (receipt_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  constraint j102_receipt_prior_state_digest
    check (ops.f01_is_digest_ref(prior_state_digest)),
  constraint j102_receipt_matches_envelope
    check (subject_kind = envelope -> 'record' ->> 'subject_kind'
       and subject_id = envelope -> 'record' ->> 'subject_id'
       and corrected_by = envelope -> 'record' ->> 'corrected_by')
);

comment on table ops.j102_correction_receipt is
  'Q082/Q072: every manual correction leaves an append-only receipt naming the human who made it, the reason, the durable correction record it rests on, and the exact prior state digest. Nothing here overwrites history.';

-- Q103's visible reconciliation. A conflict that cannot be auto-merged lands
-- here with BOTH versions preserved, and it is resolved by a person.
create table if not exists ops.j102_reconciliation_item (
  tenant            text not null,
  item_seq          bigserial primary key,
  subject_kind      text not null,
  subject_id        text not null,
  conflict_kind     text not null,
  base_version_digest text not null,
  current_version_digest text not null,
  envelope          jsonb not null,
  envelope_digest   text not null,
  item_digest       text not null,
  proposed_by       text not null,
  recorded_at       timestamptz not null,
  constraint j102_item_tenant check (tenant = ops.f01_tenant()),
  constraint j102_item_envelope_digest
    check (envelope_digest = ops.f01_digest_jsonb(envelope)),
  constraint j102_item_digest_bound
    check (item_digest = ops.f01_digest_jsonb(envelope -> 'record')),
  -- Both sides, always. An item recording only the losing edit would make the
  -- resolution unreviewable, which is the same defect as resolving it silently.
  constraint j102_item_preserves_both_sides
    check (jsonb_typeof(envelope -> 'record' -> 'incoming_edits') = 'array'
       and jsonb_typeof(envelope -> 'record' -> 'concurrent_edits') = 'array'),
  constraint j102_item_not_machine_resolved
    check ((envelope -> 'record' ->> 'resolved_by_machine') = 'false')
);

comment on table ops.j102_reconciliation_item is
  'Q103: a lifecycle, financial, recipient or document conflict, visible and unresolved, with both versions preserved. Nothing in this file resolves one; a person does.';

create table if not exists ops.j102_idempotency (
  tenant            text not null,
  operation         text not null,
  idempotency_key   text not null,
  request_digest    text not null,
  actor_slug        text not null,
  result            jsonb,
  result_digest     text,
  claimed_at        timestamptz not null,
  settled_at        timestamptz,
  primary key (tenant, operation, idempotency_key),
  constraint j102_idempotency_tenant check (tenant = ops.f01_tenant()),
  constraint j102_idempotency_request_digest check (ops.f01_is_digest_ref(request_digest)),
  constraint j102_idempotency_result_digest
    check (result_digest is null or result_digest = ops.f01_digest_jsonb(result))
);

-- An idempotency key is bound to ONE operation and ONE payload, so a key can
-- never substitute one write for another.
create unique index if not exists j102_idempotency_key_operation_uq
  on ops.j102_idempotency (tenant, idempotency_key, operation);

-- ---------------------------------------------------------------------------
-- Trigger wiring. Every relation refuses direct DML; the history relations
-- additionally refuse UPDATE, DELETE and TRUNCATE.
-- ---------------------------------------------------------------------------
do $$
declare v_table text; v_append_only boolean;
begin
  for v_table, v_append_only in
    select * from (values
      ('j102_subject_current', false),
      ('j102_subject_event', true),
      ('j102_first_party_record', true),
      ('j102_evidence_subject_link', true),
      ('j102_salesforce_reference', true),
      ('j102_correction_receipt', true),
      ('j102_reconciliation_item', true),
      ('j102_idempotency', false)
    ) as t(name, append_only)
  loop
    execute format('drop trigger if exists %I on ops.%I', v_table || '_dml_guard', v_table);
    execute format(
      'create trigger %I before insert or update on ops.%I for each row execute function ops.j102_guard_direct_dml()',
      v_table || '_dml_guard', v_table);
    execute format('drop trigger if exists %I on ops.%I', v_table || '_no_truncate', v_table);
    execute format(
      'create trigger %I before truncate on ops.%I execute function ops.j102_guard_no_truncate()',
      v_table || '_no_truncate', v_table);
    if v_append_only then
      execute format('drop trigger if exists %I on ops.%I', v_table || '_append_only', v_table);
      execute format(
        'create trigger %I before update or delete on ops.%I for each row execute function ops.j102_guard_append_only()',
        v_table || '_append_only', v_table);
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Envelope verification, and the readers.
-- ---------------------------------------------------------------------------

-- Recompute, never trust. A row whose stored digests no longer describe its own
-- bytes RAISES rather than reading back as healthy: a lifecycle record that
-- cannot prove its own integrity is not a record to make a decision from.
create or replace function ops.j102_verify_envelope(
  p_envelope jsonb, p_envelope_digest text, p_record_digest text, p_record_kind text)
returns jsonb language plpgsql immutable
set search_path = pg_catalog, ops, public
as $$
begin
  if p_envelope is null then
    raise exception 'j102_envelope_missing' using errcode = 'integrity_constraint_violation';
  end if;
  if (p_envelope ->> 'record_kind') is distinct from p_record_kind then
    raise exception 'j102_record_kind_mismatch: stored % expected %',
      p_envelope ->> 'record_kind', p_record_kind using errcode = 'integrity_constraint_violation';
  end if;
  if ops.f01_digest_jsonb(p_envelope) is distinct from p_envelope_digest then
    raise exception 'j102_envelope_digest_mismatch' using errcode = 'integrity_constraint_violation';
  end if;
  if ops.f01_digest_jsonb(p_envelope -> 'record') is distinct from p_record_digest then
    raise exception 'j102_record_digest_mismatch' using errcode = 'integrity_constraint_violation';
  end if;
  return jsonb_build_object(
    'record', p_envelope -> 'record',
    'record_digest', p_record_digest,
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_verify_envelope(jsonb,text,text,text) is
  'Recompute both digests of one stored J102 envelope from its committed bytes and raise on any mismatch. A readback that returns is a readback that verified.';

create or replace function ops.j102_subject(p_subject_kind text, p_subject_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_subject_current%rowtype; v_verified jsonb;
begin
  select * into v_row from ops.j102_subject_current
   where tenant = ops.f01_tenant() and subject_kind = p_subject_kind and subject_id = p_subject_id;
  if not found then return null; end if;
  v_verified := ops.j102_verify_envelope(v_row.envelope, v_row.envelope_digest,
    ops.f01_digest_jsonb(v_row.envelope -> 'record'), 'stored_lifecycle_subject');
  -- The state digest is RECOMPUTED here too, not read off the column, so the
  -- compare-and-swap operand a caller receives is derived from the bytes rather
  -- than from a value that could have been written beside them.
  return jsonb_build_object(
    'subject_kind', v_row.subject_kind,
    'subject_id', v_row.subject_id,
    'state', v_verified -> 'record' -> 'state',
    'state_digest', ops.f01_digest_jsonb(v_verified -> 'record' -> 'state'),
    'established_by_transition', v_verified -> 'record' ->> 'established_by_transition',
    'updated_by', v_row.updated_by,
    'updated_at', ops.f01_instant_text(v_row.updated_at),
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_subject(text,text) is
  'One lifecycle subject''s verified current state and its recomputed compare-and-swap digest, or null when no such subject exists.';

create or replace function ops.j102_first_party_record(p_record_kind text, p_record_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_first_party_record%rowtype; v_verified jsonb;
begin
  select * into v_row from ops.j102_first_party_record
   where tenant = ops.f01_tenant() and record_kind = p_record_kind and record_id = p_record_id;
  if not found then return null; end if;
  v_verified := ops.j102_verify_envelope(v_row.envelope, v_row.envelope_digest,
    v_row.record_digest, 'stored_first_party_record');
  return jsonb_build_object(
    'record', v_verified -> 'record',
    'record_digest', v_row.record_digest,
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_first_party_record(text,text) is
  'One verified first-party business record, or null. This is the ONLY door a lifecycle transition reads a business fact through, and every record it returns names the subject it is about.';

-- THE ASSOCIATION READER ASKS A YES/NO QUESTION, and the question is the point.
--
-- It takes the subject as a PARAMETER rather than returning whichever subject
-- the association happens to name, so the caller asks "is this exact document
-- version bound to THIS deal" and gets null when it is not. A reader that
-- returned the association's own subject would read identically on every happy
-- path and differ on exactly the case BLOCK-2 describes -- a perfectly authentic
-- lease, bound to somebody else's deal, presented against this one.
create or replace function ops.j102_evidence_subject_link(
  p_evidence_source text, p_evidence_ref text, p_version_no integer,
  p_content_digest text, p_subject_kind text, p_subject_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_evidence_subject_link%rowtype; v_verified jsonb;
begin
  select * into v_row from ops.j102_evidence_subject_link
   where tenant = ops.f01_tenant()
     and evidence_source = p_evidence_source
     and evidence_ref = p_evidence_ref
     and version_no = coalesce(p_version_no, 0)
     and content_digest = p_content_digest
     and subject_kind = p_subject_kind
     and subject_id = p_subject_id;
  if not found then return null; end if;
  v_verified := ops.j102_verify_envelope(v_row.envelope, v_row.envelope_digest,
    v_row.link_digest, 'stored_evidence_subject_link');
  return jsonb_build_object(
    'record', v_verified -> 'record',
    'link_digest', v_row.link_digest,
    'integrity', 'recomputed_from_committed_row');
end;
$$;

comment on function ops.j102_evidence_subject_link(text,text,integer,text,text,text) is
  'Whether one EXACT evidence pin is associated with one named lifecycle subject, verified from committed bytes, or null. The subject is asked about rather than reported, so an authentic document bound to a different subject answers null.';

-- ---------------------------------------------------------------------------
-- THE PRIVATE FAIL-CLOSED APPROVAL READER.
--
-- This is the whole of the typed-approval binding, and it is a stub on purpose.
--
-- WHAT IS MISSING, PRECISELY. Any AUTHENTICATED RECORD IN THIS DATABASE of an
-- approval that a named class of agreement is a representation equivalent
-- (Q077), or that a named assignment may hold a second selected property or
-- lease-draft target (Q095). No table holds one because no workflow writes one,
-- so there is nothing here for a transition to bind to.
--
-- WHAT THIS FUNCTION REFUSES TO DO INSTEAD, because each would manufacture the
-- authority the absent record is supposed to carry: accept an approval from its
-- caller, read one out of configuration, derive one from a synthetic fixture,
-- treat an approval REFERENCE stored on an assignment row as the approval
-- itself, or treat "the reader is not built" as "the approval is not required".
--
-- IT IS GRANTED TO NOBODY. Exposing it would turn "this record layer cannot
-- authenticate a lifecycle approval" into a callable claim about approvals in
-- general, and a callable stub is the first step toward a configurable one. The
-- writers below reach it as the function owner, which is the only access it
-- needs. In practice no shipped path reaches it: the store's own
-- V5_J102_ABSENT_EVIDENCE_READERS registry turns those two evidence kinds into a
-- recordable policy refusal before it ever issues a read, so a caller learns
-- which fact is missing instead of receiving a database error. This function is
-- the same refusal one layer down, for any future writer that reaches for an
-- approval without going through that registry.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_typed_approval(p_approval_kind text, p_approval_ref text)
returns jsonb language plpgsql stable
set search_path = pg_catalog, ops, public
as $$
begin
  raise exception 'j102_typed_approval_unavailable: this record layer holds no authenticated % approval (ref %); no producer writes one, and no approval is inferred, defaulted or accepted from a caller',
    coalesce(p_approval_kind, 'lifecycle'), coalesce(p_approval_ref, 'unnamed')
    using errcode = '42501';
end;
$$;

comment on function ops.j102_typed_approval(text,text) is
  'PRIVATE fail-closed reader for Q077 representation-equivalence and Q095 multi-target-exception approvals. It always raises, because no producer writes such an approval and this database can authenticate none. Granted to no role. Landing a producer for one approval kind does not open the other.';

-- ---------------------------------------------------------------------------
-- Idempotency.
--
-- REPLAY BEFORE STATE, and the ordering is load-bearing rather than incidental:
-- a settled key must return its stored result even though the world has moved
-- since, and a replay that ran after the compare-and-swap would refuse a request
-- that had already succeeded. No writer below reads a subject, takes a lock or
-- evaluates a CAS above its claim.
-- ---------------------------------------------------------------------------

create or replace function ops.j102_replay_outcome(
  p_operation text, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.j102_idempotency%rowtype; v_actor text := ops.f01_context_actor_slug();
begin
  -- The closed write-operation vocabulary. A new writer MUST be added here or it
  -- cannot claim, replay or settle a key at all; nothing here is a wildcard.
  if p_operation not in (
    'record-lifecycle-fact', 'record-evidence-subject-link',
    'record-representation-agreement', 'open-cre-assignment',
    'record-loi-submission', 'record-loi-acceptance', 'commit-winning-property',
    'record-deal-execution', 'record-diligence-outcome', 'record-deal-closing',
    'cancel-pending-deal', 'record-deal-axis', 'link-salesforce-reference',
    'record-lifecycle-correction') then
    raise exception 'j102_unknown_write_operation: %', p_operation using errcode = '22023';
  end if;
  if p_idempotency_key is null or length(p_idempotency_key) not between 1 and 200 then
    raise exception 'j102_idempotency_key_required' using errcode = '22023';
  end if;
  if p_request_digest is null or not ops.f01_is_digest_ref(p_request_digest) then
    raise exception 'j102_idempotency_request_digest_malformed' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'j102:request:' || ops.f01_tenant() || ':' || p_idempotency_key, 0));
  select * into v_row from ops.j102_idempotency
   where tenant = ops.f01_tenant() and operation = p_operation
     and idempotency_key = p_idempotency_key;
  if not found then return null; end if;
  if v_row.request_digest is distinct from p_request_digest then
    raise exception 'j102_idempotency_payload_mismatch: key % already binds a different payload',
      p_idempotency_key using errcode = '23505';
  end if;
  if v_row.actor_slug is distinct from v_actor then
    raise exception 'j102_idempotency_actor_mismatch: key % belongs to another actor',
      p_idempotency_key using errcode = '42501';
  end if;
  return v_row.result;
end;
$$;

create or replace function ops.j102_claim_idempotency(
  p_operation text, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  v_row ops.j102_idempotency%rowtype;
  v_actor text := ops.f01_context_actor_slug();
  v_replay jsonb;
begin
  v_replay := ops.j102_replay_outcome(p_operation, p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  -- ON CONFLICT DO UPDATE takes the row lock, so a concurrent claim of the same
  -- key BLOCKS here and then reads the committed outcome, rather than racing
  -- past it and writing a second time.
  insert into ops.j102_idempotency as i
    (tenant, operation, idempotency_key, request_digest, actor_slug, claimed_at)
  values (ops.f01_tenant(), p_operation, p_idempotency_key, p_request_digest, v_actor, now())
  on conflict (tenant, operation, idempotency_key)
  do update set claimed_at = i.claimed_at
  returning * into v_row;
  if v_row.request_digest is distinct from p_request_digest then
    raise exception 'j102_idempotency_payload_mismatch: key % already binds a different payload',
      p_idempotency_key using errcode = '23505';
  end if;
  if v_row.actor_slug is distinct from v_actor then
    raise exception 'j102_idempotency_actor_mismatch: key % belongs to another actor',
      p_idempotency_key using errcode = '42501';
  end if;
  -- A key already used for a DIFFERENT operation is a substitution attempt.
  if exists (select 1 from ops.j102_idempotency
              where tenant = ops.f01_tenant() and idempotency_key = p_idempotency_key
                and operation <> p_operation) then
    raise exception 'j102_idempotency_operation_mismatch: key % already binds another operation',
      p_idempotency_key using errcode = '23505';
  end if;
  return v_row.result;
end;
$$;

create or replace function ops.j102_settle_idempotency(
  p_operation text, p_idempotency_key text, p_result jsonb)
returns jsonb language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  update ops.j102_idempotency
     set result = p_result, result_digest = ops.f01_digest_jsonb(p_result), settled_at = now()
   where tenant = ops.f01_tenant() and operation = p_operation
     and idempotency_key = p_idempotency_key;
  return p_result;
end;
$$;

-- ---------------------------------------------------------------------------
-- THE EVIDENCE RECHECK.
--
-- This is the half a kernel cannot enforce, and it is the reason this file
-- exists rather than a set of plain INSERTs.
--
-- The store read the evidence, the kernel judged it, and time passed. A document
-- can gain a version, an artifact can be superseded, a first-party record can be
-- rewritten -- all between the read and the write. Applying a decision taken
-- against evidence that has since moved is the same defect as last-writer-wins,
-- one layer down, so the EXACT pin is re-read HERE, inside the transaction that
-- already holds the subject locks, and any movement refuses the whole transition.
--
-- IT COMPARES PINS, NOT SHAPES. A document reference names the version and the
-- content digest it meant; a first-party record names the record digest it meant.
-- "The document still exists" is not the check. "The document is still the one
-- the decision was taken against" is.
--
-- AND IT RE-ASSERTS THE SUBJECT BINDING, which is the half that used to be
-- missing entirely. The recheck compared the record's digest and never asked
-- which deal the record was about, so an authentic, unmoved, correctly pinned
-- settlement could close a deal it had nothing to do with. Every manifest item
-- now carries the subject the transition is moving, and the binding is re-read
-- under the same lock: an association withdrawn, or a record re-bound, between
-- the decision and the write refuses the whole transition exactly as a moved
-- version does.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_recheck_evidence(p_recheck jsonb)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_item jsonb;
  v_body jsonb;
  v_record jsonb;
  v_link jsonb;
  v_checked jsonb := '[]'::jsonb;
begin
  if jsonb_typeof(p_recheck) is distinct from 'array' or jsonb_array_length(p_recheck) < 1 then
    raise exception 'j102_evidence_recheck_required: a transition never applies without re-reading its evidence'
      using errcode = '22023';
  end if;
  for v_item in select * from jsonb_array_elements(p_recheck) loop
    -- EVERY item must name the subject it binds to. A manifest entry without one
    -- is a decision nobody can re-check, and it refuses rather than being
    -- re-checked on the half of itself that is present.
    if v_item -> 'binding' is null
       or (v_item -> 'binding' ->> 'subject_kind') is null
       or (v_item -> 'binding' ->> 'subject_id') is null then
      raise exception 'j102_evidence_binding_required: % evidence names no subject binding to re-assert',
        coalesce(v_item ->> 'evidence_kind', 'unnamed') using errcode = '22023';
    end if;
    if (v_item ->> 'source') = 'f01_document' then
      v_body := ops.f01_read('document',
        jsonb_build_object('document_id', v_item -> 'selector' ->> 'document_id')) -> 'body';
      if v_body is null or v_body -> 'record' is null then
        raise exception 'j102_evidence_moved: document % is no longer readable',
          v_item -> 'selector' ->> 'document_id' using errcode = '40001';
      end if;
      v_record := v_body -> 'record';
      if (v_record -> 'neon_identity' ->> 'version_no')
           is distinct from (v_item ->> 'expected_version_no')
         or (v_record -> 'neon_identity' ->> 'content_digest')
           is distinct from (v_item ->> 'expected_content_digest') then
        raise exception 'j102_evidence_moved: document % is not the version this transition was decided against',
          v_item -> 'selector' ->> 'document_id' using errcode = '40001';
      end if;
      v_link := ops.j102_evidence_subject_link(
        'f01_document', v_item -> 'selector' ->> 'document_id',
        (v_item ->> 'expected_version_no')::integer, v_item ->> 'expected_content_digest',
        v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id');
      if v_link is null or (v_link ->> 'link_digest') is distinct from (v_item ->> 'expected_link_digest') then
        raise exception 'j102_evidence_unbound: document % is not associated with % % under this lock',
          v_item -> 'selector' ->> 'document_id',
          v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id'
          using errcode = '40001';
      end if;
    elsif (v_item ->> 'source') = 'f01_corporate_artifact' then
      v_body := ops.f01_stored_artifact(v_item -> 'selector' ->> 'artifact_digest');
      if v_body is null then
        raise exception 'j102_evidence_moved: artifact % is no longer readable',
          v_item -> 'selector' ->> 'artifact_digest' using errcode = '40001';
      end if;
      v_link := ops.j102_evidence_subject_link(
        'f01_corporate_artifact', v_item -> 'selector' ->> 'artifact_digest', 0,
        v_item -> 'selector' ->> 'artifact_digest',
        v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id');
      if v_link is null or (v_link ->> 'link_digest') is distinct from (v_item ->> 'expected_link_digest') then
        raise exception 'j102_evidence_unbound: artifact % is not associated with % % under this lock',
          v_item -> 'selector' ->> 'artifact_digest',
          v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id'
          using errcode = '40001';
      end if;
    elsif (v_item ->> 'source') = 'first_party_record' then
      v_body := ops.j102_first_party_record(
        v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id');
      if v_body is null then
        raise exception 'j102_evidence_moved: % record % is no longer readable',
          v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id'
          using errcode = '40001';
      end if;
      if (v_body ->> 'record_digest') is distinct from (v_item ->> 'expected_record_digest') then
        raise exception 'j102_evidence_moved: % record % is not the record this transition was decided against',
          v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id'
          using errcode = '40001';
      end if;
      -- The record's OWN typed binding, re-read under the lock from the row
      -- rather than taken from the manifest that travelled here.
      if (v_body -> 'record' ->> 'subject_kind') is distinct from (v_item -> 'binding' ->> 'subject_kind')
         or (v_body -> 'record' ->> 'subject_id') is distinct from (v_item -> 'binding' ->> 'subject_id') then
        raise exception 'j102_evidence_unbound: % record % is bound to % %, not to the % % this transition moves',
          v_item -> 'selector' ->> 'record_kind', v_item -> 'selector' ->> 'record_id',
          v_body -> 'record' ->> 'subject_kind', v_body -> 'record' ->> 'subject_id',
          v_item -> 'binding' ->> 'subject_kind', v_item -> 'binding' ->> 'subject_id'
          using errcode = '40001';
      end if;
    elsif (v_item ->> 'source') = 'typed_approval' then
      -- Unreachable through the shipped store, which refuses these paths before
      -- opening a transaction. Routed to the private reader anyway, so a future
      -- caller that reaches here gets the same refusal rather than a gap.
      perform ops.j102_typed_approval(
        v_item -> 'selector' ->> 'approval_kind', v_item -> 'selector' ->> 'approval_ref');
    else
      raise exception 'j102_unknown_evidence_source: %', v_item ->> 'source' using errcode = '22023';
    end if;
    v_checked := v_checked || jsonb_build_array(jsonb_build_object(
      'evidence_kind', v_item ->> 'evidence_kind',
      'source', v_item ->> 'source',
      'reader', v_item ->> 'reader',
      'bound_subject_kind', v_item -> 'binding' ->> 'subject_kind',
      'bound_subject_id', v_item -> 'binding' ->> 'subject_id',
      'still_exact', true,
      'still_bound', true));
  end loop;
  return v_checked;
end;
$$;

comment on function ops.j102_recheck_evidence(jsonb) is
  'Re-read the EXACT evidence pins a transition was decided against, inside the transaction that holds its subject locks. Any movement raises a serialization failure and the whole coupled transition is refused. Existence is not the check; sameness is.';

-- ---------------------------------------------------------------------------
-- THE ONE TRANSITION WRITER.
--
-- Q082's coupled facts, made structural: every proposed subject and every event
-- lands in ONE transaction or none of them does. There is no partial apply, no
-- per-subject writer a caller could call twice, and no free-form stage update
-- anywhere in this file.
--
-- THE LOCK ORDER IS DECLARED AND ACYCLIC. Every writer here takes locks in
-- exactly this order, and a future writer MUST slot into it:
--
--   tier 1  j102:request:<tenant>:<key>          (j102_replay_outcome; shared
--           with the private claim and re-entrant within one transaction, so a
--           caller that probes for a replay and then writes does not self-block)
--   tier 2  j102:subject:<tenant>:<kind>:<id>    (every subject this transition
--           touches, acquired in ASCENDING kind:id order)
--
-- ASCENDING ORDER IS WHAT MAKES TIER 2 SAFE. Two concurrent transitions touching
-- the same pair of subjects acquire them in the same sequence, so one waits and
-- the other proceeds instead of each holding what the other needs. The ordering
-- is applied to the union of the compare-and-swap operands and the proposed
-- subjects, so a subject that is only READ is locked exactly as one that is
-- written -- a decision taken against an unlocked read is a decision taken
-- against a value that can move underneath it.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_apply_transition(
  p_transition_id text,
  p_expected_state_digests jsonb,
  p_subject_envelopes jsonb,
  p_event_envelopes jsonb,
  p_evidence_recheck jsonb,
  p_idempotency_key text,
  p_request_digest text,
  p_diagnostics jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  -- H4. THE CLOCK IS THE DATABASE'S, and it is read ONCE here. Every writer used
  -- to take updated_at and recorded_at from the supplied envelope and store them
  -- unexamined, so anything holding the carr_writer execute grant could backdate
  -- lifecycle state and its history. now() is the transaction timestamp, so every
  -- row this call writes shares one instant and a receipt can never appear to
  -- precede the event it binds.
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_operation text := p_diagnostics ->> 'operation';
  v_replay jsonb;
  v_key text;
  v_envelope jsonb;
  v_record jsonb;
  v_state jsonb;
  v_kind text;
  v_id text;
  v_stored text;
  v_expected text;
  v_proposed jsonb := '{}'::jsonb;
  v_subject_digests jsonb := '{}'::jsonb;
  v_event_digests jsonb := '[]'::jsonb;
  v_checked jsonb;
  v_readback jsonb := '{}'::jsonb;
  v_result jsonb;
begin
  if v_operation is null then
    raise exception 'j102_operation_required' using errcode = '22023';
  end if;
  if jsonb_typeof(p_subject_envelopes) is distinct from 'array'
     or jsonb_array_length(p_subject_envelopes) < 1 then
    raise exception 'j102_no_subject_envelopes' using errcode = '22023';
  end if;
  if jsonb_typeof(p_event_envelopes) is distinct from 'array'
     or jsonb_array_length(p_event_envelopes) < 1 then
    -- A state change with no event is a change nobody can audit afterwards.
    raise exception 'j102_no_event_envelopes: every transition appends its history'
      using errcode = '22023';
  end if;

  v_replay := ops.j102_claim_idempotency(v_operation, p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;

  -- TIER 2, IN ASCENDING ORDER over the union of read and written subjects.
  for v_key in
    select k from (
      select jsonb_object_keys(p_expected_state_digests) as k
      union
      select (e -> 'record' ->> 'subject_kind') || ':' || (e -> 'record' ->> 'subject_id')
        from jsonb_array_elements(p_subject_envelopes) as e
    ) s order by k collate "C"
  loop
    perform pg_advisory_xact_lock(hashtextextended(
      'j102:subject:' || ops.f01_tenant() || ':' || v_key, 0));
  end loop;

  -- EVERY PROPOSED SUBJECT MUST CARRY AN OPERAND, and a caller that omits one is
  -- refused rather than defaulted.
  --
  -- BLOCK-1. The compare-and-swap loop used to iterate the SUPPLIED MAP, so a
  -- subject that appeared only in the envelopes -- a creation -- was never
  -- checked at all, and the upsert below silently overwrote whatever already
  -- held that key. A caller naming an existing deal id as its new deal replaced
  -- that deal's authoritative current state, closing date and all, under a
  -- different assignment, while its events stayed behind: history and current
  -- state disagreeing about what that id is. Iterating the UNION, and demanding
  -- a key for every proposed subject, is what closes it. A creation's operand is
  -- an explicit JSON null, which the `is distinct from` below reads as "this
  -- subject must be ABSENT" -- so a collision refuses instead of upserting, and
  -- omitting the key is not a way to ask for the old behaviour.
  for v_envelope in select * from jsonb_array_elements(p_subject_envelopes) loop
    v_key := (v_envelope -> 'record' ->> 'subject_kind') || ':'
             || (v_envelope -> 'record' ->> 'subject_id');
    if not (p_expected_state_digests ? v_key) then
      raise exception 'j102_expected_state_digest_missing: % is proposed with no compare-and-swap operand; a creation must supply an explicit null',
        v_key using errcode = '22023';
    end if;
    -- The envelope's own prior_state_digest and the operand are the same claim
    -- written twice, and they must agree: a pair that disagrees is a request
    -- whose history would describe a version its own check did not enforce.
    if (v_envelope -> 'record' ->> 'prior_state_digest')
         is distinct from (p_expected_state_digests ->> v_key) then
      raise exception 'j102_prior_state_digest_mismatch: % declares prior state % and its operand is %',
        v_key, coalesce(v_envelope -> 'record' ->> 'prior_state_digest', 'null'),
        coalesce(p_expected_state_digests ->> v_key, 'null') using errcode = '22023';
    end if;
    v_proposed := v_proposed || jsonb_build_object(v_key, true);
  end loop;

  -- THE COMPARE-AND-SWAP, decided against the STORED row under the lock, over the
  -- UNION of the supplied operands and the proposed subjects. Every operand is
  -- checked, including the ones for subjects this transition only read: a
  -- prerequisite that moved invalidates the decision exactly as a target that
  -- moved does. And presence AND ABSENCE are both enforced -- a null operand for
  -- a subject that now exists refuses, which is the creation collision.
  for v_key in
    select k from (
      select jsonb_object_keys(p_expected_state_digests) as k
      union
      select jsonb_object_keys(v_proposed)
    ) s order by k collate "C"
  loop
    v_kind := split_part(v_key, ':', 1);
    v_id := substr(v_key, length(v_kind) + 2);
    v_expected := p_expected_state_digests ->> v_key;
    select ops.f01_digest_jsonb(c.envelope -> 'record' -> 'state') into v_stored
      from ops.j102_subject_current c
     where c.tenant = ops.f01_tenant() and c.subject_kind = v_kind and c.subject_id = v_id;
    if v_stored is distinct from v_expected then
      raise exception 'j102_stale_subject_digest: the current state of % is %, and the caller decided against %',
        v_key, coalesce(v_stored, 'absent'), coalesce(v_expected, 'absent')
        using errcode = '40001';
    end if;
  end loop;

  -- THE EVIDENCE RECHECK, under the locks just taken and before any write.
  v_checked := ops.j102_recheck_evidence(p_evidence_recheck);

  -- Every proposed subject, written. A subject whose operand is null is a
  -- CREATION and the loop above has already proved, under this transaction's
  -- locks, that no row holds that key; a subject whose operand is a digest is an
  -- update of the exact version that digest names. Both take the same advisory
  -- lock, so two concurrent creations of one id serialize on it and the second
  -- one's null operand meets the first one's committed row and refuses.
  for v_envelope in select * from jsonb_array_elements(p_subject_envelopes) loop
    v_record := v_envelope -> 'record';
    v_state := v_record -> 'state';
    v_kind := v_record ->> 'subject_kind';
    v_id := v_record ->> 'subject_id';
    if (v_record ->> 'updated_by') is distinct from v_actor then
      raise exception 'j102_actor_injection_refused: updated_by is derived, never supplied'
        using errcode = '42501';
    end if;
    -- H4. THE INSTANT IS THE DATABASE'S, verified against this transaction's own
    -- clock and then stamped from it. A caller-chosen updated_at is refused here
    -- rather than stored, so no grant holder can backdate lifecycle state.
    if (v_record ->> 'updated_at') is distinct from v_txn_now_text then
      raise exception 'j102_clock_injection_refused: updated_at is the database transaction time %, not %',
        v_txn_now_text, coalesce(v_record ->> 'updated_at', 'null') using errcode = '42501';
    end if;
    if ops.f01_digest_jsonb(v_record) is distinct from (v_envelope ->> 'record_digest') then
      raise exception 'j102_subject_digest_mismatch: the supplied subject does not hash to its claim'
        using errcode = '22000';
    end if;
    insert into ops.j102_subject_current as c
      (tenant, subject_kind, subject_id, envelope, envelope_digest, state_digest,
       parent_id, deal_state, updated_by, updated_at)
    values (
      ops.f01_tenant(), v_kind, v_id, v_envelope, ops.f01_digest_jsonb(v_envelope),
      ops.f01_digest_jsonb(v_state),
      coalesce(v_state ->> 'relationship_id', v_state ->> 'engagement_id',
               v_state ->> 'assignment_id'),
      v_state ->> 'deal_state',
      v_actor, v_txn_now)
    on conflict (tenant, subject_kind, subject_id) do update
      set envelope = excluded.envelope,
          envelope_digest = excluded.envelope_digest,
          state_digest = excluded.state_digest,
          parent_id = excluded.parent_id,
          deal_state = excluded.deal_state,
          updated_by = excluded.updated_by,
          updated_at = excluded.updated_at;
    v_subject_digests := v_subject_digests ||
      jsonb_build_object(v_kind || ':' || v_id, ops.f01_digest_jsonb(v_state));
    v_readback := v_readback ||
      jsonb_build_object(v_kind, ops.j102_subject(v_kind, v_id));
  end loop;

  -- Every event, appended. Same transaction as the state above, so history and
  -- current state can never disagree about whether something happened.
  for v_envelope in select * from jsonb_array_elements(p_event_envelopes) loop
    v_record := v_envelope -> 'record';
    if (v_record ->> 'recorded_by') is distinct from v_actor then
      raise exception 'j102_actor_injection_refused: recorded_by is derived, never supplied'
        using errcode = '42501';
    end if;
    if (v_record ->> 'recorded_at') is distinct from v_txn_now_text then
      raise exception 'j102_clock_injection_refused: recorded_at is the database transaction time %, not %',
        v_txn_now_text, coalesce(v_record ->> 'recorded_at', 'null') using errcode = '42501';
    end if;
    insert into ops.j102_subject_event
      (tenant, subject_kind, subject_id, event_kind, transition_id, envelope, envelope_digest,
       event_digest, recorded_by, recorded_at, idempotency_key)
    values (
      ops.f01_tenant(),
      v_record -> 'event' ->> 'subject_kind',
      v_record -> 'event' ->> 'subject_id',
      v_record -> 'event' ->> 'event_kind',
      v_record ->> 'transition_id',
      v_envelope, ops.f01_digest_jsonb(v_envelope), ops.f01_digest_jsonb(v_record),
      v_actor, v_txn_now, p_idempotency_key);
    v_event_digests := v_event_digests || jsonb_build_array(ops.f01_digest_jsonb(v_record));
  end loop;

  v_result := jsonb_build_object(
    'operation', v_operation,
    'decision', 'allow',
    'outcome', 'applied',
    'actor_slug', v_actor,
    'transition_id', p_transition_id,
    'reason_id', p_diagnostics ->> 'reason_id',
    'coupled_facts_committed', coalesce(p_diagnostics -> 'coupled_facts', '[]'::jsonb),
    'decision_refs', coalesce(p_diagnostics -> 'decision_refs', '[]'::jsonb),
    'subject_digests', v_subject_digests,
    'event_digests', v_event_digests,
    'evidence_rechecked_under_lock', true,
    'evidence_bound_under_lock', true,
    'evidence_checked', v_checked,
    -- THE COMMITTED RECEIPT. The instant every row in this transaction carries,
    -- taken from the database rather than echoed back from the request, and the
    -- operands the swap was actually decided against.
    'committed_at', v_txn_now_text,
    'committed_state_digests', v_subject_digests,
    'expected_state_digests', p_expected_state_digests,
    'readback', v_readback,
    'external_effects', false);
  return ops.j102_settle_idempotency(v_operation, p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb) is
  'The ONLY writer of lifecycle state. Claims its idempotency key before reading any state, locks every subject it reads or writes in ascending order, requires a compare-and-swap operand for EVERY proposed subject (an explicit null for a creation, which the swap enforces as absence), re-reads the exact evidence pins and their subject associations under those locks, derives both the actor and the instant, then writes every proposed subject and every event in one transaction or none.';

-- ---------------------------------------------------------------------------
-- The three non-transition writers.
-- ---------------------------------------------------------------------------

create or replace function ops.j102_record_first_party_fact(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb;
begin
  v_replay := ops.j102_claim_idempotency('record-lifecycle-fact', p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_fact_digest_mismatch: the supplied record does not hash to its claim'
      using errcode = '22000';
  end if;
  if (v_record ->> 'recorded_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: recorded_by is derived, never supplied'
      using errcode = '42501';
  end if;
  -- H5. The author's class is DERIVED from the same principal the actor comes
  -- from; a record claiming a different one is refused rather than believed.
  if (v_record ->> 'recorded_by_authorization_class') is distinct from v_class then
    raise exception 'j102_author_class_injection_refused: the author class is derived (% here), never supplied',
      v_class using errcode = '42501';
  end if;
  if v_record ->> 'record_kind' in ('winning_property_commitment', 'closing_settlement',
                                    'deal_failure', 'lifecycle_correction')
     and v_class <> 'verified_partner' then
    raise exception 'j102_partner_authored_record_refused: a % record is authored by a verified partner, and % holds %',
      v_record ->> 'record_kind', v_actor, v_class using errcode = '42501';
  end if;
  -- H4. The instant is the transaction's, verified and then stamped from it.
  if (v_record ->> 'recorded_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: recorded_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'recorded_at', 'null') using errcode = '42501';
  end if;
  -- BLOCK-2, and WHY THE EXISTENCE OF THE BOUND SUBJECT IS NOT RAISED HERE.
  --
  -- The store refuses `bound_subject_not_found` before it reaches this function,
  -- so a record bound to an id nobody holds does not get written through the
  -- shipped path. It is deliberately not a hard error at this layer, because
  -- requiring the subject to pre-exist would make the record layer unseedable in
  -- a single transaction -- a business record needs its subject, a transition
  -- needs its record, and nothing here creates the first subject (see the store's
  -- V5_J102_UNWIRED_CAPABILITIES). What actually gives the binding its force is
  -- one layer along: ops.j102_recheck_evidence re-reads this row's own typed
  -- binding under the transition's lock and refuses when it is not the subject
  -- being moved. A record bound to an id that does not exist can therefore never
  -- advance anything, which is the property that matters.
  insert into ops.j102_first_party_record
    (tenant, record_kind, record_id, envelope, envelope_digest, record_digest,
     bound_subject_kind, bound_subject_id,
     closing_date, recorded_by, recorded_by_class, recorded_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'record_kind', v_record ->> 'record_id',
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_record ->> 'subject_kind', v_record ->> 'subject_id',
    -- THE ONE DATE THAT IS NOT THE SERVER'S, and it is the business fact this
    -- record exists to carry: Q094 closes a deal on the ACTUAL final closing
    -- date, which is a statement about the world and not about when the row was
    -- written. It stays distinct from recorded_at above on purpose.
    case when v_record ->> 'closing_date' is null then null
         else ops.f01_instant(v_record ->> 'closing_date') end,
    v_actor, v_class, v_txn_now, p_idempotency_key);
  v_result := jsonb_build_object(
    'operation', 'record-lifecycle-fact', 'decision', 'allow',
    'reason_id', 'first_party_record_appended', 'actor_slug', v_actor,
    'record_kind', v_record ->> 'record_kind', 'record_id', v_record ->> 'record_id',
    'record_digest', v_digest,
    'bound_subject_kind', v_record ->> 'subject_kind',
    'bound_subject_id', v_record ->> 'subject_id',
    'recorded_by_authorization_class', v_class,
    'committed_at', v_txn_now_text,
    'readback', ops.j102_first_party_record(v_record ->> 'record_kind', v_record ->> 'record_id'),
    'advances_lifecycle_state', false, 'external_effects', false);
  return ops.j102_settle_idempotency('record-lifecycle-fact', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_first_party_fact(jsonb,text,text) is
  'Append one authenticated first-party business record, bound to an existing subject and stamped with the derived author, the derived author class and the database transaction time. The record''s closing_date remains the business fact it carries and is deliberately distinct from the server instant. It advances no lifecycle state; a transition still has to accept it as evidence.';

create or replace function ops.j102_record_evidence_subject_link(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_class text := ops.f01_principal() ->> 'authorization_class';
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb; v_seq bigint;
begin
  -- A partner's statement about which transaction a document belongs to. An
  -- agent that could make it could bind any authentic lease to any deal.
  if v_class <> 'verified_partner' then
    raise exception 'j102_evidence_association_requires_partner_authority: % holds %',
      v_actor, v_class using errcode = '42501';
  end if;
  v_replay := ops.j102_claim_idempotency('record-evidence-subject-link',
    p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_link_digest_mismatch' using errcode = '22000';
  end if;
  if (v_record ->> 'associated_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: associated_by is derived, never supplied'
      using errcode = '42501';
  end if;
  if (v_record ->> 'associated_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: associated_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'associated_at', 'null') using errcode = '42501';
  end if;
  -- BOTH ENDS ARE REAL, checked here as well as in the store. The subject has to
  -- exist in this rail; the pin has to exist in F01, at exactly the version and
  -- content digest named, so an association cannot be made to a document version
  -- the record layer does not hold.
  if ops.j102_subject(v_record ->> 'subject_kind', v_record ->> 'subject_id') is null then
    raise exception 'j102_link_subject_missing: no % % exists to associate evidence with',
      v_record ->> 'subject_kind', v_record ->> 'subject_id' using errcode = '23503';
  end if;
  if (v_record ->> 'evidence_source') = 'f01_document' then
    if (ops.f01_read('document', jsonb_build_object(
          'document_id', v_record ->> 'evidence_ref')) -> 'body' -> 'record'
          -> 'neon_identity' ->> 'content_digest')
         is distinct from (v_record ->> 'content_digest') then
      raise exception 'j102_link_pin_not_held: F01 does not hold document % at the content digest this association names',
        v_record ->> 'evidence_ref' using errcode = '23503';
    end if;
  elsif ops.f01_stored_artifact(v_record ->> 'evidence_ref') is null then
    raise exception 'j102_link_pin_not_held: no stored corporate artifact exists for %',
      v_record ->> 'evidence_ref' using errcode = '23503';
  end if;
  insert into ops.j102_evidence_subject_link
    (tenant, evidence_source, evidence_ref, version_no, content_digest,
     subject_kind, subject_id, envelope, envelope_digest, link_digest,
     associated_by, associated_by_class, associated_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'evidence_source', v_record ->> 'evidence_ref',
    coalesce((v_record ->> 'version_no')::integer, 0), v_record ->> 'content_digest',
    v_record ->> 'subject_kind', v_record ->> 'subject_id',
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_actor, v_class, v_txn_now, p_idempotency_key)
  returning link_seq into v_seq;
  v_result := jsonb_build_object(
    'operation', 'record-evidence-subject-link', 'decision', 'allow',
    'reason_id', 'evidence_subject_association_appended', 'actor_slug', v_actor,
    'link_digest', v_digest,
    'evidence_source', v_record ->> 'evidence_source',
    'bound_subject_kind', v_record ->> 'subject_kind',
    'bound_subject_id', v_record ->> 'subject_id',
    'committed_at', v_txn_now_text,
    'readback', jsonb_build_object('link_seq', v_seq, 'record', v_record),
    'advances_lifecycle_state', false, 'creates_document', false,
    'asserts_document_state', false, 'external_effects', false);
  return ops.j102_settle_idempotency('record-evidence-subject-link', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_evidence_subject_link(jsonb,text,text) is
  'BLOCK-2''s producer: append one partner-authored association between an exact F01 document version or corporate artifact and one existing lifecycle subject. It creates no document, asserts no document state, and advances no lifecycle state.';

create or replace function ops.j102_record_salesforce_reference(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor text := ops.f01_context_actor_slug();
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb; v_seq bigint;
begin
  v_replay := ops.j102_claim_idempotency('link-salesforce-reference', p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_reference_digest_mismatch' using errcode = '22000';
  end if;
  if (v_record ->> 'recorded_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: recorded_by is derived, never supplied'
      using errcode = '42501';
  end if;
  -- Q083, enforced rather than merely intended: a reference whose stored record
  -- claims to set lifecycle state is refused outright.
  if (v_record ->> 'phase_label_is_doctorcre_state') is distinct from 'false' then
    raise exception 'j102_salesforce_label_mapping_refused: a Salesforce phase is never a DoctorCRE state'
      using errcode = '42501';
  end if;
  -- H4. `recorded_at` is OURS and is verified; `observed_at` is SALESFORCE'S own
  -- statement about when it saw its own record, and stays the caller's to supply.
  -- The two are different facts and this is the one place they meet.
  if (v_record ->> 'recorded_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: recorded_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'recorded_at', 'null') using errcode = '42501';
  end if;
  insert into ops.j102_salesforce_reference
    (tenant, opportunity_id, opportunity_name, opportunity_phase, linked_subject_kind,
     linked_subject_id, observed_at, envelope, envelope_digest, reference_digest,
     recorded_by, recorded_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'opportunity_id', v_record ->> 'opportunity_name',
    v_record ->> 'opportunity_phase', v_record ->> 'linked_subject_kind',
    v_record ->> 'linked_subject_id', ops.f01_instant(v_record ->> 'observed_at'),
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_actor, v_txn_now, p_idempotency_key)
  returning reference_seq into v_seq;
  v_result := jsonb_build_object(
    'operation', 'link-salesforce-reference', 'decision', 'allow',
    'reason_id', case when v_record ->> 'linked_subject_kind' is null
                   then 'external_reference_recorded_unlinked'
                   else 'external_reference_progressively_linked' end,
    'actor_slug', v_actor,
    'opportunity_id', v_record ->> 'opportunity_id',
    'linked_subject_kind', v_record ->> 'linked_subject_kind',
    'reference_digest', v_digest,
    'committed_at', v_txn_now_text,
    'readback', jsonb_build_object('reference_seq', v_seq, 'record', v_record),
    'sets_lifecycle_state', false, 'external_effects', false);
  return ops.j102_settle_idempotency('link-salesforce-reference', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_salesforce_reference(jsonb,text,text) is
  'Q083: append one external Salesforce opportunity reference with its own name and phase. It sets no lifecycle state, and a record claiming otherwise is refused.';

create or replace function ops.j102_record_correction(
  p_envelope jsonb, p_idempotency_key text, p_request_digest text)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_principal jsonb := ops.f01_principal();
  v_actor text := ops.f01_context_actor_slug();
  v_txn_now timestamptz := now();
  v_txn_now_text text := ops.f01_instant_text(now());
  v_replay jsonb; v_record jsonb; v_digest text; v_result jsonb; v_seq bigint;
begin
  -- humanOnly plus authorityOnly, checked HERE and not only in the handler, so a
  -- writer that somehow reached this function still refuses.
  --
  -- M3. FROM THE DERIVED PRINCIPAL, NOT FROM A ROLE-NAME LITERAL. This used to
  -- read `session_user not in ('carr_authority_joe','carr_authority_dell')`,
  -- which was a SECOND identity source sitting beside ops.f01_principal() — one
  -- that would have to be edited in two places to stay true, and that answered a
  -- different question from the one every other writer here asks. The principal
  -- is derived from session_user in exactly one place, and this reads that.
  if (v_principal ->> 'human') is distinct from 'true'
     or (v_principal ->> 'authorization_class') is distinct from 'verified_partner' then
    raise exception 'j102_correction_requires_partner_authority: % may not correct the lifecycle record',
      v_actor using errcode = '42501';
  end if;
  v_replay := ops.j102_claim_idempotency('record-lifecycle-correction', p_idempotency_key, p_request_digest);
  if v_replay is not null then return v_replay; end if;
  v_record := p_envelope -> 'record';
  v_digest := ops.f01_digest_jsonb(v_record);
  if (p_envelope ->> 'record_digest') is distinct from v_digest then
    raise exception 'j102_receipt_digest_mismatch' using errcode = '22000';
  end if;
  if (v_record ->> 'corrected_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: corrected_by is derived, never supplied'
      using errcode = '42501';
  end if;
  if (v_record ->> 'corrected_at') is distinct from v_txn_now_text then
    raise exception 'j102_clock_injection_refused: corrected_at is the database transaction time %, not %',
      v_txn_now_text, coalesce(v_record ->> 'corrected_at', 'null') using errcode = '42501';
  end if;
  -- The correction must rest on a durable authored record, not on a summary of
  -- what somebody meant. An absent one refuses here as well as in the store.
  if ops.j102_first_party_record('lifecycle_correction',
       v_record ->> 'correction_record_id') is null then
    raise exception 'j102_correction_record_missing: no lifecycle_correction record % exists',
      v_record ->> 'correction_record_id' using errcode = '23503';
  end if;
  insert into ops.j102_correction_receipt
    (tenant, subject_kind, subject_id, correction_record_id, reason, prior_state_digest,
     envelope, envelope_digest, receipt_digest, corrected_by, corrected_at, idempotency_key)
  values (
    ops.f01_tenant(), v_record ->> 'subject_kind', v_record ->> 'subject_id',
    v_record ->> 'correction_record_id', v_record ->> 'reason',
    v_record ->> 'prior_state_digest',
    p_envelope, ops.f01_digest_jsonb(p_envelope), v_digest,
    v_actor, v_txn_now, p_idempotency_key)
  returning receipt_seq into v_seq;
  v_result := jsonb_build_object(
    'operation', 'record-lifecycle-correction', 'decision', 'allow',
    'reason_id', 'correction_receipt_appended', 'actor_slug', v_actor,
    'receipt_digest', v_digest,
    'committed_at', v_txn_now_text,
    'corrected_fields', coalesce(v_record -> 'corrected_fields', '[]'::jsonb),
    'readback', jsonb_build_object('receipt_seq', v_seq, 'record', v_record),
    'external_effects', false);
  return ops.j102_settle_idempotency('record-lifecycle-correction', p_idempotency_key, v_result);
end;
$$;

comment on function ops.j102_record_correction(jsonb,text,text) is
  'Q082/Q072: append one partner-authority correction receipt binding the reason, the durable correction record and the exact prior state digest. Append-only; it overwrites nothing and no assistant text is ever its basis.';

create or replace function ops.j102_record_reconciliation_item(p_envelope jsonb)
returns jsonb language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare v_actor text := ops.f01_context_actor_slug(); v_record jsonb; v_seq bigint;
begin
  v_record := p_envelope -> 'record';
  if (v_record ->> 'proposed_by') is distinct from v_actor then
    raise exception 'j102_actor_injection_refused: proposed_by is derived, never supplied'
      using errcode = '42501';
  end if;
  insert into ops.j102_reconciliation_item
    (tenant, subject_kind, subject_id, conflict_kind, base_version_digest,
     current_version_digest, envelope, envelope_digest, item_digest, proposed_by, recorded_at)
  values (
    ops.f01_tenant(), v_record ->> 'subject_kind', v_record ->> 'subject_id',
    v_record ->> 'conflict_kind', v_record ->> 'base_version_digest',
    v_record ->> 'current_version_digest', p_envelope, ops.f01_digest_jsonb(p_envelope),
    ops.f01_digest_jsonb(v_record), v_actor, now())
  returning item_seq into v_seq;
  return jsonb_build_object('item_seq', v_seq, 'visible', true, 'resolved_by_machine', false);
end;
$$;

comment on function ops.j102_record_reconciliation_item(jsonb) is
  'Q103: record one visible conflict with both versions preserved. Nothing here resolves it.';

-- ---------------------------------------------------------------------------
-- Q081 -- the compatibility projection and the migration shadow.
--
-- READ-ONLY, AND DERIVED ON EVERY READ. The legacy shape is reconstructed from
-- the current records; it stores nothing of its own, so there is no second home
-- to drift. It is a function rather than a view precisely so that nobody can
-- write through it.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_compatibility_view(p_assignment_id text, p_deal_id text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_assignment jsonb; v_deal jsonb; v_phase text; v_source text; v_deal_state text;
begin
  v_assignment := case when p_assignment_id is null then null
                    else ops.j102_subject('assignment', p_assignment_id) end;
  v_deal := case when p_deal_id is null then null else ops.j102_subject('deal', p_deal_id) end;
  if v_assignment is null and v_deal is null then
    raise exception 'j102_compatibility_subject_required: name an assignment, a deal, or both'
      using errcode = '22023';
  end if;
  v_deal_state := v_deal -> 'state' ->> 'deal_state';
  if v_deal is not null and v_deal_state <> 'cancelled' then
    v_source := 'deal';
    v_phase := case
      when (v_deal -> 'state' ->> 'closing_state') = 'closed' then 'closing'
      when (v_deal -> 'state' ->> 'diligence_state') = 'in_progress' then 'due_diligence'
      when (v_deal -> 'state' ->> 'execution_state') = 'executed' then 'legal'
      else 'negotiation' end;
  elsif v_assignment is not null then
    v_source := 'assignment';
    v_phase := case (v_assignment -> 'state' ->> 'assignment_phase')
      when 'research' then 'research'
      when 'search' then 'site_selection'
      when 'concluded' then 'closing'
      else 'negotiation' end;
  end if;
  return jsonb_build_object(
    'legacy_phase', v_phase,
    'legacy_phase_source', v_source,
    'legacy_closed', coalesce(v_deal_state = 'closed', false),
    'legacy_outcome', case v_deal_state
      when 'closed' then 'won' when 'cancelled' then 'lost'
      when 'pending' then 'open' else null end,
    'assignment_id', p_assignment_id,
    'deal_id', p_deal_id,
    -- The four properties that keep this a view rather than a second home.
    'authoritative', false,
    'writable', false,
    'derived_from_current_records', true,
    'retires_any_caller', false);
end;
$$;

comment on function ops.j102_compatibility_view(text,text) is
  'Q081: the old single-phase shape, PROJECTED from the current records on every read for callers that have not migrated. Not authoritative, not writable, and it retires nobody.';

create or replace function ops.j102_migration_readiness()
returns jsonb language sql stable
set search_path = pg_catalog, ops, public
as $$
  select jsonb_build_object(
    'decision', 'refuse',
    'reason_id', 'caller_census_absent',
    'may_retire_callers', false,
    'migration_complete', false,
    'caller_census_verified', false,
    'compatibility_view_available', true,
    'big_bang_rename', false,
    'missing_facts', jsonb_build_array(
      jsonb_build_object(
        'fact', 'exact_caller_census',
        'why', 'Q081 permits retiring the old interface only after migration proof, and no verified enumeration of the callers and projections still reading the legacy shape exists in this record layer.',
        'produced_by', 'not_produced_by_this_slice'),
      jsonb_build_object(
        'fact', 'shadow_comparison_clean_run',
        'why', 'A clean shadow comparison over the real rows has not been performed and is not asserted here.',
        'produced_by', 'not_produced_by_this_slice')))
$$;

comment on function ops.j102_migration_readiness() is
  'Q081: whether the old interface may be retired. It answers no, for every input, because no verified caller census exists here. A reader, and it says no.';

-- ---------------------------------------------------------------------------
-- The read door.
-- ---------------------------------------------------------------------------
create or replace function ops.j102_read(p_kind text, p_selector jsonb default '{}'::jsonb)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_actor text := ops.f01_context_actor_slug(); v_body jsonb;
begin
  if p_kind = 'subject' then
    v_body := ops.j102_subject(p_selector ->> 'subject_kind', p_selector ->> 'subject_id');
  elsif p_kind = 'subject_events' then
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select e.event_seq as seq,
                   ops.j102_verify_envelope(e.envelope, e.envelope_digest, e.event_digest,
                                            'stored_lifecycle_event') as verified
              from ops.j102_subject_event e
             where e.tenant = ops.f01_tenant()
               and e.subject_kind = p_selector ->> 'subject_kind'
               and e.subject_id = p_selector ->> 'subject_id') s;
  elsif p_kind = 'first_party_record' then
    v_body := ops.j102_first_party_record(p_selector ->> 'record_kind', p_selector ->> 'record_id');
  elsif p_kind = 'evidence_subject_links' then
    -- The associations one subject holds, so a reviewer can see WHICH documents
    -- a deal's transitions were entitled to rest on without having to ask the
    -- writer.
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select l.link_seq as seq,
                   ops.j102_verify_envelope(l.envelope, l.envelope_digest, l.link_digest,
                                            'stored_evidence_subject_link') as verified
              from ops.j102_evidence_subject_link l
             where l.tenant = ops.f01_tenant()
               and l.subject_kind = p_selector ->> 'subject_kind'
               and l.subject_id = p_selector ->> 'subject_id') s;
  elsif p_kind = 'salesforce_references' then
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select r.reference_seq as seq,
                   ops.j102_verify_envelope(r.envelope, r.envelope_digest, r.reference_digest,
                                            'stored_salesforce_reference') as verified
              from ops.j102_salesforce_reference r
             where r.tenant = ops.f01_tenant()
               and (p_selector ->> 'opportunity_id' is null
                    or r.opportunity_id = p_selector ->> 'opportunity_id')) s;
  elsif p_kind = 'correction_receipts' then
    select coalesce(jsonb_agg(verified order by seq), '[]'::jsonb) into v_body
      from (select c.receipt_seq as seq,
                   ops.j102_verify_envelope(c.envelope, c.envelope_digest, c.receipt_digest,
                                            'stored_correction_receipt') as verified
              from ops.j102_correction_receipt c
             where c.tenant = ops.f01_tenant()
               and c.subject_kind = p_selector ->> 'subject_kind'
               and c.subject_id = p_selector ->> 'subject_id') s;
  elsif p_kind = 'reconciliation_items' then
    select coalesce(jsonb_agg(i.envelope -> 'record' order by i.item_seq), '[]'::jsonb) into v_body
      from ops.j102_reconciliation_item i
     where i.tenant = ops.f01_tenant()
       and i.subject_kind = p_selector ->> 'subject_kind'
       and i.subject_id = p_selector ->> 'subject_id';
  elsif p_kind = 'compatibility_view' then
    v_body := ops.j102_compatibility_view(
      p_selector ->> 'assignment_id', p_selector ->> 'deal_id');
  elsif p_kind = 'migration_shadow' then
    v_body := ops.j102_migration_readiness();
  else
    raise exception 'j102_unknown_read_kind: %', p_kind using errcode = '22023';
  end if;
  return jsonb_build_object(
    'operation', 'read-cre-lifecycle',
    'kind', p_kind,
    'tenant', ops.f01_tenant(),
    'actor_slug', v_actor,
    'server_time', ops.f01_now_text(),
    'body', v_body,
    'integrity', 'recomputed_not_trusted');
end;
$$;

comment on function ops.j102_read(text,jsonb) is
  'The one read door for J102 records. Every envelope it returns has had both digests recomputed from committed bytes; it exposes no Salesforce-to-lifecycle mapping and no write path.';

-- ---------------------------------------------------------------------------
-- Grants. Reads reach the ordinary bundles. DIRECT INSERT IS GRANTED TO NOBODY:
-- every write goes through a definer function that derives its own actor, so a
-- writer holding a raw connection cannot attribute a row to someone else.
--
-- No role is created by this file. Every role named below already exists.
-- ---------------------------------------------------------------------------
grant select on ops.j102_subject_current, ops.j102_subject_event,
  ops.j102_first_party_record, ops.j102_evidence_subject_link,
  ops.j102_salesforce_reference,
  ops.j102_correction_receipt, ops.j102_reconciliation_item
  to carr_reader, carr_writer, carr_authority;

revoke insert, update, delete, truncate on ops.j102_subject_current,
  ops.j102_subject_event, ops.j102_first_party_record, ops.j102_evidence_subject_link,
  ops.j102_salesforce_reference,
  ops.j102_correction_receipt, ops.j102_reconciliation_item, ops.j102_idempotency
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.j102_verify_envelope(jsonb,text,text,text),
  ops.j102_subject(text,text), ops.j102_first_party_record(text,text),
  ops.j102_evidence_subject_link(text,text,integer,text,text,text),
  ops.j102_compatibility_view(text,text), ops.j102_migration_readiness(),
  ops.j102_read(text,jsonb), ops.j102_recheck_evidence(jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.j102_verify_envelope(jsonb,text,text,text),
  ops.j102_subject(text,text), ops.j102_first_party_record(text,text),
  ops.j102_evidence_subject_link(text,text,integer,text,text,text),
  ops.j102_compatibility_view(text,text), ops.j102_migration_readiness(),
  ops.j102_read(text,jsonb)
  to carr_reader, carr_writer, carr_jobs, carr_authority;
-- The evidence recheck is reachable only from the transition writer that owns
-- it. Exposing it would let a caller establish "the evidence was still exact"
-- outside the transaction that holds the locks making that statement true.
revoke all on function ops.j102_recheck_evidence(jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- THE PRIVATE APPROVAL READER IS GRANTED TO NOBODY, for the reason its own
-- comment gives: a callable stub is the first step toward a configurable one.
revoke all on function ops.j102_typed_approval(text,text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function
  ops.j102_replay_outcome(text,text,text), ops.j102_claim_idempotency(text,text,text),
  ops.j102_settle_idempotency(text,text,jsonb),
  ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb),
  ops.j102_record_first_party_fact(jsonb,text,text),
  ops.j102_record_evidence_subject_link(jsonb,text,text),
  ops.j102_record_salesforce_reference(jsonb,text,text),
  ops.j102_record_correction(jsonb,text,text),
  ops.j102_record_reconciliation_item(jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function
  ops.j102_replay_outcome(text,text,text),
  ops.j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb),
  ops.j102_record_first_party_fact(jsonb,text,text),
  ops.j102_record_salesforce_reference(jsonb,text,text),
  ops.j102_record_reconciliation_item(jsonb)
  to carr_writer, carr_authority;
-- Correction and the evidence association reach the authority bundle only, and
-- each additionally checks the DERIVED PRINCIPAL inside the function: the grant
-- says who may call it, the check says who may succeed.
grant execute on function ops.j102_record_correction(jsonb,text,text),
  ops.j102_record_evidence_subject_link(jsonb,text,text)
  to carr_authority;

-- ---------------------------------------------------------------------------
-- The direct-DML guard's schema assumption, asserted rather than assumed.
--
-- The guard matches an OPTIONALLY ops-qualified frame, because a genuine ops
-- writer prints unqualified under the guard's own search_path. That is only safe
-- while each unqualified writer NAME resolves to the ops function under exactly
-- that search_path; if something else shadowed one, an unqualified frame from
-- the impostor would satisfy the pattern. This block refuses to leave the file
-- in that state.
-- ---------------------------------------------------------------------------
do $$
declare v_name text; v_signature text;
begin
  perform set_config('search_path', 'pg_catalog, ops, public', true);
  foreach v_signature in array array[
    'j102_apply_transition(text,jsonb,jsonb,jsonb,jsonb,text,text,jsonb)',
    'j102_record_first_party_fact(jsonb,text,text)',
    'j102_record_evidence_subject_link(jsonb,text,text)',
    'j102_record_salesforce_reference(jsonb,text,text)',
    'j102_record_correction(jsonb,text,text)',
    'j102_record_reconciliation_item(jsonb)',
    'j102_claim_idempotency(text,text,text)',
    'j102_settle_idempotency(text,text,jsonb)'
  ] loop
    v_name := to_regprocedure(v_signature)::text;
    if to_regprocedure(v_signature) is distinct from to_regprocedure('ops.' || v_signature) then
      raise exception 'j102_writer_name_shadowed: the unqualified name % resolves to %, not to the ops writer; the direct-DML guard cannot rely on an unqualified frame',
        v_signature, coalesce(v_name, 'nothing');
    end if;
  end loop;
end $$;
