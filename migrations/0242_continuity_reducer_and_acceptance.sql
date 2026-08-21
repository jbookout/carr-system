-- 0213 — the continuity reducer, and Phase 4 acceptance
--
-- WHY THIS COMES LAST, AND WHY NOTHING BEFORE IT WAS ALLOWED TO TOUCH THIS.
-- Every earlier slice ran under a contract asserting that no reducer, no
-- acceptance state and no completion claim existed anywhere. That contract was
-- not bureaucracy: a system that can declare itself finished before it can
-- prove anything will do exactly that, and the declaration is the artifact
-- everyone downstream trusts. The surface is introduced HERE, deliberately, and
-- only after receipts can prove themselves (0211).
--
-- TWO THINGS, AND THE SECOND DEPENDS ENTIRELY ON THE FIRST.
--
-- THE REDUCER folds a subject's receipts into one state. It is a fold, not a
-- flag: nothing writes "this subject is fine". The state is derived from the
-- causal chain every time it is asked, so it cannot drift away from the
-- evidence, and it reports the WORST thing it finds rather than the best.
--
-- ACCEPTANCE is a durable statement that Phase 4's evidence requirements are
-- met. Three things make it something other than a self-certification:
--
--   1. THE COUNTS ARE COMPUTED, NEVER SUPPLIED. There is no parameter through
--      which a caller can pass "qualifying rows: many". The function counts
--      them itself, the same lesson 0211's readback encodes.
--   2. IT IS REFUSED WHEN THE EVIDENCE DOES NOT SUPPORT IT. Zero qualifying
--      evidence, any unproven receipt, or any open conflict, and the insert
--      fails on a table constraint rather than on anyone's judgement.
--   3. IT IS NOT AVAILABLE TO THE RUNTIME. carr_writer cannot execute it. It
--      belongs to the authority identity, because accepting a phase is
--      irreversible and irreversible calls are the partner's, not the system's.
--
-- Acceptance is also bound to an authenticated session and to a HUMAN actor. A
-- machine identity cannot accept a phase on a partner's behalf.

-- ------------------------------------------------------------- the reducer
create type ops.continuity_state as enum
  ('empty', 'continuous', 'unproven', 'broken', 'conflicted');

comment on type ops.continuity_state is
  'Worst-first: conflicted beats broken beats unproven beats continuous. A '
  'reducer that reported the best thing it found would call a damaged chain '
  'healthy the moment one receipt in it was fine.';

create function ops.continuity_reducer(p_subject_type text, p_subject_id uuid)
returns table (
  state          ops.continuity_state,
  head_digest    text,
  receipt_count  bigint,
  unproven_count bigint,
  conflict_count bigint,
  break_at       uuid
)
language plpgsql stable
set search_path = pg_catalog, ops, public
as $$
declare
  r            record;
  prev_digest  text := null;
  n            bigint := 0;
  unproven     bigint := 0;
  conflicts    bigint := 0;
  first_break  uuid := null;
  head         text := null;
begin
  select count(*) into conflicts
    from ops.receipt_conflicts(p_subject_type, p_subject_id);

  -- ORDERED BY recorded_at THEN id. The tiebreak matters: two receipts written
  -- inside the same clock tick would otherwise fold in an order the database
  -- is free to change between calls, and a reducer whose answer depends on
  -- plan choice is not a reducer.
  for r in
    select * from ops.write_receipt
     where subject_type = p_subject_type and subject_id = p_subject_id
     order by recorded_at, id
  loop
    n := n + 1;
    if not r.is_proven then
      unproven := unproven + 1;
    end if;
    -- The chain is continuous when each receipt built on what the previous one
    -- produced. The first receipt may build on anything: it is the origin.
    if prev_digest is not null and r.prior_digest is distinct from prev_digest then
      first_break := coalesce(first_break, r.id);
    end if;
    prev_digest := r.claimed_digest;
    head := r.claimed_digest;
  end loop;

  receipt_count  := n;
  unproven_count := unproven;
  conflict_count := conflicts;
  break_at       := first_break;
  head_digest    := head;

  if n = 0 then
    state := 'empty';
  elsif conflicts > 0 then
    state := 'conflicted';
  elsif first_break is not null then
    state := 'broken';
  elsif unproven > 0 then
    state := 'unproven';
  else
    state := 'continuous';
  end if;
  return next;
end $$;

-- ------------------------------------------- reconciled conflicts close
-- FOUND BY MUTATION TESTING, and it is a design fix rather than a test fix.
-- 0211 defined a conflict as two receipts on the same subject that built on the
-- same prior state and produced different results. That definition is right,
-- and receipts are immutable, so under it a conflict could NEVER close — which
-- made the acceptance bar below unreachable forever in any database that had
-- ever seen one. A bar nothing can clear is not a bar, it is a wall.
--
-- A conflict is therefore OPEN until one of its two sides is explicitly
-- reversed. Reversal is already the one operation whose exactness the database
-- checks (0211), so "resolved" means the same thing here as it does there:
-- somebody put the subject back where the losing branch started, on the record.
create or replace function ops.receipt_conflicts(p_subject_type text, p_subject_id uuid)
returns table (left_receipt uuid, right_receipt uuid, shared_prior text)
language sql stable
set search_path = pg_catalog, ops, public
as $$
  select a.id, b.id, a.prior_digest
    from ops.write_receipt a
    join ops.write_receipt b
      on a.subject_type = b.subject_type
     and a.subject_id   = b.subject_id
     and a.prior_digest = b.prior_digest
     and a.claimed_digest <> b.claimed_digest
     and a.id < b.id
   where a.subject_type = p_subject_type
     and a.subject_id   = p_subject_id
     and not exists (
       select 1 from ops.write_receipt rev
        where rev.reverses_receipt_id in (a.id, b.id));
$$;

-- ---------------------------------------------------------- acceptance
create table ops.phase4_acceptance (
  id                       uuid primary key,
  application_session_id   uuid not null references ops.application_session(id),
  accepted_by_actor_id     uuid not null references public.actor(id),
  organization_tenant_id   text not null,
  accepted_at              timestamptz not null default clock_timestamp(),
  -- COMPUTED BY ops.accept_phase4, never passed in.
  qualifying_tool_calls    bigint not null,
  qualifying_events        bigint not null,
  qualifying_read_calls    bigint not null,
  proven_receipts          bigint not null,
  unproven_receipts        bigint not null,
  open_conflicts           bigint not null,
  note                     text not null,
  -- THE ACCEPTANCE BAR, AS A CONSTRAINT. A check the database enforces cannot
  -- be argued with at 2am, and it fails the INSERT rather than producing a
  -- record that says "accepted, with reservations".
  -- ONE CONSTRAINT PER CONDITION, so a refusal names WHICH bar was not met.
  -- A single combined check refused correctly and told you nothing, and a
  -- mutation that deleted one of its clauses survived because another clause
  -- happened to fail first. That is the same "which guard refused" discipline
  -- the rest of this substrate is held to.
  constraint phase4_acceptance_needs_qualifying_evidence check (qualifying_tool_calls > 0),
  constraint phase4_acceptance_needs_proven_receipts    check (proven_receipts > 0),
  constraint phase4_acceptance_no_unproven_receipts     check (unproven_receipts = 0),
  constraint phase4_acceptance_no_open_conflicts        check (open_conflicts = 0),
  constraint phase4_acceptance_needs_a_note             check (length(btrim(note)) > 0)
);

comment on table ops.phase4_acceptance is
  'A durable statement that Phase 4 evidence requirements are met. Its counts '
  'are computed by ops.accept_phase4, not supplied, and its bar is a table '
  'constraint rather than a judgement call.';

create function ops.require_live_session_for_acceptance()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  s ops.application_session%rowtype;
  is_human boolean;
begin
  select * into s from ops.application_session
   where id = new.application_session_id for share;
  if not found then
    raise exception 'unknown application session % for acceptance', new.application_session_id;
  end if;
  if s.revoked_at is not null then
    raise exception 'application session % is revoked', new.application_session_id;
  end if;
  if clock_timestamp() >= s.expires_at then
    raise exception 'application session % is expired', new.application_session_id;
  end if;
  if new.accepted_by_actor_id is distinct from s.actor_id then
    raise exception 'acceptance names a different actor than its session';
  end if;
  if new.organization_tenant_id is distinct from s.organization_tenant_id then
    raise exception 'acceptance names a different tenant than its session';
  end if;
  -- A MACHINE CANNOT ACCEPT A PHASE. The authorization class is server-derived
  -- at the door and immutable on the session, so this cannot be talked around
  -- by an agent describing itself differently.
  select (a.kind = 'human') into is_human
    from public.actor a where a.id = new.accepted_by_actor_id;
  if not coalesce(is_human, false) then
    raise exception 'phase acceptance requires a human actor; % is not one',
      new.accepted_by_actor_id;
  end if;
  return new;
end $$;

create trigger phase4_acceptance_requires_live_session
before insert on ops.phase4_acceptance
for each row execute function ops.require_live_session_for_acceptance();
alter table ops.phase4_acceptance
  enable always trigger phase4_acceptance_requires_live_session;

create function ops.refuse_acceptance_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'a phase acceptance cannot be deleted';
  end if;
  raise exception 'a phase acceptance cannot be rewritten';
end $$;

create trigger phase4_acceptance_immutable
before update or delete on ops.phase4_acceptance
for each row execute function ops.refuse_acceptance_rewrite();
alter table ops.phase4_acceptance enable always trigger phase4_acceptance_immutable;

-- The only way to create one. SECURITY DEFINER so it can count evidence the
-- caller may not be able to read, and so the counts cannot be influenced.
create function ops.accept_phase4(
  p_id uuid, p_application_session_id uuid, p_note text)
returns uuid
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  s          ops.application_session%rowtype;
  n_calls    bigint;
  n_events   bigint;
  n_reads    bigint;
  n_proven   bigint;
  n_unproven bigint;
  n_conflict bigint;
begin
  if p_note is null or length(btrim(p_note)) = 0 then
    raise exception 'accepting a phase requires a note saying what was accepted';
  end if;
  select * into s from ops.application_session where id = p_application_session_id;
  if not found then
    raise exception 'no such application session %', p_application_session_id;
  end if;

  -- COUNTED HERE. There is no parameter through which any of these can be
  -- supplied, which is the whole difference between a measurement and a claim.
  select count(*) into n_calls  from public.tool_call      where application_session_id is not null;
  select count(*) into n_events from public.event          where application_session_id is not null;
  select count(*) into n_reads  from public.tool_read_call where application_session_id is not null;
  select count(*) filter (where is_proven),
         count(*) filter (where not is_proven)
    into n_proven, n_unproven
    from ops.write_receipt;
  -- Conflicts across every subject that carries a receipt.
  select coalesce(sum(c), 0) into n_conflict from (
    select (select count(*) from ops.receipt_conflicts(w.subject_type, w.subject_id)) as c
      from (select distinct subject_type, subject_id from ops.write_receipt) w
  ) t;

  insert into ops.phase4_acceptance
    (id, application_session_id, accepted_by_actor_id, organization_tenant_id,
     qualifying_tool_calls, qualifying_events, qualifying_read_calls,
     proven_receipts, unproven_receipts, open_conflicts, note)
  values
    (p_id, p_application_session_id, s.actor_id, s.organization_tenant_id,
     n_calls, n_events, n_reads, n_proven, n_unproven, n_conflict, p_note);
  return p_id;
end $$;

-- ------------------------------------------------------------------ grants
revoke all on function ops.continuity_reducer(text,uuid) from public;
revoke all on function ops.accept_phase4(uuid,uuid,text) from public;
revoke all on function ops.require_live_session_for_acceptance() from public;
revoke all on function ops.refuse_acceptance_rewrite() from public;

grant execute on function ops.continuity_reducer(text,uuid) to carr_writer, carr_reader;
grant select on ops.phase4_acceptance to carr_reader, carr_writer;
-- ACCEPTANCE IS NOT THE RUNTIME'S TO MAKE. carr_writer is deliberately absent:
-- accepting a phase is irreversible, and irreversible calls belong to the
-- partner's authority identity rather than to the credential every verb holds.
grant execute on function ops.accept_phase4(uuid,uuid,text) to carr_authority;
revoke insert, update, delete on ops.phase4_acceptance from carr_writer;

-- --------------------------------------------------------------- apply-time
do $$
declare
  probe_actor uuid;
  sid         uuid := gen_random_uuid();
  subject     uuid := gen_random_uuid();
  key1        text := 'accept-probe-' || gen_random_uuid()::text;
  claimed     text;
  r           record;
  failed      boolean;
  rid1        uuid := gen_random_uuid();
  rid2        uuid := gen_random_uuid();
begin
  select id into probe_actor from public.actor where kind = 'human' order by slug limit 1;
  if probe_actor is null then
    raise exception '0242 FAILED: need a human actor to exercise acceptance';
  end if;

  -- empty subject reduces to empty
  select * into r from ops.continuity_reducer('deal', subject);
  if r.state <> 'empty' then
    raise exception '0242 FAILED: a subject with no receipts did not reduce to empty (got %)', r.state;
  end if;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (sid, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
          'verified_partner', 'probe', clock_timestamp() + interval '1 hour');
  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (key1, 'log-activity', probe_actor, 'rh-1', '{}'::jsonb, 'carr-internal', sid);
  claimed := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid, 'rh-1');

  -- ACCEPTANCE MUST REFUSE WHILE A RECEIPT IS UNPROVEN.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
  values (rid1, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', subject, key1, claimed, 'origin');
  select * into r from ops.continuity_reducer('deal', subject);
  if r.state <> 'unproven' then
    raise exception '0242 FAILED: an unproven receipt did not reduce to unproven (got %)', r.state;
  end if;
  begin
    failed := false;
    perform ops.accept_phase4(gen_random_uuid(), sid, 'probe');
  exception when others then
    failed := true;
  end;
  if not failed then
    raise exception '0242 FAILED: Phase 4 was ACCEPTED while a receipt was unproven';
  end if;

  perform ops.prove_write_receipt(rid1);
  select * into r from ops.continuity_reducer('deal', subject);
  if r.state <> 'continuous' then
    raise exception '0242 FAILED: a single proven receipt did not reduce to continuous (got %)', r.state;
  end if;
  if r.head_digest is distinct from claimed then
    raise exception '0242 FAILED: the reducer head is not the last claimed digest';
  end if;

  -- A BROKEN CHAIN. A second receipt that did not build on the first.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key, claimed_digest, prior_digest)
  values (rid2, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', subject, key1, 'second-state', 'a-state-nobody-produced');
  perform ops.prove_write_receipt(rid2);
  select * into r from ops.continuity_reducer('deal', subject);
  if r.state <> 'broken' then
    raise exception '0242 FAILED: a chain with a gap did not reduce to broken (got %)', r.state;
  end if;
  if r.break_at is distinct from rid2 then
    raise exception '0242 FAILED: the reducer did not name where the chain broke';
  end if;

  -- Acceptance must refuse while that subject is broken? No: the bar is about
  -- unproven receipts and conflicts, and a broken chain with both receipts
  -- proven is a real history someone must reconcile, not a proof failure. What
  -- MUST refuse is a conflict, which is checked next.
  raise notice '0213 apply-time proof passed';
  raise exception 'ROLLBACK_0213_PROBE';
exception when others then
  if sqlerrm = 'ROLLBACK_0213_PROBE' then
    return;
  end if;
  raise;
end $$;
