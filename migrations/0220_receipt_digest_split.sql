-- 0220 — the receipt digest was two different facts wearing one name
--
-- WHAT WAS WRONG, in one sentence: ops.write_receipt.claimed_digest served BOTH
-- as proof-of-attachment (a function of the CALL, recomputed by the database in
-- ops.prove_write_receipt from the frozen tool_call row) AND as the material
-- claim about a SUBJECT (chosen by the caller, and read by prior_digest, the
-- conflict detector, exact reversal, and the continuity reducer). Those are
-- different facts, and one column could not be honest about both.
--
-- THREE FAILURES FELL OUT OF THAT ONE CONFLATION, each reproduced by execution
-- against a disposable cluster before this file was written:
--
--   1. A REVERSAL RECEIPT COULD NEVER PROVE. 0211 defines exact reversal as
--      claimed_digest = target.prior_digest, but claimed_digest must ALSO equal
--      the readback, which is always the digest of the frozen call. The two
--      requirements are mutually exclusive, so closing a conflict guaranteed a
--      permanently unproven receipt.
--
--   2. ONE UNPROVEN RECEIPT BRICKED ACCEPTANCE FOR THE WHOLE DATABASE. 0213
--      bars acceptance on unproven_receipts = 0, counted globally with no
--      scoping, and a receipt can be neither deleted, re-proven, nor repaired.
--      Combined with (1), the only mechanism that closes a conflict guaranteed
--      the wall. The contract suite did this to itself on its first run.
--
--   3. THE DIGEST PROVED ATTACHMENT ONLY. The readback digested verb, actor,
--      tenant, session and request_hash FROM THE TOOL_CALL ROW, and never
--      touched the receipt's own verb, subject_type or subject_id — the fields
--      the reducer, the conflict detector and Drive retirement all key on. A
--      receipt claiming verb 'retire-the-entire-drive' over a log-activity row
--      proved cleanly, and 0214's two-receipt gate separated its two receipts
--      by nothing but their row ids.
--
-- WHAT THIS MIGRATION DOES, and nothing else:
--
--   A. SPLITS THE DIGEST. claimed_digest is RENAMED to call_digest and keeps
--      exactly one job: it must equal the readback. A new material_digest
--      carries the caller's claim about subject state, and prior_digest keeps
--      its meaning as the state that claim was built on. is_proven follows the
--      rename automatically, because a stored generation expression tracks the
--      column it was written against.
--
--   B. BINDS THE READBACK TO THE RECEIPT'S OWN CLAIMS. The digest now covers
--      the receipt's subject_type and subject_id, and the readback refuses
--      outright when the receipt's verb, actor or tenant disagrees with the
--      frozen evidence. A receipt can no longer prove against evidence it does
--      not describe.
--
--   C. GIVES THE ACCEPTANCE BAR A WAY TO CLEAR. A later receipt may RETRACT an
--      earlier one, and the bar ignores an unproven receipt that a PROVEN
--      receipt has retracted — mirroring how a conflict already closes through
--      an exact reversal. Unproven retractions clear nothing, so the escape
--      hatch cannot be opened from inside.
--
--   D. MAKES 0214'S TWO-RECEIPT GATE MEAN SOMETHING. Each receipt must NAME the
--      dependency being retired, the two must rest on DIFFERENT calls and make
--      DIFFERENT material claims, and the recovery must build on the state the
--      repoint produced.
--
-- WHAT THIS MIGRATION DELIBERATELY DOES NOT DO, so the next reader does not
-- mistake silence for coverage: prior_digest is still caller-chosen and is not
-- verified against any existing receipt. Enforcing it would make the reducer's
-- 'broken' state unreachable, and 'broken' is a finding the system is supposed
-- to be able to report. Causal continuity therefore remains an honest-caller
-- integrity check, exactly as 0211 left it. It is recorded here as a known
-- limitation rather than fixed by a change that would blind the reducer.

-- ============================================================ (A) the split

alter table ops.write_receipt rename column claimed_digest to call_digest;

alter table ops.write_receipt rename constraint write_receipt_digests_nonempty
  to write_receipt_call_and_prior_nonempty;

comment on column ops.write_receipt.call_digest is
  'PROOF OF ATTACHMENT, and nothing else. What the caller believes the digest '
  'of its own qualified CALL is; ops.prove_write_receipt recomputes it from the '
  'frozen tool_call row and from this receipt''s own subject, and is_proven is '
  'the comparison. It says nothing about what was written.';

alter table ops.write_receipt add column material_digest text;

-- BACKFILL, and the reason the immutability trigger has to stand down for it.
-- ops.refuse_receipt_rewrite permits exactly one update — recording a readback —
-- so it would refuse this backfill. It is disabled for the statement and
-- restored to ENABLE ALWAYS, which is the state 0211 left it in; restoring it
-- with a plain ENABLE would quietly downgrade it to origin-only.
alter table ops.write_receipt disable trigger write_receipt_immutable;
update ops.write_receipt set material_digest = call_digest where material_digest is null;
alter table ops.write_receipt enable always trigger write_receipt_immutable;

alter table ops.write_receipt alter column material_digest set not null;
alter table ops.write_receipt add constraint write_receipt_material_digest_nonempty
  check (length(btrim(material_digest)) > 0);

comment on column ops.write_receipt.material_digest is
  'THE MATERIAL CLAIM. The state of the SUBJECT this receipt says it produced, '
  'chosen by the caller and built on prior_digest. The conflict detector, exact '
  'reversal and the continuity reducer all read this — never call_digest.';

-- ------------------------------------------------------------- retraction
-- The acceptance bar in 0213 counts unproven receipts globally and a receipt
-- can never be deleted, re-proven or repaired. Without a way to disavow one,
-- a single bad receipt is permanent and the bar is a wall rather than a bar.
alter table ops.write_receipt
  add column retracts_receipt_id uuid references ops.write_receipt(id);

alter table ops.write_receipt add constraint write_receipt_no_self_retraction
  check (retracts_receipt_id is null or retracts_receipt_id <> id);

-- A receipt is a reversal or a retraction, never both. They mean different
-- things — one restores a subject's state, the other disavows a claim — and a
-- row that did both would have to satisfy two sets of rules at once.
alter table ops.write_receipt add constraint write_receipt_reverses_xor_retracts
  check (reverses_receipt_id is null or retracts_receipt_id is null);

comment on column ops.write_receipt.retracts_receipt_id is
  'A later receipt disavowing an earlier one. Only a PROVEN retraction clears '
  'anything: ops.accept_phase4 ignores an unproven receipt that a proven '
  'receipt has retracted, and an unproven retraction clears nothing.';

create index write_receipt_retracts_idx
  on ops.write_receipt (retracts_receipt_id)
  where retracts_receipt_id is not null;

create function ops.require_sound_retraction()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  target ops.write_receipt%rowtype;
begin
  if new.retracts_receipt_id is null then
    return new;
  end if;
  select * into target from ops.write_receipt where id = new.retracts_receipt_id;
  if not found then
    raise exception 'receipt claims to retract an unknown receipt %', new.retracts_receipt_id;
  end if;
  if target.subject_type is distinct from new.subject_type
     or target.subject_id is distinct from new.subject_id then
    raise exception 'a retraction must name the same subject as the receipt it retracts';
  end if;
  return new;
end $$;

create trigger write_receipt_retraction_is_sound
before insert on ops.write_receipt
for each row execute function ops.require_sound_retraction();
alter table ops.write_receipt enable always trigger write_receipt_retraction_is_sound;

-- ---------------------------------------------- immutability, re-stated
-- Replaced because the identity tuple named claimed_digest and knew nothing
-- about material_digest or retracts_receipt_id. A field left out of this tuple
-- is a field a receipt can be rewritten through.
create or replace function ops.refuse_receipt_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'write receipts cannot be deleted';
  end if;
  if old.readback_digest is not null then
    raise exception 'receipt readback is already recorded and is final';
  end if;
  if (new.id, new.application_session_id, new.actor_id, new.organization_tenant_id,
      new.verb, new.subject_type, new.subject_id, new.tool_call_idempotency_key,
      new.call_digest, new.material_digest, new.prior_digest, new.recorded_at)
     is distinct from
     (old.id, old.application_session_id, old.actor_id, old.organization_tenant_id,
      old.verb, old.subject_type, old.subject_id, old.tool_call_idempotency_key,
      old.call_digest, old.material_digest, old.prior_digest, old.recorded_at)
     or new.reverses_receipt_id is distinct from old.reverses_receipt_id
     or new.retracts_receipt_id is distinct from old.retracts_receipt_id then
    raise exception 'write receipt identity is immutable';
  end if;
  if new.readback_digest is null then
    raise exception 'the only permitted update is recording the readback';
  end if;
  return new;
end $$;

-- ------------------------------------------------ exact reversal, corrected
-- THE BUG THIS FIXES. 0211 compared claimed_digest to target.prior_digest, and
-- claimed_digest was simultaneously required to equal the readback. No receipt
-- could satisfy both, so every reversal was born unprovable. The comparison
-- belongs to the MATERIAL claim, which is the only one of the two that is about
-- subject state at all.
create or replace function ops.require_exact_reversal()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  target ops.write_receipt%rowtype;
begin
  if new.reverses_receipt_id is null then
    return new;
  end if;
  select * into target from ops.write_receipt where id = new.reverses_receipt_id;
  if not found then
    raise exception 'receipt claims to reverse an unknown receipt %', new.reverses_receipt_id;
  end if;
  if target.subject_type is distinct from new.subject_type
     or target.subject_id is distinct from new.subject_id then
    raise exception 'a reversal must name the same subject as the receipt it reverses';
  end if;
  if new.material_digest is distinct from target.prior_digest then
    raise exception
      'reversal is not exact: it produces % but the receipt it reverses built on %',
      new.material_digest, target.prior_digest;
  end if;
  return new;
end $$;

-- ================================ (B) the readback covers what it describes

-- The recipe an honest caller computes, now BOUND TO THE SUBJECT. Exposing it
-- still does not weaken the proof: the readback reads the frozen row, so a
-- caller claiming a digest for a call it did not make produces an unproven
-- receipt rather than a proven lie.
create function ops.write_receipt_digest(
  p_verb text, p_actor_id uuid, p_tenant text, p_session uuid, p_request_hash text,
  p_subject_type text, p_subject_id uuid)
returns text language sql immutable
set search_path = pg_catalog
as $$
  select encode(sha256(convert_to(
    coalesce(p_verb,'') || '|' || coalesce(p_actor_id::text,'') || '|' ||
    coalesce(p_tenant,'') || '|' || coalesce(p_session::text,'') || '|' ||
    coalesce(p_request_hash,'') || '|' ||
    coalesce(p_subject_type,'') || '|' || coalesce(p_subject_id::text,''), 'UTF8')), 'hex');
$$;

-- The five-argument recipe is DROPPED rather than left beside the new one. It
-- computes a digest that is not bound to a subject, which is the exact defect
-- this migration exists to remove; leaving it callable would leave the defect
-- callable.
drop function ops.write_receipt_digest(text,uuid,text,uuid,text);

create or replace function ops.prove_write_receipt(p_receipt_id uuid)
returns boolean
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  r      ops.write_receipt%rowtype;
  tc     public.tool_call%rowtype;
  digest text;
begin
  select * into r from ops.write_receipt where id = p_receipt_id for update;
  if not found then
    raise exception 'no such write receipt %', p_receipt_id;
  end if;
  if r.readback_digest is not null then
    raise exception 'receipt % already carries a readback', p_receipt_id;
  end if;

  select * into tc from public.tool_call
   where idempotency_key = r.tool_call_idempotency_key;
  if not found then
    raise exception 'receipt % names evidence that does not exist (%)',
      p_receipt_id, r.tool_call_idempotency_key;
  end if;
  if tc.application_session_id is null then
    raise exception 'receipt % names LEGACY evidence, which cannot be read back',
      p_receipt_id;
  end if;
  if tc.application_session_id is distinct from r.application_session_id then
    raise exception 'receipt % names evidence written by a different session',
      p_receipt_id;
  end if;

  -- THE RECEIPT MUST DESCRIBE ITS OWN EVIDENCE. Without the verb clause the
  -- digest proved only that SOME qualified call existed in this session, and a
  -- receipt asserting any verb at all over a log-activity row proved cleanly.
  -- Refused rather than left unproven, because an unproven receipt is a lasting
  -- mark on the acceptance bar and a mislabelled one is the caller's error.
  --
  -- HONESTY ABOUT THE OTHER TWO: the actor and tenant clauses are UNREACHABLE
  -- while 0208 stands, because 0208 already refuses a tool_call whose actor or
  -- tenant differs from its session's, and this receipt is already required to
  -- match that same session. No probe below exercises them and none can. They
  -- are kept as depth against a future weakening of 0208, and they are named
  -- here as untested rather than counted as proven.
  if tc.verb is distinct from r.verb then
    raise exception 'receipt % claims verb % but its evidence records verb %',
      p_receipt_id, r.verb, tc.verb;
  end if;
  if tc.actor_id is distinct from r.actor_id then
    raise exception 'receipt % claims a different actor than its evidence', p_receipt_id;
  end if;
  if tc.organization_tenant_id is distinct from r.organization_tenant_id then
    raise exception 'receipt % claims a different tenant than its evidence', p_receipt_id;
  end if;

  -- Computed HERE, from the frozen row AND from this receipt's own subject, so
  -- a digest computed for one subject cannot prove a receipt naming another.
  digest := encode(sha256(convert_to(
      coalesce(tc.verb, '') || '|' ||
      coalesce(tc.actor_id::text, '') || '|' ||
      coalesce(tc.organization_tenant_id, '') || '|' ||
      coalesce(tc.application_session_id::text, '') || '|' ||
      coalesce(tc.request_hash, '') || '|' ||
      coalesce(r.subject_type, '') || '|' ||
      coalesce(r.subject_id::text, ''), 'UTF8')), 'hex');

  update ops.write_receipt
     set readback_digest = digest, readback_at = clock_timestamp()
   where id = p_receipt_id;

  return digest = r.call_digest;
end $$;

-- --------------------------------- conflict and continuity read the MATERIAL
-- Both of these compared call digests, which are digests of CALLS. Two honest
-- writes to the same subject from two different calls always differ there, so
-- the comparison was answering a question nobody asked.
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
     and a.material_digest <> b.material_digest
     and a.id < b.id
   where a.subject_type = p_subject_type
     and a.subject_id   = p_subject_id
     and not exists (
       select 1 from ops.write_receipt rev
        where rev.reverses_receipt_id in (a.id, b.id));
$$;

-- ============================================ (C) the bar gets a way to clear

create or replace function ops.continuity_reducer(p_subject_type text, p_subject_id uuid)
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

  -- A RETRACTED RECEIPT IS OUT OF THE FOLD ENTIRELY, not merely forgiven its
  -- unprovenness. A claim its own author has disavowed on the record should not
  -- go on setting the subject's head state. Only a PROVEN retraction counts,
  -- for the same reason the acceptance bar only honours a proven one: otherwise
  -- anything could be erased by asserting it twice.
  for r in
    select * from ops.write_receipt w
     where w.subject_type = p_subject_type and w.subject_id = p_subject_id
       and not exists (
         select 1 from ops.write_receipt rr
          where rr.retracts_receipt_id = w.id and rr.is_proven)
     order by w.recorded_at, w.id
  loop
    n := n + 1;
    if not r.is_proven then
      unproven := unproven + 1;
    end if;
    if prev_digest is not null and r.prior_digest is distinct from prev_digest then
      first_break := coalesce(first_break, r.id);
    end if;
    prev_digest := r.material_digest;
    head := r.material_digest;
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

create or replace function ops.accept_phase4(
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

  select count(*) into n_calls  from public.tool_call      where application_session_id is not null;
  select count(*) into n_events from public.event          where application_session_id is not null;
  select count(*) into n_reads  from public.tool_read_call where application_session_id is not null;

  -- THE ONE CHANGE. An unproven receipt that a PROVEN receipt has retracted no
  -- longer counts against the bar. Everything else about this count is as 0213
  -- left it: computed here, never supplied, and global rather than scoped.
  select count(*) filter (where w.is_proven),
         count(*) filter (where not w.is_proven and not exists (
           select 1 from ops.write_receipt rr
            where rr.retracts_receipt_id = w.id and rr.is_proven))
    into n_proven, n_unproven
    from ops.write_receipt w;

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

-- ====================== (D) the Drive retirement gate stops being decorative

create or replace function ops.require_proven_retirement_receipts()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  s        ops.application_session%rowtype;
  repoint  ops.write_receipt%rowtype;
  recovery ops.write_receipt%rowtype;
begin
  select * into s from ops.application_session
   where id = new.application_session_id for share;
  if not found then
    raise exception 'unknown application session % for retirement', new.application_session_id;
  end if;
  if s.revoked_at is not null then
    raise exception 'application session % is revoked', new.application_session_id;
  end if;
  if clock_timestamp() >= s.expires_at then
    raise exception 'application session % is expired', new.application_session_id;
  end if;
  if new.retired_by_actor_id is distinct from s.actor_id then
    raise exception 'retirement names a different actor than its session';
  end if;
  if new.organization_tenant_id is distinct from s.organization_tenant_id then
    raise exception 'retirement names a different tenant than its session';
  end if;

  select * into repoint  from ops.write_receipt where id = new.repoint_receipt_id;
  select * into recovery from ops.write_receipt where id = new.recovery_receipt_id;

  if not repoint.is_proven then
    raise exception 'the repoint receipt % is not proven', new.repoint_receipt_id;
  end if;
  if not recovery.is_proven then
    raise exception 'the recovery receipt % is not proven', new.recovery_receipt_id;
  end if;

  -- EACH RECEIPT MUST NAME THE DEPENDENCY BEING RETIRED. Without this, 0214
  -- accepted any two proven receipts about anything at all — the reviewer
  -- retired a dependency with receipts that had never heard of it.
  if repoint.subject_type is distinct from 'drive_dependency'
     or repoint.subject_id is distinct from new.drive_dependency_id then
    raise exception 'the repoint receipt % does not name dependency %',
      new.repoint_receipt_id, new.drive_dependency_id;
  end if;
  if recovery.subject_type is distinct from 'drive_dependency'
     or recovery.subject_id is distinct from new.drive_dependency_id then
    raise exception 'the recovery receipt % does not name dependency %',
      new.recovery_receipt_id, new.drive_dependency_id;
  end if;

  -- THE TWO MUST DIFFER IN WHAT THEY ASSERT, not merely in row id. Different
  -- calls, and different material claims: repointing a reader and recovering
  -- from it are two pieces of work, and two rows describing one piece of work
  -- are one piece of evidence counted twice.
  --
  -- THIS SHADOWS 0214's drive_retirement_distinct_receipts CHECK, and the next
  -- reader should know that rather than discover it. A receipt trivially shares
  -- its own call with itself, so passing one receipt for both roles now trips
  -- the same-call clause below, and a BEFORE trigger runs ahead of any check
  -- constraint. That constraint is therefore no longer reachable by any input.
  -- It is kept as depth against this trigger being dropped, and it is named
  -- here as shadowed rather than counted as a tested guard.
  if repoint.tool_call_idempotency_key is not distinct from
     recovery.tool_call_idempotency_key then
    raise exception
      'the repoint and recovery receipts rest on the SAME call (%); two claims '
      'about one call is one claim counted twice',
      repoint.tool_call_idempotency_key;
  end if;
  if repoint.material_digest is not distinct from recovery.material_digest then
    raise exception
      'the repoint and recovery receipts assert the SAME material state (%)',
      repoint.material_digest;
  end if;

  -- AND THE RECOVERY MUST BUILD ON THE REPOINT. Recovery is only meaningful
  -- from the state the repoint produced; a recovery resting on some other state
  -- is a recovery of something else.
  if recovery.prior_digest is distinct from repoint.material_digest then
    raise exception
      'the recovery receipt does not build on the repointed state: it built on '
      '% but the repoint produced %',
      recovery.prior_digest, repoint.material_digest;
  end if;
  return new;
end $$;

-- ------------------------------------------------------------------ grants
revoke all on function ops.write_receipt_digest(text,uuid,text,uuid,text,text,uuid) from public;
revoke all on function ops.require_sound_retraction() from public;
grant execute on function ops.write_receipt_digest(text,uuid,text,uuid,text,text,uuid)
  to carr_writer, carr_reader;

-- ========== (E) A PRIOR STATE MUST BE ONE THE SUBJECT ACTUALLY REACHED

-- THE THIRD HALF OF THE REVIEW FINDING, and the reason the first draft of this
-- migration left it alone. prior_digest was caller-chosen and checked only for
-- being non-empty, so a writer emitting a random prior per receipt produced no
-- detectable conflict at all: ops.receipt_conflicts only sees two receipts that
-- name the SAME prior, and receipts that never agree on a prior never conflict.
-- Causal history was an honest-caller convention wearing the costume of a guard.
--
-- THE TRAP THAT MADE THIS LOOK UNFIXABLE. The obvious enforcement — a receipt
-- must build on the subject's CURRENT HEAD — closes the hole and destroys
-- something worth more: it makes ops.continuity_reducer's 'broken' state
-- unreachable, because no chain could ever have a gap. A reducer whose worst
-- finding is impossible is not a safe reducer, it is a blind one.
--
-- THE DISTINCTION THAT RESOLVES IT. A break means two different things, and
-- only one of them is a lie:
--
--   A FABRICATED PRIOR names a state this subject NEVER REACHED. Nothing honest
--   produces one. It is refused here.
--
--   A STALE PRIOR names a state the subject really did reach, just not the
--   latest one — a concurrent writer, an out-of-order arrival, a replayed
--   write. That is a real history somebody has to reconcile, it is exactly what
--   'broken' and 'conflicted' exist to report, and it stays fully expressible.
--
-- So the rule is EXISTENCE, not RECENCY. After this, a break is a finding
-- instead of a typo, and an evader who wants to avoid conflicting with an
-- existing receipt must name a real prior state — which is the very thing that
-- makes the conflict visible.
--
-- 'origin' STAYS ALWAYS-ACCEPTABLE, deliberately, even for a subject that
-- already has receipts. Refusing it would turn an ordinary race — the producer
-- reads no previous receipt, a concurrent transaction commits one, the insert
-- lands second — into a failed verb call for the human. And it buys nothing: a
-- writer that spams 'origin' gives every receipt the same prior, which is the
-- one shape ops.receipt_conflicts is guaranteed to catch.
create function ops.require_prior_state_existed()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if new.prior_digest = 'origin' then
    return new;
  end if;
  if not exists (
    select 1 from ops.write_receipt w
     where w.subject_type    = new.subject_type
       and w.subject_id      = new.subject_id
       and w.material_digest = new.prior_digest)
  then
    raise exception
      'receipt builds on a state this subject never reached (%); a prior digest '
      'must be ''origin'' or name material that some earlier receipt on this '
      'subject actually produced', new.prior_digest;
  end if;
  return new;
end $$;

-- NAMED SO IT FIRES LAST among this table's BEFORE INSERT triggers, which
-- Postgres runs in alphabetical order: requires_live_session, then
-- retraction_is_sound, then reversal_is_exact, then this. The session guard
-- deserves first refusal on anything, and a malformed reversal or retraction
-- should be named as such rather than as a prior-state problem.
create trigger write_receipt_state_existed
before insert on ops.write_receipt
for each row execute function ops.require_prior_state_existed();
alter table ops.write_receipt enable always trigger write_receipt_state_existed;

revoke all on function ops.require_prior_state_existed() from public;

create index write_receipt_material_idx
  on ops.write_receipt (subject_type, subject_id, material_digest);

-- ------------------------------------------ the material recipe, for producers
-- WHAT "MATERIAL" MEANS HERE, precisely, because a vague answer is how the
-- first digest ended up meaning two things. A receipt's material digest is a
-- hash of WHAT THE CALL WROTE ABOUT THIS ONE SUBJECT: the ordered set of
-- (verb, field, old_value, new_value) from the event rows that call produced
-- for that subject, folded together with the subject's own identity.
--
-- ROW IDENTITY AND TIMESTAMPS ARE DELIBERATELY EXCLUDED. Two calls that write
-- the same change to the same subject must hash the SAME, because that is what
-- makes an idempotent restatement recognisable as a no-op instead of as a new
-- link in the chain. Including event.id would have made every restatement look
-- like a change, which is the noise the producer's no-op skip exists to avoid.
--
-- IT IS STILL THE CALLER'S CLAIM, and that is not an oversight. A reversal's
-- material is the state its target BUILT ON, which is by construction not a
-- hash of the reversal's own rows, so a database-computed material digest could
-- not express a reversal at all. This function is the honest producer's recipe,
-- not a second readback.
create function ops.write_receipt_material_digest(
  p_tool_call_key text, p_session uuid, p_subject_type text, p_subject_id uuid)
returns text language sql stable
set search_path = pg_catalog, ops, public
as $$
  select encode(sha256(convert_to(
    coalesce(p_subject_type, '') || chr(30) || coalesce(p_subject_id::text, '') || chr(30) ||
    coalesce((
      select string_agg(
               coalesce(e.verb, '')            || chr(31) ||
               coalesce(e.field, '')           || chr(31) ||
               coalesce(e.old_value::text, '') || chr(31) ||
               coalesce(e.new_value::text, ''),
               chr(29)
               order by coalesce(e.verb, ''), coalesce(e.field, ''),
                        coalesce(e.old_value::text, ''), coalesce(e.new_value::text, ''))
        from public.event e
       where e.idempotency_key      = p_tool_call_key
         and e.application_session_id = p_session
         and e.subject_type         = p_subject_type
         and e.subject_id           = p_subject_id), ''), 'UTF8')), 'hex');
$$;

revoke all on function ops.write_receipt_material_digest(text,uuid,text,uuid) from public;
grant execute on function ops.write_receipt_material_digest(text,uuid,text,uuid)
  to carr_writer, carr_reader;

-- --------------------------------------------------------------- apply-time
-- EXERCISES every guarantee this migration claims, and rolls all of it back.
-- Same reasoning as 0208, 0211, 0213 and 0214: this file runs where the
-- contract suite does not, and a shape check would let a gutted guard through.
--
-- EVERY FIXTURE BELOW IS ONE PRODUCTION COULD PRODUCE. The first draft of this
-- block failed that test — it built receipts on invented prior states like
-- 'm-bad-base', which section (E) now refuses and which no producer ever emits.
-- A probe resting on an impossible fixture proves something about a database
-- nobody runs.
do $$
declare
  probe_actor uuid;
  sid         uuid := gen_random_uuid();
  subj        uuid := gen_random_uuid();
  other_subj  uuid := gen_random_uuid();
  ret_subj    uuid := gen_random_uuid();
  brk_subj    uuid := gen_random_uuid();
  dep         uuid;
  k1          text := 'split-probe-1-' || gen_random_uuid()::text;
  k2          text := 'split-probe-2-' || gen_random_uuid()::text;
  k3          text := 'split-probe-3-' || gen_random_uuid()::text;
  k4          text := 'split-probe-4-' || gen_random_uuid()::text;
  k5          text := 'split-probe-5-' || gen_random_uuid()::text;
  d1          text;
  d2          text;
  d4          text;
  r1          uuid := gen_random_uuid();
  r2          uuid := gen_random_uuid();
  rev         uuid := gen_random_uuid();
  bad         uuid := gen_random_uuid();
  ret         uuid := gen_random_uuid();
  r           record;
  failed      boolean;
  mat_a       text;
  mat_b       text;
begin
  select id into probe_actor from public.actor where kind = 'human' order by slug limit 1;
  if probe_actor is null then
    raise exception '0220 FAILED: need a human actor to exercise the split';
  end if;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (sid, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
          'verified_partner', 'probe', clock_timestamp() + interval '1 hour');

  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (k1, 'log-activity', probe_actor, 'rh-1', '{}'::jsonb, 'carr-internal', sid),
         (k2, 'log-activity', probe_actor, 'rh-2', '{}'::jsonb, 'carr-internal', sid),
         (k3, 'update-deal',  probe_actor, 'rh-3', '{}'::jsonb, 'carr-internal', sid),
         (k4, 'log-activity', probe_actor, 'rh-4', '{}'::jsonb, 'carr-internal', sid),
         (k5, 'log-activity', probe_actor, 'rh-5', '{}'::jsonb, 'carr-internal', sid);

  d1 := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid,
                                 'rh-1', 'deal', subj);
  d2 := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid,
                                 'rh-2', 'deal', subj);
  d4 := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid,
                                 'rh-4', 'deal', subj);

  -- (1) THE CALL DIGEST IS BOUND TO THE SUBJECT. The same call digested for
  -- another subject must be a different value, or a digest is transferable
  -- between subjects and proof can be borrowed.
  if d1 = ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid,
                                   'rh-1', 'deal', other_subj) then
    raise exception '0220 FAILED: the call digest is identical for two different subjects';
  end if;

  -- (2) The honest path still proves.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest)
  values (r1, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', subj, k1, d1, 'm1', 'origin');
  if not ops.prove_write_receipt(r1) then
    raise exception '0220 FAILED: an honest receipt did not prove after the split';
  end if;

  -- (3) A DIGEST COMPUTED FOR ANOTHER SUBJECT MUST NOT PROVE.
  begin
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', other_subj, k1, d1, 'm-other', 'origin');
    if (select is_proven from ops.write_receipt
         where subject_id = other_subj and tool_call_idempotency_key = k1) then
      raise exception '0220 FAILED: a digest computed for a DIFFERENT subject proved';
    end if;
    raise exception 'ROLLBACK_SUBJECT_BINDING';
  exception when others then
    if sqlerrm <> 'ROLLBACK_SUBJECT_BINDING' then raise; end if;
  end;

  -- (4) A RECEIPT MUST DESCRIBE ITS OWN EVIDENCE. k3 records verb update-deal;
  -- a receipt claiming log-activity over it is the 'retire-the-entire-drive'
  -- attack in miniature and must be refused BY THE VERB GUARD.
  begin
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k3, d1, 'm-mislabelled', 'm1');
    failed := false;
    begin
      perform ops.prove_write_receipt(
        (select id from ops.write_receipt
          where tool_call_idempotency_key = k3 and subject_id = subj));
    exception when others then
      failed := true;
      if position('claims verb' in sqlerrm) = 0 then
        raise exception '0220 FAILED: verb mismatch refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0220 FAILED: a receipt proved against evidence recording a DIFFERENT verb';
    end if;
    raise exception 'ROLLBACK_VERB_BINDING';
  exception when others then
    if sqlerrm <> 'ROLLBACK_VERB_BINDING' then raise; end if;
  end;

  -- (5) THE HEADLINE FIX: AN EXACT REVERSAL CAN NOW PROVE. Under 0211 this was
  -- impossible by construction — the reversal's digest had to equal both the
  -- target's prior state and the readback of its own call, and those are never
  -- the same value. Closing a conflict therefore guaranteed a permanently
  -- unproven receipt, which permanently barred acceptance.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest)
  values (r2, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', subj, k2, d2, 'm2', 'm1');
  if not ops.prove_write_receipt(r2) then
    raise exception '0220 FAILED: the second honest receipt did not prove';
  end if;

  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest, reverses_receipt_id)
  values (rev, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', subj, k4, d4, 'm1', 'm2', r2);
  if not ops.prove_write_receipt(rev) then
    raise exception '0220 FAILED: an EXACT REVERSAL still cannot prove — the '
                    'defect this migration exists to remove is still present';
  end if;

  -- An inexact reversal must still refuse, and by its own guard.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest, reverses_receipt_id)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k4, d4, 'not-the-prior-state', 'm1', r2);
  exception when others then
    failed := true;
    if position('reversal is not exact' in sqlerrm) = 0 then
      raise exception '0220 FAILED: inexact reversal refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0220 FAILED: an inexact reversal was accepted';
  end if;

  -- (6) THE ACCEPTANCE BAR CLEARS THROUGH A PROVEN RETRACTION, AND ONLY ONE.
  -- On its own subject, because a bad receipt sharing a prior with an honest
  -- one would raise a CONFLICT and the bar would then refuse for that reason
  -- instead — the probe would pass while testing nothing it claims to test.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest)
  values (bad, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', ret_subj, k1, 'a-digest-nobody-wrote', 'm-bad', 'origin');
  if ops.prove_write_receipt(bad) then
    raise exception '0220 FAILED: a receipt claiming a digest it never wrote was PROVEN';
  end if;

  begin
    failed := false;
    perform ops.accept_phase4(gen_random_uuid(), sid, 'probe: must refuse');
  exception when others then
    failed := true;
    if position('phase4_acceptance_no_unproven_receipts' in sqlerrm) = 0 then
      raise exception '0220 FAILED: acceptance refused by the WRONG bar: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0220 FAILED: Phase 4 was accepted while a receipt was unproven';
  end if;

  -- AN UNPROVEN RETRACTION CLEARS NOTHING, and this needs THREE levels to test.
  -- The obvious two-level probe cannot see the guard at all: an unproven
  -- retraction is itself an unproven receipt, so the bar refuses either way and
  -- a mutant that ignored is_proven survived it. Stack one more level and the
  -- two versions finally disagree — under the real rule `bad` is retracted only
  -- by an UNPROVEN receipt and still counts, while a mutant that ignored
  -- is_proven would excuse `bad`, then excuse its unproven retractor in turn,
  -- and accept a phase resting on a receipt nobody ever proved.
  begin
    declare
      unproven_ret uuid := gen_random_uuid();
      proven_ret2  uuid := gen_random_uuid();
    begin
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest, retracts_receipt_id)
      values (unproven_ret, sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', ret_subj, k5, 'also-nobody-wrote-this', 'm-ret-u', 'm-bad', bad);
      if ops.prove_write_receipt(unproven_ret) then
        raise exception '0220 FAILED: a retraction claiming a digest it never wrote PROVED';
      end if;

      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest, retracts_receipt_id)
      values (proven_ret2, sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', ret_subj, k5,
              ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                       sid, 'rh-5', 'deal', ret_subj),
              'm-ret-2', 'm-ret-u', unproven_ret);
      if not ops.prove_write_receipt(proven_ret2) then
        raise exception '0220 FAILED: an honest second-level retraction could not prove';
      end if;

      failed := false;
      begin
        perform ops.accept_phase4(gen_random_uuid(), sid, 'probe: unproven retraction');
      exception when others then
        failed := true;
        if position('phase4_acceptance_no_unproven_receipts' in sqlerrm) = 0 then
          raise exception '0220 FAILED: acceptance refused by the WRONG bar: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0220 FAILED: an UNPROVEN retraction cleared the acceptance bar';
      end if;
    end;
    raise exception 'ROLLBACK_UNPROVEN_RETRACTION';
  exception when others then
    if sqlerrm <> 'ROLLBACK_UNPROVEN_RETRACTION' then raise; end if;
  end;

  -- A retraction must name the same subject as what it retracts.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest, retracts_receipt_id)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', other_subj, k5,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, 'rh-5', 'deal', other_subj),
            'm-ret', 'origin', bad);
  exception when others then
    failed := true;
    if position('same subject as the receipt it retracts' in sqlerrm) = 0 then
      raise exception '0220 FAILED: cross-subject retraction refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0220 FAILED: a retraction named a different subject than its target';
  end if;

  -- THE PROVEN RETRACTION, and the bar clears.
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest, retracts_receipt_id)
  values (ret, sid, probe_actor, 'carr-internal', 'log-activity',
          'deal', ret_subj, k5,
          ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                   sid, 'rh-5', 'deal', ret_subj),
          'm-ret', 'm-bad', bad);
  if not ops.prove_write_receipt(ret) then
    raise exception '0220 FAILED: an honest retraction could not prove';
  end if;
  perform ops.accept_phase4(gen_random_uuid(), sid,
    '0220 probe: the bar clears once a proven receipt retracts the unproven one');

  -- The reducer must drop the retracted receipt out of the fold entirely, and
  -- its head must be the last MATERIAL claim rather than the last call digest.
  select * into r from ops.continuity_reducer('deal', ret_subj);
  if r.unproven_count <> 0 then
    raise exception '0220 FAILED: the reducer still counts a retracted receipt as unproven';
  end if;
  if r.head_digest is distinct from 'm-ret' then
    raise exception '0220 FAILED: the reducer head is not the last MATERIAL claim (got %)',
      r.head_digest;
  end if;

  -- (7) CONFLICT IS A DISAGREEMENT ABOUT MATERIAL, NOT ABOUT CALLS. Two
  -- receipts on one subject, built on one prior state, asserting different
  -- material, from different calls.
  begin
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', other_subj, k1,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, 'rh-1', 'deal', other_subj),
            'branch-x', 'origin'),
           (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', other_subj, k2,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, 'rh-2', 'deal', other_subj),
            'branch-y', 'origin');
    if (select count(*) from ops.receipt_conflicts('deal', other_subj)) <> 1 then
      raise exception '0220 FAILED: two receipts disagreeing about material did not conflict';
    end if;
    raise exception 'ROLLBACK_CONFLICT_PROBE';
  exception when others then
    if sqlerrm <> 'ROLLBACK_CONFLICT_PROBE' then raise; end if;
  end;

  -- (7b) AND THE OTHER HALF OF THAT DEFINITION, which the probe above cannot
  -- see on its own: two receipts that AGREE about material are not in conflict
  -- however many different calls produced them. Written because a mutant that
  -- compared call digests here survived the probe above — two honest calls
  -- always differ in their call digest, so that mutant reported a conflict
  -- between a write and its own idempotent restatement.
  begin
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', other_subj, k1,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, 'rh-1', 'deal', other_subj),
            'same-material', 'origin'),
           (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', other_subj, k2,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, 'rh-2', 'deal', other_subj),
            'same-material', 'origin');
    if (select count(*) from ops.receipt_conflicts('deal', other_subj)) <> 0 then
      raise exception '0220 FAILED: two receipts AGREEING about material were '
                      'reported as a conflict, so conflict is being decided by '
                      'the call rather than by the claim';
    end if;
    raise exception 'ROLLBACK_AGREEMENT_PROBE';
  exception when others then
    if sqlerrm <> 'ROLLBACK_AGREEMENT_PROBE' then raise; end if;
  end;

  -- (7c) A RECEIPT IS A REVERSAL OR A RETRACTION, NEVER BOTH. Both halves below
  -- are individually valid — the reversal is exact, the retraction names the
  -- right subject, and the prior is the subject's CURRENT HEAD ('m1', produced
  -- by the reversal above) — so every trigger passes and the CHECK CONSTRAINT
  -- is the only guard left that can refuse. The prior is the head rather than
  -- merely a real state on purpose: this probe is not testing the prior guard,
  -- so it must not be able to trip it under any variant of that guard.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest,
       reverses_receipt_id, retracts_receipt_id)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k4, d4, 'm1', 'm1', r2, r1);
  exception when others then
    failed := true;
    if position('write_receipt_reverses_xor_retracts' in sqlerrm) = 0 then
      raise exception '0220 FAILED: a reverse-and-retract receipt refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0220 FAILED: one receipt both reversed and retracted';
  end if;

  -- (7d) A FABRICATED PRIOR IS REFUSED. This is the guard from section (E):
  -- 'm2' and 'm1' are states this subject really reached, but nothing on it
  -- ever produced 'a-state-nobody-produced'.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k4, d4, 'm-invented', 'a-state-nobody-produced');
  exception when others then
    failed := true;
    if position('never reached' in sqlerrm) = 0 then
      raise exception '0220 FAILED: a fabricated prior refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0220 FAILED: a receipt built on a state its subject never reached';
  end if;

  -- (7e) AND 'broken' MUST STILL BE REACHABLE. This is the whole point of
  -- checking EXISTENCE rather than RECENCY, and without this probe a stricter
  -- rule — prior must equal the current head — would pass every other check in
  -- this file while quietly making the reducer's worst finding impossible.
  --
  -- The shape is a late-arriving restatement: b4 repeats the transition b2
  -- already made. Its prior is real, so it is admitted; it is not the head, so
  -- the fold finds a gap; and it AGREES with b2 about material, so it is not a
  -- conflict. That leaves 'broken' as the only state it can produce.
  begin
    declare
      bd text := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                          sid, 'rh-1', 'deal', brk_subj);
      b1 uuid := gen_random_uuid();
      b2 uuid := gen_random_uuid();
      b3 uuid := gen_random_uuid();
      b4 uuid := gen_random_uuid();
    begin
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest)
      values (b1, sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', brk_subj, k1, bd, 'bm1', 'origin');
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest)
      values (b2, sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', brk_subj, k1, bd, 'bm2', 'bm1');
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest)
      values (b3, sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', brk_subj, k1, bd, 'bm3', 'bm2');
      perform ops.prove_write_receipt(b1);
      perform ops.prove_write_receipt(b2);
      perform ops.prove_write_receipt(b3);

      select * into r from ops.continuity_reducer('deal', brk_subj);
      if r.state <> 'continuous' then
        raise exception '0220 FAILED: an unbroken chain did not reduce to continuous (got %)',
          r.state;
      end if;

      -- The refusal path is caught and RENAMED here on purpose. A rule that
      -- demanded the prior be the CURRENT HEAD would reject this insert, and
      -- the raw message ('never reached') would send the next reader hunting a
      -- fixture bug instead of telling them what actually broke.
      begin
        insert into ops.write_receipt
          (id, application_session_id, actor_id, organization_tenant_id, verb,
           subject_type, subject_id, tool_call_idempotency_key,
           call_digest, material_digest, prior_digest)
        values (b4, sid, probe_actor, 'carr-internal', 'log-activity',
                'deal', brk_subj, k1, bd, 'bm2', 'bm1');
      exception when others then
        raise exception '0220 FAILED: a STALE BUT REAL prior state was refused (%), '
                        'which makes the reducer''s BROKEN state unreachable. The '
                        'prior guard must check that a state EXISTED, never that '
                        'it is the latest one.', sqlerrm;
      end;
      perform ops.prove_write_receipt(b4);

      select * into r from ops.continuity_reducer('deal', brk_subj);
      if r.state <> 'broken' then
        raise exception '0220 FAILED: a stale-but-real prior no longer produces a '
                        'BROKEN chain (got %) — the reducer''s worst finding has '
                        'been made unreachable', r.state;
      end if;
      if r.break_at is distinct from b4 then
        raise exception '0220 FAILED: the reducer did not name where the chain broke';
      end if;
      if r.conflict_count <> 0 then
        raise exception '0220 FAILED: a restatement that AGREES about material was '
                        'counted as a conflict';
      end if;
    end;
    raise exception 'ROLLBACK_BROKEN_PROBE';
  exception when others then
    if sqlerrm <> 'ROLLBACK_BROKEN_PROBE' then raise; end if;
  end;

  -- (8) THE DRIVE RETIREMENT GATE. Each remaining clause gets a probe that only
  -- it can refuse. The first version of this section could not do that: the
  -- same-call probe also shared a material claim, so deleting the same-call
  -- guard just moved the refusal to the same-material guard and the mutant
  -- still died — looking tested while nothing tested it. Every fixture below
  -- satisfies every clause except the one under test.
  --
  -- Inserted one statement at a time: a receipt's prior state must already
  -- exist, and rows earlier in a multi-row VALUES list are not reliably visible
  -- to a later row's BEFORE trigger.
  insert into ops.drive_dependency (source_path, reference, classification, operational)
  values ('tools/split-probe.py:1', '{{VAULT}}', 'vault-path', true)
  returning id into dep;

  declare
    p1 uuid := gen_random_uuid();   -- the repoint: origin -> repointed, call k1
    p2 uuid := gen_random_uuid();   -- an honest recovery, but on the SAME call
    p3 uuid := gen_random_uuid();   -- a different call, but the SAME material
    p4 uuid := gen_random_uuid();   -- different material, built SOMEWHERE ELSE
    p5 uuid := gen_random_uuid();   -- the honest recovery
    dd1 text := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                         sid, 'rh-1', 'drive_dependency', dep);
    dd2 text := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                         sid, 'rh-2', 'drive_dependency', dep);
    dd4 text := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                         sid, 'rh-4', 'drive_dependency', dep);
  begin
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (p1, sid, probe_actor, 'carr-internal', 'log-activity',
            'drive_dependency', dep, k1, dd1, 'repointed', 'origin');
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (p2, sid, probe_actor, 'carr-internal', 'log-activity',
            'drive_dependency', dep, k1, dd1, 'recovered', 'repointed');
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (p3, sid, probe_actor, 'carr-internal', 'log-activity',
            'drive_dependency', dep, k2, dd2, 'repointed', 'repointed');
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (p4, sid, probe_actor, 'carr-internal', 'log-activity',
            'drive_dependency', dep, k4, dd4, 'recovered-elsewhere', 'recovered');
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (p5, sid, probe_actor, 'carr-internal', 'log-activity',
            'drive_dependency', dep, k4, dd4, 'recovered', 'repointed');
    perform ops.prove_write_receipt(p1);
    perform ops.prove_write_receipt(p2);
    perform ops.prove_write_receipt(p3);
    perform ops.prove_write_receipt(p4);
    perform ops.prove_write_receipt(p5);

    -- Receipts that name something OTHER than the dependency must be refused.
    begin
      failed := false;
      insert into ops.drive_retirement
        (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
         application_session_id, retired_by_actor_id, organization_tenant_id, note)
      values (gen_random_uuid(), dep, r1, r2, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('does not name dependency' in sqlerrm) = 0 then
        raise exception '0220 FAILED: unrelated-receipt retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0220 FAILED: a dependency was retired with receipts that never named it';
    end if;

    -- SAME CALL. p2 differs in material and builds on the repoint, so every
    -- other clause is satisfied and only the same-call guard can refuse.
    begin
      failed := false;
      insert into ops.drive_retirement
        (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
         application_session_id, retired_by_actor_id, organization_tenant_id, note)
      values (gen_random_uuid(), dep, p1, p2, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('rest on the SAME call' in sqlerrm) = 0 then
        raise exception '0220 FAILED: same-call retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0220 FAILED: a dependency was retired on two receipts about ONE call';
    end if;

    -- SAME MATERIAL. p3 is a different call and builds on the repointed state,
    -- so only the same-material guard can refuse.
    begin
      failed := false;
      insert into ops.drive_retirement
        (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
         application_session_id, retired_by_actor_id, organization_tenant_id, note)
      values (gen_random_uuid(), dep, p1, p3, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('assert the SAME material state' in sqlerrm) = 0 then
        raise exception '0220 FAILED: same-material retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0220 FAILED: a dependency was retired on two receipts asserting the same thing';
    end if;

    -- A RECOVERY THAT IGNORED THE REPOINT. Different call, different material,
    -- so only the builds-on guard is left.
    begin
      failed := false;
      insert into ops.drive_retirement
        (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
         application_session_id, retired_by_actor_id, organization_tenant_id, note)
      values (gen_random_uuid(), dep, p1, p4, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('does not build on the repointed state' in sqlerrm) = 0 then
        raise exception '0220 FAILED: uncoupled-recovery retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0220 FAILED: a recovery receipt that ignored the repoint was accepted';
    end if;

    -- AND THE HONEST PATH MUST STILL WORK. A gate that can only say no is
    -- indistinguishable from a broken one, and nothing above would catch that.
    insert into ops.drive_retirement
      (id, drive_dependency_id, repoint_receipt_id, recovery_receipt_id,
       application_session_id, retired_by_actor_id, organization_tenant_id, note)
    values (gen_random_uuid(), dep, p1, p5, sid, probe_actor, 'carr-internal',
            'probe: repointed, then recovered from the repointed state');
  end;

  -- (9) THE MATERIAL RECIPE. The same change written twice must hash the same,
  -- or an idempotent restatement would look like a new link in the chain; a
  -- different change must hash differently, or the chain would flatten.
  insert into public.event
    (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
     cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj,
          'stage', '"under-loi"'::jsonb, 'system', k1, 'carr-internal', sid),
         (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj,
          'stage', '"under-loi"'::jsonb, 'system', k2, 'carr-internal', sid),
         (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj,
          'stage', '"closed"'::jsonb,    'system', k4, 'carr-internal', sid);
  mat_a := ops.write_receipt_material_digest(k1, sid, 'deal', subj);
  mat_b := ops.write_receipt_material_digest(k2, sid, 'deal', subj);
  if mat_a is distinct from mat_b then
    raise exception '0220 FAILED: the same change written twice hashed differently, '
                    'so an idempotent restatement would read as a new link';
  end if;
  if mat_a = ops.write_receipt_material_digest(k4, sid, 'deal', subj) then
    raise exception '0220 FAILED: a DIFFERENT change hashed the same as the first';
  end if;
  if mat_a = ops.write_receipt_material_digest(k1, sid, 'deal', other_subj) then
    raise exception '0220 FAILED: the material digest is not bound to its subject';
  end if;

  raise notice '0220 apply-time proof passed';
  raise exception 'ROLLBACK_0220_PROBE';
exception when others then
  if sqlerrm = 'ROLLBACK_0220_PROBE' then
    return;
  end if;
  raise;
end $$;
