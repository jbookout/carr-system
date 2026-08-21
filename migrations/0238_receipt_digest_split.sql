-- 0238 — the receipt digest was two different facts wearing one name
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
--   1. A REVERSAL RECEIPT COULD NEVER PROVE. 0235 defines exact reversal as
--      claimed_digest = target.prior_digest, but claimed_digest must ALSO equal
--      the readback, which is always the digest of the frozen call. The two
--      requirements are mutually exclusive, so closing a conflict guaranteed a
--      permanently unproven receipt.
--
--   2. ONE UNPROVEN RECEIPT BRICKED ACCEPTANCE FOR THE WHOLE DATABASE. 0236
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
--      proved cleanly, and 0237's two-receipt gate separated its two receipts
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
--   D. MAKES 0237'S TWO-RECEIPT GATE MEAN SOMETHING. Each receipt must NAME the
--      dependency being retired, the two must rest on DIFFERENT calls and make
--      DIFFERENT material claims, and the recovery must build on the state the
--      repoint produced.
--
-- APPLY IT THROUGH tools/migrate.py, WHICH WRAPS EACH MIGRATION IN A
-- TRANSACTION. Applying it by hand with `psql -f` does not: every statement
-- autocommits, so a failure in the apply-time proof at the bottom leaves the
-- DDL above it committed and the migration unrecorded -- a database carrying
-- call_digest and material_digest that the ledger says never ran. A reviewer
-- hit exactly that while probing this file. bin/migrate-prod.sh goes through
-- migrate.py; ops/check-application-session.sh uses psql on a disposable
-- cluster, where a half-applied database is thrown away rather than kept.
--
-- APPLYING THIS IS NOT A ROLLING DEPLOY, and an operator needs to know before
-- they start. 0238 drops the five-argument ops.write_receipt_digest and renames
-- a column the producer writes, so there is no ordering in which a Worker built
-- against the other side of this migration keeps working: old code against 0238
-- fails on the dropped signature, and new code against 0237 fails on the missing
-- seven-argument one. Both failures land inside the receipt producer, which runs
-- after the tool_call insert, so the whole verb rolls back and every qualified
-- write returns an error.
--
-- The safe sequences are: apply the WHOLE chain and then deploy, on a database
-- whose Worker does not yet file receipts at all (which is production's state
-- today, since no Worker on main references these objects); or deploy and
-- migrate together. The unsafe ones are applying only part of the chain --
-- `bin/migrate-prod.sh --through 0237_drive_retirement.sql` is exactly that --
-- and deploying the new Worker before the migrations land.
--
-- WHAT THIS MIGRATION DELIBERATELY DOES NOT DO, so the next reader does not
-- mistake silence for coverage.
--
-- A RETRACTION'S MATERIAL CLAIM IS UNCONSTRAINED, and that is by construction
-- rather than by omission: a reversal's material is the state its TARGET built
-- on, which is not a digest of its own rows and cannot be recomputed from them,
-- so section (F)'s material check exempts both. That exemption was briefly a
-- hole, because two other guards accepted a retraction as evidence of subject
-- state -- the prior-state guard as a SOURCE of state, and the retirement gate
-- as a repoint or a recovery. Both now refuse retractions and reversals, so the
-- unconstrained material is inert: nothing downstream reads it. If a future
-- guard starts reading material, it must exclude them too or reopen this.
--
-- PROOF IS ATTACHMENT AND INTERNAL CONSISTENCY, NOT TRUTH. carr_writer authors
-- both sides -- it inserts the tool_call row including the request hash, it
-- inserts the event rows the material digest folds, and it holds the digest
-- function. A proven receipt therefore shows that a receipt is attached to a
-- real qualified call in its own session and that its claims agree with that
-- call's evidence. It does not show the claim is true. Against a BUGGY writer,
-- which is the failure this layer exists for, that is strong. Against a
-- compromised carr_writer no database-side guard helps, because writing the
-- evidence is that credential's legitimate job. Closing that needs receipts
-- minted by a credential the verb path does not hold, the way carr_session_issuer
-- already works for sessions, and that is a Phase 5 change to the write path
-- rather than a patch here. No surface may describe a proven receipt as
-- evidence that a write is TRUE.
--
-- CORRECTED, because this section said the opposite until section (E) existed:
-- prior_digest IS now verified. It must be 'origin' or name material a proven,
-- unretracted, ordinary receipt on the same subject in the same tenant actually
-- produced. The check is EXISTENCE and never recency, so a stale-but-real prior
-- stays legal and the reducer's 'broken' state stays reachable, which is what
-- the old text was protecting when it declined to verify anything at all.

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
-- restored to ENABLE ALWAYS, which is the state 0235 left it in; restoring it
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

-- --------------------------------------------------- a deterministic order
-- THE FOLD ORDER WAS DECIDED BY A RANDOM NUMBER, in the one case that matters.
-- 0236 folds a subject's receipts by (recorded_at, id) and its comment defends
-- the tiebreak, but recorded_at is clock_timestamp() and id is gen_random_uuid()
-- -- so two receipts written inside one clock tick fold in whichever order two
-- random uuids happen to sort. Measured on this machine the gaps are tens of
-- microseconds and no tie occurred in two hundred inserts, which is exactly
-- what makes it dangerous: it is latent, not absent, and clock granularity is a
-- property of the host. A reducer whose answer depends on the machine it runs
-- on is not a reducer.
--
-- WHAT AN IDENTITY COLUMN DOES AND DOES NOT GIVE YOU. It cannot be written by
-- a caller and it is monotonic for every row inserted AFTER it exists, which is
-- what the fold needs from here on. It does NOT reconstruct history: adding one
-- to a populated table rewrites the table and numbers rows in HEAP order, and a
-- row that was ever updated has usually moved to the end of the heap. Every
-- receipt is updated once when its readback lands, and the material backfill
-- above rewrites all of them, so on a database that already holds receipts the
-- heap order is close to meaningless. Left that way the fold would be
-- deterministic, which was the goal, but it would freeze an ARBITRARY order and
-- the reducer's head and break point could differ across the migration for data
-- nobody touched.
--
-- So the existing rows are numbered explicitly, in the order the reducer used
-- before this migration -- recorded_at then id -- and the identity takes over
-- from the next row on. The immutability trigger stands down for that write for
-- the same reason it does for the material backfill, and is restored to ENABLE
-- ALWAYS, the state 0235 left it in.
alter table ops.write_receipt add column seq bigint;

do $$
declare next_seq bigint;
begin
  alter table ops.write_receipt disable trigger write_receipt_immutable;
  update ops.write_receipt w
     set seq = t.rn
    from (select id, row_number() over (order by recorded_at, id) as rn
            from ops.write_receipt) t
   where t.id = w.id;
  alter table ops.write_receipt enable always trigger write_receipt_immutable;

  select coalesce(max(seq), 0) + 1 into next_seq from ops.write_receipt;
  execute format(
    'alter table ops.write_receipt alter column seq set not null, '
    'alter column seq add generated always as identity (start with %s)', next_seq);
end $$;

comment on column ops.write_receipt.seq is
  'Insertion order, assigned by the database. The continuity reducer folds in '
  'this order; recorded_at is a timestamp for humans and ties on it are real.';

create index write_receipt_subject_seq_idx
  on ops.write_receipt (subject_type, subject_id, seq);

-- ------------------------------------------------------------- retraction
-- The acceptance bar in 0236 counts unproven receipts globally and a receipt
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
  -- A RETRACTION STAYS INSIDE ITS OWN TENANT. Without this, any holder of the
  -- runtime credential could disavow another tenant's receipt: carr_writer has
  -- INSERT across the whole table and there is no row-level policy behind it.
  -- The reducer drops a retracted receipt out of the fold, so a cross-tenant
  -- retraction erased another partner's proven history from continuity.
  if target.organization_tenant_id is distinct from new.organization_tenant_id then
    raise exception 'a retraction cannot cross tenants: % may not disavow a receipt of %',
      new.organization_tenant_id, target.organization_tenant_id;
  end if;
  -- AND IT MAY ONLY DISAVOW SOMETHING THE DATABASE NEVER CONFIRMED. Retraction
  -- exists to clear an unproven receipt off the acceptance bar. Applied to a
  -- PROVEN receipt it is not a repair, it is an erasure of confirmed history,
  -- and the operation that undoes a proven write is a reversal, which has to
  -- state where it puts the subject back.
  if target.is_proven then
    raise exception
      'receipt % is proven and cannot be retracted; reverse it instead, which '
      'has to say what state it restores', new.retracts_receipt_id;
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
-- THE BUG THIS FIXES. 0235 compared claimed_digest to target.prior_digest, and
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
  if target.organization_tenant_id is distinct from new.organization_tenant_id then
    raise exception 'a reversal cannot cross tenants: % may not reverse a receipt of %',
      new.organization_tenant_id, target.organization_tenant_id;
  end if;
  -- THE MESSAGE NO LONGER PRINTS THE TARGET'S PRIOR STATE. It used to, which
  -- turned this guard into an oracle: anyone holding a receipt id could read
  -- back the digest it was built on by offering a deliberately wrong reversal.
  -- Naming which guard refused does not require handing back the secret.
  if new.material_digest is distinct from target.prior_digest then
    raise exception
      'reversal is not exact: it does not restore the state its target built on';
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
-- The grants 0235 filed on that recipe are withdrawn EXPLICITLY, in the same
-- breath as the drop. In the database the drop removes its ACLs by itself, but
-- the canonical full-rebuild plan the staging bundle gates compare against is
-- COMPOSED FROM THE GRANT AND REVOKE STATEMENTS this chain writes, and it does
-- not model DROP. A drop with no matching revoke therefore leaves carr_writer
-- and carr_reader holding a planned grant on a function no rebuilt database
-- has, and the gates fail with "carr_reader differs from canonical
-- full-rebuild plan". This is the pairing 0235 itself used when it superseded
-- a recipe: revoke the old in the same migration that grants the new.
revoke all on function ops.write_receipt_digest(text,uuid,text,uuid,text)
  from carr_writer, carr_reader;
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

  -- SHADOWED SINCE SECTION (F), AND NAMED AS SUCH. Every check from here to the
  -- digest below now also runs at INSERT, where refusing is better than leaving
  -- a receipt permanently unprovable. A receipt that reaches this function has
  -- already passed all of them, so none of these branches can fire through the
  -- filing path. They are kept because this function is SECURITY DEFINER and
  -- executable by carr_writer on any receipt id: if the insert trigger were
  -- ever dropped, this is the last thing standing between a mislabelled receipt
  -- and a clean proof. Recorded as depth, not counted as tested.
  --
  -- THE RECEIPT MUST DESCRIBE ITS OWN EVIDENCE. Without the verb clause the
  -- digest proved only that SOME qualified call existed in this session, and a
  -- receipt asserting any verb at all over a log-activity row proved cleanly.
  -- Refused rather than left unproven, because an unproven receipt is a lasting
  -- mark on the acceptance bar and a mislabelled one is the caller's error.
  --
  -- HONESTY ABOUT THE OTHER TWO: the actor and tenant clauses are UNREACHABLE
  -- while 0232 stands, because 0232 already refuses a tool_call whose actor or
  -- tenant differs from its session's, and this receipt is already required to
  -- match that same session. No probe below exercises them and none can. They
  -- are kept as depth against a future weakening of 0232, and they are named
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

  -- A DISAVOWED RECEIPT CANNOT BE PROVED AFTERWARDS. Retraction is refused
  -- against a receipt that is already proven, but nothing stopped the reverse
  -- order: retract while unproven, prove the retraction, then come back and
  -- prove the original. Readback is one-way and final, so that left a receipt
  -- permanently proven and permanently retracted -- a state no honest sequence
  -- produces and every downstream reader disagrees about.
  if exists (select 1 from ops.write_receipt rr
              where rr.retracts_receipt_id = r.id and rr.is_proven) then
    raise exception
      'receipt % has already been retracted by a proven receipt and cannot now '
      'be proved', p_receipt_id;
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
  -- WHAT COUNTS AS A LIVE CLAIM. A retraction is bookkeeping, not a statement
  -- about subject state, so it is not a party to a disagreement; and a claim a
  -- proven receipt has disavowed is no longer being made. Both were previously
  -- treated as ordinary receipts here, which is how an honest retraction
  -- manufactured a conflict with the receipt it was repairing.
  with live as (
    select w.*
      from ops.write_receipt w
     where w.subject_type = p_subject_type
       and w.subject_id   = p_subject_id
       and w.retracts_receipt_id is null
       -- A PROVEN RECEIPT IS NEVER DROPPED, the same rule ops.continuity_reducer
       -- follows. Without this clause the two functions read one predicate two
       -- ways, and the gap between them is a false yes: file a receipt, retract
       -- it WHILE UNPROVEN so the retraction is accepted, prove the retraction,
       -- then prove the original. It is now proven AND retracted, this function
       -- drops it, and a second receipt forking the same prior stops being a
       -- conflict. Two proven receipts disagreeing about one subject, and the
       -- acceptance record says the database is clean.
       and (w.is_proven or not exists (
         select 1 from ops.write_receipt rr
          where rr.retracts_receipt_id = w.id and rr.is_proven))
  )
  select a.id, b.id, a.prior_digest
    from live a
    join live b
      on a.prior_digest    = b.prior_digest
     and a.material_digest <> b.material_digest
     -- TENANTS DO NOT DISAGREE WITH EACH OTHER. A subject id is a bare uuid and
     -- nothing ties one to a tenant, so without this a writer could file
     -- receipts naming ANOTHER tenant's subject, manufacture a conflict inside
     -- that tenant's chain, and block a phase acceptance it has no part in --
     -- the bar counts open conflicts across the whole database. The prior-state
     -- guard was written tenant-scoped and this was not, which is the kind of
     -- gap that only shows up when someone reads the two side by side.
     and a.organization_tenant_id = b.organization_tenant_id
     and a.id < b.id
   -- ONLY A PROVEN REVERSAL CLOSES ANYTHING. The earlier version accepted ANY
   -- row that named a side, with no test of proof and no test of whether that
   -- row had itself been disavowed. Under 0236 that was self-punishing: an
   -- unproven reversal was a permanent wall on the acceptance bar, so nobody
   -- could profit from one. Section (C) removed the punishment by making
   -- unproven receipts retractable, and removing the punishment without
   -- closing this hole made silencing a real conflict free -- fork a subject,
   -- reverse both sides with digests you never computed, then retract the
   -- reversals. The fork survives, proven and unretracted, and the bar reports
   -- a clean database.
     and not exists (
       select 1 from ops.write_receipt rev
        where rev.reverses_receipt_id in (a.id, b.id)
          and rev.is_proven
          and not exists (
            select 1 from ops.write_receipt rr2
             where rr2.retracts_receipt_id = rev.id and rr2.is_proven));
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
  -- KNOWN LIMIT, DISCLOSED RATHER THAN LEFT FOR A READER TO FIND: this folds a
  -- subject's receipts across ALL tenants, because its arguments do not name
  -- one. ops.receipt_conflicts is tenant-scoped and so is the prior-state
  -- guard, so nothing a second tenant writes can manufacture a conflict or a
  -- prior in another tenant's chain; what it can do is make this reducer's
  -- reading of a shared subject id look broken. The reducer gates nothing --
  -- the acceptance bar counts unproven receipts and open conflicts, never a
  -- reduced state -- so the exposure is a misleading read, not a false pass.
  -- Scoping it properly means a third argument and a signature change across
  -- every caller, which is a change to make deliberately, not inside this one.
  --
  -- A RETRACTION IS NOT A STATE TRANSITION, so it does not fold either. When
  -- it did, the repair displaced the damage: break_at named the retraction
  -- rather than the receipt that broke the chain, and head_digest became the
  -- material of a disavowal rather than the subject's state. Dropping both the
  -- retracted receipt AND its retractor leaves the chain reading as though
  -- neither had happened, which is what a retraction means.
  --
  -- A PROVEN RECEIPT IS NEVER DROPPED. Retracting a proven receipt is refused
  -- at insert, but a receipt can be retracted while unproven and proved
  -- afterwards; if that lands, the proof wins and the claim stays in the fold.
  for r in
    select * from ops.write_receipt w
     where w.subject_type = p_subject_type and w.subject_id = p_subject_id
       and w.retracts_receipt_id is null
       and (w.is_proven or not exists (
         select 1 from ops.write_receipt rr
          where rr.retracts_receipt_id = w.id and rr.is_proven))
     order by w.seq
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
  -- longer counts against the bar. Everything else about this count is as 0236
  -- left it: computed here, never supplied, and global rather than scoped.
  -- SCOPED TO THE ACCEPTING TENANT, because retraction is. The bar used to
  -- count every receipt in the database while the only mechanism for clearing
  -- one is confined to its own tenant, and that asymmetry is a denial route: a
  -- writer in one tenant leaves an unproven receipt and no one in another
  -- tenant can ever clear it, so acceptance is blocked for everybody until
  -- somebody holding a live session in the offending tenant acts. A bar you
  -- cannot clear from where you stand is the wall this migration exists to
  -- remove, wearing a different hat. Counting what the accepting party can
  -- actually answer for is both safer and more honest.
  select count(*) filter (where w.is_proven),
         count(*) filter (where not w.is_proven and not exists (
           select 1 from ops.write_receipt rr
            where rr.retracts_receipt_id = w.id
              and rr.is_proven
              and rr.organization_tenant_id = w.organization_tenant_id))
    into n_proven, n_unproven
    from ops.write_receipt w
   where w.organization_tenant_id = s.organization_tenant_id;

  -- SCOPED ON BOTH HALVES. Enumerating this tenant's subjects is not the same
  -- as counting this tenant's conflicts: a subject id is a bare uuid, so a
  -- writer in another tenant can file two conflicting receipts naming a subject
  -- of THIS one. Those two are same-tenant with each other, so the detector
  -- calls them a legal conflict, and they landed in this tenant's count with no
  -- remedy available here -- every closer is same-tenant by construction. That
  -- is the identical "bar you cannot clear from where you stand" this migration
  -- exists to remove, surviving in the half the earlier fix did not touch.
  select coalesce(sum(c), 0) into n_conflict from (
    select (select count(*) from ops.receipt_conflicts(w.subject_type, w.subject_id) rc
             join ops.write_receipt lw on lw.id = rc.left_receipt
            where lw.organization_tenant_id = s.organization_tenant_id) as c
      from (select distinct subject_type, subject_id from ops.write_receipt
             where organization_tenant_id = s.organization_tenant_id) w
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

  -- AND NEITHER MAY BE A RETRACTION OR A REVERSAL. Those carry caller-chosen
  -- material by design, because a reversal's material is the state its target
  -- built on and cannot be recomputed from its own rows. Every clause below
  -- compares material, so admitting them let two proven retractions carrying
  -- three string literals satisfy the whole gate and retire a dependency that
  -- nothing had repointed. A repoint and a recovery are ordinary writes about
  -- work that happened; they are the only thing this gate should accept.
  if repoint.retracts_receipt_id is not null or repoint.reverses_receipt_id is not null then
    raise exception
      'the repoint receipt % is a retraction or a reversal, which carries '
      'caller-chosen material and cannot evidence work that happened',
      new.repoint_receipt_id;
  end if;
  if recovery.retracts_receipt_id is not null or recovery.reverses_receipt_id is not null then
    raise exception
      'the recovery receipt % is a retraction or a reversal, which carries '
      'caller-chosen material and cannot evidence work that happened',
      new.recovery_receipt_id;
  end if;

  -- EACH RECEIPT MUST NAME THE DEPENDENCY BEING RETIRED. Without this, 0237
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
  -- THIS SHADOWS 0237's drive_retirement_distinct_receipts CHECK, and the next
  -- reader should know that rather than discover it. A receipt trivially shares
  -- its own call with itself, so passing one receipt for both roles now trips
  -- the same-call clause below, and a BEFORE trigger runs ahead of any check
  -- constraint. That constraint is therefore no longer reachable by any input.
  -- It is kept as depth against this trigger being dropped, and it is named
  -- here as shadowed rather than counted as a tested guard.
  --
  -- AND THE SAME-CALL CLAUSE BELOW IS ITSELF SHADOWED, one layer further in.
  -- Section (F) forces two receipts resting on one call and one subject to
  -- carry the SAME material, so the same-material clause catches that shape
  -- first and nothing isolates the same-call clause any more. Deleting it is
  -- still noticed -- its neighbour refuses instead -- but no probe names it.
  -- It is kept for the one shape (F) exempts, a pair of reversals or
  -- retractions, and it is recorded here as depth rather than as a tested guard.
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
  -- PROVEN, LIVE, AND IN THIS TENANT. An earlier version accepted material from
  -- ANY row on the subject, unproven and retracted rows included, which made
  -- the guard bootstrappable in three cheap steps: insert a junk receipt
  -- carrying the state you want, let the readback refuse it, use its material
  -- as your prior, then retract the junk so it stops counting anywhere. The
  -- result was a PROVEN receipt resting on a state nothing real ever produced,
  -- which is exactly what this guard exists to refuse. Restricting the source
  -- to proven, unretracted receipts removes the ladder, and the tenant clause
  -- stops the lookup doubling as a cross-tenant existence oracle.
  -- ONLY AN ORDINARY RECEIPT IS A SOURCE OF SUBJECT STATE. A retraction's
  -- material claim is constrained by NOTHING: this guard used to accept it,
  -- require_sound_retraction never looks at it, require_receipt_says_what_its_call_wrote
  -- explicitly exempts it, and require_exact_reversal returns early. Only a
  -- non-empty check survived. So a writer could file junk, let the readback
  -- refuse it, retract it while carrying a state of its own invention, and
  -- build on that invention -- the exact three-step bootstrap this guard was
  -- written to remove, rebuilt out of the retraction primitive added to close
  -- a different finding.
  --
  -- ops.receipt_conflicts already treats a retraction as NOT a statement about
  -- subject state and drops it from its live set. This guard treated the same
  -- row as a SOURCE of subject state. Both readings cannot be right, and the
  -- gap between them was free to walk through. A reversal is excluded on the
  -- same reasoning: its material is the state its TARGET built on, not a state
  -- its own call produced.
  if not exists (
    select 1 from ops.write_receipt w
     where w.subject_type           = new.subject_type
       and w.subject_id             = new.subject_id
       and w.organization_tenant_id = new.organization_tenant_id
       and w.material_digest        = new.prior_digest
       and w.is_proven
       and w.retracts_receipt_id is null
       and w.reverses_receipt_id is null
       and not exists (
         select 1 from ops.write_receipt rr
          where rr.retracts_receipt_id = w.id and rr.is_proven))
  then
    raise exception
      'receipt builds on a state this subject never reached (%); a prior digest '
      'must be ''origin'' or name material a PROVEN, unretracted receipt on this '
      'subject produced', new.prior_digest;
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
               -- COLLATE "C" ON EVERY SORT KEY, and it is load-bearing rather
               -- than tidy. Text ordering follows the database collation, so
               -- the same events folded under C and under en_US produce
               -- DIFFERENT digests. Field names carry underscores and
               -- new_value is punctuation-heavy JSON, which is precisely where
               -- collations disagree. Without this the local harness and the
               -- production database compute different material for identical
               -- writes, and a collation change during a major-version upgrade
               -- silently stops restatements being recognised as no-ops.
               order by coalesce(e.verb, '')            collate "C",
                        coalesce(e.field, '')           collate "C",
                        coalesce(e.old_value::text, '') collate "C",
                        coalesce(e.new_value::text, '') collate "C")
        from public.event e
       where e.idempotency_key      = p_tool_call_key
         and e.application_session_id = p_session
         and e.subject_type         = p_subject_type
         and e.subject_id           = p_subject_id), ''), 'UTF8')), 'hex');
$$;

revoke all on function ops.write_receipt_material_digest(text,uuid,text,uuid) from public;
grant execute on function ops.write_receipt_material_digest(text,uuid,text,uuid)
  to carr_writer, carr_reader;

-- ===== (G) A WRONG RETIREMENT MUST BE CORRECTABLE

-- THE DEFECT THIS CLOSES IS THE MIRROR OF THE ONE 0238 EXISTS FOR. The original
-- bug was a permanent REFUSAL: one unproven receipt barred acceptance forever
-- with no way back. 0237 shipped the opposite and nobody noticed, because it
-- only bites once something is wrong: ops.drive_retirement rows cannot be
-- updated, cannot be deleted, and one row per dependency was unique, so a
-- dependency retired in error stayed retired forever and readiness went on
-- reporting it. A permanent false yes is worse than a permanent no, because
-- nothing downstream ever asks again.
--
-- A WITHDRAWAL IS A NEW ROW, NEVER AN EDIT. Retirement records stay immutable;
-- correcting one means recording that it was withdrawn, so the mistake and its
-- correction are both on the record. Readiness then reads what is true now.
--
-- THE UNIQUE CONSTRAINT GOES, because a dependency withdrawn and later retired
-- properly needs a second row. Counting moves to DISTINCT dependencies so that
-- two rows for one dependency cannot inflate the retired total.
alter table ops.drive_retirement drop constraint drive_retirement_one_per_dependency;

create table ops.drive_retirement_withdrawal (
  id                     uuid primary key,
  drive_retirement_id    uuid not null unique references ops.drive_retirement(id),
  application_session_id uuid not null references ops.application_session(id),
  withdrawn_by_actor_id  uuid not null references public.actor(id),
  organization_tenant_id text not null,
  withdrawn_at           timestamptz not null default clock_timestamp(),
  note                   text not null,
  constraint drive_retirement_withdrawal_needs_a_note check (length(btrim(note)) > 0)
);

comment on table ops.drive_retirement_withdrawal is
  'Records that a drive retirement was made in error. Retirement rows stay '
  'immutable; this is how a false yes gets corrected without anyone editing '
  'history. Readiness counts only retirements with no withdrawal.';

create function ops.require_live_session_for_withdrawal()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  s ops.application_session%rowtype;
  r ops.drive_retirement%rowtype;
begin
  select * into s from ops.application_session
   where id = new.application_session_id for share;
  if not found then
    raise exception 'unknown application session % for withdrawal', new.application_session_id;
  end if;
  if s.revoked_at is not null then
    raise exception 'application session % is revoked', new.application_session_id;
  end if;
  if clock_timestamp() >= s.expires_at then
    raise exception 'application session % is expired', new.application_session_id;
  end if;
  if new.withdrawn_by_actor_id is distinct from s.actor_id then
    raise exception 'withdrawal names a different actor than its session';
  end if;
  if new.organization_tenant_id is distinct from s.organization_tenant_id then
    raise exception 'withdrawal names a different tenant than its session';
  end if;
  select * into r from ops.drive_retirement where id = new.drive_retirement_id;
  if r.organization_tenant_id is distinct from new.organization_tenant_id then
    raise exception 'a withdrawal cannot cross tenants';
  end if;
  -- STANDING. Withdrawing flips a dependency back to not-retired and readiness
  -- back to no, and withdrawals are immutable and unique per retirement, so
  -- each one is irreversible. With only the tenant checked, any automation
  -- actor in the tenant could withdraw a retirement it had no part in, and do
  -- it again after every re-retirement. Either you are the party that retired
  -- it, or you are a human -- the same standard phase acceptance holds, and
  -- for the same reason: undoing somebody else's recorded work is a partner's
  -- call, not a runtime credential's.
  -- ONLY THE PARTY THAT RETIRED IT. The earlier form allowed a stranger through
  -- if the actor was human, and that is not a bound against this credential:
  -- carr_writer holds UPDATE on public.actor (granted wholesale in 0004), so it
  -- can relabel its own actor as human, or insert a human one, and walk through.
  -- Any check that reads actor.kind is decoration while that grant stands.
  -- Narrowing to the retiring party needs no such check and cannot be forged
  -- from the writer's side, because the retirer is recorded on the row being
  -- withdrawn. A partner who needs to withdraw somebody else's retirement can
  -- still do it through the authority identity, which carr_writer does not hold.
  if new.withdrawn_by_actor_id is distinct from r.retired_by_actor_id then
    raise exception
      'only the party that retired a dependency may withdraw that retirement; '
      '% did not make it', new.withdrawn_by_actor_id;
  end if;
  return new;
end $$;

create trigger drive_retirement_withdrawal_requires_live_session
before insert on ops.drive_retirement_withdrawal
for each row execute function ops.require_live_session_for_withdrawal();
alter table ops.drive_retirement_withdrawal
  enable always trigger drive_retirement_withdrawal_requires_live_session;

create function ops.refuse_withdrawal_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'a withdrawal cannot be deleted';
  end if;
  raise exception 'a withdrawal cannot be rewritten';
end $$;

create trigger drive_retirement_withdrawal_immutable
before update or delete on ops.drive_retirement_withdrawal
for each row execute function ops.refuse_withdrawal_rewrite();
alter table ops.drive_retirement_withdrawal
  enable always trigger drive_retirement_withdrawal_immutable;

-- ONE LIVE RETIREMENT PER DEPENDENCY. The unique constraint had to go so that a
-- withdrawn dependency could be retired again, but dropping it left nothing at
-- all: a dependency could accumulate unbounded retirement rows, each costing
-- only two proven receipts. Readiness counts DISTINCT dependencies so the total
-- was never corrupted, but the record became a pile. This restores the
-- invariant the constraint carried, in the one form that still allows a
-- withdrawal to be followed by an honest re-retirement.
create function ops.require_one_live_retirement()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
begin
  -- SERIALISE ON THE DEPENDENCY FIRST. What this replaced was a UNIQUE
  -- CONSTRAINT, which is atomic; an existence read in a BEFORE trigger is not.
  -- Under READ COMMITTED neither of two concurrent inserts sees the other's
  -- uncommitted row, so both pass the check and both commit -- reproduced with
  -- two real connections. That matters beyond tidiness: a second live row makes
  -- the withdrawal correction path a no-op, because withdrawing one still
  -- leaves the dependency counted retired, and section (G) exists precisely so
  -- a wrong retirement can be undone. A transaction-scoped advisory lock keyed
  -- on the dependency makes the read-then-insert atomic without reintroducing a
  -- constraint that would forbid the honest retire-after-withdrawal case.
  perform pg_advisory_xact_lock(hashtext('drive_retirement:' || new.drive_dependency_id::text));
  if exists (
    select 1 from ops.drive_retirement r
     where r.drive_dependency_id = new.drive_dependency_id
       and r.id <> new.id
       and not exists (select 1 from ops.drive_retirement_withdrawal w
                        where w.drive_retirement_id = r.id))
  then
    raise exception
      'dependency % already has a retirement that has not been withdrawn; '
      'withdraw it before retiring the dependency again',
      new.drive_dependency_id;
  end if;
  return new;
end $$;

-- NAMED TO FIRE AFTER the evidence checks, since Postgres runs BEFORE triggers
-- in alphabetical order and `drive_retirement_requires_proven_receipts` must
-- speak first. Named `one_live_per_dependency` it sorted ahead of them and
-- masked every evidence refusal behind "this dependency is already retired",
-- which is true but is never the interesting reason.
create trigger drive_retirement_single_live_per_dependency
before insert on ops.drive_retirement
for each row execute function ops.require_one_live_retirement();
alter table ops.drive_retirement
  enable always trigger drive_retirement_single_live_per_dependency;

revoke all on function ops.require_one_live_retirement() from public;

create or replace function ops.drive_retirement_readiness()
returns table (
  operational_total bigint,
  retired_total     bigint,
  remaining         bigint,
  has_authority     boolean,
  ready             boolean
)
language sql stable
set search_path = pg_catalog, ops, public
as $$
  with op as (select count(*) n from ops.drive_dependency where operational),
       ret as (select count(distinct r.drive_dependency_id) n
                 from ops.drive_retirement r
                 join ops.drive_dependency d on d.id = r.drive_dependency_id
                where d.operational
                  and not exists (select 1 from ops.drive_retirement_withdrawal w
                                   where w.drive_retirement_id = r.id)),
       auth as (select count(*) n from ops.phase4_acceptance)
  select op.n, ret.n, op.n - ret.n, auth.n > 0,
         op.n > 0 and op.n = ret.n and auth.n > 0
    from op, ret, auth;
$$;

comment on function ops.drive_retirement_readiness() is
  'ready requires: at least one operational dependency on record, every one of '
  'them retired with two proven receipts and NOT withdrawn, and a phase '
  'acceptance only the authority identity can create. Counts DISTINCT '
  'dependencies, so repeated rows for one dependency cannot inflate the total. '
  'An empty inventory is NOT ready.';

grant select, insert on ops.drive_retirement_withdrawal to carr_writer;
grant select on ops.drive_retirement_withdrawal to carr_reader;
revoke update, delete on ops.drive_retirement_withdrawal from carr_writer;
revoke all on function ops.require_live_session_for_withdrawal() from public;
revoke all on function ops.refuse_withdrawal_rewrite() from public;

-- ===== (F) A RECEIPT MUST SAY WHAT ITS OWN CALL WROTE

-- WHAT WAS STILL WRONG AFTER (B). Binding the readback to the receipt's verb
-- and subject stopped a receipt proving against evidence recording a DIFFERENT
-- verb. It did not stop a receipt naming a subject its call never touched. One
-- honest log-activity call, whose single event moved one deal, could back any
-- number of PROVEN receipts on any subjects at all, each carrying whatever
-- material the caller felt like: a deal it never opened, a Drive dependency it
-- never repointed. The digest matched because the digest was computed from the
-- receipt's own claims, and nothing ever asked whether the call supported them.
--
-- The database already knew the answer and was never consulted.
-- ops.write_receipt_material_digest computes what a call actually wrote about a
-- subject, and it sat there as a convenience recipe for producers while the
-- guard that should have used it did not exist.
--
-- TWO CHECKS, AT INSERT, SO A RECEIPT LIKE THAT NEVER EXISTS:
--
--   THE CALL MUST HAVE TOUCHED THE SUBJECT. A receipt is a claim about what a
--   write did to a subject; if that call produced no event for that subject, it
--   did nothing to it and there is nothing to receipt.
--
--   AN ORDINARY RECEIPT MUST CARRY THE MATERIAL ITS CALL WROTE, recomputed here
--   rather than accepted. This is what finally makes is_proven mean something
--   about content instead of only about attachment.
--
-- REVERSALS AND RETRACTIONS ARE EXEMPT FROM THE SECOND CHECK, and must be: a
-- reversal's material is by construction the state its TARGET built on, never a
-- digest of its own rows, so a computed material could not express one at all.
-- They are NOT exempt from the first. Their call must still have touched the
-- subject they claim to repair.
--
-- WHAT THIS DOES NOT DO, stated plainly because the limit matters. An attacker
-- holding carr_writer can write event rows too, so this does not make
-- fabrication impossible. It makes fabrication CONSISTENT: a false receipt now
-- requires false events under the same frozen call, and the record can no
-- longer contain a proven receipt that its own evidence contradicts.
create function ops.require_receipt_says_what_its_call_wrote()
returns trigger language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  tc       public.tool_call%rowtype;
  computed text;
begin
  select * into tc from public.tool_call
   where idempotency_key = new.tool_call_idempotency_key;
  if not found then
    raise exception 'receipt names evidence that does not exist (%)',
      new.tool_call_idempotency_key;
  end if;
  if tc.application_session_id is null then
    raise exception 'receipt names LEGACY evidence, which vouches for nothing';
  end if;
  if tc.application_session_id is distinct from new.application_session_id then
    raise exception 'receipt names evidence written by a different session';
  end if;
  if tc.verb is distinct from new.verb then
    raise exception 'receipt claims verb % but its evidence records verb %',
      new.verb, tc.verb;
  end if;

  if not exists (
    select 1 from public.event e
     where e.idempotency_key        = new.tool_call_idempotency_key
       and e.application_session_id = new.application_session_id
       and e.subject_type           = new.subject_type
       and e.subject_id             = new.subject_id)
  then
    raise exception
      'receipt names subject %/% but its call wrote nothing about that subject',
      new.subject_type, new.subject_id;
  end if;

  if new.reverses_receipt_id is null and new.retracts_receipt_id is null then
    computed := ops.write_receipt_material_digest(
      new.tool_call_idempotency_key, new.application_session_id,
      new.subject_type, new.subject_id);
    if new.material_digest is distinct from computed then
      raise exception
        'receipt material does not match what its call wrote about %/%',
        new.subject_type, new.subject_id;
    end if;
  end if;
  return new;
end $$;

-- Named to fire after the session, retraction and reversal guards and before
-- the prior-state guard, which Postgres orders alphabetically:
-- requires_live_session, retraction_is_sound, reversal_is_exact,
-- says_what_its_call_wrote, state_existed.
create trigger write_receipt_says_what_its_call_wrote
before insert on ops.write_receipt
for each row execute function ops.require_receipt_says_what_its_call_wrote();
alter table ops.write_receipt
  enable always trigger write_receipt_says_what_its_call_wrote;

revoke all on function ops.require_receipt_says_what_its_call_wrote() from public;

-- ===== (H) AN EVENT A RECEIPT HAS PROVEN AGAINST STOPS BEING EDITABLE

-- THE COLLISION THIS RESOLVES, and it is a real one rather than an oversight.
-- 0232 froze public.tool_call against UPDATE and DELETE but froze public.event
-- against DELETE only, and said why ten lines below: update-decision and
-- detach-decision rewrite an event row in place, and detaching is this repo's
-- designed "nothing is deleted, the pointer is restated" retraction path.
-- Freezing UPDATE outright would mean a wrongly-attached decision could never
-- be retracted. That decision was correct when it was made.
--
-- Section (F) then made a receipt's material digest a recomputation over those
-- same event rows -- specifically over verb, field, old_value and new_value.
-- Both legitimate update paths rewrite new_value. So a receipt could prove, and
-- the rows it proved against could then be edited underneath it, leaving a
-- proven receipt whose stored material no longer matches what the database
-- would compute today. That is rejection reason 2 from 0235's own header --
-- "its readback proof relied on MUTABLE rows and proved no readback" --
-- reintroduced by a check added to close a different hole.
--
-- NEITHER REQUIREMENT HAS TO LOSE. The freeze applies only once a PROVEN
-- receipt rests on the event, and only to the four columns the digest folds.
-- An unreceipted decision stays as editable as it ever was, which is the case
-- update-decision and detach-decision exist for. A receipted one is frozen,
-- and the way to correct it is the one the substrate already provides: retract
-- the receipt, then file a new one against the corrected state. That is what
-- retraction is for, and it leaves both the mistake and the correction on the
-- record instead of rewriting history in place.
-- THE DIGEST IS A FOLD OVER A SET, so the freeze has to guard SET MEMBERSHIP,
-- not just one row's contents. The first version guarded a single shape and a
-- reviewer walked past it three ways, all reproduced:
--
--   APPEND. Adding a new event row to the same call and subject changes the set
--   the digest folds without touching any existing row, and the trigger was
--   BEFORE UPDATE only, so nothing fired at all.
--
--   MOVE OUT. Changing an event's subject_id leaves the four digest columns
--   untouched, so the early return fired and the row left the set silently.
--
--   THE WINDOW. Editing between filing a receipt and proving it: the freeze
--   looked for a PROVEN receipt, and at that moment there was not one yet.
--
-- All three end in the same place: a proven receipt whose stored material is not
-- what the database would compute today. The honest producer opens none of them
-- -- it proves in the same transaction and never appends afterwards -- but this
-- layer exists for the writer that is buggy rather than careful, and a retry or
-- a two-phase verb reaches the first one without trying.
--
-- ANY receipt now freezes the set, not merely a proven one, which closes the
-- window without costing the producer anything: within its own transaction the
-- producer has already written every event before it files the receipt.
create function ops.refuse_receipted_event_rewrite()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  key_    text;
  sess_   uuid;
  stype_  text;
  sid_    uuid;
begin
  if tg_op = 'INSERT' then
    key_ := new.idempotency_key; sess_ := new.application_session_id;
    stype_ := new.subject_type;  sid_ := new.subject_id;
  else
    -- On UPDATE the OLD identity is what matters: a row moving OUT of a receipted
    -- set is exactly as damaging as one being edited inside it.
    if (new.verb, new.field, new.old_value, new.new_value,
        new.idempotency_key, new.application_session_id, new.subject_type, new.subject_id)
       is not distinct from
       (old.verb, old.field, old.old_value, old.new_value,
        old.idempotency_key, old.application_session_id, old.subject_type, old.subject_id)
    then
      return new;                    -- nothing the digest reads, or keys on, has moved
    end if;
    key_ := old.idempotency_key; sess_ := old.application_session_id;
    stype_ := old.subject_type;  sid_ := old.subject_id;
  end if;

  if sess_ is null then
    return new;                      -- legacy evidence backs no receipt
  end if;
  if exists (
    select 1 from ops.write_receipt w
     where w.tool_call_idempotency_key = key_
       and w.application_session_id    = sess_
       and w.subject_type              = stype_
       and w.subject_id                = sid_)
  then
    raise exception
      'a write receipt rests on this call and subject, so the evidence it was '
      'filed against cannot be added to, moved or rewritten. Retract the receipt '
      'and file a new one against the corrected state.';
  end if;
  return new;
end $$;

create trigger event_receipted_content_frozen
before insert or update on public.event
for each row execute function ops.refuse_receipted_event_rewrite();
alter table public.event enable always trigger event_receipted_content_frozen;

revoke all on function ops.refuse_receipted_event_rewrite() from public;

-- THE TRIGGER ABOVE IS INVOKER-RIGHTS, so it reads ops.write_receipt as
-- whoever is updating the event. 0235 granted that read to carr_writer and
-- carr_reader only -- but carr_authority and carr_jobs both write public.event,
-- so the trigger fires for them and dies with "permission denied for table
-- write_receipt", freezing nothing and breaking every update those two roles
-- make. No rehearsal as the owner can see it, because grants never fire for
-- the owner; that is how set-lead stayed broken for five days.
--
-- Column-scoped, the form 0078 established: exactly the five columns the
-- trigger body reads, and no others. The receipt table's remaining columns
-- stay unreadable to both roles.
grant select (tool_call_idempotency_key, application_session_id, subject_type,
              subject_id, is_proven)
  on ops.write_receipt to carr_authority, carr_jobs;

-- --------------------------------------------------------------- apply-time
-- EXERCISES every guarantee this migration claims, and rolls all of it back.
--
-- EVERY RECEIPT BELOW IS BUILT THE WAY THE PRODUCER BUILDS ONE: write the event
-- rows first, let the database compute the material from them, then file the
-- receipt and prove it. The previous version of this block invented material
-- strings like 'm1' and never once called ops.write_receipt_material_digest,
-- which is how a hole in that function survived a probe whose stated purpose
-- was to test it. A fixture that cannot be produced proves nothing about the
-- database anyone runs.
--
-- THE ACCEPTANCE PROBES ARE BASELINE-AWARE. ops.accept_phase4 counts unproven
-- receipts and open conflicts across the WHOLE table. An earlier version simply
-- asserted that acceptance succeeds, which held only because its own rows were
-- the only rows -- so this migration refused to apply to any database that had
-- already taken traffic and carried one unproven receipt. It measures the
-- baseline first and says out loud when it cannot exercise the success half.
do $$
declare
  probe_actor uuid;
  sid         uuid := gen_random_uuid();
  subj        uuid := gen_random_uuid();
  other_subj  uuid := gen_random_uuid();
  ret_subj    uuid := gen_random_uuid();
  brk_subj    uuid := gen_random_uuid();
  cnf_subj    uuid := gen_random_uuid();
  dep         uuid;
  r1          uuid := gen_random_uuid();
  r2          uuid := gen_random_uuid();
  rev         uuid := gen_random_uuid();
  bad         uuid := gen_random_uuid();
  ret         uuid := gen_random_uuid();
  mat_a       text;
  mat_b       text;
  base_unproven bigint;
  base_conflict bigint;
  r           record;
  failed      boolean;
  k           text;
  sid2        uuid := gen_random_uuid();
  foreign_receipt uuid := gen_random_uuid();
  tsubj       uuid := gen_random_uuid();
  -- Every probe call uses its idempotency key as its request_hash, so the call
  -- digest for a subject is ops.write_receipt_digest(..., k, ..., subject).
begin
  select id into probe_actor from public.actor where kind = 'human' order by slug limit 1;
  if probe_actor is null then
    raise exception '0238 FAILED: need a human actor to exercise the split';
  end if;

  select count(*) filter (where not w.is_proven and not exists (
           select 1 from ops.write_receipt rr
            where rr.retracts_receipt_id = w.id and rr.is_proven))
    into base_unproven from ops.write_receipt w;
  select coalesce(sum(c), 0) into base_conflict from (
    select (select count(*) from ops.receipt_conflicts(w.subject_type, w.subject_id)) as c
      from (select distinct subject_type, subject_id from ops.write_receipt) w) t;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (sid, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
          'verified_partner', 'probe', clock_timestamp() + interval '1 hour');

  -- ============================================== (1) the honest path, twice
  k := 'p1-' || gen_random_uuid()::text;
  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (k, 'log-activity', probe_actor, k, '{}'::jsonb, 'carr-internal', sid);
  insert into public.event
    (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
     cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj,
          'stage', '"under-loi"'::jsonb, 'system', k, 'carr-internal', sid);
  mat_a := ops.write_receipt_material_digest(k, sid, 'deal', subj);

  -- The call digest is bound to the subject: the same call digested for another
  -- subject must be a different value, or proof is transferable between them.
  if ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid,
                              k, 'deal', subj)
   = ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal', sid,
                              k, 'deal', other_subj) then
    raise exception '0238 FAILED: the call digest is identical for two different subjects';
  end if;

  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest)
  values (r1, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', subj, k,
          ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                   sid, k, 'deal', subj),
          mat_a, 'origin');
  if not ops.prove_write_receipt(r1) then
    raise exception '0238 FAILED: an honest receipt did not prove after the split';
  end if;

  -- (2) A RECEIPT MUST SAY WHAT ITS OWN CALL WROTE. Three separate refusals,
  -- each isolated so only the clause under test can fire.
  --
  -- (2a) the call wrote nothing about this subject
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', other_subj, k,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, k, 'deal', other_subj),
            ops.write_receipt_material_digest(k, sid, 'deal', other_subj), 'origin');
  exception when others then
    failed := true;
    if position('wrote nothing about that subject' in sqlerrm) = 0 then
      raise exception '0238 FAILED: untouched-subject receipt refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0238 FAILED: a receipt named a subject its call never wrote to';
  end if;

  -- (2b) the material is not what the call wrote
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, k, 'deal', subj),
            'THE-DEAL-CLOSED-AT-FORTY-DOLLARS', mat_a);
  exception when others then
    failed := true;
    if position('does not match what its call wrote' in sqlerrm) = 0 then
      raise exception '0238 FAILED: false-material receipt refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0238 FAILED: a receipt carried material its call never wrote';
  end if;

  -- (2c) the verb disagrees with the frozen evidence
  declare
    kv text := 'p1v-' || gen_random_uuid()::text;
  begin
    insert into public.tool_call
      (idempotency_key, verb, actor_id, request_hash, response,
       organization_tenant_id, application_session_id)
    values (kv, 'update-deal', probe_actor, kv, '{}'::jsonb, 'carr-internal', sid);
    insert into public.event
      (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
       cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(), probe_actor, 'update-deal', 'deal', subj,
            'stage', '"closed"'::jsonb, 'system', kv, 'carr-internal', sid);
    failed := false;
    begin
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest)
      values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', subj, kv,
              ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                       sid, kv, 'deal', subj),
              ops.write_receipt_material_digest(kv, sid, 'deal', subj), mat_a);
    exception when others then
      failed := true;
      if position('claims verb' in sqlerrm) = 0 then
        raise exception '0238 FAILED: verb mismatch refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a receipt claimed a verb its evidence does not record';
    end if;
  end;

  -- ================================ (3) the headline fix: a reversal can prove
  k := 'p2-' || gen_random_uuid()::text;
  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (k, 'log-activity', probe_actor, k, '{}'::jsonb, 'carr-internal', sid);
  insert into public.event
    (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
     cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj,
          'stage', '"closed"'::jsonb, 'system', k, 'carr-internal', sid);
  mat_b := ops.write_receipt_material_digest(k, sid, 'deal', subj);
  if mat_b = mat_a then
    raise exception '0238 FAILED: two DIFFERENT changes hashed to the same material';
  end if;
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest)
  values (r2, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', subj, k,
          ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                   sid, k, 'deal', subj),
          mat_b, mat_a);
  if not ops.prove_write_receipt(r2) then
    raise exception '0238 FAILED: the second honest receipt did not prove';
  end if;

  -- A reversal restores the state its target built on. Its material is by
  -- construction NOT a digest of its own rows, which is why reversals are
  -- exempt from the material rule -- and that exemption is exercised here
  -- rather than assumed.
  k := 'p3-' || gen_random_uuid()::text;
  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (k, 'log-activity', probe_actor, k, '{}'::jsonb, 'carr-internal', sid);
  insert into public.event
    (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
     cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj,
          'stage', '"under-loi"'::jsonb, 'system', k, 'carr-internal', sid);
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest, reverses_receipt_id)
  values (rev, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', subj, k,
          ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                   sid, k, 'deal', subj),
          mat_a, mat_b, r2);
  if not ops.prove_write_receipt(rev) then
    raise exception '0238 FAILED: an EXACT REVERSAL still cannot prove -- the '
                    'defect this migration exists to remove is still present';
  end if;

  -- An inexact reversal refuses, by its own guard.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest, reverses_receipt_id)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, k, 'deal', subj),
            'not-the-prior-state', mat_a, r2);
  exception when others then
    failed := true;
    if position('reversal is not exact' in sqlerrm) = 0 then
      raise exception '0238 FAILED: inexact reversal refused by the WRONG guard: %', sqlerrm;
    end if;
    -- AND THE MESSAGE MUST NOT HAND BACK THE SECRET. It used to print the
    -- target's prior digest, which turned this guard into an oracle: anyone
    -- holding a receipt id could read the state it was built on by offering a
    -- deliberately wrong reversal. Naming which guard refused never requires
    -- disclosing the value that made it refuse.
    if position(mat_a in sqlerrm) > 0 then
      raise exception '0238 FAILED: the reversal refusal disclosed its target''s prior state';
    end if;
  end;
  if not failed then
    raise exception '0238 FAILED: an inexact reversal was accepted';
  end if;

  -- AND THE SAME-SUBJECT CLAUSE ON REVERSAL, which had no probe at all while
  -- the identical clause on retraction had two. r2 lives on subj; this names
  -- other_subj, and its call did write about other_subj, so only the reversal's
  -- own subject check can refuse it.
  declare
    ko text := 'p3o-' || gen_random_uuid()::text;
  begin
    insert into public.tool_call
      (idempotency_key, verb, actor_id, request_hash, response,
       organization_tenant_id, application_session_id)
    values (ko, 'log-activity', probe_actor, ko, '{}'::jsonb, 'carr-internal', sid);
    insert into public.event
      (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
       cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(), probe_actor, 'log-activity', 'deal', other_subj,
            'stage', '"x"'::jsonb, 'system', ko, 'carr-internal', sid);
    failed := false;
    begin
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest, reverses_receipt_id)
      values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', other_subj, ko,
              ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                       sid, ko, 'deal', other_subj),
              mat_a, 'origin', r2);
    exception when others then
      failed := true;
      if position('same subject as the receipt it reverses' in sqlerrm) = 0 then
        raise exception '0238 FAILED: cross-subject reversal refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a reversal named a different subject than its target';
    end if;
  end;

  -- ============ (6) 'broken' must still be reachable, and must name the damage
  -- This is the whole reason the prior guard checks EXISTENCE and not RECENCY.
  -- The shape is a late-arriving restatement: b4 repeats the transition b2
  -- already made. Its prior is real and proven, so it is admitted; it is not
  -- the head, so the fold finds a gap; and it AGREES with b2 about material, so
  -- it is not a conflict. That leaves 'broken' as the only state it can produce.
  declare
    b1 uuid := gen_random_uuid(); b2 uuid := gen_random_uuid();
    b3 uuid := gen_random_uuid(); b4 uuid := gen_random_uuid();
    kb1 text := 'p7a-' || gen_random_uuid()::text;
    kb2 text := 'p7b-' || gen_random_uuid()::text;
    kb3 text := 'p7c-' || gen_random_uuid()::text;
    kb4 text := 'p7d-' || gen_random_uuid()::text;
    mb1 text; mb2 text; mb3 text; mb4 text;
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kb1,'log-activity',probe_actor,kb1,'{}'::jsonb,'carr-internal',sid),
           (kb2,'log-activity',probe_actor,kb2,'{}'::jsonb,'carr-internal',sid),
           (kb3,'log-activity',probe_actor,kb3,'{}'::jsonb,'carr-internal',sid),
           (kb4,'log-activity',probe_actor,kb4,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',brk_subj,'stage','"s1"'::jsonb,'system',kb1,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',brk_subj,'stage','"s2"'::jsonb,'system',kb2,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',brk_subj,'stage','"s3"'::jsonb,'system',kb3,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',brk_subj,'stage','"s2"'::jsonb,'system',kb4,'carr-internal',sid);
    mb1 := ops.write_receipt_material_digest(kb1, sid, 'deal', brk_subj);
    mb2 := ops.write_receipt_material_digest(kb2, sid, 'deal', brk_subj);
    mb3 := ops.write_receipt_material_digest(kb3, sid, 'deal', brk_subj);
    mb4 := ops.write_receipt_material_digest(kb4, sid, 'deal', brk_subj);
    if mb4 is distinct from mb2 then
      raise exception '0238 FAILED: the same change written twice hashed differently, '
                      'so an idempotent restatement would read as a new link';
    end if;

    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (b1,sid,probe_actor,'carr-internal','log-activity','deal',brk_subj,kb1,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kb1,'deal',brk_subj),
            mb1,'origin');
    perform ops.prove_write_receipt(b1);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (b2,sid,probe_actor,'carr-internal','log-activity','deal',brk_subj,kb2,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kb2,'deal',brk_subj),
            mb2,mb1);
    perform ops.prove_write_receipt(b2);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (b3,sid,probe_actor,'carr-internal','log-activity','deal',brk_subj,kb3,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kb3,'deal',brk_subj),
            mb3,mb2);
    perform ops.prove_write_receipt(b3);

    select * into r from ops.continuity_reducer('deal', brk_subj);
    if r.state <> 'continuous' then
      raise exception '0238 FAILED: an unbroken chain did not reduce to continuous (got %)', r.state;
    end if;

    -- The refusal path is caught and RENAMED on purpose. A rule demanding the
    -- prior be the CURRENT HEAD would reject this insert, and the raw message
    -- would send the next reader hunting a fixture bug instead of telling them
    -- what actually broke.
    begin
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (b4,sid,probe_actor,'carr-internal','log-activity','deal',brk_subj,kb4,
              ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kb4,'deal',brk_subj),
              mb4,mb1);
    exception when others then
      raise exception '0238 FAILED: a STALE BUT REAL prior state was refused (%), '
                      'which makes the reducer''s BROKEN state unreachable. The '
                      'prior guard must check that a state EXISTED, never that '
                      'it is the latest one.', sqlerrm;
    end;
    perform ops.prove_write_receipt(b4);

    select * into r from ops.continuity_reducer('deal', brk_subj);
    if r.state <> 'broken' then
      raise exception '0238 FAILED: a stale-but-real prior no longer produces a BROKEN chain (got %)', r.state;
    end if;
    if r.break_at is distinct from b4 then
      raise exception '0238 FAILED: the reducer did not name where the chain broke';
    end if;
    if r.conflict_count <> 0 then
      raise exception '0238 FAILED: a restatement that AGREES about material was counted as a conflict';
    end if;
    if r.head_digest is distinct from mb2 then
      raise exception '0238 FAILED: the reducer head is not the last MATERIAL claim';
    end if;

    -- (7) A FABRICATED PRIOR IS REFUSED, and so is the bootstrap that used to
    -- get around it: file a junk receipt carrying the state you want, let the
    -- readback refuse it, then use its material as your prior. An unproven
    -- receipt is not a state this subject reached.
    begin
      failed := false;
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (gen_random_uuid(),sid,probe_actor,'carr-internal','log-activity','deal',brk_subj,kb4,
              ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kb4,'deal',brk_subj),
              mb4,'a-state-nobody-produced');
    exception when others then
      failed := true;
      if position('never reached' in sqlerrm) = 0 then
        raise exception '0238 FAILED: a fabricated prior refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a receipt built on a state its subject never reached';
    end if;

    declare
      junk uuid := gen_random_uuid();
      kj text := 'p7j-' || gen_random_uuid()::text;
      mj text;
    begin
      insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
        response, organization_tenant_id, application_session_id)
      values (kj,'log-activity',probe_actor,kj,'{}'::jsonb,'carr-internal',sid);
      insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
        field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
      values (clock_timestamp(),probe_actor,'log-activity','deal',brk_subj,'stage','"junk"'::jsonb,'system',kj,'carr-internal',sid);
      mj := ops.write_receipt_material_digest(kj, sid, 'deal', brk_subj);
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (junk,sid,probe_actor,'carr-internal','log-activity','deal',brk_subj,kj,
              'a-digest-i-never-computed', mj, mb2);
      if ops.prove_write_receipt(junk) then
        raise exception '0238 FAILED: the bootstrap fixture proved';
      end if;
      failed := false;
      begin
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest)
        values (gen_random_uuid(),sid,probe_actor,'carr-internal','log-activity','deal',brk_subj,kj,
                ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kj,'deal',brk_subj),
                mj, mj);
      exception when others then
        failed := true;
        if position('never reached' in sqlerrm) = 0 then
          raise exception '0238 FAILED: the bootstrap refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: an UNPROVEN receipt was usable as a prior state, '
                        'so the prior guard can be bootstrapped with junk';
      end if;
    end;
  end;

  -- ================================ (4) retraction, and what it may not touch
  k := 'p4-' || gen_random_uuid()::text;
  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (k, 'log-activity', probe_actor, k, '{}'::jsonb, 'carr-internal', sid);
  insert into public.event
    (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
     cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(), probe_actor, 'log-activity', 'deal', ret_subj,
          'stage', '"opened"'::jsonb, 'system', k, 'carr-internal', sid),
         -- and one for `subj` too, so the proven-retraction probe below is
         -- refused by the retraction guard rather than by the guard that checks
         -- a call wrote about the subject its receipt names
         (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj,
          'stage', '"also-here"'::jsonb, 'system', k, 'carr-internal', sid);
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest)
  values (bad, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', ret_subj, k,
          'a-digest-nobody-wrote',
          ops.write_receipt_material_digest(k, sid, 'deal', ret_subj), 'origin');
  if ops.prove_write_receipt(bad) then
    raise exception '0238 FAILED: a receipt claiming a digest it never wrote was PROVEN';
  end if;

  -- A PROVEN RECEIPT CANNOT BE RETRACTED. Retraction clears an unconfirmed
  -- claim off the bar; applied to a proven receipt it is an erasure of
  -- confirmed history, and the reducer drops a retracted receipt out of the
  -- fold, so this is how one writer quietly deletes another's continuity.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest, retracts_receipt_id)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, k, 'deal', subj),
            mat_a, mat_a, r1);
  exception when others then
    failed := true;
    if position('is proven and cannot be retracted' in sqlerrm) = 0 then
      raise exception '0238 FAILED: retracting a proven receipt refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0238 FAILED: a PROVEN receipt was retracted';
  end if;

  -- A RETRACTION MUST NAME THE SAME SUBJECT AS WHAT IT RETRACTS. The call
  -- behind it wrote about both subjects, so the subject clause is the only
  -- guard that can refuse this.
  begin
    failed := false;
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest, retracts_receipt_id)
    values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
            'deal', subj, k,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, k, 'deal', subj),
            'a-retraction-states-no-material', mat_a, bad);
  exception when others then
    failed := true;
    if position('same subject as the receipt it retracts' in sqlerrm) = 0 then
      raise exception '0238 FAILED: cross-subject retraction refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0238 FAILED: a retraction named a different subject than its target';
  end if;

  -- A RETRACTION CANNOT CROSS TENANTS. carr_writer holds INSERT across the
  -- whole table with no row-level policy behind it, so without this any holder
  -- of the runtime credential could disavow another tenant's receipt.
  declare
    kt   text := 'p4t-' || gen_random_uuid()::text;
    krt  text := 'p4rt-' || gen_random_uuid()::text;
  begin
    insert into ops.application_session
      (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
       authorization_class, verified_subject, expires_at)
    values (sid2, probe_actor, 'other-tenant', 'joe', 'probe', 'probe-issuer',
            'verified_partner', 'probe', clock_timestamp() + interval '1 hour');
    insert into public.tool_call
      (idempotency_key, verb, actor_id, request_hash, response,
       organization_tenant_id, application_session_id)
    values (kt, 'log-activity', probe_actor, kt, '{}'::jsonb, 'other-tenant', sid2);
    insert into public.event
      (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
       cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(), probe_actor, 'log-activity', 'deal', tsubj,
            'stage', '"theirs"'::jsonb, 'system', kt, 'other-tenant', sid2);
    insert into ops.write_receipt
      (id, application_session_id, actor_id, organization_tenant_id, verb,
       subject_type, subject_id, tool_call_idempotency_key,
       call_digest, material_digest, prior_digest)
    values (foreign_receipt, sid2, probe_actor, 'other-tenant', 'log-activity',
            'deal', tsubj, kt, 'not-the-right-digest',
            ops.write_receipt_material_digest(kt, sid2, 'deal', tsubj), 'origin');
    if ops.prove_write_receipt(foreign_receipt) then
      raise exception '0238 FAILED: the cross-tenant fixture proved when it should not';
    end if;

    insert into public.tool_call
      (idempotency_key, verb, actor_id, request_hash, response,
       organization_tenant_id, application_session_id)
    values (krt, 'log-activity', probe_actor, krt, '{}'::jsonb, 'carr-internal', sid);
    insert into public.event
      (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
       cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(), probe_actor, 'log-activity', 'deal', tsubj,
            'stage', '"mine"'::jsonb, 'system', krt, 'carr-internal', sid);
    failed := false;
    begin
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest, retracts_receipt_id)
      values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', tsubj, krt,
              ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                       sid, krt, 'deal', tsubj),
              'whatever', 'origin', foreign_receipt);
    exception when others then
      failed := true;
      if position('cannot cross tenants' in sqlerrm) = 0 then
        raise exception '0238 FAILED: cross-tenant retraction refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: one tenant disavowed another tenant''s receipt';
    end if;

    -- And the same boundary on reversal.
    failed := false;
    begin
      insert into ops.write_receipt
        (id, application_session_id, actor_id, organization_tenant_id, verb,
         subject_type, subject_id, tool_call_idempotency_key,
         call_digest, material_digest, prior_digest, reverses_receipt_id)
      values (gen_random_uuid(), sid, probe_actor, 'carr-internal', 'log-activity',
              'deal', tsubj, krt,
              ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                       sid, krt, 'deal', tsubj),
              'origin', 'origin', foreign_receipt);
    exception when others then
      failed := true;
      if position('cannot cross tenants' in sqlerrm) = 0 then
        raise exception '0238 FAILED: cross-tenant reversal refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: one tenant reversed another tenant''s receipt';
    end if;
  end;

  -- THE HONEST RETRACTION. bad is unproven, so its material is NOT an eligible
  -- prior; 'origin' is, and always is.
  k := 'p5-' || gen_random_uuid()::text;
  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (k, 'log-activity', probe_actor, k, '{}'::jsonb, 'carr-internal', sid);
  insert into public.event
    (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
     cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(), probe_actor, 'log-activity', 'deal', ret_subj,
          'stage', '"withdrawn"'::jsonb, 'system', k, 'carr-internal', sid);
  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest, retracts_receipt_id)
  values (ret, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', ret_subj, k,
          ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                   sid, k, 'deal', ret_subj),
          'a-retraction-states-no-material', 'origin', bad);
  if not ops.prove_write_receipt(ret) then
    raise exception '0238 FAILED: an honest retraction could not prove';
  end if;

  -- A RETRACTION IS NOT A PARTY TO A CONFLICT, and neither is what it retracts.
  -- bad and ret share the prior 'origin' and assert different material, which
  -- under the earlier definition made the repair itself a conflict.
  if (select count(*) from ops.receipt_conflicts('deal', ret_subj)) <> 0 then
    raise exception '0238 FAILED: an honest retraction manufactured a conflict '
                    'with the receipt it was repairing';
  end if;

  -- AND BOTH LEAVE THE FOLD. The chain must read as though neither happened.
  select * into r from ops.continuity_reducer('deal', ret_subj);
  if r.receipt_count <> 0 or r.state <> 'empty' then
    raise exception '0238 FAILED: a retracted receipt and its retractor did not '
                    'both leave the fold (count %, state %)', r.receipt_count, r.state;
  end if;

  -- =========================== (5) an unproven reversal closes nothing
  declare
    c1 uuid := gen_random_uuid();
    c2 uuid := gen_random_uuid();
    kc1 text := 'p6a-' || gen_random_uuid()::text;
    kc2 text := 'p6b-' || gen_random_uuid()::text;
    kc3 text := 'p6c-' || gen_random_uuid()::text;
    kc4 text := 'p6d-' || gen_random_uuid()::text;
    m1 text; m2 text;
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kc1, 'log-activity', probe_actor, kc1, '{}'::jsonb, 'carr-internal', sid),
           (kc2, 'log-activity', probe_actor, kc2, '{}'::jsonb, 'carr-internal', sid),
           (kc3, 'log-activity', probe_actor, kc3, '{}'::jsonb, 'carr-internal', sid),
           (kc4, 'log-activity', probe_actor, kc4, '{}'::jsonb, 'carr-internal', sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(), probe_actor, 'log-activity', 'deal', cnf_subj,
            'stage', '"branch-a"'::jsonb, 'system', kc1, 'carr-internal', sid),
           (clock_timestamp(), probe_actor, 'log-activity', 'deal', cnf_subj,
            'stage', '"branch-b"'::jsonb, 'system', kc2, 'carr-internal', sid),
           (clock_timestamp(), probe_actor, 'log-activity', 'deal', cnf_subj,
            'stage', '"undo"'::jsonb,     'system', kc3, 'carr-internal', sid),
           (clock_timestamp(), probe_actor, 'log-activity', 'deal', cnf_subj,
            'stage', '"undo2"'::jsonb,    'system', kc4, 'carr-internal', sid);
    m1 := ops.write_receipt_material_digest(kc1, sid, 'deal', cnf_subj);
    m2 := ops.write_receipt_material_digest(kc2, sid, 'deal', cnf_subj);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (c1, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', cnf_subj, kc1,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, kc1, 'deal', cnf_subj), m1, 'origin');
    perform ops.prove_write_receipt(c1);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (c2, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', cnf_subj, kc2,
            ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                     sid, kc2, 'deal', cnf_subj), m2, 'origin');
    perform ops.prove_write_receipt(c2);
    if (select count(*) from ops.receipt_conflicts('deal', cnf_subj)) <> 1 then
      raise exception '0238 FAILED: two receipts disagreeing about material did not conflict';
    end if;

    -- An UNPROVEN reversal of one side. Under the earlier definition this alone
    -- closed the conflict, and section (C) then made it free to clean up after
    -- itself: fork a subject, silence it with digests you never computed, then
    -- retract the silencers. The fork survived, proven, and the bar was clean.
    declare
      fake_rev uuid := gen_random_uuid();
    begin
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest,
        reverses_receipt_id)
      values (fake_rev, sid, probe_actor, 'carr-internal', 'log-activity', 'deal',
              cnf_subj, kc3, 'I-NEVER-COMPUTED-THIS', 'origin', m2, c2);
      if ops.prove_write_receipt(fake_rev) then
        raise exception '0238 FAILED: the unproven-reversal fixture proved';
      end if;
      if (select count(*) from ops.receipt_conflicts('deal', cnf_subj)) <> 1 then
        raise exception '0238 FAILED: an UNPROVEN reversal silenced a real conflict';
      end if;
      -- and it may not be laundered away by retracting it either
      declare
        launder uuid := gen_random_uuid();
      begin
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest,
          retracts_receipt_id)
        values (launder, sid, probe_actor, 'carr-internal', 'log-activity', 'deal',
                cnf_subj, kc4,
                ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                         sid, kc4, 'deal', cnf_subj),
                'a-retraction-states-no-material', 'origin', fake_rev);
        perform ops.prove_write_receipt(launder);
        if (select count(*) from ops.receipt_conflicts('deal', cnf_subj)) <> 1 then
          raise exception '0238 FAILED: retracting a lying reversal laundered the conflict away';
        end if;
      end;
    end;

    -- A PROVEN reversal closes it, which is the half that must still work.
    declare
      real_rev uuid := gen_random_uuid();
      kc5 text := 'p6e-' || gen_random_uuid()::text;
    begin
      insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
        response, organization_tenant_id, application_session_id)
      values (kc5, 'log-activity', probe_actor, kc5, '{}'::jsonb, 'carr-internal', sid);
      insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
        field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
      values (clock_timestamp(), probe_actor, 'log-activity', 'deal', cnf_subj,
              'stage', '"real-undo"'::jsonb, 'system', kc5, 'carr-internal', sid);
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest,
        reverses_receipt_id)
      values (real_rev, sid, probe_actor, 'carr-internal', 'log-activity', 'deal',
              cnf_subj, kc5,
              ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                       sid, kc5, 'deal', cnf_subj),
              'origin', m2, c2);
      if not ops.prove_write_receipt(real_rev) then
        raise exception '0238 FAILED: an honest reversal could not prove';
      end if;
      if (select count(*) from ops.receipt_conflicts('deal', cnf_subj)) <> 0 then
        raise exception '0238 FAILED: a PROVEN exact reversal did not close the conflict';
      end if;
    end;
  end;

  -- (7f) ONE TENANT CANNOT MANUFACTURE A CONFLICT IN ANOTHER'S CHAIN. Both
  -- receipts name the same subject uuid and the same prior, and assert
  -- different material, which is the exact shape of a conflict -- and they are
  -- in different tenants, so it is not one.
  declare
    kx text := 'p7x-' || gen_random_uuid()::text;
    ky text := 'p7y-' || gen_random_uuid()::text;
    shared uuid := gen_random_uuid();
    x1 uuid := gen_random_uuid();
    y1 uuid := gen_random_uuid();
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kx,'log-activity',probe_actor,kx,'{}'::jsonb,'carr-internal',sid),
           (ky,'log-activity',probe_actor,ky,'{}'::jsonb,'other-tenant',sid2);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',shared,'stage','"mine"'::jsonb,'system',kx,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',shared,'stage','"theirs"'::jsonb,'system',ky,'other-tenant',sid2);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (x1,sid,probe_actor,'carr-internal','log-activity','deal',shared,kx,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kx,'deal',shared),
            ops.write_receipt_material_digest(kx,sid,'deal',shared),'origin');
    perform ops.prove_write_receipt(x1);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (y1,sid2,probe_actor,'other-tenant','log-activity','deal',shared,ky,
            ops.write_receipt_digest('log-activity',probe_actor,'other-tenant',sid2,ky,'deal',shared),
            ops.write_receipt_material_digest(ky,sid2,'deal',shared),'origin');
    perform ops.prove_write_receipt(y1);
    if (select count(*) from ops.receipt_conflicts('deal', shared)) <> 0 then
      raise exception '0238 FAILED: two TENANTS writing the same subject id were '
                      'reported as being in conflict, so one tenant can block '
                      'another tenant''s phase acceptance';
    end if;
  end;

  -- ================================== (8) the material recipe's own clauses
  -- All three were unproven before: a reviewer deleted the subject clause from
  -- the aggregate and both proof surfaces still passed, because the subject is
  -- also folded into the hash prefix and two subjects therefore still differed
  -- while the aggregate swept in every other subject's events.
  declare
    ks text := 'p8-' || gen_random_uuid()::text;
    s1 uuid := gen_random_uuid();
    s2 uuid := gen_random_uuid();
    before_other text;
    sid3 uuid := gen_random_uuid();
    ka  text := 'p8a-' || gen_random_uuid()::text;
    kb  text := 'p8b-' || gen_random_uuid()::text;
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (ks,'log-activity',probe_actor,ks,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',s1,'stage','"one"'::jsonb,'system',ks,'carr-internal',sid);
    before_other := ops.write_receipt_material_digest(ks, sid, 'deal', s1);

    -- ANOTHER SUBJECT'S EVENT UNDER THE SAME CALL MUST NOT MOVE THIS DIGEST.
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',s2,'stage','"two"'::jsonb,'system',ks,'carr-internal',sid);
    if ops.write_receipt_material_digest(ks, sid, 'deal', s1) is distinct from before_other then
      raise exception '0238 FAILED: an unrelated subject''s event under the same call '
                      'changed this subject''s material digest, so the aggregate is '
                      'not scoped to its subject';
    end if;
    if ops.write_receipt_material_digest(ks, sid, 'deal', s2) = before_other then
      raise exception '0238 FAILED: two different subjects under one call hashed the same';
    end if;

    -- AND IT MUST BE SCOPED TO ITS SESSION.
    insert into ops.application_session (id, actor_id, organization_tenant_id,
      sponsoring_human_slug, via, auth_issuer, authorization_class, verified_subject, expires_at)
    values (sid3, probe_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
            'verified_partner', 'probe', clock_timestamp() + interval '1 hour');
    if ops.write_receipt_material_digest(ks, sid3, 'deal', s1) = before_other then
      raise exception '0238 FAILED: the material digest ignores which session wrote the events';
    end if;

    -- AND IT MUST NOT DEPEND ON THE ORDER THE ROWS WERE INSERTED IN.
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (ka,'log-activity',probe_actor,ka,'{}'::jsonb,'carr-internal',sid),
           (kb,'log-activity',probe_actor,kb,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',s1,'a_field','"alpha"'::jsonb,'system',ka,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',s1,'b-field','"beta"'::jsonb,'system',ka,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',s1,'b-field','"beta"'::jsonb,'system',kb,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',s1,'a_field','"alpha"'::jsonb,'system',kb,'carr-internal',sid);
    if ops.write_receipt_material_digest(ka, sid, 'deal', s1)
       is distinct from ops.write_receipt_material_digest(kb, sid, 'deal', s1) then
      raise exception '0238 FAILED: the same two changes hashed differently depending on '
                      'the order their rows were written';
    end if;
  end;

  -- ================= (9) a receipt is a reversal or a retraction, never both
  -- Both halves are individually valid here, so every trigger passes and the
  -- CHECK CONSTRAINT is the only guard left that can refuse.
  k := 'p9-' || gen_random_uuid()::text;
  insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
    response, organization_tenant_id, application_session_id)
  values (k,'log-activity',probe_actor,k,'{}'::jsonb,'carr-internal',sid);
  insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
    field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(),probe_actor,'log-activity','deal',ret_subj,'stage','"both"'::jsonb,'system',k,'carr-internal',sid);
  begin
    failed := false;
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest,
      reverses_receipt_id, retracts_receipt_id)
    values (gen_random_uuid(),sid,probe_actor,'carr-internal','log-activity','deal',ret_subj,k,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,k,'deal',ret_subj),
            'origin','origin', ret, bad);
  exception when others then
    failed := true;
    if position('write_receipt_reverses_xor_retracts' in sqlerrm) = 0 then
      raise exception '0238 FAILED: a reverse-and-retract receipt refused by the WRONG guard: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0238 FAILED: one receipt both reversed and retracted';
  end if;

  -- ============================ (10) the immutability tuple covers the new fields
  -- The file's own rule is that a field left out of this tuple is a field a
  -- receipt can be rewritten through. 0238 added two fields to the row and both
  -- went untested. Rolled back, because the fixture must be an UNPROVEN receipt
  -- with no readback yet and leaving one behind would sit on the bar.
  begin
    declare
      mut uuid := gen_random_uuid();
      km  text := 'p10-' || gen_random_uuid()::text;
    begin
      insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
        response, organization_tenant_id, application_session_id)
      values (km,'log-activity',probe_actor,km,'{}'::jsonb,'carr-internal',sid);
      insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
        field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
      values (clock_timestamp(),probe_actor,'log-activity','deal',subj,'stage','"mut"'::jsonb,'system',km,'carr-internal',sid);
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (mut,sid,probe_actor,'carr-internal','log-activity','deal',subj,km,
              ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,km,'deal',subj),
              ops.write_receipt_material_digest(km, sid, 'deal', subj), mat_a);

      -- THE UPDATE THAT COULD ACTUALLY SLIP THROUGH records a readback at the
      -- same time as it rewrites a field. An update that only rewrites is
      -- caught by the final branch ('the only permitted update is recording
      -- the readback') whatever the identity tuple contains, so an earlier
      -- version of this probe passed while the tuple went untested.
      failed := false;
      begin
        update ops.write_receipt
           set material_digest = 'rewritten', readback_digest = 'x',
               readback_at = clock_timestamp()
         where id = mut;
      exception when others then
        failed := true;
        if position('identity is immutable' in sqlerrm) = 0 then
          raise exception '0238 FAILED: rewriting material refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a receipt''s MATERIAL CLAIM was rewritten in place';
      end if;

      failed := false;
      begin
        update ops.write_receipt
           set retracts_receipt_id = bad, readback_digest = 'x',
               readback_at = clock_timestamp()
         where id = mut;
      exception when others then
        failed := true;
        if position('identity is immutable' in sqlerrm) = 0 then
          raise exception '0238 FAILED: rewriting the retraction link refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a receipt was turned into a retraction after the fact';
      end if;
    end;
    raise exception 'ROLLBACK_IMMUTABILITY_PROBE';
  exception when others then
    if sqlerrm <> 'ROLLBACK_IMMUTABILITY_PROBE' then raise; end if;
  end;

  -- ================================ (11) the five-argument recipe is really gone
  -- The migration says leaving the subject-blind recipe callable would leave the
  -- defect callable. Dynamic SQL, because a direct call would fail to compile.
  begin
    failed := false;
    execute 'select ops.write_receipt_digest($1,$2,$3,$4,$5)'
      using 'log-activity', probe_actor, 'carr-internal', sid, 'rh';
  exception when others then
    failed := true;
    if position('does not exist' in sqlerrm) = 0 then
      raise exception '0238 FAILED: the five-argument recipe refused for the WRONG reason: %', sqlerrm;
    end if;
  end;
  if not failed then
    raise exception '0238 FAILED: the subject-blind five-argument digest is still callable';
  end if;

  -- ================================================ (12) the acceptance bar
  -- SWEEP FIRST. Every unproven receipt this probe created gets a proven
  -- retraction naming its own subject, which is the mechanism section (C) adds
  -- and the only honest way to reach a clean bar. Each retraction needs an
  -- event for its target's subject under the sweeping call, because a receipt
  -- must say what its own call wrote.
  declare
    sweep_a text := 'p12a-' || gen_random_uuid()::text;
    sweep_b text := 'p12b-' || gen_random_uuid()::text;
    sk text;
    ssid uuid;
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (sweep_a,'log-activity',probe_actor,sweep_a,'{}'::jsonb,'carr-internal',sid),
           (sweep_b,'log-activity',probe_actor,sweep_b,'{}'::jsonb,'other-tenant',sid2);
    for r in
      select w.id, w.subject_type, w.subject_id, w.application_session_id,
             w.organization_tenant_id
        from ops.write_receipt w
       where w.application_session_id in (sid, sid2)
         and not w.is_proven
         and not exists (select 1 from ops.write_receipt rr
                          where rr.retracts_receipt_id = w.id and rr.is_proven)
       order by w.seq
    loop
      if r.organization_tenant_id = 'carr-internal' then
        sk := sweep_a; ssid := sid;
      else
        sk := sweep_b; ssid := sid2;
      end if;
      insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
        field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
      values (clock_timestamp(), probe_actor, 'log-activity', r.subject_type,
              r.subject_id, 'stage', '"swept"'::jsonb, 'system', sk,
              r.organization_tenant_id, ssid);
      declare
        sweep_id uuid := gen_random_uuid();
      begin
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest,
          retracts_receipt_id)
        values (sweep_id, ssid, probe_actor, r.organization_tenant_id, 'log-activity',
                r.subject_type, r.subject_id, sk,
                ops.write_receipt_digest('log-activity', probe_actor,
                  r.organization_tenant_id, ssid, sk, r.subject_type, r.subject_id),
                'a-retraction-states-no-material', 'origin', r.id);
        if not ops.prove_write_receipt(sweep_id) then
          raise exception '0238 FAILED: a sweep retraction of % failed to prove', r.id;
        end if;
      end;
    end loop;
  end;

  -- BASELINE-AWARE. accept_phase4 counts unproven receipts and open conflicts
  -- across the WHOLE table, so on a database that already carried either, the
  -- success half cannot be exercised without disavowing rows this probe does
  -- not own. It says so rather than failing the migration, which is what an
  -- earlier version did to every database that had ever taken traffic.
  if base_unproven = 0 and base_conflict = 0 then
    perform ops.accept_phase4(gen_random_uuid(), sid,
      '0238 probe: the bar clears once every unproven receipt is proven-retracted');
  else
    raise notice '0238: acceptance-success probe SKIPPED -- this database already '
                 'carries % unretracted unproven receipt(s) and % open conflict(s) '
                 'that predate the migration. The refusal half still ran.',
                 base_unproven, base_conflict;
  end if;

  -- AN UNPROVEN RETRACTION CLEARS NOTHING, and this needs THREE levels to see.
  -- The obvious two-level shape cannot test the rule at all: an unproven
  -- retraction is itself an unproven receipt, so the bar refuses either way and
  -- a mutant that dropped the is_proven test survives. Stack one more level and
  -- the two readings finally disagree -- under the real rule the first receipt
  -- is excused only by an UNPROVEN receipt and still counts, while a mutant
  -- that ignored proof would excuse it, then excuse its unproven retractor in
  -- turn, and accept a phase resting on a receipt nobody ever proved.
  --
  -- Runs only on a database whose baseline is clean, for the same reason the
  -- success probe above does, and is rolled back either way.
  if base_unproven = 0 and base_conflict = 0 then
    begin
      declare
        u_subj uuid := gen_random_uuid();
        u1 uuid := gen_random_uuid();
        ur uuid := gen_random_uuid();
        ur2 uuid := gen_random_uuid();
        ku text := 'p12u-' || gen_random_uuid()::text;
      begin
        insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
          response, organization_tenant_id, application_session_id)
        values (ku,'log-activity',probe_actor,ku,'{}'::jsonb,'carr-internal',sid);
        insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
          field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
        values (clock_timestamp(),probe_actor,'log-activity','deal',u_subj,'stage','"u"'::jsonb,'system',ku,'carr-internal',sid);
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest)
        values (u1,sid,probe_actor,'carr-internal','log-activity','deal',u_subj,ku,
                'nobody-computed-this',
                ops.write_receipt_material_digest(ku, sid, 'deal', u_subj),'origin');
        if ops.prove_write_receipt(u1) then
          raise exception '0238 FAILED: the three-level fixture proved when it should not';
        end if;
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest,
          retracts_receipt_id)
        values (ur,sid,probe_actor,'carr-internal','log-activity','deal',u_subj,ku,
                'nor-this','a-retraction-states-no-material','origin',u1);
        if ops.prove_write_receipt(ur) then
          raise exception '0238 FAILED: the unproven retraction proved';
        end if;
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest,
          retracts_receipt_id)
        values (ur2,sid,probe_actor,'carr-internal','log-activity','deal',u_subj,ku,
                ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,ku,'deal',u_subj),
                'a-retraction-states-no-material','origin',ur);
        if not ops.prove_write_receipt(ur2) then
          raise exception '0238 FAILED: an honest second-level retraction could not prove';
        end if;
        failed := false;
        begin
          perform ops.accept_phase4(gen_random_uuid(), sid, 'probe: unproven retraction');
        exception when others then
          failed := true;
          if position('phase4_acceptance_no_unproven_receipts' in sqlerrm) = 0 then
            raise exception '0238 FAILED: acceptance refused by the WRONG bar: %', sqlerrm;
          end if;
        end;
        if not failed then
          raise exception '0238 FAILED: an UNPROVEN retraction cleared the acceptance bar';
        end if;
      end;
      raise exception 'ROLLBACK_THREE_LEVEL';
    exception when others then
      if sqlerrm <> 'ROLLBACK_THREE_LEVEL' then raise; end if;
    end;
  end if;


  -- CROSS-TENANT DENIAL. Placed BEFORE the retirement probes below, which
  -- deliberately leave open conflicts in this tenant: with those present the
  -- bar refuses on conflicts and the clause under test never speaks, which is
  -- how the first version of this probe passed while testing nothing.
  if base_unproven = 0 and base_conflict = 0 then
    declare
      kx text := 'p14d-' || gen_random_uuid()::text;
      foreign_junk uuid := gen_random_uuid();
      fsubj uuid := gen_random_uuid();
    begin
      insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
        response, organization_tenant_id, application_session_id)
      values (kx,'log-activity',probe_actor,kx,'{}'::jsonb,'other-tenant',sid2);
      insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
        field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
      values (clock_timestamp(),probe_actor,'log-activity','deal',fsubj,'stage','"theirs"'::jsonb,'system',kx,'other-tenant',sid2);
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (foreign_junk,sid2,probe_actor,'other-tenant','log-activity','deal',fsubj,kx,
              'nobody-computed-this',
              ops.write_receipt_material_digest(kx, sid2, 'deal', fsubj),'origin');
      if ops.prove_write_receipt(foreign_junk) then
        raise exception '0238 FAILED: the cross-tenant denial fixture proved';
      end if;
      -- ASSERT THE FOREIGN RECEIPT IS NOT THE REASON, rather than requiring a
      -- clean bar. By this point the retirement probes above have deliberately
      -- left open conflicts in this tenant, so acceptance may well refuse --
      -- but it must never refuse because of an unproven receipt belonging to
      -- somebody else. Under the old global count it refused on exactly that.
      begin
        perform ops.accept_phase4(gen_random_uuid(), sid,
          '0238 probe: another tenant''s unproven receipt must not block this one');
      exception when others then
        if position('phase4_acceptance_no_unproven_receipts' in sqlerrm) > 0 then
          raise exception '0238 FAILED: another tenant''s unproven receipt blocked '
                          'acceptance here, and nobody in this tenant can clear it';
        end if;
      end;
    end;
    end if;


  -- =========================== (13) Drive retirement, and correcting a wrong one
  insert into ops.drive_dependency (source_path, reference, classification, operational)
  values ('tools/split-probe.py:1', '{{VAULT}}', 'vault-path', true)
  returning id into dep;
  declare
    p0 uuid := gen_random_uuid(); p1 uuid := gen_random_uuid();
    p1b uuid := gen_random_uuid(); p2 uuid := gen_random_uuid();
    p3 uuid := gen_random_uuid(); p4 uuid := gen_random_uuid();
    p5 uuid := gen_random_uuid(); p6 uuid := gen_random_uuid();
    p7 uuid := gen_random_uuid(); p8 uuid := gen_random_uuid();
    kd0 text := 'pd0-' || gen_random_uuid()::text;
    kd1 text := 'pd1-' || gen_random_uuid()::text;
    kd2 text := 'pd2-' || gen_random_uuid()::text;
    kd3 text := 'pd3-' || gen_random_uuid()::text;
    kd4 text := 'pd4-' || gen_random_uuid()::text;
    kd5 text := 'pd5-' || gen_random_uuid()::text;
    kd6 text := 'pd6-' || gen_random_uuid()::text;
    kd7 text := 'pd7-' || gen_random_uuid()::text;
    kd8 text := 'pd8-' || gen_random_uuid()::text;
    m0 text; m1 text; m3 text; m5 text; m6 text; m7 text; m8 text;
    retirement_id uuid := gen_random_uuid();
    rdy record;
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kd0,'log-activity',probe_actor,kd0,'{}'::jsonb,'carr-internal',sid),
           (kd1,'log-activity',probe_actor,kd1,'{}'::jsonb,'carr-internal',sid),
           (kd2,'log-activity',probe_actor,kd2,'{}'::jsonb,'carr-internal',sid),
           (kd3,'log-activity',probe_actor,kd3,'{}'::jsonb,'carr-internal',sid),
           (kd4,'log-activity',probe_actor,kd4,'{}'::jsonb,'carr-internal',sid),
           (kd5,'log-activity',probe_actor,kd5,'{}'::jsonb,'carr-internal',sid),
           (kd6,'log-activity',probe_actor,kd6,'{}'::jsonb,'carr-internal',sid),
           (kd7,'log-activity',probe_actor,kd7,'{}'::jsonb,'carr-internal',sid),
           (kd8,'log-activity',probe_actor,kd8,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"vault"'::jsonb,'system',kd0,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"repointed"'::jsonb,'system',kd1,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"repointed"'::jsonb,'system',kd2,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"recovered"'::jsonb,'system',kd3,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"recovered"'::jsonb,'system',kd4,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"repointed-again"'::jsonb,'system',kd5,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"recovered-again"'::jsonb,'system',kd6,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"repointed-third"'::jsonb,'system',kd7,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','drive_dependency',dep,'reader','"recovered-third"'::jsonb,'system',kd8,'carr-internal',sid);
    m0 := ops.write_receipt_material_digest(kd0, sid, 'drive_dependency', dep);
    m1 := ops.write_receipt_material_digest(kd1, sid, 'drive_dependency', dep);
    m3 := ops.write_receipt_material_digest(kd3, sid, 'drive_dependency', dep);
    m5 := ops.write_receipt_material_digest(kd5, sid, 'drive_dependency', dep);
    m6 := ops.write_receipt_material_digest(kd6, sid, 'drive_dependency', dep);
    m7 := ops.write_receipt_material_digest(kd7, sid, 'drive_dependency', dep);
    m8 := ops.write_receipt_material_digest(kd8, sid, 'drive_dependency', dep);

    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p0,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd0,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd0,'drive_dependency',dep),m0,'origin');
    perform ops.prove_write_receipt(p0);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p1,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd1,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd1,'drive_dependency',dep),m1,m0);
    perform ops.prove_write_receipt(p1);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p1b,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd1,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd1,'drive_dependency',dep),m1,m1);
    perform ops.prove_write_receipt(p1b);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p2,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd2,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd2,'drive_dependency',dep),m1,m1);
    perform ops.prove_write_receipt(p2);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p3,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd3,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd3,'drive_dependency',dep),m3,m0);
    perform ops.prove_write_receipt(p3);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p4,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd4,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd4,'drive_dependency',dep),m3,m1);
    perform ops.prove_write_receipt(p4);

    -- Receipts that name something OTHER than the dependency.
    begin
      failed := false;
      insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
        recovery_receipt_id, application_session_id, retired_by_actor_id,
        organization_tenant_id, note)
      values (gen_random_uuid(), dep, r1, r2, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('does not name dependency' in sqlerrm) = 0 then
        raise exception '0238 FAILED: unrelated-receipt retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a dependency was retired with receipts that never named it';
    end if;

    -- SAME CALL. p1b builds on the repoint and, being the same call, cannot
    -- differ in material -- which is why the same-call clause has to come first.
    begin
      failed := false;
      insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
        recovery_receipt_id, application_session_id, retired_by_actor_id,
        organization_tenant_id, note)
      values (gen_random_uuid(), dep, p1, p1b, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('rest on the SAME call' in sqlerrm) = 0 then
        raise exception '0238 FAILED: same-call retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a dependency was retired on two receipts about ONE call';
    end if;

    -- SAME MATERIAL, different call, and it builds on the repoint, so only the
    -- same-material clause is left standing.
    begin
      failed := false;
      insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
        recovery_receipt_id, application_session_id, retired_by_actor_id,
        organization_tenant_id, note)
      values (gen_random_uuid(), dep, p1, p2, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('assert the SAME material state' in sqlerrm) = 0 then
        raise exception '0238 FAILED: same-material retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a dependency was retired on two receipts asserting the same thing';
    end if;

    -- A RECOVERY THAT IGNORED THE REPOINT: different call, different material.
    begin
      failed := false;
      insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
        recovery_receipt_id, application_session_id, retired_by_actor_id,
        organization_tenant_id, note)
      values (gen_random_uuid(), dep, p1, p3, sid, probe_actor, 'carr-internal', 'probe');
    exception when others then
      failed := true;
      if position('does not build on the repointed state' in sqlerrm) = 0 then
        raise exception '0238 FAILED: uncoupled-recovery retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a recovery receipt that ignored the repoint was accepted';
    end if;

    -- THE HONEST PATH.
    insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
      recovery_receipt_id, application_session_id, retired_by_actor_id,
      organization_tenant_id, note)
    values (retirement_id, dep, p1, p4, sid, probe_actor, 'carr-internal',
            'probe: repointed, then recovered from the repointed state');
    select * into rdy from ops.drive_retirement_readiness();
    if rdy.remaining <> 0 then
      raise exception '0238 FAILED: a retired dependency still counted as remaining';
    end if;

    -- AND A WRONG ONE MUST BE CORRECTABLE. Before this, a dependency retired in
    -- error stayed retired forever: the row could not be updated, could not be
    -- deleted, and was unique per dependency, so readiness went on reporting a
    -- yes nobody could withdraw.
    insert into ops.drive_retirement_withdrawal
      (id, drive_retirement_id, application_session_id, withdrawn_by_actor_id,
       organization_tenant_id, note)
    values (gen_random_uuid(), retirement_id, sid, probe_actor, 'carr-internal',
            'probe: the readers were never actually repointed');
    select * into rdy from ops.drive_retirement_readiness();
    if rdy.remaining <> 1 then
      raise exception '0238 FAILED: a WITHDRAWN retirement still counted as retired';
    end if;
    if rdy.ready then
      raise exception '0238 FAILED: readiness said yes with a withdrawn retirement';
    end if;

    -- A withdrawal is itself a record, not an edit.
    begin
      failed := false;
      update ops.drive_retirement_withdrawal set note = 'rewritten'
       where drive_retirement_id = retirement_id;
    exception when others then
      failed := true;
      if position('cannot be rewritten' in sqlerrm) = 0 then
        raise exception '0238 FAILED: withdrawal rewrite refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a withdrawal was rewritten';
    end if;

    -- AND THE DEPENDENCY CAN THEN BE RETIRED PROPERLY, which the old unique
    -- constraint made impossible.
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p5,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd5,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd5,'drive_dependency',dep),m5,m1);
    perform ops.prove_write_receipt(p5);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p6,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd6,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd6,'drive_dependency',dep),m6,m5);
    perform ops.prove_write_receipt(p6);
    insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
      recovery_receipt_id, application_session_id, retired_by_actor_id,
      organization_tenant_id, note)
    values (gen_random_uuid(), dep, p5, p6, sid, probe_actor, 'carr-internal',
            'probe: retired again, properly, after the withdrawal');
    select * into rdy from ops.drive_retirement_readiness();
    if rdy.remaining <> 0 then
      raise exception '0238 FAILED: a dependency could not be retired again after '
                      'its first retirement was withdrawn';
    end if;
    -- A SECOND LIVE RETIREMENT IS NOW REFUSED OUTRIGHT. Dropping the unique
    -- constraint (so a withdrawn dependency could be retired again) left
    -- nothing bounding the rows at all. The bound is back in the one form that
    -- still permits an honest re-retirement after a withdrawal, and it is the
    -- clause under test here.
    --
    -- IT ALSO SHADOWS THE DISTINCT COUNT in readiness, which is named as depth
    -- rather than counted as tested: with at most one live row per dependency,
    -- count(distinct ...) and count(*) can no longer disagree, so no fixture
    -- can tell them apart. It stays because it is the correct expression of
    -- what readiness means, and because this trigger could be dropped.
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p7,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd7,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd7,'drive_dependency',dep),m7,m6);
    perform ops.prove_write_receipt(p7);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (p8,sid,probe_actor,'carr-internal','log-activity','drive_dependency',dep,kd8,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kd8,'drive_dependency',dep),m8,m7);
    perform ops.prove_write_receipt(p8);
    failed := false;
    begin
      insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
        recovery_receipt_id, application_session_id, retired_by_actor_id,
        organization_tenant_id, note)
      values (gen_random_uuid(), dep, p7, p8, sid, probe_actor, 'carr-internal',
              'probe: a second live retirement for one dependency');
    exception when others then
      failed := true;
      if position('has a retirement that has not been withdrawn' in sqlerrm) = 0 then
        raise exception '0238 FAILED: second live retirement refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: one dependency accumulated two live retirements';
    end if;
    select * into rdy from ops.drive_retirement_readiness();
    if rdy.retired_total <> 1 then
      raise exception '0238 FAILED: two live retirement rows for one dependency inflated '
                      'the retired total (got %)', rdy.retired_total;
    end if;
  end;

  -- ============ (14) what the second security review found, and what closed it
  -- Every probe below corresponds to a hole a reviewer reproduced as carr_writer
  -- against the previous version of this file. They are grouped because they
  -- share one root: the repairs added in section (C) created primitives that
  -- carry caller-chosen material, and three separate guards then accepted those
  -- primitives as evidence of subject state.
  declare
    kq  text := 'p14a-' || gen_random_uuid()::text;
    kr  text := 'p14b-' || gen_random_uuid()::text;
    ks2 text := 'p14c-' || gen_random_uuid()::text;
    v_subj uuid := gen_random_uuid();
    junk uuid := gen_random_uuid();
    vret uuid := gen_random_uuid();
    m_ok text;
    ev_id uuid;
    machine_actor uuid;
    a_retirement uuid;
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kq,'log-activity',probe_actor,kq,'{}'::jsonb,'carr-internal',sid),
           (kr,'log-activity',probe_actor,kr,'{}'::jsonb,'carr-internal',sid),
           (ks2,'log-activity',probe_actor,ks2,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',v_subj,'stage','"one"'::jsonb,'system',kq,'carr-internal',sid)
    returning id into ev_id;
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',v_subj,'stage','"two"'::jsonb,'system',kr,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',v_subj,'stage','"three"'::jsonb,'system',ks2,'carr-internal',sid);
    m_ok := ops.write_receipt_material_digest(kq, sid, 'deal', v_subj);

    -- (14a) AN EVENT A PROVEN RECEIPT RESTS ON CANNOT BE REWRITTEN. The material
    -- digest is recomputed from event rows, and carr_writer holds UPDATE on
    -- them, so without this the readback proved against a surface the writer
    -- could edit afterwards -- the defect 0235's own header lists as the second
    -- reason an earlier version of this layer was rejected.
    declare rr1 uuid := gen_random_uuid();
    begin
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (rr1,sid,probe_actor,'carr-internal','log-activity','deal',v_subj,kq,
              ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kq,'deal',v_subj),
              m_ok,'origin');
      if not ops.prove_write_receipt(rr1) then
        raise exception '0238 FAILED: the section 14 fixture receipt did not prove';
      end if;
      failed := false;
      begin
        update public.event set new_value = '"TAMPERED"'::jsonb where id = ev_id;
      exception when others then
        failed := true;
        if position('cannot be added to, moved or rewritten' in sqlerrm) = 0 then
          raise exception '0238 FAILED: event rewrite refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: an event a receipt rests on was rewritten '
                        'underneath it';
      end if;

      -- AND AN UNRECEIPTED EVENT STAYS EDITABLE, which is the whole reason 0232
      -- left UPDATE open: update-decision and detach-decision rewrite an event
      -- in place, and detaching is this repo's designed retraction path. A
      -- freeze that broke those would be a worse bug than the one it fixed.
      -- THE THREE ESCAPES A REVIEWER WALKED THROUGH, each closed and each
      -- probed. A digest folded over a SET is only as frozen as its membership.
      --
      -- APPEND: a new row joining the set changes the fold without touching a
      -- single existing row.
      failed := false;
      begin
        insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
          field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
        values (clock_timestamp(),probe_actor,'log-activity','deal',v_subj,'price','"9999"'::jsonb,'system',kq,'carr-internal',sid);
      exception when others then
        failed := true;
        if position('cannot be added to, moved or rewritten' in sqlerrm) = 0 then
          raise exception '0238 FAILED: appending to receipted evidence refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a row was APPENDED to the evidence a receipt '
                        'was filed against, changing what the digest folds';
      end if;

      -- MOVE OUT: the four digest columns are untouched, so a check that looked
      -- only at those returned early and let the row leave the set.
      failed := false;
      begin
        update public.event set subject_id = gen_random_uuid() where id = ev_id;
      exception when others then
        failed := true;
        if position('cannot be added to, moved or rewritten' in sqlerrm) = 0 then
          raise exception '0238 FAILED: moving receipted evidence refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: an event was MOVED OUT of the set a receipt was '
                        'proved against, without touching a digest column';
      end if;

      -- THE WINDOW: edit after filing and before proving. A freeze that looked
      -- for a PROVEN receipt could not see one yet, so ordering alone defeated
      -- it. ANY receipt freezes the set now.
      declare
        kw text := 'p14w-' || gen_random_uuid()::text;
        wsubj uuid := gen_random_uuid();
        wev uuid;
        wrid uuid := gen_random_uuid();
      begin
        insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
          response, organization_tenant_id, application_session_id)
        values (kw,'log-activity',probe_actor,kw,'{}'::jsonb,'carr-internal',sid);
        insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
          field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
        values (clock_timestamp(),probe_actor,'log-activity','deal',wsubj,'stage','"before"'::jsonb,'system',kw,'carr-internal',sid)
        returning id into wev;
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest)
        values (wrid,sid,probe_actor,'carr-internal','log-activity','deal',wsubj,kw,
                ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kw,'deal',wsubj),
                ops.write_receipt_material_digest(kw, sid, 'deal', wsubj),'origin');
        failed := false;
        begin
          update public.event set new_value = '"rewritten under the receipt"'::jsonb
           where id = wev;
        exception when others then
          failed := true;
          if position('cannot be added to, moved or rewritten' in sqlerrm) = 0 then
            raise exception '0238 FAILED: in-window rewrite refused by the WRONG guard: %', sqlerrm;
          end if;
        end;
        if not failed then
          raise exception '0238 FAILED: evidence was rewritten between filing a receipt '
                          'and proving it, so ordering alone defeats the freeze';
        end if;
        if not ops.prove_write_receipt(wrid) then
          raise exception '0238 FAILED: an untouched receipt did not prove';
        end if;
      end;

      -- AND A COLUMN THE DIGEST DOES NOT READ STAYS EDITABLE EVEN WHEN THE
      -- EVENT IS RECEIPTED. update-decision rewrites human_quote and
      -- agent_rationale alongside new_value; only the four columns the material
      -- digest folds are frozen, so annotating a receipted decision is still
      -- possible. Without this probe a freeze that swallowed every update to a
      -- receipted event would pass every other check here.
      update public.event set agent_rationale = 'annotated after the receipt'
       where id = ev_id;

      declare untouched uuid;
      begin
        insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
          field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
        values (clock_timestamp(),probe_actor,'log-decision','deal',gen_random_uuid(),
                'stage','"attached"'::jsonb,'system',ks2,'carr-internal',sid)
        returning id into untouched;
        update public.event set new_value = '"detached"'::jsonb where id = untouched;
      end;

      -- (14b) A RETRACTION IS NOT A SOURCE OF SUBJECT STATE. Its material is
      -- constrained by nothing, so accepting it as a prior rebuilt the exact
      -- bootstrap the prior guard exists to refuse, out of the retraction
      -- primitive added to close a different finding.
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (junk,sid,probe_actor,'carr-internal','log-activity','deal',v_subj,kr,
              'a-digest-nobody-computed',
              ops.write_receipt_material_digest(kr, sid, 'deal', v_subj), m_ok);
      if ops.prove_write_receipt(junk) then
        raise exception '0238 FAILED: the section 14 junk receipt proved';
      end if;
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest,
        retracts_receipt_id)
      values (vret,sid,probe_actor,'carr-internal','log-activity','deal',v_subj,kr,
              ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kr,'deal',v_subj),
              'A-STATE-THIS-SUBJECT-NEVER-REACHED', m_ok, junk);
      if not ops.prove_write_receipt(vret) then
        raise exception '0238 FAILED: the section 14 retraction did not prove';
      end if;
      failed := false;
      begin
        insert into ops.write_receipt (id, application_session_id, actor_id,
          organization_tenant_id, verb, subject_type, subject_id,
          tool_call_idempotency_key, call_digest, material_digest, prior_digest)
        values (gen_random_uuid(),sid,probe_actor,'carr-internal','log-activity','deal',v_subj,ks2,
                ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,ks2,'deal',v_subj),
                ops.write_receipt_material_digest(ks2, sid, 'deal', v_subj),
                'A-STATE-THIS-SUBJECT-NEVER-REACHED');
      exception when others then
        failed := true;
        if position('never reached' in sqlerrm) = 0 then
          raise exception '0238 FAILED: the retraction bootstrap refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a RETRACTION was accepted as a source of subject '
                        'state, so the prior guard is bootstrappable again';
      end if;
    end;

    -- (14c) A RETRACTION CANNOT EVIDENCE A DRIVE RETIREMENT. Two proven
    -- retractions carrying three chosen string literals satisfied every clause
    -- of the retirement gate and retired a dependency nothing had repointed.
    --
    -- EACH SIDE IS PROBED SEPARATELY, pairing one retraction with one honest
    -- receipt. Using a retraction for BOTH roles cannot isolate either clause:
    -- disable the repoint check and the recovery check refuses instead, so the
    -- probe still passes and the mutant lives.
    declare
      honest_dep_receipt uuid;
    begin
      select w.id into honest_dep_receipt from ops.write_receipt w
       where w.subject_type = 'drive_dependency' and w.subject_id = dep
         and w.is_proven
         and w.retracts_receipt_id is null and w.reverses_receipt_id is null
       limit 1;
      if honest_dep_receipt is null then
        raise exception '0238 FAILED: section 14 found no honest receipt naming the dependency';
      end if;

      failed := false;
      begin
        insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
          recovery_receipt_id, application_session_id, retired_by_actor_id,
          organization_tenant_id, note)
        values (gen_random_uuid(), dep, vret, honest_dep_receipt, sid, probe_actor,
                'carr-internal', 'probe: retraction as the repoint');
      exception when others then
        failed := true;
        if position('repoint receipt' in sqlerrm) = 0
           or position('is a retraction or a reversal' in sqlerrm) = 0 then
          raise exception '0238 FAILED: retraction-as-repoint refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a RETRACTION was accepted as the repoint evidence';
      end if;

      failed := false;
      begin
        insert into ops.drive_retirement (id, drive_dependency_id, repoint_receipt_id,
          recovery_receipt_id, application_session_id, retired_by_actor_id,
          organization_tenant_id, note)
        values (gen_random_uuid(), dep, honest_dep_receipt, vret, sid, probe_actor,
                'carr-internal', 'probe: retraction as the recovery');
      exception when others then
        failed := true;
        if position('recovery receipt' in sqlerrm) = 0
           or position('is a retraction or a reversal' in sqlerrm) = 0 then
          raise exception '0238 FAILED: retraction-as-recovery refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a RETRACTION was accepted as the recovery evidence';
      end if;
    end;

    -- (14e) WITHDRAWING SOMEBODY ELSE'S RETIREMENT NEEDS STANDING. Withdrawals
    -- are immutable and irreversible, so without this any automation actor in
    -- the tenant could flip readiness back to no, repeatedly and at will.
    select dr.id into a_retirement from ops.drive_retirement dr
     where not exists (select 1 from ops.drive_retirement_withdrawal w
                        where w.drive_retirement_id = dr.id)
     limit 1;
    -- A HUMAN third party, deliberately. The earlier guard let any human
    -- through, and carr_writer holds UPDATE on public.actor, so "is human" was
    -- a label the attacker could give itself. Probing with a human is the only
    -- way to show that escape is closed rather than merely unused.
    select id into machine_actor from public.actor
      where kind = 'human' and id <> probe_actor order by slug limit 1;
    if machine_actor is null then
      select id into machine_actor from public.actor
        where id <> probe_actor order by slug limit 1;
    end if;
    if a_retirement is not null and machine_actor is not null then
      declare msid uuid := gen_random_uuid();
      begin
        insert into ops.application_session (id, actor_id, organization_tenant_id,
          sponsoring_human_slug, via, auth_issuer, authorization_class, verified_subject, expires_at)
        values (msid, machine_actor, 'carr-internal', 'joe', 'probe', 'probe-issuer',
                'verified_partner', 'probe', clock_timestamp() + interval '1 hour');
        failed := false;
        begin
          insert into ops.drive_retirement_withdrawal (id, drive_retirement_id,
            application_session_id, withdrawn_by_actor_id, organization_tenant_id, note)
          values (gen_random_uuid(), a_retirement, msid, machine_actor, 'carr-internal',
                  'probe: a third party with no standing');
        exception when others then
          failed := true;
          if position('did not make it' in sqlerrm) = 0 then
            raise exception '0238 FAILED: standingless withdrawal refused by the WRONG guard: %', sqlerrm;
          end if;
        end;
        if not failed then
          raise exception '0238 FAILED: a third party withdrew a retirement it had no '
                          'part in making, and being human did not stop it';
        end if;
      end;
    end if;
  end;

  -- ===== (15) the seven guards an auditor could revert with nothing failing
  -- Each was reverted by an evidence auditor against the previous commit and
  -- survived BOTH proof surfaces. Two of them undo fixes this migration takes
  -- credit for, which is the worst kind of gap: the commit message says the bug
  -- is closed and nothing would notice it reopening.
  declare
    seq_a uuid := gen_random_uuid();
    seq_b uuid := gen_random_uuid();
    kseq  text := 'p15s-' || gen_random_uuid()::text;
    swap_tmp uuid;
    tie   timestamptz := clock_timestamp();
    ssubj uuid := gen_random_uuid();
    m_seq text;
    ka text := 'p15a-' || gen_random_uuid()::text;
    kb text := 'p15b-' || gen_random_uuid()::text;
    amb uuid := gen_random_uuid();
    src text;
  begin
    -- (15a) THE MATERIAL FOLD SORTS UNDER C. A behavioural probe cannot see
    -- this: the disposable cluster's own collation is C, so dropping the
    -- clause changes nothing here and the auditor's mutant survived. It is
    -- checked by SHAPE instead, and named as a shape check rather than dressed
    -- up as behaviour. What it defends is real -- under a non-C collation the
    -- same events fold to a different digest, so the local harness and Neon
    -- would disagree about identical writes.
    select pg_get_functiondef(p.oid) into src
      from pg_proc p join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'ops' and p.proname = 'write_receipt_material_digest';
    if (length(src) - length(replace(src, 'collate "C"', ''))) / length('collate "C"') <> 4 then
      raise exception '0238 FAILED: the material fold does not sort all four keys '
                      'under collate "C", so its digest depends on the database''s '
                      'collation and will not agree across environments';
    end if;

    -- (15b) THE REDUCER FOLDS BY seq, NOT BY (recorded_at, id). Two receipts
    -- sharing a recorded_at, inserted so that insertion order is the OPPOSITE
    -- of id order: under the old tiebreak the fold followed the smaller id,
    -- which is a random number.
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kseq,'log-activity',probe_actor,kseq,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',ssubj,'stage','"s"'::jsonb,'system',kseq,'carr-internal',sid);
    m_seq := ops.write_receipt_material_digest(kseq, sid, 'deal', ssubj);
    -- Ensure the FIRST inserted carries the LARGER id, so insertion order and id
    -- order disagree and the probe can tell which one the fold followed. Written
    -- with a temporary because the two-statement form collapses both variables
    -- onto one value, which collides on the primary key half the time -- a coin
    -- flip this file would have shipped as a passing probe.
    if seq_a < seq_b then
      swap_tmp := seq_a; seq_a := seq_b; seq_b := swap_tmp;
    end if;
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest, recorded_at)
    values (seq_a,sid,probe_actor,'carr-internal','log-activity','deal',ssubj,kseq,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kseq,'deal',ssubj),
            m_seq,'origin',tie);
    perform ops.prove_write_receipt(seq_a);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest, recorded_at)
    values (seq_b,sid,probe_actor,'carr-internal','log-activity','deal',ssubj,kseq,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kseq,'deal',ssubj),
            m_seq,m_seq,tie);
    perform ops.prove_write_receipt(seq_b);
    select * into r from ops.continuity_reducer('deal', ssubj);
    if r.break_at is not null then
      raise exception '0238 FAILED: the reducer folded two same-instant receipts in id '
                      'order rather than insertion order, so the fold depends on a '
                      'random number again (break_at %)', r.break_at;
    end if;

    -- (15c) seq IS NOT CALLER-WRITABLE. `generated by default` would let a
    -- writer choose its own position in the fold.
    failed := false;
    begin
      insert into ops.write_receipt (id, seq, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (gen_random_uuid(), 1, sid, probe_actor,'carr-internal','log-activity','deal',ssubj,kseq,
              ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,kseq,'deal',ssubj),
              m_seq, m_seq);
    exception when others then
      failed := true;
      if position('non-DEFAULT value' in sqlerrm) = 0 then
        raise exception '0238 FAILED: caller-supplied seq refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a caller chose its own seq, so it can choose where '
                      'it lands in the fold';
    end if;

    -- (15d) THE SEPARATORS ARE PRESENT. Checked by SHAPE, and the reason is
    -- worth stating exactly, because the first version of this was a
    -- behavioural probe that tested nothing. It folded field 'ab' carrying "c"
    -- against field 'a' carrying "bc" and asserted the two digests differed.
    -- They do differ -- with the separator or without it -- because new_value
    -- is jsonb and a jsonb STRING renders WITH its quotes, so the folds are
    -- ab"c" and a"bc" and never collide. The quotes were doing the separator's
    -- job, and the probe passed against a fold that had lost it.
    --
    -- A COLLIDING FIXTURE IS CONSTRUCTIBLE. An earlier version of this comment
    -- claimed it was not, on the grounds that every folded row begins with its
    -- verb. That is wrong: both rows carry the SAME verb, so the prefix cancels
    -- and protects nothing. jsonb NUMBERS render bare, and field 'a' carrying
    -- 12 against field 'a1' carrying 2 both fold to a12 -- digests EQUAL once
    -- the separator is dropped, and DIFFERENT under this file. Measured on a
    -- disposable cluster in both directions, not reasoned about.
    --
    -- SHAPE IS STILL THE RIGHT CHECK HERE, for a reason that survives the
    -- correction: behaviour needs one fixture PER separator. The pair above
    -- kills a fold that lost the separators around old_value, but a fold that
    -- lost only the one before new_value needs the difference to straddle THAT
    -- boundary instead -- old 1 with new 23, against old 12 with new 3. Six
    -- positions means six fixtures and six ways to write one that quietly
    -- proves nothing. One counted assertion covers every position at once.
    -- COUNTED, not merely present. Asking whether chr(31) appears at all passes
    -- while two of the three are gone, which is how the first version of this
    -- check let a mutant through.
    if (length(src) - length(replace(src, 'chr(31)', ''))) / length('chr(31)') <> 3
       or (length(src) - length(replace(src, 'chr(30)', ''))) / length('chr(30)') <> 2
       or (length(src) - length(replace(src, 'chr(29)', ''))) / length('chr(29)') <> 1 then
      raise exception '0238 FAILED: the material fold has lost a separator, so a field '
                      'boundary inside it is ambiguous';
    end if;
  end;

  -- (15e) THE PRIOR GUARD IS TENANT-SCOPED. Without it, material another tenant
  -- proved on a subject id you both happen to name becomes a legal prior here,
  -- which is the cross-tenant half of the bootstrap the guard exists to refuse.
  declare
    kt2 text := 'p15t-' || gen_random_uuid()::text;
    km2 text := 'p15m-' || gen_random_uuid()::text;
    shared_subj uuid := gen_random_uuid();
    theirs uuid := gen_random_uuid();
    their_material text;
  begin
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kt2,'log-activity',probe_actor,kt2,'{}'::jsonb,'other-tenant',sid2),
           (km2,'log-activity',probe_actor,km2,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',shared_subj,'stage','"theirs"'::jsonb,'system',kt2,'other-tenant',sid2),
           (clock_timestamp(),probe_actor,'log-activity','deal',shared_subj,'stage','"mine"'::jsonb,'system',km2,'carr-internal',sid);
    their_material := ops.write_receipt_material_digest(kt2, sid2, 'deal', shared_subj);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (theirs,sid2,probe_actor,'other-tenant','log-activity','deal',shared_subj,kt2,
            ops.write_receipt_digest('log-activity',probe_actor,'other-tenant',sid2,kt2,'deal',shared_subj),
            their_material,'origin');
    if not ops.prove_write_receipt(theirs) then
      raise exception '0238 FAILED: the cross-tenant prior fixture did not prove';
    end if;
    failed := false;
    begin
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (gen_random_uuid(),sid,probe_actor,'carr-internal','log-activity','deal',shared_subj,km2,
              ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,km2,'deal',shared_subj),
              ops.write_receipt_material_digest(km2, sid, 'deal', shared_subj), their_material);
    exception when others then
      failed := true;
      if position('never reached' in sqlerrm) = 0 then
        raise exception '0238 FAILED: cross-tenant prior refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: material PROVEN IN ANOTHER TENANT was accepted as a '
                      'prior state here';
    end if;
  end;

  -- (15f) A WITHDRAWAL IS BOUND TO ITS OWN SESSION, in both actor and tenant.
  -- Neither binding had a probe, so either could be removed silently.
  declare
    a_ret uuid;
    other_human uuid;
  begin
    select dr.id into a_ret from ops.drive_retirement dr
     where not exists (select 1 from ops.drive_retirement_withdrawal w
                        where w.drive_retirement_id = dr.id)
     limit 1;
    select id into other_human from public.actor
     where kind = 'human' and id <> probe_actor order by slug limit 1;
    if a_ret is not null and other_human is not null then
      failed := false;
      begin
        insert into ops.drive_retirement_withdrawal (id, drive_retirement_id,
          application_session_id, withdrawn_by_actor_id, organization_tenant_id, note)
        values (gen_random_uuid(), a_ret, sid, other_human, 'carr-internal',
                'probe: actor does not match its session');
      exception when others then
        failed := true;
        if position('different actor than its session' in sqlerrm) = 0 then
          raise exception '0238 FAILED: session-mismatched withdrawal refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a withdrawal named an actor its own session never '
                        'authenticated';
      end if;
    end if;
    if a_ret is not null then
      failed := false;
      begin
        insert into ops.drive_retirement_withdrawal (id, drive_retirement_id,
          application_session_id, withdrawn_by_actor_id, organization_tenant_id, note)
        values (gen_random_uuid(), a_ret, sid, probe_actor, 'other-tenant',
                'probe: tenant does not match its session');
      exception when others then
        failed := true;
        if position('different tenant than its session' in sqlerrm) = 0 then
          raise exception '0238 FAILED: tenant-mismatched withdrawal refused by the WRONG guard: %', sqlerrm;
        end if;
      end;
      if not failed then
        raise exception '0238 FAILED: a withdrawal named a tenant its own session does '
                        'not belong to';
      end if;
    end if;
  end;

  -- ===== (16) what the third security round reproduced
  declare
    k16 text := 'p16-' || gen_random_uuid()::text;
    k16b text := 'p16b-' || gen_random_uuid()::text;
    f_subj uuid := gen_random_uuid();
    fa uuid := gen_random_uuid();
    fr uuid := gen_random_uuid();
    x_subj uuid := gen_random_uuid();
    kx1 text := 'p16x-' || gen_random_uuid()::text;
    kx2 text := 'p16y-' || gen_random_uuid()::text;
  begin
    -- (16a) RETRACT WHILE UNPROVEN, THEN PROVE. Retraction is refused against a
    -- proven receipt, but nothing stopped the reverse order, and readback is
    -- one-way -- so a receipt ended up permanently proven AND retracted, which
    -- the conflict detector then dropped, silencing a real fork under a clean
    -- acceptance. Closed twice: proving a disavowed receipt is refused, and the
    -- detector no longer drops a proven one.
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (k16,'log-activity',probe_actor,k16,'{}'::jsonb,'carr-internal',sid),
           (k16b,'log-activity',probe_actor,k16b,'{}'::jsonb,'carr-internal',sid);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',f_subj,'stage','"a"'::jsonb,'system',k16,'carr-internal',sid),
           (clock_timestamp(),probe_actor,'log-activity','deal',f_subj,'stage','"b"'::jsonb,'system',k16b,'carr-internal',sid);
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest)
    values (fa,sid,probe_actor,'carr-internal','log-activity','deal',f_subj,k16,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,k16,'deal',f_subj),
            ops.write_receipt_material_digest(k16, sid, 'deal', f_subj),'origin');
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, call_digest, material_digest, prior_digest,
      retracts_receipt_id)
    values (fr,sid,probe_actor,'carr-internal','log-activity','deal',f_subj,k16b,
            ops.write_receipt_digest('log-activity',probe_actor,'carr-internal',sid,k16b,'deal',f_subj),
            'a-retraction-states-no-material','origin',fa);
    if not ops.prove_write_receipt(fr) then
      raise exception '0238 FAILED: the retract-then-prove fixture retraction did not prove';
    end if;
    failed := false;
    begin
      perform ops.prove_write_receipt(fa);
    exception when others then
      failed := true;
      if position('already been retracted by a proven receipt' in sqlerrm) = 0 then
        raise exception '0238 FAILED: proving a disavowed receipt refused by the WRONG guard: %', sqlerrm;
      end if;
    end;
    if not failed then
      raise exception '0238 FAILED: a receipt a PROVEN retraction had disavowed was '
                      'then proved, so it is both at once and every reader disagrees';
    end if;

    -- (16b) ONE TENANT CANNOT INJECT A CONFLICT INTO ANOTHER'S ACCEPTANCE. The
    -- bar was scoped on its unproven half only; the conflict half enumerated
    -- this tenant's subjects but counted conflicts belonging to anyone, and
    -- every closer is same-tenant, so there was no remedy from inside.
    insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
      response, organization_tenant_id, application_session_id)
    values (kx1,'log-activity',probe_actor,kx1,'{}'::jsonb,'other-tenant',sid2),
           (kx2,'log-activity',probe_actor,kx2,'{}'::jsonb,'other-tenant',sid2);
    insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
      field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
    values (clock_timestamp(),probe_actor,'log-activity','deal',x_subj,'stage','"x"'::jsonb,'system',kx1,'other-tenant',sid2),
           (clock_timestamp(),probe_actor,'log-activity','deal',x_subj,'stage','"y"'::jsonb,'system',kx2,'other-tenant',sid2);
    declare xa uuid := gen_random_uuid(); xb uuid := gen_random_uuid();
    begin
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (xa,sid2,probe_actor,'other-tenant','log-activity','deal',x_subj,kx1,
              ops.write_receipt_digest('log-activity',probe_actor,'other-tenant',sid2,kx1,'deal',x_subj),
              ops.write_receipt_material_digest(kx1, sid2, 'deal', x_subj),'origin');
      perform ops.prove_write_receipt(xa);
      insert into ops.write_receipt (id, application_session_id, actor_id,
        organization_tenant_id, verb, subject_type, subject_id,
        tool_call_idempotency_key, call_digest, material_digest, prior_digest)
      values (xb,sid2,probe_actor,'other-tenant','log-activity','deal',x_subj,kx2,
              ops.write_receipt_digest('log-activity',probe_actor,'other-tenant',sid2,kx2,'deal',x_subj),
              ops.write_receipt_material_digest(kx2, sid2, 'deal', x_subj),'origin');
      perform ops.prove_write_receipt(xb);
      if (select count(*) from ops.receipt_conflicts('deal', x_subj)) <> 1 then
        raise exception '0238 FAILED: the cross-tenant injection fixture did not conflict';
      end if;
      -- ASSERTED ON THE COUNT THE BAR COMPUTES, not on whether acceptance
      -- succeeds. By this point the retirement probes above have deliberately
      -- left conflicts in THIS tenant, so acceptance refuses for a reason of its
      -- own and the clause under test never speaks -- which is how the first
      -- version of this probe reproduced the finding it was written to close.
      declare
        n_here bigint;
      begin
        select coalesce(sum(c), 0) into n_here from (
          select (select count(*) from ops.receipt_conflicts(w.subject_type, w.subject_id) rc
                   join ops.write_receipt lw on lw.id = rc.left_receipt
                  where lw.organization_tenant_id = 'carr-internal') as c
            from (select distinct subject_type, subject_id from ops.write_receipt
                   where organization_tenant_id = 'carr-internal'
                     and subject_id = x_subj) w
        ) t;
        if n_here <> 0 then
          raise exception '0238 FAILED: a conflict belonging entirely to ANOTHER tenant '
                          'counted against this one (% found), and nothing here can '
                          'close it', n_here;
        end if;
      end;
    end;
  end;

  raise notice '0238 apply-time proof passed';
  raise exception 'ROLLBACK_0238_PROBE';
exception when others then
  if sqlerrm = 'ROLLBACK_0238_PROBE' then
    return;
  end if;
  raise;
end $$;
