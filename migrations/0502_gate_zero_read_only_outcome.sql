-- DoctorCRE v5 slice V5-A02, Step B: the Gate Zero read-only outcome RECORD.
--
-- WHAT THIS CLOSES. Benchmark acceptance has failed closed since V5-A00 on one
-- prerequisite: this record layer held no authenticated Gate Zero outcome, so
-- there was nothing here for an acceptance to bind to. Two fail-closed stubs
-- said so, one in SQL (ops.benchmark_gate_zero_outcome) and one in the module
-- (readGateZeroOutcome), and both said the same thing in their own words: land
-- them TOGETHER or the gate opens without a record on the other side. This
-- migration lands the record and the SQL half; the module half lands in the
-- same commit.
--
-- WHY THE OUTCOME IS PRODUCED BY CODE AND NOT BY A PARTNER. r7's amended
-- producer registry (decision 311a9af5-3685-4c47-a158-f8dd70870ca1, applied
-- under Joe's ruling on open loop #589) registers
-- step:gate-zero-read-only-outcome as a v5 producer with the role
-- independent_control_plane_oracle and the oracle
-- oracle:gate-producer:gate-zero-read-only. The provisional ruling
-- 20c83902-f150-4d59-beca-915c5c871f95 put the human act DOWNSTREAM, on the
-- benchmark manifest, and left the Gate Zero outcome to the seat. So the older
-- "it arrives from outside and a human vouches for it" reading is retired here
-- by the packet, not by anyone's preference.
--
-- WHO MAY WRITE ONE, AND THE RULING BEHIND IT. Joe ruled on 2026-09-13
-- (decision d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70) that the non-human
-- review-token seat holding oracle:gate-producer:gate-zero-read-only -- the
-- independent Codex reviewer lane, staffed under the card-9 charter ruling
-- 8a1dad08-8707-4bb0-a159-c2831a00cea2 and Joe's blanket approval
-- 5e2b8c1a-9f47-4d63-b0e5-7a3d1c9f2e84 -- records the outcome row on its own
-- authority, with NO partner countersign. That is a NEW authority shape in this
-- system: every write verb before it gated on a verified human partner or on a
-- sponsored agent, and this one is neither. ops.gate_zero_producer_actor_id()
-- below is the record layer's half of it. The gateway's half is in
-- mcp-server/src/tools.js, and the two are deliberately independent: a handler
-- bug cannot step around the database, and a direct writer connection cannot
-- step around the gateway's derivation either, because this function re-derives
-- the seat from the server-established transaction context rather than trusting
-- anything sent to it.
--
-- NOTHING HERE IS A CALLER'S WORD FOR ANYTHING.
--   * The producing seat is DERIVED from carr.acting_actor_slug, which only
--     mcp.js's setWriterActorContext sets, and is never a parameter.
--   * The outcome digest is RECOMPUTED from the stored receipt with
--     ops.portfolio_canonical_json, which matches artifact-trust.js's
--     canonicalJson byte for byte. No caller-supplied digest is accepted, and
--     the recorded digest is therefore a statement about the persisted receipt
--     rather than about a value somebody once sent.
--   * The observed instant, the status, the candidate digest and the expiry are
--     READ OUT of the receipt, so the receipt and its columns cannot disagree.
--   * The r7 constants (gate id, step ref, producer role, oracle ref, oracle
--     version, receipt schema, evidence scope, subject environment, negative
--     admission result) are CHECK constraints, so a receipt that renames one is
--     refused by the table rather than by a reviewer.
--
-- THE DIGEST RECIPE IS A NAMED ASSUMPTION, NOT A RULE r7 STATES. r7's
-- receipt_payload_digest_rule names domain tags for four schemas and NOT for
-- consumer-gate-receipt.v1. This follows the repository's existing precedent
-- for a consumer-gate receipt digest -- plain canonical-JSON sha256 over the
-- receipt object with no domain tag (benchmark-minimum.v5.js:1480) -- and says
-- so out loud so a reviewer can overturn it in one line. The alternative
-- reading, digest(["consumer-gate-receipt.v1", receipt]), produces a DIFFERENT
-- value, and the value binds forever once a benchmark is accepted against it.
--
-- RETRYABLE, WITH EVERY RUN KEPT. The provisional ruling says so in as many
-- words. Rows are append-only and a second run over the same candidate is
-- IDEMPOTENT on the candidate digest rather than an error, so a retry cannot
-- fork the binding. A DIFFERENT candidate gets its own row, and
-- ops.benchmark_gate_zero_outcome() names exactly which of them is current.
--
-- WHAT THIS MIGRATION DOES NOT DO. It applies no part of
-- ops/benchmark-acceptance.candidate.sql: that file is still candidate source,
-- still not in public.schema_migrations, and landing it is still a separate
-- act. The Gate Zero reader it defines is UPDATED there in the same change so
-- the two never disagree, and it is REDEFINED here, against this table, for the
-- database that carries this migration. Nothing here accepts a benchmark,
-- starts the Journey 1 clock, or grants any dispatch or execution authority.

do $v5_a02_gate_zero_outcome_preflight$
begin
  if (select count(*) from public.schema_migrations
       where filename = '0501_scheduled_job_admission_and_scac_successor.sql') <> 1
     or not exists (select 1 from public.schema_migrations
       where filename = '0501_scheduled_job_admission_and_scac_successor.sql'
         and sha256 = 'a0014c38dd90874c0331f0fe4b56df4290cc11bb49e615945b70fbef967a2b3f') then
    raise exception 'V5-A02 Gate Zero outcome migration ledger receipt drifted at 0501';
  end if;
  -- 0496 is the source of ops.portfolio_canonical_json, which this migration
  -- uses to recompute the receipt digest. Naming the dependency here means a
  -- reordered series fails at the preflight rather than at the first write.
  if to_regprocedure('ops.portfolio_canonical_json(jsonb)') is null then
    raise exception 'V5-A02 Gate Zero outcome migration requires ops.portfolio_canonical_json(jsonb) from 0496';
  end if;
end $v5_a02_gate_zero_outcome_preflight$;

-- ---------------------------------------------------------------------------
-- The producing seat, derived and never supplied.
-- ---------------------------------------------------------------------------
-- THE HOLDER REF IS A LITERAL IN THIS FILE, exactly as it is a module-private
-- frozen literal in gate-zero-producer-registration.v5.js. There is no table of
-- eligible seats, no setting, no environment variable and no parameter: staffing
-- a seat is an edit somebody makes and reviews, in both places, or it does not
-- happen. Put a different lane in either half and the two disagree, which the
-- test suite reads as a defect rather than as a configuration.
create or replace function ops.gate_zero_producer_seat_holder_ref()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'seat:codex-reviewer:gpt-5.6-sol'::text $$;

comment on function ops.gate_zero_producer_seat_holder_ref() is
  'The staffed holder of oracle:gate-producer:gate-zero-read-only, as a literal. The same value gate-zero-producer-registration.v5.js declares module-private; the two are asserted equal by test.';

-- THE ONE AUTHORITY TEST, AND IT IS NARROWER THAN EVERY OTHER ONE HERE.
--
-- Four ordered questions, each answering "refuse":
--   1. Is a server-established acting actor set on this transaction? Only
--      mcp.js's setWriterActorContext sets it. No -> refuse.
--   2. Is that actor the LANE of the staffed seat -- the middle segment of the
--      holder ref -- and nothing else? A different review-token seat
--      (grok-reviewer authenticates identically and derives the same
--      review_agent class) is refused here by name.
--   3. Is the actor an ACTIVE, NON-HUMAN actor row? A human slug reaching this
--      function would mean a partner signing an oracle's receipt, which is the
--      exact thing the independent-oracle design exists to prevent, and Joe's
--      ruling put the human act downstream instead.
--   4. Is the seat still staffed in the registration this file mirrors? That is
--      the literal above; an unstaffed seat has no lane to match in (2).
--
-- IT DOES NOT ANSWER "WHO IS THIS", it answers "may THIS transaction record a
-- Gate Zero outcome". Nothing else in this database calls it.
create or replace function ops.gate_zero_producer_actor_id()
returns uuid language plpgsql stable
set search_path = pg_catalog, ops, public
as $$
declare v_slug text; v_lane text; v_id uuid; v_human boolean;
begin
  v_slug := nullif(current_setting('carr.acting_actor_slug', true), '');
  if v_slug is null then
    raise exception 'recording a Gate Zero read-only outcome requires the server-established actor context; none is set on this transaction';
  end if;
  v_lane := split_part(ops.gate_zero_producer_seat_holder_ref(), ':', 2);
  if v_lane = '' then
    raise exception 'the Gate Zero oracle seat is unstaffed; no actor may record an outcome';
  end if;
  if v_slug <> v_lane then
    raise exception 'only the staffed Gate Zero oracle seat may record a read-only outcome: the seat is held by %, and this transaction acts as %',
      ops.gate_zero_producer_seat_holder_ref(), v_slug;
  end if;
  select id, kind = 'human' into v_id, v_human from public.actor where slug = v_slug and active;
  if not found then
    raise exception 'the Gate Zero oracle seat lane % is not an active actor', v_slug;
  end if;
  if v_human then
    raise exception 'the Gate Zero oracle seat must be a machine identity; actor % is a human actor, and the human act in this chain is the benchmark acceptance downstream', v_slug;
  end if;
  return v_id;
end;
$$;

comment on function ops.gate_zero_producer_actor_id() is
  'Refuses every transaction except one acting as the staffed, active, non-human Gate Zero oracle seat lane. Derived from carr.acting_actor_slug; never a parameter. This is the record layer half of Joe''s 2026-09-13 ruling d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70; the gateway half is in mcp-server/src/tools.js and neither substitutes for the other.';

-- ---------------------------------------------------------------------------
-- The record.
-- ---------------------------------------------------------------------------
create table if not exists ops.gate_zero_read_only_outcome (
  id                        uuid primary key default gen_random_uuid(),
  idempotency_key           uuid not null unique,
  -- The r7 constants, as constraints. A receipt that renames one of them is a
  -- receipt for a different gate and is refused by the table.
  step_ref                  text not null
                              check (step_ref = 'step:gate-zero-read-only-outcome'),
  receipt_producer_step_ref text not null
                              check (receipt_producer_step_ref = 'step:gate-zero-read-only-outcome'),
  gate_id                   text not null
                              check (gate_id = 'gate-zero-read-only-accepted'),
  receipt_schema            text not null
                              check (receipt_schema = 'consumer-gate-receipt.v1'),
  producer_role             text not null
                              check (producer_role = 'independent_control_plane_oracle'),
  independent_oracle_ref    text not null
                              check (independent_oracle_ref = 'oracle:gate-producer:gate-zero-read-only'),
  oracle_version            text not null check (oracle_version = '1.0.0'),
  evidence_scope            text not null check (evidence_scope = 'candidate-and-test'),
  subject_environment       text not null check (subject_environment = 'candidate'),
  -- r7's negative_admission_result has exactly one legal value, and it is the
  -- clause a green-only run would miss: a Gate Zero run must observe its
  -- injected failures being DENIED, not merely observe passes.
  negative_admission_result text not null
                              check (negative_admission_result = 'all_required_denials_observed'),
  -- The producing seat, as recorded, and the actor row it resolved to. Both are
  -- derived by ops.gate_zero_record_read_only_outcome and neither is writable
  -- from outside it.
  producing_seat_ref        text not null
                              check (producing_seat_ref ~ '^seat:[a-z0-9][a-z0-9.-]*:[a-z0-9][a-z0-9.-]*$'),
  producing_actor_id        uuid not null references public.actor(id),
  -- WHAT THE RUN STOOD ON. candidate_digest is the idempotency subject: one
  -- current outcome per candidate, retries collapse onto it.
  candidate_digest          text not null unique
                              check (candidate_digest ~ '^sha256:[0-9a-f]{64}$'),
  subject_digest            text not null check (subject_digest ~ '^sha256:[0-9a-f]{64}$'),
  policy_digest             text not null check (policy_digest ~ '^sha256:[0-9a-f]{64}$'),
  environment_manifest_digest text not null
                              check (environment_manifest_digest ~ '^sha256:[0-9a-f]{64}$'),
  fixture_set_digest        text not null check (fixture_set_digest ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref              text not null check (evidence_ref ~ '^safe:[a-z0-9][a-z0-9:_./-]*$'),
  -- THE WHOLE RECEIPT, kept, so the digest below is checkable against something
  -- rather than being a number nobody can reproduce.
  receipt                   jsonb not null check (jsonb_typeof(receipt) = 'object'),
  -- RECOMPUTED from `receipt` by the writer, never supplied. The constraint is
  -- the shape; ops.gate_zero_outcome_digest() is the value.
  outcome_digest            text not null check (outcome_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- THE SAME RECEIPT, DIGESTED OVER WHAT THE CANDIDATE DECIDES AND NOTHING THE
  -- CALL DECIDES. outcome_digest above covers all twenty-one fields, which
  -- includes the two instants stamped when a run happened and the per-call
  -- session_ref identity.js derives from the request's correlation id -- three
  -- values that differ between two GENUINE authenticated runs of one candidate.
  -- Keying idempotency on it therefore refused the second real call (PR 1014,
  -- Sol's finding 3). This column is the key: the same receipt with the two
  -- instants dropped and the producer's and evaluator's session_ref dropped, so
  -- two authenticated calls over one candidate converge on one value. The
  -- session refs are not lost -- they are IN `receipt` and in outcome_digest,
  -- beside the row rather than inside the key.
  candidate_scoped_digest   text not null
                              check (candidate_scoped_digest ~ '^sha256:[0-9a-f]{64}$'),
  status                    text not null
                              check (status in ('pass', 'fail', 'unknown', 'stale', 'quarantined')),
  comparator                text not null
                              check (char_length(comparator) between 5 and 300),
  observed_at               timestamptz not null,
  ttl_expires_at            timestamptz not null,
  recorded_at               timestamptz not null default now(),
  -- A receipt cannot expire before it was observed, and a row cannot be
  -- recorded before the instant it claims to have observed. Both are the same
  -- exclusive reading benchmark acceptance already applies to its own ordering.
  constraint gate_zero_outcome_expiry_after_observation
    check (ttl_expires_at > observed_at),
  constraint gate_zero_outcome_recorded_after_observation
    check (recorded_at >= observed_at)
);

comment on table ops.gate_zero_read_only_outcome is
  'The authenticated Gate Zero read-only outcome: one consumer-gate-receipt.v1 per candidate, its digest recomputed from the stored receipt, the instant it was observed, and the independent oracle seat that produced it. Append-only and retryable, every run kept. It grants nothing and starts no clock; it is the value benchmark acceptance binds and the zero the v5 clock is measured from.';

comment on column ops.gate_zero_read_only_outcome.outcome_digest is
  'Recomputed from `receipt` with ops.portfolio_canonical_json, which matches artifact-trust.js canonicalJson. Never supplied by a caller. Plain canonical-JSON sha256 with no domain tag, following benchmark-minimum.v5.js precedent -- a NAMED ASSUMPTION, because r7 states no domain tag for consumer-gate-receipt.v1.';

comment on column ops.gate_zero_read_only_outcome.candidate_scoped_digest is
  'The idempotency comparison value: ops.gate_zero_outcome_candidate_digest(receipt), which is the same canonical-JSON sha256 recipe over the receipt MINUS observed_at, ttl_expires_at and the producer''s and evaluator''s per-call session_ref. Two genuine authenticated runs of one candidate differ in exactly those three values, so this is what a retry is compared on; outcome_digest stays as the evidence digest of the receipt that was actually stored.';

comment on column ops.gate_zero_read_only_outcome.producing_seat_ref is
  'The staffed oracle seat that produced this receipt, derived from the server-established actor context and the frozen holder ref. Never a parameter.';

create index if not exists gate_zero_read_only_outcome_current_idx
  on ops.gate_zero_read_only_outcome (observed_at desc, outcome_digest desc)
  where status = 'pass';

-- APPEND-ONLY, BOTH HALVES. A row-level trigger never sees a whole-table wipe,
-- and that statement event cannot be revoked from the table owner, so the
-- statement-level trigger beside it is the half that binds the owner. Same
-- house pattern as ops/benchmark-acceptance.candidate.sql and the two stores
-- beside it.
create or replace function ops.gate_zero_outcome_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'DoctorCRE v5 Gate Zero outcome rows are append-only: % is refused on ops.%',
    tg_op, tg_table_name using errcode = '42501';
end;
$$;

comment on function ops.gate_zero_outcome_rows_immutable() is
  'Refuses every update, delete and whole-table wipe on the Gate Zero outcome record. Installed twice because a row-level trigger never sees the statement event.';

drop trigger if exists gate_zero_read_only_outcome_append_only
  on ops.gate_zero_read_only_outcome;
create trigger gate_zero_read_only_outcome_append_only
  before update or delete on ops.gate_zero_read_only_outcome
  for each row execute function ops.gate_zero_outcome_rows_immutable();

drop trigger if exists gate_zero_read_only_outcome_append_only_stmt
  on ops.gate_zero_read_only_outcome;
create trigger gate_zero_read_only_outcome_append_only_stmt
  before truncate on ops.gate_zero_read_only_outcome
  for each statement execute function ops.gate_zero_outcome_rows_immutable();

-- ---------------------------------------------------------------------------
-- The digest, recomputed rather than believed.
-- ---------------------------------------------------------------------------
create or replace function ops.gate_zero_outcome_digest(p_receipt jsonb)
returns text language sql immutable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(p_receipt), 'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.gate_zero_outcome_digest(jsonb) is
  'sha256 over the canonical JSON of one consumer-gate-receipt.v1, no domain tag. The same bytes artifact-trust.js digest(receipt) hashes. A NAMED ASSUMPTION about r7''s receipt_payload_digest_rule, which lists no tag for this schema.';

-- ---------------------------------------------------------------------------
-- The SAME digest, over what the CANDIDATE decides. This is the idempotency key.
-- ---------------------------------------------------------------------------
-- THE DEFECT THIS EXISTS FOR (PR 1014, Sol's finding 3). A consumer-gate receipt
-- carries three values that a genuine second run of ONE candidate legitimately
-- changes: `observed_at` and `ttl_expires_at`, stamped when the run happened,
-- and the `session_ref` inside producer_identity and evaluator_identity, which
-- identity.js derives from the request's own correlation id. Digesting all
-- twenty-one fields and then demanding the retry match it made every REAL retry
-- a conflict: the only two calls that could ever agree were two calls carrying
-- the same bytes, which is a fixture, not a retry.
--
-- THE PROJECTION, STATED RATHER THAN IMPLIED. Drop the two instants; drop
-- session_ref from the producer and the evaluator. Nothing else moves:
--
--   * the SUBJECT MAKER's session_ref STAYS. It is
--     `session:candidate-build:<head_revision>` -- derived from the candidate,
--     not from the call -- so it is part of what this key is about, and keeping
--     it means a receipt that renames the maker's session is still a different
--     outcome for the same candidate and still conflicts.
--   * every digest, every constant, the status, the comparator and the
--     evidence ref stay. A run that READ different rows and reached a different
--     verdict for one candidate is a genuine conflict and must still raise.
--
-- WHAT IS NOT LOST. The session refs and the instants are inside `receipt` and
-- inside outcome_digest, which is stored beside this one. This narrows what the
-- IDEMPOTENCY KEY is computed over; it narrows nothing about what is kept.
create or replace function ops.gate_zero_outcome_candidate_projection(p_receipt jsonb)
returns jsonb language sql immutable
set search_path = pg_catalog, ops, public
as $$
  select (p_receipt - 'observed_at' - 'ttl_expires_at')
      || jsonb_build_object(
           'producer_identity', (p_receipt -> 'producer_identity') - 'session_ref',
           'evaluator_identity', (p_receipt -> 'evaluator_identity') - 'session_ref')
$$;

comment on function ops.gate_zero_outcome_candidate_projection(jsonb) is
  'One consumer-gate-receipt.v1 reduced to what the CANDIDATE decides: the receipt without observed_at, without ttl_expires_at, and with the per-call session_ref removed from producer_identity and evaluator_identity. The subject maker''s session_ref is derived from the candidate revision and is deliberately kept. Used only to compute the idempotency comparison value.';

create or replace function ops.gate_zero_outcome_candidate_digest(p_receipt jsonb)
returns text language sql immutable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(ops.gate_zero_outcome_candidate_projection(p_receipt)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.gate_zero_outcome_candidate_digest(jsonb) is
  'The value a Gate Zero retry is compared on: the same canonical-JSON sha256 recipe as ops.gate_zero_outcome_digest, over ops.gate_zero_outcome_candidate_projection of the receipt. Two genuine authenticated runs of one candidate produce the same value; a run that read different rows or reached a different verdict does not.';

-- ---------------------------------------------------------------------------
-- The one writer.
-- ---------------------------------------------------------------------------
-- TWO PARAMETERS, AND NEITHER OF THEM IS AUTHORITY. The receipt is the subject;
-- the idempotency key is the caller's name for one intended act. Everything
-- that decides whether this row may exist -- the seat, the actor, the digest --
-- is derived here.
--
-- THE THREE IDENTITIES ARE CHECKED, NOT COPIED. r7's identity rule requires the
-- subject maker to differ from the evaluator in BOTH actor and session, and
-- requires the producer and evaluator to be this seat. The receipt carries all
-- three as authenticated-receipt-identity.v1 objects; this function refuses a
-- producer or evaluator whose actor_id is not the derived seat lane, and
-- refuses a subject maker who is. That is why the seat was staffed with the
-- lane that reviewed every Gate Zero pull request and built none of them.
create or replace function ops.gate_zero_record_read_only_outcome(
  p_idempotency_key uuid,
  p_receipt jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor uuid; v_slug text; v_seat text; v_id uuid;
  v_existing ops.gate_zero_read_only_outcome%rowtype;
  v_digest text; v_field text; v_keys integer; v_identity jsonb; v_identity_keys integer;
begin
  if p_idempotency_key is null then
    raise exception 'recording a Gate Zero read-only outcome requires an idempotency key';
  end if;
  if p_receipt is null or jsonb_typeof(p_receipt) <> 'object' then
    raise exception 'recording a Gate Zero read-only outcome requires one consumer-gate-receipt.v1 object';
  end if;

  -- AUTHORITY FIRST, so nothing below it can be reached by a transaction that
  -- may not write here at all.
  v_actor := ops.gate_zero_producer_actor_id();
  v_seat := ops.gate_zero_producer_seat_holder_ref();
  v_slug := split_part(v_seat, ':', 2);

  -- CLOSED SCHEMA. consumer-gate-receipt.v1 sets additional_properties false and
  -- names twenty-one required fields. An unknown field denies, which is r7's own
  -- rule ("Unknown or missing payload fields deny") and is what makes the digest
  -- a statement about a known shape.
  foreach v_field in array array[
    'gate_id', 'receipt_producer_step_ref', 'subject_digest', 'candidate_digest',
    'policy_digest', 'environment_manifest_digest', 'subject_environment', 'evidence_scope',
    'subject_maker_identity', 'producer_identity', 'evaluator_identity', 'producer_role',
    'independent_oracle_ref', 'oracle_version', 'evidence_ref', 'fixture_set_digest',
    'observed_at', 'ttl_expires_at', 'status', 'comparator', 'negative_admission_result'
  ] loop
    if not (p_receipt ? v_field) then
      raise exception 'the Gate Zero receipt is missing the required consumer-gate-receipt.v1 field %', v_field;
    end if;
  end loop;
  select count(*) into v_keys from jsonb_object_keys(p_receipt);
  if v_keys <> 21 then
    raise exception 'consumer-gate-receipt.v1 is a closed schema: the Gate Zero receipt carries % fields rather than 21', v_keys;
  end if;

  -- THE THREE IDENTITIES, AND r7's SHAPE FOR THEM IS CLOSED TOO.
  -- `authenticated-receipt-identity.v1` sets additional_properties false, names
  -- exactly actor_id, session_ref and authority_class, and gives session_ref a
  -- LOWERCASE pattern with a minimum length. All three clauses are checked here,
  -- and all three are checked again in the gateway: a fourth key inside an
  -- identity object would otherwise travel into the digest unread, which would
  -- make the digest a statement about an open shape.
  --
  -- WHAT THIS COPY CANNOT CHECK, said plainly rather than implied: the record
  -- layer does not know which correlation id the gateway derived for this call,
  -- so "these identities are THIS call's" is the gateway's clause alone
  -- (gate-zero-outcome-store.v5.js, against identity.js's authenticated call).
  -- What is checkable here is the SHAPE, the seat and the self-review rule, and
  -- those are checked here because a writer connection opened outside the
  -- gateway reaches this function and not that one.
  foreach v_field in array array['subject_maker_identity', 'producer_identity', 'evaluator_identity'] loop
    v_identity := p_receipt -> v_field;
    if jsonb_typeof(v_identity) <> 'object' then
      raise exception 'the Gate Zero receipt field % is not an authenticated-receipt-identity.v1 object', v_field;
    end if;
    select count(*) into v_identity_keys from jsonb_object_keys(v_identity);
    if v_identity_keys <> 3
       or not (v_identity ? 'actor_id')
       or not (v_identity ? 'session_ref')
       or not (v_identity ? 'authority_class') then
      raise exception 'authenticated-receipt-identity.v1 is a closed schema: the Gate Zero receipt field % carries % fields rather than exactly actor_id, session_ref and authority_class',
        v_field, v_identity_keys;
    end if;
    if coalesce(v_identity ->> 'actor_id', '') = ''
       or coalesce(v_identity ->> 'authority_class', '') = '' then
      raise exception 'the Gate Zero receipt field % has an empty actor_id or authority_class', v_field;
    end if;
    -- r7's own pattern, character for character. Lowercase only, and at least
    -- nine characters after `session:`.
    if coalesce(v_identity ->> 'session_ref', '') !~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$' then
      raise exception 'the Gate Zero receipt field % has a session_ref that is not r7''s authenticated-receipt-identity.v1 pattern: %',
        v_field, coalesce(v_identity ->> 'session_ref', '');
    end if;
  end loop;
  if p_receipt -> 'producer_identity' ->> 'actor_id' <> v_slug
     or p_receipt -> 'evaluator_identity' ->> 'actor_id' <> v_slug then
    raise exception 'the Gate Zero receipt names a producer or evaluator other than the staffed seat lane %', v_slug;
  end if;
  if p_receipt -> 'producer_identity' ->> 'authority_class' <> 'review_agent'
     or p_receipt -> 'evaluator_identity' ->> 'authority_class' <> 'review_agent' then
    raise exception 'the Gate Zero receipt names a producer or evaluator authority class other than review_agent';
  end if;
  -- SAME-ACTOR SELF-REVIEW DENIES, in both dimensions r7 names.
  if p_receipt -> 'subject_maker_identity' ->> 'actor_id' = v_slug then
    raise exception 'the Gate Zero receipt names the oracle seat as the maker of its own subject; r7 requires the subject maker to differ from the evaluator';
  end if;
  if p_receipt -> 'subject_maker_identity' ->> 'session_ref'
     = p_receipt -> 'evaluator_identity' ->> 'session_ref' then
    raise exception 'the Gate Zero receipt names one session as both subject maker and evaluator';
  end if;

  -- IDEMPOTENT ON THE CANDIDATE, ATOMICALLY, and the atomicity is the whole
  -- point of the shape (2026-09-13, PR 1014 correction).
  --
  -- THE DEFECT THIS REPLACES. The first draft looked the candidate up, found
  -- nothing, and then inserted. Two runs of the same candidate arriving at once
  -- both missed the lookup, and the loser of the race got a bare
  -- unique_violation on candidate_digest instead of the durable row -- a retry
  -- policy that says "every run kept, a retry collapses onto the row that
  -- exists" turning into an error whenever two writers actually retried at once.
  --
  -- THE SHAPE THAT CANNOT RACE. ONE statement does the insert with the candidate
  -- key as its arbiter, so the conflict is resolved by the index rather than by
  -- a window between two statements: a concurrent inserter BLOCKS on the
  -- speculative insertion, and when the first committer commits the second takes
  -- the DO NOTHING branch and reads the committed row in the fallback select --
  -- which sees it, because each statement in READ COMMITTED takes a fresh
  -- snapshot. If the first transaction rolls back instead, the second inserts.
  -- Either way both callers receive the same durable row and neither receives an
  -- error.
  --
  -- AND A DIFFERENT RECEIPT FOR A RECORDED CANDIDATE IS STILL A CONFLICT, not a
  -- silent replace: the fallback select compares the recorded digest with the
  -- one offered and raises when they differ. The table is append-only and the
  -- first digest may already be bound by an acceptance.
  insert into ops.gate_zero_read_only_outcome (
    idempotency_key, step_ref, receipt_producer_step_ref, gate_id, receipt_schema,
    producer_role, independent_oracle_ref, oracle_version, evidence_scope,
    subject_environment, negative_admission_result, producing_seat_ref, producing_actor_id,
    candidate_digest, subject_digest, policy_digest, environment_manifest_digest,
    fixture_set_digest, evidence_ref, receipt, outcome_digest, candidate_scoped_digest,
    status, comparator, observed_at, ttl_expires_at)
  values (
    p_idempotency_key,
    'step:gate-zero-read-only-outcome',
    p_receipt ->> 'receipt_producer_step_ref',
    p_receipt ->> 'gate_id',
    'consumer-gate-receipt.v1',
    p_receipt ->> 'producer_role',
    p_receipt ->> 'independent_oracle_ref',
    p_receipt ->> 'oracle_version',
    p_receipt ->> 'evidence_scope',
    p_receipt ->> 'subject_environment',
    p_receipt ->> 'negative_admission_result',
    v_seat,
    v_actor,
    p_receipt ->> 'candidate_digest',
    p_receipt ->> 'subject_digest',
    p_receipt ->> 'policy_digest',
    p_receipt ->> 'environment_manifest_digest',
    p_receipt ->> 'fixture_set_digest',
    p_receipt ->> 'evidence_ref',
    p_receipt,
    ops.gate_zero_outcome_digest(p_receipt),
    ops.gate_zero_outcome_candidate_digest(p_receipt),
    p_receipt ->> 'status',
    p_receipt ->> 'comparator',
    (p_receipt ->> 'observed_at')::timestamptz,
    (p_receipt ->> 'ttl_expires_at')::timestamptz)
  on conflict (candidate_digest) do nothing
  returning id into v_id;
  if v_id is not null then
    return v_id;
  end if;

  -- THE FALLBACK, reached only when the arbiter index already held this
  -- candidate. It is a separate statement and therefore a fresh snapshot, which
  -- is what lets it see a row a concurrent transaction committed while this
  -- insert was blocked on it.
  select * into v_existing from ops.gate_zero_read_only_outcome
   where candidate_digest = p_receipt ->> 'candidate_digest';
  if not found then
    -- Neither inserted nor found: the only way here is the row having been
    -- removed between the two statements, which the append-only triggers refuse.
    -- It is reported rather than retried, because a writer that cannot explain
    -- its own outcome must not invent one.
    raise exception 'the Gate Zero outcome for candidate % was neither inserted nor found; the record layer is in a state this writer cannot account for',
      p_receipt ->> 'candidate_digest';
  end if;
  -- COMPARED ON THE CANDIDATE-SCOPED DIGEST, NOT THE FULL ONE (PR 1014,
  -- second correction). The full receipt digest carries this call's session_ref
  -- and this call's two instants, so comparing it made every genuine second
  -- authenticated run of one candidate a conflict -- the defect Sol's finding 3
  -- named. What is compared is what the CANDIDATE decides; what is KEPT is
  -- still the whole receipt and its full digest, in the row beside this check.
  v_digest := ops.gate_zero_outcome_candidate_digest(p_receipt);
  if v_existing.candidate_scoped_digest <> v_digest then
    raise exception 'a different Gate Zero outcome is already recorded for candidate %: recorded %, offered %',
      v_existing.candidate_digest, v_existing.candidate_scoped_digest, v_digest;
  end if;
  return v_existing.id;
end;
$$;

comment on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) is
  'The only way to record a Gate Zero read-only outcome. The producing seat, the actor and the outcome digest are all derived; the receipt and an idempotency key are the only parameters. Refuses every transaction except the staffed non-human oracle seat, refuses a receipt whose producer or evaluator is not that seat, refuses same-actor or same-session self-review, enforces authenticated-receipt-identity.v1''s closed three-field shape on each of the three identities, and is idempotent on the candidate digest ATOMICALLY -- one insert arbitrated by the candidate key, with a fallback select -- so two writers racing the same candidate both receive the same durable row rather than one of them receiving a unique_violation. A retry is compared on ops.gate_zero_outcome_candidate_digest, which excludes the two per-run instants and the per-call session_ref, so two GENUINE authenticated calls for one candidate converge; the full outcome_digest is stored beside it as the evidence digest of the receipt that was actually persisted.';

-- ---------------------------------------------------------------------------
-- PREREQUISITE TWO, NOW BOUND. The Gate Zero reader benchmark acceptance calls.
-- ---------------------------------------------------------------------------
-- This REPLACES the fail-closed stub that ops/benchmark-acceptance.candidate.sql
-- has carried since V5-A00. That file is updated in the same change; this is the
-- definition that binds in a database carrying this migration.
--
-- WHICH ROW IS CURRENT, stated as a procedure rather than left to a reader:
--   1. status must be 'pass'. A fail, unknown, stale or quarantined outcome is
--      a recorded run, not a binding, and truthful failure propagation is the
--      whole point of Q036.D1's injected-failure clause.
--   2. the expiry must not have passed. An expired receipt is a receipt about a
--      world that has moved.
--   3. of what remains, the LATEST observed_at wins, and ties break on
--      outcome_digest descending -- a total order, because now() is constant
--      inside a transaction and two runs can share an instant.
--   4. none left -> raise, with the same fail-closed posture the stub had.
-- The refusal text distinguishes "no outcome has ever been recorded" from
-- "every recorded outcome is expired or non-passing", because those are
-- different problems for whoever hits them.
--
-- PRIVATE, exactly as the stub was: granted to no role, reachable only from the
-- definer acceptance path that runs as the owner.
create or replace function ops.benchmark_gate_zero_outcome()
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_row ops.gate_zero_read_only_outcome%rowtype; v_any boolean;
begin
  select * into v_row from ops.gate_zero_read_only_outcome
   where status = 'pass' and ttl_expires_at > now()
   order by observed_at desc, outcome_digest collate "C" desc
   limit 1;
  if not found then
    select exists (select 1 from ops.gate_zero_read_only_outcome) into v_any;
    if v_any then
      raise exception 'benchmark acceptance requires a current passing Gate Zero read-only outcome; every outcome recorded here is non-passing or past its expiry. No caller-supplied, configured or synthetic Gate Zero outcome is accepted.';
    end if;
    raise exception 'benchmark acceptance requires an authenticated Gate Zero read-only outcome binding, and none has been recorded here yet. The record exists (ops.gate_zero_read_only_outcome) and the independent oracle seat writes it; until it does, acceptance fails closed. No caller-supplied, configured or synthetic Gate Zero outcome is accepted.';
  end if;
  -- THE CLOSED THREE-FIELD OBJECT the whole foundation join hangs on
  -- (benchmark-minimum.v5.js:435, :1449-1453). Nothing else is returned: a
  -- fourth field here is a value some future reader consumes as something it
  -- is not.
  return jsonb_build_object(
    'step_ref', v_row.step_ref,
    'outcome_digest', v_row.outcome_digest,
    'observed_at', to_char(v_row.observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
$$;

comment on function ops.benchmark_gate_zero_outcome() is
  'PRIVATE reader for the current Gate Zero read-only outcome: the latest passing, unexpired row in ops.gate_zero_read_only_outcome, as the closed { step_ref, outcome_digest, observed_at }. Raises when there is none, which is the same fail-closed posture the pre-0502 stub had. Granted to no role; reachable only from the definer acceptance path.';

-- ---------------------------------------------------------------------------
-- Grants. Reads reach the ordinary bundles; DIRECT INSERT IS GRANTED TO NOBODY.
-- No role is created here.
-- ---------------------------------------------------------------------------
grant select on ops.gate_zero_read_only_outcome to carr_reader, carr_writer, carr_authority;
-- THE GRANT HALF OF APPEND-ONLY. It does not bind the owner -- the whole-table
-- wipe privilege cannot be revoked from it -- which is what the statement-level
-- trigger above is for. Both halves are kept.
revoke insert, update, delete, truncate on ops.gate_zero_read_only_outcome
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.gate_zero_producer_seat_holder_ref(),
  ops.gate_zero_outcome_digest(jsonb),
  ops.gate_zero_outcome_candidate_projection(jsonb),
  ops.gate_zero_outcome_candidate_digest(jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.gate_zero_producer_seat_holder_ref(),
  ops.gate_zero_outcome_digest(jsonb),
  ops.gate_zero_outcome_candidate_projection(jsonb),
  ops.gate_zero_outcome_candidate_digest(jsonb)
  to carr_reader, carr_writer, carr_jobs, carr_authority;

-- THE AUTHORITY TEST AND THE PRIVATE READER ARE GRANTED TO NOBODY. Exposing
-- either would turn a boundary into a callable question: the first would let a
-- caller enumerate whether it holds the seat, and the second would make "what
-- is the current Gate Zero outcome" answerable outside an acceptance. The
-- definer writer reaches both as the function owner, which is the only access
-- they need.
revoke all on function ops.gate_zero_producer_actor_id(),
  ops.benchmark_gate_zero_outcome()
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- THE WRITER REACHES carr_writer ONLY. Not carr_authority: this row is
-- deliberately NOT a partner act, and granting it to the authority bundle would
-- put an oracle's signature within reach of the connection a partner's own acts
-- run on. Not carr_jobs: an unattended schedule does not hold this seat.
revoke all on function ops.gate_zero_record_read_only_outcome(uuid,jsonb)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) to carr_writer;
